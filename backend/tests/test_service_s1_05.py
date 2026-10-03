"""S1-05: worker capabilities from the installed runtime and support decisions without silent fallback.

Fake torch modules stand in for drivers in the support tests; the preflight tests train tiny synthetic models on the
CPU in temporary folders. Nothing is downloaded or connected.
"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.contracts.capabilities import decide, local_device_kinds, probe_local


def _torch(cuda=(), mps=False):
    props = [SimpleNamespace(name=name, total_memory=memory * 1024 * 1024) for name, memory in cuda]
    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: bool(props), device_count=lambda: len(props),
                             get_device_properties=lambda index: props[index]),
        backends=SimpleNamespace(mps=SimpleNamespace(is_built=lambda: mps, is_available=lambda: mps)))


FAMILIES = [
    {'task': 'classification', 'stages': ['label', 'train', 'evaluate', 'flow', 'export'], 'automated_training': True, 'missing_dependencies': []},
    {'task': 'detection', 'stages': ['label', 'train', 'evaluate', 'flow', 'export'], 'automated_training': True, 'missing_dependencies': ['ultralytics']},
    {'task': 'defect_gan', 'stages': ['label', 'train', 'evaluate', 'generate', 'review', 'export'], 'automated_training': False, 'missing_dependencies': []},
]


def test_windows_never_offers_mps_even_when_a_driver_claims_it():
    caps = probe_local(families=FAMILIES, system='Windows', torch_module=_torch(cuda=[('RTX A4000', 16376)], mps=True), has_torch=True)
    assert [device.kind for device in caps.devices] == ['cpu', 'cuda']
    assert caps.devices[1].name == 'RTX A4000' and caps.devices[1].memory_mb == 16376
    assert decide(caps, 'classification', 'train', 'mps').state == 'unsupported'
    assert local_device_kinds('Windows', _torch(mps=True)) == ['cpu']
    assert local_device_kinds('Darwin', _torch(mps=True)) == ['cpu', 'mps']


def test_a_device_the_worker_lacks_is_refused_never_replaced_by_the_cpu():
    caps = probe_local(families=FAMILIES, system='Linux', torch_module=_torch(), has_torch=True)
    refused = decide(caps, 'classification', 'train', 'cuda')
    assert refused.state == 'unsupported' and '바꿔 실행하지 않습니다' in refused.reason
    assert decide(caps, 'classification', 'train', 'cpu').state == 'unverified'


def test_four_support_states_are_distinguished():
    caps = probe_local(families=FAMILIES, system='Linux', torch_module=_torch(cuda=[('L4', 23034)]), has_torch=True,
                       verified={'classification:train:cuda': 1.0})
    assert decide(caps, 'classification', 'train', 'cuda').state == 'verified'
    assert decide(caps, 'classification', 'evaluate', 'cuda').state == 'unverified', 'no preflight of that stage yet'
    missing = decide(caps, 'detection', 'train', 'cuda')
    assert missing.state == 'not_installed' and 'ultralytics' in missing.reason
    assert decide(caps, 'defect_gan', 'infer', 'cpu').state == 'unsupported', 'the family has no inference stage'
    assert decide(caps, 'defect_gan', 'search', 'cpu').state == 'unsupported', 'no automated search for this family'
    assert decide(caps, 'rotation', 'train', 'cpu').state == 'unsupported', 'a family this worker does not list'


def test_without_torch_every_family_is_not_installed_and_only_the_cpu_is_listed():
    caps = probe_local(families=FAMILIES, system='Windows', has_torch=False)
    assert [device.kind for device in caps.devices] == ['cpu']
    assert {state for stages in caps.tasks.values() for state in stages.values()} <= {'not_installed', 'unsupported'}
    assert 'torch' in caps.missing['classification']


def test_a_broken_driver_reports_no_cuda_rather_than_a_guess():
    broken = _torch()
    broken.cuda.is_available = lambda: (_ for _ in ()).throw(RuntimeError('driver mismatch'))
    caps = probe_local(families=FAMILIES, system='Linux', torch_module=broken, has_torch=True)
    assert [device.kind for device in caps.devices] == ['cpu']


def test_the_model_catalog_offers_only_devices_this_computer_has(monkeypatch):
    from backend.contracts import capabilities
    from backend.engine import model_catalog
    model_catalog._device_kinds.cache_clear()
    monkeypatch.setattr(capabilities._platform, 'system', lambda: 'Windows')
    try:
        families = model_catalog.model_family_catalog()['families']
        assert all('mps' not in family['devices'] for family in families)
        assert all(family['devices'][0] == 'cpu' for family in families)
    finally:
        model_catalog._device_kinds.cache_clear()


def test_the_training_engine_advertises_only_this_computers_devices(monkeypatch):
    from backend.contracts import capabilities
    from backend.engine import model_catalog, training_engine
    model_catalog._device_kinds.cache_clear()
    monkeypatch.setattr(capabilities._platform, 'system', lambda: 'Windows')
    try:
        tasks = training_engine.capabilities()['tasks']
        assert all('mps' not in task['devices'] and task['devices'][0] == 'cpu' for task in tasks.values())
    finally:
        model_catalog._device_kinds.cache_clear()


def _devices_app(tmp_path, monkeypatch, launcher):
    from fastapi.testclient import TestClient
    from PIL import Image
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.api import routes_training
    from backend.contracts import capabilities
    from backend.engine import local_training_worker
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    monkeypatch.setattr(capabilities, 'local_device_kinds', lambda *args, **kwargs: ['cpu'])
    monkeypatch.setattr(local_training_worker, 'run_owned_training', launcher)
    source = tmp_path / 'source'
    for split in ('train', 'val'):
        for label in ('ok', 'ng'):
            (source / split / label).mkdir(parents=True)
            for index in range(2):
                Image.new('RGB', (32, 32), (200 if label == 'ok' else 40, 0, index)).save(source / split / label / f'{index}.png')
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    api.post('/api/project/create', json={'name': 'Devices'})
    assert api.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return api, {'task': 'classification', 'dataset_path': str(source), 'preset': 'fast'}


def test_a_training_start_on_a_device_this_computer_lacks_is_refused_not_moved_to_the_cpu(tmp_path, monkeypatch):
    api, body = _devices_app(tmp_path, monkeypatch, lambda *args, **kwargs: pytest.fail('nothing may launch'))
    for request in ({**body, 'device': 'cuda'}, {**body, 'device': 'mps'}, {**body, 'device': 'cuda:3'}):
        refused = api.post('/api/training/start', json=request)
        assert refused.status_code == 409 and '바꿔 실행하지 않습니다' in refused.text, refused.text


def test_no_device_auto_and_cpu_starts_pass_the_device_check(tmp_path, monkeypatch):
    launched = []
    api, body = _devices_app(tmp_path, monkeypatch,
                             lambda *args, **kwargs: launched.append(kwargs) or {'status': 'aborted', 'worker_exit_confirmed': True})
    for request in ({**body}, {**body, 'device': 'auto'}, {**body, 'device': 'cpu'}):
        response = api.post('/api/training/start', json=request)
        assert response.status_code == 200 and '바꿔 실행하지 않습니다' not in response.text, (request.get('device'), response.text)


# --- Slice 2: real preflights move a support state to verified for the runtime they ran on ------------------------

def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import backend.main as main
    from backend.api import routes_training, routes_workers
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    manager = routes_training.TrainingJobManager()
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    monkeypatch.setattr(main, 'training_job_manager', manager)
    monkeypatch.setattr(routes_workers, '_STATE', {'running': None, 'last': None})
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    return TestClient(app, headers={'X-Vision-Token': app.state.api_token}), manager


def _wait_preflight(api, seconds=300):
    import time
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = api.get('/api/workers').json()
        if state['running_preflight'] is None and state['last_preflight']:
            return state
        time.sleep(0.2)
    raise AssertionError('the preflight did not finish')


def test_a_real_preflight_verifies_each_stage_for_the_runtime_it_ran_on(tmp_path, monkeypatch):
    from backend.engine import worker_preflight
    api, _ = _app(tmp_path, monkeypatch)
    support = lambda state: {stage: state['workers'][0]['support']['classification'][stage]['cpu']['state']
                             for stage in ('train', 'evaluate', 'infer', 'export', 'search')}
    assert set(support(api.get('/api/workers').json()).values()) == {'unverified'}
    started = api.post('/api/workers/local/preflight', json={'task': 'classification'})
    assert started.status_code == 202, started.text
    assert api.post('/api/workers/local/preflight', json={'task': 'anomaly'}).status_code == 409, 'one preflight at a time'
    state = _wait_preflight(api)
    last = state['last_preflight']
    assert last['error'] is None and {stage: row['passed'] for stage, row in last['results'].items()} == dict.fromkeys(
        ('train', 'evaluate', 'infer', 'export'), True)
    export = last['results']['export']['evidence']
    assert export['verdict'] in ('OK', 'NG') and export['model_steps'] == {'node_inspect': export['model_steps']['node_inspect']}
    assert export['model_steps']['node_inspect'] in ('passed', 'flagged_ng'), 'the exported package inspected the image'
    assert {row['evidence']['architecture'] for row in last['results'].values()} == {'resnet18'}, 'the checked architecture is named'
    assert last['results']['train']['evidence']['device'] == 'cpu' and export['device'] == 'cpu'
    assert state['workers'][0]['preflight_architectures']['classification'] == 'resnet18'
    assert state['workers'][0]['local_compute_busy'] is None, 'the reservation is released when the preflight ends'
    assert support(state) == {'train': 'verified', 'evaluate': 'verified', 'infer': 'verified', 'export': 'verified',
                              'search': 'unverified'}, 'search had no preflight'
    worker = state['workers'][0]
    assert worker['runtime_digest'] == worker_preflight.runtime_digest()
    assert worker['support']['anomaly']['train']['cpu']['state'] == 'unverified', 'another family is not verified by this one'
    # An upgraded package or engine is a different runtime: verified again only after its own preflight.
    real = worker_preflight.runtime_fingerprint()
    monkeypatch.setattr(worker_preflight, 'runtime_fingerprint', lambda: {**real, 'packages': {**real['packages'], 'torch': '0.0.0'}})
    assert set(support(api.get('/api/workers').json()).values()) == {'unverified'}
    assert not list((tmp_path / 'user_data' / 'worker_preflight_runs').iterdir()), 'the preflight run folder is removed'


def test_a_failed_or_timed_out_preflight_is_kept_with_its_reason_and_verifies_nothing(tmp_path):
    from backend.engine import worker_preflight
    store = worker_preflight.PreflightStore(tmp_path / 'preflight.json')
    outcome = worker_preflight.run_preflight('classification', 'cpu', ('train', 'infer'), store=store, timeout=0.01,
                                             workdir_root=tmp_path / 'runs')
    assert {stage: (row['passed'], row['reason']) for stage, row in outcome['results'].items()} == {
        'train': (False, 'timed out after 0 s'), 'infer': (False, 'timed out after 0 s')}
    assert store.verified('local', outcome['runtime_digest']) == {}
    assert store.results('local', outcome['runtime_digest'])['classification:train:cpu']['reason'] == 'timed out after 0 s'
    store.record('local', 'r1', 'classification', 'export', 'cpu', passed=True, reason='passed', seconds=1, now=5.0)
    store.record('local', 'r1', 'classification', 'export', 'cpu', passed=False, reason='ValueError: no verdict', seconds=1)
    assert store.verified('local', 'r1') == {}, 'the last preflight of a stage decides it'
    store.record('local', 'r2', 'classification', 'train', 'cpu', passed=True, reason='passed', seconds=1,
                 evidence={'loss': float('nan'), 'nested': {'x': float('inf')}, 'path': tmp_path})
    assert store.results('local', 'r1') == {}, 'another runtime\'s results are superseded'
    assert store.results('local', 'r2')['classification:train:cpu']['evidence'] == {'loss': None, 'nested': {'x': None}, 'path': str(tmp_path)}


def test_a_preflight_is_refused_with_the_support_decisions_own_reason(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.engine import worker_preflight
    from backend.engine.worker_preflight import PreflightRefused, plan
    families = [{'task': 'classification', 'stages': ['label', 'train', 'evaluate', 'flow', 'export'], 'automated_training': True,
                 'missing_dependencies': []},
                {'task': 'detection', 'stages': ['label', 'train', 'evaluate', 'flow', 'export'], 'automated_training': True,
                 'missing_dependencies': ['ultralytics']}]
    linux = probe_local(families=families, system='Linux', torch_module=_torch(), has_torch=True)
    with pytest.raises(PreflightRefused, match='CUDA'):
        plan('classification', 'cuda', capabilities=linux)
    with pytest.raises(PreflightRefused, match='MPS'):
        plan('classification', 'mps', capabilities=probe_local(families=families, system='Windows', torch_module=_torch(mps=True), has_torch=True))
    with pytest.raises(PreflightRefused, match='ultralytics'):
        plan('detection', 'cpu', capabilities=linux)
    with pytest.raises(PreflightRefused, match='ocr'):
        plan('ocr', 'cpu', capabilities=linux)
    with pytest.raises(PreflightRefused, match='search'):
        plan('classification', 'cpu', ['search'], capabilities=linux)
    assert plan('classification', 'cpu', ['infer', 'train', 'infer'], capabilities=linux) == ('infer', 'train')
    api, _ = _app(tmp_path, monkeypatch)
    from backend.engine.shared_scheduler import ResourceLeases, shared_leases
    # A training of another app process holds the app-wide local compute reservation.
    other = ResourceLeases(shared_leases().path, owner='pid:other-app')
    assert other.acquire('job_running', 'local-compute', 'all')
    assert api.get('/api/workers').json()['workers'][0]['local_compute_busy'], 'the panel can say why before a click'
    refused = api.post('/api/workers/local/preflight', json={'task': 'classification'})
    assert refused.status_code == 409 and '계산 자원을 사용 중' in refused.json()['detail']
    assert api.get('/api/workers').json()['running_preflight'] is None
    assert other.release('job_running')


def test_with_shared_accounts_only_a_project_owner_runs_a_preflight(monkeypatch):
    from fastapi import HTTPException
    from types import SimpleNamespace
    from backend.api import routes_project, routes_workers
    monkeypatch.setattr(routes_project, 'get_current_project', lambda request: {'id': 'p1'})
    roles = {'u-owner': 'owner', 'u-member': 'trainer'}
    accounts = SimpleNamespace(project_role=lambda user, project: roles[user])

    def request(user):
        return SimpleNamespace(state=SimpleNamespace(account_user={'id': user} if user else None),
                               app=SimpleNamespace(state=SimpleNamespace(accounts=accounts)))
    routes_workers._require_owner(request(None))
    routes_workers._require_owner(request('u-owner'))
    with pytest.raises(HTTPException) as refused:
        routes_workers._require_owner(request('u-member'))
    assert refused.value.status_code == 403


def test_a_linked_data_folder_still_exports(tmp_path):
    import os
    from backend.engine import worker_preflight
    real = tmp_path / 'real'
    real.mkdir()
    linked = tmp_path / 'linked'
    try:
        os.symlink(real, linked, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('this account cannot create a directory link')
    store = worker_preflight.PreflightStore(linked / 'preflight.json')
    outcome = worker_preflight.run_preflight('classification', 'cpu', ('train', 'export'), store=store)
    assert {stage: row['reason'] for stage, row in outcome['results'].items()} == {'train': 'passed', 'export': 'passed'}


# --- Review fixes: where the child runs, which device ran, what export proves, what the runtime digest covers -------

def _restore_writable(folder):
    import os
    import stat
    os.chmod(folder, stat.S_IRWXU)


def test_the_child_runs_in_its_run_folder_never_in_the_installed_apps_folder(tmp_path, monkeypatch):
    """An installed app's folder is often read-only, and when it is not, a preflight must not write into it: the evaluate
    stage imports routes that create relative folders, so the child must not start there."""
    import os
    import stat
    from backend.engine import worker_preflight
    install = tmp_path / 'installed-app'
    install.mkdir()
    # The installed app's code root: the child still imports the real backend through the import path.
    monkeypatch.setattr(worker_preflight, 'ROOT', install)
    monkeypatch.setenv('PYTHONPATH', str(Path(worker_preflight.__file__).resolve().parents[2]))
    os.chmod(install, stat.S_IRUSR | stat.S_IXUSR)
    try:
        outcome = worker_preflight.run_preflight('classification', 'cpu', ('train', 'evaluate'),
                                                 store=worker_preflight.PreflightStore(tmp_path / 'preflight.json'),
                                                 workdir_root=tmp_path / 'runs')
    finally:
        _restore_writable(install)
    assert {stage: row['reason'] for stage, row in outcome['results'].items()} == {'train': 'passed', 'evaluate': 'passed'}
    assert list(install.iterdir()) == [], 'nothing was written into the installed app'


def test_a_device_the_preflight_process_cannot_use_fails_every_stage_and_a_fallback_fails_the_stage(tmp_path, monkeypatch):
    import torch
    from backend.engine import runtime_device, worker_preflight
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    # The trainer's fallback lands on the CPU, never on this machine's MPS GPU (tests train on the CPU only).
    monkeypatch.setattr(torch.backends.mps, 'is_available', lambda: False)
    refused = worker_preflight.run_stages('classification', 'cuda', tmp_path / 'strict')
    assert set(refused) == {'train', 'evaluate', 'infer', 'export'}
    assert all(not row['passed'] and 'cuda is not usable in the preflight process' in row['reason'] for row in refused.values())
    assert refused['train']['evidence'] == {'architecture': 'resnet18'}
    # Past the strict check (a device lost between the check and the stage), the trainer's own fallback still fails train.
    monkeypatch.setattr(runtime_device, 'resolve_runtime_device', lambda device: None)
    fallback = worker_preflight.run_stages('classification', 'cuda', tmp_path / 'fallback', ('train', 'infer'))
    assert not fallback['train']['passed'] and 'training ran on cpu, not on cuda' in fallback['train']['reason'], fallback['train']
    assert fallback['infer'] == {'passed': False, 'reason': 'not run: training did not produce a model', 'seconds': 0.0}


def test_inference_that_would_pick_another_device_fails_its_stage(tmp_path, monkeypatch):
    import torch
    from backend.engine import device as device_module, worker_preflight
    real = device_module.get_device
    monkeypatch.setattr(device_module, 'get_device', lambda requested=None: torch.device('meta') if requested == 'cpu' else real(requested))
    results = worker_preflight.run_stages('classification', 'cpu', tmp_path, ('train', 'infer'))
    assert results['train']['reason'] == 'passed', 'training picks its device through its own import'
    assert not results['infer']['passed'] and results['infer']['reason'] == 'ValueError: inference would run on meta, not on cpu'


def test_export_passes_only_when_the_package_inspected_the_image_with_its_model_on_the_device(tmp_path, monkeypatch):
    from backend.engine import flow_package_runtime, worker_preflight
    trained = worker_preflight.run_stages('classification', 'cpu', tmp_path / 'train', ('train',))
    assert trained['train']['passed'], trained
    model = tmp_path / 'train' / 'models' / 'job_preflight' / 'best_model.pt'
    image = sorted((tmp_path / 'train' / 'data' / 'test' / 'NG').glob('*.png'))[0]
    real = flow_package_runtime.run_flow_package

    def answering(change):
        def run(*args, **kwargs):
            result = real(*args, **kwargs)
            change(result)
            return result
        return run
    step = lambda result: next(row for row in result['execution_steps'] if row['node_id'] == 'node_inspect')
    cases = {
        'review': (lambda result: result.update(final_verdict='REVIEW'), "did not inspect the image (verdict 'REVIEW'"),
        'untrained': (lambda result: step(result).update(status='warning_untrained'), "ended 'warning_untrained'"),
        'skipped': (lambda result: step(result).update(status='skipped', output_count=0, skip_reason='refused'), "ended 'skipped' with 0 results: refused"),
        'no result': (lambda result: step(result).update(output_count=0), 'with 0 results'),
        'other device': (lambda result: result['execution_resources'].update(device='mps'), 'ran on mps, not on cpu'),
    }
    for index, (name, (change, message)) in enumerate(cases.items()):
        monkeypatch.setattr(flow_package_runtime, 'run_flow_package', answering(change))
        with pytest.raises(ValueError, match=__import__('re').escape(message)):
            worker_preflight._export('classification', model, image, tmp_path / f'export-{index}', 'cpu')
    monkeypatch.setattr(flow_package_runtime, 'run_flow_package', real)
    passed = worker_preflight._export('classification', model, image, tmp_path / 'export-real', 'cpu')
    assert passed['verdict'] in ('OK', 'NG') and passed['device'] == 'cpu' and set(passed['model_steps']) == {'node_inspect'}


def test_a_stage_that_raises_is_recorded_failed_and_the_worker_shows_it_unverified(tmp_path, monkeypatch):
    from backend.api import routes_workers
    from backend.engine import worker_preflight

    def broken(*args, **kwargs):
        raise RuntimeError('evaluator broke')
    monkeypatch.setattr(worker_preflight, '_evaluate', broken)
    results = worker_preflight.run_stages('classification', 'cpu', tmp_path / 'run', ('train', 'evaluate'))
    assert results['train']['passed'] is True
    assert results['evaluate'] == {'passed': False, 'reason': 'RuntimeError: evaluator broke', 'seconds': results['evaluate']['seconds'],
                                   'evidence': {'architecture': 'resnet18'}}
    store = worker_preflight.PreflightStore(tmp_path / 'preflight.json')
    digest = worker_preflight.runtime_digest()
    for stage, row in results.items():
        store.record('local', digest, 'classification', stage, 'cpu', passed=row['passed'], reason=row['reason'], seconds=row['seconds'])
    monkeypatch.setattr(routes_workers, '_store', lambda: store)
    support = routes_workers.local_worker()['support']['classification']
    assert (support['train']['cpu']['state'], support['evaluate']['cpu']['state']) == ('verified', 'unverified')


def test_a_timed_out_child_is_killed_through_its_handle(tmp_path, monkeypatch):
    import subprocess
    from backend.engine import worker_preflight
    started = []

    class Recorded(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            started.append(self)
    monkeypatch.setattr(subprocess, 'Popen', Recorded)
    worker_preflight.run_preflight('classification', 'cpu', ('train',), store=worker_preflight.PreflightStore(tmp_path / 'p.json'),
                                   timeout=0.5, workdir_root=tmp_path / 'runs')
    assert len(started) == 1 and started[0].returncode not in (None, 0), 'the child was stopped, it did not finish'
    command = started[0].args
    assert '--exit-with-parent' in command and '--deadline' in command, 'the child exits with its app and at its deadline'
    assert command[command.index('--device') + 1] == 'cpu' and command[command.index('--task') + 1] == 'classification'


def test_the_child_exits_when_the_app_side_of_its_stdin_closes(tmp_path):
    """An app that quits, crashes or is killed closes the pipe: the preflight child then exits instead of running on."""
    import subprocess
    import sys
    import time
    from backend.engine import worker_preflight
    from backend.engine.process_isolation import session_isolation
    child = subprocess.Popen([sys.executable, '-m', 'backend.engine.worker_preflight', '--task', 'classification', '--device', 'cpu',
                              '--stages', 'train,evaluate,infer,export', '--workdir', str(tmp_path), '--exit-with-parent'],
                             cwd=tmp_path, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             env=worker_preflight._child_environment(), **session_isolation())
    try:
        time.sleep(0.5)
        child.stdin.close()
        assert child.wait(timeout=30) == 75
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
    assert not (tmp_path / worker_preflight.RESULT_FILE).exists()


def test_shutdown_stops_a_running_preflight_child(tmp_path):
    import threading
    import time
    from backend.engine import worker_preflight
    outcome = {}
    worker = threading.Thread(target=lambda: outcome.update(worker_preflight.run_preflight(
        'classification', 'cpu', ('train', 'export'), store=worker_preflight.PreflightStore(tmp_path / 'p.json'),
        workdir_root=tmp_path / 'runs')))
    worker.start()
    deadline = time.monotonic() + 30
    while not worker_preflight._CHILDREN and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker_preflight.stop_running_preflights() == 1
    worker.join(30)
    assert not worker.is_alive()
    # A quit interrupts the run; it records nothing, so it never marks this runtime's stages failed.
    assert outcome['results'] == {} and '중단' in outcome['interrupted'], outcome
    assert worker_preflight.PreflightStore(tmp_path / 'p.json').results('local', outcome['runtime_digest']) == {}
    assert not worker_preflight._CHILDREN and not worker_preflight._STOPPED_BY_APP


def test_stale_run_folders_are_swept_and_a_live_one_is_kept(tmp_path):
    import os
    from backend.engine import worker_preflight
    old, live = tmp_path / 'classification-old', tmp_path / 'detection-live'
    (old / 'models').mkdir(parents=True)
    (old / 'models' / 'best_model.pt').write_bytes(b'x' * 64)
    live.mkdir()
    long_ago = worker_preflight.time.time() - worker_preflight.STALE_RUN_SECONDS - 60
    os.utime(old, (long_ago, long_ago))
    assert worker_preflight.sweep_stale_runs(tmp_path) == ['classification-old']
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ['detection-live']
    assert worker_preflight.sweep_stale_runs(tmp_path / 'missing') == []


def test_the_runtime_digest_covers_every_backend_source_the_app_version_and_the_os(tmp_path, monkeypatch):
    from backend.engine import worker_preflight
    root = tmp_path / 'app'
    for relative in ('backend/engine/trainer.py', 'backend/engine/anomaly/padim.py', 'backend/api/routes_evaluation.py',
                     'backend/tests/test_x.py'):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text('# v1\n', encoding='utf-8')
    monkeypatch.setattr(worker_preflight, 'ROOT', root)
    first = worker_preflight.runtime_digest()
    for relative in ('backend/engine/anomaly/padim.py', 'backend/api/routes_evaluation.py'):
        (root / relative).write_text('# a changed model or evaluator\n', encoding='utf-8')
        assert worker_preflight.runtime_digest() != first, relative
        (root / relative).write_text('# v1\n', encoding='utf-8')
    assert worker_preflight.runtime_digest() == first
    (root / 'backend/tests/test_x.py').write_text('# a test edit runs nothing in a preflight\n', encoding='utf-8')
    assert worker_preflight.runtime_digest() == first
    monkeypatch.setenv('VISION_AI_APP_VERSION', '9.9.9')
    assert worker_preflight.runtime_digest() != first
    monkeypatch.delenv('VISION_AI_APP_VERSION')
    monkeypatch.setattr(worker_preflight.platform, 'release', lambda: 'another-release')
    assert worker_preflight.runtime_digest() != first
    assert set(worker_preflight.runtime_fingerprint()['accelerator']) == {'torch_cuda', 'cudnn', 'nvidia_driver'}


def test_a_malformed_record_is_reported_and_set_aside_by_the_next_preflight(tmp_path, monkeypatch):
    import json
    from backend.api import routes_workers
    from backend.engine import worker_preflight
    path = tmp_path / 'preflight.json'
    store = worker_preflight.PreflightStore(path)
    monkeypatch.setattr(routes_workers, '_store', lambda: store)
    digest = worker_preflight.runtime_digest()
    for shape in ({'protocol_version': 1, 'workers': []}, {'protocol_version': 1, 'workers': {'local': []}},
                  {'protocol_version': 1, 'workers': {'local': {digest: {'classification:train:cpu': {'passed': True}}}}},
                  {'protocol_version': 1, 'workers': {'local': {digest: {'classification:train:cpu': 'passed'}}}}):
        path.write_text(json.dumps(shape), encoding='utf-8')
        worker = routes_workers.local_worker()
        assert worker['preflight_record_error'] and worker['preflight'] == {}, shape
        assert worker['support']['classification']['train']['cpu']['state'] == 'unverified'
    store.record('local', digest, 'classification', 'train', 'cpu', passed=True, reason='passed', seconds=1)
    assert store.verified('local', digest)
    assert [entry.name.split('.unreadable-')[0] for entry in tmp_path.iterdir() if '.unreadable-' in entry.name] == ['preflight.json']


def test_a_running_preflight_holds_the_local_compute_reservation(tmp_path, monkeypatch):
    import threading
    from backend.engine import worker_preflight
    from backend.engine.shared_scheduler import shared_leases
    api, _ = _app(tmp_path, monkeypatch)
    release = threading.Event()

    def held(task, device, stages, **kwargs):
        release.wait(30)
        return {'task': task, 'device': device, 'runtime_digest': 'r', 'architecture': 'resnet18', 'results': {}}
    monkeypatch.setattr(worker_preflight, 'run_preflight', held)
    assert api.post('/api/workers/local/preflight', json={'task': 'classification'}).status_code == 202
    try:
        assert not shared_leases().acquire('job_started_meanwhile', 'local-compute', 'all'), 'a training waits for the preflight'
    finally:
        release.set()
    _wait_preflight(api, 30)
    leases = shared_leases()
    assert leases.acquire('job_after', 'local-compute', 'all'), 'the reservation is released with the preflight'
    assert leases.release('job_after')


def test_a_queued_training_is_not_overtaken_by_a_preflight(tmp_path, monkeypatch):
    api, manager = _app(tmp_path, monkeypatch)
    manager._local_waiting['job_waiting'] = {}
    refused = api.post('/api/workers/local/preflight', json={'task': 'classification'})
    assert refused.status_code == 409 and '기다리는 학습' in refused.json()['detail']
    manager._local_waiting.clear()
    assert api.get('/api/workers').json()['running_preflight'] is None


def test_shutdown_releases_the_reservation_of_a_running_preflight(tmp_path, monkeypatch):
    import time
    from backend.api import routes_workers
    from backend.engine import worker_preflight
    from backend.engine.shared_scheduler import shared_leases
    api, _ = _app(tmp_path, monkeypatch)
    assert api.post('/api/workers/local/preflight', json={'task': 'classification'}).status_code == 202
    deadline = time.monotonic() + 30
    while not worker_preflight._CHILDREN and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker_preflight._CHILDREN, 'the preflight child started'
    routes_workers.stop_for_shutdown()
    state = api.get('/api/workers').json()
    assert state['running_preflight'] is None and state['workers'][0]['local_compute_busy'] is None
    assert state['last_preflight']['results'] == {} and state['last_preflight']['interrupted']
    assert shared_leases().acquire('job_after_quit', 'local-compute', 'all'), 'nothing holds the computer after the quit'


def test_the_reservation_heartbeat_survives_a_busy_table(tmp_path, monkeypatch):
    import threading
    import time
    from backend.engine import shared_scheduler, worker_preflight
    api, _ = _app(tmp_path, monkeypatch)
    path = shared_scheduler.shared_leases().path
    beats = []

    class Flaky(shared_scheduler.ResourceLeases):
        def heartbeat(self, job_id):
            beats.append(job_id)
            if len(beats) == 1:
                raise shared_scheduler.sqlite3.OperationalError('database is locked')
            return super().heartbeat(job_id)
    monkeypatch.setattr(shared_scheduler, 'shared_leases', lambda: Flaky(path, lease_seconds=1.5))
    release = threading.Event()
    monkeypatch.setattr(worker_preflight, 'run_preflight', lambda *args, **kwargs: (release.wait(30), {'runtime_digest': 'r', 'results': {}})[1])
    assert api.post('/api/workers/local/preflight', json={'task': 'classification'}).status_code == 202
    deadline = time.monotonic() + 10
    while len(beats) < 3 and time.monotonic() < deadline:
        time.sleep(0.05)
    release.set()
    _wait_preflight(api, 30)
    assert len(beats) >= 3, 'the heartbeat went on after a refused beat'


@pytest.mark.skipif(__import__('os').name == 'nt', reason='Windows keeps a folder with open files')
def test_a_run_folder_removed_mid_run_is_a_recorded_failure(tmp_path):
    import shutil
    import threading
    import time
    from backend.engine import worker_preflight
    store = worker_preflight.PreflightStore(tmp_path / 'p.json')
    outcome, errors = {}, []

    def run():
        try:
            outcome.update(worker_preflight.run_preflight('classification', 'cpu', ('train', 'export'), store=store,
                                                          workdir_root=tmp_path / 'runs'))
        except Exception as exc:  # the defect: an exception instead of a recorded failure
            errors.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 30
    while not worker_preflight._CHILDREN and time.monotonic() < deadline:
        time.sleep(0.02)
    for folder in (tmp_path / 'runs').iterdir():
        shutil.rmtree(folder)  # this test's own run folder under tmp_path
    worker.join(120)
    assert not errors, errors
    assert outcome['results'] and all(not row['passed'] for row in outcome['results'].values()), outcome


def test_an_expired_reservation_of_a_crashed_app_does_not_read_as_busy(tmp_path, monkeypatch):
    import time
    from backend.engine.shared_scheduler import ResourceLeases, shared_leases
    api, _ = _app(tmp_path, monkeypatch)
    crashed = ResourceLeases(shared_leases().path, owner='pid:crashed-app', lease_seconds=0.05)
    assert crashed.acquire('worker-preflight-crashed', 'local-compute', 'all')
    time.sleep(0.2)
    assert api.get('/api/workers').json()['workers'][0]['local_compute_busy'] is None, 'acquire would delete this row'
    live = ResourceLeases(shared_leases().path, owner='pid:live-app')
    assert live.acquire('job_live', 'local-compute', 'all')
    assert api.get('/api/workers').json()['workers'][0]['local_compute_busy'], 'a live reservation is busy'
    assert live.release('job_live')


def test_an_earlier_pass_survives_a_quit_during_the_next_preflight(tmp_path):
    import threading
    import time
    from backend.engine import worker_preflight
    store = worker_preflight.PreflightStore(tmp_path / 'p.json')
    digest = worker_preflight.runtime_digest()
    store.record('local', digest, 'classification', 'train', 'cpu', passed=True, reason='passed', seconds=1)
    worker = threading.Thread(target=lambda: worker_preflight.run_preflight('classification', 'cpu', ('train',), store=store,
                                                                            workdir_root=tmp_path / 'runs'))
    worker.start()
    deadline = time.monotonic() + 30
    while not worker_preflight._CHILDREN and time.monotonic() < deadline:
        time.sleep(0.02)
    worker_preflight.stop_running_preflights()
    worker.join(30)
    assert store.verified('local', digest) == {'classification:train:cpu': store.results('local', digest)['classification:train:cpu']['at']}


def test_the_child_runs_the_device_it_is_given(tmp_path, monkeypatch):
    import json
    import torch
    from backend.engine import worker_preflight
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: False)
    assert worker_preflight.main(['--task', 'classification', '--device', 'cuda', '--stages', 'train', '--workdir', str(tmp_path)]) == 0
    result = json.loads((tmp_path / worker_preflight.RESULT_FILE).read_text(encoding='utf-8'))
    assert 'cuda is not usable' in result['results']['train']['reason'], 'the child checks the requested device, not the CPU'


def test_the_app_lifespan_sweeps_at_start_and_stops_a_preflight_at_shutdown(tmp_path, monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    import backend.main as main
    from backend.api import routes_workers
    from backend.engine import worker_preflight
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    calls, swept = [], threading.Event()
    monkeypatch.setattr(routes_workers, 'stop_for_shutdown', lambda: calls.append('stop'))
    monkeypatch.setattr(worker_preflight, 'sweep_stale_runs', lambda: swept.set())
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app):
        assert swept.wait(10), 'run folders of an earlier app are swept at start'
        assert calls == []
    assert calls == ['stop'], 'the shutdown stops a running preflight'


def test_a_refused_training_start_names_the_preflight_as_a_possible_holder(tmp_path, monkeypatch):
    from fastapi import HTTPException
    from backend.api import routes_training
    from backend.engine.shared_scheduler import ResourceLeases, shared_leases
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    manager = routes_training.TrainingJobManager()
    holder = ResourceLeases(shared_leases().path, owner='pid:preflight')
    assert holder.acquire('worker-preflight-x', 'local-compute', 'all')
    with pytest.raises(HTTPException) as refused:
        manager.start_job('job_refused', 'classification', str(tmp_path / 'data'), str(tmp_path / 'out'))
    assert refused.value.status_code == 409 and 'worker preflight' in refused.value.detail
    assert holder.release('worker-preflight-x')

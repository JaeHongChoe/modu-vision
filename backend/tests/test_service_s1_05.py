"""S1-05: worker capabilities from the installed runtime and support decisions without silent fallback.

Fake torch modules stand in for drivers; nothing is downloaded, trained or connected.
"""
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


def test_a_training_start_on_a_device_this_computer_lacks_is_refused_not_moved_to_the_cpu(tmp_path, monkeypatch):
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
    monkeypatch.setattr(local_training_worker, 'run_owned_training', lambda *args, **kwargs: pytest.fail('nothing may launch'))
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
    body = {'task': 'classification', 'dataset_path': str(source), 'preset': 'fast'}
    for request in ({**body, 'device': 'cuda'}, {**body, 'device': 'mps'}, {**body, 'device': 'cuda:3'}):
        refused = api.post('/api/training/start', json=request)
        assert refused.status_code == 409 and '바꿔 실행하지 않습니다' in refused.text, refused.text

"""Real isolated CPU daemon/outbox recovery; no training or target approval.

The receiver commits one business result, withholds its first HTTP ACK, and
observes an identical replay after the *owned daemon* is killed/restarted.
This is controlled process interruption, never physical power-loss evidence.
"""
import asyncio
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import threading
import time
from types import SimpleNamespace
import uuid

import httpx
import pytest
from PIL import Image
import psutil

from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine import runtime_deadline
from backend.engine.runtime_process_control import process_identity, owned_inspection_process, _runtime_spawn_diagnostic
from backend.engine.service_bootstrap import runtime_command, runtime_cwd
from backend.tests.runtime_release_fixture import real_classification_checkpoints


_identity_now = time.monotonic
_IDENTITY_NOTE = 'Original daemon identity failure observations: '


def _identity_for_daemon_test(process, state, case_deadline):
    try:
        return process_identity(process, state)
    except ValueError as original:
        try:
            _add_identity_failure_note(process, state, case_deadline, original)
        except BaseException:
            pass  # Even a failed observer must preserve the original refusal.
        raise


def _add_identity_failure_note(process, state, case_deadline, original):
    """At most four failure-only observations; no sleep, retry or authority."""
    started = _identity_now()
    stop = min(case_deadline, started + .2)
    note = {'schema': 'modu-vision.daemon-identity-failure-observations/v1',
            'non_atomic': True, 'ownership_verified': False,
            'sample_budget_ms': 200, 'samples': []}
    baseline = getattr(original, 'runtime_spawn_diagnostic', {}).get('process', {}).get('birth')
    if type(baseline) not in (int, float) or not 0 < baseline < float('inf'):
        note['unavailable'] = 'original_birth_unavailable'
    else:
        def within(read):
            if _identity_now() >= stop:
                raise psutil.TimeoutExpired(.2)
            return read()
        for _ in range(4):
            if _identity_now() >= stop:
                break
            sample = {'at_monotonic': _identity_now()}
            note['samples'].append(sample)
            try:
                polled = within(process.poll)
                if polled is not None:
                    sample['original_poll_exit_code'] = polled if type(polled) is int else 'unavailable'
                    break
                owner = within(lambda: psutil.Process(process.pid))
                birth = within(owner.create_time)
                if birth != baseline:
                    sample['unavailable'] = 'original_birth_changed'
                    break
                arguments = within(owner.cmdline)
                # The production projection hashes command vectors and maps
                # observation exceptions to fixed reason codes. Proxy methods
                # check this frame before each additional OS read.
                bounded_owner = SimpleNamespace(
                    create_time=lambda: within(owner.create_time),
                    status=lambda: within(owner.status))
                bounded_process = SimpleNamespace(
                    pid=process.pid, args=process.args, poll=lambda: within(process.poll))
                observed = _runtime_spawn_diagnostic(bounded_process, bounded_owner, arguments, state)
                for key in ('expected', 'observed'):
                    sample[key] = {name: item for name, item in observed[key].items()
                                   if name in ('argc', 'sha256', 'entry', 'state_flag_count',
                                               'state_matches', 'unavailable')}
                sample['process'] = observed['process']
            except (psutil.Error, OSError) as error:
                sample['unavailable'] = ('deadline' if isinstance(error, psutil.TimeoutExpired)
                                         else 'process_observation_unavailable')
                break
    note['sampling_budget_exhausted'] = _identity_now() >= stop
    encoded = _IDENTITY_NOTE + json.dumps(note, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    if len(encoded.encode('utf-8')) <= 4096:
        original.add_note(encoded)


@pytest.fixture
def identity_observation_case(monkeypatch, tmp_path):
    counts = {'process': 0, 'birth': 0, 'cmdline': 0, 'status': 0, 'poll': 0}
    state = tmp_path / 'PRIVATE-STATE'
    command = ['python', '-m', 'backend.engine.inspection_service', '--state-dir', str(state),
               '--token-file', 'PRIVATE-TOKEN-PATH', '--package', 'PRIVATE-MODEL-PATH']
    value = {'command': command, 'birth': 42.5, 'status': 'running', 'poll': None, 'now': 0.0}
    error = ValueError('Spawned runtime command differs from its owned state')
    error.runtime_spawn_diagnostic = {'process': {'birth': 42.5}}
    error.add_note('Original diagnostic note retained')
    def read(key):
        counts[key] += 1
        return value[{'cmdline': 'command'}.get(key, key)]
    owner = SimpleNamespace(pid=733, create_time=lambda: read('birth'),
                            cmdline=lambda: read('cmdline'), status=lambda: read('status'))
    process = SimpleNamespace(pid=733, args=command, poll=lambda: read('poll'))
    def lookup(pid):
        counts['process'] += 1
        assert pid == process.pid
        return owner
    def refuse(*args):
        raise error
    monkeypatch.setitem(globals(), 'process_identity', refuse)
    # Keep doubles in this test module; other CI workers can be observing their
    # own real processes at the same time.
    monkeypatch.setitem(globals(), 'psutil', SimpleNamespace(
        Process=lookup, Error=psutil.Error, TimeoutExpired=psutil.TimeoutExpired))
    monkeypatch.setitem(globals(), '_identity_now', lambda: value['now'])
    return SimpleNamespace(process=process, owner=owner, state=state, error=error,
                           value=value, counts=counts)


def _added_identity_note(error):
    notes = [n for n in error.__notes__ if n.startswith(_IDENTITY_NOTE)]
    assert len(notes) == 1
    assert len(notes[0].encode('utf-8')) <= 4096
    return json.loads(notes[0][len(_IDENTITY_NOTE):])


def test_identity_failure_observation_preserves_refusal_and_redacts_argv(identity_observation_case):
    o = identity_observation_case
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error and o.error.__notes__[0] == 'Original diagnostic note retained'
    note = _added_identity_note(o.error)
    assert note['non_atomic'] is True and note['ownership_verified'] is False
    assert 1 <= len(note['samples']) <= 4
    retained = json.dumps(note)
    for private in [str(o.state), 'PRIVATE-TOKEN-PATH', 'PRIVATE-MODEL-PATH']:
        assert private not in retained
    assert o.counts['cmdline'] <= 4


def test_identity_failure_observation_expired_case_budget_reads_no_process(identity_observation_case):
    o = identity_observation_case
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 0.0)
    assert caught.value is o.error
    assert _added_identity_note(o.error)['samples'] == []
    assert not any(o.counts.values())


@pytest.mark.parametrize('case_deadline,crossed', [(10.0, .201), (.05, .051)])
def test_identity_failure_observation_stops_new_reads_at_its_frame(identity_observation_case, case_deadline, crossed):
    o = identity_observation_case
    def command_crosses_budget():
        o.counts['cmdline'] += 1
        o.value['now'] = crossed
        return o.value['command']
    o.owner.cmdline = command_crosses_budget
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, case_deadline)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['sampling_budget_exhausted'] is True and len(note['samples']) == 1
    assert o.counts == {'process': 1, 'birth': 1, 'cmdline': 1, 'status': 0, 'poll': 1}


def test_identity_failure_observation_unknown_original_birth_reads_no_process(identity_observation_case):
    o = identity_observation_case
    o.error.runtime_spawn_diagnostic = {'process': {'birth': {'unavailable': 'AccessDenied'}}}
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['samples'] == [] and note['unavailable'] == 'original_birth_unavailable'
    assert not any(o.counts.values())


def test_identity_failure_observation_large_vectors_remain_bounded(identity_observation_case):
    o = identity_observation_case
    o.process.args = ['PRIVATE-ARGV'] * 65
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    note = _added_identity_note(o.error)
    assert note['samples'][0]['expected'] == {'unavailable': 'command_bounds', 'argc': 65}
    assert 'PRIVATE' not in json.dumps(note)


@pytest.mark.parametrize('boundary', ['exit', 'birth'])
def test_identity_failure_observation_stops_before_reading_another_command(identity_observation_case, boundary):
    o = identity_observation_case
    o.value['poll' if boundary == 'exit' else 'birth'] = 0 if boundary == 'exit' else 43.0
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    assert len(_added_identity_note(o.error)['samples']) == 1
    assert o.counts['cmdline'] == 0


def test_identity_failure_observation_failure_cannot_replace_original_error(identity_observation_case, monkeypatch):
    o = identity_observation_case
    def unavailable(*args):
        raise SystemExit('PRIVATE-OBSERVER-ERROR')
    monkeypatch.setitem(globals(), '_runtime_spawn_diagnostic', unavailable)
    with pytest.raises(ValueError) as caught:
        _identity_for_daemon_test(o.process, o.state, 10.0)
    assert caught.value is o.error
    assert o.error.__notes__ == ['Original diagnostic note retained']


def test_identity_failure_observation_never_reads_on_success(identity_observation_case, monkeypatch):
    o = identity_observation_case
    identity = {'pid': 733, 'process_created_at': 42.5, 'process_command_sha256': '0' * 64}
    monkeypatch.setitem(globals(), 'process_identity', lambda *_: identity)
    assert _identity_for_daemon_test(o.process, o.state, 10.0) is identity
    assert not any(o.counts.values())


def _sha(path):
    p = Path(path)
    assert p.is_file() and not p.is_symlink()
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _raw_state(database):
    p = Path(database)
    assert p.is_file() and not p.is_symlink()
    with sqlite3.connect(p.as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        return {name: [dict(row) for row in conn.execute('SELECT * FROM ' + name)]
                for name in ('jobs', 'deliveries', 'inbox_items', 'events')}


def _poll(call, predicate, deadline, message):
    last = None
    while time.monotonic() < deadline:
        last = call()
        if predicate(last):
            return last
        time.sleep(min(.025, max(0, deadline - time.monotonic())))
    raise AssertionError(message)


def _prepare_daemon_command(process, command, deadline):
    """Only empty metadata may wait, inside the original readiness frame.

    This grants no ownership: the original strict identity call follows once.
    OS reads are synchronous; a read crossing the frame must fail when it returns.
    """
    message = 'Original10s command readiness cap expired'

    def read(call):
        assert time.monotonic() < deadline, message
        value = call()
        assert time.monotonic() < deadline, message
        return value

    pid = read(lambda: process.pid)
    assert type(pid) is int and pid > 0
    expected = list(command)
    assert process.args == expected
    parent = read(os.getpid)
    birth = None
    while True:
        assert read(lambda: process.pid) == pid
        assert read(process.poll) is None, 'Original daemon exited before command readiness'
        owner = read(lambda: psutil.Process(pid))
        assert read(lambda: owner.pid) == pid
        observed_birth = read(owner.create_time)
        assert type(observed_birth) in (int, float) and 0 < observed_birth < float('inf')
        if birth is None:
            birth = observed_birth
        assert observed_birth == birth, 'Original daemon birth changed before command readiness'
        assert read(owner.ppid) == parent
        assert read(lambda: os.getpgid(pid)) == pid
        assert read(lambda: os.getsid(pid)) == pid
        assert read(owner.status) not in ('zombie', 'dead')
        arguments = read(owner.cmdline)
        if arguments != []:
            assert arguments == expected, 'Original daemon command differs before identity admission'
            return
        time.sleep(min(.025, max(0, deadline - time.monotonic())))


def test_owned_real_cpu_daemon_restarts_committed_inbox_result_and_replays_outbox_once(tmp_path, monkeypatch):
    if os.name != 'posix':
        pytest.skip('POSIX owned-process interruption; Windows native scope is separately waived')
    # Same absolute 240s case cap as the existing Studio-exit daemon scenario;
    # readiness/join/HTTP stay <=10s, model initialization+inference stays30s.
    case_deadline = time.monotonic() + 240
    root = tmp_path / 'owned-daemon-recovery'
    root.mkdir(mode=0o700)
    epoch = uuid.uuid4().hex
    inbox = root / 'input' / 'inbox'
    inbox.mkdir(parents=True)
    image = inbox / ('part-' + epoch + '.png')
    Image.new('RGB', (8, 8), 'white').save(image)
    image_sha = _sha(image)
    model_root = root / 'models' / 'owned_cpu'
    model_root.mkdir(parents=True)
    checkpoint = model_root / 'best_model.pt'
    import torch
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        real_classification_checkpoints({'job_owned_cpu': checkpoint})
    finally:
        torch.set_num_threads(previous_threads)
    graph = get_single_segmentation_flowchart('job_owned_cpu')
    for node in graph.nodes:
        if node.data.node_type == 'inspection':
            node.data.task = 'classification'
    inspection_ids = {node.id for node in graph.nodes if node.data.node_type == 'inspection'}
    package = build_flow_package(pipeline=graph, checkpoints={'job_owned_cpu': checkpoint},
        output_base_dir=root / 'packages', package_name='owned_daemon_cpu',
        runtime_config={'device': 'cpu', 'cpu_threads': 1, 'deadline_ms': 30_000})
    package_root = Path(package['package_path'])
    package_files = {str(p.relative_to(package_root)): _sha(p)
                     for p in package_root.rglob('*') if p.is_file()}
    checkpoint_sha = _sha(checkpoint)
    source_root = Path(runtime_cwd())
    source_paths = ['backend/engine/inspection_service.py', 'backend/engine/input_adapters.py',
        'backend/engine/flow_package.py', 'backend/engine/flow_package_runtime.py',
        'backend/engine/flowchart_engine.py', 'backend/engine/runtime_deadline.py',
        'backend/engine/runtime_process_control.py', 'backend/engine/runtime_configuration.py',
        'backend/engine/service_runtime.py', 'backend/engine/service_listener_security.py',
        'backend/engine/process_isolation.py', 'backend/tests/runtime_release_fixture.py']
    source_pins = {rel: _sha(source_root / rel) for rel in source_paths}
    token_file = root / 'service-token.txt'
    token = uuid.uuid4().hex + uuid.uuid4().hex
    with token_file.open('x', encoding='ascii') as out:
        out.write(token)
    token_file.chmod(0o600)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        service_port = probe.getsockname()[1]
    state = root / 'state'
    receiver_db = root / 'receiver.sqlite3'
    with sqlite3.connect(receiver_db) as conn:
        conn.executescript('CREATE TABLE accepted(job_id TEXT PRIMARY KEY,payload_json TEXT,payload_sha256 TEXT);'
                           'CREATE TABLE requests(number INTEGER PRIMARY KEY,job_id TEXT,payload_sha256 TEXT);')
    first_committed = threading.Event()
    first_release = threading.Event()
    first_ack_attempted = threading.Event()
    receiver_errors = []
    receiver_lock = threading.Lock()
    route = '/owned-result/' + epoch

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            try:
                assert self.path == route
                size = int(self.headers['Content-Length'])
                assert 0 < size <= 1024 * 1024
                self.connection.settimeout(5)
                raw = self.rfile.read(size)
                assert len(raw) == size
                payload = json.loads(raw)
                assert set(payload) == {'job_id', 'model_verdict', 'image_sha256', 'result', 'runtime_identity'}
                assert payload['image_sha256'] == image_sha
                canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                digest = hashlib.sha256(canonical.encode()).hexdigest()
                with receiver_lock, sqlite3.connect(receiver_db) as conn:
                    prior = conn.execute('SELECT payload_json,payload_sha256 FROM accepted WHERE job_id=?',
                                         (payload['job_id'],)).fetchone()
                    if prior is None:
                        conn.execute('INSERT INTO accepted VALUES(?,?,?)', (payload['job_id'], canonical, digest))
                    else:
                        assert prior == (canonical, digest), 'Receiver refuses a different result for the same job'
                    number = conn.execute('SELECT COUNT(*) FROM requests').fetchone()[0] + 1
                    conn.execute('INSERT INTO requests VALUES(?,?,?)', (number, payload['job_id'], digest))
                    conn.commit()
                if number == 1:
                    first_committed.set()
                    assert first_release.wait(5), 'First ACK barrier expired before owned daemon exit'
                    first_ack_attempted.set()
                assert number in (1, 2), 'Unexpected third replay'
                self.send_response(200)
                self.send_header('Content-Length', '0')
                self.end_headers()
            except (BrokenPipeError, ConnectionResetError):
                # The first connection was owned by the killed daemon. Its
                # post-exit ACK delivery is unknown, never an original ACK claim.
                if not first_release.is_set():
                    receiver_errors.append('connection_closed_before_owned_exit')
            except Exception as exc:
                receiver_errors.append(type(exc).__name__)
                first_committed.set()

    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    receiver.daemon_threads = False
    server_thread = threading.Thread(target=receiver.serve_forever, name='owned-result-receiver')
    server_thread.start()
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'LC_ALL') if key in os.environ}
    env.update({'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none', 'HF_HUB_OFFLINE': '1',
        'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
        'VISION_PACKAGE_PARITY_IDENTITY': '1'})
    for key, rel in {'HOME': 'home', 'USERPROFILE': 'home', 'TMPDIR': 'tmp', 'TMP': 'tmp',
        'TEMP': 'tmp', 'XDG_CACHE_HOME': 'cache', 'HF_HOME': 'cache/hf',
        'TORCH_HOME': 'cache/torch', 'MPLCONFIGDIR': 'cache/matplotlib',
        'NUMBA_CACHE_DIR': 'cache/numba'}.items():
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        env[key] = str(directory)
    for key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        env[key] = '1'
    command = runtime_command(['--package', str(package_root), '--state-dir', str(state),
        '--token-file', str(token_file), '--host', '127.0.0.1', '--port', str(service_port),
        '--input-root', str(inbox.parent), '--inbox', str(inbox), '--device', 'cpu',
        '--deadline-ms', '30000', '--max-outstanding', '1', '--max-attempts', '3',
        '--max-queue-age-seconds', '3600', '--result-webhook-url',
        'http://127.0.0.1:' + str(receiver.server_port) + route])
    handles, attempts, outcomes, failures = [], [], [], []
    launch_event = threading.Event()
    original_popen = runtime_deadline.subprocess.Popen

    def observe_real_launch(args, **kwargs):
        process = original_popen(args, **kwargs)
        if args == command:
            handles.append(process)
            launch_event.set()
        # Production ownership census may also invoke its original fixed ps
        # command. Leave every unrelated invocation byte-for-byte unchanged.
        return process

    # Observer only: the original production Popen handle and arguments are
    # returned unchanged. No inference/HTTP/store function is substituted.
    monkeypatch.setattr(runtime_deadline.subprocess, 'Popen', observe_real_launch)
    def api(path, post=False, deadline=None, require200=True):
        stop = min(case_deadline - 10, time.monotonic() + 10, deadline if deadline is not None else case_deadline)
        async def request():
            remaining = stop - time.monotonic()
            assert remaining > 0, 'Original absolute HTTP frame expired'
            async with asyncio.timeout(remaining):
                async with httpx.AsyncClient(base_url='http://127.0.0.1:' + str(service_port),
                        headers={'X-Vision-Token': token}, timeout=remaining, trust_env=False) as client:
                    response = await (client.post(path) if post else client.get(path))
                    value = response.json()
                    assert time.monotonic() <= stop, 'Full response body exceeded its original frame'
                    if require200:
                        assert response.status_code == 200
                    return value if require200 else (response.status_code, value)
        return asyncio.run(request())

    def launch():
        launch_event.clear()
        execution = runtime_deadline.CancellableExecution()
        outcomes.append(None)
        index = len(outcomes) - 1
        remaining_ms = min(90_000, int((case_deadline - time.monotonic() - 10) * 1000))
        assert remaining_ms > 0
        def run():
            try:
                with execution.running() as cancel:
                    outcomes[index] = runtime_deadline.execute_owned_process(command, deadline_ms=remaining_ms,
                        cwd=source_root, env=env, cancel_event=cancel)
            except BaseException as exc:
                failures.append(type(exc).__name__)
        thread = threading.Thread(target=run, name='owned-daemon-executor-' + str(index))
        attempts.append((thread, execution))
        thread.start()
        assert launch_event.wait(min(10, max(0, case_deadline - time.monotonic())))
        process = handles[index]
        deadline = min(case_deadline - 10, time.monotonic() + 10)
        _prepare_daemon_command(process, command, deadline)
        identity = _identity_for_daemon_test(process, state, case_deadline)
        owner = owned_inspection_process(identity, state)
        assert owner is not None and owner.pid == process.pid
        assert owner.ppid() == os.getpid() and owner.cmdline() == command
        assert os.getpgid(process.pid) == os.getsid(process.pid) == process.pid
        def ready():
            assert process.poll() is None, 'Original daemon exited before readiness'
            try:
                status, value = api('/v1/readiness', deadline=deadline, require200=False)
                return value if status == 200 else None
            except httpx.TransportError:
                return None
        _poll(ready, lambda x: isinstance(x, dict) and x['status'] == 'ready' and x['worker_started'],
              deadline, 'Original10s readiness cap expired')
        return process, identity

    proof = None
    try:
        first, identity = launch()
        assert first_committed.wait(min(30, max(0, case_deadline - time.monotonic() - 10)))
        assert not receiver_errors and not first_ack_attempted.is_set()
        raw = _raw_state(state / 'inspection_service.sqlite3')
        assert len(raw['jobs']) == len(raw['deliveries']) == len(raw['inbox_items']) == 1
        job, delivery = raw['jobs'][0], raw['deliveries'][0]
        assert job['state'] == 'delivery_pending' and delivery['state'] == 'sending' and delivery['attempts'] == 1
        assert job['source'] == 'inbox' and job['image_path'] == str(image.resolve()) and job['image_sha256'] == image_sha
        assert job['result_json'] and job['attempts'] == 1 and job['deadline_at'] > time.time()
        binding = json.loads(job['runtime_binding_json'])
        assert _sha(package_root / 'manifest.json') == binding['manifest_sha256']
        assert hashlib.sha256(job['runtime_binding_json'].encode()).hexdigest() == job['runtime_binding_sha256']
        assert binding['device'] == 'cpu' and binding['package_path'] == str(package_root)
        model_result = json.loads(job['result_json'])
        assert model_result['runtime_identity'] == binding and model_result['final_verdict'] == 'OK'
        assert model_result['status'] not in ('failed', 'error', 'timeout') and not model_result.get('error_message')
        assert model_result['runtime_device_identity']['device'] == 'cpu'
        assert model_result['runtime_device_identity']['gpu_uuid'] is None
        assert model_result['runtime_device_identity']['process_id'] != first.pid
        assert any(step['node_id'] in inspection_ids and step['status'] == 'passed'
                   for step in model_result['execution_steps'])
        assert any(crop['predicted_class'] == 'OK' for crop in model_result['crops'])
        http_original = api('/v1/jobs/' + job['job_id'])
        assert http_original['result'] == model_result and http_original['runtime_binding'] == binding
        owner = owned_inspection_process(identity, state)
        assert owner is not None and owner.pid == first.pid and owner.cmdline() == command
        assert not owner.children(recursive=True), 'Refuse crash injection while child work is still observed'
        assert not first_ack_attempted.is_set()
        # Exactly the newly-created verified leader handle; no numeric PID,
        # group/name scan, foreign process or production exit hook is used.
        owner.kill()
        first_thread = attempts[0][0]
        first_thread.join(min(10, max(0, case_deadline - time.monotonic())))
        assert not first_thread.is_alive() and not failures
        assert outcomes[0]['status'] == 'completed' and outcomes[0]['returncode'] == -signal.SIGKILL
        assert outcomes[0]['pid'] == first.pid and not outcomes[0].get('private_diagnostics')
        assert owned_inspection_process(identity, state) is None
        assert _raw_state(state / 'inspection_service.sqlite3') == raw
        first_release.set()
        second, identity2 = launch()
        assert second.pid != first.pid and identity2['process_created_at'] != identity['process_created_at']
        delivery_deadline = min(case_deadline - 10, time.monotonic() + 10)
        terminal = _poll(lambda: api('/v1/jobs/' + job['job_id'], deadline=delivery_deadline),
                         lambda x: x['state'] == 'completed', delivery_deadline, 'Original result did not resume delivery')
        inbox_deadline = min(case_deadline - 10, time.monotonic() + 10)
        _poll(lambda: api('/v1/adapters', deadline=inbox_deadline), lambda x: x['file_inbox'] == 'connected',
              inbox_deadline, 'Restarted original inbox did not finish its settled scan')
        after = _raw_state(state / 'inspection_service.sqlite3')
        assert len(after['jobs']) == len(after['deliveries']) == len(after['inbox_items']) == 1
        stable = [k for k in job if k not in {'state', 'verdict', 'error', 'updated_at'}]
        assert {k: after['jobs'][0][k] for k in stable} == {k: job[k] for k in stable}
        assert terminal['result'] == model_result and terminal['runtime_binding'] == binding
        assert terminal['error'] is None and terminal['verdict'] == terminal['model_verdict'] == 'OK'
        assert after['deliveries'][0]['state'] == 'sent' and after['deliveries'][0]['attempts'] == 2
        assert after['inbox_items'] == raw['inbox_items']
        assert [e['state'] for e in after['events']] == ['queued', 'running', 'delivery_pending', 'delivery_pending', 'completed']
        with sqlite3.connect(receiver_db) as conn:
            accepted = conn.execute('SELECT * FROM accepted').fetchall()
            requests = conn.execute('SELECT * FROM requests ORDER BY number').fetchall()
        assert len(accepted) == 1 and len(requests) == 2
        assert requests[0][1:] == requests[1][1:] == (job['job_id'], accepted[0][2])
        assert json.loads(accepted[0][1]) == {'job_id': job['job_id'], 'model_verdict': 'OK',
            'image_sha256': image_sha, 'result': model_result, 'runtime_identity': binding}
        assert not receiver_errors
        assert api('/v1/runtime/shutdown', post=True)['status'] == 'stopping'
        attempts[1][0].join(min(10, max(0, case_deadline - time.monotonic())))
        assert not attempts[1][0].is_alive() and not failures
        assert outcomes[1]['status'] == 'completed' and outcomes[1]['returncode'] == 0
        assert outcomes[1]['pid'] == second.pid and not outcomes[1].get('private_diagnostics')
        assert owned_inspection_process(identity2, state) is None
        assert _sha(image) == image_sha and _sha(checkpoint) == checkpoint_sha
        assert {str(p.relative_to(package_root)): _sha(p) for p in package_root.rglob('*') if p.is_file()} == package_files
        assert {rel: _sha(source_root / rel) for rel in source_paths} == source_pins
        proof = {'scope': 'actual_owned_cpu_daemon_interruption_and_outbox_replay_only',
            'fixture_input_epoch': epoch, 'original_owned_identity': identity, 'restarted_owned_identity': identity2,
            'outcomes': outcomes, 'original_raw_state': raw, 'recovered_raw_state': after,
            'receiver_business_rows': accepted, 'receiver_request_rows': requests,
            'first_business_commit_before_crash': True, 'first_ack_sent_before_crash': False,
            'post_exit_first_ack_wire_delivery': 'unknown', 'actual_inference_count': 1,
            'receiver_request_count': 2, 'receiver_unique_business_result_count': 1,
            'source_pins': source_pins, 'original_image_sha256': image_sha,
            'original_checkpoint_sha256': checkpoint_sha, 'package_files': package_files,
            'physical_power_loss_verified': False, 'boot_or_os_service_registration_verified': False,
            'gpu_or_hardware_verified': False, 'quality_approved': False, 'parent_accepted': False,
            'complete_process_tree_verified': False}
    finally:
        first_release.set()
        unresolved = []
        for thread, execution in attempts:
            if thread.is_alive():
                execution.cancel()  # Original production owner only, preserving uncertainty retention.
                thread.join(10)
                if thread.is_alive():
                    unresolved.append(thread.name)
        receiver.shutdown()
        receiver.server_close()
        server_thread.join(10)
        assert not unresolved, 'Owned executor remains unresolved; retain all workspace bytes'
        assert not server_thread.is_alive(), 'Owned receiver remains unresolved'
    assert proof is not None
    (root / 'owned-real-daemon-recovery-proof.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')

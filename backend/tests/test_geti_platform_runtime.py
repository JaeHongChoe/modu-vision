"""Supervisor guard tests; real owned child coverage is separate and opt-in."""
from pathlib import Path
import sys
import pytest


def test_unavailable_runtime_is_lazy_and_rejects_nonopaque_keys(tmp_path):
    from backend.platforms.geti.runtime import GetiRuntimeManager
    manager = GetiRuntimeManager(tmp_path, python=Path('/missing/python'))
    assert manager.status('owned-project')['state'] == 'unavailable'
    assert not list(tmp_path.glob('*/data'))
    for key in ('../other', '', '/tmp/shared', 'p/q'):
        with pytest.raises(ValueError):
            manager.client(key)


def test_wrong_python_version_never_launches_child(tmp_path):
    from backend.platforms.geti.runtime import GetiRuntimeManager
    manager = GetiRuntimeManager(tmp_path, python=Path(sys.executable))
    if sys.version_info[:2] == (3, 14):
        pytest.skip('this guard is exercised with the 3.13 parent interpreter')
    assert manager.status('owned')['state'] == 'unavailable'
    with pytest.raises(RuntimeError, match='3.14'):
        manager.client('owned')
    assert not list(tmp_path.glob('*/data'))


def test_client_fences_absolute_urls_and_sanitizes_child_error(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from backend.platforms.geti.client import GetiClient, GetiError
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get('X-Modu-Geti-Secret') != 'owned-secret':
                self.send_response(403)
            else:
                self.send_response(500)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(b'{"detail":"/Users/private owned-secret http://127.0.0.1:1234"}')
        def log_message(self, *_): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = GetiClient(server.server_port, 'owned-secret')
        with pytest.raises(ValueError):
            client.request('GET', 'http://example.com/')
        with pytest.raises(ValueError):
            client.request('GET', '//example.com/')
        with pytest.raises(GetiError) as info:
            client.request('GET', '/health')
        assert info.value.status_code == 500
        assert 'owned-secret' not in str(info.value)
        assert '/Users/private' not in str(info.value)
        assert '127.0.0.1' not in str(info.value)
    finally:
        server.shutdown()
        server.server_close()


def test_owned_real_upstream_child_requires_secret_on_every_route_and_restarts(tmp_path):
    import http.client
    import os
    from backend.platforms.geti.runtime import GetiRuntimeManager
    python = os.environ.get('MODU_GETI_QA_PYTHON')
    if not python:
        pytest.skip('explicit isolated Python 3.14 QA environment required')
    manager = GetiRuntimeManager(tmp_path, python=Path(python))
    try:
        client = manager.client('owned-a')
        assert client.request('GET', '/health') == {'status': 'ok'}
        for path in ('/health', '/api/projects', '/api/openapi.json', '/no-such-route'):
            connection = http.client.HTTPConnection('127.0.0.1', client._port, timeout=3)
            connection.request('GET', path)
            response = connection.getresponse()
            assert response.status == 403, path
            response.read()
            connection.close()
        created = client.request('POST', '/api/projects', json={'name': 'Owned wrapper test', 'task': {'task_type': 'classification', 'exclusive_labels': True, 'labels': [{'name': 'red', 'color': '#FF0000'}, {'name': 'blue', 'color': '#0000FF'}]}})
        identifier = created['id']
        assert manager.status('owned-b')['state'] == 'stopped'
        manager.stop('owned-a')
        with pytest.raises(OSError):
            import socket
            socket.create_connection(('127.0.0.1', client._port), timeout=1)
        again = manager.client('owned-a')
        assert again.request('GET', '/api/projects/' + identifier)['name'] == 'Owned wrapper test'
        other = manager.client('owned-b')
        assert other.request('GET', '/api/projects') == []
    finally:
        manager.shutdown()


def test_worker_egress_guard_denies_external_dns_and_connections(tmp_path):
    import os
    import subprocess
    overlay = Path(__file__).resolve().parents[2] / 'platforms/geti/runtime'
    env = {**os.environ, 'PYTHONPATH': str(overlay), 'MODU_GETI_SECRET': 'owned-test', 'LOG_DIR': str(tmp_path)}
    script = '''import socket
for action in (lambda: socket.getaddrinfo('example.com', 443), lambda: socket.socket().connect(('192.0.2.10', 443))):
    try: action()
    except PermissionError: pass
    else: raise AssertionError('outbound access escaped worker guard')
'''
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert len((tmp_path / 'egress-denials.jsonl').read_text().splitlines()) == 2


def test_invalid_installer_inventory_is_rejected_before_executable_runs(tmp_path, monkeypatch):
    import json
    from backend.platforms.geti.runtime import GetiRuntimeManager
    marker = tmp_path / 'executed'
    python = tmp_path / 'python'
    python.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\necho 3.14\n')
    python.chmod(0o700)
    descriptor = tmp_path / 'runtime.json'
    descriptor.write_text(json.dumps({'version': 1, 'python': 'python', 'source_root': 'source', 'entrypoint': 'overlay/server.py', 'upstream_commit': 'ddcf394d550bf1b84ca262888260043af361f5e7', 'inventory': 'inventory.json', 'inventory_sha256': '0' * 64, 'weights_root': 'weights'}))
    monkeypatch.setenv('MODU_GETI_RUNTIME_DESCRIPTOR', str(descriptor))
    manager = GetiRuntimeManager(tmp_path / 'instances')
    assert manager.status('owned')['state'] == 'unavailable'
    assert not marker.exists(), 'unverified installed executable was launched'


def test_child_exits_when_owning_parent_disappears(tmp_path):
    import json
    import os
    import signal
    import socket
    import subprocess
    import time
    python = os.environ.get('MODU_GETI_QA_PYTHON')
    if not python: pytest.skip('explicit isolated Python 3.14 QA environment required')
    receipt = tmp_path / 'started.json'
    script = '''import json,os,sys
from pathlib import Path
from backend.platforms.geti.runtime import GetiRuntimeManager
manager=GetiRuntimeManager(Path(sys.argv[1])/'instances',python=Path(sys.argv[2]))
client=manager.client('owned')
process=manager._instances['owned']['process']
(Path(sys.argv[1])/'started.json').write_text(json.dumps({'port':client._port,'pid':process.pid}))
os._exit(0)
'''
    owner = subprocess.Popen([sys.executable, '-c', script, str(tmp_path), python], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    record = None
    try:
        assert owner.wait(timeout=90) == 0, owner.stderr.read().decode()
        record = json.loads(receipt.read_text())
        deadline = time.monotonic() + 24
        closed = False
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', record['port']), timeout=0.3): pass
            except OSError:
                closed = True
                break
            time.sleep(0.2)
        assert closed, 'owned child remained an orphan after its parent exited'
    finally:
        if record and os.name != 'nt':
            try: os.killpg(record['pid'], signal.SIGKILL)
            except ProcessLookupError: pass
        if owner.poll() is None: owner.kill()
        owner.stderr.close()


def test_uploaded_prediction_image_is_rgb_and_bounded_before_inference():
    import importlib.util
    import io
    from PIL import Image
    path = Path(__file__).resolve().parents[2] / 'platforms/geti/runtime/image_input.py'
    assert path.is_file(), 'bounded upload image decoder is missing'
    spec = importlib.util.spec_from_file_location('modu_image_input', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    stream = io.BytesIO()
    Image.new('RGB', (3, 2), (0, 0, 255)).save(stream, format='PNG')
    payload = stream.getvalue()
    decoded = module.decode_prediction_image(payload)
    assert decoded.shape == (2, 3, 3)
    assert decoded[0, 0].tolist() == [0, 0, 255]
    for body, limits in [(b'not an image', {}), (payload, {'max_bytes': 2}), (payload, {'max_pixels': 4})]:
        with pytest.raises(ValueError): module.decode_prediction_image(body, **limits)


def test_worker_exit_observer_waits_for_actual_process_exit():
    import importlib.util
    import multiprocessing as mp
    import time
    path = Path(__file__).resolve().parents[2] / 'platforms/geti/runtime/worker_status.py'
    assert path.is_file(), 'actual worker exit observer is missing'
    spec = importlib.util.spec_from_file_location('modu_worker_status', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    workers = module.WorkerRegistry()
    process = mp.get_context('spawn').Process(target=time.sleep, args=(0.3,))
    process.start()
    try:
        workers.register('job-a', process)
        assert workers.exited('job-a') is False
        assert workers.exited('unknown') is False
        process.join(timeout=5)
        assert process.exitcode == 0
        assert workers.exited('job-a') is True
    finally:
        if process.is_alive(): process.terminate()
        process.join(timeout=5)


def test_explicit_recovery_only_stops_recorded_owned_generation(tmp_path):
    import json
    import os
    import subprocess
    import psutil
    from backend.platforms.geti.runtime import GetiRuntimeManager, PINNED_SHA
    manager = GetiRuntimeManager(tmp_path)
    owned = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], env={**os.environ, 'MODU_GETI_INSTANCE_GENERATION': 'generation-a'}, start_new_session=True)
    foreign = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    def record(key, process, generation):
        folder = tmp_path / key
        folder.mkdir()
        (folder / 'instance.json').write_text(json.dumps({'project_key': key, 'source_sha': PINNED_SHA}))
        (folder / 'process.json').write_text(json.dumps({'generation': generation, 'pid': process.pid, 'process_group': process.pid, 'created_at': psutil.Process(process.pid).create_time()}))
    try:
        record('owned', owned, 'generation-a')
        record('foreign', foreign, 'generation-b')
        proof = manager.recover('owned')
        owned.wait(timeout=5)
        assert proof['generation'] == 'generation-a' and proof['worker_exited'] is True
        assert manager.exit_evidence('owned', 'generation-a')['worker_exited'] is True
        with pytest.raises(RuntimeError): manager.recover('foreign')
        assert foreign.poll() is None, 'recovery terminated a process without matching generation ownership'
    finally:
        for process in (owned, foreign):
            if process.poll() is None: process.kill()
            process.wait(timeout=5)

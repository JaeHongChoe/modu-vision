"""Launcher lifecycle tests use temporary synthetic accounts and owned loopback sockets."""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_parser_defaults_and_zero_port():
    from backend.web import parse_args
    args = parse_args([])
    assert args.port == 8443 and args.data_dir is None and args.no_open is False
    assert parse_args(['--port', '0', '--no-open']).port == 0


@pytest.mark.parametrize('args', [
    ['--port', '-1'], ['--port', '65536'], ['--port', 'no'],
    ['--host', '0.0.0.0'], ['--cert-file', 'cert.pem'], ['--key-file', 'key.pem'],
])
def test_invalid_or_exposing_cli_options_are_refused(args):
    from backend.web import parse_args
    with pytest.raises(SystemExit) as result:
        parse_args(args)
    assert result.value.code == 2


def test_help_has_no_build_engine_or_data_side_effects(tmp_path):
    target = tmp_path / 'not-created'
    completed = subprocess.run([sys.executable, '-m', 'backend.web', '--data-dir', str(target), '--help'],
                               cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    assert '--no-open' in completed.stdout
    assert not target.exists()


def test_cli_import_does_not_import_engine_or_main():
    completed = subprocess.run([sys.executable, '-c',
        "import sys; import backend.web; assert 'backend.main' not in sys.modules; "
        "assert not any(x.startswith('backend.engine') for x in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr


def store_at(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    return AccountStore(tmp_path / 'auth/accounts.sqlite')


def test_first_run_noninteractive_refuses_with_setup_command(tmp_path, monkeypatch):
    from backend.web import ensure_first_account
    store = store_at(tmp_path)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('should not read'))
    with pytest.raises(RuntimeError, match='interactive terminal.*npm run web'):
        ensure_first_account(store, tmp_path)
    assert store.users() == []


class Terminal(io.StringIO):
    def isatty(self):
        return True


def test_first_account_uses_hidden_confirmed_password_and_preserves_existing(tmp_path, monkeypatch, capsys):
    from backend.web import ensure_first_account
    import getpass
    store = store_at(tmp_path)
    monkeypatch.setattr(sys, 'stdin', Terminal('synthetic-admin\n'))
    passwords = iter(['synthetic-only-password', 'synthetic-only-password'])
    prompts = []
    def hidden(prompt, **kwargs):
        prompts.append(prompt)
        return next(passwords)
    monkeypatch.setattr(getpass, 'getpass', hidden)
    ensure_first_account(store, tmp_path)
    assert len(prompts) == 2
    assert store.login('synthetic-admin', 'synthetic-only-password')['user']['administrator']
    before = store.users()
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    ensure_first_account(store, tmp_path)
    assert store.users() == before
    output = capsys.readouterr()
    assert 'synthetic-only-password' not in output.out + output.err
    assert b'synthetic-only-password' not in store.path.read_bytes()


def test_confirmation_mismatch_creates_no_account(tmp_path, monkeypatch):
    from backend.web import ensure_first_account
    import getpass
    store = store_at(tmp_path)
    monkeypatch.setattr(sys, 'stdin', Terminal('synthetic-admin\n'))
    passwords = iter(['synthetic-only-password', 'different-password'])
    monkeypatch.setattr(getpass, 'getpass', lambda *a, **k: next(passwords))
    with pytest.raises(ValueError, match='match'):
        ensure_first_account(store, tmp_path)
    assert store.users() == []


def test_password_echo_fallback_is_refused(tmp_path, monkeypatch):
    from backend.web import ensure_first_account
    import getpass
    import warnings
    store = store_at(tmp_path)
    monkeypatch.setattr(sys, 'stdin', Terminal('synthetic-admin\n'))
    def unsafe(*a, **k):
        warnings.warn('Password may be echoed', getpass.GetPassWarning)
        return 'synthetic-only-password'
    monkeypatch.setattr(getpass, 'getpass', unsafe)
    with pytest.raises(RuntimeError, match='hidden password'):
        ensure_first_account(store, tmp_path)
    assert store.users() == []


def test_loopback_socket_owns_actual_port_and_preserves_conflicting_owner():
    from backend.web import bind_loopback
    with bind_loopback(0) as owner:
        actual = owner.getsockname()
        assert actual[0] == '127.0.0.1' and actual[1] > 0
        with pytest.raises(RuntimeError, match='port.*in use'):
            bind_loopback(actual[1])
        assert owner.getsockname() == actual
    with bind_loopback(actual[1]) as replacement:
        assert replacement.getsockname() == actual


def paths_at(tmp_path, monkeypatch):
    from backend.local_web_state import configure_environment, web_paths
    for name in ('VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_SPLIT_MANIFEST_DIR', 'MODU_THUMBNAIL_CACHE_DIR',
                 'MODU_FLOW_TEMPLATE_DIR', 'VISION_RESOURCE_LEASE_DB'):
        monkeypatch.setenv(name, os.environ.get(name, ''))
    paths = web_paths(tmp_path / 'web')
    configure_environment(paths)
    return paths


@pytest.mark.parametrize('kind', ['root-link', 'child-link', 'child-hardlink', 'output-file'])
def test_build_refuses_linked_or_escaping_output_before_vite_can_delete(tmp_path, monkeypatch, kind):
    from backend.local_web_runtime import build_renderer
    paths = paths_at(tmp_path, monkeypatch)
    output = paths.root / 'renderer'
    private = tmp_path / 'unrelated'
    private.mkdir()
    marker = private / 'keep.js'
    marker.write_text('private')
    if kind == 'root-link':
        output.symlink_to(private, target_is_directory=True)
    elif kind == 'output-file':
        output.write_text('private')
    else:
        output.mkdir()
        if kind == 'child-link':
            (output / 'assets').symlink_to(private, target_is_directory=True)
        else:
            (output / 'keep.js').hardlink_to(marker)
    with pytest.raises((ValueError, RuntimeError), match='[Ll]ink|regular|directory'):
        build_renderer(paths)
    assert marker.read_text() == 'private'


def test_same_root_lock_prevents_second_build_and_is_released(tmp_path, monkeypatch):
    from backend.local_web_state import instance_lock
    from backend.web import main
    paths = paths_at(tmp_path, monkeypatch)
    with instance_lock(paths):
        result = main(['--data-dir', str(paths.root), '--no-open'])
        assert result == 1
        assert not (paths.root / 'renderer').exists()
    with instance_lock(paths):
        pass


def test_build_uses_owned_output_and_source_cwd_without_shell_and_restores_handlers(tmp_path, monkeypatch):
    from backend.local_web_runtime import build_renderer
    import backend.local_web_runtime as runtime
    import signal
    paths = paths_at(tmp_path, monkeypatch)
    before_cwd = Path.cwd()
    previous = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    source = tmp_path / 'source with spaces'
    vite = source / 'node_modules/vite/bin/vite.js'
    vite.parent.mkdir(parents=True)
    vite.write_text("""
const fs=require('fs'); const out=process.argv[process.argv.indexOf('--outDir')+1];
fs.writeFileSync(out+'/index.html','synthetic build');
fs.writeFileSync(out+'/argv.json',JSON.stringify({argv:process.argv.slice(2),cwd:process.cwd()}));
""")
    monkeypatch.setattr(runtime, 'ROOT', source)
    with __import__('backend.local_web_state', fromlist=['instance_lock']).instance_lock(paths):
        output = build_renderer(paths)
    assert output == paths.root / 'renderer'
    observed = json.loads((output / 'argv.json').read_text())
    assert observed == {'argv': ['build', '--outDir', str(output), '--emptyOutDir', '--base', '/'], 'cwd': str(source)}
    assert Path.cwd() == before_cwd
    assert {signum: signal.getsignal(signum) for signum in previous} == previous


def test_failed_build_does_not_start_app_or_emit_ready(tmp_path, monkeypatch, capsys):
    from backend.web import main
    import backend.local_web_runtime as runtime
    paths = paths_at(tmp_path, monkeypatch)
    store_at(paths.root).bootstrap('synthetic-admin', 'synthetic password 123')
    def fail(*args):
        raise RuntimeError('Renderer build failed')
    monkeypatch.setattr(runtime, 'build_renderer', fail)
    assert main(['--data-dir', str(paths.root), '--port', '0', '--no-open']) == 1
    assert 'MODU_WEB_READY=' not in capsys.readouterr().out
    from backend.local_web_state import instance_lock
    with instance_lock(paths):
        pass


class HealthApp:
    """Lightweight test API; product Uvicorn/TLS/readiness remain real."""
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.started = False
        self.stopped = False
        self.requests = []

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            assert (await receive())['type'] == 'lifespan.startup'
            self.started = True
            await send({'type': 'lifespan.startup.complete'})
            assert (await receive())['type'] == 'lifespan.shutdown'
            self.stopped = True
            await send({'type': 'lifespan.shutdown.complete'})
        else:
            self.requests.append(scope)
            body = json.dumps({'status': 'ok' if self.healthy else 'broken'}).encode()
            await send({'type': 'http.response.start', 'status': 200,
                        'headers': [(b'content-length', str(len(body)).encode())]})
            await send({'type': 'http.response.body', 'body': body})


def test_readiness_requires_real_owned_tls_health_and_redacts_secrets(tmp_path, monkeypatch, capsys):
    from backend.local_web_state import ensure_tls
    from backend.local_web_runtime import run_https, certificate_fingerprint
    from backend.web import bind_loopback
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    paths = paths_at(tmp_path, monkeypatch)
    cert, key = ensure_tls(paths)
    renderer = paths.root / 'renderer'
    renderer.mkdir()
    (renderer / 'index.html').write_text('synthetic build')
    api = HealthApp()
    opened = []
    import webbrowser
    monkeypatch.setattr(webbrowser, 'open', lambda url: opened.append(url))
    import uvicorn
    servers = []
    original_server = uvicorn.Server
    class RecordedServer(original_server):
        def __init__(self, config):
            super().__init__(config)
            servers.append(self)
    monkeypatch.setattr(uvicorn, 'Server', RecordedServer)
    async def run():
        with bind_loopback(0) as sock:
            origin = f'https://localhost:{sock.getsockname()[1]}'
            async def stop_after_ready():
                while not opened:
                    await asyncio.sleep(.01)
                servers[0].should_exit = True
            stopper = asyncio.create_task(stop_after_ready())
            try:
                await run_https(api, renderer, origin, sock, cert, key, paths, no_open=False)
            finally:
                stopper.cancel()
                await asyncio.gather(stopper, return_exceptions=True)
            return origin
    origin = asyncio.run(run())
    assert api.started and api.stopped
    assert any(scope['path'] == '/health' and scope['scheme'] == 'https' for scope in api.requests)
    assert opened == [origin]
    ready_lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith('MODU_WEB_READY=')]
    assert len(ready_lines) == 1
    ready = json.loads(ready_lines[0].split('=', 1)[1])
    expected = x509.load_pem_x509_certificate(cert.read_bytes()).fingerprint(hashes.SHA256()).hex()
    assert ready == {'url': origin, 'pid': os.getpid(), 'data_dir': str(paths.root),
                     'certificate_file': str(cert), 'certificate_sha256': expected}
    assert certificate_fingerprint(cert) == expected
    assert expected != hashlib.sha256(cert.read_bytes()).hexdigest()


def test_failed_health_cleans_owned_server_without_ready_or_browser(tmp_path, monkeypatch, capsys):
    from backend.local_web_state import ensure_tls
    from backend.local_web_runtime import run_https
    from backend.web import bind_loopback
    paths = paths_at(tmp_path, monkeypatch)
    cert, key = ensure_tls(paths)
    renderer = paths.root / 'renderer'
    renderer.mkdir()
    api = HealthApp(healthy=False)
    async def run():
        with bind_loopback(0) as sock:
            port = sock.getsockname()[1]
            with pytest.raises(RuntimeError, match='readiness'):
                await run_https(api, renderer, f'https://localhost:{port}', sock, cert, key,
                                paths, no_open=True, readiness_timeout=.5)
        with bind_loopback(port):
            pass
    asyncio.run(run())
    assert api.started and api.stopped
    assert 'MODU_WEB_READY=' not in capsys.readouterr().out


def test_noninteractive_cli_pins_stores_before_engine_import_without_build(tmp_path):
    target = tmp_path / 'fresh-root'
    script = '''
import os, sys
from pathlib import Path
from backend.web import main
root = Path(sys.argv[1])
assert main(['--data-dir', str(root), '--port', '0', '--no-open']) == 1
assert os.environ['VISION_AI_STUDIO_USER_DATA_DIR'] == str(root)
assert os.environ['MODU_SPLIT_MANIFEST_DIR'] == str(root/'splits')
assert os.environ['VISION_RESOURCE_LEASE_DB'] == str(root/'resource_leases.sqlite3')
assert (root/'auth/accounts.sqlite').is_file()
assert 'backend.main' not in sys.modules
assert not (root/'renderer').exists()
'''
    result = subprocess.run([sys.executable, '-c', script, str(target)], cwd=ROOT,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert 'interactive terminal' in result.stderr
    assert 'MODU_WEB_READY=' not in result.stdout


def test_open_stream_has_bounded_drain_and_backend_lifespan_cleanup(tmp_path, monkeypatch, capsys):
    from backend.local_web_state import ensure_tls
    from backend.local_web_runtime import run_https
    from backend.web import bind_loopback
    import ssl
    import uvicorn
    import webbrowser
    paths = paths_at(tmp_path, monkeypatch)
    cert, key = ensure_tls(paths)
    renderer = paths.root / 'renderer'
    renderer.mkdir()
    class StreamApp(HealthApp):
        cancelled = False
        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http' and scope['path'] == '/api/stream':
                await send({'type': 'http.response.start', 'status': 200,
                            'headers': [(b'content-type', b'text/event-stream')]})
                await send({'type': 'http.response.body', 'body': b'data: waiting\n\n', 'more_body': True})
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancelled = True
                    raise
            else:
                await super().__call__(scope, receive, send)
    api = StreamApp()
    servers = []
    original_server = uvicorn.Server
    class RecordedServer(original_server):
        def __init__(self, config):
            super().__init__(config)
            servers.append(self)
    monkeypatch.setattr(uvicorn, 'Server', RecordedServer)
    opened = []
    monkeypatch.setattr(webbrowser, 'open', lambda url: opened.append(url))
    async def run():
        with bind_loopback(0) as sock:
            port = sock.getsockname()[1]
            origin = f'https://localhost:{port}'
            serving = asyncio.create_task(run_https(api, renderer, origin, sock, cert, key, paths, no_open=True))
            writer = None
            try:
                deadline = asyncio.get_running_loop().time() + 5
                while not api.requests and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(.01)
                assert api.requests, 'local TLS health was not reached'
                context = ssl.create_default_context(cafile=str(cert))
                reader, writer = await asyncio.open_connection('127.0.0.1', port, ssl=context, server_hostname='localhost')
                writer.write(f'GET /api/stream HTTP/1.1\r\nHost: localhost:{port}\r\nOrigin: {origin}\r\n\r\n'.encode())
                await writer.drain()
                header = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2)
                assert b'200 OK' in header
                await asyncio.sleep(.1)  # Let readiness publish before stopping.
                servers[0].should_exit = True
                await asyncio.wait_for(serving, 8)
            finally:
                if writer:
                    writer.close()
                    await writer.wait_closed()
                if not serving.done():
                    serving.cancel()
                await asyncio.gather(serving, return_exceptions=True)
    asyncio.run(run())
    assert api.cancelled and api.stopped
    assert opened == []
    assert 'MODU_WEB_READY=' in capsys.readouterr().out


@pytest.mark.skipif(os.name != 'posix', reason='POSIX owned build group and native signals')
def test_sigterm_during_build_keeps_lock_until_owned_descendant_cannot_write(tmp_path):
    """A dying leader must not hand its output to another owner while Vite children write."""
    import signal
    import time
    from backend.local_web_state import instance_lock, web_paths
    source = tmp_path / 'source'
    vite = source / 'node_modules/vite/bin/vite.js'
    vite.parent.mkdir(parents=True)
    vite.write_text('''
const fs = require('fs'); const {spawn} = require('child_process');
const out = process.argv[process.argv.indexOf('--outDir') + 1];
const writer = spawn(process.execPath, ['-e', `
  const fs=require('fs'); const out=process.argv[1];
  process.on('SIGTERM', () => {});
  fs.writeFileSync(out+'/descendant-started', String(process.pid));
  setInterval(() => fs.appendFileSync(out+'/writes', 'w'), 20);
`, out], {stdio:'ignore'});
process.on('SIGTERM', () => {
  fs.writeFileSync(out+'/terminating', 'yes');
  setTimeout(() => process.exit(0), 250);
});
fs.writeFileSync(out+'/leader-started', String(process.pid));
setInterval(() => {}, 1000);
''')
    data = tmp_path / 'data'
    output = data / 'renderer'
    script = '''
from pathlib import Path
import sys
from backend.local_web_state import configure_environment, instance_lock, web_paths
import backend.local_web_runtime as runtime
runtime.ROOT = Path(sys.argv[1])
paths = web_paths(sys.argv[2]); configure_environment(paths)
import signal
previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
try:
    with instance_lock(paths):
        runtime.build_renderer(paths)
finally:
    (paths.root/'handlers-restored').write_text(str(all(signal.getsignal(sig) == handler for sig, handler in previous.items())))
'''
    log = (tmp_path / 'launcher.log').open('wb')
    launcher = subprocess.Popen([sys.executable, '-c', script, str(source), str(data)], cwd=ROOT,
                                stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
    # This independent process is deliberately outside both owned groups.
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)'], start_new_session=True)
    owned_pids = []
    try:
        deadline = time.monotonic() + 5
        while not (output / 'writes').exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (output / 'writes').exists(), 'synthetic descendant did not start'
        owned_pids = [int((output / name).read_text()) for name in ('leader-started', 'descendant-started')]
        with pytest.raises(RuntimeError, match='owns this data root'):
            with instance_lock(web_paths(data)):
                pass
        launcher.send_signal(signal.SIGTERM)
        deadline = time.monotonic() + 2
        while not (output / 'terminating').exists() and launcher.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        # Termination cleanup must retain the lock, including a repeated signal.
        if launcher.poll() is None:
            launcher.send_signal(signal.SIGTERM)
            with pytest.raises(RuntimeError, match='owns this data root'):
                with instance_lock(web_paths(data)):
                    pass
        launcher.wait(timeout=7)
        with instance_lock(web_paths(data)):
            before = (output / 'writes').read_bytes()
            time.sleep(.2)
            after = (output / 'writes').read_bytes()
        assert after == before, 'old builder descendant wrote while the next owner held the lock'
        assert launcher.returncode == 143, (launcher.returncode, (tmp_path / 'launcher.log').read_text())
        assert (data / 'handlers-restored').read_text() == 'True'
        assert unrelated.poll() is None, 'unrelated process was terminated'
    finally:
        # Own only these disposable test children, even when exercising RED.
        for group in (launcher.pid, owned_pids[0] if owned_pids else None):
            if group:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for pid in owned_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if launcher.poll() is None:
            launcher.kill()
        launcher.communicate(timeout=3)
        unrelated.terminate()
        unrelated.wait(timeout=3)
        log.close()

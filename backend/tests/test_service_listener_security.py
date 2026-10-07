"""Server entrypoints refuse exposed plaintext and unprotected secret files."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest


@pytest.mark.parametrize('entrypoint', ['agent', 'inspection'])
def test_external_plaintext_listener_is_refused_before_creating_state(tmp_path, monkeypatch, entrypoint):
    module = 'backend.engine.fleet_agent' if entrypoint == 'agent' else 'backend.engine.inspection_service'
    env = dict(os.environ, VISION_FIELD_AGENT_TOKEN='test-server-token-12345678',
               VISION_INSPECTION_TOKEN='test-server-token-12345678')
    state = tmp_path / 'not-created'
    result = subprocess.run([sys.executable, '-m', module, '--state-dir', str(state), '--host', '0.0.0.0'],
        env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert 'TLS' in result.stderr, result.stderr
    assert not state.exists(), 'Rejected listener must not create or launch a runtime'
    assert 'test-server-token-12345678' not in result.stdout + result.stderr


@pytest.mark.parametrize('damage', ['world_readable', 'linked_file', 'linked_directory', 'oversized', 'hardlink', 'control_character'])
def test_unprotected_secret_file_cannot_launch_the_agent(tmp_path, damage):
    secret = tmp_path / 'server-token'; secret.write_text('controlled-token-12345678\n'); secret.chmod(0o600)
    if damage == 'world_readable': secret.chmod(0o644)
    elif damage == 'linked_file':
        original = tmp_path / 'original'; secret.rename(original); secret.symlink_to(original)
    elif damage == 'linked_directory':
        directory = tmp_path / 'secrets'; directory.mkdir(); secret.rename(directory / secret.name)
        linked = tmp_path / 'linked'; linked.symlink_to(directory, target_is_directory=True); secret = linked / secret.name
    elif damage == 'oversized': secret.write_bytes(b'a' * 5000)
    elif damage == 'hardlink': os.link(secret, tmp_path / 'other-name')
    else: secret.write_bytes(b'controlled-token-12345678\nsecond-line')
    env = dict(os.environ); env.pop('VISION_FIELD_AGENT_TOKEN', None)
    state = tmp_path / 'not-created'
    result = subprocess.run([sys.executable, '-m', 'backend.engine.fleet_agent', '--state-dir', str(state),
        '--token-file', str(secret)], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0 and not state.exists()
    assert 'controlled-token-12345678' not in result.stdout + result.stderr


@pytest.mark.skipif(os.name != 'posix', reason='Private server-file permission acceptance is POSIX only; Windows requires qualified ACL provisioning')
def test_native_https_agent_reads_private_file_and_preserves_authenticated_restart(tmp_path):
    """Real TLS socket with locally generated fixture trust; no external service."""
    certificate, key, secret = (tmp_path / name for name in ('server.pem', 'server.key', 'server-token'))
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
        '-keyout', str(key), '-out', str(certificate)], capture_output=True, check=True, timeout=15)
    key.chmod(0o600); secret.write_text('controlled-server-token-12345678\n'); secret.chmod(0o600)
    env = dict(os.environ); env.pop('VISION_FIELD_AGENT_TOKEN', None)
    state = tmp_path / 'field-state'; before = secret.read_bytes()
    configuration_hash = None
    for attempt in range(2):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        command = [sys.executable, '-m', 'backend.engine.fleet_agent', '--state-dir', str(state),
            '--host', '127.0.0.1', '--port', str(port), '--token-file', str(secret),
            '--tls-cert', str(certificate), '--tls-key', str(key)]
        with (tmp_path / f'https-{attempt}.log').open('wb') as log:
            process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                import ssl
                context = ssl.create_default_context(cafile=str(certificate))
                with httpx.Client(base_url=f'https://127.0.0.1:{port}', verify=context, timeout=2, trust_env=False) as client:
                    deadline = time.monotonic() + 12
                    while True:
                        assert process.poll() is None, (tmp_path / f'https-{attempt}.log').read_text()
                        try: response = client.get('/agent/v1/runtime'); break
                        except httpx.TransportError:
                            if time.monotonic() >= deadline: raise
                            time.sleep(.1)
                    assert response.status_code == 401
                    assert client.get('/agent/v1/runtime', headers={'Authorization': 'Bearer wrong-token'}).status_code == 401
                    response = client.get('/agent/v1/runtime', headers={'Authorization': 'Bearer controlled-server-token-12345678'})
                    assert response.status_code == 200 and response.json() == {'status': 'stopped'}
                    with pytest.raises(httpx.TransportError):
                        client.get(f'http://127.0.0.1:{port}/agent/v1/runtime')
                current = (state / 'service.json').read_bytes()
                if configuration_hash is None: configuration_hash = current
                else: assert current == configuration_hash
                assert process.poll() is None and secret.read_bytes() == before
                assert 'controlled-server-token-12345678' not in (tmp_path / f'https-{attempt}.log').read_text()
            finally:
                process.terminate()
                try: process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5); pytest.fail('Owned HTTPS agent did not stop normally')
        import signal
        assert process.returncode in (0, -signal.SIGTERM)
        assert 'Application shutdown complete' in (tmp_path / f'https-{attempt}.log').read_text()

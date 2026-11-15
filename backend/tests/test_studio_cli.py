"""Real CLI processes against an owned HTTP peer; no app, models or account stores."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[2]


def operation(method, path, *, params=(), content=None, effect='execute'):
    return {'id': f'{method} {path}', 'method': method, 'path': path, 'group': 'fixture',
            'summary': '검증', 'transport': 'http', 'effect': effect,
            'assistant': {'mode': 'read' if effect == 'read' else 'manual'},
            'input': {'parameters': list(params), **({'request_body': content} if content else {})},
            'responses': {}}


OPS = [
    operation('GET', '/api/project/current', effect='read'),
    operation('POST', '/api/fixture/items/{item_id}', params=[
        {'name': 'item_id', 'in': 'path', 'required': True, 'schema': {'type': 'string'}},
        {'name': 'filter', 'in': 'query', 'schema': {'type': 'array', 'items': {'type': 'string'}}},
        {'name': 'enabled', 'in': 'query', 'schema': {'type': 'boolean'}}],
        content={'required': True, 'content': {'application/json': {'schema': {'type': 'object'}}}}),
    operation('PUT', '/api/fixture/raw', content={'required': True, 'content': {'application/octet-stream': {}}}),
    operation('POST', '/api/fixture/upload', content={'required': True, 'content': {'multipart/form-data': {}}}),
    operation('GET', '/api/fixture/download', effect='read'),
    operation('DELETE', '/api/fixture/remove'),
    operation('POST', '/api/fixture/lost'),
    operation('GET', '/api/fixture/redirect', effect='read'),
    operation('GET', '/api/fixture/error', effect='read'),
    operation('GET', '/api/fixture/invalid', effect='read'),
    operation('POST', '/api/fixture/jobs'),
    operation('GET', '/api/training/jobs', effect='read'),
    operation('POST', '/api/fixture/short-response'),
]


@contextmanager
def peer():
    state = {'requests': [], 'bad_digest': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def handle_request(self):
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length)
            state['requests'].append({'method': self.command, 'path': self.path,
                                      'headers': dict(self.headers), 'body': body})
            route = self.path.split('?')[0]
            headers = {'Content-Type': 'application/json',
                       'X-Vision-Context': '{"project_id":"p1","workspace_id":"w1","actor_id":"a1","mode":"local"}'}
            status = 200
            if route == '/api/automation/catalog':
                from urllib.parse import parse_qs, urlsplit
                args = parse_qs(urlsplit(self.path).query)
                rows = OPS if 'operation' not in args else [x for x in OPS if x['id'] == args['operation'][0]]
                result = {'schema': 'StudioOperations/v1', 'operations': rows, 'components': {'schemas': {}},
                          'groups': [{'name': 'fixture', 'count': len(OPS)}], 'total': len(rows), 'next_offset': None,
                          'server_version': 'fixture'}
            elif route == '/api/accounts/login':
                assert json.loads(body)['transport'] == 'bearer'
                result = {'token': 'synthetic-login-private', 'expires_at': 1900000000,
                          'user': {'username': json.loads(body)['username']}}
            elif route == '/api/training/jobs':
                status = 403
                result = {'detail': {'message': self.headers.get('X-Vision-Token'), 'password': 'server-reflected-password'}}
            elif route == '/api/fixture/download':
                raw = 'zip合成\x00'.encode()
                headers.update({'Content-Type': 'application/zip',
                    'X-Content-SHA256': '0' * 64 if state['bad_digest'] else hashlib.sha256(raw).hexdigest()})
                result = None
            elif route == '/api/fixture/redirect':
                status = 307
                headers['Location'] = '/api/fixture/redirect-target'
                result = {'detail': 'redirect'}
            elif route == '/api/fixture/error':
                status = 422
                result = {'detail': [{'loc': ['body', 'field'], 'msg': '필수 값', 'input': {'password': 'private'}}],
                          'authorization': self.headers.get('X-Vision-Token')}
            elif route == '/api/fixture/invalid':
                raw = b'{"cut":'
                result = None
            elif route == '/api/fixture/short-response':
                result = {'status': 'queued', 'job_id': 'job-short'}
            elif route == '/api/fixture/lost':
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            elif route == '/api/fixture/remove':
                status, result = 204, None
            elif route == '/api/fixture/jobs':
                status, result = 202, {'job_id': 'job-fixture', 'status': 'queued'}
            else:
                result = {'name': '한글 프로젝트', 'request': json.loads(body) if body.startswith(b'{') else None}
            if result is not None: raw = json.dumps(result, ensure_ascii=False).encode()
            elif status == 204: raw = b''
            self.send_response(status)
            for key, value in headers.items(): self.send_header(key, value)
            self.send_header('Content-Length', str(len(raw) + (100 if route == '/api/fixture/short-response' else 0)))
            self.end_headers()
            self.wfile.write(raw)

        do_GET = do_POST = do_PUT = do_DELETE = handle_request

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try: yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def run(url, *args, env=None, stdin=None):
    environment = {**os.environ, 'VISION_AI_STUDIO_API_TOKEN': 'synthetic-private-token'}
    environment.pop('MODU_STUDIO_BEARER_TOKEN', None)
    environment.update(env or {})
    result = subprocess.run([sys.executable, '-m', 'backend.studio_cli', '--url', url, *args],
        cwd=ROOT, env=environment, input=stdin, capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert result.stdout.count('\n') == 1, (result.stdout, result.stderr)
    data = json.loads(result.stdout)
    assert data['schema'] == 'StudioCLIResult/v1'
    assert 'synthetic-private-token' not in result.stdout + result.stderr
    return result, data


def test_cli_import_and_help_need_no_torch_or_backend_engine():
    result = subprocess.run([sys.executable, '-c', "import sys; import backend.studio_cli; assert 'torch' not in sys.modules; assert 'backend.main' not in sys.modules"],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    help_result = subprocess.run([sys.executable, '-m', 'backend.studio_cli', '--help'],
        cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0
    assert 'capabilities' in help_result.stdout and 'diagnostics' in help_result.stdout


def test_discover_and_describe_live_operations():
    with peer() as (url, state):
        result, data = run(url, 'capabilities')
        assert result.returncode == 0
        assert len(data['result']['operations']) == len(OPS)
        _, detail = run(url, 'describe', OPS[1]['id'])
        assert detail['result']['operations'][0]['input']['request_body']['required']
        assert state['requests'][0]['headers']['X-Vision-Token'] == 'synthetic-private-token'


def test_call_uses_catalog_parameters_auth_scope_and_unicode():
    with peer() as (url, state):
        result, data = run(url, '--project', 'p1', '--context-json', '{"project_id":"p1","workspace_id":"w1","actor_id":"a1","mode":"local"}',
            'call', OPS[1]['id'], '--path-json', '{"item_id":"한글 공백"}',
            '--query-json', '{"filter":["a","b"],"enabled":true}', '--body-json', '{"name":"원본 유지"}')
        assert result.returncode == 0
        request = state['requests'][-1]
        assert request['method'] == 'POST'
        assert request['path'] == '/api/fixture/items/%ED%95%9C%EA%B8%80%20%EA%B3%B5%EB%B0%B1?filter=a&filter=b&enabled=true'
        assert request['headers']['X-Vision-Project'] == 'p1'
        assert json.loads(request['headers']['X-Vision-Context'])['project_id'] == 'p1'
        assert data['result']['request']['name'] == '원본 유지'
        assert data['outcome'] == 'responded'
        assert data['context']['project_id'] == 'p1'


@pytest.mark.parametrize('args', [
    ['--path-json', '{"item_id":".."}', '--body-json', '{}'],
    ['--path-json', '{"item_id":"%2e%2e"}', '--body-json', '{}'],
    ['--path-json', '{"item_id":"a/b"}', '--body-json', '{}'],
    ['--path-json', '{"item_id":"ok","other":"bad"}', '--body-json', '{}'],
    ['--path-json', '{"item_id":"ok"}', '--query-json', '{"unknown":1}', '--body-json', '{}'],
    ['--path-json', '{"item_id":"ok"}', '--body-json', '{"n":NaN}'],
    ['--path-json', '{"item_id":"ok"}', '--body-json', '{"n":1,"n":2}'],
    ['--path-json', '{"item_id":"ok"}'],
])
def test_bad_inputs_never_dispatch_mutation(args):
    with peer() as (url, state):
        result, data = run(url, 'call', OPS[1]['id'], *args)
        assert result.returncode != 0 and not data['ok']
        assert not any(x['method'] == 'POST' for x in state['requests'])


def test_raw_upload_streams_exact_bytes(tmp_path):
    source = tmp_path / '입력 파일.bin'; source.write_bytes(b'raw\0\xffcontent')
    with peer() as (url, state):
        result, data = run(url, 'call', 'PUT /api/fixture/raw', '--upload-file', str(source))
        assert result.returncode == 0, data
        assert state['requests'][-1]['body'] == source.read_bytes()
        assert state['requests'][-1]['headers']['Content-Type'] == 'application/octet-stream'


def test_multipart_upload_preserves_bytes_and_form(tmp_path):
    source = tmp_path / '검증 파일.png'; source.write_bytes(b'png\x00\xffbytes')
    with peer() as (url, state):
        result, data = run(url, 'call', 'POST /api/fixture/upload', '--file', f'file={source}', '--form-json', '{"task":"segmentation"}')
        assert result.returncode == 0, data
        request = state['requests'][-1]
        assert request['headers']['Content-Type'].startswith('multipart/form-data; boundary=')
        assert source.read_bytes() in request['body']
        assert b'segmentation' in request['body'] and b'name="task"' in request['body']


def test_verified_download_and_existing_output_preserved(tmp_path):
    output = tmp_path / '결과.zip'
    with peer() as (url, state):
        result, data = run(url, 'call', 'GET /api/fixture/download', '--output', str(output))
        assert result.returncode == 0, data
        assert output.read_bytes() == 'zip合成\x00'.encode()
        assert data['result']['sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()
        before = len(state['requests'])
        result, data = run(url, 'call', 'GET /api/fixture/download', '--output', str(output))
        assert result.returncode != 0 and not data['ok']
        assert len(state['requests']) == before  # refuse before even fetching metadata
        assert output.read_bytes() == 'zip合成\x00'.encode()


def test_bad_checksum_never_publishes_output(tmp_path):
    output = tmp_path / 'bad.zip'
    with peer() as (url, state):
        state['bad_digest'] = True
        result, data = run(url, 'call', 'GET /api/fixture/download', '--output', str(output))
        assert result.returncode != 0 and data['error']['code'] == 'checksum_mismatch'
        assert not output.exists() and list(tmp_path.iterdir()) == []


def test_binary_requires_explicit_output():
    with peer() as (url, _):
        result, data = run(url, 'call', 'GET /api/fixture/download')
        assert result.returncode != 0 and data['error']['code'] == 'output_required'


def test_redirect_never_forwards_credentials_or_replays_request():
    with peer() as (url, state):
        result, data = run(url, 'call', 'GET /api/fixture/redirect')
        assert result.returncode != 0 and data['error']['code'] == 'redirect_refused'
        assert len(state['requests']) == 2
        assert not any('redirect-target' in x['path'] for x in state['requests'])


def test_lost_mutation_response_is_unconfirmed_and_never_retried():
    with peer() as (url, state):
        result, data = run(url, 'call', 'POST /api/fixture/lost')
        assert result.returncode != 0 and data['outcome'] == 'unconfirmed'
        assert data['error']['retryable'] is False
        assert len([x for x in state['requests'] if x['path'] == '/api/fixture/lost']) == 1


def test_empty_success_and_async_acceptance_are_not_misparsed_as_completed():
    with peer() as (url, _):
        result, data = run(url, 'call', 'DELETE /api/fixture/remove')
        assert result.returncode == 0 and data['result'] is None
        result, data = run(url, 'call', 'POST /api/fixture/jobs')
        assert result.returncode == 0 and data['http_status'] == 202
        assert data['result']['status'] == 'queued' and data['outcome'] == 'responded'


def test_http_detail_and_secret_redaction_are_preserved():
    with peer() as (url, _):
        result, data = run(url, 'call', 'GET /api/fixture/error')
        assert result.returncode != 0 and data['http_status'] == 422
        assert data['error']['detail'][0]['msg'] == '필수 값'
        assert data['error']['detail'][0]['input']['password'] == '[redacted]'


def test_invalid_response_is_structured_not_python_traceback():
    with peer() as (url, _):
        result, data = run(url, 'call', 'GET /api/fixture/invalid')
        assert result.returncode != 0 and data['error']['code'] == 'invalid_json_response'
        assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('url', ['http://example.com', 'https://user:pass@example.com', 'https://example.com/?token=secret', 'file:///tmp/socket'])
def test_untrusted_urls_refused_before_contact(url):
    result, data = run(url, 'capabilities')
    assert result.returncode != 0 and data['outcome'] == 'not_started'


def test_bearer_and_desktop_credentials_are_not_ambiguously_combined():
    with peer() as (url, state):
        result, data = run(url, 'capabilities', env={'MODU_STUDIO_BEARER_TOKEN': 'synthetic-bearer'})
        assert result.returncode != 0 and state['requests'] == []
        result, data = run(url, '--auth', 'bearer', 'capabilities', env={'MODU_STUDIO_BEARER_TOKEN': 'synthetic-bearer'})
        assert result.returncode == 0
        headers = state['requests'][0]['headers']
        assert headers['Authorization'] == 'Bearer synthetic-bearer' and 'X-Vision-Token' not in headers


def test_argument_errors_are_json():
    result, data = run('http://127.0.0.1:1', 'call')
    assert result.returncode == 2 and data['error']['code'] == 'invalid_arguments'
    result, _ = run('http://127.0.0.1:1', 'capabilities', '--password', 'mistyped-private-password')
    assert 'mistyped-private-password' not in result.stdout + result.stderr


def test_diagnostics_requires_explicit_project_before_any_request():
    with peer() as (url, state):
        result, data = run(url, 'diagnostics')
        assert result.returncode != 0 and data['error']['code'] == 'project_required'
        assert state['requests'] == []


def test_diagnostic_partial_failure_never_prints_reflected_credentials():
    with peer() as (url, _):
        result, data = run(url, '--project', 'p1', 'diagnostics', '--section', 'jobs')
        assert result.returncode == 0 and data['result']['partial']
        assert 'server-reflected-password' not in result.stdout
        detail = data['result']['observations'][0]['error']['detail']
        assert detail['password'] == '[redacted]' and detail['message'] == '[redacted]'


def test_validation_loc_credentials_and_sensitive_body_echo_are_redacted():
    from backend.automation.client import redacted, make_body
    error = [{'loc': ['body', 'password'], 'input': 'shortsecret', 'msg': 'too short'}]
    assert redacted(error)[0]['input'] == '[redacted]'
    body = make_body(OPS[1], body_json='{"password":"shortsecret","keyed":{"api_key":"nestedsecret"}}')
    assert 'shortsecret' in body.secrets and 'nestedsecret' in body.secrets


def test_short_invalid_password_does_not_corrupt_machine_protocol_metadata():
    with peer() as (url, _):
        result, data = run(url, 'call', OPS[1]['id'], '--path-json', '{"item_id":"ok"}', '--body-json', '{"password":"e"}')
        assert result.returncode == 0 and data['outcome'] == 'responded'
        assert data['result']['request']['password'] == '[redacted]'


def test_preflight_error_does_not_echo_credential_mistaken_for_operation():
    result, data = run('http://example.com', 'call', 'synthetic-private-token')
    assert result.returncode != 0 and data['error']['code'] == 'invalid_url'


def test_explicit_login_stores_private_origin_bound_session_without_printing_secrets(tmp_path):
    session = tmp_path / 'test-session.json'
    with peer() as (url, state):
        result, data = run(url, '--session-file', str(session), 'login', '--username', 'fixture-user',
                           '--password-stdin', stdin='synthetic-password\n')
        assert result.returncode == 0, data
        assert data['result']['logged_in'] is True
        assert 'synthetic-password' not in result.stdout + result.stderr
        assert 'synthetic-login-private' not in result.stdout + result.stderr
        assert session.stat().st_mode & 0o777 == 0o600
        assert json.loads(session.read_text())['url'] == url
        result, data = run(url, '--session-file', str(session), 'call', 'GET /api/project/current',
                           env={'VISION_AI_STUDIO_API_TOKEN': ''})
        assert result.returncode == 0, data
        assert state['requests'][-1]['headers']['Authorization'] == 'Bearer synthetic-login-private'


def test_session_cannot_be_overwritten_or_sent_to_different_origin(tmp_path):
    session = tmp_path / 'test-session.json'
    with peer() as (url, state):
        run(url, '--session-file', str(session), 'login', '--username', 'fixture', '--password-stdin', stdin='synthetic-password\n')
        previous = session.read_bytes()
        before = len(state['requests'])
        result, data = run(url, '--session-file', str(session), 'login', '--username', 'fixture', '--password-stdin', stdin='unused\n')
        assert result.returncode != 0 and len(state['requests']) == before
        assert session.read_bytes() == previous
        result, data = run('http://127.0.0.1:1', '--session-file', str(session), 'capabilities', env={'VISION_AI_STUDIO_API_TOKEN': ''})
        assert result.returncode != 0 and data['error']['code'] == 'session_origin_mismatch'


def test_session_symlink_and_insecure_mode_refused(tmp_path):
    session = tmp_path / 'session.json'
    session.write_text('{"schema":"StudioCLISession/v1","url":"http://127.0.0.1:1","token":"synthetic"}')
    session.chmod(0o644)
    result, data = run('http://127.0.0.1:1', '--session-file', str(session), 'capabilities', env={'VISION_AI_STUDIO_API_TOKEN': ''})
    assert result.returncode != 0 and data['error']['code'] == 'unsafe_session_file'
    session.chmod(0o600)
    link = tmp_path / 'link.json'; link.symlink_to(session)
    result, data = run('http://127.0.0.1:1', '--session-file', str(link), 'capabilities', env={'VISION_AI_STUDIO_API_TOKEN': ''})
    assert result.returncode != 0 and data['error']['code'] == 'unsafe_session_file'


def test_schema_secret_field_names_remain_describable_but_values_stay_private():
    from backend.automation.client import redacted
    value = {'schema': 'StudioOperations/v1', 'components': {'schemas': {'Login': {
        'properties': {'password': {'type': 'string'}, 'token': {'type': 'string'}}}}}}
    assert redacted(value, metadata=True)['components']['schemas']['Login']['properties']['password']['type'] == 'string'
    assert redacted({'password': 'private'})['password'] == '[redacted]'


def test_deep_or_invalid_unicode_json_refused_without_traceback():
    with peer() as (url, state):
        result, data = run(url, 'call', OPS[1]['id'], '--path-json', '{"item_id":"ok"}', '--body-json', '"\\ud800"')
        assert result.returncode != 0 and 'Traceback' not in result.stderr
        assert not any(x['method'] == 'POST' for x in state['requests'])


def test_slashes_only_allowed_for_registered_path_converter():
    from backend.automation.client import operation_path
    op = operation('GET', '/api/dataset/raw/{image_path}')
    op['input']['path_converters'] = {'image_path': 'path'}
    assert operation_path(op, {'image_path': 'OK/한글.png'}, {}) == '/api/dataset/raw/OK/%ED%95%9C%EA%B8%80.png'
    for bad in ['../secret', '/absolute', 'OK//a', 'OK/%2e%2e/a', 'OK/../a', 'OK/./a', 'OK/\\a']:
        with pytest.raises(ValueError): operation_path(op, {'image_path': bad}, {})


def test_complete_json_shorter_than_http_length_does_not_claim_success():
    with peer() as (url, _):
        result, data = run(url, 'call', 'POST /api/fixture/short-response')
        assert result.returncode != 0 and data['outcome'] == 'unconfirmed'
        assert data['error']['code'] == 'incomplete_response'


def test_impossible_session_destination_is_json_without_login(tmp_path):
    with peer() as (url, state):
        result, data = run(url, '--session-file', str(tmp_path / ('x' * 300)), 'login',
            '--username', 'fixture', '--password-stdin', stdin='not-sent\n')
        assert result.returncode != 0 and data['outcome'] == 'not_started'
        assert 'Traceback' not in result.stderr and state['requests'] == []


def test_session_disk_failure_reports_login_succeeded_without_traceback(tmp_path, monkeypatch):
    import errno
    from backend.automation import session
    from backend.automation.client import CommandError
    class Client:
        secrets = ()
        url = 'http://127.0.0.1:1'
        def request(self, *args, **kwargs): return {'token': 'synthetic-session-token', 'expires_at': 1900000000}
    monkeypatch.setattr(session, 'password_input', lambda *args: 'synthetic-password')
    def disk_full(*args): raise OSError(errno.ENOSPC, 'synthetic disk failure')
    monkeypatch.setattr(session.os, 'link', disk_full)
    with pytest.raises(CommandError) as result:
        session.login(Client(), username='fixture', filename=str(tmp_path/'session.json'))
    assert result.value.code == 'session_persistence_failed'
    assert result.value.outcome == 'responded'
    assert result.value.detail['account_login_succeeded'] is True
    assert result.value.detail['session_saved'] is False
    assert list(tmp_path.iterdir()) == []

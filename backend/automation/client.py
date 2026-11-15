"""Dependency-free authenticated product transport. Never follows redirects or retries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import http.client
import ipaddress
import json
import math
import mimetypes
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener, HTTPSHandler
import uuid

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_INPUT_BYTES = 8 * 1024 * 1024
CHUNK = 64 * 1024
METHODS = frozenset(('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'))
SECRET_KEY = re.compile(r'password|secret|token|authorization|cookie|csrf|credential|api.?key', re.I)


class CommandError(ValueError):
    def __init__(self, code, message, *, exit_code=2, outcome='not_started', status=None, detail=None):
        super().__init__(message)
        self.code, self.exit_code, self.outcome = code, exit_code, outcome
        self.status, self.detail = status, detail


def strict_json(value, *, limit=MAX_INPUT_BYTES):
    def object_pairs(pairs):
        result = {}
        for key, item in pairs:
            if key in result: raise ValueError('Duplicate JSON object keys are not allowed')
            result[key] = item
        return result

    def finite_float(text):
        number = float(text)
        if not math.isfinite(number): raise ValueError('JSON numbers must be finite')
        return number
    try:
        if len(value.encode('utf-8')) > limit: raise ValueError('JSON exceeds the documented byte limit')
        result = json.loads(value, object_pairs_hook=object_pairs, parse_float=finite_float,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError('JSON numbers must be finite')))
        pending = [(result, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 128: raise ValueError('JSON exceeds 128 nesting levels')
            if isinstance(node, str): node.encode('utf-8')
            elif isinstance(node, dict):
                for key, item in node.items():
                    key.encode('utf-8'); pending.append((item, depth + 1))
            elif isinstance(node, list): pending.extend((item, depth + 1) for item in node)
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CommandError('invalid_json', str(exc)[:180]) from None


def json_object(value, label):
    result = strict_json(value)
    if not isinstance(result, dict): raise CommandError('invalid_arguments', f'{label} must be a JSON object')
    return result


def redacted(value, secrets=(), *, metadata=False):
    if isinstance(value, dict):
        credential_input = not metadata and isinstance(value.get('loc'), list) and any(
            isinstance(part, str) and SECRET_KEY.search(part) for part in value['loc'])
        return {key: '[redacted]' if not metadata and (SECRET_KEY.search(str(key)) or key == 'input' and credential_input)
                else redacted(item, secrets, metadata=metadata) for key, item in value.items()}
    if isinstance(value, list): return [redacted(item, secrets, metadata=metadata) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret: value = value.replace(secret, '[redacted]')
    return value


def received_at():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def result_envelope(operation, *, result=None, status=None, outcome='observed', error=None, context=None):
    return {'schema': 'StudioCLIResult/v1', 'ok': error is None, 'operation': operation,
            'http_status': status, 'outcome': outcome, 'result': result,
            'context': context, 'observed_at': received_at(), **({'error': error} if error else {})}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def checked_url(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
                or parts.password is not None or parts.path not in ('', '/') or parts.query or parts.fragment
                or parts.netloc.endswith(':') or any(ord(c) < 33 for c in value)):
            raise ValueError
        _ = parts.port
        if parts.scheme == 'http':
            try: loopback = ipaddress.ip_address(parts.hostname).is_loopback
            except ValueError: loopback = parts.hostname.lower() == 'localhost'
            if not loopback: raise ValueError
    except (ValueError, TypeError):
        raise CommandError('invalid_url', 'Use an explicit root HTTPS URL, or HTTP on loopback; userinfo/query/fragment are refused') from None
    return value.rstrip('/')


def clean_header(value, label):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise CommandError('invalid_arguments', f'{label} must be nonempty ASCII without control characters')
    return value


def checked_output(value):
    if value is None: return None
    try:
        path = Path(value).expanduser().absolute()
        if os.path.lexists(path): raise CommandError('output_exists', 'Output already exists; choose a new file')
        if not path.parent.is_dir(): raise CommandError('invalid_output', 'Output parent directory must already exist')
        if os.name == 'posix':
            maximum = os.pathconf(path.parent, 'PC_NAME_MAX')
            if maximum > 0 and len(os.fsencode(path.name)) > maximum:
                raise CommandError('invalid_output', 'Output filename exceeds this filesystem limit')
    except CommandError: raise
    except (OSError, RuntimeError, ValueError):
        raise CommandError('invalid_output', 'Output path cannot be used; choose an existing writable parent and a valid new filename') from None
    return path


@dataclass
class RequestBody:
    content_type: str
    length: int
    chunks: object
    secrets: tuple[str, ...] = ()


def bytes_body(content, media, secrets=()):
    return RequestBody(media, len(content), [content], tuple(secrets))


def body_secrets(value, *, sensitive=False):
    if isinstance(value, str): return [value] if sensitive and value else []
    if isinstance(value, dict):
        return [secret for key, item in value.items() for secret in body_secrets(item, sensitive=sensitive or bool(SECRET_KEY.search(str(key))))]
    if isinstance(value, list): return [secret for item in value for secret in body_secrets(item, sensitive=sensitive)]
    return []


def _file_chunks(path, expected_size):
    read = 0
    with path.open('rb') as handle:
        while chunk := handle.read(CHUNK):
            read += len(chunk)
            if read > expected_size: raise CommandError('input_changed', 'Upload size changed during transmission', outcome='unconfirmed', exit_code=5)
            yield chunk
    if read != expected_size:
        raise CommandError('input_changed', 'Upload size changed during transmission', outcome='unconfirmed', exit_code=5)


def _upload_path(value):
    try:
        path = Path(value).expanduser()
        if not path.is_file(): raise OSError
        size = path.stat().st_size
    except (OSError, RuntimeError, ValueError):
        raise CommandError('invalid_upload', 'An explicit readable upload file is required') from None
    return path, size


def make_body(op, *, body_json=None, body_file=None, upload_file=None, files=(), form_json=None):
    content = op.get('input', {}).get('request_body', {})
    media = content.get('content', {})
    supplied = sum((body_json is not None or body_file is not None, upload_file is not None, bool(files) or form_json is not None))
    if supplied > 1 or body_json is not None and body_file is not None:
        raise CommandError('invalid_arguments', 'Choose exactly one JSON, raw-file or multipart body encoding')
    if not supplied:
        if content.get('required'): raise CommandError('missing_body', 'This operation requires an explicit request body; see describe')
        return None
    if not media: raise CommandError('unexpected_body', 'This operation does not declare a request body')
    if body_json is not None or body_file is not None:
        if 'application/json' not in media:
            raise CommandError('unsupported_encoding', 'This operation does not accept application/json')
        if body_file is not None:
            path, size = _upload_path(body_file)
            if size > MAX_INPUT_BYTES: raise CommandError('input_too_large', 'JSON input exceeds eight MiB')
            try: body_json = path.read_text(encoding='utf-8')
            except (OSError, UnicodeError): raise CommandError('invalid_json', 'Body file must contain readable UTF-8 JSON') from None
        value = strict_json(body_json)
        return bytes_body(json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8'), 'application/json', body_secrets(value))
    if upload_file is not None:
        if 'application/octet-stream' not in media:
            raise CommandError('unsupported_encoding', 'This operation does not accept raw application/octet-stream')
        path, size = _upload_path(upload_file)
        return RequestBody('application/octet-stream', size, _file_chunks(path, size))
    if 'multipart/form-data' not in media:
        raise CommandError('unsupported_encoding', 'This operation does not accept multipart/form-data')
    fields = json_object(form_json, '--form-json') if form_json is not None else {}
    boundary = 'modu-' + uuid.uuid4().hex
    parts = []

    def name_check(name):
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,127}', name):
            raise CommandError('invalid_arguments', 'Multipart field names must be ASCII identifiers')

    for name, value in fields.items():
        name_check(name)
        if not isinstance(value, (str, int, float, bool)):
            raise CommandError('invalid_arguments', 'Multipart form values must be scalar JSON values')
        spelling = value if isinstance(value, str) else json.dumps(value, allow_nan=False)
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + spelling.encode('utf-8') + b'\r\n')
    for entry in files:
        if '=' not in entry: raise CommandError('invalid_arguments', 'Use --file FIELD=PATH')
        name, value = entry.split('=', 1); name_check(name)
        if name in fields: raise CommandError('invalid_arguments', 'A multipart file field cannot also be a form field')
        path, size = _upload_path(value)
        filename = path.name
        if any(ord(c) < 32 for c in filename): raise CommandError('invalid_upload', 'Upload filename contains control characters')
        fallback = re.sub(r'[^A-Za-z0-9_.-]', '_', filename) or 'upload'
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}\r\nContent-Type: {mime}\r\n\r\n'.encode(),
                      (path, size), b'\r\n'])
    parts.append(f'--{boundary}--\r\n'.encode())
    length = sum(part[1] if isinstance(part, tuple) else len(part) for part in parts)

    def chunks():
        for part in parts:
            if isinstance(part, tuple): yield from _file_chunks(*part)
            else: yield part
    return RequestBody(f'multipart/form-data; boundary={boundary}', length, chunks(), tuple(body_secrets(fields)))


def operation_path(op, path_args, query_args):
    path, method = op.get('path'), op.get('method')
    if (method not in METHODS or not isinstance(path, str) or not (path.startswith('/api/') or path == '/health')
            or op.get('id') != f'{method} {path}' or any(c in path for c in ('?', '#', '\\', '%', '\r', '\n'))
            or '//' in path or any(part in ('.', '..') for part in path.split('/'))):
        raise CommandError('invalid_catalog', 'Server operation identity is invalid')
    parameters = op.get('input', {}).get('parameters', [])
    path_names = set(re.findall(r'\{([^{}]+)\}', path))
    if set(path_args) != path_names:
        raise CommandError('invalid_arguments', 'Provide exactly the path parameters shown by describe')
    for name in path_names:
        value = path_args[name]
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise CommandError('invalid_arguments', 'Path values must be strings or integers')
        spelling = str(value)
        converter = op.get('input', {}).get('path_converters', {}).get(name)
        segments = spelling.split('/') if converter == 'path' else [spelling]
        if (any(not segment or segment in ('.', '..') for segment in segments)
                or any(ord(c) < 32 for c in spelling) or any(c in spelling for c in ('\\', '%'))
                or converter != 'path' and '/' in spelling):
            raise CommandError('invalid_arguments', 'Path values must be unencoded, without traversal or unregistered separators')
        path = path.replace('{' + name + '}', '/'.join(quote(segment, safe='') for segment in segments))
    allowed = {item['name']: item for item in parameters if item.get('in') == 'query'}
    if query_args.keys() - allowed.keys(): raise CommandError('invalid_arguments', 'Unknown query parameter; use describe')
    if any(item.get('required') and name not in query_args for name, item in allowed.items()):
        raise CommandError('invalid_arguments', 'A required query parameter is missing')
    pairs = []
    for name, value in query_args.items():
        for item in value if isinstance(value, list) else [value]:
            if item is None or not isinstance(item, (str, int, float, bool)):
                raise CommandError('invalid_arguments', 'Query values must be scalars or arrays of scalars')
            pairs.append((name, json.dumps(item, allow_nan=False) if not isinstance(item, str) else item))
    return path + ('?' + urlencode(pairs) if pairs else '')


class StudioClient:
    def __init__(self, url, *, token=None, bearer=None, project=None, context=None, ca_file=None, timeout=60, authenticated=True):
        self.url = checked_url(url)
        if not math.isfinite(timeout) or not 0 < timeout <= 86400: raise CommandError('invalid_arguments', 'Timeout must be positive and at most 86400 seconds')
        self.timeout = timeout
        if (authenticated and bool(token) == bool(bearer)) or (not authenticated and (token or bearer)):
            raise CommandError('authentication_required', 'Select one explicit desktop or account credential transport')
        self.authenticated = authenticated
        self.secrets = tuple(value for value in (token, bearer) if value)
        self.headers = {'Accept': 'application/json', 'User-Agent': 'Modu-Vision-Studio-CLI/1'}
        if token: self.headers['X-Vision-Token'] = clean_header(token, 'Desktop capability')
        if bearer: self.headers['Authorization'] = 'Bearer ' + clean_header(bearer, 'Account bearer')
        if project: self.headers['X-Vision-Project'] = clean_header(project, 'Project identifier')
        if context is not None:
            if (not isinstance(context, dict) or set(context) != {'workspace_id', 'project_id', 'actor_id', 'mode'}
                    or context.get('mode') not in ('local', 'team') or any(not isinstance(value, str) or not value or len(value) > 4096 for value in context.values())
                    or project and project != context['project_id']):
                raise CommandError('invalid_arguments', 'Context must contain matching workspace_id,project_id,actor_id,mode')
            self.headers['X-Vision-Context'] = json.dumps(context, ensure_ascii=True, separators=(',', ':'))
        try: tls = ssl.create_default_context(cafile=ca_file)
        except (OSError, ssl.SSLError): raise CommandError('invalid_ca', 'Cannot load the explicit CA certificate') from None
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=tls))

    def _response_context(self, response):
        value = response.headers.get('X-Vision-Context')
        if not value: return None
        try: return strict_json(value)
        except CommandError: return {'available': False, 'reason': 'invalid_context_header'}

    def request(self, method, path, *, body=None, output=None, effect='read', operation=None, _login_result=False):
        login = method == 'POST' and path == '/api/accounts/login'
        if not self.authenticated and not login or _login_result and not login:
            raise CommandError('authentication_required', 'Unauthenticated transport is restricted to explicit account login')
        operation = operation or f'{method} {path.split("?")[0]}'
        headers = dict(self.headers)
        if body:
            self.secrets = tuple(sorted(set((*self.secrets, *body.secrets)), key=len, reverse=True))
            headers.update({'Content-Type': body.content_type, 'Content-Length': str(body.length)})
        request = Request(self.url + path, data=body.chunks if body else None, headers=headers, method=method)
        try:
            try: response = self.opener.open(request, timeout=self.timeout)
            except HTTPError as response:
                with response:
                    if 300 <= response.code < 400:
                        raise CommandError('redirect_refused', 'Server redirect refused; no credentials or request forwarded', exit_code=4, outcome='error', status=response.code)
                    data = response.read(MAX_JSON_BYTES + 1)
                    detail = None
                    if len(data) <= MAX_JSON_BYTES:
                        try:
                            parsed = strict_json(data.decode('utf-8'), limit=MAX_JSON_BYTES)
                            detail = parsed.get('detail', parsed) if isinstance(parsed, dict) else parsed
                        except (CommandError, UnicodeError): detail = 'Server returned a non-JSON error body'
                    else: detail = 'Server error body exceeded the response limit'
                    codes = {401: 'unauthorized', 403: 'forbidden', 404: 'not_found', 409: 'conflict', 422: 'validation_error', 429: 'capacity_limited'}
                    raise CommandError(codes.get(response.code, 'http_error'), f'Server returned HTTP {response.code}', exit_code=4,
                                       outcome='error', status=response.code, detail=detail)
            with response:
                status = response.status
                context = self._response_context(response)
                if output is not None:
                    value = self._download(response, output)
                elif status in (204, 205) or method == 'HEAD': value = None
                else:
                    media = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                    if media != 'application/json' and not media.endswith('+json'):
                        raise CommandError('output_required', 'Use --output for a non-JSON response', exit_code=4, outcome='responded', status=status)
                    raw = response.read(MAX_JSON_BYTES + 1)
                    if len(raw) > MAX_JSON_BYTES:
                        raise CommandError('response_too_large', 'Use --output for a response larger than sixteen MiB', exit_code=4, outcome='responded', status=status)
                    declared = response.headers.get('Content-Length')
                    if declared is not None and (not declared.isdigit() or len(raw) != int(declared)):
                        raise CommandError('incomplete_response', 'JSON response length does not match the HTTP frame; no request was retried', exit_code=4,
                            outcome='unconfirmed' if effect != 'read' else 'error', status=status)
                    try: value = strict_json(raw.decode('utf-8'), limit=MAX_JSON_BYTES)
                    except (CommandError, UnicodeError):
                        raise CommandError('invalid_json_response', 'The server response is incomplete or invalid JSON', exit_code=4,
                            outcome='unconfirmed' if effect != 'read' else 'error', status=status) from None
                if _login_result: return value  # only the explicit login handler may retain a new token in memory
                envelope = result_envelope(operation, status=status, context=context,
                    outcome='observed' if effect == 'read' else 'responded')
                envelope['context'] = redacted(context, self.secrets)
                envelope['result'] = redacted(value, self.secrets, metadata=path.split('?')[0] == '/api/automation/catalog')
                return envelope
        except CommandError: raise
        except (URLError, OSError, http.client.HTTPException, socket.timeout, ValueError) as exc:
            # Do not expose exception URLs, authorization headers or reflected payloads.
            raise CommandError('transport_error', f'Transport ended ({type(exc).__name__}); the request was not retried', exit_code=5,
                outcome='unconfirmed' if effect != 'read' else 'error') from None

    def _download(self, response, output):
        digest, count = hashlib.sha256(), 0
        descriptor, temporary = tempfile.mkstemp(prefix='.modu-download-', dir=output.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                while chunk := response.read(CHUNK):
                    handle.write(chunk); digest.update(chunk); count += len(chunk)
                handle.flush(); os.fsync(handle.fileno())
            expected = response.headers.get('X-Content-SHA256')
            if expected is not None and (not re.fullmatch(r'[a-fA-F0-9]{64}', expected) or digest.hexdigest() != expected.lower()):
                raise CommandError('checksum_mismatch', 'Downloaded bytes do not match the server SHA256', exit_code=4, outcome='error', status=response.status)
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdigit() or count != int(declared)):
                raise CommandError('incomplete_download', 'Download length does not match the server length', exit_code=4, outcome='error', status=response.status)
            try: os.link(temporary, output)  # atomic create, never replace another writer's output
            except FileExistsError: raise CommandError('output_exists', 'Output appeared during download; existing file preserved') from None
            return {'file': str(output), 'bytes': count, 'sha256': digest.hexdigest(), 'server_checksum_verified': expected is not None}
        finally:
            os.unlink(temporary)

    def catalog(self, **params):
        query = {key: json.dumps(value) if isinstance(value, bool) else value for key, value in params.items() if value is not None}
        result = self.request('GET', '/api/automation/catalog' + ('?' + urlencode(query) if query else ''))
        value = result['result']
        if not isinstance(value, dict) or value.get('schema') != 'StudioOperations/v1' or not isinstance(value.get('operations'), list):
            raise CommandError('invalid_catalog', 'Server does not provide the StudioOperations/v1 catalog', exit_code=4, outcome='error')
        return result

    def describe(self, operation):
        result = self.catalog(operation=operation, schemas=True)
        rows = result['result']['operations']
        if len(rows) != 1 or rows[0].get('id') != operation:
            raise CommandError('unknown_operation', 'Operation is absent from this installed server catalog')
        return result

    def call(self, operation, *, path_args=None, query_args=None, output=None, **encoding):
        detail = self.describe(operation)['result']['operations'][0]
        path = operation_path(detail, path_args or {}, query_args or {})
        if detail.get('transport') == 'native':
            raise CommandError('native_authority_required', 'This operation requires the existing HTTPS browser tab/native-host approval; bearer CLI cannot impersonate that tab')
        body = make_body(detail, **encoding)
        return self.request(detail['method'], path, operation=operation, body=body, output=output, effect=detail.get('effect', 'execute'))

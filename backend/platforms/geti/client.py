"""Internal, loopback-only transport. Never expose this client to a renderer."""
from __future__ import annotations

import http.client
import json as json_module
import secrets
from urllib.parse import urlsplit, unquote


class GetiError(RuntimeError):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


class GetiClient:
    def __init__(self, port: int, secret: str):
        if type(port) is not int or not 0 < port < 65536 or not secret:
            raise ValueError('Invalid owned runtime transport')
        self._port = port
        self._secret = secret

    def request(self, method: str, path: str, *, json=None, files=None, timeout=60):
        parts = urlsplit(path)
        if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'} or not path.startswith('/') or parts.netloc or parts.scheme or parts.fragment or '\\' in path or any(part == '..' for part in unquote(parts.path).split('/')):
            raise ValueError('Expected an explicit internal method and relative API path')
        headers = {'X-Modu-Geti-Secret': self._secret, 'Accept': 'application/json'}
        body = None
        if files is not None:
            if json is not None: raise ValueError('JSON and multipart body cannot be combined')
            boundary = secrets.token_hex(24)
            chunks = []
            for field, (filename, content, content_type) in files.items():
                if any(c in str(field) + filename + content_type for c in '\r\n"'):
                    raise ValueError('Invalid upload metadata')
                chunks.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode(), content, b'\r\n'])
            chunks.append(f'--{boundary}--\r\n'.encode())
            body = b''.join(chunks)
            headers['Content-Type'] = 'multipart/form-data; boundary=' + boundary
        elif json is not None:
            body = json_module.dumps(json, allow_nan=False).encode()
            headers['Content-Type'] = 'application/json'
        connection = http.client.HTTPConnection('127.0.0.1', self._port, timeout=timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            content = response.read()
            if not 200 <= response.status < 300:
                # Upstream errors contain local paths and occasionally model URLs. Keep them
                # in owned logs; browser messages carry only the status category.
                messages = {400: '플랫폼 요청을 확인하세요.', 403: '플랫폼 접근 권한이 없습니다.',
                            404: '플랫폼 자료를 찾을 수 없습니다.', 409: '플랫폼 작업 또는 데이터가 변경되었습니다.',
                            422: '플랫폼 입력 조건을 확인하세요.'}
                raise GetiError(response.status, messages.get(response.status, '플랫폼 요청을 완료하지 못했습니다. 실행 상태를 확인하세요.'))
            if 'json' in response.getheader('Content-Type', ''):
                return json_module.loads(content) if content else {}
            return content
        except (OSError, http.client.HTTPException, ValueError) as exc:
            if isinstance(exc, GetiError): raise
            raise GetiError(503, '플랫폼 연결이 끊겼습니다. 작업 상태를 새로 확인하세요.') from exc
        finally:
            connection.close()

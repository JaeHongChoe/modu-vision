"""Contained public assets and exact HTTPS authority around the existing API.

No engine import belongs here: the launcher pins storage before creating the API.
"""
import os
from pathlib import Path
import re
import stat
from urllib.parse import unquote_to_bytes, urlsplit

from backend.local_web_state import _refuse_links

_PUBLIC_TYPES = {
    '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.avif': 'image/avif',
    '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff': 'font/woff',
    '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf',
    '.wasm': 'application/wasm',
}


def _safe_path(scope):
    raw = scope.get('raw_path', scope['path'].encode('utf-8'))
    if re.search(br'%(?![0-9a-fA-F]{2})|%(?:2e|2f|5c|25|00)', raw, re.I):
        return False
    try:
        path = unquote_to_bytes(raw).decode('utf-8', errors='strict')
    except (ValueError, UnicodeDecodeError):
        return False
    return (path == scope['path'] and path.startswith('/') and not path.startswith('//')
            and '\\' not in path and not any(ord(char) < 32 or ord(char) == 127 for char in path)
            and all(part not in {'.', '..'} for part in path.split('/')))


async def _refuse(scope, send, status):
    if scope['type'] == 'websocket':
        await send({'type': 'websocket.close', 'code': 1008})
        return
    body = {400: b'Invalid path', 403: b'Forbidden origin or authority',
            404: b'Not found', 405: b'Method not allowed'}[status]
    headers = [(b'content-type', b'text/plain; charset=utf-8'), (b'x-content-type-options', b'nosniff')]
    if status == 405:
        headers.append((b'allow', b'GET, HEAD'))
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': b'' if scope.get('method') == 'HEAD' else body})


class LocalWebApp:
    """ASGI dispatcher; API scopes and lifespan are forwarded without mutation."""
    def __init__(self, api_app, renderer_dir: Path, origin: str):
        parsed = urlsplit(origin)
        port = parsed.port if parsed.port is not None else 443
        canonical = 'https://localhost' if port == 443 else f'https://localhost:{port}'
        if (parsed.scheme != 'https' or parsed.hostname != 'localhost' or parsed.username
                or parsed.password or parsed.path or parsed.query or parsed.fragment
                or not 1 <= port <= 65535 or origin != canonical):
            raise ValueError('Expected a canonical https://localhost origin (explicit port unless 443)')
        self.api_app = api_app
        self.renderer_dir = Path(renderer_dir).absolute()
        _refuse_links(self.renderer_dir)
        if not self.renderer_dir.is_dir():
            raise ValueError('Build the renderer before starting the local web app')
        self.origin = origin.encode('ascii')
        self.host = parsed.netloc.encode('ascii')

    def _open_public(self, path):
        parts = path.lstrip('/').split('/')
        if any(not part or part.startswith('.') for part in parts):
            raise FileNotFoundError(path)
        media_type = _PUBLIC_TYPES.get(Path(parts[-1]).suffix.lower())
        if media_type is None:
            raise FileNotFoundError(path)
        # Each component is opened relative to its already-open parent. A
        # replaced link cannot redirect the final read outside the build root.
        _refuse_links(self.renderer_dir)
        directory = os.open(self.renderer_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory)
                directory = child
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        finally:
            os.close(directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise FileNotFoundError(path)
            return os.fdopen(fd, 'rb'), media_type, info.st_size
        except BaseException:
            os.close(fd)
            raise

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'lifespan':
            await self.api_app(scope, receive, send)
            return
        if scope['type'] not in {'http', 'websocket'}:
            return
        hosts = [value for name, value in scope['headers'] if name.lower() == b'host']
        origins = [value for name, value in scope['headers'] if name.lower() == b'origin']
        scheme = 'wss' if scope['type'] == 'websocket' else 'https'
        if (hosts != [self.host] or scope.get('scheme') != scheme
                or (origins and origins != [self.origin])
                or (scope['type'] == 'websocket' and origins != [self.origin])):
            await _refuse(scope, send, 403)
            return
        if not _safe_path(scope):
            await _refuse(scope, send, 400)
            return
        path = scope['path']
        if path in {'/api', '/health', '/ws'} or path.startswith(('/api/', '/ws/', '/health/')):
            await self.api_app(scope, receive, send)
            return
        if scope['type'] == 'websocket':
            await _refuse(scope, send, 404)
            return
        if scope['method'] not in {'GET', 'HEAD'}:
            await _refuse(scope, send, 405)
            return
        if path == '/':
            path = '/index.html'
        try:
            try:
                stream, media_type, length = self._open_public(path)
            except FileNotFoundError:
                accept = next((value for key, value in scope['headers'] if key.lower() == b'accept'), b'')
                if '.' in path or 'text/html' not in accept.decode('latin1'):
                    raise
                stream, media_type, length = self._open_public('/index.html')
        except (OSError, ValueError):
            await _refuse(scope, send, 404)
            return
        with stream:
            await send({'type': 'http.response.start', 'status': 200, 'headers': [
                (b'content-type', media_type.encode('ascii')), (b'content-length', str(length).encode('ascii')),
                (b'x-content-type-options', b'nosniff'), (b'cache-control', b'no-cache'),
            ]})
            if scope['method'] == 'HEAD':
                await send({'type': 'http.response.body', 'body': b''})
            else:
                while chunk := stream.read(256 * 1024):
                    await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                await send({'type': 'http.response.body', 'body': b''})

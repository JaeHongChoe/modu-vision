"""Owned renderer build, verified health readback and direct Uvicorn TLS lifetime."""
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import ssl
import socket
import stat
import subprocess
import time
import webbrowser
from urllib.parse import urlsplit

from backend.local_web_state import _refuse_links

ROOT = Path(__file__).resolve().parents[1]


def _validate_build_target(output, root):
    _refuse_links(root)
    _refuse_links(output)
    if output.parent.resolve() != root.resolve() or output.name != 'renderer':
        raise ValueError('Renderer output must stay in its web data directory')
    if not output.exists():
        return
    if not output.is_dir():
        raise ValueError('Renderer output must be a directory')
    # Vite emptyOutDir can recursively delete. Refuse all links and special
    # entries before giving Vite permission to empty this owned output.
    for folder, directories, files in os.walk(output, followlinks=False):
        for name in (*directories, *files):
            entry = Path(folder) / name
            _refuse_links(entry)
            info = entry.lstat()
            if not (stat.S_ISDIR(info.st_mode) or (stat.S_ISREG(info.st_mode) and info.st_nlink == 1)):
                raise ValueError(f'Renderer entries must be unlinked regular files or directories: {entry}')


def build_renderer(paths):
    """Build current source under the caller's same-root instance_lock."""
    output = paths.root / 'renderer'
    _validate_build_target(output, paths.root)
    node = shutil.which('node')
    vite = ROOT / 'node_modules/vite/bin/vite.js'
    if node is None or not vite.is_file():
        raise RuntimeError('Renderer build requires existing Node.js and npm ci dependencies; see docs/local-browser.md')
    output.mkdir(mode=0o700, exist_ok=True)
    _validate_build_target(output, paths.root)
    result = subprocess.run([node, str(vite), 'build', '--outDir', str(output), '--emptyOutDir', '--base', '/'],
                            cwd=ROOT, shell=False, check=False)
    if result.returncode:
        raise RuntimeError(f'Renderer build failed (exit {result.returncode}); server was not started')
    _validate_build_target(output, paths.root)
    if not (output / 'index.html').is_file():
        raise RuntimeError('Renderer build did not produce index.html; server was not started')
    return output


def certificate_fingerprint(cert_file):
    """Conventional SHA-256 fingerprint of certificate DER, never PEM bytes."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes
    return x509.load_pem_x509_certificate(Path(cert_file).read_bytes()).fingerprint(hashes.SHA256()).hex()


def _health(origin, cert_file, fingerprint):
    authority = urlsplit(origin)
    context = ssl.create_default_context(cafile=str(cert_file))
    # A supplied server leaf can be signed by a CA whose chain is not separately
    # installed. Trust only the validated leaf for this local readback.
    if hasattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN'):
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    connection = http.client.HTTPSConnection('127.0.0.1', authority.port, timeout=1, context=context)
    try:
        # Connect to the owned IPv4 socket, verifying the DNS localhost SAN.
        plain = socket.create_connection(('127.0.0.1', authority.port), timeout=1)
        try:
            connection.sock = context.wrap_socket(plain, server_hostname='localhost')
        except BaseException:
            plain.close()
            raise
        if hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest() != fingerprint:
            raise RuntimeError('Local TLS readiness certificate differs from the owned certificate')
        connection.request('GET', '/health', headers={'Host': authority.netloc, 'Origin': origin, 'Connection': 'close'})
        response = connection.getresponse()
        body = response.read(65537)
        return response.status == 200 and len(body) <= 65536 and json.loads(body).get('status') == 'ok'
    finally:
        connection.close()


async def run_https(api, renderer, origin, sock, cert, key, paths, *, no_open, readiness_timeout=30):
    """Serve only the already-owned socket, retaining backend startup/shutdown."""
    import uvicorn
    from backend.local_web_app import LocalWebApp
    config = uvicorn.Config(LocalWebApp(api, renderer, origin), host='127.0.0.1', port=sock.getsockname()[1],
                            ssl_certfile=str(cert), ssl_keyfile=str(key), proxy_headers=False,
                            forwarded_allow_ips='', access_log=False, lifespan='on',
                            timeout_graceful_shutdown=5, log_level='info')
    server = uvicorn.Server(config)
    fingerprint = certificate_fingerprint(cert)
    serve = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        deadline = time.monotonic() + readiness_timeout
        ready = False
        while time.monotonic() < deadline:
            if serve.done():
                await serve
                break
            if server.started:
                try:
                    ready = await asyncio.to_thread(_health, origin, cert, fingerprint)
                except (OSError, ssl.SSLError, ValueError, http.client.HTTPException, RuntimeError):
                    ready = False
                if ready:
                    break
            await asyncio.sleep(.05)
        if not ready:
            raise RuntimeError('Owned local HTTPS health readiness failed; browser was not opened')
        print('MODU_WEB_READY=' + json.dumps({
            'url': origin, 'pid': os.getpid(), 'data_dir': str(paths.root),
            'certificate_file': str(cert), 'certificate_sha256': fingerprint,
        }), flush=True)
        if not no_open:
            # Browser selection is an OS/user preference; a failed opener does
            # not invalidate a ready server or disclose private auth material.
            try:
                if not webbrowser.open(origin):
                    print(f'Open {origin} in your browser.', flush=True)
            except (OSError, webbrowser.Error):
                print(f'Open {origin} in your browser.', flush=True)
        await serve
    finally:
        if not serve.done():
            server.should_exit = True
            if not server.started:
                serve.cancel()
            await asyncio.gather(serve, return_exceptions=True)

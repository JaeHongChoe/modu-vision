"""Owned renderer build, verified health readback and direct Uvicorn TLS lifetime."""
import asyncio
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
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


def _stop_renderer_builder(process, group):
    """Stop only this Popen's new build group, including a departed leader's children."""
    def send_owned(signum):
        try:
            if group is not None:
                os.killpg(group, signum)
            elif process.poll() is None:
                process.send_signal(signum)
        except ProcessLookupError:
            pass

    def finished():
        process.poll()  # Reap the direct child without waiting on grandchildren.
        if group is None:
            return process.returncode is not None
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            # macOS may report EPERM for a just-killed descendant group during
            # OS reaping. Keep waiting; do not mistake this for a live writer
            # or let a probe error replace the original termination status.
            return False
        return False

    send_owned(signal.SIGTERM)
    deadline = time.monotonic() + 2
    while not finished() and time.monotonic() < deadline:
        time.sleep(.02)
    if not finished():
        send_owned(signal.SIGKILL)
    # The direct child must be reaped while its caller still holds instance_lock.
    process.wait(timeout=2)
    # Allow killed descendants to exit; they may remain OS-owned zombies, but
    # SIGKILL has already prevented them from returning to output-writing code.
    deadline = time.monotonic() + 2
    while not finished() and time.monotonic() < deadline:
        time.sleep(.02)


def _run_renderer_builder(argv):
    """Own spawn-to-reap lifetime even when a startup termination signal arrives."""
    from backend.engine.process_isolation import session_isolation
    process = None
    group = None
    cleaning = False
    interrupted = None
    signals = {signal.SIGINT, signal.SIGTERM}
    previous = {signum: signal.getsignal(signum) for signum in signals}

    def interrupt(signum, frame):
        nonlocal interrupted
        interrupted = interrupted or signum
        # Repeated termination must not interrupt the cleanup's bounded waits.
        if not cleaning:
            raise SystemExit(128 + interrupted)

    for signum in signals:
        signal.signal(signum, interrupt)
    try:
        # Block the spawn/handle-assignment window: an async exception after
        # fork but before Popen is assigned must not leave an unowned builder.
        mask = signal.pthread_sigmask(signal.SIG_BLOCK, signals) if os.name == 'posix' else None
        try:
            process = subprocess.Popen(argv, cwd=ROOT, shell=False, **session_isolation())
            group = process.pid if os.name == 'posix' else None
        finally:
            if mask is not None:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        return process.wait()
    finally:
        cleaning = True
        try:
            if process is not None:
                _stop_renderer_builder(process, group)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        if interrupted:
            raise SystemExit(128 + interrupted)


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
    status = _run_renderer_builder([node, str(vite), 'build', '--outDir', str(output), '--emptyOutDir', '--base', '/'])
    if status:
        raise RuntimeError(f'Renderer build failed (exit {status}); server was not started')
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
    port = authority.port if authority.port is not None else 443
    context = ssl.create_default_context(cafile=str(cert_file))
    # A supplied server leaf can be signed by a CA whose chain is not separately
    # installed. Trust only the validated leaf for this local readback.
    if hasattr(ssl, 'VERIFY_X509_PARTIAL_CHAIN'):
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    connection = http.client.HTTPSConnection('127.0.0.1', port, timeout=1, context=context)
    try:
        # Connect to the owned IPv4 socket, verifying the DNS localhost SAN.
        plain = socket.create_connection(('127.0.0.1', port), timeout=1)
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
    from backend.main import SharedSecurityHeaders
    # The browser entrypoint serves static files and outer authority refusals as
    # well as API responses, so apply the shared-server policy to the whole app.
    app = SharedSecurityHeaders(LocalWebApp(api, renderer, origin))
    config = uvicorn.Config(app, host='127.0.0.1', port=sock.getsockname()[1],
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

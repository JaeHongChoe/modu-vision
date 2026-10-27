"""Run the source-installed Studio at an authenticated localhost HTTPS origin."""
import argparse
import asyncio
import getpass
import os
from pathlib import Path
import socket
import sys
import warnings

from backend.local_web_state import configure_environment, ensure_tls, instance_lock, web_paths


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, help='Separate persistent browser data directory')
    parser.add_argument('--port', type=int, default=8443, help='Local HTTPS port (default 8443; 0 allocates a port)')
    parser.add_argument('--cert-file', type=Path, help='Existing localhost SAN certificate (PEM)')
    parser.add_argument('--key-file', type=Path, help='Existing private key (requires --cert-file)')
    parser.add_argument('--no-open', action='store_true', help='Print the URL without opening a browser')
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    if (args.cert_file is None) != (args.key_file is None):
        parser.error('Supply --cert-file and --key-file together')
    return args


def ensure_first_account(store, data_dir):
    """Provision only an empty store, in a terminal with non-echoing passwords."""
    if store.users():
        return
    if not sys.stdin.isatty():
        raise RuntimeError(f'First account requires an interactive terminal. Run npm run web -- --data-dir "{data_dir}" there to set it up')
    username = input('First administrator username (3-80 letters, numbers, _ . @ -): ').strip()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            password = getpass.getpass('Password (12-1024 characters; hidden): ')
            confirmation = getpass.getpass('Confirm password (hidden): ')
    except getpass.GetPassWarning as exc:
        raise RuntimeError('A hidden password terminal is required; no account was created') from exc
    if password != confirmation:
        raise ValueError('Passwords do not match; no account was created')
    store.bootstrap(username, password)
    print('Local administrator configured. Sign in in the browser.', flush=True)


def bind_loopback(port):
    """Reserve an exclusive IPv4 loopback listener before deriving the origin."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == 'posix':
            # Permit restart after our accepted connections enter TIME_WAIT.
            # Windows reuse has different ownership semantics; leave it disabled.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        # Reusable POSIX sockets must listen to exclude other reservations while
        # the renderer builds. Uvicorn accepts/handles requests after app startup.
        sock.listen()
    except OSError as exc:
        sock.close()
        raise RuntimeError(f'Local HTTPS port {port} is in use or unavailable; choose --port PORT. No other process was stopped') from exc
    return sock


def main(argv=None):
    args = parse_args(argv)  # --help exits before storage/import/build work.
    try:
        paths = web_paths(args.data_dir)
        configure_environment(paths)  # Must precede every backend engine import.
        with instance_lock(paths), bind_loopback(args.port) as sock:
            port = sock.getsockname()[1]
            origin = 'https://localhost' if port == 443 else f'https://localhost:{port}'
            cert, key = ensure_tls(paths, args.cert_file, args.key_file)
            from backend.engine.shared_accounts import AccountStore
            ensure_first_account(AccountStore(paths.auth / 'accounts.sqlite'), paths.root)
            from backend.local_web_runtime import build_renderer, build_local_helper
            renderer = build_renderer(paths)
            helper_entry = build_local_helper(paths)
            import json
            os.environ['MODU_BROWSER_ORIGINS'] = json.dumps([origin])
            # Discard a desktop supervisor's capability; keep the freshly
            # generated backend capability entirely on the server.
            os.environ.pop('VISION_AI_STUDIO_API_TOKEN', None)
            from backend.main import create_app
            api = create_app(project_dir=str(paths.projects), shared_auth_dir=str(paths.auth))
            from backend.local_host.launcher import serve_with_local_host
            asyncio.run(serve_with_local_host(api, renderer, origin, sock, cert, key, paths,
                                             helper_entry, no_open=args.no_open))
        return 0
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, RuntimeError, EOFError) as exc:
        print(f'Local browser launch failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

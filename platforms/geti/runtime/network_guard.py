"""Process-local outbound deny policy, inherited by Python spawned workers.

Only an explicit checksum-pinned public weight preparation can temporarily
resolve/connect to its declared HTTPS origins. No ambient proxy or credentials.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import ipaddress
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.parse import urlsplit

_allowed = ContextVar('modu_geti_download_hosts', default=None)
_installed = False


def _loopback(host):
    if host == 'localhost': return True
    try: return ipaddress.ip_address(host).is_loopback
    except ValueError: return False


def _denied(event, target):
    root = os.environ.get('LOG_DIR')
    if root:
        try:
            folder = Path(root)
            folder.mkdir(parents=True, exist_ok=True)
            with (folder / 'egress-denials.jsonl').open('a') as handle:
                handle.write(json.dumps({'event': event, 'target': str(target), 'at': time.time()}) + '\n')
        except OSError: pass
    raise PermissionError('Geti runtime outbound network is disabled; prepare audited public weights explicitly')


def install():
    global _installed
    if _installed: return
    _installed = True
    original_getaddrinfo = socket.getaddrinfo
    def getaddrinfo(host, port, *args, **kwargs):
        allow = _allowed.get()
        if not _loopback(host) and (allow is None or host not in allow['hosts'] or port not in {443, '443'}):
            _denied('dns', host)
        result = original_getaddrinfo(host, port, *args, **kwargs)
        if allow is not None:
            allow['addresses'].update(row[4][0] for row in result)
        return result
    socket.getaddrinfo = getaddrinfo
    def audit(event, args):
        if event not in {'socket.connect', 'socket.sendto'}: return
        address = args[-1]
        if not isinstance(address, tuple): return  # owned local Unix-domain IPC
        host, port = address[:2]
        if _loopback(host): return
        allow = _allowed.get()
        if allow is not None and port == 443 and host in allow['addresses']: return
        _denied(event, address)
    sys.addaudithook(audit)


@contextmanager
def public_weight_download(urls):
    hosts = set()
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme != 'https' or parts.username or parts.password or parts.port not in {None, 443} or not parts.hostname:
            raise ValueError('Public weights must use an audited HTTPS source')
        hosts.add(parts.hostname)
    # GitHub's official release attachments use this redirect origin.
    if 'github.com' in hosts: hosts.add('release-assets.githubusercontent.com')
    token = _allowed.set({'hosts': hosts, 'addresses': set()})
    try: yield
    finally: _allowed.reset(token)

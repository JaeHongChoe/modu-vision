"""Explicit server secret provisioning and TLS listener admission.

This validates startup controls; it does not claim production certificate trust,
OS credential-vault provisioning, quality approval or target qualification.
"""
import ipaddress
import os
from pathlib import Path
import ssl
import stat


def _server_file(value, *, private, maximum):
    path = Path(value).expanduser().absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Server credential/certificate path must be unlinked')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    descriptor = os.open(path, flags)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or not 0 < info.st_size <= maximum:
            raise ValueError('Server credential/certificate must be a bounded regular file with one owner path')
        if private:
            if os.name == 'nt':
                raise ValueError('Windows secret-file ACL qualification is unavailable; provision the service environment instead')
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('Server secret must be owned by this service user and unreadable by other users')
        raw = os.read(descriptor, maximum + 1)
        if len(raw) > maximum or len(raw) != info.st_size:
            raise ValueError('Server credential/certificate changed while reading')
        final = path.stat(follow_symlinks=False)
        if (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise ValueError('Server credential/certificate identity changed while reading')
        return path, raw
    finally:
        os.close(descriptor)


def service_token(value, token_file=None):
    if token_file:
        if value:
            raise ValueError('Select one server secret source: environment/argument or protected file')
        _, raw = _server_file(token_file, private=True, maximum=4096)
        try: value = raw.decode('utf-8').removesuffix('\n')
        except UnicodeError as exc: raise ValueError('Server secret file is not UTF-8') from exc
    if (not isinstance(value, str) or not 16 <= len(value) <= 4096
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise ValueError('Provision a server access token of 16 to 4096 printable characters without whitespace')
    return value


def listener_tls(host, certificate=None, private_key=None):
    """HTTP is limited to loopback; externally bound listeners need real TLS."""
    if not isinstance(host, str) or not host or host.strip() != host:
        raise ValueError('Server listener host is invalid')
    try: loopback = ipaddress.ip_address(host).is_loopback
    except ValueError: loopback = host == 'localhost'
    if bool(certificate) != bool(private_key):
        raise ValueError('TLS certificate and private key must be supplied together')
    if not certificate:
        if not loopback:
            raise ValueError('TLS is required for a non-loopback service listener; use a loopback tunnel or provision a certificate/key')
        return {}
    certificate, _ = _server_file(certificate, private=False, maximum=1024 * 1024)
    private_key, _ = _server_file(private_key, private=True, maximum=256 * 1024)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    try: context.load_cert_chain(str(certificate), str(private_key))
    except ssl.SSLError as exc: raise ValueError('TLS certificate/private key pair is invalid') from exc
    return {'ssl_certfile': str(certificate), 'ssl_keyfile': str(private_key), 'ssl_version': ssl.PROTOCOL_TLS_SERVER}

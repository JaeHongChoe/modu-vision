"""Private, persistent state for the localhost browser launcher.

This module deliberately imports no engine modules. Call configure_environment before
importing the backend so import-time caches and stores use the web installation.
The instance lock owns the web server only; it does not own detached workers.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import stat
import sys


@dataclass(frozen=True)
class WebPaths:
    root: Path
    projects: Path
    auth: Path
    tls: Path
    splits: Path
    thumbnails: Path
    flow_templates: Path


def web_paths(data_dir: str | Path | None = None) -> WebPaths:
    """Choose web-only user data without adopting any desktop environment path."""
    if data_dir is not None:
        root = Path(data_dir).expanduser().absolute()
    elif sys.platform == 'darwin':
        root = Path.home() / 'Library' / 'Application Support' / 'Modu Vision Web'
    elif sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA')
        root = (Path(base) if base else Path.home() / 'AppData' / 'Local') / 'Modu Vision Web'
    else:
        base = os.environ.get('XDG_DATA_HOME')
        root = (Path(base) if base and Path(base).is_absolute() else Path.home() / '.local' / 'share') / 'modu-vision-web'
    return WebPaths(root, *(root / name for name in (
        'projects', 'auth', 'tls', 'splits', 'thumbnails', 'flow_templates',
    )))


def _refuse_links(path: Path) -> None:
    """Keep user-selected paths literal, while allowing macOS system aliases."""
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            # /var and /tmp are OS-owned canonical aliases on macOS. A selected
            # root or a custom link further down the path still fails closed.
            if part != path and sys.platform == 'darwin' and part in {Path('/var'), Path('/tmp'), Path('/etc')}:
                continue
            raise ValueError(f'Linked web state or TLS path is not allowed: {part}')
        if getattr(info, 'st_file_attributes', 0) & 0x400:  # Windows reparse point
            raise ValueError(f'Reparse web state or TLS path is not allowed: {part}')


def _directory(path: Path) -> None:
    _refuse_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    _refuse_links(path)
    if not path.is_dir():
        raise ValueError(f'Web state path must be a directory: {path}')


def configure_environment(paths: WebPaths) -> None:
    """Pin all discovered engine global mutable stores; never replace HOME."""
    # Validate every destination before creating directories or changing env.
    for path in vars(paths).values():
        _refuse_links(path)
    for path in vars(paths).values():
        _directory(path)
    os.environ.update({
        'VISION_AI_STUDIO_USER_DATA_DIR': str(paths.root),
        'MODU_SPLIT_MANIFEST_DIR': str(paths.splits),
        'MODU_THUMBNAIL_CACHE_DIR': str(paths.thumbnails),
        'MODU_FLOW_TEMPLATE_DIR': str(paths.flow_templates),
        # An inherited explicit lease override otherwise escapes the new root.
        'VISION_RESOURCE_LEASE_DB': str(paths.root / 'resource_leases.sqlite3'),
    })
    # Keep the registry-local default layout; inherited server/NAS/S3 choices
    # must not redirect this local-only launcher's managed artifact bytes.
    for name in ('VISION_ARTIFACT_OBJECT_ROOT', 'VISION_ARTIFACT_S3_BUCKET', 'VISION_ARTIFACT_S3_ENDPOINT'):
        os.environ.pop(name, None)


def _regular_fd(path: Path, flags: int, mode: int = 0o600) -> int:
    _refuse_links(path)
    try:
        previous = path.lstat()
    except FileNotFoundError:
        if not flags & os.O_CREAT:
            raise
    else:
        if not stat.S_ISREG(previous.st_mode) or previous.st_nlink != 1:
            raise ValueError(f'Web state and TLS files must be unlinked regular files: {path}')
    # NONBLOCK also prevents a raced-in FIFO from hanging before fstat rejects it.
    fd = os.open(path, flags | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
                 | getattr(os, 'O_NONBLOCK', 0), mode)
    try:
        info = os.fstat(fd)
        current = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or stat.S_ISLNK(current.st_mode)
                or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)):
            raise ValueError(f'Web state and TLS files must be unlinked regular files: {path}')
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _file_lock(path: Path):
    _directory(path.parent)
    fd = _regular_fd(path, os.O_RDWR | os.O_CREAT)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError as exc:
            raise RuntimeError(f'Another local web instance owns this data root: {path.parent}') from exc
        yield
    finally:
        try:
            if acquired:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        # Do not unlink: a waiter may already have opened this exact inode.


@contextmanager
def instance_lock(paths: WebPaths):
    """Hold an OS lock; process death releases it without PID-based cleanup."""
    with _file_lock(paths.root / '.web-instance.lock'):
        yield


def _private_key_permissions(fd: int, path: Path) -> None:
    info = os.fstat(fd)
    if info.st_mode & 0o077 or info.st_uid != os.getuid():
        raise ValueError(f'TLS private key must be owned by you and accessible only to you (chmod 600): {path}')
    if sys.platform == 'darwin':
        import ctypes
        import errno

        guidance = (f'Cannot establish user-only TLS key privacy for {path}: '
                    'review key and parent directory ACLs explicitly, then replace the TLS pair; no ACLs were changed')
        try:
            library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
            getter, free = library.acl_get_fd_np, library.acl_free
            getter.argtypes, getter.restype = [ctypes.c_int, ctypes.c_int], ctypes.c_void_p
            free.argtypes, free.restype = [ctypes.c_void_p], ctypes.c_int
            ctypes.set_errno(0)
            acl = getter(fd, 0x100)  # macOS SDK: ACL_TYPE_EXTENDED.
            query_errno = ctypes.get_errno()
        except (AttributeError, OSError) as exc:
            raise RuntimeError(f'{guidance}; native ACL query unavailable') from exc
        if acl:
            # Any extended ACL is conservatively unsupported, including benign
            # entries. POSIX mode bits do not describe these independent grants.
            if free(acl) != 0:
                raise RuntimeError(f'{guidance}; native ACL release failed')
            raise ValueError(f'{guidance}; extended ACL present')
        if query_errno != errno.ENOENT:
            raise RuntimeError(f'{guidance}; native ACL query failed (errno {query_errno})')


def _read_tls(path: Path, *, private: bool = False) -> bytes:
    fd = _regular_fd(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        if private:
            _private_key_permissions(stream.fileno(), path)
        return stream.read()


def _validate_tls(cert_file: Path, key_file: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    certificate = x509.load_pem_x509_certificate(_read_tls(cert_file))
    key = serialization.load_pem_private_key(_read_tls(key_file, private=True), password=None)
    now = datetime.now(timezone.utc)
    if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
        raise ValueError('Localhost TLS certificate is expired or not yet valid')
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except x509.ExtensionNotFound as exc:
        raise ValueError('Localhost TLS certificate requires a localhost DNS SAN') from exc
    if 'localhost' not in names.get_values_for_type(x509.DNSName):
        raise ValueError('Localhost TLS certificate requires a localhost DNS SAN')
    public_format = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if certificate.public_key().public_bytes(*public_format) != key.public_key().public_bytes(*public_format):
        raise ValueError('Localhost TLS certificate and private key do not match')


def _generate_tls(cert_file: Path, key_file: Path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=365))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
                       x509.IPAddress(ipaddress.ip_address('::1')),
                   ]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                   .sign(key, hashes.SHA256()))
    # Exclusive creation never overwrites existing bytes, even after a race.
    # A failed write leaves the partial pair visible and fail-closed next time.
    for path, content in (
        (key_file, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
        (cert_file, certificate.public_bytes(serialization.Encoding.PEM)),
    ):
        fd = _regular_fd(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, 'wb') as stream:
            if path == key_file:
                # A 0600 create can still inherit an independent macOS ACL.
                # Establish privacy before serializing any secret key bytes.
                _private_key_permissions(stream.fileno(), path)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())


def ensure_tls(paths: WebPaths, cert_file: Path | None = None, key_file: Path | None = None) -> tuple[Path, Path]:
    """Generate only absent defaults, or validate an explicit existing pair.

    Existing invalid material is never repaired or replaced automatically. Windows
    is fail-closed until native private-key ACL validation is implemented.
    """
    if (cert_file is None) != (key_file is None):
        raise ValueError('Supply both a TLS certificate and private key')
    if os.name == 'nt':
        raise RuntimeError('Local web TLS requires private-key ACL validation, which is not yet supported on Windows; no TLS files were changed')
    custom = cert_file is not None
    cert = Path(cert_file).expanduser().absolute() if custom else paths.tls / 'localhost-cert.pem'
    key = Path(key_file).expanduser().absolute() if custom else paths.tls / 'localhost-key.pem'
    for path in (paths.root, cert, key):
        _refuse_links(path)
    if custom:
        if not cert.exists() or not key.exists():
            raise ValueError('The supplied TLS certificate and private key must both exist')
        _validate_tls(cert, key)
    else:
        with _file_lock(paths.tls / '.tls-generation.lock'):
            if cert.exists() != key.exists():
                raise ValueError(f'Incomplete localhost TLS pair in {paths.tls}; restore or explicitly replace both files')
            if not cert.exists():
                _generate_tls(cert, key)
            _validate_tls(cert, key)
    return cert, key

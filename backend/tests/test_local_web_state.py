"""Local browser state must stay private and refuse invalid existing TLS material."""

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest


def test_support_import_has_no_engine_or_main_side_effects():
    result = subprocess.run([sys.executable, '-c', '''import sys
import backend.local_web_state
assert not any(name == 'backend.main' or name.startswith('backend.engine') for name in sys.modules)
'''], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def state():
    # Keep absent-feature RED failures in individual tests, rather than collection.
    from backend import local_web_state
    return local_web_state


@pytest.mark.parametrize(('platform', 'variable', 'suffix'), [
    ('darwin', None, 'Library/Application Support/Modu Vision Web'),
    ('win32', 'LOCALAPPDATA', 'Modu Vision Web'),
    ('linux', 'XDG_DATA_HOME', 'modu-vision-web'),
])
def test_platform_roots_do_not_adopt_desktop_state(state, tmp_path, monkeypatch, platform, variable, suffix):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'desktop'))
    if variable:
        monkeypatch.setenv(variable, str(tmp_path / 'os-data'))
    root = tmp_path / ('os-data' if variable else '') / suffix
    paths = state.web_paths()
    assert paths.root == root
    assert paths.projects == root / 'projects'
    assert paths.auth == root / 'auth'
    assert paths.tls == root / 'tls'
    assert paths.splits == root / 'splits'
    assert paths.thumbnails == root / 'thumbnails'
    assert paths.flow_templates == root / 'flow_templates'
    assert not root.exists(), 'path selection must not create data'
    with pytest.raises(FrozenInstanceError):
        paths.root = tmp_path


@pytest.mark.parametrize(('platform', 'suffix'), [
    ('linux', '.local/share/modu-vision-web'),
    ('win32', 'AppData/Local/Modu Vision Web'),
])
def test_platform_fallbacks(state, tmp_path, monkeypatch, platform, suffix):
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    assert state.web_paths().root == tmp_path / suffix


def test_explicit_root_and_environment_isolate_mutable_stores(state, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    expected = {
        'VISION_AI_STUDIO_USER_DATA_DIR': tmp_path / 'web',
        'MODU_SPLIT_MANIFEST_DIR': tmp_path / 'web/splits',
        'MODU_THUMBNAIL_CACHE_DIR': tmp_path / 'web/thumbnails',
        'MODU_FLOW_TEMPLATE_DIR': tmp_path / 'web/flow_templates',
        'VISION_RESOURCE_LEASE_DB': tmp_path / 'web/resource_leases.sqlite3',
    }
    for name in expected:
        monkeypatch.setenv(name, str(tmp_path / 'foreign'))
    monkeypatch.setenv('HF_HOME', str(tmp_path / 'shared-weights'))
    home = os.environ.get('HOME')
    before = set(sys.modules)
    paths = state.web_paths('web')
    state.configure_environment(paths)
    assert {name: os.environ[name] for name in expected} == {name: str(path) for name, path in expected.items()}
    assert os.environ.get('HOME') == home
    assert os.environ['HF_HOME'] == str(tmp_path / 'shared-weights')
    assert not {name for name in set(sys.modules) - before if name.startswith('backend.engine') or name == 'backend.main'}
    assert all(path.is_dir() for path in vars(paths).values())


def test_instance_lock_excludes_another_process_and_releases_on_error(state, tmp_path):
    paths = state.web_paths(tmp_path / 'web')
    child = '''import sys
from backend.local_web_state import instance_lock, web_paths
try:
    with instance_lock(web_paths(sys.argv[1])):
        print('acquired')
except RuntimeError:
    print('busy')
'''
    def probe():
        return subprocess.run([sys.executable, '-c', child, str(paths.root)], check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(ValueError, match='controlled'):
        with state.instance_lock(paths):
            (paths.root / 'keep.txt').write_text('user data')
            assert probe() == 'busy'
            raise ValueError('controlled')
    assert probe() == 'acquired'
    assert (paths.root / 'keep.txt').read_text() == 'user data'


def test_instance_lock_recovers_after_owner_process_exits(state, tmp_path):
    root = tmp_path / 'web'
    child = '''import os, sys
from backend.local_web_state import instance_lock, web_paths
with instance_lock(web_paths(sys.argv[1])):
    os._exit(0)
'''
    subprocess.run([sys.executable, '-c', child, str(root)], check=True)
    with state.instance_lock(state.web_paths(root)):
        assert root.is_dir()


def test_tls_generates_localhost_san_private_key_and_reuses_bytes(state, tmp_path):
    paths = state.web_paths(tmp_path / 'web')
    cert, key = state.ensure_tls(paths)
    original = cert.read_bytes(), key.read_bytes()
    certificate = x509.load_pem_x509_certificate(original[0])
    assert 'localhost' in certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert certificate.not_valid_before_utc <= datetime.now(timezone.utc) < certificate.not_valid_after_utc
    assert key.stat().st_mode & 0o077 == 0
    assert state.ensure_tls(paths) == (cert, key)
    assert (cert.read_bytes(), key.read_bytes()) == original


def write_pair(directory, *, hostname='localhost', expired=False, future=False, mismatch=False):
    directory.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now + timedelta(days=1) if future else now - timedelta(days=2))
            .not_valid_after(now - timedelta(days=1) if expired else now + timedelta(days=10))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / 'custom-cert.pem', directory / 'custom-key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    if mismatch:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key_path.chmod(0o600)
    return cert_path, key_path


def test_valid_custom_tls_is_used_without_creating_default_pair(state, tmp_path):
    cert, key = write_pair(tmp_path / 'custom')
    paths = state.web_paths(tmp_path / 'web')
    assert state.ensure_tls(paths, cert, key) == (cert, key)
    assert not paths.tls.exists()


@pytest.mark.parametrize('case', ['expired', 'future', 'san', 'mismatch', 'permissions', 'malformed'])
def test_invalid_existing_tls_fails_without_replacing_material(state, tmp_path, case):
    cert, key = write_pair(tmp_path / 'custom', expired=case == 'expired', future=case == 'future',
                           hostname='other.example' if case == 'san' else 'localhost', mismatch=case == 'mismatch')
    if case == 'permissions':
        key.chmod(0o644)
    if case == 'malformed':
        cert.write_bytes(b'not a certificate')
    before = cert.read_bytes(), key.read_bytes(), key.stat().st_mode
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(state.web_paths(tmp_path / 'web'), cert, key)
    assert (cert.read_bytes(), key.read_bytes(), key.stat().st_mode) == before


@pytest.mark.parametrize('missing', ['cert', 'key'])
def test_partial_existing_tls_pair_is_left_intact(state, tmp_path, missing):
    paths = state.web_paths(tmp_path / 'web')
    cert, key = state.ensure_tls(paths)
    (cert if missing == 'cert' else key).unlink()
    survivor = key if missing == 'cert' else cert
    before = survivor.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(paths)
    assert survivor.read_bytes() == before
    assert not (cert if missing == 'cert' else key).exists()


def test_custom_pair_must_be_complete_and_exist(state, tmp_path):
    cert, key = write_pair(tmp_path / 'custom')
    paths = state.web_paths(tmp_path / 'web')
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(paths, cert_file=cert)
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(paths, key_file=key)
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(paths, tmp_path / 'absent-cert', tmp_path / 'absent-key')
    assert not (tmp_path / 'absent-cert').exists()
    assert not (tmp_path / 'absent-key').exists()


@pytest.mark.parametrize('link_kind', ['cert_symlink', 'key_symlink', 'key_hardlink', 'parent_symlink'])
def test_tls_refuses_linked_material(state, tmp_path, link_kind):
    cert, key = write_pair(tmp_path / 'custom')
    if link_kind == 'parent_symlink':
        linked = tmp_path / 'linked'
        linked.symlink_to(cert.parent, target_is_directory=True)
        cert, key = linked / cert.name, linked / key.name
    else:
        source = cert if link_kind == 'cert_symlink' else key
        linked = source.with_suffix('.linked')
        if link_kind == 'key_hardlink':
            os.link(source, linked)
        else:
            linked.symlink_to(source)
        if link_kind == 'cert_symlink':
            cert = linked
        else:
            key = linked
    before = cert.read_bytes(), key.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        state.ensure_tls(state.web_paths(tmp_path / 'web'), cert, key)
    assert (cert.read_bytes(), key.read_bytes()) == before


def test_data_root_link_is_refused_before_writes(state, tmp_path, monkeypatch):
    target = tmp_path / 'desktop'
    target.mkdir()
    linked = tmp_path / 'linked'
    linked.symlink_to(target, target_is_directory=True)
    paths = state.web_paths(linked)
    for operation in (state.configure_environment, state.ensure_tls):
        with pytest.raises((ValueError, RuntimeError)):
            operation(paths)
    with pytest.raises((ValueError, RuntimeError)):
        with state.instance_lock(paths):
            pass
    assert list(target.iterdir()) == []


@pytest.mark.skipif(os.name == 'nt', reason='POSIX FIFO rejection')
def test_tls_rejects_fifo_before_opening_it(state, tmp_path):
    cert, key = write_pair(tmp_path / 'custom')
    cert.unlink()
    os.mkfifo(cert)
    child = '''import sys
from backend.local_web_state import ensure_tls, web_paths
try:
    ensure_tls(web_paths(sys.argv[1]), sys.argv[2], sys.argv[3])
except ValueError:
    print('rejected')
'''
    result = subprocess.run([sys.executable, '-c', child, str(tmp_path / 'web'), str(cert), str(key)],
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'rejected'


def test_windows_tls_fails_before_creating_or_changing_files(state, tmp_path, monkeypatch):
    from types import SimpleNamespace
    paths = state.web_paths(tmp_path / 'web')
    cert, key = write_pair(tmp_path / 'custom')
    before = cert.read_bytes(), key.read_bytes()
    monkeypatch.setattr(state, 'os', SimpleNamespace(name='nt'))
    with pytest.raises(RuntimeError, match='ACL'):
        state.ensure_tls(paths)
    with pytest.raises(RuntimeError, match='ACL'):
        state.ensure_tls(paths, cert, key)
    assert not paths.root.exists()
    assert (cert.read_bytes(), key.read_bytes()) == before

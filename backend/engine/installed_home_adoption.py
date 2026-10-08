"""Explicit CAS adoption of original, attested-quiescent current POSIX stores.

Preview never opens source store constructors or creates source coordination
files. Adoption preserves every original byte and publishes ownership only
after a private sealed backup. Attestation is required because an unowned old
writer does not yet participate in global admission; this is not process proof.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
import uuid

from backend.engine.global_store_paths import OWNER_FILE, POINTER_FILE, SUPPORTED_SCOPES
from backend.engine.migration_guard import maintenance_guard
from backend.engine.migration_inventory import inventory
from backend.engine.runtime_process_control import atomic_private_json

CONTROL = '.installed-home-adoption'
MAX_FILES = 10000
MAX_FILE_BYTES = 512 * 1024**2
MAX_TOTAL_BYTES = 2 * 1024**3
MAX_RECORDS = 10000
_MARKERS = {OWNER_FILE, POINTER_FILE, '.global-generations', '.global-migrations',
    '.global-generation.json', 'application-active.json', 'application-update-pending.json',
    '.application-updates', '.application-generations', 'application-launch-lease.json',
    '.application-launches', 'application-database-ownership.lock', '.application-writer-epochs'}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value: raise ValueError('Duplicate installed-home JSON key is ambiguous')
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


def _root(root, scopes):
    if os.name != 'posix' or sys.platform not in {'darwin', 'linux'}:
        raise ValueError('Installed-home adoption requires qualified POSIX durability')
    if not isinstance(scopes, dict) or scopes != SUPPORTED_SCOPES:
        raise ValueError('Explicit complete current SUPPORTED_SCOPES are required')
    root = Path(root).expanduser()
    if not root.is_absolute() or root != root.resolve() or not root.is_dir():
        raise ValueError('Explicit canonical existing installation root is required')
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError('Installed-home adoption cannot follow linked roots')
    node = root.stat()
    if node.st_uid != os.getuid() or node.st_mode & 0o022:
        raise ValueError('Installation root must be owned by the current user without shared write access')
    if any((parent / OWNER_FILE).exists() or (parent / OWNER_FILE).is_symlink()
           for parent in root.parents):
        raise ValueError('Installation is inside another owned installation')
    return root, {'path': str(root), 'device': node.st_dev, 'inode': node.st_ino}


def _identity(node):
    return {'device': node.st_dev, 'inode': node.st_ino, 'uid': node.st_uid,
            'mode': stat.S_IMODE(node.st_mode), 'bytes': node.st_size,
            'mtime_ns': node.st_mtime_ns, 'ctime_ns': node.st_ctime_ns}


def _regular(path, *, limit=MAX_FILE_BYTES, allow_links=1, capture=False, sink=None, expected=None):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink > allow_links
                or before.st_nlink < 1 or before.st_uid != os.getuid()
                or before.st_mode & 0o022 or before.st_size > limit):
            raise ValueError('Unowned, linked, shared-writable or unbounded file: ' + str(path))
        if expected is not None and _identity(before) != {key: expected[key] for key in _identity(before)}:
            raise ValueError('Reviewed source identity differs before copy')
        digest = hashlib.sha256()
        chunks = []
        total = 0
        while block := os.read(descriptor, 1024 * 1024):
            total += len(block)
            if total > before.st_size:
                raise ValueError('Source grew during adoption read')
            digest.update(block)
            if capture: chunks.append(block)
            if sink is not None: sink(block)
        after = os.fstat(descriptor)
        current = path.lstat()
        if (_identity(before) != _identity(after) or _identity(before) != _identity(current)
                or not stat.S_ISREG(current.st_mode) or current.st_nlink != before.st_nlink
                or total != before.st_size):
            raise ValueError('Source identity or bytes changed during adoption read')
        if expected is not None and digest.hexdigest() != expected['sha256']:
            raise ValueError('Reviewed source bytes changed during copy')
        result = {'sha256': digest.hexdigest(), **_identity(before)}
        if capture: result['data'] = b''.join(chunks)
        return result
    finally:
        os.close(descriptor)


def _raw_inventory(root):
    files = []
    directories = []
    total = 0
    for parent, folders, names in os.walk(root, followlinks=False):
        base = Path(parent)
        if base == root:
            folders[:] = [name for name in folders if name != CONTROL]
            names = [name for name in names if name not in {OWNER_FILE, 'migration_admission.lock'}]
        for name in sorted(folders):
            path = base / name
            node = path.lstat()
            if (not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid()
                    or node.st_mode & 0o022):
                raise ValueError('Unowned, linked or shared-writable directory: ' + str(path))
            directories.append({'path': path.relative_to(root).as_posix(),
                'device': node.st_dev, 'inode': node.st_ino, 'uid': node.st_uid,
                'mode': stat.S_IMODE(node.st_mode)})
        folders.sort()
        for name in sorted(names):
            path = base / name
            row = {'path': path.relative_to(root).as_posix(), **_regular(path)}
            files.append(row)
            total += row['bytes']
            if len(files) + len(directories) > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise ValueError('Installed-home inventory exceeds bounded qualification')
    if len(directories) > MAX_FILES:
        raise ValueError('Installed-home directory inventory exceeds bounded qualification')
    return {'files': sorted(files, key=lambda row: row['path']),
            'directories': sorted(directories, key=lambda row: row['path']),
            'file_count': len(files), 'byte_count': total}


def _control_directory(root):
    directory = root / CONTROL
    if directory.exists() or directory.is_symlink():
        node = directory.lstat()
        if (not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid()
                or stat.S_IMODE(node.st_mode) != 0o700):
            raise ValueError('Adoption recovery directory is foreign or linked')
        if set(p.name for p in directory.iterdir()) - {'intent.json', 'seal.json', 'owner.json', 'backup', 'copy-staging'}:
            raise ValueError('Adoption recovery directory has unknown controls')
    return directory


def _read_control(path):
    snapshot = _regular(path, limit=8 * 1024**2, allow_links=2 if path.name == 'owner.json' else 1, capture=True)
    data = _json(snapshot['data'])
    if not isinstance(data, dict):
        raise ValueError('Invalid adoption control')
    return data


def _authority(root, scopes):
    from backend.engine.global_migration import _read_db, _authority_blockers
    blockers = _authority_blockers(root, scopes)
    with _read_db(root / scopes['context']) as db:
        identities = dict(db.execute('SELECT name,value FROM identities'))
        locations = list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations ORDER BY scope_key'))
    with _read_db(root / scopes['accounts']) as db:
        accounts = dict(db.execute('SELECT name,value FROM account_meta'))
        users = [row[0] for row in db.execute('SELECT id FROM users ORDER BY id')]
        memberships = list(db.execute('SELECT project_id,user_id,role FROM members ORDER BY project_id,user_id'))
        projects = list(db.execute('SELECT id,path FROM projects ORDER BY id'))
        for table in ('sessions', 'oidc_pending'):
            if db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]:
                blockers.append('Drain all original sessions and OIDC pending authority before adoption')
    if not locations:
        blockers.append('Original registered project authority is required; empty/copied provenance is unsupported')
    known = {(project, path) for _, _, project, path in locations}
    for key, workspace, project, directory in locations:
        path = Path(directory)
        if (workspace != identities.get('workspace_id') or not path.is_absolute()
                or not path.is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents))):
            blockers.append('Project namespace/path lacks original same-installation authority')
            continue
        try:
            manifest = _json(_regular(path / 'project.json', limit=4 * 1024**2, capture=True)['data'])
            if not isinstance(manifest, dict):
                raise ValueError('Original project manifest must be a JSON object')
            models = Path(manifest.get('models_dir', ''))
            if (manifest.get('id') != project or manifest.get('workspace_id', workspace) != workspace
                    or manifest.get('project_dir', directory) != directory
                    or not models.is_absolute() or models != models.resolve()
                    or not models.is_relative_to(path)):
                raise ValueError('Original project manifest/model namespace differs')
        except (ValueError, OSError, TypeError) as exc:
            blockers.append(str(exc))
    if any(row not in known for row in projects) or any(user not in users or project not in {p for p, _ in known}
            for project, user, _ in memberships):
        blockers.append('Account project/membership lacks original registered authority')
    return _digest({'context': identities, 'accounts': accounts, 'locations': locations,
                    'users': users, 'memberships': memberships, 'projects': projects}), blockers


def _preview(root, scopes, *, allow_owner=False):
    root, identity = _root(root, scopes)
    result = {'schema_version': 1, 'status': 'preview', 'root_identity': identity,
              'scope_sha256': _digest(scopes), 'scopes': dict(scopes),
              'owner_uid': os.getuid(), 'raw_inventory': {}, 'schema_sha256': {},
              'authority_sha256': None, 'blockers': [],
              'policy': 'original owned quiescent current POSIX stores; no session or worker authority created'}
    try:
        for name in _MARKERS - ({OWNER_FILE} if allow_owner else set()):
            if (root / name).exists() or (root / name).is_symlink():
                raise ValueError('Current or copied installation marker exists: ' + name)
        lock = root / 'migration_admission.lock'
        if lock.exists() or lock.is_symlink(): _regular(lock, limit=65536)
        control = _control_directory(root)
        if control.exists() and any(control.iterdir()):
            intent = _read_control(control / 'intent.json')
            if (intent.get('schema_version') != 1 or not isinstance(intent.get('source'), dict)
                    or intent['source'].get('root_identity') != identity
                    or intent['source'].get('scopes') != scopes):
                raise ValueError('Adoption recovery belongs to another original root or scope')
        before = _raw_inventory(root)
        from backend.engine.global_migration import _known_schemas, _schema, _read_db
        known = _known_schemas(scopes)
        for name, relative in scopes.items():
            path = root / relative
            if name.endswith('_journals'):
                if not path.is_dir(): raise ValueError('Missing declared journal directory: ' + name)
            elif not path.is_file(): raise ValueError('Missing declared scope: ' + name)
            elif name in known:
                actual = _schema(path)
                if actual != known[name]: raise ValueError('Adoption requires exact current ' + name + ' schema')
                result['schema_sha256'][name] = _digest(actual)
                with _read_db(path) as db:
                    for table, in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                        quoted = '"' + table.replace('"', '""') + '"'
                        if db.execute('SELECT COUNT(*) FROM ' + quoted).fetchone()[0] > MAX_RECORDS:
                            raise ValueError('Adoption scope record count exceeds qualification')
            elif name == 'profiles':
                from backend.remote.profiles import ComputeProfile
                raw = _json(_regular(path, limit=8 * 1024**2, capture=True)['data'])
                if (not isinstance(raw, dict) or not isinstance(raw.get('profiles'), list)
                        or len(raw['profiles']) > MAX_RECORDS):
                    raise ValueError('Compute profile store is invalid or unbounded')
                profiles = [ComputeProfile.model_validate(item) for item in raw['profiles']]
                if raw.get('selected') is not None and raw['selected'] not in {p.id for p in profiles}:
                    raise ValueError('Selected compute profile is missing')
        logical = inventory(root, kind='installed_home_adoption',
                            paths=[root / row['path'] for row in before['files']])
        result['blockers'].extend(logical['blockers'])
        from backend.engine.terminal_runtime_history import journal_blockers
        result['blockers'].extend(journal_blockers(root, scopes))
        authority, blockers = _authority(root, scopes)
        result['authority_sha256'] = authority
        result['blockers'].extend(blockers)
        if _raw_inventory(root) != before:
            raise ValueError('Original source changed during read-only preview')
        result['raw_inventory'] = before
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        result['blockers'].append(str(exc))
    result['blockers'] = sorted(set(result['blockers']))
    result['can_adopt'] = not result['blockers']
    result['preview_sha256'] = _digest({key: value for key, value in result.items()
                                       if key not in {'status', 'blockers', 'can_adopt'}})
    return result


def preview_installed_home(root, *, scopes):
    """Read-only bounded source/authority review; never discovers a user home."""
    return _preview(root, scopes)


def _verify_backup(directory, source):
    backup = directory / 'backup'
    actual = _raw_inventory(backup)
    expected = source['raw_inventory']
    if ({row['path'] for row in actual['directories']} != {row['path'] for row in expected['directories']}
            or [{key: row[key] for key in ('path', 'bytes', 'sha256')} for row in actual['files']]
            != [{key: row[key] for key in ('path', 'bytes', 'sha256')} for row in expected['files']]
            or any(row['mode'] != 0o600 for row in actual['files'])
            or any(row['mode'] != 0o700 for row in actual['directories'])
            or stat.S_IMODE(backup.stat().st_mode) != 0o700):
        raise ValueError('Sealed original backup membership/bytes/mode differ')
    return _digest([{key: row[key] for key in ('path', 'bytes', 'sha256')}
                    for row in expected['files']])


def _backup(root, directory, source):
    backup = directory / 'backup'
    if not backup.exists(): backup.mkdir(mode=0o700)
    node = backup.lstat()
    if not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid() or stat.S_IMODE(node.st_mode) != 0o700:
        raise ValueError('Original backup root is foreign or linked')
    staging = directory / 'copy-staging'
    if not staging.exists(): staging.mkdir(mode=0o700)
    node = staging.lstat()
    if not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid() or stat.S_IMODE(node.st_mode) != 0o700:
        raise ValueError('Private backup copy staging is foreign or linked')
    expected_temporary = {_digest(row['path']) + '.tmp' for row in source['raw_inventory']['files']}
    if set(p.name for p in staging.iterdir()) - expected_temporary:
        raise ValueError('Unknown private backup copy remains; do not overwrite it')
    for row in source['raw_inventory']['directories']:
        target = backup / row['path']
        if not target.exists(): target.mkdir(mode=0o700)
        node = target.lstat()
        if not stat.S_ISDIR(node.st_mode) or node.st_uid != os.getuid() or stat.S_IMODE(node.st_mode) != 0o700:
            raise ValueError('Backup directory is foreign or linked')
    for row in source['raw_inventory']['files']:
        target = backup / row['path']
        temporary = staging / (_digest(row['path']) + '.tmp')
        if target.exists() or target.is_symlink():
            actual = _regular(target, allow_links=2 if temporary.exists() else 1)
            if actual['bytes'] != row['bytes'] or actual['sha256'] != row['sha256']:
                raise ValueError('Existing adoption backup differs; do not overwrite it')
            if temporary.exists() or temporary.is_symlink():
                temp = _regular(temporary, allow_links=2)
                if (temp['device'], temp['inode']) != (actual['device'], actual['inode']):
                    raise ValueError('Staged backup alias differs from its published file')
                temporary.unlink()
            continue
        descriptor = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        node = os.fstat(descriptor)
        if (not stat.S_ISREG(node.st_mode) or node.st_nlink != 1 or node.st_uid != os.getuid()
                or stat.S_IMODE(node.st_mode) != 0o600 or node.st_size > row['bytes']):
            os.close(descriptor)
            raise ValueError('Private incomplete backup is not owned and bounded')
        with os.fdopen(descriptor, 'wb') as writer:
            os.ftruncate(writer.fileno(), 0)
            _regular(root / row['path'], limit=row['bytes'], expected=row, sink=writer.write)
            writer.flush(); os.fsync(writer.fileno())
        copied = _regular(temporary)
        if copied['bytes'] != row['bytes'] or copied['sha256'] != row['sha256']:
            raise ValueError('Private copy changed before backup publication')
        os.link(temporary, target, follow_symlinks=False)
        temporary.unlink()
    sealed = _verify_backup(directory, source)
    from backend.engine.global_migration import _sync_directories
    _sync_directories(backup)
    return sealed


def adopt_installed_home(root, *, scopes, expected_preview_sha256, owned_quiescent_attestation):
    """Publish one new root-bound descriptor after explicit review and attestation."""
    root, identity = _root(root, scopes)
    if not isinstance(expected_preview_sha256, str) or not re.fullmatch('[a-f0-9]{64}', expected_preview_sha256):
        raise ValueError('Separately supplied exact preview digest is required')
    attestation = owned_quiescent_attestation
    if (not isinstance(attestation, dict) or set(attestation) != {'owned_original', 'writers_quiescent', 'root_identity'}
            or attestation['owned_original'] is not True or attestation['writers_quiescent'] is not True
            or attestation['root_identity'] != identity):
        raise ValueError('Explicit original-ownership/quiescent-writer attestation for this root is required')
    with maintenance_guard(root, exclusive=True):
        from backend.engine.application_launch_lease import assert_quiescent
        assert_quiescent(root)
        owner = root / OWNER_FILE
        repeated = owner.exists() or owner.is_symlink()
        if repeated:
            owner_snapshot = _regular(owner, limit=65536, allow_links=2, capture=True)
            current = _json(owner_snapshot['data'])
            if (not isinstance(current, dict) or type(current.get('schema_version')) is not int
                    or current.get('schema_version') != 1
                    or current.get('root_identity') != identity or current.get('scopes') != scopes
                    or current.get('adoption_preview_sha256') != expected_preview_sha256):
                raise ValueError('Existing owner was not published by this reviewed adoption')
        fresh = _preview(root, scopes, allow_owner=repeated)
        if not fresh['can_adopt'] or fresh['preview_sha256'] != expected_preview_sha256:
            raise ValueError('Original source changed or cannot be adopted: ' + '; '.join(fresh['blockers']))
        directory = _control_directory(root)
        if not directory.exists(): directory.mkdir(mode=0o700)
        intent_path = directory / 'intent.json'
        if intent_path.exists():
            intent = _read_control(intent_path)
            if (set(intent) != {'schema_version', 'installation_id', 'source'} or type(intent['schema_version']) is not int
                    or intent['schema_version'] != 1
                    or not isinstance(intent['installation_id'], str)
                    or not re.fullmatch('[a-f0-9]{32}', intent['installation_id']) or intent['source'] != fresh):
                raise ValueError('Adoption recovery intent differs from original reviewed source')
        else:
            if any(directory.iterdir()): raise ValueError('Adoption recovery intent is missing')
            intent = {'schema_version': 1, 'installation_id': uuid.uuid4().hex, 'source': fresh}
            atomic_private_json(intent_path, intent)
        backup_sha = _backup(root, directory, fresh)
        seal = {'schema_version': 1, 'installation_id': intent['installation_id'],
                'preview_sha256': expected_preview_sha256, 'root_identity': identity,
                'scope_sha256': fresh['scope_sha256'], 'backup_sha256': backup_sha}
        seal_path = directory / 'seal.json'
        if seal_path.exists():
            if _read_control(seal_path) != seal: raise ValueError('Adoption backup seal changed')
        else: atomic_private_json(seal_path, seal)
        descriptor = {'schema_version': 1, 'installation_id': intent['installation_id'],
            'scopes': dict(scopes), 'root_identity': identity,
            'adoption_preview_sha256': expected_preview_sha256, 'adoption_backup_sha256': backup_sha}
        prepared = directory / 'owner.json'
        if prepared.exists():
            if _read_control(prepared) != descriptor: raise ValueError('Prepared adoption owner changed')
        else: atomic_private_json(prepared, descriptor)
        from backend.engine.global_migration import _sync_directories
        _sync_directories(directory)
        last = _preview(root, scopes, allow_owner=repeated)
        if not last['can_adopt'] or last['preview_sha256'] != expected_preview_sha256:
            raise ValueError('Original source changed while sealing backup; ownership not published')
        if repeated:
            if current != descriptor or _regular(owner, limit=65536, allow_links=2, capture=True) != owner_snapshot:
                raise ValueError('Published adoption descriptor changed')
        else:
            try: os.link(prepared, owner, follow_symlinks=False)
            except FileExistsError as exc: raise ValueError('Another owner already published; no replacement allowed') from exc
            _sync_directories(root, recursive=False)
        return {'status': 'adopted', 'installation_id': intent['installation_id'],
                'preview_sha256': expected_preview_sha256, 'backup_sha256': backup_sha,
                'backup_path': str(directory / 'backup'), 'root_identity': identity,
                'scope_sha256': fresh['scope_sha256'], 'sessions_activated': 0,
                'workers_adopted': 0, 'processes_signalled': 0}

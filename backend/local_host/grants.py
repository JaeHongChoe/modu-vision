"""Short-lived path selections bound to authenticated operations and file identities."""
from pathlib import Path
import os
import secrets
import stat
import threading
import time


PURPOSES = {
    'project-create': ({'project_dir'}, {'/api/project/create', '/api/local-host/projects/create'}),
    'project-open': ({'project_dir'}, {'/api/project/open', '/api/local-host/projects/open'}),
    'project-backup': ({'destination_dir'}, {'/api/project/backup', '/api/local-host/projects/backup'}),
    'project-restore-archive': ({'archive_path'}, {'/api/project/restore', '/api/local-host/projects/restore'}),
    'project-restore-target': ({'target_dir'}, {'/api/project/restore', '/api/local-host/projects/restore'}),
    'project-recover-target': ({'target_dir'}, {'/api/local-host/projects/recover'}),
    'dataset-source': ({'folder_path', 'source_dataset_dir'}, {'/api/dataset/import', '/api/project/update', '/api/label-candidates/setup'}),
    'label-import': ({'import_dir'}, {'/api/dataset/formats/import', '/api/dataset/formats/preview'}),
    'mask-import': ({'import_dir'}, {'/api/dataset/masks/import', '/api/dataset/masks/preview'}),
    'model-directory': ({'model_dir', 'mask_model_dir', 'checkpoint_path'}, {'/api/label-candidates/setup', '/api/label-suggestions/setup', '/api/foundation/setup'}),
    'pretrained-weights': ({'pretrained_checkpoint', 'checkpoint_path'}, {'/api/training/start', '/api/engine/train', '/api/training-workspace/prepare','/api/training-workspace/import-weights'}),
    'runtime-source': ({'source_dir'}, {'/api/product-delivery/runtime-packs/install'}),
    'runtime-inventory': ({'inventory_path'}, {'/api/product-delivery/runtime-packs/preview', '/api/product-delivery/runtime-packs/install'}),
    'project-compatibility': ({'project_dir'}, {'/api/project/compatibility/preview','/api/project/compatibility/apply'}),
    'inspection-image': ({'image_path', 'file_path'}, {'/api/inference/predict', '/api/inspections/run', '/api/model-execution/trials','/api/flowchart/run','/api/flowchart/partial-runs/prepare'}),
}


def _scope(authority):
    return tuple(authority.get(key) for key in ('actor_id', 'session_id', 'tab_id', 'workspace_id', 'project_id','role','permission_revision'))


def literal_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute() or len(str(path)) > 4096:
        raise ValueError('Select an absolute local path')
    for parent in (path, *path.parents):
        if parent.is_symlink() and not (parent != path and os.sys.platform == 'darwin' and parent in {Path('/var'), Path('/tmp'), Path('/etc')}):
            raise ValueError('Linked selections are not allowed')
    return path.resolve()


def identity(path):
    info = path.stat()
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise ValueError('Select a regular file or directory')
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise ValueError('Linked files are not allowed')
    # Directory contents can legitimately change while a user chooses another
    # file. The directory inode is the authority, not its volatile mtime.
    return (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
            None if stat.S_ISDIR(info.st_mode) else (info.st_size, info.st_mtime_ns))


class PathGrants:
    def __init__(self, *, protected_roots=(), now=time.monotonic, ttl=120):
        self.protected = tuple(Path(p).resolve() for p in protected_roots)
        self.now, self.ttl = now, ttl
        self.rows = {}
        self.lock = threading.RLock()

    def issue(self, authority, path, purpose, kind):
        if purpose not in PURPOSES or kind not in {'file', 'directory'}:
            raise ValueError('Choose a supported file operation')
        selected = literal_path(path)
        if any(selected == p or selected.is_relative_to(p) or kind == 'directory' and p.is_relative_to(selected) for p in self.protected):
            raise ValueError('Host credentials and private state cannot be selected')
        if kind == 'file' and not selected.is_file() or kind == 'directory' and not selected.is_dir():
            raise ValueError('The selected path has the wrong type')
        row = {'scope': _scope(authority), 'path': selected, 'identity': identity(selected),
               'purpose': purpose, 'expires': self.now() + self.ttl}
        with self.lock:
            self.rows = {key: value for key, value in self.rows.items() if value['expires'] > self.now()}
            if len(self.rows) >= 512:
                raise ValueError('Too many unconsumed file selections')
            token = secrets.token_urlsafe(24)
            self.rows[token] = row
        return {'path': str(selected), 'grant_id': token}

    def _current(self, authority, token):
        row = self.rows.get(token)
        if not row or row['scope'] != _scope(authority) or row['expires'] <= self.now():
            return None
        try:
            if literal_path(row['path']) != row['path'] or identity(row['path']) != row['identity']:
                return None
        except (OSError, ValueError):
            return None
        return row

    def validate(self, authority, tokens, value, field, endpoint):
        if not isinstance(tokens, list) or len(tokens) > 16:
            return None
        try: candidate = literal_path(value)
        except (OSError, TypeError, ValueError): return None
        with self.lock:
            for token in tokens:
                if not isinstance(token, str): continue
                row = self._current(authority, token)
                if not row: continue
                fields, routes = PURPOSES[row['purpose']]
                if field not in fields or endpoint not in routes: continue
                exact = candidate == row['path']
                # The restore chooser selects a parent, while the fresh target
                # is one named child. It does not grant the entire subtree.
                fresh_child = (row['purpose'] == 'project-restore-target' and candidate.parent == row['path']
                               and candidate.name == 'restored-project' and not candidate.exists())
                if exact or fresh_child: return token
        return None

    def consume(self, authority, tokens):
        unique = list(dict.fromkeys(tokens))
        with self.lock:
            if any(not self._current(authority, token) for token in unique):
                raise ValueError('File selection expired or changed; select it again')
            for token in unique: del self.rows[token]

    def revoke(self, *, actor_id=None, session_id=None, tab_id=None):
        with self.lock:
            self.rows = {key: row for key, row in self.rows.items() if not (
                (actor_id is None or row['scope'][0] == actor_id)
                and (session_id is None or row['scope'][1] == session_id)
                and (tab_id is None or row['scope'][2] == tab_id))}

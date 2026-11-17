"""Resolve an installer-owned portable runtime without executing its contents."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

UPSTREAM_COMMIT = 'ddcf394d550bf1b84ca262888260043af361f5e7'


def _contained(root: Path, relative: str, *, directory=False) -> Path:
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise ValueError('Runtime path must be a contained relative path')
    parts = PurePosixPath(relative)
    if parts.is_absolute() or any(x in {'', '.', '..'} for x in relative.split('/')):
        raise ValueError('Runtime path must be a contained relative path')
    candidate = root.joinpath(*parts.parts)
    cursor = root
    for part in parts.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError('Runtime inventory paths cannot be links')
    if not candidate.resolve().is_relative_to(root) or (directory and not candidate.is_dir()):
        raise ValueError('Runtime path is missing or outside its contained installation')
    return candidate


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read_installation(path: Path) -> dict:
    """Only trusted launcher configuration supplies this path; HTTP never does.

    Every selected executable/entrypoint/source file must be in the manifest.
    Full dependency inventories are checked by the builder/release hook; startup
    verifies executable, overlay and upstream Python/recipe source before spawn.
    """
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError('Runtime installation descriptor is missing or linked')
    root = path.parent.resolve()
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('version') != 1 or data.get('upstream_commit') != UPSTREAM_COMMIT:
        raise ValueError('Runtime installation identity is not supported')
    inventory_path = _contained(root, data.get('inventory'))
    if not inventory_path.is_file() or inventory_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Runtime inventory is missing or too large')
    if _sha(inventory_path) != data.get('inventory_sha256'):
        raise ValueError('Runtime inventory hash integrity failure')
    inventory = json.loads(inventory_path.read_text(encoding='utf-8'))
    rows = inventory.get('files')
    if inventory.get('version') != 1 or not isinstance(rows, list) or not rows:
        raise ValueError('Runtime inventory is invalid')
    python = _contained(root, data.get('python'))
    entrypoint = _contained(root, data.get('entrypoint'))
    source = _contained(root, data.get('source_root'), directory=True)
    weights = _contained(root, data.get('weights_root'), directory=True)
    names = set()
    critical = {data['python'], data['entrypoint']}
    source_prefix = data['source_root'] + '/'
    overlay_prefix = str(PurePosixPath(data['entrypoint']).parent) + '/'
    for row in rows:
        name = row.get('path')
        file = _contained(root, name)
        if name in names:
            raise ValueError('Runtime inventory contains duplicate files')
        names.add(name)
        check = name in critical or name.startswith(overlay_prefix) or name.startswith(source_prefix)
        if not check:
            continue
        if not file.is_file() or file.stat().st_size != row.get('size') or _sha(file) != row.get('sha256'):
            raise ValueError('Runtime file inventory integrity failure')
    if not critical.issubset(names) or not any(x.startswith(source_prefix) for x in names):
        raise ValueError('Runtime executable/source inventory is incomplete')
    for row in inventory.get('links', []):
        relative = row.get('path')
        if not isinstance(relative, str) or relative in names:
            raise ValueError('Runtime link inventory is invalid')
        parent, _, leaf = relative.rpartition('/')
        directory = _contained(root, parent, directory=True) if parent else root
        if not leaf or leaf in {'.', '..'} or '\\' in leaf or ':' in leaf:
            raise ValueError('Runtime link inventory is not contained')
        link = directory / leaf
        target = row.get('target')
        if (not isinstance(target, str) or not link.is_symlink() or link.readlink().as_posix() != target
                or not link.resolve().is_relative_to(root)):
            raise ValueError('Runtime link inventory integrity failure')
        names.add(relative)
    return {**data, 'root': str(root), 'python': str(python), 'entrypoint': str(entrypoint),
            'source_root': str(source), 'weights_root': str(weights)}

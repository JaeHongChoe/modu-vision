"""Recoverable overlay removal, published inside the caller's image/ledger lock.

Archives deliberately use no label/image extensions. They are retained after
restoration; the live overlay and metadata commit share exception rollback.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path


class RecoveryConflict(ValueError):
    pass


def _safe(root: Path, path: Path) -> Path:
    if path != root and not path.is_relative_to(root):
        raise ValueError('복구 경로가 라벨 폴더 밖에 있습니다.')
    for candidate in (path, *path.parents):
        if candidate.is_symlink():
            raise ValueError('라벨·복구 경로의 연결 파일은 사용할 수 없습니다.')
        if candidate == root: break
    if path.exists() and not path.is_file():
        raise ValueError('라벨·복구 파일이 일반 파일이 아닙니다.')
    return path


def _store(root, archive_scope=None):
    root = Path(root).absolute()
    if archive_scope is None: return root, root / '.recovery'
    scope = Path(archive_scope).absolute()
    relative = root.relative_to(scope).as_posix()
    # Project version restoration replaces by_dataset overlays. Recovery must
    # survive outside that tree, without becoming a version/training input.
    key = hashlib.sha256(relative.encode()).hexdigest()[:32]
    return scope, scope / '.recovery' / key


def paths(root: Path, image_id: str, archive_scope=None):
    root = Path(root).absolute()
    # Validate recovery parents even when no live label exists.
    scope, base = _store(root, archive_scope)
    _safe(scope, base / 'probe')
    return {'annotation': _safe(root, root / f'{image_id}.json'),
            'mask': _safe(root, root / 'masks' / f'{image_id}.png')}


def _manifest_path(root, recovery_id, archive_scope=None):
    if not re.fullmatch(r'[a-f0-9]{32}', recovery_id):
        raise ValueError('복구 기록 번호가 올바르지 않습니다.')
    scope, base = _store(root, archive_scope)
    return _safe(scope, base / recovery_id / 'manifest')


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')


def _read(root, image_id, recovery_id, archive_scope=None):
    path = _manifest_path(root, recovery_id, archive_scope)
    if not path.exists(): raise FileNotFoundError('라벨 복구 기록을 찾을 수 없습니다.')
    item = json.loads(path.read_bytes())
    if (item.get('schema_version') != 1 or item.get('image_id') != image_id
            or item.get('recovery_id') != recovery_id or not isinstance(item.get('files'), dict)
            or not item['files'] or set(item['files']) - {'annotation', 'mask'}):
        raise ValueError('라벨 복구 기록의 이미지·형식이 다릅니다.')
    return path, item


def history(root, image_id, archive_scope=None):
    paths(root, image_id, archive_scope)
    _, base = _store(root, archive_scope)
    items = []
    if base.exists():
        for entry in sorted(base.iterdir(), key=lambda p: p.name, reverse=True):
            if not re.fullmatch(r'[a-f0-9]{32}', entry.name): continue
            path = _manifest_path(root, entry.name, archive_scope)
            if not path.exists(): continue  # Rolled-back staging directories are empty.
            item = json.loads(path.read_bytes())
            if item.get('image_id') == image_id:
                _, item = _read(root, image_id, entry.name, archive_scope)
                items.append(item)
    return sorted(items, key=lambda item: item['created_at'], reverse=True)


def archive(root, image_id, actor, transaction, *, archive_scope=None, identity=None):
    live = paths(root, image_id, archive_scope)
    content = {kind: path.read_bytes() for kind, path in live.items() if path.exists()}
    if not content: return None
    recovery_id = uuid.uuid4().hex
    manifest_path = _manifest_path(root, recovery_id, archive_scope)
    item = {'schema_version': 1, 'recovery_id': recovery_id, 'image_id': image_id,
            'actor': actor, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'files': {kind: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                      for kind, data in content.items()}}
    if identity is not None: item['image_identity'] = identity
    updates = {manifest_path.parent / f'{kind}.data': data for kind, data in content.items()}
    updates[manifest_path] = _encoded(item)
    updates.update({live[kind]: None for kind in content})
    transaction.publish(updates)
    return item


def restoration(root, image_id, recovery_id, *, archive_scope=None, identity=None):
    live = paths(root, image_id, archive_scope)
    manifest_path, item = _read(root, image_id, recovery_id, archive_scope)
    if identity is not None and item.get('image_identity') != identity:
        raise RecoveryConflict('보관할 때와 원본 이미지가 다릅니다. 같은 원본을 연결한 뒤 복구하세요.')
    if any(path.exists() for path in live.values()):
        raise RecoveryConflict('새 라벨이 있어 덮어쓰지 않았습니다. 현재 라벨을 먼저 보관한 뒤 복구하세요.')
    content = {}
    for kind, receipt in item['files'].items():
        data = _safe(_store(root, archive_scope)[0], manifest_path.parent / f'{kind}.data').read_bytes()
        if receipt != {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}:
            raise ValueError('복구 사본의 크기·해시가 다릅니다. 원본 사본을 확인하세요.')
        content[kind] = data
    return live, manifest_path, item, content


def restore(prepared, actor, transaction):
    live, manifest_path, item, content = prepared
    # Keep earlier recovery actions if the same bytes were subsequently archived.
    item.setdefault('restorations', []).append({'actor': actor,
        'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})
    item.update(restored_by=actor, restored_at=item['restorations'][-1]['at'])
    transaction.publish({**{live[kind]: data for kind, data in content.items()}, manifest_path: _encoded(item)})
    return item

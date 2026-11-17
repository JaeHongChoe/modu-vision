"""Portable runtime manifests must fail closed before any external Python starts."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest


def fixture(tmp_path):
    root = tmp_path / 'portable-runtime'
    root.mkdir()
    files = {'python/bin/python3.14': b'owned-python', 'source/LICENSE': b'Apache fixture',
             'source/application/backend/app/main.py': b'owned source', 'overlay/server.py': b'owned overlay'}
    inventory = []
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        inventory.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest(), 'size': len(content)})
    (root / 'weights').mkdir()
    raw = json.dumps({'version': 1, 'files': inventory}).encode()
    (root / 'inventory.json').write_bytes(raw)
    descriptor = {'version': 1, 'python': 'python/bin/python3.14', 'source_root': 'source',
                  'entrypoint': 'overlay/server.py', 'weights_root': 'weights',
                  'upstream_commit': 'ddcf394d550bf1b84ca262888260043af361f5e7',
                  'inventory': 'inventory.json', 'inventory_sha256': hashlib.sha256(raw).hexdigest()}
    path = root / 'runtime.json'
    path.write_text(json.dumps(descriptor), encoding='utf-8')
    return root, path, descriptor


def read(path):
    return importlib.import_module('backend.platforms.geti.installation').read_installation(path)


def test_portable_runtime_resolves_after_directory_move(tmp_path):
    root, descriptor, _ = fixture(tmp_path)
    moved = tmp_path / '한국어 새 위치'
    root.rename(moved)
    result = read(moved / descriptor.name)
    assert Path(result['python']) == moved / 'python/bin/python3.14'
    assert Path(result['source_root']) == moved / 'source'
    assert Path(result['entrypoint']) == moved / 'overlay/server.py'


def test_python_byte_change_is_refused_before_launch(tmp_path):
    root, descriptor, _ = fixture(tmp_path)
    (root / 'python/bin/python3.14').write_bytes(b'different executable')
    with pytest.raises(ValueError, match='integrity|hash|inventory'):
        read(descriptor)


def test_manifest_cannot_redirect_executable_outside_runtime(tmp_path):
    _, descriptor, value = fixture(tmp_path)
    outside = tmp_path / 'outside-python'
    outside.write_bytes(b'not executable by this installer')
    value['python'] = '../outside-python'
    descriptor.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(ValueError, match='relative|outside|contained'):
        read(descriptor)


def test_inventory_and_symlink_tampering_is_rejected(tmp_path):
    root, descriptor, _ = fixture(tmp_path)
    target = root / 'overlay/server.py'
    target.unlink()
    target.symlink_to(tmp_path / 'external-secret')
    with pytest.raises(ValueError, match='link|missing|integrity|inventory'):
        read(descriptor)
    target.unlink()
    target.write_bytes(b'owned overlay')
    (root / 'inventory.json').write_text('{"version":1,"files":[]}', encoding='utf-8')
    with pytest.raises(ValueError, match='integrity|hash|inventory'):
        read(descriptor)


def test_recorded_internal_runtime_aliases_cannot_be_retargeted(tmp_path):
    root, descriptor, value = fixture(tmp_path)
    alias = root / 'python/bin/python'
    alias.symlink_to('python3.14')
    inventory_path = root / 'inventory.json'
    inventory = json.loads(inventory_path.read_text())
    inventory['links'] = [{'path': 'python/bin/python', 'target': 'python3.14'}]
    raw = json.dumps(inventory).encode()
    inventory_path.write_bytes(raw)
    value['inventory_sha256'] = hashlib.sha256(raw).hexdigest()
    descriptor.write_text(json.dumps(value), encoding='utf-8')
    assert read(descriptor)['python'] == str(root / 'python/bin/python3.14')
    alias.unlink()
    alias.symlink_to('../../../../external')
    with pytest.raises(ValueError, match='link|contained|inventory'):
        read(descriptor)

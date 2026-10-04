"""Large Windows file identities remain exact in the persistent image stat cache."""
import hashlib
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
from PIL import Image

from backend.engine import dataset_index


@pytest.mark.parametrize('device,inode', [
    (11, -(1 << 63) - 1),
    (11, -(1 << 63)),
    (11, (1 << 63) - 1),
    (11, 1 << 63),
    (11, (1 << 64) - 1),
    ((1 << 64) - 1, (1 << 128) - 1),
])
@pytest.mark.parametrize('trusted', [False, True])
def test_large_file_identities_seal_reuse_and_invalidate_without_changing_sources(
        tmp_path, monkeypatch, device, inode, trusted):
    source = tmp_path / '검사 source'
    image = source / 'good' / 'part.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (8, 6), 'white').save(image)
    original_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    identity = {'device': device, 'inode': inode}
    real_stat, real_read = Path.stat, dataset_index._read_entry
    reads = []

    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path == image:
            fields = {name: getattr(result, name) for name in dir(result) if name.startswith('st_')}
            fields.update(st_dev=identity['device'], st_ino=identity['inode'])
            return SimpleNamespace(**fields)
        return result

    def read(path):
        reads.append(path)
        return real_read(path)

    monkeypatch.setattr(Path, 'stat', stat)
    monkeypatch.setattr(dataset_index, '_STAT_CACHE_TRUSTED', trusted)
    monkeypatch.setattr(dataset_index, '_read_entry', read)
    index = dataset_index.DatasetIndex(tmp_path / 'registry' / 'index.sqlite3')

    def build():
        receipt = index.build_revision('p', tmp_path / 'project', source, 'classification', 'exclude')
        assert (receipt.state, receipt.image_count, receipt.valid_count, receipt.error_count) == ('prepared', 1, 1, 0)
        assert index.page('p', receipt.revision_id)['items'][0]['sha256'] == original_hash
        return receipt

    first = build()
    assert first.reused_entries == 0 and len(reads) == 1
    index = dataset_index.DatasetIndex(index.path)  # reuse must survive reopening the persisted cache
    second = build()
    assert second.reused_entries == int(trusted)
    assert len(reads) == (1 if trusted else 2)
    with sqlite3.connect(index.path) as db:
        cached_identity = db.execute('SELECT dev, ino FROM dataset_stat_cache').fetchone()
    restored_identity = tuple(value if isinstance(value, int) else int(value.partition(':')[2], 16)
                              for value in cached_identity)
    assert restored_identity == (device, inode), 'SQLite must retain the complete identity without REAL coercion'

    # Same bytes, size, mtime and ctime; for wide IDs change the high bits to catch any truncation to 64 bits.
    identity['inode'] += (1 << 64) if abs(inode) >= (1 << 64) else 1
    third = build()
    assert third.reused_entries == 0
    assert len(reads) == (2 if trusted else 3)
    with sqlite3.connect(index.path) as db:
        assert db.execute('SELECT dev, ino FROM dataset_stat_cache').fetchone() != cached_identity
    assert hashlib.sha256(image.read_bytes()).hexdigest() == original_hash
    assert first.manifest_sha256 == second.manifest_sha256 == third.manifest_sha256

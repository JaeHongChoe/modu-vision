"""S3-01 slice 2: persistent image index with immutable, completely validated dataset revisions.

Synthetic images in temporary folders; every source byte is hashed before and after: indexing only reads.
Platform behaviour that cannot occur on the test machine (Windows ctime, junctions, undecodable names) is emulated
explicitly and says so.
"""
import hashlib
import os
import threading
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def index(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    return DatasetIndex(index_path(tmp_path / 'registry'))


def _snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def _classification_tree(root, per_class=3, corrupt_at_tail=False):
    for label, color in (('ok', 'white'), ('ng', 'black')):
        folder = root / 'train' / label
        folder.mkdir(parents=True)
        for index in range(per_class):
            Image.new('RGB', (20, 18), color).save(folder / f'{index:04}.png')
    if corrupt_at_tail:
        (root / 'train' / 'ok' / 'zz_corrupt.png').write_bytes(b'not an image')
    return root


def _paths(index, receipt, **filters):
    return [row['relative_path'] for row in index.page('ns:a', receipt.revision_id, limit=500, **filters)['items']]


def _link(link, target):
    try:
        link.symlink_to(target, target_is_directory=Path(target).is_dir())
    except OSError:
        pytest.skip('links are not available here')


def test_every_image_is_validated_and_a_corrupt_tail_is_kept_as_a_receipt(index, tmp_path):
    source = tmp_path / '검사 data'
    good = source / 'good'
    good.mkdir(parents=True)
    for number in range(205):  # beyond the quick inspection's 201-image sample
        Image.new('RGB', (16, 16), 'white').save(good / f'{number:04}.png')
    (good / 'zz_corrupt.png').write_bytes(b'not an image')
    before = _snapshot(source)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    assert _snapshot(source) == before, 'indexing never changes a source byte'
    assert (receipt.image_count, receipt.valid_count, receipt.error_count, receipt.state) == (206, 205, 1, 'prepared')
    bad = index.page('ns:a', receipt.revision_id, valid=False)['items']
    assert [row['relative_path'] for row in bad] == ['good/zz_corrupt.png'] and bad[0]['error_code'] == 'UNIDENTIFIED_IMAGE'


@pytest.mark.parametrize('suffix,fmt', [('jpg', 'JPEG'), ('bmp', 'BMP'), ('tif', 'TIFF'), ('png', 'PNG')])
def test_a_truncated_bitstream_is_invalid_and_holds_a_reject_revision(index, tmp_path, suffix, fmt):
    from backend.engine.dataset_index import RevisionNotActivatable
    source = _classification_tree(tmp_path / 'source', per_class=1)
    cut = source / 'train' / 'ok' / f'cut.{suffix}'
    Image.effect_noise((64, 64), 60).convert('RGB').save(cut, format=fmt)
    cut.write_bytes(cut.read_bytes()[:int(cut.stat().st_size * 0.6)])
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    row = index.page('ns:a', receipt.revision_id, valid=False)['items'][0]
    assert row['relative_path'] == f'train/ok/cut.{suffix}' and row['error_code'] == 'DECODE_ERROR', row
    assert receipt.state == 'rejected'
    with pytest.raises(RevisionNotActivatable):
        index.activate('ns:a', receipt.revision_id, expected_active=None)


def test_an_unreadable_folder_is_a_gap_that_holds_a_reject_revision(index, tmp_path):
    if os.name == 'nt' or (hasattr(os, 'geteuid') and os.geteuid() == 0):
        pytest.skip('needs POSIX permissions enforced for this user')
    source = _classification_tree(tmp_path / 'source')
    locked = source / 'train' / 'locked'
    locked.mkdir()
    Image.new('RGB', (20, 18), 'gray').save(locked / 'hidden.png')
    locked.chmod(0)
    try:
        rejected = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
        excluded = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    finally:
        locked.chmod(0o755)
    assert (rejected.state, rejected.unreadable_folders, rejected.error_count) == ('rejected', 1, 0)
    assert excluded.state == 'prepared' and excluded.unreadable_folders == 1
    gaps = index.gaps('ns:a', excluded.revision_id)
    assert gaps[0]['relative_path'] == 'train/locked' and gaps[0]['reason'].startswith('FOLDER_UNREADABLE')


def test_the_reject_policy_records_the_revision_but_it_can_never_become_active(index, tmp_path):
    from backend.engine.dataset_index import RevisionNotActivatable
    source = _classification_tree(tmp_path / 'source', corrupt_at_tail=True)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert receipt.state == 'rejected' and receipt.error_count == 1
    with pytest.raises(RevisionNotActivatable):
        index.activate('ns:a', receipt.revision_id, expected_active=None)
    assert index.active('ns:a') is None


def test_an_empty_revision_is_never_active(index, tmp_path):
    from backend.engine.dataset_index import RevisionNotActivatable
    (tmp_path / 'empty').mkdir()
    receipt = index.build_revision('ns:a', tmp_path / 'project', tmp_path / 'empty', 'classification', 'reject')
    with pytest.raises(RevisionNotActivatable):
        index.activate('ns:a', receipt.revision_id, expected_active=None)


def test_revisions_are_immutable_and_activation_is_compare_and_swap(index, tmp_path):
    from backend.engine.dataset_index import StaleActiveRevision
    source = _classification_tree(tmp_path / 'source')
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    index.activate('ns:a', first.revision_id, expected_active=None)
    assert index.activate('ns:a', first.revision_id, expected_active=None) == first.revision_id, 'a repeat is a no-op'
    Image.new('RGB', (20, 18), 'red').save(source / 'train' / 'ng' / '9999.png')
    second = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', parent_revision=first.revision_id)
    assert (first.image_count, second.image_count) == (6, 7) and first.manifest_sha256 != second.manifest_sha256
    assert len(_paths(index, first)) == 6, 'the earlier revision is unchanged'
    with pytest.raises(StaleActiveRevision):
        index.activate('ns:a', second.revision_id, expected_active=None)  # a stale view of the active revision
    index.activate('ns:a', second.revision_id, expected_active=first.revision_id)
    assert index.active('ns:a') == second.revision_id
    assert [row['active'] for row in index.revisions('ns:a')] == [True, False]
    with pytest.raises(ValueError):
        index.build_revision('ns:b', tmp_path / 'project', source, 'classification', parent_revision=first.revision_id)


def test_concurrent_activations_from_one_view_have_exactly_one_winner(index, tmp_path):
    from backend.engine.dataset_index import DatasetIndex, StaleActiveRevision
    source = _classification_tree(tmp_path / 'source', per_class=1)
    candidates = [index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True).revision_id
                  for _ in range(6)]
    barrier, outcomes = threading.Barrier(len(candidates)), []

    def race(revision_id):
        other = DatasetIndex(index.path)  # its own connections, as another request would have
        barrier.wait()
        try:
            other.activate('ns:a', revision_id, expected_active=None)
            outcomes.append(('won', revision_id))
        except StaleActiveRevision:
            outcomes.append(('stale', revision_id))

    threads = [threading.Thread(target=race, args=(revision,)) for revision in candidates]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    winners = [revision for outcome, revision in outcomes if outcome == 'won']
    assert len(winners) == 1 and index.active('ns:a') == winners[0]


def test_the_same_inputs_give_the_same_manifest(index, tmp_path):
    source = _classification_tree(tmp_path / 'source')
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True)
    again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    assert first.manifest_sha256 == again.manifest_sha256 and first.revision_id != again.revision_id


def test_a_publication_key_returns_the_first_receipt(index, tmp_path):
    source = _classification_tree(tmp_path / 'source')
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', publication_key='job-1')
    Image.new('RGB', (20, 18), 'red').save(source / 'train' / 'ng' / 'late.png')
    assert index.build_revision('ns:a', tmp_path / 'project', source, 'classification', publication_key='job-1') == first
    assert len(index.revisions('ns:a')) == 1


def test_paging_is_deterministic_filtered_and_bound_to_its_revision(index, tmp_path):
    source = _classification_tree(tmp_path / 'source', per_class=5)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    seen, cursor = [], None
    while True:
        page = index.page('ns:a', receipt.revision_id, cursor=cursor, limit=3, label='ok')
        seen += [row['relative_path'] for row in page['items']]
        cursor = page['next_cursor']
        if not cursor:
            break
    assert seen == [f'train/ok/{n:04}.png' for n in range(5)]
    first = index.page('ns:a', receipt.revision_id, limit=2, label='ok')
    with pytest.raises(ValueError, match='another revision or filter'):
        index.page('ns:a', receipt.revision_id, cursor=first['next_cursor'], label='ng')
    with pytest.raises(KeyError):
        index.page('ns:b', receipt.revision_id)  # another project namespace never reads it
    with pytest.raises(ValueError):
        index.page('ns:a', receipt.revision_id, cursor='x' * 9000)


def test_labels_splits_and_identity_follow_the_app_listing_and_the_metadata_ledger(index, tmp_path):
    from backend.engine.dataset_index import image_identity
    from backend.engine.dataset_summary import dataset_summary
    source = tmp_path / 'classification-source'
    for folder in ('lotA/train/ok', 'ok/train', 'train/images', 'test/ng'):
        (source / folder).mkdir(parents=True)
        Image.new('RGB', (16, 16), 'white').save(source / folder / 'same.png')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    rows = {row['relative_path']: row for row in index.page('ns:a', receipt.revision_id)['items']}
    summary = {row['relative_path']: row for row in dataset_summary(source, 'classification')['items']}
    assert set(rows) == set(summary), 'the index and the listing see the same images'
    for path, row in rows.items():
        split = summary[path]['split'] if summary[path]['split'] in ('train', 'val', 'test') else None
        assert (row['label'], row['split']) == (summary[path]['label'], split), path
    assert rows['lotA/train/ok/same.png']['image_uuid'] == image_identity(tmp_path / 'project', source, 'lotA/train/ok/same.png')
    assert len({row['image_uuid'] for row in rows.values()}) == 4, 'identical names in different folders stay distinct'


@pytest.mark.parametrize('task,auxiliary', [('segmentation', 'masks'), ('detection', 'labels'), ('anomaly', 'ground_truth'),
                                            ('detection', 'annotations')])
def test_auxiliary_hidden_service_and_project_folders_are_not_dataset_images(index, tmp_path, task, auxiliary):
    source = tmp_path / 'source'
    for folder in ('train/good', f'train/{auxiliary}', '.thumbs', '@eaDir', 'project/models'):
        (source / folder).mkdir(parents=True)
    Image.new('RGB', (16, 16), 'white').save(source / 'train' / 'good' / 'a.png')
    for folder in (f'train/{auxiliary}', '.thumbs', '@eaDir', 'project/models'):
        Image.new('L', (16, 16), 0).save(source / folder / 'x.png')
    Image.new('RGB', (16, 16), 'gray').save(source / 'train' / 'good' / '.hidden.png')
    receipt = index.build_revision('ns:a', source / 'project', source, task)
    assert _paths(index, receipt) == ['train/good/a.png']


def test_the_task_subfolder_is_the_scan_root_as_in_the_listing(index, tmp_path):
    source = tmp_path / 'multi'
    for folder in ('segmentation/images', 'classification/ok'):
        (source / folder).mkdir(parents=True)
        Image.new('RGB', (16, 16), 'white').save(source / folder / 'a.png')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'segmentation')
    assert _paths(index, receipt) == ['segmentation/images/a.png']


def test_links_are_counted_when_skipped_and_walked_once_when_followed(index, tmp_path):
    source = _classification_tree(tmp_path / 'source', per_class=2)
    shared = tmp_path / 'nas' / 'scratch'
    shared.mkdir(parents=True)
    Image.new('RGB', (20, 18), 'red').save(shared / 'linked.png')
    outside_file = tmp_path / 'nas' / 'single.png'
    Image.new('RGB', (20, 18), 'blue').save(outside_file)
    _link(source / 'train' / 'scratch', shared)
    _link(source / 'train' / 'ok' / 'loop', source)
    _link(source / 'train' / 'ok' / 'outside.png', outside_file)
    _link(source / 'train' / 'ng' / 'inside.png', source / 'train' / 'ng' / '0000.png')
    kept = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    assert (kept.image_count, kept.skipped_links) == (5, 3), 'a team source never leaves its folder, and says so'
    assert 'train/ng/inside.png' in _paths(index, kept), 'a file link inside the source is listed, as in the gallery'
    followed = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', follow_links=True)
    rows = {row['relative_path']: row for row in index.page('ns:a', followed.revision_id, limit=500)['items']}
    assert followed.skipped_links == 1, 'a link to a folder containing the source is never walked'
    assert rows['train/scratch/linked.png']['label'] == 'scratch' and rows['train/scratch/linked.png']['via_link'] == 1
    assert 'train/ok/outside.png' in rows


def test_a_junction_counts_as_a_link_emulated(index, tmp_path, monkeypatch):
    """Windows junctions are not symlinks to Python; os.path.isjunction reports them (emulated here)."""
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=1)
    secret = tmp_path / 'secret'
    secret.mkdir()
    Image.new('RGB', (20, 18), 'red').save(secret / 'secret.png')
    _link(source / 'train' / 'j', secret)
    real_islink = Path.is_symlink
    monkeypatch.setattr(Path, 'is_symlink', lambda self: False if self.name == 'j' else real_islink(self))
    monkeypatch.setattr(dataset_index, '_isjunction', lambda path: Path(path).name == 'j')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    assert 'train/j/secret.png' not in _paths(index, receipt) and receipt.skipped_links == 1


def test_an_undecodable_name_is_an_invalid_entry_not_a_failed_build_emulated(index, tmp_path, monkeypatch):
    """Linux can hand Python a name that is not UTF-8 (a surrogate escape); emulated on this file system."""
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=1)
    real_discover = dataset_index._discover

    def with_bad_name(*args, **kwargs):
        inventory = real_discover(*args, **kwargs)
        stored, ok = dataset_index._storable('train/ok/\udcb0bad.png')
        inventory.entries.append((stored, source / 'train' / 'ok' / 'missing.png', 0, ok))
        return inventory

    monkeypatch.setattr(dataset_index, '_discover', with_bad_name)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    bad = index.page('ns:a', receipt.revision_id, valid=False)['items']
    assert [row['error_code'] for row in bad] == ['UNREPRESENTABLE_NAME'] and '\\xb0' in bad[0]['relative_path']


def test_a_rebuild_reuses_unchanged_files_only_where_stat_fields_are_change_evidence(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source')
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    reads = []
    real = dataset_index._read_entry
    monkeypatch.setattr(dataset_index, '_read_entry', lambda path: reads.append(Path(path).name) or real(path))
    changed = source / 'train' / 'ok' / '0001.bmp'  # uncompressed: a rewrite keeps the exact size
    Image.new('RGB', (20, 18), 'white').save(changed)
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    reads.clear()
    stat = changed.stat()
    Image.new('RGB', (20, 18), 'gray').save(changed)
    assert changed.stat().st_size == stat.st_size
    os.utime(changed, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # the old modification time is restored
    second = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    if os.name == 'nt':
        assert len(reads) == 7 and second.verified_all, 'Windows ctime is the creation time: everything is read again'
    else:
        assert reads == ['0001.bmp'], 'only the changed file is decoded again (its ctime moved)'
        assert (second.reused_entries, second.verified_all) == (6, False)
    reads.clear()
    third = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True)
    assert len(reads) == 7 and third.verified_all, 'verify re-reads every file'
    reads.clear()
    monkeypatch.setattr(dataset_index, '_STAT_CACHE_TRUSTED', False)  # Windows stat semantics
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    assert len(reads) == 7


def test_a_file_that_vanishes_or_changes_while_indexed_is_an_invalid_entry_not_a_failed_build(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=2)
    vanishing, changing = source / 'train' / 'ok' / '0000.png', source / 'train' / 'ng' / '0001.png'
    real_open = Image.open
    calls = []

    def race(handle, *args, **kwargs):
        calls.append(1)  # files are read in path order: train/ng/0000, train/ng/0001, train/ok/0000, ...
        if len(calls) == 1:
            vanishing.unlink()  # another program removes train/ok/0000 before it is reached
        if len(calls) == 2:
            stat = changing.stat()
            Image.new('RGB', (20, 18), 'blue').save(changing)  # train/ng/0001 is rewritten (same size) while decoded
            os.utime(changing, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return real_open(handle, *args, **kwargs)

    monkeypatch.setattr(dataset_index.Image, 'open', race)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    codes = {row['relative_path']: row['error_code'] for row in index.page('ns:a', receipt.revision_id, valid=False)['items']}
    assert codes == {'train/ng/0001.png': 'CHANGED_DURING_SCAN', 'train/ok/0000.png': 'READ_ERROR'}, codes
    assert receipt.state == 'rejected' and receipt.image_count == 4
    monkeypatch.setattr(dataset_index.Image, 'open', real_open)
    again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert again.state == 'prepared', 'the changed entry was not cached: it is read again'


def test_a_cancelled_build_records_no_revision_but_keeps_what_it_read(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    monkeypatch.setattr(dataset_index, '_BATCH', 2)
    source = _classification_tree(tmp_path / 'source', per_class=4)
    reads = []
    real = dataset_index._read_entry
    monkeypatch.setattr(dataset_index, '_read_entry', lambda path: reads.append(path) or real(path))
    with pytest.raises(InterruptedError):
        index.build_revision('ns:a', tmp_path / 'project', source, 'classification', cancelled=lambda: len(reads) >= 5)
    assert index.revisions('ns:a') == []
    with dataset_index.sqlite3.connect(index.path) as db:
        assert db.execute('SELECT COUNT(*) FROM dataset_index_staging').fetchone()[0] == 0, 'no staging rows are left'
    reads.clear()
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    if os.name != 'nt':
        assert len(reads) <= 4, f'the retry reads only what the cancelled build had not stored ({len(reads)} reads)'
    assert receipt.image_count == 8


def test_an_unknown_task_and_an_unwritable_schema_are_refused(index, tmp_path):
    from backend.engine.dataset_index import DatasetIndex
    source = _classification_tree(tmp_path / 'source', per_class=1)
    with pytest.raises(ValueError, match='Unknown dataset task'):
        index.build_revision('ns:a', tmp_path / 'project', source, 'classificaton')
    with __import__('sqlite3').connect(index.path) as db:
        db.execute('PRAGMA user_version = 1')
    with pytest.raises(RuntimeError, match='schema 1'):
        DatasetIndex(index.path)


def test_the_manifest_does_not_depend_on_directory_listing_order(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source')
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True)
    real_walk = os.walk

    def reversed_walk(*args, **kwargs):  # another file system lists entries in another order
        for root, dirs, files in real_walk(*args, **kwargs):
            dirs.reverse()
            yield root, dirs, list(reversed(files))

    monkeypatch.setattr(dataset_index.os, 'walk', reversed_walk)
    again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True)
    assert again.manifest_sha256 == first.manifest_sha256


def test_a_link_to_a_folder_containing_the_source_is_caught_by_identity_not_spelling(index, tmp_path, monkeypatch):
    """A case-insensitive volume lets a link spell the parent differently (/x/DATASETS for /x/datasets); emulated by
    making the string comparison miss, so only the device/inode check can catch it."""
    from backend.engine import dataset_index
    parent = tmp_path / 'datasets'
    source = _classification_tree(parent / 'line-a', per_class=1)
    sibling = parent / 'line-b' / 'train' / 'ok'
    sibling.mkdir(parents=True)
    Image.new('RGB', (20, 18), 'red').save(sibling / 'other.png')
    _link(source / 'train' / 'up', parent)
    monkeypatch.setattr(dataset_index, '_contains', lambda parent_path, child: False)  # spelling never matches
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', follow_links=True)
    assert all('line-b' not in path for path in _paths(index, receipt)) and receipt.skipped_links == 1


def test_a_transient_read_error_is_never_cached(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=1)
    flaky = source / 'train' / 'ok' / '0000.png'
    real_open = Path.open
    failures = []

    def share_hiccup(self, *args, **kwargs):
        if self == flaky and not failures:
            failures.append(1)
            raise OSError(5, 'Input/output error')
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', share_hiccup)
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    codes = [row['error_code'] for row in index.page('ns:a', first.revision_id, valid=False)['items']]
    assert codes == ['READ_ERROR'] and first.state == 'rejected'
    again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert again.state == 'prepared', 'the file is read again, not remembered as broken'


def test_a_build_whose_staged_rows_were_removed_records_no_revision(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    monkeypatch.setattr(dataset_index, '_BATCH', 2)
    source = _classification_tree(tmp_path / 'source', per_class=3)

    def other_build_cleans_up():  # a long build judged stale by another build's clock
        with dataset_index.sqlite3.connect(index.path) as db:
            db.execute('DELETE FROM dataset_index_staging')

    with pytest.raises(RuntimeError, match='lost staged rows'):
        index.build_revision('ns:a', tmp_path / 'project', source, 'classification', before_seal=other_build_cleans_up)
    assert index.revisions('ns:a') == []


def test_two_attempts_sealing_one_publication_key_share_the_first_revision(index, tmp_path):
    source = _classification_tree(tmp_path / 'source', per_class=1)
    second = []

    def another_attempt_seals_first():
        if not second:
            second.append(index.build_revision('ns:a', tmp_path / 'project', source, 'classification', publication_key='job-9'))

    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', publication_key='job-9',
                                 before_seal=another_attempt_seals_first)
    assert first == second[0] and len(index.revisions('ns:a')) == 1
    with dataset_index_db(index) as db:
        assert db.execute('SELECT COUNT(*) FROM dataset_index_staging').fetchone()[0] == 0


def dataset_index_db(index):
    import sqlite3
    return sqlite3.connect(index.path)


# --- Freeze-3 review: pin the behaviour the surviving mutants left open -----------------------------------
def test_staging_is_cleaned_by_heartbeat_not_by_start_time(index, tmp_path):
    import sqlite3
    import time as _time
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=1)
    day = dataset_index._STALE_BUILD_NS
    now = _time.time_ns()
    with sqlite3.connect(index.path) as db:
        db.execute('INSERT INTO dataset_builds VALUES(?, ?, ?, ?)', ('crashed', 'ns:a', now - 3 * day, now - 2 * day))
        db.execute('INSERT INTO dataset_builds VALUES(?, ?, ?, ?)', ('long-alive', 'ns:a', now - 3 * day, now))
        for build in ('crashed', 'long-alive'):
            db.execute('INSERT INTO dataset_index_staging VALUES(?, ?, ?, NULL, 0, NULL, NULL, NULL, NULL, 1, NULL, NULL, 0)',
                       (build, 'x.png', 'u'))
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    with sqlite3.connect(index.path) as db:
        builds = {row[0] for row in db.execute('SELECT build_id FROM dataset_builds')}
        staged = {row[0] for row in db.execute('SELECT build_id FROM dataset_index_staging')}
    assert builds == staged == {'long-alive'}, 'a build that keeps its heartbeat fresh keeps its rows, however old'


def test_cache_rows_are_pruned_for_missing_files_but_kept_for_another_scan_root(index, tmp_path, monkeypatch):
    import sqlite3
    from backend.engine import dataset_index
    source = tmp_path / 'multi'
    for folder in ('segmentation/images', 'classification/ok'):
        (source / folder).mkdir(parents=True)
        for number in range(2):
            Image.new('RGB', (16, 16), 'white').save(source / folder / f'{number}.png')
    index.build_revision('ns:a', tmp_path / 'project', source, 'segmentation')
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    (source / 'classification' / 'ok' / '1.png').unlink()
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    with sqlite3.connect(index.path) as db:
        cached = sorted(row[0] for row in db.execute('SELECT relative_path FROM dataset_stat_cache'))
    assert cached == ['classification/ok/0.png', 'segmentation/images/0.png', 'segmentation/images/1.png']


def test_a_cache_row_of_another_validator_version_is_read_again(index, tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('the stat cache is not trusted on Windows')
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=1)
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    reads = []
    real = dataset_index._read_entry
    monkeypatch.setattr(dataset_index, '_read_entry', lambda path: reads.append(path) or real(path))
    monkeypatch.setattr(dataset_index, 'VALIDATOR', 'decode-v2')
    index.build_revision('ns:a', tmp_path / 'project', source, 'classification')
    assert len(reads) == 2, 'results of an older validator are not reused'


def test_a_same_size_rewrite_during_the_read_is_caught(index, tmp_path, monkeypatch):
    if os.name == 'nt':
        pytest.skip('change detection relies on POSIX ctime')
    from backend.engine import dataset_index
    source = tmp_path / 'bmp'
    (source / 'ok').mkdir(parents=True)
    target = source / 'ok' / 'a.bmp'
    Image.new('RGB', (20, 18), 'white').save(target)
    real_open = Image.open

    def rewrite(handle, *args, **kwargs):
        stat = target.stat()
        Image.new('RGB', (20, 18), 'black').save(target)
        assert target.stat().st_size == stat.st_size
        os.utime(target, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return real_open(handle, *args, **kwargs)

    monkeypatch.setattr(dataset_index.Image, 'open', rewrite)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert [row['error_code'] for row in index.page('ns:a', receipt.revision_id, valid=False)['items']] == ['CHANGED_DURING_SCAN']


def test_a_dicom_decode_failure_is_never_cached(index, tmp_path, monkeypatch):
    from backend.engine import dicom_input, dataset_index
    source = tmp_path / 'scans'
    (source / 'ok').mkdir(parents=True)
    (source / 'ok' / 'scan.dcm').write_bytes(b'DICM' * 32)
    monkeypatch.setattr(dicom_input, 'is_dicom', lambda path: True)
    monkeypatch.setattr(dicom_input, 'read_dicom', lambda path: (_ for _ in ()).throw(ValueError('DICOM input requires optional pydicom')))
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert first.state == 'rejected'
    monkeypatch.setattr(dicom_input, 'read_dicom', lambda path: (Image.new('L', (16, 16)), {'source_sha256': 'f' * 64}))
    assert index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject').state == 'prepared', \
        'installing the decoder is enough; the failure was not cached'


def test_a_link_back_inside_the_source_spelled_differently_is_not_walked_twice(index, tmp_path):
    """Case-insensitive volume: a link to /source spelled /SOURCE reaches the same folder; only identity shows it."""
    source = _classification_tree(tmp_path / 'source', per_class=1)
    _link(source / 'train' / 'ok' / 'BACK', Path(str(source).replace('/source', '/SOURCE')))
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', follow_links=True)
    assert receipt.image_count == 2, _paths(index, receipt)


def test_a_volume_without_inode_numbers_still_walks_every_folder(index, tmp_path, monkeypatch):
    """Some virtual drives report inode 0 for everything (emulated): identity then falls back to the path."""
    from backend.engine import dataset_index
    source = _classification_tree(tmp_path / 'source', per_class=2)
    real_stat = os.stat

    class NoInode:
        def __init__(self, stat):
            self._stat = stat

        def __getattr__(self, name):
            return 0 if name == 'st_ino' else getattr(self._stat, name)

    monkeypatch.setattr(dataset_index.os, 'stat', lambda path, *args, **kwargs: NoInode(real_stat(path, *args, **kwargs)))
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', verify=True)
    assert receipt.image_count == 4

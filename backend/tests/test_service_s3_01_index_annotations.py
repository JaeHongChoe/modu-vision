"""S3-01 slice 4b: source annotations and duplicate groups recorded in every index revision (schema 3).

Synthetic images and label files in temporary folders; every source byte is hashed before and after.
"""
import hashlib
import json
import sqlite3

import pytest
from PIL import Image


@pytest.fixture
def index(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    return DatasetIndex(index_path(tmp_path / 'registry'))


def _image(path, color='white', size=(16, 16)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', size, color).save(path)
    return path


def _snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def _rows(index, receipt, **filters):
    return {row['relative_path']: row for row in index.page('ns:a', receipt.revision_id, limit=500, **filters)['items']}


def _detection_source(root):
    labelme = _image(root / 'lm' / 'a.png', 'red')
    labelme.with_suffix('.json').write_text(json.dumps({'shapes': [{'label': 'scratch'}, {'label': 'dent'}]}))
    _image(root / 'images' / 'b.png', 'green')
    (root / 'annotations.json').write_text(json.dumps({
        'images': [{'id': 1, 'file_name': 'images/b.png', 'width': 16, 'height': 16}],
        'categories': [{'id': 3, 'name': 'crack'}],
        'annotations': [{'id': 1, 'image_id': 1, 'category_id': 3, 'bbox': [1, 1, 4, 4]}]}))
    _image(root / 'yolo' / 'images' / 'c.png', 'blue')
    (root / 'yolo' / 'labels').mkdir(parents=True)
    (root / 'yolo' / 'labels' / 'c.txt').write_text('1 0.5 0.5 0.2 0.2\n')
    (root / 'yolo' / 'classes.txt').write_text('ok\nng\n')
    _image(root / 'plain' / 'd.png', 'black')
    return root


def test_each_revision_records_the_source_annotations_with_their_file_hashes(index, tmp_path):
    source = _detection_source(tmp_path / '검사 source')
    before = _snapshot(source)
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert _snapshot(source) == before, 'indexing never changes a source byte'
    assert (receipt.image_count, receipt.valid_count, receipt.annotated, receipt.annotation_errors) == (4, 4, 3, 0)
    rows = _rows(index, receipt)
    assert (rows['lm/a.png']['annotation_format'], rows['lm/a.png']['annotation_labels']) == ('labelme', ['dent', 'scratch'])
    assert rows['lm/a.png']['annotation_files'] == [
        {'path': 'lm/a.json', 'sha256': hashlib.sha256((source / 'lm' / 'a.json').read_bytes()).hexdigest()}]
    assert (rows['images/b.png']['annotation_format'], rows['images/b.png']['annotation_labels']) == ('coco', ['crack'])
    assert (rows['yolo/images/c.png']['annotation_format'], rows['yolo/images/c.png']['annotation_labels']) == ('yolo', ['ng'])
    assert [item['path'] for item in rows['yolo/images/c.png']['annotation_files']] == ['yolo/labels/c.txt', 'yolo/classes.txt']
    assert (rows['plain/d.png']['annotation_format'], rows['plain/d.png']['annotation_labels'],
            rows['plain/d.png']['annotation_files']) == (None, [], [])
    assert list(_rows(index, receipt, annotation_label='crack')) == ['images/b.png']
    listed = index.revisions('ns:a')[0]
    assert listed['annotations_scanned'] is True and listed['annotated'] == 3


def test_a_changed_label_file_changes_the_revision_while_unchanged_images_are_reused(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    monkeypatch.setattr(dataset_index, '_STAT_CACHE_TRUSTED', True)
    source = _detection_source(tmp_path / 'source')
    first = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    (source / 'lm' / 'a.json').write_text(json.dumps({'shapes': [{'label': 'dent'}]}))
    second = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert second.reused_entries == 4, 'image bytes are not read again'
    assert second.manifest_sha256 != first.manifest_sha256, 'the revision states which label bytes it saw'
    assert _rows(index, second)['lm/a.png']['annotation_labels'] == ['dent']
    assert _rows(index, first)['lm/a.png']['annotation_labels'] == ['dent', 'scratch'], 'older revisions never change'


@pytest.mark.parametrize('broken,code', [
    ('{"images": [', 'INVALID_ANNOTATION'),
    (json.dumps({'images': [{'id': 1, 'file_name': 'images/b.png'}, {'id': 2, 'file_name': 'images/b.png'}],
                 'categories': [], 'annotations': []}), 'AMBIGUOUS_ANNOTATION'),
])
def test_an_annotation_error_is_an_invalid_entry_under_the_chosen_policy(index, tmp_path, broken, code):
    from backend.engine.dataset_index import RevisionNotActivatable
    source = _detection_source(tmp_path / 'source')
    (source / 'annotations.json').write_text(broken)
    held = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'reject')
    assert held.state == 'rejected' and held.annotation_errors >= 1
    row = _rows(index, held)['images/b.png']
    assert (row['valid'], row['error_code'], row['annotation_error'].split(':')[0]) == (0, code, code)
    with pytest.raises(RevisionNotActivatable):
        index.activate('ns:a', held.revision_id, None)
    kept = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert kept.state == 'prepared' and _rows(index, kept, valid=False)['images/b.png']['error_code'] == code
    assert _rows(index, kept)['lm/a.png']['valid'] == 1, 'an adjacent LabelMe file binds its image before any COCO document'


def test_a_yolo_class_id_outside_the_list_is_explicit(index, tmp_path):
    source = _detection_source(tmp_path / 'source')
    (source / 'yolo' / 'labels' / 'c.txt').write_text('7 0.5 0.5 0.2 0.2\n')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    row = _rows(index, receipt)['yolo/images/c.png']
    assert (row['valid'], row['error_code']) == (0, 'INVALID_ANNOTATION') and 'outside the class list' in row['error_detail']


def test_duplicates_are_grouped_with_conflicting_labels_and_split_leakage(index, tmp_path):
    source = tmp_path / 'source'
    for path in ('train/ok/a.png', 'train/ok/a_copy.png', 'train/ng/a_again.png'):  # same bytes, two labels
        _image(source / path, 'white')
    _image(source / 'train/ok/b.png', 'gray')
    _image(source / 'test/ok/b.png', 'gray')  # same bytes in train and test
    _image(source / 'train/ok/c.png', 'blue')
    (source / 'train/ok/broken.png').write_bytes((source / 'train/ok/c.png').read_bytes()[:40])
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    assert (receipt.duplicate_groups, receipt.duplicate_images, receipt.conflicting_duplicates,
            receipt.cross_split_duplicates) == (2, 5, 1, 1)
    groups = index.duplicates('ns:a', receipt.revision_id)['groups']
    by_size = {group['members']: group for group in groups}
    assert [item['relative_path'] for item in by_size[3]['items']] == ['train/ng/a_again.png', 'train/ok/a.png', 'train/ok/a_copy.png']
    assert (by_size[3]['conflicting'], by_size[3]['cross_split']) == (True, False)
    assert (by_size[2]['conflicting'], by_size[2]['cross_split']) == (False, True)
    assert [g['members'] for g in index.duplicates('ns:a', receipt.revision_id, kind='conflicting')['groups']] == [3]
    assert [g['members'] for g in index.duplicates('ns:a', receipt.revision_id, kind='cross_split')['groups']] == [2]
    listed = index.revisions('ns:a')[0]
    assert (listed['duplicate_groups'], listed['conflicting_duplicates'], listed['cross_split_duplicates']) == (2, 1, 1)


def test_duplicate_groups_page_by_digest_and_the_cursor_is_bound(index, tmp_path):
    source = tmp_path / 'source'
    for number in range(5):
        for copy in range(3):
            _image(source / 'ok' / f'{number}_{copy}.png', (number * 40, 0, 0))
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    seen, cursor = [], None
    while True:
        page = index.duplicates('ns:a', receipt.revision_id, cursor=cursor, limit=2)
        seen += [group['sha256'] for group in page['groups']]
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert len(seen) == 5 and seen == sorted(seen)
    first = index.duplicates('ns:a', receipt.revision_id, limit=2)['next_cursor']
    with pytest.raises(ValueError):
        index.duplicates('ns:a', receipt.revision_id, cursor=first, kind='conflicting')
    with pytest.raises(KeyError):
        index.duplicates('ns:b', receipt.revision_id)


def test_a_revision_without_a_valid_image_is_never_active(index, tmp_path):
    from backend.engine.dataset_index import RevisionNotActivatable
    source = tmp_path / 'source' / 'ok'
    source.mkdir(parents=True)
    (source / 'a.png').write_bytes(b'not an image')
    receipt = index.build_revision('ns:a', tmp_path / 'project', source.parent, 'classification', 'exclude')
    assert (receipt.state, receipt.image_count, receipt.valid_count) == ('prepared', 1, 0)
    with pytest.raises(RevisionNotActivatable, match='No image'):
        index.activate('ns:a', receipt.revision_id, None)


def test_a_schema_2_index_is_upgraded_in_place_and_keeps_its_revisions(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    path = index_path(tmp_path / 'registry')
    source = _detection_source(tmp_path / 'source')
    old = DatasetIndex(path).build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    with sqlite3.connect(path) as db:  # the schema-2 layout: the same tables without the schema-3 additions
        db.executescript('DROP TABLE dataset_revision_details; DROP TABLE dataset_index_annotations; '
                         'DROP TABLE dataset_annotation_staging; DROP INDEX dataset_index_digest; PRAGMA user_version = 2;')
    upgraded = DatasetIndex(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 3
    listed = upgraded.revisions('ns:a')
    assert len(listed) == 1 and listed[0]['annotations_scanned'] is False and listed[0]['manifest_sha256'] == old.manifest_sha256
    assert _rows(upgraded, old)['lm/a.png']['annotation_labels'] == [], 'an older revision states no annotations it never read'
    assert upgraded.duplicates('ns:a', old.revision_id)['groups'] == []
    fresh = upgraded.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert fresh.annotated == 3
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version = 4')
    with pytest.raises(RuntimeError, match='schema 4'):
        DatasetIndex(path)


def test_a_cancelled_build_leaves_no_staged_annotation_rows(index, tmp_path):
    source = _detection_source(tmp_path / 'source')
    calls = []

    def cancelled():
        calls.append(1)
        return len(calls) > 3

    with pytest.raises(InterruptedError):
        index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude', cancelled=cancelled)
    with sqlite3.connect(index.path) as db:
        assert db.execute('SELECT COUNT(*) FROM dataset_annotation_staging').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM dataset_index_staging').fetchone()[0] == 0
    assert index.revisions('ns:a') == []


def test_a_stray_annotation_file_never_excludes_images_of_a_folder_labelled_task(index, tmp_path, monkeypatch):
    from backend.engine import dataset_annotations
    source = tmp_path / 'source'
    for number in range(6):
        _image(source / 'ok' / f'{number}.png', (number * 30, 0, 0))
    broken = {'images': [{'id': 1, 'file_name': 'ok\\0.png'}], 'categories': [], 'annotations': []}  # a backslash name
    (source / 'annotations.json').write_text(json.dumps(broken))
    loads = []
    real_load = dataset_annotations.SourceAnnotationScanner._load
    monkeypatch.setattr(dataset_annotations.SourceAnnotationScanner, '_load',
                        lambda self, path: loads.append(path.name) or real_load(self, path))
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'reject')
    assert (receipt.state, receipt.valid_count, receipt.annotation_errors, receipt.annotations_bind) == ('prepared', 6, 6, False)
    assert loads.count('annotations.json') == 1, 'a broken document is parsed once per build, not once per image'
    assert index.activate('ns:a', receipt.revision_id, None) == receipt.revision_id
    row = _rows(index, receipt)['ok/0.png']
    assert row['valid'] == 1 and row['error_code'] is None and row['annotation_error'].startswith('INVALID_ANNOTATION')
    held = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'reject')
    assert (held.state, held.valid_count, held.annotations_bind) == ('rejected', 0, True), 'detection training reads that file'


def test_a_bom_labelme_file_and_a_differently_cased_image_path_bind(index, tmp_path):
    source = tmp_path / 'source'
    image = _image(source / 'lm' / 'Part.PNG')
    image.with_suffix('.json').write_bytes(b'\xef\xbb\xbf' + json.dumps({'imagePath': 'part.png', 'shapes': [{'label': 'dent'}]}).encode())
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert _rows(index, receipt)['lm/Part.PNG']['annotation_labels'] == ['dent']


def test_a_receipt_sealed_before_schema_3_reports_unknown_not_zero(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    path = index_path(tmp_path / 'registry')
    source = _detection_source(tmp_path / 'source')
    DatasetIndex(path).build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude', publication_key='job-1')
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM dataset_revision_details')  # what a schema-2 revision looks like after the upgrade
    replay = DatasetIndex(path).build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude', publication_key='job-1')
    assert (replay.annotated, replay.duplicate_groups, replay.annotations_bind) == (None, None, None)


def test_a_studio_overlay_keeps_an_image_whose_source_label_is_broken(index, tmp_path):
    from backend.engine.annotation_storage import dataset_annotation_dir
    source = _detection_source(tmp_path / 'source')
    (source / 'annotations.json').write_text('{"images": [')  # breaks the COCO binding of images/b.png
    overlays = tmp_path / 'project' / 'annotations'
    studio = dataset_annotation_dir(source / 'images', overlays, use_scope=False) / 'b.json'
    studio.parent.mkdir(parents=True)
    studio.write_text(json.dumps({'annotations': [{'type': 'bbox', 'label': 'crack', 'bbox': [1, 1, 4, 4]}]}))
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude', overlay_root=overlays)
    rows = _rows(index, receipt)
    assert rows['images/b.png']['valid'] == 1 and rows['images/b.png']['annotation_error'].startswith('INVALID_ANNOTATION')
    # the unreadable root document may bind every image without a LabelMe file: each reports it, only the overlaid one stays
    assert set(_rows(index, receipt, annotation_error=True)) == {'images/b.png', 'plain/d.png', 'yolo/images/c.png'}
    assert (rows['plain/d.png']['valid'], rows['yolo/images/c.png']['valid']) == (0, 0)
    assert list(_rows(index, receipt, annotation_error=False)) == ['lm/a.png']
    plain = index.build_revision('ns:a', tmp_path / 'project', source, 'detection', 'exclude')
    assert _rows(index, plain)['images/b.png']['valid'] == 0, 'without the overlay the broken source label excludes it'


def test_an_index_written_before_annotations_bind_existed_is_upgraded(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    path = index_path(tmp_path / 'registry')
    DatasetIndex(path)
    with sqlite3.connect(path) as db:
        db.executescript('DROP TABLE dataset_revision_details; CREATE TABLE dataset_revision_details(revision_id TEXT PRIMARY KEY, '
                         'annotated INTEGER NOT NULL, annotation_errors INTEGER NOT NULL, duplicate_groups INTEGER NOT NULL, '
                         'duplicate_images INTEGER NOT NULL, conflicting_duplicates INTEGER NOT NULL, cross_split_duplicates INTEGER NOT NULL);')
    reopened = DatasetIndex(path)
    receipt = reopened.build_revision('ns:a', tmp_path / 'project', _detection_source(tmp_path / 'source'), 'detection', 'exclude')
    assert receipt.annotations_bind is True and reopened.revisions('ns:a')[0]['annotations_bind'] == 1

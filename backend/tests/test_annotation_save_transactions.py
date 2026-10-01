"""Failed saves preserve the committed label pair and its review ledger."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_annotation as ra
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)


@pytest.fixture
def saved_overlay(tmp_path):
    root = tmp_path.resolve() / 'project'; root.mkdir()
    source = tmp_path.resolve() / 'source'; source.mkdir()
    Image.new('RGB', (40, 30)).save(source / 'a.png')
    (root / 'project.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    annotation = set_request_annotation_root(root / 'annotations')
    project = set_request_project_root(root)
    def request(points=None, **extra):
        labels = ([{'type': 'polygon', 'label': 'NG', 'category_id': 1,
                    'polygon': points or [[1, 1], [10, 1], [10, 10]]}]
                  if points is not False else [{'type': 'tag', 'label': 'OK', 'category_id': 0, 'is_normal': True}])
        return ra.AnnotationSaveRequest(image_id='a', image_path=str(source / 'a.png'),
            image_width=40, image_height=30, annotations=labels, **extra)
    try:
        row = ra.save_annotations(request())['metadata']
        overlay = dataset_annotation_dir(source, root / 'annotations')
        paths = {'json': overlay / 'a.json', 'png': overlay / 'masks' / 'a.png',
                 'ledger': dm.ledger_path(root, source)}
        yield root, source, request, row, paths, {key: path.read_bytes() for key, path in paths.items()}
    finally:
        reset_request_project_root(project); reset_request_annotation_root(annotation)


def assert_preserved(paths, before):
    for key, path in paths.items():
        assert path.read_bytes() == before[key], f'{key} changed after failed save'
    assert not list(paths['json'].parent.rglob('.annotation-atomic-*'))


def fail_publication_once(monkeypatch, destination):
    replace = dm.os.replace
    failed = False
    def failing(source, target):
        nonlocal failed
        if str(target) == str(destination) and not failed:
            failed = True
            raise OSError('fixture: publication failed')
        return replace(source, target)
    monkeypatch.setattr(dm.os, 'replace', failing)


def test_invalid_palette_preserves_json_png_and_ledger(saved_overlay):
    _, _, request, row, paths, before = saved_overlay
    with pytest.raises(HTTPException) as error:
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]],
            expected_revision=row['revision'], mask_classes=[]))
    assert error.value.status_code == 422
    assert_preserved(paths, before)


def test_json_publication_failure_restores_prior_pair(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    fail_publication_once(monkeypatch, paths['json'])
    with pytest.raises((HTTPException, OSError)):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_mask_publication_failure_preserves_prior_pair(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    fail_publication_once(monkeypatch, paths['png'])
    with pytest.raises(HTTPException):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_staging_fsync_failure_preserves_committed_files(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    def failed_sync(descriptor):
        raise OSError('fixture: staging disk write failed')
    monkeypatch.setattr(dm.os, 'fsync', failed_sync)
    with pytest.raises(HTTPException):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_mask_encoding_failure_never_publishes_annotations(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    monkeypatch.setattr(ra.cv2, 'imencode', lambda *args: (False, None))
    with pytest.raises(HTTPException):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_json_encoding_failure_never_publishes_mask(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    encode = json.dumps
    def fail_annotation_encoding(value, **kwargs):
        if kwargs.get('allow_nan') is False:
            raise ValueError('fixture: annotation encoding failed')
        return encode(value, **kwargs)
    monkeypatch.setattr(json, 'dumps', fail_annotation_encoding)
    with pytest.raises(HTTPException) as error:
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert error.value.status_code == 422
    assert_preserved(paths, before)


def test_ledger_publication_failure_restores_prior_pair(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    fail_publication_once(monkeypatch, paths['ledger'])
    with pytest.raises((HTTPException, OSError)):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_failed_restoration_retains_original_backup_for_recovery(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    replace = dm.os.replace
    ledger_failed = False
    def failed_restore(origin, target):
        nonlocal ledger_failed
        if str(target) == str(paths['ledger']):
            ledger_failed = True
            raise OSError('fixture: ledger publication failed')
        if ledger_failed and str(target) == str(paths['png']):
            raise PermissionError('fixture: mask cannot be restored')
        return replace(origin, target)
    monkeypatch.setattr(dm.os, 'replace', failed_restore)
    with pytest.raises(RuntimeError, match='rollback failed'):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
    assert paths['ledger'].read_bytes() == before['ledger']
    assert paths['json'].read_bytes() == before['json']
    backups = list(paths['json'].parent.rglob('.annotation-atomic-*'))
    assert backups and any(path.read_bytes() == before['png'] for path in backups)


def test_deleted_mask_is_restored_when_ledger_commit_fails(saved_overlay, monkeypatch):
    _, _, request, row, paths, before = saved_overlay
    fail_publication_once(monkeypatch, paths['ledger'])
    with pytest.raises((HTTPException, OSError)):
        ra.save_annotations(request(False, expected_revision=row['revision']))
    assert_preserved(paths, before)


def test_outer_failure_restores_earliest_nested_originals(saved_overlay):
    root, source, request, row, paths, before = saved_overlay
    with pytest.raises(RuntimeError, match='outer failure'):
        with dm.metadata_transaction(root, source):
            first = ra.save_annotations(request([[15, 1], [25, 1], [25, 10]], expected_revision=row['revision']))
            # Nested consumers see committed bytes immediately, including fingerprints.
            assert json.loads(paths['json'].read_text())['annotations'][0]['polygon'][0] == [15, 1]
            ra.save_annotations(request(False, expected_revision=first['metadata']['revision']))
            raise RuntimeError('outer failure')
    assert_preserved(paths, before)


def test_failed_outer_transaction_removes_new_image_artifacts(saved_overlay):
    root, source, request, row, paths, before = saved_overlay
    Image.new('RGB', (40, 30)).save(source / 'b.png')
    second = request().model_copy(update={'image_id': 'b', 'image_path': str(source / 'b.png')})
    with pytest.raises(RuntimeError, match='outer failure'):
        with dm.metadata_transaction(root, source):
            ra.save_annotations(request([[15, 1], [25, 1], [25, 10]], expected_revision=row['revision']))
            ra.save_annotations(second)
            raise RuntimeError('outer failure')
    assert not (paths['json'].parent / 'b.json').exists()
    assert not (paths['png'].parent / 'b.png').exists()
    assert_preserved(paths, before)


def test_caught_nested_failure_cannot_commit_partial_ledger(saved_overlay, monkeypatch):
    root, source, request, row, paths, before = saved_overlay
    with pytest.raises(RuntimeError, match='transaction'):
        with dm.metadata_transaction(root, source):
            first = ra.save_annotations(request([[15, 1], [25, 1], [25, 10]], expected_revision=row['revision']))
            fail_publication_once(monkeypatch, paths['json'])
            try:
                ra.save_annotations(request(False, expected_revision=first['metadata']['revision']))
            except (HTTPException, OSError):
                pass
    assert_preserved(paths, before)


def test_successful_mask_removal_cleans_backups_and_updates_ledger(saved_overlay):
    _, _, request, row, paths, _ = saved_overlay
    saved = ra.save_annotations(request(False, expected_revision=row['revision']))
    assert not paths['png'].exists()
    assert json.loads(paths['json'].read_text())['mask_file'] is None
    assert saved['metadata']['mask_hash'] is None
    assert saved['metadata']['revision'] == row['revision'] + 1
    assert not list(paths['json'].parent.rglob('.annotation-atomic-*'))


def test_legacy_save_rolls_back_json_publication_failure(tmp_path, monkeypatch):
    output = tmp_path / 'legacy'
    monkeypatch.setenv('VISION_AI_STUDIO_ANNOTATION_ROOTS', str(output))
    first = ra.AnnotationSaveRequest(image_id='a', output_dir=str(output), image_width=40, image_height=30,
        annotations=[{'type': 'polygon', 'label': 'NG', 'polygon': [[1, 1], [10, 1], [10, 10]]}])
    ra.save_annotations(first)
    paths = {'json': output / 'a.json', 'png': output / 'masks' / 'a.png'}
    before = {key: path.read_bytes() for key, path in paths.items()}
    fail_publication_once(monkeypatch, paths['json'])
    with pytest.raises((HTTPException, OSError)):
        ra.save_annotations(first.model_copy(update={'annotations': []}))
    assert_preserved(paths, before)


def test_waiting_writer_sees_original_after_failed_commit(saved_overlay, monkeypatch):
    root, source, request, row, paths, _ = saved_overlay
    failing_commit = Event(); waiting_writer = Event()
    replace = dm.os.replace
    failed = False
    def fail_ledger_once(origin, target):
        nonlocal failed
        if str(target) == str(paths['ledger']) and not failed:
            failed = True; failing_commit.set()
            assert waiting_writer.wait(5)
            raise OSError('fixture: ledger publication failed')
        return replace(origin, target)
    monkeypatch.setattr(dm.os, 'replace', fail_ledger_once)
    def write(points, wait=False):
        annotation = set_request_annotation_root(root / 'annotations'); project = set_request_project_root(root)
        try:
            if wait:
                assert failing_commit.wait(5); waiting_writer.set()
            try:
                return ra.save_annotations(request(points, expected_revision=row['revision']))
            except (HTTPException, OSError) as error:
                return error
        finally:
            reset_request_project_root(project); reset_request_annotation_root(annotation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        failing = pool.submit(write, [[20, 15], [30, 15], [30, 25]])
        waiting = pool.submit(write, [[2, 15], [12, 15], [12, 25]], True)
        assert isinstance(failing.result(timeout=10), OSError)
        result = waiting.result(timeout=10)
    assert isinstance(result, dict), f'waiting writer rejected: {result}'
    assert result['metadata']['revision'] == row['revision'] + 1
    assert json.loads(paths['json'].read_text())['annotations'][0]['polygon'][0] == [2, 15]


def test_nested_fingerprint_and_version_inventory_ignore_private_backups(saved_overlay):
    root, source, request, row, paths, _ = saved_overlay
    from backend.api.routes_dataset_versions import _iter_files
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    with dm.metadata_transaction(root, source):
        ra.save_annotations(request([[20, 15], [30, 15], [30, 25]], expected_revision=row['revision']))
        private = list(paths['json'].parent.rglob('.annotation-atomic-*'))
        assert private and all(path.suffix == '' for path in private)
        inventory = list(_iter_files(paths['json'].parent, source=False, project_dir=root))
        assert not any(path in inventory for path in private)
        fingerprint = fingerprint_dataset(source, studio_root=root / 'annotations')
    # Removing staged backups at commit must not change the accepted fingerprint.
    assert fingerprint_dataset(source, studio_root=root / 'annotations') == fingerprint
    assert not list(paths['json'].parent.rglob('.annotation-atomic-*'))


def test_legacy_failed_writer_cannot_undo_waiting_success(tmp_path, monkeypatch):
    output = tmp_path.resolve() / 'legacy'
    monkeypatch.setenv('VISION_AI_STUDIO_ANNOTATION_ROOTS', str(output))
    def request(points):
        return ra.AnnotationSaveRequest(image_id='a', output_dir=str(output), image_width=40, image_height=30,
            annotations=[{'type': 'polygon', 'label': 'NG', 'polygon': points}])
    ra.save_annotations(request([[1, 1], [10, 1], [10, 10]]))
    replacing = Event(); second_started = Event()
    replace = dm.os.replace
    failed = False
    def fail_json_once(origin, target):
        nonlocal failed
        if str(target) == str(output / 'a.json') and not failed:
            failed = True; replacing.set()
            assert second_started.wait(5)
            raise OSError('fixture: legacy publication failed')
        return replace(origin, target)
    monkeypatch.setattr(dm.os, 'replace', fail_json_once)
    def write(points, wait=False):
        if wait:
            assert replacing.wait(5); second_started.set()
        try:
            return ra.save_annotations(request(points))
        except HTTPException as error:
            return error
    with ThreadPoolExecutor(max_workers=2) as pool:
        failing = pool.submit(write, [[20, 15], [30, 15], [30, 25]])
        waiting = pool.submit(write, [[2, 15], [12, 15], [12, 25]], True)
        assert isinstance(failing.result(timeout=10), HTTPException)
        assert isinstance(waiting.result(timeout=10), dict)
    assert json.loads((output / 'a.json').read_text())['annotations'][0]['polygon'][0] == [2, 15]
    assert not list(output.rglob('.annotation-atomic-*'))

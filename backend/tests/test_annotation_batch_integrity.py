"""Batch failures disclose committed saves or roll back one project label set."""
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event, local

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_annotation as ra
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)


def polygon(image_id, **extra):
    return ra.AnnotationSaveRequest(image_id=image_id, image_width=40, image_height=30,
        annotations=[{'type': 'polygon', 'label': 'NG', 'category_id': 1,
                      'polygon': [[1, 1], [10, 1], [10, 10]]}], **extra)


@pytest.fixture
def project_batch(tmp_path):
    project = tmp_path.resolve() / 'project'; project.mkdir()
    source = tmp_path.resolve() / 'source'; source.mkdir()
    for name in ('a', 'b'):
        Image.new('RGB', (40, 30)).save(source / f'{name}.png')
    (project / 'project.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    annotation_scope = set_request_annotation_root(project / 'annotations')
    project_scope = set_request_project_root(project)
    try:
        rows = {name: ra.save_annotations(polygon(name, image_path=str(source / f'{name}.png')))['metadata']
                for name in ('a', 'b')}
        overlay = dataset_annotation_dir(source, project / 'annotations')
        paths = [dm.ledger_path(project, source)]
        for name in ('a', 'b'):
            paths.extend([overlay / f'{name}.json', overlay / 'masks' / f'{name}.png'])
        before = {path: path.read_bytes() for path in paths}
        items = [polygon(name, image_path=str(source / f'{name}.png'), expected_revision=rows[name]['revision'])
                 for name in ('a', 'b')]
        items[0] = items[0].model_copy(update={'annotations': [ra.AnnotationItem(type='polygon', label='NG',
            polygon=[[20, 15], [30, 15], [30, 25]])]})
        yield project, source, overlay, rows, before, items
    finally:
        reset_request_project_root(project_scope)
        reset_request_annotation_root(annotation_scope)


@pytest.mark.parametrize('failure', ['publication', 'palette', 'revision'])
def test_project_batch_late_failure_restores_both_label_pairs_and_ledger(project_batch, monkeypatch, failure):
    _, _, overlay, _, before, items = project_batch
    status = {'publication': 500, 'palette': 422, 'revision': 409}[failure]
    if failure == 'publication':
        replace = dm.os.replace
        failed = False
        def fail_second_json(origin, target):
            nonlocal failed
            if str(target) == str(overlay / 'b.json') and not failed:
                failed = True
                raise OSError('fixture: second image publication failed')
            return replace(origin, target)
        monkeypatch.setattr(dm.os, 'replace', fail_second_json)
    elif failure == 'palette':
        items[1] = items[1].model_copy(update={'mask_classes': []})
    else:
        items[1] = items[1].model_copy(update={'expected_revision': items[1].expected_revision - 1})
    with pytest.raises(HTTPException) as error:
        ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=items))
    assert error.value.status_code == status
    for path, original in before.items():
        assert path.read_bytes() == original, f'{path.name} survived a failed project batch'
    assert not list(overlay.rglob('.annotation-atomic-*'))


def test_project_batch_success_commits_each_revision(project_batch):
    project, source, overlay, rows, _, items = project_batch
    result = ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=items))
    assert result['saved_images_count'] == 2
    ledger = json.loads(dm.ledger_path(project, source).read_text())
    for name, saved in zip(('a', 'b'), result['results']):
        assert saved['metadata']['revision'] == rows[name]['revision'] + 1
        assert ledger['images'][f'{name}.png']['revision'] == saved['metadata']['revision']
    assert json.loads((overlay / 'a.json').read_text())['annotations'][0]['polygon'][0] == [20, 15]


@pytest.mark.parametrize('failed_index', [0, 1])
def test_legacy_batch_failure_discloses_saved_items_and_preserves_original_cause(tmp_path, monkeypatch, failed_index):
    output = tmp_path.resolve() / 'legacy'
    monkeypatch.setenv('VISION_AI_STUDIO_ANNOTATION_ROOTS', str(output))
    items = [polygon(name, output_dir=str(output)) for name in ('a', 'b', 'c')]
    items[failed_index] = items[failed_index].model_copy(update={'mask_classes': []})
    with pytest.raises(HTTPException) as error:
        ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=items))
    assert error.value.status_code == 422
    detail = error.value.detail
    assert isinstance(detail, dict), 'Failure response omits partial-save information'
    assert detail['saved_images_count'] == failed_index
    assert [row['image_id'] for row in detail['results']] == ([] if failed_index == 0 else ['a'])
    assert detail['failed_image_id'] == ('a', 'b')[failed_index]
    assert isinstance(error.value.__cause__, HTTPException)
    assert detail['cause'] == error.value.__cause__.detail
    assert not (output / 'c.json').exists()
    if failed_index:
        assert (output / 'a.json').is_file() and (output / 'masks' / 'a.png').is_file()


def test_mixed_project_and_legacy_batch_discloses_the_committed_project_save(project_batch):
    project, source, overlay, rows, _, items = project_batch
    items[1] = polygon('legacy', output_dir=str(project / 'annotations'), mask_classes=[])
    with pytest.raises(HTTPException) as error:
        ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=items))
    assert error.value.status_code == 422
    assert isinstance(error.value.detail, dict), 'Mixed batch failure omits the committed project save'
    assert error.value.detail['saved_images_count'] == 1
    assert error.value.detail['failed_image_id'] == 'legacy'
    assert error.value.detail['results'][0]['metadata']['revision'] == rows['a']['revision'] + 1
    assert json.loads((overlay / 'a.json').read_text())['annotations'][0]['polygon'][0] == [20, 15]
    ledger = json.loads(dm.ledger_path(project, source).read_text())
    assert ledger['images']['a.png']['revision'] == rows['a']['revision'] + 1


@pytest.mark.parametrize('rollback_failure', [False, True])
def test_mixed_batch_ledger_failure_discloses_committed_legacy_save(project_batch, monkeypatch, rollback_failure):
    project, source, overlay, _, before, items = project_batch
    legacy = polygon('legacy', output_dir=str(project / 'annotations'))
    ledger_path = dm.ledger_path(project, source)
    replace = dm.os.replace
    ledger_failed = False

    def fail_project_commit(origin, target):
        nonlocal ledger_failed
        if str(target) == str(ledger_path):
            ledger_failed = True
            raise OSError('fixture: project ledger commit failed')
        if rollback_failure and ledger_failed and str(target) == str(overlay / 'a.json'):
            raise OSError('fixture: original JSON restore failed')
        return replace(origin, target)

    monkeypatch.setattr(dm.os, 'replace', fail_project_commit)
    with pytest.raises(HTTPException) as error:
        ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=[legacy, *items]))

    assert ledger_failed, 'The regression must fail at the actual ledger publication'
    assert error.value.status_code == 500
    detail = error.value.detail
    assert detail['saved_images_count'] == 1
    assert [row['image_id'] for row in detail['results']] == ['legacy']
    assert detail['failed_image_id'] == 'a'
    assert detail['failed_index'] == 1
    assert isinstance(error.value.__cause__, RuntimeError if rollback_failure else OSError)
    assert detail['cause'] == str(error.value.__cause__)
    if rollback_failure:
        assert 'original JSON restore failed' in detail['cause']
        assert isinstance(error.value.__cause__.__cause__, OSError)
        assert 'project ledger commit failed' in str(error.value.__cause__.__cause__)
        assert list(overlay.rglob('.annotation-atomic-*')), 'Failed restoration must retain recovery files'
    else:
        assert 'project ledger commit failed' in detail['cause']
        assert (overlay / 'a.json').read_bytes() == before[overlay / 'a.json']
        assert not list(overlay.rglob('.annotation-atomic-*'))
    assert ledger_path.read_bytes() == before[ledger_path]
    assert (overlay / 'masks' / 'a.png').read_bytes() == before[overlay / 'masks' / 'a.png']
    assert (overlay / 'b.json').read_bytes() == before[overlay / 'b.json']
    assert (project / 'annotations' / 'legacy.json').is_file()
    assert (project / 'annotations' / 'masks' / 'legacy.png').is_file()


def test_atomic_project_batch_ledger_failure_restores_all_items(project_batch, monkeypatch):
    project, source, overlay, _, before, items = project_batch
    ledger_path = dm.ledger_path(project, source)
    replace = dm.os.replace

    def fail_ledger(origin, target):
        if str(target) == str(ledger_path):
            raise OSError('fixture: atomic ledger commit failed')
        return replace(origin, target)

    monkeypatch.setattr(dm.os, 'replace', fail_ledger)
    with pytest.raises(OSError, match='atomic ledger commit failed'):
        ra.batch_save_annotations(ra.AnnotationBatchSaveRequest(items=items))
    for path, original in before.items():
        assert path.read_bytes() == original
    assert not list(overlay.rglob('.annotation-atomic-*'))


def test_legacy_palette_read_is_serialized_with_publication(tmp_path, monkeypatch):
    output = tmp_path.resolve() / 'legacy'
    monkeypatch.setenv('VISION_AI_STUDIO_ANNOTATION_ROOTS', str(output))
    palette = [{'id': 0, 'name': 'background', 'color': '#000000'},
               {'id': 2, 'name': 'unused_old', 'color': '#ff0000'}]
    initial = polygon('a', output_dir=str(output), mask_classes=palette)
    ra.save_annotations(initial)
    updated_palette = [palette[0], {'id': 2, 'name': 'unused_new', 'color': '#00ff00'}]
    first = initial.model_copy(update={'mask_classes': updated_palette})
    second = initial.model_copy(update={'mask_classes': None})
    identity = local()
    first_publishing = Event(); second_lock_attempt = Event()
    lock = dm._file_lock
    @contextmanager
    def observed_lock(path):
        if getattr(identity, 'writer', None) == 'second':
            second_lock_attempt.set()
        with lock(path):
            yield
    monkeypatch.setattr(dm, '_file_lock', observed_lock)
    replace = dm.os.replace
    def hold_first_json(origin, target):
        if getattr(identity, 'writer', None) == 'first' and str(target) == str(output / 'a.json'):
            first_publishing.set()
            assert second_lock_attempt.wait(5)
        return replace(origin, target)
    monkeypatch.setattr(dm.os, 'replace', hold_first_json)
    def write(name, request):
        identity.writer = name
        if name == 'second':
            assert first_publishing.wait(5)
        return ra.save_annotations(request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(write, 'first', first)
        two = pool.submit(write, 'second', second)
        assert one.result(timeout=10)['status'] == 'saved'
        assert two.result(timeout=10)['status'] == 'saved'
    saved = json.loads((output / 'a.json').read_text())
    unused = next(row for row in saved['mask_classes'] if row['id'] == 2)
    assert unused == {'id': 2, 'name': 'unused_new', 'color': '#00ff00'}
    assert not list(output.rglob('.annotation-atomic-*'))

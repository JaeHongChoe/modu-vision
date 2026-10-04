"""Whether a validated revision still matches its source, by the index's own inventory definition (native QA follow-up,
review nqa2 P2-1): the data step compared a revision's counts with the quick import's, which count differently (a
macOS ._ file, a nested class folder, an image at a classification root, an image a COCO file does not list), so a
fresh validation of an unchanged source was called different. Synthetic images in temporary folders; nothing is
decoded by the check.
"""
import json
import os
import time
from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def index(tmp_path):
    from backend.engine.dataset_index import DatasetIndex, index_path
    return DatasetIndex(index_path(tmp_path / 'registry'))


def _image(path, color='white'):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (16, 16), color).save(path)


def _classification(root):
    for label, color in (('ok', 'white'), ('ng', 'black')):
        for number in range(3):
            _image(root / label / f'{number}.png', color)
    return root


def _status(index, tmp_path, source, task):
    receipt = index.build_revision('ns:a', tmp_path / 'project', source, task, 'exclude')
    return receipt, index.source_status('ns:a', receipt.revision_id)


def _apple_double(root):
    _classification(root)
    (root / 'ng' / '._0.png').write_bytes(b'\x00\x05\x16\x07 AppleDouble metadata, not an image')


def _nested(root):
    _classification(root)
    _image(root / 'ok' / 'sub' / 'a.png')


def _image_at_root(root):
    _classification(root)
    _image(root / 'loose.png')


def _coco_listing_fewer(root):
    for number in range(3):
        _image(root / 'images' / f'{number}.png')
    (root / 'annotations.json').write_text(json.dumps({
        'images': [{'id': 1, 'file_name': 'images/0.png', 'width': 16, 'height': 16},
                   {'id': 2, 'file_name': 'images/1.png', 'width': 16, 'height': 16}],
        'annotations': [{'id': 1, 'image_id': 1, 'category_id': 1, 'bbox': [1, 1, 4, 4], 'area': 16, 'iscrowd': 0}],
        'categories': [{'id': 1, 'name': 'scratch'}]}))


@pytest.mark.parametrize('layout,task', [(_apple_double, 'classification'), (_nested, 'classification'),
                                         (_image_at_root, 'classification'), (_coco_listing_fewer, 'detection')])
def test_a_fresh_validation_of_an_unchanged_source_matches_it(index, tmp_path, layout, task):
    source = tmp_path / 'source'
    layout(source)
    receipt, status = _status(index, tmp_path, source, task)
    assert status['matches'] is True, status
    assert (status['added'], status['removed'], status['changed'], status['gaps_changed']) == (0, 0, 0, False)
    assert status['images'] == status['revision_images'] == receipt.image_count
    assert status['revision_id'] == receipt.revision_id


def test_added_removed_and_rewritten_images_are_counted_and_the_source_no_longer_matches(index, tmp_path):
    source = _classification(tmp_path / 'source')
    receipt, status = _status(index, tmp_path, source, 'classification')
    assert status['matches'] is True
    _image(source / 'ok' / 'new.png')
    (source / 'ng' / '2.png').unlink()
    later = time.time() + 5
    _image(source / 'ok' / '0.png', 'gray')  # other bytes under the same name
    os.utime(source / 'ok' / '0.png', (later, later))
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['added'], status['removed'], status['changed']) == (False, 1, 1, 1), status
    assert status['images'] == receipt.image_count and status['revision_images'] == receipt.image_count


def test_a_file_the_cache_cannot_confirm_counts_as_changed_until_validated_again(index, tmp_path):
    source = _classification(tmp_path / 'source')
    receipt, _status_now = _status(index, tmp_path, source, 'classification')
    later = time.time() + 5
    os.utime(source / 'ok' / '1.png', (later, later))  # same bytes, new modification time: unconfirmed without a read
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['changed']) == (False, 1)
    again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    assert index.source_status('ns:a', again.revision_id)['matches'] is True
    assert index.source_status('ns:a', receipt.revision_id)['matches'] is True, 'the new read confirmed the same bytes'


@pytest.mark.parametrize('operation', ['stat', 'open'])
def test_a_recorded_read_error_matches_while_unreadable_and_changes_when_readable(index, tmp_path, monkeypatch, operation):
    source = _classification(tmp_path / 'source')
    target = source / 'ng' / '1.png'
    original = getattr(Path, operation)

    def denied(path, *args, **kwargs):
        if path == target and kwargs.get('follow_symlinks', True):
            raise PermissionError('synthetic access denial')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as blocked:
        blocked.setattr(Path, operation, denied)
        receipt, status = _status(index, tmp_path, source, 'classification')
        assert (receipt.valid_count, receipt.error_count) == (5, 1)
        assert (status['matches'], status['changed']) == (True, 0), status
        again = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
        assert index.source_status('ns:a', again.revision_id)['matches'] is True
        with index._connect() as db:
            assert db.execute('SELECT COUNT(*) FROM dataset_stat_cache WHERE relative_path=?',
                              ('ng/1.png',)).fetchone()[0] == 0, 'transient failures must never become cached reads'
    recovered = index.source_status('ns:a', receipt.revision_id)
    assert (recovered['matches'], recovered['changed']) == (False, 1), recovered
    fresh, status = _status(index, tmp_path, source, 'classification')
    assert fresh.error_count == 0 and status['matches'] is True


def test_a_valid_image_that_becomes_unreadable_never_matches_its_previous_revision(index, tmp_path, monkeypatch):
    source = _classification(tmp_path / 'source')
    receipt, _ = _status(index, tmp_path, source, 'classification')
    target = source / 'ng' / '1.png'
    original = Path.stat

    def denied(path, *args, **kwargs):
        if path == target and kwargs.get('follow_symlinks', True):
            raise PermissionError('synthetic access denial')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', denied)
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['changed']) == (False, 1), status


def test_a_recorded_read_error_that_disappears_after_listing_is_a_change(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification(tmp_path / 'source')
    target = source / 'ng' / '1.png'
    original = Path.open

    def denied(path, *args, **kwargs):
        if path == target and kwargs.get('follow_symlinks', True):
            raise PermissionError('synthetic access denial')
        return original(path, *args, **kwargs)

    with monkeypatch.context() as blocked:
        blocked.setattr(Path, 'open', denied)
        receipt = index.build_revision('ns:a', tmp_path / 'project', source, 'classification', 'exclude')
    discover = dataset_index._discover

    def disappears(*args, **kwargs):
        inventory = discover(*args, **kwargs)
        target.unlink()
        return inventory

    monkeypatch.setattr(dataset_index, '_discover', disappears)
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['changed']) == (False, 1), status


def test_a_recorded_unstorable_name_matches_until_the_discovered_name_is_storable(index, tmp_path, monkeypatch):
    from backend.engine import dataset_index
    source = _classification(tmp_path / 'source')
    storable = dataset_index._storable

    def simulated_legacy_name(relative):
        # Portable error-state regression; this does not claim a native non-UTF-8 Windows/APFS filename.
        return (relative, False) if relative == 'ng/1.png' else storable(relative)

    with monkeypatch.context() as legacy:
        legacy.setattr(dataset_index, '_storable', simulated_legacy_name)
        receipt, status = _status(index, tmp_path, source, 'classification')
        assert receipt.error_count == 1
        assert (status['matches'], status['changed']) == (True, 0), status
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['changed']) == (False, 1), status
    (source / 'ng' / '1.png').rename(source / 'ng' / 'renamed.png')
    status = index.source_status('ns:a', receipt.revision_id)
    assert (status['matches'], status['removed'], status['added']) == (False, 1, 1), status


def test_a_newer_revisions_cache_cannot_confirm_an_older_revisions_different_bytes(index, tmp_path):
    source = _classification(tmp_path / 'source')
    first, _ = _status(index, tmp_path, source, 'classification')
    _image(source / 'ng' / '1.png', 'gray')
    second, status = _status(index, tmp_path, source, 'classification')
    assert status['matches'] is True
    status = index.source_status('ns:a', first.revision_id)
    assert (status['matches'], status['changed']) == (False, 1), status
    assert first.revision_id != second.revision_id


def test_a_folder_that_becomes_unreadable_is_a_change(index, tmp_path):
    if os.name == 'nt' or os.geteuid() == 0:
        pytest.skip('POSIX permissions; native Windows file access is covered by native QA')
    source = _classification(tmp_path / 'source')
    receipt, _ = _status(index, tmp_path, source, 'classification')
    os.chmod(source / 'ng', 0)
    try:
        status = index.source_status('ns:a', receipt.revision_id)
    finally:
        os.chmod(source / 'ng', 0o755)
    assert status['matches'] is False and status['gaps_changed'] is True and status['gaps'] == 1


def test_an_unknown_revision_or_another_projects_is_refused(index, tmp_path):
    source = _classification(tmp_path / 'source')
    receipt, _ = _status(index, tmp_path, source, 'classification')
    with pytest.raises(KeyError):
        index.source_status('ns:a', 'f' * 32)
    with pytest.raises(KeyError):
        index.source_status('ns:other', receipt.revision_id)


def test_the_route_answers_for_the_active_projects_revision(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    from backend.api import routes_dataset_imports, routes_project
    from backend.contracts.context import ContextRegistry
    from backend.main import ProjectContextMiddleware
    app = FastAPI()
    app.state.project_dir = tmp_path / 'projects'
    app.state.context_registry = ContextRegistry(tmp_path / 'projects')
    app.include_router(routes_project.router)
    app.include_router(routes_dataset_imports.router)
    app.add_middleware(ProjectContextMiddleware, project_app=app)
    client = TestClient(app)
    source = _apple_double(tmp_path / 'source') or tmp_path / 'source'
    client.post('/api/project/create', json={'name': 'Status', 'task': 'classification'})
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    job = client.post('/api/dataset/imports', json={'task': 'classification'}, headers={'Idempotency-Key': 'k1'}).json()['job_id']
    for _ in range(200):
        view = client.get(f'/api/dataset/imports/{job}').json()
        if view['state'] in ('completed', 'failed', 'aborted', 'interrupted'):
            break
        time.sleep(0.05)
    revision = view['result']['revision']['revision_id']
    answered = client.get(f'/api/dataset/revisions/{revision}/source-status')
    assert answered.status_code == 200, answered.text
    assert answered.json()['matches'] is True and answered.json()['revision_id'] == revision
    assert client.get(f'/api/dataset/revisions/{"f" * 32}/source-status').status_code == 404

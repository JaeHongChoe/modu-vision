"""Removing an overlay must retain recoverable bytes and its guarded authorship."""
import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_annotation as ra
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import (
    dataset_annotation_dir, set_request_annotation_root, reset_request_annotation_root,
    set_request_project_root, reset_request_project_root,
)


@pytest.fixture
def legacy(tmp_path, monkeypatch):
    root = tmp_path / 'labels'; root.mkdir()
    monkeypatch.setattr(ra, 'ANNOTATIONS_DIR', root)
    (root / 'a.json').write_bytes(b'{"image_id":"a","annotations":[]}\n')
    (root / 'masks').mkdir()
    (root / 'masks/a.png').write_bytes(b'original raster bytes')
    return root


def test_delete_retains_both_exact_bytes_and_restore_audits_actor(legacy):
    before = {p.relative_to(legacy): p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
    deleted = ra.delete_annotations('a', actor='reviewer')
    assert deleted['status'] == 'deleted'
    assert not (legacy / 'a.json').exists()
    assert not (legacy / 'masks/a.png').exists()
    history = ra.annotation_recovery_history('a')
    assert len(history['items']) == 1
    assert history['items'][0]['actor'] == 'reviewer'
    assert history['items'][0]['recovery_id'] == deleted['recovery_id']
    ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(actor='restorer'))
    assert {p: (legacy / p).read_bytes() for p in before} == before
    item = ra.annotation_recovery_history('a')['items'][0]
    assert item['restored_by'] == 'restorer'
    assert item['restored_at']
    # Archives have no JSON/PNG suffix that active label scanners could discover.
    assert not list((legacy / '.recovery').rglob('*.json'))
    assert not list((legacy / '.recovery').rglob('*.png'))


def test_restore_refuses_to_overwrite_a_newer_annotation(legacy):
    deleted = ra.delete_annotations('a')
    (legacy / 'a.json').write_bytes(b'new human labels')
    with pytest.raises(HTTPException) as error:
        ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest())
    assert error.value.status_code == 409
    assert (legacy / 'a.json').read_bytes() == b'new human labels'


def test_http_recovery_routes_return_handle_and_refuse_invalid_queries(legacy):
    import asyncio
    import httpx
    from fastapi import FastAPI
    app = FastAPI(); app.include_router(ra.router)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            assert (await client.delete('/api/annotations/a', params={'expected_revision': 0})).status_code == 422
            assert (legacy / 'a.json').is_file()
            deleted = await client.delete('/api/annotations/a', params={'actor': 'http-reviewer'})
            assert deleted.status_code == 200
            recovery_id = deleted.json()['recovery_id']
            listed = (await client.get('/api/annotations/a/recovery')).json()['items']
            assert listed[0]['actor'] == 'http-reviewer'
            restored = await client.post(f'/api/annotations/a/restore/{recovery_id}', json={'actor': 'http-restorer'})
            assert restored.status_code == 200
            assert restored.json()['status'] == 'restored'
            assert (legacy / 'masks/a.png').read_bytes() == b'original raster bytes'
    asyncio.run(exercise())


def test_tampered_or_wrong_image_recovery_cannot_write_live_labels(legacy):
    deleted = ra.delete_annotations('a')
    archive = legacy / '.recovery' / deleted['recovery_id']
    (archive / 'mask.data').write_bytes(b'tampered')
    for name in ('a', 'b'):
        with pytest.raises(HTTPException) as error:
            ra.restore_annotations(name, deleted['recovery_id'], ra.AnnotationRecoveryRequest())
        assert error.value.status_code == 422
    assert not (legacy / 'a.json').exists()
    assert not (legacy / 'masks/a.png').exists()


def test_mask_only_archive_is_recoverable_and_missing_delete_has_no_history(legacy):
    (legacy / 'a.json').unlink()
    deleted = ra.delete_annotations('a')
    assert deleted['status'] == 'deleted'
    assert ra.delete_annotations('a')['status'] == 'not_found'
    ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest())
    assert not (legacy / 'a.json').exists()
    assert (legacy / 'masks/a.png').read_bytes() == b'original raster bytes'


@pytest.mark.parametrize('entry', ['.recovery', 'masks', 'a.json'])
def test_delete_rejects_links_without_touching_their_destination(legacy, tmp_path, entry):
    destination = tmp_path / 'outside'; destination.mkdir()
    victim = destination / 'victim'; victim.write_bytes(b'outside')
    path = legacy / entry
    if path.is_dir():
        for child in path.iterdir(): child.unlink()
        path.rmdir()
    elif path.exists(): path.unlink()
    path.symlink_to(victim if entry == 'a.json' else destination, target_is_directory=entry != 'a.json')
    with pytest.raises(HTTPException): ra.delete_annotations('a')
    assert victim.read_bytes() == b'outside'
    assert sorted(p.name for p in destination.iterdir()) == ['victim']


def test_failed_mask_removal_restores_label_and_leaves_no_success_history(legacy, monkeypatch):
    original = type(legacy).unlink
    once = False
    def fail(path, *args, **kwargs):
        nonlocal once
        if path == legacy / 'masks/a.png' and not once:
            once = True
            raise OSError('fixture mask removal failed')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(legacy), 'unlink', fail)
    with pytest.raises(HTTPException): ra.delete_annotations('a')
    assert (legacy / 'a.json').read_bytes() == b'{"image_id":"a","annotations":[]}\n'
    assert (legacy / 'masks/a.png').read_bytes() == b'original raster bytes'
    assert ra.annotation_recovery_history('a')['items'] == []


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'; root.mkdir()
    source = tmp_path / 'source'; source.mkdir()
    image = source / 'a.png'; Image.new('RGB', (40, 30)).save(image)
    (root / 'project.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    a = set_request_annotation_root(root / 'annotations'); p = set_request_project_root(root)
    try:
        result = ra.save_annotations(ra.AnnotationSaveRequest(image_id='a', image_path=str(image),
            image_width=40, image_height=30, actor='author', annotations=[{
                'type': 'polygon', 'label': 'NG', 'polygon': [[1, 1], [10, 1], [10, 10]]}]))
        yield root, source, image, result['metadata']
    finally:
        reset_request_project_root(p); reset_request_annotation_root(a)


def test_project_delete_cannot_omit_binding_or_use_stale_revision(project):
    root, source, image, row = project
    overlay = dataset_annotation_dir(source, root / 'annotations')
    before = (overlay / 'a.json').read_bytes()
    with pytest.raises(HTTPException) as missing: ra.delete_annotations('a')
    assert missing.value.status_code == 422
    with pytest.raises(HTTPException) as missing_revision:
        ra.delete_annotations('a', image_path=str(image))
    assert missing_revision.value.status_code == 409
    with pytest.raises(HTTPException) as stale:
        ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'] - 1)
    assert stale.value.status_code == 409
    assert (overlay / 'a.json').read_bytes() == before


def test_same_stem_peer_cannot_archive_another_images_overlay(project):
    root, source, image, row = project
    peer = source / 'a.jpg'; Image.new('RGB', (40, 30)).save(peer)
    ledger_path = dm.ledger_path(root, source)
    overlay = dataset_annotation_dir(source, root / 'annotations')
    before = {p: p.read_bytes() for p in (ledger_path, overlay / 'a.json', overlay / 'masks/a.png')}
    with pytest.raises(HTTPException) as error:
        ra.delete_annotations('a', image_path=str(peer))
    assert error.value.status_code == 422
    assert {p: p.read_bytes() for p in before} == before
    assert image.is_file() and peer.is_file()


def test_project_delete_and_restore_use_authenticated_actor_and_invalidate_review(project):
    root, source, image, row = project
    request = SimpleNamespace(state=SimpleNamespace(account_user={'username': 'authenticated'}))
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'],
                                    actor='forged', request=request)
    current = dm.metadata_for_path(root, source, image)
    assert current['revision'] > row['revision']
    assert current['workflow_state'] == 'needs_review'
    assert current['audit'][-1]['action'] == 'annotation_archived'
    assert current['audit'][-1]['actor'] == 'authenticated'
    restored = ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(
        image_path=str(image), expected_revision=current['revision']), request=request)
    assert restored['metadata']['audit'][-1]['action'] == 'annotation_restored'
    assert restored['metadata']['audit'][-1]['actor'] == 'authenticated'
    assert restored['metadata']['workflow_state'] == 'needs_review'


def test_project_team_delete_requires_current_revision_and_lease(project):
    root, source, image, row = project
    with dm.metadata_transaction(root, source) as ledger:
        from backend.engine.team_data import _state
        _state(ledger)['settings']['editing_enabled'] = True
    for revision in (None, row['revision']):
        with pytest.raises(HTTPException) as error:
            ra.delete_annotations('a', image_path=str(image), expected_revision=revision)
        assert error.value.status_code == 409
    assert (dataset_annotation_dir(source, root / 'annotations') / 'a.json').is_file()


def test_metadata_publication_failure_rolls_back_archived_pair_and_history(project, monkeypatch):
    root, source, image, row = project
    overlay = dataset_annotation_dir(source, root / 'annotations')
    ledger_path = dm.ledger_path(root, source)
    before = {p: p.read_bytes() for p in (overlay / 'a.json', overlay / 'masks/a.png', ledger_path)}
    replace = dm.os.replace
    def fail(origin, target):
        if target == ledger_path: raise OSError('fixture ledger publication failure')
        return replace(origin, target)
    monkeypatch.setattr(dm.os, 'replace', fail)
    with pytest.raises(HTTPException):
        ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    assert {p: p.read_bytes() for p in before} == before
    assert ra.annotation_recovery_history('a', image_path=str(image))['items'] == []


def test_restore_refuses_labels_for_replaced_source_image(project):
    root, source, image, row = project
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    Image.new('RGB', (40, 30), color='red').save(image)
    current = dm.metadata_for_path(root, source, image)
    with pytest.raises(HTTPException) as error:
        ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(
            image_path=str(image), expected_revision=current['revision']))
    assert error.value.status_code == 409
    assert not (dataset_annotation_dir(source, root / 'annotations') / 'a.json').exists()


def test_project_recovery_is_outside_version_replaced_overlay_and_not_training_data(project):
    root, source, image, row = project
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    overlay = dataset_annotation_dir(source, root / 'annotations')
    assert not list(overlay.rglob('*.data'))
    assert list((root / 'annotations/.recovery').rglob('manifest'))
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    before = fingerprint_dataset(source, studio_root=root / 'annotations')
    item = next((root / 'annotations/.recovery').rglob('manifest'))
    item.write_bytes(item.read_bytes() + b'\n')
    assert fingerprint_dataset(source, studio_root=root / 'annotations') == before
    assert ra.annotation_recovery_history('a', image_path=str(image))['items'][0]['recovery_id'] == deleted['recovery_id']


def _tree_bytes(root):
    """Include link identity and file modes without following linked directories."""
    return {p.relative_to(root).as_posix(): ('link', os.readlink(p)) if p.is_symlink()
            else ('file', p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
            for p in root.rglob('*') if p.is_symlink() or p.is_file()}


@pytest.mark.parametrize('failed_target', ['annotation', 'mask', 'manifest', 'ledger'])
def test_restore_publication_failure_preserves_archive_live_absence_and_ledger(project, monkeypatch, failed_target):
    root, source, image, row = project
    overlay = dataset_annotation_dir(source, root / 'annotations')
    original_pair = {name: (overlay / name).read_bytes() for name in ('a.json', 'masks/a.png')}
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    manifest = next((root / 'annotations/.recovery').rglob('manifest'))
    target = {'annotation': overlay / 'a.json', 'mask': overlay / 'masks/a.png',
              'manifest': manifest, 'ledger': dm.ledger_path(root, source)}[failed_target]
    before, source_before = _tree_bytes(root), _tree_bytes(source)
    replace = dm.os.replace
    failed = False

    def fail_once(origin, destination):
        nonlocal failed
        if Path(destination) == target and not failed:
            failed = True
            raise OSError('fixture restore publication failure: ' + failed_target)
        return replace(origin, destination)

    request = ra.AnnotationRecoveryRequest(image_path=str(image), expected_revision=deleted['metadata']['revision'])
    with monkeypatch.context() as patch:
        patch.setattr(dm.os, 'replace', fail_once)
        with pytest.raises(HTTPException) as error:
            ra.restore_annotations('a', deleted['recovery_id'], request)
        assert error.value.status_code == 500
    assert failed, 'the selected publication point was reached'
    assert _tree_bytes(root) == before, 'failed restore changes neither live files, archive audit, nor ledger bytes/modes'
    assert _tree_bytes(source) == source_before
    assert not list(root.rglob('.annotation-atomic-*'))

    # A failed attempt must not consume the revision or append a restoration.
    restored = ra.restore_annotations('a', deleted['recovery_id'], request)
    assert restored['status'] == 'restored'
    assert {name: (overlay / name).read_bytes() for name in original_pair} == original_pair
    assert len(json.loads(manifest.read_bytes())['restorations']) == 1


@pytest.mark.parametrize('operation', ['archive', 'restore'])
def test_same_revision_replay_preserves_first_commit_without_duplicate_history(project, operation):
    root, source, image, row = project
    if operation == 'archive':
        def action():
            return ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    else:
        deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
        request = ra.AnnotationRecoveryRequest(image_path=str(image), expected_revision=deleted['metadata']['revision'])

        def action():
            return ra.restore_annotations('a', deleted['recovery_id'], request)

    committed = action()
    before, source_before = _tree_bytes(root), _tree_bytes(source)
    with pytest.raises(HTTPException) as error:
        action()
    assert error.value.status_code == 409
    assert _tree_bytes(root) == before
    assert _tree_bytes(source) == source_before
    current = dm.metadata_for_path(root, source, image)
    assert current['revision'] == committed['metadata']['revision']
    assert sum(event['action'] == ('annotation_archived' if operation == 'archive' else 'annotation_restored')
               for event in current['audit']) == 1
    history = ra.annotation_recovery_history('a', image_path=str(image))['items']
    assert len(history) == 1
    assert len(history[0].get('restorations', [])) == (operation == 'restore')


@pytest.mark.parametrize('linked_entry', ['root', 'scope', 'record', 'manifest', 'annotation.data', 'mask.data'])
def test_project_restore_rejects_archive_parent_and_payload_links_without_writes(project, tmp_path, linked_entry):
    root, source, image, row = project
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    recovery_root = root / 'annotations/.recovery'
    manifest = next(recovery_root.rglob('manifest'))
    original = {'root': recovery_root, 'scope': manifest.parent.parent,
                'record': manifest.parent, 'manifest': manifest}.get(linked_entry, manifest.parent / linked_entry)
    destination = tmp_path / 'outside-recovery'
    is_directory = original.is_dir()
    original.rename(destination)
    try:
        original.symlink_to(destination, target_is_directory=is_directory)
    except OSError:
        if os.name == 'nt':
            pytest.skip('Windows account cannot create this symbolic link')
        raise
    before, outside_before, source_before = _tree_bytes(root), _tree_bytes(destination) if is_directory else destination.read_bytes(), _tree_bytes(source)
    with pytest.raises(HTTPException) as error:
        ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(
            image_path=str(image), expected_revision=deleted['metadata']['revision']))
    assert error.value.status_code == 422
    assert _tree_bytes(root) == before
    assert (_tree_bytes(destination) if is_directory else destination.read_bytes()) == outside_before
    assert _tree_bytes(source) == source_before


def test_real_dataset_version_restore_retains_archive_and_allows_original_label_recovery(project):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from backend.api import routes_dataset_versions as versions
    from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root

    root, source, image, row = project
    overlay = dataset_annotation_dir(source, root / 'annotations')
    original_pair = {name: (overlay / name).read_bytes() for name in ('a.json', 'masks/a.png')}
    source_before = _tree_bytes(source)
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    recovery_before = _tree_bytes(root / 'annotations/.recovery')
    app = FastAPI()
    app.state.current_project = {'id': 'recovery-version-test', 'project_dir': str(root),
        'source_dataset_dir': str(source), 'task': 'segmentation', 'annotations_dir': str(root / 'annotations')}
    app.include_router(versions.router)

    async def version_roundtrip():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
            created = await client.post('/api/dataset/versions', json={'name': 'Archived labels absent'})
            assert created.status_code == 200, created.text
            version_id = created.json()['id']
            manifest = json.loads((root / 'versions' / version_id / 'manifest.json').read_bytes())
            assert not any('.recovery' in Path(item['source_path']).parts for item in manifest['files'])
            ra.save_annotations(ra.AnnotationSaveRequest(image_id='a', image_path=str(image), actor='later-author',
                expected_revision=deleted['metadata']['revision'], image_width=40, image_height=30,
                annotations=[{'type': 'polygon', 'label': 'Later', 'polygon': [[15, 5], [25, 5], [25, 15], [15, 15]]}]))
            assert (overlay / 'a.json').read_bytes() != original_pair['a.json']
            restored = await client.post(f'/api/dataset/versions/{version_id}/restore')
            assert restored.status_code == 200, restored.text
            assert restored.json()['backup_version_id'] != version_id

    split_token = set_request_split_root(root / 'dataset/splits')
    try:
        asyncio.run(version_roundtrip())
    finally:
        reset_request_split_root(split_token)
    assert not (overlay / 'a.json').exists(), 'the real version restore actually replaced the newer overlay'
    assert not (overlay / 'masks/a.png').exists()
    assert _tree_bytes(root / 'annotations/.recovery') == recovery_before
    assert _tree_bytes(source) == source_before
    current = dm.metadata_for_path(root, source, image)
    assert any(event['action'] == 'version_restored' for event in current['audit'])
    assert any(event['action'] == 'annotation_archived' for event in current['audit'])
    restored = ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(
        image_path=str(image), expected_revision=current['revision']))
    assert restored['status'] == 'restored'
    assert {name: (overlay / name).read_bytes() for name in original_pair} == original_pair
    assert _tree_bytes(source) == source_before


@pytest.mark.parametrize('first_writer', ['save', 'restore'])
def test_same_revision_save_restore_contenders_commit_only_one_pair_and_audit(project, first_writer):
    root, source, image, row = project
    overlay = dataset_annotation_dir(source, root / 'annotations')
    original_pair = {name: (overlay / name).read_bytes() for name in ('a.json', 'masks/a.png')}
    source_before = _tree_bytes(source)
    deleted = ra.delete_annotations('a', image_path=str(image), expected_revision=row['revision'])
    revision = deleted['metadata']['revision']
    recovery_before = _tree_bytes(root / 'annotations/.recovery')
    first_locked, second_started = Event(), Event()

    def action(kind):
        try:
            if kind == 'restore':
                return ra.restore_annotations('a', deleted['recovery_id'], ra.AnnotationRecoveryRequest(
                    image_path=str(image), expected_revision=revision, actor='race-restorer'))
            return ra.save_annotations(ra.AnnotationSaveRequest(image_id='a', image_path=str(image),
                image_width=40, image_height=30, expected_revision=revision, actor='race-saver',
                annotations=[{'type': 'polygon', 'label': 'Race new', 'polygon': [[15, 5], [25, 5], [25, 15], [15, 15]]}]))
        except HTTPException as error:
            return error

    def first():
        # Hold the real outer OS lock while the other request starts. Each worker
        # has a distinct Context, so neither can inherit the other's transaction.
        with dm.metadata_transaction(root, source):
            first_locked.set()
            assert second_started.wait(10), 'the competing request did not start'
            return action(first_writer)

    def second():
        assert first_locked.wait(10), 'the first writer did not acquire the actual ledger lock'
        second_started.set()
        return action('restore' if first_writer == 'save' else 'save')

    with ThreadPoolExecutor(max_workers=2) as executor:
        winner = executor.submit(copy_context().run, first)
        loser = executor.submit(copy_context().run, second)
        committed, refused = winner.result(timeout=20), loser.result(timeout=20)
    assert isinstance(committed, dict)
    assert isinstance(refused, HTTPException) and refused.status_code == 409
    current = dm.metadata_for_path(root, source, image)
    assert current['revision'] == revision + 1
    assert current['audit'][-1]['actor'] == ('race-saver' if first_writer == 'save' else 'race-restorer')
    assert sum(event['actor'] in {'race-saver', 'race-restorer'} for event in current['audit']) == 1
    manifest = next((root / 'annotations/.recovery').rglob('manifest'))
    if first_writer == 'restore':
        assert {name: (overlay / name).read_bytes() for name in original_pair} == original_pair
        assert len(json.loads(manifest.read_bytes())['restorations']) == 1
    else:
        import cv2
        saved = json.loads((overlay / 'a.json').read_bytes())
        assert [item['label'] for item in saved['annotations']] == ['Race new']
        mask = cv2.imread(str(overlay / 'masks/a.png'), cv2.IMREAD_UNCHANGED)
        assert mask.shape == (30, 40) and mask[10, 20] == 1 and mask[2, 2] == 0
        assert _tree_bytes(root / 'annotations/.recovery') == recovery_before
    assert _tree_bytes(source) == source_before
    assert not list(root.rglob('.annotation-atomic-*'))

"""Class rename and merge (v1): a preview with counts per store, an apply behind a data version backup with a ledger
event per image, and refusals for id collisions (case variants included), a label book naming the class, foreign edit
leases, active jobs (shown already in the preview), a role flip, a class split by source-only labels and a stale
preview. Source label files and mask pixels are never touched; a class only in source files still counts as a merge
target, and a failed write is reported in Korean with the backup to restore when the rollback failed too."""
import hashlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_annotation, routes_class_rename, routes_dataset_versions, routes_project
from backend.engine import class_rename, dataset_metadata as dm, team_data as td
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.dataset_fingerprint import fingerprint_dataset

_original_httpx_init = httpx.Client.__init__


def _compatible_httpx_init(self, *args, app=None, **kwargs):
    return _original_httpx_init(self, *args, **kwargs)


httpx.Client.__init__ = _compatible_httpx_init

SCRATCH = [{'type': 'polygon', 'label': 'Scratch', 'category_id': 7, 'color': '#f59e0b', 'polygon': [[1, 1], [15, 1], [10, 15]]},
           {'type': 'bbox', 'label': 'Scratch', 'category_id': 7, 'color': '#f59e0b', 'bbox': [20, 20, 40, 40]}]
DENT = [{'type': 'bbox', 'label': 'Dent', 'category_id': 8, 'color': '#22d3ee', 'bbox': [50, 10, 70, 30]}]
PALETTE = [{'id': 0, 'name': 'background', 'color': '#000000'}, {'id': 7, 'name': 'Scratch', 'color': '#f59e0b'}]


@pytest.fixture
def workspace(tmp_path, task='detection'):
    source = tmp_path / 'source'
    source.mkdir()
    for name, color in (('a', 'white'), ('b', 'black'), ('c', 'gray')):
        Image.new('RGB', (100, 80), color).save(source / f'{name}.png')
    app = FastAPI()
    app.state.project_dir = tmp_path / 'projects'
    app.include_router(routes_project.router)
    app.include_router(routes_dataset_versions.router)
    app.include_router(routes_class_rename.router)
    client = TestClient(app)
    client.post('/api/project/create', json={'name': 'rename', 'task': task})
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project = client.get('/api/project/current').json()
    return client, project, source


def scoped(project):
    return (set_request_annotation_root(Path(project['annotations_dir'])), set_request_project_root(Path(project['project_dir'])))


def unscope(tokens):
    reset_request_project_root(tokens[1])
    reset_request_annotation_root(tokens[0])


def seed(project, source, name, annotations, mask_classes=None):
    tokens = scoped(project)
    try:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
            image_id=name, image_path=str(source / f'{name}.png'), image_width=100, image_height=80,
            annotations=annotations, mask_classes=mask_classes))
    finally:
        unscope(tokens)


def overlay(project, source, name):
    return dataset_annotation_dir(source, Path(project['annotations_dir']), use_scope=False) / f'{name}.json'


def labels(project, source, name):
    return [item['label'] for item in json.loads(overlay(project, source, name).read_text(encoding='utf-8'))['annotations']]


def ledger(project, source):
    return json.loads(dm.ledger_path(Path(project['project_dir']), source, Path(project['annotations_dir'])).read_text(encoding='utf-8'))


def freeze(root):
    return {f.relative_to(root).as_posix(): f.read_bytes() for f in Path(root).rglob('*') if f.is_file() and not f.name.startswith('.')}


def fingerprint(project, source):
    from backend.api import routes_dataset
    return fingerprint_dataset(source, studio_root=Path(project['annotations_dir']),
                               split_manifest=routes_dataset._split_manifest_file(source), use_scope=False)


def populate(project, source, source_label='Scratch'):
    """Two overlays naming Scratch (a with a palette and a mask, b with Dent too), c with a source-only LabelMe file
    (naming Scratch unless told otherwise), a pending draft, a saved flow version, a model receipt and a truth
    declaration that all name Scratch."""
    seed(project, source, 'a', SCRATCH, PALETTE)
    seed(project, source, 'b', [SCRATCH[1], *DENT])
    (source / 'c.json').write_text(json.dumps({'imagePath': 'c.png', 'imageWidth': 100, 'imageHeight': 80,
        'shapes': [{'label': source_label, 'shape_type': 'rectangle', 'points': [[1, 1], [20, 20]]}]}), encoding='utf-8')
    root = Path(project['project_dir'])
    drafts = root / 'label_suggestions'
    drafts.mkdir()
    (drafts / f'suggestion_{"a" * 24}.json').write_text(json.dumps({
        'id': f'suggestion_{"a" * 24}', 'project_id': project['id'], 'status': 'pending', 'image_path': str(source / 'c.png'),
        'candidates': [{'id': 'x_c1', 'confidence': 0.9, 'annotation': {'type': 'bbox', 'label': 'Scratch', 'bbox': [1, 1, 5, 5]}},
                       {'id': 'x_c2', 'confidence': 0.4, 'annotation': {'type': 'bbox', 'label': 'Dent', 'bbox': [1, 1, 5, 5]}}]}), encoding='utf-8')
    (drafts / f'suggestion_{"b" * 24}.json').write_text(json.dumps({
        'id': f'suggestion_{"b" * 24}', 'project_id': project['id'], 'status': 'accepted',
        'candidates': [{'id': 'y_c1', 'annotation': {'label': 'Scratch'}}]}), encoding='utf-8')
    flows = root / 'flowcharts' / 'versions'
    flows.mkdir(parents=True)
    (flows / f'{"c" * 32}.json').write_text(json.dumps({'version_id': 'c' * 32, 'pipeline': {
        'nodes': [{'id': 'n1', 'data': {'node_type': 'detection_crop', 'params': {'target_classes': ['Scratch', 'Dent']}}}],
        'edges': [{'source': 'n1', 'target': 'n2', 'predicate': {'kind': 'class', 'class_name': 'Scratch'}}]}}), encoding='utf-8')
    (flows / f'{"d" * 32}.json').write_text(json.dumps({'version_id': 'd' * 32, 'pipeline': {'nodes': [], 'edges': []}}), encoding='utf-8')
    model = Path(project['models_dir']) / 'job_model1'
    model.mkdir(parents=True)
    (model / 'model_meta.json').write_text(json.dumps({'task': 'detection', 'classes': ['Scratch', 'Dent']}), encoding='utf-8')
    with dm.metadata_transaction(Path(project['project_dir']), source, Path(project['annotations_dir'])) as rows:
        row = dm._ensure(rows, Path(project['project_dir']), source, source / 'a.png', Path(project['annotations_dir']))
        row.setdefault('explicit_truth', {})['scope1'] = [{'scope': {'task': 'detection', 'classes': ['Scratch', 'Dent']},
                                                          'verdict': 'NG', 'sha256': 'f' * 64}]


def test_preview_counts_every_store_and_is_stable(workspace):
    client, project, source = workspace
    populate(project, source)
    first = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]})
    assert first.status_code == 200, first.text
    view = first.json()
    # c's source-only label keeps the old name, so the preview already says why the apply would split the class.
    assert view['applied'] is False and [conflict['code'] for conflict in view['conflicts']] == ['source_only']
    assert view['mapping'] == [{'from': 'Scratch', 'to': 'Crack', 'merge': False, 'source_ids': [7], 'target_ids': [], 'existing_spellings': [],
                                'source_spellings': ['Scratch'], 'role_from': 'defect', 'role_to': 'defect'}]
    assert view['overlays'] == {'files': 2, 'shapes': 3, 'shapes_by_type': {'polygon': 1, 'bbox': 2}, 'palette_entries': 1,
                                'images': 2, 'images_with_masks': 1}
    assert view['review'] == {'approved': 0}
    assert view['source_only'] == {'images': 1, 'unreadable': 0}
    assert view['drafts'] == {'pending': 1, 'candidates': 1}
    assert view['flows'] == {'versions': 1, 'references': 2}
    assert view['models'] == {'count': 1, 'job_ids': ['job_model1']}
    assert view['truth'] == {'images': 1, 'declarations': 1}
    assert view['labelbook'] == {'version': None, 'names_old_class': False}
    second = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]}).json()
    assert second['preview_sha256'] == view['preview_sha256']
    assert len(view['preview_sha256']) == 64
    # Source files, drafts and receipts are untouched by a preview.
    assert not overlay(project, source, 'c').exists()
    assert labels(project, source, 'a') == ['Scratch', 'Scratch']


def test_apply_backs_up_first_rewrites_names_only_and_records_the_ledger_event(workspace):
    client, project, source = workspace
    populate(project, source, source_label='Hole')
    root = Path(project['project_dir'])
    mask = overlay(project, source, 'a').parent / 'masks' / 'a.png'
    mask_bytes = mask.read_bytes()
    before = freeze(root)
    source_before = freeze(source)
    fingerprint_before = fingerprint(project, source)
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]}).json()
    response = client.post('/api/class-rename/apply', json={
        'mapping': [{'from': 'Scratch', 'to': 'Crack'}], 'actor': 'Kim', 'expected_preview_sha256': view['preview_sha256']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['applied'] is True and result['overlays']['files'] == 2 and result['affected_images'] == ['a.png', 'b.png']
    assert result['dataset_fingerprint'] != fingerprint_before
    # Names changed, ids and mask pixels did not; the palette follows.
    assert labels(project, source, 'a') == ['Crack', 'Crack'] and labels(project, source, 'b') == ['Crack', 'Dent']
    saved = json.loads(overlay(project, source, 'a').read_text(encoding='utf-8'))
    assert [item['category_id'] for item in saved['annotations']] == [7, 7]
    assert {entry['id']: entry['name'] for entry in saved['mask_classes']} == {0: 'background', 7: 'Crack'}
    assert mask.read_bytes() == mask_bytes
    # The source LabelMe file and the source-only image are untouched: no overlay was materialized for c.
    assert freeze(source) == source_before and not overlay(project, source, 'c').exists()
    # Drafts, flows and model receipts keep their bytes.
    after = freeze(root)
    for name in before:
        if name.startswith(('label_suggestions/', 'flowcharts/', 'models/')):
            assert after[name] == before[name], name
    # The backup is an auto_backup data version holding the old names, taken for this label set.
    versions = client.get('/api/dataset/versions').json()['versions']
    assert [row['id'] for row in versions] == [result['backup_version_id']]
    assert versions[0]['kind'] == 'auto_backup' and versions[0]['name'].startswith('클래스 이름 변경 전 · Scratch→Crack')
    backup = root / 'versions' / result['backup_version_id'] / 'labels' / 'studio' / 'a.json'
    assert backup.read_bytes() == before[overlay(project, source, 'a').relative_to(root).as_posix()]
    # One ledger event per rewritten image names the mapping, the counts and the backup.
    rows = ledger(project, source)['images']
    for name in ('a.png', 'b.png'):
        event = rows[name]['audit'][-1]
        assert event['action'] == 'class_renamed' and event['actor'] == 'Kim'
        assert event['changes']['backup_version_id'] == result['backup_version_id']
        assert event['changes']['mapping'] == [{'from': 'Scratch', 'to': 'Crack'}] and event['changes']['files'] == 2
        assert rows[name]['workflow_state'] == 'needs_review' and rows[name]['team']['annotation_actor'] == 'Kim'
    assert rows['a.png']['audit'][-1]['changes']['shapes'] == 2 and rows['b.png']['audit'][-1]['changes']['shapes'] == 1
    assert 'c.png' not in rows or rows['c.png']['audit'][-1]['action'] != 'class_renamed'
    # The ledger already knows the new hashes: reading again records no unexplained external change.
    with dm.metadata_transaction(root, source, Path(project['annotations_dir'])) as current:
        row = dm._ensure(current, root, source, source / 'a.png', Path(project['annotations_dir']))
        assert row['audit'][-1]['action'] == 'class_renamed'
    # Restoring the backup brings the old names back.
    tokens = scoped(project)
    try:
        restored = client.post(f"/api/dataset/versions/{result['backup_version_id']}/restore")
    finally:
        unscope(tokens)
    assert restored.status_code == 200, restored.text
    assert labels(project, source, 'a') == ['Scratch', 'Scratch']


def test_merge_into_a_class_with_the_same_id_is_a_name_only_merge(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    seed(project, source, 'b', [{**SCRATCH[1], 'label': 'scratch'}, *DENT])
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'scratch', 'to': 'Scratch'}]}).json()
    assert view['conflicts'] == [] and view['mapping'][0]['merge'] is True and view['mapping'][0]['existing_spellings'] == ['Scratch', 'scratch']
    assert view['overlays']['files'] == 1 and view['overlays']['shapes'] == 1
    response = client.post('/api/class-rename/apply', json={'mapping': [{'from': 'scratch', 'to': 'Scratch'}], 'expected_preview_sha256': view['preview_sha256']})
    assert response.status_code == 200, response.text
    assert labels(project, source, 'b') == ['Scratch', 'Dent'] and labels(project, source, 'a') == ['Scratch', 'Scratch']


def refused_apply(client, project, source, mapping, status=409, **body):
    before = freeze(Path(project['project_dir']))
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    response = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view.get('preview_sha256', '0' * 64), **body})
    assert response.status_code == status, response.text
    assert freeze(Path(project['project_dir'])) == before, 'a refused rename changed project files'
    assert not (Path(project['project_dir']) / 'versions').exists() or not any((Path(project['project_dir']) / 'versions').iterdir())
    return view, response.json()['detail']


def test_id_collision_merge_is_refused_before_any_backup(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH, PALETTE)
    seed(project, source, 'b', [SCRATCH[1], *DENT])
    view, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Dent'}])
    assert view['conflicts'][0]['code'] == 'id_collision' and '7, 8' in view['conflicts'][0]['message']
    assert view['mapping'][0] == {'from': 'Scratch', 'to': 'Dent', 'merge': True, 'source_ids': [7], 'target_ids': [8], 'existing_spellings': ['Dent'],
                                  'source_spellings': ['Scratch'], 'role_from': 'defect', 'role_to': 'defect'}
    assert 'ID' in detail and '병합' in detail
    # Two classes merged into a new name with different ids collide too.
    view, _ = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Defect'}, {'from': 'Dent', 'to': 'Defect'}])
    assert [conflict['code'] for conflict in view['conflicts']] == ['id_collision', 'id_collision']


def test_a_published_label_book_naming_the_class_refuses_the_rename(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH, PALETTE)
    tokens = scoped(project)
    try:
        td.publish_book(project, source, 0, 'owner', 'Book', [{'id': 7, 'name': 'Scratch', 'color': '#f59e0b'}, {'id': 8, 'name': 'Dent', 'color': '#22d3ee'}])
    finally:
        unscope(tokens)
    view, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Crack'}])
    assert view['labelbook'] == {'version': 1, 'names_old_class': True}
    # The refusal names a step that works: publish_book refuses renamed categories (below), so no "rename it in the book".
    assert view['conflicts'][0]['code'] == 'labelbook' and class_rename.LABELBOOK_REFUSAL in detail
    assert '라벨 기준서 v1' in detail and '발행' not in detail
    # The book's guard itself is unchanged: a renamed category is still refused at publish.
    tokens = scoped(project)
    try:
        with pytest.raises(ValueError):
            td.publish_book(project, source, 1, 'owner', 'Renamed', [{'id': 7, 'name': 'Crack', 'color': '#f59e0b'}, {'id': 8, 'name': 'Dent', 'color': '#22d3ee'}])
    finally:
        unscope(tokens)
    # A class the book does not name can still be renamed, unless its target is bound to another id in the book.
    seed(project, source, 'b', [{'type': 'bbox', 'label': 'Hole', 'category_id': 9, 'color': '#ffffff', 'bbox': [1, 1, 9, 9]}])
    view, _ = refused_apply(client, project, source, [{'from': 'Hole', 'to': 'Dent'}])
    assert view['conflicts'][0]['code'] == 'id_collision'
    ok = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Hole', 'to': 'Pit'}]}).json()
    assert ok['conflicts'] == []


def test_another_actors_live_edit_lease_refuses_the_rewrite(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    seed(project, source, 'b', [SCRATCH[1]])
    tokens = scoped(project)
    try:
        state = td.workspace(project, source)
        td.update_settings(project, source, state['settings']['revision'], 'owner', {'editing_enabled': True})
        row = td.image_state(project, source, source / 'a.png')
        lease = td.acquire_lease(project, source, row['image_uuid'], row['revision'], 'Lee')
    finally:
        unscope(tokens)
    _, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Crack'}], actor='Kim')
    assert 'Lee(1장)' in detail and '편집 잠금이 풀린 뒤' in detail
    # The preview already names the holder, so the dialog never offers an apply that is bound to fail.
    preview = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}], 'actor': 'Kim'}).json()
    assert [conflict['code'] for conflict in preview['conflicts']] == ['lease'] and 'Lee(1장)' in preview['conflicts'][0]['message']
    # The lease holder's own rename proceeds (their open edits are saved by the renderer before the call).
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}], 'actor': 'Lee'}).json()
    assert view['conflicts'] == []
    response = client.post('/api/class-rename/apply', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}], 'actor': 'Lee', 'expected_preview_sha256': view['preview_sha256']})
    assert response.status_code == 200, response.text
    assert lease['lease_token'] and labels(project, source, 'b') == ['Crack']


def test_active_training_batches_and_labeling_jobs_refuse_the_rewrite(workspace, monkeypatch):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    from backend.api import routes_label_suggestions, routes_training
    from backend.engine import labeling_tasks
    monkeypatch.setattr(routes_training.training_job_manager, 'get_active_job', lambda: {'job_id': 'live'})
    view, detail = refused_apply(client, project, source, mapping)
    assert '학습이 진행 중' in detail and view['conflicts'][0] == {'code': 'busy', 'message': detail}
    monkeypatch.setattr(routes_training.training_job_manager, 'get_active_job', lambda: None)
    key = routes_label_suggestions._project_batch_key(project)
    monkeypatch.setitem(routes_label_suggestions._LIVE_BATCHES, key, object())
    view, detail = refused_apply(client, project, source, mapping)
    assert '일괄 초안' in detail and view['conflicts'][0]['code'] == 'busy'
    monkeypatch.delitem(routes_label_suggestions._LIVE_BATCHES, key)
    monkeypatch.setitem(labeling_tasks._LIVE, 'batch_' + 'e' * 24, (project['id'], None, None))
    view, detail = refused_apply(client, project, source, mapping)
    assert '라벨 작업' in detail and view['conflicts'][0]['code'] == 'busy'
    monkeypatch.delitem(labeling_tasks._LIVE, 'batch_' + 'e' * 24)
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    assert client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']}).status_code == 200


def test_a_stale_preview_and_invalid_mappings_are_refused(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    seed(project, source, 'b', [SCRATCH[1]])
    stale = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']})
    assert stale.status_code == 409 and '다시 미리보기' in stale.json()['detail']
    assert labels(project, source, 'a') == ['Scratch', 'Scratch']
    for bad in ([{'from': 'Scratch', 'to': 'Scratch'}], [{'from': 'Scratch', 'to': 'Crack'}, {'from': 'Crack', 'to': 'Hole'}],
                [{'from': 'Scratch', 'to': 'Crack'}, {'from': 'scratch', 'to': 'Hole'}], [{'from': 'Scratch', 'to': ' '}],
                [{'from': 'Scratch', 'to': 'Defect'}, {'from': 'Dent', 'to': 'defect'}], [{'from': 'Scratch', 'to': 'Crack\u200b'}],
                [{'from': 'Scratch', 'to': 'a/b'}], [{'from': 'Scratch', 'to': 'NUL'}], [{'from': 'Scratch', 'to': 'x' * 121}], []):
        response = client.post('/api/class-rename/preview', json={'mapping': bad})
        # The engine validates, so every refusal is a Korean sentence (never a pydantic list).
        assert response.status_code == 422 and isinstance(response.json()['detail'], str), response.text
        assert any('\uac00' <= character <= '\ud7a3' for character in response.json()['detail']), response.text
    actor = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}], 'actor': 'k' * 101})
    assert actor.status_code == 422 and actor.json()['detail'] == '작업자 이름을 1~100자로 입력하세요.'
    missing = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Nobody', 'to': 'Crack'}]}).json()
    assert missing['conflicts'][0]['code'] == 'nothing_to_do' and '원본 파일에만 있는 라벨은 바꾸지 않습니다' in missing['conflicts'][0]['message']


def test_classification_projects_are_out_of_scope(tmp_path):
    source = tmp_path / 'source'
    (source / 'OK').mkdir(parents=True)
    Image.new('RGB', (10, 10)).save(source / 'OK' / 'a.png')
    app = FastAPI()
    app.state.project_dir = tmp_path / 'projects'
    app.include_router(routes_project.router)
    app.include_router(routes_class_rename.router)
    client = TestClient(app)
    client.post('/api/project/create', json={'name': 'cls', 'task': 'classification'})
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    response = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'OK', 'to': 'Good'}]})
    assert response.status_code == 422 and '폴더 이름' in response.json()['detail']


def test_a_failure_while_publishing_restores_every_overlay_and_the_ledger(workspace, monkeypatch):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH, PALETTE)
    seed(project, source, 'b', [SCRATCH[1], *DENT])
    root = Path(project['project_dir'])
    before = freeze(root)
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    replace = os.replace
    failed = False
    def failing(src, target):
        nonlocal failed
        if str(target).endswith('b.json') and not failed:
            failed = True
            raise OSError('fixture: publication failed')
        return replace(src, target)
    monkeypatch.setattr(os, 'replace', failing)
    response = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']})
    monkeypatch.setattr(os, 'replace', replace)
    assert response.status_code != 200
    after = freeze(root)
    changed = {name for name in set(before) | set(after) if before.get(name) != after.get(name)}
    assert all(name.startswith('versions/') for name in changed), changed
    assert labels(project, source, 'a') == ['Scratch', 'Scratch'] and labels(project, source, 'b') == ['Scratch', 'Dent']
    assert not list(overlay(project, source, 'a').parent.glob('.annotation-atomic-*'))
    assert 'class_renamed' not in json.dumps(ledger(project, source))


def test_source_only_images_naming_the_class_refuse_the_split(workspace):
    client, project, source = workspace
    populate(project, source)
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    view, detail = refused_apply(client, project, source, mapping)
    assert [conflict['code'] for conflict in view['conflicts']] == ['source_only']
    assert 'c.png' in detail and '둘로 나뉩니다' in detail and '2단계에서 열어 저장' in detail
    # Saving c in step 2 makes it an edited label: the rename then covers every image.
    seed(project, source, 'c', [{'type': 'bbox', 'label': 'Scratch', 'category_id': 7, 'color': '#f59e0b', 'bbox': [1, 1, 20, 20]}])
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    assert view['conflicts'] == [] and view['source_only'] == {'images': 0, 'unreadable': 0}
    response = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']})
    assert response.status_code == 200, response.text
    assert labels(project, source, 'c') == ['Crack']
    # A class found only in source files is not "nothing to do": the reason names the images and the next step.
    (source / 'd.json').write_text(json.dumps({'imagePath': 'd.png', 'imageWidth': 100, 'imageHeight': 80,
        'shapes': [{'label': 'Hole', 'shape_type': 'rectangle', 'points': [[1, 1], [9, 9]]}]}), encoding='utf-8')
    Image.new('RGB', (100, 80), 'white').save(source / 'd.png')
    missing = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Hole', 'to': 'Pit'}]}).json()
    assert missing['conflicts'][0]['code'] == 'nothing_to_do' and 'd.png' in missing['conflicts'][0]['message']


def test_an_unreadable_source_label_file_refuses_the_rename(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    (source / 'b.json').write_text('{not json', encoding='utf-8')
    view, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Crack'}])
    assert view['source_only']['unreadable'] == 1 and view['conflicts'][0]['code'] == 'source_only'
    assert 'b.png' in detail and '읽지 못한' in detail


def test_case_variant_spellings_with_different_ids_are_refused(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    seed(project, source, 'b', [{**SCRATCH[1], 'label': 'scratch', 'category_id': 9}])
    view, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'Crack'}])
    assert view['mapping'][0]['source_ids'] == [7, 9] and view['mapping'][0]['source_spellings'] == ['Scratch', 'scratch']
    assert [conflict['code'] for conflict in view['conflicts']] == ['id_collision'] and '7, 9' in detail and 'scratch' in detail
    assert labels(project, source, 'b') == ['scratch']


def test_a_merge_into_a_class_only_in_source_files_is_reported(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    (source / 'c.json').write_text(json.dumps({'imagePath': 'c.png', 'imageWidth': 100, 'imageHeight': 80,
        'shapes': [{'label': 'Crack', 'shape_type': 'rectangle', 'points': [[1, 1], [20, 20]]}]}), encoding='utf-8')
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]}).json()
    assert view['mapping'][0]['merge'] is True and view['mapping'][0]['existing_spellings'] == ['Crack']
    assert view['conflicts'] == []
    # A different spelling of that source-only class would leave two classes: refused with the spelling to use.
    view, detail = refused_apply(client, project, source, [{'from': 'Scratch', 'to': 'crack'}])
    assert [conflict['code'] for conflict in view['conflicts']] == ['spelling'] and '"Crack"' in detail
    response = client.post('/api/class-rename/apply', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}],
        'expected_preview_sha256': client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]}).json()['preview_sha256']})
    assert response.status_code == 200, response.text and labels(project, source, 'a') == ['Crack', 'Crack']


def test_specialized_and_family_jobs_at_their_real_paths_refuse_the_rewrite(workspace, monkeypatch):
    import threading
    from backend.api import routes_automated_training, routes_enhancement, routes_rotated_detection
    from backend.engine import specialized_training_jobs as specialized
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    models = Path(project['models_dir'])
    for task in ('ocr', 'rotation', 'defect_gan'):
        job = models / task / ('a' * 32)
        job.mkdir(parents=True)
        (job / 'job.json').write_text(json.dumps({'job_id': 'a' * 32, 'status': 'running', 'created_at': time.time(),
                                                  'owner_instance': specialized.PROCESS_INSTANCE}), encoding='utf-8')
        monkeypatch.setitem(specialized._EVENTS, str(job.resolve()), threading.Event())
        assert class_rename.active_work(project) and '특수 모델 학습' in class_rename.active_work(project)
        view, detail = refused_apply(client, project, source, mapping)
        assert view['conflicts'][0]['code'] == 'busy' and '특수 모델 학습' in detail
        monkeypatch.delitem(specialized._EVENTS, str(job.resolve()))
        # Without a live owner the job reads back as interrupted and no longer blocks.
        assert class_rename.active_work(project) is None
    live = type('Job', (), {'status': 'running'})()
    monkeypatch.setitem(routes_rotated_detection._JOBS, (str(Path(project['project_dir']).resolve()), 'b' * 32), live)
    assert '특수 모델 학습' in class_rename.active_work(project)
    live.status = 'completed'
    assert class_rename.active_work(project) is None
    monkeypatch.setitem(routes_enhancement._CANCEL_EVENTS, str((models / 'enhancement' / ('c' * 32)).resolve()), threading.Event())
    assert '특수 모델 학습' in class_rename.active_work(project)
    monkeypatch.delitem(routes_enhancement._CANCEL_EVENTS, str((models / 'enhancement' / ('c' * 32)).resolve()))
    monkeypatch.setitem(routes_automated_training._EVENTS, (str(models.resolve()), 'd' * 32), threading.Event())
    view, detail = refused_apply(client, project, source, mapping)
    assert '자동 학습' in detail and view['conflicts'][0]['code'] == 'busy'
    monkeypatch.delitem(routes_automated_training._EVENTS, (str(models.resolve()), 'd' * 32))
    # Another project's jobs never block this one.
    monkeypatch.setitem(routes_automated_training._EVENTS, (str((Path(project['project_dir']).parent / 'other' / 'models').resolve()), 'e' * 32), threading.Event())
    assert class_rename.active_work(project) is None


@pytest.mark.parametrize('old, new', [('Scratch', 'Pass'), ('wear', 'normal_wear'), ('흠집', '양품 흠집'), ('OK', 'Dent')])
def test_a_rename_that_flips_the_inspection_role_is_refused(workspace, old, new):
    client, project, source = workspace
    seed(project, source, 'a', [{**SCRATCH[1], 'label': old}])
    view, detail = refused_apply(client, project, source, [{'from': old, 'to': new}])
    assert [conflict['code'] for conflict in view['conflicts']] == ['role_change'] and '검사 판정 역할' in detail
    assert view['mapping'][0]['role_from'] != view['mapping'][0]['role_to']


def test_the_role_stays_in_the_plan_for_an_allowed_rename(workspace):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH)
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': 'Scratch', 'to': 'Crack'}]}).json()
    assert view['conflicts'] == [] and (view['mapping'][0]['role_from'], view['mapping'][0]['role_to']) == ('defect', 'defect')


def test_a_publish_failure_is_reported_in_korean_and_a_failed_rollback_names_the_backup(workspace, monkeypatch):
    client, project, source = workspace
    seed(project, source, 'a', SCRATCH, PALETTE)
    seed(project, source, 'b', [SCRATCH[1], *DENT])
    mapping = [{'from': 'Scratch', 'to': 'Crack'}]
    replace = os.replace
    state = {'left': 1}
    def locked(src, target):
        if str(target).endswith('b.json') and state['left']:
            state['left'] -= 1
            raise PermissionError(13, 'Access is denied', str(target))
        return replace(src, target)
    monkeypatch.setattr(os, 'replace', locked)
    view = client.post('/api/class-rename/preview', json={'mapping': mapping}).json()
    response = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']})
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert '되돌렸습니다' in detail and 'Errno' not in detail and str(source) not in detail and project['project_dir'] not in detail
    assert labels(project, source, 'a') == ['Scratch', 'Scratch'] and labels(project, source, 'b') == ['Scratch', 'Dent']
    # The file stays locked, so putting it back fails too: a 500 that names the backup to restore.
    versions = Path(project['project_dir']) / 'versions'
    earlier = {path.name for path in versions.iterdir() if path.is_dir()}
    state['left'] = 10 ** 6
    response = client.post('/api/class-rename/apply', json={'mapping': mapping, 'expected_preview_sha256': view['preview_sha256']})
    monkeypatch.setattr(os, 'replace', replace)
    assert response.status_code == 500, response.text
    detail = response.json()['detail']
    backups = {path.name for path in versions.iterdir() if path.is_dir()} - earlier
    assert len(backups) == 1 and backups.pop() in detail and '복원하세요' in detail
    # b was never replaced and a was put back; only b's recovery copy is left beside the overlays.
    assert labels(project, source, 'a') == ['Scratch', 'Scratch'] and labels(project, source, 'b') == ['Scratch', 'Dent']


def test_names_compare_by_nfkc_and_case_and_the_preview_counts_lost_approvals(workspace):
    import unicodedata
    client, project, source = workspace
    # macOS folder names arrive decomposed (NFD); a typed name is composed (NFC). Both are one class.
    seed(project, source, 'a', [{**SCRATCH[1], 'label': unicodedata.normalize('NFD', '흠집')}])
    with dm.metadata_transaction(Path(project['project_dir']), source, Path(project['annotations_dir'])) as rows:
        dm._ensure(rows, Path(project['project_dir']), source, source / 'a.png', Path(project['annotations_dir']))['workflow_state'] = 'approved'
    view = client.post('/api/class-rename/preview', json={'mapping': [{'from': unicodedata.normalize('NFC', '흠집'), 'to': '찍힘'}]}).json()
    assert view['conflicts'] == [] and view['overlays']['shapes'] == 1
    # Every rewritten image goes back to review: the preview says how many approvals that drops.
    assert view['review'] == {'approved': 1}

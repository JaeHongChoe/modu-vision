"""S3-01 slice 3 over HTTP: durable imports, revision listing, explicit accept and paged revision images.

A minimal app with the production project-context middleware; user data in a temporary folder; synthetic images.
"""
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture
def api(tmp_path, monkeypatch):
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
    source = tmp_path / 'source'
    for label, color in (('ok', 'white'), ('ng', 'black')):
        (source / 'train' / label).mkdir(parents=True)
        for number in range(3):
            Image.new('RGB', (16, 16), color).save(source / 'train' / label / f'{number}.png')
    project = client.post('/api/project/create', json={'name': 'Import QA', 'task': 'classification'}).json()
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    return client, project, source


def _finished(client, job_id):
    for _ in range(200):
        view = client.get(f'/api/dataset/imports/{job_id}').json()
        if view['state'] in ('completed', 'failed', 'aborted', 'interrupted'):
            return view
        time.sleep(0.05)
    raise AssertionError(view)


def test_an_import_runs_durably_and_its_revision_becomes_active_only_when_accepted(api):
    client, project, source = api
    body = {'task': 'classification'}
    started = client.post('/api/dataset/imports', json=body, headers={'Idempotency-Key': 'import-1'})
    assert started.status_code == 200, started.text
    job_id = started.json()['job_id']
    view = _finished(client, job_id)
    assert view['state'] == 'completed', view
    retry = client.post('/api/dataset/imports', json=body, headers={'Idempotency-Key': 'import-1'}).json()
    assert retry['job_id'] == job_id and retry['idempotent_replay'] is True
    revision = view['result']['revision']['revision_id']
    listed = client.get('/api/dataset/revisions').json()
    assert listed['active_revision'] is None and [row['revision_id'] for row in listed['revisions']] == [revision]
    accepted = client.post(f'/api/dataset/imports/{job_id}/accept', json={'revision_id': revision, 'expected_active': None})
    assert accepted.status_code == 200 and accepted.json()['active_revision'] == revision
    replay = client.post(f'/api/dataset/imports/{job_id}/accept', json={'revision_id': revision, 'expected_active': None})
    assert replay.status_code == 200 and replay.json()['active_revision'] == revision, 'a repeated accept is a no-op'
    second = client.post('/api/dataset/imports', json={'task': 'classification', 'verify': True}).json()['job_id']
    newer = _finished(client, second)['result']['revision']['revision_id']
    stale = client.post(f'/api/dataset/imports/{second}/accept', json={'revision_id': newer, 'expected_active': None})
    assert stale.status_code == 409, 'the active revision changed since that view'
    first = client.get(f'/api/dataset/revisions/{revision}/images', params={'limit': 4}).json()
    rest = client.get(f'/api/dataset/revisions/{revision}/images', params={'limit': 4, 'cursor': first['next_cursor']}).json()
    assert [row['relative_path'] for row in first['items'] + rest['items']] == sorted(
        f'train/{label}/{n}.png' for label in ('ok', 'ng') for n in range(3))
    assert client.get(f'/api/dataset/revisions/{revision}/images',
                      params={'cursor': first['next_cursor'], 'label': 'ng'}).status_code == 422
    assert client.get(f'/api/dataset/revisions/{revision}/gaps').json() == {'revision_id': revision, 'gaps': []}


def test_another_project_reads_neither_the_import_nor_its_revisions(api):
    client, project, source = api
    job_id = client.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id']
    revision = _finished(client, job_id)['result']['revision']['revision_id']
    client.post('/api/project/create', json={'name': 'Other', 'task': 'classification'})  # now the selected project
    assert client.get(f'/api/dataset/imports/{job_id}').status_code == 404
    assert client.post(f'/api/dataset/imports/{job_id}/accept', json={'revision_id': revision}).status_code == 404
    assert client.get(f'/api/dataset/revisions/{revision}/images').status_code == 404
    assert client.get(f'/api/dataset/revisions/{revision}/gaps').status_code == 404
    assert client.get('/api/dataset/revisions').json() == {'active_revision': None, 'revisions': []}


def test_the_caller_names_no_path_and_a_project_without_a_source_cannot_import(api):
    client, project, source = api
    assert client.post('/api/dataset/imports', json={'task': 'classification', 'source_root': '/etc'}).status_code == 422
    client.post('/api/project/create', json={'name': 'Empty', 'task': 'classification'})
    assert client.post('/api/dataset/imports', json={'task': 'classification'}).status_code == 409


def test_a_team_server_never_follows_links_out_of_its_registered_source(api, monkeypatch):
    from backend.contracts import context as contracts
    client, project, source = api
    real = contracts.get_project_context
    monkeypatch.setattr(contracts, 'get_project_context', lambda request: real(request).model_copy(update={'mode': 'team'}))
    refused = client.post('/api/dataset/imports', json={'task': 'classification', 'follow_links': True})
    assert refused.status_code == 403


def test_a_cancelled_import_ends_aborted_with_no_revision(api, monkeypatch):
    from backend.engine import dataset_import_job, dataset_index
    client, project, source = api
    monkeypatch.setattr(dataset_import_job, '_CANCEL_CHECK_SECONDS', 0)
    real, gate = dataset_index._read_entry, []

    def slow(path):
        gate.append(path)
        time.sleep(0.2)
        return real(path)

    monkeypatch.setattr(dataset_index, '_read_entry', slow)
    job_id = client.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id']
    for _ in range(100):
        if gate:
            break
        time.sleep(0.02)
    assert client.post(f'/api/dataset/imports/{job_id}/cancel').json()['cancel_requested'] is True
    assert _finished(client, job_id)['state'] == 'aborted'
    assert client.get('/api/dataset/revisions').json()['revisions'] == []


def test_the_backend_serves_imports_and_recovers_them_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.contracts.context import ProjectContext
    from backend.engine.dataset_import_job import ImportSpec
    from backend.engine.job_store import ledger
    store = ledger()
    orphan = store.submit(ProjectContext(workspace_id='w', project_id='p', actor_id='a', mode='local'), 'ns:orphan',
                          'dataset_import', {'project_root': str(tmp_path), 'source_root': str(tmp_path), 'task': 'classification',
                                             'invalid_policy': 'exclude', 'verify': False, 'follow_links': False}, 'left-running')
    store.transition(orphan.id, orphan.revision, 'start')
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        assert store.get(orphan.id).state == 'interrupted', 'an import left running by a previous process is not resumed silently'
        client.post('/api/project/create', json={'name': 'Served', 'task': 'classification'})
        response = client.post('/api/dataset/imports', json={'task': 'classification'})
        assert response.status_code == 409 and 'source folder' in response.text, 'the route is served (no source registered yet)'


def test_an_uploaded_zip_is_extracted_into_the_project_and_imported(tmp_path, monkeypatch):
    """Full app: the ZIP goes through the artifact upload API, then the archive import extracts and indexes it."""
    import hashlib
    import io
    import zipfile
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for label, color in (('양품', 'white'), ('불량', 'black')):
            image = io.BytesIO()
            Image.new('RGB', (16, 16), color).save(image, format='PNG')
            archive.writestr(f'{label}/sample 1.png', image.getvalue())
        archive.writestr('불량/broken.png', b'not an image')
    data = buffer.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.post('/api/project/create', json={'name': 'Archive', 'task': 'classification'}).json()
        upload = client.post('/api/artifacts/uploads', json={'kind': 'source', 'sha256': digest, 'size_bytes': len(data)}).json()['upload']
        assert client.put(f"/api/artifacts/uploads/{upload['id']}", params={'offset': 0}, content=data,
                          headers={'Content-Type': 'application/octet-stream'}).status_code == 200
        ref = client.post(f"/api/artifacts/uploads/{upload['id']}/complete").json()['artifact_ref']
        started = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'},
                              headers={'Idempotency-Key': 'zip-1'})
        assert started.status_code == 200, started.text
        target = Path(project['project_dir']) / 'dataset_imports' / digest
        assert started.json()['source_root'] == str(target) and (target / '양품' / 'sample 1.png').is_file()
        view = _finished(client, started.json()['job_id'])
        receipt = view['result']['revision']
        assert (view['state'], receipt['image_count'], receipt['error_count']) == ('completed', 3, 1)
        before = (target / '양품' / 'sample 1.png').stat().st_mtime_ns
        again = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification', 'verify': True})
        assert again.status_code == 200 and (target / '양품' / 'sample 1.png').stat().st_mtime_ns == before, 'extracted once, reused'
        bad = client.post('/api/dataset/imports/archive', json={'artifact': {**ref, 'sha256': '0' * 64}, 'task': 'classification'})
        assert bad.status_code in (403, 404, 409), bad.text


def _zip_bytes(entries):
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return buffer.getvalue()


def _png_bytes(color='white'):
    import io
    image = io.BytesIO()
    Image.new('RGB', (16, 16), color).save(image, format='PNG')
    return image.getvalue()


def _uploaded(client, data):
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    upload = client.post('/api/artifacts/uploads', json={'kind': 'source', 'sha256': digest, 'size_bytes': len(data)}).json()['upload']
    assert client.put(f"/api/artifacts/uploads/{upload['id']}", params={'offset': 0}, content=data,
                      headers={'Content-Type': 'application/octet-stream'}).status_code == 200
    return client.post(f"/api/artifacts/uploads/{upload['id']}/complete").json()['artifact_ref']


def test_a_reused_extraction_is_verified_and_the_job_records_its_artifact(tmp_path, monkeypatch):
    import json
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    from backend.engine.dataset_archive_input import verify_extraction
    from backend.engine.job_store import ledger
    data = _zip_bytes([('양품/a.png', _png_bytes('white')), ('불량/b.png', _png_bytes('black'))])
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.post('/api/project/create', json={'name': 'Archive reuse', 'task': 'classification'}).json()
        ref = _uploaded(client, data)
        first = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'},
                            headers={'Idempotency-Key': 'zip-a'})
        assert first.status_code == 200, first.text
        _finished(client, first.json()['job_id'])
        target = Path(project['project_dir']) / 'dataset_imports' / ref['sha256']
        spec = json.loads(ledger().record(first.json()['job_id'])['spec_json'])
        assert spec['artifact'] == {'id': ref['id'], 'revision': ref['revision'], 'sha256': ref['sha256']}
        assert spec['source_root'] == str(target) and verify_extraction(target, ref['sha256'])
        assert first.json()['source'] == {'root': str(target), 'artifact': spec['artifact']}, 'the view names the archive it read'

        forged = client.post('/api/dataset/imports/archive', json={'artifact': {**ref, 'id': 'f' * 32}, 'task': 'classification'})
        assert forged.status_code in (404, 409), 'a forged reference never reaches the existing folder'

        (target / '양품' / 'a.png').rename(tmp_path / 'moved-out.png')
        (target / '양품' / 'added.png').write_bytes(_png_bytes('gray'))
        again = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'},
                            headers={'Idempotency-Key': 'zip-b'})
        assert again.status_code == 200, again.text
        fresh = Path(again.json()['source_root'])
        assert fresh != target and fresh.name.startswith(ref['sha256'] + '.') and verify_extraction(fresh, ref['sha256'])
        assert (target / '양품' / 'added.png').is_file(), 'the changed folder is kept as it is'
        assert _finished(client, again.json()['job_id'])['result']['revision']['image_count'] == 2

        clash = _uploaded(client, _zip_bytes([('x/a.png', _png_bytes()), ('x/a.png/b.png', _png_bytes())]))
        refused = client.post('/api/dataset/imports/archive', json={'artifact': clash, 'task': 'classification'})
        assert refused.status_code == 422 and 'both a file and a folder' in refused.text, refused.text


def test_a_source_import_keeps_the_request_digest_it_had_before_archives(api):
    import json
    from backend.engine.job_store import ledger
    client, _project, _source = api
    response = client.post('/api/dataset/imports', json={'task': 'classification'}, headers={'Idempotency-Key': 'src-1'})
    assert response.status_code == 200, response.text
    assert 'artifact' not in json.loads(ledger().record(response.json()['job_id'])['spec_json'])


def test_duplicate_groups_and_annotation_labels_are_served_per_revision(api, tmp_path):
    client, _project, source = api
    Image.new('RGB', (16, 16), 'white').save(source / 'train' / 'ng' / 'white-copy.png')  # same bytes as ok/*.png
    revision = _finished(client, client.post('/api/dataset/imports', json={'task': 'classification'}).json()['job_id'])
    revision_id = revision['result']['revision']['revision_id']
    assert revision['result']['revision']['duplicate_groups'] == 2
    groups = client.get(f'/api/dataset/revisions/{revision_id}/duplicates').json()['groups']
    assert sorted(group['members'] for group in groups) == [3, 4]
    conflicting = client.get(f'/api/dataset/revisions/{revision_id}/duplicates', params={'kind': 'conflicting'}).json()['groups']
    assert [group['members'] for group in conflicting] == [4] and conflicting[0]['conflicting'] is True
    assert 'train/ng/white-copy.png' in [item['relative_path'] for item in conflicting[0]['items']]
    assert client.get(f'/api/dataset/revisions/{revision_id}/duplicates', params={'kind': 'all'}).status_code == 422
    assert client.get(f'/api/dataset/revisions/{"0" * 32}/duplicates').status_code == 404
    images = client.get(f'/api/dataset/revisions/{revision_id}/images', params={'annotation_label': 'none'}).json()
    assert images['items'] == [], 'no source annotation carries that label'
    listed = client.get('/api/dataset/revisions').json()['revisions'][0]
    assert (listed['annotations_scanned'], listed['duplicate_groups'], listed['conflicting_duplicates']) == (True, 2, 1)


def test_a_changed_folder_is_extracted_again_once_and_a_retry_never_extracts(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    data = _zip_bytes([('양품/a.png', _png_bytes('white')), ('불량/b.png', _png_bytes('black'))])
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        project = client.post('/api/project/create', json={'name': 'Archive copies', 'task': 'classification'}).json()
        ref = _uploaded(client, data)
        imports = Path(project['project_dir']) / 'dataset_imports'
        first = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'})
        _finished(client, first.json()['job_id'])
        (imports / ref['sha256'] / '양품' / 'added.png').write_bytes(_png_bytes('gray'))  # a real change
        (imports / ref['sha256'] / '.DS_Store').write_bytes(b'browser metadata')
        second = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'},
                             headers={'Idempotency-Key': 'zip-copy'})
        _finished(client, second.json()['job_id'])
        copies = sorted(path.name for path in imports.iterdir() if path.is_dir() and not path.name.startswith('.'))
        assert len(copies) == 2 and second.json()['source_root'] != first.json()['source_root']
        third = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification', 'verify': True})
        assert third.json()['source_root'] == second.json()['source_root'], 'the verified copy is reused, not a third one'
        retry = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'},
                            headers={'Idempotency-Key': 'zip-copy'})
        assert retry.status_code == 200 and retry.json()['idempotent_replay'] is True and retry.json()['job_id'] == second.json()['job_id']
        assert sorted(path.name for path in imports.iterdir() if path.is_dir() and not path.name.startswith('.')) == copies
        other = _uploaded(client, _zip_bytes([('양품/c.png', _png_bytes('blue'))]))
        conflict = client.post('/api/dataset/imports/archive', json={'artifact': other, 'task': 'classification'},
                               headers={'Idempotency-Key': 'zip-copy'})
        assert conflict.status_code == 409, conflict.text


def test_the_import_view_says_which_source_it_read(api, tmp_path, monkeypatch):
    client, project, source = api
    view = client.post('/api/dataset/imports', json={'task': 'classification'}).json()
    assert view['source']['artifact'] is None and Path(view['source']['root']).resolve() == source.resolve()
    assert _finished(client, view['job_id'])['source']['artifact'] is None


def test_the_same_key_with_other_import_settings_is_a_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    import backend.main as main
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        client.post('/api/project/create', json={'name': 'Archive keys', 'task': 'classification'})
        ref = _uploaded(client, _zip_bytes([('양품/a.png', _png_bytes())]))
        first = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'}, headers={'Idempotency-Key': 'k'})
        assert first.status_code == 200
        _finished(client, first.json()['job_id'])
        same = client.post('/api/dataset/imports/archive', json={'artifact': ref, 'task': 'classification'}, headers={'Idempotency-Key': 'k'})
        assert same.status_code == 200 and same.json()['idempotent_replay'] is True
        other = client.post('/api/dataset/imports/archive', headers={'Idempotency-Key': 'k'},
                            json={'artifact': ref, 'task': 'classification', 'invalid_policy': 'reject', 'verify': True})
        assert other.status_code == 409, other.text

"""S3-01 slice 3 over HTTP: durable imports, revision listing, explicit accept and paged revision images.

A minimal app with the production project-context middleware; user data in a temporary folder; synthetic images.
"""
import time

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

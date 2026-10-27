"""Source updates retain only server-validated local registration response metadata."""
import json
from pathlib import Path

from backend.main import create_app
from backend.local_host.projects import LocalProjectRegistry, install_local_projects
from fastapi.testclient import TestClient


def app_fixture(tmp_path, local=True):
    app = create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'auth'))
    owner = app.state.accounts.bootstrap('owner', 'long test password')
    session = app.state.accounts.login('owner', 'long test password')
    if local: install_local_projects(app, LocalProjectRegistry(tmp_path/'registrations'))
    return app, owner, TestClient(app, headers={'Authorization': 'Bearer '+session['token']})


def _registered(tmp_path, *, copy=False):
    from backend.api.routes_project import _load_project
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    first = app.state.local_projects.register(app, owner, original)
    if copy:
        from backend.engine.project_archive import create_archive, restore_archive
        archive = create_archive(original, tmp_path/'archives')
        project = _load_project(restore_archive(Path(archive['archive_path']), tmp_path/'restored'))
        registration = app.state.local_projects.register(app, owner, project)
    else:
        project, registration = original, first
    client.headers['X-Vision-Context'] = json.dumps(registration['context'])
    return app, owner, client, original, project, registration


def test_source_update_retains_authenticated_local_registration_only_in_response(tmp_path):
    app, owner, client, _, project, registration = _registered(tmp_path)
    source = Path(project['project_dir'])/'dataset'/'chosen'; source.mkdir()
    response = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['registration_id'] == registration['registration_id']
    assert updated['workspace_id'] == registration['workspace_id']
    assert updated['context'] == registration['context']
    assert updated['source_dataset_dir'] == str(source)
    saved = json.loads((Path(project['project_dir'])/'project.json').read_text())
    assert saved['source_dataset_dir'] == str(source)
    assert not {'registration_id', 'workspace_id', 'context', 'permission_key'} & saved.keys()


def test_registered_copy_update_never_rebinds_logical_permission_key_or_selection(tmp_path):
    app, owner, client, original, project, registration = _registered(tmp_path, copy=True)
    before = app.state.accounts.project_for(owner['id'])
    original_bytes = (Path(original['project_dir'])/'project.json').read_bytes()
    response = client.put('/api/project/update', json={'description': 'only restored copy'})
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated['id'] == original['id'] and updated['project_dir'] == project['project_dir']
    assert updated['registration_id'] == registration['registration_id']
    assert updated['workspace_id'] == registration['workspace_id']
    assert updated['context'] == registration['context']
    assert app.state.accounts.project_for(owner['id']) == before
    assert (Path(original['project_dir'])/'project.json').read_bytes() == original_bytes
    with app.state.accounts._db() as db:
        assert db.execute('SELECT path FROM projects WHERE id=?', (original['id'],)).fetchone()['path'] == original['project_dir']
        assert db.execute('SELECT path FROM projects WHERE id=?', (registration['permission_key'],)).fetchone()['path'] == project['project_dir']


def test_update_ignores_forged_response_metadata_and_never_stores_it(tmp_path):
    _, _, client, _, project, registration = _registered(tmp_path)
    response = client.put('/api/project/update', json={'name': 'safe name', 'registration_id': 'forged',
        'workspace_id': 'foreign', 'context': {'actor_id': 'foreign'}})
    assert response.status_code == 200, response.text
    assert response.json()['registration_id'] == registration['registration_id']
    assert response.json()['context'] == registration['context']
    saved = json.loads((Path(project['project_dir'])/'project.json').read_text())
    assert 'registration_id' not in saved and 'context' not in saved


def test_ordinary_shared_update_response_has_no_local_registration_metadata(tmp_path):
    _, _, client = app_fixture(tmp_path, local=False)
    project = client.post('/api/project/create', json={'name': 'shared'}).json()
    response = client.put('/api/project/update', json={'description': 'ordinary'})
    assert response.status_code == 200, response.text
    assert response.json()['id'] == project['id']
    assert not {'registration_id', 'workspace_id', 'context'} & response.json().keys()


def test_full_app_native_current_to_source_update_keeps_real_registration_response_only(tmp_path):
    from backend.main import create_app
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    from backend.tests.test_local_web_projects import NativeBoundary
    from fastapi.testclient import TestClient
    app = create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'auth'))
    owner = app.state.accounts.bootstrap('owner', 'long test password')
    session = app.state.accounts.login('owner', 'long test password')
    install_local_projects(app, LocalProjectRegistry(tmp_path/'registrations'))
    app.state.local_host = NativeBoundary()
    client = TestClient(app, headers={'Authorization': 'Bearer '+session['token'], 'X-Vision-Local-Tab': 'tab-one'})
    project = client.post('/api/project/create', json={'name': 'full app'}).json()
    current = client.get('/api/local-host/projects/current')
    assert current.status_code == 200, current.text
    registration = current.json()
    context = json.loads(current.headers['X-Vision-Context'])
    assert context['actor_id'] == owner['id'] and context['project_id'] == project['id']
    source = Path(project['project_dir'])/'dataset'/'copied-source'; source.mkdir()
    response = client.put('/api/project/update', headers={'X-Vision-Context': json.dumps(context)}, json={'source_dataset_dir': str(source)})
    assert response.status_code == 200, response.text
    assert json.loads(response.headers['X-Vision-Context']) == context
    for key in ('registration_id', 'workspace_id', 'context'):
        assert response.json()[key] == registration[key]
    saved = json.loads((Path(project['project_dir'])/'project.json').read_text())
    assert saved['source_dataset_dir'] == str(source)
    assert not {'registration_id', 'workspace_id', 'context', 'permission_key'} & saved.keys()


import pytest
from contextlib import contextmanager


@pytest.mark.parametrize('change,expected', [('session', 401), ('role', 403), ('account_path', 409), ('physical_location', 409)])
def test_registration_authority_is_rechecked_before_manifest_write(tmp_path, monkeypatch, change, expected):
    from backend.api import routes_project
    app, owner, client, _, project, registration = _registered(tmp_path)
    root = Path(project['project_dir'])
    before = (root/'project.json').read_bytes()
    original = routes_project._project_context_mutation

    @contextmanager
    def changed_after_initial_authorization(selected):
        with original(selected) as current:
            if change == 'session':
                app.state.accounts.logout(client.headers['Authorization'].removeprefix('Bearer '))
            elif change == 'role':
                with app.state.accounts._db() as db:
                    db.execute("UPDATE members SET role='viewer' WHERE user_id=? AND project_id=?", (owner['id'], registration['permission_key']))
            elif change == 'account_path':
                with app.state.accounts._db() as db:
                    db.execute('UPDATE projects SET path=? WHERE id=?', (str(tmp_path/'foreign'), registration['permission_key']))
            else:
                import shutil
                preserved = tmp_path/'preserved-location'
                root.rename(preserved)
                shutil.copytree(preserved, root)
            yield current

    monkeypatch.setattr(routes_project, '_project_context_mutation', changed_after_initial_authorization)
    response = client.put('/api/project/update', json={'description': 'must not save'})
    assert response.status_code == expected, response.text
    assert (root/'project.json').read_bytes() == before

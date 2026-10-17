"""A source-registration administrator may inspect one candidate without adopting it."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.tests.test_service_s1_07 import PASSWORD, team


@pytest.fixture
def candidate_source(team, tmp_path):
    _, _, owner, project, _, _ = team
    old, candidate = tmp_path / 'old-source', tmp_path / 'candidate-source'
    for root in (old, candidate):
        (root / 'OK').mkdir(parents=True)
        Image.new('RGB', (16, 16), 'white').save(root / 'OK/one.png')
    registered = owner.put('/api/project/update', json={
        'task': 'classification', 'source_dataset_dir': str(old)})
    assert registered.status_code == 200, registered.text
    manifest = Path(project['project_dir']) / 'project.json'
    return old, candidate, manifest, manifest.read_bytes()


def test_admin_owner_inspects_new_folder_without_registering_it(team, candidate_source):
    _, _, owner, _, _, _ = team
    old, candidate, manifest, before = candidate_source
    response = owner.post('/api/dataset/import', json={
        'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 200, response.text
    assert response.json()['total_images'] == 1
    assert manifest.read_bytes() == before
    assert owner.get('/api/project/current').json()['source_dataset_dir'] == str(old)


@pytest.mark.parametrize('failure', ['missing', 'incompatible-labelme'])
def test_failed_candidate_inspection_preserves_registered_source(team, candidate_source, tmp_path, failure):
    _, _, owner, _, _, _ = team
    old, _, manifest, before = candidate_source
    candidate = tmp_path / failure
    expected = 404
    if failure == 'incompatible-labelme':
        candidate.mkdir()
        Image.new('RGB', (16, 16), 'white').save(candidate / 'part.png')
        (candidate / 'part.json').write_text(json.dumps({
            'imagePath': 'part.png', 'imageWidth': 16, 'imageHeight': 16,
            'shapes': [{'label': 'defect', 'shape_type': 'rectangle', 'points': [[1, 1], [4, 4]]}]}))
        expected = 422
    response = owner.post('/api/dataset/import', json={
        'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == expected, response.text
    assert manifest.read_bytes() == before
    assert owner.get('/api/project/current').json()['source_dataset_dir'] == str(old)


@pytest.mark.parametrize('role', ['owner', 'labeler', 'trainer', 'reviewer', 'viewer'])
def test_nonadministrator_roles_cannot_inspect_an_unregistered_source(team, candidate_source, role):
    app, admin, _, project, users, clients = team
    old, candidate, manifest, before = candidate_source
    app.state.accounts.set_membership(project['id'], users['targetuser']['id'], role, admin['id'])
    client = clients['targetuser']
    assert client.post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
    response = client.post('/api/dataset/import', json={
        'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 403, response.text
    if role == 'owner':
        registered = client.post('/api/dataset/import', json={'folder_path': str(old), 'task': 'classification'})
        assert registered.status_code == 200, registered.text
    assert manifest.read_bytes() == before


@pytest.mark.parametrize('membership', [None, 'viewer'])
def test_administrator_still_requires_project_owner_authority(team, candidate_source, membership):
    app, admin, _, project, users, clients = team
    _, candidate, manifest, before = candidate_source
    if membership:
        app.state.accounts.set_membership(project['id'], users['serveradmin']['id'], membership, admin['id'])
    response = clients['serveradmin'].post('/api/dataset/import',
        headers={'X-Vision-Project': project['id']},
        json={'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 403, response.text
    assert manifest.read_bytes() == before


@pytest.mark.parametrize('selector', ['nested', 'list', 'other-field', 'query'])
def test_candidate_permission_does_not_exempt_other_or_nested_selectors(team, candidate_source, tmp_path, selector):
    _, _, owner, _, _, _ = team
    _, candidate, manifest, before = candidate_source
    outside = str(tmp_path / 'unregistered-private-folder')
    body = {'folder_path': str(candidate), 'task': 'classification'}
    params = None
    if selector == 'nested':
        body['ignored'] = {'folder_path': outside}
    elif selector == 'list':
        body['ignored'] = [{'folder_path': outside}]
    elif selector == 'other-field':
        body['output_dir'] = outside
    else:
        params = {'folder_path': outside}
    response = owner.post('/api/dataset/import', json=body, params=params)
    assert response.status_code == 403, response.text
    assert manifest.read_bytes() == before


@pytest.mark.parametrize('source', ['workspace', 'workspace-parent'])
def test_candidate_cannot_contain_the_project_workspace(team, candidate_source, source):
    _, _, owner, project, _, _ = team
    _, _, manifest, before = candidate_source
    candidate = Path(project['project_dir'])
    if source == 'workspace-parent':
        candidate = candidate.parent
    response = owner.post('/api/dataset/import', json={'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 403, response.text
    assert manifest.read_bytes() == before


def test_candidate_permission_does_not_extend_to_industrial_import(team, candidate_source):
    _, _, owner, _, _, _ = team
    _, candidate, manifest, before = candidate_source
    response = owner.post('/api/dataset/import-industrial', json={
        'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 403, response.text
    assert manifest.read_bytes() == before


def test_cookie_candidate_inspection_still_requires_csrf(team, candidate_source):
    app, _, _, _, _, _ = team
    _, candidate, manifest, before = candidate_source
    browser = TestClient(app, base_url='https://fixture.test')
    logged = browser.post('/api/accounts/login', headers={'Origin': 'https://fixture.test'},
        json={'username': 'administrator', 'password': PASSWORD, 'transport': 'cookie'})
    assert logged.status_code == 200, logged.text
    body = {'folder_path': str(candidate), 'task': 'classification'}
    assert browser.post('/api/dataset/import', headers={'Origin': 'https://fixture.test'}, json=body).status_code == 403
    accepted = browser.post('/api/dataset/import',
        headers={'Origin': 'https://fixture.test', 'X-Vision-CSRF': logged.json()['csrf_token']}, json=body)
    assert accepted.status_code == 200, accepted.text
    assert manifest.read_bytes() == before


def test_revoked_session_cannot_use_candidate_permission(team, candidate_source):
    _, _, owner, _, _, _ = team
    _, candidate, manifest, before = candidate_source
    assert owner.post('/api/accounts/logout').status_code == 200
    response = owner.post('/api/dataset/import', json={'folder_path': str(candidate), 'task': 'classification'})
    assert response.status_code == 401, response.text
    assert manifest.read_bytes() == before

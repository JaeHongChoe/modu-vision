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


def _source_precondition(project):
    return {'project_id': project['id'], 'source_dataset_dir': project['source_dataset_dir'],
            'task': project['task'], 'annotations_dir': project['annotations_dir'],
            'active_labelset_id': project['active_labelset_id']}


@pytest.mark.parametrize('changed', ['source', 'task', 'labelset', 'project_id', 'annotations_dir', 'active_labelset_id'])
def test_conditional_source_update_refuses_changed_context_without_publishing(team, changed):
    _, _, owner, project, _, _ = team
    workspace = Path(project['project_dir'])
    old, concurrent, picked = (workspace / 'dataset_imports' / name for name in ('old', 'concurrent', 'picked'))
    for folder in (old, concurrent, picked):
        folder.mkdir(parents=True)
    registered = owner.put('/api/project/update', json={
        'source_dataset_dir': str(old), 'task': 'classification'}).json()
    expected = _source_precondition(registered)
    old_label = Path(registered['annotations_dir']) / 'kept.json'
    old_label.write_bytes(b'{"old_label": true}\n')
    if changed == 'source':
        assert owner.put('/api/project/update', json={'source_dataset_dir': str(concurrent)}).status_code == 200
    elif changed == 'task':
        assert owner.put('/api/project/update', json={'task': 'segmentation'}).status_code == 200
    elif changed == 'labelset':
        added = owner.post('/api/project/labelsets', json={'name': 'Concurrent labels'})
        assert added.status_code == 200, added.text
        assert owner.put(f"/api/project/labelsets/{added.json()['id']}/activate").status_code == 200
    else:
        expected[changed] = str(workspace / 'other-annotations') if changed == 'annotations_dir' else 'another-context'
    current = owner.get('/api/project/current').json()
    manifest = workspace / 'project.json'
    before = manifest.read_bytes()

    response = owner.put('/api/project/update', json={
        'source_dataset_dir': str(picked), 'description': 'must not publish', 'expected_source': expected})

    assert response.status_code == 409, response.text
    assert '바뀌었습니다' in response.json()['detail']
    assert manifest.read_bytes() == before
    assert owner.get('/api/project/current').json() == current
    assert old_label.read_bytes() == b'{"old_label": true}\n'


@pytest.mark.parametrize('has_source', [False, True])
def test_matching_source_precondition_saves_canonical_source_without_metadata(team, has_source):
    _, _, owner, project, _, _ = team
    workspace = Path(project['project_dir'])
    old, picked = workspace / 'dataset_imports' / 'old', workspace / 'dataset_imports' / 'picked'
    for folder in (old, picked):
        folder.mkdir(parents=True)
    if has_source:
        assert owner.put('/api/project/update', json={'source_dataset_dir': str(old)}).status_code == 200
    current = owner.get('/api/project/current').json()
    old_label = Path(current['annotations_dir']) / 'kept.json'
    old_label.write_bytes(b'{"old_label": true}\n')

    response = owner.put('/api/project/update', json={
        'source_dataset_dir': str(picked / '..' / 'picked'),
        'expected_source': _source_precondition(current)})

    assert response.status_code == 200, response.text
    assert response.json()['source_dataset_dir'] == str(picked.resolve())
    assert 'expected_source' not in response.json()
    saved = json.loads((workspace / 'project.json').read_bytes())
    assert 'expected_source' not in saved and saved['source_dataset_dir'] == str(picked.resolve())
    assert old.is_dir() and old_label.read_bytes() == b'{"old_label": true}\n'
    # Other callers keep the existing unconditional source-update behavior.
    legacy = owner.put('/api/project/update', json={'source_dataset_dir': str(old)})
    assert legacy.status_code == 200 and legacy.json()['source_dataset_dir'] == str(old)


def test_source_precondition_requires_all_strict_fields_and_forbids_extra_fields(team):
    _, _, owner, project, _, _ = team
    current = owner.get('/api/project/current').json()
    expected = _source_precondition(current)
    malformed = [dict(expected, project_id=3), dict(expected, unrecognized='value'),
                 {key: value for key, value in expected.items() if key != 'source_dataset_dir'}]
    manifest = Path(project['project_dir']) / 'project.json'
    before = manifest.read_bytes()
    for precondition in malformed:
        response = owner.put('/api/project/update', json={
            'description': 'must not publish', 'expected_source': precondition})
        assert response.status_code == 422, response.text
        assert manifest.read_bytes() == before


def test_source_precondition_does_not_grant_project_write_or_path_authority(team, tmp_path):
    app, admin, owner, project, users, clients = team
    current = owner.get('/api/project/current').json()
    manifest = Path(project['project_dir']) / 'project.json'
    before = manifest.read_bytes()
    body = {'source_dataset_dir': str(Path(project['project_dir']) / 'dataset_imports' / 'picked'),
            'expected_source': _source_precondition(current)}
    for role in ('viewer', 'labeler', 'trainer', 'reviewer'):
        app.state.accounts.set_membership(project['id'], users['targetuser']['id'], role, admin['id'])
        client = clients['targetuser']
        assert client.post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
        assert client.put('/api/project/update', json=body).status_code == 403
        assert manifest.read_bytes() == before
    body['expected_source']['source_dataset_dir'] = str(tmp_path / 'unregistered-private-folder')
    assert clients['projectowner'].put('/api/project/update', json=body).status_code == 403
    assert manifest.read_bytes() == before

"""Catalog metadata and admission, against the real factory without lifespan work."""
import asyncio

import httpx
import pytest
from fastapi import APIRouter
from pydantic import BaseModel

CATALOG = '/api/automation/catalog'


@pytest.fixture
def app(tmp_path):
    from backend.main import create_app
    return create_app(project_dir=str(tmp_path / 'projects'))


def request(app, method='GET', path=CATALOG, *, authenticated=True, params=None):
    async def run():
        headers = {'x-vision-token': app.state.api_token} if authenticated else {}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            return await client.request(method, path, headers=headers, params=params)
    return asyncio.run(run())


def test_catalog_requires_desktop_auth_and_never_selects_or_creates_project(app):
    baseline = set(app.state.project_dir.iterdir())
    assert request(app, authenticated=False).status_code == 401
    response = request(app)
    assert response.status_code == 200
    assert response.json()['schema'] == 'StudioOperations/v1'
    assert getattr(app.state, 'current_project', None) is None
    assert set(app.state.project_dir.iterdir()) == baseline
    assert app.state.api_token not in response.text
    assert str(app.state.project_dir) not in response.text


@pytest.mark.parametrize(('method', 'path'), [
    ('POST', CATALOG), ('GET', CATALOG + '-extra'), ('GET', CATALOG + '/child'), ('HEAD', CATALOG),
])
def test_catalog_lookalikes_keep_desktop_project_selection(app, method, path):
    request(app, method, path)
    # Existing desktop policy initializes its default project; bypassing it would leave None.
    assert getattr(app.state, 'current_project', None) is not None


def test_shared_catalog_authenticates_but_exact_get_does_not_select_project(tmp_path):
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'accounts'))
    baseline = set(app.state.project_dir.iterdir())
    store = app.state.accounts
    store.bootstrap('catalog-admin', 'owned-password-123')
    token = store.login('catalog-admin', 'owned-password-123')['token']
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned.invalid') as client:
            assert (await client.get(CATALOG)).status_code == 401
            headers = {'authorization': 'Bearer ' + token}
            response = await client.get(CATALOG, headers=headers)
            assert response.status_code == 200
            assert token not in response.text
            for method, path in [('POST', CATALOG), ('HEAD', CATALOG), ('GET', CATALOG+'-extra'), ('GET', CATALOG+'/child')]:
                assert (await client.request(method, path, headers=headers)).status_code == 409
    asyncio.run(run())
    assert getattr(app.state, 'current_project', None) is None
    assert set(app.state.project_dir.iterdir()) == baseline


class CatalogLeaf(BaseModel):
    label: str


class CatalogNested(BaseModel):
    leaf: CatalogLeaf


def test_catalog_covers_actual_openapi_lazy_and_new_local_host_routes(app):
    # Nested included routers are lazy in supported FastAPI releases.
    nested = APIRouter(prefix='/api/catalog-fixture')
    @nested.post('/nested/{item_id}', response_model=CatalogNested)
    def fixture(item_id: int, body: CatalogNested, enabled: bool = False):
        raise AssertionError('Metadata must never execute a handler')
    parent = APIRouter()
    parent.include_router(nested)
    app.include_router(parent)
    before = request(app, params={'limit': 1000}).json()
    assert before.get('operations'), before
    # Add a native route after the schema has already been consumed.
    @app.get('/api/local-host/catalog-fixture')
    def native_fixture():
        raise AssertionError('Metadata must never execute native handlers')
    response = request(app, params={'limit': 1000})
    assert response.status_code == 200
    value = response.json()
    expected = {method.upper()+' '+path for path, methods in app.openapi()['paths'].items()
                if path.startswith('/api/') or path == '/health'
                for method in methods if method in {'get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace'}}
    assert {row['id'] for row in value['operations']} == expected
    assert len(expected) == value['total']
    assert sum(group['count'] for group in value['groups']) == value['total']
    assert value['next_offset'] is None
    assert value['components'] == {'schemas': {}}
    assert all('input' not in row and 'responses' not in row for row in value['operations'])
    native = next(row for row in value['operations'] if row['id'] == 'GET /api/local-host/catalog-fixture')
    assert native['transport'] == 'native' and native['assistant'] == {'mode': 'manual'}
    detail = request(app, params={'operation':'POST /api/catalog-fixture/nested/{item_id}'}).json()
    row = detail['operations'][0]
    assert row['input']['parameters'][0]['name'] == 'item_id'
    assert row['input']['request_body']['content']['application/json']['schema']['$ref'].endswith('/CatalogNested')
    assert row['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/CatalogNested')
    assert {'CatalogNested', 'CatalogLeaf'} <= detail['components']['schemas'].keys()
    assert 'ProjectCreateRequest' not in detail['components']['schemas']
    without = request(app, params={'operation':row['id'], 'schemas':False}).json()
    assert without['components'] == {'schemas': {}}


def test_catalog_filters_paginates_and_validates_queries(app):
    value = request(app, params={'group': 'project', 'limit': 1}).json()
    assert len(value['operations']) == 1
    assert value['operations'][0]['group'] == 'project'
    assert value['total'] > 1 and value['next_offset'] == 1
    second = request(app, params={'group':'project', 'offset':1, 'limit':1}).json()
    assert second['operations'][0]['id'] != value['operations'][0]['id']
    for params in [{'group':'missing'}, {'operation':'GET /api/no-such-operation'}]:
        assert request(app, params=params).status_code == 404
    for params in [{'offset':-1}, {'limit':0}, {'limit':1001}]:
        assert request(app, params=params).status_code == 422


def test_only_exact_observers_are_safe_and_existing_task_mappings_do_not_grant_writes(app):
    value = request(app, params={'limit':1000}).json()
    operations = {row['id']:row for row in value.get('operations', [])}
    assert 'GET /health' in operations
    for identifier in ['GET /health', 'GET /api/training-workspace/tasks', 'GET /api/models/capabilities']:
        assert operations[identifier]['assistant'] == {'mode':'read'}
        assert operations[identifier]['effect'] == 'read'
    assert operations['GET /api/evaluation/results']['effect'] == 'execute'
    assert operations['GET /api/evaluation/results']['assistant'] == {'mode':'task', 'task_action':'evaluate_model'}
    for identifier in ['GET /api/evaluation/heatmap/{image_id}', 'GET /api/evaluation/overkill-underkill']:
        assert operations[identifier]['effect'] == 'execute'
        assert operations[identifier]['assistant'] == {'mode':'manual'}
    assert operations['POST /api/training/start']['assistant'] == {'mode':'task', 'task_action':'start_training'}
    assert operations['POST /api/project/create']['assistant'] == {'mode':'task', 'task_action':'create_project'}
    assert operations['POST /api/training/stop']['assistant'] == {'mode':'manual'}
    assert operations['POST /api/model-deployments/approve']['assistant'] == {'mode':'manual'}


def test_manual_request_bodies_are_explicit_without_inventing_bodies_for_other_writes(app):
    upload = request(app, params={'operation':'PUT /api/artifacts/uploads/{upload_id}'}).json()['operations'][0]
    body = upload['input'].get('request_body')
    assert body is not None, 'Streaming artifact upload requires a catalog body contract'
    assert body['required'] is True
    schema = body['content']['application/octet-stream']['schema']
    assert schema == {'type':'string', 'format':'binary', 'maxLength':4194304}
    assert upload['assistant'] == {'mode':'manual'} and upload['effect'] == 'execute'
    for operation in ['POST /api/assistant-tasks', 'POST /api/assistant-tasks/{task_id}/events',
                      'POST /api/assistant-tasks/{task_id}/quality']:
        detail = request(app, params={'operation':operation}).json()['operations'][0]
        assert detail['input']['request_body']['required'] is True
        assert detail['input']['request_body']['content']['application/json']['schema']['type'] == 'object'
    complete = request(app, params={'operation':'POST /api/artifacts/uploads/{upload_id}/complete'}).json()['operations'][0]
    assert 'request_body' not in complete['input']


def test_path_converter_metadata_comes_from_actual_effective_routes(app):
    raw = request(app, params={'operation':'GET /api/dataset/raw/{image_path}'}).json()['operations'][0]
    assert raw['input'].get('path_converters') == {'image_path':'path'}
    single = request(app, params={'operation':'GET /api/annotations/{image_id}'}).json()['operations'][0]
    assert single['input']['path_converters'] == {'image_id':'str'}
    nested = APIRouter(prefix='/objects/{bucket:int}')
    @nested.get('/{object_path:path}')
    def fixture(bucket: int, object_path: str):
        raise AssertionError('Catalog discovery must not invoke a route')
    parent = APIRouter(prefix='/api/path-fixture')
    parent.include_router(nested)
    app.include_router(parent)
    detail = request(app, params={'operation':'GET /api/path-fixture/objects/{bucket}/{object_path}'}).json()['operations'][0]
    assert detail['input']['path_converters'] == {'bucket':'int', 'object_path':'path'}


def test_installed_native_manual_bodies_remain_native_and_manual(app):
    from types import SimpleNamespace
    from backend.local_host.remote import install_remote
    controller = SimpleNamespace(resource_epochs={})
    app.include_router(install_remote(controller))
    for operation in ['POST /api/local-host/remote/login', 'POST /api/local-host/remote/{connection_id}/context']:
        row = request(app, params={'operation':operation}).json()['operations'][0]
        assert row['input'].get('request_body', {}).get('required') is True
        assert row['input']['request_body']['content']['application/json']['schema']['type'] == 'object'
        assert row['transport'] == 'native' and row['assistant'] == {'mode':'manual'}
    proxy = request(app, params={'operation':'POST /api/local-host/remote/{connection_id}/api/{path}'}).json()['operations'][0]
    assert proxy['input']['path_converters'] == {'connection_id':'str', 'path':'path'}
    assert proxy['input']['request_body']['required'] is False
    assert {'application/json', 'application/octet-stream'} <= proxy['input']['request_body']['content'].keys()


def test_installed_asset_download_declares_exact_form_body(app):
    from backend.local_host.project_assets import install_project_assets
    install_project_assets(app, app.state.context_registry)
    row = request(app, params={'operation':'POST /api/local-host/project-assets/download'}).json()['operations'][0]
    body = row['input'].get('request_body')
    assert body is not None, 'Cookie download middleware consumes a required URL-encoded form'
    schema = body['content']['application/x-www-form-urlencoded']['schema']
    assert set(schema['required']) == {'ticket', 'csrf_token', 'kind', 'export_id'}
    assert body['required'] is True
    assert row['transport'] == 'native' and row['assistant'] == {'mode':'manual'}


def test_current_project_get_is_executable_because_it_can_initialize_default_selection(app):
    row = request(app, params={'operation':'GET /api/project/current'}).json()['operations'][0]
    assert getattr(app.state, 'current_project', None) is None
    assert row['effect'] == 'execute'
    assert row['assistant'] == {'mode':'manual'}
    response = request(app, path='/api/project/current')
    assert response.status_code == 200
    assert app.state.current_project['id'] == response.json()['id']
    assert (app.state.project_dir / 'default_project' / 'project.json').is_file()

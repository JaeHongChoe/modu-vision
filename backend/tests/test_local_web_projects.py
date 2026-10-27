"""Local registrations must preserve manifest identities without sharing authority."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.engine.shared_accounts import AccountStore
from backend.contracts.context import ContextRegistry
from backend.api.shared_authorization import SharedAuthorizationMiddleware
from backend.api.routes_project import router as project_router


def app_fixture(tmp_path, local=True):
    app = FastAPI()
    app.state.project_dir = str(tmp_path / 'projects')
    app.state.api_token = 'test-only'
    app.state.context_registry = ContextRegistry(tmp_path / 'projects')
    app.state.accounts = AccountStore(tmp_path / 'accounts.sqlite')
    owner = app.state.accounts.bootstrap('owner', 'long test password')
    token = app.state.accounts.login('owner', 'long test password')['token']
    app.include_router(project_router)
    app.add_middleware(SharedAuthorizationMiddleware, project_app=app)
    if local:
        import backend.local_host.projects as projects
        registry = projects.LocalProjectRegistry(tmp_path / 'local')
        projects.install_local_projects(app, registry)
    return app, owner, TestClient(app, headers={'Authorization': 'Bearer ' + token})


def test_local_registration_component_exists():
    # Missing explicit resolver leaves restored copies rejected by AccountStore.
    import importlib.util
    assert importlib.util.find_spec('backend.local_host') is not None
    assert importlib.util.find_spec('backend.local_host.projects') is not None


def test_ordinary_shared_open_and_external_backup_remain_denied(tmp_path):
    app, owner, client = app_fixture(tmp_path, local=False)
    first = client.post('/api/project/create', json={'name': 'original'}).json()
    assert client.post('/api/project/open', json={'project_dir': first['project_dir']}).status_code == 403
    assert client.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')}).status_code == 403


def test_same_id_locations_have_distinct_contexts_and_legacy_default(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.api.routes_project import _load_project
    from backend.engine.project_archive import create_archive, restore_archive
    backup = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(backup['archive_path']), tmp_path / 'restored'))
    registry = app.state.local_projects
    first = registry.register(app, owner, original)
    second = registry.register(app, owner, restored)
    assert first['project_id'] == second['project_id'] == original['id']
    assert first['registration_id'] != second['registration_id']
    assert first['workspace_id'] != second['workspace_id']
    assert app.state.accounts.project_for(owner['id'], original['id'])['path'] == original['project_dir']
    assert app.state.accounts.project_for(owner['id'])['path'] == original['project_dir']
    for row in (first, second):
        response = client.get('/api/project/current', headers={'X-Vision-Context': json.dumps(row['context'])})
        assert response.status_code == 200, response.text
        assert response.json()['project_dir'] == row['path']


class NativeBoundary:
    """Only native chooser/owner consent is substituted; accounts and file I/O are real."""
    def __init__(self):
        self.paths = set()

    def authorize(self, request, action, *, project=False):
        account = request.app.state.accounts.authenticate(request.state.account_session_token)
        if not request.headers.get('x-vision-local-tab'):
            from fastapi import HTTPException
            raise HTTPException(403, 'Tab required')
        return {'account': account, 'actor_id': account['id'], 'tab_id': request.headers['x-vision-local-tab']}

    def require_grants(self, request, paths, project=None):
        from fastapi import HTTPException
        if not all(str(Path(path).resolve()) in self.paths for key, path in paths):
            raise HTTPException(403, 'Native selection required')
        for key, path in paths:
            self.paths.remove(str(Path(path).resolve()))

    def commit_grants(self, scope):
        pass


def native_fixture(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    app.state.local_host = NativeBoundary()
    client.headers['X-Vision-Local-Tab'] = 'tab-one'
    return app, owner, client


def test_native_open_requires_grant_and_backup_restore_preserves_original(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    body = {'project_dir': original['project_dir']}
    assert client.post('/api/local-host/projects/open', json=body).status_code == 403
    app.state.local_host.paths.add(original['project_dir'])
    opened = client.post('/api/local-host/projects/open', json=body)
    assert opened.status_code == 200, opened.text
    context = opened.headers['x-vision-context']
    assert client.post('/api/local-host/projects/open', json=body).status_code == 403
    client.headers['X-Vision-Context'] = context
    destination = tmp_path / 'saved'; destination.mkdir()
    app.state.local_host.paths.add(str(destination))
    saved = client.post('/api/local-host/projects/backup', json={'destination_dir': str(destination)})
    assert saved.status_code == 200, saved.text
    assert saved.json()['source_bytes'] == 0
    assert saved.json()['source_included'] is False
    archive = Path(saved.json()['archive_path'])
    assert archive.is_file()
    target = tmp_path / 'restored'
    app.state.local_host.paths.update((str(archive), str(target)))
    restored = client.post('/api/local-host/projects/restore', json={'archive_path': str(archive), 'target_dir': str(target)})
    assert restored.status_code == 200, restored.text
    assert restored.json()['id'] == original['id']
    assert restored.headers['x-vision-context'] != context
    assert client.get('/api/project/current').json()['project_dir'] == original['project_dir']
    assert (Path(original['project_dir']) / 'project.json').is_file()


def test_restored_membership_is_not_inherited_from_original(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.api.routes_project import _load_project
    from backend.engine.project_archive import create_archive, restore_archive
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    registration = app.state.local_projects.register(app, owner, restored)
    member = app.state.accounts.create_user('member', 'another test password')
    app.state.accounts.set_membership(original['id'], member['id'], 'owner', owner['id'])
    session = app.state.accounts.login('member', 'another test password')
    context = {**registration['context'], 'actor_id': member['id']}
    member_client = TestClient(app, headers={'Authorization': 'Bearer ' + session['token'], 'X-Vision-Context': json.dumps(context)})
    assert member_client.get('/api/project/current').status_code == 403
    app.state.accounts.set_membership(registration['permission_key'], member['id'], 'viewer', owner['id'])
    assert member_client.get('/api/project/current').status_code == 200
    assert member_client.put('/api/project/update', json={'name': 'forbidden'}).status_code == 403
    app.state.accounts.remove_membership(registration['permission_key'], member['id'], owner['id'])
    assert member_client.get('/api/project/current').status_code == 403


def test_restore_register_failure_has_restart_recovery_without_reextract(tmp_path, monkeypatch):
    app, owner, client = native_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.engine.project_archive import create_archive
    archive = Path(create_archive(original, tmp_path / 'backups')['archive_path'])
    target = tmp_path / 'restored'
    app.state.local_host.paths.update((str(archive), str(target)))
    registry = app.state.local_projects
    def unavailable(*args, **kwargs):
        raise OSError('Injected registration database outage')
    monkeypatch.setattr(registry, 'register', unavailable)
    with pytest.raises(OSError, match='database outage'):
        client.post('/api/local-host/projects/restore', json={'archive_path': str(archive), 'target_dir': str(target)})
    assert (target / 'project.json').is_file()
    with registry.db() as db:
        row = dict(db.execute("SELECT * FROM operations WHERE kind='restore'").fetchone())
    assert row['phase'] == 'published'
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    # A new app models a backend restart; the account/context stores remain real.
    restarted = FastAPI(); restarted.state.accounts = app.state.accounts
    restarted.state.context_registry = app.state.context_registry
    restarted.state.api_token = 'test-only'; restarted.state.local_host = NativeBoundary()
    restarted.add_middleware(SharedAuthorizationMiddleware, project_app=restarted)
    install_local_projects(restarted, LocalProjectRegistry(registry.root))
    retry = TestClient(restarted, headers=dict(client.headers))
    recovered = retry.post('/api/local-host/projects/operations/' + row['id'] + '/recover')
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['id'] == original['id']
    assert recovered.json()['project_dir'] == str(target)
    # An unowned replacement at the same path cannot reuse a completed receipt.
    target.rename(tmp_path / 'preserved')
    target.mkdir(); (target / 'project.json').write_text((tmp_path / 'preserved' / 'project.json').read_text())
    assert retry.post('/api/local-host/projects/operations/' + row['id'] + '/recover').status_code == 409


def test_registered_location_replacement_is_not_authorized(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    registration = app.state.local_projects.register(app, owner, original)
    target = Path(original['project_dir'])
    target.rename(tmp_path / 'preserved')
    import shutil
    shutil.copytree(tmp_path / 'preserved', target)
    result = client.get('/api/project/current', headers={'X-Vision-Context': json.dumps(registration['context'])})
    assert result.status_code == 409


def test_stale_archive_hash_and_backup_source_are_rejected(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    destination = tmp_path / 'destination'; destination.mkdir()
    app.state.local_host.paths.add(str(destination))
    failed = client.post('/api/local-host/projects/backup', json={'destination_dir': str(destination), 'expected_source': '0' * 64})
    assert failed.status_code == 409
    assert list(destination.iterdir()) == []
    from backend.engine.project_archive import create_archive
    archive = Path(create_archive(original, tmp_path / 'backups')['archive_path'])
    target = tmp_path / 'restored'
    app.state.local_host.paths.update((str(archive), str(target)))
    failed = client.post('/api/local-host/projects/restore', json={'archive_path': str(archive), 'target_dir': str(target), 'expected_archive_sha256': '0' * 64})
    assert failed.status_code == 409
    assert not target.exists()


def test_native_create_and_context_permission_resolver_use_exact_location(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    created = client.post('/api/local-host/projects/create', json={'name': 'native project'})
    assert created.status_code == 200, created.text
    original = created.json()
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.api.routes_project import _load_project
    from backend.contracts.context import current_project_context, ProjectContext
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    registration = app.state.local_projects.register(app, owner, restored)
    member = app.state.accounts.create_user('member', 'another test password')
    app.state.accounts.set_membership(original['id'], member['id'], 'owner', owner['id'])
    app.state.accounts.set_membership(registration['permission_key'], member['id'], 'viewer', owner['id'])
    token = current_project_context.set(ProjectContext(**{**registration['context'], 'actor_id': member['id']}))
    try:
        assert app.state.accounts.project_role(member['id'], original['id']) == 'viewer'
        assert not app.state.accounts.authorize(member['id'], 'project.manage', original['id']).allowed
    finally:
        current_project_context.reset(token)


def test_legacy_account_default_context_is_reserved_before_first_copy_open(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    captured = {'workspace_id': app.state.context_registry.workspace_id, 'project_id': original['id'],
                'actor_id': owner['id'], 'mode': 'team'}
    from backend.api.routes_project import _load_project
    from backend.engine.project_archive import create_archive, restore_archive
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    registration = app.state.local_projects.register(app, owner, restored)
    assert registration['workspace_id'] != captured['workspace_id']
    result = client.get('/api/project/current', headers={'X-Vision-Context': json.dumps(captured)})
    assert result.status_code == 200, result.text
    assert result.json()['project_dir'] == original['project_dir']


def test_real_context_middleware_and_artifact_refs_isolate_restored_copies(tmp_path):
    from backend.main import ProjectContextMiddleware
    from backend.contracts.context import get_project_context, ArtifactRegistration
    app, owner, client = app_fixture(tmp_path)
    # Middleware is constructed before the first request; auth stays outermost.
    app.user_middleware.clear()
    app.add_middleware(ProjectContextMiddleware, project_app=app)
    app.add_middleware(SharedAuthorizationMiddleware, project_app=app)
    @app.get('/api/test-context')
    def inspect_context(request: Request):
        context = get_project_context(request)
        project = request.state.scoped_project
        return {'context': context.model_dump(), 'path': project['project_dir'],
                'role': request.app.state.accounts.project_role(request.state.account_user['id'], project['id'])}
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.api.routes_project import _load_project
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    first = app.state.local_projects.register(app, owner, original)
    second = app.state.local_projects.register(app, owner, restored)
    contexts = []
    for row in (first, second):
        result = client.get('/api/test-context', headers={'X-Vision-Context': json.dumps(row['context'])})
        assert result.status_code == 200, result.text
        assert result.json()['context'] == row['context']
        assert result.json()['path'] == row['path']
        contexts.append(result.json()['context'])
    from backend.contracts.context import ProjectContext
    registry = app.state.context_registry
    assert registry.project_key(ProjectContext(**contexts[0])) != registry.project_key(ProjectContext(**contexts[1]))


def test_telemetry_revocation_checks_restored_membership_even_without_request_context(tmp_path):
    from backend.api.websocket_telemetry import TelemetryBroadcaster
    from types import SimpleNamespace
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.api.routes_project import _load_project
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    registration = app.state.local_projects.register(app, owner, restored)
    member = app.state.accounts.create_user('member', 'another test password')
    app.state.accounts.set_membership(original['id'], member['id'], 'viewer', owner['id'])
    session = app.state.accounts.login('member', 'another test password')
    websocket = SimpleNamespace(scope={'app': app, 'state': {'account_user': member,
        'account_session_token': session['token'], 'scoped_project': restored,
        'account_project_key': registration['permission_key']}})
    assert not TelemetryBroadcaster()._can_receive(websocket, {'event': 'hardware_stats', 'data': {}})


def test_backup_rejects_replaced_destination_after_staging(tmp_path, monkeypatch):
    app, owner, client = native_fixture(tmp_path)
    client.post('/api/project/create', json={'name': 'original'})
    destination = tmp_path / 'destination'; destination.mkdir()
    app.state.local_host.paths.add(str(destination))
    from backend.engine.data_backup_job import DataBackupJobs
    original_run = DataBackupJobs.run
    def replace_after_run(self, job_id):
        result = original_run(self, job_id)
        destination.rename(tmp_path / 'preserved-destination'); destination.mkdir()
        return result
    monkeypatch.setattr(DataBackupJobs, 'run', replace_after_run)
    result = client.post('/api/local-host/projects/backup', json={'destination_dir': str(destination)})
    assert result.status_code == 409
    assert list(destination.iterdir()) == []


def test_native_create_accepts_selected_empty_folder_and_refuses_nonempty(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    target = tmp_path / 'new-project'; target.mkdir()
    app.state.local_host.paths.add(str(target))
    result = client.post('/api/local-host/projects/create', json={'name': 'Native', 'project_dir': str(target)})
    assert result.status_code == 200, result.text
    occupied = tmp_path / 'occupied'; occupied.mkdir(); (occupied / 'keep.txt').write_text('preserve')
    app.state.local_host.paths.add(str(occupied))
    assert client.post('/api/local-host/projects/create', json={'name': 'Native', 'project_dir': str(occupied)}).status_code == 409
    assert (occupied / 'keep.txt').read_text() == 'preserve'


def test_other_tab_cannot_recover_operation_receipt(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    result = client.post('/api/local-host/projects/create', json={'name': 'Native'})
    assert result.status_code == 200, result.text
    operation = result.json()['operation_id']
    assert client.get('/api/local-host/projects/operations/' + operation).status_code == 200
    response = client.post('/api/local-host/projects/operations/' + operation + '/recover', headers={'X-Vision-Local-Tab': 'tab-two'})
    assert response.status_code == 404


def test_real_local_host_cookie_grants_project_backup_restore_roundtrip(tmp_path):
    from backend.main import create_app
    from backend.contracts.authentication import configure_browser_origins
    from backend.local_host.controller import LocalHostController
    from backend.local_host.routes import local_host_router
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    origin = 'https://localhost:8443'
    app = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'auth'))
    configure_browser_origins(app, [origin])
    owner = app.state.accounts.bootstrap('owner', 'long test password')
    session = app.state.accounts.login('owner', 'long test password', transport='cookie')
    class Chooser:
        ready = {'platform': 'darwin'}
        closed = False
        path = None
        async def call(self, authority, method, args):
            if method == 'host.approve':
                return {'approved': True}
            if method in {'dialog.folder', 'dialog.file'}:
                return str(self.path)
            raise AssertionError(method)
    chooser = Chooser()
    controller = LocalHostController(app, chooser, origin=origin, state_dir=tmp_path / 'host-state')
    app.state.local_host = controller
    install_local_projects(app, LocalProjectRegistry(tmp_path / 'registrations'))
    app.include_router(local_host_router(controller))
    client = TestClient(app, base_url=origin, headers={'Origin': origin, 'X-Vision-CSRF': session['csrf_token']})
    client.cookies.set('vision_session', session['token'])
    tab = client.post('/api/local-host/session')
    assert tab.status_code == 200, tab.text
    client.headers['X-Vision-Local-Tab'] = tab.json()['tab_id']
    assert client.post('/api/local-host/approve', json={}).status_code == 200
    created = client.post('/api/local-host/projects/create', json={'name': 'Browser project'})
    assert created.status_code == 200, created.text
    original = created.json()
    captured = created.headers['x-vision-context']
    client.headers['X-Vision-Context'] = captured
    # The chooser proof belongs to captured A even when the selected folder is B.
    second = client.post('/api/local-host/projects/create', json={'name': 'Other project'})
    assert second.status_code == 200, second.text
    chooser.path = Path(second.json()['project_dir'])
    pick_b = client.post('/api/local-host/dialog/folder', json={'purpose': 'project-open'})
    assert pick_b.status_code == 200, pick_b.text
    opened_b = client.post('/api/local-host/projects/open', json={'project_dir': str(chooser.path)},
        headers={'X-Vision-Path-Grants': json.dumps([pick_b.json()['grant_id']])})
    assert opened_b.status_code == 200, opened_b.text
    assert opened_b.json()['id'] == second.json()['id']
    assert client.get('/api/project/current').json()['id'] == original['id']
    destination = tmp_path / 'exports'; destination.mkdir(); chooser.path = destination
    choice = client.post('/api/local-host/dialog/folder', json={'purpose': 'project-backup'})
    assert choice.status_code == 200, choice.text
    backup = client.post('/api/local-host/projects/backup', json={'destination_dir': str(destination)},
                         headers={'X-Vision-Path-Grants': json.dumps([choice.json()['grant_id']])})
    assert backup.status_code == 200, backup.text
    archive = Path(backup.json()['archive_path']); assert archive.is_file()
    chooser.path = archive
    source = client.post('/api/local-host/dialog/file', json={'purpose': 'project-restore-archive'})
    parent = tmp_path / 'restore-parent'; parent.mkdir(); chooser.path = parent
    choice = client.post('/api/local-host/dialog/folder', json={'purpose': 'project-restore-target'})
    target = parent / 'restored-project'
    restored = client.post('/api/local-host/projects/restore', json={'archive_path': str(archive), 'target_dir': str(target)},
        headers={'X-Vision-Path-Grants': json.dumps([source.json()['grant_id'], choice.json()['grant_id']])})
    assert restored.status_code == 200, restored.text
    assert restored.json()['id'] == original['id']
    assert restored.headers['x-vision-context'] != captured
    assert client.get('/api/project/current').json()['project_dir'] == original['project_dir']
    client.headers['X-Vision-Context'] = restored.headers['x-vision-context']
    assert client.get('/api/project/current').json()['project_dir'] == str(target)
    controller.tabs[client.headers['X-Vision-Local-Tab']]['approved'] = False
    refused = client.post('/api/project/backup', json={'destination_dir': str(destination)})
    assert refused.status_code == 403, refused.text
    app.state.accounts.logout(session['token'])
    assert client.get('/api/local-host/projects/current').status_code == 401


def test_uncertain_response_receipts_are_discoverable_only_in_originating_tab(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    created = client.post('/api/local-host/projects/create', json={'name': 'Native'})
    assert created.status_code == 200
    results = client.get('/api/local-host/projects/operations')
    assert results.status_code == 200, results.text
    assert results.json()['operations'][0]['id'] == created.json()['operation_id']
    assert results.json()['operations'][0]['phase'] == 'completed'
    foreign = client.get('/api/local-host/projects/operations', headers={'X-Vision-Local-Tab': 'tab-two'})
    assert foreign.json()['operations'] == []


def test_real_artifact_refs_and_backup_jobs_cannot_cross_same_id_locations(tmp_path):
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.api.routes_project import _load_project
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.contracts.context import ArtifactRegistration, ProjectContext
    from backend.engine.data_backup_job import DataBackupJobs
    from backend.engine.job_store import JobStore
    from fastapi import HTTPException
    (Path(original['reports_dir']) / 'report.txt').write_text('original')
    archive = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archive['archive_path']), tmp_path / 'restored'))
    first = app.state.local_projects.register(app, owner, original)
    second = app.state.local_projects.register(app, owner, restored)
    import hashlib
    registration = ArtifactRegistration(kind='result', relative_path='report.txt', sha256=hashlib.sha256(b'original').hexdigest())
    ref = app.state.context_registry.register_artifact(original, registration)
    assert app.state.context_registry.resolve_artifact(original, ref).read_text() == 'original'
    with pytest.raises(HTTPException) as denied:
        app.state.context_registry.resolve_artifact(restored, ref)
    assert denied.value.status_code == 404
    jobs = DataBackupJobs(JobStore(tmp_path / 'jobs.sqlite3'))
    a, b = ProjectContext(**first['context']), ProjectContext(**second['context'])
    ka, kb = app.state.context_registry.project_key(a), app.state.context_registry.project_key(b)
    job = jobs.submit(a, ka, original, 'test-backup')
    with pytest.raises(KeyError):
        jobs.view(job.id, kb, owner['id'])


def test_concurrent_tabs_register_one_location_without_duplicate_identity(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    app, owner, client = app_fixture(tmp_path)
    project = client.post('/api/project/create', json={'name': 'original'}).json()
    barrier = threading.Barrier(6)
    def register():
        barrier.wait()
        return app.state.local_projects.register(app, owner, project)['registration_id']
    with ThreadPoolExecutor(max_workers=6) as pool:
        registrations = list(pool.map(lambda _: register(), range(6)))
    assert len(set(registrations)) == 1


def test_native_current_returns_permission_denial_after_membership_revocation(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    created = client.post('/api/local-host/projects/create', json={'name': 'Native'}).json()
    member = app.state.accounts.create_user('member', 'another test password')
    app.state.accounts.set_membership(created['id'], member['id'], 'viewer', owner['id'])
    session = app.state.accounts.login('member', 'another test password')
    context = {**created['context'], 'actor_id': member['id']}
    other = TestClient(app, headers={'Authorization': 'Bearer ' + session['token'], 'X-Vision-Local-Tab': 'member-tab',
                                   'X-Vision-Context': json.dumps(context)})
    assert other.get('/api/local-host/projects/current').status_code == 200
    app.state.accounts.remove_membership(created['id'], member['id'], owner['id'])
    assert other.get('/api/local-host/projects/current').status_code == 403


def test_restart_new_session_discovers_and_recovers_only_verified_published_location(tmp_path, monkeypatch):
    app, owner, client = native_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'original'}).json()
    from backend.engine.project_archive import create_archive
    archive = Path(create_archive(original, tmp_path / 'backups')['archive_path'])
    target = tmp_path / 'restored'
    app.state.local_host.paths.update((str(archive), str(target)))
    registry = app.state.local_projects
    def unavailable(*args, **kwargs):
        raise OSError('Injected registration database outage')
    monkeypatch.setattr(registry, 'register', unavailable)
    with pytest.raises(OSError):
        client.post('/api/local-host/projects/restore', json={'archive_path': str(archive), 'target_dir': str(target)})
    old_token = client.headers['Authorization'].removeprefix('Bearer ')
    app.state.accounts.logout(old_token)
    # New account DB instance, registry instance, login, and tab after restart.
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    restarted = FastAPI(); restarted.state.accounts = AccountStore(tmp_path / 'accounts.sqlite')
    restarted.state.context_registry = ContextRegistry(tmp_path / 'projects')
    restarted.state.api_token = 'test-only'; restarted.state.local_host = NativeBoundary()
    restarted.add_middleware(SharedAuthorizationMiddleware, project_app=restarted)
    install_local_projects(restarted, LocalProjectRegistry(registry.root))
    session = restarted.state.accounts.login('owner', 'long test password')
    retry = TestClient(restarted, headers={'Authorization': 'Bearer ' + session['token'], 'X-Vision-Local-Tab': 'new-tab'})
    discover = retry.get('/api/local-host/projects/recovery')
    assert discover.status_code == 200, discover.text
    rows = discover.json()['operations']
    assert len(rows) == 1 and rows[0]['phase'] == 'published'
    assert rows[0]['requires_adoption'] is True
    assert 'payload' not in rows[0] and 'target' not in rows[0]
    operation = rows[0]['id']
    assert retry.post('/api/local-host/projects/recover', json={'operation_id': operation, 'target_dir': str(target)}).status_code == 403
    restarted.state.local_host.paths.add(str(target))
    recovered = retry.post('/api/local-host/projects/recover', json={'operation_id': operation, 'target_dir': str(target)})
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()['id'] == original['id']
    assert recovered.json()['project_dir'] == str(target)
    assert retry.get('/api/local-host/projects/recovery').json()['operations'] == []
    assert retry.get('/api/local-host/projects/operations/' + operation).status_code == 200


def test_recovery_rejects_manifest_tamper_and_foreign_actor(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    created = client.post('/api/local-host/projects/create', json={'name': 'Native'}).json()
    operation = created['operation_id']; target = Path(created['project_dir'])
    intruder = app.state.accounts.create_user('intruder', 'another test password')
    session = app.state.accounts.login('intruder', 'another test password')
    other = TestClient(app, headers={'Authorization': 'Bearer ' + session['token'], 'X-Vision-Local-Tab': 'intruder-tab'})
    assert other.get('/api/local-host/projects/recovery').json()['operations'] == []
    assert other.post('/api/local-host/projects/operations/' + operation + '/adopt', json={'confirmed': True}).status_code == 404
    (target / 'project.json').write_text((target / 'project.json').read_text().replace('Native', 'Tampered'))
    app.state.local_host.paths.add(str(target))
    response = client.post('/api/local-host/projects/recover', json={'operation_id': operation, 'target_dir': str(target)})
    assert response.status_code == 409


def test_actual_new_cookie_and_controller_after_restart_adopt_receipt_with_native_approval(tmp_path):
    from backend.main import create_app
    from backend.contracts.authentication import configure_browser_origins
    from backend.local_host.controller import LocalHostController
    from backend.local_host.routes import local_host_router
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    origin = 'https://localhost:8443'
    class Helper:
        ready = {'platform': 'darwin'}
        closed = False
        async def call(self, authority, method, args):
            assert method == 'host.approve'
            return {'approved': True}
    def boot(first=False):
        app = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'auth'))
        configure_browser_origins(app, [origin])
        if first: app.state.accounts.bootstrap('owner', 'long test password')
        session = app.state.accounts.login('owner', 'long test password', transport='cookie')
        controller = LocalHostController(app, Helper(), origin=origin, state_dir=tmp_path / 'host-state')
        app.state.local_host = controller
        install_local_projects(app, LocalProjectRegistry(tmp_path / 'registrations'))
        app.include_router(local_host_router(controller))
        client = TestClient(app, base_url=origin, headers={'Origin': origin, 'X-Vision-CSRF': session['csrf_token']})
        client.cookies.set('vision_session', session['token'])
        tab = client.post('/api/local-host/session')
        assert tab.status_code == 200, tab.text
        client.headers['X-Vision-Local-Tab'] = tab.json()['tab_id']
        return app, client, session
    app, client, session = boot(first=True)
    assert client.post('/api/local-host/approve', json={}).status_code == 200
    created = client.post('/api/local-host/projects/create', json={'name': 'Recoverable'})
    assert created.status_code == 200, created.text
    operation = created.json()['operation_id']
    with app.state.local_projects.db() as db:
        db.execute("UPDATE operations SET phase='published' WHERE id=?", (operation,))
    app.state.accounts.logout(session['token'])
    restarted, retry, next_session = boot()
    assert next_session['token'] != session['token']
    assert retry.headers['X-Vision-Local-Tab'] != client.headers['X-Vision-Local-Tab']
    rows = retry.get('/api/local-host/projects/recovery').json()['operations']
    assert rows[0]['id'] == operation and rows[0]['requires_adoption'] is True
    route = '/api/local-host/projects/operations/' + operation + '/adopt'
    assert retry.post(route, json={'confirmed': True}).status_code == 403
    assert retry.post('/api/local-host/approve', json={}).status_code == 200
    adopted = retry.post(route, json={'confirmed': True})
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()['phase'] == 'published'  # metadata only; no restore or create rerun
    assert retry.get('/api/local-host/projects/operations/' + operation).status_code == 200
    # Removal from an already-registered recovered target must deny adoption.
    original_owner = next_session['user']['id']
    other = restarted.state.accounts.create_user('other-owner', 'another test password')
    restarted.state.accounts.set_membership(created.json()['id'], other['id'], 'owner', original_owner)
    restarted.state.accounts.remove_membership(created.json()['id'], original_owner, other['id'])
    assert retry.post(route, json={'confirmed': True}).status_code == 403


def test_native_open_cannot_migrate_another_accounts_project_before_membership_check(tmp_path):
    app, owner, client = native_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'Private'}).json()
    manifest = Path(original['project_dir']) / 'project.json'
    value = json.loads(manifest.read_text()); value.pop('schema_version', None)
    manifest.write_text(json.dumps(value)); before = manifest.read_bytes()
    outsider = app.state.accounts.create_user('outsider', 'another test password')
    session = app.state.accounts.login('outsider', 'another test password')
    other = TestClient(app, headers={'Authorization': 'Bearer ' + session['token'], 'X-Vision-Local-Tab': 'outsider-tab'})
    app.state.local_host.paths.add(original['project_dir'])
    response = other.post('/api/local-host/projects/open', json={'project_dir': original['project_dir']})
    assert response.status_code == 403
    assert manifest.read_bytes() == before


import shutil
def test_restored_only_member_must_not_resolve_original_account_path(tmp_path):
    from backend.api.routes_project import _load_project
    from backend.api.routes_flowchart import _registered_retained_source
    from backend.engine.project_archive import create_archive,restore_archive
    from backend.contracts.context import current_project_context,ProjectContext
    app,owner,client=app_fixture(tmp_path)
    original=client.post('/api/project/create',json={'name':'original'}).json()
    source=tmp_path/'source';source.mkdir()
    result=client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    assert result.status_code==200,result.text
    original=result.json()
    archive=create_archive(original,tmp_path/'backups')
    restored=_load_project(restore_archive(Path(archive['archive_path']),tmp_path/'restored'))
    registration=app.state.local_projects.register(app,owner,restored)
    member=app.state.accounts.create_user('copy-only','another test password')
    app.state.accounts.set_membership(registration['permission_key'],member['id'],'trainer',owner['id'])
    session=app.state.accounts.login('copy-only','another test password')
    assert app.state.accounts.project_role(member['id'],original['id']) is None
    token=current_project_context.set(ProjectContext(**{**registration['context'],'actor_id':member['id']}))
    try:
        request=Request({'type':'http','method':'POST','path':'/api/flowchart/models/verify','headers':[],
            'app':app,'state':{'account_user':member,'account_session_token':session['token']}})
        selected=_registered_retained_source(request,original['id'])
        assert selected['project_dir']==restored['project_dir'], {'returned':selected['project_dir'],'authorized':restored['project_dir']}
    finally:current_project_context.reset(token)


def test_list_must_check_registered_inode_before_manifest_normalization(tmp_path):
    app,owner,client=native_fixture(tmp_path)
    original=client.post('/api/project/create',json={'name':'original'}).json()
    app.state.local_projects.register(app,owner,original)
    target=Path(original['project_dir'])
    target.rename(tmp_path/'kept-original')
    shutil.copytree(tmp_path/'kept-original',target)
    manifest=target/'project.json';value=json.loads(manifest.read_text());value.pop('schema_version',None)
    manifest.write_text(json.dumps(value));before=manifest.read_bytes()
    response=client.get('/api/local-host/projects/list')
    assert response.status_code==409,response.text
    assert manifest.read_bytes()==before,'Unowned replacement was normalized before inode denial'


@pytest.mark.parametrize('route',['/api/project/current','/api/local-host/projects/current'])
def test_default_selection_must_check_inode_before_normalization(tmp_path,route):
    app,owner,client=native_fixture(tmp_path)
    original=client.post('/api/project/create',json={'name':'original'}).json()
    app.state.local_projects.register(app,owner,original)
    target=Path(original['project_dir']);target.rename(tmp_path/'preserved')
    shutil.copytree(tmp_path/'preserved',target)
    manifest=target/'project.json';value=json.loads(manifest.read_text());value.pop('schema_version',None)
    manifest.write_text(json.dumps(value));before=manifest.read_bytes()
    result=client.get(route)
    assert result.status_code==409,result.text
    assert manifest.read_bytes()==before,'Default selected replacement was normalized before inode denial'


def test_real_project_list_must_not_leak_original_to_restored_only_member(tmp_path):
    from backend.api.routes_project import _load_project
    from backend.main import ProjectContextMiddleware
    from backend.api.shared_authorization import SharedAuthorizationMiddleware
    from backend.engine.project_archive import create_archive,restore_archive
    from fastapi.testclient import TestClient
    app,owner,client=app_fixture(tmp_path)
    app.user_middleware.clear()
    app.add_middleware(ProjectContextMiddleware,project_app=app)
    app.add_middleware(SharedAuthorizationMiddleware,project_app=app)
    original=client.post('/api/project/create',json={'name':'private-original'}).json()
    archive=create_archive(original,tmp_path/'backups')
    restored=_load_project(restore_archive(Path(archive['archive_path']),tmp_path/'restored'))
    registration=app.state.local_projects.register(app,owner,restored)
    member=app.state.accounts.create_user('copy-member','another test password')
    app.state.accounts.set_membership(registration['permission_key'],member['id'],'trainer',owner['id'])
    session=app.state.accounts.login('copy-member','another test password')
    other=TestClient(app,headers={'Authorization':'Bearer '+session['token'],
        'X-Vision-Context':json.dumps({**registration['context'],'actor_id':member['id']})})
    response=other.get('/api/project/list')
    assert response.status_code==200,response.text
    paths=[row['project_dir'] for row in response.json()['projects']]
    assert original['project_dir'] not in paths,paths


def test_local_account_queries_mutations_and_selections_use_one_permission_key(tmp_path):
    from backend.contracts.context import current_project_context, ProjectContext
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.api.routes_project import _load_project
    app, owner, client = app_fixture(tmp_path)
    original = client.post('/api/project/create', json={'name': 'Private original'}).json()
    archived = create_archive(original, tmp_path / 'backups')
    restored = _load_project(restore_archive(Path(archived['archive_path']), tmp_path / 'restored'))
    registration = app.state.local_projects.register(app, owner, restored)
    store = app.state.accounts
    copy_owner = store.create_user('copy-owner', 'another test password')
    only_original = store.create_user('only-original', 'another test password')
    new_member = store.create_user('new-member', 'another test password')
    store.set_membership(registration['permission_key'], copy_owner['id'], 'owner', owner['id'])
    store.set_membership(original['id'], only_original['id'], 'viewer', owner['id'])
    token = current_project_context.set(ProjectContext(**{**registration['context'], 'actor_id': copy_owner['id']}))
    try:
        assert store.project_for(copy_owner['id'], original['id'])['path'] == restored['project_dir']
        assert {p['path'] for p in store.projects_for(copy_owner['id'])} == {restored['project_dir']}
        assert only_original['id'] not in {p['id'] for p in store.project_members(original['id'], copy_owner['id'])}
        store.select_project(copy_owner['id'], original['id'])
        store.set_membership(original['id'], new_member['id'], 'viewer', copy_owner['id'])
        assert any(row['action'] == 'membership.change' for row in store.audit_events(original['id'], copy_owner['id']))
        store.remove_membership(original['id'], new_member['id'], copy_owner['id'])
        # Exact registered locations cannot borrow the current copy's authority.
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as denied:
            app.state.local_projects.authorize_location(app, copy_owner, original['project_dir'])
        assert denied.value.status_code == 403
    finally:
        current_project_context.reset(token)
    assert store.project_for(copy_owner['id'])['path'] == restored['project_dir']
    assert store.project_role(new_member['id'], original['id']) is None
    assert store.project_role(new_member['id'], registration['permission_key']) is None
    assert store.project_role(only_original['id'], original['id']) == 'viewer'


@pytest.mark.parametrize('route', ['/api/project/list', '/api/local-host/projects/list'])
def test_all_project_lists_check_each_registered_inode_before_loading(tmp_path, route):
    app, owner, client = native_fixture(tmp_path)
    first = client.post('/api/project/create', json={'name': 'Selected'}).json()
    app.state.local_projects.register(app, owner, first)
    second = client.post('/api/project/create', json={'name': 'Replaced'}).json()
    app.state.local_projects.register(app, owner, second)
    client.headers['X-Vision-Project'] = first['id']
    target = Path(second['project_dir']); target.rename(tmp_path / 'preserved-second')
    shutil.copytree(tmp_path / 'preserved-second', target)
    manifest = target / 'project.json'; value = json.loads(manifest.read_text()); value.pop('schema_version', None)
    manifest.write_text(json.dumps(value)); before = manifest.read_bytes()
    result = client.get(route)
    assert result.status_code == 409
    assert manifest.read_bytes() == before

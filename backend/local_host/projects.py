"""Opt-in localhost project locations; logical manifest IDs remain unchanged."""

from contextlib import contextmanager
import json
import hashlib
from pathlib import Path
import sqlite3
import uuid

from fastapi import APIRouter, HTTPException, Request
from backend.contracts.context import ProjectContext, current_project_context
from . import location_identity
from .location_recovery import location_changed


class LocalProjectRegistry:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'registrations.sqlite3'
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS locations(
                    registration_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL,
                    project_id TEXT NOT NULL, permission_key TEXT NOT NULL UNIQUE,
                    path TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL, identity TEXT NOT NULL,
                    UNIQUE(workspace_id, project_id));
                CREATE TABLE IF NOT EXISTS backup_restore_requests(
                    actor_id TEXT NOT NULL, request_key TEXT NOT NULL, scope TEXT NOT NULL,
                    spec TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE,
                    PRIMARY KEY(actor_id,request_key));
                CREATE TABLE IF NOT EXISTS operations(
                    id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, kind TEXT NOT NULL,
                    phase TEXT NOT NULL, payload TEXT NOT NULL);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def permission_key(self, context, identifier):
        if context is None or context.project_id != identifier:
            return identifier
        with self.db() as db:
            row = db.execute('SELECT permission_key FROM locations WHERE workspace_id=? AND project_id=?',
                             (context.workspace_id, identifier)).fetchone()
        return row['permission_key'] if row else identifier

    def authorize_location(self, app, account, path, *, selecting=False):
        """Authorize known storage before its loader can perform legacy normalization."""
        literal = str(Path(path).expanduser().absolute())
        path = str(Path(path).expanduser().resolve())
        with self.db() as db:
            # Check the registered literal first: canonicalizing a replacement
            # symlink must not make its old registration disappear from lookup.
            location = db.execute('SELECT * FROM locations WHERE path=?', (literal,)).fetchone()
            if location is None:
                location = db.execute('SELECT * FROM locations WHERE path=?', (path,)).fetchone()
        with app.state.accounts._db() as db:
            existing = db.execute('SELECT id FROM projects WHERE path=?', (path,)).fetchone()
        key = location['permission_key'] if location else existing['id'] if existing else None
        if key and app.state.accounts.project_role_exact(account['id'], key) is None:
            raise HTTPException(403, 'Project membership required')
        if location and getattr(self, 'recovery', None):
            self.recovery.check_access(location, account['id'], selecting=selecting)
        if location and not location_identity.matches(json.loads(location['identity']), location['path']):
            raise location_changed()

    def register(self, app, account, project):
        """Register a location without moving legacy account selection or granting an existing location."""
        path = str(Path(project['project_dir']).resolve())
        self.authorize_location(app, account, path)
        accounts = app.state.accounts
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM locations WHERE path=?', (path,)).fetchone()
            if row:
                if not location_identity.matches(json.loads(row['identity']), path):
                    raise location_changed()
                if row['project_id'] != project['id']:
                    raise HTTPException(409, 'Registered manifest identity changed')
                if accounts.project_role_exact(account['id'], row['permission_key']) is None:
                    raise HTTPException(403, 'Project membership required')
                return self.public(dict(row), account)
            # ContextRegistry already preserves exact workspace/location identities.
            registry = app.state.context_registry
            # Reserve legacy default contexts before a restored copy can claim
            # the default workspace. This does not load or edit the original.
            with accounts._db() as account_db:
                default = account_db.execute('SELECT path FROM projects WHERE id=?', (project['id'],)).fetchone()
            if default and default['path'] != path:
                registry.register_project({'id': project['id'], 'project_dir': default['path']})
            registry.register_project(project)
            context = registry.context(project, None)
            with accounts._db() as account_db:
                existing = account_db.execute('SELECT id,path FROM projects WHERE id=?', (project['id'],)).fetchone()
                if existing and existing['path'] == path:
                    key = project['id']
                    if accounts._role(account_db, account['id'], key) is None:
                        raise HTTPException(403, 'Project membership required')
                else:
                    foreign = account_db.execute('SELECT id FROM projects WHERE path=?', (path,)).fetchone()
                    if foreign:
                        key = foreign['id']
                        if accounts._role(account_db, account['id'], key) is None:
                            raise HTTPException(403, 'Existing project location requires membership')
                    else:
                        key = 'local_' + uuid.uuid4().hex if existing else project['id']
                        account_db.execute('INSERT INTO projects VALUES(?,?)', (key, path))
                        account_db.execute('INSERT INTO members VALUES(?,?,?)', (key, account['id'], 'owner'))
                        accounts._bump_membership(account_db, key, account['id'])
                    # Preserve existing/default selections even after opening a restored copy.
                    account_db.execute('INSERT OR IGNORE INTO selections VALUES(?,?)', (account['id'], key))
            row = dict(registration_id=uuid.uuid4().hex, workspace_id=context.workspace_id,
                       project_id=project['id'], permission_key=key, path=path, revision=1, identity=json.dumps(location_identity.read(path)))
            db.execute('INSERT INTO locations VALUES(:registration_id,:workspace_id,:project_id,:permission_key,:path,:revision,:identity)', row)
        return self.public(row, account)

    def response_registration(self, app, request, project, *, required=False):
        """Read existing response metadata without registering, granting, or selecting a location."""
        path = str(Path(project['project_dir']).resolve())
        with self.db() as db:
            row = db.execute('SELECT * FROM locations WHERE path=?', (path,)).fetchone()
        if row is None:
            if required: raise HTTPException(409, 'Selected local project registration is unavailable')
            return None
        row = dict(row)
        account = _fresh_authority(app, request, row['permission_key'], action='project.manage')
        context = getattr(request.state, 'local_project_context', None) or getattr(request.state, 'project_context', None)
        scoped = getattr(request.state, 'scoped_project', None)
        expected = ProjectContext(workspace_id=row['workspace_id'], project_id=row['project_id'],
                                  actor_id=account['id'], mode='team')
        if (context != expected or getattr(request.state, 'account_project_key', None) != row['permission_key']
                or project['id'] != row['project_id'] or scoped is None or scoped['id'] != project['id']
                or str(Path(scoped['project_dir']).resolve()) != path):
            raise HTTPException(403, 'Updated project does not match its authenticated local registration')
        try:
            selected = app.state.accounts.registered_project_for(account['id'], row['permission_key'])
            if str(Path(selected['path']).resolve()) != path or not location_identity.matches(json.loads(row['identity']), path):
                raise location_changed()
        except ValueError as exc:
            raise HTTPException(403, 'Current registered project membership required') from exc
        except OSError as exc:
            raise HTTPException(409, 'Registered project location is unavailable') from exc
        return self.public(row, account)

    @staticmethod
    def public(row, account):
        return {**{key: value for key, value in row.items() if key != 'identity'}, 'context': ProjectContext(workspace_id=row['workspace_id'], project_id=row['project_id'],
                                               actor_id=account['id'], mode='team').model_dump()}

    def resolve_request(self, request, account):
        from backend.contracts.context import declared_context
        project, key, context = self.resolve(request.app, account, declared_context(request.headers), request.headers.get('x-vision-project'))
        if project is None:
            raise HTTPException(409, 'Select a project first')
        role = request.app.state.accounts.project_role_exact(account['id'], key)
        return {'project': project, 'role': role, 'context': context, 'membership_key': key}

    def resolve(self, app, account, declared, selected_id=None):
        try:
            return self._resolve(app, account, declared, selected_id)
        except ValueError as exc:
            raise HTTPException(403, 'Project membership required') from exc
        except OSError as exc:
            raise HTTPException(409, 'Registered project location is unavailable') from exc

    def _resolve(self, app, account, declared, selected_id=None):
        """Only a server-registered location can authorize a nondefault workspace."""
        from backend.api.routes_project import _load_project
        accounts = app.state.accounts
        if declared:
            if declared.actor_id != account['id'] or declared.mode != 'team':
                raise HTTPException(403, 'Explicit context actor is not authorized')
            with self.db() as db:
                row = db.execute('SELECT * FROM locations WHERE workspace_id=? AND project_id=?',
                                 (declared.workspace_id, declared.project_id)).fetchone()
            if row:
                row = dict(row)
                selected = accounts.registered_project_for(account['id'], row['permission_key'])
                if getattr(self, 'recovery', None): self.recovery.check_access(row, account['id'])
                if not location_identity.matches(json.loads(row['identity']), row['path']):
                    raise location_changed()
                if selected['path'] != row['path']:
                    raise HTTPException(409, 'Registered project location changed')
                project = _load_project(Path(row['path']))
                if project['id'] != row['project_id']:
                    raise HTTPException(409, 'Registered manifest identity changed')
                return project, row['permission_key'], declared
            if declared.workspace_id != app.state.context_registry.workspace_id:
                raise HTTPException(403, 'Explicit workspace is not registered')
        selected = accounts.project_for(account['id'], declared.project_id if declared else selected_id)
        if selected is None:
            return None, None, None
        self.authorize_location(app, account, selected['path'])
        project = _load_project(Path(selected['path']))
        registration = self.register(app, account, project)
        return project, registration['permission_key'], ProjectContext(**registration['context'])


def install_local_projects(app, registry):
    from .location_recovery import LocationRecovery, RecoveryAdmissionMiddleware, recovery_router
    app.state.local_projects = registry
    registry.recovery = LocationRecovery(app, registry)
    from .project_assets import install_project_assets
    install_project_assets(app,registry)
    app.include_router(recovery_router(registry.recovery))
    app.add_middleware(RecoveryAdmissionMiddleware, coordinator=registry.recovery)
    app.state.accounts.local_permission_resolver = lambda identifier: registry.permission_key(current_project_context.get(), identifier)
    app.include_router(local_projects_router(app, registry))


# Native operations use explicit, durable receipts; interrupted filesystem work
# remains available for review and is never automatically repeated on startup.
def _identity(path):
    path = Path(path)
    stat = path.stat()
    return [stat.st_dev, stat.st_ino]


def _fresh_authority(app, request, key=None, *, action='project.manage'):
    host_action=getattr(request.state,'local_project_action',None)
    if host_action:
        app.state.local_host.authorize(request,host_action)
    accounts = app.state.accounts
    try:
        account = accounts.authenticate(request.state.account_session_token)
    except ValueError as exc:
        raise HTTPException(401, 'Account session unavailable') from exc
    if account['id'] != request.state.account_user['id']:
        raise HTTPException(403, 'Account changed')
    if key:
        decision = accounts.authorize(account['id'], action, key, session_token=request.state.account_session_token, scoped=False)
        if not decision.allowed:
            raise HTTPException(403, 'Current project permission required')
    return account


def _journal(registry, operation, account, kind, phase, payload):
    with registry.db() as db:
        db.execute('INSERT INTO operations VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET phase=excluded.phase,payload=excluded.payload',
                   (operation, account['id'], kind, phase, json.dumps(payload)))


def local_projects_router(app, registry):
    import os
    import shutil
    from fastapi.responses import JSONResponse
    from pydantic import BaseModel, ConfigDict, Field
    from backend.contracts.context import declared_context
    from backend.api.routes_project import (_load_project, ProjectCreateRequest, ProjectOpenRequest,
                                            ProjectBackupRequest, ProjectRestoreRequest, ProjectConfigResponse, create_project)
    from backend.engine.project_archive import ArchiveError, restore_archive, _digest_file, _publish_fresh_directory
    from backend.engine.data_backup_job import DataBackupJobs
    from backend.engine.job_store import JobStore

    router = APIRouter(prefix='/api/local-host/projects', tags=['local-projects'])

    class Backup(ProjectBackupRequest):
        expected_source: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')

    class Restore(ProjectRestoreRequest):
        expected_archive_sha256: str | None = Field(default=None, pattern='^[0-9a-f]{64}$')

    class Selection(BaseModel):
        model_config = ConfigDict(extra='forbid')
        registration_id: str = Field(min_length=1, max_length=128)

    class Recovery(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        operation_id: str = Field(min_length=1, max_length=128)
        target_dir: str = Field(min_length=1, max_length=4096)

    class Adoption(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        confirmed: bool

    def host(request, action):
        controller = getattr(app.state, 'local_host', None)
        if controller is None:
            raise HTTPException(404, 'Local host unavailable')
        controller.authorize(request, 'projects.' + action)
        request.state.local_project_action='projects.' + action
        return controller

    def binding(request):
        return {'tab_id': request.headers.get('x-vision-local-tab'), 'session_id': hashlib.sha256(request.state.account_session_token.encode()).hexdigest()}

    def selected(request, *, write=False):
        account = _fresh_authority(app, request)
        project, key, context = registry.resolve(app, account, declared_context(request.headers), request.headers.get('x-vision-project'))
        if project is None:
            raise HTTPException(409, 'Select a project first')
        _fresh_authority(app, request, key, action='project.manage' if write else 'project.read')
        request.state.scoped_project = project
        request.state.account_project_key = key
        request.state.project_context = context
        return account, project, key, context

    def response(project, registration, operation=None):
        result = {**project, 'registration_id': registration['registration_id'],
                  'workspace_id': registration['workspace_id'], 'context': registration['context']}
        if operation:
            result['operation_id'] = operation
        return JSONResponse(result, headers={'X-Vision-Context': json.dumps(registration['context']), 'Cache-Control': 'no-store'})

    @router.get('/list')
    def listing(request: Request):
        host(request, 'list')
        account = _fresh_authority(app, request)
        projects = []
        for row in app.state.accounts.projects_for(account['id']):
            registry.authorize_location(app, account, row['path'])
            project = _load_project(Path(row['path']))
            registration = registry.register(app, account, project)
            projects.append({**project, **registration, 'id': project['id']})
        return {'projects': projects}

    @router.get('/current')
    def current(request: Request):
        host(request, 'current')
        account, project, key, context = selected(request)
        return response(project, registry.register(app, account, project))

    @router.post('/select')
    def select(body: Selection, request: Request):
        host(request, 'select')
        account = _fresh_authority(app, request)
        with registry.db() as db:
            row = db.execute('SELECT * FROM locations WHERE registration_id=?', (body.registration_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Registration unavailable')
        _fresh_authority(app, request, row['permission_key'], action='project.read')
        registry.authorize_location(app, account, row['path'], selecting=True)
        project = _load_project(Path(row['path']))
        if project['id'] != row['project_id']: raise HTTPException(409, 'Registered manifest identity changed')
        registry.recovery.selected(row, account['id'])
        app.state.accounts.select_project(account['id'], row['permission_key'])
        return response(project, registry.register(app, account, project))

    @router.get('/template')
    def template(request: Request):
        from backend.engine.project_templates import export_template
        host(request, 'template')
        return export_template(selected(request)[1])

    @router.post('/open')
    def opening(body: ProjectOpenRequest, request: Request):
        controller = host(request, 'open')
        account = _fresh_authority(app, request)
        controller.require_grants(request, [('project_dir', body.project_dir)])
        registry.authorize_location(app, account, body.project_dir)
        project = _load_project(Path(body.project_dir).expanduser().resolve())
        account = _fresh_authority(app, request)
        return response(project, registry.register(app, account, project))

    @router.post('/create')
    def creating(body: ProjectCreateRequest, request: Request):
        controller = host(request, 'create')
        account = _fresh_authority(app, request)
        if body.project_dir:
            controller.require_grants(request, [('project_dir', body.project_dir)])
        else:
            body = body.model_copy(update={'project_dir': str(Path(app.state.project_dir) / ('local-' + uuid.uuid4().hex))})
        target = Path(body.project_dir).expanduser().resolve()
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise HTTPException(409, 'Create requires an empty project location')
        operation = uuid.uuid4().hex
        payload = {'target': str(target), **binding(request)}
        _journal(registry, operation, account, 'create', 'accepted', payload)
        request.state.local_project_creation = True
        project = create_project(body, request)
        payload.update(target_identity=_identity(target), manifest_sha256=_digest_file(target / 'project.json'), project_id=project['id'])
        _journal(registry, operation, account, 'create', 'published', payload)
        registration = registry.register(app, _fresh_authority(app, request), project)
        _journal(registry, operation, account, 'create', 'completed', {**payload, 'registration': registration})
        return response(project, registration, operation)

    @router.post('/backup')
    def backup(body: Backup, request: Request):
        controller = host(request, 'backup')
        account, project, key, context = selected(request, write=True)
        destination = Path(body.destination_dir).expanduser().resolve()
        roots = [Path(project['project_dir']).resolve()]
        if project.get('source_dataset_dir'):
            roots.append(Path(project['source_dataset_dir']).resolve())
        if any(destination == root or destination.is_relative_to(root) for root in roots):
            raise HTTPException(422, 'Choose a backup folder outside project and source')
        jobs = DataBackupJobs(JobStore(registry.root / 'backup-jobs.sqlite3'))
        if body.expected_source and jobs.snapshot(project) != body.expected_source:
            raise HTTPException(409, 'Backup source changed')
        controller.require_grants(request, [('destination_dir', body.destination_dir)], project)
        operation = uuid.uuid4().hex
        payload = {'destination': str(destination), 'destination_identity': _identity(destination), 'permission_key': key, 'context': context.model_dump(), **binding(request)}
        _journal(registry, operation, account, 'backup', 'accepted', payload)
        ref = jobs.submit(context, app.state.context_registry.project_key(context), project, operation)
        payload['job_id'] = ref.id
        _journal(registry, operation, account, 'backup', 'staging', payload)
        jobs.run(ref.id)
        view = jobs.view(ref.id, app.state.context_registry.project_key(context), account['id'])
        if view['state'] != 'completed':
            _journal(registry, operation, account, 'backup', 'interrupted', payload)
            raise HTTPException(409, 'Backup did not complete; inspect operation receipt')
        _fresh_authority(app, request, key)
        result = view['result_ref']
        if not destination.is_dir() or _identity(destination) != payload['destination_identity']:
            raise HTTPException(409, 'Backup destination ownership changed')
        target = destination / Path(result['path']).name
        payload.update(target=str(target), archive_sha256=result['sha256'])
        _journal(registry, operation, account, 'backup', 'publishing', payload)
        # Exclusive creation never overwrites a previously exported archive.
        with open(result['path'], 'rb') as source, open(target, 'xb') as output:
            shutil.copyfileobj(source, output)
            output.flush(); os.fsync(output.fileno())
        if _digest_file(target) != result['sha256']:
            raise HTTPException(409, 'Export verification failed; retained output requires review')
        from zipfile import ZipFile
        with ZipFile(target) as archive:
            inventory = json.loads(archive.read('backup-manifest.json'))
        payload['target_identity'] = _identity(target)
        _journal(registry, operation, account, 'backup', 'completed', payload)
        return {'source_included': inventory['original_source_dir'] is not None,
                'source_bytes': sum(row['size'] for row in inventory['files'] if row['member'].startswith('source/')),
                'archive_path': str(target), 'archive_sha256': result['sha256'], 'file_count': result['count'],
                'total_bytes': sum(row['size'] for row in inventory['files']), 'operation_id': operation, 'backup_status': 'archive_verified'}

    def perform_restore(request, archive, target, digest, operation, *, extra=None, recheck=None):
        account = _fresh_authority(app, request)
        if recheck:recheck()
        if extra and extra.get('parent_identity') != _identity(target.parent):
            raise HTTPException(409, 'Restore parent ownership changed')
        stage = target.parent / ('.' + target.name + '.local-restore-' + operation)
        payload = {'archive': str(archive), 'archive_sha256': digest, 'target': str(target), 'stage': str(stage),
                   'parent_identity': _identity(target.parent), **binding(request), **(extra or {})}
        _journal(registry, operation, account, 'restore', 'accepted', payload)

        def before_publish(staging, destination):
            _fresh_authority(app, request)
            if recheck:recheck()
            if _identity(destination.parent) != payload['parent_identity']:
                raise HTTPException(409, 'Restore parent ownership changed')
            if _digest_file(archive) != digest:
                raise HTTPException(409, 'Archive changed since selection')
            if payload.get('backup_scope'):
                from backend.engine.data_restore_job import inventory
                from backend.engine.migration_guard import maintenance_guard
                from backend.engine.project_migration import normalize_legacy_manifest
                from backend.engine.project_labelsets import load_labelsets, labelset_root
                from backend.api.routes_project import _write_json
                # Prepare activation before sealing publication, without rebinding
                # the archive's final-target paths to the temporary staging path.
                with maintenance_guard(staging):
                    manifest = staging / 'project.json'
                    ProjectConfigResponse.model_validate_json(manifest.read_text())
                    normalize_legacy_manifest(staging)
                    active = load_labelsets(staging)['active_id']
                    project = ProjectConfigResponse.model_validate_json(manifest.read_text()).model_dump()
                    annotations = str(labelset_root(destination, active))
                    if project['annotations_dir'] != annotations:
                        project.update(annotations_dir=annotations, active_labelset_id=active)
                        _write_json(manifest, project)
                payload['restored_inventory'] = inventory(staging)
            payload.update(target_identity=_identity(staging), manifest_sha256=_digest_file(staging / 'project.json'), project_id=json.loads((staging / 'project.json').read_text())['id'])
            _journal(registry, operation, account, 'restore', 'publishing', payload)

        def publish(staging, destination):
            _publish_fresh_directory(staging, destination)
            _journal(registry, operation, account, 'restore', 'published', payload)

        try:
            restore_archive(archive, target, staging_dir=stage, before_publish=before_publish,
                            publish=publish, retain_staging=True)
            if recheck:recheck()
            if payload.get('backup_scope'):
                verify_published(request, {'kind': 'restore', 'phase': 'published', 'payload': payload})
            project = _load_project(target)
            payload['manifest_sha256'] = _digest_file(target / 'project.json')
            if payload.get('backup_scope'):
                verify_published(request, {'kind': 'restore', 'phase': 'published', 'payload': payload})
            _journal(registry, operation, account, 'restore', 'published', payload)
            registration = registry.register(app, _fresh_authority(app, request), project)
            payload['registration'] = registration
            _journal(registry, operation, account, 'restore', 'completed', payload)
            return project, registration, payload
        except ArchiveError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post('/restore')
    def restore(body: Restore, request: Request):
        controller = host(request, 'restore')
        archive = Path(body.archive_path).expanduser().resolve()
        target = Path(body.target_dir).expanduser().resolve()
        controller.require_grants(request, [('archive_path', body.archive_path), ('target_dir', body.target_dir)])
        digest = _digest_file(archive)
        if body.expected_archive_sha256 and body.expected_archive_sha256 != digest:
            raise HTTPException(409, 'Archive changed since preview')
        operation = uuid.uuid4().hex
        project, registration, _ = perform_restore(request, archive, target, digest, operation)
        return response(project, registration, operation)

    class BackupRestore(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        target_dir: str = Field(min_length=1, max_length=4096)
        expected_archive_sha256: str = Field(pattern='^[0-9a-f]{64}$')

    @router.post('/backups/{backup_id}/restore')
    def restore_verified_backup(backup_id: str, body: BackupRestore, request: Request):
        from backend.api.routes_dataset_imports import _backup_jobs, _IDEMPOTENCY_KEY
        controller = host(request, 'restore-backup')
        account, original, permission_key, context = selected(request, write=True)
        project_key = app.state.context_registry.project_key(context)
        authority = controller.authorize(request, 'projects.restore-backup', project=True)
        request_key = request.headers.get('idempotency-key', '')
        if not _IDEMPOTENCY_KEY.fullmatch(request_key):
            raise HTTPException(422, 'A valid Idempotency-Key is required')
        target = Path(body.target_dir).expanduser().resolve()
        scope = {**binding(request), 'workspace_id': context.workspace_id, 'project_key': project_key,
                 'context': context.model_dump(), 'permission_key': permission_key, 'role': authority['role'],
                 'labelset_id': original.get('active_labelset_id', 'default'),
                 'permission_revision': authority.get('permission_revision')}
        spec = {'backup_id': backup_id, 'target': str(target), 'archive_sha256': body.expected_archive_sha256}
        scope_json, spec_json = json.dumps(scope, sort_keys=True), json.dumps(spec, sort_keys=True)

        def source():
            _fresh_authority(app, request, permission_key)
            controller.recheck(request, 'projects.restore-backup', authority, project=True)
            current = registry.resolve_request(request, account)
            if (current['context'] != context or current['project'].get('active_labelset_id', 'default') != scope['labelset_id']):
                raise HTTPException(409, 'Backup restore project authority changed')
            try:view = _backup_jobs().view(backup_id, project_key, account['id'])
            except KeyError:raise HTTPException(404, 'Backup not found in this account and project') from None
            if not view['downloadable'] or view['result_ref']['sha256'] != body.expected_archive_sha256:
                raise HTTPException(409, 'Verified backup is missing, expired or changed')
            return Path(view['result_ref']['path'])

        archive = source()
        def replay(row):
            if row['scope'] != scope_json or row['spec'] != spec_json:
                raise HTTPException(409, 'Restore request belongs to another tab, scope or target')
            operation = owned_operation(request, row['operation_id'])
            if operation['phase'] != 'completed':
                raise HTTPException(409, 'recovery_required: restore request already exists; inspect its receipt')
            verify_published(request, operation)
            payload = operation['payload']
            return backup_restore_result(row['operation_id'], payload, True)

        with registry.db() as db:
            previous = db.execute('SELECT * FROM backup_restore_requests WHERE actor_id=? AND request_key=?',
                                  (account['id'], request_key)).fetchone()
        if previous:return replay(previous)
        if target.exists():raise HTTPException(409, 'Restore requires a fresh target')
        parent_identity = _identity(target.parent)
        controller.require_grants(request, [('target_dir', body.target_dir)], original)
        operation = uuid.uuid4().hex
        with registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT * FROM backup_restore_requests WHERE actor_id=? AND request_key=?',
                                  (account['id'], request_key)).fetchone()
            if previous:
                raise HTTPException(409, 'recovery_required: restore request already reserved')
            db.execute('INSERT INTO backup_restore_requests VALUES(?,?,?,?,?)',
                       (account['id'], request_key, scope_json, spec_json, operation))
        extra = {'backup_id': backup_id, 'backup_scope': scope, 'parent_identity': parent_identity}
        _journal(registry, operation, account, 'restore', 'accepted', {'target': str(target), **binding(request), **extra})
        _, _, payload = perform_restore(request, archive, target, body.expected_archive_sha256, operation,
                                         extra=extra, recheck=source)
        return backup_restore_result(operation, payload, False)

    def backup_restore_result(operation, payload, replay):
        registration = payload['registration']
        return {'state': 'completed', 'autoactivated': False, 'operation_id': operation, 'idempotent_replay': replay,
                'project_id': registration['project_id'], 'project_dir': registration['path'],
                'registration_id': registration['registration_id'], 'workspace_id': registration['workspace_id'],
                'context': registration['context']}

    def owned_operation(request, operation_id):
        account = _fresh_authority(app, request)
        with registry.db() as db:
            row = db.execute('SELECT * FROM operations WHERE id=? AND actor_id=?', (operation_id, account['id'])).fetchone()
        if row is None:
            raise HTTPException(404, 'Operation not found')
        return {**dict(row), 'payload': json.loads(row['payload'])}

    def verify_backup_source(request, payload):
        # A journal's source authority is immutable. Caller-selected headers may
        # refer to another project (or none), and cannot substitute for it.
        if not payload.get('backup_id') and not payload.get('backup_scope'):
            return
        from backend.api.routes_dataset_imports import _backup_jobs
        scope = payload.get('backup_scope') or {}
        account = _fresh_authority(app, request)
        try:
            context = ProjectContext(**scope['context'])
            permission_key = scope['permission_key']
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, 'recovery_required: captured backup authority is incomplete') from exc
        if context.actor_id != account['id'] or context.mode != 'team':
            raise HTTPException(403, 'Captured backup actor is not authorized')
        project, current_key, current_context = registry.resolve(app, account, context)
        decision = app.state.accounts.authorize(account['id'], 'project.manage', permission_key,
            session_token=request.state.account_session_token, scoped=False)
        if not decision.allowed or decision.role != 'owner':
            raise HTTPException(403, 'Current backup source owner permission required')
        if (current_key != permission_key or current_context != context or scope.get('role') != decision.role
                or scope.get('workspace_id') != context.workspace_id
                or scope.get('project_key') != app.state.context_registry.project_key(context)
                or scope.get('permission_revision') != decision.membership_revision
                or scope.get('labelset_id') != project.get('active_labelset_id', 'default')):
            raise HTTPException(409, 'recovery_required: captured backup source authority changed')
        try:
            view = _backup_jobs().view(payload['backup_id'], scope['project_key'], account['id'])
        except KeyError:
            raise HTTPException(409, 'recovery_required: captured source backup is unavailable') from None
        result = view.get('result_ref') or {}
        if (not view.get('downloadable') or result.get('sha256') != payload.get('archive_sha256')
                or result.get('path') != payload.get('archive')):
            raise HTTPException(409, 'recovery_required: captured source backup changed')

    def verify_published(request, operation):
        payload = operation['payload']
        verify_backup_source(request, payload)
        if operation['kind'] not in {'restore', 'create'} or operation['phase'] not in {'published', 'completed'}:
            raise HTTPException(409, 'recovery_required: publication is uncertain; manual review required')
        target = Path(payload['target'])
        try:
            if target.resolve() != target or _identity(target) != payload.get('target_identity'):
                raise ValueError('ownership changed')
            if _digest_file(target / 'project.json') != payload.get('manifest_sha256'):
                raise ValueError('manifest changed')
            manifest = ProjectConfigResponse.model_validate_json((target / 'project.json').read_text())
            if manifest.id != payload.get('project_id'):
                raise ValueError('manifest identity changed')
        except (OSError, ValueError) as exc:
            raise HTTPException(409, 'recovery_required: published target identity changed; manual review required') from exc
        if payload.get('backup_scope'):
            from backend.engine.data_restore_job import inventory
            from backend.engine.dataset_import_job import ImportNotAcceptable
            try:
                if inventory(target) != payload.get('restored_inventory'):
                    raise ValueError('inventory changed or absent')
            except (OSError, ValueError, ImportNotAcceptable) as exc:
                raise HTTPException(409, 'recovery_required: restored output changed') from exc
        with registry.db() as db:
            row = db.execute('SELECT permission_key FROM locations WHERE path=?', (str(target),)).fetchone()
        if row:
            _fresh_authority(app, request, row['permission_key'], action='project.read')
        else:
            # Account insertion may have committed before registration publication.
            with app.state.accounts._db() as db:
                row = db.execute('SELECT id FROM projects WHERE path=?', (str(target),)).fetchone()
            if row:
                _fresh_authority(app, request, row['id'], action='project.read')
        return target

    def adopted_payload(request, payload):
        previous = {key: payload.get(key) for key in ('session_id', 'tab_id')}
        return {**payload, **binding(request), 'adoptions': [*payload.get('adoptions', []), previous][-20:]}

    @router.get('/recovery')
    def recovery_list(request: Request):
        host(request, 'receipt')
        account = _fresh_authority(app, request)
        with registry.db() as db:
            rows = db.execute("SELECT * FROM operations WHERE actor_id=? AND phase!='completed' ORDER BY rowid DESC LIMIT 200", (account['id'],)).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row['payload'])
            result.append({'id': row['id'], 'kind': row['kind'], 'phase': row['phase'],
                'requires_adoption': any(payload.get(key) != value for key, value in binding(request).items()),
                'recovery_status': 'registration_pending' if row['kind'] in {'create', 'restore'} and row['phase'] == 'published' else 'recovery_required'})
        return {'operations': result}

    @router.post('/operations/{operation_id}/adopt')
    def adopt(operation_id: str, body: Adoption, request: Request):
        controller = host(request, 'adopt')
        if not body.confirmed:
            raise HTTPException(422, 'Explicit receipt adoption confirmation required')
        controller.authorize(request, 'recovery.adopt')  # native owner approval, even for metadata-only adoption
        operation = owned_operation(request, operation_id)
        verify_published(request, operation)
        account = _fresh_authority(app, request)
        payload = adopted_payload(request, operation['payload'])
        _journal(registry, operation_id, account, operation['kind'], operation['phase'], payload)
        return {'id': operation_id, 'kind': operation['kind'], 'phase': operation['phase'], 'adopted': True,
                'recovery_status': 'completed' if operation['phase'] == 'completed' else 'registration_pending'}

    @router.post('/recover')
    def recover_selected(body: Recovery, request: Request):
        controller = host(request, 'recover')
        operation = owned_operation(request, body.operation_id)
        target = verify_published(request, operation)
        if Path(body.target_dir).expanduser().resolve() != target:
            raise HTTPException(409, 'Select the original published recovery target')
        controller.require_grants(request, [('target_dir', body.target_dir)])
        account = _fresh_authority(app, request)
        verify_published(request, operation)
        project = _load_project(target)
        if operation['payload'].get('backup_scope'):
            verify_published(request, operation)
        registration = registry.register(app, account, project)
        payload = adopted_payload(request, operation['payload'])
        payload.update(registration=registration, manifest_sha256=_digest_file(target / 'project.json'))
        _journal(registry, body.operation_id, account, operation['kind'], 'completed', payload)
        return response(project, registration, body.operation_id)

    @router.get('/operations')
    def receipts(request: Request):
        host(request, 'receipt')
        account = _fresh_authority(app, request)
        expected = binding(request)
        with registry.db() as db:
            rows = db.execute('SELECT * FROM operations WHERE actor_id=? ORDER BY rowid DESC LIMIT 200', (account['id'],)).fetchall()
        operations = []
        for row in rows:
            payload = json.loads(row['payload'])
            if all(payload.get(key) == value for key, value in expected.items()):
                operations.append({**dict(row), 'payload': payload})
        return {'operations': operations}

    @router.get('/operations/{operation_id}')
    def receipt(operation_id: str, request: Request):
        host(request, 'receipt')
        account = _fresh_authority(app, request)
        with registry.db() as db:
            row = db.execute('SELECT * FROM operations WHERE id=? AND actor_id=?', (operation_id, account['id'])).fetchone()
        if row is None:
            raise HTTPException(404, 'Operation not found')
        payload=json.loads(row['payload'])
        if any(payload.get(key) != value for key,value in binding(request).items()):
            raise HTTPException(404, 'Operation not found in this tab session')
        return {**dict(row), 'payload': payload}

    @router.post('/operations/{operation_id}/recover')
    def recover(operation_id: str, request: Request):
        host(request, 'recover')
        operation = receipt(operation_id, request)
        payload = operation['payload']
        target = verify_published(request, operation)
        account = _fresh_authority(app, request)
        project = _load_project(target)
        if operation['payload'].get('backup_scope'):
            verify_published(request, operation)
        registration = registry.register(app, account, project)
        _journal(registry, operation_id, account, operation['kind'], 'completed', {**payload, 'registration': registration})
        return response(project, registration, operation_id)

    return router


def native_backup_view(request, view):
    """Advertise the host-only restore entry point without changing team job policy."""
    app = request.app
    if getattr(app.state, 'local_host', None) is None or getattr(app.state, 'local_projects', None) is None:
        return view
    account = getattr(request.state, 'account_user', None)
    if not account:return view
    try:
        resolved = app.state.local_projects.resolve_request(request, account)
        decision = app.state.accounts.authorize(account['id'], 'project.manage', resolved['membership_key'],
            session_token=request.state.account_session_token, scoped=False)
        allowed = decision.allowed and decision.role == 'owner' and bool(view.get('downloadable'))
    except (HTTPException, ValueError, OSError):allowed = False
    return {**view, 'native_restore': allowed,
            'capabilities': {**view.get('capabilities', {}), 'restore': allowed},
            'restore_reason': 'Choose a native folder and explicitly restore this verified backup; the current project remains selected' if allowed else view.get('restore_reason', '')}

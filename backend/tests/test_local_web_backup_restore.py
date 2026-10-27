"""Task Center restores verified durable backups through the local host boundary."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def fixture(tmp_path):
    from backend.main import create_app
    from backend.contracts.authentication import configure_browser_origins
    from backend.local_host.controller import LocalHostController
    from backend.local_host.routes import local_host_router
    from backend.local_host.projects import LocalProjectRegistry, install_local_projects
    from backend.contracts.context import ProjectContext
    from backend.engine.job_store import ledger
    from backend.engine.data_backup_job import DataBackupJobs
    origin = 'https://localhost:8443'
    app = create_app(project_dir=str(tmp_path / 'projects'), shared_auth_dir=str(tmp_path / 'auth'))
    configure_browser_origins(app, [origin])
    app.state.accounts.bootstrap('owner', 'long test password')
    session = app.state.accounts.login('owner', 'long test password', transport='cookie')
    class Helper:
        ready = {'platform': 'darwin'}
        closed = False
        path = None
        async def call(self, authority, method, args):
            if method == 'host.approve': return {'approved': True}
            if method == 'dialog.folder': return str(self.path)
            raise AssertionError(method)
    helper = Helper()
    controller = LocalHostController(app, helper, origin=origin, state_dir=tmp_path / 'host')
    app.state.local_host = controller
    install_local_projects(app, LocalProjectRegistry(tmp_path / 'registrations'))
    app.include_router(local_host_router(controller))
    client = TestClient(app, base_url=origin, headers={'Origin': origin, 'X-Vision-CSRF': session['csrf_token']})
    client.cookies.set('vision_session', session['token'])
    client.headers['X-Vision-Local-Tab'] = client.post('/api/local-host/session').json()['tab_id']
    assert client.post('/api/local-host/approve', json={}).status_code == 200
    created = client.post('/api/local-host/projects/create', json={'name': 'Backup source'})
    assert created.status_code == 200, created.text
    project = created.json(); client.headers['X-Vision-Context'] = created.headers['x-vision-context']
    context = ProjectContext(**project['context'])
    jobs = DataBackupJobs(ledger())
    key = app.state.context_registry.project_key(context)
    job = jobs.submit(context, key, project); jobs.run(job.id)
    view = jobs.view(job.id, key, context.actor_id)
    assert view['downloadable'] and view['capabilities']['restore'] is False
    parent = tmp_path / 'restore-parent'; parent.mkdir(); helper.path = parent
    return app, client, helper, session, project, jobs, job.id, view, parent


def select_target(client):
    result = client.post('/api/local-host/dialog/folder', json={'purpose': 'project-restore-target'})
    assert result.status_code == 200, result.text
    return {'X-Vision-Path-Grants': json.dumps([result.json()['grant_id']]), 'Idempotency-Key': 'task-restore-test'}


def test_verified_task_backup_native_restore_preserves_selection_and_replays_receipt(tmp_path):
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    response = client.get('/api/dataset/operations/backups/' + job_id)
    assert response.json().get('native_restore') is True, response.text
    assert response.json()['capabilities']['restore'] is True
    target = parent / 'restored-project'; headers = select_target(client)
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    body = {'target_dir': str(target), 'expected_archive_sha256': view['result_ref']['sha256']}
    restored = client.post(route, json=body, headers=headers)
    assert restored.status_code == 200, restored.text
    result = restored.json()
    assert result['state'] == 'completed' and result['autoactivated'] is False
    assert result['project_dir'] == str(target)
    assert result['project_id'] == project['id']
    assert 'x-vision-context' not in restored.headers
    assert client.get('/api/project/current').json()['project_dir'] == project['project_dir']
    manifest = (target / 'project.json').read_bytes()
    replay = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['idempotent_replay'] is True
    assert replay.json()['operation_id'] == result['operation_id']
    assert (target / 'project.json').read_bytes() == manifest
    changed = client.post(route, json={**body, 'target_dir': str(parent / 'another')}, headers={'Idempotency-Key': 'task-restore-test'})
    assert changed.status_code == 409
    other_tab = client.post('/api/local-host/session').json()['tab_id']
    switched = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test', 'X-Vision-Local-Tab': other_tab})
    assert switched.status_code == 409
    (target / 'reports' / 'changed.txt').write_text('changed since completed receipt')
    changed_output = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert changed_output.status_code == 409


def test_native_task_restore_refuses_stale_archive_and_generic_team_restore(tmp_path):
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    assert client.post('/api/dataset/operations/backups/' + job_id + '/restore', json=body).status_code == 403
    headers = select_target(client)
    archive = Path(view['result_ref']['path']); archive.write_bytes(archive.read_bytes() + b'changed')
    response = client.post('/api/local-host/projects/backups/' + job_id + '/restore', json=body, headers=headers)
    assert response.status_code == 409, response.text
    assert not (parent / 'restored-project').exists()


def test_task_wrapper_advertises_native_restore_only_for_current_owner(tmp_path):
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    response = client.get('/api/training-workspace/tasks')
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()['tasks'] if row['job_id'] == job_id)
    assert row['native_restore'] is True and row['capabilities']['restore'] is True
    from backend.contracts.context import ProjectContext
    assert jobs.view(job_id, app.state.context_registry.project_key(ProjectContext(**project['context'])), session['user']['id'])['capabilities']['restore'] is False
    other = app.state.accounts.create_user('other-owner', 'another test password')
    app.state.accounts.set_membership(project['id'], other['id'], 'owner', session['user']['id'])
    app.state.accounts.set_membership(project['id'], session['user']['id'], 'trainer', other['id'])
    response = client.get('/api/dataset/operations/backups/' + job_id)
    assert response.status_code == 200
    assert response.json()['native_restore'] is False and response.json()['capabilities']['restore'] is False
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    assert client.post('/api/local-host/projects/backups/' + job_id + '/restore', json=body, headers={'Idempotency-Key': 'no-owner'}).status_code == 403


def test_backup_restore_refuses_another_actor_and_same_id_other_workspace(tmp_path):
    from backend.engine.project_archive import restore_archive
    from backend.api.routes_project import _load_project
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    other = app.state.accounts.create_user('other-owner', 'another test password')
    app.state.accounts.set_membership(project['id'], other['id'], 'owner', session['user']['id'])
    next_session = app.state.accounts.login('other-owner', 'another test password', transport='cookie')
    other_client = TestClient(app, base_url='https://localhost:8443', headers={'Origin': 'https://localhost:8443', 'X-Vision-CSRF': next_session['csrf_token'], 'X-Vision-Context': json.dumps({**project['context'], 'actor_id': other['id']})})
    other_client.cookies.set('vision_session', next_session['token'])
    other_client.headers['X-Vision-Local-Tab'] = other_client.post('/api/local-host/session').json()['tab_id']
    assert other_client.post(route, json=body, headers={'Idempotency-Key': 'foreign-actor'}).status_code == 404
    restored = _load_project(restore_archive(Path(view['result_ref']['path']), tmp_path / 'same-id-copy'))
    registered = app.state.local_projects.register(app, session['user'], restored)
    response = client.post(route, json=body, headers={'Idempotency-Key': 'foreign-namespace', 'X-Vision-Context': json.dumps(registered['context'])})
    assert response.status_code == 404
    assert not (parent / 'restored-project').exists()


@pytest.mark.parametrize('change', ['archive', 'membership', 'permission_revision'])
def test_backup_restore_rechecks_custody_before_publish_and_never_replays_partial(tmp_path, monkeypatch, change):
    from backend.engine import project_archive
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    other = app.state.accounts.create_user('other-owner', 'another test password')
    app.state.accounts.set_membership(project['id'], other['id'], 'owner', session['user']['id'])
    headers = select_target(client)
    real_restore = project_archive._restore_archive
    calls = []
    def intervening_restore(archive_path, target_dir, **options):
        calls.append(1)
        before = options['before_publish']
        def intervene(staging, target):
            if change == 'archive':
                path = Path(archive_path); path.write_bytes(path.read_bytes() + b'changed')
            else:
                app.state.accounts.set_membership(project['id'], session['user']['id'], 'trainer', other['id'])
                if change == 'permission_revision':
                    app.state.accounts.set_membership(project['id'], session['user']['id'], 'owner', other['id'])
            before(staging, target)
        return real_restore(archive_path, target_dir, **{**options, 'before_publish': intervene})
    monkeypatch.setattr(project_archive, '_restore_archive', intervening_restore)
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    failed = client.post(route, json=body, headers=headers)
    assert failed.status_code == (403 if change == 'membership' else 409), failed.text
    assert not (parent / 'restored-project').exists()
    assert list(parent.glob('.restored-project.local-restore-*'))  # retained verification evidence
    retry = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert retry.status_code in {403, 409}
    assert len(calls) == 1


def test_missing_native_grant_creates_no_restore_or_reservation(tmp_path):
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    response = client.post('/api/local-host/projects/backups/' + job_id + '/restore', json={
        'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']},
        headers={'Idempotency-Key': 'no-native-proof'})
    assert response.status_code == 403
    with app.state.local_projects.db() as db:
        assert db.execute('SELECT count(*) FROM backup_restore_requests').fetchone()[0] == 0
    assert list(parent.iterdir()) == []


def test_uncertain_registration_failure_is_journalled_and_never_reextracts(tmp_path, monkeypatch):
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    headers = select_target(client)
    registry = app.state.local_projects
    def unavailable(*args, **kwargs):raise OSError('Injected registration outage')
    monkeypatch.setattr(registry, 'register', unavailable)
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    with pytest.raises(OSError):client.post(route, json=body, headers=headers)
    target = parent / 'restored-project'; manifest = (target / 'project.json').read_bytes()
    with registry.db() as db:
        row = db.execute('SELECT operation_id FROM backup_restore_requests').fetchone()
        receipt = db.execute('SELECT phase FROM operations WHERE id=?', (row['operation_id'],)).fetchone()
    assert receipt['phase'] == 'published'
    retry = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert retry.status_code == 409 and 'recovery_required' in retry.text
    assert (target / 'project.json').read_bytes() == manifest
    assert len(list(parent.iterdir())) == 1


def interrupted_task_restore(tmp_path, monkeypatch):
    values = fixture(tmp_path)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    registry = app.state.local_projects
    real_register = registry.register
    def unavailable(*args, **kwargs):raise OSError('Injected registration outage')
    monkeypatch.setattr(registry, 'register', unavailable)
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    with pytest.raises(OSError):client.post(route, json=body, headers=select_target(client))
    monkeypatch.setattr(registry, 'register', real_register)
    with registry.db() as db:
        operation = db.execute('SELECT operation_id FROM backup_restore_requests').fetchone()['operation_id']
    return values, route, body, operation


def recover_task(client, helper, body, operation, entry):
    if entry == 'adopt':
        return client.post('/api/local-host/projects/operations/' + operation + '/adopt', json={'confirmed': True})
    if entry == 'named':
        return client.post('/api/local-host/projects/operations/' + operation + '/recover')
    helper.path = Path(body['target_dir'])
    grant = client.post('/api/local-host/dialog/folder', json={'purpose': 'project-recover-target'})
    assert grant.status_code == 200, grant.text
    return client.post('/api/local-host/projects/recover', json={'operation_id': operation, 'target_dir': body['target_dir']},
        headers={'X-Vision-Path-Grants': json.dumps([grant.json()['grant_id']])})


@pytest.mark.parametrize('entry', ['selected', 'named', 'adopt'])
@pytest.mark.parametrize('change', ['removed', 'role', 'revision', 'labelset', 'archive'])
def test_recovery_rechecks_captured_backup_source_without_context_header(tmp_path, monkeypatch, entry, change):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    actor = session['user']['id']
    if change in {'removed', 'role', 'revision'}:
        other = app.state.accounts.create_user('remaining-owner', 'another test password')
        app.state.accounts.set_membership(project['id'], other['id'], 'owner', actor)
        if change == 'removed':app.state.accounts.remove_membership(project['id'], actor, other['id'])
        else:
            app.state.accounts.set_membership(project['id'], actor, 'trainer', other['id'])
            if change == 'revision':app.state.accounts.set_membership(project['id'], actor, 'owner', other['id'])
    elif change == 'archive':
        archive = Path(view['result_ref']['path']); archive.write_bytes(archive.read_bytes() + b'changed')
    else:
        result = client.post('/api/project/labelsets', json={'name': 'Changed source labelset'})
        assert result.status_code == 200, result.text
        activated = client.put('/api/project/labelsets/' + result.json()['id'] + '/activate')
        assert activated.status_code == 200, activated.text
    client.headers.pop('X-Vision-Context', None)
    recovered = recover_task(client, helper, body, operation, entry)
    assert recovered.status_code in {403, 409}, recovered.text
    with app.state.local_projects.db() as db:
        assert db.execute('SELECT count(*) FROM locations WHERE path=?', (body['target_dir'],)).fetchone()[0] == 0
        assert db.execute('SELECT phase FROM operations WHERE id=?', (operation,)).fetchone()['phase'] == 'published'


@pytest.mark.parametrize('entry', ['selected', 'named'])
def test_recovered_task_receipt_replays_without_reextract_or_context_switch(tmp_path, monkeypatch, entry):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    def forbidden(*args, **kwargs):raise AssertionError('Recovery must never extract again')
    monkeypatch.setattr('backend.engine.project_archive._restore_archive', forbidden)
    manifest = (Path(body['target_dir']) / 'project.json').read_bytes()
    recovered = recover_task(client, helper, body, operation, entry)
    assert recovered.status_code == 200, recovered.text
    replay = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert replay.status_code == 200, replay.text
    assert replay.json()['idempotent_replay'] is True
    assert 'x-vision-context' not in replay.headers
    assert client.get('/api/project/current').json()['project_dir'] == project['project_dir']
    assert (Path(body['target_dir']) / 'project.json').read_bytes() == manifest


def test_recovery_refuses_changed_published_inventory_before_registration(tmp_path, monkeypatch):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    (Path(body['target_dir']) / 'reports' / 'tampered.txt').write_text('changed before recovery')
    recovered = recover_task(client, helper, body, operation, 'selected')
    assert recovered.status_code == 409, recovered.text


def test_new_session_and_tab_require_explicit_adoption_of_backup_receipt(tmp_path, monkeypatch):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    app.state.accounts.logout(session['token'])
    assert client.post('/api/local-host/projects/operations/' + operation + '/recover').status_code == 401
    fresh = app.state.accounts.login('owner', 'long test password', transport='cookie')
    client.cookies.set('vision_session', fresh['token'])
    client.headers['X-Vision-CSRF'] = fresh['csrf_token']
    client.headers.pop('X-Vision-Context', None)
    client.headers['X-Vision-Local-Tab'] = client.post('/api/local-host/session').json()['tab_id']
    assert recover_task(client, helper, body, operation, 'named').status_code == 404
    assert recover_task(client, helper, body, operation, 'adopt').status_code == 403
    assert client.post('/api/local-host/approve', json={}).status_code == 200
    assert recover_task(client, helper, body, operation, 'adopt').status_code == 200
    assert recover_task(client, helper, body, operation, 'named').status_code == 200
    assert client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'}).status_code == 409


def test_recovery_refuses_missing_inventory_instead_of_learning_current_output(tmp_path, monkeypatch):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    with app.state.local_projects.db() as db:
        payload = json.loads(db.execute('SELECT payload FROM operations WHERE id=?', (operation,)).fetchone()['payload'])
        payload.pop('restored_inventory')
        db.execute('UPDATE operations SET payload=? WHERE id=?', (json.dumps(payload), operation))
    response = recover_task(client, helper, body, operation, 'selected')
    assert response.status_code == 409, response.text
    with app.state.local_projects.db() as db:
        assert db.execute('SELECT count(*) FROM locations WHERE path=?', (body['target_dir'],)).fetchone()[0] == 0


def test_first_published_receipt_recovers_verified_inventory_without_reextract(tmp_path, monkeypatch):
    import backend.local_host.projects as local_projects
    app, client, helper, session, project, jobs, job_id, view, parent = fixture(tmp_path)
    real_journal = local_projects._journal
    def stop_at_publication(registry, operation, account, kind, phase, payload):
        real_journal(registry, operation, account, kind, phase, payload)
        if phase == 'published':raise OSError('Interrupted at first durable publication')
    monkeypatch.setattr(local_projects, '_journal', stop_at_publication)
    route = '/api/local-host/projects/backups/' + job_id + '/restore'
    body = {'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': view['result_ref']['sha256']}
    failed = client.post(route, json=body, headers=select_target(client))
    assert failed.status_code == 422, failed.text  # archive publication wraps the interrupted callback
    monkeypatch.setattr(local_projects, '_journal', real_journal)
    with app.state.local_projects.db() as db:
        operation = db.execute('SELECT operation_id FROM backup_restore_requests').fetchone()['operation_id']
    def forbidden(*args, **kwargs):raise AssertionError('Recovery must not extract again')
    monkeypatch.setattr('backend.engine.project_archive._restore_archive', forbidden)
    recovered = recover_task(client, helper, body, operation, 'selected')
    assert recovered.status_code == 200, recovered.text
    replay = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert replay.status_code == 200 and replay.json()['idempotent_replay'] is True, replay.text


@pytest.mark.parametrize('entry', ['selected', 'named', 'adopt'])
def test_completed_recovery_receipt_still_checks_source_authority(tmp_path, monkeypatch, entry):
    values, route, body, operation = interrupted_task_restore(tmp_path, monkeypatch)
    app, client, helper, session, project, jobs, job_id, view, parent = values
    assert recover_task(client, helper, body, operation, 'named').status_code == 200
    other = app.state.accounts.create_user('remaining-owner', 'another test password')
    app.state.accounts.set_membership(project['id'], other['id'], 'owner', session['user']['id'])
    app.state.accounts.remove_membership(project['id'], session['user']['id'], other['id'])
    client.headers.pop('X-Vision-Context', None)
    assert recover_task(client, helper, body, operation, entry).status_code in {403, 409}


@pytest.mark.parametrize('original_in_memory', [False, True])
def test_native_restored_model_receipt_and_saved_evaluation_use_new_namespace(tmp_path, monkeypatch, original_in_memory):
    """Synthetic checkpoint metadata only: no trainer, inference, or actual model construction."""
    import hashlib
    import torch
    from PIL import Image
    from backend.api import routes_training
    from backend.contracts.context import ProjectContext
    from backend.engine.evaluation_history import EvaluationHistory
    app, client, helper, session, project, jobs, old_backup, _, parent = fixture(tmp_path)
    manager = routes_training.TrainingJobManager(local_execution='embedded')
    monkeypatch.setattr(routes_training, 'training_job_manager', manager)
    source = Path(project['dataset_dir']) / 'source'
    for split in ('train', 'val', 'test'):
        for label in ('OK', 'NG'):
            image = source / split / label / 'sample.png'; image.parent.mkdir(parents=True)
            Image.new('RGB', (8, 8), 'white').save(image)
    update = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert update.status_code == 200, update.text
    project = client.get('/api/project/current').json()
    version = client.post('/api/dataset/versions', json={'name': 'Synthetic saved checkpoint input'})
    assert version.status_code == 200, version.text
    directory = Path(project['project_dir']) / 'versions' / version.json()['id']
    manifest = json.loads((directory / 'manifest.json').read_text())
    binding = {'dataset_version_id': directory.name, 'version_dir': str(directory),
        'manifest_sha256': manifest['content_digest'], 'dataset_fingerprint': manifest['dataset_fingerprint'],
        'labelset_id': 'default', 'split_binding': 'versioned_dataset_layout',
        'split_sha256': hashlib.sha256(json.dumps([{key: row[key] for key in ('origin', 'relative_path', 'sha256')}
            for row in manifest['files']], sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
    job_id = 'job_saved_archive_' + project['id']
    output = Path(project['models_dir']) / job_id; output.mkdir()
    checkpoint = output / 'best_model.pt'
    torch.save({'task': 'classification', 'classes': ['OK', 'NG'], 'backbone': 'resnet18',
        'model_state_dict': {'weight': torch.zeros(2, 3)}, 'training_provenance': binding}, checkpoint)
    (output / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'classes': ['OK', 'NG'], 'backbone': 'resnet18'}))
    record = routes_training.JobRecord(job_id=job_id, task='classification', preset='fast',
        dataset_path=str(source), source_dataset_path=str(source), dataset_fingerprint=binding['dataset_fingerprint'],
        output_dir=str(output), status='completed', phase='completed', dataset_binding=binding,
        current_epoch=2, total_epochs=2, loss_history=[{'epoch': 2, 'train_loss': .4, 'val_loss': .5}])
    routes_training._write_job_receipt(record)
    if original_in_memory:manager.restore_terminal_job(record)
    saved = EvaluationHistory(Path(project['reports_dir']) / 'evaluations').append(
        {'job_id': job_id, 'task': 'classification', 'quality_approved': False, 'test_predictions': []},
        {'source_dataset_path': str(source), 'labelset_id': 'default', 'checkpoint_path': str(checkpoint),
         'model_sha256': hashlib.sha256(checkpoint.read_bytes()).hexdigest(), 'training_provenance': binding})
    original_checkpoint = checkpoint.read_bytes()
    context = ProjectContext(**json.loads(client.headers['X-Vision-Context']))
    key = app.state.context_registry.project_key(context)
    terminal = jobs.store.submit(context, key, 'training', {'task': 'classification', 'dataset_path': str(source)},
        job_id='job_ledger_' + project['id'], output_dir=str(Path(project['models_dir']) / 'original-ledger-only'), project_dir=project['project_dir'])
    jobs.store.transition(terminal.id, terminal.revision, 'interrupt', {'reason': 'Synthetic interrupted metadata fixture; no worker run'})
    assert any(row['job_id'] == terminal.id for row in client.get('/api/training/jobs').json()['jobs'])
    backup = jobs.submit(context, key, project); jobs.run(backup.id)
    verified = jobs.view(backup.id, key, context.actor_id)
    assert verified['downloadable'], verified
    restored = client.post('/api/local-host/projects/backups/' + backup.id + '/restore', json={
        'target_dir': str(parent / 'restored-project'), 'expected_archive_sha256': verified['result_ref']['sha256']}, headers=select_target(client))
    assert restored.status_code == 200, restored.text
    result = restored.json()
    assert result['project_id'] == project['id'] and result['workspace_id'] != context.workspace_id
    chosen = client.post('/api/local-host/projects/select', json={'registration_id': result['registration_id']})
    assert chosen.status_code == 200, chosen.text
    client.headers['X-Vision-Context'] = chosen.headers['x-vision-context']
    copied = chosen.json(); copied_source = copied['source_dataset_dir']
    models = client.get('/api/model-operations/models')
    assert models.status_code == 200, models.text
    assert [row['job_id'] for row in models.json()['models']] == [job_id]
    history = client.get('/api/evaluation/history/' + saved['evaluation_id'], params={'source_dataset_path': copied_source, 'task': 'classification'})
    assert history.status_code == 200, history.text
    assert history.json()['binding']['checkpoint_path'] == str(Path(copied['models_dir']) / job_id / 'best_model.pt')
    assert history.json()['original_evidence_sha256'] == saved['evidence_sha256']
    assert client.get('/api/evaluation/history/' + saved['evaluation_id'], params={'source_dataset_path': str(source), 'task': 'classification'}).status_code in {403, 409}
    assert client.get('/api/dataset/operations/backups/' + backup.id).status_code == 404
    assert client.get('/api/training/jobs').json()['jobs'] == []  # no original execution is imported
    assert client.get('/api/training/status', params={'job_id': terminal.id}).json()['status'] == 'idle'
    status = client.get('/api/training/status', params={'job_id': job_id})
    assert status.status_code == 200, status.text
    assert status.json()['status'] == 'completed', status.text
    assert status.json()['loss_history'] == record.loss_history
    assert (Path(copied['models_dir']) / job_id / 'best_model.pt').read_bytes() == original_checkpoint
    assert checkpoint.read_bytes() == original_checkpoint
    (Path(copied['models_dir']) / job_id / 'best_model.pt').write_bytes(b'changed restored checkpoint')
    assert client.get('/api/training/status', params={'job_id': job_id}).json()['status'] == 'idle'
    assert checkpoint.read_bytes() == original_checkpoint


@pytest.mark.parametrize('registration_outage', [False, True])
@pytest.mark.parametrize('legacy_layout', ['schema_only', 'without_labelsets', 'active_labelset'])
def test_legacy_backup_normalizes_in_staging_before_immutable_publication(tmp_path, monkeypatch, registration_outage, legacy_layout):
    from backend.contracts.context import ProjectContext
    from backend.engine.project_archive import restore_archive, _digest_file
    from backend.api.routes_project import _load_project
    app, client, helper, session, project, jobs, _, _, parent = fixture(tmp_path)
    if legacy_layout == 'active_labelset':
        labelset = client.post('/api/project/labelsets', json={'name': 'Legacy active overlay'}).json()['id']
        activated = client.put('/api/project/labelsets/' + labelset + '/activate')
        assert activated.status_code == 200, activated.text
        project = {**project, **activated.json()}
    if legacy_layout == 'without_labelsets':
        (Path(project['project_dir']) / 'labelsets.json').unlink()
    manifest = Path(project['project_dir']) / 'project.json'
    legacy = json.loads(manifest.read_text()); legacy.pop('schema_version')
    manifest.write_text(json.dumps(legacy))
    context = ProjectContext(**project['context']); key = app.state.context_registry.project_key(context)
    backup = jobs.submit(context, key, project); jobs.run(backup.id)
    view = jobs.view(backup.id, key, context.actor_id)
    assert view['downloadable']
    archive = Path(view['result_ref']['path'])
    control = _load_project(restore_archive(archive, tmp_path / 'legacy-engine-control'))
    assert control['schema_version'] == 1 and control['id'] == project['id']
    registry = app.state.local_projects; real_register = registry.register
    if registration_outage:
        def unavailable(*args, **kwargs):raise OSError('Injected legacy registration outage')
        monkeypatch.setattr(registry, 'register', unavailable)
    target = parent / 'restored-project'
    route = '/api/local-host/projects/backups/' + backup.id + '/restore'
    body = {'target_dir': str(target), 'expected_archive_sha256': view['result_ref']['sha256']}
    if registration_outage:
        with pytest.raises(OSError):client.post(route, json=body, headers=select_target(client))
        monkeypatch.setattr(registry, 'register', real_register)
        with registry.db() as db:
            operation = db.execute('SELECT operation_id FROM backup_restore_requests').fetchone()['operation_id']
        recovered = recover_task(client, helper, body, operation, 'selected')
        assert recovered.status_code == 200, recovered.text
    else:
        result = client.post(route, json=body, headers=select_target(client))
        assert result.status_code == 200, result.text
    saved = json.loads((target / 'project.json').read_text())
    assert saved['schema_version'] == 1 and saved['id'] == project['id']
    assert saved['project_dir'] == str(target)
    migrations = list((target / '.migrations').glob('*/receipt.json'))
    assert len(migrations) == 1 and json.loads(migrations[0].read_text())['status'] == 'applied'
    original = json.loads(migrations[0].with_name('project.original.json').read_text())
    assert 'schema_version' not in original and original['id'] == project['id']
    assert original['project_dir'] == str(target)
    replay = client.post(route, json=body, headers={'Idempotency-Key': 'task-restore-test'})
    assert replay.status_code == 200 and replay.json()['idempotent_replay'] is True, replay.text
    assert _digest_file(archive) == view['result_ref']['sha256']


def test_verified_unsupported_schema_archive_is_refused_before_publication(tmp_path):
    from backend.contracts.context import ProjectContext
    from backend.engine.project_archive import _digest_file
    app, client, helper, session, project, jobs, _, _, parent = fixture(tmp_path)
    manifest = Path(project['project_dir']) / 'project.json'; original = manifest.read_bytes()
    unsupported = json.loads(original); unsupported['schema_version'] = 99
    manifest.write_text(json.dumps(unsupported))
    context = ProjectContext(**project['context']); key = app.state.context_registry.project_key(context)
    backup = jobs.submit(context, key, project); jobs.run(backup.id)
    view = jobs.view(backup.id, key, context.actor_id)
    assert view['downloadable'], view
    manifest.write_bytes(original)  # current source is valid; only the completed archive is unsupported
    target = parent / 'restored-project'
    result = client.post('/api/local-host/projects/backups/' + backup.id + '/restore', json={
        'target_dir': str(target), 'expected_archive_sha256': view['result_ref']['sha256']}, headers=select_target(client))
    assert result.status_code == 422, result.text
    assert not target.exists()
    assert _digest_file(Path(view['result_ref']['path'])) == view['result_ref']['sha256']
    with app.state.local_projects.db() as db:
        assert db.execute("SELECT count(*) FROM operations WHERE kind='restore' AND phase IN ('published','completed')").fetchone()[0] == 0

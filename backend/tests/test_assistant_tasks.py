"""Durable assistant plans never substitute for action permission or human decisions."""
import copy
import hashlib
import json
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

PASSWORD = 'task-test-password-123'
BINDING = 'workspace:A/project:none/epoch:1'


def plan(**changes):
    value = {'goal': '프로젝트 준비', 'binding': BINDING, 'steps': [
        {'action': 'open_step', 'args': {'step': 1}, 'label': '자료 화면 열기'}]}
    return {**value, **changes}


def desktop(tmp_path):
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path/'projects'))
    return app, TestClient(app, headers={'X-Vision-Token': app.state.api_token})


def team(tmp_path, monkeypatch=None):
    from backend.main import create_app
    if monkeypatch:
        monkeypatch.setenv('MODU_BROWSER_ORIGINS', '["https://localhost:8443"]')
    app = create_app(project_dir=str(tmp_path/'projects'), shared_auth_dir=str(tmp_path/'auth'))
    app.state.accounts.bootstrap('admin', PASSWORD)
    app.state.accounts.create_user('other', PASSWORD, False)
    clients = []
    for username in ('admin', 'other'):
        client = TestClient(app)
        login = client.post('/api/accounts/login', json={'username': username, 'password': PASSWORD}).json()
        client.headers['Authorization'] = 'Bearer '+login['token']
        clients.append(client)
    return app, *clients


def create(client, value=None):
    response = client.post('/api/assistant-tasks', json=value or plan())
    assert response.status_code == 200, response.text
    return response.json()['task']


def event(client, task, kind, **fields):
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'expected_revision': task['revision'], 'binding': task['binding'], 'type': kind, **fields})
    assert response.status_code == 200, response.text
    return response.json()['task']


@pytest.mark.parametrize('source', ['user_request', 'oneclick'])
def test_execution_authorization_is_durable_scoped_and_never_human_approval(tmp_path, source):
    app, client = desktop(tmp_path)
    task = create(client)
    authorized = event(client, task, 'authorize', authorization_source=source)
    assert authorized['status'] == 'ready'
    assert authorized['approval'] is None
    grant = authorized['authorization']
    assert grant == {'source': source, 'scope': {
        'workspace_id': app.state.context_registry.workspace_id,
        'actor_id': app.state.context_registry.local_actor_id},
        'plan_sha256': task['plan_sha256'], 'binding': BINDING, 'allowed_steps': [step['id'] for step in task['steps']],
        'policy_version': 'continuous-execution-v1', 'authorized_at': grant['authorized_at']}
    assert grant['authorized_at']
    started = event(client, authorized, 'begin_step', step_id=task['steps'][0]['id'], attempt_id='attempt-auto')
    finished = event(client, started, 'complete_step', step_id=task['steps'][0]['id'], attempt_id='attempt-auto', receipt={'opened_step': 1})
    assert finished['status'] == 'completed'
    assert finished['authorization'] == grant and finished['approval'] is None
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == finished


def test_execution_authorization_cannot_auto_resume_an_explicit_pause_or_change_scope(tmp_path):
    _, client = desktop(tmp_path)
    task = create(client)
    authorized = event(client, task, 'authorize', authorization_source='user_request')
    paused = event(client, authorized, 'pause')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'authorize', 'authorization_source': 'user_request', 'expected_revision': paused['revision'], 'binding': BINDING})
    assert response.status_code == 409
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task']['status'] == 'paused'
    other = create(client)
    assert client.post('/api/assistant-tasks/'+other['id']+'/events', json={
        'type': 'authorize', 'authorization_source': 'advice', 'expected_revision': 1, 'binding': BINDING}).status_code == 422


def test_machine_input_recheck_does_not_attest_human_review_or_resume_an_explicit_pause(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'authorize', authorization_source='user_request')
    step_id = task['steps'][0]['id']
    running = event(client, task, 'begin_step', step_id=step_id, attempt_id='input-attempt')
    waiting = event(client, running, 'wait_step', step_id=step_id, attempt_id='input-attempt',
                    wait_kind='human', receipt={'not_started': True, 'reason_code': 'waiting_input'})
    rechecking = event(client, waiting, 'recheck')
    assert rechecking['status'] == 'needs_reconciliation' and rechecking['approval'] is None
    paused = event(client, rechecking, 'pause')
    response = client.post('/api/assistant-tasks/'+paused['id']+'/events', json={
        'type': 'recheck', 'binding': BINDING, 'expected_revision': paused['revision']})
    assert response.status_code == 409


def test_input_recipe_plan_uses_canonical_bounded_operations(tmp_path):
    _, client = desktop(tmp_path)
    recipe = {'version': 1, 'operations': [{'op': 'crop', 'roi': [0, 0, .5, 1]}, {'op': 'rotate90', 'turns': 1}]}
    value = plan(steps=[{'action': 'configure_input_recipe', 'args': {'input_recipe': recipe}, 'label': '학습 입력 규칙'}])
    assert create(client, value)['steps'][0]['args']['input_recipe'] == recipe
    recipe['operations'][0]['unknown'] = True
    assert client.post('/api/assistant-tasks', json=value).status_code == 422


def test_renderer_cannot_invent_quality_result_through_generic_ledger_events(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'authorize', authorization_source='user_request')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'quality_result', 'expected_revision': task['revision'], 'binding': task['binding'],
        'step_id': task['steps'][0]['id'], 'attempt_id': 'a',
        'receipt': {'quality_assessment': {'assessment_id': 'quality_'+'a'*32, 'status': 'qualified'}}})
    assert response.status_code == 422


def test_authenticated_no_project_plan_does_not_bootstrap_unrelated_apis(tmp_path):
    app, admin, other = team(tmp_path)
    task = create(admin)
    assert task['status'] == 'draft' and task['revision'] == 1
    assert task['approval'] is None
    assert admin.get('/api/project/current').status_code == 409
    assert admin.post('/api/training/start', json={}).status_code == 409
    assert admin.get('/api/assistant-tasks-extra').status_code == 409
    assert list((tmp_path/'projects').glob('*/project.json')) == []
    assert list((tmp_path/'projects').rglob('migration_admission.lock')) == []
    assert other.get('/api/assistant-tasks').json() == {'tasks': []}
    assert other.get('/api/assistant-tasks/'+task['id']).status_code == 404
    assert other.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'approve', 'binding': BINDING, 'expected_revision': 1, 'confirmed': True}).status_code == 404
    assert TestClient(app).get('/api/assistant-tasks').status_code == 401


def test_desktop_capability_and_browser_csrf_are_still_required(tmp_path, monkeypatch):
    app, client = desktop(tmp_path/'desktop')
    assert TestClient(app).post('/api/assistant-tasks', json=plan()).status_code == 401
    assert create(client)['goal'] == '프로젝트 준비'
    shared, _, _ = team(tmp_path/'shared', monkeypatch)
    browser = TestClient(shared, base_url='https://localhost:8443', headers={'Origin': 'https://localhost:8443'})
    login = browser.post('/api/accounts/login', json={'username': 'admin', 'password': PASSWORD, 'transport': 'cookie'})
    assert login.status_code == 200, login.text
    assert browser.post('/api/assistant-tasks', json=plan()).status_code == 403
    browser.headers['X-Vision-CSRF'] = login.json()['csrf_token']
    assert create(browser)['status'] == 'draft'
    browser.headers['Origin'] = 'https://attacker.example'
    assert browser.post('/api/assistant-tasks', json=plan()).status_code == 403


def test_plan_hash_immutable_grant_and_cas_are_persisted(tmp_path):
    app, client = desktop(tmp_path)
    task = create(client)
    canonical = json.dumps(plan(), sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    assert task['plan_sha256'] == hashlib.sha256(canonical).hexdigest()
    stale = copy.deepcopy(task)
    approved = event(client, task, 'approve', confirmed=True)
    assert approved['status'] == 'ready'
    assert approved['approval']['binding'] == BINDING
    assert approved['approval']['plan_sha256'] == task['plan_sha256']
    assert client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'approve', 'expected_revision': stale['revision'], 'binding': BINDING, 'confirmed': True}).status_code == 409
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == approved
    from backend.engine.assistant_tasks import TaskLedger, TaskScope
    reloaded = TaskLedger(app.state.assistant_tasks.root)
    scope = TaskScope(app.state.context_registry.workspace_id, app.state.context_registry.local_actor_id)
    assert reloaded.read(scope, task['id']) == approved


def test_no_approval_no_skipping_no_wrong_attempt_and_receipt_required(tmp_path):
    _, client = desktop(tmp_path)
    task = create(client, plan(steps=[{'action': 'open_step', 'args': {'step': 1}, 'label': 'first'},
                                    {'action': 'open_step', 'args': {'step': 2}, 'label': 'second'}]))
    step = task['steps'][0]
    def rejected(value, code=409):
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': 'begin_step', 'expected_revision': task['revision'], 'binding': task['binding'],
            'step_id': step['id'], 'attempt_id': 'attempt-1', **value})
        assert response.status_code == code, response.text
    rejected({})
    task = event(client, task, 'approve', confirmed=True)
    rejected({'step_id': task['steps'][1]['id']})
    rejected({'binding': 'another-context'})
    task = event(client, task, 'begin_step', step_id=step['id'], attempt_id='attempt-1')
    rejected({})
    rejected({'type': 'complete_step', 'attempt_id': 'wrong', 'receipt': {'step': 1}})
    rejected({'type': 'complete_step'}, 422)
    task = event(client, task, 'complete_step', step_id=step['id'], attempt_id='attempt-1', receipt={'step': 1})
    assert task['steps'][0]['status'] == 'completed'
    assert task['status'] == 'ready' and task['current_step_id'] is None


def test_ambiguous_running_recovery_reconciles_instead_of_retrying(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'resume')
    assert task['status'] == 'needs_reconciliation' and task['steps'][0]['status'] == 'uncertain'
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'begin_step', 'expected_revision': task['revision'], 'binding': BINDING,
        'step_id': sid, 'attempt_id': 'attempt-2'})
    assert response.status_code == 409
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', receipt={'step': 1})
    assert task['status'] == 'completed' and task['steps'][0]['attempt_id'] == 'attempt-1'


def test_pause_wait_and_human_resume_never_manufacture_completion(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    task = event(client, task, 'pause')
    assert task['status'] == 'paused'
    task = event(client, task, 'resume')
    assert task['status'] == 'ready'
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'draft_id': 'draft-1'})
    assert task['status'] == 'waiting_human'
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'expected_revision': task['revision'], 'binding': BINDING, 'type': 'resume'})
    assert response.status_code == 409
    task = event(client, task, 'resume', confirmed=True)
    assert task['status'] == 'needs_reconciliation'
    assert task['steps'][0]['status'] != 'completed'


@pytest.mark.parametrize('patch', [
    {'steps': [{'action': 'approve_model', 'args': {}, 'label': 'bad'}]},
    {'steps': [{'action': 'open_step', 'args': {'step': 1, 'script': 'arbitrary'}, 'label': 'bad'}]},
    {'steps': [{'action': 'open_step', 'args': {'step': True}, 'label': 'bad'}]},
    {'steps': [{'action': 'start_training', 'args': {'actor': 'other'}, 'label': 'bad'}]},
    {'steps': [{'action': 'save_split', 'args': {'train_percent': 90, 'val_percent': 20, 'test_percent': 10}, 'label': 'bad'}]},
    {'steps': []}, {'binding': 'b'*16385}, {'goal': 'g'*4097}, {'approval': {'confirmed': True}},
])
def test_strict_plan_action_and_size_limits(tmp_path, patch):
    _, client = desktop(tmp_path)
    response = client.post('/api/assistant-tasks', json=plan(**patch))
    assert response.status_code == 422, response.text


def test_oversized_body_and_receipt_do_not_persist(tmp_path):
    _, client = desktop(tmp_path)
    assert client.post('/api/assistant-tasks', content=b' '*70000, headers={'Content-Type': 'application/json'}).status_code == 413
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'complete_step', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': {'large': 'x'*17000}})
    assert response.status_code == 422
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_symlinks_and_tampered_immutable_plan_are_refused(tmp_path):
    app, client = desktop(tmp_path)
    task = create(client)
    files = list(app.state.assistant_tasks.root.rglob(task['id']+'.json'))
    assert len(files) == 1
    record_file = files[0]
    assert record_file.stat().st_mode & 0o777 == 0o600
    stored = json.loads(record_file.read_text())
    stored['task']['steps'][0]['args']['step'] = 6
    record_file.write_text(json.dumps(stored))
    assert client.get('/api/assistant-tasks/'+task['id']).status_code == 409
    record_file.unlink()
    secret = tmp_path/'secret.json'; secret.write_text('sensitive')
    record_file.symlink_to(secret)
    assert client.get('/api/assistant-tasks/'+task['id']).status_code == 409
    assert secret.read_text() == 'sensitive'


def test_parallel_revisions_have_only_one_winner(tmp_path):
    _, client = desktop(tmp_path)
    task = create(client)
    def approve(_):
        return client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': 'approve', 'expected_revision': 1, 'binding': BINDING, 'confirmed': True}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(approve, range(2))) == [200, 409]


def test_same_actor_in_different_workspace_has_no_shared_tasks(tmp_path):
    from backend.engine.assistant_tasks import TaskError, TaskLedger, TaskScope
    ledger = TaskLedger(tmp_path/'ledger')
    first = TaskScope('workspace-one', 'same-actor')
    second = TaskScope('workspace-two', 'same-actor')
    task = ledger.create(first, plan())
    assert ledger.list(second) == []
    with pytest.raises(TaskError) as refused:
        ledger.read(second, task['id'])
    assert getattr(refused.value, 'status_code', None) == 404


@pytest.mark.parametrize('task_kind', ['classification', 'detection', 'segmentation', 'anomaly'])
def test_supported_app_task_kinds_and_catalog_limits_are_accepted(tmp_path, task_kind):
    _, client = desktop(tmp_path)
    value = plan(goal='g'*4096, steps=[{'action': 'create_project', 'label': 'l'*160,
        'args': {'name': 'n'*120, 'task': task_kind, 'description': 'd'*2000}}])
    assert create(client, value)['steps'][0]['args']['task'] == task_kind


@pytest.mark.parametrize('step', [
    {'action': 'configure_training', 'label': 'bad', 'args': {'preset': 'arbitrary'}},
    {'action': 'open_step', 'label': 'bad', 'args': {'step': 1, 'target': '../external'}},
    {'action': 'open_step', 'label': 'bad\x00', 'args': {'step': 1}},
    {'action': 'create_project', 'label': 'bad', 'args': {'name': 'n', 'task': 'object_detection'}},
])
def test_catalog_options_and_control_characters_are_refused(tmp_path, step):
    _, client = desktop(tmp_path)
    assert client.post('/api/assistant-tasks', json=plan(steps=[step])).status_code == 422


def test_expected_binding_transition_keeps_original_plan_and_approval(tmp_path):
    _, client = desktop(tmp_path)
    task = create(client, plan(steps=[{'action': 'import_dataset', 'label': 'import', 'args': {}},
                                    {'action': 'open_step', 'label': 'next', 'args': {'step': 2}}]))
    task = event(client, task, 'approve', confirmed=True)
    approval = copy.deepcopy(task['approval'])
    sha = task['plan_sha256']
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='import-1')
    task = event(client, task, 'complete_step', step_id=sid, attempt_id='import-1', receipt={'dataset_version': 'v2'}, binding_after='project:A/source:v2')
    assert task['binding'] == 'project:A/source:v2'
    assert task['approval'] == approval and task['plan_sha256'] == sha
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task
    sid = task['steps'][1]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='open-1')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'expected_revision': task['revision'], 'binding': task['binding'], 'type': 'complete_step',
        'step_id': sid, 'attempt_id': 'open-1', 'receipt': {'step': 2}, 'binding_after': 'foreign-context'})
    assert response.status_code == 409


def test_running_pause_and_job_wait_keep_attempt_receipts_for_reconciliation(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='job', receipt={'job_id': 'job-1'})
    assert task['status'] == 'waiting_job'
    task = event(client, task, 'pause')
    assert task['steps'][0]['receipt'] == {'job_id': 'job-1'}
    task = event(client, task, 'resume')
    assert task['status'] == 'needs_reconciliation'
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', message='still unavailable')
    assert task['status'] == 'needs_reconciliation' and task['steps'][0]['status'] == 'uncertain'
    assert task['steps'][0]['receipt'] == {'job_id': 'job-1'}
    task = event(client, task, 'pause')
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', receipt={'job_id': 'job-1', 'status': 'completed'})
    assert task['status'] == 'completed'


def test_task_preparation_never_touches_existing_current_project(tmp_path):
    app, client = desktop(tmp_path)
    project = client.post('/api/project/create', json={'name': 'existing'}).json()
    root = Path(project['project_dir'])
    before = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    task = create(client)
    task = event(client, task, 'approve', confirmed=True)
    task = event(client, task, 'pause')
    assert client.get('/api/assistant-tasks').status_code == 200
    after = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    assert after == before
    assert not (root/'migration_admission.lock').exists()
    assert app.state.current_project['id'] == project['id']


def test_task_context_cannot_impersonate_actor_or_unregistered_workspace(tmp_path):
    app, admin, _ = team(tmp_path)
    account = admin.get('/api/accounts/me').json()['user']
    header = {'workspace_id': app.state.context_registry.workspace_id, 'project_id': 'not-created',
              'actor_id': 'other-actor', 'mode': 'team'}
    assert admin.post('/api/assistant-tasks', json=plan(), headers={'X-Vision-Context': json.dumps(header)}).status_code == 403
    header.update(actor_id=account['id'], workspace_id='foreign-workspace')
    assert admin.post('/api/assistant-tasks', json=plan(), headers={'X-Vision-Context': json.dumps(header)}).status_code == 403


def test_linked_storage_root_and_lock_and_grant_tampering_are_refused(tmp_path):
    from backend.engine.assistant_tasks import TaskError, TaskLedger, TaskScope
    scope = TaskScope('workspace', 'actor')
    other = tmp_path/'other'; other.mkdir()
    linked = tmp_path/'linked'; linked.symlink_to(other, target_is_directory=True)
    with pytest.raises(TaskError): TaskLedger(linked).create(scope, plan())
    ledger = TaskLedger(tmp_path/'ledger')
    task = ledger.create(scope, plan())
    stored_path = next(ledger.root.rglob(task['id']+'.json'))
    data = json.loads(stored_path.read_text())
    data['task']['approval'] = {'plan_sha256': 'a'*64, 'binding': BINDING, 'confirmed_at': 'now'}
    stored_path.write_text(json.dumps(data))
    with pytest.raises(TaskError): ledger.read(scope, task['id'])
    lock = stored_path.parent/'.lock'
    lock.unlink(); lock.symlink_to(other/'lock')
    with pytest.raises(TaskError): ledger.list(scope)
    assert not (other/'lock').exists()


@pytest.mark.parametrize('kind, extra', [
    ('approve', {'receipt': {}}), ('pause', {'confirmed': True}),
    ('begin_step', {'receipt': {'pretend': 'done'}}),
    ('fail_step', {'binding_after': 'forged'}),
])
def test_event_fields_cannot_smuggle_another_transition(tmp_path, kind, extra):
    _, client = desktop(tmp_path)
    task = create(client)
    fields = {'type': kind, 'binding': BINDING, 'expected_revision': 1, **extra}
    if kind in {'begin_step', 'fail_step'}:
        fields.update(step_id=task['steps'][0]['id'], attempt_id='attempt-1')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json=fields)
    assert response.status_code == 422, response.text


def test_persisted_unapproved_completion_and_oversized_receipt_are_refused(tmp_path):
    app, client = desktop(tmp_path)
    task = create(client)
    stored_path = next(app.state.assistant_tasks.root.rglob(task['id']+'.json'))
    stored = json.loads(stored_path.read_text())
    stored['task']['status'] = 'completed'
    stored['task']['steps'][0].update(status='completed', receipt={'step': 1}, attempt_id='attempt-1')
    stored_path.write_text(json.dumps(stored))
    assert client.get('/api/assistant-tasks/'+task['id']).status_code == 409
    stored['task']['status'] = 'draft'
    stored['task']['steps'][0].update(status='pending', receipt={'huge': 'x'*20000}, attempt_id=None)
    stored_path.write_text(json.dumps(stored))
    assert client.get('/api/assistant-tasks/'+task['id']).status_code == 409


@pytest.mark.parametrize('field,value', [('goal', '\ud800'), ('binding', '\udfff')])
def test_invalid_unicode_is_a_validation_error_instead_of_server_error(tmp_path, field, value):
    _, client = desktop(tmp_path)
    raw = json.dumps(plan(**{field: value})).encode('ascii')
    response = client.post('/api/assistant-tasks', content=raw, headers={'Content-Type': 'application/json'})
    assert response.status_code == 422, response.text


def test_unsupported_platform_fails_closed_without_disabling_existing_assistant(tmp_path, monkeypatch):
    from backend.engine import assistant_tasks
    app, client = desktop(tmp_path)
    monkeypatch.setattr(assistant_tasks, 'platform_supported', lambda: False, raising=False)
    assert client.get('/api/assistant-tasks').status_code == 501
    assert client.post('/api/assistant-tasks', json=plan()).status_code == 501
    assert not app.state.assistant_tasks.root.exists()
    assert client.post('/api/project/create', json={'name': 'legacy'}).status_code == 200
    assert client.get('/api/assistant/runs').status_code == 200


@pytest.mark.parametrize('threshold, numeric_json', [(0.000001, '0.000001'), (0.0000001, '1e-7')])
def test_plan_hash_uses_interoperable_compact_number_encoding(tmp_path, threshold, numeric_json):
    _, client = desktop(tmp_path)
    task = create(client, plan(goal='drafts', binding='scope', steps=[{
        'action': 'generate_label_drafts', 'label': 'drafts', 'args': {'threshold': threshold}}]))
    canonical = ('{"binding":"scope","goal":"drafts","steps":[{"action":"generate_label_drafts",'
                 '"args":{"threshold":'+numeric_json+'},"label":"drafts"}]}')
    assert task['plan_sha256'] == hashlib.sha256(canonical.encode()).hexdigest()


def test_owned_training_job_wait_can_advance_binding_with_receipt(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client, plan(steps=[{'action': 'start_training', 'label': 'train', 'args': {}}])), 'approve', confirmed=True)
    approval = copy.deepcopy(task['approval'])
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='train-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='train-1', wait_kind='job',
                 receipt={'job_id': 'job-1', 'device': 'cpu', 'batch_size': 4}, binding_after='same-project/consumed-device-cpu/batch-4')
    assert task['status'] == 'waiting_job'
    assert task['binding'] == 'same-project/consumed-device-cpu/batch-4'
    assert task['approval'] == approval
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_approved_plan_never_grants_underlying_project_role(tmp_path):
    _, admin, viewer = team(tmp_path)
    project = admin.post('/api/project/create', json={'name': 'role-bound'}).json()
    user = viewer.get('/api/accounts/me').json()['user']
    assert admin.put('/api/accounts/projects/'+project['id']+'/members', json={'user_id': user['id'], 'role': 'viewer'}).status_code == 200
    assert viewer.post('/api/accounts/select-project', json={'project_id': project['id']}).status_code == 200
    task = create(viewer, plan(steps=[{'action': 'start_training', 'args': {}, 'label': 'train'}]))
    assert event(viewer, task, 'approve', confirmed=True)['status'] == 'ready'
    assert viewer.post('/api/training/start', json={}).status_code == 403
    assert viewer.post('/api/image-truth', json={}).status_code == 403
    assert viewer.post('/api/model-deployments', json={}).status_code == 403


def test_identical_logical_project_ids_in_two_bindings_do_not_share_execution(tmp_path):
    _, client = desktop(tmp_path)
    first = create(client, plan(binding='registered-workspace-one/project:same-id'))
    second = create(client, plan(binding='registered-workspace-two/project:same-id'))
    response = client.post('/api/assistant-tasks/'+first['id']+'/events', json={
        'type': 'approve', 'expected_revision': 1, 'binding': second['binding'], 'confirmed': True})
    assert response.status_code == 409
    assert client.get('/api/assistant-tasks/'+first['id']).json()['task']['approval'] is None


def test_session_revocation_removes_access_to_own_persisted_plan(tmp_path):
    app, admin, _ = team(tmp_path)
    task = create(admin)
    app.state.accounts.logout(admin.headers['Authorization'].removeprefix('Bearer '))
    assert admin.get('/api/assistant-tasks/'+task['id']).status_code == 401
    assert admin.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'approve', 'expected_revision': 1, 'binding': BINDING, 'confirmed': True}).status_code == 401


def test_persisted_out_of_order_completion_is_refused(tmp_path):
    app, client = desktop(tmp_path)
    task = create(client, plan(steps=[{'action': 'open_step', 'args': {'step': 1}, 'label': 'first'},
                                    {'action': 'open_step', 'args': {'step': 2}, 'label': 'second'}]))
    task = event(client, task, 'approve', confirmed=True)
    stored_path = next(app.state.assistant_tasks.root.rglob(task['id']+'.json'))
    stored = json.loads(stored_path.read_text())
    stored['task']['steps'][1].update(status='completed', attempt_id='attempt-2', receipt={'step': 2})
    stored_path.write_text(json.dumps(stored))
    assert client.get('/api/assistant-tasks/'+task['id']).status_code == 409


def test_record_evidence_retains_started_identifier_without_claiming_completion(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client, plan(steps=[{'action': 'start_training', 'label': 'train', 'args': {}}])), 'approve', confirmed=True)
    approval = copy.deepcopy(task['approval'])
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='train-1')
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='train-1',
                 receipt={'job_id': 'job-1', 'scope': {'project_id': 'project-1'}, 'status': 'started'}, message='Check response')
    assert task['status'] == 'needs_reconciliation'
    assert task['steps'][0]['status'] == 'uncertain'
    assert task['steps'][0]['receipt']['job_id'] == 'job-1'
    assert task['binding'] == BINDING and task['approval'] == approval
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='train-1',
                 receipt={'scope': {'source_version_id': 'v1'}, 'status': 'waiting'})
    assert task['steps'][0]['receipt'] == {'job_id': 'job-1', 'scope': {'project_id': 'project-1', 'source_version_id': 'v1'}, 'status': 'waiting'}
    conflict = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'train-1', 'receipt': {'scope': {'project_id': 'project-2'}}})
    assert conflict.status_code == 409
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_paused_attempt_keeps_partial_evidence_then_requires_verified_reconciliation(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'pause')
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='attempt-1', receipt={'run_id': 'run-1'})
    assert task['status'] == 'paused' and task['steps'][0]['status'] == 'uncertain'
    assert task['steps'][0]['receipt'] == {'run_id': 'run-1'}
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'complete_step', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': {'run_id': 'run-1', 'status': 'completed'}})
    assert response.status_code == 409
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', receipt={'run_id': 'run-1', 'status': 'completed'})
    assert task['status'] == 'completed'


@pytest.mark.parametrize('extra', [
    {'receipt': {}}, {'receipt': {'job_id': 'known'}, 'binding_after': 'foreign'},
    {'receipt': {'job_id': 'known'}, 'confirmed': True},
    {'receipt': {'job_id': 'known'}, 'wait_kind': 'job'}, {},
])
def test_evidence_event_cannot_change_authority_or_manufacture_a_wait(tmp_path, extra):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', **extra})
    assert response.status_code == 422
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_evidence_merge_checks_total_size_and_exact_attempt(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='attempt-1', receipt={'job_id': 'job-1', 'first': 'x'*9000})
    for attempt, receipt, expected in [('attempt-2', {'status': 'seen'}, 409), ('attempt-1', {'second': 'x'*9000}, 422), ('attempt-1', {'job_id': 'job-2'}, 409)]:
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
            'step_id': sid, 'attempt_id': attempt, 'receipt': receipt})
        assert response.status_code == expected, response.text
        assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


@pytest.mark.parametrize('kind', ['record_evidence', 'reconcile_step'])
def test_uncertainty_event_cannot_bypass_an_unconfirmed_human_wait(tmp_path, kind):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'draft_id': 'draft-1'})
    fields = {'receipt': {'draft_id': 'draft-1'}} if kind == 'record_evidence' else {}
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': kind, 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', **fields})
    assert response.status_code == 409
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


@pytest.mark.parametrize('kind,fields', [
    ('wait_step', {'wait_kind': 'job', 'receipt': {'job_id': 'job-1'}}),
    ('wait_step', {'wait_kind': 'human', 'receipt': {'draft_id': 'draft-1'}}),
    ('fail_step', {'message': 'skip review'}),
    ('reconcile_step', {'receipt': {'draft_id': 'draft-1', 'not_started': False}}),
])
@pytest.mark.parametrize('paused', [False, True])
def test_every_step_transition_requires_confirmed_resume_after_human_wait(tmp_path, kind, fields, paused):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'draft_id': 'draft-1'})
    if paused: task = event(client, task, 'pause')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': kind, 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', **fields})
    assert response.status_code == 409, response.text
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


@pytest.mark.parametrize('kind, fields', [('wait_step', {'wait_kind': 'job'}), ('reconcile_step', {})])
@pytest.mark.parametrize('foreign', [{'job_id': 'job-foreign'}, {'scope': {'project_id': 'project-foreign'}}, {'scope': 'discard nested IDs'}])
def test_every_receipt_update_preserves_known_execution_identity(tmp_path, kind, fields, foreign):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='job', receipt={'job_id': 'job-1', 'scope': {'project_id': 'project-1'}})
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': kind, 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': foreign, **fields})
    assert response.status_code == 409, response.text
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


@pytest.mark.parametrize('kind, fields', [('wait_step', {'wait_kind': 'job'}), ('reconcile_step', {})])
def test_receipt_update_retains_omitted_identifiers_and_checks_merged_size(tmp_path, kind, fields):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='job', receipt={'job_id': 'job-1', 'first': 'x'*9000})
    for receipt in ({}, {'second': 'x'*9000}):
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': kind, 'binding': BINDING, 'expected_revision': task['revision'],
            'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': receipt, **fields})
        assert response.status_code == 422, response.text
        assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task
    task = event(client, task, kind, step_id=sid, attempt_id='attempt-1', receipt={'samples': 12, 'status': 'verified', 'not_started': False}, **fields)
    assert task['steps'][0]['receipt']['job_id'] == 'job-1'
    assert task['steps'][0]['receipt']['first'] == 'x'*9000


@pytest.mark.parametrize('receipt', [{}, {'not_started': True}, {'not_started': False}, {'status': 'completed'}])
def test_completion_requires_actual_nonempty_evidence_not_only_control_markers(tmp_path, receipt):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'complete_step', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': receipt})
    assert response.status_code == 422, response.text
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_not_started_human_preparation_can_only_finish_with_verified_started_result(tmp_path):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'not_started': True})
    task = event(client, task, 'resume', confirmed=True)
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'reconcile_step', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': {'step': 1}})
    assert response.status_code == 422
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', receipt={'step': 1, 'not_started': False})
    assert task['status'] == 'completed' and task['steps'][0]['receipt']['not_started'] is False


def test_human_flow_recomputation_keeps_old_and_new_identifiers_in_distinct_fields(tmp_path):
    _, client = desktop(tmp_path)
    task = create(client, plan(steps=[{'action': 'evaluate_flow', 'args': {}, 'label': 'flow'}]))
    task = event(client, task, 'approve', confirmed=True)
    approval = copy.deepcopy(task['approval'])
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='flow-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='flow-1', wait_kind='human', receipt={'evaluation_id': 'old-evaluation', 'cohort_id': 'old-cohort', 'unknown': 2})
    task = event(client, task, 'resume', confirmed=True)
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='flow-1', receipt={'recomputation': {'intent_id': 'intent-1', 'previous_evaluation_id': 'old-evaluation', 'phase': 'started'}})
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='flow-1', receipt={'not_started': False, 'unknown': 0, 'recomputation': {'new_cohort_id': 'new-cohort', 'new_evaluation_id': 'new-evaluation', 'phase': 'completed'}})
    receipt = task['steps'][0]['receipt']
    assert task['status'] == 'completed' and task['approval'] == approval
    assert receipt['evaluation_id'] == 'old-evaluation' and receipt['cohort_id'] == 'old-cohort'
    assert receipt['recomputation']['intent_id'] == 'intent-1'
    assert receipt['recomputation']['new_evaluation_id'] == 'new-evaluation'


CLAIM_ONE = '11111111-1111-4111-8111-111111111111'
CLAIM_TWO = '22222222-2222-4222-8222-222222222222'


def resumed_unstarted(client, receipt=None):
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='attempt-1')
    task = event(client, task, 'wait_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt=receipt or {'not_started': True})
    return event(client, task, 'resume', confirmed=True)


def claim(client, task, nonce=CLAIM_ONE):
    return event(client, task, 'record_evidence', step_id=task['steps'][0]['id'], attempt_id='attempt-1',
                 receipt={'not_started': False, 'unstarted_claim': {'nonce': nonce, 'phase': 'claimed'}})


def test_resumed_unstarted_proof_is_consumed_once_even_after_competitor_reads_latest(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    assert task['status'] == 'needs_reconciliation' and task['steps'][0]['status'] == 'uncertain'
    assert task['steps'][0]['receipt']['not_started'] is False
    assert task['steps'][0]['receipt']['unstarted_claim']['nonce'] == CLAIM_ONE
    for nonce in (CLAIM_TWO, CLAIM_ONE):
        latest = client.get('/api/assistant-tasks/'+task['id']).json()['task']
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': 'record_evidence', 'binding': BINDING, 'expected_revision': latest['revision'],
            'step_id': task['steps'][0]['id'], 'attempt_id': 'attempt-1',
            'receipt': {'not_started': False, 'unstarted_claim': {'nonce': nonce, 'phase': 'claimed'}}})
        assert response.status_code == 409, response.text
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_parallel_unstarted_claims_have_one_owner_and_loser_cannot_refresh_retry(tmp_path):
    _, client = desktop(tmp_path)
    task = resumed_unstarted(client)
    def compete(nonce):
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
            'step_id': task['steps'][0]['id'], 'attempt_id': 'attempt-1',
            'receipt': {'not_started': False, 'unstarted_claim': {'nonce': nonce, 'phase': 'claimed'}}})
        return nonce, response.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(compete, (CLAIM_ONE, CLAIM_TWO)))
    assert sorted(status for _, status in outcomes) == [200, 409]
    winner = client.get('/api/assistant-tasks/'+task['id']).json()['task']
    loser = next(nonce for nonce, status in outcomes if status == 409)
    assert winner['steps'][0]['receipt']['unstarted_claim']['nonce'] != loser
    assert compete(loser)[1] == 409
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': winner['revision'],
        'step_id': task['steps'][0]['id'], 'attempt_id': 'attempt-1',
        'receipt': {'not_started': False, 'unstarted_claim': {'nonce': loser, 'phase': 'claimed'}}})
    assert response.status_code == 409


def test_fresh_claim_after_proven_no_effect_human_resume_preserves_previous_claim(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    sid = task['steps'][0]['id']
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'not_started': True})
    assert task['steps'][0]['receipt']['unstarted_claim']['nonce'] == CLAIM_ONE
    task = event(client, task, 'resume', confirmed=True)
    task = claim(client, task, CLAIM_TWO)
    receipt = task['steps'][0]['receipt']
    assert receipt['unstarted_claim'] == {'nonce': CLAIM_TWO, 'phase': 'claimed'}
    assert receipt['unstarted_claim_history'] == [{'nonce': CLAIM_ONE, 'phase': 'no_effect'}]


@pytest.mark.parametrize('case', ['not_resumed', 'paused', 'unknown_proof', 'known_job', 'nested_execution'])
def test_unstarted_claim_requires_resumed_human_proof_and_no_execution_references(tmp_path, case):
    _, client = desktop(tmp_path)
    initial = {'not_started': True}
    if case == 'known_job': initial['job_id'] = 'job-already-started'
    if case == 'nested_execution': initial['recomputation'] = {'new_evaluation_id': 'already-created'}
    task = resumed_unstarted(client, initial)
    sid = task['steps'][0]['id']
    if case == 'paused': task = event(client, task, 'pause')
    elif case == 'not_resumed':
        task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'not_started': True})
    elif case == 'unknown_proof':
        task = event(client, task, 'record_evidence', step_id=sid, attempt_id='attempt-1', receipt={'not_started': True, 'message': 'only uncertain'})
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1',
        'receipt': {'not_started': False, 'unstarted_claim': {'nonce': CLAIM_ONE, 'phase': 'claimed'}}})
    assert response.status_code == 409, response.text
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


@pytest.mark.parametrize('receipt', [
    {'unstarted_claim': {'nonce': CLAIM_ONE, 'phase': 'claimed'}},
    {'not_started': False, 'unstarted_claim': {'nonce': 'bad', 'phase': 'claimed'}},
    {'not_started': False, 'unstarted_claim': {'nonce': CLAIM_ONE, 'phase': 'owned'}},
    {'not_started': False, 'unstarted_claim': {'nonce': CLAIM_ONE, 'phase': 'claimed', 'extra': True}},
])
def test_claim_receipt_schema_is_narrow(tmp_path, receipt):
    _, client = desktop(tmp_path)
    task = resumed_unstarted(client)
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': task['steps'][0]['id'], 'attempt_id': 'attempt-1', 'receipt': receipt})
    assert response.status_code == 422, response.text


def test_claim_and_claim_history_cannot_be_mutated_through_other_receipt_updates(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    sid = task['steps'][0]['id']
    for kind, receipt in [('record_evidence', {'unstarted_claim_history': []}),
                          ('reconcile_step', {'step': 1, 'unstarted_claim': {'nonce': CLAIM_TWO, 'phase': 'claimed'}})]:
        response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
            'type': kind, 'binding': BINDING, 'expected_revision': task['revision'],
            'step_id': sid, 'attempt_id': 'attempt-1', 'receipt': receipt})
        assert response.status_code == 409, response.text
        assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_claim_intent_alone_never_counts_as_completion_evidence(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'reconcile_step', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': task['steps'][0]['id'], 'attempt_id': 'attempt-1', 'receipt': {'status': 'completed'}})
    assert response.status_code == 422
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_claim_is_retained_in_fuller_real_execution_receipts(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    sid = task['steps'][0]['id']
    receipt = {**task['steps'][0]['receipt'], 'job_id': 'job-1', 'status': 'started'}
    task = event(client, task, 'record_evidence', step_id=sid, attempt_id='attempt-1', receipt=receipt)
    assert task['steps'][0]['receipt']['unstarted_claim']['nonce'] == CLAIM_ONE
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', receipt={**receipt, 'status': 'completed'})
    assert task['status'] == 'completed' and task['steps'][0]['receipt']['job_id'] == 'job-1'


def test_retired_claim_nonce_cannot_be_reused_after_another_proven_no_effect(tmp_path):
    _, client = desktop(tmp_path)
    task = claim(client, resumed_unstarted(client))
    sid = task['steps'][0]['id']
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'not_started': True})
    task = claim(client, event(client, task, 'resume', confirmed=True), CLAIM_TWO)
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='attempt-1', wait_kind='human', receipt={'not_started': True})
    task = event(client, task, 'resume', confirmed=True)
    response = client.post('/api/assistant-tasks/'+task['id']+'/events', json={
        'type': 'record_evidence', 'binding': BINDING, 'expected_revision': task['revision'],
        'step_id': sid, 'attempt_id': 'attempt-1',
        'receipt': {'not_started': False, 'unstarted_claim': {'nonce': CLAIM_ONE, 'phase': 'claimed'}}})
    assert response.status_code == 409
    assert client.get('/api/assistant-tasks/'+task['id']).json()['task'] == task


def test_task_training_batch_limit_matches_the_existing_training_screen(tmp_path):
    _, client = desktop(tmp_path)
    for size, status in [(128, 200), (129, 422)]:
        response = client.post('/api/assistant-tasks', json=plan(steps=[{
            'action': 'configure_training', 'args': {'batch_size': size}, 'label': '학습 설정'}]))
        assert response.status_code == status


@pytest.mark.parametrize('kind', ['record_evidence', 'reconcile_step'])
def test_uncertain_step_keeps_its_real_error_after_pause_and_clears_only_on_verified_completion(tmp_path, kind):
    _, client = desktop(tmp_path)
    task = event(client, create(client), 'approve', confirmed=True)
    sid = task['steps'][0]['id']
    task = event(client, task, 'begin_step', step_id=sid, attempt_id='owned-step')
    task = event(client, task, kind, step_id=sid, attempt_id='owned-step',
                 message='Actual saved-result mismatch', **({'receipt': {'not_started': False}} if kind == 'record_evidence' else {}))
    task = event(client, task, 'pause', message='Paused after context change')
    assert task['steps'][0]['error'] == 'Actual saved-result mismatch'
    task = event(client, task, 'resume', confirmed=True)
    task = event(client, task, 'reconcile_step', step_id=sid, attempt_id='owned-step', receipt={'step': 1, 'not_started': False})
    assert task['status'] == 'completed' and task['steps'][0]['error'] is None

"""Gateway boundaries must deny cross-project operations before child contact."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient


def gateway(tmp_path):
    from backend.api.routes_platform_geti import router
    from backend.platforms.geti.bindings import PlatformStore
    from backend.contracts.context import ProjectContext
    app = FastAPI()
    project = {'id': 'project-a', 'name': 'Owned fixture', 'task': 'classification', 'project_dir': str(tmp_path / 'p'), 'source_dataset_dir':str(tmp_path / 'source'),'active_labelset_id':'default'}
    app.state.fixture_project = project
    class Registry:
        local_actor_id = 'local-actor'
        def project_key(self, context): return 'scope-' + context.project_id
    class Accounts:
        role = 'viewer'
        def project_role(self, actor, project_id): return self.role
    class Runtime:
        starts = 0
        def status(self, key): return {'state': 'stopped'}
        def client(self, key):
            self.starts += 1
            return object()
    app.state.context_registry = Registry()
    app.state.accounts = Accounts()
    app.state.geti_runtime = Runtime()
    app.state.geti_store = PlatformStore(tmp_path / 'ledger')
    @app.middleware('http')
    async def context(request, next):
        request.state.scoped_project = project
        request.state.account_user = {'id': 'actor-a'}
        request.state.project_context = ProjectContext(workspace_id='workspace', project_id='project-a', actor_id='actor-a', mode='team')
        return await next(request)
    app.include_router(router)
    return app, TestClient(app)


def test_viewer_cannot_start_or_prepare_even_with_body_actor(tmp_path):
    app, client = gateway(tmp_path)
    for path, body in [('start', {}), ('prepare', {'operation_id': 'op-a', 'actor': 'owner'})]:
        assert client.post('/api/platform/geti/' + path, json=body).status_code in {403, 422}
    assert app.state.geti_runtime.starts == 0
    assert app.state.geti_store.get_dataset('scope-project-a') is None


def test_status_never_starts_child_and_redacts_internal_transfer_roots(tmp_path):
    app, client = gateway(tmp_path)
    app.state.geti_store.save_dataset('scope-project-a', {'id': 'receipt', 'project_id':'project-a','task':'classification','labelset_id':'default','source_root': str(tmp_path/'source'), 'transfer_root': '/private/copy', 'source_mapping': [{'relative_path': 'a.png', 'source_path': '/private/source/a.png'}]})
    response = client.get('/api/platform/geti/status')
    assert response.status_code == 200
    body = response.json()
    assert body['permissions']['can_train'] is False
    assert body['dataset']['source_mapping'][0]['relative_path'] == 'a.png'
    assert '/private/' not in response.text
    assert app.state.geti_runtime.starts == 0


def test_cross_scope_job_unknown_model_and_revoked_membership_do_not_contact_child(tmp_path):
    app, client = gateway(tmp_path)
    app.state.geti_store.save_job('scope-project-b', 'actor-b', {'id': 'foreign', 'status': 'running'})
    app.state.accounts.role = 'trainer'
    assert client.get('/api/platform/geti/jobs/foreign').status_code == 404
    assert client.post('/api/platform/geti/jobs/foreign/cancel', json={}).status_code == 404
    app.state.accounts.role = None
    assert client.post('/api/platform/geti/start', json={}).status_code == 403
    assert client.get('/api/platform/geti/status').status_code == 403
    assert app.state.geti_runtime.starts == 0


def test_registered_location_role_never_falls_back_to_same_logical_project(tmp_path):
    app, client = gateway(tmp_path)
    # Existing local-project middleware resolves a separate permission key for
    # each registered physical location, even when project.json IDs match.
    @app.middleware('http')
    async def location(request, next):
        request.state.account_project_key = 'registration-location-a'
        return await next(request)
    app.state.accounts.role = 'owner'
    app.state.accounts.project_role_exact = lambda actor, key: None
    assert client.get('/api/platform/geti/status').status_code == 403
    assert app.state.geti_runtime.starts == 0


def test_prediction_admits_new_inventory_image_but_never_unregistered_or_escaped_paths(tmp_path):
    import pytest
    from fastapi import HTTPException
    from PIL import Image
    from backend.platforms.geti.policy import admitted_image
    source = tmp_path / 'source'
    (source / 'red').mkdir(parents=True)
    image = source / 'red/new.png'
    Image.new('RGB', (8, 8), 'red').save(image)
    hidden = source / 'red/.hidden.png'
    Image.new('RGB', (8, 8), 'red').save(hidden)
    foreign = tmp_path / 'foreign.png'
    Image.new('RGB', (8, 8), 'blue').save(foreign)
    project = {'source_dataset_dir': str(source), 'task': 'classification'}
    assert admitted_image(project, {'source_mapping': []}, str(image)) == image.resolve()
    for path in (hidden, foreign):
        with pytest.raises(HTTPException) as info:
            admitted_image(project, {'source_mapping': []}, str(path))
        assert info.value.status_code == 403


def test_recovery_preserves_ambiguous_submission_and_only_interrupts_proven_generation(tmp_path):
    app, client = gateway(tmp_path)
    app.state.accounts.role = 'trainer'
    store = app.state.geti_store
    store.save_job('scope-project-a', 'actor-a', {'id': 'job-a', 'status': 'submission_unconfirmed', 'generation': 'old-a', 'worker_exited': False, 'platform_job_id': 'child-a', 'operation_id': 'op-a'})
    store.save_job('scope-project-a', 'actor-a', {'id': 'job-b', 'status': 'submission_unconfirmed', 'generation': 'other-b', 'worker_exited': False})
    proof = {'generation': 'old-a', 'worker_exited': True, 'finished_at': 123, 'reason': 'owned process group exited'}
    app.state.geti_runtime.recover = lambda key: proof
    app.state.geti_runtime.exit_evidence = lambda key, generation: proof if generation == 'old-a' else None
    response = client.post('/api/platform/geti/recover', json={})
    assert response.status_code == 200, response.text
    recovered = store.get_job('scope-project-a', 'job-a')
    assert recovered['status'] == 'interrupted' and recovered['worker_exited'] is True
    assert recovered['platform_job_id'] == 'child-a' and recovered['operation_id'] == 'op-a'
    assert recovered['submission_evidence']['status'] == 'submission_unconfirmed'
    assert store.get_job('scope-project-a', 'job-b')['status'] == 'submission_unconfirmed'
    assert app.state.geti_runtime.starts == 0


def test_long_adapter_request_rechecks_revoked_session_before_child_mutation(tmp_path):
    import pytest
    from fastapi import HTTPException
    from types import SimpleNamespace
    from backend.api.routes_platform_geti import client_for
    from backend.contracts.context import ProjectContext
    app, client = gateway(tmp_path)
    app.state.accounts.role = 'trainer'
    state = SimpleNamespace(scoped_project={'id':'project-a','task':'classification'},
        account_user={'id':'actor-a'},account_session_token='expired',
        project_context=ProjectContext(workspace_id='workspace',project_id='project-a',actor_id='actor-a',mode='team'))
    request = SimpleNamespace(app=app,state=state)
    def expired(token): raise ValueError('Session expired')
    app.state.accounts.authenticate = expired
    class Transport:
        touched = False
        def request(self, *args, **kwargs): self.touched = True; return {}
    transport = Transport()
    app.state.geti_runtime.client = lambda key: transport
    bound = client_for(app.state.geti_runtime,'scope-project-a',request=request,action='train')
    with pytest.raises(HTTPException) as info: bound.request('POST','/modu/jobs',json={})
    assert info.value.status_code == 401
    assert transport.touched is False


def test_new_transfer_never_advertises_previous_target_cached_models(tmp_path, monkeypatch):
    from backend.platforms.geti import dataset_transfer
    app, client = gateway(tmp_path)
    app.state.accounts.role = 'trainer'
    store = app.state.geti_store
    store.save_dataset('scope-project-a', {'id':'old-transfer','geti_project_id':'old-project'})
    store.save_project('scope-project-a', {'models':[{'id':'old-model','transfer_id':'old-transfer'}], 'architectures':[{'id':'old-architecture'}]})
    monkeypatch.setattr(dataset_transfer, 'prepare_dataset', lambda *args, **kwargs: {'id':'new-transfer','geti_project_id':'new-project','project_id':'project-a','task':'classification','labelset_id':'default','source_root':str(tmp_path/'source')})
    response = client.post('/api/platform/geti/prepare', json={'operation_id':'new-prepare'})
    assert response.status_code == 200, response.text
    status = client.get('/api/platform/geti/status').json()
    assert status['dataset']['id'] == 'new-transfer'
    assert status['models'] == [] and status['architectures'] == []


def test_public_native_choices_match_the_existing_model_catalog(tmp_path):
    from backend.engine.model_catalog import model_family_catalog
    app, client = gateway(tmp_path)
    choices = client.get('/api/platform/geti/status').json()['native_tasks']
    assert len(choices) == 10
    assert set(choices) == {row['task'] for row in model_family_catalog()['families']}
    assert app.state.geti_runtime.starts == 0


def test_cancelled_job_recovery_preserves_verdict_and_confirms_generation_exit(tmp_path):
    app, client = gateway(tmp_path)
    app.state.accounts.role = 'trainer'
    store = app.state.geti_store
    store.save_job('scope-project-a','actor-a',{'id':'cancelled-job','status':'cancelled','cancel_requested':True,'worker_exited':False,'generation':'old-generation'})
    proof = {'generation':'old-generation','worker_exited':True,'finished_at':123,'reason':'owned process group exited'}
    app.state.geti_runtime.recover = lambda key: proof
    app.state.geti_runtime.exit_evidence = lambda key, generation: proof
    response = client.post('/api/platform/geti/recover',json={})
    assert response.status_code == 200, response.text
    row = store.get_job('scope-project-a','cancelled-job')
    assert row['status'] == 'cancelled' and row['cancel_requested'] is True
    assert row['worker_exited'] is True and row['exit_evidence']['generation']=='old-generation'


def test_changed_parent_context_hides_stale_readiness_and_preserves_stored_history(tmp_path):
    app, client = gateway(tmp_path)
    project = app.state.fixture_project
    receipt = {'id':'sealed-transfer','project_id':'project-a','task':'classification','labelset_id':'default','source_root':project['source_dataset_dir']}
    app.state.geti_store.save_dataset('scope-project-a',receipt)
    app.state.geti_store.save_project('scope-project-a',{'models':[{'id':'model','transfer_id':'sealed-transfer'}],'architectures':[{'id':'classification-architecture'}]})
    for field, value in [('task','anomaly'),('source_dataset_dir',str(tmp_path/'other-source')),('active_labelset_id','other-labelset')]:
        previous = project[field]
        project[field] = value
        status = client.get('/api/platform/geti/status').json()
        assert status['dataset'] is None and status['models']==[] and status['architectures']==[]
        assert status['project']['binding_state']=='stale' and status['project']['binding_message']
        if field=='task': assert status['project']['supported'] is False
        assert app.state.geti_store.get_dataset('scope-project-a') == receipt
        project[field] = previous
    restored = client.get('/api/platform/geti/status').json()
    assert restored['dataset']['id']=='sealed-transfer' and restored['models'][0]['id']=='model'
    assert restored['project']['supported'] is True

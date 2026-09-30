"""Independent regressions for shared file selectors and comparison recovery."""
import json
import os
from pathlib import Path
import psutil
from fastapi.testclient import TestClient


def test_shared_pretrained_checkpoint_selector_is_scoped_before_execution(tmp_path,monkeypatch):
    from backend.main import create_app
    from backend.engine import training_engine as engine
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert bootstrap.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'}).status_code==200
    admin=TestClient(app);admin.headers['Authorization']='Bearer '+admin.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()['token']
    project=admin.post('/api/project/create',json={'name':'Checkpoint scope'}).json();root=Path(project['project_dir'])
    source=root/'source';source.mkdir();assert admin.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    user=admin.post('/api/accounts/users',json={'username':'trainer','password':'long password 456','administrator':False}).json()
    assert admin.put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':user['id'],'role':'trainer'}).status_code==200
    trainer=TestClient(app);trainer.headers['Authorization']='Bearer '+trainer.post('/api/accounts/login',json={'username':'trainer','password':'long password 456'}).json()['token']
    assert trainer.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
    monkeypatch.setattr(engine,'_prepared',lambda output:{'source_dataset_path':str(source)})
    observed=[]
    def submit(**options):observed.append(options['config']['pretrained_checkpoint']);return {'status':'queued'}
    monkeypatch.setattr(engine,'start_run',submit)
    foreign=tmp_path/'foreign.pt';foreign.write_bytes(b'foreign-private-model')
    base={'output_dir':str(root/'engine'),'config':{'pretrained_checkpoint':str(foreign)}}
    assert trainer.post('/api/engine/train',json=base).status_code==403 and observed==[]
    owned=root/'approved.pt';owned.write_bytes(b'owned-model')
    assert trainer.post('/api/engine/train',json={**base,'config':{'pretrained_checkpoint':str(owned)}}).status_code==200
    assert observed==[str(owned)]
    recipe=root/'recipe.json';recipe.write_text(json.dumps({'config':{'pretrained_checkpoint':str(foreign)}}))
    response=trainer.post('/api/engine/train',json={'output_dir':str(root/'engine'),'config_path':str(recipe)})
    assert response.status_code==403 and observed==[str(owned)]


def test_comparison_recovery_rejects_unrelated_pid_even_with_matching_creation_time(tmp_path):
    from backend.engine.evaluation_history import ComparisonJobs
    path=tmp_path/'jobs.sqlite3';jobs=ComparisonJobs(path);row=jobs.create({'task':'classification'})
    assert ComparisonJobs(path).get(row['job_id'])['status']=='queued'
    unrelated=psutil.Process(os.getppid())
    with jobs.connect() as connection:
        connection.execute("UPDATE jobs SET status='running',owner_pid=?,owner_created_at=? WHERE job_id=?",(unrelated.pid,unrelated.create_time(),row['job_id']))
    assert ComparisonJobs(path).get(row['job_id'])['status']=='interrupted'
    assert psutil.pid_exists(unrelated.pid)
    row=jobs.create({'task':'classification'})
    with jobs.connect() as connection:connection.execute('UPDATE jobs SET owner_created_at=NULL,owner_command_sha256=NULL WHERE job_id=?',(row['job_id'],))
    assert ComparisonJobs(path).get(row['job_id'])['status']=='interrupted'


def test_shared_precision_approval_requires_reviewer_or_owner(tmp_path,monkeypatch):
    from backend.main import create_app
    from backend.api import routes_export
    from backend.engine import runtime_precision_approval
    app=create_app(project_dir=str(tmp_path/'projects'),shared_auth_dir=str(tmp_path/'accounts'))
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert bootstrap.post('/api/accounts/bootstrap',json={'username':'admin','password':'long password 123'}).status_code==200
    admin=TestClient(app);admin.headers['Authorization']='Bearer '+admin.post('/api/accounts/login',json={'username':'admin','password':'long password 123'}).json()['token']
    project=admin.post('/api/project/create',json={'name':'Runtime precision review'}).json()
    clients={}
    for role in ('labeler','trainer','reviewer','owner'):
        member=admin.post('/api/accounts/users',json={'username':role,'password':'long password 456','administrator':False}).json()
        assert admin.put(f"/api/accounts/projects/{project['id']}/members",json={'user_id':member['id'],'role':role}).status_code==200
        client=TestClient(app);client.headers['Authorization']='Bearer '+client.post('/api/accounts/login',json={'username':role,'password':'long password 456'}).json()['token']
        assert client.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
        clients[role]=client
    monkeypatch.setattr(routes_export,'_optimization_approval_context',lambda project,job_id:(tmp_path/'candidate',{}, {'model':{'revision_id':'active-model-revision'}}))
    calls=[]
    def approve(candidate,output,**options):
        calls.append(options['reviewer'])
        return {'package_path':str(output),'release_policy':{'manifest_sha256':('1' if len(calls)==1 else '2')*64}}
    monkeypatch.setattr(runtime_precision_approval,'approve_precision_package',approve)
    payload={'reviewer':'forged','reason':'Measured heldout parity accepted','maximum_absolute_drift':.01,'holdout_reviewed':True,'approval_revision_ids':{'model':'active-model-revision'}}
    path='/api/export/flow/optimization-jobs/runtime-review-job/approve'
    for role in ('labeler','trainer'):
        assert clients[role].post(path,json=payload).status_code==403
    assert calls==[]
    for role in ('reviewer','owner'):
        assert clients[role].post(path,json=payload).status_code==200
    assert calls==['reviewer','owner'],'approval actor must come from the authenticated account'

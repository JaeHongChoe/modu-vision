"""Production async comparison lifecycle, using temporary scoped projects."""
import threading
from pathlib import Path
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.api import routes_model_comparisons as api


def setup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app=create_app(project_dir=str(tmp_path/'registry'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    source=tmp_path/'source';source.mkdir()
    project=client.post('/api/project/create',json={'name':'Comparison','task':'classification'}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    checkpoint=tmp_path/'model.pt';checkpoint.write_bytes(b'checkpoint')
    monkeypatch.setattr(api,'_model',lambda p,s,t,j:{'job_id':j,'task':t,'checkpoint_path':str(checkpoint),'training_dataset_fingerprint':'bound'})
    monkeypatch.setattr(api,'_fingerprint',lambda s:'bound')
    monkeypatch.setattr(api,'_test_images',lambda *a:([],0))
    from backend.engine import comparison_truth
    monkeypatch.setattr(comparison_truth,'bind_truth',lambda *a:None)
    pending=[];original=threading.Thread.start
    def defer(thread):
        if thread.name.startswith('comparejob_'):pending.append(lambda:thread._target(*thread._args,**thread._kwargs))
        else:original(thread)
    monkeypatch.setattr(threading.Thread,'start',defer)
    payload={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_a','candidate_job_id':'job_b'}
    return client,project,source,payload,pending


def test_http_same_key_reserves_one_dispatch(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'same'})
    second=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'same'})
    assert first.status_code==second.status_code==202,(first.text,second.text)
    assert first.json()['job_id']==second.json()['job_id']
    assert len(pending)==1


def test_cancel_after_evaluator_last_check_cannot_complete(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    job=client.post('/api/evaluation/model-comparisons/jobs',json=payload).json()
    def evaluator(payload,project,source,progress=None,cancelled=None,**kwargs):
        progress(0,0)
        assert not cancelled()
        response=client.post('/api/evaluation/model-comparisons/jobs/'+job['job_id']+'/cancel',params={'source_dataset_path':str(source),'task':'classification'})
        assert response.status_code==200,response.text
        report={'status':'completed','comparison_id':'comparison_'+'a'*32}
        if kwargs.get('publish_report'):kwargs['publish_report'](report)
        return report
    monkeypatch.setattr(api,'_run_comparison',evaluator)
    pending[0]()
    response=client.get('/api/evaluation/model-comparisons/jobs/'+job['job_id'],params={'source_dataset_path':str(source),'task':'classification'})
    assert response.json()['status']=='cancelled',response.text
    assert not response.json().get('report_id')


def test_replay_precedes_live_binding_and_changed_request_conflicts(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'held'}).json()
    monkeypatch.setattr(api,'_model',lambda *a:None)
    replay=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'held'})
    assert replay.status_code==202,replay.text
    assert replay.json()['job_id']==first['job_id']
    assert replay.json()['binding']==first['binding']
    changed=client.post('/api/evaluation/model-comparisons/jobs',json={**payload,'max_images':3},headers={'Idempotency-Key':'held'})
    assert changed.status_code==409
    assert len(pending)==1


def coordinator(tmp_path,actor='actor'):
    from backend.engine.comparison_jobs import ComparisonJobs
    from backend.engine.job_store import JobStore
    from backend.contracts.context import ProjectContext
    context=ProjectContext(workspace_id='workspace',project_id='project',actor_id=actor,mode='local')
    jobs=ComparisonJobs(JobStore(tmp_path/'ledger.sqlite3'))
    project={'project_dir':str(tmp_path/'project'),'id':'project'}
    identifier,created=jobs.submit(context,'key',project,{'source_dataset_path':str(tmp_path/'source'),'task':'classification'}, {'bytes':'original'},'request')
    assert created
    return jobs,identifier,context,tmp_path/'project'/'reports'/'model_comparisons'


def report(status='completed'):
    return {'comparison_id':'comparison_'+'b'*32,'status':status,'images':[]}


def test_actual_owned_publication_and_tamper_readback(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    def evaluate(progress,cancelled,publish,binding):
        progress(1,1);publish(report('completed_with_errors'))
    jobs.run(identifier,evaluate,output,lambda expected:None)
    row=jobs.view(identifier)
    assert row['state']=='completed' and row['status']=='completed_with_errors'
    assert row['result_available'] and row['completed_images']==row['total_images']==1
    assert len(jobs.store.artifacts(identifier))==1
    assert len(jobs.store.attempts(identifier))==1
    path=output/(row['report_id']+'.json')
    path.write_text('changed',encoding='utf-8')
    assert jobs.view(identifier)['result_available'] is False
    assert jobs.view(identifier)['report_id'] is None


def test_cancellation_inside_final_binding_verification_prevents_publication(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    checks=[]
    def verify(expected):
        checks.append(expected)
        if len(checks)==2:jobs.cancel(identifier)
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,verify)
    assert jobs.view(identifier)['status']=='cancelled'
    assert not list(output.glob('comparison_*.json'))
    assert not list(output.glob('*.tmp'))
    assert not jobs.store.artifacts(identifier)


def test_foreign_final_is_preserved_and_failed(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    output.mkdir(parents=True)
    path=output/(report()['comparison_id']+'.json');path.write_bytes(b'foreign')
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert jobs.view(identifier)['status']=='failed'
    assert path.read_bytes()==b'foreign'
    assert not jobs.store.artifacts(identifier)


def test_stale_fence_cannot_publish_or_finish_new_attempt(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    def evaluate(progress,cancelled,publish,binding):
        jobs.store.begin_attempt(identifier,jobs.store.get(identifier).revision,'new-owner','other',123)
        publish(report())
    jobs.run(identifier,evaluate,output,lambda expected:None)
    assert jobs.store.get(identifier).state=='running'
    assert not list(output.glob('comparison_*.json'))
    assert len(jobs.store.attempts(identifier))==2


def test_proven_dead_restart_is_interrupted_never_queued(tmp_path,monkeypatch):
    from backend.engine import comparison_jobs
    jobs,identifier,context,output=coordinator(tmp_path)
    comparison_jobs.release_dispatch(jobs,identifier)
    monkeypatch.setattr(comparison_jobs,'_alive',lambda owner:False)
    assert jobs.view(identifier)['status']=='interrupted'
    assert jobs.view(identifier)['resumable'] is False
    assert not jobs.store.attempts(identifier)


def test_other_live_owner_is_not_adopted_or_interrupted(tmp_path,monkeypatch):
    from backend.engine import comparison_jobs
    jobs,identifier,context,output=coordinator(tmp_path)
    comparison_jobs.release_dispatch(jobs,identifier)
    monkeypatch.setattr(comparison_jobs,'_BOOT','another-process')
    monkeypatch.setattr(comparison_jobs,'_alive',lambda owner:True)
    assert jobs.view(identifier)['status']=='queued'
    assert not jobs.store.attempts(identifier)


def test_cancel_revision_race_before_start_resolves_aborted(tmp_path,monkeypatch):
    jobs,identifier,context,output=coordinator(tmp_path)
    original=jobs.store.transition
    def raced(*args,**kwargs):
        jobs.cancel(identifier)
        return original(*args,**kwargs)
    monkeypatch.setattr(jobs.store,'transition',raced)
    jobs.run(identifier,lambda *a: (_ for _ in ()).throw(AssertionError('must not infer')),output,lambda expected:None)
    assert jobs.view(identifier)['status']=='cancelled'
    assert not jobs.store.attempts(identifier)


def test_actor_and_project_scope_are_not_legacy_fallback(tmp_path):
    import pytest
    from backend.contracts.context import ProjectContext
    jobs,identifier,context,output=coordinator(tmp_path)
    other=ProjectContext(workspace_id='workspace',project_id='project',actor_id='other',mode='local')
    with pytest.raises(KeyError):jobs.scoped(identifier,other,'key')
    with pytest.raises(KeyError):jobs.scoped(identifier,context,'other-project')


def test_http_source_drift_before_worker_fails_without_inference(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload).json()
    monkeypatch.setattr(api,'_fingerprint',lambda s:'changed')
    monkeypatch.setattr(api,'_run_comparison',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('must not infer')))
    pending[0]()
    row=client.get('/api/evaluation/model-comparisons/jobs/'+first['job_id'],params={'source_dataset_path':str(source),'task':'classification'}).json()
    assert row['status']=='failed' and not row['report_id']
    assert 'inputs changed' in row['error'].lower()


def test_cancel_between_verified_checkpoint_and_atomic_finish_is_aborted(tmp_path,monkeypatch):
    jobs,identifier,context,output=coordinator(tmp_path)
    original=jobs.store.finish
    def finish(identifier,event,*args,**kwargs):
        if event=='complete':jobs.cancel(identifier)
        return original(identifier,event,*args,**kwargs)
    monkeypatch.setattr(jobs.store,'finish',finish)
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert jobs.view(identifier)['status']=='cancelled'
    assert not list(output.glob('comparison_*.json'))
    assert not jobs.store.artifacts(identifier)


def test_ordinary_evaluator_error_fails_and_late_cancel_preserves_completion(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    def broken(*args):raise RuntimeError('controlled evaluation failure')
    jobs.run(identifier,broken,output,lambda expected:None)
    assert jobs.view(identifier)['status']=='failed'
    assert 'controlled evaluation failure' in jobs.view(identifier)['error']
    jobs,identifier,context,output=coordinator(tmp_path/'other')
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    jobs.cancel(identifier)
    assert jobs.view(identifier)['status']=='completed'
    assert jobs.view(identifier)['result_available']
    assert jobs.view(identifier)['cancel_requested']==0


def test_crash_after_exclusive_link_is_not_a_public_completed_receipt(tmp_path,monkeypatch):
    import os
    import pytest
    from backend.engine import comparison_jobs
    jobs,identifier,context,output=coordinator(tmp_path)
    original=os.link
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('controlled crash before ledger commit')
    monkeypatch.setattr(comparison_jobs.os,'link',crash)
    with pytest.raises(SystemExit):
        jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert jobs.store.get(identifier).state=='running'
    assert not jobs.store.artifacts(identifier)
    monkeypatch.setattr(comparison_jobs,'_alive',lambda owner:False)
    assert jobs.view(identifier)['status']=='interrupted'
    assert not jobs.view(identifier)['result_available']
    assert not list(output.glob('comparison_*.json'))


def test_legacy_read_adapter_does_not_normalize_or_recover_receipts(tmp_path):
    import hashlib
    from backend.engine.evaluation_history import ComparisonJobs
    path=tmp_path/'legacy.sqlite3'
    legacy=ComparisonJobs(path);row=legacy.create({'source_dataset_path':'old','task':'classification'})
    before=path.read_bytes()
    opened=ComparisonJobs(path,read_only=True)
    assert opened.get(row['job_id'])['status']=='queued'
    assert path.read_bytes()==before
    legacy.finish(row['job_id'],'completed','comparison_old')
    assert ComparisonJobs(path).get(row['job_id'])['status']=='completed'


def test_http_project_labelset_change_refuses_captured_old_binding(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload).json()
    import json
    manifest=Path(project['project_dir'])/'project.json'
    content=json.loads(manifest.read_text(encoding='utf-8'));content['active_labelset_id']='changed'
    manifest.write_text(json.dumps(content),encoding='utf-8')
    pending[0]()
    row=client.get('/api/evaluation/model-comparisons/jobs/'+first['job_id'],params={'source_dataset_path':str(source),'task':'classification'}).json()
    assert row['status']=='failed' and not row['result_available']


def test_actual_concurrent_http_reservation_dispatches_once(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses=list(pool.map(lambda _:client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'concurrent'}),range(8)))
    assert all(row.status_code==202 for row in responses),[row.text for row in responses]
    assert len({row.json()['job_id'] for row in responses})==1
    assert len(pending)==1
    assert responses[-1].json()['status']=='queued'


def test_http_cancel_before_dispatch_closes_same_id_and_never_infers(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload).json()
    cancel=client.post('/api/evaluation/model-comparisons/jobs/'+first['job_id']+'/cancel',params={'source_dataset_path':str(source),'task':'classification'})
    assert cancel.json()['status']=='cancelled',cancel.text
    monkeypatch.setattr(api,'_run_comparison',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('must not infer')))
    pending[0]()
    row=client.get('/api/evaluation/model-comparisons/jobs/'+first['job_id'],params={'source_dataset_path':str(source),'task':'classification'}).json()
    assert row['status']=='cancelled' and not row['attempts']


def test_http_unauthorized_request_never_reserves_job(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    response=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'X-Vision-Token':'invalid'})
    assert response.status_code in (401,403)
    assert not pending


def test_true_post_link_crash_leaves_provisional_file_unavailable_on_recovery(tmp_path,monkeypatch):
    import pytest
    from backend.engine import comparison_jobs
    jobs,identifier,context,output=coordinator(tmp_path)
    original=comparison_jobs.os.link
    def crash(*args,**kwargs):
        original(*args,**kwargs)
        raise SystemExit('process lost after link')
    monkeypatch.setattr(comparison_jobs.os,'link',crash)
    # Simulate process death: real crash bypasses finally cleanup, unlike raised SystemExit.
    monkeypatch.setattr(jobs,'_owned',lambda *a:True)
    original_unlink=Path.unlink
    def keep_owned(path,*args,**kwargs):
        if path.parent==output:return
        return original_unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',keep_owned)
    with pytest.raises(SystemExit):
        jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert list(output.glob('comparison_*.json'))
    monkeypatch.setattr(comparison_jobs,'_alive',lambda owner:False)
    assert jobs.view(identifier)['status']=='interrupted'
    assert not jobs.view(identifier)['result_available']
    assert not jobs.store.artifacts(identifier)


def test_completed_symlink_result_is_readable_unavailable(tmp_path):
    jobs,identifier,context,output=coordinator(tmp_path)
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    path=output/(report()['comparison_id']+'.json')
    external=tmp_path/'external';external.write_bytes(path.read_bytes());path.unlink();path.symlink_to(external)
    assert jobs.view(identifier)['status']=='completed'
    assert not jobs.view(identifier)['result_available']


def test_foreign_stage_swap_is_preserved_not_published(tmp_path,monkeypatch):
    jobs,identifier,context,output=coordinator(tmp_path)
    original=jobs.store.finish
    foreign=[]
    def swap(identifier,event,*args,**kwargs):
        if event=='complete':
            stage=next(output.glob('*.tmp'))
            stage.unlink();stage.write_bytes(b'foreign stage');foreign.append(stage)
        return original(identifier,event,*args,**kwargs)
    monkeypatch.setattr(jobs.store,'finish',swap)
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert jobs.view(identifier)['status']=='failed'
    assert foreign[0].read_bytes()==b'foreign stage'
    assert not list(output.glob('comparison_*.json'))


def test_provisional_report_is_hidden_by_actual_http_report_routes(tmp_path,monkeypatch):
    import json
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload).json()
    output=api._report_dir(project);output.mkdir(parents=True)
    receipt={**report(),'project_id':project['id'],'source_dataset_path':str(source),'task':'classification',
             '_durable_comparison':{'job_id':first['job_id'],'fencing_token':1,'spec_sha256':'uncommitted'}}
    path=output/(receipt['comparison_id']+'.json');path.write_text(json.dumps(receipt),encoding='utf-8')
    params={'source_dataset_path':str(source),'task':'classification'}
    assert client.get('/api/evaluation/model-comparisons/'+receipt['comparison_id'],params=params).status_code==409
    assert client.get('/api/evaluation/model-comparisons',params=params).json()['total']==0


def test_existing_legacy_http_list_get_cancel_keeps_legacy_status(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    legacy=api._jobs(project);created=legacy.create(payload)
    params={'source_dataset_path':str(source),'task':'classification'}
    row=client.get('/api/evaluation/model-comparisons/jobs/'+created['job_id'],params=params).json()
    assert row['legacy'] and not row['durable'] and not row['ownership_verified']
    listed=client.get('/api/evaluation/model-comparisons/jobs',params=params).json()
    assert listed['total']==1 and listed['jobs'][0]['job_id']==created['job_id']
    cancelled=client.post('/api/evaluation/model-comparisons/jobs/'+created['job_id']+'/cancel',params=params).json()
    assert cancelled['status']=='cancelled'


def test_shared_actor_history_and_viewer_readback_cannot_recover_owned_job(tmp_path,monkeypatch):
    from backend.engine.comparison_jobs import release_dispatch
    from backend.engine.job_store import ledger
    from backend.engine.comparison_jobs import ComparisonJobs
    monkeypatch.chdir(tmp_path)
    app=create_app(project_dir=str(tmp_path/'registry'),shared_auth_dir=str(tmp_path/'accounts'))
    bootstrap=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    assert bootstrap.post('/api/accounts/bootstrap',json={'username':'admin','password':'long fixture password123'}).status_code==200
    admin=TestClient(app);admin.headers['Authorization']='Bearer '+admin.post('/api/accounts/login',json={'username':'admin','password':'long fixture password123'}).json()['token']
    project=admin.post('/api/project/create',json={'name':'Scoped comparisons','task':'classification'}).json()
    source=Path(project['project_dir'])/'source';source.mkdir()
    assert admin.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    member=admin.post('/api/accounts/users',json={'username':'trainer','password':'long fixture password456','administrator':False}).json()
    assert admin.put('/api/accounts/projects/'+project['id']+'/members',json={'user_id':member['id'],'role':'trainer'}).status_code==200
    trainer=TestClient(app);trainer.headers['Authorization']='Bearer '+trainer.post('/api/accounts/login',json={'username':'trainer','password':'long fixture password456'}).json()['token']
    assert trainer.post('/api/accounts/select-project',json={'project_id':project['id']}).status_code==200
    checkpoint=Path(project['project_dir'])/'checkpoint.pt';checkpoint.write_bytes(b'fixture')
    monkeypatch.setattr(api,'_model',lambda p,s,t,j:{'job_id':j,'task':t,'checkpoint_path':str(checkpoint),'training_dataset_fingerprint':'bound'})
    monkeypatch.setattr(api,'_fingerprint',lambda s:'bound');monkeypatch.setattr(api,'_test_images',lambda *a:([],0))
    from backend.engine import comparison_truth
    monkeypatch.setattr(comparison_truth,'bind_truth',lambda *a:None)
    original=threading.Thread.start
    monkeypatch.setattr(threading.Thread,'start',lambda thread:None if thread.name.startswith('comparejob_') else original(thread))
    payload={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_a','candidate_job_id':'job_b'}
    response=trainer.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'scope'})
    assert response.status_code==202,response.text
    identifier=response.json()['job_id'];params={'source_dataset_path':str(source),'task':'classification'}
    assert admin.get('/api/evaluation/model-comparisons/jobs/'+identifier,params=params).status_code==404
    assert admin.get('/api/evaluation/model-comparisons/jobs',params=params).json()['total']==0
    release_dispatch(ComparisonJobs(ledger()),identifier)
    assert admin.put('/api/accounts/projects/'+project['id']+'/members',json={'user_id':member['id'],'role':'viewer'}).status_code==200
    row=trainer.get('/api/evaluation/model-comparisons/jobs/'+identifier,params=params)
    assert row.status_code==200,row.text
    assert row.json()['status']=='queued',row.text
    assert ledger().get(identifier).state=='accepted'
    assert trainer.post('/api/evaluation/model-comparisons/jobs/'+identifier+'/cancel',params=params).status_code==403


def test_acceptance_rejects_project_scope_change_during_input_capture(tmp_path,monkeypatch):
    import json
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    original=api._comparison_inputs
    def moved(*args,**kwargs):
        inputs=original(*args,**kwargs)
        manifest=Path(project['project_dir'])/'project.json'
        data=json.loads(manifest.read_text(encoding='utf-8'));data['active_labelset_id']='new-labelset'
        manifest.write_text(json.dumps(data),encoding='utf-8')
        return inputs
    monkeypatch.setattr(api,'_comparison_inputs',moved)
    response=client.post('/api/evaluation/model-comparisons/jobs',json=payload)
    assert response.status_code==409,response.text
    assert not pending


def test_directory_publication_flush_failure_is_not_completed(tmp_path,monkeypatch):
    import os
    import stat
    import pytest
    from backend.engine import comparison_jobs
    if os.name=='nt':pytest.skip('Windows directory flush needs native acceptance')
    jobs,identifier,context,output=coordinator(tmp_path)
    original=os.fsync
    def fail_directory(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):raise OSError('controlled directory flush failure')
        return original(descriptor)
    monkeypatch.setattr(comparison_jobs.os,'fsync',fail_directory)
    jobs.run(identifier,lambda p,c,publish,b:publish(report()),output,lambda expected:None)
    assert jobs.view(identifier)['status']=='failed'
    assert not jobs.view(identifier)['result_available']
    assert not jobs.store.artifacts(identifier)


def test_same_process_copied_or_unconfirmed_owner_is_not_declared_dead(tmp_path,monkeypatch):
    from backend.engine import comparison_jobs
    jobs,identifier,context,output=coordinator(tmp_path)
    jobs.store.transition(identifier,jobs.store.get(identifier).revision,'start')
    jobs.store.begin_attempt(identifier,jobs.store.get(identifier).revision,'local',comparison_jobs._BOOT,comparison_jobs.os.getpid())
    comparison_jobs.release_dispatch(jobs,identifier)
    monkeypatch.setattr(comparison_jobs,'_alive',lambda owner:True)
    assert jobs.view(identifier)['status']=='running'


def test_legacy_home_source_selector_normalizes_before_reservation(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    original=Path.expanduser
    monkeypatch.setattr(Path,'expanduser',lambda path:source if str(path)=='~' else original(path))
    response=client.post('/api/evaluation/model-comparisons/jobs',json={**payload,'source_dataset_path':'~'})
    assert response.status_code==202,response.text
    assert response.json()['payload']['source_dataset_path']==str(source)


def test_dispatch_failure_returns_same_durable_interrupted_receipt(tmp_path,monkeypatch):
    client,project,source,payload,pending=setup(tmp_path,monkeypatch)
    original=threading.Thread.start
    def refuse(thread):
        if thread.name.startswith('comparejob_'):raise RuntimeError('controlled thread admission failure')
        return original(thread)
    monkeypatch.setattr(threading.Thread,'start',refuse)
    first=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'dispatch'})
    assert first.status_code==202,first.text
    assert first.json()['status']=='interrupted'
    replay=client.post('/api/evaluation/model-comparisons/jobs',json=payload,headers={'Idempotency-Key':'dispatch'})
    assert replay.json()['job_id']==first.json()['job_id']
    assert replay.json()['status']=='interrupted' and not replay.json()['attempts']

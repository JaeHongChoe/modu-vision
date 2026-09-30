"""Independent regressions for owned watcher startup and activation cancellation."""
import os
import threading
from types import SimpleNamespace
import pytest
import psutil
from PIL import Image
from backend.tests.test_model_operations import setup_project
from backend.engine import operations_worker,model_operations


def test_concurrent_watcher_start_claims_one_process_and_preserves_owner(tmp_path,monkeypatch):
    project={'project_dir':str(tmp_path)};entered=threading.Event();release=threading.Event();spawned=[];errors=[];results=[]
    def process(pid):
        return SimpleNamespace(pid=pid,create_time=lambda:42.,cmdline=lambda:['python','-m','backend.engine.operations_worker','--project-dir',str(tmp_path.resolve())])
    def launch(*args,**kwargs):
        spawned.append(10000);entered.set();assert release.wait(3);return process(10000)
    monkeypatch.setattr(operations_worker.subprocess,'Popen',launch)
    monkeypatch.setattr(operations_worker.psutil,'Process',process)
    def start():
        try:results.append(operations_worker.start_watcher(project))
        except Exception as exc:errors.append(exc)
    first=threading.Thread(target=start);first.start();assert entered.wait(3)
    second=threading.Thread(target=start);second.start();second.join(2)
    release.set();first.join(3);second.join(3)
    assert spawned==[10000] and len(results)==1 and results[0]['running'] is True
    assert len(errors)==1 and isinstance(errors[0],ValueError)
    assert operations_worker.start_watcher(project)['pid']==10000 and spawned==[10000]


def test_running_journal_with_unrelated_live_pid_and_matching_time_is_recovered(tmp_path):
    client,project,_=setup_project(tmp_path)
    model_operations.configure_program(project,{'parent_job_id':'job_parent','reviewer':'qa'})
    event=threading.Event();event.set();cycle=model_operations.run_cycle(project,event)
    cycle.update(cycle_id='cycle_stale_unrelated',status='running',owner_pid=os.getppid(),owner_created_at=psutil.Process(os.getppid()).create_time())
    store=model_operations.OperationsStore(project['project_dir']);store.save(cycle)
    read=client.get('/api/model-operations').json()
    assert next(row for row in read['cycles'] if row['cycle_id']==cycle['cycle_id'])['status']=='interrupted'


@pytest.mark.parametrize('cancel_at',['approval','deployment_prepare','deployment_ack'])
def test_activation_cancellation_keeps_durable_truth_and_blocks_the_next_side_effect(tmp_path,monkeypatch,cancel_at):
    from backend.api import routes_model_deployments
    from backend.api.routes_flowchart import _single_inspection_template,save_pipeline
    client,project,source=setup_project(tmp_path)
    flow=_single_inspection_template('classification',job_id='job_parent')
    saved=save_pipeline(flow,recipe_task='classification',source_dataset_path=str(source),
        request=SimpleNamespace(state=SimpleNamespace(scoped_project=project,account_user=None)))
    model_operations.configure_program(project,{'parent_job_id':'job_parent','task':'classification','auto_retrain':True,
        'auto_approve':True,'auto_deploy':True,'approval_policy_authorized':True,'pipeline_id':flow.id,
        'pipeline_version_id':saved['version_id'],'require_label_review':False,'reviewer':'qa'})
    Image.new('RGB',(32,32),'red').save(source/'new.png');event=threading.Event();deployments=[]
    def approve(*args):
        if cancel_at=='approval':event.set()
        return {'revision':{'revision_id':'reviewed-revision'}}
    def deploy(*args):
        if cancel_at=='deployment_prepare':event.set();raise InterruptedError('Cancelled before applying the prepared release')
        deployments.append('owned-runtime');event.set();return {'status':'ready','manifest_sha256':'a'*64}
    monkeypatch.setattr(routes_model_deployments,'approve_candidate',approve)
    monkeypatch.setattr(model_operations,'_deploy_candidate',deploy)
    result=model_operations.run_cycle(project,event,training_fn=lambda *args:{'status':'completed','winner':{'checkpoint_path':'/fixture/job_candidate/best_model.pt'}},
        evaluation_fn=lambda *args:{'comparison':{'comparison_id':'fixture','selected_image_count':8}})
    reopened=model_operations.OperationsStore(project['project_dir']).get(result['cycle_id'])
    assert reopened['result']['approval']['revision']['revision_id']=='reviewed-revision'
    if cancel_at in {'approval','deployment_prepare'}:
        assert reopened['status']=='approved_deployment_cancelled' and not deployments and reopened['result'].get('deployment') is None
    else:
        assert reopened['status']=='deployed' and deployments==['owned-runtime']
        assert reopened['result']['deployment']['status']=='ready' and reopened['result']['cancellation_requested'] is True

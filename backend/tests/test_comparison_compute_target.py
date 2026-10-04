"""Comparison target binding uses the production route and portable worker path."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from backend.api import routes_model_comparisons as routes
from backend.remote import profiles, operations
from backend.remote.profiles import ComputeProfile
from backend.tests.test_model_comparisons import _client, _checkpoint
from backend.tests.test_remote_portable_flow import portable_models
from backend.tests.test_remote_operations import FakeAdditionalRemote


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'userdata'))
    source=tmp_path/'source';folder=source/'test'/'OK';folder.mkdir(parents=True)
    Image.new('RGB',(32,32),'white').save(folder/'part.png')
    client=_client(tmp_path)
    project=client.post('/api/project/create',json={'name':'Target bound comparison','task':'classification'}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    project['source_dataset_dir']=str(source)
    for job in ['job_incumbent','job_candidate']:
        _checkpoint(Path(project['models_dir'])/job,source,routes._fingerprint(source),0)
    profile=ComputeProfile(id='selected',name='GPU2',ssh_target='user@server',ssh_port=22,
        remote_root=str(tmp_path/'remote'),runtime_kind='docker',runtime_value='worker-image',gpu_selector='2')
    held=[profile]
    monkeypatch.setattr(profiles,'get_profile_store',lambda:SimpleNamespace(get=lambda key:held[0] if key=='selected' else None))
    payload={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_incumbent',
             'candidate_job_id':'job_candidate','full_test':True,'execution_target':'selected_compute',
             'compute_profile_id':'selected','device':'cuda'}
    return client,project,source,payload,held


def test_selected_target_routes_each_model_without_local_cpu_fallback(comparison,monkeypatch):
    client,project,source,payload,held=comparison;calls=[]
    monkeypatch.setattr(routes,'FlowchartEngine',lambda **kw:pytest.fail('Selected server silently ran host CPU'))
    def execute(profile,project,pipeline,checkpoints,image,image_id,**kwargs):
        assert profile==held[0] and kwargs['device']=='cuda'
        assert len(checkpoints)==1 and kwargs['comparison_binding_sha256']
        calls.append((image,checkpoints,kwargs))
        return {'final_verdict':'OK','crops':[],'execution_target':'selected_compute','execution_device':'cuda',
                'compute_profile_id':'selected','compute_gpu_selector':'2','device_name':'controlled GPU',
                'remote_operation_id':'op_'+'a'*32,'remote_result_sha256':'b'*64}
    monkeypatch.setattr(operations,'run_verified_flowchart_on_compute',execute)
    response=client.post('/api/evaluation/model-comparisons',json=payload)
    assert response.status_code==200,response.text
    report=response.json();assert len(calls)==2 and report['execution']['compute_profile']['id']=='selected'
    assert report['execution']['device']=='cuda'
    for role in ('incumbent','candidate'):
        assert report['images'][0][role]['execution']['remote_operation_id']=='op_'+'a'*32
    assert calls[0][0]==calls[1][0] and not report['summary']['error_images']


def test_profile_configuration_is_frozen_in_durable_comparison_binding(comparison):
    _,project,source,payload,held=comparison
    before=routes._comparison_binding(routes.ComparisonRequest(**payload),project,source)
    held[0]=held[0].model_copy(update={'gpu_selector':'3'})
    after=routes._comparison_binding(routes.ComparisonRequest(**payload),project,source)
    assert before!=after


@pytest.mark.parametrize('changes',[{'compute_profile_id':None},{'compute_profile_id':'missing'},
                                    {'execution_target':'local_cpu','device':'cuda'}])
def test_invalid_comparison_target_refused_before_inference(comparison,monkeypatch,changes):
    client,_,_,payload,_=comparison
    monkeypatch.setattr(routes,'FlowchartEngine',lambda **kw:pytest.fail('Invalid target executed'))
    response=client.post('/api/evaluation/model-comparisons',json={**payload,**changes})
    assert response.status_code in (404,422),response.text


@pytest.mark.parametrize('phase',['upload','launched'])
def test_bound_portable_comparison_cancel_releases_only_its_confirmed_worker(tmp_path,monkeypatch,phase):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path);intent=[False];signals=[]
    class CancelRemote(FakeAdditionalRemote):
        def upload(self,*args,**kwargs):
            result=super().upload(*args,**kwargs)
            if phase=='upload':intent[0]=True
            return result
        def launch(self,*args):
            handle=super().launch(*args)
            path=self.root/'runs'/args[2]/'status.json';status=json.loads(path.read_text())
            spec=path.parent/'spec.json'
            status.update(spec_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),device='cpu')
            path.write_text(json.dumps(status))
            if phase=='launched':
                intent[0]=True
                path=self.root/'runs'/args[2]/'status.json';status=json.loads(path.read_text());status['status']='running';path.write_text(json.dumps(status))
            return handle
        def touch_cancel(self,profile,op_id):
            signals.append(op_id);return super().touch_cancel(profile,op_id)
    remote=CancelRemote(Path(profile.remote_root))
    with pytest.raises(InterruptedError):
        operations.run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote,
            comparison_binding_sha256='a'*64,cancelled=lambda:intent[0])
    journal=json.loads(next((Path(project['reports_dir'])/'remote_flow').rglob('flowchart_run_*.json')).read_text())
    assert journal['state']=='aborted' and journal['worker_exit_confirmed'] and journal['cancel_acknowledged_at']
    assert remote.launches==(0 if phase=='upload' else 1)
    assert signals==([] if phase=='upload' else [journal['op_id']])
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list()==[]


def test_cancelled_comparison_with_uncertain_exit_retains_reservation(tmp_path,monkeypatch):
    from backend.remote.coordinator import RemoteDisconnected
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path);intent=[False];signals=[]
    class Uncertain(FakeAdditionalRemote):
        def launch(self,*args):
            handle=super().launch(*args);intent[0]=True
            run=self.root/'runs'/args[2];spec=run/'spec.json';path=run/'status.json'
            status=json.loads(path.read_text());status.update(spec_sha256=hashlib.sha256(spec.read_bytes()).hexdigest(),device='cpu',status='running');path.write_text(json.dumps(status))
            return handle
        def touch_cancel(self,profile,op_id):
            signals.append(op_id);return super().touch_cancel(profile,op_id)
    monkeypatch.setattr(operations,'_confirm_owned_exit',lambda *args:(_ for _ in ()).throw(RemoteDisconnected('Controlled owned exit unknown')))
    remote=Uncertain(Path(profile.remote_root))
    with pytest.raises(RemoteDisconnected,match='exit unknown'):
        operations.run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote,
            comparison_binding_sha256='a'*64,cancelled=lambda:intent[0])
    from backend.engine.shared_scheduler import shared_leases
    rows=shared_leases().list();assert len(rows)==1 and rows[0]['uncertain']==1
    journal=json.loads(next((Path(project['reports_dir'])/'remote_flow').rglob('flowchart_run_*.json')).read_text())
    assert signals==[journal['op_id']] and not journal.get('worker_exit_confirmed')


def test_foreign_comparison_cancel_intent_refused_before_remote_signal(tmp_path):
    from backend.remote.coordinator import ArtifactValidationError
    context=SimpleNamespace(output_dir=tmp_path,job_id='job_owned')
    journal={'op_id':'op_'+'a'*32,'spec':{'comparison_operation_contract':1,'comparison_binding_sha256':'b'*64}}
    path=operations._common_cancel_path(context,journal);path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'op_id':journal['op_id'],'job_id':'job_foreign','comparison_binding_sha256':'b'*64,'cancel_requested_at':1}))
    remote=SimpleNamespace(touch_cancel=lambda *args:pytest.fail('Foreign worker signalled'))
    with pytest.raises(ArtifactValidationError):operations._deliver_common_cancel(context,journal,remote)


def test_hash_valid_portable_result_from_foreign_comparison_binding_is_refused(tmp_path,monkeypatch):
    from backend.remote.coordinator import ArtifactValidationError
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    class ForeignResult(FakeAdditionalRemote):
        def launch(self,*args):
            handle=super().launch(*args);run=self.root/'runs'/args[2];spec=json.loads((run/'spec.json').read_text())
            status=json.loads((run/'status.json').read_text());status.update(spec_sha256=hashlib.sha256((run/'spec.json').read_bytes()).hexdigest(),device='cpu');(run/'status.json').write_text(json.dumps(status))
            result=run/'outputs/flowchart_result.json';body=json.loads(result.read_text());body['comparison_binding_sha256']='f'*64;result.write_text(json.dumps(body))
            manifest=json.loads((run/'artifacts.json').read_text())
            for row in manifest['artifacts']:
                if row['path']=='outputs/flowchart_result.json':row.update(size=result.stat().st_size,sha256=hashlib.sha256(result.read_bytes()).hexdigest())
            (run/'artifacts.json').write_text(json.dumps(manifest));return handle
    with pytest.raises(ArtifactValidationError,match='different operation binding'):
        operations.run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',
            transport=ForeignResult(Path(profile.remote_root)),comparison_binding_sha256='a'*64)
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list()==[]


def test_portable_comparison_receipt_passes_actual_worker_binding_and_result_verification(tmp_path,monkeypatch):
    import base64
    from backend.remote.worker import run_flowchart
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    png='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()
    class Engine:
        def execute(self,**kwargs):
            assert kwargs['pipeline'].nodes and Path(kwargs['image_path']).read_bytes()==image.read_bytes()
            return {'final_verdict':'OK','annotated_image':png,'crops':[],'execution_steps':[]}
    class WorkerRemote(FakeAdditionalRemote):
        def launch(self,*args):
            handle=super().launch(*args);run=self.root/'runs'/args[2]
            (run/'status.json').unlink();(run/'artifacts.json').unlink()
            status=run_flowchart(run/'spec.json',engine_factory=lambda checkpoints,device:Engine())
            assert status['status']=='completed',status.get('error')
            return handle
    result=operations.run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',
        transport=WorkerRemote(Path(profile.remote_root)),comparison_binding_sha256='a'*64,cancelled=lambda:False)
    assert result['comparison_binding_sha256']=='a'*64 and result['remote_operation_id'].startswith('op_')
    downloaded=Path(project['reports_dir'])/'remote_flow'/'remote_operations'/result['remote_operation_id']/'outputs/flowchart_result.json'
    assert hashlib.sha256(downloaded.read_bytes()).hexdigest()==result['remote_result_sha256']


def test_legacy_cpu_idempotency_replay_stays_same_job_with_new_explicit_defaults(tmp_path):
    from backend.tests.test_comparison_durable_jobs import coordinator
    from backend.engine.job_store import JobConflict
    jobs,identifier,context,_=coordinator(tmp_path)
    legacy=json.loads(jobs.store.record(identifier)['spec_json'])['request']
    modern={**legacy,'execution_target':'local_cpu','compute_profile_id':None,'device':'cpu'}
    assert jobs.replay(context,'key','request',modern)['id']==identifier
    with pytest.raises(JobConflict):jobs.replay(context,'key','request',{**modern,'execution_target':'selected_compute','compute_profile_id':'server'})

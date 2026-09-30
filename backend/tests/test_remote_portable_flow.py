import base64
import copy
import json
import builtins
import importlib.util
import os
import sys
from types import SimpleNamespace
from pathlib import Path

import pytest
import torch
from PIL import Image

from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.remote.coordinator import ArtifactValidationError
from backend.remote.operations import run_verified_flowchart_on_compute, RemoteComputeBusy
from backend.remote.profiles import ComputeProfile
from backend.remote.worker import run_flowchart
from backend.tests.test_remote_operations import FakeAdditionalRemote


def portable_models(tmp_path):
    project={'models_dir':str(tmp_path/'project'/'models'),'project_dir':str(tmp_path/'project'),
             'reports_dir':str(tmp_path/'project'/'reports')}
    checkpoints={}
    for job,task in [('a'*32,'enhancement'),('job_1790739931_dcbffa','segmentation')]:
        output=Path(project['models_dir'])/(task if task=='enhancement' else '')/job
        output.mkdir(parents=True)
        checkpoint=output/'best_model.pt'
        torch.save({'task':task,'model_state_dict':{'weight':torch.zeros(1)}},checkpoint)
        (output/'model_meta.json').write_text(json.dumps({'task':task}))
        checkpoints[(job,task)]=checkpoint
    pipeline=get_single_segmentation_flowchart('job_1790739931_dcbffa').model_dump()
    enhance=copy.deepcopy(pipeline['nodes'][1]);enhance['id']='enhance'
    enhance['data'].update({'node_type':'preprocess','model_job_id':'a'*32,'params':{'operation':'enhancement'}})
    pipeline['nodes'].insert(1,enhance)
    pipeline['edges'][0]['target']='enhance'
    edge=copy.deepcopy(pipeline['edges'][0]);edge.update({'id':'enhance-inspect','source':'enhance','target':'node_inspect'})
    pipeline['edges'].insert(1,edge)
    image=tmp_path/'image.png';Image.new('RGB',(16,16),(40,50,60)).save(image)
    profile=ComputeProfile(id='selected-profile',name='Selected GPU',ssh_target='user@server',ssh_port=22,remote_root=str(tmp_path/'remote'),
                           runtime_kind='docker',runtime_value='worker-image',gpu_selector='2')
    return project,checkpoints,pipeline,image,profile


def test_selected_compute_uploads_all_owned_models_independent_of_training_location(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    result=run_verified_flowchart_on_compute(profile,project,pipeline,models,image,'known',device='cpu',transport=remote)
    assert result['compute_profile_id']==profile.id
    assert result['execution_target']=='selected_compute' and result['execution_device']=='cpu'
    assert result['annotated_image'].startswith('data:image/png;base64,')
    assert remote.launches==1
    spec=json.loads(next((Path(profile.remote_root)/'runs').glob('op_*/spec.json')).read_text())
    assert spec['portable_models'] is True and spec['device']=='cpu'
    assert {row['task'] for row in spec['models']}=={'enhancement','segmentation'}
    run=next((Path(profile.remote_root)/'runs').glob('op_*'))
    for row in spec['models']:
        assert (run/row['checkpoint_path']).read_bytes()==models[(row['job_id'],row['task'])].read_bytes()
    from backend.engine.shared_scheduler import shared_leases
    assert shared_leases().list()==[]


@pytest.mark.parametrize('damage',['foreign_project','linked_metadata','wrong_task'])
def test_portable_model_validation_fails_before_any_transfer(tmp_path,monkeypatch,damage):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    checkpoint=models[('a'*32,'enhancement')]
    if damage=='foreign_project':
        foreign=tmp_path/'foreign'/'best_model.pt';foreign.parent.mkdir();foreign.write_bytes(checkpoint.read_bytes())
        models[('a'*32,'enhancement')]=foreign
    elif damage=='linked_metadata':
        metadata=checkpoint.parent/'model_meta.json';content=metadata.read_text();metadata.unlink()
        target=tmp_path/'elsewhere.json';target.write_text(content);metadata.symlink_to(target)
    else:
        torch.save({'task':'classification','model_state_dict':{}},checkpoint)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    with pytest.raises((ArtifactValidationError,ValueError)):
        run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    assert remote.launches==0


def test_selected_remote_operation_obeys_existing_shared_gpu_owner(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    from backend.engine.shared_scheduler import shared_leases
    leases=shared_leases();assert leases.acquire('unrelated-training','ssh:server:22','2',remote=True)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    with pytest.raises(RemoteComputeBusy):
        run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    assert remote.launches==0
    assert leases.list()[0]['job_id']=='unrelated-training'


def test_uncertain_portable_launch_keeps_reservation_and_retries_same_worker(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    from backend.engine.shared_scheduler import shared_leases
    from backend.remote.coordinator import RemoteDisconnected
    class AmbiguousLaunch(FakeAdditionalRemote):
        def launch(self,*args):
            assert shared_leases().list()[0]['selector']=='2'
            super().launch(*args)
            raise ConnectionError('Worker launched; SSH response was lost')
    remote=AmbiguousLaunch(Path(profile.remote_root))
    with pytest.raises(RemoteDisconnected):
        run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    pending=shared_leases().list()
    assert len(pending)==1 and pending[0]['remote']==1 and pending[0]['uncertain']==1
    result=run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    assert result['execution_device']=='cpu' and remote.launches==1
    assert shared_leases().list()==[]


def test_prelaunch_preparing_journal_reuploads_and_launches_once_after_process_crash(tmp_path,monkeypatch):
    from backend.remote import operations
    from backend.engine.shared_scheduler import shared_leases
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    class InterruptedUpload(FakeAdditionalRemote):
        interrupted = False
        def upload(self,*args):
            if not self.interrupted:
                self.interrupted = True
                raise SystemExit('Simulated process crash before worker launch')
            return super().upload(*args)
    remote=InterruptedUpload(Path(profile.remote_root))
    with pytest.raises(SystemExit):
        run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    journal=next((Path(project['reports_dir'])/'remote_flow').rglob('flowchart_run_*.json'))
    assert json.loads(journal.read_text())['state']=='preparing' and remote.launches==0
    execute=operations._run_remote_operation_artifacts
    def bounded(*args,**kwargs):
        kwargs['timeout_seconds']=0
        return execute(*args,**kwargs)
    monkeypatch.setattr(operations,'_run_remote_operation_artifacts',bounded)
    result=run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    assert result['execution_target']=='selected_compute' and remote.launches==1
    assert json.loads(journal.read_text())['state']=='completed' and shared_leases().list()==[]


def test_operations_imports_without_unix_fcntl(monkeypatch):
    from backend.remote import operations
    spec=importlib.util.spec_from_file_location('portable_operations_import',operations.__file__)
    module=importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules,spec.name,module)
    original=builtins.__import__
    def portable_import(name,*args,**kwargs):
        if name=='fcntl': raise ImportError('Unavailable on native Windows')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',portable_import)
    spec.loader.exec_module(module)
    assert module.fcntl is None


def test_operation_file_lock_uses_windows_nonblocking_lock_without_o_nofollow(tmp_path,monkeypatch):
    from backend.remote import operations
    calls=[]
    monkeypatch.setattr(operations,'fcntl',None)
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(LK_NBLCK=1,LK_UNLCK=0,
        locking=lambda descriptor,mode,count:calls.append((mode,count))))
    monkeypatch.delattr(os,'O_NOFOLLOW',raising=False)
    descriptor=operations._open_operation_lock(tmp_path/'operation.lock')
    os.close(descriptor)
    assert calls==[(1,1)]


def test_same_flow_can_run_on_two_explicit_selected_profiles_without_cached_owner_collision(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    second=profile.model_copy(update={'id':'other-selected-profile','gpu_selector':'3'})
    result=run_verified_flowchart_on_compute(second,project,pipeline,models,image,device='cpu',transport=remote)
    assert result['compute_profile_id']==second.id and result['compute_gpu_selector']=='3'
    assert remote.launches==2


def test_portable_worker_rejects_unavailable_selected_device_without_cpu_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cuda',transport=remote)
    run=next((Path(profile.remote_root)/'runs').glob('op_*'))
    (run/'status.json').unlink();(run/'artifacts.json').unlink()
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    result=run_flowchart(run/'spec.json',engine_factory=lambda *_:pytest.fail('Silent CPU fallback executed'))
    assert result['status']=='failed' and 'unavailable' in result['error']


@pytest.mark.parametrize('tamper',[None,'checkpoint','metadata','pipeline'])
def test_portable_worker_verifies_transferred_model_and_pipeline_before_execution(tmp_path,monkeypatch,tamper):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'userdata'))
    project,models,pipeline,image,profile=portable_models(tmp_path)
    remote=FakeAdditionalRemote(Path(profile.remote_root))
    run_verified_flowchart_on_compute(profile,project,pipeline,models,image,device='cpu',transport=remote)
    run=next((Path(profile.remote_root)/'runs').glob('op_*'));spec_path=run/'spec.json'
    spec=json.loads(spec_path.read_text())
    for output in ('status.json','artifacts.json'):(run/output).unlink()
    if tamper in ('checkpoint','metadata'):
        row=spec['models'][0];(run/row[tamper+'_path']).write_bytes(b'changed')
    elif tamper=='pipeline':
        spec['pipeline']['nodes'][1]['data']['model_job_id']='b'*32;spec_path.write_text(json.dumps(spec))
    png='data:image/png;base64,'+base64.b64encode(image.read_bytes()).decode()
    class Engine:
        def execute(self,**kwargs):
            assert tamper is None,'Unverified transferred weights executed'
            return {'final_verdict':'OK','annotated_image':png,'crops':[],'execution_steps':[]}
    result=run_flowchart(spec_path,engine_factory=lambda checkpoints,device:Engine())
    assert result['status']==('completed' if tamper is None else 'failed'),result.get('error')
    if tamper is None:
        assert json.loads((run/'artifacts.json').read_text())['model_refs']==sorted(spec['models'],key=lambda row:row['job_id'])

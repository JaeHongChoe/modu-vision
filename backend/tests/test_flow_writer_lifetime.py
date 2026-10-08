"""Selected CPU flow producer controls; no full API/tree/release qualification."""
from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.engine import flow_package_runtime as runtime, runtime_deadline
from backend.tests.test_application_launch_execution import controlled_canary_publication


@pytest.fixture(scope='module')
def cpu_package(tmp_path_factory):
    import torch
    from PIL import Image
    from backend.engine.ocr import SmallCTCOCR
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowNode, FlowNodeData, FlowEdge, FlowchartPipeline
    directory = tmp_path_factory.mktemp('flow-cpu-inputs')
    model = SmallCTCOCR(1)
    for parameter in model.parameters(): parameter.data.zero_()
    model.head.bias.data[1] = 20
    checkpoint = directory/'model'/'best_model.pt'; checkpoint.parent.mkdir()
    torch.save({'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],
        'model_state_dict':model.state_dict(),'dataset_provenance':{'dataset_sha256':'controlled'},'best_epoch':1},checkpoint)
    nodes = [FlowNode(id=name,position={},data=FlowNodeData(label=name,node_type=kind,
        task='ocr' if name=='ocr' else None,model_job_id='a'*32 if name=='ocr' else None,
        params={'regex':'^A$'} if name=='ocr' else {}))
        for name,kind in [('input','input'),('ocr','inspection'),('decision','decision'),('output','output')]]
    graph = FlowchartPipeline(nodes=nodes,edges=[FlowEdge(id=f'e{i}',source=nodes[i].id,target=nodes[i+1].id) for i in range(3)])
    package = Path(build_flow_package(pipeline=graph,checkpoints={'a'*32:checkpoint},
        output_base_dir=directory/'exports',package_name='selected_cpu')['package_path'])
    image=directory/'input.png'; Image.new('RGB',(64,32),(127,127,127)).save(image)
    return package,image


@pytest.fixture
def writer_capability(tmp_path,monkeypatch):
    """Real original OFD/registry; process reader is explicitly controlled here."""
    from backend.engine import application_launch_quiescence as q, application_launch_lease as lease, runtime_update as update
    from backend.tests.test_application_launch_lease import installed,reserve
    root,value,_=installed(tmp_path); owner=reserve(root,value)
    epoch=q.WriterEpoch.create(root,owner.nonce,expected_launch_sha256=update._sha(update._canonical(lease._load(root))))
    registration=epoch.enroll('backend',expected_registry_sha256=epoch.snapshot()['registry_sha256'])
    guard=q.writer_guard(root,owner.nonce,registration.writer_id,expected_registration_sha256=registration.registration_sha256)
    with guard as retained:
        private=os.dup(retained.pass_fds[0]); info=os.fstat(private)
        proof={'nonce':owner.nonce}; state=handshake.BackendWorkAdmission()
        cache={'context':(),'ready':True,'proof':proof,'challenge':{'writer':{
            'writer_id':registration.writer_id,'registration_sha256':registration.registration_sha256}},
            'writer_guard':guard,'writer_handle':retained,'writer_private_fd':private,
            'writer_fd_identity':(info.st_dev,info.st_ino),'admission':state}
        protocol=[4]
        monkeypatch.setattr(handshake,'_root_context',lambda:(root,{}))
        monkeypatch.setattr(handshake,'_context',lambda:())
        def validate(*_,validated=None,absolute_deadline=None,before_acquire=None):
            # Model the original bounded-entry arguments, never owner authentication.
            if absolute_deadline is not None:
                assert type(absolute_deadline) in (int,float) and math.isfinite(absolute_deadline)
                assert handshake.time.monotonic() < absolute_deadline
            if before_acquire is not None:
                assert callable(before_acquire)
                before_acquire()
            if absolute_deadline is not None: assert handshake.time.monotonic() < absolute_deadline
            if validated is not None: validated['protocol_version']=protocol[0]
            return proof
        monkeypatch.setattr(handshake,'_validate',validate)
        monkeypatch.setattr(handshake,'_CACHE',cache)
        try: yield state,epoch,cache,protocol
        finally: os.close(private); owner.close()


def options(): return {'device':'cpu','deadline_ms':30000,'cpu_threads':1}


def test_closed_original_admission_refuses_before_workspace_or_process(cpu_package,writer_capability,tmp_path,monkeypatch):
    state,*_=writer_capability; state.close(); marker=tmp_path/'premature-workspace'
    original=runtime_deadline.owned_process_workspace
    def workspace(**kwargs): marker.write_text('created'); return original(**kwargs)
    monkeypatch.setattr(runtime_deadline,'owned_process_workspace',workspace)
    def dispatch(*_,**__): raise AssertionError('Closed producer reached subprocess dispatch')
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',dispatch)
    with pytest.raises(handshake.HandshakeError,match='closed'):
        runtime._run_isolated(*cpu_package,'closed-image',options())
    assert not marker.exists() and state.snapshot()['active_scopes']==0


def test_flow_scope_spans_result_validation_and_workspace_cleanup(cpu_package,writer_capability,monkeypatch):
    state,*_=writer_capability; observed=[]
    original=runtime_deadline.owned_process_workspace
    @contextmanager
    def workspace(**kwargs):
        with original(**kwargs) as owned:
            observed.append(('workspace',state.snapshot()['active_scopes']))
            try: yield owned
            finally: observed.append(('cleanup',state.snapshot()['active_scopes']))
        observed.append(('cleaned',state.snapshot()['active_scopes']))
    def dispatch(command,**kwargs):
        observed.append(('dispatch',state.snapshot()['active_scopes']))
        assert len(kwargs['pass_fds'])==1
        Path(command[-1]).write_text('{"final_verdict":"OK"}')
        return {'status':'completed','returncode':0,'pid':123,'elapsed_ms':1}
    original_loads=runtime.json.loads
    def loads(raw,*args,**kwargs):
        if raw=='{"final_verdict":"OK"}': observed.append(('parse',state.snapshot()['active_scopes']))
        return original_loads(raw,*args,**kwargs)
    monkeypatch.setattr(runtime_deadline,'owned_process_workspace',workspace)
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',dispatch)
    monkeypatch.setattr(runtime.json,'loads',loads)
    result=runtime._run_isolated(*cpu_package,'controlled-result',options())
    assert result['final_verdict']=='OK'
    assert observed==[(name,1) for name in ('workspace','dispatch','parse','cleanup','cleaned')]
    assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


@pytest.mark.parametrize('status',['timeout','cancelled','uncertain'])
def test_noncompletion_retains_public_contract_and_sticky_admission(cpu_package,writer_capability,monkeypatch,status):
    state,*_=writer_capability
    outcome={'status':status,'returncode':1,'pid':123,'elapsed_ms':1,'stdout':'','stderr':'controlled refusal','final_verdict':'REVIEW'}
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',lambda *_,**__:dict(outcome))
    if status=='uncertain':
        with pytest.raises(RuntimeError,match='Owned inference failed'):
            runtime._run_isolated(*cpu_package,'refused-image',options())
    else:
        assert runtime._run_isolated(*cpu_package,'refused-image',options())=={**outcome,'image_id':'refused-image'}
    assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}
    state.close(); assert state.drain(.01)['status']=='refused'


def test_invalid_output_stays_sticky_after_original_scope_leaves(cpu_package,writer_capability,monkeypatch):
    state,*_=writer_capability
    def dispatch(command,**kwargs):
        Path(command[-1]).write_text('invalid json')
        return {'status':'completed','returncode':0,'pid':123,'elapsed_ms':1}
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',dispatch)
    with pytest.raises(json.JSONDecodeError):runtime._run_isolated(*cpu_package,'invalid-image',options())
    assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}


def test_workspace_cleanup_failure_stays_sticky_before_count_leaves(cpu_package,writer_capability,monkeypatch):
    state,*_=writer_capability; original=runtime_deadline.owned_process_workspace
    @contextmanager
    def workspace(**kwargs):
        with original(**kwargs) as owned:
            yield owned
            assert state.snapshot()['active_scopes']==1
        assert state.snapshot()['active_scopes']==1
        raise OSError('controlled owned workspace cleanup refusal')
    def dispatch(command,**kwargs):
        Path(command[-1]).write_text('{"final_verdict":"OK"}')
        return {'status':'completed','returncode':0,'pid':123,'elapsed_ms':1}
    monkeypatch.setattr(runtime_deadline,'owned_process_workspace',workspace)
    monkeypatch.setattr(runtime_deadline,'execute_owned_process',dispatch)
    with pytest.raises(OSError,match='workspace cleanup refusal'):
        runtime._run_isolated(*cpu_package,'cleanup-image',options())
    assert state.snapshot()=={'active_scopes':0,'unsupported':['cpu_producer_unconfirmed']}


def test_required_adapter_import_failure_cannot_become_unowned(cpu_package,monkeypatch):
    import builtins
    original=builtins.__import__
    def blocked(name,*args,**kwargs):
        if name=='backend.engine.application_launch_handshake':
            raise ImportError('controlled required flow adapter unavailable')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',blocked)
    with pytest.raises(ImportError,match='required flow adapter unavailable'):
        runtime._run_isolated(*cpu_package,'import-image',options())


@pytest.mark.parametrize('device',['cuda:0','openvino:CPU',None])
def test_enrolled_flow_adapter_refuses_non_cpu_without_claiming_other_producers(writer_capability,device):
    state,*_=writer_capability
    with pytest.raises(handshake.HandshakeError,match='CPU|cpu'):
        with handshake.owned_flow_cpu_writer_scope(device=device): raise AssertionError('Non-CPU owned producer admitted')
    assert state.snapshot()=={'active_scopes':0,'unsupported':[]}


@pytest.mark.parametrize('device',['cpu','cuda:0','openvino:CPU'])
def test_true_unowned_accessor_preserves_empty_transport_for_existing_devices(monkeypatch,device):
    monkeypatch.setattr(handshake,'_CACHE',None); monkeypatch.setattr(handshake,'_root_context',lambda:None)
    with handshake.owned_flow_cpu_writer_scope(device=device) as transport: assert transport==()


@pytest.mark.parametrize('damage',['protocol2','partial4','changed_context','closed_epoch'])
def test_partial_foreign_or_closed_original_flow_capability_refuses(writer_capability,monkeypatch,damage):
    state,epoch,cache,protocol=writer_capability
    if damage=='protocol2':
        protocol[0]=2;cache['challenge']={}
        for name in ('writer_guard','writer_handle','writer_private_fd','writer_fd_identity'):cache[name]=None
    elif damage=='partial4':cache['writer_handle']=None
    elif damage=='changed_context':monkeypatch.setattr(handshake,'_context',lambda:('foreign',))
    else:epoch.close_epoch(expected_registry_sha256=epoch.snapshot()['registry_sha256'])
    with pytest.raises(ValueError):
        with handshake.owned_flow_cpu_writer_scope(device='cpu'):raise AssertionError('Unproven flow capability admitted')
    assert state.snapshot()['active_scopes']==0


@pytest.mark.parametrize('protocol',[2,3,4])
def test_actual_authenticated_cpu_flow_preserves_reference_and_original_transport(cpu_package,tmp_path,monkeypatch,protocol):
    """Real private descriptor/backend/CPU; parent identity is a controlled fixture."""
    from backend.tests import test_application_launch_handshake as fixtures
    from backend.engine import application_launch_lease as lease, application_launch_quiescence as q, runtime_update as update
    package,image=cpu_package; receipt=tmp_path/'actual-flow-writer-proof.json'
    original_fixture=fixtures.fixture; original_installed=fixtures.installed_backend; authority=[]
    program=f'''
from backend.engine import flow_package_runtime as f,runtime_deadline as d,application_launch_handshake as h
from pathlib import Path
state=h.backend_work_admission();observations=[]
original_execute=d.execute_owned_process
def observed_execute(*args,**kwargs):
 passed=kwargs.get('pass_fds',());identities=[{{'device':os.fstat(fd).st_dev,'inode':os.fstat(fd).st_ino}} for fd in passed]
 observations.append({{'at':'execute','active_scopes':state.snapshot()['active_scopes'],'passed_count':len(passed),'identities':identities,'fds':list(passed)}})
 original_popen=d.subprocess.Popen
 def observed_popen(*command,**kw):
  if command and command[0]==args[0]:
   inherited=kw.get('pass_fds',())
   observations.append({{'at':'popen','active_scopes':state.snapshot()['active_scopes'],'passed_count':len(inherited),'fds':list(inherited),
    'identities':[{{'device':os.fstat(fd).st_dev,'inode':os.fstat(fd).st_ino}} for fd in inherited]}})
  return original_popen(*command,**kw)
 d.subprocess.Popen=observed_popen
 try:result=original_execute(*args,**kwargs)
 finally:d.subprocess.Popen=original_popen
 observations.append({{'at':'returned','active_scopes':state.snapshot()['active_scopes'],'status':result['status'],'returncode':result['returncode']}})
 return result
d.execute_owned_process=observed_execute
if {protocol}==2:
 try:
  f.Predictor(Path({str(package)!r}),deadline_ms=30000,cpu_threads=1).predict(Path({str(image)!r}),'controlled-cpu')
  answer={{'refused':False}}
 except h.HandshakeError as error:answer={{'refused':True,'reason':str(error)}}
else:
 reference=f.run_flow_package(Path({str(package)!r}),Path({str(image)!r}),'controlled-cpu',device='cpu',cpu_threads=1,_owned_worker=True)
 result=f.Predictor(Path({str(package)!r}),deadline_ms=30000,cpu_threads=1).predict(Path({str(image)!r}),'controlled-cpu')
 answer={{'comparison':f.compare_flow_results(reference,result),'result':result}}
answer.update(observations=observations,state=state.snapshot(),protocol={protocol},controlled_source_transport=True,
 model_quality_approved=False,native_acceptance=False,whole_writer_coverage=False,process_tree_exit_verified=False,lease_release=False)
Path({str(receipt)!r}).write_text(json.dumps(answer))
'''
    def fixture(folder):
        value=original_fixture(folder); sign=value['sign']
        def added_program(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as z: files={name:z.read(name) for name in z.namelist()}
            manifest=json.loads(files.pop('portable-application.json'))
            script=files['bin/vision_backend.py']; at=script.index(b'print(json.dumps(')
            files['bin/vision_backend.py']=script[:at]+program.encode()+script[at:]
            manifest['files']=[{**row,'size':len(files[row['path']]),'sha256':hashlib.sha256(files[row['path']]).hexdigest()} for row in manifest['files']]
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('portable-application.json',json.dumps(manifest,sort_keys=True,separators=(',',':')))
                for name,raw in files.items():z.writestr(name,raw)
            payload.update(size=archive.stat().st_size,sha256=hashlib.sha256(archive.read_bytes()).hexdigest())
            payload['artifacts'][0].update(size=payload['size'],sha256=payload['sha256'])
            return sign(payload)
        value['sign']=added_program;return value
    def installed(folder):
        root,record,backend=original_installed(folder)
        if protocol==4:
            epoch=q.WriterEpoch.create(root,record['nonce'],expected_launch_sha256=update._sha(update._canonical(record)))
            registration=epoch.enroll('backend',expected_registry_sha256=epoch.snapshot()['registry_sha256'])
            writer={'writer_id':registration.writer_id,'registration_sha256':registration.registration_sha256,
                'registration_registry_sha256':epoch.snapshot()['registry_sha256']}
            record.update(protocol_version=4,writer_drain={**writer,'phase':'enrolled','request':None,'receipt':None,'backend_exit':None})
            authority.append((epoch,writer))
        elif protocol==2:
            record.update(protocol_version=2);record.pop('cpu_execution')
        if protocol!=3:
            update._write(root/lease.LEASES/record['nonce']/'journal.json',record)
            update._write(root/lease.ACTIVE_LEASE,lease._publication(record))
        return root,record,backend
    monkeypatch.setattr(fixtures,'fixture',fixture);monkeypatch.setattr(fixtures,'installed_backend',installed)
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [image,*package.rglob('*')] if p.is_file()}
    (tmp_path/'actual-inputs-before.json').write_text(json.dumps(protected,sort_keys=True))
    root,record,backend,channel,child,challenge=fixtures.launch(tmp_path,
        change=(lambda frame:frame.update(writer=authority[0][1])) if protocol==4 else None)
    try:
        assert fixtures.api().read_frame(channel,10)['kind']=='backend_claim'
        assert fixtures.api().read_frame(channel,10)['kind']=='backend_ready'
        stdout,stderr=child.communicate(timeout=45)
        (tmp_path/'actual-source.stdout').write_text(stdout);(tmp_path/'actual-source.stderr').write_text(stderr)
        assert child.returncode==0,stderr
        result=json.loads(receipt.read_text())
        assert result['state']=={'active_scopes':0,'unsupported':[]}
        if protocol==2:
            assert result['refused'] is True and result['observations']==[]
        else:
            assert result['comparison']['status']=='passed'
            assert result['result']['final_verdict']=='OK' and result['result']['roi_count']==1
            observed=result['observations'];assert [x['at'] for x in observed]==['execute','popen','returned']
            assert [x['active_scopes'] for x in observed]==[1,1,1]
            assert observed[2]['status']=='completed' and observed[2]['returncode']==0
            assert [x['passed_count'] for x in observed[:2]]==[1 if protocol==4 else 0]*2
            if protocol==4:
                epoch,writer=authority[0];snapshot=epoch.snapshot();row=snapshot['registry']['writers'][0]
                assert row['status']=='reserved' and row['process'] is None
                assert observed[0]['identities']==observed[1]['identities']==[row['lock_identity']]
                assert observed[0]['fds']!=observed[1]['fds']
                with pytest.raises(lease.LaunchLeaseError):lease.assert_quiescent(root)
        after={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in protected}
        (tmp_path/'actual-inputs-after.json').write_text(json.dumps(after,sort_keys=True))
        assert protected==after
    finally:
        channel.close()
        if child.poll() is None:child.communicate(timeout=45)


def test_fresh_exported_sdk_needs_no_application_import_and_keeps_default_transport(cpu_package,tmp_path):
    package,image=cpu_package
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [image,*package.rglob('*')] if p.is_file()}
    (tmp_path/'standalone-inputs-before.json').write_text(json.dumps(protected,sort_keys=True))
    for name in ('application_launch_handshake.py','global_store_paths.py','migration_guard.py'):
        assert (package/'backend'/'engine'/name).is_file()
    program='''import sys,json,importlib.abc
from pathlib import Path
class NoApplication(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,*args):
  if fullname=='backend.main' or fullname.startswith(('backend.api','backend.contracts')):raise AssertionError('Standalone SDK imported application dependency: '+fullname)
sys.meta_path.insert(0,NoApplication());sys.path.insert(0,sys.argv[1])
from backend.engine.flow_package_runtime import Predictor
result=Predictor(Path(sys.argv[1]),deadline_ms=30000,cpu_threads=1).predict(Path(sys.argv[2]),'standalone-cpu')
assert result['final_verdict']=='OK' and result['roi_count']==1
assert result['runtime_execution']['device']=='cpu'
print(json.dumps({'verdict':result['final_verdict'],'roi_count':result['roi_count'],'application_imported':False}))
'''
    env={key:value for key,value in os.environ.items() if not key.startswith('VISION_APPLICATION_') and key!='VISION_AI_STUDIO_USER_DATA_DIR'}
    child=subprocess.run([sys.executable,'-I','-B','-c',program,str(package),str(image)],env=env,
        cwd=tmp_path,capture_output=True,text=True,timeout=45)
    (tmp_path/'standalone.stdout').write_text(child.stdout);(tmp_path/'standalone.stderr').write_text(child.stderr)
    assert child.returncode==0,child.stderr
    assert json.loads(child.stdout)=={'verdict':'OK','roi_count':1,'application_imported':False}
    after={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in protected}
    (tmp_path/'standalone-inputs-after.json').write_text(json.dumps(after,sort_keys=True))
    assert protected==after

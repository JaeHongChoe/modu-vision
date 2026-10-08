"""PROPOSED ORIGINAL ACTUAL SOURCE qualification; source only until root grant.

Reuses original signed controlled ASGI/main/controller synthetic OCR stack,
original pin inputs, request, 30s reviewed plan, 210s dispatch and 4s managed
shutdown. Generated local one-character weights prove arithmetic, not quality.
Only retained original controller Popen is used after authenticated main exit.
"""
import json,os,sys,time
from pathlib import Path
import pytest
from backend.tests.test_application_cpu_writer_lifetime import wait_file

def test_actual_known_source_cpu_typed_relay_preserves_original_fences_through_final_ack(tmp_path, monkeypatch):
    """Actual CPU arithmetic; controlled source scripts are not a native release."""
    import zipfile
    from backend.tests import test_application_launch_execution as fixtures
    from backend.tests.test_application_launch_quiescence_bridge import managed_stack, finish_managed
    from backend.tests.test_staged_update_canary import controlled_proof
    from backend.tests.test_service_s6_04 import canonical, sha
    from backend.engine import application_launch_lease as lease
    from backend.engine.application_launch_quiescence import inspect_epoch

    controlled_proof(monkeypatch)  # Setup only, no staged-canary or native acceptance.
    original_fixture = fixtures.fixture
    instrumentation = '''
 from backend.engine import runtime_deadline as rd,application_launch_execution as ex,application_owned_cpu_child_relay as relay
 import psutil,stat
 original_spawn=rd.subprocess.Popen
 def observed_spawn(*args,**kwargs):
  child=original_spawn(*args,**kwargs)
  passed=kwargs.get('pass_fds',())
  if passed:
   info=os.fstat(passed[0]);writer=__import__('backend.engine.application_launch_handshake',fromlist=['_CACHE'])._CACHE['challenge']['writer']
   lock=root/'.application-writer-epochs'/os.environ['VISION_APPLICATION_LAUNCH_NONCE']/'writers'/writer['writer_id']/'ownership.lock'
   refs=[{'fd':f.fd,'path':f.path} for f in psutil.Process(child.pid).open_files() if f.path==str(lock)]
   (projects/'actual-cpu-writer-observation.json').write_text(json.dumps({'worker_pid':child.pid,'device':info.st_dev,'inode':info.st_ino,
    'child_writer_open_refs':refs,'active_scopes':state.snapshot()['active_scopes'],'passed_count':len(passed),'passed_fds':[{'fd':fd,'device':os.fstat(fd).st_dev,'inode':os.fstat(fd).st_ino,'file_type':stat.S_IFMT(os.fstat(fd).st_mode)} for fd in passed],
    'CUDA_VISIBLE_DEVICES':kwargs['env'].get('CUDA_VISIBLE_DEVICES'),'NVIDIA_VISIBLE_DEVICES':kwargs['env'].get('NVIDIA_VISIBLE_DEVICES'),
    'OMP_NUM_THREADS':kwargs['env'].get('OMP_NUM_THREADS'),'cpu_command_prefix':args[0][:5]}))
  return child
 rd.subprocess.Popen=observed_spawn
 original_checkpoint=ex._checkpoint
 def observed_checkpoint(stage):
  if stage in ('before_cpu_worker','after_cpu_output'):
   (projects/('actual-cpu-scope-'+stage+'.json')).write_text(json.dumps(state.snapshot()))
  return original_checkpoint(stage)
 ex._checkpoint=observed_checkpoint
 original_execute=ex.execute_backend
 def observed_execute(*args,**kwargs):
  result=original_execute(*args,**kwargs)
  (projects/'actual-cpu-scope-returned.json').write_text(json.dumps(state.snapshot()))
  return result
 ex.execute_backend=observed_execute
 from backend.engine import application_owned_cpu_child_relay as relay
 def observe(name,data):
  with (projects/name).open('x') as output:json.dump(data,output,sort_keys=True)
 original_exchange=relay._backend_cpu_exchange
 def observed_exchange(capability,action,payload):
  s=relay._PRODUCERS[capability]
  before=state.snapshot()
  result=original_exchange(capability,action,payload)
  assert state.snapshot()==before=={'active_scopes':1,'unsupported':[]}
  if action=='bind':
   assert not (s['snapshot']/'result.json').exists(), 'Math preceded original bind ACK and startup gate'
   assert s['child'].poll() is None
  observe('actual-source-cpu-'+action+'-ack.json',{'action':action,'payload':payload,'reply':result,
   'phase':s['phase'],'admission':before,'registration':s.get('registration'),
   'child_identity':s.get('child_identity'),'plan':s.get('plan'),
   'guard_identity':s.get('guard_identity'),'anchor_identity':s['anchor_identity'],
   'guard_closed':s.get('guard_closed',False),'result_exists':(s['snapshot']/'result.json').exists()})
  return result
 relay._backend_cpu_exchange=observed_exchange
 original_published=relay._backend_published_receipt
 def observed_published(s,answer):
  result=original_published(s,answer)
  assert state.snapshot()=={'active_scopes':1,'unsupported':[]} and not s['guard_closed']
  observe('actual-source-cpu-publication-readback.json',{'answer':answer,'admission':state.snapshot(),
   'registration':s['registration'],'child_identity':s['child_identity'],'completion':s['completion']})
  return result
 relay._backend_published_receipt=observed_published
'''
    def instrumented_fixture(folder):
        value = original_fixture(folder); original_sign = value['sign']
        def instrumented_sign(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as z: files={name:z.read(name) for name in z.namelist()}
            manifest=json.loads(files.pop('portable-application.json'))
            raw=files['bin/backend_fixture.py']; needle=b' backend_execution_service(stop)\n'
            assert raw.count(needle)==1
            files['bin/backend_fixture.py']=raw.replace(needle,instrumentation.encode()+needle)
            manifest['files']=[{'path':name,'size':len(data),'sha256':sha(data),'executable':name.startswith('bin/')} for name,data in files.items()]
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('portable-application.json',canonical(manifest))
                for name,data in files.items():z.writestr(name,data)
            payload.update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
            payload['artifacts'][0].update(sha256=payload['sha256'],size=payload['size'])
            return original_sign(payload)
        value['sign']=instrumented_sign
        return value
    monkeypatch.setattr(fixtures,'fixture',instrumented_fixture)
    values=managed_stack(tmp_path,monkeypatch);root,value,current,project,reviewed,pin=values
    inputs=[project/'project.json',project/reviewed['input_path'],project/reviewed['package_path']/'manifest.json',
            project/reviewed['package_path']/'models'/('a'*32)/'best_model.pt']
    protected={str(p):sha(p.read_bytes()) for p in inputs}
    child,ack=fixtures.start_cpu_stack(values, prefix=['-c', fixtures._source_input_first_refusal_script()])
    try:
        target,cpu=fixtures.wait_receipt(root,ack['nonce'],child)
        observation=wait_file(root/'projects/actual-cpu-writer-observation.json')
        returned=wait_file(root/'projects/actual-cpu-scope-returned.json')
        assert returned=={'active_scopes':0,'unsupported':[]}
        snapshot=inspect_epoch(root,ack['nonce']);writers=snapshot['registry']['writers']
        assert len(writers)==2
        backend,worker=sorted(writers,key=lambda row:row['role'])
        assert backend['role']=='backend' and backend['status']=='active'
        assert backend['process']==cpu['backend_process']
        assert worker['role']=='owned_cpu_worker' and worker['status']=='direct_exited'
        assert worker['reason_code'] is None and worker['exit_code']==0
        assert worker['process']['pid']==cpu['worker_pid'] and worker['process']!=backend['process']
        lock=root/'.application-writer-epochs'/ack['nonce']/'writers'/backend['writer_id']/'ownership.lock'
        info=lock.stat()
        assert (observation['device'],observation['inode'])==(info.st_dev,info.st_ino)
        assert observation['passed_count']==3 and observation['active_scopes']==1
        assert len(observation['child_writer_open_refs'])==1
        assert observation['child_writer_open_refs'][0]['path']==str(lock)
        assert observation['worker_pid']==cpu['worker_pid']
        descriptors=observation['passed_fds']
        assert len(descriptors)==3 and len({row['fd'] for row in descriptors})==3
        import stat
        assert descriptors[0]['file_type']==descriptors[1]['file_type']==stat.S_IFREG
        assert descriptors[2]['file_type']==stat.S_IFIFO
        worker_lock=root/'.application-writer-epochs'/ack['nonce']/'writers'/worker['writer_id']/'ownership.lock'
        worker_info=worker_lock.stat()
        assert (descriptors[1]['device'],descriptors[1]['inode'])==(worker_info.st_dev,worker_info.st_ino)
        assert (descriptors[0]['device'],descriptors[0]['inode'])!=(descriptors[1]['device'],descriptors[1]['inode'])
        exchange={action:wait_file(root/('projects/actual-source-cpu-'+action+'-ack.json')) for action in ('reserve','bind','finish')}
        published=wait_file(root/'projects/actual-source-cpu-publication-readback.json')
        assert exchange['reserve']['reply']=={'writer_id':worker['writer_id'],'registration_sha256':worker['registration_sha256']}
        assert exchange['bind']['reply']=={'status':'bound'} and exchange['bind']['result_exists'] is False
        assert exchange['finish']['reply']=={'status':'direct_exited'} and exchange['finish']['guard_closed'] is True
        for event in [*exchange.values(),published]:
            assert event['admission']=={'active_scopes':1,'unsupported':[]}
        assert published['child_identity']==worker['process']
        assert published['answer']['receipt_sha256']==sha(target.read_bytes())
        assert published['completion']['worker_pid']==cpu['worker_pid']
        assert exchange['bind']['plan']['command'][0]==sys.executable
        assert exchange['bind']['plan']['command'][1:4]==['-I','-B','-X']
        assert exchange['bind']['plan']['command'][5]=='-c' and len(exchange['bind']['plan']['command'])==12
        assert exchange['bind']['plan']['budget_ms']==reviewed['deadline_ms']==30000
        assert exchange['bind']['plan']['cpu_request']['plan_sha256']==pin
        assert observation['CUDA_VISIBLE_DEVICES']=='' and observation['NVIDIA_VISIBLE_DEVICES']=='none'
        assert observation['OMP_NUM_THREADS']=='1'
        for stage in ('before_cpu_worker','after_cpu_output'):
            assert wait_file(root/('projects/actual-cpu-scope-'+stage+'.json'))=={'active_scopes':1,'unsupported':[]}
        assert wait_file(root/'projects/actual-cpu-scope-returned.json')=={'active_scopes':0,'unsupported':[]}
        assert cpu['semantic_output']==fixtures.EXPECTED_OUTPUT
        assert cpu['actual_cpu_execution_verified'] is True and cpu['owned_backend_execution_origin_verified'] is True
        for key in ('worker_process_tree_exit_verified','actual_application_inference_verified','model_quality_approved','native_app_handshake_verified','release_ready'):
            assert cpu[key] is False
        assert protected=={str(p):sha(p.read_bytes()) for p in inputs}
        proof={'cpu_receipt_sha256':sha(target.read_bytes()),'cpu':cpu,'writer_observation':observation,
               'protected_inputs':protected,'writer_registry':snapshot,'controlled_source_fixture':True,
               'actual_cpu_math':True,'whole_writer_coverage':False,'process_tree_exit_verified':False,
               'native_release_qualified':False,'model_quality_approved':False,'lease_release':False}
        (root/'projects/actual-cpu-writer-proof.json').write_text(json.dumps(proof,indent=2))
    finally:
        finish_managed(child,root)
    final=lease._load(root)
    assert final['state']=='recovery_required'
    final_snapshot=inspect_epoch(root,ack['nonce'])
    final_writers=final_snapshot['registry']['writers']
    shutdown_observation=_source_cpu_post_shutdown_observation(root,final,final_snapshot,child)
    assert [(row['role'],row['status'],row['reason_code']) for row in final_writers]==[
        ('backend','direct_exited',None),('owned_cpu_worker','direct_exited',None)], shutdown_observation
    with pytest.raises(ValueError,match='launch ownership'):
        lease.assert_quiescent(root)
    assert child.returncode is not None
    post={'schema':'modu-vision.original-source-cpu-post-finish-fixture/v1',
          'writer_registry':inspect_epoch(root,ack['nonce']), 'lease_state':final,
          'original_retained_controller_pid':child.pid,'original_retained_controller_returncode':child.returncode,
          'controlled_source_fixture':True,'component_only':True,'whole_writer_coverage':False,
          'process_tree_exit_verified':False,'can_release_launch_lease':False,'release_ready':False}
    with (root/'projects/actual-source-cpu-post-finish-proof.json').open('x') as output:json.dump(post,output,indent=2)


def _source_cpu_post_shutdown_observation(root, final, snapshot, child):
    """Failure context only; never qualify rows, adopt a PID or alter cleanup."""
    serialized='Original post-shutdown diagnostic is unavailable'
    try:
        stop=None;stop_error=None;stop_sha=None;stop_bytes=None
        try:
            path=root/'projects/managed-stop-result.json'
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
            with os.fdopen(fd,'rb') as reader:raw=reader.read(65537)
            if len(raw)>65536:raise ValueError('Original stop observation exceeds diagnostic bound')
            stop=json.loads(raw)
            stop_sha=__import__('hashlib').sha256(raw).hexdigest();stop_bytes=len(raw)
        except BaseException as error:stop_error=type(error).__name__
        observation={'schema':'modu-vision.original-source-cpu-shutdown-observation/v1',
                     'original_managed_stop':stop,'original_lease_state':final,
                     'original_managed_stop_error_type':stop_error,'original_managed_stop_sha256':stop_sha,
                     'original_managed_stop_bytes':stop_bytes,'original_writer_snapshot':snapshot,
                     'original_retained_controller_pid':child.pid,
                     'original_retained_controller_returncode':child.returncode,
                     'qualification':False,'whole_writer_coverage':False,'process_tree_exit_verified':False,
                     'can_release_launch_lease':False,'release_ready':False}
        serialized=json.dumps(observation,sort_keys=True)
        with (root/'projects/actual-source-cpu-shutdown-observation.json').open('x') as output:output.write(serialized)
    except BaseException:
        # Diagnostic failures preserve the original row assertion and outcome.
        pass
    return serialized


class _FixtureClock:
    def __init__(self):self.now=100.0
    def monotonic(self):return self.now
    def sleep(self,seconds):self.now+=seconds


class _RetainedFixtureController:
    """Inert cleanup delegate only; no actual OS handle/process is constructed."""
    def __init__(self):
        self.pid=51;self.returncode=None;self.calls=[]
        self._original_cpu_fixture_nonce='a'*32
        self._original_cpu_fixture_supervisor={'pid':51,'created_at':1.0,'command_sha256':'b'*64}
    def poll(self):return self.returncode
    def terminate(self):self.calls.append('terminate')
    def wait(self,*,timeout):self.calls.append(('wait',timeout));self.returncode=-15;return -15


def _terminal_fixture_case(tmp_path,monkeypatch):
    import copy
    from backend.tests import test_application_launch_quiescence_bridge as bridge
    from backend.engine import application_launch_lease as lease
    clock=_FixtureClock();child=_RetainedFixtureController();(tmp_path/'projects').mkdir()
    row={'nonce':child._original_cpu_fixture_nonce,'state':'recovery_required','supervisor':copy.deepcopy(child._original_cpu_fixture_supervisor),
        'process':{'pid':52,'created_at':2.0,'command_sha256':'c'*64},'claimed':True,'ready_receipt_sha256':'d'*64,
        'exit_observation':{'direct_child_pid':52,'direct_child_returncode':3,'process_tree_exit_verified':False}}
    monkeypatch.setattr(bridge,'time',clock)
    monkeypatch.setattr(lease,'inspect_launch',lambda root:copy.deepcopy(row))
    monkeypatch.setattr(lease,'_identity',lambda pid:copy.deepcopy(child._original_cpu_fixture_supervisor))
    def impossible(*args,**kwargs):pytest.fail('Original ordinary-stop file is impossible after authenticated main exit')
    monkeypatch.setattr(bridge,'wait_file',impossible)
    return bridge,lease,clock,child,row


def test_fixture_terminal_cleanup_consumes_authentic_retained_main_exit(tmp_path,monkeypatch):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch)
    bridge.finish_managed(child,tmp_path)
    assert child.calls==['terminate',('wait',5)] and clock.now==100.0
    assert not (tmp_path/'projects/ordinary-stop-requested.json').exists()


@pytest.mark.parametrize('damage',['nonce','supervisor_pid','supervisor_birth','supervisor_command','main_pid','return_bool','return_missing','tree','state','claimed','receipt','extra_exit','late_inspect','late_identity','missing_nonce','missing_witness'])
def test_fixture_terminal_foreign_uncertain_or_late_never_cleans_original_controller(tmp_path,monkeypatch,damage):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch)
    if damage=='nonce':row['nonce']='e'*32
    if damage=='supervisor_pid':row['supervisor']['pid']=99
    if damage=='supervisor_birth':row['supervisor']['created_at']=9.0
    if damage=='supervisor_command':row['supervisor']['command_sha256']='f'*64
    if damage=='main_pid':row['exit_observation']['direct_child_pid']=99
    if damage=='return_bool':row['exit_observation']['direct_child_returncode']=True
    if damage=='return_missing':row['exit_observation']['direct_child_returncode']=None
    if damage=='tree':row['exit_observation']['process_tree_exit_verified']=True
    if damage=='state':row['state']='ready'
    if damage=='claimed':row['claimed']=False
    if damage=='receipt':row['ready_receipt_sha256']=None
    if damage=='extra_exit':row['exit_observation']['foreign']=True
    if damage=='missing_nonce':del child._original_cpu_fixture_nonce
    if damage=='missing_witness':del child._original_cpu_fixture_supervisor
    if damage=='late_inspect':
        def late(root):clock.now+=6;return row
        monkeypatch.setattr(lease,'inspect_launch',late)
    if damage=='late_identity':
        def late(pid):clock.now+=6;return child._original_cpu_fixture_supervisor
        monkeypatch.setattr(lease,'_identity',late)
    with pytest.raises((AssertionError,pytest.fail.Exception)):
        bridge.finish_managed(child,tmp_path)
    assert child.calls==[]


def test_fixture_terminal_only_original_typed_busy_retries_with_first_budget(tmp_path,monkeypatch):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch);calls=[]
    def busy(root):
        calls.append(clock.now)
        if len(calls)<3:raise lease.LeaseTransitionBusy('exact original publication mutex')
        return row
    monkeypatch.setattr(lease,'inspect_launch',busy)
    bridge.finish_managed(child,tmp_path)
    assert len(calls)==3 and child.calls==['terminate',('wait',5)] and clock.now<105


@pytest.mark.parametrize('error',[ValueError('structure changed'),RuntimeError('foreign owner')])
def test_fixture_terminal_structural_read_failure_does_not_retry_or_mask(tmp_path,monkeypatch,error):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch);calls=[]
    def fail(root):calls.append(1);raise error
    monkeypatch.setattr(lease,'inspect_launch',fail)
    with pytest.raises(type(error)) as caught:bridge.finish_managed(child,tmp_path)
    assert caught.value is error and calls==[1] and child.calls==[]


def test_fixture_terminal_busy_cannot_renew_original_first_five_seconds(tmp_path,monkeypatch):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch);calls=[]
    def busy(root):calls.append(clock.now);raise lease.LeaseTransitionBusy('original remains busy')
    monkeypatch.setattr(lease,'inspect_launch',busy)
    with pytest.raises(pytest.fail.Exception):bridge.finish_managed(child,tmp_path)
    assert child.calls==[] and 105<=clock.now<=105.1 and calls[-1]<105


def test_fixture_terminal_normal_five_seven_five_path_is_unchanged(tmp_path,monkeypatch):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch);calls=[]
    (tmp_path/'projects/ordinary-stop-requested.json').write_text('{}')
    def observed(path,*,seconds,absolute_deadline=None):
        calls.append((path.name,seconds))
        assert absolute_deadline==(105 if path.name=='ordinary-stop-requested.json' else None)
        return b'{}'
    monkeypatch.setattr(bridge,'wait_file',observed)
    bridge.finish_managed(child,tmp_path)
    assert calls==[('ordinary-stop-requested.json',5),('managed-stop-result.json',7)]
    assert (tmp_path/'projects/exit.trigger').exists() and child.calls==['terminate',('wait',5)]


def test_fixture_terminal_ordinary_file_disappearance_does_not_renew_first_budget(tmp_path,monkeypatch):
    from backend.tests import test_application_launch_quiescence_bridge as bridge
    original_wait=bridge.wait_file
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch)
    monkeypatch.setattr(bridge,'wait_file',original_wait)
    ordinary=tmp_path/'projects/ordinary-stop-requested.json';ordinary.write_text('{}')
    original_is_file=Path.is_file;seen=[]
    def disappears(path):
        if path==ordinary and not seen:
            seen.append(True);clock.now=104.9;ordinary.unlink();return True
        return original_is_file(path)
    monkeypatch.setattr(Path,'is_file',disappears)
    with pytest.raises(pytest.fail.Exception):bridge.finish_managed(child,tmp_path)
    assert child.calls==[] and 105<=clock.now<=105.01
    assert not (tmp_path/'projects/exit.trigger').exists()


def test_fixture_terminal_original_file_read_must_finish_before_same_deadline(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend.tests import test_application_launch_quiescence_bridge as bridge
    clock=_FixtureClock();monkeypatch.setattr(bridge,'time',clock)
    def late_read():clock.now=106;return b'{}'
    path=SimpleNamespace(is_file=lambda:True,read_bytes=late_read)
    with pytest.raises(AssertionError):bridge.wait_file(path,seconds=5,absolute_deadline=105)


def test_fixture_terminal_busy_crossing_expiry_never_sleeps_negative_or_cleans(tmp_path,monkeypatch):
    bridge,lease,clock,child,row=_terminal_fixture_case(tmp_path,monkeypatch)
    def late_busy(root):clock.now=106;raise lease.LeaseTransitionBusy('original publication delayed')
    monkeypatch.setattr(lease,'inspect_launch',late_busy)
    original_sleep=clock.sleep
    def bounded_sleep(seconds):assert seconds>=0;original_sleep(seconds)
    clock.sleep=bounded_sleep
    with pytest.raises(pytest.fail.Exception):bridge.finish_managed(child,tmp_path)
    assert child.calls==[] and clock.now==106


def _fixture_environment_observer(delegate):
    """Execute only the exact signed-fixture closure with an inert delegate."""
    import ast,types
    from backend.tests import test_application_launch_execution as fixtures
    tree=ast.parse(Path(fixtures.__file__).read_text())
    fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='test_reviewed_package_python_cannot_shadow_snapshotted_current_runtime')
    suffix=next(n.value.value for n in fn.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='suffix' for t in n.targets))
    signed=ast.parse(suffix.replace('\n ','\n').lstrip())
    closure=next(n for n in signed.body if isinstance(n,ast.FunctionDef) and n.name=='closed')
    namespace={'original':delegate,'Path':Path,'json':json,'relay':types.SimpleNamespace(subprocess=types.SimpleNamespace(Popen=delegate))}
    exec(compile(ast.Module(body=[closure],type_ignores=[]),'<original-signed-environment-observer>','exec'),namespace)
    return namespace['closed']


def _fixture_environment_inputs(tmp_path):
    command=['python','-I','-B','-X','pycache_prefix='+str(tmp_path/'bytecode'),'-c','original-startup',str(tmp_path/'request.json'),str(tmp_path/'result.json'),'19','30.0','a'*32]
    environment={'HOME':str(tmp_path/'home'),'HF_HUB_OFFLINE':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none'}
    return command,{'cwd':tmp_path,'env':environment,'pass_fds':(17,18,19)}


def test_fixture_environment_original_source_delegate_once_without_command_rewrite(tmp_path):
    from types import SimpleNamespace
    child=SimpleNamespace(pid=52);calls=[]
    def original(command,**options):calls.append((command,options));return child
    observe=_fixture_environment_observer(original);command,options=_fixture_environment_inputs(tmp_path)
    assert observe(command,**options) is child
    assert len(calls)==1 and calls[0][0] is command and calls[0][1]==options
    proof=json.loads((tmp_path/'worker-env-proof.json').read_bytes())
    assert proof=={'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none','pid':52,'command':command,'pass_fds':[17,18,19]}


@pytest.mark.parametrize('damage',['secret','pythonpath','gpu','nvidia','omp','mkl','openblas','home','offline','fds','command'])
def test_fixture_environment_foreign_inputs_refuse_before_original_spawn(tmp_path,damage):
    calls=[]
    def original(*args,**kwargs):calls.append((args,kwargs));raise AssertionError('Foreign input reached original spawn')
    observe=_fixture_environment_observer(original);command,options=_fixture_environment_inputs(tmp_path);env=options['env']
    if damage=='secret':env['OPENAI_API_KEY']='controlled'
    elif damage=='pythonpath':env['PYTHONPATH']='controlled'
    elif damage=='gpu':env['CUDA_VISIBLE_DEVICES']='0'
    elif damage=='nvidia':env['NVIDIA_VISIBLE_DEVICES']='all'
    elif damage in ('omp','mkl','openblas'):env[{'omp':'OMP_NUM_THREADS','mkl':'MKL_NUM_THREADS','openblas':'OPENBLAS_NUM_THREADS'}[damage]]='2'
    elif damage=='home':env['HOME']=str(tmp_path.parent/'foreign')
    elif damage=='offline':env['HF_HUB_OFFLINE']='0'
    elif damage=='fds':options['pass_fds']=(17,)
    else:command[1]='-m'
    with pytest.raises(AssertionError):observe(command,**options)
    assert calls==[] and not (tmp_path/'worker-env-proof.json').exists()


def test_fixture_environment_unrelated_original_spawn_is_unmodified(tmp_path):
    child=object();calls=[]
    def original(*args,**kwargs):calls.append((args,kwargs));return child
    observe=_fixture_environment_observer(original);command=['/bin/ps','-o','pgid=','-p','52']
    assert observe(command,text=True) is child
    assert calls==[((command,),{'text':True})] and not (tmp_path/'worker-env-proof.json').exists()


def test_fixture_environment_original_spawn_failure_is_not_retried_or_masked(tmp_path):
    failure=OSError('Original controlled spawn failure');calls=[]
    def original(*args,**kwargs):calls.append((args,kwargs));raise failure
    observe=_fixture_environment_observer(original);command,options=_fixture_environment_inputs(tmp_path)
    with pytest.raises(OSError) as caught:observe(command,**options)
    assert caught.value is failure and len(calls)==1 and not (tmp_path/'worker-env-proof.json').exists()


def _fixture_early_refusal():
    return {'error_type':'HandshakeError','error':'Original SOURCE CPU admission deadline expired','request_id':'a'*32,
            'admission':{'active_scopes':0,'unsupported':[]},'producer':None,'snapshot_names':[],'result_exists':False}


def test_fixture_source_deadline_before_mint_requires_exact_original_failure_not_missing_proof():
    from backend.tests.test_application_launch_execution import _assert_source_callback_refusal
    _assert_source_callback_refusal(_fixture_early_refusal(),'deadline',[],'a'*32)


@pytest.mark.parametrize('damage',['error','type','request','result','names','producer','count','unsupported','extra'])
def test_fixture_source_deadline_foreign_or_incomplete_failure_is_not_accepted(damage):
    from backend.tests.test_application_launch_execution import _assert_source_callback_refusal
    evidence=_fixture_early_refusal()
    if damage=='error':evidence['error']='generic execution failed'
    elif damage=='type':evidence['error_type']='ValueError'
    elif damage=='request':evidence['request_id']='b'*32
    elif damage=='result':evidence['result_exists']=True
    elif damage=='names':evidence['snapshot_names']=['foreign']
    elif damage=='producer':evidence['producer']={'phase':'finished','counted':False,'private_acquired':False}
    elif damage=='count':evidence['admission']['active_scopes']=1
    elif damage=='unsupported':evidence['admission']['unsupported']=['foreign']
    else:evidence['foreign']=True
    with pytest.raises(AssertionError):_assert_source_callback_refusal(evidence,'deadline',[],'a'*32)


def test_fixture_source_deadline_after_mint_retains_exact_snapshot_count_and_private_custody(tmp_path):
    from backend.tests.test_application_launch_execution import _assert_source_callback_refusal
    snapshot=tmp_path/('.owned-cpu-'+'a'*32)
    snapshot.mkdir();evidence=_fixture_early_refusal()
    evidence.update(error='Original SOURCE CPU producer deadline expired',producer={'phase':'unresolved','counted':True,'private_acquired':True},snapshot_names=[snapshot.name],admission={'active_scopes':1,'unsupported':['cpu_producer_unconfirmed']})
    _assert_source_callback_refusal(evidence,'deadline',[snapshot],'a'*32)
    for field,value in [('counted',False),('phase','finished'),('private_acquired',False)]:
        original=evidence['producer'][field];evidence['producer'][field]=value
        with pytest.raises(AssertionError):_assert_source_callback_refusal(evidence,'deadline',[snapshot],'a'*32)
        evidence['producer'][field]=original


def _fixture_failure_observer(tmp_path,delegate):
    import ast,types
    from backend.tests import test_application_launch_execution as fixtures
    code=ast.parse(fixtures._source_callback_failure_suffix().replace('\n ','\n').lstrip())
    closure=next(n for n in code.body if isinstance(n,ast.FunctionDef))
    namespace={'original_failure_execute':delegate,'Path':Path,'json':json,'root':tmp_path,
               'relay':types.SimpleNamespace(_CACHE_TICKETS={},_PRODUCERS={}),
               'h':types.SimpleNamespace(_CACHE={'admission':types.SimpleNamespace(snapshot=lambda:{'active_scopes':0,'unsupported':[]})})}
    exec(compile(ast.Module(body=[closure],type_ignores=[]),'<original-source-failure-observer>','exec'),namespace)
    return namespace['failed_source']


@pytest.mark.parametrize('damage',['none','existing-output','hostile-error-string','failed-json-write','missing-cache'])
def test_fixture_failure_diagnostics_delegate_once_and_never_replace_original_exception(tmp_path,monkeypatch,damage):
    from types import SimpleNamespace
    class HostileFailure(RuntimeError):
        def __str__(self):raise OSError('Advisory error string unavailable')
    failure=HostileFailure() if damage=='hostile-error-string' else RuntimeError('Original callback failure')
    calls=[];(tmp_path/'projects').mkdir();output=tmp_path/'projects/source-callback-failure.json'
    if damage=='existing-output':output.write_bytes(b'original retained fact')
    if damage=='failed-json-write':
        def fail(*args,**kwargs):raise OSError('Diagnostic write unavailable')
        monkeypatch.setattr(json,'dump',fail)
    def original(*args,**kwargs):calls.append((args,kwargs));raise failure
    observe=_fixture_failure_observer(tmp_path,original)
    if damage=='missing-cache':observe.__globals__['h']._CACHE={}
    request={'request_id':'a'*32};proof={'original':True}
    with pytest.raises(type(failure)) as caught:observe(request,proof,tmp_path)
    assert caught.value is failure and calls==[((request,proof,tmp_path),{})]
    if damage=='existing-output':assert output.read_bytes()==b'original retained fact'
    if damage=='none':assert json.loads(output.read_bytes())['request_id']==request['request_id']


def test_fixture_failure_diagnostics_success_returns_original_result_without_side_effect(tmp_path):
    calls=[];answer=object();(tmp_path/'projects').mkdir()
    def original(*args,**kwargs):calls.append((args,kwargs));return answer
    observe=_fixture_failure_observer(tmp_path,original)
    assert observe({'request_id':'a'*32},value=True) is answer
    assert len(calls)==1 and list((tmp_path/'projects').iterdir())==[]


@pytest.mark.parametrize('damage',['none','write-error','original-error'])
def test_fixture_first_refusal_observer_preserves_original_recovery_publication(tmp_path,monkeypatch,damage):
    import ast,types
    from backend.tests import test_application_launch_execution as fixtures
    code=ast.parse(fixtures._source_input_first_refusal_script())
    closure=next(n for n in code.body if isinstance(n,ast.FunctionDef));calls=[];answer={'state':'recovery_required'}
    failure=ValueError('Original recovery failed');(tmp_path/'projects').mkdir()
    def original(owner,reason):
        calls.append((owner,reason))
        if damage=='original-error':raise failure
        return answer
    namespace={'original':original,'json':json};exec(compile(ast.Module(body=[closure],type_ignores=[]),'<original-first-refusal-observer>','exec'),namespace)
    if damage=='write-error':
        def fail(*args,**kwargs):raise OSError('Diagnostic write unavailable')
        monkeypatch.setattr(json,'dump',fail)
    owner=types.SimpleNamespace(root=tmp_path)
    if damage=='original-error':
        with pytest.raises(ValueError) as caught:namespace['first'](owner,'exact original reason')
        assert caught.value is failure
    else:assert namespace['first'](owner,'exact original reason') is answer
    assert calls==[(owner,'exact original reason')]


@pytest.mark.parametrize('damage',['changed_nonce','identical_request_replay'])
@pytest.mark.parametrize('foreign',['none','cpu-status','cpu-code','cpu-reason','cpu-pid','cpu-role','backend-status','backend-exit','writer-id','registration','nonce','binding','count','state','closed-hash'])
def test_fixture_damaged_drain_keeps_exact_completed_cpu_and_refuses_foreign_rows(damage,foreign):
    from backend.tests.test_application_launch_quiescence_bridge import _assert_damaged_registry
    from backend.tests.test_service_s6_04 import canonical,sha
    cpu={'nonce':'a'*32,'binding':{'original':True},'backend_process':{'pid':51},'worker_pid':52}
    backend={'role':'backend','status':'active','process':cpu['backend_process'],'exit_code':None,'writer_id':'b'*32,'registration_sha256':'c'*64}
    worker={'role':'owned_cpu_worker','status':'direct_exited','process':{'pid':52},'exit_code':0,'reason_code':None}
    registry={'state':'open' if damage=='changed_nonce' else 'closed','binding':{'nonce':cpu['nonce'],'launch_binding_sha256':sha(canonical(cpu['binding']))},'writers':[backend,worker]}
    refusal={'writer_drain':{'writer_id':backend['writer_id'],'registration_sha256':backend['registration_sha256'],'request':{'closed_registry_sha256':'d'*64}}}
    if foreign=='cpu-status':worker['status']='unsupported'
    elif foreign=='cpu-code':worker['exit_code']=3
    elif foreign=='cpu-reason':worker['reason_code']='unresolved'
    elif foreign=='cpu-pid':worker['process']['pid']=99
    elif foreign=='cpu-role':worker['role']='foreign'
    elif foreign=='backend-status':backend['status']='direct_exited'
    elif foreign=='backend-exit':backend['exit_code']=0
    elif foreign=='writer-id':backend['writer_id']='f'*32
    elif foreign=='registration':backend['registration_sha256']='f'*64
    elif foreign=='nonce':registry['binding']['nonce']='f'*32
    elif foreign=='binding':registry['binding']['launch_binding_sha256']='f'*64
    elif foreign=='count':registry['writers'].append(dict(worker))
    elif foreign=='state':registry['state']='foreign'
    elif foreign=='closed-hash':
        if damage=='changed_nonce':registry['binding']['launch_binding_sha256']='f'*64
        else:refusal['writer_drain']['request']['closed_registry_sha256']='f'*64
    if foreign=='none':_assert_damaged_registry(registry,damage,refusal,cpu,'d'*64)
    else:
        with pytest.raises((AssertionError,StopIteration)):_assert_damaged_registry(registry,damage,refusal,cpu,'d'*64)

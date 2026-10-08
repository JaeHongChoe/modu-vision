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
    child,ack=fixtures.start_cpu_stack(values)
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
    final_writers=inspect_epoch(root,ack['nonce'])['registry']['writers']
    assert [(row['role'],row['status'],row['reason_code']) for row in final_writers]==[
        ('backend','direct_exited',None),('owned_cpu_worker','direct_exited',None)]
    with pytest.raises(ValueError,match='launch ownership'):
        lease.assert_quiescent(root)
    assert child.returncode is not None
    post={'schema':'modu-vision.original-source-cpu-post-finish-fixture/v1',
          'writer_registry':inspect_epoch(root,ack['nonce']), 'lease_state':final,
          'original_retained_controller_pid':child.pid,'original_retained_controller_returncode':child.returncode,
          'controlled_source_fixture':True,'component_only':True,'whole_writer_coverage':False,
          'process_tree_exit_verified':False,'can_release_launch_lease':False,'release_ready':False}
    with (root/'projects/actual-source-cpu-post-finish-proof.json').open('x') as output:json.dump(post,output,indent=2)

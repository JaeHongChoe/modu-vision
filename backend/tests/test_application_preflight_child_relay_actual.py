"""Serialized actual original source relay/preflight math; no native/tree/release claim."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from backend.engine import application_launch_handshake as h
from backend.tests.test_application_cpu_writer_lifetime import wait_file

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Original POSIX private relay')


def instrument_original_bridge_refusal(raw):
    """Private fixture observation only; no changed frames, authority or budgets."""
    text=raw.decode()
    start='        const invalidate = () => {'
    assert text.count(start)==1
    text=text.replace(start, """        let originalRelayDiagnostic = null;
        const invalidate = (cause) => {
            if (!failed) {
                try { require('node:fs').writeFileSync(require('node:path').join(this.projects,'original-node-refusal.json'),
                    JSON.stringify({scope:'private_original_fixture_diagnostic',reason:String(cause?.stack||cause||'original endpoint closed'),
                        observation:originalRelayDiagnostic,main_pid:process.pid,backend_pid:proc.pid,authority:false}),{flag:'wx',mode:384}); }
                catch (diagnosticError) { /* Observation failure must never interrupt original invalidation. */ }
            }""")
    observation='                    fields(request, [\'schema_version\', \'kind\', \'action\', \'nonce\', \'epoch\', \'binding_sha256\', \'backend_claim_sha256\', \'request_id\', \'payload\']);'
    assert text.count(observation)==1
    text=text.replace(observation,"""                    originalRelayDiagnostic={kind:request.kind,action:request.action,raw_sha256:hash(raw),
                        original_request_utf8:raw.toString('utf8'),node_hrtime_ms:Number(process.hrtime.bigint())/1e6,node_performance_ms:performance.now(),
                        plan_deadline_monotonic:request.payload?.deadline_monotonic,budget_ms:request.payload?.budget_ms};
"""+observation)
    return text.encode()


def read_original_fixture_lease(root, ack, binding, deadline):
    """Same original fixture; read-only transient within its first five seconds."""
    from backend.engine import application_launch_lease as lease
    while time.monotonic()<deadline:
        try: row=lease._load(root)
        except lease.LaunchLeaseError as exc:
            if str(exc)!='Application launch ownership requires recovery: ownership publication interrupted or changed':raise
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            time.sleep(min(.01,remaining));continue
        if row is not None:
            assert row['nonce']==ack['nonce']
            if binding is not None:assert row['binding']==binding
        if time.monotonic()>=deadline:break
        return row
    raise h.HandshakeError('Original fixture cleanup observation deadline expired; retain original handle')


def wait_original_fixture_stop(root, ack, binding, deadline, *, original_backend=None):
    """Observe either original stop or durable main exit in the same first bound."""
    ordinary=root/'projects/ordinary-stop-requested.json'
    while time.monotonic()<deadline:
        row=read_original_fixture_lease(root,ack,binding,deadline)
        if row is not None and row.get('exit_observation',{}):
            assert row['exit_observation']['direct_child_pid']==row['process']['pid']
            assert row['exit_observation']['process_tree_exit_verified'] is False
            if time.monotonic()>=deadline:break
            return 'exit',row
        if ordinary.exists():
            stopped=json.loads(ordinary.read_text())
            # The original fixture writes this observation inside backend,
            # whose already-validated bootstrap identity is distinct from
            # main. The file itself grants no process/adoption authority.
            from backend.engine import application_launch_lease as lease
            assert row is not None and lease._identity_shape(original_backend)
            assert original_backend['pid']!=row['process']['pid']
            assert stopped=={'pid':original_backend['pid'],'requested':True}
            if time.monotonic()>=deadline:break
            return 'ordinary',row
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        time.sleep(min(.01,remaining))
    raise h.HandshakeError('Original fixture cleanup stop/exit observation expired; retain original handle')


def test_actual_original_preflight_child_is_enrolled_bound_and_finalized(tmp_path,monkeypatch):
    """Original source controller/backend descriptors plus real preflight CPU.

    Signed source-script trust is controlled. This is not packaged/native,
    publisher, full-writer/tree, accelerator, or human-quality acceptance.
    """
    import select
    import urllib.request
    import zipfile
    from backend.tests import test_application_launch_execution as fixtures
    from backend.tests.test_application_launch_controller import controller_arguments
    from backend.tests.test_application_launch_quiescence_bridge import managed_stack
    from backend.tests.test_staged_update_canary import controlled_proof
    from backend.tests.test_service_s6_04 import canonical,sha
    from backend.engine import application_launch_lease as lease,application_launch_quiescence as q
    controlled_proof(monkeypatch)
    original_fixture=fixtures.fixture
    instrumentation='''
 os.environ.update(CUDA_VISIBLE_DEVICES='',NVIDIA_VISIBLE_DEVICES='none',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
 from backend.api import routes_workers as rw
 from backend.engine import worker_preflight as wp
 import subprocess,psutil
 app.include_router(rw.router)
 original_spawn=subprocess.Popen
 def observed_spawn(command,*args,**kwargs):
  child=original_spawn(command,*args,**kwargs)
  if len(command)>2 and command[1:3]==['-m','backend.engine.worker_preflight']:
   passed=kwargs.get('pass_fds',());info=os.fstat(passed[0]);writer=h._CACHE['challenge']['writer']
   lock=root/'.application-writer-epochs'/os.environ['VISION_APPLICATION_LAUNCH_NONCE']/'writers'/writer['writer_id']/'ownership.lock'
   refs=[{'fd':entry.fd,'path':entry.path} for entry in psutil.Process(child.pid).open_files() if entry.path==str(lock)]
   (projects/'preflight-original-child.json').write_text(json.dumps({'pid':child.pid,'birth':psutil.Process(child.pid).create_time(),
    'command':command,'device':info.st_dev,'inode':info.st_ino,'inherited_refs':refs,'passed_count':len(passed),'all_passed_refs':[{'fd':fd,'device':os.fstat(fd).st_dev,'inode':os.fstat(fd).st_ino} for fd in passed],
    'active_scopes':state.snapshot()['active_scopes'],'CUDA_VISIBLE_DEVICES':kwargs['env'].get('CUDA_VISIBLE_DEVICES'),
    'NVIDIA_VISIBLE_DEVICES':kwargs['env'].get('NVIDIA_VISIBLE_DEVICES'),'close_fds':kwargs.get('close_fds')}))
  return child
 subprocess.Popen=observed_spawn
 original_record=wp.PreflightStore.record
 def observed_record(self,*args,**kwargs):
  value=original_record(self,*args,**kwargs)
  with (projects/'preflight-record-counts.jsonl').open('a') as out:out.write(json.dumps({'stage':args[3],'active_scopes':state.snapshot()['active_scopes']})+'\\n')
  return value
 wp.PreflightStore.record=observed_record
'''
    def fixture(folder):
        value=original_fixture(folder);sign=value['sign']
        def instrumented(payload):
            archive=value['directory']/'application.zip'
            with zipfile.ZipFile(archive) as reader:files={name:reader.read(name) for name in reader.namelist()}
            manifest=json.loads(files.pop('portable-application.json'));needle=b' app=FastAPI()\n'
            files['bridge.cjs']=instrument_original_bridge_refusal(files['bridge.cjs'])
            assert files['bin/backend_fixture.py'].count(needle)==1
            files['bin/backend_fixture.py']=files['bin/backend_fixture.py'].replace(needle,needle+instrumentation.encode())
            manifest['files']=[{'path':name,'size':len(raw),'sha256':sha(raw),'executable':name.startswith('bin/')} for name,raw in files.items()]
            with zipfile.ZipFile(archive,'w') as writer:
                writer.writestr('portable-application.json',canonical(manifest))
                for name,raw in files.items():writer.writestr(name,raw)
            payload.update(sha256=sha(archive.read_bytes()),size=archive.stat().st_size)
            payload['artifacts'][0].update(sha256=payload['sha256'],size=payload['size'])
            return sign(payload)
        value['sign']=instrumented;return value
    monkeypatch.setattr(fixtures,'fixture',fixture)
    values=managed_stack(tmp_path,monkeypatch);root,value,current,project,reviewed,pin=values
    protected={str(path):sha(path.read_bytes()) for path in (root/'application-active.json',root/'global-active.json',project/'project.json',project/reviewed['input_path'],project/reviewed['package_path']/'manifest.json')}
    # No known-image flags: this exact admitted epoch executes only preflight.
    child=subprocess.Popen([sys.executable,'-m','backend.engine.application_launch_controller',*controller_arguments(root,value,current)],
        cwd=Path(__file__).resolve().parents[2],stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    assert select.select([child.stdout],[],[],10)[0],'Original controller acknowledgement missing'
    raw=child.stdout.readline(65537);assert raw,'Original controller refused source preflight fixture'
    ack=json.loads(raw);child.stdout.close();child.stderr.close();assert ack['status']=='starting'
    original_binding=None;original_backend=None
    try:
        # 'starting' acknowledges the original main spawn, not ASGI readiness.
        # Observe cold source startup within the unchanged controller bootstrap
        # bound, rather than borrowing the post-ready five-second leaf helper.
        server=wait_file(root/'projects/managed-server.json',seconds=h.MAX_DEADLINE);base=f"http://127.0.0.1:{server['port']}"
        bootstrap=wait_file(root/'.application-launches'/ack['nonce']/'bootstrap-receipt.json')
        assert bootstrap['nonce']==ack['nonce'] and bootstrap['backend_process']['pid']==server['pid']
        original_binding=bootstrap['binding']
        original_backend=bootstrap['backend_process']
        request=urllib.request.Request(base+'/api/workers/local/preflight',data=json.dumps({'task':'classification','device':'cpu'}).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,timeout=10) as response:assert response.status==202;started=json.load(response)
        until=time.monotonic()+90
        while time.monotonic()<until:
            with urllib.request.urlopen(base+'/api/workers',timeout=10) as response:status=json.load(response)
            if status['running_preflight'] is None:break
            time.sleep(.05)
        assert status['running_preflight'] is None,'Original preflight did not finish its existing production budget'
        last=status['last_preflight'];assert last['error'] is None,last
        assert set(last['results'])=={'train','evaluate','infer','export'}
        assert all(row['passed'] and row['evidence']['device']=='cpu' for row in last['results'].values()),last
        observation=wait_file(root/'projects/preflight-original-child.json')
        records=[json.loads(raw) for raw in (root/'projects/preflight-record-counts.jsonl').read_text().splitlines()]
        assert {row['stage'] for row in records}=={'train','evaluate','infer','export'} and all(row['active_scopes']>=1 for row in records)
        registry=q.inspect_epoch(root,ack['nonce']);backend=registry['registry']['writers'][0]
        assert len(registry['registry']['writers'])==2 and backend['role']=='backend' and backend['status']=='active'
        preflight=registry['registry']['writers'][1]
        assert preflight['role']=='preflight' and preflight['status']=='direct_exited' and preflight['exit_code']==0
        assert preflight['process']['pid']==observation['pid'] and preflight['process']['created_at']==observation['birth']
        assert preflight['process']['pid'] not in (backend['process']['pid'],bootstrap['main_process']['pid'])
        assert backend['process']==bootstrap['backend_process']
        assert backend['process']['pid']==server['pid']!=bootstrap['main_process']['pid']
        assert backend['lock_identity']=={'device':observation['device'],'inode':observation['inode']}
        assert observation['passed_count']==3 and len(observation['inherited_refs'])==1 and observation['close_fds'] is True
        assert {'device':observation['all_passed_refs'][1]['device'],'inode':observation['all_passed_refs'][1]['inode']}==preflight['lock_identity']
        assert observation['active_scopes']>=1 and observation['CUDA_VISIBLE_DEVICES']=='' and observation['NVIDIA_VISIBLE_DEVICES']=='none'
        assert started['device']=='cpu' and status['workers'][0]['local_compute_busy'] is None
        assert protected=={name:sha(Path(name).read_bytes()) for name in protected}
        (root/'projects/actual-preflight-writer-proof.json').write_bytes(canonical({'scope':'controlled_signed_source_application_epoch',
            'controller':ack,'backend':server,'preflight':last,'original_child':observation,'record_lifetimes':records,
            'registry':registry,'protected':protected,'actual_preflight_enrollment_verified':True,'actual_preflight_original_popen_finalize_verified':True,'actual_preflight_cpu_stages_verified':True,
            'actual_native_application_verified':False,'whole_writer_coverage':False,'process_tree_exit_verified':False,
            'lease_release':False,'model_quality_approved':False,'real_publisher_verified':False,'gpu_execution_verified':False}))
    finally:
        # Only this original test-owned stack's private cooperative controls.
        # Keep the existing 5 / 7 / 5 second cleanup observation budgets. A
        # two-file publication interval refuses strict readers transiently;
        # retry that exact observation, never edit either record or reopen PID
        # authority. Every other read failure retains the original controller.
        if child.returncode is None:
            # A protocol refusal can reap main/backend before cooperative stop
            # files are possible. Read only the original durable exit record;
            # the exact still-retained controller Popen may then be reaped.
            # This grants no PID lookup, descendant signal or cleanup repair.
            first_cleanup_deadline=time.monotonic()+5
            prior=read_original_fixture_lease(root,ack,original_binding,first_cleanup_deadline)
            if prior is not None and prior.get('exit_observation',{}):
                assert prior['nonce']==ack['nonce']
                if original_binding is not None:assert prior['binding']==original_binding
                assert prior['exit_observation']['direct_child_pid']==prior['process']['pid']
                assert prior['exit_observation']['process_tree_exit_verified'] is False
                child.terminate();child.wait(timeout=5)
            else:
                projects=root/'projects';(projects/'drain.trigger').touch()
                kind,observed=wait_original_fixture_stop(root,ack,original_binding,first_cleanup_deadline,original_backend=original_backend)
                if kind=='exit':
                    child.terminate();child.wait(timeout=5)
                else:
                    (projects/'exit.trigger').touch()
                    wait_file(projects/'managed-stop-result.json',seconds=7)
            until=time.monotonic()+5
            while time.monotonic()<until:
                try: final=lease._load(root)
                except lease.LaunchLeaseError as exc:
                    if str(exc) != 'Application launch ownership requires recovery: ownership publication interrupted or changed':raise
                    time.sleep(.01);continue
                assert final['nonce']==ack['nonce']
                if original_binding is not None:assert final['binding']==original_binding
                assert final['binding']['installation_id']==ack['installation_id']
                assert final['binding']['update_id']==ack['update_id']
                assert final['binding']['database_pointer']['fence']==ack['database_fence']
                if final.get('exit_observation',{}):
                    assert final['exit_observation']['direct_child_pid']==final['process']['pid']
                    assert final['exit_observation']['process_tree_exit_verified'] is False
                    if child.returncode is None:child.terminate();child.wait(timeout=5)
                    break
                time.sleep(.05)
            else:pytest.fail('Original controller did not record retained main exit; retain this fixture')
    final=lease._load(root);assert final['state']=='recovery_required' and final['writer_drain']['phase']=='refused'
    assert final['writer_drain']['receipt']['whole_writer_coverage'] is False
    assert final['writer_drain']['receipt']['can_release_launch_lease'] is False
    with pytest.raises(ValueError):lease.assert_quiescent(root)

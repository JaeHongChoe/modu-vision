"""Fixed CPU writer transport, not complete-tree or lease-release evidence.

The kernel cases use real owned temporary processes. Controlled cache/body
fixtures qualify admission ordering only; real authenticated OCR is separate.
"""
from contextlib import contextmanager
import inspect
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from backend.engine import application_launch_execution as execution
from backend.engine import application_launch_handshake as handshake
from backend.engine import runtime_deadline as deadline
from backend.tests.test_application_launch_quiescence import epoch, enroll


REPOSITORY = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX writer-OFD transport only')


def call_with_transport(command, fd):
    # The unchanged baseline has no keyword. Execute the same real child there
    # to reproduce descriptor loss, instead of treating TypeError as that proof.
    args = {'pass_fds': (fd,)} if 'pass_fds' in inspect.signature(deadline.execute_owned_process).parameters else {}
    return deadline.execute_owned_process(command, deadline_ms=5000, **args)


def exclusive_available(path):
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return False
        return True
    finally: os.close(fd)  # No explicit unlock of an inherited OFD.


def wait_file(path, seconds=5):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        if path.is_file(): return json.loads(path.read_bytes())
        time.sleep(.01)
    pytest.fail('Missing owned cooperative fixture publication: ' + str(path))


FIND_LOCK = '''
import os,sys,stat,json
device,inode=map(int,sys.argv[1:3]);matches=[]
for fd in range(3,256):
 try:
  info=os.fstat(fd)
  if stat.S_ISREG(info.st_mode) and (info.st_dev,info.st_ino)==(device,inode):matches.append(fd)
 except OSError:pass
if len(matches)!=1:
 print(json.dumps({'inherited_writer_refs':len(matches)}),flush=True);sys.exit(7)
writer_fd=matches[0]
'''


def test_actual_helper_inherits_writer_inode_and_closes_its_private_transport(epoch):
    q, root, owner, authority = epoch; registration = enroll(authority, 'backend')
    lock = root/q.EPOCHS/owner.nonce/'writers'/registration.writer_id/'ownership.lock'
    with q.writer_guard(root, owner.nonce, registration.writer_id,
                        expected_registration_sha256=registration.registration_sha256) as guard:
        info = os.fstat(guard.pass_fds[0])
        command = [sys.executable, '-I', '-B', '-c', FIND_LOCK +
                   "print(json.dumps({'device':device,'inode':inode,'inherited_writer_refs':1}),flush=True)",
                   str(info.st_dev), str(info.st_ino)]
        outcome = call_with_transport(command, guard.pass_fds[0])
        assert outcome['status'] == 'completed' and outcome['returncode'] == 0, outcome
        assert json.loads(outcome['stdout']) == {'device': info.st_dev, 'inode': info.st_ino, 'inherited_writer_refs': 1}
        assert exclusive_available(lock) is False
    assert exclusive_available(lock), 'Helper retained an undisclosed parent duplicate after return'
    assert authority.snapshot()['registry']['writers'][0]['status'] == 'reserved'
    (root/'kernel-helper-proof.json').write_text(json.dumps({'outcome': outcome,
        'original_writer_inode': info.st_ino, 'original_writer_device': info.st_dev,
        'exclusive_blocked_during_guard': True, 'exclusive_available_after_guard': True,
        'whole_writer_coverage': False, 'process_tree_exit_verified': False, 'lease_release': False}))


def test_escaped_cooperative_descendant_keeps_kernel_fence_after_original_fixture_exit(epoch, tmp_path):
    q, root, owner, authority = epoch; registration = enroll(authority, 'backend')
    lock = root/q.EPOCHS/owner.nonce/'writers'/registration.writer_id/'ownership.lock'
    ready, release, exited = (tmp_path/name for name in ('descendant-ready.json', 'release.trigger', 'descendant-exited.json'))
    leaf = FIND_LOCK + '''
from pathlib import Path
import time
ready,release,exited=map(Path,sys.argv[3:6]);ready.write_text(json.dumps({'pid':os.getpid(),'device':device,'inode':inode,'session':os.getsid(0)}))
limit=time.monotonic()+20
while not release.exists() and time.monotonic()<limit:time.sleep(.01)
os.close(writer_fd)
exited.write_text(json.dumps({'cooperative_release':release.exists(),'pid':os.getpid()}))
'''
    worker = FIND_LOCK + '''
import subprocess,time
from pathlib import Path
leaf,ready,release,exited=sys.argv[3:7]
child=subprocess.Popen([sys.executable,'-I','-B','-c',leaf,str(device),str(inode),ready,release,exited],
 pass_fds=(writer_fd,),start_new_session=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
limit=time.monotonic()+5
while not Path(ready).exists() and time.monotonic()<limit:time.sleep(.01)
assert Path(ready).exists()
print(json.dumps({'original_worker':os.getpid(),'escaped_child':child.pid,'writer_inode':inode}),flush=True)
'''
    driver = '''import sys,os,json,inspect
sys.path.insert(0,sys.argv[1])
from backend.engine.application_launch_quiescence import writer_guard
from backend.engine.runtime_deadline import execute_owned_process
from pathlib import Path
root,nonce,writer,pin,worker,leaf,ready,release,exited=sys.argv[2:11]
with writer_guard(root,nonce,writer,expected_registration_sha256=pin) as guard:
 info=os.fstat(guard.pass_fds[0])
 args={'pass_fds':guard.pass_fds} if 'pass_fds' in inspect.signature(execute_owned_process).parameters else {}
 outcome=execute_owned_process([sys.executable,'-I','-B','-c',worker,str(info.st_dev),str(info.st_ino),leaf,ready,release,exited],deadline_ms=5000,**args)
 print(json.dumps(outcome),flush=True)
'''
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', driver, str(REPOSITORY), str(root), owner.nonce,
                              registration.writer_id, registration.registration_sha256, worker, leaf,
                              str(ready), str(release), str(exited)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr.decode()
        outcome = json.loads(stdout)
        assert outcome.get('leader_returncode', outcome.get('returncode')) == 0, outcome
        published = wait_file(ready)
        assert published['inode'] == lock.stat().st_ino
        # Original fixture Popen exited and closed its refs. Only the escaped
        # cooperative descendant's inherited OFD blocks this real EX probe.
        assert exclusive_available(lock) is False
        assert authority.snapshot()['registry']['writers'][0]['status'] == 'reserved'
    finally:
        release.touch()  # Owned cooperative protocol only; no PID/group signal.
        if child.poll() is None: child.communicate(timeout=10)
    final = wait_file(exited)
    assert final['cooperative_release'] is True
    until = time.monotonic() + 5
    while not exclusive_available(lock) and time.monotonic() < until: time.sleep(.01)
    assert exclusive_available(lock)
    (tmp_path/'escaped-kernel-proof.json').write_text(json.dumps({'fixture_original_exit_code': child.returncode,
        'worker_outcome': outcome, 'descendant': published, 'cooperative_exit': final,
        'exclusive_blocked_after_original_exit': True, 'exclusive_available_after_cooperative_release': True,
        'whole_writer_coverage': False, 'process_tree_exit_verified': False, 'lease_release': False}))
    authority.close_epoch(expected_registry_sha256=authority.snapshot()['registry_sha256'])
    with pytest.raises(q.QuiescenceError, match='reserved|unresolved'):
        with authority.enrolled_writer_fence(expected_registry_sha256=authority.snapshot()['registry_sha256']):
            pytest.fail('A released kernel lock invented original child or whole-tree authority')


@contextmanager
def controlled_cache(epoch, monkeypatch):
    """A real registry/OFD with modeled authentication; never app execution."""
    q, root, owner, authority = epoch; registration = enroll(authority, 'backend')
    with q.writer_guard(root, owner.nonce, registration.writer_id,
                        expected_registration_sha256=registration.registration_sha256) as guard:
        private = os.dup(guard.pass_fds[0]); info = os.fstat(private)
        state = handshake.BackendWorkAdmission(); proof = {'nonce': owner.nonce}; context = ('controlled-original-context',)
        cache = {'context': context, 'ready': True, 'root': root, 'proof': proof,
                 'challenge': {'writer': {'writer_id': registration.writer_id, 'registration_sha256': registration.registration_sha256}},
                 'writer_guard': guard, 'writer_handle': guard, 'writer_private_fd': private,
                 'writer_fd_identity': (info.st_dev, info.st_ino), 'admission': state}
        monkeypatch.setattr(handshake, '_CACHE', cache)
        monkeypatch.setattr(handshake, '_root_context', lambda: (root, {}))
        monkeypatch.setattr(handshake, '_context', lambda: context)
        def valid(*_, validated, absolute_deadline=None, before_acquire=None):
            # Explicit bounded-entry model, not original owner authentication.
            if absolute_deadline is not None:
                assert type(absolute_deadline) in (int, float) and math.isfinite(absolute_deadline)
                assert time.monotonic() < absolute_deadline
            if before_acquire is not None:
                assert callable(before_acquire)
                before_acquire()
            if absolute_deadline is not None: assert time.monotonic() < absolute_deadline
            validated['protocol_version'] = 4
            return proof
        monkeypatch.setattr(handshake, '_validate', valid)
        try: yield root, authority, state, cache
        finally: os.close(private)


@pytest.mark.parametrize('damage', ['closed_epoch', 'closed_admission', 'partial_guard', 'changed_context'])
def test_fixed_cpu_refuses_before_validation_snapshot_or_home_writes(epoch, monkeypatch, damage):
    with controlled_cache(epoch, monkeypatch) as (root, authority, state, cache):
        if damage == 'closed_epoch': authority.close_epoch(expected_registry_sha256=authority.snapshot()['registry_sha256'])
        if damage == 'closed_admission': state.close()
        if damage == 'partial_guard': cache['writer_private_fd'] = None
        if damage == 'changed_context': monkeypatch.setattr(handshake, '_context', lambda: ('foreign-context',))
        touched = []
        def before(*_): touched.append('validation'); raise AssertionError('CPU body reached before refused writer admission')
        monkeypatch.setattr(execution, 'validate_request', before)
        with pytest.raises(ValueError, match='closed|partial|unavailable'):
            execution.execute_backend({}, {'frozen': False}, root)
        assert touched == []
        assert state.snapshot()['active_scopes'] == 0


@pytest.mark.parametrize('status', ['timeout', 'cancelled', 'uncertain', 'nonzero'])
def test_returned_unsuccessful_cpu_outcome_is_sticky_before_admission_leaves(epoch, tmp_path, monkeypatch, status):
    with controlled_cache(epoch, monkeypatch) as (root, authority, state, cache):
        project = tmp_path/'controlled-body'; project.mkdir()
        capability = {'project_path': str(project), 'plan': {'deadline_ms': 30000}}
        frame = {'nonce': cache['proof']['nonce'], 'workspace_id': 'fixture', 'project_id': 'fixture',
                 'plan_sha256': 'a'*64, 'request_id': 'b'*32}
        monkeypatch.setattr(execution, 'validate_request', lambda *_: {'capability': capability})
        monkeypatch.setattr(execution, '_live_origin', lambda *_: None)
        original_read = execution._read
        monkeypatch.setattr(execution, '_read', lambda p,*a,**kw: b'{}' if Path(p).name=='bootstrap-receipt.json' else original_read(p,*a,**kw))
        monkeypatch.setattr(execution, 'admit_plan', lambda *_,**__: capability)
        def observed(*_, **kwargs):
            assert state.snapshot()['active_scopes'] == 1
            assert len(kwargs.get('pass_fds', ())) == 1
            assert os.fstat(kwargs['pass_fds'][0]).st_ino == cache['writer_fd_identity'][1]
            return {'status': 'completed' if status=='nonzero' else status, 'returncode': 1}
        monkeypatch.setattr(deadline, 'execute_owned_process', observed)
        # Explicit transport-only legacy body model: this incomplete cache
        # grants no authenticated SOURCE producer/queue/enrollment authority.
        with pytest.raises(execution.ExecutionError, match='failed|timed out'):
            with handshake.owned_cpu_writer_scope() as writer_pass_fds:
                execution._execute_backend_admitted(frame, {'frozen': False}, root, writer_pass_fds)
        assert state.snapshot() == {'active_scopes': 0, 'unsupported': ['cpu_producer_unconfirmed']}


@pytest.mark.parametrize('bad', [None, [], (True,), (0,), (1,), (2,), (3, 3)])
def test_bad_transport_shape_refuses_before_process_creation(monkeypatch, bad):
    monkeypatch.setattr(deadline.subprocess, 'Popen', lambda *a,**k: pytest.fail('Invalid writer transport spawned a process'))
    with pytest.raises(ValueError, match='transport|descriptor'):
        deadline.execute_owned_process([sys.executable, '-c', 'pass'], deadline_ms=1000, pass_fds=bad)


def test_non_writer_descriptor_refuses_before_process_creation(tmp_path, monkeypatch):
    path = tmp_path/'not-private-writer'; path.write_bytes(b'content')
    fd = os.open(path, os.O_RDWR)
    monkeypatch.setattr(deadline.subprocess, 'Popen', lambda *a,**k: pytest.fail('Invalid writer transport spawned a process'))
    try:
        with pytest.raises(ValueError, match='transport|descriptor'):
            deadline.execute_owned_process([sys.executable, '-c', 'pass'], deadline_ms=1000, pass_fds=(fd,))
    finally: os.close(fd)


def test_actual_original_cpu_epoch_inherits_writer_inode_and_keeps_scope_through_output(tmp_path, monkeypatch):
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
    'child_writer_open_refs':refs,'active_scopes':state.snapshot()['active_scopes'],'passed_count':len(passed),
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
        assert wait_file(root/'projects/actual-cpu-scope-returned.json')=={'active_scopes':0,'unsupported':[]}
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


def test_incomplete_controlled_cache_never_grants_authenticated_source_cpu(epoch,monkeypatch):
    """Transport-only legacy controls cannot silently become a SOURCE adapter."""
    with controlled_cache(epoch,monkeypatch) as (root,authority,state,cache):
        touched=[]
        def unsafe(*args,**kwargs):
            touched.append('source_body');pytest.fail('Incomplete modeled cache minted SOURCE authority')
        monkeypatch.setattr(execution,'_execute_backend_admitted',unsafe)
        with pytest.raises(handshake.HandshakeError,match='SOURCE CPU producer admission is unavailable'):
            execution.execute_backend({}, {'frozen':False},root)
        assert touched==[] and state.snapshot()=={'active_scopes':0,'unsupported':[]}
        rows=authority.snapshot()['registry']['writers']
        assert len(rows)==1 and rows[0]['role']=='backend' and rows[0]['status']=='reserved'

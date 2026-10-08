"""Original SOURCE known-image CPU lifetime, separate from preflight authority.

Wire requests and returned PIDs are data. A canonical, create-only in-memory
producer holds the backend's original OFD/count through output and final ACK.
Its source implementation connects the original execution callback, sole reader
and Node/controller path; actual qualification is separate. Direct child
qualification is never process-tree or launch-lease release.
"""
import copy
import hashlib
import math
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
import weakref
from contextlib import contextmanager

from backend.engine.application_launch_handshake import HandshakeError

_MINTS=set()
_PRODUCERS=weakref.WeakKeyDictionary()
_CONTROLLERS=weakref.WeakKeyDictionary()
_ACTIVE=set()
_UNRESOLVED=set()
_CACHE_TICKETS={}
_CPU_EXCHANGES=weakref.WeakKeyDictionary()
_CPU_QUEUE_CACHES={}
_ORIGINAL_POPEN_TYPE=subprocess.Popen


class BackendCpuProducer:
    __slots__=('__weakref__',)
    def __init__(self,*args,_mint=None,**kwargs):
        if args or kwargs or _mint not in _MINTS:
            raise HandshakeError('Original create-only SOURCE CPU producer is unavailable')
    def __copy__(self):raise HandshakeError('Original CPU producer cannot be copied')
    def __deepcopy__(self,memo):raise HandshakeError('Original CPU producer cannot be copied')
    def __reduce_ex__(self,protocol):raise HandshakeError('Original CPU producer cannot be serialized')


class ControllerCpuRelay(BackendCpuProducer):
    __slots__=()


class _CpuExchange(BackendCpuProducer):
    __slots__=()


def _new_exchange():
    mint=object();_MINTS.add(mint)
    try:return _CpuExchange(_mint=mint)
    finally:_MINTS.discard(mint)


def _new_controller():
    mint=object();_MINTS.add(mint)
    try:return ControllerCpuRelay(_mint=mint)
    finally:_MINTS.discard(mint)


def _new_producer():
    mint=object();_MINTS.add(mint)
    try:return BackendCpuProducer(_mint=mint)
    finally:_MINTS.discard(mint)


def _canonical(value):
    from backend.engine import application_launch_handshake as h
    return h._canonical(value)


def _endpoint(channel):
    if (type(channel) is not socket.socket or channel.family!=socket.AF_UNIX
            or channel.type!=socket.SOCK_STREAM or channel.get_inheritable()):
        raise HandshakeError('Original CPU private channel differs')
    try:info=os.fstat(channel.fileno())
    except OSError as exc:raise HandshakeError('Original CPU private channel is unavailable') from exc
    if not stat.S_ISSOCK(info.st_mode):raise HandshakeError('Original CPU private endpoint differs')
    return channel.fileno(),info.st_dev,info.st_ino,stat.S_IFMT(info.st_mode),info.st_uid


def _fd_identity(fd):
    if type(fd) is not int or fd<0:raise HandshakeError('Original CPU writer descriptor is unavailable')
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_size!=0 or info.st_nlink!=1
                or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600
                or os.get_inheritable(fd)):
            raise HandshakeError('Original CPU private writer descriptor differs')
    except OSError as exc:raise HandshakeError('Original CPU private writer descriptor is unavailable') from exc
    return info.st_dev,info.st_ino


def _retain(capability):
    if type(capability) is ControllerCpuRelay:
        state=_CONTROLLERS.get(capability)
        if state is not None:
            state['failed']=True;_UNRESOLVED.add(capability)
        return
    state=_PRODUCERS.get(capability) if type(capability) is BackendCpuProducer else None
    if state is None:return
    state['phase']='unresolved';_UNRESOLVED.add(capability)
    if state['counted'] or state['private'] is not None:
        state['admission'].uncovered('cpu_producer_unconfirmed')


def _deadline(state):
    limit=min(state['deadline'],state['execution_deadline'])
    # Future CPU queue closure will clamp this to the original admitted drain.
    queue=state['cache'].get('source_cpu_queue')
    if queue is not state['cpu_queue'] or queue is not None and type(queue) is not CpuRelayQueue:
        raise HandshakeError('Original SOURCE CPU queue changed')
    if queue is not None and queue._closed_deadline is not None:
        limit=min(limit,queue._closed_deadline)
    if time.monotonic()>=limit:raise HandshakeError('Original SOURCE CPU producer deadline expired')
    return limit


def _current_source_producer(capability):
    from backend.engine import application_launch_handshake as h
    state=_PRODUCERS.get(capability) if type(capability) is BackendCpuProducer else None
    if (state is None or capability not in _ACTIVE or state['phase']=='unresolved'
            or state['pid']!=os.getpid() or state['thread'] is not threading.current_thread()):
        raise HandshakeError('Original SOURCE CPU process/thread producer is unavailable')
    if (capability not in _ACTIVE or state['phase']=='unresolved' or state['pid']!=os.getpid()
            or state['thread'] is not threading.current_thread()):
        raise HandshakeError('Original SOURCE CPU process/thread producer changed')
    _deadline(state);cache=h._CACHE
    if (cache is not state['cache'] or h._context()!=state['context'] or not cache['ready']
            or cache['proof']!=state['proof'] or cache['socket'] is not state['socket']
            or _endpoint(cache['socket'])!=state['endpoint']
            or h._root_context()!=state['root_context']
            or cache['challenge'] is not state['challenge']
            or _canonical(cache['challenge'])!=state['challenge_bytes']
            or cache['admission'] is not state['admission']
            or cache.get('original_cpu_execution') is not state['execution']
            or state['execution'].get('thread') is not state['thread']
            or state['execution'].get('done') is not state['done']
            or state['done'].is_set()
            or state['execution'].get('deadline')!=state['execution_deadline']
            or cache['writer_private_fd']!=state['anchor']
            or cache['writer_fd_identity']!=state['anchor_identity']
            or _fd_identity(state['anchor'])!=state['anchor_identity']
            or (not state.get('private_closed',False) and _fd_identity(state['private'])!=state['anchor_identity'])
            or state.get('private_closed',False) and state['phase']!='closing'):
        raise HandshakeError('Original SOURCE CPU cache/channel/OFD changed')
    if state.get('snapshot_fd') is not None and not state.get('snapshot_closed',False):_snapshot_handle_current(state)
    if state.get('snapshot_closed',False) and state['phase']!='closing':
        raise HandshakeError('Original SOURCE CPU snapshot closed outside final ACK')
    if state.get('guard_fd') is not None and not state.get('guard_closed',False):
        if _fd_identity(state['guard_fd'])!=state['guard_identity']:
            raise HandshakeError('Original SOURCE CPU child fence changed')
    _deadline(state)
    return state


def _original(capability):
    from backend.engine import application_launch_handshake as h
    try:
        from backend.engine.application_launch_lease import LeaseTransitionBusy
        while True:
            state=_current_source_producer(capability)
            validated={}
            try:
                proof=h._validate(state['root_context'][0],state['root_context'][1],state['challenge'],validated=validated)
            except LeaseTransitionBusy:
                # This is only a read-only wait for the original publication
                # mutex; no CAS, replay, owner repair or renewed producer budget.
                state=_current_source_producer(capability)
                time.sleep(min(.005,_deadline(state)-time.monotonic()))
                continue
            if proof!=state['proof'] or validated.get('protocol_version')!=4:
                raise HandshakeError('Original SOURCE CPU owner authentication changed')
            state=_current_source_producer(capability)
            return state
    except BaseException:
        _retain(capability)
        raise


@contextmanager
def source_publication_admission(capability):
    """One real authentication mutex; original typed lifetime never leaves.

    Only acquisition-busy reads can wait, within the same original producer
    deadline. Once the body begins there is no publication retry or lock drop.
    """
    from backend.engine import application_launch_handshake as h
    from backend.engine.application_launch_lease import LeaseTransitionBusy
    entered=False
    def current():
        state=_current_source_producer(capability)
        if (state['phase']!='child_exited' or state['counted'] is not True
                or state['admission'].snapshot()['active_scopes']<1):
            raise HandshakeError('Original SOURCE CPU publication count/phase changed')
        return state
    try:
        while True:
            state=current();validated={}
            try:
                with h._validated_backend_admission(state['root_context'][0],state['root_context'][1],
                                                    state['challenge'],validated=validated) as proof:
                    entered=True
                    if proof!=state['proof'] or validated.get('protocol_version')!=4:
                        raise HandshakeError('Original SOURCE CPU owner authentication changed')
                    current()
                    yield
                    current()
                # Authentication itself may consume the last original budget;
                # no completion/finalization can follow a late successful read.
                current()
                return
            except LeaseTransitionBusy:
                if entered:raise
                state=current()
                time.sleep(min(.005,_deadline(state)-time.monotonic()))
    except BaseException:
        _retain(capability)
        raise


def admit_backend_source_cpu(frame,proof,root):
    """Only the original execution thread; count before any snapshot/worker write.

    Successful enrollment/child startup/final publication are separate later
    operations. Missing/frozen/legacy contexts cannot fall back to this ticket.
    """
    from backend.engine import application_launch_handshake as h
    from backend.engine import application_launch_execution as execution
    from backend.engine.application_launch_quiescence import writer_guard
    started=time.monotonic();cache=h._CACHE;context=h._root_context()
    if (cache is None or context is None or type(proof) is not dict or proof.get('frozen') is not False
            or not cache.get('ready') or h._context()!=cache.get('context')
            or cache.get('proof')!=proof or context[0]!=root
            or type(cache.get('original_cpu_execution')) is not dict
            or cache['original_cpu_execution'].get('thread') is not threading.current_thread()
            or type(cache['original_cpu_execution'].get('done')) is not threading.Event
            or cache['original_cpu_execution']['done'].is_set()
            or type(cache['original_cpu_execution'].get('deadline')) not in (int,float)
            or not math.isfinite(cache['original_cpu_execution']['deadline'])
            or cache['original_cpu_execution']['deadline']<=started
            or type(frame) is not dict or frame.get('kind')!='cpu_execution_request'
            or type(cache.get('admission')) is not h.BackendWorkAdmission
            or not isinstance(cache.get('challenge'),dict) or 'writer' not in cache['challenge']
            or id(cache) in _CACHE_TICKETS
            or any(cache.get(k) is None for k in ('writer_guard','writer_handle','writer_private_fd','writer_fd_identity'))):
        raise HandshakeError('Original enrolled SOURCE CPU producer admission is unavailable')
    original_context=h._context();original_socket=cache['socket'];endpoint=_endpoint(original_socket)
    validation={}
    if h._validate(root,context[1],cache['challenge'],validated=validation)!=proof or validation.get('protocol_version')!=4:
        raise HandshakeError('Original SOURCE CPU authentication differs')
    intent=execution.validate_request(frame,proof,root)
    capability=execution.admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'])
    if (_canonical(intent['request'])!=_canonical(frame) or _canonical(intent['capability'])!=_canonical(capability)):
        raise HandshakeError('Original SOURCE CPU intent or reviewed capability changed')
    plan=capability['plan'];budget=plan.get('deadline_ms')
    if (plan.get('kind')!='owned_cpu_ocr_known_image_plan' or plan.get('device')!='cpu'
            or type(plan.get('cpu_threads')) is not int or plan['cpu_threads']!=1
            or type(budget) is not int or not 0<budget<=60000
            or capability['plan_sha256']!=frame['plan_sha256']):
        raise HandshakeError('Original reviewed SOURCE CPU plan differs')
    deadline=min(started+budget/1000,cache['original_cpu_execution']['deadline'])
    if time.monotonic()>=deadline:raise HandshakeError('Original SOURCE CPU admission deadline expired')
    anchor=cache['writer_private_fd'];identity=_fd_identity(anchor)
    if identity!=cache['writer_fd_identity']:raise HandshakeError('Original SOURCE CPU writer descriptor changed')
    ticket=_new_producer();state={'pid':os.getpid(),'thread':threading.current_thread(),'cache':cache,
        'context':original_context,'root_context':copy.deepcopy(context),'challenge':cache['challenge'],
        'challenge_bytes':_canonical(cache['challenge']),'socket':original_socket,'endpoint':endpoint,'proof':copy.deepcopy(proof),
        'frame':copy.deepcopy(frame),'intent':copy.deepcopy(intent),'capability':copy.deepcopy(capability),
        'started':started,'deadline':deadline,'anchor':anchor,'anchor_identity':identity,'private':None,
        'admission':cache['admission'],'counted':False,'phase':'creating','using_transport':0,
        'execution':cache['original_cpu_execution'],'done':cache['original_cpu_execution']['done'],
        'execution_deadline':cache['original_cpu_execution']['deadline'],'cpu_queue':cache.get('source_cpu_queue')}
    _PRODUCERS[ticket]=state;_ACTIVE.add(ticket);_CACHE_TICKETS[id(cache)]=ticket
    try:
        writer=cache['challenge']['writer']
        with writer_guard(root,proof['nonce'],writer['writer_id'],expected_registration_sha256=writer['registration_sha256']):
            if (h._CACHE is not cache or h._context()!=original_context or cache['socket'] is not original_socket
                    or _endpoint(original_socket)!=endpoint or _fd_identity(anchor)!=identity
                    or time.monotonic()>=deadline):
                raise HandshakeError('Original SOURCE CPU admission changed before count')
            state['private']=os.dup(anchor)
            cache['admission']._begin_producer();state['counted']=True
        # Guard exit and deadline are decisive, not merely the initial read.
        state['phase']='active';_original(ticket)
        return ticket
    except BaseException:
        # Once a private fd or count is acquired, unknown guard/close outcome
        # retains original custody. No late mint or inferred rollback/retry.
        _retain(ticket)
        raise


@contextmanager
def producer_transport(capability):
    state=_original(capability)
    if state['phase']!='active':raise HandshakeError('Original SOURCE CPU transport is inactive')
    private=None
    try:
        private=os.dup(state['private']);state['using_transport']+=1
        _original(capability)
        yield (private,)
    except BaseException:
        _retain(capability)
        raise
    finally:
        if private is not None:
            try:os.close(private)
            except BaseException:
                _retain(capability)
                raise
            else:
                state['using_transport']-=1


def _sha(value):return hashlib.sha256(_canonical(value)).hexdigest()


def _hex(value,length=64):
    return type(value) is str and len(value)==length and all(c in '0123456789abcdef' for c in value)


def source_command(snapshot,gate_fd,deadline,request_id):
    """Fixed SOURCE command; stdlib-only startup gate precedes model imports."""
    snapshot=Path(snapshot);runtime=str(snapshot/'runtime')
    if (not snapshot.is_absolute() or str(snapshot)!=str(snapshot.resolve())
            or type(gate_fd) is not int or not 3<=gate_fd<=8192
            or type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline<=0
            or not _hex(request_id,32)):
        raise HandshakeError('Original SOURCE CPU command inputs differ')
    bootstrap=('import os,sys,time,select,json,stat\n'
        'sys.dont_write_bytecode=True\n'
        'gate=int(sys.argv[3]);deadline=float(sys.argv[4]);raw=b""\n'
        'while not raw.endswith(b"\\n"):\n'
        ' remaining=deadline-time.monotonic()\n'
        ' if remaining<=0:raise ValueError("Original CPU startup deadline expired")\n'
        ' if not select.select([gate],[],[],remaining)[0]:raise ValueError("Original CPU startup gate expired")\n'
        ' chunk=os.read(gate,8193-len(raw))\n'
        ' if not chunk or len(raw)+len(chunk)>8192:raise ValueError("Original CPU startup gate ended or exceeded bound")\n'
        ' raw+=chunk\n'
        ' if time.monotonic()>=deadline:raise ValueError("Original CPU startup response is late")\n'
        'g=json.loads(raw)\n'
        'assert set(g)=={"request_id","parent_pid","writer_fd","writer_identity","deadline"}\n'
        'assert g["request_id"]==sys.argv[5] and type(g["parent_pid"]) is int and g["parent_pid"]==os.getppid()\n'
        'assert type(g["writer_fd"]) is int and g["writer_fd"]>=3 and g["deadline"]==deadline\n'
        'assert type(g["writer_identity"]) is list and len(g["writer_identity"])==2 and all(type(i) is int for i in g["writer_identity"])\n'
        's=os.fstat(g["writer_fd"]);assert stat.S_ISREG(s.st_mode) and s.st_size==0 and s.st_nlink==1 and s.st_uid==os.getuid() and stat.S_IMODE(s.st_mode)==0o600\n'
        'assert [s.st_dev,s.st_ino]==g["writer_identity"]\n'
        'os.close(gate)\n'
        'if time.monotonic()>=deadline:raise ValueError("Original CPU startup admission is late")\n'
        'sys.path.insert(0,'+repr(runtime)+')\n'
        'from pathlib import Path\n'
        'from backend.engine.flow_package_runtime import run_flow_package\n'
        'assert Path(run_flow_package.__code__.co_filename).resolve()==Path('+repr(runtime)+')/"backend/engine/flow_package_runtime.py"\n'
        'import torch;torch.set_num_threads(1)\n'
        'r=json.loads(Path(sys.argv[1]).read_bytes())\n'
        'if time.monotonic()>=deadline:raise ValueError("Original CPU math admission is late")\n'
        'v=run_flow_package(Path(r["package"]),Path(r["image"]),"owned-cpu-known-image",device="cpu",cpu_threads=1,_owned_worker=True)\n'
        'Path(sys.argv[2]).write_text(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False),encoding="utf-8")')
    return [sys.executable,'-I','-B','-X','pycache_prefix='+str(snapshot/'bytecode'),'-c',bootstrap,
        str(snapshot/'request.json'),str(snapshot/'result.json'),str(gate_fd),repr(float(deadline)),request_id]


def _snapshot_handle_current(state):
    path=state['snapshot'];fd=state['snapshot_fd'];held=os.fstat(fd);named=path.lstat()
    if (path.is_symlink() or not stat.S_ISDIR(held.st_mode) or not stat.S_ISDIR(named.st_mode)
            or (held.st_dev,held.st_ino)!=state['snapshot_identity']
            or (named.st_dev,named.st_ino)!=state['snapshot_identity']
            or held.st_uid!=os.getuid() or named.st_uid!=os.getuid()
            or stat.S_IMODE(held.st_mode)!=0o700 or stat.S_IMODE(named.st_mode)!=0o700
            or os.get_inheritable(fd)):
        raise HandshakeError('Original SOURCE CPU snapshot directory custody changed')


def _backend_cpu_exchange(capability,action,payload):
    state=_original(capability);queue=state['cpu_queue']
    if type(queue) is not CpuRelayQueue:raise HandshakeError('Original SOURCE CPU sole-reader queue unavailable')
    token=queue.enqueue(capability,action,payload);answer=queue.result(token)
    _original(capability)
    return answer['payload']


def reserve_backend_cpu_child(capability,snapshot):
    """Reserve the exact known SOURCE child before the sole original Popen."""
    from backend.engine import application_launch_execution as execution
    from backend.engine.application_launch_quiescence import writer_guard
    state=_original(capability)
    try:
        expected=Path(state['capability']['project_path'])/execution.OUTPUTS/('.owned-cpu-'+state['frame']['request_id'])
        snapshot=Path(snapshot)
        if state['phase']!='active' or snapshot!=expected or snapshot.is_symlink():
            raise HandshakeError('Original SOURCE CPU snapshot/reservation differs')
        _snapshot_current(snapshot,state['capability']);_original(capability)
        state['snapshot']=snapshot
        state['snapshot_fd']=os.open(snapshot,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        os.set_inheritable(state['snapshot_fd'],False)
        info=os.fstat(state['snapshot_fd']);state['snapshot_identity']=(info.st_dev,info.st_ino)
        _snapshot_handle_current(state);_original(capability)
        state.update(pipe=os.pipe(),pipe_close_attempted=set(),child=None,guard=None,guard_closed=False,
            guard_close_attempted=False,transport_fd=None,transport_close_attempted=False)
        state['plan']={'cpu_request':copy.deepcopy(state['frame']),
            'budget_ms':state['capability']['plan']['deadline_ms'],'deadline_monotonic':state['deadline'],
            'workdir':str(snapshot),'gate_fd':state['pipe'][0],
            'command':source_command(snapshot,state['pipe'][0],state['deadline'],state['frame']['request_id'])}
        registration=_backend_cpu_exchange(capability,'reserve',state['plan'])
        if (type(registration) is not dict or set(registration)!={'writer_id','registration_sha256'}
                or not _hex(registration['writer_id'],32) or not _hex(registration['registration_sha256'])):
            raise HandshakeError('Original SOURCE CPU reservation acknowledgement differs')
        state['registration']=copy.deepcopy(registration);_original(capability)
        guard=writer_guard(state['root_context'][0],state['proof']['nonce'],registration['writer_id'],
            expected_registration_sha256=registration['registration_sha256'])
        state['guard']=guard;handle=guard.__enter__()
        if type(handle.pass_fds) is not tuple or len(handle.pass_fds)!=1:
            raise HandshakeError('Original SOURCE CPU child fence unavailable')
        state['guard_fd']=handle.pass_fds[0];state['guard_identity']=_fd_identity(state['guard_fd'])
        _original(capability);state['phase']='reserved'
        return capability
    except BaseException:
        _retain(capability);raise


def _close_startup_once(state,fd):
    if fd in state['pipe_close_attempted']:raise HandshakeError('Original CPU startup close outcome unresolved')
    state['pipe_close_attempted'].add(fd);os.close(fd)


def launch_backend_cpu_child(capability,*,stdout,stderr,environment):
    from backend.engine.process_isolation import session_isolation
    from backend.engine import runtime_deadline as runtime
    state=_original(capability)
    try:
        if state['phase']!='reserved':raise HandshakeError('Original SOURCE CPU spawn already consumed')
        state['phase']='spawning';state['transport_fd']=os.dup(state['private']);_original(capability)
        child=subprocess.Popen(list(state['plan']['command']),cwd=state['plan']['workdir'],stdin=subprocess.DEVNULL,
            stdout=stdout,stderr=stderr,env=environment,close_fds=True,
            pass_fds=(state['transport_fd'],state['guard_fd'],state['pipe'][0]),**session_isolation())
        state['child']=child
        state['ownership']=runtime._OwnedGroup(child,runtime._optional_psutil())
        state['transport_close_attempted']=True;os.close(state['transport_fd']);state['transport_fd']=None
        _capture_backend_cpu_child(capability,child)
        return child
    except BaseException:
        _retain(capability)
        # Only the original startup pipe may end cooperatively; no PID/group
        # signalling or close retry after an uncertain close result.
        if state.get('pipe') is not None and state['pipe'][1] not in state['pipe_close_attempted']:
            try:_close_startup_once(state,state['pipe'][1])
            except BaseException:pass
        raise


def _capture_backend_cpu_child(capability,child):
    from backend.engine import application_launch_lease as lease
    state=_original(capability)
    if state['phase']!='spawning' or type(child) is not _ORIGINAL_POPEN_TYPE or state['child'] is not child:
        raise HandshakeError('Original SOURCE CPU exact Popen capture unavailable')
    try:
        if child.poll() is not None:raise HandshakeError('Original startup-gated CPU child already exited')
        identity=lease._identity(child.pid);_live_cpu_child(identity,state['plan'],state['proof'])
        state['child_identity']=copy.deepcopy(identity);_original(capability)
        answer=_backend_cpu_exchange(capability,'bind',{'registration':state['registration'],
            'child':identity,'plan_sha256':_sha(state['plan'])})
        if answer!={'status':'bound'}:raise HandshakeError('Original SOURCE CPU binding acknowledgement differs')
        if child.poll() is not None:raise HandshakeError('Original CPU child exited before binding acknowledgement')
        _live_cpu_child(identity,state['plan'],state['proof']);_original(capability)
        raw=_canonical({'request_id':state['frame']['request_id'],'parent_pid':os.getpid(),
            'writer_fd':state['guard_fd'],'writer_identity':list(state['guard_identity']),
            'deadline':state['plan']['deadline_monotonic']})+b'\n'
        if len(raw)>8192 or os.write(state['pipe'][1],raw)!=len(raw):
            raise HandshakeError('Original SOURCE CPU startup gate write incomplete')
        _original(capability)
        for fd in state['pipe']:_close_startup_once(state,fd)
        state['pipe']=None;_original(capability);state['phase']='active_child'
    except BaseException:
        _retain(capability);raise


def execute_source_process(capability,*,environment):
    """Fixed known SOURCE only; original handle observation without signals.

    Logs, snapshot, exact Popen and both fences stay retained through public
    result validation and ACK. Unknown exit/group/close outcome never retries.
    """
    from backend.engine import runtime_deadline as runtime
    state=_original(capability)
    try:
        if state['phase']!='reserved':raise HandshakeError('Original SOURCE CPU executor is not reserved')
        state['streams']=[]
        for name in ('cpu-stdout.txt','cpu-stderr.txt'):
            fd=os.open(state['snapshot']/name,os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
            state['streams'].append(os.fdopen(fd,'w+b'))
        child=launch_backend_cpu_child(capability,stdout=state['streams'][0],stderr=state['streams'][1],environment=environment)
        if state['child'] is not child or type(child) is not _ORIGINAL_POPEN_TYPE:
            raise HandshakeError('Original SOURCE CPU executor handle differs')
        while child.poll() is None:
            _original(capability);runtime._observe_owned(state['ownership'],None);_original(capability)
            try:child.wait(timeout=min(.025,_deadline(state)-time.monotonic()))
            except subprocess.TimeoutExpired:pass
        _original(capability)
        if child.poll()!=0 or runtime._observe_owned(state['ownership'],None):
            raise HandshakeError('Original SOURCE CPU exit or observed group unresolved')
        _original(capability);state['phase']='child_exited'
        texts=[]
        for stream in state['streams']:
            stream.flush();stream.seek(0);texts.append(stream.read(65536).decode('utf-8',errors='replace'))
        _original(capability)
        return {'status':'completed','returncode':0,'pid':child.pid,'stdout':texts[0],'stderr':texts[1],
            'elapsed_ms':round((time.monotonic()-state['started'])*1000,3)}
    except BaseException:
        _retain(capability);raise


def completion_ready(capability,completion):
    state=_original(capability);queue=state['cpu_queue']
    try:
        if (state['phase']!='child_exited' or type(queue) is not CpuRelayQueue
                or completion.get('kind')!='cpu_execution_completed'
                or _canonical(completion.get('request'))!=_canonical(state['frame'])
                or _canonical(completion.get('backend_proof'))!=_canonical(state['proof'])
                or completion.get('worker_pid')!=state['child_identity']['pid']):
            raise HandshakeError('Original SOURCE CPU completion differs from exited child')
        with queue._condition:
            queue._fresh();_original(capability)
            state.update(completion=copy.deepcopy(completion),completion_sent=False,publication_answer=None,
                publication_used=False,settlement_sent=False,phase='waiting_publication')
            queue._condition.notify_all()
    except BaseException:
        _retain(capability);raise


def _backend_published_receipt(state,answer):
    from backend.engine import application_launch_execution as execution,application_launch_lease as lease
    root=state['root_context'][0];request=state['frame'];completion=state['completion']
    _deadline(state)
    with lease._transition_admission(root,request['nonce']):
        row=lease._load(root);record=row.get('cpu_execution')
        if (type(record) is not dict or record.get('request_id')!=request['request_id']
                or record.get('request_sha256')!=_sha(request) or record.get('receipt_sha256')!=answer['receipt_sha256']):
            raise HandshakeError('Original SOURCE CPU published receipt binding differs')
        path=root/lease.LEASES/request['nonce']/'cpu-execution-receipt.json';raw=execution._read(path)
        if hashlib.sha256(raw).hexdigest()!=answer['receipt_sha256']:
            raise HandshakeError('Original SOURCE CPU published receipt bytes differ')
        receipt=execution._json(raw)
        if (receipt.get('worker_pid')!=state['child_identity']['pid'] or receipt.get('backend_frozen') is not False
                or receipt.get('execution_scope')!='controlled_source_backend'
                or receipt.get('output_path')!=completion['output_path'] or receipt.get('output_sha256')!=completion['output_sha256']):
            raise HandshakeError('Original SOURCE CPU published child/output differs')
        execution.recheck_receipt_artifacts(root,row,receipt)
        if execution._read(path)!=raw or lease._load(root).get('cpu_execution')!=record:
            raise HandshakeError('Original SOURCE CPU publication changed during readback')
    _deadline(state)


def finish_backend_source_cpu(capability,completion):
    """Final original ACK, then exact close/leave once; no partial repair."""
    state=_original(capability)
    try:
        if state['phase']!='waiting_publication' or _canonical(completion)!=_canonical(state['completion']):
            raise HandshakeError('Original SOURCE CPU final completion differs')
        answer=state['cpu_queue'].publication_result(capability)
        _backend_published_receipt(state,answer);_original(capability)
        if state['child'].poll()!=0 or state['guard_close_attempted']:
            raise HandshakeError('Original SOURCE CPU exit/guard finalization unavailable')
        state['guard_close_attempted']=True;state['guard'].__exit__(None,None,None);state['guard_closed']=True
        _original(capability);state['phase']='finishing'
        reply=_backend_cpu_exchange(capability,'finish',{'registration':state['registration'],
            'child':state['child_identity'],'plan_sha256':_sha(state['plan']),'returncode':0,
            'completion_sha256':_sha(completion),'receipt_sha256':answer['receipt_sha256']})
        if reply!={'status':'direct_exited'}:raise HandshakeError('Original SOURCE CPU final ACK differs')
        _original(capability);state['phase']='closing'
        state['private_close_attempted']=True;os.close(state['private']);state['private_closed']=True
        _original(capability)
        state['snapshot_close_attempted']=True;os.close(state['snapshot_fd']);state['snapshot_closed']=True
        _original(capability)
        for stream in state.get('streams',[]):
            stream.close();_original(capability)
        # Preserve the queue -> admission lock order used by the sole reader.
        # A drain may observe either the counted producer, the finished state,
        # or sticky refusal; never a clean zero between leave and final checks.
        # An original leave that consumed its count before raising is not
        # retried or repaired by inventing another count.
        with state['cpu_queue']._condition:
            with state['admission']._condition:
                try:
                    _original(capability)
                    state['count_release_attempted']=True
                    state['admission'].leave();state['count_release_returned']=True;state['counted']=False
                    _original(capability)
                    state['phase']='finished';_ACTIVE.remove(capability)
                    state['cpu_queue']._condition.notify_all()
                except BaseException:
                    _retain(capability);raise
    except BaseException:
        _retain(capability);raise


def _source_rows(expected):
    from backend.engine import application_launch_execution as execution
    if execution.runtime_source_identity()!=expected:
        raise HandshakeError('Original SOURCE CPU runtime inventory changed')
    root=Path(execution.__file__).resolve().parents[2]
    rows={};total=0
    for path in sorted((root/'backend').rglob('*.py')):
        if 'tests' in path.relative_to(root/'backend').parts:continue
        raw=execution._read(path,execution.MAX_FILE);total+=len(raw)
        if total>execution.MAX_TOTAL:raise HandshakeError('Original SOURCE CPU runtime exceeds bound')
        rows[path.relative_to(root).as_posix()]={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    if not 1<=len(rows)<=execution.MAX_FILES or _sha([{'path':name,**row} for name,row in sorted(rows.items())])!=expected:
        raise HandshakeError('Original SOURCE CPU runtime inventory changed during reading')
    return rows


def _snapshot_current(snapshot,capability):
    from backend.engine import application_launch_execution as execution
    from backend.engine import runtime_update as update
    snapshot=update._unlinked(snapshot);plan=capability['plan']
    if not snapshot.is_dir():raise HandshakeError('Original SOURCE CPU retained snapshot is unavailable')
    raw=execution._read(snapshot/'package/manifest.json',execution.MAX_RESULT,expected=plan['package_manifest_sha256'])
    manifest=execution._json(raw);files=manifest.get('files') if type(manifest) is dict else None
    if type(files) is not list or not 1<=len(files)<=execution.MAX_FILES:
        raise HandshakeError('Original SOURCE CPU snapshot inventory differs')
    names={'manifest.json'};total=len(raw)
    for row in files:
        if (type(row) is not dict or set(row)!={'path','size','sha256'} or type(row['size']) is not int
                or not 0<=row['size']<=execution.MAX_FILE or not _hex(row['sha256'])):
            raise HandshakeError('Original SOURCE CPU snapshot row differs')
        name=update._safe_path(row['path'])
        if name in names:raise HandshakeError('Original SOURCE CPU snapshot membership repeats')
        names.add(name);total+=row['size']
        if total>execution.MAX_TOTAL:raise HandshakeError('Original SOURCE CPU snapshot exceeds bound')
        execution._read(snapshot/'package'/name,row['size'],expected=row['sha256'])
    if execution._members(snapshot/'package')!=names:raise HandshakeError('Original SOURCE CPU package has unlisted files')
    execution._read(snapshot/'input.png',execution.MAX_FILE,expected=plan['input_sha256'])
    request=execution._json(execution._read(snapshot/'request.json'))
    if request!={'package':str(snapshot/'package'),'image':str(snapshot/'input.png')}:
        raise HandshakeError('Original SOURCE CPU snapshot request differs')
    rows=_source_rows(plan['runtime_source_sha256'])
    if execution._members(snapshot/'runtime')!=set(rows):raise HandshakeError('Original SOURCE CPU runtime has unlisted files')
    for name,row in rows.items():execution._read(snapshot/'runtime'/name,row['size'],expected=row['sha256'])


def validate_controller_plan(owner,value,proof):
    from backend.engine import application_launch_execution as execution
    from backend.engine import runtime_update as update
    names={'cpu_request','budget_ms','deadline_monotonic','workdir','gate_fd','command'}
    if (type(value) is not dict or set(value)!=names or proof.get('frozen') is not False
            or type(value['cpu_request']) is not dict or type(value['budget_ms']) is not int
            or not 0<value['budget_ms']<=60000 or type(value['deadline_monotonic']) not in (float,int)
            or not math.isfinite(value['deadline_monotonic'])
            or not 0<value['deadline_monotonic']-time.monotonic()<=value['budget_ms']/1000+.05
            or type(value['workdir']) is not str or type(value['command']) is not list
            or len(_canonical(value))>16384):
        raise HandshakeError('Original SOURCE CPU plan or producer deadline differs')
    request=value['cpu_request'];intent=execution.validate_request(request,proof,owner.root)
    capability=execution.admit_plan(owner.root,request['workspace_id'],request['project_id'],request['plan_sha256'])
    if _canonical(intent['capability'])!=_canonical(capability):
        raise HandshakeError('Original SOURCE CPU durable capability changed')
    plan=capability['plan']
    if (plan['kind']!='owned_cpu_ocr_known_image_plan' or plan['device']!='cpu' or type(plan['cpu_threads']) is not int
            or plan['cpu_threads']!=1 or value['budget_ms']!=plan['deadline_ms']):
        raise HandshakeError('Original SOURCE CPU reviewed plan differs')
    snapshot=update._unlinked(Path(capability['project_path'])/execution.OUTPUTS/('.owned-cpu-'+request['request_id']))
    if value['workdir']!=str(snapshot) or value['command']!=source_command(snapshot,value['gate_fd'],value['deadline_monotonic'],request['request_id']):
        raise HandshakeError('Original SOURCE CPU fixed command or workdir differs')
    _snapshot_current(snapshot,capability)
    if time.monotonic()>=value['deadline_monotonic']:raise HandshakeError('Original SOURCE CPU validation completed late')
    return copy.deepcopy(value)


def _controller_original(capability):
    state=_CONTROLLERS.get(capability) if type(capability) is ControllerCpuRelay else None
    if (state is None or capability not in _ACTIVE or state['failed'] or state['pid']!=os.getpid()
            or state['thread'] is not threading.current_thread()):
        raise HandshakeError('Original SOURCE CPU controller capability is unavailable')
    return state


def _controller_deadline(state):
    from backend.engine import application_node_writer_authority as node
    original=node._state(state['node']);limit=state['plan']['deadline_monotonic']
    if original['deadline'] is not None:limit=min(limit,original['deadline'])
    if time.monotonic()>=limit:raise HandshakeError('Original SOURCE CPU publication deadline expired')
    return limit


def _core_publication_deadline(capability,epoch,*,phase=None):
    from backend.engine import application_node_writer_authority as node
    state=_controller_original(capability)
    if (state['epoch'] is not epoch or state['owner']._writer_epoch is not epoch
            or state['phase'] not in ('creating','binding','finishing')
            or phase is not None and state['phase']!=phase):
        raise HandshakeError('Original SOURCE CPU publication capability differs')
    _controller_deadline(state);node._fresh(state['node']);_controller_deadline(state)


def _cpu_request(value,proof):
    names={'schema_version','kind','action','nonce','epoch','binding_sha256','backend_claim_sha256','request_id','payload'}
    if (type(value) is not dict or set(value)!=names or type(value['schema_version']) is not int or value['schema_version']!=1
            or value['kind']!='backend_cpu_child_request' or value['action'] not in ('reserve','bind','finish')
            or value['nonce']!=proof['nonce'] or value['epoch']!=proof['epoch'] or value['binding_sha256']!=proof['binding_sha256']
            or value['backend_claim_sha256']!=_sha(proof) or not _hex(value['request_id'],32)
            or type(value['payload']) is not dict or len(_canonical(value))>16384):
        raise HandshakeError('Original SOURCE CPU request binding differs')
    return copy.deepcopy(value)


def _cpu_reply(request,payload):
    return {'schema_version':1,'kind':'controller_cpu_child_reply','nonce':request['nonce'],'epoch':request['epoch'],
        'request_id':request['request_id'],'action':request['action'],'request_sha256':_sha(request),'payload':payload}


def _live_cpu_child(value,plan,proof):
    from backend.engine import application_launch_lease as lease
    import psutil
    if (type(value) is not dict or set(value)!={'pid','created_at','command_sha256'}
            or type(value['pid']) is not int or value['pid']<=0 or value!=lease._identity(value['pid'])):
        raise HandshakeError('Original SOURCE CPU child birth or command differs')
    process=psutil.Process(value['pid'])
    if (process.ppid()!=proof['process']['pid'] or process.cmdline()!=plan['command']
            or process.cwd()!=plan['workdir'] or os.getsid(value['pid'])!=value['pid']
            or os.getpgid(value['pid'])!=value['pid']):
        raise HandshakeError('Original SOURCE CPU child parent/command/session differs')


def _core_child_binding(capability,epoch,writer_id):
    state=_controller_original(capability)
    if (state['epoch'] is not epoch or state['registration'].writer_id!=writer_id or state['phase']!='binding'):
        raise HandshakeError('Original SOURCE CPU binding capability differs')
    _controller_deadline(state);_live_cpu_child(state['child'],state['plan'],state['proof']);_controller_deadline(state)
    return copy.deepcopy(state['child']),state['registration'].registration_sha256


def _receipt_current(state):
    from backend.engine import application_launch_execution as execution,application_launch_lease as lease
    publication=state.get('publication')
    if publication is None:raise HandshakeError('Original SOURCE CPU output publication is unavailable')
    _controller_deadline(state)
    owner=state['owner'];request=state['plan']['cpu_request']
    with lease._transition_admission(owner.root,owner.nonce):
        row=owner._owned();value=row.get('cpu_execution')
        if (type(value) is not dict or value.get('request_id')!=request['request_id']
                or value.get('request_sha256')!=_sha(request) or value.get('receipt_sha256')!=publication['receipt_sha256']):
            raise HandshakeError('Original SOURCE CPU durable receipt binding changed')
        path=owner.root/lease.LEASES/owner.nonce/'cpu-execution-receipt.json'
        raw=execution._read(path)
        if (hashlib.sha256(raw).hexdigest()!=publication['receipt_sha256']
                or _canonical(execution._json(raw))!=_canonical(publication['receipt'])):
            raise HandshakeError('Original SOURCE CPU published receipt bytes changed')
        execution.recheck_receipt_artifacts(owner.root,row,publication['receipt'])
        if execution._read(path)!=raw or owner._owned().get('cpu_execution')!=value:
            raise HandshakeError('Original SOURCE CPU receipt changed during independent readback')
    _controller_deadline(state)
    return publication


def admit_controller_publication(owner,completion,receipt,receipt_sha256):
    """Original controller after independent CPU proof validation/publication.

    Data pins never mint a relay. The prior typed active child and fresh durable
    receipt are both required; this one publication cannot be replayed.
    """
    from backend.engine import application_node_writer_authority as node
    capability=getattr(owner,'_cpu_relays',{}).get(completion.get('request',{}).get('request_id'))
    state=_controller_original(capability)
    try:
        request=state['plan']['cpu_request']
        if (state['owner'] is not owner or state['phase']!='active' or state.get('publication') is not None
                or _canonical(completion.get('request'))!=_canonical(request)
                or completion.get('worker_pid')!=state['child']['pid'] or receipt.get('worker_pid')!=state['child']['pid']
                or receipt.get('request_id')!=request['request_id'] or not _hex(receipt_sha256)
                or receipt.get('backend_frozen') is not False or receipt.get('execution_scope')!='controlled_source_backend'
                or completion.get('output_path')!=receipt.get('output_path') or completion.get('output_sha256')!=receipt.get('output_sha256')):
            raise HandshakeError('Original SOURCE CPU completion/publication differs')
        _controller_deadline(state);node._fresh(state['node']);_controller_deadline(state)
        state['publication']={'completion':copy.deepcopy(completion),'completion_sha256':_sha(completion),
            'receipt':copy.deepcopy(receipt),'receipt_sha256':receipt_sha256}
        _receipt_current(state)
        _controller_deadline(state);node._fresh(state['node']);_controller_deadline(state)
        return {'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':owner.nonce,
            'request_id':request['request_id'],'request_sha256':_sha(request),
            'completion_sha256':_sha(completion),'receipt_sha256':receipt_sha256}
    except BaseException:
        _retain(capability)
        raise


def _core_child_exit(capability,epoch,writer_id):
    state=_controller_original(capability)
    if (state['epoch'] is not epoch or state['registration'].writer_id!=writer_id or state['phase']!='finishing'):
        raise HandshakeError('Original SOURCE CPU exit capability differs')
    _controller_deadline(state);_receipt_current(state);_controller_deadline(state)
    # No PID reopen after exit. The original one-use authenticated event must
    # match this previously bound child and independently published output.
    return copy.deepcopy(state['child'])


def admit_controller_settlement(owner,frame):
    """Read data from the original reader; only prior finished typed row binds it."""
    from backend.engine import application_node_writer_authority as node
    capability=getattr(owner,'_cpu_relays',{}).get(frame.get('request_id'))
    state=_CONTROLLERS.get(capability) if type(capability) is ControllerCpuRelay else None
    if (state is None or state['owner'] is not owner or state['failed'] or state['phase']!='finished'
            or state['pid']!=os.getpid() or state['thread'] is not threading.current_thread()):
        raise HandshakeError('Original SOURCE CPU finished controller unavailable')
    try:
        if state.get('settled'):raise HandshakeError('Original SOURCE CPU settlement replayed')
        publication=state['publication'];request=state['plan']['cpu_request']
        expected={'schema_version':1,'kind':'source_cpu_settled','nonce':owner.nonce,'request_id':request['request_id'],
            'request_sha256':_sha(request),'completion_sha256':publication['completion_sha256'],
            'receipt_sha256':publication['receipt_sha256']}
        _controller_deadline(state);node._fresh(state['node']);_controller_deadline(state)
        if _canonical(frame)!=_canonical(expected):raise HandshakeError('Original SOURCE CPU settlement binding differs')
        row=next(r for r in state['epoch'].snapshot()['registry']['writers'] if r['writer_id']==state['registration'].writer_id)
        if row['status']!='direct_exited' or row['exit_code']!=0 or row['process']!=state['child']:
            raise HandshakeError('Original SOURCE CPU final row changed')
        _receipt_current(state);node._fresh(state['node']);_controller_deadline(state)
        state['settled']=True
        return copy.deepcopy(frame)
    except BaseException:
        _retain(capability);raise


def process_cpu_controller_event(owner,event):
    """Typed original controller receive only; consumed before any registry CAS."""
    from backend.engine import application_node_writer_authority as node
    authority=owner._node_backend_authority
    request,proof=node.consume_cpu_receive(authority,event);request=_cpu_request(request,proof)
    relays=getattr(owner,'_cpu_relays',None)
    if relays is None:owner._cpu_relays=relays={}
    identifier=request['request_id']
    if request['action']!='reserve':
        capability=relays.get(identifier);state=_controller_original(capability)
        try:
            if (state['owner'] is not owner or state['epoch'] is not owner._writer_epoch
                    or state['node'] is not authority or state['proof']!=proof):
                raise HandshakeError('Original SOURCE CPU controller binding changed')
            _controller_deadline(state)
            registration=state['registration'];payload=request['payload']
            names={'registration','child','plan_sha256'}
            if request['action']=='finish':names|={'returncode','completion_sha256','receipt_sha256'}
            if (set(payload)!=names or payload['registration']!={'writer_id':registration.writer_id,
                    'registration_sha256':registration.registration_sha256} or payload['plan_sha256']!=_sha(state['plan'])):
                raise HandshakeError('Original SOURCE CPU registration pin differs')
            if request['action']=='bind':
                if state['phase']!='reserved':raise HandshakeError('Original SOURCE CPU child cannot rebind')
                _live_cpu_child(payload['child'],state['plan'],proof);_controller_deadline(state)
                state.update(phase='binding',child=copy.deepcopy(payload['child']))
                owner._writer_epoch.bind_authenticated_cpu_child(registration.writer_id,capability,
                    expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
                state['phase']='active';status='bound'
            else:
                if (state['phase']!='active' or payload['child']!=state['child']
                        or type(payload['returncode']) is not int or payload['returncode']!=0):
                    raise HandshakeError('Original SOURCE CPU child exit is incomplete or replayed')
                publication=_receipt_current(state)
                if (payload['completion_sha256']!=publication['completion_sha256']
                        or payload['receipt_sha256']!=publication['receipt_sha256']):
                    raise HandshakeError('Original SOURCE CPU output acknowledgement differs')
                state['phase']='finishing'
                owner._writer_epoch.observe_authenticated_cpu_child_exit(registration.writer_id,capability,
                    expected_registry_sha256=owner._writer_epoch.snapshot()['registry_sha256'])
                state['phase']='finished';status='direct_exited'
            node._fresh(authority);_controller_deadline(state)
            if status=='direct_exited':_ACTIVE.remove(capability)
            return _cpu_reply(request,{'status':status})
        except BaseException:
            _retain(capability)
            raise
    if identifier in relays:raise HandshakeError('Original SOURCE CPU reservation is replayed or unresolved')
    plan=validate_controller_plan(owner,request['payload'],proof)
    if plan['cpu_request']['request_id']!=identifier:raise HandshakeError('Original SOURCE CPU request identifier differs')
    epoch=owner._writer_epoch;epoch._original();capability=_new_controller()
    state={'pid':os.getpid(),'thread':threading.current_thread(),'owner':owner,'epoch':epoch,'node':authority,
        'proof':proof,'request_id':identifier,'plan':plan,'registration':None,'phase':'creating','failed':False}
    _CONTROLLERS[capability]=state;relays[identifier]=capability;_ACTIVE.add(capability)
    try:
        state['registration']=registration=epoch.enroll_authenticated_cpu_child(capability,
            expected_registry_sha256=epoch.snapshot()['registry_sha256'])
        state['phase']='reserved';node._fresh(authority);_controller_deadline(state)
        return _cpu_reply(request,{'writer_id':registration.writer_id,'registration_sha256':registration.registration_sha256})
    except BaseException:
        _retain(capability)
        raise


class CpuRelayQueue:
    """Distinct in-memory CPU exchanges, scheduled by the existing sole reader.

    No socket read or send occurs here. Tokens are separate from preflight;
    original worker ownership and the original endpoint stay pinned through
    consumption. Unknown/late outcomes retain the producer count and OFD.
    """
    def __init__(self,cache):
        from backend.engine import application_launch_handshake as h
        if (cache is not h._CACHE or type(cache) is not dict or cache.get('proof',{}).get('frozen') is not False
                or 'writer' not in cache.get('challenge',{}) or h._root_context() is None
                or id(cache) in _CPU_QUEUE_CACHES or cache.get('source_cpu_queue') is not None):
            raise HandshakeError('Original SOURCE CPU queue admission is unavailable or replayed')
        self._cache=cache;self._channel=cache['socket'];self._endpoint=_endpoint(self._channel)
        self._context=copy.deepcopy(cache['context']);self._root_context=copy.deepcopy(h._root_context())
        self._proof=_canonical(cache['proof']);self._challenge=cache['challenge'];self._challenge_bytes=_canonical(self._challenge)
        self._pid=os.getpid();self._reader=None;self._condition=threading.Condition()
        self._active=None;self._seen=set();self._closed_deadline=None;self.unresolved=False
        _CPU_QUEUE_CACHES[id(cache)]=self

    def _producer_state(self):
        cap=_CACHE_TICKETS.get(id(self._cache));state=_PRODUCERS.get(cap) if type(cap) is BackendCpuProducer else None
        if state is None or state['cache'] is not self._cache or state['phase']=='unresolved':
            raise HandshakeError('Original SOURCE CPU queue producer unavailable')
        _deadline(state)
        return cap,state

    def completion_outgoing(self):
        with self._condition:
            self._fresh(reader=True)
            cap=_CACHE_TICKETS.get(id(self._cache));state=_PRODUCERS.get(cap) if type(cap) is BackendCpuProducer else None
            if state is None or state.get('phase')!='waiting_publication' or state.get('completion_sent'):
                return None
            _deadline(state);state['completion_sent']=True
            return copy.deepcopy(state['completion'])

    def action_deadline(self):
        with self._condition:
            self._fresh(reader=True)
            if id(self._cache) not in _CACHE_TICKETS:
                # Sole-reader transport wait while the already retained worker
                # is entering admission. This grants no producer/frame authority.
                execution=self._cache.get('original_cpu_execution')
                if (type(execution) is not dict or type(execution.get('done')) is not threading.Event
                        or type(execution.get('deadline')) not in (int,float)
                        or not math.isfinite(execution['deadline'])):
                    raise HandshakeError('Original SOURCE CPU callback wait unavailable')
                deadline=min(execution['deadline'],self._closed_deadline or float('inf'))
                if time.monotonic()>=deadline:raise HandshakeError('Original SOURCE CPU callback wait expired')
                return deadline
            _,state=self._producer_state();return _deadline(state)

    def settlement_outgoing(self):
        with self._condition:
            self._fresh(reader=True);_,state=self._producer_state()
            if state['phase']!='finished' or state['settlement_sent']:
                raise HandshakeError('Original SOURCE CPU settlement is incomplete or replayed')
            if (self._active is not None or state['counted'] or not state.get('private_closed')
                    or not state.get('snapshot_closed') or not state['guard_closed']
                    or state['execution'] is not self._cache.get('original_cpu_execution')
                    or state['execution']['done'] is not state['done'] or not state['done'].is_set()):
                raise HandshakeError('Original SOURCE CPU callback/fence settlement incomplete')
            answer=state['publication_answer'];_deadline(state);state['settlement_sent']=True
            return {'schema_version':1,'kind':'backend_source_cpu_settled','nonce':state['frame']['nonce'],
                'request_id':state['frame']['request_id'],'request_sha256':_sha(state['frame']),
                'completion_sha256':_sha(state['completion']),'receipt_sha256':answer['receipt_sha256']}

    def deliver_publication(self,answer):
        with self._condition:
            try:
                self._fresh(reader=True);cap,state=self._producer_state()
                request=state['frame']
                expected={'schema_version':1,'kind':'controller_cpu_publication_ack','nonce':request['nonce'],
                    'request_id':request['request_id'],'request_sha256':_sha(request),
                    'completion_sha256':_sha(state.get('completion')),
                    'receipt_sha256':answer.get('receipt_sha256') if type(answer) is dict else None}
                if (state['phase']!='waiting_publication' or not state.get('completion_sent')
                        or state.get('publication_answer') is not None or not _hex(expected['receipt_sha256'])
                        or _canonical(answer)!=_canonical(expected)):
                    raise HandshakeError('Original SOURCE CPU publication ACK differs or replayed')
                self._fresh(reader=True);_deadline(state)
                state['publication_answer']=copy.deepcopy(answer);self._condition.notify_all()
            except BaseException as exc:
                self.abandon(exc);raise

    def publication_result(self,capability):
        with self._condition:
            state=_original(capability)
            if state['cpu_queue'] is not self or state['phase']!='waiting_publication' or state['publication_used']:
                raise HandshakeError('Original SOURCE CPU publication is consumed or foreign')
            try:
                while state['publication_answer'] is None:
                    self._fresh();_original(capability)
                    self._condition.wait(_deadline(state)-time.monotonic())
                self._fresh();_original(capability);state['publication_used']=True
                return copy.deepcopy(state['publication_answer'])
            except BaseException as exc:
                self.abandon(exc);raise

    def abandon(self,error):
        with self._condition:
            self.unresolved=True
            if self._active is not None:
                value=self._token(self._active);value['error']=error;_retain(value['producer'])
            cap=_CACHE_TICKETS.get(id(self._cache))
            if cap is not None and _PRODUCERS.get(cap,{}).get('phase')!='finished':_retain(cap)
            self._condition.notify_all()

    def _fresh(self,channel=None,*,reader=False):
        from backend.engine import application_launch_handshake as h
        try:
            if (self._pid!=os.getpid() or self.unresolved or h._CACHE is not self._cache
                    or self._cache.get('source_cpu_queue') is not self or h._context()!=self._context
                    or h._root_context()!=self._root_context or not self._cache.get('ready')
                    or self._cache.get('socket') is not self._channel or _endpoint(self._channel)!=self._endpoint
                    or _canonical(self._cache.get('proof'))!=self._proof
                    or self._cache.get('challenge') is not self._challenge or _canonical(self._challenge)!=self._challenge_bytes
                    or channel is not None and channel is not self._channel
                    or reader and self._reader is not threading.current_thread()):
                raise HandshakeError('Original SOURCE CPU sole reader/cache/channel differs')
        except BaseException as exc:
            self.abandon(exc)
            raise

    def claim_reader(self):
        with self._condition:
            self._fresh()
            if self._reader is not None:raise HandshakeError('Original SOURCE CPU queue already has a reader')
            self._reader=threading.current_thread()

    def _token(self,token):
        state=_CPU_EXCHANGES.get(token) if type(token) is _CpuExchange else None
        if state is None or state['queue'] is not self:raise HandshakeError('Original SOURCE CPU exchange token differs')
        return state

    def enqueue(self,producer,action,payload):
        with self._condition:
            self._fresh();state=_original(producer)
            if state['cache'] is not self._cache or state['cpu_queue'] is not self:
                raise HandshakeError('Original SOURCE CPU exchange belongs to another producer')
            if self._closed_deadline is not None and action!='finish':raise HandshakeError('Original SOURCE CPU admission is closed')
            key=(state['frame']['request_id'],action)
            if action not in ('reserve','bind','finish') or self._active is not None or key in self._seen or len(self._seen)>=3:
                raise HandshakeError('Original SOURCE CPU exchange is active, replayed or invalid')
            proof=state['proof'];frame={'schema_version':1,'kind':'backend_cpu_child_request','action':action,
                'nonce':proof['nonce'],'epoch':proof['epoch'],'binding_sha256':proof['binding_sha256'],
                'backend_claim_sha256':_sha(proof),'request_id':state['frame']['request_id'],'payload':payload}
            frame=_cpu_request(frame,proof);deadline=_deadline(state);token=_new_exchange()
            _CPU_EXCHANGES[token]={'queue':self,'producer':producer,'owner':threading.current_thread(),
                'frame':frame,'deadline':deadline,'taken':False,'done':False,'used':False,'answer':None,'error':None}
            self._active=token;self._seen.add(key);self._condition.notify_all();return token

    def close_admission(self,deadline):
        with self._condition:
            self._fresh(reader=True)
            if self._closed_deadline is not None or type(deadline) not in (int,float) or not math.isfinite(deadline):
                raise HandshakeError('Original SOURCE CPU drain bound differs or replayed')
            self._closed_deadline=deadline
            if self._active is not None:
                state=self._token(self._active);state['deadline']=min(state['deadline'],deadline)
            self._condition.notify_all()

    def take(self,channel):
        with self._condition:
            self._fresh(channel,reader=True)
            if self._active is None:return None
            state=self._token(self._active)
            if state['taken']:return None
            if time.monotonic()>=state['deadline']:
                self.abandon(HandshakeError('Original SOURCE CPU dispatch is late'))
                raise HandshakeError('Original SOURCE CPU dispatch is late')
            state['taken']=True;return self._active

    def outgoing(self,token):
        with self._condition:
            self._fresh(reader=True);state=self._token(token)
            if token is not self._active or not state['taken'] or state['done'] or state['used']:
                raise HandshakeError('Original SOURCE CPU outgoing exchange differs')
            if time.monotonic()>=state['deadline']:
                self.abandon(HandshakeError('Original SOURCE CPU dispatch deadline expired'))
                raise HandshakeError('Original SOURCE CPU dispatch deadline expired')
            return copy.deepcopy(state['frame']),state['deadline']

    def complete(self,token,answer):
        with self._condition:
            self._fresh(reader=True);state=self._token(token)
            if token is not self._active or not state['taken'] or state['done'] or state['used']:
                raise HandshakeError('Original SOURCE CPU reply is foreign or replayed')
            try:
                if (type(answer) is not dict or type(answer.get('payload')) is not dict
                        or _canonical(answer)!=_canonical(_cpu_reply(state['frame'],answer['payload']))
                        or len(_canonical(answer))>8192 or time.monotonic()>=state['deadline']):
                    raise HandshakeError('Original SOURCE CPU acknowledgement differs or is late')
                state['answer']=copy.deepcopy(answer);state['done']=True;self._condition.notify_all()
            except BaseException as exc:
                self.abandon(exc)
                raise

    def result(self,token):
        with self._condition:
            state=self._token(token)
            if token is not self._active or state['owner'] is not threading.current_thread() or state['used']:
                raise HandshakeError('Original SOURCE CPU reply belongs to another worker or is consumed')
            try:
                self._fresh();_original(state['producer'])
                while not state['done'] and state['error'] is None:
                    remaining=state['deadline']-time.monotonic()
                    if remaining<=0:raise HandshakeError('Original SOURCE CPU reply deadline expired')
                    self._condition.wait(remaining)
                    self._fresh();_original(state['producer'])
                if state['error'] is not None:raise HandshakeError('Original SOURCE CPU exchange is unresolved') from state['error']
                self._fresh();_original(state['producer'])
                if time.monotonic()>=state['deadline']:raise HandshakeError('Original SOURCE CPU reply consumption is late')
                state['used']=True;self._active=None;return copy.deepcopy(state['answer'])
            except BaseException as exc:
                self.abandon(exc)
                raise

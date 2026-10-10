"""Private runtime state publication and owned process lifecycle control."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import psutil

from backend.remote.file_replace import replace_file  # Windows: waits out a reader of the file
from backend.engine.process_isolation import session_isolation  # noqa: F401  (re-exported for existing callers)

_REGISTRY_LOCK=threading.Lock()
_THREAD_LOCKS={}
_LOCAL=threading.local()


@contextmanager
def runtime_state_lock(root):
    """Reject concurrent mutations; nested calls on the owning thread reenter."""
    root=Path(root).resolve();path=root/'runtime_lifecycle.lock';key=str(path)
    with _REGISTRY_LOCK:lock=_THREAD_LOCKS.setdefault(key,threading.RLock())
    if not lock.acquire(blocking=False):raise ValueError('This runtime already has a lifecycle operation')
    depths=getattr(_LOCAL,'depths',None)
    if depths is None:depths={};_LOCAL.depths=depths
    if key in depths:
        depths[key]+=1
        try:yield
        finally:depths[key]-=1;lock.release()
        return
    handle=None;held=False
    try:
        if path.is_symlink():raise ValueError('Runtime lifecycle lock is linked')
        flags=os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0)
        handle=os.fdopen(os.open(path,flags,0o600),'r+b')
        try:
            if os.name=='nt':
                import msvcrt
                if os.fstat(handle.fileno()).st_size==0:handle.write(b'0');handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as exc:raise ValueError('This runtime already has a lifecycle operation') from exc
        held=True;depths[key]=1
        yield
    finally:
        depths.pop(key,None)
        if held:
            if os.name=='nt':
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        if handle is not None:handle.close()
        lock.release()


def atomic_private_json(path,value):
    path=Path(path)
    if path.is_symlink():raise ValueError('Runtime state file is linked')
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as writer:
            json.dump(value,writer,allow_nan=False);writer.flush();os.fsync(writer.fileno())
        temporary.chmod(0o600);replace_file(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def command_sha256(arguments):
    return hashlib.sha256(json.dumps(arguments).encode()).hexdigest()




def inspection_command(arguments,state):
    entry=(len(arguments)>=3 and arguments[1:3]==['-m','backend.engine.inspection_service']) or (len(arguments)>=2 and arguments[1]=='--inspection-service')
    return (entry
        and arguments.count('--state-dir')==1 and arguments.index('--state-dir')+1<len(arguments)
        and arguments[arguments.index('--state-dir')+1]==str(Path(state)))


def _runtime_spawn_diagnostic(process,owner,arguments,state):
    """Bounded non-atomic observations; no raw argv values or authority grant."""
    def command(value):
        if type(value) not in (list,tuple) or any(type(item) is not str for item in value):
            return {'unavailable':'command_shape'}
        if len(value)>64 or any(len(item)>65536 for item in value):
            return {'unavailable':'command_bounds','argc':len(value)}
        vector=list(value)
        entry=('module' if len(vector)>=3 and vector[1:3]==['-m','backend.engine.inspection_service']
               else 'frozen' if len(vector)>=2 and vector[1]=='--inspection-service'
               else 'empty' if not vector else 'other')
        count=vector.count('--state-dir')
        state_matches=(count==1 and vector.index('--state-dir')+1<len(vector)
                       and vector[vector.index('--state-dir')+1]==str(Path(state)))
        flags={'-m','--inspection-service','--state-dir','--package','--runtime-root',
               '--release-policy','--require-approved-release','--device','--port',
               '--input-root','--camera-source','--camera-fps','--camera-width','--camera-height'}
        tokens=[]
        for index,item in enumerate(vector[:8]):
            if index and item in flags:
                tokens.append({'index':index,'flag':item})
            elif index==2 and entry=='module':
                tokens.append({'index':index,'module':'backend.engine.inspection_service'})
            else:
                tokens.append({'index':index,'sha256':hashlib.sha256(item.encode()).hexdigest()})
        return {'argc':len(vector),'sha256':command_sha256(vector),'entry':entry,
                'state_flag_count':count,'state_matches':state_matches,'tokens':tokens}

    def observe(read,kind):
        try:
            value=read()
        except BaseException as exc:
            unavailable=('ZombieProcess' if isinstance(exc,psutil.ZombieProcess)
                         else 'NoSuchProcess' if isinstance(exc,psutil.NoSuchProcess)
                         else 'AccessDenied' if isinstance(exc,psutil.AccessDenied)
                         else 'TimeoutExpired' if isinstance(exc,psutil.TimeoutExpired)
                         else 'observation_error')
            return {'unavailable':unavailable}
        if kind=='birth' and type(value) in (int,float) and 0<value<float('inf'):
            return value
        if kind=='status' and type(value) is str:
            statuses={'running','sleeping','disk-sleep','stopped','tracing-stop','zombie','dead',
                      'wake-kill','waking','parked','idle','locked','waiting','suspended'}
            return value if value in statuses else 'other'
        if kind=='poll' and (value is None or type(value) is int and -(1<<31)<=value<(1<<31)):
            return value
        return {'unavailable':'observation_shape'}

    pid=process.pid
    return {'schema':'modu-vision.runtime-spawn-observation/v1',
            'non_atomic':True,'ownership_verified':False,
            'expected':command(getattr(process,'args',None)),'observed':command(arguments),
            'process':{'pid':pid if type(pid) is int and 0<pid<(1<<63) else {'unavailable':'observation_shape'},
                       'birth':observe(lambda:owner.create_time(),'birth'),
                       'status':observe(lambda:owner.status(),'status'),
                       'poll_exit_code':observe(lambda:process.poll(),'poll')}}


def process_identity(process,state):
    owner=psutil.Process(process.pid);arguments=owner.cmdline()
    if not inspection_command(arguments,state):
        error=ValueError('Spawned runtime command differs from its owned state')
        try:
            diagnostic=_runtime_spawn_diagnostic(process,owner,arguments,state)
            note='Runtime spawn diagnostic: '+json.dumps(diagnostic,separators=(',',':'),allow_nan=False)
            if len(note.encode('utf-8'))<=4096:
                error.runtime_spawn_diagnostic=diagnostic
                if hasattr(error,'add_note'):error.add_note(note)
        except BaseException:
            pass  # Observation failure must preserve the already selected refusal.
        raise error
    return {'pid':process.pid,'process_created_at':owner.create_time(),'process_command_sha256':command_sha256(arguments)}


def owned_inspection_process(config,state):
    try:
        pid=config.get('pid');created=config.get('process_created_at')
        if type(pid)is not int or pid<=0 or not isinstance(created,(float,int)) or isinstance(created,bool):return None
        owner=psutil.Process(pid);arguments=owner.cmdline()
        if (abs(owner.create_time()-created)<.01 and inspection_command(arguments,state)
                and command_sha256(arguments)==config.get('process_command_sha256')):return owner
    except (psutil.Error,TypeError,ValueError):pass
    return None


def wait_for_owned_exit(process,timeout=12):
    """Non-child zombies have exited even while their parent retains wait status."""
    deadline=time.monotonic()+timeout
    while True:
        try:
            if process.status()==psutil.STATUS_ZOMBIE:return
            remaining=deadline-time.monotonic()
            if remaining<=0:raise psutil.TimeoutExpired(timeout)
            try:process.wait(timeout=min(.25,remaining));return
            except psutil.TimeoutExpired:
                if time.monotonic()>=deadline:raise
        except psutil.NoSuchProcess:return


def serialized_lifecycle(function):
    @wraps(function)
    def run(self,*args,**kwargs):
        with runtime_state_lock(self.root):
            path=getattr(self,'config_path',None) or self.path
            if path.is_symlink():raise ValueError('Runtime state file is linked')
            self.config=json.loads(path.read_text(encoding='utf-8'))
            return function(self,*args,**kwargs)
    return run

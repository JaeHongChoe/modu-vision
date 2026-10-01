"""Private runtime state publication and owned process lifecycle control."""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import psutil

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
        with os.fdopen(descriptor,'w') as writer:
            json.dump(value,writer,allow_nan=False);writer.flush();os.fsync(writer.fileno())
        temporary.chmod(0o600);os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def command_sha256(arguments):
    return hashlib.sha256(json.dumps(arguments).encode()).hexdigest()


def inspection_command(arguments,state):
    entry=(len(arguments)>=3 and arguments[1:3]==['-m','backend.engine.inspection_service']) or (len(arguments)>=2 and arguments[1]=='--inspection-service')
    return (entry
        and arguments.count('--state-dir')==1 and arguments.index('--state-dir')+1<len(arguments)
        and arguments[arguments.index('--state-dir')+1]==str(Path(state)))


def process_identity(process,state):
    owner=psutil.Process(process.pid);arguments=owner.cmdline()
    if not inspection_command(arguments,state):raise ValueError('Spawned runtime command differs from its owned state')
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


def serialized_lifecycle(function):
    @wraps(function)
    def run(self,*args,**kwargs):
        with runtime_state_lock(self.root):
            path=getattr(self,'config_path',None) or self.path
            if path.is_symlink():raise ValueError('Runtime state file is linked')
            self.config=json.loads(path.read_text())
            return function(self,*args,**kwargs)
    return run

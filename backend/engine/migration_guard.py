"""Cooperative project mutation admission, held through ASGI response completion."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path


_HELD=ContextVar("project_maintenance_admission",default=())


def _windows_admission(handle, exclusive):
    # The CRT locking API has no shared mode. LockFileEx permits overlapping
    # ordinary admissions while an exclusive migration still refuses them.
    import ctypes
    import msvcrt
    class Overlapped(ctypes.Structure):
        _fields_=[('Internal',ctypes.c_size_t),('InternalHigh',ctypes.c_size_t),
                  ('Offset',ctypes.c_uint32),('OffsetHigh',ctypes.c_uint32),
                  ('hEvent',ctypes.c_void_p)]
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    dword=ctypes.c_uint32;pointer=ctypes.POINTER(Overlapped)
    kernel.LockFileEx.argtypes=[ctypes.c_void_p,dword,dword,dword,dword,pointer]
    kernel.UnlockFileEx.argtypes=[ctypes.c_void_p,dword,dword,dword,pointer]
    kernel.LockFileEx.restype=kernel.UnlockFileEx.restype=ctypes.c_int32
    native=ctypes.c_void_p(msvcrt.get_osfhandle(handle.fileno()))
    overlapped=Overlapped()
    # Synchronous, immediate failure; byte zero may be beyond EOF. Writing an
    # initializer here would conflict with another request's shared lock.
    if not kernel.LockFileEx(native,1 | (2 if exclusive else 0),0,1,0,ctypes.byref(overlapped)):
        raise ctypes.WinError(ctypes.get_last_error())
    def release():
        if not kernel.UnlockFileEx(native,0,1,0,ctypes.byref(overlapped)):
            raise ctypes.WinError(ctypes.get_last_error())
    return release


@contextmanager
def maintenance_guard(root, *, exclusive=False):
    root=Path(root).expanduser()
    if not root.is_dir() or any(path.is_symlink() for path in (root,*root.parents)):
        raise ValueError('Maintenance admission requires an existing unlinked project')
    key=str(root.resolve())
    for prior in _HELD.get():
        if prior['key']==key and prior['active'] and (not exclusive or prior['exclusive']):
            yield
            return
    path=root/'migration_admission.lock'
    if path.is_symlink():raise ValueError('Maintenance admission lock cannot be linked')
    handle=os.fdopen(os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600),'r+b')
    held=False;token=None;ownership=None
    try:
        try:
            if os.name=='nt':
                unlock=_windows_admission(handle,exclusive)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            held=True
        except OSError as exc:raise ValueError('Project writers or migration hold maintenance admission; retry after drain') from exc
        ownership={'key':key,'active':True,'exclusive':exclusive}
        token=_HELD.set((*_HELD.get(),ownership))
        yield
    finally:
        try:
            if ownership:ownership['active']=False
            if token is not None:_HELD.reset(token)
            if held:
                if os.name=='nt':unlock()
                else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        finally:handle.close()


class ProjectMaintenanceMiddleware:
    # Migration handlers own admission; preview is read-only. Restore writes a
    # validated fresh destination and must work when the old project is missing.
    CONTROL_PATHS={'/api/project/compatibility/apply','/api/project/compatibility/recover',
                   '/api/project/compatibility/preview','/api/project/compatibility/global-preview',
                   '/api/project/restore'}
    def __init__(self,app,project_app):self.app=app;self.project_app=project_app
    async def __call__(self,scope,receive,send):
        # A fresh HTTP request must not inherit a caller/background admission capability.
        token=_HELD.set(())
        try:return await self._dispatch(scope,receive,send)
        finally:_HELD.reset(token)
    async def _dispatch(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method') not in {'POST','PUT','PATCH','DELETE'} or scope.get('path') in self.CONTROL_PATHS:
            return await self.app(scope,receive,send)
        project=scope.get('state',{}).get('scoped_project') or getattr(self.project_app.state,'current_project',None)
        if not project:return await self.app(scope,receive,send)
        from starlette.responses import JSONResponse
        # Catch admission errors only; route exceptions retain their ordinary handling.
        guard=maintenance_guard(project['project_dir'])
        try:guard.__enter__()
        except ValueError as exc:return await JSONResponse({'detail':str(exc)},status_code=423)(scope,receive,send)
        try:await self.app(scope,receive,send)
        finally:guard.__exit__(None,None,None)

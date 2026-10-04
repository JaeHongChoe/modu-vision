"""Cooperative project mutation admission, held through ASGI response completion."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path


_HELD=ContextVar("project_maintenance_admission",default=())


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
                import msvcrt
                if os.fstat(handle.fileno()).st_size==0:handle.write(b'0');handle.flush()
                handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),(fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)|fcntl.LOCK_NB)
            held=True
        except OSError as exc:raise ValueError('Project writers or migration hold maintenance admission; retry after drain') from exc
        ownership={'key':key,'active':True,'exclusive':exclusive}
        token=_HELD.set((*_HELD.get(),ownership))
        yield
    finally:
        if ownership:ownership['active']=False
        if token is not None:_HELD.reset(token)
        if held:
            if os.name=='nt':handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
        handle.close()


class ProjectMaintenanceMiddleware:
    # These specific handlers perform their own exclusive admission; preview is read-only.
    CONTROL_PATHS={'/api/project/compatibility/apply','/api/project/compatibility/recover',
                   '/api/project/compatibility/preview','/api/project/compatibility/global-preview'}
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

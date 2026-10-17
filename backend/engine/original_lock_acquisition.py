"""Original reviewed flock acquisition/lifetime facts, not a kernel-held query.

Capture only immediately after the owner's successful flock. Official release
invalidates before unlock. An unobserved external in-process unlock is outside
this local observation; the final process/fence check retains its authority.
"""
from __future__ import annotations
import os
from pathlib import Path
import stat
import threading
import weakref

_BINDINGS=weakref.WeakKeyDictionary()


class LockAcquisitionError(ValueError):pass


class OriginalLockAcquisition:
    def __init__(self):raise LockAcquisitionError('Original acquisition must be captured by its owner')
    def __copy__(self):raise LockAcquisitionError('Original acquisition cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):raise LockAcquisitionError('Original acquisition cannot be serialized')

    @property
    def handle(self):return _BINDINGS[self]['handle']
    @property
    def original_owner(self):return _BINDINGS[self]['owner']

    def current(self,*,record=None):
        bound=_BINDINGS.get(self)
        if (bound is None or not bound['active'] or os.getpid()!=bound['pid']
                or bound['current']() is not bound['handle']
                or bound['handle'].closed or bound['handle'].fileno()!=bound['fd']
                or record is not None and record is not bound['record']):
            raise LockAcquisitionError('Original lock acquisition is released or substituted')
        if bound['record'] is not None:
            held=bound['record']
            if (not held['active'] or held['owner']!=bound['owner'] or held['key']!=bound['key']
                    or held['exclusive'] is not bound['exclusive'] or held.get('original_lock_observation') is not self):
                raise LockAcquisitionError('Original lock owner record differs')
        fd=os.fstat(bound['fd']);path=bound['path'].lstat()
        if (bound['path'].is_symlink() or _identity(fd)!=bound['identity']
                or (path.st_dev,path.st_ino)!=(fd.st_dev,fd.st_ino)):
            raise LockAcquisitionError('Original lock descriptor or path differs')
        return {'original_acquisition_retained':True,'official_release_observed':False,
                'kernel_lock_query_performed':False,'whole_writer_coverage':False,
                'process_tree_exit_verified':False,'can_release_launch_lease':False}

    def invalidate(self):
        bound=_BINDINGS.get(self)
        if bound is None:raise LockAcquisitionError('Original lock acquisition is unavailable')
        bound['active']=False
        # Remove the value's owner-record backreference; a weak key alone
        # would otherwise retain every completed ordinary admission.
        del _BINDINGS[self]


def _identity(st):return (st.st_dev,st.st_ino,st.st_mode,st.st_nlink,st.st_uid,st.st_gid)


def _capture_successful_acquisition(handle,path,*,owner,current,record=None,exclusive):
    # A narrow owner hook, never a later cache/JSON adoption interface.
    path=Path(path).absolute();fd=handle.fileno();info=os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or not callable(current)
            or not isinstance(owner,tuple) or owner[0]!=os.getpid()
            or owner[1] is not threading.current_thread()):
        raise LockAcquisitionError('Original successful acquisition binding is unavailable')
    observed=object.__new__(OriginalLockAcquisition)
    _BINDINGS[observed]={'pid':os.getpid(),'owner':owner,'handle':handle,'fd':fd,'path':path,
                         'key':str(path.parent.resolve()),'exclusive':exclusive,'current':current,
                         'record':record,'identity':_identity(info),'active':True}
    return observed

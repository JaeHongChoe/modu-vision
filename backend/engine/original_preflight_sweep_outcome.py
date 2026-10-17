"""Original startup sweep observations only; no epoch/admission/release authority."""
from __future__ import annotations
import os
from pathlib import Path
import stat
import threading
from backend.engine.startup_thread_lifetime import OriginalStartupThread


class SweepObservationError(ValueError):pass


def _inventory(root):
    root=Path(root).absolute()
    if any(path.is_symlink() for path in (root,*root.parents)):
        raise SweepObservationError('Original sweep root inventory is linked')
    try:before=root.lstat()
    except FileNotFoundError:
        parent=root.parent.stat()
        if not stat.S_ISDIR(parent.st_mode) or root.name in os.listdir(root.parent):
            raise SweepObservationError('Original missing sweep root is uncertain')
        return (False,(),parent.st_dev,parent.st_ino)
    if not stat.S_ISDIR(before.st_mode):raise SweepObservationError('Original sweep root is not a directory')
    names=tuple(sorted(os.listdir(root)))
    if len(names)>10000:raise SweepObservationError('Original sweep inventory exceeds finite bound')
    after=root.lstat()
    if (before.st_dev,before.st_ino,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns):
        raise SweepObservationError('Original sweep inventory changed while observed')
    return (True,names,before.st_dev,before.st_ino)


class OriginalPreflightSweepOutcome:
    """Capture complete root facts before dispatch and the exact owned Thread/Event.

    Root separately binds this observation to the installation/generation and
    original startup admission. Complete empty facts never close a lease.
    """
    def __init__(self,root):
        self._root=Path(root).absolute();self._pid=os.getpid();self._creator=threading.current_thread()
        self._lock=threading.RLock();self._owner=None;self._thread=None;self._stop=None;self._owner_binding=None
        self._phase='captured';self._completed=False;self._uncertain=False;self._errors=[];self._initial=None
        self._binding=(self,self._root,self._pid,self._creator,self._lock)
        try:
            self._initial=_inventory(self._root)
            if self._initial[1]:self._uncertain=True
        except Exception as error:self.error(error,'initial_inventory_error')

    def __copy__(self):raise SweepObservationError('Original sweep observer cannot be copied')
    def __deepcopy__(self,memo):raise SweepObservationError('Original sweep observer cannot be copied')
    def __reduce_ex__(self,protocol):raise SweepObservationError('Original sweep observer cannot be serialized')
    @property
    def root(self):return self._root
    @property
    def original_owner(self):return self._owner
    @property
    def original_thread(self):return self._thread
    @property
    def original_stop(self):return self._stop

    def _original(self):
        return (self is self._binding[0] and self._root is self._binding[1] and self._pid==self._binding[2]
                and self._creator is self._binding[3] and self._lock is self._binding[4] and os.getpid()==self._pid)

    def _owner_original(self):
        return (self._owner_binding is not None and self._owner is self._owner_binding[0]
                and self._thread is self._owner_binding[1] and self._stop is self._owner_binding[2]
                and type(self._owner) is OriginalStartupThread and self._owner.thread is self._thread
                and self._owner.stop_event is self._stop and self._owner._role=='preflight_run_sweep'
                and self._owner._pid==self._pid and self._owner._binding==(self._pid,'preflight_run_sweep',self._thread,self._stop))

    def error(self,error,reason):
        with self._lock:
            self._uncertain=True;self._errors.append({'reason':reason,'type':type(error).__name__})

    def bind(self,owner):
        with self._lock:
            if (not self._original() or threading.current_thread() is not self._creator or self._phase!='captured'
                    or type(owner) is not OriginalStartupThread or owner._role!='preflight_run_sweep'
                    or owner._pid!=self._pid or owner._started or owner.thread.ident is not None):
                self._uncertain=True;raise SweepObservationError('Original sweep binding is unavailable or repeated')
            self._owner,self._thread,self._stop=owner,owner.thread,owner.stop_event
            self._owner_binding=(owner,owner.thread,owner.stop_event)
            if not self._owner_original():
                self._uncertain=True;raise SweepObservationError('Original sweep owner handle differs')
            self._phase='bound'

    def stage(self,root,stop):
        with self._lock:
            if (not self._original() or not self._owner_original() or self._phase!='bound'
                    or threading.current_thread() is not self._thread or not self._owner._started
                    or Path(root).absolute()!=self._root or stop is not self._stop):
                self._uncertain=True;raise SweepObservationError('Original sweep stage or caller differs')
            self._phase='running'
            try:
                if _inventory(self._root)!=self._initial:self._uncertain=True
            except Exception as error:self.error(error,'begin_inventory_error')
        return self

    def __enter__(self):return self
    def read_error(self,error):
        # A genuinely absent exact original root is the normal fresh-empty case.
        if isinstance(error,FileNotFoundError) and self._initial is not None and self._initial[0] is False:return
        self.error(error,'sweep_read_error')

    def __exit__(self,kind,error,traceback):
        with self._lock:
            if not self._original() or not self._owner_original() or threading.current_thread() is not self._thread:
                self._uncertain=True
            if error is not None:self.error(error,'raised_sweep_error')
            try:
                after=_inventory(self._root)
                if self._initial is None or after!=self._initial or after[1]:self._uncertain=True
            except Exception as fault:self.error(fault,'final_inventory_error')
            self._completed=error is None;self._phase='completed'
        return False

    def snapshot(self):
        with self._lock:
            original=self._original() and self._owner_original()
            if (not self._original() or self._owner_binding is not None and not self._owner_original()
                    or original and self._owner._failure is not None):self._uncertain=True
            completed=(original and self._completed and self._phase=='completed' and self._owner._started
                       and not self._thread.is_alive() and self._owner._failure is None)
            if completed:
                try:
                    after=_inventory(self._root)
                    if self._initial is None or after!=self._initial or after[1]:self._uncertain=True
                except Exception as fault:self.error(fault,'receipt_inventory_error')
            uncertain=self._uncertain
            return {'completed':completed,'uncertain':uncertain,
                    'empty_without_uncertainty':completed and not uncertain and self._initial is not None and not self._initial[1],
                    'initial_root_exists':None if self._initial is None else self._initial[0],
                    'initial_entry_count':None if self._initial is None else len(self._initial[1]),
                    'errors':[dict(row) for row in self._errors],
                    'whole_writer_coverage':False,'process_tree_exit_verified':False,'can_release_launch_lease':False}


class _Unobserved:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read_error(self,*args):pass
    def error(self,*args):pass


def original_sweep_stage(observer,root,stop):
    if observer is None:return _Unobserved()
    if type(observer) is not OriginalPreflightSweepOutcome:raise SweepObservationError('An original typed sweep observer is required')
    return observer.stage(root,stop)

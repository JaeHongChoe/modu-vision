"""Typed original startup observations; no admission, worker or release authority."""
from __future__ import annotations

from dataclasses import dataclass, field
import copy
import os
from pathlib import Path
import stat
import threading

ROLES = frozenset({'remote_recovery','local_recovery','local_journals','ledger_reconcile',
                   'import_recovery','import_orphans','backup_orphans','restore_orphans'})
STORE_TABLES = ('jobs','attempts','events','artifacts','cancel_intents','migrations')


class RecoveryObservationError(ValueError):
    pass


@dataclass
class _Outcome:
    role: str
    owner: object
    completed: bool = False
    observed: bool = False
    skipped: str | None = None
    counts: dict = field(default_factory=dict)
    uncertainties: set = field(default_factory=set)
    errors: list = field(default_factory=list)


def _inventory(path):
    path = Path(path).absolute()
    for item in (path,*path.parents):
        if item.is_symlink(): raise RecoveryObservationError('Original journal inventory is linked')
    try: held = path.lstat()
    except FileNotFoundError:
        parent = path.parent.stat()
        if not stat.S_ISDIR(parent.st_mode) or path.name in os.listdir(path.parent):
            raise RecoveryObservationError('Original missing journal inventory is uncertain')
        return (False, (), parent.st_dev, parent.st_ino)
    if not stat.S_ISDIR(held.st_mode):
        raise RecoveryObservationError('Original journal inventory is not a directory')
    names = tuple(sorted(os.listdir(path)))
    if len(names) > 10000:
        raise RecoveryObservationError('Original journal inventory exceeds its finite observation bound')
    after = path.lstat()
    if (held.st_dev,held.st_ino,held.st_mtime_ns,held.st_ctime_ns) != (after.st_dev,after.st_ino,after.st_mtime_ns,after.st_ctime_ns):
        raise RecoveryObservationError('Original journal inventory changed while read')
    return (True, names, held.st_dev, held.st_ino)


class RecoveryOutcomeObserver:
    """Retain original stage owners and sticky local completion/uncertainty data.

    Root must separately bind these facts to its original launch/generation and
    close all retained lifetimes. An empty outcome never releases a lease.
    """
    def __init__(self, owner, *, startup_observer=None):
        if startup_observer is not None:
            from backend.engine.original_fresh_startup import _is_original_startup
            if not _is_original_startup(startup_observer):
                raise RecoveryObservationError("An original startup observer is required")
        self._startup_observer = startup_observer
        self._owner = owner
        self._pid = os.getpid()
        self._thread = threading.current_thread()
        self._lock = threading.RLock()
        self._outcomes = []
        self._owners = {}
        self._threads = {}
        self._stores = {}
        self._folder_locks = {}
        self._uncertain = False
        self._binding = (self, owner, self._pid, self._thread, self._lock)

    def before_maintenance_start(self, manager, store, owner):
        if self._startup_observer is not None:
            self._startup_observer.bind_maintenance(manager, store, owner)

    def maintenance_outcome(self, manager, *, error=None, reason='maintenance_error'):
        if self._startup_observer is not None:
            with self._lock: self._uncertain = True
            self._startup_observer.maintenance_outcome(manager, error=error, reason=reason)

    def _original(self):
        return ((self, self._owner, self._pid, self._thread, self._lock) == self._binding
                and os.getpid() == self._pid and threading.current_thread() is self._thread)

    def _begin(self, role, owner):
        if role not in ROLES:
            with self._lock: self._uncertain = True
            raise RecoveryObservationError('Unknown startup recovery stage')
        with self._lock:
            row = _Outcome(role,owner)
            if not self._original():
                row.uncertainties.add('original_observer_context_changed')
            if role in self._owners:
                row.uncertainties.add('original_stage_replayed' if self._owners[role] is owner else 'original_stage_owner_changed')
            else: self._owners[role] = owner
            self._outcomes.append(row)
            self._uncertain |= bool(row.uncertainties)
            return _Stage(self,row)

    def _retained_owner_available(self):
        if (self is not self._binding[0] or self._owner is not self._binding[1]
                or self._pid != self._binding[2] or os.getpid()!=self._pid
                or self._thread is not self._binding[3] or self._lock is not self._binding[4]):
            raise RecoveryObservationError('Original recovery observer owner is unavailable')

    def original_thread(self, role):
        self._retained_owner_available()
        with self._lock: return self._threads.get(role)

    def original_owner(self, role):
        self._retained_owner_available()
        with self._lock: return self._owners.get(role)

    def original_store(self, role):
        self._retained_owner_available()
        with self._lock: return self._stores.get(role)

    def original_folder_lock(self, role):
        self._retained_owner_available()
        with self._lock: return self._folder_locks.get(role)

    def snapshot(self):
        with self._lock:
            rows = [{'role':r.role,'owner_identity':id(r.owner),'completed':r.completed,
                     'empty_without_uncertainty':r.completed and r.observed and not r.uncertainties,
                     'skipped':r.skipped,'counts':dict(r.counts),
                     'uncertainties':sorted(r.uncertainties),'errors':copy.deepcopy(r.errors)} for r in self._outcomes]
            original = (self is self._binding[0] and self._owner is self._binding[1]
                        and self._pid == self._binding[2] and self._thread is self._binding[3]
                        and self._lock is self._binding[4] and os.getpid() == self._pid)
            uncertain = not original or self._uncertain or any(r['uncertainties'] for r in rows)
            return {'outcomes':rows,'uncertain':uncertain,
                    'all_completed_empty':{r['role'] for r in rows} == ROLES and not uncertain and all(r['empty_without_uncertainty'] for r in rows),
                    'observed_roles':sorted({r['role'] for r in rows}),
                    'whole_writer_coverage':False,'process_tree_exit_verified':False,'can_release_launch_lease':False}


class _Stage:
    def __init__(self, observer, row):
        self._observer, self._row = observer,row
        self._inventories = []
        self._stores = []
        self._managers = []
        self._leases = []
        self._folder_locks = []

    def __enter__(self): return self

    def _uncertain(self, reason):
        with self._observer._lock:
            self._row.uncertainties.add(reason);self._observer._uncertain=True

    def error(self, error, reason='caught_recovery_error'):
        self._uncertain(reason)
        with self._observer._lock:
            self._row.errors.append({'reason':reason,'type':type(error).__name__})

    def skipped(self, reason):
        self._row.skipped=reason;self._uncertain('skipped_recovery')

    def records(self, rows, label='records'):
        self._row.observed=True;self._row.counts[label]=len(rows)
        if rows:self._uncertain('nonempty_records')

    def inventory(self, path):
        try:
            facts=_inventory(path);self._inventories.append((Path(path),facts))
            self._row.observed=True;self._row.counts['inventory_entries']=len(facts[1])
            if facts[1]:self._uncertain('nonempty_inventory')
            if any(not name.startswith('job_') or not name.endswith('.json') for name in facts[1]):
                self._uncertain('unknown_inventory_entry')
        except Exception as error:self.error(error,'inventory_observation_error')

    def _store_counts(self, store):
        with store._connect() as db:
            return {name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in STORE_TABLES}

    def store(self, store):
        try:
            counts=self._store_counts(store);self._stores.append((store,store.path,counts))
            with self._observer._lock:
                old=self._observer._stores.get(self._row.role)
                if old is not None and (old[0] is not store or old[1]!=store.path):self._uncertain('original_store_changed')
                else:self._observer._stores[self._row.role]=(store,store.path)
            self._row.observed=True;self._row.counts.update({'store_'+k:v for k,v in counts.items()})
            if any(counts.values()):self._uncertain('nonempty_store')
        except Exception as error:self.error(error,'store_observation_error')

    def manager(self, manager):
        try:
            rows=manager.list_jobs();self.records(rows,'manager_jobs')
            self._managers.append((manager,len(rows)))
            for name in ('_remote_queue','_local_waiting'):
                values=getattr(manager,name,())
                if values:self._uncertain('nonempty_manager_queue')
            for name in ('_queue_watcher','_local_watcher'):
                if getattr(manager,name,None) is not None:self._uncertain('existing_manager_observer')
        except Exception as error:self.error(error,'manager_observation_error')

    def leases(self, leases):
        try:
            with leases.connect() as db:count=db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]
            self._leases.append((leases,leases.path,count))
            self._row.observed=True;self._row.counts['leases']=count
            if count:self._uncertain('nonempty_leases')
        except Exception as error:self.error(error,'lease_observation_error')

    def retained_thread(self, role, thread):
        if type(thread) is not threading.Thread:
            self._uncertain('original_thread_unavailable');return
        with self._observer._lock:
            old=self._observer._threads.get(role)
            if old is not None and old is not thread:self._uncertain('original_thread_changed')
            else:self._observer._threads[role]=thread

    def folder_owner(self, handle):
        try:
            fd=handle.fileno();held=os.fstat(fd)
            if not stat.S_ISREG(held.st_mode):raise RecoveryObservationError('Original folder lock is not a file')
            identity=(held.st_dev,held.st_ino,held.st_mode,held.st_uid,held.st_gid,held.st_nlink)
            binding=(handle,fd,identity)
            self._folder_locks.append(binding)
            with self._observer._lock:
                old=self._observer._folder_locks.get(self._row.role)
                if old is not None and (old[0] is not handle or old[1:]!=binding[1:]):self._uncertain('original_folder_lock_changed')
                else:self._observer._folder_locks[self._row.role]=binding
        except Exception as error:self.error(error,'folder_owner_observation_error')

    def __exit__(self, kind, error, traceback):
        if not self._observer._original():self._uncertain('original_observer_context_changed')
        if error is not None:self.error(error,'raised_recovery_error')
        for path,facts in self._inventories:
            try:
                if _inventory(path)!=facts:self._uncertain('inventory_changed')
            except Exception as fault:self.error(fault,'inventory_observation_error')
        for store,path,counts in self._stores:
            try:
                after=self._store_counts(store)
                if store.path!=path or after!=counts:self._uncertain('store_changed')
            except Exception as fault:self.error(fault,'store_observation_error')
        for manager,count in self._managers:
            try:
                if len(manager.list_jobs())!=count:self._uncertain('manager_changed')
                if any(getattr(manager,k,()) for k in ('_remote_queue','_local_waiting')):self._uncertain('nonempty_manager_queue')
                if any(getattr(manager,k,None) is not None for k in ('_queue_watcher','_local_watcher')):self._uncertain('existing_manager_observer')
            except Exception as fault:self.error(fault,'manager_observation_error')
        for leases,path,count in self._leases:
            try:
                with leases.connect() as db:after=db.execute('SELECT COUNT(*) FROM leases').fetchone()[0]
                if leases.path!=path or after!=count:self._uncertain('leases_changed')
            except Exception as fault:self.error(fault,'lease_observation_error')
        for handle,fd,identity in self._folder_locks:
            try:
                held=os.fstat(fd)
                if handle.fileno()!=fd or (held.st_dev,held.st_ino,held.st_mode,held.st_uid,held.st_gid,held.st_nlink)!=identity:
                    self._uncertain('original_folder_lock_changed')
            except Exception as fault:self.error(fault,'folder_owner_observation_error')
        with self._observer._lock:self._row.completed=error is None
        return False


class _Unobserved:
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def error(self,*args,**kwargs):pass
    def skipped(self,*args,**kwargs):pass
    def records(self,*args,**kwargs):pass
    def inventory(self,*args,**kwargs):pass
    def store(self,*args,**kwargs):pass
    def manager(self,*args,**kwargs):pass
    def leases(self,*args,**kwargs):pass
    def retained_thread(self,*args,**kwargs):pass
    def folder_owner(self,*args,**kwargs):pass


def recovery_stage(observer, role, owner):
    if observer is None:return _Unobserved()
    if type(observer) is not RecoveryOutcomeObserver:
        raise RecoveryObservationError('An original typed recovery observer is required')
    return observer._begin(role,owner)

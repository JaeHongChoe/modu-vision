"""Compose original startup local facts; never a clean writer/tree/lease grant.

Only reviewed owner/acquisition lifetimes are observed. There is no kernel
flock-held query and no adoption of a late cache/JSON result. Existing binding,
controller closing gate, original process exit and final fence remain required.
"""
from __future__ import annotations
import asyncio
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import quote
import weakref
from types import MethodType

_OBSERVATIONS=weakref.WeakKeyDictionary()
_FLAGS=('whole_writer_coverage','process_tree_exit_verified','can_release_launch_lease')


class FreshStartupError(ValueError):pass


def _is_original_startup(value):return type(value) is OriginalFreshStartupObservation and value in _OBSERVATIONS


def _bytes(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def _file(path):
    path=Path(path)
    if any(p.is_symlink() for p in (path,*path.parents)):raise FreshStartupError('Original startup path is linked')
    st=path.stat();return (st.st_dev,st.st_ino)


def _generation(root):
    from backend.engine.global_store_paths import active_generation,OWNER_FILE,POINTER_FILE
    active=active_generation(root)
    if active is None:raise FreshStartupError('Original owned database generation is unavailable')
    directory,pointer=active
    files=(root/OWNER_FILE,root/POINTER_FILE,directory/'.global-generation.json')
    return (directory,_bytes(pointer),_file(root),_file(directory),tuple((_file(p),p.read_bytes()) for p in files))


def _deadline(deadline):
    if deadline is not None and (type(deadline) not in (int,float) or not math.isfinite(deadline) or time.monotonic()>=deadline):
        raise FreshStartupError('Original startup settlement deadline expired')


@contextmanager
def _locked(lock,deadline):
    _deadline(deadline)
    acquired=lock.acquire() if deadline is None else lock.acquire(timeout=max(0,deadline-time.monotonic()))
    if not acquired:raise FreshStartupError('Original startup observation lock deadline expired')
    try:_deadline(deadline);yield
    finally:lock.release()


def _zero_tables(path,tables,deadline=None):
    """Read the retained original SQLite file, with no WAL setup or write PRAGMA.

    The original store object/path/file identity are separately retained. A
    fresh read-only connection avoids its 30-second mutable setup during the
    existing shorter preparation deadline, and observes the current WAL.
    """
    _deadline(deadline);before=_file(path)
    db=sqlite3.connect('file:'+quote(str(Path(path).absolute()),safe='/')+'?mode=ro',uri=True,timeout=0)
    try:
        if deadline is not None:
            db.execute('PRAGMA busy_timeout='+str(max(0,min(2147483647,int((deadline-time.monotonic())*1000)))))
            db.set_progress_handler(lambda:int(time.monotonic()>=deadline),100)
        counts={name:db.execute('SELECT COUNT(*) FROM '+name).fetchone()[0] for name in tables}
    finally:db.close()
    _deadline(deadline)
    if _file(path)!=before:raise FreshStartupError('Original startup store file changed')
    return counts


def _manager_empty(manager,lock,deadline=None):
    if manager._lock is not lock:raise FreshStartupError('Original startup manager lock changed')
    with _locked(lock,deadline):
        if manager._lock is not lock or manager._jobs or manager._active_job_id is not None:
            raise FreshStartupError('Original startup manager is nonempty or substituted')
        if any(getattr(manager,k) for k in ('_remote_queue','_local_waiting')) or any(getattr(manager,k) is not None for k in ('_queue_watcher','_local_watcher')):
            raise FreshStartupError('Original startup manager has recovered observers or queued work')


class OriginalFreshStartupObservation:
    def __init__(self):raise FreshStartupError('An original startup observation must be minted from its live cache')
    def __copy__(self):raise FreshStartupError('Original startup observation cannot be copied')
    def __deepcopy__(self,memo):return self.__copy__()
    def __reduce_ex__(self,protocol):raise FreshStartupError('Original startup observation cannot be serialized')

    def poison(self,reason,deadline=None):
        row=_OBSERVATIONS.get(self)
        if row is None:raise FreshStartupError('Original startup observation is unavailable')
        row['uncertain'].add(reason)
        admission=row['admission']
        if deadline is None: admission.uncovered('startup_background_protocols')
        else:
            with admission._startup_condition(deadline,row['condition']):
                admission._unsupported.add('startup_background_protocols')

    def lifespan_ended(self):
        row=_OBSERVATIONS[self]
        if row['phase']!='settled':self.poison('lifespan_ended_before_original_settlement')

    def _fresh(self,deadline=None):
        from backend.engine import application_launch_handshake as hs
        from backend.engine import job_store
        row=_OBSERVATIONS.get(self);_deadline(deadline)
        if row is None:raise FreshStartupError('Original startup observation is unavailable')
        for owner,name,function in row['methods']:
            method=getattr(owner,name,None)
            if type(method) is not MethodType or method.__self__ is not owner or method.__func__ is not function:
                raise FreshStartupError('Original startup observation method changed')
        current_context=(row['pid'],row['creator'],row['task'],row['loop'],row['manager_lock'])
        if (current_context[0]!=row['original_context'][0] or any(current_context[i] is not row['original_context'][i] for i in (1,2,3,4))
                or row['loop'].is_closed() or row['task'].done()):
            raise FreshStartupError('Original startup task, loop or retained owner changed')
        cache=row['cache'];admission=row['admission'];manager=row['manager'];store=row['store'];leases=row['leases']
        if (os.getpid()!=row['pid'] or hs._CACHE is not cache or hs._context()!=row['context_value']
                or cache.get('fresh_startup_observation') is not self or cache.get('admission') is not admission
                or admission._condition is not row['condition'] or admission._activity_observer is not row['activity']
                or not row['activity']._original(admission) or admission._startup_pending is not row['pending_map']
                or cache.get('root') is not row['root'] or cache.get('context') is not row['context']
                or cache.get('socket') is not row['socket'] or cache.get('proof') is not row['proof']
                or cache.get('challenge') is not row['challenge'] or _bytes(cache['proof'])!=row['proof_bytes']
                or _bytes(cache['challenge'])!=row['challenge_bytes'] or cache.get('writer_guard') is not row['writer_guard']
                or cache.get('writer_handle') is not row['writer_handle'] or row['writer_handle'].pass_fds is not row['writer_fds']
                or cache.get('writer_private_fd')!=row['private_fd'] or cache.get('writer_fd_identity')!=row['writer_fd_identity']
                or tuple(hs._shutdown_fd(fd) for fd in row['writer_fds'])!=row['fd_facts']
                or hs._shutdown_fd(row['private_fd'])!=row['private_facts']
                or row['app'].state.owned_work_admission is not admission or self.recovery is not row['recovery']
                or self.recovery._startup_observer is not self or self.recovery._binding is not row['recovery_binding']
                or self.recovery._lock is not row['recovery_binding'][4]
                or self.sweep is not row['sweep'] or self.sweep._binding is not row['sweep_binding']
                or self.sweep._lock is not row['sweep_binding'][4] or manager._maintenance_owners is not row['maintenance_owners']
                or manager._leases is not leases or job_store._shared is not store
                or store.path is not row['store_path'] or leases.path is not row['lease_path']
                or _file(store.path)!=row['store_identity'] or _file(leases.path)!=row['lease_identity']
                or _generation(row['root'])!=row['generation']):
            raise FreshStartupError('Original startup cache, generation, owner or descriptor changed')
        row['shared_lock'].current(record=row['shared_record'])
        _manager_empty(manager,row['manager_lock'],deadline)
        _deadline(deadline)
        return row

    def bind_maintenance(self,manager,store,owner):
        from backend.engine.startup_thread_lifetime import OriginalStartupThread
        row=_OBSERVATIONS[self]
        if (manager is not row['manager'] or store is not row['store'] or row['maintenance'] is not None
                or threading.current_thread() is not row['creator'] or asyncio.current_task() is not row['task']
                or type(owner) is not OriginalStartupThread or owner._role!='job_ledger_maintenance'
                or owner._started or owner.thread.ident is not None or manager._maintenance is not owner.thread
                or manager._maintenance_owners!=[owner] or owner.stop_event is not manager._shutdown):
            self.poison('original_maintenance_start_changed');return
        row['maintenance']=(owner,owner.thread,owner.stop_event)

    def maintenance_outcome(self,manager,*,error=None,reason='maintenance_error'):
        row=_OBSERVATIONS[self];held=row['maintenance']
        if (held is None or manager is not row['manager'] or threading.current_thread() is not held[1]
                or os.getpid()!=row['pid']):
            self.poison('unknown_maintenance_observation');return
        self.poison('original_'+reason)

    def bind_sweep(self,owner):
        row=self._fresh()
        if threading.current_thread() is not row['creator'] or asyncio.current_task() is not row['task']:
            raise FreshStartupError('Original sweep must bind in its startup task')
        self.sweep.bind(owner)

    def recovery_ready(self):
        row=self._fresh()
        from backend.api.routes_training import _original_folder_lock_observation
        held=_original_folder_lock_observation(row['store_path'].parent)
        if held is None:self.poison('original_folder_acquisition_missing')
        else:row['folder']=held;held.current()
        if row['maintenance'] is None:self.poison('original_maintenance_start_missing')
        callbacks=tuple(row['admission']._startup_joins.items())
        if {role for role,_ in callbacks}!={'job_ledger_maintenance','preflight_run_sweep','telemetry_broadcaster'}:
            self.poison('original_startup_join_roles_missing')
        row['callbacks']=callbacks;row['phase']='recovered'

    def settle(self,absolute_deadline,reader,receipts):
        row=self._fresh(absolute_deadline)
        if (row['phase']!='recovered' or not row['admission']._startup_join_preparing
                or row['admission']._closed or reader is None or reader is row['shared_record']):
            raise FreshStartupError('Original startup settlement phase or separate reader admission differs')
        from backend.engine.migration_guard import _admission_capability
        if _admission_capability(row['root'],exclusive=False) is not reader:
            raise FreshStartupError('Original startup settlement reader changed')
        reader_lock=reader.get('original_lock_observation')
        if reader_lock is None:raise FreshStartupError('Original execution reader acquisition is unavailable')
        reader_lock.current(record=reader)
        with row['admission']._startup_condition(absolute_deadline,row['condition']):
            if tuple(row['admission']._startup_joins.items())!=row['callbacks']:
                raise FreshStartupError('Original startup join callbacks changed')
        if len(receipts)!=3 or any(type(r) is not dict or any(r.get(k) is not False for k in _FLAGS) for r in receipts):
            raise FreshStartupError('Original startup join local receipts differ')
        for (role,_),receipt in zip(row['callbacks'],receipts):
            if role=='telemetry_broadcaster':
                if receipt.get('idle') is not True or receipt.get('supported') is not True:raise FreshStartupError('Original telemetry did not settle')
            elif receipt.get('role')!=role or receipt.get('original_thread_joined') is not True:
                raise FreshStartupError('Original startup thread did not join')
        with _locked(self.recovery._lock,absolute_deadline):
            recovery=self.recovery.snapshot()
            maintenance_thread=self.recovery.original_thread('job_ledger_maintenance')
            retained_stores={role:self.recovery.original_store(role) for role in ('ledger_reconcile','import_recovery')}
            retained_folders={role:self.recovery.original_folder_lock(role) for role in ('ledger_reconcile','import_recovery')}
        with _locked(self.sweep._lock,absolute_deadline):sweep=self.sweep.snapshot()
        if not recovery['all_completed_empty']:self.poison('original_recovery_not_empty',absolute_deadline)
        if not sweep['empty_without_uncertainty']:self.poison('original_sweep_not_empty',absolute_deadline)
        owner,thread,stop=row['maintenance'] if row['maintenance'] is not None else (None,None,None)
        if (owner is None or owner.thread is not thread or owner.stop_event is not stop or thread.is_alive()
                or owner._failure is not None or row['manager']._maintenance is not thread
                or row['manager']._maintenance_owners!=[owner]):
            raise FreshStartupError('Original maintenance owner did not settle')
        if maintenance_thread is not thread:
            raise FreshStartupError('Original recovery maintenance identity differs')
        for role in ('ledger_reconcile','import_recovery'):
            retained=retained_stores[role]
            if retained is None or retained[0] is not row['store'] or retained[1]!=row['store_path']:
                raise FreshStartupError('Original recovery store object differs')
            facts=retained_folders[role]
            if row['folder'] is None or facts is None or facts[0] is not row['folder'].handle:
                raise FreshStartupError('Original recovery folder object differs')
        row['folder'].current()
        from backend.engine.startup_recovery_outcomes import STORE_TABLES,_inventory
        for path,facts in row['journals']:
            if _inventory(path)!=facts:self.poison('original_journal_changed',absolute_deadline)
        if any(_zero_tables(row['store_path'],STORE_TABLES,absolute_deadline).values()):self.poison('original_store_nonempty',absolute_deadline)
        if any(_zero_tables(row['lease_path'],('leases',),absolute_deadline).values()):self.poison('original_leases_nonempty',absolute_deadline)
        self._fresh(absolute_deadline)
        return {'supported':not row['uncertain'],'uncertainties':sorted(row['uncertain']),
                'all_original_startup_local_lifetimes_settled':True,'kernel_lock_query_performed':False,
                **{k:False for k in _FLAGS}}


def _begin_from_original_cache(cache,app,manager,telemetry):
    from backend.engine import application_launch_handshake as hs
    from backend.engine.migration_guard import _admission_capability
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from backend.engine.startup_recovery_outcomes import RecoveryOutcomeObserver,STORE_TABLES,_inventory
    from backend.engine.original_preflight_sweep_outcome import OriginalPreflightSweepOutcome
    from backend.engine import job_store
    from backend.engine.local_training_worker import _index
    from backend.remote.coordinator import _journal_index
    from backend.engine.worker_preflight import default_runs_root
    if (cache is not hs._CACHE or cache.get('ready') is not False or cache.get('fresh_startup_attempted')
            or type(cache.get('admission')) is not hs.BackendWorkAdmission):
        raise FreshStartupError('Original startup cache is unavailable or replayed')
    cache['fresh_startup_attempted']=True
    admission=cache['admission'];shared=_admission_capability(cache['root'],exclusive=False)
    if (app.state.owned_work_admission is not admission or shared is None or shared.get('original_lock_observation') is None
            or type(admission._activity_observer) is not OriginalRequestActivityObserver
            or admission._active or admission._activity_sequence or admission._unsupported or admission._closed
            or admission._startup_joins or cache.get('writer_guard') is None or cache.get('writer_handle') is None
            or cache.get('writer_private_fd') is None or manager._maintenance is not None or manager._maintenance_owners):
        raise FreshStartupError('Original first empty startup binding is unavailable')
    generation=_generation(cache['root']);directory,pointer,*_=generation
    if (cache['challenge']['binding']['database_pointer']!=json.loads(pointer)
            or Path(cache['challenge']['binding']['database_generation_path'])!=directory):
        raise FreshStartupError('Original startup generation differs from its challenge')
    store=job_store.ledger();leases=manager._leases
    if store.path!=directory/'jobs/ledger.sqlite3' or leases.path!=directory/'resource_leases.sqlite3':
        raise FreshStartupError('Original startup stores differ from admitted generation')
    _manager_empty(manager,manager._lock)
    if any(_zero_tables(store.path,STORE_TABLES).values()) or any(_zero_tables(leases.path,('leases',)).values()):
        raise FreshStartupError('Original startup store or lease history is nonempty')
    journals=tuple((path,_inventory(path)) for path in (_index(),_journal_index()))
    if any(facts[1] for _,facts in journals):raise FreshStartupError('Original startup journal inventory is nonempty')
    held=shared['original_lock_observation'];held.current(record=shared)
    observed=object.__new__(OriginalFreshStartupObservation)
    row={'cache':cache,'admission':admission,'condition':admission._condition,'pending_map':admission._startup_pending,
         'pid':os.getpid(),'creator':threading.current_thread(),'task':asyncio.current_task(),'loop':asyncio.get_running_loop(),
         'root':cache['root'],'context':cache['context'],'context_value':hs._context(),'socket':cache['socket'],
         'proof':cache['proof'],'challenge':cache['challenge'],'proof_bytes':_bytes(cache['proof']),'challenge_bytes':_bytes(cache['challenge']),
         'writer_guard':cache['writer_guard'],'writer_handle':cache['writer_handle'],'writer_fds':cache['writer_handle'].pass_fds,
         'private_fd':cache['writer_private_fd'],'writer_fd_identity':cache['writer_fd_identity'],
         'fd_facts':tuple(hs._shutdown_fd(fd) for fd in cache['writer_handle'].pass_fds),'private_facts':hs._shutdown_fd(cache['writer_private_fd']),
         'generation':generation,'shared_record':shared,'shared_lock':held,'app':app,'manager':manager,'manager_lock':manager._lock,'maintenance_owners':manager._maintenance_owners,
         'store':store,'store_path':store.path,'store_identity':_file(store.path),'leases':leases,'lease_path':leases.path,'lease_identity':_file(leases.path),
         'telemetry':telemetry,'journals':journals,'maintenance':None,'folder':None,'callbacks':(),
         'uncertain':set(),'phase':'captured','token':None}
    _OBSERVATIONS[observed]=row
    observed.recovery=RecoveryOutcomeObserver(app,startup_observer=observed)
    observed.sweep=OriginalPreflightSweepOutcome(default_runs_root())
    row['recovery']=observed.recovery;row['sweep']=observed.sweep
    row['activity']=admission._activity_observer
    row['recovery_binding']=observed.recovery._binding;row['sweep_binding']=observed.sweep._binding
    row['original_context']=(row['pid'],row['creator'],row['task'],row['loop'],row['manager_lock'])
    row['methods']=tuple((owner,name,getattr(owner,name).__func__) for owner,names in (
        (observed,('_fresh','poison','lifespan_ended','bind_maintenance','maintenance_outcome','bind_sweep','recovery_ready','settle')),
        (observed.recovery,('snapshot','before_maintenance_start','maintenance_outcome','original_store','original_thread','original_folder_lock')),
        (observed.sweep,('snapshot','bind'))) for name in names)
    cache['fresh_startup_observation']=observed
    admission.begin_original_startup(observed)
    return observed


def _claim_pending(observed,admission):
    if not _is_original_startup(observed):raise FreshStartupError('An original typed startup observation is required')
    row=observed._fresh()
    if (row['admission'] is not admission or row['phase']!='captured' or row['token'] is not None
            or threading.current_thread() is not row['creator'] or asyncio.current_task() is not row['task']
            or asyncio.get_running_loop() is not row['loop']):
        raise FreshStartupError('Original startup token is unavailable or replayed')
    token=object();row['token']=token;row['phase']='pending'
    return token


def _pending_token(observed,admission):
    if not _is_original_startup(observed):raise FreshStartupError('An original typed startup observation is required')
    row=_OBSERVATIONS[observed]
    if row['admission'] is not admission:return None
    return row['token']


def _mark_settled(observed):_OBSERVATIONS[observed]['phase']='settled'


def _settle_original(observed,deadline,reader,receipts):
    if not _is_original_startup(observed):raise FreshStartupError('An original typed startup observation is required')
    observed._fresh(deadline)
    return observed.settle(deadline,reader,receipts)

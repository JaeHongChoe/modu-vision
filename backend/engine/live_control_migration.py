"""Opt-in live basic and SSH training observers in an owned POSIX installation.

This path is deliberately separate from drained/historical migration. Compute
outputs remain at their original registered paths. Only cooperative short
control mutations wait for the exclusive snapshot and pointer transaction.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import uuid

from backend.engine.global_store_paths import owned_root, active_generation, resolve_store_path, store_admission
from backend.engine.migration_guard import maintenance_guard
from backend.engine.runtime_process_control import atomic_private_json


def validate_adoptions(value,installation_id):
    fields={'job_id','lease_owner','attempt_fence','attempt_number','owner_pid','owner_created_at',
        'owner_command_sha256','spec_sha256','output_dir','reserved','uncertain'}
    if (not isinstance(value,dict) or set(value)!={'protocol_version','installation_id','workers'}
            or type(value['protocol_version']) is not int or value['protocol_version'] not in (1,2,3)
            or value['installation_id']!=installation_id or not isinstance(value['workers'],list)
            or not 1<=len(value['workers'])<=1000):raise ValueError('Invalid live adoption descriptor')
    seen=set()
    for row in value['workers']:
        expected=fields
        if value['protocol_version'] in (2,3):
            kind=row.get('worker_kind') if isinstance(row,dict) else None
            kinds={'local_basic','remote_training'}|({'local_specialist'} if value['protocol_version']==3 else set())
            if kind not in kinds:raise ValueError('Invalid live control worker kind')
            expected=fields|{'worker_kind'}
            if kind=='remote_training':
                expected|={'remote_profile_sha256','remote_handle_sha256'}
                if any(not isinstance(row.get(key),str) or not re.fullmatch('[a-f0-9]{64}',row[key])
                       for key in ('remote_profile_sha256','remote_handle_sha256')):
                    raise ValueError('Invalid remote live launch binding')
            if kind=='local_specialist':
                expected|={'native_control_sha256'}
                if not isinstance(row.get('native_control_sha256'),str) or not re.fullmatch('[a-f0-9]{64}',row['native_control_sha256']):
                    raise ValueError('Invalid native live closure binding')
        native=value['protocol_version']==3 and isinstance(row,dict) and row.get('worker_kind')=='local_specialist'
        if (not isinstance(row,dict) or set(row)!=expected or not isinstance(row.get('job_id'),str)
                or not re.fullmatch(r'[a-f0-9]{32}' if native else r'job_[A-Za-z0-9_-]{1,123}',row['job_id']) or row['job_id'] in seen
                or not isinstance(row['lease_owner'],str) or not 1<=len(row['lease_owner'])<=256
                or any(type(row[key]) is not int or row[key]<1 for key in ('attempt_fence','attempt_number','owner_pid'))
                or type(row['owner_created_at']) not in (int,float) or not math.isfinite(row['owner_created_at']) or row['owner_created_at']<=0
                or any(not isinstance(row[key],str) or not re.fullmatch('[a-f0-9]{64}',row[key]) for key in ('owner_command_sha256','spec_sha256'))
                or not isinstance(row['output_dir'],str) or not Path(row['output_dir']).is_absolute()
                or any(type(row[key]) is not bool for key in ('reserved','uncertain'))):
            raise ValueError('Invalid live adoption worker binding')
        seen.add(row['job_id'])


def _current_adoptions(root):
    current=active_generation(root)
    if current is None:return None,None
    seal=json.loads((current[0]/'.global-generation.json').read_bytes())
    return current,seal.get('live_adoptions')


@contextmanager
def lease_admission(leases):
    root,owner=owned_root(leases.path)
    if root is None:
        with store_admission(leases.path):yield
        return
    with maintenance_guard(root,wait=True):
        target=resolve_store_path(root/owner['scopes']['leases'])
        if leases.path!=target:
            if leases.path!=root/owner['scopes']['leases']:
                raise ValueError('Only the original declared lease store can follow a live adoption')
            _,adoptions=_current_adoptions(root)
            if not adoptions or not any(row['lease_owner']==leases.owner for row in adoptions['workers']):
                raise ValueError('Lease owner has no live adoption into this generation')
            leases.path=target
            leases._live_continuation=True
        with store_admission(leases.path):yield


@contextmanager
def journal_admission(journal):
    root,_=owned_root(Path(journal['output_dir']))
    if root is None or journal.get('global_control_protocol')!=1:
        yield
    else:
        configured=os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR')
        if not configured or Path(configured).absolute()!=root:
            raise ValueError('Cooperative journal index differs from its original installation')
        with maintenance_guard(root,wait=True):
            if 'control_owner' in journal:
                from backend.engine.live_remote_control import remote_journal_fence
                remote_journal_fence(root,journal)
            yield


class _ObserverStore:
    """Hold admission through each original observer's complete store call."""
    def __init__(self,store,job_id,fence):
        self._store,self._job_id,self._fence=store,job_id,fence

    def __getattr__(self,name):
        value=getattr(self._store,name)
        if not callable(value):return value
        def call(*args,**kwargs):
            root,owner=owned_root(self._store.path)
            with maintenance_guard(root,wait=True):
                target=resolve_store_path(root/owner['scopes']['ledger'])
                if self._store.path!=target:
                    if self._store.path!=root/owner['scopes']['ledger']:
                        raise ValueError('Only the original declared ledger can follow a live adoption')
                    _,adoptions=_current_adoptions(root)
                    carried=next((r for r in (adoptions or {}).get('workers',[]) if r['job_id']==self._job_id),None)
                    if carried is None or carried['attempt_fence']!=self._fence():
                        raise ValueError('Job observer has no matching live adoption fence')
                    from backend.engine.job_store import JobStore,StaleFencingToken
                    fresh=JobStore(target)
                    attempts=fresh.attempts(self._job_id)
                    if not attempts or attempts[-1]['fencing_token']!=self._fence():
                        raise StaleFencingToken('A newer job attempt owns this generation')
                    self._store=fresh
                return getattr(self._store,name)(*args,**kwargs)
        return call


def cooperative_observer_store(store,job_id,fence):
    path=getattr(store,'path',None)
    if not isinstance(path,(str,os.PathLike)):return store
    root,_=owned_root(path)
    return _ObserverStore(store,job_id,fence) if root is not None and os.name!='nt' else store


def _inspect_workers(root,owner):
    from backend.engine.global_migration import _read_db
    from backend.engine.local_training_worker import _owned, _owned_members
    from backend.engine.terminal_runtime_history import registered_model_output, _read
    scopes=owner['scopes'];workers=[];errors=[]
    with _read_db(root/scopes['ledger']) as db:
        db.row_factory=sqlite3.Row
        jobs=[dict(r) for r in db.execute("SELECT * FROM jobs WHERE state NOT IN ('completed','failed','aborted','cancelled','canceled','interrupted','error','expired','rejected','succeeded')")]
        attempts=[dict(r) for r in db.execute('SELECT * FROM attempts WHERE ended_ns IS NULL')]
    with _read_db(root/scopes['leases']) as db:
        db.row_factory=sqlite3.Row;reservations=[dict(r) for r in db.execute('SELECT * FROM leases')]
    with _read_db(root/scopes['context']) as db:
        locations=list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations'))
    for row in jobs:
        try:
            identifier=row['id']
            if row['kind']=='specialist_training':
                active=[a for a in attempts if a['job_id']==identifier]
                if len(active)!=1:raise ValueError('Native live worker must have one open original attempt')
                claims=[r for r in reservations if r['job_id']==identifier]
                from backend.engine.live_specialist_control import inspect_native_worker
                workers.append(inspect_native_worker(root,owner,row,active[0],claims,locations))
                continue
            if not isinstance(identifier,str) or not re.fullmatch(r'job_[A-Za-z0-9_-]{1,123}',identifier):
                raise ValueError('Live job identity is not canonical')
            if row['kind']!='training' or row['state'] not in {'running','stopping','detached','disconnected'} or row['source']!='api':
                raise ValueError('Only an already launched current training job can participate')
            output=Path(row['output_dir']);index=root/scopes['local_journals']/(identifier+'.json')
            if not output.is_absolute() or output!=output.resolve() or not output.is_relative_to(root):raise ValueError('Live output must stay in the original installation')
            matches=[r for r in locations if output.is_relative_to(Path(r[3]))]
            if len(matches)!=1:raise ValueError('Live output lacks its original registered project')
            key,workspace,project,directory=matches[0]
            metadata,_=_read(root,Path(directory)/'project.json')
            active=[a for a in attempts if a['job_id']==identifier]
            if len(active)!=1:raise ValueError('Live worker must have one open original attempt')
            claims=[r for r in reservations if r['job_id']==identifier]
            remote_index=root/scopes['remote_journals']/(identifier+'.json')
            if remote_index.exists() or remote_index.is_symlink():
                if index.exists() or index.is_symlink():raise ValueError('Live job has conflicting local and remote controls')
                from backend.engine.live_remote_control import inspect_remote_worker
                journal,carried=inspect_remote_worker(root,owner,row,active[0],claims)
                if ((row['workspace_id'],row['project_key'],row['project_id'])!=(workspace,key,project)
                        or metadata.get('id')!=project or output!=registered_model_output(metadata.get('models_dir',''),journal)
                        or not Path(metadata.get('models_dir','')).is_relative_to(Path(directory))):
                    raise ValueError('Live remote worker namespace or model directory differs')
                workers.append(carried)
                continue
            for path in (output,index,output/'local_job.json',output/'local_spec.json'):
                if any(p.is_symlink() for p in (path,*path.parents)) or not path.exists():raise ValueError('Live controls are missing or linked')
            journal,index_raw=_read(root,index);spec_path=output/'local_spec.json';spec,spec_raw=_read(root,spec_path)
            _,run_raw=_read(root,output/'local_job.json')
            if index_raw!=run_raw:raise ValueError('Live recovery journal copies differ')
            if (journal.get('global_control_protocol')!=1 or spec.get('global_control_protocol')!=1
                    or journal.get('protocol_version')!=1 or spec.get('protocol_version')!=1):
                raise ValueError('Worker does not support cooperative global control protocol')
            if (journal.get('job_id')!=identifier or spec.get('job_id')!=identifier
                    or journal.get('output_dir')!=str(output) or spec.get('output_dir')!=str(output)
                    or journal.get('spec_path')!=str(spec_path)
                    or journal.get('spec_sha256')!=hashlib.sha256(spec_raw).hexdigest()
                    or (spec.get('task'),spec.get('preset'))!=(journal.get('task'),journal.get('preset'))
                    or spec.get('lease_path')!=str(root/scopes['leases']) or not spec.get('lease_owner')):
                raise ValueError('Live immutable specification or lease origin differs')
            ready,_=_read(root,output/'local_control_ready.json')
            expected={'protocol_version':1,'installation_id':owner['installation_id'],'job_id':identifier,
                'spec_sha256':journal['spec_sha256'],'lease_owner':spec['lease_owner'],
                'owner_pid':journal['owner_pid'],'owner_created_at':journal['owner_created_at'],
                'owner_command_sha256':journal['owner_command_sha256']}
            if ready!=expected:raise ValueError('Worker has not acknowledged this cooperative control protocol and identity')
            if ((row['workspace_id'],row['project_key'],row['project_id'])!=(workspace,key,project)
                    or metadata.get('id')!=project or output!=registered_model_output(metadata.get('models_dir',''),journal)
                    or not Path(metadata.get('models_dir','')).is_relative_to(Path(directory))):
                raise ValueError('Live worker namespace or model directory differs')
            process=_owned(journal);members=_owned_members(journal)
            if process is None or not members or process.pid not in {p.pid for p in members}:
                raise ValueError('Live process identity/session/token could not be proven')
            if any(r['owner']!=spec['lease_owner'] or r['fence']!=active[0]['fencing_token'] or r['remote'] or r['app_schema']!=2 for r in claims):
                raise ValueError('Live reservation owner/fence/protocol differs')
            if journal.get('status') not in {'running','launched','launching'}:
                raise ValueError('Worker became terminal or unsupported; inspect again')
            workers.append({'job_id':identifier,'lease_owner':spec['lease_owner'],
                'attempt_fence':active[0]['fencing_token'],'attempt_number':active[0]['number'],
                'owner_pid':journal['owner_pid'],'owner_created_at':journal['owner_created_at'],
                'owner_command_sha256':journal['owner_command_sha256'],'spec_sha256':journal['spec_sha256'],
                'output_dir':str(output),'reserved':bool(claims),'uncertain':bool(claims and claims[0]['uncertain'])})
        except (ValueError,OSError,KeyError,TypeError) as exc:errors.append(row['id']+': '+str(exc))
    selected={w['job_id'] for w in workers}
    if any(a['job_id'] not in selected for a in attempts):errors.append('An open attempt lacks a proven cooperative live worker')
    if any(r['job_id'] not in selected for r in reservations):errors.append('A reservation lacks a proven live worker; reconcile it separately')
    if not workers:errors.append('No proven cooperative live worker; use drained migration instead')
    return workers,errors


def _view(root,owner):
    from backend.engine import global_migration as migration
    from backend.engine.migration_inventory import inventory
    workers,errors=_inspect_workers(root,owner)
    selected={r['job_id'] for r in workers};scopes=owner['scopes']
    live={scopes['ledger']:selected,scopes['leases']:selected}
    remote={r['job_id'] for r in workers if r.get('worker_kind')=='remote_training'}
    native={r['job_id'] for r in workers if r.get('worker_kind')=='local_specialist'}
    local=selected-remote-native
    live.update({scopes['local_journals']+'/'+identifier+'.json':{identifier} for identifier in local})
    live.update({scopes['remote_journals']+'/'+identifier+'.json':{identifier} for identifier in remote})
    outputs=[Path(r['output_dir']) for r in workers]
    files=[p for p in migration._files(root) if not any(p==d or p.is_relative_to(d) for d in outputs)]
    view=inventory(root,kind='owned_global_live',paths=files,live_jobs=live)
    errors+=view['blockers'];known=migration._known_schemas(scopes)
    for name,relative in scopes.items():
        if name.endswith('_journals'):continue
        path=root/relative
        if not path.is_file():errors.append('Missing declared '+name+' scope')
        elif name in known and migration._schema(path)!=known[name]:errors.append('Live migration requires the exact current '+name+' schema')
        elif name=='profiles':
            from backend.remote.profiles import ProfileStore
            ProfileStore(path)._read()
    from backend.engine.terminal_runtime_history import journal_blockers
    if not errors:errors+=journal_blockers(root,scopes,live_jobs=local,live_remote_jobs=remote)
    if not errors:errors+=migration._authority_blockers(root,scopes)
    if active_generation(root):errors.append('Initial live cutover only; an active generation must be drained before forward migration')
    if any((root/name).exists() for name in ('application-active.json','application-update-pending.json')):
        errors.append('App/DB paired installations require their own drained update transaction')
    if remote or native:
        workers=[r if r['job_id'] in remote|native else {**r,'worker_kind':'local_basic'} for r in workers]
    adoption={'protocol_version':3 if native else 2 if remote else 1,'installation_id':owner['installation_id'],'workers':workers}
    if not errors:validate_adoptions(adoption,owner['installation_id'])
    source_hash=migration.digest({'inventory':view['source_snapshot']['sha256'],
        'owner_sha256':hashlib.sha256((root/migration.OWNER_FILE).read_bytes()).hexdigest(),'adoption':adoption})
    view.update(source_sha256=source_hash,installation_id=owner['installation_id'],live_adoptions=adoption,
        can_apply=not errors,blockers=sorted(set(errors)),schema_conversions={},
        ownership_policy='same owned installation, positively proven cooperative current workers only; no relaunch')
    return view


def preview_live(root):
    from backend.engine.global_migration import _owner
    root,owner=_owner(root)
    with store_admission(root,exclusive=True):return _view(root,owner)


def _publish(root,owner,identifier,seal,fence):
    from backend.engine import global_migration as migration
    atomic_private_json(root/migration.POINTER_FILE,{'schema_version':1,'installation_id':owner['installation_id'],
        'generation_id':identifier,'sealed_sha256':seal['sealed_sha256'],'fence':fence,
        'live_adoptions_sha256':migration.digest(seal['live_adoptions'])})
    migration._sync_directories(root,recursive=False)


def apply_live(root,*,expected_source_sha256,on_prepared=None):
    from backend.engine import global_migration as migration
    root,owner=migration._owner(root)
    with store_admission(root,exclusive=True):
        before=_view(root,owner)
        if before['source_sha256']!=expected_source_sha256:raise ValueError('Live source changed since preview')
        if not before['can_apply']:raise ValueError('; '.join(before['blockers']))
        identifier=uuid.uuid4().hex;directory=root/'.global-migrations'/identifier
        directory.mkdir(parents=True,exist_ok=False)
        backup=directory/'original';migration._copy(before,root,backup,scopes=owner['scopes'])
        staged=root/'.global-generations'/identifier;migration._copy(before,root,staged,scopes=owner['scopes'])
        # Exact current schemas need no constructor (which could reattach jobs).
        migration._revoke(staged,owner['scopes'])
        seal=migration._seal(staged,owner,identifier);seal['live_adoptions']=before['live_adoptions']
        atomic_private_json(staged/'.global-generation.json',seal);migration._sync_directories(staged)
        if _view(root,owner)['source_sha256']!=before['source_sha256']:raise ValueError('Live source or process changed during preparation')
        journal={'schema_version':1,'kind':'live','installation_id':owner['installation_id'],
            'migration_id':identifier,'status':'prepared','source_sha256':before['source_sha256'],
            'source_inventory':before['inventory'],'target_sha256':seal['sealed_sha256'],
            'backup_inventory':migration._snapshot(backup)['inventory'],'previous_pointer':None,
            'fence':1,'scopes':owner['scopes'],'live_adoptions_sha256':migration.digest(seal['live_adoptions'])}
        atomic_private_json(directory/'journal.json',journal);migration._sync_directories(directory)
        if on_prepared is not None:on_prepared(dict(journal))
        _publish(root,owner,identifier,seal,1)
        journal['status']='applied';atomic_private_json(directory/'journal.json',journal)
        return {'status':'applied','kind':'live','migration_id':identifier,'fence':1,
            'worker_count':len(seal['live_adoptions']['workers']),'workers_relaunched':0,
            'uncertain_reservations_cleared':0,'session_policy':'copied sessions revoked'}


def recover_live(root,identifier,*,action):
    from backend.engine import global_migration as migration
    root,owner=migration._owner(root)
    with store_admission(root,exclusive=True):
        path,record=migration._journal(root,identifier)
        if (record.get('kind')!='live' or record.get('schema_version')!=1 or record.get('installation_id')!=owner['installation_id']
                or type(record.get('fence')) is not int or record['fence']!=1 or record.get('previous_pointer') is not None
                or record.get('status') not in {'prepared','applied'}):
            raise ValueError('Not this installation live migration')
        current=active_generation(root)
        if action!='finish':raise ValueError('Live worker generations cannot be restored or replayed; drain and recover forward')
        staged=root/'.global-generations'/identifier
        if any(p.is_symlink() for p in (staged,staged.parent,staged/'.global-generation.json')):
            raise ValueError('Prepared live generation is linked')
        seal=json.loads((staged/'.global-generation.json').read_bytes())
        if (seal.get('installation_id')!=owner['installation_id'] or seal.get('generation_id')!=identifier
                or seal.get('sealed_sha256')!=record['target_sha256']
                or migration.digest(seal.get('live_adoptions'))!=record.get('live_adoptions_sha256')):
            raise ValueError('Prepared live binding changed')
        if current is None:
            if record['status']!='prepared':raise ValueError('Applied live pointer is missing; no authority replay')
            before=_view(root,owner)
            if not before['can_apply'] or before['source_sha256']!=record['source_sha256']:
                raise ValueError('Live source changed; prepare a fresh transaction')
            if migration.digest(migration._snapshot(staged)['inventory'])!=record['target_sha256']:
                raise ValueError('Prepared live files changed')
            _publish(root,owner,identifier,seal,record['fence'])
        elif (current[1]['generation_id']!=identifier or current[1]['fence']!=record['fence']
                or current[1]['sealed_sha256']!=record['target_sha256']):
            raise ValueError('Another generation is current')
        # Pointer publication is irreversible for live authority. Finishing its
        # receipt retains every current write, even if the worker already ended.
        migration._sync_directories(root,recursive=False)
        record['status']='applied';atomic_private_json(path,record)
        return {'status':'applied','kind':'live','migration_id':identifier,'current_writes_preserved':True}

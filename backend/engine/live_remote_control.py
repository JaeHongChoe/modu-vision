"""Original remote-training observer protocol for owned live control cutover.

The local observer acknowledges an already launched remote handle. Preview
independently proves the worker identity over read-only SSH. No handle recovery
in this module launches, signals, reattaches or clears an uncertain reservation.
"""
import hashlib
import os
from pathlib import Path

from backend.engine.global_store_paths import owned_root,active_generation
from backend.engine.migration_guard import maintenance_guard
from backend.engine.runtime_process_control import atomic_private_json,command_sha256


def digest(value):
    from backend.engine.job_store import spec_digest
    return spec_digest(value)


def _observer_binding(record,leases,owner,root,attempt):
    import psutil
    process=psutil.Process(os.getpid())
    return {'protocol_version':1,'installation_id':owner['installation_id'],'job_id':record.job_id,
        'lease_owner':leases.owner,'lease_path':str(root/owner['scopes']['leases']),
        'attempt_fence':record.ledger.fencing_token,'attempt_number':attempt['number'],
        'owner_pid':process.pid,'owner_created_at':process.create_time(),
        'owner_command_sha256':command_sha256(process.cmdline())}


def _prove_observer_process(binding):
    import psutil
    try:
        if type(binding.get('owner_pid')) is not int or binding['owner_pid']<1:
            raise ValueError('Original remote observer PID is invalid')
        process=psutil.Process(binding['owner_pid'])
        if (process.status()==psutil.STATUS_ZOMBIE or process.uids().real!=os.getuid()
                or process.create_time()!=binding.get('owner_created_at')
                or command_sha256(process.cmdline())!=binding.get('owner_command_sha256')):
            raise ValueError('Original remote observer process identity changed')
    except psutil.Error as exc:raise ValueError('Original remote observer process is unavailable') from exc


def bind_remote_control(record,leases):
    """Capture one original observer/attempt before its first remote launch."""
    root,owner=owned_root(record.output_dir)
    link=getattr(record,'ledger',None)
    if root is None or os.name=='nt' or not getattr(leases,'cooperative',False) or link is None:
        return None
    with maintenance_guard(root,wait=True):
        if active_generation(root) is not None:return None
        if Path(leases.path)!=root/owner['scopes']['leases'] or Path(link.store.path)!=root/owner['scopes']['ledger']:
            raise ValueError('Remote observer differs from original declared controls')
        row=link.store.record(record.job_id);attempts=link.store.attempts(record.job_id)
        if (row['kind']!='training' or row['source']!='api' or row['output_dir']!=str(Path(record.output_dir).absolute())
                or len(attempts)!=1 or attempts[0]['ended_ns'] is not None or attempts[0]['executor']!='remote'
                or attempts[0]['owner_pid']!=os.getpid() or attempts[0]['fencing_token']!=link.fencing_token):
            return None  # Recovered/older attempts remain subject to drain.
        if not leases.stamp_fence(record.job_id,link.fencing_token):
            raise ValueError('Original remote reservation could not be fenced')
        return _observer_binding(record,leases,owner,root,attempts[0])


def _remote_identity_matches(transport,profile,identifier,handle,spec_sha):
    """Docker's worker reports a short hostname; resolve both actual IDs."""
    recovered=transport.recover_handle(profile,identifier,job_id=identifier,operation='train',spec_sha256=spec_sha)
    if recovered is None:return False
    if recovered!=handle:
        import re
        if profile.runtime_kind!='docker' or not all(isinstance(h,str) and re.fullmatch('[a-fA-F0-9]{12,64}',h) for h in (recovered,handle)):
            return False
        result=transport.exec(profile,['docker','container','inspect','--format','{{.Id}} {{.Name}}',recovered,handle],timeout=10)
        lines=result.stdout.strip().splitlines()
        if result.returncode!=0 or len(lines)!=2 or lines[0]!=lines[1]:return False
        fields=lines[0].split()
        if len(fields)!=2 or not re.fullmatch('[a-fA-F0-9]{64}',fields[0]) or fields[1]!='/modu-vision-'+identifier:return False
    return transport.is_running(profile,identifier,handle) is True


def bind_remote_recovery(record,leases):
    """An ordinary authorized reattachment can observe the same carried worker.

    No new live adoption or remote launch is created. The existing ledger's
    higher fencing token and this fresh reservation owner must already exist.
    """
    root,owner=owned_root(record.output_dir);link=getattr(record,'ledger',None)
    if root is None or os.name=='nt' or link is None or not getattr(leases,'cooperative',False):return None
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.live_control_migration import _current_adoptions
    from backend.engine.terminal_runtime_history import _read
    from backend.remote.profiles import ComputeProfile,ProfileStore
    from backend.remote.ssh_transport import SSHTransport
    with maintenance_guard(root,wait=True):
        current,adoption=_current_adoptions(root)
        if current is None:return None
        carried=next((r for r in (adoption or {}).get('workers',[]) if r['job_id']==record.job_id and r.get('worker_kind')=='remote_training'),None)
        if carried is None:return None
        if Path(link.store.path)!=resolve_store_path(root/owner['scopes']['ledger']) or Path(leases.path)!=resolve_store_path(root/owner['scopes']['leases']):
            raise ValueError('Remote recovery must use fresh current-generation controls')
        attempts=link.store.attempts(record.job_id);attempt=attempts[-1] if attempts else {}
        if (attempt.get('fencing_token')!=link.fencing_token or link.fencing_token<=carried['attempt_fence']
                or attempt.get('worker_id')!='pid:'+str(os.getpid()) or attempt.get('ended_ns') is not None):
            raise ValueError('Remote recovery lacks the current backend reattachment fence')
        journal,_=_read(root,Path(record.output_dir)/'remote_job.json')
        profile=ComputeProfile.model_validate(journal['profile'])
        _,raw=_read(root,Path(record.output_dir)/'remote_spec.json')
        spec_sha=hashlib.sha256(raw).hexdigest();handle=journal['remote_handle']
        if (spec_sha!=carried['spec_sha256'] or digest(journal['profile'])!=carried['remote_profile_sha256']
                or hashlib.sha256(handle.encode()).hexdigest()!=carried['remote_handle_sha256']
                or ProfileStore(root/owner['scopes']['profiles']).get(profile.id)!=profile or profile!=record.remote_profile
                or not _remote_identity_matches(SSHTransport(),profile,record.job_id,handle,spec_sha)):
            raise ValueError('Remote recovery cannot prove the same original live worker')
        claims=[r for r in leases.list() if r['job_id']==record.job_id]
        if len(claims)!=1 or claims[0]['owner']!=leases.owner or claims[0]['remote']!=1:
            raise ValueError('Remote recovery lacks its fresh reservation owner')
        if not leases.stamp_fence(record.job_id,link.fencing_token):raise ValueError('Remote recovery reservation could not follow its current fence')
        return _observer_binding(record,leases,owner,root,attempt)


def acknowledge_remote_control(journal):
    """Publish immutable local acknowledgment after a remote launch returned."""
    binding=journal.get('control_owner')
    if journal.get('global_control_protocol')!=1 or binding is None:return
    from backend.engine.live_control_migration import journal_admission
    from backend.engine.terminal_runtime_history import _read
    root,owner=owned_root(journal['output_dir'])
    if root is None or binding['installation_id']!=owner['installation_id']:
        raise ValueError('Remote control acknowledgment lacks its original installation')
    with journal_admission(journal):
        if active_generation(root):raise ValueError('A remote launch cannot opt in after live activation')
        output=Path(journal['output_dir']);spec_path=output/'remote_spec.json'
        spec,raw=_read(root,spec_path)
        if (journal.get('state')!='launched' or not isinstance(journal.get('remote_handle'),str)
                or not journal['remote_handle'] or spec.get('job_id')!=binding['job_id'] or spec.get('operation')!='train'):
            raise ValueError('Remote acknowledgment must follow the original training launch')
        ready={**binding,'spec_sha256':hashlib.sha256(raw).hexdigest(),
            'profile_sha256':digest(journal['profile']),
            'remote_handle_sha256':hashlib.sha256(journal['remote_handle'].encode()).hexdigest()}
        path=output/'remote_control_ready.json'
        if path.exists():
            prior,_=_read(root,path)
            if prior!=ready:raise ValueError('Remote control acknowledgment cannot replace an earlier owner')
        else:atomic_private_json(path,ready)


def remote_journal_fence(root,journal):
    """A carried remote observer cannot write after a backend reattachment."""
    current=active_generation(root)
    if current is None:return
    from backend.engine.live_control_migration import _current_adoptions
    from backend.engine.job_store import JobStore
    from backend.engine.global_store_paths import resolve_store_path
    _,adoption=_current_adoptions(root);binding=journal.get('control_owner') or {}
    row=next((r for r in (adoption or {}).get('workers',[]) if r['job_id']==journal['job_id']),None)
    if row is None or row.get('worker_kind')!='remote_training':
        raise ValueError('Remote journal has no matching carried observer fence')
    _,owner=owned_root(root)
    attempts=JobStore(resolve_store_path(root/owner['scopes']['ledger'])).attempts(journal['job_id'])
    if not attempts or attempts[-1]['fencing_token']!=binding.get('attempt_fence') or attempts[-1]['ended_ns'] is not None:
        raise ValueError('A newer remote observer attempt owns this generation')
    _prove_observer_process(binding)
    original=(row['lease_owner']==binding.get('lease_owner') and row['attempt_fence']==binding.get('attempt_fence')
        and row['owner_pid']==binding.get('owner_pid') and row['owner_created_at']==binding.get('owner_created_at')
        and row['owner_command_sha256']==binding.get('owner_command_sha256'))
    if not original:
        from backend.engine.shared_scheduler import ResourceLeases
        if (binding.get('attempt_number')!=row['attempt_number'] or binding.get('attempt_fence',0)<=row['attempt_fence']
                or attempts[-1].get('worker_id')!='pid:'+str(binding.get('owner_pid'))
                or binding.get('installation_id')!=owner['installation_id'] or binding.get('job_id')!=journal['job_id']):
            raise ValueError('Remote journal lacks its current backend reattachment fence')
        claims=[r for r in ResourceLeases(root/owner['scopes']['leases']).list() if r['job_id']==journal['job_id']]
        if len(claims)!=1 or claims[0]['owner']!=binding.get('lease_owner') or claims[0]['fence']!=binding['attempt_fence']:
            raise ValueError('Remote journal lacks its current reservation fence')
    if (digest(journal['profile'])!=row['remote_profile_sha256']
            or hashlib.sha256(journal['remote_handle'].encode()).hexdigest()!=row['remote_handle_sha256']):
        raise ValueError('Remote journal changed its carried remote authority')


def inspect_remote_worker(root,owner,row,active,claims):
    """Read-only positive original local/remote ownership and launch proof."""
    from backend.engine.terminal_runtime_history import _read
    from backend.remote.profiles import ComputeProfile,ProfileStore
    from backend.remote.ssh_transport import SSHTransport,SSHTransportError
    identifier=row['id'];output=Path(row['output_dir']);scopes=owner['scopes']
    journal,index_raw=_read(root,root/scopes['remote_journals']/(identifier+'.json'))
    _,run_raw=_read(root,output/'remote_job.json')
    spec_path=output/'remote_spec.json';spec,spec_raw=_read(root,spec_path)
    if index_raw!=run_raw:raise ValueError('Live remote recovery journal copies differ')
    if (journal.get('global_control_protocol')!=1 or journal.get('protocol_version')!=1
            or journal.get('state')!='launched' or journal.get('operation')!='train'
            or journal.get('job_id')!=identifier or journal.get('output_dir')!=str(output)
            or spec.get('protocol_version')!=1 or spec.get('job_id')!=identifier or spec.get('operation')!='train'
            or (spec.get('task'),spec.get('preset'))!=(journal.get('task'),journal.get('preset'))):
        raise ValueError('Only an acknowledged current cooperative remote training launch can participate')
    profile=ComputeProfile.model_validate(journal['profile'])
    if ProfileStore(root/scopes['profiles']).get(profile.id)!=profile:
        raise ValueError('Remote profile differs from the original registered target')
    spec_sha=hashlib.sha256(spec_raw).hexdigest()
    manifest=journal.get('transfers')
    if not isinstance(manifest,list) or any(not isinstance(r,dict) for r in manifest):raise ValueError('Invalid remote transfer manifest')
    transfers=[r for r in manifest if r.get('target')=='spec.json']
    if len(transfers)!=1 or transfers[0]!={'source':str(spec_path),'target':'spec.json','size':len(spec_raw),'sha256':spec_sha}:
        raise ValueError('Remote immutable specification differs from its uploaded manifest')
    binding=journal.get('control_owner')
    fields={'protocol_version','installation_id','job_id','lease_owner','lease_path','attempt_fence','attempt_number',
            'owner_pid','owner_created_at','owner_command_sha256'}
    if (not isinstance(binding,dict) or set(binding)!=fields or type(binding['protocol_version']) is not int
            or binding['protocol_version']!=1 or binding['installation_id']!=owner['installation_id']
            or binding['job_id']!=identifier or binding['lease_path']!=str(root/scopes['leases'])
            or type(binding['owner_pid']) is not int or binding['owner_pid']<1
            or type(binding['owner_created_at']) not in (int,float)
            or binding['attempt_fence']!=active['fencing_token'] or binding['attempt_number']!=active['number']
            or active['executor']!='remote' or active['owner_pid']!=binding['owner_pid']):
        raise ValueError('Remote original observer/attempt binding differs')
    handle=journal.get('remote_handle')
    if not isinstance(handle,str) or not handle:raise ValueError('Remote launch acknowledgment is missing')
    ready,_=_read(root,output/'remote_control_ready.json')
    profile_sha=digest(journal['profile']);handle_sha=hashlib.sha256(handle.encode()).hexdigest()
    if ready!={**binding,'spec_sha256':spec_sha,'profile_sha256':profile_sha,'remote_handle_sha256':handle_sha}:
        raise ValueError('Remote observer has not acknowledged this exact immutable launch')
    _prove_observer_process(binding)
    host='ssh:'+profile.ssh_target.rsplit('@',1)[-1].lower()+':'+str(profile.ssh_port)
    if not claims or any(r['owner']!=binding['lease_owner'] or r['fence']!=active['fencing_token'] or r['remote']!=1
            or r['app_schema']!=2 or r['host']!=host or r['selector']!=(profile.gpu_selector or 'all') for r in claims):
        raise ValueError('Remote reservation owner/fence/target differs')
    transport=SSHTransport()
    try:
        if not _remote_identity_matches(transport,profile,identifier,handle,spec_sha):
            raise ValueError('Remote worker is exited, uncertain or belongs to another launch')
    except (OSError,TimeoutError,SSHTransportError) as exc:raise ValueError('Remote live ownership could not be proven') from exc
    return journal,{'job_id':identifier,'lease_owner':binding['lease_owner'],'attempt_fence':active['fencing_token'],
        'attempt_number':active['number'],'owner_pid':binding['owner_pid'],'owner_created_at':binding['owner_created_at'],
        'owner_command_sha256':binding['owner_command_sha256'],'spec_sha256':spec_sha,'output_dir':str(output),
        'reserved':True,'uncertain':bool(claims[0]['uncertain']),'worker_kind':'remote_training',
        'remote_profile_sha256':profile_sha,'remote_handle_sha256':handle_sha}

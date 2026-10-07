"""Explicitly owned drained control generations. Live/copy ownership never adopted."""
from contextlib import closing,contextmanager
import hashlib,json,os,shutil,sqlite3,tempfile,uuid
from pathlib import Path
from backend.engine.global_store_paths import OWNER_FILE,POINTER_FILE,owned_root,active_generation,store_admission,staged_construction,SUPPORTED_SCOPES
from backend.engine.migration_inventory import inventory,owned_file_snapshot
from backend.engine.runtime_process_control import atomic_private_json

class GlobalMigrationError(ValueError):pass

_SCOPES={'ledger','leases','profiles','accounts','context','local_journals','remote_journals'}
_EXCLUDED={OWNER_FILE,POINTER_FILE,'.global-generations','.global-migrations','.global-generation.json','migration_admission.lock'}
_APPLICATION_CONTROL={'application-active.json','application-update-pending.json','.application-updates','.application-generations'}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def initialize_owned(root,*,scopes):
    root=Path(root).expanduser()
    if os.name=='nt':raise GlobalMigrationError('Owned generation activation currently requires POSIX directory durability and shared admission; Windows qualification is pending')
    if not root.is_dir() or any(p.is_symlink() for p in (root,*root.parents)) or any(root.iterdir()):
        raise GlobalMigrationError('Initialize only a new empty owned installation; installed directories are unsupported')
    if not isinstance(scopes,dict) or scopes!=SUPPORTED_SCOPES:
        raise GlobalMigrationError('Explicit complete current scope paths are required; custom/overlapping paths are unsupported')
    root=root.resolve();st=root.stat();record={'schema_version':1,'installation_id':uuid.uuid4().hex,'scopes':dict(scopes),
        'root_identity':{'path':str(root),'device':st.st_dev,'inode':st.st_ino}}
    atomic_private_json(root/OWNER_FILE,record);return {'installation_id':record['installation_id']}


def _owner(root):
    try:
        root=Path(root).expanduser().absolute();found,data=owned_root(root)
        if found!=root or data.get('schema_version')!=1 or set(data.get('scopes',{}))!=_SCOPES:
            raise ValueError('Explicit owned global installation required')
        return root,data
    except (ValueError,OSError,TypeError) as exc:raise GlobalMigrationError(str(exc)) from exc


def _files(root):
    return [p for p in sorted(Path(root).rglob('*')) if p.relative_to(root).parts[0] not in _APPLICATION_CONTROL
            and not any(part in _EXCLUDED for part in p.relative_to(root).parts)]


def _schema(path):
    with owned_file_snapshot(path) as copied,closing(sqlite3.connect(copied.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        return [list(row) for row in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]


def _construct(root,scopes):
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.shared_accounts import AccountStore
    from backend.contracts.context import ContextRegistry
    from backend.remote.profiles import ProfileStore
    JobStore(root/scopes['ledger']);ResourceLeases(root/scopes['leases'])
    accounts=AccountStore(root/scopes['accounts']);context=ContextRegistry((root/scopes['context']).parent)
    accounts.bind_workspace(context.workspace_id)
    ProfileStore(root/scopes['profiles']).list()


def _known_schemas(scopes):
    with tempfile.TemporaryDirectory(prefix='modu-global-schema-') as temp:
        root=Path(temp);p=root/scopes['profiles'];p.parent.mkdir(parents=True,exist_ok=True);p.write_text('{"profiles":[],"selected":null}',encoding='utf-8')
        _construct(root,scopes)
        return {name:_schema(root/scopes[name]) for name in ('ledger','leases','accounts','context')}


def _snapshot(root):
    result=inventory(root,kind='owned_global',paths=_files(root))
    return result


def preview(root):
    root,owner=_owner(root);result=_snapshot(root);blockers=list(result['blockers']);known=_known_schemas(owner['scopes'])
    conversions={}
    for name,relative in owner['scopes'].items():
        path=root/relative
        if name in {'local_journals','remote_journals'}:continue
        if not path.is_file():blockers.append('Missing declared '+name+' scope');continue
        if name in known:
            try:
                actual=_schema(path)
                if actual!=known[name]:
                    from backend.engine.historical_control_schema import predecessor
                    source=predecessor(name,actual)
                    if source is None:blockers.append('Unsupported '+name+' schema; an exact recorded current or predecessor structure is required')
                    else:conversions[name]={'source_commit':source,'target':'current trusted control schema'}
            except (OSError,ValueError,sqlite3.Error) as exc:blockers.append('Unreadable '+name+' scope: '+type(exc).__name__)
        elif name=='profiles':
            from backend.remote.profiles import ProfileStore
            try:ProfileStore(path)._read()
            except (ValueError,OSError) as exc:blockers.append('Invalid profile store: '+type(exc).__name__)
    from backend.engine.terminal_runtime_history import journal_blockers
    if not blockers:blockers.extend(journal_blockers(root,owner['scopes']))
    if not blockers:
        blockers.extend(_authority_blockers(root,owner['scopes']))
    if active_generation(root):blockers.append('An active generation already exists; migrate forward from it in a separately supported conversion')
    source_sha=digest({'inventory':result['source_snapshot']['sha256'],'owner_sha256':hashlib.sha256((root/OWNER_FILE).read_bytes()).hexdigest()})
    result.update(source_sha256=source_sha,installation_id=owner['installation_id'],
                  blockers=sorted(set(blockers)),can_apply=not blockers,activation_supported=True,
                  schema_conversions=conversions,
                  ownership_policy='owned drained generations only; live or copied workers/leases unsupported')
    return result


@contextmanager
def _read_db(path):
    with owned_file_snapshot(path) as copy,closing(sqlite3.connect(copy.resolve().as_uri()+'?mode=ro',uri=True)) as db:yield db


def _authority_blockers(root,scopes,*,installation_root=None,history_jobs=frozenset()):
    blockers=[]
    with _read_db(root/scopes['context']) as db:
        identities=dict(db.execute('SELECT name,value FROM identities'))
        projects=list(db.execute('SELECT path FROM projects'))
        locations=list(db.execute('SELECT scope_key,workspace_id,project_id,path FROM project_locations'))
    with _read_db(root/scopes['accounts']) as db:
        account=dict(db.execute('SELECT name,value FROM account_meta'))
        projects+=list(db.execute('SELECT path FROM projects'))
        users={row[0] for row in db.execute('SELECT id FROM users')}
        members={(project,user) for project,user in db.execute('SELECT project_id,user_id FROM members')}
    if (identities.get('schema_version')!='2' or account.get('schema_version')!='2'
            or not identities.get('workspace_id') or not identities.get('local_actor_id')
            or account.get('workspace_id')!=identities['workspace_id']
            or account.get('context_workspace_id')!=identities['workspace_id']):
        blockers.append('Account and context authority differ; no automatic workspace rebind')
    for (path,) in projects:
        p=Path(path)
        if not p.is_absolute() or not p.is_dir() or any(x.is_symlink() for x in (p,*p.parents)) or not p.resolve().is_relative_to(installation_root or root):
            blockers.append('External or unavailable registered project references are unsupported in this phase')
    from backend.engine.portable_flow_history import project_blockers
    blockers.extend(project_blockers(installation_root or root,locations))
    known={(w,p,k) for k,w,p,_ in locations}
    with _read_db(root/scopes['ledger']) as db:
        for identifier,workspace,project,key,actor,mode in db.execute('SELECT id,workspace_id,project_id,project_key,actor_id,mode FROM jobs'):
            # A historical converter separately validates each excluded ended
            # row's source, output, schema and immutable registered namespace.
            if identifier in history_jobs:continue
            actor_known=(actor==identities['local_actor_id']) if mode=='local' else (actor in users and (project,actor) in members) if mode=='team' else False
            if (workspace,project,key) not in known or not actor_known:
                blockers.append('Ledger namespace/actor lacks original registered authority; historical adapter required')
    return blockers


def _forward_view(root,owner,generation,pointer):
    """Inspect a specifically bound generation without activating retired stores."""
    seal_path=generation/'.global-generation.json'
    if any(p.is_symlink() for p in (generation,generation.parent,seal_path)):
        raise GlobalMigrationError('Forward source generation cannot be linked')
    seal=json.loads(seal_path.read_bytes())
    if (seal.get('installation_id')!=owner['installation_id'] or seal.get('generation_id')!=pointer.get('generation_id')
            or seal.get('sealed_sha256')!=pointer.get('sealed_sha256')):
        raise GlobalMigrationError('Forward source ownership or seal differs')
    view=_snapshot(generation);known=_known_schemas(owner['scopes']);blockers=list(view['blockers'])
    for name,relative in owner['scopes'].items():
        source=generation/relative
        if name in {'local_journals','remote_journals'}:
            continue
        elif not source.is_file():blockers.append('Missing declared '+name+' scope')
        elif name in known and _schema(source)!=known[name]:blockers.append('Unsupported '+name+' schema')
        elif name=='profiles':
            from backend.remote.profiles import ProfileStore
            ProfileStore(source)._read()
    from backend.engine.terminal_runtime_history import journal_blockers
    if not blockers:blockers.extend(journal_blockers(generation,owner['scopes'],installation_root=root))
    if not blockers:blockers.extend(_authority_blockers(generation,owner['scopes'],installation_root=root))
    original=_snapshot(root)
    view.update(source_sha256=digest({'generation':view['source_snapshot']['sha256'],
        'original':original['source_snapshot']['sha256'],'pointer':pointer,
        'owner_sha256':hashlib.sha256((root/OWNER_FILE).read_bytes()).hexdigest()}),
        installation_id=owner['installation_id'],blockers=sorted(set(blockers)),can_apply=not blockers,
        source_generation=pointer['generation_id'],ownership_policy='owned drained current generation; no live authority adoption')
    return view


def preview_forward(root):
    root,owner=_owner(root)
    with store_admission(root):
        current=active_generation(root)
        if current is None:raise GlobalMigrationError('Forward migration requires an active owned generation')
        return _forward_view(root,owner,*current)


def advance(root,*,expected_source_sha256,on_prepared=None):
    """Preserve post-cutover writes in another drained current-schema generation.

    This is forward recovery, not a historical schema converter or live adoption.
    Old generations and installation files remain intact, and copied sessions are revoked.
    """
    root,owner=_owner(root)
    with store_admission(root,exclusive=True):
        current=active_generation(root)
        if current is None:raise GlobalMigrationError('Forward migration requires an active owned generation')
        before=_forward_view(root,owner,*current)
        if before['source_sha256']!=expected_source_sha256:raise GlobalMigrationError('Forward source changed since preview')
        if not before['can_apply']:raise GlobalMigrationError('; '.join(before['blockers']))
        identifier=uuid.uuid4().hex;directory=root/'.global-migrations'/identifier
        directory.mkdir(parents=True,exist_ok=False)
        backup=directory/'original';_copy(before,current[0],backup,scopes=owner['scopes'])
        staged=root/'.global-generations'/identifier;_copy(before,current[0],staged,scopes=owner['scopes'])
        with staged_construction(root,staged):_construct(staged,owner['scopes']);_revoke(staged,owner['scopes'])
        seal=_seal(staged,owner,identifier)
        if active_generation(root)[1]!=current[1] or _forward_view(root,owner,*current)['source_sha256']!=before['source_sha256']:
            raise GlobalMigrationError('Forward source changed during staging')
        fence=current[1]['fence']+1
        record={'schema_version':1,'kind':'forward','installation_id':owner['installation_id'],
            'migration_id':identifier,'status':'prepared','source_sha256':before['source_sha256'],
            'source_inventory':before['inventory'],'target_sha256':seal['sealed_sha256'],
            'backup_inventory':_snapshot(backup)['inventory'],'previous_pointer':current[1],
            'fence':fence,'scopes':owner['scopes']}
        atomic_private_json(directory/'journal.json',record);_sync_directories(directory)
        if on_prepared is not None:on_prepared(dict(record))
        _publish(root,owner,identifier,seal,fence=fence)
        record['status']='applied';atomic_private_json(directory/'journal.json',record)
        return {'status':'applied','migration_id':identifier,'fence':fence,'kind':'forward',
            'session_policy':'copied sessions and OIDC pending revoked'}


def _copy(before,source,destination,*,scopes=None):
    destination.mkdir(parents=True,exist_ok=False)
    for row in before['inventory']['files']:
        if scopes is not None and row['path'] not in set(scopes.values()):
            relative=Path(row['path'])
            if relative.parent.as_posix() not in {scopes['local_journals'],scopes['remote_journals']}:continue
        path=source/row['path'];target=destination/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
        with owned_file_snapshot(path) as copied:shutil.copyfile(copied,target,follow_symlinks=False)
        if hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:raise GlobalMigrationError('Source changed during global backup')
        target.chmod(0o600)
        with target.open('rb') as handle:os.fsync(handle.fileno())
    _sync_directories(destination)


def _revoke(root,scopes):
    with closing(sqlite3.connect(root/scopes['accounts'])) as db,db:
        tables={r[0] for r in db.execute("select name from sqlite_master where type='table'")}
        for name in ('sessions','oidc_pending'):
            if name in tables:db.execute('DELETE FROM '+name)


def _seal(root,owner,identifier):
    snapshot=_snapshot(root);record={'schema_version':1,'installation_id':owner['installation_id'],'generation_id':identifier,
        'sealed_sha256':digest(snapshot['inventory']), 'inventory':snapshot['inventory']}
    atomic_private_json(root/'.global-generation.json',record)
    _sync_directories(root)
    return record


def _journal(root,identifier):
    if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise GlobalMigrationError('Invalid global migration identity')
    path=root/'.global-migrations'/identifier/'journal.json'
    if any(p.is_symlink() for p in (path,path.parent,path.parent.parent)) or not path.is_file():raise GlobalMigrationError('Unknown owned migration journal')
    return path,json.loads(path.read_bytes())


def _publish(root,owner,identifier,seal,*,fence):
    atomic_private_json(root/POINTER_FILE,{'schema_version':1,'installation_id':owner['installation_id'],
        'generation_id':identifier,'sealed_sha256':seal['sealed_sha256'],'fence':fence})
    _sync_directories(root,recursive=False)


def _sync_directories(root,*,recursive=True):
    if os.name=='nt':return  # Native power-loss qualification remains a separate gate.
    paths=[p for p in Path(root).rglob('*') if p.is_dir()] if recursive else []
    for p in sorted([Path(root),*paths],key=lambda p:len(p.parts),reverse=True):
        descriptor=os.open(p,os.O_RDONLY)
        try:os.fsync(descriptor)
        finally:os.close(descriptor)


def _resume_restore(root,owner,identifier,path,record,source):
    """Replay one durable restoration intent; neither originals nor later writes are replaced."""
    restored_id=record.get('restored_generation');target_hash=record.get('restored_target_sha256')
    fence=record.get('restored_fence');original_fence=record.get('fence')
    def hexadecimal(value,size):
        return isinstance(value,str) and len(value)==size and all(c in '0123456789abcdef' for c in value)
    if (record.get('schema_version')!=1 or record.get('migration_id')!=identifier
            or record.get('scopes')!=owner['scopes'] or not hexadecimal(restored_id,32)
            or restored_id==identifier or not hexadecimal(target_hash,64)
            or type(original_fence)is not int or original_fence<1
            or type(fence)is not int or fence!=original_fence+1):
        raise GlobalMigrationError('Invalid owned restoration intent')
    restored=root/'.global-generations'/restored_id;seal_path=restored/'.global-generation.json'
    if any(p.is_symlink() for p in (restored,restored.parent,seal_path)) or not restored.is_dir():
        raise GlobalMigrationError('Prepared restoration generation is missing or linked')
    seal=json.loads(seal_path.read_bytes())
    if (seal.get('schema_version')!=1 or seal.get('installation_id')!=owner['installation_id']
            or seal.get('generation_id')!=restored_id or seal.get('sealed_sha256')!=target_hash
            or digest(_snapshot(restored)['inventory'])!=target_hash):
        raise GlobalMigrationError('Restoration generation integrity changed; forward recovery required')
    backup=path.parent/'original'
    if backup.is_symlink() or _snapshot(backup)['inventory']!=record['backup_inventory']:
        raise GlobalMigrationError('Original backup failed integrity verification')
    # Initial preview deliberately refuses a second activation once any
    # generation exists. Its source hash still binds the already drained
    # original snapshot; a new job/lease/write changes that hash.
    if source['source_sha256']!=record['source_sha256']:
        raise GlobalMigrationError('Normal source writes or drain changed; forward recovery required')
    current=active_generation(root)
    previous={'schema_version':1,'installation_id':owner['installation_id'],
        'generation_id':identifier,'sealed_sha256':record['target_sha256'],'fence':original_fence}
    target={'schema_version':1,'installation_id':owner['installation_id'],
        'generation_id':restored_id,'sealed_sha256':target_hash,'fence':fence}
    if current is None or current[1] not in (previous,target):
        raise GlobalMigrationError('Current generation changed after restoration intent')
    if current[1]==previous:
        if record['status']!='restoring' or digest(_snapshot(current[0])['inventory'])!=record['target_sha256']:
            raise GlobalMigrationError('Normal current writes or restoration state changed; forward recovery required')
        _publish(root,owner,restored_id,seal,fence=fence)
    else:
        # The earlier replace may have succeeded before directory fsync failed.
        # Confirm its durability before recording a completed restoration.
        _sync_directories(root,recursive=False)
    if record['status']!='restored':
        record['status']='restored';atomic_private_json(path,record)
        _sync_directories(path.parent,recursive=False)
    return {'status':'restored','generation_id':restored_id,'fence':fence}


def apply(root,*,expected_source_sha256,on_prepared=None):
    root,owner=_owner(root)
    with store_admission(root,exclusive=True):
        if active_generation(root):raise GlobalMigrationError('An active generation exists; original-source reactivation is refused')
        before=preview(root)
        if before['source_sha256']!=expected_source_sha256:raise GlobalMigrationError('Global source changed since preview')
        if not before['can_apply']:raise GlobalMigrationError('; '.join(before['blockers']))
        previous=active_generation(root);identifier=uuid.uuid4().hex
        directory=root/'.global-migrations'/identifier;directory.mkdir(parents=True,exist_ok=False)
        backup=directory/'original';_copy(before,root,backup,scopes=owner['scopes']);backup_view=_snapshot(backup)
        generations=root/'.global-generations';generations.mkdir(exist_ok=True)
        staged=generations/identifier;_copy(before,root,staged,scopes=owner['scopes'])
        with staged_construction(root,staged):
            from backend.engine.historical_control_schema import normalize_staged
            normalize_staged(staged,owner['scopes'],_known_schemas(owner['scopes']))
            _construct(staged,owner['scopes']);_revoke(staged,owner['scopes'])
        seal=_seal(staged,owner,identifier)
        if preview(root)['source_sha256']!=before['source_sha256']:raise GlobalMigrationError('Global source changed during staging')
        fence=(previous[1]['fence'] if previous else 0)+1
        journal={'schema_version':1,'installation_id':owner['installation_id'],'migration_id':identifier,'status':'prepared',
            'source_sha256':before['source_sha256'],'source_inventory':before['inventory'],'target_sha256':seal['sealed_sha256'],
            'backup_inventory':backup_view['inventory'],'schema_conversions':before['schema_conversions'],
            'previous_pointer':previous[1] if previous else None,'fence':fence,'scopes':owner['scopes']}
        atomic_private_json(directory/'journal.json',journal);_sync_directories(directory)
        if on_prepared is not None:on_prepared(dict(journal))
        _publish(root,owner,identifier,seal,fence=fence)
        journal['status']='applied';atomic_private_json(directory/'journal.json',journal)
        return {'status':'applied','migration_id':identifier,'fence':fence,'session_policy':'copied sessions and OIDC pending revoked'}


def recover(root,identifier,*,action):
    root,owner=_owner(root)
    with store_admission(root,exclusive=True):
        path,record=_journal(root,identifier);current=active_generation(root)
        if record['installation_id']!=owner['installation_id']:raise GlobalMigrationError('Foreign migration journal')
        if action not in {'finish','restore'}:raise GlobalMigrationError('Select finish or restore')
        def source_view():
            if record.get('kind')!='forward':return preview(root)
            previous=record.get('previous_pointer') or {};prior=previous.get('generation_id')
            if (not isinstance(prior,str) or len(prior)!=32 or any(c not in '0123456789abcdef' for c in prior)
                    or previous.get('installation_id')!=owner['installation_id'] or type(previous.get('fence')) is not int
                    or previous['fence']+1!=record.get('fence')):
                raise GlobalMigrationError('Forward journal source pointer is invalid')
            return _forward_view(root,owner,root/'.global-generations'/prior,previous)
        if record.get('status') in {'restoring','restored'}:
            if action!='restore':raise GlobalMigrationError('Selected migration is restoring or restored; select restore')
            return _resume_restore(root,owner,identifier,path,record,source_view())
        if (record.get('kind')=='forward' and action=='finish' and record.get('status')=='prepared'
                and current and current[1]==record.get('previous_pointer')):
            source=source_view()
            if source['source_sha256']!=record['source_sha256'] or not source['can_apply']:
                raise GlobalMigrationError('Forward source changed after preparation')
            staged=root/'.global-generations'/identifier;seal_path=staged/'.global-generation.json'
            if any(p.is_symlink() for p in (staged,staged.parent,seal_path)):
                raise GlobalMigrationError('Prepared forward generation is linked')
            seal=json.loads(seal_path.read_bytes())
            if (seal.get('installation_id')!=owner['installation_id'] or seal.get('generation_id')!=identifier
                    or seal.get('sealed_sha256')!=record['target_sha256']
                    or digest(_snapshot(staged)['inventory'])!=record['target_sha256']):
                raise GlobalMigrationError('Prepared forward generation integrity differs')
            _publish(root,owner,identifier,seal,fence=record['fence']);current=active_generation(root)
        if current is None and action=='finish' and record.get('status')=='prepared' and record.get('previous_pointer') is None:
            if preview(root)['source_sha256']!=record['source_sha256'] or not preview(root)['can_apply']:
                raise GlobalMigrationError('Source or drain changed after prepared migration')
            staged=root/'.global-generations'/identifier
            if any(p.is_symlink() for p in (staged,staged.parent)) or not staged.is_dir():
                raise GlobalMigrationError('Owned prepared generation is missing or linked')
            seal_path=staged/'.global-generation.json'
            if seal_path.is_symlink():raise GlobalMigrationError('Prepared generation seal is linked')
            seal=json.loads(seal_path.read_bytes())
            if (seal.get('installation_id')!=owner['installation_id'] or seal.get('generation_id')!=identifier
                    or seal.get('sealed_sha256')!=record['target_sha256']
                    or digest(_snapshot(staged)['inventory'])!=record['target_sha256'] or record.get('fence')!=1):
                raise GlobalMigrationError('Prepared generation binding/integrity differs')
            _publish(root,owner,identifier,seal,fence=1)
            current=active_generation(root)
        if not current or current[1]['generation_id']!=identifier:raise GlobalMigrationError('Selected migration is not current')
        if current[1].get('fence')!=record.get('fence') or current[1].get('sealed_sha256')!=record['target_sha256']:
            raise GlobalMigrationError('Current migration fence or seal changed')
        if digest(_snapshot(current[0])['inventory'])!=record['target_sha256'] or source_view()['source_sha256']!=record['source_sha256']:
            raise GlobalMigrationError('Normal writes or source changed after migration; forward recovery required')
        if action=='finish':
            record['status']='applied';atomic_private_json(path,record);return {'status':'applied','migration_id':identifier}
        # Source sessions are never reactivated: restore private sanitized backup
        # into another sealed generation, never replace original installation files.
        restored_id=uuid.uuid4().hex;restored=root/'.global-generations'/restored_id
        backup=path.parent/'original'
        if backup.is_symlink():raise GlobalMigrationError('Original backup is linked')
        backup_view=_snapshot(backup)
        if backup_view['inventory']!=record['backup_inventory']:raise GlobalMigrationError('Original backup failed integrity verification')
        _copy(backup_view,backup,restored)
        with staged_construction(root,restored):
            from backend.engine.historical_control_schema import normalize_staged
            normalize_staged(restored,owner['scopes'],_known_schemas(owner['scopes']))
            _construct(restored,owner['scopes']);_revoke(restored,owner['scopes'])
        seal=_seal(restored,owner,restored_id)
        # A restart must know the exact sealed restoration before the active
        # pointer changes. Retain staged bytes on every failure for inspection.
        record.update(status='restoring',restored_generation=restored_id,
            restored_target_sha256=seal['sealed_sha256'],restored_fence=current[1]['fence']+1)
        atomic_private_json(path,record);_sync_directories(path.parent,recursive=False)
        return _resume_restore(root,owner,identifier,path,record,source_view())


def main(argv=None):
    """Explicit offline CLI; never discovers or adopts an installed home."""
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    for command in ('initialize','preview','apply','preview-forward','advance','recover'):
        item=commands.add_parser(command);item.add_argument('--root',required=True)
        if command=='initialize':item.add_argument('--scopes-file',required=True)
        elif command in ('apply','advance'):item.add_argument('--expected-source-sha256',required=True)
        elif command=='recover':item.add_argument('--migration-id',required=True);item.add_argument('--action',choices=('finish','restore'),required=True)
    args=parser.parse_args(argv)
    try:
        if args.command=='initialize':result=initialize_owned(args.root,scopes=json.loads(Path(args.scopes_file).read_bytes()))
        elif args.command=='preview':result=preview(args.root)
        elif args.command=='preview-forward':result=preview_forward(args.root)
        elif args.command=='apply':result=apply(args.root,expected_source_sha256=args.expected_source_sha256)
        elif args.command=='advance':result=advance(args.root,expected_source_sha256=args.expected_source_sha256)
        else:result=recover(args.root,args.migration_id,action=args.action)
    except (ValueError,OSError,TypeError,sqlite3.Error) as exc:
        print(json.dumps({'status':'refused','error':str(exc)},sort_keys=True));return 1
    print(json.dumps(result,sort_keys=True));return 0

if __name__=='__main__':raise SystemExit(main())

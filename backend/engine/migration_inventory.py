"""Read-only migration identity, SQLite logical snapshots and drain inspection.

Never opens JobStore/AccountStore constructors (which can migrate or reattach).
Only counts/schema/hashes leave this module; credential values stay private.
"""
from contextlib import closing,contextmanager
import hashlib,json,os,sqlite3,stat,tempfile
from pathlib import Path
from backend.engine.project_archive import _sqlite_file,_sqlite_snapshot

_TERMINAL={'completed','failed','aborted','cancelled','canceled','interrupted','error','expired','rejected','succeeded'}
_EXCLUDED={'.migrations','runtime_lifecycle.lock','migration_admission.lock'}
MAX_SNAPSHOT_BYTES=2*1024**3


def _file_identity(node):
    return (node.st_dev,node.st_ino,node.st_mode,node.st_uid,node.st_nlink,
            node.st_size,node.st_mtime_ns,node.st_ctime_ns)


def _copy_regular_snapshot(path,target,max_bytes):
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size>max_bytes:
            raise ValueError('Migration file is not regular or exceeds its bounded snapshot size')
        count=0
        with target.open('xb') as writer:
            target.chmod(0o600)
            while block:=os.read(descriptor,1024*1024):
                count+=len(block)
                if count>before.st_size:raise ValueError('Migration source grew during bounded snapshot')
                writer.write(block)
        if (count!=before.st_size or _file_identity(before)!=_file_identity(os.fstat(descriptor))
                or _file_identity(before)!=_file_identity(path.lstat())):
            raise ValueError('Migration source identity or bytes changed during bounded snapshot')
        return _file_identity(before)
    finally:os.close(descriptor)


@contextmanager
def bounded_file_snapshot(path,*,max_bytes=MAX_SNAPSHOT_BYTES):
    """A stable finite regular-file copy; callers never reopen the source."""
    path=Path(path)
    with tempfile.TemporaryDirectory(prefix='modu-bounded-read-') as temporary:
        copied=Path(temporary)/path.name
        identity=_copy_regular_snapshot(path,copied,max_bytes)
        yield copied
        if _file_identity(path.lstat())!=identity:
            raise ValueError('Migration source changed while validating bounded snapshot')


def bounded_file_bytes(path,*,max_bytes=MAX_SNAPSHOT_BYTES):
    with bounded_file_snapshot(path,max_bytes=max_bytes) as copied:return copied.read_bytes()


def bounded_file_digest(path,*,max_bytes=MAX_SNAPSHOT_BYTES,expected_size=None):
    with bounded_file_snapshot(path,max_bytes=max_bytes) as copied:
        if expected_size is not None and copied.stat().st_size!=expected_size:
            raise ValueError('Migration artifact size differs from its recorded bytes')
        with copied.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def _canonical(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@contextmanager
def owned_file_snapshot(path):
    path=Path(path)
    with bounded_file_snapshot(path) as staged:
        if not _sqlite_file(staged):
            yield staged
            return
        # Copy main+committed WAL before opening SQLite, so dry-run cannot create
        # coordination files in another installation's source directory.
        wal=path.with_name(path.name+'-wal');wal_identity=None
        if wal.exists() or wal.is_symlink():
            wal_identity=_copy_regular_snapshot(wal,staged.with_name(staged.name+'-wal'),MAX_SNAPSHOT_BYTES)
        try:
            yield _sqlite_snapshot(staged,staged.parent)
            if (wal_identity is None and (wal.exists() or wal.is_symlink())
                    or wal_identity is not None and _file_identity(wal.lstat())!=wal_identity):
                raise ValueError('SQLite WAL changed during read-only inventory; retry after writer drain')
        except sqlite3.Error as exc:
            raise ValueError(f'Unsupported or unreadable SQLite migration source: {path.name}') from exc


def inventory(root, *, kind='project', paths=None, live_jobs=None):
    root=Path(root).expanduser()
    if not root.is_dir() or any(p.is_symlink() for p in (root,*root.parents)):
        raise ValueError('Migration inventory requires an unlinked owned directory')
    root=root.resolve();rows=[];blockers=[]
    for path in sorted(root.rglob('*') if paths is None else paths):
        relative=path.relative_to(root)
        if any(part in _EXCLUDED for part in relative.parts):continue
        if path.is_symlink():raise ValueError(f'Migration cannot follow linked artifact {relative.as_posix()}')
        if not path.is_file():continue
        if path.name.endswith(('-wal','-shm','-journal')):
            main=path.with_name(path.name.rsplit('-',1)[0])
            if main.is_file():
                with bounded_file_snapshot(main) as copied:
                    if _sqlite_file(copied):continue
        allowed=(live_jobs or {}).get(relative.as_posix(),frozenset())
        row={'path':relative.as_posix(),'bytes':path.stat().st_size}
        with owned_file_snapshot(path) as copied:
            row.update(bytes=copied.stat().st_size,sha256=hashlib.sha256(copied.read_bytes()).hexdigest())
            if _sqlite_file(copied):
                with closing(sqlite3.connect(copied.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                    conn.row_factory=sqlite3.Row
                    row['schema_version']=conn.execute('pragma user_version').fetchone()[0]
                    if row['schema_version']>1:blockers.append(f'Unsupported SQLite schema in {relative.as_posix()}')
                    row['schema_sha256']=_canonical([list(record) for record in conn.execute("select type,name,tbl_name,sql from sqlite_master where name not like 'sqlite_%' order by type,name")])
                    tables={r[0] for r in conn.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
                    row['tables']={}
                    for table in sorted(tables):
                        quoted='"'+table.replace('"','""')+'"'
                        columns=[r[1] for r in conn.execute(f'pragma table_info({quoted})')]
                        count=conn.execute(f'select count(*) from {quoted}').fetchone()[0]
                        row['tables'][table]={'count':count,'columns':columns}
                        if table in {'jobs','local_jobs','remote_jobs'} and 'state' in columns:
                            states=([(str(r['state']),1) for r in conn.execute(f'select * from {quoted}')
                                    if not ('id' in columns and r['id'] in allowed)] if allowed else
                                    [(str(r[0]),r[1]) for r in conn.execute(f'select state,count(*) from {quoted} group by state')])
                            if any(state not in _TERMINAL and count for state,count in states):blockers.append(f'Drain active jobs in {relative.as_posix()}:{table}; worker ownership is not adopted')
                        if table in {'leases','resource_leases'} and count and (not allowed or 'job_id' not in columns
                                or any(r['job_id'] not in allowed for r in conn.execute(f'select * from {quoted}'))):
                            blockers.append(f'Resolve resource leases in {relative.as_posix()}:{table}; uncertain/copied ownership cannot activate')
                        if table=='attempts' and 'ended_ns' in columns:
                            open_unknown=(any(r['job_id'] not in allowed for r in conn.execute(f'select * from {quoted} where ended_ns is null'))
                                if allowed and 'job_id' in columns else conn.execute(f'select count(*) from {quoted} where ended_ns is null').fetchone()[0])
                            if open_unknown:blockers.append(f'Reconcile open fenced job attempts in {relative.as_posix()} before migration')
            elif path.suffix=='.json':
                try:value=json.loads(copied.read_bytes())
                except (ValueError,UnicodeError):
                    # Arbitrary label/artifact bytes still belong in the source
                    # CAS inventory. Only their optional JSON metadata is absent.
                    value=None
                    if (path.name in {'local_jobs.json','remote_jobs.json','local_job.json','remote_job.json',
                                      'runtime-state.json','service.json','worker.json','runtime_process.json'}
                            or relative.parts[0] in {'local_jobs','remote_jobs'}):
                        blockers.append(f'Unreadable runtime/job control journal in {relative.as_posix()}')
                row['structure']='object' if isinstance(value,dict) else 'array' if isinstance(value,list) else 'scalar'
                if isinstance(value,(dict,list)):row['record_count']=len(value)
                if isinstance(value,dict):
                    for collection in ('profiles','accounts','memberships','jobs'):
                        if isinstance(value.get(collection),(dict,list)):
                            row['collections']=dict(row.get('collections',{}),**{collection:len(value[collection])})
                if isinstance(value,dict) and 'schema_version' in value:
                    row['schema_version']=value['schema_version'] if type(value['schema_version']) is int else 'unsupported'
                if path.name in {'local_jobs.json','remote_jobs.json','local_job.json','remote_job.json'} or relative.parts[0] in {'local_jobs','remote_jobs'}:
                    records=[value] if isinstance(value,dict) and 'job_id' in value else value.values() if isinstance(value,dict) else value if isinstance(value,list) else []
                    if any(isinstance(r,dict) and r.get('job_id') not in allowed and r.get('status',r.get('state')) not in _TERMINAL for r in records):
                        blockers.append(f'Drain jobs in {relative.as_posix()}; no worker adoption')
                if path.name in {'runtime-state.json','service.json','worker.json','runtime_process.json'} and isinstance(value,dict) and any(value.get(key) for key in ('pid','process_id','worker_id','process_identity')):
                    blockers.append(f'Runtime worker ownership in {relative.as_posix()} requires explicit shutdown/reconciliation')
        rows.append(row)
    identity={'kind':kind,'root':str(root),'files':rows}
    return {'source_snapshot':{'sha256':_canonical(identity),'root_identity':hashlib.sha256(str(root).encode()).hexdigest(),'kind':kind},
            'inventory':{'file_count':len(rows),'byte_count':sum(r['bytes'] for r in rows),'files':rows},
            'blockers':sorted(set(blockers)),'can_apply':not blockers}


def global_dry_run(root):
    result=inventory(root,kind='global')
    result.update(status='dry_run',can_apply=False,activation_supported=False,
                  authentication_policy='preserve hashes/memberships as stored; copied sessions/tokens are never activated',
                  ownership_policy='no live worker or lease adoption; receipt/identity/fence reconciliation required')
    result['blockers'].append('Global activation is unsupported; inspect counts/schema and reconcile installation ownership before a supported conversion')
    return result


def project_snapshot(root, *, manifest_override=None):
    root=Path(root).resolve();result=inventory(root)
    manifest=json.loads((root/'project.json').read_bytes())
    source=manifest.get('source_dataset_dir')
    source_inventory=None
    if source:
        path=Path(source).expanduser()
        if not path.is_absolute():path=root/path
        path=path.resolve()
        if not path.is_relative_to(root):source_inventory=inventory(path,kind='source')
    if manifest_override is not None:
        import copy
        result=copy.deepcopy(result)
        for row in result['inventory']['files']:
            if row['path']=='project.json':
                row.update(bytes=len(manifest_override),sha256=hashlib.sha256(manifest_override).hexdigest(),schema_version=1,record_count=len(json.loads(manifest_override)))
        result['inventory']['byte_count']=sum(row['bytes'] for row in result['inventory']['files'])
        result['source_snapshot']['sha256']=_canonical({'kind':'project','root':str(root),'files':result['inventory']['files']})
    control=global_control_inventory()
    result['control_snapshot']=control['source_snapshot']
    result['control_inventory']=control['inventory']
    result['blockers']+=control['blockers']
    result['scopes']={'project':result['inventory']}
    hashes={'project':result['source_snapshot']['sha256'],'control':control['source_snapshot']['sha256']}
    if source_inventory:
        result['scopes']['source']=source_inventory['inventory'];hashes['source']=source_inventory['source_snapshot']['sha256']
        result['blockers']+=source_inventory['blockers']
    result['source_snapshot']['sha256']=_canonical(hashes)
    result['can_apply']=not result['blockers']
    return result


def verified_backup(root,directory,before):
    import shutil,os
    root=Path(root).resolve();manifest=json.loads((root/'project.json').read_bytes())
    sources={'project':root}
    if 'source' in before['scopes']:
        source_path=Path(manifest['source_dataset_dir']).expanduser()
        sources['source']=(source_path if source_path.is_absolute() else root/source_path).resolve()
    records=[]
    for scope,listing in before['scopes'].items():
        for row in listing['files']:
            source=sources[scope]/row['path'];target=Path(directory)/'full_backup'/scope/row['path']
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.is_symlink():raise ValueError('Migration backup file cannot be linked')
            with owned_file_snapshot(source) as copied:
                shutil.copyfile(copied,target,follow_symlinks=False)
            if hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Migration source changed while verifying backup')
            target.chmod(0o600)
            # Windows _commit (used by fsync) requires a writable descriptor.
            # This is the owned backup, never the source being migrated.
            with target.open('r+b') as stored:os.fsync(stored.fileno())
            records.append({'scope':scope,**row})
    if os.name!='nt':
        for parent in sorted({p.parent for p in (Path(directory)/'full_backup').rglob('*')},key=lambda p:len(p.parts),reverse=True):
            descriptor=os.open(parent,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    return {'file_count':len(records),'byte_count':sum(r['bytes'] for r in records),'sha256':_canonical(records),'files':records}


def verify_backup(directory,receipt):
    records=receipt['backup_verified']['files']
    expected={(Path(row['scope'])/row['path']).as_posix() for row in records}
    backup_root=Path(directory)/'full_backup'
    actual={path.relative_to(backup_root).as_posix() for path in backup_root.rglob('*') if path.is_file() or path.is_symlink()}
    if actual!=expected:raise ValueError('Migration backup file count or membership failed')
    for row in records:
        relative=Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['scope'] not in {'project','source'}:raise ValueError('Invalid migration backup record')
        path=Path(directory)/'full_backup'/row['scope']/relative
        if any(p.is_symlink() for p in (path,*path.parents)) or not path.is_file() or path.stat().st_size!=row['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('Migration backup integrity failed')
    if len(records)!=receipt['backup_verified']['file_count'] or _canonical(records)!=receipt['backup_verified']['sha256']:
        raise ValueError('Migration backup count/hash failed')


def global_control_inventory():
    import os
    root=Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home()/'.modu_vision').expanduser().resolve()
    if not root.exists():
        return {'source_snapshot':{'sha256':_canonical({'control_root':str(root),'absent':True}),'kind':'control'},'inventory':{'file_count':0,'byte_count':0,'files':[]},'blockers':['Configured external resource lease store requires ownership reconciliation; migration activation unsupported'] if os.environ.get('VISION_RESOURCE_LEASE_DB') else []}
    paths=[]
    for name in ('jobs','local_jobs','remote_jobs'):
        path=root/name
        if path.is_symlink():raise ValueError('Global job control index cannot be linked')
        if path.is_dir():paths.extend(path.rglob('*'))
    for name in ('resource_leases.sqlite3','local_jobs.json','remote_jobs.json'):
        path=root/name
        if path.exists():paths.append(path)
    result=inventory(root,kind='control',paths=paths)
    override=os.environ.get('VISION_RESOURCE_LEASE_DB')
    if override and Path(override).expanduser().resolve()!=root.resolve()/'resource_leases.sqlite3':
        result['blockers'].append('Configured external resource lease store requires ownership reconciliation; migration activation unsupported')
    return result

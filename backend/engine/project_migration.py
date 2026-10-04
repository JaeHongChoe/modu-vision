"""Read-only schema inspection and durable, hash-bound legacy normalization."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from backend.engine.runtime_process_control import runtime_state_lock

CURRENT_SCHEMA = 1
MAX_MANIFEST_BYTES = 4 * 1024 * 1024


class MigrationError(ValueError):
    pass


def _read(root):
    root=Path(root).expanduser()
    path=root/'project.json'
    if any(parent.is_symlink() for parent in (root,*root.parents)) or path.is_symlink() or not root.is_dir() or not path.is_file():
        raise MigrationError('Open an existing project with an unlinked project.json')
    if path.stat().st_size > MAX_MANIFEST_BYTES:raise MigrationError('Project manifest exceeds 4 MiB')
    raw=path.read_bytes();value=json.loads(raw)
    if not isinstance(value,dict):raise MigrationError('project.json must contain an object')
    schema=value.get('schema_version')
    if 'schema_version' in value and (type(schema) is not int or schema!=CURRENT_SCHEMA):
        raise MigrationError(f'Unsupported project schema {schema}; this app supports schema {CURRENT_SCHEMA}')
    return root.resolve(),raw,value


def legacy_preview_migration(project_dir):
    root,raw,value=_read(project_dir)
    missing='schema_version' not in value
    return {'compatible':True,'schema_version':value.get('schema_version'),
            'current_schema_version':CURRENT_SCHEMA,'migration_required':missing,
            'manifest_sha256':hashlib.sha256(raw).hexdigest(),'status':'legacy' if missing else 'current'}


def _atomic_bytes(path,data):
    path=Path(path)
    if path.is_symlink():raise MigrationError('Migration files cannot be linked')
    fd,name=tempfile.mkstemp(prefix='.'+path.name,dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(fd,'wb') as output:
            output.write(data);output.flush();os.fsync(output.fileno())
        temporary.chmod(0o600);os.replace(temporary,path)
        # Persist the rename on hosts which expose directory fsync.
        if os.name!='nt':
            descriptor=os.open(path.parent,os.O_RDONLY)
            try:os.fsync(descriptor)
            finally:os.close(descriptor)
    finally:temporary.unlink(missing_ok=True)


def _atomic_json(path,value):
    _atomic_bytes(path,json.dumps(value,ensure_ascii=False,indent=2).encode())


def normalize_legacy_manifest(project_dir,expected_manifest_sha256=None):
    preview=legacy_preview_migration(project_dir)  # Future versions fail before creating a lock or folder.
    if expected_manifest_sha256 and expected_manifest_sha256!=preview['manifest_sha256']:
        raise MigrationError('Project manifest changed since preview; preview again')
    if not preview['migration_required']:
        # A process may have exited after replacing the manifest but before
        # publishing its receipt. Finalize only an exactly matching output.
        root=Path(project_dir).resolve();storage=root/'.migrations'
        if storage.is_symlink():raise MigrationError('Migration storage cannot be linked')
        if storage.is_dir():
            for record in storage.glob('*/receipt.json'):
                if record.is_symlink() or record.parent.is_symlink():raise MigrationError('Migration records cannot be linked')
                receipt=json.loads(record.read_text(encoding='utf-8'))
                if receipt.get('status')!='prepared':continue
                backup=record.parent/'project.original.json'
                if backup.is_symlink() or not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest()!=receipt.get('original_sha256'):
                    raise MigrationError('Interrupted migration backup failed integrity verification')
                if receipt.get('normalized_sha256')!=preview['manifest_sha256']:
                    raise MigrationError('Interrupted migration differs from current edits; original backup retained for explicit recovery')
                with runtime_state_lock(root):
                    if legacy_preview_migration(root)['manifest_sha256']!=preview['manifest_sha256']:raise MigrationError('Project manifest changed during recovery')
                    receipt.update(status='applied',recovered=True,updated_at=time.time());_atomic_json(record,receipt)
                return {**preview,'status':'applied','receipt':receipt,'backup_relative_path':backup.relative_to(root).as_posix()}
        return preview
    root=Path(project_dir).resolve()
    with runtime_state_lock(root):
        _,raw,value=_read(root);digest=hashlib.sha256(raw).hexdigest()
        if expected_manifest_sha256 and digest!=expected_manifest_sha256:
            raise MigrationError('Project manifest changed since preview; preview again')
        if 'schema_version' in value:return legacy_preview_migration(root)
        storage=root/'.migrations';directory=storage/digest
        if storage.is_symlink() or directory.is_symlink():raise MigrationError('Migration storage cannot be linked')
        directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
        backup=directory/'project.original.json';receipt_path=directory/'receipt.json'
        if backup.is_symlink() or receipt_path.is_symlink():raise MigrationError('Migration records cannot be linked')
        if backup.exists():
            if backup.read_bytes()!=raw:raise MigrationError('Original migration backup differs from the manifest')
        else:_atomic_bytes(backup,raw)
        normalized={**value,'schema_version':CURRENT_SCHEMA}
        new_bytes=json.dumps(normalized,ensure_ascii=False,indent=2).encode()
        new_digest=hashlib.sha256(new_bytes).hexdigest()
        receipt={'migration_id':digest,'from_schema':None,'to_schema':CURRENT_SCHEMA,
                 'original_sha256':digest,'normalized_sha256':new_digest,
                 'backup_file':'project.original.json','status':'prepared','updated_at':time.time()}
        _atomic_json(receipt_path,receipt)
        try:
            if (root/'project.json').read_bytes()!=raw:raise MigrationError('Project manifest changed during migration')
            _atomic_bytes(root/'project.json',new_bytes)
            receipt.update(status='applied',updated_at=time.time());_atomic_json(receipt_path,receipt)
        except (OSError,ValueError) as exc:
            # Restore only this transaction's output; never overwrite a later edit.
            try:
                current=(root/'project.json').read_bytes()
                if hashlib.sha256(current).hexdigest()==new_digest:_atomic_bytes(root/'project.json',raw)
                elif current!=raw:raise MigrationError('Manifest changed after migration; use the original backup for explicit recovery')
                receipt.update(status='rolled_back',error=str(exc),updated_at=time.time());_atomic_json(receipt_path,receipt)
            except (OSError,ValueError) as recovery:
                raise MigrationError(f'Migration failed: {exc}; recovery required: {recovery}; backup retained in .migrations/{digest}') from exc
            raise MigrationError(f'Migration failed and original manifest restored: {exc}') from exc
        return {**legacy_preview_migration(root),'status':'applied','receipt':receipt,'backup_relative_path':backup.relative_to(root).as_posix()}


def preview_migration(project_dir):
    from backend.engine.migration_inventory import project_snapshot
    preview = legacy_preview_migration(project_dir)
    try:
        snapshot = project_snapshot(project_dir)
    except (ValueError, OSError) as exc:
        raise MigrationError(str(exc)) from exc
    transactions = []
    storage = Path(project_dir) / '.migrations'
    if storage.is_symlink():raise MigrationError('Migration storage cannot be linked')
    if storage.is_dir():
        for path in sorted(storage.glob('*/journal.json'))[-100:]:
            if path.is_symlink() or path.parent.is_symlink():raise MigrationError('Migration journal cannot be linked')
            record=json.loads(path.read_bytes())
            transactions.append({'migration_id':record['migration_id'],'status':record['status'],
                'fencing_token':record['fencing_token'],'backup_file_count':record['backup_verified']['file_count'],
                'backup_sha256':record['backup_verified']['sha256'],
                'can_restore':not snapshot['blockers'] and snapshot['source_snapshot']['sha256']==record['output_source_sha256']})
    return {**preview, **snapshot, 'transactions':transactions, 'conversion': 'manifest schema normalization; artifacts and SQLite schemas preserved',
            'writer_policy': 'cooperative API admission; external writers unsupported', 'global_activation_supported': False}


def _journal(root, migration_id):
    if not isinstance(migration_id, str) or len(migration_id) != 64 or any(c not in '0123456789abcdef' for c in migration_id):
        raise MigrationError('Migration ID must be a SHA-256 transaction identifier')
    directory = Path(root) / '.migrations' / migration_id
    if any(p.is_symlink() for p in (directory, directory.parent)):
        raise MigrationError('Migration journal cannot be linked')
    return directory, directory / 'journal.json'


def _next_migration_fence(root):
    path=Path(root)/'.migrations'/'fence.json'
    if path.is_symlink():raise MigrationError('Migration fence cannot be linked')
    previous=json.loads(path.read_bytes()).get('generation') if path.is_file() else 0
    if type(previous) is not int or previous<0:raise MigrationError('Invalid migration fence')
    generation=previous+1
    _atomic_json(path,{'generation':generation})
    return generation


def apply_migration(project_dir, expected_manifest_sha256=None, *, expected_source_sha256=None):
    from backend.engine.migration_guard import maintenance_guard
    from backend.engine.migration_inventory import project_snapshot, verified_backup, verify_backup
    preview = preview_migration(project_dir)
    if expected_manifest_sha256 and preview['manifest_sha256'] != expected_manifest_sha256:
        raise MigrationError('Project manifest changed since preview; preview again')
    if expected_source_sha256 and preview['source_snapshot']['sha256'] != expected_source_sha256:
        raise MigrationError('Migration source snapshot changed since dry-run; preview again')
    if preview['blockers']:
        raise MigrationError('; '.join(preview['blockers']))
    root = Path(project_dir).resolve()
    try:
        with maintenance_guard(root, exclusive=True), runtime_state_lock(root):
            locked = preview_migration(root)
            if locked['source_snapshot'] != preview['source_snapshot']:
                raise MigrationError('Migration source changed while acquiring writer drain')
            # Recover only our exact cutover. Current/no-op stays compatible.
            storage = root / '.migrations'
            if storage.is_symlink():raise MigrationError('Migration storage cannot be linked')
            if storage.is_dir():
                for path in storage.glob('*/journal.json'):
                    if path.is_symlink() or path.parent.is_symlink():raise MigrationError('Migration journal cannot be linked')
                    record = json.loads(path.read_bytes())
                    if record.get('status') == 'prepared' and locked['manifest_sha256'] == record['normalized_sha256']:
                        verify_backup(path.parent, record)
                        if locked['source_snapshot']['sha256'] != record['output_source_sha256']:
                            raise MigrationError('Post-cutover writes changed source; forward recovery required')
                        record.update(status='applied', recovered=True, updated_at=time.time())
                        _atomic_json(path, record)
                        return {**preview_migration(root), 'status':'applied', 'receipt':record, 'backup_relative_path':path.parent.relative_to(root).as_posix()}
            if not locked['migration_required']:
                # Keep the pre-existing receipt recovery contract for legacy callers.
                return {**locked, **normalize_legacy_manifest(root, expected_manifest_sha256)}
            _, raw, value = _read(root)
            new_bytes = json.dumps({**value,'schema_version':CURRENT_SCHEMA},ensure_ascii=False,indent=2).encode()
            migration_id = locked['manifest_sha256']
            directory, journal = _journal(root, migration_id)
            directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
            if journal.exists():
                old = json.loads(journal.read_bytes())
                if old['source_snapshot'] != locked['source_snapshot']:
                    raise MigrationError('Existing migration transaction has a different source; forward recovery required')
            expected_output = project_snapshot(root, manifest_override=new_bytes)['source_snapshot']['sha256']
            backup = verified_backup(root, directory, locked)
            if project_snapshot(root)['source_snapshot'] != locked['source_snapshot']:
                raise MigrationError('Migration source changed during full backup; cutover refused')
            record={'migration_id':migration_id,'status':'prepared','fencing_token':_next_migration_fence(root),
                    'original_sha256':locked['manifest_sha256'],'normalized_sha256':hashlib.sha256(new_bytes).hexdigest(),
                    'source_snapshot':locked['source_snapshot'],'output_source_sha256':expected_output,
                    'backup_verified':backup,'conversion':'manifest_schema_1','updated_at':time.time()}
            _atomic_json(journal,record)
            verify_backup(directory,record)
            result=normalize_legacy_manifest(root,locked['manifest_sha256'])
            after=project_snapshot(root)
            if after['source_snapshot']['sha256'] != expected_output:
                raise MigrationError('Cutover source changed; backup retained for explicit forward recovery')
            record.update(status='applied',updated_at=time.time());_atomic_json(journal,record)
            return {**preview_migration(root),**{k:result[k] for k in ('compatible','schema_version','current_schema_version','migration_required','manifest_sha256')},
                    'status':'applied','receipt':record,'backup_relative_path':directory.relative_to(root).as_posix()}
    except (ValueError,OSError) as exc:
        if isinstance(exc,MigrationError):raise
        raise MigrationError(str(exc)) from exc


def restore_migration(project_dir,migration_id):
    from backend.engine.migration_guard import maintenance_guard
    from backend.engine.migration_inventory import project_snapshot,verify_backup
    root=Path(project_dir).expanduser().resolve();directory,journal=_journal(root,migration_id)
    try:
        with maintenance_guard(root,exclusive=True),runtime_state_lock(root):
            if journal.is_symlink():raise MigrationError('Migration journal cannot be linked')
            record=json.loads(journal.read_bytes());verify_backup(directory,record)
            current=preview_migration(root)
            if current['blockers']:raise MigrationError('; '.join(current['blockers']))
            digest=current['source_snapshot']['sha256']
            if record['status'] in {'restored','recovering'} and digest==record['source_snapshot']['sha256']:
                if record['status']=='recovering':
                    record.update(status='restored',recovered=True,updated_at=time.time());_atomic_json(journal,record)
                return {**current,'status':'restored','receipt':record}
            if digest!=record['output_source_sha256']:
                raise MigrationError('Post-cutover source changed or subsequent writes occurred; stale restore refused; forward recovery required')
            # This supported conversion changes only project.json. Full backup proof covers all preserved artifacts.
            original=directory/'full_backup'/'project'/'project.json'
            record.update(status='recovering',updated_at=time.time());_atomic_json(journal,record)
            _atomic_bytes(root/'project.json',original.read_bytes())
            if project_snapshot(root)['source_snapshot']['sha256']!=record['source_snapshot']['sha256']:
                raise MigrationError('Recovery source drifted; journal retained for forward recovery')
            record.update(status='restored',updated_at=time.time());_atomic_json(journal,record)
            return {**preview_migration(root),'status':'restored','receipt':record}
    except (ValueError,OSError,KeyError) as exc:
        if isinstance(exc,MigrationError):raise
        raise MigrationError(str(exc)) from exc


def preview_global_migration(root):
    from backend.engine.migration_inventory import global_dry_run
    try:return global_dry_run(root)
    except (ValueError,OSError) as exc:raise MigrationError(str(exc)) from exc


def finish_migration(project_dir,migration_id):
    directory,journal=_journal(Path(project_dir).resolve(),migration_id)
    if journal.is_symlink() or not journal.is_file():raise MigrationError('No such migration journal')
    record=json.loads(journal.read_bytes())
    if record.get('status')!='prepared':raise MigrationError('Only a prepared cutover can be finalized')
    if preview_migration(project_dir)['source_snapshot']['sha256']!=record['output_source_sha256']:
        raise MigrationError('Prepared cutover differs from current source; explicit forward recovery required')
    result=apply_migration(project_dir)
    if result.get('receipt',{}).get('migration_id')!=migration_id:
        raise MigrationError('Selected recovery journal was not finalized')
    return result

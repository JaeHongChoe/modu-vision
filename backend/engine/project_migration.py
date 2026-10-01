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
    if root.is_symlink() or path.is_symlink() or not root.is_dir() or not path.is_file():
        raise MigrationError('Open an existing project with an unlinked project.json')
    if path.stat().st_size > MAX_MANIFEST_BYTES:raise MigrationError('Project manifest exceeds 4 MiB')
    raw=path.read_bytes();value=json.loads(raw)
    if not isinstance(value,dict):raise MigrationError('project.json must contain an object')
    schema=value.get('schema_version')
    if 'schema_version' in value and (type(schema) is not int or schema!=CURRENT_SCHEMA):
        raise MigrationError(f'Unsupported project schema {schema}; this app supports schema {CURRENT_SCHEMA}')
    return root.resolve(),raw,value


def preview_migration(project_dir):
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


def apply_migration(project_dir,expected_manifest_sha256=None):
    preview=preview_migration(project_dir)  # Future versions fail before creating a lock or folder.
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
                receipt=json.loads(record.read_text())
                if receipt.get('status')!='prepared':continue
                backup=record.parent/'project.original.json'
                if backup.is_symlink() or not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest()!=receipt.get('original_sha256'):
                    raise MigrationError('Interrupted migration backup failed integrity verification')
                if receipt.get('normalized_sha256')!=preview['manifest_sha256']:
                    raise MigrationError('Interrupted migration differs from current edits; original backup retained for explicit recovery')
                with runtime_state_lock(root):
                    if preview_migration(root)['manifest_sha256']!=preview['manifest_sha256']:raise MigrationError('Project manifest changed during recovery')
                    receipt.update(status='applied',recovered=True,updated_at=time.time());_atomic_json(record,receipt)
                return {**preview,'status':'applied','receipt':receipt,'backup_relative_path':backup.relative_to(root).as_posix()}
        return preview
    root=Path(project_dir).resolve()
    with runtime_state_lock(root):
        _,raw,value=_read(root);digest=hashlib.sha256(raw).hexdigest()
        if expected_manifest_sha256 and digest!=expected_manifest_sha256:
            raise MigrationError('Project manifest changed since preview; preview again')
        if 'schema_version' in value:return preview_migration(root)
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
        return {**preview_migration(root),'status':'applied','receipt':receipt,'backup_relative_path':backup.relative_to(root).as_posix()}

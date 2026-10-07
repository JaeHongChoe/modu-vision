"""Read-only ended operation archives; no transport, launch, adoption or quality grant."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from backend.engine.migration_inventory import bounded_file_digest

OPERATIONS={'evaluate','infer','benchmark','flowchart_run','export','package_parity','flow_preflight'}


def validate_operations(root, output, parent):
    from backend.engine.terminal_runtime_history import _file, _read
    from backend.remote.profiles import ComputeProfile
    directory=Path(output)/'remote_operations'
    if not directory.exists() and not directory.is_symlink():return []
    if directory.is_symlink() or not directory.is_dir():raise ValueError('Remote operation archive directory is linked or unavailable')
    entries=list(directory.iterdir())
    if len(entries)>2000:raise ValueError('Remote operation archive exceeds the bounded directory count')
    archives={}; indexes={}
    for path in entries:
        if path.is_symlink():raise ValueError('Remote operation archive cannot follow links')
        if path.is_dir():
            if not re.fullmatch(r'op_[0-9a-f]{32}',path.name):raise ValueError('Unsupported remote operation archive directory')
            journal,raw=_read(root,path/'operation_journal.json')
            archives[path.name]=(path,journal,raw)
        elif path.suffix=='.json':
            journal,raw=_read(root,path);indexes[path.name]=(journal,raw)
        elif path.suffix!='.lock' or not path.is_file():
            raise ValueError('Unsupported remote operation archive entry')
    if len(archives)>1000:raise ValueError('Remote operation history exceeds 1000 archived runs')
    for name,(journal,raw) in indexes.items():
        original=archives.get(journal.get('op_id'))
        spec=journal.get('spec')
        if not isinstance(spec,dict):raise ValueError('Remote operation specification is unavailable')
        key=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:20]
        if name!=str(journal.get('operation'))+'_'+key+'.json' or original is None or raw!=original[2]:
            raise ValueError('Remote operation index differs from its original run journal')
    for path,journal,_ in archives.values():
        original_parent=parent(journal) if callable(parent) else parent
        profile=ComputeProfile.model_validate(original_parent['profile'])
        operation=journal.get('operation');spec=journal.get('spec');state=journal.get('state')
        if (type(journal.get('protocol_version')) is not int or journal['protocol_version']!=1
                or operation not in OPERATIONS or journal.get('op_id')!=path.name
                or journal.get('job_id')!=original_parent['job_id'] or state not in {'completed','failed','aborted'}
                or journal.get('worker_exit_confirmed') is not True
                or ComputeProfile.model_validate(journal.get('profile'))!=profile or not isinstance(spec,dict)):
            raise ValueError('Remote operation history lacks exact ended original ownership')
        handle=journal.get('remote_handle')
        pattern=r'[1-9][0-9]*:[0-9a-f]{32}' if profile.runtime_kind=='python' else r'[0-9a-fA-F]{12,64}'
        if not isinstance(handle,str) or not re.fullmatch(pattern,handle):
            raise ValueError('Remote operation history lacks its original launch handle')
        if journal.get('worker_terminal_state')!=state:raise ValueError('Remote operation terminal worker state differs')
        original,spec_raw=_read(root,path/'spec.json')
        if (original!=spec or type(spec.get('protocol_version')) is not int or spec['protocol_version']!=1
                or spec.get('operation')!=operation or spec.get('job_id')!=original_parent['job_id']
                or spec.get('task')!=original_parent['task'] or spec.get('input_manifest_sha256')!=original_parent['input_manifest_sha256']):
            raise ValueError('Remote operation specification belongs to another model or snapshot')
        receipt_path=path/'operation_artifacts.json';outputs=path/'outputs'
        present=receipt_path.exists() or receipt_path.is_symlink() or outputs.exists() or outputs.is_symlink()
        if state!='completed' and not present:continue
        receipt,_=_read(root,receipt_path);manifest=receipt.get('manifest')
        if (receipt.get('schema')!='modu-vision.remote-operation-archive/v1' or receipt.get('op_id')!=path.name
                or receipt.get('spec_sha256')!=hashlib.sha256(spec_raw).hexdigest() or not isinstance(manifest,dict)
                or type(manifest.get('protocol_version')) is not int or manifest['protocol_version']!=1
                or manifest.get('job_id')!=original_parent['job_id'] or manifest.get('operation')!=operation
                or manifest.get('input_manifest_sha256')!=original_parent['input_manifest_sha256']):
            raise ValueError('Remote operation received manifest differs from its original binding')
        if operation=='flowchart_run' and (manifest.get('selected_image_sha256')!=spec.get('image_sha256')
                or manifest.get('model_refs')!=sorted(spec.get('models',[]),key=lambda row:row['job_id'])):
            raise ValueError('Remote operation archived flow image or model references differ')
        rows=manifest.get('artifacts');seen=set();total=0
        if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError('Remote operation artifact list is missing or unbounded')
        for row in rows:
            if not isinstance(row,dict):raise ValueError('Invalid remote operation artifact entry')
            name=row.get('path');size=row.get('size');checksum=row.get('sha256')
            if (not isinstance(name,str) or not name.startswith('outputs/') or '\\' in name
                    or any(part in ('','.','..') for part in name.split('/')) or name in seen
                    or type(size) is not int or not 0<size<=256*1024*1024
                    or not isinstance(checksum,str) or not re.fullmatch('[0-9a-f]{64}',checksum)):
                raise ValueError('Invalid, repeated or unsafe remote operation artifact')
            seen.add(name);total+=size
            if total>1024*1024*1024:raise ValueError('Remote operation archived output exceeds limits')
            file=_file(root,path/PurePosixPath(name))
            found=bounded_file_digest(file,max_bytes=size,expected_size=size)
            if found!=checksum:
                raise ValueError('Remote operation archived artifact changed')
        if not outputs.is_dir() or outputs.is_symlink():raise ValueError('Remote operation output directory is unavailable')
        found={p.relative_to(path).as_posix() for p in outputs.rglob('*') if p.is_file() or p.is_symlink()}
        if found!=seen:raise ValueError('Remote operation output membership differs from its received manifest')

    return [(path,journal) for path,journal,_ in archives.values()]

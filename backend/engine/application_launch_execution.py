"""One pinned CPU OCR execution in the original authenticated source backend.

This is an execution capability, not model approval or native acceptance. The
current-runtime worker never imports Python from an exported package. Source
and frozen workers use separate fixed entry points and receipt scopes.
"""
from __future__ import annotations

import base64
import hashlib
import math
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import tempfile
from contextlib import contextmanager

from backend.engine import runtime_update as update

PLAN = 'delivery/launch-known-image/plan.json'
PACKAGE = 'delivery/launch-known-image/package'
INPUT = 'delivery/launch-known-image/input.png'
OUTPUTS = 'delivery/launch-known-image/results'
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 128 * 1024**2
MAX_RESULT = 8 * 1024**2
MAX_FILES = 1024
CPU_PROTOCOL = 'owned_application_cpu_execution_protocol'
CPU_RESOURCES = ('scripts/frozen_backend_entry.py',
    'backend/engine/application_launch_controller.py', 'backend/engine/application_launch_handshake.py',
    'backend/engine/application_launch_lease.py', 'backend/engine/application_launch_execution.py',
    'backend/engine/flow_package_runtime.py', 'backend/engine/ocr.py',
    'backend/engine/runtime_deadline.py', 'backend/engine/process_isolation.py')
REQUEST_FIELDS = {'schema_version', 'kind', 'challenge', 'request_id', 'nonce', 'epoch',
    'binding_sha256', 'backend_claim_sha256', 'workspace_id', 'project_id', 'plan_sha256'}
PLAN_FIELDS = {'schema_version', 'kind', 'workspace_id', 'project_id', 'project_manifest_sha256',
    'package_path', 'package_manifest_sha256', 'graph_sha256', 'checkpoints', 'input_path',
    'input_sha256', 'semantic_output_sha256', 'device', 'cpu_threads', 'deadline_ms',
    'release_policy', 'runtime_source_sha256'}
RECEIPT_FIELDS = {'schema_version','kind','status','nonce','binding','request_id','request_sha256',
    'challenge_sha256','backend_claim_sha256','plan_sha256','epoch','main_process','backend_process',
    'backend_executable','backend_executable_sha256','backend_frozen','execution_scope','runtime_source_sha256',
    'workspace_id','project_id','scope_key','package_manifest_sha256','graph_sha256','checkpoints','input_sha256',
    'output_path','output_sha256','semantic_output','semantic_output_sha256','worker_pid',
    'worker_process_tree_exit_verified','actual_cpu_execution_verified','owned_backend_execution_origin_verified',
    'actual_application_inference_verified','model_quality_approved','native_app_handshake_verified','release_ready'}


class ExecutionError(ValueError):
    pass


def _json(raw):
    """Bound parsing and traversal so malformed proofs cannot kill the owner."""
    try:
        value = update._json(raw)
    except (ValueError, RecursionError) as exc:
        raise ExecutionError('Invalid bounded CPU execution document') from exc
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > 32: raise ExecutionError('CPU execution document nesting exceeds its bound')
        if isinstance(item,float) and not math.isfinite(item): raise ExecutionError('Nonfinite CPU execution document')
        if isinstance(item, dict): pending.extend((child, depth+1) for child in item.values())
        elif isinstance(item, list): pending.extend((child, depth+1) for child in item)
    return value


def _checkpoint(point):
    """Publication fault boundary; no operation is retried automatically."""


def _read(path, limit=65536, *, expected=None, destination=None):
    """Bounded no-follow stable descriptor read/copy, including final named stat."""
    chunks = []; digest = hashlib.sha256(); total = 0; writer = None
    path=update._unlinked(path)
    fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or not 0<=before.st_size<=limit:
            raise ExecutionError('Execution input is not a bounded unlinked regular file')
        with os.fdopen(fd,'rb',closefd=False) as reader:
            if destination is not None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                writer = os.fdopen(destination_fd, 'wb')
            while chunk := reader.read(min(1024**2, before.st_size-total+1)):
                total += len(chunk)
                if total > before.st_size: raise ExecutionError('Reviewed execution artifact grew while reading')
                digest.update(chunk)
                if writer is not None: writer.write(chunk)
                else: chunks.append(chunk)
                _checkpoint('after_artifact_chunk')
            if total != before.st_size: raise ExecutionError('Reviewed execution artifact shrank while reading')
        update._unlinked(path);after=os.fstat(fd);named=path.stat()
        identity=lambda value:(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns,value.st_nlink,value.st_mode)
        if identity(before)!=identity(after) or identity(before)!=identity(named):
            raise ExecutionError('Execution artifact identity changed during reading')
        observed = digest.hexdigest()
        if expected is not None and observed != expected: raise ExecutionError('Reviewed execution artifact checksum differs')
        if writer is not None: writer.flush(); os.fsync(writer.fileno())
        return b''.join(chunks) if destination is None else observed
    finally:
        if writer is not None: writer.close()
        os.close(fd)


def runtime_source_identity(*, copy_to=None):
    """Exact runtime resource rows; this never proves native acceptance."""
    if getattr(sys,'frozen',False):
        return _frozen_inventory()[1]
    root = Path(__file__).resolve().parents[2]
    for item in (root/'backend').rglob('*'):
        if 'tests' in item.relative_to(root/'backend').parts:continue
        if item.suffix in {'.so','.pyd','.dll'} or item.suffix=='.pyc' and '__pycache__' not in item.parts:
            raise ExecutionError('Current runtime contains an unreviewed source-shadowing executable')
    files = sorted(p for p in (root/'backend').rglob('*.py') if 'tests' not in p.relative_to(root/'backend').parts)
    if not 1 <= len(files) <= MAX_FILES: raise ExecutionError('Current runtime source inventory exceeds its bound')
    rows = []; total = 0
    for path in files:
        raw = _read(path, MAX_FILE); total += len(raw)
        if total > MAX_TOTAL: raise ExecutionError('Current runtime source inventory exceeds its total bound')
        rows.append({'path': path.relative_to(root).as_posix(), 'size': len(raw), 'sha256': update._sha(raw)})
        if copy_to is not None:
            _read(path, len(raw), expected=rows[-1]['sha256'], destination=copy_to/path.relative_to(root))
    if files != sorted(p for p in (root/'backend').rglob('*.py') if 'tests' not in p.relative_to(root/'backend').parts):
        raise ExecutionError('Current runtime source inventory changed')
    return update._sha(update._canonical(rows))


def _frozen_inventory():
    """Bind every bundled backend source resource to this compiled inventory."""
    if not getattr(sys,'frozen',False) or not isinstance(getattr(sys,'_MEIPASS',None),str):
        raise ExecutionError('requires_target: fixed CPU worker requires a compiled frozen backend')
    root=update._unlinked(sys._MEIPASS)
    inventory=_json(_read(root/'backend-build-inventory.json',MAX_RESULT))
    if (not isinstance(inventory,dict) or type(inventory.get(CPU_PROTOCOL)) is not int
            or inventory[CPU_PROTOCOL]!=1 or type(inventory.get('owned_application_launch_controller_protocol')) is not int
            or inventory['owned_application_launch_controller_protocol']!=1
            or not update._hex(inventory.get('build_identity_sha256'))
            or update._sha(update._canonical({k:v for k,v in inventory.items() if k!='build_identity_sha256'}))!=inventory['build_identity_sha256']
            or not isinstance(inventory.get('resources'),list) or len(inventory['resources'])>MAX_FILES*2):
        raise ExecutionError('Frozen CPU worker inventory capability is missing or changed')
    pins={}
    for row in inventory['resources']:
        if not isinstance(row,dict) or set(row)!={'path','sha256'} or not update._hex(row['sha256']):
            raise ExecutionError('Invalid frozen CPU resource row')
        name=update._safe_path(row['path'])
        if name in pins:raise ExecutionError('Duplicate frozen CPU resource row')
        pins[name]=row['sha256']
    if not set(CPU_RESOURCES)<=pins.keys():raise ExecutionError('Frozen CPU prerequisites are not checksum-bound')
    names=sorted(name for name in pins if name.startswith('backend/') and name.endswith('.py'))
    if not 1<=len(names)<=MAX_FILES:raise ExecutionError('Frozen CPU source resource bound exceeded')
    # Native libraries are legitimate bundled dependencies. Only source rows
    # are a source digest; none are put on sys.path or imported dynamically.
    actual=sorted(p.relative_to(root).as_posix() for p in (root/'backend').rglob('*.py'))
    if actual!=names:raise ExecutionError('Frozen backend source resource membership differs')
    total=0;rows=[]
    for name in sorted(set(names)|set(CPU_RESOURCES)):
        raw=_read(root/name,MAX_FILE,expected=pins[name]);total+=len(raw)
        if total>MAX_TOTAL:raise ExecutionError('Frozen CPU source resources exceed total bound')
        if name in names:rows.append({'path':name,'size':len(raw),'sha256':pins[name]})
    rows.sort(key=lambda row:row['path'])
    if actual!=sorted(p.relative_to(root).as_posix() for p in (root/'backend').rglob('*.py')):
        raise ExecutionError('Frozen CPU source resources changed')
    return inventory,update._sha(update._canonical(rows))


def _scope(frozen):
    if type(frozen) is not bool:raise ExecutionError('Invalid CPU runtime mode')
    return 'controlled_frozen_backend' if frozen else 'controlled_source_backend'


def _project(root, workspace_id, project_id):
    from backend.engine.global_store_paths import resolve_store_path
    if not update._hex(workspace_id, 32) or not update._hex(project_id, 32):
        raise ExecutionError('Known-image requires an explicit workspace/project pair')
    registry = update._unlinked(resolve_store_path(root/'projects'/'.context.sqlite3'))
    # No ContextRegistry constructor, discovery, migration, creation or fallback.
    # SQLite only opens this private exact main/WAL copy. Reopening the original
    # pathname after a regular-file check would permit a FIFO/replacement race.
    raw=_read(registry,8*1024**2);wal=update._unlinked(registry.with_name(registry.name+'-wal'))
    wal_raw=_read(wal,8*1024**2) if wal.exists() else None
    with tempfile.TemporaryDirectory(prefix='owned-cpu-scope-') as temporary:
        copied=Path(temporary)/'context.sqlite3';copied.write_bytes(raw);copied.chmod(0o600)
        if wal_raw is not None:copied.with_name(copied.name+'-wal').write_bytes(wal_raw)
        try:db = sqlite3.connect(copied.as_uri()+'?mode=ro', uri=True, timeout=2)
        except sqlite3.Error as exc:raise ExecutionError('Unreadable registered CPU project snapshot') from exc
        try:
            db.execute('PRAGMA query_only=ON')
            rows = db.execute('SELECT l.scope_key,l.path,p.path FROM project_locations l JOIN projects p ON p.id=l.scope_key '
                'WHERE l.workspace_id=? AND l.project_id=? LIMIT 2', (workspace_id, project_id)).fetchall()
        except sqlite3.Error as exc:raise ExecutionError('Unsupported registered CPU project snapshot') from exc
        finally: db.close()
        if _read(registry,8*1024**2)!=raw or (_read(wal,8*1024**2) if wal.exists() else None)!=wal_raw:
            raise ExecutionError('Registered CPU project source changed during snapshot')
    if len(rows) != 1 or rows[0][1] != rows[0][2]: raise ExecutionError('Known-image project scope is absent or ambiguous')
    key, named, _ = rows[0]
    if not update._hex(key, 32) or not isinstance(named, str) or not Path(named).is_absolute() or str(Path(named)) != named:
        raise ExecutionError('Registered known-image project scope is invalid')
    directory = update._unlinked(named)
    if not directory.is_relative_to(root/'projects') or directory == root/'projects' or not directory.is_dir():
        raise ExecutionError('Known-image project must be contained in the owned project folder')
    return directory, key


def _members(package):
    if not package.is_dir(): raise ExecutionError('Known-image package is missing')
    members = set()
    for path in package.rglob('*'):
        update._unlinked(path)
        if path.is_file(): members.add(path.relative_to(package).as_posix())
        elif not path.is_dir(): raise ExecutionError('Known-image package contains a special file')
        if len(members) > MAX_FILES: raise ExecutionError('Known-image package has too many files')
    return members


def admit_plan(root, workspace_id, project_id, plan_sha256, *, copy_to=None):
    """Independently verify the fixed registered capability and every source row."""
    root, _ = update._root(root)
    if not update._hex(plan_sha256): raise ExecutionError('An independent known-image plan pin is required')
    directory, key = _project(root, workspace_id, project_id)
    raw = _read(directory/PLAN, expected=plan_sha256); plan = _json(raw)
    if (not isinstance(plan, dict) or set(plan) != PLAN_FIELDS or type(plan['schema_version']) is not int
            or plan['schema_version'] != 1 or plan['kind'] != 'owned_cpu_ocr_known_image_plan'
            or plan['workspace_id'] != workspace_id or plan['project_id'] != project_id
            or plan['package_path'] != PACKAGE or plan['input_path'] != INPUT
            or plan['device'] != 'cpu' or type(plan['cpu_threads']) is not int or plan['cpu_threads'] != 1
            or type(plan['deadline_ms']) is not int or not 1 <= plan['deadline_ms'] <= 60000
            or any(not update._hex(plan[name]) for name in ('project_manifest_sha256', 'package_manifest_sha256',
                'graph_sha256', 'input_sha256', 'semantic_output_sha256', 'runtime_source_sha256'))):
        raise ExecutionError('Invalid bounded CPU OCR known-image plan')
    project = _json(_read(directory/'project.json', 1024**2, expected=plan['project_manifest_sha256']))
    if not isinstance(project, dict) or project.get('id') != project_id or project.get('project_dir') != str(directory):
        raise ExecutionError('Registered project manifest identity differs')
    package = update._unlinked(directory/PACKAGE)
    manifest_raw = _read(package/'manifest.json', MAX_RESULT, expected=plan['package_manifest_sha256'])
    manifest = _json(manifest_raw); files = manifest.get('files') if isinstance(manifest, dict) else None
    models = manifest.get('models') if isinstance(manifest, dict) else None
    if (not isinstance(manifest,dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES
            or not isinstance(models, list) or not 1 <= len(models) <= 16):
        raise ExecutionError('Known-image package inventory is incomplete')
    pins = {}; total = len(manifest_raw)
    for row in files:
        if (not isinstance(row, dict) or set(row) != {'path','size','sha256'} or type(row['size']) is not int
                or not 0 <= row['size'] <= MAX_FILE or not update._hex(row['sha256'])):
            raise ExecutionError('Known-image package row is invalid')
        name = update._safe_path(row['path'])
        if name in pins or name == 'manifest.json': raise ExecutionError('Duplicate known-image package row')
        pins[name] = row; total += row['size']
        if total > MAX_TOTAL: raise ExecutionError('Known-image package exceeds its reviewed total bound')
    if _members(package) != {'manifest.json', *pins}:
        raise ExecutionError('Known-image package contains unlisted files')
    if 'pipeline.json' not in pins or pins['pipeline.json']['sha256'] != plan['graph_sha256']:
        raise ExecutionError('Known-image graph pin differs')
    checkpoints = plan['checkpoints']; expected = []
    for row in models:
        if (not isinstance(row, dict) or set(row) != {'job_id','task','checkpoint'} or not update._hex(row['job_id'],32)
                or row['task'] != 'ocr' or row['checkpoint'] != 'models/'+row['job_id']+'/best_model.pt'
                or row['checkpoint'] not in pins): raise ExecutionError('This execution contract requires exact OCR checkpoints')
        expected.append({'job_id':row['job_id'], 'sha256':pins[row['checkpoint']]['sha256']})
    if (not isinstance(checkpoints,list) or len({row['job_id'] for row in models}) != len(models)
            or update._canonical(checkpoints) != update._canonical(expected)):
        raise ExecutionError('Independent known-image checkpoint pins differ')
    for name,row in pins.items():
        target = package/name
        if target.stat().st_size != row['size']: raise ExecutionError('Known-image package size differs')
        _read(target, row['size'], expected=row['sha256'], destination=copy_to/'package'/name if copy_to else None)
    if copy_to:
        destination = copy_to/'package'/'manifest.json'; destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_bytes(manifest_raw)
    _read(directory/INPUT, MAX_FILE, expected=plan['input_sha256'], destination=copy_to/'input.png' if copy_to else None)
    policy = plan['release_policy']
    if 'release' in manifest:
        if not isinstance(policy,dict) or set(policy) != {'sha256'} or not update._hex(policy['sha256']):
            raise ExecutionError('Released known-image package requires separately pinned approval policy')
        policy_path = directory/'delivery'/'launch-known-image'/'release-policy.json'
        policy_raw=_read(policy_path, MAX_RESULT, expected=policy['sha256'])
        from backend.engine.flow_package_runtime import verify_flow_package
        from backend.engine.inspection_service import _verify_release_policy
        # Existing semantic validators reopen names. They receive only captured
        # bounded private bytes; none can reopen a raced original file/FIFO.
        with tempfile.TemporaryDirectory(prefix='owned-cpu-release-') as temporary:
            snapshot=Path(temporary);copied_package=snapshot/'package';copied_package.mkdir(mode=0o700)
            for name,row in pins.items():
                _read(package/name,row['size'],expected=row['sha256'],destination=copied_package/name)
            (copied_package/'manifest.json').write_bytes(manifest_raw)
            copied_policy=snapshot/'release-policy.json';copied_policy.write_bytes(policy_raw)
            _, resolved = verify_flow_package(copied_package)
            _verify_release_policy(copied_package,resolved,copied_policy,device='cpu')
    elif policy is not None: raise ExecutionError('Unreleased known-image package cannot invent an approval policy')
    if runtime_source_identity(copy_to=copy_to/'runtime' if copy_to else None) != plan['runtime_source_sha256']:
        raise ExecutionError('Reviewed current runtime source differs')
    if _members(package) != {'manifest.json', *pins}: raise ExecutionError('Known-image inventory changed')
    return {'plan':plan, 'plan_sha256':plan_sha256, 'project_path':str(directory), 'scope_key':key}


def semantic_output(result):
    if (not isinstance(result,dict) or result.get('status') != 'success' or not isinstance(result.get('final_verdict'),str)
            or result['final_verdict'] not in {'OK','NG'}
            or result.get('image_id') != 'owned-cpu-known-image'
            or result.get('error') or result.get('error_message') or result.get('stop_node_id') is not None or not isinstance(result.get('crops'),list)
            or not 1 <= len(result['crops']) <= 1000 or not isinstance(result.get('execution_steps'),list)
            or not 1 <= len(result['execution_steps']) <= 1000
            or any(not isinstance(step,dict) or not isinstance(step.get('status'),str) or step['status'] not in {'passed','flagged_ng','skipped'} for step in result['execution_steps'])
            or not any(step.get('status') in {'passed','flagged_ng'} and step.get('output_payload_type')=='result'
                       for step in result['execution_steps'])):
        raise ExecutionError('Known-image requires a complete actual trained OCR flow result')
    texts = []
    for crop in result['crops']:
        text = crop.get('recognized_text') if isinstance(crop,dict) else None
        confidence = crop.get('confidence') if isinstance(crop,dict) else None
        rules = crop.get('ocr_rule_result') if isinstance(crop,dict) else None
        if (not isinstance(text,str) or len(text)>4096 or not isinstance(crop.get('source_node_id'),str)
                or not isinstance(crop.get('original_text'),str) or not isinstance(crop.get('corrected_text'),str)
                or type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1
                or not isinstance(rules,dict) or type(rules.get('passed')) is not bool
                or not isinstance(rules.get('failed_rules'),list)):
            raise ExecutionError('Known-image trained OCR output is missing or invalid')
        texts.append(text)
    fields = ('final_verdict','roi_count','defective_roi_count','routed_output_node_id')
    resources=result.get('execution_resources')
    if (any(type(result.get(name)) is not int or result[name] < 0 for name in ('roi_count','defective_roi_count'))
            or result['roi_count']!=len(texts) or result['defective_roi_count']>result['roi_count']
            or not isinstance(result.get('routed_output_node_id'),str)
            or not isinstance(resources,dict) or resources.get('device')!='cpu'
            or any(type(resources.get(name)) is not int or resources[name]!=1 for name in
                ('requested_workers','requested_device_slots','effective_device_slots','engine_device_capacity'))):
        raise ExecutionError('Known-image result counts are invalid')
    return {**{name:result.get(name) for name in fields}, 'recognized_texts':texts}


def validate_result(result, capability):
    """Bind actual execution evidence to the reviewed graph and input dimensions."""
    semantic=semantic_output(result)
    directory=Path(capability['project_path']);plan=capability['plan']
    graph=_json(_read(directory/PACKAGE/'pipeline.json', MAX_RESULT, expected=plan['graph_sha256']))
    from backend.engine.flow_provenance import pipeline_sha256
    if result.get('graph_sha256')!=pipeline_sha256(graph): raise ExecutionError('CPU result graph identity differs')
    nodes=graph.get('nodes') if isinstance(graph,dict) else None
    if not isinstance(nodes,list) or not nodes: raise ExecutionError('CPU result graph nodes are absent')
    if any(not isinstance(node,dict) or not isinstance(node.get('id'),str) or not isinstance(node.get('data'),dict) for node in nodes):
        raise ExecutionError('CPU result graph node shape differs')
    by_id={node['id']:node for node in nodes}
    if len(by_id)!=len(nodes):raise ExecutionError('CPU result graph has duplicate nodes')
    steps=result['execution_steps'];seen=set()
    for step in steps:
        name=step.get('node_id')
        if not isinstance(name,str) or name not in by_id or name in seen:
            raise ExecutionError('CPU execution step identity differs from reviewed graph')
        seen.add(name)
    if semantic['routed_output_node_id'] not in seen or by_id[semantic['routed_output_node_id']]['data']['node_type']!='output':
        raise ExecutionError('CPU routed output was not executed')
    for crop in result['crops']:
        node=by_id.get(crop['source_node_id'])
        if node is None or node['id'] not in seen or node['data'].get('task')!='ocr':
            raise ExecutionError('CPU crop has no reviewed trained OCR source')
    from PIL import Image
    import io
    with Image.open(io.BytesIO(_read(directory/INPUT, MAX_FILE, expected=plan['input_sha256']))) as image:
        if list(image.size)!=result.get('inspected_image_size'): raise ExecutionError('CPU input dimensions differ')
    return semantic


def _runtime_execution(result, plan, worker_pid):
    runtime=result.get('runtime_execution')
    if (not isinstance(runtime,dict) or set(runtime)!={'device','cpu_threads','deadline_ms','isolated_process','pid','elapsed_ms'}
            or runtime['device']!='cpu' or type(runtime['cpu_threads']) is not int or runtime['cpu_threads']!=1
            or type(runtime['deadline_ms']) is not int or runtime['deadline_ms']!=plan['deadline_ms']
            or runtime['isolated_process'] is not True or type(runtime['pid']) is not int or runtime['pid']!=worker_pid
            or type(runtime['elapsed_ms']) not in (int,float) or not math.isfinite(runtime['elapsed_ms']) or runtime['elapsed_ms']<0):
        raise ExecutionError('CPU output runtime execution evidence differs')


def _validate_receipt(row,intent,bootstrap,receipt):
    if not isinstance(receipt,dict) or set(receipt)!=RECEIPT_FIELDS:
        raise ExecutionError('CPU execution receipt fields differ')
    request_=intent['request'];capability=intent['capability'];plan=capability['plan']
    identities={'schema_version':1,'kind':'owned_cpu_ocr_execution','status':'succeeded',
        'nonce':row['nonce'],'binding':row['binding'],'request_id':request_['request_id'],
        'request_sha256':update._sha(update._canonical(request_)),
        'challenge_sha256':update._sha(request_['challenge'].encode()),'backend_claim_sha256':request_['backend_claim_sha256'],
        'plan_sha256':request_['plan_sha256'],'epoch':bootstrap['epoch'],'main_process':row['process'],
        'backend_process':bootstrap['backend_process'],'backend_executable':bootstrap['backend_executable'],
        'backend_executable_sha256':bootstrap['backend_executable_sha256'],'backend_frozen':bootstrap['backend_frozen'],
        'execution_scope':_scope(bootstrap['backend_frozen']),'runtime_source_sha256':plan['runtime_source_sha256'],
        'workspace_id':request_['workspace_id'],'project_id':request_['project_id'],
        'scope_key':capability['scope_key'],'checkpoints':plan['checkpoints'],
        'package_manifest_sha256':plan['package_manifest_sha256'],'graph_sha256':plan['graph_sha256'],
        'input_sha256':plan['input_sha256'],'output_path':OUTPUTS+'/'+request_['request_id']+'.json',
        'semantic_output_sha256':plan['semantic_output_sha256'],'actual_cpu_execution_verified':True,
        'owned_backend_execution_origin_verified':True,'worker_process_tree_exit_verified':False,
        'actual_application_inference_verified':False,'model_quality_approved':False,'native_app_handshake_verified':False,'release_ready':False}
    if (any(update._canonical(receipt[name])!=update._canonical(expected) for name,expected in identities.items())
            or type(receipt['worker_pid']) is not int or receipt['worker_pid']<1
            or not update._hex(receipt['output_sha256']) or not isinstance(receipt['semantic_output'],dict)
            or update._sha(update._canonical(receipt['semantic_output']))!=plan['semantic_output_sha256']):
        raise ExecutionError('CPU execution receipt scope/checkpoint/worker identity differs')


def _live_origin(root,row,bootstrap):
    from backend.engine.application_launch_controller import _same_identity,_backend_artifact
    import psutil
    if (row.get('state')!='ready' or row.get('claimed') is not True
            or not update._hex(row.get('ready_receipt_sha256'))):
        raise ExecutionError('Original CPU ownership is not ready')
    try:
        for process in (row['supervisor'],row['process'],bootstrap['backend_process']):
            if not _same_identity(process,process['pid']):raise ExecutionError('Original CPU process birth or command changed')
        main=row['process'];backend=bootstrap['backend_process']
        if (psutil.Process(main['pid']).ppid()!=row['supervisor']['pid'] or psutil.Process(backend['pid']).ppid()!=main['pid']
                or os.getsid(main['pid'])!=main['pid'] or os.getpgid(main['pid'])!=main['pid']):
            raise ExecutionError('Original CPU process parent/session changed')
        _backend_artifact(root,row['binding'],bootstrap['backend_executable'],bootstrap['backend_executable_sha256'],
            bootstrap['backend_build_identity_sha256'],bootstrap['backend_frozen'],backend['pid'])
    except (psutil.Error,OSError) as exc:raise ExecutionError('Original CPU process ownership is ambiguous') from exc


def request(owner, capability, epoch):
    return {'schema_version':1,'kind':'cpu_execution_request','challenge':secrets.token_hex(32),
        'request_id':secrets.token_hex(16),'nonce':owner.nonce,'epoch':epoch,
        'binding_sha256':update._sha(update._canonical(owner._owned()['binding'])),
        'backend_claim_sha256':update._sha(update._canonical(owner._authenticated_backend_proof)),
        'workspace_id':capability['plan']['workspace_id'],'project_id':capability['plan']['project_id'],
        'plan_sha256':capability['plan_sha256']}


def validate_request(frame, proof, root, *, absolute_deadline=None):
    # Only an explicit original action bound opts into typed mutex-entry retry.
    # Generic callers keep the original nonblocking transition admission.
    def current_request():
        if (not isinstance(frame,dict) or set(frame)!=REQUEST_FIELDS or type(frame['schema_version']) is not int
                or frame['schema_version']!=1 or frame['kind']!='cpu_execution_request'
                or any(not update._hex(frame[name],32) for name in ('request_id','nonce','epoch','workspace_id','project_id'))
                or any(not update._hex(frame[name]) for name in ('challenge','binding_sha256','backend_claim_sha256','plan_sha256'))
                or frame['nonce']!=proof['nonce'] or frame['epoch']!=proof['epoch'] or frame['binding_sha256']!=proof['binding_sha256']):
            raise ExecutionError('Foreign or stale CPU execution request')
        if frame['backend_claim_sha256']!=update._sha(update._canonical(proof)):
            raise ExecutionError('CPU execution request has a different original backend claim')
    current_request()
    from backend.engine import application_launch_lease as lease
    if absolute_deadline is None:
        admission = lease._transition_admission(root, frame['nonce'])
    else:
        from backend.engine.application_launch_handshake import _transition_admission_before_deadline
        original_frame = update._canonical(frame)
        original_proof = update._canonical(proof)
        def before_attempt():
            current_request()
            if update._canonical(frame) != original_frame or update._canonical(proof) != original_proof:
                raise ExecutionError('CPU request or backend claim changed during original transition entry')
        admission = _transition_admission_before_deadline(root, frame['nonce'], absolute_deadline,
            before_attempt=before_attempt)
    with admission:
        if absolute_deadline is not None: before_attempt()
        row = lease._load(root)
        path=root/lease.LEASES/frame['nonce']/'cpu-execution-intent.json'
        intent=_json(_read(path))
        if (row['state']!='ready' or row.get('cpu_execution') is None
                or update._canonical(intent['request'])!=update._canonical(frame)
                or row['cpu_execution']['receipt_sha256'] is not None):
            raise ExecutionError('CPU request has no original durable unexecuted admission')
    return intent


@contextmanager
def _retained_snapshot(outputs, request_id):
    # A completed leader or killed process group is not whole-tree exit proof.
    # Keep bounded original worker inputs on both success and uncertain failure.
    directory=update._unlinked(outputs/('.owned-cpu-'+request_id))
    directory.mkdir(mode=0o700)
    update.migration._sync_directories(outputs,recursive=False)
    yield directory


def _frozen_worker_admission(path, digest):
    """Validate the original direct parent and seals before writable caches."""
    inventory,source_digest=_frozen_inventory()
    if not update._hex(digest):raise ExecutionError('Frozen worker requires an independent request pin')
    raw=_read(path,65536,expected=digest);capsule=_json(raw)
    if (not isinstance(capsule,dict) or set(capsule)!={'schema_version','kind','root','request','backend_proof','capability'}
            or type(capsule['schema_version']) is not int or capsule['schema_version']!=1
            or capsule['kind']!='owned_frozen_cpu_worker' or not isinstance(capsule['root'],str)):
        raise ExecutionError('Invalid fixed frozen worker capsule')
    root,_=update._root(capsule['root']);proof=capsule['backend_proof'];frame=capsule['request']
    from backend.engine.application_launch_controller import _same_identity,_backend_artifact
    from backend.engine import application_launch_lease as lease
    import psutil
    # No request can manufacture the original process birth/command or make a
    # foreign process become our direct parent. The controller's original
    # authenticated proof is independently sealed in the intent hash.
    if (not isinstance(proof,dict) or set(proof)!={'schema_version','kind','challenge','epoch','nonce','binding_sha256',
            'process','executable','executable_sha256','build_identity_sha256','frozen'}
            or proof['frozen'] is not True or proof['build_identity_sha256']!=inventory['build_identity_sha256']
            or not isinstance(proof['process'],dict) or proof['process'].get('pid')!=os.getppid()
            or not _same_identity(proof['process'],os.getppid())
            or psutil.Process(os.getppid()).exe()!=sys.executable or proof['executable']!=sys.executable):
        raise ExecutionError('Frozen CPU worker has a foreign original backend parent/build')
    intent=validate_request(frame,proof,root)
    row=lease._load(root)
    bootstrap=_json(_read(root/lease.LEASES/frame['nonce']/'bootstrap-receipt.json'))
    if (bootstrap['backend_frozen'] is not True or bootstrap['backend_process']!=proof['process']
            or bootstrap['backend_build_identity_sha256']!=proof['build_identity_sha256']
            or bootstrap['backend_executable_sha256']!=proof['executable_sha256']
            or bootstrap['epoch']!=proof['epoch']):
        raise ExecutionError('Frozen CPU worker original epoch/receipt differs')
    _live_origin(root,row,bootstrap)
    _backend_artifact(root,row['binding'],proof['executable'],proof['executable_sha256'],proof['build_identity_sha256'],True,os.getppid())
    capability=admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'])
    if (update._canonical(capability)!=update._canonical(intent['capability'])
            or update._canonical(capability)!=update._canonical(capsule['capability'])
            or capability['plan']['runtime_source_sha256']!=source_digest):
        raise ExecutionError('Frozen CPU worker capability/source differs')
    directory=update._unlinked(Path(capability['project_path'])/OUTPUTS/('.owned-cpu-'+frame['request_id']))
    if update._unlinked(path)!=directory/'worker-request.json':raise ExecutionError('Frozen CPU request is outside its fixed retained path')
    if _read(path,65536,expected=digest)!=raw:raise ExecutionError('Frozen CPU request changed during admission')
    return root,frame,proof,capability,directory


def frozen_worker_main(argv=None):
    """Only the fixed compiled entry calls this; arbitrary modules are refused."""
    if not getattr(sys,'frozen',False):
        print('requires_target: fixed CPU worker requires compiled backend',file=sys.stderr)
        return 2
    import argparse
    parser=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    parser.add_argument('--request-file',required=True);parser.add_argument('--request-sha256',required=True)
    args=parser.parse_args(argv)
    try:
        root,frame,proof,capability,directory=_frozen_worker_admission(args.request_file,args.request_sha256)
        # A interrupted or previous execution is never retried by the worker.
        marker=directory/'worker-admission.json';update._unlinked(marker)
        fd=os.open(marker,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        with os.fdopen(fd,'wb') as writer:
            writer.write(update._canonical({'request_sha256':args.request_sha256,'parent':proof['process'],'worker_pid':os.getpid()}))
            writer.flush();os.fsync(writer.fileno())
        update.migration._sync_directories(directory,recursive=False)
        # Independently re-read every exact private row. Heavy model imports and
        # library-created home/cache state occur only after parent admission.
        plan=capability['plan'];package=directory/'package';manifest=_json(_read(package/'manifest.json',MAX_RESULT,expected=plan['package_manifest_sha256']))
        if _members(package)!={'manifest.json',*(row['path'] for row in manifest['files'])}:
            raise ExecutionError('Frozen CPU snapshot has unlisted files')
        for item in manifest['files']:_read(package/item['path'],item['size'],expected=item['sha256'])
        _read(directory/'input.png',MAX_FILE,expected=plan['input_sha256'])
        for name in ('home','cache','tmp'):
            folder=update._unlinked(directory/name)
            if not folder.is_dir() or folder.stat().st_mode&0o077:raise ExecutionError('Frozen CPU cache is not private')
        if (os.environ.get('HOME')!=str(directory/'home') or os.environ.get('TMPDIR')!=str(directory/'tmp')
                or os.environ.get('CUDA_VISIBLE_DEVICES')!='' or os.environ.get('NVIDIA_VISIBLE_DEVICES')!='none'):
            raise ExecutionError('Frozen CPU environment differs from fixed private contract')
        from backend.engine.flow_package_runtime import run_flow_package
        import torch
        torch.set_num_threads(1)
        result=run_flow_package(package,directory/'input.png','owned-cpu-known-image',device='cpu',cpu_threads=1,_owned_worker=True)
        validate_result(result,capability)
        after=admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'])
        if update._canonical(after)!=update._canonical(capability):raise ExecutionError('Frozen CPU artifacts changed during arithmetic')
        raw=update._canonical(result)
        if len(raw)>MAX_RESULT:raise ExecutionError('Frozen CPU result exceeds bound')
        from backend.engine import application_launch_lease as lease
        with lease._transition_admission(root,frame['nonce']):
            original=_json(_read(root/lease.LEASES/frame['nonce']/'bootstrap-receipt.json'))
            _live_origin(root,lease._load(root),original)
            fd=os.open(update._unlinked(directory/'result.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as writer:writer.write(raw);writer.flush();os.fsync(writer.fileno())
            update.migration._sync_directories(directory,recursive=False)
        return 0
    except (ValueError,OSError,KeyError,TypeError) as exc:
        print('Frozen CPU execution refused: '+str(exc)[:1000],file=sys.stderr)
        return 2


def execute_backend(frame, proof, root):
    """Only the authenticated cache consumer calls this, under its shared life."""
    from backend.engine import application_launch_handshake as handshake
    from backend.engine.application_launch_handshake import owned_cpu_writer_scope
    cache=handshake._CACHE
    if (proof.get('frozen') is False and cache is not None and 'writer' in cache.get('challenge',{})):
        from backend.engine import application_owned_cpu_child_relay as cpu
        producer=cpu.admit_backend_source_cpu(frame,proof,root)
        try:
            completed=_execute_backend_admitted(frame,proof,root,(),source_producer=producer)
            cpu.completion_ready(producer,completed)
            cpu.finish_backend_source_cpu(producer,completed)
            return completed
        except BaseException:
            cpu._retain(producer);raise
    # Admit before snapshot, output, home/cache or worker side effects. Retain
    # the original writer OFD through outcome and all output/source validation.
    with owned_cpu_writer_scope() as writer_pass_fds:
        return _execute_backend_admitted(frame, proof, root, writer_pass_fds)


def _execute_backend_admitted(frame, proof, root, writer_pass_fds, *, source_producer=None):
    def source_current():
        if source_producer is not None:
            from backend.engine import application_owned_cpu_child_relay as cpu
            cpu._original(source_producer)
    source_current()
    if proof['frozen']:
        inventory,_=_frozen_inventory()
        if inventory['build_identity_sha256']!=proof['build_identity_sha256']:
            raise ExecutionError('Frozen CPU backend inventory differs from original claim')
    intent = validate_request(frame,proof,root)
    from backend.engine import application_launch_lease as lease
    row=lease._load(root)
    original=_json(_read(root/lease.LEASES/frame['nonce']/'bootstrap-receipt.json'))
    _live_origin(root,row,original)
    capability = admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'])
    if update._canonical(capability)!=update._canonical(intent['capability']): raise ExecutionError('Reviewed project capability changed')
    source_current()
    directory = Path(capability['project_path']); outputs=update._unlinked(directory/OUTPUTS)
    outputs.mkdir(parents=True,exist_ok=True)
    with _retained_snapshot(outputs,frame['request_id']) as temporary:
        temporary=Path(temporary)
        copied=admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'],copy_to=temporary)
        if update._canonical(copied)!=update._canonical(capability): raise ExecutionError('CPU snapshot capability changed')
        source_current()
        _checkpoint('before_cpu_worker')
        plan=capability['plan']; request_path=temporary/'request.json'; output=temporary/'result.json'
        request_path.write_bytes(update._canonical({'package':str(temporary/'package'),'image':str(temporary/'input.png')}))
        runtime_root=str(temporary/'runtime')
        update.migration._sync_directories(temporary,recursive=True)
        bootstrap = ('import sys,json;sys.dont_write_bytecode=True;sys.path.insert(0,'+repr(runtime_root)+');'
            'from pathlib import Path;from backend.engine.flow_package_runtime import run_flow_package;'
            'assert Path(run_flow_package.__code__.co_filename).resolve()==Path('+repr(runtime_root)+')/"backend/engine/flow_package_runtime.py";'
            'import torch;torch.set_num_threads(1);'
            'r=json.loads(Path(sys.argv[1]).read_bytes());'
            'v=run_flow_package(Path(r["package"]),Path(r["image"]),"owned-cpu-known-image",device="cpu",cpu_threads=1,_owned_worker=True);'
            'Path(sys.argv[2]).write_text(json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False),encoding="utf-8")')
        home=temporary/'home';cache=temporary/'cache';scratch=temporary/'tmp'
        for folder in (home,cache,scratch):folder.mkdir(mode=0o700)
        environment={'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
            'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none',
            'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1',
            'HF_DATASETS_OFFLINE':'1','HOME':str(home),'USERPROFILE':str(home),'XDG_CACHE_HOME':str(cache),
            'XDG_CONFIG_HOME':str(home/'config'),'TORCH_HOME':str(cache/'torch'),'HF_HOME':str(cache/'huggingface'),
            'HUGGINGFACE_HUB_CACHE':str(cache/'huggingface/hub'),'TRANSFORMERS_CACHE':str(cache/'transformers'),
            'MPLCONFIGDIR':str(cache/'matplotlib'),'TMPDIR':str(scratch),'TMP':str(scratch),'TEMP':str(scratch),
            'YOLO_CONFIG_DIR':str(home/'yolo'),'OPENBLAS_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1',
            'VECLIB_MAXIMUM_THREADS':'1','BLIS_NUM_THREADS':'1'}
        from backend.engine.runtime_deadline import execute_owned_process
        command=[sys.executable,'-I','-B','-X','pycache_prefix='+str(temporary/'bytecode'),'-c',bootstrap,str(request_path),str(output)]
        if proof['frozen']:
            worker_request=temporary/'worker-request.json'
            capsule=update._canonical({'schema_version':1,'kind':'owned_frozen_cpu_worker','root':str(root),
                'request':frame,'backend_proof':proof,'capability':capability})
            fd=os.open(worker_request,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as writer:writer.write(capsule);writer.flush();os.fsync(writer.fileno())
            update.migration._sync_directories(temporary,recursive=False)
            command=[sys.executable,'--owned-application-cpu-worker','--request-file',str(worker_request),
                '--request-sha256',update._sha(capsule)]
        if source_producer is not None:
            from backend.engine import application_owned_cpu_child_relay as cpu
            cpu.reserve_backend_cpu_child(source_producer,temporary)
            outcome=cpu.execute_source_process(source_producer,environment=environment)
        else:
            outcome=execute_owned_process(command,
                deadline_ms=plan['deadline_ms'],env=environment,cwd=temporary,pass_fds=writer_pass_fds)
        if outcome['status']!='completed' or outcome['returncode']!=0:
            raise ExecutionError('Owned CPU execution failed or timed out; process-tree exit remains unverified')
        result=_json(_read(output,MAX_RESULT)); semantic=validate_result(result,capability)
        if update._sha(update._canonical(semantic))!=plan['semantic_output_sha256']:
            raise ExecutionError('Actual known-image semantic output differs from independent pin')
        source_current()
        result['runtime_execution']={'device':'cpu','cpu_threads':1,'deadline_ms':plan['deadline_ms'],
            'isolated_process':True,'pid':outcome['pid'],'elapsed_ms':outcome['elapsed_ms']}
        # Recheck all original inputs/source while the exact private snapshot
        # still exists. No mutable package code was imported by the worker.
        after=admit_plan(root,frame['workspace_id'],frame['project_id'],frame['plan_sha256'])
        if update._canonical(after)!=update._canonical(capability): raise ExecutionError('CPU execution source changed')
        source_current()
        raw=update._canonical(result)
        if len(raw)>MAX_RESULT: raise ExecutionError('CPU result exceeds publication bound')
        # Publication and the final original-owner check share the lifecycle
        # transition admission. Recovery cannot win between the check and a
        # new public output; the backend lifespan still blocks cutover.
        publication = (cpu.source_publication_admission(source_producer) if source_producer is not None
                       else lease._transition_admission(root,frame['nonce']))
        with publication:
            _live_origin(root,lease._load(root),original)
            if source_producer is not None: cpu._current_source_producer(source_producer)
            path=outputs/(frame['request_id']+'.json'); update._unlinked(path)
            if path.exists(): raise ExecutionError('Foreign CPU output requires recovery')
            # Exclusive creation: interrupted output is not overwritten.
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
            with os.fdopen(fd,'wb') as writer: writer.write(raw);writer.flush();os.fsync(writer.fileno())
            update.migration._sync_directories(outputs,recursive=False)
            if source_producer is not None: cpu._current_source_producer(source_producer)
        _checkpoint('after_cpu_output')
        return {'schema_version':1,'kind':'cpu_execution_completed','request':frame,
            'backend_proof':proof,'output_path':OUTPUTS+'/'+path.name,'output_sha256':update._sha(raw),
            'semantic_output':semantic,'worker_pid':outcome['pid'],'runtime_source_sha256':plan['runtime_source_sha256']}


def verify_completion(owner, intent, proof):
    from backend.engine import application_launch_lease as lease
    from backend.engine.application_launch_controller import _same_identity, _backend_artifact
    names={'schema_version','kind','request','backend_proof','output_path','output_sha256','semantic_output','worker_pid','runtime_source_sha256'}
    if (not isinstance(proof,dict) or set(proof)!=names or type(proof['schema_version']) is not int
            or proof['schema_version']!=1 or proof['kind']!='cpu_execution_completed'
            or update._canonical(proof['request'])!=update._canonical(intent['request'])
            or not update._hex(proof['output_sha256']) or type(proof['worker_pid']) is not int or proof['worker_pid']<1):
        raise ExecutionError('CPU completion differs from original request')
    capability=intent['capability']; request_=intent['request']; plan=capability['plan']
    fresh=admit_plan(owner.root,request_['workspace_id'],request_['project_id'],request_['plan_sha256'])
    if update._canonical(fresh)!=update._canonical(capability): raise ExecutionError('CPU completion source capability changed')
    with lease._transition_admission(owner.root,owner.nonce):
        row=owner._owned();owner._binding(row);owner._live(row,row['process'])
        bootstrap=_json(_read(owner.root/lease.LEASES/owner.nonce/'bootstrap-receipt.json'))
        backend=proof['backend_proof']; expected={'schema_version':1,'kind':'backend_claim',
            'challenge':backend.get('challenge') if isinstance(backend,dict) else None,'epoch':bootstrap['epoch'],
            'nonce':owner.nonce,'binding_sha256':update._sha(update._canonical(row['binding'])),
            'process':bootstrap['backend_process'],'executable':bootstrap['backend_executable'],
            'executable_sha256':bootstrap['backend_executable_sha256'],
            'build_identity_sha256':bootstrap['backend_build_identity_sha256'],'frozen':bootstrap['backend_frozen']}
        if (update._canonical(backend)!=update._canonical(expected) or not update._hex(expected['challenge'])
                or update._canonical(backend)!=update._canonical(owner._authenticated_backend_proof)
                or update._sha(update._canonical(backend))!=request_['backend_claim_sha256']):
            raise ExecutionError('CPU completion backend epoch/build differs')
        process=bootstrap['backend_process']
        import psutil
        if not _same_identity(process,process['pid']) or psutil.Process(process['pid']).ppid()!=row['process']['pid']:
            raise ExecutionError('Original CPU backend birth or parent changed')
        _backend_artifact(owner.root,row['binding'],expected['executable'],expected['executable_sha256'],
            expected['build_identity_sha256'],expected['frozen'],process['pid'])
    output=OUTPUTS+'/'+request_['request_id']+'.json'
    if proof['output_path']!=output or proof['runtime_source_sha256']!=plan['runtime_source_sha256']:
        raise ExecutionError('CPU completion output or source path differs')
    raw=_read(Path(capability['project_path'])/output,MAX_RESULT,expected=proof['output_sha256'])
    result=_json(raw); semantic=validate_result(result,capability)
    _runtime_execution(result,plan,proof['worker_pid'])
    if (update._canonical(semantic)!=update._canonical(proof['semantic_output'])
            or update._sha(update._canonical(semantic))!=plan['semantic_output_sha256']):
        raise ExecutionError('Actual CPU completion output binding differs')
    return {'schema_version':1,'kind':'owned_cpu_ocr_execution','status':'succeeded',
        'nonce':owner.nonce,'binding':row['binding'],'request_id':request_['request_id'],
        'request_sha256':update._sha(update._canonical(request_)),'challenge_sha256':update._sha(request_['challenge'].encode()),
        'backend_claim_sha256':request_['backend_claim_sha256'],
        'plan_sha256':request_['plan_sha256'],'epoch':bootstrap['epoch'],'main_process':row['process'],
        'backend_process':bootstrap['backend_process'],'backend_executable':bootstrap['backend_executable'],
        'backend_executable_sha256':bootstrap['backend_executable_sha256'],'backend_frozen':bootstrap['backend_frozen'],
        'execution_scope':_scope(bootstrap['backend_frozen']),'runtime_source_sha256':plan['runtime_source_sha256'],
        'workspace_id':request_['workspace_id'],'project_id':request_['project_id'],'scope_key':capability['scope_key'],
        'package_manifest_sha256':plan['package_manifest_sha256'],'graph_sha256':plan['graph_sha256'],
        'checkpoints':plan['checkpoints'],'input_sha256':plan['input_sha256'],'output_path':output,
        'output_sha256':proof['output_sha256'],'semantic_output':semantic,'semantic_output_sha256':plan['semantic_output_sha256'],
        'worker_pid':proof['worker_pid'],'worker_process_tree_exit_verified':False,
        'actual_cpu_execution_verified':True,'owned_backend_execution_origin_verified':True,
        'actual_application_inference_verified':False,'model_quality_approved':False,'native_app_handshake_verified':False,'release_ready':False}


def encoded_proof(raw):
    if not isinstance(raw,str) or len(raw)>50000: raise ExecutionError('CPU proof encoding exceeds its bound')
    try: decoded=base64.b64decode(raw,validate=True)
    except (ValueError,TypeError) as exc: raise ExecutionError('Invalid CPU proof encoding') from exc
    if len(decoded)>32768: raise ExecutionError('CPU proof exceeds its bound')
    value=_json(decoded)
    if update._canonical(value)!=decoded: raise ExecutionError('CPU proof is not canonical')
    return value


def validate_sealed_execution(row, directory):
    """Validate original sealed intent/receipt bytes without repairing history."""
    state=row['cpu_execution']
    if (not isinstance(state,dict) or set(state)!={'request_id','request_sha256','intent_sha256','receipt_sha256'}
            or not update._hex(state['request_id'],32) or not update._hex(state['request_sha256'])
            or not update._hex(state['intent_sha256']) or state['receipt_sha256'] is not None and not update._hex(state['receipt_sha256'])
            or row['state'] in {'reserved','exited'} or not row['claimed'] or row['ready_receipt_sha256'] is None):
        raise ExecutionError('Invalid durable CPU execution admission')
    raw=_read(directory/'cpu-execution-intent.json');intent=_json(raw)
    if (update._sha(raw)!=state['intent_sha256'] or not isinstance(intent,dict)
            or set(intent)!={'schema_version','kind','request','capability'} or type(intent['schema_version']) is not int
            or intent['schema_version']!=1 or intent['kind']!='owned_cpu_execution_intent'):
        raise ExecutionError('CPU execution intent changed')
    request_=intent['request'];capability=intent['capability']
    if (not isinstance(request_,dict) or set(request_)!=REQUEST_FIELDS or type(request_['schema_version']) is not int
            or request_['schema_version']!=1 or request_['kind']!='cpu_execution_request'
            or any(not update._hex(request_[name],32) for name in ('request_id','nonce','epoch','workspace_id','project_id'))
            or any(not update._hex(request_[name]) for name in ('challenge','binding_sha256','backend_claim_sha256','plan_sha256'))
            or request_['nonce']!=row['nonce'] or request_['request_id']!=state['request_id']
            or update._sha(update._canonical(request_))!=state['request_sha256']
            or request_['binding_sha256']!=update._sha(update._canonical(row['binding']))
            or not isinstance(capability,dict) or set(capability)!={'plan','plan_sha256','project_path','scope_key'}
            or capability['plan_sha256']!=request_['plan_sha256'] or not update._hex(capability['scope_key'],32)
            or not isinstance(capability['plan'],dict) or set(capability['plan'])!=PLAN_FIELDS):
        raise ExecutionError('CPU execution intent has foreign binding')
    bootstrap=_json(_read(directory/'bootstrap-receipt.json'))
    if request_['epoch']!=bootstrap['epoch']:raise ExecutionError('CPU intent epoch differs from authenticated backend')
    members={'cpu-execution-intent.json'}
    if state['receipt_sha256'] is not None:
        raw=_read(directory/'cpu-execution-receipt.json');receipt=_json(raw);plan=capability['plan']
        _validate_receipt(row,intent,bootstrap,receipt)
        if (update._sha(raw)!=state['receipt_sha256'] or not isinstance(receipt,dict)
                or type(receipt.get('schema_version')) is not int or receipt['schema_version']!=1
                or receipt.get('kind')!='owned_cpu_ocr_execution' or receipt.get('status')!='succeeded'
                or receipt.get('nonce')!=row['nonce'] or receipt.get('request_id')!=request_['request_id']
                or receipt.get('request_sha256')!=state['request_sha256'] or receipt.get('plan_sha256')!=request_['plan_sha256']
                or receipt.get('challenge_sha256')!=update._sha(request_['challenge'].encode())
                or receipt.get('backend_claim_sha256')!=request_['backend_claim_sha256']
                or receipt.get('epoch')!=bootstrap['epoch'] or update._canonical(receipt.get('binding'))!=update._canonical(row['binding'])
                or update._canonical(receipt.get('main_process'))!=update._canonical(row['process'])
                or update._canonical(receipt.get('backend_process'))!=update._canonical(bootstrap['backend_process'])
                or receipt.get('backend_executable')!=bootstrap['backend_executable']
                or receipt.get('backend_executable_sha256')!=bootstrap['backend_executable_sha256']
                or receipt.get('backend_frozen') is not bootstrap['backend_frozen'] or receipt.get('execution_scope')!=_scope(bootstrap['backend_frozen'])
                or receipt.get('runtime_source_sha256')!=plan['runtime_source_sha256']
                or receipt.get('package_manifest_sha256')!=plan['package_manifest_sha256'] or receipt.get('graph_sha256')!=plan['graph_sha256']
                or receipt.get('input_sha256')!=plan['input_sha256'] or receipt.get('semantic_output_sha256')!=plan['semantic_output_sha256']
                or update._sha(update._canonical(receipt.get('semantic_output')))!=plan['semantic_output_sha256']
                or not update._hex(receipt.get('output_sha256')) or receipt.get('output_path')!=OUTPUTS+'/'+request_['request_id']+'.json'
                or receipt.get('actual_cpu_execution_verified') is not True or receipt.get('owned_backend_execution_origin_verified') is not True
                or any(receipt.get(name) is not False for name in ('worker_process_tree_exit_verified','actual_application_inference_verified','model_quality_approved','native_app_handshake_verified','release_ready'))):
            raise ExecutionError('CPU execution receipt changed or unbound')
        members.add('cpu-execution-receipt.json')
    return members


def recheck_receipt_artifacts(root, row, receipt):
    """A deliberate receipt retry/readback must still bind exact current bytes."""
    from backend.engine import application_launch_lease as lease
    directory=root/lease.LEASES/row['nonce']
    intent=_json(_read(directory/'cpu-execution-intent.json'));request_=intent['request']
    bootstrap=_json(_read(directory/'bootstrap-receipt.json'))
    _validate_receipt(row,intent,bootstrap,receipt);_live_origin(root,row,bootstrap)
    capability=admit_plan(root,request_['workspace_id'],request_['project_id'],request_['plan_sha256'])
    if update._canonical(capability)!=update._canonical(intent['capability']):raise ExecutionError('CPU receipt project scope changed')
    if receipt.get('output_path')!=OUTPUTS+'/'+request_['request_id']+'.json' or not update._hex(receipt.get('output_sha256')):
        raise ExecutionError('CPU receipt output identity differs')
    raw=_read(Path(capability['project_path'])/receipt['output_path'],MAX_RESULT,expected=receipt['output_sha256'])
    result=_json(raw);semantic=validate_result(result,capability)
    _runtime_execution(result,capability['plan'],receipt['worker_pid'])
    if (update._canonical(semantic)!=update._canonical(receipt.get('semantic_output'))
            or update._sha(update._canonical(semantic))!=capability['plan']['semantic_output_sha256']):
        raise ExecutionError('CPU receipt semantic output differs')
    return capability


def inspect_execution(args):
    """Read exact sealed source execution evidence; never lock transitions/repair."""
    from backend.engine import application_launch_lease as lease
    from backend.engine.application_launch_controller import _inspection_snapshot,_expected
    root,_=update._root(args.root)
    nonce=getattr(args,'expected_launch_nonce',None)
    workspace=getattr(args,'cpu_known_image_workspace_id',None);project=getattr(args,'cpu_known_image_project_id',None)
    pin=getattr(args,'cpu_known_image_plan_sha256',None)
    if not update._hex(nonce,32) or not update._hex(workspace,32) or not update._hex(project,32) or not update._hex(pin):
        raise ExecutionError('CPU inspection requires explicit original nonce and independent project/plan pins')
    with _inspection_snapshot(root):
        binding=update._launch_binding(root,args.authority,pinned_authority_sha256=args.pinned_authority_sha256)
        _expected(binding,args);row=lease._load(root)
        if row is None or row['nonce']!=nonce or update._canonical(row['binding'])!=update._canonical(binding):
            raise ExecutionError('CPU receipt belongs to a different original app/database pair')
        state=row.get('cpu_execution');receipt=None
        capability=admit_plan(root,workspace,project,pin)
        if state is not None:
            intent=_json(_read(root/lease.LEASES/nonce/'cpu-execution-intent.json'))
            if update._canonical(intent['capability'])!=update._canonical(capability):
                raise ExecutionError('CPU inspection project/plan differs from original request')
            if row['state']!='ready':raise ExecutionError('Original CPU ownership requires recovery')
            bootstrap=_json(_read(root/lease.LEASES/nonce/'bootstrap-receipt.json'));_live_origin(root,row,bootstrap)
            if state['receipt_sha256'] is not None:
                receipt=_json(_read(root/lease.LEASES/nonce/'cpu-execution-receipt.json',expected=state['receipt_sha256']))
                recheck_receipt_artifacts(root,row,receipt)
        return {'schema_version':1,'status':'verified' if receipt is not None else 'pending' if state else 'absent',
            'nonce':nonce,'installation_id':binding['installation_id'],'update_id':binding['update_id'],
            'database_fence':binding['database_pointer']['fence'],'plan_sha256':pin,
            'receipt_sha256':state['receipt_sha256'] if state else None,
            'actual_cpu_execution_verified':receipt is not None,'owned_backend_execution_origin_verified':receipt is not None,
            'execution_scope':receipt['execution_scope'] if receipt else _scope(bool(getattr(sys,'frozen',False))), 'worker_process_tree_exit_verified':False,
            'actual_application_inference_verified':False,'native_app_handshake_verified':False,'model_quality_approved':False,'release_ready':False}

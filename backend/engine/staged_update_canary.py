"""Required preactivation proof for an explicitly bound staged app/DB pair.

Source workers and their retained private snapshots do not qualify a native
application, signing, process-tree exit, model quality or release acceptance.
No ordinary launch admission or committed backend cache is relaxed here.
"""
import os
from pathlib import Path
import secrets
import sys
import math


class CanaryError(ValueError):pass


SPEC_FIELDS={'workspace_id','project_id','plan_sha256'}
REQUIREMENT='canary-requirement.json'
INTENT='canary-intent.json'
RECEIPT='canary-receipt.json'
FLAGS={'native_application_verified','frozen_backend_verified',
       'candidate_main_launch_verified',
       'owned_backend_execution_origin_verified','worker_process_tree_exit_verified',
       'model_quality_verified','release_ready'}
ENVIRONMENT_PROOF_FIELDS=('CUDA_VISIBLE_DEVICES','NVIDIA_VISIBLE_DEVICES','OMP_NUM_THREADS',
    'MKL_NUM_THREADS','HOME','TMPDIR','VISION_AI_STUDIO_USER_DATA_DIR')


def _require_exclusive(root):
    from backend.engine.migration_guard import exclusive_admitted
    if not exclusive_admitted(root):raise CanaryError('Canary mutation requires original exclusive installation admission')


def _capsule_members(directory,expected):
    u=_update();directory=u._unlinked(directory)
    if not directory.is_dir() or {path.name for path in directory.iterdir()}!=expected:
        raise CanaryError('Canary capsule has missing or unknown activity members')
    for name in expected:
        path=u._unlinked(directory/name)
        if name=='private' and not path.is_dir():raise CanaryError('Canary private capsule is not a directory')


def _update():
    from backend.engine import runtime_update
    return runtime_update


def validate_spec(value):
    u=_update()
    if (not isinstance(value,dict) or set(value)!=SPEC_FIELDS
            or not u._hex(value.get('workspace_id'),32) or not u._hex(value.get('project_id'),32)
            or not u._hex(value.get('plan_sha256'))):
        raise CanaryError('Preactivation canary requires exact workspace_id, project_id and independently pinned plan_sha256')
    return dict(value)


def _execution():
    try:
        from backend.engine import application_launch_execution
        return application_launch_execution
    except ImportError as exc:
        raise CanaryError('Preactivation canary requires_target: reviewed source CPU validator is not installed') from exc


def review_spec(root,value):
    """Exact registered source plan admission is also part of the reviewed plan hash."""
    spec=validate_spec(value)
    try:capability=_execution().admit_plan(root,spec['workspace_id'],spec['project_id'],spec['plan_sha256'])
    except (ValueError,OSError) as exc:raise CanaryError('Preactivation canary plan refused: '+str(exc)) from exc
    return _update()._sha(_update()._canonical(capability))


def _candidate_rows(manifest):
    u=_update();rows=[]
    if manifest['schema_version']!=1:raise CanaryError('requires_target: native candidate source worker is not qualified')
    for row in manifest['files']:
        if row['path'].startswith('backend/'):
            if not row['path'].endswith('.py') or 'tests' in row['path'].split('/') or row['executable']:
                raise CanaryError('requires_target: candidate backend contains unsupported executable/data members')
            if row['size']>32*1024**2:raise CanaryError('Candidate source member exceeds bound')
            rows.append({name:row[name] for name in ('path','size','sha256')})
    rows.sort(key=lambda row:row['path'])
    if not 1<=len(rows)<=1024 or sum(row['size'] for row in rows)>128*1024**2:
        raise CanaryError('requires_target: candidate lacks a complete bounded source backend')
    if 'backend/engine/flow_package_runtime.py' not in {row['path'] for row in rows}:
        raise CanaryError('requires_target: candidate runtime helper is missing')
    return rows,u._sha(u._canonical(rows))


def review_candidate(root,manifest,spec,capability_sha256):
    value={'schema_version':1,'protocol':1,'required':True,'policy':'same_reviewed_source_runtime_worker_v1',
        'status':'missing_pins','supported':False,'pins':spec,'capability_sha256':capability_sha256,
        'candidate_runtime_source_sha256':None,'reason':'Supply explicit trusted known-image workspace, project and plan SHA-256 pins',
        **{name:False for name in FLAGS}}
    if spec is None:return value
    try:
        if getattr(sys,'frozen',False):raise CanaryError('requires_target: frozen staged worker is not qualified')
        _,source_sha=_candidate_rows(manifest)
        capability=_execution().admit_plan(root,spec['workspace_id'],spec['project_id'],spec['plan_sha256'])
        if source_sha!=capability['plan']['runtime_source_sha256']:
            raise CanaryError('requires_target: candidate changes the independently reviewed source runtime')
        e=_execution();plan=capability['plan'];graph=e._json(e._read(Path(capability['project_path'])/e.PACKAGE/'pipeline.json',e.MAX_RESULT,expected=plan['graph_sha256']))
        _linear_graph(graph,plan)
        value.update(status='source_ready',supported=True,candidate_runtime_source_sha256=source_sha,reason=None)
    except (ValueError,OSError) as exc:
        value.update(status='requires_target',reason=str(exc)[:500])
    return value


def _write_sealed(path,value):
    u=_update();raw=u._canonical(value);path=u._unlinked(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o400)
    try:
        with os.fdopen(fd,'wb',closefd=False) as writer:
            writer.write(raw);writer.flush();os.fsync(fd)
    finally:os.close(fd)
    u.migration._sync_directories(path.parent,recursive=False)
    return u._sha(raw)


def _directory(root,record):
    u=_update()
    if not u._hex(record.get('migration_id'),32):raise CanaryError('Canary requires an exact prepared migration')
    return u._unlinked(Path(root)/u.UPDATES/record['update_id']/'canary'/record['migration_id'])


def _document(path,limit=1024**2):
    u=_update();raw=u._read(path,limit)
    return u._json(raw),u._sha(raw)


def expected_requirement(root,record,database,manifest):
    """The mutable journal status is excluded; every publication authority is bound."""
    u=_update();root,owner=u._root(root);spec=validate_spec(record.get('canary'))
    if (database.get('installation_id')!=owner['installation_id']
            or database.get('application_update_id')!=record['update_id']
            or database.get('migration_id')!=record.get('migration_id')
            or database.get('source_sha256')!=record['source_sha256']
            or database.get('previous_pointer')!=record['previous_database']):
        raise CanaryError('Staged canary database/update authority differs')
    return {'schema_version':1,'kind':'staged_update_canary_requirement',
        'root_identity':owner['root_identity'],'installation_id':record['installation_id'],
        'update_id':record['update_id'],'application_generation':record['application_generation'],
        'migration_id':database['migration_id'],'source_sha256':record['source_sha256'],
        'target_sha256':database['target_sha256'],'database_fence':database['fence'],
        'previous_database':record['previous_database'],'previous_application':record['previous_application'],
        'envelope_sha256':record['envelope_sha256'],'authority_sha256':record['authority_sha256'],
        'release_sha256':u._sha(u._canonical(record['release'])),'target':record['target'],
        'application_manifest_sha256':u._sha(u._canonical(manifest)),
        'canary':spec,'canary_capability_sha256':record['canary_capability_sha256'],
        'execution_policy':'staged_source_runtime_worker'}


def _source_and_target(root,record,database):
    u=_update();root,owner=u._root(root);m=u.migration;current=u.active_generation(root)
    previous=record['previous_database']
    target={'schema_version':1,'installation_id':record['installation_id'],
        'generation_id':database['migration_id'],'sealed_sha256':database['target_sha256'],'fence':database['fence']}
    if (current[1] if current else None) not in (previous,target):
        raise CanaryError('Canary original database pointer changed')
    if u._pointer(root) not in (record['previous_application'],
            {'schema_version':1,'installation_id':record['installation_id'],'update_id':record['update_id'],
             'application_generation':record['application_generation'],'database_pointer':target}):
        raise CanaryError('Canary original application pointer changed')
    source=m._forward_view(root,owner,root/'.global-generations'/previous['generation_id'],previous) if previous else m.preview(root)
    if source['source_sha256']!=record['source_sha256']:
        raise CanaryError('Canary original source changed')
    staged=u._unlinked(root/'.global-generations'/database['migration_id'])
    seal,_=_document(staged/'.global-generation.json')
    if (not isinstance(seal,dict) or seal.get('installation_id')!=record['installation_id'] or seal.get('generation_id')!=database['migration_id']
            or seal.get('sealed_sha256')!=database['target_sha256']
            or m.digest(m._snapshot(staged)['inventory'])!=database['target_sha256']):
        raise CanaryError('Canary staged database seal changed')


def validate_receipt(root,record,database,manifest):
    u=_update();directory=_directory(root,record)
    expected_members={REQUIREMENT,INTENT,RECEIPT,'canary-result.json','private'}
    _capsule_members(directory,expected_members)
    requirement,requirement_sha=_document(directory/REQUIREMENT)
    if (requirement!=expected_requirement(root,record,database,manifest)
            or record.get('canary_requirement_sha256')!=requirement_sha):
        raise CanaryError('Canary requirement binding changed or is missing')
    receipt,receipt_sha=_document(directory/RECEIPT)
    fields={'schema_version','kind','requirement_sha256','status','execution_scope','nonce','epoch',
            'intent_sha256','result_sha256','semantic_output_sha256','runtime_source_sha256',*FLAGS}
    if (not isinstance(receipt,dict) or set(receipt)!=fields or type(receipt.get('schema_version')) is not int
            or receipt['schema_version']!=1 or receipt.get('kind')!='staged_update_canary_receipt'
            or receipt.get('status')!='verified' or receipt.get('execution_scope')!='staged_source_runtime_worker'
            or receipt.get('requirement_sha256')!=requirement_sha
            or record.get('canary_receipt_sha256')!=receipt_sha
            or not u._hex(receipt.get('nonce'),32) or not u._hex(receipt.get('epoch'),32)
            or any(receipt.get(name) is not False for name in FLAGS)
            or any(not u._hex(receipt.get(name)) for name in
                   ('intent_sha256','result_sha256','semantic_output_sha256','runtime_source_sha256'))):
        raise CanaryError('Canary receipt is absent, partial, foreign or grants unsupported acceptance')
    intent,intent_sha=_document(directory/INTENT)
    if (not isinstance(intent,dict) or set(intent)!={'schema_version','kind','requirement_sha256','nonce','epoch','status'}
            or type(intent.get('schema_version')) is not int or intent['schema_version']!=1
            or intent.get('kind')!='staged_update_canary_intent' or intent.get('status')!='spawn_started'
            or intent.get('requirement_sha256')!=requirement_sha or intent_sha!=receipt['intent_sha256']
            or intent.get('nonce')!=receipt['nonce'] or intent.get('epoch')!=receipt['epoch']):
        raise CanaryError('Canary original execution intent changed')
    result,result_sha=_document(directory/'canary-result.json',8*1024**2)
    if (not isinstance(result,dict) or result_sha!=receipt['result_sha256']
            or result.get('requirement_sha256')!=requirement_sha
            or result.get('nonce')!=receipt['nonce'] or result.get('epoch')!=receipt['epoch']
            or result.get('semantic_output_sha256')!=receipt['semantic_output_sha256']
            or result.get('runtime_source_sha256')!=receipt['runtime_source_sha256']):
        raise CanaryError('Canary retained result differs from sealed receipt')
    validate_source_result(root,record,manifest,receipt,result)
    _source_and_target(root,record,database)
    _capsule_members(directory,expected_members)
    return receipt


def execute_source_candidate(root,record,database,manifest,requirement_sha256):
    """Fixed private source adapter; frozen/native candidates require target work."""
    _require_exclusive(root)
    if os.name!='posix' or getattr(sys,'frozen',False):
        raise CanaryError('requires_target: frozen/native staged worker is not qualified')
    u=_update();e=_execution();spec=validate_spec(record['canary']);attempt=_directory(root,record)
    capability=e.admit_plan(root,spec['workspace_id'],spec['project_id'],spec['plan_sha256'])
    if u._sha(u._canonical(capability))!=record['canary_capability_sha256']:
        raise CanaryError('Reviewed canary capability changed')
    rows,runtime_sha=_candidate_rows(manifest);plan=capability['plan']
    if runtime_sha!=plan['runtime_source_sha256']:
        raise CanaryError('requires_target: candidate changes the reviewed source runtime')
    private=u._unlinked(attempt/'private');private.mkdir(mode=0o700,exist_ok=False)
    delivery=private/'project'/'delivery'/'launch-known-image'
    copied=e.admit_plan(root,spec['workspace_id'],spec['project_id'],spec['plan_sha256'],copy_to=delivery)
    if u._canonical(copied)!=u._canonical(capability):raise CanaryError('Canary copied capability changed')
    project=Path(capability['project_path'])
    u._write_raw(delivery/'plan.json',e._read(project/e.PLAN,expected=spec['plan_sha256']))
    u._write_raw(private/'project'/'project.json',e._read(project/'project.json',1024**2,expected=plan['project_manifest_sha256']))
    if plan['release_policy'] is not None:
        u._write_raw(delivery/'release-policy.json',e._read(project/'delivery/launch-known-image/release-policy.json',e.MAX_RESULT,
            expected=plan['release_policy']['sha256']))
    application=u._unlinked(Path(root)/u.GENERATIONS/record['application_generation']/'application')
    runtime=private/'candidate-runtime'
    for row in rows:
        e._read(application/row['path'],row['size'],expected=row['sha256'],destination=runtime/row['path'])
        (runtime/row['path']).chmod(0o400)
    _write_sealed(private/'candidate-runtime-inventory.json',rows)
    _validate_private_inputs(root,record,manifest,capability)
    _source_and_target(root,record,database)
    nonce=secrets.token_hex(16);epoch=secrets.token_hex(16)
    request={'schema_version':1,'kind':'staged_source_runtime_request','requirement_sha256':requirement_sha256,
        'nonce':nonce,'epoch':epoch,'runtime_root':str(runtime),'package':str(delivery/'package'),'image':str(delivery/'input.png')}
    request_path=private/'worker-request.json';request_sha=_write_sealed(request_path,request);output=private/'worker-result.json'
    home=private/'home';cache=private/'cache';scratch=private/'tmp'
    for path in (home,cache,scratch):path.mkdir(mode=0o700)
    u.migration._sync_directories(private,recursive=True)
    intent={'schema_version':1,'kind':'staged_update_canary_intent','requirement_sha256':requirement_sha256,
        'nonce':nonce,'epoch':epoch,'status':'spawn_started'}
    intent_sha=_write_sealed(attempt/INTENT,intent)
    _checkpoint('before_canary_spawn')
    # The child receives private data paths only. Its code comes exclusively
    # from the signed candidate snapshot; exported package Python is not imported.
    bootstrap=('import sys,json,hashlib,os;from pathlib import Path;'
        'sys.dont_write_bytecode=True;sys.modules["pyarrow"]=None;'
        'raw=Path(sys.argv[1]).read_bytes();assert len(raw)<=65536 and hashlib.sha256(raw).hexdigest()==sys.argv[3];'
        'r=json.loads(raw);assert set(r)=={"schema_version","kind","requirement_sha256","nonce","epoch","runtime_root","package","image"};'
        'sys.path.insert(0,r["runtime_root"]);from backend.engine.flow_package_runtime import run_flow_package;'
        'helper=Path(run_flow_package.__code__.co_filename).resolve();'
        'assert helper==Path(r["runtime_root"])/"backend/engine/flow_package_runtime.py";'
        'import torch;torch.set_num_threads(1);import psutil;'
        'v=run_flow_package(Path(r["package"]),Path(r["image"]),"owned-cpu-known-image",device="cpu",cpu_threads=1,_owned_worker=True);'
        'proof={"request_sha256":sys.argv[3],"nonce":r["nonce"],"epoch":r["epoch"],"requirement_sha256":r["requirement_sha256"],'
        '"runtime_helper_path":str(helper),"process":{"pid":os.getpid(),"created_at":psutil.Process().create_time()},"flow_result":v,'
        '"environment":{k:os.environ.get(k) for k in '+repr(ENVIRONMENT_PROOF_FIELDS)+'}};'
        'raw=json.dumps(proof,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode();assert len(raw)<=8*1024**2;'
        'fd=os.open(sys.argv[2],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);'
        'f=os.fdopen(fd,"wb");f.write(raw);f.flush();os.fsync(f.fileno());f.close()')
    from backend.engine.runtime_deadline import execute_owned_process
    outcome=execute_owned_process([sys.executable,'-I','-B','-X','pycache_prefix='+str(private/'bytecode'),'-c',bootstrap,
        str(request_path),str(output),request_sha],deadline_ms=plan['deadline_ms'],env=_worker_environment(home,cache,scratch),cwd=private)
    if outcome['status']!='completed' or outcome['returncode']!=0:
        raise CanaryError('Canary CPU failed or timed out; retain recovery ownership, process-tree exit is unverified')
    proof=e._json(e._read(output,e.MAX_RESULT))
    if (not isinstance(proof,dict) or set(proof)!={'request_sha256','nonce','epoch','requirement_sha256','runtime_helper_path','process','flow_result','environment'}
            or any(proof.get(name)!=request[name] for name in ('nonce','epoch','requirement_sha256'))
            or proof.get('request_sha256')!=request_sha or proof.get('runtime_helper_path')!=str(runtime/'backend/engine/flow_package_runtime.py')
            or not isinstance(proof.get('process'),dict) or set(proof['process'])!={'pid','created_at'}
            or type(proof['process']['pid']) is not int or proof['process']['pid']!=outcome['pid']
            or type(proof['process']['created_at']) not in (int,float) or not math.isfinite(proof['process']['created_at'])
            or proof['process']['created_at']<=0 or not isinstance(proof.get('flow_result'),dict)
            or proof.get('environment')!={name:_worker_environment(home,cache,scratch)[name] for name in ENVIRONMENT_PROOF_FIELDS}):
        raise CanaryError('Canary private worker request/process proof differs')
    flow=proof['flow_result'];flow['runtime_execution']={'device':'cpu','cpu_threads':1,'deadline_ms':plan['deadline_ms'],
        'isolated_process':True,'pid':outcome['pid'],'elapsed_ms':outcome['elapsed_ms']}
    semantic=e.validate_result(flow,{**capability,'project_path':str(private/'project')})
    semantic_sha=u._sha(u._canonical(semantic))
    if semantic_sha!=plan['semantic_output_sha256']:raise CanaryError('Actual canary known-image semantic output differs from independent pin')
    _checkpoint('after_canary_math')
    _validate_private_inputs(root,record,manifest,capability)
    after=e.admit_plan(root,spec['workspace_id'],spec['project_id'],spec['plan_sha256'])
    if u._canonical(after)!=u._canonical(capability):raise CanaryError('Canary original source capability changed during math')
    _source_and_target(root,record,database)
    result={'schema_version':1,'requirement_sha256':requirement_sha256,'nonce':nonce,'epoch':epoch,
        'semantic_output':semantic,'semantic_output_sha256':semantic_sha,'runtime_source_sha256':runtime_sha,
        'capability':capability,'worker':{name:proof[name] for name in ('process','request_sha256','runtime_helper_path','environment')},'flow_result':flow}
    if len(u._canonical(result))>e.MAX_RESULT:raise CanaryError('Canary result exceeds bound')
    result_sha=_write_sealed(attempt/'canary-result.json',result);_checkpoint('after_canary_result')
    receipt={'schema_version':1,'kind':'staged_update_canary_receipt','status':'verified','execution_scope':'staged_source_runtime_worker',
        'requirement_sha256':requirement_sha256,'nonce':nonce,'epoch':epoch,'intent_sha256':intent_sha,'result_sha256':result_sha,
        'semantic_output_sha256':semantic_sha,'runtime_source_sha256':runtime_sha,**{name:False for name in FLAGS}}
    _write_sealed(attempt/RECEIPT,receipt);_checkpoint('after_canary_receipt')


def _checkpoint(point):
    """Retained fault boundaries; observers never execute or repair a canary."""


def _worker_environment(home,cache,scratch):
    return {'PATH':os.defpath,'LANG':'C.UTF-8','PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1',
        'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'none',
        'HF_DATASETS_OFFLINE':'1','HOME':str(home),'USERPROFILE':str(home),'XDG_CACHE_HOME':str(cache),
        'XDG_CONFIG_HOME':str(home/'config'),'TORCH_HOME':str(cache/'torch'),'HF_HOME':str(cache/'huggingface'),
        'HUGGINGFACE_HUB_CACHE':str(cache/'huggingface/hub'),'TRANSFORMERS_CACHE':str(cache/'transformers'),
        'MPLCONFIGDIR':str(cache/'matplotlib'),'TMPDIR':str(scratch),'TMP':str(scratch),'TEMP':str(scratch),
        'YOLO_CONFIG_DIR':str(home/'yolo'),'OPENBLAS_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1',
        'VECLIB_MAXIMUM_THREADS':'1','BLIS_NUM_THREADS':'1','VISION_AI_STUDIO_USER_DATA_DIR':str(home/'userdata')}


def _validate_private_inputs(root,record,manifest,capability):
    u=_update();e=_execution();private=_directory(root,record)/'private';plan=capability['plan'];delivery=private/'project'/'delivery'/'launch-known-image'
    captured=e._json(e._read(delivery/'plan.json',expected=record['canary']['plan_sha256']))
    if u._canonical(captured)!=u._canonical(plan):raise CanaryError('Canary captured plan differs')
    e._read(private/'project'/'project.json',1024**2,expected=plan['project_manifest_sha256'])
    rows,runtime_sha=_candidate_rows(manifest);runtime=private/'candidate-runtime'
    if runtime_sha!=plan['runtime_source_sha256'] or e._json(e._read(private/'candidate-runtime-inventory.json',e.MAX_RESULT))!=rows:
        raise CanaryError('Canary private candidate source inventory differs')
    if e._members(runtime)!={row['path'] for row in rows}:raise CanaryError('Canary private candidate source membership differs')
    for row in rows:e._read(runtime/row['path'],row['size'],expected=row['sha256'])
    package=delivery/'package';package_manifest=e._json(e._read(package/'manifest.json',e.MAX_RESULT,expected=plan['package_manifest_sha256']))
    pins=package_manifest.get('files') if isinstance(package_manifest,dict) else None
    if not isinstance(pins,list) or e._members(package)!={'manifest.json',*(row['path'] for row in pins)}:
        raise CanaryError('Canary private package membership differs')
    for row in pins:e._read(package/row['path'],row['size'],expected=row['sha256'])
    e._read(delivery/'input.png',e.MAX_FILE,expected=plan['input_sha256'])
    graph=e._json(e._read(package/'pipeline.json',e.MAX_RESULT,expected=plan['graph_sha256']))
    _linear_graph(graph,plan)
    if plan['release_policy'] is not None:
        e._read(delivery/'release-policy.json',e.MAX_RESULT,expected=plan['release_policy']['sha256'])
        from backend.engine.flow_package_runtime import verify_flow_package
        from backend.engine.inspection_service import _verify_release_policy
        _,verified=verify_flow_package(package);_verify_release_policy(package,verified,delivery/'release-policy.json',device='cpu')


def _linear_graph(graph,plan):
    nodes=graph.get('nodes') if isinstance(graph,dict) else None;edges=graph.get('edges') if isinstance(graph,dict) else None
    if (not isinstance(nodes,list) or len(nodes)!=4 or not isinstance(edges,list) or len(edges)!=3
            or any(not isinstance(node,dict) or not isinstance(node.get('data'),dict)
                   or not isinstance(node.get('id'),str) for node in nodes)
            or any(not isinstance(edge,dict) for edge in edges)
            or len({node['id'] for node in nodes})!=4
            or [node['data'].get('node_type') for node in nodes]!=['input','inspection','decision','output']
            or nodes[1]['data'].get('task')!='ocr' or len(plan['checkpoints'])!=1
            or nodes[1]['data'].get('model_job_id')!=plan['checkpoints'][0]['job_id']
            or [(edge.get('source'),edge.get('target')) for edge in edges]!=[(nodes[i]['id'],nodes[i+1]['id']) for i in range(3)]):
        raise CanaryError('Canary requires the fixed linear CPU OCR graph and checkpoint')


def validate_source_result(root,record,manifest,receipt,result):
    u=_update();e=_execution();fields={'schema_version','requirement_sha256','nonce','epoch','semantic_output','semantic_output_sha256',
        'runtime_source_sha256','capability','worker','flow_result'}
    if (set(result)!=fields or type(result.get('schema_version')) is not int or result['schema_version']!=1
            or not isinstance(result.get('capability'),dict) or not isinstance(result.get('worker'),dict)
            or not isinstance(result.get('flow_result'),dict)):
        raise CanaryError('Canary source result shape differs')
    capability=e.admit_plan(root,**record['canary'])
    if (u._sha(u._canonical(capability))!=record['canary_capability_sha256']
            or u._canonical(capability)!=u._canonical(result['capability'])):
        raise CanaryError('Canary retained source capability differs')
    _validate_private_inputs(root,record,manifest,capability);private=_directory(root,record)/'private';worker=result['worker']
    request,request_sha=_document(private/'worker-request.json')
    if (set(worker)!={'process','request_sha256','runtime_helper_path','environment'} or not isinstance(worker.get('process'),dict)
            or set(worker['process'])!={'pid','created_at'} or type(worker['process']['pid']) is not int or worker['process']['pid']<=0
            or type(worker['process']['created_at']) not in (int,float) or not math.isfinite(worker['process']['created_at'])
            or worker['process']['created_at']<=0 or worker.get('request_sha256')!=request_sha
            or worker.get('runtime_helper_path')!=str(private/'candidate-runtime/backend/engine/flow_package_runtime.py')
            or worker.get('environment')!={name:_worker_environment(private/'home',private/'cache',private/'tmp')[name] for name in ENVIRONMENT_PROOF_FIELDS}
            or request!={'schema_version':1,'kind':'staged_source_runtime_request','requirement_sha256':receipt['requirement_sha256'],
                'nonce':receipt['nonce'],'epoch':receipt['epoch'],'runtime_root':str(private/'candidate-runtime'),
                'package':str(private/'project/delivery/launch-known-image/package'),'image':str(private/'project/delivery/launch-known-image/input.png')}):
        raise CanaryError('Canary retained private worker request differs')
    flow=result['flow_result'];e._runtime_execution(flow,capability['plan'],worker['process']['pid'])
    semantic=e.validate_result(flow,{**capability,'project_path':str(private/'project')})
    sha=u._sha(u._canonical(semantic))
    if (semantic!=result['semantic_output'] or sha!=receipt['semantic_output_sha256']
            or sha!=capability['plan']['semantic_output_sha256'] or result['runtime_source_sha256']!=capability['plan']['runtime_source_sha256']):
        raise CanaryError('Canary retained actual math differs from independent pins')


def ensure_verified(root,record,database,manifest):
    _require_exclusive(root)
    u=_update();directory=_directory(root,record)
    directory.mkdir(mode=0o700,parents=True,exist_ok=True);u._unlinked(directory)
    expected=expected_requirement(root,record,database,manifest)
    if (directory/REQUIREMENT).exists():
        requirement,sha=_document(directory/REQUIREMENT)
        if requirement!=expected:raise CanaryError('Retained canary requirement changed; forward recovery required')
    else:sha=_write_sealed(directory/REQUIREMENT,expected)
    if record.get('canary_requirement_sha256') not in (None,sha):
        raise CanaryError('Retained canary requirement bytes changed')
    if record.get('canary_requirement_sha256') is None:
        record['canary_requirement_sha256']=sha;u._write(Path(root)/u.UPDATES/record['update_id']/'journal.json',record)
    _source_and_target(root,record,database)
    if not (directory/RECEIPT).exists():
        if (directory/INTENT).exists():
            raise CanaryError('Canary execution already attempted without sealed proof; retain recovery ownership')
        execute_source_candidate(root,record,database,manifest,sha)
    _,receipt_sha=_document(directory/RECEIPT)
    if record.get('canary_receipt_sha256') not in (None,receipt_sha):
        raise CanaryError('Retained canary receipt bytes changed')
    if record.get('canary_receipt_sha256') is None:
        record['canary_receipt_sha256']=receipt_sha;u._write(Path(root)/u.UPDATES/record['update_id']/'journal.json',record)
    validate_receipt(root,record,database,manifest)


def require_publication(root,database):
    """All app-bound DB publication paths, including direct recovery, pass here."""
    u=_update();pending=Path(root)/u.PENDING;identifier=database.get('application_update_id')
    if identifier is None:
        if pending.exists() or (Path(root)/u.ACTIVE).exists():
            raise CanaryError('Application-bound migration lacks required preactivation canary authority')
        return
    if not u._hex(identifier,32) or not pending.exists():raise CanaryError('Canary update recovery ownership is missing')
    value,_=_document(pending)
    if value!={'schema_version':1,'installation_id':database['installation_id'],'update_id':identifier}:
        raise CanaryError('Canary update recovery ownership differs')
    record,_,manifest=u._validated_intent(root,identifier)
    if record.get('schema_version')!=2 or record.get('migration_id')!=database['migration_id']:
        raise CanaryError('Application-bound migration lacks required preactivation canary')
    validate_receipt(root,record,database,manifest)


def assert_no_spawn_abort(root,record):
    """No child attempt is inferred from PID absence, exit or a process-group scan."""
    u=_update();directory=u._unlinked(Path(root)/u.UPDATES/record['update_id'])
    allowed={'bundle','envelope.json','journal.json','canary'}
    if any(path.name not in allowed for path in directory.iterdir()):
        raise CanaryError('Canary execution may have started; retain recovery ownership and private artifacts')
    if record.get('canary_receipt_sha256') is not None:
        raise CanaryError('Canary receipt already exists; retain recovery ownership')
    if record['migration_id'] is not None:
        attempt=_directory(root,record)
        if attempt.exists():
            members={path.name for path in attempt.iterdir()}
            if not members<={REQUIREMENT,'private'}:
                raise CanaryError('Canary execution may have started; retain recovery ownership and private artifacts')
            _capsule_members(attempt,members)
        _,database=u.migration._journal(root,record['migration_id'])
        _source_and_target(root,record,database)

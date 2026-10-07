"""Persisted delivery inventories and truthful device/installation evidence."""
from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import sqlite3
import sys
import sysconfig
import time
import uuid
from pathlib import Path

from backend.engine.runtime_process_control import atomic_private_json, runtime_state_lock
from backend.engine.flow_package_runtime import verify_flow_package, _sha256


def _root(project):
    root=Path(project['project_dir']).expanduser()
    if root.is_symlink() or not root.is_dir():raise ValueError('Open an existing project')
    return root.resolve()


def _scope(project):
    source=project.get('source_dataset_dir')
    return {'project_id':project['id'],'source_dataset_path':str(Path(source).resolve()) if source else None,'task':project.get('task')}


def _preference_key(project):
    return hashlib.sha256(str(project.get('_delivery_account_id','local-desktop')).encode()).hexdigest()


def _storage(project):
    root=_root(project)/'delivery'
    if root.is_symlink():raise ValueError('Delivery storage cannot be linked')
    root.mkdir(exist_ok=True)
    for name in ('library.json','operator_reviews.sqlite3','hardware.json','diagnostics.json'):
        if (root/name).is_symlink():raise ValueError('Delivery records cannot be linked')
    return root


def _journal(project):
    path=_storage(project)/'library.json'
    if not path.exists():return {'schema_version':1,'packages':{},'selection':None}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema_version')!=1:raise ValueError('Unsupported delivery inventory version')
    return value


def _owned_package(project,path):
    root=_root(project)/'exports'/'flows';candidate=Path(path).expanduser()
    if any(p.is_symlink() for p in (candidate,*candidate.parents)):raise ValueError('Package storage cannot be linked')
    if not candidate.is_dir() or not candidate.resolve().is_relative_to(root):raise ValueError('Package must belong to this project')
    return candidate.resolve()


def record_package(project,path,*,version_id=None,recipe_task=None,parity=None):
    package=_owned_package(project,path);verify_flow_package(package)
    identifier=hashlib.sha256(package.relative_to(_root(project)).as_posix().encode()).hexdigest()[:32]
    root=_storage(project)
    with runtime_state_lock(root):
        journal=_journal(project)
        journal['packages'][identifier]={**_scope(project),'package_id':identifier,'relative_path':package.relative_to(_root(project)).as_posix(),
            'manifest_sha256':_sha256(package/'manifest.json'),'version_id':version_id,'recipe_task':recipe_task or project.get('task'),
            'parity':parity or {'status':'not_run'},'recorded_at':time.time()}
        atomic_private_json(root/'library.json',journal)
    return identifier


def _legacy_binding(project,package,pipeline):
    from backend.engine.flowchart_engine import FlowchartPipeline
    source=_scope(project)['source_dataset_path'];directory=_root(project)/'flowcharts'/'versions'
    if directory.is_symlink() or not directory.is_dir():return None
    for path in sorted(directory.glob('*.json')):
        if path.is_symlink():continue
        try:
            row=json.loads(path.read_text(encoding='utf-8'))
            if row.get('source_dataset_path')==source and FlowchartPipeline.model_validate(row['pipeline'])==pipeline:
                return {'version_id':row.get('version_id'),'recipe_task':row.get('recipe_task'),**_scope(project)}
        except (ValueError,KeyError,OSError):continue
    converted=package/'openvino_models.json'
    if converted.is_file() and not converted.is_symlink():
        receipt=json.loads(converted.read_text(encoding='utf-8')).get('input_receipt',{})
        if receipt.get('source_dataset_path')==source:return {**_scope(project),'version_id':None,'recipe_task':project.get('task')}
    return None


def package_library(project):
    root=_root(project);directory=root/'exports'/'flows';journal=_journal(project);rows=[];scope=_scope(project)
    if directory.is_symlink():raise ValueError('Package inventory cannot follow linked storage')
    if directory.is_dir():
        for package in sorted(directory.iterdir(),key=lambda p:p.name):
            if package.is_symlink() or not package.is_dir() or package.name.startswith('.'):continue
            identifier=hashlib.sha256(package.relative_to(root).as_posix().encode()).hexdigest()[:32]
            saved=journal['packages'].get(identifier,{});row={'package_id':identifier,'name':package.name,'package_path':str(package),
                'integrity':'failed','scope_matches':False,'parity':saved.get('parity',{'status':'not_run'}),'optimization_jobs':[]}
            try:
                pipeline,_=verify_flow_package(package);manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'));digest=_sha256(package/'manifest.json')
                binding=saved or _legacy_binding(project,package,pipeline) or {}
                matches=all(binding.get(key)==value for key,value in scope.items())
                if saved and saved['manifest_sha256']!=digest:raise ValueError('Package manifest differs from the saved inventory receipt')
                row.update(integrity='verified',scope_matches=matches,manifest_sha256=digest,pipeline_id=pipeline.id,
                    version_id=binding.get('version_id'),recipe_task=binding.get('recipe_task'),created_at=manifest.get('created_at'),
                    models=manifest['models'],runtime=manifest.get('runtime',{}),approval_present=bool(manifest.get('release')),
                    total_files=len(manifest['files'])+1)
                if not matches:row['error']='Package source or project binding needs confirmation from its saved flow'
            except (ValueError,OSError,KeyError,TypeError) as exc:row['error']=str(exc)
            rows.append(row)
    jobs=root/'exports'/'optimization_jobs'
    if jobs.is_dir() and not jobs.is_symlink():
        for file in jobs.glob('*.json'):
            if file.is_symlink():continue
            try:
                job=json.loads(file.read_text(encoding='utf-8'));selected=job.get('options',{}).get('package_dir')
                for row in rows:
                    if selected==row['package_path']:row['optimization_jobs'].append({'job_id':job['job_id'],'status':job['status']})
            except (OSError,ValueError,KeyError):continue
    selected=journal.get('selections',{}).get(_preference_key(project))
    if selected is None and not project.get('_delivery_account_id'):selected=journal.get('selection')
    selected_id=selected.get('package_id') if selected and selected.get('scope')==scope else None
    return {'packages':rows,'selected_package_id':selected_id,'scope':scope}


def select_package(project,identifier):
    row=next((r for r in package_library(project)['packages'] if r['package_id']==identifier),None)
    if row is None:raise ValueError('Package not found in this project')
    if row['integrity']!='verified':raise ValueError(row.get('error','Package checksum failed'))
    if not row['scope_matches']:raise ValueError('Package scope does not match this active project source')
    root=_storage(project)
    with runtime_state_lock(root):
        journal=_journal(project);journal.setdefault('selections',{})[_preference_key(project)]={'package_id':identifier,'scope':_scope(project),'manifest_sha256':row['manifest_sha256']}
        atomic_private_json(root/'library.json',journal)
    return row


_INPUT_SCRIPT="""import base64,hashlib,io,json,sys
from PIL import Image
import numpy as np
import torch
torch.set_num_threads(1)
data=base64.b64decode(sys.argv[1],validate=True)
image=Image.open(io.BytesIO(data));image.load();image=image.convert('RGB')
width,height=image.size
image.thumbnail((1024,1024))
tensor=torch.from_numpy(np.array(image,copy=True)).to(dtype=torch.float32)
print(json.dumps({'remote_sha256':hashlib.sha256(data).hexdigest(),'width':width,'height':height,'tensor_shape':list(tensor.shape),'mean':float(tensor.mean()),'finite':bool(torch.isfinite(tensor).all()),'execution':'decode_and_tensor_cpu'}))
"""


def server_preflight(project,profile,image_path,*,transport=None):
    from backend.remote.ssh_transport import SSHTransport
    from backend.engine.industrial_adapters import read_image_safely_rgb
    source=Path(project.get('source_dataset_dir') or '').resolve();image=Path(image_path).expanduser()
    if image.is_symlink() or not image.is_file() or not image.resolve().is_relative_to(source):raise ValueError('Select an image inside the active source')
    if image.stat().st_size>64*1024*1024:raise ValueError('Preflight source image exceeds 64 MiB')
    preview=read_image_safely_rgb(image,max_dim=64)
    if preview.size==0:raise ValueError('Source image cannot be decoded')
    from PIL import Image
    with Image.open(image) as dimensions:
        if dimensions.width*dimensions.height>150_000_000:raise ValueError('Preflight image exceeds 150 million pixels')
    transport=transport or SSHTransport();probe=transport.probe(profile)
    result={'ready':False,'profile_id':profile.id,'probe':probe,'training_started':False,'observed_at':time.time()}
    if not probe.get('runtime_ready',probe.get('ready')):return result
    data=image.read_bytes();scratch=None;script=_INPUT_SCRIPT;argument=base64.b64encode(data).decode()
    try:
        if len(data)>48*1024:
            run_id='preflight_'+uuid.uuid4().hex;scratch=profile.remote_root+'/runs/'+run_id;relative='runs/'+run_id+'/input'+image.suffix.lower()
            uploaded=transport.upload(profile,image,relative)
            if uploaded.returncode:raise ValueError('Source image preflight transfer failed')
            script=script.replace('base64.b64decode(sys.argv[1],validate=True)',"__import__('pathlib').Path(sys.argv[1]).read_bytes()")
            argument=profile.remote_root+'/'+relative
        args=transport.runtime_argv(profile,['-B','-c',script,argument],gpu=False,
            **({'user':transport._remote_user(profile)} if profile.runtime_kind=='docker' else {}))
        completed=transport.exec(profile,args,timeout=30)
    finally:
        if scratch:
            # Only the UUID scratch directory created by this request is removed.
            removed=transport.exec(profile,['rm','-rf','--',scratch],timeout=10)
            if removed.returncode:raise ValueError('Owned preflight scratch cleanup failed')
    if completed.returncode:raise ValueError('Remote source decoder failed: '+completed.stderr[-2000:])
    decoded=json.loads(completed.stdout.strip().splitlines()[-1]);decoded['local_sha256']=hashlib.sha256(data).hexdigest()
    result.update(input=decoded,ready=decoded.get('finite') is True and decoded['local_sha256']==decoded.get('remote_sha256'))
    root=_storage(project);atomic_private_json(root/('preflight_'+hashlib.sha256(profile.id.encode()).hexdigest()[:24]+'.json'),{**result,'scope':_scope(project)})
    return result


def redact_diagnostics(value):
    if isinstance(value,dict):return {str(k):('[REDACTED]' if re.search(r'token|password|secret|authorization|credential|api[_-]?key|access[_-]?key|private[_-]?key|cookie|ssh_target|(?:^|_)path$|(?:^|_)dir$',str(k),re.I) else redact_diagnostics(v)) for k,v in value.items()}
    if isinstance(value,list):return [redact_diagnostics(v) for v in value]
    if isinstance(value,str):
        # Endpoint paths/hosts may themselves contain private identities or keys.
        # Scrub complete URLs even when malformed or embedded in an error text.
        value=re.sub(r'(?i)\b(?:https?|wss?|ssh)://[^\s<>"\']+', '[URL]',value)
        value=re.sub(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*', '[REDACTED]',value)
        value=re.sub(r'\b(?:gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AKIA[0-9A-Z]{16})\b', '[REDACTED]',value)
        value=re.sub(r'(?i)(bearer\s+|(?:token|password|secret)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',value)
        value=re.sub(r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b', '[PRIVATE_ADDRESS]',value)
        value=re.sub(r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', '[CONTACT]',value)
        return re.sub(r'(?:[A-Za-z]:[\\/]|/)[^\s<>"\']+', '[PATH]',value)
    return value


def installation_readiness(project):
    from backend.engine.runtime_pack_store import inventory as runtime_pack_inventory
    root=_root(project);manifest=root/'project.json';schema=1
    if manifest.is_symlink():raise ValueError('Project manifest cannot be linked')
    if manifest.exists():schema=json.loads(manifest.read_text(encoding='utf-8')).get('schema_version',1)
    compiler=shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')
    headers=Path(sysconfig.get_path('include'))/'Python.h'
    dependencies={name:importlib.util.find_spec(name) is not None for name in ('torch','numpy','PIL','cv2','pydantic','httpx')}
    app_version=os.environ.get('VISION_AI_APP_VERSION') or None;version_source='desktop_process' if app_version else 'unavailable'
    package=Path(__file__).resolve().parents[2]/'package.json'
    if not app_version and package.is_file():
        app_version=json.loads(package.read_text(encoding='utf-8')).get('version');version_source='package_manifest'
    receipts=root/'.migrations'
    migrated=False
    if receipts.is_dir() and not receipts.is_symlink():
        for record in receipts.glob('*/receipt.json'):
            if record.is_symlink() or record.parent.is_symlink():continue
            try:migrated=migrated or json.loads(record.read_text(encoding='utf-8')).get('status')=='applied'
            except (OSError,ValueError):continue
    return {'app_version':app_version,'version_source':version_source,'host':{'os':platform.system(),'architecture':platform.machine(),'python':platform.python_version()},
        'project':{'schema_version':schema,'supported_schema_versions':[1],'compatible':type(schema)is int and schema==1,'migration_performed':migrated},
        'runtime_dependencies':dependencies,'runtime_packs':runtime_pack_inventory(project),'python_supported':(3,10)<=sys.version_info[:2]<(3,14),
        'sdk':{'Python':{'ready':all(dependencies.values()),'requires_python':True},
               'C++':{'ready':bool(compiler) and headers.is_file() and all(dependencies.values()),'compiler_available':bool(compiler),'python_headers_available':headers.is_file(),'requires_embedded_python':True},
               'C#':{'ready':bool(shutil.which('dotnet')) and bool(compiler) and headers.is_file() and all(dependencies.values()),'dotnet_available':bool(shutil.which('dotnet')),'requires_embedded_python':True,'bridge':'C ABI / PInvoke'}},
        'install':{'mode':'desktop_and_project_runtime','native_autostart_supported':platform.system() in ('Darwin','Linux','Windows'),'native_registration_verified':False,'signing_verified':False,'signing_status':'desktop_main_process_required'},
        'update':{'automatic_update_available':False,'actions':['Export a project archive before changing app versions','Check project schema and runtime dependencies before reopening'],
                  'compatibility_check_performed':True}}


def operator_records(project,*,identifier=None):
    database=_root(project)/'runtime_service'/'state'/'inspection_service.sqlite3'
    if database.is_symlink():raise ValueError('Operator records cannot follow links')
    if not database.is_file():return []
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as conn:
        conn.row_factory=sqlite3.Row
        query='SELECT job_id,image_path,image_id,image_sha256,source,state,verdict,model_verdict,error,created_at FROM jobs'
        query+= ' WHERE job_id=?' if identifier is not None else ' ORDER BY created_at DESC,rowid DESC LIMIT 100'
        rows=[dict(row) for row in conn.execute(query,(identifier,) if identifier is not None else ())]
    reviews=_storage(project)/'operator_reviews.sqlite3'
    if reviews.exists():
        with sqlite3.connect(reviews) as conn:
            conn.row_factory=sqlite3.Row
            for row in rows:
                review=conn.execute('SELECT verdict,reviewer,reason,created_at FROM reviews WHERE job_id=? ORDER BY id DESC LIMIT 1',(row['job_id'],)).fetchone()
                row['operator_review']=dict(review) if review else None
    return rows


def review_operator_result(project,identifier,verdict,reviewer,reason):
    if verdict not in ('OK','NG','REVIEW') or not reviewer.strip() or len(reason.strip())<3:raise ValueError('Select a verdict and enter reviewer/reason')
    row=next(iter(operator_records(project,identifier=identifier)),None)
    if row is None:raise ValueError('Inspection job not found in this project')
    if row['state'] in ('queued','running'):raise ValueError('Wait for the inspection result before review')
    with sqlite3.connect(_storage(project)/'operator_reviews.sqlite3') as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY,job_id TEXT,verdict TEXT,reviewer TEXT,reason TEXT,created_at REAL)')
        conn.execute('INSERT INTO reviews(job_id,verdict,reviewer,reason,created_at) VALUES(?,?,?,?,?)',(identifier,verdict,reviewer.strip(),reason.strip(),time.time()))
    return {'saved':True,'job_id':identifier,'model_verdict':row['model_verdict'],'operator_verdict':verdict}


def hardware_matrix(project,*,capabilities=None):
    if capabilities is None:
        from backend.api.routes_export import runtime_capabilities
        capabilities=runtime_capabilities()
    verified=[];root=_storage(project);path=root/'hardware.json'
    if path.exists():verified=json.loads(path.read_text(encoding='utf-8')).get('executions',[])
    devices=[{'device':d,'kind':'cuda' if d.startswith('cuda') else d,'configured':True} for d in capabilities['torch_devices']]
    devices += [{'device':'openvino:'+d,'kind':d.lower(),'configured':True} for d in capabilities['openvino'].get('devices',[])]
    for device,kind in [('MIG','mig'),('openvino:NPU','npu'),('linux-arm64','edge'),('cuda:0','cuda')]:
        if not any(r['device']==device for r in devices):devices.append({'device':device,'kind':kind,'configured':False})
    valid={row.get('manifest_sha256') for row in package_library(project)['packages'] if row['integrity']=='verified' and row['scope_matches']}
    applied=None;runtime={}
    service_root=_root(project)/'runtime_service'
    if (service_root/'service.json').is_file():
        from backend.engine.managed_service import ManagedService
        state=ManagedService(project['project_dir']).state();applied=state.get('active');runtime=state.get('runtime',{})
    for row in devices:
        evidence=[v for v in verified if v.get('scope')==_scope(project) and v.get('device')==row['device'] and v.get('manifest_sha256') in valid]
        current=(applied or {}).get('release',{});approved=bool(evidence and runtime.get('status')=='ready' and current.get('device')==row['device'] and runtime.get('manifest_sha256')==current.get('manifest_sha256') and any(v['manifest_sha256']==current.get('manifest_sha256') for v in evidence))
        row.update(live_verified=bool(evidence),approved=approved,evidence=evidence,acceptance='approved_active_execution' if approved else 'target_execution_required')
    return {'devices':devices,'scope':_scope(project),'note':'Configuration is not target execution or operational approval'}


def record_execution(project,package,image,result,device):
    root=_storage(project)
    with runtime_state_lock(root):
        path=root/'hardware.json';journal=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'executions':[]}
        record={'scope':_scope(project),'device':device,'manifest_sha256':_sha256(Path(package)/'manifest.json'),
            'image_sha256':_sha256(Path(image)),'verdict':result.get('final_verdict'),'observed_at':time.time()}
        journal['executions'].append(record);journal['executions']=journal['executions'][-100:];atomic_private_json(path,journal)
    return record


def optimization_records(project):
    from backend.engine.runtime_optimization_jobs import read_job
    root=_root(project)/'exports'/'optimization_jobs';rows=[]
    if root.is_symlink() or not root.is_dir():return []
    for file in root.glob('*.json'):
        if file.is_symlink():continue
        try:
            row=read_job(project['project_dir'],file.stem);source=row.get('options',{}).get('input_receipt',{}).get('source_dataset_path')
            if source!=_scope(project)['source_dataset_path']:continue
            rows.append({**row,'kind':'runtime_optimization','source_dataset_path':source,'task':'flow','cancellable':row['status'] in ('queued','running'),
                         'cancel_endpoint':'/api/export/flow/optimization-jobs/'+row['job_id']+'/cancel'})
        except (ValueError,OSError,KeyError):continue
    return rows


def exercise_protocol(protocol,mode='success'):
    from backend.engine.protocol_exercise import exercise_protocol as execute
    return execute(protocol,mode)


def read_operator_inputs(project):
    root=_root(project)/'runtime_service';path=root/'inputs.json'
    if root.is_symlink() or path.is_symlink():raise ValueError('Input configuration cannot follow links')
    if not path.exists():return {'mode':'manual','folder':None,'camera':None,'scope':_scope(project)}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('scope')!=_scope(project):return {'mode':'manual','folder':None,'camera':None,'scope':_scope(project),'needs_reconfiguration':True}
    if value.get('mode')=='camera':
        from backend.engine.camera_admission import camera_identity
        try:return {**value,'camera_id':camera_identity(value.get('camera'),value.get('camera_id'))}
        except ValueError:return {**value,'camera_id':None,'needs_reconfiguration':True}
    return {**value,'camera_id':None}


def configure_operator_inputs(project,mode,folder=None,camera=None,camera_id=None):
    from backend.engine.managed_service import ManagedService
    if mode not in ('manual','folder','camera'):raise ValueError('Select manual, folder or camera input')
    if mode=='folder':
        source=project.get('source_dataset_dir');path=Path(folder or '').expanduser()
        if not source or path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(Path(source).resolve()):raise ValueError('Input folder must belong to the active project source')
        folder=str(path.resolve())
    else:folder=None
    if mode=='camera':
        if not camera or not (camera.isdecimal() or camera.startswith(('rtsp://','rtsps://'))):raise ValueError('Camera input needs a device index or RTSP address')
        from backend.engine.camera_admission import camera_identity
        camera_id=camera_identity(camera,camera_id)
    else:camera=None;camera_id=None
    service=ManagedService(project['project_dir']);path=service.root/'inputs.json'
    if path.is_symlink():raise ValueError('Input configuration cannot follow links')
    value={'mode':mode,'folder':folder,'camera':camera,'camera_id':camera_id,'scope':_scope(project)}
    atomic_private_json(path,value)
    return {'saved':True,'restart_required':service.owned_process() is not None,'config':value}

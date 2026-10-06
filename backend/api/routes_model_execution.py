"""Explicit execution selection for native specialist evaluate/predict/generate."""
import base64
import hashlib
import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,model_validator
from backend.api.routes_project import get_current_project
from backend.engine.evaluation_history import canonical
from backend.engine.execution_recipe import request_model,run_recipe,support_matrix
from backend.remote.recipe import sha,execute_remote

router=APIRouter(prefix='/api/model-execution',tags=['model-execution'])


class RecipeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    task:Literal['classification','detection','segmentation','anomaly','rotation','ocr','rotated_detection','enhancement','defect_gan','patch_classification']
    stage:Literal['evaluate','predict','generate','benchmark']
    execution_target:Literal['local','selected_compute']='local'
    device:Literal['cpu','cuda','cuda:0','mps']='cpu'
    compute_profile_id:str|None=None
    params:dict

    @model_validator(mode='after')
    def explicit_target(self):
        if (self.execution_target=='selected_compute') != bool(self.compute_profile_id):raise ValueError('Recipe target and profile must match explicitly')
        if self.execution_target=='selected_compute' and self.device=='mps':raise ValueError('Remote recipe cannot execute on local MPS')
        return self


def _root(project):
    root=Path(project['reports_dir'])/'execution_recipes'
    if Path(project['reports_dir']).is_symlink() or root.is_symlink():raise ValueError('Recipe receipt storage is linked')
    root.mkdir(parents=True,exist_ok=True);return root


def _checkpoint(request,task,identifier):
    from backend.api import routes_rotation,routes_ocr,routes_rotated_detection,routes_enhancement,routes_defect_gan,routes_patch_classification
    modules={'rotation':routes_rotation,'ocr':routes_ocr,'rotated_detection':routes_rotated_detection,'enhancement':routes_enhancement,'defect_gan':routes_defect_gan}
    if task in ('classification','detection','segmentation','anomaly'):
        from backend.api.routes_evaluation import _resolve_job_artifacts
        project=get_current_project(request)
        return _resolve_job_artifacts(identifier,source_dataset_path=project['source_dataset_dir'],source_task=task)[1]
    if task=='patch_classification':return routes_patch_classification._completed_patch(get_current_project(request),identifier)
    return modules[task]._checkpoint(request,identifier)


def _regular(path,root=None):
    p=Path(path).expanduser()
    if p.is_symlink() or not p.is_file() or (root and not p.resolve().is_relative_to(Path(root).resolve())):raise ValueError('Recipe input must be a regular owned file')
    boundary=Path(root).resolve() if root else p.parent.resolve()
    if any(parent.is_symlink() for parent in (p,*p.parents) if parent.resolve().is_relative_to(boundary)):raise ValueError('Recipe input directory is linked')
    return p.resolve()


def _binding(request,project,req):
    model=request_model(req.task,req.stage)
    if set(req.params)-set(model.model_fields):raise ValueError('Unknown native recipe option')
    params=model.model_validate(req.params).model_dump(mode='json')
    # A duplicated inner device is not allowed to override the outer selection.
    params.pop('device',None)
    checkpoint=_checkpoint(request,req.task,params['job_id']);files={}
    files['inputs/model/best_model.pt']=_regular(checkpoint,project['models_dir'])
    files['inputs/model/model_meta.json']=_regular(checkpoint.parent/'model_meta.json',project['models_dir'])
    source=Path(project['source_dataset_dir']).resolve();image_ref=None;dataset_ref=None
    image=params.get('image_path') or params.get('source_image_path')
    if image:
        p=_regular(image,source);image_ref='inputs/image'+p.suffix.lower();files[image_ref]=p
    dataset=params.get('dataset_path');core_evaluation=None
    if req.task in ('classification','detection','segmentation','anomaly') and req.stage=='evaluate':
        from backend.remote.profiles import get_profile_store
        from backend.engine.core_evaluation_recipe import freeze_core_recipe
        profile=get_profile_store().get(req.compute_profile_id) if req.compute_profile_id else None
        if req.compute_profile_id and profile is None:raise ValueError('Selected recipe execution profile is missing')
        core_evaluation,archive=freeze_core_recipe(project,checkpoint,params,req.execution_target,profile,'cuda:0' if req.device=='cuda' else req.device)
        files['inputs/cohort.tar.gz']=_regular(archive,project['reports_dir'])
    elif dataset:
        from backend.engine.model_execution import resolve_training_input
        dataset=resolve_training_input(project,req.task,dataset);dataset_ref='inputs/dataset'
        allowed_manifest={'patch_classification':'patches.json','rotation':'rotation.json','ocr':'ocr.json','rotated_detection':'rotated_boxes.json','enhancement':'pairs.json','defect_gan':'defect_gan.json'}[req.task]
        allowed_json={allowed_manifest}
        if req.task=='patch_classification' and (dataset/'source_manifest.json').exists():
            from backend.engine.patch_classification import load_patch_manifest
            mapping=load_patch_manifest(dataset).provenance['source_map']
            expected=[{'image':str(dataset/name),'source_image':str(source/row['source_relative_path']),'source_sha256':row['source_sha256']} for name,row in mapping.items()]
            sidecar=json.loads(_regular(dataset/'source_manifest.json',dataset).read_text())
            if not isinstance(sidecar,list) or sorted(canonical(row) for row in sidecar)!=sorted(canonical(row) for row in expected):
                raise ValueError('Patch source sidecar differs from its exact original provenance')
            allowed_json.add('source_manifest.json')
        for p in dataset.rglob('*'):
            if p.is_file():
                if p.suffix.lower()=='.json' and p.relative_to(dataset).as_posix() not in allowed_json:
                    raise ValueError('Recipe accepts only its task manifest and pixel files')
                files[dataset_ref+'/'+p.relative_to(dataset).as_posix()]=_regular(p,dataset)
        # Frozen original aliases keep the unchanged manifest/checkpoint lineage
        # valid on the worker without rewriting their recorded source paths.
        from backend.engine.grouped_dataset_views import source_image_paths
        for p in source_image_paths(source,req.task):
            files['inputs/original/'+p.relative_to(source).as_posix()]=_regular(p,source)
    descriptor=[];total=0
    for name,p in sorted(files.items()):
        size=p.stat().st_size;total+=size
        if size>512*1024**2 or total>2*1024**3 or len(files)>10000:raise ValueError('Recipe input exceeds transfer limits')
        if name!='inputs/cohort.tar.gz' and not name.startswith('inputs/model/') and p.suffix.lower() not in {'.json','.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp','.dcm','.dicom'}:
            raise ValueError('Recipe dataset contains an unsupported file; prepare a task-only snapshot')
        descriptor.append({'path':name,'size':size,'sha256':sha(p)})
    binding={'task':req.task,'stage':req.stage,'params':params,'inputs':descriptor,'image_ref':image_ref,'dataset_ref':dataset_ref,
             'source_root':str(source) if dataset_ref else None}
    if core_evaluation:binding['core_evaluation']=core_evaluation
    return checkpoint,files,binding


def _save(project,record):
    body={**record,'receipt_id':'recipe_'+uuid.uuid4().hex,'created_at':time.time()}
    body['evidence_sha256']=hashlib.sha256(canonical(body)).hexdigest()
    with (_root(project)/(body['receipt_id']+'.json')).open('xb') as stream:
        stream.write(canonical(body));stream.flush()
        import os
        os.fsync(stream.fileno())
    return body


@router.get('/capabilities')
def capabilities():return {'families':support_matrix(),'remote_devices':['cpu','cuda:0'],'local_devices':['cpu','cuda','mps']}


@router.get('/recipes/{receipt_id}')
def receipt(receipt_id:str,request:Request):
    if not re.fullmatch(r'recipe_[0-9a-f]{32}',receipt_id):raise HTTPException(422,'Invalid recipe receipt')
    path=_root(get_current_project(request))/(receipt_id+'.json')
    try:
        if path.is_symlink():raise ValueError('Recipe receipt is linked')
        value=json.loads(path.read_text());digest=value.pop('evidence_sha256')
        if value['receipt_id']!=receipt_id or hashlib.sha256(canonical(value)).hexdigest()!=digest:raise ValueError('Recipe receipt hash changed')
        return {**value,'evidence_sha256':digest}
    except FileNotFoundError as exc:raise HTTPException(404,'Recipe receipt not found') from exc
    except (ValueError,KeyError) as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/recipes')
def history(request:Request,task:str,job_id:str):
    rows=[]
    for path in _root(get_current_project(request)).glob('recipe_*.json'):
        row=receipt(path.stem,request)
        if row['task']==task and row['job_id']==job_id:rows.append({k:v for k,v in row.items() if k!='result'})
    return {'receipts':sorted(rows,key=lambda row:row['created_at'],reverse=True)[:20]}


@router.post('/recipes')
def execute(req:RecipeRequest,request:Request):
    from backend.engine.runtime_device_identity import runtime_device_identity
    from backend.remote.profiles import get_profile_store
    from backend.remote import operations
    from backend.remote.coordinator import ArtifactValidationError,RemoteDisconnected
    from backend.remote.ssh_transport import SSHTransportError
    project=get_current_project(request)
    try:
        if req.task in ('classification','detection','segmentation','anomaly') and req.stage=='evaluate':
            from backend.api.routes_evaluation import _common_project
            project=_common_project(request)
        checkpoint,files,binding=_binding(request,project,req)
        profile=get_profile_store().get(req.compute_profile_id) if req.compute_profile_id else None
        if req.compute_profile_id and profile is None:raise ValueError('Selected recipe execution profile is missing')
        if binding.get('core_evaluation') and binding['core_evaluation']['execution_profile_sha256']!=(hashlib.sha256(canonical(profile.model_dump())).hexdigest() if profile else None):
            raise ValueError('Selected core evaluation profile changed before execution')
        device='cuda:0' if req.device=='cuda' else req.device
        output=_root(project)/('local_'+uuid.uuid4().hex)
        if profile:
            result,runtime,artifacts=execute_remote(profile,project,binding,files,device)
        else:
            inputs={}
            if binding['image_ref']:inputs['image']=files[binding['image_ref']]
            if binding['dataset_ref']:inputs['dataset']=Path(binding['params']['dataset_path'])
            native_output=output
            if binding.get('core_evaluation'):
                inputs.update(core_evaluation=binding['core_evaluation'],cohort_archive=files['inputs/cohort.tar.gz'])
                native_output=output/'outputs'
            result=run_recipe(req.task,req.stage,checkpoint,inputs,binding['params'],native_output,device)
            runtime=runtime_device_identity(device) if device!='mps' else {'device':'mps','process_id':__import__('os').getpid(),'device_name':'Metal MPS'}
            import platform,torch
            runtime.update(torch_version=torch.__version__,python_version=platform.python_version(),platform=platform.platform())
            artifacts={'outputs/'+p.relative_to(native_output).as_posix():p for p in native_output.rglob('*') if p.is_file()}
        for row in binding['inputs']:
            if sha(files[row['path']])!=row['sha256']:raise ValueError('Recipe inputs changed during execution')
        if profile and get_profile_store().get(profile.id)!=profile:raise ValueError('Selected recipe profile changed during execution')
        if req.stage=='generate':
            review=Path(project['project_dir'])/'synthetic_review'/binding['params']['job_id']/uuid.uuid4().hex
            if any(p.is_symlink() for p in (review.parent,review.parent.parent)):raise ValueError('GAN review storage is linked')
            review.mkdir(parents=True)
            for name,path in artifacts.items():
                if name.startswith('outputs/candidates/'):
                    target=review/name.removeprefix('outputs/candidates/');target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(path,target)
            for candidate in result['candidates']:
                candidate['path']=str(review/Path(candidate['path']).name)
                if sha(candidate['path'])!=candidate['sha256']:raise ValueError('GAN downloaded candidate hash changed')
                candidate['preview_data_url']='data:image/png;base64,'+base64.b64encode(Path(candidate['path']).read_bytes()).decode()
            if result.get('source_snapshot'):result['source_snapshot']=str(review/Path(result['source_snapshot']).name)
            if result.get('source_image_path'):result['source_image_path']=binding['params']['source_image_path']
            (review/'review_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));result['review_dir']=str(review)
        if req.stage=='evaluate':
            from backend.engine.evaluation_history import archive_specialized_evaluation
            if binding.get('core_evaluation'):
                from backend.engine.core_evaluation_recipe import archive_core_evaluation
                result=archive_core_evaluation(project,checkpoint,binding['core_evaluation'],result,artifacts,runtime,output/'adoption')
            elif req.task=='patch_classification':
                from backend.engine.patch_evaluation_evidence import archive_patch_evaluation
                result=archive_patch_evaluation(project,checkpoint,binding['params']['dataset_path'],result,
                    execution={'execution_target':req.execution_target,'compute_profile_id':req.compute_profile_id,'compute_profile_name':profile.name if profile else None,
                        'execution_profile_sha256':hashlib.sha256(canonical(profile.model_dump())).hexdigest() if profile else None,
                        'device':device,'runtime':runtime,'input_binding_sha256':hashlib.sha256(canonical(binding)).hexdigest()})
            else:result=archive_specialized_evaluation(project,checkpoint,project['source_dataset_dir'],result,task=req.task,dataset_path=binding['params']['dataset_path'])
        saved=_save(project,{'task':req.task,'stage':req.stage,'job_id':binding['params']['job_id'],'result':result,
            'execution_target':req.execution_target,'compute_profile_id':req.compute_profile_id,'compute_profile_name':profile.name if profile else None,
            'execution_profile_sha256':hashlib.sha256(canonical(profile.model_dump())).hexdigest() if profile else None,
            'device':device,'runtime':runtime,'checkpoint_sha256':sha(checkpoint),'input_binding_sha256':hashlib.sha256(canonical(binding)).hexdigest()})
        return {**result,'execution':{k:v for k,v in saved.items() if k!='result'}}
    except (RemoteDisconnected,SSHTransportError) as exc:raise HTTPException(503,'Selected execution target disconnected; no local fallback: '+str(exc)) from exc
    except operations.RemoteComputeBusy as exc:raise HTTPException(409,str(exc)) from exc
    except ArtifactValidationError as exc:raise HTTPException(502,str(exc)) from exc
    except (ValueError,OSError,RuntimeError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc)) from exc

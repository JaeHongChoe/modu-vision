"""Text grounding and image-exemplar inference produce review-only proposals."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Optional,Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field,field_validator
from PIL import Image
from backend.engine.dicom_input import open_source_image
from backend.api.routes_project import get_current_project,_write_json
from backend.engine.label_candidate_providers import semantic_readiness,grounded_candidates,template_candidates,model_directory_hash,foundation_candidates,filter_candidate_sizes
from backend.engine.foundation_labeling import foundation_readiness,check_cancel,file_sha256
from backend.engine import labeling_tasks
from backend.engine.dataset_metadata import metadata_for_path
from backend.api import routes_label_suggestions as suggestions,routes_annotation,routes_dataset
from backend.api.routes_dataset_versions import _snapshot,_source_path,_VERSION_LOCK
from backend.engine.annotation_storage import dataset_annotation_dir

router=APIRouter(prefix='/api/label-candidates',tags=['label-candidates'])

class SetupRequest(BaseModel):
    model_dir:Optional[str]=None
    mask_model_dir:Optional[str]=None
    feature_backbone:str='dinov3_vits16'
    feature_checkpoint:Optional[str]=None
    feature_sha256:Optional[str]=None

class ExampleRequest(BaseModel):
    image_path:str
    roi:list[float]=Field(...,min_length=4,max_length=4)

class PointRequest(BaseModel):
    x:float
    y:float
    label:Literal[0,1]=1

class CandidateRequest(BaseModel):
    backend:Literal['grounding_dino','template_match','foundation']
    image_path:str
    prompt:str=''
    label:str='defect'
    threshold:float=Field(.5,ge=0,le=1)
    text_threshold:float=Field(.25,ge=0,le=1)
    exemplar_path:Optional[str]=None
    exemplar_roi:Optional[list[float]]=Field(None,min_length=4,max_length=4)
    max_candidates:int=Field(20,ge=1,le=100)
    positive_examples:list[ExampleRequest]=Field(default_factory=list,max_length=100)
    negative_examples:list[ExampleRequest]=Field(default_factory=list,max_length=100)
    points:list[PointRequest]=Field(default_factory=list,max_length=1000)
    boxes:list[list[float]]=Field(default_factory=list,max_length=1000)
    device:str='cpu'
    min_area:float=Field(0,ge=0)
    max_area:Optional[float]=Field(None,ge=0)
    min_width:float=Field(0,ge=0)
    max_width:Optional[float]=Field(None,ge=0)
    min_height:float=Field(0,ge=0)
    max_height:Optional[float]=Field(None,ge=0)
    output_geometry:Literal['polygon','mask','bbox']='polygon'
    labelset_id:Optional[str]=None
    labelset_version:Optional[str]=None
    suggestion_model_id:Optional[str]=None
    class_ids:dict[str,int]=Field(default_factory=dict)

    @field_validator('class_ids')
    @classmethod
    def validate_class_ids(cls,value):
        if any(not name.strip() or not 1<=cid<=255 for name,cid in value.items()) or len(set(value.values()))!=len(value):
            raise ValueError('Candidate class mapping requires unique IDs 1–255 and nonempty names')
        return value

class CandidateBatchRequest(CandidateRequest):
    image_path:str=''
    image_paths:list[str]=Field(...,min_length=1,max_length=5000)


def _setup(project):
    path=Path(project['project_dir'])/'semantic_labeling.json'
    try: return json.loads(path.read_text()) if path.is_file() else {}
    except (OSError,ValueError): raise HTTPException(422,detail='Invalid semantic labeling configuration')

def _verify_provider_provenance(proposal):
    try:
        for candidate in proposal['candidates']:
            provenance=candidate.get('provenance',{})
            if provenance.get('model_dir') and model_directory_hash(provenance['model_dir'])!=provenance.get('model_sha256'):
                raise ValueError('Foundation model changed')
            feature=provenance.get('feature_metadata',{})
            if feature.get('feature_checkpoint') and file_sha256(feature['feature_checkpoint'])!=feature.get('pretrained_sha256'):
                raise ValueError('DINOv3 feature weights changed')
            grounding=provenance.get('grounding',{})
            if grounding.get('model_dir') and model_directory_hash(grounding['model_dir'])!=grounding['model_sha256']:
                raise ValueError('Text grounding model changed')
    except (OSError,ValueError) as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.get('/setup')
def get_setup(request:Request):
    project=get_current_project(request)
    config=_setup(project)
    return {**semantic_readiness(config.get('model_dir')),'configuration':config,
            'providers':{'foundation':foundation_readiness(config),'grounding_dino':semantic_readiness(config.get('model_dir'))},
            'labelset_id':project.get('active_labelset_id','default'),'labelset_version':suggestions._dataset_fingerprint(project)}

@router.put('/setup')
def save_setup(req:SetupRequest,request:Request):
    project=get_current_project(request); config=_setup(project)
    for field in req.model_fields_set:
        value=getattr(req,field)
        if field.endswith('_dir') and value:
            directory=Path(value).expanduser().resolve()
            if not directory.is_dir(): raise HTTPException(422,detail='Choose an existing local model directory')
            value=str(directory)
        config[field]=value
    _write_json(Path(project['project_dir'])/'semantic_labeling.json',config)
    return get_setup(request)

@router.post('/generate')
def generate_candidates(req:CandidateRequest,request:Request):
    return _generate_candidates(req,get_current_project(request))

def _generate_candidates(req,project,cancel=None,batch_id=None):
    if project['task'] not in {'detection','segmentation'}: raise HTTPException(422,detail='Text/exemplar region proposals require detection or segmentation labeling')
    image=suggestions._image_path(project,req.image_path)
    source=_source_path(project)
    metadata=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)/f'{image.stem}.json'
    before={'image_sha256':suggestions._sha256(image),'labelme_sha256':suggestions._sha256(image.with_suffix('.json')),
            'studio_sha256':suggestions._sha256(studio),'dataset_fingerprint':suggestions._dataset_fingerprint(project)}
    labelset_id=project.get('active_labelset_id','default')
    if req.labelset_id is not None and req.labelset_id!=labelset_id or req.labelset_version is not None and req.labelset_version!=before['dataset_fingerprint']:
        raise HTTPException(409,detail='Source label set or version changed; refresh before generating')
    details={}
    try:
        if req.backend=='grounding_dino':
            directory=_setup(project).get('model_dir'); readiness=semantic_readiness(directory)
            if not readiness['ready']: raise HTTPException(422,detail=readiness['error'])
            signature=model_directory_hash(directory)
            candidates=grounded_candidates(image,directory,req.prompt,req.threshold,req.text_threshold,device=req.device,cancel=cancel)
            if model_directory_hash(directory)!=signature: raise HTTPException(409,detail='Semantic model changed during inference')
            details={'model_dir':directory,'checkpoint_sha256':signature,'prompt':req.prompt,'text_threshold':req.text_threshold,
                     'support_limits':readiness['limits']}
        elif req.backend=='foundation':
            setup=_setup(project)
            examples={}
            for kind in ['positive_examples','negative_examples']:
                examples[kind]=[{**e.model_dump(),'image_path':str(suggestions._image_path(project,e.image_path)),
                                 'sha256':suggestions._sha256(suggestions._image_path(project,e.image_path))} for e in getattr(req,kind)]
            suggestion_model=None
            if req.suggestion_model_id:
                from backend.engine.feature_labeling_jobs import load_feature_model
                suggestion_model=load_feature_model(project,req.suggestion_model_id)
            candidates=foundation_candidates(image,setup,prompt=req.prompt,label=req.label,points=[p.model_dump() for p in req.points],
                boxes=req.boxes,device=req.device,threshold=req.threshold,text_threshold=req.text_threshold,
                min_area=req.min_area,max_area=req.max_area,min_width=req.min_width,max_width=req.max_width,
                min_height=req.min_height,max_height=req.max_height,max_candidates=req.max_candidates,
                output_geometry=req.output_geometry,cancel=cancel,suggestion_model=suggestion_model,**examples)
            details={'foundation_setup':setup,'prompt':req.prompt,'device':req.device,'output_geometry':req.output_geometry,
                     'positive_examples':examples['positive_examples'],'negative_examples':examples['negative_examples'],
                     'suggestion_model_id':req.suggestion_model_id,
                     'suggestion_checkpoint_sha256':suggestion_model['checkpoint_sha256'] if suggestion_model else None,
                     'support_limits':foundation_readiness(setup)['limits']}
        else:
            if not req.exemplar_path: raise HTTPException(422,detail='Select an exemplar image from the current dataset')
            exemplar=suggestions._image_path(project,req.exemplar_path); exemplar_hash=suggestions._sha256(exemplar)
            candidates=template_candidates(image,exemplar,req.label,req.threshold,req.max_candidates,req.exemplar_roi)
            if suggestions._sha256(exemplar)!=exemplar_hash: raise HTTPException(409,detail='Exemplar changed during inference')
            details={'exemplar_path':str(exemplar),'exemplar_sha256':exemplar_hash,'exemplar_roi':req.exemplar_roi,
                     'support_limits':'OpenCV TM_CCOEFF_NORMED; exact scale and orientation only. No semantic classification; human review required.'}
    except (ValueError,OSError,RuntimeError,ImportError) as exc: raise HTTPException(422,detail=str(exc)) from exc
    try:
        candidates=filter_candidate_sizes(candidates,req.min_area,req.max_area,req.min_width,req.max_width,req.min_height,req.max_height)[:req.max_candidates]
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    check_cancel(cancel)
    from backend.engine.project_labelsets import load_labelsets
    if not suggestions._project_binding_is_current(project) or load_labelsets(Path(project['project_dir']))['active_id']!=labelset_id:
        raise HTTPException(409,detail='Project task, source or label set changed during inference')
    if req.backend=='foundation':
        if _setup(project)!=details['foundation_setup']:raise HTTPException(409,detail='Foundation setup changed during inference')
        _verify_provider_provenance({'candidates':candidates})
        for example in details['positive_examples']+details['negative_examples']:
            if file_sha256(example['image_path'])!=example['sha256']:raise HTTPException(409,detail='Image example changed during inference')
    if (suggestions._sha256(image)!=before['image_sha256'] or suggestions._sha256(studio)!=before['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=before['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=before['dataset_fingerprint']):
        raise HTTPException(409,detail='Image, labels or dataset changed during inference')
    suggestion_id=f'suggestion_{uuid.uuid4().hex[:24]}'
    for index,candidate in enumerate(candidates,1):
        candidate['id']=f'{suggestion_id}_c{index}'; candidate['annotation']['id']=candidate['id']
        if candidate['annotation']['label'] in req.class_ids:
            candidate['annotation']['category_id']=req.class_ids[candidate['annotation']['label']]
            candidate.setdefault('provenance',{})['label_class_ids']=req.class_ids
    with open_source_image(image) as pil: width,height=pil.size
    proposal={'id':suggestion_id,'project_id':project['id'],'status':'pending','created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
              'image_path':str(image),'image_id':image.stem,'image_width':width,'image_height':height,
              'image_uuid':metadata['image_uuid'],'image_revision':metadata['revision'],'task':project['task'],'backend':req.backend,
              'job_id':req.backend,'threshold':req.threshold,'candidates':candidates,
              'confidence':max((c['confidence'] for c in candidates),default=0),'latency_ms':None,
              'accepted_candidate_ids':[],'backup_version_id':None,'labelset_id':labelset_id,
              'labelset_version':before['dataset_fingerprint'],'batch_id':batch_id,**before,**details}
    _write_json(suggestions._proposal_path(project,suggestion_id),proposal)
    return proposal

@router.post('/batches')
def start_candidate_batch(req:CandidateBatchRequest,request:Request):
    project=dict(get_current_project(request))
    paths=[str(suggestions._image_path(project,path)) for path in req.image_paths]
    baseline=suggestions._dataset_fingerprint(project)
    def worker(job,cancel):
        for index,path in enumerate(paths):
            check_cancel(cancel)
            entry=job['entries'][index]
            entry['status']='running';labeling_tasks.write(project,job)
            if suggestions._dataset_fingerprint(project)!=baseline: raise ValueError('Source dataset changed during labeling batch')
            single=CandidateRequest.model_validate({**req.model_dump(),'image_path':path})
            try: proposal=_generate_candidates(single,project,cancel,job['id'])
            except HTTPException as exc:
                check_cancel(cancel)
                entry.update(status='failed',error=str(exc.detail));job['failed']+=1;job['processed']+=1
                labeling_tasks.write(project,job)
                raise ValueError(str(exc.detail)) from exc
            entry.update(status='generated' if proposal['candidates'] else 'zero_candidates',proposal_id=proposal['id'],candidate_count=len(proposal['candidates']))
            job['proposals'].append(proposal['id']);job['generated']+=int(bool(proposal['candidates']))
            job['processed']+=1;job['zero_candidates']+=int(not proposal['candidates'])
            labeling_tasks.write(project,job)
    try:
        return labeling_tasks.start(project,'candidate_batch',{'total':len(paths),'processed':0,'generated':0,
            'failed':0,'zero_candidates':0,'proposals':[],'entries':[{'image_path':path,'status':'queued'} for path in paths],
            'request':req.model_dump(),'review_fingerprint':baseline},worker)
    except ValueError as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.get('/batches')
def list_candidate_batches(request:Request):
    return {'batches':labeling_tasks.list_jobs(get_current_project(request),'candidate_batch')}

@router.get('/batches/{batch_id}')
def get_candidate_batch(batch_id:str,request:Request):
    try: return labeling_tasks.read(get_current_project(request),batch_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='Labeling batch not found') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc

@router.post('/batches/{batch_id}/cancel')
def cancel_candidate_batch(batch_id:str,request:Request):
    try: return labeling_tasks.cancel(get_current_project(request),batch_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='Labeling batch not found') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc


def review_external_proposal(project,proposal,req):
    image=suggestions._image_path(project,proposal['image_path']); source=_source_path(project)
    if proposal['task']!=project['task']: raise HTTPException(409,detail='Project task changed')
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)
    json_path=studio/f'{image.stem}.json';mask_path=studio/'masks'/f'{image.stem}.png'
    batch=labeling_tasks.read(project,proposal['batch_id']) if proposal.get('batch_id') else None
    if labeling_tasks.active(project): raise HTTPException(409,detail='Finish or cancel the active labeling task before accepting labels')
    if proposal.get('labelset_id',project.get('active_labelset_id','default'))!=project.get('active_labelset_id','default'):
        raise HTTPException(409,detail='Source label set changed')
    expected_fingerprint=batch['review_fingerprint'] if batch else proposal['dataset_fingerprint']
    if (suggestions._sha256(image)!=proposal['image_sha256'] or suggestions._sha256(json_path)!=proposal['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=proposal['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=expected_fingerprint):
        raise HTTPException(409,detail='Source labels or dataset changed; generate candidates again')
    if proposal['backend']=='template_match':
        if suggestions._sha256(suggestions._image_path(project,proposal['exemplar_path']))!=proposal['exemplar_sha256']: raise HTTPException(409,detail='Exemplar changed')
    elif proposal['backend']=='foundation':
        if _setup(project)!=proposal['foundation_setup']: raise HTTPException(409,detail='Foundation provider setup changed')
        for example in proposal['positive_examples']+proposal['negative_examples']:
            if suggestions._sha256(suggestions._image_path(project,example['image_path']))!=example['sha256']: raise HTTPException(409,detail='Image example changed')
        _verify_provider_provenance(proposal)
        if proposal.get('suggestion_model_id'):
            from backend.engine.feature_labeling_jobs import load_feature_model
            try:
                if load_feature_model(project,proposal['suggestion_model_id'])['checkpoint_sha256']!=proposal['suggestion_checkpoint_sha256']:
                    raise ValueError('Trained feature model checkpoint changed')
            except (OSError,ValueError) as exc:raise HTTPException(409,detail='Trained feature model became unavailable or changed') from exc
    elif model_directory_hash(proposal['model_dir'])!=proposal['checkpoint_sha256']: raise HTTPException(409,detail='Semantic model changed')
    selected=set(req.candidate_ids);available={c['id'] for c in proposal['candidates']}
    if not selected or not selected<=available: raise HTTPException(422,detail='Select proposal candidates to adopt')
    from backend.api.routes_training import training_job_manager
    if training_job_manager.get_active_job() is not None: raise HTTPException(409,detail='Finish training before adopting labels')
    existing=routes_annotation.get_annotations(image.stem,file_path=str(image)).get('annotations',[])
    current=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    if current['revision']!=proposal['image_revision']: raise HTTPException(409,detail='Another reviewer changed this image; generate again')
    backup=_snapshot(project,source,f'후보 채택 전 · {image.name}',req.actor,'auto_backup')
    previous_json=json_path.read_bytes() if json_path.exists() else None; previous_mask=mask_path.read_bytes() if mask_path.exists() else None
    try:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id=image.stem,image_path=str(image),
            annotations=[*existing,*[c['annotation'] for c in proposal['candidates'] if c['id'] in selected]],
            image_width=proposal['image_width'],image_height=proposal['image_height'],expected_revision=current['revision'],actor=req.actor))
        proposal.update(status='accepted',reviewer=req.actor,reviewed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                        accepted_candidate_ids=sorted(selected),backup_version_id=backup['id'])
        _write_json(suggestions._proposal_path(project,proposal['id']),proposal)
        if batch:
            batch['review_fingerprint']=suggestions._dataset_fingerprint(project)
            labeling_tasks.write(project,batch)
    except Exception:
        suggestions._restore_bytes(json_path,previous_json);suggestions._restore_bytes(mask_path,previous_mask);raise
    return proposal

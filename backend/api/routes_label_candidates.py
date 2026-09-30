"""Text grounding and image-exemplar inference produce review-only proposals."""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Optional,Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field
from PIL import Image
from backend.api.routes_project import get_current_project,_write_json
from backend.engine.label_candidate_providers import semantic_readiness,grounded_candidates,template_candidates,model_directory_hash
from backend.engine.dataset_metadata import metadata_for_path
from backend.api import routes_label_suggestions as suggestions,routes_annotation,routes_dataset
from backend.api.routes_dataset_versions import _snapshot,_source_path,_VERSION_LOCK
from backend.engine.annotation_storage import dataset_annotation_dir

router=APIRouter(prefix='/api/label-candidates',tags=['label-candidates'])

class SetupRequest(BaseModel):
    model_dir:str=Field(...,min_length=1)

class CandidateRequest(BaseModel):
    backend:Literal['grounding_dino','template_match']
    image_path:str
    prompt:str=''
    label:str='defect'
    threshold:float=Field(.5,ge=0,le=1)
    text_threshold:float=Field(.25,ge=0,le=1)
    exemplar_path:Optional[str]=None
    exemplar_roi:Optional[list[float]]=Field(None,min_length=4,max_length=4)
    max_candidates:int=Field(20,ge=1,le=100)


def _setup(project):
    path=Path(project['project_dir'])/'semantic_labeling.json'
    try: return json.loads(path.read_text()) if path.is_file() else {}
    except (OSError,ValueError): raise HTTPException(422,detail='Invalid semantic labeling configuration')

@router.get('/setup')
def get_setup(request:Request):
    project=get_current_project(request)
    return semantic_readiness(_setup(project).get('model_dir'))

@router.put('/setup')
def save_setup(req:SetupRequest,request:Request):
    project=get_current_project(request); directory=Path(req.model_dir).expanduser().resolve()
    if not directory.is_dir(): raise HTTPException(422,detail='Choose an existing local model directory')
    readiness=semantic_readiness(directory)
    _write_json(Path(project['project_dir'])/'semantic_labeling.json',{'model_dir':str(directory),'backend':'grounding_dino'})
    return readiness

@router.post('/generate')
def generate_candidates(req:CandidateRequest,request:Request):
    project=get_current_project(request)
    if project['task'] not in {'detection','segmentation'}: raise HTTPException(422,detail='Text/exemplar region proposals require detection or segmentation labeling')
    image=suggestions._image_path(project,req.image_path)
    source=_source_path(project)
    metadata=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)/f'{image.stem}.json'
    before={'image_sha256':suggestions._sha256(image),'labelme_sha256':suggestions._sha256(image.with_suffix('.json')),
            'studio_sha256':suggestions._sha256(studio),'dataset_fingerprint':suggestions._dataset_fingerprint(project)}
    details={}
    try:
        if req.backend=='grounding_dino':
            directory=_setup(project).get('model_dir'); readiness=semantic_readiness(directory)
            if not readiness['ready']: raise HTTPException(422,detail=readiness['error'])
            signature=model_directory_hash(directory)
            candidates=grounded_candidates(image,directory,req.prompt,req.threshold,req.text_threshold)
            if model_directory_hash(directory)!=signature: raise HTTPException(409,detail='Semantic model changed during inference')
            details={'model_dir':directory,'checkpoint_sha256':signature,'prompt':req.prompt,'text_threshold':req.text_threshold,
                     'support_limits':readiness['limits']}
        else:
            if not req.exemplar_path: raise HTTPException(422,detail='Select an exemplar image from the current dataset')
            exemplar=suggestions._image_path(project,req.exemplar_path); exemplar_hash=suggestions._sha256(exemplar)
            candidates=template_candidates(image,exemplar,req.label,req.threshold,req.max_candidates,req.exemplar_roi)
            if suggestions._sha256(exemplar)!=exemplar_hash: raise HTTPException(409,detail='Exemplar changed during inference')
            details={'exemplar_path':str(exemplar),'exemplar_sha256':exemplar_hash,'exemplar_roi':req.exemplar_roi,
                     'support_limits':'OpenCV TM_CCOEFF_NORMED; exact scale and orientation only. No semantic classification; human review required.'}
    except (ValueError,OSError,RuntimeError,ImportError) as exc: raise HTTPException(422,detail=str(exc)) from exc
    if (suggestions._sha256(image)!=before['image_sha256'] or suggestions._sha256(studio)!=before['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=before['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=before['dataset_fingerprint']):
        raise HTTPException(409,detail='Image, labels or dataset changed during inference')
    suggestion_id=f'suggestion_{uuid.uuid4().hex[:24]}'
    for index,candidate in enumerate(candidates,1):
        candidate['id']=f'{suggestion_id}_c{index}'; candidate['annotation']['id']=candidate['id']
    with Image.open(image) as pil: width,height=pil.size
    proposal={'id':suggestion_id,'project_id':project['id'],'status':'pending','created_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
              'image_path':str(image),'image_id':image.stem,'image_width':width,'image_height':height,
              'image_uuid':metadata['image_uuid'],'image_revision':metadata['revision'],'task':project['task'],'backend':req.backend,
              'job_id':req.backend,'threshold':req.threshold,'candidates':candidates,
              'confidence':max((c['confidence'] for c in candidates),default=0),'latency_ms':None,
              'accepted_candidate_ids':[],'backup_version_id':None,**before,**details}
    _write_json(suggestions._proposal_path(project,suggestion_id),proposal)
    return proposal


def review_external_proposal(project,proposal,req):
    image=suggestions._image_path(project,proposal['image_path']); source=_source_path(project)
    if proposal['task']!=project['task']: raise HTTPException(409,detail='Project task changed')
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)
    json_path=studio/f'{image.stem}.json';mask_path=studio/'masks'/f'{image.stem}.png'
    if (suggestions._sha256(image)!=proposal['image_sha256'] or suggestions._sha256(json_path)!=proposal['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=proposal['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=proposal['dataset_fingerprint']):
        raise HTTPException(409,detail='Source labels or dataset changed; generate candidates again')
    if proposal['backend']=='template_match':
        if suggestions._sha256(suggestions._image_path(project,proposal['exemplar_path']))!=proposal['exemplar_sha256']: raise HTTPException(409,detail='Exemplar changed')
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
    except Exception:
        suggestions._restore_bytes(json_path,previous_json);suggestions._restore_bytes(mask_path,previous_mask);raise
    return proposal

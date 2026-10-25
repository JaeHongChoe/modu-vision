"""Text grounding and image-exemplar inference produce review-only proposals."""
from __future__ import annotations
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Optional,Literal
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator
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
_CLASS_REGISTRY_LOCK=threading.Lock()
_SAVED_CLASS_CACHE={}


def _saved_classes(project,source,fingerprint):
    """Class names and ids this project's saved labels already use (each Studio label file's shapes and mask classes),
    read once per dataset state: {casefolded name: (name, id)}."""
    studio=dataset_annotation_dir(source,Path(project['annotations_dir']),use_scope=False)
    key=(str(studio),fingerprint)
    classes=_SAVED_CLASS_CACHE.get(key)
    if classes is None:
        classes={}
        for path in sorted(studio.glob('*.json')) if studio.is_dir() else []:
            try: data=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError): continue
            rows=[*(data.get('annotations') or []),*(data.get('mask_classes') or [])] if isinstance(data,dict) else []
            for row in rows:
                if not isinstance(row,dict): continue
                name=row.get('label') if 'label' in row else row.get('name'); cid=row.get('category_id',row.get('id'))
                if isinstance(name,str) and name.strip() and isinstance(cid,int) and 1<=cid<=255: classes.setdefault(name.casefold(),(name,cid))
        if len(_SAVED_CLASS_CACHE)>=8: _SAVED_CLASS_CACHE.clear()
        _SAVED_CLASS_CACHE[key]=classes
    return classes


def _new_class_ids(project,labelset_id,names,taken):
    """Ids for classes a phrase draft names for the first time. They are kept per label set in the project, so every
    image, batch and restart gives a name the same id, and a new one is the lowest id in 1..255 no class uses."""
    path=suggestions._proposal_root(project)/'new_classes.json'
    with _CLASS_REGISTRY_LOCK:
        try: registry=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        except (OSError,ValueError): registry={}
        table=registry.setdefault(labelset_id,{})
        used=set(taken)|set(table.values()); result={}; changed=False
        for name in names:
            known=next((cid for key,cid in table.items() if key.casefold()==name.casefold()),None)
            if known is None:
                known=next((cid for cid in range(1,256) if cid not in used),None)
                if known is None: raise HTTPException(422,detail='새 클래스 번호(1–255)가 남지 않았습니다. 이 문구를 쓸 기존 클래스를 고르세요.')
                table[name]=known;used.add(known);changed=True
            result[name]=known
        if changed: _write_json(path,registry)
    return result

class SetupRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    vlm:Optional[dict]=None
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
    backend:Literal['grounding_dino','template_match','foundation','vlm']
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
    exclude_regions:list[list[float]]=Field(default_factory=list,max_length=100)
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
            raise ValueError('후보 클래스 번호는 1–255에서 서로 달라야 하고 클래스 이름은 비어 있으면 안 됩니다. 클래스 목록을 확인한 뒤 다시 만드세요.')
        return value

class CandidateBatchRequest(CandidateRequest):
    image_path:str=''
    image_paths:list[str]=Field(...,min_length=1,max_length=5000)


def _setup(project):
    path=Path(project['project_dir'])/'semantic_labeling.json'
    try: return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    except (OSError,ValueError): raise HTTPException(422,detail='모델 보조 설정 파일을 읽을 수 없습니다. 프로젝트 폴더의 semantic_labeling.json을 고치거나 다른 곳으로 옮긴 뒤 설정을 다시 저장하세요.')

def _can_configure_vlm(request):
    account=getattr(request.state,'account_user',None)
    if account is not None:return bool(account.get('administrator'))
    # Desktop mode is protected by the process capability. Fail closed if a
    # shared account store exists without an authenticated account context.
    return getattr(request.app.state,'accounts',None) is None

_PROVENANCE_CHANGED_AT_REVIEW='초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.'
_PROVENANCE_CHANGED_WHILE_DRAFTING='초안을 만드는 동안 모델 파일이 바뀌었거나 읽을 수 없게 되었습니다. 모델 폴더를 확인한 뒤 초안을 다시 만드세요.'


def _verify_provider_provenance(proposal,refusal=_PROVENANCE_CHANGED_AT_REVIEW):
    """Refuse (409) when a model file a draft's candidates name changed. The English reasons stay internal (the exception
    chain); the person reads ``refusal``: the review's by default, the drafting one while candidates are being made."""
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
    except (OSError,ValueError) as exc: raise HTTPException(409,detail=refusal) from exc

@router.get('/setup')
def get_setup(request:Request):
    project=get_current_project(request)
    config=_setup(project)
    return {**semantic_readiness(config.get('model_dir')),'configuration':config,'can_configure_vlm':_can_configure_vlm(request),
            'providers':{'foundation':foundation_readiness(config),'grounding_dino':semantic_readiness(config.get('model_dir')),'vlm':__import__('backend.engine.korean_condition_vlm',fromlist=['readiness']).readiness(config.get('vlm'))},
            'labelset_id':project.get('active_labelset_id','default'),'labelset_version':suggestions._dataset_fingerprint(project)}

@router.put('/setup')
def save_setup(req:SetupRequest,request:Request):
    if 'vlm' in req.model_fields_set and not _can_configure_vlm(request):raise HTTPException(403,detail='VLM 연결 주소와 인증 정보 참조는 서버 관리자만 설정할 수 있습니다. 서버 관리자에게 설정을 요청하세요.')
    project=get_current_project(request); config=_setup(project)
    for field in req.model_fields_set:
        value=getattr(req,field)
        if field=='vlm':
            from backend.engine.korean_condition_vlm import validate_configuration
            try:value=validate_configuration(value or {})
            except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
        if field.endswith('_dir') and value:
            directory=Path(value).expanduser().resolve()
            if not directory.is_dir(): raise HTTPException(422,detail='지정한 경로를 폴더로 찾지 못했습니다. 백엔드가 읽을 수 있는 모델 폴더 경로를 입력하세요.')
            value=str(directory)
        config[field]=value
    _write_json(Path(project['project_dir'])/'semantic_labeling.json',config)
    return get_setup(request)

@router.post('/generate')
def generate_candidates(req:CandidateRequest,request:Request):
    return _generate_candidates(req,get_current_project(request))

def _generate_candidates(req,project,cancel=None,batch_id=None):
    if req.exclude_regions and req.backend != 'foundation':
        raise HTTPException(422,detail='제외 영역은 SAM2 마스크 초안에서만 쓸 수 있습니다. 제외 영역을 빼거나 SAM2로 초안을 만드세요.')
    if project['task'] not in {'detection','segmentation'}: raise HTTPException(422,detail='영역 초안(문구·예시·SAM2·VLM)은 검출·분할 프로젝트에서만 만들 수 있습니다. 분류·이상 탐지는 직접 라벨을 달거나 학습한 모델로 초안을 만드세요.')
    if not req.label and not (req.backend in ('grounding_dino','foundation') and req.prompt.strip()):
        raise HTTPException(422,detail='후보 클래스가 비어 있습니다. 클래스 이름을 고르세요.')
    image=suggestions._image_path(project,req.image_path)
    source=_source_path(project)
    metadata=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)/f'{image.stem}.json'
    before={'image_sha256':suggestions._sha256(image),'labelme_sha256':suggestions._sha256(image.with_suffix('.json')),
            'studio_sha256':suggestions._sha256(studio),'dataset_fingerprint':suggestions._dataset_fingerprint(project)}
    labelset_id=project.get('active_labelset_id','default')
    if req.labelset_id is not None and req.labelset_id!=labelset_id or req.labelset_version is not None and req.labelset_version!=before['dataset_fingerprint']:
        raise HTTPException(409,detail='화면에 보이는 라벨 세트·데이터가 최신이 아닙니다. 화면을 새로고침한 뒤 초안을 다시 만드세요.')
    details={}
    try:
        if req.backend=='grounding_dino':
            directory=_setup(project).get('model_dir'); readiness=semantic_readiness(directory)
            if not readiness['ready']: raise HTTPException(422,detail=readiness['error'])
            signature=model_directory_hash(directory)
            candidates=grounded_candidates(image,directory,req.prompt,req.threshold,req.text_threshold,device=req.device,cancel=cancel)
            if model_directory_hash(directory)!=signature: raise HTTPException(409,detail='초안을 만드는 동안 텍스트 검출 모델 파일이 바뀌었습니다. 변경이 끝난 뒤 초안을 다시 만드세요.')
            details={'model_dir':directory,'checkpoint_sha256':signature,'prompt':req.prompt,'text_threshold':req.text_threshold,
                     'support_limits':readiness['limits']}
        elif req.backend=='vlm':
            from backend.engine.korean_condition_vlm import candidates as vlm_candidates,LIMITS
            setup=_setup(project).get('vlm')
            if req.output_geometry!='bbox':raise ValueError('VLM은 사각형(bbox) 후보만 만듭니다. 출력 형태를 bbox로 고르세요.')
            examples={kind:[{**e.model_dump(),'image_path':str(suggestions._image_path(project,e.image_path)),'sha256':suggestions._sha256(suggestions._image_path(project,e.image_path))} for e in getattr(req,kind)] for kind in ('positive_examples','negative_examples')}
            candidates=vlm_candidates(image,setup,prompt=req.prompt,label=req.label,threshold=req.threshold,max_candidates=req.max_candidates,cancel=cancel,**examples)
            details={'vlm_setup':setup,'prompt':req.prompt,'positive_examples':examples['positive_examples'],'negative_examples':examples['negative_examples'],'support_limits':LIMITS}
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
                output_geometry=req.output_geometry,cancel=cancel,suggestion_model=suggestion_model,
                exclude_regions=req.exclude_regions,**examples)
            details={'foundation_setup':setup,'prompt':req.prompt,'device':req.device,'output_geometry':req.output_geometry,
                     'exclude_regions':req.exclude_regions,
                     'prompt_spec':{'points':[p.model_dump() for p in req.points],'boxes':req.boxes,'exclude_regions':req.exclude_regions},
                     'positive_examples':examples['positive_examples'],'negative_examples':examples['negative_examples'],
                     'suggestion_model_id':req.suggestion_model_id,
                     'suggestion_checkpoint_sha256':suggestion_model['checkpoint_sha256'] if suggestion_model else None,
                     'support_limits':foundation_readiness(setup)['limits']}
        else:
            if not req.exemplar_path: raise HTTPException(422,detail='예시 이미지가 없습니다. 현재 데이터에서 예시 이미지를 고르세요.')
            exemplar=suggestions._image_path(project,req.exemplar_path); exemplar_hash=suggestions._sha256(exemplar)
            candidates=template_candidates(image,exemplar,req.label,req.threshold,req.max_candidates,req.exemplar_roi)
            if suggestions._sha256(exemplar)!=exemplar_hash: raise HTTPException(409,detail='초안을 만드는 동안 예시(템플릿) 이미지가 바뀌었습니다. 변경이 끝난 뒤 초안을 다시 만드세요.')
            details={'exemplar_path':str(exemplar),'exemplar_sha256':exemplar_hash,'exemplar_roi':req.exemplar_roi,
                     'support_limits':'OpenCV TM_CCOEFF_NORMED; exact scale and orientation only. No semantic classification; human review required.'}
    except (ValueError,OSError,RuntimeError,ImportError) as exc: raise HTTPException(422,detail=str(exc)) from exc
    try:
        candidates=filter_candidate_sizes(candidates,req.min_area,req.max_area,req.min_width,req.max_width,req.min_height,req.max_height)[:req.max_candidates]
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    check_cancel(cancel)
    from backend.engine.project_labelsets import load_labelsets
    if not suggestions._project_binding_is_current(project) or load_labelsets(Path(project['project_dir']))['active_id']!=labelset_id:
        raise HTTPException(409,detail='초안을 만드는 동안 작업 종류나 데이터 폴더, 라벨 세트가 바뀌었습니다. 지금 프로젝트를 확인한 뒤 초안을 다시 만드세요.')
    if req.backend=='vlm':
        if _setup(project).get('vlm')!=details['vlm_setup']:raise HTTPException(409,detail='초안을 만드는 동안 VLM 설정이 바뀌었습니다. 설정을 확인한 뒤 초안을 다시 만드세요.')
        for example in details['positive_examples']+details['negative_examples']:
            if file_sha256(example['image_path'])!=example['sha256']:raise HTTPException(409,detail='초안을 만드는 동안 VLM 예시 이미지가 바뀌었습니다. 변경이 끝난 뒤 초안을 다시 만드세요.')
    if req.backend=='foundation':
        if _setup(project)!=details['foundation_setup']:raise HTTPException(409,detail='초안을 만드는 동안 모델 보조 설정이 바뀌었습니다. 설정을 확인한 뒤 초안을 다시 만드세요.')
        _verify_provider_provenance({'candidates':candidates},_PROVENANCE_CHANGED_WHILE_DRAFTING)
        for example in details['positive_examples']+details['negative_examples']:
            if file_sha256(example['image_path'])!=example['sha256']:raise HTTPException(409,detail='초안을 만드는 동안 예시 이미지가 바뀌었습니다. 변경이 끝난 뒤 초안을 다시 만드세요.')
    if (suggestions._sha256(image)!=before['image_sha256'] or suggestions._sha256(studio)!=before['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=before['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=before['dataset_fingerprint']):
        raise HTTPException(409,detail='초안을 만드는 동안 이 이미지·라벨이나 데이터가 바뀌었습니다. 변경이 끝난 뒤 초안을 다시 만드세요.')
    if req.backend in ('grounding_dino','foundation') and req.prompt.strip() and not req.suggestion_model_id:
        # A phrase that names a class of this project ("Scratch" for "scratch", "긁힘" for a class 긁힘) labels its draft with
        # that class, whether the renderer listed it (class_ids) or only saved labels use it. Any other phrase drafts under
        # the class the person chose, or, when they chose none (label ''), under a new class named as they typed it: an
        # empty project's first drafts name their classes on purpose. A few-label classifier's own class is never replaced.
        from backend.engine.label_candidate_providers import grounding_phrases
        listed={name.casefold():(name,cid) for name,cid in req.class_ids.items()}
        saved=_saved_classes(project,source,before['dataset_fingerprint'])
        known=lambda text:listed.get(text.casefold()) or saved.get(text.casefold())
        unmatched=[] if req.label else [p['text'] for p in grounding_phrases(req.prompt) if not (known(p['text']) or known(p['query']))]
        new_classes=_new_class_ids(project,labelset_id,unmatched,{cid for _,cid in listed.values()}|{cid for _,cid in saved.values()}) if unmatched else {}
        for candidate in candidates:
            grounding=candidate.get('grounding') or (candidate.get('provenance') or {}).get('grounding_phrase')
            if not grounding: continue
            match=known(grounding['phrase']) or known(grounding['query'])
            if match: candidate['annotation'].update(label=match[0],category_id=match[1])
            elif req.label: candidate['annotation']['label']=req.label
            else:
                candidate['annotation'].update(label=grounding['phrase'],category_id=new_classes[grounding['phrase']])
                candidate.setdefault('provenance',{})['new_class']=True
            candidate.setdefault('provenance',{})['grounding_phrase']=grounding
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
    # Phrases the text model cannot read are refused before any image runs, in the words of the refusal itself.
    phrases=[]
    if req.backend in ('grounding_dino','foundation') and req.prompt.strip():
        from backend.engine.label_candidate_providers import grounding_phrases
        try: phrases=grounding_phrases(req.prompt)
        except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc
    baseline=suggestions._dataset_fingerprint(project)
    def worker(job,cancel):
        for index,path in enumerate(paths):
            check_cancel(cancel)
            entry=job['entries'][index]
            entry['status']='running';labeling_tasks.write(project,job)
            if suggestions._dataset_fingerprint(project)!=baseline: raise ValueError('초안을 만드는 동안 데이터가 바뀌어 멈췄습니다. 남은 이미지는 다시 시작하면 이어서 만듭니다.')
            single=CandidateRequest.model_validate({**req.model_dump(),'image_path':path})
            try: proposal=_generate_candidates(single,project,cancel,job['id'])
            except HTTPException as exc:
                check_cancel(cancel)
                # One image the model cannot read (422, 404) does not cost the rest of the folder their drafts, as in the
                # trained-model batch. A conflict (409: the project, label set, source or model changed) holds for every
                # remaining image, so it stops the batch, and so does a batch where every image failed.
                entry.update(status='failed',error=str(exc.detail));job['failed']+=1;job['processed']+=1
                job.setdefault('error',f'실패한 이미지가 있습니다 · 첫 이유: {exc.detail}')
                labeling_tasks.write(project,job)
                if exc.status_code==409 or job['failed']==len(paths): raise ValueError(str(exc.detail)) from exc
                continue
            entry.update(status='generated' if proposal['candidates'] else 'zero_candidates',proposal_id=proposal['id'],candidate_count=len(proposal['candidates']))
            job['proposals'].append(proposal['id']);job['generated']+=int(bool(proposal['candidates']))
            job['processed']+=1;job['zero_candidates']+=int(not proposal['candidates'])
            labeling_tasks.write(project,job)
    try:
        return labeling_tasks.start(project,'candidate_batch',{'total':len(paths),'processed':0,'generated':0,
            'failed':0,'zero_candidates':0,'proposals':[],'entries':[{'image_path':path,'status':'queued'} for path in paths],
            'request':req.model_dump(),'review_fingerprint':baseline,'phrases':phrases},worker)
    except ValueError as exc: raise HTTPException(409,detail=str(exc)) from exc

@router.get('/batches')
def list_candidate_batches(request:Request):
    return {'batches':labeling_tasks.list_jobs(get_current_project(request),'candidate_batch')}

@router.get('/batches/{batch_id}')
def get_candidate_batch(batch_id:str,request:Request):
    try: return labeling_tasks.read(get_current_project(request),batch_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='그 일괄 초안 기록을 찾을 수 없습니다. 목록을 새로고침한 뒤 다시 고르세요.') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc

@router.post('/batches/{batch_id}/cancel')
def cancel_candidate_batch(batch_id:str,request:Request):
    try: return labeling_tasks.cancel(get_current_project(request),batch_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='그 일괄 초안 기록을 찾을 수 없습니다. 목록을 새로고침한 뒤 다시 고르세요.') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc


def review_external_proposal(project,proposal,req):
    image=suggestions._image_path(project,proposal['image_path']); source=_source_path(project)
    if proposal['task']!=project['task']: raise HTTPException(409,detail='작업 종류가 바뀌어 이 초안은 채택할 수 없습니다. 거절하세요.')
    studio=dataset_annotation_dir(image.parent,Path(project['annotations_dir']),use_scope=False)
    json_path=studio/f'{image.stem}.json';mask_path=studio/'masks'/f'{image.stem}.png'
    batch=labeling_tasks.read(project,proposal['batch_id']) if proposal.get('batch_id') else None
    # Either kind of running draft work (this phrase/SAM2 kind or the trained-model batch) blocks the adoption.
    if refusal:=suggestions.draft_work_refusal(project,'채택하세요'): raise HTTPException(409,detail=refusal)
    if proposal.get('labelset_id',project.get('active_labelset_id','default'))!=project.get('active_labelset_id','default'):
        raise HTTPException(409,detail='라벨 세트가 바뀌어 이 초안은 채택할 수 없습니다. 거절하세요.')
    expected_fingerprint=batch['review_fingerprint'] if batch else proposal['dataset_fingerprint']
    if (suggestions._sha256(image)!=proposal['image_sha256'] or suggestions._sha256(json_path)!=proposal['studio_sha256']
            or suggestions._sha256(image.with_suffix('.json'))!=proposal['labelme_sha256'] or suggestions._dataset_fingerprint(project)!=expected_fingerprint):
        raise HTTPException(409,detail='초안을 만든 뒤 데이터가 바뀌어 이 초안은 채택할 수 없습니다. 이 초안은 거절하고 초안을 새로 만드세요.')
    if proposal['backend']=='template_match':
        if suggestions._sha256(suggestions._image_path(project,proposal['exemplar_path']))!=proposal['exemplar_sha256']: raise HTTPException(409,detail='예시(템플릿) 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
    elif proposal['backend']=='vlm':
        if _setup(project).get('vlm')!=proposal['vlm_setup']:raise HTTPException(409,detail='VLM 설정이 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
        for example in proposal['positive_examples']+proposal['negative_examples']:
            if suggestions._sha256(suggestions._image_path(project,example['image_path']))!=example['sha256']:raise HTTPException(409,detail='VLM 예시 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
    elif proposal['backend']=='foundation':
        if _setup(project)!=proposal['foundation_setup']: raise HTTPException(409,detail='모델 보조 설정이 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
        for example in proposal['positive_examples']+proposal['negative_examples']:
            if suggestions._sha256(suggestions._image_path(project,example['image_path']))!=example['sha256']: raise HTTPException(409,detail='예시 이미지가 바뀌어 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
        _verify_provider_provenance(proposal)
        if proposal.get('suggestion_model_id'):
            from backend.engine.feature_labeling_jobs import load_feature_model
            try:
                if load_feature_model(project,proposal['suggestion_model_id'])['checkpoint_sha256']!=proposal['suggestion_checkpoint_sha256']:
                    raise ValueError('Trained feature model checkpoint changed')
            except (OSError,ValueError) as exc:raise HTTPException(409,detail='특징 모델이 바뀌거나 없어져 이 초안은 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.') from exc
    elif model_directory_hash(proposal['model_dir'])!=proposal['checkpoint_sha256']: raise HTTPException(409,detail='초안을 만든 모델 파일이 바뀌어 채택할 수 없습니다. 거절하고 초안을 새로 만드세요.')
    selected=set(req.candidate_ids);available={c['id'] for c in proposal['candidates']}
    if not selected or not selected<=available: raise HTTPException(422,detail='채택할 후보가 없습니다. 후보 없는 초안은 거절하세요.')
    from backend.api.routes_training import training_job_manager
    if training_job_manager.get_active_job() is not None: raise HTTPException(409,detail='학습이 진행 중입니다. 학습이 끝나거나 멈춘 뒤 채택하세요.')
    existing=routes_annotation.get_annotations(image.stem,file_path=str(image)).get('annotations',[])
    current=metadata_for_path(Path(project['project_dir']),source,image,Path(project['annotations_dir']))
    if suggestions._changed_since_draft(current,proposal): raise HTTPException(409,detail='다른 검토자가 이 이미지를 바꿨습니다. 이 초안은 거절하고 초안을 새로 만드세요.')
    backup=_snapshot(project,source,f'후보 채택 전 · {image.name}',req.actor,'auto_backup')
    previous_json=json_path.read_bytes() if json_path.exists() else None; previous_mask=mask_path.read_bytes() if mask_path.exists() else None
    try:
        # Each adopted shape keeps its origin (draft, provider, reviewer, backup) so one adoption can be taken back.
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id=image.stem,image_path=str(image),
            annotations=[*existing,*suggestions.adopted_candidates(proposal,selected,req.actor,backup['id'])],
            image_width=proposal['image_width'],image_height=proposal['image_height'],expected_revision=current['revision'],actor=req.actor,lease_token=req.lease_token))
        proposal.update(status='accepted',reviewer=req.actor,reviewed_at=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
                        accepted_candidate_ids=sorted(selected),backup_version_id=backup['id'],
                        adopted_annotations=suggestions.saved_adoption(image,selected))
        _write_json(suggestions._proposal_path(project,proposal['id']),proposal)
        if batch:
            batch['review_fingerprint']=suggestions._dataset_fingerprint(project)
            labeling_tasks.write(project,batch)
    except Exception:
        suggestions._restore_bytes(json_path,previous_json);suggestions._restore_bytes(mask_path,previous_mask);raise
    return proposal

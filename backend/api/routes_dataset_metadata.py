"""Review metadata, split leakage and annotation interoperability endpoints."""
from __future__ import annotations
import hashlib
import io
import json
import os
import uuid
import zipfile
from pathlib import Path
from typing import Any, Literal, Optional
from fastapi import APIRouter,HTTPException,Query,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from backend.api.routes_project import get_current_project
from backend.api.routes_dataset_versions import _source_path,_snapshot,_VERSION_LOCK
from backend.api import routes_annotation,routes_dataset
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_formats import import_annotations,export_annotations,bundle_files,safe_name
from backend.engine.annotation_storage import dataset_annotation_dir,set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root
from backend.engine.dataset_loaders import set_request_split_root,reset_request_split_root
from backend.engine.source_text import read_source_text

router=APIRouter(prefix='/api/dataset/metadata',tags=['dataset-review'])
format_router=APIRouter(prefix='/api/dataset/formats',tags=['annotation-formats'])

class EditRequest(BaseModel):
    expected_revision:int=Field(...,ge=1)
    actor:str=Field(...,min_length=1,max_length=100)
    changes:dict[str,Any]

class SplitRequest(BaseModel):
    group_by:list[Literal['product','lot','group']]=Field(...,min_length=1)
    train_ratio:float=Field(.7,ge=0,le=1)
    val_ratio:float=Field(.2,ge=0,le=1)
    test_ratio:float=Field(.1,ge=0,le=1)
    seed:int=42
    apply:bool=False
    actor:str='operator'

class ExchangeRequest(BaseModel):
    format:Literal['labelme','coco','yolo']
    folder_path:Optional[str]=None
    payload:Optional[Any]=None
    import_dir:Optional[str]=None
    mode:Literal['preview','apply']='preview'
    conflict_policy:Literal['reject','replace','merge']='reject'
    expected_revisions:dict[str,int]=Field(default_factory=dict)
    actor:str=Field('operator',min_length=1,max_length=100)


class BulkItem(BaseModel):
    image_uuid:str
    expected_revision:int=Field(...,ge=1)

class BulkMetadataRequest(BaseModel):
    actor:str=Field(...,min_length=1,max_length=100)
    items:list[BulkItem]=Field(...,min_length=1,max_length=5000)
    changes:dict[str,Any]


def _context(request,folder_path=None):
    project=get_current_project(request); source=_source_path(project,folder_path)
    return project,source

def _rows(project,source):
    from backend.engine.grouped_dataset_views import source_image_paths
    allowed={str(p) for p in source_image_paths(source,project['task'],include_unused=True)}
    return [r for r in dm.list_metadata(Path(project['project_dir']),source,Path(project['annotations_dir'])) if r['file_path'] in allowed]

def _errors(exc):
    if isinstance(exc,dm.RevisionConflict): return HTTPException(409,detail={'message':str(exc),'current':exc.current})
    if isinstance(exc,KeyError): return HTTPException(404,detail='Image not found in current project')
    return HTTPException(422,detail=str(exc))

@router.get('/statistics')
def dataset_statistics(request:Request,folder_path:Optional[str]=None):
    project,source=_context(request,folder_path)
    from backend.engine.dataset_summary import dataset_summary
    try:
        metadata={r['file_path']:r for r in _rows(project,source)}
        result=dataset_summary(source,project['task'],assignments=routes_dataset._read_split_manifest(source),metadata=metadata)
        result['labelset_id']=project.get('active_labelset_id','default')
        return result
    except (ValueError,OSError) as exc:
        raise _errors(exc) from exc

@router.get('')
def list_review_metadata(request:Request,folder_path:Optional[str]=None,state:Optional[str]=None,
                         product:Optional[str]=None,lot:Optional[str]=None,group:Optional[str]=None,tag:Optional[str]=None,
                         offset:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=5000)):
    project,source=_context(request,folder_path)
    try: rows=_rows(project,source)
    except (ValueError,OSError) as exc: raise _errors(exc) from exc
    for key,value in [('workflow_state',state),('product',product),('lot',lot),('group',group)]:
        if value is not None: rows=[r for r in rows if r[key]==value]
    if tag: rows=[r for r in rows if tag in r['tags']]
    return {'items':rows[offset:offset+limit],'total':len(rows),'offset':offset,'limit':limit}

@router.get('/image')
def get_image_metadata(request:Request,image_path:str):
    project,source=_context(request)
    try: return dm.metadata_for_path(Path(project['project_dir']),source,Path(image_path),Path(project['annotations_dir']))
    except (ValueError,OSError) as exc: raise _errors(exc) from exc

@router.post('/bulk')
def bulk_edit_metadata(req:BulkMetadataRequest,request:Request):
    project,source=_context(request)
    if not req.changes or set(req.changes)-{'tags','product','lot','group','usage_state'}:
        raise HTTPException(422,detail='Bulk edits support tags, product, lot and group; approval is per image')
    if len({item.image_uuid for item in req.items})!=len(req.items): raise HTTPException(422,detail='Duplicate selected image')
    try:
        with dm.metadata_transaction(Path(project['project_dir']),source,Path(project['annotations_dir'])):
            items=[dm.update_metadata(Path(project['project_dir']),source,item.image_uuid,item.expected_revision,req.actor,req.changes,Path(project['annotations_dir'])) for item in req.items]
        return {'items':items,'updated':len(items)}
    except (ValueError,KeyError,OSError) as exc: raise _errors(exc) from exc

@router.patch('/{image_uuid}')
def edit_image_metadata(image_uuid:str,req:EditRequest,request:Request):
    project,source=_context(request)
    try: return dm.update_metadata(Path(project['project_dir']),source,image_uuid,req.expected_revision,req.actor,req.changes,Path(project['annotations_dir']))
    except (ValueError,KeyError,OSError) as exc: raise _errors(exc) from exc

@router.get('/duplicates')
def duplicates(request:Request):
    project,source=_context(request)
    split_path=Path(project['dataset_dir'])/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
    assignments=json.loads(split_path.read_text(encoding='utf-8')).get('assignments',{}) if split_path.is_file() else {}
    # Structural train/val/test directories are also inspected before a manifest exists.
    for row in _rows(project,source):
        assignments.setdefault(row['relative_path'],next((p for p in Path(row['relative_path']).parts[:-1] if p in {'train','val','test'}),'unassigned'))
    return {'duplicates':dm.duplicate_leakage(_rows(project,source),assignments)}

@router.post('/split')
def grouped_split(req:SplitRequest,request:Request):
    project,source=_context(request)
    rows=[r for r in _rows(project,source) if r.get('usage_state','active')!='not_used']
    # Use only trainable image inventory for LabelMe; do not mark unlabeled files trainable.
    flat=routes_dataset._has_flat_labelme_annotations(source)
    if flat and project['task'] in {'detection','segmentation'}:
        paired=routes_dataset._paired_labelme_images(source)
        rows=[r for r in rows if Path(r['file_path']).resolve() in paired]
    if project['task'] in {'anomaly','anomaly_detection'}:
        from backend.engine.grouped_dataset_views import is_anomaly_normal
        rows=[{**r,'train_eligible':is_anomaly_normal(r['file_path'],source)} for r in rows]
    try: preview=dm.preview_split(rows,req.group_by,req.train_ratio,req.val_ratio,req.test_ratio,req.seed)
    except ValueError as exc: raise _errors(exc) from exc
    preview['applied']=False
    supported,reason=True,None
    preview.update(apply_supported=supported,apply_unavailable_reason=reason)
    if req.apply:
        if not supported: raise HTTPException(422,detail=reason)
        from backend.api.routes_training import training_job_manager
        if training_job_manager.get_active_job() is not None: raise HTTPException(409,detail='학습이 끝난 뒤 분할을 적용하세요.')
        token=set_request_split_root(Path(project['dataset_dir'])/'splits')
        try:
            with _VERSION_LOCK:
                backup=_snapshot(project,source,'그룹 분할 전 자동 백업',f'{req.actor}: {req.group_by}','auto_backup')
                path=routes_dataset._split_manifest_file(source)
                previous=path.read_bytes() if path.is_file() else None
                try:
                    routes_dataset._write_split_manifest(source,preview['assignments'],req.seed)
                    from backend.engine.grouped_dataset_views import load_manifest_dataset
                    if project['task'] != 'classification' and not flat:
                        for partition,count in preview['split'].items():
                            if count:
                                dataset=load_manifest_dataset(project['task'],source,partition)
                                if dataset is None or len(dataset)!=count: raise ValueError('Saved split does not match actual task inputs')
                except Exception as exc:
                    if previous is None:path.unlink(missing_ok=True)
                    else:path.write_bytes(previous)
                    if isinstance(exc,ValueError):raise HTTPException(422,detail=str(exc)) from exc
                    raise
            preview.update(applied=True,backup_version_id=backup['id'])
        finally: reset_request_split_root(token)
    return preview


def _directory_payload(req,source,inventory):
    if req.payload is not None: return req.payload
    if not req.import_dir: raise ValueError('Choose annotation folder or provide a format document')
    root=Path(req.import_dir).expanduser().resolve()
    if not root.is_dir(): raise ValueError('Annotation folder does not exist')
    paths=[p for p in root.rglob('*') if p.is_file()]
    if len(paths)>10000: raise ValueError('Import folder exceeds 10000 files')
    if any(p.is_symlink() or not p.resolve().is_relative_to(root) for p in paths): raise ValueError('Import folder contains unsafe links')
    if sum(p.stat().st_size for p in paths)>128*1024*1024: raise ValueError('Import folder exceeds 128 MB')
    if req.format=='labelme':
        documents=[]
        for path in paths:
            if path.suffix!='.json': continue
            doc=json.loads(read_source_text(path))
            if not isinstance(doc,dict) or 'shapes' not in doc: continue
            name=doc.get('imagePath') or path.with_suffix('.png').relative_to(root).as_posix()
            safe_name(name)
            # Standard LabelMe basename paths are relative to each JSON file.
            local_name=(path.parent.relative_to(root)/name).as_posix()
            inventory_names={r['relative_path'] for r in inventory}
            if Path(name).name==name and local_name in inventory_names:
                name=local_name
            doc['imagePath']=name; documents.append(doc)
        if not documents: raise ValueError('No LabelMe documents found')
        return {'documents':documents}
    if req.format=='coco':
        candidates=[]
        for path in paths:
            if path.suffix=='.json':
                doc=json.loads(read_source_text(path))
                if isinstance(doc,dict) and {'images','categories','annotations'}<=set(doc): candidates.append(doc)
        if len(candidates)!=1: raise ValueError('Select a folder containing exactly one COCO annotation document')
        return candidates[0]
    classes_path=root/'classes.txt'
    if not classes_path.exists(): raise ValueError('YOLO import requires classes.txt')
    classes=read_source_text(classes_path).splitlines()
    manifest=root/'image_manifest.json'
    images=json.loads(read_source_text(manifest)) if manifest.exists() else [
        {'file_name':r['relative_path'],'width':r['width'],'height':r['height']} for r in inventory]
    labels={p.relative_to(root).as_posix():read_source_text(p) for p in paths if p.suffix=='.txt' and p.name!='classes.txt'}
    # Standard labels/ tree may be separate from images/; bind explicit image names.
    for image in images:
        target=str(Path(image['file_name']).with_suffix('.txt'))
        alternative=target.replace('images/','labels/',1)
        if target not in labels and alternative in labels: labels[target]=labels[alternative]
    return {'classes':classes,'images':images,'labels':labels}

@format_router.post('/import')
def import_format(req:ExchangeRequest,request:Request):
    from backend.api.shared_authorization import request_actor
    req=req.model_copy(update={'actor':request_actor(request,req.actor)})
    project,source=_context(request,req.folder_path)
    all_rows=_rows(project,source)
    try: imported=import_annotations(_directory_payload(req,source,all_rows),req.format)
    except (ValueError,KeyError,TypeError,OSError) as exc: raise _errors(exc) from exc
    inventory={r['relative_path']:r for r in all_rows}
    preview=[]
    a=set_request_annotation_root(Path(project['annotations_dir'])); p=set_request_project_root(Path(project['project_dir']))
    s=set_request_split_root(Path(project['dataset_dir'])/'splits')
    try:
        for row in imported:
            name=safe_name(row['file_name']); meta=inventory.get(name)
            if meta is None: raise HTTPException(422,detail=f'Imported image is absent in selected source: {name}')
            if (row['width'],row['height'])!=(meta['width'],meta['height']): raise HTTPException(422,detail=f'Image dimensions differ: {name}')
            existing=routes_annotation.get_annotations(Path(name).stem,file_path=meta['file_path']).get('annotations',[])
            preview.append({'file_name':name,'image_uuid':meta['image_uuid'],'revision':meta['revision'],'existing_count':len(existing),
                            'incoming_count':len(row['annotations']),'conflict':bool(existing),'annotations':row['annotations']})
        if req.mode=='preview': return {'preview':preview,'applied':False,'format':req.format}
        if req.conflict_policy=='reject' and any(r['conflict'] for r in preview): raise HTTPException(409,detail='Existing labels conflict. Preview and choose merge or replace.')
        # No import may replace an annotation read by another operator since preview.
        for row in preview:
            if req.expected_revisions.get(row['image_uuid'])!=row['revision']: raise HTTPException(409,detail=f"Preview is outdated or missing: {row['file_name']}")
        with _VERSION_LOCK, dm.metadata_transaction(Path(project['project_dir']),source,Path(project['annotations_dir'])) as ledger:
            for row in preview:
                meta=inventory[row['file_name']]
                current=dm._ensure(ledger,Path(project['project_dir']),source,meta['file_path'],Path(project['annotations_dir']))
                if current['revision']!=row['revision']: raise HTTPException(409,detail=f"Image changed while importing: {row['file_name']}")
            backup=_snapshot(project,source,f'{req.format.upper()} 라벨 가져오기 전',req.actor,'auto_backup')
            previous={}
            for row in preview:
                meta=inventory[row['file_name']]
                studio=dataset_annotation_dir(Path(meta['file_path']).parent,Path(project['annotations_dir']),use_scope=False)
                for path in [studio/f"{Path(row['file_name']).stem}.json",studio/'masks'/f"{Path(row['file_name']).stem}.png"]:
                    previous[path]=path.read_bytes() if path.exists() else None
            try:
                for row in preview:
                    meta=inventory[row['file_name']]; existing=routes_annotation.get_annotations(Path(row['file_name']).stem,file_path=meta['file_path']).get('annotations',[])
                    annotations=[*existing,*row['annotations']] if req.conflict_policy=='merge' else row['annotations']
                    routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id=Path(row['file_name']).stem,image_path=meta['file_path'],annotations=annotations,
                        image_width=meta['width'],image_height=meta['height'],expected_revision=row['revision'],actor=req.actor),request)
            except Exception:
                for path,value in previous.items():
                    if value is None: path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
                raise
        return {'preview':preview,'applied':True,'backup_version_id':backup['id'],'format':req.format}
    finally: reset_request_project_root(p);reset_request_annotation_root(a);reset_request_split_root(s)

@format_router.post('/export')
def export_format(req:ExchangeRequest,request:Request):
    project,source=_context(request,req.folder_path); images=[]
    a=set_request_annotation_root(Path(project['annotations_dir'])); p=set_request_project_root(Path(project['project_dir']))
    try:
        for meta in _rows(project,source):
            current=routes_annotation.get_annotations(Path(meta['relative_path']).stem,file_path=meta['file_path'])
            images.append({'file_name':meta['relative_path'],'width':meta['width'],'height':meta['height'],'annotations':current.get('annotations',[])})
        payload=export_annotations(images,req.format); files=bundle_files(payload,req.format)
        export_id=f'annotations_{uuid.uuid4().hex}'
        directory=Path(project['project_dir'])/'exports'; directory.mkdir(exist_ok=True)
        output=directory/f'{export_id}.zip'
        with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
            for name,value in files.items(): archive.writestr(safe_name(name),value)
        return {'format':req.format,'image_count':len(images),'annotation_count':sum(len(r['annotations']) for r in images),
                'payload':payload,'download_url':f'/api/dataset/formats/download/{export_id}'}
    except (ValueError,KeyError,TypeError,OSError) as exc: raise _errors(exc) from exc
    finally: reset_request_project_root(p);reset_request_annotation_root(a)

@format_router.get('/download/{export_id}')
def download_format(export_id:str,request:Request):
    if not export_id.startswith('annotations_') or len(export_id)!=44 or any(c not in '0123456789abcdef' for c in export_id[12:]): raise HTTPException(422,detail='Invalid export ID')
    project=get_current_project(request); path=Path(project['project_dir'])/'exports'/f'{export_id}.zip'
    if not path.is_file() or path.is_symlink(): raise HTTPException(404,detail='Export not found')
    return FileResponse(path,media_type='application/zip',filename=f'{export_id}.zip')

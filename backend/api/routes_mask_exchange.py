"""Preview, revision-checked import and pixel-exact mask export."""
from pathlib import Path
import hashlib
import uuid
import zipfile
import numpy as np
from PIL import Image
from fastapi import APIRouter,HTTPException,Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from typing import Literal
from backend.api import routes_dataset_metadata as exchange,routes_annotation
from backend.api.routes_dataset_versions import _VERSION_LOCK,_snapshot
from backend.engine import dataset_metadata as dm
from backend.engine.mask_exchange import load_mask_bundle,build_mask_bundle
from backend.engine.annotation_storage import dataset_annotation_dir,set_request_annotation_root,reset_request_annotation_root,set_request_project_root,reset_request_project_root

router=APIRouter(prefix='/api/dataset/masks',tags=['mask-exchange'])

class MaskImportRequest(BaseModel):
    import_dir:str
    mode:Literal['preview','apply']='preview'
    conflict_policy:Literal['reject','replace','merge']='reject'
    actor:str=Field('operator',min_length=1,max_length=100)
    expected_revisions:dict[str,int]=Field(default_factory=dict)
    expected_manifest_sha256:str|None=None

@router.post('/import')
def import_masks(req:MaskImportRequest,request:Request):
    from backend.api.shared_authorization import request_actor
    req=req.model_copy(update={'actor':request_actor(request,req.actor)})
    project,source=exchange._context(request);inventory={r['relative_path']:r for r in exchange._rows(project,source)}
    a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:
        rows=load_mask_bundle(req.import_dir);preview=[]
        signature=hashlib.sha256((''.join(r['manifest_sha256']+r['mask_sha256'] for r in rows)).encode()).hexdigest()
        if req.expected_manifest_sha256 and signature!=req.expected_manifest_sha256:raise HTTPException(409,detail='Mask files changed since preview; preview again')
        for row in rows:
            meta=inventory.get(row['file_name'])
            if meta is None:raise ValueError(f"Mask source image absent from selected dataset: {row['file_name']}")
            if (row['width'],row['height'])!=(meta['width'],meta['height']):raise ValueError('Mask source dimensions differ')
            if row.get('source_sha256') and hashlib.sha256(Path(meta['file_path']).read_bytes()).hexdigest()!=row['source_sha256']:raise ValueError('Original image content differs from mask source manifest')
            existing=routes_annotation.get_annotations(Path(row['file_name']).stem,file_path=meta['file_path']).get('annotations',[])
            preview.append({'file_name':row['file_name'],'image_uuid':meta['image_uuid'],'revision':meta['revision'],
                'existing_count':len(existing),'incoming_count':len(row['annotations']),'conflict':bool(existing),'classes':row['classes'],
                'preview_masks':[{'label':ann['label'],'color':ann['color'],'mask_rle':ann['mask_rle']} for ann in row['annotations']]})
        if req.mode=='preview':return {'preview':preview,'applied':False,'manifest_sha256':signature}
        if req.conflict_policy=='reject' and any(r['conflict'] for r in preview):raise HTTPException(409,detail='Existing labels conflict; choose merge or replace after preview')
        for row in preview:
            if req.expected_revisions.get(row['image_uuid'])!=row['revision']:raise HTTPException(409,detail='Preview revision is missing or stale')
        with _VERSION_LOCK,dm.metadata_transaction(Path(project['project_dir']),source,Path(project['annotations_dir'])) as ledger:
            for row in preview:
                meta=inventory[row['file_name']];current=dm._ensure(ledger,Path(project['project_dir']),source,meta['file_path'],Path(project['annotations_dir']))
                if current['revision']!=row['revision']:raise HTTPException(409,detail='Image revision changed during mask import')
            backup=_snapshot(project,source,'외부 mask 라벨 가져오기 전',req.actor,'auto_backup');previous={}
            for row in rows:
                meta=inventory[row['file_name']];studio=dataset_annotation_dir(Path(meta['file_path']).parent,Path(project['annotations_dir']),use_scope=False)
                for path in [studio/f"{Path(row['file_name']).stem}.json",studio/'masks'/f"{Path(row['file_name']).stem}.png"]:previous[path]=path.read_bytes() if path.exists() else None
            try:
                for row,receipt in zip(rows,preview):
                    meta=inventory[row['file_name']];existing=routes_annotation.get_annotations(Path(row['file_name']).stem,file_path=meta['file_path']).get('annotations',[])
                    if req.conflict_policy=='merge':
                        mapping={ann.get('category_id') or 1:ann['label'] for ann in existing if ann['type']!='tag'}
                        if any(ann['category_id'] in mapping and mapping[ann['category_id']]!=ann['label'] for ann in row['annotations']):raise ValueError('Merge mask class IDs conflict with existing class names')
                    annotations=[*existing,*row['annotations']] if req.conflict_policy=='merge' else row['annotations']
                    routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(image_id=Path(row['file_name']).stem,image_path=meta['file_path'],annotations=annotations,image_width=meta['width'],image_height=meta['height'],expected_revision=receipt['revision'],actor=req.actor,mask_classes=row['classes']),request)
            except Exception:
                for path,value in previous.items():
                    if value is None:path.unlink(missing_ok=True)
                    else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(value)
                raise
        return {'preview':preview,'applied':True,'backup_version_id':backup['id'],'manifest_sha256':signature}
    except (ValueError,OSError,KeyError,TypeError) as exc:raise exchange._errors(exc) from exc
    finally:reset_request_project_root(p);reset_request_annotation_root(a)

class MaskExportRequest(BaseModel):
    include_originals:bool=True

@router.post('/export')
def export_masks(req:MaskExportRequest,request:Request):
    project,source=exchange._context(request);a=set_request_annotation_root(Path(project['annotations_dir']));p=set_request_project_root(Path(project['project_dir']))
    try:
        rows=[]
        for meta in exchange._rows(project,source):
            current=routes_annotation.get_annotations(Path(meta['file_path']).stem,file_path=meta['file_path'])
            row={'file_name':meta['relative_path'],'width':meta['width'],'height':meta['height'],'annotations':current.get('annotations',[])}
            if current.get('mask_classes'): row['classes']=current['mask_classes']
            if current.get('mask_file'):
                mask=dataset_annotation_dir(Path(meta['file_path']).parent,Path(project['annotations_dir']),use_scope=False)/'masks'/f"{Path(meta['file_path']).stem}.png"
                with Image.open(mask) as image:row['mask_pixels']=np.asarray(image).copy()
            rows.append(row)
        files=build_mask_bundle(rows,source,req.include_originals);export_id='masks_'+uuid.uuid4().hex
        directory=Path(project['project_dir'])/'exports';directory.mkdir(exist_ok=True)
        with zipfile.ZipFile(directory/f'{export_id}.zip','w',zipfile.ZIP_DEFLATED) as archive:
            for name,value in files.items():archive.writestr(name,value)
        return {'download_url':f'/api/dataset/masks/download/{export_id}','image_count':len(rows),'annotation_count':sum(len(r['annotations']) for r in rows),'include_originals':req.include_originals}
    except (ValueError,OSError,KeyError,TypeError) as exc:raise exchange._errors(exc) from exc
    finally:reset_request_project_root(p);reset_request_annotation_root(a)

@router.get('/download/{export_id}')
def download_masks(export_id:str,request:Request):
    import re
    if not re.fullmatch('masks_[0-9a-f]{32}',export_id):raise HTTPException(422,detail='Invalid mask export ID')
    project,_=exchange._context(request);path=Path(project['project_dir'])/'exports'/f'{export_id}.zip'
    if not path.is_file() or path.is_symlink():raise HTTPException(404,detail='Mask export not found')
    return FileResponse(path,media_type='application/zip',filename=f'{export_id}.zip')

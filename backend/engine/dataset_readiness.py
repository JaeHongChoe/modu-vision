"""Frozen grouping qualification and task schema diagnostics; no source writes."""
from pathlib import Path
import hashlib,json
import cv2
import numpy as np
from PIL import Image
from backend.engine.grouped_dataset_views import _annotations,_paired_mask,NORMAL_NAMES
from backend.engine.annotation_formats import _shape
from backend.engine.annotation_storage import scoped_annotation_root
from backend.engine.dataset_inventory import folder_label_split

def split_qualification(rows,group_by,seed,project,ratios=None):
    fields=('relative_path','content_hash','annotation_hash','mask_hash','product','lot','group','revision','usage_state','train_eligible')
    members=[{key:row.get(key) for key in fields} for row in sorted(rows,key=lambda row:row['relative_path'])]
    value={'schema_version':1,'task':project['task'],'labelset_id':project.get('active_labelset_id','default'),'group_by':list(group_by),'seed':seed,'ratios':ratios,'members':members,'mandatory_unions':['content_hash','nonempty_common_original_group'],'unknown_lineage':'Blank group does not prove an independent original; declare the same group for external crops/derived/synthetic variants.'}
    value['sha256']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return value

def split_staleness(saved,rows,project):
    return saved.get('sha256')!=split_qualification(rows,saved.get('group_by',[]),saved.get('seed'),project,saved.get('ratios'))['sha256']

def task_schema(source,task,rows):
    source=Path(source).resolve();items=[]
    for row in rows:
        error=None
        try:
            width,height=row.get('width'),row.get('height')
            if not width or not height:raise ValueError('Image cannot be decoded')
            image=Path(row['file_path'])
            if task=='classification':
                label,_=folder_label_split(Path(row['relative_path']),task)
                annotations,_=_annotations(source,image)
                if not label and not any(a.get('type')=='tag' and a.get('label') for a in annotations or []):raise ValueError('Classification class is missing')
            elif task in {'detection','segmentation'}:
                annotations,overlay=_annotations(source,image)
                mask=Path(overlay) if overlay else (_paired_mask(source,image) if task=='segmentation' else None)
                if annotations is None and mask is None:raise ValueError('Task annotation/mask is missing')
                normal=[a for a in annotations or [] if a.get('type')=='tag' and (a.get('is_normal') or str(a.get('label','')).casefold() in NORMAL_NAMES)]
                other=[a for a in annotations or [] if a not in normal]
                if normal and other:raise ValueError('Normal mark conflicts with object annotations')
                for shape in other:
                    if shape.get('type')=='tag':raise ValueError('Object task cannot use a classification tag')
                    if shape.get('type')=='brush_mask':
                        if task!='segmentation' or mask is None:raise ValueError('Brush region requires its saved class mask')
                    else:
                        checked=_shape(shape,width,height)
                        if checked['type']=='rotated_bbox':
                            cx,cy,bw,bh,angle=checked['rotated_bbox'];corners=cv2.boxPoints(((cx,cy),(bw,bh),angle))
                            if np.any(corners<-.001) or np.any(corners[:,0]>width+.001) or np.any(corners[:,1]>height+.001):raise ValueError('Rotated box outside image')
                if mask is not None:
                    allowed=(source,scoped_annotation_root(source/'.annotations').resolve())
                    if mask.is_symlink() or not any(mask.resolve().is_relative_to(root) for root in allowed):raise ValueError('Mask is outside active annotation/source scope')
                    if any(parent.is_symlink() for parent in mask.parents if parent not in allowed):raise ValueError('Mask path contains symbolic links')
                    with Image.open(mask) as raster:
                        pixels=np.asarray(raster)
                        if pixels.ndim!=2 or raster.size!=(width,height):raise ValueError('Mask must match source size with single-channel class IDs')
                    if normal and np.any(pixels):raise ValueError('Normal mark conflicts with mask regions')
            elif task not in {'anomaly','anomaly_detection'}:raise ValueError('Unsupported task schema')
        except (ValueError,OSError,KeyError,TypeError) as exc:error=str(exc)
        items.append({'relative_path':row['relative_path'],'valid':error is None,'error':error})
    return {'task':task,'valid_count':sum(r['valid'] for r in items),'invalid_count':sum(not r['valid'] for r in items),'items':items,'limits':'Schema/geometry consistency only; labels and common-original metadata still need human review.'}

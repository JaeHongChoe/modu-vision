"""Read-only diagnostics, immutable derived edits, and durable scoped review queues."""
from __future__ import annotations
import base64
import copy
import hashlib
import io
import json
import math
import os
import re
import shutil
import time
import uuid
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from backend.engine.dicom_input import open_source_image
from backend.engine.dataset_metadata import _file_lock


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()


def _source_image(source,path):
    source=Path(source).resolve();path=Path(path)
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source):
        raise ValueError('Image must be a regular file in the active source scope')
    return path.resolve()


def _storage(project,source):
    project=Path(project).resolve();key=hashlib.sha256(str(Path(source).resolve()).encode()).hexdigest()[:24]
    root=project/'dataset'/'data_workbench'/key
    for path in (project/'dataset',project/'dataset'/'data_workbench',root):
        if path.is_symlink():raise ValueError('Project data storage cannot contain symbolic links')
    root.mkdir(parents=True,exist_ok=True)
    return root


def _write(path,value,exclusive=False):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink():raise ValueError('Data state cannot be a symbolic link')
    if exclusive:
        with path.open('xb') as handle:handle.write(canonical(value));handle.flush();os.fsync(handle.fileno())
    else:
        temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex)
        try:
            with temporary.open('xb') as handle:handle.write(canonical(value));handle.flush();os.fsync(handle.fileno())
            os.replace(temporary,path)
        finally:temporary.unlink(missing_ok=True)


def diagnose(rows,assignments=None,*,blur_threshold=50,exposure_fraction=.9,near_distance=6):
    if not math.isfinite(blur_threshold) or blur_threshold<0 or not 0<exposure_fraction<=1 or not 0<=near_distance<=64:
        raise ValueError('Diagnostic thresholds are invalid')
    if len(rows)>5000:raise ValueError('Choose at most 5000 images per diagnostic run')
    assignments=assignments or {};items=[];hashes={};counts={k:0 for k in ('blur','underexposed','overexposed','unreadable','near_duplicate')}
    for row in rows:
        path=Path(row['file_path']);item={k:row[k] for k in ('image_uuid','file_path','relative_path','workflow_state','revision') if k in row}
        item.update(issues=[],split=assignments.get(row['relative_path'],'unassigned'))
        if path.is_symlink():raise ValueError('Diagnostic images cannot be symbolic links')
        digest=sha256(path);hashes[row['relative_path']]=digest;item['source_sha256']=digest
        try:
            with open_source_image(path) as image:pixels=np.asarray(image.convert('RGB'))
            gray=cv2.cvtColor(pixels,cv2.COLOR_RGB2GRAY);lap=float(cv2.Laplacian(gray,cv2.CV_64F).var())
            dark=float(np.mean(gray<=8));bright=float(np.mean(gray>=247))
            scaled=cv2.resize(gray,(32,32),interpolation=cv2.INTER_AREA).astype(np.float32)
            low=cv2.dct(scaled)[:8,:8].flatten();bits=low>np.median(low[1:]);bits[0]=False
            phash=sum(int(v)<<i for i,v in enumerate(bits))
            item.update(blur_score=lap,dark_fraction=dark,bright_fraction=bright,perceptual_hash=f'{phash:016x}')
            if lap<blur_threshold:item['issues'].append('blur')
            if dark>=exposure_fraction:item['issues'].append('underexposed')
            if bright>=exposure_fraction:item['issues'].append('overexposed')
        except (OSError,ValueError,cv2.error):item['issues'].append('unreadable')
        # Never report measurements against bytes changed while decoding.
        if sha256(path)!=digest:raise ValueError('Source image changed during diagnostics')
        items.append(item)
    near=[]
    for i,left in enumerate(items):
        if 'perceptual_hash' not in left:continue
        for right in items[i+1:]:
            if 'perceptual_hash' not in right:continue
            distance=(int(left['perceptual_hash'],16)^int(right['perceptual_hash'],16)).bit_count()
            if distance>near_distance:continue
            near.append({'images':[left['relative_path'],right['relative_path']],'distance':distance,'exact_bytes':left['source_sha256']==right['source_sha256'],
                         'splits':sorted({left['split'],right['split']}),'cross_split':left['split']!=right['split'] and 'unassigned' not in (left['split'],right['split'])})
            for item in (left,right):
                if 'near_duplicate' not in item['issues']:item['issues'].append('near_duplicate')
    for item in items:
        for issue in item['issues']:counts[issue]+=1
    return {'schema_version':1,'items':items,'issue_counts':counts,'near_duplicates':near,'source_sha256':hashes,
            'decision':'needs_review' if any(counts.values()) else 'ready','thresholds':{'blur_threshold':blur_threshold,'exposure_fraction':exposure_fraction,'near_distance':near_distance},
            'measurement_limits':'Laplacian variance and clipped grayscale exposure are diagnostics; perceptual DCT hash is a similarity hint requiring review.'}


def _numbers(values,length,name):
    if not isinstance(values,(list,tuple)) or len(values)!=length or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in values):
        raise ValueError(f'Invalid {name} geometry')
    return [float(v) for v in values]


def _clip_polygon(points,rect):
    x1,y1,x2,y2=rect;output=points
    for axis,bound,inside in [(0,x1,lambda v,b:v>=b),(0,x2,lambda v,b:v<=b),(1,y1,lambda v,b:v>=b),(1,y2,lambda v,b:v<=b)]:
        incoming=output;output=[]
        if not incoming:break
        previous=incoming[-1]
        for current in incoming:
            a,b=inside(previous[axis],bound),inside(current[axis],bound)
            if a!=b:
                ratio=(bound-previous[axis])/(current[axis]-previous[axis]);output.append([previous[j]+ratio*(current[j]-previous[j]) for j in range(2)])
            if b:output.append(current)
            previous=current
    return output


def edit_image_and_annotations(image,annotations,operation):
    """Pixel-edge coordinates; rotations are clockwise quarter turns only."""
    width,height=image.size;kind=operation.get('kind');rect=None
    if kind=='crop':
        rect=_numbers(operation.get('rect'),4,'crop');x1,y1,x2,y2=rect
        if any(v!=int(v) for v in rect) or not 0<=x1<x2<=width or not 0<=y1<y2<=height:raise ValueError('Crop must use integer bounds inside the image')
        transform=lambda p:[p[0]-x1,p[1]-y1];result=image.crop(tuple(map(int,rect)));angle=lambda v:v
        mask_edit=lambda im:im.crop(tuple(map(int,rect)))
    elif kind=='rotate':
        degrees=operation.get('degrees')
        if degrees not in (90,180,270):raise ValueError('Rotation supports clockwise 90, 180 or 270 degrees')
        if degrees==90:transform=lambda p:[height-p[1],p[0]];transpose=Image.Transpose.ROTATE_270
        elif degrees==180:transform=lambda p:[width-p[0],height-p[1]];transpose=Image.Transpose.ROTATE_180
        else:transform=lambda p:[p[1],width-p[0]];transpose=Image.Transpose.ROTATE_90
        result=image.transpose(transpose);mask_edit=lambda im:im.transpose(transpose);angle=lambda v,rotation=degrees:(v+rotation)%360
    elif kind=='flip':
        axis=operation.get('axis')
        if axis=='horizontal':transform=lambda p:[width-p[0],p[1]];transpose=Image.Transpose.FLIP_LEFT_RIGHT;angle=lambda v:(180-v)%360
        elif axis=='vertical':transform=lambda p:[p[0],height-p[1]];transpose=Image.Transpose.FLIP_TOP_BOTTOM;angle=lambda v:(-v)%360
        else:raise ValueError('Flip axis must be horizontal or vertical')
        result=image.transpose(transpose);mask_edit=lambda im:im.transpose(transpose)
    else:raise ValueError('Choose crop, rotate or flip')
    transformed=[];omitted=[]
    for annotation in annotations:
        row=copy.deepcopy(annotation);shape=row.get('type')
        if shape=='tag':transformed.append(row);continue
        if shape not in ('bbox','polygon','brush_mask','rotated_bbox'):raise ValueError('Unsupported annotation type; image edit stopped')
        if row.get('direction_deg') is not None:
            direction=_numbers([row['direction_deg']],1,'direction')[0]
            if not 0<=direction<360:raise ValueError('Object direction must be [0,360)')
            row['direction_deg']=angle(direction)
        points=None
        if shape=='bbox':
            bx1,by1,bx2,by2=_numbers(row.get('bbox'),4,'bbox')
            if not 0<=bx1<bx2<=width or not 0<=by1<by2<=height:raise ValueError('Invalid bbox bounds')
            points=[[bx1,by1],[bx2,by1],[bx2,by2],[bx1,by2]]
            if rect:points=_clip_polygon(points,rect)
        elif shape=='polygon':
            points=row.get('polygon') or row.get('points')
            if not isinstance(points,list) or len(points)<3:raise ValueError('Invalid polygon geometry')
            points=[_numbers(p,2,'polygon') for p in points]
            if any(not 0<=x<=width or not 0<=y<=height for x,y in points) or abs(cv2.contourArea(np.asarray(points,np.float32)))<=1e-8:raise ValueError('Invalid polygon bounds or zero area')
            if rect:points=_clip_polygon(points,rect)
        elif shape=='rotated_bbox':
            cx,cy,bw,bh,degrees=_numbers(row.get('rotated_bbox'),5,'rotated box')
            if bw<=0 or bh<=0:raise ValueError('Invalid rotated box size')
            rad=math.radians(degrees);co,si=math.cos(rad),math.sin(rad)
            corners=[[cx+x*co-y*si,cy+x*si+y*co] for x,y in [(-bw/2,-bh/2),(bw/2,-bh/2),(bw/2,bh/2),(-bw/2,bh/2)]]
            if any(x<-1e-7 or x>width+1e-7 or y<-1e-7 or y>height+1e-7 for x,y in corners):raise ValueError('Invalid rotated box bounds')
            if rect:
                clipped=_clip_polygon(corners,rect)
                if not clipped:omitted.append(row.get('id'));continue
                if any(not rect[0]<=x<=rect[2] or not rect[1]<=y<=rect[3] for x,y in corners):raise ValueError('Crop partly intersects a rotated box; convert it to polygon before cropping')
            center=transform([cx,cy]);row['rotated_bbox']=[*center,bw,bh,(angle(degrees)+90)%180-90]
            projected=[transform(p) for p in corners]
            row['bbox']=[min(p[0] for p in projected),min(p[1] for p in projected),max(p[0] for p in projected),max(p[1] for p in projected)]
            if row.get('polygon') is not None:row['polygon']=projected
            if row.get('points') is not None:row['points']=projected
        elif shape=='brush_mask':
            encoded=row.get('mask_rle','')
            try:
                prefix,body=encoded.split(',',1)
                if prefix!='data:image/png;base64' or len(body)>32_000_000:raise ValueError()
                with Image.open(io.BytesIO(base64.b64decode(body,validate=True))) as mask:
                    if mask.size!=(width,height) or mask.mode!='RGBA':raise ValueError()
                    edited=mask_edit(mask);array=np.asarray(edited)
                if not np.any(array[:,:,3]):omitted.append(row.get('id'));continue
                out=io.BytesIO();edited.save(out,format='PNG');row['mask_rle']='data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()
                ys,xs=np.nonzero(array[:,:,3]);row['bbox']=[int(xs.min()),int(ys.min()),int(xs.max()+1),int(ys.max()+1)]
            except (ValueError,OSError) as exc:raise ValueError('Invalid brush mask dimensions or PNG encoding') from exc
        if points is not None:
            if len(points)<3:omitted.append(row.get('id'));continue
            points=[transform(p) for p in points]
            if shape=='bbox':row['bbox']=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
            else:
                row['polygon']=points;row['points']=points
                row['bbox']=[min(p[0] for p in points),min(p[1] for p in points),max(p[0] for p in points),max(p[1] for p in points)]
        transformed.append(row)
    return result,transformed,omitted


def read_derived(project,source,identifier,scope=None):
    if not re.fullmatch(r'derived_[0-9a-f]{32}',identifier):raise ValueError('Invalid derived version ID')
    root=_storage(project,source);directory=root/'derived'/identifier;path=directory/'version.json'
    if directory.is_symlink() or path.is_symlink() or not path.is_file():raise ValueError('Derived version not found in active source scope')
    record=json.loads(path.read_text());digest=record.pop('evidence_sha256',None)
    if digest!=hashlib.sha256(canonical(record)).hexdigest():raise ValueError('Derived version integrity failure')
    record['evidence_sha256']=digest
    if scope is not None and record.get('scope')!=scope:raise ValueError('Derived version task or labelset scope changed')
    image=directory/'images'/'derived.png'
    if image.is_symlink() or not image.is_file() or sha256(image)!=record['derived_sha256']:raise ValueError('Derived image changed')
    original=_source_image(source,record['source_path'])
    if sha256(original)!=record['source_sha256']:raise ValueError('Original source changed; saved derived version is stale')
    return record


def derived_history(project,source,image_path,scope=None):
    image=_source_image(source,image_path);root=_storage(project,source)/'derived';rows=[]
    for path in root.glob('derived_*/version.json'):
        if path.is_symlink() or path.parent.is_symlink():raise ValueError('Derived version cannot be a symbolic link')
        hint=json.loads(path.read_text())
        if hint.get('source_path')!=str(image) or (scope is not None and hint.get('scope')!=scope):continue
        row=read_derived(project,source,path.parent.name)
        if row['source_path']==str(image) and (scope is None or row.get('scope')==scope):rows.append(row)
    return sorted(rows,key=lambda r:(r['created_at'],r['id']))


def derive(project,source,image_path,annotations,operation,actor,expected_sha256,parent_id=None,scope=None):
    original=_source_image(source,image_path);digest=sha256(original)
    if digest!=expected_sha256:raise ValueError('Original source changed; reload before editing')
    if not isinstance(actor,str) or not actor.strip() or len(actor)>100:raise ValueError('Enter editor name')
    parent=read_derived(project,source,parent_id,scope) if parent_id else None
    if parent and parent['source_path']!=str(original):raise ValueError('Parent version belongs to another source image')
    target=Path(parent['file_path']) if parent else original
    with open_source_image(target) as opened:
        result,transformed,omitted=edit_image_and_annotations(opened.convert('RGB'),parent['annotations'] if parent else annotations,operation)
    if sha256(original)!=digest:raise ValueError('Source changed during editing')
    identifier='derived_'+uuid.uuid4().hex;root=_storage(project,source)/'derived';root.mkdir(exist_ok=True)
    staging=root/('.'+identifier);directory=root/identifier
    try:
        (staging/'images').mkdir(parents=True);image=staging/'images'/'derived.png';result.save(image,format='PNG')
        record={'schema_version':1,'scope':scope,'id':identifier,'parent_id':parent_id,'source_path':str(original),'source_sha256':digest,
                'source_relative_path':original.relative_to(Path(source).resolve()).as_posix(),'created_at':time.time(),'actor':actor.strip(),
                'operation':operation,'size':list(result.size),'annotations':transformed,'omitted_annotation_ids':omitted,
                'derived_sha256':sha256(image),'file_path':str(directory/'images'/'derived.png'),'dataset_path':str(directory)}
        # Persist native annotations and a task-readable raster; brush masks stay lossless.
        _write(staging/'annotations.json',{'image_id':'derived','annotations':transformed,'image_width':result.width,'image_height':result.height})
        shapes=[];mask=np.zeros((result.height,result.width),np.uint8)
        for row in transformed:
            label=row.get('label','defect');cid=row.get('category_id') or 1;kind=row['type']
            if kind=='bbox':x1,y1,x2,y2=row['bbox'];points=[[x1,y1],[x2,y2]];shape='rectangle'
            elif kind=='polygon':points=row['polygon'];shape='polygon'
            elif kind=='rotated_bbox':
                from backend.engine.rotated_detection import _points
                cx,cy,bw,bh,angle=row['rotated_bbox'];points=_points({'cx':cx,'cy':cy,'width':bw,'height':bh,'angle_deg':angle}).tolist();shape='polygon'
            elif kind=='brush_mask':
                with Image.open(io.BytesIO(base64.b64decode(row['mask_rle'].split(',')[1]))) as decoded:mask[np.asarray(decoded)[:,:,3]>0]=cid
                continue
            elif kind=='tag':continue
            else:raise ValueError('Unsupported annotation export')
            flags={}
            if kind=='rotated_bbox':flags['studio_rotated_bbox']=row['rotated_bbox']
            if row.get('direction_deg') is not None:flags['studio_direction_deg']=row['direction_deg']
            shapes.append({'label':label,'points':points,'shape_type':shape,'flags':flags})
            contour=np.asarray(points if shape=='polygon' else [[points[0][0],points[0][1]],[points[1][0],points[0][1]],points[1],[points[0][0],points[1][1]]],np.int32)
            cv2.fillPoly(mask,[contour],int(cid))
        _write(staging/'images'/'derived.json',{'version':'5.0','imagePath':'derived.png','imageWidth':result.width,'imageHeight':result.height,'shapes':shapes,'flags':{},'derived_version_id':identifier})
        (staging/'masks').mkdir();Image.fromarray(mask).save(staging/'masks'/'derived.png')
        record['evidence_sha256']=hashlib.sha256(canonical(record)).hexdigest();_write(staging/'version.json',record)
        if sha256(original)!=digest:raise ValueError('Source changed during editing')
        os.rename(staging,directory)
        return record
    finally:
        if staging.exists():shutil.rmtree(staging)


def _queue_path(project,source,identifier):
    if not re.fullmatch(r'review_[0-9a-f]{32}',identifier):raise ValueError('Invalid review queue ID')
    return _storage(project,source)/'review_queues'/(identifier+'.json')


def create_review_queue(project,source,task,labelset_id,rows,origin,threshold=.5,margin=.05):
    if not 0<=threshold<=1 or not 0<=margin<=1:raise ValueError('Review threshold and margin must be [0,1]')
    if origin.get('step')!=4 or not (origin.get('evaluation_id') or origin.get('comparison_id')):raise ValueError('Review queue requires an originating saved evaluation or comparison')
    items=[];seen=set()
    for row in rows:
        path=row.get('file_path') or row.get('source_image')
        if not path:continue
        image=_source_image(source,path);relative=image.relative_to(Path(source).resolve()).as_posix()
        if relative in seen:continue
        seen.add(relative);reasons=[];digest=sha256(image)
        recorded=row.get('image_sha256') or row.get('source_sha256') or row.get('content_hash')
        if recorded and recorded!=digest:raise ValueError('Saved evaluation source image changed; evaluate again before creating a queue')
        candidate=row.get('candidate') or {};incumbent=row.get('incumbent') or {};truth=row.get('ground_truth_verdict')
        if row.get('error') or row.get('is_correct') is False or candidate.get('error') or incumbent.get('error') or (truth in ('OK','NG') and candidate.get('verdict') in ('OK','NG') and truth!=candidate['verdict']):reasons.append('error')
        if row.get('disagreement') or row.get('models_disagree') or row.get('disagrees'):reasons.append('disagreement')
        score=row.get('defect_score',row.get('confidence',candidate.get('max_defect_score')))
        if isinstance(score,(int,float)) and math.isfinite(score) and abs(score-threshold)<=margin:reasons.append('threshold')
        if not reasons:continue
        rank=(300 if 'error' in reasons else 0)+(200 if 'disagreement' in reasons else 0)+(100 if 'threshold' in reasons else 0)
        items.append({'relative_path':relative,'file_path':str(image),'source_sha256':digest,'reasons':reasons,'priority':rank,'state':'pending'})
    items.sort(key=lambda r:(-r['priority'],r['relative_path']))
    queue={'schema_version':1,'id':'review_'+uuid.uuid4().hex,'scope':{'source':str(Path(source).resolve()),'task':task,'labelset_id':labelset_id},'revision':1,'created_at':time.time(),'items':items,'cursor':0,'origin':origin,'threshold':threshold,'margin':margin,'history':[]}
    _write(_queue_path(project,source,queue['id']),queue,True);return queue


def read_review_queue(project,source,task,labelset_id,identifier):
    path=_queue_path(project,source,identifier)
    if path.is_symlink() or not path.is_file():raise ValueError('Review queue not found in active source scope')
    queue=json.loads(path.read_text())
    if queue.get('scope')!={'source':str(Path(source).resolve()),'task':task,'labelset_id':labelset_id}:raise ValueError('Review queue scope changed')
    for row in queue['items']:
        image=_source_image(source,row['file_path'])
        if sha256(image)!=row['source_sha256']:raise ValueError('Review queue source image changed; create a new queue')
    return queue


def list_review_queues(project,source,task,labelset_id):
    directory=_storage(project,source)/'review_queues';result=[]
    for path in directory.glob('review_*.json'):
        try:result.append(read_review_queue(project,source,task,labelset_id,path.stem))
        except ValueError as exc:
            # A stale saved queue is visible but cannot be resumed or silently replaced.
            raw=json.loads(path.read_text())
            if raw.get('scope',{}).get('task')==task and raw.get('scope',{}).get('labelset_id')==labelset_id:result.append({**raw,'stale':True,'error':str(exc)})
    return sorted(result,key=lambda q:q['created_at'],reverse=True)


def advance_review_queue(project,source,task,labelset_id,identifier,expected_revision,relative,state,actor):
    if state not in ('reviewed','skipped') or not actor.strip():raise ValueError('Choose reviewed or skipped and enter reviewer')
    path=_queue_path(project,source,identifier)
    with _file_lock(_storage(project,source)/'review_queue.lock'):
        queue=read_review_queue(project,source,task,labelset_id,identifier)
        if queue['revision']!=expected_revision:raise ValueError('Review queue revision changed; reload')
        if queue['cursor']>=len(queue['items']) or queue['items'][queue['cursor']]['relative_path']!=relative:raise ValueError('Review the current queue image before advancing')
        row=queue['items'][queue['cursor']];row['state']=state;row['actor']=actor.strip();row['reviewed_at']=time.time()
        queue['cursor']+=1;queue['revision']+=1;queue['history'].append({'relative_path':relative,'state':state,'actor':actor.strip(),'at':time.time()})
        _write(path,queue);return queue

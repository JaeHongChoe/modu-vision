"""Lossless multiclass PNG interchange with explicit class and source mapping."""
from __future__ import annotations
import base64
import hashlib
import io
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from backend.engine.annotation_formats import safe_name


def _safe_file(root,name):
    try:name=safe_name(name)
    except ValueError as exc:raise ValueError(f'Invalid mask path: {exc}') from exc
    path=root/name
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise ValueError('Mask path escapes import directory')
    return path


def _classes(rows):
    result={}
    for row in rows:
        cid=row.get('id');name=row.get('name');color=row.get('color')
        if not isinstance(cid,int) or not 0<=cid<=255 or not isinstance(name,str) or not name.strip():raise ValueError('Mask class IDs must be integers 0–255 with explicit names')
        if not isinstance(color,str) or len(color)!=7 or color[0]!='#':raise ValueError('Mask classes require #RRGGBB palette colors')
        try:bytes.fromhex(color[1:])
        except ValueError as exc:raise ValueError('Invalid mask palette color') from exc
        if cid in result:raise ValueError('Duplicate mask class ID')
        result[cid]={'id':cid,'name':name,'color':color}
    if 0 not in result:raise ValueError('Mask class 0 background mapping is required')
    return result


def mask_annotations(pixels,classes):
    items=[]
    for cid in sorted(int(v) for v in np.unique(pixels) if v):
        if cid not in classes:raise ValueError(f'Mask class ID {cid} has no palette/name mapping')
        color=classes[cid]['color'];rgba=np.zeros((*pixels.shape,4),np.uint8)
        rgba[:,:,:3]=tuple(bytes.fromhex(color[1:]));rgba[:,:,3]=np.where(pixels==cid,255,0)
        stream=io.BytesIO();Image.fromarray(rgba).save(stream,format='PNG')
        items.append({'id':f'external_mask_{cid}','type':'brush_mask','label':classes[cid]['name'],'category_id':cid,
            'color':color,'mask_rle':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode()})
    return items


def load_mask_bundle(directory):
    root=Path(directory).expanduser().resolve()
    manifest=_safe_file(root,'mask_manifest.json')
    doc=json.loads(manifest.read_text())
    if doc.get('schema_version')!=1:raise ValueError('Mask manifest schema_version must be 1')
    classes=_classes(doc['classes']);rows=[];seen=set()
    for row in doc['images']:
        name=safe_name(row['file_name'])
        if name in seen:raise ValueError('Duplicate source image in mask manifest')
        seen.add(name);path=_safe_file(root,row['mask_file'])
        with Image.open(path) as image:
            if image.format!='PNG' or image.mode not in ('L','P'):raise ValueError('Masks require lossless single-channel 8-bit PNG class IDs')
            pixels=np.asarray(image).copy()
        if pixels.shape!=(row['height'],row['width']):raise ValueError(f'Mask geometry differs from declared source: {name}')
        unmapped=set(int(v) for v in np.unique(pixels))-set(classes)
        if unmapped:raise ValueError(f'Mask class IDs absent from mapping: {sorted(unmapped)}')
        rows.append({**row,'file_name':name,'annotations':mask_annotations(pixels,classes),'mask_pixels':pixels,
            'classes':list(classes.values()),'mask_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()})
    if not rows:raise ValueError('Mask manifest contains no source images')
    return rows


def annotation_pixels(row):
    if row.get('mask_pixels') is not None:return np.asarray(row['mask_pixels'],dtype=np.uint8)
    pixels=np.zeros((row['height'],row['width']),np.uint8)
    for ann in row['annotations']:
        cid=ann.get('category_id') or 1
        if not isinstance(cid,int) or not 1<=cid<=255:raise ValueError('Export mask class IDs must be 1–255')
        if ann['type']=='brush_mask':
            with Image.open(io.BytesIO(base64.b64decode(ann['mask_rle'].split(',',1)[1],validate=True))) as image:rgba=np.asarray(image)
            if rgba.shape!=(*pixels.shape,4):raise ValueError('Brush mask source geometry differs')
            pixels[rgba[:,:,3]>0]=cid
        elif ann['type']=='polygon':cv2.fillPoly(pixels,[np.rint(ann.get('polygon') or ann['points']).astype(np.int32)],cid)
        elif ann['type']=='bbox':
            x1,y1,x2,y2=ann['bbox'];cv2.rectangle(pixels,(int(x1),int(y1)),(int(x2),int(y2)),cid,-1)
        elif ann['type']=='rotated_bbox':
            cx,cy,w,h,angle=ann['rotated_bbox'];cv2.fillPoly(pixels,[np.rint(cv2.boxPoints(((cx,cy),(w,h),angle))).astype(np.int32)],cid)
        elif ann['type']!='tag':raise ValueError('Unsupported mask export annotation')
    return pixels


def build_mask_bundle(rows,source,include_originals=True):
    source=Path(source).resolve();files={};classes={0:{'id':0,'name':'background','color':'#000000'}};images=[]
    for row in rows:
        local=_classes(row['classes']) if row.get('classes') else {0:classes[0],**{a.get('category_id') or 1:{'id':a.get('category_id') or 1,'name':a['label'],'color':a.get('color') or '#22d3ee'} for a in row['annotations'] if a['type']!='tag'}}
        for cid,value in local.items():
            if cid in classes and classes[cid]!=value:raise ValueError(f'Conflicting class name/color for mask ID {cid}; resolve the project mapping before export')
            classes[cid]=value
        name=safe_name(row['file_name']);pixels=annotation_pixels(row);unmapped=set(int(v) for v in np.unique(pixels))-set(local)
        if unmapped:raise ValueError(f'Mask class IDs absent from labels: {sorted(unmapped)}')
        mask_name='masks/'+name+'.mask.png';mask=Image.fromarray(pixels).convert('P')
        palette=[0]*768
        for cid,value in local.items():palette[cid*3:cid*3+3]=list(bytes.fromhex(value['color'][1:]))
        mask.putpalette(palette);stream=io.BytesIO();mask.save(stream,format='PNG');files[mask_name]=stream.getvalue()
        original=_safe_file(source,name).read_bytes()
        images.append({'file_name':name,'mask_file':mask_name,'width':row['width'],'height':row['height'],
            'source_sha256':hashlib.sha256(original).hexdigest(),'mask_sha256':hashlib.sha256(files[mask_name]).hexdigest(),
            'original_file':'originals/'+name if include_originals else None})
        if include_originals:files['originals/'+name]=original
    files['mask_manifest.json']=json.dumps({'schema_version':1,'coordinate_space':'native_source_pixels',
        'classes':[classes[k] for k in sorted(classes)],'images':images},ensure_ascii=False,indent=2).encode()
    return files

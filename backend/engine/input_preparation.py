"""Versioned deterministic RGB input rules, shared with exported inspectors.

Pixels remain uint8 until the caller's single /255 tensor conversion. Model
internal normalization is deliberately outside this contract. Geometry records
pixel edges and restores only observed ROI pixels; excluded pixels are background.
"""
from hashlib import sha256
import json
import math
import cv2
import numpy as np

IMPLEMENTATION = 'modu-fixed-input-v1'
SUPPORTED_TASKS = ('classification', 'segmentation')


def canonical_recipe(value, task='classification'):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'version', 'operations'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('Input recipe requires version 1 and ordered operations')
    operations = value['operations']
    if not isinstance(operations, list) or len(operations) > 16:
        raise ValueError('Input recipe requires at most 16 ordered operations')
    if operations and task not in SUPPORTED_TASKS:
        raise ValueError('Input recipe currently supports classification and segmentation only')
    result=[]
    for row in operations:
        if not isinstance(row, dict):
            raise ValueError('Input recipe operation must be an object')
        op=row.get('op')
        if op == 'resize':
            if set(row) != {'op','width','height'} or any(type(row.get(key)) is not int or not 16 <= row[key] <= 4096 for key in ('width','height')):
                raise ValueError('Resize width and height must be integers from 16 to 4096')
        elif op == 'crop':
            roi=row.get('roi')
            if (set(row) != {'op','roi'} or not isinstance(roi,list) or len(roi)!=4
                or any(type(n) not in (int,float) or not math.isfinite(n) or not 0<=n<=1 for n in roi)
                or roi[0]>=roi[2] or roi[1]>=roi[3]):
                raise ValueError('Crop requires normalized [left, top, right, bottom] ROI')
        elif op == 'rotate90':
            if set(row) != {'op','turns'} or type(row.get('turns')) is not int or not 1<=row['turns']<=3:
                raise ValueError('Rotation requires 1, 2 or 3 counterclockwise quarter turns')
        elif op in ('brightness','contrast'):
            factor=row.get('factor')
            if set(row) != {'op','factor'} or type(factor) not in (int,float) or not math.isfinite(factor) or not .25<=factor<=4:
                raise ValueError('Photometric factor must be finite from 0.25 to 4')
        else:
            raise ValueError(f'Unsupported input recipe operation: {op}')
        result.append({**row, **({"roi":list(row["roi"])} if op=="crop" else {})})
    return {'version':1,'operations':result}


def recipe_sha256(recipe):
    canonical=canonical_recipe(recipe)
    return sha256(json.dumps(canonical,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def bind_recipe(recipe, task):
    recipe=canonical_recipe(recipe,task)
    if not recipe or not recipe['operations']:
        return {}
    return {'input_recipe':recipe,'input_recipe_sha256':recipe_sha256(recipe),
            'input_preparation_contract':{'implementation':IMPLEMENTATION,'image_interpolation':'linear',
                'mask_interpolation':'nearest','tensor_range':'0..1','normalization':'model_internal',
                'rotation':'counterclockwise','excluded_pixels':'background'}}


def saved_recipe(metadata, checkpoint=None, task='classification'):
    """Checkpoint and sidecar must agree; old models retain their old input path."""
    bindings=[]
    for value in (metadata, checkpoint) if checkpoint is not None else (metadata,):
        recipe=value.get('input_recipe')
        binding=bind_recipe(recipe,task)
        if binding:
            if any(value.get(key)!=item for key,item in binding.items()):
                raise ValueError('Saved input recipe hash or implementation differs')
        elif value.get('input_recipe_sha256') or value.get('input_preparation_contract'):
            raise ValueError('Saved input recipe binding is incomplete')
        bindings.append(binding)
    if len(bindings)==2 and bindings[0]!=bindings[1]:
        raise ValueError('Metadata and checkpoint input recipes differ')
    return bindings[0].get('input_recipe')


def prepare_input(rgb, recipe, *, task='classification', target=None, image_size=None):
    recipe=canonical_recipe(recipe,task)
    image=np.asarray(rgb).copy()
    if image.dtype!=np.uint8 or image.ndim!=3 or image.shape[2]!=3:
        raise ValueError('Input preparation requires uint8 RGB pixels')
    mask=None if target is None else np.asarray(target).copy()
    if mask is not None and (task!='segmentation' or mask.ndim!=2 or mask.shape!=image.shape[:2]):
        raise ValueError('Segmentation target must align with original RGB pixels')
    geometry={'source_size':[image.shape[1],image.shape[0]],'steps':[]}
    operations=list((recipe or {}).get('operations',[]))
    if image_size is not None:
        if not isinstance(image_size,(list,tuple)) or len(image_size)!=2 or any(type(n) is not int or not 16<=n<=4096 for n in image_size):
            raise ValueError('Model input size must contain two integers from 16 to 4096')
        operations.append({'op':'resize','width':int(image_size[0]),'height':int(image_size[1])})
    for row in operations:
        op=row['op'];height,width=image.shape[:2]
        step={'op':op,'before':[width,height]}
        if op=='crop':
            left,top,right,bottom=row['roi']
            x1=int(math.floor(left*width));y1=int(math.floor(top*height))
            x2=int(math.ceil(right*width));y2=int(math.ceil(bottom*height))
            step['box']=[x1,y1,x2,y2]
            image=image[y1:y2,x1:x2].copy()
            if mask is not None:mask=mask[y1:y2,x1:x2].copy()
        elif op=='rotate90':
            step['turns']=row['turns'];image=np.rot90(image,row['turns']).copy()
            if mask is not None:mask=np.rot90(mask,row['turns']).copy()
        elif op=='resize':
            size=(row['width'],row['height'])
            if size==(width,height):continue
            image=cv2.resize(image,size,interpolation=cv2.INTER_LINEAR)
            if mask is not None:mask=cv2.resize(mask,size,interpolation=cv2.INTER_NEAREST)
        elif op=='brightness':
            image=np.clip(image.astype(np.float32)*row['factor'],0,255).round().astype(np.uint8)
        elif op=='contrast':
            # Fixed midpoint; no statistics fitted on val/test pixels.
            image=np.clip((image.astype(np.float32)-127.5)*row['factor']+127.5,0,255).round().astype(np.uint8)
        geometry['steps'].append(step)
    geometry['prepared_size']=[image.shape[1],image.shape[0]]
    return image,mask,geometry


def restore_mask(mask, geometry, *, continuous=False):
    """Inverse spatial operations; crop exterior is zero, never invented labels."""
    output=np.asarray(mask).copy()
    for step in reversed(geometry['steps']):
        op=step['op'];width,height=step['before']
        if op=='resize':
            output=cv2.resize(output,(width,height),interpolation=cv2.INTER_LINEAR if continuous else cv2.INTER_NEAREST)
        elif op=='rotate90':
            output=np.rot90(output,-step['turns']).copy()
        elif op=='crop':
            x1,y1,x2,y2=step['box'];canvas=np.zeros((height,width,*output.shape[2:]),dtype=output.dtype)
            canvas[y1:y2,x1:x2]=output;output=canvas
    return output

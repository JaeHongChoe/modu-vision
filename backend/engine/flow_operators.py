"""Reusable image operators with homogeneous local-to-source pixel transforms."""
from __future__ import annotations
import base64
import math
import cv2
import numpy as np


def validate_operator(kind, params):
    if kind == 'patch_split':
        for key in ('patch_width','patch_height'):
            value=params.get(key,224)
            if type(value) is not int or not 16 <= value <= 8192:
                raise ValueError(f'{key} must be an integer from 16 to 8192')
        overlap=params.get('overlap',0)
        if type(overlap) is not int or not 0 <= overlap < min(params.get('patch_width',224),params.get('patch_height',224)):
            raise ValueError('Patch overlap must be smaller than each patch dimension')
    else:
        operation=params.get('operation','rotate')
        if operation not in ('rotate','align','improve','enhancement'):
            raise ValueError('Unknown preprocessing operation')
        angle=params.get('angle_deg',0)
        if isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle):
            raise ValueError('Rotation angle must be finite')
        target_angle=params.get('target_angle_deg',0)
        if isinstance(target_angle,bool) or not isinstance(target_angle,(int,float)) or not math.isfinite(target_angle):
            raise ValueError('Alignment target angle must be finite')
        if operation=='improve' and params.get('method','clahe') not in ('clahe','denoise','sharpen'):
            raise ValueError('Unknown image improvement method')


def local_image(image, roi):
    if isinstance(roi.get('image'),np.ndarray):
        return roi['image'],np.asarray(roi['source_transform'],dtype=float)
    x1,y1,x2,y2=roi['bbox']
    return image[y1:y2,x1:x2].copy(),np.array([[1,0,x1],[0,1,y1],[0,0,1]],dtype=float)


def source_bbox(transform, width, height):
    points=(transform@np.array([[0,width,width,0],[0,0,height,height],[1,1,1,1]],dtype=float))[:2]
    return [int(np.floor(points[0].min())),int(np.floor(points[1].min())),int(np.ceil(points[0].max())),int(np.ceil(points[1].max()))]


def _starts(length, size, overlap):
    if length<=size: return [0]
    values=list(range(0,length-size+1,size-overlap))
    if values[-1]!=length-size: values.append(length-size)
    return values


def apply_operator(image, regions, kind, params, node_id, enhancement=None):
    validate_operator(kind,params)
    result=[]
    for roi in regions:
        local,transform=local_image(image,roi)
        if not local.size: raise ValueError('Operator received empty image region')
        h,w=local.shape[:2]
        if kind=='patch_split':
            pw,ph=params.get('patch_width',224),params.get('patch_height',224)
            overlap=params.get('overlap',0)
            xs,ys=_starts(w,pw,overlap),_starts(h,ph,overlap)
            if len(xs)*len(ys)+len(result)>1024: raise ValueError('Patch split exceeds 1024 regions')
            for y in ys:
                for x in xs:
                    patch=local[y:min(y+ph,h),x:min(x+pw,w)].copy()
                    mapped=transform@np.array([[1,0,x],[0,1,y],[0,0,1]],dtype=float)
                    result.append({**roi,'id':f'{node_id}:{roi["id"]}:{x}:{y}','image':patch,'bbox':source_bbox(mapped,patch.shape[1],patch.shape[0]),'source_transform':mapped.tolist(),'crop_padding':0})
            continue
        operation=params.get('operation','rotate')
        if operation in ('rotate','align'):
            angle=float(params.get('angle_deg',0))
            if operation=='align':
                # Alignment requires an explicit observed orientation or configured target.
                box=roi.get('rotated_box')
                if box is None and 'angle_deg' not in params: raise ValueError('Alignment needs rotated detection orientation or configured angle_deg')
                angle=float(box['angle_deg'])-float(params.get('target_angle_deg',0)) if box else angle
            matrix=cv2.getRotationMatrix2D(((w-1)/2,(h-1)/2),angle,1)
            cosine,sine=abs(matrix[0,0]),abs(matrix[0,1])
            output_w=max(1,int(math.ceil(w*cosine+h*sine-1e-8)))
            output_h=max(1,int(math.ceil(h*cosine+w*sine-1e-8)))
            matrix[0,2]+=(output_w-w)/2
            matrix[1,2]+=(output_h-h)/2
            output=cv2.warpAffine(local,matrix,(output_w,output_h),flags=cv2.INTER_LINEAR,borderMode=cv2.BORDER_CONSTANT)
            homogeneous=np.vstack([matrix,[0,0,1]])
            transform=transform@np.linalg.inv(homogeneous)
        elif operation=='enhancement':
            if enhancement is None: raise ValueError('Enhancement needs a trained model')
            output=enhancement(local)
            if output.shape!=local.shape or output.dtype!=np.uint8: raise ValueError('Enhancement adapter changed image shape or type')
        else:
            method=params.get('method','clahe')
            if method=='clahe':
                lab=cv2.cvtColor(local,cv2.COLOR_RGB2LAB)
                lab[:,:,0]=cv2.createCLAHE(clipLimit=2,tileGridSize=(8,8)).apply(lab[:,:,0])
                output=cv2.cvtColor(lab,cv2.COLOR_LAB2RGB)
            elif method=='denoise': output=cv2.fastNlMeansDenoisingColored(local,None,3,3,7,21)
            else: output=cv2.addWeighted(local,1.5,cv2.GaussianBlur(local,(0,0),1),-0.5,0)
        result.append({**roi,'id':f'{node_id}:{roi["id"]}','image':output,'bbox':roi['bbox'],'source_transform':transform.tolist(),'crop_padding':0})
    return result


def image_uri(image, mask=False):
    pixels=(image>0).astype(np.uint8)*255 if mask else image
    if not mask and pixels.ndim==3: pixels=cv2.cvtColor(pixels,cv2.COLOR_RGB2BGR)
    h,w=pixels.shape[:2]
    if max(h,w)>320: pixels=cv2.resize(pixels,(max(1,round(w*320/max(h,w))),max(1,round(h*320/max(h,w)))),interpolation=cv2.INTER_NEAREST if mask else cv2.INTER_AREA)
    ok,encoded=cv2.imencode('.png',pixels)
    if not ok: raise ValueError('Intermediate image encoding failed')
    return 'data:image/png;base64,'+base64.b64encode(encoded).decode('ascii')


def region_artifacts(image, regions, evidence=()):
    artifacts=[]
    for roi in regions[:64]:
        local,transform=local_image(image,roi)
        if local.size:
            artifacts.append({'roi_id':roi['id'],'bbox':roi['bbox'],'image':image_uri(local),'source_transform':transform.tolist(),'image_size':[local.shape[1],local.shape[0]]})
    for crop in evidence:
        match=next((r for r in artifacts if r['roi_id']==crop.roi_id),None)
        if match is None:
            match={'roi_id':crop.roi_id,'bbox':crop.bbox,'image':crop.crop_thumbnail}; artifacts.append(match)
        if crop._defect_mask is not None: match['mask']=image_uri(crop._defect_mask,mask=True)
        match['evidence']=crop.model_dump(exclude={'crop_thumbnail'})
    return artifacts

"""Real candidate inference providers with explicit support boundaries."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import threading
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from backend.engine.dicom_input import open_source_image

_MODEL_LOCK=threading.RLock()
_MODEL_CACHE={}

# The real model provider is deliberately separate from deterministic tools.
from backend.engine.foundation_labeling import foundation_candidates

def filter_keywords(candidates,keywords):
    if not keywords: return candidates
    words=[word.strip().casefold() for word in keywords]
    if any(not word for word in words): raise ValueError('Enter nonempty class keywords')
    return [c for c in candidates if any(word in c['annotation']['label'].casefold() for word in words)]

def filter_candidate_sizes(candidates,min_area=0,max_area=None,min_width=0,max_width=None,min_height=0,max_height=None):
    for lower,upper in [(min_area,max_area),(min_width,max_width),(min_height,max_height)]:
        if lower<0 or upper is not None and upper<lower: raise ValueError('Maximum candidate size must be at least its minimum')
    result=[]
    for candidate in candidates:
        annotation=candidate['annotation'];box=annotation.get('bbox');polygon=annotation.get('polygon') or annotation.get('points')
        if polygon:
            points=np.asarray(polygon,dtype=np.float32)
            area=float(cv2.contourArea(points));x1,y1=points.min(0);x2,y2=points.max(0)
            width=float(x2-x1);height=float(y2-y1)
            if box:width=float(box[2]-box[0]);height=float(box[3]-box[1])
        elif box:
            width=float(box[2]-box[0]);height=float(box[3]-box[1]);area=width*height
        else:
            # Image tags have no object region and are not invented as boxes.
            if min_area or max_area is not None or min_width or max_width is not None or min_height or max_height is not None: continue
            result.append(candidate);continue
        area=candidate.get('area',area)
        if area<min_area or max_area is not None and area>max_area:continue
        if width<min_width or max_width is not None and width>max_width:continue
        if height<min_height or max_height is not None and height>max_height:continue
        result.append(candidate)
    return result

def template_candidates(image_path,exemplar_path,label,threshold=.8,max_candidates=20,exemplar_roi=None):
    if not label.strip(): raise ValueError('An exemplar candidate needs a class label')
    image=cv2.imread(str(image_path)); template=cv2.imread(str(exemplar_path))
    if image is None or template is None: raise ValueError('Cannot decode image or exemplar')
    if exemplar_roi is not None:
        if len(exemplar_roi)!=4: raise ValueError('Exemplar region needs four coordinates')
        x1,y1,x2,y2=map(int,exemplar_roi)
        if not(0<=x1<x2<=template.shape[1] and 0<=y1<y2<=template.shape[0]): raise ValueError('Exemplar region outside image')
        template=template[y1:y2,x1:x2]
    ih,iw=image.shape[:2];th,tw=template.shape[:2]
    if min(th,tw)<3 or th>ih or tw>iw: raise ValueError('Exemplar must fit inside target and be at least 3 pixels per side')
    if float(np.std(template))<1: raise ValueError('Exemplar has insufficient texture for template matching')
    scores=cv2.matchTemplate(image,template,cv2.TM_CCOEFF_NORMED);candidates=[]
    for _ in range(max_candidates):
        _,score,_,(x,y)=cv2.minMaxLoc(scores)
        if not np.isfinite(score) or score<threshold: break
        candidates.append({'confidence':float(score),'annotation':{'type':'bbox','label':label,'category_id':1,'bbox':[float(x),float(y),float(x+tw),float(y+th)],'color':'#22d3ee'}})
        # Avoid overlapping proposals for one matched instance.
        scores[max(0,y-th+1):min(scores.shape[0],y+th),max(0,x-tw+1):min(scores.shape[1],x+tw)]=-1
    return candidates

def semantic_readiness(model_dir):
    path=Path(model_dir).expanduser().resolve() if model_dir else None
    dependency=importlib.util.find_spec('transformers') is not None
    configured=path is not None and path.is_dir()
    valid=False;error=None
    if configured:
        try:
            config=json.loads((path/'config.json').read_text())
            valid=config.get('model_type')=='grounding-dino' and ((path/'model.safetensors').is_file() or (path/'pytorch_model.bin').is_file() or (path/'model.safetensors.index.json').is_file())
            if not valid: error='Grounding DINO config and local model weights are required'
        except (OSError,ValueError): error='Missing or invalid local model config'
    if not dependency: error='Install the optional semantic labeling dependencies in the backend environment'
    elif not configured: error='Choose a local Grounding DINO model directory'
    return {'backend':'grounding_dino','ready':bool(dependency and configured and valid),'dependency_available':dependency,
            'model_dir':str(path) if path else None,'error':error,
            'limits':'English object phrases; complex relations and microscopic defects may be missed. CPU or an available GPU; local weights only; each box requires human review.'}

def model_directory_hash(model_dir):
    root=Path(model_dir).resolve(); digest=hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name.startswith('.'): continue
        if path.is_symlink() and not path.resolve().is_file(): raise ValueError('Broken model link')
        digest.update(path.relative_to(root).as_posix().encode())
        with path.open('rb') as handle:
            for block in iter(lambda:handle.read(1024*1024),b''): digest.update(block)
    return digest.hexdigest()

def grounded_candidates(image_path,model_dir,prompt,threshold=.3,text_threshold=.25,device='cpu',cancel=None):
    ready=semantic_readiness(model_dir)
    if not ready['ready']: raise ValueError(ready['error'])
    if not prompt.strip(): raise ValueError('Enter nonempty object phrases')
    from backend.engine.foundation_labeling import prompt_chunks,resolve_device,check_cancel
    device=resolve_device(device)
    import torch
    from transformers import AutoProcessor,AutoModelForZeroShotObjectDetection
    directory=str(Path(model_dir).resolve()); signature=model_directory_hash(directory)
    with _MODEL_LOCK:
        if (directory,signature,device) not in _MODEL_CACHE:
            processor=AutoProcessor.from_pretrained(directory,local_files_only=True,trust_remote_code=False)
            model=AutoModelForZeroShotObjectDetection.from_pretrained(directory,local_files_only=True,trust_remote_code=False).to(device).eval()
            _MODEL_CACHE.clear();_MODEL_CACHE[(directory,signature,device)]=(processor,model)
        processor,model=_MODEL_CACHE[(directory,signature,device)]
        image=open_source_image(image_path).convert('RGB')
        candidates=[]
        pending=list(prompt_chunks(prompt,max_words=40))
        while pending:
            check_cancel(cancel)
            chunk=pending.pop(0)
            # Model token capacity is the actual bound, never a character UI cap.
            token_ids=processor.tokenizer(chunk,add_special_tokens=True)['input_ids']
            if len(token_ids)>model.config.max_text_len:
                if len(chunk)<2: raise ValueError('Text cannot fit the configured grounding model token capacity')
                middle=len(chunk)//2
                pending[:0]=[chunk[:middle],chunk[middle:]]
                continue
            inputs=processor(images=image,text=chunk.strip().lower(),return_tensors='pt').to(device)
            with torch.inference_mode(): outputs=model(**inputs)
            check_cancel(cancel)
            result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,threshold=threshold,text_threshold=text_threshold,target_sizes=[(image.height,image.width)])[0]
            labels=result.get('text_labels',result.get('labels',[]))
            for score,label,box in zip(result['scores'],labels,result['boxes']):
                coords=[float(v) for v in box.tolist()]; coords=[max(0,min(v,image.width if i%2==0 else image.height)) for i,v in enumerate(coords)]
                if coords[2]<=coords[0] or coords[3]<=coords[1]: continue
                candidates.append({'confidence':float(score),'annotation':{'type':'bbox','label':str(label),'category_id':1,'bbox':coords,'color':'#22d3ee'}})
        return candidates

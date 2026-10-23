"""Real candidate inference providers with explicit support boundaries."""
from __future__ import annotations
import hashlib
import importlib.util
import json
import re
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

GROUNDING_DINO_OFFICIAL_REFERENCE={'family':'Grounding DINO (Hugging Face transformers format)','example_repo_id':'IDEA-Research/grounding-dino-tiny',
    'url':'https://huggingface.co/IDEA-Research/grounding-dino-tiny'}
# One fixed revision checked against the hub and the downloaded bytes (2026-10-23), as SAM2_OFFICIAL_PINNED.
GROUNDING_DINO_OFFICIAL_PINNED={'repo_id':'IDEA-Research/grounding-dino-tiny','revision':'a2bb814dd30d776dcf7e30523b00659f4f141c71',
    'url':'https://huggingface.co/IDEA-Research/grounding-dino-tiny/tree/a2bb814dd30d776dcf7e30523b00659f4f141c71',
    'license':'apache-2.0','weights_bytes':689359096,'weights_sha256':'1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3','verified_on':'2026-10-23'}

def semantic_readiness(model_dir):
    path=Path(model_dir).expanduser().resolve() if model_dir else None
    dependency=importlib.util.find_spec('transformers') is not None
    configured=path is not None and path.is_dir()
    valid=False;error=None
    if configured:
        try:
            config=json.loads((path/'config.json').read_text(encoding='utf-8'))
            valid=config.get('model_type')=='grounding-dino' and ((path/'model.safetensors').is_file() or (path/'pytorch_model.bin').is_file() or (path/'model.safetensors.index.json').is_file())
            if not valid: error='Grounding DINO config and local model weights are required'
        except (OSError,ValueError): error='Missing or invalid local model config'
    if not dependency: error='Install the optional semantic labeling dependencies in the backend environment'
    elif not configured: error='Choose a local Grounding DINO model directory'
    sha256=None
    if configured and (path/'model.safetensors').is_file():
        from backend.engine.foundation_labeling import cached_file_sha256
        try: sha256=cached_file_sha256(path/'model.safetensors')
        except OSError: sha256=None
    return {'backend':'grounding_dino','ready':bool(dependency and configured and valid),'dependency_available':dependency,
            'model_dir':str(path) if path else None,'error':error,'model_sha256':sha256,
            'official_reference':dict(GROUNDING_DINO_OFFICIAL_REFERENCE),'official_pinned':dict(GROUNDING_DINO_OFFICIAL_PINNED),
            'official_match':sha256==GROUNDING_DINO_OFFICIAL_PINNED['weights_sha256'] if sha256 else None,
            'limits':'English object phrases; complex relations and microscopic defects may be missed. CPU or an available GPU; local weights only; each box requires human review.'}

_DIRECTORY_HASHES={}

def model_directory_hash(model_dir):
    """SHA256 over every file's relative name and bytes, read again only after a file's size, modification or change time,
    or inode changes: the official folders are 156 MB and 689 MB, and one text draft asked for this several times per image."""
    root=Path(model_dir).resolve(); files=[]
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.name.startswith('.'): continue
        if path.is_symlink() and not path.resolve().is_file(): raise ValueError('Broken model link')
        stat=path.stat(); files.append((path.relative_to(root).as_posix(),path,(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns,stat.st_ino)))
    # ctime and inode change on any rewrite or replacement, even a copy that keeps the old modification time.
    key=(str(root),tuple((name,signature) for name,_,signature in files))
    digest=_DIRECTORY_HASHES.get(key)
    if digest is None:
        hasher=hashlib.sha256()
        for name,path,_ in files:
            hasher.update(name.encode())
            with path.open('rb') as handle:
                for block in iter(lambda:handle.read(1024*1024),b''): hasher.update(block)
        digest=hasher.hexdigest()
        if len(_DIRECTORY_HASHES)>=8: _DIRECTORY_HASHES.clear()
        _DIRECTORY_HASHES[key]=digest
    return digest

# Grounding DINO reads English object phrases, each ending in '.', as "scratch. dent." (its processor's merged-label
# text). Without the final '.', or with commas, the real model returned no box at all (2026-10-23, grounding-dino-tiny
# @a2bb814), and its English tokenizer turns Korean into sub-word pieces. So people's text ("scratch, dent", "긁힘")
# is made into that form here: a known Korean defect word is read as its English word (the pair stays in the draft's
# provenance); any other Korean phrase is refused with what to type, never sent as text the model cannot read.
KOREAN_DEFECT_TERMS={
    '긁힘':'scratch','스크래치':'scratch','흠집':'scratch','찍힘':'dent','눌림':'dent','덴트':'dent','균열':'crack',
    '크랙':'crack','갈라짐':'crack','얼룩':'stain','오염':'stain','이물':'foreign object','이물질':'foreign object',
    '기포':'bubble','구멍':'hole','핀홀':'pinhole','깨짐':'broken part','파손':'broken part','이빠짐':'chip','칩핑':'chip',
    '변색':'discoloration','녹':'rust','버':'burr','점':'spot','반점':'spot','주름':'wrinkle','돌기':'bump',
    '변형':'deformation','결함':'defect','불량':'defect','사각형':'square','원':'circle','선':'line'}
_PHRASE_SPLIT=re.compile(r'[,.;\n/·、，。]+')
_HANGUL=re.compile(r'[\u1100-\u11ff\u3130-\u318f\uac00-\ud7a3]')


def grounding_phrases(prompt):
    """The person's phrases as the model reads them: [{'text': what they typed, 'query': lowercase English}], in order,
    without repeats. Separators are commas, periods, semicolons, slashes and new lines."""
    phrases=[];seen=set();unknown=[]
    for raw in _PHRASE_SPLIT.split(prompt or ''):
        text=' '.join(raw.split())
        if not text: continue
        if _HANGUL.search(text):
            query=KOREAN_DEFECT_TERMS.get(text.replace(' ',''))
            if query is None: unknown.append(text);continue
        else:
            query=text.lower()
        if query in seen: continue
        seen.add(query);phrases.append({'text':text,'query':query})
    if unknown:
        known=', '.join(f'{k}→{v}' for k,v in list(KOREAN_DEFECT_TERMS.items())[:8])
        raise ValueError(f"문구 초안 모델은 영어 단어를 읽습니다. '{', '.join(unknown)}'을(를) 영어로 적으세요(예: scratch, dent). "
                         f"바로 읽는 한국어 낱말: {known} 등.")
    if not phrases: raise ValueError('Enter nonempty object phrases')
    return phrases


def _phrase_token_spans(tokenizer,text,phrases):
    """Token positions of each phrase inside the merged text "a. b." (special tokens have empty offsets)."""
    offsets=tokenizer(text,return_offsets_mapping=True,add_special_tokens=True)['offset_mapping']
    spans=[];cursor=0
    for phrase in phrases:
        start=cursor;end=start+len(phrase['query']);cursor=end+2
        spans.append([index for index,(a,b) in enumerate(offsets) if b>a and a>=start and b<=end])
    return spans


def grounded_candidates(image_path,model_dir,prompt,threshold=.3,text_threshold=.25,device='cpu',cancel=None):
    ready=semantic_readiness(model_dir)
    if not ready['ready']: raise ValueError(ready['error'])
    if not prompt.strip(): raise ValueError('Enter nonempty object phrases')
    phrases=grounding_phrases(prompt)
    from backend.engine.foundation_labeling import resolve_device,check_cancel
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
        merged=lambda group:'. '.join(p['query'] for p in group)+'.'
        # Model token capacity is the actual bound, never a character UI cap: phrases are grouped whole into calls.
        chunks=[];current=[]
        for phrase in phrases:
            if len(processor.tokenizer(merged(current+[phrase]),add_special_tokens=True)['input_ids'])<=model.config.max_text_len:
                current.append(phrase);continue
            if not current: raise ValueError('Text cannot fit the configured grounding model token capacity')
            chunks.append(current);current=[phrase]
        if current: chunks.append(current)
        candidates=[]
        for chunk in chunks:
            check_cancel(cancel)
            text=merged(chunk)
            inputs=processor(images=image,text=text,return_tensors='pt').to(device)
            with torch.inference_mode(): outputs=model(**inputs)
            check_cancel(cancel)
            result=processor.post_process_grounded_object_detection(outputs,inputs.input_ids,threshold=threshold,text_threshold=text_threshold,target_sizes=[(image.height,image.width)])[0]
            # The processor keeps, in query order, the queries whose best token clears `threshold`; the same queries'
            # token probabilities say which one phrase each box matched (the phrase holding the highest token).
            probs=outputs.logits[0].sigmoid().float().cpu()
            kept=probs[probs.max(-1).values>threshold]
            spans=_phrase_token_spans(processor.tokenizer,text,chunk)
            for score,box,prob in zip(result['scores'],result['boxes'],kept):
                phrase_scores=[float(prob[span].max()) if span else 0. for span in spans]
                best=int(np.argmax(phrase_scores))
                if phrase_scores[best]<text_threshold: continue
                coords=[float(v) for v in box.tolist()]; coords=[max(0,min(v,image.width if i%2==0 else image.height)) for i,v in enumerate(coords)]
                if coords[2]<=coords[0] or coords[3]<=coords[1]: continue
                phrase=chunk[best]
                candidates.append({'confidence':float(score),'annotation':{'type':'bbox','label':phrase['text'],'category_id':1,'bbox':coords,'color':'#22d3ee'},
                                   'grounding':{'phrase':phrase['text'],'query':phrase['query'],'phrase_score':phrase_scores[best]}})
        # Two phrases can box the same object ("dark square, scratch"): one draft per object, the higher score kept.
        kept_boxes=[]
        for candidate in sorted(candidates,key=lambda c:c['confidence'],reverse=True):
            if all(_box_iou(candidate['annotation']['bbox'],other['annotation']['bbox'])<=.8 for other in kept_boxes): kept_boxes.append(candidate)
        return kept_boxes


def _box_iou(a,b):
    width=max(0.,min(a[2],b[2])-max(a[0],b[0]));height=max(0.,min(a[3],b[3])-max(a[1],b[1]));inter=width*height
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-inter
    return inter/union if union>0 else 0.

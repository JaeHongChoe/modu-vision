"""Persisted project/labelset review metadata. Sources are read only.

The JSON ledger is copied by the existing dataset version and project backup
mechanisms. An OS lock serializes independent API workers; revisions reject old
review decisions. Stable UUIDs identify paths, while content versions identify
changed bytes. Reading also detects edits made by legacy annotation clients.
"""
from __future__ import annotations
import copy
try:
    import fcntl
except ImportError:  # Native Windows backend
    fcntl = None
    import msvcrt
import hashlib
import json
import os
import random
import tempfile
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from PIL import Image
from backend.engine.dicom_input import open_source_image
from backend.engine.annotation_storage import dataset_annotation_dir, scoped_annotation_root
from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS

_ACTIVE_TRANSACTIONS = ContextVar("metadata_transactions", default=None)

@contextmanager
def _file_lock(path):
    with path.open("a+b") as handle:
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX)
        else:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0: handle.write(b"0"); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            if fcntl is not None: fcntl.flock(handle, fcntl.LOCK_UN)
            else:
                handle.seek(0); msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

class RevisionConflict(ValueError):
    def __init__(self, current):
        super().__init__('다른 작업자가 수정했습니다. 최신 내용을 불러와 다시 검토하세요.')
        self.current = copy.deepcopy(current)

def _hash(path):
    if not path.is_file(): return None
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()

def _now(): return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def _root(project_root, annotation_root=None):
    return Path(annotation_root) if annotation_root is not None else scoped_annotation_root(Path(project_root)/'annotations')

def ledger_path(project_root, dataset_root, annotation_root=None):
    return dataset_annotation_dir(Path(dataset_root), _root(project_root, annotation_root), use_scope=False)/'metadata'/'workflow.json'

@contextmanager
def metadata_transaction(project_root, dataset_root, annotation_root=None):
    path = ledger_path(project_root, dataset_root, annotation_root)
    key = str(path.resolve())
    active = _ACTIVE_TRANSACTIONS.get() or {}
    if key in active:
        yield active[key]
        return
    # Restoring a ledger never replaces its external lock inode.
    locks = Path(project_root)/'.metadata_locks'; locks.mkdir(parents=True, exist_ok=True)
    lock_name = hashlib.sha256(key.encode()).hexdigest()
    with _file_lock(locks/f'{lock_name}.lock'):
        if path.is_symlink(): raise ValueError('Metadata ledger cannot be a symbolic link')
        ledger = json.loads(path.read_text()) if path.exists() else {'schema_version':1, 'images':{}}
        if not isinstance(ledger.get('images'),dict): raise ValueError('Invalid metadata ledger')
        before = json.dumps(ledger, sort_keys=True)
        token = _ACTIVE_TRANSACTIONS.set({**active,key:ledger})
        try:
            yield ledger
            if json.dumps(ledger,sort_keys=True) != before or not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as handle:
                    json.dump(ledger,handle,ensure_ascii=False,indent=2); handle.flush(); os.fsync(handle.fileno())
                    temporary = Path(handle.name)
                os.replace(temporary,path)
        finally:
            _ACTIVE_TRANSACTIONS.reset(token)

def _visible_path(dataset_root, image_path):
    source = Path(dataset_root).expanduser().resolve()
    image = Path(os.path.abspath(Path(image_path).expanduser()))
    if not image.is_relative_to(source) or image.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS or not image.is_file():
        raise ValueError('Image must be a file inside the selected dataset')
    return source, image

def _annotation_hash(project_root, source, image, annotation_root=None):
    studio = dataset_annotation_dir(image.parent, _root(project_root,annotation_root),use_scope=False)/f'{image.stem}.json'
    if studio.exists(): return _hash(studio)
    if image.with_suffix('.json').exists(): return _hash(image.with_suffix('.json'))
    from backend.engine.annotation_formats import source_annotation_files
    files = source_annotation_files(source, image)
    if not files: return None
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(source)).encode());digest.update((_hash(path) or '').encode())
    return digest.hexdigest()

def _mask_hash(project_root,source,image,annotation_root=None):
    studio=dataset_annotation_dir(image.parent,_root(project_root,annotation_root),use_scope=False)
    overlay=studio/'masks'/f'{image.stem}.png'
    if overlay.is_file(): return _hash(overlay)
    relative=image.relative_to(Path(source));parts=list(relative.parts)
    candidates=[Path(source)/'masks'/f'{image.stem}.png',Path(source)/'masks'/f'{image.stem}_mask.png',Path(source)/'ground_truth'/image.parent.name/f'{image.stem}_mask.png']
    if 'images' in parts:
        parts[parts.index('images')]='masks';candidates.insert(0,Path(source)/Path(*parts).with_suffix('.png'))
    mask=next((p for p in candidates if p.is_file()),None)
    return _hash(mask) if mask else None

def _event(row, actor, action, changes):
    row['revision'] += 1
    row['audit'].append({'id':str(uuid.uuid4()),'at':_now(),'actor':actor,'action':action,
                         'revision':row['revision'],'changes':changes})

def _ensure(ledger, project_root, dataset_root, image_path, annotation_root=None):
    source,image = _visible_path(dataset_root,image_path)
    relative = image.relative_to(source).as_posix()
    content = _hash(image)
    annotation = _annotation_hash(project_root,source,image,annotation_root)
    mask = _mask_hash(project_root,source,image,annotation_root)
    row = ledger['images'].get(relative)
    if row is None:
        with open_source_image(image) as pil: width,height=pil.size
        row={'image_uuid':str(uuid.uuid5(uuid.NAMESPACE_URL,f'{Path(project_root).resolve()}\0{source}\0{relative}')),
             'file_path':str(image),'relative_path':relative,'content_hash':content,'content_version':1,
             'width':width,'height':height,'revision':0,'tags':[], 'product':'','lot':'','group':'',
             'workflow_state':'unworked','usage_state':'active','reviewer':None,'review_history':[],'audit':[],
             'annotation_hash':annotation,'mask_hash':mask}
        _event(row,'system','registered',{'content_hash':content}); ledger['images'][relative]=row
    else:
        changes={}
        if row['content_hash'] != content:
            with open_source_image(image) as pil: row['width'],row['height']=pil.size
            row['content_hash']=content; row['content_version']+=1; changes['content_hash']=content
        if row.get('annotation_hash') != annotation:
            row['annotation_hash']=annotation; changes['annotation_hash']=annotation
        if row.get('mask_hash') != mask:
            row['mask_hash']=mask; changes['mask_hash']=mask
        if changes:
            row['workflow_state']='needs_review'; row['reviewer']=None
            _event(row,'system','source_changed' if 'content_hash' in changes else 'external_annotation_changed',changes)
    return row

def metadata_for_path(project_root, dataset_root, image_path, annotation_root=None):
    with metadata_transaction(project_root,dataset_root,annotation_root) as ledger:
        return copy.deepcopy(_ensure(ledger,project_root,dataset_root,image_path,annotation_root))

def list_metadata(project_root, dataset_root, annotation_root=None):
    source=Path(dataset_root).resolve(); project=Path(project_root).resolve()
    rows=[]
    with metadata_transaction(project_root,source,annotation_root) as ledger:
        for directory,names,files in os.walk(source,followlinks=False):
            names[:]=sorted(n for n in names if not n.startswith('.') and (Path(directory)/n).resolve()!=project)
            for name in sorted(files):
                image=Path(directory)/name
                if name.startswith('.') or image.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS: continue
                rows.append(copy.deepcopy(_ensure(ledger,project,source,image,annotation_root)))
    return rows

def _find(ledger,image_uuid):
    row=next((r for r in ledger['images'].values() if r['image_uuid']==image_uuid),None)
    if row is None: raise KeyError(image_uuid)
    return row

def update_metadata(project_root,dataset_root,image_uuid,expected_revision,actor,changes,annotation_root=None):
    actor=actor.strip()
    if not actor or len(actor)>100: raise ValueError('검토자 이름을 1~100자로 입력하세요.')
    allowed={'tags','product','lot','group','workflow_state','usage_state'}
    if set(changes)-allowed: raise ValueError('Unknown metadata fields')
    with metadata_transaction(project_root,dataset_root,annotation_root) as ledger:
        old=_find(ledger,image_uuid)
        row=_ensure(ledger,project_root,dataset_root,old['file_path'],annotation_root)
        if row['revision']!=expected_revision: raise RevisionConflict(row)
        clean={}
        for key,value in changes.items():
            if key=='tags':
                if not isinstance(value,list) or len(value)>100 or any(not isinstance(t,str) or len(t)>100 for t in value): raise ValueError('Invalid tags')
                clean[key]=list(dict.fromkeys(t.strip() for t in value if t.strip()))
            elif key=='usage_state':
                if value not in {'active','not_used'}:raise ValueError('Invalid usage state')
                clean[key]=value
            elif key=='workflow_state':
                if value not in {'unworked','needs_review','approved'}: raise ValueError('Invalid review state')
                clean[key]=value
            else:
                if not isinstance(value,str) or len(value)>200: raise ValueError('Metadata value must be under 200 characters')
                clean[key]=value.strip()
        row.update(clean)
        if 'workflow_state' in clean:
            row['reviewer']=actor if clean['workflow_state']=='approved' else None
            row['review_history'].append({'at':_now(),'actor':actor,'state':clean['workflow_state'], 'revision':row['revision']+1})
        _event(row,actor,'review' if 'workflow_state' in clean else 'metadata_edited',clean)
        return copy.deepcopy(row)

def annotation_changed(project_root,dataset_root,image_path,actor='operator',annotation_root=None,expected_revision=None):
    with metadata_transaction(project_root,dataset_root,annotation_root) as ledger:
        row=_ensure(ledger,project_root,dataset_root,image_path,annotation_root)
        if expected_revision is not None and row['revision']!=expected_revision: raise RevisionConflict(row)
        row['workflow_state']='needs_review'; row['reviewer']=None
        row['annotation_hash']=_annotation_hash(project_root,Path(dataset_root),Path(image_path),annotation_root)
        row['mask_hash']=_mask_hash(project_root,Path(dataset_root),Path(image_path),annotation_root)
        _event(row,actor,'annotation_changed',{'workflow_state':'needs_review'})
        return copy.deepcopy(row)

def duplicate_leakage(rows,assignments):
    by_hash={}
    for row in rows: by_hash.setdefault(row['content_hash'],[]).append(row)
    return [{'content_hash':key,'images':[r['relative_path'] for r in group],
             'splits':sorted({assignments.get(r['relative_path'],'unassigned') for r in group}),
             'cross_split':len({assignments.get(r['relative_path'],'unassigned') for r in group})>1}
            for key,group in by_hash.items() if len(group)>1]

def preview_split(rows,group_by,train_ratio=.7,val_ratio=.2,test_ratio=.1,seed=42):
    if not rows: raise ValueError('No images to split')
    if not group_by or set(group_by)-{'product','lot','group'}: raise ValueError('Select product, lot or group')
    ratios=[train_ratio,val_ratio,test_ratio]
    if any(v<0 for v in ratios) or abs(sum(ratios)-1)>1e-6: raise ValueError('Split ratios must sum to one')
    # Union groups and content duplicates. This also handles transitive leakage.
    parents=list(range(len(rows)))
    def find(i):
        while parents[i]!=i: parents[i]=parents[parents[i]]; i=parents[i]
        return i
    seen={}
    for i,row in enumerate(rows):
        identity=tuple(row[k] for k in group_by)
        if not all(identity): raise ValueError(f"Missing grouping metadata: {row['relative_path']}")
        for key in [('group',identity),('hash',row['content_hash'])]:
            if key in seen: parents[find(i)]=find(seen[key])
            else: seen[key]=i
    groups={}
    for i,row in enumerate(rows): groups.setdefault(find(i),[]).append(row)
    active=[i for i,r in enumerate(ratios) if r>0]
    if len(groups)<len(active): raise ValueError('Not enough independent groups for nonempty splits')
    ordered=list(groups.values()); random.Random(seed).shuffle(ordered); ordered.sort(key=lambda g:(all(r.get("train_eligible",True) for r in g),len(g)),reverse=True)
    counts=[0,0,0]; targets=[len(rows)*r for r in ratios]; assignments={}
    for position,group in enumerate(ordered):
        empty=[i for i in active if counts[i]==0]
        options=empty if len(ordered)-position==len(empty) else active
        if not all(r.get("train_eligible",True) for r in group): options=[i for i in options if i!=0]
        if not options: raise ValueError("No normal-only anomaly training group can satisfy the split")
        partition=min(options,key=lambda i:sum((counts[j]+(len(group) if j==i else 0)-targets[j])**2 for j in active))
        for row in group: assignments[row['relative_path']]=['train','val','test'][partition]
        counts[partition]+=len(group)
    return {'assignments':assignments,'split':dict(zip(['train','val','test'],counts)),
            'group_count':len(groups),'group_by':group_by,'seed':seed,'duplicates':duplicate_leakage(rows,assignments)}

"""Explicit local Ultralytics OBB adapter; never chooses or downloads model weights."""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import hashlib
import importlib.metadata
import io
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import numpy as np
import torch

_TRUSTED_NATIVE_SHA256: set[str] = set()
_TRUST_LOCK = threading.RLock()
_TRUST_ENV = 'MODU_VISION_TRUSTED_YOLO_OBB_SHA256'


def _register_native_trust(digest):
    with _TRUST_LOCK: _TRUSTED_NATIVE_SHA256.add(digest)


def _require_native_trust(digest):
    """Authorization belongs to this host/process, never the packaged metadata."""
    entries={value.strip().lower() for value in os.environ.get(_TRUST_ENV,'').split(',') if value.strip()}
    if any(len(value)!=64 or any(char not in '0123456789abcdef' for char in value) for value in entries):
        raise ValueError(f'{_TRUST_ENV} requires exact native SHA256 digests; wildcard trust is unsupported')
    with _TRUST_LOCK: trusted=digest in _TRUSTED_NATIVE_SHA256 or digest in entries
    if not trusted: raise ValueError(f'YOLO OBB native weights require explicit host trust for SHA256 {digest}')


@dataclass(frozen=True)
class OBBRecipe:
    adapter: str = 'fixed_slot_cnn'
    angle_convention: str = 'clockwise_degrees_axial_180'
    direction_schema: str = 'none'
    empty_background_policy: str = 'explicit_empty_objects'
    model_path: str | None = None
    trust_native_weights: bool = False

    @classmethod
    def from_value(cls,value=None):
        if isinstance(value,cls): return value
        if value is None: return cls()
        if not isinstance(value,dict) or set(value)-set(cls.__dataclass_fields__): raise ValueError('Invalid OBB recipe fields')
        result=cls(**value)
        if result.adapter not in ('fixed_slot_cnn','ultralytics_yolo_obb'): raise ValueError('Unknown OBB adapter')
        if result.angle_convention!='clockwise_degrees_axial_180': raise ValueError('OBB requires clockwise axial angles modulo 180')
        if result.direction_schema not in ('none','unit_vector_360'): raise ValueError('Unknown independent direction schema')
        if result.empty_background_policy!='explicit_empty_objects': raise ValueError('Background requires explicit empty objects')
        if type(result.trust_native_weights) is not bool: raise ValueError('Native weights trust acknowledgement must be boolean')
        return result

    def to_dict(self): return asdict(self)


def _digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _local_model(recipe):
    path=Path(recipe.model_path).expanduser() if isinstance(recipe.model_path,str) and recipe.model_path else None
    if path is None or not path.is_absolute() or path.is_symlink() or not path.is_file() or path.suffix.lower()!='.pt':
        raise ValueError('YOLO OBB needs an explicit absolute local regular model .pt; arbitrary YAML and automatic model download are disabled')
    return path.resolve()


def validate_training_recipe(manifest,recipe,warm_start=None):
    if recipe.adapter=='fixed_slot_cnn':
        counts={image.image:0 for image in manifest.images}
        for record in manifest.records: counts[record.image]+=1
        if any(count==0 or count>32 for count in counts.values()):
            raise ValueError('The fixed_slot_cnn adapter supports 1-32 objects per image; explicitly select YOLO OBB for empty backgrounds or larger counts')
        return
    _local_model(recipe)
    if manifest.direction_enabled or recipe.direction_schema!='none': raise ValueError('YOLO OBB predicts axial geometry only; independent direction is unsupported')
    if warm_start is not None: raise ValueError('YOLO OBB warm start requires an explicitly selected local model; fixed-slot parent migration is unsupported')
    train_classes={row.label for row in manifest.records if row.split=='train'}
    if set(manifest.class_names)-train_classes: raise ValueError('YOLO OBB evaluation contains a class absent from train')
    if not recipe.trust_native_weights: raise ValueError('YOLO OBB training requires explicit native weights trust acknowledgement')


def _runtime():
    try:
        from ultralytics import YOLO
    except ImportError as exc: raise ValueError('The optional Ultralytics OBB runtime is unavailable') from exc
    return YOLO


def export_dataset(manifest,output):
    """Create derived images/labels without modifying original source bytes."""
    from backend.engine.rotated_detection import _points
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    groups={image.image:[] for image in manifest.images}
    for row in manifest.records: groups[row.image].append(row)
    for index,image in enumerate(manifest.images):
        if _digest(image.path)!=image.source_sha256: raise ValueError('Rotated source changed after validation')
        image_dir=output/'images'/image.split;label_dir=output/'labels'/image.split
        image_dir.mkdir(parents=True,exist_ok=True);label_dir.mkdir(parents=True,exist_ok=True)
        destination=image_dir/f'{index:06d}{image.path.suffix.lower()}'
        shutil.copyfile(image.path,destination)
        if _digest(destination)!=image.source_sha256: raise ValueError('Rotated source changed during dataset export')
        lines=[]
        for row in groups[image.image]:
            points=_points(row.box)/np.asarray(image.size)
            lines.append(str(manifest.class_names.index(row.label))+' '+' '.join(f'{value:.9f}' for value in points.reshape(-1)))
        (label_dir/f'{index:06d}.txt').write_text('\n'.join(lines)+('\n' if lines else ''),encoding='utf-8')
    # JSON is a YAML subset, avoiding serialization ambiguity for Unicode class names.
    config={'path':str(output.resolve()),'train':'images/train','val':'images/val','test':'images/test',
            'names':{index:label for index,label in enumerate(manifest.class_names)}}
    path=output/'dataset.yaml';path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf-8')
    return path


def _metrics(result):
    box=getattr(result,'box',None)
    values={'mAP_50':getattr(box,'map50',None),'mAP_50_95':getattr(box,'map',None)}
    return {key:float(value) for key,value in values.items() if value is not None and math.isfinite(float(value))}


def train_yolo(manifest,output,recipe,*,epochs,batch_size,image_size,learning_rate,device,cancel_event=None):
    local=_local_model(recipe)
    if not recipe.trust_native_weights: raise ValueError('YOLO OBB training requires explicit native weights trust acknowledgement')
    initial_bytes=local.read_bytes();initial_digest=hashlib.sha256(initial_bytes).hexdigest()
    # Native deserialization receives the exact bytes acknowledged by this request.
    with tempfile.TemporaryDirectory(prefix='modu-obb-initial-') as temporary:
        selected=Path(temporary)/'local-obb.pt';selected.write_bytes(initial_bytes)
        _register_native_trust(initial_digest);_require_native_trust(initial_digest)
        return _train_yolo_verified(manifest,output,recipe,local,selected,initial_digest,epochs=epochs,batch_size=batch_size,
            image_size=image_size,learning_rate=learning_rate,device=device,cancel_event=cancel_event)


def _train_yolo_verified(manifest,output,recipe,local,selected,initial_digest,*,epochs,batch_size,image_size,learning_rate,device,cancel_event):
    from backend.engine.rotated_detection import RotatedTrainingCancelled
    model=_runtime()(str(selected),task='obb')
    if getattr(model,'task',None)!='obb': raise ValueError('Selected local model is not an OBB model')
    def cancellation(trainer):
        if cancel_event is not None and cancel_event.is_set(): raise RotatedTrainingCancelled()
    for event in ('on_train_batch_start','on_train_epoch_start','on_train_end'): model.add_callback(event,cancellation)
    output=Path(output).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    data=export_dataset(manifest,output/'yolo_dataset')
    result=model.train(data=str(data),epochs=epochs,batch=batch_size,imgsz=image_size,lr0=learning_rate,
        device=str(device),project=str(output),name='ultralytics_run',exist_ok=True,workers=0,
        pretrained=False,plots=False,amp=False)
    cancellation(None)
    completed = int(model.trainer.epoch) + 1
    if not 1 <= completed <= epochs:
        raise ValueError('YOLO OBB returned an invalid completed epoch count')
    if _digest(local)!=initial_digest:raise ValueError('The selected local model changed during training')
    best=Path(model.trainer.best)
    if not best.is_file(): raise ValueError('YOLO OBB training did not produce a best checkpoint')
    native_bytes=best.read_bytes()
    payload={'task':'rotated_detection','version':3,'adapter':recipe.adapter,'model_state_dict':{},
        'class_name':manifest.class_name,'class_names':list(manifest.class_names),'image_size':image_size,
        'direction_enabled':False,'dataset_sha256':manifest.provenance['dataset_sha256'],'provenance':manifest.provenance,
        'recipe':recipe.to_dict(),'native_model_bytes':native_bytes,'native_model_sha256':hashlib.sha256(native_bytes).hexdigest()}
    torch.save(payload,output/'best_model.pt')
    try:runtime_version=importlib.metadata.version('ultralytics')
    except importlib.metadata.PackageNotFoundError:runtime_version='unavailable'
    metadata={'task':'rotated_detection','version':3,'adapter':recipe.adapter,'recipe':recipe.to_dict(),
        'class_name':manifest.class_name,'class_names':list(manifest.class_names),'image_size':image_size,
        'direction_enabled':False,'angle_convention':recipe.angle_convention,'dataset_sha256':manifest.provenance['dataset_sha256'],
        'provenance':manifest.provenance,'checkpoint_sha256':_digest(output/'best_model.pt'),
        'initial_model_sha256':initial_digest,'initial_model_path':str(local),'runtime':{'name':'ultralytics','version':runtime_version},
        'native_model_sha256':payload['native_model_sha256'],
        'license':{'runtime':'AGPL-3.0 or separately obtained Enterprise terms','weights':'caller supplied local model; provenance requires review',
                   'distribution_status':'pending_review','source_url':'https://www.ultralytics.com/license'},
        'validation':_metrics(result),'epochs_completed':completed,'training_samples':manifest.provenance['split_counts']['train']}
    (output/'model_meta.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    _register_native_trust(payload['native_model_sha256'])
    return metadata


def _verified_payload(checkpoint):
    checkpoint=Path(checkpoint).expanduser();path=checkpoint.parent/'model_meta.json'
    if not path.is_file(): return None
    if path.is_symlink(): raise ValueError('Rotated model metadata must be a regular file')
    try:meta=json.loads(path.read_text(encoding='utf-8'))
    except (ValueError,OSError) as exc: raise ValueError('Rotated checkpoint metadata is invalid') from exc
    if meta.get('adapter')!='ultralytics_yolo_obb': return None
    if checkpoint.is_symlink() or not checkpoint.is_file() or meta.get('task')!='rotated_detection' or meta.get('version')!=3:
        raise ValueError('YOLO OBB checkpoint provenance or hash is invalid')
    snapshot=checkpoint.read_bytes()
    if meta.get('checkpoint_sha256')!=hashlib.sha256(snapshot).hexdigest(): raise ValueError('YOLO OBB checkpoint provenance or hash is invalid')
    if not isinstance(meta.get('class_names'),list) or not meta['class_names']: raise ValueError('YOLO OBB class metadata is invalid')
    recipe=OBBRecipe.from_value(meta.get('recipe'))
    if recipe.adapter!='ultralytics_yolo_obb':raise ValueError('YOLO OBB recipe differs from adapter metadata')
    try:payload=torch.load(io.BytesIO(snapshot),map_location='cpu',weights_only=True)
    except Exception as exc:raise ValueError('YOLO OBB checkpoint cannot be loaded safely') from exc
    if not isinstance(payload,dict) or any(payload.get(key)!=meta.get(key) for key in ('task','version','adapter','class_names','image_size','dataset_sha256','native_model_sha256','recipe')):
        raise ValueError('YOLO OBB checkpoint signature differs from metadata')
    native=payload.get('native_model_bytes')
    if not isinstance(native,bytes) or hashlib.sha256(native).hexdigest()!=meta.get('native_model_sha256'):
        raise ValueError('YOLO OBB native model checksum differs')
    return meta,payload


def adapter_metadata(checkpoint):
    verified=_verified_payload(checkpoint)
    return verified[0] if verified else None


@contextmanager
def _model(checkpoint,verified=None):
    """Unpack a hash-checked native model only inside the explicit runtime boundary."""
    verified=verified or _verified_payload(checkpoint)
    if verified is None: raise ValueError('Checkpoint is not a YOLO OBB adapter')
    meta,payload=verified
    _require_native_trust(meta['native_model_sha256'])
    with tempfile.TemporaryDirectory(prefix='modu-obb-runtime-') as temporary:
        native=Path(temporary)/'local-obb.pt';native.write_bytes(payload['native_model_bytes'])
        model=_runtime()(str(native),task='obb')
        if getattr(model,'task',None)!='obb': raise ValueError('Checkpoint runtime is not an OBB model')
        yield model


def _prediction_device(device):
    # The SDK accepts an explicit device without resetting caller CPU threads.
    selected = str(device)
    return torch.device('cpu') if selected == 'cpu' else selected


def predict_yolo_array(checkpoint,image_rgb,*,device='cpu',threshold=.5,meta=None):
    if not isinstance(image_rgb,np.ndarray) or image_rgb.ndim!=3 or image_rgb.shape[2]!=3 or image_rgb.dtype!=np.uint8 or not image_rgb.shape[0] or not image_rgb.shape[1]:
        raise ValueError('YOLO OBB requires a nonempty uint8 RGB image')
    if not math.isfinite(threshold) or not 0<=threshold<=1: raise ValueError('Rotated threshold must be [0,1]')
    verified=_verified_payload(checkpoint)
    if verified is None: raise ValueError('Checkpoint is not a YOLO OBB adapter')
    current=verified[0]
    if meta is not None and meta.get('checkpoint_sha256')!=current['checkpoint_sha256']: raise ValueError('YOLO OBB checkpoint changed during evaluation')
    meta=current
    height,width=image_rgb.shape[:2]
    # Ultralytics ndarray input uses BGR; app/flow contract is RGB.
    with _model(checkpoint,verified) as model:
        results=model.predict(source=np.ascontiguousarray(image_rgb[:,:,::-1]),device=_prediction_device(device),imgsz=meta['image_size'],conf=max(threshold,.001),max_det=10000,verbose=False,save=False)
    detections=[]
    for result in results:
        obb=result.obb
        if obb is None: continue
        geometry=obb.xywhr.detach().cpu().numpy();corners=obb.xyxyxyxy.detach().cpu().numpy()
        classes=obb.cls.detach().cpu().numpy();scores=obb.conf.detach().cpu().numpy()
        if not(len(geometry)==len(corners)==len(classes)==len(scores)): raise ValueError('YOLO OBB returned inconsistent result arrays')
        for values,polygon,index,score in zip(geometry,corners,classes,scores):
            index=int(index);score=float(score)
            if not 0<=index<len(meta['class_names']) or not np.isfinite(values).all() or not np.isfinite(polygon).all() or not math.isfinite(score) or not 0<=score<=1: raise ValueError('YOLO OBB returned invalid classes or geometry')
            if score<threshold:continue
            cx,cy,w,h,angle=map(float,values)
            if w<=0 or h<=0: raise ValueError('YOLO OBB returned an empty box')
            box={'cx':cx,'cy':cy,'width':w,'height':h,'angle_deg':(math.degrees(angle)+90)%180-90}
            detections.append({'label':meta['class_names'][index],'confidence':score,'box':box,'polygon':polygon.astype(float).tolist(),
                'axis_aligned_box':[float(polygon[:,0].min()),float(polygon[:,1].min()),float(polygon[:,0].max()),float(polygon[:,1].max())]})
    return {'task':'rotated_detection','version':3,'adapter':'ultralytics_yolo_obb','detections':detections,
        'image_size':[width,height],'model_sha256':meta['checkpoint_sha256'],'coordinate_space':'original_image_pixels',
        'angle_convention':'clockwise_degrees_axial_180','direction_supported':False}


def evaluate_yolo(checkpoint,manifest,*,split,device,allow_dataset_revision=False):
    from PIL import Image
    from backend.engine.evaluation_evidence import match_objects,object_average_precision
    verified=_verified_payload(checkpoint)
    if verified is None: raise ValueError('Checkpoint is not a YOLO OBB adapter')
    meta=verified[0];revised=meta['dataset_sha256']!=manifest.provenance['dataset_sha256']
    if revised and not allow_dataset_revision: raise ValueError('Rotated evaluation dataset differs from checkpoint source')
    if revised and manifest.provenance['source_sha256']!=meta['provenance']['source_sha256']: raise ValueError('Rotated source images differ from checkpoint lineage')
    if not set(manifest.class_names).issubset(meta['class_names']): raise ValueError('Rotated evaluation class differs from checkpoint')
    images=[image for image in manifest.images if image.split==split]
    if not images: raise ValueError(f'Rotated {split} split has no samples')
    samples=[]
    for image in images:
        if _digest(image.path)!=image.source_sha256: raise ValueError('Rotated source changed after validation')
        with Image.open(image.path) as opened:rgb=np.asarray(opened.convert('RGB'))
        predictions=predict_yolo_array(checkpoint,rgb,device=device,threshold=.001,meta=meta)['detections']
        truth=[{'label':r.label,'box':r.box} for r in manifest.records if r.image==image.image]
        evidence=match_objects(predictions,truth)
        samples.append({'image':image.image,'source_sha256':image.source_sha256,'object_evidence':evidence})
    with tempfile.TemporaryDirectory(prefix='modu-obb-eval-') as temporary:
        data=export_dataset(manifest,temporary)
        with _model(checkpoint,verified) as model:
            runtime_metrics=_metrics(model.val(data=str(data),split=split,device=str(device),imgsz=meta['image_size'],workers=0,plots=False,verbose=False,project=temporary,name='validation'))
    tp=sum(s['object_evidence']['counts']['tp'] for s in samples);fp=sum(s['object_evidence']['counts']['fp'] for s in samples);fn=sum(s['object_evidence']['counts']['fn'] for s in samples)
    matched=[m['iou'] for s in samples for m in s['object_evidence']['matches']]
    angles=[m['angle_error_deg'] for s in samples for m in s['object_evidence']['matches']]
    return {'task':'rotated_detection','adapter':meta['adapter'],'split':split,'sample_count':len(samples),
        'ground_truth_objects':tp+fn,'predicted_objects':tp+fp,'precision':tp/(tp+fp) if tp+fp else 0.,'recall':tp/(tp+fn) if tp+fn else 0.,
        'mean_oriented_iou':sum(matched)/len(matched) if matched else 0.,'samples':samples,
        'mean_angle_error_deg':sum(angles)/len(angles) if angles else None,'matched_objects':len(matched),
        'angle_convention':'clockwise_degrees_axial_180','direction_supported':False,
        'dataset_sha256':manifest.provenance['dataset_sha256'],'training_dataset_sha256':meta['dataset_sha256'],
        'dataset_revision_changed':revised,'model_sha256':meta['checkpoint_sha256'],'runtime_metrics':runtime_metrics,**object_average_precision(samples)}

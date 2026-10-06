"""Explicit core-model trial adapters. Timing measures no production pipeline."""
from pathlib import Path
import base64,hashlib,io,json,time
from pydantic import BaseModel,ConfigDict,Field
import numpy as np
from PIL import Image
import torch

class CorePredictRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    image_path:str
    threshold:float|None=Field(None,ge=0,le=1,allow_inf_nan=False)
    score_spec:dict|None=None

class CoreBenchmarkRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    iterations:int=Field(25,ge=5,le=100,strict=True)
    resolution:int=Field(256,ge=32,le=2048,strict=True)


def execute_core_trial(task,stage,checkpoint,inputs,options,device):
    from backend.engine.runtime_device import resolve_runtime_device
    target=resolve_runtime_device(device);checkpoint=Path(checkpoint)
    payload=torch.load(checkpoint,map_location='cpu',weights_only=True)
    meta=json.loads(checkpoint.with_name('model_meta.json').read_text())
    if meta.get('task')!=task or payload.get('task',task)!=task:raise ValueError('Core trial checkpoint task differs')
    if stage=='predict':
        from backend.engine.native_patches import validate_image_size,DEFAULT_MAX_IMAGE_PIXELS
        raw=Path(inputs['image']).read_bytes()
        with Image.open(io.BytesIO(raw)) as opened:
            validate_image_size(*opened.size,DEFAULT_MAX_IMAGE_PIXELS);rgb=np.asarray(opened.convert('RGB')).copy()
        from backend.engine.trainer import infer
        result=infer(task,checkpoint,rgb,threshold=options.get('threshold'),device=target,score_spec=options.get('score_spec'))
        stream=io.BytesIO();Image.fromarray(result.visual_overlay).save(stream,format='PNG')
        return {'task':task,'predictions':result.predictions,'confidence_score':result.confidence_score,'latency_ms':result.latency_ms,
                'overlay_base64':'data:image/png;base64,'+base64.b64encode(stream.getvalue()).decode(),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'input_size':list(rgb.shape[1::-1]),'quality_approved':False}
    from backend.engine.exporter import load_checkpoint_and_reconstruct_model
    model,loaded,anomaly=load_checkpoint_and_reconstruct_model(checkpoint)
    if loaded.get('task')!=task:raise ValueError('Benchmark reconstructed another task')
    model=model.to(target).eval();resolution=options['resolution'];iterations=options['iterations']
    dummy=torch.rand((1,3,resolution,resolution),dtype=torch.float32,device=target)
    def synchronize():
        if target.type=='cuda':torch.cuda.synchronize(target)
        elif target.type=='mps':torch.mps.synchronize()
    timings=[]
    with torch.inference_mode():
        for _ in range(3):model(dummy);synchronize()
        for _ in range(iterations):
            started=time.perf_counter();model(dummy);synchronize();timings.append((time.perf_counter()-started)*1000)
    scope='feature_extractor_forward_only' if task=='anomaly' and loaded.get('detector_type')!='dino_synthetic' else 'model_forward_only'
    mean=float(np.mean(timings));name=torch.cuda.get_device_name(target) if target.type=='cuda' else 'Metal MPS' if target.type=='mps' else 'CPU'
    return {'status':'success','task':task,'device':str(target),'device_name':name,'iterations':iterations,'input_kind':'synthetic_random_tensor',
            'measurement_scope':scope,'batch_size':1,'resolution':f'{resolution}x{resolution}','mean_latency_ms':round(mean,4),
            'p95_latency_ms':round(float(np.percentile(timings,95)),4),'min_latency_ms':round(float(np.min(timings)),4),
            'max_latency_ms':round(float(np.max(timings)),4),'std_latency_ms':round(float(np.std(timings)),4),'fps':round(1000/mean,1),
            'inspection_or_pipeline_timing':False,'quality_approved':False}

"""Device-explicit native family recipes shared by the API and owned worker."""
import base64
import hashlib
import io
import json
from pathlib import Path
import numpy as np
from PIL import Image
from pydantic import BaseModel,ConfigDict
from typing import Literal
from backend.engine.runtime_device import resolve_runtime_device

FAMILIES=('classification','segmentation','detection','anomaly','patch_classification',
          'rotation','ocr','rotated_detection','enhancement','defect_gan')
SPECIALISTS=FAMILIES[5:]

class PatchEvaluationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id:str
    dataset_path:str
    split:Literal['val','test']='test'


def support_matrix():
    return {task:{'train':True,'evaluate':True,'predict':task!='defect_gan','generate':task=='defect_gan','flow':task!='defect_gan',
                  'quality_approved':False,'adapter':'native_trial_recipe_and_frozen_flow' if task in FAMILIES[:4] else 'native_recipe',
                  'evaluation_scope':'RGB-statistics diagnostic' if task=='defect_gan' else 'explicit heldout',
                  'generation_only':task=='defect_gan',
                  'benchmark':task in FAMILIES[:4],
                  'benchmark_scope':'features or DINO export forward; never pipeline timing' if task=='anomaly' else 'model_forward_only' if task in FAMILIES[:4] else 'unsupported; use saved flow timing',
                  'native_recipe_stages':(['predict','benchmark'] if task in FAMILIES[:4] else ['evaluate','generate'] if task=='defect_gan' else ['evaluate','predict'] if task in SPECIALISTS or task=='patch_classification' else []),
                  'target_acceptance':'pending; CPU control execution is not physical GPU qualification'} for task in FAMILIES}


def request_model(task,stage):
    if task=='patch_classification' and stage=='evaluate':return PatchEvaluationRequest
    if task in FAMILIES[:4]:
        from backend.engine.core_model_trials import CorePredictRequest,CoreBenchmarkRequest
        if stage in ('predict','benchmark'):return CorePredictRequest if stage=='predict' else CoreBenchmarkRequest
    from backend.api import routes_rotation as rotation, routes_ocr as ocr, routes_enhancement as enhancement
    from backend.api import routes_rotated_detection as obb, routes_defect_gan as gan, routes_patch_classification as patch
    table={('rotation','evaluate'):rotation.EvaluateRequest,('rotation','predict'):rotation.PredictRequest,
           ('ocr','evaluate'):ocr.OCREvaluateRequest,('ocr','predict'):ocr.OCRPredictRequest,
           ('rotated_detection','evaluate'):obb.EvaluateRequest,('rotated_detection','predict'):obb.PredictRequest,
           ('enhancement','evaluate'):enhancement.Evaluate,('enhancement','predict'):enhancement.Predict,
           ('defect_gan','evaluate'):gan.GANEvaluateRequest,('defect_gan','generate'):gan.GANGenerateRequest,
           ('patch_classification','predict'):patch.PredictRequest}
    if (task,stage) not in table:raise ValueError('This model stage has no native recipe adapter; use its frozen flow or comparison')
    return table[task,stage]


def _encoded(rgb):
    stream=io.BytesIO();Image.fromarray(rgb).save(stream,format='PNG')
    return base64.b64encode(stream.getvalue()).decode('ascii')


def run_recipe(task,stage,checkpoint,inputs,options,output,device):
    # This check precedes loading the model and prevents the legacy engine's
    # get_device() fallback from silently changing a requested accelerator.
    target=str(resolve_runtime_device(device));checkpoint=Path(checkpoint);output=Path(output)
    request_model(task,stage)
    if task in FAMILIES[:4]:
        from backend.engine.core_model_trials import execute_core_trial
        result=execute_core_trial(task,stage,checkpoint,inputs,options,target)
    elif stage=='evaluate':
        dataset=inputs['dataset'];split=options.get('split','test')
        if split not in ('val','test'):raise ValueError('Recipe evaluation requires heldout val or test')
        if task=='patch_classification':
            from backend.api.routes_evaluation import _evaluate_patch_classification
            metadata=json.loads(checkpoint.with_name('model_meta.json').read_text())
            result=_evaluate_patch_classification(checkpoint,metadata,Path(dataset),target,selected_split=split)
            for row in result['test_predictions']:
                row['image']=Path(row['file_path']).relative_to(dataset).as_posix()
                row['file_path']='@prepared/'+row['image'];row.pop('thumbnail_url',None)
            result['confusion_matrix']['cell_samples']={}
            result.update(task=task,dataset_sha256=result['dataset_provenance']['dataset_sha256'],quality_approved=False)
        elif task=='rotation':
            from backend.engine.rotation import evaluate_rotation_checkpoint
            result=evaluate_rotation_checkpoint(checkpoint,dataset,split=split,device=target)
        elif task=='ocr':
            from backend.engine.ocr import evaluate_ocr_checkpoint
            result=evaluate_ocr_checkpoint(checkpoint,dataset,split=split,device=target)
        elif task=='rotated_detection':
            from backend.engine.rotated_detection import evaluate_rotated_detector
            result=evaluate_rotated_detector(checkpoint,dataset,split=split,device=target)
        elif task=='enhancement':
            from backend.engine.enhancement import evaluate_enhancement
            result=evaluate_enhancement(checkpoint,dataset,split=split,device=target)
        else:
            from backend.engine.defect_gan import evaluate_defect_generator
            result=evaluate_defect_generator(checkpoint,dataset,split=split,count=options.get('count',8),seed=options.get('seed',0),device=target)
    elif stage=='generate':
        from backend.engine.defect_gan import generate_defect_candidates,generate_composited_candidates
        if inputs.get('image'):
            result=generate_composited_candidates(checkpoint,inputs['image'],output/'candidates',regions=options['regions'],
                count=options['count'],seed=options['seed'],device=target,source_sha256=options.get('source_sha256'))
        else:
            if options.get('regions') or options.get('source_sha256'):raise ValueError('GAN composition needs its original image')
            result=generate_defect_candidates(checkpoint,output/'candidates',count=options['count'],seed=options['seed'],device=target)
        # Portable output references stay relative until verified download.
        for row in result['candidates']:row['path']=Path(row['path']).relative_to(output).as_posix()
        if result.get('source_snapshot'):result['source_snapshot']=Path(result['source_snapshot']).relative_to(output).as_posix()
        if result.get('source_image_path'):result['source_image_path']='@original_image'
    else:
        image=Path(inputs['image']);raw=image.read_bytes()
        from backend.engine.native_patches import validate_image_size,DEFAULT_MAX_IMAGE_PIXELS
        with Image.open(io.BytesIO(raw)) as opened:
            validate_image_size(*opened.size,DEFAULT_MAX_IMAGE_PIXELS)
            rgb=np.asarray(opened.convert('RGB')).copy()
        if task=='rotation':
            from backend.engine.rotation import predict_rotation_array
            result=predict_rotation_array(checkpoint,rgb,device=target);aligned=result.pop('aligned_image')
            for key in ('transform','inverse_transform'):result[key]=result[key].tolist()
            if options.get('include_aligned'):result['aligned_image_base64']=_encoded(aligned)
        elif task=='ocr':
            from backend.engine.ocr import predict_ocr
            result=predict_ocr(checkpoint,image,device=target,recipe=options.get('recipe'))
            if options.get('include_preview'):
                preview=Image.fromarray(rgb);preview.thumbnail((800,600),Image.Resampling.BILINEAR)
                result['preview_data_url']='data:image/png;base64,'+_encoded(np.asarray(preview))
        elif task=='enhancement':
            from backend.engine.enhancement import predict_enhancement
            enhanced=predict_enhancement(checkpoint,rgb,target)
            result={'image_base64':_encoded(enhanced),'width':enhanced.shape[1],'height':enhanced.shape[0]}
        elif task=='patch_classification':
            from backend.engine.patch_classification import predict_patch_classification,patch_score_preview
            result=predict_patch_classification(checkpoint,rgb,device=target,source_id=options['image_path'],recipe=options.get('recipe'))
            for row in result['patches']:row['source_sha256']=hashlib.sha256(raw).hexdigest()
            result.update(patch_score_preview(rgb,result['patches']))
        else:
            from backend.engine.rotated_detection import predict_rotated_box
            result=predict_rotated_box(checkpoint,image,device=target)
            from PIL import ImageDraw
            preview=Image.fromarray(rgb);preview.thumbnail((480,320),Image.Resampling.BILINEAR)
            sx,sy=preview.width/rgb.shape[1],preview.height/rgb.shape[0]
            for row in result.get('detections',[result]):
                points=[(x*sx,y*sy) for x,y in row['polygon']]
                ImageDraw.Draw(preview).line(points+[points[0]],fill=(34,211,238),width=2)
            result.update(preview_data_url='data:image/png;base64,'+_encoded(np.asarray(preview)),preview_size=list(preview.size))
        result['source_sha256']=hashlib.sha256(raw).hexdigest()
    result.update(job_id=options['job_id'],model_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest())
    return result

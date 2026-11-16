"""Explicit Geti references inside the established native flow and inspection UI."""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
import json
from pathlib import Path
import time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .dataset_transfer import validate_inspection_binding
from .model_runtime import list_platform_models, _resolve, export_platform_model, predict_inspection_image

_ACTIVE = ContextVar('modu_geti_flow_inspection',default=None)


class GetiFlowModelReference(BaseModel):
    model_config=ConfigDict(extra='forbid')
    platform: Literal['geti']='geti'
    transfer_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    model_id: UUID
    variant_id: UUID

    def token(self):
        return f'geti:{self.transfer_id}:{self.model_id}:{self.variant_id}'


def is_platform_reference(value):
    return isinstance(value,str) and value.startswith('geti:')


def parse_reference(value):
    if not isinstance(value,str): raise ValueError('Geti flow reference is required')
    parts=value.split(':')
    if len(parts)!=4 or parts[0]!='geti': raise ValueError('Malformed Geti flow reference')
    reference=GetiFlowModelReference(transfer_id=parts[1],model_id=parts[2],variant_id=parts[3])
    if reference.token()!=value: raise ValueError('Geti flow reference is not canonical')
    return reference


def _services(request,project,source):
    if request is None: raise ValueError('Platform models require an authorized project request')
    from .policy import authority
    current,context,key,_=authority(request)
    if current['id']!=project['id'] or Path(current['project_dir']).resolve()!=Path(project['project_dir']).resolve():
        raise ValueError('Platform flow project authority changed')
    if not source or not project.get('source_dataset_dir') or Path(source).resolve()!=Path(project['source_dataset_dir']).resolve():
        raise ValueError('Platform flow source differs from current project')
    runtime=getattr(request.app.state,'geti_runtime',None); store=getattr(request.app.state,'geti_store',None)
    if runtime is None or store is None: raise ValueError('Platform runtime is not installed')
    return runtime,store,key


def _receipt(project,store,key,reference):
    receipt=store.get_dataset(key)
    if not receipt or receipt.get('id')!=reference.transfer_id:
        roots=[Path(store.root)/'transfers'/key,Path(project['project_dir'])/'platforms'/'geti'/'transfers']
        paths=[root/reference.transfer_id/'receipt.json' for root in roots]
        path=next((path for path in paths if path.is_file() and not path.is_symlink()),None)
        if path is None: raise ValueError('Platform transfer does not belong to this project')
        receipt=json.loads(path.read_text(encoding='utf-8'))
    if receipt.get('id')!=reference.transfer_id: raise ValueError('Platform transfer identity changed')
    validate_inspection_binding(project,receipt)
    return receipt


def resolve_reference(request,project,source,token,task,*,export=False):
    reference=parse_reference(token)
    runtime,store,key=_services(request,project,source)
    receipt=_receipt(project,store,key,reference)
    flow_task=receipt.get('source_task',receipt['task'])
    if task!=flow_task or (receipt['task'] not in ('classification','detection','instance_segmentation')):
        raise ValueError('Platform flow task differs or needs an unsupported native geometry adapter')
    from backend.api.routes_platform_geti import client_for
    client=client_for(runtime,key,request=request)
    model,variant=_resolve(client,receipt,str(reference.model_id),str(reference.variant_id))
    if not variant['prediction_supported']: raise ValueError('Select an OpenVINO or ONNX platform variant')
    result={'reference':reference,'receipt':receipt,'client':client,'model':model,'variant':variant}
    if export:
        artifact=export_platform_model(client,receipt,str(reference.model_id),str(reference.variant_id))
        result.update(artifact={key:value for key,value in artifact.items() if key!='data'},
            artifact_path=Path(receipt['transfer_root'])/'model_exports'/f'{reference.model_id}-{reference.variant_id}.zip')
    return result


def class_metadata(bound):
    names=[row['name'] for row in bound['receipt']['class_mapping']]
    task=bound['receipt'].get('source_task',bound['receipt']['task'])
    return {'task':task,'classes':(['background',*names] if task=='segmentation' else names)}


def catalog_entries(request,project,source):
    if request is None or not project.get('source_dataset_dir') or not source or getattr(request.app.state,'geti_runtime',None) is None:
        return []
    runtime,store,key=_services(request,project,source)
    receipt=store.get_dataset(key)
    if not receipt: return []
    validate_inspection_binding(project,receipt)
    flow_task=receipt.get('source_task',receipt['task'])
    if receipt['task'] not in ('classification','detection','instance_segmentation'): return []
    if flow_task not in ('classification','detection','segmentation'): return []
    if runtime.status(key)['state']=='running':
        from backend.api.routes_platform_geti import client_for
        models=list_platform_models(client_for(runtime,key,request=request),receipt)
    else:
        models=[row for row in (store.get_project(key) or {}).get('models',[]) if row.get('transfer_id')==receipt['id']]
    from backend.engine.class_semantics import class_semantics_record
    classes=[row['name'] for row in receipt['class_mapping']]
    if flow_task=='segmentation': classes=['background',*classes]
    rows=[]
    for model in models:
        for variant in model.get('variants',[]):
            if not variant.get('prediction_supported'): continue
            reference=GetiFlowModelReference(transfer_id=receipt['id'],model_id=model['id'],variant_id=variant['id'])
            settings={}
            if variant.get('optimal_confidence_threshold') is not None:
                settings['optimal_threshold']=variant['optimal_confidence_threshold']
            rows.append({'job_id':reference.token(),'task':flow_task,'platform':'geti','platform_task':receipt['task'],
                'platform_model':reference.model_dump(mode='json'),'format':variant['format'],'precision':variant['precision'],
                'label':f"Geti · {model['name']} · {variant['format']} {variant['precision']}",
                'created_at':(model.get('training_info') or {}).get('end_time') or receipt['created_at'],
                'source_dataset_path':str(source),'class_names':classes,'class_ids':list(range(1 if receipt['task']=='detection' else 0,len(classes)+(1 if receipt['task']=='detection' else 0))),
                'class_semantics':class_semantics_record(classes,task=flow_task),'training_labelset_id':receipt['labelset_id'],
                'dataset_revision_id':model.get('dataset_revision_id'),'dataset_version_id':receipt['dataset_version_id'],
                'threshold_settings':settings,'score_spec':None,'best_metric':None,'preset':None})
    return rows


class FlowSession:
    def __init__(self,request,project,source,image_path):
        self.request=request; self.project=project; self.source=source; self.image_path=Path(image_path)
        self.last={}

    def _predict(self,img_rgb,regions,node):
        import numpy as np
        from backend.engine.industrial_adapters import read_image_safely_rgb
        from backend.engine.flowchart_engine import FlowchartInspectionConfigurationError
        original=read_image_safely_rgb(self.image_path,max_dim=None)
        h,w=original.shape[:2]
        if (not np.array_equal(img_rgb,original) or len(regions)!=1 or regions[0].get('bbox')!=[0,0,w,h]
                or any('image' in roi or roi.get('source_transform') is not None for roi in regions)):
            raise FlowchartInspectionConfigurationError('Geti model requires the admitted whole original image; ROI/transformed inference is unsupported')
        bound=resolve_reference(self.request,self.project,self.source,node.data.model_job_id,node.data.task)
        try:
            result=predict_inspection_image(self.project,bound['client'],bound['receipt'],str(bound['reference'].model_id),str(bound['reference'].variant_id),self.image_path)
        except ValueError as exc:
            raise FlowchartInspectionConfigurationError(str(exc)) from exc
        self.last[node.id]={'kind':'geti_model_prediction',**{key:value for key,value in result.items() if key not in ('raw','predictions')},
            'transfer_id':bound['receipt']['id'],'platform_task':bound['receipt']['task'],'prediction_count':len(result['predictions']),
            **({'instance_predictions':result['predictions']} if bound['receipt']['task']=='instance_segmentation' else {})}
        return result,bound

    def inspect(self,img_rgb,regions,node):
        from backend.engine.flowchart_engine import CropInspectionResult
        from backend.engine.class_semantics import class_role,normal_class_indices
        from backend.engine.flow_operators import image_uri
        started=time.monotonic(); result,bound=self._predict(img_rgb,regions,node)
        if bound['receipt']['task']=='instance_segmentation':
            return self._segment(img_rgb,regions,node,result,bound,started)
        predictions=result['predictions']
        if len(predictions)!=1: raise ValueError('Exclusive Geti classification requires exactly one prediction')
        prediction=predictions[0]; confidence=prediction.get('confidence'); role=class_role(prediction['label'])
        classes=[row['name'] for row in bound['receipt']['class_mapping']]
        threshold=node.data.threshold
        known_normal=bool(normal_class_indices(classes))
        score=(1-confidence if role=='normal' else confidence) if confidence is not None else 0.0
        verdict='REVIEW' if confidence is None or role=='unknown' or not known_normal else ('NG' if score>=threshold else 'OK')
        # Upstream returns the winning class only. Multiple same-role classes
        # provide a bound, not the full summed normal/defect probability.
        if confidence is not None and len(classes)>2:
            same_role=sum(class_role(name)==role for name in classes)
            if same_role>1 and ((role=='normal' and score>=threshold) or (role=='defect' and score<threshold)):
                verdict='REVIEW'
        crop=CropInspectionResult(roi_id=regions[0]['id'],label=regions[0].get('label','Full image'),
            bbox=regions[0]['bbox'],defect_score=score,score_basis='geti_class_probability',verdict=verdict,
            crop_thumbnail=image_uri(img_rgb),flaw_type='플랫폼 이미지 분류',confidence=confidence,predicted_class=prediction['label'])
        return [crop],(time.monotonic()-started)*1000,'passed' if verdict!='REVIEW' else 'review_required'

    def _segment(self,img_rgb,regions,node,result,bound,started):
        import cv2
        import numpy as np
        from backend.engine.flowchart_engine import CropInspectionResult,FlowchartInspectionConfigurationError
        from backend.engine.flow_operators import image_uri
        from backend.engine.segmentation_evidence import encoded_array,filter_components
        names=['background',*[row['name'] for row in bound['receipt']['class_mapping']]]
        if len(names)!=len(set(names)):
            raise FlowchartInspectionConfigurationError('Instance foreground class conflicts with reserved native background channel')
        params=node.data.params
        if params.get('class_names') is not None and params['class_names']!=names:
            raise FlowchartInspectionConfigurationError('Platform instance class mapping differs from flow class names')
        rules={row['class_id']:row for row in params.get('class_rules',[])}
        if any('probability_threshold' in rule for rule in rules.values()):
            raise FlowchartInspectionConfigurationError('Instance masks carry per-object confidence, not pixel probability thresholds')
        selected=params.get('class_ids',list(range(1,len(names))))
        if any(index not in range(1,len(names)) for index in [*selected,*rules]):
            raise FlowchartInspectionConfigurationError('Platform instance class ID is outside recorded mapping')
        masks={i:np.zeros(img_rgb.shape[:2],dtype=np.uint8) for i in range(len(names))}
        scores={i:0.0 for i in range(len(names))}
        for prediction in result['predictions']:
            confidence=prediction.get('confidence')
            if confidence is None: raise FlowchartInspectionConfigurationError('Instance confidence is unavailable')
            if confidence<=node.data.threshold: continue
            index=names.index(prediction['label']); shape=prediction['shape']
            if shape['type']!='polygon': raise FlowchartInspectionConfigurationError('Instance output requires polygons')
            points=np.array([[p['x'],p['y']] for p in shape['points']],dtype=np.float64)
            cv2.fillPoly(masks[index],[np.rint(points).astype(np.int32)],1)
            scores[index]=max(scores[index],confidence)
        union=np.zeros(img_rgb.shape[:2],dtype=np.uint8); entries=[]
        gray=cv2.cvtColor(img_rgb,cv2.COLOR_RGB2GRAY)
        for index,name in enumerate(names[1:],1):
            raw=masks[index]; rule=rules.get(index,{})
            masks[index]=filter_components(raw,rule.get('min_area_px',1),rule.get('max_area_px'))
            if index in selected: union|=masks[index]
            area=int(masks[index].sum())
            entries.append({'class_id':index,'class_name':name,'selected':index in selected,'area_px':area,
                'raw_area_px':int(raw.sum()),'confidence':scores[index],
                'mean_grayscale':float(gray[masks[index]>0].mean()) if area else None,
                'bbox':regions[0]['bbox'],'coordinate_space':'original','mask':encoded_array(masks[index],'uint8'),
                'score_semantics':'instance_confidence_within_polygon'})
        masks[0]=(1-np.maximum.reduce([masks[i] for i in range(1,len(names))])).astype(np.uint8)
        entries.insert(0,{'class_id':0,'class_name':'background','selected':False,'area_px':int(masks[0].sum()),
            'bbox':regions[0]['bbox'],'coordinate_space':'original','mask':encoded_array(masks[0],'uint8')})
        area=int(union.sum()); minimum=params.get('min_defect_area_px',8); maximum=params.get('max_defect_area_px',float('inf'))
        verdict='NG' if area>0 and minimum<=area<=maximum else 'OK'
        active=[row for row in entries if row['selected'] and row['area_px']>0]
        crop=CropInspectionResult(roi_id=regions[0]['id'],label=regions[0].get('label','Full image'),bbox=regions[0]['bbox'],
            defect_score=max((scores[i] for i in selected),default=0.0),score_basis='geti_instance_confidence',
            verdict=verdict,crop_thumbnail=image_uri(img_rgb),flaw_type='플랫폼 인스턴스 polygon 합집합',
            defect_area_px=area,segmentation_classes=entries,mask=image_uri(union,mask=True),map_semantics='instance_polygon_union',
            predicted_class=max(active,key=lambda row:row['area_px'])['class_name'] if active else 'background')
        crop._defect_mask=union; crop._segmentation_masks=masks
        return [crop],(time.monotonic()-started)*1000,'passed'

    def detect(self,img_rgb,regions,node):
        started=time.monotonic(); result,_=self._predict(img_rgb,regions,node)
        detected=[]
        for i,prediction in enumerate(result['predictions']):
            confidence=prediction.get('confidence')
            if confidence is None: raise ValueError('Geti detection confidence is unavailable')
            if confidence<node.data.threshold: continue
            shape=prediction['shape']
            if shape['type']!='rectangle': raise ValueError('Detection needs original-coordinate rectangles')
            detected.append({'id':f'geti:{node.id}:{i}','label':prediction['label'],'confidence':confidence,
                'bbox':[shape['x'],shape['y'],shape['x']+shape['width'],shape['y']+shape['height']],'crop_padding':0})
        return detected,(time.monotonic()-started)*1000,'passed'


@contextmanager
def execution_scope(request,project,pipeline,image_path):
    if not any(is_platform_reference(node.data.model_job_id) for node in pipeline.nodes):
        yield; return
    if not project: raise ValueError('Platform inspection requires a selected project')
    _services(request,project,project.get('source_dataset_dir'))
    token=_ACTIVE.set(FlowSession(request,project,project['source_dataset_dir'],image_path))
    try: yield
    finally: _ACTIVE.reset(token)


def active_session(job_id):
    if not is_platform_reference(job_id): return None
    session=_ACTIVE.get()
    if session is None:
        from backend.engine.flowchart_engine import FlowchartInspectionConfigurationError
        raise FlowchartInspectionConfigurationError('Platform model has no authorized inspection binding')
    return session


def step_artifacts(node):
    session=_ACTIVE.get()
    return [session.last[node.id]] if session is not None and node.id in session.last else []

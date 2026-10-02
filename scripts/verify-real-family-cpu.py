"""Bounded real-pixel acceptance, preserving projects and hash-bound receipts.

This verifies software paths on 64px native crops. All input images are defect
images; background OK, OCR text and rotation targets are controlled functional
proxies. The output is not manufacturing model-quality approval.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import cv2
import numpy as np
from PIL import Image
import torch
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.engine import training_engine as engine
from backend.engine.flowchart_engine import FlowchartEngine,FlowNode,FlowNodeData,FlowEdge,get_single_segmentation_flowchart,get_single_detection_flowchart
from backend.engine.flow_package import build_flow_package
from backend.engine.flow_package_runtime import run_flow_package

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(engine._json(value),ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def make_crops(original,output):
    source=output/'native_crops';source.mkdir(parents=True)
    selected=[path for path in sorted(original.glob('*.jpg')) if path.with_suffix('.json').is_file()][:6]
    assert len(selected)==6
    rows=[];mapping=[];seen=set()
    for index,path in enumerate(selected):
        annotation=path.with_suffix('.json');raw=json.loads(annotation.read_text(encoding='utf-8'));points=np.array(raw['shapes'][0]['points'],np.float32)
        x,y,w,h=cv2.boundingRect(points);split=('train','val','test')[index%3]
        with Image.open(path) as image:
            image=image.convert('RGB');width,height=image.size
            left=max(0,min(width-64,int(x+w/2)-32));top=max(0,min(height-64,int(y+h/2)-32))
            mask=np.zeros((64,64),np.uint8);cv2.fillPoly(mask,[np.round(points-[left,top]).astype(np.int32)],255)
            contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
            contour=max(contours,key=cv2.contourArea);assert cv2.contourArea(contour)>0
            polygon=contour.reshape(-1,2).astype(float).tolist();bx,by,bw,bh=cv2.boundingRect(contour)
            name=f'{index}_Bow.png';image.crop((left,top,left+64,top+64)).save(source/name)
            rows.append({'image':name,'split':split,'label':'Bow','annotations':[{'type':'polygon','label':'Bow','points':polygon}],
                'bbox':[bx,by,bx+bw,by+bh],'box':{'cx':bx+bw/2,'cy':by+bh/2,'width':bw,'height':bh,'angle_deg':0},
                'correction_deg':0,'text':'A','source_sha256':digest(source/name)})
            seen.add(digest(source/name))
            mapping.append({'image':name,'original_image':str(path),'original_sha256':digest(path),'original_labels':str(annotation),
                'original_labels_sha256':digest(annotation),'source_bbox':[left,top,left+64,top+64],'pixel_transform':'native crop; no resize',
                'label_semantics':'Bow polygon raster-clipped from original LabelMe truth'})
            for left,top in [(n*83+10,n*71+10) for n in range(12)]:
                if left+64>width or top+64>height:continue
                crop=image.crop((left,top,left+64,top+64));name=f'{index}_OK_proxy.png';crop.save(source/name)
                if digest(source/name) not in seen:break
            else:raise AssertionError('Cannot find a distinct native background crop')
            seen.add(digest(source/name))
            rows.append({'image':name,'split':split,'label':'OK','annotations':[],'correction_deg':0,'text':'A','source_sha256':digest(source/name)})
            mapping.append({'image':name,'original_image':str(path),'original_sha256':digest(path),'original_labels':str(annotation),
                'original_labels_sha256':digest(annotation),'source_bbox':[left,top,left+64,top+64],'pixel_transform':'native crop; no resize',
                'label_semantics':'Controlled background OK proxy for functional acceptance; healthy manufacturing truth unverified'})
    save(output/'crop_source_manifest.json',mapping);save(output/'external_truth.json',{'samples':rows})
    return source,rows

def flow_for(task,job_id,classification_id=None):
    pipeline=get_single_detection_flowchart(job_id) if task=='detection' else get_single_segmentation_flowchart(job_id)
    if task in {'rotation','enhancement'}:
        for node in pipeline.nodes:
            if node.data.node_type=='inspection':node.data.task='classification';node.data.model_job_id=classification_id
        pipeline.nodes.append(FlowNode(id='learned_preprocess',position={'x':200,'y':160},data=FlowNodeData(label=task,node_type='preprocess',
            model_job_id=job_id,params={'operation':'learned_rotation' if task=='rotation' else 'enhancement'})))
        pipeline.edges[0]=FlowEdge(id='input-preprocess',source='node_input',target='learned_preprocess')
        pipeline.edges.append(FlowEdge(id='preprocess-model',source='learned_preprocess',target='node_inspect'))
    elif task!='detection':
        for node in pipeline.nodes:
            if node.data.node_type=='inspection':
                node.data.task=task
                node.data.params={'expected_text':'A'} if task=='ocr' else {'min_defect_area_px':1} if task=='segmentation' else {}
    return pipeline

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dino-checkpoint',type=Path,required=True,help='Existing verified genuine pretrained DINOv3 small weights; no download')
    args=parser.parse_args();original=args.source.resolve();output=args.output.resolve()
    if output.exists():raise ValueError('Choose a new acceptance output directory; previous evidence is preserved')
    torch.set_num_threads(1);output.mkdir(parents=True)
    original_hashes={path.relative_to(original).as_posix():digest(path) for path in original.rglob('*') if path.is_file()}
    save(output/'original_sha256_before.json',original_hashes)
    source,base_rows=make_crops(original,output)
    dino=args.dino_checkpoint.expanduser().resolve()
    yolo=Path(torch.hub.get_dir())/'checkpoints'/'yolo26n.pt'
    assert dino.is_file() and yolo.is_file(),'Validated local pretrained files required; no downloads'
    pretrained={'pretrained_checkpoint':str(dino),'pretrained_sha256':digest(dino),'image_size':64,'batch_size':2,'augmentation_profile':'none'}
    configurations={
        'classification':{**pretrained,'backbone':'dinov3_vits16'},'detection':{**pretrained,'backbone':'yolo26n','pretrained_checkpoint':str(yolo),'pretrained_sha256':digest(yolo)},
        'segmentation':{**pretrained,'model_name':'dinov3_vits16'},'patch_classification':{**pretrained,'backbone':'dinov3_vits16'},
        'anomaly':{'pretrained_checkpoint':str(dino),'pretrained_sha256':digest(dino),'anomaly_backbone':'dinov3_vits16','patch_size':32,'stride':32,'patches_per_image':2,'batch_size':2},
        'rotation':{'width':8,'image_size':32,'batch_size':2},'ocr':{'image_size':32,'image_width':64,'batch_size':2},
        'rotated_detection':{'image_size':64,'batch_size':2},'enhancement':{'batch_size':2},'defect_gan':{'base_channels':8,'batch_size':2}}
    report={'evidence_scope':'real_native_pixels_functional_CPU_not_manufacturing_quality_approval','original_root':str(original),
        'original_file_count':len(original_hashes),'crop_manifest':str(output/'crop_source_manifest.json'),'truth_controls':{'OK':'background proxy; healthy truth unverified','OCR':'controlled A text; natural glyph accuracy unverified','rotation':'controlled identity correction; physical orientation unverified'},'families':[]}
    app=create_app(project_dir=str(output/'projects'));api=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    checkpoints={};family_runs={}
    for task in engine.TASKS:
        started=time.monotonic();rows=[dict(row) for row in base_rows]
        if task in {'defect_gan','rotated_detection'}:rows=[row for row in rows if row['label']=='Bow']
        if task=='anomaly':rows=[row for row in rows if row['split']!='train' or row['label']=='OK']
        workspace=output/'projects'/task
        preparation=engine.prepare(task=task,source_dataset_path=source,output_dir=workspace,labels={'samples':rows},
            prepare_options={'patch_size':64,'stride':64,'minimum_overlap':.001} if task=='patch_classification' else {})
        queued=engine.create_run(output_dir=workspace,mode='quick',epochs_per_trial=1,config=configurations[task]);run=engine.execute_run(workspace,queued['run_id'])
        assert run['status']=='completed',run
        evaluation=engine.evaluate(workspace,run['run_id']);prediction=engine.predict(workspace,run['run_id'],images=['2_Bow.png'])
        readback=engine.read_run(workspace,run['run_id']);checkpoints[task]=(run['winner']['trial_id'],Path(run['winner']['checkpoint_path']));family_runs[task]=run
        opened=api.post('/api/project/open',json={'project_dir':str(workspace)});assert opened.status_code==200,opened.text
        rest=api.get('/api/engine/status',params={'output_dir':str(workspace),'run_id':run['run_id']});assert rest.status_code==200 and rest.json()['status']=='completed',rest.text
        family={'task':task,'project_dir':str(workspace),'preparation':preparation,'run':readback,'evaluation':evaluation,'prediction':prediction,'native_project_open_http':opened.status_code,'REST_readback_http':rest.status_code,'seconds':time.monotonic()-started}
        if task!='defect_gan':
            pipeline=flow_for(task,checkpoints[task][0],checkpoints['classification'][0]);mapping={checkpoints[task][0]:checkpoints[task][1]}
            if task in {'rotation','enhancement'}:mapping[checkpoints['classification'][0]]=checkpoints['classification'][1]
            package=build_flow_package(pipeline=pipeline,checkpoints=mapping,output_base_dir=workspace/'exports',package_name='actual_native_functional')
            result=run_flow_package(Path(package['package_path']),source/'2_Bow.png',device='cpu')
            family['flow']={'package':package,'result':engine._json(result)}
            save(workspace/'flow_actual_input.json',family['flow'])
        else:family['flow']={'state':'generation_for_human_review','checkpoint_usage':'actual composited source-linked candidate in prediction; no GAN verdict node'}
        report['families'].append(family);save(output/'acceptance.json',report)
        print(json.dumps({'task':task,'status':readback['status'],'project_dir':str(workspace),'seconds':family['seconds']},ensure_ascii=False),flush=True)
    # Exercise cancellation after an actual optimizer update, retaining the first
    # completed rotation checkpoint for native UI inspection.
    workspace=output/'projects'/'rotation';reached=threading.Event()
    queued=engine.create_run(output_dir=workspace,epochs_per_trial=500,config=configurations['rotation'])
    def progress(event):
        if any(row.get('progress',{}).get('batch') for row in event.get('trials',[])):reached.set();engine.cancel_run(workspace,queued['run_id'])
    cancelled=engine.execute_run(workspace,queued['run_id'],progress);assert reached.is_set() and cancelled['status']=='cancelled' and cancelled['winner'] is None,cancelled
    report['actual_cancel']=cancelled
    after={path.relative_to(original).as_posix():digest(path) for path in original.rglob('*') if path.is_file()};save(output/'original_sha256_after.json',after)
    assert original_hashes==after,'Original images or labels changed'
    report.update(originals_unchanged=True,all_families_completed=len(report['families'])==10);save(output/'acceptance.json',report)
    print(json.dumps({'all_families':len(report['families']),'originals_unchanged':True,'report':str(output/'acceptance.json')}),flush=True)

if __name__=='__main__':main()

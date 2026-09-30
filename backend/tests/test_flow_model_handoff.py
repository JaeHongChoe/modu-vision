from pathlib import Path
import json,hashlib
import numpy as np
from PIL import Image
import torch
from backend.engine.ocr import SmallCTCOCR
from backend.engine.flow_package import build_flow_package, verify_flow_parity
from backend.engine.flow_package_runtime import run_flow_package
from backend.engine.flowchart_engine import FlowNode,FlowNodeData,FlowEdge,FlowchartPipeline,FlowchartEngine


def test_package_parity_checks_raw_anomaly_values_even_when_visual_maps_match():
    from backend.engine.flow_package_runtime import compare_flow_results
    import copy
    reference={'crops':[{'defect_score':.5,'anomaly_map':'same PNG','anomaly_values':{'dtype':'float32','encoding':'base64','shape':[1,1],'data':'AAAAAA=='}}]}
    modified=copy.deepcopy(reference);modified['crops'][0]['anomaly_values']['data']='AACAPw=='
    assert 'crops[0].anomaly_values' in compare_flow_results(reference,modified)['mismatched_fields']


def test_ocr_rules_rotation_and_package_share_checkpoint_adapter(tmp_path:Path):
    job='a'*32;modeldir=tmp_path/'model';modeldir.mkdir();checkpoint=modeldir/'best_model.pt'
    model=SmallCTCOCR(1)
    for p in model.parameters(): p.data.zero_()
    model.head.bias.data[0] = 20
    # Empty decoded output is real model behavior; only an explicit regex accepts it.
    torch.save({'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],'model_state_dict':model.state_dict(),'dataset_provenance':{'dataset_sha256':'truth'},'best_epoch':1},checkpoint)
    nodes=[FlowNode(id=n,position={},data=FlowNodeData(label=n,node_type=t,task='ocr' if n=='ocr' else None,model_job_id=job if n=='ocr' else None,params={'operation':'rotate','angle_deg':90} if n=='rotate' else {'regex':'^$'} if n=='ocr' else {})) for n,t in [('input','input'),('rotate','preprocess'),('ocr','inspection'),('decision','decision'),('output','output')]]
    graph=FlowchartPipeline(nodes=nodes,edges=[FlowEdge(id=f'e{i}',source=nodes[i].id,target=nodes[i+1].id) for i in range(4)])
    image=tmp_path/'source.png';Image.fromarray(np.full((32,64,3),127,dtype=np.uint8)).save(image)
    package=build_flow_package(pipeline=graph,checkpoints={job:checkpoint},output_base_dir=tmp_path/'packages',package_name='ocr')
    result=run_flow_package(Path(package['package_path']),image,device='cpu')
    assert result['final_verdict']=='OK'
    assert result['crops'][0]['recognized_text']==''
    assert result['crops'][0]['source_transform'] is not None
    parity=verify_flow_parity(package_dir=Path(package['package_path']),pipeline=graph,checkpoints={job:checkpoint},image_path=image)
    assert parity['status']=='passed'


def test_anomaly_region_mask_reaches_blob_and_artifacts(monkeypatch):
    class Model:
        def predict_anomaly_map(self,tensor):
            values=np.zeros((16,16),np.float32);values[2:5,2:5]=0.9
            return values,0.9
    nodes=[FlowNode(id=n,position={},data=FlowNodeData(label=n,node_type=t,task='anomaly' if n=='anomaly' else None,model_job_id='job_anomaly' if n=='anomaly' else None,params={'anomaly_mode':'region','min_defect_area_px':2} if n=='anomaly' else {})) for n,t in [('input','input'),('anomaly','inspection'),('blob','blob_measure'),('decision','decision'),('output','output')]]
    graph=FlowchartPipeline(nodes=nodes,edges=[FlowEdge(id=f'e{i}',source=nodes[i].id,target=nodes[i+1].id) for i in range(4)])
    engine=FlowchartEngine(device='cpu');monkeypatch.setattr(engine,'_get_inspection_model',lambda **kw:(Model(),True));monkeypatch.setattr(engine,'_resolve_checkpoint',lambda *a:None)
    result=engine.execute(pipeline=graph,image=np.zeros((32,32,3),np.uint8))
    assert result['final_verdict']=='NG'
    assert result['crops'][0]['blob_count']==1
    assert result['crops'][0]['mask'].startswith('data:')
    assert result['crops'][0]['anomaly_values']['shape']==[16,16]
    assert next(s for s in result['execution_steps'] if s['node_id']=='anomaly')['artifacts'][0]['mask']


def test_multi_class_rotated_alignment_package_parity(tmp_path:Path):
    from backend.engine.rotated_detection import RotatedMultiBoxNet
    rotated='d'*32;ocr='e'*32
    rotated_dir=tmp_path/'rotated';rotated_dir.mkdir();rotated_checkpoint=rotated_dir/'best_model.pt'
    model=RotatedMultiBoxNet(2,2)
    for parameter in model.parameters():parameter.data.zero_()
    import math
    def logit(value):return math.log(value/(1-value))
    biases=[]
    for center,class_logits in [(0.25,[10,-10]),(0.75,[-10,10])]:
        biases.extend([logit(center),logit(center),logit(0.15),logit(0.1),0,1,10,*class_logits])
    model.head[-1].bias.data.copy_(torch.tensor(biases))
    metadata={'task':'rotated_detection','version':2,'class_name':'a','class_names':['a','b'],'max_objects':2,'image_size':64}
    torch.save({**metadata,'model_state_dict':model.state_dict()},rotated_checkpoint)
    (rotated_dir/'model_meta.json').write_text(json.dumps({**metadata,'checkpoint_sha256':hashlib.sha256(rotated_checkpoint.read_bytes()).hexdigest()}))
    ocr_dir=tmp_path/'ocr';ocr_dir.mkdir();ocr_checkpoint=ocr_dir/'best_model.pt'
    recognizer=SmallCTCOCR(1)
    for parameter in recognizer.parameters():parameter.data.zero_()
    recognizer.head.bias.data[0]=20
    torch.save({'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],'model_state_dict':recognizer.state_dict(),'dataset_provenance':{'dataset_sha256':'fixture'},'best_epoch':1},ocr_checkpoint)
    descriptors=[('input','input',None,None,{}),('rotated','inspection','rotated_detection',rotated,{}),('align','preprocess',None,None,{'operation':'align','target_angle_deg':0}),('ocr','inspection','ocr',ocr,{'regex':'^$'}),('decision','decision',None,None,{}),('output','output',None,None,{})]
    nodes=[FlowNode(id=id,position={},data=FlowNodeData(label=id,node_type=kind,task=task,model_job_id=job,params=params)) for id,kind,task,job,params in descriptors]
    graph=FlowchartPipeline(nodes=nodes,edges=[FlowEdge(id=f'e{i}',source=nodes[i].id,target=nodes[i+1].id) for i in range(5)])
    image=tmp_path/'source.png';Image.fromarray(np.full((64,64,3),127,np.uint8)).save(image)
    checkpoints={rotated:rotated_checkpoint,ocr:ocr_checkpoint}
    package=build_flow_package(pipeline=graph,checkpoints=checkpoints,output_base_dir=tmp_path/'packages',package_name='rotated')
    result=run_flow_package(Path(package['package_path']),image)
    assert result['final_verdict']=='OK'
    assert result['roi_count']==2
    assert {a['evidence']['predicted_class'] for a in next(step for step in result['execution_steps'] if step['node_id']=='rotated')['artifacts'] if 'evidence' in a}=={'a','b'}
    assert len(next(step for step in result['execution_steps'] if step['node_id']=='align')['artifacts'])==2
    assert verify_flow_parity(package_dir=Path(package['package_path']),pipeline=graph,checkpoints=checkpoints,image_path=image)['status']=='passed'

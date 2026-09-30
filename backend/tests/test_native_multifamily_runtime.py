"""Native C ABI dispatch exercises an actual multi-model DAG and GAN pixels."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from PIL import Image
import pytest
import torch

from backend.tests.test_runtime_deadline_sdk import real_package
from backend.tests.test_gan_source_composition import generator


@pytest.fixture(autouse=True)
def bounded_cpu():
    prior=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(prior)


def compile_sdk(package,tmp_path):
    output=tmp_path/'native'
    result=subprocess.run([sys.executable,str(package/'native_runtime/build_native.py'),'--output',str(output)],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr
    return output


def test_native_cpp_executes_learned_alignment_segmentation_and_measurement(real_package,tmp_path):
    from backend.engine.rotation import RotationNet,ARCHITECTURE
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart,FlowNode,FlowNodeData,FlowEdge
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import Predictor,compare_flow_results
    original,image=real_package
    rotation=RotationNet(width=8)
    with torch.no_grad():
        for value in rotation.parameters():value.zero_()
        rotation.head[-1].bias[0]=1
    checkpoint=tmp_path/'rotation/best_model.pt';checkpoint.parent.mkdir()
    torch.save({'task':'rotation','version':1,'architecture':ARCHITECTURE,'width':8,'image_size':32,'model_state_dict':rotation.state_dict()},checkpoint)
    graph=get_single_segmentation_flowchart('job_real_runtime')
    graph.nodes[1].data.crop_padding=0;graph.nodes[1].data.params={'min_defect_area_px':1}
    graph.nodes.insert(1,FlowNode(id='align',position={},data=FlowNodeData(label='Learned alignment',node_type='preprocess',model_job_id='a'*32,params={'operation':'learned_rotation'})))
    graph.edges[0].target='align';graph.edges.insert(1,FlowEdge(id='align-segment',source='align',target='node_inspect'))
    graph.nodes.insert(3,FlowNode(id='measure',position={},data=FlowNodeData(label='Calibrated length',node_type='measurement',params={'paths':[{'id':'width','points':[[0,0],[20,0]]}],'calibration':{'unit':'mm','mm_per_pixel_x':.5,'mm_per_pixel_y':.5,'source_size':[48,32]}})))
    next(edge for edge in graph.edges if edge.source=='node_inspect').target='measure'
    graph.edges.append(FlowEdge(id='measure-decision',source='measure',target='node_decision'))
    package=Path(build_flow_package(pipeline=graph,checkpoints={'a'*32:checkpoint,'job_real_runtime':original/'models/job_real_runtime/best_model.pt'},output_base_dir=tmp_path/'mixed',package_name='dag')['package_path'])
    native=compile_sdk(package,tmp_path)
    result=subprocess.run([str(native/'vision_predict'),str(package),str(image)],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr
    output=json.loads(result.stdout)
    assert compare_flow_results(Predictor(package).predict(image),output)['status']=='passed'
    assert {'align','node_inspect','measure'}.issubset({row['node_id'] for row in output['execution_steps']})
    assert next(row for row in output['crops'][0]['measurements'] if row['id']=='width')['length']==10


def test_native_cpp_generator_preserves_actual_seed_pixels_source_and_review(generator,tmp_path):
    from backend.engine.gan_package_runtime import build_generator_package,GeneratorExecutor
    from backend.engine.defect_gan import generate_composited_candidates
    package=build_generator_package(generator,tmp_path/'gan_package');native=compile_sdk(package,tmp_path)
    source=tmp_path/'source.png';Image.new('RGB',(80,64),'white').save(source)
    digest=hashlib.sha256(source.read_bytes()).hexdigest();regions=[{'id':'defect','bbox':[10,10,42,42]}]
    reference=generate_composited_candidates(generator,source,tmp_path/'reference',regions=regions,count=1,seed=31)
    request={'output_dir':str(tmp_path/'candidate'),'count':1,'seed':31,'source_image_path':str(source),'source_sha256':digest,'regions':regions}
    request_path=tmp_path/'request.json';request_path.write_text(json.dumps(request))
    command=[str(native/'vision_execute'),str(package),str(request_path)]
    result=subprocess.run(command,capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stderr
    output=json.loads(result.stdout)
    assert output['candidates'][0]['sha256']==reference['candidates'][0]['sha256']
    assert output['source_image_sha256']==digest and output['seed']==31
    assert output['candidates'][0]['status']=='synthetic_unreviewed'
    assert Path(output['candidates'][0]['path']).is_file()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==digest
    with pytest.raises(ValueError,match='count'):GeneratorExecutor(package).execute({**request,'count':True})
    request['output_dir']=str(tmp_path/'timeout');request_path.write_text(json.dumps(request))
    timed=subprocess.run(command+['1'],capture_output=True,text=True,timeout=20)
    assert timed.returncode==3 and json.loads(timed.stdout)['status']=='timeout'
    assert not (tmp_path/'timeout').exists()

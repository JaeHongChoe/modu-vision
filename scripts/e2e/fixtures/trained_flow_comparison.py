"""Save owned development graphs for genuine completed trial checkpoints."""
import hashlib,json,subprocess,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.api.routes_flowchart import _save_version
from backend.engine.flowchart_engine import get_fixed_roi_flowchart,FlowNode,FlowEdge,FlowchartEngine,verified_checkpoint_scope
from backend.engine.flow_package import build_flow_package
from backend.engine.flow_package_runtime import compare_flow_results

root=Path(sys.argv[1]).resolve();project=json.loads(sys.argv[2]);jobs=json.loads(sys.argv[3])
assert Path(project['project_dir']).resolve().is_relative_to(root)
assert len(jobs)==5 and len(set(jobs))==5
checkpoints={job:Path(project['models_dir'])/job/'best_model.pt' for job in jobs}
original={job:hashlib.sha256(path.read_bytes()).hexdigest() for job,path in checkpoints.items()}
single=get_fixed_roi_flowchart(inspection_task='classification',job_id=jobs[0])
single.nodes[1].data.params['roi_bbox']=[0,0,64,48]
single.name='Actual learned single-model control'
a=_save_version(single,'classification',project['source_dataset_dir'],Path(project['project_dir']))
chain=single.model_copy(deep=True);chain.name='Five genuine learned checkpoints';chain.id='actual_five_classifiers'
first=chain.nodes[2];last=first.id
for index,job in enumerate(jobs[1:],1):
 node=FlowNode(id=f'learned_{index}',position={'x':610+index*250,'y':160},data=first.data.model_copy(deep=True))
 node.data.model_job_id=job;node.data.label=f'Actual learned model {index+1}'
 chain.nodes.insert(3+index-1,node)
 chain.edges.append(FlowEdge(id=f'learned_edge_{index}',source=last,target=node.id,payload_type='image'))
 last=node.id
chain.edges=[edge for edge in chain.edges if not (edge.source==first.id and edge.target=='node_decision')]
chain.edges.append(FlowEdge(id='learned_decision',source=last,target='node_decision',payload_type='result'))
b=_save_version(chain,'classification',project['source_dataset_dir'],Path(project['project_dir']))
package=build_flow_package(pipeline=chain,checkpoints=checkpoints,output_base_dir=root/'trained-flow-packages',
 package_name='five_actual_models',runtime_config={'device':'cpu'})
parity=[]
test_images=sorted((Path(project['source_dataset_dir'])/'test').rglob('*.png'))
assert len(test_images)==3
package_dir=Path(package['package_path'])
import torch
torch.set_num_threads(1)
with verified_checkpoint_scope({(job,'classification'):file for job,file in checkpoints.items()}):
 engine=FlowchartEngine(device='cpu')
 for index,image in enumerate(test_images):
  identity=f'actual-heldout-{index}'
  reference=engine.execute(pipeline=chain,image_path=str(image),image_id=identity)
  output=root/f'five-model-packaged-{index}.json'
  run=subprocess.run([sys.executable,str(package_dir/'run_flow.py'),'--image',str(image),
   '--image-id',identity,'--device','cpu','--cpu-threads','1','--output',str(output)],
   cwd=package_dir,check=True,capture_output=True,text=True,timeout=90)
  packaged=json.loads(output.read_text())
  comparison=compare_flow_results(reference,packaged)
  assert comparison['status']=='passed',comparison
  parity.append({'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(),
   'reference':reference,'packaged':packaged,'parity':comparison,'exit_code':run.returncode})
assert original=={job:hashlib.sha256(path.read_bytes()).hexdigest() for job,path in checkpoints.items()}
print(json.dumps({'version_a':a,'version_b':b,'single':single.model_dump(),'chain':chain.model_dump(),
 'checkpoint_sha256':original,'package':package,'package_parity':parity,'active_flow_changed':False,'quality_approved':False}))

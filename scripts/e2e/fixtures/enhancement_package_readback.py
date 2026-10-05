"""Actual enhancement plus learned classifier candidate whole-flow export."""
import base64,hashlib,io,json,os,subprocess,sys
from pathlib import Path
import numpy as np
from PIL import Image
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartPipeline
from backend.engine.flow_package_runtime import compare_flow_results,verify_flow_package
request=json.loads(sys.argv[1]);project=Path(request['project_dir']).resolve();job=request['job_id'];downstream=request['downstream_job_id']
assert len(job)==32 and all(c in '0123456789abcdef' for c in job) and downstream.startswith('job_')
checkpoints={job:project/'models/enhancement'/job/'best_model.pt',downstream:project/'models'/downstream/'best_model.pt'}
package=build_flow_package(pipeline=FlowchartPipeline.model_validate(request['pipeline']),checkpoints=checkpoints,output_base_dir=project.parent/'enhancement-candidate-packages',package_name='enhancement-candidate-'+job)
root=Path(package['package_path']);output=root.parent/(root.name+'-result.json');assert not output.exists()
run=subprocess.run([sys.executable,str(root/'run_flow.py'),'--image',request['source'],'--output',str(output)],cwd=root,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=45)
assert run.returncode==0,run.stderr
result=json.loads(output.read_text());comparison=compare_flow_results(request['flow_result'],result);assert comparison['status']=='passed',comparison
def pixels(encoded):return np.asarray(Image.open(io.BytesIO(base64.b64decode(encoded.split(',')[-1]))).convert('RGB'))
expected=pixels(request['prediction']['image_base64'])
for report in (request['flow_result'],result):
    artifact=next(s for s in report['execution_steps'] if s['node_id']=='enhancement')['artifacts'][0]
    assert np.array_equal(pixels(artifact['image']),expected)
manifest=verify_flow_package(root);assert 'release_policy' not in package
print(json.dumps({'package':package,'manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),
    'checkpoints':{key:{'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()} for key,file in checkpoints.items()},
    'output':str(output),'comparison':comparison,'result':result,'pixel_parity':True,'fresh_package_process':True,'candidate_only':True}))

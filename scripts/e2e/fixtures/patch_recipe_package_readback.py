"""Independent candidate-only patch runtime; compares measured recipes and geometry."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartPipeline
from backend.engine.flow_package_runtime import compare_flow_results,verify_flow_package
req=json.loads(sys.argv[1]);project=Path(req['project_dir']).resolve();job=req['job_id'];assert job.startswith('job_') and '/' not in job
checkpoint=project/'models'/job/'best_model.pt'
package=build_flow_package(pipeline=FlowchartPipeline.model_validate(req['pipeline']),checkpoints={job:checkpoint},output_base_dir=project.parent/'patch-recipe-packages',package_name='patch-'+req['mode']+'-'+job)
root=Path(package['package_path']);output=root.parent/(root.name+'-result.json');assert not output.exists()
run=subprocess.run([sys.executable,str(root/'run_flow.py'),'--image',req['source'],'--device','cpu','--output',str(output)],cwd=root,env={**os.environ,'PYTHONPATH':'','HF_HUB_OFFLINE':'1'},capture_output=True,text=True,timeout=45)
assert run.returncode==0,run.stderr
result=json.loads(output.read_text());comparison=compare_flow_results(req['flow_result'],result);assert comparison['status']=='passed',comparison
verify_flow_package(root);assert 'release_policy' not in package
print(json.dumps({'package':package,'manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
    'output':str(output),'result':result,'comparison':comparison,'fresh_package_process':True,'candidate_only':True}))

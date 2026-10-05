"""Build and execute a private candidate-only whole OCR flow; never a release."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import FlowchartPipeline
from backend.engine.flow_package_runtime import compare_flow_results,verify_flow_package
request=json.loads(sys.argv[1]);project=Path(request['project_dir']).resolve();job=request['job_id']
assert len(job)==32 and all(c in '0123456789abcdef' for c in job)
checkpoint=project/'models/ocr'/job/'best_model.pt'
package=build_flow_package(pipeline=FlowchartPipeline.model_validate(request['pipeline']),checkpoints={job:checkpoint},output_base_dir=project.parent/'ocr-candidate-packages',package_name='ocr-candidate-'+job)
root=Path(package['package_path']);output=root.parent/(root.name+'-result.json')
assert not output.exists()
run=subprocess.run([sys.executable,str(root/'run_flow.py'),'--image',request['source'],'--output',str(output)],cwd=root,env={**os.environ,'PYTHONPATH':''},capture_output=True,text=True,timeout=35)
assert run.returncode==0,run.stderr
result=json.loads(output.read_text());comparison=compare_flow_results(request['flow_result'],result)
assert comparison['status']=='passed',comparison
assert 'release_policy' not in package
manifest=verify_flow_package(root)
print(json.dumps({'package':package,'manifest_sha256':hashlib.sha256((root/'manifest.json').read_bytes()).hexdigest(),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'output':str(output),'comparison':comparison,'result':result,'fresh_package_process':True,'candidate_only':True},ensure_ascii=False))

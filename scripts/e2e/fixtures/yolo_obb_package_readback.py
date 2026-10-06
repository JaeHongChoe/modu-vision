"""Candidate-only exact-host-trust OBB inference and fresh offline flow parity."""
import hashlib, json, os, subprocess, sys
from pathlib import Path
import numpy as np
from PIL import Image
repo=Path(__file__).resolve().parents[3];sys.path.insert(0,str(repo))
from backend.engine.yolo_obb_adapter import predict_yolo_array
from backend.engine.flow_package import build_flow_package
from backend.engine.flow_package_runtime import verify_flow_package,compare_flow_results
from backend.engine.flowchart_engine import FlowchartPipeline

req=json.load(sys.stdin);project=Path(req['project_dir']).resolve();job=req['job_id']
assert len(job)==32 and all(c in '0123456789abcdef' for c in job)
checkpoint=project/'models'/'rotated_detection'/job/'best_model.pt'
meta=json.loads((checkpoint.parent/'model_meta.json').read_text());native_sha=meta['native_model_sha256']
assert meta['license']['distribution_status']=='pending_review'
# Separate test process authorizes only the exact candidate produced by this run.
os.environ['MODU_VISION_TRUSTED_YOLO_OBB_SHA256']=native_sha
sha=lambda f:hashlib.sha256(Path(f).read_bytes()).hexdigest()
if req.get('action')=='inspect':
    rgb=np.asarray(Image.open(req['source']).convert('RGB'))
    prediction=predict_yolo_array(checkpoint,rgb,device='cpu',threshold=.001)
    scores=sorted([r['confidence'] for r in prediction['detections']],reverse=True);assert scores
    # A diagnostic cut retains the most confident ROI(s); it is not quality tuning.
    threshold=(scores[0]+scores[1])/2 if len(scores)>1 and scores[0]>scores[1] else scores[0]
    selected=sum(score>=threshold for score in scores);assert 1<=selected<=4
    print(json.dumps({'prediction':prediction,'diagnostic_roi_threshold':threshold,'selected_rois':selected,'checkpoint_sha256':sha(checkpoint),'native_model_sha256':native_sha}));sys.exit(0)
package=build_flow_package(pipeline=FlowchartPipeline.model_validate(req['pipeline']),checkpoints={job:checkpoint},output_base_dir=project.parent/'obb-packages',package_name='obb-'+job)
root=Path(package['package_path']);output=root.parent/(root.name+'-result.json');verify_flow_package(root)
env={**os.environ,'PYTHONPATH':'','HF_HUB_OFFLINE':'1','MODU_VISION_TRUSTED_YOLO_OBB_SHA256':native_sha}
run=subprocess.run([sys.executable,str(root/'run_flow.py'),'--image',req['source'],'--device','cpu','--output',str(output)],cwd=root,env=env,capture_output=True,text=True,timeout=80)
assert run.returncode==0,run.stderr
result=json.loads(output.read_text());comparison=compare_flow_results(req['flow_result'],result);assert comparison['status']=='passed',comparison;verify_flow_package(root);assert 'release_policy' not in package
print(json.dumps({'package':package,'manifest_sha256':sha(root/'manifest.json'),'checkpoint_sha256':sha(checkpoint),'native_model_sha256':native_sha,'output':str(output),'result_sha256':sha(output),'comparison':comparison,'fresh_offline_process':True,'exact_candidate_host_trust':True,'candidate_only':True,'license_approval':False}))

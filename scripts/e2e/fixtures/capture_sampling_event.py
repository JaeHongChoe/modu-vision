"""One additional owned synthetic service result for sampling handoff QA."""
import hashlib,json,sys
from pathlib import Path
from PIL import Image
repo=Path(sys.argv[1]);sys.path.insert(0,str(repo))
from backend.engine.inspection_service import InspectionStore
root=Path(sys.argv[2]).resolve();project=json.loads(sys.argv[3]);home=Path(project['project_dir']).resolve()
if not home.is_relative_to(root):raise ValueError('Explicit owned workspace required')
store=InspectionStore(home/'runtime_service/state');image=store.state_dir/'uploads/sampling-revision2.png'
if image.exists() or image.is_symlink():raise ValueError('Synthetic event already exists')
Image.new('RGB',(32,32),'purple').save(image);identifier=store.enqueue(image,'http');assert store.claim()['job_id']==identifier
store.finish(identifier,result={'status':'success','final_verdict':'REVIEW','review_required':True,'graph_sha256':'a'*64,'execution_steps':[{'node_id':'decision','branch_verdict':'REVIEW'}],'crops':[],'runtime_identity':{'manifest_sha256':'b'*64,'model_sha256':{'controlled_fixture':'c'*64}}})
print(json.dumps({'job_id':identifier,'image':str(image),'sha256':hashlib.sha256(image.read_bytes()).hexdigest(),'model_executed':False}))

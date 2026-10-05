"""Controlled ten-family restart records, never simulated completed models."""
import json,os,re,shutil,sqlite3,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.contracts.context import ContextRegistry,ProjectContext
from backend.engine.job_store import ledger

root=Path(sys.argv[1]).resolve();run=Path(os.environ['MV_E2E_RUN_DIR']).resolve()
if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name):raise ValueError('Owned E2E workspace required')
if Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR']).resolve()!=root/'userData':raise ValueError('Owned profile required')
project=json.loads(sys.argv[2]);context=ProjectContext.model_validate(project['project_context']);source=project['source_dataset_dir']
source_root=Path(source).resolve()
if not source_root.is_relative_to(root) or source_root.is_symlink():raise ValueError('Owned generated dataset required')
# Core detection has a COCO loader. Supply that real layout so changing to its
# workbench does not intentionally fail image import before the handoff.
for partition,label in [('train','ok'),('val','ng')]:
    target=source_root/'detection'/'images'/partition;target.mkdir(parents=True)
    shutil.copyfile(source_root/label/f'sample-{label}.png',target/f'{partition}.png')
    annotations={'images':[{'id':1,'file_name':f'{partition}.png','width':32,'height':32}],
                 'categories':[{'id':1,'name':'controlled-fixture'}],
                 'annotations':[{'id':1,'image_id':1,'category_id':1,'bbox':[4,4,8,8],'area':64,'iscrowd':0}]}
    (source_root/'detection'/f'annotations_{partition}.json').write_text(json.dumps(annotations))
models=Path(project['models_dir']).resolve()
if not models.is_relative_to(root) or models.is_symlink():raise ValueError('Owned model store required')
registries=[]
for directory in (root/'projects',root/'userData'/'projects'):
 database=directory/'.context.sqlite3'
 if not database.is_file() or database.is_symlink():continue
 with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as db:
  if db.execute('SELECT 1 FROM project_locations WHERE workspace_id=? AND project_id=?',(context.workspace_id,context.project_id)).fetchone():registries.append(directory)
if len(registries)!=1:raise ValueError('Exact originating registry required')
registry=ContextRegistry(registries[0]);key=registry.project_key(context);store=ledger();records=[]
for task in ['classification','segmentation','detection','anomaly','patch_classification']:
 identifier='job_'+task+'_retained';output=models/identifier
 ref=store.submit(context,key,'training',{'task':task,'dataset_path':source,'labelset_id':'default'},job_id=identifier,output_dir=str(output),registry_root=str(registry.root),project_dir=project['project_dir'])
 store.transition(ref.id,ref.revision,'interrupt',{'reason':'Controlled restart: accepted before worker launch'})
 records.append({'family':task,'kind':'training','job_id':identifier,'recorded_execution':False,'state':'interrupted'})
for index,task in enumerate(['ocr','rotated_detection','rotation','defect_gan','enhancement']):
 identifier=f'{index+1:032x}';folder=models/task/identifier;folder.mkdir(parents=True)
 name='job_state.json' if task=='rotated_detection' else 'job.json'
 row={'job_id':identifier,'task':task,'status':'running','epoch':2,'current_epoch':2,'epochs':5,'epochs_completed':2,'total_epochs':5,'created_at':1,'owner_instance':'old-owned-fixture','source_dataset_path':source,'dataset_path':source,'training_provenance':{'labelset_id':'default'},'device':'cpu','warm_start':None,'events':[],'error':None,'result':None}
 (folder/name).write_text(json.dumps(row))
 records.append({'family':task,'kind':task.replace('_','-') if task in ['defect_gan','rotated_detection'] else task,'job_id':identifier,'recorded_execution':False,'state':'running_without_owner'})
print(json.dumps({'records':records,'actual_training':False,'completed_candidates':0,'context':context.model_dump()}))

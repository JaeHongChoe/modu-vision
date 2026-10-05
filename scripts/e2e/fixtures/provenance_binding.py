"""Owned immutable provenance fixture; inert weights and controlled historical result.

Nothing here executes or approves a model. App/backend trace reads are real.
"""
import hashlib,json,os,re,sys,uuid
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
root=Path(sys.argv[1]).resolve();directory=Path(sys.argv[2]).resolve()
run=Path(os.environ.get('MV_E2E_RUN_DIR','missing')).resolve()
owned_project=any(directory.is_relative_to(parent) for parent in (root/'projects',root/'userData'/'projects'))
if root.parent!=run/'ws' or not re.fullmatch(r'\d+-[a-f0-9]{8}-r\d+',root.name) or not owned_project or any(p.is_symlink() for p in (directory,*directory.parents)):
    raise ValueError('Only an owned E2E project can host the trace fixture')
from backend.api.routes_project import _load_project
from backend.api import routes_dataset
from backend.engine.training_provenance import bind_training_version
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.flow_provenance import pipeline_sha256,canonical_pipeline_json
from backend.api.routes_inspections import _store
project=_load_project(directory);source=Path(project['source_dataset_dir']);assert source==root/'dataset'
routes_dataset.STUDIO_ANNOTATIONS_DIR=Path(project['annotations_dir']);routes_dataset.SPLIT_MANIFEST_DIR=Path(project['dataset_dir'])/'splits'
binding=bind_training_version(project,source)
model=Path(project['models_dir'])/'job_trace';model.mkdir();checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'inert provenance fixture; never loaded')
sha=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
(model/'model_meta.json').write_text(json.dumps({'task':'classification','checkpoint_sha256':sha(checkpoint),'source_dataset_path':str(source),'training_provenance':binding}))
graph=get_single_segmentation_flowchart('job_trace');graph.name='Controlled trace flow'
for n in graph.nodes:
    if n.data.node_type=='inspection':n.data.task='classification'
version=uuid.uuid4().hex;flow=directory/'flowcharts'/'versions'/f'{version}.json';flow.parent.mkdir(parents=True,exist_ok=True);flow.write_text(json.dumps({'version_id':version,'pipeline':graph.model_dump(),'pipeline_hash':pipeline_sha256(graph),'source_dataset_path':str(source),'created_at':'controlled-history'}))
image=source/'ok'/'sample-ok.png';assert image.is_file()
identity={'image_id':'sample-ok','file_path':str(image),'file_name':image.name,'split':None}
run_id=str(uuid.uuid4());execution={'execution_target':'local','device':'cpu','project_id':project['id'],'compute_profile_id':None};encoded=json.dumps(execution,sort_keys=True,separators=(',',':'))
request=SimpleNamespace(state=SimpleNamespace(scoped_project=project))
with _store(request) as conn:
    conn.execute('INSERT INTO runs(run_id,source_folder,task,scope,pipeline_id,pipeline_name,pipeline_hash,pipeline_json,saved_version_id,model_sha256_json,execution_config_json,execution_config_sha256,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(run_id,str(source),'classification','all',graph.id,graph.name,pipeline_sha256(graph),canonical_pipeline_json(graph),version,json.dumps({'job_trace':sha(checkpoint)}),encoded,hashlib.sha256(encoded.encode()).hexdigest(),'completed','controlled-history','controlled-history'))
    conn.execute('INSERT INTO rows(run_id,image_path,image_json,state,result_json,image_sha256,updated_at) VALUES(?,?,?,?,?,?,?)',(run_id,str(image),json.dumps(identity),'REVIEW',json.dumps({'status':'success','final_verdict':'REVIEW','is_ok':False,'controlled_fixture':True}),sha(image),'controlled-history'))
protected=[checkpoint,model/'model_meta.json',flow,Path(binding['version_dir'])/'manifest.json',Path(binding['version_dir'])/'team-data.json']
protected.extend((Path(binding['version_dir'])/'labels').rglob('*'))
print(json.dumps({'project':project,'image':str(image),'image_sha256':sha(image),'binding':binding,'flow_version_id':version,'graph_sha256':pipeline_sha256(graph),'run_id':run_id,'checkpoint_sha256':sha(checkpoint),'frozen_files':{str(p):sha(p) for p in protected if p.is_file()},'historical_result':'controlled_inert_no_model_execution'}))

"""Bind owned deterministic checkpoints to two real CPU inspection versions.

Weights come from comparison_models.py: actual CPU inference, untrained
synthetic controls, no representative model quality or training claim.
"""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.api.routes_flowchart import _save_version
from backend.engine.flowchart_engine import get_fixed_roi_flowchart
from backend.engine.flow_debug_cache import file_sha256

project=json.loads(sys.argv[1]);source=project['source_dataset_dir']
graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_fixture_incumbent')
roi=next(n for n in graph.nodes if n.data.node_type=='fixed_roi');roi.data.params['roi_bbox']=[4,4,40,40]
model=next(n for n in graph.nodes if n.data.node_type=='inspection');graph.name='CPU control A'
a=_save_version(graph,'classification',source,Path(project['project_dir']))
graph_b=graph.model_copy(deep=True);graph_b.name='CPU control B'
next(n for n in graph_b.nodes if n.id==model.id).data.model_job_id='job_fixture_candidate'
b=_save_version(graph_b,'classification',source,Path(project['project_dir']))
print(json.dumps({'pipeline':graph.model_dump(),'version_a':a,'version_b':b,'stop_node_id':model.id,
    'models':{job:file_sha256(Path(project['models_dir'])/job/'best_model.pt') for job in ('job_fixture_incumbent','job_fixture_candidate')},
    'actual_cpu_inference':True,'trained':False,'quality_approved':False}))

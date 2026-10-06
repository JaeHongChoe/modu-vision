"""Synthetic/untrained cross-project template controls; no quality claim."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from backend.api.routes_flowchart import _save_version
from backend.engine.flowchart_engine import get_fixed_roi_flowchart
from backend.engine.flow_debug_cache import file_sha256
import torch

project=json.loads(sys.argv[1]); target=len(sys.argv)>2 and sys.argv[2]=='target'
job='job_target_incumbent' if target else 'job_fixture_incumbent'
if target:
    for suffix in ('incumbent','candidate'):
        old=Path(project['models_dir'])/('job_fixture_'+suffix)
        new=Path(project['models_dir'])/('job_target_'+suffix); old.rename(new)
        checkpoint=torch.load(new/'best_model.pt',weights_only=True,map_location='cpu')
        checkpoint['classes']=['OK','scratch']; torch.save(checkpoint,new/'best_model.pt')
        receipt=json.loads((new/'job_receipt.json').read_text(encoding='utf-8'))
        receipt['dataset_path']=str(new/'dataset')
        (new/'job_receipt.json').write_text(json.dumps(receipt),encoding='utf-8')
graph=get_fixed_roi_flowchart(inspection_task='classification',job_id=job)
graph.name='Target baseline' if target else 'Source template graph'
next(n for n in graph.nodes if n.data.node_type=='fixed_roi').data.params['roi_bbox']=[4,4,40,40]
model=next(n for n in graph.nodes if n.data.node_type=='inspection')
next(e for e in graph.edges if e.source==model.id).predicate={'kind':'class','operator':'present','class_name':'scratch' if target else 'NG','min_confidence':0}
version=_save_version(graph,'classification',project['source_dataset_dir'],Path(project['project_dir']))
print(json.dumps({'version':version,'pipeline':graph.model_dump(),'model_id':model.id,'job_id':job,
                  'checkpoint_sha256':file_sha256(Path(project['models_dir'])/job/'best_model.pt'),
                  'synthetic_only':True,'trained':False,'quality_approved':False}))

"""Owned image/version controls. Missing models prove failure history, never inference."""
from pathlib import Path
import hashlib,json,sys
from PIL import Image
root=Path(sys.argv[1]);source=root/'comparison-control-inputs'
if len(sys.argv)==2:
    rows=[]
    for split,count in [('train',2),('val',1),('test',1)]:
        for label in ['OK','NG']:
            for i in range(count):
                file=source/split/label/f'{split}_{label}_{i}.png';file.parent.mkdir(parents=True,exist_ok=True)
                Image.new('RGB',(48,48),(20+i+len(split),60 if label=='OK' else 150,80)).save(file)
                rows.append({'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'split':split})
    print(json.dumps({'source':str(source),'files':rows,'synthetic_only':True,'actual_model_execution':False}))
else:
    sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
    from backend.api.routes_flowchart import _save_version
    from backend.engine.flowchart_engine import get_fixed_roi_flowchart
    project=json.loads(sys.argv[2]);graph=get_fixed_roi_flowchart(inspection_task='classification',job_id='job_unavailable_control')
    roi=next(n for n in graph.nodes if n.data.node_type=='fixed_roi');roi.data.params['roi_bbox']=[4,4,40,40]
    graph.name='History control A';a=_save_version(graph,'classification',str(source),Path(project['project_dir']))
    graph=graph.model_copy(deep=True);graph.name='History control B';b=_save_version(graph,'classification',str(source),Path(project['project_dir']))
    print(json.dumps({'version_a':a,'version_b':b,'missing_model_id':'job_unavailable_control','stop_node_id':roi.id,'active_flow_not_changed':True}))

"""Prepare a saved recipe using the actual detector job trained by the GUI test."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.api.routes_flowchart import _save_version
from backend.engine.flowchart_engine import get_single_detection_flowchart

project = json.loads(sys.argv[1])
job = sys.argv[2]
receipt = json.loads((Path(project['models_dir']) / job / 'job_receipt.json').read_text())
assert receipt['status'] == 'completed' and receipt['task'] == 'detection'
graph = get_single_detection_flowchart(job)
version = _save_version(graph, 'detection', project['source_dataset_dir'], Path(project['project_dir']))
print(json.dumps({'version': version, 'model_id': next(n.id for n in graph.nodes if n.data.node_type == 'detection_crop')}))

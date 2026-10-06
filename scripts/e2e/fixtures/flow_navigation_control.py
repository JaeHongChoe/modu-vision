"""Synthetic saved graph for actual navigation QA; does not qualify inference."""
from pathlib import Path
import hashlib, json, sys
from PIL import Image

root = Path(sys.argv[1])
source = root / 'navigation-control-inputs'
if len(sys.argv) == 2:
    files = []
    for split in ['train', 'val', 'test']:
        for label in ['OK', 'NG']:
            file = source / split / label / 'control.png'
            file.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (48, 48), (20, 70 if label == 'OK' else 160, 80)).save(file)
            files.append({'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    print(json.dumps({'source': str(source), 'files': files, 'synthetic_only': True}))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.api.routes_flowchart import _recipe_file, _write_pipeline, _save_version
    from backend.engine.flowchart_engine import get_fixed_roi_flowchart
    project = json.loads(sys.argv[2])
    graph = get_fixed_roi_flowchart(inspection_task='classification', job_id='job_navigation_unavailable')
    for index, node in enumerate(graph.nodes):
        node.position = {'x': -100 + index * 620, 'y': -160 + index * 500}
        node.data.label = f'탐색 노드 {index + 1}'
    graph.nodes[-1].data.label = '먼곳 판정'
    graph.name = 'Navigation control'
    graph_file = _recipe_file('classification', str(source), Path(project['project_dir']))
    _write_pipeline(graph_file, graph)
    version = _save_version(graph, 'classification', str(source), Path(project['project_dir']))
    directory = Path(project['project_dir']) / 'flowcharts/comparisons'
    directory.mkdir(parents=True, exist_ok=True)
    duplicate = directory / 'duplicate-control.json'
    duplicate.write_text('{"comparison_id":"duplicate-control","created_at":"2000","status":"completed","status":"completed","rows":[]}', encoding='utf-8')
    print(json.dumps({'graph': graph.model_dump(), 'graph_file': str(graph_file), 'graph_sha256': hashlib.sha256(graph_file.read_bytes()).hexdigest(), 'version_id': version, 'target_id': graph.nodes[-1].id, 'duplicate_record': str(duplicate), 'duplicate_sha256': hashlib.sha256(duplicate.read_bytes()).hexdigest(), 'actual_model_execution': False}))

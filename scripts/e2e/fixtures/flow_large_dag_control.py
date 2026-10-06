"""46-node / 52-edge supported DAG. No training, inference or quality claim."""
from pathlib import Path
import hashlib, json, sys
from PIL import Image

root = Path(sys.argv[1])
source = root / 'large-dag-control-inputs'
sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
if len(sys.argv) == 2:
    files = []
    for split in ['train', 'val', 'test']:
        for label in ['OK', 'NG']:
            file = source / split / label / 'control.png'
            file.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (48, 48), (20, 70 if label == 'OK' else 160, 80)).save(file)
            files.append({'path': str(file), 'sha256': sha(file)})
    print(json.dumps({'source': str(source), 'files': files, 'synthetic_only': True}))
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.api.routes_flowchart import _recipe_file, _write_pipeline, _save_version
    from backend.engine.flowchart_engine import (get_fixed_roi_flowchart, FlowNode,
        FlowNodeData, FlowEdge, ordered_linear_nodes)
    project = json.loads(sys.argv[2])
    graph = get_fixed_roi_flowchart(inspection_task='segmentation', job_id='job_large_dag_unavailable')
    original = graph.nodes[2]
    branches, links = [], []
    for i in range(8):
        node = original.model_copy(deep=True)
        node.id = f'model-{i:03}'
        node.data.label = f'공유 모델 검사 {i:03}'
        node.data.threshold = round(.1 + i * .1, 2)
        node.data.params['min_defect_area_px'] = i + 1
        roi = FlowNode(id=f'roi-{i}', position={'x': 0, 'y': 0}, data=FlowNodeData(label=f'영역 {i}', node_type='fixed_roi', params={'roi_bbox': [i, i, 40, 40]}))
        operators = [FlowNode(id=f'rotate-{i}-{j}', position={'x': 0, 'y': 0}, data=FlowNodeData(label=f'회전 {i}-{j}', node_type='preprocess', params={'operation': 'rotate', 'angle_deg': j * 90})) for j in range(2)]
        blob = FlowNode(id=f'blob-{i}', position={'x': 0, 'y': 0}, data=FlowNodeData(label=f'Blob 규칙 {i}', node_type='blob_measure', params={'min_blob_area_px': i + 1}))
        branches += [roi, *operators, node, blob]
        chain = ['node_input', roi.id, *[op.id for op in operators], node.id, blob.id, 'merge']
        for j, (start, end) in enumerate(zip(chain, chain[1:])):
            links.append(FlowEdge(id=f'branch-{i}-{j}', source=start, target=end, payload_type='image' if j == 0 else 'roi' if j < 4 else 'result'))
    aggregate = FlowNode(id='merge', position={'x': 0, 'y': 0},
        data=FlowNodeData(label='전체 결과 집계', node_type='aggregate', rule='any_ng'))
    decision = graph.nodes[-2]
    decision.data.rule = 'aggregate_verdict'
    outputs = [FlowNode(id=f'output-{branch}', position={'x': 0, 'y': 0}, data=FlowNodeData(label=f'큰 그래프 끝 {branch}', node_type='output')) for branch in ['pass', 'fail', 'review']]
    graph.nodes = [graph.nodes[0], *branches, aggregate, decision, *outputs]
    for i, node in enumerate(graph.nodes):
        node.position = {'x': -100 + i % 10 * 380, 'y': -160 + i // 10 * 340}
    graph.edges = links + [FlowEdge(id='merge-decision', source='merge', target='node_decision', payload_type='result')]
    graph.edges += [FlowEdge(id=f'decision-{branch}', source='node_decision', target=f'output-{branch}', payload_type='result', isBranch=branch) for branch in ['pass', 'fail', 'review']]
    graph.name = 'Large DAG control'
    ordered_linear_nodes(graph)
    graph_file = _recipe_file('segmentation', str(source), Path(project['project_dir']))
    _write_pipeline(graph_file, graph)
    version = _save_version(graph, 'segmentation', str(source), Path(project['project_dir']))
    print(json.dumps({'graph': graph.model_dump(), 'graph_file': str(graph_file),
        'graph_sha256': sha(graph_file), 'version_id': version, 'target_id': graph.nodes[-1].id,
        'actual_model_execution': False, 'model_quality_approved': False}))

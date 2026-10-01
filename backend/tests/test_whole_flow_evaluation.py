"""Whole-flow evidence uses real graph decisions and explicit scoped truth."""
import importlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest
import torch

from backend.engine.flowchart_engine import FlowchartEngine, FlowEdge, FlowNode, FlowNodeData, get_single_segmentation_flowchart


class ASGIClient:
    """Use supported httpx ASGI transport without Starlette's legacy app kwarg."""
    def __init__(self, app): self.app=app
    def request(self, method, path, **kwargs):
        import asyncio, httpx
        async def send():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as client:
                return await client.request(method,path,**kwargs)
        return asyncio.run(send())
    def get(self,path,**kwargs): return self.request('GET',path,**kwargs)
    def put(self,path,**kwargs): return self.request('PUT',path,**kwargs)
    def post(self,path,**kwargs): return self.request('POST',path,**kwargs)


@pytest.fixture
def modules():
    assert importlib.util.find_spec('backend.engine.flow_evaluation'), 'Whole-flow evaluation is not implemented'
    assert importlib.util.find_spec('backend.engine.image_truth'), 'Explicit truth is not implemented'
    return importlib.import_module('backend.engine.flow_evaluation'), importlib.import_module('backend.engine.image_truth')


@pytest.fixture
def workspace(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    project = {'id': 'project-one', 'project_dir': str(tmp_path / 'project'), 'source_dataset_dir': str(source),
               'annotations_dir': str(tmp_path / 'project' / 'annotations'), 'dataset_dir': str(tmp_path / 'project' / 'dataset'),
               'models_dir': str(tmp_path / 'project' / 'models'), 'task': 'segmentation', 'active_labelset_id': 'default'}
    for name, red in [('defect', True), ('normal', False), ('unknown', False)]:
        image = np.zeros((32, 32, 3), np.uint8)
        if red: image[4:12, 4:12, 0] = 255
        Image.fromarray(image).save(source / f'{name}.png')
    # Identical unknown pixels use a different image encoding; cohort duplicates are disclosed.
    Image.new('RGB', (33, 32)).save(source / 'unknown.png')
    graph = get_single_segmentation_flowchart('job_eval')
    inspect = next(n for n in graph.nodes if n.data.node_type == 'inspection')
    inspect.data.params = {'min_defect_area_px': 1, 'class_names': ['background', 'crack']}
    version_id = '1' * 32
    folder = Path(project['project_dir']) / 'flowcharts' / 'versions'; folder.mkdir(parents=True)
    (folder / f'{version_id}.json').write_text(json.dumps({'version_id': version_id, 'recipe_task': 'segmentation',
        'source_dataset_path': str(source), 'pipeline': graph.model_dump()}))
    import hashlib
    split = Path(project['dataset_dir']) / 'splits' / f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
    split.parent.mkdir(parents=True)
    split.write_text(json.dumps({'folder_path': str(source), 'assignments': {f'{name}.png': 'test' for name in ['defect','normal','unknown']}}))
    checkpoint = Path(project['models_dir']) / 'job_eval' / 'best_model.pt'; checkpoint.parent.mkdir(parents=True)
    torch.save({'task': 'segmentation', 'model_state_dict': {}, 'class_names': ['background','crack']}, checkpoint)
    return project, graph, version_id, checkpoint


def declare(truth, project, name, verdict, *, classes=None):
    image = str(Path(project['source_dataset_dir']) / f'{name}.png')
    row = truth.read_truth(project, image, task='segmentation', classes=['background','crack'])
    return truth.declare_truth(project, image, task='segmentation', classes=['background','crack'], verdict=verdict,
        defect_classes=classes or [], reviewer='Reviewer', expected_revision=row['truth_revision'],
        expected_image_revision=row['image_revision'])


def test_legacy_segmentation_truth_respects_mask_channel_roles(modules,workspace):
    evaluation,truth=modules;project,graph,_,checkpoint=workspace
    names=['unlabeled background','good']
    next(node for node in graph.nodes if node.data.node_type=='inspection').data.params['class_names']=names
    provider={('job_eval','segmentation'):{'path':str(checkpoint),'metadata':{'class_names':names}}}
    scope=evaluation.graph_truth_scope(project,graph,provider)
    assert scope['class_semantics']['roles']=={'unlabeled background':'normal','good':'defect'}
    image=str(Path(project['source_dataset_dir'])/'defect.png')
    current=truth.read_truth(project,image,task='segmentation',classes=names)
    row=truth.declare_truth(project,image,task='segmentation',classes=names,verdict='NG',defect_classes=['good'],reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    assert row['verdict']=='NG'


def _mixed_truth_workspace(workspace):
    project, graph, version, checkpoint = workspace
    inspection = next(node for node in graph.nodes if node.data.node_type == 'inspection')
    inspection.data.params['class_names'] = ['background', 'good']
    classifier = FlowNode(id='classification-truth', position={'x': 0, 'y': 0},
        data=FlowNodeData(label='Classification', node_type='inspection', task='classification',
                          model_job_id='job_cls', params={'class_names': ['OK', 'NG']}))
    previous = next(edge for edge in graph.edges if edge.target == inspection.id)
    previous.target = classifier.id
    graph.nodes.insert(1, classifier)
    graph.edges.append(FlowEdge(id='classification-to-segmentation', source=classifier.id, target=inspection.id))
    path = Path(project['project_dir']) / 'flowcharts' / 'versions' / f'{version}.json'
    record = json.loads(path.read_text()); record['pipeline'] = graph.model_dump(); path.write_text(json.dumps(record))
    models = {('job_eval', 'segmentation'): {'path': str(checkpoint), 'metadata': {'class_names': ['background', 'good']}},
              ('job_cls', 'classification'): {'path': str(checkpoint), 'metadata': {'classes': ['OK', 'NG']}}}
    return project, graph, version, models


def test_mixed_whole_flow_scope_keeps_legacy_segmentation_channel_roles(modules, workspace):
    evaluation, _ = modules
    project, graph, version, models = _mixed_truth_workspace(workspace)
    scope = evaluation.graph_truth_scope(project, graph, models)
    assert scope['task'] == 'mixed'
    assert scope['class_semantics']['roles']['good'] == 'defect'


def test_scope_api_exposes_mixed_task_context_without_changing_canonical_scope(modules, workspace, monkeypatch):
    from fastapi import FastAPI
    from backend.api import routes_flow_evaluation
    evaluation, _ = modules
    project, graph, version, models = _mixed_truth_workspace(workspace)
    canonical = evaluation.graph_truth_scope(project, graph, models)
    monkeypatch.setattr(routes_flow_evaluation, 'get_current_project', lambda request: project)
    monkeypatch.setattr(evaluation, 'verified_models', lambda *args: models)
    app = FastAPI(); app.include_router(routes_flow_evaluation.router)
    response = ASGIClient(app).get(f'/api/flow-evaluations/scope/{version}')
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['participating_tasks'] == ['classification', 'segmentation']
    assert {key: item for key, item in value.items() if key != 'participating_tasks'} == canonical


def test_mixed_cohort_hashes_participating_truth_and_rechecks_new_declarations(modules, workspace):
    evaluation, truth = modules
    project, graph, version, models = _mixed_truth_workspace(workspace)
    image = str(Path(project['source_dataset_dir']) / 'defect.png')
    scope = evaluation.graph_truth_scope(project, graph, models)
    roles = {'OK': 'normal', 'NG': 'defect', 'background': 'normal', 'good': 'defect'}
    current = truth.read_truth(project, image, task='mixed', classes=scope['classes'], class_roles=roles)
    truth.declare_truth(project, image, task='mixed', classes=scope['classes'], class_roles=roles,
        verdict='OK', reviewer='Reviewer', expected_revision=current['truth_revision'],
        expected_image_revision=current['image_revision'])
    cohort = evaluation.freeze_cohort(project, version, relative_paths=['defect.png'], model_provider=lambda *args: models)
    assert cohort['samples'][0]['truth']['verdict'] == 'OK'
    assert cohort['participating_tasks'] == ['classification', 'segmentation']
    assert evaluation._cohort_changes(project, cohort) == []
    current = truth.read_truth(project, image, task='segmentation', classes=['background', 'good'])
    truth.declare_truth(project, image, task='segmentation', classes=['background', 'good'], verdict='UNKNOWN',
        reviewer='Reviewer', expected_revision=current['truth_revision'], expected_image_revision=current['image_revision'])
    assert evaluation._cohort_changes(project, cohort) == ['truth_or_source_changed:defect.png']


class Pixels(torch.nn.Module):
    def forward(self, image):
        logits = torch.full((len(image), 2, *image.shape[-2:]), -12.0, device=image.device)
        logits[:, 0] = 8
        logits[:, 1][image[:, 0] > .7] = 24
        return logits


def real_engine(monkeypatch):
    engine = FlowchartEngine(device='cpu')
    # Only checkpoint/model loading is replaced; image processing, ROIs, Blob and routing are real.
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kwargs: (Pixels(), True))
    engine._model_input_sizes[('segmentation','job_eval','fast')] = (32,32)
    return engine


def model_provider(checkpoint):
    return lambda project, graph: {('job_eval','segmentation'): {'path': str(checkpoint), 'metadata': {'class_names': ['background','crack']}}}


@pytest.mark.parametrize('matching', [False, True])
def test_whole_flow_truth_roles_use_the_same_record_as_engine_checkpoint(workspace, monkeypatch, matching):
    from backend.engine import flow_evaluation
    from backend.engine import checkpoint_paths
    from backend.api import routes_evaluation, routes_training
    from backend.remote import operations
    project, graph, _, checkpoint = workspace
    next(node for node in graph.nodes if node.data.node_type=='inspection').data.task='classification'
    payload=torch.load(checkpoint,weights_only=True);payload['task']='classification';torch.save(payload,checkpoint)
    roles = {'version': 1, 'roles': {'background': 'defect', 'crack': 'normal'}}
    (checkpoint.parent/'model_meta.json').write_text(json.dumps({'task': 'classification', 'class_names': ['background','crack'], 'class_semantics': roles}))
    if matching:
        payload = torch.load(checkpoint, weights_only=True)
        payload['class_semantics'] = roles
        torch.save(payload, checkpoint)
    monkeypatch.setattr(checkpoint_paths, 'trusted_checkpoint', lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(routes_evaluation, '_matches_source_dataset', lambda *args: True)
    monkeypatch.setattr(routes_training.training_job_manager, 'get_job', lambda *args: None)
    monkeypatch.setattr(operations, 'verify_downloaded_checkpoint', lambda *args: None)
    if matching:
        assert flow_evaluation.verified_models(project, graph)[('job_eval','classification')]['metadata']['class_semantics'] == roles
    else:
        with pytest.raises(ValueError, match='roles'):
            flow_evaluation.verified_models(project, graph)


@pytest.mark.parametrize('metadata_names', [['acceptable', 'scratch'], ['NG', 'OK']])
@pytest.mark.parametrize('metadata_key', ['classes', 'class_names'])
def test_whole_flow_rejects_metadata_vocabulary_different_from_completed_checkpoint(workspace, monkeypatch, metadata_names, metadata_key):
    from backend.engine import flow_evaluation, checkpoint_paths
    from backend.api import routes_evaluation, routes_training
    from backend.remote import operations
    from backend.engine.classification.model import create_classification_model
    project, graph, _, checkpoint = workspace
    inspection = next(node for node in graph.nodes if node.data.node_type == 'inspection')
    inspection.data.task = 'classification'; inspection.data.params = {}
    model = create_classification_model('resnet18', 2, pretrained=False)
    torch.save({'task': 'classification', 'classes': ['OK', 'NG'], 'backbone': 'resnet18',
                'model_state_dict': model.state_dict()}, checkpoint)
    (checkpoint.parent / 'model_meta.json').write_text(json.dumps({'task': 'classification', metadata_key: metadata_names}))
    monkeypatch.setattr(checkpoint_paths, 'trusted_checkpoint', lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(routes_evaluation, '_matches_source_dataset', lambda *args: True)
    monkeypatch.setattr(routes_training.training_job_manager, 'get_job', lambda *args: None)
    monkeypatch.setattr(operations, 'verify_downloaded_checkpoint', lambda *args: None)
    with pytest.raises(ValueError, match='vocabulary'):
        flow_evaluation.verified_models(project, graph)


def test_whole_flow_legacy_metadata_without_vocabulary_uses_checkpoint_classes(workspace, monkeypatch):
    from backend.engine import flow_evaluation, checkpoint_paths
    from backend.api import routes_evaluation, routes_training
    from backend.remote import operations
    project, graph, _, checkpoint = workspace
    (checkpoint.parent / 'model_meta.json').write_text(json.dumps({'task': 'segmentation'}))
    monkeypatch.setattr(checkpoint_paths, 'trusted_checkpoint', lambda *args, **kwargs: checkpoint)
    monkeypatch.setattr(routes_evaluation, '_matches_source_dataset', lambda *args: True)
    monkeypatch.setattr(routes_training.training_job_manager, 'get_job', lambda *args: None)
    monkeypatch.setattr(operations, 'verify_downloaded_checkpoint', lambda *args: None)
    models = flow_evaluation.verified_models(project, graph)
    assert flow_evaluation.graph_truth_scope(project, graph, models)['classes'] == ['background', 'crack']


def test_empty_approved_annotation_is_unknown_and_explicit_negative_invalidates(modules, workspace):
    _, truth = modules; project, _, _, _ = workspace
    source = Path(project['source_dataset_dir']); image = source / 'normal.png'
    (source / 'normal.json').write_text(json.dumps({'shapes': []}))
    from backend.engine import dataset_metadata as dm
    row = dm.metadata_for_path(Path(project['project_dir']), source, image, Path(project['annotations_dir']))
    dm.update_metadata(project['project_dir'], source, row['image_uuid'], row['revision'], 'Reviewer', {'workflow_state':'approved'}, project['annotations_dir'])
    assert truth.read_truth(project, str(image), task='segmentation', classes=['background','crack'])['verdict'] == 'UNKNOWN'
    assert declare(truth, project, 'normal', 'OK')['verdict'] == 'OK'
    (source / 'normal.json').write_text(json.dumps({'shapes': [{'label': 'crack'}]}))
    stale = truth.read_truth(project, str(image), task='segmentation', classes=['background','crack'])
    assert stale['verdict'] == 'UNKNOWN'
    assert stale['invalidated'] is True
    assert truth.read_truth(project, str(image), task='classification', classes=['background','crack'])['verdict'] == 'UNKNOWN'


def test_real_flow_records_unknown_coverage_without_normal_overkill(modules, workspace, monkeypatch):
    evaluation, truth = modules; project, _, version, checkpoint = workspace
    declare(truth, project, 'defect', 'NG', classes=['crack'])
    cohort = evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    result = evaluation.evaluate_flow(project, version, cohort['cohort_id'], engine=real_engine(monkeypatch), model_provider=model_provider(checkpoint))
    assert result['status'] == 'completed'
    assert result['coverage'] == {'total':3, 'known':1, 'unknown':2, 'invalidated':0, 'known_fraction':1/3}
    assert result['confusion']['NG'] == {'OK':0,'NG':1,'REVIEW':0}
    assert result['metrics']['overkill_rate'] is None
    assert result['metrics']['overkill_unavailable_reason']
    assert result['metrics']['escape_rate'] == 0
    assert len(result['unknown_truth']) == 2
    assert len(result['graph_sha256']) == len(result['cohort_sha256']) == len(result['truth_sha256']) == 64
    assert all(len(r['input_sha256']) == 64 and r['node_evidence'] for r in result['records'])
    reopened = evaluation.read_evaluation(project, result['evaluation_id'])
    assert reopened['record_sha256'] == result['record_sha256']
    assert reopened['validity']['valid'] is True


def test_blob_rule_changes_final_decision_and_escape_evidence(modules, workspace, monkeypatch):
    evaluation, truth = modules; project, graph, version, checkpoint = workspace
    declare(truth, project, 'defect', 'NG', classes=['crack']); declare(truth, project, 'normal', 'OK')
    model_edge = graph.edges[1]; graph.edges.remove(model_edge)
    graph.nodes.insert(-2, FlowNode(id='blob', position={}, data=FlowNodeData(label='Blob', node_type='blob_measure', params={'min_blob_area_px':100,'min_blob_count_for_ng':1})))
    graph.edges += [FlowEdge(id='to-blob',source=model_edge.source,target='blob'), FlowEdge(id='to-decision',source='blob',target=model_edge.target)]
    file = Path(project['project_dir'])/'flowcharts'/'versions'/f'{version}.json'
    value = json.loads(file.read_text()); value['pipeline'] = graph.model_dump(); file.write_text(json.dumps(value))
    cohort = evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    result = evaluation.evaluate_flow(project, version, cohort['cohort_id'], engine=real_engine(monkeypatch), model_provider=model_provider(checkpoint))
    assert result['confusion']['NG']['OK'] == 1
    assert result['metrics']['escape_rate'] == 1
    escape = result['escapes'][0]
    assert escape['relative_path'] == 'defect.png'
    assert any(r['node_id'] == 'blob' and r['branch_verdict'] == 'OK' for r in escape['node_evidence'])
    assert escape['roi_evidence'][0]['bbox'] == [0,0,32,32]
    assert result['metrics']['overkill_rate'] == 0


def test_frozen_cohort_rejects_source_label_truth_checkpoint_and_graph_mutation(modules, workspace, monkeypatch):
    evaluation, truth = modules; project, _, version, checkpoint = workspace
    declare(truth, project, 'normal', 'OK')
    cohort = evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    result = evaluation.evaluate_flow(project, version, cohort['cohort_id'], engine=real_engine(monkeypatch), model_provider=model_provider(checkpoint))
    declare(truth, project, 'normal', 'UNKNOWN')
    with pytest.raises(ValueError, match='truth|Truth'):
        evaluation.evaluate_flow(project, version, cohort['cohort_id'], engine=real_engine(monkeypatch), model_provider=model_provider(checkpoint))
    assert evaluation.read_evaluation(project,result['evaluation_id'])['validity']['valid'] is False
    # The historical confusion is immutable even when current truth has changed.
    assert evaluation.read_evaluation(project,result['evaluation_id'])['confusion']['OK']['OK'] == 1


def test_freeze_rejects_train_images_and_cross_split_pixel_duplicates(modules, workspace):
    evaluation, _ = modules; project, _, version, checkpoint = workspace
    split = next((Path(project['dataset_dir'])/'splits').glob('*.json'))
    value=json.loads(split.read_text()); value['assignments']['normal.png']='train';split.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='test|held'):
        evaluation.freeze_cohort(project, version, relative_paths=['normal.png'], model_provider=model_provider(checkpoint))
    source=Path(project['source_dataset_dir']); (source/'duplicate.png').write_bytes((source/'defect.png').read_bytes())
    value['assignments']['duplicate.png']='train';split.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='leak|duplicate|Duplicate'):
        evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))


def test_truth_conflicts_and_unknown_defect_class_are_rejected(modules, workspace):
    _, truth = modules; project, _, _, _ = workspace
    old=truth.read_truth(project,str(Path(project['source_dataset_dir'])/'normal.png'),task='segmentation',classes=['background','crack'])
    declare(truth, project, 'normal', 'OK')
    from backend.engine.dataset_metadata import RevisionConflict
    with pytest.raises(RevisionConflict):
        truth.declare_truth(project,old['image_path'],task='segmentation',classes=['background','crack'],verdict='OK',
            reviewer='Reviewer',expected_revision=old['truth_revision'],expected_image_revision=old['image_revision'])
    with pytest.raises(ValueError,match='class'):
        declare(truth,project,'defect','NG',classes=['outside-scope'])


def test_explicit_normal_role_cannot_be_declared_as_defect_truth(modules, workspace):
    _, truth = modules; project, _, _, _ = workspace
    with pytest.raises(ValueError,match='normal|class'):
        declare(truth,project,'defect','NG',classes=['background'])
    image=str(Path(project['source_dataset_dir'])/'defect.png')
    with pytest.raises(ValueError,match='Segmentation roles'):
        truth.read_truth(project,image,task='segmentation',classes=['background','crack'],class_roles={'crack':'normal'})
    current=truth.read_truth(project,image,task='classification',classes=['background','crack'],class_roles={'crack':'normal'})
    with pytest.raises(ValueError,match='normal|class'):
        truth.declare_truth(project,image,task='classification',classes=['background','crack'],class_roles={'crack':'normal'},
            verdict='NG',defect_classes=['crack'],reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])


def test_label_revision_invalidates_truth_even_when_label_bytes_return_to_same_hash(modules, workspace):
    _,truth=modules;project,_,_,_=workspace
    declare(truth,project,'normal','OK')
    from backend.engine import dataset_metadata as dm
    image=Path(project['source_dataset_dir'])/'normal.png'
    dm.annotation_changed(project['project_dir'],project['source_dataset_dir'],image,actor='Labeler',annotation_root=project['annotations_dir'])
    row=truth.read_truth(project,str(image),task='segmentation',classes=['background','crack'])
    assert row['verdict']=='UNKNOWN'
    assert row['invalidated'] is True


@pytest.mark.parametrize('change', ['source','label','mask','checkpoint','graph'])
def test_saved_evaluation_discloses_changed_evidence(modules, workspace, monkeypatch, change):
    evaluation, truth=modules; project, _, version, checkpoint=workspace
    declare(truth,project,'normal','OK')
    cohort=evaluation.freeze_cohort(project,version,model_provider=model_provider(checkpoint))
    result=evaluation.evaluate_flow(project,version,cohort['cohort_id'],engine=real_engine(monkeypatch),model_provider=model_provider(checkpoint))
    source=Path(project['source_dataset_dir'])
    if change=='source': Image.new('RGB',(35,35),'red').save(source/'normal.png')
    elif change=='label': (source/'normal.json').write_text('{"shapes": []}')
    elif change=='mask':
        (source/'masks').mkdir();Image.new('L',(32,32)).save(source/'masks'/'normal.png')
    elif change=='checkpoint': checkpoint.write_bytes(b'changed')
    elif change=='graph':
        file=Path(project['project_dir'])/'flowcharts'/'versions'/f'{version}.json'
        row=json.loads(file.read_text());row['pipeline']['nodes'][-2]['data']['rule']='max_flaws_allowed';file.write_text(json.dumps(row))
    reopened=evaluation.read_evaluation(project,result['evaluation_id'])
    assert reopened['validity']['valid'] is False
    assert reopened['validity']['reasons']
    assert reopened['record_sha256']==result['record_sha256']


def test_truth_api_binds_session_reviewer_and_enforces_role_and_revision(workspace):
    from fastapi import FastAPI
    from backend.api.routes_image_truth import router
    from types import SimpleNamespace
    project, _, _, _=workspace
    app=FastAPI();app.include_router(router)
    role={'value':'reviewer'}
    app.state.accounts=SimpleNamespace(project_role=lambda account,project:role['value'])
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=project;request.state.account_user={'id':'session','username':'Session Reviewer'}
        return await call_next(request)
    client=ASGIClient(app)
    query=[('image_path',str(Path(project['source_dataset_dir'])/'normal.png')),('task','segmentation'),('classes','background'),('classes','crack')]
    current=client.get('/api/image-truth',params=query)
    assert current.status_code==200,current.text
    row=current.json()
    payload={'image_path':row['image_path'],'task':'segmentation','classes':['background','crack'],'verdict':'OK',
             'reviewer':'Forged Reviewer','expected_revision':row['truth_revision'],'expected_image_revision':row['image_revision']}
    role['value']='trainer'
    assert client.put('/api/image-truth',json=payload).status_code==403
    role['value']='reviewer'
    written=client.put('/api/image-truth',json=payload)
    assert written.status_code==200,written.text
    assert written.json()['reviewer']=='Session Reviewer'
    assert client.put('/api/image-truth',json=payload).status_code==409


def test_flow_evaluation_route_preserves_whole_flow_review_and_unknown_contract(workspace, monkeypatch):
    from fastapi import FastAPI
    from backend.api.routes_flow_evaluation import router
    from backend.engine import flow_evaluation as evaluation, image_truth as truth
    project, _, version, checkpoint=workspace
    declare(truth,project,'defect','NG',classes=['crack'])
    monkeypatch.setattr(evaluation,'verified_models',model_provider(checkpoint))
    engine=real_engine(monkeypatch)
    # Empty ROI is a real engine review decision, not a synthetic classifier metric.
    graph_file=Path(project['project_dir'])/'flowcharts'/'versions'/f'{version}.json'
    row=json.loads(graph_file.read_text())
    graph=row['pipeline'];graph['nodes'].insert(1,FlowNode(id='roi',position={},data=FlowNodeData(label='ROI',node_type='fixed_roi',params={'roi_bbox':[100,100,120,120]})).model_dump())
    graph['edges'][0]['target']='roi';graph['edges'].insert(1,{'id':'roi-inspect','source':'roi','target':'node_inspect'})
    graph_file.write_text(json.dumps(row))
    monkeypatch.setattr(evaluation,'FlowchartEngine',lambda device:engine)
    app=FastAPI();app.state.current_project=project;app.include_router(router);client=ASGIClient(app)
    frozen=client.post('/api/flow-evaluations/cohorts',json={'version_id':version})
    assert frozen.status_code==200,frozen.text
    response=client.post('/api/flow-evaluations',json={'version_id':version,'cohort_id':frozen.json()['cohort_id']})
    assert response.status_code==200,response.text
    result=response.json()
    assert result['confusion']['NG']['REVIEW']==1
    assert result['coverage']['unknown']==2
    assert result['metrics']['overkill_rate'] is None
    assert result['records'][0]['node_evidence']
    assert client.get('/api/flow-evaluations/'+result['evaluation_id']).json()['record_sha256']==result['record_sha256']
    queued=client.post('/api/flow-evaluations/'+result['evaluation_id']+'/review-queue')
    assert queued.status_code==200,queued.text
    queue=queued.json()
    assert queue['origin']['step']==5
    assert queue['origin']['flow_evaluation_id']==result['evaluation_id']
    assert len(queue['items'])==3
    assert queue['items'][0]['origin_evidence']['node_evidence']
    from backend.api.routes_data_workbench import _validate_origin
    assert _validate_origin(project,Path(project['source_dataset_dir']),queue)['id']==queue['id']

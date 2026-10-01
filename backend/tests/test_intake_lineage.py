"""Ancestor reuse requires owned intake lineage and unchanged frozen truth."""
import hashlib
import importlib
import json
from pathlib import Path

from PIL import Image
import pytest
import torch

from backend.engine import capture_intake, image_truth, flow_evaluation
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.flowchart_engine import FlowchartEngine, get_single_segmentation_flowchart
from backend.engine.inspection_service import InspectionStore
from backend.engine.warm_start import resolve_warm_start_parent
from backend.tests.test_whole_flow_evaluation import Pixels, ASGIClient


@pytest.fixture
def lineage():
    assert importlib.util.find_spec('backend.engine.intake_lineage'), 'Verified intake ancestor reuse is missing'
    return importlib.import_module('backend.engine.intake_lineage')


def bind_model(project, source, job_id, monkeypatch):
    from backend.api import routes_dataset, routes_dataset_versions
    monkeypatch.setattr(routes_dataset, 'STUDIO_ANNOTATIONS_DIR', Path(project['annotations_dir']))
    monkeypatch.setattr(routes_dataset, 'SPLIT_MANIFEST_DIR', Path(project['dataset_dir']) / 'splits')
    scoped = {**project, 'source_dataset_dir': str(source)}
    snapshot = routes_dataset_versions._snapshot(scoped, source, 'Exact parent data', '', 'snapshot')
    directory = Path(project['project_dir']) / 'versions' / snapshot['id']
    manifest = json.loads((directory / 'manifest.json').read_text())
    binding = {'dataset_version_id': snapshot['id'], 'version_dir': str(directory), 'labelset_id': 'default',
               'manifest_sha256': manifest['content_digest'], 'dataset_fingerprint': manifest['dataset_fingerprint']}
    job = Path(project['models_dir']) / job_id; job.mkdir(parents=True)
    payload = {'task': 'segmentation', 'classes': ['background', 'defect'], 'preset': 'fast',
               'model_state_dict': torch.nn.Linear(3, 2).state_dict(), 'training_provenance': binding}
    torch.save(payload, job / 'best_model.pt')
    checksum = hashlib.sha256((job / 'best_model.pt').read_bytes()).hexdigest()
    meta = {**payload, 'model_state_dict': {}, 'source_dataset_path': str(source), 'checkpoint_sha256': checksum}
    (job / 'model_meta.json').write_text(json.dumps(meta))
    (job / 'job_receipt.json').write_text(json.dumps({'job_id': job_id, 'status': 'completed', 'task': 'segmentation',
        'source_dataset_path': str(source), 'dataset_fingerprint': binding['dataset_fingerprint'],
        'checkpoint_sha256': checksum, 'training_provenance': binding}))
    return job / 'best_model.pt'


@pytest.fixture
def branch(tmp_path, monkeypatch, request):
    source = tmp_path / 'source'; source.mkdir(); root = tmp_path / 'project'; root.mkdir()
    p = {'id': 'lineage-project', 'project_dir': str(root), 'source_dataset_dir': str(source),
         'annotations_dir': str(root / 'annotations'), 'dataset_dir': str(root / 'dataset'),
         'models_dir': str(root / 'models'), 'reports_dir': str(root / 'reports'), 'task': 'segmentation', 'active_labelset_id': 'default'}
    for name, color in [('train', 'gray'), ('ng', 'red'), ('ok', 'black'), ('unknown', 'white')]:
        Image.new('RGB', (32, 32), color).save(source / f'{name}.png')
        (source / f'{name}.json').write_text(json.dumps({'imagePath': f'{name}.png', 'imageWidth':32,'imageHeight':32,
            'shapes': [{'label':'defect','shape_type':'polygon','points':[[1,1],[5,1],[5,5]]}] if name=='train' else []}))
    key = hashlib.sha256(str(source).encode()).hexdigest(); split = root / 'dataset' / 'splits' / f'{key}.json'; split.parent.mkdir(parents=True)
    absolute = getattr(request, 'param', None) == 'absolute'
    split.write_text(json.dumps({'folder_path': str(source), 'assignments': {(str(source/f'{name}.png') if absolute else f'{name}.png'): 'train' if name == 'train' else 'test' for name in ['train', 'ng', 'ok', 'unknown']}}))
    checkpoint = bind_model(p, source, 'job_parent', monkeypatch)
    graph = get_single_segmentation_flowchart('job_parent')
    graph.nodes[1].data.params.update(class_names=['background', 'defect'], min_defect_area_px=1)
    version = '2' * 32; folder = root / 'flowcharts' / 'versions'; folder.mkdir(parents=True)
    (folder / f'{version}.json').write_text(json.dumps({'version_id': version, 'source_dataset_path': str(source), 'pipeline': graph.model_dump()}))
    for name, verdict in [('ng', 'NG'), ('ok', 'OK')]:
        image = source / f'{name}.png'; current = image_truth.read_truth(p, str(image), task='segmentation', classes=['background', 'defect'])
        image_truth.declare_truth(p, str(image), task='segmentation', classes=['background', 'defect'], verdict=verdict,
            defect_classes=['defect'] if verdict == 'NG' else [], reviewer='Reviewer',
            expected_revision=current['truth_revision'], expected_image_revision=current['image_revision'])
    provider = lambda p, graph: {('job_parent', 'segmentation'): {'path': str(checkpoint), 'metadata': {'classes': ['background', 'defect']}}}
    cohort = flow_evaluation.freeze_cohort(p, version, model_provider=provider)
    store = InspectionStore(root / 'runtime_service' / 'state'); upload = store.state_dir / 'uploads' / 'capture.png'
    Image.new('RGB', (32, 32), 'blue').save(upload); identifier = store.enqueue(upload, 'http'); store.claim(); store.finish(identifier, result={'status': 'success', 'final_verdict': 'OK'})
    candidate = capture_intake.register_service_jobs(p, job_ids=[identifier])['candidates'][0]
    capture_intake.review_candidate(p, candidate['candidate_id'], expected_revision=1, actor='Reviewer', decision='adopt')
    adopted = capture_intake.adopt_candidates(p, [candidate['candidate_id']], actor='Reviewer', name='Owned candidate data')
    newp = {**p, 'source_dataset_dir': adopted['source_dataset_path']}; (root / 'project.json').write_text(json.dumps(newp))
    return p, newp, checkpoint, cohort, adopted


def test_verified_intake_branch_can_use_original_parent_with_exact_classes_and_binding(lineage, branch):
    _, project, checkpoint, cohort, _ = branch
    parent = resolve_warm_start_parent('job_parent', project['models_dir'], project['source_dataset_dir'], 'segmentation', 'segmentation:fast')
    assert parent.checkpoint_path == checkpoint
    verified = lineage.verify_ancestor_model(project, Path(project['source_dataset_dir']), checkpoint, 'segmentation')
    assert verified['cohort_id'] == cohort['cohort_id']
    assert verified['truth_sha256'] == cohort['truth_sha256']
    assert len(verified['images']) == 3
    assert sum(row['ground_truth_verdict'] is None for row in verified['images']) == 1
    from backend.engine.image_truth import read_truth
    assert read_truth(project, str(Path(project['source_dataset_dir'])/'ok.png'), task='segmentation', classes=['background', 'defect'])['verdict'] == 'UNKNOWN', 'ancestor truth is evidence for its original source, never a new-source OK declaration'


@pytest.mark.parametrize('branch', ['absolute'], indirect=True)
def test_app_absolute_split_survives_owned_adoption_and_exact_parent_reuse(lineage, branch):
    old, project, checkpoint, cohort, adopted = branch
    split = flow_evaluation._split(old)[0]
    before = split.read_bytes()
    assert json.loads(before)['assignments'][str(Path(old['source_dataset_dir'])/'ok.png')] == 'test'
    parent = resolve_warm_start_parent('job_parent', project['models_dir'], project['source_dataset_dir'], 'segmentation', 'segmentation:fast')
    assert parent.checkpoint_path == checkpoint
    verified = lineage.verify_ancestor_model(project, Path(project['source_dataset_dir']), checkpoint, 'segmentation')
    assert verified['cohort_id'] == cohort['cohort_id']
    assert adopted['split_assignments']['ok.png'] == 'test'
    assert split.read_bytes() == before


@pytest.mark.parametrize('change', ['record', 'split', 'test_image', 'test_label', 'truth', 'checkpoint', 'binding', 'missing_cohort', 'class_roles'])
def test_ancestor_reuse_fails_closed_for_changed_or_missing_evidence(lineage, branch, change):
    old, project, checkpoint, cohort, adopted = branch
    source = Path(project['source_dataset_dir']); root = Path(project['project_dir'])
    if change == 'record': (source.parent/'record.json').write_text('{}')
    elif change == 'split':
        split = root/'dataset'/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
        value = json.loads(split.read_text()); value['assignments']['ok.png']='train'; split.write_text(json.dumps(value))
    elif change == 'test_image': Image.new('RGB',(32,32),'yellow').save(source/'ok.png')
    elif change == 'test_label':
        overlay = dataset_annotation_dir(source, Path(project['annotations_dir']), use_scope=False); overlay.mkdir(parents=True,exist_ok=True)
        (overlay/'ok.json').write_text('{"annotations": [{"type":"polygon","label":"defect","points":[[1,1],[5,1],[5,5]]}]}')
    elif change == 'truth':
        image = Path(old['source_dataset_dir'])/'ok.png'; current=image_truth.read_truth(old,str(image),task='segmentation',classes=['background','defect'])
        image_truth.declare_truth(old,str(image),task='segmentation',classes=['background','defect'],verdict='UNKNOWN',reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    elif change == 'checkpoint': checkpoint.write_bytes(checkpoint.read_bytes()+b'changed')
    elif change == 'binding':
        manifest = Path(json.loads((checkpoint.parent/'model_meta.json').read_text())['training_provenance']['version_dir'])/'manifest.json';manifest.write_text('{}')
    elif change == 'missing_cohort': (root/'flow_evaluations'/'cohorts'/cohort['cohort_id']/'record.json').unlink()
    elif change == 'class_roles':
        path=checkpoint.parent/'model_meta.json';value=json.loads(path.read_text());value['class_semantics']={'version':1,'roles':{'background':'defect','defect':'normal'}};path.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        lineage.verify_ancestor_model(project, source, checkpoint, 'segmentation')


def test_training_overlay_edits_can_advance_but_same_byte_test_label_revision_cannot(lineage, branch):
    _, p, checkpoint, _, _ = branch; source=Path(p['source_dataset_dir'])
    overlay=dataset_annotation_dir(source,Path(p['annotations_dir']),use_scope=False);overlay.mkdir(parents=True,exist_ok=True)
    (overlay/'train.json').write_text('{"annotations": [{"type":"polygon","label":"defect","points":[[1,1],[5,1],[5,5]]}]}')
    assert lineage.verify_ancestor_model(p,source,checkpoint,'segmentation')['images']
    from backend.engine import dataset_metadata as dm
    dm.annotation_changed(p['project_dir'],source,source/'ok.png',actor='Labeler',annotation_root=p['annotations_dir'])
    with pytest.raises(ValueError,match='label|truth|revision'):
        lineage.verify_ancestor_model(p,source,checkpoint,'segmentation')


def test_real_comparison_executes_both_models_on_same_frozen_bytes_and_preserves_unknown(lineage, branch, monkeypatch):
    from fastapi import FastAPI
    from backend.api import routes_model_comparisons as comparison
    _, p, parent, cohort, adopted = branch
    candidate=bind_model(p,Path(p['source_dataset_dir']),'job_candidate',monkeypatch)
    calls=[]
    class Engine(FlowchartEngine):
        def _get_inspection_model(self, **kwargs):
            self._model_input_sizes[('segmentation',kwargs['job_id'],'fast')]=(32,32)
            return Pixels(),True
        def execute(self, **kwargs):
            calls.append((kwargs['pipeline'].nodes[1].data.model_job_id,kwargs['image_path']))
            return super().execute(**kwargs)
    monkeypatch.setattr(comparison,'FlowchartEngine',Engine)
    app=FastAPI();app.include_router(comparison.router,prefix='/api/evaluation')
    @app.middleware('http')
    async def scope(request,call_next):request.state.scoped_project=p;return await call_next(request)
    client=ASGIClient(app)
    result=client.post('/api/evaluation/model-comparisons',json={'source_dataset_path':p['source_dataset_dir'],'task':'segmentation','incumbent_job_id':'job_parent','candidate_job_id':'job_candidate','full_test':True})
    assert result.status_code==200,result.text
    report=result.json();assert report['intake_lineage']['cohort_id']==cohort['cohort_id']
    assert report['summary']['unknown_truth_images']==1 and report['summary']['known_ok_images']==1
    assert len(calls)==6
    assert {path for job,path in calls if job=='job_parent'}=={path for job,path in calls if job=='job_candidate'}
    assert all(Path(path).is_relative_to(Path(p['project_dir'])/'flow_evaluations'/'cohorts'/cohort['cohort_id']) for _,path in calls)
    assert all(row['evaluation_file_path'] != row['file_path'] and row['truth_sha256'] for row in report['images'])
    assert not any(adopted['adopted'][0]['relative_path'] in path for _,path in calls)
    reopened=client.get('/api/evaluation/model-comparisons/'+report['comparison_id'],params={'source_dataset_path':p['source_dataset_dir'],'task':'segmentation'})
    assert reopened.json()==report


def test_repeated_adoption_retains_original_frozen_cohort_for_a_verified_intermediate_parent(lineage, branch, monkeypatch):
    old,p,_,cohort,_=branch;source=Path(p['source_dataset_dir'])
    intermediate=bind_model(p,source,'job_intermediate',monkeypatch)
    store=InspectionStore(Path(p['project_dir'])/'runtime_service'/'state');upload=store.state_dir/'uploads'/'second.png'
    Image.new('RGB',(32,32),'purple').save(upload);job=store.enqueue(upload,'http');store.claim();store.finish(job,result={'status':'success','final_verdict':'NG'})
    row=capture_intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    capture_intake.review_candidate(p,row['candidate_id'],expected_revision=1,actor='Reviewer',decision='adopt')
    adopted=capture_intake.adopt_candidates(p,[row['candidate_id']],actor='Reviewer',name='Second owned version')
    current={**p,'source_dataset_dir':adopted['source_dataset_path']};(Path(p['project_dir'])/'project.json').write_text(json.dumps(current))
    result=lineage.verify_ancestor_model(current,Path(current['source_dataset_dir']),intermediate,'segmentation')
    assert result['cohort_id']==cohort['cohort_id']
    assert result['truth_source_dataset_path']==old['source_dataset_dir']
    assert result['ancestor_source_dataset_path']==str(source)
    assert len(result['version_ids'])==2
    assert all(row['evaluation_file_path'].startswith(str(Path(p['project_dir'])/'flow_evaluations'/'cohorts')) for row in result['images'])


def test_lineage_comparison_rejects_a_candidate_with_incompatible_class_order(lineage, branch, monkeypatch):
    from backend.api.routes_model_comparisons import _run_comparison,ComparisonRequest
    from fastapi import HTTPException
    _,p,_,_,_=branch
    checkpoint=bind_model(p,Path(p['source_dataset_dir']),'job_candidate',monkeypatch)
    payload=torch.load(checkpoint,weights_only=True);payload['classes']=['defect','background'];torch.save(payload,checkpoint)
    checksum=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    for name in ['model_meta.json','job_receipt.json']:
        path=checkpoint.parent/name;record=json.loads(path.read_text());record['checkpoint_sha256']=checksum
        if name=='model_meta.json':record['classes']=payload['classes']
        path.write_text(json.dumps(record))
    with pytest.raises(HTTPException,match='ordered classes'):
        _run_comparison(ComparisonRequest(source_dataset_path=p['source_dataset_dir'],task='segmentation',incumbent_job_id='job_parent',candidate_job_id='job_candidate',full_test=True),p,Path(p['source_dataset_dir']))


def test_current_candidate_roles_must_match_checkpoint_and_metadata(lineage, branch, monkeypatch):
    _,p,_,_,_=branch;source=Path(p['source_dataset_dir'])
    checkpoint=bind_model(p,source,'job_candidate',monkeypatch)
    path=checkpoint.parent/'model_meta.json';meta=json.loads(path.read_text())
    roles={'background':'defect','defect':'normal'};meta['class_semantics']={'version':1,'roles':roles};path.write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='roles'):
        lineage.verify_current_model(p,source,checkpoint,'segmentation',{'classes':['background','defect'],'class_roles':roles})


@pytest.mark.parametrize('metadata_field', ['classes', 'class_names'])
@pytest.mark.parametrize('checkpoint_field', ['classes', 'class_names'])
def test_intake_segmentation_accepts_ordered_vocabulary_aliases_with_recorded_roles(lineage, branch, metadata_field, checkpoint_field):
    from backend.engine.class_semantics import class_semantics_record
    _, project, checkpoint, cohort, _ = branch
    payload = torch.load(checkpoint, weights_only=True)
    names = payload.pop('classes')
    semantics = class_semantics_record(names, task='segmentation')
    payload.update({checkpoint_field: names, 'class_semantics': semantics})
    torch.save(payload, checkpoint)
    checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    metadata_path = checkpoint.parent / 'model_meta.json'
    metadata = json.loads(metadata_path.read_text()); metadata.pop('classes')
    metadata.update({metadata_field: names, 'class_semantics': semantics, 'checkpoint_sha256': checksum})
    metadata_path.write_text(json.dumps(metadata))
    receipt_path = checkpoint.parent / 'job_receipt.json'
    receipt = json.loads(receipt_path.read_text()); receipt['checkpoint_sha256'] = checksum
    receipt_path.write_text(json.dumps(receipt))
    result = lineage.verify_ancestor_model(project, Path(project['source_dataset_dir']), checkpoint, 'segmentation')
    assert result['classes'] == names and result['cohort_id'] == cohort['cohort_id']
    assert result['class_roles'] == {'background': 'normal', 'defect': 'defect'}
    metadata['class_semantics']['roles'] = {'background': 'defect', 'defect': 'normal'}
    metadata_path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='roles'):
        lineage.verify_ancestor_model(project, Path(project['source_dataset_dir']), checkpoint, 'segmentation')

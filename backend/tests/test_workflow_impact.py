import hashlib
import json
from pathlib import Path

from backend.engine import workflow_impact


def project_fixture(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'one.png').write_bytes(b'image')
    root=tmp_path/'project';root.mkdir()
    project={'id':'p','project_dir':str(root),'models_dir':str(root/'models'),'dataset_dir':str(root/'dataset'),
             'annotations_dir':str(root/'annotations'),'source_dataset_dir':str(source),'active_labelset_id':'default','task':'classification'}
    model=root/'models'/'job_one';model.mkdir(parents=True);checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'model-v1')
    (model/'model_meta.json').write_text(json.dumps({'task':'classification','source_dataset_path':str(source),'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()}))
    return project,checkpoint


def test_changed_checkpoint_marks_dependent_flow_and_evaluation_stale(tmp_path):
    project,checkpoint=project_fixture(tmp_path);root=Path(project['project_dir'])
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.flow_provenance import pipeline_sha256
    graph=get_single_segmentation_flowchart('job_one')
    directory=root/'flowcharts'/'versions';directory.mkdir(parents=True)
    (directory/('a'*32+'.json')).write_text(json.dumps({'version_id':'a'*32,'source_dataset_path':project['source_dataset_dir'],
        'pipeline':graph.model_dump(),'pipeline_hash':pipeline_sha256(graph)}))
    history=root/'reports'/'evaluations';history.mkdir(parents=True)
    from backend.engine.evaluation_history import EvaluationHistory
    EvaluationHistory(history).append({'job_id':'job_one','task':'classification','test_predictions':[]},
        {'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()})
    checkpoint.write_bytes(b'model-v2')
    report=workflow_impact.analyze(project)
    assert report['models'][0]['checkpoint_state']=='changed'
    assert report['flows'][0]['state']=='changed'
    assert report['model_evaluations'][0]['state']=='changed'
    assert 'evaluate_model' in report['required_actions']
    assert 'evaluate_flow' in report['required_actions']
    assert checkpoint.read_bytes()==b'model-v2'


def test_legacy_model_without_bound_labels_cannot_claim_current_training_data(tmp_path):
    project,_=project_fixture(tmp_path)
    report=workflow_impact.analyze(project)
    assert report['models'][0]['data_state']=='unverified'
    assert report['quality_approved'] is False


def test_changed_truth_requests_flow_revalidation(tmp_path,monkeypatch):
    project,_=project_fixture(tmp_path);root=Path(project['project_dir'])
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.engine import flow_evaluation
    graph=get_single_segmentation_flowchart('job_one');digest=pipeline_sha256(graph)
    directory=root/'flowcharts'/'versions';directory.mkdir(parents=True)
    (directory/('a'*32+'.json')).write_text(json.dumps({'version_id':'a'*32,'source_dataset_path':project['source_dataset_dir'],'pipeline':graph.model_dump(),'pipeline_hash':digest}))
    (root/'flow_evaluations'/'runs').mkdir(parents=True)
    monkeypatch.setattr(flow_evaluation,'list_evidence',lambda *_:[{'version_id':'a'*32,'graph_sha256':digest,'validity':{'valid':False,'reasons':['truth_changed']}}])
    report=workflow_impact.analyze(project)
    assert report['flows'][0]['state']=='revalidation_required'
    assert 'evaluate_flow' in report['required_actions']


def test_switching_source_keeps_previous_model_and_flow_evidence_visible(tmp_path):
    project,checkpoint=project_fixture(tmp_path);root=Path(project['project_dir'])
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    graph=get_single_segmentation_flowchart('job_one')
    directory=root/'flowcharts'/'versions';directory.mkdir(parents=True)
    (directory/('a'*32+'.json')).write_text(json.dumps({'version_id':'a'*32,'source_dataset_path':project['source_dataset_dir'],'pipeline':graph.model_dump()}))
    new=tmp_path/'new-source';new.mkdir();project['source_dataset_dir']=str(new)
    report=workflow_impact.analyze(project)
    assert report['models'][0]['scope_matches'] is False
    assert report['flows'][0]['scope_matches'] is False
    assert report['models'][0]['job_id']==checkpoint.parent.name


def test_missing_bound_version_requires_new_training_and_does_not_follow_foreign_path(tmp_path):
    project,checkpoint=project_fixture(tmp_path)
    meta=checkpoint.parent/'model_meta.json';value=json.loads(meta.read_text())
    value['training_provenance']={'version_dir':str(tmp_path/'foreign'),'manifest_sha256':'f'*64}
    meta.write_text(json.dumps(value))
    report=workflow_impact.analyze(project)
    assert report['models'][0]['data_state']=='unverified'
    assert 'foreign' not in json.dumps(report['models'][0].get('manifest',{}))
    assert 'verify_dataset_version' in report['required_actions']


def test_changed_labels_require_retraining_and_preserve_bound_snapshot(tmp_path, monkeypatch):
    from backend.api import routes_dataset, routes_dataset_versions
    project,checkpoint=project_fixture(tmp_path)
    source=Path(project['source_dataset_dir']);label=source/'one.json'
    label.write_text(json.dumps({'shapes':[{'label':'Bow'}]}))
    monkeypatch.setattr(routes_dataset,'STUDIO_ANNOTATIONS_DIR',Path(project['annotations_dir']))
    monkeypatch.setattr(routes_dataset,'SPLIT_MANIFEST_DIR',Path(project['dataset_dir'])/'splits')
    snapshot=routes_dataset_versions._snapshot(project,source,'Reviewed labels','','manual')
    directory=Path(project['project_dir'])/'versions'/snapshot['id']
    bound=(directory/'manifest.json').read_bytes()
    metadata=checkpoint.parent/'model_meta.json';value=json.loads(metadata.read_text())
    value['training_provenance']={'version_dir':str(directory),'manifest_sha256':json.loads(bound)['content_digest'],'dataset_version_id':snapshot['id']}
    metadata.write_text(json.dumps(value))
    assert workflow_impact.analyze(project)['models'][0]['data_state']=='current'
    label.write_text(json.dumps({'shapes':[{'label':'Changed'}]}))
    report=workflow_impact.analyze(project)
    assert report['models'][0]['data_state']=='changed'
    assert {'train_candidate','compare_fixed_cohort','approve_model'}<=set(report['required_actions'])
    assert (directory/'manifest.json').read_bytes()==bound


def test_impact_endpoint_reads_selected_project_and_keeps_history(tmp_path):
    import asyncio
    import httpx
    from fastapi import FastAPI
    from backend.api import routes_project,routes_provenance
    app=FastAPI();app.state.project_dir=tmp_path/'projects'
    app.include_router(routes_project.router);app.include_router(routes_provenance.router)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            first=(await client.post('/api/project/create',json={'name':'First impact','task':'segmentation'})).json()
            assert (await client.get('/api/provenance/impact')).json()['project_id']==first['id']
            second=(await client.post('/api/project/create',json={'name':'Second impact','task':'segmentation'})).json()
            result=await client.get('/api/provenance/impact')
            assert result.status_code==200,result.text
            assert result.json()['project_id']==second['id']
            assert result.json()['models']==[]
            assert Path(first['project_dir']).is_dir()
    asyncio.run(check())


def test_metadata_tags_and_truth_do_not_require_retraining(tmp_path, monkeypatch):
    from backend.api import routes_dataset, routes_dataset_versions
    from backend.engine import dataset_metadata as dm, image_truth
    project,checkpoint=project_fixture(tmp_path)
    source=Path(project['source_dataset_dir'])
    from PIL import Image
    Image.new('RGB',(8,8)).save(source/'one.png')
    monkeypatch.setattr(routes_dataset,'STUDIO_ANNOTATIONS_DIR',Path(project['annotations_dir']))
    monkeypatch.setattr(routes_dataset,'SPLIT_MANIFEST_DIR',Path(project['dataset_dir'])/'splits')
    row=dm.metadata_for_path(Path(project['project_dir']),source,source/'one.png',Path(project['annotations_dir']))
    snapshot=routes_dataset_versions._snapshot(project,source,'Training labels','','manual')
    directory=Path(project['project_dir'])/'versions'/snapshot['id']
    meta=checkpoint.parent/'model_meta.json';value=json.loads(meta.read_text())
    value['training_provenance']={'version_dir':str(directory),'manifest_sha256':json.loads((directory/'manifest.json').read_text())['content_digest']}
    meta.write_text(json.dumps(value))
    dm.update_metadata(project['project_dir'],source,row['image_uuid'],row['revision'],'Reviewer',{'tags':['Lot A']},project['annotations_dir'])
    truth=image_truth.read_truth(project,str(source/'one.png'),task='classification',classes=['OK','Bow'])
    image_truth.declare_truth(project,str(source/'one.png'),task='classification',classes=['OK','Bow'],verdict='NG',defect_classes=['Bow'],reviewer='Reviewer',expected_revision=truth['truth_revision'],expected_image_revision=truth['image_revision'])
    report=workflow_impact.analyze(project)
    assert report['models'][0]['data_state']=='current'
    assert 'train_candidate' not in report['required_actions']


def test_current_valid_flow_evaluation_supersedes_invalid_history(tmp_path,monkeypatch):
    project,_=project_fixture(tmp_path);root=Path(project['project_dir'])
    from backend.engine.flowchart_engine import get_single_segmentation_flowchart
    from backend.engine.flow_provenance import pipeline_sha256
    from backend.engine import flow_evaluation
    graph=get_single_segmentation_flowchart('job_one');digest=pipeline_sha256(graph)
    directory=root/'flowcharts'/'versions';directory.mkdir(parents=True)
    (directory/('a'*32+'.json')).write_text(json.dumps({'version_id':'a'*32,'source_dataset_path':project['source_dataset_dir'],'pipeline':graph.model_dump(),'pipeline_hash':digest}))
    (root/'flow_evaluations'/'runs').mkdir(parents=True)
    rows=[{'version_id':'a'*32,'graph_sha256':digest,'validity':{'valid':True}}, {'version_id':'a'*32,'graph_sha256':digest,'validity':{'valid':False,'reasons':['truth_changed']}}]
    monkeypatch.setattr(flow_evaluation,'list_evidence',lambda *_:rows)
    report=workflow_impact.analyze(project)
    assert report['flows'][0]['state']=='current'
    assert 'evaluate_flow' not in report['required_actions']
    assert len(report['flow_evaluations'])==2


def test_review_eligibility_change_requires_candidate_training(tmp_path,monkeypatch):
    from backend.api import routes_dataset
    from backend.engine import dataset_metadata as dm
    from backend.engine.training_provenance import bind_training_version
    from PIL import Image
    project,checkpoint=project_fixture(tmp_path)
    source=Path(project['source_dataset_dir']);Image.new('RGB',(8,8)).save(source/'one.png')
    monkeypatch.setattr(routes_dataset,'STUDIO_ANNOTATIONS_DIR',Path(project['annotations_dir']))
    monkeypatch.setattr(routes_dataset,'SPLIT_MANIFEST_DIR',Path(project['dataset_dir'])/'splits')
    binding=bind_training_version(project,source)
    meta=checkpoint.parent/'model_meta.json';value=json.loads(meta.read_text());value['training_provenance']=binding
    meta.write_text(json.dumps(value))
    assert workflow_impact.analyze(project)['models'][0]['data_state']=='current'
    row=dm.metadata_for_path(Path(project['project_dir']),source,source/'one.png',Path(project['annotations_dir']))
    dm.update_metadata(project['project_dir'],source,row['image_uuid'],row['revision'],'Reviewer',{'usage_state':'not_used'},project['annotations_dir'])
    report=workflow_impact.analyze(project)
    assert report['models'][0]['data_state']=='changed'
    assert 'train_candidate' in report['required_actions']

"""A comparison is saved evidence for one project and one exact test sample."""

import hashlib
import json
from pathlib import Path

import torch
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset
from backend.api.routes_model_comparisons import _summary
from backend.engine.classification.model import create_classification_model
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.main import create_app


def test_comparison_accepts_segmentation_class_names_metadata(tmp_path):
    from backend.engine.comparison_truth import bind_truth
    from backend.engine.class_semantics import class_semantics_record
    source=tmp_path/'source';source.mkdir()
    job=tmp_path/'job';job.mkdir()
    names=['background','scratch']
    semantics=class_semantics_record(names,task='segmentation')
    checkpoint=job/'best_model.pt'
    torch.save({'task':'segmentation','classes':names,'class_semantics':semantics},checkpoint)
    (job/'model_meta.json').write_text(json.dumps({'task':'segmentation','class_names':names,'class_semantics':semantics}))
    binding=bind_truth({'id':'comparison-project','project_dir':str(tmp_path),'source_dataset_dir':str(source),'active_labelset_id':'default'},
                       source,'segmentation',[{'task':'segmentation','checkpoint_path':str(checkpoint)}],[])
    assert binding['scope']['classes']==names
    assert binding['scope']['class_semantics']['roles']=={'background':'normal','scratch':'defect'}


def test_checkpoint_change_after_truth_binding_cannot_complete_comparison(tmp_path,monkeypatch):
    import pytest
    from fastapi import HTTPException
    from backend.api.routes_model_comparisons import ComparisonRequest,_run_comparison,_fingerprint
    from backend.engine.class_semantics import class_semantics_record
    monkeypatch.chdir(tmp_path)
    client=_client(tmp_path);source=tmp_path/'source'
    folder=source/'test'/'OK';folder.mkdir(parents=True)
    Image.new('RGB',(64,64),'white').save(folder/'part.png')
    project=client.post('/api/project/create',json={'name':'Binding race','task':'classification'}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    project['source_dataset_dir']=str(source)
    fingerprint=_fingerprint(source)
    for job in ['job_incumbent','job_candidate']:
        _checkpoint(Path(project['models_dir'])/job,source,fingerprint,0)
    candidate=Path(project['models_dir'])/'job_candidate'/'best_model.pt'
    def progress(done,total):
        if done==0:
            payload=torch.load(candidate,weights_only=True)
            payload['class_semantics']=class_semantics_record(payload['classes'],{'OK':'defect','NG':'normal'})
            torch.save(payload,candidate)
    request=ComparisonRequest(source_dataset_path=str(source),task='classification',
                              incumbent_job_id='job_incumbent',candidate_job_id='job_candidate',full_test=True)
    with pytest.raises(HTTPException,match='checkpoint'):
        _run_comparison(request,project,source,progress=progress)
    assert not list((Path(project['reports_dir'])/'model_comparisons').glob('comparison_*.json'))


def test_reviewed_unknown_survives_equivalent_recorded_role_basis(tmp_path):
    from backend.engine import image_truth
    from backend.engine.comparison_truth import bind_truth,verify_truth
    from backend.engine.class_semantics import class_semantics_record
    source=tmp_path/'source';source.mkdir()
    image=source/'ok.png';Image.new('RGB',(32,32),'white').save(image)
    project={'id':'truth-project','project_dir':str(tmp_path),'source_dataset_dir':str(source),
             'annotations_dir':str(tmp_path/'annotations'),'active_labelset_id':'default'}
    names=['OK','NG'];roles={'OK':'normal','NG':'defect'}
    current=image_truth.read_truth(project,str(image),task='classification',classes=names)
    declared=image_truth.declare_truth(project,str(image),task='classification',classes=names,
        verdict='UNKNOWN',reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    job=tmp_path/'job';job.mkdir()
    semantics=class_semantics_record(names,roles)
    torch.save({'task':'classification','classes':names,'class_semantics':semantics},job/'best_model.pt')
    (job/'model_meta.json').write_text(json.dumps({'task':'classification','classes':names,'class_semantics':semantics}))
    rows=[{'file_path':str(image),'ground_truth_label':'OK'}]
    binding=bind_truth(project,source,'classification',[{'task':'classification','checkpoint_path':str(job/'best_model.pt')}],rows)
    assert rows[0]['ground_truth_verdict'] is None
    assert rows[0]['truth_basis']=='reviewed_declaration'
    verify_truth(project,source,binding)
    equivalent=image_truth.read_truth(project,str(image),task='classification',classes=names,class_roles=roles)
    assert equivalent['declaration']['sha256']==declared['declaration']['sha256']
    assert equivalent['truth_revision']==1
    changed=image_truth.read_truth(project,str(image),task='classification',classes=names,class_roles={'OK':'defect','NG':'normal'})
    assert changed['declaration'] is None
    updated=image_truth.declare_truth(project,str(image),task='classification',classes=names,class_roles=roles,
        verdict='OK',reviewer='Reviewer',expected_revision=equivalent['truth_revision'],expected_image_revision=equivalent['image_revision'])
    assert updated['truth_revision']==2
    assert image_truth.read_truth(project,str(image),task='classification',classes=names)['verdict']=='OK'


@pytest.mark.parametrize('names',[['NG','OK'],['OK','NG','dust']])
def test_changed_truth_vocabulary_never_reverts_to_folder_label(tmp_path,names):
    from backend.engine import image_truth
    from backend.engine.comparison_truth import bind_truth,verify_truth
    from backend.engine.class_semantics import class_semantics_record
    source=tmp_path/'source';source.mkdir()
    image=source/'ok.png';Image.new('RGB',(32,32),'white').save(image)
    project={'id':'truth-project','project_dir':str(tmp_path),'source_dataset_dir':str(source),
             'annotations_dir':str(tmp_path/'annotations'),'active_labelset_id':'default'}
    current=image_truth.read_truth(project,str(image),task='classification',classes=['OK','NG'])
    image_truth.declare_truth(project,str(image),task='classification',classes=['OK','NG'],
        verdict='UNKNOWN',reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    job=tmp_path/'job';job.mkdir()
    semantics=class_semantics_record(names)
    torch.save({'task':'classification','classes':names,'class_semantics':semantics},job/'best_model.pt')
    (job/'model_meta.json').write_text(json.dumps({'task':'classification','classes':names,'class_semantics':semantics}))
    rows=[{'file_path':str(image),'ground_truth_label':'OK'}]
    binding=bind_truth(project,source,'classification',[{'task':'classification','checkpoint_path':str(job/'best_model.pt')}],rows)
    assert rows[0]['ground_truth_verdict'] is None
    assert rows[0]['truth_basis']=='reviewed_declaration'
    verify_truth(project,source,binding)


@pytest.mark.parametrize('selected_task,source_task,names',[
    ('segmentation','classification',['background','good']),
    ('classification','segmentation',['OK','NG']),
])
def test_homogeneous_foreign_task_models_preserve_their_reviewed_truth(tmp_path,selected_task,source_task,names):
    from backend.engine import image_truth
    from backend.engine.comparison_truth import bind_truth,verify_truth
    source=tmp_path/'source';source.mkdir()
    image=source/'part.png';Image.new('RGB',(32,32),'white').save(image)
    project={'id':'truth-project','project_dir':str(tmp_path),'source_dataset_dir':str(source),
             'annotations_dir':str(tmp_path/'annotations'),'active_labelset_id':'default'}
    current=image_truth.read_truth(project,str(image),task=selected_task,classes=names)
    image_truth.declare_truth(project,str(image),task=selected_task,classes=names,verdict='UNKNOWN',
        reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    specs=[]
    for job_id in ('first','second'):
        job=tmp_path/job_id;job.mkdir()
        torch.save({'task':selected_task,'classes':names},job/'best_model.pt')
        (job/'model_meta.json').write_text(json.dumps({'task':selected_task,'classes':names}))
        specs.append({'task':selected_task,'checkpoint_path':str(job/'best_model.pt')})
    rows=[{'file_path':str(image),'ground_truth_label':names[-1]}]
    binding=bind_truth(project,source,source_task,specs,rows)
    assert binding['scope']['task']==selected_task
    assert binding['scope']['class_semantics']['roles'][names[-1]]=='defect'
    assert rows[0]['ground_truth_verdict'] is None
    assert rows[0]['truth_basis']=='reviewed_declaration'
    verify_truth(project,source,binding)


def test_latest_truth_revision_wins_after_clock_moves_backwards(tmp_path,monkeypatch):
    from backend.engine import image_truth,dataset_metadata
    source=tmp_path/'source';source.mkdir()
    image=source/'ok.png';Image.new('RGB',(32,32),'white').save(image)
    project={'id':'truth-project','project_dir':str(tmp_path),'source_dataset_dir':str(source),
             'annotations_dir':str(tmp_path/'annotations'),'active_labelset_id':'default'}
    names=['OK','NG'];roles={'OK':'normal','NG':'defect'}
    monkeypatch.setattr(dataset_metadata,'_now',lambda:'2030-01-01T00:00:00Z')
    current=image_truth.read_truth(project,str(image),task='classification',classes=names)
    first=image_truth.declare_truth(project,str(image),task='classification',classes=names,
        verdict='UNKNOWN',reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    monkeypatch.setattr(dataset_metadata,'_now',lambda:'2020-01-01T00:00:00Z')
    second=image_truth.declare_truth(project,str(image),task='classification',classes=names,class_roles=roles,
        verdict='OK',reviewer='Reviewer',expected_revision=first['truth_revision'],expected_image_revision=first['image_revision'])
    assert second['truth_revision']==2
    assert image_truth.read_truth(project,str(image),task='classification',classes=names)['verdict']=='OK'


def test_comparison_uses_agreed_checkpoint_roles_and_reviewed_truth(tmp_path,monkeypatch):
    from backend.engine.class_semantics import class_semantics_record
    from backend.engine import image_truth
    monkeypatch.chdir(tmp_path)
    client=_client(tmp_path);source=tmp_path/'source'
    folder=source/'test'/'acceptable';folder.mkdir(parents=True)
    image=folder/'part.png';Image.new('RGB',(64,64),'white').save(image)
    project=client.post('/api/project/create',json={'name':'Role comparison','task':'classification'}).json()
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    project['source_dataset_dir']=str(source)
    fingerprint=fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source))
    roles={'acceptable':'normal','scratch':'defect'}
    for job in ['job_incumbent','job_candidate']:
        directory=Path(project['models_dir'])/job
        _checkpoint(directory,source,fingerprint,0)
        checkpoint=directory/'best_model.pt';payload=torch.load(checkpoint,weights_only=True)
        payload.update(classes=list(roles),class_semantics=class_semantics_record(list(roles),roles))
        torch.save(payload,checkpoint)
        (directory/'model_meta.json').write_text(json.dumps({'task':'classification','classes':list(roles),'class_semantics':payload['class_semantics']}))
    body={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_incumbent','candidate_job_id':'job_candidate','full_test':True}
    first=client.post('/api/evaluation/model-comparisons',json=body)
    assert first.status_code==200,first.text
    assert first.json()['summary']['known_ok_images']==1
    assert first.json()['images'][0]['ground_truth_verdict']=='OK'
    current=image_truth.read_truth(project,str(image),task='classification',classes=list(roles),class_roles=roles)
    image_truth.declare_truth(project,str(image),task='classification',classes=list(roles),class_roles=roles,verdict='NG',defect_classes=['scratch'],reviewer='Reviewer',expected_revision=current['truth_revision'],expected_image_revision=current['image_revision'])
    second=client.post('/api/evaluation/model-comparisons',json=body)
    assert second.status_code==200,second.text
    assert second.json()['images'][0]['ground_truth_verdict']=='NG'
    assert second.json()['summary']['known_ng_images']==1
    assert second.json()['images'][0]['truth_sha256']


def test_comparison_rejects_disagreed_model_roles(tmp_path,monkeypatch):
    from backend.engine.class_semantics import class_semantics_record
    monkeypatch.chdir(tmp_path)
    client=_client(tmp_path);source=tmp_path/'source';folder=source/'test'/'acceptable';folder.mkdir(parents=True)
    Image.new('RGB',(64,64),'white').save(folder/'part.png')
    project=client.post('/api/project/create',json={'name':'Role conflict','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    fingerprint=fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source))
    for job,role in [('job_incumbent','normal'),('job_candidate','defect')]:
        directory=Path(project['models_dir'])/job;_checkpoint(directory,source,fingerprint,0)
        checkpoint=directory/'best_model.pt';payload=torch.load(checkpoint,weights_only=True)
        payload.update(classes=['acceptable','scratch'],class_semantics=class_semantics_record(['acceptable','scratch'],{'acceptable':role,'scratch':'defect'}))
        torch.save(payload,checkpoint)
        (directory/'model_meta.json').write_text(json.dumps({'task':'classification','classes':payload['classes'],'class_semantics':payload['class_semantics']}))
    response=client.post('/api/evaluation/model-comparisons',json={'source_dataset_path':str(source),'task':'classification','incumbent_job_id':'job_incumbent','candidate_job_id':'job_candidate','full_test':True})
    assert response.status_code==409,response.text
    assert 'role' in response.text.lower()


def _client(tmp_path: Path) -> TestClient:
    app = create_app(project_dir=str(tmp_path / "workspace_registry"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _checkpoint(job_dir: Path, source: Path, fingerprint: str, preferred_class: int) -> None:
    (job_dir / "dataset").mkdir(parents=True)
    model = create_classification_model("resnet18", 2, pretrained=False)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.fc.bias[preferred_class] = 10.0
    torch.save({
        "task": "classification", "backbone": "resnet18", "classes": ["OK", "NG"],
        "image_size": [64, 64], "model_state_dict": model.state_dict(),
    }, job_dir / "best_model.pt")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "classification", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(job_dir / "dataset"),
    }), encoding="utf-8")


def test_real_test_image_comparison_persists_hashes_disagreements_and_project_scope(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(tmp_path)
    source = tmp_path / "source"
    for category, color in (("OK", "white"), ("NG", "black")):
        folder = source / "test" / category
        folder.mkdir(parents=True)
        Image.new("RGB", (64, 64), color).save(folder / f"{category.lower()}.png")
    project = client.post("/api/project/create", json={"name": "Comparison", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    models = Path(project["models_dir"])
    _checkpoint(models / "job_incumbent", source, fingerprint, preferred_class=0)
    _checkpoint(models / "job_candidate", source, fingerprint, preferred_class=1)
    # Compare an older trained pair against the current fixed test set. The
    # report records both training provenance and the new evaluation version.
    (source / "updated_labels.txt").write_text("reviewed after training", encoding="utf-8")
    current_fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    params = {"source_dataset_path": str(source), "task": "classification"}

    catalog = client.get("/api/evaluation/model-comparisons/models", params=params)
    assert catalog.status_code == 200, catalog.text
    assert {model["job_id"] for model in catalog.json()["models"]} == {"job_incumbent", "job_candidate"}
    created = client.post("/api/evaluation/model-comparisons", json={
        **params, "incumbent_job_id": "job_incumbent", "candidate_job_id": "job_candidate", "max_images": 2,
    })
    assert created.status_code == 200, created.text
    report = created.json()
    assert report["status"] == "completed"
    assert report["summary"]["selected_images"] == 2
    assert report["summary"]["comparable_images"] == 2
    assert report["summary"]["disagreements"] == 2
    assert report["summary"]["known_ok_images"] == 1
    assert report["summary"]["known_ng_images"] == 1
    assert report["summary"]["new_missed_ng"] == 0
    assert report["summary"]["new_overkill_ok"] == 1
    assert report["model_sha256"]["incumbent"] == hashlib.sha256(
        (models / "job_incumbent" / "best_model.pt").read_bytes()).hexdigest()
    assert report["dataset_fingerprint"] == current_fingerprint
    assert report["dataset_fingerprint"] != fingerprint
    assert report["incumbent_training_dataset_fingerprint"] == fingerprint
    assert {row["incumbent"]["verdict"] for row in report["images"]} == {"OK"}
    assert {row["candidate"]["verdict"] for row in report["images"]} == {"NG"}
    for row in report["images"]:
        assert row["disagrees"] is True
        assert row["image_sha256"] == hashlib.sha256(Path(row["file_path"]).read_bytes()).hexdigest()
    report_file = Path(project["reports_dir"]) / "model_comparisons" / f"{report['comparison_id']}.json"
    assert report_file.is_file()
    assert client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}", params=params).json() == report
    assert client.get("/api/evaluation/model-comparisons", params=params).json()["total"] == 1

    # Reading another project never recovers this project's models or report.
    second = client.post("/api/project/create", json={"name": "Other", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    assert client.get("/api/evaluation/model-comparisons/models", params=params).json()["models"] == []
    assert client.get("/api/evaluation/model-comparisons", params=params).json()["comparisons"] == []
    assert client.get(f"/api/evaluation/model-comparisons/{report['comparison_id']}", params=params).status_code == 404
    assert second["id"] != project["id"]


def test_comparison_requires_distinct_completed_models_and_test_images(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    client = _client(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    project = client.post("/api/project/create", json={"name": "Comparison", "task": "classification"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    payload = {"source_dataset_path": str(source), "task": "classification",
               "incumbent_job_id": "job_a", "candidate_job_id": "job_b"}
    assert client.post("/api/evaluation/model-comparisons", json={**payload, "candidate_job_id": "job_a"}).status_code == 422
    assert client.post("/api/evaluation/model-comparisons", json=payload).status_code == 409
    assert client.get("/api/evaluation/model-comparisons/models", params={
        "source_dataset_path": str(tmp_path / "other"), "task": "classification"}).status_code == 409
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    for job_id in ("job_a", "job_b"):
        job_dir = Path(project["models_dir"]) / job_id
        job_dir.mkdir()
        (job_dir / "best_model.pt").write_bytes(b"checkpoint")
        (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
        (job_dir / "job_receipt.json").write_text(json.dumps({
            "status": "completed", "task": "classification", "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint,
        }), encoding="utf-8")
    missing_test = client.post("/api/evaluation/model-comparisons", json=payload)
    assert missing_test.status_code == 422
    assert "test" in missing_test.json()["detail"]


def test_known_ng_to_candidate_ok_is_reported_as_possible_new_miss():
    summary = _summary([{
        "ground_truth_verdict": "NG",
        "incumbent": {"verdict": "NG"},
        "candidate": {"verdict": "OK"},
    }])
    assert summary["new_missed_ng"] == 1
    assert summary["new_overkill_ok"] == 0
    assert summary["disagreements"] == 1


def test_full_test_async_comparison_can_reopen_complete_report(tmp_path, monkeypatch):
    import time
    monkeypatch.chdir(tmp_path)
    client=_client(tmp_path)
    source=tmp_path/'source'
    for category,color in [('OK','white'),('NG','black')]:
        folder=source/'test'/category;folder.mkdir(parents=True)
        for index in range(3):Image.new('RGB',(32,32),color).save(folder/f'{index}.png')
    project=client.post('/api/project/create',json={'name':'Full test','task':'classification'}).json()
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    fingerprint=fingerprint_dataset(source,studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,split_manifest=routes_dataset._split_manifest_file(source))
    for job_id in ['job_baseline','job_candidate']:_checkpoint(Path(project['models_dir'])/job_id,source,fingerprint,0)
    params={'source_dataset_path':str(source),'task':'classification'}
    started=client.post('/api/evaluation/model-comparisons/jobs',json={**params,'incumbent_job_id':'job_baseline','candidate_job_id':'job_candidate','full_test':True,'max_images':1})
    assert started.status_code==202,started.text
    identifier=started.json()['job_id']
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        row=client.get(f'/api/evaluation/model-comparisons/jobs/{identifier}',params=params).json()
        if row['status'] not in ('queued','running'):break
        time.sleep(.05)
    assert row['status']=='completed',row
    assert row['total_images']==6 and row['completed_images']==6
    assert client.get('/api/evaluation/model-comparisons/jobs',params=params).json()['jobs'][0]['job_id']==identifier
    report=client.get('/api/evaluation/model-comparisons/'+row['report_id'],params=params).json()
    assert report['selected_image_count']==report['total_test_images']==6
    assert report['full_test'] is True

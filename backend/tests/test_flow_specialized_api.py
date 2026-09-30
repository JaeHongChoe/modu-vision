import json
from pathlib import Path
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient
import torch
from backend.main import create_app
from backend.engine.ocr import SmallCTCOCR,write_ocr_manifest


def test_project_owned_ocr_catalog_run_save_reopen(tmp_path:Path):
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    created=client.post('/api/project/create',json={'name':'OCR flow','task':'classification'}).json()
    source=tmp_path/'source';source.mkdir();rows=[]
    for i,split in enumerate(('train','val','test')):
        path=source/f'{i}.png';Image.fromarray(np.full((32,64,3),20+i*30,np.uint8)).save(path);rows.append({'image':path.name,'text':'A','split':split})
    manifest=write_ocr_manifest(source,rows)
    project=client.get('/api/project/current').json()
    # Existing project update API persists the original source for downstream verification.
    response=client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    assert response.status_code==200,response.text
    project=client.get('/api/project/current').json()
    job='c'*32;directory=Path(project['models_dir'])/'ocr'/job;directory.mkdir(parents=True)
    model=SmallCTCOCR(1)
    for parameter in model.parameters():parameter.data.zero_()
    model.head.bias.data[1]=20
    payload={'task':'ocr','version':1,'alphabet':'A','image_size':[32,64],'model_state_dict':model.state_dict(),'dataset_provenance':manifest.provenance,'best_epoch':1}
    torch.save(payload,directory/'best_model.pt');(directory/'model_meta.json').write_text(json.dumps({'task':'ocr','dataset_provenance':manifest.provenance}))
    catalog=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
    assert catalog.status_code==200,catalog.text
    assert any(m['job_id']==job for m in catalog.json()['models'])
    template=client.get('/api/flowchart/templates/single-segmentation',params={'inspection_task':'ocr','job_id':job})
    assert template.status_code==200,template.text
    pipeline=template.json();next(n for n in pipeline['nodes'] if n['data']['node_type']=='inspection')['data']['params']={'expected_text':'A'}
    verified=client.post('/api/flowchart/models/verify',json={'source_dataset_path':str(source),'models':[{'job_id':job,'task':'ocr'}]})
    assert verified.status_code==200,verified.text
    saved=client.post('/api/flowchart/pipeline',params={'recipe_task':'ocr','source_dataset_path':str(source)},json=pipeline)
    assert saved.status_code==200,saved.text
    reopened=client.get('/api/flowchart/pipeline/active',params={'source_dataset_path':str(source)})
    assert reopened.json()==pipeline
    result=client.post('/api/flowchart/run',json={'image_path':str(source/'2.png'),'pipeline':pipeline})
    assert result.status_code==200,result.text
    assert result.json()['final_verdict']=='OK'
    assert result.json()['crops'][0]['recognized_text']=='A'
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowchartPipeline
    import pytest
    for status in ('running','stopped','failed','interrupted'):
        (directory/'job.json').write_text(json.dumps({'status':status,'job_id':job}))
        catalog=client.get('/api/flowchart/models/catalog',params={'source_dataset_path':str(source)})
        assert not any(m['job_id']==job for m in catalog.json()['models'])
        assert client.post('/api/flowchart/models/verify',json={'source_dataset_path':str(source),'models':[{'job_id':job,'task':'ocr'}]}).status_code==409
        assert client.post('/api/ocr/predict',json={'job_id':job,'image_path':str(source/'2.png')}).status_code==409
        assert client.post('/api/flowchart/run',json={'image_path':str(source/'2.png'),'pipeline':pipeline}).status_code==409
        with pytest.raises(ValueError,match='completed'):
            build_flow_package(pipeline=FlowchartPipeline.model_validate(pipeline),checkpoints={job:directory/'best_model.pt'},output_base_dir=tmp_path/'packages',package_name=status)


def test_historical_ocr_still_selects_runs_and_exports_after_label_correction(tmp_path):
    import shutil
    import pytest
    from backend.engine.training_provenance import bind_family_training, persist_model_binding
    from backend.engine.specialized_models import resolve_specialized_checkpoint
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import FlowchartPipeline
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    client.post("/api/project/create", json={"name": "OCR iteration", "task": "classification"})
    source = tmp_path / "source"
    source.mkdir()
    rows = []
    for index, split in enumerate(("train", "val", "test")):
        image = source / f"{index}.png"
        Image.fromarray(np.full((32, 64, 3), 20 + index * 30, np.uint8)).save(image)
        rows.append({"image": image.name, "text": "A", "split": split})
    manifest = write_ocr_manifest(source, rows)
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    project = client.get("/api/project/current").json()
    binding = bind_family_training(project, source, "ocr")
    job = "d" * 32
    directory = Path(project["models_dir"]) / "ocr" / job
    directory.mkdir(parents=True)
    payload = {"task": "ocr", "version": 1, "alphabet": "A", "image_size": [32, 64],
               "model_state_dict": SmallCTCOCR(1).state_dict(), "dataset_provenance": manifest.provenance}
    torch.save(payload, directory / "best_model.pt")
    (directory / "model_meta.json").write_text(json.dumps({"task": "ocr", "dataset_provenance": manifest.provenance}))
    persist_model_binding(directory, binding)
    rows[-1]["text"] = "AA"
    write_ocr_manifest(source, rows)
    verified = client.post("/api/flowchart/models/verify", json={"source_dataset_path": str(source),
                           "models": [{"job_id": job, "task": "ocr"}]})
    assert verified.status_code == 200, verified.text
    catalog = client.get("/api/flowchart/models/catalog", params={"source_dataset_path": str(source)}).json()
    assert any(model["job_id"] == job for model in catalog["models"])
    pipeline = client.get("/api/flowchart/templates/single-segmentation", params={"inspection_task": "ocr", "job_id": job}).json()
    next(node for node in pipeline["nodes"] if node["data"]["node_type"] == "inspection")["data"]["params"] = {"expected_text": "A"}
    run = client.post("/api/flowchart/run", json={"image_path": str(source / "2.png"), "pipeline": pipeline})
    assert run.status_code == 200, run.text
    checkpoint, _ = resolve_specialized_checkpoint(project["models_dir"], job, "ocr", source)
    package = build_flow_package(pipeline=FlowchartPipeline.model_validate(pipeline), checkpoints={job: checkpoint},
                                 output_base_dir=tmp_path / "packages", package_name="historical-candidate")
    assert package
    foreign = tmp_path / "foreign"
    shutil.copytree(source, foreign)
    with pytest.raises(ValueError, match="source|lineage"):
        resolve_specialized_checkpoint(project["models_dir"], job, "ocr", foreign)
    Path(binding["family_inputs"][0]["snapshot_path"] or next(row["snapshot_path"] for row in binding["family_inputs"] if row["snapshot_path"])).write_text("tampered")
    with pytest.raises(ValueError, match="backup|manifest|binding"):
        resolve_specialized_checkpoint(project["models_dir"], job, "ocr", source)

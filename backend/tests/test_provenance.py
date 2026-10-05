import json
import hashlib
from pathlib import Path

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


def test_trace_keeps_frozen_review_binding_after_approval_revocation(tmp_path):
    from backend.engine.training_provenance import bind_training_version
    from backend.api import routes_dataset
    app=create_app(str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'Review trace','task':'classification'})
    source=tmp_path/'source';source.mkdir();image=source/'part.png';Image.new('RGB',(16,12),'white').save(image)
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    settings=client.put('/api/team-data/settings',json={'expected_revision':1,'actor':'owner','changes':{'review_enabled':True,'approved_only_training':True}});assert settings.status_code==200,settings.text
    saved=client.post('/api/annotations/save',json={'image_id':'part','image_path':str(image),'image_width':16,'image_height':12,'actor':'labeler','annotations':[{'type':'tag','label':'OK','category_id':0,'is_normal':True}]}).json()['metadata']
    approved=client.post('/api/team-data/images/'+saved['image_uuid']+'/review',json={'actor':'reviewer','expected_revision':saved['revision'],'decision':'approve'});assert approved.status_code==200,approved.text
    old_annotation=client.get('/api/provenance',params={'image_path':str(image)}).json()['label']['sha256']
    previous_annotations=routes_dataset.STUDIO_ANNOTATIONS_DIR;previous_splits=routes_dataset.SPLIT_MANIFEST_DIR
    try:
        routes_dataset.STUDIO_ANNOTATIONS_DIR=Path(project['annotations_dir']);routes_dataset.SPLIT_MANIFEST_DIR=Path(project['dataset_dir'])/'splits'
        binding=bind_training_version(project,source)
    finally:routes_dataset.STUDIO_ANNOTATIONS_DIR=previous_annotations;routes_dataset.SPLIT_MANIFEST_DIR=previous_splits
    model=Path(project['models_dir'])/'job_trace';model.mkdir();checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'inert trace fixture; never loaded')
    (model/'model_meta.json').write_text(json.dumps({'task':'classification','source_dataset_path':str(source),'training_provenance':binding}))
    frozen=Path(binding['version_dir'])/'team-data.json';frozen_before=frozen.read_bytes();original=image.read_bytes()
    before=client.get('/api/provenance',params={'image_path':str(image)}).json()
    assert before['review_binding']['image_eligible'] is True
    assert before['review_binding']['eligible_count']==1 and before['models'][0]['data_state']=='current'
    revoked=client.patch('/api/dataset/metadata/'+saved['image_uuid'],json={'actor':'reviewer','expected_revision':before['label']['revision'],'changes':{'workflow_state':'needs_review'}});assert revoked.status_code==200,revoked.text
    after=client.get('/api/provenance',params={'image_path':str(image)}).json()
    assert after['label']['sha256']==old_annotation and after['review_binding']['image_eligible'] is False
    assert after['review_binding']['eligible_count']==0 and after['review_binding']['eligibility_sha256']!=before['review_binding']['eligibility_sha256']
    assert after['models'][0]['data_state']=='changed' and after['dataset_versions'][0]['review_binding']['eligibility_sha256']==binding['team_data']['eligibility_sha256']
    assert frozen.read_bytes()==frozen_before and image.read_bytes()==original


def test_trace_ignores_bound_manifest_outside_project_and_linked_model_folder(tmp_path):
    app=create_app(str(tmp_path/'registry'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    client.post('/api/project/create',json={'name':'Bound path trace','task':'classification'})
    source=tmp_path/'source';source.mkdir();image=source/'part.png';Image.new('RGB',(8,8)).save(image)
    project=client.put('/api/project/update',json={'source_dataset_dir':str(source)}).json()
    foreign=tmp_path/'foreign';foreign.mkdir();(foreign/'manifest.json').write_text(json.dumps({'source_dataset_dir':str(source)}))
    model=Path(project['models_dir'])/'job_own';model.mkdir();(model/'model_meta.json').write_text(json.dumps({'task':'classification','source_dataset_path':str(source),'training_provenance':{'version_dir':str(foreign)}}));(model/'best_model.pt').write_bytes(b'inert')
    (foreign/'model_meta.json').write_text(json.dumps({'task':'classification','source_dataset_path':str(source)}));(foreign/'best_model.pt').write_bytes(b'private unrelated bytes')
    (Path(project['models_dir'])/'linked').symlink_to(foreign,target_is_directory=True)
    result=client.get('/api/provenance',params={'image_path':str(image)}).json()
    assert [m['job_id'] for m in result['models']]==['job_own']
    assert result['models'][0]['data_state']=='unverified'
    assert result['models'][0]['bound_manifest_verified'] is False


def test_provenance_links_image_label_version_and_rejects_other_sources(tmp_path):
    app = create_app(str(tmp_path / "workspace"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "Trace", "task": "segmentation"}).json()
    source = tmp_path / "source"
    source.mkdir()
    image = source / "part.png"
    Image.fromarray(np.full((20, 25, 3), 110, np.uint8)).save(image)
    (source / "part.json").write_text(json.dumps({"imagePath": "part.png", "shapes": [{"label": "scratch", "points": [[1, 2], [7, 8]], "shape_type": "rectangle"}]}))
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    version = client.post("/api/dataset/versions", json={"name": "Reviewed baseline"}).json()
    response = client.get("/api/provenance", params={"image_path": str(image)})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["project"]["id"] == project["id"]
    assert result["image"]["image_uuid"] and len(result["image"]["content_hash"]) == 64
    assert result["label"]["sha256"]
    assert any(v["id"] == version["id"] and v["image_matches"] for v in result["dataset_versions"])
    assert result["models"] == [] and result["inspections"] == []
    assert client.get("/api/provenance", params={"image_path": str(tmp_path / "foreign.png")}).status_code == 409



def test_provenance_reports_native_yolo_label_hash_and_files(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'YOLO trace', 'task': 'detection'})
    source = tmp_path / 'source'
    (source / 'images' / 'train').mkdir(parents=True)
    (source / 'labels' / 'train').mkdir(parents=True)
    image = source / 'images' / 'train' / 'part.png'
    Image.fromarray(np.full((20, 25, 3), 110, np.uint8)).save(image)
    label = source / 'labels' / 'train' / 'part.txt'
    label.write_text('0 .5 .5 .4 .4')
    (source / 'classes.txt').write_text('scratch')
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    response = client.get('/api/provenance', params={'image_path': str(image)})
    assert response.status_code == 200, response.text
    record = response.json()
    assert len(record['label']['sha256']) == 64
    assert record['label']['sha256'] == record['image']['annotation_hash']
    assert str(label) in record['label']['paths']


def test_provenance_prefers_bound_source_and_keeps_training_identity(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Bound trace', 'task': 'classification'}).json()
    source = tmp_path / 'source'; source.mkdir()
    image = source / 'part.png'; Image.new('RGB', (12, 16)).save(image)
    client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    model_root = Path(project['models_dir'])
    version_root = Path(project['project_dir']) / 'versions'
    own_version = version_root / 'own'; own_version.mkdir(parents=True)
    foreign_version = version_root / 'foreign'; foreign_version.mkdir()
    (own_version / 'manifest.json').write_text(json.dumps({'source_dataset_dir': str(source)}))
    (foreign_version / 'manifest.json').write_text(json.dumps({'source_dataset_dir': str(tmp_path / 'other-source')}))
    for job, version in [('own-model', own_version), ('other-source-model', foreign_version)]:
        folder = model_root / job; folder.mkdir()
        (folder / 'best_model.pt').write_bytes(b'checkpoint evidence')
        (folder / 'model_meta.json').write_text(json.dumps({
            'task': 'classification', 'source_dataset_path': str(source),
            'dataset_path': str(tmp_path / 'prepared-view'),
            'training_provenance': {'dataset_version_id': version.name, 'version_dir': str(version),
                                    'labelset_id': 'reviewed', 'split_sha256': 'bound-split'},
        }))
    response = client.get('/api/provenance', params={'image_path': str(image)})
    assert response.status_code == 200, response.text
    models = response.json()['models']
    assert [row['job_id'] for row in models] == ['own-model']
    assert models[0]['source_dataset_path'] == str(source)
    assert models[0]['source_verified'] is True
    assert models[0]['training_provenance']['dataset_version_id'] == 'own'
    assert models[0]['training_provenance']['labelset_id'] == 'reviewed'
    assert models[0]['training_provenance']['split_sha256'] == 'bound-split'

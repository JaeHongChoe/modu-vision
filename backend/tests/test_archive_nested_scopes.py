"""Archive relocation preserves nested annotations, immutable versions and reviews."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from backend.main import create_app
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_metadata import metadata_for_path, update_metadata
from backend.engine.project_labelsets import labelset_root
from backend.api.routes_dataset_versions import _manifest_digest


def test_unfinished_flow_drafts_survive_two_restores_for_each_labelset(tmp_path):
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.post("/api/project/create", json={"name": "Draft archive", "task": "segmentation"}).json()
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (16, 16), "white").save(source / "image.png")
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    second = client.post("/api/project/labelsets", json={"name": "Alternative"}).json()["id"]
    records = {}
    for labelset in ("default", second):
        assert client.put(f"/api/project/labelsets/{labelset}/activate").status_code == 200
        pipeline = client.get("/api/flowchart/templates/single-segmentation").json()
        pipeline["name"] = f"Unfinished {labelset}"
        context = {"project_id": project["id"], "source_dataset_path": str(source), "labelset_id": labelset}
        saved = client.put("/api/flowchart/draft", json={"pipeline": pipeline, "context": context})
        assert saved.status_code == 200, saved.text
        records[labelset] = saved.json()
    for iteration in (1, 2):
        backup = client.post("/api/project/backup", json={"destination_dir": str(tmp_path / "backups")})
        assert backup.status_code == 200, backup.text
        restored = client.post("/api/project/restore", json={"archive_path": backup.json()["archive_path"],
                              "target_dir": str(tmp_path / f"restored_{iteration}")})
        assert restored.status_code == 200, restored.text
        for labelset in records:
            assert client.put(f"/api/project/labelsets/{labelset}/activate").status_code == 200
            draft = client.get("/api/flowchart/draft")
            assert draft.status_code == 200, draft.text
            assert draft.json()["draft_sha256"] == records[labelset]["draft_sha256"]
            assert draft.json()["pipeline"] == records[labelset]["pipeline"]
            assert draft.json()["context"]["project_id"] == restored.json()["id"]
            assert draft.json()["context"]["source_dataset_path"] == restored.json()["source_dataset_dir"]
            assert draft.json()["active_version_id"] is None


def test_nested_labelsets_versions_and_approved_identity_survive_two_archive_restores(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name':'Nested archive','task':'detection'}).json()
    project_dir = Path(project['project_dir'])
    source = tmp_path / 'source'
    image = source / 'train' / 'product' / 'part.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (24,24), 'white').save(image)
    source_labels = image.with_suffix('.json')
    customer_bytes = json.dumps({'imagePath':str(image),'shapes':[]}).encode()
    source_labels.write_bytes(customer_bytes)
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    second = client.post('/api/project/labelsets',json={'name':'Nested B'}).json()['id']
    approved = {}; versions = {}
    for set_id in ('default',second):
        assert client.put(f'/api/project/labelsets/{set_id}/activate').status_code==200
        saved = client.post('/api/annotations/save',json={'image_id':'part','image_path':str(image),
            'image_width':24,'image_height':24,'annotations':[{'type':'bbox','label':set_id,'bbox':[1,1,12,12]}]})
        assert saved.status_code==200,saved.text
        root = labelset_root(project_dir,set_id)
        row = metadata_for_path(project_dir,source,image,root)
        approved[set_id] = update_metadata(project_dir,source,row['image_uuid'],row['revision'],'Kim',
            {'workflow_state':'approved','lot':'L1'},root)
        split = project_dir/'dataset'/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json'
        split.parent.mkdir(parents=True,exist_ok=True)
        split.write_text(json.dumps({'folder_path':str(source),'assignments':{'train/product/part.png':'train'}}))
        version = client.post('/api/dataset/versions',json={'name':f'Nested {set_id}'})
        assert version.status_code==200,version.text
        versions[set_id]=version.json()['id']
    for round_no in (1,2):
        archive=client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')})
        assert archive.status_code==200,archive.text
        target=tmp_path/f'restored_{round_no}'
        restored=client.post('/api/project/restore',json={'archive_path':archive.json()['archive_path'],'target_dir':str(target)})
        assert restored.status_code==200,restored.text
        new_source=Path(restored.json()['source_dataset_dir']);new_image=new_source/'train/product/part.png'
        assert new_image.with_suffix('.json').read_bytes()==customer_bytes
        for set_id in ('default',second):
            assert client.put(f'/api/project/labelsets/{set_id}/activate').status_code==200
            overlay=dataset_annotation_dir(new_image.parent,labelset_root(target,set_id),use_scope=False)
            assert (overlay/'part.json').is_file(), 'Nested scope must follow relocated image parent'
            row=metadata_for_path(target,new_source,new_image,labelset_root(target,set_id))
            for key in ('image_uuid','revision','audit','review_history','workflow_state','reviewer'):
                assert row[key]==approved[set_id][key],key
            assert row['file_path']==str(new_image)
            viewed=client.get('/api/annotations/part',params={'file_path':str(new_image)})
            assert viewed.status_code==200,viewed.text
            assert viewed.json()['annotations'][0]['label']==set_id
            verified=client.get(f'/api/dataset/versions/{versions[set_id]}/verify')
            assert verified.status_code==200,verified.text
            assert verified.json()['status']=='verified',verified.text
            assert verified.json()['changed_files']==[]
            manifest_path=target/'versions'/versions[set_id]/'manifest.json'
            manifest=json.loads(manifest_path.read_text())
            assert manifest['content_digest']==_manifest_digest(manifest)
            for record in manifest['files']:
                if record['snapshot_path']:
                    snapshot=manifest_path.parent/record['snapshot_path']
                    assert snapshot.is_file()
                    assert hashlib.sha256(snapshot.read_bytes()).hexdigest()==record['sha256']
                    assert snapshot.stat().st_size==record['size_bytes']
                if record['origin']=='studio_scoped':
                    assert record['relative_path'].split('/')[0]==overlay.name
                    assert record['source_path']==str(overlay/Path(record['relative_path']).relative_to(overlay.name))
            assert (manifest_path.parent/'labels/source/train/product/part.json').read_bytes()==customer_bytes
    assert source_labels.read_bytes()==customer_bytes


def test_training_binding_restores_verified_manifest_alias_without_changing_checkpoint(tmp_path):
    from backend.engine.training_provenance import validate_training_binding
    from backend.api.routes_dataset_versions import _manifest_digest
    app=create_app(project_dir=str(tmp_path/'projects'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Bound restore'}).json()
    original=Path(project['project_dir']);source=tmp_path/'source';source.mkdir()
    Image.new('RGB',(8,8),'white').save(source/'part.png')
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    version=client.post('/api/dataset/versions',json={'name':'Training input'}).json()['id']
    version_dir=original/'versions'/version;manifest=json.loads((version_dir/'manifest.json').read_text())
    binding={'dataset_version_id':version,'version_dir':str(version_dir),'manifest_sha256':manifest['content_digest'],
        'dataset_fingerprint':manifest['dataset_fingerprint'],'labelset_id':'default','split_binding':'versioned_dataset_layout',
        'split_sha256':hashlib.sha256(json.dumps([{k:r[k] for k in ('origin','relative_path','sha256')} for r in manifest['files']],sort_keys=True,separators=(',',':')).encode()).hexdigest()}
    model=original/'models'/'job_bound_restore';model.mkdir(parents=True)
    checkpoint=model/'best_model.pt';checkpoint.write_bytes(b'checkpoint original provenance and weights are immutable')
    (model/'model_meta.json').write_text(json.dumps({'task':'classification','training_provenance':binding}))
    archived=client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')}).json()
    target=tmp_path/'restored_binding'
    restored=client.post('/api/project/restore',json={'archive_path':archived['archive_path'],'target_dir':str(target)})
    assert restored.status_code==200,restored.text
    metadata=json.loads((target/'models/job_bound_restore/model_meta.json').read_text())
    validate_training_binding(metadata['training_provenance'])
    assert metadata['training_provenance']['archive_restored_from_manifest_sha256']==binding['manifest_sha256']
    assert (target/'models/job_bound_restore/best_model.pt').read_bytes()==checkpoint.read_bytes()


@pytest.mark.parametrize('family',['ocr','rotated_detection','defect_gan','enhancement'])
def test_family_training_alias_and_frozen_manifest_survive_restore(tmp_path,family):
    from backend.engine.training_provenance import bind_family_training,validate_training_binding
    app=create_app(project_dir=str(tmp_path/'projects'));client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':f'Family {family}'}).json()
    original=Path(project['project_dir']);source=tmp_path/'source';source.mkdir()
    for i in range(3):Image.new('RGB',(32,32),(30+i*70,30,30)).save(source/f'{i}.png')
    client.put('/api/project/update',json={'source_dataset_dir':str(source)})
    project=client.get('/api/project/current').json();dataset=source
    if family=='ocr':
        from backend.engine.ocr import write_ocr_manifest
        write_ocr_manifest(source,[{'image':f'{i}.png','text':'A','split':split} for i,split in enumerate(('train','val','test'))])
    elif family=='rotated_detection':
        from backend.engine.rotated_detection import write_rotated_manifest
        write_rotated_manifest(source,[{'image':f'{i}.png','label':'a','box':{'cx':16,'cy':16,'width':12,'height':8,'angle_deg':0},'split':split} for i,split in enumerate(('train','val','test'))])
    elif family=='defect_gan':
        from backend.engine.defect_gan import write_defect_gan_manifest
        write_defect_gan_manifest(source,[{'image':f'{i}.png','bbox':[0,0,32,32],'split':split} for i,split in enumerate(('train','train','val'))])
    else:
        from backend.engine.enhancement import prepare_enhancement
        dataset=original/'dataset/enhancement'/'prepared';prepare_enhancement(source,dataset)
    binding=bind_family_training(project,dataset,family)
    frozen=next(Path(row['snapshot_path']) for row in binding['family_inputs'] if row['snapshot_path'])
    frozen_bytes=frozen.read_bytes()
    output=original/'models'/family/('f'*32);output.mkdir(parents=True)
    (output/'model_meta.json').write_text(json.dumps({'task':family,'training_provenance':binding}))
    for round_no in (1,2):
        archived=client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')}).json()
        target=tmp_path/f'family_restore_{round_no}'
        response=client.post('/api/project/restore',json={'archive_path':archived['archive_path'],'target_dir':str(target)})
        assert response.status_code==200,response.text
        meta=json.loads((target/output.relative_to(original)/'model_meta.json').read_text())
        validate_training_binding(meta['training_provenance'])
        backup=next(Path(row['snapshot_path']) for row in meta['training_provenance']['family_inputs'] if row['snapshot_path'])
        assert backup.read_bytes()==frozen_bytes


def test_archive_rebases_integrity_bound_evaluation_and_jobs_without_mutating_packages(tmp_path):
    from backend.engine.evaluation_history import EvaluationHistory, ComparisonJobs
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flow_package_runtime import verify_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    from backend.engine.project_archive import _dataset_fingerprint
    import sqlite3
    app=create_app(project_dir=str(tmp_path/'projects'))
    client=TestClient(app,headers={'X-Vision-Token':app.state.api_token})
    project=client.post('/api/project/create',json={'name':'Evidence relocation','task':'detection'}).json()
    original=Path(project['project_dir']);source=tmp_path/'source';source.mkdir()
    Image.new('RGB',(8,8),'white').save(source/'part.png')
    assert client.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    fingerprint=_dataset_fingerprint(original,source)
    directory=original/'models'/'job_archive_evidence';directory.mkdir(parents=True)
    checkpoint=directory/'best_model.pt';checkpoint.write_bytes(b'owned fixed checkpoint')
    (directory/'model_meta.json').write_text(json.dumps({'task':'detection','dataset_path':str(source),'dataset_fingerprint':fingerprint}))
    package=build_flow_package(pipeline=get_single_detection_flowchart(job_id=directory.name),
        checkpoints={directory.name:checkpoint},output_base_dir=original/'exports',package_name='frozen')
    package_dir=Path(package['package_path'])
    frozen={p.relative_to(package_dir):p.read_bytes() for p in package_dir.rglob('*') if p.is_file()}
    policy=original/'runtime_service'/'releases'/'r1'/'release_policy.json';policy.parent.mkdir(parents=True)
    policy.write_text(json.dumps({'manifest_sha256':hashlib.sha256((package_dir/'manifest.json').read_bytes()).hexdigest(),
        'source_dataset_path':str(source),'dataset_fingerprint':fingerprint}))
    policy_bytes=policy.read_bytes()
    evaluation=EvaluationHistory(original/'reports'/'evaluations').append(
        {'job_id':directory.name,'task':'detection','test_predictions':[{'file_path':str(source/'part.png'),'ground_truth':'OK','predicted_class':'OK'}]},
        {'source_dataset_path':str(source),'dataset_fingerprint':fingerprint,'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest()})
    with sqlite3.connect(original/'model_deployments.sqlite3') as database:
        database.execute('CREATE TABLE revisions(comparison_id TEXT,comparison_sha256 TEXT)')
        database.execute('INSERT INTO revisions VALUES(?,?)',(evaluation['evaluation_id'],hashlib.sha256((original/'reports/evaluations'/f'{evaluation["evaluation_id"]}.json').read_bytes()).hexdigest()))
    jobs=ComparisonJobs(original/'reports'/'comparison_jobs.sqlite3')
    complete=jobs.create({'source_dataset_path':str(source),'task':'detection','candidate_job_id':directory.name})
    jobs.finish(complete['job_id'],'completed','comparison_saved')
    running=jobs.create({'source_dataset_path':str(source),'task':'detection'});jobs.start(running['job_id'],2)
    from backend.engine.runtime_deployment import DeploymentLedger
    ledger=DeploymentLedger(original/'runtime_service')
    release={'package_path':str(package_dir),'release_policy':str(policy),'manifest_sha256':hashlib.sha256((package_dir/'manifest.json').read_bytes()).hexdigest(),'device':'cpu'}
    deployment=ledger.apply(release,lambda r:{**r,'status':'ready'},reviewer='Kim')
    config=original/'runtime_service'/'service.json'
    config.write_text(json.dumps({'port':54321,'token':'original-service-token','pid':12345,'native_label':'com.modu.original'}))
    config_bytes=config.read_bytes()
    runtime_identity=original/'runtime_service'/'state'/'runtime.json';runtime_identity.parent.mkdir(parents=True)
    runtime_identity.write_text(json.dumps(release))
    for round_no in (1,2):
        archived=client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')})
        assert archived.status_code==200,archived.text
        target=tmp_path/f'relocated_evidence_{round_no}'
        restored=client.post('/api/project/restore',json={'archive_path':archived.json()['archive_path'],'target_dir':str(target)})
        assert restored.status_code==200,restored.text
        relocated_source=Path(restored.json()['source_dataset_dir'])
        restored_evaluation=EvaluationHistory(target/'reports'/'evaluations').get(evaluation['evaluation_id'])
        assert restored_evaluation['original_evidence_sha256']==evaluation['evidence_sha256']
        assert restored_evaluation['original_binding']==evaluation['binding']
        from backend.engine.evaluation_history import canonical
        assert hashlib.sha256(canonical(restored_evaluation['original_evidence'])).hexdigest()==evaluation['evidence_sha256']
        assert len(restored_evaluation['archive_restorations'])==round_no
        assert restored_evaluation['binding']['source_dataset_path']==str(relocated_source)
        assert restored_evaluation['binding']['dataset_fingerprint']==_dataset_fingerprint(target,relocated_source)
        assert restored_evaluation['result']['test_predictions'][0]['file_path']==str(relocated_source/'part.png')
        with sqlite3.connect(target/'model_deployments.sqlite3') as database:
            approval_hash=database.execute('SELECT comparison_sha256 FROM revisions').fetchone()[0]
        assert approval_hash==hashlib.sha256((target/'reports/evaluations'/f'{evaluation["evaluation_id"]}.json').read_bytes()).hexdigest()
        restored_jobs=ComparisonJobs(target/'reports'/'comparison_jobs.sqlite3')
        assert restored_jobs.get(complete['job_id'])['payload']['source_dataset_path']==str(relocated_source)
        assert restored_jobs.get(complete['job_id'])['status']=='completed'
        assert restored_jobs.get(running['job_id'])['status']=='interrupted'
        assert restored_jobs.get(running['job_id'])['owner_pid'] is None
        restored_package=target/package_dir.relative_to(original)
        verify_flow_package(restored_package)
        assert {p.relative_to(restored_package):p.read_bytes() for p in restored_package.rglob('*') if p.is_file()}==frozen
        assert (target/policy.relative_to(original)).read_bytes()==policy_bytes
        restored_deployment=DeploymentLedger(target/'runtime_service').active()
        assert restored_deployment['deployment_id']==deployment['deployment_id']
        assert restored_deployment['release']['package_path']==str(restored_package)
        assert restored_deployment['ack']['manifest_sha256']==release['manifest_sha256']
        restored_config=json.loads((target/'runtime_service/service.json').read_text())
        assert restored_config['pid'] is None and 'native_label' not in restored_config
        assert restored_config['token']!='original-service-token' and restored_config['port']!=54321
        assert json.loads((target/'runtime_service/state/runtime.json').read_text())['package_path']==str(restored_package)
    assert config.read_bytes()==config_bytes


def test_frozen_inspection_execution_and_hardware_evidence_survive_two_restores(tmp_path):
    import sqlite3
    from backend.tests.test_project_archive import _project_with_model_flow_and_run
    from backend.api.routes_inspections import _canonical_json
    client, project, _, image, _, _, run_id = _project_with_model_flow_and_run(tmp_path)
    config = {'execution_target':'selected_compute','device':'cuda','compute_profile_id':'example-compute',
              'project_id':project['id'],'profile':{'id':'example-compute','name':'Example GPU','host':'192.0.2.10',
                                                  'gpu_selector':'2','workspace_root':'/srv/vision'}}
    encoded = _canonical_json(config)
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    hardware = {'execution_target':'selected_compute','execution_device':'cuda',
                'compute_profile_id':'example-compute','compute_gpu_selector':'2','device_name':'GPU fixture',
                'model_sha256':{'job_123_archive':'a'*64}}
    with sqlite3.connect(Path(project['project_dir'])/'inspection_history.sqlite3') as conn:
        conn.execute("UPDATE runs SET execution_config_json=?, execution_config_sha256=?,status='completed' WHERE run_id=?",
                     (encoded,digest,run_id))
        conn.execute("UPDATE rows SET state='NG',result_json=? WHERE run_id=?",
                     (json.dumps({**hardware,'image_path':str(image),'image_id':'part'}),run_id))
    previous_id = run_id
    for round_no in (1,2):
        backed = client.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')})
        assert backed.status_code == 200, backed.text
        target = tmp_path/f'execution_restore_{round_no}'
        restored = client.post('/api/project/restore',json={'archive_path':backed.json()['archive_path'],
                                                           'target_dir':str(target)})
        assert restored.status_code == 200, restored.text
        assert restored.json()['id'] == project['id']
        with sqlite3.connect(target/'inspection_history.sqlite3') as conn:
            row = conn.execute('SELECT run_id,execution_config_json,execution_config_sha256 FROM runs').fetchone()
            assert row[0] != previous_id
            assert row[1:] == (encoded,digest)
            recorded = json.loads(conn.execute('SELECT result_json FROM rows').fetchone()[0])
        previous_id = row[0]
        assert {key:recorded[key] for key in hardware} == hardware
        assert recorded['image_path'] == str(Path(restored.json()['source_dataset_dir'])/'test'/'part.png')
        saved = client.get(f'/api/inspections/runs/{row[0]}')
        assert saved.status_code == 200, saved.text
        assert saved.json()['execution_config'] == config


@pytest.mark.parametrize('alternate_labelset', [False, True])
def test_brush_mask_scope_paths_survive_staged_restore_and_repeated_archive(tmp_path, alternate_labelset):
    """Real saved sparse masks remain readable; embedded brush labels alone are insufficient."""
    import base64
    import io
    import numpy as np
    from backend.engine.project_archive import restore_archive
    from backend.engine.annotation_storage import set_request_annotation_root, reset_request_annotation_root
    from backend.engine.dataset_summary import dataset_summary
    from backend.engine.grouped_dataset_views import ManifestSegmentationDataset
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Sparse mask archive', 'task': 'segmentation'}).json()
    original_project = Path(project['project_dir'])
    source = tmp_path / 'original-source'
    images = [source / 'part.png', source / 'nested' / 'part.png']
    for image in images:
        image.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (32, 32), 'white').save(image)
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    set_id = 'default'
    if alternate_labelset:
        set_id = client.post('/api/project/labelsets', json={'name': 'Sparse alternate'}).json()['id']
        assert client.put('/api/project/labelsets/' + set_id + '/activate').status_code == 200
    annotation_root = labelset_root(original_project, set_id)
    expected = np.zeros((32, 32), dtype=np.uint8)
    expected[2:14, 2:14] = 3; expected[5:9, 5:9] = 0
    expected[18:29, 19:30] = 7
    brushes = []
    for identifier, name in ((3, 'Scratch'), (7, 'Dent')):
        rgba = np.zeros((32, 32, 4), dtype=np.uint8); rgba[expected == identifier] = [255, 100, 0, 255]
        stream = io.BytesIO(); Image.fromarray(rgba).save(stream, format='PNG')
        brushes.append({'id': str(identifier), 'type': 'brush_mask', 'label': name, 'category_id': identifier,
            'mask_rle': 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()})
    palette = [{'id': 0, 'name': 'background', 'color': '#000000'},
        {'id': 3, 'name': 'Scratch', 'color': '#ff0000'}, {'id': 7, 'name': 'Dent', 'color': '#00ff00'}]
    original_rows = {}; source_bytes = {}; mask_bytes = {}; annotation_values = {}; original_json = {}
    for image in images:
        saved = client.post('/api/annotations/save', json={'image_id': image.stem, 'image_path': str(image),
            'image_width': 32, 'image_height': 32, 'annotations': brushes, 'mask_classes': palette})
        assert saved.status_code == 200 and saved.json()['mask_generated'], saved.text
        relative = image.relative_to(source)
        overlay = dataset_annotation_dir(image.parent, annotation_root, use_scope=False)
        annotation = overlay / 'part.json'; value = json.loads(annotation.read_text())
        original_rows[relative] = metadata_for_path(original_project, source, image, annotation_root)
        source_bytes[relative] = image.read_bytes(); mask_bytes[relative] = Path(value['mask_file']).read_bytes()
        annotation_values[relative] = value; original_json[relative] = annotation.read_bytes()
        assert np.array_equal(np.asarray(Image.open(value['mask_file'])), expected)
    # Customer JSON and an immutable package must not be swept into path repair.
    customer = source / 'customer.json'; customer.write_text(json.dumps({'mask_file': annotation_values[Path('part.png')]['mask_file']}))
    immutable = original_project / 'models' / 'immutable-package'; immutable.mkdir()
    protected = json.dumps({'mask_file': annotation_values[Path('part.png')]['mask_file']}).encode()
    (immutable / 'record.json').write_bytes(protected)
    (immutable / 'manifest.json').write_text(json.dumps({'pipeline_id': 'fixture-package',
        'files': [{'path': 'record.json', 'sha256': hashlib.sha256(protected).hexdigest()}]}))
    customer_bytes = customer.read_bytes()
    version = client.post('/api/dataset/versions', json={'name': 'Frozen sparse masks'})
    assert version.status_code == 200, version.text
    version_id = version.json()['id']
    original_version = original_project / 'versions' / version_id
    version_bytes = {path.relative_to(original_version): path.read_bytes() for path in original_version.rglob('*') if path.is_file()}
    current_source = source
    for round_no in (1, 2):
        archived = client.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backups')})
        assert archived.status_code == 200, archived.text
        target = tmp_path / f'restored-masks-{round_no}'
        target_annotations = labelset_root(target, set_id)
        def before_publish(staging, destination):
            assert not destination.exists()
            rebound_source = Path(json.loads((staging / 'project.json').read_text())['source_dataset_dir'])
            for relative in source_bytes:
                overlay = dataset_annotation_dir((rebound_source / relative).parent, target_annotations, use_scope=False)
                stored = staging / (overlay / 'part.json').relative_to(target)
                declared = Path(json.loads(stored.read_text())['mask_file'])
                assert declared == overlay / 'masks' / 'part.png'
                assert (staging / declared.relative_to(target)).read_bytes() == mask_bytes[relative]
        restore_archive(Path(archived.json()['archive_path']), target, before_publish=before_publish)
        opened = client.post('/api/project/open', json={'project_dir': str(target)})
        assert opened.status_code == 200, opened.text
        assert opened.json()['id'] == project['id'] and opened.json()['active_labelset_id'] == set_id
        current_source = Path(opened.json()['source_dataset_dir'])
        token = set_request_annotation_root(target_annotations)
        try:
            summary = dataset_summary(current_source, 'segmentation')
            assert summary['labeling']['labeled']['count'] == 2 and summary['labeling']['unlabeled']['count'] == 0
            dataset = ManifestSegmentationDataset(current_source, [current_source / relative for relative in source_bytes])
            for index, relative in enumerate(source_bytes):
                image = current_source / relative
                value = client.get('/api/annotations/part', params={'file_path': str(image)}).json()
                assert value['annotations'] == annotation_values[relative]['annotations']
                assert value['mask_classes'] == annotation_values[relative]['mask_classes']
                assert Path(value['mask_file']).read_bytes() == mask_bytes[relative]
                assert np.array_equal(dataset[index][1].numpy(), expected)
                row = metadata_for_path(target, current_source, image, target_annotations)
                for key in ('image_uuid', 'revision', 'content_version', 'workflow_state'):
                    assert row[key] == original_rows[relative][key]
                overlay = dataset_annotation_dir(image.parent, target_annotations, use_scope=False)
                assert row['annotation_hash'] == hashlib.sha256((overlay / 'part.json').read_bytes()).hexdigest()
                assert row['mask_hash'] == hashlib.sha256(mask_bytes[relative]).hexdigest()
                assert image.read_bytes() == source_bytes[relative]
        finally:reset_request_annotation_root(token)
        assert (current_source / 'customer.json').read_bytes() == customer_bytes
        assert (target / 'models/immutable-package/record.json').read_bytes() == protected
        verified = client.get('/api/dataset/versions/' + version_id + '/verify')
        assert verified.status_code == 200, verified.text
        assert verified.json()['status'] == 'verified' and verified.json()['editable_changed_files'] == [], verified.text
    for relative, raw in source_bytes.items():
        assert (source / relative).read_bytes() == raw
        original_overlay = dataset_annotation_dir((source / relative).parent, annotation_root, use_scope=False)
        assert (original_overlay / 'part.json').read_bytes() == original_json[relative]
    assert {path.relative_to(original_version): path.read_bytes() for path in original_version.rglob('*') if path.is_file()} == version_bytes


def test_archive_mask_rebind_does_not_retarget_foreign_scopes_or_arbitrary_json(tmp_path):
    from backend.engine.project_archive import create_archive, restore_archive
    app = create_app(project_dir=str(tmp_path / 'projects'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    project = client.post('/api/project/create', json={'name': 'Mask pointer bounds', 'task': 'segmentation'}).json()
    root = Path(project['project_dir']); source = tmp_path / 'source'
    for image in (source / 'one.png', source / 'nested' / 'two.png'):
        image.parent.mkdir(parents=True, exist_ok=True); Image.new('RGB', (8, 8)).save(image)
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project = client.get('/api/project/current').json()
    overlay = dataset_annotation_dir(source, root / 'annotations', use_scope=False); overlay.mkdir(parents=True)
    nested = dataset_annotation_dir(source / 'nested', root / 'annotations', use_scope=False)
    own = str(overlay / 'masks/one.png')
    pointers = {
        'relative': str((overlay / 'masks/one.png').relative_to(root)),
        'foreign': str(tmp_path / 'external' / overlay.name / 'masks/one.png'),
        'other_labelset': str(root / 'labelsets/ls_ffffffffffff/annotations/by_dataset' / overlay.name / 'masks/one.png'),
        'other_source_scope': str(nested / 'masks/two.png'),
    }
    for name, pointer in pointers.items():
        (overlay / (name + '.json')).write_text(json.dumps({'mask_file': pointer, 'note': overlay.name,
            'nested_metadata': {'mask_file': own}}))
    report = root / 'reports' / 'unrelated.json'; report.write_text(json.dumps({'mask_file': own}))
    archive = create_archive(project, tmp_path / 'backups')
    target = tmp_path / 'restored'; restore_archive(Path(archive['archive_path']), target)
    relocated = json.loads((target / 'project.json').read_text())
    new_overlay = dataset_annotation_dir(Path(relocated['source_dataset_dir']), target / 'annotations', use_scope=False)
    for name, pointer in pointers.items():
        value = json.loads((new_overlay / (name + '.json')).read_text())
        expected = str(target) + pointer[len(str(root)):] if pointer.startswith(str(root) + '/') else pointer
        assert value['mask_file'] == expected
        assert value['note'] == overlay.name
        assert value['nested_metadata']['mask_file'] == str(target / Path(own).relative_to(root))
    assert json.loads((target / 'reports/unrelated.json').read_text())['mask_file'] == str(target / Path(own).relative_to(root))

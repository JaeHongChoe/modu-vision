"""Recoverable retention and backup integrate through actual project routes."""
import hashlib
import json
from pathlib import Path
import threading

import pytest
from PIL import Image


def project(tmp_path):
    from backend.tests.test_project_archive import _client
    api = _client(tmp_path / 'projects')
    value = api.post('/api/project/create', json={'name':'Retention'}).json()
    source = tmp_path / 'source'; source.mkdir(); Image.new('RGB',(16,16),'white').save(source/'part.png')
    assert api.put('/api/project/update',json={'source_dataset_dir':str(source)}).status_code==200
    value = api.get('/api/project/current').json()
    root = Path(value['project_dir']); artifact=root/'models'/'job_old'; artifact.mkdir(); (artifact/'best_model.pt').write_bytes(b'verified fixture model')
    return api,value,root,source,artifact


def test_explicit_retention_pin_survives_restart_and_refuses_trash(tmp_path):
    from backend.engine.artifact_retention import ArtifactRetention
    _,value,root,_,artifact=project(tmp_path)
    store=ArtifactRetention(root);store.pin('legal:review',[artifact],reason='legal_hold')
    restarted=ArtifactRetention(root)
    with pytest.raises(ValueError,match='pinned'):restarted.move_to_trash([artifact],project=value,retention_days=0)
    assert (artifact/'best_model.pt').is_file()
    assert any(pin['reason']=='legal_hold' for pin in restarted.status(value)['pins'])


def test_gc_is_recoverable_and_reports_physical_quota_and_expiry(tmp_path):
    from backend.engine.artifact_retention import ArtifactRetention
    _,value,root,_,artifact=project(tmp_path);store=ArtifactRetention(root)
    store.configure(retention_days=0,trash_days=7,quota_bytes=1)
    result=store.move_to_trash([artifact],project=value)
    assert len(result['trashed'])==1 and not artifact.exists()
    row=store.status(value)['trash'][0]
    assert row['state']=='trashed' and row['purge_eligible'] is False
    assert store.status(value)['over_quota'] is True
    restored=ArtifactRetention(root).restore_trash(row['trash_id'])
    assert restored['state']=='restored' and (artifact/'best_model.pt').read_bytes()==b'verified fixture model'
    with pytest.raises(ValueError,match='source|managed|outside'):store.move_to_trash([root/'project.json'],project=value)


def test_pending_and_active_release_are_durably_pinned_before_external_apply(tmp_path):
    from backend.engine.artifact_retention import ArtifactRetention
    from backend.engine.runtime_deployment import DeploymentLedger
    _,value,root,_,_=project(tmp_path);service=root/'runtime_service';package=service/'releases'/'a';package.mkdir(parents=True);(package/'manifest.json').write_text('{}')
    policy=service/'releases'/'a.policy.json';policy.write_text('{}')
    release={'package_path':str(package),'release_policy':str(policy),'manifest_sha256':'a'*64,'device':'cpu'}
    ledger=DeploymentLedger(service)
    def apply_runtime(selected):
        rows=ArtifactRetention(root).status(value)['pins']
        assert any(row['reason']=='pending_release' and row['relative_path']=='runtime_service/releases/a' for row in rows)
        return {'status':'ready','manifest_sha256':selected['manifest_sha256'],'device':selected['device']}
    applied=ledger.apply(release,apply_runtime,reviewer='operator')
    assert applied['release']==release
    with pytest.raises(ValueError,match='pinned'):ArtifactRetention(root).move_to_trash([package,policy],project=value,retention_days=0)


def test_drift_reference_protects_snapshot_and_original_capture(tmp_path):
    from backend.engine.artifact_retention import ArtifactRetention
    from backend.engine.image_truth import digest
    _,value,root,_,_=project(tmp_path);capture=root/'dataset'/'capture_intake';reference=capture/'drift'/('driftref_'+'1'*32+'.json')
    reference.parent.mkdir(parents=True);candidate='capture_'+'2'*32
    snapshot=capture/'candidates'/candidate/'image.png';snapshot.parent.mkdir(parents=True);snapshot.write_bytes(b'capture')
    original=root/'runtime_service'/'state'/'uploads'/'original.png';original.parent.mkdir(parents=True);original.write_bytes(b'capture')
    checksum=hashlib.sha256(b'capture').hexdigest();record={'schema_version':1,'reference_id':reference.stem,'samples':[{'candidate_id':candidate,'source_sha256':checksum,'job_receipt_sha256':'3'*64}]};record['record_sha256']=digest(record);reference.write_text(json.dumps(record))
    (capture/'index.json').write_text(json.dumps({'candidates':{candidate:{'source_sha256':checksum,'job_receipt_sha256':'3'*64,'snapshot_path':str(snapshot.relative_to(capture)),'origin':{'image_path':str(original)}}}}))
    store=ArtifactRetention(root)
    for path in (reference,snapshot,original):
        with pytest.raises(ValueError,match='pinned'):store.move_to_trash([path],project=value,retention_days=0)
    reference.unlink()
    with pytest.raises(ValueError,match='reference|pinned'):store.move_to_trash([snapshot],project=value,retention_days=0)


def test_backup_holds_same_gc_lock_and_fresh_restore_is_a_separate_verified_receipt(tmp_path,monkeypatch):
    import backend.engine.project_archive as archive
    from backend.engine.artifact_retention import ArtifactRetention
    api,value,root,source,artifact=project(tmp_path)
    original_bytes=(source/'part.png').read_bytes();observed=threading.Event();release=threading.Event();errors=[];result={};actual=archive._digest_file
    def pause(path):
        if Path(path)==artifact/'best_model.pt':observed.set();assert release.wait(5)
        return actual(path)
    monkeypatch.setattr(archive,'_digest_file',pause)
    def backup():
        try:result.update(archive.create_archive(value,tmp_path/'backups'))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=backup);thread.start();assert observed.wait(5)
    try:
        with pytest.raises(ValueError,match='operation|busy|lock'):ArtifactRetention(root).move_to_trash([artifact],project=value,retention_days=0)
    finally:release.set();thread.join(5)
    assert not errors and result['backup_status']=='archive_verified' and result['restore_status']=='unverified'
    assert ArtifactRetention(root).status(value)['backups'][0]['restore_verified'] is False
    target=tmp_path/'fresh-restored';restored=api.post('/api/project/restore',json={'archive_path':result['archive_path'],'target_dir':str(target)})
    assert restored.status_code==200,restored.text
    assert (target/'models'/'job_old'/'best_model.pt').read_bytes()==b'verified fixture model'
    assert (Path(restored.json()['source_dataset_dir'])/'part.png').read_bytes()==original_bytes
    receipts=ArtifactRetention(target).status(restored.json())['restores']
    assert receipts[0]['status']=='restore_verified' and receipts[0]['archive_sha256']==result['archive_sha256']
    assert (source/'part.png').read_bytes()==original_bytes
    assert api.post('/api/project/restore',json={'archive_path':result['archive_path'],'target_dir':str(target)}).status_code==409


def test_retention_routes_preview_before_mutation_and_restore_exact_bytes(tmp_path):
    api,_,root,_,artifact=project(tmp_path)
    assert api.put('/api/project/retention/policy',json={'retention_days':0,'trash_days':7,'quota_bytes':1024}).status_code==200
    relative=str(artifact.relative_to(root))
    preview=api.post('/api/project/retention/trash',json={'paths':[relative]})
    assert preview.status_code==200 and preview.json()['dry_run'] and artifact.exists()
    moved=api.post('/api/project/retention/trash',json={'paths':[relative],'dry_run':False})
    assert moved.status_code==200,moved.text
    trash_id=moved.json()['trashed'][0]['trash_id']
    assert api.post('/api/project/retention/restore-trash',json={'trash_id':trash_id}).status_code==200
    assert (artifact/'best_model.pt').read_bytes()==b'verified fixture model'


def test_managed_looking_registered_source_is_never_a_cleanup_candidate(tmp_path):
    from backend.engine.artifact_retention import ArtifactRetention
    _,value,root,_,artifact=project(tmp_path)
    value['source_dataset_dir']=str(artifact)
    with pytest.raises(ValueError,match='source'):
        ArtifactRetention(root).move_to_trash([artifact],project=value,retention_days=0)
    assert (artifact/'best_model.pt').read_bytes()==b'verified fixture model'


def test_fresh_restore_preserves_a_real_drift_reference_and_capture_dependencies(tmp_path):
    from backend.engine.inspection_service import InspectionStore
    from backend.engine import capture_intake as ci, capture_drift as drift
    from backend.engine.artifact_retention import ArtifactRetention
    api,value,root,source,_=project(tmp_path)
    split=root/'dataset'/'splits'/(hashlib.sha256(str(source.resolve()).encode()).hexdigest()+'.json')
    split.parent.mkdir(parents=True,exist_ok=True);split.write_text(json.dumps({'folder_path':str(source),'assignments':{'part.png':'test'}}))
    store=InspectionStore(root/'runtime_service'/'state');image=store.state_dir/'uploads'/'camera.png'
    Image.new('RGB',(16,16),'red').save(image);job=store.enqueue(image,'http');store.claim();store.finish(job,result={'status':'success','final_verdict':'OK'})
    row=ci.register_service_jobs(value,job_ids=[job])['candidates'][0]
    reference=drift.create_reference(value,[row['candidate_id']],actor='Reviewer',name='Baseline')
    backup=api.post('/api/project/backup',json={'destination_dir':str(tmp_path/'backups')});assert backup.status_code==200,backup.text
    restored=api.post('/api/project/restore',json={'archive_path':backup.json()['archive_path'],'target_dir':str(tmp_path/'fresh')})
    assert restored.status_code==200,restored.text
    target=restored.json();actual=drift.read_reference(target,reference['reference_id'])
    assert actual['archive_restored_from_sha256']==reference['record_sha256']
    assert drift.report(target,reference['reference_id'])['reference_count']==1
    assert len([pin for pin in ArtifactRetention(target['project_dir']).status(target)['pins'] if pin['reason']=='drift_reference'])==3

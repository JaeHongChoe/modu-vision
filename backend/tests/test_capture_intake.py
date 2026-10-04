"""Service captures stay candidates until explicit review and owned adoption."""
import importlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from PIL import Image
import pytest

from backend.engine.inspection_service import InspectionStore
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import dataset_annotation_dir


@pytest.fixture
def intake():
    assert importlib.util.find_spec('backend.engine.capture_intake'), 'Capture intake is not implemented'
    return importlib.import_module('backend.engine.capture_intake')


@pytest.fixture
def project(tmp_path):
    source=tmp_path/'source';source.mkdir();root=tmp_path/'project';root.mkdir()
    project={'id':'intake-project','project_dir':str(root),'source_dataset_dir':str(source),'annotations_dir':str(root/'annotations'),
             'dataset_dir':str(root/'dataset'),'models_dir':str(root/'models'),'task':'segmentation','active_labelset_id':'default'}
    for name,color in [('train','black'),('test','white')]:
        Image.new('RGB',(24,24),color).save(source/f'{name}.png')
        (source/f'{name}.json').write_text(json.dumps({'imagePath':f'{name}.png','imageWidth':24,'imageHeight':24,'shapes':[]}))
    import hashlib
    split=root/'dataset'/'splits'/f'{hashlib.sha256(str(source).encode()).hexdigest()}.json';split.parent.mkdir(parents=True)
    split.write_text(json.dumps({'folder_path':str(source),'assignments':{'train.png':'train','test.png':'test'}}))
    with dm.metadata_transaction(root,source,project['annotations_dir']) as ledger:
        ledger['team_data']={'schema_version':1,'settings':{'revision':4,'editing_enabled':True,'review_enabled':True,
          'required_reviews':2,'prevent_self_review':True,'approved_only_training':True},'books':[]}
    store=InspectionStore(root/'runtime_service'/'state')
    image=store.state_dir/'uploads'/'camera.png';Image.new('RGB',(24,24),'red').save(image)
    job=store.enqueue(image,'http');assert store.claim()['job_id']==job
    store.finish(job,result={'status':'success','final_verdict':'OK','graph_sha256':'a'*64,'execution_steps':[{'node_id':'decision','branch_verdict':'OK'}],
      'crops':[],'runtime_identity':{'manifest_sha256':'b'*64,'model_sha256':{'job_model':'c'*64}}})
    return project,store,job


def test_registration_reopens_actual_service_job_with_unknown_truth_and_duplicate_routing(intake,project):
    p,store,job=project
    candidate=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    assert candidate['source_prediction']=='OK'
    assert candidate['truth_verdict']=='UNKNOWN'
    assert candidate['routing']=='unknown'
    assert candidate['origin']['job_id']==job
    assert candidate['origin']['runtime_identity']['manifest_sha256']=='b'*64
    assert candidate['origin']['node_evidence'][0]['node_id']=='decision'
    assert len(candidate['source_sha256'])==64
    reopened=intake.list_candidates(p)['candidates'][0]
    assert reopened['candidate_id']==candidate['candidate_id']
    assert intake.register_service_jobs(p,job_ids=[job])['candidates'][0]['candidate_id']==candidate['candidate_id']
    duplicate=store.enqueue(Path(p['source_dataset_dir'])/'test.png','file');assert store.claim()['job_id']==duplicate
    store.finish(duplicate,result={'status':'success','final_verdict':'NG'})
    assert intake.register_service_jobs(p,job_ids=[duplicate])['candidates'][0]['routing']=='duplicate'


def test_failed_capture_cannot_be_reviewed_for_adoption(intake,project):
    p,store,_=project
    missing=store.enqueue_device_event('camera','event1',store.state_dir/'uploads'/'missing.png')
    candidate=intake.register_service_jobs(p,job_ids=[missing])['candidates'][0]
    assert candidate['routing']=='failed'
    assert candidate['truth_verdict']=='UNKNOWN'
    with pytest.raises(ValueError,match='failed|unavailable'):
        intake.review_candidate(p,candidate['candidate_id'],expected_revision=candidate['revision'],actor='Reviewer',decision='adopt')


def test_adoption_requires_review_and_creates_owned_version_with_fixed_test_and_policy(intake,project):
    p,store,job=project
    source=Path(p['source_dataset_dir']);before={name:dm._hash(source/name) for name in ['train.png','test.png','test.json']}
    candidate=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    with pytest.raises(ValueError,match='review'):
        intake.adopt_candidates(p,[candidate['candidate_id']],actor='Reviewer',name='Candidate source')
    reviewed=intake.review_candidate(p,candidate['candidate_id'],expected_revision=candidate['revision'],actor='Reviewer',decision='adopt')
    assert reviewed['truth_verdict']=='UNKNOWN'
    version=intake.adopt_candidates(p,[candidate['candidate_id']],actor='Reviewer',name='Candidate source')
    assert version['activated'] is False
    assert p['source_dataset_dir']==str(source)
    owned=Path(version['source_dataset_path'])
    assert owned.is_relative_to(Path(p['dataset_dir'])) and owned!=source
    assert dm._hash(owned/'test.png')==before['test.png']
    assert {name:dm._hash(source/name) for name in before}==before
    assert version['split_assignments']['test.png']=='test'
    capture=version['adopted'][0]
    assert version['split_assignments'][capture['relative_path']]=='train'
    assert dm._hash(owned/capture['relative_path'])==candidate['source_sha256']
    newp={**p,'source_dataset_dir':str(owned)}
    row=dm.metadata_for_path(p['project_dir'],owned,owned/capture['relative_path'],p['annotations_dir'])
    assert row['workflow_state']=='needs_review'
    assert row['usage_state']=='not_used'
    with dm.metadata_transaction(p['project_dir'],owned,p['annotations_dir']) as ledger:
        assert ledger['team_data']['settings']['required_reviews']==2
        assert ledger['team_data']['settings']['approved_only_training'] is True
    reopened=intake.read_version(p,version['version_id'])
    assert reopened['record_sha256']==version['record_sha256']
    from backend.engine.image_truth import read_truth
    assert read_truth(newp,str(owned/capture['relative_path']),task='segmentation',classes=['background','crack'])['verdict']=='UNKNOWN'


def test_changed_capture_or_source_blocks_adoption_and_stale_review(intake,project):
    p,store,job=project
    candidate=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    intake.review_candidate(p,candidate['candidate_id'],expected_revision=candidate['revision'],actor='Reviewer',decision='adopt')
    with pytest.raises(ValueError,match='revision|수정'):
        intake.review_candidate(p,candidate['candidate_id'],expected_revision=candidate['revision'],actor='Second',decision='reject')
    Image.new('RGB',(25,25),'blue').save(Path(p['source_dataset_dir'])/'test.png')
    with pytest.raises(ValueError,match='source|changed'):
        intake.adopt_candidates(p,[candidate['candidate_id']],actor='Reviewer',name='Changed source')
    # Re-registering against the new base creates a fresh review candidate.
    replacement=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    assert replacement['candidate_id']!=candidate['candidate_id']
    assert replacement['routing']=='unknown'


def test_snapshot_tampering_invalidates_review_evidence(intake,project):
    p,store,job=project
    candidate=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    image=intake.candidate_image(p,candidate['candidate_id'])
    image.write_bytes(b'tampered')
    assert intake.list_candidates(p)['candidates'][0]['stale'] is True
    with pytest.raises(ValueError,match='changed'):
        intake.review_candidate(p,candidate['candidate_id'],expected_revision=1,actor='Reviewer',decision='adopt')


def test_service_database_and_capture_symlinks_are_rejected(intake,project,tmp_path):
    p,store,job=project
    database=store.database;target=database.with_name('saved.sqlite3');database.rename(target);database.symlink_to(target)
    with pytest.raises(ValueError,match='link'):
        intake.register_service_jobs(p,job_ids=[job])


def test_capture_api_keeps_session_review_identity_and_source_selection_explicit(intake,project):
    from fastapi import FastAPI
    from backend.api.routes_capture_intake import router
    from backend.tests.test_whole_flow_evaluation import ASGIClient
    p,store,job=project
    app=FastAPI();app.include_router(router);role={'value':'trainer'}
    app.state.accounts=SimpleNamespace(project_role=lambda account,project:role['value'])
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'session','username':'Session Reviewer'}
        return await call_next(request)
    client=ASGIClient(app)
    response=client.post('/api/capture-intake/register',json={'job_ids':[job]})
    assert response.status_code==200,response.text
    candidate=response.json()['candidates'][0]
    assert client.get('/api/capture-intake/candidates/'+candidate['candidate_id']+'/preview').json()['data_url'].startswith('data:image/png;base64,')
    payload={'expected_revision':candidate['revision'],'actor':'Forged','decision':'adopt'}
    assert client.post('/api/capture-intake/candidates/'+candidate['candidate_id']+'/review',json=payload).status_code==403
    role['value']='reviewer'
    reviewed=client.post('/api/capture-intake/candidates/'+candidate['candidate_id']+'/review',json=payload)
    assert reviewed.status_code==200,reviewed.text
    assert reviewed.json()['review']['actor']=='Session Reviewer'
    adopted=client.post('/api/capture-intake/adopt',json={'candidate_ids':[candidate['candidate_id']],'actor':'Forged','name':'Reviewed source'})
    assert adopted.status_code==200,adopted.text
    assert adopted.json()['actor']=='Session Reviewer'
    assert adopted.json()['activated'] is False
    assert p['source_dataset_dir']!=adopted.json()['source_dataset_path']


def sampling_policy(revision=1):
    return dict(revision=revision,seed=7,eligibility_reasons=['ng_verdict'],window_seconds=86400,
                max_items_per_window=1,max_bytes_per_window=100000,per_product_lot_camera_quota=1,
                normal_baseline_fraction=0)


def test_sampling_production_skips_without_staging_and_replays(intake,project):
    p,store,job=project
    intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    first=intake.register_service_jobs(p,job_ids=[job])
    assert first['candidates']==[]
    assert first['sampling_receipts'][0]['decision']=='skipped'
    assert not list((Path(p['dataset_dir'])/'capture_intake'/'candidates').glob('*/image*'))
    assert intake.register_service_jobs(p,job_ids=[job])['sampling_receipts']==first['sampling_receipts']
    image=store.state_dir/'uploads'/'ng.png';Image.new('RGB',(24,24),'blue').save(image)
    ng=store.enqueue(image,'http');store.claim();store.finish(ng,result={'final_verdict':'NG'})
    selected=intake.register_service_jobs(p,job_ids=[ng])
    assert selected['candidates'][0]['truth_verdict']=='UNKNOWN'
    assert selected['candidates'][0]['sampling_receipt']['run_ref']['run_id']==ng
    assert intake.sampling_status(p)['held_items']==1
    extra=store.enqueue(image,'http');store.claim();store.finish(extra,result={'final_verdict':'NG'})
    assert intake.register_service_jobs(p,job_ids=[extra])['sampling_receipts'][0]['reason'].startswith('quota_exhausted')
    assert intake.sampling_status(p)['held_items']==1


def test_sampling_policy_revision_geometry_and_reopen(intake,project):
    p,_,_=project
    saved=intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    assert intake.sampling_status(p)['policy']==saved['policy']
    with pytest.raises(ValueError,match='revision'):
        intake.save_sampling_policy(p,sampling_policy(2),expected_revision=0)
    with pytest.raises(ValueError,match='geometry'):
        intake.save_sampling_policy(p,{**sampling_policy(2),'window_seconds':60},expected_revision=1)


def test_sampling_api_owner_policy_and_bounded_status(intake,project):
    from fastapi import FastAPI
    from backend.api.routes_capture_intake import router
    from backend.tests.test_whole_flow_evaluation import ASGIClient
    p,_,_=project;app=FastAPI();app.include_router(router);role={'value':'trainer'}
    app.state.accounts=SimpleNamespace(project_role=lambda account,project:role['value'])
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'session','username':'Session'}
        return await call_next(request)
    client=ASGIClient(app);body={'policy':sampling_policy(),'expected_revision':0}
    assert client.post('/api/capture-intake/sampling',json=body).status_code==403
    role['value']='owner'
    assert client.post('/api/capture-intake/sampling',json=body).status_code==200
    assert client.get('/api/capture-intake/sampling').json()['policy']['revision']==1
    assert client.get('/api/capture-intake/sampling?limit=501').status_code==422


def test_sampling_held_accounting_tracks_recoverable_retention_without_rewriting_receipt(intake,project):
    from backend.engine.artifact_retention import ArtifactRetention
    p,store,_=project
    image=store.state_dir/'uploads'/'ng.png';Image.new('RGB',(24,24),'blue').save(image)
    job=store.enqueue(image,'http');store.claim();store.finish(job,result={'final_verdict':'NG'})
    intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    registered=intake.register_service_jobs(p,job_ids=[job]);candidate=registered['candidates'][0]
    before=intake.sampling_status(p)
    retention=ArtifactRetention(p['project_dir'])
    moved=retention.move_to_trash([Path(p['dataset_dir'])/'capture_intake'/candidate['snapshot_path']],project=p,retention_days=0)
    after=intake.sampling_status(p)
    assert after['held_items']==0 and after['held_bytes']==0
    assert after['receipts']==before['receipts'] and after['windows'][0]['selected']==1
    retention.restore_trash(moved['trashed'][0]['trash_id'])
    assert intake.sampling_status(p)['held_items']==1


def test_sampling_uses_production_crop_typed_score(intake,project):
    p,store,_=project
    spec={'domain':'probability','unit':'probability','direction':'higher_is_defect','calibration_id':'model-a','threshold':.5}
    policy={**sampling_policy(),'eligibility_reasons':['low_confidence'],'score_spec':spec,'low_confidence_band':[.4,.6]}
    intake.save_sampling_policy(p,policy,expected_revision=0)
    image=store.state_dir/'uploads'/'typed.png';Image.new('RGB',(24,24),'blue').save(image)
    job=store.enqueue(image,'http');store.claim();store.finish(job,result={'final_verdict':'OK','crops':[{'defect_score':.5,'score_spec':spec,'roi_id':'inspect-a'}]})
    registered=intake.register_service_jobs(p,job_ids=[job])
    assert registered['sampling_receipts'][0]['decision']=='selected'
    assert registered['sampling_receipts'][0]['reason']=='low_confidence'


def test_sampling_storage_quota_failure_is_visible_and_does_not_stage(intake,project):
    from backend.engine.artifact_retention import ArtifactRetention
    p,store,_=project
    ArtifactRetention(p['project_dir']).configure(retention_days=30,trash_days=30,quota_bytes=1)
    intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    image=store.state_dir/'uploads'/'ng.png';Image.new('RGB',(24,24),'blue').save(image)
    job=store.enqueue(image,'http');store.claim();store.finish(job,result={'final_verdict':'NG'})
    candidate=intake.register_service_jobs(p,job_ids=[job])['candidates'][0]
    assert candidate['snapshot_path'] is None and candidate['routing']=='failed'
    assert 'quota' in candidate['failure'].lower()
    assert intake.sampling_status(p)['windows'][0]['retention_failed']==1


def test_sampling_policy_history_keeps_seed_and_window_for_replay(intake,project):
    p,_,_=project
    first=intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    intake.save_sampling_policy(p,{**sampling_policy(2),'seed':999},expected_revision=1)
    history=intake.sampling_status(p)['policy_history']
    assert history[0]==first and history[1]['policy']['seed']==999


def test_legacy_copy_failure_still_raises_without_publishing_candidate(intake,project,monkeypatch):
    p,_,job=project
    def failed_copy(*args,**kwargs):raise OSError('disk unavailable')
    monkeypatch.setattr(intake.shutil,'copyfile',failed_copy)
    with pytest.raises(OSError,match='disk unavailable'):
        intake.register_service_jobs(p,job_ids=[job])
    assert intake.list_candidates(p)['total']==0


def test_sampling_local_process_token_and_shared_unauthenticated_authority(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'local-user'))
    app=create_app(project_dir=str(tmp_path/'local-projects'))
    with TestClient(app) as client:
        assert client.get('/api/capture-intake/sampling').status_code==401
        client.headers['X-Vision-Token']=app.state.api_token
        assert client.post('/api/project/create',json={'name':'Sampling authority','task':'classification'}).status_code==200
        assert client.get('/api/capture-intake/sampling').status_code==200
        assert client.post('/api/capture-intake/sampling',json={'policy':sampling_policy(),'expected_revision':0}).status_code==200
        assert client.get('/api/capture-intake/sampling').json()['policy']['revision']==1
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'shared-user'))
    shared=create_app(project_dir=str(tmp_path/'shared-projects'),shared_auth_dir=str(tmp_path/'shared-auth'))
    with TestClient(shared,headers={'X-Vision-Token':shared.state.api_token}) as client:
        assert client.get('/api/capture-intake/sampling').status_code==401
        assert client.post('/api/capture-intake/sampling',json={'policy':sampling_policy(),'expected_revision':0}).status_code==401


def test_selected_sampling_replay_after_source_revision_does_not_stage_twice(intake,project):
    p,store,_=project
    intake.save_sampling_policy(p,sampling_policy(),expected_revision=0)
    image=store.state_dir/'uploads'/'ng.png';Image.new('RGB',(24,24),'blue').save(image)
    job=store.enqueue(image,'http');store.claim();store.finish(job,result={'final_verdict':'NG'})
    first=intake.register_service_jobs(p,job_ids=[job])
    Image.new('RGB',(24,24),'green').save(Path(p['source_dataset_dir'])/'new.png')
    replay=intake.register_service_jobs(p,job_ids=[job])
    assert replay['candidates'][0]['candidate_id']==first['candidates'][0]['candidate_id']
    assert intake.sampling_status(p)['held_items']==1
    row=replay['candidates'][0]
    intake.review_candidate(p,row['candidate_id'],expected_revision=row['revision'],actor='Reviewer',decision='adopt')
    with pytest.raises(ValueError,match='Active source'):
        intake.adopt_candidates(p,[row['candidate_id']],actor='Reviewer',name='Stale replay')


def test_skipped_sampling_replay_keeps_first_multicrop_policy_decision(intake,project):
    p,store,_=project
    spec=lambda name:{'domain':'probability','unit':'probability','direction':'higher_is_defect','calibration_id':name,'threshold':.5}
    policy={**sampling_policy(),'eligibility_reasons':['low_confidence'],'score_spec':spec('A'),'low_confidence_band':[.4,.6]}
    intake.save_sampling_policy(p,policy,expected_revision=0)
    image=store.state_dir/'uploads'/'multicrop.png';Image.new('RGB',(24,24),'blue').save(image)
    job=store.enqueue(image,'http');store.claim();store.finish(job,result={'final_verdict':'OK','crops':[
      {'defect_score':.9,'score_spec':spec('A')},{'defect_score':.5,'score_spec':spec('B')}]})
    first=intake.register_service_jobs(p,job_ids=[job]);assert first['sampling_receipts'][0]['decision']=='skipped'
    intake.save_sampling_policy(p,{**policy,'revision':2,'score_spec':spec('B')},expected_revision=1)
    assert intake.register_service_jobs(p,job_ids=[job])['sampling_receipts']==first['sampling_receipts']
    Image.new('RGB',(24,24),'green').save(image)
    with pytest.raises(ValueError,match='changed'):
        intake.register_service_jobs(p,job_ids=[job])

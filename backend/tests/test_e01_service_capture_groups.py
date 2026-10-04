"""Opt-in multi-view admission and durable whole-part simulator."""
import json,time
from pathlib import Path
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from backend.tests.test_inspection_service import package,_wait
from backend.engine.inspection_service import InspectionStore


def policy(revision=1,deadline=1000):
    return {'revision':revision,'policy':{'required_view_ids':['front','back'],'timestamp_basis':'trigger_offset',
      'max_skew_ms':20,'deadline_ms':deadline,'late_window_ms':1000,'completeness_policy':'all_required'}}


def frame(part,view,at=1):
    return {'part_id':part,'trigger_id':'trigger-1','view_id':view,'captured_at_ms':at}


def image(tmp_path,name):
    path=tmp_path/f'{name}.png';Image.new('RGB',(24,24),'blue').save(path);return path


def test_service_admission_reordered_interleaved_views_and_recipe_bindings(tmp_path):
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64,'recipe_id':'recipe-1'})
    store.configure_capture_groups(policy(),expected_revision=0)
    jobs=[]
    for part,view in [('A','back'),('B','front'),('A','front'),('B','back')]:
        job=store.enqueue(image(tmp_path,part+view),'file',capture=frame(part,view));jobs.append(job)
        assert store.claim()['job_id']==job
        store.finish(job,result={'final_verdict':'NG' if part=='B' and view=='back' else 'OK'})
    groups=store.capture_group_status()['groups'];by_part={row['part_id']:row for row in groups}
    assert by_part['A']['verdict']=='OK' and by_part['B']['verdict']=='NG'
    assert all(row['state']=='COMPLETE' and row['recipe_sha256']=='a'*64 for row in groups)
    assert by_part['A']['frame_refs']['front']!=by_part['B']['frame_refs']['front']
    assert store.get(jobs[0])['verdict']=='REVIEW','open part cannot expose frame OK as whole-part OK'
    assert store.get(jobs[2])['result']['capture_group']['verdict']=='OK'


def test_capture_group_duplicate_missing_metadata_conflict_and_recipe_mismatch(tmp_path):
    binding={'manifest_sha256':'a'*64}
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:binding)
    store.configure_capture_groups(policy(),expected_revision=0)
    source=image(tmp_path,'one');one=store.enqueue(source,'file',capture=frame('A','front'))
    assert store.enqueue(source,'file',capture=frame('A','front'))==one
    missing=store.enqueue(source,'file')
    assert store.get(missing)['verdict']=='REVIEW' and store.get(missing)['state']=='error'
    binding['manifest_sha256']='b'*64
    mixed=store.enqueue(source,'file',capture=frame('A','back'))
    assert store.get(mixed)['verdict']=='REVIEW' and store.get(mixed)['state']=='error'
    assert store.claim()['job_id']==one;store.finish(one,result={'final_verdict':'OK'})
    binding['manifest_sha256']='a'*64
    last=store.enqueue(source,'file',capture=frame('A','back'));store.claim();store.finish(last,result={'final_verdict':'OK'})
    assert store.capture_group_status()['groups'][0]['verdict']=='REVIEW'


def test_capture_group_restart_deadline_and_late_frame_cannot_promote_ok(tmp_path):
    binding=lambda:{'manifest_sha256':'a'*64}
    store=InspectionStore(tmp_path/'state',runtime_provider=binding);store.configure_capture_groups(policy(deadline=5),expected_revision=0)
    source=image(tmp_path,'part');store.enqueue(source,'file',capture=frame('A','front'))
    time.sleep(.02)
    reopened=InspectionStore(tmp_path/'state',runtime_provider=binding)
    group=reopened.capture_group_status()['groups'][0]
    assert group['state']=='EXPIRED' and group['verdict']=='REVIEW'
    late=reopened.enqueue(source,'file',capture=frame('A','back'))
    assert reopened.get(late)['verdict']=='REVIEW' and reopened.get(late)['state']=='error'


def test_pipeline_export_reopen_retains_optional_join_policy(package,tmp_path):
    from backend.engine.flow_package import build_flow_package,verify_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart,FlowchartPipeline
    pipeline=get_single_detection_flowchart(job_id='job_detector')
    assert 'capture_group_policy' not in pipeline.model_dump(),'no-policy graph serialization stays legacy'
    data=pipeline.model_dump();data['capture_group_policy']=policy()
    configured=FlowchartPipeline.model_validate(data)
    built=build_flow_package(pipeline=configured,checkpoints={'job_detector':package/'models'/'job_detector'/'best_model.pt'},output_base_dir=tmp_path/'exports2',package_name='join_policy')
    reopened,_=verify_flow_package(Path(built['package_path']))
    assert reopened.capture_group_policy==configured.capture_group_policy


@pytest.mark.parametrize('second_view_late',[False,True],ids=['within-deadline','expired-during-inference'])
def test_actual_service_http_admission_join_and_restart_readback(package,tmp_path,monkeypatch,second_view_late):
    from backend.engine import inspection_service,service_capture_groups
    # Real HTTP/SQLite lifecycle; only the join clock and inference are controlled.
    # A loaded Windows runner must not turn the normal-join case into a timeout.
    # The second case crosses the unchanged deadline while inference is running.
    clock=[0];calls=[];capture_groups=service_capture_groups.CaptureGroups
    def groups_with_clock(*args,**kwargs):
        return capture_groups(*args,**kwargs,wall_ms=lambda:1_700_000_000_000+clock[0],monotonic_ms=lambda:clock[0])
    monkeypatch.setattr(service_capture_groups,'CaptureGroups',groups_with_clock)
    def infer(*_args,**_kwargs):
        calls.append(True)
        if len(calls)==2:
            clock[0]=1001 if second_view_late else 500
        return {'final_verdict':'OK','crops':[]}
    monkeypatch.setattr(inspection_service,'run_flow_package',infer)
    expected_verdict='REVIEW' if second_view_late else 'OK'
    expected_state='EXPIRED' if second_view_late else 'COMPLETE'
    state=tmp_path/'state';app=inspection_service.create_service_app(package,state,token='secret')
    source=image(tmp_path,'http')
    with TestClient(app) as client:
        assert client.get('/v1/capture-groups').status_code==401
        client.headers['X-Vision-Token']='secret'
        assert client.put('/v1/capture-groups/policy',json={'policy':policy(),'expected_revision':0}).status_code==200
        first=client.post('/v1/jobs/file',json={'image_path':str(source),'capture':frame('A','back')}).json()['job_id']
        assert _wait(client,first,'completed')['verdict']=='REVIEW'
        last=client.post('/v1/jobs/file',json={'image_path':str(source),'capture':frame('A','front')}).json()['job_id']
        result=_wait(client,last,'completed')
        assert result['verdict']==expected_verdict
        assert result['result']['capture_group']['state']==expected_state
        if second_view_late:assert result['result']['capture_group']['disposition']=='late'
        assert len(calls)==2
        assert client.post('/v1/jobs/file',json={'image_path':str(source),'capture':frame('A','front')}).json()['job_id']==last
        assert client.get('/v1/capture-groups').json()['groups'][0]['state']==expected_state
    reopened=inspection_service.create_service_app(package,state,token='secret',auto_worker=False)
    with TestClient(reopened,headers={'X-Vision-Token':'secret'}) as client:
        group=client.get('/v1/capture-groups').json()['groups'][0]
        assert group['verdict']==expected_verdict and group['state']==expected_state


def test_optin_device_event_missing_group_identity_is_review(tmp_path):
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(),expected_revision=0)
    job=store.enqueue_device_event('camera','trigger',image(tmp_path,'device'))
    assert store.get(job)['state']=='error' and store.get(job)['verdict']=='REVIEW'


def test_project_policy_api_save_reopen_local_authority(tmp_path,monkeypatch):
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user-data'))
    app=create_app(project_dir=str(tmp_path/'projects'))
    with TestClient(app) as client:
        assert client.get('/api/runtime-services/capture-groups').status_code==401
        client.headers['X-Vision-Token']=app.state.api_token
        client.post('/api/project/create',json={'name':'Join policy','task':'classification'})
        saved=client.put('/api/runtime-services/capture-groups/policy',json={'policy':policy(),'expected_revision':0})
        assert saved.status_code==200,saved.text
        assert client.get('/api/runtime-services/capture-groups').json()['policy']==saved.json()
        assert client.put('/api/runtime-services/capture-groups/policy',json={'policy':policy(2),'expected_revision':0}).status_code==409


def test_group_rejections_cannot_retry_or_replay_into_legacy_ok(tmp_path):
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(),expected_revision=0);source=image(tmp_path,'retry')
    missing=store.enqueue(source,'file')
    assert not store.retry(missing)
    good=store.enqueue(source,'file',capture=frame('A','front'));store.claim();store.finish(good,result={'final_verdict':'OK'})
    with pytest.raises(ValueError,match='capture|Capture|part'):
        store.replay(good,reason='test replay',operator='QA')


def test_group_upload_identity_and_dedup_use_actual_http_path(package,tmp_path):
    from backend.engine import inspection_service
    source=image(tmp_path,'upload');app=inspection_service.create_service_app(package,tmp_path/'upload-state',token='secret',auto_worker=False)
    with TestClient(app,headers={'X-Vision-Token':'secret'}) as client:
        client.put('/v1/capture-groups/policy',json={'policy':policy(),'expected_revision':0})
        headers={'X-Vision-Capture':json.dumps(frame('A','front'))}
        first=client.post('/v1/jobs/upload',content=source.read_bytes(),headers=headers)
        assert first.status_code==202 and first.json()['state']=='queued'
        assert client.get('/v1/jobs/'+first.json()['job_id']).json()['capture']['part_id']=='A'
        assert client.post('/v1/jobs/upload',content=source.read_bytes(),headers=headers).json()['job_id']==first.json()['job_id']


def test_exported_revision_can_initialize_new_service(package,tmp_path):
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart,FlowchartPipeline
    from backend.engine.inspection_service import create_service_app
    data=get_single_detection_flowchart(job_id='job_detector').model_dump();data['capture_group_policy']=policy(3)
    configured=FlowchartPipeline.model_validate(data)
    built=build_flow_package(pipeline=configured,checkpoints={'job_detector':package/'models'/'job_detector'/'best_model.pt'},output_base_dir=tmp_path/'exports3',package_name='revision3')
    app=create_service_app(Path(built['package_path']),tmp_path/'fresh-service',token='secret',auto_worker=False)
    with TestClient(app,headers={'X-Vision-Token':'secret'}) as client:
        assert client.get('/v1/capture-groups').json()['policy']==configured.capture_group_policy


def test_same_capture_bytes_relocated_are_idempotent_but_changed_pixels_conflict(tmp_path):
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(),expected_revision=0)
    first=image(tmp_path,'first');copy=tmp_path/'copy.png';copy.write_bytes(first.read_bytes())
    original=store.enqueue(first,'file',capture=frame('A','front'))
    assert store.enqueue(copy,'file',capture=frame('A','front'))==original
    Image.new('RGB',(24,24),'red').save(copy)
    conflict=store.enqueue(copy,'file',capture=frame('A','front'))
    assert store.get(conflict)['dead_letter_reason']=='CAPTURE_VIEW_CONFLICT'
    assert store.claim()['job_id']==original;store.finish(original,result={'final_verdict':'OK'})
    last=store.enqueue(first,'file',capture=frame('A','back'));store.claim();store.finish(last,result={'final_verdict':'OK'})
    assert store.capture_group_status()['groups'][0]['verdict']=='REVIEW'


def test_conflicting_late_view_is_recorded_without_rewriting_whole_part(tmp_path):
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(),expected_revision=0)
    for view in ('front','back'):
        job=store.enqueue(image(tmp_path,view),'file',capture=frame('A',view));store.claim();store.finish(job,result={'final_verdict':'OK'})
    changed=tmp_path/'changed.png';Image.new('RGB',(24,24),'red').save(changed)
    late=store.enqueue(changed,'file',capture=frame('A','front'))
    assert store.get(late)['verdict']=='REVIEW'
    group=store.capture_group_status()['groups'][0]
    assert group['verdict']=='OK' and group['alarms']

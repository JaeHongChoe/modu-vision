"""E01 idle-worker whole-part deadline output, using local fake transport only."""
import json,time
from types import SimpleNamespace
from fastapi.testclient import TestClient
from backend.tests.test_inspection_service import package,_wait
from backend.tests.test_e01_service_capture_groups import policy,frame,image
from backend.engine.inspection_service import InspectionStore


def test_idle_worker_autonomously_delivers_missing_view_review(package,tmp_path,monkeypatch):
    from backend.engine import inspection_service
    messages=[]
    def post(url,**kwargs):
        messages.append(kwargs['json']);return SimpleNamespace(status_code=200)
    monkeypatch.setattr(inspection_service.httpx,'post',post)
    monkeypatch.setattr(inspection_service,'run_flow_package',lambda *_args,**_kwargs:{'final_verdict':'OK','crops':[]})
    state=tmp_path/'state';app=inspection_service.create_service_app(package,state,token='secret',result_webhook_url='http://fake.invalid/result')
    with TestClient(app,headers={'X-Vision-Token':'secret'}) as client:
        client.put('/v1/capture-groups/policy',json={'policy':policy(deadline=250),'expected_revision':0})
        admitted=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path,'view')),'capture':frame('part-A','front')}).json()['job_id']
        original=_wait(client,admitted,'completed')
        started=time.monotonic()
        while not messages and time.monotonic()-started<2:time.sleep(.02)
        assert len(messages)==1,'idle worker must enqueue and deliver one missing-view whole-part outcome'
        output=messages[0]
        assert output['model_verdict']=='REVIEW'
        assert output['result']['capture_group']['state']=='EXPIRED'
        assert output['result']['capture_group']['missing_view_ids']==['back']
        assert output['result']['capture_group']['part_id']=='part-A'
        assert output['runtime_identity']==original['runtime_binding']
        time.sleep(.25);assert len(messages)==1
    reopened=inspection_service.create_service_app(package,state,token='secret',result_webhook_url='http://fake.invalid/result')
    with TestClient(reopened,headers={'X-Vision-Token':'secret'}):time.sleep(.3)
    assert len(messages)==1,'confirmed delivery must stay confirmed after restart'


def admitted_store(tmp_path,monkeypatch):
    clock=controlled_join_clock(monkeypatch)
    selected={'manifest_sha256':'a'*64,'recipe_id':'frozen'}
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:selected)
    store.configure_capture_groups(policy(deadline=5),expected_revision=0)
    source=image(tmp_path,'first');job=store.enqueue(source,'file',capture=frame('A','front'))
    store.claim();store.finish(job,result={'final_verdict':'OK'})
    clock['now']+=20
    return store,selected,source,job


def test_deadline_outbox_restart_retry_reuses_frozen_identity(tmp_path,monkeypatch):
    store,selected,source,original=admitted_store(tmp_path,monkeypatch)
    selected.update(manifest_sha256='b'*64,recipe_id='new-active')
    outputs=store.sweep_capture_deadlines(require_delivery=True)
    assert len(outputs)==1
    output=store.get(outputs[0]);assert output['runtime_binding']['manifest_sha256']=='a'*64
    delivery=store.claim_delivery();assert delivery['job_id']==outputs[0]
    # Crash while sending reuses the existing delivery identity.
    reopened=InspectionStore(tmp_path/'state',runtime_provider=lambda:selected);reopened.recover()
    assert reopened.sweep_capture_deadlines(require_delivery=True)==[]
    delivery=reopened.claim_delivery();assert delivery['job_id']==outputs[0]
    reopened.finish_delivery(outputs[0],'simulated destination unavailable')
    assert reopened.retry_delivery(outputs[0])
    assert reopened.claim_delivery()['job_id']==outputs[0]
    reopened.finish_delivery(outputs[0],None)
    assert reopened.get(outputs[0])['verdict']=='REVIEW'
    late=reopened.enqueue(source,'file',capture=frame('A','back'))
    assert reopened.get(late)['verdict']=='REVIEW'
    assert reopened.sweep_capture_deadlines(require_delivery=True)==[]
    assert reopened.claim_delivery() is None


def test_readback_expiry_before_outbox_and_failed_commit_are_recoverable(tmp_path,monkeypatch):
    store,selected,source,original=admitted_store(tmp_path,monkeypatch)
    assert store.capture_group_status()['groups'][0]['state']=='EXPIRED'
    old=store._event
    def fail_event(*args,**kwargs):raise RuntimeError('crash before outbox transaction commit')
    monkeypatch.setattr(store,'_event',fail_event)
    import pytest
    with pytest.raises(RuntimeError,match='crash'):store.sweep_capture_deadlines(require_delivery=True)
    monkeypatch.setattr(store,'_event',old)
    outputs=store.sweep_capture_deadlines(require_delivery=True)
    assert len(outputs)==1 and store.claim_delivery()['job_id']==outputs[0]


def test_deadline_output_stays_local_when_delivery_is_not_enabled(tmp_path,monkeypatch):
    store,selected,source,original=admitted_store(tmp_path,monkeypatch)
    outputs=store.sweep_capture_deadlines(require_delivery=False)
    assert len(outputs)==1
    assert store.get(outputs[0])['state']=='completed' and store.get(outputs[0])['verdict']=='REVIEW'
    assert store.claim_delivery() is None
    assert store.sweep_capture_deadlines(require_delivery=True)==[]
    assert store.claim_delivery() is None,'enabling transport later must not reinterpret an already local-only outcome'


def test_completed_group_does_not_create_second_whole_part_output(tmp_path):
    selected={'manifest_sha256':'a'*64}
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:selected)
    store.configure_capture_groups(policy(deadline=20),expected_revision=0)
    source=image(tmp_path,'whole')
    for view in ('front','back'):
        job=store.enqueue(source,'file',capture=frame('A',view));store.claim()
        store.finish(job,result={'final_verdict':'OK'},require_delivery=True)
    delivery=store.claim_delivery();assert delivery is not None
    store.finish_delivery(delivery['job_id'],None)
    time.sleep(.04)
    assert store.sweep_capture_deadlines(require_delivery=True)==[]
    assert store.claim_delivery() is None


def test_deadline_output_cannot_be_replayed_as_ordinary_inference(tmp_path,monkeypatch):
    import pytest
    store,selected,source,original=admitted_store(tmp_path,monkeypatch)
    output=store.sweep_capture_deadlines(require_delivery=True)[0]
    store.claim_delivery();store.finish_delivery(output,'destination unavailable')
    with pytest.raises(ValueError,match='Capture'):store.replay(output,reason='retry',operator='QA')
    assert not store.retry(output)


def test_deadline_transport_retries_expose_same_destination_idempotency_key(tmp_path,monkeypatch):
    from backend.engine import inspection_service
    store,selected,source,original=admitted_store(tmp_path,monkeypatch)
    output=store.sweep_capture_deadlines(require_delivery=True)[0];requests=[]
    def post(url,**kwargs):
        requests.append(kwargs);return SimpleNamespace(status_code=503 if len(requests)==1 else 200)
    monkeypatch.setattr(inspection_service.httpx,'post',post)
    inspection_service._deliver_job(store,store.claim_delivery(),'http://fake.invalid/result',None)
    assert store.retry_delivery(output)
    inspection_service._deliver_job(store,store.claim_delivery(),'http://fake.invalid/result',None)
    assert [row['headers'].get('Idempotency-Key') for row in requests]==[output,output]
    assert store.get(output)['state']=='completed' and store.get(output)['verdict']=='REVIEW'


def controlled_join_clock(monkeypatch):
    # Keep real joins/SQLite/outbox and advance only the deadline clock. A five
    # millisecond wall deadline could expire before finish under parallel load.
    from backend.engine import service_capture_groups
    real_groups=service_capture_groups.CaptureGroups
    clock={'now':1000}
    monkeypatch.setattr(service_capture_groups,'CaptureGroups',
        lambda path,policy:real_groups(path,policy,monotonic_ms=lambda:clock['now'],wall_ms=lambda:1700000000000+clock['now']))
    return clock


def test_many_parts_receive_one_outcome_without_outbox_capacity_overflow(tmp_path,monkeypatch):
    clock=controlled_join_clock(monkeypatch)
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64},max_outstanding=100)
    store.configure_capture_groups(policy(deadline=5),expected_revision=0);source=image(tmp_path,'many')
    for part in range(101):
        job=store.enqueue(source,'file',capture=frame(str(part),'front'));store.claim();store.finish(job,result={'final_verdict':'OK'})
    clock['now']+=20
    first=store.sweep_capture_deadlines(require_delivery=True)
    assert len(first)==100
    first_delivery=store.claim_delivery();store.finish_delivery(first_delivery['job_id'],None)
    second=store.sweep_capture_deadlines(require_delivery=True)
    assert len(second)==1 and second[0] not in first
    all_outputs=set(first+second)
    while delivery:=store.claim_delivery():store.finish_delivery(delivery['job_id'],None)
    for _ in range(3):assert store.sweep_capture_deadlines(require_delivery=True)==[]
    assert len(all_outputs)==101 and all(store.get(job)['verdict']=='REVIEW' for job in all_outputs)


def test_skewed_view_deadline_delivers_incomplete_review_identity(tmp_path,monkeypatch):
    clock=controlled_join_clock(monkeypatch)
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(deadline=5),expected_revision=0);source=image(tmp_path,'skew')
    job=store.enqueue(source,'file',capture=frame('A','front',at=100));store.claim();store.finish(job,result={'final_verdict':'OK'})
    clock['now']+=20
    output=store.sweep_capture_deadlines(require_delivery=True)[0]
    result=store.get(output)['result']
    assert result['capture_group']['state']=='INCOMPLETE' and result['final_verdict']=='REVIEW'
    assert result['admitted_frames'][0]['captured_at_ms']==100
    assert store.claim_delivery()['job_id']==output


def test_destination_acceptance_before_local_crash_retries_same_key(tmp_path,monkeypatch):
    import pytest
    from backend.engine import inspection_service
    store,selected,source,original=admitted_store(tmp_path,monkeypatch);output=store.sweep_capture_deadlines(require_delivery=True)[0]
    accepted=set();requests=[]
    def post(url,**kwargs):
        key=kwargs['headers']['Idempotency-Key'];requests.append(key);accepted.add(key)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(inspection_service.httpx,'post',post)
    def crash(*args):raise SystemExit('process exited before acknowledgment commit')
    monkeypatch.setattr(store,'finish_delivery',crash)
    with pytest.raises(SystemExit):inspection_service._deliver_job(store,store.claim_delivery(),'http://fake.invalid/result',None)
    reopened=InspectionStore(tmp_path/'state');reopened.recover()
    inspection_service._deliver_job(reopened,reopened.claim_delivery(),'http://fake.invalid/result',None)
    assert requests==[output,output] and accepted=={output}
    assert reopened.get(output)['state']=='completed'


def test_rejected_attempts_are_not_claimed_as_admitted_deadline_frames(tmp_path):
    selected={'manifest_sha256':'a'*64,'recipe_id':'rejected-origin'}
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:selected)
    store.configure_capture_groups(policy(deadline=80),expected_revision=0);source=image(tmp_path,'valid')
    unknown=store.enqueue(source,'file',capture=frame('A','side'))
    selected['recipe_id']='valid-origin'
    valid=store.enqueue(source,'file',capture=frame('A','front'));store.claim();store.finish(valid,result={'final_verdict':'OK'})
    changed=tmp_path/'conflict.png'
    from PIL import Image
    Image.new('RGB',(24,24),'red').save(changed)
    conflict=store.enqueue(changed,'file',capture=frame('A','front'))
    time.sleep(.1)
    late=store.enqueue(source,'file',capture=frame('A','back'))
    assert all(store.get(job)['capture']['admission_failure'] for job in (unknown,conflict,late))
    output=store.sweep_capture_deadlines(require_delivery=True)[0];result=store.get(output)['result']
    assert [row['job_id'] for row in result['admitted_frames']]==[valid]
    assert result['runtime_identity']==store.get(valid)['runtime_binding']
    assert result['final_verdict']=='REVIEW'

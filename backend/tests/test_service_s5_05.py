"""Part-level trace identity and immutable links between original/reinspection."""
import json
import pytest
from backend.tests.test_service_s5_02 import client_for,image
from backend.tests.test_inspection_service import package  # noqa: F401


def test_part_reinspection_links_preserve_original_result_and_version_after_restart(package,tmp_path):
    app,client=client_for(package,tmp_path)
    with client:
        first=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path)),'image_id':'PART-001','product_id':'Product-A','lot_id':'Lot-1','operator':'operator-a'}).json()['job_id']
        store=app.state.inspection_store;store.claim();store.finish(first,result={'final_verdict':'NG','nodes':{'model-a':{'score':.8}}})
        original=store.get(first)
        second=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path,'next.png',100)),'image_id':'PART-001','product_id':'Product-A','lot_id':'Lot-1','operator':'operator-b','reinspection_of':first})
        assert second.status_code==202,second.text
        new=store.get(second.json()['job_id'])
        assert new['reinspection_of']==first and new['input_operator']=='operator-b'
        assert store.get(first)==original
    _,reopened=client_for(package,tmp_path)
    with reopened:
        rows=reopened.get('/v1/queue?part_id=PART-001').json()
        assert rows['total']==2 and len(rows['jobs'])==2
        assert {r['image_sha256'] for r in rows['jobs']}=={original['image_sha256'],new['image_sha256']}
        exported=reopened.get('/v1/results/export?part_id=PART-001').json()
        assert exported['jobs'][0]['reinspection_of']==first
        assert reopened.get('/v1/queue?part_id=unknown').json()['total']==0


def test_reinspection_refuses_foreign_part_and_running_previous_job(package,tmp_path):
    _,client=client_for(package,tmp_path)
    with client:
        image_path=str(image(tmp_path));first=client.post('/v1/jobs/file',json={'image_path':image_path,'image_id':'PART-001'}).json()['job_id']
        for part,prior in [('PART-002',first),('PART-001','foreign'),('PART-001',first)]:
            response=client.post('/v1/jobs/file',json={'image_path':image_path,'image_id':part,'reinspection_of':prior})
            assert response.status_code==422,response.text
        assert client.get('/v1/queue').json()['total']==1


def test_retransmission_cannot_change_part_operator_or_reinspection_identity(package,tmp_path):
    _,client=client_for(package,tmp_path)
    with client:
        payload={'image_path':str(image(tmp_path)),'image_id':'PART-001','operator':'operator-a','idempotency_key':'captured-001'}
        first=client.post('/v1/jobs/file',json=payload)
        assert first.status_code==202
        for changed in ({'operator':'operator-b'},{'image_id':'PART-002'},{'reinspection_of':'foreign'}):
            assert client.post('/v1/jobs/file',json={**payload,**changed}).status_code==409
        assert client.post('/v1/jobs/file',json=payload).json()['job_id']==first.json()['job_id']


def test_filtered_csv_keeps_part_operator_prior_and_original_prediction(package,tmp_path):
    app,client=client_for(package,tmp_path)
    with client:
        first=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path)),'image_id':'=PART','operator':'=Operator'}).json()['job_id']
        store=app.state.inspection_store;store.claim();store.finish(first,result={'final_verdict':'NG','nodes':{'real-model-output':{'score':.75}}})
        exported=client.get('/v1/results/export?format=csv&part_id=%3DPART')
        assert exported.status_code==200
        assert "'=PART" in exported.text and "'=Operator" in exported.text
        assert 'real-model-output' in exported.text and 'reinspection_of' in exported.text


def test_capture_group_retransmission_keeps_operator_identity(tmp_path):
    from backend.engine.inspection_service import InspectionStore,InputConflict
    from backend.tests.test_e01_service_capture_groups import policy,frame
    store=InspectionStore(tmp_path/'state',runtime_provider=lambda:{'manifest_sha256':'a'*64})
    store.configure_capture_groups(policy(),expected_revision=0)
    payload=dict(image_id='PART-001',operator='operator-a',idempotency_key='same-capture',capture=frame('PART-001','front'))
    source=image(tmp_path)
    first=store.enqueue(source,'file',**payload)
    assert store.enqueue(source,'file',**payload)==first
    with pytest.raises(InputConflict):store.enqueue(source,'file',**{**payload,'operator':'operator-b'})


def test_old_part_review_is_reachable_after_more_than_recent_page(tmp_path):
    from backend.tests.test_product_delivery import project
    from backend.engine import product_delivery
    from backend.engine.inspection_service import InspectionStore
    p=project(tmp_path);store=InspectionStore(tmp_path/'project'/'runtime_service'/'state');source=image(tmp_path)
    original=store.enqueue(source,'file',image_id='PART-001');store.claim();store.finish(original,result={'final_verdict':'NG'})
    before=store.get(original)
    for index in range(101):
        recent=store.enqueue(source,'file',image_id=f'other-{index}');store.claim();store.finish(recent,result={'final_verdict':'OK'})
    assert original not in {row['job_id'] for row in product_delivery.operator_records(p)}
    assert product_delivery.review_operator_result(p,original,'REVIEW','reviewer-a','Reinspection requested')['saved']
    assert product_delivery.operator_records(p,identifier=original)[0]['operator_review']['verdict']=='REVIEW'
    assert store.get(original)==before


def test_shared_review_records_authenticated_actor_instead_of_supplied_name(tmp_path):
    from fastapi import FastAPI
    from types import SimpleNamespace
    from backend.tests.test_product_delivery import project,ApiClient
    from backend.api.routes_product_delivery import router
    from backend.engine import product_delivery
    from backend.engine.inspection_service import InspectionStore
    p=project(tmp_path);store=InspectionStore(tmp_path/'project'/'runtime_service'/'state');source=image(tmp_path)
    identifier=store.enqueue(source,'file',image_id='PART-001');store.claim();store.finish(identifier,result={'final_verdict':'NG'})
    app=FastAPI();app.include_router(router);app.state.accounts=SimpleNamespace(project_role=lambda *args:'reviewer')
    @app.middleware('http')
    async def scope(request,call_next):
        request.state.scoped_project=p;request.state.account_user={'id':'user-a','username':'Authenticated reviewer'}
        return await call_next(request)
    response=ApiClient(app).post(f'/api/product-delivery/operator/results/{identifier}/review',json={'verdict':'REVIEW','reviewer':'another-user','reason':'Controlled review'})
    assert response.status_code==200,response.text
    assert product_delivery.operator_records(p)[0]['operator_review']['reviewer']=='Authenticated reviewer'


def test_csv_keeps_equipment_and_camera_acquisition_identity(package,tmp_path):
    app,client=client_for(package,tmp_path)
    acquisition={'schema_version':1,'camera_id':'camera-front','stream_session_id':'a'*32,'sequence':1,
                 'captured_unix_ms':1700000000000,'observed_monotonic_ms':100,'clock_basis':'host_read_completion',
                 'observed_dropped_count':0,'clock_discontinuity':False}
    with client:
        identifier=app.state.inspection_store.enqueue(image(tmp_path),'device:fixture-equipment',image_id='PART-001',acquisition=acquisition)
        exported=client.get('/v1/results/export?format=csv&part_id=PART-001')
        assert exported.status_code==200
        assert 'device:fixture-equipment' in exported.text and 'camera-front' in exported.text and identifier in exported.text

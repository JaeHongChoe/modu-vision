"""Admission identity, bounded durable input and explicit replay contracts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.main  # noqa: F401
from backend.engine import inspection_service as service
from backend.tests.test_inspection_service import package  # noqa: F401


def image(tmp_path, name="part.png", color=0):
    path = tmp_path / name
    Image.new("RGB", (8, 8), (color, color, color)).save(path)
    return path


def client_for(package, tmp_path, **kwargs):
    app = service.create_service_app(package, tmp_path / "state", token="secret", auto_worker=False, **kwargs)
    return app, TestClient(app, headers={"X-Vision-Token": "secret"})


def test_duplicate_http_key_survives_restart_and_conflicting_payload_is_rejected(package, tmp_path):
    path = image(tmp_path)
    app, client = client_for(package, tmp_path, max_outstanding=1)
    with client:
        first = client.post("/v1/jobs/file", json={"image_path": str(path), "idempotency_key": "trigger-1"})
        assert first.status_code == 202
        assert client.post("/v1/jobs/file", json={"image_path": str(image(tmp_path, "other.png"))}).status_code == 429
    _, reopened = client_for(package, tmp_path, max_outstanding=1)
    with reopened:
        duplicate = reopened.post("/v1/jobs/file", json={"image_path": str(path), "idempotency_key": "trigger-1"})
        assert duplicate.json()["job_id"] == first.json()["job_id"]
        image(tmp_path, color=100)
        conflict = reopened.post("/v1/jobs/file", json={"image_path": str(path), "idempotency_key": "trigger-1"})
        assert conflict.status_code == 409
        assert len(app.state.inspection_store.list()) == 1


def test_admission_capacity_is_atomic_across_two_store_openers(tmp_path):
    path = image(tmp_path)
    stores = [service.InspectionStore(tmp_path / "state", max_outstanding=3) for _ in range(2)]
    def admit(index):
        try:
            return stores[index % 2].enqueue(path, "file")
        except service.InboxFull:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(admit, range(16)))
    assert len([value for value in results if value]) == 3
    assert stores[0].pending_count() == 3


def test_a_admitted_before_b_activation_executes_and_delivers_a_identity(package, tmp_path, monkeypatch):
    path = image(tmp_path)
    app, client = client_for(package, tmp_path)
    with client:
        runtime = app.state.service_runtime
        runtime.identity.update(recipe_id="product-A", recipe_revision="rev-A", product_id="A")
        first = client.post("/v1/jobs/file", json={"image_path": str(path)}).json()["job_id"]
        admitted = runtime.read()
        runtime.identity.update(recipe_id="product-B", recipe_revision="rev-B", product_id="B")
        calls = []
        monkeypatch.setattr(service, "run_flow_package", lambda pkg, *_args, **kw: calls.append(str(pkg)) or {"final_verdict": "OK"})
        store = app.state.inspection_store
        row = store.claim()
        service._inspect_job(store, package, row, True, "cpu", runtime.read())
        result = store.get(first)
        assert result["result"]["runtime_identity"] == admitted
        assert result["runtime_binding"]["recipe_revision"] == "rev-A"
        payloads = []
        monkeypatch.setattr(service.httpx, "post", lambda *args, **kw: payloads.append(kw["json"]) or type("Ack", (), {"status_code": 200})())
        service._deliver_job(store, store.claim_delivery(), "http://simulator/result", None)
        assert payloads[0]["runtime_identity"]["product_id"] == "A"
        assert store.get(first)["verdict"] == "OK"
        assert calls == [admitted["package_path"]]


def test_admitted_package_tamper_fails_review_before_inference(package, tmp_path, monkeypatch):
    app, client = client_for(package, tmp_path)
    with client:
        job = client.post("/v1/jobs/file", json={"image_path": str(image(tmp_path))}).json()["job_id"]
        manifest = package / "manifest.json"
        manifest.write_text(manifest.read_text() + " ")
        monkeypatch.setattr(service, "run_flow_package", lambda *_args, **_kw: pytest.fail("Changed release must never execute"))
        store = app.state.inspection_store
        service._inspect_job(store, package, store.claim(), False)
        assert store.get(job)["state"] == "error"
        assert store.get(job)["verdict"] == "REVIEW"
        assert store.get(job)["dead_letter_reason"]


def test_expired_admission_dead_letters_without_execution_and_replay_is_explicit(package, tmp_path):
    app, client = client_for(package, tmp_path, max_queue_age_seconds=0.01)
    with client:
        job = client.post("/v1/jobs/file", json={"image_path": str(image(tmp_path))}).json()["job_id"]
        time.sleep(0.03)
        assert app.state.inspection_store.claim() is None
        failed = client.get(f"/v1/jobs/{job}").json()
        assert failed["verdict"] == "REVIEW"
        assert failed["dead_letter_reason"] == "ADMISSION_DEADLINE_EXCEEDED"
        assert client.post(f"/v1/jobs/{job}/retry").status_code == 409
        replay = client.post(f"/v1/jobs/{job}/replay", json={"reason": "operator confirmed input", "operator": "tester"})
        assert replay.status_code == 202
        reopened = client.get(f"/v1/jobs/{replay.json()['job_id']}").json()
        assert reopened["replay_of"] == job
        assert reopened["replay_count"] == 1
        assert reopened["runtime_binding"] == failed["runtime_binding"]
        assert reopened["replay_reason"] == "operator confirmed input"


def test_retry_bound_and_delivery_bound_preserve_original_result(tmp_path):
    store = service.InspectionStore(tmp_path / "state", max_attempts=2)
    job = store.enqueue(image(tmp_path), "file")
    for _ in range(2):
        assert store.claim()
        store.finish(job, error="disconnect")
        if _ == 0:
            assert store.retry(job)
    assert not store.retry(job)
    assert store.get(job)["dead_letter_reason"] == "ATTEMPT_LIMIT_EXCEEDED"


def test_folder_settlement_partial_write_corruption_and_backpressure(package, tmp_path):
    from backend.engine.input_adapters import FolderInputAdapter
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    path = image(inbox)
    adapter = FolderInputAdapter(inbox, settle_seconds=0.01)
    store = service.InspectionStore(tmp_path / "state", max_outstanding=1)
    assert adapter.scan(store) == "checking"
    image(inbox, color=70)
    time.sleep(.02)
    assert adapter.scan(store) == "checking"
    time.sleep(.02)
    assert adapter.scan(store) == "connected"
    assert len(store.list()) == 1
    (inbox / "broken.png").write_bytes(b"not a picture")
    time.sleep(.02)
    adapter.scan(store)
    time.sleep(.02)
    assert adapter.scan(store) == "backpressure"
    assert len(store.list()) == 1


def test_folder_scan_observation_is_bounded_and_later_files_are_not_starved(tmp_path):
    from backend.engine.input_adapters import FolderInputAdapter
    inbox=tmp_path/'inbox'
    inbox.mkdir()
    paths={image(inbox,f'part-{index:03d}.png') for index in range(300)}
    # The store represents already-completed duplicates, so queue capacity cannot
    # itself limit watcher state or make it stop walking the source tree.
    class CompletedStore:
        def __init__(self):self.visited=set()
        def enqueue(self,path,_source):self.visited.add(path);return str(path)
        def reject_queued(self,*_args):pytest.fail('Valid source must not be rejected')
    store=CompletedStore()
    adapter=FolderInputAdapter(inbox,settle_seconds=0)
    for _ in range(12):
        adapter.scan(store)
        assert len(adapter.observed)<=256
    assert store.visited==paths
    later=image(inbox,'new-arrival.png')
    for _ in range(12):
        adapter.scan(store)
        assert len(adapter.observed)<=256
    assert later in store.visited


def test_folder_unsettled_front_window_does_not_starve_settled_tail(tmp_path,monkeypatch):
    from backend.engine.input_adapters import FolderInputAdapter
    inbox=tmp_path/'inbox'
    inbox.mkdir()
    paths=[image(inbox,f'part-{index}.png') for index in range(6)]
    class CompletedStore:
        def __init__(self):self.visited=set()
        def enqueue(self,path,_source):self.visited.add(path);return str(path)
        def reject_queued(self,*_args):pytest.fail('Valid source must not be rejected')
    store=CompletedStore()
    adapter=FolderInputAdapter(inbox,settle_seconds=.1,max_scan_entries=2)
    clock=[0.0]
    monkeypatch.setattr('backend.engine.input_adapters.time.monotonic',lambda:clock[0])
    for index in range(16):
        # Whatever directory traversal order is used, two files keep changing.
        for path in paths[:2]:image(inbox,path.name,color=index)
        adapter.scan(store)
        assert len(adapter.observed)<=2
        clock[0]+=.5
    assert set(paths[2:]).issubset(store.visited)


def test_legacy_jobs_remain_readable_but_unknown_recipe_is_never_guessed(package, tmp_path, monkeypatch):
    store = service.InspectionStore(tmp_path / "state")
    legacy = store.enqueue(image(tmp_path), "file")
    app, client = client_for(package, tmp_path)
    with client:
        monkeypatch.setattr(service, "run_flow_package", lambda *_args, **_kw: pytest.fail("Unknown legacy binding must not execute current recipe"))
        row = app.state.inspection_store.claim()
        service._inspect_job(app.state.inspection_store, package, row, False)
        preserved = client.get(f"/v1/jobs/{legacy}").json()
        assert preserved["verdict"] == "REVIEW"
        assert preserved["binding_provenance"] == "legacy_unknown"
        assert preserved["dead_letter_reason"] == "LEGACY_RECIPE_UNKNOWN"


def test_result_exports_keep_identity_and_escape_csv(package, tmp_path, monkeypatch):
    app, client = client_for(package, tmp_path)
    with client:
        job = client.post("/v1/jobs/file", json={"image_path": str(image(tmp_path)), "image_id": "=SUM(1,2)"}).json()["job_id"]
        store = app.state.inspection_store
        monkeypatch.setattr(service, "run_flow_package", lambda *_args, **_kw: {"final_verdict": "NG"})
        service._inspect_job(store, package, store.claim(), False)
        exported = client.get("/v1/results/export?format=json")
        assert exported.status_code == 200
        assert exported.json()["jobs"][0]["runtime_binding"]["manifest_sha256"]
        csv = client.get("/v1/results/export?format=csv")
        assert "'=SUM(1,2)" in csv.text
        assert job in csv.text


def test_real_release_switch_reopen_executes_old_package_with_old_recipe(tmp_path, monkeypatch):
    from backend.engine.flow_package import build_flow_package
    from backend.engine.flowchart_engine import get_single_detection_flowchart
    root=tmp_path/'releases'
    packages=[]
    for name in ('A','B'):
        checkpoint=tmp_path/f'job_{name}'/'best_model.pt'
        checkpoint.parent.mkdir()
        checkpoint.write_bytes(name.encode())
        graph=get_single_detection_flowchart(job_id=f'job_{name}')
        packages.append(Path(build_flow_package(pipeline=graph,checkpoints={f'job_{name}':checkpoint},output_base_dir=root,package_name=name)['package_path']))
        (root/f'{name}.policy.json').write_text('{}')
    # Simulate provisioned approval, leaving actual package/device/hash verification active.
    monkeypatch.setattr(service,'_verify_release_policy',lambda *_args,**_kw:None)
    app,client=client_for(packages[0],tmp_path,runtime_root=root)
    with client:
        for index,name in enumerate(('A','B')):
            response=client.post('/v1/runtime/apply',json={'package_path':str(packages[index]),'release_policy':str(root/f'{name}.policy.json'),
                'manifest_sha256':hashlib.sha256((packages[index]/'manifest.json').read_bytes()).hexdigest(),'device':'cpu',
                'recipe':{'recipe_id':name,'recipe_revision':f'{name}-revision','product_id':f'product-{name}'}})
            assert response.status_code==200,response.text
            if name=='A':
                job=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path)),'idempotency_key':'old-trigger'}).json()['job_id']
    reopened,client=client_for(packages[1],tmp_path,runtime_root=root)
    with client:
        assert client.get('/v1/runtime').json()['recipe_id']=='B'
        calls=[]
        monkeypatch.setattr(service,'run_flow_package',lambda package,*_args,**_kw:calls.append(str(package)) or {'final_verdict':'NG'})
        store=reopened.state.inspection_store
        service._inspect_job(store,packages[1],store.claim(),False,runtime_identity=reopened.state.service_runtime.read())
        row=store.get(job)
        assert calls==[str(packages[0].resolve())]
        assert row['result']['runtime_identity']['recipe_revision']=='A-revision'
        assert row['result']['runtime_identity']['product_id']=='product-A'


def test_outbox_is_bounded_across_restarts_and_deadline_never_becomes_ok(tmp_path):
    store=service.InspectionStore(tmp_path/'state',max_attempts=2)
    job=store.enqueue(image(tmp_path),'file')
    store.claim()
    store.finish(job,result={'final_verdict':'OK'},require_delivery=True)
    for _ in range(2):
        assert store.claim_delivery()
        store.finish_delivery(job,'MES disconnected')
        store.recover()
    assert store.claim_delivery() is None
    assert not store.retry_delivery(job)
    row=store.get(job)
    assert row['verdict']=='REVIEW'
    assert row['model_verdict']=='OK'
    assert row['result']['final_verdict']=='OK'
    assert row['dead_letter_reason']=='DELIVERY_ATTEMPT_LIMIT_EXCEEDED'


def test_old_database_migration_preserves_original_result_and_events(tmp_path):
    root=tmp_path/'state'
    root.mkdir()
    with sqlite3.connect(root/'inspection_service.sqlite3') as db:
        db.executescript('''CREATE TABLE jobs(job_id TEXT PRIMARY KEY,image_path TEXT NOT NULL,image_id TEXT,image_sha256 TEXT NOT NULL,
            source TEXT NOT NULL,state TEXT NOT NULL,verdict TEXT,result_json TEXT,error TEXT,attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE events(event_id INTEGER PRIMARY KEY AUTOINCREMENT,job_id TEXT NOT NULL,state TEXT NOT NULL,message TEXT,created_at TEXT NOT NULL);
            INSERT INTO jobs VALUES('old','original.png','original','hash','file','completed','NG','{"final_verdict":"NG"}',NULL,1,'old-time','old-time');
            INSERT INTO events(job_id,state,message,created_at) VALUES('old','completed','original evidence','old-time');''')
    store=service.InspectionStore(root)
    row=store.get('old')
    assert row['verdict']=='NG'
    assert row['result']=={'final_verdict':'NG'}
    assert row['runtime_binding'] is None
    assert row['binding_provenance']=='legacy_unknown'
    assert store.events('old')[0]['message']=='original evidence'


def test_upload_retransmission_conflict_and_backpressure_leave_no_orphan_blobs(package,tmp_path):
    app,client=client_for(package,tmp_path,max_outstanding=1)
    data=image(tmp_path).read_bytes()
    with client:
        first=client.post('/v1/jobs/upload',content=data,headers={'Idempotency-Key':'upload-A'})
        duplicate=client.post('/v1/jobs/upload',content=data,headers={'Idempotency-Key':'upload-A'})
        assert first.status_code==duplicate.status_code==202
        assert first.json()['job_id']==duplicate.json()['job_id']
        full=client.post('/v1/jobs/upload',content=data,headers={'Idempotency-Key':'upload-B'})
        assert full.status_code==429
        changed=image(tmp_path,color=120).read_bytes()
        assert client.post('/v1/jobs/upload',content=changed,headers={'Idempotency-Key':'upload-A'}).status_code==409
        assert len(list((tmp_path/'state'/'uploads').iterdir()))==1


def test_repeated_replay_of_same_dead_letter_cannot_bypass_lineage_limit(package,tmp_path):
    app,client=client_for(package,tmp_path,max_attempts=2)
    with client:
        job=client.post('/v1/jobs/file',json={'image_path':str(image(tmp_path))}).json()['job_id']
        store=app.state.inspection_store
        store.claim();store.finish(job,error='failure')
        first=store.replay(job,reason='retry 1',operator='reviewer')
        second=store.replay(job,reason='retry 2',operator='reviewer')
        with pytest.raises(ValueError,match='limit'):
            store.replay(job,reason='retry 3',operator='reviewer')
        assert store.get(first)['replay_root']==job
        assert store.get(second)['replay_root']==job


def test_late_delivery_ack_preserves_ack_but_never_sets_operational_ok(tmp_path,monkeypatch):
    # Advance only after delivery was claimed: Windows disk latency must not
    # accidentally turn this delivery-ack contract into an admission expiry.
    clock=[time.time()]
    monkeypatch.setattr(service.time,'time',lambda:clock[0])
    store=service.InspectionStore(tmp_path/'state',max_queue_age_seconds=.05)
    job=store.enqueue(image(tmp_path),'file')
    assert store.claim()['job_id']==job
    store.finish(job,result={'final_verdict':'OK'},require_delivery=True)
    assert store.claim_delivery()['job_id']==job
    clock[0]+=.06
    store.finish_delivery(job,None)
    row=store.get(job)
    assert row['verdict']=='REVIEW'
    assert row['model_verdict']=='OK'
    assert row['dead_letter_reason']=='DELIVERY_DEADLINE_EXCEEDED'
    with store._connection() as conn:
        assert conn.execute('SELECT state FROM deliveries WHERE job_id=?',(job,)).fetchone()[0]=='sent'

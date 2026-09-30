"""Conversion cancellation kills the owner and preserves scoped reopen journals."""
import asyncio
import json
from pathlib import Path
import sys
import threading
import time
import httpx
import pytest
from fastapi import FastAPI
from backend.tests.test_runtime_deadline_sdk import real_package


def test_optimization_api_cancels_owned_worker_and_reopens_receipt(real_package,monkeypatch):
    from backend.api import routes_export
    from backend.engine import runtime_optimization_jobs as jobs
    from backend.engine.runtime_deadline import execute_owned_process
    from backend.engine.specialized_training_jobs import require_training_source
    import backend.engine.specialized_training_jobs as specialized
    package,image=real_package;project=package.parents[1]
    folder=project/'exports'/'flows';folder.mkdir();owned=folder/'source';package.rename(owned)
    monkeypatch.setattr(routes_export,'get_current_project',lambda _:{'project_dir':str(project)})
    monkeypatch.setattr(specialized,'require_training_source',lambda _,value:image.parent)
    observed=[];started=threading.Event()
    def conversion(**options):
        started.set()
        outcome=execute_owned_process([sys.executable,'-c','import time;time.sleep(30)'],deadline_ms=60000,cancel_event=options['cancel_event'])
        observed.append(outcome)
        if outcome['status']=='cancelled':raise InterruptedError('Owned process cancelled')
        raise AssertionError('Cancellation did not terminate the worker')
    monkeypatch.setattr(jobs,'optimize_flow_package',conversion)
    app=FastAPI();app.include_router(routes_export.router)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            body={'package_dir':str(owned),'source_dataset_path':str(image.parent),'validation_images':[str(image)],'calibration_images':[]}
            rejected=await client.post('/api/export/flow/optimize',json={**body,'package_dir':str(image.parent)})
            assert rejected.status_code==422
            response=await client.post('/api/export/flow/optimize',json=body)
            assert response.status_code==200,response.text
            job_id=response.json()['job_id'];assert started.wait(2)
            cancelled=await client.post(f'/api/export/flow/optimization-jobs/{job_id}/cancel')
            assert cancelled.status_code==200
            for _ in range(50):
                await asyncio.sleep(.03)
                read=await client.get(f'/api/export/flow/optimization-jobs/{job_id}')
                if read.json()['status']=='cancelled':break
            assert read.json()['status']=='cancelled'
            assert not Path(read.json()['package_path']).exists()
            return job_id
    job_id=asyncio.run(exercise())
    assert observed[0]['deadline']['terminated'] is True
    assert jobs.read_job(project,job_id)['status']=='cancelled'
    with pytest.raises(ValueError):jobs.read_job(project/'foreign',job_id)


def test_orphan_conversion_is_interrupted_and_never_returned_as_completed(tmp_path):
    from backend.engine.runtime_optimization_jobs import read_job
    journal=tmp_path/'exports/optimization_jobs';journal.mkdir(parents=True)
    job_id='a'*32;(journal/(job_id+'.json')).write_text(json.dumps({'job_id':job_id,'status':'running','result':None}))
    assert read_job(tmp_path,job_id)['status']=='interrupted'
    assert json.loads((journal/(job_id+'.json')).read_text())['result'] is None


def test_runtime_controls_without_project_return_explicit_conflict(monkeypatch):
    from backend.api import routes_export
    monkeypatch.setattr(routes_export,'get_current_project',lambda _:None)
    app=FastAPI();app.include_router(routes_export.router)
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            prefix='/api/export/flow/optimization-jobs/'+('a'*32)
            for suffix in ('','/approval-prerequisites','/heldout-results/0'):
                assert (await client.get(prefix+suffix)).status_code==409
            assert (await client.post(prefix+'/cancel')).status_code==409
    asyncio.run(exercise())

"""Native cancellation must expose separately recorded execution and resource facts."""
import threading
import time

import pytest

from backend.tests.test_specialist_runtime_limits import FAMILIES, prepared_client, await_terminal
from backend.tests.test_specialist_training_queue import submit
from backend.engine.job_store import ledger
from backend.engine.shared_scheduler import shared_leases


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user'))


@pytest.mark.parametrize('family,module,method', FAMILIES)
def test_native_cancel_observation_keeps_reservation_until_runner_unwinds(tmp_path,monkeypatch,family,module,method):
    client,_,_,dataset=prepared_client(tmp_path,family)
    entered=threading.Event();release=threading.Event()
    def runner(*args,cancel_event,**kwargs):
        entered.set()
        assert release.wait(4)
        assert cancel_event.is_set()
        raise InterruptedError('Owned native cancellation acknowledged')
    monkeypatch.setattr(module,method,runner)
    try:
        response=submit(client,family,dataset)
        assert response.status_code in {200,202},response.text
        identifier=response.json()['job_id'];assert entered.wait(3)
        response=client.post(f'/api/{family}/jobs/{identifier}/cancel')
        assert response.status_code==200,response.text
        assert response.json()['observation']['cancel']['requested_at']>0
        pending=client.get(f'/api/{family}/jobs/{identifier}').json()['observation']
        assert pending['cancel']['requested_at']>0
        assert pending['cancel']['exit_confirmed'] is False
        assert pending['cancel']['reservation_released'] is False
        assert pending['cancel']['complete'] is False
        assert any(row['job_id']==identifier for row in shared_leases().list())
        release.set();terminal=await_terminal(client,family,identifier)
        deadline=time.monotonic()+3
        while terminal['observation']['pending_finalization'] and time.monotonic()<deadline:
            time.sleep(.01)
            terminal=client.get(f'/api/{family}/jobs/{identifier}').json()
        observed=terminal['observation']
        assert observed['execution_started'] is True
        assert observed['cancel']['acknowledged_at']>0
        assert observed['cancel']['exit_confirmed'] is True
        assert observed['cancel']['reservation_released'] is True
        assert observed['cancel']['complete'] is True
        assert observed['pending_finalization'] is False
        assert not any(row['job_id']==identifier for row in shared_leases().list())
    finally:release.set()


def test_queued_native_cancel_reports_no_trainer_entry(tmp_path,monkeypatch):
    client,_,_,dataset=prepared_client(tmp_path,'rotation')
    leases=shared_leases();assert leases.acquire('other-owned','local-compute','all')
    try:
        response=submit(client,'rotation',dataset);identifier=response.json()['job_id']
        client.post(f'/api/rotation/jobs/{identifier}/cancel')
        observed=await_terminal(client,'rotation',identifier)['observation']
        assert observed['execution_started'] is False and observed['worker_recorded'] is False
        assert observed['cancel']['complete'] is True
        assert ledger().attempts(identifier)==[]
        assert any(row['job_id']=='other-owned' for row in leases.list())
    finally:leases.release('other-owned')


def test_native_unknown_reservation_does_not_claim_completed_cancel(tmp_path,monkeypatch):
    client,_,_,dataset=prepared_client(tmp_path,'rotation')
    leases=shared_leases();assert leases.acquire('other-owned','local-compute','all')
    try:
        response=submit(client,'rotation',dataset);identifier=response.json()['job_id']
        client.post(f'/api/rotation/jobs/{identifier}/cancel');await_terminal(client,'rotation',identifier)
    finally:leases.release('other-owned')
    from backend.engine.shared_scheduler import ResourceLeases
    with monkeypatch.context() as scoped:
        scoped.setattr(ResourceLeases,'list',lambda *a,**k:(_ for _ in ()).throw(OSError('Controlled reservation read outage')))
        observed=client.get(f'/api/rotation/jobs/{identifier}').json()['observation']
        assert observed['cancel']['exit_confirmed'] is True
        assert observed['cancel']['reservation_released'] is None
        assert observed['cancel']['complete'] is False and observed['pending_finalization'] is True
    assert client.get(f'/api/rotation/jobs/{identifier}').json()['observation']['cancel']['complete'] is True


def test_old_terminal_state_without_native_exit_proof_stays_unconfirmed(tmp_path):
    from backend.contracts.context import ProjectContext
    from backend.engine.specialist_training_queue import queue_status
    store=ledger();output=tmp_path/'old-native'
    ref=store.submit(ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local'),'p',
        'specialist_training',{},job_id=output.name,output_dir=str(output))
    store.request_cancel(ref.id,'a','user requested cancellation');store.finish(ref.id,'abort')
    observed=queue_status(output)['observation']
    assert observed['cancel']['requested_at']>0
    assert observed['cancel']['exit_confirmed'] is False and observed['cancel']['complete'] is False
    assert observed['worker_recorded'] is None
    assert observed['pending_finalization'] is False
    assert queue_status(tmp_path/'foreign'/output.name)=={}


@pytest.mark.parametrize('quota_blocked', [False,True])
def test_native_dispatch_rechecks_the_highest_eligible_job_after_a_reservation_race(tmp_path,monkeypatch,quota_blocked):
    from backend.contracts.context import ProjectContext
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine import specialist_training_queue as queue
    store=ledger();leases=shared_leases();scheduler=JobScheduler(store,leases)
    admissions=[]
    for identifier,priority in [('low',-3),('high',8)]:
        ref=store.submit(ProjectContext(workspace_id='w',project_id=identifier,actor_id='a',mode='local'),
            identifier,'specialist_training',{},job_id=identifier,output_dir=str(tmp_path/identifier))
        scheduler.enqueue(ref.id,ref.revision,priority=priority,resources={'host':'local-compute','selector':'all'})
        owner=queue.NativeAdmission(store,scheduler,identifier,tmp_path/identifier,'a',threading.Event())
        admissions.append(owner);queue._READY[owner.key]=owner
    low,high=admissions
    if quota_blocked:store.set_quota('high',0)
    else:
        assert leases.acquire('held','local-compute','all')
        original=leases.acquire_for_job;released=[]
        def racing(*args,**kwargs):
            answer=original(*args,**kwargs)
            if not answer[0] and not released:
                leases.release('held');released.append(True)
            return answer
        monkeypatch.setattr(leases,'acquire_for_job',racing)
    try:
        with queue._LOCK:queue._dispatch(low)
        if quota_blocked:
            assert low.lease is not None and high.lease is None
            assert store.record('high')['wait_reason']=='project_quota'
        else:
            assert low.lease is None and high.lease is None,'Retry priority after capacity changes; never claim the lower job in that scan'
            with queue._LOCK:queue._dispatch(low)
            assert high.lease is not None and low.lease is None
    finally:
        for owner in admissions:owner.abandon(RuntimeError('Owned test cleanup'))
        leases.release('held')

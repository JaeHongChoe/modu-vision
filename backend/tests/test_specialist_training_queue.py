"""Native specialist submissions share durable admission and owned reservations."""
import json
from pathlib import Path
import threading
import time

import pytest

from backend.tests.test_specialist_runtime_limits import FAMILIES, prepared_client, await_terminal
from backend.engine.job_store import ledger
from backend.engine.shared_scheduler import shared_leases


@pytest.fixture(autouse=True)
def private_store(tmp_path, monkeypatch):
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path/'user'))


def submit(client, family, dataset, **options):
    body = {'dataset_path': dataset, 'epochs': 20, 'device': 'cpu', **options}
    if family != 'rotated-detection': body['background'] = True
    return client.post(f'/api/{family}/train', json=body)


@pytest.mark.parametrize('family,module,method', FAMILIES)
def test_queued_cancel_is_durable_and_never_enters_trainer(tmp_path, monkeypatch, family, module, method):
    client, project, _, dataset = prepared_client(tmp_path, family)
    called=[]
    monkeypatch.setattr(module, method, lambda *a, **k: called.append(True))
    leases=shared_leases()
    assert leases.acquire('other-owned-test', 'local-compute', 'all')
    try:
        response=submit(client, family, dataset, queue=True, priority=7, max_runtime_s=.05)
        assert response.status_code == (200 if family=='rotated-detection' else 202), response.text
        identifier=response.json()['job_id']
        time.sleep(.12)  # Waiting must not spend the execution budget.
        observed=client.get(f'/api/{family}/jobs/{identifier}').json()
        assert observed['status']=='queued', observed
        assert observed['priority']==7 and observed['queue_position']==1
        assert observed['wait_reason'] in {'device_reserved','external_reservation'}
        assert not observed.get('runtime_started_at')
        row=ledger().record(identifier)
        assert row['kind']=='specialist_training' and row['state']=='queued'
        assert json.loads(row['budget_json'])=={'max_runtime_s':.05}
        assert json.loads(row['spec_json'])['task']==family.replace('-','_')
        assert Path(row['output_dir']).parent==Path(project['models_dir'])/family.replace('-','_')
        cancelled=client.post(f'/api/{family}/jobs/{identifier}/cancel')
        assert cancelled.status_code==200, cancelled.text
        terminal=await_terminal(client,family,identifier)
        assert terminal['status'] in {'stopped','aborted'}, terminal
        assert ledger().get(identifier).state=='aborted' and ledger().cancel_intent(identifier)
        assert ledger().attempts(identifier)==[] and called==[]
        assert not Path(row['output_dir']).joinpath('best_model.pt').exists()
        assert any(r['job_id']=='other-owned-test' for r in leases.list())
    finally: leases.release('other-owned-test')


@pytest.mark.parametrize('family,module,method', FAMILIES)
def test_busy_refusal_and_idempotency_never_duplicate_execution(tmp_path, monkeypatch, family, module, method):
    client, project, _, dataset=prepared_client(tmp_path,family)
    called=[]
    monkeypatch.setattr(module,method,lambda *a,**k:called.append(True))
    leases=shared_leases();assert leases.acquire('held', 'local-compute', 'all')
    try:
        before=set(Path(project['models_dir']).rglob('job*.json'))
        refused=submit(client,family,dataset,queue=False)
        assert refused.status_code==409,refused.text
        assert before==set(Path(project['models_dir']).rglob('job*.json')) and called==[]
        body={'dataset_path':dataset,'epochs':20,'queue':True,'priority':3}
        if family!='rotated-detection':body['background']=True
        first=client.post(f'/api/{family}/train',json=body,headers={'Idempotency-Key':'one-native-request'})
        assert first.status_code in {200,202}, first.text
        second=client.post(f'/api/{family}/train',json=body,headers={'Idempotency-Key':'one-native-request'})
        assert second.status_code in {200,202},second.text
        assert second.json()['job_id']==first.json()['job_id'] and second.json()['idempotent_replay']
        conflict=client.post(f'/api/{family}/train',json={**body,'priority':4},headers={'Idempotency-Key':'one-native-request'})
        assert conflict.status_code==409 and called==[]
        client.post(f"/api/{family}/jobs/{first.json()['job_id']}/cancel")
        await_terminal(client,family,first.json()['job_id'])
    finally: leases.release('held')


def test_native_cohort_claims_priority_across_families_and_releases_after_exit(tmp_path,monkeypatch):
    clients=[];order=[];entered=threading.Event();release=threading.Event()
    from backend.engine.job_scheduler import JobScheduler
    trace=[];claim=JobScheduler.claim_job
    def traced(self,*args,**kwargs):
        before=[(r['id'],r['priority'],r['wait_reason']) for r in self.store.queued()]
        result=claim(self,*args,**kwargs)
        trace.append((before,args[1].get('job_ids'),result.job_id if result else None))
        return result
    monkeypatch.setattr(JobScheduler,'claim_job',traced)
    leases=shared_leases();assert leases.acquire('held','local-compute','all')
    def runner(tag):
        def execute(*args,cancel_event,**kwargs):
            order.append(tag);entered.set()
            assert release.wait(3)
            raise InterruptedError('Owned synthetic exit')
        return execute
    try:
        for family,module,method,priority in [('rotation',FAMILIES[0][1],FAMILIES[0][2],-3),('ocr',FAMILIES[1][1],FAMILIES[1][2],8)]:
            folder=tmp_path/family;folder.mkdir()
            client,_,_,dataset=prepared_client(folder,family)
            monkeypatch.setattr(module,method,runner(family))
            response=submit(client,family,dataset,queue=True,priority=priority)
            assert response.status_code==202,response.text
            clients.append((client,family,response.json()['job_id']))
        assert not entered.is_set(), {'order_before_release':order,'leases':leases.list()}
        from backend.engine import specialist_training_queue as queue
        queued_before=[(row['id'],row['priority'],row['state']) for row in ledger().queued()]
        assert len(queued_before)==2,queued_before
        assert all((str(ledger().path),identifier) in queue._READY for _,_,identifier in clients)
        leases.release('held')
        assert entered.wait(3) and order==['ocr'], json.dumps({'order':order,'queued_before':queued_before,'trace':trace[-8:]})
        assert ledger().get(clients[0][2]).state=='queued'
        release.set()
        for client,family,identifier in clients:await_terminal(client,family,identifier)
        assert order==['ocr','rotation']
        assert not leases.list()
        assert all(ledger().get(identifier).state=='aborted' for _,_,identifier in clients)
    finally:
        release.set();leases.release('held')


@pytest.mark.parametrize('family,module,method', FAMILIES)
def test_actual_cpu_completion_is_fenced_in_common_ledger(tmp_path,family,module,method):
    client,project,source,dataset=prepared_client(tmp_path,family)
    import hashlib
    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
    options={'epochs':1,'queue':False,'priority':2,'max_runtime_s':15}
    if family=='rotation':options.update(width=8,image_size=32,batch_size=2)
    if family=='ocr':options.update(image_height=32,image_width=64,batch_size=2)
    if family=='defect-gan':options.update(base_channels=8,batch_size=2)
    if family=='rotated-detection':options.update(image_size=32,batch_size=2)
    response=submit(client,family,dataset,**options)
    assert response.status_code in {200,202},response.text
    identifier=response.json()['job_id']
    terminal=await_terminal(client,family,identifier)
    assert terminal['status']=='completed',terminal
    store=ledger();assert store.get(identifier).state=='completed'
    attempts=store.attempts(identifier)
    assert len(attempts)==1 and attempts[0]['ended_ns'] is not None
    assert any(e['event']=='complete' for e in store.events(identifier))
    assert not any(r['job_id']==identifier for r in shared_leases().list())
    root=Path(project['models_dir'])/family.replace('-','_')/identifier
    receipt=json.loads((root/'job_receipt.json').read_text())
    assert receipt['checkpoint_sha256']==hashlib.sha256((root/'best_model.pt').read_bytes()).hexdigest()
    assert originals=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source.glob('*.png')}
    observed=client.get(f'/api/{family}/jobs/{identifier}').json()['observation']
    assert observed['execution_started'] is True and observed['cancel']['exit_confirmed'] is True
    assert observed['cancel']['reservation_released'] is True and observed['pending_finalization'] is False


def test_scheduling_controls_require_json_types_and_bounded_priority():
    from pydantic import ValidationError
    from backend.api import routes_rotation,routes_ocr,routes_defect_gan,routes_enhancement,routes_rotated_detection
    models=[routes_rotation.TrainRequest,routes_ocr.OCRTrainRequest,routes_defect_gan.GANTrainRequest,
            routes_enhancement.Train,routes_rotated_detection.TrainRequest]
    for model in models:
        for field,values in [('priority',[-11,11,True,'1',1.5]),('queue',[0,1,'true'])]:
            for value in values:
                with pytest.raises(ValidationError):model(dataset_path='owned',**{field:value})


def test_unlaunched_recovery_never_replays_a_lost_python_closure(tmp_path,monkeypatch):
    client,_,_,dataset=prepared_client(tmp_path,'rotation')
    from backend.engine import specialized_training_jobs as jobs
    original=jobs.threading.Thread.start
    # Suppress just the newly owned adapter thread; this models loss before claim.
    monkeypatch.setattr(jobs.threading.Thread,'start',lambda self:None if self.name.startswith('rotation-') else original(self))
    response=submit(client,'rotation',dataset,priority=9)
    identifier=response.json()['job_id']
    jobs._EVENTS.clear()
    observed=client.get(f'/api/rotation/jobs/{identifier}').json()
    assert observed['status']=='interrupted' and observed['ledger_state']=='interrupted'
    assert ledger().attempts(identifier)==[] and not shared_leases().list()


def test_stale_owner_cannot_publish_or_release_newer_attempt(tmp_path):
    from backend.engine.specialist_training_queue import NativeAdmission
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore,StaleFencingToken
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.contracts.context import ProjectContext
    store=JobStore(tmp_path/'ledger');leases=ResourceLeases(tmp_path/'leases')
    context=ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local')
    ref=store.submit(context,'p','specialist_training',{},job_id='native')
    scheduler=JobScheduler(store,leases);scheduler.enqueue(ref.id,ref.revision,resources={'host':'local-compute'})
    old=scheduler.claim_job('owned',{'hosts':['local-compute']})
    admission=NativeAdmission(store,scheduler,'native',tmp_path/'native','a',threading.Event());admission.lease=old
    store.transition('native',store.get('native').revision,'disconnect',fencing_token=old.fence)
    current=scheduler.reattach('native','owned-replacement')
    published=[]
    with pytest.raises(StaleFencingToken):admission.complete(lambda:published.append(True))
    with pytest.raises(StaleFencingToken):admission.finish_error(RuntimeError('old owner failed'))
    assert published==[] and store.get('native').state=='running'
    assert leases.list()[0]['fence']==current.fence
    scheduler.publish_result(current,'aborted')


def test_project_quota_blocks_native_runner_until_owned_cancel(tmp_path,monkeypatch):
    client,_,_,dataset=prepared_client(tmp_path,'rotation')
    from backend.api import routes_rotation
    calls=[];monkeypatch.setattr(routes_rotation,'train_rotation',lambda *a,**k:calls.append(True))
    leases=shared_leases();assert leases.acquire('held','local-compute','all')
    response=submit(client,'rotation',dataset);identifier=response.json()['job_id']
    store=ledger();store.set_quota(store.record(identifier)['project_key'],0)
    leases.release('held')
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        row=client.get(f'/api/rotation/jobs/{identifier}').json()
        if row.get('wait_reason')=='project_quota':break
        time.sleep(.01)
    assert row['status']=='queued' and row['wait_reason']=='project_quota' and not calls
    client.post(f'/api/rotation/jobs/{identifier}/cancel');await_terminal(client,'rotation',identifier)
    assert store.get(identifier).state=='aborted' and not store.attempts(identifier) and not leases.list()


def test_ledger_outage_refuses_before_journal_or_trainer(tmp_path,monkeypatch):
    client,project,_,dataset=prepared_client(tmp_path,'rotation')
    from backend.api import routes_rotation
    from backend.engine.job_store import JobStore
    calls=[];monkeypatch.setattr(routes_rotation,'train_rotation',lambda *a,**k:calls.append(True))
    def broken(*args,**kwargs):raise OSError('Controlled native admission outage')
    monkeypatch.setattr(JobStore,'submit',broken)
    response=submit(client,'rotation',dataset)
    assert response.status_code==422 and 'admission outage' in response.text
    assert not calls and list(Path(project['models_dir']).rglob('job*.json'))==[]


def test_process_creation_identity_recovers_only_a_confirmed_exited_owner(tmp_path,monkeypatch):
    from backend.engine.specialist_training_queue import NativeAdmission,interrupt_unlaunched
    from backend.engine.job_scheduler import JobScheduler
    from backend.contracts.context import ProjectContext
    store=ledger();leases=shared_leases();output=tmp_path/'dead-native'
    ref=store.submit(ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local'),
                     'p','specialist_training',{},job_id=output.name,output_dir=str(output))
    scheduler=JobScheduler(store,leases);scheduler.enqueue(ref.id,ref.revision,resources={'host':'local-compute'})
    lease=scheduler.claim_job('owned',{'hosts':['local-compute']})
    owner=NativeAdmission(store,scheduler,ref.id,output,'a',threading.Event());owner.lease=lease;owner.check()
    interrupt_unlaunched(output)
    assert store.get(ref.id).state=='running' and leases.list()  # same PID + creation time remains owned
    identity=dict(owner.identity);identity['owner_created_at']-=1  # prove this saved process has exited, even with PID reuse
    store.checkpoint(ref.id,identity,lease.fence)
    interrupt_unlaunched(output)
    assert store.get(ref.id).state=='interrupted' and not leases.list()


def test_owned_heartbeat_keeps_lease_valid_and_closes_after_completion(tmp_path,monkeypatch):
    from backend.engine.specialist_training_queue import NativeAdmission
    from backend.engine.job_scheduler import JobScheduler
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.contracts.context import ProjectContext
    # Observe actual renewal instead of relying on a 120ms wall-clock window
    # during a loaded hosted run. Expired/stale-fence cases are separate tests.
    store=JobStore(tmp_path/'ledger');leases=ResourceLeases(tmp_path/'leases',lease_seconds=5)
    ref=store.submit(ProjectContext(workspace_id='w',project_id='p',actor_id='a',mode='local'),'p','specialist_training',{},job_id='native')
    scheduler=JobScheduler(store,leases);scheduler.enqueue(ref.id,ref.revision,resources={'host':'local-compute'})
    owner=NativeAdmission(store,scheduler,ref.id,tmp_path/'native','a',threading.Event())
    renewed=threading.Event();observed=[];refresh=scheduler.heartbeat
    def trace(lease):
        result=refresh(lease);observed.append((lease,result,leases.list()[0]['expires'],time.time()))
        if len(observed)>=2:renewed.set()
        return result
    monkeypatch.setattr(scheduler,'heartbeat',trace)
    published=[]
    with owner.scope():
        assert renewed.wait(15), 'Owned worker did not renew its real ledger/resource lease twice'
        assert all(after.expires_at>before.expires_at and expiry>at for before,after,expiry,at in observed)
        assert leases.list()[0]['expires']>time.time() and store.get(ref.id).state=='running'
        owner.complete(lambda:published.append(True))
    assert published==[True] and store.get(ref.id).state=='completed' and not leases.list()
    assert not owner.event.wait(.15)
    assert not any(t.name==f'native-lease-{ref.id[:8]}' for t in threading.enumerate())


@pytest.mark.parametrize('family,module,method', FAMILIES)
def test_native_thread_start_failure_returns_claimed_reservation(tmp_path,monkeypatch,family,module,method):
    client,_,_,dataset=prepared_client(tmp_path,family)
    original=threading.Thread.start
    prefix=family.replace('-','_') if family in {'defect-gan'} else ('rotated' if family=='rotated-detection' else family)
    def broken(self):
        if self.name.startswith(prefix+'-'):raise RuntimeError('Controlled native thread launch failure')
        return original(self)
    monkeypatch.setattr(threading.Thread,'start',broken)
    try:response=submit(client,family,dataset,queue=False)
    except RuntimeError as exc:assert 'Controlled native thread' in str(exc)
    else:assert response.status_code==422 and 'Controlled native thread' in response.text
    assert not ledger().active('specialist_training') and not shared_leases().list()

"""Live same-installation cutover must not become a worker launch capability."""
import json
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from backend.tests.test_global_migration import owned


def test_cooperative_writer_waits_then_joins_the_shared_admission(tmp_path):
    from backend.engine.migration_guard import maintenance_guard
    entered = threading.Event()
    failures = []

    def writer():
        try:
            with maintenance_guard(tmp_path, wait=True):
                entered.set()
        except Exception as exc:
            failures.append(exc)

    with maintenance_guard(tmp_path, exclusive=True):
        thread = threading.Thread(target=writer)
        thread.start()
        assert not entered.wait(.1)
    thread.join(3)
    assert entered.is_set() and not failures and not thread.is_alive()


def test_live_preview_still_refuses_unknown_running_job_and_unfenced_orphan(tmp_path):
    from backend.engine.live_control_migration import preview_live, apply_live
    root, scopes, *_ = owned(tmp_path)
    with sqlite3.connect(root / scopes['ledger']) as db:
        db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES('foreign','w','p','p','a','local','training','h','{}','running',1,'fixture',1,1)")
    plan = preview_live(root)
    assert not plan['can_apply'] and plan['blockers']
    with pytest.raises(ValueError):
        apply_live(root, expected_source_sha256=plan['source_sha256'])
    assert not (root / 'global-active.json').exists()


def test_live_path_is_not_a_relaxed_drained_migration(tmp_path):
    from backend.engine.live_control_migration import preview_live
    root, *_ = owned(tmp_path)
    plan = preview_live(root)
    assert not plan['can_apply']
    assert any('live' in error.lower() for error in plan['blockers'])


def test_normal_cached_lease_handle_cannot_follow_a_drained_cutover(tmp_path):
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.global_migration import preview, apply
    root, scopes, *_ = owned(tmp_path)
    old = ResourceLeases(root / scopes['leases'], owner='old', cooperative=True)
    apply(root, expected_source_sha256=preview(root)['source_sha256'])
    with pytest.raises(ValueError, match='adopt|carry|generation'):
        old.list()


def test_waiting_writer_does_not_inherit_an_exclusive_capability(tmp_path):
    from backend.engine.migration_guard import maintenance_guard, exclusive_admitted
    observed = []
    with maintenance_guard(tmp_path, wait=True):
        observed.append(exclusive_admitted(tmp_path))
    assert observed == [False]


def _registered_job(tmp_path,monkeypatch):
    from backend.api.routes_training import TrainingLedgerLink
    root,scopes,store,registry,*_=owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    directory=root/'projects'/'live-project';models=directory/'models';models.mkdir(parents=True)
    project={'id':'live-project','workspace_id':registry.workspace_id,'project_dir':str(directory),'models_dir':str(models)}
    (directory/'project.json').write_text(json.dumps(project))
    context=registry.context(project,None);key=registry.project_key(context);identifier='job_live'
    output=models/identifier
    store.submit(context,key,'training',{'task':'classification'},job_id=identifier,output_dir=str(output),
                 registry_root=str(root/'projects'),project_dir=str(directory))
    link=TrainingLedgerLink(store,identifier);link.queued()
    return root,scopes,store,link,output,directory


@pytest.fixture
def controlled_live(tmp_path,monkeypatch):
    """Controlled identity observation for failure matrix; not a real worker."""
    import hashlib
    import os
    import psutil
    from backend.engine import local_training_worker as worker
    from backend.engine.shared_scheduler import ResourceLeases
    root,scopes,store,link,output,directory=_registered_job(tmp_path,monkeypatch)
    link.launched('local');output.mkdir()
    leases=ResourceLeases(root/scopes['leases'],owner='controlled-owner',cooperative=True)
    assert leases.acquire('job_live','local-compute','all')
    assert leases.stamp_fence('job_live',link.fencing_token)
    spec={'protocol_version':1,'global_control_protocol':1,'job_id':'job_live','task':'classification','preset':'fast',
          'output_dir':str(output),'lease_path':str(root/scopes['leases']),'lease_owner':leases.owner}
    path=output/'local_spec.json';path.write_text(json.dumps(spec))
    process=psutil.Process(os.getpid())
    journal={'protocol_version':1,'global_control_protocol':1,'job_id':'job_live','task':'classification','preset':'fast',
        'status':'launching','output_dir':str(output),'spec_path':str(path),
        'spec_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'owner_pid':process.pid,
        'owner_created_at':process.create_time(),'owner_command_sha256':'c'*64,'owner_token':'d'*32}
    from backend.engine.global_store_paths import owned_root
    _,owner=owned_root(root)
    ready={key:journal[key] for key in ('job_id','spec_sha256','owner_pid','owner_created_at','owner_command_sha256')}
    ready.update(protocol_version=1,installation_id=owner['installation_id'],lease_owner=leases.owner)
    (output/'local_control_ready.json').write_text(json.dumps(ready))
    index=root/scopes['local_journals'];index.mkdir()
    def save():
        raw=json.dumps(journal).encode();(output/'local_job.json').write_bytes(raw);(index/'job_live.json').write_bytes(raw)
    save()
    monkeypatch.setattr(worker,'_owned',lambda row:process if row['owner_pid']==process.pid and row['owner_created_at']==process.create_time() else None)
    monkeypatch.setattr(worker,'_owned_members',lambda row:[process] if row['owner_token']=='d'*32 else None)
    return root,scopes,store,link,leases,output,journal,save


@pytest.mark.parametrize('damage',['old_protocol','spec_changed','foreign_lease','foreign_actor','pid_reused','token_changed',
    'journal_copy','orphan','missing_attempt','wrong_fence','unknown_index','noncanonical_id','missing_ack','foreign_ack','paired_app'])
def test_controlled_identity_matrix_refuses_without_activation(controlled_live,damage):
    from backend.engine.live_control_migration import preview_live,apply_live
    root,scopes,store,link,leases,output,journal,save=controlled_live
    if damage=='old_protocol':journal['global_control_protocol']=None;save()
    elif damage=='spec_changed':(output/'local_spec.json').write_text('{}')
    elif damage=='foreign_lease':
        with leases.connect() as db:db.execute("UPDATE leases SET owner='other'")
    elif damage=='foreign_actor':
        with store._tx() as db:db.execute("UPDATE jobs SET actor_id='other'")
    elif damage=='pid_reused':journal['owner_created_at']-=100;save()
    elif damage=='token_changed':journal['owner_token']='e'*32;save()
    elif damage=='journal_copy':(output/'local_job.json').write_text('{}')
    elif damage=='orphan':
        with leases.connect() as db:db.execute("UPDATE leases SET job_id='job_orphan'")
    elif damage=='missing_attempt':
        with store._tx() as db:db.execute('DELETE FROM attempts')
    elif damage=='wrong_fence':
        with leases.connect() as db:db.execute('UPDATE leases SET fence=987')
    elif damage=='unknown_index':(root/scopes['local_journals']/'job_unknown.json').write_text('{}')
    elif damage=='missing_ack':(output/'local_control_ready.json').unlink()
    elif damage=='foreign_ack':
        path=output/'local_control_ready.json';ready=json.loads(path.read_bytes());ready['installation_id']='b'*32;path.write_text(json.dumps(ready))
    elif damage=='paired_app':(root/'application-active.json').write_text('{}')
    else:
        # Deliberately corrupt an offline source; the normal writer correctly
        # rejects this foreign-key-breaking fixture mutation.
        with sqlite3.connect(root/scopes['ledger']) as db:db.execute("UPDATE jobs SET id='bad/name'")
    view=preview_live(root);assert not view['can_apply'] and view['blockers']
    with pytest.raises(ValueError):apply_live(root,expected_source_sha256=view['source_sha256'])
    assert not (root/'global-active.json').exists() and not (root/'.global-generations').exists()


def test_controlled_stale_review_and_original_observer_fencing(controlled_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.job_store import JobStore,StaleFencingToken
    root,scopes,store,link,leases,*_=controlled_live
    view=preview_live(root)
    with leases.connect() as db:db.execute('UPDATE leases SET expires=expires+1')
    with pytest.raises(ValueError,match='changed'):apply_live(root,expected_source_sha256=view['source_sha256'])
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    link.heartbeat(30)
    fresh=JobStore(root/scopes['ledger'])
    new_fence=fresh.reattach('job_live',worker_id='new-observer',lease_seconds=30)
    assert new_fence>link.fencing_token
    with pytest.raises(StaleFencingToken):link.store.heartbeat('job_live',link.fencing_token,30)
    assert len(fresh.attempts('job_live'))==1 and fresh.get('job_live').state=='running'


@pytest.mark.parametrize('after_pointer',[False,True])
def test_controlled_prepared_crash_finishes_without_replaying_current_writes(controlled_live,monkeypatch,after_pointer):
    from backend.engine import live_control_migration as live
    from backend.engine.global_migration import recover
    from backend.engine.job_store import JobStore
    root,scopes,*_=controlled_live
    publish=live._publish;write=live.atomic_private_json
    if after_pointer:
        def fail(path,value):
            if path.name=='journal.json' and value.get('status')=='applied':raise OSError('controlled receipt failure')
            return write(path,value)
        monkeypatch.setattr(live,'atomic_private_json',fail)
    else:monkeypatch.setattr(live,'_publish',lambda *a,**k:(_ for _ in ()).throw(OSError('controlled pointer failure')))
    with pytest.raises(OSError,match='controlled'):live.apply_live(root,expected_source_sha256=live.preview_live(root)['source_sha256'])
    record=next((root/'.global-migrations').glob('*/journal.json'));identifier=record.parent.name
    monkeypatch.setattr(live,'_publish',publish);monkeypatch.setattr(live,'atomic_private_json',write)
    if after_pointer:JobStore(root/scopes['ledger']).record_event('job_live','post_cutover',{'preserve':True})
    assert recover(root,identifier,action='finish')['current_writes_preserved']
    if after_pointer:assert JobStore(root/scopes['ledger']).events('job_live')[-1]['event']=='post_cutover'
    assert recover(root,identifier,action='finish')['status']=='applied'
    with pytest.raises(ValueError,match='forward'):recover(root,identifier,action='restore')


def test_controlled_corrupted_adoption_cannot_refresh_old_handles(controlled_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.global_store_paths import active_generation
    root,scopes,store,link,leases,*_=controlled_live
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    generation,_=active_generation(root);path=generation/'.global-generation.json';seal=json.loads(path.read_bytes())
    seal['live_adoptions']['workers'][0]['lease_owner']='other';path.write_text(json.dumps(seal))
    with pytest.raises(ValueError,match='adoption'):leases.list()
    with pytest.raises(ValueError,match='adoption'):link.store.get('job_live')


def test_carried_lease_handle_only_continues_its_reservation_not_new_execution(controlled_live):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.shared_scheduler import ResourceLeases
    root,scopes,store,link,leases,*_=controlled_live
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    assert leases.heartbeat('job_live')
    with pytest.raises(ValueError,match='restart|continuation'):
        leases.acquire('job_new','unused-host','all')
    with pytest.raises(ValueError,match='restart|continuation'):
        leases.restamp_fence('job_live',987)
    assert leases.list()[0]['fence']==link.fencing_token
    assert leases.release('job_live',terminal=True)
    with pytest.raises(ValueError,match='restart|continuation'):
        leases.acquire_for_job('job_live','local-compute','all')
    fresh=ResourceLeases(root/scopes['leases'],owner='fresh-backend',cooperative=True)
    assert fresh.acquire('job_new','unused-host','all')
    assert fresh.release('job_new',terminal=True)


@pytest.mark.skipif(__import__('os').name=='nt',reason='Owned POSIX migration; Windows real QA waived')
def test_actual_cpu_worker_finishes_once_across_live_cutover(tmp_path,monkeypatch):
    import hashlib
    import os
    import psutil
    from PIL import Image
    from backend.api.routes_training import TrainingJobManager
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.global_migration import recover
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    root,scopes,original_store,link,output,directory=_registered_job(tmp_path,monkeypatch)
    monkeypatch.setenv('OMP_NUM_THREADS','1');monkeypatch.setenv('MKL_NUM_THREADS','1')
    source=directory/'dataset'
    for split in ('train','val'):
        for label in ('OK','NG'):
            folder=source/split/label;folder.mkdir(parents=True)
            for i in range(12):Image.new('RGB',(64,64),'red' if label=='NG' else 'blue').save(folder/(str(i)+'.png'))
    source_hashes={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.png')}
    manager=TrainingJobManager()
    record=manager.start_job('job_live','classification',str(source),str(output),device='cpu',ledger=link,
        config_overrides={'pretrained':False,'backbone':'resnet18','epochs':12,'image_size':64,'batch_size':8,'num_workers':0,'early_stopping_patience':30})
    try:
        deadline=time.monotonic()+20
        while not (output/'local_control_ready.json').is_file() and time.monotonic()<deadline:time.sleep(.02)
        journal=json.loads((output/'local_job.json').read_bytes())
        pid,created=journal['owner_pid'],journal['owner_created_at']
        assert pid!=os.getpid() and record.process.poll() is None
        # An uncertain reservation is not converted into a new execution grant.
        manager._leases.mark_uncertain(record.job_id)
        plan=preview_live(root);assert plan['can_apply'],plan['blockers']
        fence=link.fencing_token
        result=apply_live(root,expected_source_sha256=plan['source_sha256'])
        assert result['workers_relaunched']==result['uncertain_reservations_cleared']==0
        assert record.process.pid==pid and psutil.Process(pid).create_time()==created
        carried=manager._leases.list()
        assert len(carried)==1 and carried[0]['fence']==fence and carried[0]['uncertain']==1
        assert ResourceLeases(root/scopes['leases'],owner='another').acquire('job_duplicate','local-compute','all') is False
        old_controls={p.relative_to(root).as_posix():p.read_bytes() for p in (root/scopes['ledger'],root/scopes['leases'],root/scopes['local_journals']/'job_live.json')}
        record.thread.join(60)
        assert not record.thread.is_alive() and record.status=='completed',record.error
        fresh=JobStore(root/scopes['ledger']);assert fresh.get('job_live').state=='completed'
        attempts=fresh.attempts('job_live');assert len(attempts)==1 and attempts[0]['fencing_token']==fence and attempts[0]['ended_ns']
        assert manager._leases.list()==[]
        ended=json.loads((resolve_store_path(root/scopes['local_journals'])/'job_live.json').read_bytes())
        assert ended['owner_pid']==pid and ended['worker_exit_confirmed'] is True and ended['status']=='completed'
        assert all((root/path).read_bytes()==raw for path,raw in old_controls.items())
        assert source_hashes=={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.png')}
        assert recover(root,result['migration_id'],action='finish')['current_writes_preserved']
        with pytest.raises(ValueError,match='restored|replayed|forward'):recover(root,result['migration_id'],action='restore')
        with pytest.raises(ValueError,match='restart|generation'):original_store.get('job_live')
    finally:
        if record.thread.is_alive():
            manager.abort_job(record.job_id);record.thread.join(20)

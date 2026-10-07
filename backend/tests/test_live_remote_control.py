"""Controlled SSH observations exercise authority boundaries, not real GPU QA."""
import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from backend.tests.test_live_control_migration import _registered_job

pytestmark=pytest.mark.skipif(os.name=='nt',reason='Owned POSIX live protocol; actual Windows QA is waived')


@pytest.fixture
def remote_control(tmp_path,monkeypatch):
    from backend.engine.live_remote_control import bind_remote_control,acknowledge_remote_control
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.coordinator import _save_journal
    from backend.remote.profiles import ComputeProfile,ProfileStore
    from backend.remote.ssh_transport import SSHTransport
    root,scopes,store,link,output,directory=_registered_job(tmp_path,monkeypatch)
    link.launched('remote');output.mkdir()
    profile=ComputeProfile(id='owned-remote',name='Controlled remote',ssh_target='fixture.invalid',ssh_port=22,
        remote_root='/owned/workspace',runtime_kind='docker',runtime_value='owned-worker:fixture',gpu_selector='2')
    ProfileStore(root/scopes['profiles']).save(profile)
    leases=ResourceLeases(root/scopes['leases'],owner='remote-original',cooperative=True)
    assert leases.acquire('job_live','ssh:fixture.invalid:22','2',remote=True)
    record=SimpleNamespace(job_id='job_live',output_dir=str(output),ledger=link,remote_profile=profile)
    binding=bind_remote_control(record,leases);assert binding is not None
    spec={'protocol_version':1,'job_id':'job_live','operation':'train','task':'classification','preset':'fast'}
    spec_path=output/'remote_spec.json';spec_path.write_text(json.dumps(spec))
    journal={'protocol_version':1,'global_control_protocol':1,'job_id':'job_live','operation':'train',
        'state':'launched','task':'classification','preset':'fast','output_dir':str(output),
        'profile':profile.model_dump(),'remote_handle':'a'*64,'control_owner':binding,
        'transfers':[{'source':str(spec_path),'target':'spec.json','size':spec_path.stat().st_size,
                      'sha256':hashlib.sha256(spec_path.read_bytes()).hexdigest()}]}
    acknowledge_remote_control(journal);_save_journal(journal)
    calls=[]
    def recover(self,selected,run_id,**kwargs):
        calls.append(('recover',selected,run_id,kwargs));return journal['remote_handle']
    def running(self,selected,run_id,handle):
        calls.append(('running',selected,run_id,handle));return True
    monkeypatch.setattr(SSHTransport,'recover_handle',recover)
    monkeypatch.setattr(SSHTransport,'is_running',running)
    def unproven_inspect(self,*args,**kwargs):
        import subprocess
        return subprocess.CompletedProcess([],1,'','Controlled unknown container identity')
    monkeypatch.setattr(SSHTransport,'exec',unproven_inspect)
    return root,scopes,store,link,leases,output,journal,calls,record


def test_remote_cutover_carries_only_original_fenced_observer_and_reservation(remote_control):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.job_store import JobStore
    from backend.remote.coordinator import _save_journal
    root,scopes,store,link,leases,output,journal,calls,record=remote_control
    leases.mark_uncertain('job_live')
    plan=preview_live(root);assert plan['can_apply'],plan['blockers']
    row=plan['live_adoptions']['workers'][0]
    assert plan['live_adoptions']['protocol_version']==2 and row['worker_kind']=='remote_training'
    assert row['reserved'] and row['uncertain']
    result=apply_live(root,expected_source_sha256=plan['source_sha256'])
    assert result['workers_relaunched']==result['uncertain_reservations_cleared']==0
    originals={name:(root/name).read_bytes() for name in (scopes['ledger'],scopes['leases'],scopes['remote_journals']+'/job_live.json')}
    link.heartbeat(30);link.store.checkpoint('job_live',{'remote_step':2},fencing_token=link.fencing_token)
    journal['remote_step']=2;_save_journal(journal);assert leases.heartbeat('job_live')
    assert leases.list()[0]['uncertain']==1
    assert JobStore(root/scopes['ledger']).checkpoint_value('job_live')=={'remote_step':2}
    assert json.loads((resolve_store_path(root/scopes['remote_journals'])/'job_live.json').read_text())['remote_step']==2
    assert (output/'remote_job.json').read_bytes()==(resolve_store_path(root/scopes['remote_journals'])/'job_live.json').read_bytes()
    assert all((root/name).read_bytes()==raw for name,raw in originals.items())
    with pytest.raises(ValueError,match='continuation|restart'):leases.acquire('job_new','another-host','all',remote=True)
    assert len(JobStore(root/scopes['ledger']).attempts('job_live'))==1
    assert calls and {c[0] for c in calls}=={'recover','running'}


@pytest.mark.parametrize('damage',['old_protocol','missing_ack','spec_changed','profile_changed','foreign_owner',
    'wrong_fence','wrong_host','lost_reservation','owner_pid_reused','command_changed','journal_copy','foreign_actor',
    'remote_identity','remote_uncertain','remote_exited','attempt_replaced','unknown_remote_index'])
def test_remote_preview_refuses_unproven_or_changed_authority_before_activation(remote_control,monkeypatch,damage):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.remote.coordinator import _save_journal
    from backend.remote.ssh_transport import SSHTransport
    root,scopes,store,link,leases,output,journal,calls,record=remote_control
    if damage=='old_protocol':journal['global_control_protocol']=None;_save_journal(journal)
    elif damage=='missing_ack':(output/'remote_control_ready.json').unlink()
    elif damage=='spec_changed':(output/'remote_spec.json').write_text('{}')
    elif damage=='profile_changed':journal['profile']['remote_root']='/other/workspace';_save_journal(journal)
    elif damage in {'foreign_owner','wrong_fence','wrong_host','lost_reservation'}:
        with leases.connect() as db:
            db.execute({'foreign_owner':"UPDATE leases SET owner='foreign'",'wrong_fence':'UPDATE leases SET fence=999',
                'wrong_host':"UPDATE leases SET host='ssh:other:22'",'lost_reservation':'DELETE FROM leases'}[damage])
    elif damage in {'owner_pid_reused','command_changed'}:
        journal['control_owner']['owner_created_at' if damage=='owner_pid_reused' else 'owner_command_sha256']=1 if damage=='owner_pid_reused' else 'f'*64
        _save_journal(journal)
    elif damage=='journal_copy':(output/'remote_job.json').write_text('{}')
    elif damage=='foreign_actor':
        with store._tx() as db:db.execute("UPDATE jobs SET actor_id='foreign'")
    elif damage=='remote_identity':monkeypatch.setattr(SSHTransport,'recover_handle',lambda *a,**k:'b'*64)
    elif damage in {'remote_uncertain','remote_exited'}:monkeypatch.setattr(SSHTransport,'is_running',lambda *a,**k:None if damage=='remote_uncertain' else False)
    elif damage=='attempt_replaced':store.reattach('job_live',worker_id='replacement',lease_seconds=30)
    else:(root/scopes['remote_journals']/'job_unknown.json').write_text('{}')
    plan=preview_live(root);assert not plan['can_apply'] and plan['blockers']
    with pytest.raises(ValueError):apply_live(root,expected_source_sha256=plan['source_sha256'])
    assert not (root/'global-active.json').exists() and not (root/'.global-generations').exists()


def test_old_remote_observer_cannot_write_a_journal_after_new_backend_reattachment(remote_control):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.job_store import JobStore
    from backend.remote.coordinator import _save_journal
    root,scopes,store,link,leases,output,journal,*_=remote_control
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    JobStore(root/scopes['ledger']).reattach('job_live',worker_id='replacement',lease_seconds=30)
    before=(output/'remote_job.json').read_bytes()
    with pytest.raises(ValueError,match='fence|attempt'):_save_journal(journal)
    assert (output/'remote_job.json').read_bytes()==before


def test_new_remote_launch_cannot_opt_in_to_an_existing_live_generation(remote_control):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.live_remote_control import bind_remote_control
    root,scopes,store,link,leases,output,journal,calls,record=remote_control
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    assert bind_remote_control(record,leases) is None


def test_remote_adoption_descriptor_cannot_invent_unknown_control_kind(remote_control):
    from backend.engine.live_control_migration import preview_live,validate_adoptions
    root,*_=remote_control
    adoption=preview_live(root)['live_adoptions'];adoption['workers'][0]['worker_kind']='unknown'
    with pytest.raises(ValueError):validate_adoptions(adoption,adoption['installation_id'])


def test_fresh_backend_reattachment_can_continue_same_worker_without_replacing_ack(remote_control):
    from backend.engine.live_control_migration import preview_live,apply_live
    from backend.engine.live_remote_control import bind_remote_recovery
    from backend.engine.job_store import JobStore
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.api.routes_training import TrainingLedgerLink
    from backend.remote.coordinator import _save_journal
    root,scopes,store,link,leases,output,journal,calls,record=remote_control
    apply_live(root,expected_source_sha256=preview_live(root)['source_sha256'])
    original_ack=(output/'remote_control_ready.json').read_bytes();original_journal=json.loads(json.dumps(journal))
    fresh_store=JobStore(root/scopes['ledger'])
    fresh_fence=fresh_store.reattach('job_live',worker_id='pid:'+str(os.getpid()),lease_seconds=30)
    fresh_link=TrainingLedgerLink(fresh_store,'job_live');fresh_link.fencing_token=fresh_fence
    fresh_leases=ResourceLeases(root/scopes['leases'],owner='fresh-observer',cooperative=True)
    with fresh_leases.connect() as db:db.execute('UPDATE leases SET expires=0,uncertain=1')
    assert fresh_leases.adopt('job_live')
    fresh_record=SimpleNamespace(job_id='job_live',output_dir=str(output),ledger=fresh_link,remote_profile=record.remote_profile)
    binding=bind_remote_recovery(fresh_record,fresh_leases);assert binding['attempt_fence']==fresh_fence
    journal['control_owner']=binding;journal['reattached_progress']=3;_save_journal(journal)
    assert fresh_leases.list()[0]['uncertain']==1
    assert (output/'remote_control_ready.json').read_bytes()==original_ack
    with pytest.raises(ValueError,match='attempt|fence'):_save_journal(original_journal)
    assert json.loads((output/'remote_job.json').read_bytes())['reattached_progress']==3
    assert len(fresh_store.attempts('job_live'))==1


def test_docker_short_recovery_id_must_resolve_to_same_actual_container_and_run(remote_control,monkeypatch):
    import subprocess
    from backend.engine.live_control_migration import preview_live
    from backend.remote.ssh_transport import SSHTransport
    root,scopes,store,link,leases,output,journal,calls,*_=remote_control
    monkeypatch.setattr(SSHTransport,'recover_handle',lambda *a,**k:'a'*12)
    def inspect(self,profile,argv,**kwargs):
        assert argv==['docker','container','inspect','--format','{{.Id}} {{.Name}}','a'*12,'a'*64]
        return subprocess.CompletedProcess(argv,0,('a'*64+' /modu-vision-job_live\n')*2,'')
    monkeypatch.setattr(SSHTransport,'exec',inspect)
    plan=preview_live(root);assert plan['can_apply'],plan['blockers']
    monkeypatch.setattr(SSHTransport,'exec',lambda *a,**k:subprocess.CompletedProcess([],0,
        'a'*64+' /modu-vision-job_live\n'+'b'*64+' /modu-vision-other\n',''))
    assert not preview_live(root)['can_apply']

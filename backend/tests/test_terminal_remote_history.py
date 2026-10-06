"""Owned ended remote history is archival control, never SSH launch authority."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from backend.tests.test_historical_job_binding import history


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def remote_history(tmp_path, state='completed'):
    root,scopes,ledger,registry,key,old,output=history(tmp_path)
    with ledger._tx() as db:
        for table in ['events','migrations','jobs']:db.execute('DELETE FROM '+table)
    old.unlink()
    profile=dict(id='archived-worker',name='Archived controlled worker',ssh_target='worker.invalid',ssh_port=22,
        remote_root='/owned/history',runtime_kind='python',runtime_value='/usr/bin/python3')
    spec=dict(protocol_version=1,job_id='job_legacy',operation='train',task='classification',preset='controlled',input_manifest_sha256='a'*64)
    path=output/'remote_spec.json';path.write_text(json.dumps(spec))
    journal=dict(protocol_version=1,job_id='job_legacy',operation='train',task='classification',preset='controlled',
        state=state,worker_terminal_state=state,worker_exit_confirmed=True,output_dir=str(output),dataset_path=str(output.parent.parent),
        profile=profile,remote_handle='123:'+uuid.uuid4().hex,launch_spec={'operation':'train'},input_manifest_sha256='a'*64,
        transfers=[{'source':str(path),'target':'spec.json','size':path.stat().st_size,'sha256':digest(path)}])
    checkpoint=output/'best_model.pt';checkpoint.write_bytes(b'controlled received model bytes, not a learned model')
    metadata=output/'model_meta.json';metadata.write_text(json.dumps({'task':'classification'}))
    (output/'remote_artifacts.json').write_text(json.dumps(dict(protocol_version=1,job_id='job_legacy',operation='train',
        input_manifest_sha256='a'*64,artifacts=[{'path':'outputs/'+f.name,'size':f.stat().st_size,'sha256':digest(f)} for f in [checkpoint,metadata]])))
    (output/'job_receipt.json').write_text(json.dumps(dict(job_id='job_legacy',status=state,task='classification',output_dir=str(output),
        compute_profile_id=profile['id'],checkpoint_sha256=digest(checkpoint))))
    index=root/scopes['remote_journals'];index.mkdir();source=index/'job_legacy.json'
    raw=json.dumps(journal).encode();source.write_bytes(raw);(output/'remote_job.json').write_bytes(raw)
    ledger.migrate_legacy([index],root/'remote-import-receipt.json')
    return root,scopes,ledger,registry,key,source,output,journal


@pytest.mark.parametrize('state',['completed','failed','aborted'])
def test_ended_remote_history_survives_cutover_forward_and_fresh_readback_without_a_connection(tmp_path,monkeypatch,state):
    from backend.engine import historical_job_binding as binding,global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.job_store import JobStore
    from backend.remote.coordinator import recover_remote_jobs
    from backend.remote.ssh_transport import SSHTransport
    root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path,state)
    before={p:digest(p) for p in [source,*output.iterdir()] if p.is_file()}
    monkeypatch.setattr(SSHTransport,'exec',lambda *_a,**_k:pytest.fail('Archival migration must not connect'))
    plan=binding.preview(root);assert plan['can_apply'],plan['blockers']
    bound=binding.apply(root,expected_preview_sha256=plan['preview_sha256'],reason='Reviewed original ended remote history')
    assert bound['worker_authority_created'] is False
    first=migration.preview(root);assert first['can_apply'],first['blockers'];migration.apply(root,expected_source_sha256=first['source_sha256'])
    first_index=resolve_store_path(root/scopes['remote_journals']);assert (first_index/source.name).read_bytes()==source.read_bytes()
    forward=migration.preview_forward(root);assert forward['can_apply'],forward['blockers'];migration.advance(root,expected_source_sha256=forward['source_sha256'])
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root));restored=[]
    manager=SimpleNamespace(get_job=lambda _:None,restore_terminal_job=restored.append,
        start_remote_job=lambda **_:pytest.fail('Ended remote history must never launch'))
    recover_remote_jobs(manager)
    assert len(restored)==1 and restored[0].job_id=='job_legacy' and restored[0].status==state
    assert restored[0].remote_profile_id=='archived-worker'
    assert ResourceLeases(root/scopes['leases']).list()==[] and JobStore(root/scopes['ledger']).attempts('job_legacy')==[]
    assert all(digest(p)==sha for p,sha in before.items())


@pytest.mark.parametrize('damage',['unconfirmed','missing_handle','spec_changed','run_copy_changed','profile_invalid','checkpoint_changed','receipt_changed','uncertain_lease',
    'duplicate_artifact','untyped_artifact','extra_artifact','linked_checkpoint','duplicate_spec_key','relocation','changed_worker_state','foreign_operation'])
def test_remote_history_refuses_incomplete_or_changed_original_proof_without_binding(tmp_path,damage):
    from backend.engine.historical_job_binding import preview
    from backend.engine.job_store import spec_digest
    from backend.engine.shared_scheduler import ResourceLeases
    root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path)
    if damage in ['unconfirmed','missing_handle','profile_invalid','changed_worker_state','foreign_operation']:
        if damage=='unconfirmed':journal['worker_exit_confirmed']=False
        elif damage=='missing_handle':journal.pop('remote_handle')
        elif damage=='profile_invalid':journal['profile']['ssh_target']='other@host;echo unsafe'
        elif damage=='changed_worker_state':journal['worker_terminal_state']='running'
        else:journal['operation']='label'
        raw=json.dumps(journal).encode();source.write_bytes(raw);(output/'remote_job.json').write_bytes(raw)
        spec={'legacy_source':str(source),'legacy_sha256':digest(source)}
        with ledger._tx() as db:db.execute('UPDATE jobs SET spec_json=?,spec_sha256=?',(json.dumps(spec),spec_digest(spec)))
    elif damage=='spec_changed':(output/'remote_spec.json').write_text('{}')
    elif damage=='run_copy_changed':(output/'remote_job.json').write_text('{}')
    elif damage=='checkpoint_changed':(output/'best_model.pt').write_bytes(b'changed')
    elif damage=='receipt_changed':(output/'job_receipt.json').write_text('{}')
    elif damage=='duplicate_spec_key':(output/'remote_spec.json').write_text('{"job_id":"job_legacy","job_id":"job_other"}')
    elif damage=='linked_checkpoint':
        checkpoint=output/'best_model.pt';original=output/'linked-original.pt';checkpoint.rename(original);checkpoint.symlink_to(original)
    elif damage in ['duplicate_artifact','untyped_artifact','extra_artifact','relocation']:
        path=output/'remote_artifacts.json';manifest=json.loads(path.read_text())
        if damage=='duplicate_artifact':manifest['artifacts'][1]=dict(manifest['artifacts'][0])
        elif damage=='untyped_artifact':manifest['artifacts'][1]['path']={}
        elif damage=='extra_artifact':manifest['artifacts'].append(dict(manifest['artifacts'][0],path='outputs/unreceived.bin'))
        else:manifest['relocation']={'local_dataset_root':'other'}
        path.write_text(json.dumps(manifest))
    else:
        leases=ResourceLeases(root/scopes['leases']);assert leases.acquire('job_legacy','archived-worker','all',remote=True);leases.mark_uncertain('job_legacy')
    previous=ledger.record('job_legacy');plan=preview(root)
    assert not plan['can_apply'] and plan['blockers'] and ledger.record('job_legacy')==previous


def test_changed_remote_source_after_review_cannot_bind_or_cut_over(tmp_path):
    from backend.engine import historical_job_binding as binding,global_migration as migration
    root,scopes,ledger,registry,key,source,output,journal=remote_history(tmp_path)
    reviewed=binding.preview(root);before=ledger.record('job_legacy')
    (output/'model_meta.json').write_text('{"task":"detection"}')
    with pytest.raises(ValueError,match='changed since preview'):
        binding.apply(root,expected_preview_sha256=reviewed['preview_sha256'],reason='Previously reviewed remote history')
    assert ledger.record('job_legacy')==before and not migration.preview(root)['can_apply']


def test_production_coordinator_and_receipt_writer_emit_migratable_archival_records(tmp_path,monkeypatch):
    """Real producer/schema/copy paths, controlled SSH transport; no GPU claim."""
    from backend.api.routes_training import JobRecord,_write_job_receipt
    from backend.engine import historical_job_binding as binding,global_migration as migration
    from backend.remote.coordinator import persist_queued_remote_job,run_remote_training
    from backend.remote import coordinator
    from backend.remote.profiles import ComputeProfile
    from backend.tests.test_remote_coordinator import FakeRemote
    root,scopes,ledger,registry,key,old,output=history(tmp_path)
    with ledger._tx() as db:
        for table in ['events','migrations','jobs']:db.execute('DELETE FROM '+table)
    old.unlink();monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    foundation=root/'controlled-foundation.safetensors';foundation.write_bytes(b'controlled transfer bytes; not executable weights')
    monkeypatch.setattr(coordinator,'_local_pretrained_weights',lambda *_args:(foundation,digest(foundation),'controlled fixture','dinov3_vits16'))
    source=output.parent.parent/'training';source.mkdir();(source/'controlled.png').write_bytes(b'controlled transport input; not an image')
    profile=ComputeProfile(id='archived-coordinator',name='Controlled coordinator transport',ssh_target='worker.invalid',ssh_port=22,
        remote_root=str(root/'controlled-server'),runtime_kind='python',runtime_value='/usr/bin/python3')
    launch={'operation':'train'}
    record=JobRecord(job_id='job_legacy',task='segmentation',preset='fast',dataset_path=str(source),output_dir=str(output),
        status='running',remote_profile_id=profile.id,launch_spec=launch)
    class OwnedTransport(FakeRemote):
        def launch(self,*args):
            super().launch(*args)
            return '123:'+uuid.uuid4().hex
    persist_queued_remote_job(record,profile,launch)
    result=run_remote_training(record,profile,transport=OwnedTransport(Path(profile.remote_root)))
    assert result['status']=='completed' and result['worker_exit_confirmed']
    record.status='completed';_write_job_receipt(record)
    index=root/scopes['remote_journals'];ledger.migrate_legacy([index],root/'producer-import.json')
    plan=binding.preview(root);assert plan['can_apply'],plan['blockers']
    receipt=binding.apply(root,expected_preview_sha256=plan['preview_sha256'],reason='Reviewed production-format terminal remote history')
    assert receipt['worker_authority_created'] is False
    cutover=migration.preview(root);assert cutover['can_apply'],cutover['blockers']
    migration.apply(root,expected_source_sha256=cutover['source_sha256'])
    assert migration.preview_forward(root)['can_apply']

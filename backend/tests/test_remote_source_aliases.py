from pathlib import Path
import pytest


def test_source_alias_is_request_scoped_and_does_not_rewrite_manifest_bytes(tmp_path):
    from backend.engine.source_aliases import source_alias_scope,resolve_source_root
    source=tmp_path/'desktop';remote=tmp_path/'remote';remote.mkdir()
    assert resolve_source_root(source)==source.resolve()
    with source_alias_scope({str(source):remote}):
        assert resolve_source_root(source)==remote.resolve()
        with source_alias_scope({}):assert resolve_source_root(source)==source.resolve()
        assert resolve_source_root(source)==remote.resolve()
    assert resolve_source_root(source)==source.resolve()


def test_alias_rejects_links_or_missing_snapshot_roots(tmp_path):
    from backend.engine.source_aliases import source_alias_scope
    with pytest.raises(ValueError):
        with source_alias_scope({'/original':tmp_path/'missing'}):pass


def test_real_prepared_rotation_keeps_labels_identical_when_original_root_moves(tmp_path):
    from backend.engine.source_aliases import source_alias_scope
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import prepare_rotation_dataset,load_rotation_manifest
    source=tmp_path/'original';rows=rotation_data(source)
    prepared=prepare_rotation_dataset(source,tmp_path/'prepared',rows).root
    before=(prepared/'rotation.json').read_bytes();expected=load_rotation_manifest(prepared).provenance
    moved=tmp_path/'verified_snapshot';source.rename(moved)
    with pytest.raises(ValueError,match='original'):load_rotation_manifest(prepared)
    with source_alias_scope({str(source):moved}):
        actual=load_rotation_manifest(prepared).provenance
        assert actual==expected
        assert (prepared/'rotation.json').read_bytes()==before


def test_actual_loopback_coordinator_transfers_source_then_reopens_relocated_family(tmp_path,monkeypatch):
    import json,subprocess,torch
    from backend.tests.test_rotation import rotation_data
    from backend.engine.rotation import prepare_rotation_dataset
    from backend.api.routes_training import JobRecord
    from backend.remote.profiles import ComputeProfile
    from backend.remote.coordinator import run_remote_training
    from backend.tests.test_remote_coordinator import FakeRemote
    from backend.remote.worker import run_train
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(tmp_path/'user'))
    torch.set_num_threads(1)
    source=tmp_path/'source';rows=rotation_data(source)
    prepared=prepare_rotation_dataset(source,tmp_path/'prepared',rows).root
    immutable=(prepared/'rotation.json').read_bytes()
    local_id='a'*32;remote_id='job_'+local_id
    output=tmp_path/'models'/'rotation'/local_id;profile=ComputeProfile(id='cpu',name='Loopback',ssh_target='loopback',ssh_port=22,remote_root=str(tmp_path/'server'),runtime_kind='python',runtime_value='python3')
    record=JobRecord(job_id=remote_id,task='rotation',preset='fast',dataset_path=str(prepared),output_dir=str(output),status='running',remote_profile_id=profile.id,source_dataset_path=str(source),dataset_fingerprint='v1:fixture',launch_spec={'local_model_id':local_id})
    class RealRemote(FakeRemote):
        def launch(self,profile,argv,run_id):
            self.launches+=1
            source.rename(tmp_path/'original_unmounted')
            result=run_train(self.root/'runs'/run_id/'spec.json')
            assert result['status']=='completed',result
            return 'loopback-owned-worker'
    remote=RealRemote(Path(profile.remote_root))
    result=run_remote_training(record,profile,transport=remote,device='cpu',config_overrides={'epochs':1,'image_size':32,'batch_size':8})
    assert result['status']=='completed',result
    metadata=json.loads((output/'model_meta.json').read_text())
    payload=torch.load(output/'best_model.pt',map_location='cpu',weights_only=True)
    assert metadata['dataset_path']==str(output/'remote_snapshot/data')
    assert payload['dataset_provenance']['source_dataset_path']==str(source)
    assert payload['training_config']==metadata['training_config']
    assert (prepared/'rotation.json').read_bytes()==immutable
    assert json.loads((output/'remote_artifacts.json').read_text())['relocation']['local_dataset_root']==metadata['dataset_path']
    assert (output/'remote_received_artifacts.json').is_file()
    received=json.loads((output/'remote_received_artifacts.json').read_text())
    for row in received['artifacts']:
        original=output/'remote_received'/Path(row['path']).name
        import hashlib
        assert original.stat().st_size==row['size']
        assert hashlib.sha256(original.read_bytes()).hexdigest()==row['sha256']
    (tmp_path/'original_unmounted').rename(source)
    from backend.api.routes_training import _write_job_receipt
    record.status='completed';_write_job_receipt(record)
    receipt=json.loads((output/'job_receipt.json').read_text())
    assert receipt['job_id']==local_id and receipt['remote_job_id']==remote_id
    from backend.remote.operations import remote_job_context
    assert remote_job_context(output,local_id).job_id==remote_id
    from backend.engine.specialized_warm_start import resolve_family_parent
    parent=resolve_family_parent(tmp_path/'models',local_id,'rotation',source,prepared,{'width':8,'image_size':32})
    assert parent.job_id==local_id
    # The actual receipt has a native model ID and a different remote job ID.
    # A restart must preserve its observed epoch/metrics without retraining.
    from types import SimpleNamespace
    from backend.remote import coordinator
    from backend.remote.ssh_transport import SSHTransport
    from backend.engine.shared_scheduler import ResourceLeases
    assert receipt['current_epoch']==1 and receipt['total_epochs']==1
    retained={p:coordinator._sha256(p) for p in output.iterdir() if p.is_file()}
    restored=[]
    monkeypatch.setattr(SSHTransport,'exec',lambda *_a,**_k:pytest.fail('Ended CPU readback must not connect'))
    monkeypatch.setattr(coordinator,'make_remote_runner',lambda *_a,**_k:pytest.fail('Ended CPU readback must not create a runner'))
    coordinator.recover_remote_jobs(SimpleNamespace(get_job=lambda _:None,restore_terminal_job=restored.append,
        start_remote_job=lambda **_:pytest.fail('Ended CPU readback must not launch')))
    assert len(restored)==1 and restored[0].job_id==remote_id and restored[0].thread is None
    assert restored[0].current_epoch==receipt['current_epoch'] and restored[0].total_epochs==receipt['total_epochs']
    assert restored[0].train_loss==receipt['current_train_loss'] and restored[0].val_loss==receipt['current_val_loss']
    assert restored[0].metrics==receipt['metrics'] and restored[0].loss_history==receipt['loss_history']
    assert ResourceLeases(tmp_path/'user'/'resource_leases.sqlite3').list()==[]
    assert {p:coordinator._sha256(p) for p in retained}==retained

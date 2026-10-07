"""Actual portable report producer feeds read-only owned migration validation."""
import hashlib
import json
from pathlib import Path
import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_remote_portable_flow import portable_models
from backend.tests.test_remote_operations import FakeAdditionalRemote


def produced_history(tmp_path, monkeypatch, *, mixed=False):
    from backend.remote.operations import run_verified_flowchart_on_compute
    root, scopes, ledger, registry, account, actor = owned(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR',str(root))
    project, models, graph, image, profile=portable_models(root)
    profile=profile.model_copy(update={'runtime_kind':'python','runtime_value':'/usr/bin/python3',
        'remote_root':str(tmp_path/'remote-fixture')})
    project.update(id='portable-project',workspace_id=registry.workspace_id)
    directory=Path(project['project_dir']);(directory/'project.json').write_text(json.dumps(project))
    registry.register_project(project)
    class OriginalHandleRemote(FakeAdditionalRemote):
        def launch(self,*args):
            super().launch(*args)
            return '123:'+'a'*32
    remote=OriginalHandleRemote(Path(profile.remote_root))
    run_verified_flowchart_on_compute(profile,project,graph,models,image,device='cpu',transport=remote)
    if mixed:
        import torch
        path=next(iter(models.values()));torch.save({'task':'enhancement','model_state_dict':{'weight':torch.ones(1)}},path)
        profile=profile.model_copy(update={'id':'another-selected-worker','name':'Another archived worker'})
        run_verified_flowchart_on_compute(profile,project,graph,models,image,device='cpu',transport=remote)
    output=directory/'reports/remote_flow';indexes=sorted((output/'remote_operations').glob('flowchart_run_*.json'))
    archive=output/'remote_operations'/json.loads(indexes[0].read_text())['op_id']
    return root,scopes,ledger,registry,directory,output,indexes,archive


def test_independent_reports_with_different_models_and_profiles_survive_cutover_without_execution(tmp_path,monkeypatch):
    from backend.engine import global_migration as migration
    from backend.remote.ssh_transport import SSHTransport
    root,scopes,ledger,registry,directory,output,indexes,archive=produced_history(tmp_path,monkeypatch,mixed=True)
    assert len(indexes)==2
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(SSHTransport,'exec',lambda *_a,**_k:pytest.fail('Archival validation cannot connect'))
    view=migration.preview(root);assert view['can_apply'],view['blockers']
    migration.apply(root,expected_source_sha256=view['source_sha256'])
    forward=migration.preview_forward(root);assert forward['can_apply'],forward['blockers']
    migration.advance(root,expected_source_sha256=forward['source_sha256'])
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in before.items())
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.engine.global_store_paths import resolve_store_path
    import sqlite3
    with sqlite3.connect(resolve_store_path(root/scopes['ledger'])) as db:
        assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==0
    assert ResourceLeases(root/scopes['leases']).list()==[]


@pytest.mark.parametrize('damage',['result','manifest','live','uncertain_exit','handle','model_binding','profile_binding',
    'pipeline','extra_model','wrong_image','wrong_task','missing_journal','unknown_file','linked_report','foreign_reports','foreign_namespace'])
def test_portable_report_damage_blocks_cutover_without_creating_worker_authority(tmp_path,monkeypatch,damage):
    from backend.engine import global_migration as migration
    root,scopes,ledger,registry,directory,output,indexes,archive=produced_history(tmp_path,monkeypatch)
    index=indexes[0];journal=json.loads(index.read_text());spec=journal['spec']
    if damage=='result':(archive/'outputs/flowchart_result.json').write_text('{}')
    elif damage=='manifest':(archive/'operation_artifacts.json').write_text('{}')
    elif damage=='missing_journal':(archive/'operation_journal.json').unlink()
    elif damage=='unknown_file':(output/'remote_operations/foreign.txt').write_text('unlisted')
    elif damage=='linked_report':
        old=output.parent/'original';output.rename(old);output.symlink_to(old,target_is_directory=True)
    elif damage in {'foreign_reports','foreign_namespace'}:
        path=directory/'project.json';project=json.loads(path.read_text());project['reports_dir' if damage=='foreign_reports' else 'id']=str(tmp_path/'elsewhere') if damage=='foreign_reports' else 'another-project';path.write_text(json.dumps(project))
    else:
        if damage=='live':journal['state']='running'
        elif damage=='uncertain_exit':journal['worker_exit_confirmed']=False
        elif damage=='handle':journal['remote_handle']='123'
        elif damage=='model_binding':spec['input_manifest_sha256']='b'*64
        elif damage=='profile_binding':spec['execution_profile_sha256']='c'*64
        elif damage=='pipeline':spec['pipeline']['nodes'][1]['data']['model_job_id']='b'*32
        elif damage=='extra_model':spec['models'].append(dict(spec['models'][0]))
        elif damage=='wrong_image':spec['image_path']='inputs/../image.png'
        elif damage=='wrong_task':spec['task']='classification'
        if damage in {'model_binding','profile_binding','pipeline','extra_model','wrong_image','wrong_task'}:
            encoded=json.dumps(spec,sort_keys=True,separators=(',',':')).encode()
            (archive/'spec.json').write_bytes(encoded)
            new=index.with_name('flowchart_run_'+hashlib.sha256(encoded).hexdigest()[:20]+'.json');index.rename(new);index=new
            receipt_path=archive/'operation_artifacts.json';receipt=json.loads(receipt_path.read_text())
            receipt['spec_sha256']=hashlib.sha256(encoded).hexdigest();receipt_path.write_text(json.dumps(receipt))
        raw=json.dumps(journal).encode();index.write_bytes(raw);(archive/'operation_journal.json').write_bytes(raw)
    if damage=='linked_report':
        with pytest.raises(ValueError,match='linked'):migration.preview(root)
        return
    view=migration.preview(root)
    assert not view['can_apply'] and view['blockers']
    assert not (root/'global-active.json').exists()


@pytest.mark.parametrize('damage',['image','models','device','comparison','debug'])
def test_rehashed_result_cannot_change_the_original_portable_execution(tmp_path,monkeypatch,damage):
    from backend.engine import global_migration as migration
    root,scopes,ledger,registry,directory,output,indexes,archive=produced_history(tmp_path,monkeypatch)
    result_path=archive/'outputs/flowchart_result.json';result=json.loads(result_path.read_text())
    if damage=='image':result['image_sha256']='f'*64
    elif damage=='models':result['model_job_ids']=[]
    elif damage=='device':result['execution_device']='cuda'
    else:
        index=indexes[0];journal=json.loads(index.read_text());spec=journal['spec']
        if damage=='comparison':spec.update(comparison_operation_contract=1,comparison_binding_sha256='a'*64)
        else:spec['stop_node_id']='node_inspect'
        encoded=json.dumps(spec,sort_keys=True,separators=(',',':')).encode();(archive/'spec.json').write_bytes(encoded)
        index.rename(index.with_name('flowchart_run_'+hashlib.sha256(encoded).hexdigest()[:20]+'.json'))
        raw=json.dumps(journal).encode();(archive/'operation_journal.json').write_bytes(raw)
        next((output/'remote_operations').glob('flowchart_run_*.json')).write_bytes(raw)
    result_path.write_text(json.dumps(result))
    receipt_path=archive/'operation_artifacts.json';receipt=json.loads(receipt_path.read_text())
    receipt['spec_sha256']=hashlib.sha256((archive/'spec.json').read_bytes()).hexdigest()
    row=next(x for x in receipt['manifest']['artifacts'] if x['path']=='outputs/flowchart_result.json')
    row.update(sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),size=result_path.stat().st_size)
    receipt_path.write_text(json.dumps(receipt))
    view=migration.preview(root);assert not view['can_apply'],view


@pytest.mark.parametrize('state',['failed','aborted'])
def test_ended_partial_original_portable_bytes_remain_archival_without_quality_grant(tmp_path,monkeypatch,state):
    from backend.engine import global_migration as migration
    root,scopes,ledger,registry,directory,output,indexes,archive=produced_history(tmp_path,monkeypatch)
    index=indexes[0];journal=json.loads(index.read_text());journal.update(state=state,worker_terminal_state=state)
    raw=json.dumps(journal).encode();index.write_bytes(raw);(archive/'operation_journal.json').write_bytes(raw)
    view=migration.preview(root);assert view['can_apply'],view['blockers']
    migration.apply(root,expected_source_sha256=view['source_sha256'])
    assert (archive/'operation_journal.json').read_bytes()==raw


def test_received_old_portable_model_bytes_need_not_equal_later_current_checkpoint(tmp_path,monkeypatch):
    from backend.engine import global_migration as migration
    root,scopes,ledger,registry,directory,output,indexes,archive=produced_history(tmp_path,monkeypatch)
    checkpoint=next((directory/'models').rglob('best_model.pt'));checkpoint.write_bytes(b'Later retained model bytes')
    before=checkpoint.read_bytes();view=migration.preview(root);assert view['can_apply'],view['blockers']
    migration.apply(root,expected_source_sha256=view['source_sha256'])
    assert checkpoint.read_bytes()==before

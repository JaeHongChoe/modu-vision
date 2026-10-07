"""Ended remote-operation archives retain received hashes, never worker authority."""
import hashlib
import json
import uuid
import pytest
from backend.tests.test_terminal_remote_history import remote_history, digest


def operation_history(tmp_path, operation='evaluate'):
    root, scopes, ledger, registry, key, source, output, parent = remote_history(tmp_path)
    spec = dict(protocol_version=1, operation=operation, job_id=parent['job_id'],
                task=parent['task'], input_manifest_sha256=parent['input_manifest_sha256'])
    if operation == 'flowchart_run':
        spec.update(models=[{'job_id': parent['job_id'], 'sha256': 'c'*64}], image_sha256='b'*64)
    encoded = json.dumps(spec, sort_keys=True, separators=(',', ':')).encode()
    directory = output/'remote_operations'/('op_'+uuid.uuid4().hex)
    directory.mkdir(parents=True)
    (directory/'spec.json').write_bytes(encoded)
    result = directory/'outputs/nested/result.json'; result.parent.mkdir(parents=True)
    result.write_bytes(b'{"archived":true}')
    manifest = dict(protocol_version=1, operation=operation, job_id=parent['job_id'],
                    input_manifest_sha256=parent['input_manifest_sha256'],
                    artifacts=[dict(path='outputs/nested/result.json', size=result.stat().st_size, sha256=digest(result))])
    if operation == 'flowchart_run':
        manifest.update(model_refs=spec['models'], selected_image_sha256=spec['image_sha256'])
    journal = dict(protocol_version=1, operation=operation, job_id=parent['job_id'], op_id=directory.name,
                   profile=parent['profile'], spec=spec, state='completed', worker_terminal_state='completed',
                   worker_exit_confirmed=True, remote_handle='123:'+uuid.uuid4().hex)
    record = directory.parent/(operation+'_'+hashlib.sha256(encoded).hexdigest()[:20]+'.json')
    record.write_text(json.dumps(journal))
    (directory/'operation_journal.json').write_bytes(record.read_bytes())
    archive = dict(schema='modu-vision.remote-operation-archive/v1', op_id=directory.name,
                   spec_sha256=digest(directory/'spec.json'), manifest=manifest)
    (directory/'operation_artifacts.json').write_text(json.dumps(archive))
    return root, scopes, ledger, source, output, directory, record, result


@pytest.mark.parametrize('operation', ['evaluate','infer','benchmark','flowchart_run','export','package_parity','flow_preflight'])
def test_ended_operation_bytes_survive_owned_cutover_and_forward_without_execution(tmp_path, monkeypatch, operation):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.remote.ssh_transport import SSHTransport
    root, scopes, ledger, source, output, directory, record, result = operation_history(tmp_path, operation)
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('Archive inspection cannot connect'))
    before={p:digest(p) for p in [record,*directory.rglob('*')] if p.is_file()}
    plan=binding.preview(root); assert plan['can_apply'], plan['blockers']
    binding.apply(root, expected_preview_sha256=plan['preview_sha256'], reason='Reviewed ended operation archive integrity')
    view=migration.preview(root); assert view['can_apply'], view['blockers']
    migration.apply(root, expected_source_sha256=view['source_sha256'])
    forward=migration.preview_forward(root); assert forward['can_apply'],forward['blockers']
    migration.advance(root, expected_source_sha256=forward['source_sha256'])
    assert all(digest(p)==sha for p,sha in before.items())
    assert ledger.path.exists() and result.read_bytes()==b'{"archived":true}'


@pytest.mark.parametrize('damage', ['result','spec','missing_archive','foreign_job','foreign_profile','live',
    'uncertain_exit','wrong_handle','duplicate_artifact','unsafe_artifact','missing_result','unlisted_result',
    'linked_result','unsupported_operation','archive_identity','duplicate_json','wrong_directory','changed_flow_image','missing_run_journal'])
def test_operation_history_rejects_changed_or_unproven_results_before_binding(tmp_path, damage):
    from backend.engine.historical_job_binding import preview
    root, scopes, ledger, source, output, directory, record, result=operation_history(tmp_path,'flowchart_run')
    journal=json.loads(record.read_text()); archive_path=directory/'operation_artifacts.json'
    archive=json.loads(archive_path.read_text())
    if damage=='result': result.write_bytes(b'changed result')
    elif damage=='spec': (directory/'spec.json').write_text('{}')
    elif damage=='missing_archive': archive_path.unlink()
    elif damage=='missing_run_journal': (directory/'operation_journal.json').unlink()
    elif damage=='missing_result': result.unlink()
    elif damage=='unlisted_result': (result.parent/'extra.json').write_text('{}')
    elif damage=='linked_result':
        kept=directory/'original.bin'; result.rename(kept); result.symlink_to(kept)
    elif damage=='duplicate_json': archive_path.write_text('{"schema":1,"schema":2}')
    elif damage=='wrong_directory': record.rename(record.with_name('flowchart_run_'+'0'*20+'.json'))
    elif damage in ['foreign_job','foreign_profile','live','uncertain_exit','wrong_handle','unsupported_operation']:
        if damage=='foreign_job': journal['job_id']='job_other'
        elif damage=='foreign_profile': journal['profile']['id']='other-profile'
        elif damage=='live': journal['state']='running'
        elif damage=='uncertain_exit': journal['worker_exit_confirmed']=False
        elif damage=='wrong_handle': journal['remote_handle']='123'
        else: journal['operation']='unsupported'
        record.write_text(json.dumps(journal))
    else:
        if damage=='duplicate_artifact': archive['manifest']['artifacts'].append(dict(archive['manifest']['artifacts'][0]))
        elif damage=='unsafe_artifact': archive['manifest']['artifacts'][0]['path']='outputs/../result.json'
        elif damage=='changed_flow_image': archive['manifest']['selected_image_sha256']='d'*64
        else: archive['op_id']='op_'+'0'*32
        archive_path.write_text(json.dumps(archive))
    before=ledger.record('job_legacy'); plan=preview(root)
    assert not plan['can_apply'] and plan['blockers']
    assert ledger.record('job_legacy')==before


def test_real_operation_producer_retains_original_manifest_after_verified_download(tmp_path,monkeypatch):
    from backend.tests.test_remote_operations import _completed_remote
    from backend.remote.operations import run_remote_evaluation
    context,transport=_completed_remote(tmp_path,monkeypatch)
    run_remote_evaluation(context,transport=transport)
    journal=json.loads(next((context.output_dir/'remote_operations').glob('evaluate_*.json')).read_text())
    directory=context.output_dir/'remote_operations'/journal['op_id']
    archive=json.loads((directory/'operation_artifacts.json').read_text())
    original=json.loads((transport.root/'runs'/journal['op_id']/'artifacts.json').read_text())
    assert archive['manifest']==original and archive['spec_sha256']==digest(directory/'spec.json')
    assert archive['op_id']==journal['op_id']
    assert json.loads((directory/'operation_journal.json').read_text())==journal


@pytest.mark.parametrize('state',['failed','aborted'])
def test_failed_operation_received_bytes_remain_bound(tmp_path,state):
    from backend.engine.historical_job_binding import preview
    root,scopes,ledger,source,output,directory,record,result=operation_history(tmp_path)
    journal=json.loads(record.read_text());journal.update(state=state,worker_terminal_state=state)
    raw=json.dumps(journal).encode();record.write_bytes(raw);(directory/'operation_journal.json').write_bytes(raw)
    assert preview(root)['can_apply']
    result.write_bytes(b'changed partial result')
    assert not preview(root)['can_apply']


def test_rerun_does_not_erase_an_earlier_received_archive(tmp_path,monkeypatch):
    from backend.tests.test_remote_operations import _completed_remote
    from backend.remote.operations import run_remote_evaluation
    context,transport=_completed_remote(tmp_path,monkeypatch)
    run_remote_evaluation(context,transport=transport)
    index=next((context.output_dir/'remote_operations').glob('evaluate_*.json'))
    first=json.loads(index.read_text());previous=context.output_dir/'remote_operations'/first['op_id']
    original={p:digest(p) for p in previous.rglob('*') if p.is_file()}
    run_remote_evaluation(context,transport=transport,force_recompute=True)
    second=json.loads(index.read_text())
    assert first['op_id']!=second['op_id']
    assert all(digest(p)==sha for p,sha in original.items())

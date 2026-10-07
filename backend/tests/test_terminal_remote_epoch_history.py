"""Archival epoch bytes keep their original receipt; no compute authority."""
import json

import pytest

from backend.tests.test_terminal_remote_history import remote_history, digest


def epoch_history(tmp_path, state='aborted'):
    from backend.engine.training_resume import save_training_state
    from backend.remote.training_state_transfer import identity_sha256
    from backend.tests.test_extension_resume_state import components
    result = remote_history(tmp_path, state)
    root, scopes, ledger, registry, key, source, output, journal = result
    parts = components()
    identity = {'task': journal['task'], 'preset': journal['preset'],
                'device': 'cpu', 'recipe': {'epochs': 3}}
    checkpoint = output / 'latest_training_state.pt'
    save_training_state(checkpoint, *parts[:4], identity=identity,
                        next_epoch=1, global_step=2, early_stopping=parts[4])
    receipt = {'protocol_version': 1, 'operation': 'train', 'job_id': journal['job_id'],
               'input_manifest_sha256': journal['input_manifest_sha256'],
               'path': 'outputs/latest_training_state.pt', 'size': checkpoint.stat().st_size,
               'sha256': digest(checkpoint), 'identity_sha256': identity_sha256(identity),
               'next_epoch': 1, 'global_step': 2}
    (output / 'training_state_receipt.json').write_text(json.dumps(receipt))
    return result


@pytest.mark.parametrize('damage', [
    'changed_bytes', 'missing_state', 'missing_receipt', 'linked_state', 'linked_receipt',
    'wrong_job', 'wrong_input', 'wrong_path', 'wrong_size', 'wrong_hash',
    'wrong_identity', 'wrong_epoch', 'wrong_step', 'boolean_epoch', 'duplicate_key',
    'invalid_payload', 'wrong_task', 'wrong_preset', 'oversized_state',
])
def test_invalid_epoch_history_cannot_bind_or_migrate(tmp_path, monkeypatch, damage):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.remote.training_state_transfer import identity_sha256
    root, scopes, ledger, registry, key, source, output, journal = epoch_history(tmp_path)
    checkpoint, receipt_path = output / 'latest_training_state.pt', output / 'training_state_receipt.json'
    receipt = json.loads(receipt_path.read_text())
    if damage == 'changed_bytes':
        checkpoint.write_bytes(checkpoint.read_bytes() + b'changed')
    elif damage == 'missing_state':
        checkpoint.unlink()
    elif damage == 'missing_receipt':
        receipt_path.unlink()
    elif damage in ('linked_state', 'linked_receipt'):
        target = checkpoint if damage == 'linked_state' else receipt_path
        original = target.with_name('original-' + target.name)
        target.rename(original); target.symlink_to(original)
    elif damage == 'duplicate_key':
        receipt_path.write_text('{"job_id":"job_legacy","job_id":"other"}')
    elif damage == 'oversized_state':
        monkeypatch.setattr('backend.remote.training_state_transfer.MAX_STATE_BYTES', 16)
    else:
        if damage in ('invalid_payload', 'wrong_task', 'wrong_preset'):
            if damage == 'invalid_payload':
                checkpoint.write_bytes(b'not an exact epoch state')
            else:
                import torch
                payload = torch.load(checkpoint, weights_only=True)
                payload['identity']['task' if damage == 'wrong_task' else 'preset'] = 'other'
                torch.save(payload, checkpoint)
                receipt['identity_sha256'] = identity_sha256(payload['identity'])
            receipt.update(sha256=digest(checkpoint), size=checkpoint.stat().st_size)
        else:
            field, value = {
                'wrong_job': ('job_id', 'job_other'), 'wrong_input': ('input_manifest_sha256', 'b' * 64),
                'wrong_path': ('path', '../latest_training_state.pt'), 'wrong_size': ('size', True),
                'wrong_hash': ('sha256', '0' * 64), 'wrong_identity': ('identity_sha256', '0' * 64),
                'wrong_epoch': ('next_epoch', 2), 'wrong_step': ('global_step', 3),
                'boolean_epoch': ('next_epoch', True),
            }[damage]
            receipt[field] = value
        receipt_path.write_text(json.dumps(receipt))
    previous = ledger.record('job_legacy')
    plan = binding.preview(root)
    assert not plan['can_apply'] and any('state' in reason.lower() or 'runtime history' in reason.lower()
                                         for reason in plan['blockers']), plan
    with pytest.raises(ValueError):
        binding.apply(root, expected_preview_sha256=plan['preview_sha256'],
                      reason='Reviewed original ended epoch history')
    assert ledger.record('job_legacy') == previous
    if damage in ('linked_state', 'linked_receipt'):
        with pytest.raises(ValueError, match='linked artifact'):
            migration.preview(root)
    else:
        assert not migration.preview(root)['can_apply']


@pytest.mark.parametrize('state', ['completed', 'failed', 'aborted'])
def test_valid_epoch_history_survives_cutover_forward_without_compute(tmp_path, monkeypatch, state):
    from backend.engine import historical_job_binding as binding, global_migration as migration
    from backend.engine.global_store_paths import resolve_store_path
    from backend.engine.shared_scheduler import ResourceLeases
    from backend.remote.ssh_transport import SSHTransport
    root, scopes, ledger, registry, key, source, output, journal = epoch_history(tmp_path, state)
    before = {path: digest(path) for path in [source, *output.iterdir()] if path.is_file()}
    monkeypatch.setattr(SSHTransport, 'exec', lambda *_a, **_k: pytest.fail('History must not connect'))
    plan = binding.preview(root); assert plan['can_apply'], plan['blockers']
    result = binding.apply(root, expected_preview_sha256=plan['preview_sha256'],
                           reason='Reviewed original ended epoch history')
    assert result['worker_authority_created'] is False
    for preview, apply in [(migration.preview, migration.apply), (migration.preview_forward, migration.advance)]:
        plan = preview(root); assert plan['can_apply'], plan['blockers']
        apply(root, expected_source_sha256=plan['source_sha256'])
    assert (resolve_store_path(root / scopes['remote_journals']) / source.name).read_bytes() == source.read_bytes()
    assert ResourceLeases(root / scopes['leases']).list() == []
    assert all(digest(path) == sha for path, sha in before.items())

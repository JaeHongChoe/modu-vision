"""Review binds the real signed bundle and drained source, not UI assertions."""
import json
import pytest
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan


def args(root, value, command):
    return [command, '--root', str(root), '--bundle', str(value['directory']),
        '--envelope', str(value['envelope']), '--authority', str(value['authority']),
        '--pinned-authority-sha256', value['pinned_authority_sha256'],
        '--target-json', json.dumps(value['target'])]


def test_preview_is_read_only_and_install_requires_the_reviewed_source(tmp_path, capsys):
    from backend.engine import runtime_update as update
    root, scopes, *_ = owned(tmp_path); value = fixture(tmp_path)
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
        if p.is_file() and p.name != 'migration_admission.lock'}
    assert update.main(args(root, value, 'preview')) == 0
    review = json.loads(capsys.readouterr().out)
    assert review['status'] == 'reviewed' and review['version'] == '1.0.0'
    assert review['source_sha256'] == plan(root, value).source_sha256
    assert review['application_file_count'] == 1 and review['pack_count'] == 0
    assert review['installation_id'] and len(review['plan_sha256']) == 64
    assert 'password' not in json.dumps(review) and 'key' not in review
    # Preview can establish the admission lock but changes no scope/artifact.
    after = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*')
        if p.is_file() and p.name != 'migration_admission.lock'}
    assert after == before
    (root/'projects/labels.json').write_bytes(b'new write after review')
    assert update.main(args(root, value, 'install') + ['--expected-plan-sha256', review['plan_sha256']]) == 2
    assert 'review' in json.loads(capsys.readouterr().out)['error'].lower()
    assert not (root/'application-active.json').exists()
    assert not (root/'.application-updates').exists()


def test_exact_review_can_install_and_inspect_reopens_the_pair(tmp_path, capsys):
    from backend.engine import runtime_update as update
    root, scopes, *_ = owned(tmp_path); value = fixture(tmp_path)
    assert update.main(args(root, value, 'preview')) == 0
    review = json.loads(capsys.readouterr().out)
    assert update.main(args(root, value, 'install') + ['--expected-plan-sha256', review['plan_sha256']]) == 0
    installed = json.loads(capsys.readouterr().out)
    info = update.inspect_update(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])
    assert info['status'] == 'committed' and info['update_id'] == installed['update_id']
    assert info['version'] == '1.0.0' and info['database_fence'] == 1
    assert info['allowed_recovery'] == ['finish', 'forward']
    assert info['model_quality_acceptance'] == 'required'
    assert info['application_started'] is False


@pytest.mark.parametrize('boundary', ['before_database', 'database_prepared', 'after_database', 'after_application', 'after_receipt'])
def test_inspection_recognizes_pending_without_recovering_it(tmp_path, monkeypatch, boundary):
    from backend.engine import runtime_update as update
    root, scopes, *_ = owned(tmp_path); value = fixture(tmp_path)
    def interrupt(point):
        if point == boundary: raise KeyboardInterrupt('owned interruption')
    monkeypatch.setattr(update, '_checkpoint', interrupt)
    with pytest.raises(KeyboardInterrupt): update.install_update(root, plan(root, value))
    before = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    info = update.inspect_update(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])
    assert info['status'] == 'recovery_required' and info['allowed_recovery'][0] == 'finish'
    assert ('abort' in info['allowed_recovery']) == (boundary == 'before_database')
    assert before == {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_inspection_refuses_a_different_external_authority(tmp_path):
    from backend.engine import runtime_update as update
    root, scopes, *_ = owned(tmp_path); value = fixture(tmp_path)
    update.install_update(root, plan(root, value)); other = fixture(tmp_path, version='2.0.0', authority=tmp_path/'other.json')
    with pytest.raises(ValueError, match='authority'):
        update.inspect_update(root, other['authority'], pinned_authority_sha256=other['pinned_authority_sha256'])


def test_cli_owned_version_comes_from_original_intent_not_renderer(tmp_path, capsys):
    from backend.engine import runtime_update as update
    root, scopes, *_ = owned(tmp_path); first = fixture(tmp_path)
    update.install_update(root, plan(root, first))
    second = fixture(tmp_path, version='1.1.0', key=first['key'], authority=first['authority'])
    assert update.main(args(root, second, 'preview') + ['--use-owned-version']) == 0
    review = json.loads(capsys.readouterr().out)
    assert review['current_version'] == '1.0.0' and review['version'] == '1.1.0'
    assert update.main(args(root, second, 'install') + ['--use-owned-version', '--expected-plan-sha256', review['plan_sha256']]) == 0
    assert json.loads(capsys.readouterr().out)['version'] == '1.1.0'

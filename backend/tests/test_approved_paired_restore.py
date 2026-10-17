"""Disposable signed fixtures and real SQLite policy gates, not installed proof.

Only the preactivation provider is the original explicitly controlled supplier.
No application process, model forward, training, GPU, API or network is run here.
Root owns every execution. The original restore-denial test remains independent
of the new API's success expectation before and after implementation.
"""
import hashlib
import importlib
import json
from pathlib import Path
import threading

import pytest

from backend.engine import runtime_update as update
from backend.engine import global_migration as migration
from backend.engine.global_store_paths import active_generation, resolve_store_path
from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan as update_plan
from backend.tests.test_staged_update_canary import CONTROL_SPEC, controlled_proof


def _raw_pointers(root):
    return {name: (root / name).read_bytes() for name in (update.ACTIVE, 'global-active.json')}


def _retained_generations(root):
    return {str(path.relative_to(root)): path.read_bytes()
            for name in (update.GENERATIONS, update.UPDATES, '.global-generations', '.global-migrations')
            for path in (root / name).rglob('*') if path.is_file()}


@pytest.fixture
def pair(tmp_path, monkeypatch):
    from backend.engine.shared_accounts import AccountStore
    root, scopes, *_ = owned(tmp_path)
    calls = controlled_proof(monkeypatch)
    first = fixture(tmp_path, version='1.0.0')
    installed_first = update.install_update(root, update_plan(root, first))
    prior_account = AccountStore(root / scopes['accounts'])
    prior_token = prior_account.login('fixture-admin', 'fixture-password-123')['token']
    second = fixture(tmp_path, version='1.1.0', key=first['key'], authority=first['authority'])
    second['target']['current_version'] = '1.0.0'
    installed_second = update.install_update(root, update_plan(root, second))
    assert len(calls) == 2
    return dict(root=root, scopes=scopes, first=first, second=second,
                first_result=installed_first, second_result=installed_second,
                prior_token=prior_token, calls=calls, tmp=tmp_path)


def _api():
    # This is the same API on RED and GREEN. No fallback into another action.
    return importlib.import_module('backend.engine.approved_paired_restore')


def _approval(pair, *, change=None, expected_sha256=None):
    api = _api()
    document = api.review(pair['root'], canary=dict(CONTROL_SPEC))
    if change is not None:
        change(document)
    raw = json.dumps(document, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()
    path = pair['tmp'] / 'explicit-reviewed-conversion.json'
    path.write_bytes(raw)
    proposal = api.plan(pair['root'], path,
                        pinned_approval_sha256=expected_sha256 or hashlib.sha256(raw).hexdigest())
    return api, document, proposal


def test_approved_inverse_publishes_fresh_exact_prior_pair_and_revokes_copied_sessions(pair):
    """Wrong target, old-pointer reactivation or reused canary must break this."""
    from backend.engine.shared_accounts import AccountStore
    root = pair['root']
    original_pair = _raw_pointers(root)
    retained = _retained_generations(root)
    old_app = json.loads(original_pair[update.ACTIVE])
    old_db = json.loads(original_pair['global-active.json'])
    api, document, proposal = _approval(pair)
    result = api.install(root, proposal)
    fresh_app = json.loads((root / update.ACTIVE).read_bytes())
    fresh_db = active_generation(root)[1]
    assert result['status'] == 'committed' and result['version'] == '1.0.0'
    assert result['update_id'] not in {pair['first_result']['update_id'], pair['second_result']['update_id']}
    assert fresh_app['update_id'] == fresh_app['application_generation'] == result['update_id']
    assert fresh_app['database_pointer'] == fresh_db
    assert fresh_db['generation_id'] not in {old_db['generation_id'], pair['first_result']['database_pointer']['generation_id']}
    assert fresh_db['fence'] == 3
    assert old_app['update_id'] == pair['second_result']['update_id']
    assert all((root / relative).read_bytes() == body for relative, body in retained.items())
    account = AccountStore(root / pair['scopes']['accounts'])
    with pytest.raises(ValueError, match='expired|unavailable'):
        account.authenticate(pair['prior_token'])
    assert account.login('fixture-admin', 'fixture-password-123')['user']['username'] == 'fixture-admin'
    assert (root / 'projects/labels.json').read_bytes() == b'{"label":"original"}'
    assert len(pair['calls']) == 3 and pair['calls'][-1] == fresh_db['generation_id']
    record, directory, _ = update._validated_intent(root, result['update_id'])
    assert record['kind'] == 'approved_paired_restore'
    assert record['previous_application'] == old_app and record['previous_database'] == old_db
    assert record['approved_conversion']['document'] == document
    journal = migration._journal(root, record['migration_id'])[1]
    assert journal['kind'] == 'paired_restore' and journal['application_update_id'] == result['update_id']
    assert (directory / 'canary' / record['migration_id'] / 'canary-requirement.json').is_file()
    assert not (root / update.PENDING).exists()


def test_original_application_bound_restore_and_ordinary_downgrade_remain_refused(pair):
    """This independent existing boundary passes before and after the feature."""
    root = pair['root']
    before = _raw_pointers(root)
    current = pair['second_result']['update_id']
    with pytest.raises(ValueError, match='approved|conversion|finish|forward'):
        update.recover_update(root, current, action='restore')
    with pytest.raises(ValueError, match='Application-bound|reviewed|[Cc]anary'):
        migration.recover(root, pair['second_result']['database_pointer']['generation_id'], action='restore')
    downgrade = dict(pair['first'])
    downgrade['target'] = dict(downgrade['target'], current_version='1.1.0')
    with pytest.raises(ValueError, match='downgrade|rollback'):
        update.plan_update(root, downgrade['directory'], downgrade['envelope'], downgrade['authority'],
                           pinned_authority_sha256=downgrade['pinned_authority_sha256'],
                           target=downgrade['target'], canary=CONTROL_SPEC)
    assert _raw_pointers(root) == before and not (root / update.PENDING).exists()


def test_wrong_explicit_approval_hash_refuses_before_staging(pair):
    root = pair['root']
    before = _raw_pointers(root)
    retained = _retained_generations(root)
    with pytest.raises(ValueError, match='approval|conversion|hash|SHA'):
        _approval(pair, expected_sha256='0' * 64)
    assert _raw_pointers(root) == before and _retained_generations(root) == retained
    assert not (root / update.PENDING).exists()


@pytest.mark.parametrize('part', ['current', 'previous'])
def test_rehashed_foreign_pair_is_not_an_approved_current_previous_pair(pair, part):
    root = pair['root']
    before = _raw_pointers(root)
    def foreign(document):
        document[part]['application_pointer']['update_id'] = 'f' * 32
    with pytest.raises(ValueError, match='approval|conversion|pair|changed'):
        _approval(pair, change=foreign)
    assert _raw_pointers(root) == before and not (root / update.PENDING).exists()


@pytest.mark.parametrize('where', ['current_database', 'immutable_backup', 'prior_application'])
def test_later_source_backup_or_prior_application_write_refuses_without_new_pointer(pair, where):
    root = pair['root']
    api, _, proposal = _approval(pair)
    before = _raw_pointers(root)
    current_record = update._intent(root, pair['second_result']['update_id'])[0]
    if where == 'current_database':
        path = resolve_store_path(root / pair['scopes']['profiles'])
        path.write_bytes(b'{"profiles":[],"selected":null,"later_write":true}')
    elif where == 'immutable_backup':
        path = root / '.global-migrations' / current_record['migration_id'] / 'original' / pair['scopes']['profiles']
        path.write_bytes(b'{"profiles":[],"selected":null,"foreign_backup":true}')
    else:
        path = root / update.GENERATIONS / pair['first_result']['update_id'] / 'application/bin/app'
        path.chmod(0o600)
        path.write_bytes(b'changed prior executable')
    with pytest.raises(ValueError, match='changed|integrity|checksum|size|source|backup|approval|conversion'):
        api.install(root, proposal)
    assert _raw_pointers(root) == before and not (root / update.PENDING).exists()
    assert len(pair['calls']) == 2


def test_active_original_shared_writer_blocks_inverse_without_stop_or_staging(pair):
    from backend.engine.migration_guard import maintenance_guard
    root = pair['root']
    api, _, proposal = _approval(pair)
    before = _raw_pointers(root)
    entered = threading.Event()
    release = threading.Event()
    errors = []
    def writer():
        try:
            with maintenance_guard(root):
                entered.set()
                assert release.wait(5)
        except BaseException as error:
            errors.append(error)
    original = threading.Thread(target=writer)
    original.start()
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError, match='writers|admission|drain'):
            api.install(root, proposal)
        assert original.is_alive() and _raw_pointers(root) == before
        assert not (root / update.PENDING).exists()
    finally:
        release.set()
        original.join(5)
    assert not original.is_alive() and errors == [] and len(pair['calls']) == 2


def test_failed_fresh_preactivation_keeps_pending_pair_and_never_reuses_old_receipt(pair, monkeypatch):
    root = pair['root']
    api, _, proposal = _approval(pair)
    before = _raw_pointers(root)
    failed_calls = controlled_proof(monkeypatch, damage=lambda receipt: receipt.update(native_application_verified=True))
    with pytest.raises(ValueError, match='canary|Canary'):
        api.install(root, proposal)
    pending = (root / update.PENDING).read_bytes()
    identifier = json.loads(pending)['update_id']
    assert identifier not in {pair['first_result']['update_id'], pair['second_result']['update_id']}
    assert _raw_pointers(root) == before and len(failed_calls) == 1
    with pytest.raises(ValueError, match='canary|Canary'):
        update.recover_update(root, identifier, action='finish')
    assert _raw_pointers(root) == before and (root / update.PENDING).read_bytes() == pending
    assert len(failed_calls) == 1


@pytest.mark.parametrize('point', ['database_prepared', 'after_database', 'after_application'])
def test_interrupted_inverse_finishes_exact_fresh_pair_without_second_canary(pair, monkeypatch, point):
    root = pair['root']
    api, _, proposal = _approval(pair)
    original = update._checkpoint
    def stop(value):
        if value == point:
            raise KeyboardInterrupt('controlled inverse durable boundary')
    monkeypatch.setattr(update, '_checkpoint', stop)
    with pytest.raises(KeyboardInterrupt, match='controlled inverse'):
        api.install(root, proposal)
    identifier = json.loads((root / update.PENDING).read_bytes())['update_id']
    calls_before_finish = len(pair['calls'])
    monkeypatch.setattr(update, '_checkpoint', original)
    result = update.recover_update(root, identifier, action='finish')
    assert result['status'] == 'committed' and result['version'] == '1.0.0'
    assert len(pair['calls']) == 3
    if point != 'database_prepared':
        assert calls_before_finish == 3
    assert json.loads((root / update.ACTIVE).read_bytes())['database_pointer'] == active_generation(root)[1]
    assert active_generation(root)[1]['fence'] == 3
    assert not (root / update.PENDING).exists()


def test_first_install_has_no_previous_pair_to_inverse(tmp_path, monkeypatch):
    root, *_ = owned(tmp_path)
    controlled_proof(monkeypatch)
    first = fixture(tmp_path)
    update.install_update(root, update_plan(root, first))
    before = _raw_pointers(root)
    with pytest.raises(ValueError, match='previous|prior|pair'):
        _api().review(root, canary=dict(CONTROL_SPEC))
    assert _raw_pointers(root) == before and not (root / update.PENDING).exists()


def test_inverse_pending_before_database_resumes_approved_conversion_and_not_ordinary_advance(pair, monkeypatch):
    root = pair['root']
    api, _, proposal = _approval(pair)
    before = _raw_pointers(root)
    original = update._checkpoint
    def stop(point):
        if point == 'before_database':
            raise KeyboardInterrupt('controlled inverse before database')
    monkeypatch.setattr(update, '_checkpoint', stop)
    with pytest.raises(KeyboardInterrupt, match='controlled inverse before database'):
        api.install(root, proposal)
    identifier = json.loads((root / update.PENDING).read_bytes())['update_id']
    assert update._intent(root, identifier)[0]['migration_id'] is None
    assert _raw_pointers(root) == before and len(pair['calls']) == 2
    monkeypatch.setattr(update, '_checkpoint', original)
    result = update.recover_update(root, identifier, action='finish')
    record = update._intent(root, identifier)[0]
    assert result['version'] == '1.0.0' and result['status'] == 'committed'
    assert migration._journal(root, record['migration_id'])[1]['kind'] == 'paired_restore'
    assert active_generation(root)[1]['fence'] == 3 and len(pair['calls']) == 3
    assert not (root / update.PENDING).exists()


def test_unsupported_forward_from_approved_inverse_refuses_before_journal_or_pointer_write(pair):
    root = pair['root']
    api, _, proposal = _approval(pair)
    result = api.install(root, proposal)
    before = _raw_pointers(root)
    journal = root / update.UPDATES / result['update_id'] / 'journal.json'
    original = journal.read_bytes()
    profile = resolve_store_path(root / pair['scopes']['profiles'])
    profile.write_bytes(b'{"profiles":[],"selected":null,"later_inverse_write":true}')
    with pytest.raises(ValueError, match='newly reviewed|successor|approved|Approved'):
        update.recover_update(root, result['update_id'], action='forward')
    assert journal.read_bytes() == original and _raw_pointers(root) == before
    assert not (root / update.PENDING).exists()
    assert json.loads(profile.read_bytes())['later_inverse_write'] is True

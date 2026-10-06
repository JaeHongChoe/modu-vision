"""Restore interruption resumes the exact owned generation without losing writes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from backend.engine import global_migration as migration
from backend.engine.global_store_paths import active_generation, resolve_store_path
from backend.tests.test_global_migration import owned


def original_pins(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def unchanged(root, pins):
    assert all(hashlib.sha256((root / name).read_bytes()).hexdigest() == value
               for name, value in pins.items())


def interrupted_restore(root, monkeypatch, *, after_pointer):
    applied = migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    identifier = applied['migration_id']
    with monkeypatch.context() as patch:
        if after_pointer:
            writer = migration.atomic_private_json
            def fail(path, value):
                if path.name == 'journal.json' and value.get('status') == 'restored':
                    raise OSError('controlled restore receipt interruption')
                return writer(path, value)
            patch.setattr(migration, 'atomic_private_json', fail)
        else:
            patch.setattr(migration, '_publish', lambda *a, **kw:
                          (_ for _ in ()).throw(OSError('controlled restore pointer interruption')))
        with pytest.raises(OSError, match='interruption'):
            migration.recover(root, identifier, action='restore')
    return identifier


@pytest.mark.parametrize('after_pointer', [False, True])
def test_restore_retry_uses_one_durable_generation_before_and_after_pointer(tmp_path, monkeypatch, after_pointer):
    root, scopes, _, _, account, _ = owned(tmp_path)
    session = account.login('fixture-admin', 'fixture-password-123')
    pins = original_pins(root)
    identifier = interrupted_restore(root, monkeypatch, after_pointer=after_pointer)
    journal = root / '.global-migrations' / identifier / 'journal.json'
    intent = json.loads(journal.read_bytes())
    assert intent['status'] == 'restoring', 'restore intent must precede pointer publication'
    generation = intent['restored_generation']
    directories = sorted(p.name for p in (root / '.global-generations').iterdir())
    result = migration.recover(root, identifier, action='restore')
    assert result == {'status': 'restored', 'generation_id': generation, 'fence': 2}
    pointer = (root / 'global-active.json').read_bytes()
    assert migration.recover(root, identifier, action='restore') == result
    assert (root / 'global-active.json').read_bytes() == pointer
    assert sorted(p.name for p in (root / '.global-generations').iterdir()) == directories
    unchanged(root, pins)
    from backend.engine.shared_accounts import AccountStore
    fresh = AccountStore(root / scopes['accounts'])
    with pytest.raises(ValueError, match='expired|unavailable'):
        fresh.authenticate(session['token'])


@pytest.mark.parametrize('after_pointer', [False, True])
def test_interrupted_restore_refuses_new_current_writes(tmp_path, monkeypatch, after_pointer):
    root, scopes, *_ = owned(tmp_path)
    identifier = interrupted_restore(root, monkeypatch, after_pointer=after_pointer)
    profiles = resolve_store_path(root / scopes['profiles'])
    profiles.write_text('{"profiles":[],"selected":null,"new_write":"must remain"}')
    pointer = (root / 'global-active.json').read_bytes()
    journal = root / '.global-migrations' / identifier / 'journal.json'
    before = journal.read_bytes()
    with pytest.raises(migration.GlobalMigrationError, match='write|changed|integrity|forward'):
        migration.recover(root, identifier, action='restore')
    assert (root / 'global-active.json').read_bytes() == pointer
    assert journal.read_bytes() == before
    assert json.loads(profiles.read_bytes())['new_write'] == 'must remain'


@pytest.mark.parametrize('after_pointer', [False, True])
def test_real_process_exit_then_fresh_cli_resumes_restore(tmp_path, after_pointer):
    root, _, *_ = owned(tmp_path)
    pins = original_pins(root)
    applied = migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    identifier = applied['migration_id']
    script = '''
import os,sys
from pathlib import Path
from backend.engine import global_migration as m
root=Path(sys.argv[1]);identifier=sys.argv[2]
if sys.argv[3]=='after':
    writer=m.atomic_private_json
    def fail(path,value):
        if path.name=='journal.json' and value.get('status')=='restored':os._exit(17)
        return writer(path,value)
    m.atomic_private_json=fail
else:
    def fail(*a,**kw):os._exit(17)
    m._publish=fail
m.recover(root,identifier,action='restore')
'''
    repo = Path(__file__).resolve().parents[2]
    failed = subprocess.run([sys.executable, '-c', script, str(root), identifier,
                             'after' if after_pointer else 'before'], cwd=repo)
    assert failed.returncode == 17
    result = subprocess.run([sys.executable, '-m', 'backend.engine.global_migration', 'recover',
                             '--root', str(root), '--migration-id', identifier, '--action', 'restore'],
                            cwd=repo, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    recovered = json.loads(result.stdout)
    assert recovered['status'] == 'restored'
    assert active_generation(root)[1]['generation_id'] == recovered['generation_id']
    unchanged(root, pins)


@pytest.mark.parametrize('mutation', ['identity', 'fence', 'payload', 'seal', 'backup', 'original'])
def test_pending_restore_refuses_changed_bindings_and_preserves_pointer(tmp_path, monkeypatch, mutation):
    root, scopes, *_ = owned(tmp_path)
    identifier = interrupted_restore(root, monkeypatch, after_pointer=False)
    journal = root / '.global-migrations' / identifier / 'journal.json'
    intent = json.loads(journal.read_bytes())
    stage = root / '.global-generations' / intent['restored_generation']
    if mutation in {'identity', 'fence'}:
        intent['restored_generation' if mutation == 'identity' else 'restored_fence'] = '../foreign' if mutation == 'identity' else True
        journal.write_text(json.dumps(intent))
    elif mutation == 'payload':
        (stage / scopes['profiles']).write_text('{"profiles":[],"selected":null,"tampered":true}')
    elif mutation == 'seal':
        seal_path = stage / '.global-generation.json'
        seal = json.loads(seal_path.read_bytes()); seal['installation_id'] = 'f' * 32
        seal_path.write_text(json.dumps(seal))
    elif mutation == 'backup':
        (journal.parent / 'original' / scopes['profiles']).write_text('{"profiles":[],"selected":null,"tampered":true}')
    else:
        (root / scopes['profiles']).write_text('{"profiles":[],"selected":null,"new_source_write":true}')
    pointer = (root / 'global-active.json').read_bytes()
    before = journal.read_bytes()
    with pytest.raises(migration.GlobalMigrationError):
        migration.recover(root, identifier, action='restore')
    assert (root / 'global-active.json').read_bytes() == pointer
    assert journal.read_bytes() == before


def test_pending_restore_never_follows_linked_generation(tmp_path, monkeypatch):
    root, _, *_ = owned(tmp_path)
    identifier = interrupted_restore(root, monkeypatch, after_pointer=False)
    journal = root / '.global-migrations' / identifier / 'journal.json'
    intent = json.loads(journal.read_bytes())
    stage = root / '.global-generations' / intent['restored_generation']
    preserved = stage.with_name(stage.name + '-preserved'); stage.rename(preserved)
    outside = tmp_path / 'outside'; outside.mkdir(); sentinel = outside / 'sentinel'
    sentinel.write_bytes(b'untouched'); stage.symlink_to(outside, target_is_directory=True)
    pointer = (root / 'global-active.json').read_bytes()
    with pytest.raises(migration.GlobalMigrationError, match='link'):
        migration.recover(root, identifier, action='restore')
    assert sentinel.read_bytes() == b'untouched'
    assert list(outside.iterdir()) == [sentinel]
    assert (root / 'global-active.json').read_bytes() == pointer


def test_successful_restore_replay_does_not_erase_new_account_or_advance(tmp_path):
    from backend.engine.shared_accounts import AccountStore
    root, scopes, *_ = owned(tmp_path)
    applied = migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    restored = migration.recover(root, applied['migration_id'], action='restore')
    account = AccountStore(root / scopes['accounts'])
    user = account.create_user('after-restore', 'fixture-password-456')
    pointer = (root / 'global-active.json').read_bytes()
    with pytest.raises(migration.GlobalMigrationError, match='changed|forward'):
        migration.recover(root, applied['migration_id'], action='restore')
    assert (root / 'global-active.json').read_bytes() == pointer
    forward = migration.advance(root, expected_source_sha256=migration.preview_forward(root)['source_sha256'])
    assert forward['fence'] == restored['fence'] + 1
    with pytest.raises(migration.GlobalMigrationError, match='changed'):
        migration.recover(root, applied['migration_id'], action='restore')
    assert user['id'] in {row['id'] for row in AccountStore(root / scopes['accounts']).users()}


@pytest.mark.parametrize('after_pointer', [False, True])
def test_forward_restore_retry_preserves_users_and_revokes_copied_sessions(tmp_path, monkeypatch, after_pointer):
    from backend.engine.shared_accounts import AccountStore
    root, scopes, *_ = owned(tmp_path)
    migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    account = AccountStore(root / scopes['accounts'])
    user = account.create_user('preserved-forward-user', 'fixture-password-456')
    session = account.login('fixture-admin', 'fixture-password-123')
    prior = resolve_store_path(root / scopes['accounts']); prior_bytes = prior.read_bytes()
    applied = migration.advance(root, expected_source_sha256=migration.preview_forward(root)['source_sha256'])
    identifier = applied['migration_id']
    with monkeypatch.context() as patch:
        if after_pointer:
            writer = migration.atomic_private_json
            def fail(path, value):
                if path.name == 'journal.json' and value.get('status') == 'restored':
                    raise OSError('controlled forward restore interruption')
                return writer(path, value)
            patch.setattr(migration, 'atomic_private_json', fail)
        else:
            patch.setattr(migration, '_publish', lambda *a, **kw:
                          (_ for _ in ()).throw(OSError('controlled forward restore interruption')))
        with pytest.raises(OSError, match='interruption'):
            migration.recover(root, identifier, action='restore')
    restored = migration.recover(root, identifier, action='restore')
    assert restored['fence'] == 3
    assert migration.recover(root, identifier, action='restore') == restored
    assert prior.read_bytes() == prior_bytes
    fresh = AccountStore(root / scopes['accounts'])
    assert user['id'] in {row['id'] for row in fresh.users()}
    with pytest.raises(ValueError, match='expired|unavailable'):
        fresh.authenticate(session['token'])


def test_retry_after_pointer_directory_sync_failure_orders_durability_before_completion(tmp_path, monkeypatch):
    root, _, *_ = owned(tmp_path)
    applied = migration.apply(root, expected_source_sha256=migration.preview(root)['source_sha256'])
    identifier = applied['migration_id']
    synchronize = migration._sync_directories
    with monkeypatch.context() as patch:
        def fail(directory, **kwargs):
            if directory == root:
                raise OSError('controlled pointer directory durability interruption')
            return synchronize(directory, **kwargs)
        patch.setattr(migration, '_sync_directories', fail)
        with pytest.raises(OSError, match='durability interruption'):
            migration.recover(root, identifier, action='restore')
    assert json.loads((root / '.global-migrations' / identifier / 'journal.json').read_bytes())['status'] == 'restoring'
    assert active_generation(root)[1]['generation_id'] != identifier
    calls = []
    writer = migration.atomic_private_json
    def observe_sync(directory, **kwargs):
        calls.append(directory)
        return synchronize(directory, **kwargs)
    def observe_write(path, value):
        if path.name == 'journal.json' and value.get('status') == 'restored':
            assert root in calls, 'confirm active pointer directory durability before completing restore'
        return writer(path, value)
    monkeypatch.setattr(migration, '_sync_directories', observe_sync)
    monkeypatch.setattr(migration, 'atomic_private_json', observe_write)
    assert migration.recover(root, identifier, action='restore')['status'] == 'restored'

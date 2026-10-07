"""Current-source privileged readers; controlled stores/bootstrap dependency only."""
import asyncio
from contextvars import Context, copy_context
import hashlib
import json
import os
import sqlite3
import threading

import pytest

from backend.engine.migration_guard import maintenance_guard, exclusive_admitted


@pytest.fixture
def staged(tmp_path):
    if os.name == 'nt':
        pytest.skip('Owned global generation initialization requires POSIX durability')
    from backend.engine.global_migration import initialize_owned
    from backend.engine.global_store_paths import SUPPORTED_SCOPES
    from backend.remote.profiles import ProfileStore, ComputeProfile
    root = tmp_path / 'owned'
    root.mkdir()
    initialize_owned(root, scopes=SUPPORTED_SCOPES)
    generation = root / '.global-generations' / ('a' * 32)
    generation.mkdir(parents=True)
    path = generation / SUPPORTED_SCOPES['profiles']
    path.write_text('{"profiles":[],"selected":null}', encoding='utf-8')
    profile = ComputeProfile(id='controlled', name='Controlled staged writer', ssh_target='controlled-host',
                             ssh_port=22, remote_root='/controlled', runtime_kind='python', runtime_value='python')
    return root, generation, ProfileStore(path), profile


@pytest.mark.parametrize('mode', ['unguarded', 'expired-staging', 'expired-exclusive', 'both-exited'])
def test_staged_profile_writer_requires_exact_live_exclusive_scope(staged, mode):
    from backend.engine.global_store_paths import staged_construction
    root, generation, store, profile = staged
    before = store.path.read_bytes()
    owner_before = (root / '.global-migration-owner.json').read_bytes()

    def refused(context=None):
        with pytest.raises(ValueError, match='generation|staged|restart'):
            if context is None:
                store.save(profile)
            else:
                context.run(store.save, profile)

    if mode == 'unguarded':
        with staged_construction(root, generation):
            refused()
    elif mode == 'expired-staging':
        with maintenance_guard(root, exclusive=True):
            with staged_construction(root, generation):
                copied = copy_context()
            assert exclusive_admitted(root)
            refused(copied)
    elif mode == 'expired-exclusive':
        original = maintenance_guard(root, exclusive=True)
        original.__enter__()
        scope = staged_construction(root, generation)
        scope.__enter__()
        try:
            original.__exit__(None, None, None)
            with maintenance_guard(root, exclusive=True):
                assert exclusive_admitted(root)
                # A new guard by the same owner cannot revive the old scope.
                refused()
        finally:
            scope.__exit__(None, None, None)
    else:
        with maintenance_guard(root, exclusive=True):
            with staged_construction(root, generation):
                copied = copy_context()
        assert not exclusive_admitted(root)
        refused(copied)
    assert store.path.read_bytes() == before
    assert (root / '.global-migration-owner.json').read_bytes() == owner_before
    assert not (root / 'global-active.json').exists()
    assert not (root / 'application-active.json').exists()
    print(json.dumps({'scenario': 'staged-profile-authority', 'mode': mode,
                      'unpublished_generation_write_refused': True, 'original_pointers_preserved': True}))


def predecessor(staged):
    from backend.engine.global_migration import _known_schemas, _schema
    from backend.engine.global_store_paths import SUPPORTED_SCOPES
    from backend.engine.historical_control_schema import KNOWN_PREDECESSORS
    root, generation, _, _ = staged
    known = _known_schemas(SUPPORTED_SCOPES)
    hashes = {}
    for version in KNOWN_PREDECESSORS:
        path = generation / SUPPORTED_SCOPES[version['scope']]
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            for _, _, _, sql in sorted(version['schema'], key=lambda row: row[0] != 'table'):
                db.execute(sql)
            if version['scope'] == 'ledger':
                db.execute("INSERT INTO jobs(id,workspace_id,project_key,project_id,actor_id,mode,kind,spec_sha256,spec_json,state,revision,source,created_ns,updated_ns) VALUES('job_preserved','w','pk','p','a','local','training','h','{}','completed',1,'controlled',1,2)")
            else:
                db.execute("INSERT INTO devices VALUES('controlled-host','cpu','controlled-cpu',NULL,1024)")
        assert _schema(path) != known[version['scope']]
        hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
    return known, hashes


@pytest.mark.parametrize('mode', ['unguarded', 'copied-thread', 'expired-context'])
def test_historical_converter_requires_its_original_live_staging_owner(staged, mode):
    from backend.engine.global_store_paths import staged_construction, SUPPORTED_SCOPES
    from backend.engine.historical_control_schema import normalize_staged
    root, generation, _, _ = staged
    known, hashes = predecessor(staged)

    def refused():
        with pytest.raises(ValueError, match='staged|exclusive|ownership'):
            normalize_staged(generation, SUPPORTED_SCOPES, known)

    if mode == 'unguarded':
        with staged_construction(root, generation):
            refused()
    elif mode == 'expired-context':
        with maintenance_guard(root, exclusive=True):
            with staged_construction(root, generation):
                copied = copy_context()
        copied.run(refused)
    else:
        failures = []
        def convert():
            try:
                assert not exclusive_admitted(root)
                refused()
            except BaseException as exc:
                failures.append(exc)
        with maintenance_guard(root, exclusive=True):
            with staged_construction(root, generation):
                copied = copy_context()
                thread = threading.Thread(target=copied.run, args=(convert,))
                thread.start()
                thread.join(15)
                assert not thread.is_alive()
        assert not failures
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == value for path, value in hashes.items())
    assert not (root / 'global-active.json').exists()
    print(json.dumps({'scenario': 'historical-predecessor-owner', 'mode': mode,
                      'actual_sqlite_bytes_preserved': True, 'normalization_refused': True}))


def test_original_owner_nested_staging_preserves_profile_and_predecessor_rows(staged):
    from backend.engine.global_store_paths import staged_construction, SUPPORTED_SCOPES
    from backend.engine.historical_control_schema import normalize_staged
    from backend.engine.global_migration import _schema
    root, generation, store, profile = staged
    known, _ = predecessor(staged)
    with maintenance_guard(root, exclusive=True):
        with staged_construction(root, generation):
            with staged_construction(root, generation):
                assert store.save(profile) == profile
                normalize_staged(generation, SUPPORTED_SCOPES, known)
            assert store.get(profile.id) == profile
    for name in ('ledger', 'leases'):
        assert _schema(generation / SUPPORTED_SCOPES[name]) == known[name]
    with sqlite3.connect(generation / SUPPORTED_SCOPES['ledger']) as db:
        assert db.execute("SELECT id,operation_json FROM jobs WHERE id='job_preserved'").fetchone() == ('job_preserved', None)
    with sqlite3.connect(generation / SUPPORTED_SCOPES['leases']) as db:
        assert db.execute('SELECT uuid FROM devices').fetchone() == ('controlled-cpu',)
    assert not (root / 'global-active.json').exists()
    print(json.dumps({'scenario': 'original-owner-nested-staging', 'actual_profiles_and_sqlite_rows_preserved': True}))


@pytest.mark.parametrize('mode', ['thread', 'async-task'])
def test_ready_reader_requires_owned_shared_admission_after_prevalidated_bootstrap(tmp_path, monkeypatch, mode):
    from backend.engine import application_launch_handshake as handshake
    root = tmp_path / 'controlled-bootstrap-root'
    root.mkdir()
    # Isolate the actual admission reader from independently tested descriptor
    # authentication. This is never native/backend handshake qualification.
    proof = {'schema_version': 1, 'kind': 'backend_claim', 'controlled_dependency': True}
    monkeypatch.setattr(handshake, 'early_backend_bootstrap', lambda: dict(proof))
    monkeypatch.setattr(handshake, '_CACHE', {'root': root.resolve(), 'ready': True})

    def probe():
        try:
            handshake.backend_bootstrap_ready()
        except handshake.HandshakeError as exc:
            return 'admitted shared' in str(exc)
        return False

    if mode == 'thread':
        results = []
        with maintenance_guard(root):
            assert handshake.backend_bootstrap_ready()['kind'] == 'backend_ready'
            copied = copy_context()
            thread = threading.Thread(target=lambda: results.append(copied.run(probe)))
            thread.start()
            thread.join(5)
            assert not thread.is_alive()
        assert results == [True]
    else:
        async def run():
            async def child():
                return probe()
            with maintenance_guard(root):
                assert handshake.backend_bootstrap_ready()['kind'] == 'backend_ready'
                return await asyncio.create_task(child())
        assert asyncio.run(run()) is True
    with maintenance_guard(root, exclusive=True):
        assert probe() is True
    assert Context().run(probe) is True
    print(json.dumps({'scenario': 'controlled-bootstrap-admission-reader', 'mode': mode,
                      'foreign_owner_refused': True, 'original_owner_shared_ready': True,
                      'native_descriptor_authentication_qualified': False}))

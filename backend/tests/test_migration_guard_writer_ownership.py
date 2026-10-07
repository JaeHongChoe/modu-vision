"""Actual cooperative writer admission; copied contexts do not own a lease."""
import asyncio
from contextlib import closing
from contextvars import Context, copy_context
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading

import pytest

from backend.engine.migration_guard import exclusive_admitted, maintenance_guard


def _exclusive(root):
    with maintenance_guard(root, exclusive=True):
        pass


@pytest.fixture
def owned(tmp_path):
    if os.name == 'nt':
        pytest.skip('Owned global store initialization requires POSIX durability')
    from backend.engine.global_migration import initialize_owned
    from backend.engine.global_store_paths import SUPPORTED_SCOPES
    from backend.remote.profiles import ComputeProfile, ProfileStore
    root = tmp_path / 'owned'
    root.mkdir()
    initialize_owned(root, scopes=SUPPORTED_SCOPES)
    path = root / SUPPORTED_SCOPES['profiles']
    path.write_text('{"profiles":[],"selected":null}', encoding='utf-8')
    profile = ComputeProfile(id='owned', name='Controlled writer', ssh_target='controlled-host',
                             ssh_port=22, remote_root='/controlled', runtime_kind='python',
                             runtime_value='/controlled/python')
    return root, ProfileStore(path), profile


def test_copied_context_profile_save_keeps_own_lock_after_parent_exit(owned, monkeypatch):
    root, store, profile = owned
    entered, release = threading.Event(), threading.Event()
    failures = []
    original_write = store._write
    before = store.path.read_bytes()
    owner_before = (root / '.global-migration-owner.json').read_bytes()

    def paused_write(data):
        entered.set()
        if not release.wait(10):
            raise AssertionError('Controlled profile writer was not released')
        original_write(data)

    def save():
        try:
            store.save(profile)
        except BaseException as exc:
            failures.append(exc)

    monkeypatch.setattr(store, '_write', paused_write)
    thread = None
    try:
        with maintenance_guard(root):
            copied = copy_context()
            thread = threading.Thread(target=copied.run, args=(save,))
            thread.start()
            assert entered.wait(5), 'Actual ProfileStore.save did not reach its write barrier'
        assert thread.is_alive()
        assert store.path.read_bytes() == before
        assert (root / '.global-migration-owner.json').read_bytes() == owner_before
        with pytest.raises(ValueError, match='writers|admission|drain'):
            Context().run(_exclusive, root)
        # Another ordinary reader is still allowed while the writer is paused.
        with maintenance_guard(root):
            assert store.path.read_bytes() == before
        assert not (root / 'global-active.json').exists()
        assert not (root / '.global-generations').exists()
    finally:
        release.set()
        if thread is not None:
            thread.join(12)
            assert not thread.is_alive(), 'Owned profile writer did not exit cooperatively'
    assert not failures
    assert store.get(profile.id) == profile
    Context().run(_exclusive, root)
    print(json.dumps({'scenario': 'copied-thread-profile-save', 'writer_committed': True,
                      'exclusive_refused_until_writer_exit': True, 'thread_exited': True}))


@pytest.mark.parametrize('mode', ['thread', 'async-task'])
def test_copied_owner_cannot_inherit_exclusive_capability(tmp_path, mode):
    root = tmp_path / 'project'
    root.mkdir()

    def probe():
        admitted = exclusive_admitted(root)
        try:
            with maintenance_guard(root):
                refusal = None
        except ValueError as exc:
            refusal = str(exc)
        return admitted, refusal

    if mode == 'thread':
        results = []
        with maintenance_guard(root, exclusive=True):
            copied = copy_context()
            thread = threading.Thread(target=lambda: results.append(copied.run(probe)))
            thread.start()
            thread.join(5)
            assert not thread.is_alive()
            assert exclusive_admitted(root)
        admitted, refusal = results[0]
    else:
        async def run():
            async def child():
                return probe()
            with maintenance_guard(root, exclusive=True):
                result = await asyncio.create_task(child())
                assert exclusive_admitted(root)
                return result
        admitted, refusal = asyncio.run(run())
    assert admitted is False, 'Copied context retained another execution owner\'s exclusive capability'
    assert refusal and 'admission' in refusal
    Context().run(_exclusive, root)


def test_async_child_sqlite_transaction_keeps_own_lock_after_parent_exit(owned):
    from backend.contracts.context import ContextRegistry
    root, _, _ = owned
    registry = ContextRegistry(root / 'projects')
    owner_before = (root / '.global-migration-owner.json').read_bytes()

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def write():
            with registry.transaction() as db:
                db.execute('INSERT INTO identities VALUES(?,?)', ('controlled_child', 'committed'))
                entered.set()
                await asyncio.wait_for(release.wait(), 10)

        task = None
        try:
            with maintenance_guard(root):
                task = asyncio.create_task(write())
                await asyncio.wait_for(entered.wait(), 5)
            assert not task.done()
            with closing(sqlite3.connect(registry.path)) as reader:
                assert reader.execute("SELECT value FROM identities WHERE name='controlled_child'").fetchone() is None
            with pytest.raises(ValueError, match='writers|admission|drain'):
                Context().run(_exclusive, root)
            assert (root / '.global-migration-owner.json').read_bytes() == owner_before
            assert not (root / 'global-active.json').exists()
        finally:
            release.set()
            if task is not None:
                await asyncio.wait_for(task, 12)

    asyncio.run(run())
    with registry._db() as reader:
        assert reader.execute("SELECT value FROM identities WHERE name='controlled_child'").fetchone() == ('committed',)
    Context().run(_exclusive, root)
    print(json.dumps({'scenario': 'copied-async-task-sqlite', 'transaction_committed': True,
                      'exclusive_refused_until_transaction_exit': True, 'task_exited': True}))


def test_same_execution_nested_exclusive_recovery_keeps_capability(owned):
    root, store, profile = owned
    # A controlled incomplete application marker normally refuses attachment.
    # Original exclusive recovery must still construct/use its own scoped store.
    (root / 'application-update-pending.json').write_text('{"controlled":"incomplete"}', encoding='utf-8')
    with maintenance_guard(root, exclusive=True):
        assert exclusive_admitted(root)
        with maintenance_guard(root):
            with maintenance_guard(root, exclusive=True):
                assert exclusive_admitted(root)
                assert store.save(profile) == profile
        assert exclusive_admitted(root)
    assert not exclusive_admitted(root)
    assert json.loads(store.path.read_text())['profiles'][0]['id'] == profile.id
    with pytest.raises(ValueError):
        store.list()
    (root / 'application-update-pending.json').unlink()
    assert store.get(profile.id) == profile
    Context().run(_exclusive, root)


def test_retained_context_cannot_transfer_capability_to_recycled_thread_id(tmp_path):
    root = tmp_path / 'retained-thread-project'
    root.mkdir()
    original_context = Context()
    retained = {}

    def hold():
        guard = maintenance_guard(root, exclusive=True)
        guard.__enter__()
        retained.update(guard=guard, ident=threading.get_ident(), thread=threading.current_thread())

    original = threading.Thread(target=original_context.run, args=(hold,))
    original.start()
    original.join(5)
    assert not original.is_alive()
    assert retained['thread'] is original
    observed = None
    try:
        # A caller retains the exact context/guard even after its real Thread
        # exits. A different real Thread may reuse the numeric identifier.
        for attempt in range(8):
            result = {}

            def probe():
                result.update(ident=threading.get_ident(), thread=threading.current_thread(),
                              inherited_exclusive=exclusive_admitted(root))
                try:
                    with maintenance_guard(root):
                        result['shared_refused'] = False
                except ValueError:
                    result['shared_refused'] = True

            copied = original_context.copy()
            replacement = threading.Thread(target=copied.run, args=(probe,))
            replacement.start()
            replacement.join(5)
            assert not replacement.is_alive()
            assert result['thread'] is replacement and replacement is not original
            if result['ident'] == retained['ident']:
                observed = {'scenario': 'actual-recycled-thread-id', 'attempt': attempt + 1,
                            'numeric_id_reused': True, 'distinct_real_thread': True,
                            'inherited_exclusive': result['inherited_exclusive'],
                            'shared_refused': result['shared_refused']}
                print(json.dumps(observed))
                assert observed['inherited_exclusive'] is False
                assert observed['shared_refused'] is True
                break
        if observed is None:
            pytest.skip('This host did not recycle a real Thread identifier in eight sequential threads')
    finally:
        # Close only the exact guard retained by this test, in the Context
        # where its token was created; no guessed/inherited descriptor cleanup.
        original_context.run(retained['guard'].__exit__, None, None, None)
    Context().run(_exclusive, root)


_FORK_PROBE = r'''
import json, os, select, sys, time
from contextvars import Context
sys.path.insert(0, sys.argv[1])
from backend.engine.migration_guard import maintenance_guard, exclusive_admitted
root, mode = sys.argv[2:]
to_child_read, to_child_write = os.pipe()
to_parent_read, to_parent_write = os.pipe()
def read(fd):
    if not select.select([fd], [], [], 10)[0]:
        raise RuntimeError('Controlled fork peer exceeded its cooperative deadline')
    value = os.read(fd, 4096)
    if not value:
        raise RuntimeError('Controlled fork peer closed its pipe')
    return json.loads(value)
def send(fd, value):
    os.write(fd, json.dumps(value).encode())
def exclusive():
    with maintenance_guard(root, exclusive=True):
        pass
receipt = {}
with maintenance_guard(root, exclusive=mode == 'exclusive'):
    child = os.fork()
    if child == 0:
        os.close(to_child_write); os.close(to_parent_read)
        try:
            inherited = exclusive_admitted(root)
            if mode == 'exclusive':
                try:
                    with maintenance_guard(root):
                        refused = False
                except ValueError:
                    refused = True
                send(to_parent_write, {'inherited_exclusive': inherited, 'shared_refused': refused})
            else:
                with maintenance_guard(root):
                    send(to_parent_write, {'child_entered': True, 'inherited_exclusive': inherited})
                    assert read(to_child_read) == {'release': True}
            os._exit(0)
        except BaseException as exc:
            send(to_parent_write, {'child_error': type(exc).__name__ + ': ' + str(exc)})
            os._exit(1)
    os.close(to_child_read); os.close(to_parent_write)
    try:
        receipt.update(read(to_parent_read))
    except BaseException:
        os.close(to_child_write)
        raise
try:
    if mode == 'shared':
        try:
            Context().run(exclusive)
            receipt['exclusive_refused_after_parent_exit'] = False
        except ValueError:
            receipt['exclusive_refused_after_parent_exit'] = True
finally:
    if mode == 'shared':
        send(to_child_write, {'release': True})
    os.close(to_child_write); os.close(to_parent_read)
deadline = time.monotonic() + 12
while True:
    reaped, status = os.waitpid(child, os.WNOHANG)
    if reaped:
        receipt['child_exit'] = os.waitstatus_to_exitcode(status)
        break
    if time.monotonic() >= deadline:
        raise RuntimeError('Owned fork did not exit cooperatively')
    time.sleep(.01)
Context().run(exclusive)
receipt['exclusive_after_child_exit'] = True
receipt['scenario'] = 'actual-posix-fork-' + mode
print(json.dumps(receipt))
'''


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='Actual fork ownership requires POSIX fork')
@pytest.mark.parametrize('mode', ['shared', 'exclusive'])
def test_actual_fork_does_not_borrow_parent_admission(tmp_path, mode):
    root = tmp_path / 'fork-project'
    root.mkdir()
    # Fork a fresh minimal interpreter, not pytest's backend/ML import process.
    # Pipe deadlines and normal exits own teardown; no termination signals.
    result = subprocess.run([sys.executable, '-I', '-B', '-c', _FORK_PROBE,
                             str(Path(__file__).resolve().parents[2]), str(root), mode],
                            capture_output=True, text=True, check=True)
    receipt = json.loads(result.stdout)
    print(json.dumps(receipt))
    assert receipt['child_exit'] == 0
    assert receipt['inherited_exclusive'] is False
    assert receipt['exclusive_after_child_exit'] is True
    if mode == 'shared':
        assert receipt['child_entered'] is True
        assert receipt['exclusive_refused_after_parent_exit'] is True
    else:
        assert receipt['shared_refused'] is True

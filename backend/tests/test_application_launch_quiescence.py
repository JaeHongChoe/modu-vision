"""Owned writer-lock authority fixtures; no launch unlock or complete-tree claim."""
import importlib
import copy
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest


def api():
    try:
        return importlib.import_module('backend.engine.application_launch_quiescence')
    except ModuleNotFoundError as exc:
        if exc.name == 'backend.engine.application_launch_quiescence':
            pytest.fail('The owned writer-epoch admission core is missing')
        raise


@pytest.fixture
def epoch(tmp_path, monkeypatch):
    q = api()
    from backend.tests.test_staged_update_canary import controlled_proof
    from backend.tests.test_application_launch_lease import installed, reserve
    from backend.engine import application_launch_lease as lease, runtime_update as update
    controlled_proof(monkeypatch)  # inert signed app/DB fixture, never model/native execution
    root, value, _ = installed(tmp_path)
    owner = reserve(root, value)
    row = lease._load(root)
    launch_sha = update._sha(update._canonical(row))
    authority = q.WriterEpoch.create(root, row['nonce'], expected_launch_sha256=launch_sha)
    originals = {name: (root/name).read_bytes() for name in ('.global-migration-owner.json', 'global-active.json')}
    try:
        yield q, root, owner, authority
    finally:
        # Test-only deliberate corruption is restored to its original bytes for
        # never-spawned fixture cleanup; the production core never repairs it.
        for name, raw in originals.items():
            if (root/name).read_bytes() != raw: (root/name).write_bytes(raw)
        owner.cancel()
        owner.close()


def digest(authority):
    return authority.snapshot()['registry_sha256']


def enroll(authority, role='owned_cpu_worker'):
    return authority.enroll(role, expected_registry_sha256=digest(authority))


def proof(authority):
    return authority.enrolled_writer_fence(expected_registry_sha256=digest(authority))


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*')
            if p.is_file() and 'sqlite' not in p.name}


def test_empty_closed_epoch_is_only_a_limited_enrolled_lock_candidate(epoch):
    q, root, owner, authority = epoch
    authority.close_epoch(expected_registry_sha256=digest(authority))
    before = tree(root)
    with proof(authority) as candidate:
        assert candidate['scope'] == 'enrolled_writer_lifetime_locks_only'
        assert candidate['enrolled_writer_count'] == 0
        assert candidate['all_enrolled_lock_references_closed'] is True
        assert candidate['can_release_launch_lease'] is False
        assert candidate['process_tree_exit_verified'] is False
        assert candidate['application_shutdown_verified'] is False
        from backend.engine.application_launch_lease import assert_quiescent
        with pytest.raises(ValueError, match='launch ownership'):
            assert_quiescent(root)
    assert tree(root) == before


def test_live_enrolled_writer_and_reserved_spawn_both_block(epoch):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    with q.writer_guard(root, owner.nonce, registration.writer_id,
                        expected_registration_sha256=registration.registration_sha256):
        authority.close_epoch(expected_registry_sha256=digest(authority))
        with pytest.raises(q.QuiescenceError, match='reserved|active|unresolved'):
            with proof(authority):
                pytest.fail('A reserved live writer was treated as drained')
    with pytest.raises(q.QuiescenceError, match='reserved|unresolved'):
        with proof(authority):
            pytest.fail('Closing a lock cannot invent original child exit evidence')


@pytest.mark.parametrize('status', ['unsupported', 'uncertain'])
def test_unsupported_and_uncertain_writer_never_expire_or_unlock(epoch, status, monkeypatch):
    q, root, owner, authority = epoch
    if status == 'unsupported':
        authority.block_unsupported('local_training', expected_registry_sha256=digest(authority))
    else:
        registration = enroll(authority)
        authority.mark_uncertain(registration.writer_id, reason_code='handoff_interrupted',
                                 expected_registry_sha256=digest(authority))
    authority.close_epoch(expected_registry_sha256=digest(authority))
    before = tree(root)
    # The fence must not turn absence, reuse or wall-clock expiry into proof.
    monkeypatch.setattr(q, '_identity', lambda *_: pytest.fail('Fence queried a PID instead of retained lock authority'))
    monkeypatch.setattr(time, 'time', lambda: 10**30)
    with pytest.raises(q.QuiescenceError, match=status):
        with proof(authority):
            pytest.fail('Ambiguous writer was released')
    assert tree(root) == before


def test_close_epoch_refuses_late_enrollment_and_old_registration_admission(epoch):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    authority.close_epoch(expected_registry_sha256=digest(authority))
    before = tree(root)
    with pytest.raises(q.QuiescenceError, match='closed'):
        enroll(authority)
    with pytest.raises(q.QuiescenceError, match='closed'):
        with q.writer_guard(root, owner.nonce, registration.writer_id,
                            expected_registration_sha256=registration.registration_sha256):
            pytest.fail('An old registration admitted a new writer after close')
    assert tree(root) == before


def test_registry_cas_refuses_stale_enrollment_without_partial_writer(epoch):
    q, root, owner, authority = epoch
    original = digest(authority)
    enroll(authority)
    before = tree(root)
    with pytest.raises(q.QuiescenceError, match='CAS|changed|snapshot'):
        authority.enroll('backend', expected_registry_sha256=original)
    assert tree(root) == before


@pytest.mark.parametrize('point', ['after_writer_lock', 'after_registry_journal'])
def test_partial_writer_publication_is_sticky_and_inspection_never_repairs(epoch, monkeypatch, point):
    q, root, owner, authority = epoch
    def interrupt(at):
        if at == point:
            raise OSError('controlled publication interruption')
    monkeypatch.setattr(q, '_checkpoint', interrupt)
    with pytest.raises(OSError, match='controlled'):
        enroll(authority)
    before = tree(root)
    with pytest.raises(q.QuiescenceError, match='partial|publication|member|CAS|changed'):
        q.inspect_epoch(root, owner.nonce)
    with pytest.raises(q.QuiescenceError):
        authority.close_epoch(expected_registry_sha256='f'*64)
    assert tree(root) == before


@pytest.mark.parametrize('change', ['nonce', 'installation', 'database', 'launch_cas', 'registration_pin'])
def test_exact_launch_installation_database_and_registration_binding(epoch, change):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    args = [root, owner.nonce, registration.writer_id]
    pin = registration.registration_sha256
    if change == 'nonce':
        args[1] = 'f'*32
    elif change == 'registration_pin':
        pin = 'f'*64
    elif change == 'installation':
        path = root/'.global-migration-owner.json'
        row = json.loads(path.read_bytes()); row['installation_id'] = 'f'*32
        path.write_text(json.dumps(row))
    elif change == 'database':
        path = root/'global-active.json'
        row = json.loads(path.read_bytes()); row['fence'] += 1
        path.write_text(json.dumps(row))
    else:
        with pytest.raises(q.QuiescenceError, match='CAS|changed|snapshot'):
            q.WriterEpoch.create(root, owner.nonce, expected_launch_sha256='f'*64)
        return
    before = tree(root)
    with pytest.raises((q.QuiescenceError, ValueError), match='nonce|binding|installation|database|pin|foreign|global generation'):
        with q.writer_guard(*args, expected_registration_sha256=pin):
            pytest.fail('A foreign writer binding was admitted')
    assert tree(root) == before


def test_inherited_descendant_lock_survives_original_leader_exit(epoch):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    code = '''import os,sys
from backend.engine.application_launch_quiescence import writer_guard
with writer_guard(sys.argv[1],sys.argv[2],sys.argv[3],expected_registration_sha256=sys.argv[4]) as guard:
 print('admitted',flush=True)
 assert sys.stdin.buffer.read(1)==b'f'
 pid=os.fork()
 if pid==0:
  print('descendant-held',flush=True)
  assert sys.stdin.buffer.read(1)==b'x'
  os._exit(0)
 # close only this reference; inherited descendant remains authoritative
'''
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv.pop(1));'+code,
        str(Path(__file__).resolve().parents[2]), str(root), owner.nonce,
        registration.writer_id, registration.registration_sha256],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == b'admitted'
        authority.bind_original_child(registration.writer_id, child,
                                      expected_registry_sha256=digest(authority))
        authority.close_epoch(expected_registry_sha256=digest(authority))
        child.stdin.write(b'f'); child.stdin.flush()
        assert select.select([child.stdout], [], [], 10)[0]
        assert child.stdout.readline().strip() == b'descendant-held'
        assert child.wait(timeout=5) == 0
        authority.observe_original_child_exit(registration.writer_id,
                                              expected_registry_sha256=digest(authority))
        before = tree(root)
        with pytest.raises(q.QuiescenceError, match='held|reference|writer lock'):
            with proof(authority):
                pytest.fail('Direct leader exit erased the descendant lifetime lock')
        assert tree(root) == before
        child.stdin.write(b'x'); child.stdin.flush(); child.stdin.close()
        # Only kernel release of all original lock references makes this limited
        # candidate available. No PID sample or descendant signal is used.
        until = time.monotonic()+5
        while True:
            try:
                with proof(authority) as candidate:
                    assert candidate['enrolled_writer_count'] == 1
                    assert candidate['process_tree_exit_verified'] is False
                    assert candidate['can_release_launch_lease'] is False
                break
            except q.QuiescenceError:
                if time.monotonic() >= until: raise
                time.sleep(.025)
    finally:
        if child.stdin and not child.stdin.closed:
            try: child.stdin.write(b'x'); child.stdin.flush(); child.stdin.close()
            except (BrokenPipeError, OSError): pass
        if child.poll() is None:
            child.kill(); child.wait(timeout=5)  # this fixture's original handle only
        assert child.returncode == 0, child.stderr.read(4096)


def test_caller_exit_json_or_reopened_authority_cannot_complete_a_writer(epoch):
    q, root, owner, authority = epoch
    registration = enroll(authority)
    before = tree(root)
    with pytest.raises((q.QuiescenceError, TypeError), match='original|handle'):
        authority.bind_original_child(registration.writer_id, {'pid': os.getpid(), 'returncode': 0},
                                      expected_registry_sha256=digest(authority))
    with pytest.raises(q.QuiescenceError, match='original|handle'):
        authority.observe_original_child_exit(registration.writer_id,
                                              expected_registry_sha256=digest(authority))
    with pytest.raises(q.QuiescenceError, match='existing|partial|reopen'):
        from backend.engine import application_launch_lease as lease, runtime_update as update
        q.WriterEpoch.create(root, owner.nonce,
            expected_launch_sha256=update._sha(update._canonical(lease._load(root))))
    assert tree(root) == before


def test_foreign_process_cannot_reconstruct_authority_from_current_registry_bytes(epoch):
    q, root, owner, authority = epoch
    enroll(authority)
    before = tree(root)
    code = '''import json,sys
from backend.engine.application_launch_quiescence import WriterEpoch,inspect_epoch,EPOCHS
from pathlib import Path
root=Path(sys.argv[1]);nonce=sys.argv[2];directory=root/EPOCHS/nonce
try:
 snapshot=inspect_epoch(root,nonce)
 value=snapshot['registry']
 reopened=WriterEpoch(root,nonce,value['binding'],value,directory.joinpath('registry.json').read_bytes(),directory.joinpath('publication.json').read_bytes())
 reopened.close_epoch(expected_registry_sha256=snapshot['registry_sha256'])
except (ValueError,TypeError):print(json.dumps({'refused':True}),flush=True)
else:print(json.dumps({'refused':False}),flush=True)
'''
    result = subprocess.run([sys.executable, '-I', '-B', '-c',
        'import sys;sys.path.insert(0,sys.argv.pop(1));'+code,
        str(Path(__file__).resolve().parents[2]), str(root), owner.nonce],
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'refused': True}
    assert tree(root) == before


def test_copied_authority_instance_cannot_close_original_epoch(epoch):
    q, root, owner, authority = epoch
    clone = copy.copy(authority)
    before = tree(root)
    with pytest.raises(q.QuiescenceError, match='original|instance|capability'):
        clone.close_epoch(expected_registry_sha256=digest(authority))
    assert tree(root) == before


def test_unknown_writer_directory_enumeration_is_bounded_and_read_only(epoch, monkeypatch):
    q, root, owner, authority = epoch
    writers = root/q.EPOCHS/owner.nonce/'writers'
    for i in range(q.MAX_WRITERS+3):
        (writers/f'{i:032x}').mkdir(mode=0o700)
    before = tree(root)
    original = os.scandir
    visited = []

    class ObservedEntries:
        def __init__(self, entries): self.entries = entries
        def __enter__(self): return self
        def __exit__(self, *args): self.entries.close()
        def __iter__(self): return self
        def __next__(self):
            entry = next(self.entries)
            visited.append(entry.name)
            return entry
        def close(self): self.entries.close()

    def observe(path):
        entries = original(path)
        return ObservedEntries(entries) if Path(path) == writers else entries

    with monkeypatch.context() as patch:
        patch.setattr(os, 'scandir', observe)
        with pytest.raises(q.QuiescenceError, match='unknown|partial|member|bound'):
            q.inspect_epoch(root, owner.nonce)
    assert visited and len(visited) <= q.MAX_WRITERS+1
    assert tree(root) == before

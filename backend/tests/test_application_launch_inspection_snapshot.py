"""Deterministic ordinary-error snapshot controls over owned temporary files.

These controls grant no lease/process/model/native/quality/release authority.
The real reserved-lease controls start no process. Existing managed cleanup
fixtures and their original 5/7/5 second deadlines are not changed.
"""
import json

import pytest

from backend.engine import application_launch_controller as controller
from backend.engine import application_launch_lease as lease
from backend.engine import runtime_update as update
from backend.engine.application_launch_handshake import HandshakeError
# Collect the exact original lease module's autouse controlled-canary fixture.
# This remains test-only staged publication; no native/source-worker proof.
from backend.tests.test_application_launch_lease import controlled_canary_publication


UNKNOWN = 'Application launch ownership requires recovery: unknown lease history member'
CHANGE = {
    'namespace': 'Launch sidecar publication changed during read-only inspection',
    'pointer': 'Launch publication changed during read-only inspection',
    'receipt': 'Launch journal or receipt changed during read-only inspection',
}


def snapshot_root(tmp_path):
    # A minimal real-file snapshot control, explicitly not an owned lease.
    root = tmp_path / 'snapshot-control'; root.mkdir()
    nonce = 'a' * 32
    directory = root / lease.LEASES / nonce; directory.mkdir(parents=True)
    pointer = root / lease.ACTIVE_LEASE
    pointer.write_text(json.dumps({'nonce': nonce, 'revision': 1}))
    (directory / 'journal.json').write_bytes(b'{"control": 1}')
    (directory / 'bootstrap-receipt.json').write_bytes(b'{"receipt_control": 1}')
    return root, directory, pointer


def file_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob('*') if p.is_file()}


def mutate(kind, directory, pointer):
    if kind == 'namespace': (directory / 'unexpected.json').write_bytes(b'unknown')
    elif kind == 'pointer': pointer.write_text(json.dumps({'nonce': 'a' * 32, 'revision': 2}))
    elif kind == 'receipt': (directory / 'bootstrap-receipt.json').write_bytes(b'{"receipt_control": 2}')
    elif kind is not None: raise AssertionError(kind)


def body_error(kind):
    return lease.LaunchLeaseError(UNKNOWN) if kind == 'unknown' else ValueError('malformed controlled body')


@pytest.mark.parametrize('kind', ['namespace', 'pointer', 'receipt'])
@pytest.mark.parametrize('error_kind', ['unknown', 'malformed'])
def test_observed_change_is_classified_after_ordinary_body_error(tmp_path, kind, error_kind):
    root, directory, pointer = snapshot_root(tmp_path)
    original = body_error(error_kind)
    with pytest.raises(HandshakeError) as result:
        with controller._inspection_snapshot(root):
            mutate(kind, directory, pointer)
            changed = file_bytes(root)
            raise original
    assert str(result.value) == CHANGE[kind] and result.value is not original
    assert file_bytes(root) == changed  # Inspector does not write or recover.


@pytest.mark.parametrize('error_kind', ['unknown', 'malformed'])
@pytest.mark.parametrize('stable_unknown', [False, True])
def test_stable_ordinary_body_error_is_same_object_and_bytes(tmp_path, error_kind, stable_unknown):
    root, directory, _ = snapshot_root(tmp_path)
    if stable_unknown: (directory / 'stable-unknown.json').write_bytes(b'unknown')
    before = file_bytes(root); original = body_error(error_kind)
    with pytest.raises(type(original)) as result:
        with controller._inspection_snapshot(root): raise original
    assert result.value is original and file_bytes(root) == before


@pytest.mark.parametrize('kind', [None, 'namespace', 'pointer', 'receipt'])
def test_original_success_path_and_change_refusals_are_preserved(tmp_path, kind):
    root, directory, pointer = snapshot_root(tmp_path)
    before = file_bytes(root); seen = []
    if kind is None:
        with controller._inspection_snapshot(root): seen.append('body')
        assert seen == ['body'] and file_bytes(root) == before
    else:
        with pytest.raises(HandshakeError) as result:
            with controller._inspection_snapshot(root): mutate(kind, directory, pointer)
        assert str(result.value) == CHANGE[kind]


@pytest.mark.parametrize('error_type', [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize('kind', [None, 'namespace', 'pointer', 'receipt'])
def test_original_body_interrupt_priority_skips_postread(tmp_path, monkeypatch, error_type, kind):
    root, directory, pointer = snapshot_root(tmp_path)
    original_read = update._read; reads = []
    def observed(path, *args, **kwargs):
        raw = original_read(path, *args, **kwargs); reads.append((path, raw)); return raw
    monkeypatch.setattr(update, '_read', observed)
    original = error_type('original controlled body interrupt')
    with pytest.raises(error_type) as result:
        with controller._inspection_snapshot(root):
            read_count = len(reads)
            mutate(kind, directory, pointer)
            changed = file_bytes(root)
            raise original
    assert result.value is original and len(reads) == read_count
    assert file_bytes(root) == changed


@pytest.mark.parametrize('body_raises', [False, True])
@pytest.mark.parametrize('guard_type', [KeyboardInterrupt, SystemExit, update.UpdateError])
def test_fresh_postread_guard_error_keeps_its_exact_identity(tmp_path, monkeypatch, body_raises, guard_type):
    root, _, pointer = snapshot_root(tmp_path)
    original_read = update._read; entered = False; postreads = []
    guard_error = guard_type('original controlled postread guard error')
    def observed(path, *args, **kwargs):
        raw = original_read(path, *args, **kwargs)
        if entered and path == pointer:
            postreads.append((path, raw)); raise guard_error
        return raw
    monkeypatch.setattr(update, '_read', observed)
    before = file_bytes(root)
    with pytest.raises(guard_type) as result:
        with controller._inspection_snapshot(root):
            entered = True
            if body_raises: raise lease.LaunchLeaseError(UNKNOWN)
    assert result.value is guard_error and postreads == [(pointer, before[lease.ACTIVE_LEASE])]
    assert file_bytes(root) == before


@pytest.mark.parametrize('body_raises', [False, True])
def test_real_postread_symlink_guard_is_not_a_retryable_change(tmp_path, body_raises):
    root, directory, _ = snapshot_root(tmp_path)
    target = tmp_path / 'original-controlled-target.json'; target.write_bytes(b'{}')
    with pytest.raises(update.UpdateError) as result:
        with controller._inspection_snapshot(root):
            (directory / 'journal.json').unlink()
            (directory / 'journal.json').symlink_to(target)
            if body_raises: raise lease.LaunchLeaseError(UNKNOWN)
    assert not isinstance(result.value, HandshakeError)
    assert (directory / 'journal.json').is_symlink() and target.read_bytes() == b'{}'


def reserved_root(tmp_path):
    from backend.tests.test_application_launch_lease import installed, reserve
    root, value, _ = installed(tmp_path)
    owner = reserve(root, value)
    assert owner._process is None
    return root, root / lease.LEASES / owner.nonce


def test_real_stable_unknown_history_member_is_rejected_not_retried(tmp_path):
    root, directory = reserved_root(tmp_path)
    (directory / 'stable-unknown.json').write_bytes(b'unknown')
    before = file_bytes(root); raised = []
    with pytest.raises(lease.LaunchLeaseError) as result:
        with controller._inspection_snapshot(root):
            try: lease._load(root)
            except lease.LaunchLeaseError as exc: raised.append(exc); raise
    assert len(raised) == 1 and result.value is raised[0] and str(result.value) == UNKNOWN
    assert file_bytes(root) == before


@pytest.mark.parametrize('malformed', [b'{', b'{}'])
def test_real_stable_malformed_journal_keeps_exact_body_error(tmp_path, malformed):
    root, directory = reserved_root(tmp_path)
    (directory / 'journal.json').write_bytes(malformed)
    before = file_bytes(root); raised = []
    with pytest.raises((update.UpdateError, lease.LaunchLeaseError)) as result:
        with controller._inspection_snapshot(root):
            try: lease._load(root)
            except (update.UpdateError, lease.LaunchLeaseError) as exc: raised.append(exc); raise
    assert len(raised) == 1 and result.value is raised[0] and file_bytes(root) == before


def test_real_unknown_member_removed_during_error_is_observed_change(tmp_path):
    root, directory = reserved_root(tmp_path)
    unknown = directory / 'controlled-unpublished-sidecar.json'; unknown.write_bytes(b'unknown')
    raised = []
    with pytest.raises(HandshakeError) as result:
        with controller._inspection_snapshot(root):
            try: lease._load(root)
            except lease.LaunchLeaseError as exc:
                raised.append(exc); unknown.unlink(); changed = file_bytes(root); raise
    assert len(raised) == 1 and str(raised[0]) == UNKNOWN
    assert str(result.value) == CHANGE['namespace'] and result.value is not raised[0]
    assert file_bytes(root) == changed


def test_malformed_snapshot_pointer_refuses_before_body(tmp_path):
    root, _, pointer = snapshot_root(tmp_path)
    pointer.write_bytes(b'{"nonce":"invalid"}')
    before = file_bytes(root); entered = []
    with pytest.raises(HandshakeError, match='Invalid read-only launch pointer'):
        with controller._inspection_snapshot(root): entered.append('body')
    assert entered == [] and file_bytes(root) == before

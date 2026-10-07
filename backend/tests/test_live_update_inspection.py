"""Actual shared OS fence permits readback without admitting cutover or recovery."""
import hashlib
from pathlib import Path

import pytest

from backend.tests.test_application_launch_lease import installed, backend_lifetime_admission


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob('*') if p.is_file()}


def test_committed_inspection_reopens_under_live_backend_fence_without_writes(tmp_path):
    from backend.engine import runtime_update as update
    root, value, current = installed(tmp_path)
    before = snapshot(root)
    with backend_lifetime_admission(root):
        observed = update.inspect_update(root, value['authority'],
            pinned_authority_sha256=value['pinned_authority_sha256'])
        assert observed['status'] == 'committed'
        assert observed['update_id'] == current['update_id']
        assert observed['database_fence'] == current['database_pointer']['fence']
        assert observed['application_started'] is False
        with pytest.raises(ValueError, match='admission'):
            update.recover_update(root, current['update_id'])
    assert snapshot(root) == before


def test_pending_inspection_still_requires_exclusive_and_never_repairs(tmp_path, monkeypatch):
    from backend.engine import runtime_update as update
    from backend.tests.test_service_s6_04 import fixture, plan
    root, value, current = installed(tmp_path)
    next_value = fixture(tmp_path, version='1.1.0', key=value['key'], authority=value['authority'])
    next_value['target']['current_version'] = '1.0.0'
    proposed = plan(root, next_value)
    def interrupt(point):
        if point == 'before_database': raise KeyboardInterrupt('controlled interruption')
    monkeypatch.setattr(update, '_checkpoint', interrupt)
    with pytest.raises(KeyboardInterrupt): update.install_update(root, proposed)
    before = snapshot(root)
    with backend_lifetime_admission(root):
        with pytest.raises(ValueError, match='admission'):
            update.inspect_update(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])
    observed = update.inspect_update(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])
    assert observed['status'] == 'recovery_required'
    assert snapshot(root) == before


def test_pending_pointer_appearing_before_shared_admission_refuses_without_readback(tmp_path, monkeypatch):
    from backend.engine import runtime_update as update
    root, value, current = installed(tmp_path)
    from contextlib import contextmanager
    original = update.store_admission
    @contextmanager
    def race(path, *, exclusive=False):
        assert exclusive is False
        (root/update.PENDING).write_bytes(update._canonical({'schema_version': 1,
            'installation_id': update._root(root)[1]['installation_id'], 'update_id': current['update_id']}))
        with original(path, exclusive=exclusive): yield
    monkeypatch.setattr(update, 'store_admission', race)
    with pytest.raises(ValueError, match='update|recovery'):
        update.inspect_update(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])

"""Per-launch private homes cannot become migrated or adopted customer data."""
from pathlib import Path

import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_installed_home_adoption import installed, original_bytes

CONTROL = '.application-runtime-homes'


def test_actual_migration_keeps_launch_cache_outside_customer_inventory(tmp_path):
    from backend.engine import global_migration as migration
    root, *_ = owned(tmp_path)
    before = migration._snapshot(root)['inventory']
    cache = root/CONTROL/('a'*32)/'cache'
    cache.mkdir(parents=True, mode=0o700)
    artifact = cache/'controlled-private-runtime.json'
    artifact.write_bytes(b'{"scope":"original-launch-cache"}')
    assert migration._snapshot(root)['inventory'] == before
    assert artifact.read_bytes() == b'{"scope":"original-launch-cache"}'


def test_actual_adoption_refuses_orphaned_runtime_home_without_creating_owner(tmp_path):
    from backend.engine import installed_home_adoption as adoption
    root, scopes, _ = installed(tmp_path)
    assert adoption.preview_installed_home(root, scopes=scopes)['can_adopt'] is True
    (root/CONTROL).mkdir(mode=0o700)
    before = original_bytes(root)
    members = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    result = adoption.preview_installed_home(root, scopes=scopes)
    assert result['can_adopt'] is False
    assert any(CONTROL in message for message in result['blockers'])
    assert original_bytes(root) == before
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == members
    assert not (root/adoption.CONTROL).exists()


@pytest.mark.parametrize('dangling', [False, True])
def test_owned_root_refuses_runtime_home_link_before_foreign_read(tmp_path, monkeypatch, dangling):
    from backend.engine.global_store_paths import owned_root, OWNER_FILE
    root, scopes, *_ = owned(tmp_path)
    foreign = tmp_path/'foreign-runtime'
    if not dangling:
        foreign.mkdir()
        (foreign/'untouched.bin').write_bytes(b'original foreign bytes')
    link = root/CONTROL
    link.symlink_to(foreign, target_is_directory=True)
    owner_before = (root/OWNER_FILE).read_bytes()
    read = Path.read_bytes
    def reject_foreign(path):
        if path == foreign or foreign in path.parents:
            pytest.fail('Runtime control link caused a foreign read')
        return read(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', reject_foreign)
        with pytest.raises(ValueError, match='control.*links|linked'):
            owned_root(root/scopes['profiles'])
    assert link.is_symlink() and link.readlink() == foreign
    assert (root/OWNER_FILE).read_bytes() == owner_before
    if dangling:
        assert not foreign.exists()
    else:
        assert (foreign/'untouched.bin').read_bytes() == b'original foreign bytes'


def test_orphaned_launch_home_is_not_quiescent_unowned_installation(tmp_path):
    from backend.engine.application_launch_lease import assert_quiescent, LaunchLeaseError
    root = tmp_path/'unowned'; root.mkdir()
    (root/CONTROL).mkdir(mode=0o700)
    with pytest.raises(LaunchLeaseError, match='unowned launch control'):
        assert_quiescent(root)

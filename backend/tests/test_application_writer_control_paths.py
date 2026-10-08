"""Durable writer authority is control state, never adopted customer payload."""
from pathlib import Path

import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_installed_home_adoption import installed, original_bytes

WRITER_CONTROL = '.application-writer-epochs'


def test_writer_history_is_excluded_from_actual_global_inventory(tmp_path):
    from backend.engine import global_migration as migration
    root, *_ = owned(tmp_path)
    before = migration._snapshot(root)
    history = root/WRITER_CONTROL/('a'*32)
    history.mkdir(parents=True, mode=0o700)
    journal = history/'registry.json'
    journal.write_bytes(b'{"controlled_writer_authority":"not_customer_data"}')
    after = migration._snapshot(root)
    assert all(not row['path'].startswith(WRITER_CONTROL+'/') for row in after['inventory']['files'])
    assert after['inventory'] == before['inventory']
    assert journal.read_bytes() == b'{"controlled_writer_authority":"not_customer_data"}'


def test_original_unowned_home_with_writer_marker_cannot_be_adopted(tmp_path):
    from backend.engine import installed_home_adoption as adoption
    root, scopes, _ = installed(tmp_path)
    assert adoption.preview_installed_home(root, scopes=scopes)['can_adopt'] is True
    marker = root/WRITER_CONTROL
    marker.mkdir(mode=0o700)
    before = original_bytes(root)
    members = sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    result = adoption.preview_installed_home(root, scopes=scopes)
    assert result['can_adopt'] is False
    assert any(WRITER_CONTROL in blocker for blocker in result['blockers'])
    assert original_bytes(root) == before
    assert sorted(str(p.relative_to(root)) for p in root.rglob('*')) == members
    assert not (root/adoption.CONTROL).exists()


@pytest.mark.parametrize('dangling', [False, True])
def test_owned_root_refuses_linked_writer_control_without_foreign_access(tmp_path, dangling, monkeypatch):
    from backend.engine.global_store_paths import owned_root, OWNER_FILE
    root, scopes, *_ = owned(tmp_path)
    foreign = tmp_path/'foreign'
    if not dangling:
        foreign.mkdir()
        (foreign/'untouched.bin').write_bytes(b'foreign original bytes')
    link = root/WRITER_CONTROL
    link.symlink_to(foreign, target_is_directory=True)
    owner_before = (root/OWNER_FILE).read_bytes()
    foreign_before = None if dangling else (foreign/'untouched.bin').read_bytes()
    read = Path.read_bytes
    def forbid_foreign_read(path):
        if path == foreign or foreign in path.parents:
            pytest.fail('Linked writer control attempted to read foreign bytes')
        return read(path)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_bytes', forbid_foreign_read)
        with pytest.raises(ValueError, match='control.*links|linked'):
            owned_root(root/scopes['profiles'])
    assert link.is_symlink() and link.readlink() == foreign
    assert (root/OWNER_FILE).read_bytes() == owner_before
    if dangling:
        assert not foreign.exists()
    else:
        assert list(foreign.iterdir()) == [foreign/'untouched.bin']
        assert (foreign/'untouched.bin').read_bytes() == foreign_before

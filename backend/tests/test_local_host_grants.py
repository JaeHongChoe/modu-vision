"""A selected path is authority for one operation, never arbitrary filesystem access."""
import pytest


AUTH = {'actor_id': 'actor', 'session_id': 'a' * 64, 'tab_id': 'b' * 32,
        'workspace_id': 'workspace', 'project_id': 'project'}


def test_selected_backup_path_is_bound_to_actor_tab_project_and_consumed_once(tmp_path):
    from backend.local_host.grants import PathGrants
    grants = PathGrants(now=lambda: 100)
    target = tmp_path / 'backups'; target.mkdir()
    selected = grants.issue(AUTH, target, 'project-backup', 'directory')
    proof = [selected['grant_id']]
    for changed in ({'actor_id': 'other'}, {'session_id': 'c' * 64},
                    {'tab_id': 'd' * 32}, {'workspace_id': 'other'}, {'project_id': 'other'}):
        assert grants.validate({**AUTH, **changed}, proof, str(target), 'destination_dir', '/api/project/backup') is None
    assert grants.validate(AUTH, proof, str(target), 'archive_path', '/api/project/restore') is None
    token = grants.validate(AUTH, proof, str(target), 'destination_dir', '/api/project/backup')
    assert token == selected['grant_id']
    grants.consume(AUTH, [token])
    assert grants.validate(AUTH, proof, str(target), 'destination_dir', '/api/project/backup') is None


def test_expired_replaced_or_linked_selection_cannot_be_consumed(tmp_path):
    from backend.local_host.grants import PathGrants
    clock = [100]
    grants = PathGrants(now=lambda: clock[0], ttl=120)
    target = tmp_path / 'backups'; target.mkdir()
    selected = grants.issue(AUTH, target, 'project-backup', 'directory')
    clock[0] = 221
    assert grants.validate(AUTH, [selected['grant_id']], str(target), 'destination_dir', '/api/project/backup') is None
    clock[0] = 100
    selected = grants.issue(AUTH, target, 'project-backup', 'directory')
    target.rename(tmp_path / 'original'); target.mkdir()
    assert grants.validate(AUTH, [selected['grant_id']], str(target), 'destination_dir', '/api/project/backup') is None
    link = tmp_path / 'linked'; link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError): grants.issue(AUTH, link, 'project-backup', 'directory')


def test_host_secrets_and_ancestor_directories_are_never_export_grants(tmp_path):
    from backend.local_host.grants import PathGrants
    secret = tmp_path / 'state' / 'auth'; secret.mkdir(parents=True)
    grants = PathGrants(protected_roots=[secret])
    for candidate in (secret, secret.parent, tmp_path):
        with pytest.raises(ValueError): grants.issue(AUTH, candidate, 'label-import', 'directory')


def test_atomic_consumption_rechecks_identity_and_does_not_partially_consume(tmp_path):
    from backend.local_host.grants import PathGrants
    grants = PathGrants()
    archive = tmp_path / 'archive.zip'; archive.write_bytes(b'archive')
    target = tmp_path / 'target'; target.mkdir()
    a = grants.issue(AUTH, archive, 'project-restore-archive', 'file')
    b = grants.issue(AUTH, target, 'project-restore-target', 'directory')
    archive.rename(tmp_path / 'original.zip'); archive.write_bytes(b'replaced')
    with pytest.raises(ValueError): grants.consume(AUTH, [a['grant_id'], b['grant_id']])
    assert grants.validate(AUTH, [b['grant_id']], str(target), 'target_dir', '/api/project/restore') == b['grant_id']


def test_unselected_sibling_and_wrong_operation_are_denied(tmp_path):
    from backend.local_host.grants import PathGrants
    grants = PathGrants(); one = tmp_path / 'one'; two = tmp_path / 'two'
    one.mkdir(); two.mkdir()
    chosen = grants.issue(AUTH, one, 'dataset-source', 'directory')
    assert grants.validate(AUTH, [chosen['grant_id']], str(two), 'folder_path', '/api/dataset/import') is None
    assert grants.validate(AUTH, [chosen['grant_id']], str(one), 'folder_path', '/api/dataset/import') == chosen['grant_id']
    assert grants.validate(AUTH, [chosen['grant_id']], str(one), 'destination_dir', '/api/project/backup') is None


def test_backup_restore_route_matches_only_the_selected_parent_and_named_fresh_child(tmp_path):
    from backend.local_host.grants import PathGrants
    grants=PathGrants();parent=tmp_path/'destination';parent.mkdir()
    chosen=grants.issue(AUTH,parent,'project-restore-target','directory');tokens=[chosen['grant_id']]
    route='/api/local-host/projects/backups/backup_123/restore'
    assert grants.validate(AUTH,tokens,str(parent/'restored-project'),'target_dir',route)==chosen['grant_id']
    assert grants.validate(AUTH,tokens,str(parent/'other'),'target_dir',route) is None
    assert grants.validate(AUTH,tokens,str(parent/'restored-project'),'target_dir',route+'/extra') is None
    assert grants.validate(AUTH,tokens,str(parent/'restored-project'),'target_dir','/api/local-host/projects/backups/../restore') is None

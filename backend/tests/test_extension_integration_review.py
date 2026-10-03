"""Independent integration regressions for rollout and project retention boundaries."""
import json
from pathlib import Path

import pytest

from backend.engine.fleet import FleetRegistry
from backend.tests.test_service_s5_06 import fleet


def test_rollback_must_not_finish_with_unpublished_committed_forward_target(fleet, monkeypatch):
    store, project, ids, release, runtime, _, writes, _ = fleet
    for index, identifier in enumerate(ids[:2]):
        policy = store.root.parent / f'old-policy-{index}.json'
        policy.write_text(json.dumps({'manifest_sha256': 'c' * 64}))
        store.apply(identifier, {**release, 'manifest_sha256': 'c' * 64,
                                'release_policy': str(policy)}, reviewer='QA', project=project)
    plan = store.create_rollout(release, target_ids=ids[:2], canary_target_ids=[ids[0]],
                                batch_size=1, reviewer='QA', project=project)
    plan = store.advance_rollout(plan['plan_id'], expected_revision=plan['revision'],
                                reviewer='QA', project=project)
    actual_probe = store._rollout_probe

    class Crash(BaseException):
        pass

    def crash_after_target_commit(plan, target, *, require_desired=False):
        if target['target_id'] == ids[1] and require_desired:
            raise Crash()
        return actual_probe(plan, target, require_desired=require_desired)

    monkeypatch.setattr(store, '_rollout_probe', crash_after_target_commit)
    with pytest.raises(Crash):
        store.advance_rollout(plan['plan_id'], expected_revision=plan['revision'],
                              reviewer='QA', project=project, confirm_canary=True)
    reopened = FleetRegistry(store.root.parent)
    saved = reopened.rollout(plan['plan_id'])
    assert saved['targets'][1]['status'] == 'applying'
    assert saved['targets'][1]['deployment_id'] is None
    assert reopened.ledger(ids[1]).active()['release']['rollout_id'] == plan['plan_id']
    try:
        result = reopened.rollback_rollout(saved['plan_id'], expected_revision=saved['revision'],
                                          reviewer='QA', project=project)
    except ValueError:
        # Requiring explicit live adoption before rollback is also a safe result.
        return
    forward_live = [identifier for identifier in ids[:2]
                    if reopened.ledger(identifier).active()['release'].get('rollout_id') == plan['plan_id']]
    assert not (result['status'] == 'rolled_back' and forward_live), (
        f'Plan claims rolled_back while forward rollout remains active at {forward_live}')


def test_project_retention_blocks_package_needed_by_fleet_active_release(fleet):
    from backend.engine.artifact_retention import ArtifactRetention
    store, project, ids, release, *_ = fleet
    package = store.root.parent / 'runtime_service' / 'releases' / 'fleet-active'
    package.mkdir(parents=True)
    (package / 'manifest.json').write_text('{}')
    policy = package.parent / 'fleet-active.policy.json'
    policy.write_text(json.dumps({'manifest_sha256': release['manifest_sha256']}))
    release = {**release, 'package_path': str(package), 'release_policy': str(policy)}
    store.apply(ids[0], release, reviewer='QA', project=project)
    assert store.ledger(ids[0]).active()['release']['package_path'] == str(package)
    retention = ArtifactRetention(store.root.parent)
    with pytest.raises(ValueError, match='pinned|operation|busy|lock'):
        retention.move_to_trash([package], project=project, retention_days=0)
    assert package.is_dir()


def test_restore_rebinds_fleet_release_and_rollout_to_fresh_project(fleet, tmp_path):
    from backend.engine.project_archive import create_archive, restore_archive
    from backend.api.routes_project import _load_project
    from backend.tests.test_service_s5_09 import project as create_project
    fixture_store, _, ids, release, *_ = fleet
    _, project, root, _, _ = create_project(tmp_path / 'archive-fixture')
    store = FleetRegistry(root)
    target = fixture_store.target(ids[0])
    store.save_target(name=target['name'], url=target['url'], token=fixture_store.secret(ids[0]),
                      target_id=ids[0])
    package = root / 'runtime_service' / 'releases' / 'fleet-restored'
    package.mkdir(parents=True)
    (package / 'manifest.json').write_text('{}')
    policy = package.parent / 'fleet-restored.policy.json'
    policy.write_text(json.dumps({'manifest_sha256': release['manifest_sha256']}))
    release = {**release, 'package_path': str(package), 'release_policy': str(policy)}
    store.apply(ids[0], release, reviewer='QA', project=project)
    plan = store.create_rollout(release, target_ids=[ids[0]], canary_target_ids=[ids[0]],
                                batch_size=1, reviewer='QA', project=project)
    backup = create_archive(project, tmp_path / 'backups')
    fresh = tmp_path / 'fresh-fleet-project'
    restore_archive(Path(backup['archive_path']), fresh)
    restored = FleetRegistry(fresh)
    paths = [restored.ledger(ids[0]).active()['release']['package_path'],
             restored.ledger(ids[0]).active()['release']['release_policy'],
             restored.rollout(plan['plan_id'])['release']['package_path'],
             restored.rollout(plan['plan_id'])['release']['release_policy']]
    assert all(Path(path).is_relative_to(fresh) for path in paths), (
        f'Restored fleet references still bind original project: {paths}')
    assert _load_project(fresh)['project_dir'] == str(fresh)

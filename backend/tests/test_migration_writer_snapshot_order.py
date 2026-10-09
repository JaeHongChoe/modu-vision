"""Real migration admission/snapshot ordering; Root executes these declarations."""
import json
from pathlib import Path

import pytest

from backend.engine import migration_inventory, project_migration
from backend.engine.migration_guard import exclusive_admitted, maintenance_guard


@pytest.fixture(autouse=True)
def isolated_control_store(tmp_path, monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "installation"))
    monkeypatch.delenv("VISION_RESOURCE_LEASE_DB", raising=False)


def original_project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "project.json").write_text(json.dumps({"id": "p", "name": "legacy", "extension": {"kept": True}}))
    labels = root / "labels"
    labels.mkdir()
    (labels / "truth.json").write_bytes(b'{"truth":1}')
    return root


def payload_bytes(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*")
            if path.is_file() and path.name not in {"migration_admission.lock", "runtime_lifecycle.lock"}}


def test_active_writer_refuses_before_a_racing_full_snapshot(tmp_path, monkeypatch):
    # Bug caught: reading live artifacts before asking for writer drain.
    root = original_project(tmp_path)
    attempted = []
    def unstable_snapshot(*args, **kwargs):
        attempted.append(args)
        raise ValueError("Migration source identity or bytes changed during bounded snapshot")
    monkeypatch.setattr(migration_inventory, "project_snapshot", unstable_snapshot)
    with maintenance_guard(root):
        before = payload_bytes(root)
        with pytest.raises(project_migration.MigrationError, match="writers"):
            project_migration.apply_migration(root)
        assert attempted == []
        assert payload_bytes(root) == before
    assert not (root / ".migrations").exists()
    assert "schema_version" not in json.loads((root / "project.json").read_bytes())


def test_real_snapshot_backup_and_cutover_hold_exclusive_admission(tmp_path, monkeypatch):
    # Bug caught: any original full snapshot escapes this owner's exclusive scope.
    root = original_project(tmp_path)
    original_snapshot = migration_inventory.project_snapshot
    original_backup = migration_inventory.verified_backup
    snapshots = []
    backups = []
    def snapshot(directory, **kwargs):
        assert Path(directory).resolve() == root.resolve()
        assert exclusive_admitted(root), "Full migration snapshot ran before exclusive writer admission"
        snapshots.append(dict(kwargs))
        return original_snapshot(directory, **kwargs)
    def backup(directory, target, before):
        assert exclusive_admitted(root)
        backups.append(before["inventory"]["file_count"])
        return original_backup(directory, target, before)
    monkeypatch.setattr(migration_inventory, "project_snapshot", snapshot)
    monkeypatch.setattr(migration_inventory, "verified_backup", backup)
    result = project_migration.apply_migration(root)
    assert result["receipt"]["status"] == "applied"
    assert len(snapshots) >= 2
    assert backups == [2]
    assert result["receipt"]["backup_verified"]["file_count"] == 2
    assert json.loads((root / "project.json").read_bytes())["schema_version"] == 1
    assert json.loads((root / "project.json").read_bytes())["extension"] == {"kept": True}
    assert (root / "labels/truth.json").read_bytes() == b'{"truth":1}'
    assert not exclusive_admitted(root)
    with maintenance_guard(root, exclusive=True):
        assert exclusive_admitted(root)


def test_genuine_identity_change_still_refuses_after_writer_drain(tmp_path, monkeypatch):
    # Bug caught: admission incorrectly bypasses unchanged inode/ctime checks.
    root = original_project(tmp_path)
    target = root / "labels/truth.json"
    target.chmod(0o600)
    identity = target.stat()
    before = payload_bytes(root)
    original_read = migration_inventory.os.read
    changed = []
    def mutate_after_real_read(fd, count):
        block = original_read(fd, count)
        observed = migration_inventory.os.fstat(fd)
        if block and not changed and (observed.st_dev, observed.st_ino) == (identity.st_dev, identity.st_ino):
            assert exclusive_admitted(root)
            target.chmod(0o640)
            changed.append(True)
        return block
    monkeypatch.setattr(migration_inventory.os, "read", mutate_after_real_read)
    with pytest.raises(project_migration.MigrationError, match="identity or bytes changed during bounded snapshot"):
        project_migration.apply_migration(root)
    assert changed == [True]
    assert payload_bytes(root) == before
    assert target.stat().st_mode & 0o777 == 0o640
    assert not (root / ".migrations").exists()
    assert "schema_version" not in json.loads((root / "project.json").read_bytes())
    assert not exclusive_admitted(root)
    with maintenance_guard(root, exclusive=True):
        assert exclusive_admitted(root)


@pytest.mark.parametrize("schema", [999, True, "1"])
def test_unsupported_manifest_refuses_without_new_admission_namespace(tmp_path, schema):
    # Bug caught: moving admission ahead of supported manifest validation.
    root = original_project(tmp_path)
    manifest = json.loads((root / "project.json").read_bytes())
    manifest["schema_version"] = schema
    (root / "project.json").write_text(json.dumps(manifest))
    before = payload_bytes(root)
    with pytest.raises(project_migration.MigrationError, match="Unsupported project schema"):
        project_migration.apply_migration(root)
    assert payload_bytes(root) == before
    assert not (root / "migration_admission.lock").exists()
    assert not (root / "runtime_lifecycle.lock").exists()
    assert not (root / ".migrations").exists()

"""Verified, self-contained project backups restored into a fresh workspace."""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
import re
import shutil
import sqlite3
import secrets
import socket
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Iterable
from zipfile import ZIP_STORED, ZipFile, BadZipFile, LargeZipFile


MAX_SOURCE_BYTES = 2 * 1024**3
MAX_TOTAL_BYTES = 4 * 1024**3
MAX_FILES = 100_000
_CHUNK = 1024 * 1024


class ArchiveError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _files(root: Path, excluded_root: Path | None = None, *, skip_sqlite_journals: bool = False) -> Iterable[Path]:
    for directory, folders, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in folders:
            if (parent / name).is_symlink():
                raise ArchiveError("Project or source contains a symbolic link")
        if excluded_root is not None:
            folders[:] = [name for name in folders if parent / name != excluded_root]
        folders.sort()
        for name in sorted(files):
            if name.startswith('.annotation-atomic-'):
                continue  # Private publication backups are not project artifacts.
            path = parent / name
            if path.is_symlink():
                raise ArchiveError("Project or source contains an unsupported file or symbolic link")
            if skip_sqlite_journals and name.endswith(("-wal", "-shm")) and _sqlite_file(path.with_name(name[:-4])):
                continue
            if not path.is_file():
                raise ArchiveError("Project or source contains an unsupported file or symbolic link")
            yield path


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_key(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]


def _split_key(path: Path) -> str:
    return hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()


def _safe_member(name: str) -> bool:
    if not isinstance(name, str) or "\\" in name or name.startswith("/"):
        return False
    parts = Path(name).parts
    return len(parts) >= 2 and parts[0] in {"project", "source"} and all(part not in {"", ".", ".."} for part in parts)


def _content_digest(value: dict[str, Any]) -> str:
    unsigned = {key: item for key, item in value.items() if key != "content_digest"}
    canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dataset_fingerprint(project_dir: Path, source_dir: Path, labelset_id: str | None = None) -> str:
    """Use the selected labels and split from project-scoped training verification."""
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.project_labelsets import labelset_root, load_labelsets

    selected_id = labelset_id if labelset_id is not None else load_labelsets(project_dir)["active_id"]
    return fingerprint_dataset(
        source_dir, studio_root=labelset_root(project_dir, selected_id),
        split_manifest=project_dir / "dataset" / "splits" / f"{_split_key(source_dir)}.json",
        use_scope=False,
    )


def _labelset_dataset_fingerprints(project_dir: Path, source_dir: Path) -> tuple[str, dict[str, str]]:
    from backend.engine.project_labelsets import load_labelsets

    registry = load_labelsets(project_dir)
    return registry["active_id"], {
        row["id"]: _dataset_fingerprint(project_dir, source_dir, row["id"])
        for row in registry["labelsets"]
    }


def _sqlite_file(path: Path) -> bool:
    if path.suffix not in {".sqlite3", ".sqlite", ".db"} or not path.is_file():
        return False
    with path.open("rb") as source:
        return source.read(16) == b"SQLite format 3\x00"


def _sqlite_snapshot(path: Path, directory: Path) -> Path:
    snapshot = directory / f"{uuid.uuid4().hex}.sqlite3"
    source = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)
    target = sqlite3.connect(snapshot)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return snapshot


def create_archive(project: dict[str, Any], destination_dir: Path) -> dict[str, Any]:
    project_dir = Path(project["project_dir"]).resolve()
    source_text = project.get("source_dataset_dir")
    source_dir = Path(source_text).resolve() if source_text else None
    destination_dir = Path(destination_dir).expanduser().resolve()
    if (destination_dir == project_dir or destination_dir.is_relative_to(project_dir)
            or source_dir is not None and (destination_dir == source_dir or destination_dir.is_relative_to(source_dir))):
        raise ArchiveError("Choose a backup folder outside the project and source dataset")
    if not (project_dir / "project.json").is_file():
        raise ArchiveError("Project manifest is missing")
    if source_dir is not None and not source_dir.is_dir():
        raise ArchiveError("Source dataset is unavailable; backup cannot include it")
    if source_dir is not None and project_dir.is_relative_to(source_dir):
        raise ArchiveError("Source dataset must not contain the project workspace")

    source_active_set, source_fingerprints = (
        _labelset_dataset_fingerprints(project_dir, source_dir) if source_dir else (None, {})
    )
    source_fingerprint = source_fingerprints.get(source_active_set)

    inventory: list[tuple[str, Path, int]] = []
    source_bytes = total_bytes = 0
    for prefix, folder in (("project", project_dir), ("source", source_dir)):
        if folder is None:
            continue
        # Restored source lives inside the workspace. Archive it once under source/.
        for path in _files(folder, excluded_root=source_dir if prefix == "project" else None,
                           skip_sqlite_journals=prefix == "project"):
            size = path.stat().st_size
            total_bytes += size
            if prefix == "source":
                source_bytes += size
            if source_bytes > MAX_SOURCE_BYTES or total_bytes > MAX_TOTAL_BYTES:
                raise ArchiveError("Backup exceeds the source or total size limit", 413)
            inventory.append((f"{prefix}/{path.relative_to(folder).as_posix()}", path, size))
            if len(inventory) > MAX_FILES:
                raise ArchiveError("Backup exceeds the file count limit", 413)
    destination_dir.mkdir(parents=True, exist_ok=True)
    archive_name = re.sub(r"[^\w-]+", "_", project["name"]).strip("_") or "project"
    archive = destination_dir / f"{archive_name}-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}-{uuid.uuid4().hex[:6]}.mvision.zip"
    temporary = destination_dir / f".{archive.name}.{uuid.uuid4().hex}.partial"
    rows = []
    sqlite_staging = Path(tempfile.mkdtemp(prefix=".database-snapshot-", dir=destination_dir))
    try:
        with ZipFile(temporary, "w", compression=ZIP_STORED, allowZip64=True) as output:
            for member, path, size in inventory:
                if member.startswith("project/") and _sqlite_file(path):
                    path = _sqlite_snapshot(path, sqlite_staging)
                    snapshot_size = path.stat().st_size
                    total_bytes += snapshot_size - size
                    size = snapshot_size
                    if total_bytes > MAX_TOTAL_BYTES:
                        raise ArchiveError("Backup exceeds the total size limit", 413)
                before = path.stat()
                output.write(path, member)
                after = path.stat()
                if before.st_size != size or before.st_mtime_ns != after.st_mtime_ns:
                    raise ArchiveError(f"File changed during backup: {member}", 409)
                digest = _digest_file(path)
                final = path.stat()
                if final.st_size != size or final.st_mtime_ns != before.st_mtime_ns:
                    raise ArchiveError(f"File changed during backup: {member}", 409)
                rows.append({"member": member, "size": size, "sha256": digest})
            manifest = {
                "format": "modu-project-backup-v1", "project_id": project["id"],
                "original_project_dir": str(project_dir),
                "original_source_dir": str(source_dir) if source_dir else None,
                "source_dataset_fingerprint": source_fingerprint,
                "source_active_labelset_id": source_active_set,
                "source_dataset_fingerprints_by_labelset": source_fingerprints,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": rows,
            }
            if (source_dir is not None and _labelset_dataset_fingerprints(project_dir, source_dir)
                    != (source_active_set, source_fingerprints)):
                raise ArchiveError("Source dataset changed during backup", 409)
            output.writestr("backup-manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)
        shutil.rmtree(sqlite_staging)
    return {"archive_path": str(archive), "source_included": source_dir is not None,
            "file_count": len(rows), "total_bytes": total_bytes, "source_bytes": source_bytes}


def _rebind_text(value: str, old_project: Path, target: Path,
                 old_source: Path | None, new_source: Path | None) -> str:
    for original, replacement in ((old_source, new_source), (old_project, target)):
        if original is not None and replacement is not None:
            prefix = str(original)
            if value == prefix or value.startswith(prefix + os.sep):
                return str(replacement) + value[len(prefix):]
    return value


def _rebind_value(value: Any, old_project: Path, target: Path,
                  old_source: Path | None, new_source: Path | None) -> Any:
    if isinstance(value, str):
        return _rebind_text(value, old_project, target, old_source, new_source)
    if isinstance(value, list):
        return [_rebind_value(item, old_project, target, old_source, new_source) for item in value]
    if isinstance(value, dict):
        return {key: _rebind_value(item, old_project, target, old_source, new_source)
                for key, item in value.items()}
    return value


def _immutable_package_roots(project_dir: Path) -> list[Path]:
    roots = []
    for manifest in project_dir.rglob("manifest.json"):
        try:
            value = json.loads(manifest.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if (isinstance(value, dict) and isinstance(value.get("files"), list)
                and ("pipeline_id" in value or value.get("task") == "defect_gan")
                and all(isinstance(row, dict) and "path" in row and "sha256" in row for row in value["files"])):
            roots.append(manifest.parent)
    return roots


def _evaluation_digest(value: dict) -> str:
    unsigned = {key: item for key, item in value.items() if key != "evidence_sha256"}
    return hashlib.sha256(json.dumps(unsigned, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def _rebind_evaluation(value: dict, old_project: Path, target: Path,
                       old_source: Path | None, new_source: Path | None) -> dict:
    if _evaluation_digest(value) != value.get("evidence_sha256"):
        raise ArchiveError("Evaluation evidence integrity failure")
    updated = dict(value)
    for key in ("binding", "result"):
        updated[key] = _rebind_value(value[key], old_project, target, old_source, new_source)
    updated.setdefault("original_evidence_sha256", value["evidence_sha256"])
    updated.setdefault("original_binding", value["binding"])
    updated.setdefault("original_evidence", {key: item for key, item in value.items() if key != "evidence_sha256"})
    updated["archive_restorations"] = [*value.get("archive_restorations", []), {
        "previous_evidence_sha256": value["evidence_sha256"], "original_project_dir": str(old_project),
        "restored_project_dir": str(target), "original_source_dir": str(old_source) if old_source else None,
        "restored_source_dir": str(new_source) if new_source else None,
    }]
    updated["evidence_sha256"] = _evaluation_digest(updated)
    return updated


def _rebind_json_records(staging: Path, old_project: Path, target: Path,
                         old_source: Path | None, new_source: Path | None) -> None:
    source_roots = _restored_source_roots(staging)
    immutable_roots = _immutable_package_roots(staging)
    annotation_hashes: dict[str, str] = {}
    ledgers: list[Path] = []
    for path in staging.rglob("*.json"):
        # Source files are customer data. Keep their verified bytes intact.
        # Version snapshots need their own hashes, paths and immutable source bytes.
        if (path.is_relative_to(staging / "versions") or path.is_relative_to(staging/'.migrations') or path.name == "release_policy.json"
                or any(path.is_relative_to(root) for root in (*source_roots, *immutable_roots))):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if path.parent == staging / "reports" / "evaluations" and "evidence_sha256" in value:
            updated = _rebind_evaluation(value, old_project, target, old_source, new_source)
        else:
            updated = _rebind_value(value, old_project, target, old_source, new_source)
        if updated != value:
            previous_hash = _digest_file(path)
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
            if "by_dataset" in path.parts and "metadata" not in path.parts:
                annotation_hashes[previous_hash] = _digest_file(path)
        if path.name == "workflow.json" and path.parent.name == "metadata":
            from backend.engine.team_data import invalidate_reviews
            from backend.engine.dataset_metadata import _event
            changed=False
            review_enabled=updated.get('team_data',{}).get('settings',{}).get('review_enabled',False)
            for row in updated.get('images',{}).values():
                team=row.get('team',{})
                if review_enabled or team.get('reviews') or team.get('edit_lease'):
                    invalidate_reviews(row,'project_restored',clear_lease=True)
                    if review_enabled:
                        row['workflow_state']='needs_review';row['reviewer']=None
                    _event(row,'system','team_restore_invalidated',{'reason':'project_restored'})
                    changed=True
            if changed:path.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
            ledgers.append(path)
    for ledger in ledgers:
        _rebind_annotation_hashes(ledger, annotation_hashes)


def _rebind_annotation_hashes(path: Path, replacements: dict[str, str]) -> None:
    """Relocation changes path-bearing Studio JSON, without a new review event."""
    if not replacements:
        return
    value = json.loads(path.read_text(encoding="utf-8"))
    changed = False
    for row in value.get("images", {}).values():
        original = row.get("annotation_hash")
        if original in replacements:
            row["annotation_hash"] = replacements[original]
            changed = True
    if changed:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _scope_replacements(members: Iterable[str], old_source: Path | None,
                        new_source: Path | None) -> dict[str, str]:
    """Scope identities include every image parent, not only the source root."""
    if old_source is None or new_source is None:
        return {}
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
    parents = {Path(".")}
    for member in members:
        if member.startswith("source/") and Path(member).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS:
            parents.add(Path(*Path(member).parts[1:]).parent)
    return {_source_key(old_source / parent): _source_key(new_source / parent) for parent in parents}


def _rebind_scope_path(raw: str, replacements: dict[str, str]) -> str:
    # Hashes are entire path components; never replace a matching substring.
    return str(Path(*(replacements.get(part, part) for part in Path(raw).parts)))


def _rebind_versions(staging: Path, old_project: Path, target: Path,
                     old_source: Path | None, new_source: Path | None,
                     scopes: dict[str, str]) -> dict[str, str]:
    versions = staging / "versions"
    if not versions.is_dir():
        return {}
    digests = {}
    for path in versions.glob("*/manifest.json"):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("content_digest") != _content_digest(manifest):
            raise ArchiveError("Dataset version manifest digest mismatch")
        digests[manifest["content_digest"]] = str(path.parent.relative_to(staging))
        snapshots: dict[str, Path] = {}
        annotation_hashes: dict[str, str] = {}
        for row in manifest.get("files", []):
            raw = row.get("snapshot_path")
            if not raw:
                continue
            snapshot = path.parent / raw
            if not snapshot.resolve().is_relative_to(path.parent.resolve()) or not snapshot.is_file():
                raise ArchiveError("Dataset version snapshot path is invalid")
            if _digest_file(snapshot) != row.get("sha256"):
                raise ArchiveError("Dataset version snapshot checksum mismatch")
            snapshots[raw] = snapshot
        manifest = _rebind_value(manifest, old_project, target, old_source, new_source)
        if old_source is not None and new_source is not None:
            for row in manifest.get("files", []):
                raw = row.get("source_path")
                origin = row.get("origin")
                if isinstance(raw, str) and origin in {"studio", "studio_scoped"}:
                    row["source_path"] = _rebind_scope_path(raw, scopes)
                elif isinstance(raw, str) and origin == "split":
                    row["source_path"] = _rebind_scope_path(raw, {f'{_split_key(old_source)}.json': f'{_split_key(new_source)}.json'})
                if origin == "studio_scoped":
                    row["relative_path"] = _rebind_scope_path(row["relative_path"], scopes)
                backup = row.get("snapshot_path")
                if not backup:
                    continue
                snapshot = snapshots[backup]
                if origin != "source" and snapshot.suffix.lower() == ".json":
                    before = _digest_file(snapshot)
                    try:
                        value = json.loads(snapshot.read_text(encoding="utf-8"))
                    except ValueError:
                        continue
                    updated = _rebind_value(value, old_project, target, old_source, new_source)
                    if updated != value:
                        snapshot.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
                    if origin in {"studio", "studio_scoped"} and "metadata" not in Path(backup).parts:
                        annotation_hashes[before] = _digest_file(snapshot)
            # Snapshot ledgers bind their own historical annotations, not today's.
            for snapshot in snapshots.values():
                if snapshot.name == "workflow.json" and snapshot.parent.name == "metadata":
                    _rebind_annotation_hashes(snapshot, annotation_hashes)
            for row in manifest.get("files", []):
                backup = row.get("snapshot_path")
                if backup:
                    snapshot = snapshots[backup]
                    row["sha256"] = _digest_file(snapshot)
                    row["size_bytes"] = snapshot.stat().st_size
                    if row.get("origin") == "studio_scoped":
                        row["snapshot_path"] = _rebind_scope_path(backup, scopes)
            snapshot_root = path.parent / "labels" / "studio_scoped"
            _rename_scopes(snapshot_root, scopes)
        manifest["content_digest"] = _content_digest(manifest)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        team_receipt=path.parent/'team-data.json'
        if team_receipt.is_file():
            value=json.loads(team_receipt.read_text(encoding='utf-8'))
            updated=_rebind_value(value,old_project,target,old_source,new_source)
            for row in updated.get('eligibility',[]):
                if row.get('annotation_hash') in annotation_hashes:
                    row['annotation_hash']=annotation_hashes[row['annotation_hash']]
            from backend.engine.team_data import _digest
            updated['eligibility_sha256']=_digest(updated.get('eligibility',[]))
            team_receipt.write_text(json.dumps(updated,ensure_ascii=False,indent=2),encoding='utf-8')
    return digests


def _rebind_training_version_aliases(target: Path, previous_digests: dict[str, str]) -> None:
    """Metadata aliases use relocated verified versions; checkpoint bytes stay fixed."""
    immutable = _immutable_package_roots(target)
    for path in (target / "models").rglob("*.json"):
        if any(path.is_relative_to(root) for root in immutable):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        binding = value.get("training_provenance") if isinstance(value, dict) else None
        if not isinstance(binding, dict) or binding.get("manifest_sha256") not in previous_digests:
            continue
        old_digest = binding["manifest_sha256"]
        directory = target / previous_digests[old_digest]
        if Path(binding.get("version_dir", "")).resolve() != directory.resolve():
            raise ArchiveError("Training version alias does not match its bound manifest")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        split_rows = [row["sha256"] for row in manifest["files"] if row["origin"] == "split"]
        split_digest = split_rows[0] if len(split_rows) == 1 else hashlib.sha256(json.dumps(
            [{key: row[key] for key in ("origin", "relative_path", "sha256")} for row in manifest["files"]],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        binding.setdefault("archive_restored_from_manifest_sha256", old_digest)
        binding.update(manifest_sha256=manifest["content_digest"], split_sha256=split_digest)
        if binding.get('team_data'):
            from backend.engine.team_data import _digest
            receipt=directory/'team-data.json'
            if not receipt.is_file() or receipt.is_symlink():raise ArchiveError('Team-data training receipt is unavailable')
            binding.setdefault('archive_restored_from_team_data_sha256',binding.get('team_data_sha256'))
            binding['team_data']=json.loads(receipt.read_text(encoding='utf-8'))
            binding['team_data_sha256']=_digest(binding['team_data'])
        if binding.get('family_inputs'):
            for row in binding['family_inputs']:
                if (binding.get('family_task')=='enhancement' and row.get('relative_path')=='pairs.json'
                        and row.get('snapshot_path')):
                    backup=Path(row['snapshot_path']);current=Path(row['source_path'])
                    if (not backup.resolve().is_relative_to(directory.resolve()) or _digest_file(backup)!=row['sha256']
                            or not current.resolve().is_relative_to(target/'dataset'/'enhancement')):
                        raise ArchiveError('Enhancement family relocation binding is invalid')
                    from backend.engine.enhancement import load_enhancement_manifest
                    actual=load_enhancement_manifest(current.parent)['provenance']['dataset_sha256']
                    if actual!=binding['family_provenance']['dataset_sha256']:
                        raise ArchiveError('Enhancement pairs changed from bound family manifest')
                    row['restored_source_sha256']=_digest_file(current)
            binding['family_inputs_sha256']=hashlib.sha256(json.dumps(binding['family_inputs'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _rename_scopes(root: Path, scopes: dict[str, str]) -> None:
    for old_key, new_key in scopes.items():
        if old_key == new_key:
            continue
        original, replacement = root / old_key, root / new_key
        if original.exists():
            if replacement.exists():
                raise ArchiveError("Restored annotation key collides with an existing set")
            original.rename(replacement)


def _rebind_dataset_state(staging: Path, old_source: Path | None, new_source: Path | None,
                          scopes: dict[str, str]) -> None:
    if old_source is None or new_source is None:
        return
    old_key = _source_key(old_source)
    new_key = _source_key(new_source)
    for original in (staging / "flowcharts").glob(f"pipeline_*_{old_key}.json"):
        replacement = original.with_name(original.name.removesuffix(f"{old_key}.json") + f"{new_key}.json")
        if replacement.exists():
            raise ArchiveError("Restored flow recipe key collides with an existing recipe")
        original.rename(replacement)
    roots = [staging / "annotations"]
    for labelset in (staging / "flowcharts" / "drafts").glob("*"):
        if labelset.is_dir():
            _rename_scopes(labelset, scopes)
    roots.extend(staging.glob("labelsets/ls_*/annotations"))
    for root in roots:
        _rename_scopes(root / "by_dataset", scopes)
    split = staging / "dataset" / "splits" / f"{_split_key(old_source)}.json"
    if split.exists():
        replacement = split.with_name(f"{_split_key(new_source)}.json")
        if replacement.exists():
            raise ArchiveError("Restored split key collides with an existing split")
        try:
            value = json.loads(split.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("folder_path") == str(old_source):
                value["folder_path"] = str(new_source)
                split.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        except (ValueError, OSError):
            pass
        split.rename(replacement)


def _rebind_json_blob(raw: str | None, old_project: Path, target: Path,
                      old_source: Path | None, new_source: Path | None) -> str | None:
    if raw is None:
        return None
    value = json.loads(raw)
    updated = _rebind_value(value, old_project, target, old_source, new_source)
    return (json.dumps(updated, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if updated != value else raw)


def _rebind_inspection_history(staging: Path, old_project: Path, target: Path,
                               old_source: Path | None, new_source: Path | None) -> None:
    database = staging / "inspection_history.sqlite3"
    if not database.is_file():
        return
    # SQLite's connection context manages a transaction; closing releases the
    # staging file handle before publication, including when rebinding fails.
    with closing(sqlite3.connect(database)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = OFF")
        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
        if not {"run_id", "source_folder", "pipeline_json", "pipeline_hash"}.issubset(run_columns):
            raise ArchiveError("Inspection history has an invalid run schema")
        ids: dict[str, str] = {}
        with conn:
            for run in conn.execute("SELECT * FROM runs").fetchall():
                old_id = run["run_id"]
                new_id = str(uuid.uuid4())
                ids[old_id] = new_id
                source_folder = _rebind_text(run["source_folder"], old_project, target, old_source, new_source)
                pipeline_json = _rebind_json_blob(run["pipeline_json"], old_project, target, old_source, new_source)
                fields = {"run_id": new_id, "source_folder": source_folder, "pipeline_json": pipeline_json}
                if pipeline_json != run["pipeline_json"]:
                    fields["pipeline_hash"] = hashlib.sha256(pipeline_json.encode("utf-8")).hexdigest()
                if "model_paths_json" in run_columns:
                    fields["model_paths_json"] = _rebind_json_blob(
                        run["model_paths_json"], old_project, target, old_source, new_source,
                    )
                if "owner_instance" in run_columns:
                    # A copied in-flight run has no worker in the restored project.
                    fields["owner_instance"] = None
                assignments = ", ".join(f"{name} = ?" for name in fields)
                conn.execute(f"UPDATE runs SET {assignments} WHERE run_id = ?", (*fields.values(), old_id))

            row_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rows)")}
            if not {"run_id", "image_path", "image_json"}.issubset(row_columns):
                raise ArchiveError("Inspection history has an invalid image schema")
            for row in conn.execute("SELECT * FROM rows").fetchall():
                fields = {
                    "run_id": ids[row["run_id"]],
                    "image_path": _rebind_text(row["image_path"], old_project, target, old_source, new_source),
                    "image_json": _rebind_json_blob(row["image_json"], old_project, target, old_source, new_source),
                }
                if "result_json" in row_columns:
                    fields["result_json"] = _rebind_json_blob(
                        row["result_json"], old_project, target, old_source, new_source,
                    )
                assignments = ", ".join(f"{name} = ?" for name in fields)
                conn.execute(
                    f"UPDATE rows SET {assignments} WHERE run_id = ? AND image_path = ?",
                    (*fields.values(), row["run_id"], row["image_path"]),
                )

            reviews = {row["name"] for row in conn.execute("PRAGMA table_info(reviews)")}
            if reviews:
                if not {"review_id", "run_id", "image_path"}.issubset(reviews):
                    raise ArchiveError("Inspection history has an invalid review schema")
                for review in conn.execute("SELECT review_id, run_id, image_path FROM reviews").fetchall():
                    conn.execute(
                        "UPDATE reviews SET run_id = ?, image_path = ? WHERE review_id = ?",
                        (ids[review["run_id"]],
                         _rebind_text(review["image_path"], old_project, target, old_source, new_source),
                         review["review_id"]),
                    )


def _rebind_deployments(staging: Path, old_source: Path | None, new_source: Path | None) -> None:
    database = staging / "model_deployments.sqlite3"
    if not database.is_file() or old_source is None or new_source is None:
        return
    with closing(sqlite3.connect(database)) as conn, conn:
        for table in ("revisions", "active_revisions"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "source_dataset_path" in columns:
                conn.execute(f"UPDATE {table} SET source_dataset_path = ? WHERE source_dataset_path = ?",
                             (str(new_source), str(old_source)))


def _rebind_execution_state(staging: Path, old_project: Path, target: Path,
                            old_source: Path | None, new_source: Path | None) -> None:
    database = staging / "reports" / "comparison_jobs.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database)) as conn, conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
            if not {"job_id", "payload", "status"}.issubset(columns):
                raise ArchiveError("Comparison jobs have an invalid schema")
            for identifier, payload, status in conn.execute("SELECT job_id, payload, status FROM jobs").fetchall():
                rebound = _rebind_json_blob(payload, old_project, target, old_source, new_source)
                fields = {"payload": rebound}
                if status in {"queued", "running"}:
                    fields["status"] = "interrupted"
                    if "error" in columns:
                        fields["error"] = "Restored comparison has no worker; rerun the bound models."
                if "owner_pid" in columns:
                    fields["owner_pid"] = None
                assignments = ", ".join(f"{name} = ?" for name in fields)
                conn.execute(f"UPDATE jobs SET {assignments} WHERE job_id = ?", (*fields.values(), identifier))
    database = staging / "runtime_service" / "runtime_deployments.sqlite3"
    if database.is_file():
        with closing(sqlite3.connect(database)) as conn, conn:
            for identifier, release, ack in conn.execute("SELECT deployment_id, release, ack FROM deployments").fetchall():
                conn.execute("UPDATE deployments SET release = ?, ack = ? WHERE deployment_id = ?", (
                    _rebind_json_blob(release, old_project, target, old_source, new_source),
                    _rebind_json_blob(ack, old_project, target, old_source, new_source), identifier))
    service_config = staging / "runtime_service" / "service.json"
    if service_config.is_file():
        value = json.loads(service_config.read_text(encoding="utf-8"))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        value.update(pid=None, port=port, token=secrets.token_urlsafe(32))
        for key in ('native_label','native_kind','native_registration_path','process_created_at','process_command_sha256'):
            value.pop(key,None)
        service_config.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        service_config.chmod(0o600)
    # Native files belong to the original host and may contain old secrets/paths.
    install=staging/'runtime_service'/'install'
    if install.is_dir():shutil.rmtree(install)


def _replace_fingerprints(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_fingerprints(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_fingerprints(item, replacements) for key, item in value.items()}
    return value


def _restored_source_roots(project_dir: Path) -> list[Path]:
    return [path for path in (project_dir / "dataset").glob("restored_source*")
            if path.is_dir() and re.fullmatch(r"restored_source(?:_[0-9]+)?", path.name)]


def _restore_source_location(members: Iterable[str]) -> Path:
    """Keep a retained previous source separate when the active source changes."""
    project_files = {Path(*Path(member).parts[1:]) for member in members if member.startswith("project/")}
    candidate = Path("dataset/restored_source")
    suffix = 2
    while any(path == candidate or path.is_relative_to(candidate) for path in project_files):
        candidate = Path(f"dataset/restored_source_{suffix}")
        suffix += 1
    if any(parent in project_files for parent in candidate.parents if parent != Path(".")):
        raise ArchiveError("Backup project file collides with the source directory")
    return candidate


def _rebind_fingerprint_records(target: Path, source: Path, old_fingerprints: dict[str, str]) -> None:
    from backend.engine.project_labelsets import load_labelsets

    registry = load_labelsets(target)
    registered_ids = {row["id"] for row in registry["labelsets"]}
    replacements: dict[str, str] = {}
    for set_id, old_fingerprint in old_fingerprints.items():
        if (set_id not in registered_ids or not isinstance(old_fingerprint, str)
                or not re.fullmatch(r"v1:[0-9a-f]{64}", old_fingerprint)):
            raise ArchiveError("Backup contains an invalid label set fingerprint")
        new_fingerprint = _dataset_fingerprint(target, source, set_id)
        if old_fingerprint in replacements and replacements[old_fingerprint] != new_fingerprint:
            raise ArchiveError("Backup label set fingerprints are ambiguous")
        replacements[old_fingerprint] = new_fingerprint
    source_roots = _restored_source_roots(target)
    immutable_roots = _immutable_package_roots(target)
    for path in target.rglob("*.json"):
        if (path.is_relative_to(target/'.migrations') or path.name == "release_policy.json" or any(path.is_relative_to(root) for root in (*source_roots, *immutable_roots))
                or path.is_relative_to(target / "versions") and path.name != "manifest.json"):
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if path.parent == target / "reports" / "evaluations" and "evidence_sha256" in value:
            if _evaluation_digest(value) != value["evidence_sha256"]:
                raise ArchiveError("Restored evaluation evidence integrity failure")
            updated = dict(value)
            for key in ("binding", "result"):
                updated[key] = _replace_fingerprints(value[key], replacements)
            updated["evidence_sha256"] = _evaluation_digest(updated)
        else:
            updated = _replace_fingerprints(value, replacements)
        if updated != value:
            if path.parent.parent == target / "versions" and path.name == "manifest.json":
                updated["content_digest"] = _content_digest(updated)
            path.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")

    database = target / "model_deployments.sqlite3"
    if not database.is_file():
        return
    with closing(sqlite3.connect(database)) as conn, conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(revisions)")}
        for column in ("training_dataset_fingerprint", "evaluation_dataset_fingerprint"):
            if column in columns and replacements:
                cases = " ".join("WHEN ? THEN ?" for _ in replacements)
                parameters = [item for pair in replacements.items() for item in pair]
                conn.execute(f"UPDATE revisions SET {column} = CASE {column} {cases} ELSE {column} END", parameters)
        if {"comparison_id", "comparison_sha256"}.issubset(columns):
            for (comparison_id,) in conn.execute("SELECT DISTINCT comparison_id FROM revisions"):
                folder = 'evaluations' if isinstance(comparison_id,str) and comparison_id.startswith('evaluation_') else 'model_comparisons'
                report = target / "reports" / folder / f"{comparison_id}.json"
                if report.is_file():
                    conn.execute("UPDATE revisions SET comparison_sha256 = ? WHERE comparison_id = ?",
                                 (_digest_file(report), comparison_id))


def restore_archive(archive_path: Path, target_dir: Path) -> Path:
    archive_path = Path(archive_path).expanduser().resolve()
    target_dir = Path(target_dir).expanduser().resolve()
    if not archive_path.is_file():
        raise ArchiveError("Backup archive not found", 404)
    if target_dir.exists():
        raise ArchiveError("Restore folder already exists; choose an empty new location", 409)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = target_dir.parent / f".{target_dir.name}.restore-{uuid.uuid4().hex}"
    installed = completed = False
    try:
        with ZipFile(archive_path) as source:
            infos = source.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or names.count("backup-manifest.json") != 1:
                raise ArchiveError("Backup has duplicate or missing manifest entries")
            if source.getinfo('backup-manifest.json').file_size > 32*1024*1024:
                raise ArchiveError('Backup manifest exceeds 32 MiB',413)
            manifest = json.loads(source.read("backup-manifest.json"))
            if not isinstance(manifest,dict):raise ArchiveError('Backup manifest must be an object')
            if manifest.get("format") != "modu-project-backup-v1":
                raise ArchiveError("Unsupported backup format")
            rows = manifest.get("files")
            if not isinstance(rows, list) or len(rows) > MAX_FILES:
                raise ArchiveError("Invalid backup file inventory")
            expected = {row["member"]: row for row in rows}
            if len(expected) != len(rows) or set(names) != set(expected) | {"backup-manifest.json"}:
                raise ArchiveError("Backup inventory does not match its files")
            total = sum(int(row["size"]) for row in rows)
            if total > MAX_TOTAL_BYTES or any(not _safe_member(name) for name in expected):
                raise ArchiveError("Backup exceeds limits or contains an unsafe path")
            old_project = Path(manifest["original_project_dir"])
            old_source = Path(manifest["original_source_dir"]) if manifest.get("original_source_dir") else None
            if (target_dir == old_project or target_dir.is_relative_to(old_project)
                    or old_source is not None and (target_dir == old_source or target_dir.is_relative_to(old_source))):
                raise ArchiveError("Restore location must be outside the original project and source dataset")
            source_size = sum(int(row["size"]) for row in rows if row["member"].startswith("source/"))
            if source_size > MAX_SOURCE_BYTES:
                raise ArchiveError("Backup source exceeds the size limit", 413)
            source_relative = _restore_source_location(expected) if old_source else None
            new_source = target_dir / source_relative if source_relative is not None else None
            staging.mkdir()
            if source_relative is not None:
                (staging / source_relative).mkdir(parents=True)
            for info in infos:
                if info.filename == "backup-manifest.json":
                    continue
                row = expected[info.filename]
                if (info.is_dir() or info.file_size != row["size"]
                        or (info.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ArchiveError("Backup contains an invalid file member")
                relative = Path(*Path(info.filename).parts[1:])
                destination = staging / relative if info.filename.startswith("project/") else staging / source_relative / relative
                if destination.exists():
                    raise ArchiveError("Backup members collide at the restore destination")
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with source.open(info) as input_file, destination.open("wb") as output:
                    for chunk in iter(lambda: input_file.read(_CHUNK), b""):
                        digest.update(chunk)
                        output.write(chunk)
                if digest.hexdigest() != row["sha256"]:
                    raise ArchiveError(f"Backup checksum mismatch: {info.filename}")
            project_path = staging / "project.json"
            if not project_path.is_file():
                raise ArchiveError("Backup project.json is missing")
            project = json.loads(project_path.read_text(encoding="utf-8"))
            if 'schema_version' in project and (type(project['schema_version']) is not int or project['schema_version']!=1):
                raise ArchiveError('Backup project schema is unsupported')
            if project.get("id") != manifest.get("project_id"):
                raise ArchiveError("Backup project identity mismatch")
            project.update({"project_dir": str(target_dir), "dataset_dir": str(target_dir / "dataset"),
                            "models_dir": str(target_dir / "models"), "reports_dir": str(target_dir / "reports"),
                            "annotations_dir": str(target_dir / "annotations"),
                            "source_dataset_dir": str(new_source) if new_source else None})
            project_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")
            scopes = _scope_replacements(expected, old_source, new_source)
            _rebind_dataset_state(staging, old_source, new_source, scopes)
            _rebind_json_records(staging, old_project, target_dir, old_source, new_source)
            version_digests = _rebind_versions(staging, old_project, target_dir, old_source, new_source, scopes)
            _rebind_inspection_history(staging, old_project, target_dir, old_source, new_source)
            _rebind_deployments(staging, old_source, new_source)
            _rebind_execution_state(staging, old_project, target_dir, old_source, new_source)
        if target_dir.exists():
            raise ArchiveError("Restore folder was created by another process", 409)
        os.replace(staging, target_dir)
        installed = True
        if new_source is not None:
            old_fingerprints = manifest.get("source_dataset_fingerprints_by_labelset")
            if old_fingerprints is not None:
                if not isinstance(old_fingerprints, dict):
                    raise ArchiveError("Backup contains an invalid label set fingerprint map")
            else:
                # Existing v1 backups recorded only the label set active at backup.
                from backend.engine.project_labelsets import load_labelsets
                old_fingerprint = manifest.get("source_dataset_fingerprint")
                active_id = load_labelsets(target_dir)["active_id"]
                old_fingerprints = ({active_id: old_fingerprint}
                                    if isinstance(old_fingerprint, str) and old_fingerprint.startswith("v1:") else {})
            _rebind_fingerprint_records(target_dir, new_source, old_fingerprints)
        _rebind_training_version_aliases(target_dir, version_digests)
        completed = True
        return target_dir
    except (OSError, KeyError, TypeError, ValueError, sqlite3.Error,BadZipFile,LargeZipFile,RuntimeError,NotImplementedError) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError(f"Invalid backup: {exc}") from exc
    finally:
        if staging.exists():
            shutil.rmtree(staging,ignore_errors=True)
        if installed and not completed and target_dir.exists():
            shutil.rmtree(target_dir,ignore_errors=True)

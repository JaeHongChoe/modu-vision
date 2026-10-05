"""Read attributable image/label/data/model/flow/inspection evidence together."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from backend.api.routes_project import get_current_project
from backend.api.routes_dataset import _split_manifest_file
from backend.api.routes_inspections import _store, _read_run
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_metadata import metadata_for_path

router = APIRouter(prefix="/api/provenance", tags=["provenance"])


@router.get('/impact')
def workflow_impact(request: Request):
    from backend.engine.workflow_impact import analyze
    try:
        return analyze(get_current_project(request))
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise HTTPException(409, str(exc)) from exc


def _hash(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path):
    if path.is_symlink() or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (ValueError, OSError):
        return None


def _owned(root: Path, path: Path) -> bool:
    return (path.resolve().is_relative_to(root.resolve())
            and not any(parent.is_symlink() for parent in (path, *path.parents)))


def _review_summary(binding):
    if not isinstance(binding, dict):
        return None
    settings = binding.get('settings')
    eligibility = binding.get('eligibility')
    if not isinstance(settings, dict) or not isinstance(eligibility, list):
        return None
    return {'book_version': binding.get('book_version'), 'book_sha256': binding.get('book_sha256'),
            'policy_revision': settings.get('revision'),
            'policy_sha256': binding.get('policy_sha256'),
            'eligibility_sha256': binding.get('eligibility_sha256'),
            'eligible_count': len(eligibility),
            'scope': binding.get('scope')}


@router.get("")
def trace(request: Request, image_path: str):
    project = get_current_project(request)
    source = Path(project.get("source_dataset_dir") or "").expanduser().resolve()
    image = Path(image_path).expanduser().resolve()
    if not project.get("source_dataset_dir") or not image.is_relative_to(source):
        raise HTTPException(409, "Image does not belong to the active project source")
    if not image.is_file():
        raise HTTPException(404, "Source image is unavailable")
    try:
        metadata = metadata_for_path(Path(project["project_dir"]), source, image, Path(project["annotations_dir"]))
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    root = Path(project["project_dir"])
    from backend.engine.team_data import review_trace
    try:
        review_binding = review_trace(project, source, image)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
    annotation = dataset_annotation_dir(image.parent, Path(project["annotations_dir"]), use_scope=False) / f"{image.stem}.json"
    if not annotation.is_file():
        annotation = image.with_suffix(".json")
    from backend.engine.annotation_formats import source_annotation_files
    annotation_paths = [annotation] if annotation.is_file() else source_annotation_files(source, image)
    label = {"path": str(annotation) if annotation.is_file() else None,
             "paths": [str(path) for path in annotation_paths],
             "sha256": metadata.get('annotation_hash'), "mask_sha256": metadata.get('mask_hash'),
             "labelset_id": project.get("active_labelset_id", "default"), "revision": metadata["revision"],
             "workflow_state": metadata["workflow_state"], "reviewer": metadata.get("reviewer"),
             "history": metadata.get("audit", []), "reviews": metadata.get("review_history", [])}
    split_path = _split_manifest_file(source)
    split = _json(split_path) or {}
    assignments = split.get("assignments", {})
    assigned = assignments.get(image.relative_to(source).as_posix(), assignments.get(image.name))
    split_record = {"id": _hash(split_path), "path": str(split_path) if split_path.exists() else None,
                    "assignment": assigned, "sha256": _hash(split_path)}
    versions = []
    for folder in sorted((root / "versions").iterdir(), reverse=True) if (root / "versions").is_dir() else []:
        if not _owned(root / 'versions', folder):
            continue
        record = _json(folder / "manifest.json")
        if not record or Path(record.get("source_dataset_dir", "")).resolve() != source:
            continue
        matches = [r for r in record.get("files", []) if r.get("kind") == "image"
                   and r.get("relative_path") == image.relative_to(source).as_posix()]
        if matches:
            receipt = _json(folder / 'team-data.json')
            versions.append({"id": record["id"], "name": record.get("name"), "created_at": record.get("created_at"),
                             "labelset_id": record.get('labelset_id', 'default'),
                             "review_binding": _review_summary(receipt),
                             "review_receipt_sha256": _hash(folder / 'team-data.json'),
                             "sha256": record.get("content_digest"), "dataset_fingerprint": record.get("dataset_fingerprint"),
                             "image_sha256": matches[0].get("sha256"),
                             "image_matches": matches[0].get("sha256") == metadata["content_hash"]})
    models = []
    model_root = Path(project["models_dir"])
    for meta_path in model_root.rglob("model_meta.json") if model_root.is_dir() else []:
        if not _owned(root, meta_path):
            continue
        meta = _json(meta_path)
        if not meta:
            continue
        provenance = meta.get("dataset_provenance") or meta.get("provenance") or {}
        training = meta.get('training_provenance') or {}
        bound_path = Path(training['version_dir']) / 'manifest.json' if training.get('version_dir') else None
        bound_manifest = _json(bound_path) if bound_path and _owned(root / 'versions', bound_path) else None
        model_source = (bound_manifest or {}).get('source_dataset_dir') or meta.get("source_dataset_path") or provenance.get("source_dataset_path") or meta.get("dataset_path") or provenance.get("dataset_path")
        if model_source and Path(model_source).expanduser().resolve() != source:
            continue
        checkpoint = meta_path.parent / "best_model.pt"
        if not checkpoint.is_file():
            checkpoint = meta_path.parent / "model.pt"
        from backend.engine.workflow_impact import _data_state
        from backend.api.routes_dataset_versions import _manifest_digest
        data_state, data_reason = _data_state(project, training)
        manifest_verified = bool(bound_manifest and training.get('manifest_sha256')
                                 and _manifest_digest(bound_manifest) == training['manifest_sha256'])
        models.append({"job_id": meta_path.parent.name, "task": meta.get("task"), "sha256": _hash(checkpoint),
                       "data_state": data_state, "data_reason": data_reason,
                       "bound_manifest_verified": manifest_verified,
                       "dataset_fingerprint": meta.get("dataset_fingerprint"), "dataset_provenance": provenance,
                       "training_provenance": training,
                       "parent_job_id": meta.get("parent_job_id"), "source_dataset_path": model_source,
                       "source_verified": bool(model_source), "path": str(checkpoint) if checkpoint.is_file() else None})
    flows = []
    flow_root = root / "flowcharts" / "versions"
    for path in sorted(flow_root.glob("*.json"), reverse=True):
        if not _owned(flow_root, path):
            continue
        record = _json(path)
        if not record or Path(record.get("source_dataset_path", "")).resolve() != source:
            continue
        pipeline = record.get("pipeline", {})
        flows.append({"version_id": record.get("version_id"), "sha256": record.get("pipeline_hash") or _hash(path),
                      "name": pipeline.get("name"), "created_at": record.get("created_at"),
                      "model_job_ids": list(dict.fromkeys(n.get("data", {}).get("model_job_id") for n in pipeline.get("nodes", []) if n.get("data", {}).get("model_job_id")))})
    inspections = []
    if (root / "inspection_history.sqlite3").is_file():
        with _store(request) as conn:
            for run in conn.execute("SELECT DISTINCT runs.run_id FROM runs JOIN rows ON runs.run_id=rows.run_id WHERE rows.image_path=? ORDER BY runs.created_at DESC", (str(image),)).fetchall():
                record = _read_run(conn, run["run_id"])
                for row in record["rows"]:
                    if Path(row["image"]["file_path"]).resolve() != image:
                        continue
                    inspections.append({"run_id": record["run_id"], "created_at": record["created_at"],
                                        "state": row["state"], "result": row["result"], "error": row["error"],
                                        "image_sha256": row["image_sha256"],
                                        "image_matches": row["image_sha256"] == metadata["content_hash"],
                                        "flow_version_id": record.get("saved_version_id"), "flow_sha256": record["pipeline_hash"],
                                        "execution_config": record.get("execution_config"),
                                        "model_sha256": record["model_sha256"], "reviews": row["reviews"]})
    return {"project": {k: project.get(k) for k in ("id", "name", "task", "active_labelset_id")},
            "image": metadata, "label": label, "split": split_record, "review_binding": review_binding,
            "dataset_versions": versions, "models": models, "flows": flows, "inspections": inspections,
            "limits": "Current editable labels are separate from historical snapshots. A matching source alone does not prove which label version trained a legacy model."}

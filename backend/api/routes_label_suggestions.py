"""Reviewable model label proposals for the active project and dataset.

Inference creates a pending proposal only. Accepting selected candidates saves a
Studio overlay after taking an immutable label snapshot; source images and
LabelMe files are never written by this route.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Request
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

from backend.api import routes_annotation, routes_dataset
from backend.api.routes_dataset_versions import _snapshot, _source_path, _VERSION_LOCK
from backend.api.routes_project import _write_json, get_current_project
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.checkpoint_paths import completed_job_receipt, trusted_checkpoint
from backend.engine.dataset_loaders import (
    SUPPORTED_IMAGE_EXTENSIONS, reset_request_split_root, set_request_split_root,
)
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.trainer import infer


router = APIRouter(prefix="/api/label-suggestions", tags=["label-suggestions"])
_SUGGESTION_ID = re.compile(r"suggestion_[a-f0-9]{24}\Z")
_BATCH_ID = re.compile(r"batch_[a-f0-9]{24}\Z")
_REVIEW_LOCK = threading.RLock()
_BATCH_LOCK = threading.RLock()


@dataclass
class _LiveBatch:
    batch: Dict[str, Any]
    cancel: threading.Event
    thread: threading.Thread


_LIVE_BATCHES: Dict[str, _LiveBatch] = {}


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: str = Field(..., min_length=1)
    image_path: str = Field(..., min_length=1)
    threshold: float = Field(0.5, ge=0.0, le=1.0)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: Literal["accept", "reject"]
    candidate_ids: List[str] = Field(default_factory=list)


class BatchGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: str = Field(..., min_length=1)
    threshold: float = Field(0.5, ge=0.0, le=1.0)
    image_paths: Optional[List[str]] = Field(None, max_length=5000)


def _sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _model_catalog(project: Dict[str, Any]) -> List[Dict[str, Any]]:
    source = _source_path(project)
    project_root = Path(project["models_dir"])
    candidates = [
        *project_root.glob("job_*"),
        *Path("./models").glob("job_*"),
        *Path("./projects").glob("job_*/models"),
    ]
    models: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for directory in candidates:
        job_id = directory.parent.name if directory.name == "models" else directory.name
        if job_id in seen:
            continue
        checkpoint = trusted_checkpoint(job_id, str(directory.resolve()))
        if checkpoint is None:
            continue
        receipt = completed_job_receipt(directory)
        if not receipt or receipt.get("status") != "completed":
            continue
        recorded_source = receipt.get("source_dataset_path")
        if not isinstance(recorded_source, str) or Path(recorded_source).expanduser().resolve() != source:
            continue
        meta_path = directory / "model_meta.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                continue
        except (OSError, ValueError):
            continue
        task = receipt.get("task") or meta.get("task")
        if task != project["task"] or meta.get("task") not in (None, task):
            continue
        seen.add(job_id)
        models.append({
            "job_id": job_id,
            "task": task,
            "classes": meta.get("classes", []),
            "created_at": meta.get("created_at"),
            "checkpoint_path": str(checkpoint),
            "source_dataset_path": str(source),
        })
    return models


def _image_path(project: Dict[str, Any], requested: str) -> Path:
    source = _source_path(project)
    requested_path = Path(requested).expanduser()
    if not requested_path.is_absolute():
        raise HTTPException(status_code=422, detail="Image path must be absolute.")
    # A symlinked image within the imported tree is allowed, while arbitrary
    # paths outside its visible dataset hierarchy are not.
    candidate = Path(os.path.abspath(requested_path))
    if not candidate.is_relative_to(source) or candidate.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=422, detail="Image is outside the active dataset.")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="Image is unavailable.")
    return candidate


def _proposal_root(project: Dict[str, Any]) -> Path:
    root = Path(project["project_dir"]) / "label_suggestions"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Suggestion directory cannot be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _proposal_path(project: Dict[str, Any], suggestion_id: str) -> Path:
    if not _SUGGESTION_ID.fullmatch(suggestion_id):
        raise HTTPException(status_code=422, detail="Invalid suggestion ID.")
    return _proposal_root(project) / f"{suggestion_id}.json"


def _read_proposal(project: Dict[str, Any], suggestion_id: str) -> Dict[str, Any]:
    path = _proposal_path(project, suggestion_id)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("id") != suggestion_id or value.get("project_id") != project["id"]:
            raise ValueError("proposal identity mismatch")
        return value
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Suggestion not found.") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid suggestion: {exc}") from exc


def _candidate_annotations(task: str, predictions: Any, score: float, suggestion_id: str) -> List[Dict[str, Any]]:
    annotations: List[tuple[Dict[str, Any], float]] = []
    if task == "detection":
        for item in predictions if isinstance(predictions, list) else []:
            if isinstance(item, dict) and isinstance(item.get("bbox"), list) and len(item["bbox"]) == 4:
                annotations.append(({
                    "type": "bbox", "label": str(item.get("label") or "defect"),
                    "bbox": [float(value) for value in item["bbox"]],
                    "category_id": 1, "color": "#22d3ee",
                }, float(item.get("score", score))))
    elif task == "segmentation":
        contours = predictions.get("polygon_contours", []) if isinstance(predictions, dict) else []
        for item in contours:
            if not isinstance(item, dict) or item.get("is_hole"):
                continue
            points = item.get("points")
            if not isinstance(points, list) or len(points) < 3:
                continue
            annotations.append(({
                "type": "polygon", "label": str(item.get("class_name") or "defect"),
                "polygon": [[float(x), float(y)] for x, y in points],
                "category_id": max(1, int(item.get("class_id") or 1)), "color": "#f59e0b",
            }, score))
    elif task == "classification" and isinstance(predictions, dict):
        label = str(predictions.get("predicted_class") or "Unknown")
        annotations.append(({
            "type": "tag", "label": label, "category_id": int(predictions.get("class_index", 0)) + 1,
            "is_normal": label.casefold() in {"ok", "good", "normal", "pass"}, "color": "#22d3ee",
        }, float(predictions.get("confidence", score))))
    elif task == "anomaly" and isinstance(predictions, dict):
        anomalous = bool(predictions.get("is_anomaly"))
        annotations.append(({
            "type": "tag", "label": "Anomaly" if anomalous else "OK", "category_id": 1,
            "is_normal": not anomalous, "color": "#f59e0b" if anomalous else "#34d399",
        }, float(predictions.get("anomaly_score", score))))
    return [
        {"id": f"{suggestion_id}_c{index}", "confidence": round(confidence, 4),
         "annotation": {**annotation, "id": f"{suggestion_id}_c{index}"}}
        for index, (annotation, confidence) in enumerate(annotations, 1)
    ]


def _verified_model(project: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    """Require a completed model trained on this unchanged source/label set."""
    model = next((item for item in _model_catalog(project) if item["job_id"] == job_id), None)
    if model is None:
        raise HTTPException(status_code=404, detail="No completed model matches this project's task and dataset.")
    from backend.api.routes_evaluation import _matches_source_dataset

    if not _matches_source_dataset(Path(model["checkpoint_path"]).parent,
                                   str(_source_path(project)), project["task"]):
        raise HTTPException(status_code=409, detail="The model's training dataset fingerprint no longer matches this project's labels or split.")
    return model


def _project_binding_is_current(project: Dict[str, Any]) -> bool:
    try:
        saved = json.loads((Path(project["project_dir"]) / "project.json").read_text(encoding="utf-8"))
        if not isinstance(saved, dict):
            return False
        recorded = saved.get("source_dataset_dir")
        return (saved.get("id") == project.get("id")
                and saved.get("task") == project.get("task")
                and isinstance(recorded, str)
                and Path(recorded).expanduser().resolve() == _source_path(project))
    except (OSError, ValueError, TypeError):
        return False


def _dataset_fingerprint(project: Dict[str, Any]) -> str:
    source = _source_path(project)
    return fingerprint_dataset(
        source, studio_root=Path(project["annotations_dir"]),
        split_manifest=routes_dataset._split_manifest_file(source), use_scope=False,
    )


def _generate_proposal(project: Dict[str, Any], image: Path, model: Dict[str, Any],
                       threshold: float, batch_id: Optional[str] = None) -> Dict[str, Any]:
    checkpoint = Path(model["checkpoint_path"])
    studio = dataset_annotation_dir(image.parent, Path(project["annotations_dir"]), use_scope=False)
    labelme = image.with_suffix(".json")
    studio_json = studio / f"{image.stem}.json"
    before_image = _sha256(image)
    before_checkpoint = _sha256(checkpoint)
    before_labelme = _sha256(labelme)
    before_studio = _sha256(studio_json)
    before_dataset = _dataset_fingerprint(project)
    if not before_image or not before_checkpoint:
        raise HTTPException(status_code=409, detail="Image or checkpoint became unavailable.")
    with Image.open(image) as pil:
        width, height = pil.size
    result = infer(task=project["task"], model_path=checkpoint, image_input=image,
                   threshold=threshold, device="cpu")
    if (_sha256(image) != before_image or _sha256(checkpoint) != before_checkpoint
            or _sha256(labelme) != before_labelme or _sha256(studio_json) != before_studio):
        raise HTTPException(status_code=409, detail="Image, labels, or checkpoint changed during inference.")
    if not _project_binding_is_current(project):
        raise HTTPException(status_code=409, detail="Project identity, task, or source changed during inference.")
    if _dataset_fingerprint(project) != before_dataset:
        raise HTTPException(status_code=409, detail="Training dataset fingerprint changed during inference.")
    from backend.api.routes_evaluation import _matches_source_dataset

    if not _matches_source_dataset(checkpoint.parent, str(_source_path(project)), project["task"]):
        raise HTTPException(status_code=409, detail="Training dataset fingerprint no longer matches this model.")
    suggestion_id = f"suggestion_{uuid.uuid4().hex[:24]}"
    proposal = {
        "id": suggestion_id, "project_id": project["id"], "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image_path": str(image), "image_id": image.stem, "image_width": width, "image_height": height,
        "image_sha256": before_image, "labelme_sha256": before_labelme,
        "studio_sha256": before_studio,
        "job_id": model["job_id"], "task": project["task"], "checkpoint_sha256": before_checkpoint,
        "dataset_fingerprint": before_dataset,
        "threshold": threshold, "confidence": round(float(result.confidence_score), 4),
        "latency_ms": round(float(result.latency_ms), 2),
        "candidates": _candidate_annotations(project["task"], result.predictions,
                                             float(result.confidence_score), suggestion_id),
        "accepted_candidate_ids": [], "backup_version_id": None,
    }
    if batch_id:
        proposal["batch_id"] = batch_id
    _write_json(_proposal_path(project, suggestion_id), proposal)
    return proposal


@router.get("/models")
def list_suggestion_models(request: Request):
    project = get_current_project(request)
    from backend.api.routes_evaluation import _matches_source_dataset

    source = str(_source_path(project))
    return {"models": [
        model for model in _model_catalog(project)
        if _matches_source_dataset(Path(model["checkpoint_path"]).parent, source, project["task"])
    ]}


@router.post("/generate")
def generate_suggestion(req: GenerateRequest, request: Request):
    project = get_current_project(request)
    image = _image_path(project, req.image_path)
    return _generate_proposal(project, image, _verified_model(project, req.job_id), req.threshold)


@router.get("")
def list_suggestions(request: Request, image_path: Optional[str] = None):
    project = get_current_project(request)
    source = _source_path(project)
    selected = str(_image_path(project, image_path)) if image_path else None
    results = []
    for path in sorted(_proposal_root(project).glob("suggestion_*.json"), reverse=True):
        if not _SUGGESTION_ID.fullmatch(path.stem):
            continue
        try:
            item = _read_proposal(project, path.stem)
            image = item.get("image_path")
            if (item.get("task") == project["task"] and isinstance(image, str)
                    and Path(image).is_relative_to(source) and (selected is None or image == selected)):
                results.append(item)
        except HTTPException:
            continue
    return {"suggestions": results}


def _batch_root(project: Dict[str, Any]) -> Path:
    root = _proposal_root(project) / "batches"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Batch directory cannot be a symbolic link.")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _batch_path(project: Dict[str, Any], batch_id: str) -> Path:
    if not _BATCH_ID.fullmatch(batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch ID.")
    return _batch_root(project) / f"{batch_id}.json"


def _persist_batch(project: Dict[str, Any], batch: Dict[str, Any]) -> None:
    _write_json(_batch_path(project, batch["id"]), batch)


def _read_batch(project: Dict[str, Any], batch_id: str) -> Dict[str, Any]:
    path = _batch_path(project, batch_id)
    if path.is_symlink():
        raise HTTPException(status_code=422, detail="Batch file cannot be a symbolic link.")
    try:
        batch = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(batch, dict) or batch.get("id") != batch_id
                or batch.get("project_id") != project["id"]
                or batch.get("source_dataset_dir") != project.get("source_dataset_dir")):
            raise ValueError("batch identity mismatch")
        return batch
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Batch not found in this project.") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid batch: {exc}") from exc


def _project_batch_key(project: Dict[str, Any]) -> str:
    return str(Path(project["project_dir"]).resolve())


def _batch_for_read(project: Dict[str, Any], batch_id: str) -> Dict[str, Any]:
    with _BATCH_LOCK:
        batch = _read_batch(project, batch_id)
        live = _LIVE_BATCHES.get(_project_batch_key(project))
        if batch["status"] in {"running", "cancelling"} and (live is None or live.batch["id"] != batch_id):
            batch["status"] = "interrupted"
            batch["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _persist_batch(project, batch)
        return batch


def _batch_images(project: Dict[str, Any], requested: Optional[List[str]]) -> List[Path]:
    source = _source_path(project)
    if requested is not None:
        result: List[Path] = []
        seen: set[str] = set()
        for value in requested:
            image = _image_path(project, value)
            if str(image) not in seen:
                result.append(image)
                seen.add(str(image))
    else:
        # Stage 1's `label_status` is exact for a flat LabelMe source. In a
        # structured COCO, YOLO, or class-folder dataset, labels can live in
        # another tree and an absent adjacent JSON does not mean unlabeled.
        root_images = {
            path.stem for path in source.iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
        }
        flat_labelme = routes_dataset._has_flat_labelme_annotations(source)
        structured = (
            project["task"] not in {"detection", "segmentation"}
            or not root_images or not flat_labelme
            or any(path.is_dir() and not path.name.startswith(".") for path in source.iterdir())
            or any(not routes_dataset.is_valid_labelme_file(path, require_image=True)
                   for path in source.glob("*.json"))
            or any((source / f"{stem}.txt").is_file() for stem in root_images)
        )
        if structured:
            raise HTTPException(
                status_code=422,
                detail="Automatic unlabeled selection supports flat LabelMe datasets only. Select images explicitly for COCO, YOLO, or class-folder datasets.",
            )
        result = []
        offset = 0
        seen: set[str] = set()
        while True:
            # Reuse the Stage 1 gallery's label status, including its handling
            # of an empty Studio edit that overrides source LabelMe shapes.
            page = routes_dataset.list_dataset_images(
                folder_path=str(source), task=project["task"], limit=500, offset=offset,
                split=None, class_name=None, label_status="unlabeled",
            )
            for item in page["items"]:
                image = _image_path(project, item["file_path"])
                if str(image) not in seen:
                    result.append(image)
                    seen.add(str(image))
                    if len(result) > 5000:
                        raise HTTPException(status_code=413, detail="More than 5,000 unlabeled images. Select a smaller batch.")
            offset += len(page["items"])
            if offset >= page["total"] or not page["items"]:
                break
    if not result:
        raise HTTPException(status_code=422, detail="No images selected for model-assisted labeling.")
    return result


def _run_batch(project: Dict[str, Any], model: Dict[str, Any], live: _LiveBatch) -> None:
    annotation_token = set_request_annotation_root(Path(project["annotations_dir"]))
    split_token = set_request_split_root(Path(project["dataset_dir"]) / "splits")
    project_token = set_request_project_root(Path(project["project_dir"]))
    batch = live.batch
    checkpoint = Path(model["checkpoint_path"])
    try:
        from backend.api.routes_evaluation import _matches_source_dataset

        for entry in batch["entries"]:
            if live.cancel.is_set():
                break
            if (not _project_binding_is_current(project) or _sha256(checkpoint) != batch["checkpoint_sha256"] or
                    not _matches_source_dataset(checkpoint.parent, batch["source_dataset_dir"], batch["task"])):
                with _BATCH_LOCK:
                    entry.update(status="failed", error="The project identity, task, source, checkpoint, or training dataset fingerprint changed during this batch.")
                    batch["processed"] += 1
                    batch["failed"] += 1
                    batch["status"] = "failed"
                    batch["error"] = entry["error"]
                    _persist_batch(project, batch)
                break
            try:
                image = _image_path(project, entry["image_path"])
                proposal = _generate_proposal(project, image, model, batch["threshold"], batch["id"])
                result = {
                    "status": "generated" if proposal["candidates"] else "zero_candidates",
                    "proposal_id": proposal["id"], "candidate_count": len(proposal["candidates"]),
                }
            except Exception as exc:
                result = {"status": "failed", "error": str(exc)}
            with _BATCH_LOCK:
                entry.update(result)
                batch["processed"] += 1
                if result["status"] == "generated":
                    batch["generated"] += 1
                elif result["status"] == "zero_candidates":
                    batch["zero_candidates"] += 1
                else:
                    batch["failed"] += 1
                _persist_batch(project, batch)
        with _BATCH_LOCK:
            if batch["status"] != "failed":
                batch["status"] = "cancelled" if live.cancel.is_set() else "completed"
            batch["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _persist_batch(project, batch)
    except Exception as exc:
        with _BATCH_LOCK:
            batch["status"] = "failed"
            batch["error"] = str(exc)
            batch["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _persist_batch(project, batch)
    finally:
        reset_request_project_root(project_token)
        reset_request_split_root(split_token)
        reset_request_annotation_root(annotation_token)
        with _BATCH_LOCK:
            if _LIVE_BATCHES.get(_project_batch_key(project)) is live:
                del _LIVE_BATCHES[_project_batch_key(project)]


@router.post("/batches")
def start_batch(req: BatchGenerateRequest, request: Request):
    project = get_current_project(request)
    model = _verified_model(project, req.job_id)
    images = _batch_images(project, req.image_paths)
    checkpoint_sha256 = _sha256(Path(model["checkpoint_path"]))
    if checkpoint_sha256 is None:
        raise HTTPException(status_code=409, detail="Checkpoint became unavailable.")
    batch_id = f"batch_{uuid.uuid4().hex[:24]}"
    batch = {
        "id": batch_id, "project_id": project["id"], "source_dataset_dir": str(_source_path(project)),
        "job_id": req.job_id, "task": project["task"], "threshold": req.threshold,
        "review_fingerprint": _dataset_fingerprint(project),
        "checkpoint_sha256": checkpoint_sha256, "status": "running",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "finished_at": None, "total": len(images), "processed": 0,
        "generated": 0, "zero_candidates": 0, "failed": 0,
        "entries": [{"image_path": str(image), "image_id": image.stem, "status": "queued"} for image in images],
    }
    project_key = _project_batch_key(project)
    cancel = threading.Event()
    with _BATCH_LOCK:
        if project_key in _LIVE_BATCHES:
            raise HTTPException(status_code=409, detail="A model-assisted labeling batch is already running for this project.")
        _persist_batch(project, batch)
        live = _LiveBatch(batch=batch, cancel=cancel, thread=threading.Thread(
            target=lambda: _run_batch(project, model, live), name=f"LabelBatch-{batch_id}", daemon=True,
        ))
        _LIVE_BATCHES[project_key] = live
        try:
            live.thread.start()
        except RuntimeError as exc:
            del _LIVE_BATCHES[project_key]
            batch.update(status="failed", error=str(exc), finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            _persist_batch(project, batch)
            raise HTTPException(status_code=500, detail="Could not start model-assisted labeling batch.") from exc
    return batch


@router.get("/batches")
def list_batches(request: Request):
    project = get_current_project(request)
    results = []
    for path in sorted(_batch_root(project).glob("batch_*.json"), reverse=True):
        if _BATCH_ID.fullmatch(path.stem):
            try:
                results.append(_batch_for_read(project, path.stem))
            except HTTPException:
                continue
    return {"batches": results}


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, request: Request):
    return _batch_for_read(get_current_project(request), batch_id)


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id: str, request: Request):
    project = get_current_project(request)
    with _BATCH_LOCK:
        batch = _batch_for_read(project, batch_id)
        live = _LIVE_BATCHES.get(_project_batch_key(project))
        if live is not None and live.batch["id"] == batch_id and batch["status"] == "running":
            live.cancel.set()
            live.batch["status"] = "cancelling"
            _persist_batch(project, live.batch)
            return live.batch
        return batch


@router.get("/{suggestion_id}")
def get_suggestion(suggestion_id: str, request: Request):
    return _read_proposal(get_current_project(request), suggestion_id)


def _restore_bytes(path: Path, previous: Optional[bytes]) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.rollback"
        temporary.write_bytes(previous)
        os.replace(temporary, path)


@router.post("/{suggestion_id}/review")
def review_suggestion(suggestion_id: str, req: ReviewRequest, request: Request):
    project = get_current_project(request)
    with _REVIEW_LOCK, _VERSION_LOCK:
        proposal = _read_proposal(project, suggestion_id)
        if proposal["status"] != "pending":
            raise HTTPException(status_code=409, detail="Suggestion has already been reviewed.")
        if req.decision == "reject":
            proposal.update(status="rejected", reviewed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            _write_json(_proposal_path(project, suggestion_id), proposal)
            return proposal
        if proposal.get("task") != project["task"]:
            raise HTTPException(status_code=409, detail="Project task changed after proposal generation.")

        with _BATCH_LOCK:
            if _project_batch_key(project) in _LIVE_BATCHES:
                raise HTTPException(status_code=409, detail="Finish or cancel the active label batch before accepting candidates.")

        selected_ids = set(req.candidate_ids)
        candidates = proposal.get("candidates", [])
        available = {item["id"] for item in candidates}
        if not selected_ids or not selected_ids.issubset(available):
            raise HTTPException(status_code=422, detail="Select one or more proposal candidates to accept.")
        image = _image_path(project, proposal["image_path"])
        model = next((item for item in _model_catalog(project) if item["job_id"] == proposal["job_id"]), None)
        checkpoint = Path(model["checkpoint_path"]) if model else None
        studio = dataset_annotation_dir(image.parent, routes_dataset.STUDIO_ANNOTATIONS_DIR)
        json_path = studio / f"{image.stem}.json"
        mask_path = studio / "masks" / f"{image.stem}.png"
        if (_sha256(image) != proposal["image_sha256"]
                or _sha256(image.with_suffix(".json")) != proposal["labelme_sha256"]
                or _sha256(json_path) != proposal["studio_sha256"]
                or checkpoint is None or _sha256(checkpoint) != proposal["checkpoint_sha256"]):
            raise HTTPException(status_code=409, detail="Image, labels, or checkpoint changed after proposal generation. Generate again.")
        batch = _read_batch(project, proposal["batch_id"]) if proposal.get("batch_id") else None
        expected_fingerprint = (
            batch.get("review_fingerprint") if batch is not None else proposal.get("dataset_fingerprint")
        )
        if expected_fingerprint is None:
            from backend.api.routes_evaluation import _matches_source_dataset

            matches_training = _matches_source_dataset(checkpoint.parent, str(_source_path(project)), project["task"])
        else:
            matches_training = _dataset_fingerprint(project) == expected_fingerprint
        if not matches_training:
            raise HTTPException(status_code=409, detail="Training dataset fingerprint changed after proposal generation. Generate again.")
        from backend.api.routes_training import training_job_manager
        if training_job_manager.get_active_job() is not None:
            raise HTTPException(status_code=409, detail="Finish or stop active training before accepting labels.")

        current = routes_annotation.get_annotations(image.stem, file_path=str(image))
        existing = current.get("annotations", [])
        if not isinstance(existing, list):
            raise HTTPException(status_code=422, detail="Existing annotation file is invalid.")
        accepted = [item["annotation"] for item in candidates if item["id"] in selected_ids]
        annotation_request = routes_annotation.AnnotationSaveRequest(
            image_id=image.stem, image_path=str(image), annotations=[*existing, *accepted],
            image_width=proposal["image_width"], image_height=proposal["image_height"],
        )
        backup = _snapshot(project, _source_path(project),
                           f"모델 라벨 채택 전 · {image.name}",
                           f"Before accepting {suggestion_id}", "auto_backup")
        previous_json = json_path.read_bytes() if json_path.is_file() else None
        previous_mask = mask_path.read_bytes() if mask_path.is_file() else None
        previous_proposal = _proposal_path(project, suggestion_id).read_bytes()
        batch_path = _batch_path(project, batch["id"]) if batch is not None else None
        previous_batch = batch_path.read_bytes() if batch_path is not None else None
        try:
            routes_annotation.save_annotations(annotation_request)
            if batch is not None:
                # A verified acceptance is the only dataset change that may
                # advance this batch's review baseline. External label/split
                # edits between reviews still produce a fingerprint mismatch.
                batch["review_fingerprint"] = _dataset_fingerprint(project)
                _persist_batch(project, batch)
            proposal.update(
                status="accepted", accepted_candidate_ids=sorted(selected_ids),
                backup_version_id=backup["id"],
                reviewed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            _write_json(_proposal_path(project, suggestion_id), proposal)
        except Exception:
            _restore_bytes(json_path, previous_json)
            _restore_bytes(mask_path, previous_mask)
            _restore_bytes(_proposal_path(project, suggestion_id), previous_proposal)
            if batch_path is not None:
                _restore_bytes(batch_path, previous_batch)
            raise
        return proposal

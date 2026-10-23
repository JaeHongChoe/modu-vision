"""Reviewable model label proposals for the active project and dataset.

Inference creates a pending proposal only. Accepting selected candidates saves a
Studio overlay after taking an immutable label snapshot; source images and
LabelMe files are never written by this route.
"""

from __future__ import annotations

import hashlib
import json
import math
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
from backend.engine import dataset_metadata as metadata_engine


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
    keywords: List[str] = Field(default_factory=list, max_length=100)
    device: str = 'cpu'
    min_area: float = Field(0,ge=0)
    max_area: Optional[float] = Field(None,ge=0)
    min_width: float = Field(0,ge=0)
    max_width: Optional[float] = Field(None,ge=0)
    min_height: float = Field(0,ge=0)
    max_height: Optional[float] = Field(None,ge=0)


# Ledger events that advance an image's revision without touching its labels: taking or giving back the edit lease
# that team editing requires. Everything else (a save, a class change, a review, an assignment) makes a draft stale.
_LEASE_ONLY_EVENTS = {"edit_lease_acquired", "edit_lease_released"}


def _changed_since_draft(metadata: Dict[str, Any], proposal: Dict[str, Any]) -> bool:
    """Whether the image changed since the draft was made, apart from edit-lease events.

    The labels and the image are compared by content hash before this; the revision catches the other ledger
    changes. A reviewer must take the edit lease to adopt under team editing, and that alone must not refuse them."""
    drafted = proposal.get("image_revision")
    if drafted is None or metadata["revision"] == drafted:
        return False
    later = [event for event in metadata.get("audit", []) if int(event.get("revision", 0)) > int(drafted)]
    if len(later) != metadata["revision"] - drafted:
        return True
    return any(event.get("action") not in _LEASE_ONLY_EVENTS for event in later)


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    decision: Literal["accept", "reject"]
    candidate_ids: List[str] = Field(default_factory=list)
    actor: str = Field("operator", min_length=1, max_length=100)
    # With team editing on, a save needs the image's edit lease; the reviewer passes the one they hold.
    lease_token: Optional[str] = Field(None, min_length=1, max_length=200)


class BatchGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: str = Field(..., min_length=1)
    threshold: float = Field(0.5, ge=0.0, le=1.0)
    keywords: List[str] = Field(default_factory=list, max_length=100)
    image_paths: Optional[List[str]] = Field(None, max_length=5000)
    device: str = 'cpu'
    min_area: float = Field(0,ge=0)
    max_area: Optional[float] = Field(None,ge=0)
    min_width: float = Field(0,ge=0)
    max_width: Optional[float] = Field(None,ge=0)
    min_height: float = Field(0,ge=0)
    max_height: Optional[float] = Field(None,ge=0)

class FeatureTrainRequest(BaseModel):
    device: str = 'cpu'
    backbone: str = 'dinov3_vits16'
    pretrained_checkpoint: Optional[str] = None
    pretrained_sha256: Optional[str] = None
    parent_model_id: Optional[str] = None
    epochs: int = Field(100, ge=1, le=1000)
    learning_rate: float = Field(.05, gt=0, le=1)
    image_paths: Optional[List[str]] = Field(None, min_length=1, max_length=5000)

@router.get('/feature-jobs')
def list_feature_training_jobs(request: Request):
    from backend.engine import labeling_tasks
    project=get_current_project(request)
    return {'jobs': [job for job in labeling_tasks.list_jobs(project, 'feature_train') if job.get('labelset_id')==project.get('active_labelset_id','default')]}

@router.post('/feature-train')
def start_feature_training(req: FeatureTrainRequest, request: Request):
    from backend.engine import labeling_tasks
    from backend.engine.feature_labeling_jobs import train_feature_model
    from backend.engine.foundation_labeling import resolve_device
    project = dict(get_current_project(request))
    try:
        resolve_device(req.device)
        return labeling_tasks.start(project, 'feature_train', {'options':req.model_dump()},
            lambda job,cancel: train_feature_model(project,req.model_dump(),job,cancel))
    except ValueError as exc: raise HTTPException(422, detail=str(exc)) from exc

@router.get('/feature-models')
def list_feature_suggestion_models(request: Request):
    from backend.engine.feature_labeling_jobs import list_feature_models
    return {'models': list_feature_models(get_current_project(request))}

@router.get('/feature-train/{job_id}')
def get_feature_training(job_id: str, request: Request):
    from backend.engine import labeling_tasks
    try: return labeling_tasks.read(get_current_project(request),job_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='Feature training job not found') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc

@router.post('/feature-train/{job_id}/cancel')
def cancel_feature_training(job_id: str, request: Request):
    from backend.engine import labeling_tasks
    try: return labeling_tasks.cancel(get_current_project(request),job_id)
    except FileNotFoundError as exc: raise HTTPException(404,detail='Feature training job not found') from exc
    except ValueError as exc: raise HTTPException(422,detail=str(exc)) from exc


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
        # A model trained under another label set of this project learned that set's classes and truth: it does not
        # draft into the active one (2026-10-23 default; a receipt that names no label set predates the record).
        trained_set = (receipt.get("training_provenance") or {}).get("labelset_id") if isinstance(receipt.get("training_provenance"), dict) else None
        if isinstance(trained_set, str) and trained_set != project.get("active_labelset_id", "default"):
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


def _proposal_root(project: Dict[str, Any], *, create: bool = True) -> Path:
    """The project's suggestion folder; a read (create=False) never makes it, so listing leaves the project unchanged."""
    root = Path(project["project_dir"]) / "label_suggestions"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Suggestion directory cannot be a symbolic link.")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _proposal_path(project: Dict[str, Any], suggestion_id: str, *, create: bool = True) -> Path:
    if not _SUGGESTION_ID.fullmatch(suggestion_id):
        raise HTTPException(status_code=422, detail="Invalid suggestion ID.")
    return _proposal_root(project, create=create) / f"{suggestion_id}.json"


def _read_proposal(project: Dict[str, Any], suggestion_id: str) -> Dict[str, Any]:
    path = _proposal_path(project, suggestion_id, create=False)
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
        masks = predictions.get('mask_candidates') if isinstance(predictions, dict) else None
        if isinstance(masks, list):
            return [{'id':f'{suggestion_id}_c{index}', 'confidence':float(item['confidence']),
                     'annotation':{'id':f'{suggestion_id}_c{index}', 'type':'brush_mask',
                                   'label':item['class_name'], 'category_id':item['class_id'],
                                   'mask_rle':item['mask_rle'], 'color':'#22d3ee'}}
                    for index,item in enumerate(masks,1)]
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


def _model_data_state(project: Dict[str, Any], model: Dict[str, Any]) -> str:
    """'current' when the model's receipt fingerprint equals this project's data now, else 'changed'."""
    from backend.api.routes_evaluation import _matches_source_dataset

    return "current" if _matches_source_dataset(Path(model["checkpoint_path"]).parent, str(_source_path(project)), project["task"]) else "changed"


def _verified_model(project: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    """Require a completed model of this task trained on this source.

    A model trained before the data changed may still draft (2026-10-22 decision): drafting is not evidence, and the
    review checks every draft against the data at adoption time. The row says which it is (data_state)."""
    model = next((item for item in _model_catalog(project) if item["job_id"] == job_id), None)
    if model is None:
        raise HTTPException(status_code=404, detail="No completed model matches this project's task and dataset.")
    return {**model, "data_state": _model_data_state(project, model)}


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
                       threshold: float, batch_id: Optional[str] = None, keywords: Optional[List[str]] = None,
                       device: str = 'cpu', size_bounds=None) -> Dict[str, Any]:
    from backend.engine.foundation_labeling import resolve_device
    try: device=resolve_device(device)
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    metadata = metadata_engine.metadata_for_path(Path(project["project_dir"]), _source_path(project), image, Path(project["annotations_dir"]))
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
                   threshold=threshold, device=device)
    if (_sha256(image) != before_image or _sha256(checkpoint) != before_checkpoint
            or _sha256(labelme) != before_labelme or _sha256(studio_json) != before_studio):
        raise HTTPException(status_code=409, detail="Image, labels, or checkpoint changed during inference.")
    if not _project_binding_is_current(project):
        raise HTTPException(status_code=409, detail="Project identity, task, or source changed during inference.")
    if _dataset_fingerprint(project) != before_dataset:
        raise HTTPException(status_code=409, detail="Training dataset fingerprint changed during inference.")
    suggestion_id = f"suggestion_{uuid.uuid4().hex[:24]}"
    proposal = {
        "id": suggestion_id, "project_id": project["id"], "status": "pending",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "image_path": str(image), "image_id": image.stem, "image_width": width, "image_height": height,
        "image_uuid": metadata["image_uuid"], "image_revision": metadata["revision"],
        "image_sha256": before_image, "labelme_sha256": before_labelme,
        "studio_sha256": before_studio,
        "job_id": model["job_id"], "task": project["task"], "checkpoint_sha256": before_checkpoint,
        "dataset_fingerprint": before_dataset,
        "model_data_state": model.get("data_state") or _model_data_state(project, model),
        "threshold": threshold, "confidence": round(float(result.confidence_score), 4),
        "latency_ms": round(float(result.latency_ms), 2),
        "candidates": _candidate_annotations(project["task"], result.predictions,
                                             float(result.confidence_score), suggestion_id),
        "accepted_candidate_ids": [], "backup_version_id": None,
    }
    from backend.engine.label_candidate_providers import filter_keywords,filter_candidate_sizes
    proposal["candidates"] = filter_keywords(proposal["candidates"], keywords or [])
    try: proposal['candidates']=filter_candidate_sizes(proposal['candidates'],**(size_bounds or {}))
    except ValueError as exc:raise HTTPException(422,detail=str(exc)) from exc
    proposal['device']=device;proposal['size_bounds']=size_bounds or {}
    proposal["keywords"] = keywords or []
    proposal["backend"] = "trained_model"
    if batch_id:
        proposal["batch_id"] = batch_id
    _write_json(_proposal_path(project, suggestion_id), proposal)
    return proposal


@router.get("/models")
def list_suggestion_models(request: Request):
    """Completed models of this task trained on this source, current-data ones first.

    A model whose training data changed since (data_state 'changed') can still draft; it is never evaluation
    evidence, and the screen says so next to it."""
    project = get_current_project(request)
    rows = [{**model, "data_state": _model_data_state(project, model), "trained_at": model.get("created_at")}
            for model in _model_catalog(project)]
    rows.sort(key=lambda row: (row["data_state"] != "current", -(_sortable_time(row.get("trained_at")))))
    return {"models": rows}


def _sortable_time(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return time.mktime(time.strptime(value[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            return 0.0
    return 0.0


@router.post("/generate")
def generate_suggestion(req: GenerateRequest, request: Request):
    project = get_current_project(request)
    image = _image_path(project, req.image_path)
    return _generate_proposal(project, image, _verified_model(project, req.job_id), req.threshold, keywords=req.keywords,
        device=req.device,size_bounds={k:getattr(req,k) for k in ('min_area','max_area','min_width','max_width','min_height','max_height')})


@router.get("")
def list_suggestions(request: Request, image_path: Optional[str] = None):
    project = get_current_project(request)
    source = _source_path(project)
    selected = str(_image_path(project, image_path)) if image_path else None
    results = []
    for path in sorted(_proposal_root(project, create=False).glob("suggestion_*.json"), reverse=True):
        if not _SUGGESTION_ID.fullmatch(path.stem):
            continue
        try:
            item = _read_proposal(project, path.stem)
            image = item.get("image_path")
            if (item.get("task") == project["task"] and isinstance(image, str)
                    and Path(image).is_relative_to(source) and (selected is None or image == selected)):
                item.setdefault("backend", "trained_model")
                results.append(item)
        except HTTPException:
            continue
    return {"suggestions": results}


def _batch_root(project: Dict[str, Any], *, create: bool = True) -> Path:
    root = _proposal_root(project, create=create) / "batches"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Batch directory cannot be a symbolic link.")
    if create:
        root.mkdir(parents=True, exist_ok=True)
    return root


def _batch_path(project: Dict[str, Any], batch_id: str, *, create: bool = True) -> Path:
    if not _BATCH_ID.fullmatch(batch_id):
        raise HTTPException(status_code=422, detail="Invalid batch ID.")
    return _batch_root(project, create=create) / f"{batch_id}.json"


def _persist_batch(project: Dict[str, Any], batch: Dict[str, Any]) -> None:
    _write_json(_batch_path(project, batch["id"]), batch)


def _read_batch(project: Dict[str, Any], batch_id: str) -> Dict[str, Any]:
    path = _batch_path(project, batch_id, create=False)
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
        flat_labelme = routes_dataset._flat_image_workspace(source, project["task"])
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
        for entry in batch["entries"]:
            if live.cancel.is_set():
                break
            # A model trained before the data changed may start a batch (its drafts say so), but the data must not
            # change under a running batch: every draft is reviewed against the batch's start-time fingerprint, so
            # drafts made after a label or split save could never be adopted, and the start-time model_data_state
            # would no longer describe them.
            if (not _project_binding_is_current(project) or _sha256(checkpoint) != batch["checkpoint_sha256"]
                    or _dataset_fingerprint(project) != batch["review_fingerprint"]):
                with _BATCH_LOCK:
                    entry.update(status="failed", error="The project identity, task, source, or checkpoint, or the labels or split, changed during this batch.")
                    batch["processed"] += 1
                    batch["failed"] += 1
                    batch["status"] = "failed"
                    batch["error"] = entry["error"]
                    _persist_batch(project, batch)
                break
            try:
                image = _image_path(project, entry["image_path"])
                proposal = _generate_proposal(project, image, model, batch["threshold"], batch["id"], batch.get("keywords", []),
                    device=batch.get('device','cpu'),size_bounds=batch.get('size_bounds'))
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
        "job_id": req.job_id, "task": project["task"], "threshold": req.threshold, "keywords": req.keywords,
        'device':req.device,'size_bounds':{k:getattr(req,k) for k in ('min_area','max_area','min_width','max_width','min_height','max_height')},
        "review_fingerprint": _dataset_fingerprint(project),
        "model_data_state": model.get("data_state", "current"),
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
    for path in sorted(_batch_root(project, create=False).glob("batch_*.json"), reverse=True):
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


def changed_adopted_ids(adopted: List[Dict[str, Any]], current: List[Any]) -> List[str]:
    """The adopted shapes that no longer match the label file (edited or removed): whole-shape equality by id.

    The take-back and the adoption statistics share this rule, so "edited after adoption" means the same thing in
    both places."""
    by_id = {item["id"]: item for item in current if isinstance(item, dict) and isinstance(item.get("id"), str)}
    return sorted(item["id"] for item in adopted if by_id.get(item["id"]) != item)


class LabelSuggestionThresholdAdvice(BaseModel):
    reason: Literal["too_few", "already_high", "none_reaches", "task_not_applicable", "suggestion"]
    reviewed_candidates: int
    adopted_rate: Optional[float] = None
    threshold: Optional[float] = None
    adopted_rate_above: Optional[float] = None
    candidates_above: Optional[int] = None


class LabelSuggestionModelStats(BaseModel):
    job_id: str
    backend: str
    drafts: int
    pending: int
    adopted: int
    rejected: int
    reverted: int
    edited_after: int
    edited_unknown: int
    reviewed_candidates: int
    adopted_candidates: int
    rejected_candidates: int
    mean_confidence_adopted: Optional[float] = None
    mean_confidence_rejected: Optional[float] = None
    advice: LabelSuggestionThresholdAdvice


class LabelSuggestionStats(BaseModel):
    models: List[LabelSuggestionModelStats]
    generated_at: str


# The slider's grid in step 2 (0.05 … 0.95).
_THRESHOLD_GRID = [round(0.05 * step, 2) for step in range(1, 20)]


def threshold_advice(task: str, reviewed: List[tuple], backend: str = "trained_model") -> Dict[str, Any]:
    """A confidence threshold a person can trust, from candidates a person already judged.

    ``reviewed`` holds (confidence, adopted) per candidate of accepted or rejected drafts. Honest with small samples:
    nothing is suggested under 20 reviewed candidates, and a cut needs at least 10 candidates above it with 90% or
    more adopted. Classification and anomaly drafts carry one candidate whose score is a class probability or an
    anomaly score, so no cut is suggested for them. The cut is for the trained model's slider in step 2; other draft
    sources (template match, grounding, VLM, foundation) score on their own scales, so none is suggested for them."""
    count = len(reviewed)
    if task not in ("detection", "segmentation") or backend != "trained_model":
        return {"reason": "task_not_applicable", "reviewed_candidates": count}
    if count < 20:
        return {"reason": "too_few", "reviewed_candidates": count}
    adopted_rate = sum(1 for _, adopted in reviewed if adopted) / count
    if adopted_rate >= 0.9:
        return {"reason": "already_high", "reviewed_candidates": count, "adopted_rate": round(adopted_rate, 3)}
    for cut in _THRESHOLD_GRID:
        above = [adopted for confidence, adopted in reviewed if confidence >= cut]
        if len(above) >= 10 and sum(above) / len(above) >= 0.9:
            return {"reason": "suggestion", "reviewed_candidates": count, "adopted_rate": round(adopted_rate, 3),
                    "threshold": cut, "adopted_rate_above": round(sum(above) / len(above), 3), "candidates_above": len(above)}
    return {"reason": "none_reaches", "reviewed_candidates": count, "adopted_rate": round(adopted_rate, 3)}


def _candidate_confidence(value: Any) -> Optional[float]:
    """A candidate's confidence as a finite number, or None when the draft file holds anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _mean_confidence(values: List[float]) -> Optional[float]:
    mean = sum(values) / len(values) if values else None
    return round(mean, 4) if mean is not None and math.isfinite(mean) else None


@router.get("/stats", response_model=LabelSuggestionStats)
def suggestion_stats(request: Request, job_id: Optional[str] = None):
    """Per model (or draft source): how its drafts fared with people. Read-only: it writes no label, ledger or
    proposal byte (nor the suggestion folder), so a person can look at it as often as they like.

    A summary, not a gate: a draft file it cannot read is left out on its own, and an adoption whose image or labels
    can no longer be read (moved out of the source, say) counts as 'edited_unknown'."""
    project = get_current_project(request)
    source = _source_path(project)
    groups: Dict[tuple, Dict[str, Any]] = {}
    labels_cache: Dict[str, Optional[List[Any]]] = {}

    def current_labels(image: Path) -> Optional[List[Any]]:
        key = str(image)
        if key not in labels_cache:
            try:
                saved = routes_annotation._get_annotations_impl(image.stem, None, key)
            except HTTPException:
                saved = None
            rows = saved.get("annotations") if isinstance(saved, dict) else None
            labels_cache[key] = rows if isinstance(rows, list) else None
        return labels_cache[key]

    folder = Path(project["project_dir"]) / "label_suggestions"
    # _proposal_root refuses a symlinked folder; a missing one means no drafts yet and is not created by a read.
    paths = sorted(_proposal_root(project).glob("suggestion_*.json")) if folder.is_dir() or folder.is_symlink() else []
    for path in paths:
        if not _SUGGESTION_ID.fullmatch(path.stem):
            continue
        try:
            item = _read_proposal(project, path.stem)
        except HTTPException:
            continue
        image_path = item.get("image_path")
        if item.get("task") != project["task"] or not isinstance(image_path, str) or not Path(image_path).is_relative_to(source):
            continue
        if job_id and item.get("job_id") != job_id:
            continue
        # Work out this draft's share before any counter moves, so a malformed file leaves out only itself.
        backend, draft_job = item.get("backend") or "trained_model", item.get("job_id") or ""
        candidates, chosen_ids = item.get("candidates", []), item.get("accepted_candidate_ids") or []
        if not all(isinstance(value, str) for value in (backend, draft_job)) or not all(
                isinstance(value, list) for value in (candidates, chosen_ids)):
            continue
        status = item.get("status")
        chosen = {value for value in chosen_ids if isinstance(value, str)}
        judged = []  # (adopted, confidence or None) per candidate a person judged
        if status in ("accepted", "rejected"):
            for row in candidates:
                if isinstance(row, dict):
                    candidate_id = row.get("id")
                    adopted = status == "accepted" and isinstance(candidate_id, str) and candidate_id in chosen
                    judged.append((adopted, _candidate_confidence(row.get("confidence"))))
        edited = None
        if status == "accepted":
            adopted_rows = item.get("adopted_annotations")
            current = None  # stays None when the adoption record or the image's labels cannot be read
            if isinstance(adopted_rows, list) and adopted_rows and all(
                    isinstance(row, dict) and isinstance(row.get("id"), str) for row in adopted_rows):
                try:
                    current = current_labels(_image_path(project, image_path))
                except HTTPException:
                    pass  # the image left the source (moved or removed): whether its shapes were edited is unknown
            if current is None:
                edited = "edited_unknown"
            elif changed_adopted_ids(adopted_rows, current):
                edited = "edited_after"
        group = groups.setdefault((backend, draft_job), {
            "job_id": draft_job, "backend": backend, "drafts": 0, "pending": 0, "adopted": 0, "rejected": 0,
            "reverted": 0, "edited_after": 0, "edited_unknown": 0, "reviewed": [], "adopted_candidates": 0, "rejected_candidates": 0})
        group["drafts"] += 1
        if status in ("pending", "rejected", "reverted"):
            group[status] += 1
        elif status == "accepted":
            group["adopted"] += 1
        for adopted, confidence in judged:
            group["adopted_candidates" if adopted else "rejected_candidates"] += 1
            if confidence is not None:
                group["reviewed"].append((confidence, adopted))
        if edited:
            group[edited] += 1
    models = []
    for group in groups.values():
        reviewed = group.pop("reviewed")
        models.append({**group, "reviewed_candidates": len(reviewed),
                       "mean_confidence_adopted": _mean_confidence([confidence for confidence, adopted in reviewed if adopted]),
                       "mean_confidence_rejected": _mean_confidence([confidence for confidence, adopted in reviewed if not adopted]),
                       "advice": threshold_advice(project["task"], reviewed, group["backend"])})
    models.sort(key=lambda row: (-row["drafts"], row["job_id"]))
    return {"models": models, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


@router.get("/{suggestion_id}")
def get_suggestion(suggestion_id: str, request: Request):
    return _read_proposal(get_current_project(request), suggestion_id)


def adopted_candidates(proposal: Dict[str, Any], selected_ids: set, reviewer: str,
                       backup_version_id: Optional[str]) -> List[Dict[str, Any]]:
    """The selected candidates as label shapes that remember which draft, model and review they came from."""
    adopted_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    shapes = []
    for candidate in proposal.get("candidates", []):
        if candidate["id"] not in selected_ids:
            continue
        shape = dict(candidate["annotation"])
        shape["origin"] = {
            "kind": "model_draft", "proposal_id": proposal["id"], "candidate_id": candidate["id"],
            "backend": proposal.get("backend") or "trained_model", "job_id": proposal.get("job_id"),
            "checkpoint_sha256": proposal.get("checkpoint_sha256"), "reviewer": reviewer,
            "adopted_at": adopted_at, "backup_version_id": backup_version_id,
        }
        shapes.append(shape)
    return shapes


def saved_adoption(image: Path, candidate_ids: set) -> List[Dict[str, Any]]:
    """The adopted shapes exactly as the label file now holds them; undo compares against these."""
    saved = routes_annotation.get_annotations(image.stem, file_path=str(image)).get("annotations", [])
    return [item for item in saved if isinstance(item, dict) and item.get("id") in candidate_ids]


def _restore_bytes(path: Path, previous: Optional[bytes]) -> None:
    if previous is None:
        path.unlink(missing_ok=True)
    else:
        temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.rollback"
        temporary.write_bytes(previous)
        os.replace(temporary, path)


@router.post("/{suggestion_id}/review")
def review_suggestion(suggestion_id: str, req: ReviewRequest, request: Request):
    from backend.api.shared_authorization import request_actor
    req=req.model_copy(update={'actor':request_actor(request,req.actor)})
    project = get_current_project(request)
    with _REVIEW_LOCK, _VERSION_LOCK, metadata_engine.metadata_transaction(Path(project["project_dir"]), _source_path(project), Path(project["annotations_dir"])):
        proposal = _read_proposal(project, suggestion_id)
        if proposal["status"] != "pending":
            raise HTTPException(status_code=409, detail="Suggestion has already been reviewed.")
        if req.decision == "reject":
            proposal.update(status="rejected", reviewer=req.actor, reviewed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            _write_json(_proposal_path(project, suggestion_id), proposal)
            return proposal
        if proposal.get("backend") in {"template_match", "grounding_dino", "foundation", "vlm"}:
            from backend.api.routes_label_candidates import review_external_proposal
            return review_external_proposal(project, proposal, req)
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

        metadata = metadata_engine.metadata_for_path(Path(project["project_dir"]), _source_path(project), image, Path(project["annotations_dir"]))
        if _changed_since_draft(metadata, proposal):
            raise HTTPException(status_code=409, detail="Another reviewer changed this image; generate candidates again.")
        current = routes_annotation.get_annotations(image.stem, file_path=str(image))
        existing = current.get("annotations", [])
        if not isinstance(existing, list):
            raise HTTPException(status_code=422, detail="Existing annotation file is invalid.")
        accepted = [item["annotation"] for item in candidates if item["id"] in selected_ids]
        # Validate the merged labels before taking the backup; the saved request below adds each shape's origin.
        routes_annotation.AnnotationSaveRequest(
            image_id=image.stem, image_path=str(image), annotations=[*existing, *accepted],
            image_width=proposal["image_width"], image_height=proposal["image_height"], actor=req.actor, expected_revision=metadata["revision"],
            lease_token=req.lease_token,
        )
        backup = _snapshot(project, _source_path(project),
                           f"모델 라벨 채택 전 · {image.name}",
                           f"Before accepting {suggestion_id}", "auto_backup")
        annotation_request = routes_annotation.AnnotationSaveRequest(
            image_id=image.stem, image_path=str(image),
            annotations=[*existing, *adopted_candidates(proposal, selected_ids, req.actor, backup["id"])],
            image_width=proposal["image_width"], image_height=proposal["image_height"], actor=req.actor, expected_revision=metadata["revision"],
            lease_token=req.lease_token,
        )
        previous_json = json_path.read_bytes() if json_path.is_file() else None
        previous_mask = mask_path.read_bytes() if mask_path.is_file() else None
        previous_proposal = _proposal_path(project, suggestion_id).read_bytes()
        batch_path = _batch_path(project, batch["id"]) if batch is not None else None
        previous_batch = batch_path.read_bytes() if batch_path is not None else None
        try:
            routes_annotation.save_annotations(annotation_request,request)
            if batch is not None:
                # A verified acceptance is the only dataset change that may
                # advance this batch's review baseline. External label/split
                # edits between reviews still produce a fingerprint mismatch.
                batch["review_fingerprint"] = _dataset_fingerprint(project)
                _persist_batch(project, batch)
            proposal.update(
                status="accepted", reviewer=req.actor, accepted_candidate_ids=sorted(selected_ids),
                backup_version_id=backup["id"], adopted_annotations=saved_adoption(image, selected_ids),
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


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    actor: str = Field("operator", min_length=1, max_length=100)
    lease_token: Optional[str] = Field(None, min_length=1, max_length=200)


@router.post("/{suggestion_id}/undo")
def undo_adoption(suggestion_id: str, req: UndoRequest, request: Request):
    """Take back one adoption: remove exactly the shapes it added, only while nobody has changed them since.

    The label file is backed up first. When an adopted shape was edited or removed, nothing is written and the
    reply names the data version taken before the adoption, so the person can restore it knowingly."""
    from backend.api.shared_authorization import request_actor
    actor = request_actor(request, req.actor)
    project = get_current_project(request)
    source = _source_path(project)
    with _REVIEW_LOCK, _VERSION_LOCK, metadata_engine.metadata_transaction(Path(project["project_dir"]), source, Path(project["annotations_dir"])):
        proposal = _read_proposal(project, suggestion_id)
        if proposal["status"] != "accepted":
            raise HTTPException(status_code=409, detail="Only an adopted draft can be taken back.")
        adopted = proposal.get("adopted_annotations")
        if not adopted:
            raise HTTPException(status_code=409, detail={
                "message": "이 채택은 도형 단위 되돌리기 기록이 없습니다. 채택 전에 남긴 데이터 버전으로 복원하세요.",
                "backup_version_id": proposal.get("backup_version_id")})
        external = proposal.get("backend") in {"template_match", "grounding_dino", "foundation", "vlm"}
        from backend.api.routes_training import training_job_manager
        if training_job_manager.get_active_job() is not None:
            raise HTTPException(status_code=409, detail="Finish or stop active training before changing labels.")
        if external:
            from backend.engine import labeling_tasks
            if labeling_tasks.active(project):
                raise HTTPException(status_code=409, detail="Finish or cancel the active labeling task before changing labels.")
            batch = labeling_tasks.read(project, proposal["batch_id"]) if proposal.get("batch_id") else None
        else:
            with _BATCH_LOCK:
                if _project_batch_key(project) in _LIVE_BATCHES:
                    raise HTTPException(status_code=409, detail="Finish or cancel the active label batch before changing labels.")
            batch = _read_batch(project, proposal["batch_id"]) if proposal.get("batch_id") else None
        image = _image_path(project, proposal["image_path"])
        current = routes_annotation.get_annotations(image.stem, file_path=str(image)).get("annotations", [])
        if not isinstance(current, list):
            raise HTTPException(status_code=422, detail="Existing annotation file is invalid.")
        changed = changed_adopted_ids(adopted, current)
        if changed:
            raise HTTPException(status_code=409, detail={
                "message": "채택한 도형이 그 뒤 바뀌어 이 채택만 되돌릴 수 없습니다. 채택 전에 남긴 데이터 버전으로 복원하세요.",
                "backup_version_id": proposal.get("backup_version_id"), "changed_candidate_ids": changed})
        adopted_ids = {item["id"] for item in adopted}
        remaining = [item for item in current if not (isinstance(item, dict) and item.get("id") in adopted_ids)]
        metadata = metadata_engine.metadata_for_path(Path(project["project_dir"]), source, image, Path(project["annotations_dir"]))
        before = _dataset_fingerprint(project)
        # The same label folder each review path writes (and restores on failure).
        studio = (dataset_annotation_dir(image.parent, Path(project["annotations_dir"]), use_scope=False) if external
                  else dataset_annotation_dir(image.parent, routes_dataset.STUDIO_ANNOTATIONS_DIR))
        json_path, mask_path = studio / f"{image.stem}.json", studio / "masks" / f"{image.stem}.png"
        save = routes_annotation.AnnotationSaveRequest(
            image_id=image.stem, image_path=str(image), annotations=remaining, image_width=proposal["image_width"],
            image_height=proposal["image_height"], actor=actor, expected_revision=metadata["revision"], lease_token=req.lease_token)
        backup = _snapshot(project, source, f"채택 되돌리기 전 · {image.name}", f"Before taking back {suggestion_id}", "auto_backup")
        proposal_path = _proposal_path(project, suggestion_id)
        previous = {path: (path.read_bytes() if path.is_file() else None) for path in (json_path, mask_path, proposal_path)}
        if batch is not None:
            if external:
                from backend.engine import labeling_tasks
                batch_path = labeling_tasks._root(project) / f"{batch['id']}.json"
            else:
                batch_path = _batch_path(project, batch["id"])
            previous[batch_path] = batch_path.read_bytes() if batch_path.is_file() else None
        try:
            routes_annotation.save_annotations(save, request)
            # The take-back is a reviewed change, like an adoption: the batch's remaining drafts stay reviewable
            # only when nothing else changed the dataset since the last reviewed change.
            if batch is not None and batch.get("review_fingerprint") == before:
                batch["review_fingerprint"] = _dataset_fingerprint(project)
                if external:
                    from backend.engine import labeling_tasks
                    labeling_tasks.write(project, batch)
                else:
                    _persist_batch(project, batch)
            proposal.update(status="reverted", reverted_by=actor, revert_backup_version_id=backup["id"],
                            reverted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            _write_json(proposal_path, proposal)
        except Exception:
            for path, data in previous.items():
                _restore_bytes(path, data)
            raise
        return proposal

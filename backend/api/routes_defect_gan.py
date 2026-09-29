"""Project-scoped saved GAN candidates; generation never edits training data."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal
import base64
import json
import re
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_project import get_current_project
from backend.engine.defect_gan import (
    generate_defect_candidates, load_defect_gan_manifest,
    train_defect_gan, write_defect_gan_manifest,
)


router = APIRouter(prefix="/api/defect-gan", tags=["defect-gan"])
_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


class DefectCropRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1)
    bbox: list[int] = Field(min_length=4, max_length=4)
    split: Literal["train", "val", "test"]


class GANManifestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    samples: list[DefectCropRow] = Field(min_length=1)


class GANTrainRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_path: str = Field(min_length=1)
    epochs: int = Field(default=20, ge=1, le=500)
    batch_size: int = Field(default=8, ge=2, le=128)
    seed: int = 0
    base_channels: int = Field(default=16, ge=8, le=128)
    device: Literal["cpu", "cuda", "mps"] = "cpu"


class GANGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    count: int = Field(default=8, ge=1, le=20)
    seed: int = 0
    device: Literal["cpu", "cuda", "mps"] = "cpu"


def _models_root(request: Request) -> Path:
    project = get_current_project(request)
    project_dir = Path(project["project_dir"]).resolve()
    models_dir = Path(project["models_dir"])
    if models_dir.is_symlink() or models_dir.resolve() != project_dir / "models":
        raise HTTPException(status_code=422, detail="Project model storage is invalid")
    root = models_dir / "defect_gan"
    if root.is_symlink():
        raise HTTPException(status_code=422, detail="Defect GAN model storage is invalid")
    return root


def _checkpoint(request: Request, job_id: str) -> Path:
    if not _JOB_ID.fullmatch(job_id):
        raise HTTPException(status_code=422, detail="Invalid GAN job ID")
    job_dir = _models_root(request) / job_id
    path = job_dir / "best_model.pt"
    if job_dir.is_symlink() or path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="GAN checkpoint not found in active project")
    return path


@router.post("/manifest")
def create_manifest(req: GANManifestRequest, request: Request):
    get_current_project(request)
    try:
        path = write_defect_gan_manifest(req.dataset_path, [row.model_dump() for row in req.samples])
        manifest = load_defect_gan_manifest(req.dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"manifest_path": str(path), "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
            "sample_count": len(manifest["samples"]),
            "samples": manifest["samples"],
            "split_counts": {split: sum(row["split"] == split for row in manifest["samples"])
                             for split in ("train", "val", "test")}}


@router.get("/manifest")
def inspect_manifest(dataset_path: str, request: Request):
    get_current_project(request)
    try:
        path = Path(dataset_path).expanduser().resolve() / "defect_gan.json"
        manifest = load_defect_gan_manifest(dataset_path)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"manifest_path": str(path), "manifest_sha256": sha256(path.read_bytes()).hexdigest(),
            "sample_count": len(manifest["samples"]), "samples": manifest["samples"],
            "split_counts": {split: sum(row["split"] == split for row in manifest["samples"])
                             for split in ("train", "val", "test")}}


@router.post("/train")
def train(req: GANTrainRequest, request: Request):
    output = _models_root(request) / uuid.uuid4().hex
    try:
        result = train_defect_gan(
            req.dataset_path, output, epochs=req.epochs, batch_size=req.batch_size,
            seed=req.seed, base_channels=req.base_channels, device=req.device,
        )
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"job_id": output.name, "result": result}


@router.get("/models")
def list_models(request: Request):
    root = _models_root(request)
    if not root.is_dir():
        return {"models": []}
    models = []
    for directory in sorted(root.iterdir()):
        if directory.is_symlink() or not directory.is_dir() or not _JOB_ID.fullmatch(directory.name):
            continue
        checkpoint, metadata = directory / "best_model.pt", directory / "model_meta.json"
        if not checkpoint.is_file() or not metadata.is_file() or checkpoint.is_symlink() or metadata.is_symlink():
            continue
        try:
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            digest = sha256(checkpoint.read_bytes()).hexdigest()
        except (OSError, ValueError):
            continue
        if meta.get("model_kind") != "dcgan_defect_crop" or meta.get("checkpoint_sha256") != digest:
            continue
        models.append({"job_id": directory.name, "checkpoint_sha256": digest,
                       "sample_count": meta.get("sample_count"), "epochs": meta.get("epochs"),
                       "quality_status": "unvalidated"})
    return {"models": models}


@router.post("/generate")
def generate(req: GANGenerateRequest, request: Request):
    project = get_current_project(request)
    checkpoint = _checkpoint(request, req.job_id)
    review_root = Path(project["project_dir"]).resolve() / "synthetic_review"
    if review_root.is_symlink() or (review_root / req.job_id).is_symlink():
        raise HTTPException(status_code=422, detail="Synthetic review storage is invalid")
    output = review_root / req.job_id / uuid.uuid4().hex
    try:
        result = generate_defect_candidates(checkpoint, output, count=req.count,
                                            seed=req.seed, device=req.device)
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for item in result["candidates"]:
        item["preview_data_url"] = "data:image/png;base64," + base64.b64encode(Path(item["path"]).read_bytes()).decode("ascii")
    return {"job_id": req.job_id, "review_dir": str(output), **result}

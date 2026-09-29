"""Project-local, reproducible comparison of two completed inspection models.

This is decision evidence. It never changes the active model or flow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from backend.api import routes_dataset
from backend.api.routes_project import get_current_project
from backend.engine.checkpoint_paths import completed_job_receipt, is_job_id, trusted_checkpoint
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.flowchart_engine import (
    FlowchartEngine,
    get_single_detection_flowchart,
    get_single_segmentation_flowchart,
)
from backend.engine.industrial_adapters import read_image_safely_rgb
from backend.engine.zero_escape_analyzer import is_defect_label


router = APIRouter(prefix="/model-comparisons", tags=["evaluation"])
Task = Literal["classification", "detection", "segmentation", "anomaly"]
_REPORT_ID = re.compile(r"comparison_[0-9a-f]{32}\Z")


class ComparisonRequest(BaseModel):
    source_dataset_path: str
    task: Task
    incumbent_job_id: str
    candidate_job_id: str
    max_images: int = Field(default=4, ge=1, le=16)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _scope(request: Request, source_dataset_path: str, task: Task) -> tuple[dict[str, Any], Path]:
    project = dict(get_current_project(request))
    source = Path(source_dataset_path).expanduser().resolve()
    registered = project.get("source_dataset_dir")
    if project.get("task") != task or not registered or source != Path(registered).expanduser().resolve():
        raise HTTPException(status_code=409, detail="선택한 작업 유형과 데이터 출처가 현재 프로젝트와 일치하지 않습니다.")
    if not source.is_dir():
        raise HTTPException(status_code=422, detail="현재 프로젝트의 원본 데이터 폴더를 찾을 수 없습니다.")
    return project, source


def _models_dir(project: dict[str, Any]) -> Path:
    project_dir = Path(project["project_dir"])
    root = Path(project["models_dir"])
    if root.is_symlink() or root.resolve() != (project_dir / "models").resolve():
        raise HTTPException(status_code=422, detail="프로젝트 모델 저장소가 유효하지 않습니다.")
    return root


def _report_dir(project: dict[str, Any]) -> Path:
    project_dir = Path(project["project_dir"])
    reports = Path(project["reports_dir"])
    if reports.is_symlink() or reports.resolve() != (project_dir / "reports").resolve():
        raise HTTPException(status_code=422, detail="프로젝트 보고서 저장소가 유효하지 않습니다.")
    target = reports / "model_comparisons"
    if target.is_symlink():
        raise HTTPException(status_code=422, detail="모델 비교 보고서 저장소가 유효하지 않습니다.")
    return target


def _model(project: dict[str, Any], source: Path, task: Task, job_id: str) -> dict[str, Any] | None:
    if not is_job_id(job_id):
        return None
    job_dir = _models_dir(project) / job_id
    if job_dir.is_symlink() or not job_dir.is_dir():
        return None
    checkpoint = trusted_checkpoint(job_id, str(job_dir))
    if checkpoint is None or checkpoint.parent.resolve() != job_dir.resolve():
        return None
    receipt = completed_job_receipt(job_dir)
    if not receipt or receipt.get("task") != task:
        return None
    recorded_source = receipt.get("source_dataset_path")
    if not recorded_source or Path(recorded_source).expanduser().resolve() != source:
        return None
    training_fingerprint = receipt.get("dataset_fingerprint")
    if not isinstance(training_fingerprint, str) or not training_fingerprint.startswith("v1:"):
        return None
    metadata = job_dir / "model_meta.json"
    if metadata.is_symlink() or not metadata.is_file():
        return None
    try:
        meta = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict) or meta.get("task") != task:
        return None
    return {
        "job_id": job_id,
        "task": task,
        "checkpoint_path": str(checkpoint),
        "training_dataset_fingerprint": training_fingerprint,
        "created_at": meta.get("created_at") or receipt.get("completed_at") or None,
        "preset": meta.get("preset"),
    }


def _fingerprint(source: Path) -> str:
    return fingerprint_dataset(
        source,
        studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )


def _ground_truth_verdict(label: Any) -> str | None:
    if label is None or not str(label).strip():
        return None
    if str(label).strip().casefold() in {"정상", "양품", "합격"}:
        return "OK"
    return "NG" if is_defect_label(label) else "OK"


def _test_images(source: Path, task: Task, maximum: int) -> tuple[list[dict[str, Any]], int]:
    page = routes_dataset.list_dataset_images(
        folder_path=str(source), task=task, limit=maximum, offset=0, split="test", class_name=None,
    )
    total = int(page["total"])
    if total == 0:
        raise HTTPException(status_code=422, detail="비교할 test 이미지가 없습니다. 1단계에서 test 분할을 저장해 주세요.")
    images = sorted(page["items"], key=lambda item: item["file_path"])
    selected: list[dict[str, Any]] = []
    for item in images:
        path = Path(item["file_path"])
        # A source may intentionally contain linked files. The path used by the
        # dataset inventory must still be lexically beneath the selected source.
        if not Path(os.path.abspath(path)).is_relative_to(source) or not path.is_file():
            raise HTTPException(status_code=409, detail="test 이미지 경로가 원본 데이터 폴더를 벗어났습니다.")
        try:
            # The flow engine substitutes a synthetic example if decoding
            # fails. Validate with its own reader so comparison never does so.
            read_image_safely_rgb(path, max_dim=32)
            digest = _sha256(path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"test 이미지를 읽을 수 없습니다: {path.name}") from exc
        label = item.get("label")
        selected.append({
            "image_id": item["image_id"],
            "file_name": item["file_name"],
            "file_path": str(path),
            "image_sha256": digest,
            "ground_truth_label": label,
            "ground_truth_verdict": _ground_truth_verdict(label),
        })
    return selected, total


def _pipeline(task: Task, job_id: str):
    if task == "detection":
        return get_single_detection_flowchart(job_id=job_id)
    pipeline = get_single_segmentation_flowchart(job_id=job_id)
    if task != "segmentation":
        next(node for node in pipeline.nodes if node.data.node_type == "inspection").data.task = task
    return pipeline


def _outcome(result: dict[str, Any]) -> dict[str, Any]:
    verdict = result.get("final_verdict")
    if verdict not in ("OK", "NG", "REVIEW"):
        raise ValueError("모델 실행 결과에 유효한 판정이 없습니다.")
    crops = result.get("crops") or []
    return {
        "verdict": verdict,
        "defective_roi_count": result.get("defective_roi_count", 0),
        "max_defect_score": max((float(crop.get("defect_score") or 0) for crop in crops), default=None),
        "reason": result.get("rejection_reason") or "",
        "error": None,
    }


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    compared = [row for row in rows if row["incumbent"]["verdict"] and row["candidate"]["verdict"]]
    disagreements = [row for row in compared if row["incumbent"]["verdict"] != row["candidate"]["verdict"]]
    return {
        "selected_images": len(rows),
        "comparable_images": len(compared),
        "error_images": len(rows) - len(compared),
        "disagreements": len(disagreements),
        "incumbent_verdicts": {v: sum(row["incumbent"]["verdict"] == v for row in rows) for v in ("OK", "NG", "REVIEW")},
        "candidate_verdicts": {v: sum(row["candidate"]["verdict"] == v for row in rows) for v in ("OK", "NG", "REVIEW")},
        "ng_to_ok": sum(row["incumbent"]["verdict"] == "NG" and row["candidate"]["verdict"] == "OK" for row in compared),
        "ok_to_ng": sum(row["incumbent"]["verdict"] == "OK" and row["candidate"]["verdict"] == "NG" for row in compared),
        "new_missed_ng": sum(
            row["ground_truth_verdict"] == "NG" and row["incumbent"]["verdict"] == "NG"
            and row["candidate"]["verdict"] == "OK" for row in compared
        ),
        "new_overkill_ok": sum(
            row["ground_truth_verdict"] == "OK" and row["incumbent"]["verdict"] == "OK"
            and row["candidate"]["verdict"] == "NG" for row in compared
        ),
        "known_ok_images": sum(row["ground_truth_verdict"] == "OK" for row in rows),
        "known_ng_images": sum(row["ground_truth_verdict"] == "NG" for row in rows),
        "unknown_truth_images": sum(row["ground_truth_verdict"] is None for row in rows),
    }


def _summary_record(report: dict[str, Any]) -> dict[str, Any]:
    return {key: report[key] for key in (
        "comparison_id", "created_at", "project_id", "source_dataset_path", "task",
        "incumbent_job_id", "candidate_job_id", "status", "summary",
        "dataset_fingerprint", "selected_image_count", "total_test_images",
    )}


@router.get("/models")
def comparison_models(request: Request, source_dataset_path: str, task: Task):
    project, source = _scope(request, source_dataset_path, task)
    root = _models_dir(project)
    models = []
    if root.is_dir():
        for job_dir in sorted(root.iterdir()):
            if job_dir.is_dir():
                model = _model(project, source, task, job_dir.name)
                if model:
                    models.append({key: value for key, value in model.items() if key != "checkpoint_path"})
    return {"models": models, "total": len(models)}


@router.post("")
def create_comparison(payload: ComparisonRequest, request: Request):
    project, source = _scope(request, payload.source_dataset_path, payload.task)
    if payload.incumbent_job_id == payload.candidate_job_id:
        raise HTTPException(status_code=422, detail="비교 기준과 후보 모델은 서로 달라야 합니다.")
    baseline = _model(project, source, payload.task, payload.incumbent_job_id)
    candidate = _model(project, source, payload.task, payload.candidate_job_id)
    if baseline is None or candidate is None:
        raise HTTPException(status_code=409, detail="두 모델 모두 현재 프로젝트·출처·작업 유형의 완료 checkpoint여야 합니다.")

    dataset_fingerprint = _fingerprint(source)
    images, total_test_images = _test_images(source, payload.task, payload.max_images)
    model_hashes = {
        "incumbent": _sha256(Path(baseline["checkpoint_path"])),
        "candidate": _sha256(Path(candidate["checkpoint_path"])),
    }
    paths = {baseline["job_id"]: Path(baseline["checkpoint_path"]), candidate["job_id"]: Path(candidate["checkpoint_path"])}
    engines = {
        job_id: FlowchartEngine(device="cpu", checkpoint_resolver=lambda requested, _task, paths=paths: paths.get(requested))
        for job_id in paths
    }
    pipelines = {job_id: _pipeline(payload.task, job_id) for job_id in paths}
    rows: list[dict[str, Any]] = []
    for image in images:
        row = dict(image)
        for key, model in (("incumbent", baseline), ("candidate", candidate)):
            try:
                result = engines[model["job_id"]].execute(
                    pipeline=pipelines[model["job_id"]], image_path=image["file_path"], image_id=image["image_id"],
                )
                row[key] = _outcome(result)
            except Exception as exc:
                row[key] = {"verdict": None, "defective_roi_count": None, "max_defect_score": None,
                            "reason": "", "error": str(exc)[:500]}
        row["disagrees"] = bool(
            row["incumbent"]["verdict"] and row["candidate"]["verdict"]
            and row["incumbent"]["verdict"] != row["candidate"]["verdict"]
        )
        rows.append(row)

    # Reject a mixed-version comparison; the saved report must describe one
    # exact dataset, image list, and pair of checkpoint contents.
    if _fingerprint(source) != dataset_fingerprint:
        raise HTTPException(status_code=409, detail="비교 중 데이터가 바뀌었습니다. 다시 실행해 주세요.")
    if any(_sha256(Path(model["checkpoint_path"])) != model_hashes[key]
           for key, model in (("incumbent", baseline), ("candidate", candidate))):
        raise HTTPException(status_code=409, detail="비교 중 모델 checkpoint가 바뀌었습니다. 다시 실행해 주세요.")
    if any(_sha256(Path(image["file_path"])) != image["image_sha256"] for image in images):
        raise HTTPException(status_code=409, detail="비교 중 test 이미지가 바뀌었습니다. 다시 실행해 주세요.")

    summary = _summary(rows)
    report = {
        "schema_version": 1,
        "comparison_id": f"comparison_{uuid.uuid4().hex}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": project["id"],
        "source_dataset_path": str(source),
        "task": payload.task,
        "incumbent_job_id": baseline["job_id"],
        "candidate_job_id": candidate["job_id"],
        "incumbent_training_dataset_fingerprint": baseline["training_dataset_fingerprint"],
        "candidate_training_dataset_fingerprint": candidate["training_dataset_fingerprint"],
        "dataset_fingerprint": dataset_fingerprint,
        "model_sha256": model_hashes,
        "image_selection": "first N test images in dataset gallery order; exact paths and SHA-256 saved below",
        "selected_image_count": len(images),
        "total_test_images": total_test_images,
        "status": "completed" if summary["error_images"] == 0 else "completed_with_errors",
        "summary": summary,
        "limitations": [
            "선택한 test 이미지에서 두 모델의 원판정을 비교한 결과입니다. 전체 데이터 성능을 뜻하지 않습니다.",
            "실행 순서와 CPU 환경이 같아도 지연 시간이나 FPS 비교 근거로 사용하지 않습니다.",
            "OK 정답 이미지가 없으면 과검률을 판단할 수 없습니다.",
            "모델 활성화·교체·롤백은 수행하지 않았습니다.",
        ],
        "images": rows,
    }
    output = _report_dir(project)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{report['comparison_id']}.json"
    temporary = output / f".{report['comparison_id']}.tmp"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return report


@router.get("")
def list_comparisons(request: Request, source_dataset_path: str, task: Task):
    project, source = _scope(request, source_dataset_path, task)
    output = _report_dir(project)
    reports = []
    if output.is_dir():
        for path in output.glob("comparison_*.json"):
            if path.is_symlink() or not _REPORT_ID.fullmatch(path.stem):
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                if (report.get("project_id") == project["id"] and report.get("source_dataset_path") == str(source)
                        and report.get("task") == task):
                    reports.append(_summary_record(report))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    reports.sort(key=lambda report: report["created_at"], reverse=True)
    return {"comparisons": reports, "total": len(reports)}


@router.get("/{comparison_id}")
def get_comparison(comparison_id: str, request: Request, source_dataset_path: str, task: Task):
    project, source = _scope(request, source_dataset_path, task)
    if not _REPORT_ID.fullmatch(comparison_id):
        raise HTTPException(status_code=404, detail="모델 비교 기록을 찾을 수 없습니다.")
    path = _report_dir(project) / f"{comparison_id}.json"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="모델 비교 기록을 찾을 수 없습니다.")
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="모델 비교 기록을 읽을 수 없습니다.") from exc
    if (report.get("project_id") != project["id"] or report.get("source_dataset_path") != str(source)
            or report.get("task") != task):
        raise HTTPException(status_code=404, detail="현재 프로젝트의 모델 비교 기록이 아닙니다.")
    return report

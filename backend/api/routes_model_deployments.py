"""Project-local, auditable model approvals; flow and field runtime are separate."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from backend.engine.sqlite_wal import use_wal  # a concurrent WAL switch is retried, not failed
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from typing_extensions import Literal

from backend.api.routes_model_comparisons import (
    Task, _fingerprint, _model, _report_dir, _scope, _sha256,
)


router = APIRouter(prefix="/api/model-deployments", tags=["model-deployments"])
_REPORT_ID = re.compile(r"comparison_[0-9a-f]{32}\Z")
_REVISION_ID = re.compile(r"[0-9a-f]{32}\Z")
_MIN_EACH_CLASS = 8
DeploymentTask = Literal["classification", "patch_classification", "detection", "segmentation", "anomaly", "ocr", "rotated_detection", "enhancement", "rotation"]


def _approval_scope(request, source_dataset_path, task):
    if task in ("ocr", "rotated_detection", "enhancement", "rotation"):
        from backend.api.routes_evaluation_history import history_scope
        return history_scope(request, source_dataset_path, task)
    return _scope(request, source_dataset_path, task)


class ApprovalRequest(BaseModel):
    source_dataset_path: str
    task: Task
    comparison_id: str
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=8, max_length=2000)
    holdout_reviewed: Literal[True]

    @field_validator("reviewer", "reason")
    @classmethod
    def nonblank_evidence(cls, value: str, info) -> str:
        cleaned = value.strip()
        if len(cleaned) < (8 if info.field_name == "reason" else 1):
            raise ValueError("Reviewer and reason must contain meaningful text.")
        return cleaned


class RollbackRequest(BaseModel):
    source_dataset_path: str
    task: DeploymentTask
    target_revision_id: str
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=8, max_length=2000)

    @field_validator("reviewer", "reason")
    @classmethod
    def nonblank_evidence(cls, value: str, info) -> str:
        return ApprovalRequest.nonblank_evidence(value, info)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _deployment_db(project: dict[str, Any]) -> Path:
    project_dir = Path(project["project_dir"])
    if project_dir.is_symlink() or not project_dir.is_dir():
        raise HTTPException(status_code=422, detail="Project deployment storage is invalid.")
    path = project_dir / "model_deployments.sqlite3"
    if path.is_symlink():
        raise HTTPException(status_code=422, detail="Project deployment storage is invalid.")
    return path


@contextmanager
def _store(project: dict[str, Any]):
    conn = sqlite3.connect(_deployment_db(project), timeout=10)
    conn.row_factory = sqlite3.Row
    use_wal(conn)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS revisions (
            revision_id TEXT PRIMARY KEY,
            source_dataset_path TEXT NOT NULL,
            task TEXT NOT NULL,
            job_id TEXT NOT NULL,
            checkpoint_sha256 TEXT NOT NULL,
            training_dataset_fingerprint TEXT NOT NULL,
            evaluation_dataset_fingerprint TEXT NOT NULL,
            comparison_id TEXT NOT NULL,
            comparison_sha256 TEXT NOT NULL,
            parent_revision_id TEXT,
            restored_from_revision_id TEXT,
            action TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS active_revisions (
            source_dataset_path TEXT NOT NULL,
            task TEXT NOT NULL,
            revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
            PRIMARY KEY (source_dataset_path, task)
        );
        CREATE INDEX IF NOT EXISTS deployment_history_idx
            ON revisions(source_dataset_path, task, created_at);
    """)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _active(conn: sqlite3.Connection, source: Path, task: Task) -> dict[str, Any] | None:
    row = conn.execute("""
        SELECT revisions.* FROM active_revisions JOIN revisions
        ON revisions.revision_id = active_revisions.revision_id
        WHERE active_revisions.source_dataset_path = ? AND active_revisions.task = ?
    """, (str(source), task)).fetchone()
    return dict(row) if row else None


def _read_report(project: dict[str, Any], source: Path, task: Task, comparison_id: str) -> tuple[dict[str, Any], str]:
    if not _REPORT_ID.fullmatch(comparison_id):
        raise HTTPException(status_code=422, detail="Invalid comparison ID.")
    path = _report_dir(project) / f"{comparison_id}.json"
    if path.is_symlink() or not path.is_file():
        raise HTTPException(status_code=404, detail="Comparison report not found.")
    try:
        raw = path.read_bytes()
        report = json.loads(raw)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Comparison report is unreadable.") from exc
    if (not isinstance(report, dict) or report.get("comparison_id") != comparison_id
            or report.get("project_id") != project["id"]
            or report.get("source_dataset_path") != str(source) or report.get("task") != task):
        raise HTTPException(status_code=409, detail="Comparison report belongs to a different project or source.")
    return report, hashlib.sha256(raw).hexdigest()


def _assess(project: dict[str, Any], source: Path, task: Task, comparison_id: str,
            active: dict[str, Any] | None) -> dict[str, Any]:
    report, report_sha = _read_report(project, source, task, comparison_id)
    reasons: list[str] = []
    try:
        from backend.engine.comparison_truth import verify_evidence_binding
        verify_evidence_binding(project, source, report)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        reasons.append(f"Comparison truth binding is stale; re-evaluate: {exc}")
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    images = report.get("images") if isinstance(report.get("images"), list) else []
    selected = report.get("selected_image_count")
    if report.get("status") != "completed" or summary.get("error_images") != 0:
        reasons.append("비교 실행에 실패한 이미지가 있습니다. 동일 test 데이터로 다시 평가하세요.")
    if not isinstance(selected, int) or selected != len(images) or selected < 2 * _MIN_EACH_CLASS:
        reasons.append(f"test 표본은 OK/NG 각 {_MIN_EACH_CLASS}장 이상 필요합니다.")
    if (summary.get("known_ok_images", 0) < _MIN_EACH_CLASS
            or summary.get("known_ng_images", 0) < _MIN_EACH_CLASS):
        reasons.append(f"OK·NG 정답 이미지를 각각 {_MIN_EACH_CLASS}장 이상 준비하세요.")
    if summary.get("unknown_truth_images") != 0:
        reasons.append("정답이 확인되지 않은 test 이미지가 있습니다.")
    if summary.get("comparable_images") != selected or summary.get("selected_images") != selected:
        reasons.append("선택한 모든 test 이미지에서 두 모델의 판정이 필요합니다.")
    if summary.get("new_missed_ng") != 0 or summary.get("new_overkill_ok") != 0:
        reasons.append("후보 모델의 신규 미검·과검이 있습니다. 오류를 검토하고 재평가하세요.")
    incumbent_id = report.get("incumbent_job_id")
    candidate_id = report.get("candidate_job_id")
    if not isinstance(incumbent_id, str) or not isinstance(candidate_id, str) or incumbent_id == candidate_id:
        reasons.append("비교 기준과 후보 모델 식별자가 유효하지 않습니다.")
    if active and incumbent_id != active["job_id"]:
        reasons.append("비교 기준 모델이 현재 승인 모델과 다릅니다. 현재 모델을 기준으로 다시 비교하세요.")
    if report.get("dataset_fingerprint") != _fingerprint(source):
        reasons.append("비교 뒤 데이터·라벨·분할이 바뀌었습니다. 다시 평가하세요.")

    verified_models: dict[str, dict[str, Any]] = {}
    hashes = report.get("model_sha256") if isinstance(report.get("model_sha256"), dict) else {}
    for label, job_id in (("incumbent", incumbent_id), ("candidate", candidate_id)):
        model = _model(project, source, task, job_id) if isinstance(job_id, str) else None
        if model is None or hashes.get(label) != _sha256(Path(model["checkpoint_path"])):
            reasons.append(f"{label} 체크포인트가 비교 당시의 완료 모델과 다릅니다.")
        else:
            verified_models[label] = model

    candidate_model = verified_models.get("candidate")
    if candidate_model is not None:
        lineage = candidate_model.get("warm_start")
        receipt_lineage = candidate_model.get("receipt_warm_start")
        if lineage is not None or receipt_lineage is not None:
            if not isinstance(lineage, dict) or lineage != receipt_lineage:
                reasons.append("재학습 후보의 모델·영수증 계보가 일치하지 않습니다.")
            else:
                try:
                    import torch

                    checkpoint_lineage = torch.load(
                        Path(candidate_model["checkpoint_path"]), map_location="cpu", weights_only=True,
                    )
                    if (not isinstance(checkpoint_lineage, dict)
                            or checkpoint_lineage.get("task") != task
                            or checkpoint_lineage.get("warm_start") != lineage):
                        reasons.append("재학습 후보의 체크포인트 계보가 메타데이터와 다릅니다.")
                except Exception:
                    reasons.append("재학습 후보 체크포인트의 계보를 검증할 수 없습니다.")
                parent_id = lineage.get("parent_job_id")
                parent = _model(project, source, task, parent_id) if isinstance(parent_id, str) else None
                if (parent is None
                        or _sha256(Path(parent["checkpoint_path"])) != lineage.get("parent_checkpoint_sha256")
                        or parent.get("training_dataset_fingerprint") != lineage.get("parent_dataset_fingerprint")):
                    reasons.append("재학습 시작 모델의 출처·체크포인트가 변경되었습니다.")
                if not active and incumbent_id != parent_id:
                    reasons.append("첫 승인 전 재학습 후보는 시작 모델을 기준으로 test 비교해야 합니다.")

    known_ok = known_ng = unknown = missed = overkill = errors = 0
    seen: set[str] = set()
    for row in images:
        if not isinstance(row, dict):
            errors += 1
            continue
        raw_path = row.get("file_path")
        try:
            path = Path(raw_path)
            if (not isinstance(raw_path, str) or path.is_symlink() or not path.is_file()
                    or not path.resolve().is_relative_to(source) or raw_path in seen
                    or row.get("image_sha256") != _sha256(path)):
                errors += 1
                continue
            seen.add(raw_path)
        except (OSError, TypeError, ValueError):
            errors += 1
            continue
        truth = row.get("ground_truth_verdict")
        baseline = row.get("incumbent", {}).get("verdict") if isinstance(row.get("incumbent"), dict) else None
        candidate = row.get("candidate", {}).get("verdict") if isinstance(row.get("candidate"), dict) else None
        known_ok += truth == "OK"
        known_ng += truth == "NG"
        unknown += truth not in ("OK", "NG")
        errors += baseline not in ("OK", "NG", "REVIEW") or candidate not in ("OK", "NG", "REVIEW")
        missed += truth == "NG" and baseline == "NG" and candidate == "OK"
        overkill += truth == "OK" and baseline == "OK" and candidate == "NG"
    if errors or len(seen) != len(images):
        reasons.append("비교에 사용한 이미지·원판정·SHA-256 기록을 다시 확인하세요.")
    if (known_ok != summary.get("known_ok_images") or known_ng != summary.get("known_ng_images")
            or unknown != summary.get("unknown_truth_images")
            or missed != summary.get("new_missed_ng") or overkill != summary.get("new_overkill_ok")):
        reasons.append("비교 보고서의 요약과 이미지별 근거가 일치하지 않습니다.")
    if active:
        current_model = _model(project, source, task, active["job_id"])
        if current_model is None or _sha256(Path(current_model["checkpoint_path"])) != active["checkpoint_sha256"]:
            reasons.append("현재 승인 모델 체크포인트가 변경되었습니다.")

    return {
        "status": "ready" if not reasons else "needs_review",
        "reasons": list(dict.fromkeys(reasons)),
        "comparison_id": comparison_id,
        "comparison_sha256": report_sha,
        "candidate_job_id": candidate_id,
        "candidate_checkpoint_sha256": hashes.get("candidate"),
        "dataset_fingerprint": report.get("dataset_fingerprint"),
        "known_ok_images": known_ok,
        "known_ng_images": known_ng,
        "minimum_each_class": _MIN_EACH_CLASS,
        "current_revision_id": active["revision_id"] if active else None,
        "training_dataset_fingerprint": verified_models.get("candidate", {}).get("training_dataset_fingerprint"),
    }


@router.get("/assess/{comparison_id}")
def assess_comparison(comparison_id: str, source_dataset_path: str, task: Task, request: Request):
    project, source = _scope(request, source_dataset_path, task)
    with _store(project) as conn:
        active = _active(conn, source, task)
    return _assess(project, source, task, comparison_id, active)


@router.get("/active")
def get_active_approval(source_dataset_path: str, task: DeploymentTask, request: Request):
    project, source = _approval_scope(request, source_dataset_path, task)
    with _store(project) as conn:
        active = _active(conn, source, task)
    if active:
        active["valid"] = verified_approval_revision(project, active["revision_id"], expected_task=task) is not None
    return {"active": active, "field_runtime_applied": False}


@router.get("/history")
def get_approval_history(source_dataset_path: str, task: DeploymentTask, request: Request):
    project, source = _approval_scope(request, source_dataset_path, task)
    with _store(project) as conn:
        active = _active(conn, source, task)
        rows = conn.execute(
            "SELECT * FROM revisions WHERE source_dataset_path = ? AND task = ? ORDER BY rowid DESC LIMIT 100",
            (str(source), task),
        ).fetchall()
    return {"revisions": [{**dict(row), "is_active": bool(active and row["revision_id"] == active["revision_id"])} for row in rows]}


@contextmanager
def _approval_authority(project):
    from backend.engine.release_eligibility import release_authority
    try:
        with release_authority(project):
            yield
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/approve")
def approve_candidate(payload: ApprovalRequest, request: Request):
    project, source = _scope(request, payload.source_dataset_path, payload.task)
    with _approval_authority(project), _store(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = _active(conn, source, payload.task)
        assessment = _assess(project, source, payload.task, payload.comparison_id, active)
        if assessment["status"] != "ready":
            raise HTTPException(status_code=409, detail={"status": "needs_review", "reasons": assessment["reasons"]})
        revision = {
            "revision_id": uuid.uuid4().hex,
            "source_dataset_path": str(source), "task": payload.task,
            "job_id": assessment["candidate_job_id"],
            "checkpoint_sha256": assessment["candidate_checkpoint_sha256"],
            "training_dataset_fingerprint": assessment["training_dataset_fingerprint"],
            "evaluation_dataset_fingerprint": assessment["dataset_fingerprint"],
            "comparison_id": payload.comparison_id,
            "comparison_sha256": assessment["comparison_sha256"],
            "parent_revision_id": active["revision_id"] if active else None,
            "restored_from_revision_id": None,
            "action": "approve", "reviewer": payload.reviewer.strip(),
            "reason": payload.reason.strip(), "created_at": _now(),
        }
        current = _assess(project, source, payload.task, payload.comparison_id, active)
        if current["status"] != "ready" or current["comparison_sha256"] != assessment["comparison_sha256"]:
            raise HTTPException(status_code=409, detail={"status": "needs_review", "reasons": current["reasons"] or ["Comparison evidence changed during approval"]})
        conn.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(revision.values()))
        conn.execute("""INSERT INTO active_revisions VALUES (?, ?, ?)
            ON CONFLICT(source_dataset_path, task) DO UPDATE SET revision_id = excluded.revision_id""",
            (str(source), payload.task, revision["revision_id"]),
        )
    return {"revision": revision, "field_runtime_applied": False}


@router.post("/rollback")
def rollback_approval(payload: RollbackRequest, request: Request):
    project, source = _approval_scope(request, payload.source_dataset_path, payload.task)
    if not _REVISION_ID.fullmatch(payload.target_revision_id):
        raise HTTPException(status_code=422, detail="Invalid deployment revision ID.")
    with _approval_authority(project), _store(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = _active(conn, source, payload.task)
        target = conn.execute(
            "SELECT * FROM revisions WHERE revision_id = ? AND source_dataset_path = ? AND task = ?",
            (payload.target_revision_id, str(source), payload.task),
        ).fetchone()
        if active is None or target is None:
            raise HTTPException(status_code=404, detail="Approved model revision not found.")
        target = dict(target)
        if active["revision_id"] == target["revision_id"]:
            raise HTTPException(status_code=409, detail="Selected revision is already active.")
        if verified_approval_revision(project, target["revision_id"], expected_task=payload.task) is None:
            raise HTTPException(status_code=409, detail="Previous approval evidence is stale; re-evaluate before rollback.")
        model = _model(project, source, payload.task, target["job_id"])
        if (model is None or _sha256(Path(model["checkpoint_path"])) != target["checkpoint_sha256"]
                or _fingerprint(source) != target["evaluation_dataset_fingerprint"]):
            raise HTTPException(status_code=409, detail="Previous approval's data or checkpoint changed; re-evaluate before rollback.")
        revision = {
            "revision_id": uuid.uuid4().hex,
            "source_dataset_path": str(source), "task": payload.task,
            "job_id": target["job_id"], "checkpoint_sha256": target["checkpoint_sha256"],
            "training_dataset_fingerprint": target["training_dataset_fingerprint"],
            "evaluation_dataset_fingerprint": target["evaluation_dataset_fingerprint"],
            "comparison_id": target["comparison_id"], "comparison_sha256": target["comparison_sha256"],
            "parent_revision_id": active["revision_id"],
            "restored_from_revision_id": target["revision_id"],
            "action": "rollback", "reviewer": payload.reviewer.strip(),
            "reason": payload.reason.strip(), "created_at": _now(),
        }
        conn.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(revision.values()))
        conn.execute("UPDATE active_revisions SET revision_id = ? WHERE source_dataset_path = ? AND task = ?",
                     (revision["revision_id"], str(source), payload.task))
    return {"revision": revision, "field_runtime_applied": False}


def verified_approval_revision(
    project: dict[str, Any], revision_id: str, *, expected_task: Task | None = None,
) -> dict[str, Any] | None:
    """Resolve approved weights by revision for a mixed-task package release."""
    if not isinstance(revision_id, str) or not _REVISION_ID.fullmatch(revision_id):
        return None
    with _store(project) as conn:
        row = conn.execute("SELECT * FROM revisions WHERE revision_id = ?", (revision_id,)).fetchone()
    if (row is None or row["source_dataset_path"] != project.get("source_dataset_dir")
            or (expected_task is not None and row["task"] != expected_task)):
        return None
    source = Path(row["source_dataset_path"])
    model = _model(project, source, row["task"], row["job_id"])
    if model is None or _sha256(Path(model["checkpoint_path"])) != row["checkpoint_sha256"]:
        return None
    if row["comparison_id"].startswith("evaluation_"):
        report_path = Path(project["reports_dir"]) / "evaluations" / f"{row['comparison_id']}.json"
    else:
        report_path = _report_dir(project) / f"{row['comparison_id']}.json"
    if report_path.is_symlink() or not report_path.is_file() or _sha256(report_path) != row["comparison_sha256"]:
        return None
    if row["comparison_id"].startswith("evaluation_"):
        try:
            from backend.engine.evaluation_history import EvaluationHistory,verify_specialized_evaluation_inputs
            evidence=EvaluationHistory(report_path.parent).get(row['comparison_id'])
            verify_specialized_evaluation_inputs(project,source,row['task'],evidence['binding'])
        except (ValueError,OSError,KeyError,TypeError):return None
    else:
        try:
            from backend.engine.comparison_truth import verify_evidence_binding
            from backend.engine.release_eligibility import evidence_context
            with evidence_context(project):
                if _fingerprint(source) != row["evaluation_dataset_fingerprint"]:
                    return None
                verify_evidence_binding(project, source, json.loads(report_path.read_text(encoding='utf-8')))
        except (ValueError, OSError, KeyError, TypeError):
            return None
    return dict(row)


def verified_release_revision(
    project: dict[str, Any], revision_id: str, *, source: Path, task: str,
    job_id: str, checkpoint: Path,
) -> dict[str, str] | None:
    """Bind the active approval to the exact model selected by a saved flow."""
    revision = verified_approval_revision(project, revision_id, expected_task=task)
    if (revision is None or revision["source_dataset_path"] != str(source)
            or revision["job_id"] != job_id
            or revision["checkpoint_sha256"] != _sha256(checkpoint)):
        return None
    with _store(project) as conn:
        active = _active(conn, source, task)
    if active is None or active["revision_id"] != revision_id:
        return None
    if _fingerprint(source) != revision["evaluation_dataset_fingerprint"]:
        return None
    return {
        "revision_id": revision_id, "job_id": job_id, "task": task,
        "checkpoint_sha256": revision["checkpoint_sha256"],
    }


class SpecializedApprovalRequest(BaseModel):
    source_dataset_path: str
    task: Literal["ocr", "rotated_detection", "enhancement", "rotation"]
    evaluation_id: str
    incumbent_evaluation_id: str | None = None
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str = Field(min_length=8, max_length=2000)
    holdout_reviewed: Literal[True]
    minimum_sample_count: int = Field(default=8, ge=1, le=1000000)
    minimum_metrics: dict[str, float] = Field(default_factory=dict)
    maximum_metrics: dict[str, float] = Field(default_factory=dict)


@router.post("/specialized-approve")
def approve_specialized(payload: SpecializedApprovalRequest, request: Request):
    from backend.api.routes_evaluation_history import history_scope, history_store
    import math
    project, source = history_scope(request, payload.source_dataset_path, payload.task)
    if not payload.reviewer.strip() or len(payload.reason.strip()) < 8: raise HTTPException(422, "Reviewer and evidence reason are required")
    allowed = {"ocr": {"exact_match_accuracy", "character_error_rate"},
               "rotated_detection": {"precision", "recall", "mean_oriented_iou", "mean_angle_error_deg"},
               "enhancement": {"output_mse", "output_psnr"},
               "rotation": {"angular_mae_deg", "within_10_deg", "loss"}}[payload.task]
    if not payload.minimum_metrics and not payload.maximum_metrics: raise HTTPException(422, "Explicit family quality bounds are required")
    if any(key not in allowed or not math.isfinite(value) for key, value in {**payload.minimum_metrics, **payload.maximum_metrics}.items()): raise HTTPException(422, "Invalid family quality metric bounds")
    try: record = history_store(project).get(payload.evaluation_id)
    except (OSError, ValueError) as exc: raise HTTPException(409, str(exc)) from exc
    result, binding = record["result"], record["binding"]
    if result.get("task") != payload.task or binding.get("source_dataset_path") != str(source) or binding.get("dataset_fingerprint") != _fingerprint(source): raise HTTPException(409, "Evaluation is not bound to the active source and family")
    try:
        from backend.engine.evaluation_history import verify_specialized_evaluation_inputs
        verify_specialized_evaluation_inputs(project,source,payload.task,binding)
    except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
    if result.get("split") != "test" or type(result.get("sample_count")) is not int or result["sample_count"] < payload.minimum_sample_count:
        raise HTTPException(409, "Approval requires an independently reviewed test holdout meeting the requested sample count")
    if payload.task == "ocr" and (len(result.get("samples", [])) != result["sample_count"] or any(not isinstance(row.get("reference_text"), str) for row in result["samples"])):
        raise HTTPException(409, "OCR approval needs actual held-out text labels for every sample")
    if payload.task == "enhancement" and result.get("improved") is not True: raise HTTPException(409, "Enhancement candidate did not improve held-out reconstruction against the original input")
    model = _model(project, source, payload.task, result.get("job_id"))
    if model is None or _sha256(Path(model["checkpoint_path"])) != binding.get("checkpoint_sha256"):
        raise HTTPException(409, "Evaluation model is missing, changed, or belongs to another project")
    for key, minimum in payload.minimum_metrics.items():
        if not isinstance(result.get(key), (int, float)) or not math.isfinite(result[key]) or result[key] < minimum: raise HTTPException(409, f"Held-out {key} is below the requested quality bound")
    for key, maximum in payload.maximum_metrics.items():
        if not isinstance(result.get(key), (int, float)) or not math.isfinite(result[key]) or result[key] > maximum: raise HTTPException(409, f"Held-out {key} exceeds the requested quality bound")
    with _approval_authority(project), _store(project) as conn:
        conn.execute("BEGIN IMMEDIATE")
        active = _active(conn, source, payload.task)
        if active and active["job_id"] != result["job_id"]:
            try: incumbent = history_store(project).get(payload.incumbent_evaluation_id)
            except (ValueError, OSError, TypeError) as exc: raise HTTPException(409, "A current held-out incumbent evaluation is required") from exc
            prior = incumbent["result"]
            prior_binding=incumbent["binding"]
            family_identity=binding.get("family_dataset_sha256")
            if (prior.get("job_id") != active["job_id"] or prior.get("task") != payload.task
                    or prior_binding.get("source_dataset_path")!=str(source)
                    or prior_binding.get("dataset_fingerprint") != binding["dataset_fingerprint"]
                    or prior_binding.get("checkpoint_sha256")!=active["checkpoint_sha256"]
                    or not isinstance(family_identity,str) or not family_identity
                    or prior_binding.get("family_dataset_sha256")!=family_identity
                    or prior.get("sample_count")!=result["sample_count"] or prior.get("split") != "test"):
                raise HTTPException(409, "Incumbent and candidate must use the same exact test dataset")
            if any(result[key] < prior.get(key, float("inf")) for key in payload.minimum_metrics) or any(result[key] > prior.get(key, -float("inf")) for key in payload.maximum_metrics):
                raise HTTPException(409, "Candidate regresses against the active incumbent on the reviewed quality metrics")
        path = Path(project["reports_dir"]) / "evaluations" / (payload.evaluation_id + ".json")
        revision = {"revision_id": uuid.uuid4().hex, "source_dataset_path": str(source), "task": payload.task,
                    "job_id": result["job_id"], "checkpoint_sha256": binding["checkpoint_sha256"],
                    "training_dataset_fingerprint": model["training_dataset_fingerprint"] or "unverified",
                    "evaluation_dataset_fingerprint": binding["dataset_fingerprint"], "comparison_id": payload.evaluation_id,
                    "comparison_sha256": _sha256(path), "parent_revision_id": active["revision_id"] if active else None,
                    "restored_from_revision_id": None, "action": "approve_specialized", "reviewer": payload.reviewer.strip(),
                    "reason": payload.reason.strip() + " | quality_bounds=" + json.dumps({"minimum": payload.minimum_metrics, "maximum": payload.maximum_metrics, "minimum_sample_count": payload.minimum_sample_count}, sort_keys=True), "created_at": _now()}
        conn.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(revision.values()))
        conn.execute("INSERT INTO active_revisions VALUES (?, ?, ?) ON CONFLICT(source_dataset_path, task) DO UPDATE SET revision_id=excluded.revision_id", (str(source), payload.task, revision["revision_id"]))
    return {"revision": revision, "field_runtime_applied": False}

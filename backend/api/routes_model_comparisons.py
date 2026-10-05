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
from pydantic import BaseModel, Field, model_validator
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
from backend.engine.patch_classification import load_patch_manifest


router = APIRouter(prefix="/model-comparisons", tags=["evaluation"])
Task = Literal["classification", "detection", "segmentation", "anomaly", "patch_classification"]
_REPORT_ID = re.compile(r"comparison_[0-9a-f]{32}\Z")


class ComparisonRequest(BaseModel):
    source_dataset_path: str
    task: Task
    incumbent_job_id: str
    candidate_job_id: str
    max_images: int = Field(default=4, ge=1, le=500)
    full_test: bool = False
    incumbent_task:Literal['classification','detection','segmentation','anomaly','patch_classification','rotated_detection','ocr']|None=None
    candidate_task:Literal['classification','detection','segmentation','anomaly','patch_classification','rotated_detection','ocr']|None=None
    incumbent_params:dict[str,Any]=Field(default_factory=dict)
    candidate_params:dict[str,Any]=Field(default_factory=dict)
    execution_target: Literal['local_cpu','selected_compute'] = 'local_cpu'
    compute_profile_id: str | None = None
    device: str = Field(default='cpu',pattern=r'^(cpu|mps|cuda(?::[0-9]+)?)$')

    @model_validator(mode='after')
    def explicit_target(self):
        if self.execution_target=='local_cpu' and (self.device!='cpu' or self.compute_profile_id is not None):
            raise ValueError('Local comparison requires CPU and no remote profile')
        if self.execution_target=='selected_compute' and not self.compute_profile_id:
            raise ValueError('Choose an explicit comparison compute profile')
        return self


def _comparison_execution(payload):
    if payload.execution_target=='local_cpu':
        return {'execution_target':'local_cpu','device':'cpu','compute_profile':None}
    from backend.remote.profiles import get_profile_store
    profile=get_profile_store().get(payload.compute_profile_id)
    if profile is None:raise HTTPException(404,'Selected comparison compute profile is unavailable')
    return {'execution_target':'selected_compute','device':payload.device,'compute_profile':profile.model_dump()}


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
    if (task != "patch_classification" and project.get("task") != task) or not registered or source != Path(registered).expanduser().resolve():
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
    if task in ("ocr", "rotated_detection", "enhancement", "rotation"):
        from backend.engine.specialized_models import resolve_specialized_checkpoint
        try:
            checkpoint, metadata = resolve_specialized_checkpoint(_models_dir(project), job_id, task, str(source))
            binding = metadata.get("training_provenance") or {}
            return {"job_id": job_id, "task": task, "checkpoint_path": str(checkpoint),
                    "training_dataset_fingerprint": binding.get("dataset_fingerprint") or metadata.get("dataset_sha256") or metadata.get("dataset_provenance", {}).get("dataset_sha256"),
                    "created_at": checkpoint.stat().st_mtime, "preset": "specialized",
                    "warm_start": None, "receipt_warm_start": None}
        except Exception:
            return None
    if not is_job_id(job_id):
        return None
    job_dir = _models_dir(project) / job_id
    if job_dir.is_symlink() or not job_dir.is_dir():
        return None
    checkpoint = trusted_checkpoint(job_id, str(job_dir), project_models_dir=_models_dir(project))
    if checkpoint is None or checkpoint.parent.resolve() != job_dir.resolve():
        return None
    receipt = completed_job_receipt(job_dir)
    if not receipt or receipt.get("task") != task:
        return None
    recorded_source = receipt.get("source_dataset_path")
    if not recorded_source:
        return None
    intake_lineage=None
    if Path(recorded_source).expanduser().resolve()!=source:
        try:
            from backend.engine.intake_lineage import verify_ancestor_model
            intake_lineage=verify_ancestor_model(project,source,checkpoint,task)
        except (ValueError,OSError,KeyError,TypeError):return None
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
    score_spec = None
    if task == 'anomaly':
        from backend.engine.score_contract import checkpoint_score_spec
        try: score_spec = checkpoint_score_spec(checkpoint)
        except (ValueError, OSError, KeyError, TypeError, RuntimeError): return None
    return {
        "job_id": job_id,
        "task": task,
        "score_spec": score_spec,
        "checkpoint_path": str(checkpoint),
        "training_dataset_fingerprint": training_fingerprint,
        "created_at": meta.get("created_at") or receipt.get("completed_at") or None,
        "preset": meta.get("preset"),
        "warm_start": meta.get("warm_start"),
        "receipt_warm_start": receipt.get("warm_start"),
        "family_dataset_path": meta.get("dataset_path") or receipt.get("dataset_path"),
        "intake_lineage":intake_lineage,
    }


def _fingerprint(source: Path) -> str:
    return fingerprint_dataset(
        source,
        studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )


def _ground_truth_verdict(label: Any) -> str | None:
    from backend.engine.evaluation_history import binary_verdict
    return binary_verdict(label)


def _test_images(source: Path, task: Task, maximum: int | None) -> tuple[list[dict[str, Any]], int]:
    if task == "patch_classification":
        return _patch_test_images(source, maximum or 2**31)
    page = routes_dataset.list_dataset_images(
        folder_path=str(source), task=task, limit=min(maximum or 500, 500), offset=0, split="test", class_name=None,
    )
    total = int(page["total"])
    if total == 0:
        raise HTTPException(status_code=422, detail="비교할 test 이미지가 없습니다. 1단계에서 test 분할을 저장해 주세요.")
    images = list(page["items"])
    if maximum is None:
        while len(images) < total:
            more = routes_dataset.list_dataset_images(folder_path=str(source), task=task, limit=500, offset=len(images), split="test", class_name=None)
            if not more["items"]: raise HTTPException(409, "Test inventory changed during selection")
            images.extend(more["items"])
    images = sorted(images, key=lambda item: item["file_path"])
    selected: list[dict[str, Any]] = []
    for item in images:
        path = Path(item["file_path"])
        # A source may intentionally contain linked files. The path used by the
        # dataset inventory must still be lexically beneath the selected source.
        if not Path(os.path.abspath(path)).is_relative_to(source) or not path.is_file():
            raise HTTPException(status_code=409, detail="test 이미지 경로가 원본 데이터 폴더를 벗어났습니다.")
        from backend.engine.annotation_storage import request_shared_scope
        if request_shared_scope() and (path.is_symlink() or not path.resolve().is_relative_to(source)):
            raise HTTPException(409,'Shared comparison image escaped its authorized source')
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


def _patch_test_images(source: Path, maximum: int) -> tuple[list[dict[str, Any]], int]:
    """Compare source images only when image-level test truth was supplied."""
    try:
        manifest = load_patch_manifest(source)
        raw = json.loads((source / "patches.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid patch holdout: {exc}") from exc
    test_sources = {record.image: record for record in manifest.patches if record.split == "test"}
    if not test_sources:
        raise HTTPException(status_code=422, detail="Patch comparison needs test source images in patches.json")
    truth = raw.get("test_image_verdicts") if isinstance(raw, dict) else None
    if (not isinstance(truth, dict) or set(truth) != set(test_sources)
            or any(value not in ("OK", "NG") for value in truth.values())):
        raise HTTPException(status_code=422, detail=(
            "patches.json needs test_image_verdicts with an independently reviewed OK or NG "
            "image-level truth for every test source image"
        ))
    ordered = sorted(test_sources)
    if len(ordered) > maximum:
        quota = maximum // 2
        selected_paths = ([path for path in ordered if truth[path] == "OK"][:quota]
                          + [path for path in ordered if truth[path] == "NG"][:quota])
        selected_set = set(selected_paths)
        selected_paths += [path for path in ordered if path not in selected_set][:maximum - len(selected_paths)]
        ordered = sorted(selected_paths)
    selected = []
    for relative in ordered[:maximum]:
        record = test_sources[relative]
        path = record.image_path
        try:
            read_image_safely_rgb(path, max_dim=32)
            digest = _sha256(path)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Patch test image is unreadable: {relative}") from exc
        if digest != record.source_sha256:
            raise HTTPException(status_code=409, detail=f"Patch test image changed after manifest validation: {relative}")
        selected.append({
            "image_id": path.stem,
            "file_name": path.name,
            "file_path": str(path),
            "image_sha256": digest,
            "ground_truth_label": truth[relative],
            "ground_truth_verdict": truth[relative],
        })
    return selected, len(test_sources)


def _owned_patch_test_images(project, source, models, maximum):
    """Use one full, agreeing prepared holdout without editing original inputs."""
    if (source / 'patches.json').is_file():
        return _patch_test_images(source, maximum)
    cohorts = []
    for model in models:
        if model['task'] != 'patch_classification':
            continue
        requested = model.get('family_dataset_path')
        if not requested:
            raise HTTPException(409, 'Patch model has no prepared dataset identity')
        dataset = Path(requested)
        allowed = (Path(project['dataset_dir']).resolve(), Path(project['models_dir']).resolve())
        if dataset.is_symlink() or not any(dataset.resolve().is_relative_to(root) for root in allowed):
            raise HTTPException(409, 'Patch comparison inputs belong to another project')
        try:
            manifest = load_patch_manifest(dataset)
            raw = json.loads((dataset / 'patches.json').read_text(encoding='utf-8'))
            if Path(manifest.provenance.get('source_dataset_path', '')).resolve() != source:
                raise ValueError('Patch model source differs')
            copied, total = _patch_test_images(dataset, 2**31)
            mapping = manifest.provenance.get('source_map') or {}
            cohort = []
            for row in copied:
                relative = Path(row['file_path']).relative_to(dataset.resolve()).as_posix()
                original = source / mapping[relative]['source_relative_path']
                cohort.append({**row, 'file_path': str(original), 'image_id': original.stem,
                               'file_name': original.name})
            cohorts.append(sorted(cohort, key=lambda row: row['file_path']))
        except (OSError, ValueError, KeyError) as exc:
            raise HTTPException(409, f'Prepared patch holdout is invalid: {exc}') from exc
    if not cohorts:
        raise HTTPException(422, 'Choose a patch model with a reviewed prepared test holdout')
    identity = lambda rows: [(r['file_path'], r['image_sha256'], r['ground_truth_verdict']) for r in rows]
    if any(identity(rows) != identity(cohorts[0]) for rows in cohorts[1:]):
        raise HTTPException(409, 'Patch models have different test images or reviewed image truth')
    rows = cohorts[0]
    if len(rows) > maximum:
        quota = maximum // 2
        chosen = [r for r in rows if r['ground_truth_verdict'] == 'OK'][:quota]
        chosen += [r for r in rows if r['ground_truth_verdict'] == 'NG'][:quota]
        paths = {r['file_path'] for r in chosen}
        chosen += [r for r in rows if r['file_path'] not in paths][:maximum-len(chosen)]
        return sorted(chosen, key=lambda row: row['file_path']), len(rows)
    return rows, len(rows)


def _pipeline(task: Task, job_id: str):
    if task in ("detection","rotated_detection"):
        pipeline=get_single_detection_flowchart(job_id=job_id)
        if task=='rotated_detection':next(node for node in pipeline.nodes if node.data.node_type=='detection_crop').data.task=task
        return pipeline
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
        "execution": {key:result.get(key) for key in (
            'execution_target','execution_device','compute_profile_id','compute_gpu_selector',
            'device_name','remote_operation_id','remote_result_sha256')},
    }


def _binary_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Same known-truth binary intersection for both models; NG is positive."""
    selected = []
    excluded = {'unknown_truth': 0, 'review': 0, 'error': 0}
    for row in rows:
        if row.get('ground_truth_verdict') not in {'OK', 'NG'}:
            excluded['unknown_truth'] += 1
        elif any(row[role].get('error') or row[role].get('verdict') not in {'OK', 'NG', 'REVIEW'}
                 for role in ('incumbent', 'candidate')):
            excluded['error'] += 1
        elif any(row[role]['verdict'] == 'REVIEW' for role in ('incumbent', 'candidate')):
            excluded['review'] += 1
        else:
            selected.append(row)
    metrics = {'scope': 'shared_known_truth_binary_verdicts', 'positive_verdict': 'NG',
               'selected_images': len(rows), 'evaluated_images': len(selected), 'excluded': excluded}
    ratio = lambda numerator, denominator: numerator / denominator if denominator else None
    for role in ('incumbent', 'candidate'):
        counts = {key: 0 for key in ('tp', 'tn', 'fp', 'fn')}
        for row in selected:
            truth, verdict = row['ground_truth_verdict'], row[role]['verdict']
            counts[{('NG', 'NG'): 'tp', ('OK', 'OK'): 'tn', ('OK', 'NG'): 'fp', ('NG', 'OK'): 'fn'}[truth, verdict]] += 1
        tp, tn, fp, fn = (counts[key] for key in ('tp', 'tn', 'fp', 'fn'))
        metrics[role] = {'counts': counts, 'accuracy': ratio(tp + tn, len(selected)),
                         'precision_ng': ratio(tp, tp + fp), 'recall_ng': ratio(tp, tp + fn),
                         'miss_rate': ratio(fn, tp + fn), 'overkill_rate': ratio(fp, tn + fp)}
    return metrics


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
        'binary_metrics': _binary_metrics(rows),
    }


def _summary_record(report: dict[str, Any]) -> dict[str, Any]:
    return {**{key: report[key] for key in (
        "comparison_id", "created_at", "project_id", "source_dataset_path", "task",
        "incumbent_job_id", "candidate_job_id", "status", "summary",
        "dataset_fingerprint", "selected_image_count", "total_test_images",
    )},'execution':report.get('execution')}


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


@router.get('/models/all')
def all_comparison_models(request:Request,source_dataset_path:str):
    project=get_current_project(request);source=Path(source_dataset_path).expanduser().resolve()
    if not project.get('source_dataset_dir') or source!=Path(project['source_dataset_dir']).resolve():raise HTTPException(409,'Comparison source differs from active project')
    models=[];root=_models_dir(project)
    for task in ('classification','detection','segmentation','anomaly','patch_classification','rotated_detection','ocr'):
        directories=(root/task).iterdir() if task in ('rotated_detection','ocr') and (root/task).is_dir() else root.iterdir()
        for directory in directories:
            if directory.is_dir():
                model=_model(project,source,task,directory.name)
                if model:models.append({key:value for key,value in model.items() if key!='checkpoint_path'})
    return {'models':models,'total':len(models),'verdict_semantics':'same held-out image OK/NG/REVIEW; task-specific localization/text metrics remain separate'}


@router.post("")
def create_comparison(payload: ComparisonRequest, request: Request):
    project, source = _scope(request, payload.source_dataset_path, payload.task)
    return _run_comparison(payload, project, source)


def _comparison_inputs(payload: ComparisonRequest, project, source):
    execution=_comparison_execution(payload)
    if payload.incumbent_job_id == payload.candidate_job_id:
        raise HTTPException(status_code=422, detail="비교 기준과 후보 모델은 서로 달라야 합니다.")
    baseline_task=payload.incumbent_task or payload.task;candidate_task=payload.candidate_task or payload.task
    baseline = _model(project, source, baseline_task, payload.incumbent_job_id)
    candidate = _model(project, source, candidate_task, payload.candidate_job_id)
    if baseline is None or candidate is None:
        raise HTTPException(status_code=409, detail="두 모델 모두 현재 프로젝트·출처·작업 유형의 완료 checkpoint여야 합니다.")

    model_hashes = {
        'incumbent': _sha256(Path(baseline['checkpoint_path'])),
        'candidate': _sha256(Path(candidate['checkpoint_path'])),
    }
    dataset_fingerprint = _fingerprint(source)
    intake_lineage=baseline.get('intake_lineage') or candidate.get('intake_lineage')
    if intake_lineage:
        if baseline_task!=candidate_task:raise HTTPException(409,'Intake ancestor comparison requires the same task and ordered classes')
        from backend.engine.intake_lineage import verify_current_model
        for model in (baseline,candidate):
            if not model.get('intake_lineage'):
                try:verify_current_model(project,source,model['checkpoint_path'],model['task'],intake_lineage)
                except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
        other=baseline.get('intake_lineage') and candidate.get('intake_lineage')
        if other and other['cohort_sha256']!=intake_lineage['cohort_sha256']:raise HTTPException(409,'Ancestor models require the identical frozen held-out cohort')
        images=list(intake_lineage['images']);total_test_images=len(images)
        if not payload.full_test:images=images[:payload.max_images]
    else:
        images, total_test_images = (_owned_patch_test_images(project, source, [baseline, candidate], 2**31 if payload.full_test else payload.max_images)
                                if payload.task == 'patch_classification' else
                                _test_images(source, payload.task, None if payload.full_test else payload.max_images))
    truth_binding=None
    if not intake_lineage and payload.task!='patch_classification':
        from backend.engine.comparison_truth import bind_truth
        try:truth_binding=bind_truth(project,source,payload.task,[baseline,candidate],images)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
    return dict(baseline=baseline, candidate=candidate, baseline_task=baseline_task, candidate_task=candidate_task, model_hashes=model_hashes, dataset_fingerprint=dataset_fingerprint, intake_lineage=intake_lineage, images=images, total_test_images=total_test_images, truth_binding=truth_binding,execution=execution)


def _binding_from_inputs(project, source, inputs):
    manifest = Path(project['project_dir']) / 'project.json'
    if manifest.is_symlink():
        raise HTTPException(409, 'Project binding cannot be linked')
    try:
        current = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Project binding unavailable') from exc
    for key in ('id', 'source_dataset_dir', 'active_labelset_id', 'task'):
        default = 'default' if key == 'active_labelset_id' else None
        if current.get(key, default) != project.get(key, default):
            raise HTTPException(409, 'Project scope changed during comparison input capture')
    return {"project_id": project["id"], "labelset_id": project.get("active_labelset_id", "default"),
            "source_dataset_path": str(source), "inputs": inputs,
            "scope_identity": {key: current.get(key) for key in ('id', 'source_dataset_dir', 'active_labelset_id', 'task')}}


def _comparison_binding(payload, project, source):
    return _binding_from_inputs(project, source, _comparison_inputs(payload, project, source))


def _run_comparison(payload: ComparisonRequest, project, source, progress=None, cancelled=None,
                    publish_report=None, expected_binding=None, execution_id=None):
    inputs = _comparison_inputs(payload, project, source)
    binding = _binding_from_inputs(project, source, inputs) if expected_binding is not None else None
    if expected_binding is not None and binding != expected_binding:
        raise HTTPException(409, "Comparison inputs changed after acceptance")
    baseline, candidate = inputs['baseline'], inputs['candidate']
    baseline_task, candidate_task = inputs['baseline_task'], inputs['candidate_task']
    model_hashes, dataset_fingerprint = inputs['model_hashes'], inputs['dataset_fingerprint']
    intake_lineage, images = inputs['intake_lineage'], inputs['images']
    total_test_images, truth_binding = inputs['total_test_images'], inputs['truth_binding']
    execution=inputs['execution']
    execution_id=execution_id or 'compare_'+uuid.uuid4().hex
    operation_binding=hashlib.sha256(json.dumps({'execution_id':execution_id,'project_id':project['id'],
        'labelset_id':project.get('active_labelset_id','default'),'inputs':inputs,
        'parameters':payload.model_dump()},sort_keys=True,separators=(',',':')).encode()).hexdigest()
    if progress: progress(0, len(images))
    if any(_sha256(Path(model['checkpoint_path'])) != model_hashes[key]
           for key,model in (('incumbent',baseline),('candidate',candidate))):
        raise HTTPException(409,'비교 중 모델 checkpoint가 바뀌었습니다. 다시 실행해 주세요.')
    paths = {baseline["job_id"]: Path(baseline["checkpoint_path"]), candidate["job_id"]: Path(candidate["checkpoint_path"])}
    engines = {} if execution['execution_target']=='selected_compute' else {
        job_id: FlowchartEngine(device="cpu", checkpoint_resolver=lambda requested, _task, paths=paths: paths.get(requested))
        for job_id in paths
    }
    pipelines={baseline['job_id']:_pipeline(baseline_task,baseline['job_id']),candidate['job_id']:_pipeline(candidate_task,candidate['job_id'])}
    from backend.engine.flowchart_engine import ordered_linear_nodes
    for model,params in ((baseline,payload.incumbent_params),(candidate,payload.candidate_params)):
        pipeline=pipelines[model['job_id']]
        if params:
            node=next(n for n in pipeline.nodes if n.data.node_type in ('inspection','detection_crop'))
            node.data.params.update({key:value for key,value in params.items() if key not in ('threshold','score_spec')})
            if 'threshold' in params or 'score_spec' in params:
                from backend.engine.score_contract import resolve_inference_score
                try:
                    threshold, spec = resolve_inference_score(model['checkpoint_path'], model['task'], params.get('threshold'), params.get('score_spec'))
                except (ValueError,TypeError,KeyError) as exc:raise HTTPException(422,str(exc)) from exc
                node.data.threshold=threshold
                node.data.score_spec=spec
        try:ordered_linear_nodes(pipeline)
        except ValueError as exc:raise HTTPException(422,str(exc)) from exc
    rows: list[dict[str, Any]] = []
    for image in images:
        if cancelled and cancelled(): raise InterruptedError("Comparison cancelled by user")
        row = dict(image)
        from backend.engine.dataset_metadata import metadata_for_path
        metadata = metadata_for_path(Path(project["project_dir"]), source, Path(image["file_path"]), routes_dataset.STUDIO_ANNOTATIONS_DIR)
        row.update({key: metadata.get(key) for key in ("image_uuid", "content_hash", "content_version", "revision", "product", "lot", "group", "tags")})
        for key, model in (("incumbent", baseline), ("candidate", candidate)):
            if cancelled and cancelled():raise InterruptedError('Comparison cancelled before next model')
            try:
                if execution['execution_target']=='selected_compute':
                    from backend.remote.operations import run_verified_flowchart_on_compute
                    from backend.remote.profiles import ComputeProfile
                    result=run_verified_flowchart_on_compute(ComputeProfile.model_validate(execution['compute_profile']),
                        project,pipelines[model['job_id']].model_dump(),
                        {(model['job_id'],model['task']):Path(model['checkpoint_path'])},
                        Path(image.get('evaluation_file_path') or image['file_path']),image['image_id'],
                        device=execution['device'],comparison_binding_sha256=operation_binding,cancelled=cancelled)
                else:
                    result = engines[model["job_id"]].execute(
                        pipeline=pipelines[model["job_id"]], image_path=image.get('evaluation_file_path') or image["file_path"], image_id=image["image_id"],
                    )
                    result={**result,'execution_target':'local_cpu','execution_device':'cpu','device_name':'Host CPU'}
                row[key] = _outcome(result)
            except InterruptedError:raise
            except Exception as exc:
                if execution['execution_target']=='selected_compute':
                    # An uncertain remote operation keeps its lease; do not
                    # proceed to another model or publish a partial target claim.
                    raise
                row[key] = {"verdict": None, "defective_roi_count": None, "max_defect_score": None,
                            "reason": "", "error": str(exc)[:500]}
        row["disagrees"] = bool(
            row["incumbent"]["verdict"] and row["candidate"]["verdict"]
            and row["incumbent"]["verdict"] != row["candidate"]["verdict"]
        )
        rows.append(row)
        if progress: progress(len(rows), len(images))

    if cancelled and cancelled(): raise InterruptedError("Comparison cancelled by user")
    if _comparison_execution(payload)!=execution:
        raise HTTPException(409,'Comparison compute profile changed during execution')
    # Reject a mixed-version comparison; the saved report must describe one
    # exact dataset, image list, and pair of checkpoint contents.
    if _fingerprint(source) != dataset_fingerprint:
        raise HTTPException(status_code=409, detail="비교 중 데이터가 바뀌었습니다. 다시 실행해 주세요.")
    if any(_sha256(Path(model["checkpoint_path"])) != model_hashes[key]
           for key, model in (("incumbent", baseline), ("candidate", candidate))):
        raise HTTPException(status_code=409, detail="비교 중 모델 checkpoint가 바뀌었습니다. 다시 실행해 주세요.")
    if any(_sha256(Path(image["file_path"])) != image["image_sha256"] for image in images):
        raise HTTPException(status_code=409, detail="비교 중 test 이미지가 바뀌었습니다. 다시 실행해 주세요.")
    if intake_lineage:
        from backend.engine.intake_lineage import verify_ancestor_model,verify_current_model
        for model in (baseline,candidate):
            if model.get('intake_lineage'):
                try:current=verify_ancestor_model(project,source,model['checkpoint_path'],model['task'])
                except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
                if current!=model['intake_lineage']:raise HTTPException(409,'Intake frozen cohort or truth changed during comparison')
            else:
                try:verify_current_model(project,source,model['checkpoint_path'],model['task'],intake_lineage)
                except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc

    summary = _summary(rows)
    if truth_binding:
        from backend.engine.comparison_truth import verify_truth
        try:verify_truth(project,source,truth_binding)
        except (ValueError,OSError,KeyError,TypeError) as exc:raise HTTPException(409,str(exc)) from exc
    report = {
        "schema_version": 1,
        "comparison_id": f"comparison_{uuid.uuid4().hex}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project_id": project["id"],
        "source_dataset_path": str(source),
        "task": payload.task,
        "incumbent_job_id": baseline["job_id"],
        "candidate_job_id": candidate["job_id"],
        'incumbent_task':baseline_task,'candidate_task':candidate_task,
        'labelset_id':project.get('active_labelset_id','default'),
        'comparison_parameters':{'incumbent':payload.incumbent_params,'candidate':payload.candidate_params},
        "incumbent_training_dataset_fingerprint": baseline["training_dataset_fingerprint"],
        "candidate_training_dataset_fingerprint": candidate["training_dataset_fingerprint"],
        "dataset_fingerprint": dataset_fingerprint,
        "model_sha256": model_hashes,
        "full_test": payload.full_test,
        "image_selection": "all held-out test images" if payload.full_test else (
            "patch: deterministic OK/NG balanced test sources when count exceeds limit; exact paths and SHA-256 below"
            if payload.task == "patch_classification" else
            "first N test images in dataset gallery order; exact paths and SHA-256 saved below"
        ),
        "selected_image_count": len(images),
        "total_test_images": total_test_images,
        "status": "completed" if summary["error_images"] == 0 else "completed_with_errors",
        "summary": summary,
        "grouped_errors": __import__("backend.engine.evaluation_history", fromlist=["grouped_errors"]).grouped_errors(rows, payload.task),
        "limitations": [
            "선택한 test 이미지에서 두 모델의 원판정을 비교한 결과입니다. 전체 데이터 성능을 뜻하지 않습니다.",
            "동일 장치에서 순차 실행해도 지연 시간이나 FPS 비교 근거로 사용하지 않습니다.",
            "OK 정답 이미지가 없으면 과검률을 판단할 수 없습니다.",
            "모델 활성화·교체·롤백은 수행하지 않았습니다.",
        ],
        "images": rows,
        "execution": execution,
    }
    if intake_lineage:
        report['intake_lineage']={key:value for key,value in intake_lineage.items() if key!='images'}
        report['image_selection']='Verified intake ancestor frozen held-out image bytes and original scoped truth'
    if truth_binding:report['truth_binding']=truth_binding
    if publish_report is not None:
        publish_report(report)
        return report
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


def _report_available(report, request):
    marker = report.get('_durable_comparison')
    if marker is None:
        return True
    try:
        context, key, _, jobs = _durable_scope(request)
        jobs.scoped(marker['job_id'], context, key)
        result = jobs.store.checkpoint_value(marker['job_id']).get('result', {})
        return result.get('id') == report.get('comparison_id') and jobs.available(marker['job_id'])
    except (KeyError, ValueError, OSError, TypeError):
        return False


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
                        and report.get("task") == task and report.get("comparison_id") == path.stem and _report_available(report, request)):
                    reports.append(_summary_record(report))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    reports.sort(key=lambda report: report["created_at"], reverse=True)
    return {"comparisons": reports, "total": len(reports)}


class AsyncComparisonRequest(ComparisonRequest):
    full_test: bool = True


def _jobs(project):
    """Legacy receipt API, retained for existing callers."""
    from backend.engine.evaluation_history import ComparisonJobs
    return ComparisonJobs(Path(project["project_dir"]) / "reports" / "comparison_jobs.sqlite3")


def _durable_scope(request):
    from backend.contracts.context import get_project_context
    from backend.engine.comparison_jobs import ComparisonJobs
    from backend.engine.job_store import ledger
    context = get_project_context(request)
    return context, request.app.state.context_registry.project_key(context), get_current_project(request), ComparisonJobs(ledger())


def _legacy_rows(project):
    from backend.engine.evaluation_history import ComparisonJobs
    path = Path(project["project_dir"]) / "reports" / "comparison_jobs.sqlite3"
    if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
        return []
    rows = ComparisonJobs(path, recover=False, read_only=True).list()
    for row in rows:
        row.update(durable=False, legacy=True, ownership_verified=False, resumable=False)
    return rows


def _job_view(jobs, identifier, request):
    decision = getattr(request.state, 'permission_decision', None)
    may_recover = decision is None or decision.role in ('owner', 'trainer', 'reviewer')
    return jobs.view(identifier, recover=may_recover)


def _durable_row(jobs, identifier, context, key, source, task, request):
    try:
        record = jobs.scoped(identifier, context, key)
    except KeyError:
        raise HTTPException(404, "Comparison job not found in this actor/project scope") from None
    payload = json.loads(record['spec_json'])['request']
    if payload['source_dataset_path'] != str(source) or payload['task'] != task:
        raise HTTPException(404, "Comparison job belongs to another source")
    return _job_view(jobs, identifier, request)


@router.post("/jobs", status_code=202)
def queue_comparison(payload: AsyncComparisonRequest, request: Request):
    from backend.engine.job_store import JobConflict
    context, key, project, jobs = _durable_scope(request)
    idempotency = request.headers.get('Idempotency-Key')
    if idempotency is not None and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', idempotency):
        raise HTTPException(422, 'Invalid Idempotency-Key')
    normalized = payload.model_dump()
    normalized['source_dataset_path'] = str(Path(payload.source_dataset_path).expanduser().resolve())
    try:
        held = jobs.replay(context, key, idempotency, normalized)
        if held is not None:
            return jobs.view(held['id'])
        project, source = _scope(request, normalized['source_dataset_path'], payload.task)
        if payload.incumbent_job_id == payload.candidate_job_id:
            raise HTTPException(409, 'Comparison requires two distinct completed source-bound models')
        binding = _comparison_binding(payload, project, source)
        identifier, created = jobs.submit(context, key, project, normalized, binding, idempotency)
    except JobConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    if created:
        def worker():
            def verify(expected):
                if _comparison_binding(payload, project, source) != expected:
                    raise HTTPException(409, 'Comparison inputs changed after acceptance')
            jobs.run(identifier,
                     lambda progress, cancelled, publish, expected: _run_comparison(
                         payload, project, source, progress, cancelled, publish_report=publish, expected_binding=expected,execution_id=identifier),
                     _report_dir(project), verify)
        import threading
        from contextvars import copy_context
        copied = copy_context()
        try:
            threading.Thread(target=copied.run, args=(worker,), daemon=True, name=identifier).start()
        except Exception:
            # Accepted evidence remains durable; view resolves undispatched work as interrupted.
            from backend.engine.comparison_jobs import release_dispatch
            release_dispatch(jobs, identifier, failed=True)
            return jobs.view(identifier)
    return jobs.view(identifier)


@router.get("/jobs")
def list_comparison_jobs(request: Request, source_dataset_path: str, task: Task):
    project, source = _scope(request, source_dataset_path, task)
    context, key, _, jobs = _durable_scope(request)
    from backend.engine.comparison_jobs import KIND
    from backend.engine.job_state import TERMINAL
    records = jobs.store.active(KIND) + jobs.store.ended(KIND, key, TERMINAL)
    rows = []
    for record in records:
        try:
            jobs.scoped(record['id'], context, key)
            payload = json.loads(record['spec_json'])['request']
            if payload['source_dataset_path'] == str(source) and payload['task'] == task:
                rows.append(_job_view(jobs, record['id'], request))
        except KeyError:
            continue
    rows.extend(row for row in _legacy_rows(project)
                if row['payload']['source_dataset_path'] == str(source) and row['payload']['task'] == task)
    rows.sort(key=lambda row: row['created_at'], reverse=True)
    return {'jobs': rows, 'total': len(rows)}


def task_center_comparisons(request: Request, project: dict, warnings: list | None = None):
    """Read existing actor-owned durable jobs, without starting or retargeting them.

    Legacy journals have no actor/labelset binding and remain in their original
    comparison screen. A project task change does not discard durable history.
    """
    from backend.engine.comparison_jobs import KIND
    from backend.engine.job_state import TERMINAL
    from backend.engine.job_observation import cancellation_evidence
    from dataclasses import asdict
    context, key, _, jobs = _durable_scope(request)
    source = str(Path(project['source_dataset_dir']).expanduser().resolve()) if project.get('source_dataset_dir') else ''
    labelset = project.get('active_labelset_id', 'default')
    decision = getattr(request.state, 'permission_decision', None)
    may_control = decision is None or decision.role in ('owner', 'trainer', 'reviewer')
    rows = []
    for record in jobs.store.active(KIND) + jobs.store.ended(KIND, key, TERMINAL):
        try:
            jobs.scoped(record['id'], context, key)
        except KeyError:
            continue
        try:
            spec = json.loads(record['spec_json'])
            if not isinstance(spec,dict) or not isinstance(spec.get('request'),dict) or not isinstance(spec.get('binding'),dict):
                raise ValueError('Invalid comparison specification')
            payload, binding = spec['request'], spec['binding']
            if payload['source_dataset_path'] != source or binding.get('labelset_id', 'default') != labelset:
                continue
            if not isinstance(payload.get('task'),str) or not payload['task']:
                raise ValueError('Missing recorded comparison task')
            row = _job_view(jobs, record['id'], request)
        except (ValueError,TypeError,KeyError):
            if warnings is not None:
                warnings.append({'kind':KIND,'message':f"저장된 비교 {record['id']}의 기록을 읽을 수 없습니다. 다른 작업은 계속 표시합니다."})
            continue
        # Ledger completion verifies report publication, not execution return or
        # release of a selected worker. Unknown steps stay unknown here.
        cancel = cancellation_evidence({}, jobs.store.cancel_intent(record['id']), None)
        rows.append({**row, 'kind': KIND, 'execution_job_id': row['job_id'],
                     'task': payload['task'], 'source_dataset_path': source,
                     'training_provenance': {'labelset_id': labelset},
                     'compute_profile_id': payload.get('compute_profile_id'),
                     'cancel_supported': may_control and row['state'] not in TERMINAL and not row['cancel_requested'],
                     'observation': {'cancel': asdict(cancel), 'worker_recorded': bool(row['attempts']),
                         'next_action': ('비교 요청은 중단됐습니다. 자동 재개는 지원하지 않습니다. 입력을 확인하고 새 비교를 요청하세요.'
                                         if row['status']=='interrupted' else
                                         '취소 요청을 저장했습니다. 같은 비교의 실행 종료와 자원 반환을 다시 확인하세요.'
                                         if row['cancel_requested'] else None)}})
    return sorted(rows, key=lambda row: row['created_at'], reverse=True)


@router.get("/jobs/{job_id}")
def comparison_job(job_id: str, request: Request, source_dataset_path: str, task: Task):
    project, source = _scope(request, source_dataset_path, task)
    context, key, _, jobs = _durable_scope(request)
    try:
        jobs.store.record(job_id)
    except KeyError:
        for row in _legacy_rows(project):
            if row['job_id'] == job_id and row['payload']['source_dataset_path'] == str(source) and row['payload']['task'] == task:
                return row
        raise HTTPException(404, 'Comparison job not found') from None
    return _durable_row(jobs, job_id, context, key, source, task, request)


@router.post("/jobs/{job_id}/cancel")
def cancel_comparison_job(job_id: str, request: Request, source_dataset_path: str, task: Task):
    row = comparison_job(job_id, request, source_dataset_path, task)
    context, key, project, jobs = _durable_scope(request)
    if row.get('legacy'):
        _jobs(project).cancel(job_id)
        return comparison_job(job_id, request, source_dataset_path, task)
    jobs.cancel(job_id)
    return jobs.view(job_id)


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
    if report.get("comparison_id") != comparison_id or not _report_available(report, request):
        raise HTTPException(409, "Durable comparison report is not verified/available in this actor scope")
    return report


@router.get('/{comparison_id}/evidence-image')
def comparison_image(comparison_id: str, request: Request, source_dataset_path: str, task: Task, image_path: str):
    report = get_comparison(comparison_id, request, source_dataset_path, task)
    row = next((row for row in report.get('images', []) if row.get('file_path') == image_path), None)
    if row is None:
        raise HTTPException(404, 'Image does not belong to this comparison.')
    expected = row.get('image_sha256')
    if not expected:
        raise HTTPException(409, 'This comparison has no captured source image hash.')
    from backend.engine.evidence_image import verified_preview
    try:
        preview = verified_preview(image_path, Path(report['source_dataset_path']), expected)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'comparison_id': comparison_id, **preview}


@router.get('/{comparison_id}/export')
def export_comparison(comparison_id: str, request: Request, source_dataset_path: str, task: Task):
    report = get_comparison(comparison_id, request, source_dataset_path, task)
    project, _ = _scope(request, source_dataset_path, task)
    path = _report_dir(project) / f'{comparison_id}.json'
    if path.is_symlink():
        raise HTTPException(409, 'Comparison changed during export')
    try:
        raw = path.read_bytes()
        if json.loads(raw) != report:
            raise ValueError('Comparison changed during export')
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Comparison changed during export') from exc
    return {'schema': 'ModelComparisonEvidenceExport/v1',
            'saved_report_sha256': hashlib.sha256(raw).hexdigest(), 'report': report,
            'binary_metrics': _binary_metrics(report['images']),
            'task_specific_metrics': {'status': 'unavailable',
                'reason': 'Image OK/NG verdict evidence does not establish object, pixel, text, angle or enhancement metrics.'},
            'scope': 'Saved comparison evidence; not threshold tuning, representative quality approval or deployment.'}

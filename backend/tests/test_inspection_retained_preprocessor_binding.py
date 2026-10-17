"""Batch admission must revalidate an explicit original rotation binding.

The original model/version/registry/inspection APIs execute. Only the final
remote operation is controlled: these are not GPU, model-quality or native QA.
"""
import hashlib
import json
import os
from pathlib import Path
import runpy
import sqlite3
import sys
from contextlib import contextmanager

import pytest


SOURCE = Path(os.environ.get("INSPECTION_RETAINED_SOURCE_ROOT") or Path(__file__).resolve().parents[2]).resolve()
if not Path(__file__).resolve().is_relative_to(SOURCE):
    runpy.run_path(str(SOURCE / "backend/conftest.py"))
    sys.path.insert(0, str(SOURCE))

from backend.tests.test_retained_preprocessor_binding import retained_sources, _bind, _pipeline


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _prepare(case, tmp_path, monkeypatch):
    from backend.remote import operations
    client = case["client"]
    binding = _bind(case)
    graph = _pipeline(case)
    source = case["segmentation_source"]
    profile = {"id": "retained-batch-component-gpu2", "name": "Component only GPU2",
               "ssh_target": "component.invalid", "ssh_port": 22,
               "remote_root": "/component-only", "runtime_kind": "python",
               "runtime_value": "python3", "gpu_selector": "2"}
    response = client.post("/api/compute/profiles", json=profile)
    assert response.status_code == 201, response.text
    saved = client.post("/api/flowchart/pipeline", params={
        "recipe_task": "segmentation", "source_dataset_path": str(source)}, json=graph.model_dump())
    assert saved.status_code == 200, saved.text
    images = client.get("/api/dataset/images", params={"folder_path": str(source), "task": "segmentation"})
    assert images.status_code == 200, images.text
    payload = {"source_folder": str(source), "task": "segmentation", "scope": "all",
               "pipeline": graph.model_dump(), "images": images.json()["items"],
               "execution_target": "selected_compute", "device": "cuda",
               "compute_profile_id": profile["id"], "project_id": case["project"]["id"]}
    assert len(payload["images"]) == 1
    expected = {(case["rotation_job"], "rotation"): (case["rotation_model"] / "best_model.pt").resolve(),
                (case["segmentation_job"], "segmentation"):
                    (Path(case["project"]["models_dir"]) / case["segmentation_job"] / "best_model.pt").resolve()}
    calls = []

    def external_operation(context, operation, spec, *, transport=None, input_files):
        assert context.profile.id == profile["id"] and context.profile.gpu_selector == "2"
        assert operation == "flowchart_run" and spec["device"] == "cuda"
        assert spec["pipeline"] == graph.model_dump()
        assert {(row["job_id"], row["task"]) for row in spec["models"]} == set(expected)
        for row in spec["models"]:
            checkpoint = expected[(row["job_id"], row["task"])]
            assert input_files[row["checkpoint_path"]] == checkpoint
            assert row["checkpoint_sha256"] == _sha(checkpoint)
            assert input_files[row["metadata_path"]] == checkpoint.parent / "model_meta.json"
        assert input_files[spec["image_path"]] == source / "sample.png"
        calls.append(spec)
        result = tmp_path / "controlled-operation" / "outputs" / "flowchart_result.json"
        result.parent.mkdir(parents=True)
        result.write_text(json.dumps({
            "status": "success", "final_verdict": "REVIEW", "roi_count": 1,
            "is_ok": False, "rejection_reason": "Controlled component REVIEW", "total_latency_ms": 1.0,
            "defective_roi_count": 0, "execution_device": "cuda",
            "device_name": "CONTROLLED-NOT-A-GPU-RESULT", "image_path": spec["image_path"],
            "image_sha256": spec["image_sha256"], "image_id": spec["image_id"],
            "model_job_ids": sorted(job for job, _task in expected), "annotated_image": None, "crops": [],
            "execution_steps": [{"node_id": "component-final-operation", "name": "Controlled external result",
                                 "status": "passed", "latency_ms": 1}],
        }), encoding="utf-8")
        return {"outputs/flowchart_result.json": result}

    monkeypatch.setattr(operations, "run_remote_operation_artifacts", external_operation)
    return binding, payload, expected, calls


@contextmanager
def _damage(case, binding, kind):
    held = None
    if kind == "missing_binding":
        held = Path(case["project"]["project_dir"]) / "held-binding-control.json"
        binding.rename(held)
        try:
            yield
        finally:
            held.rename(binding)
        return
    if kind == "source_model_drift":
        path = case["rotation_model"] / "best_model.pt"
        raw = path.read_bytes()
        path.write_bytes(raw + b"changed original checkpoint")
    elif kind == "source_project_drift":
        path = Path(case["rotation_project"]["project_dir"]) / "project.json"
        raw = path.read_bytes()
        value = json.loads(raw)
        value["name"] = "Changed original source project"
        path.write_text(json.dumps(value), encoding="utf-8")
    else:
        path = binding
        raw = path.read_bytes()
        value = json.loads(raw)
        if kind == "forged_source":
            value["source_project_id"] = "not-registered-original-source"
        elif kind == "wrong_target":
            value["target"]["project_id"] = "another-target-project"
        else:
            raise AssertionError(kind)
        path.write_text(json.dumps(value), encoding="utf-8")
    try:
        yield
    finally:
        path.write_bytes(raw)


def test_inspection_batch_accepts_and_dispatches_only_original_bound_rotation(retained_sources, tmp_path, monkeypatch):
    case = retained_sources
    _binding, payload, expected, calls = _prepare(case, tmp_path, monkeypatch)
    client = case["client"]
    created = client.post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    assert created.json()["model_sha256"] == {job: _sha(path) for (job, _task), path in expected.items()}
    with sqlite3.connect(Path(case["project"]["project_dir"]) / "inspection_history.sqlite3") as conn:
        stored = json.loads(conn.execute("SELECT model_paths_json FROM runs WHERE run_id=?", (run_id,)).fetchone()[0])
    assert stored == {job: str(path) for (job, _task), path in expected.items()}
    image = payload["images"][0]["file_path"]
    executed = client.post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": image})
    assert executed.status_code == 200, executed.text
    assert len(calls) == 1
    assert executed.json()["execution_target"] == "selected_compute"
    assert executed.json()["execution_device"] == "cuda"
    assert executed.json()["compute_profile_id"] == payload["compute_profile_id"]
    saved = client.get(f"/api/inspections/runs/{run_id}").json()
    assert saved["rows"][0]["state"] == "REVIEW"
    assert saved["rows"][0]["result"]["model_sha256"] == created.json()["model_sha256"]
    assert not (Path(case["project"]["models_dir"]) / "rotation" / case["rotation_job"]).exists()


@pytest.mark.parametrize("kind", ["missing_binding", "forged_source", "wrong_target", "source_model_drift", "source_project_drift"])
def test_inspection_batch_create_refuses_invalid_original_rotation_binding(retained_sources, tmp_path, monkeypatch, kind):
    case = retained_sources
    binding, payload, _expected, calls = _prepare(case, tmp_path, monkeypatch)
    with _damage(case, binding, kind):
        refused = case["client"].post("/api/inspections/runs", json=payload)
        assert refused.status_code == 409, refused.text
    assert calls == []


@pytest.mark.parametrize("kind", ["missing_binding", "forged_source", "wrong_target", "source_model_drift", "source_project_drift"])
def test_inspection_batch_execute_rechecks_original_binding_before_any_dispatch(retained_sources, tmp_path, monkeypatch, kind):
    case = retained_sources
    binding, payload, _expected, calls = _prepare(case, tmp_path, monkeypatch)
    created = case["client"].post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    with _damage(case, binding, kind):
        refused = case["client"].post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": payload["images"][0]["file_path"]})
        assert refused.status_code == 409, refused.text
    assert calls == []
    assert case["client"].get(f"/api/inspections/runs/{run_id}").json()["rows"][0]["state"] == "pending"


@pytest.mark.parametrize("changed", ["path", "sha256"])
def test_inspection_batch_retained_reference_cannot_replace_frozen_checkpoint(retained_sources, tmp_path, monkeypatch, changed):
    case = retained_sources
    _binding, payload, _expected, calls = _prepare(case, tmp_path, monkeypatch)
    created = case["client"].post("/api/inspections/runs", json=payload)
    assert created.status_code == 200, created.text
    run_id = created.json()["run_id"]
    column = "model_paths_json" if changed == "path" else "model_sha256_json"
    with sqlite3.connect(Path(case["project"]["project_dir"]) / "inspection_history.sqlite3") as conn:
        value = json.loads(conn.execute(f"SELECT {column} FROM runs WHERE run_id=?", (run_id,)).fetchone()[0])
        value[case["rotation_job"]] = (str(Path(case["project"]["models_dir"]) / case["segmentation_job"] / "best_model.pt")
                                         if changed == "path" else "0" * 64)
        conn.execute(f"UPDATE runs SET {column}=? WHERE run_id=?", (json.dumps(value), run_id))
    refused = case["client"].post(f"/api/inspections/runs/{run_id}/execute", json={"image_path": payload["images"][0]["file_path"]})
    assert refused.status_code == 409, refused.text
    assert calls == []

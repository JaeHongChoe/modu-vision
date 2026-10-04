"""The exported flow is portable, bound to its models, and detects corruption."""

import json
import importlib
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest
import torch
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset, routes_export, routes_flowchart
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.flowchart_engine import get_fixed_roi_flowchart, get_single_detection_flowchart, get_single_segmentation_flowchart
from backend.engine.classification.model import create_classification_model
from backend.main import create_app

def build_flow_package(**kwargs):
    try:
        module = importlib.import_module("backend.engine.flow_package")
    except ModuleNotFoundError:
        pytest.fail("Whole-flow package builder is missing")
    return module.build_flow_package(**kwargs)


def _checkpoint(tmp_path: Path) -> Path:
    model_dir = tmp_path / "job_detector"
    model_dir.mkdir()
    checkpoint = model_dir / "best_model.pt"
    checkpoint.write_bytes(b"detector checkpoint fixture")
    (model_dir / "model_meta.json").write_text(
        json.dumps({"task": "detection", "image_size": [256, 256]}), encoding="utf-8",
    )
    return checkpoint


def test_flow_package_contains_saved_graph_and_checksum_bound_model(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    pipeline = get_single_detection_flowchart(job_id="job_detector")

    result = build_flow_package(
        pipeline=pipeline,
        checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports",
        package_name="line_a_flow",
    )

    package = Path(result["package_path"])
    saved = json.loads((package / "pipeline.json").read_text(encoding="utf-8"))
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert saved == pipeline.model_dump()
    assert manifest["models"] == [{
        "job_id": "job_detector",
        "task": "detection",
        "checkpoint": "models/job_detector/best_model.pt",
    }]
    assert (package / "models/job_detector/best_model.pt").read_bytes() == checkpoint.read_bytes()
    assert json.loads((package / "models/job_detector/model_meta.json").read_text(encoding="utf-8")) == {
        "task": "detection", "image_size": [256, 256],
    }
    assert (package / "run_flow.py").is_file()
    assert (package / "backend/engine/flowchart_engine.py").is_file()

    command = [sys.executable, str(package / "run_flow.py"), "--verify-only"]
    env = {**os.environ, "PYTHONPATH": ""}
    verified = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert verified.returncode == 0, verified.stderr

    poison = tmp_path / "poison" / "backend"
    poison.mkdir(parents=True)
    (poison / "__init__.py").write_text("raise RuntimeError('external backend imported')\n", encoding="utf-8")
    isolated = subprocess.run(command, cwd=tmp_path, env={**env, "PYTHONPATH": str(poison.parent)},
                              capture_output=True, text=True)
    assert isolated.returncode == 0, isolated.stderr

    (package / "models/job_detector/best_model.pt").write_bytes(b"tampered checkpoint")
    rejected = subprocess.run(command, cwd=tmp_path, env=env, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "checksum" in rejected.stderr.lower()


@pytest.mark.parametrize("windows,busy_calls,expected_calls", [(True, 2, 3), (True, 100, 4), (False, 2, 1)])
def test_package_publication_waits_out_windows_readers_but_refuses_persistent_errors(
        tmp_path, monkeypatch, windows, busy_calls, expected_calls):
    from backend.remote import file_replace
    checkpoint = _checkpoint(tmp_path)
    real_rename = os.rename
    attempts, sleeps = [], []
    def busy_reader(source, target):
        attempts.append((Path(source), Path(target)))
        if len(attempts) <= busy_calls:
            raise PermissionError(13, 'Access is denied')
        return real_rename(source, target)
    monkeypatch.setattr(file_replace, '_WINDOWS', windows)
    monkeypatch.setattr(file_replace, 'ATTEMPTS', 4)
    monkeypatch.setattr(file_replace.time, 'sleep', sleeps.append)
    monkeypatch.setattr(os, 'rename', busy_reader)
    kwargs = dict(pipeline=get_single_detection_flowchart(job_id='job_detector'),
                  checkpoints={'job_detector': checkpoint}, output_base_dir=tmp_path/'exports', package_name='reader_flow')
    if windows and busy_calls < 4:
        result = build_flow_package(**kwargs)
        package = Path(result['package_path'])
        from backend.engine.flow_package_runtime import verify_flow_package
        verify_flow_package(package)
        assert (package/'models/job_detector/best_model.pt').read_bytes() == checkpoint.read_bytes()
    else:
        with pytest.raises(PermissionError): build_flow_package(**kwargs)
        assert not (tmp_path/'exports'/'reader_flow').exists()
        assert not list((tmp_path/'exports').iterdir())
    assert len(attempts) == expected_calls
    assert len(sleeps) == expected_calls - 1
    assert checkpoint.read_bytes() == b'detector checkpoint fixture'


def test_approved_release_rejects_checkpoint_hash_mismatch_before_package_write(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    with pytest.raises(ValueError, match="Approved checkpoint SHA-256"):
        build_flow_package(
            pipeline=get_single_detection_flowchart(job_id="job_detector"),
            checkpoints={"job_detector": checkpoint},
            output_base_dir=tmp_path / "exports",
            package_name="invalid_release",
            approved_revisions={"job_detector": {
                "revision_id": "a" * 32, "job_id": "job_detector", "task": "detection",
                "checkpoint_sha256": "0" * 64,
            }},
        )
    assert not (tmp_path / "exports" / "invalid_release").exists()


def test_approved_release_records_revision_and_pins_whole_manifest(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    model_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    result = build_flow_package(
        pipeline=get_single_detection_flowchart(job_id="job_detector"),
        checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports",
        package_name="approved_release",
        approved_revisions={"job_detector": {
            "revision_id": "a" * 32, "job_id": "job_detector", "task": "detection",
            "checkpoint_sha256": model_sha,
        }},
    )
    package = Path(result["package_path"])
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    revisions = [{"revision_id": "a" * 32, "job_id": "job_detector", "task": "detection",
                  "checkpoint_sha256": model_sha}]
    assert manifest["release"] == {"approval_revisions": revisions}
    assert result["release_policy"] == {
        "schema_version": 1,
        "manifest_sha256": hashlib.sha256((package / "manifest.json").read_bytes()).hexdigest(),
        "approval_revisions": revisions,
    }


@pytest.mark.parametrize("name", ["../outside", "nested/name", "bad\\name", "..", ""])
def test_flow_package_rejects_unsafe_names_without_creating_output(tmp_path: Path, name: str):
    checkpoint = _checkpoint(tmp_path)
    with pytest.raises(ValueError, match="package name"):
        build_flow_package(
            pipeline=get_single_detection_flowchart(job_id="job_detector"),
            checkpoints={"job_detector": checkpoint},
            output_base_dir=tmp_path / "exports",
            package_name=name,
        )
    assert not (tmp_path / "exports").exists()


def test_flow_package_rejects_symlinked_output_parent(tmp_path: Path):
    checkpoint = _checkpoint(tmp_path)
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "exports").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic link"):
        build_flow_package(
            pipeline=get_single_detection_flowchart(job_id="job_detector"),
            checkpoints={"job_detector": checkpoint},
            output_base_dir=project / "exports" / "flows", package_name="safe_flow",
        )
    assert list(outside.iterdir()) == []


def test_export_flow_route_requires_saved_graph_and_source_matched_model(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    app = create_app(project_dir=str(tmp_path / "workspace_registry"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.get("/api/project/current").json()
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"source image fixture")
    request = {"source_dataset_path": str(source), "recipe_task": "detection", "package_name": "saved_line"}

    assert client.post("/api/export/flow", json=request).status_code == 409

    job_id = "job_saved_detector"
    job_dir = Path(project["models_dir"]) / job_id
    (job_dir / "dataset").mkdir(parents=True)
    torch.save({"task": "detection", "model_state_dict": {}}, job_dir / "best_model.pt")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "detection"}), encoding="utf-8")
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "detection", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(job_dir / "dataset"),
    }), encoding="utf-8")
    pipeline = get_single_detection_flowchart(job_id=job_id)
    saved = client.post("/api/flowchart/pipeline", params={
        "recipe_task": "detection", "source_dataset_path": str(source),
    }, json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text

    unreadable = client.post("/api/export/flow", json={
        **request, "package_name": "unreadable_image",
        "verification_image_path": str(source / "image.jpg"),
    })
    assert unreadable.status_code == 422
    assert not (Path(project["project_dir"]) / "exports" / "flows" / "unreadable_image").exists()

    exported = client.post("/api/export/flow", json=request)
    assert exported.status_code == 200, exported.text
    package = Path(exported.json()["package_path"])
    assert package.is_relative_to(Path(project["project_dir"]))
    assert json.loads((package / "pipeline.json").read_text(encoding="utf-8")) == pipeline.model_dump()

    checkpoint = job_dir / "best_model.pt"
    original_checkpoint = checkpoint.read_bytes()
    checkpoint.write_bytes(b"corrupt checkpoint")
    corrupt = client.post("/api/export/flow", json={**request, "package_name": "corrupt_line"})
    assert corrupt.status_code == 409
    assert not (package.parent / "corrupt_line").exists()
    checkpoint.write_bytes(original_checkpoint)

    parity_image = tmp_path / "parity.png"
    Image.new("RGB", (32, 32), (50, 60, 70)).save(parity_image)
    def fail_packaged_run(**_kwargs):
        raise ValueError("packaged runner failed")
    with monkeypatch.context() as patcher:
        patcher.setattr(routes_export.flow_package_engine, "verify_flow_parity_cohort", fail_packaged_run)
        failed_parity = client.post("/api/export/flow", json={
            **request, "package_name": "failed_parity",
            "verification_image_path": str(parity_image),
        })
    assert failed_parity.status_code == 409
    assert failed_parity.json()["detail"]["package_path"].endswith("failed_parity")
    assert failed_parity.json()["detail"]["parity"]["status"] == "failed"
    receipt = Path(failed_parity.json()["detail"]["package_path"]) / "parity_receipt.json"
    assert json.loads(receipt.read_text(encoding="utf-8"))["error"] == "packaged runner failed"

    (source / "image.jpg").write_bytes(b"changed source image")
    refused = client.post("/api/export/flow", json={**request, "package_name": "changed_line"})
    assert refused.status_code == 409
    assert not (package.parent / "changed_line").exists()


def test_exported_runner_rejects_unreadable_image_instead_of_synthetic_fallback(tmp_path: Path, monkeypatch):
    checkpoint = _checkpoint(tmp_path)
    pipeline = get_single_detection_flowchart(job_id="job_detector")
    package = Path(build_flow_package(
        pipeline=pipeline, checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports", package_name="image_validation",
    )["package_path"])
    invalid_image = tmp_path / "broken.jpg"
    invalid_image.write_bytes(b"not an image")

    from backend.engine import flowchart_engine, flow_package_runtime
    monkeypatch.setattr(flowchart_engine.FlowchartEngine, "execute", lambda self, **kwargs: {"final_verdict": "OK"})
    with pytest.raises(ValueError, match="readable image"):
        flow_package_runtime.run_flow_package(package, invalid_image)


def test_parity_comparison_ignores_latency_but_catches_changed_roi_verdict():
    from backend.engine import flow_package_runtime
    compare = getattr(flow_package_runtime, "compare_flow_results", None)
    assert compare is not None, "Whole-flow parity comparison is missing"
    reference = {
        "final_verdict": "NG", "roi_count": 1, "defective_roi_count": 1,
        "routed_output_node_id": "ng", "rejection_reason": "defect found",
        "inspected_image_size": [32, 32],
        "execution_steps": [{"node_id": "model", "status": "flagged_ng", "latency_ms": 9.0}],
        "crops": [{"roi_id": "crop_1", "label": "Bow", "bbox": [1, 2, 20, 21],
                   "verdict": "NG", "defect_score": 0.8234, "defect_area_px": 40}],
    }
    same = json.loads(json.dumps(reference))
    same["execution_steps"][0]["latency_ms"] = 125.0
    assert compare(reference, same)["status"] == "passed"

    same["crops"][0]["verdict"] = "OK"
    mismatch = compare(reference, same)
    assert mismatch["status"] == "mismatch"
    assert any("crops" in field for field in mismatch["mismatched_fields"])


def test_parity_comparison_checks_blob_and_branch_provenance():
    from backend.engine.flow_package_runtime import compare_flow_results

    reference = {
        "final_verdict": "NG", "roi_count": 1, "defective_roi_count": 1,
        "execution_steps": [{
            "node_id": "blob", "status": "flagged_ng", "input_count": 1,
            "output_count": 1, "branch_verdict": "NG",
            "selected_edge_ids": ["blob-aggregate"], "latency_ms": 1.0,
        }],
        "crops": [{
            "roi_id": "seg:fixed_roi", "source_node_id": "seg",
            "label": "defect", "bbox": [0, 0, 32, 32],
            "verdict": "NG", "defect_score": 0.8, "defect_area_px": 12,
            "blob_count": 2, "largest_blob_area_px": 8,
        }],
    }
    for path, replacement, expected in (
        (("execution_steps", 0, "branch_verdict"), "OK", "execution_steps[0].branch_verdict"),
        (("execution_steps", 0, "selected_edge_ids"), [], "execution_steps[0].selected_edge_ids"),
        (("crops", 0, "source_node_id"), "other", "crops[0].source_node_id"),
        (("crops", 0, "blob_count"), 1, "crops[0].blob_count"),
        (("crops", 0, "largest_blob_area_px"), 7, "crops[0].largest_blob_area_px"),
    ):
        changed = json.loads(json.dumps(reference))
        changed[path[0]][path[1]][path[2]] = replacement
        assert expected in compare_flow_results(reference, changed)["mismatched_fields"]


def test_flow_export_can_verify_real_image_parity_before_claiming_pass(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    app = create_app(project_dir=str(tmp_path / "workspace_registry"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.get("/api/project/current").json()
    source = tmp_path / "source"
    source.mkdir()
    image = source / "real.png"
    Image.new("RGB", (64, 64), (125, 40, 10)).save(image)
    job_id = "job_parity_classifier"
    job_dir = Path(project["models_dir"]) / job_id
    (job_dir / "dataset").mkdir(parents=True)
    torch.manual_seed(7)
    model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
    torch.save({
        "task": "classification", "backbone": "resnet18", "classes": ["OK", "NG"],
        "image_size": [64, 64], "model_state_dict": model.state_dict(),
    }, job_dir / "best_model.pt")
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
    fingerprint = fingerprint_dataset(
        source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "classification", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(job_dir / "dataset"),
    }), encoding="utf-8")
    pipeline = get_single_segmentation_flowchart(job_id=job_id)
    next(node for node in pipeline.nodes if node.data.node_type == "inspection").data.task = "classification"
    saved = client.post("/api/flowchart/pipeline", params={
        "recipe_task": "classification", "source_dataset_path": str(source),
    }, json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text

    exported = client.post("/api/export/flow", json={
        "source_dataset_path": str(source), "recipe_task": "classification",
        "package_name": "verified_classifier", "verification_image_path": str(image),
    })
    assert exported.status_code == 200, exported.text
    parity = exported.json()["parity"]
    assert parity["status"] == "passed"
    assert parity["roi_count"] == 1
    assert parity["final_verdict"] in ("OK", "NG")


def test_fixed_roi_package_runs_same_source_pixel_rectangle_standalone(tmp_path: Path):
    from backend.engine.flow_package import verify_flow_parity

    image = tmp_path / "original.png"
    Image.new("RGB", (96, 96), (120, 45, 20)).save(image)
    job_id = "job_fixed_classifier"
    job_dir = tmp_path / "models" / job_id
    job_dir.mkdir(parents=True)
    checkpoint = job_dir / "best_model.pt"
    torch.manual_seed(13)
    model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
    torch.save({
        "task": "classification", "backbone": "resnet18", "classes": ["OK", "NG"],
        "image_size": [64, 64], "model_state_dict": model.state_dict(),
    }, checkpoint)
    pipeline = get_fixed_roi_flowchart(inspection_task="classification", job_id=job_id)
    next(node for node in pipeline.nodes if node.data.node_type == "fixed_roi").data.params["roi_bbox"] = [8, 16, 72, 80]

    package = Path(build_flow_package(
        pipeline=pipeline, checkpoints={job_id: checkpoint},
        output_base_dir=tmp_path / "exports", package_name="fixed_roi_flow",
    )["package_path"])
    assert json.loads((package / "pipeline.json").read_text(encoding="utf-8"))["nodes"][1]["data"]["params"]["roi_bbox"] == [8, 16, 72, 80]

    parity = verify_flow_parity(
        package_dir=package, pipeline=pipeline, checkpoints={job_id: checkpoint}, image_path=image,
    )
    assert parity["status"] == "passed"
    command = subprocess.run(
        [sys.executable, str(package / "run_flow.py"), "--image", str(image)],
        cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True,
    )
    assert command.returncode == 0, command.stderr
    result = json.loads(command.stdout)
    assert result["inspected_image_size"] == [96, 96]
    assert result["crops"][0]["bbox"] == [8, 16, 72, 80]

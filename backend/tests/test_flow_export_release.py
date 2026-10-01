"""Manual flow export binds explicit verified approvals and records frozen multi-image parity."""
import hashlib
import json
import uuid
from pathlib import Path

import pytest
import torch
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset, routes_export, routes_flowchart, routes_model_deployments
from backend.engine import flow_package
from backend.engine.classification.model import create_classification_model
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.flow_package_runtime import verify_flow_package
from backend.engine.flowchart_engine import get_fixed_roi_flowchart, get_single_segmentation_flowchart
from backend.engine.product_delivery import package_library
from backend.main import create_app

JOB = "job_release_classifier"


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _classifier(path, seed=13):
    torch.manual_seed(seed)
    model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
    torch.save({"task": "classification", "backbone": "resnet18", "classes": ["OK", "NG"],
                "image_size": [64, 64], "model_state_dict": model.state_dict()}, path)
    return path


def _images(folder, count):
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        path = folder / f"img_{index}.png"
        Image.new("RGB", (96, 96), ((37 * index) % 255, 90, 20 + index)).save(path)
        paths.append(path)
    return paths


@pytest.fixture
def package(tmp_path):
    job_dir = tmp_path / "models" / JOB
    job_dir.mkdir(parents=True)
    checkpoint = _classifier(job_dir / "best_model.pt")
    pipeline = get_fixed_roi_flowchart(inspection_task="classification", job_id=JOB)
    built = flow_package.build_flow_package(pipeline=pipeline, checkpoints={JOB: checkpoint},
                                            output_base_dir=tmp_path / "exports", package_name="release_flow")
    return Path(built["package_path"]), pipeline, {JOB: checkpoint}


def _cohort(root, pipeline, checkpoints, images, device="cpu"):
    return flow_package.verify_flow_parity_cohort(package_dir=root, pipeline=pipeline, checkpoints=checkpoints,
                                                  images=[{"path": str(path)} for path in images], device=device)


def test_cohort_parity_runs_each_frozen_image_on_the_explicit_device(tmp_path, package):
    root, pipeline, checkpoints = package
    images = _images(tmp_path / "cohort", 3)
    report = _cohort(root, pipeline, checkpoints, images)
    assert report["contract"] == "flow_parity_v1" and report["status"] == "passed"
    assert report["scope"] == "cohort" and report["limitation"] is None
    assert report["device"] == "cpu" and report["image_count"] == report["completed_count"] == 3
    assert report["manifest_sha256"] == _sha256(root / "manifest.json")
    assert report["graph_sha256"] == _sha256(root / "pipeline.json")
    assert report["checkpoints"] == {JOB: _sha256(checkpoints[JOB])}
    assert report["tolerance"]["defect_score_abs"] == 1e-4
    assert report["cohort_sha256"] == flow_package.parity_cohort_sha256([_sha256(path) for path in images])
    assert [row["image_sha256"] for row in report["images"]] == [_sha256(path) for path in images]
    for row in report["images"]:
        assert row["status"] == "passed" and row["mismatched_fields"] == []
        assert row["reference"] == row["packaged"]
        assert row["packaged"]["final_verdict"] in ("OK", "NG", "REVIEW")
        assert row["packaged"]["branch_path"] and row["packaged"]["rois"][0]["bbox"]
    assert report["mismatched_fields"] == []
    runner = report["packaged_runtime"]
    assert runner["kind"] == "isolated_python_runner" and runner["independent_process"] is True
    assert runner["package_runner_sha256"] == _sha256(root / "run_flow.py")
    assert runner["package_runtime_sha256"] == _sha256(root / "backend/engine/flow_package_runtime.py")
    assert report["reference_runtime"]["kind"] == "in_process_app_engine"


def test_frozen_application_runs_the_package_through_the_dispatcher(tmp_path, package, monkeypatch):
    root, _, _ = package
    monkeypatch.setattr(flow_package.sys, "frozen", True, raising=False)
    item = {"path": str(tmp_path / "x.png"), "image_id": "x"}
    command, runtime = flow_package._packaged_runner_command(root, item, tmp_path / "out.json", "cpu")
    assert command[:2] == [flow_package.sys.executable, flow_package.FROZEN_PACKAGE_RUNNER_FLAG]
    assert command[command.index("--package") + 1] == str(root)
    assert command[command.index("--manifest-sha256") + 1] == _sha256(root / "manifest.json")
    assert command[command.index("--image") + 1] == item["path"] and command[command.index("--image-id") + 1] == "x"
    assert command[command.index("--device") + 1] == "cpu" and command[command.index("--output") + 1] == str(tmp_path / "out.json")
    assert runtime["kind"] == "frozen_package_dispatcher" and runtime["independent_process"] is True
    assert runtime["package_runner_sha256"] == _sha256(root / "run_flow.py")


def test_cohort_identity_ignores_input_order():
    assert flow_package.parity_cohort_sha256(["b" * 64, "a" * 64]) == flow_package.parity_cohort_sha256(["a" * 64, "b" * 64])


@pytest.mark.parametrize("device", ["cuda:7", "gpu", "openvino:CPU"])
def test_unavailable_or_unsupported_target_device_never_falls_back_to_cpu(tmp_path, package, device):
    root, pipeline, checkpoints = package
    with pytest.raises(ValueError):
        _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2), device=device)


@pytest.mark.parametrize("count", [1, 65])
def test_cohort_size_is_bounded(tmp_path, package, count):
    root, pipeline, checkpoints = package
    images = _images(tmp_path / "cohort", 1) * count
    with pytest.raises(ValueError, match="2 to 64|duplicate"):
        _cohort(root, pipeline, checkpoints, images)


def test_duplicate_cohort_images_are_rejected(tmp_path, package):
    root, pipeline, checkpoints = package
    image = _images(tmp_path / "cohort", 1)[0]
    with pytest.raises(ValueError, match="duplicate"):
        _cohort(root, pipeline, checkpoints, [image, image])


def test_one_mismatched_image_fails_the_cohort_and_names_the_field(tmp_path, package, monkeypatch):
    root, pipeline, checkpoints = package
    real = flow_package._run_packaged_image

    def tamper(package_dir, item, device, timeout):
        result = real(package_dir, item, device, timeout)
        if item["index"] == 1:
            result["final_verdict"] = "OK" if result["final_verdict"] != "OK" else "NG"
        return result

    monkeypatch.setattr(flow_package, "_run_packaged_image", tamper)
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 3))
    assert report["status"] == "mismatch"
    assert report["images"][0]["status"] == "passed" and report["images"][2]["status"] == "passed"
    assert report["images"][1]["status"] == "mismatch"
    assert "final_verdict" in report["images"][1]["mismatched_fields"]
    assert "images[1].final_verdict" in report["mismatched_fields"]


def test_input_changed_during_parity_is_a_failure(tmp_path, package, monkeypatch):
    root, pipeline, checkpoints = package
    real = flow_package._run_packaged_image

    def mutate(package_dir, item, device, timeout):
        result = real(package_dir, item, device, timeout)
        Image.new("RGB", (96, 96), (1, 2, 3)).save(item["path"])
        return result

    monkeypatch.setattr(flow_package, "_run_packaged_image", mutate)
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2))
    assert report["status"] == "failed"
    assert report["images"][0]["status"] == "failed" and "changed" in report["images"][0]["error"]


def test_runner_failure_is_recorded_as_a_failed_report(tmp_path, package, monkeypatch):
    root, pipeline, checkpoints = package

    def broken(*args, **kwargs):
        raise ValueError("Packaged flow execution failed: boom")

    monkeypatch.setattr(flow_package, "_run_packaged_image", broken)
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2))
    assert report["status"] == "failed" and "boom" in report["error"]
    assert report["completed_count"] == 0 and report["images"][0]["status"] == "failed"
    assert report["images"][1]["status"] == "not_run"


def test_legacy_single_image_parity_is_explicitly_limited(tmp_path, package):
    root, pipeline, checkpoints = package
    image = _images(tmp_path / "one", 1)[0]
    report = flow_package.verify_flow_parity(package_dir=root, pipeline=pipeline, checkpoints=checkpoints, image_path=image)
    assert report["status"] == "passed" and report["scope"] == "single_image"
    assert "not a cohort" in report["limitation"]
    assert report["image_path"] == str(image.resolve())
    assert report["roi_count"] == 1 and report["final_verdict"] in ("OK", "NG", "REVIEW")


def test_parity_receipt_stays_with_the_package_and_is_bound_to_its_manifest(tmp_path, package, monkeypatch):
    root, pipeline, checkpoints = package
    monkeypatch.setattr(flow_package, "_run_packaged_image", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom")))
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2))
    path = flow_package.write_parity_receipt(root, report)
    assert path == root / "parity_receipt.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["status"] == "failed" and saved["manifest_sha256"] == _sha256(root / "manifest.json")
    verify_flow_package(root)
    assert flow_package.parity_receipt_status(root) == {**flow_package.parity_receipt_status(root),
                                                        "present": True, "matches_manifest": True, "status": "failed"}
    saved["manifest_sha256"] = "0" * 64
    path.write_text(json.dumps(saved), encoding="utf-8")
    assert flow_package.parity_receipt_status(root)["matches_manifest"] is False


# ---------- export route ----------

def _project_flow(tmp_path, monkeypatch, count=3):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    app = create_app(project_dir=str(tmp_path / "workspace_registry"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.get("/api/project/current").json()
    source = tmp_path / "source"
    images = _images(source, count)
    job_dir = Path(project["models_dir"]) / JOB
    (job_dir / "dataset").mkdir(parents=True)
    _classifier(job_dir / "best_model.pt", seed=7)
    (job_dir / "model_meta.json").write_text(json.dumps({"task": "classification"}), encoding="utf-8")
    fingerprint = fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                                      split_manifest=routes_dataset._split_manifest_file(source))
    (job_dir / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "classification", "source_dataset_path": str(source),
        "dataset_fingerprint": fingerprint, "dataset_path": str(job_dir / "dataset")}), encoding="utf-8")
    pipeline = get_single_segmentation_flowchart(job_id=JOB)
    next(node for node in pipeline.nodes if node.data.node_type == "inspection").data.task = "classification"
    saved = client.post("/api/flowchart/pipeline", params={"recipe_task": "classification", "source_dataset_path": str(source)},
                        json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text
    return client, project, source, images, job_dir


def _export(client, source, name, **extra):
    return client.post("/api/export/flow", json={"source_dataset_path": str(source), "recipe_task": "classification",
                                                 "package_name": name, **extra})


def _library_parity(project, package_path):
    rows = package_library({**project, "source_dataset_dir": project.get("source_dataset_dir")})["packages"]
    return next(row["parity"] for row in rows if row["package_path"] == package_path)


def test_export_records_passed_cohort_parity_in_the_library_and_package(tmp_path, monkeypatch):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch)
    response = _export(client, source, "cohort_pass", parity_images=[{"path": str(p)} for p in images], parity_device="cpu")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["parity"]["status"] == "passed" and result["parity"]["scope"] == "cohort"
    assert result["parity"]["image_count"] == 3 and result["parity"]["device"] == "cpu"
    receipt = json.loads((Path(result["package_path"]) / "parity_receipt.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "passed" and receipt["cohort_sha256"] == result["parity"]["cohort_sha256"]


def test_failed_parity_export_keeps_a_failure_receipt_instead_of_not_run(tmp_path, monkeypatch):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch)
    real = flow_package._run_packaged_image

    def tamper(package_dir, item, device, timeout):
        result = real(package_dir, item, device, timeout)
        result["roi_count"] = 99
        return result

    monkeypatch.setattr(flow_package, "_run_packaged_image", tamper)
    response = _export(client, source, "cohort_mismatch", parity_images=[{"path": str(p)} for p in images[:2]],
                       parity_device="cpu")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["parity"]["status"] == "mismatch"
    package_path = detail["package_path"]
    assert json.loads((Path(package_path) / "parity_receipt.json").read_text())["status"] == "mismatch"
    assert _library_parity(project, package_path)["status"] == "mismatch"


def test_legacy_single_image_export_is_marked_limited(tmp_path, monkeypatch):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch, count=1)
    response = _export(client, source, "single", verification_image_path=str(images[0]))
    assert response.status_code == 200, response.text
    assert response.json()["parity"]["scope"] == "single_image"
    assert "not a cohort" in response.json()["parity"]["limitation"]


@pytest.mark.parametrize("extra, message", [
    (lambda imgs: {"parity_images": [{"path": str(p)} for p in imgs]}, "parity_device"),
    (lambda imgs: {"parity_device": "cpu"}, "parity_images"),
    (lambda imgs: {"parity_images": [{"path": str(imgs[0])}], "parity_device": "cpu"}, ""),
    (lambda imgs: {"parity_images": [{"path": str(p)} for p in imgs], "parity_device": "cpu",
                   "verification_image_path": str(imgs[0])}, "verification_image_path"),
    (lambda imgs: {"parity_images": [{"path": str(p)} for p in imgs], "parity_device": "mps",
                   "runtime_config": {"device": "cpu"}}, "device"),
])
def test_cohort_requests_need_an_explicit_matching_target(tmp_path, monkeypatch, extra, message):
    client, _, source, images, _ = _project_flow(tmp_path, monkeypatch, count=2)
    response = _export(client, source, "invalid_request", **extra(images))
    assert response.status_code == 422
    assert message in json.dumps(response.json())


def test_cohort_images_outside_the_source_or_unavailable_devices_create_no_package(tmp_path, monkeypatch):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch, count=2)
    outside = _images(tmp_path / "elsewhere", 2)
    response = _export(client, source, "outside", parity_images=[{"path": str(p)} for p in outside], parity_device="cpu")
    assert response.status_code == 422
    response = _export(client, source, "no_cuda", parity_images=[{"path": str(p)} for p in images], parity_device="cuda:7",
                       runtime_config={"device": "cuda:7"})
    assert response.status_code == 422
    flows = Path(project["project_dir"]) / "exports" / "flows"
    assert not (flows / "outside").exists() and not (flows / "no_cuda").exists()


# ---------- approval selection ----------

def _revision(conn, source, job, checkpoint_sha, *, fingerprint="fp", action="approve", parent=None, task="classification"):
    revision_id = uuid.uuid4().hex
    conn.execute("INSERT INTO revisions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
        revision_id, str(source), task, job, checkpoint_sha, "train-fp", fingerprint, "cmp_" + revision_id,
        "0" * 64, parent, None, action, "reviewer", "reason", "2026-10-01T00:00:00Z"))
    return revision_id


def _approval_fixture(project, source, job_dir, monkeypatch):
    checkpoint_sha = _sha256(job_dir / "best_model.pt")
    with routes_model_deployments._store(project) as conn:
        stale = _revision(conn, source, JOB, checkpoint_sha, fingerprint="older")
        bad = _revision(conn, source, JOB, checkpoint_sha)
        _revision(conn, source, "job_previous_model", "e" * 64, action="rollback", parent=bad)
        good = _revision(conn, source, JOB, checkpoint_sha)
        other = _revision(conn, source, "job_other_model", "f" * 64)
        conn.execute("INSERT INTO active_revisions VALUES (?, ?, ?)", (str(source), "classification", other))
    rows = {}
    with routes_model_deployments._store(project) as conn:
        for row in conn.execute("SELECT * FROM revisions"):
            rows[row["revision_id"]] = dict(row)
    monkeypatch.setattr(routes_model_deployments, "verified_approval_revision",
                        lambda project, revision_id, expected_task=None: rows.get(revision_id))
    monkeypatch.setattr(routes_model_deployments, "_fingerprint", lambda source: "fp")
    return {"stale": stale, "bad": bad, "good": good, "other": other, "count": len(rows)}


def _revision_count(project):
    with routes_model_deployments._store(project) as conn:
        return conn.execute("SELECT COUNT(*) FROM revisions").fetchone()[0]


def test_approval_readback_lists_verified_candidates_for_the_exact_checkpoint_without_creating_any(tmp_path, monkeypatch):
    client, project, source, _, job_dir = _project_flow(tmp_path, monkeypatch, count=1)
    ids = _approval_fixture(project, source, job_dir, monkeypatch)
    response = client.get("/api/export/flow/approval-prerequisites",
                          params={"source_dataset_path": str(source), "recipe_task": "classification"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "selection_required" and body["approval_revision_ids"] == {}
    model = body["models"][0]
    assert model["job_id"] == JOB and model["checkpoint_sha256"] == _sha256(job_dir / "best_model.pt")
    assert [row["revision_id"] for row in model["candidates"]] == [ids["good"]]
    assert model["candidates"][0]["is_active"] is False and model["selected_revision_id"] is None
    assert _revision_count(project) == ids["count"]
    with routes_model_deployments._store(project) as conn:
        conn.execute("UPDATE active_revisions SET revision_id = ?", (ids["good"],))
    body = client.get("/api/export/flow/approval-prerequisites",
                      params={"source_dataset_path": str(source), "recipe_task": "classification"}).json()
    assert body["status"] == "ready" and body["approval_revision_ids"] == {JOB: ids["good"]}


def test_export_accepts_an_explicit_verified_non_active_revision_for_its_checkpoint_only(tmp_path, monkeypatch):
    client, project, source, _, job_dir = _project_flow(tmp_path, monkeypatch, count=1)
    ids = _approval_fixture(project, source, job_dir, monkeypatch)
    for key in ("bad", "stale", "other"):
        rejected = _export(client, source, f"reject_{key}", approval_revision_ids={JOB: ids[key]})
        assert rejected.status_code == 409, key
    accepted = _export(client, source, "release_ok", approval_revision_ids={JOB: ids["good"]})
    assert accepted.status_code == 200, accepted.text
    manifest = json.loads((Path(accepted.json()["package_path"]) / "manifest.json").read_text())
    assert manifest["release"]["approval_revisions"] == [{"revision_id": ids["good"], "job_id": JOB, "task": "classification",
                                                         "checkpoint_sha256": _sha256(job_dir / "best_model.pt")}]
    assert _revision_count(project) == ids["count"]


def test_same_task_models_with_different_checkpoints_are_verified_separately(tmp_path, monkeypatch):
    client, project, source, _, job_dir = _project_flow(tmp_path, monkeypatch, count=1)
    second = tmp_path / "second.pt"
    second.write_bytes(b"second checkpoint")
    with routes_model_deployments._store(project) as conn:
        first_rev = _revision(conn, source, JOB, _sha256(job_dir / "best_model.pt"))
        second_rev = _revision(conn, source, "job_second", _sha256(second))
        conn.execute("INSERT INTO active_revisions VALUES (?, ?, ?)", (str(source), "classification", second_rev))
        rows = {row["revision_id"]: dict(row) for row in conn.execute("SELECT * FROM revisions")}
    monkeypatch.setattr(routes_model_deployments, "verified_approval_revision", lambda p, r, expected_task=None: rows.get(r))
    monkeypatch.setattr(routes_model_deployments, "_fingerprint", lambda source: "fp")
    first = routes_export._release_candidates(project, source, "classification", JOB, job_dir / "best_model.pt")
    other = routes_export._release_candidates(project, source, "classification", "job_second", second)
    assert [row["revision_id"] for row in first] == [first_rev] and first[0]["is_active"] is False
    assert [row["revision_id"] for row in other] == [second_rev] and other[0]["is_active"] is True
    assert routes_export._selected_release(project, source, "classification", JOB, job_dir / "best_model.pt", first_rev)["revision_id"] == first_rev
    assert routes_export._selected_release(project, source, "classification", "job_second", second, second_rev)["revision_id"] == second_rev
    assert routes_export._selected_release(project, source, "classification", JOB, job_dir / "best_model.pt", second_rev) is None


# ---------- automatic deployment ----------

def _operations(tmp_path, monkeypatch, report_status="passed"):
    import threading
    from backend.tests.test_operations_deployment_flow import fixture
    from backend.engine import model_operations
    from backend.engine.managed_service import ManagedService
    project, source, policy, approval, request, saved = fixture(tmp_path)
    calls = []

    def capture(**kwargs):
        calls.append(kwargs)
        return {"contract": "flow_parity_v1", "status": report_status, "scope": kwargs["scope"], "device": kwargs["device"],
                "image_count": len(kwargs["images"]), "manifest_sha256": "m" * 64}

    monkeypatch.setattr(flow_package, "verify_flow_parity_cohort", capture)
    monkeypatch.setattr(ManagedService, "apply", lambda self, *args, **kwargs: {"release": {"manifest_sha256": "m" * 64}})

    def run(policy_override=None):
        with model_operations.project_scope(project):
            return model_operations._deploy_candidate(project, {**policy, **(policy_override or {})}, "job_candidate",
                                                      approval, threading.Event())
    return policy, calls, run


def test_automatic_deployment_checks_the_whole_frozen_holdout_on_its_target_device(tmp_path, monkeypatch):
    policy, calls, run = _operations(tmp_path, monkeypatch)
    result = run()
    [call] = calls
    assert [row["path"] for row in call["images"]] == sorted(policy["holdout"])[:16]
    assert call["device"] == policy["inference_device"] and call["scope"] == "cohort"
    assert result["parity"]["scope"] == "cohort"
    assert json.loads((Path(result["package_path"]) / "parity_receipt.json").read_text())["status"] == "passed"


def test_automatic_deployment_refuses_mismatched_or_single_image_parity(tmp_path, monkeypatch):
    policy, calls, run = _operations(tmp_path, monkeypatch, report_status="mismatch")
    with pytest.raises(ValueError, match="differ"):
        run()
    first = sorted(policy["holdout"])[0]
    with pytest.raises(ValueError, match="two held-out"):
        run({"holdout": {first: policy["holdout"][first]}})


# ---------- release policy and frozen worker ----------

def test_approved_release_policy_is_issued_only_with_a_passed_cohort_receipt(tmp_path, monkeypatch):
    client, project, source, images, job_dir = _project_flow(tmp_path, monkeypatch, count=3)
    ids = _approval_fixture(project, source, job_dir, monkeypatch)
    raw = _export(client, source, "approved_raw", approval_revision_ids={JOB: ids["good"]})
    assert raw.status_code == 200, raw.text
    assert raw.json()["release_policy"] is None and raw.json()["release_policy_withheld"] == "cohort_parity_required"
    single = _export(client, source, "approved_single", approval_revision_ids={JOB: ids["good"]},
                     verification_image_path=str(images[0]))
    assert single.status_code == 200, single.text
    assert single.json()["release_policy"] is None and single.json()["release_policy_withheld"] == "cohort_parity_required"
    cohort = _export(client, source, "approved_cohort", approval_revision_ids={JOB: ids["good"]},
                     parity_images=[{"path": str(p)} for p in images], parity_device="cpu")
    assert cohort.status_code == 200, cohort.text
    body = cohort.json()
    receipt = Path(body["package_path"]) / "parity_receipt.json"
    policy = body["release_policy"]
    assert policy["device"] == "cpu" and policy["parity_receipt_sha256"] == _sha256(receipt)
    assert policy["manifest_sha256"] == _sha256(Path(body["package_path"]) / "manifest.json")
    assert "release_policy_withheld" not in body
    from backend.engine.runtime_release_evidence import verify_release_evidence
    evidence = verify_release_evidence(Path(body["package_path"]), "cpu", expected_receipt_sha256=policy["parity_receipt_sha256"])
    assert evidence["image_count"] == 3


def test_frozen_isolated_inference_runs_the_vendored_worker(tmp_path, package, monkeypatch):
    from backend.engine import flow_package_runtime, runtime_deadline
    root, _, _ = package
    image = _images(tmp_path / "one", 1)[0]
    seen = {}

    def fake(command, *, deadline_ms, env=None, cwd=None, cancel_event=None):
        seen["command"] = command
        target = command[command.index("--output") + 1] if "--output" in command else command[-1]
        Path(target).write_text(json.dumps({"final_verdict": "OK"}), encoding="utf-8")
        return {"status": "completed", "returncode": 0, "pid": 1, "elapsed_ms": 1.0, "stdout": "", "stderr": ""}

    monkeypatch.setattr(runtime_deadline, "execute_owned_process", fake)
    options = {"device": "cpu", "cpu_threads": 1, "deadline_ms": None}
    monkeypatch.setattr(flow_package_runtime.sys, "frozen", True, raising=False)
    result = flow_package_runtime._run_isolated(root, image, "img", options)
    command = seen["command"]
    assert command[:2] == [flow_package_runtime.sys.executable, "--flow-package-worker"]
    assert command[command.index("--package") + 1] == str(root)
    assert command[command.index("--manifest-sha256") + 1] == _sha256(root / "manifest.json")
    assert Path(command[command.index("--request") + 1]).name == "request.json"
    assert result["final_verdict"] == "OK" and result["runtime_execution"]["isolated_process"] is True
    monkeypatch.setattr(flow_package_runtime.sys, "frozen", False)
    flow_package_runtime._run_isolated(root, image, "img", options)
    assert seen["command"][1] == "-c"


# ---------- package identity before and after parity ----------

def _resave(checkpoint):
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    torch.save({**payload, "epoch": 99}, checkpoint)


def test_parity_refuses_a_source_checkpoint_that_differs_from_the_packaged_one(tmp_path, package):
    root, pipeline, checkpoints = package
    _resave(checkpoints[JOB])
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2))
    assert report["status"] == "failed" and "checkpoint" in report["error"].lower()
    assert report["completed_count"] == 0 and {row["status"] for row in report["images"]} == {"not_run"}


def test_parity_refuses_a_graph_that_differs_from_the_packaged_graph(tmp_path, package):
    root, pipeline, checkpoints = package
    changed = pipeline.model_copy(deep=True)
    next(node for node in changed.nodes if node.data.node_type == "fixed_roi").data.params["roi_bbox"] = [0, 0, 40, 40]
    report = _cohort(root, changed, checkpoints, _images(tmp_path / "cohort", 2))
    assert report["status"] == "failed" and "graph" in report["error"].lower()


@pytest.mark.parametrize("target", ["checkpoint", "runtime"])
def test_bytes_changed_during_parity_fail_the_report(tmp_path, package, monkeypatch, target):
    root, pipeline, checkpoints = package
    real = flow_package._run_packaged_image

    def mutate(package_dir, item, device, timeout):
        result = real(package_dir, item, device, timeout)
        if item["index"] == 0:
            if target == "checkpoint":
                _resave(checkpoints[JOB])
            else:
                runtime = root / "backend/engine/flow_package_runtime.py"
                runtime.write_text(runtime.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
        return result

    monkeypatch.setattr(flow_package, "_run_packaged_image", mutate)
    report = _cohort(root, pipeline, checkpoints, _images(tmp_path / "cohort", 2))
    assert report["status"] == "failed" and "changed during" in report["error"]


def test_package_changed_during_export_parity_keeps_a_failed_library_record(tmp_path, monkeypatch):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch, count=2)
    real = flow_package._run_packaged_image

    def corrupt(package_dir, item, device, timeout):
        result = real(package_dir, item, device, timeout)
        graph = Path(package_dir) / "pipeline.json"
        graph.write_text(graph.read_text(encoding="utf-8").replace("{", "{ ", 1), encoding="utf-8")
        return result

    monkeypatch.setattr(flow_package, "_run_packaged_image", corrupt)
    response = _export(client, source, "mutated", parity_images=[{"path": str(p)} for p in images], parity_device="cpu")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["parity"]["status"] == "failed"
    assert _library_parity(project, detail["package_path"])["status"] == "failed"

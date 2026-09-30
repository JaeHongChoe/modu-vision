"""CPU Edge exports validate the actual target before installing or executing."""

import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest
from PIL import Image

from backend.engine.flow_package import build_flow_package
from backend.engine.flowchart_engine import get_single_detection_flowchart


def edge_module():
    try:
        return importlib.import_module("backend.engine.edge_runtime")
    except ModuleNotFoundError:
        pytest.fail("CPU Edge runtime is missing")


def host_target():
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}[platform.system()]
    machine = platform.machine().lower()
    arch = "x86_64" if machine in ("x86_64", "amd64") else "arm64"
    return system, arch


def export_edge(tmp_path, *, target=None):
    checkpoint = tmp_path / "job_edge_detector" / "best_model.pt"
    checkpoint.parent.mkdir(exist_ok=True)
    checkpoint.write_bytes(b"checksum-only detector fixture; never execute this model")
    os_name, arch = target or host_target()
    return build_flow_package(
        pipeline=get_single_detection_flowchart(job_id="job_edge_detector"),
        checkpoints={"job_edge_detector": checkpoint}, output_base_dir=tmp_path / "exports",
        package_name="cpu_edge_flow", deployment_profile="edge_cpu", target_os=os_name,
        target_arch=arch,
    )


def test_edge_profile_has_executable_bootstrap_and_verified_target(tmp_path):
    edge = edge_module()
    result = export_edge(tmp_path)
    package = Path(result["package_path"])
    profile = json.loads((package / "edge_deployment.json").read_text())
    manifest = json.loads((package / "manifest.json").read_text())
    assert profile["profile"] == "edge_cpu"
    assert profile["device"] == "cpu"
    assert profile["target"] == {"os": host_target()[0], "architecture": host_target()[1]}
    assert manifest["deployment"] == profile
    assert result["deployment"] == profile
    assert (package / "edge.py").stat().st_mode & 0o111
    assert edge.preflight_edge_package(package, check_dependencies=False)["status"] == "verified"
    # The bootstrap must report useful target/checksum errors without importing torch.
    check = subprocess.run([sys.executable, "-S", str(package / "edge.py"), "preflight", "--skip-dependencies"],
                           cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    assert json.loads(check.stdout)["device"] == "cpu"


@pytest.mark.parametrize("os_name,arch", [("android", "arm64"), ("linux", "armv7"), ("windows", "arm64"), ("macos", "x86_64")])
def test_edge_export_refuses_unsupported_targets_before_writing(tmp_path, os_name, arch):
    edge_module()
    with pytest.raises(ValueError, match="Unsupported CPU Edge target"):
        export_edge(tmp_path, target=(os_name, arch))
    assert not (tmp_path / "exports").exists()


def test_edge_preflight_rejects_incompatible_host_before_environment_creation(tmp_path):
    edge_module()
    result = export_edge(tmp_path, target=("linux", "x86_64") if host_target() != ("linux", "x86_64") else ("macos", "arm64"))
    package = Path(result["package_path"])
    env_dir = tmp_path / "must_not_exist"
    check = subprocess.run([sys.executable, "-S", str(package / "edge.py"), "install", "--venv", str(env_dir)],
                           cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True)
    assert check.returncode == 2
    assert "target" in check.stderr.lower() and "host" in check.stderr.lower()
    assert not env_dir.exists()


@pytest.mark.parametrize("relative", ["pipeline.json", "models/job_edge_detector/best_model.pt", "requirements.txt", "edge_deployment.json"])
def test_edge_bootstrap_rejects_tampering_before_any_dependency_import(tmp_path, relative):
    edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    with (package / relative).open("ab") as target:
        target.write(b"tampered")
    checked = subprocess.run([sys.executable, "-S", str(package / "edge.py"), "preflight", "--skip-dependencies"],
                             cwd=tmp_path, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True)
    assert checked.returncode == 2
    assert "checksum" in checked.stderr.lower()


def test_edge_dependency_checks_reject_missing_or_old_runtime(tmp_path, monkeypatch):
    edge = edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    real_version = edge.metadata.version
    def old_torch(name):
        return "2.3.0" if name == "torch" else real_version(name)
    monkeypatch.setattr(edge.metadata, "version", old_torch)
    with pytest.raises(ValueError, match="torch.*2.4"):
        edge.preflight_edge_package(package)


def test_direct_edge_runner_and_service_cannot_bypass_dependency_gate(tmp_path, monkeypatch):
    edge = edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    real_version = edge.metadata.version
    monkeypatch.setattr(edge.metadata, "version", lambda name: "2.3.0" if name == "torch" else real_version(name))
    image = tmp_path / "input.png"
    Image.new("RGB", (32, 32), "gray").save(image)
    from backend.engine.flow_package_runtime import run_flow_package
    from backend.engine.inspection_service import create_service_app
    for launch in (
        lambda: edge.enforce_edge_device(package, "cpu"),
        lambda: run_flow_package(package, image, device="cpu"),
        lambda: create_service_app(package, tmp_path / "state", token="fixture", device="cpu", auto_worker=False),
    ):
        with pytest.raises(ValueError, match="torch.*2.4"):
            launch()
    assert not (tmp_path / "state").exists()


def test_edge_profile_rejects_python_outside_declared_range(tmp_path, monkeypatch):
    edge = edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    monkeypatch.setattr(edge.sys, "version_info", (3, 9, 20))
    with pytest.raises(ValueError, match="Python"):
        edge.preflight_edge_package(package, check_dependencies=False)


def test_edge_flow_and_service_refuse_gpu_before_loading_or_state_creation(tmp_path):
    edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    image = tmp_path / "input.png"
    Image.new("RGB", (32, 32), "gray").save(image)
    from backend.engine.flow_package_runtime import run_flow_package
    from backend.engine.inspection_service import create_service_app
    with pytest.raises(ValueError, match="CPU Edge.*cpu"):
        run_flow_package(package, image, device="cuda")
    with pytest.raises(ValueError, match="CPU Edge.*cpu"):
        create_service_app(package, tmp_path / "state", token="test", device="mps", auto_worker=False)
    assert not (tmp_path / "state").exists()


def test_generic_flow_export_retains_explicit_gpu_option(tmp_path, monkeypatch):
    # The Edge CPU gate must only apply to Edge packages.
    checkpoint = tmp_path / "job_detector" / "best_model.pt"
    checkpoint.parent.mkdir()
    checkpoint.write_bytes(b"checksum-only detector fixture")
    package = Path(build_flow_package(
        pipeline=get_single_detection_flowchart(job_id="job_detector"), checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports", package_name="standard_flow",
    )["package_path"])
    assert "deployment" not in json.loads((package / "manifest.json").read_text())
    assert not (package / "edge.py").exists()


def test_edge_target_api_and_export_request_require_explicit_valid_target(tmp_path):
    from backend.main import create_app
    from fastapi.testclient import TestClient
    app = create_app(str(tmp_path / "registry"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    targets = client.get("/api/export/edge-targets")
    assert targets.status_code == 200, targets.text
    assert targets.json()["host"] == {"os": host_target()[0], "architecture": host_target()[1]}
    assert targets.json()["supported"]["windows"] == ["x86_64"]
    from backend.api.routes_export import ExportFlowRequest
    request = {"source_dataset_path": str(tmp_path), "recipe_task": "detection", "package_name": "line"}
    valid = ExportFlowRequest.model_validate({**request, "deployment_profile": "edge_cpu",
                                            "target_os": "linux", "target_arch": "aarch64"})
    assert valid.target_arch == "arm64"
    with pytest.raises(ValueError, match="target"):
        ExportFlowRequest.model_validate({**request, "deployment_profile": "edge_cpu"})
    with pytest.raises(ValueError, match="edge_cpu"):
        ExportFlowRequest.model_validate({**request, "target_os": "linux", "target_arch": "x86_64"})


def test_edge_export_route_packages_saved_flow_with_declared_profile(tmp_path, monkeypatch):
    import torch
    from fastapi.testclient import TestClient
    from backend.api import routes_dataset, routes_flowchart
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.main import create_app
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    app = create_app(str(tmp_path / "registry"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    project = client.get("/api/project/current").json()
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (32, 32), "gray").save(source / "source.png")
    job = "job_edge_saved"
    directory = Path(project["models_dir"]) / job
    (directory / "dataset").mkdir(parents=True)
    torch.save({"task": "detection", "model_state_dict": {}}, directory / "best_model.pt")
    (directory / "job_receipt.json").write_text(json.dumps({
        "status": "completed", "task": "detection", "source_dataset_path": str(source),
        "dataset_path": str(directory / "dataset"),
        "dataset_fingerprint": fingerprint_dataset(source, studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
                                                   split_manifest=routes_dataset._split_manifest_file(source)),
    }))
    pipeline = get_single_detection_flowchart(job_id=job)
    saved = client.post("/api/flowchart/pipeline", params={"recipe_task": "detection", "source_dataset_path": str(source)},
                        json=pipeline.model_dump())
    assert saved.status_code == 200, saved.text
    result = client.post("/api/export/flow", json={
        "source_dataset_path": str(source), "recipe_task": "detection", "package_name": "line",
        "deployment_profile": "edge_cpu", "target_os": host_target()[0], "target_arch": host_target()[1],
    })
    assert result.status_code == 200, result.text
    assert result.json()["deployment"]["device"] == "cpu"
    assert result.json()["parity"]["status"] == "not_run"
    assert (Path(result.json()["package_path"]) / "edge.py").is_file()


@pytest.mark.parametrize("task,backbone,dependency", [
    ("classification", "dinov3_vits16", "timm"),
    ("detection", "yolo26n", "ultralytics"),
])
def test_flow_package_declares_adapter_dependencies_from_checkpoint(tmp_path, monkeypatch, task, backbone, dependency):
    import torch
    edge = edge_module()
    job = "job_adapter"
    checkpoint = tmp_path / job / "best_model.pt"
    checkpoint.parent.mkdir()
    torch.save({"task": task, "backbone": backbone, "model_state_dict": {}}, checkpoint)
    pipeline = get_single_detection_flowchart(job_id=job)
    node = next(node for node in pipeline.nodes if node.data.model_job_id == job)
    if task == "classification":
        node.data.node_type = "inspection"
    node.data.task = task
    os_name, arch = host_target()
    package = Path(build_flow_package(
        pipeline=pipeline, checkpoints={job: checkpoint}, output_base_dir=tmp_path / "exports", package_name="adapter",
        deployment_profile="edge_cpu", target_os=os_name, target_arch=arch,
    )["package_path"])
    real_version = edge.metadata.version
    def missing_adapter(name):
        if name == dependency:
            raise edge.metadata.PackageNotFoundError(name)
        return real_version(name)
    monkeypatch.setattr(edge.metadata, "version", missing_adapter)
    with pytest.raises(ValueError, match=f"{dependency}.*missing"):
        edge.preflight_edge_package(package)


def test_full_preflight_rejects_manifest_model_links_that_disagree_with_saved_graph(tmp_path, monkeypatch):
    edge = edge_module()
    package = Path(export_edge(tmp_path)["package_path"])
    manifest = json.loads((package / "manifest.json").read_text())
    manifest["models"][0]["task"] = "classification"
    (package / "manifest.json").write_text(json.dumps(manifest))
    # Simulate the two outdated host distribution receipts, keeping the actual
    # imports/CPU tensor/packaged graph verification active.
    real_version = edge.metadata.version
    monkeypatch.setattr(edge.metadata, "version", lambda name: {
        "psutil": "6.0.0", "fastapi": "0.115.0",
    }.get(name, real_version(name)))
    with pytest.raises(ValueError, match="model jobs.*saved graph"):
        edge.preflight_edge_package(package)


def test_service_runtime_cannot_apply_edge_release_with_a_gpu_override(tmp_path, monkeypatch):
    edge = edge_module()
    # This test isolates device enforcement; dependency rejection has its own tests.
    monkeypatch.setattr(edge, '_check_dependencies', lambda _profile: {})
    package = Path(export_edge(tmp_path)["package_path"])
    policy = tmp_path / "exports" / "trusted_policy.json"
    policy.write_text("{}")
    from backend.engine.service_runtime import ServiceRuntime
    runtime = ServiceRuntime(package, tmp_path / "state", "cpu", None, tmp_path / "exports", lambda *_args: None)
    with pytest.raises(ValueError, match="CPU Edge.*cpu"):
        runtime.apply(package, policy, "cuda", None)
    assert runtime.read()["device"] == "cpu"

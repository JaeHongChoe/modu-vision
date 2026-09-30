"""Durable remote worker results must be attributable and hash checked."""

import base64
import hashlib
import json
import shutil
import tarfile
import threading

import pytest
import torch
from PIL import Image

from backend.engine.classification import create_classification_model
from backend.engine.flowchart_engine import get_default_flowchart, get_single_segmentation_flowchart
from backend.engine.segmentation import build_segmentation_model
from backend.remote.snapshot import build_snapshot, extract_snapshot
from backend.remote.worker import main, run_benchmark, run_evaluate, run_export, run_flowchart, run_infer, run_train


def _spec(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "sample.png").write_bytes(b"sample")
    snapshot = build_snapshot(source, tmp_path / "snapshot", threading.Event())
    run = tmp_path / "run"
    run.mkdir()
    (run / "snapshot.tar.gz").write_bytes(snapshot.archive_path.read_bytes())
    spec = run / "spec.json"
    spec.write_text(json.dumps({
        "protocol_version": 1,
        "job_id": "job_test_123",
        "operation": "train",
        "task": "classification",
        "preset": "fast",
        "config_overrides": {"epochs": 2},
        "device": "cpu",
        "snapshot_archive": "snapshot.tar.gz",
        "input_manifest_sha256": snapshot.manifest_sha256,
    }), encoding="utf-8")
    return run, spec, snapshot


class StubTrainer:
    def __init__(self, *, task, dataset_path, output_dir, preset, device, callback, config_overrides):
        self.dataset_path = dataset_path
        self.output_dir = output_dir
        self.callback = callback
        self.config_overrides = config_overrides
        self.aborted = threading.Event()

    def abort(self):
        self.aborted.set()

    def train(self, job_id):
        assert self.dataset_path.joinpath("sample.png").read_bytes() == b"sample"
        self.callback.on_training_start({"job_id": job_id, "epochs": 2, "device": "cpu"})
        self.callback.on_step_end(step=1, total_steps=4, current_loss=0.8, epoch=0)
        self.callback.on_epoch_end(epoch=0, total_epochs=2, train_loss=0.8, val_loss=0.6, lr=0.01,
                                   metrics={"val_loss": 0.6})
        self.output_dir.joinpath("best_model.pt").write_bytes(b"checkpoint")
        self.output_dir.joinpath("model_meta.json").write_text('{"task":"classification"}', encoding="utf-8")
        self.callback.on_training_completed(job_id, 0.1, 0.6, str(self.output_dir / "best_model.pt"))
        return {"status": "completed", "best_metric": 0.6, "model_path": str(self.output_dir / "best_model.pt")}


def test_train_writes_atomic_status_and_checkpoint_manifest(tmp_path):
    run, spec, snapshot = _spec(tmp_path)

    result = run_train(spec, trainer_factory=StubTrainer)

    status = json.loads((run / "status.json").read_text())
    artifacts = json.loads((run / "artifacts.json").read_text())
    assert result["status"] == status["status"] == "completed"
    assert (status["job_id"], status["operation"], status["protocol_version"]) == ("job_test_123", "train", 1)
    assert (status["current_epoch"], status["total_epochs"], status["train_loss"], status["val_loss"]) == (0, 2, 0.8, 0.6)
    assert artifacts["input_manifest_sha256"] == snapshot.manifest_sha256
    assert {(item["path"], item["sha256"]) for item in artifacts["artifacts"]} == {
        ("outputs/best_model.pt", hashlib.sha256(b"checkpoint").hexdigest()),
        ("outputs/model_meta.json", hashlib.sha256(b'{"task":"classification"}').hexdigest()),
    }
    assert not list(run.glob("*.tmp"))


def test_cli_train_accepts_spec_path_with_stubbed_trainer(tmp_path):
    run, spec, _ = _spec(tmp_path)
    assert main(["train", "--spec", str(spec)], trainer_factory=StubTrainer) == 0
    assert json.loads((run / "status.json").read_text())["status"] == "completed"


def test_repeated_worker_invocation_does_not_train_same_job_twice(tmp_path):
    run, spec, _ = _spec(tmp_path)
    assert run_train(spec, trainer_factory=StubTrainer)["status"] == "completed"

    def duplicate_trainer(**kwargs):
        pytest.fail("A completed job was trained again")

    assert run_train(spec, trainer_factory=duplicate_trainer)["status"] == "completed"
    assert json.loads((run / "status.json").read_text())["status"] == "completed"


def test_repeated_worker_invocation_rejects_changed_spec(tmp_path):
    run, spec, _ = _spec(tmp_path)
    assert run_train(spec, trainer_factory=StubTrainer)["status"] == "completed"
    request = json.loads(spec.read_text())
    request["config_overrides"]["epochs"] = 3
    spec.write_text(json.dumps(request), encoding="utf-8")

    assert run_train(spec, trainer_factory=StubTrainer)["status"] == "failed"
    assert json.loads((run / "status.json").read_text())["status"] == "completed"


def test_cancel_sentinel_aborts_running_trainer_and_writes_terminal_receipt(tmp_path):
    run, spec, _ = _spec(tmp_path)
    entered = threading.Event()
    trainers = []

    class BlockingTrainer(StubTrainer):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            trainers.append(self)

        def train(self, job_id):
            self.callback.on_training_start({"job_id": job_id, "epochs": 2, "device": "cpu"})
            entered.set()
            assert self.aborted.wait(timeout=3)
            self.callback.on_training_aborted(0, "cancelled")
            return {"status": "aborted", "epoch": 0}

    worker = threading.Thread(target=run_train, args=(spec,), kwargs={"trainer_factory": BlockingTrainer})
    worker.start()
    try:
        assert entered.wait(timeout=3)
        (run / "cancel").touch()
        worker.join(timeout=3)
        assert not worker.is_alive()
    finally:
        (run / "cancel").touch()
        worker.join(timeout=3)
    assert trainers[0].aborted.is_set()
    assert json.loads((run / "status.json").read_text())["status"] == "aborted"
    assert not (run / "artifacts.json").exists()


def test_cancel_before_training_skips_trainer(tmp_path):
    run, spec, _ = _spec(tmp_path)
    (run / "cancel").touch()

    def fail_if_called(**kwargs):
        pytest.fail("Cancelled worker constructed a trainer")

    result = run_train(spec, trainer_factory=fail_if_called)
    assert result["status"] == "aborted"
    assert json.loads((run / "status.json").read_text())["status"] == "aborted"


def test_corrupt_snapshot_fails_before_training_and_reports_error(tmp_path):
    run, spec, _ = _spec(tmp_path)
    (run / "snapshot.tar.gz").write_bytes(b"invalid archive")

    def fail_if_called(**kwargs):
        pytest.fail("Unverified snapshot reached trainer")

    result = run_train(spec, trainer_factory=fail_if_called)
    assert result["status"] == "failed"
    assert json.loads((run / "status.json").read_text())["status"] == "failed"
    assert not (run / "artifacts.json").exists()


def test_failure_status_does_not_expose_remote_absolute_path(tmp_path):
    run, spec, _ = _spec(tmp_path)
    (run / "snapshot.tar.gz").unlink()

    result = run_train(spec, trainer_factory=StubTrainer)
    assert result["status"] == "failed"
    assert str(tmp_path) not in result["error"]
    assert str(tmp_path) not in (run / "status.json").read_text()
    assert "FileNotFoundError" in (run / "worker_error.log").read_text()
    assert (run / "worker_error.log").stat().st_mode & 0o777 == 0o600


def test_missing_checkpoint_cannot_be_completed(tmp_path):
    run, spec, _ = _spec(tmp_path)

    class MissingCheckpoint(StubTrainer):
        def train(self, job_id):
            return {"status": "completed"}

    result = run_train(spec, trainer_factory=MissingCheckpoint)
    assert result["status"] == "failed"
    assert json.loads((run / "status.json").read_text())["status"] == "failed"
    assert not (run / "artifacts.json").exists()


def test_hardware_telemetry_keeps_exact_requested_device(tmp_path):
    run, spec, _ = _spec(tmp_path)
    request = json.loads(spec.read_text())
    request["device"] = "cuda:0"
    spec.write_text(json.dumps(request), encoding="utf-8")

    class HardwareTrainer(StubTrainer):
        def train(self, job_id):
            self.callback.on_training_start({"job_id": job_id, "epochs": 2, "device": "cuda:0"})
            self.callback.on_hardware_stats({"device_type": "cuda", "gpu_name": "L40S"})
            self.output_dir.joinpath("best_model.pt").write_bytes(b"checkpoint")
            self.output_dir.joinpath("model_meta.json").write_text('{"task":"classification"}')
            return {"status": "completed", "best_metric": 0.5}

    assert run_train(spec, trainer_factory=HardwareTrainer)["status"] == "completed"
    status = json.loads((run / "status.json").read_text())
    assert status["device"] == "cuda:0"
    assert status["gpu_name"] == "L40S"


def test_snapshot_archive_path_cannot_escape_run(tmp_path):
    run, spec, _ = _spec(tmp_path)
    raw = json.loads(spec.read_text())
    raw["snapshot_archive"] = "../snapshot/snapshot.tar.gz"
    spec.write_text(json.dumps(raw), encoding="utf-8")

    result = run_train(spec, trainer_factory=StubTrainer)
    assert result["status"] == "failed"
    assert "snapshot_archive" in json.loads((run / "status.json").read_text())["error"]


def _evaluation_run(tmp_path, *, real_checkpoint=False):
    dataset = tmp_path / "dataset"
    for split in ("train", "val"):
        for label, color in (("NG", (220, 30, 30)), ("OK", (30, 220, 30))):
            folder = dataset / split / label
            folder.mkdir(parents=True)
            Image.new("RGB", (64, 64), color).save(folder / f"{label.lower()}.png")
    snapshot = build_snapshot(dataset, tmp_path / "snapshot", threading.Event())
    job_id = "job_eval_123"
    runs = tmp_path / "runs"
    train_run = runs / job_id
    train_run.mkdir(parents=True)
    extract_snapshot(snapshot.archive_path, train_run / "input", snapshot.manifest_sha256)
    outputs = train_run / "outputs"
    outputs.mkdir()
    if real_checkpoint:
        model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
        torch.save({"model_state_dict": model.state_dict(), "task": "classification", "classes": ["NG", "OK"]},
                   outputs / "best_model.pt")
    else:
        (outputs / "best_model.pt").write_bytes(b"checkpoint placeholder")
    (outputs / "model_meta.json").write_text(json.dumps({
        "task": "classification", "classes": ["NG", "OK"], "backbone": "resnet18", "image_size": [64, 64],
    }), encoding="utf-8")
    (train_run / "status.json").write_text(json.dumps({
        "protocol_version": 1, "job_id": job_id, "operation": "train", "status": "completed",
    }), encoding="utf-8")
    (train_run / "artifacts.json").write_text(json.dumps({
        "protocol_version": 1, "job_id": job_id, "operation": "train",
        "input_manifest_sha256": snapshot.manifest_sha256,
        "artifacts": [{
            "path": f"outputs/{filename}",
            "size": (outputs / filename).stat().st_size,
            "sha256": hashlib.sha256((outputs / filename).read_bytes()).hexdigest(),
        } for filename in ("best_model.pt", "model_meta.json")],
    }), encoding="utf-8")
    op_run = runs / "op_eval_123"
    op_run.mkdir()
    spec = op_run / "spec.json"
    spec.write_text(json.dumps({
        "protocol_version": 1, "operation": "evaluate", "job_id": job_id,
        "task": "classification", "device": "cpu", "input_manifest_sha256": snapshot.manifest_sha256,
    }), encoding="utf-8")
    return train_run, op_run, spec


def test_evaluate_actual_model_and_snapshot_emits_portable_source_mapping(tmp_path):
    _, op_run, spec = _evaluation_run(tmp_path, real_checkpoint=True)

    result = run_evaluate(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "eval_results.json").read_text())
    assert payload["job_id"] == "job_eval_123"
    assert payload["task"] == "classification"
    assert len(payload["test_predictions"]) == 2
    assert all(row["file_path"].startswith("input/data/val/") for row in payload["test_predictions"])
    assert all("thumbnail_url" not in row for row in payload["test_predictions"])
    assert str(tmp_path) not in json.dumps(payload)
    assert all(path.startswith("input/data/val/") for samples in payload["confusion_matrix"]["cell_samples"].values()
               for path in samples)
    manifest = json.loads((op_run / "artifacts.json").read_text())
    assert manifest["operation"] == "evaluate"
    assert manifest["artifacts"] == [{
        "path": "outputs/eval_results.json",
        "size": (op_run / "outputs" / "eval_results.json").stat().st_size,
        "sha256": hashlib.sha256((op_run / "outputs" / "eval_results.json").read_bytes()).hexdigest(),
    }]


def test_evaluate_rejects_training_run_that_is_not_completed(tmp_path):
    train_run, op_run, spec = _evaluation_run(tmp_path)
    status = json.loads((train_run / "status.json").read_text())
    status["status"] = "running"
    (train_run / "status.json").write_text(json.dumps(status))

    result = run_evaluate(spec)
    assert result["status"] == "failed"
    assert not (op_run / "outputs" / "eval_results.json").exists()


def test_evaluate_rejects_modified_checkpoint_and_dataset(tmp_path):
    train_run, op_run, spec = _evaluation_run(tmp_path)
    (train_run / "outputs" / "best_model.pt").write_bytes(b"altered")
    assert run_evaluate(spec)["status"] == "failed"
    assert not (op_run / "outputs" / "eval_results.json").exists()

    # A new operation run can detect a changed dataset after checkpoint repair.
    train_run2, op_run2, spec2 = _evaluation_run(tmp_path / "second")
    (train_run2 / "input" / "data" / "val" / "OK" / "ok.png").write_bytes(b"altered")
    assert run_evaluate(spec2)["status"] == "failed"
    assert not (op_run2 / "outputs" / "eval_results.json").exists()


def _infer_spec(tmp_path, *, real_checkpoint=False):
    train_run, op_run, spec = _evaluation_run(tmp_path, real_checkpoint=real_checkpoint)
    selected = op_run / "inputs" / "selected.png"
    selected.parent.mkdir()
    Image.new("RGB", (64, 64), (25, 180, 25)).save(selected)
    request = json.loads(spec.read_text())
    request.update({
        "operation": "infer", "image_path": "inputs/selected.png",
        "image_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
        "threshold": 0.4, "image_id": "selected",
    })
    spec.write_text(json.dumps(request), encoding="utf-8")
    return train_run, op_run, spec, selected


def test_infer_actual_selected_image_writes_overlay_and_portable_result(tmp_path):
    _, op_run, spec, selected = _infer_spec(tmp_path, real_checkpoint=True)

    result = run_infer(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "result.json").read_text())
    assert payload["image_path"] == "inputs/selected.png"
    assert payload["image_sha256"] == hashlib.sha256(selected.read_bytes()).hexdigest()
    assert payload["overlay_path"] == "outputs/overlay.png"
    assert payload["predictions"]["predicted_class"] in ("NG", "OK")
    assert str(tmp_path) not in json.dumps(payload)
    assert (op_run / "outputs" / "overlay.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    manifest = json.loads((op_run / "artifacts.json").read_text())
    assert {row["path"] for row in manifest["artifacts"]} == {"outputs/result.json", "outputs/overlay.png"}
    for row in manifest["artifacts"]:
        file = op_run / row["path"]
        assert row["size"] == file.stat().st_size
        assert row["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()


def test_infer_rejects_changed_image_before_loading_model(tmp_path):
    _, op_run, spec, selected = _infer_spec(tmp_path)
    selected.write_bytes(b"modified after upload")

    result = run_infer(spec)

    assert result["status"] == "failed"
    assert not (op_run / "outputs" / "result.json").exists()
    assert not (op_run / "artifacts.json").exists()


def test_infer_rejects_image_path_escape(tmp_path):
    _, op_run, spec, _ = _infer_spec(tmp_path)
    request = json.loads(spec.read_text())
    request["image_path"] = "../dataset/val/OK/ok.png"
    spec.write_text(json.dumps(request), encoding="utf-8")

    result = run_infer(spec)
    assert result["status"] == "failed"
    assert not (op_run / "outputs" / "overlay.png").exists()


def _flowchart_spec(tmp_path):
    train_run, op_run, spec, selected = _infer_spec(tmp_path)
    detector_id = "job_detector_123"
    detector_run = train_run.parent / detector_id
    shutil.copytree(train_run, detector_run)
    detector_status = json.loads((detector_run / "status.json").read_text())
    detector_status["job_id"] = detector_id
    (detector_run / "status.json").write_text(json.dumps(detector_status))
    metadata = json.loads((detector_run / "outputs" / "model_meta.json").read_text())
    metadata["task"] = "detection"
    (detector_run / "outputs" / "model_meta.json").write_text(json.dumps(metadata))
    detector_artifacts = json.loads((detector_run / "artifacts.json").read_text())
    detector_artifacts["job_id"] = detector_id
    for row in detector_artifacts["artifacts"]:
        path = detector_run / row["path"]
        row["size"] = path.stat().st_size
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (detector_run / "artifacts.json").write_text(json.dumps(detector_artifacts))
    pipeline = get_default_flowchart()
    for node in pipeline.nodes:
        if node.data.node_type == "detection_crop":
            node.data.model_job_id = detector_id
        elif node.data.node_type == "inspection":
            node.data.model_job_id = "job_eval_123"
            node.data.task = "classification"
    request = json.loads(spec.read_text())
    request.update({
        "operation": "flowchart_run", "pipeline": pipeline.model_dump(),
        "models": [
            {"job_id": detector_id, "task": "detection", "input_manifest_sha256": request["input_manifest_sha256"]},
            {"job_id": "job_eval_123", "task": "classification", "input_manifest_sha256": request["input_manifest_sha256"]},
        ],
    })
    spec.write_text(json.dumps(request), encoding="utf-8")
    return train_run, detector_run, op_run, spec, selected


def test_flowchart_verifies_two_models_and_selected_image_then_hashes_previews(tmp_path):
    _, _, op_run, spec, selected = _flowchart_spec(tmp_path)
    png_data_url = "data:image/png;base64," + base64.b64encode(selected.read_bytes()).decode()

    class FakeEngine:
        def execute(self, *, pipeline, image_path, image_id):
            assert image_path == str(selected)
            assert {node.data.model_job_id for node in pipeline.nodes if node.data.model_job_id} == {
                "job_detector_123", "job_eval_123",
            }
            return {
                "status": "completed", "final_verdict": "OK", "image_path": image_path,
                "image_id": image_id, "annotated_image": png_data_url,
                "crops": [{"roi_id": "crop_1", "crop_thumbnail": png_data_url}],
                "execution_steps": [],
            }

    result = run_flowchart(spec, engine_factory=lambda checkpoints, device: FakeEngine())

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "flowchart_result.json").read_text())
    assert payload["image_path"] == "inputs/selected.png"
    assert payload["annotated_image"] == "outputs/preview.png"
    assert payload["crops"][0]["crop_thumbnail"] == "outputs/crops/crop_000.png"
    assert str(tmp_path) not in json.dumps(payload)
    manifest = json.loads((op_run / "artifacts.json").read_text())
    assert {row["path"] for row in manifest["artifacts"]} == {
        "outputs/flowchart_result.json", "outputs/preview.png", "outputs/crops/crop_000.png",
    }
    assert {row["job_id"] for row in manifest["model_refs"]} == {"job_detector_123", "job_eval_123"}
    for row in manifest["artifacts"]:
        file = op_run / row["path"]
        assert row["size"] == file.stat().st_size
        assert row["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()


def test_flowchart_rejects_unlisted_pipeline_model(tmp_path):
    _, _, op_run, spec, _ = _flowchart_spec(tmp_path)
    request = json.loads(spec.read_text())
    request["models"] = request["models"][:1]
    spec.write_text(json.dumps(request), encoding="utf-8")
    result = run_flowchart(spec, engine_factory=lambda checkpoints, device: pytest.fail("Unverified model ran"))
    assert result["status"] == "failed"
    assert "model" in result["error"].lower()
    assert not (op_run / "artifacts.json").exists()


def test_flowchart_rejects_modified_selected_image(tmp_path):
    _, _, op_run, spec, selected = _flowchart_spec(tmp_path)
    selected.write_bytes(b"changed")
    result = run_flowchart(spec, engine_factory=lambda checkpoints, device: pytest.fail("Changed image ran"))
    assert result["status"] == "failed"
    assert "image" in result["error"].lower()
    assert not (op_run / "artifacts.json").exists()


def test_flowchart_runs_actual_cpu_segmentation_checkpoint_on_selected_image(tmp_path):
    train_run, op_run, spec, _ = _infer_spec(tmp_path)
    model = build_segmentation_model(num_classes=2, preset="fast", pretrained=False)
    checkpoint = train_run / "outputs" / "best_model.pt"
    torch.save({
        "model_state_dict": model.state_dict(), "task": "segmentation",
        "classes": ["background", "defect"], "preset": "fast", "image_size": [64, 64],
    }, checkpoint)
    metadata = train_run / "outputs" / "model_meta.json"
    metadata.write_text(json.dumps({
        "task": "segmentation", "classes": ["background", "defect"],
        "preset": "fast", "image_size": [64, 64],
    }), encoding="utf-8")
    artifacts = json.loads((train_run / "artifacts.json").read_text())
    for row in artifacts["artifacts"]:
        path = train_run / row["path"]
        row["size"] = path.stat().st_size
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (train_run / "artifacts.json").write_text(json.dumps(artifacts))
    request = json.loads(spec.read_text())
    request.update({
        "operation": "flowchart_run",
        "pipeline": get_single_segmentation_flowchart(job_id="job_eval_123").model_dump(),
        "models": [{
            "job_id": "job_eval_123", "task": "segmentation",
            "input_manifest_sha256": request["input_manifest_sha256"],
        }],
    })
    spec.write_text(json.dumps(request), encoding="utf-8")

    result = run_flowchart(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "flowchart_result.json").read_text())
    assert payload["image_path"] == "inputs/selected.png"
    assert payload["annotated_image"] == "outputs/preview.png"
    assert payload["tiles_processed"] == 1
    assert payload["model_job_ids"] == ["job_eval_123"]
    assert str(tmp_path) not in json.dumps(payload)
    assert (op_run / "outputs" / "preview.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.parametrize("conditioned", [False, True])
def test_remote_worker_runs_two_verified_models_in_sequential_graph(tmp_path, conditioned):
    train_run, op_run, spec, _ = _infer_spec(tmp_path)
    model = build_segmentation_model(num_classes=2, preset="fast", pretrained=False)
    checkpoint = train_run / "outputs" / "best_model.pt"
    torch.save({
        "model_state_dict": model.state_dict(), "task": "segmentation",
        "classes": ["background", "defect"], "preset": "fast", "image_size": [64, 64],
    }, checkpoint)
    metadata = train_run / "outputs" / "model_meta.json"
    metadata.write_text(json.dumps({
        "task": "segmentation", "classes": ["background", "defect"],
        "preset": "fast", "image_size": [64, 64],
    }), encoding="utf-8")
    first_manifest = json.loads((train_run / "artifacts.json").read_text())
    for row in first_manifest["artifacts"]:
        path = train_run / row["path"]
        row["size"] = path.stat().st_size
        row["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (train_run / "artifacts.json").write_text(json.dumps(first_manifest))

    second_id = "job_seg_second"
    second_run = train_run.parent / second_id
    shutil.copytree(train_run, second_run)
    second_status = json.loads((second_run / "status.json").read_text())
    second_status["job_id"] = second_id
    (second_run / "status.json").write_text(json.dumps(second_status))
    second_manifest = json.loads((second_run / "artifacts.json").read_text())
    second_manifest["job_id"] = second_id
    (second_run / "artifacts.json").write_text(json.dumps(second_manifest))

    pipeline = get_single_segmentation_flowchart(job_id="job_eval_123")
    second_node = pipeline.nodes[1].model_copy(deep=True)
    second_node.id = "inspect_second"
    second_node.data.label = "Second segmentation"
    second_node.data.model_job_id = second_id
    pipeline.nodes.insert(2, second_node)
    pipeline.edges[1].source = second_node.id
    pipeline.edges.insert(1, pipeline.edges[1].model_copy(update={
        "id": "first-to-second", "source": "node_inspect", "target": second_node.id,
        "payload_type": "roi",
    }))
    if conditioned:
        pipeline.nodes[1].data.params["min_defect_area_px"] = 0
        pipeline.edges[1].isBranch = "fail"
    request = json.loads(spec.read_text())
    request.update({
        "operation": "flowchart_run", "pipeline": pipeline.model_dump(),
        "models": [
            {"job_id": job_id, "task": "segmentation",
             "input_manifest_sha256": request["input_manifest_sha256"]}
            for job_id in ("job_eval_123", second_id)
        ],
    })
    spec.write_text(json.dumps(request), encoding="utf-8")

    result = run_flowchart(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "flowchart_result.json").read_text())
    assert payload["model_job_ids"] == ["job_eval_123", second_id]
    assert [step["node_id"] for step in payload["execution_steps"]] == [
        "node_input", "node_inspect", "inspect_second", "node_decision", "node_output",
    ]
    assert payload["execution_steps"][2]["input_count"] == 1
    if conditioned:
        assert payload["execution_steps"][1]["branch_verdict"] == "NG"
        assert payload["execution_steps"][1]["selected_edge_ids"] == ["first-to-second"]
    assert payload["roi_count"] == 1
    assert payload["crops"][0]["crop_thumbnail"].startswith("outputs/crops/")
    assert (op_run / "artifacts.json").is_file()


def _benchmark_spec(tmp_path, *, real_checkpoint=False):
    train_run, op_run, spec = _evaluation_run(tmp_path, real_checkpoint=real_checkpoint)
    request = json.loads(spec.read_text())
    request.update({"operation": "benchmark", "iterations": 5, "resolution": 64})
    spec.write_text(json.dumps(request), encoding="utf-8")
    return train_run, op_run, spec


def test_benchmark_real_cpu_checkpoint_reports_server_device_without_remote_path(tmp_path):
    _, op_run, spec = _benchmark_spec(tmp_path, real_checkpoint=True)

    result = run_benchmark(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "benchmark.json").read_text())
    assert payload["status"] == "success"
    assert payload["model_job_id"] == "job_eval_123"
    assert payload["device"] == "cpu"
    assert payload["iterations"] == 5
    assert payload["mean_latency_ms"] > 0
    assert payload["fps"] > 0
    assert "model_path" not in payload
    assert str(tmp_path) not in json.dumps(payload)
    manifest = json.loads((op_run / "artifacts.json").read_text())
    assert manifest["artifacts"][0]["path"] == "outputs/benchmark.json"
    assert manifest["artifacts"][0]["sha256"] == hashlib.sha256((op_run / "outputs" / "benchmark.json").read_bytes()).hexdigest()


def test_benchmark_rejects_invalid_resolution_before_model_loading(tmp_path):
    _, op_run, spec = _benchmark_spec(tmp_path)
    request = json.loads(spec.read_text())
    request["resolution"] = 16
    spec.write_text(json.dumps(request), encoding="utf-8")

    result = run_benchmark(spec)
    assert result["status"] == "failed"
    assert "resolution" in result["error"].lower()
    assert not (op_run / "outputs" / "benchmark.json").exists()


def _export_spec(tmp_path, *, real_checkpoint=False, with_calibration=False):
    train_run, op_run, spec = _evaluation_run(tmp_path, real_checkpoint=real_checkpoint)
    request = json.loads(spec.read_text())
    request.update({
        "operation": "export", "export_format": "torchscript", "resolution": 64,
        "quantize_fp16": False, "package_name": "test_runtime",
    })
    if with_calibration:
        evaluation = op_run / "inputs" / "eval_results.json"
        evaluation.parent.mkdir()
        evaluation.write_text(json.dumps({
            "job_id": "job_eval_123", "task": "classification", "zero_underkill_calibrated": True,
            "optimal_threshold": 0.37,
            "test_predictions": [{"ground_truth": "OK"}, {"ground_truth": "NG"}],
        }), encoding="utf-8")
        request["evaluation_path"] = "inputs/eval_results.json"
        request["evaluation_sha256"] = hashlib.sha256(evaluation.read_bytes()).hexdigest()
    spec.write_text(json.dumps(request), encoding="utf-8")
    return train_run, op_run, spec


def test_export_real_cpu_checkpoint_packages_hash_checked_runtime_and_calibration(tmp_path):
    _, op_run, spec = _export_spec(tmp_path, real_checkpoint=True, with_calibration=True)

    result = run_export(spec)

    assert result["status"] == "completed", result.get("error")
    payload = json.loads((op_run / "outputs" / "export_result.json").read_text())
    assert payload["package_path"] == "outputs/test_runtime.tar.gz"
    assert payload["export_format"] == "torchscript"
    assert payload["optimal_threshold"] == 0.37
    assert payload["total_files"] >= 5
    assert str(tmp_path) not in json.dumps(payload)
    package = op_run / payload["package_path"]
    with tarfile.open(package, "r:gz") as archive:
        names = {row.name for row in archive.getmembers()}
    assert "test_runtime/model.pt" in names
    assert "test_runtime/config.json" in names
    assert "test_runtime/infer.py" in names
    manifest = json.loads((op_run / "artifacts.json").read_text())
    assert {row["path"] for row in manifest["artifacts"]} == {
        "outputs/export_result.json", "outputs/test_runtime.tar.gz",
    }
    for row in manifest["artifacts"]:
        file = op_run / row["path"]
        assert row["size"] == file.stat().st_size
        assert row["sha256"] == hashlib.sha256(file.read_bytes()).hexdigest()


def test_export_rejects_calibration_file_hash_mismatch(tmp_path):
    _, op_run, spec = _export_spec(tmp_path, with_calibration=True)
    (op_run / "inputs" / "eval_results.json").write_bytes(b"changed")

    result = run_export(spec)

    assert result["status"] == "failed"
    assert "evaluation" in result["error"].lower()
    assert not (op_run / "artifacts.json").exists()

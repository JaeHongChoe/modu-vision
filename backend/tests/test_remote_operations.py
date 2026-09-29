"""Remote evaluation stays tied to its training server and local snapshot."""

from __future__ import annotations

import json
import base64
import io
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.routes_training import _write_job_receipt
from backend.remote.coordinator import ArtifactValidationError, run_remote_training
from backend.remote.operations import (
    remote_job_context, run_remote_benchmark, run_remote_evaluation,
    run_remote_export, run_remote_flowchart, run_remote_inference,
)
from backend.tests.test_remote_coordinator import FakeRemote, _digest, _setup


class FakeEvaluationRemote(FakeRemote):
    def __init__(self, root: Path, *, unsafe_path: bool = False, tamper: bool = False):
        super().__init__(root, tamper=tamper)
        self.unsafe_path = unsafe_path

    def launch(self, profile, argv, run_id):
        if not run_id.startswith("op_"):
            return super().launch(profile, argv, run_id)
        self.launches += 1
        run = self.root / "runs" / run_id
        spec = json.loads((run / "spec.json").read_text())
        image = "input/data/../../etc/passwd" if self.unsafe_path else "input/data/training_image.png"
        payload = {
            "job_id": spec["job_id"], "task": spec["task"], "metrics": {"accuracy": 1.0},
            "confusion_matrix": {"cell_samples": {"1_1": [image]}},
            "test_predictions": [{"file_path": image, "image_id": "training_image"}],
            "evaluated_at": "2026-09-29T00:00:00Z",
        }
        data = json.dumps(payload).encode()
        output = run / "outputs" / "eval_results.json"
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(data)
        (run / "artifacts.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": spec["job_id"], "operation": "evaluate",
            "input_manifest_sha256": spec["input_manifest_sha256"],
            "artifacts": [{"path": "outputs/eval_results.json", "size": len(data), "sha256": _digest(data)}],
        }))
        (run / "status.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": spec["job_id"], "operation": "evaluate", "status": "completed",
        }))
        return "fake-evaluation-pid"

    def download(self, profile, remote_relative, local):
        result = super().download(profile, remote_relative, local)
        if self.tamper and remote_relative.endswith("eval_results.json"):
            Path(local).write_bytes(b"tampered evaluation")
        return result


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+nm1sAAAAASUVORK5CYII="
)


class FakeAdditionalRemote(FakeRemote):
    def __init__(self, root: Path, *, unsafe_export: bool = False):
        super().__init__(root)
        self.unsafe_export = unsafe_export

    def launch(self, profile, argv, run_id):
        self.launches += 1
        run = self.root / "runs" / run_id
        spec = json.loads((run / "spec.json").read_text())
        outputs = {}
        manifest_extra = {}
        operation = spec["operation"]
        if operation == "infer":
            outputs["outputs/overlay.png"] = _PNG
            outputs["outputs/result.json"] = json.dumps({
                "image_id": spec["image_id"], "image_path": spec["image_path"],
                "image_sha256": spec["image_sha256"], "overlay_path": "outputs/overlay.png",
                "threshold": spec["threshold"], "confidence_score": 0.8,
                "predictions": {"label": "NG"}, "latency_ms": 1.2,
            }).encode()
        elif operation == "flowchart_run":
            outputs["outputs/preview.png"] = _PNG
            outputs["outputs/crops/crop_000.png"] = _PNG
            outputs["outputs/flowchart_result.json"] = json.dumps({
                "status": "success", "final_verdict": "NG", "is_ok": False,
                "rejection_reason": "defect", "roi_count": 1, "defective_roi_count": 1,
                "annotated_image": "outputs/preview.png", "image_path": spec["image_path"],
                "image_sha256": spec["image_sha256"],
                "model_job_ids": sorted(row["job_id"] for row in spec["models"]),
                "crops": [{"roi_id": "r1", "crop_thumbnail": "outputs/crops/crop_000.png"}],
                "execution_steps": [], "total_latency_ms": 1,
            }).encode()
            manifest_extra = {
                "model_refs": sorted(spec["models"], key=lambda row: row["job_id"]),
                "selected_image_sha256": spec["image_sha256"],
            }
        elif operation == "benchmark":
            outputs["outputs/benchmark.json"] = json.dumps({
                "status": "success", "model_job_id": spec["job_id"], "task": spec["task"],
                "device": "cuda", "device_name": "NVIDIA L40S",
                "mean_latency_ms": 3.1, "fps": 322.5,
            }).encode()
        elif operation == "export":
            package = spec["package_name"]
            path = "../escape.py" if self.unsafe_export else "infer.py"
            content = b"print('verified package')\n"
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w:gz") as archive:
                member = tarfile.TarInfo(f"{package}/{path}")
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            outputs[f"outputs/{package}.tar.gz"] = stream.getvalue()
            outputs["outputs/export_result.json"] = json.dumps({
                "status": "success", "model_job_id": spec["job_id"],
                "package_name": package, "package_path": f"outputs/{package}.tar.gz",
                "manifest": [{"path": path, "size": len(content), "sha256": _digest(content)}],
                "total_files": 1, "export_format": spec["export_format"],
                "model_file": "infer.py", "optimal_threshold": 0.5,
            }).encode()
        else:
            raise AssertionError(operation)
        entries = []
        for relative, content in outputs.items():
            file = run / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
            entries.append({"path": relative, "size": len(content), "sha256": _digest(content)})
        (run / "artifacts.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": spec["job_id"], "operation": operation,
            "input_manifest_sha256": spec["input_manifest_sha256"],
            "artifacts": entries, **manifest_extra,
        }))
        (run / "status.json").write_text(json.dumps({
            "protocol_version": 1, "job_id": spec["job_id"], "operation": operation,
            "status": "completed",
        }))
        return "fake-operation-pid"


def _completed_remote(tmp_path, monkeypatch, *, unsafe_path=False, tamper=False):
    monkeypatch.setenv("VISION_AI_STUDIO_USER_DATA_DIR", str(tmp_path / "user_data"))
    _, output, profile, record = _setup(tmp_path)
    fake = FakeEvaluationRemote(Path(profile.remote_root), unsafe_path=unsafe_path, tamper=tamper)
    # Train artifact transfer is valid; tampering applies only to evaluation.
    fake.tamper = False
    assert run_remote_training(record, profile, transport=fake)["status"] == "completed"
    record.status = "completed"
    _write_job_receipt(record)
    fake.tamper = tamper
    context = remote_job_context(output, record.job_id)
    assert context is not None
    return context, fake


def test_remote_evaluation_maps_real_snapshot_files_back_to_local_urls(tmp_path, monkeypatch):
    context, fake = _completed_remote(tmp_path, monkeypatch)
    result = run_remote_evaluation(context, transport=fake)
    local = context.dataset_path / "training_image.png"
    assert result["confusion_matrix"]["cell_samples"]["1_1"] == [str(local)]
    assert result["test_predictions"][0]["file_path"] == str(local)
    assert result["test_predictions"][0]["thumbnail_url"].startswith("/api/dataset/thumbnail/training_image.png?")
    assert (context.output_dir / "eval_results.json").is_file()
    assert fake.launches == 2  # one train, one evaluation


def test_remote_context_rejects_changed_local_training_snapshot(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    image = context.dataset_path / "training_image.png"
    image.write_bytes(b"other bytes")  # Same length as the original; size checks alone cannot catch this.

    with pytest.raises(ArtifactValidationError, match="snapshot.*hash mismatch"):
        remote_job_context(context.output_dir, context.job_id)


def test_remote_context_accepts_verified_data_when_unused_archive_is_removed(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    (context.dataset_path.parent / "snapshot.tar.gz").unlink()

    restored = remote_job_context(context.output_dir, context.job_id)

    assert restored is not None
    assert restored.input_manifest_sha256 == context.input_manifest_sha256


@pytest.mark.parametrize("operation", ["evaluate", "infer", "flowchart_run"])
def test_stale_remote_context_rejects_changed_snapshot_before_result(
    tmp_path, monkeypatch, operation,
):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    image = context.dataset_path / "training_image.png"
    image.write_bytes(b"other bytes")
    fake = (FakeEvaluationRemote(Path(context.profile.remote_root)) if operation == "evaluate"
            else FakeAdditionalRemote(Path(context.profile.remote_root)))

    with pytest.raises(ArtifactValidationError, match="snapshot.*hash mismatch"):
        if operation == "evaluate":
            run_remote_evaluation(context, transport=fake)
        elif operation == "infer":
            run_remote_inference(context, image, 0.42, "selected-image", transport=fake)
        else:
            run_remote_flowchart(contexts=[context], pipeline={"id": "pipeline", "nodes": [], "edges": []},
                                 image=image, transport=fake)
    assert not (context.output_dir / "eval_results.json").exists()


def test_remote_evaluation_rejects_path_escaping_the_snapshot(tmp_path, monkeypatch):
    context, fake = _completed_remote(tmp_path, monkeypatch, unsafe_path=True)
    with pytest.raises(ArtifactValidationError, match="unsafe"):
        run_remote_evaluation(context, transport=fake)
    assert not (context.output_dir / "eval_results.json").exists()


def test_remote_evaluation_rejects_tampered_result(tmp_path, monkeypatch):
    context, fake = _completed_remote(tmp_path, monkeypatch, tamper=True)
    with pytest.raises(ArtifactValidationError, match="hash mismatch"):
        run_remote_evaluation(context, transport=fake)
    assert not (context.output_dir / "eval_results.json").exists()


def test_remote_evaluation_rejects_dataset_override_even_with_cached_result(tmp_path, monkeypatch):
    from backend.api import routes_evaluation

    context, _ = _completed_remote(tmp_path, monkeypatch)
    another = tmp_path / "another_dataset"
    another.mkdir()
    local = context.dataset_path / "training_image.png"
    (context.output_dir / "eval_results.json").write_text(json.dumps({
        "confusion_matrix": {"cell_samples": {"0_0": [str(local)]}},
        "test_predictions": [{"file_path": str(local), "ground_truth": "OK"}],
    }))
    monkeypatch.setattr(routes_evaluation, "_resolve_job_artifacts", lambda **kwargs: (
        context.output_dir, context.output_dir / "best_model.pt", {}, context.task,
        context.job_id, Path(kwargs["dataset_path_override"]),
    ))
    with pytest.raises(HTTPException) as error:
        routes_evaluation.run_or_load_evaluation(job_id=context.job_id, dataset_path=str(another))
    assert error.value.status_code == 422
    assert "snapshot" in str(error.value.detail)


def test_remote_operation_worker_exit_returns_failure_without_hour_long_wait(tmp_path, monkeypatch):
    from backend.remote import operations

    context, _ = _completed_remote(tmp_path, monkeypatch)
    monkeypatch.setattr(operations, "OP_POLL_INTERVAL_SECONDS", 0.001, raising=False)

    class DeadOperation(FakeRemote):
        def launch(self, profile, argv, run_id):
            self.launches += 1
            return "dead-pid"

        def is_running(self, profile, run_id, handle):
            return False

    with pytest.raises(RuntimeError, match="exited before publishing status"):
        run_remote_evaluation(context, transport=DeadOperation(Path(context.profile.remote_root)))


def test_remote_owner_cannot_be_changed_by_editing_local_receipt(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    receipt_file = context.output_dir / "job_receipt.json"
    receipt = json.loads(receipt_file.read_text())
    receipt["compute_profile_id"] = "another-server"
    receipt_file.write_text(json.dumps(receipt))
    with pytest.raises(ArtifactValidationError, match="provenance"):
        remote_job_context(context.output_dir, context.job_id)


def test_missing_remote_journal_never_falls_back_to_local_compute(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    (context.output_dir / "remote_job.json").unlink()
    with pytest.raises(ArtifactValidationError, match="refusing local compute fallback"):
        remote_job_context(context.output_dir, context.job_id)


def test_remote_inference_returns_verified_png_without_server_path(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    fake = FakeAdditionalRemote(Path(context.profile.remote_root))
    image = context.dataset_path / "training_image.png"
    result, png = run_remote_inference(context, image, 0.42, "selected-image", transport=fake)
    assert png == _PNG
    assert result["overlay_base64"].startswith("data:image/png;base64,")
    assert result["threshold"] == 0.42
    assert "image_path" not in result
    assert fake.launches == 1


def test_flowchart_restores_previews_and_accepts_reverse_model_id_order(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    second = replace(context, job_id="job_0000000000_aaaaaa")
    fake = FakeAdditionalRemote(Path(context.profile.remote_root))
    image = context.dataset_path / "training_image.png"
    result = run_remote_flowchart(
        [context, second], {"id": "pipeline", "nodes": [], "edges": []}, image,
        "selected-image", transport=fake,
    )
    assert result["annotated_image"].startswith("data:image/png;base64,")
    assert result["crops"][0]["crop_thumbnail"].startswith("data:image/png;base64,")
    assert result["image_path"] == str(image)
    assert "model_job_ids" not in result


def test_remote_benchmark_reports_actual_server_and_local_model_path(tmp_path, monkeypatch):
    context, _ = _completed_remote(tmp_path, monkeypatch)
    fake = FakeAdditionalRemote(Path(context.profile.remote_root))
    result = run_remote_benchmark(context, 5, 64, transport=fake)
    assert result["device_name"] == "NVIDIA L40S"
    assert result["compute_profile_id"] == context.profile.id
    assert result["model_path"] == str(context.output_dir / "best_model.pt")


def test_remote_export_extracts_verified_local_package_and_blocks_traversal(tmp_path, monkeypatch):
    from backend.engine import exporter

    monkeypatch.setattr(exporter, "EXPORTS_DIR", tmp_path / "packages")
    context, _ = _completed_remote(tmp_path, monkeypatch)
    fake = FakeAdditionalRemote(Path(context.profile.remote_root))
    result = run_remote_export(context, "torchscript", 64, False, "package_good", transport=fake)
    assert Path(result["package_path"]).is_dir()
    assert (Path(result["package_path"]) / "infer.py").is_file()
    assert result["compute_profile_id"] == context.profile.id

    bad = FakeAdditionalRemote(Path(context.profile.remote_root), unsafe_export=True)
    with pytest.raises(ArtifactValidationError, match="unsafe"):
        run_remote_export(context, "torchscript", 64, False, "package_bad", transport=bad)
    assert not (tmp_path / "packages" / "package_bad").exists()

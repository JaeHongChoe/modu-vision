"""
backend/tests/test_api_endpoints.py

Comprehensive Integration Test Suite for all Vision AI Studio REST API Endpoints.
Validates:
  - System Health contract (GET /health)
  - Bilingual Error Catalog Endpoints (/api/errors)
  - Project Management CRUD (/api/project/*)
  - Dataset Studio (generate, import, split, images, thumbnails)
  - Annotation Studio (save, get, delete, bbox sanitization, mask generation)
  - AutoML Training Controller (start, stop, status)
  - Evaluation & Inspection Studio (results, clickable confusion matrix cell_samples, heatmap overlay)
  - Standalone Report Exporter (HTML & JSON, print styling, zero external assets)
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

_orig_httpx_init = httpx.Client.__init__
def _compat_httpx_init(self, *args, app=None, **kwargs):
    return _orig_httpx_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_init

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.synthetic_generator import generate_synthetic_dataset
from backend.engine.trainer import UnifiedAutoMLTrainer
from backend.api.routes_training import training_job_manager, JobRecord


@pytest.fixture(scope="module")
def app_and_client():
    temp_dir = tempfile.mkdtemp(prefix="test_vision_studio_")
    app = create_app(project_dir=temp_dir)
    client = TestClient(app)
    yield app, client, Path(temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(scope="module")
def trained_eval_job(tmp_path_factory):
    """Trains a fast 1-epoch classification model on a synthetic dataset for authentic API evaluation."""
    tmp_dir = tmp_path_factory.mktemp("eval_job_suite")
    data_dir = tmp_dir / "dataset"
    out_dir = tmp_dir / "model_job"

    # Generate 20 synthetic samples for robust train/val split
    generate_synthetic_dataset(output_dir=data_dir, num_samples=20, modality="pcb", task="classification")
    cls_data = data_dir / "classification"

    # Fast 1-epoch training (~5 seconds)
    trainer = UnifiedAutoMLTrainer(
        task="classification",
        dataset_path=cls_data,
        output_dir=out_dir,
        preset="fast",
        config_overrides={"epochs": 1, "image_size": 64, "batch_size": 8},
    )
    res = trainer.train(job_id="job_eval_authentic")

    record = JobRecord(
        job_id="job_eval_authentic",
        task="classification",
        preset="fast",
        dataset_path=str(cls_data),
        output_dir=str(out_dir),
        status="completed",
        trainer=trainer,
        result=res,
        best_metric=res.get("best_metric"),
    )
    training_job_manager._jobs["job_eval_authentic"] = record
    return {
        "job_id": "job_eval_authentic",
        "dataset_path": cls_data,
        "output_dir": out_dir,
        "record": record,
    }


class TestHealthCheckContract:
    """Validates GET /health adhering strictly to PROJECT.md line 144 contract."""

    def test_health_check_contract(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert data["device"] in ["mps", "cuda", "cpu"]
        assert isinstance(data["device_name"], str) and len(data["device_name"]) > 0
        assert "is_accelerated" in data
        assert "torch_version" in data


class TestErrorCatalogEndpoints:
    """Validates bilingual error catalog query endpoints."""

    def test_list_all_errors(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/errors")
        assert res.status_code == 200
        errors = res.json()["errors"]
        assert len(errors) == 8
        codes = [e["code"] for e in errors]
        assert "ERR_001" in codes
        assert "ERR_008" in codes

    def test_get_specific_error(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/errors/ERR_001")
        assert res.status_code == 200
        data = res.json()
        assert data["code"] == "ERR_001"
        assert "메모리" in data["title_kr"]
        assert data["auto_fixable"] is True

    def test_get_non_existent_error(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/errors/ERR_999")
        assert res.status_code == 404


class TestProjectRoutes:
    """Validates Project CRUD endpoints."""

    def test_create_and_current_project(self, app_and_client):
        _, client, temp_dir = app_and_client
        proj_name = "PCB Defect Project"
        res = client.post(
            "/api/project/create",
            json={"name": proj_name, "task": "classification", "description": "Shop-floor PCB run"},
        )
        assert res.status_code == 200
        proj = res.json()
        assert proj["name"] == proj_name
        assert proj["task"] == "classification"
        assert Path(proj["project_dir"]).is_dir()

        # Query current
        curr_res = client.get("/api/project/current")
        assert curr_res.status_code == 200
        assert curr_res.json()["name"] == proj_name

    def test_update_project(self, app_and_client):
        _, client, _ = app_and_client
        res = client.put(
            "/api/project/update",
            json={"task": "detection", "active_preset": "precision"},
        )
        assert res.status_code == 200
        updated = res.json()
        assert updated["task"] == "detection"
        assert updated["active_preset"] == "precision"

    def test_list_projects(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/project/list")
        assert res.status_code == 200
        assert "projects" in res.json()


class TestDatasetRoutes:
    """Validates dataset generation, import, split, pagination, and thumbnails."""

    def test_dataset_generation_and_import(self, app_and_client):
        _, client, temp_dir = app_and_client
        out_dataset = temp_dir / "generated_dataset"
        # Generate 12 samples for fast test execution
        gen_res = client.post(
            "/api/dataset/generate",
            json={
                "task": "classification",
                "num_samples": 12,
                "output_dir": str(out_dataset),
                "modality": "pcb",
                "width": 128,
                "height": 128,
            },
        )
        assert gen_res.status_code == 200
        gen_data = gen_res.json()
        assert gen_data["status"] == "success"
        assert gen_data["count"] >= 10
        assert len(gen_data["classes"]) >= 1

        # Import the generated dataset
        import_res = client.post(
            "/api/dataset/import",
            json={"folder_path": str(out_dataset), "task": "classification"},
        )
        assert import_res.status_code == 200
        imp_data = import_res.json()
        assert imp_data["total_images"] >= 10
        assert "train" in imp_data["split"]

    def test_dataset_import_non_existent_folder(self, app_and_client):
        _, client, _ = app_and_client
        res = client.post(
            "/api/dataset/import",
            json={"folder_path": "/path/does/not/exist", "task": "classification"},
        )
        assert res.status_code == 404

    def test_dataset_split(self, app_and_client):
        _, client, temp_dir = app_and_client
        out_dataset = temp_dir / "generated_dataset"
        split_res = client.post(
            "/api/dataset/split",
            json={"folder_path": str(out_dataset), "train_ratio": 0.75, "seed": 42},
        )
        assert split_res.status_code == 200
        data = split_res.json()
        assert "split" in data
        assert data["split"]["train"] > 0

    def test_dataset_images_pagination(self, app_and_client):
        _, client, temp_dir = app_and_client
        out_dataset = temp_dir / "generated_dataset"
        res = client.get(f"/api/dataset/images?folder_path={out_dataset}&limit=5&offset=0")
        assert res.status_code == 200
        data = res.json()
        assert "total" in data
        assert len(data["items"]) <= 5
        if data["items"]:
            first = data["items"][0]
            assert "thumbnail_url" in first
            assert "file_path" in first

            # Test thumbnail generation endpoint
            thumb_res = client.get(first["thumbnail_url"])
            assert thumb_res.status_code == 200
            assert "image" in thumb_res.headers.get("content-type", "")
            assert "Cache-Control" in thumb_res.headers

    def test_dataset_thumbnail_corrupt_and_zero_byte(self, app_and_client):
        _, client, temp_dir = app_and_client
        # 0-byte image
        zero_file = temp_dir / "empty_defect.png"
        zero_file.touch()
        res_zero = client.get(f"/api/dataset/thumbnail/{zero_file.name}?file_path={zero_file}")
        assert res_zero.status_code == 400
        assert "ERR_005" in str(res_zero.json()) or "ERR_CORRUPT_IMAGE" in str(res_zero.json())

        # Corrupted non-image text file
        corrupt_file = temp_dir / "corrupted_surface.jpg"
        corrupt_file.write_text("CORRUPT_HEADER_NOT_A_JPEG_STREAM_XYZ123")
        res_corrupt = client.get(f"/api/dataset/thumbnail/{corrupt_file.name}?file_path={corrupt_file}")
        assert res_corrupt.status_code == 400
        assert "ERR_005" in str(res_corrupt.json()) or "ERR_CORRUPT_IMAGE" in str(res_corrupt.json())


class TestAnnotationRoutes:
    """Validates BBox sanitization, mask generation, and annotation persistence."""

    def test_annotation_save_bbox_sanitization_and_mask_generation(self, app_and_client):
        _, client, temp_dir = app_and_client
        annot_dir = temp_dir / "annotations_test"
        payload = {
            "image_id": "test_img_001",
            "output_dir": str(annot_dir),
            "image_width": 256,
            "image_height": 256,
            "annotations": [
                {
                    "type": "bbox",
                    "label": "solder_bridge",
                    "category_id": 1,
                    # Inverted coordinates: xmin=60 > xmax=20, ymin=80 > ymax=30
                    "bbox": [60.0, 80.0, 20.0, 30.0],
                },
                {
                    "type": "polygon",
                    "label": "crack",
                    "category_id": 2,
                    "points": [[10, 10], [50, 10], [50, 50], [10, 50]],
                },
            ],
        }
        res = client.post("/api/annotations/save", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "saved"
        assert data["mask_generated"] is True

        # Retrieve saved annotations
        get_res = client.get(f"/api/annotations/test_img_001?dir_path={annot_dir}")
        assert get_res.status_code == 200
        saved = get_res.json()
        assert len(saved["annotations"]) == 2
        # Verify bounding box was sanitized: xmin < xmax and ymin < ymax
        bbox_item = [a for a in saved["annotations"] if a["type"] == "bbox"][0]
        assert bbox_item["bbox"][0] == 20.0
        assert bbox_item["bbox"][2] == 60.0

        # Verify raster mask PNG was generated
        mask_file = annot_dir / "masks" / "test_img_001.png"
        assert mask_file.is_file()

        # Delete annotation
        del_res = client.delete(f"/api/annotations/test_img_001?dir_path={annot_dir}")
        assert del_res.status_code == 200
        assert not (annot_dir / "test_img_001.json").exists()


class TestTrainingRoutes:
    """Validates AutoML Training controller start/stop/status."""

    def test_training_status_idle(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/training/status")
        assert res.status_code == 200
        assert res.json()["is_training"] is False

    def test_training_start_missing_dataset_returns_400(self, app_and_client):
        _, client, _ = app_and_client
        res = client.post(
            "/api/training/start",
            json={
                "task": "classification",
                "preset": "fast",
                "dataset_path": "/invalid/non_existent/path",
            },
        )
        assert res.status_code == 400
        assert "ERR_NO_DATA" in str(res.json())

    def test_training_stop_when_idle(self, app_and_client):
        _, client, _ = app_and_client
        res = client.post("/api/training/stop", json={"job_id": "none"})
        assert res.status_code == 200
        assert res.json()["status"] == "not_running"


class TestEvaluationRoutes:
    """Validates 100% authentic Confusion Matrix, real cell_samples on disk, and Heatmap overlays."""

    def test_evaluation_results_authentic_execution(self, app_and_client, trained_eval_job):
        _, client, _ = app_and_client
        job_id = trained_eval_job["job_id"]
        res = client.get(f"/api/evaluation/results?job_id={job_id}")
        assert res.status_code == 200
        data = res.json()

        assert data["job_id"] == job_id
        assert data["task"] == "classification"
        assert "metrics" in data
        assert "confusion_matrix" in data
        assert "test_predictions" in data

        cm = data["confusion_matrix"]
        assert "classes" in cm
        assert "matrix" in cm
        assert "cell_samples" in cm
        cell_samples = cm["cell_samples"]
        assert len(cell_samples) > 0

        # CRITICAL VERIFICATION 1: All files in cell_samples must exist on disk!
        total_samples_in_cells = 0
        for cell_key, sample_files in cell_samples.items():
            assert ":" in cell_key
            for f_path in sample_files:
                assert os.path.exists(f_path), f"File {f_path} from cell_samples does not exist on disk!"
                total_samples_in_cells += 1

        # CRITICAL VERIFICATION 2: Confusion matrix sum equals total evaluated images
        matrix_sum = sum(sum(row) for row in cm["matrix"])
        assert matrix_sum == total_samples_in_cells
        assert matrix_sum > 0, "Confusion matrix must evaluate at least 1 image"

        # CRITICAL VERIFICATION 3: Metrics are genuine numbers within [0.0, 1.0]
        metrics = data["metrics"]
        assert 0.0 <= metrics["accuracy"] <= 1.0
        assert 0.0 <= metrics["macro_f1"] <= 1.0

        # CRITICAL VERIFICATION 4: test_predictions contain real files and valid confidences
        assert len(data["test_predictions"]) == total_samples_in_cells
        for pred in data["test_predictions"]:
            assert os.path.exists(pred["file_path"])
            assert 0.0 <= pred["confidence"] <= 1.0
            assert pred["predicted_class"] in cm["classes"]
            assert pred["ground_truth"] in cm["classes"]

    def test_evaluation_non_existent_job_returns_404(self, app_and_client):
        _, client, _ = app_and_client
        res = client.get("/api/evaluation/results?job_id=non_existent_job_999")
        assert res.status_code == 404
        assert "not found" in res.json()["detail"].lower()

    def test_evaluation_heatmap_base64_and_image(self, app_and_client, trained_eval_job):
        _, client, _ = app_and_client
        job_id = trained_eval_job["job_id"]
        res_eval = client.get(f"/api/evaluation/results?job_id={job_id}")
        assert res_eval.status_code == 200
        test_pred = res_eval.json()["test_predictions"][0]
        real_img_path = test_pred["file_path"]
        img_name = test_pred["file_name"]

        # Base64 query with real image and real model
        res_b64 = client.get(
            f"/api/evaluation/heatmap/{img_name}?job_id={job_id}&file_path={real_img_path}&threshold=0.5&format=base64"
        )
        assert res_b64.status_code == 200
        data = res_b64.json()
        assert "overlay_base64" in data
        assert data["overlay_base64"].startswith("data:image/png;base64,")
        assert 0.0 <= data["confidence_score"] <= 1.0
        assert "latency_ms" in data
        assert data["latency_ms"] > 0.0

        # Image query with real image and real model
        res_img = client.get(
            f"/api/evaluation/heatmap/{img_name}?job_id={job_id}&file_path={real_img_path}&threshold=0.5&format=image"
        )
        assert res_img.status_code == 200
        assert res_img.headers["content-type"] == "image/png"
        assert len(res_img.content) > 100

    def test_evaluation_heatmap_missing_model_returns_404(self, app_and_client, trained_eval_job):
        _, client, _ = app_and_client
        job_id = trained_eval_job["job_id"]
        res_eval = client.get(f"/api/evaluation/results?job_id={job_id}")
        real_img_path = res_eval.json()["test_predictions"][0]["file_path"]
        res = client.get(f"/api/evaluation/heatmap/test.png?job_id=non_existent_model&file_path={real_img_path}")
        assert res.status_code == 404

    def test_zero_escape_calibrate_post_and_get(self, app_and_client, trained_eval_job):
        _, client, _ = app_and_client
        job_id = trained_eval_job["job_id"]

        # Test POST /api/evaluation/zero-escape-calibrate
        res_post = client.post(
            "/api/evaluation/zero-escape-calibrate",
            json={
                "job_id": job_id,
                "target_max_underkill": 0,
                "cost_escape": 500.0,
                "cost_scrap": 25.0,
                "current_threshold": 0.50,
                "apply_to_eval_results": True,
            },
        )
        assert res_post.status_code == 200
        data_post = res_post.json()
        assert "optimal_threshold" in data_post
        assert data_post["calibrated"] is True
        assert "message" in data_post
        assert 0.0 <= data_post["optimal_threshold"] <= 1.0

        # Test GET /api/evaluation/zero-escape-calibrate
        res_get = client.get(
            f"/api/evaluation/zero-escape-calibrate?job_id={job_id}&target_max_underkill=0&apply_to_eval_results=false"
        )
        assert res_get.status_code == 200
        data_get = res_get.json()
        assert "optimal_threshold" in data_get
        assert data_get["calibrated"] is True

    def test_zero_escape_calibration_directory_target_job(self, app_and_client):
        _, client, _ = app_and_client
        tmp_dir = Path(tempfile.mkdtemp(prefix="test_dir_job_"))
        try:
            eval_file = tmp_dir / "eval_results.json"
            initial_data = {
                "job_id": "test_dir",
                "task": "classification",
                "test_predictions": [
                    {
                        "ground_truth": "NG",
                        "defect_score": 0.88,
                        "predicted_class": "NG",
                        "confidence": 0.88,
                    },
                    {
                        "ground_truth": "OK",
                        "defect_score": 0.12,
                        "predicted_class": "OK",
                        "confidence": 0.88,
                    },
                ],
            }
            with open(eval_file, "w", encoding="utf-8") as f:
                json.dump(initial_data, f, indent=2)

            res = client.post(
                "/api/evaluation/zero-escape-calibrate",
                json={"job_id": str(tmp_dir), "apply_to_eval_results": True},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["calibrated"] is True
            assert abs(data["optimal_threshold"] - 0.8799) < 1e-3

            with open(eval_file, "r", encoding="utf-8") as f:
                updated = json.load(f)
            assert updated.get("zero_underkill_calibrated") is True
            assert abs(updated.get("optimal_threshold") - 0.8799) < 1e-3
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_zero_escape_calibration_concurrent_calls(self, app_and_client):
        import concurrent.futures
        _, client, temp_dir = app_and_client
        job_id = "job_test_concurrent_endpoints"
        job_dir = temp_dir / "models" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        eval_file = job_dir / "eval_results.json"

        initial_data = {
            "job_id": job_id,
            "task": "classification",
            "test_predictions": [
                {
                    "ground_truth": "NG",
                    "defect_score": 0.75,
                    "predicted_class": "NG",
                    "confidence": 0.75,
                },
                {
                    "ground_truth": "OK",
                    "defect_score": 0.20,
                    "predicted_class": "OK",
                    "confidence": 0.80,
                },
            ],
        }
        with open(eval_file, "w", encoding="utf-8") as f:
            json.dump(initial_data, f, indent=2)

        record = JobRecord(
            job_id=job_id,
            task="classification",
            preset="fast",
            dataset_path=str(job_dir),
            output_dir=str(job_dir),
            status="completed",
        )
        training_job_manager._jobs[job_id] = record

        def worker(_):
            return client.post(
                "/api/evaluation/zero-escape-calibrate",
                json={"job_id": job_id, "apply_to_eval_results": True},
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(worker, i) for i in range(16)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        assert len(results) == 16
        for r in results:
            assert r.status_code == 200
            assert r.json()["calibrated"] is True
            assert abs(r.json()["optimal_threshold"] - 0.7499) < 1e-3

        with open(eval_file, "r", encoding="utf-8") as f:
            updated = json.load(f)
        assert updated.get("zero_underkill_calibrated") is True
        assert abs(updated.get("optimal_threshold") - 0.7499) < 1e-3


class TestReportRoutes:
    """Validates standalone single-file HTML & JSON report generation using real model evaluations."""

    def test_export_html_report_standalone_zero_cdn(self, app_and_client, trained_eval_job):
        _, client, temp_dir = app_and_client
        job_id = trained_eval_job["job_id"]
        out_html = temp_dir / "reports" / "report.html"
        res = client.post(
            "/api/report/export",
            json={"job_id": job_id, "format": "html", "output_path": str(out_html)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "html"
        assert Path(data["file_path"]).is_file()

        content = data["content"]
        assert "<!DOCTYPE html>" in content
        assert "@media print" in content
        assert "http://" not in content and "https://" not in content
        assert "<script src=" not in content
        assert "data:image/png;base64," in content
        # Ensure procedural mock badges and static captions do NOT exist
        assert "Solder Bridge detected (Conf: 96.8%)" not in content
        assert "Confidence: 99.4%" not in content

    def test_export_json_report(self, app_and_client, trained_eval_job):
        _, client, temp_dir = app_and_client
        job_id = trained_eval_job["job_id"]
        out_json = temp_dir / "reports" / "report.json"
        res = client.post(
            "/api/report/export",
            json={"job_id": job_id, "format": "json", "output_path": str(out_json)},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "json"
        assert Path(data["file_path"]).is_file()
        parsed = json.loads(data["content"])
        assert "evaluation" in parsed
        assert parsed["job_id"] == job_id
        assert "metrics" in parsed["evaluation"]
        assert "confusion_matrix" in parsed["evaluation"]

    def test_export_report_non_existent_job_returns_404(self, app_and_client):
        _, client, _ = app_and_client
        res = client.post(
            "/api/report/export",
            json={"job_id": "non_existent_job_404", "format": "html"},
        )
        assert res.status_code == 404

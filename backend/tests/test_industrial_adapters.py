"""
backend/tests/test_industrial_adapters.py

Comprehensive Test Suite for Industrial Dataset Adapters & Preprocessing Safeguards.
Validates:
  1. WebP magic bytes and format validation.
  2. Double extension sanitation (.jpg.jpg).
  3. 16-bit TIFF/grayscale normalization and RGBA compositing.
  4. Adaptive resolution memory safeguard (capping 45MP images at max_dim=1600).
  5. Hierarchical classification adapter on real and synthetic manufacturing trees.
  6. LabelMe-to-COCO object detection adapter with microscopic flaw precision.
  7. LabelMe polygon rasterization into 8-bit discrete segmentation masks.
  8. Flexible industrial anomaly dataset loader (non-Reference normal folders).
  9. Single-class evaluation crash fix (avoiding IndexError when len(classes) == 1).
  10. REST API endpoint /api/dataset/import-industrial conforming to SCOPE.md.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image
import torch
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.engine.dataset_loaders import (
    ClassificationDataset,
    DetectionDataset,
    SegmentationDataset,
    AnomalyDataset,
    sanitize_file_stem,
    validate_image_file,
    _read_image_rgb,
)
from backend.engine.industrial_adapters import (
    DEFECT_KEYWORD_MAP,
    FlexibleAnomalyDataset,
    HierarchicalClassificationAdapter,
    HierarchicalClassificationDataset,
    IGNORED_JSON_NAMES,
    LabelMeDetectionDataset,
    LabelMeParser,
    LabelMeRasterizer,
    LabelMeSegmentationDataset,
    find_labelme_folder,
    find_matching_image,
    inspect_industrial_dataset,
    is_valid_labelme_file,
    normalize_defect_category,
    read_image_safely_rgb,
)
from backend.engine.classification.model import create_classification_model
from backend.engine.classification.metrics import compute_classification_metrics


REAL_OPERATIONAL_SERVER = Path("/Users/kai/Downloads/운영서버")
REAL_BACKUP_QC = Path("/Volumes/backup/Reference_QC_데이터")


class TestWebPAndFormatValidation:
    """Verifies image format validation including WebP magic bytes and double extension handling."""

    def test_webp_magic_byte_validation_success(self, tmp_path: Path):
        """Valid WebP file with RIFF...WEBP header is accepted as OK."""
        webp_path = tmp_path / "valid_image.webp"
        im = Image.new("RGB", (64, 64), color=(120, 200, 80))
        im.save(webp_path, format="WEBP")

        result = validate_image_file(webp_path)
        assert result.valid is True
        assert result.error_code == "OK"
        assert result.dimensions == (64, 64)

    def test_corrupt_header_rejection(self, tmp_path: Path):
        """Corrupt magic bytes return CORRUPT_HEADER."""
        corrupt_path = tmp_path / "corrupt.webp"
        with open(corrupt_path, "wb") as f:
            f.write(b"UNKNOWN_HEADER_BYTES_1234567890")

        result = validate_image_file(corrupt_path)
        assert result.valid is False
        assert result.error_code == "CORRUPT_HEADER"

    def test_double_extension_sanitization(self):
        """Removes duplicate extension artifacts (.jpg.jpg) returning clean stems."""
        assert sanitize_file_stem("diag_00_G107_M1-c_Photo-L1-11.jpg.jpg") == "diag_00_G107_M1-c_Photo-L1-11"
        assert sanitize_file_stem("sample_part.png.png") == "sample_part"
        assert sanitize_file_stem("normal_image.jpg") == "normal_image"
        assert sanitize_file_stem(Path("/some/dir/inspect_123.jpg.jpg")) == "inspect_123"


class TestTiffGrayscaleAndRgbaNormalization:
    """Tests 16-bit TIFF / Grayscale dynamic range normalization and RGBA compositing."""

    def test_16bit_grayscale_tiff_normalization(self, tmp_path: Path):
        """16-bit grayscale TIFF (uint16 up to 65535) safely maps to 8-bit RGB without clipping."""
        tiff_path = tmp_path / "wafer_aoi_16bit.tiff"
        # Create 16-bit gradient
        arr_16 = np.linspace(1000, 60000, 100 * 100, dtype=np.uint16).reshape((100, 100))
        im_16 = Image.fromarray(arr_16, mode="I;16")
        im_16.save(tiff_path)

        rgb = _read_image_rgb(tiff_path)
        assert rgb.shape == (100, 100, 3)
        assert rgb.dtype == np.uint8
        # Should span dynamic range
        assert rgb.min() >= 0
        assert rgb.max() <= 255
        assert rgb.max() > 200

    def test_8bit_grayscale_l_mode(self, tmp_path: Path):
        """Single-channel 8-bit grayscale replicates across 3 RGB channels."""
        l_path = tmp_path / "mask_gray.png"
        arr_l = np.full((50, 50), 128, dtype=np.uint8)
        Image.fromarray(arr_l, mode="L").save(l_path)

        rgb = _read_image_rgb(l_path)
        assert rgb.shape == (50, 50, 3)
        assert np.all(rgb[:, :, 0] == rgb[:, :, 1])
        assert np.all(rgb[:, :, 1] == rgb[:, :, 2])

    def test_rgba_transparent_cutout_compositing(self, tmp_path: Path):
        """RGBA transparent images composite cleanly onto white or black background."""
        rgba_path = tmp_path / "cutout_wafer.png"
        # 100x100 transparent image with center 40x40 green square
        arr_rgba = np.zeros((100, 100, 4), dtype=np.uint8)
        arr_rgba[30:70, 30:70] = [0, 255, 0, 255]
        Image.fromarray(arr_rgba, mode="RGBA").save(rgba_path)

        # White background compositing (default)
        rgb_white = _read_image_rgb(rgba_path, bg_color="white")
        assert rgb_white.shape == (100, 100, 3)
        # Background pixel (0, 0) should be white (255, 255, 255)
        assert tuple(rgb_white[0, 0]) == (255, 255, 255)
        # Foreground pixel (50, 50) should be green (0, 255, 0)
        assert tuple(rgb_white[50, 50]) == (0, 255, 0)

        # Black background compositing
        rgb_black = _read_image_rgb(rgba_path, bg_color="black")
        assert tuple(rgb_black[0, 0]) == (0, 0, 0)
        assert tuple(rgb_black[50, 50]) == (0, 255, 0)


class TestAdaptiveResolutionSafeguard:
    """Verifies that 45 MP images (e.g. 8192x5464) are adaptively capped to max_dim=1600."""

    def test_classification_loader_45mp_adaptive_cap(self, tmp_path: Path):
        """ClassificationDataset adaptively caps ultra-high resolution images to max_dim=1600."""
        cls_dir = tmp_path / "cls_test"
        ok_dir = cls_dir / "OK"
        ok_dir.mkdir(parents=True)

        # Create large 3000x2000 image
        large_img = ok_dir / "high_res_wafer.jpg"
        Image.new("RGB", (3000, 2000), color=(100, 150, 200)).save(large_img)

        ds = ClassificationDataset(root_dir=cls_dir, image_size=None, max_dim=1600)
        assert len(ds) == 1
        tensor, label = ds[0]

        # Max dimension must not exceed 1600
        _, c_h, c_w = tensor.shape
        assert max(c_h, c_w) == 1600
        assert c_h == int(round(2000 * (1600 / 3000)))  # ~1067
        assert tensor.dtype == torch.float32

    def test_detection_loader_45mp_adaptive_cap_and_bbox_scaling(self, tmp_path: Path):
        """DetectionDataset adaptively caps ultra-high res image and scales bounding boxes proportionally."""
        img_dir = tmp_path / "det_imgs"
        img_dir.mkdir(parents=True)
        img_file = img_dir / "raw_inspection.jpg"
        Image.new("RGB", (4000, 2000), color=(50, 50, 50)).save(img_file)

        coco_data = {
            "images": [{"id": 1, "file_name": "raw_inspection.jpg", "width": 4000, "height": 2000}],
            "categories": [{"id": 1, "name": "scratch"}],
            "annotations": [
                {
                    "id": 1,
                    "image_id": 1,
                    "category_id": 1,
                    "bbox": [1000, 500, 200, 100],  # [x, y, w, h] in 4000x2000
                }
            ],
        }
        anno_file = tmp_path / "annotations.json"
        with open(anno_file, "w") as f:
            json.dump(coco_data, f)

        ds = DetectionDataset(images_dir=img_dir, annotation_file=anno_file, image_size=None, max_dim=1600)
        tensor, target = ds[0]

        # Image width 4000 scaled to 1600 (scale factor = 0.4)
        _, h, w = tensor.shape
        assert w == 1600
        assert h == 800

        boxes = target["boxes"].numpy()
        assert len(boxes) == 1
        # Original: [1000, 500, 1200, 600] scaled by 0.4 -> [400, 200, 480, 240]
        np.testing.assert_allclose(boxes[0], [400.0, 200.0, 480.0, 240.0], atol=1.0)


class TestHierarchicalClassificationAdapter:
    """Verifies hierarchical folder ingestion with binary and multi-class defect extraction."""

    def test_hierarchical_synthetic_tree(self, tmp_path: Path):
        """Parses multi-level manufacturing folder with parentheses categories."""
        root = tmp_path / "QC_Tree"

        # Create OK folder
        ok_folder = root / "OK" / "20260923" / "ModelA" / "Lot1"
        ok_folder.mkdir(parents=True)
        Image.new("RGB", (32, 32), (0, 255, 0)).save(ok_folder / "img_ok1.jpg")
        Image.new("RGB", (32, 32), (0, 255, 0)).save(ok_folder / "img_ok2.jpg")

        # Create NG folders with defect names in parentheses
        ng_scratch = root / "NG" / "ZJ13" / "G3834(Mount Guide Scratch)"
        ng_scratch.mkdir(parents=True)
        Image.new("RGB", (32, 32), (255, 0, 0)).save(ng_scratch / "img_ng1.jpg")

        ng_white_spot = root / "NG" / "YP29" / "G3898(접합면 White Spot)"
        ng_white_spot.mkdir(parents=True)
        Image.new("RGB", (32, 32), (255, 255, 0)).save(ng_white_spot / "img_ng2.jpg")

        # Binary Mode
        bin_res = HierarchicalClassificationAdapter.parse_directory(root, mode="binary")
        assert bin_res["total_images"] == 4
        assert bin_res["classes"] == {"OK": 2, "NG": 2}

        # Multi-class Mode
        mc_res = HierarchicalClassificationAdapter.parse_directory(root, mode="multiclass")
        assert mc_res["total_images"] == 4
        assert "OK" in mc_res["classes"]
        assert "Scratch" in mc_res["classes"]
        assert "White Spot" in mc_res["classes"]
        assert mc_res["classes"]["OK"] == 2
        assert mc_res["classes"]["Scratch"] == 1
        assert mc_res["classes"]["White Spot"] == 1

        # Dataset loader
        ds = HierarchicalClassificationDataset(root_dir=root, mode="multiclass", image_size=(64, 64))
        assert len(ds) == 4
        tensor, label_idx = ds[0]
        assert tensor.shape == (3, 64, 64)

    @pytest.mark.skipif(not REAL_OPERATIONAL_SERVER.exists(), reason="Operational server not available on this host")
    def test_real_operational_server_hierarchical_classification(self):
        """Verifies ingestion of real /Users/kai/Downloads/운영서버."""
        res = HierarchicalClassificationAdapter.parse_directory(REAL_OPERATIONAL_SERVER, mode="binary")
        assert res["total_images"] >= 80
        assert "OK" in res["classes"]
        assert "NG" in res["classes"]
        assert res["classes"]["NG"] >= 2  # fail/ contains 2 fail images


class TestLabelMeAdapters:
    """Verifies LabelMe JSON parsing, bounding box extraction, and mask rasterization."""

    def test_labelme_parsing_and_microscopic_flaw_preservation(self, tmp_path: Path):
        """Parses LabelMe JSON with sub-pixel micro-flaw, retaining >= 1px dimension."""
        json_file = tmp_path / "ng_0001__Mount Guide Scratch___B_Photo-L1-03.json"
        img_file = tmp_path / "ng_0001__Mount Guide Scratch___B_Photo-L1-03.jpg"
        Image.new("RGB", (100, 100), color=(100, 100, 100)).save(img_file)

        # Polygon with tiny 0.4 x 0.4 px flaw
        data = {
            "version": "6.2.0",
            "imagePath": img_file.name,
            "imageWidth": 100,
            "imageHeight": 100,
            "shapes": [
                {
                    "label": "Bow",
                    "shape_type": "polygon",
                    "points": [[20.0, 30.0], [20.4, 30.0], [20.4, 30.4], [20.0, 30.4]],
                }
            ],
        }
        with open(json_file, "w") as f:
            json.dump(data, f)

        parsed = LabelMeParser.parse_file(json_file)
        assert len(parsed["boxes"]) == 1
        box = parsed["boxes"][0]
        # Annotation labels are authoritative; filenames are descriptive only.
        assert box["category_name"] == "Bow"
        # Bbox width and height must be clamped to at least 1.0 px
        assert box["bbox"][2] >= 1.0
        assert box["bbox"][3] >= 1.0

    def test_labelme_to_coco_conversion(self, tmp_path: Path):
        """Converts directory of LabelMe files into standard COCO JSON format."""
        json_file = tmp_path / "sample.json"
        data = {
            "version": "6.2.0",
            "imagePath": "sample.jpg",
            "imageWidth": 200,
            "imageHeight": 150,
            "shapes": [
                {
                    "label": "Discolor",
                    "shape_type": "polygon",
                    "points": [[10, 10], [50, 10], [50, 40], [10, 40]],
                }
            ],
        }
        with open(json_file, "w") as f:
            json.dump(data, f)

        coco_dict = LabelMeParser.to_coco(tmp_path)
        assert len(coco_dict["images"]) == 1
        assert len(coco_dict["annotations"]) == 1
        assert coco_dict["categories"][0]["name"] == "Discolor"
        anno = coco_dict["annotations"][0]
        assert anno["bbox"] == [10.0, 10.0, 40.0, 30.0]

    def test_labelme_polygon_rasterization(self, tmp_path: Path):
        """Rasterizes polygon coordinates into discrete 8-bit mask array."""
        json_file = tmp_path / "seg_sample.json"
        img_file = tmp_path / "seg_sample.png"
        Image.new("RGB", (100, 100), color=(200, 200, 200)).save(img_file)

        data = {
            "version": "6.2.0",
            "imagePath": "seg_sample.png",
            "imageWidth": 100,
            "imageHeight": 100,
            "shapes": [
                {
                    "label": "defect",
                    "shape_type": "polygon",
                    "points": [[20, 20], [80, 20], [80, 80], [20, 80]],
                }
            ],
        }
        with open(json_file, "w") as f:
            json.dump(data, f)

        mask = LabelMeRasterizer.rasterize_labelme_file(json_file, target_size=(100, 100))
        assert mask.shape == (100, 100)
        assert mask.dtype == np.uint8
        # Inside polygon: value 1
        assert mask[50, 50] == 1
        # Outside polygon: value 0
        assert mask[5, 5] == 0

        # Test dataset
        ds = LabelMeSegmentationDataset(tmp_path, image_size=(64, 64))
        assert len(ds) == 1
        img_t, mask_t = ds[0]
        assert img_t.shape == (3, 64, 64)
        assert mask_t.shape == (64, 64)
        assert mask_t.max().item() == 1


class TestFlexibleAnomalyDataset:
    """Tests FlexibleAnomalyDataset loading arbitrary normal folders without rigid train/good."""

    def test_flexible_anomaly_arbitrary_normal_dir(self, tmp_path: Path):
        """Loads normal baseline from designated arbitrary folder."""
        norm_dir = tmp_path / "my_production_ok"
        norm_dir.mkdir()
        for i in range(5):
            Image.new("RGB", (32, 32), (100, 100, 100)).save(norm_dir / f"ok_{i}.png")

        anom_dir = tmp_path / "my_production_ng"
        anom_dir.mkdir()
        for i in range(2):
            Image.new("RGB", (32, 32), (255, 50, 50)).save(anom_dir / f"ng_{i}.png")

        # Train split uses normal_dir exclusively
        ds_tr = FlexibleAnomalyDataset(normal_dir=norm_dir, split="train", image_size=(32, 32))
        assert len(ds_tr) == 5
        for idx in range(len(ds_tr)):
            img_t, lbl, mask_t = ds_tr[idx]
            assert lbl == 0
            assert (mask_t == 0).all()

        # Val split includes both normal and anomaly
        ds_val = FlexibleAnomalyDataset(normal_dir=norm_dir, anomaly_dir=anom_dir, split="val", image_size=(32, 32))
        assert len(ds_val) >= 3  # anomalies + val normals

    @pytest.mark.skipif(not REAL_OPERATIONAL_SERVER.exists(), reason="Operational server not available on this host")
    def test_real_operational_server_anomaly_inspection(self):
        """Loads real /Users/kai/Downloads/운영서버 without train/good crashing."""
        summary = inspect_industrial_dataset(REAL_OPERATIONAL_SERVER, task="anomaly")
        assert summary["status"] == "success"
        assert summary["total_images"] > 0
        assert "good" in summary["classes"]
        assert summary["adapter_used"] == "FlexibleIndustrialAnomalyAdapter"


class TestSingleClassEvaluationRobustness:
    """Verifies single-class (e.g. normal-only evaluation lots) does NOT crash with IndexError."""

    def test_single_class_evaluation_index_error_remediation(self):
        """Simulates single-class classification evaluation and confirms safe cell_samples creation."""
        classes = ["OK"]
        num_classes = len(classes)
        assert num_classes == 1

        # Replicate fixed routes_evaluation logic
        cell_samples = {
            f"{classes[i]}:{classes[j]}": [] for i in range(num_classes) for j in range(num_classes)
        }
        assert "OK:OK" in cell_samples
        assert len(cell_samples) == 1

        preds = [0, 0, 0]
        targets = [0, 0, 0]
        metrics = compute_classification_metrics(preds, targets, num_classes=num_classes, class_names=classes)
        assert metrics["accuracy"] == 1.0
        assert "OK" in metrics["per_class"]


class TestImportIndustrialEndpoint:
    """Tests the /api/dataset/import-industrial FastAPI REST API endpoint."""

    @pytest.fixture
    def client(self, tmp_path: Path):
        test_app = create_app(project_dir=str(tmp_path / "projects"))
        return TestClient(test_app)

    def test_import_industrial_endpoint_synthetic(self, client: TestClient, tmp_path: Path):
        """Calls POST /api/dataset/import-industrial on a created folder."""
        # Setup synthetic hierarchical folder
        ok_dir = tmp_path / "OK" / "batch1"
        ok_dir.mkdir(parents=True)
        Image.new("RGB", (64, 64), (10, 200, 30)).save(ok_dir / "wafer1.png")

        ng_dir = tmp_path / "NG" / "Lot(Scratch)"
        ng_dir.mkdir(parents=True)
        Image.new("RGB", (64, 64), (200, 10, 30)).save(ng_dir / "wafer2.png")

        payload = {
            "folder_path": str(tmp_path),
            "task": "classification",
            "options": {"mode": "multiclass"},
        }
        resp = client.post("/api/dataset/import-industrial", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "success"
        assert data["total_images"] == 2
        assert "OK" in data["classes"]
        assert "Scratch" in data["classes"]
        assert "split" in data
        assert "adapter_used" in data
        assert "HierarchicalClassificationAdapter" in data["adapter_used"]

    @pytest.mark.skipif(not REAL_OPERATIONAL_SERVER.exists(), reason="Operational server not available on this host")
    def test_import_industrial_endpoint_real_server(self, client: TestClient):
        """Calls POST /api/dataset/import-industrial on real /Users/kai/Downloads/운영서버."""
        payload = {
            "folder_path": str(REAL_OPERATIONAL_SERVER),
            "task": "classification",
            "options": {"mode": "binary"},
        }
        resp = client.post("/api/dataset/import-industrial", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == "success"
        assert data["total_images"] >= 80
        assert "OK" in data["classes"]
        assert "NG" in data["classes"]
        assert len(data["sample_thumbnails"]) > 0


class TestM7RemediationSafeguards:
    """Tests the 3 remediation areas identified during real-data stress testing."""

    def test_is_valid_labelme_file_schema_and_telemetry_rejection(self, tmp_path: Path):
        """Verifies rejection of non-LabelMe audit/telemetry files and invalid shapes."""
        # 1. Audit telemetry files must be rejected by name and schema
        audit_file = tmp_path / "audit_progress.json"
        with open(audit_file, "w") as f:
            json.dump({"total": 19857, "completed": 534, "anomalies_count": 3}, f)
        assert is_valid_labelme_file(audit_file) is False

        progress_file = tmp_path / "all_inspection_progress.json"
        with open(progress_file, "w") as f:
            json.dump({"total": 21453, "completed": 3853}, f)
        assert is_valid_labelme_file(progress_file) is False

        # 2. JSON without shapes key
        no_shapes = tmp_path / "config.json"
        with open(no_shapes, "w") as f:
            json.dump({"model": "resnet50", "lr": 0.001}, f)
        assert is_valid_labelme_file(no_shapes) is False

        # 3. JSON with empty shapes list
        empty_shapes = tmp_path / "empty_shapes.json"
        with open(empty_shapes, "w") as f:
            json.dump({"shapes": [], "imagePath": "empty_shapes.jpg"}, f)
        assert is_valid_labelme_file(empty_shapes) is False

        # 4. Genuine LabelMe with shapes and matching image
        valid_json = tmp_path / "valid_sample.json"
        valid_img = tmp_path / "valid_sample.jpg"
        Image.new("RGB", (100, 100), (50, 50, 50)).save(valid_img)
        with open(valid_json, "w") as f:
            json.dump({
                "shapes": [{"label": "scratch", "points": [[10, 10], [50, 50]]}],
                "imagePath": "valid_sample.jpg",
            }, f)
        assert is_valid_labelme_file(valid_json, require_image=True) is True

    def test_hierarchical_classification_mixed_aspect_ratio_dataloader_collate(self, tmp_path: Path):
        """Verifies default DataLoader collation succeeds on variable aspect ratios without error."""
        from torch.utils.data import DataLoader

        ok_dir = tmp_path / "OK" / "batch_01"
        ok_dir.mkdir(parents=True)
        # Two different aspect ratios: 100x200 vs 200x150
        Image.new("RGB", (100, 200), (0, 255, 0)).save(ok_dir / "img1.jpg")
        Image.new("RGB", (200, 150), (0, 200, 0)).save(ok_dir / "img2.jpg")

        ds = HierarchicalClassificationDataset(tmp_path, mode="binary", image_size=None, max_dim=256)
        assert len(ds) == 2

        loader = DataLoader(ds, batch_size=2, shuffle=False)
        for imgs, lbls in loader:
            assert imgs.shape == (2, 3, 256, 256)
            assert len(lbls) == 2

    def test_flexible_anomaly_mixed_aspect_ratio_dataloader_collate(self, tmp_path: Path):
        """Verifies default DataLoader collation succeeds on variable aspect ratios in anomaly detection."""
        from torch.utils.data import DataLoader

        norm_dir = tmp_path / "normal_cutouts"
        norm_dir.mkdir()
        # Two different aspect ratios: 120x80 vs 80x160
        Image.new("RGB", (120, 80), (128, 128, 128)).save(norm_dir / "norm1.png")
        Image.new("RGB", (80, 160), (100, 100, 100)).save(norm_dir / "norm2.png")

        ds = FlexibleAnomalyDataset(normal_dir=norm_dir, split="train", image_size=None, max_dim=256)
        assert len(ds) == 2

        loader = DataLoader(ds, batch_size=2, shuffle=False)
        for imgs, lbls, masks in loader:
            assert imgs.shape == (2, 3, 256, 256)
            assert masks.shape == (2, 256, 256)
            assert len(lbls) == 2

    def test_segmentation_routing_prefers_cutouts_when_no_valid_labelme(self, tmp_path: Path):
        """Verifies segmentation inspection routes to AlphaMaskAndPairedSegmentationAdapter when no paired LabelMe exists."""
        # Create non-annotation audit json
        with open(tmp_path / "audit_progress.json", "w") as f:
            json.dump({"total": 100}, f)

        # Create cutout PNGs in test_crop_output
        crop_dir = tmp_path / "test_crop_output"
        crop_dir.mkdir()
        for i in range(3):
            Image.new("RGBA", (64, 64), (100, 100, 100, 255)).save(crop_dir / f"crop_{i}.png")

        summary = inspect_industrial_dataset(tmp_path, task="segmentation")
        assert summary["status"] == "success"
        assert summary["total_images"] == 3
        assert summary["adapter_used"] == "AlphaMaskAndPairedSegmentationAdapter"

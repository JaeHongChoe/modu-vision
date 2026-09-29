"""
backend/tests/test_detection.py

Comprehensive test suite for Task 2: Object Detection Engine.
Validates:
  - Faster R-CNN models (MobileNetV3 FPN & ResNet50 FPN v2)
  - Custom FastRCNNPredictor head replacement
  - MPS-safe RoI pooling wrapper
  - Pure-Python/PyTorch vectorized mAP@0.5 and mAP@0.5:0.95 evaluator
  - Safe NMS on CPU/MPS with empty tensor edge cases
  - Bounding box overlay rendering with confidence threshold filtering
"""

from __future__ import annotations

import numpy as np
import pytest
import torch
import torchvision.ops as ops

from backend.engine.detection import (
    build_detection_model,
    compute_ap_coco,
    create_detection_model,
    draw_detection_overlays,
    evaluate_detections_map,
    safe_nms,
)


class TestDetectionModelFactory:
    """Test suite for Faster R-CNN architecture construction and custom predictor heads."""

    def test_faster_rcnn_mobilenet_v3_fast_preset(self):
        model = create_detection_model(preset="fast", num_classes=4, pretrained=False)
        # Background = 0, 3 defect classes
        assert model.roi_heads.box_predictor.cls_score.out_features == 4
        assert model.roi_heads.box_predictor.bbox_pred.out_features == 16  # 4 * 4 coordinates

    def test_faster_rcnn_resnet50_precision_preset(self):
        model = create_detection_model(preset="precision", num_classes=3, pretrained=False)
        assert model.roi_heads.box_predictor.cls_score.out_features == 3
        assert model.roi_heads.box_predictor.bbox_pred.out_features == 12

    def test_mps_safe_roi_pool_wrapper_attached(self):
        model = create_detection_model(preset="fast", num_classes=2, pretrained=False)
        assert hasattr(model.roi_heads.box_roi_pool, "forward")
        assert model.roi_heads.box_roi_pool.forward.__name__ == "mps_safe_roi_pool"

    def test_detection_forward_train_mode(self):
        model = create_detection_model(preset="fast", num_classes=2, pretrained=False)
        model.train()

        imgs = [torch.rand(3, 128, 128), torch.rand(3, 128, 128)]
        targets = [
            {
                "boxes": torch.tensor([[10.0, 10.0, 50.0, 50.0]], dtype=torch.float32),
                "labels": torch.tensor([1], dtype=torch.int64),
            },
            {
                "boxes": torch.tensor([[20.0, 20.0, 60.0, 60.0]], dtype=torch.float32),
                "labels": torch.tensor([1], dtype=torch.int64),
            },
        ]

        loss_dict = model(imgs, targets)
        assert isinstance(loss_dict, dict)
        assert "loss_classifier" in loss_dict
        assert "loss_box_reg" in loss_dict
        total_loss = sum(v for v in loss_dict.values())
        assert torch.isfinite(total_loss)

    def test_detection_forward_eval_mode(self):
        model = create_detection_model(preset="fast", num_classes=3, pretrained=False)
        model.eval()

        imgs = [torch.rand(3, 128, 128)]
        with torch.no_grad():
            preds = model(imgs)

        assert len(preds) == 1
        assert "boxes" in preds[0]
        assert "scores" in preds[0]
        assert "labels" in preds[0]


class TestSafeNMS:
    """Test suite for Non-Maximum Suppression and edge cases."""

    def test_safe_nms_standard(self):
        boxes = torch.tensor([
            [10.0, 10.0, 50.0, 50.0],
            [12.0, 12.0, 48.0, 48.0],  # High overlap with box 0
            [100.0, 100.0, 150.0, 150.0],  # Disjoint
        ])
        scores = torch.tensor([0.9, 0.7, 0.8])
        keep = safe_nms(boxes, scores, iou_threshold=0.5)

        assert len(keep) == 2
        assert 0 in keep
        assert 2 in keep
        assert 1 not in keep

    def test_safe_nms_empty_boxes(self):
        boxes = torch.zeros((0, 4), dtype=torch.float32)
        scores = torch.zeros((0,), dtype=torch.float32)
        keep = safe_nms(boxes, scores, iou_threshold=0.5)
        assert len(keep) == 0


class TestEvaluatorMAP:
    """Test suite for COCO AP and mAP@0.5, mAP@0.5:0.95 vectorized evaluation."""

    def test_compute_ap_coco_perfect_curve(self):
        recalls = np.array([0.2, 0.5, 0.8, 1.0])
        precisions = np.array([1.0, 1.0, 1.0, 1.0])
        ap = compute_ap_coco(recalls, precisions)
        assert ap == pytest.approx(1.0, abs=1e-3)

    def test_compute_ap_coco_empty(self):
        ap = compute_ap_coco(np.array([]), np.array([]))
        assert ap == 0.0

    def test_evaluate_detections_map_perfect_match(self):
        boxes = torch.tensor([[20.0, 20.0, 60.0, 60.0]], dtype=torch.float32)
        labels = torch.tensor([1], dtype=torch.int64)
        scores = torch.tensor([0.99], dtype=torch.float32)

        predictions = [{"boxes": boxes, "scores": scores, "labels": labels}]
        targets = [{"boxes": boxes, "labels": labels}]

        results = evaluate_detections_map(
            predictions=predictions,
            targets=targets,
            num_classes=2,
            class_names={1: "solder_bridge"},
        )

        assert results["mAP_50"] == 1.0
        assert results["mAP_50_95"] == 1.0
        assert "solder_bridge" in results["class_ap50"]

    def test_evaluate_detections_map_zero_overlap(self):
        pred_boxes = torch.tensor([[10.0, 10.0, 30.0, 30.0]], dtype=torch.float32)
        gt_boxes = torch.tensor([[100.0, 100.0, 150.0, 150.0]], dtype=torch.float32)

        predictions = [{"boxes": pred_boxes, "scores": torch.tensor([0.9]), "labels": torch.tensor([1])}]
        targets = [{"boxes": gt_boxes, "labels": torch.tensor([1])}]

        results = evaluate_detections_map(predictions=predictions, targets=targets, num_classes=2)
        assert results["mAP_50"] == 0.0
        assert results["mAP_50_95"] == 0.0

    def test_evaluate_detections_map_empty_predictions(self):
        targets = [{"boxes": torch.tensor([[10.0, 10.0, 30.0, 30.0]]), "labels": torch.tensor([1])}]
        predictions = [{"boxes": torch.zeros((0, 4)), "scores": torch.zeros((0,)), "labels": torch.zeros((0,), dtype=torch.long)}]

        results = evaluate_detections_map(predictions=predictions, targets=targets, num_classes=2)
        assert results["mAP_50"] == 0.0


class TestDetectionVisualizer:
    """Test suite for detection bounding box and banner overlay drawing."""

    def test_draw_detection_overlays_filters_threshold(self):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        boxes = np.array([[10, 10, 50, 50], [60, 60, 90, 90]])
        scores = np.array([0.9, 0.3])  # Box 1 below 0.5 threshold
        labels = np.array([1, 1])

        annotated = draw_detection_overlays(
            image_rgb=img,
            boxes=boxes,
            scores=scores,
            labels=labels,
            class_names={1: "scratch"},
            confidence_threshold=0.5,
        )

        assert annotated.shape == (100, 100, 3)
        # Verify box 0 was drawn (non-zero pixels in box region)
        assert np.count_nonzero(annotated[10:50, 10:50]) > 0
        # Verify box 1 was filtered out (zeros in box 1 region)
        assert np.count_nonzero(annotated[60:90, 60:90]) == 0

    def test_draw_detection_overlays_tensor_inputs(self):
        img = np.zeros((120, 120, 3), dtype=np.uint8)
        boxes = torch.tensor([[15.0, 15.0, 45.0, 45.0]])
        scores = torch.tensor([0.85])
        labels = torch.tensor([1])

        annotated = draw_detection_overlays(
            image_rgb=img,
            boxes=boxes,
            scores=scores,
            labels=labels,
            class_names={1: "void"},
            confidence_threshold=0.5,
        )
        assert np.count_nonzero(annotated) > 0

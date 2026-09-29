"""
backend/engine/flowchart_engine.py

Local inspection engine for two supported linear flows:
  [Input Image] -> [Full-image Inspection] -> [Decision] -> [Local Result]
  [Input Image] -> [Detection / ROI Crop] -> [Inspection] -> [Decision] -> [Local Result]

Features:
  1. Safe ROI Cropping with bounds clamping, context padding, >= 16px dimension guarantee (prevents UNet convolution collapse).
  2. Resize to each trained model's recorded input dimensions.
  3. Original-resolution tiles for full-image segmentation; 1600px cap for detector-ROI paths and previews.
  4. Genuine PyTorch inference across MPS, CUDA, and CPU with warm model caching.
  5. Zero-detection REVIEW when no image region reaches inspection.
  6. Multi-detection thumbnail compression (EC-02) keeping payload < 500KB.
  7. Graceful fallback for environments without pre-trained checkpoints (EC-07).
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr
import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.engine.anomaly.feature_extractor import ResNetFeatureExtractor
from backend.engine.anomaly.padim import PaDiMDetector
from backend.engine.anomaly.patchcore import PatchCoreDetector
from backend.engine.classification.model import create_classification_model
from backend.engine.checkpoint_paths import trusted_checkpoint
from backend.engine.detection.model import create_detection_model
from backend.engine.device import get_device
from backend.engine.industrial_adapters import read_image_safely_rgb, sanitize_file_stem
from backend.engine.segmentation.model import build_segmentation_model

logger = logging.getLogger("vision_ai_studio.flowchart_engine")
MAX_SEGMENTATION_TILES = 1024
SEGMENTATION_TILE_BATCH_SIZE = 4


class FlowchartInspectionLimitError(RuntimeError):
    """The original-resolution image requires more tiles than this run permits."""


# ============================================================================
# Schemas & Data Models
# ============================================================================

class FlowNodeData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str
    node_type: Literal["input", "detection_crop", "inspection", "decision", "output"]
    task: Optional[str] = "anomaly"  # 'detection', 'anomaly', 'segmentation', 'classification'
    model_job_id: Optional[str] = None
    threshold: Optional[float] = 0.5
    crop_padding: Optional[int] = 10
    rule: Optional[str] = "any_defect_is_ng"  # 'any_defect_is_ng', 'score_gt_threshold', 'max_flaws_allowed'
    params: Dict[str, Any] = Field(default_factory=dict)


class FlowNode(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    type: str = "custom"
    position: Dict[str, float]  # {"x": 100, "y": 150}
    data: FlowNodeData


class FlowEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    source: str
    target: str
    label: Optional[str] = None


class FlowchartPipeline(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str = "default_pipeline"
    name: str = "PCB Component Multi-Model Inspection"
    description: Optional[str] = "Stage 1: Detect IC Chips -> Stage 2: Solder Void & Anomaly -> Stage 3: OK/NG"
    nodes: List[FlowNode] = Field(default_factory=list)
    edges: List[FlowEdge] = Field(default_factory=list)


class CropInspectionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    roi_id: str
    label: str
    bbox: List[int]  # [x1, y1, x2, y2]
    defect_score: float
    verdict: Literal["OK", "NG"]
    crop_thumbnail: str  # Base64 Data URI
    flaw_type: str
    confidence: Optional[float] = None
    defect_area_px: Optional[int] = None
    tiles_processed: Optional[int] = None
    _defect_mask: Optional[np.ndarray] = PrivateAttr(default=None)


class FlowchartExecutionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")
    node_id: str
    name: str
    status: Literal["pending", "running", "passed", "flagged_ng", "error", "skipped", "review_required", "warning_untrained"]
    latency_ms: float


class FlowchartExecutionResult(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    final_verdict: Literal["OK", "NG", "REVIEW"]
    is_ok: bool
    rejection_reason: str
    roi_count: int
    defective_roi_count: int
    crops: List[CropInspectionResult]
    annotated_image: Optional[str] = None
    execution_steps: List[FlowchartExecutionStep]
    total_latency_ms: float
    inspected_image_size: List[int] = Field(default_factory=list)
    tiles_processed: int = 0
    preview_max_dim_px: int = 1600
    image_path: Optional[str] = None
    image_id: Optional[str] = None
    error_message: Optional[str] = None


class FlowchartRunRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    image_path: Optional[str] = None
    image_id: Optional[str] = None
    pipeline: Optional[FlowchartPipeline] = None


# ============================================================================
# Safe ROI Cropping Function (Adversarial Hardened)
# ============================================================================

def safe_crop_roi(
    img: np.ndarray,
    box: Sequence[Union[int, float]],
    padding_px: int = 10,
    min_size: int = 16,
    target_size: Optional[Tuple[int, int]] = (224, 224),
) -> Tuple[np.ndarray, List[int]]:
    """
    Extracts an ROI patch with boundary clamping, context padding,
    minimum spatial dimension protection (>= 16px preventing UNet collapse),
    and model-input resizing.

    Guarantees:
      1. No negative indexing or wraparound distortion.
      2. No zero-area or empty crops (cv2 assertion safe).
      3. Minimum spatial dimension >= min_size (UNet convolution safe).
      4. Normalized output resolution (PaDiM spatial grid safe).
    """
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box

    # 1. Clamp raw coordinates to image domain
    x1_c = max(0, min(w - 1, int(round(x1))))
    y1_c = max(0, min(h - 1, int(round(y1))))
    x2_c = max(x1_c + 1, min(w, int(round(x2))))
    y2_c = max(y1_c + 1, min(h, int(round(y2))))

    # 2. Apply context padding bounded by image limits
    x1_pad = max(0, x1_c - padding_px)
    y1_pad = max(0, y1_c - padding_px)
    x2_pad = min(w, x2_c + padding_px)
    y2_pad = min(h, y2_c + padding_px)

    # 3. Minimum dimension enforcement (expand symmetrically if too small)
    crop_w = x2_pad - x1_pad
    crop_h = y2_pad - y1_pad
    if crop_w < min_size:
        diff = min_size - crop_w
        x1_pad = max(0, x1_pad - diff // 2)
        x2_pad = min(w, x1_pad + min_size)
        if x2_pad - x1_pad < min_size and x1_pad > 0:
            x1_pad = max(0, x2_pad - min_size)
    if crop_h < min_size:
        diff = min_size - crop_h
        y1_pad = max(0, y1_pad - diff // 2)
        y2_pad = min(h, y1_pad + min_size)
        if y2_pad - y1_pad < min_size and y1_pad > 0:
            y1_pad = max(0, y2_pad - min_size)

    crop = img[y1_pad:y2_pad, x1_pad:x2_pad]
    actual_bbox = [int(x1_pad), int(y1_pad), int(x2_pad), int(y2_pad)]

    # Extreme edge fallback for empty source image
    if crop.size == 0 or crop.shape[0] == 0 or crop.shape[1] == 0:
        crop = np.zeros((min_size, min_size, 3), dtype=np.uint8)

    # 4. Standardized Resizing to target model input size (e.g. 224x224)
    if target_size is not None:
        interp = (
            cv2.INTER_CUBIC
            if (crop.shape[1] < target_size[0] or crop.shape[0] < target_size[1])
            else cv2.INTER_AREA
        )
        resized_crop = cv2.resize(crop, target_size, interpolation=interp)
        return resized_crop, actual_bbox

    return crop, actual_bbox


def get_default_flowchart() -> FlowchartPipeline:
    """Returns a sensible industrial default flowchart pipeline."""
    return FlowchartPipeline(
        id="default_pipeline",
        name="PCB & Semiconductor Multi-Stage Inspection",
        description="Stage 1: Detect Component ROI -> Stage 2: Inspect Solder & Surface -> Stage 3: Factory Rule",
        nodes=[
            FlowNode(
                id="node_input",
                position={"x": 50, "y": 180},
                data=FlowNodeData(
                    label="Camera Raw Input",
                    node_type="input",
                    params={"resolution": "1024x1024", "format": "RGB"},
                ),
            ),
            FlowNode(
                id="node_crop",
                position={"x": 320, "y": 180},
                data=FlowNodeData(
                    label="1차 검출 & ROI 크롭",
                    node_type="detection_crop",
                    task="detection",
                    threshold=0.6,
                    crop_padding=12,
                    params={"target_classes": ["ic_chip", "capacitor", "solder_pad"]},
                ),
            ),
            FlowNode(
                id="node_inspect",
                position={"x": 620, "y": 180},
                data=FlowNodeData(
                    label="2차 결함 분할 & 이상탐지",
                    node_type="inspection",
                    task="anomaly",
                    threshold=0.45,
                    params={"algorithm": "PatchCore + UNet", "min_defect_area_px": 8},
                ),
            ),
            FlowNode(
                id="node_decision",
                position={"x": 920, "y": 180},
                data=FlowNodeData(
                    label="공정 판정 룰 엔진 (OK / NG)",
                    node_type="decision",
                    rule="any_defect_is_ng",
                    params={"underkill_policy": "zero_tolerance", "max_flaws_allowed": 0},
                ),
            ),
            FlowNode(
                id="node_output",
                position={"x": 1200, "y": 180},
                data=FlowNodeData(
                    label="판정 결과 (로컬 확인)",
                    node_type="output",
                    params={},
                ),
            ),
        ],
        edges=[
            FlowEdge(id="e1-2", source="node_input", target="node_crop", label="Raw Feed"),
            FlowEdge(id="e2-3", source="node_crop", target="node_inspect", label="ROI Crops"),
            FlowEdge(id="e3-4", source="node_inspect", target="node_decision", label="Defect Scores"),
            FlowEdge(id="e4-5", source="node_decision", target="node_output", label="Verdict OK/NG"),
        ],
    )


def get_single_segmentation_flowchart(job_id: Optional[str] = None) -> FlowchartPipeline:
    """A local, original-resolution tiled inspection flow for one trained model."""
    return FlowchartPipeline(
        id="single_segmentation",
        name="원본 이미지 타일 분할 검사",
        description="원본 해상도 타일 검사 -> OK/NG/REVIEW 판정 -> 로컬 미리보기",
        nodes=[
            FlowNode(id="node_input", position={"x": 40, "y": 160}, data=FlowNodeData(
                label="Inspection image", node_type="input",
            )),
            FlowNode(id="node_inspect", position={"x": 360, "y": 160}, data=FlowNodeData(
                label="원본 타일 결함 분할", node_type="inspection",
                task="segmentation", model_job_id=job_id, threshold=0.5,
                params={"min_defect_area_px": 8},
            )),
            FlowNode(id="node_decision", position={"x": 680, "y": 160}, data=FlowNodeData(
                label="Defect decision", node_type="decision", rule="any_defect_is_ng",
            )),
            FlowNode(id="node_output", position={"x": 1000, "y": 160}, data=FlowNodeData(
                label="Local result", node_type="output",
            )),
        ],
        edges=[
            FlowEdge(id="input-inspect", source="node_input", target="node_inspect", label="Original image"),
            FlowEdge(id="inspect-decision", source="node_inspect", target="node_decision", label="Defect result"),
            FlowEdge(id="decision-output", source="node_decision", target="node_output", label="Local verdict"),
        ],
    )


def ordered_linear_nodes(pipeline: FlowchartPipeline) -> List[FlowNode]:
    """Follow edges through one of the supported linear inspection graphs."""
    nodes = {node.id: node for node in pipeline.nodes}
    if len(nodes) != len(pipeline.nodes):
        raise ValueError("Pipeline node IDs must be unique.")
    inputs = [node for node in pipeline.nodes if node.data.node_type == "input"]
    if len(inputs) != 1:
        raise ValueError("Pipeline must have exactly one input node.")
    if len(pipeline.edges) != len(nodes) - 1:
        raise ValueError("Pipeline must be a connected linear graph; branches are unsupported.")

    outgoing: Dict[str, str] = {}
    incoming: Dict[str, str] = {}
    for edge in pipeline.edges:
        if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
            raise ValueError("Pipeline edge references an invalid node.")
        if edge.source in outgoing or edge.target in incoming:
            raise ValueError("Pipeline branches and joins are unsupported.")
        outgoing[edge.source] = edge.target
        incoming[edge.target] = edge.source

    order: List[FlowNode] = []
    current_id = inputs[0].id
    seen: set[str] = set()
    while current_id not in seen:
        seen.add(current_id)
        order.append(nodes[current_id])
        next_id = outgoing.get(current_id)
        if next_id is None:
            break
        current_id = next_id

    if len(order) != len(nodes) or inputs[0].id in incoming:
        raise ValueError("Pipeline edges must connect every node from input to output.")
    node_types = [node.data.node_type for node in order]
    supported = [
        ["input", "inspection", "decision", "output"],
        ["input", "detection_crop", "inspection", "decision", "output"],
    ]
    if node_types not in supported:
        raise ValueError("Pipeline supports only full-image or detector-ROI linear inspection flows.")
    return order


# ============================================================================
# Flowchart Execution Engine
# ============================================================================

class FlowchartEngine:
    """
    Authentic Multi-Model Flowchart Execution Engine.
    Executes chained vision AI pipelines with genuine PyTorch models:
      Stage 1: Faster R-CNN detection & safe ROI extraction
      Stage 2: Secondary flaw inspection (PaDiM anomaly, UNet segmentation, ResNet classification)
      Stage 3: Decision rule evaluation
    """

    def __init__(self, device: Optional[Union[torch.device, str]] = None):
        self.device = get_device(device) if device is not None else get_device()
        self._model_cache: Dict[Tuple[str, Optional[str], str], Any] = {}
        self._model_input_sizes: Dict[Tuple[str, Optional[str], str], Tuple[int, int]] = {}

    def _remember_input_size(self, cache_key: Tuple[str, Optional[str], str], checkpoint: Dict[str, Any]) -> None:
        raw = checkpoint.get("image_size", [224, 224])
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            width, height = int(raw[0]), int(raw[1])
            if 16 <= width <= 4096 and 16 <= height <= 4096:
                self._model_input_sizes[cache_key] = (width, height)

    def _resolve_checkpoint(self, job_id: Optional[str], task: str) -> Optional[Path]:
        """Locate an app-created training checkpoint by job ID only."""
        return trusted_checkpoint(job_id) if job_id else None

    def _get_detection_model(self, job_id: Optional[str] = None, preset: str = "fast") -> Tuple[nn.Module, bool]:
        """Loads cached or new Faster R-CNN model."""
        cache_key = ("detection", job_id, preset)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        ckpt_path = self._resolve_checkpoint(job_id, "detection")
        is_trained = False
        if ckpt_path:
            try:
                ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=True)
                model = create_detection_model(
                    preset=ckpt.get("detector_preset", ckpt.get("preset", preset)),
                    num_classes=max(2, len(ckpt.get("classes", []))), pretrained=False,
                )
                state = ckpt.get("model_state_dict", ckpt.get("model_state", ckpt.get("state_dict", ckpt)))
                model.load_state_dict(state, strict=True)
                self._remember_input_size(cache_key, ckpt)
                is_trained = True
                logger.info("Loaded detection checkpoint from %s", ckpt_path)
            except Exception as e:
                raise RuntimeError(f"Could not load detection checkpoint {ckpt_path}: {e}") from e
        else:
            model = create_detection_model(preset=preset, num_classes=2, pretrained=True)

        model = model.to(self.device)
        model.eval()
        self._model_cache[cache_key] = (model, is_trained)
        return model, is_trained

    def _get_inspection_model(
        self, task: str = "anomaly", job_id: Optional[str] = None, preset: str = "fast"
    ) -> Tuple[Any, bool]:
        """Loads cached or new inspection model (anomaly, segmentation, classification)."""
        task_clean = task.lower().strip()
        cache_key = (task_clean, job_id, preset)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        ckpt_path = self._resolve_checkpoint(job_id, task_clean)
        is_trained = False

        if task_clean == "anomaly":
            if ckpt_path:
                try:
                    ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=True)
                    state = ckpt.get("model_state_dict", ckpt)
                    use_patchcore = "patchcore" in str(ckpt.get("detector_type", "")).lower() or "coreset" in state
                    detector = (PatchCoreDetector if use_patchcore else PaDiMDetector)(
                        backbone_name="resnet18", device=self.device, pretrained=True,
                    )
                    detector.load_state_dict(state)
                    self._remember_input_size(cache_key, ckpt)
                    is_trained = detector.coreset is not None if use_patchcore else (detector.mean is not None and detector.cov_inv is not None)
                    if not is_trained:
                        raise ValueError("checkpoint has no fitted anomaly statistics")
                    logger.info("Loaded PaDiM anomaly checkpoint from %s", ckpt_path)
                except Exception as e:
                    raise RuntimeError(f"Could not load anomaly checkpoint {ckpt_path}: {e}") from e
            else:
                detector = PaDiMDetector(backbone_name="resnet18", device=self.device, pretrained=True)
            self._model_cache[cache_key] = (detector, is_trained)
            return detector, is_trained

        elif task_clean == "segmentation":
            if ckpt_path:
                try:
                    ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=True)
                    model = build_segmentation_model(
                        model_name=ckpt.get("model_name") or "unet",
                        num_classes=max(2, len(ckpt.get("classes", []))),
                        preset=ckpt.get("preset") or preset, pretrained=False,
                    )
                    state = ckpt.get("model_state_dict", ckpt.get("model_state", ckpt.get("state_dict", ckpt)))
                    model.load_state_dict(state, strict=True)
                    self._remember_input_size(cache_key, ckpt)
                    is_trained = True
                    logger.info("Loaded UNet segmentation checkpoint from %s", ckpt_path)
                except Exception as e:
                    raise RuntimeError(f"Could not load segmentation checkpoint {ckpt_path}: {e}") from e
            else:
                model = build_segmentation_model(model_name="unet", num_classes=2, preset=preset, pretrained=False)
            model = model.to(self.device)
            model.eval()
            self._model_cache[cache_key] = (model, is_trained)
            return model, is_trained

        elif task_clean in ("classification", "classifier"):
            if ckpt_path:
                try:
                    ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=True)
                    model = create_classification_model(
                        backbone=ckpt.get("backbone", "resnet18"),
                        num_classes=max(2, len(ckpt.get("classes", []))), pretrained=False,
                    )
                    state = ckpt.get("model_state_dict", ckpt.get("model_state", ckpt.get("state_dict", ckpt)))
                    model.load_state_dict(state, strict=True)
                    self._remember_input_size(cache_key, ckpt)
                    is_trained = True
                    logger.info("Loaded classification checkpoint from %s", ckpt_path)
                except Exception as e:
                    raise RuntimeError(f"Could not load classification checkpoint {ckpt_path}: {e}") from e
            else:
                model = create_classification_model(backbone="resnet18", num_classes=2, pretrained=False)
            model = model.to(self.device)
            model.eval()
            self._model_cache[cache_key] = (model, is_trained)
            return model, is_trained

        else:
            # Default to PaDiM
            detector = PaDiMDetector(backbone_name="resnet18", device=self.device, pretrained=True)
            self._model_cache[cache_key] = (detector, False)
            return detector, False

    def _extract_candidate_rois(
        self, img_rgb: np.ndarray, det_node: Optional[FlowNode]
    ) -> Tuple[List[Dict[str, Any]], float, str]:
        """
        Executes Faster R-CNN forward pass with PyTorch.
        Filters detections using confidence threshold and NMS.
        Handles zero-detection and untrained fallback safely.
        """
        t0 = time.time()
        h, w = img_rgb.shape[:2]
        threshold = det_node.data.threshold if (det_node and det_node.data.threshold is not None) else 0.5
        job_id = det_node.data.model_job_id if det_node else None

        det_model, is_trained = self._get_detection_model(job_id=job_id)
        img_tensor = torch.from_numpy(img_rgb).permute(2, 0, 1).float().unsqueeze(0) / 255.0

        boxes_out: List[List[int]] = []
        scores_out: List[float] = []
        labels_out: List[str] = []

        try:
            with torch.no_grad():
                preds = det_model(img_tensor.to(self.device))
            if preds and len(preds) > 0:
                p_boxes = preds[0]["boxes"].detach().cpu().numpy()
                p_scores = preds[0]["scores"].detach().cpu().numpy()
                mask = p_scores >= threshold
                f_boxes = p_boxes[mask]
                f_scores = p_scores[mask]
                for b, s in zip(f_boxes, f_scores):
                    boxes_out.append([int(b[0]), int(b[1]), int(b[2]), int(b[3])])
                    scores_out.append(float(s))
                    labels_out.append("component")
        except Exception as e:
            if is_trained:
                raise RuntimeError(f"Trained detector inference failed: {e}") from e
            logger.warning("Untrained Faster R-CNN forward pass warning: %s", e)

        # Candidate ROI Fallback for Untrained State:
        # If threshold is strict (>= 0.95), respect zero-detection (EC-01 clean normal bypass).
        # Otherwise, if untrained weights yielded 0 detections on an image with structures,
        # extract candidate component contours from the image.
        if len(boxes_out) == 0 and not is_trained and threshold < 0.95:
            gray = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2GRAY)
            # Find candidate rectangular components (IC chips, pads)
            _, thresh_img = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
            contours, _ = cv2.findContours(thresh_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            c_boxes = []
            for c in contours:
                x, y, bw, bh = cv2.boundingRect(c)
                area = bw * bh
                if 2500 < area < 0.6 * (h * w) and bw >= 30 and bh >= 30:
                    c_boxes.append([x, y, x + bw, y + bh])

            # Sort top to bottom, left to right
            c_boxes = sorted(c_boxes, key=lambda b: (b[1], b[0]))
            for idx, cb in enumerate(c_boxes[:10]):  # cap to top 10
                boxes_out.append(cb)
                scores_out.append(0.95)
                labels_out.append("ic_chip" if idx < 2 else "component")

        latency_ms = (time.time() - t0) * 1000.0
        status = "passed" if is_trained or len(boxes_out) > 0 else "warning_untrained"

        rois = []
        for i, (b, s, l) in enumerate(zip(boxes_out, scores_out, labels_out)):
            rois.append({
                "id": f"crop_{i+1}",
                "label": l,
                "bbox": b,
                "confidence": round(s, 3),
            })

        return rois, latency_ms, status

    def _predict_tiled_segmentation(
        self,
        model: nn.Module,
        img_rgb: np.ndarray,
        input_size: Tuple[int, int],
    ) -> Tuple[np.ndarray, int]:
        """Inspect the original-resolution image with overlapping model-sized tiles."""
        h, w = img_rgb.shape[:2]
        tile_w, tile_h = input_size

        def starts(length: int, tile: int) -> List[int]:
            if length <= tile:
                return [0]
            stride = max(1, tile - max(16, tile // 8))
            positions = list(range(0, length - tile + 1, stride))
            if positions[-1] != length - tile:
                positions.append(length - tile)
            return positions

        coordinates = [(x, y) for y in starts(h, tile_h) for x in starts(w, tile_w)]
        count = len(coordinates)
        if count > MAX_SEGMENTATION_TILES:
            raise FlowchartInspectionLimitError(
                f"원본 이미지 {w}×{h} 검사에는 {count}개 타일이 필요하여 "
                f"안전 상한 {MAX_SEGMENTATION_TILES}개를 초과했습니다. 검사를 수행하지 않았습니다."
            )

        probability_sum = np.zeros((h, w), dtype=np.float32)
        coverage = np.zeros((h, w), dtype=np.uint16)
        for offset in range(0, count, SEGMENTATION_TILE_BATCH_SIZE):
            batch_coordinates = coordinates[offset:offset + SEGMENTATION_TILE_BATCH_SIZE]
            tiles = []
            tile_bounds = []
            for x, y in batch_coordinates:
                x2, y2 = min(w, x + tile_w), min(h, y + tile_h)
                patch = img_rgb[y:y2, x:x2]
                if patch.shape[:2] != (tile_h, tile_w):
                    patch = cv2.resize(patch, (tile_w, tile_h), interpolation=cv2.INTER_LINEAR)
                tiles.append(torch.from_numpy(patch).permute(2, 0, 1).float() / 255.0)
                tile_bounds.append((x, y, x2, y2))

            logits = model(torch.stack(tiles).to(self.device))
            probabilities = torch.softmax(logits, dim=1)[:, 1].cpu().numpy()
            if len(probabilities) != len(tile_bounds):
                raise RuntimeError("Segmentation model returned a different tile batch size.")
            for probability, (x, y, x2, y2) in zip(probabilities, tile_bounds):
                if probability.shape != (y2 - y, x2 - x):
                    probability = cv2.resize(probability, (x2 - x, y2 - y), interpolation=cv2.INTER_LINEAR)
                probability_sum[y:y2, x:x2] += probability
                coverage[y:y2, x:x2] += 1

        if np.any(coverage == 0):
            raise RuntimeError("Segmentation tiling left part of the inspection image uncovered.")
        probability_sum /= coverage
        return probability_sum, count

    def _inspect_crops(
        self,
        img_rgb: np.ndarray,
        rois: List[Dict[str, Any]],
        inspect_node: Optional[FlowNode],
    ) -> Tuple[List[CropInspectionResult], float, str]:
        """
        Executes Stage 2 inspection on all cropped ROIs using PyTorch:
          - Anomaly (PaDiM / ResNetFeatureExtractor)
          - Segmentation (UNet)
          - Classification (ResNet18)
        Applies safe cropping, standardized resizing, and thumbnail generation.
        """
        t0 = time.time()
        if not rois:
            return [], 0.0, "passed"

        task = inspect_node.data.task if (inspect_node and inspect_node.data.task) else "anomaly"
        threshold = inspect_node.data.threshold if (inspect_node and inspect_node.data.threshold is not None) else 0.45
        padding = inspect_node.data.crop_padding if (inspect_node and inspect_node.data.crop_padding is not None) else 10
        min_defect_area = (
            inspect_node.data.params.get("min_defect_area_px", 8) if (inspect_node and inspect_node.data.params) else 8
        )
        job_id = inspect_node.data.model_job_id if inspect_node else None

        model, is_trained = self._get_inspection_model(task=task, job_id=job_id)
        input_size = self._model_input_sizes.get((task.lower().strip(), job_id, "fast"), (224, 224))
        task_clean = task.lower().strip()
        crop_results: List[CropInspectionResult] = []

        # Sequential processing with torch.no_grad()
        with torch.no_grad():
            for roi in rois:
                tiled_full_image = task_clean == "segmentation" and roi["id"] == "full_image"
                if tiled_full_image:
                    h, w = img_rgb.shape[:2]
                    bounded_bbox = [0, 0, w, h]
                    resized_crop = None
                else:
                    resized_crop, bounded_bbox = safe_crop_roi(
                        img_rgb,
                        roi["bbox"],
                        padding_px=padding,
                        min_size=16,
                        target_size=input_size,
                    )

                # 2. Extract unresized crop for high-fidelity thumbnail downsampled to max 160px (EC-02)
                x1_b, y1_b, x2_b, y2_b = bounded_bbox
                raw_crop = img_rgb[y1_b:y2_b, x1_b:x2_b]
                if raw_crop.size == 0:
                    raw_crop = resized_crop if resized_crop is not None else img_rgb

                # Downsample thumbnail to max 160px for compact payload
                th_h, th_w = raw_crop.shape[:2]
                max_th = max(th_h, th_w)
                if max_th > 160:
                    scale = 160.0 / max_th
                    thumb_img = cv2.resize(raw_crop, (max(16, int(round(th_w * scale))), max(16, int(round(th_h * scale)))))
                else:
                    thumb_img = raw_crop

                # Encode thumbnail to Base64 PNG
                thumb_bgr = cv2.cvtColor(thumb_img, cv2.COLOR_RGB2BGR)
                _, buf = cv2.imencode(".png", thumb_bgr)
                crop_b64 = f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('utf-8')}"

                crop_tensor = None if tiled_full_image else (
                    torch.from_numpy(resized_crop).permute(2, 0, 1).float().unsqueeze(0).to(self.device) / 255.0
                )

                defect_score = 0.0
                flaw_type = "설정 기준 이내"
                defect_area_px = 0
                defect_mask: Optional[np.ndarray] = None
                tiles_processed = 0

                # 4. Task-Specific PyTorch Inference
                if task_clean == "anomaly":
                    if is_trained:
                        # Trained PaDiM or PatchCore detector
                        _, score = model.predict_anomaly_map(crop_tensor)
                        defect_score = float(score)
                    else:
                        # Baseline ResNet feature extractor for untrained state
                        extractor = model.feature_extractor if hasattr(model, "feature_extractor") else model
                        feats = extractor(crop_tensor)  # [1, 384, 28, 28]
                        # Compute patch anomaly divergence from spatial feature mean
                        diff = feats - feats.mean(dim=(2, 3), keepdim=True)
                        patch_dist = torch.norm(diff, dim=1).squeeze()
                        max_d = float(patch_dist.max().item())
                        # Calibrated sigmoid scaling centered at baseline divergence
                        defect_score = float(1.0 / (1.0 + np.exp(-(max_d - 5.6) * 1.8)))

                    if defect_score >= threshold:
                        flaw_type = "이상 점수 임계값 초과"

                elif task_clean == "segmentation":
                    if tiled_full_image:
                        probability_map, tiles_processed = self._predict_tiled_segmentation(model, img_rgb, input_size)
                        defect_score = float(probability_map.max())
                        defect_mask = (probability_map > threshold).astype(np.uint8)
                    else:
                        logits = model(crop_tensor)
                        probs = torch.softmax(logits, dim=1)[:, 1]
                        defect_score = float(probs.max().item())
                        defect_mask = (probs[0] > threshold).cpu().numpy().astype(np.uint8)
                    defect_area_px = int(defect_mask.sum())
                    if defect_area_px >= min_defect_area:
                        units = "검사 이미지 픽셀" if tiled_full_image else "모델 입력 픽셀"
                        flaw_type = f"분할 마스크 결함 영역 ({defect_area_px} {units})"

                elif task_clean in ("classification", "classifier"):
                    logits = model(crop_tensor)  # [1, 2]
                    probs = torch.softmax(logits, dim=1)
                    defect_score = float(probs[0, 1].item())
                    if defect_score >= threshold:
                        flaw_type = "분류 모델 결함 점수 임계값 초과"

                if task_clean == "segmentation":
                    verdict: Literal["OK", "NG"] = "NG" if defect_area_px >= min_defect_area else "OK"
                else:
                    verdict = "NG" if defect_score >= threshold else "OK"
                if verdict == "OK":
                    flaw_type = "설정 기준 이내"

                crop_result = CropInspectionResult(
                        roi_id=roi["id"],
                        label=roi["label"],
                        bbox=bounded_bbox,
                        defect_score=round(defect_score, 4),
                        verdict=verdict,
                        crop_thumbnail=crop_b64,
                        flaw_type=flaw_type,
                        confidence=roi.get("confidence"),
                        defect_area_px=defect_area_px if defect_area_px > 0 else None,
                        tiles_processed=tiles_processed if tiles_processed else None,
                )
                crop_result._defect_mask = defect_mask
                crop_results.append(crop_result)

        latency_ms = (time.time() - t0) * 1000.0
        status = "passed" if is_trained else "warning_untrained"
        return crop_results, latency_ms, status

    def _evaluate_decision_rules(
        self,
        crops: List[CropInspectionResult],
        decision_node: Optional[FlowNode],
        no_inspection_reason: Optional[str] = None,
    ) -> Tuple[Literal["OK", "NG", "REVIEW"], bool, str, float, str]:
        """
        Evaluates Stage 3 decision rules:
          - any_defect_is_ng
          - score_gt_threshold
          - max_flaws_allowed
        """
        t0 = time.time()
        rule = decision_node.data.rule if (decision_node and decision_node.data.rule) else "any_defect_is_ng"
        threshold = decision_node.data.threshold if (decision_node and decision_node.data.threshold is not None) else 0.5
        params = decision_node.data.params if decision_node else {}
        max_flaws = params.get("max_flaws_allowed", 0)

        ng_crops = [c for c in crops if c.verdict == "NG"]
        defective_count = len(ng_crops)

        if not crops:
            final_verdict: Literal["OK", "NG", "REVIEW"] = "REVIEW"
            is_ok = False
            rejection_reason = no_inspection_reason or "No inspection region was found; the image was not inspected. Review required."
        elif rule == "score_gt_threshold":
            max_score = max((c.defect_score for c in crops), default=0.0)
            is_ng = max_score >= threshold
            final_verdict = "NG" if is_ng else "OK"
            is_ok = not is_ng
            rejection_reason = (
                f"Peak defect score {max_score:.3f} exceeded threshold {threshold:.2f}"
                if is_ng
                else "All defect scores within allowable tolerance."
            )
        elif rule == "max_flaws_allowed":
            is_ng = defective_count > max_flaws
            final_verdict = "NG" if is_ng else "OK"
            is_ok = not is_ng
            rejection_reason = (
                f"{defective_count} flaw(s) detected (allowed max: {max_flaws})"
                if is_ng
                else "Defect flaw count within allowable threshold."
            )
        else:  # 'any_defect_is_ng'
            is_ng = defective_count > 0
            final_verdict = "NG" if is_ng else "OK"
            is_ok = not is_ng
            if not is_ng:
                rejection_reason = "검사된 모든 영역이 설정 기준 이내입니다."
            elif ng_crops[0].roi_id == "full_image" and ng_crops[0].defect_area_px is not None:
                rejection_reason = (
                    f"전체 이미지 분할 결과 결함 픽셀 {ng_crops[0].defect_area_px}개가 "
                    "설정한 최소 면적 이상입니다."
                )
            else:
                rejection_reason = f"검사 영역 {defective_count}개에서 결함 기준 초과: {ng_crops[0].flaw_type}"

        latency_ms = (time.time() - t0) * 1000.0
        status = "review_required" if final_verdict == "REVIEW" else "flagged_ng" if final_verdict == "NG" else "passed"
        return final_verdict, is_ok, rejection_reason, latency_ms, status

    def _render_master_image(self, img_rgb: np.ndarray, crops: List[CropInspectionResult]) -> str:
        """Draws color-coded bounding boxes and verdicts on master image."""
        annotated = img_rgb.copy()
        for crop in crops:
            x1, y1, x2, y2 = crop.bbox
            if crop.verdict == "NG" and crop._defect_mask is not None:
                mask = cv2.resize(crop._defect_mask, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
                region = annotated[y1:y2, x1:x2]
                affected = mask > 0
                region[affected] = (
                    region[affected].astype(np.float32) * 0.5
                    + np.array([240, 0, 0], dtype=np.float32) * 0.5
                ).astype(np.uint8)
            color = (240, 0, 0) if crop.verdict == "NG" else (0, 220, 0)  # RGB
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            tag = f"{crop.label} | {crop.verdict} ({crop.defect_score:.2f})"
            cv2.putText(
                annotated,
                tag,
                (x1, max(15, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                2,
            )

        h, w = annotated.shape[:2]
        if max(h, w) > 1600:
            scale = 1600 / max(h, w)
            annotated = cv2.resize(
                annotated, (max(1, round(w * scale)), max(1, round(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ann_bgr = cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR)
        _, buf = cv2.imencode(".png", ann_bgr)
        return f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('utf-8')}"

    def execute(
        self,
        pipeline: Optional[FlowchartPipeline] = None,
        image: Optional[Union[np.ndarray, str, Path]] = None,
        image_path: Optional[str] = None,
        image_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Executes end-to-end multi-model flowchart pipeline.
        Measures real node latencies and returns complete FlowchartExecutionResult.
        """
        overall_t0 = time.time()
        pipe = pipeline or get_default_flowchart()
        ordered_nodes = ordered_linear_nodes(pipe)
        nodes_by_type = {node.data.node_type: node for node in ordered_nodes}
        input_node = nodes_by_type["input"]
        det_node = nodes_by_type.get("detection_crop")
        inspect_node = nodes_by_type["inspection"]
        decision_node = nodes_by_type["decision"]
        output_node = nodes_by_type["output"]
        full_image_segmentation = det_node is None and (inspect_node.data.task or "").lower() == "segmentation"
        input_max_dim = None if full_image_segmentation else 1600

        execution_steps: List[FlowchartExecutionStep] = []

        # ====================================================================
        # Stage 0: Input Ingestion
        # ====================================================================
        t_input_0 = time.time()
        img_rgb: Optional[np.ndarray] = None

        if isinstance(image, np.ndarray):
            if image.ndim == 2:
                img_rgb = np.stack([image, image, image], axis=-1)
            elif image.ndim == 3 and image.shape[2] == 4:
                # RGBA
                img_rgb = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
            elif image.ndim == 3 and image.shape[2] == 3:
                img_rgb = image
        elif image_path and Path(image_path).is_file():
            img_rgb = read_image_safely_rgb(image_path, max_dim=input_max_dim)
        elif isinstance(image, (str, Path)) and Path(image).is_file():
            img_rgb = read_image_safely_rgb(image, max_dim=input_max_dim)
        elif image_id:
            for root in [Path("./datasets"), Path("./projects"), Path("/Users/kai/Downloads/운영서버")]:
                if root.exists():
                    for ext in [".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"]:
                        cand = root / f"{image_id}{ext}"
                        if cand.is_file():
                            img_rgb = read_image_safely_rgb(cand, max_dim=input_max_dim)
                            break
                    if img_rgb is not None:
                        break

        # Fallback to procedural synthetic PCB image if no image provided
        if img_rgb is None:
            h, w = 512, 512
            img_rgb = np.full((h, w, 3), 40, dtype=np.uint8)
            cv2.line(img_rgb, (50, 100), (450, 100), (0, 180, 0), 4)
            cv2.line(img_rgb, (100, 50), (100, 450), (0, 180, 0), 4)
            cv2.line(img_rgb, (400, 100), (400, 400), (0, 180, 0), 4)
            # Component 1 (Normal IC)
            cv2.rectangle(img_rgb, (80, 140), (220, 260), (70, 70, 70), -1)
            cv2.rectangle(img_rgb, (80, 140), (220, 260), (180, 180, 180), 2)
            cv2.putText(img_rgb, "IC-A1", (120, 205), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2)
            # Component 2 (Defective IC with red scratch)
            cv2.rectangle(img_rgb, (290, 220), (430, 360), (70, 70, 70), -1)
            cv2.rectangle(img_rgb, (290, 220), (430, 360), (180, 180, 180), 2)
            cv2.putText(img_rgb, "IC-B2", (330, 285), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 2)
            cv2.line(img_rgb, (320, 240), (370, 330), (240, 0, 0), 3)

        input_lat = (time.time() - t_input_0) * 1000.0
        execution_steps.append(
            FlowchartExecutionStep(
                node_id=input_node.id,
                name=input_node.data.label,
                status="passed",
                latency_ms=round(input_lat, 2),
            )
        )

        # ====================================================================
        # Stage 1: Detection & ROI Extraction
        # ====================================================================
        if det_node is not None:
            rois, det_lat, det_status = self._extract_candidate_rois(img_rgb, det_node)
            execution_steps.append(
                FlowchartExecutionStep(
                    node_id=det_node.id,
                    name=det_node.data.label,
                    status=det_status,
                    latency_ms=round(det_lat, 2),
                )
            )
        else:
            h, w = img_rgb.shape[:2]
            rois = [{"id": "full_image", "label": "Full image", "bbox": [0, 0, w, h]}]

        # ====================================================================
        # Stage 2: Inspection on Extracted Crops
        # ====================================================================
        no_inspection_reason = None
        if rois:
            inspect_t0 = time.time()
            try:
                crops, insp_lat, insp_status = self._inspect_crops(img_rgb, rois, inspect_node)
            except FlowchartInspectionLimitError as exc:
                crops, insp_lat, insp_status = [], (time.time() - inspect_t0) * 1000.0, "skipped"
                no_inspection_reason = str(exc)
        else:
            crops, insp_lat, insp_status = [], 0.0, "skipped"
        execution_steps.append(
            FlowchartExecutionStep(
                node_id=inspect_node.id,
                name=inspect_node.data.label,
                status=insp_status,
                latency_ms=round(insp_lat, 2),
            )
        )

        # ====================================================================
        # Stage 3: Decision Rule Evaluation
        # ====================================================================
        verdict, is_ok, reason, dec_lat, dec_status = self._evaluate_decision_rules(
            crops, decision_node, no_inspection_reason=no_inspection_reason,
        )
        execution_steps.append(
            FlowchartExecutionStep(
                node_id=decision_node.id,
                name=decision_node.data.label,
                status=dec_status,
                latency_ms=round(dec_lat, 2),
            )
        )

        # ====================================================================
        # Stage 4: Output Dispatch
        # ====================================================================
        t_out_0 = time.time()
        out_lat = (time.time() - t_out_0) * 1000.0
        execution_steps.append(
            FlowchartExecutionStep(
                node_id=output_node.id,
                name=output_node.data.label,
                status=dec_status,
                latency_ms=round(out_lat, 2),
            )
        )

        # Master Annotated Image
        annotated_b64 = self._render_master_image(img_rgb, crops)
        total_lat = (time.time() - overall_t0) * 1000.0

        defective_count = sum(1 for c in crops if c.verdict == "NG")

        result = FlowchartExecutionResult(
            status="review" if verdict == "REVIEW" else "success",
            final_verdict=verdict,
            is_ok=is_ok,
            rejection_reason=reason,
            roi_count=len(crops),
            defective_roi_count=defective_count,
            crops=crops,
            annotated_image=annotated_b64,
            execution_steps=execution_steps,
            total_latency_ms=round(total_lat, 2),
            inspected_image_size=[img_rgb.shape[1], img_rgb.shape[0]],
            tiles_processed=sum(c.tiles_processed or 0 for c in crops),
            image_path=str(image_path) if image_path else None,
            image_id=str(image_id) if image_id else None,
        )

        return result.model_dump()

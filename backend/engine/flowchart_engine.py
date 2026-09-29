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
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Literal, Mapping, Optional, Sequence, Tuple, Union

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
from backend.engine.detection.model import (
    checkpoint_detection_num_classes,
    create_detection_model,
    foreground_class_names,
)
from backend.engine.device import get_device
from backend.engine.industrial_adapters import read_image_safely_rgb, sanitize_file_stem
from backend.engine.segmentation.model import build_segmentation_model

logger = logging.getLogger("vision_ai_studio.flowchart_engine")
MAX_SEGMENTATION_TILES = 1024
SEGMENTATION_TILE_BATCH_SIZE = 4
_VERIFIED_CHECKPOINTS: ContextVar[Optional[Dict[Tuple[str, str], Path]]] = ContextVar(
    "flowchart_verified_checkpoints", default=None,
)


@contextmanager
def verified_checkpoint_scope(checkpoints: Mapping[Tuple[str, str], Path]) -> Iterator[None]:
    """Bind one execution to already verified checkpoint files, even during project switches."""
    scoped = {
        (job_id, task.lower().strip()): Path(path).resolve(strict=True)
        for (job_id, task), path in checkpoints.items()
    }
    token = _VERIFIED_CHECKPOINTS.set(scoped)
    try:
        yield
    finally:
        _VERIFIED_CHECKPOINTS.reset(token)


class FlowchartInspectionLimitError(RuntimeError):
    """The original-resolution image requires more tiles than this run permits."""


class FlowchartInspectionConfigurationError(RuntimeError):
    """A trained checkpoint cannot support a safe OK/NG decision."""


def _normal_class_indices(classes: Sequence[str]) -> List[int]:
    normal_names = {"ok", "good", "normal", "pass", "nondefect", "nodefect", "정상", "양품"}
    return [
        index for index, name in enumerate(classes)
        if str(name).casefold().strip().replace(" ", "").replace("_", "").replace("-", "") in normal_names
    ]


# ============================================================================
# Schemas & Data Models
# ============================================================================

class FlowNodeData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    label: str
    node_type: Literal["input", "fixed_roi", "detection_crop", "inspection", "decision", "output"]
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
    isBranch: Optional[Literal["pass", "fail", "review", "default"]] = None
    payload_type: Optional[Literal["image", "roi", "result"]] = None


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
    input_payload_type: Optional[Literal["image", "roi", "result"]] = None
    output_payload_type: Optional[Literal["image", "roi", "result"]] = None
    input_count: Optional[int] = None
    output_count: Optional[int] = None
    branch_verdict: Optional[Literal["OK", "NG", "REVIEW"]] = None
    selected_edge_ids: List[str] = Field(default_factory=list)
    skip_reason: Optional[str] = None


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
    routed_output_node_id: Optional[str] = None
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


def get_single_detection_flowchart(job_id: Optional[str] = None) -> FlowchartPipeline:
    """A detector-only flow that treats detected defect boxes as NG evidence."""
    return FlowchartPipeline(
        id="single_detection",
        name="원본 이미지 결함 검출",
        description="결함 객체 검출 -> 검출 수 기준 OK/NG 판정 -> 로컬 미리보기",
        nodes=[
            FlowNode(id="node_input", position={"x": 40, "y": 160}, data=FlowNodeData(
                label="Inspection image", node_type="input",
            )),
            FlowNode(id="node_crop", position={"x": 360, "y": 160}, data=FlowNodeData(
                label="결함 객체 검출", node_type="detection_crop", task="detection",
                model_job_id=job_id, threshold=0.5, crop_padding=0,
            )),
            FlowNode(id="node_decision", position={"x": 680, "y": 160}, data=FlowNodeData(
                label="Defect decision", node_type="decision", rule="any_defect_is_ng",
            )),
            FlowNode(id="node_output", position={"x": 1000, "y": 160}, data=FlowNodeData(
                label="Local result", node_type="output",
            )),
        ],
        edges=[
            FlowEdge(id="input-detect", source="node_input", target="node_crop", label="Original image"),
            FlowEdge(id="detect-decision", source="node_crop", target="node_decision", label="Detected defects"),
            FlowEdge(id="decision-output", source="node_decision", target="node_output", label="Local verdict"),
        ],
    )


def get_fixed_roi_flowchart(
    inspection_task: Literal["anomaly", "segmentation", "classification"] = "segmentation",
    job_id: Optional[str] = None,
) -> FlowchartPipeline:
    """A configured source-pixel rectangle followed by one trained inspection model."""
    return FlowchartPipeline(
        id="fixed_roi_inspection", name="고정 ROI 결함 검사",
        description="원본 픽셀 좌표 ROI → 검사 모델 → OK/NG/REVIEW 판정",
        nodes=[
            FlowNode(id="node_input", position={"x": 40, "y": 160}, data=FlowNodeData(
                label="검사 이미지", node_type="input",
            )),
            FlowNode(id="node_fixed_roi", position={"x": 325, "y": 160}, data=FlowNodeData(
                label="고정 ROI", node_type="fixed_roi", params={"roi_bbox": [0, 0, 512, 512]},
            )),
            FlowNode(id="node_inspect", position={"x": 610, "y": 160}, data=FlowNodeData(
                label="ROI 결함 검사", node_type="inspection", task=inspection_task,
                model_job_id=job_id, threshold=0.5, crop_padding=0,
                params={"min_defect_area_px": 8},
            )),
            FlowNode(id="node_decision", position={"x": 895, "y": 160}, data=FlowNodeData(
                label="최종 판정", node_type="decision", rule="any_defect_is_ng",
            )),
            FlowNode(id="node_output", position={"x": 1180, "y": 160}, data=FlowNodeData(
                label="검사 결과", node_type="output",
            )),
        ],
        edges=[
            FlowEdge(id="input-fixed", source="node_input", target="node_fixed_roi", payload_type="image"),
            FlowEdge(id="fixed-inspect", source="node_fixed_roi", target="node_inspect", payload_type="roi"),
            FlowEdge(id="inspect-decision", source="node_inspect", target="node_decision", payload_type="result"),
            FlowEdge(id="decision-output", source="node_decision", target="node_output", payload_type="result"),
        ],
    )


def _fixed_roi_rectangle(node: FlowNode) -> List[int]:
    """Return the configured [x1, y1, x2, y2] original-pixel rectangle."""
    rectangle = node.data.params.get("roi_bbox")
    if not isinstance(rectangle, list) or len(rectangle) != 4 or any(type(value) is not int for value in rectangle):
        raise ValueError(f"Fixed ROI rectangle for {node.id} must contain four integer original-image coordinates.")
    x1, y1, x2, y2 = rectangle
    if x1 < 0 or y1 < 0 or x2 - x1 < 16 or y2 - y1 < 16:
        raise ValueError(f"Fixed ROI rectangle for {node.id} must start at nonnegative coordinates and be at least 16x16 pixels.")
    return rectangle


def get_five_model_chain_flowchart() -> FlowchartPipeline:
    """A reusable classification → ROI detection → classification → segmentation chain."""
    specs = [
        ("classify_1", "inspection", "classification", "1차 제품 분류"),
        ("detect_roi", "detection_crop", "detection", "2차 관심 영역 검출"),
        ("classify_2", "inspection", "classification", "3차 ROI 상태 분류"),
        ("classify_3", "inspection", "classification", "4차 세부 유형 분류"),
        ("segment", "inspection", "segmentation", "5차 결함 영역 분할"),
    ]
    nodes = [FlowNode(id="input", position={"x": 40, "y": 180}, data=FlowNodeData(
        label="검사 이미지", node_type="input",
    ))]
    nodes.extend(
        FlowNode(id=node_id, position={"x": 270 + index * 250, "y": 180}, data=FlowNodeData(
            label=label, node_type=node_type, task=task, model_job_id=None,
        ))
        for index, (node_id, node_type, task, label) in enumerate(specs)
    )
    nodes.extend([
        FlowNode(id="decision", position={"x": 1540, "y": 180}, data=FlowNodeData(
            label="최종 판정", node_type="decision", rule="any_defect_is_ng",
        )),
        FlowNode(id="output", position={"x": 1790, "y": 180}, data=FlowNodeData(
            label="검사 결과", node_type="output",
        )),
    ])
    links = [
        ("input", "classify_1", "image"),
        ("classify_1", "detect_roi", "image"),
        ("detect_roi", "classify_2", "roi"),
        ("classify_2", "classify_3", "roi"),
        ("classify_3", "segment", "roi"),
        ("segment", "decision", "result"),
        ("decision", "output", "result"),
    ]
    return FlowchartPipeline(
        id="five_model_chain", name="5단계 다중 모델 체인",
        description="분류 → 검출 ROI → 분류 → 분류 → 분할 → 판정",
        nodes=nodes,
        edges=[FlowEdge(id=f"e{index + 1}", source=source, target=target, payload_type=payload)
               for index, (source, target, payload) in enumerate(links)],
    )


def get_conditional_inspection_flowchart() -> FlowchartPipeline:
    """A classifier gate that runs expensive follow-up inspection only on NG."""
    return FlowchartPipeline(
        id="conditional_inspection", name="조건 분기 정밀 검사",
        description="분류 결과가 NG일 때만 ROI 정밀 검사를 실행합니다.",
        nodes=[
            FlowNode(id="input", position={"x": 40, "y": 180}, data=FlowNodeData(
                label="검사 이미지", node_type="input",
            )),
            FlowNode(id="inspect_gate", position={"x": 320, "y": 180}, data=FlowNodeData(
                label="1차 분류", node_type="inspection", task="classification",
            )),
            FlowNode(id="inspect_followup", position={"x": 620, "y": 300}, data=FlowNodeData(
                label="NG 정밀 분할", node_type="inspection", task="segmentation",
            )),
            FlowNode(id="decision", position={"x": 920, "y": 180}, data=FlowNodeData(
                label="최종 판정", node_type="decision", rule="any_defect_is_ng",
            )),
            FlowNode(id="ok", position={"x": 1210, "y": 60}, data=FlowNodeData(
                label="OK", node_type="output",
            )),
            FlowNode(id="ng", position={"x": 1210, "y": 180}, data=FlowNodeData(
                label="NG", node_type="output",
            )),
            FlowNode(id="review", position={"x": 1210, "y": 300}, data=FlowNodeData(
                label="REVIEW", node_type="output",
            )),
        ],
        edges=[
            FlowEdge(id="input-gate", source="input", target="inspect_gate", payload_type="image"),
            FlowEdge(id="gate-pass", source="inspect_gate", target="decision", payload_type="result", isBranch="pass"),
            FlowEdge(id="gate-fail", source="inspect_gate", target="inspect_followup", payload_type="roi", isBranch="fail"),
            FlowEdge(id="followup-decision", source="inspect_followup", target="decision", payload_type="result"),
            FlowEdge(id="decision-ok", source="decision", target="ok", isBranch="pass", payload_type="result"),
            FlowEdge(id="decision-ng", source="decision", target="ng", isBranch="fail", payload_type="result"),
            FlowEdge(id="decision-review", source="decision", target="review", isBranch="review", payload_type="result"),
        ],
    )


def ordered_linear_nodes(pipeline: FlowchartPipeline) -> List[FlowNode]:
    """Validate an executable inspection DAG and return its topological order.

    The historical name stays available to saved flows and the remote worker.
    Model branches may split and chain before joining at one decision.
    An edge without a condition always runs; pass/fail/review gates are
    evaluated from the source model's result.
    """
    nodes = {node.id: node for node in pipeline.nodes}
    if len(nodes) != len(pipeline.nodes):
        raise ValueError("Pipeline node IDs must be unique.")
    inputs = [node for node in pipeline.nodes if node.data.node_type == "input"]
    if len(inputs) != 1:
        raise ValueError("Pipeline must have exactly one input node.")
    decisions = [node for node in pipeline.nodes if node.data.node_type == "decision"]
    outputs = [node for node in pipeline.nodes if node.data.node_type == "output"]
    models = [node for node in pipeline.nodes if node.data.node_type in ("detection_crop", "inspection")]
    fixed_rois = [node for node in pipeline.nodes if node.data.node_type == "fixed_roi"]
    if len(decisions) != 1 or not 1 <= len(outputs) <= 3 or not 1 <= len(models) <= 8:
        raise ValueError("Pipeline needs one decision, 1-3 outputs, and 1-8 model nodes.")
    if len(fixed_rois) > 8:
        raise ValueError("Pipeline supports at most eight fixed ROI nodes.")

    outgoing: Dict[str, List[FlowEdge]] = {node_id: [] for node_id in nodes}
    incoming: Dict[str, List[FlowEdge]] = {node_id: [] for node_id in nodes}
    edge_ids: set[str] = set()
    for edge in pipeline.edges:
        if edge.source not in nodes or edge.target not in nodes or edge.source == edge.target:
            raise ValueError("Pipeline edge references an invalid node.")
        if not edge.id or edge.id in edge_ids:
            raise ValueError("Pipeline edge IDs must be unique and nonempty.")
        edge_ids.add(edge.id)
        if any(existing.target == edge.target for existing in outgoing[edge.source]):
            raise ValueError("Pipeline cannot contain duplicate connections between nodes.")
        source_type = nodes[edge.source].data.node_type
        target_type = nodes[edge.target].data.node_type
        if source_type == "input":
            if target_type not in ("fixed_roi", "detection_crop", "inspection"):
                raise ValueError("Input edge must feed a fixed ROI or model node.")
            allowed_payloads = {"image"}
        elif source_type == "fixed_roi":
            if target_type not in ("detection_crop", "inspection"):
                raise ValueError("Fixed ROI output must feed a model node.")
            allowed_payloads = {"roi"}
        elif source_type in ("detection_crop", "inspection"):
            if target_type in ("detection_crop", "inspection"):
                allowed_payloads = {"image", "roi"}
            elif target_type == "decision":
                allowed_payloads = {"result"}
            else:
                raise ValueError("Model outputs must feed a model or decision node.")
        elif source_type == "decision" and target_type == "output":
            allowed_payloads = {"result"}
        else:
            raise ValueError("Pipeline edge has unsupported source and target types.")
        if edge.payload_type is not None and edge.payload_type not in allowed_payloads:
            raise ValueError(f"Edge {edge.id} has an incompatible {edge.payload_type} payload type.")
        if source_type in ("input", "fixed_roi") and edge.isBranch not in (None, "default"):
            raise ValueError("Input and fixed ROI edges cannot have a conditional branch.")
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)

    input_id = inputs[0].id
    decision_id = decisions[0].id
    if incoming[input_id] or not outgoing[input_id]:
        raise ValueError("Pipeline input must be the connected starting node.")
    for node in fixed_rois:
        _fixed_roi_rectangle(node)
        parents = incoming[node.id]
        if len(parents) != 1 or parents[0].source != input_id:
            raise ValueError(f"Fixed ROI node {node.id} needs exactly one original-image input edge.")
        if not outgoing[node.id]:
            raise ValueError(f"Fixed ROI node {node.id} must connect to an inspection or detection model.")
    for node in models:
        threshold = node.data.threshold
        if threshold is None or not 0 <= threshold <= 1:
            raise ValueError(f"Model node {node.id} threshold must be between 0 and 1.")
        if node.data.crop_padding is not None and node.data.crop_padding < 0:
            raise ValueError(f"Model node {node.id} crop padding cannot be negative.")
        parents = incoming[node.id]
        if len(parents) != 1:
            raise ValueError(f"Model node {node.id} needs exactly one incoming edge.")
        parent_type = nodes[parents[0].source].data.node_type
        allowed = ("input", "fixed_roi", "detection_crop", "inspection")
        if parent_type not in allowed:
            raise ValueError(f"Model node {node.id} has an unsupported upstream connection.")
        if node.data.node_type == "inspection" and node.data.task not in ("anomaly", "segmentation", "classification"):
            raise ValueError(f"Inspection node {node.id} has an unsupported task.")
        targets = outgoing[node.id]
        if not targets:
            raise ValueError(f"Model node {node.id} must connect to an inspection or decision node.")
        target_types = {nodes[edge.target].data.node_type for edge in targets}
        if not target_types.issubset({"inspection", "detection_crop", "decision"}):
            raise ValueError(f"Model node {node.id} must connect to a model or decision node.")
    decision_inputs = incoming[decision_id]
    if not decision_inputs or any(nodes[edge.source].data.node_type not in ("inspection", "detection_crop") for edge in decision_inputs):
        raise ValueError("Decision needs incoming model results.")
    decision = decisions[0]
    rule = decision.data.rule or "any_defect_is_ng"
    if rule not in ("any_defect_is_ng", "score_gt_threshold", "max_flaws_allowed"):
        raise ValueError(f"Decision rule {rule} is unsupported.")
    if decision.data.params.get("incomplete_policy", "review") not in ("review", "ng"):
        raise ValueError("Decision incomplete_policy must be review or ng.")
    if decision.data.params.get("review_fallback") not in (None, "pass", "fail"):
        raise ValueError("Decision review_fallback must be pass or fail when configured.")
    if rule == "score_gt_threshold" and not 0 <= (decision.data.threshold or 0) <= 1:
        raise ValueError("Decision score threshold must be between 0 and 1.")
    if rule == "max_flaws_allowed":
        if any(node.data.node_type == "inspection" and node.data.task == "segmentation" for node in models):
            raise ValueError(
                "max_flaws_allowed cannot count individual segmentation defects; choose any_defect_is_ng or score_gt_threshold."
            )
        allowed_flaws = decision.data.params.get("max_flaws_allowed", 0)
        if isinstance(allowed_flaws, bool) or not isinstance(allowed_flaws, int) or allowed_flaws < 0:
            raise ValueError("Decision max_flaws_allowed must be a nonnegative integer.")
    branches = outgoing[decision_id]
    if len(branches) != len(outputs) or any(nodes[edge.target].data.node_type != "output" for edge in branches):
        raise ValueError("Decision must connect directly to every output branch.")
    for node in outputs:
        if len(incoming[node.id]) != 1 or incoming[node.id][0].source != decision_id or outgoing[node.id]:
            raise ValueError(f"Output {node.id} must terminate one decision branch.")
    if len(outputs) > 1:
        expected = {"pass", "fail"} if len(outputs) == 2 else {"pass", "fail", "review"}
        if {edge.isBranch for edge in branches} != expected or len({edge.isBranch for edge in branches}) != len(branches):
            raise ValueError("Decision branches require distinct pass/fail labels and optional review label.")

    pending = {node_id: len(incoming[node_id]) for node_id in nodes}
    ready = [input_id]
    order: List[FlowNode] = []
    while ready:
        node_id = ready.pop(0)
        order.append(nodes[node_id])
        for edge in outgoing[node_id]:
            pending[edge.target] -= 1
            if pending[edge.target] == 0:
                ready.append(edge.target)
    if len(order) != len(nodes):
        raise ValueError("Pipeline edges must connect every node without cycles.")
    return order


def _edge_payload_type(edge: FlowEdge, source_type: str, target_type: str) -> Literal["image", "roi", "result"]:
    """Resolve old untyped saved edges without changing their execution meaning."""
    if edge.payload_type is not None:
        return edge.payload_type
    if source_type == "input":
        return "image"
    if target_type in ("decision", "output"):
        return "result"
    return "roi"


def _branch_matches(edge: FlowEdge, verdict: Literal["OK", "NG", "REVIEW"]) -> bool:
    if edge.isBranch in (None, "default"):
        return True
    return edge.isBranch == {"OK": "pass", "NG": "fail", "REVIEW": "review"}[verdict]


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

    def __init__(
        self, device: Optional[Union[torch.device, str]] = None,
        checkpoint_resolver: Optional[Callable[[str, str], Optional[Path]]] = None,
    ):
        self.device = get_device(device) if device is not None else get_device()
        self._checkpoint_resolver = checkpoint_resolver
        self._model_cache: Dict[Tuple[Any, ...], Any] = {}
        self._model_input_sizes: Dict[Tuple[Any, ...], Tuple[int, int]] = {}
        self._model_classes: Dict[Tuple[Any, ...], List[str]] = {}

    def _remember_input_size(self, cache_key: Tuple[Any, ...], checkpoint: Dict[str, Any]) -> None:
        raw = checkpoint.get("image_size", [224, 224])
        if isinstance(raw, (list, tuple)) and len(raw) == 2:
            width, height = int(raw[0]), int(raw[1])
            if 16 <= width <= 4096 and 16 <= height <= 4096:
                self._model_input_sizes[cache_key] = (width, height)

    def _resolve_checkpoint(self, job_id: Optional[str], task: str) -> Optional[Path]:
        """Locate a training checkpoint through the app or a verified export bundle."""
        if not job_id:
            return None
        scoped = _VERIFIED_CHECKPOINTS.get()
        if scoped is not None:
            checkpoint = scoped.get((job_id, task.lower().strip()))
            if checkpoint is None:
                raise FlowchartInspectionConfigurationError(
                    f"No verified {task} checkpoint was bound for model {job_id}."
                )
            return checkpoint
        if self._checkpoint_resolver is not None:
            resolved = self._checkpoint_resolver(job_id, task)
            return Path(resolved) if resolved is not None else None
        return trusted_checkpoint(job_id)

    def _cache_key(self, task: str, job_id: Optional[str], preset: str, checkpoint: Optional[Path]) -> Tuple[Any, ...]:
        key: Tuple[Any, ...] = (task, job_id, preset)
        if checkpoint is None:
            return key
        digest = sha256()
        with Path(checkpoint).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return (*key, str(Path(checkpoint).resolve()), digest.hexdigest())

    def _get_detection_model(self, job_id: Optional[str] = None, preset: str = "fast") -> Tuple[nn.Module, bool]:
        """Loads cached or new Faster R-CNN model."""
        ckpt_path = self._resolve_checkpoint(job_id, "detection")
        cache_key = self._cache_key("detection", job_id, preset, ckpt_path)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

        is_trained = False
        if ckpt_path:
            try:
                ckpt = torch.load(str(ckpt_path), map_location=self.device, weights_only=True)
                state = ckpt.get("model_state_dict", ckpt.get("model_state", ckpt.get("state_dict", ckpt)))
                classes = ckpt.get("classes", [])
                model = create_detection_model(
                    preset=ckpt.get("detector_preset", ckpt.get("preset", preset)),
                    num_classes=checkpoint_detection_num_classes(state, classes), pretrained=False,
                )
                model.load_state_dict(state, strict=True)
                self._remember_input_size(cache_key, ckpt)
                self._model_classes[cache_key] = foreground_class_names(classes)
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
        ckpt_path = self._resolve_checkpoint(job_id, task_clean)
        cache_key = self._cache_key(task_clean, job_id, preset, ckpt_path)
        if cache_key in self._model_cache:
            return self._model_cache[cache_key]

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
                    self._model_classes[cache_key] = [str(name) for name in ckpt.get("classes", [])]
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
                p_labels = preds[0].get("labels")
                class_ids = p_labels.detach().cpu().numpy() if p_labels is not None else None
                mask = p_scores >= threshold
                f_boxes = p_boxes[mask]
                f_scores = p_scores[mask]
                f_labels = class_ids[mask] if class_ids is not None else None
                key = self._cache_key("detection", job_id, "fast", self._resolve_checkpoint(job_id, "detection"))
                known_classes = self._model_classes.get(key, [])
                for index, (b, s) in enumerate(zip(f_boxes, f_scores)):
                    boxes_out.append([int(b[0]), int(b[1]), int(b[2]), int(b[3])])
                    scores_out.append(float(s))
                    class_id = int(f_labels[index]) if f_labels is not None else 0
                    labels_out.append(
                        known_classes[class_id - 1] if 1 <= class_id <= len(known_classes)
                        else f"defect_{class_id}" if class_id > 0 else "component"
                    )
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
        task_clean = task.lower().strip()
        key = self._cache_key(task_clean, job_id, "fast", self._resolve_checkpoint(job_id, task_clean))
        input_size = self._model_input_sizes.get(key, (224, 224))
        class_names = self._model_classes.get(key, [])
        normal_indices = _normal_class_indices(class_names) if is_trained else [0]
        if task_clean in ("classification", "classifier") and is_trained and not normal_indices:
            raise FlowchartInspectionConfigurationError(
                "Classification checkpoint has no normal/OK class; the image cannot be safely classified."
            )
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
                        padding_px=roi.get("crop_padding", padding),
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
                    logits = model(crop_tensor)
                    probs = torch.softmax(logits, dim=1)
                    if max(normal_indices) >= probs.shape[1]:
                        raise FlowchartInspectionConfigurationError(
                            "Classification checkpoint normal/OK class index exceeds model outputs."
                        )
                    defect_score = float((1.0 - probs[0, normal_indices].sum()).clamp(0, 1).item())
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

    def _detected_defect_crops(
        self, img_rgb: np.ndarray, rois: List[Dict[str, Any]],
    ) -> List[CropInspectionResult]:
        """Represent detector boxes as defect evidence without a second model."""
        results: List[CropInspectionResult] = []
        for roi in rois:
            crop, bbox = safe_crop_roi(
                img_rgb, roi["bbox"], padding_px=0, min_size=1, target_size=None,
            )
            height, width = crop.shape[:2]
            if max(height, width) > 160:
                scale = 160 / max(height, width)
                crop = cv2.resize(crop, (max(1, round(width * scale)), max(1, round(height * scale))))
            _, encoded = cv2.imencode(".png", cv2.cvtColor(crop, cv2.COLOR_RGB2BGR))
            confidence = float(roi.get("confidence", 0.0))
            results.append(CropInspectionResult(
                roi_id=roi["id"], label=roi["label"], bbox=bbox,
                defect_score=round(confidence, 4), verdict="NG",
                crop_thumbnail=f"data:image/png;base64,{base64.b64encode(encoded.tobytes()).decode('utf-8')}",
                flaw_type="결함 객체 검출", confidence=confidence,
            ))
        return results

    def _evaluate_decision_rules(
        self,
        crops: List[CropInspectionResult],
        decision_node: Optional[FlowNode],
        no_inspection_reason: Optional[str] = None,
        empty_is_ok: bool = False,
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

        if not crops and empty_is_ok:
            final_verdict = "OK"
            is_ok = True
            rejection_reason = "설정한 신뢰도 이상으로 검출된 결함 객체가 없습니다."
        elif not crops:
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

    def _detect_in_regions(
        self, img_rgb: np.ndarray, regions: List[Dict[str, Any]], node: FlowNode,
    ) -> Tuple[List[Dict[str, Any]], float, str]:
        """Run a detector on the image or inherited ROIs and restore source coordinates."""
        image_h, image_w = img_rgb.shape[:2]
        detected: List[Dict[str, Any]] = []
        latency_ms = 0.0
        status = "passed"
        for region in regions:
            if region["id"] == "full_image":
                region_image = img_rgb
                offset_x = offset_y = 0
            else:
                region_image, bounds = safe_crop_roi(
                    img_rgb, region["bbox"], padding_px=0, min_size=1, target_size=None,
                )
                offset_x, offset_y = bounds[:2]
            height, width = region_image.shape[:2]
            scale = min(1.0, 1600.0 / max(height, width))
            model_image = region_image if scale == 1.0 else cv2.resize(
                region_image, (max(1, round(width * scale)), max(1, round(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
            rois, elapsed, region_status = self._extract_candidate_rois(model_image, node)
            latency_ms += elapsed
            if region_status != "passed":
                status = region_status
            for roi in rois:
                x1, y1, x2, y2 = roi["bbox"]
                mapped = [
                    max(0, min(image_w - 1, round(x1 / scale) + offset_x)),
                    max(0, min(image_h - 1, round(y1 / scale) + offset_y)),
                    max(1, min(image_w, round(x2 / scale) + offset_x)),
                    max(1, min(image_h, round(y2 / scale) + offset_y)),
                ]
                if mapped[2] <= mapped[0] or mapped[3] <= mapped[1]:
                    continue
                detected.append({
                    **roi,
                    "id": roi["id"] if region["id"] == "full_image" else f"{region['id']}:{roi['id']}",
                    "bbox": mapped,
                })
        return detected, latency_ms, status

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
        nodes = {node.id: node for node in ordered_nodes}
        incoming = {node.id: [] for node in ordered_nodes}
        outgoing = {node.id: [] for node in ordered_nodes}
        for edge in pipe.edges:
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)
        input_node = next(node for node in ordered_nodes if node.data.node_type == "input")
        decision_node = next(node for node in ordered_nodes if node.data.node_type == "decision")
        output_nodes = [node for node in ordered_nodes if node.data.node_type == "output"]
        model_nodes = [node for node in ordered_nodes if node.data.node_type in ("detection_crop", "inspection")]
        processing_nodes = [node for node in ordered_nodes if node.data.node_type in ("fixed_roi", "detection_crop", "inspection")]
        detector_only_ids = {
            node.id for node in model_nodes
            if node.data.node_type == "detection_crop"
            and any(edge.target == decision_node.id for edge in outgoing[node.id])
        }
        if any(not nodes[node_id].data.model_job_id for node_id in detector_only_ids):
            raise ValueError("A trained detection model is required for a detector-only flow.")
        full_image_input: Dict[str, bool] = {}
        for node in processing_nodes:
            edge = incoming[node.id][0]
            source = nodes[edge.source]
            payload = _edge_payload_type(edge, source.data.node_type, node.data.node_type)
            full_image_input[node.id] = node.data.node_type != "fixed_roi" and (
                payload == "image" or source.data.node_type == "input"
                or (source.data.node_type == "inspection" and full_image_input[source.id])
            )
        full_image_segmentation = any(
            node.data.node_type == "inspection" and node.data.task == "segmentation"
            and full_image_input[node.id] for node in model_nodes
        )
        # Fixed coordinates refer to source pixels, so never downsample input
        # before computing their intersection with the real image bounds.
        input_max_dim = None if full_image_segmentation or any(
            node.data.node_type == "fixed_roi" for node in processing_nodes
        ) else 1600

        execution_steps: List[FlowchartExecutionStep] = []

        # ====================================================================
        # Stage 0: Input Ingestion
        # ====================================================================
        t_input_0 = time.time()
        img_rgb: Optional[np.ndarray] = None

        if image_path is not None or isinstance(image, (str, Path)):
            selected_path = Path(image_path if image_path is not None else image)
            if not selected_path.is_file():
                raise FileNotFoundError(f"Selected inspection image does not exist: {selected_path}")
            img_rgb = read_image_safely_rgb(selected_path, max_dim=input_max_dim)
        elif isinstance(image, np.ndarray):
            if image.size == 0:
                raise ValueError("The inspection image array is empty.")
            if image.ndim == 2:
                img_rgb = np.stack([image, image, image], axis=-1)
            elif image.ndim == 3 and image.shape[2] == 4:
                # RGBA
                img_rgb = cv2.cvtColor(image, cv2.COLOR_RGBA2RGB)
            elif image.ndim == 3 and image.shape[2] == 3:
                img_rgb = image
            else:
                raise ValueError("The inspection image array must have grayscale, RGB, or RGBA channels.")
        else:
            raise ValueError("An inspection image array or path is required; image_id is metadata only.")
        if img_rgb is None or img_rgb.size == 0:
            raise ValueError("The inspection image could not be decoded.")

        input_lat = (time.time() - t_input_0) * 1000.0
        execution_steps.append(
            FlowchartExecutionStep(
                node_id=input_node.id,
                name=input_node.data.label,
                status="passed",
                latency_ms=round(input_lat, 2),
                output_payload_type="image",
                output_count=1,
                selected_edge_ids=[edge.id for edge in outgoing[input_node.id]],
            )
        )

        # Keep each model's region payload and result separate. Only an active
        # edge into the decision contributes to the final verdict. A model
        # skipped by a condition cannot accidentally contribute stale evidence.
        active_edges = {edge.id for edge in outgoing[input_node.id]}
        node_rois: Dict[str, List[Dict[str, Any]]] = {}
        node_evidence: Dict[str, List[CropInspectionResult]] = {}
        incomplete_reasons: List[str] = []
        h, w = img_rgb.shape[:2]
        whole_image = [{"id": "full_image", "label": "Full image", "bbox": [0, 0, w, h]}]
        for node in processing_nodes:
            parent_edge = incoming[node.id][0]
            parent = nodes[parent_edge.source]
            payload_type = _edge_payload_type(parent_edge, parent.data.node_type, node.data.node_type)
            if parent_edge.id not in active_edges:
                execution_steps.append(FlowchartExecutionStep(
                    node_id=node.id, name=node.data.label, status="skipped", latency_ms=0.0,
                    input_payload_type=payload_type, input_count=0, output_count=0,
                    skip_reason="condition_not_met",
                ))
                node_rois[node.id] = []
                node_evidence[node.id] = []
                continue

            rois = whole_image if payload_type == "image" or parent.id == input_node.id else node_rois[parent.id]
            if not rois:
                incomplete_reasons.append(
                    f"Node {node.id} received no ROI from {parent.id}; the image was not inspected."
                )
                execution_steps.append(FlowchartExecutionStep(
                    node_id=node.id, name=node.data.label, status="skipped", latency_ms=0.0,
                    input_payload_type=payload_type, input_count=0, output_count=0,
                    branch_verdict="REVIEW", skip_reason="empty_roi",
                ))
                node_rois[node.id] = []
                node_evidence[node.id] = []
                continue

            if node.data.node_type == "fixed_roi":
                started = time.time()
                x1, y1, x2, y2 = _fixed_roi_rectangle(node)
                clipped = [max(0, min(w, x1)), max(0, min(h, y1)),
                           max(0, min(w, x2)), max(0, min(h, y2))]
                if clipped[2] - clipped[0] >= 16 and clipped[3] - clipped[1] >= 16:
                    selected_roi = [{
                        "id": f"fixed_roi:{node.id}", "label": node.data.label,
                        "bbox": clipped, "crop_padding": 0,
                    }]
                    status = "passed"
                    branch_verdict = "OK"
                else:
                    selected_roi = []
                    status = "review_required"
                    branch_verdict = "REVIEW"
                    incomplete_reasons.append(
                        f"Fixed ROI {node.id} does not overlap this image by at least 16x16 pixels."
                    )
                node_rois[node.id] = selected_roi
                node_evidence[node.id] = []
                latency = (time.time() - started) * 1000.0
                output_count = len(selected_roi)
            elif node.data.node_type == "detection_crop":
                detected, latency, status = self._detect_in_regions(img_rgb, rois, node)
                node_rois[node.id] = [
                    {**roi, "crop_padding": node.data.crop_padding}
                    if node.data.crop_padding is not None else roi
                    for roi in detected
                ]
                node_evidence[node.id] = (
                    self._detected_defect_crops(img_rgb, detected)
                    if node.id in detector_only_ids else []
                )
                branch_verdict: Literal["OK", "NG", "REVIEW"] = (
                    "NG" if detected and node.id in detector_only_ids else
                    "OK" if detected else "REVIEW"
                )
                output_count = len(detected)
            else:
                started = time.time()
                try:
                    evidence, latency, status = self._inspect_crops(img_rgb, rois, node)
                except (FlowchartInspectionLimitError, FlowchartInspectionConfigurationError) as exc:
                    evidence, latency, status = [], (time.time() - started) * 1000.0, "skipped"
                    incomplete_reasons.append(str(exc))
                node_rois[node.id] = rois
                node_evidence[node.id] = evidence
                branch_verdict = (
                    "NG" if any(crop.verdict == "NG" for crop in evidence) else
                    "OK" if evidence else "REVIEW"
                )
                output_count = len(evidence)

            selected = [edge for edge in outgoing[node.id] if _branch_matches(edge, branch_verdict)]
            active_edges.update(edge.id for edge in selected)
            output_types = {
                _edge_payload_type(edge, node.data.node_type, nodes[edge.target].data.node_type)
                for edge in selected
            }
            execution_steps.append(FlowchartExecutionStep(
                node_id=node.id, name=node.data.label,
                status=status, latency_ms=round(latency, 2),
                input_payload_type=payload_type,
                output_payload_type=next(iter(output_types)) if len(output_types) == 1 else None,
                input_count=len(rois), output_count=output_count,
                branch_verdict=branch_verdict,
                selected_edge_ids=[edge.id for edge in selected],
            ))

        decision_edges = [edge for edge in incoming[decision_node.id] if edge.id in active_edges]
        crops: List[CropInspectionResult] = []
        for edge in decision_edges:
            for crop in node_evidence[edge.source]:
                crops.append(
                    crop.model_copy(update={"roi_id": f"{edge.source}:{crop.roi_id}"})
                    if len(decision_edges) > 1 else crop
                )
        if not decision_edges:
            incomplete_reasons.append("No active route reached the decision; review required.")
        empty_is_ok = bool(decision_edges) and all(edge.source in detector_only_ids for edge in decision_edges)
        no_inspection_reason = "; ".join(incomplete_reasons) or None
        verdict, is_ok, reason, dec_lat, dec_status = self._evaluate_decision_rules(
            crops, decision_node, no_inspection_reason=no_inspection_reason,
            empty_is_ok=empty_is_ok and not incomplete_reasons,
        )
        if incomplete_reasons:
            incomplete_policy = decision_node.data.params.get("incomplete_policy", "review")
            if incomplete_policy == "ng":
                verdict, is_ok, reason, dec_status = "NG", False, no_inspection_reason or "Inspection incomplete.", "flagged_ng"
            else:
                verdict, is_ok, reason, dec_status = "REVIEW", False, no_inspection_reason or "Inspection incomplete.", "review_required"

        # ====================================================================
        # Stage 4: Route one result branch. A REVIEW without a review output
        # remains unrouted unless a fallback is explicitly configured.
        # ====================================================================
        branch_edges = outgoing[decision_node.id]
        desired = "pass" if verdict == "OK" else "fail" if verdict == "NG" else "review"
        selected_edge = next((edge for edge in branch_edges if edge.isBranch == desired), None)
        if selected_edge is None and len(branch_edges) == 1:
            selected_edge = branch_edges[0]
        if selected_edge is None and verdict == "REVIEW":
            fallback = decision_node.data.params.get("review_fallback")
            if fallback in ("pass", "fail"):
                selected_edge = next((edge for edge in branch_edges if edge.isBranch == fallback), None)
        execution_steps.append(FlowchartExecutionStep(
            node_id=decision_node.id, name=decision_node.data.label,
            status=dec_status, latency_ms=round(dec_lat, 2),
            input_payload_type="result", output_payload_type="result",
            input_count=len(crops), output_count=1,
            branch_verdict=verdict,
            selected_edge_ids=[selected_edge.id] if selected_edge else [],
        ))
        for output_node in output_nodes:
            execution_steps.append(FlowchartExecutionStep(
                node_id=output_node.id, name=output_node.data.label,
                status=dec_status if selected_edge and output_node.id == selected_edge.target else "skipped",
                latency_ms=0.0,
                input_payload_type="result", input_count=1 if selected_edge and output_node.id == selected_edge.target else 0,
                output_count=1 if selected_edge and output_node.id == selected_edge.target else 0,
                skip_reason=None if selected_edge and output_node.id == selected_edge.target else "branch_not_selected",
            ))

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
            routed_output_node_id=selected_edge.target if selected_edge else None,
        )

        return result.model_dump()

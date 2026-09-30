"""
backend/engine/synthetic_generator.py

High-Speed Procedural Industrial Defect Dataset Generator.
Supports 3 manufacturing domains:
  1. Printed Circuit Board (PCB) Inspection
  2. Semiconductor Wafer Inspection
  3. Brushed Metal Surface Inspection

Synchronizes ground-truth representations across all 4 industrial vision tasks:
  1. Multi-Class Classification (train/OK, train/NG_*, val/OK, val/NG_*)
  2. Object Detection (COCO instances JSON + VOC pixel coordinates)
  3. Semantic Segmentation (Paired RGB images + single-channel 8-bit PNG masks)
  4. Unsupervised Anomaly Detection (standard anomaly layout: strict normal-only train split)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
from PIL import Image, ImageDraw


# ============================================================================
# Modality & Task Definitions
# ============================================================================

class Modality(str, Enum):
    PCB = "pcb"
    WAFER = "wafer"
    METAL = "metal"


class VisionTask(str, Enum):
    CLASSIFICATION = "classification"
    DETECTION = "detection"
    SEGMENTATION = "segmentation"
    ANOMALY = "anomaly"
    ALL = "all"


MODALITY_DEFECTS: Dict[str, List[str]] = {
    "pcb": ["solder_bridge", "broken_trace", "solder_ball"],
    "wafer": ["micro_scratch", "ring_stain", "center_stain"],
    "metal": ["scratch", "gouge", "pit_corrosion"],
}


@dataclass
class SyntheticSample:
    """
    Standard container for a procedural synthetic industrial sample.
    Synchronizes representations across Classification, Detection, Segmentation, and Anomaly tasks.
    """
    image: np.ndarray             # (H, W, 3) uint8 RGB array
    mask: np.ndarray              # (H, W) uint8 class mask (0=bg, 1..K=defect classes)
    bboxes: List[List[int]]       # List of [xmin, ymin, xmax, ymax] VOC pixel coordinates
    labels: List[str]             # Class name per bounding box
    classification_label: str     # "OK" or primary defect name
    is_normal: bool               # True if no defects
    modality: str                 # "pcb", "wafer", "metal"


# ============================================================================
# 1. Modality A: PCB Inspection Renderer
# ============================================================================

def generate_pcb_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """
    Procedurally renders a PCB inspection image with FR4 substrate, copper traces,
    solder pads, through-hole vias, silkscreen fiducials, IC chip body, and defect injection.
    Adaptive across arbitrary resolutions from 64x64 upwards.
    """
    if rng is None:
        rng = np.random.default_rng()

    # Base FR4 green substrate with micro-texture stochastic noise
    base_color = (int(rng.integers(18, 28)), int(rng.integers(70, 85)), int(rng.integers(35, 48)))
    substrate = np.full((height, width, 3), base_color, dtype=np.int16)
    noise = rng.integers(-6, 7, size=(height, width, 3), dtype=np.int16)
    img_np = np.clip(substrate + noise, 0, 255).astype(np.uint8)

    img = Image.fromarray(img_np)
    mask = np.zeros((height, width), dtype=np.uint8)
    draw = ImageDraw.Draw(img)

    margin_x = max(6, int(width * 0.08))
    margin_y = max(6, int(height * 0.08))

    # Silkscreen fiducials / crosshairs at 4 corners
    f_rad = max(2, int(min(width, height) * 0.015))
    fid_pts = [
        (margin_x, margin_y),
        (width - margin_x, margin_y),
        (margin_x, height - margin_y),
        (width - margin_x, height - margin_y),
    ]
    for cx, cy in fid_pts:
        draw.line([(cx - f_rad * 2, cy), (cx + f_rad * 2, cy)], fill=(240, 240, 240), width=1)
        draw.line([(cx, cy - f_rad * 2), (cx, cy + f_rad * 2)], fill=(240, 240, 240), width=1)
        draw.ellipse([cx - f_rad, cy - f_rad, cx + f_rad, cy + f_rad], outline=(240, 240, 240), width=1)

    # Silkscreen IC outline
    ic_box = [int(width * 0.28), int(height * 0.28), int(width * 0.72), int(height * 0.72)]
    if ic_box[2] > ic_box[0] + 10 and ic_box[3] > ic_box[1] + 10:
        draw.rectangle(ic_box, outline=(230, 230, 230), width=1)

    # Copper traces & Solder pads
    copper_color = (195, 155, 45)
    pad_color = (215, 185, 75)
    pad_border = (135, 105, 30)

    trace_ys = [int(height * f) for f in [0.18, 0.38, 0.58, 0.78]]
    pad_step_x = max(16, int(width * 0.16))
    for y in trace_ys:
        draw.line([(margin_x, y), (width - margin_x, y)], fill=copper_color, width=max(2, int(height * 0.015)))
        for x in range(margin_x + pad_step_x // 2, width - margin_x, pad_step_x):
            pw, ph = max(3, int(width * 0.025)), max(3, int(height * 0.025))
            draw.rectangle([x - pw, y - ph, x + pw, y + ph], fill=pad_color, outline=pad_border)
            vr = max(1, pw // 3)
            draw.ellipse([x - vr, y - vr, x + vr, y + vr], fill=(20, 25, 20))

    # Central IC Chip Body & Pins
    chip_x1, chip_y1 = int(width * 0.35), int(height * 0.35)
    chip_x2, chip_y2 = int(width * 0.65), int(height * 0.65)
    if chip_x2 > chip_x1 + 6 and chip_y2 > chip_y1 + 6:
        draw.rectangle([chip_x1, chip_y1, chip_x2, chip_y2], fill=(35, 38, 42), outline=(60, 65, 70))
        p1_r = max(1, int(min(width, height) * 0.012))
        draw.ellipse([chip_x1 + 3, chip_y1 + 3, chip_x1 + 3 + 2 * p1_r, chip_y1 + 3 + 2 * p1_r], fill=(90, 95, 100))

    bboxes: List[List[int]] = []
    labels: List[str] = []
    d_norm = (defect_type is None or str(defect_type).lower() in ["none", "ok", "good", "pass"])

    if not d_norm and defect_type == "solder_bridge":
        bx = int(rng.integers(margin_x + 10, max(margin_x + 12, width - margin_x - 30)))
        by = trace_ys[0]
        bh = max(10, trace_ys[1] - trace_ys[0])
        bw = max(8, int(width * 0.06))
        draw.ellipse([bx, by, bx + bw, by + bh], fill=(210, 215, 225), outline=(160, 165, 175))
        cv2.ellipse(mask, (bx + bw // 2, by + bh // 2), (bw // 2, bh // 2), 0, 0, 360, 1, -1)
        bboxes.append([bx, by, bx + bw, by + bh])
        labels.append("solder_bridge")

    elif not d_norm and defect_type == "broken_trace":
        ty = int(rng.choice(trace_ys))
        gw = max(6, int(width * 0.05))
        gh = max(4, int(height * 0.03))
        gx = int(rng.integers(margin_x + 10, max(margin_x + 12, width - margin_x - gw - 10)))
        draw.rectangle([gx, ty - gh // 2, gx + gw, ty + gh // 2], fill=base_color)
        draw.line([(gx, ty - gh // 2), (gx + 1, ty + gh // 2)], fill=(15, 20, 15), width=1)
        draw.line([(gx + gw, ty - gh // 2), (gx + gw - 1, ty + gh // 2)], fill=(15, 20, 15), width=1)
        cv2.rectangle(mask, (gx, ty - gh // 2), (gx + gw, ty + gh // 2), 2, -1)
        bboxes.append([gx, ty - gh // 2, gx + gw, ty + gh // 2])
        labels.append("broken_trace")

    elif not d_norm and defect_type == "solder_ball":
        r = max(3, int(min(width, height) * 0.025))
        sx = int(rng.integers(margin_x + r + 2, max(margin_x + r + 4, width - margin_x - r - 2)))
        sy = int(rng.integers(margin_y + r + 2, max(margin_y + r + 4, height - margin_y - r - 2)))
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=(225, 228, 235), outline=(130, 135, 145))
        draw.ellipse([sx - r // 2, sy - r // 2, sx - r // 4, sy - r // 4], fill=(255, 255, 255))
        cv2.circle(mask, (sx, sy), r, 3, -1)
        bboxes.append([sx - r, sy - r, sx + r, sy + r])
        labels.append("solder_ball")

    # Strict coordinate boundary clamping
    clamped_boxes = []
    for b in bboxes:
        clamped_boxes.append([
            max(0, min(width, b[0])),
            max(0, min(height, b[1])),
            max(0, min(width, b[2])),
            max(0, min(height, b[3])),
        ])

    is_ok = (len(labels) == 0)
    cls_label = "OK" if is_ok else labels[0]

    return SyntheticSample(
        image=np.array(img),
        mask=mask,
        bboxes=clamped_boxes,
        labels=labels,
        classification_label=cls_label,
        is_normal=is_ok,
        modality="pcb",
    )


# ============================================================================
# 2. Modality B: Semiconductor Wafer Renderer
# ============================================================================

def generate_wafer_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """
    Procedurally renders a semiconductor wafer sample with silicon circular disc,
    die grid scribe lines, alignment notch, and defect injection.
    Optimized via analytical circular chord lengths for >1,500 fps throughput.
    """
    if rng is None:
        rng = np.random.default_rng()

    img_np = np.full((height, width, 3), (16, 16, 20), dtype=np.uint8)
    mask = np.zeros((height, width), dtype=np.uint8)

    center = (width // 2, height // 2)
    radius = max(8, int(min(width, height) * 0.44))

    # Silicon disc & radial sheen
    cv2.circle(img_np, center, radius, (65, 78, 98), -1)
    cv2.circle(img_np, center, int(radius * 0.7), (72, 86, 108), -1)
    cv2.circle(img_np, center, int(radius * 0.4), (78, 94, 118), -1)
    cv2.GaussianBlur(img_np, (15, 15), 0, dst=img_np)

    # Clean outside wafer boundary
    y_g, x_g = np.ogrid[:height, :width]
    wafer_circle = ((x_g - center[0]) ** 2 + (y_g - center[1]) ** 2) <= radius ** 2
    img_np[~wafer_circle] = (16, 16, 20)

    # Die grid (scribe lines) using analytical circular chords
    die_size = max(6, int(min(width, height) * 0.07))
    street_color = (35, 42, 54)
    for x in range(center[0] - radius + die_size, center[0] + radius, die_size):
        if 0 <= x < width:
            dx = abs(x - center[0])
            if dx < radius:
                dy = int(np.sqrt(radius ** 2 - dx ** 2))
                cv2.line(img_np, (x, center[1] - dy), (x, center[1] + dy), street_color, 1)
    for y in range(center[1] - radius + die_size, center[1] + radius, die_size):
        if 0 <= y < height:
            dy = abs(y - center[1])
            if dy < radius:
                dx = int(np.sqrt(radius ** 2 - dy ** 2))
                cv2.line(img_np, (center[0] - dx, y), (center[0] + dx, y), street_color, 1)

    cv2.circle(img_np, center, radius, (110, 125, 145), 1)
    cv2.circle(img_np, center, max(1, radius - 1), (40, 50, 65), 1)
    notch_r = max(2, int(min(width, height) * 0.016))
    cv2.circle(img_np, (center[0], min(height - 1, center[1] + radius)), notch_r, (16, 16, 20), -1)

    bboxes: List[List[int]] = []
    labels: List[str] = []
    d_norm = (defect_type is None or str(defect_type).lower() in ["none", "ok", "good", "pass"])

    if not d_norm and defect_type in ["micro_scratch", "scratch"]:
        low_x = max(0, center[0] - radius // 2)
        high_x = min(width - 1, center[0] + radius // 2)
        low_y = max(0, center[1] - radius // 2)
        high_y = min(height - 1, center[1] + radius // 2)

        start_x = int(rng.integers(low_x, max(low_x + 1, high_x)))
        start_y = int(rng.integers(low_y, max(low_y + 1, high_y)))
        length = max(10, int(min(width, height) * 0.25))
        angle = float(rng.uniform(0, 2 * np.pi))

        pts = []
        cx_cur, cy_cur = float(start_x), float(start_y)
        for _ in range(5):
            pts.append((int(np.clip(cx_cur, 0, width - 1)), int(np.clip(cy_cur, 0, height - 1))))
            cx_cur += (length / 5.0) * np.cos(angle) + float(rng.integers(-2, 3))
            cy_cur += (length / 5.0) * np.sin(angle) + float(rng.integers(-2, 3))

        pts_np = np.array(pts, dtype=np.int32)
        cv2.polylines(img_np, [pts_np], False, (225, 235, 245), max(1, int(min(width, height) * 0.008)))
        cv2.polylines(mask, [pts_np], False, 1, max(1, int(min(width, height) * 0.008)))

        xmin, ymin = np.min(pts_np, axis=0) - 2
        xmax, ymax = np.max(pts_np, axis=0) + 2
        bx1, by1 = max(0, int(xmin)), max(0, int(ymin))
        bx2, by2 = min(width, int(xmax)), min(height, int(ymax))
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("micro_scratch")

    elif not d_norm and defect_type == "ring_stain":
        low_r = max(4, int(radius * 0.15))
        high_r = max(low_r + 2, int(radius * 0.5))
        stain_r = int(rng.integers(low_r, high_r))

        offset_range = max(2, int(radius * 0.25))
        stain_cx = int(center[0] + rng.integers(-offset_range, offset_range + 1))
        stain_cy = int(center[1] + rng.integers(-offset_range, offset_range + 1))

        # Direct circular ring overlay with alpha blend
        overlay = img_np.copy()
        cv2.circle(overlay, (stain_cx, stain_cy), stain_r, (160, 130, 90), -1)
        cv2.circle(overlay, (stain_cx, stain_cy), max(1, int(stain_r * 0.7)), (65, 78, 98), -1)
        cv2.addWeighted(overlay, 0.4, img_np, 0.6, 0, dst=img_np)
        img_np[~wafer_circle] = (16, 16, 20)
        cv2.circle(mask, (stain_cx, stain_cy), stain_r, 2, -1)
        cv2.circle(mask, (stain_cx, stain_cy), max(1, int(stain_r * 0.7)), 0, -1)

        bx1, by1 = max(0, stain_cx - stain_r), max(0, stain_cy - stain_r)
        bx2, by2 = min(width, stain_cx + stain_r), min(height, stain_cy + stain_r)
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("ring_stain")

    elif not d_norm and defect_type in ["center_stain", "dead_die"]:
        low_cr = max(4, int(radius * 0.15))
        high_cr = max(low_cr + 2, int(radius * 0.35))
        cr = int(rng.integers(low_cr, high_cr))
        cv2.circle(img_np, center, cr, (130, 95, 70), -1)
        cv2.circle(mask, center, cr, 3, -1)

        bx1, by1 = max(0, center[0] - cr), max(0, center[1] - cr)
        bx2, by2 = min(width, center[0] + cr), min(height, center[1] + cr)
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("center_stain")

    # Clamping
    clamped_boxes = []
    for b in bboxes:
        clamped_boxes.append([
            max(0, min(width, b[0])),
            max(0, min(height, b[1])),
            max(0, min(width, b[2])),
            max(0, min(height, b[3])),
        ])

    is_ok = (len(labels) == 0)
    cls_label = "OK" if is_ok else labels[0]

    return SyntheticSample(
        image=img_np,
        mask=mask,
        bboxes=clamped_boxes,
        labels=labels,
        classification_label=cls_label,
        is_normal=is_ok,
        modality="wafer",
    )


# ============================================================================
# 3. Modality C: Brushed Metal Surface Renderer
# ============================================================================

def generate_metal_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """
    Procedurally renders a brushed metal inspection image with directional grain,
    lighting gradient, and defect injection (scratch, gouge, pit_corrosion).
    """
    if rng is None:
        rng = np.random.default_rng()

    raw_noise = rng.normal(165, 14, (height, width)).astype(np.float32)
    kernel_len = max(5, int(width * 0.12)) | 1
    kernel = np.ones((1, kernel_len), dtype=np.float32) / kernel_len
    brushed = cv2.filter2D(raw_noise, -1, kernel)

    y_ramp = np.linspace(-8, 8, height)[:, None]
    brushed = np.clip(brushed + y_ramp, 0, 255).astype(np.uint8)

    img_np = np.stack([brushed, np.clip(brushed - 2, 0, 255), np.clip(brushed + 3, 0, 255)], axis=-1).astype(np.uint8)
    mask = np.zeros((height, width), dtype=np.uint8)

    bboxes: List[List[int]] = []
    labels: List[str] = []
    margin_x = max(4, int(width * 0.1))
    margin_y = max(4, int(height * 0.1))
    d_norm = (defect_type is None or str(defect_type).lower() in ["none", "ok", "good", "pass"])

    if not d_norm and defect_type == "scratch":
        low_x = margin_x
        high_x = max(low_x + 1, width - margin_x)
        low_y = margin_y
        high_y = max(low_y + 1, height - margin_y)

        x1 = int(rng.integers(low_x, high_x))
        y1 = int(rng.integers(low_y, high_y))
        length = max(8, int(min(width, height) * 0.3))
        angle = float(rng.uniform(np.pi * 0.2, np.pi * 0.8))

        pts = []
        cur_x, cur_y = float(x1), float(y1)
        for _ in range(4):
            pts.append((int(np.clip(cur_x, 0, width - 1)), int(np.clip(cur_y, 0, height - 1))))
            cur_x += (length / 4.0) * np.cos(angle) + float(rng.integers(-2, 3))
            cur_y += (length / 4.0) * np.sin(angle) + float(rng.integers(-2, 3))
        pts_np = np.array(pts, dtype=np.int32)

        shadow_pts = pts_np + np.array([[1, 1]], dtype=np.int32)
        cv2.polylines(img_np, [shadow_pts], False, (40, 42, 45), 2)
        cv2.polylines(img_np, [pts_np], False, (245, 248, 255), 2)
        cv2.polylines(mask, [pts_np], False, 1, 2)

        xmin, ymin = np.min(pts_np, axis=0) - 2
        xmax, ymax = np.max(pts_np, axis=0) + 2
        bx1, by1 = max(0, int(xmin)), max(0, int(ymin))
        bx2, by2 = min(width, int(xmax)), min(height, int(ymax))
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("scratch")

    elif not d_norm and defect_type in ["gouge", "oil_smudge"]:
        gx = int(rng.integers(margin_x, max(margin_x + 1, width - margin_x)))
        gy = int(rng.integers(margin_y, max(margin_y + 1, height - margin_y)))
        gw = max(6, int(width * 0.08))
        gh = max(4, int(height * 0.05))

        cv2.ellipse(img_np, (gx + 2, gy + 2), (gw // 2 + 2, gh // 2 + 2), 20, 0, 360, (240, 245, 250), -1)
        cv2.ellipse(img_np, (gx, gy), (gw // 2, gh // 2), 20, 0, 360, (45, 48, 52), -1)
        cv2.ellipse(mask, (gx, gy), (gw // 2, gh // 2), 20, 0, 360, 2, -1)

        bx1, by1 = max(0, gx - gw // 2 - 2), max(0, gy - gh // 2 - 2)
        bx2, by2 = min(width, gx + gw // 2 + 2), min(height, gy + gh // 2 + 2)
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("gouge")

    elif not d_norm and defect_type == "pit_corrosion":
        cx = int(rng.integers(margin_x, max(margin_x + 1, width - margin_x)))
        cy = int(rng.integers(margin_y, max(margin_y + 1, height - margin_y)))
        cluster_r = max(5, int(min(width, height) * 0.09))

        num_pits = int(rng.integers(5, 12))
        for _ in range(num_pits):
            px = int(np.clip(cx + rng.integers(-cluster_r, cluster_r + 1), 0, width - 1))
            py = int(np.clip(cy + rng.integers(-cluster_r, cluster_r + 1), 0, height - 1))
            pr = max(1, int(cluster_r * 0.2))
            cv2.circle(img_np, (px, py), pr + 2, (105, 75, 55), -1)
            cv2.circle(img_np, (px, py), pr, (30, 25, 22), -1)
            cv2.circle(mask, (px, py), pr + 1, 3, -1)

        bx1, by1 = max(0, cx - cluster_r - 5), max(0, cy - cluster_r - 5)
        bx2, by2 = min(width, cx + cluster_r + 5), min(height, cy + cluster_r + 5)
        if bx2 > bx1 and by2 > by1:
            bboxes.append([bx1, by1, bx2, by2])
            labels.append("pit_corrosion")

    # Clamping
    clamped_boxes = []
    for b in bboxes:
        clamped_boxes.append([
            max(0, min(width, b[0])),
            max(0, min(height, b[1])),
            max(0, min(width, b[2])),
            max(0, min(height, b[3])),
        ])

    is_ok = (len(labels) == 0)
    cls_label = "OK" if is_ok else labels[0]

    return SyntheticSample(
        image=img_np,
        mask=mask,
        bboxes=clamped_boxes,
        labels=labels,
        classification_label=cls_label,
        is_normal=is_ok,
        modality="metal",
    )


# ============================================================================
# Modality Dispatcher
# ============================================================================

def render_sample(
    modality: str,
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """Dispatches sample generation to the appropriate domain engine."""
    mod = modality.lower().strip()
    if mod == "pcb":
        return generate_pcb_sample(width, height, defect_type, rng)
    elif mod == "wafer":
        return generate_wafer_sample(width, height, defect_type, rng)
    elif mod in ["metal", "surface"]:
        return generate_metal_sample(width, height, defect_type, rng)
    else:
        raise ValueError(f"Unknown modality: {modality}")


# ============================================================================
# Universal Programmatic Dataset Generator & Exporter
# ============================================================================

def generate_synthetic_dataset(
    output_dir_or_modality: Optional[Union[str, Path]] = None,
    num_samples_or_task: Optional[Union[int, str]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    num_samples: int = 100,
    modality: Optional[Union[str, List[str]]] = None,
    modalities: Optional[List[str]] = None,
    task: str = "all",
    image_size: Tuple[int, int] = (256, 256),
    width: Optional[int] = None,
    height: Optional[int] = None,
    split_ratio: float = 0.8,
    normal_ratio: float = 0.5,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Universal entrypoint for procedural synthetic dataset generation across all 4 tasks.
    Supports polymorphic argument order, single/multi modality, and complete directory export.
    """
    start_time = time.time()

    # Polymorphic argument resolution
    target_out_dir = output_dir
    target_num_samples = num_samples
    target_modality = modality
    target_task = task

    if isinstance(output_dir_or_modality, (str, Path)):
        s_val = str(output_dir_or_modality)
        if s_val in ["pcb", "wafer", "metal"]:
            target_modality = s_val
        else:
            target_out_dir = s_val

    if isinstance(num_samples_or_task, int):
        target_num_samples = num_samples_or_task
    elif isinstance(num_samples_or_task, str):
        target_task = num_samples_or_task

    if target_out_dir is None:
        target_out_dir = "./datasets/synthetic"

    # Input validations
    if not isinstance(target_task, str):
        raise ValueError(f"Invalid task: {target_task}. Must be one of: all, classification, detection, segmentation, anomaly")
    clean_task = target_task.lower().strip()
    if clean_task == "anomaly_detection":
        clean_task = "anomaly"
    valid_tasks = ["all", "classification", "detection", "segmentation", "anomaly"]
    if clean_task not in valid_tasks:
        raise ValueError(f"Invalid task: {target_task}. Must be one of: all, classification, detection, segmentation, anomaly")
    target_task = clean_task

    if target_num_samples <= 0:
        raise ValueError(f"num_samples must be greater than 0, got {target_num_samples}")

    if width is not None and height is not None:
        img_w, img_h = width, height
    else:
        img_w, img_h = image_size

    if img_w <= 0 or img_h <= 0:
        raise ValueError("image_size dimensions must be positive")

    out_path = Path(target_out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Resolve active modalities
    active_modalities: List[str] = []
    if modalities:
        active_modalities = [m.lower() for m in modalities]
    elif target_modality:
        if isinstance(target_modality, list):
            active_modalities = [m.lower() for m in target_modality]
        else:
            active_modalities = [target_modality.lower()]
    else:
        active_modalities = ["pcb", "wafer", "metal"]

    for m in active_modalities:
        if m not in ["pcb", "wafer", "metal"]:
            raise ValueError(f"Invalid modality: {m}")

    rng = np.random.default_rng(seed)

    # Calculate splits
    num_train = max(1, int(round(target_num_samples * split_ratio))) if split_ratio < 1.0 else target_num_samples
    num_val = target_num_samples - num_train

    # Collect defect categories across active modalities
    all_categories: List[Dict[str, Any]] = []
    cat_id_map: Dict[str, int] = {}
    c_idx = 1
    for m in active_modalities:
        for d in MODALITY_DEFECTS[m]:
            if d not in cat_id_map:
                cat_id_map[d] = c_idx
                all_categories.append({"id": c_idx, "name": d, "supercategory": m})
                c_idx += 1

    # Directory structures
    gen_classification = target_task in ["all", "classification"]
    gen_detection = target_task in ["all", "detection"]
    gen_segmentation = target_task in ["all", "segmentation"]
    gen_anomaly = target_task in ["all", "anomaly"]

    if gen_classification:
        cls_train_ok = out_path / "classification" / "train" / "OK"
        cls_val_ok = out_path / "classification" / "val" / "OK"
        cls_train_ok.mkdir(parents=True, exist_ok=True)
        cls_val_ok.mkdir(parents=True, exist_ok=True)

    if gen_detection:
        det_img_train = out_path / "detection" / "images" / "train"
        det_img_val = out_path / "detection" / "images" / "val"
        det_img_train.mkdir(parents=True, exist_ok=True)
        det_img_val.mkdir(parents=True, exist_ok=True)
        coco_train = {"images": [], "annotations": [], "categories": all_categories}
        coco_val = {"images": [], "annotations": [], "categories": all_categories}

    if gen_segmentation:
        seg_img_train = out_path / "segmentation" / "images" / "train"
        seg_img_val = out_path / "segmentation" / "images" / "val"
        seg_mask_train = out_path / "segmentation" / "masks" / "train"
        seg_mask_val = out_path / "segmentation" / "masks" / "val"
        seg_img_train.mkdir(parents=True, exist_ok=True)
        seg_img_val.mkdir(parents=True, exist_ok=True)
        seg_mask_train.mkdir(parents=True, exist_ok=True)
        seg_mask_val.mkdir(parents=True, exist_ok=True)

    if gen_anomaly:
        anom_train_good = out_path / "anomaly" / "train" / "good"
        anom_test_good = out_path / "anomaly" / "test" / "good"
        anom_train_good.mkdir(parents=True, exist_ok=True)
        anom_test_good.mkdir(parents=True, exist_ok=True)

    manifest_images: List[Dict[str, Any]] = []
    global_anno_id = 1

    # Generate samples deterministically
    for i in range(target_num_samples):
        is_train = (i < num_train)
        split_name = "train" if is_train else "val"
        curr_mod = active_modalities[i % len(active_modalities)]
        mod_defects = MODALITY_DEFECTS[curr_mod]

        is_ok = bool(rng.random() < normal_ratio)
        defect_choice = None if is_ok else str(rng.choice(mod_defects))

        sample = render_sample(curr_mod, img_w, img_h, defect_choice, rng=rng)
        file_name = f"{curr_mod}_{i:04d}.png"

        # 1. Classification export
        if gen_classification:
            cls_sub = "OK" if sample.is_normal else f"NG_{sample.classification_label}"
            cls_folder = out_path / "classification" / split_name / cls_sub
            cls_folder.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(cls_folder / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))

        # 2. Detection export
        if gen_detection:
            det_split_dir = det_img_train if is_train else det_img_val
            cv2.imwrite(str(det_split_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))

            coco_target = coco_train if is_train else coco_val
            coco_target["images"].append({
                "id": i + 1,
                "file_name": file_name,
                "width": img_w,
                "height": img_h,
            })

            for box, lbl in zip(sample.bboxes, sample.labels):
                cid = cat_id_map.get(lbl, 1)
                bw = box[2] - box[0]
                bh = box[3] - box[1]
                coco_target["annotations"].append({
                    "id": global_anno_id,
                    "image_id": i + 1,
                    "category_id": cid,
                    "bbox": [box[0], box[1], bw, bh],
                    "area": float(bw * bh),
                    "iscrowd": 0,
                })
                global_anno_id += 1

        # 3. Segmentation export
        if gen_segmentation:
            s_img_dir = seg_img_train if is_train else seg_img_val
            s_mask_dir = seg_mask_train if is_train else seg_mask_val
            cv2.imwrite(str(s_img_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
            cv2.imwrite(str(s_mask_dir / file_name), sample.mask)

        # 4. Anomaly Detection export (standard anomaly layout)
        if gen_anomaly:
            if is_train:
                # Train split MUST be 100% normal
                if sample.is_normal:
                    cv2.imwrite(str(anom_train_good / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
                else:
                    ok_sample = render_sample(curr_mod, img_w, img_h, None, rng=rng)
                    cv2.imwrite(str(anom_train_good / file_name), cv2.cvtColor(ok_sample.image, cv2.COLOR_RGB2BGR))
            else:
                if sample.is_normal:
                    cv2.imwrite(str(anom_test_good / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
                else:
                    anom_defect_dir = out_path / "anomaly" / "test" / sample.classification_label
                    anom_defect_dir.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(anom_defect_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))

                    gt_dir = out_path / "anomaly" / "ground_truth" / sample.classification_label
                    gt_dir.mkdir(parents=True, exist_ok=True)
                    bin_mask = np.where(sample.mask > 0, 255, 0).astype(np.uint8)
                    cv2.imwrite(str(gt_dir / f"{curr_mod}_{i:04d}_mask.png"), bin_mask)

        manifest_images.append({
            "id": f"{curr_mod}_{i:04d}",
            "modality": curr_mod,
            "split": split_name,
            "is_normal": sample.is_normal,
            "label": sample.classification_label,
            "num_bboxes": len(sample.bboxes),
            "bboxes": sample.bboxes,
        })

    # Save COCO detection JSON annotations
    if gen_detection:
        det_anno_dir = out_path / "detection"
        with open(det_anno_dir / "annotations_train.json", "w") as f:
            json.dump(coco_train, f, indent=2)
        with open(det_anno_dir / "annotations_val.json", "w") as f:
            json.dump(coco_val, f, indent=2)

    # Save segmentation class map
    if gen_segmentation:
        class_map = {"0": "background"}
        for cat in all_categories:
            class_map[str(cat["id"])] = cat["name"]
        with open(out_path / "segmentation" / "class_map.json", "w") as f:
            json.dump(class_map, f, indent=2)

    # Dataset summaries and master manifest
    summary: Dict[str, Any] = {
        "dataset_name": "VisionAI_Synthetic_Industrial",
        "task": target_task,
        "total_images": target_num_samples,
        "train_count": num_train,
        "val_count": num_val,
        "modalities": active_modalities,
        "image_size": [img_w, img_h],
        "split_ratio": split_ratio,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with open(out_path / "dataset_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    with open(out_path / "manifest.json", "w") as f:
        json.dump({
            "dataset_name": "VisionAI_Synthetic_Dataset",
            "summary": summary,
            "split_summary": summary,
            "categories": all_categories,
            "images": manifest_images,
        }, f, indent=2)

    elapsed = time.time() - start_time
    summary["duration_seconds"] = round(elapsed, 4)
    summary["status"] = "success"
    summary["output_dir"] = str(out_path.resolve())
    return summary


# ============================================================================
# CLI Entrypoint
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Vision AI Studio Procedural Industrial Defect Dataset Generator",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--modality", choices=["pcb", "wafer", "metal", "all"], default="pcb", help="Industrial domain modality")
    parser.add_argument("--task", choices=["classification", "detection", "segmentation", "anomaly", "all"], default="all", help="Target vision task format")
    parser.add_argument("--output-dir", "-o", default="./datasets/synthetic", help="Output directory path")
    parser.add_argument("--num-samples", "-n", type=int, default=100, help="Total number of images to generate")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed for deterministic reproducibility")
    parser.add_argument("--width", type=int, default=256, help="Image width in pixels")
    parser.add_argument("--height", type=int, default=256, help="Image height in pixels")
    parser.add_argument("--split-ratio", type=float, default=0.8, help="Train/val split ratio (e.g. 0.8 = 80%% train)")
    parser.add_argument("--normal-ratio", type=float, default=0.5, help="Proportion of defect-free normal (OK) images")

    args = parser.parse_args()

    mod_param = None if args.modality == "all" else args.modality
    result = generate_synthetic_dataset(
        output_dir=args.output_dir,
        num_samples=args.num_samples,
        modality=mod_param,
        task=args.task,
        width=args.width,
        height=args.height,
        split_ratio=args.split_ratio,
        normal_ratio=args.normal_ratio,
        seed=args.seed,
    )

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

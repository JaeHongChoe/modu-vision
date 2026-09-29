"""
Reference implementation of backend/engine/synthetic_generator.py for test harness.
Procedural industrial defect generator across PCB, Wafer, and Metal surfaces
supporting all 4 vision tasks with bit-for-bit determinism and microsecond rendering.
"""

import json
import os
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw


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
    image: np.ndarray             # (H, W, 3) uint8 RGB
    mask: np.ndarray              # (H, W) uint8 (0=bg, 1..K=defect classes)
    bboxes: List[List[int]]       # List of [xmin, ymin, xmax, ymax]
    labels: List[str]             # Class name per box
    classification_label: str     # "OK" or primary defect name
    is_normal: bool               # True if no defects
    modality: str                 # "pcb", "wafer", "metal"


def generate_pcb_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """Renders procedural PCB inspection sample with traces, pads, IC chip, and defects."""
    if rng is None:
        rng = np.random.default_rng()

    # Base FR4 substrate (green)
    base_color = (int(rng.integers(18, 28)), int(rng.integers(70, 85)), int(rng.integers(35, 48)))
    substrate = np.full((height, width, 3), base_color, dtype=np.int16)
    noise = rng.integers(-6, 7, size=(height, width, 3), dtype=np.int16)
    img_np = np.clip(substrate + noise, 0, 255).astype(np.uint8)

    img = Image.fromarray(img_np)
    mask = np.zeros((height, width), dtype=np.uint8)
    draw = ImageDraw.Draw(img)

    margin_x = max(6, int(width * 0.08))
    margin_y = max(6, int(height * 0.08))

    # Fiducials / crosshairs
    f_rad = max(2, int(min(width, height) * 0.015))
    for cx, cy in [(margin_x, margin_y), (width - margin_x, margin_y),
                   (margin_x, height - margin_y), (width - margin_x, height - margin_y)]:
        draw.line([(cx - f_rad * 2, cy), (cx + f_rad * 2, cy)], fill=(240, 240, 240), width=1)
        draw.line([(cx, cy - f_rad * 2), (cx, cy + f_rad * 2)], fill=(240, 240, 240), width=1)
        draw.ellipse([cx - f_rad, cy - f_rad, cx + f_rad, cy + f_rad], outline=(240, 240, 240), width=1)

    # Silkscreen IC border
    ic_box = [int(width * 0.28), int(height * 0.28), int(width * 0.72), int(height * 0.72)]
    if ic_box[2] > ic_box[0] + 10 and ic_box[3] > ic_box[1] + 10:
        draw.rectangle(ic_box, outline=(230, 230, 230), width=1)

    # Traces & Solder Pads
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

    # Central IC Package
    chip_x1, chip_y1 = int(width * 0.35), int(height * 0.35)
    chip_x2, chip_y2 = int(width * 0.65), int(height * 0.65)
    if chip_x2 > chip_x1 + 6 and chip_y2 > chip_y1 + 6:
        draw.rectangle([chip_x1, chip_y1, chip_x2, chip_y2], fill=(35, 38, 42), outline=(60, 65, 70))
        p1_r = max(1, int(min(width, height) * 0.012))
        draw.ellipse([chip_x1 + 3, chip_y1 + 3, chip_x1 + 3 + 2 * p1_r, chip_y1 + 3 + 2 * p1_r], fill=(90, 95, 100))

    bboxes = []
    labels = []
    d_norm = (defect_type is None or defect_type.lower() in ["none", "ok", "good"])

    if not d_norm and defect_type == "solder_bridge":
        bx = int(rng.integers(margin_x + 10, max(margin_x + 11, width - margin_x - 30)))
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
        gx = int(rng.integers(margin_x + 10, max(margin_x + 11, width - margin_x - gw - 10)))
        draw.rectangle([gx, ty - gh // 2, gx + gw, ty + gh // 2], fill=base_color)
        draw.line([(gx, ty - gh // 2), (gx + 1, ty + gh // 2)], fill=(15, 20, 15), width=1)
        draw.line([(gx + gw, ty - gh // 2), (gx + gw - 1, ty + gh // 2)], fill=(15, 20, 15), width=1)
        cv2.rectangle(mask, (gx, ty - gh // 2), (gx + gw, ty + gh // 2), 2, -1)
        bboxes.append([gx, ty - gh // 2, gx + gw, ty + gh // 2])
        labels.append("broken_trace")

    elif not d_norm and defect_type == "solder_ball":
        r = max(3, int(min(width, height) * 0.025))
        sx = int(rng.integers(margin_x + r + 2, max(margin_x + r + 3, width - margin_x - r - 2)))
        sy = int(rng.integers(margin_y + r + 2, max(margin_y + r + 3, height - margin_y - r - 2)))
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


def generate_wafer_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """Renders semiconductor wafer inspection sample with circular die array and defects."""
    if rng is None:
        rng = np.random.default_rng()

    img_np = np.full((height, width, 3), (16, 16, 20), dtype=np.uint8)
    mask = np.zeros((height, width), dtype=np.uint8)

    center = (width // 2, height // 2)
    radius = int(min(width, height) * 0.44)

    wafer_circle_mask = np.zeros((height, width), dtype=np.uint8)
    cv2.circle(wafer_circle_mask, center, radius, 255, -1)

    y_idx, x_idx = np.ogrid[:height, :width]
    dist_from_center = np.sqrt((x_idx - center[0]) ** 2 + (y_idx - center[1]) ** 2)
    norm_dist = np.clip(dist_from_center / max(1, radius), 0, 1.0)

    silicon_r = np.clip(55 + 20 * (1.0 - norm_dist), 0, 255).astype(np.uint8)
    silicon_g = np.clip(68 + 22 * (1.0 - norm_dist), 0, 255).astype(np.uint8)
    silicon_b = np.clip(88 + 28 * (1.0 - norm_dist), 0, 255).astype(np.uint8)
    silicon_base = np.stack([silicon_r, silicon_g, silicon_b], axis=-1)

    img_np[wafer_circle_mask == 255] = silicon_base[wafer_circle_mask == 255]

    die_size = max(10, int(min(width, height) * 0.07))
    street_color = (35, 42, 54)
    for x in range(center[0] - radius, center[0] + radius, die_size):
        if 0 <= x < width:
            col_mask = (wafer_circle_mask == 255) & (x_idx == x)
            img_np[col_mask] = street_color
    for y in range(center[1] - radius, center[1] + radius, die_size):
        if 0 <= y < height:
            row_mask = (wafer_circle_mask == 255) & (y_idx == y)
            img_np[row_mask] = street_color

    cv2.circle(img_np, center, radius, (110, 125, 145), 1)
    cv2.circle(img_np, center, max(1, radius - 1), (40, 50, 65), 1)

    bboxes = []
    labels = []
    d_norm = (defect_type is None or defect_type.lower() in ["none", "ok", "good"])

    if not d_norm and defect_type in ["micro_scratch", "scratch"]:
        start_x = int(rng.integers(center[0] - radius // 2, center[0] + radius // 2))
        start_y = int(rng.integers(center[1] - radius // 2, center[1] + radius // 2))
        length = int(rng.integers(max(20, radius // 3), max(30, int(radius * 0.7))))
        angle = float(rng.uniform(0, 2 * np.pi))

        pts = []
        cx_cur, cy_cur = float(start_x), float(start_y)
        for _ in range(5):
            pts.append((int(cx_cur), int(cy_cur)))
            cx_cur += (length / 5.0) * np.cos(angle) + float(rng.integers(-3, 4))
            cy_cur += (length / 5.0) * np.sin(angle) + float(rng.integers(-3, 4))

        pts_np = np.array(pts, dtype=np.int32)
        cv2.polylines(img_np, [pts_np], False, (225, 235, 245), 2)
        cv2.polylines(mask, [pts_np], False, 1, 2)

        xmin, ymin = np.min(pts_np, axis=0) - 2
        xmax, ymax = np.max(pts_np, axis=0) + 2
        bboxes.append([int(xmin), int(ymin), int(xmax), int(ymax)])
        labels.append("micro_scratch")

    elif not d_norm and defect_type == "ring_stain":
        stain_r = int(rng.integers(max(10, radius // 6), max(15, radius // 2)))
        stain_cx = int(center[0] + rng.integers(-radius // 4, radius // 4 + 1))
        stain_cy = int(center[1] + rng.integers(-radius // 4, radius // 4 + 1))

        stain_layer = np.zeros_like(img_np)
        cv2.circle(stain_layer, (stain_cx, stain_cy), stain_r, (160, 130, 90), -1)
        cv2.circle(stain_layer, (stain_cx, stain_cy), int(stain_r * 0.7), (0, 0, 0), -1)

        stain_mask = (cv2.cvtColor(stain_layer, cv2.COLOR_BGR2GRAY) > 0) & (wafer_circle_mask == 255)
        img_np[stain_mask] = cv2.addWeighted(img_np, 0.65, stain_layer, 0.35, 0)[stain_mask]
        mask[stain_mask] = 2

        bboxes.append([stain_cx - stain_r, stain_cy - stain_r, stain_cx + stain_r, stain_cy + stain_r])
        labels.append("ring_stain")

    elif not d_norm and defect_type in ["center_stain", "dead_die"]:
        cr = int(rng.integers(max(10, radius // 6), max(18, radius // 3)))
        cv2.circle(img_np, center, cr, (130, 95, 70), -1)
        cv2.circle(mask, center, cr, 3, -1)
        bboxes.append([center[0] - cr, center[1] - cr, center[0] + cr, center[1] + cr])
        labels.append("center_stain")

    # Clamp boxes
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


def generate_metal_sample(
    width: int = 256,
    height: int = 256,
    defect_type: Optional[str] = None,
    rng: Optional[np.random.Generator] = None,
) -> SyntheticSample:
    """Renders brushed metal inspection sample with directional grain and surface flaws."""
    if rng is None:
        rng = np.random.default_rng()

    raw_noise = rng.normal(165, 14, (height, width)).astype(np.float32)
    kernel_len = max(9, int(width * 0.12)) | 1
    kernel = np.ones((1, kernel_len), dtype=np.float32) / kernel_len
    brushed = cv2.filter2D(raw_noise, -1, kernel)

    y_ramp = np.linspace(-8, 8, height)[:, None]
    brushed = np.clip(brushed + y_ramp, 0, 255).astype(np.uint8)

    img_np = np.stack([brushed, np.clip(brushed - 2, 0, 255), np.clip(brushed + 3, 0, 255)], axis=-1).astype(np.uint8)
    mask = np.zeros((height, width), dtype=np.uint8)

    bboxes = []
    labels = []
    d_norm = (defect_type is None or defect_type.lower() in ["none", "ok", "good"])

    if not d_norm and defect_type == "scratch":
        x1 = int(rng.integers(20, max(21, width - 40)))
        y1 = int(rng.integers(20, max(21, height - 40)))
        length = int(rng.integers(max(25, int(min(width, height) * 0.2)), max(35, int(min(width, height) * 0.5))))
        angle = float(rng.uniform(np.pi * 0.2, np.pi * 0.8))

        pts = []
        cur_x, cur_y = float(x1), float(y1)
        for _ in range(4):
            pts.append((int(cur_x), int(cur_y)))
            cur_x += (length / 4.0) * np.cos(angle) + float(rng.integers(-3, 4))
            cur_y += (length / 4.0) * np.sin(angle) + float(rng.integers(-3, 4))
        pts_np = np.array(pts, dtype=np.int32)

        shadow_pts = pts_np + np.array([[1, 1]], dtype=np.int32)
        cv2.polylines(img_np, [shadow_pts], False, (40, 42, 45), 2)
        cv2.polylines(img_np, [pts_np], False, (245, 248, 255), 2)
        cv2.polylines(mask, [pts_np], False, 1, 2)

        xmin, ymin = np.min(pts_np, axis=0) - 2
        xmax, ymax = np.max(pts_np, axis=0) + 2
        bboxes.append([int(xmin), int(ymin), int(xmax), int(ymax)])
        labels.append("scratch")

    elif not d_norm and defect_type == "gouge":
        gx = int(rng.integers(30, max(31, width - 30)))
        gy = int(rng.integers(30, max(31, height - 30)))
        gw = max(8, int(width * 0.08))
        gh = max(6, int(height * 0.05))

        cv2.ellipse(img_np, (gx + 2, gy + 2), (gw // 2 + 2, gh // 2 + 2), 20, 0, 360, (240, 245, 250), -1)
        cv2.ellipse(img_np, (gx, gy), (gw // 2, gh // 2), 20, 0, 360, (45, 48, 52), -1)
        cv2.ellipse(mask, (gx, gy), (gw // 2, gh // 2), 20, 0, 360, 2, -1)

        bboxes.append([gx - gw // 2 - 2, gy - gh // 2 - 2, gx + gw // 2 + 2, gy + gh // 2 + 2])
        labels.append("gouge")

    elif not d_norm and defect_type in ["pit_corrosion", "oil_smudge"]:
        cx = int(rng.integers(30, max(31, width - 30)))
        cy = int(rng.integers(30, max(31, height - 30)))
        cluster_r = max(10, int(min(width, height) * 0.08))

        num_pits = int(rng.integers(6, 12))
        for _ in range(num_pits):
            px = int(cx + rng.integers(-cluster_r, cluster_r + 1))
            py = int(cy + rng.integers(-cluster_r, cluster_r + 1))
            pr = max(2, int(min(width, height) * 0.015))
            cv2.circle(img_np, (px, py), pr + 2, (105, 75, 55), -1)
            cv2.circle(img_np, (px, py), pr, (30, 25, 22), -1)
            cv2.circle(mask, (px, py), pr + 1, 3, -1)

        bboxes.append([cx - cluster_r - 5, cy - cluster_r - 5, cx + cluster_r + 5, cy + cluster_r + 5])
        labels.append("pit_corrosion")

    # Clamp boxes
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
    elif mod == "metal":
        return generate_metal_sample(width, height, defect_type, rng)
    else:
        raise ValueError(f"Unknown modality: {modality}")


def generate_synthetic_dataset(
    output_dir: str,
    num_samples: int = 100,
    modalities: Optional[List[str]] = None,
    image_size: Tuple[int, int] = (256, 256),
    split_ratio: float = 0.8,
    normal_ratio: float = 0.5,
    seed: int = 42,
) -> Dict[str, Any]:
    """
    Generates multi-task industrial dataset on disk for all 4 vision tasks.
    """
    if num_samples <= 0:
        raise ValueError("num_samples must be greater than 0")

    width, height = image_size
    if width <= 0 or height <= 0:
        raise ValueError("image_size dimensions must be positive")

    if modalities is None:
        modalities = ["pcb", "wafer", "metal"]
    else:
        for m in modalities:
            if m.lower() not in ["pcb", "wafer", "metal"]:
                raise ValueError(f"Invalid modality: {m}")

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)

    # Multi-task directory layouts
    cls_train_ok = out_path / "classification" / "train" / "OK"
    cls_val_ok = out_path / "classification" / "val" / "OK"
    cls_train_ok.mkdir(parents=True, exist_ok=True)
    cls_val_ok.mkdir(parents=True, exist_ok=True)

    det_img_train = out_path / "detection" / "images" / "train"
    det_img_val = out_path / "detection" / "images" / "val"
    det_img_train.mkdir(parents=True, exist_ok=True)
    det_img_val.mkdir(parents=True, exist_ok=True)

    seg_img_train = out_path / "segmentation" / "images" / "train"
    seg_img_val = out_path / "segmentation" / "images" / "val"
    seg_mask_train = out_path / "segmentation" / "masks" / "train"
    seg_mask_val = out_path / "segmentation" / "masks" / "val"
    seg_img_train.mkdir(parents=True, exist_ok=True)
    seg_img_val.mkdir(parents=True, exist_ok=True)
    seg_mask_train.mkdir(parents=True, exist_ok=True)
    seg_mask_val.mkdir(parents=True, exist_ok=True)

    anom_train_good = out_path / "anomaly" / "train" / "good"
    anom_test_good = out_path / "anomaly" / "test" / "good"
    anom_train_good.mkdir(parents=True, exist_ok=True)
    anom_test_good.mkdir(parents=True, exist_ok=True)

    num_train = max(1, int(round(num_samples * split_ratio))) if split_ratio < 1.0 else num_samples
    num_val = num_samples - num_train

    # Pre-build defect categories
    all_categories = []
    cat_id = 1
    for m in modalities:
        for d in MODALITY_DEFECTS[m]:
            all_categories.append({"id": cat_id, "name": d, "supercategory": m})
            cat_id += 1

    coco_train = {"images": [], "annotations": [], "categories": all_categories}
    coco_val = {"images": [], "annotations": [], "categories": all_categories}

    manifest_images = []
    global_anno_id = 1

    for i in range(num_samples):
        is_train = (i < num_train)
        split_name = "train" if is_train else "val"
        mod = modalities[i % len(modalities)]
        defects = MODALITY_DEFECTS[mod]

        # In anomaly mode, train MUST be 100% normal
        # For general sample generation, obey normal_ratio
        is_ok = bool(rng.random() < normal_ratio)
        d_type = None if is_ok else str(rng.choice(defects))

        sample = render_sample(mod, width, height, d_type, rng)
        img_id_str = f"{mod}_{i:04d}"
        file_name = f"{img_id_str}.png"

        # 1. Classification
        cls_sub = "OK" if sample.is_normal else f"NG_{sample.classification_label}"
        cls_folder = out_path / "classification" / split_name / cls_sub
        cls_folder.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(cls_folder / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))

        # 2. Detection
        det_split_dir = det_img_train if is_train else det_img_val
        cv2.imwrite(str(det_split_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))

        coco_target = coco_train if is_train else coco_val
        coco_target["images"].append({
            "id": i + 1,
            "file_name": file_name,
            "width": width,
            "height": height,
        })

        for box, lbl in zip(sample.bboxes, sample.labels):
            cid = next((c["id"] for c in all_categories if c["name"] == lbl), 1)
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

        # 3. Segmentation
        s_img_dir = seg_img_train if is_train else seg_img_val
        s_mask_dir = seg_mask_train if is_train else seg_mask_val
        cv2.imwrite(str(s_img_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
        cv2.imwrite(str(s_mask_dir / file_name), sample.mask)

        # 4. Anomaly Detection
        if is_train:
            # Train split MUST be normal: if sample had defect, generate normal replacement for anomaly train
            if sample.is_normal:
                cv2.imwrite(str(anom_train_good / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
            else:
                ok_sample = render_sample(mod, width, height, None, rng)
                cv2.imwrite(str(anom_train_good / file_name), cv2.cvtColor(ok_sample.image, cv2.COLOR_RGB2BGR))
        else:
            if sample.is_normal:
                cv2.imwrite(str(anom_test_good / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
            else:
                anom_defect_dir = out_path / "anomaly" / "test" / sample.classification_label
                anom_defect_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(anom_defect_dir / file_name), cv2.cvtColor(sample.image, cv2.COLOR_RGB2BGR))
                # Save ground truth mask
                gt_dir = out_path / "anomaly" / "ground_truth" / sample.classification_label
                gt_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(gt_dir / f"{img_id_str}_mask.png"), sample.mask)

        manifest_images.append({
            "id": img_id_str,
            "modality": mod,
            "split": split_name,
            "is_normal": sample.is_normal,
            "label": sample.classification_label,
            "num_bboxes": len(sample.bboxes),
        })

    # Save detection annotations
    det_anno_dir = out_path / "detection"
    with open(det_anno_dir / "annotations_train.json", "w") as f:
        json.dump(coco_train, f, indent=2)
    with open(det_anno_dir / "annotations_val.json", "w") as f:
        json.dump(coco_val, f, indent=2)

    # Save segmentation class map
    class_map = {"0": "background"}
    for cat in all_categories:
        class_map[str(cat["id"])] = cat["name"]
    with open(out_path / "segmentation" / "class_map.json", "w") as f:
        json.dump(class_map, f, indent=2)

    # Manifest and Summary
    summary = {
        "dataset_name": "VisionAI_Synthetic_Industrial",
        "total_images": num_samples,
        "train_count": num_train,
        "val_count": num_val,
        "modalities": modalities,
        "image_size": [width, height],
        "split_ratio": split_ratio,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    with open(out_path / "dataset_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    with open(out_path / "manifest.json", "w") as f:
        json.dump({
            "summary": summary,
            "split_summary": summary,
            "categories": all_categories,
            "images": manifest_images,
        }, f, indent=2)

    return summary

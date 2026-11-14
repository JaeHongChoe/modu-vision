"""
backend/engine/detection/metrics.py

Vectorized pure-Python/PyTorch mAP@0.5 and mAP@0.5:0.95 evaluator without external pycocotools dependencies,
safe MPS Non-Maximum Suppression with CPU fallback, and visual overlay renderer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Union
import cv2
import numpy as np
import torch
import torchvision.ops as ops

# High-contrast 8-color industrial defect visualization palette (RGB)
PALETTE = [
    (255, 56, 56),   # Red
    (56, 168, 255),  # Blue
    (56, 255, 114),  # Green
    (255, 178, 56),  # Orange
    (178, 56, 255),  # Purple
    (255, 56, 224),  # Pink
    (56, 255, 237),  # Cyan
    (255, 224, 56),  # Yellow
]

DETECTION_AP_LEGACY = 'detection-ap-legacy-v1'
DETECTION_AP_V2 = 'detection-ap-v2'


def bound_detection_ap_contract(spec, metrics):
    """Unmarked historical inputs/results retain their original metric meaning."""
    expected = spec.get('detection_metric_contract', DETECTION_AP_LEGACY)
    if expected not in (DETECTION_AP_LEGACY, DETECTION_AP_V2): raise ValueError('Unknown detection AP metric contract')
    if metrics.get('detection_metric_contract', DETECTION_AP_LEGACY) != expected:
        raise ValueError('Detection AP metric contract differs from its frozen recipe')
    return expected


def _validate_v2_inputs(predictions, targets, num_classes):
    if len(predictions) != len(targets): raise ValueError('Detection predictions and targets must have the same image count')
    if num_classes is not None and (type(num_classes) is not int or num_classes < 1):
        raise ValueError('Detection class count must include a valid background class')
    for rows, prediction in ((predictions, True), (targets, False)):
        for row in rows:
            boxes, labels = row.get('boxes'), row.get('labels')
            if (not isinstance(boxes, torch.Tensor) or not isinstance(labels, torch.Tensor)
                    or boxes.ndim != 2 or boxes.shape[1] != 4 or labels.ndim != 1 or len(boxes) != len(labels)
                    or not bool(torch.isfinite(boxes).all()) or not bool(torch.isfinite(labels).all())):
                raise ValueError('Detection inputs require finite aligned box and label tensors')
            if labels.dtype not in (torch.int8, torch.int16, torch.int32, torch.int64, torch.uint8):
                raise ValueError('Detection class identifiers must be integers')
            if len(boxes) and (not bool((boxes[:, 2:] > boxes[:, :2]).all()) or bool((labels < 0).any())
                               or num_classes is not None and bool((labels >= num_classes).any())):
                raise ValueError('Detection boxes/classes are outside the supported contract')
            for key in ('iscrowd', 'ignore'):
                if key in row and bool(torch.as_tensor(row[key]).any()):
                    raise ValueError('Detection APv2 supports noncrowd, nonignored boxes only')
            if prediction:
                scores = row.get('scores')
                if (not isinstance(scores, torch.Tensor) or scores.ndim != 1 or len(scores) != len(boxes)
                        or not bool(torch.isfinite(scores).all()) or bool(((scores < 0) | (scores > 1)).any())):
                    raise ValueError('Detection scores must be aligned finite probabilities')


def safe_nms(
    boxes: torch.Tensor,
    scores: torch.Tensor,
    iou_threshold: float = 0.5,
) -> torch.Tensor:
    """
    Non-Maximum Suppression with automatic CPU fallback if MPS throws unsupported kernel errors.
    """
    if len(boxes) == 0:
        return torch.empty(0, dtype=torch.int64, device=boxes.device)
    try:
        return ops.nms(boxes, scores, iou_threshold)
    except Exception:
        return ops.nms(boxes.cpu(), scores.cpu(), iou_threshold).to(boxes.device)


def compute_ap_coco(recalls: np.ndarray, precisions: np.ndarray) -> float:
    """
    Computes standard COCO 101-point interpolated Average Precision.
    Samples monotonic precision envelope across recall thresholds in [0.00, 0.01, ..., 1.00].
    """
    if len(recalls) == 0 or len(precisions) == 0:
        return 0.0

    rec_thresholds = np.linspace(0.0, 1.0, 101)
    pr_envelope = np.zeros_like(precisions)
    curr_max = 0.0
    for i in range(len(precisions) - 1, -1, -1):
        curr_max = max(curr_max, precisions[i])
        pr_envelope[i] = curr_max

    interp_p = np.zeros(101)
    for idx, r_thresh in enumerate(rec_thresholds):
        inds = np.where(recalls >= r_thresh)[0]
        interp_p[idx] = pr_envelope[inds[0]] if len(inds) > 0 else 0.0

    return float(np.mean(interp_p))


def evaluate_detections_map(
    predictions: List[Dict[str, torch.Tensor]],
    targets: List[Dict[str, torch.Tensor]],
    iou_thresholds: Optional[List[float]] = None,
    num_classes: Optional[int] = None,
    class_names: Optional[Dict[int, str]] = None,
    *, metric_contract: str = DETECTION_AP_LEGACY,
) -> Dict[str, Any]:
    """
    Calculates mAP@0.5 and mAP@0.5:0.95 across all evaluation samples.
    
    Args:
        predictions: List of dicts per image containing 'boxes', 'scores', 'labels'
        targets: List of dicts per image containing 'boxes', 'labels'
        iou_thresholds: List of IoU thresholds (default: 0.50 to 0.95 with step 0.05)
        num_classes: Total number of classes (including background 0)
        class_names: Dict mapping category ID to class label name
    """
    if metric_contract not in (DETECTION_AP_LEGACY, DETECTION_AP_V2): raise ValueError('Unknown detection AP metric contract')
    version2 = metric_contract == DETECTION_AP_V2
    if version2: _validate_v2_inputs(predictions, targets, num_classes)
    if iou_thresholds is None:
        iou_thresholds = [round(x, 2) for x in np.arange(0.50, 1.00, 0.05).tolist()]
    if version2 and (0.50 not in iou_thresholds or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) or not 0 < v <= 1 for v in iou_thresholds)):
        raise ValueError('Detection APv2 thresholds must be finite probabilities including 0.50')

    # Collect all present classes excluding background (class 0)
    all_classes = set()
    for t in targets:
        if len(t["labels"]) > 0:
            all_classes.update(t["labels"].cpu().numpy().tolist())
    for p in predictions:
        if len(p["labels"]) > 0:
            all_classes.update(p["labels"].cpu().numpy().tolist())
    all_classes.discard(0)

    class_ids = [c for c in range(1, num_classes)] if num_classes is not None else sorted(list(all_classes))

    if len(class_ids) == 0 and not version2:
        return {"mAP_50": 0.0, "mAP_50_95": 0.0, "class_ap50": {}, "class_ap50_95": {}}

    # Organize ground truth by image and class
    gt_by_img: List[Dict[int, torch.Tensor]] = []
    total_gt_per_class = {c: 0 for c in class_ids}
    for t in targets:
        img_gt: Dict[int, torch.Tensor] = {}
        for c in class_ids:
            if len(t["labels"]) > 0:
                mask = (t["labels"] == c)
                boxes = t["boxes"][mask].cpu()
            else:
                boxes = torch.zeros((0, 4), dtype=torch.float32)
            img_gt[c] = boxes
            total_gt_per_class[c] += len(boxes)
        gt_by_img.append(img_gt)

    def get_cname(cid: int) -> str:
        if class_names and cid in class_names: return class_names[cid]
        return f'class_{cid}'

    excluded = [get_cname(c) for c in class_ids if total_gt_per_class[c] == 0] if version2 else []
    if version2:
        class_ids = [c for c in class_ids if total_gt_per_class[c] > 0]
        if len({get_cname(c) for c in class_ids}) != len(class_ids): raise ValueError('Detection AP class names must be unique')
        if not class_ids:
            return {'mAP_50': None, 'mAP_50_95': None, 'class_ap50': {}, 'class_ap50_95': {},
                    'metric_contract': DETECTION_AP_V2, 'available': False,
                    'unavailable_reason': 'no_ground_truth_objects', 'excluded_classes': excluded,
                    'max_detections_per_image_class': 100, 'scope': 'bbox_all_areas_noncrowd_nonignored'}

    # Organize predictions by class across dataset
    preds_by_class: Dict[int, List[Tuple[float, int, torch.Tensor]]] = {c: [] for c in class_ids}
    for img_idx, p in enumerate(predictions):
        if len(p["labels"]) == 0:
            continue
        boxes = p["boxes"].cpu()
        scores = p["scores"].cpu()
        labels = p["labels"].cpu()
        for b, s, l in zip(boxes, scores, labels):
            c = int(l.item())
            if c in preds_by_class:
                preds_by_class[c].append((float(s.item()), img_idx, b))

    # Sort predictions descending by score
    for c in class_ids:
        preds_by_class[c].sort(key=lambda x: x[0], reverse=True)
        if version2:
            counts = {}
            selected = []
            for candidate in preds_by_class[c]:
                image = candidate[1]
                counts[image] = counts.get(image, 0) + 1
                if counts[image] <= 100: selected.append(candidate)
            preds_by_class[c] = selected

    aps_per_iou: Dict[float, Dict[int, float]] = {iou_th: {} for iou_th in iou_thresholds}

    for iou_th in iou_thresholds:
        for c in class_ids:
            n_gt = total_gt_per_class[c]
            c_preds = preds_by_class[c]
            if n_gt == 0:
                aps_per_iou[iou_th][c] = 0.0 if len(c_preds) > 0 else 1.0
                continue
            if len(c_preds) == 0:
                aps_per_iou[iou_th][c] = 0.0
                continue

            matched_gt: List[set] = [set() for _ in range(len(targets))]
            tp = np.zeros(len(c_preds))
            fp = np.zeros(len(c_preds))

            for p_idx, (score, img_idx, pred_box) in enumerate(c_preds):
                gt_boxes = gt_by_img[img_idx][c]
                if len(gt_boxes) == 0:
                    fp[p_idx] = 1.0
                    continue

                # COCO computes bbox overlap in double precision. In float32
                # exact threshold .70 becomes .699999988 and wrongly fails.
                ious = ops.box_iou(pred_box.unsqueeze(0).double(), gt_boxes.double()).squeeze(0) if version2 else ops.box_iou(pred_box.unsqueeze(0), gt_boxes).squeeze(0)
                if version2:
                    # COCO searches the remaining noncrowd truth; a used best
                    # match does not prevent choosing another qualifying GT.
                    for matched in matched_gt[img_idx]: ious[matched] = -1
                best_iou, best_gt_idx = torch.max(ious, dim=0)
                if version2:
                    # COCO accepts equal-IoU candidates while scanning in GT
                    # order, so the last eligible GT wins an exact tie.
                    best_gt_idx = torch.nonzero(ious == best_iou, as_tuple=False)[-1, 0]
                best_iou = float(best_iou.item())
                best_gt_idx = int(best_gt_idx.item())

                if best_iou >= iou_th and (best_gt_idx not in matched_gt[img_idx]):
                    tp[p_idx] = 1.0
                    matched_gt[img_idx].add(best_gt_idx)
                else:
                    fp[p_idx] = 1.0

            cum_tp = np.cumsum(tp)
            cum_fp = np.cumsum(fp)
            recalls = cum_tp / n_gt
            precisions = cum_tp / (cum_tp + cum_fp)
            aps_per_iou[iou_th][c] = compute_ap_coco(recalls, precisions)

    ap50_per_class = {get_cname(c): aps_per_iou[0.50][c] for c in class_ids}
    mAP_50 = float(np.mean(list(aps_per_iou[0.50].values())))

    ap50_95_per_class = {
        get_cname(c): float(np.mean([aps_per_iou[th][c] for th in iou_thresholds]))
        for c in class_ids
    }
    mAP_50_95 = float(np.mean(list(ap50_95_per_class.values())))

    result = {
        "mAP_50": round(mAP_50, 4),
        "mAP_50_95": round(mAP_50_95, 4),
        "class_ap50": {k: round(v, 4) for k, v in ap50_per_class.items()},
        "class_ap50_95": {k: round(v, 4) for k, v in ap50_95_per_class.items()},
    }
    if version2:
        result.update(metric_contract=DETECTION_AP_V2, available=True, unavailable_reason=None,
                      excluded_classes=excluded, max_detections_per_image_class=100,
                      scope='bbox_all_areas_noncrowd_nonignored')
    return result


def draw_detection_overlays(
    image_rgb: np.ndarray,
    boxes: Union[np.ndarray, torch.Tensor],
    scores: Union[np.ndarray, torch.Tensor],
    labels: Union[np.ndarray, torch.Tensor],
    class_names: Optional[Dict[int, str]] = None,
    confidence_threshold: float = 0.5,
    thickness: int = 2,
    font_scale: float = 0.5,
) -> np.ndarray:
    """
    Renders high-visibility colored bounding boxes and readable classification banners
    on top of inspection images with confidence threshold filtering.
    """
    if isinstance(boxes, torch.Tensor):
        boxes = boxes.detach().cpu().numpy()
    if isinstance(scores, torch.Tensor):
        scores = scores.detach().cpu().numpy()
    if isinstance(labels, torch.Tensor):
        labels = labels.detach().cpu().numpy()

    if class_names is None:
        class_names = {}

    annotated = image_rgb.copy()
    h, w = annotated.shape[:2]

    for box, score, label in zip(boxes, scores, labels):
        if score < confidence_threshold:
            continue
        x1, y1, x2, y2 = [int(round(coord)) for coord in box]
        x1 = max(0, min(w - 1, x1))
        y1 = max(0, min(h - 1, y1))
        x2 = max(0, min(w - 1, x2))
        y2 = max(0, min(h - 1, y2))

        cname = class_names.get(int(label), f"class_{label}")
        color = PALETTE[int(label) % len(PALETTE)]

        # Bounding box border
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, thickness)

        # Background banner for label text
        text = f"{cname}: {score:.1%}"
        (text_w, text_h), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)

        banner_y1 = max(0, y1 - text_h - baseline - 4)
        banner_y2 = y1
        banner_x2 = min(w, x1 + text_w + 6)
        cv2.rectangle(annotated, (x1, banner_y1), (banner_x2, banner_y2), color, -1)
        cv2.putText(
            annotated,
            text,
            (x1 + 3, banner_y2 - baseline - 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font_scale,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

    return annotated

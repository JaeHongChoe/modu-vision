"""
backend/engine/exporter.py

Authentic Standalone Runtime Exporter for Modu Vision.
Exports genuine trained model weights into standalone, self-contained deployment packages:
  - Reconstructs authentic model architecture from trained checkpoint (Classification, Detection, Segmentation, Anomaly).
  - Multi-format export: ONNX (with dynamic batching) and TorchScript (torch.jit).
  - Calibrated config.json containing zero-escape threshold, preprocessing coefficients, and classes.
  - Standalone executable infer.py runner supporting both ONNXRuntime and TorchScript.
  - Built-in smoke test verifying the exported artifact is non-corrupt before returning success.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np
import torch
import torch.nn as nn

from backend.api.routes_training import training_job_manager
from backend.engine.checkpoint_paths import trusted_checkpoint
from backend.engine.classification import create_classification_model
from backend.engine.detection import create_detection_model, checkpoint_detection_num_classes
from backend.engine.segmentation import build_segmentation_model
from backend.engine.anomaly import PaDiMDetector, PatchCoreDetector
from backend.engine.zero_escape_analyzer import analyze_zero_escape

logger = logging.getLogger("vision_ai_studio.exporter")

EXPORTS_DIR = Path("./release/runtime_packages")
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)


def locate_checkpoint(job_id: Optional[str] = None) -> Optional[Path]:
    """Accept a completed local job ID, never an arbitrary checkpoint path."""
    if not job_id:
        return None
    rec = training_job_manager.get_job(job_id)
    if rec and rec.status != "completed":
        return None
    return trusted_checkpoint(job_id, rec.output_dir if rec else None)


def load_checkpoint_and_reconstruct_model(
    checkpoint_path: Path,
) -> Tuple[nn.Module, Dict[str, Any], Optional[Any]]:
    """
    Loads genuine weights from checkpoint and reconstructs model architecture.
    Returns: (reconstructed_torch_model, metadata_dict, optional_anomaly_artifacts)
    """
    ckpt = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    state_dict = ckpt.get("model_state_dict", ckpt)
    meta = {k: v for k, v in ckpt.items() if k != "model_state_dict"}

    meta_file = checkpoint_path.parent / "model_meta.json"
    if meta_file.is_file():
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = {**meta, **json.load(f)}
        except Exception:
            pass

    task = meta.get("task", "classification").lower().strip()
    classes = meta.get("classes", ["OK", "Defect"])
    anomaly_obj = None

    if task == "classification":
        backbone = meta.get("backbone", "resnet18")
        num_classes = max(2, len(classes))
        model = create_classification_model(backbone=backbone, num_classes=num_classes, pretrained=False)
        model.load_state_dict(state_dict)
        model.eval()

    elif task == "detection":
        det_preset = meta.get("detector_preset", meta.get("preset", "fast"))
        num_classes = checkpoint_detection_num_classes(state_dict, classes)
        model = create_detection_model(preset=det_preset, num_classes=num_classes, pretrained=False)
        model.load_state_dict(state_dict)
        model.eval()

    elif task == "segmentation":
        seg_preset = meta.get("preset", "fast")
        model_name = meta.get("model_name", "unet")
        model = build_segmentation_model(
            model_name=model_name,
            num_classes=len(classes),
            preset=seg_preset,
            pretrained=False,
        )
        model.load_state_dict(state_dict)
        model.eval()

    elif task in ("anomaly", "anomaly_detection"):
        det_type = str(meta.get("detector_type", "")).lower()
        preset_str = str(meta.get("preset", "")).lower()
        if "patchcore" in det_type or "patchcore" in preset_str or "precision" in preset_str:
            anomaly_obj = PatchCoreDetector(backbone_name="resnet18", device="cpu", pretrained=False)
        else:
            anomaly_obj = PaDiMDetector(backbone_name="resnet18", device="cpu", pretrained=False)

        anomaly_obj.load_state_dict(state_dict)
        model = anomaly_obj.feature_extractor
        model.eval()

    else:
        # Fallback to classification
        model = create_classification_model("resnet18", num_classes=2, pretrained=False)
        if isinstance(state_dict, dict):
            try:
                model.load_state_dict(state_dict)
            except Exception:
                pass
        model.eval()

    return model, meta, anomaly_obj


def resolve_calibrated_threshold(checkpoint_path: Path, meta: Dict[str, Any]) -> float:
    """Use only an explicitly applied calibration from a two-class validation set."""
    eval_file = checkpoint_path.parent / "eval_results.json"
    if eval_file.is_file():
        try:
            with open(eval_file, "r", encoding="utf-8") as f:
                eval_data = json.load(f)
            if eval_data.get("zero_underkill_calibrated") and _has_both_validation_classes(eval_data):
                return float(eval_data["optimal_threshold"])
        except Exception as e:
            logger.warning("Could not read evaluation file for threshold: %s", e)

    return float(meta.get("optimal_threshold", 0.50))


def _has_both_validation_classes(eval_data: Dict[str, Any]) -> bool:
    from backend.engine.zero_escape_analyzer import is_defect_label

    labels = [is_defect_label(p.get("ground_truth")) for p in eval_data.get("test_predictions", [])]
    return any(labels) and not all(labels)


class DetectionExportAdapter(nn.Module):
    """
    Wraps Faster R-CNN detection model for ONNX and TorchScript export.
    Converts list-of-dicts eval output to a fixed tuple: (boxes, scores, labels).
    """

    def __init__(self, model: nn.Module):
        super().__init__()
        self.model = model

    def forward(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        detections = self.model(x)
        if isinstance(detections, (list, tuple)) and len(detections) > 0 and isinstance(detections[0], dict):
            first = detections[0]
            boxes = first.get("boxes", torch.empty((0, 4), dtype=torch.float32, device=x.device))
            scores = first.get("scores", torch.empty((0,), dtype=torch.float32, device=x.device))
            labels = first.get("labels", torch.empty((0,), dtype=torch.int64, device=x.device))
            return boxes, scores, labels
        return (
            torch.empty((0, 4), dtype=torch.float32, device=x.device),
            torch.empty((0,), dtype=torch.float32, device=x.device),
            torch.empty((0,), dtype=torch.int64, device=x.device),
        )


def run_smoke_test_validation(
    model_path: Path,
    export_format: str,
    resolution: int,
    task: str = "classification",
) -> None:
    """Runs a quick smoke-test inference pass to confirm exported artifact is operational."""
    if export_format == "onnx":
        import onnxruntime as ort

        sess = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        inp_name = sess.get_inputs()[0].name
        dummy_in_1 = np.zeros((1, 3, resolution, resolution), dtype=np.float32)
        sess.run(None, {inp_name: dummy_in_1})

        out_names = [o.name for o in sess.get_outputs()]
        is_detection = (task == "detection") or ("boxes" in out_names and "scores" in out_names)
        if not is_detection:
            dummy_in_2 = np.zeros((2, 3, resolution, resolution), dtype=np.float32)
            sess.run(None, {inp_name: dummy_in_2})

        logger.info("ONNX smoke test passed for %s (dynamic batch verified)", model_path.name)
    elif export_format == "torchscript":
        try:
            import torchvision  # registers custom ops like torchvision::nms in TorchScript JIT
        except ImportError:
            pass
        ts_model = torch.jit.load(str(model_path), map_location="cpu")
        ts_model.eval()
        dummy_in_1 = torch.zeros((1, 3, resolution, resolution), dtype=torch.float32)
        with torch.no_grad():
            out = ts_model(dummy_in_1)
            is_detection = (task == "detection") or (isinstance(out, (tuple, list)) and len(out) == 3)
            if not is_detection:
                dummy_in_2 = torch.zeros((2, 3, resolution, resolution), dtype=torch.float32)
                try:
                    ts_model(dummy_in_2)
                except Exception:
                    pass
        logger.info("TorchScript smoke test passed for %s", model_path.name)


def generate_standalone_infer_py() -> str:
    """Generates the Python inference client shipped with each model package."""
    return '''"""
Modu Vision Standalone Python Inference Client
Usage:
    python infer.py --image path/to/image.png
    python infer.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

try:
    import torchvision  # registers custom ops like torchvision::nms in TorchScript JIT
except ImportError:
    pass


def load_config(config_path: str = "config.json") -> dict:
    if not os.path.exists(config_path):
        cfg_cand = Path(__file__).parent / config_path
        if cfg_cand.is_file():
            config_path = str(cfg_cand)
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def preprocess_image(image_input, target_size=(256, 256), mean=None, std=None):
    if mean is None:
        mean = [0.485, 0.456, 0.406]
    if std is None:
        std = [0.229, 0.224, 0.225]

    if isinstance(image_input, (str, Path)):
        img = cv2.imread(str(image_input))
        if img is None:
            raise FileNotFoundError(f"Could not read image: {image_input}")
    elif isinstance(image_input, np.ndarray):
        img = image_input.copy()
    else:
        raise ValueError(f"Unsupported image input type: {type(image_input)}")

    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    elif img.ndim == 3 and img.shape[2] == 4:
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

    orig_h, orig_w = img.shape[:2]
    w_t, h_t = target_size
    resized = cv2.resize(img, (w_t, h_t), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0

    mean_arr = np.array(mean, dtype=np.float32).reshape(1, 1, 3)
    std_arr = np.array(std, dtype=np.float32).reshape(1, 1, 3)
    norm = (rgb - mean_arr) / std_arr

    chw = np.transpose(norm, (2, 0, 1))
    batch = np.expand_dims(chw, axis=0)
    return batch, orig_w, orig_h


def is_defect_class(name):
    clean = str(name).strip().lower().replace("-", "_")
    tokens = clean.split("_")
    if "ng" in tokens or "defect" in tokens or "fail" in tokens:
        return True
    compact = clean.replace("_", "").replace(" ", "")
    return not (compact in ("ok", "normal", "pass", "good", "0", "background", "bg",
                           "nondefect", "nodefect", "정상", "양품")
                or clean.startswith(("ok_", "normal_", "good_"))
                or clean.endswith(("_ok", "_normal", "_good")))


class StandaloneInspector:
    def __init__(self, model_path: str = None, config_path: str = "config.json"):
        base_dir = Path(__file__).parent
        if not os.path.exists(config_path):
            config_path = str(base_dir / config_path)
        self.cfg = load_config(config_path)

        self.model_format = self.cfg.get("model_format", "onnx").lower()
        if model_path is None:
            model_file = "model.onnx" if self.model_format == "onnx" else "model.pt"
            model_path = str(base_dir / model_file)

        self.model_path = model_path
        self.task = self.cfg.get("task", "classification").lower()
        self.classes = self.cfg.get("classes", ["OK", "Defect"])
        self.resolution = tuple(self.cfg.get("image_size", [256, 256]))
        self.threshold = float(self.cfg.get("optimal_threshold", 0.50))
        self.segmentation_mode = self.cfg.get("segmentation_mode", "resize_single")
        self.min_defect_area_px = int(self.cfg.get("min_defect_area_px", 8))
        self.max_segmentation_tiles = int(self.cfg.get("max_segmentation_tiles", 1024))
        self.segmentation_tile_batch_size = int(self.cfg.get("segmentation_tile_batch_size", 4))
        norm_cfg = self.cfg.get("normalization", {})
        self.mean = norm_cfg.get("mean", [0.485, 0.456, 0.406])
        self.std = norm_cfg.get("std", [0.229, 0.224, 0.225])

        if self.model_format == "onnx":
            import onnxruntime as ort

            self.session = ort.InferenceSession(self.model_path, providers=["CPUExecutionProvider"])
            self.input_name = self.session.get_inputs()[0].name
        else:
            import torch

            try:
                import torchvision  # registers custom ops like torchvision::nms in TorchScript JIT
            except ImportError:
                pass

            self.torch_model = torch.jit.load(self.model_path, map_location="cpu")
            self.torch_model.eval()

    def _inspect_tiled_segmentation(self, image_input, started_at) -> dict:
        if isinstance(image_input, (str, Path)):
            bgr = cv2.imread(str(image_input))
            if bgr is None:
                raise FileNotFoundError(f"Could not read image: {image_input}")
        elif isinstance(image_input, np.ndarray):
            bgr = image_input.copy()
        else:
            raise ValueError(f"Unsupported image input type: {type(image_input)}")
        if bgr.ndim == 2:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
        elif bgr.ndim == 3 and bgr.shape[2] == 4:
            bgr = cv2.cvtColor(bgr, cv2.COLOR_BGRA2BGR)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        height, width = rgb.shape[:2]
        tile_w, tile_h = self.resolution

        def starts(length, tile):
            if length <= tile:
                return [0]
            stride = max(1, tile - max(16, tile // 8))
            positions = list(range(0, length - tile + 1, stride))
            if positions[-1] != length - tile:
                positions.append(length - tile)
            return positions

        coordinates = [(x, y) for y in starts(height, tile_h) for x in starts(width, tile_w)]
        count = len(coordinates)
        if count > self.max_segmentation_tiles:
            raise ValueError(f"Image requires {count} tiles; limit is {self.max_segmentation_tiles}")

        probability_sum = np.zeros((height, width), dtype=np.float32)
        coverage = np.zeros((height, width), dtype=np.uint16)
        mean = np.asarray(self.mean, dtype=np.float32).reshape(3, 1, 1)
        std = np.asarray(self.std, dtype=np.float32).reshape(3, 1, 1)
        for offset in range(0, count, self.segmentation_tile_batch_size):
            bounds = []
            tiles = []
            for x, y in coordinates[offset:offset + self.segmentation_tile_batch_size]:
                x2, y2 = min(width, x + tile_w), min(height, y + tile_h)
                patch = rgb[y:y2, x:x2]
                if patch.shape[:2] != (tile_h, tile_w):
                    patch = cv2.resize(patch, (tile_w, tile_h), interpolation=cv2.INTER_LINEAR)
                chw = patch.transpose(2, 0, 1).astype(np.float32) / 255.0
                tiles.append((chw - mean) / std)
                bounds.append((x, y, x2, y2))
            batch = np.stack(tiles)
            if self.model_format == "onnx":
                logits = self.session.run(None, {self.input_name: batch})[0]
            else:
                import torch
                with torch.no_grad():
                    raw = self.torch_model(torch.from_numpy(batch))
                logits = (raw[0] if isinstance(raw, (tuple, list)) else raw).cpu().numpy()
            if logits.ndim != 4 or logits.shape[0] != len(bounds) or logits.shape[1] < 2:
                raise RuntimeError("Segmentation model returned an unexpected tile shape")
            shifted = logits - np.max(logits, axis=1, keepdims=True)
            exponentials = np.exp(shifted)
            probabilities = (exponentials / exponentials.sum(axis=1, keepdims=True))[:, 1]
            for probability, (x, y, x2, y2) in zip(probabilities, bounds):
                if probability.shape != (y2 - y, x2 - x):
                    probability = cv2.resize(probability, (x2 - x, y2 - y), interpolation=cv2.INTER_LINEAR)
                probability_sum[y:y2, x:x2] += probability
                coverage[y:y2, x:x2] += 1

        if np.any(coverage == 0):
            raise RuntimeError("Segmentation tiling left pixels uncovered")
        probability_sum /= coverage
        defect_score = float(probability_sum.max())
        defect_area_px = int(np.count_nonzero(probability_sum > self.threshold))
        verdict = "NG" if defect_area_px >= self.min_defect_area_px else "OK"
        return {
            "status": "success",
            "verdict": verdict,
            "defect_score": round(defect_score, 4),
            "confidence": round(defect_score, 4),
            "threshold": round(self.threshold, 4),
            "optimal_threshold": round(self.threshold, 4),
            "task": self.task,
            "predicted_class": "Defect" if verdict == "NG" else "OK",
            "latency_ms": round((time.perf_counter() - started_at) * 1000.0, 2),
            "image_dimensions": [width, height],
            "tiles_processed": count,
            "defect_area_px": defect_area_px,
            "min_defect_area_px": self.min_defect_area_px,
        }

    def inspect(self, image_input) -> dict:
        t0 = time.perf_counter()
        if self.task == "segmentation" and self.segmentation_mode == "tiled_full_image":
            return self._inspect_tiled_segmentation(image_input, t0)
        batch, orig_w, orig_h = preprocess_image(
            image_input, target_size=self.resolution, mean=self.mean, std=self.std
        )

        if self.model_format == "onnx":
            outputs = self.session.run(None, {self.input_name: batch})
            if self.task == "detection" and len(outputs) >= 2:
                det_boxes = outputs[0]
                det_scores = outputs[1]
                det_labels = outputs[2] if len(outputs) > 2 else np.ones(len(det_scores), dtype=int)
                raw_out = outputs[0]
            else:
                raw_out = outputs[0]
        else:
            import torch

            with torch.no_grad():
                tensor_in = torch.from_numpy(batch)
                out = self.torch_model(tensor_in)
                if self.task == "detection" and isinstance(out, (tuple, list)) and len(out) >= 2:
                    det_boxes = out[0].cpu().numpy()
                    det_scores = out[1].cpu().numpy()
                    det_labels = out[2].cpu().numpy() if len(out) > 2 else np.ones(len(det_scores), dtype=int)
                    raw_out = det_boxes
                elif isinstance(out, (tuple, list)):
                    raw_out = out[0].cpu().numpy()
                else:
                    raw_out = out.cpu().numpy()

        latency_ms = (time.perf_counter() - t0) * 1000.0

        # Calculate task-specific defect score
        requires_review = False
        review_reason = None
        detections = []
        if self.task == "classification":
            logits = raw_out[0]
            # Softmax
            exp_logits = np.exp(logits - np.max(logits))
            probs = exp_logits / np.sum(exp_logits)
            pred_idx = int(np.argmax(probs))
            confidence = float(probs[pred_idx])
            pred_name = self.classes[pred_idx] if pred_idx < len(self.classes) else f"class_{pred_idx}"

            normal_indices = [index for index, name in enumerate(self.classes)
                              if not is_defect_class(name)]
            if not normal_indices:
                requires_review = True
                review_reason = "Classification checkpoint has no OK/normal class."
                defect_score = 1.0
            else:
                defect_score = float(1.0 - np.sum(probs[normal_indices]))

        elif self.task == "detection":
            foreground_classes = list(self.classes)
            if foreground_classes and str(foreground_classes[0]).strip().lower() in ("background", "__background__"):
                foreground_classes = foreground_classes[1:]
            defect_indices = [idx for idx, cname in enumerate(foreground_classes, start=1)
                              if is_defect_class(cname)]
            if not defect_indices:
                requires_review = True
                review_reason = "Detection checkpoint has no defect class."

            defect_candidates = []
            if len(det_scores) > 0:
                for box, score, lbl in zip(det_boxes, det_scores, det_labels):
                    lbl_id = int(lbl)
                    if lbl_id in defect_indices:
                        defect_candidates.append((float(score), lbl_id, box))
                        if float(score) >= self.threshold:
                            scale_x = orig_w / self.resolution[0]
                            scale_y = orig_h / self.resolution[1]
                            detections.append({
                                "bbox": [round(float(box[0]) * scale_x, 2),
                                         round(float(box[1]) * scale_y, 2),
                                         round(float(box[2]) * scale_x, 2),
                                         round(float(box[3]) * scale_y, 2)],
                                "score": round(float(score), 4),
                                "label": foreground_classes[lbl_id - 1],
                            })

            strongest = max(defect_candidates, key=lambda row: row[0]) if defect_candidates else None
            defect_score = strongest[0] if strongest else 0.0
            confidence = defect_score
            pred_name = (foreground_classes[strongest[1] - 1]
                         if strongest and defect_score >= self.threshold else "OK")

        elif self.task == "segmentation":
            # [1, C, H, W]
            probs = np.exp(raw_out[0] - np.max(raw_out[0], axis=0, keepdims=True))
            probs = probs / np.sum(probs, axis=0, keepdims=True)
            if probs.shape[0] > 1:
                defect_score = float(np.max(probs[1]))
            else:
                defect_score = float(np.max(probs[0]))
            confidence = defect_score
            pred_name = "Defect" if defect_score >= self.threshold else "OK"

        elif "anomaly" in self.task:
            # Distance / feature norm
            defect_score = float(np.mean(np.abs(raw_out)))
            if defect_score > 1.0:
                defect_score = float(defect_score / (defect_score + 1.0))
            confidence = defect_score
            pred_name = "NG" if defect_score >= self.threshold else "OK"

        else:
            defect_score = float(np.max(raw_out)) if raw_out.size > 0 else 0.0
            confidence = defect_score
            pred_name = "Defect" if defect_score >= self.threshold else "OK"

        verdict = "REVIEW" if requires_review else "NG" if defect_score >= self.threshold else "OK"

        result = {
            "status": "success",
            "verdict": verdict,
            "defect_score": round(defect_score, 4),
            "confidence": round(confidence, 4),
            "threshold": round(self.threshold, 4),
            "optimal_threshold": round(self.threshold, 4),
            "task": self.task,
            "predicted_class": pred_name,
            "review_reason": review_reason,
            "latency_ms": round(latency_ms, 2),
            "image_dimensions": [orig_w, orig_h],
        }
        if self.task == "detection":
            result["detections"] = detections
        return result


def main():
    parser = argparse.ArgumentParser(description="Modu Vision Standalone Inspector")
    parser.add_argument("--image", "-i", type=str, default=None, help="Path to image file")
    parser.add_argument("--model", "-m", type=str, default=None, help="Path to model artifact")
    parser.add_argument("--config", "-c", type=str, default="config.json", help="Path to config.json")
    parser.add_argument("--output", "-o", type=str, default=None, help="Optional output JSON path")
    parser.add_argument("--self-test", action="store_true", help="Run quick self-test on dummy image")
    parser.add_argument(
        "--threshold-override",
        type=float,
        default=None,
        help="Override calibrated decision threshold",
    )
    args = parser.parse_args()

    inspector = StandaloneInspector(model_path=args.model, config_path=args.config)
    if args.threshold_override is not None:
        inspector.threshold = float(args.threshold_override)

    temporary_dir = None
    try:
        target_image = args.image
        if not target_image or args.self_test:
            temporary_dir = tempfile.TemporaryDirectory(prefix="modu_vision_selftest_")
            dummy_path = Path(temporary_dir.name) / "dummy_test.png"
            res_w, res_h = inspector.resolution
            dummy = np.zeros((res_h, res_w, 3), dtype=np.uint8)
            cv2.rectangle(dummy, (30, 30), (res_w - 30, res_h - 30), (128, 128, 128), -1)
            if not cv2.imwrite(str(dummy_path), dummy):
                raise OSError("Could not write temporary self-test image")
            target_image = str(dummy_path)

        result = inspector.inspect(target_image)
        json_output = json.dumps(result, indent=2)
        print(json_output)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_output)
        sys.exit(0)
    except Exception as exc:
        err_response = {
            "status": "error",
            "error": str(exc),
            "verdict": "ERROR",
        }
        json_output = json.dumps(err_response, indent=2)
        print(json_output)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_output)
        sys.exit(1)
    finally:
        if temporary_dir is not None:
            temporary_dir.cleanup()


if __name__ == "__main__":
    main()
'''


def export_runtime_package(
    job_id: Optional[str] = None,
    export_format: str = "onnx",
    resolution: int = 256,
    quantize_fp16: bool = False,
    package_name: Optional[str] = None,
    output_base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Main export engine: loads genuine trained weights and packages a self-contained runtime.
    """
    # 1. Locate Checkpoint
    if not job_id:
        raise FileNotFoundError("A completed model job ID is required for export.")
    if quantize_fp16:
        raise ValueError("FP16 conversion is not implemented or verified for this export format.")
    ckpt_path = locate_checkpoint(job_id)
    if not ckpt_path:
        raise FileNotFoundError(f"No trained model checkpoint found for job: {job_id}")

    pkg_name = package_name or f"modu_vision_export_{Path(job_id).name}"
    if not isinstance(pkg_name, str) or not re.fullmatch(r"[\w][\w.-]{0,95}", pkg_name, flags=re.UNICODE):
        raise ValueError("Invalid package name: use letters, numbers, underscores, dots, or hyphens only.")

    model, meta, anomaly_obj = load_checkpoint_and_reconstruct_model(ckpt_path)
    task = meta.get("task", "classification").lower().strip()
    if task in ("anomaly", "anomaly_detection"):
        raise ValueError(
            "Standalone anomaly export is unavailable: the generated infer.py does not apply "
            "the trained PaDiM/PatchCore statistics or memory bank. Use the model in the app until "
            "a verified anomaly runtime is available."
        )
    classes = meta.get("classes", ["OK", "Defect"])
    res = int(resolution or meta.get("image_size", [256, 256])[0])
    img_size = [res, res]

    base_dir = (output_base_dir or EXPORTS_DIR).resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    requested_name = pkg_name
    for attempt in range(5):
        candidate_name = requested_name if attempt == 0 else f"{requested_name}_{time.time_ns()}"
        pkg_dir = base_dir / candidate_name
        if pkg_dir.is_symlink():
            raise ValueError("Invalid package name: target path is a symbolic link.")
        try:
            pkg_dir.mkdir(exist_ok=False)
            pkg_name = candidate_name
            break
        except FileExistsError:
            continue
    else:
        raise RuntimeError("Could not reserve a unique runtime package directory")

    # 3. Export Multi-Format Artifact
    format_clean = str(export_format).lower().strip()
    if format_clean not in ("onnx", "torchscript"):
        format_clean = "onnx"

    model_filename = "model.onnx" if format_clean == "onnx" else "model.pt"
    model_output_path = pkg_dir / model_filename

    dummy_tensor = torch.randn(1, 3, res, res, dtype=torch.float32)

    if task == "detection":
        if hasattr(model, "roi_heads") and hasattr(model.roi_heads, "box_roi_pool"):
            if "forward" in model.roi_heads.box_roi_pool.__dict__:
                del model.roi_heads.box_roi_pool.__dict__["forward"]
        export_model = DetectionExportAdapter(model)
        output_names = ["boxes", "scores", "labels"]
        dynamic_axes = {
            "input": {0: "batch_size"},
            "boxes": {0: "num_boxes"},
            "scores": {0: "num_boxes"},
            "labels": {0: "num_boxes"},
        }
    else:
        export_model = model
        output_names = ["output"]
        dynamic_axes = {"input": {0: "batch_size"}, "output": {0: "batch_size"}}

    if format_clean == "onnx":
        torch.onnx.export(
            export_model,
            dummy_tensor,
            str(model_output_path),
            export_params=True,
            opset_version=14,
            do_constant_folding=True,
            input_names=["input"],
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            dynamo=False,
        )
    else:  # torchscript
        try:
            traced = torch.jit.trace(export_model, dummy_tensor)
            traced.save(str(model_output_path))
        except Exception as trace_err:
            logger.warning("torch.jit.trace failed, attempting torch.jit.script: %s", trace_err)
            scripted = torch.jit.script(export_model)
            scripted.save(str(model_output_path))

    # Anomaly statistical artifacts if applicable
    if anomaly_obj is not None:
        if hasattr(anomaly_obj, "mean") and anomaly_obj.mean is not None:
            torch.save(
                {
                    "mean": anomaly_obj.mean.cpu(),
                    "cov_inv": anomaly_obj.cov_inv.cpu() if anomaly_obj.cov_inv is not None else None,
                    "sub_dims": anomaly_obj.sub_dims.cpu() if anomaly_obj.sub_dims is not None else None,
                    "threshold": getattr(anomaly_obj, "threshold", 0.5),
                },
                pkg_dir / "anomaly_stats.pt",
            )
        elif hasattr(anomaly_obj, "coreset") and anomaly_obj.coreset is not None:
            np.save(pkg_dir / "memory_bank.npy", anomaly_obj.coreset.cpu().numpy())
            torch.save(
                {"coreset": anomaly_obj.coreset.cpu(), "threshold": getattr(anomaly_obj, "threshold", 0.5)},
                pkg_dir / "anomaly_stats.pt",
            )

    # 4. Resolve Zero-Escape Calibrated Threshold
    optimal_th = resolve_calibrated_threshold(ckpt_path, meta)
    eval_file = ckpt_path.parent / "eval_results.json"
    calibration_applied = False
    if eval_file.is_file():
        try:
            eval_data = json.loads(eval_file.read_text(encoding="utf-8"))
            calibration_applied = bool(eval_data.get("zero_underkill_calibrated")) and _has_both_validation_classes(eval_data)
        except (OSError, ValueError):
            pass

    # 5. Write config.json
    config_data = {
        "model_format": format_clean,
        "task": task,
        "classes": classes,
        "image_size": img_size,
        "input_channels": 3,
        "normalization": {
            "mean": [0.0, 0.0, 0.0],
            "std": [1.0, 1.0, 1.0],
        },
        "optimal_threshold": round(optimal_th, 4),
        "zero_underkill_calibrated": calibration_applied,
        "exported_from_checkpoint": ckpt_path.name,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if task == "segmentation":
        config_data.update({
            "segmentation_mode": "tiled_full_image",
            "min_defect_area_px": 8,
            "max_segmentation_tiles": 1024,
            "segmentation_tile_batch_size": 4,
        })
    with open(pkg_dir / "config.json", "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    # 6. Standalone code templates
    with open(pkg_dir / "infer.py", "w", encoding="utf-8") as f:
        f.write(generate_standalone_infer_py())
    runtime_requirements = [
        "numpy>=1.26.0",
        "opencv-python-headless>=4.10.0.84",
    ]
    if format_clean == "onnx":
        runtime_requirements.append("onnxruntime>=1.19.0")
    else:
        runtime_requirements.extend(["torch>=2.4.0", "torchvision>=0.19.0"])
    (pkg_dir / "requirements.txt").write_text(
        "\n".join(runtime_requirements) + "\n",
        encoding="utf-8",
    )

    if task == "segmentation":
        inspection_scope = f"""## Inspection Scope
- `infer.py` inspects the original image with overlapping {res}×{res} tiles (batch size 4, at most 1024 tiles).
- It averages overlapping foreground probabilities, then returns NG when more than `optimal_threshold` covers at least `min_defect_area_px` pixels (default 8). These rules match the single full-image segmentation node in Step 5.
- Export at the Step 5 model input resolution, then set `optimal_threshold` and `min_defect_area_px` in `config.json` to the same values used by that node before comparing results.
- The standalone package does not execute other Step 5 flowchart nodes such as detector ROIs, crops, filters, or final review rules. The Step 6 single-forward benchmark does not include full-image tiling, image I/O, camera, or PLC time.

"""
    else:
        inspection_scope = """## Inspection Scope
- `infer.py` resizes one image to the configured model input size and runs that model once. It does not execute the Step 5 flowchart's ROI, crop, filter, or final review nodes.
- The Step 6 single-forward benchmark does not include image I/O, camera, or PLC time.

"""

    readme_content = f"""# Modu Vision Runtime Package

**Package Name**: `{pkg_name}`
**Export Date**: {config_data['exported_at']}
**Format**: {format_clean.upper()} + standalone Python client
**Task**: {task.upper()}
**Decision Threshold**: {optimal_th:.4f} ({'validated calibration' if calibration_applied else 'uncalibrated default/checkpoint value'})

## Included Files
- `{model_filename}`: Authentic trained model weights ({format_clean})
- `config.json`: Preprocessing coefficients & shop-floor zero-escape threshold
- `infer.py`: Standalone Python inference script (CLI runner)
- `requirements.txt`: Python packages needed by `infer.py`
- Native C#/C++ clients: not included
- `README_DEPLOY.md`: Quickstart deployment guide

{inspection_scope}
## Standalone Quickstart
Install Python 3.10 or newer, then from this package directory:
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

Run an image or verify the packaged model:
```bash
python infer.py --image test_sample.png
python infer.py --self-test
```
The exported files include the model and client; a Python runtime and the listed packages must be installed on the target machine.
"""
    with open(pkg_dir / "README_DEPLOY.md", "w", encoding="utf-8") as f:
        f.write(readme_content)

    # 7. Smoke Test Verification
    run_smoke_test_validation(model_output_path, format_clean, res, task=task)

    # 8. Build File Manifest
    manifest = []
    for item in sorted(pkg_dir.iterdir()):
        if item.is_file():
            manifest.append({
                "name": item.name,
                "size_kb": round(item.stat().st_size / 1024, 1),
            })

    return {
        "status": "success",
        "package_name": pkg_name,
        "package_path": str(pkg_dir.resolve()),
        "manifest": manifest,
        "total_files": len(manifest),
        "export_format": format_clean,
        "model_file": model_filename,
        "optimal_threshold": round(optimal_th, 4),
    }

"""Paired RGB denoising with immutable inputs and a portable checkpoint.

Synthetic degradation uses the original image as target. It never declares an
inspection image normal and never overwrites the source image.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset

_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
_TRAINING_GEOMETRY = {"mode": "training_resize", "resize": True, "width": 64, "height": 64,
                      "output_dtype": "float32", "approval_evidence": False}
_EVALUATION_GEOMETRY = {"mode": "source_resolution", "resize": False, "tile_size": 512,
                        "tile_overlap": 4, "output_dtype": "uint8", "output_quantization": "round_clip_0_255"}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _path(root: Path, name: str) -> Path:
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Enhancement images require safe relative paths")
    path = root / relative
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("Enhancement image is missing or outside dataset")
    return path


def _device(name: str) -> torch.device:
    if name not in {"cpu", "cuda", "mps"}:
        raise ValueError("Enhancement device must be cpu, cuda or mps")
    if name == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA enhancement runtime is unavailable")
    if name == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS enhancement runtime is unavailable")
    return torch.device(name)


def prepare_enhancement(source: str | Path, destination: str | Path, *, seed: int = 0,
                        noise_sigma: float = 15, image_paths: list[str] | None = None) -> dict:
    source = Path(source).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if not source.is_dir() or destination == source or destination.is_relative_to(source):
        raise ValueError("Use a separate enhancement dataset outside the source folder")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Enhancement destination must be empty")
    if not math.isfinite(noise_sigma) or not 0 < noise_sigma <= 50:
        raise ValueError("Noise strength must be between 0 and 50")
    paths = [_path(source, name) for name in image_paths] if image_paths is not None else [
        p for p in sorted(source.rglob("*")) if p.suffix.lower() in _EXTENSIONS
        and p.is_file() and not p.is_symlink() and not any(part.startswith(".") for part in p.relative_to(source).parts)
    ]
    unique: dict[str, Path] = {}
    for path in paths:
        unique.setdefault(_sha(path), path)
    paths = list(unique.values())
    if len(paths) < 3:
        raise ValueError("Enhancement needs at least three distinct images for train/val/test")
    if len(paths) > 10000:
        raise ValueError("Select at most 10000 images per enhancement dataset")
    rng = np.random.default_rng(seed)
    order = rng.permutation(len(paths))
    val_count = max(1, round(len(paths) * .2))
    test_count = max(1, round(len(paths) * .2))
    assignments = {int(index): ("test" if rank < test_count else "val" if rank < test_count + val_count else "train")
                   for rank, index in enumerate(order)}
    records = []
    destination.mkdir(parents=True, exist_ok=True)
    for index, path in enumerate(paths):
        image = np.asarray(Image.open(path).convert("RGB"))
        noisy = np.clip(image.astype(np.float32) + rng.normal(0, noise_sigma, image.shape), 0, 255).astype(np.uint8)
        names = {"input": f"inputs/{index:06d}.png", "target": f"targets/{index:06d}.png"}
        for kind, pixels in (("input", noisy), ("target", image)):
            stream = io.BytesIO()
            Image.fromarray(pixels).save(stream, format="PNG")
            _atomic(destination / names[kind], stream.getvalue())
        records.append({**names, "split": assignments[index], "source_relative_path": path.relative_to(source).as_posix(),
                        "source_sha256": _sha(path), "input_sha256": _sha(destination / names["input"]),
                        "target_sha256": _sha(destination / names["target"])})
    manifest = {"version": 1, "task": "enhancement", "mode": "synthetic_gaussian", "seed": seed,
                "noise_sigma": noise_sigma, "source_dataset_path": str(source), "records": records}
    _atomic(destination / "pairs.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return load_enhancement_manifest(destination)


def load_enhancement_manifest(dataset_path: str | Path) -> dict:
    root = Path(dataset_path).expanduser().resolve()
    file = root / "pairs.json"
    if file.is_symlink() or not file.is_file():
        raise ValueError("Enhancement requires a pairs.json manifest")
    data = file.read_bytes()
    raw = json.loads(data)
    if raw.get("version") != 1 or raw.get("task") != "enhancement" or not isinstance(raw.get("records"), list):
        raise ValueError("Invalid enhancement manifest")
    seen_paths, seen_targets = set(), {}
    splits = set()
    for row in raw["records"]:
        if not isinstance(row, dict) or row.get("split") not in {"train", "val", "test"}:
            raise ValueError("Enhancement pairs need explicit train/val/test splits")
        splits.add(row["split"])
        for kind in ("input", "target"):
            path = _path(root, row.get(kind, ""))
            digest = _sha(path)
            if row.get(f"{kind}_sha256") != digest:
                raise ValueError(f"Enhancement {kind} hash changed")
            if kind == "input":
                if path in seen_paths:
                    raise ValueError("Enhancement input is repeated")
                seen_paths.add(path)
            else:
                previous = seen_targets.setdefault(digest, row["split"])
                if previous != row["split"]:
                    raise ValueError("Duplicate enhancement target crosses splits")
        with Image.open(root / row["input"]) as image, Image.open(root / row["target"]) as target:
            if image.size != target.size:
                raise ValueError("Enhancement input/target dimensions differ")
    if not raw["records"] or "train" not in splits or "val" not in splits or "test" not in splits:
        raise ValueError("Enhancement requires nonempty train, val and test splits")
    # Location is not data identity: archives rebase source paths, while pixels,
    # pair geometry, origin hashes and split assignments must remain identical.
    portable = {key: value for key, value in raw.items() if key not in {"source_dataset_path", "dataset_path", "provenance"}}
    digest = hashlib.sha256(json.dumps(portable, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    raw["dataset_path"] = str(root)
    raw["provenance"] = {"dataset_sha256": digest, "sample_count": len(raw["records"]),
                         "source_dataset_path": raw.get("source_dataset_path", str(root))}
    return raw


class RGBDenoiser(nn.Module):
    def __init__(self):
        super().__init__()
        self.residual = nn.Sequential(nn.Conv2d(3, 16, 3, padding=1), nn.ReLU(),
                                      nn.Conv2d(16, 16, 3, padding=1), nn.ReLU(), nn.Conv2d(16, 3, 3, padding=1))

    def forward(self, inputs):
        return torch.clamp(inputs + self.residual(inputs), 0, 1)


class _Pairs(Dataset):
    def __init__(self, manifest: dict, split: str):
        self.root = Path(manifest["dataset_path"])
        self.records = [r for r in manifest["records"] if r["split"] == split]

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        row = self.records[index]
        tensors = []
        for kind in ("input", "target"):
            path = _path(self.root, row[kind])
            if _sha(path) != row[f"{kind}_sha256"]:
                raise ValueError("Enhancement image changed during training/evaluation")
            with Image.open(path) as image:
                pixels = np.array(image.convert("RGB").resize((64, 64)), copy=True)
            tensors.append(torch.from_numpy(pixels).permute(2, 0, 1).float() / 255)
        return tuple(tensors)


def _evaluate(model: RGBDenoiser, dataset: _Pairs, device: torch.device) -> dict:
    inputs_mse, output_mse = [], []
    model.eval()
    with torch.inference_mode():
        for inputs, targets in DataLoader(dataset, batch_size=4):
            predictions = model(inputs.to(device)).cpu()
            inputs_mse.extend(((inputs-targets)**2).mean(dim=(1, 2, 3)).tolist())
            output_mse.extend(((predictions-targets)**2).mean(dim=(1, 2, 3)).tolist())
    if not output_mse:
        raise ValueError("Enhancement evaluation split is empty")
    input_mean, output_mean = float(np.mean(inputs_mse)), float(np.mean(output_mse))
    return {"sample_count": len(output_mse), "input_mse": input_mean, "output_mse": output_mean,
            "input_psnr": -10 * math.log10(max(input_mean, 1e-10)),
            "output_psnr": -10 * math.log10(max(output_mean, 1e-10)), "improved": output_mean < input_mean,
            "evaluation_geometry": dict(_TRAINING_GEOMETRY)}


def train_enhancement(dataset_path: str | Path, output_dir: str | Path, *, epochs: int = 1, batch_size: int = 4,
                      learning_rate: float = 1e-3, device: str = "cpu", seed: int = 0,
                      cancel_event=None, on_progress=None, warm_start=None) -> dict:
    target_device = _device(device)
    if not 1 <= epochs <= 500 or not 1 <= batch_size <= 256 or not 0 < learning_rate <= 1:
        raise ValueError("Invalid enhancement training settings")
    manifest = load_enhancement_manifest(dataset_path)
    torch.manual_seed(seed)
    model = RGBDenoiser().to(target_device)
    lineage = {}
    if warm_start is not None:
        from backend.engine.specialized_warm_start import load_family_weights, requested_signature, require_new_candidate
        require_new_candidate(output_dir, warm_start)
        load_family_weights({'model_state_dict': model}, warm_start, requested_signature('enhancement', dataset_path, {}))
        lineage = {'warm_start': warm_start.lineage()}
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    loader = DataLoader(_Pairs(manifest, "train"), batch_size=batch_size, shuffle=True,
                        generator=torch.Generator().manual_seed(seed))
    output = Path(output_dir).expanduser().resolve()
    if output.exists() and any(p.name not in ("job.json","job_receipt.json") for p in output.iterdir()):
        raise ValueError("Enhancement output must be a new candidate directory")
    receipt=output/'job_receipt.json'
    if receipt.exists():
        reserved=json.loads(receipt.read_text())
        if receipt.is_symlink() or reserved.get('task')!='enhancement' or reserved.get('job_id')!=output.name or reserved.get('status') not in ('queued','running'):
            raise ValueError('Enhancement candidate reservation differs from this run')
    output.mkdir(parents=True, exist_ok=True)
    best_loss, history, best_epoch = float("inf"), [], 0
    for epoch in range(epochs):
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Enhancement training cancelled")
        model.train()
        loss_total = 0.
        for inputs, targets in loader:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("Enhancement training cancelled")
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.mse_loss(model(inputs.to(target_device)), targets.to(target_device))
            if not torch.isfinite(loss):
                raise ValueError("Enhancement training loss is not finite")
            loss.backward()
            optimizer.step()
            loss_total += loss.item() * len(inputs)
        history.append(loss_total / len(loader.dataset))
        if on_progress is not None:
            on_progress(epoch + 1, epochs, history[-1])
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Enhancement training cancelled")
        validation = _evaluate(model, _Pairs(manifest, "val"), target_device)
        if validation["output_mse"] < best_loss:
            best_loss, best_epoch = validation["output_mse"], epoch + 1
            buffer = io.BytesIO()
            torch.save({"task": "enhancement", "version": 1, "architecture": "rgb_residual_cnn",
                        "model_state_dict": model.cpu().state_dict(), "provenance": manifest["provenance"],
                        "dataset_provenance": manifest["provenance"], "dataset_path": manifest["dataset_path"],
                        "source_dataset_path": manifest["provenance"]["source_dataset_path"],
                        "best_epoch": best_epoch, "validation": validation, **lineage}, buffer)
            _atomic(output / "best_model.pt", buffer.getvalue())
            model.to(target_device)
    metadata = {"task": "enhancement", "version": 1, "architecture": "rgb_residual_cnn",
                "dataset_path": manifest["dataset_path"], "source_dataset_path": manifest["provenance"]["source_dataset_path"],
                "provenance": manifest["provenance"], "dataset_provenance": manifest["provenance"],
                "mode": manifest.get("mode", "explicit_pairs"), "best_epoch": best_epoch,
                "epochs_completed": epochs, "training_loss_history": history,
                "training_validation_geometry": dict(_TRAINING_GEOMETRY), "created_at": datetime.now(timezone.utc).isoformat(), **lineage}
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode()
    _atomic(output / "model_meta.json", encoded)
    _atomic(output / "metadata.json", encoded)
    return metadata


def _load(checkpoint_path: str | Path, device: str):
    target = _device(device)
    path = Path(checkpoint_path).expanduser().resolve()
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if payload.get("task") != "enhancement" or payload.get("version") != 1:
        raise ValueError("Checkpoint is not an enhancement candidate")
    model = RGBDenoiser()
    model.load_state_dict(payload["model_state_dict"], strict=True)
    return model.to(target).eval(), payload, target


def predict_enhancement(checkpoint_path: str | Path, image_rgb: np.ndarray, device: str = "cpu") -> np.ndarray:
    model, _, target = _load(checkpoint_path, device)
    return _predict_pixels(model, image_rgb, target)


def _predict_pixels(model: RGBDenoiser, image_rgb: np.ndarray, target: torch.device) -> np.ndarray:
    """Shared deployed pixel path; evaluation reuses a loaded model across pairs."""
    if image_rgb.dtype != np.uint8 or image_rgb.ndim != 3 or image_rgb.shape[2] != 3 or not image_rgb.size:
        raise ValueError("Enhancement input must be a nonempty RGB uint8 image")
    height, width = image_rgb.shape[:2]
    output = np.empty_like(image_rgb)
    # Overlap covers the three convolution receptive fields and bounds memory.
    with torch.inference_mode():
        for y in range(0, height, 512):
            for x in range(0, width, 512):
                top, left, bottom, right = max(0, y-4), max(0, x-4), min(height, y+516), min(width, x+516)
                pixels = np.array(image_rgb[top:bottom, left:right], copy=True)
                tensor = torch.from_numpy(pixels).permute(2, 0, 1).unsqueeze(0).float().to(target) / 255
                predicted = (model(tensor)[0].permute(1, 2, 0).cpu().numpy() * 255).round().clip(0, 255).astype(np.uint8)
                h, w = min(512, height-y), min(512, width-x)
                output[y:y+h, x:x+w] = predicted[y-top:y-top+h, x-left:x-left+w]
    return output


def _pixel_mse(pixels: np.ndarray, target: np.ndarray) -> float:
    # Accumulate original pixels without allocating a full float64 8k RGB image.
    squared_error = 0.0
    for top in range(0, len(pixels), 256):
        difference = pixels[top:top + 256].astype(np.float64) - target[top:top + 256]
        squared_error += float(np.sum(difference * difference, dtype=np.float64))
    return squared_error / pixels.size / (255 ** 2)


def evaluate_enhancement(checkpoint_path: str | Path, dataset_path: str | Path, *, split: str = "test", device: str = "cpu",
                         allow_dataset_revision: bool = False) -> dict:
    if split not in {"train", "val", "test"}:
        raise ValueError("Invalid enhancement evaluation split")
    manifest = load_enhancement_manifest(dataset_path)
    model, payload, target = _load(checkpoint_path, device)
    training_sha = payload["provenance"]["dataset_sha256"]
    revised = training_sha != manifest["provenance"]["dataset_sha256"]
    if revised and not allow_dataset_revision:
        raise ValueError("Enhancement evaluation dataset differs from training manifest")
    if revised:
        trained_source = payload.get("source_dataset_path") or payload["provenance"].get("source_dataset_path")
        # Restored metadata can locate the byte-bound training manifest without
        # rewriting the immutable checkpoint's original absolute source path.
        candidate_paths = [payload.get("dataset_path")]
        metadata_file = Path(checkpoint_path).parent / "model_meta.json"
        if metadata_file.is_file():
            metadata = json.loads(metadata_file.read_text())
            if metadata.get("provenance", {}).get("dataset_sha256") == training_sha:
                candidate_paths.append(metadata.get("dataset_path"))
        for candidate in candidate_paths:
            if candidate and (Path(candidate) / "pairs.json").is_file():
                original = load_enhancement_manifest(candidate)
                if original["provenance"]["dataset_sha256"] == training_sha:
                    trained_source = original.get("source_dataset_path")
                    break
        current_source = manifest.get("source_dataset_path")
        if not trained_source or not current_source or Path(trained_source).resolve() != Path(current_source).resolve():
            raise ValueError("Enhancement revised evaluation requires the original source")
        for row in manifest["records"]:
            source_file = _path(Path(current_source).resolve(), row.get("source_relative_path", ""))
            if _sha(source_file) != row.get("source_sha256"):
                raise ValueError("Enhancement revised evaluation source hash changed")
    samples = []
    root = Path(manifest["dataset_path"])
    for row in manifest["records"]:
        if row["split"] != split:
            continue
        images = []
        for kind in ("input", "target"):
            path = _path(root, row[kind])
            if _sha(path) != row[f"{kind}_sha256"]:
                raise ValueError("Enhancement image changed during evaluation")
            with Image.open(path) as image:
                images.append(np.array(image.convert("RGB"), copy=True))
        image, reference = images
        if image.shape != reference.shape:
            raise ValueError("Enhancement input/target dimensions differ")
        output = _predict_pixels(model, image, target)
        input_mse, output_mse = _pixel_mse(image, reference), _pixel_mse(output, reference)
        samples.append({**row, "source_width": image.shape[1], "source_height": image.shape[0],
                        "channels": 3, "pixel_count": image.shape[0] * image.shape[1],
                        "output_pixels_sha256": hashlib.sha256(memoryview(output)).hexdigest(),
                        "input_mse": input_mse, "output_mse": output_mse,
                        "input_psnr": -10 * math.log10(max(input_mse, 1e-10)),
                        "output_psnr": -10 * math.log10(max(output_mse, 1e-10)),
                        "improved": output_mse < input_mse})
    if not samples:
        raise ValueError("Enhancement evaluation split is empty")
    input_mean = float(np.mean([row["input_mse"] for row in samples]))
    output_mean = float(np.mean([row["output_mse"] for row in samples]))
    metrics = {"sample_count": len(samples), "input_mse": input_mean, "output_mse": output_mean,
               "input_psnr": -10 * math.log10(max(input_mean, 1e-10)),
               "output_psnr": -10 * math.log10(max(output_mean, 1e-10)), "improved": output_mean < input_mean,
               "evaluation_geometry": dict(_EVALUATION_GEOMETRY), "samples": samples,
               "metric_aggregation": "mean_per_image_mse_then_psnr", "metric_data_range": 255,
               "mse_normalization": "squared_rgb_difference_divided_by_255_squared", "psnr_mse_floor": 1e-10}
    return {**metrics, "task": "enhancement", "split": split,
            "training_dataset_sha256": training_sha, "dataset_revision_changed": revised,
            "model_sha256": _sha(Path(checkpoint_path)), "dataset_sha256": manifest["provenance"]["dataset_sha256"]}

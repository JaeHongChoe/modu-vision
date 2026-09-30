"""Small, trainable line OCR with explicit labels and source provenance.

The dataset root must contain ``ocr.json`` with this schema::

    {"version": 1, "samples": [
        {"image": "images/serial.png", "text": "AB12", "split": "train",
         "source_sha256": "<64 lowercase hexadecimal digits>"}
    ]}

``write_ocr_manifest`` creates that file from *provided* image/text/split rows;
it never infers text from a filename or pixels. Images are single-line text crops.
The model is a compact CNN + bidirectional GRU trained with CTC from scratch.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset


@dataclass(frozen=True)
class OCRRecord:
    image: str
    image_path: Path
    text: str
    split: str
    source_sha256: str


@dataclass(frozen=True)
class OCRManifest:
    root: Path
    samples: list[OCRRecord]
    alphabet: str
    provenance: dict[str, Any]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_path(root: Path, image: Any) -> tuple[str, Path]:
    if (not isinstance(image, str) or not image or "\\" in image
            or Path(image).is_absolute() or ".." in Path(image).parts):
        raise ValueError("OCR image path must stay inside dataset root")
    candidate = root / image
    path = candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"OCR image must be a file inside dataset root: {image}")
    if candidate.is_symlink() or any(parent.is_symlink() for parent in candidate.parents if parent != root and parent.is_relative_to(root)):
        raise ValueError(f"OCR image may not use a symlink: {image}")
    return path.relative_to(root).as_posix(), path


def _parse_manifest(root: Path, raw: Any, manifest_bytes: bytes) -> OCRManifest:
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError("ocr.json must use version 1")
    rows = raw.get("samples")
    if not isinstance(rows, list) or not rows:
        raise ValueError("ocr.json needs explicit image/text/split samples")
    records: list[OCRRecord] = []
    path_splits: dict[str, str] = {}
    hash_splits: dict[str, str] = {}
    split_counts = {"train": 0, "val": 0, "test": 0}
    train_characters: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"OCR sample {index} must be an object")
        image, path = _source_path(root, row.get("image"))
        if image in path_splits:
            raise ValueError(f"OCR source image occurs more than once: {image}")
        split = row.get("split")
        if not isinstance(split, str) or split not in split_counts:
            raise ValueError(f"OCR sample {index} split must be train, val or test")
        label = row.get("text")
        if (not isinstance(label, str) or not label.strip()
                or any(ord(character) < 32 for character in label)):
            raise ValueError(f"OCR sample {index} needs a nonempty single-line text label")
        source_bytes = path.read_bytes()
        actual_sha = _sha256(source_bytes)
        claimed_sha = row.get("source_sha256")
        if not isinstance(claimed_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", claimed_sha):
            raise ValueError(f"OCR sample {index} needs source_sha256")
        if claimed_sha != actual_sha:
            raise ValueError(f"Source SHA-256 mismatch for {image}")
        try:
            with Image.open(io.BytesIO(source_bytes)) as opened:
                if opened.width < 1 or opened.height < 1:
                    raise ValueError("empty image")
                opened.verify()
        except (OSError, ValueError) as exc:
            raise ValueError(f"OCR source image is unreadable: {image}: {exc}") from exc
        old_split = hash_splits.setdefault(actual_sha, split)
        if old_split != split:
            raise ValueError(f"Byte-identical OCR images occur in different split partitions: {image}")
        path_splits[image] = split
        split_counts[split] += 1
        if split == "train":
            train_characters.update(label)
        records.append(OCRRecord(image, path, label, split, actual_sha))
    if not split_counts["train"] or not split_counts["val"]:
        raise ValueError("OCR training requires separate nonempty train and val splits")
    alphabet = "".join(sorted(train_characters))
    for record in records:
        if record.split != "train" and not set(record.text).issubset(train_characters):
            raise ValueError(f"OCR {record.split} text uses a character outside the training alphabet: {record.image}")
    manifest_sha = _sha256(manifest_bytes)
    digest = hashlib.sha256(b"ocr-dataset-v1\0" + manifest_bytes)
    for image, source_sha in sorted((record.image, record.source_sha256) for record in records):
        digest.update(b"\0" + image.encode("utf-8") + b"\0" + source_sha.encode("ascii"))
    provenance = {
        "dataset_sha256": f"sha256:{digest.hexdigest()}",
        "manifest_sha256": manifest_sha,
        "source_sha256": dict(sorted((record.image, record.source_sha256) for record in records)),
        "split_counts": split_counts,
        "source_image_count": len(records),
    }
    from backend.engine.prepared_family_datasets import prepared_source_provenance
    provenance.update(prepared_source_provenance(root, raw, provenance['source_sha256']))
    return OCRManifest(root, records, alphabet, provenance)


def load_ocr_manifest(root: str | Path) -> OCRManifest:
    """Validate explicit labels, original bytes and split isolation."""
    root = Path(root).expanduser().resolve()
    path = root / "ocr.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"OCR needs explicit labels in a regular ocr.json: {path}")
    try:
        data = path.read_bytes()
        raw = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid ocr.json: {exc}") from exc
    return _parse_manifest(root, raw, data)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
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


def write_ocr_manifest(root: str | Path, rows: Sequence[dict[str, str]]) -> OCRManifest:
    """Write user-provided labels with hashes taken from the source image bytes."""
    root = Path(root).expanduser().resolve()
    samples = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"OCR sample {index} must be an object")
        image, path = _source_path(root, row.get("image"))
        samples.append({
            "image": image, "text": row.get("text"), "split": row.get("split"),
            "source_sha256": _sha256(path.read_bytes()),
        })
    raw = {"version": 1, "samples": samples}
    data = (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    manifest = _parse_manifest(root, raw, data)
    _atomic_write(root / "ocr.json", data)
    return manifest


def _prepare_image(source: Image.Image, image_size: tuple[int, int]) -> torch.Tensor:
    height, width = image_size
    if height < 8 or width < 8:
        raise ValueError("OCR image height and width must each be at least 8")
    gray = source.convert("L")
    scale = min(width / gray.width, height / gray.height)
    resized = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.Resampling.BILINEAR)
    canvas = Image.new("L", (width, height), 255)
    canvas.paste(resized, (0, (height - resized.height) // 2))
    pixels = torch.from_numpy(np.array(canvas, copy=True)).float()
    return ((255.0 - pixels) / 255.0).unsqueeze(0)


class OCRDataset(Dataset):
    """Recheck immutable source bytes at each read, then encode text for CTC."""

    def __init__(self, manifest: OCRManifest, *, split: str, image_size: tuple[int, int] = (32, 128)):
        if split not in {"train", "val", "test"}:
            raise ValueError("OCR split must be train, val or test")
        self.samples = [record for record in manifest.samples if record.split == split]
        self.image_size = image_size
        self.alphabet = manifest.alphabet
        self.index = {character: i + 1 for i, character in enumerate(self.alphabet)}

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> tuple[torch.Tensor, list[int]]:
        record = self.samples[index]
        source_bytes = record.image_path.read_bytes()
        if _sha256(source_bytes) != record.source_sha256:
            raise ValueError(f"OCR source changed after manifest validation: {record.image}")
        with Image.open(io.BytesIO(source_bytes)) as source:
            image = _prepare_image(source, self.image_size)
        return image, [self.index[character] for character in record.text]


class SmallCTCOCR(nn.Module):
    """Horizontal visual features and a bidirectional sequence recognizer."""

    def __init__(self, character_count: int):
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv2d(1, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2, 2),
            nn.Conv2d(32, 64, 3, padding=1), nn.ReLU(),
        )
        self.sequence = nn.GRU(64, 32, batch_first=True, bidirectional=True)
        self.head = nn.Linear(64, character_count + 1)  # index 0 = CTC blank

    def forward(self, images: torch.Tensor) -> torch.Tensor:
        features = self.features(images).mean(dim=2).transpose(1, 2)
        sequence, _ = self.sequence(features)
        return self.head(sequence)


def _collate(samples: list[tuple[torch.Tensor, list[int]]]) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    images, texts = zip(*samples)
    targets = torch.tensor([token for text in texts for token in text], dtype=torch.long)
    lengths = torch.tensor([len(text) for text in texts], dtype=torch.long)
    return torch.stack(images), targets, lengths


def _ctc_steps(width: int) -> int:
    return width // 4


def _required_steps(label: str) -> int:
    return len(label) + sum(left == right for left, right in zip(label, label[1:]))


def _logadd(left: float, right: float) -> float:
    if left == -math.inf:
        return right
    if right == -math.inf:
        return left
    larger, smaller = (left, right) if left >= right else (right, left)
    return larger + math.log1p(math.exp(smaller - larger))


def _decode(logits: torch.Tensor, alphabet: str, *, beam_width: int = 8) -> tuple[str, float]:
    """CTC prefix beam search; sum valid alignments before choosing text."""
    steps = logits.log_softmax(dim=-1).detach().cpu().tolist()
    beams: dict[tuple[int, ...], tuple[float, float]] = {(): (0.0, -math.inf)}
    for probabilities in steps:
        updated: dict[tuple[int, ...], tuple[float, float]] = {}
        for prefix, (blank_score, nonblank_score) in beams.items():
            total = _logadd(blank_score, nonblank_score)
            old_blank, old_nonblank = updated.get(prefix, (-math.inf, -math.inf))
            updated[prefix] = (_logadd(old_blank, total + probabilities[0]), old_nonblank)
            for token in range(1, len(probabilities)):
                score = probabilities[token]
                if prefix and token == prefix[-1]:
                    old_blank, old_nonblank = updated.get(prefix, (-math.inf, -math.inf))
                    updated[prefix] = (old_blank, _logadd(old_nonblank, nonblank_score + score))
                    extension_score = blank_score + score
                else:
                    extension_score = total + score
                extended = prefix + (token,)
                old_blank, old_nonblank = updated.get(extended, (-math.inf, -math.inf))
                updated[extended] = (old_blank, _logadd(old_nonblank, extension_score))
        ranked = sorted(updated.items(), key=lambda item: (-_logadd(*item[1]), item[0]))
        beams = dict(ranked[:beam_width])
    best = max(beams, key=lambda prefix: _logadd(*beams[prefix]))
    best_score = _logadd(*beams[best])
    normalization = -math.inf
    for blank_score, nonblank_score in beams.values():
        normalization = _logadd(normalization, _logadd(blank_score, nonblank_score))
    # Approximate posterior over retained prefixes, not a calibrated quality score.
    confidence = math.exp(best_score - normalization)
    return "".join(alphabet[token - 1] for token in best), confidence


def _distance(left: str, right: str) -> int:
    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, 1):
        current = [i]
        for j, right_char in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (left_char != right_char)))
        previous = current
    return previous[-1]


def _evaluate(model: SmallCTCOCR, dataset: OCRDataset, *, device: torch.device, batch_size: int, cancel_event=None) -> dict[str, Any]:
    if not dataset:
        raise ValueError("OCR evaluation split is empty")
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=False, collate_fn=_collate)
    criterion = nn.CTCLoss(blank=0)
    model.eval()
    losses: list[float] = []
    predictions: list[dict[str, Any]] = []
    sample_cursor = 0
    with torch.inference_mode():
        for images, targets, target_lengths in loader:
            if cancel_event is not None and cancel_event.is_set():raise InterruptedError('OCR validation cancelled')
            logits = model(images.to(device))
            input_lengths = torch.full((len(images),), logits.shape[1], dtype=torch.long)
            loss = criterion(logits.log_softmax(-1).transpose(0, 1), targets, input_lengths, target_lengths)
            losses.extend([float(loss.item())] * len(images))
            for item_logits in logits:
                record = dataset.samples[sample_cursor]
                predicted, confidence = _decode(item_logits, dataset.alphabet)
                predictions.append({
                    "image": record.image, "source_sha256": record.source_sha256,
                    "reference_text": record.text, "predicted_text": predicted,
                    "confidence": confidence,
                })
                sample_cursor += 1
    return {
        "sample_count": len(predictions),
        "loss": sum(losses) / len(losses),
        "exact_match_accuracy": sum(item["predicted_text"] == item["reference_text"] for item in predictions) / len(predictions),
        "character_error_rate": sum(_distance(item["predicted_text"], item["reference_text"]) for item in predictions) / sum(len(item["reference_text"]) for item in predictions),
        "samples": predictions,
    }


def _save_checkpoint(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        torch.save(payload, temporary)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def train_ocr(
    dataset_root: str | Path,
    output_dir: str | Path,
    *,
    epochs: int = 20,
    batch_size: int = 8,
    image_size: tuple[int, int] = (32, 128),
    learning_rate: float = 1e-3,
    device: str = "cpu",
    seed: int = 0,
    cancel_event=None,
    on_progress=None,
    warm_start=None,
) -> dict[str, Any]:
    """Fit a scratch OCR model on train only, selecting by validation CTC loss."""
    manifest = load_ocr_manifest(dataset_root)
    if epochs < 1 or batch_size < 1 or not math.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("OCR epochs, batch_size and learning_rate must be positive")
    if len(image_size) != 2 or image_size[0] < 8 or image_size[1] < 8:
        raise ValueError("OCR image_size must be (height, width), both at least 8")
    time_steps = _ctc_steps(image_size[1])
    for record in manifest.samples:
        if _required_steps(record.text) > time_steps:
            raise ValueError(f"OCR text is too long for image width: {record.image}")
    train = OCRDataset(manifest, split="train", image_size=image_size)
    val = OCRDataset(manifest, split="val", image_size=image_size)
    target_device = torch.device(device)
    torch.manual_seed(seed)
    model = SmallCTCOCR(len(manifest.alphabet)).to(target_device)
    lineage = {}
    if warm_start is not None:
        from backend.engine.specialized_warm_start import load_family_weights, requested_signature, require_new_candidate
        require_new_candidate(output_dir, warm_start)
        load_family_weights({'model_state_dict': model}, warm_start,
                            requested_signature('ocr', dataset_root, {'image_size': image_size}))
        lineage = {'warm_start': warm_start.lineage()}
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    criterion = nn.CTCLoss(blank=0)
    loader = DataLoader(train, batch_size=batch_size, shuffle=True, collate_fn=_collate, generator=torch.Generator().manual_seed(seed))
    output_dir = Path(output_dir).expanduser().resolve()
    best_loss = float("inf")
    best_epoch = 0
    history: list[float] = []
    for epoch in range(1, epochs + 1):
        if cancel_event is not None and cancel_event.is_set():raise InterruptedError('OCR training cancelled')
        model.train()
        total_loss = 0.0
        for batch_index, (images, targets, target_lengths) in enumerate(loader,1):
            if cancel_event is not None and cancel_event.is_set():raise InterruptedError('OCR training cancelled')
            optimizer.zero_grad(set_to_none=True)
            logits = model(images.to(target_device))
            input_lengths = torch.full((len(images),), logits.shape[1], dtype=torch.long)
            loss = criterion(logits.log_softmax(-1).transpose(0, 1), targets, input_lengths, target_lengths)
            if not torch.isfinite(loss):
                raise ValueError("OCR CTC training loss is not finite")
            loss.backward()
            optimizer.step()
            total_loss += float(loss.item()) * len(images)
            if on_progress is not None:on_progress({'epoch':epoch,'batch':batch_index,'batches':len(loader),'loss':float(loss.item())})
        if cancel_event is not None and cancel_event.is_set():raise InterruptedError('OCR training cancelled')
        history.append(total_loss / len(train))
        validation = _evaluate(model, val, device=target_device, batch_size=batch_size, cancel_event=cancel_event)
        if validation["loss"] < best_loss:
            best_loss = validation["loss"]
            best_epoch = epoch
            _save_checkpoint(output_dir / "best_model.pt", {
                "task": "ocr", "version": 1, "architecture": "small_cnn_bigru_ctc",
                "alphabet": manifest.alphabet, "image_size": list(image_size),
                "model_state_dict": model.state_dict(),
                "dataset_provenance": manifest.provenance,
                "best_epoch": epoch, "validation": validation,
                **lineage,
            })
    metadata = {
        "task": "ocr", "version": 1, "architecture": "small_cnn_bigru_ctc",
        "alphabet": manifest.alphabet, "image_size": list(image_size),
        "dataset_provenance": manifest.provenance,
        "best_epoch": best_epoch, "best_validation_loss": best_loss,
        "training_samples": len(train), "validation_samples": len(val),
        "epochs_completed": epochs, "training_loss_history": history,
        **lineage,
    }
    _atomic_write(output_dir / "model_meta.json", (json.dumps(metadata, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return metadata


def _load_model(checkpoint: str | Path, device: str) -> tuple[SmallCTCOCR, dict[str, Any], str]:
    path = Path(checkpoint).expanduser().resolve()
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(payload, dict) or payload.get("task") != "ocr" or payload.get("version") != 1:
        raise ValueError("Checkpoint is not an OCR model")
    alphabet = payload.get("alphabet")
    image_size = payload.get("image_size")
    if not isinstance(alphabet, str) or not alphabet or not isinstance(image_size, list) or len(image_size) != 2:
        raise ValueError("OCR checkpoint has invalid model metadata")
    model = SmallCTCOCR(len(alphabet))
    model.load_state_dict(payload["model_state_dict"])
    model = model.to(torch.device(device)).eval()
    return model, payload, _sha256(path.read_bytes())


def evaluate_ocr_checkpoint(
    checkpoint: str | Path, dataset_root: str | Path, *, split: str = "test", device: str = "cpu", batch_size: int = 8,
    allow_dataset_revision: bool = False,
) -> dict[str, Any]:
    """Evaluate only the requested held-out split of the checkpoint's dataset."""
    model, payload, model_sha = _load_model(checkpoint, device)
    manifest = load_ocr_manifest(dataset_root)
    training_sha = payload["dataset_provenance"]["dataset_sha256"]
    revised = manifest.provenance["dataset_sha256"] != training_sha
    if revised and not allow_dataset_revision:
        raise ValueError("OCR dataset provenance differs from checkpoint")
    if revised and manifest.provenance["source_sha256"] != payload["dataset_provenance"].get("source_sha256"):
        raise ValueError("OCR source images differ from checkpoint lineage")
    if (not set(manifest.alphabet).issubset(payload["alphabet"]) if allow_dataset_revision
            else manifest.alphabet != payload["alphabet"]):
        raise ValueError("OCR alphabet differs from checkpoint")
    dataset = OCRDataset(manifest, split=split, image_size=tuple(payload["image_size"]))
    dataset.alphabet = payload["alphabet"]
    dataset.index = {character: i + 1 for i, character in enumerate(dataset.alphabet)}
    result = _evaluate(model, dataset, device=torch.device(device), batch_size=batch_size)
    return {
        "task": "ocr", "split": split, "dataset_sha256": manifest.provenance["dataset_sha256"],
        "training_dataset_sha256": training_sha, "dataset_revision_changed": revised,
        "model_sha256": model_sha, **result,
    }


def predict_ocr(checkpoint: str | Path, image: str | Path, *, device: str = "cpu") -> dict[str, Any]:
    """Recognize one image with a saved candidate, reporting model and source hashes."""
    model, payload, model_sha = _load_model(checkpoint, device)
    path = Path(image).expanduser().resolve()
    source_bytes = path.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as source:
        tensor = _prepare_image(source, tuple(payload["image_size"]))
    with torch.inference_mode():
        logits = model(tensor.unsqueeze(0).to(torch.device(device)))[0]
        recognized, confidence = _decode(logits, payload["alphabet"])
    return {
        "task": "ocr", "text": recognized, "confidence": confidence,
        "source_image": str(path), "source_sha256": _sha256(source_bytes),
        "model_sha256": model_sha,
        "dataset_sha256": payload["dataset_provenance"]["dataset_sha256"],
        "best_epoch": payload["best_epoch"],
    }


def predict_ocr_array(checkpoint: str | Path, image_rgb: np.ndarray, *, device: str = "cpu") -> dict[str, Any]:
    """Array adapter used by app and exported flow; preserves the same CTC decoder."""
    model,payload,model_sha = _load_model(checkpoint,device)
    tensor = _prepare_image(Image.fromarray(image_rgb),tuple(payload['image_size']))
    with torch.inference_mode():
        text,confidence = _decode(model(tensor.unsqueeze(0).to(torch.device(device)))[0],payload['alphabet'])
    return {'task':'ocr','text':text,'confidence':confidence,'model_sha256':model_sha}

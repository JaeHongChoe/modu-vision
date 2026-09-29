"""Small trained defect-crop GAN whose outputs always require human review.

This is an explicit crop dataset and a saved generator, distinct from the
procedural sample generator. It makes no image-quality or production claim.
"""

from __future__ import annotations

from io import BytesIO
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any
import json
import os
import uuid

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.utils.data import DataLoader, Dataset


MANIFEST_NAME = "defect_gan.json"
IMAGE_SIZE = 64
LATENT_SIZE = 64


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_path(root: Path, relative: str) -> Path:
    name = PurePosixPath(relative)
    if name.is_absolute() or not name.parts or any(part in (".", "..") for part in name.parts):
        raise ValueError("Defect GAN image path must be relative and remain within the dataset")
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise ValueError("Defect GAN image is outside the dataset or missing")
    return candidate


def _verified_samples(root: Path, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(samples, list) or not samples:
        raise ValueError("Defect GAN manifest needs explicit image crops")
    verified: list[dict[str, Any]] = []
    path_split: dict[str, str] = {}
    hash_split: dict[str, str] = {}
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("image"), str):
            raise ValueError("Defect GAN image path is invalid")
        image = _source_path(root, sample["image"])
        split = sample.get("split")
        if split not in ("train", "val", "test"):
            raise ValueError("Defect GAN split must be train, val, or test")
        fingerprint = _hash(image)
        supplied = sample.get("source_sha256")
        if supplied is not None and supplied != fingerprint:
            raise ValueError("Defect GAN source image hash changed")
        if sample["image"] in path_split and path_split[sample["image"]] != split:
            raise ValueError("Defect GAN source image leaks across split")
        if fingerprint in hash_split and hash_split[fingerprint] != split:
            raise ValueError("Duplicate source bytes leak across split")
        path_split[sample["image"]] = split
        hash_split[fingerprint] = split
        bbox = sample.get("bbox")
        if (not isinstance(bbox, list) or len(bbox) != 4 or
                any(isinstance(value, bool) or not isinstance(value, int) for value in bbox)):
            raise ValueError("Defect GAN crop bbox must contain four integers")
        with Image.open(BytesIO(image.read_bytes())) as opened:
            width, height = opened.size
        x1, y1, x2, y2 = bbox
        if x1 < 0 or y1 < 0 or x2 > width or y2 > height or x2 - x1 < 16 or y2 - y1 < 16:
            raise ValueError("Defect GAN crop bbox is outside the image or too small")
        verified.append({"image": sample["image"], "bbox": bbox, "split": split,
                         "source_sha256": fingerprint})
    return verified


def write_defect_gan_manifest(root: str | Path, rows: list[dict[str, Any]]) -> Path:
    root = Path(root).expanduser().resolve()
    samples = _verified_samples(root, rows)
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink():
        raise ValueError("Defect GAN manifest cannot be a symbolic link")
    temporary = root / f".{MANIFEST_NAME}.{uuid.uuid4().hex}.tmp"
    temporary.write_text(json.dumps({"version": 1, "image_size": IMAGE_SIZE,
                                     "samples": samples}, indent=2), encoding="utf-8")
    os.replace(temporary, manifest)
    return manifest


def load_defect_gan_manifest(root: str | Path) -> dict[str, Any]:
    root = Path(root).expanduser().resolve()
    manifest = root / MANIFEST_NAME
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Defect GAN manifest is missing or linked")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Defect GAN manifest is unreadable") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("image_size") != IMAGE_SIZE:
        raise ValueError("Unsupported Defect GAN manifest")
    samples = payload.get("samples")
    if not isinstance(samples, list) or any(not isinstance(row, dict) or
            not isinstance(row.get("source_sha256"), str) for row in samples):
        raise ValueError("Defect GAN manifest requires source SHA-256 for every crop")
    _verified_samples(root, samples)
    return payload


class _DefectCrops(Dataset):
    def __init__(self, root: Path, samples: list[dict[str, Any]]):
        self.root = root
        self.samples = samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> torch.Tensor:
        item = self.samples[index]
        image = _source_path(self.root, item["image"])
        raw = image.read_bytes()
        if sha256(raw).hexdigest() != item["source_sha256"]:
            raise ValueError("Defect GAN source image hash changed during training")
        with Image.open(BytesIO(raw)) as opened:
            crop = opened.convert("RGB").crop(tuple(item["bbox"]))
            crop = crop.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.BILINEAR)
            array = np.asarray(crop, dtype=np.float32).copy()
        return torch.from_numpy(array).permute(2, 0, 1) / 127.5 - 1.0


class DefectGenerator(nn.Module):
    def __init__(self, base_channels: int = 16):
        super().__init__()
        base = base_channels
        self.network = nn.Sequential(
            nn.ConvTranspose2d(LATENT_SIZE, base * 8, 4, 1, 0, bias=False),
            nn.BatchNorm2d(base * 8), nn.ReLU(True),
            nn.ConvTranspose2d(base * 8, base * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 4), nn.ReLU(True),
            nn.ConvTranspose2d(base * 4, base * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 2), nn.ReLU(True),
            nn.ConvTranspose2d(base * 2, base, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base), nn.ReLU(True),
            nn.ConvTranspose2d(base, 3, 4, 2, 1, bias=False), nn.Tanh(),
        )

    def forward(self, latent: torch.Tensor) -> torch.Tensor:
        return self.network(latent)


class _DefectDiscriminator(nn.Module):
    def __init__(self, base_channels: int = 16):
        super().__init__()
        base = base_channels
        self.network = nn.Sequential(
            nn.Conv2d(3, base, 4, 2, 1, bias=False), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base, base * 2, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 2), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 2, base * 4, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 4), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 4, base * 8, 4, 2, 1, bias=False),
            nn.BatchNorm2d(base * 8), nn.LeakyReLU(0.2, True),
            nn.Conv2d(base * 8, 1, 4, 1, 0, bias=False), nn.Sigmoid(),
        )

    def forward(self, image: torch.Tensor) -> torch.Tensor:
        return self.network(image).flatten()


def train_defect_gan(
    root: str | Path, output_dir: str | Path, *, epochs: int = 20,
    batch_size: int = 8, seed: int = 0, device: str = "cpu", base_channels: int = 16,
) -> dict[str, Any]:
    if epochs < 1 or batch_size < 2 or base_channels < 8:
        raise ValueError("Defect GAN epochs must be positive, batch size >= 2, base channels >= 8")
    root = Path(root).expanduser().resolve()
    manifest = load_defect_gan_manifest(root)
    samples = [row for row in manifest["samples"] if row["split"] == "train"]
    if len(samples) < 2:
        raise ValueError("Defect GAN needs at least two explicit training crops")
    dev = torch.device(device)
    torch.manual_seed(seed)
    loader = DataLoader(_DefectCrops(root, samples), batch_size=min(batch_size, len(samples)),
                        shuffle=True, drop_last=False)
    generator = DefectGenerator(base_channels).to(dev)
    discriminator = _DefectDiscriminator(base_channels).to(dev)
    loss_fn = nn.BCELoss()
    generator_opt = torch.optim.Adam(generator.parameters(), lr=2e-4, betas=(0.5, 0.999))
    discriminator_opt = torch.optim.Adam(discriminator.parameters(), lr=2e-4, betas=(0.5, 0.999))
    generator_loss = discriminator_loss = 0.0
    for _ in range(epochs):
        for real in loader:
            real = real.to(dev)
            size = real.shape[0]
            if size < 2:
                continue  # BatchNorm cannot train on the final singleton batch.
            latent = torch.randn(size, LATENT_SIZE, 1, 1, device=dev)
            fake = generator(latent)
            discriminator_opt.zero_grad(set_to_none=True)
            discriminator_loss_tensor = (
                loss_fn(discriminator(real), torch.ones(size, device=dev)) +
                loss_fn(discriminator(fake.detach()), torch.zeros(size, device=dev))
            )
            discriminator_loss_tensor.backward()
            discriminator_opt.step()
            generator_opt.zero_grad(set_to_none=True)
            generator_loss_tensor = loss_fn(discriminator(fake), torch.ones(size, device=dev))
            generator_loss_tensor.backward()
            generator_opt.step()
            generator_loss = float(generator_loss_tensor.item())
            discriminator_loss = float(discriminator_loss_tensor.item())
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / "best_model.pt"
    temporary = output / f".{checkpoint.name}.{uuid.uuid4().hex}.tmp"
    source_manifest_sha256 = _hash(root / MANIFEST_NAME)
    payload = {
        "model_kind": "dcgan_defect_crop", "image_size": IMAGE_SIZE,
        "latent_size": LATENT_SIZE, "base_channels": base_channels,
        "generator_state_dict": generator.cpu().state_dict(),
        "discriminator_state_dict": discriminator.cpu().state_dict(),
        "source_manifest_sha256": source_manifest_sha256,
        "sample_count": len(samples), "epochs": epochs, "seed": seed,
    }
    torch.save(payload, temporary)
    os.replace(temporary, checkpoint)
    meta = {key: value for key, value in payload.items() if not key.endswith("state_dict")}
    meta.update({"checkpoint_sha256": _hash(checkpoint),
                 "last_generator_loss": generator_loss,
                 "last_discriminator_loss": discriminator_loss,
                 "quality_status": "unvalidated"})
    (output / "model_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return {"status": "completed", "checkpoint_path": str(checkpoint), **meta}


def generate_defect_candidates(
    checkpoint_path: str | Path, output_dir: str | Path, *, count: int = 8,
    seed: int = 0, device: str = "cpu",
) -> dict[str, Any]:
    if count < 1 or count > 500:
        raise ValueError("Defect GAN output count must be between 1 and 500")
    checkpoint_path = Path(checkpoint_path).expanduser().resolve()
    meta_path = checkpoint_path.parent / "model_meta.json"
    if not checkpoint_path.is_file() or not meta_path.is_file():
        raise ValueError("Defect GAN checkpoint and metadata are required")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    digest = _hash(checkpoint_path)
    if meta.get("checkpoint_sha256") != digest:
        raise ValueError("Defect GAN checkpoint hash changed")
    state = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    if (state.get("model_kind") != "dcgan_defect_crop" or
            state.get("source_manifest_sha256") != meta.get("source_manifest_sha256")):
        raise ValueError("Defect GAN checkpoint metadata mismatch")
    base_channels = int(state["base_channels"])
    dev = torch.device(device)
    generator = DefectGenerator(base_channels).to(dev)
    generator.load_state_dict(state["generator_state_dict"], strict=True)
    generator.eval()
    random = torch.Generator(device="cpu").manual_seed(seed)
    latent = torch.randn(count, LATENT_SIZE, 1, 1, generator=random).to(dev)
    with torch.no_grad():
        images = generator(latent).cpu().numpy()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    candidates = []
    for index, tensor in enumerate(images, 1):
        array = np.clip((tensor.transpose(1, 2, 0) + 1.0) * 127.5, 0, 255).astype(np.uint8)
        path = output / f"synthetic_candidate_{index:04d}.png"
        Image.fromarray(array).save(path)
        candidates.append({"id": f"candidate_{index:04d}", "path": str(path),
                           "sha256": _hash(path), "status": "synthetic_unreviewed"})
    review = {"model_kind": "dcgan_defect_crop", "generator_sha256": digest,
              "source_manifest_sha256": state["source_manifest_sha256"],
              "quality_status": "unvalidated", "seed": seed, "candidates": candidates}
    (output / "review_manifest.json").write_text(json.dumps(review, indent=2), encoding="utf-8")
    return review

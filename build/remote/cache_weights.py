"""Bake the training defaults into a network-independent Docker worker image."""

from __future__ import annotations

import gc
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

import torch
from torchvision import models
from torchvision.models import detection, segmentation


WEIGHTS = {
    "resnet18": models.ResNet18_Weights.DEFAULT,
    "convnext_tiny": models.ConvNeXt_Tiny_Weights.DEFAULT,
    "efficientnet_b0": models.EfficientNet_B0_Weights.DEFAULT,
    "fasterrcnn_mobilenet_v3_large_fpn": detection.FasterRCNN_MobileNet_V3_Large_FPN_Weights.DEFAULT,
    "fasterrcnn_resnet50_fpn_v2": detection.FasterRCNN_ResNet50_FPN_V2_Weights.DEFAULT,
    "deeplabv3_resnet50": segmentation.DeepLabV3_ResNet50_Weights.DEFAULT,
    "deeplabv3_mobilenet_v3_large": segmentation.DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT,
}


def main() -> None:
    checkpoint_dir = Path(torch.hub.get_dir()) / "checkpoints"
    manifest_path = Path(
        os.environ.get("MODU_VISION_WEIGHTS_MANIFEST")
        or checkpoint_dir.parent.parent / "weights-manifest.json"
    )
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, dict[str, str]] = {}

    for name, weights in WEIGHTS.items():
        # TorchVision verifies the URL's hash prefix when downloading.
        state = weights.get_state_dict(progress=False, check_hash=True)
        del state
        gc.collect()

        filename = Path(urlparse(weights.url).path).name
        checkpoint = checkpoint_dir / filename
        match = re.search(r"-([0-9a-f]{8,64})\.(?:pth|pt)$", filename)
        if not checkpoint.is_file() or match is None:
            raise RuntimeError(f"Missing hash-named checkpoint: {name}")
        digest = hashlib.sha256()
        with checkpoint.open("rb") as reader:
            for block in iter(lambda: reader.read(1024 * 1024), b""):
                digest.update(block)
        hexdigest = digest.hexdigest()
        if not hexdigest.startswith(match.group(1)):
            raise RuntimeError(f"Checksum mismatch for {name}")
        manifest[name] = {"file": filename, "sha256": hexdigest}

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

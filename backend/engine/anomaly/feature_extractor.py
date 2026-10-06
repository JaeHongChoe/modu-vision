"""
backend/engine/anomaly/feature_extractor.py

Intermediate ResNet Feature Extractor for Unsupervised Anomaly Detection.
Hooks intermediate representations from layer2 and layer3.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple
import hashlib
import io
from pathlib import Path
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.models as models


class ResNetFeatureExtractor(nn.Module):
    """
    Extracts concatenated, spatially aligned multi-scale feature maps from
    layer2 and layer3 of a pre-trained ResNet backbone.
    """

    def __init__(
        self,
        backbone_name: str = "resnet18",
        pretrained: bool = True,
        local_avg_pool: bool = False,
        pretrained_checkpoint: Optional[str] = None,
        pretrained_sha256: Optional[str] = None,
    ):
        super().__init__()
        self.backbone_name = backbone_name.lower().strip()
        self.local_avg_pool = local_avg_pool

        if self.backbone_name in ("resnet18", "resnet"):
            weights = models.ResNet18_Weights.DEFAULT if pretrained and not pretrained_checkpoint else None
            self.backbone = models.resnet18(weights=weights)
            self.embed_dim = 128 + 256  # layer2 (128) + layer3 (256)
        elif self.backbone_name == "resnet50":
            weights = models.ResNet50_Weights.DEFAULT if pretrained and not pretrained_checkpoint else None
            self.backbone = models.resnet50(weights=weights)
            self.embed_dim = 512 + 1024  # layer2 (512) + layer3 (1024)
        else:
            raise ValueError(f"Unsupported anomaly backbone: {backbone_name}")

        self.model_metadata = {'pretrained': bool(pretrained), 'input_normalization': 'rgb_0_1'}
        if pretrained and pretrained_checkpoint:
            encoded = Path(pretrained_checkpoint).expanduser().read_bytes()
            digest = hashlib.sha256(encoded).hexdigest()
            if pretrained_sha256 and digest != pretrained_sha256.strip().lower():
                raise ValueError('Pretrained feature checkpoint hash does not match the requested weights')
            state = torch.load(io.BytesIO(encoded), map_location='cpu', weights_only=True)
            if isinstance(state, dict):
                state = state.get('model_state_dict', state.get('state_dict', state))
            self.backbone.load_state_dict(state, strict=True)
            self.model_metadata.update(pretrained_sha256=digest,
                                       pretrained_source=f'local:{Path(pretrained_checkpoint).name}')
        elif weights is not None:
            # The torchvision default has already resolved this exact checkpoint.
            path = Path(torch.hub.get_dir()) / 'checkpoints' / weights.url.rsplit('/', 1)[-1]
            from backend.engine.model_backbones import checkpoint_sha256
            digest = checkpoint_sha256(path)
            expected_prefix = path.stem.rsplit('-', 1)[-1]
            if not digest.startswith(expected_prefix):
                raise ValueError('Cached official feature checkpoint hash differs from its torchvision identity')
            self.model_metadata.update(pretrained_sha256=digest, pretrained_source=weights.url)

        self._features: Dict[str, torch.Tensor] = {}

        def _get_hook(name: str):
            def _hook(module, inp, out):
                self._features[name] = out
            return _hook

        self.backbone.layer2.register_forward_hook(_get_hook("layer2"))
        self.backbone.layer3.register_forward_hook(_get_hook("layer3"))
        self.backbone.eval()

        if self.local_avg_pool:
            self.pool = nn.AvgPool2d(kernel_size=3, stride=1, padding=1)
        else:
            self.pool = nn.Identity()

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Extracts concatenated feature representations.
        Returns:
            Tensor of shape [B, embed_dim, H_patch, W_patch]
        """
        with torch.no_grad():
            _ = self.backbone(x)
            f2 = self._features["layer2"]
            f3 = self._features["layer3"]

            # Spatially align layer3 to layer2 resolution
            f3_up = F.interpolate(f3, size=f2.shape[2:], mode="bilinear", align_corners=False)
            f_cat = torch.cat([f2, f3_up], dim=1)
            return self.pool(f_cat)

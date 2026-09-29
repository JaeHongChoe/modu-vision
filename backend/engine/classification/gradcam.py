"""
backend/engine/classification/gradcam.py

Pure PyTorch native Grad-CAM implementation for visual explainability (XAI).
Extracts activation heatmaps from target convolutional layers without external dependencies.
"""

from __future__ import annotations

from typing import List, Optional, Union
import torch
import torch.nn as nn
import torch.nn.functional as F


class GradCAM:
    """
    Pure PyTorch native Grad-CAM module utilizing forward and full-backward hooks.
    
    Supports:
      - Context manager syntax (`with GradCAM(...) as cam: ...`)
      - Single-sample or batched feature map interpolation
      - Min-max normalization into [0.0, 1.0]
    """

    def __init__(self, model: nn.Module, target_layer: nn.Module):
        self.model = model
        self.target_layer = target_layer
        self.activations: Optional[torch.Tensor] = None
        self.gradients: Optional[torch.Tensor] = None
        self.hooks: List[Any] = []
        self._register()

    def _register(self) -> None:
        def f_hook(module, inp, out):
            self.activations = out

        def b_hook(module, gin, gout):
            self.gradients = gout[0]

        self.hooks.append(self.target_layer.register_forward_hook(f_hook))
        self.hooks.append(self.target_layer.register_full_backward_hook(b_hook))

    def remove(self) -> None:
        """Removes all registered PyTorch hooks to prevent memory leaks."""
        for h in self.hooks:
            h.remove()
        self.hooks = []

    def __enter__(self) -> "GradCAM":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.remove()

    def generate(
        self,
        x: torch.Tensor,
        target_class: Optional[Union[int, torch.Tensor]] = None,
    ) -> torch.Tensor:
        """
        Generates normalized activation heatmap for input tensor x.
        
        Args:
            x: Input image tensor of shape [B, 3, H, W]
            target_class: Specific class index or tensor of indices. If None, uses argmax.
            
        Returns:
            Normalized heatmap tensor of shape [B, H, W] in range [0.0, 1.0].
        """
        with torch.enable_grad():
            self.model.eval()
            self.model.zero_grad()
            B, C, H, W = x.shape

            logits = self.model(x)

            if target_class is None:
                targets = torch.argmax(logits, dim=1)
            elif isinstance(target_class, int):
                targets = torch.full((B,), target_class, dtype=torch.long, device=x.device)
            else:
                targets = torch.as_tensor(target_class, device=x.device, dtype=torch.long)
                if targets.dim() == 0:
                    targets = targets.expand(B)
                elif targets.dim() == 1 and targets.size(0) == 1 and B > 1:
                    targets = targets.expand(B)

            # Single backward pass for entire batch
            score = logits.gather(1, targets.unsqueeze(1)).sum()
            score.backward()

            if self.gradients is None or self.activations is None:
                raise RuntimeError("GradCAM hooks failed to capture gradients or activations.")

            # Vectorized global average pooling of gradients: [B, C_feat, 1, 1]
            weights = torch.mean(self.gradients, dim=(2, 3), keepdim=True)

            # Weighted combination of activation maps
            cam = torch.sum(weights * self.activations, dim=1, keepdim=True)
            cam = F.relu(cam)

            # Upsample to input image resolution
            cam = F.interpolate(cam, size=(H, W), mode="bilinear", align_corners=False)

            # Per-sample min-max normalization into [0.0, 1.0]
            c_min = cam.view(B, -1).min(dim=1)[0].view(B, 1, 1, 1)
            c_max = cam.view(B, -1).max(dim=1)[0].view(B, 1, 1, 1)
            denom = torch.where(c_max - c_min > 1e-8, c_max - c_min, torch.ones_like(c_max))
            norm_cam = torch.where(c_max - c_min > 1e-8, (cam - c_min) / denom, torch.zeros_like(cam))

            return norm_cam.squeeze(1).detach()

"""
backend/engine/augmentations.py

Industrial-Safe Non-Destructive Data Augmentation Pipeline.
Strictly avoids random resized crops that can destroy microscopic defects (<0.5% area).
Supports synchronized multi-task transformations for Classification, Detection,
Segmentation, and Anomaly Detection.
"""

from __future__ import annotations

import math
import random
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import torch
import torchvision.transforms.functional as TF


class IndustrialAugmentationPipeline:
    """
    Industrial-Safe Data Augmentation Pipeline.
    
    Invariants Enforced:
      1. Zero destructive cropping: Preserves full field of view and microscopic defects.
      2. Bounded photometric jitter: Brightness/contrast variations within [-12%, +12%].
      3. Bounded angular rotation: Bounded within [-10.0, 10.0] degrees.
      4. Constrained cutout: Maximum 8-10% of image dimension.
      5. Task-specific spatial synchronization (box projection, nearest-neighbor mask).
    """

    def __init__(
        self,
        task: str = "classification",
        brightness_range: Tuple[float, float] = (-0.12, 0.12),
        contrast_range: Tuple[float, float] = (-0.12, 0.12),
        max_rotation_deg: float = 10.0,
        flip_horizontal: bool = True,
        flip_vertical: bool = True,
        cutout_prob: float = 0.3,
        max_cutout_size_ratio: float = 0.08,
        is_training: bool = True,
    ):
        self.task = task.lower().strip()
        self.brightness_range = brightness_range
        self.contrast_range = contrast_range
        self.max_rotation_deg = max_rotation_deg
        self.flip_horizontal = flip_horizontal
        self.flip_vertical = flip_vertical
        self.cutout_prob = cutout_prob
        self.max_cutout_size_ratio = max_cutout_size_ratio
        self.is_training = is_training

    def _apply_photometric(self, img: torch.Tensor) -> torch.Tensor:
        """Applies subtle factory lighting and contrast jitter within industrial tolerances."""
        if self.brightness_range[0] != self.brightness_range[1]:
            factor = 1.0 + random.uniform(self.brightness_range[0], self.brightness_range[1])
            img = TF.adjust_brightness(img, factor)

        if self.contrast_range[0] != self.contrast_range[1]:
            factor = 1.0 + random.uniform(self.contrast_range[0], self.contrast_range[1])
            img = TF.adjust_contrast(img, factor)

        # Subtle sensor Gaussian noise (p=0.2, sigma in [0.002, 0.012])
        if random.random() < 0.2:
            sigma = random.uniform(0.002, 0.012)
            noise = torch.randn_like(img) * sigma
            img = torch.clamp(img + noise, 0.0, 1.0)

        return img

    def _apply_cutout(self, img: torch.Tensor) -> torch.Tensor:
        """Applies small cutout patch simulating surface dust without occluding defects."""
        if random.random() > self.cutout_prob:
            return img

        _, h, w = img.shape
        max_patch_h = max(4, int(h * self.max_cutout_size_ratio))
        max_patch_w = max(4, int(w * self.max_cutout_size_ratio))

        patch_h = random.randint(4, max_patch_h)
        patch_w = random.randint(4, max_patch_w)

        top = random.randint(0, max(0, h - patch_h))
        left = random.randint(0, max(0, w - patch_w))

        img = img.clone()
        mean_val = img.mean(dim=(1, 2), keepdim=True)
        img[:, top : top + patch_h, left : left + patch_w] = mean_val
        return img

    def _rotate_boxes(
        self, boxes: torch.Tensor, angle_deg: float, width: int, height: int
    ) -> torch.Tensor:
        """Rotates bounding boxes around image center and clamps to image borders."""
        if len(boxes) == 0 or abs(angle_deg) < 1e-4:
            return boxes

        rad = math.radians(-angle_deg)  # TF.rotate is counter-clockwise
        cx, cy = width / 2.0, height / 2.0
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        new_boxes = []
        for box in boxes:
            x1, y1, x2, y2 = box.tolist()
            corners = [
                (x1 - cx, y1 - cy),
                (x2 - cx, y1 - cy),
                (x2 - cx, y2 - cy),
                (x1 - cx, y2 - cy),
            ]
            rot_corners = [(x * cos_a - y * sin_a + cx, x * sin_a + y * cos_a + cy) for x, y in corners]
            xs = [p[0] for p in rot_corners]
            ys = [p[1] for p in rot_corners]

            rx1 = max(0.0, min(float(width), min(xs)))
            ry1 = max(0.0, min(float(height), min(ys)))
            rx2 = max(0.0, min(float(width), max(xs)))
            ry2 = max(0.0, min(float(height), max(ys)))

            # Only retain box if valid non-degenerate area
            if (rx2 - rx1) >= 1.0 and (ry2 - ry1) >= 1.0:
                new_boxes.append([rx1, ry1, rx2, ry2])
            else:
                new_boxes.append([x1, y1, x2, y2])

        return torch.tensor(new_boxes, dtype=boxes.dtype, device=boxes.device)

    def forward_classification(self, img: torch.Tensor) -> torch.Tensor:
        """Transform for Task 1: Classification."""
        if not self.is_training:
            return img

        if self.flip_horizontal and random.random() < 0.5:
            img = TF.hflip(img)
        if self.flip_vertical and random.random() < 0.5:
            img = TF.vflip(img)
        if self.max_rotation_deg > 0:
            angle = random.uniform(-self.max_rotation_deg, self.max_rotation_deg)
            img = TF.rotate(img, angle, interpolation=TF.InterpolationMode.BILINEAR)

        img = self._apply_photometric(img)
        img = self._apply_cutout(img)
        return img

    def forward_detection(self, img: torch.Tensor, target: Dict[str, Any]) -> Tuple[torch.Tensor, Dict[str, Any]]:
        """Synchronized transform for Task 2: Detection."""
        if not self.is_training:
            return img, target

        _, h, w = img.shape
        target = {k: v.clone() if isinstance(v, torch.Tensor) else v for k, v in target.items()}
        boxes = target.get("boxes", torch.zeros((0, 4), dtype=torch.float32))

        # Horizontal Flip
        if self.flip_horizontal and random.random() < 0.5:
            img = TF.hflip(img)
            if len(boxes) > 0:
                x1 = boxes[:, 0].clone()
                x2 = boxes[:, 2].clone()
                boxes[:, 0] = w - x2
                boxes[:, 2] = w - x1

        # Vertical Flip
        if self.flip_vertical and random.random() < 0.5:
            img = TF.vflip(img)
            if len(boxes) > 0:
                y1 = boxes[:, 1].clone()
                y2 = boxes[:, 3].clone()
                boxes[:, 1] = h - y2
                boxes[:, 3] = h - y1

        # Slight Rotation
        if self.max_rotation_deg > 0:
            angle = random.uniform(-self.max_rotation_deg, self.max_rotation_deg)
            img = TF.rotate(img, angle, interpolation=TF.InterpolationMode.BILINEAR)
            boxes = self._rotate_boxes(boxes, angle, w, h)

        target["boxes"] = boxes
        img = self._apply_photometric(img)
        img = self._apply_cutout(img)
        return img, target

    def forward_segmentation(self, img: torch.Tensor, mask: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Synchronized transform for Task 3: Segmentation (NEAREST mask interpolation)."""
        if not self.is_training:
            return img, mask

        # Synchronized Flips
        if self.flip_horizontal and random.random() < 0.5:
            img = TF.hflip(img)
            mask = TF.hflip(mask)
        if self.flip_vertical and random.random() < 0.5:
            img = TF.vflip(img)
            mask = TF.vflip(mask)

        # Synchronized Rotation
        if self.max_rotation_deg > 0:
            angle = random.uniform(-self.max_rotation_deg, self.max_rotation_deg)
            img = TF.rotate(img, angle, interpolation=TF.InterpolationMode.BILINEAR)
            squeeze = False
            if mask.ndim == 2:
                mask = mask.unsqueeze(0)
                squeeze = True
            mask = TF.rotate(mask, angle, interpolation=TF.InterpolationMode.NEAREST)
            if squeeze:
                mask = mask.squeeze(0)

        img = self._apply_photometric(img)
        img = self._apply_cutout(img)
        return img, mask

    def forward_anomaly(self, img: torch.Tensor) -> torch.Tensor:
        """Transform for Task 4: Anomaly Detection (100% normal OK preservation)."""
        if not self.is_training:
            return img

        if self.flip_horizontal and random.random() < 0.5:
            img = TF.hflip(img)
        if self.flip_vertical and random.random() < 0.5:
            img = TF.vflip(img)
        if self.max_rotation_deg > 0:
            angle = random.uniform(-self.max_rotation_deg, self.max_rotation_deg)
            img = TF.rotate(img, angle, interpolation=TF.InterpolationMode.BILINEAR)

        img = self._apply_photometric(img)
        # Note: Cutout is intentionally omitted on anomaly training images
        return img

    def __call__(self, img: torch.Tensor) -> torch.Tensor:
        """Callable interface matching PyTorch Dataset standard transform call."""
        if self.task == "detection":
            return self.forward_classification(img)
        elif self.task == "segmentation":
            return self.forward_classification(img)
        elif self.task in ("anomaly", "anomaly_detection"):
            return self.forward_anomaly(img)
        else:
            return self.forward_classification(img)


def create_industrial_transforms(
    task: str = "classification",
    is_training: bool = True,
    preset: str = "fast",
    profile: str = "industrial",
) -> IndustrialAugmentationPipeline:
    """Factory helper constructing task-optimized industrial augmentation pipeline."""
    cutout_prob = 0.2 if preset == "fast" else 0.35
    max_rot = 5.0 if preset == "fast" else 10.0
    if profile not in ('none', 'photometric', 'industrial'):
        raise ValueError('Unsupported augmentation profile')
    return IndustrialAugmentationPipeline(
        task=task,
        max_rotation_deg=max_rot if profile == 'industrial' else 0,
        flip_horizontal=profile == 'industrial',
        flip_vertical=profile == 'industrial',
        cutout_prob=cutout_prob if profile == 'industrial' else 0,
        is_training=is_training and profile != 'none',
    )

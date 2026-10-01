"""
backend/engine/augmentations.py

Industrial-Safe Non-Destructive Data Augmentation Pipeline.
Avoids random resized crops; deterministic crops require explicit parameters.
Supports synchronized multi-task transformations for Classification, Detection,
Segmentation, and Anomaly Detection.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import torch
import torchvision.transforms.functional as TF


@dataclass(frozen=True)
class AugmentedSample:
    image: torch.Tensor
    targets: Any
    transform_receipt: Dict[str, Any]


@dataclass(frozen=True)
class PhotometricTransform:
    """Explicit declaration that a custom image callback preserves geometry.

    Custom callbacks cannot be classified safely by introspection. Use this
    adapter for lighting/normalization callbacks; spatial callbacks must use
    augment_sample or the task-specific joint forward protocol instead.
    """
    transform: Callable

    def __call__(self, image):
        result = self.transform(image)
        if not isinstance(result, torch.Tensor) or result.shape[-2:] != image.shape[-2:]:
            raise ValueError("PhotometricTransform must preserve image geometry")
        return result


def _is_supported_photometric(transform) -> bool:
    """Allow explicit declarations and known photometric transforms only."""
    from torchvision import transforms
    from torchvision.transforms import v2
    if isinstance(transform, PhotometricTransform):
        return True
    names = ("ColorJitter", "Normalize", "Grayscale", "RandomGrayscale",
             "GaussianBlur", "RandomAdjustSharpness", "RandomAutocontrast",
             "RandomEqualize", "RandomInvert", "RandomPosterize", "RandomSolarize",
             "ConvertImageDtype", "ToDtype")
    safe_types = tuple(getattr(module, name) for module in (transforms, v2)
                       for name in names if hasattr(module, name))
    if type(transform) in safe_types:
        return True
    container_names = ("Compose", "RandomApply", "RandomChoice", "RandomOrder")
    containers = tuple(getattr(module, name) for module in (transforms, v2)
                       for name in container_names if hasattr(module, name))
    if type(transform) in containers:
        return all(_is_supported_photometric(child) for child in transform.transforms)
    if type(transform) is torch.nn.Sequential:
        return all(_is_supported_photometric(child) for child in transform.children())
    return False


def apply_sample_transform(transform, image, targets, *, task, seed=None) -> AugmentedSample:
    """Use the joint sample protocol while retaining legacy image-only callables.

    Unknown image callables require PhotometricTransform to explicitly declare
    geometry preservation. Spatial transforms with labels must implement
    augment_sample or the task-specific forward method.
    """
    if callable(getattr(transform, "augment_sample", None)):
        return transform.augment_sample(image, targets, task=task, seed=seed)
    method_name = "forward_detection" if task in {"detection", "rotated_detection"} else "forward_segmentation"
    method = getattr(transform, method_name, None)
    if callable(method):
        image, targets = method(image, targets)
    else:
        if targets is not None and not _is_supported_photometric(transform):
            raise ValueError("Labelled transforms require a joint sample protocol or an explicit PhotometricTransform callback")
        image = transform(image)
    return AugmentedSample(image, targets, {"version": 1, "task": task, "seed": seed,
                                           "legacy_transform": True})


class IndustrialAugmentationPipeline:
    """
    Industrial-Safe Data Augmentation Pipeline.
    
    Invariants Enforced:
      1. No default cropping: an explicit (top,left,height,width) crop is opt-in.
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
        crop: Optional[Tuple[int, int, int, int]] = None,
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
        self.crop = crop

    def _apply_photometric(self, img, rng=random, generator=None, receipt=None):
        """Bounded lighting changes; a local RNG makes seeded samples replayable."""
        if self.brightness_range[0] != self.brightness_range[1]:
            factor = 1.0 + rng.uniform(*self.brightness_range)
            img = TF.adjust_brightness(img, factor)
            if receipt is not None:
                receipt["brightness_factor"] = factor
        if self.contrast_range[0] != self.contrast_range[1]:
            factor = 1.0 + rng.uniform(*self.contrast_range)
            img = TF.adjust_contrast(img, factor)
            if receipt is not None:
                receipt["contrast_factor"] = factor
        if rng.random() < 0.2:
            sigma = rng.uniform(0.002, 0.012)
            if generator is None:
                noise = torch.randn_like(img)
            else:
                noise = torch.randn(img.shape, generator=generator, dtype=img.dtype).to(img.device)
            img = torch.clamp(img + noise * sigma, 0.0, 1.0)
            if receipt is not None:
                receipt["noise_sigma"] = sigma
        return img

    def _apply_cutout(self, img, rng=random, receipt=None):
        if self.cutout_prob <= 0 or rng.random() > self.cutout_prob:
            return img
        _, h, w = img.shape
        max_patch_h = max(4, int(h * self.max_cutout_size_ratio))
        max_patch_w = max(4, int(w * self.max_cutout_size_ratio))
        patch_h = rng.randint(4, max_patch_h)
        patch_w = rng.randint(4, max_patch_w)
        top = rng.randint(0, max(0, h - patch_h))
        left = rng.randint(0, max(0, w - patch_w))
        img = img.clone()
        img[:, top:top + patch_h, left:left + patch_w] = img.mean(dim=(1, 2), keepdim=True)
        if receipt is not None:
            receipt["cutout"] = [top, left, patch_h, patch_w]
        return img

    def _rotate_boxes(self, boxes, angle_deg, width, height):
        """Project XYXY edges around the same image center as TF.rotate.

        Positive TF angles are counterclockwise; screen coordinates have y down.
        An axis-aligned envelope contains all four transformed corners.
        """
        if boxes.numel() == 0 or abs(angle_deg) < 1e-4:
            return boxes
        rad = math.radians(-angle_deg)
        cx, cy = width / 2.0, height / 2.0
        x1, y1, x2, y2 = boxes.unbind(dim=1)
        xs = torch.stack((x1, x2, x2, x1), dim=1) - cx
        ys = torch.stack((y1, y1, y2, y2), dim=1) - cy
        rx = xs * math.cos(rad) - ys * math.sin(rad) + cx
        ry = xs * math.sin(rad) + ys * math.cos(rad) + cy
        return torch.stack((rx.amin(1), ry.amin(1), rx.amax(1), ry.amax(1)), dim=1)

    @staticmethod
    def _oriented_envelopes(boxes):
        """cx,cy,width,height,clockwise degrees -> unclipped XYXY envelopes."""
        radians = torch.deg2rad(boxes[:, 4])
        extent_x = (boxes[:, 2] * radians.cos().abs() + boxes[:, 3] * radians.sin().abs()) / 2
        extent_y = (boxes[:, 2] * radians.sin().abs() + boxes[:, 3] * radians.cos().abs()) / 2
        return torch.stack((boxes[:, 0] - extent_x, boxes[:, 1] - extent_y,
                            boxes[:, 0] + extent_x, boxes[:, 1] + extent_y), dim=1)

    @staticmethod
    def _refresh_detection_target(target, width, height):
        boxes = target["boxes"]
        boxes[:, 0::2] = boxes[:, 0::2].clamp(0, width)
        boxes[:, 1::2] = boxes[:, 1::2].clamp(0, height)
        valid = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
        # Drop invisible objects together with every per-object tensor. Image ID
        # is a sample identity even when its length happens to match box count.
        object_fields = {"boxes", "labels", "area", "iscrowd", "boxes_normalized", "masks",
                         "keypoints", "rotated_boxes", "rotated_boxes_valid", "rotated_polygons",
                         "rotated_polygons_valid", "direction_deg", "direction_valid"}
        for key, value in list(target.items()):
            if key in object_fields and isinstance(value, torch.Tensor) and value.ndim > 0 and value.shape[0] == len(boxes):
                target[key] = value[valid]
        boxes = target["boxes"]
        target["area"] = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        target["boxes_normalized"] = boxes / boxes.new_tensor([width, height, width, height])
        return target

    def augment_sample(self, image, targets=None, task=None, seed=None) -> AugmentedSample:
        """Sample spatial decisions once, and apply them to image and labels.

        seed uses local Python/Torch generators; it does not reset global state.
        Existing datasets keep their pair/triple return shapes; consumers that
        need a receipt may use this public sample protocol directly.

        Rotated boxes are [cx,cy,width,height,clockwise_degrees] in pixels, with
        axial angles canonicalized to [-90,90). Independent direction_deg is
        clockwise from +x in [0,360). Crops translate centers; partially clipped
        oriented rectangles are rejected because their visible polygon is not
        an oriented rectangle. The XYXY envelope may be clipped at image edges
        while the original oriented geometry is retained explicitly.
        Affinely resized native boxes may instead supply rotated_polygons
        [N,4,2]. Those exact vertices remain authoritative when the associated
        rotated_boxes_valid is false; no approximate rectangle is fabricated.
        """
        task = (task or self.task).lower().strip()
        receipt = {"version": 1, "task": task, "seed": seed,
                   "horizontal_flip": False, "vertical_flip": False,
                   "rotation_deg": 0.0, "crop": None,
                   "mask_interpolation": "nearest", "image_interpolation": "bilinear"}
        if not self.is_training:
            return AugmentedSample(image, targets, receipt)
        spatial_enabled = self.crop is not None or self.flip_horizontal or self.flip_vertical or self.max_rotation_deg > 0
        if spatial_enabled and task in {"detection", "rotated_detection", "segmentation"} and targets is None:
            raise ValueError("Spatial detection/segmentation augmentation requires joint targets or a mask")
        if task in {"segmentation", "anomaly", "anomaly_detection"} and targets is not None and not isinstance(targets, torch.Tensor):
            raise ValueError("Joint mask target must be a torch Tensor")
        rng = random if seed is None else random.Random(seed)
        generator = None if seed is None else torch.Generator().manual_seed(seed)
        is_detection = task in {"detection", "rotated_detection"} and targets is not None
        if is_detection and (self.crop is not None or self.flip_horizontal
                             or self.flip_vertical or self.max_rotation_deg > 0):
            unsupported = [field for field in ("masks", "keypoints")
                           if targets.get(field) is not None]
            if unsupported:
                raise ValueError("Unsupported detection geometry requires a joint spatial adapter: "
                                 + ", ".join(unsupported))
        mask = targets if isinstance(targets, torch.Tensor) and task in {"segmentation", "anomaly", "anomaly_detection"} else None
        target = {k: v.clone() if isinstance(v, torch.Tensor) else v for k, v in targets.items()} if is_detection else targets
        boxes = target.get("boxes", image.new_zeros((0, 4))) if is_detection else None
        initially_valid = ((boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])) if boxes is not None else None
        oriented = target.get("rotated_boxes") if is_detection else None
        polygons = target.get("rotated_polygons") if is_detection else None
        directions = target.get("direction_deg") if is_detection else None
        visible_after_crop = None
        _, height, width = image.shape
        if self.crop is not None:
            if (not isinstance(self.crop, (tuple, list)) or len(self.crop) != 4
                    or any(type(value) is not int for value in self.crop)):
                raise ValueError("Explicit crop requires integer (top,left,height,width)")
            top, left, crop_h, crop_w = self.crop
            if top < 0 or left < 0 or crop_h <= 0 or crop_w <= 0 or top + crop_h > height or left + crop_w > width:
                raise ValueError("Explicit crop must lie within the image")
            if oriented is not None or polygons is not None:
                envelope = (torch.cat((polygons.amin(1), polygons.amax(1)), dim=1)
                            if polygons is not None else self._oriented_envelopes(oriented))
                intersects = ((envelope[:, 2] > left) & (envelope[:, 0] < left + crop_w)
                              & (envelope[:, 3] > top) & (envelope[:, 1] < top + crop_h))
                contained = ((envelope[:, 0] >= left) & (envelope[:, 2] <= left + crop_w)
                             & (envelope[:, 1] >= top) & (envelope[:, 3] <= top + crop_h))
                geometry = polygons if polygons is not None else oriented
                validity_key = "rotated_polygons_valid" if polygons is not None else "rotated_boxes_valid"
                valid_oriented = target.get(validity_key, torch.ones(len(geometry), dtype=torch.bool, device=geometry.device))
                if (intersects & ~contained & valid_oriented).any():
                    raise ValueError("Explicit crop partially clips a rotated box; preserve its full region")
                if oriented is not None:
                    oriented[:, 0] -= left
                    oriented[:, 1] -= top
                if polygons is not None:
                    polygons -= polygons.new_tensor([left, top])
            image = TF.crop(image, top, left, crop_h, crop_w)
            if mask is not None:
                mask = TF.crop(mask, top, left, crop_h, crop_w)
            if boxes is not None:
                boxes = boxes - boxes.new_tensor([left, top, left, top])
                boxes[:, 0::2] = boxes[:, 0::2].clamp(0, crop_w)
                boxes[:, 1::2] = boxes[:, 1::2].clamp(0, crop_h)
                visible_after_crop = (boxes[:, 2] > boxes[:, 0]) & (boxes[:, 3] > boxes[:, 1])
            height, width = crop_h, crop_w
            receipt["crop"] = list(self.crop)
        if self.flip_horizontal and rng.random() < .5:
            image = TF.hflip(image)
            if mask is not None:
                mask = TF.hflip(mask)
            if boxes is not None:
                boxes[:, [0, 2]] = width - boxes[:, [2, 0]]
            if oriented is not None:
                oriented[:, 0] = width - oriented[:, 0]
                oriented[:, 4] = 180 - oriented[:, 4]
            if polygons is not None:
                polygons[:, :, 0] = width - polygons[:, :, 0]
            if directions is not None:
                directions = 180 - directions
            receipt["horizontal_flip"] = True
        if self.flip_vertical and rng.random() < .5:
            image = TF.vflip(image)
            if mask is not None:
                mask = TF.vflip(mask)
            if boxes is not None:
                boxes[:, [1, 3]] = height - boxes[:, [3, 1]]
            if oriented is not None:
                oriented[:, 1] = height - oriented[:, 1]
                oriented[:, 4] = -oriented[:, 4]
            if polygons is not None:
                polygons[:, :, 1] = height - polygons[:, :, 1]
            if directions is not None:
                directions = -directions
            receipt["vertical_flip"] = True
        if self.max_rotation_deg > 0:
            angle = rng.uniform(-self.max_rotation_deg, self.max_rotation_deg)
            image = TF.rotate(image, angle, interpolation=TF.InterpolationMode.BILINEAR)
            if mask is not None:
                mask = TF.rotate(mask.unsqueeze(0) if mask.ndim == 2 else mask,
                                 angle, interpolation=TF.InterpolationMode.NEAREST)
                if targets.ndim == 2:
                    mask = mask.squeeze(0)
            if boxes is not None:
                boxes = self._rotate_boxes(boxes, angle, width, height)
            if oriented is not None:
                radians = math.radians(-angle)
                x, y = oriented[:, 0].clone() - width / 2, oriented[:, 1].clone() - height / 2
                oriented[:, 0] = x * math.cos(radians) - y * math.sin(radians) + width / 2
                oriented[:, 1] = x * math.sin(radians) + y * math.cos(radians) + height / 2
                oriented[:, 4] -= angle
            if polygons is not None:
                radians = math.radians(-angle)
                x, y = polygons[:, :, 0].clone() - width / 2, polygons[:, :, 1].clone() - height / 2
                polygons[:, :, 0] = x * math.cos(radians) - y * math.sin(radians) + width / 2
                polygons[:, :, 1] = x * math.sin(radians) + y * math.cos(radians) + height / 2
            if directions is not None:
                directions = directions - angle
            receipt["rotation_deg"] = angle
        if is_detection:
            if oriented is not None:
                oriented[:, 4] = (oriented[:, 4] + 90).remainder(180) - 90
                envelope = self._oriented_envelopes(oriented)
                oriented_valid = target.get("rotated_boxes_valid", torch.ones(len(oriented), dtype=torch.bool, device=oriented.device))
                boxes = torch.where(oriented_valid[:, None], envelope, boxes)
                oriented[~oriented_valid] = 0
                target["rotated_boxes"] = oriented
            if directions is not None:
                target["direction_deg"] = directions.remainder(360)
            if polygons is not None:
                envelope = torch.cat((polygons.amin(1), polygons.amax(1)), dim=1)
                polygon_valid = target.get("rotated_polygons_valid", torch.ones(len(polygons), dtype=torch.bool, device=polygons.device))
                boxes = torch.where(polygon_valid[:, None], envelope, boxes)
                target["rotated_polygons"] = polygons
            if visible_after_crop is not None:
                boxes[~visible_after_crop] = 0
            boxes[~initially_valid] = 0
            target["boxes"] = boxes
            target = self._refresh_detection_target(target, width, height)
        elif mask is not None:
            target = mask
        image = self._apply_photometric(image, rng, generator, receipt)
        if task not in {"anomaly", "anomaly_detection"}:
            image = self._apply_cutout(image, rng, receipt)
        return AugmentedSample(image, target, receipt)

    def forward_classification(self, img):
        return self.augment_sample(img, task="classification").image

    def forward_detection(self, img, target):
        sample = self.augment_sample(img, target, task="detection")
        return sample.image, sample.targets

    def forward_segmentation(self, img, mask):
        sample = self.augment_sample(img, mask, task="segmentation")
        return sample.image, sample.targets

    def forward_anomaly(self, img):
        return self.augment_sample(img, task="anomaly").image

    def __call__(self, img, targets=None):
        sample = self.augment_sample(img, targets)
        return sample.image if targets is None else (sample.image, sample.targets)


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

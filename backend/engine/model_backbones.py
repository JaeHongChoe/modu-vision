"""Pretrained DINOv3 and YOLO adapters with portable state-dict reconstruction.

The public factory input is an RGB float tensor in [0, 1]. DINO normalizes
inside its frozen encoder. YOLO keeps this range and uses 1-based foreground
labels at the Studio boundary, converting only at the Ultralytics boundary.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import torch
from torch import nn
from torch.nn import functional as F

DINO_MODELS = {
    'dinov3_vits16': 'vit_small_patch16_dinov3.lvd1689m',
    'dinov3_vitb16': 'vit_base_patch16_dinov3.lvd1689m',
}
DINO_ALIASES = {
    'dinov3': 'dinov3_vits16', 'dinov3_small': 'dinov3_vits16',
    'dinov3_base': 'dinov3_vitb16',
    **{value: key for key, value in DINO_MODELS.items()},
}
YOLO_MODELS = ('yolo26n', 'yolo26s')
ADAPTER_VERSION = 1


def canonical_dino_name(name: str) -> str:
    name = name.strip().lower()
    return DINO_ALIASES.get(name, name)


def is_dino_backbone(name: str) -> bool:
    return canonical_dino_name(name) in DINO_MODELS


def checkpoint_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _verified_weights(path: str | Path, expected_sha256: str | None) -> tuple[Path, str]:
    weights = Path(path).expanduser()
    if not weights.is_file():
        raise FileNotFoundError(f'Pretrained checkpoint does not exist: {weights}')
    digest = checkpoint_sha256(weights)
    if expected_sha256 and digest != expected_sha256.strip().lower():
        raise ValueError('Pretrained checkpoint SHA256 hash does not match the requested weights')
    return weights, digest


def _dino_weights(name: str, checkpoint: str | None, expected_sha256: str | None) -> tuple[Path, str, str]:
    repo = f'timm/{DINO_MODELS[name]}'
    if checkpoint:
        path, digest = _verified_weights(checkpoint, expected_sha256)
        return path, digest, f'local:{Path(checkpoint).name}'
    try:
        from huggingface_hub import hf_hub_download
        path = hf_hub_download(repo_id=repo, filename='model.safetensors')
    except Exception as exc:
        raise RuntimeError(
            f'Authentic DINOv3 pretrained weights are unavailable for {repo}. '
            'Provide pretrained_checkpoint with the approved official checkpoint or grant access '
            'through Hugging Face. Random initialization is not a pretrained substitute.'
        ) from exc
    path, digest = _verified_weights(path, expected_sha256)
    return path, digest, f'hf://{repo}/model.safetensors'


def _yolo_weights(name: str, checkpoint: str | None, expected_sha256: str | None) -> tuple[Path, str, str]:
    """Resolve authentic local/official weights into an explicit file/hash receipt."""
    if name not in YOLO_MODELS:
        raise ValueError(f'Unsupported YOLO backbone: {name}')
    if checkpoint:
        path, digest = _verified_weights(checkpoint, expected_sha256)
        return path, digest, f'local:{Path(checkpoint).name}'
    try:
        from ultralytics.utils.downloads import attempt_download_asset
        cache_path = Path(torch.hub.get_dir()) / 'checkpoints' / f'{name}.pt'
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        path = attempt_download_asset(str(cache_path))
        path, digest = _verified_weights(path, expected_sha256)
    except Exception as exc:
        raise RuntimeError('Authentic YOLO pretrained checkpoint is unavailable; provide pretrained_checkpoint') from exc
    return path, digest, f'ultralytics:{name}.pt'


def model_metadata(model: nn.Module) -> dict[str, Any]:
    """Metadata saved alongside the full trained state, never substitute weights."""
    return dict(getattr(model, 'model_metadata', {}))


class DinoTaskModel(nn.Module):
    """A real timm DINOv3 encoder with a trainable linear or dense head."""

    def __init__(self, task: str, backbone: str, num_classes: int, pretrained: bool = True,
                 pretrained_checkpoint: str | None = None, pretrained_sha256: str | None = None):
        super().__init__()
        if task not in ('classification', 'segmentation'):
            raise ValueError(f'Unsupported DINOv3 task: {task}')
        name = canonical_dino_name(backbone)
        if name not in DINO_MODELS:
            raise ValueError(f'Unsupported DINOv3 backbone: {backbone}')
        if num_classes < 1:
            raise ValueError('num_classes must be positive')
        receipt = _dino_weights(name, pretrained_checkpoint, pretrained_sha256) if pretrained else None
        try:
            import timm
            from timm.models import load_checkpoint
        except ImportError as exc:
            raise RuntimeError('DINOv3 requires timm>=1.0.24 and safetensors') from exc
        # No pretrained=True call here: explicit verified file loading avoids downloads
        # during reconstruction and makes the source hash exact.
        self.encoder = timm.create_model(DINO_MODELS[name], pretrained=False, num_classes=0,
                                         dynamic_img_size=True)
        if receipt:
            load_checkpoint(self.encoder, str(receipt[0]), strict=True)
        self.encoder.requires_grad_(False)
        self.encoder.eval()
        self.task = task
        self.backbone = name
        self.num_classes = num_classes
        self.patch_size = int(self.encoder.patch_embed.patch_size[0])
        self.num_prefix_tokens = int(self.encoder.num_prefix_tokens)
        channels = int(self.encoder.num_features)
        if task == 'classification':
            self.head = nn.Linear(channels, num_classes)
        else:
            self.head = nn.Sequential(nn.Conv2d(channels, 128, 3, padding=1), nn.GELU(),
                                      nn.Conv2d(128, num_classes, 1))
        self.register_buffer('input_mean', torch.tensor(self.encoder.default_cfg['mean']).reshape(1, 3, 1, 1))
        self.register_buffer('input_std', torch.tensor(self.encoder.default_cfg['std']).reshape(1, 3, 1, 1))
        # Convolutional GradCAM is not valid for frozen transformer tokens.
        self.target_gradcam_layer = None
        self.model_metadata = {
            'backbone': name, 'model_name': name, 'architecture': f'{task}:{name}',
            'encoder_architecture': DINO_MODELS[name], 'encoder_frozen': True,
            'adapter_version': ADAPTER_VERSION, 'num_classes': num_classes,
            'pretrained': bool(pretrained), 'pretrained_source': receipt[2] if receipt else None,
            'pretrained_sha256': receipt[1] if receipt else None,
            'input_normalization': 'rgb_0_1_to_imagenet',
        }

    def train(self, mode: bool = True):
        super().train(mode)
        self.encoder.eval()
        return self

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        height, width = x.shape[-2:]
        # Pad only the right/bottom edge; spatial outputs are cropped/resized to
        # the input dimensions and include every original pixel.
        x = (x - self.input_mean) / self.input_std
        pad_h = (self.patch_size - height % self.patch_size) % self.patch_size
        pad_w = (self.patch_size - width % self.patch_size) % self.patch_size
        x = F.pad(x, (0, pad_w, 0, pad_h))
        with torch.no_grad():
            tokens = self.encoder.forward_features(x)
        if self.task == 'classification':
            return self.head(tokens[:, 0])
        grid_h, grid_w = x.shape[-2] // self.patch_size, x.shape[-1] // self.patch_size
        patches = tokens[:, self.num_prefix_tokens:].transpose(1, 2).reshape(x.shape[0], -1, grid_h, grid_w).contiguous()
        logits = self.head(patches)
        logits = F.interpolate(logits, size=x.shape[-2:], mode='bilinear', align_corners=False)
        return logits[..., :height, :width]


class YoloDetectionAdapter(nn.Module):
    """Ultralytics detector adapted to the Studio loss and prediction contract.

    num_classes includes Studio background (0); native YOLO channels contain
    foreground classes only. Batches are padded to stride, with boxes returned
    in each input image's original coordinates.
    """

    def __init__(self, backbone: str = 'yolo26n', num_classes: int = 2, pretrained: bool = True,
                 pretrained_checkpoint: str | None = None, pretrained_sha256: str | None = None):
        super().__init__()
        backbone = backbone.strip().lower().removesuffix('.pt').removesuffix('.yaml')
        if backbone not in YOLO_MODELS:
            raise ValueError(f'Unsupported YOLO backbone: {backbone}')
        if num_classes < 2:
            raise ValueError('YOLO requires at least one foreground class plus background')
        # Check explicit file/hash before constructing any randomly initialized graph.
        receipt = _yolo_weights(backbone, pretrained_checkpoint, pretrained_sha256) if pretrained else None
        try:
            from ultralytics.nn.tasks import DetectionModel, torch_safe_load
            from ultralytics.utils import DEFAULT_CFG_DICT
        except ImportError as exc:
            raise RuntimeError('YOLO26 requires ultralytics>=8.4.41') from exc
        self.detector = DetectionModel(cfg=f'{backbone}.yaml', nc=num_classes - 1, verbose=False)
        self.detector.args = SimpleNamespace(**DEFAULT_CFG_DICT)
        if receipt:
            payload, _ = torch_safe_load(str(receipt[0]))
            source = payload.get('ema') or payload.get('model')
            if not isinstance(source, nn.Module):
                raise ValueError('Pretrained YOLO checkpoint has no trained detector model')
            scale = source.yaml.get('scale')
            if scale and scale != backbone[-1]:
                raise ValueError('Pretrained YOLO checkpoint architecture does not match requested backbone')
            source_state = source.float().state_dict()
            target_state = self.detector.state_dict()
            # All encoder keys must match. A changed foreground head is intentionally
            # trained afresh, while the real pretrained feature extractor is retained.
            head_prefix = f'model.{len(self.detector.model) - 1}.'
            encoder_keys = [key for key in target_state if not key.startswith(head_prefix)]
            if any(key not in source_state or source_state[key].shape != target_state[key].shape for key in encoder_keys):
                raise ValueError('Pretrained YOLO checkpoint encoder does not match requested architecture')
            self.detector.load(source, verbose=False)
        self.num_classes = num_classes
        self.backbone = backbone
        self.register_buffer('_foreground_count', torch.tensor(num_classes - 1, dtype=torch.long))
        self.stride = int(self.detector.stride.max().item())
        self.model_metadata = {
            'backbone': backbone, 'model_name': backbone, 'architecture': f'detection:{backbone}',
            'adapter_version': ADAPTER_VERSION, 'num_classes': num_classes,
            'pretrained': bool(pretrained), 'pretrained_source': receipt[2] if receipt else None,
            'pretrained_sha256': receipt[1] if receipt else None,
            'input_normalization': 'rgb_0_1', 'foreground_label_offset': 1,
        }

    def prepare_batch(self, images: torch.Tensor | Sequence[torch.Tensor],
                      targets: Sequence[Mapping[str, torch.Tensor]] | None = None):
        image_list = list(images) if isinstance(images, torch.Tensor) else list(images)
        if not image_list:
            raise ValueError('Detection requires at least one image')
        shapes = [(image.shape[-2], image.shape[-1]) for image in image_list]
        height = math.ceil(max(shape[0] for shape in shapes) / self.stride) * self.stride
        width = math.ceil(max(shape[1] for shape in shapes) / self.stride) * self.stride
        padded = [F.pad(image, (0, width - image.shape[-1], 0, height - image.shape[-2]), value=114 / 255)
                  for image in image_list]
        batch = {'img': torch.stack(padded)}
        if targets is not None:
            if len(targets) != len(image_list):
                raise ValueError('Detection image and target counts differ')
            classes, boxes, batch_indexes = [], [], []
            for index, target in enumerate(targets):
                xyxy = target['boxes'].to(device=batch['img'].device, dtype=torch.float32).clone()
                labels = target['labels'].to(device=batch['img'].device, dtype=torch.long)
                if xyxy.ndim != 2 or xyxy.shape[-1] != 4 or len(xyxy) != len(labels):
                    raise ValueError('Detection requires matching xyxy boxes and foreground labels')
                if ((labels < 1) | (labels >= self.num_classes)).any():
                    raise ValueError('Detection target labels must be foreground indices 1..K')
                if not torch.isfinite(xyxy).all():
                    raise ValueError('Detection target boxes must be finite')
                h, w = shapes[index]
                xyxy[:, [0, 2]] = xyxy[:, [0, 2]].clamp(0, w)
                xyxy[:, [1, 3]] = xyxy[:, [1, 3]].clamp(0, h)
                if ((xyxy[:, 2:] - xyxy[:, :2]) <= 0).any():
                    raise ValueError('Detection target boxes must have positive width and height')
                xywh = torch.cat(((xyxy[:, :2] + xyxy[:, 2:]) / 2, xyxy[:, 2:] - xyxy[:, :2]), dim=1)
                xywh = xywh / xywh.new_tensor([width, height, width, height])
                classes.append((labels - 1).float().unsqueeze(1))
                boxes.append(xywh)
                batch_indexes.append(torch.full((len(labels),), index, device=xyxy.device, dtype=torch.float32))
            batch.update(cls=torch.cat(classes), bboxes=torch.cat(boxes), batch_idx=torch.cat(batch_indexes))
        return batch, shapes

    def forward(self, images: torch.Tensor | Sequence[torch.Tensor],
                targets: Sequence[Mapping[str, torch.Tensor]] | None = None):
        batch, shapes = self.prepare_batch(images, targets)
        if self.training:
            if targets is None:
                raise ValueError('Detection training requires targets')
            losses, _ = self.detector(batch)
            return {'loss_yolo': losses.sum()}
        from ultralytics.utils.nms import non_max_suppression
        predictions = self.detector(batch['img'])
        predictions = predictions[0] if isinstance(predictions, tuple) else predictions
        detections = non_max_suppression(predictions, conf_thres=0.001, iou_thres=0.7,
                                        max_det=300, nc=self.num_classes - 1,
                                        end2end=bool(self.detector.end2end))
        output = []
        for detected, (height, width) in zip(detections, shapes):
            boxes = detected[:, :4].clone()
            boxes[:, [0, 2]] = boxes[:, [0, 2]].clamp(0, width)
            boxes[:, [1, 3]] = boxes[:, [1, 3]].clamp(0, height)
            valid = ((boxes[:, 2:] - boxes[:, :2]) > 0).all(dim=1)
            output.append({'boxes': boxes[valid], 'scores': detected[valid, 4],
                           'labels': detected[valid, 5].long() + 1})
        return output

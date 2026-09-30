"""Normal-only synthetic-defect DINOv3 probe with native patch score inference.

The spatial output is an overlapping patch probability map, not a pixel mask.
Only procedural training examples have masks; real defects are never used by fit.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
from PIL import Image
import torch
from torch import nn
from torch.nn import functional as F

from backend.engine.anomaly.cancellation import check_fit_cancelled
from backend.engine.anomaly.synthetic_defects import SYNTHETIC_FAMILIES, synthesize_defect
from backend.engine.model_backbones import DinoTaskModel, canonical_dino_name, checkpoint_sha256


class DinoSyntheticDetector:
    """Frozen verified DINO encoder, CLS+mean-token MLP and binary synthetic training."""

    MAX_SOURCE_PIXELS = 100_000_000
    MAX_NATIVE_PATCHES = 1_000_000

    def __init__(self, backbone_name='dinov3_vits16', device=None, pretrained=True,
                 pretrained_checkpoint=None, pretrained_sha256=None, patch_size=256,
                 stride=128, epochs=12, batch_size=16, learning_rate=3e-4,
                 patches_per_image=8, seed=17, inference_batch_size=32):
        self.backbone_name = canonical_dino_name(backbone_name)
        self.device = torch.device(device or 'cpu')
        config = dict(patch_size=patch_size, stride=stride, epochs=epochs, batch_size=batch_size,
                      learning_rate=learning_rate, patches_per_image=patches_per_image,
                      seed=seed, inference_batch_size=inference_batch_size)
        self._validate_config(config)
        self._set_config(config)
        # Initialize a deterministic head without changing the caller's RNG.
        with torch.random.fork_rng(devices=[]):
            torch.manual_seed(self.seed)
            self.model = DinoTaskModel('classification', self.backbone_name, 1,
                                      pretrained=pretrained,
                                      pretrained_checkpoint=pretrained_checkpoint,
                                      pretrained_sha256=pretrained_sha256)
            width = int(self.model.encoder.num_features)
            self.model.head = nn.Sequential(nn.LayerNorm(width * 2), nn.Linear(width * 2, width),
                                            nn.GELU(), nn.Dropout(0.1), nn.Linear(width, 1))
        self.feature_extractor = self.model.encoder
        self.head = self.model.head
        self.feature_extractor.requires_grad_(False)
        self.model_metadata = dict(self.model.model_metadata)
        self.threshold = 0.5
        self.calibration = {'method': 'uncalibrated', 'comparison': '>', 'target_image_fpr': 0.01}
        self.training_summary: dict[str, Any] = {}
        self._fitted = False
        self.placement_mask_provider: Callable[[Path, np.ndarray], np.ndarray | None] | None = None
        self._update_metadata()
        self.to(self.device).eval()

    @staticmethod
    def _validate_config(config):
        for name in ('patch_size', 'stride', 'epochs', 'batch_size', 'patches_per_image', 'inference_batch_size'):
            value = config.get(name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f'DINO synthetic {name} must be a positive integer')
        if not 32 <= config['patch_size'] <= 1024 or config['patch_size'] % 16:
            raise ValueError('DINO synthetic patch_size must be 32..1024 and a multiple of 16')
        if config['stride'] > config['patch_size']:
            raise ValueError('DINO synthetic stride must not exceed patch_size')
        if config['patches_per_image'] > 128 or config['inference_batch_size'] > 128:
            raise ValueError('DINO synthetic patches_per_image and inference_batch_size must be at most 128')
        if isinstance(config.get('seed'), bool) or not isinstance(config.get('seed'), int) or config['seed'] < 0:
            raise ValueError('DINO synthetic seed must be a nonnegative integer')
        if not math.isfinite(float(config.get('learning_rate', 0))) or config['learning_rate'] <= 0:
            raise ValueError('DINO synthetic learning_rate must be positive and finite')

    def _set_config(self, config):
        for name, value in config.items():
            setattr(self, name, value)

    def _config(self):
        return {name: getattr(self, name) for name in ('patch_size', 'stride', 'epochs', 'batch_size',
                'learning_rate', 'patches_per_image', 'seed', 'inference_batch_size')}

    def _update_metadata(self):
        self.model_metadata.update({
            'detector_type': 'dino_synthetic', 'feature_backbone': self.backbone_name,
            'backbone_name': self.backbone_name, 'backbone': self.backbone_name,
            'anomaly_backbone': self.backbone_name, 'encoder_frozen': True, 'head_version': 1,
            'architecture': f'anomaly:dino_synthetic:{self.backbone_name}:p{self.patch_size}:s{self.stride}:head_v1',
            'patch_size': self.patch_size, 'stride': self.stride, 'map_semantics': 'patch_score',
            'input_normalization': 'rgb_0_1_to_imagenet', 'threshold_comparison': '>',
            'feature_pooling': 'cls_plus_spatial_mean', 'head_type': 'layernorm_mlp_binary',
            'synthetic_families': list(SYNTHETIC_FAMILIES),
        })

    def to(self, device):
        self.device = torch.device(device)
        self.model.to(self.device)
        return self

    def eval(self):
        self.model.eval()
        self.feature_extractor.eval()
        return self

    @property
    def fitted(self):
        """A trained head is required for inference; random initialization is insufficient."""
        return self._fitted

    def _logits(self, rgb):
        normalized = (rgb - self.model.input_mean) / self.model.input_std
        token_patch = int(self.feature_extractor.patch_embed.patch_size[0])
        height, width = normalized.shape[-2:]
        normalized = F.pad(normalized, (0, (-width) % token_patch, 0, (-height) % token_patch))
        self.feature_extractor.eval()
        with torch.no_grad():
            tokens = self.feature_extractor.forward_features(normalized)
            patches = tokens[:, self.model.num_prefix_tokens:]
            if patches.shape[1] == 0:
                raise ValueError('DINO encoder returned no spatial patch tokens')
            pooled = torch.cat((tokens[:, 0], patches.mean(1)), 1)
        return self.head(pooled).squeeze(-1)

    @staticmethod
    def _normal_sources(dataset, purpose, allow_empty=False):
        def source_samples(value):
            if isinstance(value, torch.utils.data.Subset):
                base = source_samples(value.dataset)
                return [base[index] for index in value.indices] if base is not None else None
            if isinstance(value, torch.utils.data.ConcatDataset):
                groups = [source_samples(part) for part in value.datasets]
                return [sample for group in groups for sample in group] if all(group is not None for group in groups) else None
            samples = getattr(value, 'samples', None)
            if samples is not None:
                return samples
            wrapped = getattr(value, 'dataset', None)
            return source_samples(wrapped) if wrapped is not None else None

        samples = source_samples(dataset)
        if samples is None or (not len(samples) and not allow_empty):
            raise ValueError(f'{purpose} requires native source samples (path, label, mask)')
        sources = []
        for sample in samples:
            if not isinstance(sample, (tuple, list)) or len(sample) < 2:
                raise ValueError(f'{purpose} source samples must contain a path and label')
            label = sample[1]
            if isinstance(label, torch.Tensor):
                label = label.item() if label.numel() == 1 else None
            if label != 0:
                raise ValueError(f'{purpose} accepts only label 0 normal images')
            path = Path(sample[0]).expanduser().resolve()
            if not path.is_file():
                raise ValueError(f'{purpose} native source image is missing')
            sources.append(path)
        if len(set(sources)) != len(sources):
            raise ValueError(f'{purpose} source images must be unique for equal per-source coverage')
        return sorted(sources, key=str)

    @staticmethod
    def _read_rgb(path, expected_sha256=None):
        # Hash and decode the same bytes. Reopening after hashing would permit a
        # source replacement between verification and training/calibration.
        encoded = Path(path).read_bytes()
        if expected_sha256 is not None and hashlib.sha256(encoded).hexdigest() != expected_sha256:
            raise ValueError('Native source image changed after its data digest was pinned')
        with Image.open(io.BytesIO(encoded)) as source:
            return np.asarray(source.convert('RGB'), dtype=np.uint8).copy()

    @staticmethod
    def _tensor(rgb):
        return torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float().div_(255)

    def fit(self, dataloader, cancellation_requested=None, progress_callback=None, calibration_dataset=None):
        """Train balanced procedural pairs from every native normal source each epoch.

        The loader's resized image tensors are intentionally never consumed.
        Optional calibration uses only independent held-out normal image maxima.
        """
        check_fit_cancelled(cancellation_requested)
        sources = self._normal_sources(dataloader, 'Training')
        heldout = self._normal_sources(calibration_dataset, 'Calibration', allow_empty=True) if calibration_dataset is not None else []
        if set(sources) & set(heldout):
            raise ValueError('Calibration must use held-out normal images; training paths overlap')
        hashes = []
        for path in sources:
            check_fit_cancelled(cancellation_requested)
            with Image.open(path) as image:
                if min(image.size) < self.patch_size:
                    raise ValueError('Native training image is smaller than patch_size; whole-image resizing is prohibited')
                if image.width * image.height > self.MAX_SOURCE_PIXELS:
                    raise ValueError('Native training image exceeds the 100 megapixel work budget')
            hashes.append(checkpoint_sha256(path))
        training_hashes = dict(zip(sources, hashes))
        heldout_hashes = []
        if heldout:
            for path in heldout:
                check_fit_cancelled(cancellation_requested)
                heldout_hashes.append(checkpoint_sha256(path))
            if set(hashes) & set(heldout_hashes):
                raise ValueError('Calibration must use held-out normal images; training content overlaps')
        data_digest = hashlib.sha256('\n'.join(hashes).encode()).hexdigest()
        calibration_digest = hashlib.sha256('\n'.join(heldout_hashes).encode()).hexdigest() if heldout else None
        config_digest = hashlib.sha256(json.dumps(self._config(), sort_keys=True).encode()).hexdigest()
        optimizer = torch.optim.AdamW(self.head.parameters(), lr=self.learning_rate, weight_decay=1e-4)
        scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lambda epoch:
            0.05 + 0.95 * 0.5 * (1 + math.cos(math.pi * min(epoch, self.epochs) / self.epochs)))
        summary = {'source_image_count': len(sources), 'normal_patch_count': 0, 'synthetic_patch_count': 0,
                   'synthetic_family_counts': {family: 0 for family in SYNTHETIC_FAMILIES},
                   'patches_per_source_per_epoch': self.patches_per_image,
                   'epoch_history': [], 'data_digest': data_digest, 'calibration_data_digest': calibration_digest,
                   'config_digest': config_digest,
                   'map_semantics': 'patch_score', 'training_labels': [0]}
        self._fitted = False
        self.threshold = 0.5
        self.calibration = {'method': 'uncalibrated', 'comparison': '>', 'target_image_fpr': 0.01}
        for epoch in range(self.epochs):
            check_fit_cancelled(cancellation_requested)
            self.model.train()
            self.feature_extractor.eval()
            pending, targets = [], []
            total_loss = 0.0
            example_count = 0
            plan = hashlib.sha256()
            cuda_devices = [self.device.index or 0] if self.device.type == 'cuda' else []
            with torch.random.fork_rng(devices=cuda_devices):
                torch.manual_seed(self.seed + epoch)

                def flush():
                    nonlocal total_loss, example_count
                    if not pending:
                        return
                    check_fit_cancelled(cancellation_requested)
                    batch = torch.stack(pending).to(self.device)
                    labels = torch.tensor(targets, dtype=torch.float32, device=self.device)
                    optimizer.zero_grad(set_to_none=True)
                    with torch.autocast(device_type=self.device.type, dtype=torch.bfloat16,
                                        enabled=self.device.type == 'cuda'):
                        loss = F.binary_cross_entropy_with_logits(self._logits(batch), labels)
                    if not torch.isfinite(loss):
                        raise ValueError('DINO synthetic training produced nonfinite loss')
                    loss.backward()
                    optimizer.step()
                    total_loss += float(loss.detach()) * len(pending)
                    example_count += len(pending)
                    pending.clear(); targets.clear()
                    check_fit_cancelled(cancellation_requested)

                for source_index, path in enumerate(sources):
                    check_fit_cancelled(cancellation_requested)
                    rgb = self._read_rgb(path, training_hashes[path])
                    placement = self.placement_mask_provider(path, rgb) if self.placement_mask_provider else None
                    if placement is not None and np.shape(placement) != rgb.shape[:2]:
                        raise ValueError('Synthetic placement mask must match the native source image')
                    for patch_index in range(self.patches_per_image):
                        check_fit_cancelled(cancellation_requested)
                        rng = np.random.default_rng(np.random.SeedSequence([self.seed, epoch, source_index, patch_index]))
                        y = int(rng.integers(0, rgb.shape[0] - self.patch_size + 1))
                        x = int(rng.integers(0, rgb.shape[1] - self.patch_size + 1))
                        crop = rgb[y:y + self.patch_size, x:x + self.patch_size].copy()
                        allowed = np.asarray(placement)[y:y + self.patch_size, x:x + self.patch_size] if placement is not None else None
                        # When placement is configured, sample a crop containing it.
                        if allowed is not None and not np.any(allowed):
                            active = np.argwhere(np.asarray(placement, dtype=bool))
                            if not len(active):
                                raise ValueError('Synthetic placement mask is empty')
                            cy, cx = active[int(rng.integers(len(active)))]
                            y = int(np.clip(cy - rng.integers(self.patch_size), 0, rgb.shape[0] - self.patch_size))
                            x = int(np.clip(cx - rng.integers(self.patch_size), 0, rgb.shape[1] - self.patch_size))
                            crop = rgb[y:y + self.patch_size, x:x + self.patch_size].copy()
                            allowed = np.asarray(placement)[y:y + self.patch_size, x:x + self.patch_size]
                        synthetic, mask, family = synthesize_defect(crop, rng, placement_mask=allowed)
                        plan.update(crop.tobytes()); plan.update(mask.tobytes()); plan.update(synthetic.tobytes())
                        summary['normal_patch_count'] += 1
                        summary['synthetic_patch_count'] += 1
                        summary['synthetic_family_counts'][family] += 1
                        for patch, label in ((crop, 0), (synthetic, 1)):
                            pending.append(self._tensor(patch)); targets.append(label)
                            if len(pending) >= self.batch_size:
                                flush()
                flush()
            train_loss = total_loss / example_count
            row = {'epoch': epoch + 1, 'train_loss': train_loss, 'sample_plan_sha256': plan.hexdigest(),
                   'source_coverage': len(sources), 'normal_patch_count': len(sources) * self.patches_per_image,
                   'synthetic_patch_count': len(sources) * self.patches_per_image}
            summary['epoch_history'].append(row)
            if progress_callback:
                progress_callback(epoch + 1, self.epochs, train_loss, None, float(optimizer.param_groups[0]['lr']),
                                  {'normal_patch_count': row['normal_patch_count'],
                                   'synthetic_patch_count': row['synthetic_patch_count'],
                                   'source_coverage': len(sources)})
            scheduler.step()
        check_fit_cancelled(cancellation_requested)
        self._fitted = True
        self.eval()
        if heldout:
            try:
                normal_scores = []
                for path, expected_hash in zip(heldout, heldout_hashes):
                    check_fit_cancelled(cancellation_requested)
                    _, scores = self._native_maps(self._tensor(self._read_rgb(path, expected_hash)).unsqueeze(0), cancellation_requested)
                    normal_scores.append(float(scores[0]))
                ordered = sorted(normal_scores)
                index = min(len(ordered) - 1, max(0, math.ceil(0.99 * len(ordered)) - 1))
                self.threshold = ordered[index]
                self.calibration = {'method': 'heldout_normal_empirical_max_image_score', 'comparison': '>',
                                    'target_image_fpr': 0.01, 'normal_image_count': len(ordered),
                                    'observed_image_fpr': sum(score > self.threshold for score in ordered) / len(ordered)}
            except Exception:
                self._fitted = False
                raise
        summary['calibrated_threshold'] = self.threshold
        summary['calibration'] = dict(self.calibration)
        self.training_summary = summary
        self._update_metadata()
        return summary

    @staticmethod
    def _axis_positions(length, patch_size, stride):
        if length <= patch_size:
            return [0]
        positions = list(range(0, length - patch_size + 1, stride))
        if positions[-1] != length - patch_size:
            positions.append(length - patch_size)
        return positions

    @staticmethod
    def _axis_count(length, patch_size, stride):
        if length <= patch_size:
            return 1
        distance = length - patch_size
        return distance // stride + 1 + int(distance % stride != 0)

    def _native_maps(self, x, cancellation_requested=None):
        if not self._fitted:
            raise RuntimeError('DINO synthetic detector must be fitted or loaded from a trained checkpoint')
        if x.ndim == 3:
            x = x.unsqueeze(0)
        if x.ndim != 4 or x.shape[1] != 3 or not x.shape[0] or min(x.shape[-2:]) < 1:
            raise ValueError('DINO synthetic inference requires RGB [B,3,H,W] tensors')
        height, width = x.shape[-2:]
        patch_count = self._axis_count(height, self.patch_size, self.stride) * self._axis_count(width, self.patch_size, self.stride)
        if height * width * x.shape[0] > self.MAX_SOURCE_PIXELS or patch_count * x.shape[0] > self.MAX_NATIVE_PATCHES:
            raise ValueError('DINO synthetic native inference exceeds the 100 megapixel or 1 million patch work budget')
        if not x.is_floating_point() or not torch.isfinite(x).all() or x.min() < 0 or x.max() > 1:
            raise ValueError('DINO synthetic inference requires finite RGB floats in [0,1]')
        self.eval()
        maps, image_scores = [], []
        coordinates = [(y, xx) for y in self._axis_positions(height, self.patch_size, self.stride)
                       for xx in self._axis_positions(width, self.patch_size, self.stride)]
        with torch.inference_mode():
            for image in x:
                # The source and score canvas can stay on CPU; only bounded crop
                # batches occupy the accelerator. Returned maps/scores are CPU tensors.
                canvas = torch.zeros(height, width, dtype=torch.float32)
                maximum = torch.zeros((), dtype=torch.float32)
                for start in range(0, len(coordinates), self.inference_batch_size):
                    check_fit_cancelled(cancellation_requested)
                    positions = coordinates[start:start + self.inference_batch_size]
                    crops = []
                    for y, xx in positions:
                        crop = image[:, y:y + self.patch_size, xx:xx + self.patch_size]
                        crop = F.pad(crop, (0, self.patch_size - crop.shape[-1],
                                           0, self.patch_size - crop.shape[-2]), mode='replicate')
                        crops.append(crop)
                    probabilities = torch.sigmoid(self._logits(torch.stack(crops).to(self.device))).float().cpu()
                    maximum = torch.maximum(maximum, probabilities.max())
                    for (y, xx), probability in zip(positions, probabilities):
                        region = canvas[y:min(y + self.patch_size, height), xx:min(xx + self.patch_size, width)]
                        region.copy_(torch.maximum(region, probability))
                    check_fit_cancelled(cancellation_requested)
                maps.append(canvas); image_scores.append(maximum)
        return torch.stack(maps), torch.stack(image_scores)

    def __call__(self, x):
        return self._native_maps(x)

    def predict_anomaly_map(self, image_tensor, out_size=None):
        if image_tensor.ndim == 4 and image_tensor.shape[0] != 1:
            raise ValueError('predict_anomaly_map accepts one image')
        maps, scores = self(image_tensor)
        if out_size is not None:
            maps = F.interpolate(maps.unsqueeze(1), size=out_size, mode='bilinear', align_corners=False).squeeze(1)
        return maps[0].detach().float().cpu().numpy(), float(scores[0])

    def state_dict(self):
        cpu_state = lambda module: {name: value.detach().cpu().clone() for name, value in module.state_dict().items()}
        return {'format_version': 1, 'detector_type': 'dino_synthetic', 'backbone_name': self.backbone_name,
                'feature_extractor_state_dict': cpu_state(self.feature_extractor), 'head_state_dict': cpu_state(self.head),
                'input_mean': self.model.input_mean.detach().cpu().clone(),
                'input_std': self.model.input_std.detach().cpu().clone(), 'config': self._config(),
                'threshold': self.threshold, 'calibration': dict(self.calibration),
                'training_summary': self.training_summary, 'model_metadata': dict(self.model_metadata), 'fitted': self._fitted}

    def load_state_dict(self, state):
        if not isinstance(state, Mapping) or state.get('format_version') != 1 or state.get('detector_type') != 'dino_synthetic':
            raise ValueError('Unsupported DINO synthetic state format')
        if canonical_dino_name(state.get('backbone_name', '')) != self.backbone_name:
            raise ValueError('DINO synthetic checkpoint backbone differs from the constructor')
        config = state.get('config')
        if not isinstance(config, dict) or set(config) != set(self._config()):
            raise ValueError('DINO synthetic checkpoint configuration is incomplete')
        self._validate_config(config)
        for module, key in ((self.feature_extractor, 'feature_extractor_state_dict'), (self.head, 'head_state_dict')):
            current, saved = module.state_dict(), state.get(key)
            if not isinstance(saved, Mapping) or set(current) != set(saved) or any(
                not isinstance(saved[name], torch.Tensor) or saved[name].shape != value.shape or saved[name].dtype != value.dtype
                for name, value in current.items()):
                raise ValueError('DINO synthetic checkpoint encoder or head tensors are incompatible')
        for name in ('input_mean', 'input_std'):
            tensor = state.get(name)
            expected = getattr(self.model, name)
            if not isinstance(tensor, torch.Tensor) or tensor.shape != expected.shape or tensor.dtype != expected.dtype or not torch.isfinite(tensor).all():
                raise ValueError('DINO synthetic checkpoint normalization is invalid')
        if (state['input_std'] <= 0).any():
            raise ValueError('DINO synthetic checkpoint normalization is invalid')
        threshold = float(state.get('threshold', float('nan')))
        if not math.isfinite(threshold) or not 0 <= threshold <= 1:
            raise ValueError('DINO synthetic checkpoint threshold is invalid')
        if not all(isinstance(state.get(key), Mapping) for key in ('calibration', 'training_summary', 'model_metadata')):
            raise ValueError('DINO synthetic checkpoint metadata is incomplete')
        if not isinstance(state.get('fitted'), bool) or state['calibration'].get('comparison') != '>':
            raise ValueError('DINO synthetic checkpoint calibration or fitted status is invalid')
        self.feature_extractor.load_state_dict(state['feature_extractor_state_dict'], strict=True)
        self.head.load_state_dict(state['head_state_dict'], strict=True)
        self.model.input_mean.copy_(state['input_mean'].to(self.device))
        self.model.input_std.copy_(state['input_std'].to(self.device))
        self._set_config(config)
        self.threshold = threshold
        self.calibration = dict(state.get('calibration', {}))
        self.training_summary = dict(state.get('training_summary', {}))
        self.model_metadata = dict(state.get('model_metadata', {}))
        self._fitted = bool(state.get('fitted', False))
        self._update_metadata()
        self.eval()

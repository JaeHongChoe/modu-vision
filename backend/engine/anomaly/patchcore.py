"""
backend/engine/anomaly/patchcore.py

PatchCore: High-Precision Memory Bank Anomaly Detection with Greedy Coreset Subsampling.
Uses locally aware patch features and kNN search against normal feature manifold.
Strictly trained on normal (OK) images (0 defects in train set).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.engine.anomaly.feature_extractor import ResNetFeatureExtractor
from backend.engine.anomaly.cancellation import check_fit_cancelled

logger = logging.getLogger("vision_ai_studio.anomaly.patchcore")


class PatchCoreDetector:
    """
    PatchCore Anomaly Detection Engine.
    Strictly trained on normal (OK) images (0 defects).
    """

    def __init__(
        self,
        backbone_name: str = "resnet18",
        coreset_sampling_ratio: float = 0.05,
        max_coreset_size: int = 2000,
        seed: int = 42,
        device: Optional[Union[torch.device, str]] = None,
        pretrained: bool = True,
    ):
        self.backbone_name = backbone_name
        self.coreset_sampling_ratio = coreset_sampling_ratio
        self.max_coreset_size = max_coreset_size
        self.seed = seed
        self.device = torch.device(device) if device else torch.device("cpu")

        self.feature_extractor = ResNetFeatureExtractor(
            backbone_name=backbone_name,
            pretrained=pretrained,
            local_avg_pool=True,
        ).to(self.device)

        self.coreset: Optional[torch.Tensor] = None  # [K, D]
        self.threshold: float = 0.0
        self.score_spec = None

    def to(self, device: Union[torch.device, str]) -> "PatchCoreDetector":
        self.device = torch.device(device)
        self.feature_extractor = self.feature_extractor.to(self.device)
        if self.coreset is not None:
            self.coreset = self.coreset.to(self.device)
        return self

    def eval(self) -> "PatchCoreDetector":
        self.feature_extractor.eval()
        return self

    def fit(
        self, dataloader: torch.utils.data.DataLoader,
        cancellation_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        """
        Collects normal patch embeddings and builds coreset memory bank.
        """
        check_fit_cancelled(cancellation_requested)
        self.feature_extractor.eval()
        all_patches: List[torch.Tensor] = []

        with torch.no_grad():
            for batch in dataloader:
                check_fit_cancelled(cancellation_requested)
                images = batch[0] if isinstance(batch, (list, tuple)) else batch
                images = images.to(self.device)
                feats = self.feature_extractor(images)  # [B, D, H, W]
                B, D, H, W = feats.shape
                # Reshape to [B * H * W, D]
                patches = feats.permute(0, 2, 3, 1).reshape(-1, D)
                all_patches.append(patches.cpu())
                check_fit_cancelled(cancellation_requested)

        check_fit_cancelled(cancellation_requested)
        raw_memory = torch.cat(all_patches, dim=0).to(self.device)
        total_patches, embed_dim = raw_memory.shape

        # Calculate target coreset size
        target_k = max(50, int(total_patches * self.coreset_sampling_ratio))
        target_k = min(target_k, self.max_coreset_size, total_patches)

        logger.info("Running greedy coreset subsampling: %d -> %d patches", total_patches, target_k)

        # Min-Max Greedy Coreset with random projection distance acceleration
        torch.manual_seed(self.seed)
        proj_dim = min(128, embed_dim)
        proj = torch.randn(embed_dim, proj_dim, device=self.device) / (proj_dim ** 0.5)
        mem_proj = torch.matmul(raw_memory, proj)
        check_fit_cancelled(cancellation_requested)

        selected_indices = [0]
        dists = torch.norm(mem_proj - mem_proj[0], dim=1)

        for _ in range(1, target_k):
            check_fit_cancelled(cancellation_requested)
            new_idx = int(torch.argmax(dists).item())
            selected_indices.append(new_idx)
            new_dists = torch.norm(mem_proj - mem_proj[new_idx], dim=1)
            dists = torch.minimum(dists, new_dists)

        check_fit_cancelled(cancellation_requested)
        self.coreset = raw_memory[selected_indices]

        # Calibrate threshold
        train_scores = self.predict_scores(dataloader, cancellation_requested=cancellation_requested)
        check_fit_cancelled(cancellation_requested)
        mean_s = float(np.mean(train_scores))
        std_s = float(np.std(train_scores))
        self.threshold = round(mean_s + 3.0 * std_s, 4)
        self.score_spec = None
        self.state_dict()  # Persist a calibration identity from these fitted statistics.

        return {
            "total_normal_patches": total_patches,
            "coreset_size": target_k,
            "embed_dim": embed_dim,
            "calibrated_threshold": self.threshold,
            "train_score_mean": round(mean_s, 4),
            "train_score_std": round(std_s, 4),
        }

    # Alias for trainer integration
    def fit_normal_features(
        self, dataloader: torch.utils.data.DataLoader, device: Optional[Any] = None,
        cancellation_requested: Optional[Callable[[], bool]] = None,
    ) -> Dict[str, Any]:
        if device is not None:
            self.to(device)
        return self.fit(dataloader, cancellation_requested=cancellation_requested)

    def predict_anomaly_map(
        self, image_tensor: torch.Tensor, out_size: Optional[Tuple[int, int]] = None
    ) -> Tuple[np.ndarray, float]:
        """
        Computes patch distance map and scalar anomaly score via kNN search against normal coreset.
        """
        if self.coreset is None:
            raise RuntimeError("PatchCore coreset must be fitted before running prediction.")

        if image_tensor.ndim == 3:
            image_tensor = image_tensor.unsqueeze(0)

        image_tensor = image_tensor.to(self.device)
        orig_h, orig_w = image_tensor.shape[2:]
        target_size = out_size if out_size else (orig_h, orig_w)

        self.feature_extractor.eval()
        with torch.no_grad():
            feats = self.feature_extractor(image_tensor)  # [1, D, H, W]
            D, H, W = feats.shape[1], feats.shape[2], feats.shape[3]
            patches = feats.squeeze(0).permute(1, 2, 0).reshape(-1, D)  # [H*W, D]

            # Pairwise L2 distances to coreset memory bank
            dist_mat = torch.cdist(patches, self.coreset)  # [H*W, K]
            min_dist, _ = torch.min(dist_mat, dim=1)
            patch_map = min_dist.view(1, 1, H, W)

            # Upsample to image resolution
            up_map = F.interpolate(
                patch_map, size=target_size, mode="bilinear", align_corners=False
            ).squeeze().cpu().numpy()

        smoothed = cv2.GaussianBlur(up_map, (21, 21), 4.0)
        score = float(np.max(smoothed))
        return smoothed, score

    def predict_scores(
        self, dataloader: torch.utils.data.DataLoader,
        cancellation_requested: Optional[Callable[[], bool]] = None,
    ) -> List[float]:
        """Computes anomaly scores for all images in a dataloader."""
        scores: List[float] = []
        for batch in dataloader:
            check_fit_cancelled(cancellation_requested)
            imgs = batch[0] if isinstance(batch, (list, tuple)) else batch
            for i in range(len(imgs)):
                check_fit_cancelled(cancellation_requested)
                _, score = self.predict_anomaly_map(imgs[i : i + 1])
                scores.append(score)
                check_fit_cancelled(cancellation_requested)
        return scores

    def __call__(self, x: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        """Callable interface matching PyTorch models for UnifiedAutoMLTrainer."""
        if x.ndim == 3:
            x = x.unsqueeze(0)
        B = x.shape[0]
        heatmaps = []
        scores = []
        for i in range(B):
            hmap, score = self.predict_anomaly_map(x[i : i + 1])
            heatmaps.append(torch.from_numpy(hmap))
            scores.append(score)
        heatmaps_t = torch.stack(heatmaps, dim=0).unsqueeze(1).to(self.device)  # [B, 1, H, W]
        scores_t = torch.tensor(scores, dtype=torch.float32, device=self.device)  # [B]
        return heatmaps_t, scores_t

    def state_dict(self) -> Dict[str, Any]:
        """Returns serializable state dictionary."""
        state = {
            "feature_extractor_state_dict": {key: value.cpu() for key, value in self.feature_extractor.state_dict().items()},
            "coreset": self.coreset.cpu() if self.coreset is not None else None,
            "threshold": self.threshold,
            "backbone_name": self.backbone_name,
            "coreset_sampling_ratio": self.coreset_sampling_ratio,
            "max_coreset_size": self.max_coreset_size,
            "seed": self.seed,
        }
        from backend.engine.score_contract import calibrated_score_spec
        # Serialization can add migrated legacy fields or follow a statistics
        # update. Bind what is actually saved; prediction uses the cached spec.
        self.score_spec = calibrated_score_spec(state, 'euclidean_distance')
        state['score_spec'] = dict(self.score_spec)
        return state

    def load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        """Loads state dictionary."""
        if state_dict.get('feature_extractor_state_dict') is not None:
            self.feature_extractor.load_state_dict(state_dict['feature_extractor_state_dict'], strict=True)
        self.coreset = state_dict["coreset"].to(self.device) if state_dict.get("coreset") is not None else None
        self.threshold = state_dict.get("threshold", 0.0)
        from backend.engine.score_contract import restore_score_spec
        self.score_spec = restore_score_spec({**state_dict, 'threshold': self.threshold}, 'euclidean_distance')
        self.coreset_sampling_ratio = state_dict.get("coreset_sampling_ratio", self.coreset_sampling_ratio)
        self.max_coreset_size = state_dict.get("max_coreset_size", self.max_coreset_size)

    def save(self, file_path: Union[str, Path]) -> None:
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), str(p))

    def load(self, file_path: Union[str, Path]) -> None:
        data = torch.load(str(file_path), map_location=self.device)
        self.load_state_dict(data)

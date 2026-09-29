"""
backend/engine/anomaly/padim.py

PaDiM: Patch Distribution Modeling for Fast Unsupervised Anomaly Detection.
Models normal patch features via Multivariate Gaussians and Mahalanobis distances.
Strictly trained on normal (OK) images (0 defects in train set).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.engine.anomaly.feature_extractor import ResNetFeatureExtractor

logger = logging.getLogger("vision_ai_studio.anomaly.padim")


class PaDiMDetector:
    """
    PaDiM (Patch Distribution Modeling) Anomaly Detection Engine.
    Strictly trained on normal (OK) images (0 defects).
    """

    def __init__(
        self,
        backbone_name: str = "resnet18",
        target_dim: int = 100,
        regularizer: float = 0.01,
        seed: int = 42,
        device: Optional[Union[torch.device, str]] = None,
        pretrained: bool = True,
    ):
        self.backbone_name = backbone_name
        self.target_dim = target_dim
        self.regularizer = regularizer
        self.seed = seed
        self.device = torch.device(device) if device else torch.device("cpu")

        self.feature_extractor = ResNetFeatureExtractor(
            backbone_name=backbone_name,
            pretrained=pretrained,
            local_avg_pool=False,
        ).to(self.device)

        total_dim = self.feature_extractor.embed_dim
        torch.manual_seed(seed)
        self.sub_dims = torch.randperm(total_dim)[: min(target_dim, total_dim)].to(self.device)

        self.mean: Optional[torch.Tensor] = None      # [H, W, 1, d]
        self.cov_inv: Optional[torch.Tensor] = None   # [H, W, d, d]
        self.threshold: float = 0.0

    def to(self, device: Union[torch.device, str]) -> "PaDiMDetector":
        self.device = torch.device(device)
        self.feature_extractor = self.feature_extractor.to(self.device)
        if self.sub_dims is not None:
            self.sub_dims = self.sub_dims.to(self.device)
        if self.mean is not None:
            self.mean = self.mean.to(self.device)
        if self.cov_inv is not None:
            self.cov_inv = self.cov_inv.to(self.device)
        return self

    def eval(self) -> "PaDiMDetector":
        self.feature_extractor.eval()
        return self

    def fit(self, dataloader: torch.utils.data.DataLoader) -> Dict[str, Any]:
        """
        Fits Gaussian distributions over normal training images.
        HARD CONSTRAINT: dataloader must contain ONLY normal images.
        """
        self.feature_extractor.eval()
        all_embeddings: List[torch.Tensor] = []

        with torch.no_grad():
            for batch in dataloader:
                images = batch[0] if isinstance(batch, (list, tuple)) else batch
                images = images.to(self.device)
                feats = self.feature_extractor(images)  # [B, D, H, W]
                feats = feats[:, self.sub_dims, :, :]   # [B, d, H, W]
                all_embeddings.append(feats.cpu())

        train_feats = torch.cat(all_embeddings, dim=0).to(self.device)
        N, d, H, W = train_feats.shape
        if N < 2:
            raise ValueError(f"PaDiM requires at least 2 normal images to compute covariance, got {N}")

        # Permute to [H, W, N, d]
        feats_perm = train_feats.permute(2, 3, 0, 1)
        self.mean = feats_perm.mean(dim=2, keepdim=True)  # [H, W, 1, d]

        # Compute sample covariance: (X - mu)^T (X - mu) / (N - 1)
        diff = feats_perm - self.mean
        cov = torch.matmul(diff.transpose(-1, -2), diff) / float(N - 1)  # [H, W, d, d]

        # Regularize: Sigma + eps * I
        eye = torch.eye(d, device=self.device).unsqueeze(0).unsqueeze(0)
        cov_reg = cov + self.regularizer * eye

        # Flatten to [H*W, d, d] for clean MPS inversion
        cov_flat = cov_reg.view(-1, d, d)
        cov_inv_flat = torch.linalg.inv(cov_flat)
        self.cov_inv = cov_inv_flat.view(H, W, d, d)

        # Compute training normal scores to calibrate default statistical threshold (mu + 3*sigma)
        train_scores = self.predict_scores(dataloader)
        mean_s = float(np.mean(train_scores))
        std_s = float(np.std(train_scores))
        self.threshold = round(mean_s + 3.0 * std_s, 4)

        return {
            "total_samples": N,
            "feature_dim": d,
            "patch_grid": [H, W],
            "calibrated_threshold": self.threshold,
            "train_score_mean": round(mean_s, 4),
            "train_score_std": round(std_s, 4),
        }

    # Alias for trainer integration
    def fit_normal_features(self, dataloader: torch.utils.data.DataLoader, device: Optional[Any] = None) -> Dict[str, Any]:
        if device is not None:
            self.to(device)
        return self.fit(dataloader)

    def predict_anomaly_map(
        self, image_tensor: torch.Tensor, out_size: Optional[Tuple[int, int]] = None
    ) -> Tuple[np.ndarray, float]:
        """
        Computes pixel-level anomaly heatmap and scalar image-level score.
        Args:
            image_tensor: Tensor of shape [1, 3, H, W] or [3, H, W]
        Returns:
            (heatmap: np.ndarray [H, W], anomaly_score: float)
        """
        if self.mean is None or self.cov_inv is None:
            raise RuntimeError("PaDiM model must be fitted before running prediction.")

        if image_tensor.ndim == 3:
            image_tensor = image_tensor.unsqueeze(0)

        image_tensor = image_tensor.to(self.device)
        orig_h, orig_w = image_tensor.shape[2:]
        target_size = out_size if out_size else (orig_h, orig_w)

        self.feature_extractor.eval()
        with torch.no_grad():
            feat = self.feature_extractor(image_tensor)
            feat = feat[:, self.sub_dims, :, :]  # [1, d, H, W]

            # Vectorized Mahalanobis calculation
            diff = feat.permute(0, 2, 3, 1) - self.mean.permute(2, 0, 1, 3)  # [1, H, W, d]
            mult = torch.matmul(diff.unsqueeze(-2), self.cov_inv.unsqueeze(0))
            dist_sq = torch.matmul(mult, diff.unsqueeze(-1)).squeeze(-1).squeeze(-1)
            dist_map = torch.sqrt(torch.clamp(dist_sq, min=0.0))  # [1, H, W]

            # Spatial upsampling to target image size
            up_map = F.interpolate(
                dist_map.unsqueeze(1), size=target_size, mode="bilinear", align_corners=False
            ).squeeze().cpu().numpy()

        smoothed = cv2.GaussianBlur(up_map, (21, 21), 4.0)
        score = float(np.max(smoothed))
        return smoothed, score

    def predict_scores(self, dataloader: torch.utils.data.DataLoader) -> List[float]:
        """Calculates image-level anomaly scores across a dataloader."""
        scores: List[float] = []
        for batch in dataloader:
            imgs = batch[0] if isinstance(batch, (list, tuple)) else batch
            for i in range(len(imgs)):
                _, score = self.predict_anomaly_map(imgs[i : i + 1])
                scores.append(score)
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
        return {
            "mean": self.mean.cpu() if self.mean is not None else None,
            "cov_inv": self.cov_inv.cpu() if self.cov_inv is not None else None,
            "sub_dims": self.sub_dims.cpu() if self.sub_dims is not None else None,
            "threshold": self.threshold,
            "backbone_name": self.backbone_name,
            "target_dim": self.target_dim,
            "regularizer": self.regularizer,
            "seed": self.seed,
        }

    def load_state_dict(self, state_dict: Dict[str, Any]) -> None:
        """Loads state dictionary."""
        self.mean = state_dict["mean"].to(self.device) if state_dict.get("mean") is not None else None
        self.cov_inv = state_dict["cov_inv"].to(self.device) if state_dict.get("cov_inv") is not None else None
        if state_dict.get("sub_dims") is not None:
            self.sub_dims = state_dict["sub_dims"].to(self.device)
        self.threshold = state_dict.get("threshold", 0.0)
        self.target_dim = state_dict.get("target_dim", self.target_dim)
        self.regularizer = state_dict.get("regularizer", self.regularizer)

    def save(self, file_path: Union[str, Path]) -> None:
        """Saves fitted distribution parameters and metadata."""
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.state_dict(), str(p))

    def load(self, file_path: Union[str, Path]) -> None:
        """Loads fitted distribution parameters."""
        data = torch.load(str(file_path), map_location=self.device)
        self.load_state_dict(data)

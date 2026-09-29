"""
backend/engine/segmentation/loss.py

Combo Loss: Multi-Class Focal Loss + Multi-Class Soft Dice Loss.
Specifically engineered for industrial defect segmentation with severe class imbalance (<0.5% pixel area).
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple, Union
import torch
import torch.nn as nn
import torch.nn.functional as F


class SoftDiceLoss(nn.Module):
    """
    Differentiable Multi-Class Soft Dice Loss with smooth epsilon.
    Optimizes spatial overlap directly.
    """

    def __init__(
        self,
        num_classes: int = 2,
        include_background: bool = False,
        smooth: float = 1e-5,
    ):
        super().__init__()
        self.num_classes = num_classes
        self.include_background = include_background
        self.smooth = smooth

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: Predicted logits [B, C, H, W]
            targets: Integer ground truth masks [B, H, W] with values in [0, C-1]
        """
        probs = F.softmax(logits, dim=1)
        C = logits.shape[1]
        targets_onehot = F.one_hot(targets, num_classes=C).permute(0, 3, 1, 2).float()

        start_c = 0 if self.include_background else 1
        class_losses: List[torch.Tensor] = []

        for c in range(start_c, C):
            p_c = probs[:, c].reshape(-1)
            t_c = targets_onehot[:, c].reshape(-1)
            intersection = (p_c * t_c).sum()
            union = p_c.sum() + t_c.sum()
            dice = (2.0 * intersection + self.smooth) / (union + self.smooth)
            class_losses.append(1.0 - dice)

        if not class_losses:
            p_0 = probs[:, 0].reshape(-1)
            t_0 = targets_onehot[:, 0].reshape(-1)
            intersection = (p_0 * t_0).sum()
            union = p_0.sum() + t_0.sum()
            dice = (2.0 * intersection + self.smooth) / (union + self.smooth)
            return 1.0 - dice

        return torch.stack(class_losses).mean()


class FocalLoss(nn.Module):
    """
    Multi-Class Focal Loss using numerically stable log_softmax and gather.
    Downweights well-classified background pixels via (1 - p_t)^gamma.
    """

    def __init__(
        self,
        gamma: float = 2.0,
        alpha: Optional[Union[float, List[float], torch.Tensor]] = None,
    ):
        super().__init__()
        self.gamma = gamma
        self.alpha = alpha

    def forward(self, logits: torch.Tensor, targets: torch.Tensor) -> torch.Tensor:
        """
        Args:
            logits: Predicted logits [B, C, H, W]
            targets: Integer ground truth masks [B, H, W]
        """
        log_p = F.log_softmax(logits, dim=1)
        p = torch.exp(log_p)

        log_pt = log_p.gather(1, targets.unsqueeze(1)).squeeze(1)
        pt = p.gather(1, targets.unsqueeze(1)).squeeze(1)

        focal_weight = (1.0 - pt) ** self.gamma

        if self.alpha is not None:
            if isinstance(self.alpha, (list, tuple, torch.Tensor)):
                alpha_t = torch.as_tensor(self.alpha, device=logits.device, dtype=logits.dtype)
                alpha_factor = alpha_t.gather(0, targets.reshape(-1)).reshape(targets.shape)
            else:
                alpha_factor = float(self.alpha)
            focal_weight = focal_weight * alpha_factor

        loss = -focal_weight * log_pt
        return loss.mean()


class ComboLoss(nn.Module):
    """
    Composite Focal Loss + Multi-Class Soft Dice Loss.
    Total Loss: L = L_focal + lambda * L_dice
    """

    def __init__(
        self,
        num_classes: int = 2,
        gamma: float = 2.0,
        alpha: Optional[Union[float, List[float], torch.Tensor]] = None,
        dice_weight: float = 1.0,
        include_background: bool = False,
        smooth: float = 1e-5,
    ):
        super().__init__()
        self.focal = FocalLoss(gamma=gamma, alpha=alpha)
        self.dice = SoftDiceLoss(num_classes=num_classes, include_background=include_background, smooth=smooth)
        self.dice_weight = dice_weight

    def forward(
        self, logits: torch.Tensor, targets: torch.Tensor
    ) -> Tuple[torch.Tensor, Dict[str, float]]:
        """
        Returns:
            (total_loss, metrics_dict)
        """
        loss_focal = self.focal(logits, targets)
        loss_dice = self.dice(logits, targets)
        total = loss_focal + self.dice_weight * loss_dice
        return total, {
            "loss": float(total.item()),
            "focal_loss": float(loss_focal.item()),
            "dice_loss": float(loss_dice.item()),
        }

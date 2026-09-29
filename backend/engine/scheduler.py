"""
backend/engine/scheduler.py

Dynamic AdamW Optimizer & Learning Rate Scheduler Layer.
Combines parameter-selective weight decay (0 weight decay for bias & norm) with
linear warmup and cosine annealing decay.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple, Union

import torch
import torch.nn as nn
from torch.optim.lr_scheduler import _LRScheduler


class WarmupCosineAnnealingLR(_LRScheduler):
    """
    Dynamic Learning Rate Scheduler with Linear Warmup and Cosine Annealing.
    
    Args:
        optimizer: PyTorch Optimizer instance.
        total_epochs: Total training epochs (e.g. 10-15 for fast, 30-40 for precision).
        warmup_epochs: Number of linear warmup epochs (default: 3).
        min_lr: Minimum floor learning rate (default: 1e-6).
        last_epoch: The index of last epoch (default: -1).
    """

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        total_epochs: int,
        warmup_epochs: int = 3,
        min_lr: float = 1e-6,
        last_epoch: int = -1,
    ):
        if total_epochs <= 0:
            raise ValueError(f"total_epochs must be > 0, got {total_epochs}")
        self.total_epochs = total_epochs
        # Bound warmup_epochs so it never exceeds total_epochs - 1
        self.warmup_epochs = max(0, min(warmup_epochs, total_epochs - 1))
        self.min_lr = min_lr
        super().__init__(optimizer, last_epoch)

    def get_lr(self) -> List[float]:
        curr = self.last_epoch
        if curr < 0:
            return [base_lr for base_lr in self.base_lrs]

        lrs = []
        for base_lr in self.base_lrs:
            eff_min = min(self.min_lr, base_lr)
            if self.warmup_epochs > 0 and curr < self.warmup_epochs:
                # Linear warmup from eff_min to base_lr
                alpha = (curr + 1) / float(self.warmup_epochs)
                lr = eff_min + (base_lr - eff_min) * alpha
            else:
                # Cosine annealing decay from base_lr to eff_min
                decay_epochs = max(1, self.total_epochs - self.warmup_epochs)
                step = min(decay_epochs, curr - self.warmup_epochs)
                progress = step / float(decay_epochs)
                cosine = 0.5 * (1.0 + math.cos(math.pi * progress))
                lr = eff_min + (base_lr - eff_min) * cosine
            lrs.append(lr)
        return lrs


def create_adamw_optimizer(
    model: nn.Module,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    betas: Tuple[float, float] = (0.9, 0.999),
    eps: float = 1e-8,
) -> torch.optim.AdamW:
    """
    Constructs AdamW optimizer with selective weight decay.
    Biases and 1D normalization weights (BatchNorm, LayerNorm, GroupNorm) receive 0.0 weight decay.
    """
    decay_params: List[nn.Parameter] = []
    no_decay_params: List[nn.Parameter] = []

    for name, param in model.named_parameters():
        if not param.requires_grad:
            continue
        # Biases and 1D affine parameters receive 0.0 weight decay
        if param.ndim <= 1 or name.endswith(".bias") or "norm" in name.lower() or "bn" in name.lower():
            no_decay_params.append(param)
        else:
            decay_params.append(param)

    optim_groups = [
        {"params": decay_params, "weight_decay": weight_decay},
        {"params": no_decay_params, "weight_decay": 0.0},
    ]
    return torch.optim.AdamW(optim_groups, lr=lr, betas=betas, eps=eps)


def create_optimizer_and_scheduler(
    model: nn.Module,
    lr: float = 1e-3,
    weight_decay: float = 1e-4,
    total_epochs: int = 15,
    warmup_epochs: int = 3,
    min_lr: float = 1e-6,
) -> Tuple[torch.optim.AdamW, WarmupCosineAnnealingLR]:
    """Convenience factory returning configured optimizer and scheduler pair."""
    optimizer = create_adamw_optimizer(model, lr=lr, weight_decay=weight_decay)
    scheduler = WarmupCosineAnnealingLR(
        optimizer=optimizer,
        total_epochs=total_epochs,
        warmup_epochs=warmup_epochs,
        min_lr=min_lr,
    )
    return optimizer, scheduler

"""Export native binary probabilities without an extra softmax/subtraction."""
from __future__ import annotations
import torch
from torch import nn


class DinoSyntheticPatchExport(nn.Module):
    def __init__(self, detector):
        super().__init__()
        self.encoder = detector.model.encoder
        self.head = detector.model.head
        self.num_prefix_tokens = detector.model.num_prefix_tokens
        self.register_buffer('input_mean', detector.model.input_mean.detach().clone())
        self.register_buffer('input_std', detector.model.input_std.detach().clone())

    def forward(self, rgb):
        tokens = self.encoder.forward_features((rgb - self.input_mean) / self.input_std)
        pooled = torch.cat((tokens[:, 0], tokens[:, self.num_prefix_tokens:].mean(dim=1)), dim=1)
        logits = self.head(pooled).squeeze(-1)
        probability = torch.sigmoid(logits)
        return torch.stack((1.0 - probability, probability), dim=1)

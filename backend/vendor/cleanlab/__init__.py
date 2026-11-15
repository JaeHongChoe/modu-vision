"""Selected cleanlab 2.9.0 NumPy rank functions (Apache-2.0).

Modified: selected function bodies only, no cleanlab package dependencies.
Source SHA and original license: third_party/cleanlab/provenance.json and LICENSE.
"""
import numpy as np


def normalized_margin(labels: np.ndarray, pred_probs: np.ndarray) -> np.ndarray:
    """Upstream get_normalized_margin_for_each_label, with unchanged arithmetic."""
    self_confidence = pred_probs[np.arange(labels.shape[0]), labels]
    N, K = pred_probs.shape
    del_indices = np.arange(N) * K + labels
    max_prob_not_label = np.max(
        np.delete(pred_probs, del_indices, axis=None).reshape(N, K - 1), axis=-1
    )
    label_quality_scores = (self_confidence - max_prob_not_label + 1) / 2
    return label_quality_scores

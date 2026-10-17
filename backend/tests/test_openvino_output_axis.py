"""Fixed arrays verify conversion diagnostics without a model or OpenVINO call."""
import numpy as np
import pytest

from backend.engine.openvino_runtime import _output_argmax_counts


def test_nchw_class_changes_are_not_hidden_by_matching_width_argmax():
    # Every width maximum stays in column 1, while every pixel changes class.
    reference = np.array([[[[2, 3], [4, 5]], [[1, 2], [3, 4]]]], dtype=np.float32)
    candidate = reference.copy()
    candidate[:, 1] += 2
    assert _output_argmax_counts(reference, candidate) == (-1, 0, 4)
    assert _output_argmax_counts(reference, candidate, task='segmentation') == (1, 4, 4)


def test_nchw_denominator_counts_each_batch_and_spatial_location_once():
    reference = np.zeros((2, 3, 2, 4), dtype=np.float32)
    reference[:, 2] = 1
    candidate = reference.copy()
    candidate[1, 0, 0, 0] = 2
    assert _output_argmax_counts(reference, candidate, task='segmentation') == (1, 1, 16)


def test_default_classification_metric_retains_the_original_last_axis():
    reference = np.array([[1, 2, 3], [3, 2, 1]], dtype=np.float32)
    candidate = np.array([[1, 2, 3], [1, 2, 3]], dtype=np.float32)
    assert _output_argmax_counts(reference, candidate) == (-1, 1, 2)
    assert _output_argmax_counts(reference, candidate, task='classification') == (-1, 1, 2)


def test_segmentation_metric_refuses_an_ambiguous_non_nchw_output():
    reference = np.zeros((2, 3, 4), dtype=np.float32)
    with pytest.raises(ValueError, match='NCHW'):
        _output_argmax_counts(reference, reference.copy(), task='segmentation')

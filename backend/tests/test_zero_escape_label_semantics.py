"""Threshold analysis uses the same reviewed normal/defect truth as history."""
import pytest

from backend.engine.evaluation_history import binary_verdict
from backend.engine.zero_escape_analyzer import (
    analyze_zero_escape, compute_sample_defect_score, is_defect_label,
)


@pytest.mark.parametrize('normal_label', ['정상', '양품', ' 합격 '])
def test_korean_normal_labels_remain_normal_in_threshold_analysis(normal_label):
    predictions = [
        {'image_id': 'normal', 'ground_truth': normal_label,
         'predicted_class': normal_label, 'confidence': .95},
        {'image_id': 'defect', 'ground_truth': 'scratch',
         'predicted_class': 'NG_crack', 'confidence': .9},
    ]
    analysis = analyze_zero_escape(predictions)
    assert analysis['total_normals'] == 1
    assert analysis['total_defects'] == 1
    assert analysis['current_stats']['overkill_count'] == 0
    assert analysis['current_stats']['underkill_count'] == 0
    assert analysis['sample_details'][0]['status_current'] == 'CORRECT_OK'
    assert analysis['sample_details'][0]['defect_score'] == pytest.approx(.05)
    assert binary_verdict(normal_label) == 'OK'
    assert is_defect_label(normal_label) is False


@pytest.mark.parametrize('label', ['OK', 'normal', 'pass', 'good', 'background',
                                 'OK_chip', 'normal_part', 'part_good', 0])
def test_existing_normal_class_names_keep_inverse_confidence(label):
    assert is_defect_label(label) is False
    assert binary_verdict(label) == 'OK'
    assert compute_sample_defect_score({'predicted_class': label, 'confidence': .9}) == pytest.approx(.1)


@pytest.mark.parametrize('label', ['scratch', 'crack', '불량', 'NG_normal', 'good_defect', 'fail_OK', 1])
def test_named_defects_and_explicit_defect_tokens_keep_confidence(label):
    assert is_defect_label(label) is True
    assert binary_verdict(label) == 'NG'
    assert compute_sample_defect_score({'predicted_class': label, 'confidence': .9}) == pytest.approx(.9)


@pytest.mark.parametrize('normal_label', ['정상', '양품', '합격'])
def test_detection_uses_defect_box_confidence_and_ignores_normal_classes(normal_label):
    prediction = {'detections': [
        {'label': normal_label, 'score': .99},
        {'label': 'OK_chip', 'score': .98},
        {'label': 'scratch', 'score': .74},
    ]}
    assert compute_sample_defect_score(prediction, task='detection') == pytest.approx(.74)


@pytest.mark.parametrize('normal_label', ['정상', '양품', '합격'])
def test_segmentation_normal_class_without_defect_pixels_keeps_inverse_confidence(normal_label):
    prediction = {'predicted_class': normal_label, 'confidence': .95, 'mask_coverage': 0.}
    assert compute_sample_defect_score(prediction, task='segmentation') == pytest.approx(.05)


def test_segmentation_mask_evidence_still_overrides_a_normal_class_name():
    prediction = {'predicted_class': '정상', 'confidence': .95, 'mask_coverage': .2}
    assert compute_sample_defect_score(prediction, task='segmentation') == pytest.approx(1.)


@pytest.mark.parametrize('truth', [None, '', '  ', 'review', ' REVIEW ', 'Unknown'])
def test_unknown_truth_is_rejected_instead_of_counted_as_ok_or_ng(truth):
    predictions = [
        {'image_id': 'normal', 'ground_truth': 'OK', 'defect_score': .05},
        {'image_id': 'defect', 'ground_truth': 'scratch', 'defect_score': .9},
        {'image_id': 'unreviewed', 'ground_truth': truth, 'defect_score': .01},
    ]
    assert binary_verdict(truth) is None
    with pytest.raises(ValueError, match='ground truth'):
        analyze_zero_escape(predictions)


def test_missing_ground_truth_is_rejected():
    with pytest.raises(ValueError, match='ground truth'):
        analyze_zero_escape([{'image_id': 'unreviewed', 'predicted_class': 'NG', 'confidence': .9}])

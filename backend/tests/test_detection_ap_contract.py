"""Versioned bbox AP fixes with independent COCO-generated synthetic receipts."""
import json
from pathlib import Path

import pytest
import torch

from backend.engine.detection.metrics import evaluate_detections_map

V2 = 'detection-ap-v2'


def prediction(boxes=(), labels=(), scores=()):
    return {'boxes': torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            'labels': torch.tensor(labels, dtype=torch.int64), 'scores': torch.tensor(scores, dtype=torch.float32)}


def truth(boxes=(), labels=()):
    return {'boxes': torch.tensor(boxes, dtype=torch.float32).reshape(-1, 4),
            'labels': torch.tensor(labels, dtype=torch.int64)}


def evaluate(predictions, targets, **kwargs):
    return evaluate_detections_map(predictions, targets, num_classes=4,
        class_names={0: 'background', 1: 'scratch', 2: 'dent', 3: 'unused'}, metric_contract=V2, **kwargs)


def test_absent_ground_truth_classes_do_not_inflate_macro_ap():
    args = ([prediction()], [truth([[0, 0, 10, 10]], [1])])
    legacy = evaluate_detections_map(*args, num_classes=4)
    assert legacy['mAP_50'] == .6667  # historical result still validates under its original contract
    result = evaluate(*args)
    assert result['mAP_50'] == 0 and result['available'] is True
    assert result['class_ap50'] == {'scratch': 0}
    assert result['excluded_classes'] == ['dent', 'unused']


def test_matching_skips_occupied_best_and_uses_next_qualifying_ground_truth():
    result = evaluate([prediction([[0, 0, 10, 10], [0, 0, 10, 10]], [1, 1], [.9, .8])],
                      [truth([[0, 0, 10, 10], [2, 0, 12, 10]], [1, 1])])
    assert result['mAP_50'] == 1


def test_equal_iou_ties_follow_coco_last_eligible_ground_truth_order():
    result = evaluate([prediction([[0, 0, 20, 10], [0, 0, 10, 10]], [1, 1], [.9, .8])],
                      [truth([[0, 0, 10, 10], [10, 0, 20, 10]], [1, 1])])
    assert result['mAP_50'] == 1


def test_zero_ground_truth_is_unavailable_instead_of_perfect_or_zero():
    result = evaluate([prediction()], [truth()])
    assert result['mAP_50'] is None and result['mAP_50_95'] is None
    assert result['available'] is False and result['unavailable_reason'] == 'no_ground_truth_objects'
    assert result['metric_contract'] == V2


def test_unknown_metric_contract_and_invalid_observations_refuse():
    with pytest.raises(ValueError, match='contract'):
        evaluate_detections_map([prediction()], [truth()], metric_contract='unrecognized')
    with pytest.raises(ValueError): evaluate([prediction([[0, 0, 1, 1]], [1], [float('nan')])], [truth()])
    with pytest.raises(ValueError): evaluate([prediction()], [])


def test_v2_differential_receipts_match_official_coco_on_supported_subset():
    source = Path(__file__).with_name('fixtures') / 'detection_ap_v2_coco.json'
    assert source.is_file(), 'Retain the independently generated oracle and complete synthetic inputs'
    fixture = json.loads(source.read_text())
    assert fixture['oracle']['package'] == 'pycocotools' and fixture['oracle']['version'] == '2.0.11'
    assert fixture['oracle']['max_detections'] == 100
    for case in fixture['cases']:
        result = evaluate([prediction(**row) for row in case['predictions']], [truth(**row) for row in case['targets']])
        for name, expected in case['expected'].items():
            assert result[name] == expected, (case['name'], name, result[name], expected)


def test_saved_metric_contract_cannot_be_silently_reinterpreted():
    from backend.engine.detection.metrics import bound_detection_ap_contract
    assert bound_detection_ap_contract({}, {}) == 'detection-ap-legacy-v1'
    assert bound_detection_ap_contract({'detection_metric_contract': V2}, {'detection_metric_contract': V2}) == V2
    with pytest.raises(ValueError): bound_detection_ap_contract({'detection_metric_contract': V2}, {})
    with pytest.raises(ValueError): bound_detection_ap_contract({}, {'detection_metric_contract': V2})
    with pytest.raises(ValueError): bound_detection_ap_contract({'detection_metric_contract': 'unknown'}, {})


def test_new_core_detection_recipe_pins_metric_contract_and_binding(tmp_path):
    from backend.engine.core_evaluation_recipe import evaluation_spec
    from backend.remote.evaluation_cohort import digest
    checkpoint = tmp_path / 'best_model.pt'; checkpoint.write_bytes(b'synthetic-header-no-model-execution')
    checkpoint.with_name('model_meta.json').write_text('{"task":"detection","image_size":[32,32]}')
    info = {'device': 'cpu', 'job_id': 'owned-model', 'training_snapshot_sha256': '0' * 64,
            'cohort': {}, 'operation_authority': 'owned', 'execution_target': 'local',
            'compute_profile_id': None, 'compute_profile_name': None, 'compute_gpu_selector': None,
            'execution_profile_sha256': None}
    spec = evaluation_spec(info, checkpoint, 'cpu')
    assert spec['detection_metric_contract'] == V2
    assert spec['evaluation_binding_sha256'] == digest({k: v for k, v in spec.items() if k != 'evaluation_binding_sha256'})

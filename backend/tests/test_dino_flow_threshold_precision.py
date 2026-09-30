"""Calibrated patch probabilities retain precision and strict threshold semantics."""
import numpy as np
import pytest

from backend.engine.flowchart_engine import (
    CropInspectionResult, FlowchartEngine, FlowNode, FlowNodeData,
)


def _decision(threshold=.8):
    return FlowNode(id='decision', position={'x': 0, 'y': 0}, data=FlowNodeData(
        label='score', node_type='decision', rule='score_gt_threshold', threshold=threshold))


@pytest.mark.parametrize('scores,expected', [
    ([('patch_score', .8)], 'OK'),
    ([('pixel_score', .8)], 'NG'),
    ([('patch_score', .8), ('pixel_score', .79)], 'OK'),
    ([('patch_score', .79), ('pixel_score', .8)], 'NG'),
    ([('patch_score', .8000001), ('pixel_score', .79)], 'NG'),
])
def test_score_decision_compares_each_crop_using_its_semantics(scores, expected):
    rows = [CropInspectionResult(roi_id=str(index), label='fixture', bbox=[0, 0, 32, 32],
        defect_score=score, verdict='OK', map_semantics=semantics,
        crop_thumbnail='', flaw_type='fixture')
        for index, (semantics, score) in enumerate(scores)]
    assert FlowchartEngine(device='cpu')._evaluate_decision_rules(rows, _decision())[0] == expected


@pytest.mark.parametrize('score,expected', [(.800029, 'OK'), (.800049, 'NG')])
def test_patch_inspection_preserves_unrounded_score_through_final_decision(monkeypatch, score, expected):
    class PatchModel:
        model_metadata = {'map_semantics': 'patch_score', 'detector_type': 'dino_synthetic'}
        def predict_anomaly_map(self, image):
            return np.full(image.shape[-2:], score, dtype=np.float32), score
    engine = FlowchartEngine(device='cpu')
    monkeypatch.setattr(engine, '_get_inspection_model', lambda **kwargs: (PatchModel(), True))
    monkeypatch.setattr(engine, '_resolve_checkpoint', lambda *args: None)
    inspection = FlowNode(id='inspection', position={'x': 0, 'y': 0}, data=FlowNodeData(
        label='anomaly', node_type='inspection', task='anomaly', threshold=.80003))
    rows, _, _ = engine._inspect_crops(np.zeros((32, 32, 3), dtype=np.uint8),
        [{'id': 'full_image', 'bbox': [0, 0, 32, 32], 'label': 'fixture'}], inspection)
    assert rows[0].defect_score == score
    assert rows[0].verdict == expected
    assert engine._evaluate_decision_rules(rows, _decision(.80003))[0] == expected

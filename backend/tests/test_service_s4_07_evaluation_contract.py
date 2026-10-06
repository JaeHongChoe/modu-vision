"""Actual OBB adapter result contract consumed by the rotated evaluation screen."""
import pytest
from backend.engine import rotated_detection as rotated
from backend.tests.test_service_s4_07 import dataset, ExternalYOLO


@pytest.mark.parametrize('matched', [False, True])
def test_obb_axis_angle_is_reported_only_for_matched_truth(tmp_path, monkeypatch, matched):
    rows = dataset(tmp_path)
    if matched:
        rows[2]['objects'][0]['box'] = {'cx': 40, 'cy': 30, 'width': 20, 'height': 10, 'angle_deg': -90}
    manifest = rotated.write_rotated_manifest(tmp_path, rows)
    local = tmp_path / 'explicit-local.pt'; local.write_bytes(b'controlled-native-test-weights')
    from backend.engine import yolo_obb_adapter as adapter
    monkeypatch.setattr(adapter, '_runtime', lambda: ExternalYOLO)
    output = tmp_path / 'model'
    rotated.train_rotated_detector(tmp_path, output, epochs=1, recipe={
        'adapter': 'ultralytics_yolo_obb', 'model_path': str(local), 'trust_native_weights': True})
    result = adapter.evaluate_yolo(output / 'best_model.pt', manifest, split='test', device='cpu')
    assert result['angle_convention'] == 'clockwise_degrees_axial_180'
    assert result['direction_supported'] is False
    assert result['matched_objects'] == int(matched)
    if matched:
        assert result['mean_angle_error_deg'] == pytest.approx(0, abs=1e-5)
    else:
        assert result['mean_angle_error_deg'] is None
    assert 'mean_direction_error_deg' not in result

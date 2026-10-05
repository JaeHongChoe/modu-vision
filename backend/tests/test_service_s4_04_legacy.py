"""Legacy Faster R-CNN reconstruction retains tensors, labels and boxes offline."""
import pytest
import torch
from backend.engine.detection import create_detection_model
from backend.engine.exporter import load_checkpoint_and_reconstruct_model


@pytest.mark.parametrize('classes', [['scratch', 'stain'], ['background', 'scratch', 'stain']])
def test_legacy_checkpoint_reconstructs_exact_detection_without_download(tmp_path, monkeypatch, classes):
    def refused(*args, **kwargs):
        raise AssertionError('Offline reconstruction must not download')
    monkeypatch.setattr(torch.hub, 'download_url_to_file', refused)
    torch.set_num_threads(1); torch.manual_seed(114)
    model = create_detection_model('fast', num_classes=3, pretrained=False).eval()
    model.transform.min_size = (64,); model.transform.max_size = 64
    image = torch.rand(1, 3, 64, 64)
    with torch.no_grad(): expected = model(image)[0]
    checkpoint = tmp_path / 'best_model.pt'
    torch.save({'task': 'detection', 'preset': 'fast', 'classes': classes,
        'image_size': [64, 64], 'model_state_dict': model.state_dict()}, checkpoint)
    rebuilt, metadata, anomaly = load_checkpoint_and_reconstruct_model(checkpoint)
    rebuilt.transform.min_size = (64,); rebuilt.transform.max_size = 64
    with torch.no_grad(): actual = rebuilt(image)[0]
    for key in ('boxes', 'scores', 'labels'):
        torch.testing.assert_close(actual[key], expected[key], rtol=0, atol=0)
    assert metadata['classes'] == classes and anomaly is None
    assert actual['labels'].numel() and set(actual['labels'].tolist()).issubset({1, 2})

"""Checkpoint reconstruction does not request hidden backbone downloads."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine.detection import model as detection_model
from backend.engine.segmentation import model as segmentation_model


@pytest.mark.parametrize("preset,factory_name", [
    ("fast", "fasterrcnn_mobilenet_v3_large_fpn"),
    ("precision", "fasterrcnn_resnet50_fpn_v2"),
])
def test_detection_pretrained_false_disables_backbone_download(monkeypatch, preset, factory_name):
    calls = []

    def fake_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(roi_heads=SimpleNamespace(
            box_predictor=SimpleNamespace(cls_score=SimpleNamespace(in_features=4)),
            box_roi_pool=SimpleNamespace(forward=lambda *args: None),
        ))

    monkeypatch.setattr(detection_model.detection, factory_name, fake_factory)
    monkeypatch.setattr(detection_model, "FastRCNNPredictor", lambda *_: object())
    detection_model.create_detection_model(preset=preset, pretrained=False)
    assert calls == [{"weights": None, "weights_backbone": None}]


@pytest.mark.parametrize("backbone,factory_name", [
    ("resnet50", "deeplabv3_resnet50"),
    ("mobilenet_v3", "deeplabv3_mobilenet_v3_large"),
])
def test_deeplab_pretrained_false_disables_backbone_download(monkeypatch, backbone, factory_name):
    calls = []

    def fake_factory(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            classifier=[None, None, None, None, SimpleNamespace(in_channels=4)],
            aux_classifier=None,
        )

    monkeypatch.setattr(segmentation_model.tv_seg, factory_name, fake_factory)
    segmentation_model.DeepLabV3Wrapper(num_classes=2, backbone=backbone, pretrained=False)
    assert calls == [{"weights": None, "weights_backbone": None}]

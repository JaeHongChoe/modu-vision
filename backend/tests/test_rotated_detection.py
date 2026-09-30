"""A single oriented object in each crop has verified geometry and provenance."""

from __future__ import annotations

import hashlib
import json
import math
import threading
from pathlib import Path

import cv2
import numpy as np
import pytest
import torch
from PIL import Image

from backend.engine.rotated_detection import (
    RotatedBoxDataset,
    RotatedBoxNet,
    RotatedTrainingCancelled,
    box_from_polygon,
    evaluate_rotated_detector,
    load_rotated_manifest,
    oriented_iou,
    predict_rotated_box,
    train_rotated_detector,
    write_rotated_manifest,
)


def _write_dataset(root: Path) -> dict:
    samples = []
    specs = [
        ("train_a", "train", (45, 35, 30, 14, 25), 20),
        ("train_b", "train", (54, 33, 26, 12, -35), 40),
        ("val", "val", (43, 35, 24, 10, 10), 60),
        ("test", "test", (50, 35, 30, 12, -20), 80),
    ]
    for name, split, values, background in specs:
        path = root / "images" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        pixels = np.full((72, 96, 3), background, dtype=np.uint8)
        points = cv2.boxPoints(((values[0], values[1]), (values[2], values[3]), values[4]))
        cv2.fillConvexPoly(pixels, np.rint(points).astype(np.int32), (245, 245, 245))
        Image.fromarray(pixels).save(path)
        samples.append({
            "image": f"images/{name}.png",
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "split": split,
            "label": "defect",
            "box": dict(zip(("cx", "cy", "width", "height", "angle_deg"), values)),
        })
    manifest = {"version": 1, "samples": samples}
    (root / "rotated_boxes.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_manifest_requires_source_hash_and_isolated_splits(tmp_path: Path):
    data = _write_dataset(tmp_path)
    loaded = load_rotated_manifest(tmp_path)
    assert loaded.class_name == "defect"
    assert loaded.provenance["split_counts"] == {"train": 2, "val": 1, "test": 1}
    assert loaded.provenance["dataset_sha256"].startswith("sha256:")
    image, target = RotatedBoxDataset(loaded, split="test", image_size=64)[0]
    assert image.shape == (3, 64, 64)
    assert target.shape == (6,)
    assert target[0].item() == pytest.approx(50 / 96)

    (tmp_path / "images" / "val.png").write_bytes((tmp_path / "images" / "train_a.png").read_bytes())
    data["samples"][2]["source_sha256"] = data["samples"][0]["source_sha256"]
    (tmp_path / "rotated_boxes.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        load_rotated_manifest(tmp_path)


def test_manifest_writer_pins_hashes_without_destroying_last_valid_manifest(tmp_path: Path):
    data = _write_dataset(tmp_path)
    old = (tmp_path / "rotated_boxes.json").read_bytes()
    input_rows = [{key: value for key, value in row.items() if key != "source_sha256"}
                  for row in data["samples"]]

    result = write_rotated_manifest(tmp_path, input_rows)

    assert result.provenance["source_sha256"]["images/train_a.png"] == data["samples"][0]["source_sha256"]
    saved = (tmp_path / "rotated_boxes.json").read_bytes()
    assert saved != old
    invalid = [*input_rows]
    invalid[0] = {**invalid[0], "box": {**invalid[0]["box"], "cx": -10}}
    with pytest.raises(ValueError, match="outside"):
        write_rotated_manifest(tmp_path, invalid)
    assert (tmp_path / "rotated_boxes.json").read_bytes() == saved


def test_manifest_and_dataset_reject_changed_pixels(tmp_path: Path):
    _write_dataset(tmp_path)
    manifest = load_rotated_manifest(tmp_path)
    dataset = RotatedBoxDataset(manifest, split="train", image_size=64)
    path = tmp_path / "images" / "train_a.png"
    path.write_bytes((tmp_path / "images" / "train_b.png").read_bytes())
    with pytest.raises(ValueError, match="changed"):
        dataset[0]
    with pytest.raises(ValueError, match="SHA-256"):
        load_rotated_manifest(tmp_path)


def test_manifest_rejects_symlinked_source_even_inside_root(tmp_path: Path):
    _write_dataset(tmp_path)
    source = tmp_path / "images" / "train_a.png"
    relocated = tmp_path / "images" / "relocated.png"
    source.rename(relocated)
    source.symlink_to(relocated)

    with pytest.raises(ValueError, match="regular file"):
        load_rotated_manifest(tmp_path)


def test_manifest_rejects_invalid_oriented_geometry(tmp_path: Path):
    data = _write_dataset(tmp_path)
    data["samples"][0]["box"]["cx"] = 2
    (tmp_path / "rotated_boxes.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="outside"):
        load_rotated_manifest(tmp_path)


def test_rotated_long_side_is_representable_when_it_exceeds_image_width(tmp_path: Path):
    data = _write_dataset(tmp_path)
    data["samples"][0]["box"] = {
        "cx": 48, "cy": 36, "width": 105, "height": 8, "angle_deg": 35,
    }
    (tmp_path / "rotated_boxes.json").write_text(json.dumps(data), encoding="utf-8")

    target = RotatedBoxDataset(load_rotated_manifest(tmp_path), split="train", image_size=64)[0][1]

    assert target[2].item() == pytest.approx(105 / 120)
    assert target[3].item() == pytest.approx(8 / 120)


def test_polygon_conversion_and_oriented_iou_use_rotated_geometry():
    points = cv2.boxPoints(((60, 40), (30, 12), 28)).tolist()
    box = box_from_polygon(points)
    assert box["cx"] == pytest.approx(60, abs=0.1)
    assert box["cy"] == pytest.approx(40, abs=0.1)
    assert box["width"] == pytest.approx(30, abs=0.1)
    assert box["height"] == pytest.approx(12, abs=0.1)
    assert box["angle_deg"] == pytest.approx(28, abs=0.1)
    assert oriented_iou(box, box) == pytest.approx(1.0)
    shifted = {**box, "cx": box["cx"] + 50}
    assert oriented_iou(box, shifted) == 0.0


def test_checkpoint_prediction_scales_to_original_pixels(tmp_path: Path):
    data = _write_dataset(tmp_path)
    model = RotatedBoxNet()
    for parameter in model.parameters():
        parameter.data.zero_()
    logits = [0.0, 0.0, math.log(0.2 / 0.8), math.log(0.3 / 0.7),
              math.sin(math.radians(60)), math.cos(math.radians(60))]
    model.head[-1].bias.data.copy_(torch.tensor(logits))
    model_dir = tmp_path / "model"
    model_dir.mkdir()
    checkpoint = model_dir / "best_model.pt"
    torch.save({"task": "rotated_detection", "class_name": "defect", "image_size": 64,
                "model_state_dict": model.state_dict()}, checkpoint)
    checksum = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    (model_dir / "model_meta.json").write_text(json.dumps({
        "task": "rotated_detection", "class_name": "defect", "image_size": 64,
        "checkpoint_sha256": checksum,
    }), encoding="utf-8")

    source = tmp_path / data["samples"][3]["image"]
    result = predict_rotated_box(checkpoint, source, device="cpu")

    assert result["task"] == "rotated_detection"
    assert result["label"] == "defect"
    assert result["box"]["cx"] == pytest.approx(48, abs=0.01)
    assert result["box"]["cy"] == pytest.approx(36, abs=0.01)
    assert result["box"]["width"] == pytest.approx(24, abs=0.01)
    assert result["box"]["height"] == pytest.approx(36, abs=0.01)
    assert result["box"]["angle_deg"] == pytest.approx(30, abs=0.01)
    assert len(result["polygon"]) == 4
    assert result["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result["model_sha256"] == checksum

    source_alias = tmp_path / "source_alias.png"
    source_alias.symlink_to(source)
    with pytest.raises(ValueError, match="regular source image"):
        predict_rotated_box(checkpoint, source_alias, device="cpu")

    relocated = model_dir / "retained_model.pt"
    checkpoint.rename(relocated)
    checkpoint.symlink_to(relocated)
    with pytest.raises(ValueError, match="best_model.pt"):
        predict_rotated_box(checkpoint, source, device="cpu")
    checkpoint.unlink()
    relocated.rename(checkpoint)

    checkpoint.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        predict_rotated_box(checkpoint, source, device="cpu")


def test_cpu_train_evaluate_and_checkpoint_predict(tmp_path: Path):
    _write_dataset(tmp_path)
    torch.manual_seed(7)
    torch.set_num_threads(2)
    output = tmp_path / "run"
    receipt = train_rotated_detector(tmp_path, output, epochs=1, batch_size=2, image_size=64, device="cpu")
    assert receipt["status"] == "completed"
    assert receipt["epochs_completed"] == 1
    assert (output / "best_model.pt").is_file()
    assert (output / "model_meta.json").is_file()
    assert receipt["dataset_sha256"].startswith("sha256:")

    metrics = evaluate_rotated_detector(output / "best_model.pt", tmp_path, split="test", device="cpu")
    assert metrics["sample_count"] == 1
    assert 0 <= metrics["mean_oriented_iou"] <= 1
    assert 0 <= metrics['mAP_50'] <= 1
    assert metrics['test_predictions'][0]['object_evidence']['coordinate_space']=='original_image'
    assert metrics['test_predictions'][0]['source_sha256']
    assert metrics['test_predictions'][0]['object_evidence']['per_class']
    assert 0 <= metrics["mean_angle_error_deg"] <= 90
    assert metrics["dataset_sha256"] == receipt["dataset_sha256"]
    source = tmp_path / "images" / "test.png"
    prediction = predict_rotated_box(output / "best_model.pt", source, device="cpu")
    assert 0 <= prediction["box"]["cx"] <= 96
    assert 0 <= prediction["box"]["cy"] <= 72
    assert prediction["model_sha256"] == receipt["checkpoint_sha256"]


def test_explicit_corrected_box_evaluation_requires_trained_classes(tmp_path):
    raw = _write_dataset(tmp_path)
    output = tmp_path / "candidate"
    receipt = train_rotated_detector(tmp_path, output, epochs=1, batch_size=2)
    raw["samples"][-1]["box"]["angle_deg"] = 5
    (tmp_path / "rotated_boxes.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="differs"):
        evaluate_rotated_detector(output / "best_model.pt", tmp_path)
    result = evaluate_rotated_detector(output / "best_model.pt", tmp_path, allow_dataset_revision=True)
    assert result["training_dataset_sha256"] == receipt["dataset_sha256"]
    assert result["dataset_revision_changed"] is True
    raw["samples"][-1]["label"] = "new_defect"
    (tmp_path / "rotated_boxes.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="class"):
        evaluate_rotated_detector(output / "best_model.pt", tmp_path, allow_dataset_revision=True)


def test_training_cancel_before_first_batch_writes_no_candidate(tmp_path: Path):
    _write_dataset(tmp_path)
    event = threading.Event()
    event.set()
    output = tmp_path / "cancelled_run"

    with pytest.raises(RotatedTrainingCancelled):
        train_rotated_detector(tmp_path, output, epochs=10, device="cpu", cancel_event=event)

    assert not (output / "best_model.pt").exists()

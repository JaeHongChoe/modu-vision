"""An OCR candidate needs explicit text labels and source-linked evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import torch
from PIL import Image, ImageDraw, ImageFont

from backend.engine.ocr import (
    OCRDataset,
    _decode,
    evaluate_ocr_checkpoint,
    load_ocr_manifest,
    predict_ocr,
    train_ocr,
    write_ocr_manifest,
)


def test_ctc_decoder_combines_paths_when_blank_is_each_frames_argmax():
    # P(A) = 0.4*0.4 + 0.4*0.5 + 0.5*0.4 = 0.56;
    # P(empty) = 0.5*0.5 = 0.25. Greedy collapse would be empty.
    logits = torch.log(torch.tensor([[0.5, 0.4, 0.1], [0.5, 0.4, 0.1]]))
    label, confidence = _decode(logits, "AB")
    assert label == "A"
    assert confidence > 0.5


def _image(root: Path, name: str, *, mark: str) -> Path:
    path = root / "images" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("L", (64, 32), 255)
    draw = ImageDraw.Draw(image)
    draw.text((8, 0), mark, font=ImageFont.load_default(size=24), fill=0)
    image.save(path)
    return path


def _labeled_images(root: Path, *, test: bool = True) -> list[dict[str, str]]:
    rows = []
    for split in (("train", "val", "test") if test else ("train", "val")):
        for label in ("A", "B"):
            name = f"{split}_{label}.png"
            _image(root, name, mark=label)
            # Each source is deliberately distinct, even when the text is the same.
            with Image.open(root / "images" / name) as opened:
                pixels = np.asarray(opened).copy()
            pixels[0, 0] = {"train": 10, "val": 20, "test": 30}[split] + ord(label)
            Image.fromarray(pixels).save(root / "images" / name)
            rows.append({"image": f"images/{name}", "text": label, "split": split})
    return rows


def test_training_cancel_reaches_real_batch_boundary_before_export(tmp_path):
    import threading
    rows=_labeled_images(tmp_path);write_ocr_manifest(tmp_path,rows)
    event=threading.Event();progress=[]
    def cancel_after_batch(values):progress.append(values);event.set()
    with pytest.raises(InterruptedError,match='cancelled'):
        train_ocr(tmp_path,tmp_path/'cancelled',epochs=2,batch_size=2,image_size=(32,64),cancel_event=event,on_progress=cancel_after_batch)
    assert progress[0]['batch']==1 and progress[0]['epoch']==1
    assert not (tmp_path/'cancelled/best_model.pt').exists()


def test_manifest_writer_pins_explicit_text_and_source_hashes(tmp_path: Path):
    rows = _labeled_images(tmp_path)
    manifest = write_ocr_manifest(tmp_path, rows)
    saved = json.loads((tmp_path / "ocr.json").read_text(encoding="utf-8"))

    assert saved["version"] == 1
    assert saved["samples"][0]["text"] == "A"
    assert saved["samples"][0]["source_sha256"] == hashlib.sha256(
        (tmp_path / "images/train_A.png").read_bytes()
    ).hexdigest()
    assert manifest.alphabet == "AB"
    assert manifest.provenance["split_counts"] == {"train": 2, "val": 2, "test": 2}
    assert manifest.provenance["dataset_sha256"].startswith("sha256:")


def test_manifest_rejects_missing_labels_changed_source_and_cross_split_copy(tmp_path: Path):
    with pytest.raises(ValueError, match="explicit.*ocr.json"):
        load_ocr_manifest(tmp_path)

    rows = _labeled_images(tmp_path)
    write_ocr_manifest(tmp_path, rows)
    _image(tmp_path, "train_A.png", mark="B")
    with pytest.raises(ValueError, match="SHA-256"):
        load_ocr_manifest(tmp_path)

    rows = _labeled_images(tmp_path)
    write_ocr_manifest(tmp_path, rows)
    (tmp_path / "images/val_A.png").write_bytes((tmp_path / "images/train_A.png").read_bytes())
    data = json.loads((tmp_path / "ocr.json").read_text(encoding="utf-8"))
    data["samples"][2]["source_sha256"] = hashlib.sha256(
        (tmp_path / "images/val_A.png").read_bytes()
    ).hexdigest()
    (tmp_path / "ocr.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        load_ocr_manifest(tmp_path)


def test_manifest_rejects_path_escape_and_unseen_validation_character(tmp_path: Path):
    rows = _labeled_images(tmp_path, test=False)
    rows[0]["image"] = "../outside.png"
    with pytest.raises(ValueError, match="inside"):
        write_ocr_manifest(tmp_path, rows)

    rows = _labeled_images(tmp_path, test=False)
    rows[2]["text"] = "Z"
    with pytest.raises(ValueError, match="training alphabet"):
        write_ocr_manifest(tmp_path, rows)


def test_manifest_reports_invalid_split_type_as_validation_error(tmp_path: Path):
    rows = _labeled_images(tmp_path, test=False)
    write_ocr_manifest(tmp_path, rows)
    data = json.loads((tmp_path / "ocr.json").read_text(encoding="utf-8"))
    data["samples"][0]["split"] = ["train"]
    (tmp_path / "ocr.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="split"):
        load_ocr_manifest(tmp_path)


def test_dataset_rechecks_source_after_manifest_load(tmp_path: Path):
    rows = _labeled_images(tmp_path, test=False)
    manifest = write_ocr_manifest(tmp_path, rows)
    dataset = OCRDataset(manifest, split="train", image_size=(32, 64))
    image, target = dataset[0]
    assert image.shape == (1, 32, 64)
    assert target == [1]

    _image(tmp_path, "train_A.png", mark="B")
    with pytest.raises(ValueError, match="changed after"):
        dataset[0]


def test_train_evaluate_and_infer_from_source_linked_checkpoint(tmp_path: Path):
    rows = _labeled_images(tmp_path)
    manifest = write_ocr_manifest(tmp_path, rows)
    model_dir = tmp_path / "model"
    result = train_ocr(
        tmp_path, model_dir, epochs=120, batch_size=2,
        image_size=(32, 24), learning_rate=0.003, device="cpu", seed=7,
    )
    checkpoint = model_dir / "best_model.pt"
    metadata = json.loads((model_dir / "model_meta.json").read_text(encoding="utf-8"))

    assert checkpoint.is_file()
    assert result["task"] == "ocr" and result["epochs_completed"] == 120
    assert len(result["training_loss_history"]) == 120
    assert all(np.isfinite(value) for value in result["training_loss_history"])
    assert result["training_loss_history"][-1] < result["training_loss_history"][0]
    assert metadata["dataset_provenance"] == manifest.provenance

    evaluation = evaluate_ocr_checkpoint(checkpoint, tmp_path, split="test", device="cpu")
    prediction = predict_ocr(checkpoint, tmp_path / "images/test_A.png", device="cpu")
    assert evaluation["sample_count"] == 2
    assert evaluation["exact_match_accuracy"] == 1
    assert evaluation["character_error_rate"] == 0
    assert prediction["text"] == evaluation["samples"][0]["predicted_text"]
    assert prediction["source_sha256"] == hashlib.sha256(
        (tmp_path / "images/test_A.png").read_bytes()
    ).hexdigest()
    assert prediction["model_sha256"] == hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    assert prediction["dataset_sha256"] == manifest.provenance["dataset_sha256"]

    _image(tmp_path, "test_A.png", mark="B")
    with pytest.raises(ValueError, match="SHA-256"):
        evaluate_ocr_checkpoint(checkpoint, tmp_path, split="test", device="cpu")


def test_explicit_corrected_label_evaluation_preserves_checkpoint_alphabet(tmp_path):
    rows = _labeled_images(tmp_path)
    manifest = write_ocr_manifest(tmp_path, rows)
    output = tmp_path / "candidate"
    train_ocr(tmp_path, output, epochs=1, image_size=(32, 24))
    rows[-1]["text"] = "A"
    write_ocr_manifest(tmp_path, rows)
    with pytest.raises(ValueError, match="provenance"):
        evaluate_ocr_checkpoint(output / "best_model.pt", tmp_path)
    result = evaluate_ocr_checkpoint(output / "best_model.pt", tmp_path, allow_dataset_revision=True)
    assert result["training_dataset_sha256"] == manifest.provenance["dataset_sha256"]
    assert result["dataset_revision_changed"] is True
    rows[-1]["text"] = "C"
    rows[0]["text"] = "AC"
    write_ocr_manifest(tmp_path, rows)
    with pytest.raises(ValueError, match="alphabet"):
        evaluate_ocr_checkpoint(output / "best_model.pt", tmp_path, allow_dataset_revision=True)

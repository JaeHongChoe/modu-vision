"""Paired enhancement is a trained candidate with immutable image provenance."""
import importlib
import hashlib
import json
import threading
from pathlib import Path

import numpy as np
from PIL import Image
import pytest


def engine():
    return importlib.import_module("backend.engine.enhancement")


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for i in range(6):
        pixels = np.full((40, 48, 3), 40 + 25 * i, np.uint8)
        pixels[8:22, 12:30] = (i * 31, 140, 220)
        Image.fromarray(pixels).save(root / f"sample_{i}.png")
    return root


def test_preparation_pins_real_targets_and_keeps_source_untouched(source, tmp_path):
    before = {p.name: p.read_bytes() for p in source.iterdir()}
    manifest = engine().prepare_enhancement(source, tmp_path / "paired", seed=7)
    assert manifest["mode"] == "synthetic_gaussian"
    assert len(manifest["records"]) == 6
    assert {r["split"] for r in manifest["records"]} == {"train", "val", "test"}
    assert all(len(r["source_sha256"]) == 64 for r in manifest["records"])
    assert before == {p.name: p.read_bytes() for p in source.iterdir()}
    for row in manifest["records"]:
        target = np.asarray(Image.open(tmp_path / "paired" / row["target"]))
        original = np.asarray(Image.open(source / row["source_relative_path"]))
        np.testing.assert_array_equal(target, original)


def test_manifest_rejects_changed_input_and_target(source, tmp_path):
    root = tmp_path / "paired"
    manifest = engine().prepare_enhancement(source, root)
    (root / manifest["records"][0]["input"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed|hash"):
        engine().load_enhancement_manifest(root)


def test_manifest_rejects_escape_and_cross_split_duplicates(source, tmp_path):
    root = tmp_path / "paired"
    engine().prepare_enhancement(source, root)
    raw = json.loads((root / "pairs.json").read_text())
    raw["records"][0]["input"] = "../source/sample_0.png"
    (root / "pairs.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="relative|outside|escape"):
        engine().load_enhancement_manifest(root)


def test_train_evaluate_and_predict_preserve_geometry_and_checkpoint_identity(source, tmp_path):
    root = tmp_path / "paired"
    engine().prepare_enhancement(source, root)
    output = tmp_path / "model"
    result = engine().train_enhancement(root, output, epochs=1, batch_size=2)
    assert result["task"] == "enhancement"
    assert result["epochs_completed"] == 1
    assert (output / "best_model.pt").is_file()
    assert result["provenance"]["dataset_sha256"]
    image = np.asarray(Image.open(source / "sample_0.png").convert("RGB"))
    prediction = engine().predict_enhancement(output / "best_model.pt", image)
    assert prediction.shape == image.shape and prediction.dtype == np.uint8
    metrics = engine().evaluate_enhancement(output / "best_model.pt", root)
    assert metrics["sample_count"] >= 1
    assert np.isfinite(metrics["output_mse"])
    assert metrics["model_sha256"]
    assert metrics["dataset_sha256"] == result["provenance"]["dataset_sha256"]


def test_unavailable_device_and_invalid_rgb_fail_explicitly(source, tmp_path, monkeypatch):
    root = tmp_path / "paired"
    engine().prepare_enhancement(source, root)
    monkeypatch.setattr(engine().torch.cuda, "is_available", lambda: False)
    with pytest.raises(ValueError, match="CUDA|cuda"):
        engine().train_enhancement(root, tmp_path / "model", device="cuda")


def test_dataset_identity_survives_source_path_relocation(source, tmp_path):
    root = tmp_path / "paired"
    engine().prepare_enhancement(source, root)
    original = engine().load_enhancement_manifest(root)["provenance"]["dataset_sha256"]
    raw = json.loads((root / "pairs.json").read_text())
    raw["source_dataset_path"] = str(tmp_path / "restored" / "source")
    (root / "pairs.json").write_text(json.dumps(raw, indent=4))
    assert engine().load_enhancement_manifest(root)["provenance"]["dataset_sha256"] == original
    raw["records"][0]["split"] = "val" if raw["records"][0]["split"] != "val" else "train"
    (root / "pairs.json").write_text(json.dumps(raw))
    assert engine().load_enhancement_manifest(root)["provenance"]["dataset_sha256"] != original


def test_cancellation_stops_before_checkpoint_and_reports_batch_progress(source, tmp_path):
    root = tmp_path / 'paired'
    engine().prepare_enhancement(source, root)
    event = threading.Event()
    progress = []
    def callback(epoch, total, loss):
        progress.append((epoch, total, loss))
        event.set()
    with pytest.raises(InterruptedError, match='cancel'):
        engine().train_enhancement(root, tmp_path / 'cancelled', epochs=5, cancel_event=event, on_progress=callback)
    assert len(progress) == 1
    assert not (tmp_path / 'cancelled' / 'model_meta.json').exists()


def test_evaluation_matches_full_resolution_tiled_uint8_predictions(tmp_path):
    module = engine()
    source = tmp_path / "source"
    source.mkdir()
    for index in range(3):
        pixels = np.random.default_rng(index).integers(40, 210, (513, 769, 3), dtype=np.uint8)
        Image.fromarray(pixels).save(source / f"source_{index}.png")
    paired = tmp_path / "paired"
    manifest = module.prepare_enhancement(source, paired, noise_sigma=15)
    model = module.RGBDenoiser()
    with module.torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        # Fractional pixels distinguish deployed rounding from float validation.
        model.residual[-1].bias.fill_(1.4 / 255)
    checkpoint = tmp_path / "best_model.pt"
    module.torch.save({"task": "enhancement", "version": 1,
                       "model_state_dict": model.state_dict(), "provenance": manifest["provenance"]}, checkpoint)
    row = next(row for row in manifest["records"] if row["split"] == "test")
    image = np.array(Image.open(paired / row["input"]).convert("RGB"))
    target = np.array(Image.open(paired / row["target"]).convert("RGB"))
    prediction = module.predict_enhancement(checkpoint, image)
    input_mse = float(np.mean(((image.astype(np.float64) - target) / 255) ** 2))
    output_mse = float(np.mean(((prediction.astype(np.float64) - target) / 255) ** 2))
    result = module.evaluate_enhancement(checkpoint, paired)
    assert result["input_mse"] == pytest.approx(input_mse, abs=1e-12)
    assert result["output_mse"] == pytest.approx(output_mse, abs=1e-12)
    assert result["input_psnr"] == pytest.approx(-10 * np.log10(input_mse))
    assert result["output_psnr"] == pytest.approx(-10 * np.log10(output_mse))
    assert result["evaluation_geometry"] == {
        "mode": "source_resolution", "resize": False, "tile_size": 512, "tile_overlap": 4,
        "output_dtype": "uint8", "output_quantization": "round_clip_0_255",
    }
    evidence = result["samples"][0]
    assert (evidence["source_width"], evidence["source_height"]) == (769, 513)
    assert evidence["input_sha256"] == row["input_sha256"]
    assert evidence["target_sha256"] == row["target_sha256"]
    assert evidence["source_sha256"] == row["source_sha256"]
    assert evidence["output_pixels_sha256"] == hashlib.sha256(prediction.tobytes()).hexdigest()
    assert evidence["output_mse"] == pytest.approx(output_mse, abs=1e-12)


def test_explicit_revised_pairs_evaluation_requires_original_source(source, tmp_path):
    original = tmp_path / "paired"
    manifest = engine().prepare_enhancement(source, original, noise_sigma=15)
    output = tmp_path / "model"
    metadata = engine().train_enhancement(original, output)
    assert metadata["training_validation_geometry"]["approval_evidence"] is False
    revised = tmp_path / "revised"
    engine().prepare_enhancement(source, revised, noise_sigma=10)
    with pytest.raises(ValueError, match="differs"):
        engine().evaluate_enhancement(output / "best_model.pt", revised)
    result = engine().evaluate_enhancement(output / "best_model.pt", revised, allow_dataset_revision=True)
    assert result["dataset_revision_changed"] is True
    assert result["training_dataset_sha256"] == manifest["provenance"]["dataset_sha256"]
    raw = json.loads((revised / "pairs.json").read_text())
    raw["source_dataset_path"] = str(tmp_path / "different_source")
    (revised / "pairs.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="source"):
        engine().evaluate_enhancement(output / "best_model.pt", revised, allow_dataset_revision=True)

"""Current source data must be distinguishable from earlier training inputs."""

import json
from pathlib import Path
from types import SimpleNamespace

from backend.api import routes_training
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.dataset_fingerprint import fingerprint_dataset


def test_fingerprint_changes_for_source_image_label_studio_save_and_split(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    image = source / "image.jpg"
    image.write_bytes(b"source image one")
    label = source / "image.json"
    label.write_text('{"shapes": []}', encoding="utf-8")
    split = tmp_path / "split.json"
    studio_root = tmp_path / "annotations"
    kwargs = {"studio_root": studio_root, "split_manifest": split}

    original = fingerprint_dataset(source, **kwargs)
    assert fingerprint_dataset(source, **kwargs) == original

    image.write_bytes(b"source image changed and longer")
    image_changed = fingerprint_dataset(source, **kwargs)
    assert image_changed != original

    label.write_text('{"shapes": [1]}', encoding="utf-8")
    label_changed = fingerprint_dataset(source, **kwargs)
    assert label_changed != image_changed

    studio = dataset_annotation_dir(source, studio_root)
    studio.mkdir(parents=True)
    (studio / "image.json").write_text('{"annotations": [{"label": "NG"}]}', encoding="utf-8")
    studio_changed = fingerprint_dataset(source, **kwargs)
    assert studio_changed != label_changed

    split.write_text(json.dumps({"assignments": {"image.jpg": "train"}}), encoding="utf-8")
    split_changed = fingerprint_dataset(source, **kwargs)
    assert split_changed != studio_changed
    split.write_text(json.dumps({"assignments": {"image.jpg": "val"}}), encoding="utf-8")
    assert fingerprint_dataset(source, **kwargs) != split_changed


def test_fingerprint_ignores_unrelated_source_files(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"image")
    original = fingerprint_dataset(source)
    (source / "notes.md").write_text("operator notes", encoding="utf-8")
    assert fingerprint_dataset(source) == original


def test_training_receipt_records_source_version(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.jpg").write_bytes(b"original")
    captured = {}

    def capture_start(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"])

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture_start)
    response = routes_training.start_training(routes_training.TrainingStartRequest(
        task="classification", dataset_path=str(source), output_dir=str(tmp_path / "models"),
    ))
    assert response["status"] == "started"
    assert captured["source_dataset_path"] == str(source)
    assert captured["dataset_fingerprint"] == fingerprint_dataset(source)

    record = routes_training.JobRecord(
        job_id=response["job_id"], task="classification", preset="fast",
        dataset_path=str(source), output_dir=response["output_dir"], status="completed",
        source_dataset_path=captured["source_dataset_path"],
        dataset_fingerprint=captured["dataset_fingerprint"],
    )
    routes_training._write_job_receipt(record)
    saved = json.loads((Path(response["output_dir"]) / "job_receipt.json").read_text())
    assert saved["source_dataset_path"] == str(source)
    assert saved["dataset_fingerprint"] == fingerprint_dataset(source)

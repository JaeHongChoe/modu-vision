"""The Step 1 gallery must show the images counted by task-aware import."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import create_app


def _client(tmp_path):
    app = create_app(project_dir=str(tmp_path / "project"))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8)).save(path)


def _gallery(client, root, task, split=None):
    params = {"folder_path": str(root), "task": task}
    if split is not None:
        params["split"] = split
    response = client.get("/api/dataset/images", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_nested_segmentation_gallery_lists_only_images_and_filters_physical_split(tmp_path):
    source = tmp_path / "generated"
    for partition in ("train", "val"):
        _image(source / "segmentation" / "images" / partition / f"{partition}.png")
        _image(source / "segmentation" / "masks" / partition / f"{partition}_mask.png")
    client = _client(tmp_path)

    imported = client.post("/api/dataset/import", json={
        "folder_path": str(source), "task": "segmentation", "validate_images": False,
    })
    assert imported.status_code == 200, imported.text
    assert imported.json()["split"] == {"train": 1, "val": 1, "test": 0}
    assert imported.json()["split_supported"] is False
    assert imported.json()["split_unavailable_reason"]

    for partition, expected in ((None, {"train", "val"}), ("train", {"train"}), ("val", {"val"})):
        gallery = _gallery(client, source, "segmentation", partition)
        assert gallery["total"] == len(expected)
        assert {item["split"] for item in gallery["items"]} == expected
        assert all("/segmentation/images/" in item["file_path"] for item in gallery["items"])
        assert all("/masks/" not in item["file_path"] for item in gallery["items"])


def test_nested_detection_gallery_uses_images_train_val_and_import_capability(tmp_path):
    source = tmp_path / "generated"
    detection = source / "detection"
    for partition in ("train", "val"):
        _image(detection / "images" / partition / f"{partition}.png")
        (detection / f"annotations_{partition}.json").write_text(json.dumps({
            "images": [{"id": 1, "file_name": f"{partition}.png", "width": 8, "height": 8}],
            "annotations": [], "categories": [{"id": 1, "name": "defect"}],
        }), encoding="utf-8")
    client = _client(tmp_path)

    imported = client.post("/api/dataset/import", json={
        "folder_path": str(source), "task": "detection", "validate_images": False,
    })
    assert imported.status_code == 200, imported.text
    assert imported.json()["split"] == {"train": 1, "val": 1, "test": 0}
    assert imported.json()["split_supported"] is False

    for partition, expected in ((None, {"train", "val"}), ("train", {"train"}), ("val", {"val"})):
        gallery = _gallery(client, source, "detection", partition)
        assert gallery["total"] == len(expected)
        assert {item["split"] for item in gallery["items"]} == expected
        assert all("/detection/images/" in item["file_path"] for item in gallery["items"])


def test_classification_import_reports_split_supported(tmp_path):
    source = tmp_path / "classification"
    _image(source / "OK" / "ok.png")
    _image(source / "NG" / "ng.png")
    client = _client(tmp_path)

    imported = client.post("/api/dataset/import", json={
        "folder_path": str(source), "task": "classification", "validate_images": False,
    })

    assert imported.status_code == 200, imported.text
    assert imported.json()["split_supported"] is True
    assert imported.json()["split_unavailable_reason"] is None


def test_anomaly_gallery_matches_loader_partition_when_val_folder_is_absent(tmp_path):
    source = tmp_path / "generated"
    anomaly = source / "anomaly"
    for index in range(2):
        _image(anomaly / "train" / "good" / f"train_{index}.png")
        _image(anomaly / "test" / "good" / f"good_{index}.png")
    for index in range(3):
        _image(anomaly / "test" / "defect" / f"defect_{index}.png")
        _image(anomaly / "ground_truth" / "defect" / f"defect_{index}_mask.png")
    client = _client(tmp_path)

    imported = client.post("/api/dataset/import", json={
        "folder_path": str(source), "task": "anomaly", "validate_images": False,
    })
    assert imported.status_code == 200, imported.text
    assert imported.json()["split"] == {"train": 2, "val": 3, "test": 2}

    all_paths = set()
    for partition, expected_count in imported.json()["split"].items():
        gallery = _gallery(client, source, "anomaly", partition)
        assert gallery["total"] == expected_count
        assert {item["split"] for item in gallery["items"]} == {partition}
        assert all("/ground_truth/" not in item["file_path"] for item in gallery["items"])
        paths = {item["file_path"] for item in gallery["items"]}
        assert not all_paths.intersection(paths)
        all_paths.update(paths)
    assert _gallery(client, source, "anomaly")["total"] == 7

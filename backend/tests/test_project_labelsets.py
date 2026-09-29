"""An active label set changes the labels used by annotation and import flows."""

import json
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from backend.main import create_app


def _client(root: Path) -> TestClient:
    app = create_app(project_dir=str(root))
    return TestClient(app, headers={"X-Vision-Token": app.state.api_token})


def _save(client: TestClient, image: Path, label: str) -> None:
    result = client.post("/api/annotations/save", json={
        "image_id": image.stem, "image_path": str(image), "image_width": 24,
        "image_height": 24, "annotations": [{"type": "bbox", "label": label,
                                          "bbox": [1, 1, 10, 10]}],
    })
    assert result.status_code == 200, result.text


def _labels(client: TestClient, image: Path) -> list[str]:
    result = client.get(f"/api/annotations/{image.stem}", params={"file_path": str(image)})
    assert result.status_code == 200, result.text
    return [item["label"] for item in result.json()["annotations"]]


def test_labelsets_clone_switch_and_survive_backend_restart(tmp_path: Path):
    source = tmp_path / "images"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (24, 24), "white").save(image)
    client = _client(tmp_path / "projects")
    project = client.post("/api/project/create", json={"name": "Sets", "task": "detection"}).json()
    assert client.put("/api/project/update", json={"source_dataset_dir": str(source)}).status_code == 200
    _save(client, image, "Original review")

    created = client.post("/api/project/labelsets", json={"name": "Second review"})
    assert created.status_code == 200, created.text
    second = created.json()
    assert second["source_id"] == "default"
    assert client.put(f"/api/project/labelsets/{second['id']}/activate").status_code == 200
    assert _labels(client, image) == ["Original review"]
    _save(client, image, "Second review")
    assert _labels(client, image) == ["Second review"]
    imported = client.post("/api/dataset/import", json={"folder_path": str(source), "task": "detection"})
    assert imported.status_code == 200, imported.text
    assert "Second review" in imported.json()["classes"]

    restarted = _client(tmp_path / "projects")
    assert restarted.get("/api/project/current").json()["active_labelset_id"] == second["id"]
    assert _labels(restarted, image) == ["Second review"]
    assert restarted.put("/api/project/labelsets/default/activate").status_code == 200
    assert _labels(restarted, image) == ["Original review"]
    assert not (source / "sample.json").exists()
    assert Path(project["annotations_dir"]).is_dir()


def test_labelset_switch_rejects_unknown_id_without_changing_active_set(tmp_path: Path):
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Sets"})
    before = client.get("/api/project/labelsets").json()
    response = client.put("/api/project/labelsets/ls_missing/activate")
    assert response.status_code == 404
    assert client.get("/api/project/labelsets").json() == before


def test_dataset_versions_follow_active_labelset_and_block_cross_set_restore(tmp_path: Path):
    source = tmp_path / "images"
    source.mkdir()
    image = source / "sample.png"
    Image.new("RGB", (24, 24), "white").save(image)
    client = _client(tmp_path / "projects")
    client.post("/api/project/create", json={"name": "Sets", "task": "detection"})
    client.put("/api/project/update", json={"source_dataset_dir": str(source)})
    _save(client, image, "First")
    first = client.post("/api/dataset/versions", json={"name": "First"})
    assert first.status_code == 200, first.text
    second_id = client.post("/api/project/labelsets", json={"name": "Second"}).json()["id"]
    client.put(f"/api/project/labelsets/{second_id}/activate")
    _save(client, image, "Second")
    second = client.post("/api/dataset/versions", json={"name": "Second"})
    assert second.status_code == 200, second.text
    listed = client.get("/api/dataset/versions").json()["versions"]
    assert [row["id"] for row in listed] == [second.json()["id"]]
    assert client.post(f"/api/dataset/versions/{first.json()['id']}/restore").status_code == 409
    assert _labels(client, image) == ["Second"]
    client.put("/api/project/labelsets/default/activate")
    assert [row["id"] for row in client.get("/api/dataset/versions").json()["versions"]] == [first.json()["id"]]

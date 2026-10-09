"""Blank names refuse before labelset state exists; nonblank contracts stay intact.

Root executes these real request/direct-producer controls. This Source author
has not imported the product or run any test.
"""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.engine.project_labelsets import create_labelset
from backend.main import create_app


BLANK_NAMES = ("   ", "\t\r\n", "\u2003")


def snapshot(root: Path):
    if not root.exists():
        return None
    files = {}
    directories = []
    for item in sorted(root.rglob("*")):
        assert not item.is_symlink()
        info = item.stat()
        identity = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        relative = item.relative_to(root).as_posix()
        if item.is_dir():
            directories.append((relative, identity))
        else:
            files[relative] = (identity, hashlib.sha256(item.read_bytes()).hexdigest())
    info = root.stat()
    root_identity = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                     info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    return root_identity, directories, files


@pytest.fixture
def client_project(tmp_path: Path):
    source = tmp_path / "original-source"
    source.mkdir()
    Image.new("RGB", (8, 8), (11, 22, 33)).save(source / "sample.png")
    app = create_app(project_dir=str(tmp_path / "workspace"))
    with TestClient(app, headers={"X-Vision-Token": app.state.api_token}) as client:
        created = client.post("/api/project/create", json={"name": "Blank name boundary", "task": "classification"})
        assert created.status_code == 200, created.text
        project = created.json()
        updated = client.put("/api/project/update", json={"source_dataset_dir": str(source)})
        assert updated.status_code == 200, updated.text
        original = client.get("/api/project/labelsets")
        assert original.status_code == 200, original.text
        yield client, Path(project["project_dir"]), source, original.json()


@pytest.mark.parametrize("name", BLANK_NAMES)
def test_request_blank_name_refuses_without_project_or_source_writes(client_project, name):
    client, project, source, original = client_project
    before = snapshot(project), snapshot(source)
    response = client.post("/api/project/labelsets", json={"name": name})
    assert response.status_code == 422, response.text
    assert any(row["loc"] == ["body", "name"] for row in response.json()["detail"])
    assert (snapshot(project), snapshot(source)) == before
    retained = client.get("/api/project/labelsets")
    assert retained.status_code == 200, retained.text
    assert retained.json() == original


@pytest.mark.parametrize("name", BLANK_NAMES)
def test_direct_blank_name_refuses_before_registry_or_directory_creation(tmp_path: Path, name):
    project = tmp_path / "never-created-project"
    before = snapshot(tmp_path)
    with pytest.raises(ValueError, match="Label set name.*blank"):
        create_labelset(project, name)
    assert not project.exists()
    assert snapshot(tmp_path) == before


def test_direct_blank_name_is_primary_before_malformed_existing_registry(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "labelsets.json").write_bytes(b"{not valid JSON")
    (project / "original.bin").write_bytes(b"preserve original bytes")
    before = snapshot(project)
    with pytest.raises(ValueError, match="Label set name.*blank"):
        create_labelset(project, " \t ")
    assert snapshot(project) == before


def test_direct_blank_name_does_not_resolve_foreign_project_path():
    class ProjectNotYetAuthorized:
        def __fspath__(self):
            raise AssertionError("Blank request must refuse before path resolution")
    with pytest.raises(ValueError, match="Label set name.*blank"):
        create_labelset(ProjectNotYetAuthorized(), " ")


@pytest.mark.parametrize("name", ("", " " + "x" * 119 + " "))
def test_request_original_raw_length_bounds_remain_before_mutation(client_project, name):
    client, project, source, original = client_project
    before = snapshot(project), snapshot(source)
    response = client.post("/api/project/labelsets", json={"name": name})
    assert response.status_code == 422, response.text
    assert any(row["loc"] == ["body", "name"] for row in response.json()["detail"])
    assert (snapshot(project), snapshot(source)) == before
    assert client.get("/api/project/labelsets").json() == original


def test_nonblank_request_keeps_existing_trimmed_name_and_copy_contract(client_project):
    client, project, source, original = client_project
    annotations = project / "annotations"
    annotations.mkdir(exist_ok=True)
    original_label = b'{"annotations": [], "review": "retained"}\n'
    (annotations / "sample.json").write_bytes(original_label)
    before_source, before_labels = snapshot(source), snapshot(annotations)
    response = client.post("/api/project/labelsets", json={"name": "  Second review\t"})
    assert response.status_code == 200, response.text
    copied = response.json()
    assert copied["name"] == "Second review"
    assert copied["source_id"] == "default"
    assert (project / "labelsets" / copied["id"] / "annotations" / "sample.json").read_bytes() == original_label
    assert snapshot(source) == before_source
    assert snapshot(annotations) == before_labels
    retained = client.get("/api/project/labelsets").json()
    assert retained["active_id"] == original["active_id"] == "default"
    assert retained["labelsets"][:-1] == original["labelsets"]
    assert retained["labelsets"][-1] == copied


def test_direct_nonblank_preserves_existing_registry_error(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "labelsets.json").write_bytes(b"{not valid JSON")
    before = snapshot(project)
    with pytest.raises(ValueError, match="Invalid label set registry"):
        create_labelset(project, "Valid review")
    assert snapshot(project) == before


def test_direct_nonblank_string_subclass_keeps_one_original_strip_call(tmp_path: Path):
    class ObservedName(str):
        calls = 0

        def strip(self):
            self.calls += 1
            return super().strip()

    name = ObservedName("  Second review\t")
    created = create_labelset(tmp_path / "project", name)
    assert name.calls == 1
    assert created["name"] == "Second review"
    assert created["source_id"] == "default"

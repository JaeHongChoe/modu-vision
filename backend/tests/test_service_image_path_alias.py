"""Path aliases retain image identity without permitting dataset escapes."""
from pathlib import Path

import pytest
from PIL import Image

from backend.engine import dataset_metadata as dm
from backend.tests.test_dataset_metadata_api import client_workspace


def symlink(target, link, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f"Symlink creation unavailable: {error}")
    return link


def test_ancestor_alias_api_preserves_uuid_and_review(client_workspace, tmp_path):
    client, project, source = client_workspace
    alias = symlink(source.parent, tmp_path / "ancestor-alias", directory=True)
    canonical = client.get("/api/dataset/metadata/image", params={"image_path": str(source / "a.png")})
    assert canonical.status_code == 200, canonical.text
    row = canonical.json()
    approved = client.patch("/api/dataset/metadata/" + row["image_uuid"], json={
        "expected_revision": row["revision"], "actor": "Alias reviewer",
        "changes": {"workflow_state": "approved"},
    })
    assert approved.status_code == 200, approved.text
    aliased = client.get("/api/dataset/metadata/image", params={"image_path": str(alias / source.name / "a.png")})
    assert aliased.status_code == 200, aliased.text
    assert aliased.json()["image_uuid"] == row["image_uuid"]
    assert aliased.json()["revision"] == approved.json()["revision"]
    assert aliased.json()["reviewer"] == "Alias reviewer"
    # Reopening the same dataset through its alias retains the existing ledger.
    client.put("/api/project/update", json={"source_dataset_dir": str(alias / source.name)})
    reopened = client.get("/api/dataset/metadata/image", params={"image_path": str(source / "a.png")})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["image_uuid"] == row["image_uuid"]


def test_visible_internal_symlink_keeps_its_relative_identity(client_workspace):
    client, project, source = client_workspace
    link = symlink(source / "a.png", source / "visible-link.png")
    first = dm.metadata_for_path(project["project_dir"], source, link)
    assert first["relative_path"] == "visible-link.png"
    original = dm.metadata_for_path(project["project_dir"], source, source / "a.png")
    assert first["image_uuid"] != original["image_uuid"]
    approved = dm.update_metadata(project["project_dir"], source, first["image_uuid"], first["revision"],
                                  "Link reviewer", {"workflow_state": "approved"})
    again = dm.metadata_for_path(project["project_dir"], source, link)
    assert again["image_uuid"] == first["image_uuid"]
    assert again["revision"] == approved["revision"]
    assert again["reviewer"] == "Link reviewer"


def test_symlink_escape_is_rejected_by_production_metadata_api(client_workspace, tmp_path):
    client, project, source = client_workspace
    outside = tmp_path / "outside.png"
    Image.new("RGB", (4, 4), "red").save(outside)
    link = symlink(outside, source / "escape.png")
    response = client.get("/api/dataset/metadata/image", params={"image_path": str(link)})
    assert response.status_code == 422, response.text
    assert "inside the selected dataset" in response.json()["detail"]


def test_subtree_ancestor_alias_retains_visible_symlink_review(client_workspace, tmp_path):
    client, project, source = client_workspace
    nested = source / "nested"
    nested.mkdir()
    Image.new("RGB", (4, 4), "blue").save(nested / "a.png")
    link = symlink(nested / "a.png", nested / "visible-link.png")
    alias = symlink(nested, tmp_path / "subtree-alias", directory=True)
    original = client.get("/api/dataset/metadata/image", params={"image_path": str(link)}).json()
    approved = client.patch("/api/dataset/metadata/" + original["image_uuid"], json={
        "expected_revision": original["revision"], "actor": "Subtree reviewer",
        "changes": {"workflow_state": "approved"},
    })
    assert approved.status_code == 200, approved.text
    aliased = client.get("/api/dataset/metadata/image", params={"image_path": str(alias / link.name)})
    assert aliased.status_code == 200, aliased.text
    assert aliased.json()["relative_path"] == "nested/visible-link.png"
    assert aliased.json()["image_uuid"] == original["image_uuid"]
    assert aliased.json()["reviewer"] == "Subtree reviewer"
    assert aliased.json()["revision"] == approved.json()["revision"]

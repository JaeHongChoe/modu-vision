"""Portable remote input must contain the exact prepared bytes and safe paths."""

import hashlib
import io
import json
import tarfile
import threading

import pytest
from PIL import Image

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.labelme_preparation import prepare_labelme_segmentation
from backend.remote.snapshot import (
    SnapshotCancelled,
    SnapshotValidationError,
    build_snapshot,
    extract_snapshot,
    package_worker_bundle,
)


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def test_structured_dataset_snapshot_has_relative_hashes_and_content_only_archive(tmp_path):
    source = tmp_path / "source"
    (source / "images" / "train").mkdir(parents=True)
    (source / "images" / "val").mkdir(parents=True)
    (source / "annotations_train.json").write_text('{"images":[]}', encoding="utf-8")
    (source / "images" / "train" / "a.png").write_bytes(b"train image")
    (source / "images" / "val" / "b.png").write_bytes(b"val image")

    snapshot = build_snapshot(source, tmp_path / "snapshot", threading.Event())

    assert {row.path for row in snapshot.files} == {
        "annotations_train.json", "images/train/a.png", "images/val/b.png",
    }
    assert snapshot.total_bytes == len(b'{"images":[]}') + len(b"train image") + len(b"val image")
    assert next(row for row in snapshot.files if row.path == "images/train/a.png").sha256 == _sha256(b"train image")
    assert snapshot.manifest_sha256 == _sha256(snapshot.manifest_path.read_bytes())
    assert snapshot.archive_sha256 == _sha256(snapshot.archive_path.read_bytes())
    with tarfile.open(snapshot.archive_path, "r:gz") as archive:
        assert all(member.isfile() for member in archive.getmembers())
        assert archive.extractfile("data/images/train/a.png").read() == b"train image"
    extracted = extract_snapshot(snapshot.archive_path, tmp_path / "extracted", snapshot.manifest_sha256)
    assert extracted.data_path.joinpath("images", "val", "b.png").read_bytes() == b"val image"


def test_labelme_prepared_studio_annotation_and_saved_split_are_in_snapshot(tmp_path):
    source = tmp_path / "labelme"
    source.mkdir()
    first = source / "first.jpg"
    second = source / "second.jpg"
    for image in (first, second):
        Image.new("RGB", (64, 64), (40, 40, 40)).save(image)
        image.with_suffix(".json").write_text(json.dumps({
            "imagePath": image.name, "imageWidth": 64, "imageHeight": 64,
            "shapes": [{"label": "old", "shape_type": "polygon", "points": [[4, 4], [10, 4], [8, 10]]}],
        }), encoding="utf-8")
    studio = tmp_path / "studio"
    studio_dir = dataset_annotation_dir(source, studio)
    studio_dir.mkdir(parents=True)
    (studio_dir / "first.json").write_text(json.dumps({
        "annotations": [{"label": "edited", "polygon": [[40, 40], [50, 40], [45, 50]]}],
    }), encoding="utf-8")
    prepared = tmp_path / "prepared"
    prepare_labelme_segmentation(
        source, prepared, image_size=64, annotation_root=studio,
        assignments={str(first.resolve()): "val", str(second.resolve()): "train"},
        require_complete_assignments=True,
    )

    snapshot = build_snapshot(prepared, tmp_path / "snapshot", threading.Event())
    extracted = extract_snapshot(snapshot.archive_path, tmp_path / "received", snapshot.manifest_sha256)
    rows = json.loads((extracted.data_path / "source_manifest.json").read_text(encoding="utf-8"))
    edited = next(row for row in rows if row["source_image"] == str(first))
    assert edited["annotation_source"] == "studio"
    assert edited["split"] == "val"
    assert len(list((extracted.data_path / "images" / "train").glob("*.png"))) == 1
    assert len(list((extracted.data_path / "masks" / "val").glob("*.png"))) == 1


def test_symlinks_are_dereferenced_to_content_even_when_target_is_outside_source(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "image.png").write_bytes(b"external image bytes")
    source = tmp_path / "source"
    (source / "images").mkdir(parents=True)
    (source / "images" / "linked.png").symlink_to(outside / "image.png")
    (source / "linked_directory").symlink_to(outside, target_is_directory=True)

    snapshot = build_snapshot(source, tmp_path / "snapshot", threading.Event())

    assert not (snapshot.data_path / "images" / "linked.png").is_symlink()
    assert (snapshot.data_path / "images" / "linked.png").read_bytes() == b"external image bytes"
    assert (snapshot.data_path / "linked_directory" / "image.png").read_bytes() == b"external image bytes"
    with tarfile.open(snapshot.archive_path, "r:gz") as archive:
        assert all(member.isfile() for member in archive.getmembers())


def test_cancelled_snapshot_leaves_no_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.png").write_bytes(b"image")
    cancel = threading.Event()
    cancel.set()

    with pytest.raises(SnapshotCancelled):
        build_snapshot(source, tmp_path / "snapshot", cancel)
    assert not (tmp_path / "snapshot").exists()


def test_destination_parent_symlink_into_source_is_rejected(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.png").write_bytes(b"image")
    linked_parent = tmp_path / "linked_source"
    linked_parent.symlink_to(source, target_is_directory=True)

    class BoundedCancel:
        calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 5

    with pytest.raises(ValueError, match="outside source"):
        build_snapshot(source, linked_parent / "snapshot", BoundedCancel())
    assert not (source / "snapshot").exists()


@pytest.mark.parametrize("name,kind", [
    ("../escape", "file"),
    ("/absolute", "file"),
    ("data/link", "symlink"),
])
def test_archive_rejects_traversal_and_links(tmp_path, name, kind):
    archive_path = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        member = tarfile.TarInfo(name)
        if kind == "symlink":
            member.type = tarfile.SYMTYPE
            member.linkname = "../../escape"
            archive.addfile(member)
        else:
            member.size = 3
            archive.addfile(member, io.BytesIO(b"bad"))

    with pytest.raises(SnapshotValidationError):
        extract_snapshot(archive_path, tmp_path / "received", "0" * 64)
    assert not (tmp_path / "received").exists()
    assert not (tmp_path / "escape").exists()


def test_manifest_validation_rejects_tampered_data(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "image.png").write_bytes(b"original")
    snapshot = build_snapshot(source, tmp_path / "snapshot", threading.Event())
    archive_path = tmp_path / "tampered.tar.gz"
    with tarfile.open(snapshot.archive_path, "r:gz") as original, tarfile.open(archive_path, "w:gz") as tampered:
        for member in original:
            data = original.extractfile(member).read()
            if member.name == "data/image.png":
                data = b"corrupt!"
            member.size = len(data)
            tampered.addfile(member, io.BytesIO(data))
    with pytest.raises(SnapshotValidationError, match="hash"):
        extract_snapshot(archive_path, tmp_path / "received", snapshot.manifest_sha256)


def test_worker_bundle_excludes_tests_and_caches(tmp_path):
    bundle = package_worker_bundle(tmp_path / "worker.tar.gz")
    with tarfile.open(bundle, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
    assert "backend/remote/worker.py" in names
    assert "backend/engine/trainer.py" in names
    assert "backend/api/routes_evaluation.py" in names
    assert not any("tests/" in name or "__pycache__" in name or name.endswith(".pyc") for name in names)


def test_snapshot_can_omit_local_source_path_sidecar_without_changing_prepared_data(tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    (source / "image.png").write_bytes(b"prepared image")
    (source / "source_manifest.json").write_text('{"source_image":"/Users/private/data.jpg"}')
    snapshot = build_snapshot(
        source, tmp_path / "snapshot", threading.Event(),
        exclude_relative_paths=frozenset({"source_manifest.json"}),
    )
    assert (source / "source_manifest.json").is_file()
    assert not (snapshot.data_path / "source_manifest.json").exists()
    assert (snapshot.data_path / "image.png").read_bytes() == b"prepared image"
    with tarfile.open(snapshot.archive_path, "r:gz") as archive:
        assert "data/source_manifest.json" not in archive.getnames()

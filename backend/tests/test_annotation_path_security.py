"""Annotation writes and deletes must stay within approved locations."""

import pytest
from fastapi import HTTPException

from backend.api import routes_annotation


def test_annotation_image_id_cannot_escape_selected_folder(monkeypatch, tmp_path):
    approved = tmp_path / "approved"
    monkeypatch.setenv("VISION_AI_STUDIO_ANNOTATION_ROOTS", str(approved))
    with pytest.raises(HTTPException) as error:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
            image_id="../escape", annotations=[], output_dir=str(approved),
        ))
    assert error.value.status_code == 422
    assert not (tmp_path / "escape.json").exists()


def test_custom_annotation_folder_needs_explicit_root(monkeypatch, tmp_path):
    approved = tmp_path / "approved"
    outside = tmp_path / "outside"
    monkeypatch.setenv("VISION_AI_STUDIO_ANNOTATION_ROOTS", str(approved))

    with pytest.raises(HTTPException) as error:
        routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
            image_id="sample", annotations=[], output_dir=str(outside),
        ))
    assert error.value.status_code == 403
    assert not outside.exists()

    selected = approved / "operator_annotations"
    saved = routes_annotation.save_annotations(routes_annotation.AnnotationSaveRequest(
        image_id="sample", annotations=[], output_dir=str(selected),
    ))
    assert saved["status"] == "saved"
    assert (selected / "sample.json").is_file()
    assert routes_annotation.get_annotations("sample", dir_path=str(selected))["image_id"] == "sample"
    assert routes_annotation.delete_annotations("sample", dir_path=str(selected))["status"] == "deleted"


def test_delete_rejects_traversal_even_for_approved_folder(monkeypatch, tmp_path):
    approved = tmp_path / "approved"
    approved.mkdir()
    victim = tmp_path / "victim.json"
    victim.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("VISION_AI_STUDIO_ANNOTATION_ROOTS", str(approved))

    with pytest.raises(HTTPException) as error:
        routes_annotation.delete_annotations("../victim", dir_path=str(approved))
    assert error.value.status_code == 422
    assert victim.is_file()

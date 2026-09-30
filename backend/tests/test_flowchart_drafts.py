"""Workbench drafts persist incomplete graphs without activating an inspection."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from backend.api import routes_flowchart as routes
from backend.engine.flowchart_engine import get_single_segmentation_flowchart


def test_draft_roundtrip_is_scoped_and_does_not_publish_version(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    project = {"id": "project-a", "project_dir": str(tmp_path / "project"),
               "source_dataset_dir": str(source), "active_labelset_id": "default"}
    monkeypatch.setattr(routes, "get_current_project", lambda request: project)
    request = SimpleNamespace()
    context = {"project_id": "project-a", "source_dataset_path": str(source.resolve()), "labelset_id": "default"}
    pipeline = get_single_segmentation_flowchart()
    pipeline.nodes[1].data.model_job_id = None
    response = routes.save_flow_draft(routes.FlowDraftSaveRequest(pipeline=pipeline, context=context), request)
    assert response["draft_sha256"] == routes.pipeline_sha256(pipeline)
    assert response["active_version_id"] is None
    loaded = routes.get_flow_draft(request)
    assert loaded["pipeline"] == pipeline.model_dump()
    assert not (Path(project["project_dir"]) / "flowcharts" / "active.json").exists()
    assert not (Path(project["project_dir"]) / "flowcharts" / "versions").exists()
    project["active_labelset_id"] = "other"
    with pytest.raises(HTTPException) as error:
        routes.get_flow_draft(request)
    assert error.value.status_code == 404
    with pytest.raises(HTTPException) as error:
        routes.save_flow_draft(routes.FlowDraftSaveRequest(pipeline=pipeline, context=context), request)
    assert error.value.status_code == 409
    project["active_labelset_id"] = "default"
    assert routes.get_flow_draft(request)["draft_sha256"] == response["draft_sha256"]
    project["id"] = "project-b"
    with pytest.raises(HTTPException):
        routes.get_flow_draft(request)


def test_draft_rejects_corrupt_hash_and_preserves_last_valid_save(tmp_path, monkeypatch):
    project = {"id": "project-a", "project_dir": str(tmp_path),
               "source_dataset_dir": None, "active_labelset_id": "default"}
    monkeypatch.setattr(routes, "get_current_project", lambda request: project)
    request = SimpleNamespace()
    context = {"project_id": "project-a", "source_dataset_path": None, "labelset_id": "default"}
    pipeline = get_single_segmentation_flowchart()
    routes.save_flow_draft(routes.FlowDraftSaveRequest(pipeline=pipeline, context=context), request)
    file = next((tmp_path / "flowcharts" / "drafts").rglob("*.json"))
    raw = json.loads(file.read_text())
    raw["pipeline"]["name"] = "tampered"
    file.write_text(json.dumps(raw))
    with pytest.raises(HTTPException) as error:
        routes.get_flow_draft(request)
    assert error.value.status_code == 409

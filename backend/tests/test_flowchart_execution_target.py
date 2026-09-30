"""Explicit execution hardware is independent of verified model training location."""
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch
from fastapi import HTTPException

from backend.api import routes_flowchart as routes
from backend.engine.flowchart_engine import FlowNode, FlowNodeData, FlowEdge, FlowchartRunRequest, get_single_segmentation_flowchart
from backend.remote import operations


def mixed_models(tmp_path, monkeypatch):
    image = tmp_path / "image.png"
    image.write_bytes(b"verified route fixture")
    project = {"id": "project", "project_dir": str(tmp_path), "models_dir": str(tmp_path / "models"), "source_dataset_dir": str(tmp_path)}
    monkeypatch.setattr(routes, "get_current_project", lambda request: project)
    local = tmp_path / "models" / "enhancement" / ("a" * 32) / "best_model.pt"
    remote = tmp_path / "models" / "job_123_abcdef" / "best_model.pt"
    for file, task in ((local, "enhancement"), (remote, "segmentation")):
        file.parent.mkdir(parents=True)
        torch.save({"task": task, "model_state_dict": {}}, file)
    monkeypatch.setattr(routes, "resolve_specialized_checkpoint", lambda *args: (local, {}))
    monkeypatch.setattr(routes, "trusted_checkpoint", lambda *args, **kwargs: remote)
    monkeypatch.setattr(routes, "_matches_source_dataset", lambda *args: True)
    monkeypatch.setattr(routes.training_job_manager, "get_job", lambda job: None)
    monkeypatch.setattr(operations, "remote_job_context", lambda *args: SimpleNamespace(profile="historical-gpu2"))
    monkeypatch.setattr(operations, "verify_downloaded_checkpoint", lambda *args: None, raising=False)
    monkeypatch.setattr(operations, "run_remote_flowchart", lambda *args, **kwargs: pytest.fail("Historical compute routing used for an explicit local run"))
    pipeline = get_single_segmentation_flowchart("job_123_abcdef")
    input_node = next(node for node in pipeline.nodes if node.data.node_type == "input")
    inspect = next(node for node in pipeline.nodes if node.data.node_type == "inspection")
    pipeline.nodes.append(FlowNode(id="enhance", position={"x": 320, "y": 320}, data=FlowNodeData(
        label="Enhance", node_type="preprocess", model_job_id="a" * 32, params={"operation": "enhancement"})))
    pipeline.edges = [edge for edge in pipeline.edges if edge.source != input_node.id]
    pipeline.edges.extend([FlowEdge(id="enhance-in", source=input_node.id, target="enhance"),
                           FlowEdge(id="enhance-out", source="enhance", target=inspect.id)])
    return image, project, pipeline, local, remote


def test_explicit_local_cpu_runs_verified_mixed_location_models(tmp_path, monkeypatch):
    image, project, pipeline, local, remote = mixed_models(tmp_path, monkeypatch)
    calls = []
    class Engine:
        def __init__(self, *, device): calls.append(str(device))
        def execute(self, **kwargs):
            from backend.engine.flowchart_engine import _VERIFIED_CHECKPOINTS
            assert _VERIFIED_CHECKPOINTS.get() == {("a" * 32, "enhancement"): local.resolve(), ("job_123_abcdef", "segmentation"): remote.resolve()}
            return {"status": "success", "final_verdict": "REVIEW"}
    monkeypatch.setattr(routes, "FlowchartEngine", Engine)
    result = routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
        execution_target="local", device="cpu"), SimpleNamespace())
    assert calls == ["cpu"]
    assert result["execution_target"] == "local"
    assert result["execution_device"] == "cpu"


def test_unavailable_requested_local_device_fails_without_fallback(tmp_path, monkeypatch):
    image, _, pipeline, _, _ = mixed_models(tmp_path, monkeypatch)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(routes, "FlowchartEngine", lambda **kwargs: pytest.fail("Unavailable GPU silently fell back"))
    with pytest.raises(HTTPException) as error:
        routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
            execution_target="local", device="cuda"), SimpleNamespace())
    assert error.value.status_code == 422


def test_selected_compute_transports_all_verified_models_to_explicit_profile(tmp_path, monkeypatch):
    from backend.remote import profiles
    image, project, pipeline, local, remote = mixed_models(tmp_path, monkeypatch)
    profile = SimpleNamespace(id="selected-gpu", name="Selected GPU")
    monkeypatch.setattr(profiles, "get_profile_store", lambda: SimpleNamespace(get=lambda identifier: profile if identifier == profile.id else None))
    monkeypatch.setattr(operations, "RemoteComputeBusy", type("RemoteComputeBusy", (Exception,), {}), raising=False)
    calls = []
    def run(profile_arg, project_arg, graph, checkpoints, selected_image, image_id=None, *, device):
        calls.append((profile_arg.id, device))
        assert project_arg == project
        assert graph == pipeline.model_dump()
        assert checkpoints == {("a" * 32, "enhancement"): local.resolve(), ("job_123_abcdef", "segmentation"): remote.resolve()}
        assert selected_image == image
        return {"status": "success", "execution_target": "selected_compute", "execution_device": device,
                "compute_profile_id": profile_arg.id}
    monkeypatch.setattr(operations, "run_verified_flowchart_on_compute", run, raising=False)
    result = routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
        execution_target="selected_compute", device="cuda", compute_profile_id=profile.id), SimpleNamespace())
    assert calls == [(profile.id, "cuda")]
    assert result["compute_profile_id"] == profile.id
    with pytest.raises(HTTPException) as error:
        routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
            execution_target="selected_compute", device="cuda", compute_profile_id="missing"), SimpleNamespace())
    assert error.value.status_code == 404
    def busy(*args, **kwargs): raise operations.RemoteComputeBusy("GPU reserved")
    monkeypatch.setattr(operations, "run_verified_flowchart_on_compute", busy)
    with pytest.raises(HTTPException) as error:
        routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
            execution_target="selected_compute", device="cuda", compute_profile_id=profile.id), SimpleNamespace())
    assert error.value.status_code == 409


def test_frozen_execution_profile_cannot_be_changed_between_batch_check_and_launch(tmp_path, monkeypatch):
    from backend.remote import profiles
    image, _, pipeline, _, _ = mixed_models(tmp_path, monkeypatch)
    def profile(host):
        return SimpleNamespace(id='selected', model_dump=lambda: {'id':'selected','host':host})
    frozen = profile('original-server')
    store_profile = profile('different-server')
    monkeypatch.setattr(profiles, 'get_profile_store', lambda: SimpleNamespace(get=lambda identifier: store_profile))
    monkeypatch.setattr(operations, 'run_verified_flowchart_on_compute', lambda *args, **kwargs: pytest.fail('Changed server launched'))
    with pytest.raises(HTTPException) as error:
        routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
            execution_target='selected_compute', device='cuda', compute_profile_id='selected'),
            SimpleNamespace(state=SimpleNamespace(frozen_execution_profile=frozen)))
    assert error.value.status_code == 409


def test_execution_request_project_guard_rejects_stale_project_before_model_loading(tmp_path, monkeypatch):
    image, _, pipeline, _, _ = mixed_models(tmp_path, monkeypatch)
    monkeypatch.setattr(routes, 'resolve_specialized_checkpoint', lambda *args: pytest.fail('Stale project model loaded'))
    with pytest.raises(HTTPException) as error:
        routes.run_flowchart(FlowchartRunRequest(image_path=str(image), pipeline=pipeline,
            execution_target='local', device='cpu', project_id='other-project'), SimpleNamespace())
    assert error.value.status_code == 409


def test_saved_mixed_flow_version_counts_enhancement_as_a_model(tmp_path, monkeypatch):
    import json
    _, project, pipeline, _, _ = mixed_models(tmp_path, monkeypatch)
    pipeline.nodes.append(FlowNode(id='roi', position={'x':320,'y':160}, data=FlowNodeData(
        label='Source ROI', node_type='fixed_roi', params={'bbox':[0,0,512,512]})))
    versions = routes._version_dir(Path(project['project_dir']))
    versions.mkdir(parents=True, exist_ok=True)
    (versions / ('d'*32+'.json')).write_text(json.dumps({
        'version_id':'d'*32,'recipe_task':'mixed','source_dataset_path':str(tmp_path),
        'saved_at':'2026-09-30T12:00:00Z','pipeline':pipeline.model_dump(),
    }))
    rows = routes.list_saved_pipelines(str(tmp_path), SimpleNamespace())['pipelines']
    assert len(rows) == 1
    assert rows[0]['node_count'] == 6
    assert rows[0]['model_count'] == 2

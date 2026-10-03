"""Recorded model classes constrain class-bearing rules before a flow is saved."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.api import routes_dataset, routes_flowchart
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.engine.flowchart_engine import FlowEdge, FlowNode, FlowNodeData, get_single_segmentation_flowchart
from backend.main import create_app


@pytest.fixture
def flow_api(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    source = tmp_path / "source"
    source.mkdir()
    Image.new("RGB", (32, 32), "white").save(source / "part.png")
    fingerprint = fingerprint_dataset(
        source,
        studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
        split_manifest=routes_dataset._split_manifest_file(source),
    )
    app = create_app(project_dir=str(tmp_path / "workspaces"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    created = client.post("/api/project/create", json={"name": "Class rules", "project_dir": str(tmp_path / "project")})
    assert created.status_code == 200, created.text
    project = client.get("/api/project/current").json()

    def model(job_id="job_classes", task="segmentation", metadata=None):
        directory = Path(project["models_dir"]) / job_id
        (directory / "dataset").mkdir(parents=True, exist_ok=True)
        (directory / "best_model.pt").write_bytes(b"checkpoint not loaded while saving")
        (directory / "model_meta.json").write_text(json.dumps(
            {"task": task, "classes": ["background", "scratch", "chip"]} if metadata is None else metadata
        ), encoding="utf-8")
        (directory / "job_receipt.json").write_text(json.dumps({
            "status": "completed", "task": task, "source_dataset_path": str(source),
            "dataset_fingerprint": fingerprint, "dataset_path": str(directory / "dataset"),
        }), encoding="utf-8")
        return directory

    model()
    endpoint = "/api/flowchart/pipeline"

    def save(graph):
        return client.post(endpoint, params={"source_dataset_path": str(source)}, json=graph.model_dump())

    return client, source, Path(project["project_dir"]), model, save


def graph_with_rule(kind, value):
    graph = get_single_segmentation_flowchart(job_id="job_classes")
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    if kind == "predicate":
        next(edge for edge in graph.edges if edge.source == inspection.id).predicate = {
            "kind": "class", "operator": "absent", "class_name": value, "min_confidence": 0,
        }
    elif kind == "class_names":
        inspection.data.params["class_names"] = value
    elif kind.startswith("segmentation"):
        field = "class_ids" if kind.endswith("selection") else "class_rules"
        inspection.data.params[field] = [value] if field == "class_ids" else [{"class_id": value}]
    else:
        field = "class_ids" if kind.endswith("selection") else "class_rules"
        blob = FlowNode(id="blob", position={}, data=FlowNodeData(
            label="Blob", node_type="blob_measure",
            params={field: [value] if field == "class_ids" else [{"class_id": value}]},
        ))
        graph.nodes.insert(2, blob)
        result = next(edge for edge in graph.edges if edge.source == inspection.id)
        target = result.target
        result.target = blob.id
        graph.edges.append(FlowEdge(id="blob-result", source=blob.id, target=target, payload_type="result"))
    return graph


def saved_bytes(project):
    return {path.relative_to(project): path.read_bytes() for path in (project / "flowcharts").rglob("*.json")}


@pytest.mark.parametrize("kind,value", [
    ("predicate", "unknown"),
    ("predicate", " scratch "),
    ("predicate", "Scratch"),
    ("segmentation-selection", 9),
    ("segmentation-rule", 9),
    ("blob-selection", 9),
    ("blob-rule", 9),
    ("class_names", ["background", "chip", "scratch"]),
    ("class_names", ["background", "scratch"]),
])
def test_save_refuses_unrecorded_class_and_preserves_active_flow(flow_api, kind, value):
    client, source, project, _, save = flow_api
    baseline = graph_with_rule("predicate", "scratch")
    first = save(baseline)
    assert first.status_code == 200, first.text
    before = saved_bytes(project)

    rejected = save(graph_with_rule(kind, value))

    assert rejected.status_code == 422, rejected.text
    assert "class" in rejected.json()["detail"].lower()
    assert saved_bytes(project) == before
    reopened = client.get("/api/flowchart/pipeline", params={"inspection_task": "segmentation", "source_dataset_path": str(source)})
    assert reopened.status_code == 200
    assert reopened.json()["edges"][1]["predicate"]["class_name"] == "scratch"


@pytest.mark.parametrize("kind,value", [
    ("predicate", "scratch"),
    ("predicate", "background"),
    ("segmentation-selection", 2),
    ("segmentation-rule", 2),
    ("blob-selection", 2),
    ("blob-rule", 2),
    ("class_names", ["background", "scratch", "chip"]),
])
def test_save_accepts_classes_recorded_by_the_connected_model(flow_api, kind, value):
    _, _, _, _, save = flow_api
    response = save(graph_with_rule(kind, value))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("metadata", [
    {"task": "segmentation"},
    {"task": "segmentation", "classes": None},
])
def test_absent_recorded_vocabulary_remains_unknown_for_legacy_model(flow_api, metadata):
    _, _, _, model, save = flow_api
    model(metadata=metadata)
    response = save(graph_with_rule("blob-rule", 9))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("metadata", [
    {"task": "segmentation", "classes": "scratch"},
    {"task": "segmentation", "classes": ["background", ""]},
    {"task": "segmentation", "classes": ["background", "   "]},
    {"task": "segmentation", "classes": ["background", 1]},
    {"task": "segmentation", "class_names": ["background", "scratch"], "class_ids": [0, True]},
    {"task": "segmentation", "class_names": ["background", "scratch"], "class_ids": [0]},
    {"task": "segmentation", "class_names": ["background", "scratch"], "class_ids": [0, 0]},
    {"task": "segmentation", "class_names": ["background", "scratch"], "classes": ["background", "dust"]},
])
def test_save_refuses_malformed_recorded_vocabulary(flow_api, metadata):
    _, _, project, model, save = flow_api
    model(metadata=metadata)
    response = save(graph_with_rule("predicate", "scratch"))
    assert response.status_code == 422, response.text
    assert saved_bytes(project) == {}


@pytest.mark.parametrize("job_id", ["../job_classes", "job_missing"])
def test_class_rule_with_invalid_or_missing_bound_model_is_refused(flow_api, job_id):
    _, _, project, _, save = flow_api
    graph = graph_with_rule("predicate", "scratch")
    next(node for node in graph.nodes if node.data.node_type == "inspection").data.model_job_id = job_id
    response = save(graph)
    assert response.status_code == 422, response.text
    assert saved_bytes(project) == {}


def test_unbound_segmentation_does_not_borrow_detector_classes(flow_api):
    _, _, _, model, save = flow_api
    model("job_detector", "detection", {"task": "detection", "classes": ["background", "scratch"]})
    graph = graph_with_rule("blob-selection", 9)
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    inspection.data.model_job_id = None
    detector = FlowNode(id="detector", position={}, data=FlowNodeData(
        label="Detector", node_type="detection_crop", task="detection", model_job_id="job_detector",
    ))
    graph.nodes.insert(1, detector)
    graph.edges[0].target = detector.id
    graph.edges.append(FlowEdge(id="detector-inspection", source=detector.id, target=inspection.id, payload_type="roi"))
    response = save(graph)
    assert response.status_code == 200, response.text


def test_class_predicate_uses_its_source_model_in_a_multi_model_graph(flow_api):
    _, _, _, model, save = flow_api
    model("job_second", "segmentation", {"task": "segmentation", "classes": ["background", "dust"]})
    graph = graph_with_rule("predicate", "scratch")
    first = next(node for node in graph.nodes if node.data.node_type == "inspection")
    second = first.model_copy(deep=True)
    second.id = "second"
    second.data.model_job_id = "job_second"
    graph.nodes.insert(2, second)
    graph.edges[1].target = second.id
    graph.edges[1].payload_type = "roi"
    graph.edges.append(FlowEdge(id="second-result", source=second.id, target="node_decision", predicate={
        "kind": "class", "operator": "present", "class_name": "scratch",
    }))
    response = save(graph)
    assert response.status_code == 422, response.text
    assert "second" in response.json()["detail"]


def test_detection_background_is_not_a_predicate_class(flow_api):
    _, _, _, model, save = flow_api
    model(task="detection", metadata={"task": "detection", "classes": ["background", "scratch"]})
    graph = graph_with_rule("predicate", "background")
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    inspection.data.node_type = "detection_crop"
    inspection.data.task = "detection"
    response = save(graph)
    assert response.status_code == 422, response.text


def test_missing_class_rules_do_not_turn_draft_save_into_model_eligibility_check(flow_api):
    _, _, _, _, save = flow_api
    graph = get_single_segmentation_flowchart(job_id="job_not_yet_available")
    assert save(graph).status_code == 200


@pytest.mark.parametrize("raw_metadata", ["{broken json", "[]", "null"])
def test_malformed_metadata_file_is_refused_before_publishing(flow_api, raw_metadata):
    _, _, project, model, save = flow_api
    directory = model()
    (directory / "model_meta.json").write_text(raw_metadata, encoding="utf-8")
    response = save(graph_with_rule("predicate", "scratch"))
    assert response.status_code == 422, response.text
    assert saved_bytes(project) == {}


def test_legacy_model_without_metadata_file_has_unknown_vocabulary(flow_api):
    _, _, _, model, save = flow_api
    directory = model()
    (directory / "model_meta.json").unlink()
    response = save(graph_with_rule("blob-selection", 9))
    assert response.status_code == 200, response.text


def test_bound_model_from_changed_source_is_refused_for_class_rules(flow_api):
    _, source, project, _, save = flow_api
    Image.new("RGB", (32, 32), "red").save(source / "part.png")
    response = save(graph_with_rule("predicate", "scratch"))
    assert response.status_code == 422, response.text
    assert saved_bytes(project) == {}


def test_detection_class_aliases_may_differ_only_by_background_entry(flow_api):
    _, _, _, model, save = flow_api
    model(task="detection", metadata={
        "task": "detection", "classes": ["background", "scratch"], "class_names": ["scratch"],
    })
    graph = graph_with_rule("predicate", "scratch")
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    inspection.data.node_type = "detection_crop"
    inspection.data.task = "detection"
    response = save(graph)
    assert response.status_code == 200, response.text


def test_segmentation_rule_cannot_borrow_ids_from_previous_model(flow_api):
    _, _, _, model, save = flow_api
    model("job_second", "segmentation", {"task": "segmentation", "classes": ["background", "dust"]})
    graph = graph_with_rule("segmentation-selection", 2)
    first = next(node for node in graph.nodes if node.data.node_type == "inspection")
    second = first.model_copy(deep=True)
    first.data.params = {}
    second.id = "second"
    second.data.model_job_id = "job_second"
    graph.nodes.insert(2, second)
    graph.edges[1].source = second.id
    graph.edges.append(FlowEdge(id="first-second", source=first.id, target=second.id, payload_type="roi"))
    response = save(graph)
    assert response.status_code == 422, response.text
    assert "second" in response.json()["detail"]


@pytest.mark.parametrize("checkpoint_bytes", [b"corrupt checkpoint", b""])
def test_corrupt_specialized_checkpoint_is_a_save_validation_error(flow_api, checkpoint_bytes):
    _, _, project, model, save = flow_api
    standard = model()
    job_id = "a" * 32
    directory = standard.parent / "rotated_detection" / job_id
    directory.mkdir(parents=True)
    (directory / "best_model.pt").write_bytes(checkpoint_bytes)
    graph = graph_with_rule("predicate", "scratch")
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    inspection.data.task = "rotated_detection"
    inspection.data.model_job_id = job_id
    response = save(graph)
    assert response.status_code == 422, response.text
    assert saved_bytes(project) == {}


@pytest.mark.parametrize("class_ids,selected", [([0, 9], 9), ([1, 0], 1)])
def test_recorded_segmentation_ids_must_match_channel_positions(flow_api, class_ids, selected):
    _, _, project, model, save = flow_api
    model(metadata={"task": "segmentation", "classes": ["background", "scratch"], "class_ids": class_ids})
    response = save(graph_with_rule("segmentation-selection", selected))
    assert response.status_code == 422, {"response": response.json(), "saved_files": list(saved_bytes(project))}
    assert saved_bytes(project) == {}


@pytest.mark.parametrize("metadata,wanted", [
    ({"task": "segmentation", "classes": ["background", "scratch"], "class_names": []}, "unknown"),
    ({"task": "segmentation", "classes": [], "class_names": ["background", "scratch"]}, "scratch"),
])
def test_empty_class_alias_cannot_hide_another_recorded_vocabulary(flow_api, metadata, wanted):
    _, _, project, model, save = flow_api
    model(metadata=metadata)
    response = save(graph_with_rule("predicate", wanted))
    assert response.status_code == 422, {"response": response.json(), "saved_files": list(saved_bytes(project))}
    assert saved_bytes(project) == {}


def test_genuinely_empty_legacy_class_vocabulary_remains_unknown(flow_api):
    _, _, _, model, save = flow_api
    model(metadata={"task": "segmentation", "classes": [], "class_names": [], "class_ids": []})
    response = save(graph_with_rule("predicate", "unknown"))
    assert response.status_code == 200, response.text

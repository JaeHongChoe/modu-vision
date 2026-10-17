"""Retained rotation binding uses its real original source, never a new lineage.

Component scope: original project API/store, immutable temporary version and
checkpoint dictionaries, catalog and verification. No training, model forward,
selected worker, app launch, network or historical/customer data is used here.
Only Root executes these tests with the original isolated backend conftest.
"""
import hashlib
import json
from pathlib import Path
import shutil

import pytest


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


@pytest.fixture
def retained_sources(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from PIL import Image
    import torch
    from backend.main import create_app
    from backend.api import routes_dataset, routes_flowchart
    from backend.engine.annotation_storage import set_request_project_root, reset_request_project_root
    from backend.engine.dataset_fingerprint import fingerprint_dataset
    from backend.engine.rotation import write_rotation_manifest, load_rotation_manifest
    from backend.engine.training_provenance import bind_family_training

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_flowchart.training_job_manager, "_jobs", {})
    app = create_app(project_dir=str(tmp_path / "projects"))
    client = TestClient(app, headers={"X-Vision-Token": app.state.api_token})
    response = client.post("/api/project/create", json={"name": "Retained rotation source", "task": "classification"})
    assert response.status_code == 200, response.text
    rotation_project = response.json()
    rotation_source = tmp_path / "rotation-source"
    rotation_source.mkdir()
    prepared = Path(rotation_project["dataset_dir"]) / "rotation" / "original-prepared"
    prepared.mkdir(parents=True)
    rows = []
    for index, split in enumerate(("train", "val", "test")):
        name = split + ".png"
        Image.new("RGB", (16, 16), (20 + index * 60, 35, 80)).save(rotation_source / name)
        shutil.copyfile(rotation_source / name, prepared / name)
        rows.append({"image": name, "correction_deg": index * 10, "split": split})
    write_rotation_manifest(prepared, rows, source_dataset_path=rotation_source)
    updated = client.put("/api/project/update", json={"source_dataset_dir": str(rotation_source)})
    assert updated.status_code == 200, updated.text
    rotation_project = client.get("/api/project/current").json()
    binding = bind_family_training(rotation_project, prepared, "rotation")
    rotation_job = "d" * 32
    rotation_model = Path(rotation_project["models_dir"]) / "rotation" / rotation_job
    rotation_model.mkdir(parents=True)
    checkpoint = rotation_model / "best_model.pt"
    # Real serialized checkpoint/provenance format, no network weights or forward.
    torch.save({"task": "rotation", "version": 1, "model_state_dict": {},
                "dataset_provenance": load_rotation_manifest(prepared).provenance,
                "training_provenance": binding}, checkpoint)
    _json(rotation_model / "model_meta.json", {
        "task": "rotation", "dataset_path": str(prepared),
        "source_dataset_path": str(rotation_source), "checkpoint_sha256": _sha(checkpoint),
        "dataset_provenance": load_rotation_manifest(prepared).provenance,
        "training_provenance": binding,
    })
    _json(rotation_model / "job_receipt.json", {
        "job_id": rotation_job, "task": "rotation", "status": "completed",
        "source_dataset_path": str(rotation_source), "dataset_path": str(prepared),
        "checkpoint_sha256": _sha(checkpoint), "dataset_fingerprint": binding["dataset_fingerprint"],
        "training_provenance": binding,
    })
    rotation_project = client.get("/api/project/current").json()
    original_files = [checkpoint, rotation_model / "model_meta.json", rotation_model / "job_receipt.json",
                      Path(binding["version_dir"]) / "manifest.json", prepared / "rotation.json",
                      Path(rotation_project["project_dir"]) / "project.json"]
    originals = {str(path): path.read_bytes() for path in original_files}

    response = client.post("/api/project/create", json={"name": "Active segmentation source", "task": "segmentation"})
    assert response.status_code == 200, response.text
    segmentation_source = tmp_path / "segmentation-source"
    segmentation_source.mkdir()
    Image.new("RGB", (16, 16), (150, 40, 70)).save(segmentation_source / "sample.png")
    updated = client.put("/api/project/update", json={"source_dataset_dir": str(segmentation_source)})
    assert updated.status_code == 200, updated.text
    project = client.get("/api/project/current").json()
    token = set_request_project_root(Path(project["project_dir"]))
    try:
        fingerprint = fingerprint_dataset(segmentation_source,
            studio_root=routes_dataset.STUDIO_ANNOTATIONS_DIR,
            split_manifest=routes_dataset._split_manifest_file(segmentation_source))
    finally:
        reset_request_project_root(token)
    segmentation_job = "job_654_retained_seg"
    segmentation_model = Path(project["models_dir"]) / segmentation_job
    segmentation_model.mkdir(parents=True)
    torch.save({"task": "segmentation", "model_state_dict": {}}, segmentation_model / "best_model.pt")
    _json(segmentation_model / "model_meta.json", {"task": "segmentation", "classes": ["background", "defect"]})
    _json(segmentation_model / "job_receipt.json", {
        "job_id": segmentation_job, "task": "segmentation", "status": "completed",
        "source_dataset_path": str(segmentation_source), "dataset_fingerprint": fingerprint,
        "dataset_path": str(segmentation_source),
    })
    models = [{"job_id": rotation_job, "task": "rotation"},
              {"job_id": segmentation_job, "task": "segmentation"}]
    request = {
        "source_project_id": rotation_project["id"], "job_id": rotation_job,
        "source_project_manifest_sha256": _sha(Path(rotation_project["project_dir"]) / "project.json"),
        "checkpoint_sha256": _sha(checkpoint),
        "version_manifest_sha256": _sha(Path(binding["version_dir"]) / "manifest.json"),
    }
    yield {"client": client, "app": app, "project": project, "rotation_project": rotation_project,
           "rotation_source": rotation_source, "segmentation_source": segmentation_source,
           "rotation_job": rotation_job, "segmentation_job": segmentation_job,
           "models": models, "binding_request": request, "originals": originals,
           "rotation_model": rotation_model, "version": Path(binding["version_dir"])}
    for name, raw in originals.items():
        assert Path(name).read_bytes() == raw, "Binding must not rewrite original checkpoint/job/model/source-version data"


def test_explicit_retained_rotation_binding_enables_same_original_catalog_and_verification(retained_sources):
    case = retained_sources
    client = case["client"]
    bound = client.post("/api/flowchart/models/retained-preprocessors", json=case["binding_request"])
    assert bound.status_code == 200, bound.text
    assert bound.json()["job_id"] == case["rotation_job"]
    assert bound.json()["checkpoint_sha256"] == case["binding_request"]["checkpoint_sha256"]
    catalog = client.get("/api/flowchart/models/catalog", params={"source_dataset_path": str(case["segmentation_source"])})
    assert catalog.status_code == 200, catalog.text
    assert {(row["job_id"], row["task"]) for row in catalog.json()["models"]} >= {
        (case["rotation_job"], "rotation"), (case["segmentation_job"], "segmentation")}
    verified = client.post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(case["segmentation_source"]), "models": case["models"]})
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified_job_ids"] == [case["rotation_job"], case["segmentation_job"]]
    assert client.get("/api/project/current").json()["source_dataset_dir"] == str(case["segmentation_source"])
    assert not (Path(case["project"]["models_dir"]) / "rotation" / case["rotation_job"]).exists()


def test_without_explicit_binding_original_cross_source_refusal_remains(retained_sources):
    case = retained_sources
    response = case["client"].post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(case["segmentation_source"]), "models": case["models"]})
    assert response.status_code == 409, response.text
    assert case["client"].get("/api/project/current").json()["source_dataset_dir"] == str(case["segmentation_source"])


# Additional Root-only controls. The original Source01 prefix above stays exact.
def _bind(case):
    response = case["client"].post("/api/flowchart/models/retained-preprocessors", json=case["binding_request"])
    assert response.status_code == 200, response.text
    return Path(case["project"]["project_dir"]) / ".retained-preprocessors" / (case["rotation_job"] + ".json")


def _verify(case):
    return case["client"].post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(case["segmentation_source"]), "models": case["models"]})


def _pipeline(case):
    from backend.engine.flowchart_engine import FlowNode, FlowNodeData, FlowEdge, get_single_segmentation_flowchart
    graph = get_single_segmentation_flowchart(case["segmentation_job"])
    input_node = next(node for node in graph.nodes if node.data.node_type == "input")
    inspection = next(node for node in graph.nodes if node.data.node_type == "inspection")
    graph.nodes.append(FlowNode(id="retained-rotation", position={"x": 200, "y": 100}, data=FlowNodeData(
        label="Retained original rotation", node_type="preprocess", model_job_id=case["rotation_job"],
        params={"operation": "learned_rotation"})))
    graph.edges = [edge for edge in graph.edges if edge.source != input_node.id]
    graph.edges.extend([FlowEdge(id="retained-in", source=input_node.id, target="retained-rotation"),
                        FlowEdge(id="retained-out", source="retained-rotation", target=inspection.id)])
    return graph


def test_original_specialist_still_rejects_actual_foreign_source_after_explicit_binding(retained_sources):
    from backend.engine.specialized_models import resolve_specialized_checkpoint
    case = retained_sources
    _bind(case)
    with pytest.raises(ValueError, match="source lineage|source dataset"):
        resolve_specialized_checkpoint(case["rotation_project"]["models_dir"], case["rotation_job"],
                                       "rotation", str(case["segmentation_source"]))
    assert _verify(case).status_code == 200


@pytest.mark.parametrize("field", ["source_project_manifest_sha256", "checkpoint_sha256", "version_manifest_sha256"])
def test_binding_requires_all_reviewed_original_digests(retained_sources, field):
    case = retained_sources
    request = {**case["binding_request"], field: "0" * 64}
    response = case["client"].post("/api/flowchart/models/retained-preprocessors", json=request)
    assert response.status_code == 409, response.text
    assert not (Path(case["project"]["project_dir"]) / ".retained-preprocessors").exists()


def test_unregistered_source_project_cannot_be_bound_by_path_or_id(retained_sources):
    case = retained_sources
    response = case["client"].post("/api/flowchart/models/retained-preprocessors", json={
        **case["binding_request"], "source_project_id": "unregistered-project"})
    assert response.status_code == 409, response.text
    response = case["client"].post("/api/flowchart/models/retained-preprocessors", json={
        **case["binding_request"], "source_project_path": case["rotation_project"]["project_dir"]})
    assert response.status_code == 422, response.text


def test_duplicate_binding_never_replaces_first_reviewed_reference(retained_sources):
    case = retained_sources
    path = _bind(case)
    before = path.read_bytes()
    response = case["client"].post("/api/flowchart/models/retained-preprocessors", json=case["binding_request"])
    assert response.status_code == 409, response.text
    assert path.read_bytes() == before
    assert _verify(case).status_code == 200


@pytest.mark.parametrize("name", ["checkpoint", "metadata", "job_receipt", "version_manifest", "family_manifest", "source_image"])
def test_original_changed_retained_input_refuses_verification_and_catalog(retained_sources, name):
    case = retained_sources
    _bind(case)
    paths = {"checkpoint": case["rotation_model"] / "best_model.pt",
             "metadata": case["rotation_model"] / "model_meta.json",
             "job_receipt": case["rotation_model"] / "job_receipt.json",
             "version_manifest": case["version"] / "manifest.json",
             "family_manifest": Path(json.loads((case["rotation_model"] / "model_meta.json").read_text())["dataset_path"]) / "rotation.json",
             "source_image": case["rotation_source"] / "train.png"}
    path = paths[name]
    original = path.read_bytes()
    try:
        # Preserve a valid object for provenance records; only the checkpoint and
        # source image use arbitrary corrupted bytes. No substitute is accepted.
        if path.suffix == ".json":
            value = json.loads(original)
            value["retained_input_changed_control"] = True
            _json(path, value)
        else:
            path.write_bytes(b"changed retained original")
        refused = _verify(case)
        assert refused.status_code == 409, refused.text
        catalog = case["client"].get("/api/flowchart/models/catalog", params={
            "source_dataset_path": str(case["segmentation_source"])})
        assert catalog.status_code == 200, catalog.text
        assert case["rotation_job"] not in {row["job_id"] for row in catalog.json()["models"]}
    finally:
        path.write_bytes(original)


def test_active_project_source_change_invalidates_old_binding(retained_sources, tmp_path):
    case = retained_sources
    _bind(case)
    changed = tmp_path / "different-active-source"
    changed.mkdir()
    response = case["client"].put("/api/project/update", json={"source_dataset_dir": str(changed)})
    assert response.status_code == 200, response.text
    refused = case["client"].post("/api/flowchart/models/verify", json={
        "source_dataset_path": str(changed), "models": [case["models"][0]]})
    assert refused.status_code == 409, refused.text


def test_active_project_model_collision_cannot_substitute_a_retained_model(retained_sources):
    case = retained_sources
    _bind(case)
    collision = Path(case["project"]["models_dir"]) / "rotation" / case["rotation_job"]
    collision.mkdir(parents=True)
    assert _verify(case).status_code == 409


@pytest.mark.parametrize("alias", ["directory", "record"])
def test_retained_registry_alias_cannot_fall_back_to_unbound_resolution(retained_sources, alias, tmp_path):
    case = retained_sources
    directory = Path(case["project"]["project_dir"]) / ".retained-preprocessors"
    if alias == "directory":
        directory.symlink_to(tmp_path / "absent-registry", target_is_directory=True)
    else:
        directory.mkdir(mode=0o700)
        (directory / (case["rotation_job"] + ".json")).symlink_to(tmp_path / "absent-record")
    assert _verify(case).status_code == 409


def test_original_run_and_export_receive_same_original_two_checkpoints_without_forward(retained_sources, monkeypatch, tmp_path):
    """Only selected transport is controlled; original model/source checks and
    saved graph/package builders execute. This is not a GPU or parity result.
    """
    from backend.remote import operations, profiles
    case = retained_sources
    _bind(case)
    graph = _pipeline(case)
    profile = profiles.ComputeProfile(id="component-gpu2", name="Component only GPU2",
        ssh_target="component.invalid", ssh_port=22, remote_root="/component-only",
        runtime_kind="python", runtime_value="python3", gpu_selector="2")
    store = profiles.ProfileStore(tmp_path / "component-profiles.json")
    store.save(profile)
    monkeypatch.setattr(profiles, "get_profile_store", lambda: store)
    expected = {(case["rotation_job"], "rotation"): (case["rotation_model"] / "best_model.pt").resolve(),
                (case["segmentation_job"], "segmentation"): (Path(case["project"]["models_dir"]) / case["segmentation_job"] / "best_model.pt").resolve()}
    calls = []
    def controlled_artifacts(context, operation, spec, *, transport=None, input_files):
        assert context.profile == profile and operation == "flowchart_run"
        assert spec["pipeline"] == graph.model_dump() and spec["device"] == "cuda"
        assert {(row["job_id"], row["task"]) for row in spec["models"]} == set(expected)
        for row in spec["models"]:
            original = expected[(row["job_id"], row["task"])]
            assert input_files[row["checkpoint_path"]] == original
            assert row["checkpoint_sha256"] == _sha(original)
            assert input_files[row["metadata_path"]] == original.parent / "model_meta.json"
        assert input_files[spec["image_path"]] == case["segmentation_source"] / "sample.png"
        calls.append((context.profile.id, spec["device"]))
        result = tmp_path / "controlled-operation" / "outputs" / "flowchart_result.json"
        result.parent.mkdir(parents=True)
        _json(result, {"status": "success", "final_verdict": "REVIEW", "execution_device": "cuda",
            "device_name": "CONTROLLED-NOT-A-GPU-RESULT", "image_path": spec["image_path"],
            "image_sha256": spec["image_sha256"], "model_job_ids": sorted(job for job, task in expected),
            "annotated_image": None, "crops": []})
        return {"outputs/flowchart_result.json": result}
    # Only the final external operation is controlled. The original
    # run_verified_flowchart_on_compute validates/builds every portable input.
    monkeypatch.setattr(operations, "run_remote_operation_artifacts", controlled_artifacts)
    run = case["client"].post("/api/flowchart/run", json={
        "pipeline": graph.model_dump(), "image_path": str(case["segmentation_source"] / "sample.png"),
        "device": "cuda", "execution_target": "selected_compute", "compute_profile_id": profile.id})
    assert run.status_code == 200, run.text
    assert calls == [(profile.id, "cuda")]
    saved = case["client"].post("/api/flowchart/pipeline", params={
        "recipe_task": "segmentation", "source_dataset_path": str(case["segmentation_source"])}, json=graph.model_dump())
    assert saved.status_code == 200, saved.text
    exported = case["client"].post("/api/export/flow", json={"source_dataset_path": str(case["segmentation_source"]),
        "recipe_task": "segmentation", "package_name": "explicit_retained_rotation"})
    assert exported.status_code == 200, exported.text
    assert exported.json()["parity"] == {"status": "not_run"}
    package = Path(exported.json()["package_path"])
    assert json.loads((package / "pipeline.json").read_text()) == graph.model_dump()
    for (job, task), checkpoint in expected.items():
        assert _sha(package / "models" / job / "best_model.pt") == _sha(checkpoint)
    assert calls == [(profile.id, "cuda")]  # Export did not run another forward.


@pytest.mark.parametrize("denial", ["target-nonowner", "source-nonowner", "source-membership", "expired-session"])
def test_original_shared_source_membership_session_and_both_owner_gates_remain(retained_sources, tmp_path, denial):
    """Real AccountStore controls; Request is explicit route-component data.
    No shared ASGI authentication or installed lifetime is claimed here.
    """
    from types import SimpleNamespace
    from fastapi import HTTPException
    from backend.engine.shared_accounts import AccountStore
    from backend.api.routes_flowchart import _registered_retained_source
    case = retained_sources
    store = AccountStore(tmp_path / "retained-accounts.sqlite")
    password = "retained component control 123"
    actor = store.bootstrap("owner", password)
    other = store.create_user("other", password)
    session = store.login("owner", password)
    target, source = case["project"], case["rotation_project"]
    for project in (target, source):
        store.register_project(project["id"], project["project_dir"], actor["id"])
        store.set_membership(project["id"], other["id"], "owner", actor["id"], session_token=session["token"])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(accounts=store)),
        state=SimpleNamespace(account_user=actor, account_session_token=session["token"]))
    result = _registered_retained_source(request, source["id"], binding_target=target)
    assert result["id"] == source["id"]
    if denial == "target-nonowner":
        other_session = store.login("other", password)
        store.set_membership(target["id"], actor["id"], "trainer", other["id"], session_token=other_session["token"])
    elif denial == "source-nonowner":
        other_session = store.login("other", password)
        store.set_membership(source["id"], actor["id"], "trainer", other["id"], session_token=other_session["token"])
    elif denial == "source-membership":
        other_session = store.login("other", password)
        store.set_membership(source["id"], actor["id"], "viewer", other["id"], session_token=other_session["token"])
    else:
        store.logout(session["token"])
    with pytest.raises(HTTPException) as error:
        _registered_retained_source(request, source["id"], binding_target=target)
    assert error.value.status_code == 403


def _portable_case(case):
    from backend.remote.profiles import ComputeProfile
    profile = ComputeProfile(id="scope-component", name="Scope component only", ssh_target="component.invalid",
        ssh_port=22, remote_root="/component-only", runtime_kind="python", runtime_value="python3", gpu_selector="2")
    paths = {(case["rotation_job"], "rotation"): case["rotation_model"] / "best_model.pt",
             (case["segmentation_job"], "segmentation"): Path(case["project"]["models_dir"]) / case["segmentation_job"] / "best_model.pt"}
    return profile, _pipeline(case), paths


@pytest.mark.parametrize("scope", ["none", "different-project-object"])
def test_original_direct_portable_operation_still_refuses_foreign_model_without_exact_request_scope(retained_sources, monkeypatch, scope):
    from contextlib import nullcontext
    from backend.engine.retained_preprocessors import retained_rotation_transport
    from backend.remote import operations
    from backend.remote.coordinator import ArtifactValidationError
    case = retained_sources
    _bind(case)
    profile, graph, paths = _portable_case(case)
    monkeypatch.setattr(operations, "run_remote_operation_artifacts", lambda *a, **k: pytest.fail("Unscoped foreign model reached transport"))
    def forbidden_source_resolution(identifier):
        pytest.fail("A different project object resolved retained source data")
    context = (retained_rotation_transport(dict(case["project"]), forbidden_source_resolution)
               if scope == "different-project-object" else nullcontext())
    with context, pytest.raises(ArtifactValidationError, match="belongs to another project"):
        operations.run_verified_flowchart_on_compute(profile, case["project"], graph.model_dump(), paths,
            case["segmentation_source"] / "sample.png", device="cuda")


def test_original_selected_call_exception_resets_retained_scope_without_extra_dispatch(retained_sources, monkeypatch, tmp_path):
    from backend.remote import operations, profiles
    from backend.remote.coordinator import ArtifactValidationError
    case = retained_sources
    _bind(case)
    profile, graph, paths = _portable_case(case)
    store = profiles.ProfileStore(tmp_path / "exception-component-profiles.json")
    store.save(profile)
    monkeypatch.setattr(profiles, "get_profile_store", lambda: store)
    calls = []
    def failed_artifacts(context, operation, spec, **kwargs):
        calls.append(operation)
        assert operation == "flowchart_run" and spec["portable_models"] is True
        raise ValueError("Controlled external boundary failure; no worker or forward")
    monkeypatch.setattr(operations, "run_remote_operation_artifacts", failed_artifacts)
    response = case["client"].post("/api/flowchart/run", json={"pipeline": graph.model_dump(),
        "image_path": str(case["segmentation_source"] / "sample.png"), "execution_target": "selected_compute",
        "device": "cuda", "compute_profile_id": profile.id})
    assert response.status_code == 502, response.text
    assert calls == ["flowchart_run"]
    with pytest.raises(ArtifactValidationError, match="belongs to another project"):
        operations.run_verified_flowchart_on_compute(profile, case["project"], graph.model_dump(), paths,
            case["segmentation_source"] / "sample.png", device="cuda")
    assert calls == ["flowchart_run"]


@pytest.mark.parametrize("escaped", ["after-original-return", "different-original-thread"])
def test_copied_context_cannot_extend_original_retained_transport_scope(retained_sources, monkeypatch, escaped):
    from concurrent.futures import ThreadPoolExecutor
    from contextvars import copy_context
    from backend.engine.retained_preprocessors import retained_rotation_transport, read_registered_project
    from backend.remote import operations
    from backend.remote.coordinator import ArtifactValidationError
    case = retained_sources
    _bind(case)
    profile, graph, paths = _portable_case(case)
    monkeypatch.setattr(operations, "run_remote_operation_artifacts", lambda *a, **k: pytest.fail("Escaped retained context reached transport"))
    def original_source(identifier):
        assert identifier == case["rotation_project"]["id"]
        return read_registered_project(case["rotation_project"]["project_dir"], identifier)
    def refused(context):
        with pytest.raises(ArtifactValidationError, match="belongs to another project"):
            context.run(operations.run_verified_flowchart_on_compute, profile, case["project"], graph.model_dump(),
                paths, case["segmentation_source"] / "sample.png", device="cuda")
    with retained_rotation_transport(case["project"], original_source):
        copied = copy_context()
        if escaped == "different-original-thread":
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(refused, copied).result(timeout=10)
    if escaped == "after-original-return":
        refused(copied)

"""Design §3.2 composition: 20 labels -> 50 reviewed drafts -> new training input.

Only inference and worker submission are controlled. Project/storage middleware,
annotation saves, proposals, batch review, backups, frozen training versions,
LabelMe-to-COCO preparation and warm-start validation use production code.
The checkpoint is an explicitly untrained tensor fixture. No trainer, learned
model, download, provider, account or browser/UI is exercised by this test.
"""

import hashlib
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import torch


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_twenty_labels_and_fifty_explicit_reviews_reach_a_new_warm_start_input(tmp_path, monkeypatch, request):
    """Catch auto-adoption, broken batch continuation, lost parent, or stale training data."""
    from backend.api import (
        routes_annotation, routes_dataset, routes_dataset_versions,
        routes_label_suggestions, routes_project, routes_training,
    )
    from backend.contracts.context import ContextRegistry
    from backend.engine.annotation_storage import dataset_annotation_dir
    from backend.main import ProjectContextMiddleware, ProjectStorageScopeMiddleware

    # Relative legacy stores and all newly created project data stay in this fixture.
    monkeypatch.chdir(tmp_path)
    app = FastAPI()
    app.state.project_dir = tmp_path / "projects"
    app.state.context_registry = ContextRegistry(app.state.project_dir)
    for routes in (routes_project, routes_dataset, routes_annotation,
                   routes_dataset_versions, routes_label_suggestions, routes_training):
        app.include_router(routes.router)
    app.add_middleware(ProjectStorageScopeMiddleware, project_app=app)
    app.add_middleware(ProjectContextMiddleware, project_app=app)

    source = tmp_path / "synthetic-source"
    source.mkdir()
    images = []
    for index in range(70):
        image = source / f"part-{index:03d}.png"
        pixels = Image.new("RGB", (64, 48), (index + 20, 150, 210))
        ImageDraw.Draw(pixels).rectangle((10, 10, 19, 19), fill=(20, 20, 20))
        pixels.save(image)
        images.append(image)
    source_hashes = {image.name: _sha(image) for image in images}
    submissions = []
    inferred_images = []
    external_attempts = []
    original_connect = socket.socket.connect

    def deny_network(sock, address):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            external_attempts.append(str(address))
            raise AssertionError("The composition fixture cannot use network sockets")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", deny_network)

    # These are the expensive/external boundaries. No actual worker may be launched.
    def capture_submission(**kwargs):
        assert kwargs["task"] == "detection" and kwargs["device"] == "cpu"
        assert kwargs["source_dataset_path"] == str(source)
        assert kwargs["prepare_dataset"] is not None
        kwargs["prepare_dataset"](threading.Event())  # Real preparation, zero training.
        submissions.append(kwargs)
        return SimpleNamespace(job_id=kwargs["job_id"], status="running", phase=None)

    def refuse_remote_submission(**kwargs):
        raise AssertionError("A local composition fixture must never submit remote work")

    def controlled_infer(task, model_path, image_input, threshold=0.5, device=None):
        assert task == "detection" and device == "cpu" and threshold == 0.5
        assert Path(model_path) == checkpoint
        assert Path(image_input) in images[20:]
        inferred_images.append(Path(image_input))
        return SimpleNamespace(predictions=[{
            "bbox": [10, 10, 20, 20], "score": 0.91, "label": "defect",
        }], confidence_score=0.91, latency_ms=0.0)

    monkeypatch.setattr(routes_training.training_job_manager, "start_job", capture_submission)
    monkeypatch.setattr(routes_training.training_job_manager, "start_remote_job", refuse_remote_submission)
    monkeypatch.setattr(routes_label_suggestions, "infer", controlled_infer)

    with TestClient(app) as client:
        def api(method, route, **kwargs):
            response = client.request(method, route, **kwargs)
            assert response.status_code == 200, response.text
            return response.json()

        project = api("POST", "/api/project/create", json={"name": "Synthetic loop", "task": "detection"})
        project = api("PUT", "/api/project/update", json={"source_dataset_dir": str(source)})

        def stop_owned_batches():
            # On an assertion/timeout, stop this fixture before monkeypatch restores
            # real inference. No other project's live batch is touched.
            with routes_label_suggestions._BATCH_LOCK:
                owned = [live for live in routes_label_suggestions._LIVE_BATCHES.values()
                         if live.batch.get("project_id") == project["id"]
                         and live.batch.get("source_dataset_dir") == str(source)]
            for live in owned:
                live.cancel.set()
            for live in owned:
                live.thread.join(5)
                assert not live.thread.is_alive(), "The synthetic batch did not stop before provider restoration"

        request.addfinalizer(stop_owned_batches)
        studio = dataset_annotation_dir(source, Path(project["annotations_dir"]), use_scope=False)
        # Twenty explicit drawing saves, with hand-defined synthetic geometry.
        for image in images[:20]:
            api("POST", "/api/annotations/save", json={
                "image_id": image.stem, "image_path": str(image), "image_width": 64, "image_height": 48,
                "actor": "synthetic-drawer", "annotations": [{
                    "id": f"drawn-{image.stem}", "type": "bbox", "label": "defect", "category_id": 1,
                    "bbox": [10, 10, 20, 20], "color": "#22d3ee",
                }],
            })
        initial_labels = {path.name: path.read_bytes() for path in studio.glob("*.json")}
        initial_masks = {path.name: _sha(path) for path in (studio / "masks").glob("*.png")}
        assert len(initial_labels) == 20
        assert initial_masks == {}  # BBox-only saves do not create raster masks.

        options = {"backbone": "fasterrcnn_mobilenet_v3_large_fpn", "image_size": 64, "pretrained": False}
        first = api("POST", "/api/training/start", json={
            "dataset_path": str(source), "task": "detection", "preset": "fast", "device": "cpu",
            "config_overrides": {**options, "epochs": 12},
        })
        assert len(submissions) == 1
        initial = submissions[0]
        assert initial["warm_start"] is None
        first_prepared = _json(Path(initial["dataset_path"]) / "source_manifest.json")
        assert {row["source_image"] for row in first_prepared} == {str(image) for image in images[:20]}
        old_binding = dict(first["training_provenance"])

        # Simulate completion at the controlled worker boundary. These tensor bytes
        # have never been trained and cannot be interpreted as a quality result.
        model_dir = Path(initial["output_dir"])
        checkpoint = model_dir / "best_model.pt"
        metadata = {"task": "detection", "classes": ["defect"], "preset": "fast",
                    "backbone": options["backbone"], "created_at": "2026-10-24T00:00:00Z"}
        torch.save({**metadata, "model_state_dict": {"untrained_fixture": torch.zeros(1)}}, checkpoint)
        (model_dir / "model_meta.json").write_text(json.dumps(metadata), encoding="utf-8")
        routes_training._write_job_receipt(routes_training.JobRecord(
            job_id=first["job_id"], task="detection", preset="fast", status="completed",
            dataset_path=initial["dataset_path"], output_dir=str(model_dir),
            source_dataset_path=str(source), dataset_fingerprint=initial["dataset_fingerprint"],
            dataset_binding=initial["dataset_binding"],
        ))
        checkpoint_sha = _sha(checkpoint)
        models = api("GET", "/api/label-suggestions/models")["models"]
        assert [(row["job_id"], row["data_state"]) for row in models] == [(first["job_id"], "current")]

        batch = api("POST", "/api/label-suggestions/batches", json={"job_id": first["job_id"], "threshold": 0.5})
        deadline = time.monotonic() + 15
        while batch["status"] in {"running", "cancelling"}:
            assert time.monotonic() < deadline, "The bounded 50-image fixture did not finish"
            time.sleep(0.02)
            batch = api("GET", f"/api/label-suggestions/batches/{batch['id']}")
        assert batch["status"] == "completed", batch
        assert (batch["total"], batch["processed"], batch["generated"], batch["zero_candidates"], batch["failed"]) == (50, 50, 50, 0, 0)
        assert sorted(inferred_images) == images[20:]
        proposals = api("GET", "/api/label-suggestions")["suggestions"]
        assert len(proposals) == 50 and all(row["status"] == "pending" for row in proposals)
        # Merely producing/reading drafts must leave confirmed labels byte-for-byte intact.
        assert {path.name: path.read_bytes() for path in studio.glob("*.json")} == initial_labels
        assert {path.name: _sha(path) for path in (studio / "masks").glob("*.png")} == initial_masks
        assert api("GET", "/api/label-suggestions/models")["models"][0]["data_state"] == "current"

        # An explicit but empty selection cannot turn a draft into truth or add a backup.
        versions_before = api("GET", "/api/dataset/versions")
        invalid = client.post(f"/api/label-suggestions/{proposals[0]['id']}/review", json={"decision": "accept", "candidate_ids": []})
        assert invalid.status_code == 422
        assert api("GET", "/api/dataset/versions") == versions_before
        assert {path.name: path.read_bytes() for path in studio.glob("*.json")} == initial_labels
        assert {path.name: _sha(path) for path in (studio / "masks").glob("*.png")} == initial_masks

        backup_ids = []
        for proposal in proposals:
            candidate_id = proposal["candidates"][0]["id"]
            reviewed = api("POST", f"/api/label-suggestions/{proposal['id']}/review", json={
                "decision": "accept", "candidate_ids": [candidate_id], "actor": "synthetic-reviewer",
            })
            assert reviewed["status"] == "accepted" and reviewed["accepted_candidate_ids"] == [candidate_id]
            backup_ids.append(reviewed["backup_version_id"])
            saved = _json(studio / f"{Path(proposal['image_path']).stem}.json")["annotations"]
            assert len(saved) == 1 and saved[0]["bbox"] == [10.0, 10.0, 20.0, 20.0]
            origin = saved[0]["origin"]
            assert origin["kind"] == "model_draft" and origin["proposal_id"] == proposal["id"]
            assert origin["candidate_id"] == candidate_id and origin["job_id"] == first["job_id"]
            assert origin["checkpoint_sha256"] == checkpoint_sha and origin["reviewer"] == "synthetic-reviewer"
            assert origin["backup_version_id"] == reviewed["backup_version_id"]
        assert len(set(backup_ids)) == 50
        assert len(list(studio.glob("*.json"))) == 70
        assert all((studio / name).read_bytes() == data for name, data in initial_labels.items())
        assert all(_sha(studio / "masks" / name) == digest for name, digest in initial_masks.items())
        before_first = api("GET", f"/api/dataset/versions/{backup_ids[0]}")
        assert before_first["kind"] == "auto_backup"
        backed_labels = [row for row in before_first["files"] if row["kind"] == "label"
                         and Path(row["relative_path"]).suffix == ".json" and len(Path(row["relative_path"]).parts) == 1]
        assert {row["relative_path"] for row in backed_labels} == set(initial_labels)
        assert all(row["sha256"] == hashlib.sha256(initial_labels[row["relative_path"]]).hexdigest() for row in backed_labels)

        # Same completed model survives the new labels, and is explicitly no longer current evidence.
        changed = api("GET", "/api/label-suggestions/models")["models"]
        assert [(row["job_id"], row["data_state"]) for row in changed] == [(first["job_id"], "changed")]
        reused = api("POST", "/api/label-suggestions/generate", json={
            "job_id": first["job_id"], "image_path": str(images[20]), "threshold": 0.5,
        })
        assert reused["model_data_state"] == "changed" and reused["status"] == "pending"
        label_hashes = {path.name: _sha(path) for path in studio.glob("*.json")}
        mask_hashes = {path.name: _sha(path) for path in (studio / "masks").glob("*.png")}
        api("POST", f"/api/label-suggestions/{reused['id']}/review", json={"decision": "reject", "actor": "synthetic-reviewer"})
        assert {path.name: _sha(path) for path in studio.glob("*.json")} == label_hashes
        assert {path.name: _sha(path) for path in (studio / "masks").glob("*.png")} == mask_hashes

        parents = api("GET", "/api/training/warm-start-parents", params={
            "dataset_path": str(source), "task": "detection", "preset": "fast", "backbone": options["backbone"],
        })["parents"]
        assert [parent["job_id"] for parent in parents] == [first["job_id"]]
        second = api("POST", "/api/training/start", json={
            "dataset_path": str(source), "task": "detection", "preset": "fast", "device": "cpu",
            "warm_start_job_id": first["job_id"], "config_overrides": {**options, "epochs": 5},
        })
        assert len(submissions) == 2 and second["job_id"] != first["job_id"]
        retrain = submissions[1]
        assert retrain["warm_start"].job_id == first["job_id"]
        assert retrain["warm_start"].checkpoint_sha256 == checkpoint_sha
        assert retrain["config_overrides"]["epochs"] == 5
        assert second["warm_start_parent_job_id"] == first["job_id"]
        assert second["training_provenance"]["dataset_version_id"] != old_binding["dataset_version_id"]
        assert retrain["dataset_fingerprint"] != initial["dataset_fingerprint"]
        assert second["training_provenance"]["dataset_fingerprint"] == retrain["dataset_fingerprint"]
        new_prepared = _json(Path(retrain["dataset_path"]) / "source_manifest.json")
        assert {row["source_image"] for row in new_prepared} == {str(image) for image in images}
        assert sum(len(_json(path)["images"]) for path in Path(retrain["dataset_path"]).glob("annotations_*.json")) == 70
        assert {path.name: _sha(path) for path in studio.glob("*.json")} == label_hashes
        assert {path.name: _sha(path) for path in (studio / "masks").glob("*.png")} == mask_hashes
        assert {image.name: _sha(image) for image in images} == source_hashes
        assert not list(source.glob("*.json"))
        assert _sha(checkpoint) == checkpoint_sha
        assert external_attempts == []

        receipt = {"scope": "synthetic route/storage composition only", "initial_labels": 20,
                   "initial_prepared_images": len(first_prepared), "pending_drafts_before_review": 50,
                   "explicit_api_reviews": 50, "unique_adoption_backups": len(set(backup_ids)),
                   "retrain_prepared_images": len(new_prepared), "model_reuse_state": "changed",
                   "warm_start_parent_matches": True, "new_training_version_and_fingerprint": True,
                   "epochs_submitted": retrain["config_overrides"]["epochs"],
                   "source_images_unchanged": 70, "learned_model_calls": 0, "training_worker_calls": 0,
                   "external_requests": 0, "training_ui_exercised": False,
                   "quality_or_human_acceptance": False}
        receipt_path = tmp_path / "composition-receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        print(f"composition_receipt={receipt_path}")

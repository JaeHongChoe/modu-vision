"""Adoption statistics: how a model's drafts fared with people, read without writing anything.

Counts per model: drafts, pending, adopted, rejected, reverted, adopted-then-edited (the take-back's own rule), and
the candidate confidences people adopted or rejected. The threshold advice stays quiet on small samples.
"""
import hashlib
import json
from pathlib import Path

import pytest

from backend.api import routes_annotation
from backend.api.routes_label_suggestions import threshold_advice
from backend.engine.dataset_fingerprint import fingerprint_dataset
from backend.tests.test_draft_adoption_undo import _adopt, _labels, _person_save
from backend.tests.test_label_suggestions import (  # noqa: F401  (pytest fixture)
    _refresh_receipt_fingerprint, suggestion_workspace,
)


def _generate(client, image):
    proposal = client.post("/api/label-suggestions/generate", json={"job_id": "job_12345_test", "image_path": str(image)}).json()
    assert proposal["status"] == "pending", proposal
    return proposal


def test_stats_count_each_outcome_and_write_nothing(suggestion_workspace):
    client, project, source, image, labelme, studio, _ = suggestion_workspace
    client.app.include_router(routes_annotation.router)
    # One rejected draft, one adoption left alone, one adoption edited afterwards, one adoption taken back.
    rejected = _generate(client, image)
    assert client.post(f"/api/label-suggestions/{rejected['id']}/review", json={"decision": "reject"}).status_code == 200
    kept, _ = _adopt(client, image, index=0)
    _refresh_receipt_fingerprint(project, source)
    edited, edited_candidate = _adopt(client, image, index=1)
    _person_save(client, image, lambda rows: [dict(row, bbox=[row["bbox"][0] + 1, *row["bbox"][1:]]) if row.get("id") == edited_candidate else row for row in rows])
    _refresh_receipt_fingerprint(project, source)
    reverted, _ = _adopt(client, image, index=0)
    assert client.post(f"/api/label-suggestions/{reverted['id']}/undo", json={}).status_code == 200
    pending = _generate(client, image)

    before = {path: path.read_bytes() for path in sorted(Path(project["project_dir"]).rglob("*.json"))}
    labels_before = _labels(studio)
    fingerprint_before = fingerprint_dataset(source, studio_root=Path(project["annotations_dir"]), use_scope=False)
    stats = client.get("/api/label-suggestions/stats")
    assert stats.status_code == 200, stats.text
    rows = stats.json()["models"]
    assert len(rows) == 1
    row = rows[0]
    assert row["job_id"] == "job_12345_test" and row["backend"] == "trained_model"
    assert (row["drafts"], row["pending"], row["adopted"], row["rejected"], row["reverted"]) == (5, 1, 2, 1, 1)
    assert row["edited_after"] == 1 and row["edited_unknown"] == 0
    # Candidates: the rejected draft's two (0.91, 0.72) and, per adoption, the chosen one adopted and the other rejected.
    assert row["adopted_candidates"] == 2 and row["rejected_candidates"] == 4 and row["reviewed_candidates"] == 6
    assert row["mean_confidence_adopted"] == round((0.91 + 0.72) / 2, 4)
    assert row["advice"]["reason"] == "too_few" and row["advice"]["reviewed_candidates"] == 6
    assert {path: path.read_bytes() for path in sorted(Path(project["project_dir"]).rglob("*.json"))} == before
    assert _labels(studio) == labels_before
    assert fingerprint_dataset(source, studio_root=Path(project["annotations_dir"]), use_scope=False) == fingerprint_before
    assert client.get("/api/label-suggestions/stats", params={"job_id": "job_other"}).json()["models"] == []
    assert client.get(f"/api/label-suggestions/{pending['id']}").json()["status"] == "pending"


def test_threshold_advice_is_quiet_on_small_samples_and_names_a_cut_people_already_trust():
    assert threshold_advice("classification", [(0.9, True)] * 30)["reason"] == "task_not_applicable"
    assert threshold_advice("detection", [(0.9, True)] * 19)["reason"] == "too_few"
    assert threshold_advice("detection", [(0.9, True)] * 19 + [(0.3, False)])["reason"] == "already_high"
    mixed = [(0.95, True)] * 12 + [(0.6, False)] * 10 + [(0.4, True)] * 3
    advice = threshold_advice("detection", mixed)
    assert advice["reason"] == "suggestion" and advice["threshold"] == 0.65 and advice["candidates_above"] == 12 and advice["adopted_rate_above"] == 1.0
    assert threshold_advice("detection", [(0.9, False)] * 15 + [(0.9, True)] * 10)["reason"] == "none_reaches"
    # The cut is for the trained model's slider; other draft sources score on their own scales.
    assert threshold_advice("detection", mixed, "template_match")["reason"] == "task_not_applicable"


@pytest.mark.parametrize("how", ["moved_out_of_the_source", "labels_unreadable"])
def test_stats_count_an_adoption_they_can_no_longer_check_as_unknown(suggestion_workspace, tmp_path, how):
    """Moving images out of the source is a supported flow; the summary keeps working and says it cannot tell."""
    client, _, _, image, labelme, studio, _ = suggestion_workspace
    client.app.include_router(routes_annotation.router)
    _adopt(client, image, index=0)
    if how == "moved_out_of_the_source":
        holding = tmp_path / "holding"
        holding.mkdir()
        image.rename(holding / image.name)
        labelme.rename(holding / labelme.name)
    else:
        (studio / "first.json").write_text("{not json")
    stats = client.get("/api/label-suggestions/stats")
    assert stats.status_code == 200, stats.text
    [row] = stats.json()["models"]
    assert (row["drafts"], row["adopted"], row["edited_after"], row["edited_unknown"]) == (1, 1, 0, 1)
    assert row["adopted_candidates"] == 1 and row["rejected_candidates"] == 1


_MALFORMED_ID = "suggestion_" + "a" * 24


@pytest.mark.parametrize("fields,expected", [
    # Files the summary cannot read at all are left out: only the real rejected draft is counted.
    ({"candidates": None}, (1, 0, 1, 0, 2, 2, 0)),
    ({"backend": {"name": "trained_model"}}, (1, 0, 1, 0, 2, 2, 0)),
    ({"job_id": 7}, (1, 0, 1, 0, 2, 2, 0)),
    ({"status": "accepted", "accepted_candidate_ids": "x_c1"}, (1, 0, 1, 0, 2, 2, 0)),
    # A candidate without a usable confidence is a judged candidate, but stays out of the means and the advice.
    ({"candidates": [{"id": "x_c1", "confidence": "high", "annotation": {}}]}, (2, 0, 2, 0, 3, 2, 0)),
    ({"candidates": [{"id": "x_c1", "confidence": float("nan"), "annotation": {}}]}, (2, 0, 2, 0, 3, 2, 0)),
    # Adoptions whose record cannot be checked against the labels count as edited_unknown.
    ({"status": "accepted", "accepted_candidate_ids": [["x_c1"]], "candidates": [{"id": "x_c1", "confidence": 0.9}]},
     (2, 1, 1, 0, 3, 3, 1)),
    ({"status": "accepted", "accepted_candidate_ids": ["x_c1"], "candidates": [{"id": "x_c1", "confidence": 0.9}],
      "adopted_annotations": [{"label": "defect"}]}, (2, 1, 1, 1, 2, 3, 1)),
])
def test_stats_leave_out_a_malformed_draft_file_on_its_own(suggestion_workspace, fields, expected):
    """One hand-edited or half-written draft file must not take the whole summary down (the list skips such files too)."""
    client, project, _, image, *_ = suggestion_workspace
    client.app.include_router(routes_annotation.router)
    rejected = _generate(client, image)
    assert client.post(f"/api/label-suggestions/{rejected['id']}/review", json={"decision": "reject"}).status_code == 200
    body = {"id": _MALFORMED_ID, "project_id": project["id"], "status": "rejected", "task": "detection",
            "image_path": str(image), "job_id": "job_12345_test", "backend": "trained_model", "candidates": [], **fields}
    (Path(project["project_dir"]) / "label_suggestions" / f"{_MALFORMED_ID}.json").write_text(json.dumps(body))
    stats = client.get("/api/label-suggestions/stats")
    assert stats.status_code == 200, stats.text
    [row] = stats.json()["models"]
    assert (row["drafts"], row["adopted"], row["rejected"], row["adopted_candidates"], row["rejected_candidates"],
            row["reviewed_candidates"], row["edited_unknown"]) == expected
    assert row["mean_confidence_rejected"] is not None


def test_stats_on_a_project_without_drafts_create_nothing(suggestion_workspace):
    client, project, *_ = suggestion_workspace
    folder = Path(project["project_dir"]) / "label_suggestions"
    assert not folder.exists()
    stats = client.get("/api/label-suggestions/stats")
    assert stats.status_code == 200, stats.text
    assert stats.json()["models"] == []
    assert not folder.exists(), "a read creates no suggestion folder"

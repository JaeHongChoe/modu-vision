"""Inspection history CSV: the decisive score, when each image was judged and on which device (audit F-19 / B2).

The new columns are appended after the existing ones, so readers that use column positions keep working.
"""
import csv
import io

from backend.tests.test_inspection_history import client_for, seed_old_result


def test_the_csv_adds_score_time_and_device_after_the_existing_columns(tmp_path):
    client = client_for(tmp_path)
    project = client.post("/api/project/create", json={"name": "ceramic"}).json()
    images = [{"image_id": "a", "file_path": "/data/a.png", "file_name": "a.png", "split": "test"},
              {"image_id": "b", "file_path": "/data/b.png", "file_name": "b.png", "split": "test"}]
    run_id = client.post("/api/inspections/runs", json={
        "source_folder": "/data", "task": "classification", "scope": "test",
        "pipeline": {"id": "flow", "name": "test", "nodes": [], "edges": []}, "images": images}).json()["run_id"]
    seed_old_result(project["project_dir"], run_id, "/data/a.png", "NG", {
        "final_verdict": "NG", "total_latency_ms": 12.5, "execution_device": "mps", "device_name": "Apple GPU",
        "crops": [{"defect_score": 0.31}, {"defect_score": 0.87}]})
    # Stopping leaves the image that was never inspected as skipped.
    assert client.put(f"/api/inspections/runs/{run_id}/finish", json={"status": "stopped"}).status_code == 200
    content = client.get(f"/api/inspections/runs/{run_id}/export", params={"format": "csv"}).json()["content"]
    header = next(csv.reader(io.StringIO(content)))
    assert header[:14] == ["run_id", "saved_version_id", "pipeline_hash", "model_sha256", "image_sha256", "image_path",
                           "image_id", "split", "model_state", "model_verdict", "operator_verdict", "reviewer",
                           "review_reason", "error"]
    assert header[14:] == ["defect_score", "inspected_at", "execution_device", "device_name", "latency_ms"]
    rows = {row["image_id"]: row for row in csv.DictReader(io.StringIO(content))}
    judged, skipped = rows["a"], rows["b"]
    assert (judged["defect_score"], judged["execution_device"], judged["device_name"], judged["latency_ms"]) == ("0.87", "mps", "Apple GPU", "12.5")
    assert judged["inspected_at"], 'a judged image says when it was judged'
    assert skipped["model_state"] == "skipped" and skipped["defect_score"] == skipped["inspected_at"] == ""

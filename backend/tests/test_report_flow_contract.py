"""Report exports must identify the model that was actually evaluated."""

from __future__ import annotations

import json

import pytest
from PIL import Image

from backend.api import routes_report


def test_detection_report_calls_top_class_match_only_and_explains_object_metric(tmp_path, monkeypatch):
    image = tmp_path / "sample.png"
    Image.new("RGB", (12, 12), "white").save(image)
    monkeypatch.setattr(routes_report, "_find_model_file", lambda _job_id: None)
    monkeypatch.setattr(routes_report, "get_device", lambda: "cpu")
    html = routes_report._render_standalone_html(
        "job_detection", {
            "task": "detection", "metrics": {"mAP_50": 0.0},
            "confusion_matrix": {"classes": ["background", "Bow"], "matrix": [[0, 0], [2, 6]]},
            "test_predictions": [{
                "image_id": "1", "file_path": str(image), "ground_truth": "Bow",
                "predicted_class": "Bow", "confidence": 0.2088, "is_correct": True,
            }],
        }, "2026-09-29T00:00:00Z",
    )

    assert "Top box class table" in html
    assert "threshold" in html.lower() and "IoU" in html
    assert "TOP CLASS MATCH" in html
    assert ">PASS<" not in html


@pytest.mark.parametrize("report_format", ["json", "html"])
def test_latest_report_uses_resolved_model_id(tmp_path, monkeypatch, report_format):
    # Evaluation is the expensive dependency; the report assembly and disk write
    # remain real. The returned job_id is what the completed model actually used.
    monkeypatch.setattr(
        routes_report,
        "run_or_load_evaluation",
        lambda **_kwargs: {
            "job_id": "job_1790668121_694c50",
            "task": "segmentation",
            "metrics": {"miou": 0.49},
            "confusion_matrix": {"classes": ["OK", "Defect"], "matrix": [[0, 0], [5, 11]]},
            "test_predictions": [],
        },
    )
    target = tmp_path / f"report.{report_format}"

    result = routes_report.export_report(
        routes_report.ReportExportRequest(
            job_id="latest", format=report_format, include_images=False,
            output_path=str(target),
        )
    )

    assert target.is_file()
    assert result["file_path"] == str(target)
    if report_format == "json":
        payload = json.loads(target.read_text(encoding="utf-8"))
        assert payload["job_id"] == "job_1790668121_694c50"
    else:
        html = target.read_text(encoding="utf-8")
        assert "<strong>Job ID:</strong> job_1790668121_694c50" in html

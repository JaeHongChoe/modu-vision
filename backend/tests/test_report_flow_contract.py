"""Report exports must identify the model that was actually evaluated."""

from __future__ import annotations

import json

import pytest

from backend.api import routes_report


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

"""
backend/api/routes_report.py

Standalone Single-File HTML and JSON Industrial Inspection Report Exporter.
Features:
  - Zero external web asset dependencies (fully offline shop-floor viewable)
  - Embedded CSS styling with clean typography and print-to-PDF (@media print) rules
  - Real base64-encoded defect inspection overlays generated via infer() on dataset images
  - Machine-readable JSON export for MES/SCADA integration
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.api.routes_evaluation import run_or_load_evaluation, _find_model_file
from backend.api.routes_training import training_job_manager
from backend.engine.device import get_device
from backend.engine.trainer import infer

logger = logging.getLogger("vision_ai_studio.routes_report")

router = APIRouter(prefix="/api/report", tags=["report"])

REPORTS_DIR = Path("./reports")
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


class ReportExportRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    job_id: Optional[str] = "latest"
    format: Literal["html", "json"] = "html"
    include_images: bool = True
    output_path: Optional[str] = None


def _render_standalone_html(
    job_id: str,
    eval_data: Dict[str, Any],
    created_at: str,
    include_images: bool = True,
) -> str:
    """Renders single-file HTML report with real evaluation images and zero external dependencies."""
    metrics = eval_data.get("metrics", {})
    cm = eval_data.get("confusion_matrix", {})
    classes = cm.get("classes") or cm.get("class_names") or ["OK", "Defect"]
    matrix = cm.get("matrix", [])
    task_name = str(eval_data.get("task", "classification")).lower()
    is_detection = task_name == "detection"
    task = task_name.capitalize()
    matrix_title = "Top box class table" if is_detection else "Confusion Matrix"
    matrix_explanation = (
        "<p>The highest-scoring box class per image is compared without a score threshold or IoU match. "
        "This class match is separate from mAP@IoU 0.5 object localization and from image-level OK/NG at a chosen threshold.</p>"
        if is_detection else ""
    )
    test_preds = eval_data.get("test_predictions", [])

    # Generate metric rows
    metric_cards = []
    for k, v in metrics.items():
        if isinstance(v, (int, float)):
            val_str = f"{v:.4f}" if isinstance(v, float) else str(v)
            metric_cards.append(f"""
            <div class="card">
                <div class="card-title">{k.replace('_', ' ').title()}</div>
                <div class="card-val">{val_str}</div>
            </div>
            """)
    metric_cards_html = "".join(metric_cards)

    # Generate confusion matrix table rows
    cm_header = "".join([f"<th>{c} (Pred)</th>" for c in classes])
    cm_rows = []
    for i, row in enumerate(matrix):
        c_true = classes[i] if i < len(classes) else f"Class_{i}"
        tds = "".join([f"<td class='{'diag' if i==j else 'off-diag'}'>{val}</td>" for j, val in enumerate(row)])
        cm_rows.append(f"<tr><th>{c_true} (True)</th>{tds}</tr>")
    cm_table_html = "".join(cm_rows)

    # Render genuine inspection sample cards using real predictions & infer overlays
    samples_html = ""
    if include_images:
        model_file = _find_model_file(job_id)
        sample_cards = []

        # Select up to 4 representative test prediction images: errors first, then correct
        selected_samples = (
            [p for p in test_preds if not p.get("is_correct")][:2] +
            [p for p in test_preds if p.get("is_correct")][:2]
        )
        if len(selected_samples) < 4:
            for p in test_preds:
                if p not in selected_samples:
                    selected_samples.append(p)
                if len(selected_samples) >= 4:
                    break

        dev = get_device()
        for sample in selected_samples:
            f_path = sample.get("file_path")
            if not f_path or not os.path.exists(f_path):
                continue

            overlay_b64 = ""
            if model_file and model_file.is_file():
                try:
                    res = infer(
                        task=eval_data.get("task", "classification"),
                        model_path=model_file,
                        image_input=f_path,
                        threshold=0.5,
                        device=dev,
                    )
                    overlay_bgr = cv2.cvtColor(res.visual_overlay, cv2.COLOR_RGB2BGR)
                    ok, buf = cv2.imencode(".png", overlay_bgr)
                    if ok:
                        overlay_b64 = f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('utf-8')}"
                except Exception as ex:
                    logger.warning("Failed to run infer overlay on sample %s: %s", f_path, ex)

            if not overlay_b64:
                raw_bgr = cv2.imread(f_path)
                if raw_bgr is not None:
                    ok, buf = cv2.imencode(".png", raw_bgr)
                    if ok:
                        overlay_b64 = f"data:image/png;base64,{base64.b64encode(buf.tobytes()).decode('utf-8')}"

            if not overlay_b64:
                continue

            pred_class = sample.get("predicted_class", "Unknown")
            gt_class = sample.get("ground_truth", "Unknown")
            conf = float(sample.get("confidence", 0.0))
            is_correct = bool(sample.get("is_correct", True))
            status_text = (
                "TOP CLASS MATCH" if is_correct else "TOP CLASS MISMATCH"
            ) if is_detection else ("PASS" if is_correct else "MISMATCH")
            status_class = "status-ok" if is_correct else "status-err"
            score_label = "Highest box score" if is_detection else "Confidence"

            sample_cards.append(f"""
            <div class="sample-card">
                <img src="{overlay_b64}" alt="Inspection Sample: {pred_class}" />
                <div class="sample-caption">
                    <div><strong>{pred_class}</strong> (GT: {gt_class})</div>
                    <div class="sample-sub">{score_label}: {conf:.1%} <span class="badge {status_class}">{status_text}</span></div>
                </div>
            </div>
            """)

        if sample_cards:
            samples_grid = "".join(sample_cards)
            samples_html = f"""
            <div class="section">
                <h2>Inspection Samples & Defect Heatmaps</h2>
                <div class="samples-grid">
                    {samples_grid}
                </div>
            </div>
            """
        else:
            samples_html = """
            <div class="section">
                <h2>Inspection Samples & Defect Heatmaps</h2>
                <p style="color: #64748b; font-size: 14px;">No evaluation test sample images available.</p>
            </div>
            """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Vision AI Studio — Inspection & Quality Report [{job_id}]</title>
    <style>
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #f8fafc;
            color: #0f172a;
            line-height: 1.5;
            padding: 32px;
        }}
        .container {{
            max-width: 960px;
            margin: 0 auto;
            background: #ffffff;
            border-radius: 8px;
            border: 1px solid #e2e8f0;
            padding: 40px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #0284c7;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }}
        .header h1 {{
            font-size: 24px;
            font-weight: 700;
            color: #0369a1;
        }}
        .header .meta {{
            font-size: 13px;
            color: #64748b;
            text-align: right;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }}
        .badge-main {{
            background-color: #e0f2fe;
            color: #0369a1;
            margin-top: 6px;
        }}
        .status-ok {{
            background-color: #dcfce7;
            color: #166534;
        }}
        .status-err {{
            background-color: #fee2e2;
            color: #991b1b;
        }}
        .section {{
            margin-bottom: 36px;
        }}
        .section h2 {{
            font-size: 18px;
            font-weight: 600;
            color: #1e293b;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 8px;
            margin-bottom: 16px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 16px;
        }}
        .card {{
            background: #f1f5f9;
            padding: 16px;
            border-radius: 6px;
            border: 1px solid #cbd5e1;
        }}
        .card-title {{
            font-size: 12px;
            text-transform: uppercase;
            font-weight: 600;
            color: #475569;
        }}
        .card-val {{
            font-size: 22px;
            font-weight: 700;
            color: #0f172a;
            margin-top: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 12px;
            font-size: 14px;
        }}
        th, td {{
            padding: 10px 14px;
            text-align: center;
            border: 1px solid #cbd5e1;
        }}
        th {{
            background-color: #f8fafc;
            color: #334155;
            font-weight: 600;
        }}
        td.diag {{
            background-color: #dcfce7;
            color: #166534;
            font-weight: 700;
        }}
        td.off-diag {{
            background-color: #fee2e2;
            color: #991b1b;
        }}
        .samples-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-top: 12px;
        }}
        .sample-card {{
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            overflow: hidden;
            background: #ffffff;
            text-align: left;
        }}
        .sample-card img {{
            width: 100%;
            height: auto;
            display: block;
            background: #000;
        }}
        .sample-caption {{
            padding: 10px 12px;
            font-size: 13px;
            color: #1e293b;
            background: #f8fafc;
            border-top: 1px solid #e2e8f0;
        }}
        .sample-sub {{
            font-size: 12px;
            color: #64748b;
            margin-top: 4px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .footer {{
            border-top: 1px solid #e2e8f0;
            padding-top: 16px;
            margin-top: 30px;
            font-size: 12px;
            color: #94a3b8;
            display: flex;
            justify-content: space-between;
        }}
        @media print {{
            body {{
                padding: 0;
                background: #ffffff;
            }}
            .container {{
                box-shadow: none;
                border: none;
                padding: 20px;
                max-width: 100%;
            }}
            .section {{
                page-break-inside: avoid;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div>
                <h1>Vision AI Studio — Quality Evaluation Report</h1>
                <div class="badge badge-main">{task} AutoML Model</div>
            </div>
            <div class="meta">
                <div><strong>Job ID:</strong> {job_id}</div>
                <div><strong>Generated:</strong> {created_at}</div>
                <div><strong>Status:</strong> Evaluated</div>
            </div>
        </div>

        <div class="section">
            <h2>Primary Quality Metrics</h2>
            <div class="grid">
                {metric_cards_html}
            </div>
        </div>

        <div class="section">
            <h2>{matrix_title}</h2>
            {matrix_explanation}
            <table>
                <thead>
                    <tr>
                        <th>Ground Truth / Predicted</th>
                        {cm_header}
                    </tr>
                </thead>
                <tbody>
                    {cm_table_html}
                </tbody>
            </table>
        </div>

        {samples_html}

        <div class="footer">
            <div>Vision AI Studio v0.1.0 • Industrial Computer Vision Engine</div>
            <div>Strictly Confidential — For Internal Inspection Only</div>
        </div>
    </div>
</body>
</html>
"""
    return html


@router.post("/export")
def export_report(req: ReportExportRequest, request: Request = None):
    """
    Exports quality evaluation summary as standalone HTML or JSON report.
    Returns { file_path: str, content?: str }.
    """
    requested_job_id = req.job_id or "latest"
    eval_data = run_or_load_evaluation(job_id=requested_job_id)
    job_id = str(eval_data.get("job_id") or requested_job_id)
    reports_dir = REPORTS_DIR
    if request is not None:
        from backend.api.routes_project import get_current_project
        reports_dir = Path(get_current_project(request)["reports_dir"])

    now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    now_iso = time.strftime("%Y%m%d_%H%M%S", time.gmtime())

    if req.format == "json":
        out_file = Path(req.output_path or (reports_dir / f"report_{job_id}_{now_iso}.json")).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        report_data = {
            "title": "Vision AI Studio Quality Evaluation Report",
            "job_id": job_id,
            "created_at": now_str,
            "evaluation": eval_data,
        }
        content = json.dumps(report_data, indent=2, ensure_ascii=False)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)
        return {
            "status": "success",
            "file_path": str(out_file),
            "format": "json",
            "content": content,
            "file_size_bytes": len(content.encode("utf-8")),
        }
    else:
        out_file = Path(req.output_path or (reports_dir / f"report_{job_id}_{now_iso}.html")).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        html_content = _render_standalone_html(
            job_id=job_id,
            eval_data=eval_data,
            created_at=now_str,
            include_images=req.include_images,
        )
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(html_content)
        return {
            "status": "success",
            "file_path": str(out_file),
            "format": "html",
            "content": html_content,
            "file_size_bytes": len(html_content.encode("utf-8")),
        }


@router.get("/view/{report_id}")
def view_report(report_id: str, request: Request = None):
    """Serves the generated HTML report directly for browser display."""
    reports_dir = REPORTS_DIR
    if request is not None:
        from backend.api.routes_project import get_current_project
        reports_dir = Path(get_current_project(request)["reports_dir"])
    target_file = reports_dir / f"{report_id}.html"
    if not target_file.exists():
        target_file = reports_dir / report_id
    if not target_file.is_file():
        raise HTTPException(status_code=404, detail="Report file not found")

    with open(target_file, "r", encoding="utf-8") as f:
        html_content = f.read()

    return HTMLResponse(content=html_content)

"""The actual export route retains its chosen target in every parity record.

The parity engine is controlled here; these are record propagation checks,
not independent model execution or a representative quality decision.
"""
import json
from pathlib import Path

import pytest

from backend.engine import flow_package
from backend.tests.test_flow_export_release import _export, _library_parity, _project_flow


@pytest.mark.parametrize("status", ["passed", "mismatch"])
@pytest.mark.parametrize("scope", ["single_image", "cohort"])
def test_local_export_target_survives_engine_report_in_response_receipt_and_library(tmp_path, monkeypatch, status, scope):
    client, project, source, images, _ = _project_flow(tmp_path, monkeypatch, count=2)
    calls = []

    def controlled_parity(**kwargs):
        calls.append(kwargs)
        return {"contract": "flow_parity_v1", "status": status, "scope": kwargs["scope"],
                "device": kwargs["device"], "image_count": len(kwargs["images"]),
                "completed_count": len(kwargs["images"]), "error": None,
                **flow_package._parity_identity(kwargs["package_dir"])}

    monkeypatch.setattr(flow_package, "verify_flow_parity_cohort", controlled_parity)
    options = ({"verification_image_path": str(images[0])} if scope == "single_image" else
               {"parity_images": [{"path": str(image)} for image in images], "parity_device": "cpu"})
    response = _export(client, source, "local_target_" + scope + "_" + status, **options)
    assert response.status_code == (200 if status == "passed" else 409), response.text
    result = response.json() if status == "passed" else response.json()["detail"]
    assert len(calls) == 1 and calls[0]["scope"] == scope and calls[0]["device"] == "cpu"
    parity = result["parity"]
    assert parity["execution_target"] == "local" and parity["compute_profile_id"] is None
    assert parity["status"] == status and parity["scope"] == scope
    saved = json.loads((Path(result["package_path"]) / "parity_receipt.json").read_bytes())
    assert saved == {"schema_version": 1, **parity}
    assert _library_parity(project, result["package_path"]) == parity

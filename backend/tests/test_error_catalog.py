"""
backend/tests/test_error_catalog.py

Comprehensive Pytest Suite for Feature F12: Bilingual Industrial Error Catalog.
Validates:
  - Complete 8-item master catalog registration (ERR_001 to ERR_008)
  - Bilingual parity (Korean and English titles, descriptions, remediations, causes)
  - Convenience alias properties (title_ko, message_ko, error_code, etc.)
  - Exception classifier heuristic accuracy across all industrial failure modes
  - Standard JSON response formatting and WebSocket payload generation
"""

import pytest
from backend.utils.error_catalog import (
    CATALOG,
    ErrorCatalogItem,
    ErrorSeverity,
    classify_exception,
    format_error_response,
    get_all_errors,
    get_error,
)


class TestErrorCatalogRegistration:
    """Verifies all 8 industrial error codes are registered and well-formed."""

    EXPECTED_CODES = [
        "ERR_001",
        "ERR_002",
        "ERR_003",
        "ERR_004",
        "ERR_005",
        "ERR_006",
        "ERR_007",
        "ERR_008",
    ]

    def test_all_eight_codes_present(self):
        catalog = get_all_errors()
        assert len(catalog) == 8, f"Expected exactly 8 error codes, found {len(catalog)}"
        for code in self.EXPECTED_CODES:
            assert code in catalog, f"Missing expected error code: {code}"

    @pytest.mark.parametrize("code", EXPECTED_CODES)
    def test_bilingual_fields_populated(self, code: str):
        item = get_error(code)
        assert item is not None
        assert item.code == code
        # English fields
        assert item.title_en and len(item.title_en.strip()) > 3
        assert item.description_en and len(item.description_en.strip()) > 10
        assert item.remediation_en and len(item.remediation_en.strip()) > 10
        assert item.cause_en and len(item.cause_en.strip()) > 5
        # Korean fields
        assert item.title_kr and len(item.title_kr.strip()) > 2
        assert item.description_kr and len(item.description_kr.strip()) > 10
        assert item.remediation_kr and len(item.remediation_kr.strip()) > 10
        assert item.cause_kr and len(item.cause_kr.strip()) > 5
        # Metadata
        assert isinstance(item.severity, ErrorSeverity)
        assert isinstance(item.auto_fixable, bool)
        assert item.action is not None

    def test_alias_properties(self):
        err = get_error("ERR_001")
        assert err.error_code == "ERR_001"
        assert err.title_ko == err.title_kr
        assert err.message_ko == err.description_kr
        assert err.message_en == err.description_en
        assert err.remediation_ko == err.remediation_kr
        assert err.remediation == err.remediation_kr

    def test_case_insensitive_lookup_and_aliases(self):
        assert get_error("err_001") == get_error("ERR_001")
        assert get_error("  ERR_002  ") == get_error("ERR_002")
        # Semantic aliases
        assert get_error("ERR_GPU_OOM") == get_error("ERR_001")
        assert get_error("ERR_CORRUPT_IMAGE") == get_error("ERR_005")
        assert get_error("ERR_DEGENERATE_BBOX") == get_error("ERR_004")
        assert get_error("ERR_ANOMALY_DEFECT_IN_TRAIN") == get_error("ERR_006")
        assert get_error("NON_EXISTENT_CODE") is None


class TestExceptionClassification:
    """Tests automatic classification of Python and PyTorch runtime exceptions."""

    def test_classify_cuda_oom(self):
        exc = RuntimeError("CUDA error: out of memory. Tried to allocate 2.00 GiB")
        classified = classify_exception(exc)
        assert classified.code == "ERR_001"
        assert "OOM" in classified.title_en or "Memory" in classified.title_en

    def test_classify_mps_oom(self):
        exc = RuntimeError("MPS backend out of memory (allocate 1024 MB)")
        classified = classify_exception(exc)
        assert classified.code == "ERR_001"

    def test_classify_anomaly_contamination(self):
        exc = ValueError("Dataset must contain exclusively normal images for anomaly training split")
        classified = classify_exception(exc)
        assert classified.code == "ERR_006"
        assert classified.auto_fixable is False

    def test_classify_corrupted_image(self):
        exc = IOError("Cannot identify image file /tmp/broken.png: header magic bytes corrupted")
        classified = classify_exception(exc)
        assert classified.code == "ERR_005"
        assert classified.auto_fixable is True

    def test_classify_degenerate_bbox(self):
        exc = ValueError("Invalid box width: xmin >= xmax inverted bounding box")
        classified = classify_exception(exc)
        assert classified.code == "ERR_004"

    def test_classify_nan_divergence(self):
        exc = RuntimeError("Loss exploded: gradient NaN or Inf detected during backprop")
        classified = classify_exception(exc)
        assert classified.code == "ERR_008"

    def test_classify_class_imbalance(self):
        exc = ValueError("Extreme class imbalance detected: ratio > 20:1")
        classified = classify_exception(exc)
        assert classified.code == "ERR_003"

    def test_classify_insufficient_samples(self):
        exc = ValueError("Single class detected; supervised training requires at least 2 classes")
        classified = classify_exception(exc)
        assert classified.code == "ERR_002"

    def test_classify_unknown_fallback(self):
        exc = KeyError("some_random_internal_key_missing")
        classified = classify_exception(exc, details="Key error at step 4")
        assert classified.code == "ERR_UNKNOWN"
        assert classified.details == "Key error at step 4"


class TestErrorFormattingAndWebSocketPayload:
    """Validates serialized formatting for REST responses and WebSocket streaming."""

    def test_format_error_response_valid_code(self):
        res = format_error_response("ERR_001", details={"batch_size": 32})
        assert res["status"] == "error"
        assert res["error_code"] == "ERR_001"
        assert "OOM" in res["title_en"] or "Memory" in res["title_en"]
        assert "메모리" in res["title_kr"]
        assert res["auto_fixable"] is True
        assert res["severity"] == "critical"
        assert res["details"] == {"batch_size": 32}

    def test_format_error_response_unknown_code(self):
        res = format_error_response("ERR_NON_EXISTENT", custom_message_en="Custom error")
        assert res["status"] == "error"
        assert res["error_code"] == "ERR_NON_EXISTENT"
        assert res["title_en"] == "Unknown Error"
        assert res["description_en"] == "Custom error"

    def test_ws_payload_serialization(self):
        err = get_error("ERR_004")
        payload = err.to_ws_payload()
        assert payload["error_code"] == "ERR_004"
        assert "message_ko" in payload
        assert "message_en" in payload
        assert "remediation" in payload
        assert payload["action"] == "auto_sanitize_bboxes"

"""Original ASGI route diagnostics: fixed GET labels grant no new coverage.

Only the existing isolated admission/middleware fixture and real Response
frames run. Literal expectations are independent of the producer route maps.
The paired UNKNOWN request checks that projection changes no other evidence.
"""
import asyncio
import json

import pytest
from starlette.responses import Response

from backend.tests.test_first_unsupported_origin_diagnostic import origin
from backend.tests.test_original_request_activity_witness import receive, scope, setup


# Public literal contracts, deliberately not imported from a producer dictionary.
GET_ROUTES = (
    ("/api/flowchart/pipeline", "FLOW_PIPELINE"),
    ("/api/flowchart/templates/single-segmentation", "FLOW_SINGLE_SEG_TEMPLATE"),
    ("/api/evaluation/results", "EVALUATION_RESULTS"),
    ("/api/flowchart/models/catalog", "FLOW_MODEL_CATALOG"),
    ("/api/flowchart/pipelines", "FLOW_PIPELINES"),
    ("/api/flowchart/pipeline/active/record", "FLOW_ACTIVE_RECORD"),
    ("/api/flowchart/pipeline/active-version", "FLOW_ACTIVE_VERSION"),
    ("/api/flowchart/draft", "FLOW_DRAFT"),
    ("/api/compute/profiles", "COMPUTE_PROFILES"),
    ("/api/compute/selection", "COMPUTE_SELECTION"),
    ("/api/flow-workspace/templates", "FLOW_WORKSPACE_TEMPLATES"),
    ("/api/flow-workspace/comparisons", "FLOW_WORKSPACE_COMPARISONS"),
    ("/api/dataset/images", "DATASET_IMAGES"),
    ("/api/dataset/library/images", "LIBRARY_IMAGES"),
    ("/api/flow-workspace/image-info", "FLOW_IMAGE_INFO"),
)
UNKNOWN_PATH = "/private_customer_dataset_token_not_exported"
PRIVATE_QUERY = b"token=private_query_not_exported"
PRIVATE_AUTH = b"Bearer private_authorization_not_exported"
PRIVATE_HEADER = b"private_header_not_exported"
RESPONSE_BODY = b"private_original_response_not_exported"
FALSE_FLAGS = (
    "whole_writer_coverage", "process_tree_exit_verified", "can_release_launch_lease",
)


def _private_scope(path, method="GET"):
    original = scope(path)
    original["method"] = method
    original["query_string"] = PRIVATE_QUERY
    original["headers"] = [
        (b"authorization", PRIVATE_AUTH), (b"x-private", PRIVATE_HEADER),
    ]
    return original


async def _exercise(path, *, method="GET", preceding_health_requests=0):
    """Observe genuine middleware-owned requests, including a live health row."""
    async def app(original_scope, original_receive, original_send):
        request_body = await original_receive()
        assert request_body["body"] == b"private_body_not_to_record"
        if original_scope["path"] == "/health":
            payload = {
                "diagnostic": held.health_diagnostic(original_scope),
                "first_origin": origin(held),
            }
            response = Response(json.dumps(payload).encode(), media_type="application/json")
        else:
            response = Response(RESPONSE_BODY, status_code=200)
        await response(original_scope, original_receive, original_send)

    held, admission, middleware = setup(app)

    async def discard(message):
        pass

    for _ in range(preceding_health_requests):
        await middleware(_private_scope("/health"), receive, discard)
    assert admission.snapshot() == {"active_scopes": 0, "unsupported": []}

    frames = []
    async def capture(message):
        frames.append(dict(message))

    await middleware(_private_scope(path, method), receive, capture)
    assert next(frame["status"] for frame in frames
                if frame["type"] == "http.response.start") == 200
    assert b"".join(frame.get("body", b"") for frame in frames
                    if frame["type"] == "http.response.body") == RESPONSE_BODY
    expected_admission = {
        "active_scopes": 0, "unsupported": ["uncovered_request_protocol"],
    }
    assert admission.snapshot() == expected_admission
    first_before = origin(held)["first"]
    assert first_before["source"] == "DIRECT_ENTER"
    assert first_before["family"] == "ADMISSION"
    assert first_before["request_identity"] == preceding_health_requests + 1
    assert first_before["method"] == method
    assert first_before["entered_sequence"] == first_before["observed_sequence"]
    if preceding_health_requests == 0:
        assert first_before["entered_sequence"] == 1

    frames.clear()
    await middleware(_private_scope("/health"), receive, capture)
    assert next(frame["status"] for frame in frames
                if frame["type"] == "http.response.start") == 200
    payload = json.loads(b"".join(frame.get("body", b"") for frame in frames
                                 if frame["type"] == "http.response.body"))
    health = payload["diagnostic"]
    assert health is not None
    assert payload["first_origin"]["first"] == first_before
    assert health["request_count"] == preceding_health_requests + 2
    assert health["diagnostic_request_identity"] == preceding_health_requests + 2
    assert health["diagnostic_request_in_flight"] is True
    assert health["pending_records"] == 1 and health["uncertain"] is False
    assert admission.snapshot() == expected_admission

    admission.close()
    final = held.final_drain_diagnostic(admission)
    assert final is not None
    assert final["request_count"] == preceding_health_requests + 2
    assert final["active_scopes"] == 0
    assert final["unsupported"] == ["uncovered_request_protocol"]
    assert final["uncertain"] is False and final["reason_sha256"] == []
    assert held._failures == [] and held._uncertain is False
    assert origin(held)["first"] == first_before
    assert admission.snapshot() == expected_admission
    for projection in (health, final, payload["first_origin"]):
        assert all(projection[flag] is False for flag in FALSE_FLAGS)

    encoded = json.dumps((payload, final))
    for private in (
        UNKNOWN_PATH, PRIVATE_QUERY.decode(), PRIVATE_AUTH.decode(), PRIVATE_HEADER.decode(),
        "private_body_not_to_record", RESPONSE_BODY.decode(), path,
    ):
        assert private not in encoded
    for raw_key in ("query_string", "raw_path", "headers", "authorization"):
        assert raw_key not in encoded
    return health, final, payload["first_origin"], first_before


def _without_route(projection):
    return {
        **projection,
        "records": [{key: value for key, value in row.items() if key != "route"}
                    for row in projection["records"]],
    }


def _assert_target(health, final, *, identity, method, route):
    for projection in (health, final):
        rows = [row for row in projection["records"] if row["identity"] == identity]
        assert len(rows) == 1
        row = rows[0]
        assert row["protocol"] == "HTTP" and row["method"] == method
        assert row["status"] == 200
        assert row["accepted"] is row["released"] is row["completed"] is row["normal_return"] is True
        assert row["route"] == route


@pytest.mark.parametrize("path,expected_route", GET_ROUTES, ids=[row[1] for row in GET_ROUTES])
def test_fixed_public_get_labels_in_health_and_final_preserve_original_refusal(path, expected_route):
    async def scenario():
        health, final, first, first_before = await _exercise(path)
        control_health, control_final, control_first, control_before = await _exercise(UNKNOWN_PATH)
        # A route label must not change the original owner/sequence/method,
        # status, framing, refusal, uncertainty, counts, flags or origin.
        assert _without_route(health) == _without_route(control_health)
        assert _without_route(final) == _without_route(control_final)
        assert first == control_first and first_before == control_before
        _assert_target(control_health, control_final, identity=1, method="GET", route="UNKNOWN")
        _assert_target(health, final, identity=1, method="GET", route=expected_route)
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ("POST", "PUT", "DELETE"))
def test_new_labels_do_not_map_non_get_pipeline_requests(method):
    async def scenario():
        health, final, _, _ = await _exercise("/api/flowchart/pipeline", method=method)
        _assert_target(health, final, identity=1, method=method, route="UNKNOWN")
    asyncio.run(scenario())


@pytest.mark.parametrize("path", (
    UNKNOWN_PATH,
    "/api/flowchart/pipeline/private_customer_dataset_token_not_exported",
    "/api/flowchart/pipeline/",
))
def test_private_dynamic_and_near_literal_paths_stay_unknown(path):
    async def scenario():
        health, final, _, _ = await _exercise(path)
        _assert_target(health, final, identity=1, method="GET", route="UNKNOWN")
    asyncio.run(scenario())


def test_request_124_get_200_keeps_fixed_label_without_new_admission_or_origin_authority():
    async def scenario():
        health, final, first, _ = await _exercise(
            "/api/flowchart/models/catalog", preceding_health_requests=123,
        )
        control_health, control_final, control_first, _ = await _exercise(
            UNKNOWN_PATH, preceding_health_requests=123,
        )
        assert _without_route(health) == _without_route(control_health)
        assert _without_route(final) == _without_route(control_final)
        assert first == control_first and first["first"]["request_identity"] == 124
        _assert_target(health, final, identity=124, method="GET", route="FLOW_MODEL_CATALOG")
        assert final["truncated"] is True  # Original final16 cap remains unchanged.
        assert final["retained_records"] == 125 and final["omitted_records"] == 109
    asyncio.run(scenario())

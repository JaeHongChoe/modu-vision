"""Desktop API must reject web pages without the Electron process capability."""

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.websockets import WebSocketDisconnect

from backend.main import create_app


@pytest.mark.parametrize("origin", ["http://127.0.0.1:5173", "http://localhost:5173", "null"])
def test_desktop_renderer_origins_receive_cors_preflight(origin):
    client = TestClient(create_app())
    response = client.options(
        "/api/project/current",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "x-vision-token",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize("origin", ["https://attacker.example", "http://127.0.0.1:9999"])
def test_other_web_origins_have_no_cors_access(origin):
    client = TestClient(create_app())
    response = client.options(
        "/api/project/current",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_null_origin_cannot_read_json_or_local_images_without_token(tmp_path):
    app = create_app()
    client = TestClient(app)
    image_path = tmp_path / "private.png"
    Image.new("RGB", (4, 4)).save(image_path)

    json_response = client.get("/api/errors", headers={"Origin": "null"})
    image_response = client.get(
        "/api/dataset/raw/private.png",
        params={"file_path": str(image_path)},
        headers={"Origin": "null"},
    )

    assert json_response.status_code == 401
    assert image_response.status_code == 401


def test_process_token_allows_desktop_json_and_image_requests(tmp_path):
    app = create_app()
    client = TestClient(app)
    image_path = tmp_path / "private.png"
    Image.new("RGB", (4, 4)).save(image_path)
    headers = {"Origin": "null", "X-Vision-Token": app.state.api_token}

    assert client.get("/api/errors", headers=headers).status_code == 200
    image_response = client.get(
        "/api/dataset/raw/private.png", params={"file_path": str(image_path)}, headers=headers,
    )
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"


def test_websocket_requires_process_token_before_accepting():
    app = create_app()
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/ws/telemetry", headers={"Origin": "null"}):
                pass
        assert denied.value.code == 1008
        with client.websocket_connect(
            "/ws/telemetry", headers={"Origin": "null", "X-Vision-Token": app.state.api_token},
        ) as websocket:
            assert websocket.receive_json()["type"] == "hardware_stats"


def test_backend_uses_supervisor_process_token(monkeypatch):
    monkeypatch.setenv("VISION_AI_STUDIO_API_TOKEN", "unique-test-process-token")
    app = create_app()
    client = TestClient(app)

    assert app.state.api_token == "unique-test-process-token"
    assert client.get("/api/errors", headers={"X-Vision-Token": "unique-test-process-token"}).status_code == 200
    assert client.get("/api/errors", headers={"X-Vision-Token": "wrong-token"}).status_code == 401

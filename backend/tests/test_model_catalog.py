"""The catalog is reachable and reports actionable local model prerequisites."""
from fastapi.testclient import TestClient

from backend.engine import model_catalog
from backend.main import create_app


def test_catalog_mounted_at_frontend_api_contract():
    app = create_app()
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        response = client.get('/api/models/capabilities')
    assert response.status_code == 200
    families = {item['task']: item for item in response.json()['families']}
    assert families['classification']['default_architecture'] == 'dinov3_vits16'
    assert families['segmentation']['default_architecture'] == 'dinov3_vits16'
    assert families['detection']['default_architecture'] == 'yolo26n'
    assert families['rotated_detection']['remote_training'] is True
    assert families['ocr']['remote_training'] is True
    assert families['anomaly']['continuation'] == 'statistical_refit'
    assert all(item['quality_approved'] is False for item in families.values())
    from backend.engine.execution_recipe import support_matrix
    assert {task:item['execution'] for task,item in families.items()}==support_matrix()


def test_missing_encoder_dependency_is_reported_without_loading_weights(monkeypatch):
    monkeypatch.setattr(model_catalog, 'find_spec', lambda name: None)
    families = {item['task']: item for item in model_catalog.model_family_catalog()['families']}
    assert families['classification']['missing_dependencies'] == ['timm']
    assert families['detection']['missing_dependencies'] == ['ultralytics']
    assert families['ocr']['missing_dependencies'] == []

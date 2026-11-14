"""Owned stdin bridge for the renderer→original ASGI proof. No listener, lifespan, model or provider."""
import json
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient
from backend.main import create_app

with tempfile.TemporaryDirectory(prefix='modu-trace-owned-') as root:
    app = create_app(project_dir=str(Path(root) / 'projects'))
    client = TestClient(app)
    token = app.state.api_token
    created = client.post('/api/project/create', headers={'X-Vision-Token': token}, json={'name': 'Owned fixture', 'task': 'classification'})
    created.raise_for_status()
    print(json.dumps({'ready': True, 'context': json.loads(created.headers['x-vision-context'])}), flush=True)
    for line in sys.stdin:
        message = json.loads(line)
        # The process capability belongs to this owned fake transport, never the model or persisted proof.
        headers = {**message.get('headers', {}), 'X-Vision-Token': token}
        response = client.request(message.get('method', 'GET'), message['path'], headers=headers)
        print(json.dumps({'status': response.status_code, 'body': response.text, 'headers': dict(response.headers)}), flush=True)

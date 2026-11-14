"""CLI subprocesses exercise real isolated API handlers; no model or native host."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

from PIL import Image
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def studio(tmp_path):
    import uvicorn
    from backend.main import create_app
    app = create_app(project_dir=str(tmp_path / 'projects'))
    token = app.state.api_token
    config = uvicorn.Config(app, host='127.0.0.1', port=0, lifespan='off', log_level='error')
    server = uvicorn.Server(config)
    owner = socket.socket(); owner.bind(('127.0.0.1', 0)); owner.listen(16)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [owner]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline: time.sleep(.02)
    assert server.started
    url = f'http://127.0.0.1:{owner.getsockname()[1]}'

    def call(*args, project=None):
        env = {**os.environ, 'VISION_AI_STUDIO_API_TOKEN': token}
        env.pop('MODU_STUDIO_BEARER_TOKEN', None)
        result = subprocess.run([sys.executable, '-m', 'backend.studio_cli', '--url', url,
            *(['--project', project] if project else []), *args], cwd=ROOT, env=env,
            capture_output=True, text=True, encoding='utf-8', timeout=30)
        assert result.returncode == 0, (result.stdout, result.stderr)
        value = json.loads(result.stdout)
        assert token not in result.stdout + result.stderr
        return value
    try: yield app, call
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
        owner.close()


def test_cli_real_catalog_project_data_label_artifact_roundtrip(studio, tmp_path):
    app, cli = studio
    catalog = cli('capabilities', '--limit', '1000')['result']
    assert catalog['total'] >= 500
    ids = {row['id'] for row in catalog['operations']}
    for prefix in ('/api/project/', '/api/dataset/', '/api/annotations/', '/api/training/',
                   '/api/evaluation/', '/api/flowchart/', '/api/inspections/', '/api/export/', '/api/compute/'):
        assert any(' ' + prefix in identifier for identifier in ids)
    assert getattr(app.state, 'current_project', None) is None
    created = cli('call', 'POST /api/project/create', '--body-json', json.dumps({
        'name': 'CLI 합성 검증', 'task': 'segmentation', 'project_dir': str(tmp_path / '작업 공간')}, ensure_ascii=False))
    project = created['result']['id']
    source = tmp_path / '원본'; source.mkdir()
    image = source / '검사 이미지.png'
    Image.new('RGB', (16, 16), color=(24, 45, 67)).save(image)
    source_hash = hashlib.sha256(image.read_bytes()).hexdigest()
    imported = cli('call', 'POST /api/dataset/import', '--body-json', json.dumps({
        'folder_path': str(source), 'task': 'segmentation'}), project=project)
    assert imported['ok']
    raw_output = tmp_path / '원본 바이트.png'
    raw = cli('call', 'GET /api/dataset/raw/{image_path}', '--path-json', json.dumps({'image_path': image.name}),
        '--query-json', json.dumps({'file_path': str(image)}), '--output', str(raw_output), project=project)
    assert raw['result']['sha256'] == source_hash
    assert raw_output.read_bytes() == image.read_bytes()
    saved = cli('call', 'POST /api/annotations/save', '--body-json', json.dumps({
        'image_id': '검사 이미지', 'image_path': str(image), 'annotations': [],
        'image_width': 16, 'image_height': 16}), project=project)
    assert saved['ok']
    data = b'cli-owned-result\x00\xff'
    upload = tmp_path / '검증.bin'; upload.write_bytes(data)
    begun = cli('call', 'POST /api/artifacts/uploads', '--body-json', json.dumps({
        'kind': 'result', 'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}), project=project)
    upload_id = begun['result']['upload']['id']
    appended = cli('call', 'PUT /api/artifacts/uploads/{upload_id}', '--path-json', json.dumps({'upload_id': upload_id}),
        '--query-json', '{"offset":0}', '--upload-file', str(upload), project=project)
    assert appended['ok']
    completed = cli('call', 'POST /api/artifacts/uploads/{upload_id}/complete', '--path-json', json.dumps({'upload_id': upload_id}), project=project)
    assert completed['result']['artifact_ref']['sha256'] == hashlib.sha256(data).hexdigest()
    assert hashlib.sha256(image.read_bytes()).hexdigest() == source_hash
    # Training observation does not start a job. Model quality and six-stage model execution are separate.
    observed = cli('diagnostics', '--section', 'jobs', project=project)
    assert not observed['result']['partial']


def test_npm_cli_wrapper_preserves_python_selection_and_help():
    result = subprocess.run(['node', 'scripts/studio-cli.cjs', '--help'], cwd=ROOT,
        env={**os.environ, 'VISION_AI_PYTHON': sys.executable}, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'capabilities' in result.stdout

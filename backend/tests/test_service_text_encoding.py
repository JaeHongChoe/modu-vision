"""Korean project, folder, file and label names survive a process whose default text encoding is not UTF-8.

Windows opens text files in the ANSI code page (cp1252 on an English install) unless the code names an encoding,
while the app writes its JSON as UTF-8 with Korean kept as is. The scenario runs in a child process whose locale
encoding is US-ASCII (the nearest non-UTF-8 default available on every POSIX host), so any read or write that relies
on the default fails the same way it fails on Windows. The hosted Windows run executes this with its own code page.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

CHILD = 'MV_TEXT_ENCODING_CHILD'


def _scenario(tmp_path: Path) -> None:
    import locale
    from fastapi.testclient import TestClient
    import backend.main as main
    if os.name != 'nt':
        assert locale.getpreferredencoding(False).upper().replace('-', '') in ('USASCII', 'ASCII', 'ANSI_X3.41968'), \
            'the child must run without a UTF-8 default'
    source = tmp_path / '원본 데이터'
    source.mkdir(parents=True)
    for index in range(4):
        image = source / f'검사_{index}.png'
        Image.new('RGB', (16, 16), 'white').save(image)
        image.with_suffix('.json').write_text(json.dumps({
            'imagePath': image.name, 'imageWidth': 16, 'imageHeight': 16,
            'shapes': [{'label': '스크래치', 'shape_type': 'rectangle', 'points': [[1, 1], [5, 5]]}]}, ensure_ascii=False),
            encoding='utf-8')
    app = main.create_app(project_dir=str(tmp_path / '프로젝트'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        assert client.post('/api/project/create', json={'name': '검사 프로젝트', 'task': 'detection'}).status_code == 200
        assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
        for _ in range(2):  # the second listing reads the metadata ledger the first one wrote
            gallery = client.get('/api/dataset/images', params={'folder_path': str(source), 'task': 'detection', 'limit': 50})
            assert gallery.status_code == 200, gallery.text
            rows = gallery.json()['items']
            assert len(rows) == 4 and all('스크래치' in row['labels'] for row in rows), rows
        statistics = client.get('/api/dataset/metadata/statistics', params={'folder_path': str(source)})
        assert statistics.status_code == 200, statistics.text


def test_korean_names_survive_a_default_encoding_that_is_not_utf8(tmp_path):
    if os.environ.get(CHILD):
        _scenario(tmp_path)
        return
    environment = {**os.environ, CHILD: '1', 'PYTHONUTF8': '0', 'PYTHONCOERCECLOCALE': '0', 'PYTHONIOENCODING': 'utf-8'}
    if os.name != 'nt':
        environment.update(LC_ALL='C', LANG='C')
    environment.pop('LC_CTYPE', None)
    result = subprocess.run(
        [sys.executable, '-X', 'utf8=0', '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', str(tmp_path / 'child'),
         f'{__file__}::test_korean_names_survive_a_default_encoding_that_is_not_utf8'],
        cwd=Path(__file__).resolve().parents[2], env=environment, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=300)
    if result.returncode != 0:
        pytest.fail(f'the scenario failed without a UTF-8 default:\n{result.stdout[-6000:]}\n{result.stderr[-2000:]}')

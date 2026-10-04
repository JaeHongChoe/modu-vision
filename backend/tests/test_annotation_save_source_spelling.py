"""A label save through another spelling of the dataset folder (a symlinked folder, macOS /var for /private/var, a
mapped drive) records the image's hashes like a save through the saved spelling, instead of failing after the labels
were written (found by the app-flow QA on macOS)."""
import os
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.fixture
def aliased(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'Spelling', 'task': 'classification'})
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (40, 30), 'white').save(source / 'a.png')
    alias = tmp_path / 'alias'
    try:
        alias.symlink_to(source, target_is_directory=True)
    except OSError:
        pytest.skip('links are not available here')
    saved = client.put('/api/project/update', json={'source_dataset_dir': str(alias)})
    assert saved.status_code == 200
    return client, source, alias, saved.json()


def test_a_save_through_another_spelling_records_the_same_image(aliased):
    client, source, alias, project = aliased
    assert Path(project['source_dataset_dir']) == Path(os.path.realpath(source)), 'the project keeps the resolved spelling'
    label = [{'type': 'tag', 'label': 'OK', 'is_normal': True, 'category_id': 0}]
    through_alias = client.post('/api/annotations/save', json={'image_id': 'a', 'image_path': str(alias / 'a.png'), 'actor': 'Lee',
                                                                 'annotations': label})
    assert through_alias.status_code == 200, through_alias.text
    first = through_alias.json()['metadata']
    assert first['annotation_hash'] and first['workflow_state'] == 'needs_review'
    direct = client.post('/api/annotations/save', json={'image_id': 'a', 'image_path': str(source / 'a.png'), 'actor': 'Lee',
                                                         'annotations': label})
    assert direct.status_code == 200, direct.text
    second = direct.json()['metadata']
    assert second['image_uuid'] == first['image_uuid'], 'one image, whichever spelling saved it'
    assert second['annotation_hash'] == first['annotation_hash']

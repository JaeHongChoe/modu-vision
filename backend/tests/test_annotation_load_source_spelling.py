"""Annotation reads use the validated source spelling without losing entry identity."""
import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from backend.main import create_app


@pytest.mark.parametrize('format', ['empty', 'coco', 'yolo', 'internal-link', 'escaped-image', 'escaped-label'])
def test_source_alias_read_preserves_labels_and_boundary(tmp_path, format):
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (40, 30), 'white').save(source / 'a.png')
    outside = tmp_path / 'outside.png'
    Image.new('RGB', (40, 30), 'black').save(outside)
    alias = tmp_path / 'alias'
    alias.symlink_to(source, target_is_directory=True)
    name = 'a'
    if format == 'coco':
        (source / 'annotations.json').write_text(json.dumps({
            'images': [{'id': 1, 'file_name': 'a.png', 'width': 40, 'height': 30}],
            'categories': [{'id': 3, 'name': 'scratch'}],
            'annotations': [{'id': 1, 'image_id': 1, 'category_id': 3, 'bbox': [4, 3, 8, 6], 'area': 48, 'iscrowd': 0}]}))
    if format in {'yolo', 'internal-link', 'escaped-label'}:
        (source / 'classes.txt').write_text('scratch\n')
        (source / 'a.txt').write_text('0 0.5 0.5 0.2 0.2\n')
    if format == 'internal-link':
        name = 'entry'
        (source / 'entry.png').symlink_to(source / 'a.png')
        (source / 'classes.txt').write_text('scratch\nentry-defect\n')
        (source / 'entry.txt').write_text('1 0.5 0.5 0.2 0.2\n')
    if format == 'escaped-image':
        (source / 'a.png').unlink()
        (source / 'a.png').symlink_to(outside)
    if format == 'escaped-label':
        external_label = tmp_path / 'outside.txt'
        external_label.write_text('0 0.5 0.5 0.2 0.2\n')
        (source / 'a.txt').unlink()
        (source / 'a.txt').symlink_to(external_label)
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in tmp_path.rglob('*') if p.is_file()}
    app = create_app(str(tmp_path / 'registry'))
    with TestClient(app, headers={'X-Vision-Token': app.state.api_token}) as client:
        created = client.post('/api/project/create', json={'name': 'Spelling', 'task': 'detection'})
        assert created.status_code == 200, created.text
        updated = client.put('/api/project/update', json={'source_dataset_dir': str(alias)})
        assert updated.status_code == 200, updated.text
        def read(root):
            return client.get(f'/api/annotations/{name}', params={'file_path': str(root / f'{name}.png')})
        result = read(alias)
        if format in {'escaped-image', 'escaped-label'}:
            assert result.status_code == 422, result.text
            expected = ('Image must be a file inside the selected dataset' if format == 'escaped-image'
                        else 'Source annotation links outside the source')
            assert expected in result.json()['detail']
        else:
            assert result.status_code == 200, result.text
            direct = read(source)
            assert direct.status_code == 200, direct.text
            assert result.json() == direct.json()
            assert result.json()['metadata']['width'] == 40
            annotations = result.json()['annotations']
            assert [a['label'] for a in annotations] == ([] if format == 'empty' else ['entry-defect' if format == 'internal-link' else 'scratch'])
    assert all(hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest for path, digest in before.items())
    assert not list(source.rglob('*studio*'))

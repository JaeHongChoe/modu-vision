"""Grouped LabelMe detection import agrees with the original-path training loader."""
import hashlib
import json
import pytest
from fastapi import HTTPException
from PIL import Image
from backend.api import routes_dataset


def source_files(root):
    for part in ('train', 'val', 'test'):
        folder = root / part; folder.mkdir(parents=True)
        for name, shapes in [('normal', []), ('defect', [
            {'label': 'scratch', 'shape_type': 'rectangle', 'points': [[2, 2], [4, 6]]},
            {'label': 'stain', 'shape_type': 'rectangle', 'points': [[8, 4], [12, 9]]},
        ])]:
            image = folder / (name + '.png'); Image.new('RGB', (16, 12)).save(image)
            image.with_suffix('.json').write_text(json.dumps({'imagePath': image.name,
                'imageWidth': 16, 'imageHeight': 12, 'shapes': shapes}))


def test_nested_empty_and_multi_object_import_keeps_source_inventory_and_counts(tmp_path, monkeypatch):
    root = tmp_path / 'source'; source_files(root)
    monkeypatch.setattr(routes_dataset, 'SPLIT_MANIFEST_DIR', tmp_path / 'splits')
    before = {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in root.rglob('*') if file.is_file()}
    result = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(root), task='detection', validate_images=True))
    assert result['total_images'] == result['source_images'] == 6
    assert result['classes'] == {'scratch': 3, 'stain': 3}
    assert result['validation']['complete']
    routes_dataset._write_split_manifest(root, {file.relative_to(root).as_posix(): file.relative_to(root).parts[0]
        for file in root.rglob('*.png')}, seed=114)
    reopened = routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
        folder_path=str(root), task='detection', validate_images=False))
    assert reopened['split'] == {'train': 2, 'val': 2, 'test': 2}
    assert before == {str(file): hashlib.sha256(file.read_bytes()).hexdigest() for file in root.rglob('*') if file.is_file()}


def test_nested_malformed_annotation_cannot_be_counted_as_normal(tmp_path, monkeypatch):
    root = tmp_path / 'source'; source_files(root)
    monkeypatch.setattr(routes_dataset, 'SPLIT_MANIFEST_DIR', tmp_path / 'splits')
    (root / 'train' / 'normal.json').write_text('{broken')
    with pytest.raises(HTTPException) as error:
        routes_dataset.import_dataset(routes_dataset.DatasetImportRequest(
            folder_path=str(root), task='detection', validate_images=False))
    assert error.value.status_code == 422

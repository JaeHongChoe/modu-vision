"""Exchange must preserve class meaning in the reopened class-index raster."""
import json
from pathlib import Path
import pytest
import numpy as np
from PIL import Image
from backend.engine.annotation_formats import export_annotations, import_annotations, bundle_files
from backend.tests.test_dataset_metadata_api import client_workspace


def rows():
    return [{'file_name': name, 'width': 100, 'height': 80, 'annotations': [
        {'type': 'bbox', 'label': 'Scratch', 'category_id': 7, 'color': '#f59e0b', 'bbox': [1.25, 2.5, 20.75, 30.5]},
        {'type': 'polygon', 'label': 'Crack', 'category_id': 23, 'color': '#ef4444',
         'polygon': [[40.5, 10.25], [80.75, 10.25], [60.5, 40.75]]}]
    } for name in ['batch-a/part.png', 'batch-b/part.png']]


@pytest.mark.parametrize('format', ['labelme', 'coco', 'yolo'])
def test_roundtrip_keeps_native_class_ids_colors_and_nested_image_identity(format):
    original = rows()
    payload = export_annotations(original, format)
    restored = import_annotations(payload, format)
    assert [r['file_name'] for r in restored] == [r['file_name'] for r in original]
    for before, after in zip(original, restored):
        for left, right in zip(before['annotations'], after['annotations']):
            assert (right['label'], right['category_id'], right['color']) == (left['label'], left['category_id'], left['color'])
            key = 'bbox' if left['type'] == 'bbox' else 'polygon'
            if key == 'bbox': assert right[key] == pytest.approx(left[key], abs=1e-10)
            else:
                for a, b in zip(left[key], right[key]): assert b == pytest.approx(a, abs=1e-10)


@pytest.mark.parametrize('format', ['labelme', 'coco', 'yolo'])
def test_external_documents_allocate_distinct_consistent_class_ids(format):
    payload = export_annotations(rows(), format)
    if format == 'labelme':
        for doc in payload['documents']: doc['flags'] = {}
    else: payload.pop('studio_classes', None)
    restored = import_annotations(payload, format)
    ids = [{a['label']: a['category_id'] for a in r['annotations']} for r in restored]
    assert ids[0] == ids[1]
    assert len(set(ids[0].values())) == 2


def test_yolo_bundle_contains_optional_native_class_mapping():
    payload = export_annotations(rows(), 'yolo')
    files = bundle_files(payload, 'yolo')
    assert json.loads(files['studio_classes.json']) == payload['studio_classes']


@pytest.mark.parametrize('format', ['labelme', 'coco', 'yolo'])
def test_export_allocates_missing_ids_without_losing_known_class_colors(format):
    images = rows()
    for row in images:
        for a in row['annotations']: a.pop('category_id')
    restored = import_annotations(export_annotations(images, format), format)
    assert {a['color'] for a in restored[0]['annotations']} == {'#f59e0b', '#ef4444'}
    assert len({a['category_id'] for a in restored[0]['annotations']}) == 2


@pytest.mark.parametrize('format', ['coco', 'yolo'])
def test_external_vocabulary_binds_classes_even_when_one_image_has_no_other_class(format):
    images = rows()
    images[0]['annotations'] = images[0]['annotations'][:1]
    images[1]['annotations'] = images[1]['annotations'][1:]
    payload = export_annotations(images, format); payload.pop('studio_classes')
    restored = import_annotations(payload, format)
    assert restored[0]['annotations'][0]['category_id'] != restored[1]['annotations'][0]['category_id']
    # YOLO source loading also imports one image at a time with the same vocabulary.
    if format == 'yolo':
        first = import_annotations({**payload, 'images': payload['images'][:1]}, format)
        second = import_annotations({**payload, 'images': payload['images'][1:]}, format)
        assert [first[0]['annotations'][0]['category_id'], second[0]['annotations'][0]['category_id']] == [
            restored[0]['annotations'][0]['category_id'], restored[1]['annotations'][0]['category_id']]


@pytest.mark.parametrize('change', [
    [{'label': 'Scratch', 'category_id': 7}, {'label': 'Crack', 'category_id': 7}],
    [{'label': 'Scratch', 'category_id': 256}, {'label': 'Crack', 'category_id': 23}],
    [{'label': 'Scratch', 'category_id': 7, 'color': 'not-a-color'}, {'label': 'Crack', 'category_id': 23}],
])
def test_invalid_native_class_mapping_fails_before_import(change):
    payload = export_annotations(rows(), 'coco')
    payload['studio_classes'] = change
    with pytest.raises(ValueError, match='[Cc]lass|color'): import_annotations(payload, 'coco')


@pytest.mark.parametrize('format', ['labelme', 'coco', 'yolo'])
def test_directory_bundle_api_apply_keeps_distinct_class_raster(client_workspace, tmp_path, format):
    client, project, source = client_workspace
    original = {name: (source/name).read_bytes() for name in ['a.png', 'b.png']}
    images = rows()
    for row, name in zip(images, original): row['file_name'] = name
    payload = export_annotations(images, format)
    root = tmp_path/'exchange'; root.mkdir()
    for name, text in bundle_files(payload, format).items():
        file = root/name; file.parent.mkdir(parents=True, exist_ok=True); file.write_text(text)
    request = {'format': format, 'import_dir': str(root)}
    preview = client.post('/api/dataset/formats/import', json=request)
    assert preview.status_code == 200, preview.text
    response = client.post('/api/dataset/formats/import', json={**request, 'mode': 'apply', 'actor': 'format-owner',
        'expected_revisions': {r['image_uuid']: r['revision'] for r in preview.json()['preview']}})
    assert response.status_code == 200, response.text
    files = list(Path(project['annotations_dir']).rglob('*.png'))
    assert len(files) == 2
    for file in files:
        with Image.open(file) as image: raster = np.asarray(image)
        assert set(np.unique(raster)) == {0, 7, 23}
        assert raster[15, 10] == 7 and raster[20, 60] == 23
    assert {name: (source/name).read_bytes() for name in original} == original

"""Brightness edits must leave source pixels and label geometry intact."""
import base64
import copy
import hashlib
import io

import numpy as np
import pytest
from PIL import Image

from backend.engine import data_workbench as dw


def test_brightness_changes_pixels_without_changing_geometry_or_mask():
    image = Image.new('RGB', (12, 10), (40, 80, 120))
    mask = Image.new('RGBA', image.size, (0, 0, 0, 0))
    mask.putpixel((3, 4), (255, 0, 0, 255))
    buf = io.BytesIO(); mask.save(buf, format='PNG')
    encoded = 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()
    annotations = [
        {'id': 'box', 'type': 'bbox', 'bbox': [1, 2, 6, 8], 'label': 'part', 'text': 'A01'},
        {'id': 'mask', 'type': 'brush_mask', 'mask_rle': encoded},
        {'id': 'obb', 'type': 'rotated_bbox', 'rotated_bbox': [6, 5, 4, 2, 0], 'direction_deg': 270},
    ]
    original = copy.deepcopy(annotations)
    result, labels, omitted = dw.edit_image_and_annotations(image, annotations, {'kind': 'brightness', 'factor': 2})
    assert result.getpixel((3, 4)) == (80, 160, 240)
    assert image.getpixel((3, 4)) == (40, 80, 120)
    assert labels[0] == original[0]
    assert labels[2]['rotated_bbox'] == [6, 5, 4, 2, 0]
    assert labels[2]['direction_deg'] == 270
    with Image.open(io.BytesIO(base64.b64decode(labels[1]['mask_rle'].split(',')[1]))) as actual:
        assert np.array_equal(np.asarray(actual), np.asarray(mask))
    assert annotations == original and omitted == []


@pytest.mark.parametrize('factor', [True, -1, 4.01, float('nan'), float('inf'), '2', None])
def test_brightness_rejects_invalid_factor_before_a_version_is_written(tmp_path, factor):
    source = tmp_path / 'source'; source.mkdir()
    project = tmp_path / 'project'; project.mkdir()
    image = source / 'part.png'; Image.new('RGB', (12, 10), (40, 80, 120)).save(image)
    before = image.read_bytes()
    with pytest.raises(ValueError, match='Brightness'):
        dw.derive(project, source, image, [], {'kind': 'brightness', 'factor': factor}, 'Editor', hashlib.sha256(before).hexdigest())
    assert image.read_bytes() == before
    assert dw.derived_history(project, source, image) == []


def test_brightness_version_reopens_and_rotates_from_its_parent(tmp_path):
    source = tmp_path / 'source'; source.mkdir()
    project = tmp_path / 'project'; project.mkdir()
    image = source / 'part.png'; Image.new('RGB', (12, 10), (40, 80, 120)).save(image)
    before = image.read_bytes(); digest = hashlib.sha256(before).hexdigest()
    labels = [{'id': 'box', 'type': 'bbox', 'bbox': [1, 2, 6, 8], 'label': 'part'}]
    scope = {'task': 'detection', 'labelset_id': 'default'}
    first = dw.derive(project, source, image, labels, {'kind': 'brightness', 'factor': .5}, 'Editor', digest, scope=scope)
    second = dw.derive(project, source, image, [], {'kind': 'rotate', 'degrees': 90}, 'Editor', digest, first['id'], scope)
    reopened = dw.read_derived(project, source, second['id'], scope)
    assert reopened['parent_id'] == first['id']
    assert reopened['annotations'][0]['bbox'] == [2, 1, 8, 6]
    with Image.open(reopened['file_path']) as edited:
        assert edited.getpixel((0, 0)) == (20, 40, 60)
    assert image.read_bytes() == before

"""Every accepted region must survive a mixed vector/mask annotation save."""
import base64
import hashlib
import io

import numpy as np
from PIL import Image
import pytest

from backend.api import routes_annotation as route


@pytest.mark.parametrize('companion', ['polygon', 'rotated_bbox', 'brush_mask'])
def test_mixed_mask_keeps_disjoint_box_region_and_original(companion, monkeypatch, tmp_path):
    source = tmp_path / 'source' / 'part.png'
    source.parent.mkdir()
    Image.new('RGB', (64, 64), (100, 120, 140)).save(source)
    original_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    monkeypatch.setattr(route, 'ANNOTATIONS_DIR', tmp_path / 'annotations')
    other = {'id': 'other', 'type': companion, 'label': 'Scratch', 'category_id': 2}
    if companion == 'polygon':
        other['polygon'] = [[30, 30], [48, 30], [39, 48]]
    elif companion == 'rotated_bbox':
        other['rotated_bbox'] = [39, 39, 18, 12, 25]
    else:
        rgba = np.zeros((64, 64, 4), dtype=np.uint8)
        rgba[30:48, 30:48] = [255, 100, 0, 255]
        stream = io.BytesIO()
        Image.fromarray(rgba).save(stream, format='PNG')
        other['mask_rle'] = 'data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode()
    request = route.AnnotationSaveRequest(
        image_id='part', image_path=str(source), image_width=64, image_height=64,
        annotations=[route.AnnotationItem(id='box', type='bbox', label='Solder Bridge',
                                         category_id=1, bbox=[3, 3, 12, 12]),
                     route.AnnotationItem(**other)],
    )
    result = route.save_annotations(request)
    assert result['mask_generated']
    reopened = route.get_annotations('part', file_path=str(source))
    mask = np.asarray(Image.open(reopened['mask_file']))
    assert np.all(mask[3:13, 3:13] == 1)
    assert mask[39, 39] == 2
    assert set(np.unique(mask)) == {0, 1, 2}
    assert reopened['annotations'][0]['bbox'] == [3, 3, 12, 12]
    assert reopened['annotations'][1]['type'] == companion
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_sha

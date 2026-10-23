"""The two foundation models themselves, on a synthetic image, when their local folders are given.

Hosted CI has no transformers and no weights, so these skip there. Locally, with the official pinned folders
(facebook/sam2.1-hiera-tiny@de431c4, IDEA-Research/grounding-dino-tiny@a2bb814):
  MV_SAM2_DIR=<sam2 snapshot> MV_GROUNDING_DINO_DIR=<grounding dino snapshot> pytest backend/tests/test_foundation_real_models.py
"""
import importlib.util
import os

import numpy as np
import pytest
from PIL import Image

SAM2_DIR = os.environ.get('MV_SAM2_DIR')
GROUNDING_DIR = os.environ.get('MV_GROUNDING_DINO_DIR')
pytestmark = pytest.mark.skipif(not (SAM2_DIR and GROUNDING_DIR and importlib.util.find_spec('transformers')),
                                reason='needs transformers and the local SAM2 / Grounding DINO folders (MV_SAM2_DIR, MV_GROUNDING_DINO_DIR)')


@pytest.fixture
def square(tmp_path):
    """A dark 19x29 square on a colour gradient, like the synthetic QA parts."""
    y, x = np.mgrid[0:128, 0:128]
    image = np.stack([x * 2, y * 2, np.full_like(x, 150)], axis=-1).astype(np.uint8)
    image[54:83, 67:86] = 20
    path = tmp_path / 'part.png'
    Image.fromarray(image).save(path)
    return path


def near(box, expected, tolerance=4):
    return all(abs(a - b) <= tolerance for a, b in zip(box, expected))


def test_a_phrase_without_its_final_period_still_finds_the_object(square):
    from backend.engine.label_candidate_providers import grounded_candidates
    boxes = grounded_candidates(square, GROUNDING_DIR, 'dark square', threshold=.25, text_threshold=.25)
    assert boxes and near(boxes[0]['annotation']['bbox'], [67, 54, 86, 83])
    assert boxes[0]['grounding'] == {**boxes[0]['grounding'], 'phrase': 'dark square', 'query': 'dark square'}


def test_comma_separated_and_korean_phrases_box_the_object_once_under_one_phrase(square):
    from backend.engine.label_candidate_providers import grounded_candidates
    boxes = grounded_candidates(square, GROUNDING_DIR, 'dark square, 사각형', threshold=.25, text_threshold=.25)
    assert len(boxes) == 1 and boxes[0]['annotation']['label'] in ('dark square', '사각형')


def test_sam2_click_and_phrase_masks_follow_the_object(square):
    from backend.engine.foundation_labeling import foundation_candidates
    setup = {'mask_model_dir': SAM2_DIR, 'model_dir': GROUNDING_DIR}
    clicked = foundation_candidates(square, setup, points=[{'x': 76, 'y': 68, 'label': 1}], label='defect', threshold=.5)
    assert clicked and near(clicked[0]['annotation']['bbox'], [67, 54, 86, 83]) and clicked[0]['source'] == 'sam2'
    phrased = foundation_candidates(square, setup, prompt='dark square', label='defect', threshold=.3, text_threshold=.25)
    assert phrased and near(phrased[0]['annotation']['bbox'], [67, 54, 86, 83])
    assert phrased[0]['provenance']['score_kind'] == 'minimum_mask_iou_and_grounding_score'
    assert phrased[0]['annotation']['label'] == 'defect'


def test_the_official_pinned_files_are_recognised_by_their_digest():
    """MV_SAM2_DIR / MV_GROUNDING_DINO_DIR here are the official pinned folders: readiness names them as such."""
    from backend.engine.foundation_labeling import foundation_readiness
    from backend.engine.label_candidate_providers import semantic_readiness
    sam = foundation_readiness({'mask_model_dir': SAM2_DIR}, 'cpu')
    text = semantic_readiness(GROUNDING_DIR)
    assert sam['ready'] and sam['weights_check'] == 'ok' and sam['official_match'] is True
    assert sam['official_pinned']['revision'].startswith('de431c4') and sam['official_pinned']['license'] == 'apache-2.0'
    assert text['ready'] and text['official_match'] is True and text['official_pinned']['revision'].startswith('a2bb814')

import hashlib,json
from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from backend.engine.defect_gan import adopt_reviewed_candidates
from backend.engine.dataset_loaders import ClassificationDataset


def test_reviewed_gan_adoption_joins_training_and_preserves_real_validation(tmp_path: Path):
    source=tmp_path/'source';review=tmp_path/'review';review.mkdir()
    for split in ('train','val','test'):
        for label in ('OK','scratch'):
            path=source/split/label/'real.png';path.parent.mkdir(parents=True)
            Image.fromarray(np.full((32,32,3),10+len(split)+len(label),dtype=np.uint8)).save(path)
    candidate=review/'candidate.png';Image.fromarray(np.full((64,64,3),80,dtype=np.uint8)).save(candidate)
    (review/'review_manifest.json').write_text(json.dumps({'generator_sha256':'a'*64,'candidates':[{'id':'candidate_0001','path':str(candidate),'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'status':'synthetic_unreviewed'}]}))
    decisions=[{'candidate_id':'candidate_0001','decision':'adopt','label':'scratch','reviewer':'QA','reason':'confirmed visible scratch'}]
    result=adopt_reviewed_candidates(review,source,tmp_path/'adopted',decisions)
    train=ClassificationDataset(result['dataset_path'],split='train')
    assert len(train)==3
    assert len(ClassificationDataset(result['dataset_path'],split='val'))==2
    assert len(ClassificationDataset(result['dataset_path'],split='test'))==2
    assert not any(p.name.startswith('synthetic') for p in source.rglob('*.png'))
    assert json.loads((review/'review_manifest.json').read_text())['candidates'][0]['status']=='synthetic_adopted'
    with pytest.raises(ValueError,match='reviewed|already'):
        adopt_reviewed_candidates(review,source,tmp_path/'again',decisions)

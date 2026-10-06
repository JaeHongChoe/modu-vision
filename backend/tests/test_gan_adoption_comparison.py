import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image


def fixture(tmp_path):
    source = tmp_path / 'original'; adopted = tmp_path / 'adopted'; rows = []
    for split in ('train', 'val', 'test'):
        for index, label in enumerate(('OK', 'scratch')):
            p = source / split / label / 'original.png'; p.parent.mkdir(parents=True)
            Image.new('RGB', (20, 20), (len(rows) * 20, index * 30, 40)).save(p)
            q = adopted / split / label / 'copied.png'; q.parent.mkdir(parents=True); q.write_bytes(p.read_bytes())
            rows.append({'kind': 'real', 'image': q.relative_to(adopted).as_posix(), 'source_image': str(p),
                         'source_sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'split': split, 'label': label})
    q = adopted / 'train/scratch/synthetic.png'; Image.new('RGB', (20, 20), (5, 6, 7)).save(q)
    rows.append({'kind': 'synthetic_reviewed', 'image': q.relative_to(adopted).as_posix(), 'split': 'train',
                 'label': 'scratch', 'source_sha256': hashlib.sha256(q.read_bytes()).hexdigest(),
                 'generator_sha256': 'a' * 64, 'reviewer': 'synthetic unit-test reviewer', 'reason': 'software transition only'})
    audit = adopted / 'synthetic_provenance.json'
    audit.write_text(json.dumps({'source_dataset_path': str(source), 'generator_sha256': 'a' * 64, 'samples': rows}))
    return source, adopted, audit, rows


def test_adoption_identity_covers_all_original_splits_and_only_synthetic_train(tmp_path):
    from backend.engine.gan_adoption_comparison import verified_adoption
    source, adopted, _, _ = fixture(tmp_path)
    result = verified_adoption(adopted)
    assert result['original_source'] == str(source)
    assert result['heldout_count'] == 2
    assert result['synthetic_train_count'] == 1


@pytest.mark.parametrize('fault', ['changed_test', 'extra_test', 'synthetic_test', 'omitted_train', 'changed_original', 'duplicate_test'])
def test_comparison_refuses_changed_or_incomplete_provenance(tmp_path, fault):
    from backend.engine.gan_adoption_comparison import verified_adoption
    source, adopted, audit, rows = fixture(tmp_path)
    if fault == 'changed_test':
        (adopted / 'test/OK/copied.png').write_bytes(b'changed')
    elif fault == 'extra_test':
        Image.new('RGB', (20, 20)).save(adopted / 'test/OK/extra.png')
    elif fault == 'synthetic_test':
        rows[-1]['split'] = 'test'
    elif fault == 'omitted_train':
        rows.pop(0)
    elif fault == 'changed_original':
        (source / 'val/OK/original.png').write_bytes(b'changed')
    else:
        rows[4]['source_image'] = rows[0]['source_image']
        rows[4]['source_sha256'] = rows[0]['source_sha256']
        (adopted / rows[4]['image']).write_bytes((source / 'train/OK/original.png').read_bytes())
    payload = json.loads(audit.read_text()); payload['samples'] = rows; audit.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        verified_adoption(adopted)


def test_saved_comparison_is_immutable_and_integrity_checked(tmp_path):
    from backend.engine.gan_adoption_comparison import save_comparison, read_comparison
    saved = save_comparison(tmp_path, {'quality_approved': False, 'heldout_count': 2})
    assert read_comparison(tmp_path, saved['comparison_id']) == saved
    p = tmp_path / (saved['comparison_id'] + '.json'); payload = json.loads(p.read_text()); payload['heldout_count'] = 3
    p.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='integrity'):
        read_comparison(tmp_path, saved['comparison_id'])


def test_comparison_uses_the_same_saved_split_fingerprint_as_training(tmp_path, monkeypatch):
    from backend.engine.gan_adoption_comparison import compare_adoption
    source, adopted, _, _ = fixture(tmp_path)
    project = {'project_dir': str(tmp_path / 'project'), 'models_dir': str(tmp_path / 'project/models')}
    called = []
    def fingerprint(path):
        called.append(str(path)); return 'v1:saved-split-' + path.name
    monkeypatch.setattr('backend.api.routes_model_comparisons._fingerprint', fingerprint)
    for job, dataset in (('job_before', source), ('job_after', adopted)):
        root = Path(project['models_dir']) / job; root.mkdir(parents=True)
        (root / 'best_model.pt').write_bytes(b'unit test inference boundary only')
        (root / 'job_receipt.json').write_text(json.dumps({'status': 'completed', 'task': 'classification',
            'source_dataset_path': str(dataset), 'dataset_fingerprint': 'v1:saved-split-' + dataset.name}))
        (root / 'model_meta.json').write_text(json.dumps({'task': 'classification', 'backbone': 'efficientnet_b0',
            'image_size': [64,64], 'classes': ['OK','scratch']}))
    evaluated = []
    def evaluator(checkpoint, metadata, dataset, device):
        evaluated.append(str(dataset))
        return {'metrics': {'evaluated_split': 'test', 'accuracy': .5, 'macro_precision': .5,
                            'macro_recall': .5, 'macro_f1': .5}, 'test_predictions': [{}, {}]}
    monkeypatch.setattr('backend.api.routes_evaluation._evaluate_classification', evaluator)
    report = compare_adoption(project, adopted, 'job_before', 'job_after')
    assert called == [str(source), str(adopted)]
    assert evaluated == [str(source), str(source)]
    assert report['same_original_test_cohort'] and not report['quality_approved']

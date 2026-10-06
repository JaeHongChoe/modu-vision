"""Content-bound, same original test cohort GAN adoption evidence; no approval."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from backend.engine.dataset_loaders import ClassificationDataset
from backend.engine.evaluation_history import canonical


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as reader:
        for block in iter(lambda: reader.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def regular(path, root):
    path, root = Path(path), Path(root).resolve()
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError('GAN comparison source escaped its regular dataset')
    return path.resolve()


def verified_adoption(directory):
    adopted = Path(directory)
    if adopted.is_symlink() or not adopted.is_dir():
        raise ValueError('GAN adoption dataset is unavailable')
    adopted = adopted.resolve()
    audit_path = regular(adopted / 'synthetic_provenance.json', adopted)
    audit = json.loads(audit_path.read_text())
    original = Path(audit['source_dataset_path'])
    if original.is_symlink() or not original.is_dir() or original.resolve() == adopted:
        raise ValueError('GAN original source is unavailable')
    original = original.resolve()
    inventories = {}
    for root in (original, adopted):
        inventory = {}
        for split in ('train', 'val', 'test'):
            dataset = ClassificationDataset(root, split=split)
            if dataset.split_basis == 'automatic' or not dataset.samples:
                raise ValueError('GAN comparison requires explicit nonempty train/val/test')
            for p, index in dataset.samples:
                p = regular(p, root)
                if p in inventory:
                    raise ValueError('GAN image belongs to multiple partitions')
                inventory[p] = (split, dataset.classes[index], sha(p))
        inventories[root] = inventory
    originals, copies = inventories[original], inventories[adopted]
    represented_originals = set(); represented_copies = set(); synthetic = 0
    for row in audit['samples']:
        p = regular(adopted / row['image'], adopted)
        identity = (row['split'], row['label'], row['source_sha256'])
        if p in represented_copies or copies.get(p) != identity:
            raise ValueError('GAN adopted pixels or split differ from provenance')
        represented_copies.add(p)
        if row['kind'] == 'real':
            source = regular(row['source_image'], original)
            if source in represented_originals or originals.get(source) != identity:
                raise ValueError('GAN original pixels or split differ from provenance')
            represented_originals.add(source)
        elif row['kind'] == 'synthetic_reviewed':
            if row['split'] != 'train' or row['generator_sha256'] != audit['generator_sha256'] or not row.get('reviewer') or not row.get('reason'):
                raise ValueError('GAN synthetic pixels require explicit reviewed train provenance')
            if row.get('source_image_sha256') and row['source_image_sha256'] not in {value[2] for value in originals.values() if value[0] == 'train'}:
                raise ValueError('GAN composition used an image outside original train')
            synthetic += 1
        else:
            raise ValueError('GAN adoption contains an unknown provenance kind')
    if represented_originals != set(originals) or represented_copies != set(copies) or not synthetic:
        raise ValueError('GAN provenance does not cover the entire adoption snapshot')
    for inventory in (originals, copies):
        hashes = {split: {value[2] for value in inventory.values() if value[0] == split} for split in ('train', 'val', 'test')}
        if any(hashes[a] & hashes[b] for a, b in (('train', 'val'), ('train', 'test'), ('val', 'test'))):
            raise ValueError('GAN partitions contain duplicate image content')
    heldout = sorted((label, digest) for split, label, digest in originals.values() if split == 'test')
    return {'original_source': str(original), 'adopted_source': str(adopted), 'provenance_sha256': sha(audit_path),
            'heldout_count': len(heldout), 'heldout_sha256': hashlib.sha256(canonical(heldout)).hexdigest(),
            'synthetic_train_count': synthetic, 'generator_sha256': audit['generator_sha256']}


def compare_adoption(project, adopted, before_id, after_id):
    from backend.engine.checkpoint_paths import completed_job_receipt, trusted_checkpoint
    from backend.api.routes_model_comparisons import _fingerprint
    from backend.api.routes_evaluation import _evaluate_classification
    import torch
    if before_id == after_id:
        raise ValueError('Choose distinct before and after inspection models')
    identity = verified_adoption(adopted)
    models = Path(project['models_dir'])
    if models.is_symlink() or models.resolve() != Path(project['project_dir']).resolve() / 'models':
        raise ValueError('GAN comparison model storage is invalid')
    results = {}; bindings = {}; architecture = None
    for side, job, source in (('before', before_id, identity['original_source']), ('after', after_id, identity['adopted_source'])):
        checkpoint = trusted_checkpoint(job, project_models_dir=models)
        if checkpoint is None:
            raise ValueError('GAN comparison requires completed project inspection models')
        receipt = completed_job_receipt(checkpoint.parent)
        if not receipt or receipt.get('task') != 'classification' or Path(receipt.get('source_dataset_path', '')).resolve() != Path(source):
            raise ValueError('GAN model does not belong to the before/after dataset')
        fingerprint = _fingerprint(Path(source))
        if receipt.get('dataset_fingerprint') != fingerprint:
            raise ValueError('GAN training snapshot changed after model completion')
        metadata_path = regular(checkpoint.parent / 'model_meta.json', models)
        metadata = json.loads(metadata_path.read_text())
        model_architecture = (metadata.get('backbone'), metadata.get('image_size'), metadata.get('classes'), metadata.get('class_semantics'))
        if architecture is not None and architecture != model_architecture:
            raise ValueError('GAN A/B requires the same classifier structure, size and classes')
        architecture = model_architecture
        bindings[side] = {'job_id': job, 'checkpoint_sha256': sha(checkpoint), 'metadata_sha256': sha(metadata_path),
                          'training_dataset_fingerprint': fingerprint, 'source_dataset_path': source,
                          'training_config': metadata.get('training_config'), 'training_provenance': receipt.get('training_provenance')}
        results[side] = _evaluate_classification(checkpoint, metadata, Path(identity['original_source']), torch.device('cpu'))
        if results[side]['metrics']['evaluated_split'] != 'test' or len(results[side]['test_predictions']) != identity['heldout_count']:
            raise ValueError('GAN A/B did not evaluate the entire original test cohort')
        if sha(checkpoint) != bindings[side]['checkpoint_sha256'] or sha(metadata_path) != bindings[side]['metadata_sha256']:
            raise ValueError('GAN model changed while comparison was running')
    if verified_adoption(adopted) != identity:
        raise ValueError('GAN adoption changed while comparison was running')
    keys = ('accuracy', 'macro_precision', 'macro_recall', 'macro_f1')
    return {**identity, 'models': bindings, 'results': results,
            'metric_delta': {key: results['after']['metrics'][key] - results['before']['metrics'][key] for key in keys},
            'same_original_test_cohort': True, 'full_test': True, 'device': 'cpu', 'quality_approved': False,
            'quality_status': 'decision_evidence_only'}


def save_comparison(directory, report):
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError('GAN comparison storage is invalid')
    directory.mkdir(parents=True, exist_ok=True)
    evidence = {**report, 'comparison_id': 'gan_comparison_' + uuid.uuid4().hex, 'created_at': time.time()}
    saved = {**evidence, 'evidence_sha256': hashlib.sha256(canonical(evidence)).hexdigest()}
    with (directory / (saved['comparison_id'] + '.json')).open('xb') as writer:
        writer.write(canonical(saved)); writer.flush(); os.fsync(writer.fileno())
    return saved


def read_comparison(directory, comparison_id):
    if not re.fullmatch(r'gan_comparison_[0-9a-f]{32}', comparison_id):
        raise ValueError('Invalid GAN comparison identity')
    path = regular(Path(directory) / (comparison_id + '.json'), directory)
    report = json.loads(path.read_text())
    evidence = {key: value for key, value in report.items() if key != 'evidence_sha256'}
    if report.get('comparison_id') != comparison_id or hashlib.sha256(canonical(evidence)).hexdigest() != report.get('evidence_sha256'):
        raise ValueError('GAN comparison integrity failure')
    return report

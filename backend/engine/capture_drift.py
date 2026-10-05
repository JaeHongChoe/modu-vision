"""Local, reference-bound image drift. Unlabelled predictions never prove quality."""
from __future__ import annotations
import copy
import json
from pathlib import Path
import re
import uuid
from PIL import Image, ImageStat
from backend.engine import capture_intake as ci, dataset_metadata as dm
from backend.engine.flow_workspace import atomic_json
from backend.engine.image_truth import digest

MAX_SAMPLES = 2000


def _feedback(row):
    review = row.get('review')
    return {'candidate_id': row['candidate_id'],
            'decision': review['decision'] if review else 'pending',
            'review_revision': review.get('candidate_revision') if review else None,
            'actor': review.get('actor') if review else None,
            'at': review.get('at') if review else None}


def _feedback_report(reference, baseline_rows, observed_rows):
    frozen = reference.get('feedback_at_freeze')
    if frozen is not None and {r['candidate_id'] for r in frozen} != {r['candidate_id'] for r in baseline_rows}:
        raise ValueError('Reference feedback sample binding changed')
    current = [_feedback(r) for r in observed_rows]
    rates = {}
    for decision in ('adopt', 'reject', 'pending'):
        old = sum(r['decision'] == decision for r in frozen) / len(frozen) if frozen else None
        new = sum(r['decision'] == decision for r in current) / len(current) if current else None
        rates[decision] = {'reference': old, 'observed': new,
                           'delta': new - old if old is not None and new is not None else None}
    prior = {r['candidate_id']: r for r in frozen or []}
    updates = []
    for row in baseline_rows:
        now = _feedback(row); old = prior.get(row['candidate_id'])
        if old is not None and now != old:
            updates.append({'candidate_id': row['candidate_id'], 'reference_decision': old['decision'],
                            'current_decision': now['decision'], 'current_review_revision': now['review_revision']})
    return {'reference_available': frozen is not None, 'rates': rates,
            'reference_updates': updates, 'observed_review_count': sum(r['decision'] != 'pending' for r in current),
            'scope': 'capture adoption review only; not labels, truth or model-quality approval'}


def _model_bindings(samples):
    bindings = {}
    for row in samples:
        identity = row.get('runtime_identity')
        if not isinstance(identity, dict):
            continue
        fingerprint = digest(identity)
        bindings[fingerprint] = {'runtime_sha256': fingerprint,
                                 'manifest_sha256': identity.get('manifest_sha256'),
                                 'model_sha256': copy.deepcopy(identity.get('model_sha256') or {})}
    return [bindings[key] for key in sorted(bindings)]


def _directory(project):
    directory = ci._owned(project, ci._root(project) / 'drift')
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sample(project, row):
    path = ci.candidate_image(project, row['candidate_id'])
    with Image.open(path) as opened:
        gray = opened.convert('L').resize((32, 32))
        histogram = gray.histogram()
        bins = [sum(histogram[i:i + 16]) / 1024 for i in range(0, 256, 16)]
        mean = ImageStat.Stat(gray).mean[0]
    binding = row['origin'].get('runtime_binding') or {}
    return {'candidate_id': row['candidate_id'], 'source_sha256': row['source_sha256'],
            'job_receipt_sha256': row['job_receipt_sha256'], 'mean_luminance': mean, 'histogram': bins,
            'prediction': row['source_prediction'], 'truth': row['truth_verdict'],
            'camera': row['origin']['capture_source'], 'product_id': binding.get('product_id') or 'unknown',
            'lot_id': binding.get('lot_id') or 'unknown', 'recipe_id': binding.get('recipe_id') or 'unknown',
            'runtime_identity': copy.deepcopy(row['origin'].get('runtime_identity')),
            'binding_provenance': row['origin'].get('binding_provenance', 'legacy_unknown')}


def create_reference(project, candidate_ids, *, actor, name):
    if not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= MAX_SAMPLES:
        raise ValueError('Choose 1–2000 baseline capture samples')
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 100 or not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise ValueError('Enter a baseline name and reviewer')
    with dm._file_lock(ci._root(project) / 'intake.lock'):
        index = ci._index(project)
        rows = [ci._candidate(project, index, identifier) for identifier in dict.fromkeys(candidate_ids)]
        if any(not row['snapshot_path'] or row['routing'] == 'failed' for row in rows):
            raise ValueError('Baseline requires readable, unchanged capture candidates')
        record = {'schema_version': 1, 'reference_id': 'driftref_' + uuid.uuid4().hex,
                  'scope': ci._scope(project), 'name': name.strip(), 'actor': actor.strip(), 'created_at': dm._now(),
                  'samples': [_sample(project, row) for row in rows], 'quality_status': 'unverified_without_truth',
                  'feedback_at_freeze': [_feedback(row) for row in rows],
                  'feature_definition': 'PIL luminance 32x32, 16 equal bins; no learned embeddings'}
        record['record_sha256'] = digest(record)
        atomic_json(_directory(project) / (record['reference_id'] + '.json'), record)
        return record


def read_reference(project, identifier):
    if not re.fullmatch(r'driftref_[0-9a-f]{32}', identifier):
        raise ValueError('Drift reference is unavailable')
    path = ci._owned(project, _directory(project) / (identifier + '.json'))
    if not path.is_file():
        raise ValueError('Drift reference is unavailable')
    record = json.loads(path.read_text(encoding='utf-8'))
    unsigned = {key: value for key, value in record.items() if key != 'record_sha256'}
    if record.get('record_sha256') != digest(unsigned) or record.get('reference_id') != identifier:
        raise ValueError('Drift reference hash changed')
    if record.get('scope') != ci._scope(project):
        raise ValueError('Drift reference source/task/labelset changed')
    return record


def references(project):
    result = []
    for path in sorted(_directory(project).glob('driftref_*.json')):
        record = read_reference(project, path.stem)
        result.append({key: record[key] for key in ('reference_id','name','actor','created_at','record_sha256')}
                      | {'sample_count': len(record['samples'])})
    return {'references': sorted(result, key=lambda row: row['created_at'], reverse=True)}


def _aggregate(samples):
    if not samples:
        return None
    count = len(samples)
    return {'mean': sum(row['mean_luminance'] for row in samples) / count,
            'histogram': [sum(row['histogram'][index] for row in samples) / count for index in range(16)]}


def _compare(reference, observed):
    baseline, current = _aggregate(reference), _aggregate(observed)
    return {'reference_count': len(reference), 'observed_count': len(observed),
            'mean_luminance_delta': current['mean'] - baseline['mean'] if baseline and current else None,
            'histogram_l1_distance': sum(abs(a-b) for a,b in zip(baseline['histogram'],current['histogram'])) if baseline and current else None}


def report(project, identifier):
    with dm._file_lock(ci._root(project) / 'intake.lock'):
        reference = read_reference(project, identifier)
        index = ci._index(project)
        baseline_ids = {sample['candidate_id'] for sample in reference['samples']}
        # Reopen actual images instead of trusting the frozen statistics alone.
        baseline_rows = []
        for sample in reference['samples']:
            row = ci._candidate(project, index, sample['candidate_id']); baseline_rows.append(row)
            actual = _sample(project, row)
            if digest(actual) != digest(sample):
                raise ValueError('Reference image or run evidence changed')
        incoming, observed_rows, excluded = [], [], []
        candidates = ci.list_candidates(project)['candidates']
        for row in candidates:
            if row['candidate_id'] in baseline_ids:
                continue
            if row.get('stale') or row['routing'] == 'failed' or not row['snapshot_path']:
                excluded.append({'candidate_id':row['candidate_id'],'reason':row.get('stale_reason') or row['failure'] or 'unavailable'})
            elif len(incoming) < MAX_SAMPLES:
                incoming.append(_sample(project, row))
                observed_rows.append(row)
            else:
                excluded.append({'candidate_id':row['candidate_id'],'reason':'sample_limit'})
        baseline = reference['samples']
        prediction_rates = {}
        for label in ('OK','NG','REVIEW'):
            before = sum(row['prediction'] == label for row in baseline) / len(baseline)
            after = sum(row['prediction'] == label for row in incoming) / len(incoming) if incoming else None
            prediction_rates[label] = {'reference':before,'observed':after,'delta':after-before if after is not None else None}
        keys = sorted({(r['product_id'],r['lot_id'],r['camera']) for r in baseline + incoming})
        strata = []
        for product,lot,camera in keys:
            match = lambda row: (row['product_id'],row['lot_id'],row['camera']) == (product,lot,camera)
            old,new = list(filter(match,baseline)),list(filter(match,incoming))
            strata.append({'product_id':product,'lot_id':lot,'camera':camera, **_compare(old,new),
                           'model_changed':bool(old and new and {digest(r['runtime_identity']) for r in old} != {digest(r['runtime_identity']) for r in new})})
        result = {'reference_id':identifier,'reference_sha256':reference['record_sha256'],'scope':reference['scope'],
                  'generated_at':dm._now(),'state':'observed' if incoming else 'insufficient_samples',
                  'reference_count':len(baseline),'observed_count':len(incoming),'excluded_count':len(excluded),'excluded':excluded,
                  'observed_samples':incoming, 'image_statistics':_compare(baseline,incoming), 'prediction_rates':prediction_rates,
                  'human_feedback': _feedback_report(reference, baseline_rows, observed_rows),
                  'model_bindings': {'reference': _model_bindings(baseline), 'observed': _model_bindings(incoming)},
                  'strata':strata,'quality_status':'unverified_without_truth','automatic_action':'none',
                  'next_action':'Review selected captures and validated labels before considering retraining',
                  'limitations':['Image statistics and prediction-rate changes do not establish model quality',
                                 'Unknown product/lot and changed model bindings are separate strata evidence',
                                 'At most 2000 non-reference registered captures per report']}
        result['report_sha256'] = digest(result)
        return result

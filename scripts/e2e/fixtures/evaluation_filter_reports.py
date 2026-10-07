"""Controlled saved-object reports for UI arithmetic, never model-quality proof."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import EvaluationHistory


def seed(root, project, source):
    root, project, source = (Path(value).resolve() for value in (root, project, source))
    if not project.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError('Saved report fixture requires an isolated workspace')
    rows = []
    # Explicit known inputs: [truth, prediction, score, object truth, object prediction].
    cases = [('NG', 'NG', .9, 'Scratch', 'Scratch'), ('NG', 'OK', .3, 'Scratch', None),
             ('OK', 'NG', .7, None, 'Scratch'), ('OK', 'OK', .1, None, None),
             ('NG', 'NG', .8, 'Crack', 'Crack'), ('review', 'NG', .6, None, 'Scratch')]
    for index, (truth, prediction, score, actual, guess) in enumerate(cases):
        file = source / f'case-{index}.png'
        if not file.is_file() or file.is_symlink():
            raise ValueError('Fixture image is missing or linked')
        matched = actual is not None and actual == guess
        box = [1, 1, 3 + index, 3 + index]
        classes = {label: {'tp': int(matched and actual == label),
                           'fn': int(actual == label and not matched),
                           'fp': int(guess == label and not matched)} for label in ('Scratch', 'Crack')}
        rows.append({'file_path': str(file), 'file_name': file.name,
                     'image_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                     'ground_truth': truth, 'predicted_class': prediction, 'defect_score': score,
                     'is_correct': None if truth == 'review' else truth == prediction,
                     'product': 'part-a' if index < 3 else 'part-b', 'lot': 'lot-' + str(index % 2),
                     'object_evidence': {'truth': [{'label': actual, 'box': box}] if actual else [],
                                         'predicted': [{'label': guess, 'box': box, 'confidence': score}] if guess else [],
                                         'per_class': classes,
                                         'matches': [{'prediction_index': 0, 'truth_index': 0, 'label': actual, 'iou': 1.0}] if matched else [],
                                         'missing_truth_indices': [0] if actual and not matched else [],
                                         'extra_prediction_indices': [0] if guess and not matched else [],
                                         'source_size': [16, 16], 'coordinate_space': 'original_image_px'}})
    history = EvaluationHistory(project / 'reports' / 'evaluations')
    result = {'job_id': 'controlled-filter-display', 'task': 'detection',
              'fixture_kind': 'controlled_display_only_no_model_inference',
              'test_predictions': rows, 'metrics': {'fixture_only': True}}
    binding = {'source_dataset_path': str(source), 'task': 'detection', 'labelset_id': 'default',
               'threshold_settings': {'probability_threshold': .5}, 'fixture_kind': result['fixture_kind']}
    items = []
    for variant in ('full', 'no-binary-truth', 'empty'):
        value = json.loads(json.dumps(result))
        value['job_id'] += '-' + variant
        if variant == 'no-binary-truth':
            for row in value['test_predictions']:
                row['ground_truth'] = 'review'
        elif variant == 'empty':
            value['test_predictions'] = []
        record = history.append(value, binding)
        file = history.directory / (record['evaluation_id'] + '.json')
        items.append({'variant': variant, 'record': record, 'report_path': str(file),
                      'report_sha256': hashlib.sha256(file.read_bytes()).hexdigest()})
    return {'kind': 'controlled_saved_filter_reports_not_model_inference', 'items': items,
            'inputs': [{'path': row['file_path'], 'sha256': row['image_sha256']} for row in rows]}


if __name__ == '__main__':
    print(json.dumps(seed(*sys.argv[1:]), ensure_ascii=False))

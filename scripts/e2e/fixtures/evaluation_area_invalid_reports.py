"""Create new immutable display records; never rewrite existing reports."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import EvaluationHistory
from evaluation_three_invalid_reports import seed


def seed_area(root, project, source):
    baseline = seed(root, project, source)
    full = next(item for item in baseline['items'] if item['variant'] == 'full')
    controls = [
        {'variant': 'reversed-box', 'action': 'F055.area-bin-filter', 'sample_index': 0,
         'field': ['object_evidence', 'predicted', '0', 'box'], 'value': [3, 1, 1, 3],
         'domain': 'ordered_finite_box', 'reason': 'reversed-extent'},
        {'variant': 'negative-pixel-area', 'action': 'F055.area-bin-filter', 'sample_index': 1,
         'field': ['pixel_evidence', 'per_class', 'Scratch', 'predicted_area_px'], 'value': -1,
         'domain': 'nonnegative_finite_area', 'reason': 'negative-area'},
        {'variant': 'numeric-string-area', 'action': 'F055.area-bin-filter', 'sample_index': 2,
         'field': ['pixel_evidence', 'per_class', 'Scratch', 'predicted_area_px'], 'value': '4',
         'domain': 'nonnegative_finite_area', 'reason': 'numeric-string-not-number'},
    ]
    history = EvaluationHistory(Path(project).resolve() / 'reports' / 'evaluations')
    invalid = []
    for control in controls:
        result = json.loads(json.dumps(full['record']['result']))
        result['job_id'] = 'controlled-area-display-' + control['variant']
        row = result['test_predictions'][control['sample_index']]
        if control['variant'] == 'reversed-box':
            row['object_evidence']['predicted'][0]['box'] = control['value']
        else:
            row['pixel_evidence'] = {'per_class': {'Scratch': {
                'tp': 1, 'fn': 0, 'fp': 0, 'class_id': 1,
                'predicted_area_px': control['value']}}}
        record = history.append(result, json.loads(json.dumps(full['record']['binding'])))
        file = history.directory / (record['evaluation_id'] + '.json')
        invalid.append({'variant': control['variant'], 'record': record,
                        'report_path': str(file), 'report_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                        'invalid_input': control})
    return {'kind': 'controlled_saved_area_invalid_reports_not_model_inference',
            'items': baseline['items'] + invalid, 'inputs': baseline['inputs'],
            'old_reports_rewritten': False, 'source_images_rewritten': False, 'actual_model_inference': False}


if __name__ == '__main__':
    print(json.dumps(seed_area(*sys.argv[1:]), ensure_ascii=False))

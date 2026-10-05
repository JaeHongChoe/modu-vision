"""Canonical controlled reports for exact-selection display QA, never model inference."""
import json
from pathlib import Path
import sys

from pixel_evaluation_reports import seed, sha

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import EvaluationHistory


def selection_reports(root, project, source, second_set):
    fixture = seed(root, project, source, second_set, True)
    history = EvaluationHistory(Path(project) / 'reports' / 'evaluations')
    initial = next(item for item in fixture['items'] if item['labelset_id'] == 'default')
    for task in ('segmentation', 'classification'):
        result = json.loads(json.dumps(initial['record']['result']))
        result.update(job_id=f'controlled-selection-{task}', task=task)
        if task == 'classification':
            result['test_predictions'] = []
        binding = {**initial['record']['binding'], 'task': task}
        record = history.append(result, binding)
        report = history.directory / (record['evaluation_id'] + '.json')
        fixture['items'].append({'variant': f'selection_{task}', 'labelset_id': 'default',
                                 'record': record, 'report_path': str(report), 'report_sha256': sha(report)})
    return fixture


if __name__ == '__main__':
    print(json.dumps(selection_reports(*sys.argv[1:]), ensure_ascii=False))

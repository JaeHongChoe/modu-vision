"""Controlled saved-report inputs for display QA; these are not trained-model results."""
import base64
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backend.engine.evaluation_history import EvaluationHistory


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def image(mask, path):
    output = io.BytesIO()
    Image.fromarray(mask).save(output, format='PNG')
    path.write_bytes(output.getvalue())
    return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()


def seed(root, project, source, second_set):
    root, project, source = (Path(value).resolve() for value in (root, project, source))
    if not project.is_relative_to(root) or not source.is_relative_to(root):
        raise ValueError('Report fixtures require an isolated test workspace')
    inputs = root / 'controlled-evaluation-inputs'
    inputs.mkdir()
    history = EvaluationHistory(project / 'reports' / 'evaluations')
    items = []
    for index, labelset in enumerate(('default', second_set)):
        truth = np.zeros((64, 64), dtype=np.uint8)
        predicted = truth.copy()
        truth[8:24, 8:24] = 7
        truth[16:40, 32:48] = 23
        predicted[8:24, 12:28] = 7
        predicted[20:44, 32:48] = 23
        if index:
            truth = np.flipud(truth).copy()
            predicted = np.fliplr(predicted).copy()
        truth_path, prediction_path = inputs / f'{index}-truth.png', inputs / f'{index}-prediction.png'
        maps = {'truth_mask': image(truth, truth_path), 'prediction_mask': image(predicted, prediction_path)}
        counts = {}
        for name, class_id in (('Scratch', 7), ('Crack', 23)):
            actual, guess = truth == class_id, predicted == class_id
            counts[name] = {'class_id': class_id, 'tp': int((actual & guess).sum()), 'fp': int((~actual & guess).sum()),
                            'fn': int((actual & ~guess).sum()), 'truth_area_px': int(actual.sum()), 'predicted_area_px': int(guess.sum())}
        sample = {'file_path': str(source / 'part.png'), 'file_name': 'part.png', 'pixel_evidence':
                  {**maps, 'per_class': counts, 'shape': [64, 64], 'coordinate_space': 'model_input_px'}}
        for variant in (('valid',) if index == 0 else ('valid', 'missing_truth', 'unequal_size', 'wrong_shape')):
            row = json.loads(json.dumps(sample))
            if variant == 'missing_truth':
                row['pixel_evidence'].pop('truth_mask')
            elif variant == 'unequal_size':
                row['pixel_evidence']['truth_mask'] = image(truth[:32, :32], inputs / 'unequal-truth.png')
            elif variant == 'wrong_shape':
                row['pixel_evidence']['shape'] = [32, 32]
            record = history.append({'job_id': f'controlled-display-{index}-{variant}', 'task': 'segmentation',
                                     'fixture_kind': 'controlled_display_only_no_model_inference', 'test_predictions': [row]},
                                    {'source_dataset_path': str(source), 'task': 'segmentation', 'labelset_id': labelset,
                                     'fixture_kind': 'controlled_display_only_no_model_inference'})
            report_path = history.directory / (record['evaluation_id'] + '.json')
            items.append({'variant': variant, 'labelset_id': labelset, 'record': record, 'report_path': str(report_path),
                          'report_sha256': sha(report_path), 'truth_path': str(truth_path), 'prediction_path': str(prediction_path)})
    return {'kind': 'controlled_saved_report_display_fixture', 'items': items,
            'inputs': [{'path': str(path), 'sha256': sha(path)} for path in sorted(inputs.glob('*.png'))]}


if __name__ == '__main__':
    print(json.dumps(seed(*sys.argv[1:]), ensure_ascii=False))

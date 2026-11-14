"""Regenerate synthetic bbox-only AP receipts with independent pycocotools2.0.11.

Development-only: pip install pycocotools==2.0.11 in an isolated environment.
No image, model or runtime package dependency is needed by the retained tests.
"""
from contextlib import redirect_stdout
from importlib.metadata import version
import io
import json
from pathlib import Path


def generate():
    import numpy as np
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval
    assert version('pycocotools') == '2.0.11', 'Oracle version is an explicit reproducibility contract'
    empty = {'boxes': [], 'labels': [], 'scores': []}
    cases = [
        *[{'name': 'exact_iou_threshold_' + str(width),
           'predictions': [{'boxes': [[0, 0, width, 10]], 'labels': [1], 'scores': [.9]}],
           'targets': [{'boxes': [[0, 0, 10, 10]], 'labels': [1]}]} for width in (5, 7, 9)],
        {'name': 'absent_classes_and_no_predictions', 'predictions': [empty],
         'targets': [{'boxes': [[0, 0, 10, 10]], 'labels': [1]}]},
        {'name': 'occupied_best_uses_second_ground_truth',
         'predictions': [{'boxes': [[0, 0, 10, 10], [0, 0, 10, 10]], 'labels': [1, 1], 'scores': [.9, .8]}],
         'targets': [{'boxes': [[0, 0, 10, 10], [2, 0, 12, 10]], 'labels': [1, 1]}]},
        {'name': 'equal_iou_uses_last_eligible_gt',
         'predictions': [{'boxes': [[0, 0, 20, 10], [0, 0, 10, 10]], 'labels': [1, 1], 'scores': [.9, .8]}],
         'targets': [{'boxes': [[0, 0, 10, 10], [10, 0, 20, 10]], 'labels': [1, 1]}]},
        {'name': 'stable_equal_scores_across_images',
         'predictions': [{'boxes': [[20, 20, 25, 25], [0, 0, 10, 10]], 'labels': [1, 1], 'scores': [.5, .5]},
                         {'boxes': [[0, 0, 10, 10]], 'labels': [1], 'scores': [.5]}],
         'targets': [{'boxes': [[0, 0, 10, 10]], 'labels': [1]}, {'boxes': [[0, 0, 10, 10]], 'labels': [1]}]},
        {'name': 'all_zero_ground_truth', 'predictions': [empty], 'targets': [{'boxes': [], 'labels': []}]},
        {'name': 'predicted_absent_class_excluded_from_ap',
         'predictions': [{'boxes': [[0, 0, 10, 10]], 'labels': [2], 'scores': [.9]}],
         'targets': [{'boxes': [[0, 0, 10, 10]], 'labels': [1]}]},
        {'name': 'per_class_image_maximum_100',
         'predictions': [{'boxes': [[0, 0, 10, 10]] * 100 + [[20, 0, 30, 10]],
                          'labels': [1] * 101, 'scores': [1 - i / 200 for i in range(101)]}],
         'targets': [{'boxes': [[0, 0, 10, 10], [20, 0, 30, 10]], 'labels': [1, 1]}]},
    ]
    categories = [{'id': 1, 'name': 'scratch'}, {'id': 2, 'name': 'dent'}, {'id': 3, 'name': 'unused'}]
    for case in cases:
        images = [{'id': i + 1, 'width': 64, 'height': 64} for i in range(len(case['targets']))]
        annotations, predictions = [], []
        for index, (target, prediction) in enumerate(zip(case['targets'], case['predictions']), 1):
            for box, label in zip(target['boxes'], target['labels']):
                x1, y1, x2, y2 = box
                annotations.append({'id': len(annotations) + 1, 'image_id': index, 'category_id': label,
                    'bbox': [x1, y1, x2 - x1, y2 - y1], 'area': (x2 - x1) * (y2 - y1), 'iscrowd': 0})
            for box, label, score in zip(prediction['boxes'], prediction['labels'], prediction['scores']):
                x1, y1, x2, y2 = box
                predictions.append({'image_id': index, 'category_id': label,
                    'bbox': [x1, y1, x2 - x1, y2 - y1], 'score': float(score)})
        with redirect_stdout(io.StringIO()):
            truth = COCO(); truth.dataset = {'images': images, 'categories': categories, 'annotations': annotations, 'info': {}}
            truth.createIndex()
            if predictions: detected = truth.loadRes(predictions)
            else:
                detected = COCO(); detected.dataset = {'images': images, 'categories': categories, 'annotations': []}; detected.createIndex()
            evaluator = COCOeval(truth, detected, 'bbox')
            evaluator.params.maxDets = [1, 10, 100]
            evaluator.evaluate(); evaluator.accumulate(); evaluator.summarize()
        available = bool(annotations)
        case['expected'] = {'mAP_50': round(float(evaluator.stats[1]), 4) if available else None,
                            'mAP_50_95': round(float(evaluator.stats[0]), 4) if available else None,
                            'available': available}
        if available:
            precision = evaluator.eval['precision']
            case['expected']['class_ap50'] = {category['name']: round(float(np.mean(precision[0, :, k, 0, -1][precision[0, :, k, 0, -1] > -1])), 4)
                for k, category in enumerate(categories) if any(row['category_id'] == category['id'] for row in annotations)}
    return {'oracle': {'package': 'pycocotools', 'version': version('pycocotools'),
             'source': 'https://github.com/cocodataset/cocoapi/blob/master/PythonAPI/pycocotools/cocoeval.py',
             'scope': 'bbox, all areas, noncrowd/nonignored, foreground categories, stable source order', 'max_detections': 100}, 'cases': cases}


if __name__ == '__main__':
    destination = Path(__file__).resolve().parents[1] / 'backend/tests/fixtures/detection_ap_v2_coco.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(generate(), ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(destination)

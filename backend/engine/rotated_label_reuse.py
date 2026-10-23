"""Step-2 rotated boxes as rows for the step-3 rotated detection workbench (audit F-16 / B3).

Rotated boxes drawn in step 2 were never used by rotated detection: the workbench only took typed or pasted rows.
This reads the project's saved rotated boxes and returns them in the workbench's own sample shape. Nothing is saved
here; the workbench's existing "prepare" path still validates and copies the images. Angles follow the same
convention (clockwise degrees, a box and its 180-degree turn are the same box), folded into [-90, 90).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List


def _fold(angle: float) -> float:
    folded = (float(angle) + 90.0) % 180.0 - 90.0
    return 0.0 if folded == -0.0 else folded


def _proposed_split(relative: str) -> str:
    """A stable 70/15/15 proposal from the file name, used only when neither a saved split nor folders say one."""
    bucket = int(hashlib.sha256(relative.encode('utf-8')).hexdigest()[:8], 16) % 100
    return 'train' if bucket < 70 else 'val' if bucket < 85 else 'test'


def step2_rotated_samples(source: Path, task: str, assignments: Dict[str, str]) -> dict:
    from backend.engine.dataset_inventory import folder_label_split
    from backend.engine.grouped_dataset_views import _annotations, source_image_paths
    from pathlib import PurePath
    source = Path(source).resolve()
    samples: List[dict] = []
    skipped: List[dict] = []
    sources = set()
    unlabeled = 0
    for image in source_image_paths(source, task):
        relative = image.relative_to(source).as_posix()
        try:
            annotations, _ = _annotations(source, image)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            skipped.append({'image': relative, 'reason': f'라벨 파일을 읽을 수 없습니다 ({type(exc).__name__})'})
            continue
        objects = []
        for item in annotations or []:
            if not isinstance(item, dict) or item.get('type') != 'rotated_bbox':
                continue
            box = item.get('rotated_bbox')
            label = item.get('label')
            if not isinstance(label, str) or not label.strip() or not isinstance(box, list) or len(box) != 5:
                skipped.append({'image': relative, 'reason': '이름이나 좌표가 없는 회전 박스'})
                continue
            try:
                cx, cy, width, height, angle = (float(value) for value in box)
            except (TypeError, ValueError):
                skipped.append({'image': relative, 'reason': '숫자가 아닌 회전 박스 좌표'})
                continue
            if width <= 0 or height <= 0:
                skipped.append({'image': relative, 'reason': '너비나 높이가 0인 회전 박스'})
                continue
            row = {'label': label.strip(), 'box': {'cx': cx, 'cy': cy, 'width': width, 'height': height, 'angle_deg': _fold(angle)}}
            direction = item.get('direction_deg')
            if isinstance(direction, (int, float)) and 0 <= direction < 360:
                row['direction_deg'] = float(direction)
            objects.append(row)
        if not objects:
            unlabeled += 1  # no rotated box: not labeled for this family (an empty image is not assumed to be a background)
            continue
        saved = assignments.get(str(image)) or assignments.get(relative)
        _, folder = folder_label_split(PurePath(relative), task)
        split = saved if saved in ('train', 'val', 'test') else folder if folder in ('train', 'val', 'test') else _proposed_split(relative)
        sources.add('saved' if saved in ('train', 'val', 'test') else 'folders' if folder in ('train', 'val', 'test') else 'proposed')
        samples.append({'image': relative, 'split': split, 'objects': objects})
    # The workbench learns an object direction only when every box has one; a partial set is left out, not guessed.
    boxes = [obj for row in samples for obj in row['objects']]
    directions_dropped = 0
    if any('direction_deg' in obj for obj in boxes) and not all('direction_deg' in obj for obj in boxes):
        directions_dropped = sum('direction_deg' in obj for obj in boxes)
        for obj in boxes:
            obj.pop('direction_deg', None)
    return {'samples': samples, 'skipped': skipped, 'unlabeled_images': unlabeled, 'directions_dropped': directions_dropped,
            'split_source': 'mixed' if len(sources) > 1 else next(iter(sources), 'none'),
            'counts': {'images': len(samples), 'objects': sum(len(row['objects']) for row in samples)}}

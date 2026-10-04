"""Gold-sample label review (E05): a labeler's labels of gold images are compared with approved reference labels, object
by object, and every disagreement is named and placed.

A quality profile fixes what is compared: the reference label set and its approved gold images (each with the hash of
its reference labels at that moment, which must be the labels the approval was given to), the guideline (the reference
label set's labelbook version and hash), the candidate label set, the task (the project's own, detection or
segmentation), the shape metric and the tolerance. A report lists stable conflicts (missing, extra, class and geometry
disagreements, each with its image, objects and their boxes) and its limitations.

A report is current while its gold set holds: a gold label or the guideline that changed, or a gold image no longer
approved, stales it. It supports an approval only when it is current, the labeler's labels are still the ones it
compared, and it passes: every gold image labeled and no conflict.

Gold images are the reference for this check, so they leave ordinary training, validation and test use unless the
project's dataset policy includes them (``include_gold_in_training``); a reference label never
becomes test truth by being a gold sample.

Detection compares boxes, rotated boxes and polygons by area overlap (IoU: exact for convex shapes, rasterised at
1/8 px for a concave polygon); segmentation compares the connected regions of each class in the label masks (8-connected:
touching instances of one class are one region). Other tasks and shapes are refused, not approximated: image tags
(classification or anomaly labels) and, in segmentation, objects without a label mask.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np

from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.project_labelsets import _atomic_json, labelset_root, load_labelsets

TASKS = ('detection', 'segmentation')
DETECTION_SHAPES = ('bbox', 'rotated_bbox', 'polygon')
MIN_OVERLAP = 0.1   # below this two objects are not the same object
MAX_OBJECTS = 1000  # per image and side; more is refused rather than compared for minutes
_ID = re.compile(r'[0-9a-f]{32}')


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_sha(value: Any) -> str:
    return _sha(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))


def _store(project: dict) -> Path:
    return Path(project['project_dir']).resolve() / 'annotation_quality'


def _relative(source: Path, image: str) -> str:
    path = Path(image).resolve()
    if not path.is_relative_to(source):
        raise ValueError(f'{image} is not inside the project source')
    return path.relative_to(source).as_posix()


def saved_labels(project: dict, labelset_id: str, image_path: str) -> Optional[dict]:
    """The labels a label set saved for one image (the app's JSON and mask), or None when it saved none."""
    folder = dataset_annotation_dir(Path(image_path).resolve().parent, labelset_root(Path(project['project_dir']), labelset_id), use_scope=False)
    stem = Path(image_path).stem
    json_path = folder / f'{stem}.json'
    if not json_path.is_file() or json_path.is_symlink():
        return None
    raw = json_path.read_bytes()
    data = json.loads(raw.decode('utf-8'))
    mask_path = folder / 'masks' / f'{stem}.png'
    mask = mask_path.read_bytes() if mask_path.is_file() and not mask_path.is_symlink() else None
    return {'annotations': data.get('annotations') or [], 'width': data.get('image_width'), 'height': data.get('image_height'),
            'annotation_sha256': _sha(raw), 'mask_sha256': _sha(mask) if mask is not None else None, 'mask_png': mask}


def _ledger(project: dict, labelset_id: str) -> dict:
    from backend.engine import dataset_metadata
    path = dataset_metadata.ledger_path(Path(project['project_dir']), Path(project['source_dataset_dir']),
                                        labelset_root(Path(project['project_dir']), labelset_id))
    if path.is_symlink():
        raise ValueError('The label ledger cannot be a link')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'images': {}}


def guideline(project: dict, labelset_id: str) -> Optional[dict]:
    """The label set's current labelbook (its guideline): version and hash, or None when none was published."""
    books = ((_ledger(project, labelset_id).get('team_data') or {}).get('books')) or []
    return {'book_id': books[-1]['id'], 'version': books[-1]['version'], 'sha256': books[-1]['sha256']} if books else None


def create_profile(project: dict, *, task: str, reference_labelset: str, candidate_labelset: str, gold_images: list[str],
                   tolerance: float = 0.5, actor: str = 'this computer') -> dict:
    """A profile over approved gold images of the reference label set."""
    if task not in TASKS:
        raise ValueError(f'label review supports {", ".join(TASKS)}; {task} is not compared')
    if project.get('task') != task:
        raise ValueError(f"this project's task is {project.get('task')}; label review compares {task} labels only in a {task} project")
    if reference_labelset == candidate_labelset:
        raise ValueError('the candidate label set must differ from the reference')
    known = {row['id'] for row in load_labelsets(Path(project['project_dir']))['labelsets']}
    for name, value in (('reference', reference_labelset), ('candidate', candidate_labelset)):
        if value not in known:
            raise ValueError(f'the {name} label set {value} does not exist in this project')
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not MIN_OVERLAP < tolerance <= 1:
        raise ValueError(f'tolerance is the overlap (IoU) that counts as agreement, above {MIN_OVERLAP} and at most 1')
    if not gold_images or len(gold_images) > 2000 or len(set(gold_images)) != len(gold_images):
        raise ValueError('choose 1 to 2000 distinct gold images')
    source = Path(project['source_dataset_dir']).resolve()
    ledger = _ledger(project, reference_labelset)['images']
    gold = []
    for image in gold_images:
        relative = _relative(source, image)
        row = ledger.get(relative) or {}
        if row.get('workflow_state') != 'approved':
            raise ValueError(f'{relative} is not approved in the reference label set')
        labels = saved_labels(project, reference_labelset, str(source / relative))
        if labels is None:
            raise ValueError(f'{relative} has no saved reference labels')
        if (row.get('annotation_hash'), row.get('mask_hash')) != (labels['annotation_sha256'], labels['mask_sha256']):
            raise ValueError(f'the labels of {relative} changed after they were approved; review them again before using them as gold')
        _shapes(task, labels, f'{relative} (reference label set {reference_labelset})')  # refuse unsupported shapes now
        if task == 'segmentation' and labels['mask_png'] is None and any(item.get('type') != 'tag' for item in labels['annotations']):
            raise ValueError(f'{relative} has segmentation objects without a label mask; they cannot be compared')
        gold.append({'relative_path': relative, 'annotation_sha256': labels['annotation_sha256'], 'mask_sha256': labels['mask_sha256']})
    profile = {'profile_id': uuid.uuid4().hex, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'actor': actor,
               'task': task, 'reference_labelset': reference_labelset, 'candidate_labelset': candidate_labelset,
               'reference_snapshot': sorted(gold, key=lambda row: row['relative_path']),
               'guideline_revision': guideline(project, reference_labelset), 'class_match': 'exact',
               'shape_metric': 'region_iou' if task == 'segmentation' else 'shape_iou', 'tolerance': float(tolerance),
               'scope': {'project_id': project.get('id'), 'source_dataset_path': str(source)}}
    profile['profile_sha256'] = _canonical_sha(profile)
    folder = _store(project) / 'profiles'
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(folder / f"{profile['profile_id']}.json", profile)
    return profile


def rebind_store(folder: Path, rebind: Callable[[Any], Any], restoration: dict, label_hashes: Optional[dict] = None) -> int:
    """After a project restore, the store's profiles and reports with their paths rebound (the generic rewrite of a
    restore would break their hashes). Each record's hash is checked first (a changed record stops the restore), then
    recomputed with the restoration recorded; the label files the restore rewrote keep their place through
    ``label_hashes`` (old file hash to new), and reports follow their profile's new hash. Returns the records rebound."""
    hashes = dict(label_hashes or {})

    def relabel(value: Any) -> Any:
        if isinstance(value, str):
            return hashes.get(value, value)
        if isinstance(value, list):
            return [relabel(item) for item in value]
        if isinstance(value, dict):
            return {key: relabel(item) for key, item in value.items()}
        return value
    folder = Path(folder)
    renamed: dict[str, str] = {}
    count = 0
    for kind, hash_key in (('profiles', 'profile_sha256'), ('reports', 'report_sha256')):
        directory = folder / kind
        for path in sorted(directory.glob('*.json')) if directory.is_dir() else []:
            if not _ID.fullmatch(path.stem) or path.is_symlink():
                continue
            value = json.loads(path.read_text(encoding='utf-8'))
            body = {key: item for key, item in value.items() if key != hash_key}
            if _canonical_sha(body) != value.get(hash_key):
                raise ValueError(f'the quality {kind[:-1]} {path.name} was changed after it was made')
            updated = relabel(rebind(body))
            if kind == 'reports' and updated.get('profile_sha256') in renamed:
                updated['profile_sha256'] = renamed[updated['profile_sha256']]
            updated['archive_restorations'] = [*body.get('archive_restorations', []), {**restoration, f'previous_{hash_key}': value[hash_key]}]
            updated[hash_key] = _canonical_sha(updated)
            if kind == 'profiles':
                renamed[value[hash_key]] = updated[hash_key]
            _atomic_json(path, updated)
            count += 1
    return count


def read_profile(project: dict, profile_id: str) -> dict:
    if not isinstance(profile_id, str) or not _ID.fullmatch(profile_id):
        raise ValueError('no such quality profile')
    path = _store(project) / 'profiles' / f'{profile_id}.json'
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('no such quality profile')
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'the quality profile file {path.name} cannot be read ({exc}); restore or retire it') from exc
    body = {key: value for key, value in profile.items() if key != 'profile_sha256'}
    if _canonical_sha(body) != profile.get('profile_sha256'):
        raise ValueError(f'the quality profile {path.name} was changed after it was made')
    return profile


def _read_store_json(path: Path, default: Any) -> Any:
    """A store file, or the default when absent; a damaged one stops with its name (it decides which images are gold)."""
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'the quality review file {path.name} cannot be read ({exc}); restore it from a backup') from exc


def _retired(project: dict) -> dict:
    return _read_store_json(_store(project) / 'retired.json', {})


def list_profiles(project: dict, *, include_retired: bool = False) -> list[dict]:
    """The profiles (only files named by a profile id are read; a damaged one stops this with its name)."""
    folder = _store(project) / 'profiles'
    if not folder.is_dir():
        return []
    retired = _retired(project)
    rows = [{**read_profile(project, path.stem), **({'retired': retired[path.stem]} if path.stem in retired else {})}
            for path in folder.glob('*.json') if _ID.fullmatch(path.stem)]
    return sorted((row for row in rows if include_retired or 'retired' not in row), key=lambda row: row['created_at'], reverse=True)


def retire_profile(project: dict, profile_id: str, actor: str) -> dict:
    """Stop using a profile: its gold images return to ordinary use and it runs no new report (its reports stay)."""
    read_profile(project, profile_id)
    retired = _retired(project)
    retired[profile_id] = {'actor': actor, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    _store(project).mkdir(parents=True, exist_ok=True)
    _atomic_json(_store(project) / 'retired.json', retired)
    return retired[profile_id]


def gold_policy(project: dict) -> dict:
    """The dataset policy for gold images: excluded from ordinary training and test use unless it says otherwise."""
    value = _read_store_json(_store(project) / 'policy.json', {})
    value = value if isinstance(value, dict) else {}
    return {'include_gold_in_training': value.get('include_gold_in_training') is True,
            **{key: value[key] for key in ('changed_by', 'changed_at') if key in value}}


def set_gold_policy(project: dict, include_gold_in_training: bool, actor: str = 'this computer') -> dict:
    """Set whether gold images stay in training; who changed it and when are kept with it (it changes the training
    receipt)."""
    if not isinstance(include_gold_in_training, bool):
        raise ValueError('include_gold_in_training is true or false')
    _store(project).mkdir(parents=True, exist_ok=True)
    policy = {'include_gold_in_training': include_gold_in_training, 'changed_by': actor,
              'changed_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}
    _atomic_json(_store(project) / 'policy.json', policy)
    return policy


def gold_set(project: dict) -> set[str]:
    """The gold images of the active profiles of this source (whatever the policy)."""
    source = Path(project['source_dataset_dir']).resolve()
    return {str((source / row['relative_path']).resolve()) for profile in list_profiles(project)
            if Path(profile['scope']['source_dataset_path']).resolve() == source for row in profile['reference_snapshot']}


def gold_image_paths(project: dict) -> set[str]:
    """The gold images that leave ordinary training and test use (all of the gold set unless the dataset policy keeps it)."""
    return set() if gold_policy(project)['include_gold_in_training'] else gold_set(project)


def gold_receipt(project: dict) -> dict:
    """What a training or evaluation binding records about gold images: the policy and the gold set's hash."""
    images = sorted(gold_set(project))
    return {'include_gold_in_training': gold_policy(project)['include_gold_in_training'], 'gold_images': len(images),
            'gold_set_sha256': _canonical_sha(images)}


def _polygon(item: dict) -> Optional[np.ndarray]:
    shape = item.get('type')
    if shape == 'bbox' and item.get('bbox'):
        x1, y1, x2, y2 = item['bbox']
        return np.array([[x1, y1], [x2, y1], [x2, y2], [x1, y2]], np.float64)
    if shape == 'rotated_bbox' and item.get('rotated_bbox'):
        cx, cy, w, h, angle = item['rotated_bbox']
        return cv2.boxPoints(((cx, cy), (w, h), angle)).astype(np.float64)
    if shape == 'polygon' and item.get('polygon') and len(item['polygon']) >= 3:
        return np.asarray(item['polygon'], np.float64)
    return None


_NORMAL_NAMES = frozenset({'good', 'ok', 'normal', 'pass'})


def _normal_mark(item: dict) -> bool:
    """The labeling view's "Mark as Normal (OK)": a tag that says the image has no defect."""
    return item.get('type') == 'tag' and (item.get('is_normal') is True or str(item.get('label') or '').strip().lower() in _NORMAL_NAMES)


def _object_keys(annotations: list) -> list[tuple[str, list]]:
    """Readable names and typed identities: unique id, repeated id plus occurrence, or position without an id.
    User ids share no identity namespace with generated occurrence or position markers. Deleting an unrelated
    uniquely identified object renames no conflict."""
    ids = [str(item.get('id') or '') for item in annotations]
    seen: dict[str, int] = {}
    keys = []
    for index, value in enumerate(ids):
        if not value:
            keys.append((f'#{index + 1}', ['position', index + 1]))
        elif ids.count(value) == 1:
            keys.append((value, ['id', value]))
        else:
            seen[value] = seen.get(value, 0) + 1
            keys.append((f'{value}#{seen[value]}', ['repeated_id', value, seen[value]]))
    return keys


def _shapes(task: str, labels: dict, where: str = '') -> list[dict]:
    """The comparable objects of one image: detection shapes as polygons, segmentation regions per class (each region
    cropped to its box). A normal mark as the image's only label is an image without objects (a gold negative, or a
    labeler's OK on a defective part, which is then the miss). Other image tags are refused, naming the image and side:
    they say what an image is, not where (classification or anomaly)."""
    prefix = f'{where}: ' if where else ''
    annotations = labels['annotations']
    if any(_normal_mark(item) for item in annotations):
        others = [item for item in annotations if not _normal_mark(item)]
        has_mask = task == 'segmentation' and labels.get('mask_png') is not None and _mask_has_regions(labels['mask_png'])
        if others or has_mask:
            raise ValueError(f'{prefix}a normal mark sits beside objects; mark the image normal or label its objects, not both')
        return []
    if any(item.get('type') == 'tag' for item in annotations):
        tag = next(item for item in annotations if item.get('type') == 'tag')
        raise ValueError(f"{prefix}the image tag {tag.get('label')!r} (a classification or anomaly label) is not compared by object-level label "
                         'review; only a normal mark (no objects) is')
    if task == 'detection':
        objects = []
        keys = _object_keys(annotations)
        for index, item in enumerate(annotations):
            if item.get('type') not in DETECTION_SHAPES:
                raise ValueError(f"{prefix}shape {item.get('type')!r} is not compared in detection label review")
            polygon = _polygon(item)
            if polygon is None or not np.isfinite(polygon).all():
                raise ValueError(f'{prefix}object {index + 1} has no usable geometry')
            objects.append({'key': keys[index][0], 'object_key': keys[index][1], 'label': item.get('label'),
                            'polygon': polygon, 'bbox': _polygon_bbox(polygon)})
        if len(objects) > MAX_OBJECTS:
            raise ValueError(f'{prefix}an image with {len(objects)} objects is not compared (at most {MAX_OBJECTS})')
        return objects
    if labels['mask_png'] is None:
        return []
    mask = cv2.imdecode(np.frombuffer(labels['mask_png'], np.uint8), cv2.IMREAD_UNCHANGED)
    if mask is None or mask.ndim != 2:
        raise ValueError(f'{prefix}the label mask is not a single-channel class mask')
    names = {int(row['category_id']): row.get('label') for row in labels['annotations'] if row.get('category_id') is not None}
    regions = []
    for class_id in sorted(int(value) for value in np.unique(mask) if value != 0):
        count, components, stats, _ = cv2.connectedComponentsWithStats((mask == class_id).astype(np.uint8), connectivity=8)
        for component in range(1, count):
            x, y, w, h = (int(value) for value in stats[component, :4])
            region = components[y:y + h, x:x + w] == component
            first = int(np.flatnonzero(region)[0])
            regions.append({'key': f'{class_id}:{x + first % w},{y + first // w}', 'label': names.get(class_id, str(class_id)),
                            'mask': region, 'bbox': [x, y, x + w, y + h], 'shape': mask.shape})
            if len(regions) > MAX_OBJECTS:
                raise ValueError(f'{prefix}an image with more than {MAX_OBJECTS} regions is not compared')
    return regions


def _mask_has_regions(mask_png: bytes) -> bool:
    mask = cv2.imdecode(np.frombuffer(mask_png, np.uint8), cv2.IMREAD_UNCHANGED)
    return mask is not None and bool(np.any(mask))


def _polygon_bbox(polygon: np.ndarray) -> list[float]:
    return [float(polygon[:, 0].min()), float(polygon[:, 1].min()), float(polygon[:, 0].max()), float(polygon[:, 1].max())]


def _boxes_meet(a: list, b: list) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _overlap(a: dict, b: dict) -> float:
    if not _boxes_meet(a['bbox'], b['bbox']):
        return 0.0
    if 'mask' in a:
        x0, y0 = max(a['bbox'][0], b['bbox'][0]), max(a['bbox'][1], b['bbox'][1])
        x1, y1 = min(a['bbox'][2], b['bbox'][2]), min(a['bbox'][3], b['bbox'][3])
        inter = int(np.logical_and(a['mask'][y0 - a['bbox'][1]:y1 - a['bbox'][1], x0 - a['bbox'][0]:x1 - a['bbox'][0]],
                                   b['mask'][y0 - b['bbox'][1]:y1 - b['bbox'][1], x0 - b['bbox'][0]:x1 - b['bbox'][0]]).sum())
        union = int(a['mask'].sum()) + int(b['mask'].sum()) - inter
        return inter / union if union else 0.0
    first, second = a['polygon'].astype(np.float32), b['polygon'].astype(np.float32)
    if cv2.isContourConvex(first) and cv2.isContourConvex(second):
        inter, _ = cv2.intersectConvexConvex(first, second)
        union = abs(cv2.contourArea(first)) + abs(cv2.contourArea(second)) - inter
        return float(inter / union) if union > 0 else 0.0
    # A concave polygon: rasterised at 1/8 px over the pair's box (at most about 2048 px across).
    points = np.vstack([a['polygon'], b['polygon']])
    origin = points.min(axis=0)
    span = float((points.max(axis=0) - origin).max())
    scale = 8.0 if span * 8 <= 2048 else 2048.0 / max(span, 1.0)
    size = tuple(int(np.ceil(value * scale)) + 2 for value in (points[:, 1].max() - origin[1], points[:, 0].max() - origin[0]))
    canvases = []
    for polygon in (a['polygon'], b['polygon']):
        canvas = np.zeros(size, np.uint8)
        cv2.fillPoly(canvas, [np.round((polygon - origin) * scale * 16).astype(np.int32)], 1, shift=4)
        canvases.append(canvas)
    union = np.logical_or(*canvases).sum()
    return float(np.logical_and(*canvases).sum() / union) if union else 0.0


def compare_image(task: str, reference: dict, candidate: Optional[dict], tolerance: float, image: str = '') -> list[dict]:
    """The conflicts of one image: objects are paired by the largest overlap first (at least 0.1; pairs whose boxes do not
    meet are not measured); a pair of different classes is a class conflict, a pair under the tolerance a geometry
    conflict; the rest are missing or extra."""
    name = image or 'the image'
    ours = _shapes(task, reference, f'{name} (reference)')
    theirs = _shapes(task, candidate, f'{name} (candidate)') if candidate is not None else []
    if task == 'segmentation' and ours and theirs and ours[0]['shape'] != theirs[0]['shape']:
        raise ValueError(f"{name} (candidate): the label mask is {theirs[0]['shape']}, the reference's {ours[0]['shape']}")
    pairs = sorted(((_overlap(a, b), i, j) for i, a in enumerate(ours) for j, b in enumerate(theirs) if _boxes_meet(a['bbox'], b['bbox'])),
                   key=lambda row: (-row[0], row[1], row[2]))
    used_ref, used_cand, conflicts = set(), set(), []
    for overlap, i, j in pairs:
        if overlap < MIN_OVERLAP or i in used_ref or j in used_cand:
            continue
        used_ref.add(i)
        used_cand.add(j)
        a, b = ours[i], theirs[j]
        if a['label'] != b['label']:
            conflicts.append({'kind': 'class', 'reference_object': a['key'], 'candidate_object': b['key'],
                              **({'reference_object_key': a['object_key'], 'candidate_object_key': b['object_key']} if task == 'detection' else {}),
                              'reference_label': a['label'],
                              'candidate_label': b['label'], 'overlap': round(overlap, 4), 'bbox': list(a['bbox']), 'candidate_bbox': list(b['bbox'])})
        elif overlap < tolerance:
            conflicts.append({'kind': 'geometry', 'reference_object': a['key'], 'candidate_object': b['key'],
                              **({'reference_object_key': a['object_key'], 'candidate_object_key': b['object_key']} if task == 'detection' else {}),
                              'label': a['label'],
                              'overlap': round(overlap, 4), 'bbox': list(a['bbox']), 'candidate_bbox': list(b['bbox'])})
    conflicts += [{'kind': 'missing', 'reference_object': a['key'],
                   **({'reference_object_key': a['object_key']} if task == 'detection' else {}),
                   'label': a['label'], 'bbox': list(a['bbox'])} for i, a in enumerate(ours) if i not in used_ref]
    conflicts += [{'kind': 'extra', 'candidate_object': b['key'],
                   **({'candidate_object_key': b['object_key']} if task == 'detection' else {}),
                   'label': b['label'], 'candidate_bbox': list(b['bbox']), 'bbox': list(b['bbox'])}
                  for j, b in enumerate(theirs) if j not in used_cand]
    return conflicts


def staleness(project: dict, profile: dict) -> list[str]:
    """Why the profile's reports no longer speak for the gold set: a gold label or the guideline changed, or a gold image is
    no longer approved (with the labels it was approved for)."""
    source = Path(project['source_dataset_dir']).resolve()
    ledger = _ledger(project, profile['reference_labelset'])['images']
    reasons = []
    for row in profile['reference_snapshot']:
        labels = saved_labels(project, profile['reference_labelset'], str(source / row['relative_path']))
        if labels is None or (labels['annotation_sha256'], labels['mask_sha256']) != (row['annotation_sha256'], row['mask_sha256']):
            reasons.append(f"gold_label_changed:{row['relative_path']}")
        state = ledger.get(row['relative_path']) or {}
        if (state.get('workflow_state') != 'approved'
                or (state.get('annotation_hash'), state.get('mask_hash')) != (row['annotation_sha256'], row['mask_sha256'])):
            reasons.append(f"gold_unapproved:{row['relative_path']}")
    if guideline(project, profile['reference_labelset']) != profile['guideline_revision']:
        reasons.append('guideline_changed')
    if profile['profile_id'] in _retired(project):
        reasons.append('profile_retired')
    return reasons


LIMITATIONS = ['object-level comparison against approved gold samples only; it says nothing about images outside the gold set',
               'detection overlap (IoU) is exact for boxes, rotated boxes and convex polygons and rasterised at 1/8 px for a concave polygon',
               'segmentation compares 8-connected regions of each class: touching instances of one class are one region',
               'a normal mark as an image\'s only label is an image without objects; other image tags (classification or anomaly '
               'labels) are not compared, and a labeler image with one is reported as not comparable',
               'only labels saved in the app are compared: labels that exist only in the source folder (imported COCO, YOLO or '
               'mask files) are not read',
               'classes are compared by exact name; a renamed class is a class conflict',
               'the report runs within the request, for up to 2000 gold images and 1000 objects per image and side']


def run_report(project: dict, profile_id: str) -> dict:
    profile = read_profile(project, profile_id)
    stale = staleness(project, profile)
    if stale:
        raise ValueError('the gold set changed since the profile was made (' + ', '.join(stale[:5]) + '); make a new profile')
    source = Path(project['source_dataset_dir']).resolve()
    if Path(profile['scope']['source_dataset_path']).resolve() != source:
        raise ValueError('the profile belongs to another project source')
    images, revision = [], {}
    for row in profile['reference_snapshot']:
        image = str(source / row['relative_path'])
        reference = saved_labels(project, profile['reference_labelset'], image)
        candidate = saved_labels(project, profile['candidate_labelset'], image)
        try:
            conflicts, error = compare_image(profile['task'], reference, candidate, profile['tolerance'], row['relative_path']), None
        except ValueError as exc:  # the labeler's labels of this image cannot be compared; the others still are
            if candidate is None or '(candidate)' not in str(exc):
                raise
            conflicts, error = [], str(exc)
        for conflict in conflicts:
            conflict['conflict_id'] = _canonical_sha({'image': row['relative_path'], **{k: v for k, v in conflict.items()
                                                                                       if k not in ('overlap', 'bbox', 'candidate_bbox')}})[:16]
        revision[row['relative_path']] = ([candidate['annotation_sha256'], candidate['mask_sha256']] if candidate else None)
        images.append({'relative_path': row['relative_path'], 'image_path': image, 'labeled': candidate is not None,
                       'conflicts': sorted(conflicts, key=lambda c: (c['kind'], c.get('reference_object') or '', c.get('candidate_object') or '')),
                       **({'error': error} if error else {})})
    counts = {kind: sum(c['kind'] == kind for image in images for c in image['conflicts']) for kind in ('missing', 'extra', 'class', 'geometry')}
    counts['not_comparable'] = sum(1 for image in images if image.get('error'))
    report = {'report_id': uuid.uuid4().hex, 'profile_id': profile_id, 'profile_sha256': profile['profile_sha256'],
              'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'task': profile['task'],
              'reference_revision': {row['relative_path']: [row['annotation_sha256'], row['mask_sha256']] for row in profile['reference_snapshot']},
              'annotation_revision': revision, 'images': images, 'counts': counts,
              'agreeing_images': sum(1 for image in images if image['labeled'] and not image['conflicts'] and not image.get('error')),
              'limitations': LIMITATIONS}
    report['passes'] = report['agreeing_images'] == len(images) and not any(counts.values())
    report['report_sha256'] = _canonical_sha(report)
    folder = _store(project) / 'reports'
    folder.mkdir(parents=True, exist_ok=True)
    _atomic_json(folder / f"{report['report_id']}.json", report)
    return report


def _load_report(project: dict, report_id: str) -> dict:
    if not isinstance(report_id, str) or not _ID.fullmatch(report_id):
        raise ValueError('no such quality report')
    path = _store(project) / 'reports' / f'{report_id}.json'
    if not path.is_file() or path.is_symlink():
        raise FileNotFoundError('no such quality report')
    report = json.loads(path.read_text(encoding='utf-8'))
    if _canonical_sha({key: value for key, value in report.items() if key != 'report_sha256'}) != report.get('report_sha256'):
        raise ValueError(f'the quality report {path.name} was changed after it was made')
    return report


def list_reports(project: dict, profile_id: Optional[str] = None) -> list[dict]:
    """The saved reports, newest first, as summaries (each checked against its hash; a changed or unreadable one is listed
    as such, without its counts); read_report says whether one is current and eligible."""
    folder = _store(project) / 'reports'
    rows = []
    for path in folder.glob('*.json') if folder.is_dir() else []:
        if not _ID.fullmatch(path.stem):
            continue
        try:
            report = _load_report(project, path.stem)
        except (OSError, ValueError) as exc:
            rows.append({'report_id': path.stem, 'profile_id': None, 'created_at': None, 'task': None, 'counts': None,
                         'agreeing_images': None, 'images': None, 'integrity_error': str(exc)})
            continue
        if profile_id is not None and report.get('profile_id') != profile_id:
            continue
        rows.append({key: report.get(key) for key in ('report_id', 'profile_id', 'created_at', 'task', 'counts', 'agreeing_images', 'passes')}
                    | {'images': len(report.get('images') or [])})
    return sorted(rows, key=lambda row: (row['created_at'] or '', row['report_id'] or ''), reverse=True)


def read_report(project: dict, report_id: str) -> dict:
    """A saved report with whether it is current (its gold set holds) and whether it supports an approval: current, the
    labeler's labels still the ones it compared, and passing (every gold image labeled, no conflict)."""
    report = _load_report(project, report_id)
    profile = read_profile(project, report['profile_id'])
    reasons = staleness(project, profile)
    if report['profile_sha256'] != profile['profile_sha256']:
        reasons.append('profile_changed')
    source = Path(project['source_dataset_dir']).resolve()
    changed = []
    for relative, compared in (report.get('annotation_revision') or {}).items():
        candidate = saved_labels(project, profile['candidate_labelset'], str(source / relative))
        now = [candidate['annotation_sha256'], candidate['mask_sha256']] if candidate else None
        if now != compared:
            changed.append(f'candidate_changed:{relative}')
    passes = bool(report.get('passes'))
    return {**report, 'stale': bool(reasons), 'stale_reasons': reasons, 'current': not reasons, 'candidate_changes': changed,
            'approval_eligible': not reasons and not changed and passes}

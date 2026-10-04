"""E05 object identity through approved gold, saved profiles and real reports.

Display IDs may contain any delimiter; they must not collapse placed conflicts.
Fixtures write isolated app-format labels and their approval ledger, without API,
training, archive or user-data dependencies.
"""
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.engine import annotation_quality as aq
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.project_labelsets import labelset_root


CANDIDATE = 'ls_000000000001'
NORMAL = {'id': 'normal', 'type': 'tag', 'label': 'OK', 'is_normal': True}


def _project(tmp_path, task='detection'):
    source = tmp_path / 'source' / 'train'
    source.mkdir(parents=True)
    for name in 'abc':
        assert cv2.imwrite(str(source / f'{name}.png'), np.zeros((100, 120, 3), np.uint8))
    folder = tmp_path / 'project'
    folder.mkdir()
    (folder / 'labelsets.json').write_text(json.dumps({
        'schema_version': 1, 'active_id': 'default', 'labelsets': [
            {'id': 'default', 'name': 'Reference', 'source_id': None, 'created_at': ''},
            {'id': CANDIDATE, 'name': 'Candidate', 'source_id': 'default', 'created_at': ''},
        ],
    }), encoding='utf-8')
    return {'id': 'identity-contract', 'project_dir': str(folder),
            'source_dataset_dir': str(source.parent), 'task': task}


def _box(identifier, x, y=10, label='scratch'):
    row = {'type': 'bbox', 'label': label, 'bbox': [x, y, x + 10, y + 10]}
    if identifier is not None:
        row['id'] = identifier
    return row


def _save(project, labelset, name, annotations, mask=None):
    image = Path(project['source_dataset_dir']) / 'train' / f'{name}.png'
    folder = dataset_annotation_dir(image.parent, labelset_root(Path(project['project_dir']), labelset), use_scope=False)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{name}.json').write_text(json.dumps({
        'annotations': annotations, 'image_width': 120, 'image_height': 100,
    }, ensure_ascii=False), encoding='utf-8')
    if mask is not None:
        (folder / 'masks').mkdir(exist_ok=True)
        assert cv2.imwrite(str(folder / 'masks' / f'{name}.png'), mask)
    return str(image)


def _approve(project, names):
    source = Path(project['source_dataset_dir'])
    path = dm.ledger_path(Path(project['project_dir']), source, labelset_root(Path(project['project_dir']), 'default'))
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {}
    for name in names:
        saved = aq.saved_labels(project, 'default', str(source / 'train' / f'{name}.png'))
        rows[f'train/{name}.png'] = {'workflow_state': 'approved',
                                  'annotation_hash': saved['annotation_sha256'], 'mask_hash': saved['mask_sha256']}
    path.write_text(json.dumps({'images': rows}), encoding='utf-8')


def _profile(project, names=('a',)):
    _approve(project, names)
    return aq.create_profile(project, task=project['task'], reference_labelset='default', candidate_labelset=CANDIDATE,
                             gold_images=[str(Path(project['source_dataset_dir']) / 'train' / f'{name}.png') for name in names])


def _conflicts(project, profile):
    report = aq.run_report(project, profile['profile_id'])
    return report, report['images'][0]['conflicts']


def _placed_ids(conflicts):
    return {tuple(row['bbox']): row['conflict_id'] for row in conflicts}


def test_repeated_ids_cannot_alias_a_literal_suffix_in_extra_report_conflicts(tmp_path):
    """Appending an occurrence to x must not alias the separate valid ID x#1."""
    project = _project(tmp_path)
    _save(project, 'default', 'a', [NORMAL])
    _save(project, CANDIDATE, 'a', [_box('x', 10), _box('x', 40), _box('x#1', 70)])
    profile = _profile(project)
    report, conflicts = _conflicts(project, profile)
    assert report['counts'] == {'missing': 0, 'extra': 3, 'class': 0, 'geometry': 0, 'not_comparable': 0}
    assert {tuple(row['candidate_bbox']) for row in conflicts} == {(10, 10, 20, 20), (40, 10, 50, 20), (70, 10, 80, 20)}
    assert len({row['conflict_id'] for row in conflicts}) == 3, conflicts
    assert sorted(row['candidate_object'] for row in conflicts) == ['x#1', 'x#1', 'x#2'], 'display strings stay readable'
    _, repeated = _conflicts(project, profile)
    assert _placed_ids(repeated) == _placed_ids(conflicts)


def test_missing_id_cannot_alias_a_literal_position_in_missing_report_conflicts(tmp_path):
    """The positional fallback for an absent ID must not alias valid literal #1."""
    project = _project(tmp_path)
    _save(project, 'default', 'a', [_box(None, 10), _box('#1', 40)])
    _save(project, CANDIDATE, 'a', [NORMAL])
    profile = _profile(project)
    report, conflicts = _conflicts(project, profile)
    assert report['counts'] == {'missing': 2, 'extra': 0, 'class': 0, 'geometry': 0, 'not_comparable': 0}
    assert {tuple(row['bbox']) for row in conflicts} == {(10, 10, 20, 20), (40, 10, 50, 20)}
    assert len({row['conflict_id'] for row in conflicts}) == 2, conflicts
    assert [row['reference_object'] for row in conflicts] == ['#1', '#1']
    _, repeated = _conflicts(project, profile)
    assert _placed_ids(repeated) == _placed_ids(conflicts)


def test_arbitrary_literal_ids_remain_valid_and_readable(tmp_path):
    project = _project(tmp_path)
    identifiers = ['x#1', '#1', 'id|1', '["duplicate","x",1]', '균열:#1']
    _save(project, 'default', 'a', [NORMAL])
    _save(project, CANDIDATE, 'a', [_box(value, 1 + index * 20) for index, value in enumerate(identifiers)])
    report, conflicts = _conflicts(project, _profile(project))
    assert report['counts']['extra'] == 5 and report['counts']['not_comparable'] == 0
    assert {row['candidate_object'] for row in conflicts} == set(identifiers)
    assert len({row['conflict_id'] for row in conflicts}) == 5


def test_unrelated_unique_object_deletion_preserves_class_and_geometry_conflicts(tmp_path):
    project = _project(tmp_path)
    _save(project, 'default', 'a', [_box('r1', 10), _box('r2', 40, label='dent'), _box('r3', 70)])
    candidates = [_box('c1', 10), _box('c2', 40), _box('c3', 75)]
    _save(project, CANDIDATE, 'a', candidates)
    profile = _profile(project)
    _, before = _conflicts(project, profile)
    assert [(row['kind'], row['reference_object'], row['candidate_object']) for row in before] == [
        ('class', 'r2', 'c2'), ('geometry', 'r3', 'c3')]
    assert before[0]['bbox'] == before[0]['candidate_bbox'] == [40, 10, 50, 20]
    assert before[1]['bbox'] == [70, 10, 80, 20] and before[1]['candidate_bbox'] == [75, 10, 85, 20]
    _save(project, CANDIDATE, 'a', candidates[1:])
    _, after = _conflicts(project, profile)
    remaining = [row for row in after if row['kind'] in ('class', 'geometry')]
    assert remaining == before


def test_normal_marks_and_unsupported_candidates_keep_report_decisions(tmp_path):
    project = _project(tmp_path)
    _save(project, 'default', 'a', [NORMAL])
    for name in 'bc':
        _save(project, 'default', name, [_box('reference', 10)])
    _save(project, CANDIDATE, 'a', [_box('extra', 10)])
    _save(project, CANDIDATE, 'b', [NORMAL])
    _save(project, CANDIDATE, 'c', [{'id': 'tag', 'type': 'tag', 'label': 'NG'}])
    profile = _profile(project, ('a', 'b', 'c'))
    report = aq.run_report(project, profile['profile_id'])
    assert report['counts'] == {'missing': 1, 'extra': 1, 'class': 0, 'geometry': 0, 'not_comparable': 1}
    assert report['images'][2]['conflicts'] == []
    assert 'NG' in report['images'][2]['error'] and '(candidate)' in report['images'][2]['error']
    reopened = aq.read_report(project, report['report_id'])
    assert reopened['current'] is True and reopened['passes'] is False and reopened['approval_eligible'] is False
    _save(project, CANDIDATE, 'a', [NORMAL, _box('extra', 10)])
    changed = aq.run_report(project, profile['profile_id'])
    assert changed['counts']['not_comparable'] == 2 and 'normal mark' in changed['images'][0]['error']


def test_segmentation_region_identity_and_mask_placement_are_preserved(tmp_path):
    project = _project(tmp_path, 'segmentation')
    mask = np.zeros((100, 120), np.uint8)
    mask[10:20, 10:20] = 1
    mask[40:50, 40:50] = 1
    _save(project, 'default', 'a', [{'id': 'ignored', 'type': 'brush_mask', 'category_id': 1, 'label': 'scratch'}], mask)
    _save(project, CANDIDATE, 'a', [NORMAL])
    profile = _profile(project)
    report, conflicts = _conflicts(project, profile)
    assert report['counts']['missing'] == 2 and report['counts']['not_comparable'] == 0
    assert [(row['reference_object'], row['bbox']) for row in conflicts] == [
        ('1:10,10', [10, 10, 20, 20]), ('1:40,40', [40, 40, 50, 50])]
    assert all('reference_object_key' not in row for row in conflicts), 'segmentation keeps its established region identity'
    _, again = _conflicts(project, profile)
    assert _placed_ids(again) == _placed_ids(conflicts)
    _save(project, CANDIDATE, 'a', [{'id': 'candidate', 'type': 'brush_mask', 'category_id': 1, 'label': 'scratch'}], mask)
    agreeing = aq.run_report(project, profile['profile_id'])
    assert agreeing['passes'] is True and agreeing['agreeing_images'] == 1


def test_saved_legacy_report_bytes_and_conflict_ids_are_not_rewritten(tmp_path):
    """Reading old reports must preserve their original display-only conflict hash."""
    project = _project(tmp_path)
    _save(project, 'default', 'a', [NORMAL])
    _save(project, CANDIDATE, 'a', [_box('x#1', 10)])
    profile = _profile(project)
    reference = aq.saved_labels(project, 'default', str(Path(project['source_dataset_dir']) / 'train' / 'a.png'))
    candidate = aq.saved_labels(project, CANDIDATE, str(Path(project['source_dataset_dir']) / 'train' / 'a.png'))
    conflict = {'kind': 'extra', 'candidate_object': 'x#1', 'label': 'scratch',
                'candidate_bbox': [10.0, 10.0, 20.0, 20.0], 'bbox': [10.0, 10.0, 20.0, 20.0],
                'conflict_id': '19cd423ddadaef25'}
    body = {'report_id': 'a' * 32, 'profile_id': profile['profile_id'], 'profile_sha256': profile['profile_sha256'],
            'created_at': '2026-10-03T00:00:00Z', 'task': 'detection',
            'reference_revision': {'train/a.png': [reference['annotation_sha256'], reference['mask_sha256']]},
            'annotation_revision': {'train/a.png': [candidate['annotation_sha256'], candidate['mask_sha256']]},
            'images': [{'relative_path': 'train/a.png', 'image_path': str(Path(project['source_dataset_dir']) / 'train' / 'a.png'),
                        'labeled': True, 'conflicts': [conflict]}],
            'counts': {'missing': 0, 'extra': 1, 'class': 0, 'geometry': 0, 'not_comparable': 0},
            'agreeing_images': 0, 'limitations': aq.LIMITATIONS, 'passes': False}
    body['report_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    path = Path(project['project_dir']) / 'annotation_quality' / 'reports' / ('a' * 32 + '.json')
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    original = path.read_bytes()
    reopened = aq.read_report(project, body['report_id'])
    assert reopened['images'][0]['conflicts'] == [conflict] and reopened['current'] is True
    assert aq.list_reports(project)[0]['counts'] == body['counts']
    aq.run_report(project, profile['profile_id'])
    assert path.read_bytes() == original
    body['counts']['extra'] = 0
    path.write_text(json.dumps(body), encoding='utf-8')
    with pytest.raises(ValueError, match='changed after it was made'):
        aq.read_report(project, body['report_id'])
    damaged = next(row for row in aq.list_reports(project) if row['report_id'] == body['report_id'])
    assert damaged['counts'] is None and 'changed after it was made' in damaged['integrity_error']

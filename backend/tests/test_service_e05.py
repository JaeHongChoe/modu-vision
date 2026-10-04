"""E05: a labeler's labels of approved gold images are compared with the reference object by object; missing, extra,
class and geometry disagreements are stable and placed; a changed gold label or guideline stales the report and its
approval eligibility; gold images leave ordinary training and test use unless the dataset policy keeps them.
"""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.engine import annotation_quality as aq
from backend.engine import dataset_metadata as dm
from backend.engine.annotation_storage import dataset_annotation_dir
from backend.engine.project_labelsets import labelset_root
from backend.tests.test_whole_flow_evaluation import workspace  # noqa: F401 - the whole-flow fixture (P1-1)

CANDIDATE = 'ls_000000000001'


def _project(tmp_path, task='detection'):
    source = tmp_path / 'source'
    (source / 'train').mkdir(parents=True)
    for name in ('a', 'b', 'c'):
        cv2.imwrite(str(source / 'train' / f'{name}.png'), np.zeros((100, 120, 3), np.uint8))
    project_dir = tmp_path / 'project'
    project_dir.mkdir()
    (project_dir / 'labelsets.json').write_text(json.dumps({'schema_version': 1, 'active_id': 'default', 'labelsets': [
        {'id': 'default', 'name': 'default', 'source_id': None, 'created_at': ''},
        {'id': CANDIDATE, 'name': 'labeler', 'source_id': 'default', 'created_at': ''}]}))
    return {'id': 'p1', 'project_dir': str(project_dir), 'source_dataset_dir': str(source), 'task': task}


def _labels(project, labelset, image, annotations, mask=None):
    folder = dataset_annotation_dir(Path(image).parent, labelset_root(Path(project['project_dir']), labelset), use_scope=False)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f'{Path(image).stem}.json').write_text(json.dumps({'annotations': annotations, 'image_width': 120, 'image_height': 100}))
    if mask is not None:
        (folder / 'masks').mkdir(exist_ok=True)
        cv2.imwrite(str(folder / 'masks' / f'{Path(image).stem}.png'), mask)


def _approve(project, labelset, relatives, book=None, books=None, state='approved'):
    """The label ledger as the app keeps it: each image approved with the hashes of the labels it was approved for."""
    path = dm.ledger_path(Path(project['project_dir']), Path(project['source_dataset_dir']), labelset_root(Path(project['project_dir']), labelset))
    path.parent.mkdir(parents=True, exist_ok=True)
    ledger = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'images': {}}  # the app's ledger keeps its other fields
    for relative in relatives:
        saved = aq.saved_labels(project, labelset, str(Path(project['source_dataset_dir']) / relative))
        ledger['images'][relative] = {**ledger['images'].get(relative, {}), 'workflow_state': state,
                                      'annotation_hash': saved and saved['annotation_sha256'], 'mask_hash': saved and saved['mask_sha256']}
    if books or book:
        from backend.engine.team_data import DEFAULT_SETTINGS
        ledger['team_data'] = {'schema_version': 1, 'settings': dict(DEFAULT_SETTINGS), **ledger.get('team_data', {}), 'books': books or [book]}
    path.write_text(json.dumps(ledger))


def box(identifier, label, x1, y1, x2, y2):
    return {'id': identifier, 'label': label, 'type': 'bbox', 'bbox': [x1, y1, x2, y2]}


BOOK = {'id': 'book_1', 'version': 1, 'sha256': 'a' * 64}


def _detection(tmp_path):
    project = _project(tmp_path)
    source = Path(project['source_dataset_dir'])
    a, b = str(source / 'train' / 'a.png'), str(source / 'train' / 'b.png')
    _labels(project, 'default', a, [box('r1', 'scratch', 10, 10, 40, 40), box('r2', 'dent', 60, 10, 90, 40),
                                    {'id': 'r3', 'label': 'scratch', 'type': 'polygon', 'polygon': [[10, 60], [50, 60], [50, 90], [10, 90]]}])
    _labels(project, 'default', b, [box('r4', 'scratch', 20, 20, 60, 60)])
    _approve(project, 'default', ['train/a.png', 'train/b.png'], BOOK)
    _labels(project, CANDIDATE, a, [box('c1', 'scratch', 11, 11, 40, 40),       # agrees
                                    box('c2', 'scratch', 60, 10, 90, 40),        # class conflict with r2
                                    box('c3', 'scratch', 25, 60, 65, 90),        # geometry: overlaps r3 under the tolerance
                                    box('c4', 'dent', 95, 70, 115, 95)])         # extra
    return project, a, b


def test_known_disagreements_give_stable_placed_conflicts(tmp_path):
    project, a, b = _detection(tmp_path)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE,
                                gold_images=[a, b], tolerance=0.5)
    first = aq.run_report(project, profile['profile_id'])
    assert first['counts'] == {'missing': 1, 'extra': 1, 'class': 1, 'geometry': 1, 'not_comparable': 0}
    by_image = {image['relative_path']: image for image in first['images']}
    kinds = {conflict['kind']: conflict for conflict in by_image['train/a.png']['conflicts']}
    assert (kinds['class']['reference_object'], kinds['class']['candidate_object'], kinds['class']['candidate_label']) == ('r2', 'c2', 'scratch')  # freeze 3: unique ids as they are
    assert kinds['class']['bbox'] == [60, 10, 90, 40] and kinds['class']['candidate_bbox'] == [60, 10, 90, 40], 'both objects are placed'
    assert kinds['geometry']['reference_object'] == 'r3' and 0.1 <= kinds['geometry']['overlap'] < 0.5
    assert kinds['extra']['candidate_object'] == 'c4' and kinds['extra']['bbox'] == [95, 70, 115, 95]
    assert by_image['train/b.png']['labeled'] is False and by_image['train/b.png']['conflicts'][0]['kind'] == 'missing'
    assert by_image['train/a.png']['image_path'] == a, 'every conflict links to its image'
    second = aq.run_report(project, profile['profile_id'])
    ids = lambda report: [c['conflict_id'] for image in report['images'] for c in image['conflicts']]
    assert ids(first) == ids(second) and first['report_id'] != second['report_id'], 'the same disagreement keeps its id'
    reopened = aq.read_report(project, first['report_id'])
    assert reopened['current'] is True and reopened['approval_eligible'] is False and reopened['counts'] == first['counts'], \
        'a current report with conflicts does not support an approval'
    listed = aq.list_reports(project, profile['profile_id'])
    assert {row['report_id'] for row in listed} == {first['report_id'], second['report_id']}
    assert all(row['counts'] == first['counts'] and row['images'] == 2 for row in listed)
    assert aq.list_reports(project, '0' * 32) == []


def test_segmentation_compares_the_regions_of_each_class(tmp_path):
    project = _project(tmp_path, 'segmentation')
    image = str(Path(project['source_dataset_dir']) / 'train' / 'c.png')
    reference = np.zeros((100, 120), np.uint8)
    reference[10:30, 10:30] = 1
    reference[10:30, 60:80] = 1
    reference[60:90, 10:40] = 2
    candidate = np.zeros((100, 120), np.uint8)
    candidate[10:30, 10:30] = 1        # agrees
    candidate[60:90, 10:40] = 1        # class 1 where the reference has class 2
    candidate[70:90, 80:110] = 2       # extra region
    names = [{'label': 'crack', 'category_id': 1, 'type': 'brush_mask'}, {'label': 'stain', 'category_id': 2, 'type': 'brush_mask'}]
    _labels(project, 'default', image, names, reference)
    _labels(project, CANDIDATE, image, names, candidate)
    _approve(project, 'default', ['train/c.png'])
    profile = aq.create_profile(project, task='segmentation', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[image])
    report = aq.run_report(project, profile['profile_id'])
    assert report['counts'] == {'missing': 1, 'extra': 1, 'class': 1, 'geometry': 0, 'not_comparable': 0}
    [conflicts] = [image['conflicts'] for image in report['images']]
    assert {(c['kind'], c.get('label') or c.get('reference_label')) for c in conflicts} == {('class', 'stain'), ('extra', 'stain'), ('missing', 'crack')}


def test_a_changed_gold_label_or_guideline_stales_the_report_and_its_approval(tmp_path):
    project, a, b = _detection(tmp_path)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b])
    report = aq.run_report(project, profile['profile_id'])
    _labels(project, 'default', b, [box('r4', 'scratch', 20, 20, 61, 60)])
    stale = aq.read_report(project, report['report_id'])
    assert stale['stale'] is True and stale['approval_eligible'] is False and stale['stale_reasons'] == ['gold_label_changed:train/b.png']
    with pytest.raises(ValueError, match='gold set changed'):
        aq.run_report(project, profile['profile_id'])
    _labels(project, 'default', b, [box('r4', 'scratch', 20, 20, 60, 60)])
    assert aq.read_report(project, report['report_id'])['stale'] is False, 'the same labels again'
    _approve(project, 'default', ['train/a.png', 'train/b.png'], {**BOOK, 'version': 2, 'sha256': 'b' * 64})
    assert aq.read_report(project, report['report_id'])['stale_reasons'] == ['guideline_changed']


def test_gold_images_leave_training_and_test_use_unless_the_policy_keeps_them(tmp_path):
    from backend.engine.annotation_storage import reset_request_project_root, set_request_project_root
    from backend.engine.dataset_usage import unused_image_paths
    project, a, b = _detection(tmp_path)
    (Path(project['project_dir']) / 'project.json').write_text(json.dumps({'source_dataset_dir': project['source_dataset_dir']}))
    token = set_request_project_root(Path(project['project_dir']))
    try:
        assert unused_image_paths() == set()
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b])
        assert unused_image_paths() == {str(Path(a).resolve()), str(Path(b).resolve())}
        aq.set_gold_policy(project, True)
        assert unused_image_paths() == set()
    finally:
        reset_request_project_root(token)


def test_unsupported_tasks_shapes_and_unapproved_or_changed_records_are_refused(tmp_path):
    project, a, b = _detection(tmp_path)
    for kwargs, message in (({'task': 'classification'}, 'not compared'), ({'task': 'segmentation'}, "project's task is detection"),
                            ({'candidate_labelset': 'ls_ffffffffffff'}, 'does not exist'), ({'candidate_labelset': 'default'}, 'must differ'),
                            ({'tolerance': 0.05}, 'tolerance'), ({'gold_images': [str(Path(a).parent / 'c.png')]}, 'not approved')):
        values = {'task': 'detection', 'reference_labelset': 'default', 'candidate_labelset': CANDIDATE, 'gold_images': [a], **kwargs}
        with pytest.raises(ValueError, match=message):
            aq.create_profile(project, **values)
    _labels(project, 'default', b, [{'id': 'r9', 'label': 'scratch', 'type': 'brush_mask', 'mask_rle': 'data:'}])
    _approve(project, 'default', ['train/a.png', 'train/b.png'], BOOK)
    with pytest.raises(ValueError, match='brush_mask'):
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[b])
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    report = aq.run_report(project, profile['profile_id'])
    path = Path(project['project_dir']) / 'annotation_quality' / 'reports' / f"{report['report_id']}.json"
    changed = json.loads(path.read_text(encoding='utf-8'))
    changed['counts']['missing'] = 5
    path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match='changed after it was made'):
        aq.read_report(project, report['report_id'])
    with pytest.raises(ValueError):
        aq.read_report(project, '../../etc')


def test_the_review_is_reachable_through_the_team_data_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'QA', 'project_dir': str(tmp_path / 'project'), 'task': 'detection'}).status_code == 200
    source = tmp_path / 'source'
    (source / 'train').mkdir(parents=True)
    image = source / 'train' / 'a.png'
    cv2.imwrite(str(image), np.zeros((100, 120, 3), np.uint8))
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    project = client.get('/api/project/current').json()
    from backend.engine.project_labelsets import create_labelset
    candidate = create_labelset(Path(project['project_dir']), 'labeler')['id']
    _labels(project, 'default', str(image), [box('r1', 'scratch', 10, 10, 40, 40)])
    _approve(project, 'default', ['train/a.png'])
    made = client.post('/api/team-data/quality/profiles', json={'task': 'detection', 'reference_labelset': 'default',
                                                                 'candidate_labelset': candidate, 'gold_images': [str(image)]})
    assert made.status_code == 200, made.text
    report = client.post(f"/api/team-data/quality/profiles/{made.json()['profile_id']}/reports")
    assert report.status_code == 200 and report.json()['counts']['missing'] == 1
    reread = client.get(f"/api/team-data/quality/reports/{report.json()['report_id']}").json()
    assert reread['current'] is True and reread['approval_eligible'] is False, 'a report with a missing object does not pass'
    assert [row['report_id'] for row in client.get('/api/team-data/quality/reports').json()['reports']] == [report.json()['report_id']]
    assert client.get('/api/team-data/quality/reports/' + '0' * 32).status_code == 404
    assert client.post('/api/team-data/quality/profiles', json={'task': 'ocr', 'reference_labelset': 'default',
                                                                'candidate_labelset': candidate, 'gold_images': [str(image)]}).status_code == 422
    policy = client.put('/api/team-data/quality/gold-policy', json={'include_gold_in_training': True}).json()
    assert policy['include_gold_in_training'] is True and policy['changed_by'] and policy['changed_at']  # freeze 3: who and when


def test_a_label_saved_through_another_spelling_of_the_dataset_folder_is_recorded(tmp_path, monkeypatch):
    """Found by the E05 browser check: the team review approves what the save recorded, and a save through a symlinked
    spelling of the dataset folder (macOS /var for /private/var, a mapped drive) failed after writing the labels."""
    import os
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend.engine import dataset_metadata as metadata
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'QA', 'project_dir': str(tmp_path / 'project'), 'task': 'detection'}).status_code == 200
    source = tmp_path / 'source'
    (source / 'train').mkdir(parents=True)
    cv2.imwrite(str(source / 'train' / 'a.png'), np.zeros((100, 120, 3), np.uint8))
    alias = tmp_path / 'alias'
    try:
        os.symlink(source, alias, target_is_directory=True)
    except OSError:
        pytest.skip('this account cannot create a directory symlink')
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    saved = client.post('/api/annotations/save', json={'image_id': 'a', 'image_path': str(alias / 'train' / 'a.png'), 'actor': 'Lee',
                                                       'annotations': [box('r1', 'scratch', 10, 10, 40, 40)], 'image_width': 120, 'image_height': 100})
    assert saved.status_code == 200, saved.text
    project = client.get('/api/project/current').json()
    image = source.resolve() / 'train' / 'a.png'
    assert saved.json()['metadata']['relative_path'] == 'train/a.png'
    expected = metadata._annotation_hash(Path(project['project_dir']), source.resolve(), image)
    assert expected is not None
    ledger = json.loads(metadata.ledger_path(Path(project['project_dir']), source.resolve(), labelset_root(Path(project['project_dir']), 'default')).read_text(encoding='utf-8'))
    assert ledger['images']['train/a.png']['annotation_hash'] == expected


# ------------------------------------------------------------------------------------- freeze 2 (review e05s1)

def test_a_gold_image_never_becomes_held_out_test_truth(workspace):
    """Review P1-1: freeze_cohort took every saved test image, gold included, with its declared truth."""
    from backend.tests.test_whole_flow_evaluation import declare, model_provider
    project, _graph, version, checkpoint = workspace
    _gold_workspace_extras(project)
    from backend.engine import flow_evaluation, image_truth
    declare(image_truth, project, 'defect', 'NG', classes=['crack'])
    source = Path(project['source_dataset_dir'])
    _labels(project, 'default', str(source / 'defect.png'), [], mask=_crack_mask())
    _approve(project, 'default', ['defect.png'])
    aq.create_profile(project, task='segmentation', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[str(source / 'defect.png')])
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    assert 'defect.png' not in [row['relative_path'] for row in cohort['samples']], 'gold is not test truth'
    with pytest.raises(ValueError, match='gold sample'):
        flow_evaluation.freeze_cohort(project, version, relative_paths=['defect.png'], model_provider=model_provider(checkpoint))
    aq.set_gold_policy(project, True)
    kept = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    assert 'defect.png' in [row['relative_path'] for row in kept['samples']], 'the dataset policy can keep gold'
    aq.set_gold_policy(project, False)
    assert 'gold_image:defect.png' in flow_evaluation._cohort_changes(project, kept), 'a cohort holding a gold image is stale'


def _gold_workspace_extras(project):
    project_dir = Path(project['project_dir'])
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / 'labelsets.json').write_text(json.dumps({'schema_version': 1, 'active_id': 'default', 'labelsets': [
        {'id': 'default', 'name': 'default', 'source_id': None, 'created_at': ''},
        {'id': CANDIDATE, 'name': 'labeler', 'source_id': 'default', 'created_at': ''}]}))


def _crack_mask():
    mask = np.zeros((32, 32), np.uint8)
    mask[4:12, 4:12] = 1
    return mask


def test_tags_and_segmentation_objects_without_a_mask_are_refused_not_counted_as_agreement(tmp_path):
    """Review P1-2: a classification tag OK against NG compared as full agreement."""
    project = _project(tmp_path, 'classification')
    a = str(Path(project['source_dataset_dir']) / 'train' / 'a.png')
    _labels(project, 'default', a, [{'id': 't1', 'label': 'NG', 'type': 'tag'}])
    _approve(project, 'default', ['train/a.png'])
    with pytest.raises(ValueError, match="project's task is classification"):
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    project['task'] = 'detection'
    with pytest.raises(ValueError, match="image tag 'NG'"):  # freeze 3: only a normal mark is an image without objects
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    with pytest.raises(ValueError, match="image tag 'NG'"):
        aq.compare_image('detection', {'annotations': [box('r', 'scratch', 1, 1, 9, 9)], 'mask_png': None},
                         {'annotations': [{'id': 't', 'label': 'NG', 'type': 'tag'}], 'mask_png': None}, 0.5)
    seg = _project(tmp_path / 'seg', 'segmentation')
    c = str(Path(seg['source_dataset_dir']) / 'train' / 'c.png')
    _labels(seg, 'default', c, [box('r1', 'scratch', 10, 10, 40, 40)])
    _approve(seg, 'default', ['train/c.png'])
    with pytest.raises(ValueError, match='without a label mask'):
        aq.create_profile(seg, task='segmentation', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[c])


def test_only_a_current_passing_report_of_unchanged_labels_supports_an_approval(tmp_path):
    """Review P2-1: approval_eligible meant only 'not stale'."""
    project = _project(tmp_path)
    a = str(Path(project['source_dataset_dir']) / 'train' / 'a.png')
    _labels(project, 'default', a, [box('r1', 'scratch', 10, 10, 40, 40)])
    _approve(project, 'default', ['train/a.png'])
    _labels(project, CANDIDATE, a, [box('c1', 'scratch', 10, 10, 40, 41)])
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    report = aq.run_report(project, profile['profile_id'])
    assert report['passes'] is True
    assert aq.read_report(project, report['report_id'])['approval_eligible'] is True
    _labels(project, CANDIDATE, a, [box('c1', 'scratch', 10, 10, 40, 41), box('c2', 'dent', 60, 60, 70, 70)])
    changed = aq.read_report(project, report['report_id'])
    assert changed['current'] is True and changed['candidate_changes'] == ['candidate_changed:train/a.png'] and changed['approval_eligible'] is False
    failing = aq.run_report(project, profile['profile_id'])
    assert failing['passes'] is False and aq.read_report(project, failing['report_id'])['approval_eligible'] is False
    unlabeled = _project(tmp_path / 'u')
    b = str(Path(unlabeled['source_dataset_dir']) / 'train' / 'b.png')
    _labels(unlabeled, 'default', b, [box('r1', 'scratch', 10, 10, 40, 40)])
    _approve(unlabeled, 'default', ['train/b.png'])
    empty = aq.run_report(unlabeled, aq.create_profile(unlabeled, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE,
                                                       gold_images=[b])['profile_id'])
    assert empty['agreeing_images'] == 0 and empty['passes'] is False, 'an unlabeled image never agrees'


def test_gold_is_bound_to_the_labels_its_approval_was_given_to(tmp_path):
    """Review P2-2: labels changed after approval were taken as gold; a withdrawn approval did not stale."""
    project, a, b = _detection(tmp_path)
    _labels(project, 'default', b, [box('r4', 'scratch', 20, 20, 61, 60)])  # changed after approval, ledger not rechecked
    with pytest.raises(ValueError, match='changed after they were approved'):
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b])
    _approve(project, 'default', ['train/a.png', 'train/b.png'], BOOK)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b])
    report = aq.run_report(project, profile['profile_id'])
    ledger_path = dm.ledger_path(Path(project['project_dir']), Path(project['source_dataset_dir']), labelset_root(Path(project['project_dir']), 'default'))
    ledger = json.loads(ledger_path.read_text(encoding='utf-8'))
    ledger['images']['train/a.png']['workflow_state'] = 'needs_review'
    ledger_path.write_text(json.dumps(ledger))
    assert 'gold_unapproved:train/a.png' in aq.read_report(project, report['report_id'])['stale_reasons']
    with pytest.raises(ValueError, match='gold set changed'):
        aq.run_report(project, profile['profile_id'])


def test_small_objects_are_compared_with_an_exact_overlap(tmp_path):
    """Review P2-4: the whole-pixel raster called 0.38-0.48 overlaps agreement for 8-16 px objects."""
    reference = {'annotations': [box('r', 'scratch', 10, 10, 18, 18)], 'mask_png': None}
    for candidate_box, kind in (([10, 10, 18, 13.6], 'geometry'), ([10, 10, 18, 14.4], None), ([11.2, 9.77, 15.64, 15.52], 'geometry')):
        conflicts = aq.compare_image('detection', reference, {'annotations': [box('c', 'scratch', *candidate_box)], 'mask_png': None}, 0.5)
        assert [c['kind'] for c in conflicts] == ([kind] if kind else []), (candidate_box, conflicts)
    shape = [[0, 0], [20, 0], [20, 10], [10, 10], [10, 20], [0, 20]]  # an L (concave)
    moved = [[x + 1, y] for x, y in shape]
    overlap = aq._overlap(*[{'polygon': np.array(p, np.float64), 'bbox': aq._polygon_bbox(np.array(p, np.float64))} for p in (shape, moved)])
    assert overlap == pytest.approx(280 / 320, abs=0.005), 'a concave polygon is rasterised at 1/8 px'


def test_a_stray_or_damaged_profile_file_is_named_and_a_profile_can_be_retired(tmp_path):
    """Review P2-6 and P3-8."""
    project, a, b = _detection(tmp_path)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    folder = Path(project['project_dir']) / 'annotation_quality' / 'profiles'
    (folder / 'copy of profile.json').write_text('{}')
    assert [row['profile_id'] for row in aq.list_profiles(project)] == [profile['profile_id']], 'only files named by a profile id are read'
    damaged = folder / ('f' * 32 + '.json')
    damaged.write_text('{"truncated')
    with pytest.raises(ValueError, match='f' * 32 + '.json cannot be read'):
        aq.gold_image_paths(project)
    damaged.unlink()
    assert aq.gold_image_paths(project) == {str(Path(a).resolve())}
    report = aq.run_report(project, profile['profile_id'])
    aq.retire_profile(project, profile['profile_id'], 'Lee')
    assert aq.gold_image_paths(project) == set() and aq.list_profiles(project) == []
    assert aq.list_profiles(project, include_retired=True)[0]['retired']['actor'] == 'Lee'
    assert aq.read_report(project, report['report_id'])['stale_reasons'] == ['profile_retired']
    with pytest.raises(ValueError, match='gold set changed'):
        aq.run_report(project, profile['profile_id'])
    with pytest.raises(FileNotFoundError):
        aq.read_profile(project, '0' * 32)


def test_gold_leaves_training_through_a_usage_ledger_and_the_training_receipt(tmp_path):
    """Review P2-3 and the surviving dataset_usage mutant: every real project has a usage ledger."""
    from backend.engine.annotation_storage import (reset_request_annotation_root, reset_request_project_root, set_request_annotation_root,
                                                   set_request_project_root)
    from backend.engine.dataset_usage import unused_image_paths
    project, a, b = _detection(tmp_path)
    (Path(project['project_dir']) / 'project.json').write_text(json.dumps({'source_dataset_dir': project['source_dataset_dir']}))
    annotations = Path(project['project_dir']) / 'annotations'
    tokens = (set_request_project_root(Path(project['project_dir'])), set_request_annotation_root(annotations))
    try:
        ledger = dataset_annotation_dir(Path(project['source_dataset_dir'])) / 'metadata' / 'workflow.json'
        ledger.parent.mkdir(parents=True, exist_ok=True)
        rows = json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {'images': {}}  # the label ledger is the usage ledger
        rows['images']['train/c.png'] = {**rows['images'].get('train/c.png', {}), 'usage_state': 'not_used'}
        ledger.write_text(json.dumps(rows))
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
        source = Path(project['source_dataset_dir'])
        assert unused_image_paths() == {str((source / 'train' / 'c.png').resolve()), str(Path(a).resolve())}
        receipt = aq.gold_receipt(project)
        assert receipt['gold_images'] == 1 and receipt['include_gold_in_training'] is False
        aq.set_gold_policy(project, True)
        assert aq.gold_receipt(project)['include_gold_in_training'] is True and unused_image_paths() == {str((source / 'train' / 'c.png').resolve())}
    finally:
        reset_request_annotation_root(tokens[1])
        reset_request_project_root(tokens[0])


def test_review_details_are_unique_placed_and_checked(tmp_path):
    """Review P3s: duplicate ids, regions touching edges, masks of different sizes, another source, tampered summaries,
    a guideline appended by publishing, a deleted gold label and a changed mask."""
    reference = {'annotations': [box('dup', 'scratch', 10, 10, 20, 20), box('dup', 'scratch', 50, 50, 60, 60)], 'mask_png': None}
    missing = aq.compare_image('detection', reference, {'annotations': [], 'mask_png': None}, 0.5)
    assert len({c['reference_object'] for c in missing}) == 2
    edges = np.zeros((20, 20), np.uint8)
    edges[0:5, 0:2] = 1   # touches the left edge
    edges[0:2, 6:12] = 1  # touches the top edge, its own region starting on row 0
    regions = aq._shapes('segmentation', {'annotations': [], 'mask_png': cv2.imencode('.png', edges)[1].tobytes()})
    assert len({row['key'] for row in regions}) == 2
    other = {'annotations': [], 'mask_png': cv2.imencode('.png', np.zeros((10, 10), np.uint8) + 1)[1].tobytes()}
    with pytest.raises(ValueError, match=r"x.png \(candidate\): the label mask is"):
        aq.compare_image('segmentation', {'annotations': [], 'mask_png': cv2.imencode('.png', edges)[1].tobytes()}, other, 0.5, 'x.png')
    project, a, b = _detection(tmp_path)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b])
    report = aq.run_report(project, profile['profile_id'])
    path = Path(project['project_dir']) / 'annotation_quality' / 'reports' / f"{report['report_id']}.json"
    tampered = json.loads(path.read_text(encoding='utf-8'))
    tampered['counts']['missing'] = 0
    path.write_text(json.dumps(tampered))
    [row] = aq.list_reports(project)
    assert row['counts'] is None and 'changed after it was made' in row['integrity_error']
    _approve(project, 'default', ['train/a.png', 'train/b.png'], books=[BOOK, {**BOOK, 'id': 'book_2', 'version': 2, 'sha256': 'c' * 64}])
    fresh = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    _approve(project, 'default', ['train/a.png', 'train/b.png'], books=[BOOK, {**BOOK, 'id': 'book_2', 'version': 2, 'sha256': 'c' * 64},
                                                                         {**BOOK, 'id': 'book_3', 'version': 3, 'sha256': 'd' * 64}])
    assert 'guideline_changed' in aq.staleness(project, aq.read_profile(project, fresh['profile_id'])), 'an appended book is a new guideline'
    elsewhere = {**project, 'source_dataset_dir': str(tmp_path / 'elsewhere')}
    (tmp_path / 'elsewhere').mkdir()
    assert aq.gold_set(elsewhere) == set(), 'a profile of another source makes no gold here'
    seg = _project(tmp_path / 'seg', 'segmentation')
    c = str(Path(seg['source_dataset_dir']) / 'train' / 'c.png')
    names = [{'label': 'crack', 'category_id': 1, 'type': 'brush_mask'}]
    mask = np.zeros((100, 120), np.uint8)
    mask[10:30, 10:30] = 1
    _labels(seg, 'default', c, names, mask)
    _approve(seg, 'default', ['train/c.png'])
    seg_profile = aq.create_profile(seg, task='segmentation', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[c])
    mask[40:50, 40:50] = 1
    _labels(seg, 'default', c, names, mask)
    assert 'gold_label_changed:train/c.png' in aq.staleness(seg, aq.read_profile(seg, seg_profile['profile_id'])), 'a mask change stales'
    folder = dataset_annotation_dir(Path(c).parent, labelset_root(Path(seg['project_dir']), 'default'), use_scope=False)
    (folder / 'c.json').unlink()
    assert 'gold_label_changed:train/c.png' in aq.staleness(seg, aq.read_profile(seg, seg_profile['profile_id'])), 'a deleted label stales'


def test_shared_reviewers_run_the_label_review_and_labelers_cannot(tmp_path, monkeypatch):
    """Review P2-7: every quality write answered 403 to reviewers in shared mode although the screen offered it."""
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from PIL import Image
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = create_app(str(tmp_path / 'registry'), shared_auth_dir=str(tmp_path / 'accounts'))
    store = app.state.accounts
    admin = store.bootstrap('owner', 'long password 123')
    users = {name: store.create_user(name, 'long password 456') for name in ('labeler', 'reviewer')}

    def client(name, password='long password 456'):
        return TestClient(app, headers={'Authorization': 'Bearer ' + store.login(name, password)['token']})
    owner = client('owner', 'long password 123')
    project = owner.post('/api/project/create', json={'name': 'Shared review', 'task': 'detection'}).json()
    source = tmp_path / 'shared-source'
    (source / 'train').mkdir(parents=True)
    Image.new('RGB', (120, 100)).save(source / 'train' / 'a.png')
    assert owner.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200
    for name in ('labeler', 'reviewer'):
        store.set_membership(project['id'], users[name]['id'], name, admin['id'])
        store.select_project(users[name]['id'], project['id'])
    project = owner.get('/api/project/current').json()
    from backend.engine.project_labelsets import create_labelset
    candidate = create_labelset(Path(project['project_dir']), 'labeler')['id']
    image = str(source / 'train' / 'a.png')
    _labels(project, 'default', image, [box('r1', 'scratch', 10, 10, 40, 40)])
    _approve(project, 'default', ['train/a.png'])
    body = {'task': 'detection', 'reference_labelset': 'default', 'candidate_labelset': candidate, 'gold_images': [image], 'actor': 'x'}
    labeler, reviewer = client('labeler'), client('reviewer')
    assert labeler.post('/api/team-data/quality/profiles', json=body).status_code == 403
    made = reviewer.post('/api/team-data/quality/profiles', json=body)
    assert made.status_code == 200, made.text
    assert made.json()['actor'] == 'reviewer', 'the session names the actor'
    profile_id = made.json()['profile_id']
    assert labeler.post(f'/api/team-data/quality/profiles/{profile_id}/reports').status_code == 403
    assert reviewer.post(f'/api/team-data/quality/profiles/{profile_id}/reports').status_code == 200
    assert labeler.put('/api/team-data/quality/gold-policy', json={'include_gold_in_training': True}).status_code == 403
    assert reviewer.put('/api/team-data/quality/gold-policy', json={'include_gold_in_training': False}).status_code == 200
    retired = reviewer.post(f'/api/team-data/quality/profiles/{profile_id}/retire', json={'actor': 'x'})
    assert retired.status_code == 200 and retired.json()['actor'] == 'reviewer'


# ------------------------------------------------------------------------------------------------ freeze 3

NORMAL = {'id': 'n', 'type': 'tag', 'label': 'OK', 'is_normal': True, 'category_id': 0}  # the labeling view's "Mark as Normal (OK)"


def test_a_normal_mark_is_an_image_without_objects_and_a_labeler_error_names_its_image(tmp_path):
    """Review e05s2 P1-1: the labeling view's normal mark was refused, and one labeler tag stopped the whole report."""
    project = _project(tmp_path)
    source = Path(project['source_dataset_dir'])
    a, b, c = (str(source / 'train' / f'{name}.png') for name in 'abc')
    _labels(project, 'default', a, [NORMAL])                                    # a gold negative
    _labels(project, 'default', b, [box('r1', 'scratch', 10, 10, 40, 40)])
    _labels(project, 'default', c, [box('r2', 'scratch', 10, 10, 40, 40)])
    _approve(project, 'default', ['train/a.png', 'train/b.png', 'train/c.png'])
    _labels(project, CANDIDATE, a, [box('c1', 'scratch', 5, 5, 20, 20)])         # an extra box on a defect-free part
    _labels(project, CANDIDATE, b, [NORMAL])                                      # marked OK: the scratch is missed
    _labels(project, CANDIDATE, c, [{'id': 't', 'label': 'NG', 'type': 'tag'}])   # a tag the review cannot compare
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a, b, c])
    report = aq.run_report(project, profile['profile_id'])
    rows = {row['relative_path']: row for row in report['images']}
    assert [conflict['kind'] for conflict in rows['train/a.png']['conflicts']] == ['extra']
    assert [conflict['kind'] for conflict in rows['train/b.png']['conflicts']] == ['missing'], 'an OK on a defective part is the miss'
    assert rows['train/c.png']['conflicts'] == [] and 'NG' in rows['train/c.png']['error'] and 'candidate' in rows['train/c.png']['error']
    assert report['counts']['not_comparable'] == 1 and report['passes'] is False
    _labels(project, CANDIDATE, a, [NORMAL, box('c1', 'scratch', 5, 5, 20, 20)])
    rows = {row['relative_path']: row for row in aq.run_report(project, profile['profile_id'])['images']}
    assert 'normal mark' in rows['train/a.png']['error'], 'a normal mark beside an object is a contradiction, not a negative'
    _labels(project, 'default', c, [{'id': 't', 'label': 'NG', 'type': 'tag'}])
    _approve(project, 'default', ['train/c.png'])
    with pytest.raises(ValueError, match=r'train/c\.png.*reference'):
        aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[c])
    seg = _project(tmp_path / 'seg', 'segmentation')
    d = str(Path(seg['source_dataset_dir']) / 'train' / 'a.png')
    _labels(seg, 'default', d, [NORMAL])
    _approve(seg, 'default', ['train/a.png'])
    mask = np.zeros((100, 120), np.uint8)
    mask[10:30, 10:30] = 1
    _labels(seg, CANDIDATE, d, [{'id': 'm', 'label': 'crack', 'type': 'brush_mask', 'category_id': 1}], mask)
    seg_profile = aq.create_profile(seg, task='segmentation', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[d])
    assert [conflict['kind'] for conflict in aq.run_report(seg, seg_profile['profile_id'])['images'][0]['conflicts']] == ['extra']


def test_conflict_ids_survive_an_unrelated_edit_of_the_labeler(tmp_path):
    """Review e05s2 P2-1: ids came from list positions, so deleting an unrelated box renamed the remaining conflicts."""
    project, a, _b = _detection(tmp_path)
    profile = aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    ids = lambda: {conflict['kind']: conflict['conflict_id'] for conflict in aq.run_report(project, profile['profile_id'])['images'][0]['conflicts']
                   if conflict['kind'] in ('class', 'geometry')}
    before = ids()
    _labels(project, CANDIDATE, a, [box('c2', 'scratch', 60, 10, 90, 40), box('c3', 'scratch', 25, 60, 65, 90), box('c4', 'dent', 95, 70, 115, 95)])
    assert ids() == before, 'the agreeing box c1 was deleted: the class and geometry conflicts keep their ids'
    keys = [shape['key'] for shape in aq._shapes('detection', {'annotations': [box('x', 'a', 1, 1, 5, 5), box('x', 'a', 10, 10, 15, 15),
                                                                                 box('', 'a', 20, 20, 25, 25), box('y', 'a', 30, 30, 35, 35)], 'mask_png': None})]
    assert keys == ['x#1', 'x#2', '#3', 'y'], 'a unique id as is; repeated ids by occurrence; no id by position'


def test_a_backup_restored_elsewhere_keeps_its_profiles_reports_and_gold_exclusion(tmp_path, monkeypatch):
    """Review e05s2 P1-2: the restore rewrote paths inside the hashed profile and report bodies."""
    from fastapi.testclient import TestClient
    from backend.engine.annotation_storage import reset_request_project_root, set_request_project_root
    from backend.engine.dataset_usage import unused_image_paths
    from backend.main import create_app
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user_data'))
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert client.post('/api/project/create', json={'name': 'QA', 'project_dir': str(tmp_path / 'project'), 'task': 'detection'}).status_code == 200
    source = tmp_path / 'source'
    (source / 'train').mkdir(parents=True)
    for name in ('a', 'b'):
        cv2.imwrite(str(source / 'train' / f'{name}.png'), np.zeros((100, 120, 3), np.uint8))
    assert client.put('/api/project/update', json={'source_dataset_dir': str(source)}).status_code == 200

    def save(name, annotations):
        response = client.post('/api/annotations/save', json={'image_id': name, 'image_path': str(source / 'train' / f'{name}.png'),
                                                               'actor': 'Lee', 'annotations': annotations, 'image_width': 120, 'image_height': 100})
        assert response.status_code == 200, response.text
        return response.json()['metadata']
    meta = save('a', [box('r1', 'scratch', 10, 10, 40, 40)])
    save('b', [box('r2', 'scratch', 10, 10, 40, 40)])
    assert client.patch('/api/dataset/metadata/' + meta['image_uuid'], json={'expected_revision': meta['revision'], 'actor': 'Kim',
                                                                              'changes': {'workflow_state': 'approved'}}).status_code == 200
    labelset = client.post('/api/project/labelsets', json={'name': 'Labeler B'}).json()
    client.put(f"/api/project/labelsets/{labelset['id']}/activate")
    save('a', [box('c1', 'dent', 10, 10, 40, 40)])
    client.put('/api/project/labelsets/default/activate')
    made = client.post('/api/team-data/quality/profiles', json={'task': 'detection', 'reference_labelset': 'default',
                                                                 'candidate_labelset': labelset['id'], 'gold_images': [str(source / 'train' / 'a.png')]})
    assert made.status_code == 200, made.text
    report = client.post(f"/api/team-data/quality/profiles/{made.json()['profile_id']}/reports").json()
    before = client.get('/api/team-data/readiness').json()['counts']
    assert (before['gold'], before['approved'], before['eligible']) == (1, 0, 1), ('review e05s2 P2-3: readiness counts the gold image as '
        'gold, not approved, and only the other image is eligible', before)
    backup = client.post('/api/project/backup', json={'destination_dir': str(tmp_path / 'backup')})
    assert backup.status_code == 200 and backup.json()['source_included'] is True
    restored = client.post('/api/project/restore', json={'archive_path': backup.json()['archive_path'], 'target_dir': str(tmp_path / 'restored')})
    assert restored.status_code == 200, restored.text
    current = client.get('/api/project/current').json()
    assert Path(current['source_dataset_dir']).resolve() != source.resolve(), 'the source was restored beside the project'
    profiles = client.get('/api/team-data/quality/profiles')
    assert profiles.status_code == 200, profiles.text
    assert [row['profile_id'] for row in profiles.json()['profiles']] == [made.json()['profile_id']]
    assert Path(profiles.json()['profiles'][0]['scope']['source_dataset_path']).resolve() == Path(current['source_dataset_dir']).resolve()
    readiness = client.get('/api/team-data/readiness')
    assert readiness.status_code == 200 and readiness.json()['counts']['gold'] == 1, readiness.text
    reread = client.get(f"/api/team-data/quality/reports/{report['report_id']}")
    assert reread.status_code == 200 and reread.json()['current'] is True, reread.text
    token = set_request_project_root(Path(current['project_dir']))
    try:
        assert {Path(path).name for path in unused_image_paths()} == {'a.png'}, 'the restored gold image stays out of training'
    finally:
        reset_request_project_root(token)


def test_a_training_version_bound_before_gold_images_still_rebinds_while_there_are_none(tmp_path):
    """Review e05s2 P2-2: the receipt gained 'gold', so every version bound before it was refused."""
    from backend.tests.test_team_data_api import client_workspace  # noqa: F401
    from fastapi.testclient import TestClient
    from PIL import Image
    from backend.engine.training_provenance import bind_training_version, validate_training_binding
    from backend.main import create_app
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    client.post('/api/project/create', json={'name': 'Receipt', 'task': 'classification'})
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (40, 30), 'white').save(source / 'a.png')
    project = client.put('/api/project/update', json={'source_dataset_dir': str(source)}).json()
    binding = bind_training_version(project, source)
    receipt = Path(binding['version_dir']) / 'team-data.json'
    old = {key: value for key, value in json.loads(receipt.read_text(encoding='utf-8')).items() if key != 'gold'}
    receipt.write_text(json.dumps(old))  # as written before E05
    again = bind_training_version(project, source, binding['dataset_version_id'])
    assert again['dataset_version_id'] == binding['dataset_version_id'] and json.loads(receipt.read_text(encoding='utf-8')) == old
    assert again['team_data'] == old, 'returned receipt retains the actual frozen legacy body'
    validate_training_binding(again)
    real = aq.gold_receipt  # team_data reads it from annotation_quality when it binds
    aq.gold_receipt = lambda scope: {**real(scope), 'gold_images': 1}
    try:
        with pytest.raises(ValueError, match='Team-data policy differs'):
            bind_training_version(project, source, binding['dataset_version_id'])
    finally:
        aq.gold_receipt = real


def test_segmentation_geometry_shared_corners_and_the_object_cap(tmp_path):
    """Review e05s2 P2-3: a segmentation overlap under the tolerance, two regions sharing a box corner, the cap."""
    reference, candidate = np.zeros((100, 120), np.uint8), np.zeros((100, 120), np.uint8)
    reference[10:50, 10:50] = 1
    candidate[30:70, 30:70] = 1                       # the same region shifted: overlap 400/2800, about 0.14
    reference[60:70, 80:90] = 2
    reference[60:62, 100:110] = 2                     # a second region of class 2 ...
    reference[60:70, 100:101] = 2                     # ... whose box starts at the same corner as a third shape is avoided below
    names = [{'label': 'crack', 'category_id': 1}, {'label': 'dent', 'category_id': 2}]
    encode = lambda mask: cv2.imencode('.png', mask)[1].tobytes()
    conflicts = aq.compare_image('segmentation', {'annotations': names, 'mask_png': encode(reference)},
                                 {'annotations': names, 'mask_png': encode(candidate)}, 0.5)
    geometry = [conflict for conflict in conflicts if conflict['kind'] == 'geometry']
    assert len(geometry) == 1 and 0.1 < geometry[0]['overlap'] < 0.5
    corner = np.zeros((100, 120), np.uint8)
    corner[10:12, 10:40] = 1                           # a bar from (10, 10)
    corner[14:40, 10:12] = 1                           # a separate bar starting in the same column
    regions = aq._shapes('segmentation', {'annotations': names, 'mask_png': encode(corner)})
    assert len(regions) == 2 and len({region['key'] for region in regions}) == 2
    with pytest.raises(ValueError, match='at most 1000'):
        aq._shapes('detection', {'annotations': [box(str(i), 'a', 1, 1, 5, 5) for i in range(1001)], 'mask_png': None})


def test_a_gold_policy_change_keeps_who_and_when_and_a_damaged_store_file_names_itself(tmp_path):
    """Review e05s2 P3-5 and P3-2."""
    project, a, _b = _detection(tmp_path)
    policy = aq.set_gold_policy(project, True, 'Kim')
    assert policy['changed_by'] == 'Kim' and policy['changed_at'] and aq.gold_policy(project)['changed_by'] == 'Kim'
    assert aq.gold_receipt(project)['include_gold_in_training'] is True
    (aq._store(project) / 'policy.json').write_text('{not json')
    with pytest.raises(ValueError, match='policy.json'):
        aq.gold_policy(project)
    (aq._store(project) / 'policy.json').unlink()
    aq.create_profile(project, task='detection', reference_labelset='default', candidate_labelset=CANDIDATE, gold_images=[a])
    (aq._store(project) / 'retired.json').write_text('{not json')
    with pytest.raises(ValueError, match='retired.json'):
        aq.list_profiles(project)

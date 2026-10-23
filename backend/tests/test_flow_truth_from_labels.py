"""A reviewer confirms a frozen cohort's folder labels as explicit truth in one step: each image gets its own dated
declaration under the reviewer's name, a folder that names no class stays unknown, and a repeat keeps what already
says the same. Folder names are labels for classification (and anomaly) sources; a segmentation flow keeps asking for
masks, so the scope here is a classification one."""
import shutil
from pathlib import Path

import pytest

from backend.engine import flow_evaluation
from backend.tests.test_whole_flow_evaluation import workspace, model_provider  # noqa: F401  (fixtures)

CLASSIFICATION_SCOPE = {'task': 'classification', 'classes': ['OK', 'NG'],
                        'class_semantics': {'version': 1, 'roles': {'OK': 'normal', 'NG': 'defect'}, 'basis': {'OK': 'explicit', 'NG': 'explicit'}}}


@pytest.fixture
def labelled_workspace(workspace, monkeypatch):  # noqa: F811
    project, graph, version, checkpoint = workspace
    source = Path(project['source_dataset_dir'])
    for name, folder in [('defect', 'test/NG'), ('normal', 'test/OK'), ('unknown', 'test/misc')]:
        target = source / folder / f'{name}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source / f'{name}.png'), target)
    for file in (Path(project['dataset_dir']) / 'splits').glob('*.json'):
        file.unlink()
    provider = model_provider(checkpoint)
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=provider)
    monkeypatch.setattr(flow_evaluation, 'describe_scope', lambda *args, **kwargs: dict(CLASSIFICATION_SCOPE))
    return project, version, cohort


def test_folder_labels_become_reviewed_truth_once(labelled_workspace):
    project, version, cohort = labelled_workspace
    first = flow_evaluation.confirm_truth_from_labels(project, cohort['cohort_id'], version, reviewer='검토자 A')
    assert (first['total'], first['declared'], first['kept'], first['skipped']) == (3, 2, 0, 1)
    assert first['unknown'] == ['test/misc/unknown.png']
    again = flow_evaluation.confirm_truth_from_labels(project, cohort['cohort_id'], version, reviewer='검토자 A')
    assert (again['declared'], again['kept'], again['skipped']) == (0, 2, 1)
    from backend.engine import image_truth
    row = image_truth.read_truth(project, str(Path(project['source_dataset_dir']) / 'test/NG/defect.png'), task='classification',
                                 classes=['OK', 'NG'], class_roles={'OK': 'normal', 'NG': 'defect'})
    assert (row['verdict'], row['defect_classes'], row['reviewer']) == ('NG', ['NG'], '검토자 A')


def test_reviewer_name_is_required(labelled_workspace):
    project, version, cohort = labelled_workspace
    with pytest.raises(ValueError, match='검토자'):
        flow_evaluation.confirm_truth_from_labels(project, cohort['cohort_id'], version, reviewer='  ')


def test_detection_truth_comes_only_from_positive_labels_a_person_made():
    roles = {'defect': 'defect', 'scratch': 'defect', 'OK': 'normal', 'background': 'normal'}
    verdict = flow_evaluation._shape_label_verdict
    assert verdict([{'type': 'bbox', 'label': 'scratch'}, {'type': 'bbox', 'label': 'defect'}], roles) == ('NG', ['defect', 'scratch'])
    assert verdict([{'type': 'tag', 'label': 'OK', 'is_normal': True}], roles) == ('OK', [])
    assert verdict([], roles) is None, 'an empty label is never a normal truth'
    assert verdict(None, roles) is None
    assert verdict([{'type': 'bbox', 'label': 'unnamed_role'}], roles) is None, 'a box of a class with no defect role decides nothing'
    assert verdict([{'type': 'tag', 'label': 'OK', 'is_normal': True}, {'type': 'bbox', 'label': 'unnamed_role'}], roles) is None


def test_a_detection_cohort_is_confirmed_from_each_image_s_labels_under_the_reviewer(monkeypatch):
    from backend.engine import image_truth
    samples = [{'relative_path': f'{name}.png', 'image_path': f'/src/{name}.png'} for name in ('boxed', 'normal', 'blank')]
    monkeypatch.setattr(flow_evaluation, 'read_cohort', lambda project, cohort_id: {'samples': samples})
    monkeypatch.setattr(flow_evaluation, 'describe_scope', lambda project, version_id, model_provider=None: {
        'task': 'detection', 'classes': ['defect'], 'class_semantics': {'roles': {'defect': 'defect', 'OK': 'normal'}}})
    monkeypatch.setattr(image_truth, 'read_truth', lambda *args, **kwargs: {'verdict': 'UNKNOWN', 'defect_classes': [], 'invalidated': False,
                                                                             'truth_revision': None, 'image_revision': 'r'})
    declared = []
    monkeypatch.setattr(image_truth, 'declare_truth', lambda project, path, **kwargs: declared.append((path, kwargs['verdict'], kwargs['defect_classes'], kwargs['reviewer'], kwargs['note'])))
    labels = {'/src/boxed.png': [{'type': 'bbox', 'label': 'defect'}], '/src/normal.png': [{'type': 'tag', 'label': 'OK', 'is_normal': True}], '/src/blank.png': []}
    result = flow_evaluation.confirm_truth_from_labels({'source_dataset_dir': '/src'}, 'cohort_x', 'v' * 32, reviewer='kim', labels_for=labels.get)
    assert result['declared'] == 2 and result['skipped'] == 1 and result['unknown'] == ['blank.png']
    assert declared == [('/src/boxed.png', 'NG', ['defect'], 'kim', '이미지의 라벨을 검토해 정답으로 확정'),
                        ('/src/normal.png', 'OK', [], 'kim', '이미지의 라벨을 검토해 정답으로 확정')]
    with pytest.raises(ValueError, match='라벨로 확정'):
        flow_evaluation.confirm_truth_from_labels({'source_dataset_dir': '/src'}, 'cohort_x', 'v' * 32, reviewer='kim')

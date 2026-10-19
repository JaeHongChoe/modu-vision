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

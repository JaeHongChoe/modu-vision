"""A source laid out as train/val/test folders (the first layout a beginner has, and the example project's) is an
explicit held-out split: a flow evaluation freezes its test folder without a saved split file, records what the folder
held, and notices when the folder's membership changes."""
import json
import shutil
from pathlib import Path

import pytest

from backend.engine import flow_evaluation
from backend.engine.heldout_splits import folder_split_assignments, folder_split_fingerprint
from backend.tests.test_whole_flow_evaluation import workspace, model_provider  # noqa: F401  (fixtures)


@pytest.fixture
def folder_workspace(workspace):  # noqa: F811
    project, graph, version, checkpoint = workspace
    source = Path(project['source_dataset_dir'])
    for name, part in [('defect', 'test'), ('normal', 'test'), ('unknown', 'train')]:
        target = source / part / 'all' / f'{name}.png'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source / f'{name}.png'), target)
    for file in (Path(project['dataset_dir']) / 'splits').glob('*.json'):
        file.unlink()
    return project, graph, version, checkpoint


def test_folder_assignments_read_the_split_folders_only(tmp_path):
    source = tmp_path / 'src'
    for relative in ['train/ok/a.png', 'test/ng/b.PNG', 'test/ok/c.jpg', 'val/ok/d.tif', 'other/e.png', 'test/ng/._f.png', 'test/notes.txt']:
        (source / relative).parent.mkdir(parents=True, exist_ok=True)
        (source / relative).write_bytes(b'x')
    assignments = folder_split_assignments(source)
    assert assignments == {'train/ok/a.png': 'train', 'test/ng/b.PNG': 'test', 'test/ok/c.jpg': 'test', 'val/ok/d.tif': 'val'}
    first = folder_split_fingerprint(assignments)
    (source / 'test/ok/g.png').write_bytes(b'x')
    assert folder_split_fingerprint(folder_split_assignments(source)) != first
    assert folder_split_assignments(tmp_path / 'nothing') == {}


def test_a_test_folder_is_frozen_as_the_held_out_cohort_without_a_saved_split_file(folder_workspace):
    project, _, version, checkpoint = folder_workspace
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    assert [row['relative_path'] for row in cohort['samples']] == ['test/all/defect.png', 'test/all/normal.png']
    assert cohort['split_kind'] == 'folders'
    assert cohort['split_path'] == str(Path(project['source_dataset_dir']).resolve())
    assert cohort['split_sha256'] == folder_split_fingerprint(folder_split_assignments(project['source_dataset_dir']))
    assert flow_evaluation._cohort_changes(project, flow_evaluation.read_cohort(project, cohort['cohort_id'])) == []


def test_a_changed_test_folder_invalidates_the_frozen_cohort(folder_workspace):
    project, _, version, checkpoint = folder_workspace
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    source = Path(project['source_dataset_dir'])
    shutil.copyfile(source / 'train/all/unknown.png', source / 'test/all/late.png')
    reasons = flow_evaluation._cohort_changes(project, flow_evaluation.read_cohort(project, cohort['cohort_id']))
    assert 'held_out_split_changed' in reasons


def test_without_a_test_folder_or_a_saved_split_the_freeze_says_what_to_do(folder_workspace):
    project, _, version, checkpoint = folder_workspace
    source = Path(project['source_dataset_dir'])
    shutil.rmtree(source / 'test')
    with pytest.raises(ValueError, match='test'):
        flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))


def test_a_saved_split_file_still_takes_precedence_over_the_folders(folder_workspace):
    project, _, version, checkpoint = folder_workspace
    source = Path(project['source_dataset_dir'])
    import hashlib
    file = Path(project['dataset_dir']) / 'splits' / f"{hashlib.sha256(str(source.resolve()).encode()).hexdigest()}.json"
    file.write_text(json.dumps({'folder_path': str(source.resolve()), 'assignments': {'test/all/defect.png': 'test', 'test/all/normal.png': 'train', 'train/all/unknown.png': 'train'}}))
    cohort = flow_evaluation.freeze_cohort(project, version, model_provider=model_provider(checkpoint))
    assert [row['relative_path'] for row in cohort['samples']] == ['test/all/defect.png']
    assert cohort['split_kind'] == 'file'

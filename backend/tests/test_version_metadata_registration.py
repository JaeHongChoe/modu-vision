"""Real version/metadata/cohort producers, without model inference or transport.

The existing five actual model evaluation tests remain unchanged and separately
exercise repeated adoption. These controls isolate the first registration write
and preserve meaningful source/label/split/workflow refusal boundaries.
"""
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset, routes_dataset_versions
from backend.engine import dataset_metadata
from backend.engine.annotation_storage import (
    dataset_annotation_dir, reset_request_annotation_root, reset_request_project_root,
    set_request_annotation_root, set_request_project_root,
)
from backend.engine.dataset_loaders import reset_request_split_root, set_request_split_root
from backend.remote.evaluation_cohort import freeze_cohort
from backend.tests.test_model_comparisons import _client


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in Path(root).rglob('*') if p.is_file()}


@contextmanager
def _scope(project):
    annotation = set_request_annotation_root(Path(project['annotations_dir']))
    owner = set_request_project_root(Path(project['project_dir']))
    split = set_request_split_root(Path(project['dataset_dir']) / 'splits')
    try:
        yield
    finally:
        reset_request_split_root(split)
        reset_request_project_root(owner)
        reset_request_annotation_root(annotation)


def _workspace(tmp_path, monkeypatch, task):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    source = tmp_path / 'source'
    images = {}
    for part, offset in [('train', 10), ('val', 40), ('test', 90)]:
        for label, color in [('OK', 30), ('NG', 160)]:
            image = (source / 'images' / part / (label + '.png')
                     if task in ('segmentation', 'detection') else
                     source / part / ('good' if label == 'OK' else 'defect') / 'sample.png'
                     if task == 'anomaly' else source / part / label / 'sample.png')
            image.parent.mkdir(parents=True, exist_ok=True)
            Image.new('RGB', (32, 32), (color, offset, 20)).save(image)
            images[(part, label)] = image
            if task == 'detection':
                image.with_suffix('.json').write_text(json.dumps({
                    'imagePath': image.name, 'imageWidth': 32, 'imageHeight': 32,
                    'shapes': [] if label == 'OK' else [{
                        'label': 'defect', 'shape_type': 'rectangle', 'points': [[3, 3], [15, 15]],
                    }],
                }))
            if task == 'segmentation' or task == 'anomaly' and label == 'NG':
                mask = (source / 'masks' / part / image.name if task == 'segmentation'
                        else source / 'ground_truth' / 'defect' / 'sample_mask.png')
                mask.parent.mkdir(parents=True, exist_ok=True)
                Image.new('L', (32, 32), 0 if label == 'OK' else 1).save(mask)
    client = _client(tmp_path)
    response = client.post('/api/project/create', json={'name': 'Registered version', 'task': task})
    assert response.status_code == 200, response.text
    project = response.json()
    response = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert response.status_code == 200, response.text
    project['source_dataset_dir'] = str(source)
    return client, project, source, images


def _version(client, project, source):
    response = client.post('/api/dataset/versions', json={'name': 'Original registered truth'})
    assert response.status_code == 200, response.text
    version = response.json()
    directory, manifest = routes_dataset_versions._read_manifest(project, version['id'])
    ledger = dataset_metadata.ledger_path(Path(project['project_dir']), source,
                                          Path(project['annotations_dir']))
    assert ledger.is_file(), 'Initial manual version must register source metadata before snapshot'
    workflow = next(r for r in manifest['files'] if
                    r['origin'] == 'studio' and r['relative_path'] == 'metadata/workflow.json')
    assert workflow['sha256'] == _sha(ledger)
    assert (directory / workflow['snapshot_path']).read_bytes() == ledger.read_bytes()
    return version, directory, manifest, ledger


def _cohort(project, version, source, images, task):
    names = ['background', 'defect'] if task == 'segmentation' else ['defect'] if task == 'detection' else ['OK', 'NG']
    metadata = {'task': task, 'classes': names, 'anomaly_mode': 'segmentation',
                'training_provenance': {'labelset_id': 'default'}}
    # Only cohort extraction is controlled here: these are real disjoint original
    # training pixels, not a fabricated completed-model or inference receipt.
    context = SimpleNamespace(selection_sha256={_sha(p) for (part, _), p in images.items() if part in ('train', 'val')})
    with _scope(project):
        return freeze_cohort(project, version['id'], source, task, metadata, context)


@pytest.mark.parametrize('task', ['classification', 'segmentation', 'detection', 'anomaly'])
def test_new_version_registers_before_snapshot_and_repeat_cohort_metadata_reads_are_byte_stable(tmp_path, monkeypatch, task):
    client, project, source, images = _workspace(tmp_path, monkeypatch, task)
    original_source = _bytes(source)
    version, directory, manifest, ledger = _version(client, project, source)
    original_version = _bytes(directory)
    registered = ledger.read_bytes()
    rows = json.loads(registered)['images']
    assert all(rows[p.relative_to(source).as_posix()]['revision'] == 1 for p in images.values())
    assert all(rows[p.relative_to(source).as_posix()]['audit'][0]['action'] == 'registered' for p in images.values())
    first = _cohort(project, version, source, images, task)
    with _scope(project):
        for _ in range(2):
            for (part, _), image in images.items():
                if part == 'test':
                    observed = dataset_metadata.metadata_for_path(Path(project['project_dir']), source, image,
                                                                  Path(project['annotations_dir']))
                    original = rows[image.relative_to(source).as_posix()]
                    assert observed['image_uuid'] == original['image_uuid']
                    assert observed['revision'] == original['revision'] == 1
                    assert observed['content_hash'] == _sha(image)
                    assert observed['workflow_state'] == original['workflow_state'] == 'unworked'
            assert ledger.read_bytes() == registered
    second = _cohort(project, version, source, images, task)
    assert first['descriptor'] == second['descriptor']
    assert first['version_manifest_sha256'] == second['version_manifest_sha256'] == manifest['content_digest']
    assert ledger.read_bytes() == registered and _bytes(directory) == original_version
    assert _bytes(source) == original_source
    with _scope(project):
        verification = routes_dataset_versions._verify(project, directory, manifest)
    assert verification['status'] == 'verified' and verification['editable_changed_files'] == []


@pytest.mark.parametrize('change', ['product', 'workflow_state', 'annotation', 'split', 'pixels'])
def test_registered_version_still_refuses_meaningful_row_label_split_or_pixel_changes(tmp_path, monkeypatch, change):
    client, project, source, images = _workspace(tmp_path, monkeypatch, 'classification')
    version, directory, manifest, ledger = _version(client, project, source)
    original_version = _bytes(directory)
    image = images[('test', 'NG')]
    with _scope(project):
        if change in ('product', 'workflow_state'):
            row = dataset_metadata.metadata_for_path(Path(project['project_dir']), source, image,
                                                     Path(project['annotations_dir']))
            dataset_metadata.update_metadata(Path(project['project_dir']), source, row['image_uuid'],
                                             row['revision'], 'Controlled reviewer',
                                             {change: 'approved' if change == 'workflow_state' else 'Changed product'},
                                             Path(project['annotations_dir']))
        elif change == 'annotation':
            overlay = dataset_annotation_dir(image.parent, Path(project['annotations_dir']), use_scope=False)
            overlay.mkdir(parents=True, exist_ok=True)
            (overlay / (image.stem + '.json')).write_text(json.dumps({'annotations': [{'type': 'tag', 'label': 'NG'}]}))
        elif change == 'split':
            split = routes_dataset._split_manifest_file(source)
            split.parent.mkdir(parents=True, exist_ok=True)
            split.write_text(json.dumps({'folder_path': str(source), 'assignments': {image.relative_to(source).as_posix(): 'train'}}))
        else:
            Image.new('RGB', (32, 32), (1, 2, 3)).save(image)
        verification = routes_dataset_versions._verify(project, directory, manifest)
    assert verification['status'] == 'changed' or verification['editable_changed_files']
    with pytest.raises(HTTPException) as caught:
        _cohort(project, version, source, images, 'classification')
    assert caught.value.status_code == 409 and caught.value.detail == 'Common cohort version has changed pixels, labels, or split'
    assert _bytes(directory) == original_version, 'No rewritten saved version may hide the refusal'


def test_external_source_link_retains_original_snapshot_mapping_without_metadata_registration(tmp_path, monkeypatch):
    client, project, source, _ = _workspace(tmp_path, monkeypatch, 'classification')
    external = tmp_path / 'outside.png'
    Image.new('RGB', (32, 32), (4, 5, 6)).save(external)
    linked = source / 'linked.png'
    linked.symlink_to(external)
    original = _bytes(source)
    _, _, manifest, ledger = _version(client, project, source)
    row = next(r for r in manifest['files'] if r['origin'] == 'source' and r['relative_path'] == 'linked.png')
    assert row['source_path'] == str(external.resolve()) and row['sha256'] == _sha(external)
    assert 'linked.png' not in json.loads(ledger.read_bytes())['images']
    assert linked.is_symlink() and linked.readlink() == external
    assert _bytes(source) == original


def test_original_registration_error_propagates_before_snapshot_creation(tmp_path, monkeypatch):
    _, project, source, _ = _workspace(tmp_path, monkeypatch, 'classification')
    original = _bytes(source)
    failure = OSError('Original registration I/O failure')
    def refused(*args, **kwargs):
        raise failure
    def snapshot_started(*args, **kwargs):
        pytest.fail('Snapshot must not start after original registration refusal')
    monkeypatch.setattr(routes_dataset_versions, '_current_project', lambda request: project)
    monkeypatch.setattr(dataset_metadata, 'metadata_for_path', refused)
    monkeypatch.setattr(routes_dataset_versions, '_snapshot', snapshot_started)
    with _scope(project), pytest.raises(OSError) as caught:
        routes_dataset_versions.create_version(routes_dataset_versions.VersionCreateRequest(name='Refusal'), object())
    assert caught.value is failure and _bytes(source) == original


def test_initial_version_captures_default_policy_before_real_eligibility_reads(tmp_path, monkeypatch):
    from backend.engine import team_data
    client, project, source, images = _workspace(tmp_path, monkeypatch, 'classification')
    original_source = _bytes(source)
    version, directory, _, ledger = _version(client, project, source)
    registered = ledger.read_bytes()
    original_version = _bytes(directory)
    expected = {'schema_version': 1, 'settings': dict(team_data.DEFAULT_SETTINGS), 'books': []}
    assert json.loads(registered).get('team_data') == expected, (
        'Initial manual version must capture the real default team policy before snapshot')
    with _scope(project):
        workspace = team_data.workspace(project, source)
        assert workspace['settings'] == expected['settings'] and workspace['book_history'] == []
        assert team_data.training_excluded_paths(project, source, Path(project['annotations_dir'])) == set()
    _cohort(project, version, source, images, 'classification')
    assert ledger.read_bytes() == registered and _bytes(directory) == original_version
    assert _bytes(source) == original_source


def test_existing_policy_is_not_reset_and_later_policy_change_still_refuses_saved_version(tmp_path, monkeypatch):
    from backend.engine import team_data
    client, project, source, images = _workspace(tmp_path, monkeypatch, 'classification')
    with _scope(project):
        existing = team_data.update_settings(project, source, 1, 'Controlled reviewer',
                                            {'editing_enabled': True, 'review_enabled': True,
                                             'required_reviews': 2})
    version, directory, _, ledger = _version(client, project, source)
    original_version = _bytes(directory)
    original_source = _bytes(source)
    saved_settings = json.loads(ledger.read_bytes())['team_data']['settings']
    assert saved_settings == existing and saved_settings['revision'] == 2
    with _scope(project):
        assert team_data.workspace(project, source)['settings'] == saved_settings
        team_data.update_settings(project, source, 2, 'Controlled reviewer',
                                  {'approved_only_training': True})
        _, manifest = routes_dataset_versions._read_manifest(project, version['id'])
        verification = routes_dataset_versions._verify(project, directory, manifest)
    assert verification['editable_changed_files']
    with pytest.raises(HTTPException) as caught:
        _cohort(project, version, source, images, 'classification')
    assert caught.value.status_code == 409
    assert caught.value.detail == 'Common cohort version has changed pixels, labels, or split'
    assert _bytes(directory) == original_version and _bytes(source) == original_source

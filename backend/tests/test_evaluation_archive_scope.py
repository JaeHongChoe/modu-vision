"""Archive-only controls: real metadata/history, no model or inference.

Core cohort verification/extraction is an explicit prevalidated-result seam in
these controls. Existing full cohort tests own metric/truth/runtime validation.
These controls cannot qualify model quality or an original runtime receipt.
"""
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest
from PIL import Image

from backend.api import routes_dataset
from backend.engine.annotation_storage import (
    set_request_annotation_root, reset_request_annotation_root,
    set_request_project_root, reset_request_project_root,
)
from backend.engine.dataset_loaders import set_request_split_root, reset_request_split_root
from backend.engine.dataset_metadata import metadata_for_path, metadata_transaction
from backend.engine.evaluation_history import EvaluationHistory, canonical


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding='utf-8')


def _tree(root):
    return {
        path.relative_to(root).as_posix(): ('directory' if path.is_dir() else path.read_bytes())
        for path in root.rglob('*')
    }


@contextmanager
def _scope(project):
    project_token = set_request_project_root(Path(project['project_dir']))
    annotation_token = split_token = None
    try:
        annotation_token = set_request_annotation_root(Path(project['annotations_dir']))
        split_token = set_request_split_root(Path(project['dataset_dir']) / 'splits')
        yield
    finally:
        if split_token is not None:
            reset_request_split_root(split_token)
        if annotation_token is not None:
            reset_request_annotation_root(annotation_token)
        reset_request_project_root(project_token)


def _project(tmp_path, monkeypatch, source):
    monkeypatch.chdir(tmp_path)
    legacy = tmp_path / 'legacy-default'
    legacy.mkdir()
    monkeypatch.setattr(routes_dataset, 'STUDIO_ANNOTATIONS_DIR', legacy)
    root = tmp_path / 'project'
    root.mkdir()
    project = {
        'id': 'owned-scope-control', 'project_dir': str(root),
        'source_dataset_dir': str(source), 'annotations_dir': str(root / 'annotations'),
        'dataset_dir': str(root / 'dataset'), 'models_dir': str(root / 'models'),
        'reports_dir': str(tmp_path / 'fresh-reports'), 'active_labelset_id': 'default',
    }
    _write(root / 'project.json', project)
    _write(root / 'labelsets.json', {
        'schema_version': 1, 'active_id': 'default',
        'labelsets': [{'id': 'default', 'name': 'Owned', 'source_id': None, 'created_at': ''}],
    })
    return project, legacy


def _metadata(project, source, images):
    # Register through the real producer before freezing the protected trees.
    for image in images:
        metadata_for_path(Path(project['project_dir']), source, image, Path(project['annotations_dir']))
    with metadata_transaction(Path(project['project_dir']), source, Path(project['annotations_dir'])) as ledger:
        for image in images:
            row = ledger['images'][image.relative_to(source).as_posix()]
            row['product'] = 'owned-scoped-history'
            row['revision'] = 7
    return {
        str(image): metadata_for_path(Path(project['project_dir']), source, image, Path(project['annotations_dir']))
        for image in images
    }


def _lineage(project, source, task, split):
    from backend.api.routes_model_comparisons import _fingerprint
    version = Path(project['project_dir']) / 'versions/v_archive_scope_control'
    fingerprint = _fingerprint(source)
    files = [{
        'origin': 'source', 'relative_path': image.relative_to(source).as_posix(),
        'source_path': str(image), 'kind': 'image', 'size_bytes': image.stat().st_size, 'sha256': _sha(image),
    } for image in sorted(source.rglob('*.png'))]
    if split is not None:
        files.append({'origin': 'split', 'relative_path': 'saved.json', 'source_path': str(split),
                      'kind': 'label', 'size_bytes': split.stat().st_size, 'sha256': _sha(split)})
    manifest = {'schema_version': 1, 'id': version.name, 'project_id': project['id'],
                'labelset_id': 'default', 'task': task, 'source_dataset_dir': str(source),
                'dataset_fingerprint': fingerprint, 'files': files}
    manifest['content_digest'] = hashlib.sha256(canonical(manifest)).hexdigest()
    _write(version / 'manifest.json', manifest)
    binding = {
        'dataset_version_id': version.name, 'version_dir': str(version), 'labelset_id': 'default',
        'dataset_fingerprint': fingerprint, 'manifest_sha256': manifest['content_digest'],
        'split_binding': 'saved_manifest' if split is not None else 'versioned_dataset_layout',
        'split_sha256': _sha(split) if split is not None else hashlib.sha256(json.dumps(
            [{key: row[key] for key in ('origin', 'relative_path', 'sha256')} for row in files],
            sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
    }
    return manifest, binding


def test_patch_archive_reads_existing_scoped_history_without_legacy_or_original_writes(tmp_path, monkeypatch):
    from backend.engine.patch_classification import load_patch_manifest
    from backend.engine.patch_evaluation_evidence import archive_patch_evaluation
    from backend.tests.test_patch_classification import _manifest
    dataset = tmp_path / 'prepared'
    dataset.mkdir()
    value = _manifest(dataset)
    source = tmp_path / 'source'
    mapping = {}
    for row in value['patches']:
        original = source / row['image']
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes((dataset / row['image']).read_bytes())
        mapping[row['image']] = {'source_relative_path': row['image'], 'source_sha256': row['source_sha256']}
    value.update(source_dataset_path=str(source), source_map=mapping)
    _write(dataset / 'patches.json', value)
    manifest = load_patch_manifest(dataset)
    project, legacy = _project(tmp_path, monkeypatch, source)
    images = [source / row.image for row in manifest.patches if row.split == 'test']
    facts = _metadata(project, source, images)
    with _scope(project):
        _, lineage = _lineage(project, source, 'segmentation', None)
        checkpoint = Path(project['models_dir']) / 'job_scope/best_model.pt'
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(b'archive-only synthetic checkpoint; never loaded')
        meta = {'task': 'patch_classification', 'patch_provenance': manifest.provenance, 'training_provenance': lineage}
        _write(checkpoint.with_name('model_meta.json'), meta)
        rows = [{'image': row.image, 'box': list(row.box), 'ground_truth': row.label,
                 'source_sha256': row.source_sha256, 'predicted_class': row.label,
                 'model_sha256': _sha(checkpoint), 'dataset_sha256': manifest.provenance['dataset_sha256']}
                for row in manifest.patches if row.split == 'test']
        result = {'job_id': 'job_scope', 'task': 'patch_classification', 'quality_approved': False,
                  'model_sha256': _sha(checkpoint), 'dataset_sha256': manifest.provenance['dataset_sha256'],
                  'metrics': {'evaluated_split': 'test'}, 'test_predictions': rows,
                  'confusion_matrix': {'classes': manifest.classes, 'matrix': [[1, 0], [0, 1]]}}
        before = (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))
        archived = archive_patch_evaluation(project, checkpoint, dataset, result, execution={'device': 'cpu'})
        for row in archived['test_predictions']:
            expected = facts[row['file_path']]
            assert row['revision'] == 7 and row['product'] == 'owned-scoped-history'
            assert row['image_uuid'] == expected['image_uuid'] and row['content_hash'] == expected['content_hash']
        assert archived['binding']['training_provenance'] == lineage
        assert not archived['quality_approved']
        assert before == (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))
        assert EvaluationHistory(Path(project['reports_dir']) / 'evaluations').get(archived['evaluation_id'])['binding']['training_provenance'] == lineage


def _core_control(tmp_path, monkeypatch, *, saved_split):
    from backend.engine import core_evaluation_recipe as core
    from backend.api import routes_dataset_versions as versions
    source = tmp_path / 'source'
    image = source / 'test/OK/image.png'
    image.parent.mkdir(parents=True)
    Image.new('RGB', (16, 16), 'white').save(image)
    project, legacy = _project(tmp_path, monkeypatch, source)
    facts = _metadata(project, source, [image])
    with _scope(project):
        split = routes_dataset._split_manifest_file(source) if saved_split else None
        if split is not None:
            _write(split, {'folder_path': str(source), 'assignments': {'test/OK/image.png': 'test'}})
        manifest, lineage = _lineage(project, source, 'classification', split)
    checkpoint = Path(project['models_dir']) / 'job_core/best_model.pt'
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b'archive-only synthetic classifier; never loaded')
    _write(checkpoint.with_name('model_meta.json'), {'task': 'classification', 'training_provenance': lineage})
    descriptor = {'cohort_sha256': 'a' * 64, 'ordered_samples': [], 'class_semantics': {'roles': {'OK': 'normal'}}}
    info = {'device': 'cpu', 'cohort': {'dataset_version_id': manifest['id'], 'version_manifest_sha256': manifest['content_digest']},
            'descriptor': descriptor}
    archive = Path(project['reports_dir']) / 'evaluation_cohorts' / ('cohort_' + 'a' * 64) / 'input.tar.gz'
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b'explicit prevalidated cohort seam; not model input')
    runtime = {'device': 'cpu', 'process_id': 123}
    result = {'job_id': 'job_core', 'task': 'classification', 'quality_approved': False,
              'runtime_device_identity': runtime, 'test_predictions': [{'file_path': str(image), 'ground_truth': 'OK', 'predicted_class': 'OK'}],
              'common_cohort': {}, 'evaluation_binding_sha256': 'b' * 64, 'execution_target': 'local',
              'compute_profile_id': None, 'execution_profile_sha256': None, 'device': 'cpu', 'input_receipt': {}}
    # Only the already-validated cohort seam is controlled. Metadata, fingerprint,
    # lineage, checkpoint SHA and durable append/readback remain real producers.
    monkeypatch.setattr(core, 'evaluation_spec', lambda *args: {})
    monkeypatch.setattr(versions, '_read_manifest', lambda *args: (Path(lineage['version_dir']), manifest))
    monkeypatch.setattr(versions, '_verify', lambda *args: {'status': 'verified', 'editable_changed_files': []})
    monkeypatch.setattr(core, 'extract_cohort', lambda *args: (tmp_path / 'data', descriptor))
    monkeypatch.setattr(core, 'validate_result', lambda result, *args: copy.deepcopy(result))
    return project, legacy, source, image, checkpoint, info, runtime, result, lineage, facts, split


def test_core_archive_reads_existing_scoped_history_without_legacy_or_original_writes(tmp_path, monkeypatch):
    from backend.engine.core_evaluation_recipe import archive_core_evaluation
    project, legacy, source, image, checkpoint, info, runtime, result, lineage, facts, _ = _core_control(tmp_path, monkeypatch, saved_split=False)
    before = (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))
    with _scope(project):
        archived = archive_core_evaluation(project, checkpoint, info, result, {}, runtime, tmp_path / 'fresh-adoption')
    row = archived['test_predictions'][0]
    assert row['revision'] == 7 and row['product'] == 'owned-scoped-history'
    assert row['image_uuid'] == facts[str(image)]['image_uuid'] and row['content_hash'] == _sha(image)
    assert archived['binding']['training_provenance'] == lineage
    assert before == (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))


@pytest.mark.parametrize('saved_split', [False, True])
def test_core_archive_fingerprint_includes_the_existing_saved_split_only_when_present(tmp_path, monkeypatch, saved_split):
    from backend.engine.core_evaluation_recipe import archive_core_evaluation
    from backend.api.routes_model_comparisons import _fingerprint
    project, _, source, _, checkpoint, info, runtime, result, lineage, _, split = _core_control(tmp_path, monkeypatch, saved_split=saved_split)
    with _scope(project):
        expected = _fingerprint(source)
        assert expected == lineage['dataset_fingerprint']
        archived = archive_core_evaluation(project, checkpoint, info, result, {}, runtime, tmp_path / 'first-adoption')
        assert archived['binding']['dataset_fingerprint'] == expected
        if split is not None:
            previous = split.read_bytes()
            _write(split, {'folder_path': str(source), 'assignments': {'test/OK/image.png': 'test'}, 'controlled_saved_revision': 2})
            assert split.read_bytes() != previous and _fingerprint(source) != expected
            second = archive_core_evaluation(project, checkpoint, info, result, {}, runtime, tmp_path / 'second-adoption')
            assert second['binding']['dataset_fingerprint'] == _fingerprint(source)
            assert second['binding']['training_provenance'] == lineage


def test_explicit_metadata_override_stays_explicit_even_with_another_scoped_root(tmp_path, monkeypatch):
    source = tmp_path / 'source'
    image = source / 'image.png'
    source.mkdir()
    Image.new('RGB', (16, 16)).save(image)
    project, _ = _project(tmp_path, monkeypatch, source)
    explicit = tmp_path / 'explicit-annotation-override'
    with _scope(project):
        facts = metadata_for_path(Path(project['project_dir']), source, image, explicit)
    assert facts['content_hash'] == _sha(image)
    assert list(explicit.rglob('workflow.json'))
    assert not list(Path(project['annotations_dir']).rglob('workflow.json'))

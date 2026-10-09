"""Fresh evaluation records retain owned training lineage without approving it."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from backend.engine.evaluation_history import EvaluationHistory, evaluation_model_context


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _fixture(tmp_path):
    project = tmp_path / 'owned'
    source = tmp_path / 'original-source'
    source.mkdir()
    image = source / 'sample.png'
    image.write_bytes(b'inert test control pixels; no model is loaded')
    version_id = 'v_20261009_000858_8f973ca2'
    version = project / 'versions' / version_id
    split_bytes = b'{"assignments":{"sample.png":"test"}}'
    rows = [
        {'origin': 'source', 'relative_path': 'sample.png', 'source_path': str(image),
         'kind': 'image', 'size_bytes': image.stat().st_size, 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()},
        {'origin': 'split', 'relative_path': 'manifest.json', 'source_path': str(project / 'annotations/split-manifests/original.json'),
         'kind': 'label', 'size_bytes': len(split_bytes), 'sha256': hashlib.sha256(split_bytes).hexdigest()},
    ]
    manifest = {'schema_version': 1, 'id': version_id, 'project_id': 'owned-project',
                'labelset_id': 'default', 'task': 'segmentation',
                'source_dataset_dir': str(source), 'dataset_fingerprint': 'v1:' + 'a' * 64, 'files': rows}
    manifest['content_digest'] = _digest(manifest)
    team = {'scope': {'project_id': 'owned-project', 'source': str(source), 'labelset_id': 'default'},
            'settings': {'approved_only_training': False}, 'eligibility': [{'image_uuid': 'test-control'}]}
    binding = {'dataset_version_id': version_id, 'labelset_id': 'default',
               'dataset_fingerprint': manifest['dataset_fingerprint'], 'manifest_sha256': manifest['content_digest'],
               'split_sha256': rows[1]['sha256'], 'split_binding': 'saved_manifest', 'version_dir': str(version),
               'team_data': team, 'team_data_sha256': _digest(team),
               'family_task': 'patch_classification', 'family_dataset_path': str(project / 'dataset/patch/original'),
               'family_provenance': {'dataset_sha256': 'sha256:' + 'b' * 64, 'source_map': {'sample.png': {'source_relative_path': 'sample.png'}}},
               'family_inputs': [{'relative_path': 'patches.json', 'source_path': str(project / 'dataset/patch/original/patches.json'),
                                  'sha256': 'c' * 64, 'snapshot_path': str(version / 'labels/family/patch_classification/patches.json')}],
               'family_inputs_sha256': 'd' * 64}
    metadata = {'task': 'patch_classification', 'training_provenance': binding,
                'probability_threshold': .7, 'warm_start': {'parent_job_id': 'job_parent'}}
    _write(project / 'project.json', {'id': 'owned-project', 'source_dataset_dir': str(source)})
    _write(project / 'labelsets.json', {'schema_version': 1, 'active_id': 'default',
                                      'labelsets': [{'id': 'default', 'name': 'Original', 'source_id': None, 'created_at': ''}]})
    _write(version / 'manifest.json', manifest)
    _write(version / 'team-data.json', team)
    return project, metadata, manifest


def test_fresh_patch_history_retains_exact_original_metadata_lineage(tmp_path):
    project, metadata, _ = _fixture(tmp_path)
    before = {str(p): p.read_bytes() for p in project.rglob('*') if p.is_file()}
    context = evaluation_model_context(project, metadata)
    assert context['training_provenance'] == metadata['training_provenance']
    assert context['training_labelset_id'] == 'default'
    assert context['parent_job_id'] == 'job_parent'
    assert context['threshold_settings'] == {'probability_threshold': .7}
    assert not any(key in context for key in ('training_version_verified', 'quality_approved', 'human_approved'))
    result = {'job_id': 'job_original_patch', 'task': 'patch_classification', 'test_predictions': []}
    history = EvaluationHistory(tmp_path / 'new-evaluation-reports')
    row = history.append(result, context)
    readback = history.get(row['evaluation_id'])
    assert readback['binding']['training_provenance'] == metadata['training_provenance']
    assert readback['evidence_sha256'] == _digest({k: v for k, v in readback.items() if k != 'evidence_sha256'})
    assert before == {str(p): p.read_bytes() for p in project.rglob('*') if p.is_file()}


def test_retained_lineage_is_a_detached_nested_copy(tmp_path):
    project, metadata, _ = _fixture(tmp_path)
    original = copy.deepcopy(metadata['training_provenance'])
    context = evaluation_model_context(project, metadata)
    metadata['training_provenance']['team_data']['eligibility'][0]['image_uuid'] = 'later-mutation'
    metadata['training_provenance']['family_inputs'][0]['sha256'] = 'later-mutation'
    assert context['training_provenance'] == original


@pytest.mark.parametrize('value', [None, {}, [], ['labelset_id'], 'foreign', 1, True])
def test_legacy_or_malformed_lineage_keeps_existing_context_without_promoting_it(tmp_path, value):
    project, metadata, _ = _fixture(tmp_path)
    metadata['training_provenance'] = value
    context = evaluation_model_context(project, metadata)
    assert 'training_provenance' not in context
    assert context['labelset_id'] == 'default'
    assert context['parent_job_id'] == 'job_parent'
    assert context['threshold_settings'] == {'probability_threshold': .7}


def test_absent_lineage_does_not_invent_training_labels_or_verified_state(tmp_path):
    project, metadata, _ = _fixture(tmp_path)
    metadata.pop('training_provenance')
    context = evaluation_model_context(project, metadata)
    assert context == {'labelset_id': 'default', 'training_labelset_id': None,
                       'parent_job_id': 'job_parent', 'threshold_settings': {'probability_threshold': .7}}


def test_partial_legacy_labelset_is_preserved_without_claiming_full_lineage(tmp_path):
    project, metadata, _ = _fixture(tmp_path)
    metadata['training_provenance'] = {'labelset_id': 'ls_123456789abc'}
    context = evaluation_model_context(project, metadata)
    assert context['labelset_id'] == 'default'
    assert context['training_labelset_id'] == 'ls_123456789abc'
    assert 'training_provenance' not in context


def test_versioned_layout_split_uses_the_existing_original_producer_contract(tmp_path):
    project, metadata, manifest = _fixture(tmp_path)
    manifest['files'] = [row for row in manifest['files'] if row['origin'] != 'split']
    manifest['content_digest'] = _digest({k: v for k, v in manifest.items() if k != 'content_digest'})
    binding = metadata['training_provenance']
    binding['manifest_sha256'] = manifest['content_digest']
    binding['split_binding'] = 'versioned_dataset_layout'
    binding['split_sha256'] = hashlib.sha256(json.dumps(
        [{key: row[key] for key in ('origin', 'relative_path', 'sha256')} for row in manifest['files']],
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    _write(Path(binding['version_dir']) / 'manifest.json', manifest)
    assert evaluation_model_context(project, metadata)['training_provenance'] == binding


@pytest.mark.parametrize('field,value', [
    ('dataset_version_id', 'v_foreign'), ('version_dir', '/foreign/versions/v_original'),
    ('manifest_sha256', '0' * 64), ('dataset_fingerprint', 'v1:' + '0' * 64),
    ('labelset_id', 'ls_123456789abc'), ('split_sha256', '0' * 64),
    ('split_binding', 'versioned_dataset_layout'), ('team_data_sha256', '0' * 64),
    ('team_data', None), ('team_data', []), ('family_inputs', float('nan')),
])
def test_incoherent_recorded_binding_is_not_archived_as_owned_lineage(tmp_path, field, value):
    project, metadata, _ = _fixture(tmp_path)
    metadata['training_provenance'][field] = value
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


@pytest.mark.parametrize('field,value', [
    ('project_id', 'other-project'), ('source_dataset_dir', '/other/original-source'),
    ('labelset_id', 'ls_123456789abc'), ('id', 'v_other'), ('files', [None]),
])
def test_foreign_or_malformed_version_is_not_accepted_even_with_recomputed_digest(tmp_path, field, value):
    project, metadata, manifest = _fixture(tmp_path)
    manifest[field] = value
    manifest['content_digest'] = _digest({k: v for k, v in manifest.items() if k != 'content_digest'})
    metadata['training_provenance']['manifest_sha256'] = manifest['content_digest']
    _write(Path(metadata['training_provenance']['version_dir']) / 'manifest.json', manifest)
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


def test_changed_frozen_team_receipt_does_not_create_new_training_claim(tmp_path):
    project, metadata, _ = _fixture(tmp_path)
    team = copy.deepcopy(metadata['training_provenance']['team_data'])
    team['scope']['project_id'] = 'another-project'
    metadata['training_provenance']['team_data'] = team
    metadata['training_provenance']['team_data_sha256'] = _digest(team)
    _write(Path(metadata['training_provenance']['version_dir']) / 'team-data.json', team)
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


@pytest.mark.parametrize('leaf', ['manifest.json', 'team-data.json'])
def test_linked_receipt_refuses_lineage_without_loading_foreign_content(tmp_path, leaf):
    project, metadata, _ = _fixture(tmp_path)
    path = Path(metadata['training_provenance']['version_dir']) / leaf
    raw = path.read_bytes()
    path.unlink()
    outside = tmp_path / ('outside-' + leaf)
    outside.write_bytes(raw)
    path.symlink_to(outside)
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


@pytest.mark.parametrize('field,value', [('schema_version', True), ('project_id', None),
                                      ('source_dataset_dir', None), ('dataset_fingerprint', None), ('labelset_id', None)])
def test_coherent_but_malformed_manifest_identity_is_still_pending(tmp_path, field, value):
    project, metadata, manifest = _fixture(tmp_path)
    manifest[field] = value
    binding = metadata['training_provenance']
    if field in ('dataset_fingerprint', 'labelset_id'):
        binding[field] = value
    if field in ('project_id', 'source_dataset_dir'):
        project_record = json.loads((project / 'project.json').read_text())
        project_record['id' if field == 'project_id' else field] = value
        _write(project / 'project.json', project_record)
        team = binding['team_data']
        team['scope']['project_id' if field == 'project_id' else 'source'] = value
        binding['team_data_sha256'] = _digest(team)
        _write(Path(binding['version_dir']) / 'team-data.json', team)
    if field == 'labelset_id':
        binding['team_data']['scope']['labelset_id'] = value
        binding['team_data_sha256'] = _digest(binding['team_data'])
        _write(Path(binding['version_dir']) / 'team-data.json', binding['team_data'])
    manifest['content_digest'] = _digest({k: v for k, v in manifest.items() if k != 'content_digest'})
    binding['manifest_sha256'] = manifest['content_digest']
    _write(Path(binding['version_dir']) / 'manifest.json', manifest)
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


@pytest.mark.parametrize('field,value', [('sha256', None), ('size_bytes', True), ('origin', 'foreign'),
                                      ('relative_path', '../other.png')])
def test_coherent_but_malformed_inventory_cannot_create_owned_training_lineage(tmp_path, field, value):
    project, metadata, manifest = _fixture(tmp_path)
    manifest['files'][0][field] = value
    manifest['content_digest'] = _digest({k: v for k, v in manifest.items() if k != 'content_digest'})
    metadata['training_provenance']['manifest_sha256'] = manifest['content_digest']
    _write(Path(metadata['training_provenance']['version_dir']) / 'manifest.json', manifest)
    assert 'training_provenance' not in evaluation_model_context(project, metadata)


def test_dataset_snapshot_uses_size_bytes_without_an_invented_size_field(tmp_path):
    project, metadata, manifest = _fixture(tmp_path)
    # routes_dataset_versions._snapshot writes size_bytes for every row. A
    # helper must accept those original rows without modifying their receipts.
    assert all('size_bytes' in row and 'size' not in row for row in manifest['files'])
    assert evaluation_model_context(project, metadata)['training_provenance'] == metadata['training_provenance']

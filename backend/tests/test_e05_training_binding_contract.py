"""CPU contracts for frozen team-data receipts and E05 gold policy drift."""
import copy
import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException
from PIL import Image

from backend.engine import annotation_quality as aq
from backend.engine import team_data
from backend.engine.training_provenance import bind_training_version, validate_training_binding
from backend.main import create_app


EMPTY_GOLD_SHA256 = '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'
DEFAULT_GOLD = {'include_gold_in_training': False, 'gold_images': 0, 'gold_set_sha256': EMPTY_GOLD_SHA256}


def _canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


@pytest.fixture
def binding_workspace(tmp_path):
    app = create_app(str(tmp_path / 'registry'))
    client = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    response = client.post('/api/project/create', json={'name': '고정 영수증', 'task': 'detection'})
    assert response.status_code == 200, response.text
    source = tmp_path / 'source'
    source.mkdir()
    for name in ('a', 'b'):
        Image.new('RGB', (40, 30), 'white').save(source / f'{name}.png')
    response = client.put('/api/project/update', json={'source_dataset_dir': str(source)})
    assert response.status_code == 200, response.text
    project = response.json()
    for name in ('a', 'b'):
        response = client.post('/api/annotations/save', json={
            'image_id': name, 'image_path': str(source / f'{name}.png'), 'actor': 'Lee',
            'annotations': [{'id': name, 'type': 'tag', 'label': 'OK', 'is_normal': True, 'category_id': 0}],
            'image_width': 40, 'image_height': 30,
        })
        assert response.status_code == 200, response.text
        metadata = response.json()['metadata']
        response = client.patch('/api/dataset/metadata/' + metadata['image_uuid'], json={
            'expected_revision': metadata['revision'], 'actor': 'Kim',
            'changes': {'workflow_state': 'approved'},
        })
        assert response.status_code == 200, response.text
    response = client.post('/api/project/labelsets', json={'name': 'Labeler B'})
    assert response.status_code == 200, response.text
    yield client, project, source, response.json()['id']


def _legacy_receipt(binding):
    """Model the actual no-gold JSON receipt stored by the pre-E05 binder."""
    receipt = Path(binding['version_dir']) / 'team-data.json'
    frozen = json.loads(receipt.read_text(encoding='utf-8'))
    frozen.pop('gold')
    raw = ('\n' + json.dumps(frozen, ensure_ascii=False, indent=4) + '\n').encode('utf-8')
    receipt.write_bytes(raw)
    legacy = copy.deepcopy(binding)
    legacy.update(team_data=frozen, team_data_sha256=_canonical_digest(frozen))
    return receipt, raw, legacy


def test_legacy_rebind_returns_a_valid_frozen_body_and_digest_without_rewriting(binding_workspace):
    _client, project, source, _candidate = binding_workspace
    original = bind_training_version(project, source)
    receipt, raw, legacy = _legacy_receipt(original)
    raw_digest = hashlib.sha256(raw).hexdigest()
    for _ in range(2):
        returned = bind_training_version(project, source, original['dataset_version_id'])
        validate_training_binding(returned)
        assert returned['team_data'] == legacy['team_data']
        assert returned['team_data_sha256'] == legacy['team_data_sha256']
        assert returned['dataset_version_id'] == original['dataset_version_id']
        assert receipt.read_bytes() == raw
        assert hashlib.sha256(receipt.read_bytes()).hexdigest() == raw_digest


def test_modern_validation_rejects_zero_gold_inclusion_drift_with_same_cohort(binding_workspace):
    _client, project, source, _candidate = binding_workspace
    binding = bind_training_version(project, source)
    assert binding['team_data']['gold'] == DEFAULT_GOLD
    validate_training_binding(binding)
    aq.set_gold_policy(project, True, 'Kim')
    current = team_data.training_binding(project, source)
    assert all(current[key] == binding['team_data'][key]
               for key in ('book_sha256', 'policy_sha256', 'eligibility_sha256'))
    with pytest.raises(ValueError, match='Team-data.*gold'):
        validate_training_binding(binding)


def _selected_binding(project, source, legacy):
    binding = bind_training_version(project, source)
    if legacy:
        _legacy_receipt(binding)
        binding = bind_training_version(project, source, binding['dataset_version_id'])
    return binding


def _gold_profile(project, source, candidate):
    return aq.create_profile(project, task='detection', reference_labelset='default',
                             candidate_labelset=candidate, gold_images=[str(source / 'a.png')])


def test_modern_gold_binding_validates_unchanged_and_preserves_the_receipt(binding_workspace):
    _client, project, source, candidate = binding_workspace
    _gold_profile(project, source, candidate)
    binding = bind_training_version(project, source)
    receipt = Path(binding['version_dir']) / 'team-data.json'
    raw = receipt.read_bytes()
    assert binding['team_data']['gold']['gold_images'] == 1
    assert [row['relative_path'] for row in binding['team_data']['eligibility']] == ['b.png']
    assert json.loads(raw) == binding['team_data']
    assert binding['team_data_sha256'] == _canonical_digest(json.loads(raw))
    validate_training_binding(binding)
    again = bind_training_version(project, source, binding['dataset_version_id'])
    validate_training_binding(again)
    assert again == binding and receipt.read_bytes() == raw


@pytest.mark.parametrize('change', ['nonempty', 'include', 'nongold_policy'])
def test_legacy_bind_refuses_changed_gold_or_team_policy(binding_workspace, change):
    _client, project, source, candidate = binding_workspace
    binding = bind_training_version(project, source)
    receipt, raw, _legacy = _legacy_receipt(binding)
    if change == 'nonempty':
        _gold_profile(project, source, candidate)
    elif change == 'include':
        aq.set_gold_policy(project, True, 'Kim')
    else:
        team_data.update_settings(project, source, 1, 'Kim', {'required_reviews': 2})
    # An editable manifest refusal can precede the team receipt comparison.
    with pytest.raises((ValueError, HTTPException)) as refused:
        bind_training_version(project, source, binding['dataset_version_id'])
    if isinstance(refused.value, HTTPException):
        assert refused.value.status_code == 409
    else:
        assert 'Team-data policy differs' in str(refused.value)
    assert receipt.read_bytes() == raw


@pytest.mark.parametrize('malformed', [
    {'include_gold_in_training': False, 'gold_set_sha256': EMPTY_GOLD_SHA256},
    {**DEFAULT_GOLD, 'gold_images': None},
    {**DEFAULT_GOLD, 'gold_images': False},
    {**DEFAULT_GOLD, 'gold_images': 0.0},
    {**DEFAULT_GOLD, 'gold_images': '0'},
    {**DEFAULT_GOLD, 'gold_set_sha256': '0' * 64},
    {'include_gold_in_training': False, 'gold_images': 0},
    {**DEFAULT_GOLD, 'include_gold_in_training': 0},
    {**DEFAULT_GOLD, 'unexpected': True},
    None,
], ids=['missing_count', 'null_count', 'bool_count', 'float_count', 'string_count',
        'wrong_digest', 'missing_digest', 'nonbool_policy', 'extra_field', 'null_gold'])
def test_legacy_admission_and_validation_refuse_malformed_gold(binding_workspace, monkeypatch, malformed):
    _client, project, source, _candidate = binding_workspace
    binding = bind_training_version(project, source)
    receipt, raw, legacy = _legacy_receipt(binding)
    # Real gold_receipt always produces a complete receipt. Corrupt its boundary
    # value while retaining the actual binder, metadata, version and validator.
    monkeypatch.setattr(aq, 'gold_receipt', lambda _scope: copy.deepcopy(malformed))
    with pytest.raises(ValueError, match='Team-data policy differs'):
        bind_training_version(project, source, binding['dataset_version_id'])
    with pytest.raises(ValueError, match='Team-data.*gold'):
        validate_training_binding(legacy)
    assert receipt.read_bytes() == raw


def test_pre_e05_bind_and_validation_accept_when_both_receipts_have_no_gold(binding_workspace, monkeypatch):
    _client, project, source, _candidate = binding_workspace
    real_training_binding = team_data.training_binding

    def pre_e05_binding(*args, **kwargs):
        value = real_training_binding(*args, **kwargs)
        value.pop('gold')
        return value

    # Model the legitimate producer before E05 without replacing strict checks.
    monkeypatch.setattr(team_data, 'training_binding', pre_e05_binding)
    binding = bind_training_version(project, source)
    assert 'gold' not in binding['team_data']
    validate_training_binding(binding)
    assert bind_training_version(project, source, binding['dataset_version_id']) == binding


def test_modern_validation_refuses_missing_current_gold(binding_workspace, monkeypatch):
    _client, project, source, _candidate = binding_workspace
    binding = bind_training_version(project, source)
    real_training_binding = team_data.training_binding

    def missing_gold(*args, **kwargs):
        current = real_training_binding(*args, **kwargs)
        current.pop('gold')
        return current

    monkeypatch.setattr(team_data, 'training_binding', missing_gold)
    with pytest.raises(ValueError, match='Team-data.*gold'):
        validate_training_binding(binding)


@pytest.mark.parametrize('malformed', [
    {**DEFAULT_GOLD, 'gold_images': False},
    {**DEFAULT_GOLD, 'gold_images': 0.0},
    {**DEFAULT_GOLD, 'include_gold_in_training': 0},
    {**DEFAULT_GOLD, 'gold_set_sha256': '0' * 64},
], ids=['bool_count', 'float_count', 'nonbool_policy', 'wrong_empty_digest'])
def test_modern_validation_refuses_malformed_current_gold(binding_workspace, monkeypatch, malformed):
    _client, project, source, _candidate = binding_workspace
    binding = bind_training_version(project, source)
    monkeypatch.setattr(aq, 'gold_receipt', lambda _scope: copy.deepcopy(malformed))
    with pytest.raises(ValueError, match='Team-data.*gold'):
        validate_training_binding(binding)


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', 'legacy'])
@pytest.mark.parametrize('already_excluded', [False, True], ids=['eligible_image', 'already_excluded_image'])
def test_validation_refuses_real_gold_membership_drift(binding_workspace, legacy, already_excluded):
    client, project, source, candidate = binding_workspace
    if already_excluded:
        response = client.get('/api/dataset/metadata/image', params={'image_path': str(source / 'a.png')})
        assert response.status_code == 200, response.text
        row = response.json()
        response = client.patch('/api/dataset/metadata/' + row['image_uuid'], json={
            'expected_revision': row['revision'], 'actor': 'Kim', 'changes': {'usage_state': 'not_used'},
        })
        assert response.status_code == 200, response.text
    binding = _selected_binding(project, source, legacy)
    validate_training_binding(binding)
    _gold_profile(project, source, candidate)
    current = team_data.training_binding(project, source)
    assert current['gold']['gold_images'] == 1
    if already_excluded:
        assert all(current[key] == binding['team_data'][key]
                   for key in ('book_sha256', 'policy_sha256', 'eligibility_sha256'))
    with pytest.raises(ValueError, match='Team-data'):
        validate_training_binding(binding)


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', 'legacy'])
def test_validation_refuses_effective_nongold_policy_changes(binding_workspace, legacy):
    _client, project, source, _candidate = binding_workspace
    binding = _selected_binding(project, source, legacy)
    validate_training_binding(binding)
    team_data.update_settings(project, source, 1, 'Kim', {'required_reviews': 2})
    with pytest.raises(ValueError, match='guidance, review policy or eligible cohort'):
        validate_training_binding(binding)


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', 'legacy'])
def test_validation_preserves_revision_only_team_policy_semantics(binding_workspace, legacy):
    _client, project, source, _candidate = binding_workspace
    binding = _selected_binding(project, source, legacy)
    team_data.update_settings(project, source, 1, 'Kim', {'required_reviews': 1})
    current = team_data.training_binding(project, source)
    assert current['settings']['revision'] != binding['team_data']['settings']['revision']
    assert current['policy_sha256'] == binding['team_data']['policy_sha256']
    validate_training_binding(binding)


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', 'legacy'])
@pytest.mark.parametrize('tamper', ['frozen_body', 'returned_body', 'returned_digest'])
def test_validation_keeps_exact_frozen_body_and_digest_checks(binding_workspace, legacy, tamper):
    _client, project, source, _candidate = binding_workspace
    binding = _selected_binding(project, source, legacy)
    validate_training_binding(binding)
    receipt = Path(binding['version_dir']) / 'team-data.json'
    if tamper == 'frozen_body':
        frozen = json.loads(receipt.read_bytes())
        frozen['book_version'] = 999
        receipt.write_text(json.dumps(frozen), encoding='utf-8')
    elif tamper == 'returned_body':
        binding['team_data']['book_version'] = 999
    else:
        binding['team_data_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='Team-data training receipt changed'):
        validate_training_binding(binding)


@pytest.mark.parametrize('legacy', [False, True], ids=['modern', 'legacy'])
def test_linked_receipt_is_refused_by_bind_and_validation(binding_workspace, legacy):
    _client, project, source, _candidate = binding_workspace
    binding = _selected_binding(project, source, legacy)
    receipt = Path(binding['version_dir']) / 'team-data.json'
    original = receipt.with_name('original-team-data.json')
    receipt.rename(original)
    receipt.symlink_to(original)
    with pytest.raises(ValueError, match='cannot be linked'):
        bind_training_version(project, source, binding['dataset_version_id'])
    with pytest.raises(ValueError, match='receipt is unavailable'):
        validate_training_binding(binding)


def test_missing_receipt_remains_unavailable(binding_workspace):
    _client, project, source, _candidate = binding_workspace
    binding = _selected_binding(project, source, True)
    (Path(binding['version_dir']) / 'team-data.json').unlink()
    with pytest.raises(ValueError, match='receipt is unavailable'):
        validate_training_binding(binding)


@pytest.mark.parametrize('change', ['project_identity', 'active_labelset', 'manifest', 'source_image', 'label_backup'])
def test_legacy_compatibility_preserves_other_frozen_provenance_checks(binding_workspace, change):
    _client, project, source, _candidate = binding_workspace
    if change == 'label_backup':
        (source / 'a.json').write_text(json.dumps({'imagePath': 'a.png', 'shapes': [],
                                                 'imageWidth': 40, 'imageHeight': 30}), encoding='utf-8')
    binding = _selected_binding(project, source, True)
    validate_training_binding(binding)
    directory = Path(binding['version_dir'])
    if change in {'project_identity', 'active_labelset'}:
        path = Path(project['project_dir']) / 'project.json'
        value = json.loads(path.read_bytes())
        value['id' if change == 'project_identity' else 'active_labelset_id'] = 'changed'
        path.write_text(json.dumps(value), encoding='utf-8')
    elif change == 'manifest':
        path = directory / 'manifest.json'
        value = json.loads(path.read_bytes())
        value['name'] = 'Tampered manifest'
        path.write_text(json.dumps(value), encoding='utf-8')
    elif change == 'source_image':
        Image.new('RGB', (40, 30), 'black').save(source / 'a.png')
    else:
        manifest = json.loads((directory / 'manifest.json').read_bytes())
        row = next(row for row in manifest['files']
                   if row['origin'] == 'source' and row['relative_path'] == 'a.json')
        (directory / row['snapshot_path']).write_bytes(b'Tampered backup')
    expected = {
        'project_identity': 'project identity changed', 'active_labelset': 'active labelset changed',
        'manifest': 'Bound training version manifest changed',
        'source_image': 'Training source image changed|eligible cohort changed',
        'label_backup': 'Bound label/split backup changed',
    }[change]
    with pytest.raises(ValueError, match=expected):
        validate_training_binding(binding)

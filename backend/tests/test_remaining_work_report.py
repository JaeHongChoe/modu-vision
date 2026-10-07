"""Scope relief must not become software verification or product acceptance."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def reporter():
    file = ROOT / 'scripts/remaining_work_report.py'
    assert file.is_file(), 'A read-only requested-scope reporter is required'
    spec = importlib.util.spec_from_file_location('remaining_work_report', file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def save(root, relative, value):
    file = root / relative
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return file


@pytest.fixture
def owned_inputs(tmp_path):
    program = json.loads((ROOT / 'docs/service-upgrade-program.json').read_text())
    # Freeze only this owned test's accounting; production progress may advance later.
    pending = {'S1-08', 'S5-01', 'S6-02', 'S6-03', 'S6-04', 'S6-05', 'S6-06',
               'S7-01', 'S7-02', 'S7-03', 'S7-04', 'S7-05', 'S7-06', 'S7-07', 'S7-08'}
    for row in program['requirements']:
        row['acceptance_state'] = {key: 'pending' for key in row['acceptance_state']}
        row['acceptance_state']['implementation'] = 'pending' if row['id'] in pending else 'verified'
        row['status'] = 'planned' if row['id'] in pending else 'verification_pending'
    program['execution']['progress']['parent_implementation'] = {'verified': 67, 'pending': 15}
    program['execution']['progress']['parent_status'] = {'planned': 15, 'verification_pending': 67}
    program['coverage_summary']['new_plan_accepted_work_packages'] = 0
    scope = {
        'schema_version': 1,
        'kind': 'requested_work_scope_only',
        'authority': {
            'kind': 'direct_user_instruction',
            'instruction': '원도우 테스트는 안해도 되니깐 남은 잡들 진행해죠',
            'recorded_on': '2026-10-07',
        },
        'decisions': [
            {'id': identifier, 'decision': 'waived_for_requested_work',
             'scope': 'windows_native_only', 'original_acceptance_preserved': True}
            for identifier in ('S6-02', 'S7-03')
        ],
    }
    # One real declaration with seven unexecuted scenarios. No test approval is invented.
    source = 'src/renderer/components/OwnedScopeControl.tsx'
    source_file = tmp_path / source
    source_file.parent.mkdir(parents=True)
    source_file.write_text('export const Control = () => <button>Owned control</button>;\n')
    actions = {
        'schema_version': 1,
        'kind': 'curated_action_evidence_not_complete_feature_acceptance',
        'sources': {source: hashlib.sha256(source_file.read_bytes()).hexdigest()},
        'records': [
            {'id': row['legacy_id'], 'actions': [],
             'remaining': 'Other actions and full feature acceptance remain pending.'}
            for row in program['legacy_coverage']
        ],
    }
    actions['records'][0]['actions'] = [{
        'id': 'owned-control', 'label': 'Owned control', 'source': source,
        'scenarios': {name: {'state': 'pending'} for name in
                      ('success', 'empty', 'invalid', 'error', 'cancel', 'reopen', 'handoff')},
    }]
    paths = {
        'program': save(tmp_path, 'docs/service-upgrade-program.json', program),
        'actions': save(tmp_path, 'docs/service-action-evidence.json', actions),
        'scope': save(tmp_path, 'docs/implementation-ledger/user-scope-decisions.json', scope),
    }
    return tmp_path, program, scope, actions, paths


def test_scope_separates_waived_work_from_original_pending_and_acceptance(owned_inputs):
    root, _, _, _, _ = owned_inputs
    report = reporter().build_report(root)
    assert report['ok'] is True
    assert report['original_program'] == {
        'work_packages': 82, 'legacy_features': 156,
        'implementation_verified': 67, 'implementation_pending': 15,
        'accepted_work_packages': 0,
    }
    assert report['requested_work']['waived_pending_parents'] == ['S6-02', 'S7-03']
    assert report['requested_work']['waived_pending_count'] == 2
    assert report['requested_work']['still_required_pending_count'] == 13
    assert set(report['requested_work']['still_required_pending_parents']).isdisjoint({'S6-02', 'S7-03'})
    assert report['action_evidence']['actions'] == 1
    assert report['action_evidence']['verified_scenarios'] == 0
    assert report['action_evidence']['pending_scenarios'] == 7
    assert report['action_evidence']['accepted_features'] == 0
    assert all(row['original_windows_native'] == 'pending' for row in report['scope_decisions'])
    assert all(row['external_conditions'] for row in report['remaining_parents'])
    assert any(row['software_followup'] for row in report['remaining_parents'])


def test_action_counts_come_from_actual_registry_not_remaining_prose(owned_inputs):
    root, program, _, actions, paths = owned_inputs
    program['requirements'][0]['remaining'] = ['999 actions and 999 verified scenarios']
    actions['records'][0]['actions'].append(copy.deepcopy(actions['records'][0]['actions'][0]))
    actions['records'][0]['actions'][-1]['id'] = 'second-owned-control'
    save(root, paths['program'].relative_to(root), program)
    save(root, paths['actions'].relative_to(root), actions)
    report = reporter().build_report(root)
    assert report['action_evidence']['actions'] == 2
    assert report['action_evidence']['pending_scenarios'] == 14


def test_valid_new_parent_progress_is_derived_without_hardcoded_remaining_counts(owned_inputs):
    root, program, _, _, paths = owned_inputs
    next(row for row in program['requirements'] if row['id'] == 'S6-03')['acceptance_state']['implementation'] = 'verified'
    program['execution']['progress']['parent_implementation'] = {'verified': 68, 'pending': 14}
    save(root, paths['program'].relative_to(root), program)
    report = reporter().build_report(root)
    assert report['original_program']['implementation_verified'] == 68
    assert report['original_program']['implementation_pending'] == 14
    assert report['requested_work']['waived_pending_count'] == 2
    assert report['requested_work']['still_required_pending_count'] == 12
    assert 'S6-03' not in report['requested_work']['still_required_pending_parents']
    assert report['original_program']['accepted_work_packages'] == 0


@pytest.mark.parametrize('bad', [66, True])
def test_stale_or_boolean_program_counts_are_rejected(owned_inputs, bad):
    root, program, _, _, paths = owned_inputs
    program['execution']['progress']['parent_implementation']['verified'] = bad
    save(root, paths['program'].relative_to(root), program)
    with pytest.raises(ValueError, match='parent_implementation'):
        reporter().build_report(root)


@pytest.mark.parametrize('mutation', ['extra_waiver', 'pass', 'authority', 'extra_pass_field',
                                      'boolean_version', 'duplicate_waiver', 'malformed_id'])
def test_broader_forged_or_relabelled_waivers_are_rejected(owned_inputs, mutation):
    root, _, scope, _, paths = owned_inputs
    if mutation == 'extra_waiver':
        extra = copy.deepcopy(scope['decisions'][0]); extra['id'] = 'S7-06'
        scope['decisions'].append(extra)
    elif mutation == 'pass':
        scope['decisions'][0]['decision'] = 'verified'
    elif mutation == 'authority':
        scope['authority']['instruction'] = 'All acceptance and quality checks are waived.'
    elif mutation == 'extra_pass_field':
        scope['decisions'][0]['windows_native'] = 'verified'
    elif mutation == 'duplicate_waiver':
        scope['decisions'][1]['id'] = 'S6-02'
    elif mutation == 'malformed_id':
        scope['decisions'][0]['id'] = ['S6-02', 'S7-06']
    else:
        scope['schema_version'] = True
    save(root, paths['scope'].relative_to(root), scope)
    with pytest.raises(ValueError, match='scope'):
        reporter().build_report(root)


def test_scope_report_rejects_an_original_parent_relabelled_accepted(owned_inputs):
    root, program, _, _, paths = owned_inputs
    next(row for row in program['requirements'] if row['id'] == 'S7-03')['status'] = 'accepted'
    program['execution']['progress']['parent_status']['planned'] -= 1
    program['execution']['progress']['parent_status']['accepted'] = 1
    program['coverage_summary']['new_plan_accepted_work_packages'] = 1
    save(root, paths['program'].relative_to(root), program)
    with pytest.raises(ValueError, match='accepted'):
        reporter().build_report(root)


@pytest.mark.parametrize('target', ['requirements', 'legacy_coverage'])
def test_original_scope_cannot_drop_or_duplicate_ids(owned_inputs, target):
    root, program, _, _, paths = owned_inputs
    program[target][-1] = copy.deepcopy(program[target][0])
    save(root, paths['program'].relative_to(root), program)
    with pytest.raises(ValueError, match='scope'):
        reporter().build_report(root)


def test_report_is_read_only_and_does_not_hide_stale_action_source(owned_inputs):
    root, _, _, actions, paths = owned_inputs
    source_file = root / next(iter(actions['sources']))
    source_file.write_text('export const changed = true;\n')
    before = {name: path.read_bytes() for name, path in paths.items()}
    report = reporter().build_report(root)
    assert report['ok'] is False
    assert report['action_evidence']['ok'] is False
    assert any('source bytes changed' in error for error in report['action_evidence']['errors'])
    assert report['original_program']['accepted_work_packages'] == 0
    assert {name: path.read_bytes() for name, path in paths.items()} == before
    for name, raw in before.items():
        assert report['inputs'][name]['sha256'] == hashlib.sha256(raw).hexdigest()

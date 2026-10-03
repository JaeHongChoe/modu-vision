"""S7-01: every legacy feature (F001-F123, U001-U033) carries one RequirementEvidence record, and no legacy claim is
promoted to accepted without verified evidence for every dimension, native Windows included.

The gate is scripts/check_service_plan.py, which CI runs on Linux and Windows.
"""
import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('service_plan_gate_s701', ROOT / 'scripts/check_service_plan.py')
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)
SHA = '03be617f73557edf0c5579677766d03e33b4db7e'
ROW_F001 = '| F001 | 자연어 조건으로 대상 영역 찾기 | S3-06, S3-05, S7-01 | pending |'


def _program():
    return json.loads((ROOT / 'docs/service-upgrade-program.json').read_text(encoding='utf-8'))


def _evidence():
    return json.loads((ROOT / 'docs/service-upgrade-evidence.json').read_text(encoding='utf-8'))


def _coverage():
    return (ROOT / 'docs/service-upgrade-coverage.md').read_text(encoding='utf-8')


def _errors(program=None, evidence=None, coverage=None):
    return gate.check_requirement_evidence(program or _program(), evidence if evidence is not None else _evidence(), ROOT,
                                           coverage or _coverage())


def _entry(**changes):
    entry = {'task': 'S7-01', 'source_sha': SHA, 'kind': 'click', 'action': 'save the flow, reload, run',
             'expected': 'threshold 8 kept', 'observed': 'threshold 8 saved, reopened and used by an actual run',
             'reviewer': 'independent review s008d', 'test': 'scripts/e2e/service-s0-08.spec.ts'}
    entry.update(changes)
    return {key: value for key, value in entry.items() if value is not None}


def _verified(**changes):
    return {'state': 'verified', 'evidence': [_entry(**changes)]}


def test_the_repository_has_one_pending_record_per_legacy_feature_and_nothing_promoted():
    receipt = gate.check_program(_program(), ROOT)
    assert receipt['ok'], receipt['errors']
    records = _evidence()['records']
    assert len(records) == 156 == len({record['id'] for record in records})
    assert {record['id'] for record in records} == {row['legacy_id'] for row in _program()['legacy_coverage']}
    assert receipt['accepted_legacy_rows'] == 0, 'no old registry claim counts as service acceptance'


def test_check_program_runs_the_evidence_rules_and_refuses_an_unreadable_registry(tmp_path):
    broken = _evidence()
    broken['records'][0]['gui'] = {'state': 'done'}
    assert any('F001.gui' in error for error in gate.check_program(_program(), ROOT, evidence=broken)['errors'])
    assert any('schema_version 1' in error for error in gate.check_program(_program(), ROOT, evidence=['not', 'a', 'registry'])['errors'])
    import shutil
    shutil.copytree(ROOT / 'docs', tmp_path / 'docs', ignore=shutil.ignore_patterns('service-upgrade-evidence.json'))
    missing = gate.check_program(_program(), tmp_path)['errors']
    assert any('requirement evidence could not be read' in error for error in missing), missing


@pytest.mark.parametrize('change', ['drop', 'duplicate', 'extra', 'not_an_object'])
def test_a_lost_duplicated_unknown_or_malformed_record_fails(change):
    evidence = _evidence()
    if change == 'drop':
        evidence['records'].pop(5)
    elif change == 'duplicate':
        evidence['records'].append(copy.deepcopy(evidence['records'][0]))
    elif change == 'extra':
        evidence['records'].append({**copy.deepcopy(evidence['records'][0]), 'id': 'F999'})
    else:
        evidence['records'][3] = 'F004'
    assert any('one record per legacy ID' in error for error in _errors(evidence=evidence))


@pytest.mark.parametrize('dimension, value, message', [
    ('gui', {'state': 'done'}, 'state must be'),
    ('gui', {'state': ['verified']}, 'state must be'),
    ('gui', 'verified', 'state must be'),
    ('gui', {'state': 'verified'}, 'verified needs evidence'),
    ('gui', {'state': 'not_required'}, 'at least 10 characters and its reviewer'),
    ('gui', {'state': 'not_required', 'reason': 'x', 'reviewer': 'review s701s3'}, 'at least 10 characters'),
    ('gui', {'state': 'not_required', 'reason': 'no screen is involved here', 'by': 'me'}, 'unknown fields'),
    ('gui', {'state': 'not_required', 'reason': 'no screen is involved here'}, 'its reviewer'),
    ('implementation', {'state': 'not_required', 'reason': 'no implementation is needed here'}, 'cannot be not_required'),
    ('gui', _verified(task='S9-99'), 'unknown task'),
    ('gui', _verified(task=['S7-01']), 'unknown task'),
    ('gui', _verified(task='S6-05'), 'does not own this legacy feature'),
    ('gui', _verified(source_sha='03be617'), 'full 40-character'),
    ('gui', _verified(source_sha='main'), 'full 40-character'),
    ('gui', _verified(kind='unit'), 'kind for gui'),
    ('gui', _verified(kind=['click']), 'kind for gui'),
    ('target', _verified(kind='unit'), 'kind for target'),
    ('persist', _verified(kind='screenshot'), 'kind for persist'),
    ('gui', _verified(observed=' '), 'observed is required'),
    ('gui', _verified(action=None), 'action is required'),
    ('gui', _verified(expected=None), 'expected is required'),
    ('gui', _verified(reviewer=None), 'reviewer is required'),
    ('gui', _verified(test=None), 'a test or a receipt'),
    ('gui', _verified(test='backend/tests/test_missing.py::test_x'), 'does not exist'),
    ('gui', _verified(test='/etc/hosts'), 'must be a repository path'),
    ('gui', _verified(test='backend/tests/../../README.md'), 'must be a repository path'),
    ('gui', _verified(test='README.md'), 'must be a repository path'),
    ('gui', _verified(test='docs/service-upgrade-evidence.json'), 'must be a repository path'),
    ('gui', _verified(test='src/renderer/App.tsx'), '.test.cjs'),
    ('gui', _verified(test='Scripts/e2e/service-s0-08.spec.ts'), 'must be a repository path'),
    ('gui', _verified(test='scripts/e2e/Service-s0-08.spec.ts'), 'does not exist'),
    ('gui', _verified(test=None, receipt='s008d-e2e'), 'receipt_sha256'),
    ('gui', _verified(test=None, receipt=True, receipt_sha256='0' * 64), 'receipt'),
    ('gui', _verified(artifacts=[{'id': 'model'}]), 'artifacts must be'),
    ('gui', _verified(note='x'), 'unknown fields'),
    ('gui', _verified(test=None, receipt='/Users/someone/receipt.txt', receipt_sha256='0' * 64), 'plain name'),
    ('gui', _verified(test='backend/tests/test_service_s0_08.py'), 'click evidence cites a scripts/e2e spec'),
    ('persist', _verified(task='S6-05', kind='api', test='backend/tests/test_service_s0_08.py'), 'does not own this legacy feature'),
    ('target', _verified(kind='target', task='S0-08'), 'target evidence needs a receipt'),
    ('target', _verified(kind='target', task='S0-08', test=None, receipt='device-run', receipt_sha256='c' * 64), 'target evidence needs a receipt'),
])
def test_a_dimension_needs_a_valid_state_and_real_evidence(dimension, value, message):
    evidence = _evidence()
    evidence['records'][0][dimension] = value
    errors = _errors(evidence=evidence)
    assert any(message in error for error in errors), errors


def test_well_formed_evidence_of_an_owner_task_is_accepted_for_its_dimension():
    evidence = _evidence()
    record = evidence['records'][0]
    record['gui'] = _verified(artifacts=[{'id': 'job_0001', 'sha256': 'a' * 64}])
    record['persist'] = _verified(kind='api', test='backend/tests/test_service_s0_08.py::test_the_command_line_runs_one_defect_for_real_and_writes_its_record')
    record['failure'] = _verified(kind='unit', test=None, receipt='s008d-green', receipt_sha256='b' * 64)
    record['target'] = _verified(kind='target', task='S0-08', test=None, receipt='line-3-camera-run', receipt_sha256='c' * 64,
                                 artifacts=[{'id': 'run_0007', 'sha256': 'd' * 64}])  # platform and target evidence may come from any task
    assert _errors(evidence=evidence) == []


def test_native_windows_is_required_needs_its_own_evidence_and_cannot_be_waived():
    evidence = _evidence()
    record = evidence['records'][0]
    for platform, message in (({}, 'windows_native is required'), ('windows', 'windows_native is required'),
                              ({'windows_native': 'verified'}, 'state must be'),
                              ({'windows_native': {'state': 'verified'}}, 'verified needs evidence'),
                              ({'windows_native': _verified()}, 'kind for windows_native'),
                              ({'windows_native': _verified(kind='native_windows')}, 'a test the Windows workflow runs'),
                              ({'windows_native': {'state': 'not_required', 'reason': 'tested on macOS only'}}, 'cannot be not_required')):
        record['platform'] = platform
        assert any(message in error for error in _errors(evidence=evidence)), (platform, _errors(evidence=evidence))


def test_a_claimed_acceptance_without_evidence_fails_and_full_evidence_derives_it():
    program, evidence, coverage = _program(), _evidence(), _coverage()
    program['legacy_coverage'][0]['service_acceptance'] = 'accepted'
    assert any("service_acceptance 'accepted' differs from its evidence (pending)" in error for error in _errors(program=program))
    record = evidence['records'][0]
    assert record['id'] == 'F001'
    record['platform']['windows_native'] = _verified(kind='native_windows', test='backend/tests/test_service_s1_09.py')
    assert gate.derived_acceptance(record) == 'pending', 'native Windows alone accepts nothing'
    for name in gate.FUNCTIONAL:
        record[name] = _verified(kind='click' if name == 'gui' else 'api')
    record['target'] = {'state': 'not_required', 'reason': 'no equipment is involved in this feature', 'reviewer': 'review s701s3'}
    assert gate.derived_acceptance(record) == 'accepted'
    errors = _errors(program=program, evidence=evidence)
    assert any('coverage.md shows' in error and 'F001' in error for error in errors), 'the coverage table must say accepted too'
    shown = coverage.replace(ROW_F001, ROW_F001.replace('| pending |', '| accepted |'))
    assert _errors(program=program, evidence=evidence, coverage=shown) == []
    record['platform']['windows_native'] = {'state': 'pending'}
    assert gate.derived_acceptance(record) == 'pending', 'no acceptance without native Windows'


def test_unknown_fields_and_bad_prerequisites_are_refused():
    evidence = _evidence()
    evidence['records'][0]['quality'] = {'state': 'verified'}
    evidence['records'][1]['prerequisites'] = ['GPU', ' ']
    errors = _errors(evidence=evidence)
    assert any('unknown fields' in error and 'F001' in error for error in errors)
    assert any('F002.prerequisites' in error for error in errors)


def test_only_the_main_coverage_table_counts_and_it_keeps_every_owner_once():
    coverage = _coverage()
    changed = coverage.replace('| F022 | Train·Test·미사용·미분할 분포 | S3-07 | pending |', '| F022 | Train·Test·미사용·미분할 분포 | S3-03 | pending |')
    assert any('F022' in error and 'owners' in error for error in _errors(coverage=changed))
    lost = coverage.replace('| F022 | Train·Test·미사용·미분할 분포 | S3-07 | pending |\n', '')
    assert any('every legacy ID once' in error for error in _errors(coverage=lost))
    duplicated = coverage.replace(ROW_F001, ROW_F001 + '\n' + ROW_F001)
    assert any('every legacy ID once' in error for error in _errors(coverage=duplicated))
    unknown = coverage.replace(ROW_F001, ROW_F001 + '\n| F124 | 없는 기능 | S7-01 | pending |')
    assert any('every legacy ID once' in error for error in _errors(coverage=unknown))
    copy_elsewhere = coverage + '\n## 예시\n\n| 기존 ID | 기능 | 담당 task | 새 검증 |\n| --- | --- | --- | --- |\n' + ROW_F001.replace('pending', 'accepted') + '\n'
    assert _errors(coverage=copy_elsewhere) == [], 'a copy outside the main table is not read'
    headless = coverage.replace('## 모든 기존 항목', '## 기존 항목 목록')
    assert any("the table '## 모든 기존 항목' is missing" in error for error in _errors(coverage=headless))
    korean = coverage.replace(ROW_F001, ROW_F001.replace('| pending |', '| 완료 |'))
    assert any("shows '완료'" in error for error in _errors(coverage=korean))


def test_the_gate_prints_only_ascii_so_a_windows_console_cannot_fail_it():
    done = subprocess.run([sys.executable, str(ROOT / 'scripts/check_service_plan.py')], capture_output=True, cwd=ROOT,
                          env={'PYTHONIOENCODING': 'cp1252', 'PATH': ''}, timeout=120)
    assert done.returncode == 0, done.stderr.decode('utf-8', 'replace')
    assert done.stdout.isascii()


def test_malformed_ids_and_a_boolean_schema_version_give_errors_not_tracebacks():
    evidence = _evidence()
    evidence['records'][0] = {**evidence['records'][0], 'id': ['F001']}
    evidence['records'][1] = {**evidence['records'][1], 'id': {'id': 'F002'}}
    errors = _errors(evidence=evidence)
    assert any('not objects with a string id' in error for error in errors)
    flagged = _evidence()
    flagged['schema_version'] = True
    assert any('schema_version 1' in error for error in _errors(evidence=flagged))


def test_the_gate_prints_ascii_even_when_an_error_names_a_korean_title(monkeypatch, capsys):
    monkeypatch.setattr(gate, 'check_program', lambda program, root: {'ok': False, 'errors': ['F001: 자연어 조건으로 대상 영역 찾기']})
    monkeypatch.setattr(sys, 'argv', ['check_service_plan.py'])
    assert gate.main() == 1
    printed = capsys.readouterr().out
    assert printed.isascii() and '\\uc790' in printed, 'escaped, so a cp1252 console can print it'

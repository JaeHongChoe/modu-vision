"""S7-01: every legacy feature (F001-F123, U001-U033) carries one RequirementEvidence record, and no legacy claim is
promoted to accepted without verified evidence for every dimension, native Windows included.

The gate is scripts/check_service_plan.py, which CI runs on Linux and Windows.
"""
import copy
import hashlib
import importlib.util
import json
import os
import shutil
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


def _errors(program=None, evidence=None, coverage=None, root=ROOT):
    return gate.check_requirement_evidence(program or _program(), evidence if evidence is not None else _evidence(), root,
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


def test_hashed_receipt_bytes_survive_a_git_checkout_with_autocrlf(tmp_path):
    """Exercise Git's checkout conversion, including on a non-Windows host."""
    receipt = Path('docs/verification/receipts/sdk-windows-8917.json')
    original = (ROOT / receipt).read_bytes()
    repository = tmp_path / 'isolated-receipt-checkout'
    repository.mkdir()
    command = ['git', '-c', 'core.autocrlf=true', '-c', 'core.hooksPath=' + str(tmp_path / 'no-hooks')]
    def git(*arguments):
        subprocess.run(command + list(arguments), cwd=repository, check=True, capture_output=True)
    git('init')
    attributes = ROOT / '.gitattributes'
    if attributes.exists():
        (repository / '.gitattributes').write_bytes(attributes.read_bytes())
    path = repository / receipt
    path.parent.mkdir(parents=True)
    path.write_bytes(original)
    git('add', '--', receipt.as_posix(), *(['.gitattributes'] if attributes.exists() else []))
    path.unlink()  # only the newly created test-owned copy
    git('checkout-index', '--force', '--', receipt.as_posix())
    assert path.read_bytes() == original, 'Git checkout must preserve the bytes cited by receipt_sha256'


def test_missing_or_wrong_source_receipt_cannot_verify_a_dimension(tmp_path):
    entry=_entry(kind='unit',test=None,receipt='not-retained.json',receipt_sha256='b'*64)
    assert any('receipt file' in x for x in gate._reference_errors('F001.failure',entry,'unit',ROOT))
    import hashlib
    receipts=tmp_path/'docs/verification/receipts';receipts.mkdir(parents=True)
    path=receipts/'other-source.json';path.write_text(json.dumps({'source_sha':'f'*40}))
    entry.update(receipt=path.name,receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert any('receipt source' in x for x in gate._reference_errors('F001.failure',entry,'unit',tmp_path))


def test_receipt_hash_and_link_are_checked_before_use(tmp_path):
    import hashlib
    receipts=tmp_path/'docs/verification/receipts';receipts.mkdir(parents=True)
    path=receipts/'actual.json';path.write_text(json.dumps({'source_sha':SHA}))
    entry=_entry(kind='unit',test=None,receipt=path.name,receipt_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert gate._reference_errors('F001.failure',entry,'unit',tmp_path)==[]
    path.write_text(json.dumps({'source_sha':SHA,'changed':True}))
    assert any('receipt hash' in x for x in gate._reference_errors('F001.failure',entry,'unit',tmp_path))
    path.unlink();outside=tmp_path/'outside.json';outside.write_text(json.dumps({'source_sha':SHA}));path.symlink_to(outside)
    entry['receipt_sha256']=hashlib.sha256(outside.read_bytes()).hexdigest()
    assert any('linked receipt' in x for x in gate._reference_errors('F001.failure',entry,'unit',tmp_path))


def test_nonexistent_python_case_and_foreign_target_owner_are_refused():
    assert any('test selector' in x for x in gate._test_file_errors('F001.failure','backend/tests/test_service_s7_01.py::test_not_a_real_case',ROOT))
    record=_verified(task='S0-08',kind='target',test=None,receipt='not-retained.json',receipt_sha256='b'*64,artifacts=[{'id':'synthetic','sha256':'a'*64}])
    errors=gate._dimension_errors('F001.target',record,'target',['S7-01'],{r['id'] for r in _program()['requirements']},ROOT)
    assert any('does not own' in x for x in errors)


@pytest.mark.parametrize('payload, message', [
    (b'{"source_sha":"' + SHA.encode() + b'","source_sha":"' + SHA.encode() + b'"}', 'unique-key JSON'),
    (b'[]', 'unique-key JSON'),
    (b'not JSON', 'unique-key JSON'),
    (b' ' * (1024 * 1024 + 1), 'size bound'),
], ids=['duplicate-source', 'array', 'invalid-json', 'oversize'])
def test_matching_hash_does_not_admit_malformed_or_oversized_receipts(tmp_path, payload, message):
    path = tmp_path / 'docs/verification/receipts/rejected.json'
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    entry = _entry(kind='unit', test=None, receipt=path.name, receipt_sha256=hashlib.sha256(payload).hexdigest())
    assert any(message in error for error in gate._reference_errors('F001.failure', entry, 'unit', tmp_path))


def test_native_platform_evidence_also_requires_a_feature_owner():
    value = _verified(task='S0-08', kind='native_windows', test=None,
                      receipt='sdk-windows-8917.json',
                      receipt_sha256=hashlib.sha256((ROOT / 'docs/verification/receipts/sdk-windows-8917.json').read_bytes()).hexdigest())
    value['evidence'][0]['source_sha'] = '8917d8ad90dccd79aa98b338cf3652d6cc869a46'
    errors = gate._dimension_errors('F001.platform.windows_native', value, 'windows_native', ['S7-01'],
                                    {row['id'] for row in _program()['requirements']}, ROOT)
    assert any('does not own' in error for error in errors)


def test_a_python_helper_or_nested_function_is_not_a_collected_case(tmp_path):
    path = tmp_path / 'backend/tests/test_fixture.py'
    path.parent.mkdir(parents=True)
    path.write_text('def helper():\n    pass\n\ndef test_outer():\n    def test_inner():\n        pass\n\n'
                    'class TestExample:\n    async def test_method(self):\n        pass\n', encoding='utf-8')
    for selector in ('helper', 'test_outer::test_inner'):
        errors = gate._test_file_errors('F001.failure', f'backend/tests/test_fixture.py::{selector}', tmp_path)
        assert any('test selector' in error for error in errors), errors
    assert gate._test_file_errors('F001.failure', 'backend/tests/test_fixture.py::TestExample::test_method[value]', tmp_path) == []


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


def test_well_formed_evidence_of_an_owner_task_is_accepted_for_its_dimension(tmp_path):
    for name in ('backend/tests/test_runtime_deadline_sdk.py','backend/tests/test_service_s0_08.py','scripts/e2e/service-s0-08.spec.ts'):
        target=tmp_path/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,target)
    receipts=tmp_path/'docs/verification/receipts';receipts.parent.mkdir(parents=True)
    shutil.copytree(ROOT/'docs/verification/receipts',receipts)
    hashes={}
    for name in ('s008d-green','line-3-camera-run'):
        path=receipts/name;path.write_text(json.dumps({'source_sha':SHA,'scope':'Controlled format-rule fixture, not operational acceptance'}))
        hashes[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = _evidence()
    record = evidence['records'][0]
    record['gui'] = _verified(artifacts=[{'id': 'job_0001', 'sha256': 'a' * 64}])
    record['persist'] = _verified(kind='api', test='backend/tests/test_service_s0_08.py::test_the_command_line_runs_one_defect_for_real_and_writes_its_record')
    record['failure'] = _verified(kind='unit', test=None, receipt='s008d-green', receipt_sha256=hashes['s008d-green'])
    record['target'] = _verified(kind='target', task='S7-01', test=None, receipt='line-3-camera-run', receipt_sha256=hashes['line-3-camera-run'],
                                 artifacts=[{'id': 'run_0007', 'sha256': 'd' * 64}])
    assert _errors(evidence=evidence,root=tmp_path) == []


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


@pytest.fixture
def gate_root(tmp_path):
    """Only public gate inputs and phase plans; the real CLI uses this root without app imports."""
    shutil.copytree(ROOT / 'docs', tmp_path / 'docs')
    return tmp_path


def _run_gate(root):
    # Keep pytest's isolated store variables, interpreter paths and the caller's environment.
    return subprocess.run([sys.executable, str(ROOT / 'scripts/check_service_plan.py'), '--root', str(root)],
                          capture_output=True, cwd=ROOT, env={**os.environ, 'PYTHONIOENCODING': 'cp1252'}, timeout=120)


def _assert_cli_input_error(done, message):
    assert done.returncode == 1, done.stdout.decode('ascii', 'replace') + done.stderr.decode('ascii', 'replace')
    assert done.stdout.isascii() and done.stderr.isascii()
    assert b'Traceback' not in done.stderr
    receipt = json.loads(done.stdout)
    assert receipt['ok'] is False and any(message in error for error in receipt['errors']), receipt


@pytest.mark.parametrize('filename, duplicate', [
    ('service-upgrade-program.json', '"coverage_summary": {"mapped_ids": 0},'),
    ('service-upgrade-evidence.json', '"schema_version": 0,'),
    ('feature-program.json', '"features": [],'),
    ('product-upgrade-program.json', '"requirements": [],'),
])
def test_the_cli_refuses_duplicate_json_keys_in_every_gate_input(gate_root, filename, duplicate):
    path = gate_root / 'docs' / filename
    source = path.read_text(encoding='utf-8')
    path.write_text(source.replace('{', '{' + duplicate, 1), encoding='utf-8')
    # json.loads would keep the later, valid value and let the overwritten bad claim disappear.
    _assert_cli_input_error(_run_gate(gate_root), 'duplicate JSON key')


@pytest.mark.parametrize('filename, original, duplicate', [
    ('service-upgrade-program.json', '"status": "planned"', '"status": "accepted", "status": "planned"'),
    ('service-upgrade-evidence.json', '"state": "pending"', '"state": "verified", "state": "pending"'),
])
def test_the_cli_refuses_nested_duplicate_claims(gate_root, filename, original, duplicate):
    path = gate_root / 'docs' / filename
    source = path.read_text(encoding='utf-8')
    assert original in source
    path.write_text(source.replace(original, duplicate, 1), encoding='utf-8')
    _assert_cli_input_error(_run_gate(gate_root), 'duplicate JSON key')


def test_a_duplicate_korean_key_is_an_ascii_cli_error(gate_root):
    path = gate_root / 'docs/service-upgrade-program.json'
    source = path.read_text(encoding='utf-8')
    path.write_text(source.replace('{', '{"중복": 0, "\\uC911\\uBCF5": 1,', 1), encoding='utf-8')
    _assert_cli_input_error(_run_gate(gate_root), 'duplicate JSON key')


@pytest.mark.parametrize('filename', [
    'service-upgrade-program.json', 'service-upgrade-evidence.json',
    'feature-program.json', 'product-upgrade-program.json',
])
@pytest.mark.parametrize('invalid', ['{"broken":', '[]'])
def test_malformed_json_inputs_are_failed_cli_receipts_without_tracebacks(gate_root, filename, invalid):
    (gate_root / 'docs' / filename).write_text(invalid, encoding='utf-8')
    _assert_cli_input_error(_run_gate(gate_root), 'could not be read')


@pytest.mark.parametrize('field, invalid', [('requirements', None), ('requirements', [None]), ('coverage_summary', [])])
def test_malformed_program_structure_is_a_failed_cli_receipt(gate_root, field, invalid):
    path = gate_root / 'docs/service-upgrade-program.json'
    program = json.loads(path.read_text(encoding='utf-8'))
    program[field] = invalid
    path.write_text(json.dumps(program), encoding='utf-8')
    _assert_cli_input_error(_run_gate(gate_root), 'could not be read')


@pytest.mark.parametrize('filename, key', [('feature-program.json', 'features'), ('product-upgrade-program.json', 'requirements')])
def test_check_program_refuses_duplicate_baseline_keys(gate_root, filename, key):
    path = gate_root / 'docs' / filename
    path.write_text(path.read_text(encoding='utf-8').replace('{', '{"' + key + '": [],', 1), encoding='utf-8')
    receipt = gate.check_program(_program(), gate_root)
    assert not receipt['ok'] and any('duplicate JSON key' in error for error in receipt['errors']), receipt


def test_check_program_refuses_a_nested_duplicate_evidence_state(gate_root):
    path = gate_root / 'docs/service-upgrade-evidence.json'
    path.write_text(path.read_text(encoding='utf-8').replace('"state": "pending"', '"state": "verified", "state": "pending"', 1),
                    encoding='utf-8')
    receipt = gate.check_program(_program(), gate_root)
    assert not receipt['ok'] and any('duplicate JSON key' in error for error in receipt['errors']), receipt


@pytest.mark.parametrize('row', [
    '|F001|natural language|S3-06, S3-05, S7-01|pending|',
    '  |\tF001\t| natural language | S3-06, S3-05, S7-01 | pending |  ',
    '| `F001` | natural language | S3-06, S3-05, S7-01 | pending |',
    'F001 | natural language | S3-06, S3-05, S7-01 | pending',
])
def test_markdown_spacing_and_backtick_ids_do_not_lose_a_main_table_row(row):
    assert _errors(coverage=_coverage().replace(ROW_F001, row)) == []


@pytest.mark.parametrize('row', [
    '|F001|duplicate|S3-06, S3-05, S7-01|pending|',
    ' | `F001` | duplicate | S3-06, S3-05, S7-01 | pending | ',
    '|U001|duplicate|S7-01|pending|',
    '| `F999` | unknown | S7-01 | pending |',
])
def test_variant_duplicate_or_extra_main_table_rows_are_refused(row):
    coverage = _coverage().replace(ROW_F001, ROW_F001 + '\n' + row)
    assert any('every legacy ID once' in error for error in _errors(coverage=coverage))


@pytest.mark.parametrize('row', [
    '| F001 | missing state | S7-01 |',
    '| F001 | too many | S7-01 | pending | extra |',
    '| `F001 | broken code span | S7-01 | pending |',
    '| X001 | not a legacy ID | S7-01 | pending |',
])
def test_malformed_extra_main_table_rows_cannot_be_silently_skipped(row):
    coverage = _coverage().replace(ROW_F001, ROW_F001 + '\n' + row)
    assert any('malformed main table row' in error for error in _errors(coverage=coverage))


def test_a_malformed_main_table_row_fails_the_actual_cli(gate_root):
    path = gate_root / 'docs/service-upgrade-coverage.md'
    path.write_text(path.read_text(encoding='utf-8').replace(ROW_F001, ROW_F001 + '\n| `F001 | bad | S7-01 | pending |'),
                    encoding='utf-8')
    _assert_cli_input_error(_run_gate(gate_root), 'malformed main table row')

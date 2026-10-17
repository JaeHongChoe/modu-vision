"""A changed current GUI test cannot rebind an older executed receipt."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('historical_gui_gate', ROOT / 'scripts/check_service_plan.py')
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)
TEST = 'scripts/e2e/owned.spec.ts::actual save and reopen'
HISTORY = 'docs/verification/gui-test-source-history.json'


def fixture(root):
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
    git('init', '-q')
    file = root / TEST.split('::')[0]
    file.parent.mkdir(parents=True)
    file.write_text("test('actual save and reopen',async()=>{});\n")
    old_sha = hashlib.sha256(file.read_bytes()).hexdigest()
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '-qm', 'retained public test')
    anchor = git('rev-parse', 'HEAD')
    # Execution source remains its own historical identity, not the public spec anchor.
    record = {'schema': 'modu-vision.gui-execution/v1', 'source_sha': 'a' * 40, 'source_dirty': False,
              'test': TEST, 'test_source_sha256': old_sha, 'mode': 'browser',
              'platform': {'os': 'darwin', 'arch': 'arm64'}, 'status': 'passed',
              'expected_status': 'passed', 'result': 'expected', 'attempts': 1,
              'report_sha256': 'b' * 64, 'harness_sha256': 'c' * 64,
              'artifacts': [{'id': 'screen', 'sha256': 'd' * 64}]}
    receipt = root / 'docs/verification/receipts/owned.json'
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(record))
    entry = {'kind': 'click', 'source_sha': record['source_sha'], 'test': TEST,
             'receipt': receipt.name, 'receipt_sha256': hashlib.sha256(receipt.read_bytes()).hexdigest()}
    file.write_text(file.read_text() + '// Current source declaration changed; execution is still pending.\n')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '-qm', 'current test declaration')
    declaration = {'receipt': receipt.name, 'receipt_sha256': entry['receipt_sha256'],
                   'execution_source_sha': record['source_sha'], 'test': TEST,
                   'recorded_test_source_sha256': old_sha, 'retained_spec_commit': anchor,
                   'current_test_source_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                   'reason': 'Preserved public spec bytes prove only the historical test declaration; no current GUI execution.'}
    history = root / HISTORY
    value = {'schema': 'modu-vision.gui-test-source-history/v1',
             'scope': 'historical_test_declarations_only_no_new_execution', 'declarations': [declaration]}
    def write():
        history.write_text(json.dumps(value))
    write()
    return entry, record, receipt, file, declaration, value, write, git


def test_explicit_ancestor_blob_preserves_old_receipt_without_current_execution_credit(tmp_path):
    entry, record, receipt, file, declaration, _, _, _ = fixture(tmp_path)
    original = receipt.read_bytes()
    assert hashlib.sha256(file.read_bytes()).hexdigest() != record['test_source_sha256']
    assert declaration['retained_spec_commit'] != record['source_sha']
    assert not gate._reference_errors('F064.gui', entry, 'click', tmp_path)
    assert receipt.read_bytes() == original
    assert record['source_sha'] == entry['source_sha'] == 'a' * 40


@pytest.mark.parametrize('damage', ['missing', 'receipt_bytes', 'recorded_sha', 'replacement_bytes',
                                  'unknown_commit', 'wrong_blob', 'descendant', 'other_receipt',
                                  'unknown_field', 'duplicate_declaration'])
def test_history_never_bypasses_exact_receipt_current_bytes_or_ancestor_blob(tmp_path, damage):
    entry, record, receipt, file, declaration, value, write, git = fixture(tmp_path)
    if damage == 'missing':
        (tmp_path / HISTORY).unlink()
    elif damage == 'receipt_bytes':
        record['report_sha256'] = 'e' * 64
        receipt.write_text(json.dumps(record))
        entry['receipt_sha256'] = hashlib.sha256(receipt.read_bytes()).hexdigest()
    elif damage == 'recorded_sha':
        declaration['recorded_test_source_sha256'] = 'e' * 64
    elif damage == 'replacement_bytes':
        file.write_text(file.read_text() + '// Unreviewed later mutation\n')
    elif damage == 'unknown_commit':
        declaration['retained_spec_commit'] = 'e' * 40
    elif damage == 'wrong_blob':
        declaration['retained_spec_commit'] = git('rev-parse', 'HEAD')
    elif damage == 'descendant':
        current = git('rev-parse', 'HEAD')
        file.write_text(git('show', declaration['retained_spec_commit'] + ':' + TEST.split('::')[0]) + '\n')
        git('add', '.')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture.invalid', 'commit', '-qm', 'untrusted later anchor')
        declaration['retained_spec_commit'] = git('rev-parse', 'HEAD')
        git('checkout', '-q', '--detach', current)
    elif damage == 'other_receipt':
        declaration['receipt'] = 'other.json'
    elif damage == 'unknown_field':
        declaration['accept_any_source'] = True
    else:
        value['declarations'].append(dict(declaration))
    if damage != 'missing':
        write()
    assert gate._reference_errors('F064.gui', entry, 'click', tmp_path)

"""Read-only publication checks retain source evidence and redact matched text."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'publication_audit.py'


def load():
    spec = importlib.util.spec_from_file_location('publication_audit', SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_sensitive_matches_are_redacted_and_source_is_unchanged(tmp_path):
    module = load()
    token = 'ghp_' + 'a' * 36
    path = tmp_path / 'report.log'
    raw = ('token=' + token + '\n' + '/Users/' + 'privateperson/images/photo.png\n' + '10.44.23.18\n').encode()
    path.write_bytes(raw)
    report = module.audit([('artifact/report.log', raw)])
    encoded = json.dumps(report)
    assert report['status'] == 'review_required'
    assert {'credential', 'personal_path', 'private_address'} <= {f['rule'] for f in report['findings']}
    assert token not in encoded and 'privateperson' not in encoded and '10.44.23.18' not in encoded
    assert path.read_bytes() == raw


def test_exact_allowance_cannot_hide_a_changed_line_or_credential():
    module = load()
    content = b'Copyright ConfidentialPartner; retain required notice.\n'
    report = module.audit([('LICENSE', content)], forbidden_terms=['ConfidentialPartner'])
    finding = report['findings'][0]
    allowed = {finding['finding_id']: 'Required third-party copyright notice'}
    passed = module.audit([('LICENSE', content)], forbidden_terms=['ConfidentialPartner'], allowances=allowed)
    assert passed['status'] == 'passed' and passed['findings'][0]['disposition'] == 'allowed'
    changed = module.audit([('LICENSE', content + b'ConfidentialPartner customer record\n')], forbidden_terms=['ConfidentialPartner'], allowances=allowed)
    assert changed['status'] == 'review_required'
    token = ('github_pat_' + 'a' * 60).encode()
    secret = module.audit([('secret.txt', token)]); key = secret['findings'][0]['finding_id']
    assert module.audit([('secret.txt', token)], allowances={key: 'Do not permit this'})['status'] == 'review_required'


def test_data_and_oversize_are_unexamined_not_clean():
    module = load()
    report = module.audit([('models/model.pt', b'weights'), ('image.png', b'pixels'), ('huge.log', b'x' * 17)], max_file_bytes=16)
    assert report['status'] == 'review_required'
    assert {'data_artifact', 'oversize'} <= {f['rule'] for f in report['findings']}
    assert report['unexamined'] == 1


def test_zip_members_are_scanned_without_extraction_and_links_refuse(tmp_path):
    module = load(); archive = tmp_path / 'delivery.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('logs/private.log', ('sk-' + 'x' * 40).encode())
        z.writestr('../escape.txt', b'escape')
        link = zipfile.ZipInfo('link'); link.create_system = 3; link.external_attr = 0o120777 << 16
        z.writestr(link, b'outside')
    report = module.audit(module.artifact_inputs(archive, 'delivery'))
    assert {'credential', 'unsafe_member'} <= {f['rule'] for f in report['findings']}
    assert not (tmp_path / 'escape.txt').exists() and not (tmp_path / 'logs').exists()


def test_unsupported_allowlist_keys_and_stale_entries_refuse(tmp_path):
    module = load()
    path = tmp_path / 'allow.json'; path.write_text(json.dumps({'schema': 'PublicationAllowlist/v1', 'entries': [{'path': '*', 'reason': 'allow all'}]}))
    with pytest.raises(ValueError): module.read_allowances(path)
    report = module.audit([('README.md', b'public documentation')], allowances={'not-a-current-finding': 'Old notice'})
    assert report['status'] == 'review_required' and any(f['rule'] == 'stale_allowance' for f in report['findings'])


def test_actual_git_cli_scans_exact_commit_messages_and_ignores_dirty_bytes(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 'fixture@example.invalid'], ['config', 'user.name', 'Fixture']):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    source = repo / 'README.md'; source.write_text('public source\n'); subprocess.run(['git', 'add', 'README.md'], cwd=repo, check=True)
    message = 'Test scope ' + '10.88.7.6'; subprocess.run(['git', 'commit', '-qm', message], cwd=repo, check=True)
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repo, text=True).strip()
    source.write_text('different uncommitted contents\n')
    output = tmp_path / 'report.json'
    executed = subprocess.run([sys.executable, str(SCRIPT), '--root', str(repo), '--revision', sha, '--output', str(output)], capture_output=True, text=True, encoding='utf-8')
    assert executed.returncode == 1, executed.stderr
    report = json.loads(output.read_bytes()); assert report['source_commit'] == sha
    assert any(f['rule'] == 'private_address' and f['path'].startswith('commit/') for f in report['findings'])
    assert report['inputs'][0]['sha256'] == hashlib.sha256(b'public source\n').hexdigest()
    assert source.read_text() == 'different uncommitted contents\n'


def test_total_budget_stops_reading_later_inputs():
    module = load(); consumed = []
    def inputs():
        for index in range(10):
            consumed.append(index)
            yield str(index), b'12345678'
    report = module.audit(inputs(), max_file_bytes=10, max_total_bytes=10)
    assert consumed == [0, 1]
    assert report['status'] == 'review_required' and report['unexamined'] == 1


def test_linked_artifact_never_reads_the_target(tmp_path):
    module = load(); target = tmp_path / 'secret'; target.write_text('private source')
    link = tmp_path / 'link'; link.symlink_to(target)
    report = module.audit(module.artifact_inputs(link))
    assert report['unexamined'] == 1 and report['inputs'] == []
    assert report['findings'][0]['rule'] == 'linked_input'


def test_sensitive_member_path_is_masked_in_every_report_field():
    module = load(); path = '/Users/' + 'privateperson/source.txt'
    report = module.audit([(path, b'public text')])
    assert 'privateperson' not in json.dumps(report)


def test_sensitive_filename_alone_requires_review():
    module = load()
    report = module.audit([('logs/' + '10.51.4.8' + '.json', b'{}')])
    assert report['status'] == 'review_required'
    assert any(f['rule'] == 'private_address' and f['line'] is None for f in report['findings'])
    assert '10.51.4.8' not in json.dumps(report)


def test_duplicate_allowlist_fields_cannot_replace_review_reason(tmp_path):
    module = load(); path = tmp_path / 'allow.json'
    path.write_text('{"schema":"PublicationAllowlist/v1","entries":[],"entries":[]}')
    with pytest.raises(ValueError): module.read_allowances(path)


def test_cli_output_never_overwrites_existing_source(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    for args in (['init', '-q'], ['config', 'user.email', 'fixture@example.invalid'], ['config', 'user.name', 'Fixture']):
        subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True)
    source = repo / 'README.md'; source.write_text('retained source\n')
    subprocess.run(['git', 'add', 'README.md'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-qm', 'Public source'], cwd=repo, check=True)
    executed = subprocess.run([sys.executable, str(SCRIPT), '--root', str(repo), '--output', str(source)], capture_output=True, text=True)
    assert executed.returncode == 2
    assert source.read_text() == 'retained source\n'


def test_selected_artifact_basename_is_audited(tmp_path):
    module=load();source=tmp_path/('10.51.4.8'+'.json');source.write_bytes(b'{}')
    report=module.audit(module.artifact_inputs(source))
    assert report['status']=='review_required'
    assert any(f['rule']=='private_address' for f in report['findings'])
    assert '10.51.4.8' not in json.dumps(report)

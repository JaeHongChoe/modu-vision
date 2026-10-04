"""Read-only bounded publication review; no matched text or automatic publication."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
PATTERNS = {
    'credential': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{40,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|AKIA[0-9A-Z]{16})\b|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\b(?:https?|ssh)://[^\s/:]+:[^\s/@]+@'),
    'personal_path': re.compile(r'/Users/[A-Za-z0-9_.-]+(?:/[^\s\"\'<>`]*)?|/home/[A-Za-z0-9_.-]+(?:/[^\s\"\'<>`]*)?|[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.-]+'),
    'private_address': re.compile(r'\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b'),
    'contact': re.compile(r'\b[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'),
}
DATA_SUFFIXES = {'.pt', '.pth', '.onnx', '.safetensors', '.npy', '.npz', '.sqlite', '.sqlite3', '.db', '.csv', '.jpg', '.jpeg', '.png', '.bmp', '.tif', '.tiff', '.dcm'}
NEVER_ALLOW = {'credential', 'oversize', 'budget', 'unsafe_member', 'linked_input', 'unreadable'}


class Unexamined:
    def __init__(self, rule): self.rule = rule


def _sha(data): return hashlib.sha256(data).hexdigest()


def read_allowances(path):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise ValueError('Duplicate allowlist field')
            result[key] = value
        return result
    with Path(path).open('rb') as stream: raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024: raise ValueError('Allowlist exceeds size bound')
    value = json.loads(raw, object_pairs_hook=unique_fields)
    if not isinstance(value, dict) or set(value) != {'schema', 'entries'} or value['schema'] != 'PublicationAllowlist/v1' or not isinstance(value['entries'], list):
        raise ValueError('Invalid exact publication allowlist')
    result = {}
    for row in value['entries']:
        if not isinstance(row, dict) or set(row) != {'finding_id', 'reason'} or not isinstance(row['reason'], str) or len(row['reason'].strip()) < 10:
            raise ValueError('Exact finding identity and review reason required')
        identifier = row['finding_id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{64}', identifier) or identifier in result:
            raise ValueError('Invalid or duplicate finding identity')
        result[identifier] = row['reason'].strip()
    return result


def audit(inputs, *, forbidden_terms=(), allowances=None, max_file_bytes=MAX_FILE_BYTES, max_total_bytes=MAX_TOTAL_BYTES):
    allowances = allowances or {}
    if len(forbidden_terms) > 32 or any(not isinstance(term, str) or not 2 <= len(term) <= 128 for term in forbidden_terms):
        raise ValueError('Prohibited names must be explicit bounded strings')
    patterns = dict(PATTERNS)
    if forbidden_terms: patterns['prohibited_name'] = re.compile('|'.join(re.escape(term) for term in forbidden_terms), re.I)
    rows, findings, seen_ids, used, unexamined = [], [], set(), 0, 0
    def safe_path(path):
        return '[PATH:' + _sha(path.encode())[:20] + ']' if any(p.search(path) for p in patterns.values()) else path
    def finding(path, rule, line, evidence):
        identifier = _sha(json.dumps([path, rule, line, _sha(evidence)], separators=(',', ':')).encode())
        allowed = identifier in allowances and rule not in NEVER_ALLOW
        findings.append({'finding_id': identifier, 'path': safe_path(path), 'rule': rule, 'line': line,
                         'disposition': 'allowed' if allowed else 'review_required'})
        seen_ids.add(identifier)
    for index, (path, data) in enumerate(inputs):
        if index >= 10000:
            finding('remaining-inputs', 'budget', None, b'input-count'); unexamined += 1; break
        path = str(path)
        for rule, pattern in patterns.items():
            if pattern.search(path): finding(path, rule, None, path.encode())
        if isinstance(data, Unexamined):
            finding(path, data.rule, None, data.rule.encode()); unexamined += 1; continue
        if used + len(data) > max_total_bytes and len(data) <= max_file_bytes:
            finding('remaining-inputs', 'budget', None, str(len(data)).encode()); unexamined += 1; break
        if len(data) > max_file_bytes:
            finding(path, 'oversize', None, str(len(data)).encode()); unexamined += 1; continue
        used += len(data)
        rows.append({'path': safe_path(path), 'bytes': len(data), 'sha256': _sha(data)})
        if Path(path).suffix.lower() in DATA_SUFFIXES:
            finding(path, 'data_artifact', None, data)
        try: text = data.decode('utf-8')
        except UnicodeDecodeError:
            text = data.decode('utf-8', errors='replace'); finding(path, 'opaque_binary', None, data)
        else:
            if b'\0' in data: finding(path, 'opaque_binary', None, data)
        for number, line in enumerate(text.splitlines(), 1):
            for rule, pattern in patterns.items():
                if pattern.search(line): finding(path, rule, number, line.encode())
    for identifier in sorted(set(allowances) - seen_ids):
        findings.append({'finding_id': identifier, 'path': 'allowlist', 'rule': 'stale_allowance', 'line': None, 'disposition': 'review_required'})
    return {'schema': 'PublicationReport/v1', 'status': 'review_required' if any(f['disposition'] != 'allowed' for f in findings) else 'passed',
            'inputs': rows, 'findings': findings, 'unexamined': unexamined, 'examined_bytes': used,
            'limits': {'max_file_bytes': max_file_bytes, 'max_total_bytes': max_total_bytes, 'max_inputs': 10000},
            'scope': 'Heuristic static review of exact inputs; opaque/data allowances require human review. Not a secret-free, license, signing, model-quality or release approval.'}


def artifact_inputs(path, label=None):
    path = Path(path)
    label = path.name if label is None else label
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        yield label, Unexamined('linked_input'); return
    if path.is_dir():
        for index, member in enumerate(path.rglob('*')):
            if index >= 10000: yield label, Unexamined('budget'); return
            if member.is_symlink(): yield label + '/' + member.relative_to(path).as_posix(), Unexamined('linked_input')
            elif member.is_file(): yield from artifact_inputs(member, label + '/' + member.relative_to(path).as_posix())
            elif not member.is_dir(): yield label, Unexamined('unreadable')
        return
    try:
        if not path.is_file(): yield label, Unexamined('unreadable'); return
        if path.suffix.lower() == '.zip':
            if path.stat().st_size > MAX_TOTAL_BYTES: yield label, Unexamined('oversize'); return
            with zipfile.ZipFile(path) as archive:
                for member in archive.infolist():
                    if member.is_dir(): continue
                    part = PurePosixPath(member.filename)
                    name = label + '/' + member.filename
                    mode = member.external_attr >> 16
                    if part.is_absolute() or '..' in part.parts or '\\' in member.filename or stat.S_ISLNK(mode):
                        yield name, Unexamined('unsafe_member')
                    elif member.file_size > MAX_FILE_BYTES or member.file_size / max(1, member.compress_size) > 200:
                        yield name, Unexamined('oversize')
                    else: yield name, archive.read(member)
        elif path.stat().st_size > MAX_FILE_BYTES: yield label, Unexamined('oversize')
        else: yield label, path.read_bytes()
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
        yield label, Unexamined('unreadable')


def git_inputs(root, revision, since=None):
    def git(*args): return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL)
    commit = git('rev-parse', '--verify', '--end-of-options', revision + '^{commit}').decode().strip()
    tree = git('ls-tree', '-rz', commit).split(b'\0')
    def values():
        for row in tree:
            if not row: continue
            meta, name = row.split(b'\t', 1); mode, kind, oid = meta.decode().split(); label = name.decode('utf-8', errors='replace')
            if mode == '120000' or kind != 'blob': yield label, Unexamined('linked_input'); continue
            if int(git('cat-file', '-s', oid)) > MAX_FILE_BYTES: yield label, Unexamined('oversize')
            else: yield label, git('cat-file', 'blob', oid)
        if since:
            base = git('rev-parse', '--verify', '--end-of-options', since + '^{commit}').decode().strip()
            commits = git('rev-list', base + '..' + commit).decode().splitlines()
        else: commits = [commit]
        for sha in commits:
            yield 'commit/' + sha, git('show', '-s', '--format=%B', sha)
    return commit, values()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd()); parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--since', help='Scan messages of every commit after this base')
    parser.add_argument('--artifact', type=Path, action='append', default=[])
    parser.add_argument('--forbidden-term', action='append', default=[])
    parser.add_argument('--allowlist', type=Path); parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    try:
        sha, sources = git_inputs(args.root, args.revision, args.since)
        def inputs():
            yield from sources
            for index, artifact in enumerate(args.artifact):
                yield from artifact_inputs(artifact, 'artifact-' + str(index + 1) + '/' + artifact.name)
        report = audit(inputs(), forbidden_terms=args.forbidden_term, allowances=read_allowances(args.allowlist) if args.allowlist else None)
        report['source_commit'] = sha
        encoded = json.dumps(report, ensure_ascii=True, indent=2) + '\n'
        if args.output:
            # A report destination must be new; never overwrite source or inputs.
            with args.output.open('x', encoding='utf-8') as stream: stream.write(encoded)
        else: print(encoded, end='')
        return 0 if report['status'] == 'passed' else 1
    except (OSError, ValueError, subprocess.CalledProcessError):
        print(json.dumps({'schema': 'PublicationReport/v1', 'status': 'refused', 'error': 'Input, revision or exact allowlist could not be read safely'}))
        return 2

if __name__ == '__main__': raise SystemExit(main())

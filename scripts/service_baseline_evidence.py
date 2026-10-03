"""S0-08 baseline evidence: one BaselineEvidence record per reproduced core defect (S0-01 to S0-04).

The regressions are the ones ``backend/tests/test_service_integrity.py`` pins (``PINNED``, read from its source; this
script imports nothing from the app, so it never opens an app store itself). Each defect's regressions run in their own
production-path module, in a pytest child that applies the test suite's own isolation, and one record per defect says:

- source_sha: the commit that was tested, and source_changes: every file under backend/ (and this script) that differs
  from it, so a record of an edited checkout says so;
- fixture_hash: sha256 over the regression module and every test module it imports, with conftest.py (fixture_files
  lists them; line endings are normalised so a Windows checkout hashes the same);
- path: the production path the regressions go through, command: the exact pytest command, result: each test's
  outcome, a failure's own message, and the output tail when the report is missing or unreadable;
- scope: what the record shows and what it does not.

Usage: python scripts/service_baseline_evidence.py --output <file.json> [--defect S0-02 ...]
Exits 1 when any regression did not pass. The script itself writes only the output file; the pytest children write
what the regressions write (temporary folders), under the suite's own isolation.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ElementTree
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
INTEGRITY = ROOT / 'backend' / 'tests' / 'test_service_integrity.py'


def _pinned() -> dict[str, tuple[str, tuple[str, ...]]]:
    """PINNED from the integrity module's source: {defect: (module, tests)}."""
    tree = ast.parse(INTEGRITY.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'PINNED' for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError(f'PINNED is not defined in {INTEGRITY}')


PINNED = _pinned()
PATHS = {
    'S0-01': 'Dataset.__getitem__ of the detection, segmentation and rotated-box loaders (joint image and label augmentation)',
    'S0-02': 'flow model catalog, portable package, direct inference and the heatmap API with a saved raw distance threshold',
    'S0-03': 'approval, release and export eligibility when reviewed truth changes (export during real CPU parity)',
    'S0-04': 'central release rollback (revocation rechecked before any remote command)',
    'S0-04 emergency': 'emergency rollback by the owner admin on the local fleet (live gates kept)',
}
SCOPES = {
    'S0-01': 'CPU; synthetic 32 px images and labels; loader geometry only, no training quality',
    'S0-02': 'CPU; a synthetic distance model and calibration; score units and thresholds, not detection quality',
    'S0-03': 'CPU; a local project with recorded evaluations; parity is a real CPU run, no target device',
    'S0-04': 'CPU; a local central store and fleet records; no real remote device or network',
    'S0-04 emergency': 'CPU; a local fleet; no real remote device or network',
}
# The packages a result depends on; a web stack older than requirements.txt fails the API regressions run alone.
PACKAGES = ('fastapi', 'starlette', 'httpx', 'numpy', 'pillow', 'torch', 'torchvision')


@dataclass(frozen=True)
class BaselineEvidence:
    defect: str
    source_sha: str | None
    source_changes: list[str]
    fixture_hash: str
    fixture_files: list[str]
    path: str
    command: list[str]
    result: dict
    scope: str


def package_versions() -> dict[str, str | None]:
    found = {}
    for name in PACKAGES:
        try:
            found[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            found[name] = None
    return found


def module_file(module: str) -> Path:
    return ROOT / (module.replace('.', '/') + '.py')


def fixture_files(module: str) -> list[Path]:
    """The regression module, every backend.tests module it imports (transitively) and the conftest files pytest loads
    for it (backend/conftest.py holds the store isolation)."""
    seen, pending = set(), [module]
    while pending:
        name = pending.pop()
        path = module_file(name)
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
            names = ([node.module] if isinstance(node, ast.ImportFrom) and node.module and node.level == 0
                     else [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            pending.extend(item for item in names if item.startswith('backend.tests.'))
    files = sorted(module_file(name) for name in seen)
    return files + [path for path in (ROOT / 'backend' / 'conftest.py', ROOT / 'backend' / 'tests' / 'conftest.py') if path.is_file()]


def fixture_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode() + b'\0')
        digest.update(path.read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, encoding='utf-8',
                                   errors='replace', check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return completed.stdout


def source_state() -> tuple[str | None, list[str]]:
    sha = _git('rev-parse', 'HEAD')
    if sha is None:
        return None, ['(not a git checkout: the tested source is unknown)']
    status = _git('status', '--porcelain', '--untracked-files=all', '--', 'backend', 'scripts/service_baseline_evidence.py') or ''
    return sha.strip(), sorted({line[3:].strip().strip('"') for line in status.splitlines() if line.strip()})


def outcomes(junit_xml: bytes | str, tests: tuple[str, ...]) -> tuple[dict[str, str], dict[str, str]]:
    """Each test's outcome and each failed test's own message; a parametrized test passed only when every case did."""
    cases: dict[str, list[str]] = {test: [] for test in tests}
    messages: dict[str, str] = {}
    for case in ElementTree.fromstring(junit_xml).iter('testcase'):
        name = case.get('name', '').split('[', 1)[0]
        if name not in cases:
            continue
        problems = [child for child in case if child.tag in ('failure', 'error')]
        if problems:
            cases[name].append('failed')
            messages.setdefault(name, (problems[0].get('message') or problems[0].text or problems[0].tag).strip()[:500])
        else:
            cases[name].append('skipped' if any(child.tag == 'skipped' for child in case) else 'passed')
    found = {test: ('not run' if not rows else 'failed' if 'failed' in rows else 'skipped' if 'skipped' in rows else
                    f'passed ({len(rows)} cases)' if len(rows) > 1 else 'passed') for test, rows in cases.items()}
    return found, messages


def run_pytest(command: list[str]) -> tuple[int, bytes, str]:
    """Run ``command`` with a JUnit report: (exit code, report bytes, output tail). The child writes its output as UTF-8
    (PYTHONIOENCODING) and it is read with replacement, so a console code page never stops the record; the regressions
    themselves keep the platform's default encoding (no UTF-8 mode), as the app runs them."""
    with tempfile.TemporaryDirectory() as folder:
        report = Path(folder) / 'junit.xml'
        completed = subprocess.run([*command, f'--junitxml={report}'], cwd=ROOT, capture_output=True, text=True,
                                   encoding='utf-8', errors='replace',
                                   env=dict(os.environ, PYTHONIOENCODING='utf-8'))
        output = (completed.stdout + completed.stderr)[-2000:]
        return completed.returncode, report.read_bytes() if report.is_file() else b'<testsuites/>', output


def record(defect: str, runner: Callable[[list[str]], tuple[int, bytes, str]] = run_pytest) -> BaselineEvidence:
    module, tests = PINNED[defect]
    relative = module_file(module).relative_to(ROOT).as_posix()
    command = [sys.executable, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *(f'{relative}::{test}' for test in tests)]
    started = time.monotonic()
    exit_code, report, output = runner(command)
    try:
        per_test, messages = outcomes(report, tests)
    except ElementTree.ParseError as exc:
        per_test, messages = {test: 'not run' for test in tests}, {'report': f'unreadable JUnit report: {exc}'}
        output_kept = True
    else:
        output_kept = False
    passed = exit_code == 0 and all(value.startswith('passed') for value in per_test.values())
    sha, changes = source_state()
    result = {'outcome': 'passed' if passed else 'failed', 'exit_code': exit_code, 'tests': per_test, 'failures': messages,
              'seconds': round(time.monotonic() - started, 1), 'platform': f'{platform.system()} {platform.machine()}',
              'python': platform.python_version(), 'packages': package_versions()}
    if not passed and (output_kept or not messages):
        result['output_tail'] = output
    files = fixture_files(module)
    return BaselineEvidence(defect, sha, changes, fixture_hash(files), [path.relative_to(ROOT).as_posix() for path in files],
                            PATHS[defect], ['python', *command[1:], '--junitxml=<report>'], result, SCOPES[defect])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--defect', action='append', choices=sorted(PINNED))
    options = parser.parse_args(argv)
    records = [record(defect) for defect in (options.defect or list(PINNED))]
    options.output.write_text(json.dumps({'records': [asdict(item) for item in records]}, ensure_ascii=False, indent=1) + '\n',
                              encoding='utf-8')
    for item in records:
        print(f"{item.defect}: {item.result['outcome']} ({len(item.result['tests'])} tests, {item.result['seconds']} s)")
    return 0 if all(item.result['outcome'] == 'passed' for item in records) else 1


if __name__ == '__main__':
    raise SystemExit(main())

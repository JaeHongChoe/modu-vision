"""Fast-lane selection and the exact-head full-regression gate.

The full regression lane (.github/workflows/ci.yml, "Linux CPU source checks") stays the complete and unchanged
qualification. It runs when dispatched for the head that will be merged, and after every push to main. The fast lane
(.github/workflows/ci-fast.yml) answers each pull-request update with the checks related to what changed:

- select: the full CPU lane's pytest references whose test file changed or reads a changed backend module (by dotted
  import, `from backend.x import y`, or a 'backend/x/y.py' path string), plus the CI structure checks, and the browser
  specs that changed. Only references that the full lane already runs are chosen, so the fast lane never runs a test
  in a setting the full lane does not. "Changed" is measured from the newest commit of this branch whose fast run
  passed (falling back to the merge base), so changes from a superseded, cancelled run are still checked.
- full-regression: passes only when the full regression lane succeeded on exactly this head commit. It runs in its
  own workflow (.github/workflows/ci-merge-gate.yml): a fast run's success then means only that the fast checks
  passed, so the change range moves forward on every passing fast run while the merge gate stays red until the merge
  head's full regression has passed.

Usage:
  python scripts/ci/related_checks.py select --kind pytest|specs [--shard I/N] [--base REF] [--head REF]
      [--extra "..."] [--repo OWNER/NAME --branch NAME]   (prints one reference per line)
  python scripts/ci/related_checks.py full-regression --repo OWNER/NAME --sha SHA
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FULL_WORKFLOW = '.github/workflows/ci.yml'
FAST_WORKFLOW = 'ci-fast.yml'
CPU_STEP = 'CPU contract and recovery regressions'
DIAGNOSTIC_STEP = 'Isolated flow outcome diagnostic controls'
STARTUP_STEP = 'Owned startup identity controls'
REFERENCE = re.compile(r'(?:backend/tests|scripts/tests)/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_\[\]-]+)*')
# Checks of the CI definitions themselves; cheap, and they guard every lane, so the fast lane always runs them.
ALWAYS_PYTEST = (
    'backend/tests/test_source_ci_model_lanes.py',
    'backend/tests/test_service_s6_07.py',
    'backend/tests/test_service_s6_05.py',
)


def _git(*args: str, root: Path = ROOT) -> str:
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True, text=True).stdout


def full_pytest_references(root: Path = ROOT) -> list[str]:
    """Every recognized pytest step's references, in full workflow order."""
    import yaml
    workflow = yaml.safe_load((root / FULL_WORKFLOW).read_text(encoding='utf-8'))
    steps = [step for step in workflow['jobs']['source']['steps'] if step.get('name') in (STARTUP_STEP, CPU_STEP, DIAGNOSTIC_STEP)]
    references: list[str] = []
    for step in steps:
        for reference in REFERENCE.findall(step['run']):
            if reference not in references:
                references.append(reference)
    return references


def public_specs(root: Path = ROOT) -> list[str]:
    # Forward slashes as git prints them, on Windows too.
    return sorted(path.relative_to(root).as_posix() for path in (root / 'scripts/e2e').glob('*.spec.ts'))


def _module_patterns(path: str) -> list[re.Pattern[str]]:
    """How a test file can read the changed backend module at `path`."""
    parts = Path(path).with_suffix('').parts
    if parts[-1] == '__init__':
        parts = parts[:-1]
    dotted, package, name = '.'.join(parts), '.'.join(parts[:-1]), parts[-1]
    patterns = [re.compile(r'\b' + re.escape(dotted) + r'\b'), re.compile(re.escape(path))]
    if package:
        patterns.append(re.compile(r'from\s+' + re.escape(package) + r'\s+import\s+[^\n]*\b' + re.escape(name) + r'\b'))
    return patterns


def select_pytest(changed: list[str], root: Path = ROOT, extra: list[str] = ()) -> list[str]:
    references = full_pytest_references(root)
    files = {reference.split('::', 1)[0] for reference in references}
    unknown = [value for value in extra if value not in references and value not in files]
    if unknown:
        raise SystemExit(f'Not in the full CPU lane, so not selectable here: {" ".join(unknown)}')
    changed_set = set(changed)
    modules = [path for path in changed if path.startswith('backend/') and path.endswith('.py')]
    patterns = [pattern for path in modules for pattern in _module_patterns(path)]
    texts: dict[str, str] = {}
    chosen: list[str] = []
    for reference in references:
        file = reference.split('::', 1)[0]
        if file not in texts:
            target = root / file
            texts[file] = target.read_text(encoding='utf-8') if target.is_file() else ''
        if file in changed_set or file in ALWAYS_PYTEST or reference in extra or file in extra \
                or any(pattern.search(texts[file]) for pattern in patterns):
            chosen.append(reference)
    return chosen


def select_specs(changed: list[str], root: Path = ROOT, extra: list[str] = ()) -> list[str]:
    known = set(public_specs(root))
    unknown = [value for value in extra if value not in known]
    if unknown:
        raise SystemExit(f'Unknown browser spec: {" ".join(unknown)}')
    return sorted({path for path in changed if path in known} | set(extra))


def shard(values: list[str], index: int, count: int) -> list[str]:
    """Split by test file, keeping a file's references together, balancing by file size."""
    if not 1 <= index <= count:
        raise SystemExit(f'Shard {index}/{count} is out of range')
    groups: dict[str, list[str]] = {}
    for value in values:
        groups.setdefault(value.split('::', 1)[0], []).append(value)
    loads = [0] * count
    owner: dict[str, int] = {}
    for file in sorted(groups, key=lambda name: (-((ROOT / name).stat().st_size if (ROOT / name).is_file() else 0), name)):
        target = loads.index(min(loads))
        owner[file] = target
        loads[target] += (ROOT / file).stat().st_size if (ROOT / file).is_file() else 1
    return [value for value in values if owner[value.split('::', 1)[0]] == index - 1]


def passed_fast_heads(repo: str, branch: str) -> list[str] | None:
    """Head commits of this branch's fast runs that passed, newest first (None when the API cannot be read).

    A cancelled, failed or still running fast run is not listed, so the changes it would have checked stay in the
    next run's range. The merge gate is a separate workflow, so it never holds a passing fast run back."""
    try:
        raw = subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/{FAST_WORKFLOW}/runs?branch={branch}&status=success&per_page=50',
                              '-q', '[.workflow_runs[].head_sha]'], check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return json.loads(raw or '[]')


def last_green_fast_head(repo: str, branch: str, head: str, root: Path = ROOT, passed=passed_fast_heads) -> str | None:
    """Newest commit of this branch, strictly before head and an ancestor of it, whose fast run passed."""
    shas = passed(repo, branch)
    if not shas:
        return None
    head_sha = _git('rev-parse', head, root=root).strip()
    for sha in shas:
        if sha == head_sha:
            continue
        if subprocess.run(['git', 'merge-base', '--is-ancestor', sha, head_sha], cwd=root, capture_output=True).returncode == 0:
            return sha
    return None


def changed_files(base: str, head: str, root: Path = ROOT) -> list[str]:
    return [line for line in _git('diff', '--name-only', f'{base}...{head}', root=root).splitlines() if line]


def gate_decision(runs: list[dict], sha: str) -> tuple[bool, str]:
    """Pass only for a completed, successful full regression run on exactly `sha`."""
    exact = [run for run in runs if run.get('head_sha') == sha]
    if any(run.get('status') == 'completed' and run.get('conclusion') == 'success' for run in exact):
        return True, f'Full regression passed on {sha}.'
    running = [run for run in exact if run.get('status') != 'completed']
    if running:
        return False, f'Full regression is still running on {sha}: {running[0].get("html_url", "")}'
    failed = [run for run in exact if run.get('status') == 'completed']
    if failed:
        return False, f'Full regression did not pass on {sha} ({failed[0].get("conclusion")}): {failed[0].get("html_url", "")}'
    return False, (f'Full regression has not run on {sha}. Before merging, run it on this exact head: '
                   f'gh workflow run ci.yml --ref <this branch>; then re-run this check.')


def full_regression_runs(repo: str, sha: str) -> list[dict]:
    raw = subprocess.run(['gh', 'api', f'repos/{repo}/actions/workflows/ci.yml/runs?head_sha={sha}&per_page=50',
                          '-q', '.workflow_runs'], check=True, capture_output=True, text=True).stdout
    return json.loads(raw or '[]')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n', 1)[0])
    commands = parser.add_subparsers(dest='command', required=True)
    select = commands.add_parser('select')
    select.add_argument('--kind', choices=('pytest', 'specs'), required=True)
    select.add_argument('--base', default='')
    select.add_argument('--head', default='HEAD')
    select.add_argument('--base-branch', default='main')
    select.add_argument('--repo', default='')
    select.add_argument('--branch', default='')
    select.add_argument('--shard', default='1/1')
    select.add_argument('--extra', default='')
    gate = commands.add_parser('full-regression')
    gate.add_argument('--repo', required=True)
    gate.add_argument('--sha', required=True)
    args = parser.parse_args(argv)
    if args.command == 'full-regression':
        passed, message = gate_decision(full_regression_runs(args.repo, args.sha), args.sha)
        print(message)
        return 0 if passed else 1
    base = args.base
    if not base and args.repo and args.branch:
        base = last_green_fast_head(args.repo, args.branch, args.head) or ''
    if not base:
        base = _git('merge-base', f'origin/{args.base_branch}', args.head).strip()
    changed = changed_files(base, args.head)
    extra = args.extra.split()
    values = select_pytest(changed, extra=extra) if args.kind == 'pytest' else select_specs(changed, extra=extra)
    index, count = (int(part) for part in args.shard.split('/'))
    values = shard(values, index, count) if args.kind == 'pytest' else values
    print(f'# {args.kind} related to {len(changed)} changed files since {base[:12]}: {len(values)} selected', file=sys.stderr)
    for value in values:
        print(value)
    return 0


if __name__ == '__main__':
    sys.exit(main())

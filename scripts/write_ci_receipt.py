"""Write source/toolchain identity and observed test counts, without release claims."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command_version(command):
    try:
        return subprocess.check_output(command, cwd=ROOT, text=True, encoding='utf-8', errors='replace',
                                       stderr=subprocess.DEVNULL, timeout=15).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def test_counts(path):
    if not path.is_file():
        return {"status": "not_recorded"}
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    counts = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
    counts["status"] = "failed" if counts["failures"] or counts["errors"] else "recorded"
    return counts


def _browser_rows(report):
    rows = []
    def visit(suites):
        for suite in suites:
            for spec in suite.get('specs', []):
                for test in spec.get('tests', []):
                    if test.get('projectName') == 'browser':
                        results = test.get('results', [])
                        rows.append({'id': spec['id'], 'title': spec['title'],
                                     'file': spec.get('file', suite.get('file')),
                                     'tags': [tag.lstrip('@') for tag in spec.get('tags', [])],
                                     'status': results[-1]['status'] if results else 'not_recorded'})
            visit(suite.get('suites', []))
    visit(report.get('suites', []))
    return rows


def browser_evidence(root, public_selection=None, owned_selection=None, e2e_root=None):
    lane = {'status': 'not_recorded', 'selected_count': 0,
            'results': {'passed': 0, 'failed': 0, 'skipped': 0, 'not_recorded': 0},
            'owned_model_tests_not_covered': [],
            'owned_model_reason': 'requires explicitly supplied authentic local weights; no public CI credentials or automatic download'}
    def load(file):
        file = Path(file)
        return json.loads(file.read_text(encoding='utf-8')), hashlib.sha256(file.read_bytes()).hexdigest()
    if (public_selection is None or owned_selection is None
            or not Path(public_selection).is_file() or not Path(owned_selection).is_file()):
        lane['selection_status'] = 'not_recorded'
        return lane
    public, lane['selection_sha256'] = load(public_selection)
    owned, lane['owned_selection_sha256'] = load(owned_selection)
    selected, unavailable = _browser_rows(public), _browser_rows(owned)
    if (any('owned-model' in r['tags'] for r in selected)
            or any('owned-model' not in r['tags'] for r in unavailable)
            or {r['id'] for r in selected} & {r['id'] for r in unavailable}):
        raise ValueError('Public and owned-model selections overlap or have invalid tags')
    lane['selected_count'] = len(selected)
    lane['selected_tests'] = selected
    lane['owned_model_tests_not_covered'] = unavailable
    inputs = {}
    for row in selected + unavailable:
        relative = row['file']
        if not isinstance(relative, str):
            raise ValueError('Browser source file is missing')
        file = Path(root) / relative
        if not file.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError('Browser source file escapes the checkout')
        inputs[relative] = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
    lane['source_input_sha256'] = inputs
    reports = sorted(Path(e2e_root).glob('*/report.json')) if e2e_root is not None else []
    if len(reports) > 1:
        raise ValueError('CI browser evidence must identify one exact execution')
    observed = {}
    if reports:
        report, lane['report_sha256'] = load(reports[0])
        observed = {r['id']: r for r in _browser_rows(report)}
        if set(observed) - {r['id'] for r in selected}:
            raise ValueError('Browser execution contains unselected tests')
    for row in selected:
        status = observed.get(row['id'], {}).get('status', 'not_recorded')
        key = status if status in ('passed', 'skipped', 'not_recorded') else 'failed'
        lane['results'][key] += 1
    counts = lane['results']
    lane['status'] = ('failed' if counts['failed'] else 'not_recorded' if not reports
                      else 'incomplete' if counts['skipped'] or counts['not_recorded'] or not selected else 'passed')
    return lane


def receipt(root, junit, *, public_selection=None, owned_selection=None, e2e_root=None):
    root = Path(root)
    lockfiles = ["package-lock.json", "build/ci/requirements-ubuntu-py313-cpu.lock", "build/ci/requirements-windows-py313-cpu.lock"]
    inputs = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in lockfiles if (root / name).is_file()}
    electron = root / "node_modules/electron/package.json"
    return {
        "receipt": "SourceCIManifest/v1",
        "source_sha": command_version(["git", "rev-parse", "HEAD"]),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "toolchain": {"python": platform.python_version(), "node": command_version(["node", "--version"]),
                      "electron": json.loads(electron.read_text(encoding='utf-8'))["version"] if electron.is_file() else None},
        "input_sha256": inputs,
        "pytest": test_counts(Path(junit)),
        "browser_lane": browser_evidence(root, public_selection, owned_selection, e2e_root),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "gates_not_covered": ["Windows 11 installer", "signing", "real hardware", "model quality approval", "public binary distribution", "authentic-weight model workflow"],
        "scope": "source CI execution; test records and skip reasons require review; not release acceptance",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument('--public-selection', type=Path)
    parser.add_argument('--owned-selection', type=Path)
    parser.add_argument('--e2e-root', type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(receipt(ROOT, args.junit, public_selection=args.public_selection,
        owned_selection=args.owned_selection, e2e_root=args.e2e_root), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

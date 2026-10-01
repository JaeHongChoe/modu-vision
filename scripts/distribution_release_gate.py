"""Public distribution release gate (S6-11).

Reads the S6-01 license inventory and the decision record (docs/distribution-license-decision.md) and fails closed.
A public artifact scope is releasable only when all of these hold:
- the inventory receipt is valid and current;
- the owner recorded an approved decision with a named authority;
- that decision pins every unresolved item in the scope (by license and version);
- no excluded package ships in the scope;
- the scope's notices list every shipped component that needs a notice.
The gate never decides a license and never changes LICENSE.

  python scripts/distribution_release_gate.py                     # builds the inventory now
  python scripts/distribution_release_gate.py --inventory receipt.json
Exit codes: 0 releasable, 1 held, 2 the gate could not evaluate (unreadable or malformed input).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # run as a script from a release workflow
    sys.path.insert(0, str(ROOT))

from scripts import license_inventory as inventory  # noqa: E402

# Inventory distribution kinds -> the public artifact scopes they ship in. Kinds the project never ships map to nothing;
# an unknown or empty kind maps to "unmapped", which no decision row can approve, so it holds the release until mapped.
SCOPES = {
    'source_and_installer': {'source', 'installer'},
    'installer': {'installer'},
    'installer_runtime': {'installer'},
    'frozen_backend': {'installer'},
    'bundled_in_renderer': {'installer'},
    'optional_pack': {'optional_pack'},
    'dockerfile_built_by_server_owner': {'remote_worker_image'},
    'remote_worker_image_cache': {'remote_worker_image'},
    'runtime_download_from_provider': {'runtime_download'},
    'user_export_package': {'trained_export'},
    'user_import': set(),
    'user_configured_local_directory': set(),
    'generated_locally': set(),
    'not_distributed': set(),
}
UNMAPPED = 'unmapped'
INVENTORY = 'inventory'
LICENSE_SCOPES = ('source', 'installer')  # both ship LICENSE
# Conditions only a locked release build resolves; no decision can accept them for a public artifact.
NOT_WAIVABLE = frozenset({'frozen_backend:inventory_required', 'python_environment:requirements_not_satisfied'})
_LIBRARY_SUFFIX = re.compile(r'\.(so(\.\d+)*|dylib|dll|pyd)$', re.IGNORECASE)
_VERSION_TAIL = re.compile(r'([.-]\d+)+$')
# Table layouts the S6-01 generator writes, as the cells left and right of the license cell.
_NOTICE_LAYOUT = (('name', 'version'), ('status', 'source'))  # | name | version | license | status | source |
_LAYOUTS = {
    'model_weights': (('name', 'version', 'use'), ('distribution', 'status', 'source')),  # model matrix
    'trained_model': (('name', 'description'), ('status',)),  # | name | description | terms | status |
}
_MARKER = '<!-- distribution-decisions -->'
_COMPONENT_KEYS = {'category', 'name', 'version', 'license', 'distribution', 'status', 'notices_required'}
_ITEM_KEYS = {'id', 'status', 'distribution'}


class GateError(Exception):
    """The gate could not evaluate its inputs (exit 2), as opposed to holding a release (exit 1)."""


def artifact_scopes(distribution) -> set[str]:
    if not isinstance(distribution, str):
        return {UNMAPPED}
    scopes, kinds = set(), 0
    for part in distribution.split('+'):
        kind = re.sub(r'\s*\(.*\)\s*$', '', part).strip()
        if kind:
            kinds += 1
            scopes |= SCOPES.get(kind, {UNMAPPED})
    return scopes if kinds else {UNMAPPED}


def load_decisions(path: Path | str) -> list[dict]:
    """The JSON block that follows the decisions marker in the decision record."""
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError as exc:
        raise GateError(f'decision record unreadable: {exc}') from exc
    if _MARKER not in text:
        raise GateError(f'{path} has no {_MARKER} block')
    match = re.search(r'```json\s*\n(.*?)\n```', text.split(_MARKER, 1)[1], re.DOTALL)
    if match is None:
        raise GateError(f'{path} has no JSON block after {_MARKER}')
    try:
        rows = json.loads(match.group(1))
    except ValueError as exc:
        raise GateError(f'decision block is not valid JSON: {exc}') from exc
    if not isinstance(rows, list) or not all(isinstance(row, dict) and isinstance(row.get('artifact_scope'), str)
                                             for row in rows):
        raise GateError('the decision block must be a list of rows with an artifact_scope')
    scopes = [row['artifact_scope'] for row in rows]
    if len(set(scopes)) != len(scopes):
        raise GateError('one decision row per artifact scope')
    for row in rows:
        scope = row['artifact_scope']
        if not isinstance(row.get('approval_status'), str):
            raise GateError(f'{scope}: approval_status must be a string')
        if not isinstance(row.get('accepted_items', []), list) or not isinstance(row.get('excluded_packages', []), list):
            raise GateError(f'{scope}: accepted_items and excluded_packages must be lists')
        if not all(isinstance(name, str) for name in row.get('excluded_packages', [])):
            raise GateError(f'{scope}: excluded_packages must list package names')
        notices = row.get('notices')
        if not (isinstance(notices, str) or isinstance(notices, list) and all(isinstance(name, str) for name in notices)):
            raise GateError(f'{scope}: notices must be a file name or a list of file names')
    return rows


def _canonical(name) -> str:
    return re.sub(r'[-_.]+', '-', str(name).split(':')[-1]).strip().lower()


def _package_names(name) -> set[str]:
    """Spellings that name the same package: case, separators, a category prefix, and for a shared library its
    version in any platform's form (libx264.so.164, libx264.164.dylib, avcodec-61.dll)."""
    base = str(name).split(':')[-1]
    names = {_canonical(base)}
    library = _LIBRARY_SUFFIX.sub('', base)
    if library != base:
        names |= {_canonical(library), _canonical(_VERSION_TAIL.sub('', library))}
    return names - {''}


def _table_rows(text: str) -> list[list[str]]:
    """Markdown table rows outside HTML comments and code fences, as raw cells."""
    # An unterminated comment or fence hides the rest of the document, as a Markdown renderer would.
    text = re.sub(r'<!--.*?(?:-->|\Z)', '', text, flags=re.DOTALL)
    text = re.sub(r'^ {0,3}(`{3,}|~{3,}).*?(?:^ {0,3}\1[^\n]*$|\Z)', '', text, flags=re.DOTALL | re.MULTILINE)
    return [line.strip()[1:-1].split('|') for line in text.splitlines()
            if line.startswith('| ') and line.rstrip().endswith('|')]


def _row_lists(cells: list[str], item: dict) -> bool:
    """The row names this component with its exact version, license and status in the generator's columns."""
    left, right = _LAYOUTS.get(item.get('category'), _NOTICE_LAYOUT)
    if len(cells) < len(left) + len(right) + 1:
        return False
    values = dict(zip(left, (cell.strip() for cell in cells[:len(left)])))
    values.update(zip(right, (cell.strip() for cell in cells[len(cells) - len(right):])))
    license_cell = '|'.join(cells[len(left):len(cells) - len(right)]).strip()
    version = item.get('version') or '' if item.get('category') == 'model_weights' else item.get('version')
    license_text = str(item.get('license'))
    return (values['name'] == item['name'] and values['status'] == str(item.get('status'))
            and ('version' not in values or values['version'] == str(version))
            and license_cell in (license_text, license_text.replace('|', '/')))


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ''


def _component_id(item: dict) -> str:
    return item['id'] if 'id' in item else f"{item['category']}:{item['name']}"


def validate_receipt(receipt, root: Path = ROOT) -> list[str]:
    """Problems that make the receipt unusable as release evidence: wrong shape, or inputs changed since it was built."""
    if not isinstance(receipt, dict):
        return ['inventory receipt is not an object']
    problems = []
    if receipt.get('schema') != inventory.SCHEMA or receipt.get('receipt') != 'InventoryReceipt':
        problems.append(f"inventory receipt schema is {receipt.get('schema')!r}, expected {inventory.SCHEMA!r}")
    components, items = receipt.get('components'), receipt.get('unresolved_items')
    if not isinstance(components, list) or not components:
        problems.append('inventory receipt has no components')
    elif not all(isinstance(item, dict) and _COMPONENT_KEYS <= set(item) for item in components):
        problems.append('inventory components lack required fields')
    if not isinstance(items, list) or not all(isinstance(item, dict) and _ITEM_KEYS <= set(item) for item in items):
        problems.append('inventory unresolved_items missing or malformed')
    expected = ['package-lock.json', inventory.PYTHON_RUNTIME, inventory.REMOTE_WORKER_LOCK,
                inventory.REMOTE_WORKER_DOCKERFILE, inventory.CACHED_WEIGHTS, *inventory.PYTHON_OPTIONAL.values()]
    recorded = receipt.get('inputs')
    if not isinstance(recorded, dict) or set(recorded) != set(expected):
        problems.append('inventory receipt does not record the expected input files')
    else:
        for name in expected:
            if recorded[name] != inventory._sha256(Path(root) / name):
                problems.append(f'inventory receipt is stale: {name} changed since it was built')
    return problems


def _fingerprint(receipt: dict) -> tuple:
    components = sorted(json.dumps([item.get(key) for key in ('category', 'name', 'version', 'license', 'status',
                                                                'distribution')], default=str)
                        for item in receipt.get('components', []))
    items = sorted(json.dumps([item.get('id'), item.get('status'), item.get('distribution')], default=str)
                   for item in receipt.get('unresolved_items', []))
    return components, items, (receipt.get('source') or {}).get('commit')


def compare_with_fresh(receipt: dict, fresh: dict) -> list[str]:
    """A supplied receipt is evidence only if a fresh inventory of the same tree and build produces the same content."""
    mine, theirs = _fingerprint(receipt), _fingerprint(fresh)
    problems = []
    if mine[0] != theirs[0]:
        problems.append(f'inventory receipt components differ from a fresh inventory '
                        f'({len(set(mine[0]) ^ set(theirs[0]))} differing entries)')
    if mine[1] != theirs[1]:
        problems.append('inventory receipt unresolved items differ from a fresh inventory')
    if mine[2] != theirs[2]:
        problems.append(f'inventory receipt was built at commit {mine[2]!r}, the tree is at {theirs[2]!r}')
    return problems


def _covered(entries: list, item: dict, components: list[dict]) -> bool:
    """Every shipped variant of the id must be pinned by license and version; a condition item is pinned by its license."""
    pins = [entry for entry in entries if isinstance(entry, dict) and entry.get('id') == item['id']]
    if not components:
        return any('license' in entry and entry['license'] == item.get('license') for entry in pins)
    return all(any(entry.get('license') == component.get('license')
                   and (component.get('version') is None or entry.get('version') == component.get('version'))
                   for entry in pins) for component in components)


def _notice_problems(scope: str, row: dict, shipped: list[dict], receipt: dict, root: Path) -> list[str]:
    names = row.get('notices')
    names = [names] if isinstance(names, str) else names
    if not isinstance(names, list) or not names or not all(_text(name) for name in names):
        return [f"notices {row.get('notices')!r} must name one or more files"]
    names = [_text(name) for name in names]
    tree = Path(root).resolve()
    outside = [name for name in names if Path(name).is_absolute() or not (tree / name).resolve().is_relative_to(tree)]
    if outside:
        return [f'notices file {name!r} is outside the source tree' for name in outside]
    absent = [name for name in names if not (tree / name).is_file()]
    if absent:
        return [f'notices file {name!r} does not exist' for name in absent]
    text = '\n'.join((tree / name).read_text(encoding='utf-8') for name in names)
    label = ', '.join(names)
    problems = []
    if scope == 'installer':
        try:
            missing = inventory.check_notices(receipt, text, Path(root))
        except (OSError, KeyError, AttributeError, TypeError) as exc:
            missing = [f'notice check failed: {exc}']
        problems += [f'{name} missing from {label}' for name in missing]
    rows = _table_rows(text)
    for item in shipped:
        if item.get('category') in ('first_party', 'first_party_asset') or not item.get('notices_required'):
            continue
        if not any(_row_lists(cells, item) for cells in rows):
            problems.append(f"{_component_id(item)} {item.get('version')} row with its license and status missing from {label}")
    return sorted(set(problems))


def evaluate(receipt: dict, decisions: list[dict], license_sha256: str | None, root: Path = ROOT,
             fresh: dict | None = None) -> dict:
    """Release report: every scope that ships something, whether it may be released and why not.

    ``fresh`` is a newly built inventory of the same tree and build: a supplied receipt must match it, and the fresh
    inventory is what gets evaluated, so fields outside the comparison cannot be edited in. Without ``fresh`` the
    receipt is evaluated as given; the command line always passes one.
    """
    receipt_problems = validate_receipt(receipt, root)
    if not receipt_problems and fresh is not None:
        receipt_problems = compare_with_fresh(receipt, fresh)
        if not receipt_problems:
            receipt = fresh
    if receipt_problems:
        receipt = {key: value for key, value in (receipt if isinstance(receipt, dict) else {}).items()
                   if key in ('components', 'unresolved_items') and isinstance(value, list)}
    components = [item for item in receipt.get('components', []) if isinstance(item, dict) and 'name' in item]
    by_id: dict[str, list[dict]] = {}
    for item in components:
        by_id.setdefault(_component_id(item), []).append(item)
    open_items: dict[str, dict[str, dict]] = {}
    shipped: dict[str, list[dict]] = {}
    not_public = []
    for item in receipt.get('unresolved_items', []):
        if not isinstance(item, dict) or 'id' not in item:
            continue
        scopes = artifact_scopes(item.get('distribution'))
        if not scopes:
            not_public.append(item['id'])
        for scope in scopes:
            open_items.setdefault(scope, {})[item['id']] = item
    for item in components:
        for scope in artifact_scopes(item.get('distribution')):
            shipped.setdefault(scope, []).append(item)
            if item.get('status') != 'resolved':
                open_items.setdefault(scope, {}).setdefault(_component_id(item), {
                    'id': _component_id(item), 'status': item.get('status'), 'license': item.get('license'),
                    'distribution': item.get('distribution')})
    rows = {row['artifact_scope']: row for row in decisions}
    license_changed = license_sha256 is not None and (rows.get('source') or {}).get('license_file_sha256') != license_sha256
    report = {}
    for scope in sorted(set(open_items) | set(shipped) | set(rows)):
        row, reasons = rows.get(scope), []
        accepted = (row or {}).get('accepted_items') or []
        uncovered = sorted(item_id for item_id, item in open_items.get(scope, {}).items()
                           if item_id in NOT_WAIVABLE or not _covered(accepted, item, by_id.get(item_id, [])))
        accepted_ids = {entry.get('id') for entry in accepted if isinstance(entry, dict)}
        stale = sorted(str(item_id) for item_id in accepted_ids - set(open_items.get(scope, {})))
        if row is None:
            reasons.append('no decision row for this artifact scope')
        else:
            if row.get('approval_status') != 'approved':
                reasons.append(f"decision is {row.get('approval_status') or 'missing'}")
            if not _text(row.get('authority')):
                reasons.append('no named authority recorded')
            if not _text(row.get('decision')):
                reasons.append('no decision recorded')
            if any(not isinstance(entry, dict) for entry in accepted):
                reasons.append('accepted items must pin id, license and version')
            excluded = set().union(*(_package_names(name) for name in row.get('excluded_packages') or []))
            for item in shipped.get(scope, []):
                if _package_names(item['name']) & excluded:
                    reasons.append(f"{item['name']} is excluded by the decision but ships in this artifact")
            reasons += _notice_problems(scope, row, shipped.get(scope, []), receipt, root)
        if scope in LICENSE_SCOPES and license_changed:
            reasons.append('LICENSE differs from the license recorded by the source decision')
        if scope == UNMAPPED:
            reasons.append('distribution kind is not mapped to an artifact scope')
        if uncovered:
            reasons.append(f'{len(uncovered)} unresolved item(s) not covered by the decision')
        waived = sorted(NOT_WAIVABLE & set(open_items.get(scope, {})))
        if waived:
            reasons.append(f"{', '.join(waived)}: resolved only by inventorying the locked release build")
        report[scope] = {'allowed': not reasons, 'reasons': reasons, 'unresolved': uncovered, 'stale_accepted': stale}
    if receipt_problems:
        report[INVENTORY] = {'allowed': False, 'reasons': receipt_problems, 'unresolved': [], 'stale_accepted': []}
    return {'allowed': bool(report) and all(scope['allowed'] for scope in report.values()), 'scopes': report,
            'not_public_unresolved': sorted(not_public)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--decisions', type=Path, default=ROOT / 'docs' / 'distribution-license-decision.md')
    parser.add_argument('--inventory', type=Path,
                        help='an S6-01 InventoryReceipt JSON; it must match a fresh inventory of the same tree and build')
    parser.add_argument('--frozen-build', type=Path, help='the built backend folder to inventory as shipped')
    parser.add_argument('--pyz-toc', type=Path, help="the build's PYZ-00.toc")
    parser.add_argument('--root', type=Path, default=ROOT, help='the source tree the receipt and notices belong to')
    args = parser.parse_args(argv)
    try:
        decisions = load_decisions(args.decisions)
        fresh = inventory.build_inventory(args.root, frozen_build=args.frozen_build, pyz_toc=args.pyz_toc)
        receipt = json.loads(args.inventory.read_text(encoding='utf-8')) if args.inventory is not None else fresh
        license_sha256 = hashlib.sha256((args.root / 'LICENSE').read_bytes()).hexdigest()
        report = evaluate(receipt, decisions, license_sha256, args.root, fresh=fresh)
    except (GateError, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({'allowed': False, 'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report['allowed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

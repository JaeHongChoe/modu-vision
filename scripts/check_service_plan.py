"""Fail CI on lost scope, broken dependencies, or unsupported completion claims."""
import argparse
import json
import re
from pathlib import Path

EVIDENCE_FILE = "docs/service-upgrade-evidence.json"
COVERAGE_FILE = "docs/service-upgrade-coverage.md"
COVERAGE_TABLE = "## 모든 기존 항목"
DIMENSIONS = ("implementation", "gui", "persist", "reopen", "failure", "handoff", "target")
FUNCTIONAL = ("implementation", "gui", "persist", "reopen", "failure", "handoff")
# The evidence kind each dimension accepts: a clicked button is GUI evidence, a native Windows run is platform evidence,
# a run on the target equipment is target evidence; the other functional dimensions accept a click, an API run, a
# unit test or a real-input run.
KINDS = {"gui": {"click"}, "target": {"target"}, "windows_native": {"native_windows"}}
DEFAULT_KINDS = {"click", "api", "unit", "real_input"}
ENTRY_TEXT = ("action", "expected", "observed", "reviewer")
TEST_FOLDERS = ("backend/tests/", "scripts/e2e/", "src/renderer/")
MIN_REASON = 10
DIMENSION_KEYS = {"state", "evidence", "reason", "reviewer"}
ENTRY_KEYS = {"task", "source_sha", "kind", "action", "expected", "observed", "reviewer", "test", "receipt", "receipt_sha256", "artifacts"}
WINDOWS_WORKFLOW = ".github/workflows/windows-native.yml"
_RECEIPT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_SHA40 = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ROW = re.compile(r"^\| ([FU]\d{3}) \| ([^|]*?) \| ([^|]*?) \| ([^|]*?) \|\s*$")


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _test_file_errors(label, test, root):
    """A test reference: a repository test file (exact case), optionally ::name, never outside the test folders."""
    if not isinstance(test, str) or not test.strip():
        return [f"{label}: test must be a path string"]
    relative = test.split("::", 1)[0]
    parts = relative.split("/")
    if "\\" in relative or relative.startswith("/") or ".." in parts or not relative.startswith(TEST_FOLDERS):
        return [f"{label}: test {test!r} must be a repository path under {', '.join(TEST_FOLDERS)}"]
    if relative.startswith("src/renderer/") and not relative.endswith(".test.cjs"):
        return [f"{label}: a renderer test reference must name a .test.cjs file"]
    folder = Path(root)
    for index, part in enumerate(parts):
        try:
            names = set(name for name in (entry.name for entry in folder.iterdir()))
        except OSError:
            names = set()
        if part not in names:  # exact case, so a wrong-case path fails on every platform alike
            return [f"{label}: test file {test!r} does not exist"]
        folder = folder / part
    return [] if folder.is_file() else [f"{label}: test file {test!r} does not exist"]


def _reference_errors(label, entry, kind, root):
    """What the evidence cites must match its kind: a click is a browser/app spec run (scripts/e2e) or a hashed
    receipt; a target run is a hashed receipt with its artifacts; a native Windows run is a hashed receipt or a test the
    Windows workflow runs."""
    test, receipt = entry.get("test"), entry.get("receipt")
    receipted = receipt is not None and _RECEIPT_NAME.fullmatch(_text(receipt)) and _SHA256.fullmatch(_text(entry.get("receipt_sha256")))
    if kind == "click" and not receipted and not (isinstance(test, str) and test.startswith("scripts/e2e/")):
        return [f"{label}: click evidence cites a scripts/e2e spec or a receipt"]
    if kind == "target" and not (receipted and entry.get("artifacts")):
        return [f"{label}: target evidence needs a receipt with receipt_sha256 and the run's artifacts"]
    if kind == "native_windows" and not receipted:
        try:
            workflow = (Path(root) / WINDOWS_WORKFLOW).read_text(encoding="utf-8")
        except OSError:
            workflow = ""
        if not (isinstance(test, str) and test.split("::", 1)[0] in workflow):
            return [f"{label}: native Windows evidence needs a receipt or a test the Windows workflow runs"]
    return []


def _dimension_errors(where, value, dimension, owners, known, root):
    """A RequirementEvidence dimension: pending, verified with evidence, or not_required with a reason."""
    if not isinstance(value, dict) or not isinstance(value.get("state"), str) or value["state"] not in {"pending", "verified", "not_required"}:
        return [f"{where}: state must be pending, verified or not_required"]
    errors = [f"{where}: unknown fields {sorted(set(value) - DIMENSION_KEYS)}"] if set(value) - DIMENSION_KEYS else []
    if value["state"] == "not_required":
        if dimension in ("implementation", "windows_native"):
            errors.append(f"{where}: {dimension} cannot be not_required")
        elif len(_text(value.get("reason"))) < MIN_REASON or not _text(value.get("reviewer")):
            errors.append(f"{where}: not_required needs a reason of at least {MIN_REASON} characters and its reviewer")
    if value["state"] == "verified":
        entries = value.get("evidence")
        if not isinstance(entries, list) or not entries:
            return errors + [f"{where}: verified needs evidence"]
        kinds = KINDS.get(dimension, DEFAULT_KINDS)
        for index, entry in enumerate(entries):
            label = f"{where} evidence {index + 1}"
            if not isinstance(entry, dict):
                errors.append(f"{label}: not an object")
                continue
            if set(entry) - ENTRY_KEYS:
                errors.append(f"{label}: unknown fields {sorted(set(entry) - ENTRY_KEYS)}")
            task = entry.get("task")
            if not isinstance(task, str) or task not in known:
                errors.append(f"{label}: unknown task {task!r}")
            elif dimension in FUNCTIONAL and task not in owners:
                errors.append(f"{label}: task {task} does not own this legacy feature (owners: {', '.join(owners)})")
            if not _SHA40.fullmatch(_text(entry.get("source_sha"))):
                errors.append(f"{label}: source_sha must be a full 40-character commit hash")
            if not isinstance(entry.get("kind"), str) or entry["kind"] not in kinds:
                errors.append(f"{label}: kind for {dimension} must be one of {sorted(kinds)}")
            errors += [f"{label}: {name} is required" for name in ENTRY_TEXT if not _text(entry.get(name))]
            test, receipt = entry.get("test"), entry.get("receipt")
            if test is None and receipt is None:
                errors.append(f"{label}: a test or a receipt is required")
            if test is not None:
                errors += _test_file_errors(label, test, root)
            if receipt is not None and (not isinstance(receipt, str) or not _RECEIPT_NAME.fullmatch(receipt.strip())
                                        or not _SHA256.fullmatch(_text(entry.get("receipt_sha256")))):
                errors.append(f"{label}: a receipt needs a plain name (no path) and receipt_sha256")
            if isinstance(entry.get("kind"), str) and entry["kind"] in kinds:
                errors += _reference_errors(label, entry, entry["kind"], root)
            artifacts = entry.get("artifacts", [])
            if not isinstance(artifacts, list) or any(not isinstance(item, dict) or not _text(item.get("id"))
                                                      or not _SHA256.fullmatch(_text(item.get("sha256"))) for item in artifacts):
                errors.append(f"{label}: artifacts must be a list of {{id, sha256}}")
    return errors


def derived_acceptance(record):
    """accepted only when every dimension is verified or not_required and native Windows is verified."""
    settled = all(isinstance(record.get(name), dict) and record[name].get("state") in {"verified", "not_required"}
                  for name in DIMENSIONS)
    platform = record.get("platform") if isinstance(record.get("platform"), dict) else {}
    windows = isinstance(platform.get("windows_native"), dict) and platform["windows_native"].get("state") == "verified"
    return "accepted" if settled and windows else "pending"


def _coverage_rows(coverage_text):
    """The rows of the main coverage table only (another table or a copy elsewhere is not read)."""
    lines = coverage_text.splitlines()
    try:
        start = lines.index(COVERAGE_TABLE)
    except ValueError:
        return None
    rows = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        match = _ROW.match(line)
        if match:
            rows.append(match.groups())
    return rows


def check_requirement_evidence(program, evidence, root, coverage_text):
    """S7-01: every legacy row has one RequirementEvidence record; no legacy claim becomes accepted without it."""
    if not isinstance(evidence, dict) or type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1 \
            or not isinstance(evidence.get("records"), list):
        return [f"{EVIDENCE_FILE}: schema_version 1 with a records list is required"]
    errors = []
    known = {row.get("id") for row in program.get("requirements", []) if isinstance(row, dict)}
    legacy = {row.get("legacy_id"): row for row in program.get("legacy_coverage", []) if isinstance(row, dict)}
    records = evidence["records"]
    bad = [index for index, record in enumerate(records) if not isinstance(record, dict) or not isinstance(record.get("id"), str)]
    if bad:
        errors.append(f"{EVIDENCE_FILE}: records {bad[:5]} are not objects with a string id")
    ids = [record["id"] for record in records if isinstance(record, dict) and isinstance(record.get("id"), str)]
    if bad or len(ids) != len(set(ids)) or set(ids) != set(legacy):
        errors.append(f"{EVIDENCE_FILE}: one record per legacy ID is required (missing, extra or duplicated IDs)")
    rows = _coverage_rows(coverage_text)
    if rows is None:
        return errors + [f"{COVERAGE_FILE}: the table '{COVERAGE_TABLE}' is missing"]
    table_ids = [row[0] for row in rows]
    if len(table_ids) != len(set(table_ids)) or set(table_ids) != set(legacy):
        errors.append(f"{COVERAGE_FILE}: the main table must list every legacy ID once (missing, extra or duplicated rows)")
    table = {row[0]: row for row in rows}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record["id"] not in legacy:
            continue
        identifier = record["id"]
        owners = [link for link in legacy[identifier].get("service_requirements", []) if isinstance(link, str)]
        unknown = sorted(set(record) - {"id", *DIMENSIONS, "platform", "prerequisites"})
        if unknown:
            errors.append(f"{identifier}: unknown fields {unknown}")
        for name in DIMENSIONS:
            errors += _dimension_errors(f"{identifier}.{name}", record.get(name), name, owners, known, root)
        platform = record.get("platform")
        if not isinstance(platform, dict) or "windows_native" not in platform:
            errors.append(f"{identifier}.platform: an object with windows_native is required")
        else:
            for name, value in platform.items():
                errors += _dimension_errors(f"{identifier}.platform.{name}", value, name if name == "windows_native" else "platform",
                                            owners, known, root)
        prerequisites = record.get("prerequisites")
        if not isinstance(prerequisites, list) or any(not _text(item) for item in prerequisites):
            errors.append(f"{identifier}.prerequisites: a list of non-empty texts is required")
        expected = derived_acceptance(record)
        if legacy[identifier].get("service_acceptance") != expected:
            errors.append(f"{identifier}: service_acceptance {legacy[identifier].get('service_acceptance')!r} differs from its evidence ({expected})")
        row = table.get(identifier)
        if row is not None:
            if row[3].strip() != expected:
                errors.append(f"{identifier}: {COVERAGE_FILE} shows {row[3].strip()!r}, the evidence gives {expected}")
            if row[2].strip() != ", ".join(owners):
                errors.append(f"{identifier}: {COVERAGE_FILE} owners {row[2].strip()!r} differ from the mapping {', '.join(owners)!r}")
    return errors


def check_program(program, root, evidence=None):
    root = Path(root)
    errors = []
    rows = program.get("requirements", [])
    ids = [row.get("id") for row in rows]
    known = set(ids)
    if None in known or len(ids) != len(known):
        errors.append("work package IDs are missing or duplicated")
    coverage = program.get("coverage_summary", {})
    if coverage.get("new_work_packages") != len(rows):
        errors.append("work package count differs from approved scope")
    graph = {}
    accepted = 0
    for row in rows:
        identifier = row.get("id", "<missing>")
        dependencies = row.get("depends_on", [])
        graph[identifier] = dependencies
        for dependency in dependencies:
            if dependency not in known:
                errors.append(f"{identifier}: unknown dependency {dependency}")
        plan = row.get("phase_plan", "")
        if not plan or not (root / plan).is_file():
            errors.append(f"{identifier}: missing phase plan")
        elif f"## {identifier} " not in (root / plan).read_text(encoding="utf-8"):
            errors.append(f"{identifier}: missing phase plan task")
        if not row.get("acceptance"):
            errors.append(f"{identifier}: missing acceptance assertions")
        if row.get("status") not in {"planned", "in_progress", "verification_pending", "accepted"}:
            errors.append(f"{identifier}: unknown status")
        if row.get("status") == "accepted":
            accepted += 1
            gates = row.get("acceptance_state", {})
            required = {"implementation", "contract", "gui", "persist", "reopen", "failure", "handoff", "windows_native", "target_execution"}
            if not required.issubset(gates) or any(value != "verified" for value in gates.values()):
                errors.append(f"{identifier}: pending acceptance cannot be marked accepted")
            if not row.get("evidence"):
                errors.append(f"{identifier}: accepted task has no evidence")
    active, done = set(), set()

    def visit(identifier):
        if identifier in active:
            errors.append(f"dependency cycle includes {identifier}")
            return
        if identifier in done:
            return
        active.add(identifier)
        for dependency in graph.get(identifier, []):
            if dependency in known:
                visit(dependency)
        active.remove(identifier)
        done.add(identifier)

    for identifier in known:
        visit(identifier)
    if coverage.get("new_plan_accepted_work_packages") != accepted:
        errors.append("accepted count differs from actual acceptance states")
    expected_legacy = set()
    for filename, key in [("docs/feature-program.json", "features"), ("docs/product-upgrade-program.json", "requirements")]:
        baseline = json.loads((root / filename).read_text(encoding="utf-8"))
        expected_legacy.update(row["id"] for row in baseline[key])
    legacy = program.get("legacy_coverage", [])
    legacy_ids = [row.get("legacy_id") for row in legacy]
    if len(legacy_ids) != len(set(legacy_ids)) or set(legacy_ids) != expected_legacy:
        errors.append("legacy mappings are missing, duplicated, or unknown")
    if coverage.get("mapped_ids") != len(expected_legacy) or coverage.get("unmapped_ids") != 0:
        errors.append("legacy coverage counts differ from baseline")
    for row in legacy:
        links = row.get("service_requirements", [])
        if not links or any(link not in known for link in links):
            errors.append(f"{row.get('legacy_id')}: missing or unknown service mapping")
    for refinement in program.get("scope_refinements", []):
        if refinement.get("owner_task") not in known or any(task not in known for task in refinement.get("integration_tasks", [])):
            errors.append(f"{refinement.get('id')}: unknown refinement owner/integration task")
        if not refinement.get("acceptance"):
            errors.append(f"{refinement.get('id')}: missing refinement acceptance")
    try:
        if evidence is None:
            evidence = json.loads((root / EVIDENCE_FILE).read_text(encoding="utf-8"))
        coverage_text = (root / COVERAGE_FILE).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        errors.append(f"requirement evidence could not be read: {exc}")
    else:
        errors += check_requirement_evidence(program, evidence, root, coverage_text)
    return {"receipt": "ServicePlanSourceGate", "ok": not errors, "errors": errors,
            "work_packages": len(rows), "legacy_rows": len(legacy), "accepted_work_packages": accepted,
            "accepted_legacy_rows": sum(1 for row in legacy if row.get("service_acceptance") == "accepted"),
            "scope": "registry, dependency and requirement-evidence integrity; evidence references are checked for form and "
                     "existing test files, not re-executed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    program = json.loads((args.root / "docs/service-upgrade-program.json").read_text(encoding="utf-8"))
    receipt = check_program(program, args.root)
    print(json.dumps(receipt, ensure_ascii=True, indent=2))  # a Windows console code page cannot fail the gate
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

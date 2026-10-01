"""Fail CI on lost scope, broken dependencies, or unsupported completion claims."""
import argparse
import json
from pathlib import Path


def check_program(program, root):
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
    return {"receipt": "ServicePlanSourceGate", "ok": not errors, "errors": errors,
            "work_packages": len(rows), "legacy_rows": len(legacy), "accepted_work_packages": accepted,
            "scope": "registry and dependency integrity; does not independently verify execution evidence"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    program = json.loads((args.root / "docs/service-upgrade-program.json").read_text(encoding="utf-8"))
    receipt = check_program(program, args.root)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0 if receipt["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""A green source gate must not conceal removed scope or pending acceptance."""
import copy
import importlib.util
import json
from pathlib import Path
import yaml
import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("service_plan_gate", ROOT / "scripts/check_service_plan.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def program():
    return json.loads((ROOT / "docs/service-upgrade-program.json").read_text(encoding="utf-8"))


def test_current_program_preserves_all_mapped_scope_without_claiming_acceptance():
    receipt = gate.check_program(program(), ROOT)
    assert receipt["ok"], receipt["errors"]
    assert receipt["work_packages"] == 82
    assert receipt["legacy_rows"] == 156
    assert receipt["accepted_work_packages"] == sum(row["status"] == "accepted" for row in program()["requirements"])


def test_removed_task_and_unknown_dependency_fail_the_real_gate():
    removed = program()
    removed["requirements"].pop()
    assert not gate.check_program(removed, ROOT)["ok"]
    missing = program()
    missing["requirements"][0]["depends_on"] = ["S9-missing"]
    assert any("unknown dependency" in row for row in gate.check_program(missing, ROOT)["errors"])


def test_dependency_cycle_is_rejected():
    cyclic = program()
    first, second = cyclic["requirements"][:2]
    first["depends_on"] = [second["id"]]
    second["depends_on"] = [first["id"]]
    assert any("dependency cycle" in row for row in gate.check_program(cyclic, ROOT)["errors"])


def test_green_implementation_cannot_make_native_or_quality_acceptance_green():
    claimed = program()
    claimed["requirements"][0]["status"] = "accepted"
    claimed["coverage_summary"]["new_plan_accepted_work_packages"] = 1
    errors = gate.check_program(claimed, ROOT)["errors"]
    assert any("pending acceptance" in row for row in errors)


@pytest.mark.parametrize('delta',[-1,1])
def test_stale_progress_counts_fail_without_changing_requirement_rows(delta):
    claimed=program()
    verified=sum(row['acceptance_state']['implementation']=='verified' for row in claimed['requirements'])
    claimed['execution']['progress']['parent_implementation']={'verified':verified+delta,'pending':len(claimed['requirements'])-verified-delta}
    result=gate.check_program(claimed,ROOT)
    assert not result['ok']
    assert any('progress counts do not match' in row for row in result['errors'])


def test_malformed_progress_counts_fail():
    claimed=program()
    claimed['execution']['progress']['parent_implementation']['verified']=True
    assert any('progress counts do not match' in row for row in gate.check_program(claimed,ROOT)['errors'])


def test_progress_status_snapshot_cannot_hide_or_invent_an_accepted_parent():
    claimed=program()
    claimed['execution']['progress']['parent_status']['accepted']=sum(row['status']=='accepted' for row in claimed['requirements'])+1
    result=gate.check_program(claimed,ROOT)
    assert not result['ok']
    assert any('progress counts do not match' in row for row in result['errors'])


def test_duplicate_or_lost_legacy_mapping_cannot_pass():
    duplicate = program()
    duplicate["legacy_coverage"].append(copy.deepcopy(duplicate["legacy_coverage"][0]))
    assert not gate.check_program(duplicate, ROOT)["ok"]
    lost = program()
    lost["legacy_coverage"][0]["service_requirements"] = []
    assert not gate.check_program(lost, ROOT)["ok"]


def test_ci_receipt_records_failed_skipped_and_missing_tests_without_release_claims(tmp_path):
    spec = importlib.util.spec_from_file_location("ci_receipt", ROOT / "scripts/write_ci_receipt.py")
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    missing = tmp_path / "missing.xml"
    assert ci.test_counts(missing) == {"status": "not_recorded"}
    junit = tmp_path / "failed.xml"
    junit.write_text('<testsuites><testsuite tests="4" failures="1" errors="0" skipped="2"/></testsuites>')
    observed = ci.receipt(ROOT, junit)
    assert observed["pytest"] == {"tests": 4, "failures": 1, "errors": 0, "skipped": 2, "status": "failed"}
    assert "Windows 11 installer" in observed["gates_not_covered"]
    assert "model quality approval" in observed["gates_not_covered"]


def test_runtime_artifact_path_uses_step_context_instead_of_job_context():
    # The runner context exists in step env, but is not permitted in job env.
    # YAML parsing alone cannot detect the rejection before a hosted job starts.
    for name in ["ci.yml", "windows-native.yml"]:
        workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))
        for job in workflow["jobs"].values():
            assert all("runner." not in str(value) for value in job.get("env", {}).values()), name
            e2e_steps = [step for step in job["steps"] if
                         "test:e2e:browser" in step.get("run", "") or
                         "test:e2e:electron" in step.get("run", "")]
            assert e2e_steps, name
            assert all(step.get("env", {}).get("MV_E2E_ARTIFACT_DIR") ==
                       "${{ runner.temp }}/modu-e2e" for step in e2e_steps), name

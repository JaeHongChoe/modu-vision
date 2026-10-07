"""Run S7-04's explicit local fault controls and bind outcomes to exact source.

These controls do not replace real target faults, human model quality approval,
native installer cutover, network equipment, or a 72-hour soak.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = {
 'team_edit_conflicts': [('test_team_data_api','test_api_lease_conflict_actor_and_token_redaction'),('test_dataset_metadata_api','test_review_filter_conflict_and_version_retains_metadata'),('test_project_preferences','test_preferences_persist_colors_flags_and_reject_stale_updates')],
 'role_revocation_and_actor_scope': [('test_team_data_api','test_shared_members_actor_cannot_be_forged_and_roles_cannot_bypass'),('test_service_s1_06','test_team_live_permission_revocation_and_upload_actor_binding')],
 'same_job_concurrent_submit': [('test_service_s1_02','test_concurrent_submissions_with_one_key_reserve_one_job'),('test_service_s1_02','test_concurrent_engine_http_submissions_with_one_key_start_one_run')],
 'api_restart_worker_recovery': [('test_service_s1_02','test_a_restart_reattaches_the_live_owned_worker_under_its_attempt_without_launching_again'),('test_service_s1_03','test_a_backend_that_started_second_takes_over_the_folder_once_the_owner_exits')],
 'network_interruption_and_partial_transfer': [('test_remote_compute_reliability','test_interrupted_upload_reconnects_from_durable_transfers_without_duplicate_launch'),('test_service_s1_06','test_chunk_recovery_discards_only_uncommitted_tail'),('test_service_s1_06','test_complete_partial_never_bypasses_current_authorization_or_reference')],
 'pid_reuse_and_foreign_reservations': [('test_remote_compute_reliability','test_cancellation_never_signals_pid_with_another_owned_identity'),('test_service_s1_03','test_the_sweep_keeps_unknown_uncertain_and_remote_reservations'),('test_shared_security_review','test_comparison_recovery_rejects_unrelated_pid_even_with_matching_creation_time')],
 'capacity_disk_full_and_oom': [('test_service_s1_04','test_disk_full_is_named_with_its_next_action'),('test_service_s1_04','test_out_of_memory_is_named_with_its_next_action'),('test_service_s1_04','test_the_production_failure_payload_of_a_local_job_is_classified'),('test_service_failure_matrix','test_cpu_worker_oom_keeps_reservation_until_owned_exit_and_preserves_foreign_work'),('test_remote_compute_reliability','test_worker_publishes_failed_terminal_receipt_in_reserved_blocks_when_disk_full'),('test_service_s5_02','test_admission_capacity_is_atomic_across_two_store_openers')],
 'cancel_races': [('test_service_s1_03','test_a_stop_that_wins_the_claim_race_ends_the_claim_and_frees_the_device'),('test_service_s1_04','test_a_failed_journal_save_after_the_cancel_file_still_records_the_signal_and_lets_the_stop_go_on')],
 'truth_permission_version_before_release': [('test_service_release_eligibility','test_truth_changed_after_approval_blocks_new_release'),('test_service_release_eligibility','test_changed_comparison_inputs_block_approve_and_revision'),('test_whole_flow_approval','test_changed_full_subject_or_unknown_truth_cannot_authorize'),('test_whole_flow_approval','test_reviewer_membership_is_rechecked_after_revocation'),('test_service_release_eligibility','test_central_rollback_rechecks_revocation_and_preserves_valid_history'),('test_service_s5_06','test_partial_rollback_resume_keeps_restored_targets_and_cannot_advance_deployment')],
 'partial_update_recovery': [('test_runtime_update_recovery','test_abrupt_process_exit_leaves_a_durable_recovery_intent'),('test_runtime_update_recovery','test_managed_restart_restores_prior_live_runtime_after_interrupted_apply'),('test_service_s6_04','test_interruption_blocks_attachment_then_finishes_exact_pair'),('test_service_s6_04','test_forward_recovery_preserves_post_cutover_data_and_refuses_stale_owner'),('test_service_s6_04','test_abrupt_subprocess_exit_keeps_recoverable_application_database_pair'),('test_service_s6_04','test_active_training_refuses_without_staging_or_process_stop')],
}


def selectors():
    result=[]
    for cases in SCENARIOS.values():
        for module,name in cases:
            path=ROOT/'backend/tests'/f'{module}.py'
            tree=ast.parse(path.read_text(encoding='utf-8'))
            if not any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name for n in tree.body):
                raise ValueError('Missing fault control: '+module+'::'+name)
            result.append('backend/tests/'+module+'.py::'+name)
    return sorted(set(result))


def source_manifest():
    paths=list((ROOT/'backend').rglob('*.py'))+[Path(__file__)]
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(set(paths)) if '__pycache__' not in p.parts}


def summarize(junit):
    tree=ET.parse(junit); observed={}
    for case in tree.iter('testcase'):
        module=(case.get('classname') or '').split('.')[-1]
        name=(case.get('name') or '').split('[')[0]
        state='failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed'
        observed.setdefault((module,name),[]).append({'name':case.get('name'),'state':state})
    results={}
    for scenario,required in SCENARIOS.items():
        cases=[{'selector':module+'::'+name,'observed':observed.get((module,name),[])} for module,name in required]
        verified=all(case['observed'] and all(row['state']=='passed' for row in case['observed']) for case in cases)
        results[scenario]={'state':'local_controls_verified' if verified else 'pending','cases':cases,
                           'target_execution_verified':False}
    return results


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    selected=selectors(); before=source_manifest(); started=time.time()
    # Parent test filters can remove a single parametrized fault while leaving
    # the same function's other cases green. This runner owns its selection.
    child_environment=os.environ.copy()
    child_environment.pop('PYTEST_ADDOPTS',None)
    with (args.output/'pytest.log').open('xb') as log:
        completed=subprocess.run([sys.executable,'-m','pytest','-q',*selected,'--junitxml='+str(args.output/'junit.xml')],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=False,env=child_environment)
        log.flush(); os.fsync(log.fileno())
    after=source_manifest()
    scenarios=summarize(args.output/'junit.xml') if (args.output/'junit.xml').exists() else {}
    verified=completed.returncode==0 and before==after and bool(scenarios) and all(r['state']=='local_controls_verified' for r in scenarios.values())
    receipt={'schema_version':1,'requirement':'S7-04','started_at_unix':started,'ended_at_unix':time.time(),
             'command_selectors':selected,'inherited_pytest_selection_options':False,'exit_code':completed.returncode,'source_files':before,'source_unchanged':before==after,
             'source_manifest_sha256':hashlib.sha256(json.dumps(before,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
             'scenarios':scenarios,'local_controls_verified':verified,'target_fault_matrix_verified':False,
             'boundaries':['HTTP/TestClient, real SQLite and owned process controls; injected failures are named in individual cases',
                           'CPU worker OOM is injected MemoryError with real CLI/terminal/owned-exit/lease observation; diskfull includes injected/classification controls, not destructive resource exhaustion',
                           'network interruption is a controlled transport failure, not physical cable/GPU42 failure',
                           'partial update covers runtime deployment and controlled-authority POSIX portable application/DB cutover, including abrupt owned subprocess exit and preserved later writes; no real publisher or OS installer qualification',
                           'actual native Windows tests waived; signing/hardware/human approval remain separate']}
    with (args.output/'receipt.json').open('x',encoding='utf-8') as out:
        json.dump(receipt,out,ensure_ascii=False,indent=2); out.flush(); os.fsync(out.fileno())
    print(json.dumps({'receipt':str(args.output/'receipt.json'),'local_controls_verified':verified,
                      'target_fault_matrix_verified':False,'exit_code':completed.returncode}))
    sys.exit(0 if verified else 1)


if __name__=='__main__': main()

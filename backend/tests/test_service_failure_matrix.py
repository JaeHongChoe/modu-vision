"""An omitted, skipped, or failed S7-04 control can never close the matrix."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

spec=importlib.util.spec_from_file_location('failure_matrix',Path(__file__).parents[2]/'scripts/acceptance/failure_matrix.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


def receipt(tmp_path,damaged=None,state=None):
    root=ET.Element('testsuites'); suite=ET.SubElement(root,'testsuite')
    for cases in module.SCENARIOS.values():
        for name,test in cases:
            if (name,test)==damaged and state=='missing': continue
            case=ET.SubElement(suite,'testcase',classname='backend.tests.'+name,name=test)
            if (name,test)==damaged and state in {'failure','error','skipped'}: ET.SubElement(case,state)
    file=tmp_path/'junit.xml'; ET.ElementTree(root).write(file); return file


def test_all_named_controls_exist_and_actual_xml_is_required(tmp_path):
    selectors=module.selectors()
    assert len(selectors)>=20 and len(selectors)==len(set(selectors))
    result=module.summarize(receipt(tmp_path))
    assert len(result)==10 and all(r['state']=='local_controls_verified' for r in result.values())
    assert all(r['target_execution_verified'] is False for r in result.values())


def test_matrix_runner_cannot_inherit_parameter_deselection(tmp_path, monkeypatch):
    """A user pytest filter must not silently omit one required parameter."""
    import os
    import sys
    from types import SimpleNamespace

    monkeypatch.setenv('PYTEST_ADDOPTS', '-k not class_semantics')
    output = tmp_path / 'run'
    observed = {}

    def run(command, **kwargs):
        observed['environment'] = kwargs.get('env')
        # The actual suite is exercised separately; this isolates the launch
        # boundary which previously inherited selection-changing pytest flags.
        raw = receipt(tmp_path).read_bytes()
        (output / 'junit.xml').write_bytes(raw)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['failure_matrix', '--output', str(output)])
    with pytest.raises(SystemExit) as ended:
        module.main()
    assert ended.value.code == 0
    assert observed['environment'] is not None
    assert not observed['environment'].get('PYTEST_ADDOPTS')
    assert os.environ['PYTEST_ADDOPTS'] == '-k not class_semantics'


def test_one_skipped_missing_or_failed_case_leaves_its_scenario_pending(tmp_path):
    scenario=next(iter(module.SCENARIOS)); control=module.SCENARIOS[scenario][0]
    for state in ('missing','failure','error','skipped'):
        result=module.summarize(receipt(tmp_path,control,state))
        assert result[scenario]['state']=='pending'
        assert any(r['state']=='local_controls_verified' for name,r in result.items() if name!=scenario)


@pytest.mark.parametrize('scenario,control', [
    ('team_edit_conflicts', ('test_project_preferences', 'test_preferences_persist_colors_flags_and_reject_stale_updates')),
    ('capacity_disk_full_and_oom', ('test_service_failure_matrix', 'test_cpu_worker_oom_keeps_reservation_until_owned_exit_and_preserves_foreign_work')),
    ('truth_permission_version_before_release', ('test_service_release_eligibility', 'test_changed_comparison_inputs_block_approve_and_revision')),
    ('truth_permission_version_before_release', ('test_whole_flow_approval', 'test_changed_full_subject_or_unknown_truth_cannot_authorize')),
    ('truth_permission_version_before_release', ('test_whole_flow_approval', 'test_reviewer_membership_is_rechecked_after_revocation')),
    ('truth_permission_version_before_release', ('test_service_s5_06', 'test_partial_rollback_resume_keeps_restored_targets_and_cannot_advance_deployment')),
    ('partial_update_recovery', ('test_service_s6_04', 'test_interruption_blocks_attachment_then_finishes_exact_pair')),
    ('partial_update_recovery', ('test_service_s6_04', 'test_forward_recovery_preserves_post_cutover_data_and_refuses_stale_owner')),
    ('partial_update_recovery', ('test_service_s6_04', 'test_abrupt_subprocess_exit_keeps_recoverable_application_database_pair')),
    ('partial_update_recovery', ('test_service_s6_04', 'test_active_training_refuses_without_staging_or_process_stop')),
])
@pytest.mark.parametrize('state', ('missing', 'failure', 'error', 'skipped'))
def test_matrix_requires_original_scope_fault_evidence(tmp_path, scenario, control, state):
    result = module.summarize(receipt(tmp_path, control, state))
    assert result[scenario]['state'] == 'pending'
    assert result[scenario]['target_execution_verified'] is False


def test_cpu_worker_oom_keeps_reservation_until_owned_exit_and_preserves_foreign_work(tmp_path, monkeypatch):
    """Inject MemoryError in the real CPU CLI; hold teardown after failed publication."""
    import hashlib
    import json
    import os
    import subprocess
    import sys
    import time
    from PIL import Image
    from backend.api import routes_training
    from backend.engine.shared_scheduler import ResourceLeases

    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'user'))
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '')
    source, output, injection = tmp_path / 'source', tmp_path / 'output', tmp_path / 'injection'
    for split in ('train', 'val'):
        for label in ('OK', 'NG'):
            folder = source / split / label
            folder.mkdir(parents=True)
            Image.new('RGB', (32, 32), 'blue' if label == 'OK' else 'red').save(folder / 'input.png')
    originals = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.png')}
    injection.mkdir()
    # Keep the production command/handshake, trainer construction, error writer,
    # observer and lease store real. Only allocation failure and slow teardown
    # are injected inside this test's child environment.
    (injection / 'sitecustomize.py').write_text('''
import json,time
from pathlib import Path
from backend.engine import trainer,device
def fail_on_cpu(self,job_id):
    if str(self.device) != 'cpu': raise AssertionError('Fault fixture requires CPU')
    raise MemoryError('controlled CPU allocator exhaustion')
trainer.UnifiedAutoMLTrainer.train=fail_on_cpu
original_clear=device.clear_device_cache
def held_cleanup():
    import sys
    if '--spec' in sys.argv:
        root=Path(sys.argv[sys.argv.index('--spec')+1]).parent
        status=root/'status.json'
        if status.is_file() and json.loads(status.read_text()).get('status')=='failed':
            (root/'oom-terminal-held').write_text('cpu')
            deadline=time.monotonic()+30
            while not (root/'release-oom-teardown').exists():
                if time.monotonic()>=deadline: raise RuntimeError('Fault fixture teardown timed out')
                time.sleep(.02)
    return original_clear()
device.clear_device_cache=held_cleanup
''', encoding='utf-8')
    manager = routes_training.TrainingJobManager()
    foreign = ResourceLeases(manager._leases.path, owner='independent-fixture-owner')
    assert foreign.acquire('job_unrelated_reserved', 'independent-cpu-host')
    foreign.mark_uncertain('job_unrelated_reserved')
    before = next(row for row in foreign.list() if row['job_id'] == 'job_unrelated_reserved')
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(120)'], start_new_session=True)
    record = None
    try:
        repo = Path(__file__).resolve().parents[2]
        monkeypatch.setenv('PYTHONPATH', os.pathsep.join((str(injection), str(repo))))
        record = manager.start_job('job_fault_cpu_oom', 'classification', str(source), str(output), device='cpu',
            config_overrides={'pretrained': False, 'backbone': 'resnet18', 'image_size': 32, 'epochs': 1, 'num_workers': 0})
        deadline = time.monotonic() + 60
        while not (output / 'oom-terminal-held').exists() and record.thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (output / 'oom-terminal-held').is_file(), record.error
        terminal = json.loads((output / 'status.json').read_text())
        assert terminal['job_id'] == record.job_id and terminal['status'] == 'failed'
        assert terminal['error'] == 'MemoryError: controlled CPU allocator exhaustion'
        assert record.process.poll() is None and record.thread.is_alive()
        assert any(row['job_id'] == record.job_id for row in manager._leases.list()), 'terminal publication is not process exit'
        assert next(row for row in foreign.list() if row['job_id'] == 'job_unrelated_reserved') == before
        assert unrelated.poll() is None
        (output / 'release-oom-teardown').write_text('release', encoding='utf-8')
        record.thread.join(30)
        assert not record.thread.is_alive() and record.process.poll() == 1
        assert record.status == 'failed'
        journal = json.loads((output / 'local_job.json').read_text())
        assert journal['owner_pid'] == record.process.pid and journal['worker_exit_confirmed'] is True
        assert journal['status'] == 'failed' and journal['worker_exit_code'] == 1
        assert terminal['spec_sha256'] == hashlib.sha256((output / 'local_spec.json').read_bytes()).hexdigest()
        saved = json.loads((output / 'job_receipt.json').read_text())
        assert saved['job_id'] == record.job_id and saved['status'] == 'failed'
        assert routes_training._job_observation(record, set())['cause'] == 'out_of_memory'
        assert not any(row['job_id'] == record.job_id for row in manager._leases.list())
        assert next(row for row in foreign.list() if row['job_id'] == 'job_unrelated_reserved') == before
        assert unrelated.poll() is None
        assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*.png')} == originals
    finally:
        if record is not None:
            output.mkdir(exist_ok=True)
            (output / 'release-oom-teardown').write_text('release', encoding='utf-8')
            record.thread.join(10)
            if record.thread.is_alive():
                manager.abort_job(record.job_id)
                record.thread.join(30)
        unrelated.terminate()
        unrelated.wait(timeout=10)
        foreign.release('job_unrelated_reserved', terminal=True)

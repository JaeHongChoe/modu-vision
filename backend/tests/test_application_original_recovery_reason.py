"""Original signed inert launch keeps its failure cause after observed direct exit.

Real Ed25519 layout, LaunchSupervisor reservation/claim/readiness, original
Popen and durable pointer/journal are used. The child ends cooperatively from
its test-owned file. No signals, training, GUI, cached proof or tree acceptance.
"""
from __future__ import annotations

import copy
import json
import subprocess
import time
from pathlib import Path

import pytest

from backend.tests.test_application_launch_controller import application_fixture
from backend.tests.test_application_launch_lease import controlled_canary_publication, reserve


@pytest.mark.parametrize('recovery_reason', [
    'Process-tree ownership is unresolved: Original Node shutdown budget expired',
    'x' * 500,
    '',
    None,
])
def test_original_direct_exit_preserves_durable_recovery_reason_and_blocks_release(
        tmp_path, recovery_reason, controlled_canary_publication):
    from backend.engine import application_launch_lease as lease
    code = """import os,time,sys
from pathlib import Path
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])
(root/'projects'/'original-main-ready').write_text('original child entered')
deadline=time.monotonic()+15
while not (root/'projects'/'original-main-release').exists():
 if time.monotonic()>=deadline:sys.exit(6)
 time.sleep(.01)
"""
    root, value, _ = application_fixture(tmp_path, code)
    owner = reserve(root, value)
    release = root / 'projects' / 'original-main-release'
    process = None
    try:
        row = owner.start()
        process = owner._process
        assert type(process) is subprocess.Popen and process.pid == row['process']['pid']
        deadline = time.monotonic() + 5
        while not (root / 'projects' / 'original-main-ready').exists():
            assert process.poll() is None, 'Original inert child ended before test admission'
            assert time.monotonic() < deadline, 'Original inert child did not become ready'
            time.sleep(.01)
        owner.claim(row['process'])
        ready = owner.ready(row['binding'], row['process'])
        assert ready['state'] == 'ready' and ready['reason'] is None
        if recovery_reason is not None:
            refusal = owner.recovery(recovery_reason)
            assert refusal['state'] == 'recovery_required' and refusal['reason'] == recovery_reason
        journal_path = root / lease.LEASES / owner.nonce / 'journal.json'
        before = json.loads(journal_path.read_bytes())
        names_before = {path.name for path in journal_path.parent.iterdir()}
        assert process.poll() is None
        release.write_text('cooperative normal exit')
        assert process.wait(timeout=5) == 0
        assert owner._process is process
        expected_reason = recovery_reason if recovery_reason else 'Process-tree exit is unverified'
        observed = owner.observe_exit()
        assert observed['state'] == 'recovery_required'
        assert observed['reason'] == expected_reason, 'Original cause was overwritten by generic direct-exit reason'
        assert observed['exit_observation'] == {'direct_child_pid': process.pid,
            'direct_child_returncode': 0, 'process_tree_exit_verified': False}
        after = json.loads(journal_path.read_bytes())
        assert after['reason'] == expected_reason and after['revision'] == before['revision'] + 1
        restored = copy.deepcopy(after)
        for key in ('state', 'revision', 'reason', 'exit_observation'):
            restored[key] = before[key]
        assert restored == before, 'Direct observation changed another ownership/binding field'
        assert {path.name for path in journal_path.parent.iterdir()} == names_before
        assert ('writer_drain' in after) == ('writer_drain' in before)
        if 'writer_drain' in before:
            assert after['writer_drain'] == before['writer_drain']
        again = owner.observe_exit()
        assert again['reason'] == expected_reason and again['exit_observation'] == observed['exit_observation']
        assert lease.inspect_launch(root)['reason'] == expected_reason
        for key in ('native_app_handshake_verified', 'actual_application_inference_verified',
                    'known_image_execution_qualified', 'model_quality_approved', 'release_ready'):
            assert observed[key] is False
        with pytest.raises(lease.LaunchLeaseError):
            owner.cancel()
        with pytest.raises(lease.LaunchLeaseError):
            lease.assert_quiescent(root)
    finally:
        if process is not None:
            if not release.exists():
                release.write_text('cooperative cleanup')
            process.wait(timeout=15)
            assert process.returncode is not None, 'Original Popen was not joined'
        owner.close()

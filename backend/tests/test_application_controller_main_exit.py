"""Original controller handle termination only; no app/tree/writer release claim."""
import json
from pathlib import Path
import time

import psutil
import pytest

from backend.engine import application_launch_lease as lease
from backend.engine.application_launch_quiescence import EPOCHS
from backend.tests.test_application_launch_controller import (
    application_fixture, controller_fixture, stop_controlled_stack,
    controlled_canary_publication,
)
from backend.tests.test_application_launch_electron import _wait_original_process_exit


# Signed fixture application awaits an exclusive owned trigger, then exits0.
# It deliberately claims no backend/native/model or whole-tree authority.
MAIN = r"""import os,time
from pathlib import Path
root=Path(os.environ['VISION_AI_STUDIO_USER_DATA_DIR'])
trigger=root/'projects'/'controlled-normal-main-exit.trigger'
deadline=time.monotonic()+30
while not trigger.exists():
 if time.monotonic()>=deadline:raise SystemExit(9)
 time.sleep(.01)
raise SystemExit(0)
"""


def _epoch_bytes(directory):
    assert directory.is_dir() and not directory.is_symlink()
    result = {}
    for file in sorted(directory.rglob('*')):
        assert not file.is_symlink()
        if file.is_file():
            assert file.stat().st_nlink == 1
            result[file.relative_to(directory).as_posix()] = file.read_bytes()
        else:
            assert file.is_dir()
    assert {'registry.json', 'publication.json', 'authority.lock'} <= set(result)
    return result


@pytest.mark.parametrize('transition_busy', [False, True], ids=['ordinary', 'busy-transition'])
def test_original_controller_stops_after_owned_main_exit_without_releasing_epoch(tmp_path, transition_busy):
    root, value, current = application_fixture(tmp_path, MAIN)
    pointers = {name: (root/name).read_bytes() for name in ('application-active.json', 'global-active.json')}
    child, ack = controller_fixture(root, value, current)
    original_controller = lease._identity(child.pid)
    original_main = None
    try:
        row = lease.inspect_launch(root)
        assert row['nonce'] == ack['nonce'] and row['supervisor'] == original_controller
        assert row['state'] == 'starting' and row['spawn_attempted'] and not row['claimed']
        original_main = row['process']
        assert original_main is not None and lease._identity(original_main['pid']) == original_main
        process = psutil.Process(original_main['pid'])
        assert process.ppid() == child.pid and process.create_time() == original_main['created_at']
        assert child.poll() is None  # Updater streams already disconnected normally.
        epoch = root/EPOCHS/row['nonce']
        retained_epoch = _epoch_bytes(epoch)
        registry = json.loads(retained_epoch['registry.json'])
        assert registry['state'] == 'open' and len(registry['writers']) == 1
        assert registry['writers'][0]['status'] == 'reserved' and registry['writers'][0]['process'] is None
        trigger = root/'projects'/'controlled-normal-main-exit.trigger'
        if transition_busy:
            # The original controller cannot publish exit or close a DB handle
            # while another exact per-lease transition owns the pinned mutex.
            with lease._transition_admission(root, row['nonce']):
                journal_before = (root/lease.LEASES/row['nonce']/'journal.json').read_bytes()
                with trigger.open('x') as writer:
                    writer.write('normal exit of this signed owned main only\n')
                _wait_original_process_exit(original_main, time.monotonic()+5)
                time.sleep(.5)  # More than two original controller polling turns.
                assert child.poll() is None
                assert lease._identity(child.pid) == original_controller
                assert (root/lease.LEASES/row['nonce']/'journal.json').read_bytes() == journal_before
                assert _epoch_bytes(epoch) == retained_epoch
        else:
            with trigger.open('x') as writer:
                writer.write('normal exit of this signed owned main only\n')

        # Current production times out here forever after recording the same
        # original child exit. Candidate must return only after durable recovery
        # and its own successful close; this wait reaps our original Popen.
        assert child.wait(timeout=5) == 0
        final = lease.inspect_launch(root)
        assert final['nonce'] == row['nonce'] and final['binding'] == row['binding']
        assert final['supervisor'] == original_controller and final['process'] == original_main
        assert final['state'] == 'recovery_required' and final['spawn_attempted'] and not final['claimed']
        assert final['exit_observation'] == {
            'direct_child_pid': original_main['pid'], 'direct_child_returncode': 0,
            'process_tree_exit_verified': False,
        }
        assert final['writer_drain'] == row['writer_drain']
        assert _epoch_bytes(epoch) == retained_epoch
        assert {name: (root/name).read_bytes() for name in pointers} == pointers
        with pytest.raises(lease.LaunchLeaseError, match='writer epoch ownership remains unresolved'):
            lease.assert_quiescent(root)
        with pytest.raises(lease.LaunchLeaseError, match='writer epoch ownership remains unresolved'):
            lease.LaunchSupervisor.reserve(root, value['authority'],
                pinned_authority_sha256=value['pinned_authority_sha256'])
        assert _epoch_bytes(epoch) == retained_epoch
    finally:
        # Every live signal remains confined to the signed owned fixture's
        # retained original main identity/group and our original controller Popen.
        if child.poll() is None:
            assert lease._identity(child.pid) == original_controller
        stop_controlled_stack(child, root, process=original_main)

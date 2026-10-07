"""Owned inert launch fixtures; no publisher, user home, model or GUI qualification."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import zipfile
from contextlib import contextmanager
import select

import psutil
import pytest

from backend.tests.test_global_migration import owned
from backend.tests.test_service_s6_04 import fixture, plan, canonical, sha


def installed(tmp_path, *, looping=False):
    from backend.engine import runtime_update as update
    root, *_ = owned(tmp_path)
    value = fixture(tmp_path)
    if looping:
        executable = b'#!/bin/sh\nwhile :; do sleep 1; done\n'
        archive = value['directory']/'application.zip'
        manifest = {'schema_version': 1, 'version': '1.0.0', 'platform': value['target']['platform'],
            'arch': value['target']['arch'], 'entrypoint': 'bin/app',
            'files': [{'path': 'bin/app', 'size': len(executable), 'sha256': sha(executable), 'executable': True}]}
        with zipfile.ZipFile(archive, 'w') as writer:
            writer.writestr('portable-application.json', canonical(manifest)); writer.writestr('bin/app', executable)
        value['payload'].update(sha256=sha(archive.read_bytes()), size=archive.stat().st_size)
        value['payload']['artifacts'][0].update(sha256=value['payload']['sha256'], size=value['payload']['size'])
        value['sign'](value['payload'])
    result = update.install_update(root, plan(root, value))
    return root, value, result


def reserve(root, value):
    from backend.engine.application_launch_lease import LaunchSupervisor
    return LaunchSupervisor.reserve(root, value['authority'], pinned_authority_sha256=value['pinned_authority_sha256'])


def stop_fixture(supervisor):
    process = supervisor._process
    if process is not None and process.poll() is None:
        assert os.getpgid(process.pid) == process.pid
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    supervisor.close()


@contextmanager
def backend_lifetime_admission(root):
    """Real separate-process shared fence, as backend.main lifespan retains."""
    script = ('from backend.engine.migration_guard import maintenance_guard; import sys; '
              'guard=maintenance_guard(sys.argv[1]); guard.__enter__(); '
              'print("shared-admitted",flush=True); sys.stdin.read(1); guard.__exit__(None,None,None)')
    child = subprocess.Popen([sys.executable, '-c', script, str(root)],
        cwd=Path(__file__).resolve().parents[2], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0], 'controlled shared admission did not start'
        assert child.stdout.readline(128).strip() == 'shared-admitted'
        yield child
    finally:
        if child.poll() is None:
            child.stdin.write('x'); child.stdin.flush(); child.stdin.close()
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=5)
        assert child.returncode == 0, child.stderr.read(2048)


def artifact_receipt(root, row):
    receipt = {'schema_version': 1, 'status': 'succeeded', 'nonce': row['nonce'],
        'binding': row['binding'], 'process': row['process']}
    expected = {}
    for name in ('checkpoint', 'input', 'output'):
        file = root/'projects'/('shared-admission-'+name+'.bin'); file.write_bytes(name.encode())
        receipt[name] = {'path': file.relative_to(root).as_posix(), 'sha256': sha(file.read_bytes())}
        expected[name+'_sha256'] = receipt[name]['sha256']
    return receipt, expected


def test_live_backend_shared_admission_allows_controller_transitions_and_blocks_cutover(tmp_path):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, current = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    second = fixture(tmp_path, version='1.1.0', key=value['key'], authority=value['authority'])
    second['target']['current_version'] = '1.0.0'; proposed = plan(root, second)
    try:
        row = supervisor.start()
        with backend_lifetime_admission(root):
            assert supervisor.claim(row['process'])['claimed'] is True
            ready = supervisor.ready(row['binding'], row['process'])
            assert ready['state'] == 'ready' and ready['native_app_handshake_verified'] is False
            assert lease.inspect_launch(root)['state'] == 'ready'
            receipt, expected = artifact_receipt(root, row)
            result = supervisor.known_image(receipt, expected=expected)
            assert result['status'] == 'artifact_binding_verified'
            assert result['actual_application_inference_verified'] is False
            assert result['known_image_execution_qualified'] is False
            assert supervisor.known_image(receipt, expected=expected) == result
            for mutation in (lambda: reserve(root, value), lambda: supervisor.start(),
                             lambda: update.install_update(root, proposed),
                             lambda: update.recover_update(root, current['update_id'])):
                with pytest.raises(ValueError, match='admission'): mutation()
            process = supervisor._process
            os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
            observed = supervisor.observe_exit()
            assert observed['state'] == 'recovery_required'
            assert observed['exit_observation']['process_tree_exit_verified'] is False
            supervisor.close()
            assert lease.inspect_launch(root)['state'] == 'recovery_required'
        with pytest.raises(ValueError, match='launch ownership'): update.install_update(root, proposed)
        with pytest.raises(ValueError): supervisor.cancel()
    finally: stop_fixture(supervisor)


def test_transition_mutex_refuses_overlapping_ready_without_stale_revision_loss(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, current = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    entered = threading.Event(); release = threading.Event(); results = []; errors = []
    try:
        row = supervisor.start(); supervisor.claim(row['process']); before = lease.inspect_launch(root)['revision']
        def pause(point):
            if point == 'before_lease_compare_and_swap': entered.set(); assert release.wait(10)
        monkeypatch.setattr(lease, '_checkpoint', pause)
        def ready():
            try: results.append(supervisor.ready(row['binding'], row['process']))
            except BaseException as exc: errors.append(exc)
        thread = threading.Thread(target=ready); thread.start()
        try:
            assert entered.wait(10)
            with pytest.raises(ValueError, match='transition'): supervisor.ready(row['binding'], row['process'])
            with pytest.raises(ValueError, match='transition'): lease.inspect_launch(root)
            database_handle = supervisor._lock
            with pytest.raises(ValueError, match='transition'): supervisor.close()
            assert supervisor._lock is database_handle
            with pytest.raises(ValueError, match='admission'): update.recover_update(root, current['update_id'])
        finally: release.set(); thread.join(15)
        assert not thread.is_alive() and not errors and results[0]['revision'] == before+1
        assert lease.inspect_launch(root)['revision'] == before+1
    finally:
        release.set(); stop_fixture(supervisor)


@pytest.mark.parametrize('damage', ['competing_revision', 'journal_bytes', 'pointer_bytes'])
def test_journal_compare_and_swap_refuses_changed_publication(tmp_path, monkeypatch, damage):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); supervisor.claim(row['process'])
        journal = root/lease.LEASES/supervisor.nonce/'journal.json'; pointer = root/lease.ACTIVE_LEASE
        changed = []
        def intervene(point):
            if point != 'before_lease_compare_and_swap': return
            if damage == 'competing_revision':
                record = json.loads(journal.read_bytes()); record['revision'] += 1; record['reason'] = 'controlled competing publication'
                publication = json.loads(pointer.read_bytes()); publication.update(revision=record['revision'], record_sha256=sha(canonical(record)))
                journal.write_bytes(canonical(record)); pointer.write_bytes(canonical(publication))
            elif damage == 'journal_bytes': journal.write_bytes(journal.read_bytes()+b'\n')
            else: pointer.write_bytes(pointer.read_bytes()+b'\n')
            changed.append((journal.read_bytes(), pointer.read_bytes()))
        monkeypatch.setattr(lease, '_checkpoint', intervene)
        with pytest.raises(ValueError, match='publication changed'): supervisor.ready(row['binding'], row['process'])
        assert len(changed) == 1 and changed[0] == (journal.read_bytes(), pointer.read_bytes())
    finally:
        monkeypatch.setattr(lease, '_checkpoint', lambda point: None); stop_fixture(supervisor)


def test_readback_acquires_mutex_before_partial_journal_pointer_validation(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    entered = threading.Event(); release = threading.Event(); results = []; errors = []
    try:
        row = supervisor.start(); supervisor.claim(row['process'])
        def pause(point):
            if point == 'after_transition_journal': entered.set(); assert release.wait(10)
        monkeypatch.setattr(lease, '_checkpoint', pause)
        def ready():
            try: results.append(supervisor.ready(row['binding'], row['process']))
            except BaseException as exc: errors.append(exc)
        thread = threading.Thread(target=ready); thread.start()
        try:
            assert entered.wait(10)
            # Journal has advanced and the pointer has not: another reader must
            # refuse busy before interpreting this valid in-flight publication.
            with pytest.raises(ValueError, match='transition is busy'): lease.inspect_launch(root)
        finally: release.set(); thread.join(15)
        assert not thread.is_alive() and not errors and results[0]['state'] == 'ready'
        assert lease.inspect_launch(root)['state'] == 'ready'
    finally:
        release.set(); stop_fixture(supervisor)


def test_transition_journal_crash_never_repairs_pointer_or_unlocks(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, current = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); supervisor.claim(row['process'])
        def crash(point):
            if point == 'after_transition_journal': raise KeyboardInterrupt()
        monkeypatch.setattr(lease, '_checkpoint', crash)
        with pytest.raises(KeyboardInterrupt): supervisor.ready(row['binding'], row['process'])
        journal = root/lease.LEASES/supervisor.nonce/'journal.json'; pointer = root/lease.ACTIVE_LEASE
        snapshot = journal.read_bytes(), pointer.read_bytes()
        with pytest.raises(ValueError, match='publication'): lease.inspect_launch(root)
        with pytest.raises(ValueError): supervisor.ready(row['binding'], row['process'])
        with pytest.raises(ValueError): update.recover_update(root, current['update_id'])
        assert snapshot == (journal.read_bytes(), pointer.read_bytes())
    finally:
        process = supervisor._process
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
        with pytest.raises(ValueError): supervisor.close()
        assert supervisor._lock is None


@pytest.mark.parametrize('schema_version', [True, 1.0])
def test_pointer_snapshot_reread_rejects_numeric_schema_coercion(tmp_path, monkeypatch, schema_version):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    original_load = lease._load
    try:
        row = supervisor.start(); supervisor.claim(row['process'])
        pointer = root/lease.ACTIVE_LEASE; journal = root/lease.LEASES/supervisor.nonce/'journal.json'; altered = []
        def replace_after_validation(path):
            record = original_load(path)
            publication = json.loads(pointer.read_bytes()); publication['schema_version'] = schema_version
            pointer.write_bytes(canonical(publication)); altered.append((journal.read_bytes(), pointer.read_bytes()))
            return record
        monkeypatch.setattr(lease, '_load', replace_after_validation)
        with pytest.raises(ValueError, match='publication changed'): supervisor.ready(row['binding'], row['process'])
        assert len(altered) == 1 and altered[0] == (journal.read_bytes(), pointer.read_bytes())
    finally:
        monkeypatch.setattr(lease, '_load', original_load)
        process = supervisor._process
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
        with pytest.raises(ValueError): supervisor.close()
        assert supervisor._lock is None


@pytest.mark.parametrize('schema_version', [True, 1.0])
def test_spawn_intent_requires_exact_integer_schema(tmp_path, schema_version):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); intent = root/lease.LEASES/supervisor.nonce/'spawn-intent.json'
        changed = json.loads(intent.read_bytes()); changed['schema_version'] = schema_version; intent.write_bytes(canonical(changed))
        with pytest.raises(ValueError, match='spawn ownership intent'): supervisor.claim(row['process'])
        with pytest.raises(ValueError): lease.assert_quiescent(root)
    finally:
        process = supervisor._process
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
        with pytest.raises(ValueError): supervisor.close()
        assert supervisor._lock is None


@pytest.mark.parametrize('proof', [1, 1.0])
def test_only_boolean_never_spawned_proof_can_pass_quiescence(tmp_path, proof):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path); supervisor = reserve(root, value); supervisor.cancel()
    journal = root/lease.LEASES/supervisor.nonce/'journal.json'; pointer = root/lease.ACTIVE_LEASE
    record = json.loads(journal.read_bytes()); record['exit_observation'] = {'never_spawned': proof}
    publication = json.loads(pointer.read_bytes()); publication['record_sha256'] = sha(canonical(record))
    journal.write_bytes(canonical(record)); pointer.write_bytes(canonical(publication))
    before = journal.read_bytes(), pointer.read_bytes()
    with pytest.raises(ValueError, match='unproved'): lease.assert_quiescent(root)
    assert before == (journal.read_bytes(), pointer.read_bytes())


def test_cancel_requires_exact_committed_database_binding_types(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path); supervisor = reserve(root, value)
    journal = root/lease.LEASES/supervisor.nonce/'journal.json'; pointer = root/lease.ACTIVE_LEASE
    record = json.loads(journal.read_bytes()); record['binding']['database_pointer']['fence'] = float(record['binding']['database_pointer']['fence'])
    publication = json.loads(pointer.read_bytes()); publication['record_sha256'] = sha(canonical(record))
    journal.write_bytes(canonical(record)); pointer.write_bytes(canonical(publication))
    before = journal.read_bytes(), pointer.read_bytes()
    with pytest.raises(ValueError, match='binding changed'): supervisor.cancel()
    assert before == (journal.read_bytes(), pointer.read_bytes())


@pytest.mark.parametrize('damage', ['missing', 'replaced', 'linked', 'hardlinked', 'fifo'])
def test_transition_lock_identity_damage_refuses_live_controller_and_readback(tmp_path, damage):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); lock = root/lease.LEASES/supervisor.nonce/'transition.lock'
        if damage == 'hardlinked': os.link(lock, root/'controlled-lock-link')
        else:
            lock.rename(root/'controlled-original-lock')
            if damage == 'replaced': lock.write_bytes(b'')
            elif damage == 'linked': lock.symlink_to(root/'controlled-original-lock')
            elif damage == 'fifo': os.mkfifo(lock)
        with backend_lifetime_admission(root):
            with pytest.raises(ValueError): supervisor.claim(row['process'])
            with pytest.raises(ValueError): lease.inspect_launch(root)
        with pytest.raises(ValueError): lease.assert_quiescent(root)
    finally:
        process = supervisor._process
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
        handle = supervisor._lock
        with pytest.raises(ValueError): supervisor.close()
        # Damaged mutex was never admitted: production cannot drop this handle
        # behind a possible in-flight controller. Release only our test fixture.
        assert supervisor._lock is handle
        handle.__exit__(None, None, None); supervisor._lock = None


def test_reservation_reopens_exact_committed_identity_and_blocks_updates(tmp_path):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, current = installed(tmp_path)
    supervisor = reserve(root, value); row = lease.inspect_launch(root)
    assert row['state'] == 'reserved' and len(row['nonce']) == 32
    assert row['binding']['installation_id'] == json.loads((root/'.global-migration-owner.json').read_bytes())['installation_id']
    assert row['binding']['update_id'] == current['update_id']
    assert row['binding']['application_generation'] == current['update_id']
    assert row['binding']['database_pointer'] == current['database_pointer']
    assert row['binding']['executable_sha256'] == sha(Path(row['binding']['executable']).read_bytes())
    assert row['supervisor']['pid'] == os.getpid() and row['supervisor']['created_at'] > 0
    before = (root/'application-active.json').read_bytes(), (root/'global-active.json').read_bytes()
    with pytest.raises(ValueError, match='launch ownership'): update.recover_update(root, current['update_id'])
    second = fixture(tmp_path, version='1.1.0', key=value['key'], authority=value['authority']); second['target']['current_version'] = '1.0.0'
    with pytest.raises(ValueError, match='launch ownership'): update.install_update(root, plan(root, second))
    assert before == ((root/'application-active.json').read_bytes(), (root/'global-active.json').read_bytes())
    assert supervisor.cancel()['state'] == 'exited'
    assert lease.assert_quiescent(root)['status'] == 'quiescent'
    assert update.install_update(root, plan(root, second))['version'] == '1.1.0'


def test_wrong_nonce_and_foreign_authority_never_adopt_a_lease(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path); supervisor = reserve(root, value)
    with pytest.raises(ValueError, match='nonce'): lease.LaunchSupervisor(root, 'f'*32).cancel()
    other = fixture(tmp_path, version='2.0.0', authority=tmp_path/'other-authority.json')
    with pytest.raises(ValueError): reserve(root, other)
    assert lease.inspect_launch(root)['nonce'] == supervisor.nonce
    supervisor.cancel()


def test_simultaneous_reservation_has_exactly_one_owner(tmp_path):
    root, value, _ = installed(tmp_path); barrier = threading.Barrier(2); results = []; errors = []
    def run():
        barrier.wait()
        try: results.append(reserve(root, value))
        except ValueError as exc: errors.append(str(exc))
    threads = [threading.Thread(target=run) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join(10); assert not t.is_alive()
    assert len(results) == len(errors) == 1
    results[0].cancel()


def test_live_claim_readiness_and_known_image_binding_are_separate(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); assert row['state'] == 'starting'
        assert lease.inspect_launch(root)['process']['pid'] == supervisor._process.pid
        with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)
        assert supervisor.claim(row['process'])['state'] == 'starting'
        assert supervisor.ready(row['binding'], row['process'])['state'] == 'ready'
        paths = {}
        for name in ('checkpoint', 'input', 'output'):
            file = root/'projects'/('launch-fixture-'+name+'.bin'); file.write_bytes(name.encode()); paths[name] = {'path': file.relative_to(root).as_posix(), 'sha256': sha(file.read_bytes())}
        receipt = {'schema_version': 1, 'status': 'succeeded', 'nonce': row['nonce'], 'binding': row['binding'], 'process': row['process'], **paths}
        expected = {name+'_sha256': entry['sha256'] for name, entry in paths.items()}
        verified = supervisor.known_image(receipt, expected=expected)
        assert verified['status'] == 'artifact_binding_verified' and verified['model_quality_approved'] is False
        assert verified['actual_application_inference_verified'] is False and verified['known_image_execution_qualified'] is False
        assert verified['release_ready'] is False
        saved = root/lease.LEASES/supervisor.nonce/'known-image-receipt.json'
        before = saved.read_bytes(), lease.inspect_launch(root)['revision']
        repeated = supervisor.known_image(receipt, expected=expected)
        assert repeated == verified
        assert before == (saved.read_bytes(), lease.inspect_launch(root)['revision'])
        alternate = root/'projects'/'alternate-output.bin'; alternate.write_bytes(b'another bound output')
        different = json.loads(json.dumps(receipt)); different['output'] = {'path': alternate.relative_to(root).as_posix(), 'sha256': sha(alternate.read_bytes())}
        other_pins = {**expected, 'output_sha256': different['output']['sha256']}
        with pytest.raises(ValueError, match='Another known-image receipt'): supervisor.known_image(different, expected=other_pins)
        assert before == (saved.read_bytes(), lease.inspect_launch(root)['revision'])
        bad = json.loads(json.dumps(receipt)); bad['binding']['source_sha256'] = 'e'*64
        with pytest.raises(ValueError, match='binding'): supervisor.known_image(bad, expected=expected)
        bad = json.loads(json.dumps(receipt)); bad['checkpoint']['sha256'] = 'e'*64
        with pytest.raises(ValueError, match='checkpoint'): supervisor.known_image(bad, expected=expected)
        (root/paths['output']['path']).write_bytes(b'changed output')
        with pytest.raises(ValueError, match='output'): supervisor.known_image(receipt, expected=expected)
    finally: stop_fixture(supervisor)
    assert lease.inspect_launch(root)['state'] == 'recovery_required'
    with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)


def test_direct_child_exit_cannot_prove_tree_exit_or_release_database_ownership(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path); supervisor = reserve(root, value)
    try:
        supervisor.start(); supervisor._process.wait(timeout=5)
        row = supervisor.observe_exit()
        assert row['state'] == 'recovery_required'
        assert row['exit_observation']['direct_child_returncode'] == 0
        assert row['exit_observation']['process_tree_exit_verified'] is False
        with pytest.raises(ValueError): supervisor.cancel()
        with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)
    finally: supervisor.close()


def test_supervisor_crash_before_claim_remains_durable_and_cannot_be_reclaimed_from_pid_absence(tmp_path):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, result = installed(tmp_path)
    script = 'from backend.engine.application_launch_lease import LaunchSupervisor; import os,sys; LaunchSupervisor.reserve(sys.argv[1],sys.argv[2],pinned_authority_sha256=sys.argv[3]); os._exit(17)'
    child = subprocess.run([sys.executable, '-c', script, str(root), str(value['authority']), value['pinned_authority_sha256']], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
    assert child.returncode == 17, child.stderr
    row = lease.inspect_launch(root); assert row['state'] == 'reserved'
    assert row['supervisor']['pid'] != os.getpid()
    with pytest.raises(ValueError, match='supervisor'): lease.LaunchSupervisor(root, row['nonce']).cancel()
    with pytest.raises(ValueError, match='launch ownership'): update.recover_update(root, result['update_id'])


@pytest.mark.parametrize('damage', ['pointer_missing', 'journal_changed', 'linked_pointer', 'unknown_history', 'exited_with_spawn_intent'])
def test_unknown_or_contradictory_ownership_blocks_every_update_mutation(tmp_path, damage):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, result = installed(tmp_path); supervisor = reserve(root, value)
    pointer = root/lease.ACTIVE_LEASE; journal = root/lease.LEASES/supervisor.nonce/'journal.json'
    if damage == 'pointer_missing': pointer.unlink()
    elif damage == 'journal_changed': journal.write_text('{}')
    elif damage == 'linked_pointer': pointer.rename(root/'foreign-pointer'); pointer.symlink_to(root/'foreign-pointer')
    elif damage == 'unknown_history': (root/lease.LEASES/'unknown').mkdir()
    else:
        supervisor.cancel(); (journal.parent/'spawn-intent.json').write_text('{}')
    with pytest.raises(ValueError): lease.assert_quiescent(root)
    with pytest.raises(ValueError): update.recover_update(root, result['update_id'], action='finish')
    assert not (root/'application-update-pending.json').exists()


def test_spawn_failure_stays_ambiguous_and_never_unlocks(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path); supervisor = reserve(root, value)
    def fail(*a, **kw): raise OSError('controlled pre-exec failure')
    monkeypatch.setattr(lease.subprocess, 'Popen', fail)
    with pytest.raises(OSError): supervisor.start()
    assert lease.inspect_launch(root)['state'] == 'recovery_required'
    with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)
    supervisor.close()


def test_ready_mismatch_and_reopened_handle_cannot_clear_a_started_lease(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    try:
        row = supervisor.start(); supervisor.claim(row['process'])
        wrong = dict(row['binding']); wrong['database_pointer'] = {**wrong['database_pointer'], 'fence': 999}
        with pytest.raises(ValueError, match='binding'): supervisor.ready(wrong, row['process'])
        wrong_process = dict(row['process']); wrong_process['created_at'] += 1
        with pytest.raises(ValueError, match='process'): supervisor.ready(row['binding'], wrong_process)
        reopened = lease.LaunchSupervisor(root, supervisor.nonce)
        with pytest.raises(ValueError, match='handle'): reopened.observe_exit()
        assert lease.inspect_launch(root)['state'] == 'starting'
    finally: stop_fixture(supervisor)


def test_reserved_journal_crash_before_pointer_publication_is_not_absence_of_ownership(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path)
    def crash(point):
        if point == 'after_reserved_journal': raise KeyboardInterrupt()
    monkeypatch.setattr(lease, '_checkpoint', crash)
    with pytest.raises(KeyboardInterrupt): reserve(root, value)
    assert not (root/lease.ACTIVE_LEASE).exists()
    with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)


def test_update_admission_wins_before_database_cutover_and_refuses_reservation(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, _ = installed(tmp_path)
    second = fixture(tmp_path, version='1.1.0', key=value['key'], authority=value['authority']); second['target']['current_version'] = '1.0.0'
    proposed = plan(root, second); entered = threading.Event(); release = threading.Event(); result = []; errors = []
    def pause(point):
        if point == 'before_database': entered.set(); assert release.wait(10)
    monkeypatch.setattr(update, '_checkpoint', pause)
    def run():
        try: result.append(update.install_update(root, proposed))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=run); thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(ValueError, match='admission'): reserve(root, value)
        assert not (root/lease.ACTIVE_LEASE).exists()
    finally: release.set(); thread.join(15)
    assert not thread.is_alive() and not errors and result[0]['version'] == '1.1.0'


def test_reservation_admission_wins_and_refuses_cutover_before_pointer_publication(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, _ = installed(tmp_path)
    second = fixture(tmp_path, version='1.1.0', key=value['key'], authority=value['authority']); second['target']['current_version'] = '1.0.0'
    proposed = plan(root, second); entered = threading.Event(); release = threading.Event(); owners = []; errors = []
    def pause(point):
        if point == 'after_reserved_journal': entered.set(); assert release.wait(10)
    monkeypatch.setattr(lease, '_checkpoint', pause)
    def run():
        try: owners.append(reserve(root, value))
        except BaseException as exc: errors.append(exc)
    thread = threading.Thread(target=run); thread.start()
    try:
        assert entered.wait(10)
        with pytest.raises(ValueError, match='admission'): update.install_update(root, proposed)
        assert not (root/'application-update-pending.json').exists()
    finally: release.set(); thread.join(15)
    assert not thread.is_alive() and not errors and len(owners) == 1
    owners[0].cancel()


def test_supervisor_exit_after_spawn_keeps_live_child_and_durable_ambiguous_ownership(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path, looping=True)
    script = 'from backend.engine.application_launch_lease import LaunchSupervisor; import os,sys; owner=LaunchSupervisor.reserve(sys.argv[1],sys.argv[2],pinned_authority_sha256=sys.argv[3]); owner.start(); os._exit(17)'
    child = subprocess.run([sys.executable, '-c', script, str(root), str(value['authority']), value['pinned_authority_sha256']], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20)
    assert child.returncode == 17, child.stderr
    row = lease.inspect_launch(root)
    try:
        assert row['state'] == 'starting' and row['process'] is not None
        observed = psutil.Process(row['process']['pid']); assert observed.create_time() == row['process']['created_at']
        with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)
        with pytest.raises(ValueError, match='supervisor'): lease.LaunchSupervisor(root, row['nonce']).observe_exit()
    finally:
        pid = row['process']['pid']; process = psutil.Process(pid)
        assert process.create_time() == row['process']['created_at'] and os.getpgid(pid) == os.getsid(pid) == pid
        os.killpg(pid, signal.SIGTERM)
    with pytest.raises(ValueError, match='launch ownership'): lease.assert_quiescent(root)


def test_interrupted_known_image_publication_refuses_repair_or_mutation(tmp_path, monkeypatch):
    from backend.engine import application_launch_lease as lease, runtime_update as update
    root, value, installed_row = installed(tmp_path, looping=True); supervisor = reserve(root, value)
    row = supervisor.start(); supervisor.claim(row['process']); supervisor.ready(row['binding'], row['process'])
    receipt = {'schema_version': 1, 'status': 'succeeded', 'nonce': row['nonce'], 'binding': row['binding'], 'process': row['process']}
    expected = {}
    for name in ('checkpoint', 'input', 'output'):
        file = root/'projects'/('interrupted-'+name+'.bin'); file.write_bytes(name.encode())
        receipt[name] = {'path': file.relative_to(root).as_posix(), 'sha256': sha(file.read_bytes())}; expected[name+'_sha256'] = receipt[name]['sha256']
    def crash(point):
        if point == 'after_known_image_receipt': raise KeyboardInterrupt()
    monkeypatch.setattr(lease, '_checkpoint', crash)
    try:
        with pytest.raises(KeyboardInterrupt): supervisor.known_image(receipt, expected=expected)
        saved = root/lease.LEASES/supervisor.nonce/'known-image-receipt.json'; before = saved.read_bytes()
        with pytest.raises(ValueError): supervisor.known_image(receipt, expected=expected)
        assert saved.read_bytes() == before
        with pytest.raises(ValueError): update.recover_update(root, installed_row['update_id'])
    finally:
        process = supervisor._process
        if process.poll() is None: os.killpg(process.pid, signal.SIGTERM); process.wait(timeout=5)
        with pytest.raises(ValueError): supervisor.close()
        assert supervisor._lock is None


def test_standalone_cli_exposes_diagnostics_without_creating_a_dead_supervisor_reservation(tmp_path):
    from backend.engine import application_launch_lease as lease
    root, value, _ = installed(tmp_path)
    command = [sys.executable, '-m', 'backend.engine.application_launch_lease']
    cwd = Path(__file__).resolve().parents[2]
    observed = subprocess.run(command+['inspect', '--root', str(root)], cwd=cwd, capture_output=True, text=True, timeout=20)
    assert observed.returncode == 0 and json.loads(observed.stdout)['state'] == 'absent'
    refused = subprocess.run(command+['reserve', '--root', str(root), '--authority', str(value['authority']),
        '--pinned-authority-sha256', value['pinned_authority_sha256']], cwd=cwd, capture_output=True, text=True, timeout=20)
    assert refused.returncode == 2
    assert not (root/lease.ACTIVE_LEASE).exists() and not (root/lease.LEASES).exists()


@pytest.fixture(autouse=True)
def controlled_canary_publication(monkeypatch):
    """Scope this legacy lifecycle fixture to controlled canary proof only.

    Actual staged source CPU math is tested independently; these tests retain
    their original signed-layout, admission and recovery assertions.
    """
    from backend.tests.test_staged_update_canary import controlled_proof
    controlled_proof(monkeypatch)

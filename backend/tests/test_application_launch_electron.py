"""Owned environment boundaries; actual Electron proof requires pinned artifacts.

No-spawn probes qualify the production lease environment, not native startup.
Controlled fixture publication supplies an already committed disposable pair;
it does not qualify the staged installer or a publisher.
"""
import os
from pathlib import Path
import stat
import subprocess
import sys
import hashlib
import json
import select
import shutil
import time
import zipfile

import pytest

from backend.engine import application_launch_lease as lease
from backend.tests.test_application_launch_lease import installed, reserve
from backend.tests.test_staged_update_canary import controlled_proof

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Owned lease/private-directory boundary is POSIX only')

class ControlledNoSpawn(RuntimeError):
    pass


def no_spawn_owner(tmp_path, monkeypatch):
    controlled_proof(monkeypatch)
    root, value, _ = installed(tmp_path)
    owner = reserve(root, value)
    captured = []

    def refuse_spawn(command, **options):
        captured.append((command, options))
        raise ControlledNoSpawn('Controlled Popen boundary; no child created')

    monkeypatch.setattr(lease.subprocess, 'Popen', refuse_spawn)
    return root, owner, captured


def test_owned_main_spawn_uses_derived_private_environment_without_inherited_authority(tmp_path, monkeypatch):
    root, owner, captured = no_spawn_owner(tmp_path, monkeypatch)
    before = {name: (root/name).read_bytes() for name in ('application-active.json', 'global-active.json')}
    for key in ('HOME', 'TMPDIR', 'PYTHONPATH', 'PYTHONSTARTUP', 'NODE_OPTIONS', 'LD_PRELOAD',
                'DYLD_INSERT_LIBRARIES', 'MODU_CONTROLLED_SECRET'):
        monkeypatch.setenv(key, 'Controlled inherited value that must be excluded')
    try:
        with pytest.raises(ControlledNoSpawn):
            owner.start(bootstrap=True)
        assert len(captured) == 1
        command, options = captured[0]
        row = owner._owned()
        assert command == [row['binding']['executable']]
        assert options['cwd'] == root and options['close_fds'] is True
        assert len(options['pass_fds']) == 1
        env = options['env']
        home = root/'.application-runtime-homes'/owner.nonce
        assert env['HOME'] == env['USERPROFILE'] == str(home)
        assert env['TMPDIR'] == env['TMP'] == env['TEMP'] == str(home/'tmp')
        expected = {'XDG_CACHE_HOME': home/'cache', 'XDG_CONFIG_HOME': home/'config',
                    'TORCH_HOME': home/'cache/torch', 'HF_HOME': home/'cache/huggingface',
                    'MPLCONFIGDIR': home/'cache/matplotlib', 'YOLO_CONFIG_DIR': home/'config/yolo'}
        for name, path in expected.items():
            assert env[name] == str(path)
        directories = {home.parent, home, home/'tmp', *expected.values()}
        for path in directories:
            info = path.lstat()
            assert stat.S_ISDIR(info.st_mode) and not path.is_symlink()
            assert stat.S_IMODE(info.st_mode) == 0o700 and info.st_uid == os.geteuid()
        assert env['CUDA_VISIBLE_DEVICES'] == '' and env['NVIDIA_VISIBLE_DEVICES'] == 'none'
        for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE', 'HF_DATASETS_OFFLINE',
                     'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
            assert env[name] == '1'
        assert not set(env).intersection({'PYTHONPATH', 'PYTHONSTARTUP', 'NODE_OPTIONS',
                                         'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'MODU_CONTROLLED_SECRET'})
        assert row['state'] == 'recovery_required' and row['spawn_attempted'] is True
        assert row['process'] is None
        assert before == {name: (root/name).read_bytes() for name in before}
    finally:
        owner.close()
    assert home.is_dir(), 'Failed launch must retain its own private home; no tree exit was proved'


@pytest.mark.skipif(sys.platform != 'darwin', reason='Actual CoreFoundation home lookup qualifies macOS only')
def test_cocoa_home_lookup_uses_the_original_derived_private_runtime_home(tmp_path, monkeypatch):
    """Read-only Foundation helper, not an Electron or model execution."""
    pytest.importorskip('Foundation', reason='Host Foundation bridge is required for actual home lookup')
    original_popen = subprocess.Popen
    root, owner, captured = no_spawn_owner(tmp_path, monkeypatch)
    try:
        with pytest.raises(ControlledNoSpawn): owner.start(bootstrap=True)
        env = captured[0][1]['env']
    finally:
        owner.close()
    monkeypatch.setattr(subprocess, 'Popen', original_popen)
    # Only the fixed derived home policy is given to this bounded metadata probe.
    # It prints a boolean rather than exposing the account's actual home path.
    probe = {name: value for name, value in env.items() if name in {'PATH', 'LANG', 'HOME', 'TMPDIR', 'CFFIXED_USER_HOME'}}
    code = 'import os;from Foundation import NSHomeDirectory;print(NSHomeDirectory()==os.environ["HOME"])'
    result = subprocess.run([sys.executable, '-I', '-B', '-c', code], env=probe,
        cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5, check=True)
    assert result.stdout.strip() == 'True', 'Native Foundation home lookup escaped the derived private HOME policy'


@pytest.mark.parametrize('unsafe', ['linked-parent', 'public-parent', 'existing-nonce'])
def test_unsafe_owned_runtime_home_refuses_before_spawn_and_preserves_foreign_files(tmp_path, monkeypatch, unsafe):
    root, owner, captured = no_spawn_owner(tmp_path, monkeypatch)
    parent = root/'.application-runtime-homes'
    foreign = tmp_path/'foreign-runtime-home'
    foreign.mkdir(mode=0o700)
    original = foreign/'preserve.txt'
    original.write_text('Controlled foreign bytes')
    if unsafe == 'linked-parent':
        # The canonical global-path guard refuses this link before launch admission.
        # A refusal cannot publish recovery through the deliberately unsafe root.
        parent.symlink_to(foreign, target_is_directory=True)
        def original_tree():
            result = {}
            for path in (root, *sorted(root.rglob('*'))):
                info = path.lstat()
                relative = path.relative_to(root).as_posix()
                identity = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
                if stat.S_ISLNK(info.st_mode):
                    result[relative] = (identity, 'link', os.readlink(path))
                elif stat.S_ISREG(info.st_mode):
                    result[relative] = (identity, 'file', path.read_bytes())
                else:
                    assert stat.S_ISDIR(info.st_mode)
                    result[relative] = (identity, 'directory')
            return result
        before = original_tree()
        original_foreign = original.read_bytes()
        assert owner._process is owner._lock is owner._bootstrap_channel is None
        refusal = '^Global control paths cannot follow links$'
        with pytest.raises(ValueError, match=refusal):
            owner.start(bootstrap=True)
        assert not captured
        with pytest.raises(ValueError, match=refusal):
            lease.inspect_launch(root)
        with pytest.raises(ValueError, match=refusal):
            owner.close()
        assert owner._process is owner._lock is owner._bootstrap_channel is None
        assert original_tree() == before
        assert original.read_bytes() == original_foreign
        assert list(foreign.iterdir()) == [original]
        assert parent.is_symlink() and os.readlink(parent) == str(foreign)
        # This temporary unsafe fixture stays intact; no restoration admits it.
        return
    elif unsafe == 'public-parent':
        parent.mkdir(mode=0o755)
        parent.chmod(0o755)
    else:
        parent.mkdir(mode=0o700)
        home = parent/owner.nonce
        home.mkdir(mode=0o700)
        (home/'prior.txt').write_text('Prior nonce bytes')
    try:
        with pytest.raises(ValueError, match='runtime home'):
            owner.start(bootstrap=True)
        assert not captured
        assert original.read_text() == 'Controlled foreign bytes'
        assert list(foreign.iterdir()) == [original]
        assert lease.inspect_launch(root)['state'] == 'recovery_required'
        if unsafe == 'existing-nonce':
            assert (home/'prior.txt').read_text() == 'Prior nonce bytes'
    finally:
        owner.close()


def _file_sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.fixture
def native_electron_artifacts():
    """Independently pinned private assembly, never a release/publisher authority."""
    path = os.environ.get('MODU_PRIVATE_ELECTRON_ARTIFACTS')
    if not path:
        pytest.skip('Actual private Electron/frozen-backend artifacts were not provisioned for this gate')
    pin = os.environ.get('MODU_PRIVATE_ELECTRON_ARTIFACTS_SHA256')
    assert pin and len(pin) == 64, 'The private assembly requires an independent receipt pin'
    receipt = Path(path)
    assert receipt.is_absolute() and receipt.is_file() and not receipt.is_symlink()
    raw = receipt.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == pin
    value = json.loads(raw)
    fields = {'schema_version', 'kind', 'archive', 'archive_size', 'archive_sha256',
              'portable_manifest_sha256', 'entrypoint', 'bundle_identifier',
              'backend_executable_sha256', 'backend_build_identity_sha256',
              'frontend_source_inputs', 'frontend_package_version'}
    assert set(value) == fields and type(value['schema_version']) is int and value['schema_version'] == 1
    assert value['kind'] == 'controlled_private_electron_portable_assembly'
    assert value['entrypoint'] == 'Owned Native.app/Contents/MacOS/owned-main'
    assert value['bundle_identifier'].startswith('com.modu.private.native.')
    suffix = value['bundle_identifier'].removeprefix('com.modu.private.native.')
    assert len(suffix) == 32 and set(suffix) <= set('0123456789abcdef')
    archive = Path(value['archive'])
    assert archive.is_absolute() and archive.is_file() and not archive.is_symlink()
    assert type(value['archive_size']) is int and archive.stat().st_size == value['archive_size']
    assert _file_sha256(archive) == value['archive_sha256']
    with zipfile.ZipFile(archive) as packed:
        manifest_raw = packed.read('portable-application.json')
        assert len(manifest_raw) <= 8*1024**2
        assert hashlib.sha256(manifest_raw).hexdigest() == value['portable_manifest_sha256']
        manifest = json.loads(manifest_raw)
        assert manifest['schema_version'] == 2 and manifest['entrypoint'] == value['entrypoint']
        assert manifest['version'] == '1.1.0' and manifest['platform'] == 'darwin' and manifest['arch'] == 'arm64'
    repository = Path(__file__).resolve().parents[2]
    assert value['frontend_source_inputs']
    for name, digest in value['frontend_source_inputs'].items():
        relative = Path(name)
        assert not relative.is_absolute() and '..' not in relative.parts
        assert _file_sha256(repository/relative) == digest, 'Private frontend assembly source differs'
    return value


def _private_native_pair(tmp_path, monkeypatch, artifacts):
    """Controlled already-committed seed; actual staged activation is separate."""
    from backend.tests.test_application_launch_execution import cpu_stack, canonical, sha
    from backend.tests.test_service_s6_04 import fixture, plan
    from backend.engine.runtime_update import install_update

    controlled_proof(monkeypatch)
    root, original, _, project, reviewed, _ = cpu_stack(tmp_path, deadline_ms=60000)
    current = fixture(tmp_path, version='1.1.0', key=original['key'], authority=original['authority'])
    current['target']['current_version'] = '1.0.0'
    original_archive = Path(artifacts['archive'])
    # The installer requires exactly the signed artifact rows in this directory.
    # Build receipts/logs stay outside it; retain the immutable assembly archive.
    directory = tmp_path/'native-offline-artifacts'
    directory.mkdir(mode=0o700)
    archive = directory/original_archive.name
    shutil.copyfile(original_archive, archive)
    assert _file_sha256(archive) == _file_sha256(original_archive) == artifacts['archive_sha256']
    current['directory'] = directory
    current['payload'].update(sha256=artifacts['archive_sha256'], size=artifacts['archive_size'])
    current['payload']['artifacts'][0].update(path=archive.name, sha256=artifacts['archive_sha256'],
                                           size=artifacts['archive_size'])
    current['sign'](current['payload'])
    installed_pair = install_update(root, plan(root, current))
    application = root/'.application-generations'/installed_pair['update_id']/'application'
    executable = application/artifacts['entrypoint']
    backend = executable.parent.parent/'Resources/backend_bin/vision_ai_backend'
    release = json.loads((backend.parent/'backend-release.json').read_bytes())
    inventory = release['inventory']
    assert _file_sha256(backend) == release['executable_sha256'] == artifacts['backend_executable_sha256']
    assert inventory['build_identity_sha256'] == artifacts['backend_build_identity_sha256']
    # Pin the actual compiled source resources, including any independently
    # composed prerequisites. A local source checkout cannot stand in for them.
    resources = backend.parent/'_internal'
    rows = []
    for row in inventory['resources']:
        name = row['path']
        if name.startswith('backend/') and name.endswith('.py'):
            file = resources/name
            assert file.is_file() and not file.is_symlink()
            assert _file_sha256(file) == row['sha256']
            rows.append({'path': name, 'size': file.stat().st_size, 'sha256': row['sha256']})
    assert rows and len({row['path'] for row in rows}) == len(rows)
    reviewed['runtime_source_sha256'] = sha(canonical(sorted(rows, key=lambda row: row['path'])))
    reviewed_path = project/'delivery/launch-known-image/plan.json'
    reviewed_path.write_bytes(canonical(reviewed))
    return root, current, installed_pair, project, reviewed, sha(reviewed_path.read_bytes()), backend, executable


def _retain_private_running_application(root, row, bundle_identifier):
    """Retain an exact original AppKit object before requesting normal quit."""
    import AppKit
    import psutil
    main = row['process']
    assert lease._identity(main['pid']) == main
    process = psutil.Process(main['pid'])
    executable = Path(row['binding']['executable'])
    assert Path(process.exe()) == executable and process.ppid() == row['supervisor']['pid']
    bundle = executable.parent.parent.parent
    assert bundle.name == 'Owned Native.app' and bundle.is_relative_to(root)
    native = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(main['pid'])
    assert native is not None and not native.isTerminated()
    assert native.processIdentifier() == main['pid']
    assert native.bundleIdentifier() == bundle_identifier
    assert Path(str(native.bundleURL().path())) == bundle
    assert Path(str(native.executableURL().path())) == executable
    assert lease._identity(main['pid']) == main
    return native


def _original_process_exited(identity):
    import psutil
    try:
        fresh = lease._identity(identity['pid'])
        # Changed command at the same birth is unresolved ownership, not exit.
        return fresh['created_at'] != identity['created_at'] or psutil.Process(identity['pid']).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


def _stop_original_controller(controller, original_controller, cleanup):
    if controller.poll() is None:
        assert cleanup.get('original_main_exited') is True, \
            'Original controller has unresolved admission; retain it without a signal'
        assert lease._identity(controller.pid) == original_controller
        controller.terminate()  # Original unreaped Popen only; never a PID/group lookup signal.
    controller.wait(timeout=10)


def test_live_controller_without_verified_admission_is_retained():
    """A modeled original handle cannot stop a real live identity on absence."""
    from types import SimpleNamespace
    current = lease._identity(os.getpid())
    actions = []
    handle = SimpleNamespace(pid=current['pid'], poll=lambda:None,
        terminate=lambda:actions.append('terminate'), wait=lambda **kwargs:actions.append('wait'))
    with pytest.raises(AssertionError, match='unresolved admission'):
        _stop_original_controller(handle, current, {})
    assert actions == []
    assert lease._identity(os.getpid()) == current


def test_live_same_birth_changed_command_cannot_prove_original_process_exit():
    """A real live process with a stale command pin remains unresolved."""
    import psutil
    current = lease._identity(os.getpid())
    cached = {**current, 'command_sha256': '0'*64}
    assert current['command_sha256'] != cached['command_sha256']
    assert psutil.Process(current['pid']).is_running()
    assert psutil.Process(current['pid']).create_time() == cached['created_at']
    assert _original_process_exited(cached) is False
    assert lease._identity(os.getpid()) == current


def _fresh_original_launch(root, expected=None):
    """Validate the fresh pointer/journal under the existing transition mutex."""
    until = time.monotonic()+5
    while True:
        try:
            row = lease.inspect_launch(root)
            break
        except ValueError as error:
            if 'transition is busy' not in str(error) or time.monotonic() >= until:
                raise
            time.sleep(.05)
    if expected is not None:
        for name in ('nonce', 'binding', 'supervisor', 'process'):
            assert row[name] == expected[name], 'Original native ownership changed; controller retained'
    return row


@pytest.mark.skipif(sys.platform != 'darwin', reason='Actual private Electron/AppKit startup requires macOS')
def test_actual_private_electron_original_controller_backend_epoch_and_cpu_pins(
        native_electron_artifacts, tmp_path, monkeypatch):
    """Real private native software; controlled seed/model are not acceptance."""
    import psutil
    from backend.tests.test_application_launch_controller import controller_arguments
    from backend.tests.test_application_launch_execution import EXPECTED_OUTPUT, PROJECT_ID, canonical, sha, wait_receipt

    artifacts = native_electron_artifacts
    values = _private_native_pair(tmp_path, monkeypatch, artifacts)
    root, value, current, project, reviewed, pin, backend, executable = values
    originals = [project/'project.json', project/reviewed['input_path'],
                 project/reviewed['package_path']/'manifest.json', project/reviewed['package_path']/'pipeline.json',
                 project/reviewed['package_path']/'models'/('a'*32)/'best_model.pt']
    input_pins = {str(file.relative_to(root)): _file_sha256(file) for file in originals}
    pointers = {name: (root/name).read_bytes() for name in ('application-active.json', 'global-active.json')}
    controller_home = tmp_path/'controller-home'
    controller_home.mkdir(mode=0o700)
    controller_tmp = controller_home/'tmp'; controller_tmp.mkdir(mode=0o700)
    environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'HOME': str(controller_home),
                   'USERPROFILE': str(controller_home), 'CFFIXED_USER_HOME': str(controller_home),
                   'TMPDIR': str(controller_tmp), 'TMP': str(controller_tmp), 'TEMP': str(controller_tmp),
                   'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none',
                   'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
                   'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1'}
    command = [str(backend), '--owned-application-launch-controller',
               *controller_arguments(root, value, current),
               '--cpu-known-image-workspace-id', reviewed['workspace_id'],
               '--cpu-known-image-project-id', PROJECT_ID, '--cpu-known-image-plan-sha256', pin]
    controller = subprocess.Popen(command, cwd=tmp_path, env=environment,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    original_controller = lease._identity(controller.pid)
    ack = None; native = None; bootstrap = None; proof = None; cleanup = {'normal_quit_requested': False}
    try:
        assert select.select([controller.stdout], [], [], 210)[0], 'No original compiled controller acknowledgement'
        raw = controller.stdout.readline(65537)
        assert raw, 'Original compiled controller ended before its acknowledgement'
        ack = json.loads(raw); assert ack['status'] == 'starting', ack
        # The updater connection ends here. The original controller keeps its
        # descriptors and ownership; no test bridge substitutes for Electron.
        controller.stdout.close(); controller.stderr.close()
        target, receipt = wait_receipt(root, ack['nonce'], controller, timeout=360)
        directory = target.parent
        row = json.loads((directory/'journal.json').read_bytes())
        bootstrap = json.loads((directory/'bootstrap-receipt.json').read_bytes())
        assert row['state'] == 'ready' and row['claimed'] is True
        assert row['supervisor'] == original_controller
        assert bootstrap['main_process'] == row['process'] == receipt['main_process']
        assert bootstrap['backend_process'] == receipt['backend_process']
        assert receipt['epoch'] == bootstrap['epoch'] and receipt['nonce'] == ack['nonce']
        assert receipt['binding'] == bootstrap['binding'] == row['binding']
        assert Path(row['binding']['executable']) == executable
        assert Path(bootstrap['backend_executable']) == backend
        assert bootstrap['backend_frozen'] is True and receipt['backend_frozen'] is True
        assert bootstrap['backend_executable_sha256'] == artifacts['backend_executable_sha256']
        assert bootstrap['backend_build_identity_sha256'] == artifacts['backend_build_identity_sha256']
        assert psutil.Process(row['process']['pid']).ppid() == controller.pid
        assert psutil.Process(bootstrap['backend_process']['pid']).ppid() == row['process']['pid']
        native = _retain_private_running_application(root, row, artifacts['bundle_identifier'])
        assert native.isFinishedLaunching()
        runtime_home = root/'.application-runtime-homes'/ack['nonce']
        expected_environment = {'HOME': str(runtime_home), 'USERPROFILE': str(runtime_home),
                                'CFFIXED_USER_HOME': str(runtime_home), 'TMPDIR': str(runtime_home/'tmp'),
                                'XDG_CACHE_HOME': str(runtime_home/'cache'),
                                'XDG_CONFIG_HOME': str(runtime_home/'config'),
                                'TORCH_HOME': str(runtime_home/'cache/torch'),
                                'HF_HOME': str(runtime_home/'cache/huggingface'),
                                'YOLO_CONFIG_DIR': str(runtime_home/'config/yolo'),
                                'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none',
                                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1'}
        observed_environment = {}
        for name, identity in [('main', row['process']), ('backend', bootstrap['backend_process'])]:
            assert lease._identity(identity['pid']) == identity
            observed = psutil.Process(identity['pid']).environ()
            # psutil documents that this snapshot may omit mutations after
            # exec. It qualifies observed launch fields, not runtime writers.
            # Record only nonsecret fields, excluding the backend API token.
            observed_environment[name] = {key: observed.get(key) for key in expected_environment}
            assert observed_environment[name] == expected_environment
            if name == 'main':
                assert observed.get('MPLCONFIGDIR') == str(runtime_home/'cache/matplotlib')
            assert lease._identity(identity['pid']) == identity
        assert receipt['actual_cpu_execution_verified'] is True
        assert receipt['owned_backend_execution_origin_verified'] is True
        assert receipt['execution_scope'] == 'controlled_frozen_backend'
        assert receipt['semantic_output'] == EXPECTED_OUTPUT and receipt['plan_sha256'] == pin
        result_path = project/receipt['output_path']
        assert _file_sha256(result_path) == receipt['output_sha256']
        result = json.loads(result_path.read_bytes())
        assert result['crops'][0]['recognized_text'] == 'A'
        assert result['runtime_execution']['device'] == 'cpu'
        assert result['runtime_execution']['isolated_process'] is True
        assert all(step['status'] not in ('error', 'warning_untrained') for step in result['execution_steps'])
        stable = {str(file): file.read_bytes() for file in directory.iterdir() if file.is_file()}
        stable[str(root/'application-launch-lease.json')] = (root/'application-launch-lease.json').read_bytes()
        inspection = [str(backend), '--owned-application-launch-controller', '--inspect-cpu-execution',
                      *controller_arguments(root, value, current), '--expected-launch-nonce', ack['nonce'],
                      '--cpu-known-image-workspace-id', reviewed['workspace_id'],
                      '--cpu-known-image-project-id', PROJECT_ID, '--cpu-known-image-plan-sha256', pin]
        readbacks = []
        for _ in range(2):
            observed = subprocess.run(inspection, cwd=tmp_path, env=environment, capture_output=True, timeout=90)
            assert observed.returncode == 0, observed.stdout
            value_read = json.loads(observed.stdout)
            assert value_read['status'] == 'verified' and value_read['receipt_sha256'] == sha(target.read_bytes())
            assert value_read['actual_cpu_execution_verified'] is True
            readbacks.append(value_read)
            assert {name: Path(name).read_bytes() for name in stable} == stable
        assert readbacks[0] == readbacks[1]
        assert lease._identity(controller.pid) == original_controller and controller.poll() is None
        assert lease._identity(row['process']['pid']) == row['process']
        assert lease._identity(bootstrap['backend_process']['pid']) == bootstrap['backend_process']
        assert {name: (root/name).read_bytes() for name in pointers} == pointers
        assert {str(file.relative_to(root)): _file_sha256(file) for file in originals} == input_pins
        for name in ('actual_application_inference_verified', 'native_app_handshake_verified',
                     'model_quality_approved', 'release_ready', 'worker_process_tree_exit_verified'):
            assert receipt[name] is False
        proof = {'schema_version': 1, 'kind': 'controlled_private_electron_startup_cpu_proof',
                 'actual_private_electron_original_controller_backend_startup_verified': True,
                 'original_controller': original_controller, 'bootstrap': bootstrap, 'receipt': receipt,
                 'receipt_sha256': sha(target.read_bytes()), 'input_pins': input_pins,
                 'pointer_pins': {name: sha(raw) for name, raw in pointers.items()},
                 'repeat_readbacks': readbacks, 'private_bundle_identifier': artifacts['bundle_identifier'],
                 'main_backend_launch_environment_snapshot': observed_environment,
                 'post_start_environment_mutations_qualified': False,
                 'frozen_matplotlib_hook_runtime_directory_qualified': False,
                 'ordinary_backend_device_configuration_qualified': False,
                 'controlled_committed_seed_not_staged_installer': True, 'publisher_verified': False,
                 'native_target_matrix_accepted': False, 'human_or_model_quality_approved': False,
                 'whole_process_tree_exit_verified': False, 'installed_release_accepted': False}
    finally:
        # A failed/partial admission is never repaired just to clean the test.
        # Only an exact retained private AppKit object can receive normal quit.
        pointer = root/'application-launch-lease.json'
        if pointer.exists():
            owned_pointer = json.loads(pointer.read_bytes())
            assert ack is None or owned_pointer['nonce'] == ack['nonce']
            journal = root/'.application-launches'/owned_pointer['nonce']/'journal.json'
            row = _fresh_original_launch(root)
            assert row['nonce'] == owned_pointer['nonce']
            assert row['supervisor'] == original_controller
            assert not row['spawn_attempted'] or row['process'] is not None, \
                'Spawn identity is unresolved; original controller retained'
            if row['process'] is not None and not _original_process_exited(row['process']):
                if native is None:
                    native = _retain_private_running_application(root, row, artifacts['bundle_identifier'])
                assert lease._identity(row['process']['pid']) == row['process'] and not native.isTerminated()
                cleanup['normal_quit_requested'] = bool(native.terminate())
                assert cleanup['normal_quit_requested'], 'Normal private app quit refused; original controller retained'
            until = time.monotonic()+30
            while row['process'] is not None and not _original_process_exited(row['process']) and time.monotonic()<until:
                time.sleep(.05)
            assert row['process'] is None or _original_process_exited(row['process']), 'Original main still live; controller retained'
            # The original controller can durably claim/spawn while normal
            # quit is in progress. Missing-backend decisions use this fresh,
            # mutex-validated state, never the pre-quit claimed flag.
            fresh = _fresh_original_launch(root, row)
            if bootstrap is None and (journal.parent/'bootstrap-receipt.json').exists():
                bootstrap = json.loads((journal.parent/'bootstrap-receipt.json').read_bytes())
            assert bootstrap is not None or not fresh['claimed'] or fresh['process'] is None, \
                'Admitted main has no registered backend receipt; original controller retained'
            if bootstrap is not None:
                until = time.monotonic()+15
                while not _original_process_exited(bootstrap['backend_process']) and time.monotonic()<until:
                    time.sleep(.05)
                assert _original_process_exited(bootstrap['backend_process']), 'Original backend still live; controller retained'
            until = time.monotonic()+5
            final = _fresh_original_launch(root, row)
            while final['state'] != 'recovery_required' and time.monotonic()<until:
                time.sleep(.05)
                final = _fresh_original_launch(root, row)
            assert final['state'] == 'recovery_required', 'Normal exit must retain durable unresolved ownership'
            assert final['nonce'] == owned_pointer['nonce'] and final['binding'] == row['binding']
            assert {name: (root/name).read_bytes() for name in pointers} == pointers
            assert {str(file.relative_to(root)): _file_sha256(file) for file in originals} == input_pins
            cleanup.update(original_main_exited=True, original_backend_exited=bootstrap is not None,
                           durable_state=final['state'], nonce=final['nonce'], whole_tree_exit_verified=False)
        _stop_original_controller(controller, original_controller, cleanup)
        cleanup['original_controller_reaped'] = True
        (tmp_path/'native-positive-cleanup.json').write_bytes(canonical(cleanup))
        if proof is not None:
            proof['cleanup'] = cleanup
            (tmp_path/'native-positive-proof.json').write_bytes(canonical(proof))

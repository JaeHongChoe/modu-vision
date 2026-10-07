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
        parent.symlink_to(foreign, target_is_directory=True)
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

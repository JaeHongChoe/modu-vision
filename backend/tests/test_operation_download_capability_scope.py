"""Original operation/SSH download code with explicit local command suppliers.

These counted file-transfer controls launch no process and contact no server.
They prove neither selected-worker lifetime nor real transfer performance.
"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import pytest


@pytest.mark.parametrize('tamper', [False, True], ids=['all33-exact', 'tampered-third-refused'])
def test_original_33_output_downloads_reuse_one_operation_capability(tmp_path, monkeypatch, tamper):
    from backend.remote import operations, ssh_transport
    from backend.remote.profiles import ComputeProfile

    remote = tmp_path / 'remote'
    remote.mkdir()
    profile = ComputeProfile(id='transport-fixture', name='Counted local command supplier',
        ssh_target='fixture-only', ssh_port=22, remote_root=str(remote),
        runtime_kind='docker', runtime_value='fixture-only-image', gpu_selector='2')
    context = operations.RemoteJobContext('job_transport_fixture', 'classification',
        tmp_path / 'local', tmp_path / 'dataset', profile, 'a' * 64, portable=True)
    extra = {'portable_models': True, 'models': [], 'image_sha256': 'b' * 64}
    spec = {'protocol_version': 1, 'operation': 'flowchart_run', 'job_id': context.job_id,
        'task': context.task, 'input_manifest_sha256': context.input_manifest_sha256, **extra}
    op_id = 'op_' + '1' * 32
    run = remote / 'runs' / op_id
    run.mkdir(parents=True)
    entries = []
    for i in range(33):
        relative = 'outputs/result-%02d.bin' % i
        body = ('local original output %02d\n' % i).encode()
        p = run / relative
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(body)
        entries.append({'path': relative, 'size': len(body),
            'sha256': hashlib.sha256(body).hexdigest()})
    manifest = {'protocol_version': 1, 'operation': 'flowchart_run', 'job_id': context.job_id,
        'input_manifest_sha256': context.input_manifest_sha256, 'model_refs': [],
        'selected_image_sha256': extra['image_sha256'], 'artifacts': entries}
    (run / 'status.json').write_text(json.dumps({'protocol_version': 1,
        'job_id': context.job_id, 'operation': 'flowchart_run', 'status': 'completed'}))
    (run / 'artifacts.json').write_text(json.dumps(manifest))
    journal_path = operations._operation_journal_path(context, 'flowchart_run', spec)
    journal_path.parent.mkdir(parents=True)
    local_run = journal_path.parent / op_id
    local_run.mkdir()
    (local_run / 'spec.json').write_text(json.dumps(spec))
    journal_path.write_text(json.dumps({'protocol_version': 1, 'operation': 'flowchart_run',
        'job_id': context.job_id, 'op_id': op_id, 'profile': profile.model_dump(),
        'spec': spec, 'state': 'completed', 'remote_handle': 'controlled-worker-handle'}))

    probes, transfers, SSH_commands = [], [], []
    original_which = ssh_transport.shutil.which
    monkeypatch.setattr(ssh_transport.shutil, 'which',
        lambda name: '/controlled/bin/rsync' if name == 'rsync' else original_which(name))
    monkeypatch.delenv('VISION_AI_STUDIO_SSH_CONTROL_PATH', raising=False)

    def command_supplier(argv, **kwargs):
        assert kwargs['shell'] is False
        if argv[0] == 'ssh':
            assert 'StrictHostKeyChecking=yes' in argv and 'ForwardAgent=no' in argv
            SSH_commands.append(tuple(argv))
            command = shlex.split(argv[-1])
            if command == ['rsync', '--version']:
                probes.append(profile.ssh_target)
                return subprocess.CompletedProcess(argv, 0, 'rsync version 3.2.7\n', '')
            assert len(command) == 2 and command[0] == 'cat'
            body = Path(command[1]).read_text()
            return subprocess.CompletedProcess(argv, 0, body, '')
        assert argv[0] == 'rsync' and '--times' in argv
        assert '--chmod=u=rw,go-rwx' in argv
        source, target = argv[-2:]
        assert source.startswith(profile.ssh_target + ':')
        path = Path(source.split(':', 1)[1])
        assert path.is_relative_to(run / 'outputs')
        Path(target).write_bytes(b'tampered actual bytes' if tamper and path.name == 'result-02.bin'
                                 else path.read_bytes())
        transfers.append(path.relative_to(run).as_posix())
        return subprocess.CompletedProcess(argv, 0, '', '')

    monkeypatch.setattr(ssh_transport.subprocess, 'run', command_supplier)
    transport = ssh_transport.SSHTransport()
    # Explicit remote-exit supplier. The real original confirmation function
    # still runs, but this does not establish any actual worker authority.
    monkeypatch.setattr(transport, 'is_running', lambda *args: False)
    if tamper:
        with pytest.raises(operations.ArtifactValidationError, match='hash mismatch'):
            operations._run_remote_operation_artifacts(context, 'flowchart_run', extra, transport=transport)
        assert transfers == [r['path'] for r in entries[:3]] and len(probes) == 1
        assert not (local_run / 'outputs').exists()
        assert not list(local_run.glob('.artifact-*'))
        return
    result = operations._run_remote_operation_artifacts(context, 'flowchart_run', extra, transport=transport)
    assert transfers == [r['path'] for r in entries]
    assert set(result) == {r['path'] for r in entries}
    for row in entries:
        assert hashlib.sha256(result[row['path']].read_bytes()).hexdigest() == row['sha256']
    archived = json.loads((context.output_dir / 'remote_operations' / op_id / 'operation_artifacts.json').read_bytes())
    assert archived['manifest'] == manifest
    assert json.loads(journal_path.read_bytes())['state'] == 'completed'
    assert len(probes) == 1, {'actual_capability_probes': len(probes),
        'original_output_transfers': len(transfers)}


def _profile():
    from backend.remote.profiles import ComputeProfile
    return ComputeProfile(id='scope-fixture', name='Local capability control',
        ssh_target='fixture-only', ssh_port=22, remote_root='/fixture-only',
        runtime_kind='docker', runtime_value='fixture-only-image')


@pytest.mark.parametrize('foreign', ['host', 'port', 'transport', 'profile-object'])
def test_positive_scope_never_lends_other_binding_or_next_operation(foreign, monkeypatch):
    from backend.remote import ssh_transport
    transport, profile = ssh_transport.SSHTransport(), _profile()
    probes = []
    monkeypatch.setattr(ssh_transport.shutil, 'which', lambda _: '/controlled/bin/rsync')
    def supplier(p, argv, **kwargs):
        assert argv == ['rsync', '--version'] and kwargs == {'timeout': 10}
        probes.append((p.ssh_target, p.ssh_port, id(p)))
        return subprocess.CompletedProcess(argv, 0, 'rsync version 3.2.7\n', '')
    monkeypatch.setattr(transport, 'exec', supplier)
    other, selected = transport, profile
    if foreign == 'host': selected = profile.model_copy(update={'ssh_target': 'other-fixture'})
    elif foreign == 'port': selected = profile.model_copy(update={'ssh_port': 2222})
    elif foreign == 'profile-object': selected = profile.model_copy()
    else:
        other = ssh_transport.SSHTransport()
        monkeypatch.setattr(other, 'exec', supplier)
    with transport.download_capability_scope(profile):
        assert transport.supports_resumable_transfer(profile)
        assert other.supports_resumable_transfer(selected)
        assert other.supports_resumable_transfer(selected)
        assert transport.supports_resumable_transfer(profile)
        assert len(probes) == 3
    assert transport.supports_resumable_transfer(profile)
    assert len(probes) == 4


@pytest.mark.parametrize('failure', ['negative', 'OSError', 'timeout', 'malformed'])
def test_failed_or_unrecognized_capability_is_not_memoized(failure, monkeypatch):
    from backend.remote import ssh_transport
    transport, profile, probes = ssh_transport.SSHTransport(), _profile(), []
    monkeypatch.setattr(ssh_transport.shutil, 'which', lambda _: '/controlled/bin/rsync')
    def supplier(p, argv, **kwargs):
        probes.append(p)
        if len(probes) == 1:
            if failure == 'OSError': raise OSError('controlled missing executable')
            if failure == 'timeout': raise subprocess.TimeoutExpired(argv, 10)
            return subprocess.CompletedProcess(argv, 1 if failure == 'negative' else 0,
                'unrecognized version' if failure == 'malformed' else '', '')
        return subprocess.CompletedProcess(argv, 0, 'rsync version 3.2.7\n', '')
    monkeypatch.setattr(transport, 'exec', supplier)
    with transport.download_capability_scope(profile):
        assert transport.supports_resumable_transfer(profile) is False
        assert transport.supports_resumable_transfer(profile) is True
        assert transport.supports_resumable_transfer(profile) is True
        assert len(probes) == 2


def test_operation_exception_always_retires_positive_scope(monkeypatch):
    from backend.remote import ssh_transport
    transport, profile, probes = ssh_transport.SSHTransport(), _profile(), []
    monkeypatch.setattr(ssh_transport.shutil, 'which', lambda _: '/controlled/bin/rsync')
    def supplier(p, argv, **kwargs):
        probes.append(p)
        return subprocess.CompletedProcess(argv, 0, 'rsync version 3.2.7\n', '')
    monkeypatch.setattr(transport, 'exec', supplier)
    with pytest.raises(RuntimeError, match='controlled operation refusal'):
        with transport.download_capability_scope(profile):
            assert transport.supports_resumable_transfer(profile)
            raise RuntimeError('controlled operation refusal')
    assert transport.supports_resumable_transfer(profile)
    assert len(probes) == 2


def test_copied_context_cannot_reuse_capability_after_operation_exit(monkeypatch):
    import contextvars
    from backend.remote import ssh_transport
    transport, profile, probes = ssh_transport.SSHTransport(), _profile(), []
    monkeypatch.setattr(ssh_transport.shutil, 'which', lambda _: '/controlled/bin/rsync')
    def supplier(p, argv, **kwargs):
        probes.append(p)
        return subprocess.CompletedProcess(argv, 0, 'rsync version 3.2.7\n', '')
    monkeypatch.setattr(transport, 'exec', supplier)
    with transport.download_capability_scope(profile):
        assert transport.supports_resumable_transfer(profile)
        retained = contextvars.copy_context()
        assert retained.run(transport.supports_resumable_transfer, profile)
        assert len(probes) == 1
    assert retained.run(transport.supports_resumable_transfer, profile)
    assert len(probes) == 2
    assert retained.run(transport.supports_resumable_transfer, profile)
    assert len(probes) == 3

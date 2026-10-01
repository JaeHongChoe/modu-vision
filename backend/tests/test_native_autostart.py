"""Native descriptors are durable, owned, and resolve the active release at startup."""
import json
import hashlib
import plistlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.engine.managed_service import ManagedService


def service(tmp_path,monkeypatch):
    value=ManagedService(tmp_path/'project') if (tmp_path/'project').exists() else None
    if value is None:(tmp_path/'project').mkdir();value=ManagedService(tmp_path/'project')
    monkeypatch.setattr(value.ledger,'active',lambda:{'release':{'package_path':'old-release','device':'cpu'}})
    return value


@pytest.mark.parametrize('host,kind,suffix',[('Darwin','launch_agent','.plist'),('Linux','systemd_user','.service'),('Windows','scheduled_task','.xml')])
def test_descriptor_has_no_release_or_token_and_only_bootstraps_project(tmp_path,monkeypatch,host,kind,suffix):
    import backend.engine.native_autostart as native
    item=service(tmp_path,monkeypatch)
    controller=native.NativeAutostart(item,system=host,home=tmp_path/'home')
    prepared=controller.prepare()
    assert prepared['kind']==kind and prepared['files'][0].endswith(suffix)
    content=Path(prepared['files'][0]).read_text()
    assert 'old-release' not in content and item.config['token'] not in content
    assert 'managed-service-project' in content or 'backend.engine.service_bootstrap' in content
    assert str(item.root.parent) in content
    assert not prepared['registered'] and not prepared['verified']


def test_launch_agent_install_is_durable_idempotent_and_owned(tmp_path,monkeypatch):
    import backend.engine.native_autostart as native
    item=service(tmp_path,monkeypatch);calls=[];registered=False
    def run(argv,**kwargs):
        nonlocal registered
        calls.append(argv)
        if argv[1]=='print':return SimpleNamespace(returncode=0 if registered else 113,stdout='state = running\npid = 12',stderr='')
        if argv[1]=='bootstrap':registered=True
        if argv[1]=='bootout':registered=False
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr(native.subprocess,'run',run)
    controller=native.NativeAutostart(item,system='Darwin',home=tmp_path/'home')
    result=controller.install();again=controller.install()
    assert result['registered'] and again['registered']
    durable=tmp_path/'home'/'Library'/'LaunchAgents'/(item.native_identity()+'.plist')
    assert durable.exists()
    assert len([row for row in calls if row[1]=='bootstrap'])==1
    controller.remove();controller.remove();assert not durable.exists()


def test_unowned_registration_file_is_never_overwritten(tmp_path,monkeypatch):
    import backend.engine.native_autostart as native
    item=service(tmp_path,monkeypatch);controller=native.NativeAutostart(item,system='Darwin',home=tmp_path/'home')
    target=controller.registration_path();target.parent.mkdir(parents=True);target.write_bytes(plistlib.dumps({'Label':item.native_identity(),'ProgramArguments':['/different/program']}))
    monkeypatch.setattr(native.subprocess,'run',lambda *a,**k:pytest.fail('must reject foreign file before OS command'))
    with pytest.raises(ValueError,match='owned'):controller.install()


def test_bootstrap_reads_current_approved_release_and_rechecks_policy(tmp_path,monkeypatch):
    from backend.engine import service_bootstrap as bootstrap
    project=tmp_path/'project';project.mkdir();item=ManagedService(project)
    package=tmp_path/'current-release';package.mkdir();(package/'manifest.json').write_bytes(b'current manifest')
    release={'package_path':str(package),'release_policy':'current-policy','device':'cpu','manifest_sha256':hashlib.sha256((package/'manifest.json').read_bytes()).hexdigest()}
    monkeypatch.setattr(bootstrap.ManagedService,'ledger',None,raising=False)
    monkeypatch.setattr(bootstrap,'ManagedService',lambda path:SimpleNamespace(root=item.root,config=item.config,ledger=SimpleNamespace(active=lambda:{'release':release}),input_arguments=lambda r:[],validate_accepted_device=lambda p,d:None))
    seen=[];monkeypatch.setattr(bootstrap,'verify_flow_package',lambda p:(None,[]))
    monkeypatch.setattr(bootstrap,'_verify_release_policy',lambda p,c,policy:seen.append(str(policy)))
    monkeypatch.setattr(bootstrap,'resolve_runtime_device',lambda d:d)
    args,env=bootstrap.bootstrap_command(project)
    assert str(package) in args and 'old-release' not in args
    assert seen==['current-policy'] and env['VISION_INSPECTION_TOKEN']==item.config['token']


def test_windows_removal_failure_keeps_registration_record_and_descriptor(tmp_path,monkeypatch):
    import backend.engine.native_autostart as native
    item=service(tmp_path,monkeypatch);controller=native.NativeAutostart(item,system='Windows',home=tmp_path/'home')
    prepared=controller.prepare();item.config.update(native_label=item.native_identity(),native_kind='scheduled_task');item.save(item.config)
    def run(argv,**kwargs):
        if '/Query' in argv:return SimpleNamespace(returncode=0,stdout='<Task xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"><Settings><Enabled>false</Enabled></Settings></Task>',stderr='')
        return SimpleNamespace(returncode=5 if '/Delete' in argv else 0,stdout='',stderr='access denied')
    monkeypatch.setattr(native.subprocess,'run',run)
    assert controller.query()['enabled'] is False
    with pytest.raises(RuntimeError,match='access denied'):controller.remove()
    assert Path(prepared['files'][0]).exists() and item.config['native_kind']=='scheduled_task'

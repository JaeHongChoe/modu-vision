"""SCM lifecycle simulator; Session0 and Windows registration require native evidence."""
import threading
import time
from pathlib import Path

import pytest


class SCM:
    def __init__(self):
        self.statuses = []
        self.handler = None
    def register(self, name, handler):
        self.handler = handler
        return 'owned-status-handle'
    def report(self, handle, status):
        assert handle == 'owned-status-handle'
        self.statuses.append(dict(status))


class Child:
    def __init__(self):
        self.exit = None
        self.killed = False
        self.closed = False
    def poll(self): return self.exit
    def terminate_owned(self):
        self.killed = True
        self.exit = 1
    def close(self): self.closed = True


def test_scm_starts_after_readiness_and_stops_only_after_owned_child_exit():
    from backend.engine.windows_inspection_service import ScmServiceHost
    scm = SCM()
    child = Child()
    ready = threading.Event()
    graceful = []
    host = ScmServiceHost('fixture', scm, lambda:child, lambda:ready.is_set(),
                          lambda:graceful.append(True) or setattr(child,'exit',0), poll_seconds=.001)
    thread = threading.Thread(target=host.service_main)
    thread.start()
    deadline = time.monotonic()+2
    while time.monotonic()<deadline and not scm.statuses: time.sleep(.001)
    assert not any(row['state']=='running' for row in scm.statuses)
    ready.set()
    while time.monotonic()<deadline and not any(row['state']=='running' for row in scm.statuses): time.sleep(.001)
    assert scm.handler(1) == 0
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert [row['state'] for row in scm.statuses] == ['start_pending','start_pending','running','stop_pending','stopped']
    assert scm.statuses[0]['controls_accepted']==0
    assert scm.statuses[2]['controls_accepted']==5
    assert graceful == [True]
    assert not child.killed
    assert child.closed


def test_readiness_timeout_is_error_and_kills_only_retained_child_handle():
    from backend.engine.windows_inspection_service import ScmServiceHost
    scm = SCM()
    child = Child()
    host = ScmServiceHost('fixture',scm,lambda:child,lambda:False,lambda:None,
                          startup_timeout=.01,stop_timeout=.01,poll_seconds=.001)
    host.service_main()
    assert child.killed and child.closed
    assert not any(row['state']=='running' for row in scm.statuses)
    assert scm.statuses[-1]['state']=='stopped'
    assert scm.statuses[-1]['win32_exit_code'] != 0


def test_control_handler_does_not_block_on_graceful_shutdown():
    from backend.engine.windows_inspection_service import ScmServiceHost
    scm = SCM()
    host = ScmServiceHost('fixture',scm,Child,lambda:True,lambda:time.sleep(1))
    started = time.monotonic()
    assert host.control_handler(5)==0
    assert time.monotonic()-started < .1
    assert host.stop_requested.is_set()
    assert host.control_handler(99)==120


def test_scm_registration_plan_is_separate_from_windows_logon_task(tmp_path):
    from backend.engine.windows_inspection_service import scm_launch_command, registration_preflight
    command = scm_launch_command(tmp_path,'fixture')
    assert command[1:3]==['-m','backend.engine.windows_inspection_service']
    assert '--service-name' in command
    state = registration_preflight(tmp_path,'fixture',{'service_account':'LocalSystem'},system='Darwin')
    assert state['kind']=='windows_scm'
    assert state['startup_scope']=='system_boot_session0'
    assert not state['registerable']
    assert not state['studio_requires_admin']
    assert {'elevation','service_account','programdata_acl','project_access','network_credentials','session0_gpu_camera_readiness'} <= set(state['checks'])
    assert not state['checks']['service_account']['passed']


def test_preflight_never_treats_user_claims_as_native_observation(tmp_path):
    from backend.engine.windows_inspection_service import registration_preflight
    state = registration_preflight(tmp_path,'fixture',{'service_account':'NT SERVICE\\fixture',
        'elevated':True,'programdata_acl':True,'session0_verified':True},system='Windows',observer=lambda *_args:{})
    assert not state['registerable']
    assert not state['native_verified']


def test_preflight_requires_boot_receipt_matching_manifest_and_account(tmp_path):
    from backend.engine.windows_inspection_service import registration_preflight
    observed = {'elevation':True,'service_account':True,'programdata_acl':True,'project_access':True,
                'network_credentials':True,'session0_gpu_camera_readiness':False}
    state = registration_preflight(tmp_path,'fixture',{'service_account':'NT SERVICE\\fixture'},system='Windows',observer=lambda *_args:observed)
    assert state['registration_prerequisites_passed']
    assert not state['native_verified']
    assert state['session0_acceptance']=='pending'


def test_shutdown_route_requires_auth_and_requests_owned_graceful_shutdown(package, tmp_path):
    from fastapi.testclient import TestClient
    from backend.engine.inspection_service import create_service_app
    calls=[]
    app=create_service_app(package,tmp_path/'state',token='secret',auto_worker=False,shutdown_callback=lambda:calls.append(True))
    with TestClient(app) as client:
        assert client.post('/v1/runtime/shutdown').status_code==401
        assert client.post('/v1/runtime/shutdown',headers={'X-Vision-Token':'secret'}).json()['status']=='stopping'
        assert calls==[True]


def test_scm_prepare_through_manager_preserves_personal_process_and_no_os_mutation(tmp_path,monkeypatch):
    from backend.engine.managed_service import ManagedService
    item=ManagedService(tmp_path)
    original=dict(item.config)
    monkeypatch.setattr(item.ledger,'active',lambda:{'release':{'manifest_sha256':'a'*64}})
    monkeypatch.setattr('subprocess.run',lambda *_args,**_kw:pytest.fail('Preparation on macOS must not mutate OS'))
    result=item.prepare_scm({'service_account':'NT SERVICE\\'+item.native_identity(),'network_required':False})
    assert result['status']=='prepared'
    assert not result['registerable']
    assert item.config['pid']==original['pid']
    assert item.config['token']==original['token']
    assert 'native_label' not in item.config
    assert 'windows_inspection_service' in Path(result['files'][0]).read_text()
    with pytest.raises(ValueError,match='prerequisites'):
        item.activate_scm()


def test_existing_scm_owner_routes_to_scm_and_foreign_binary_is_not_controlled(tmp_path,monkeypatch):
    from backend.engine.managed_service import ManagedService
    from backend.engine.native_autostart import NativeAutostart
    from backend.engine import windows_scm_registration as registration
    item=ManagedService(tmp_path)
    item.config.update(native_kind='windows_scm',native_label=item.native_identity(),scm_configuration={'service_account':'NT SERVICE\\'+item.native_identity()})
    controller=NativeAutostart(item,system='Windows')
    monkeypatch.setattr(registration,'query_windows_service',lambda _name:{'registered':True,'running':True,'binary_path':'foreign-program',
        'service_account':item.config['scm_configuration']['service_account']})
    monkeypatch.setattr('subprocess.run',lambda *_args,**_kw:pytest.fail('Foreign service must not be controlled'))
    assert controller.kind=='windows_scm'
    with pytest.raises(ValueError,match='owned'):
        controller.stop()


def test_readiness_exposes_worker_and_warmup_separately(package,tmp_path):
    from fastapi.testclient import TestClient
    from backend.engine.inspection_service import create_service_app
    app=create_service_app(package,tmp_path/'state',token='secret')
    with TestClient(app,headers={'X-Vision-Token':'secret'}) as client:
        ready=client.get('/v1/readiness').json()
        assert ready['status']=='ready'
        assert ready['worker_started']
        assert ready['gpu_warmup']=='not_requested'
        assert not ready['session0_verified']
        assert not ready['hardware_verified']


from backend.tests.test_inspection_service import package  # noqa: E402,F401

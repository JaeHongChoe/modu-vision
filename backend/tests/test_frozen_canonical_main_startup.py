"""Actual original entry imports, stopped before any server body or network bind."""
from pathlib import Path
import os,runpy,sys
import pytest

ROOT=Path(os.environ.get('FROZEN_CANONICAL_SOURCE_ROOT',str(Path(__file__).resolve().parents[2])))
ENTRY=Path(os.environ.get('FROZEN_CANONICAL_ENTRY',str(ROOT/'scripts/frozen_backend_entry.py')))


def test_original_frozen_fallback_uses_one_canonical_main_class_and_observer(monkeypatch):
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    class BeforeServer(BaseException):pass
    captured={};prior=sys.getprofile()
    def stop_before_original_body(frame,event,arg):
        if event=='call'and frame.f_code.co_name=='run_server'and Path(frame.f_code.co_filename).name=='main.py':
            captured.update(frame.f_globals);raise BeforeServer()
    monkeypatch.setattr(sys,'argv',['owned-entry-no-server'])
    try:
        sys.setprofile(stop_before_original_body)
        with pytest.raises(BeforeServer):runpy.run_path(str(ENTRY),run_name='__main__')
    finally:sys.setprofile(prior)
    assert captured['OwnedWorkAdmissionMiddleware']is main.OwnedWorkAdmissionMiddleware
    assert captured['__name__']=='backend.main'
    observer=OriginalRequestActivityObserver();admission=BackendWorkAdmission(activity_observer=observer)
    async def original_downstream(scope,receive,send):pass
    original=captured['OwnedWorkAdmissionMiddleware'](original_downstream,admission)
    assert observer._middleware is original and observer._failures==[]


def test_canonical_main_callable_preserves_original_managed_service_dispatch(monkeypatch):
    from backend import main
    from backend.engine import service_bootstrap
    seen=[];monkeypatch.setattr(service_bootstrap,'main',lambda p:seen.append(p))
    monkeypatch.setattr(main,'run_server',lambda:pytest.fail('Managed dispatch reached ordinary server'))
    monkeypatch.setattr(sys,'argv',['owned-main','--managed-service-project','owned-isolated-project'])
    assert callable(getattr(main,'main',None)), 'Canonical original startup callable is missing'
    assert main.main()is None and seen==['owned-isolated-project']


@pytest.mark.parametrize('extra',[[],['owned-one','owned-extra']])
def test_canonical_managed_invalid_argument_count_preserves_original_refusal(monkeypatch,extra):
    from backend import main
    monkeypatch.setattr(sys,'argv',['owned-main','--managed-service-project',*extra])
    assert callable(getattr(main,'main',None)), 'Canonical original startup callable is missing'
    with pytest.raises(SystemExit,match='--managed-service-project requires exactly one project directory'):main.main()


def test_canonical_main_callable_preserves_ordinary_arguments_and_return(monkeypatch):
    from backend import main
    seen=[];arguments=['owned-main','--port','0','--project-dir','owned-isolated-project']
    monkeypatch.setattr(sys,'argv',arguments[:]);monkeypatch.setattr(main,'run_server',lambda:seen.append(tuple(sys.argv)))
    assert callable(getattr(main,'main',None)), 'Canonical original startup callable is missing'
    assert main.main()is None and seen==[tuple(arguments)]and sys.argv==arguments


def test_actual_canonical_factory_health_keeps_original_observer_and_natural_settlement(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from backend import main
    from backend.engine.application_launch_handshake import BackendWorkAdmission
    from backend.engine.original_request_activity import OriginalRequestActivityObserver
    from fastapi.testclient import TestClient
    observer=OriginalRequestActivityObserver();admission=BackendWorkAdmission(activity_observer=observer)
    monkeypatch.setattr(main,'backend_work_admission',lambda:admission)
    monkeypatch.setattr(main,'get_device',lambda:'cpu')
    monkeypatch.setattr(main,'get_device_info',lambda _:SimpleNamespace(device_type='cpu',device_name='Owned no-forward CPU',is_accelerated=False,torch_version='controlled-no-forward'))
    app=main.create_app(str(tmp_path/'projects'),str(tmp_path/'own-auth'))
    client=TestClient(app)
    try:
        response=client.get('/health',headers={'x-vision-token':app.state.api_token})
        assert response.status_code==200
        value=response.json()['owned_request_diagnostic']
        assert value['records'][0]['route']=='HEALTH'and value['records'][0]['accepted']and value['pending_records']==1
        assert value['uncertain']is False and observer._middleware is not None
    finally:client.close()
    assert observer.snapshot()['pending_requests']==0 and admission.snapshot()=={'active_scopes':0,'unsupported':[]}


def test_exact_canonical_fallback_build_gate_and_unknown_callable_refusal():
    from scripts import build_backend_binary as build
    raw=ENTRY.read_bytes()
    assert build._launch_dispatch_present(raw)
    changed=raw.replace(b'    backend_main()\n',b'    backend_main(unknown_authority=True)\n')
    if changed!=raw:assert not build._launch_dispatch_present(changed)


@pytest.mark.parametrize('status',[0,23])
def test_original_early_windows_service_arguments_and_exit_are_preserved(monkeypatch,status):
    from types import ModuleType
    helper=ModuleType('backend.engine.windows_inspection_service');seen=[]
    def windows_main(arguments):seen.append(tuple(arguments));return status
    helper.main=windows_main
    monkeypatch.setitem(sys.modules,'backend.engine.windows_inspection_service',helper)
    arguments=['--state-dir','owned-isolated-state','--port','12345']
    monkeypatch.setattr(sys,'argv',['owned-frozen','--windows-inspection-service',*arguments])
    with pytest.raises(SystemExit)as result:runpy.run_path(str(ENTRY),run_name='__main__')
    assert result.value.code==status and seen==[tuple(arguments)]

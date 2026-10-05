"""A terminated non-child zombie is exited, not a live recoverable worker."""
from types import SimpleNamespace
import pytest
import psutil
from backend.engine.managed_service import ManagedService


def test_owned_worker_zombie_after_terminate_can_finish_stopping(tmp_path,monkeypatch):
    service=ManagedService(tmp_path);terminated=[]
    def wait(**kwargs):raise psutil.TimeoutExpired(kwargs['timeout'])
    process=SimpleNamespace(terminate=lambda:terminated.append(True),wait=wait,status=lambda:psutil.STATUS_ZOMBIE)
    monkeypatch.setattr(service,'owned_process',lambda:process)
    assert service.stop()['status']=='stopped'
    assert service.config['pid'] is None
    assert terminated==[]


def test_live_worker_timeout_does_not_acknowledge_stop_or_force_kill(tmp_path,monkeypatch):
    from backend.engine import managed_service
    service=ManagedService(tmp_path);service.config['pid']=123;service.save(service.config);terminated=[]
    process=SimpleNamespace(terminate=lambda:terminated.append(True),wait=lambda **kw: (_ for _ in ()).throw(psutil.TimeoutExpired(kw['timeout'])),status=lambda:psutil.STATUS_RUNNING)
    monkeypatch.setattr(service,'owned_process',lambda:process)
    monkeypatch.setattr(managed_service,'wait_for_owned_exit',lambda *_args,**_kwargs: (_ for _ in ()).throw(psutil.TimeoutExpired(12)),raising=False)
    with pytest.raises(RuntimeError,match='remain recoverable'):service.stop()
    assert service.config['pid']==123 and terminated==[True]

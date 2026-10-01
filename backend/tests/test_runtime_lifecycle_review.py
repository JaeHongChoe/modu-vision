"""Independent lifecycle ownership regressions; launch boundaries are test owned."""
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import pytest


def command(root):
    return ['python','-m','backend.engine.inspection_service','--state-dir',str(root/'state')]


@pytest.mark.parametrize('kind',['field','managed'])
def test_concurrent_runtime_start_claims_one_process_across_instances(tmp_path,monkeypatch,kind):
    from backend.engine.fleet_agent import FieldAgent
    from backend.engine.managed_service import ManagedService
    from backend.engine import fleet_agent,managed_service
    cls=FieldAgent if kind=='field' else ManagedService
    agents=[cls(tmp_path),cls(tmp_path)];root=agents[0].root
    digest='a'*64;package=root/'releases'/digest;package.mkdir();policy=package.with_suffix('.policy.json');policy.write_text('{}')
    entered=threading.Event();release=threading.Event();spawned=[];results=[];errors=[]
    def process(pid):
        return SimpleNamespace(pid=pid,create_time=lambda:42.,cmdline=lambda:command(root),poll=lambda:None)
    def launch(*args,**kwargs):
        spawned.append(10000);entered.set();assert release.wait(3);return process(10000)
    module=fleet_agent if kind=='field' else managed_service
    if kind=='managed':
        monkeypatch.setattr(module,'verify_flow_package',lambda path:(None,{}))
        monkeypatch.setattr(module,'_verify_release_policy',lambda *args,**kwargs:None)
    else:
        from backend.engine import flow_package_runtime,inspection_service
        monkeypatch.setattr(flow_package_runtime,'verify_flow_package',lambda path:(None,{}))
        monkeypatch.setattr(inspection_service,'_verify_release_policy',lambda *args,**kwargs:None)
    monkeypatch.setattr(module.subprocess,'Popen',launch);monkeypatch.setattr(module.psutil,'Process',process)
    class Client:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,*args,**kwargs):return SimpleNamespace(raise_for_status=lambda:None)
    for agent in agents:
        monkeypatch.setattr(agent,'client',lambda:Client())
        monkeypatch.setattr(agent,'runtime' if kind=='field' else 'readback',lambda:{'status':'ready','manifest_sha256':digest,'device':'cpu'})
        if kind=='managed':monkeypatch.setattr(agent,'validate_accepted_device',lambda *args,**kwargs:None)
    def start(agent):
        try:
            if kind=='field':results.append(agent.apply(digest,'cpu'))
            else:results.append(agent.start({'package_path':str(package),'release_policy':str(policy),'device':'cpu','manifest_sha256':digest}))
        except Exception as exc:errors.append(exc)
    first=threading.Thread(target=start,args=(agents[0],));first.start();assert entered.wait(3)
    second=threading.Thread(target=start,args=(agents[1],));second.start();second.join(1)
    release.set();first.join(3);second.join(3)
    assert spawned==[10000]
    assert results and all(isinstance(exc,ValueError) for exc in errors)
    path=agents[0].path if kind=='field' else agents[0].config_path
    owner=json.loads(path.read_text());assert owner['pid']==10000 and owner['process_created_at']==42.
    assert agents[1].owned() if kind=='field' else agents[1].owned_process()


@pytest.mark.parametrize('kind',['field','managed'])
@pytest.mark.parametrize('fault',['reused_pid','marker_only','wrong_project'])
def test_stop_rejects_unrelated_runtime_identity(tmp_path,monkeypatch,kind,fault):
    from backend.engine.fleet_agent import FieldAgent
    from backend.engine.managed_service import ManagedService
    from backend.engine import fleet_agent,managed_service
    agent=(FieldAgent if kind=='field' else ManagedService)(tmp_path);args=command(agent.root);terminated=[]
    if fault=='marker_only':args=['python','unrelated.py',*args[2:]]
    if fault=='wrong_project':args=command(tmp_path/'foreign')
    process=SimpleNamespace(create_time=lambda:42.,cmdline=lambda:args,terminate=lambda:terminated.append(True),wait=lambda **kwargs:None)
    agent.config.update(pid=10000,process_created_at=41. if fault=='reused_pid' else 42.,
        process_command_sha256=hashlib.sha256(json.dumps(args).encode()).hexdigest());agent.save(agent.config)
    monkeypatch.setattr((fleet_agent if kind=='field' else managed_service).psutil,'Process',lambda pid:process)
    assert agent.stop()['status']=='stopped' and terminated==[]


def test_runtime_state_claim_is_reentrant_and_excludes_a_separate_process(tmp_path):
    from backend.engine.runtime_process_control import runtime_state_lock
    script='from backend.engine.runtime_process_control import runtime_state_lock;import sys\ntry:\n with runtime_state_lock(sys.argv[1]): print("CLAIMED")\nexcept ValueError: print("BUSY")'
    with runtime_state_lock(tmp_path):
        with runtime_state_lock(tmp_path):
            child=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=10)
            assert child.returncode==0 and child.stdout.strip()=='BUSY',child.stderr
    child=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=10)
    assert child.returncode==0 and child.stdout.strip()=='CLAIMED',child.stderr

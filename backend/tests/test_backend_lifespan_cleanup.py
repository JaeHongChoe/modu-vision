"""Cleanup remains reachable on startup failure and an early supervisor exit."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import pytest
from fastapi import FastAPI

def lifecycle(monkeypatch, fail=None):
    from backend import main
    from backend.remote import coordinator
    from backend.engine import local_training_worker, worker_preflight
    from backend.api import routes_dataset_imports, routes_workers
    calls = []
    def step(name):
        calls.append(name)
        if name == fail:
            raise RuntimeError(name)
    class Broadcaster:
        def start(self, loop): step('start')
        async def shutdown(self): step('broadcast')
    monkeypatch.setattr(main, 'broadcaster', Broadcaster())
    monkeypatch.setattr(coordinator, 'recover_remote_jobs', lambda _: step('recover'))
    monkeypatch.setattr(local_training_worker, 'recover_local_jobs', lambda _: step('local'))
    monkeypatch.setattr(routes_dataset_imports, 'recover_imports_at_startup', lambda _: step('imports'))
    monkeypatch.setattr(worker_preflight, 'sweep_stale_runs', lambda: None)
    monkeypatch.setattr(main.training_job_manager, 'detach_all_for_shutdown', lambda: step('detach'))
    monkeypatch.setattr(routes_workers, 'stop_for_shutdown', lambda: step('preflight'))
    monkeypatch.setattr(main, 'clear_device_cache', lambda: step('cache'))
    return main, calls

def test_lifespan_generator_close_after_startup_runs_owned_cleanup(monkeypatch):
    main, calls = lifecycle(monkeypatch)
    async def run():
        cm = main._admitted_lifespan(FastAPI())
        await cm.__aenter__()
        # Uvicorn skips its normal shutdown when should_exit was set during
        # startup. Event-loop finalization closes the suspended lifespan.
        await cm.gen.aclose()
    asyncio.run(run())
    assert calls == ['start', 'recover', 'local', 'imports', 'detach', 'preflight', 'broadcast', 'cache']

@pytest.mark.parametrize('failed', ['start', 'recover', 'local', 'imports'])
def test_partial_startup_failure_still_cleans_resources(monkeypatch, failed):
    main, calls = lifecycle(monkeypatch, failed)
    async def run():
        async with main._admitted_lifespan(FastAPI()):
            pytest.fail('failed startup must not admit requests')
    with pytest.raises(RuntimeError, match=failed): asyncio.run(run())
    assert calls[-4:] == ['detach', 'preflight', 'broadcast', 'cache']

@pytest.mark.parametrize('failed', ['detach', 'preflight', 'broadcast'])
def test_one_shutdown_failure_does_not_skip_the_other_owned_resources(monkeypatch, failed):
    main, calls = lifecycle(monkeypatch, failed)
    async def run():
        async with main._admitted_lifespan(FastAPI()): pass
    with pytest.raises(RuntimeError, match=failed): asyncio.run(run())
    assert calls[-4:] == ['detach', 'preflight', 'broadcast', 'cache']

def test_actual_uvicorn_early_stdin_eof_finalizes_recovered_startup(tmp_path):
    root = Path(__file__).resolve().parents[2]
    marker = tmp_path / 'startup-events.json'
    # Real server, lifespan, stdin reader and asyncio finalization; only model
    # recovery and cleanup targets are recording controls (no training/GPU).
    code = '''
import json,time,sys,threading
from pathlib import Path
from backend import main
from backend.remote import coordinator
from backend.engine import local_training_worker,worker_preflight
from backend.api import routes_dataset_imports,routes_workers
marker=Path(sys.argv[1]);events=[];lock=threading.Lock();detached=threading.Event()
def record(name):
 with lock:
  events.append(name);marker.write_text(json.dumps(events),encoding='utf-8')
def detach():record('detach');detached.set()
def recover(_):
 record('recover');assert detached.wait(10),'supervisor EOF was not received';record('recovered')
class Broadcaster:
 def start(self,loop):record('start')
 async def shutdown(self):record('broadcast')
main.broadcaster=Broadcaster();coordinator.recover_remote_jobs=recover
local_training_worker.recover_local_jobs=lambda _:None
routes_dataset_imports.recover_imports_at_startup=lambda _:None
worker_preflight.sweep_stale_runs=lambda:None
main.training_job_manager.detach_all_for_shutdown=detach
routes_workers.stop_for_shutdown=lambda:record('preflight')
main.clear_device_cache=lambda:record('cache')
sys.argv=['backend','--host','127.0.0.1','--port','0','--project-dir',str(marker.parent/'projects')]
main.run_server()
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith(('VISION_AI_STUDIO_', 'MODU_'))}
    env.update(PYTHONPATH=str(root), PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8',
               VISION_AI_STUDIO_USER_DATA_DIR=str(tmp_path / 'userData'),
               VISION_AI_STUDIO_STOP_ON_STDIN_EOF='1', VISION_AI_STUDIO_API_TOKEN='b' * 64)
    child = subprocess.Popen([sys.executable, '-c', code, str(marker)], cwd=root, env=env,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        child.stdin.close();child.stdin=None
        out, err = child.communicate(timeout=40)
        (tmp_path/'server.stdout').write_bytes(out);(tmp_path/'server.stderr').write_bytes(err)
        assert child.returncode == 0, err.decode('utf-8', 'replace')
        events = json.loads(marker.read_text(encoding='utf-8'))
        assert events.count('detach') == 2 and events[-3:] == ['preflight', 'broadcast', 'cache'], events
        assert events.index('recovered') < events.index('preflight')
        port = int(next(line.split('=',1)[1] for line in out.decode().splitlines() if line.startswith('VISION_AI_STUDIO_PORT=')))
        with socket.socket() as probe:
            probe.settimeout(.2);assert probe.connect_ex(('127.0.0.1',port)) != 0
    finally:
        if child.poll() is None:child.kill();child.wait(10)

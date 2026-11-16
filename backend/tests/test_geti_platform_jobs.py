"""Durable ledger tests: wrong scope/retries must never publish another job."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import pytest


def modules():
    from backend.platforms.geti.bindings import PlatformStore
    return PlatformStore


def test_operation_reservation_is_atomic_durable_and_actor_scoped(tmp_path):
    Store = modules()
    store = Store(tmp_path)
    def reserve(_):
        return store.reserve_operation('project-a', 'actor-a', 'request-1', 'train', {'epochs': 1})
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(reserve, range(16)))
    assert sum(row['is_new'] for row in rows) == 1
    assert Store(tmp_path).reserve_operation('project-a', 'actor-a', 'request-1', 'train', {'epochs': 1})['is_new'] is False
    with pytest.raises(ValueError, match='different'):
        store.reserve_operation('project-a', 'actor-a', 'request-1', 'train', {'epochs': 2})
    with pytest.raises(ValueError, match='actor'):
        store.reserve_operation('project-a', 'actor-b', 'request-1', 'train', {'epochs': 1})
    assert store.reserve_operation('project-b', 'actor-b', 'request-1', 'train', {'epochs': 1})['is_new'] is True


def test_bindings_and_terminal_job_survive_reopen_and_fence_scope(tmp_path):
    Store = modules()
    store = Store(tmp_path)
    store.save_project('a', {'geti_project_id': 'upstream-a', 'workspace_id': 'workspace'})
    store.save_dataset('a', {'id': 'receipt-a', 'source_root': '/server/private'})
    store.save_job('a', 'actor', {'id': 'job-a', 'status': 'done', 'model_id': 'model-a', 'worker_exited': False})
    reopened = Store(tmp_path)
    assert reopened.get_project('a')['geti_project_id'] == 'upstream-a'
    assert reopened.get_dataset('a')['id'] == 'receipt-a'
    assert reopened.get_job('b', 'job-a') is None
    assert reopened.get_job('a', 'job-a')['status'] == 'done'
    assert reopened.get_job('a', 'job-a')['worker_exited'] is False


def test_child_correlation_reserves_before_publication_and_never_retries_ambiguity(tmp_path):
    path = Path(__file__).resolve().parents[2] / 'platforms/geti/runtime/correlation.py'
    assert path.is_file(), 'child durable correlation overlay is missing'
    spec = importlib.util.spec_from_file_location('modu_correlation', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ledger = module.CorrelationStore(tmp_path / 'child.sqlite3')
    row = ledger.reserve('op-a', {'project_id': 'p', 'epochs': 1})
    assert row['is_new'] is True
    assert row['status'] == 'submission_unconfirmed'
    reopened = module.CorrelationStore(tmp_path / 'child.sqlite3')
    retry = reopened.reserve('op-a', {'project_id': 'p', 'epochs': 1})
    assert retry['is_new'] is False
    assert retry['job_id'] == row['job_id']
    with pytest.raises(ValueError, match='different'):
        reopened.reserve('op-a', {'project_id': 'p', 'epochs': 2})
    reopened.save('op-a', {'job_id': row['job_id'], 'status': 'DONE', 'progress': 100})
    assert module.CorrelationStore(tmp_path / 'child.sqlite3').get('op-a')['status'] == 'DONE'


def test_real_child_queue_deduplicates_response_loss_and_memory_restart(tmp_path):
    """No trainer is run: use the real migrated app services and real JobQueue."""
    import os
    import subprocess
    python = os.environ.get('MODU_GETI_QA_PYTHON')
    weight = os.environ.get('MODU_GETI_QA_WEIGHTS')
    if not python or not weight:
        pytest.skip('explicit owned Python 3.14 and audited weight cache required')
    root = Path(__file__).resolve().parents[2]
    script = '''import json,os,shutil
from pathlib import Path
from fastapi.testclient import TestClient
import server
from app.db import MigrationManager
from app.settings import get_settings
from app.core.jobs.control_plane import JobQueue
from app.services.event.event_bus import EventBus
import multiprocessing as mp
settings=get_settings(); settings.ensure_dirs_exist()
assert MigrationManager(settings).initialize_database()
server.app.state.settings=settings
server.app.state.job_queue=JobQueue()
ctx=mp.get_context('spawn')
server.app.state.event_bus=EventBus(source_changed_condition=ctx.Condition(),model_reload_event=ctx.Event(),inference_params_event=ctx.Event())
weights=Path(os.environ['DATA_DIR'])/'pretrained_weights/classification'
weights.mkdir(parents=True,exist_ok=True)
shutil.copy2(os.environ['MODU_GETI_QA_WEIGHTS'],weights/'efficientnet_b0-0752-0e386130.pth.zip')
client=TestClient(server.app)
headers={'X-Modu-Geti-Secret':os.environ['MODU_GETI_SECRET']}
project=client.post('/api/projects',headers=headers,json={'name':'owned durable queue fixture','task':{'task_type':'classification','exclusive_labels':True,'labels':[{'name':'a'},{'name':'b'}]}})
assert project.status_code==201, project.text
body={'operation_id':'owned-once','request':{'job_type':'train','project_id':project.json()['id'],'parameters':{'device':'cpu','model_architecture_id':'image-classification-efficientnet-b0'}},'max_epochs':1}
first=client.post('/modu/jobs',headers=headers,json=body)
assert first.status_code==200, first.text
job_id=first.json()['job_id']
again=client.post('/modu/jobs',headers=headers,json=body)
assert again.status_code==200 and again.json()['job_id']==job_id, again.text
assert len(server.app.state.job_queue.list_all())==1
body['max_epochs']=2
assert client.post('/modu/jobs',headers=headers,json=body).status_code==409
body['max_epochs']=1
cancel=client.post('/modu/jobs/owned-once/cancel',headers=headers,json={})
assert cancel.status_code==200 and cancel.json()['status']=='CANCELLED',cancel.text
assert cancel.json()['worker_exited'] is False
# Re-open the durable ledger and discard every upstream in-memory job.
server.ledger=server.CorrelationStore(Path(os.environ['DATA_DIR'])/'modu-submissions.sqlite3')
server.app.state.job_queue=JobQueue()
again=client.post('/modu/jobs',headers=headers,json=body)
assert again.status_code==200 and again.json()['job_id']==job_id,again.text
assert again.json()['status']=='CANCELLED'
assert not server.app.state.job_queue.list_all()
# An unmatched reservation after a crash stays ambiguous, never re-enqueued.
server.ledger.reserve('crashed',server.SubmitBody.model_validate(body).model_dump(mode='json',exclude={'operation_id'}))
body['operation_id']='crashed'
ambiguous=client.post('/modu/jobs',headers=headers,json=body)
assert ambiguous.status_code==200 and ambiguous.json()['status']=='submission_unconfirmed',ambiguous.text
assert not server.app.state.job_queue.list_all()
# A new explicitly requested operation remains possible; the ambiguous old
# operation is retained and is never republished into the empty queue.
body['operation_id']='independent-new-request'
new=client.post('/modu/jobs',headers=headers,json=body)
assert new.status_code==200 and new.json()['job_id']!=job_id,new.text
assert len(server.app.state.job_queue.list_all())==1
assert server.ledger.get('crashed')['status']=='submission_unconfirmed'
print(json.dumps({'deduplicated_job':job_id,'queue_after_memory_restart':0,'ambiguous_republished':False,'cancel_proves_worker_exit':False}))
'''
    env = {key: value for key, value in os.environ.items() if key in {'PATH', 'TMPDIR', 'SYSTEMROOT'}}
    env.update({'MODU_GETI_SOURCE_ROOT': str(root / 'platforms/geti/upstream'), 'MODU_GETI_SECRET': 'owned-queue-test',
                'DATA_DIR': str(tmp_path / 'data'), 'LOG_DIR': str(tmp_path / 'logs'), 'HOME': str(tmp_path / 'home'),
                'PYTHONPATH': str(root / 'platforms/geti/runtime'), 'MODU_GETI_QA_WEIGHTS': weight,
                'CUDA_VISIBLE_DEVICES': '', 'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1'})
    result = subprocess.run([python, '-c', script], cwd=root / 'platforms/geti/upstream/application/backend', env=env,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-5000:]


def test_cancel_intent_survives_running_readback_without_implying_worker_exit(tmp_path):
    from backend.platforms.geti.bindings import PlatformStore
    from backend.api.routes_platform_geti import reconcile
    store = PlatformStore(tmp_path)
    job = {'id':'owned-job','operation_id':'child-op','status':'running','cancel_requested':True,'worker_exited':False}
    store.save_job('owned-project','actor',job)
    class Child:
        def request(self, method, path):
            return {'job_id':'upstream-job','status':'RUNNING','progress':30,'worker_exited':False}
    row = reconcile(Child(),store,'owned-project','actor',job)
    assert row['cancel_requested'] is True
    assert row['status'] == 'running' and row['worker_exited'] is False
    assert store.get_job('owned-project','owned-job')['cancel_requested'] is True


def test_proven_terminal_exit_cannot_be_downgraded_by_new_child_memory(tmp_path):
    from backend.platforms.geti.bindings import PlatformStore
    from backend.api.routes_platform_geti import reconcile
    store = PlatformStore(tmp_path)
    job = {'id':'owned-job','operation_id':'child-op','status':'cancelled','cancel_requested':True,'worker_exited':True,
           'generation':'old-generation','exit_evidence':{'generation':'old-generation','worker_exited':True}}
    store.save_job('owned-project','actor',job)
    class Child:
        def request(self, method, path): return {'job_id':'upstream-job','status':'RUNNING','progress':30,'worker_exited':False}
    row = reconcile(Child(),store,'owned-project','actor',job)
    assert row['status']=='cancelled' and row['worker_exited'] is True
    assert row['exit_evidence']['generation']=='old-generation'


def test_concurrent_stale_snapshot_cannot_erase_durable_cancel_or_worker_exit(tmp_path):
    from backend.platforms.geti.bindings import PlatformStore
    store = PlatformStore(tmp_path)
    store.save_job('scope','actor',{'id':'job','status':'done','progress':100,'worker_exited':True,'cancel_requested':True,'model_id':'model'})
    result = store.save_job('scope','actor',{'id':'job','status':'running','progress':20,'worker_exited':False,'cancel_requested':False})
    assert result['worker_exited'] is True and result['cancel_requested'] is True
    assert result['status']=='done' and result['progress']==100 and result['model_id']=='model'
    assert store.get_job('scope','job') == result

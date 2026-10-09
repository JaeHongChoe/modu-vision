"""Separate-process durable inbox/result/outbox boundary, not daemon acceptance.

Regression break: recover() fails to requeue a committed 'sending' outbox, loses
its original job/input/binding/result/deadline, or creates another result on
retransmission. Real owned child exits without Python finalizers after commits.
No fit/inference/network/OS registration or physical power-loss claim.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

import pytest
from PIL import Image

from backend.engine import inspection_service as service
from backend.engine.runtime_deadline import execute_owned_process


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_owned_abrupt_process_exit_preserves_original_inbox_result_and_outbox(tmp_path, monkeypatch):
    root = tmp_path / 'owned-process-storage'
    root.mkdir()
    (root / 'inbox').mkdir()
    image = root / 'inbox' / 'part.png'
    Image.new('RGB', (8, 8), (21, 42, 63)).save(image)
    image_hash = sha(image)
    source_root = Path(service.__file__).absolute().parents[2]
    original_sources = ['backend/engine/inspection_service.py', 'backend/engine/runtime_deadline.py',
        'backend/engine/process_isolation.py', 'backend/engine/sqlite_wal.py', 'backend/engine/sqlite_schema.py']
    source_pins = {rel: {'sha256': sha(source_root / rel), 'size': (source_root / rel).stat().st_size} for rel in original_sources}
    epoch = uuid.uuid4().hex
    # Deliberately controlled storage identity; no valid model/package or human
    # approval is invented or sent to _inspect_job.
    binding = {'device': 'cpu', 'recipe_id': 'controlled-storage-A', 'recipe_revision': 'A-original',
        'fixture_input_epoch': epoch, 'manifest_sha256': hashlib.sha256(b'controlled-storage-recipe-A').hexdigest()}
    result = {'final_verdict': 'NG', 'roi_count': 0, 'crops': [], 'execution_steps': [],
        'fixture_scope': 'controlled_storage_result_no_inference', 'fixture_input_epoch': epoch}
    payload = {'schema': 'modu-vision.owned-inspection-store-interruption/v1', 'owned_root': str(root),
        'source_root': str(source_root), 'source_pins': source_pins, 'image_sha256': image_hash,
        'image_id': 'owned-storage-part', 'input_key': 'owned-storage-' + epoch, 'fixture_epoch': epoch,
        'binding': binding, 'controlled_result': result}
    request = root / 'request.json'
    request.write_text(json.dumps(payload), encoding='utf-8')
    helper = Path(__file__).absolute().with_name('inspection_abrupt_exit_fixture.py')
    helper_hash = sha(helper)
    command = [sys.executable, '-I', '-B', str(helper), '--request', str(request)]
    env = {key: os.environ[key] for key in ['PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT'] if key in os.environ}
    env.update({'HOME': str(root / 'home'), 'USERPROFILE': str(root / 'home'), 'TMPDIR': str(root / 'tmp'),
        'TMP': str(root / 'tmp'), 'TEMP': str(root / 'tmp'), 'XDG_CACHE_HOME': str(root / 'cache'),
        'HF_HOME': str(root / 'cache/hf'), 'TORCH_HOME': str(root / 'cache/torch'),
        'MPLCONFIGDIR': str(root / 'cache/matplotlib'), 'NUMBA_CACHE_DIR': str(root / 'cache/numba'),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1',
        'CUDA_VISIBLE_DEVICES': '', 'NVIDIA_VISIBLE_DEVICES': 'none', 'OMP_NUM_THREADS': '1',
        'MKL_NUM_THREADS': '1', 'OPENBLAS_NUM_THREADS': '1', 'NUMEXPR_NUM_THREADS': '1',
        'VECLIB_MAXIMUM_THREADS': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'})
    for name in ['home', 'tmp', 'cache/hf', 'cache/torch', 'cache/matplotlib', 'cache/numba']:
        (root / name).mkdir(parents=True, exist_ok=True)
    # Original production ownership and uncertainty rules stay active. A timeout,
    # cancelled/uncertain group or any other code is failure; no retry/fallback.
    outcome = execute_owned_process(command, deadline_ms=10_000, env=env, cwd=root)
    assert outcome['status'] == 'completed', outcome
    assert outcome['returncode'] == 73, outcome
    assert not outcome.get('private_diagnostics') and not outcome.get('ownership', {}).get('unknown_members')
    witness = json.loads((root / 'before-abrupt-exit.json').read_text())
    assert witness['process']['pid'] == outcome['pid']
    assert witness['process']['command'] == command
    assert witness['process']['parent_pid'] == os.getpid()
    assert witness['process']['birth'] > 0
    if os.name == 'posix':
        assert witness['process']['pgid'] == witness['process']['sid'] == outcome['pid']
    assert witness['abrupt_exit_code'] == 73 and witness['local_ack_committed'] is False
    assert witness['daemon_started'] is witness['inference_executed'] is witness['receiver_executed'] is False
    original = witness['original_job_raw']
    job = witness['job_id']
    assert original['job_id'] == job == witness['delivery_raw']['job_id'] == witness['inbox_raw']['job_id']
    assert original['state'] == 'delivery_pending' and original['verdict'] == 'REVIEW' and original['model_verdict'] == 'NG'
    assert witness['delivery_raw']['state'] == 'sending' and witness['delivery_raw']['attempts'] == 1
    assert original['image_id'] == 'owned-storage-part' and original['image_sha256'] == image_hash
    assert original['idempotency_key'] == payload['input_key'] and original['binding_provenance'] == 'admission_snapshot'
    assert json.loads(original['runtime_binding_json']) == binding
    assert hashlib.sha256(original['runtime_binding_json'].encode()).hexdigest() == original['runtime_binding_sha256']
    assert json.loads(original['result_json']) == result
    assert hashlib.sha256(original['result_json'].encode()).hexdigest() == witness['original_result_sha256']
    # No parent store is opened until the admitted production helper reconciles
    # the original child exit. Changing current recipe cannot relabel the input.
    current = {**binding, 'recipe_id': 'controlled-storage-B', 'recipe_revision': 'B-current', 'fixture_input_epoch': 'new-current-epoch'}
    store = service.InspectionStore(root / 'state', max_attempts=3, runtime_provider=lambda: current)
    with store._connection() as conn:
        before_recovery = dict(conn.execute('SELECT * FROM jobs WHERE job_id=?', (job,)).fetchone())
    assert before_recovery == original, 'All original committed fields survived the actual abrupt exit'
    store.recover()
    stable_keys = ['job_id', 'image_path', 'image_id', 'image_sha256', 'source', 'idempotency_key', 'payload_sha256',
        'runtime_binding_json', 'runtime_binding_sha256', 'binding_provenance', 'result_json', 'model_verdict', 'deadline_at', 'attempts']
    with store._connection() as conn:
        resumed = dict(conn.execute('SELECT * FROM jobs WHERE job_id=?', (job,)).fetchone())
        outbox = dict(conn.execute('SELECT * FROM deliveries WHERE job_id=?', (job,)).fetchone())
    assert {key: resumed[key] for key in stable_keys} == {key: original[key] for key in stable_keys}
    assert outbox['state'] == 'pending' and outbox['attempts'] == 1
    assert store.enqueue(image, 'inbox', image_id=payload['image_id'], idempotency_key=payload['input_key']) == job
    assert store.claim() is None, 'The committed original result is not run again'
    redelivery = store.claim_delivery()
    assert redelivery is not None and redelivery['job_id'] == job
    assert redelivery['result_json'] == original['result_json'] and redelivery['runtime_binding_json'] == original['runtime_binding_json']
    transported = []
    def local_destination(url, *, json, headers, timeout):
        assert url == 'http://owned-fixture.invalid/result' and timeout == 5 and headers == {}
        transported.append(json)
        return SimpleNamespace(status_code=200)
    monkeypatch.setattr(service.httpx, 'post', local_destination)
    service._deliver_job(store, redelivery, 'http://owned-fixture.invalid/result', None)
    assert transported == [{'job_id': job, 'model_verdict': 'NG', 'image_sha256': image_hash,
        'result': result, 'runtime_identity': binding}]
    terminal = store.get(job)
    assert terminal['state'] == 'completed' and terminal['verdict'] == terminal['model_verdict'] == 'NG'
    assert terminal['runtime_binding'] == binding and terminal['result'] == result
    assert terminal['deadline_at'] == original['deadline_at'] and terminal['error'] is None
    assert store.enqueue(image, 'inbox', image_id=payload['image_id'], idempotency_key=payload['input_key']) == job
    store.recover()
    assert store.claim_delivery() is None and store.claim() is None
    assert len(store.list()) == 1
    assert [event['state'] for event in store.events(job)] == ['queued', 'running', 'delivery_pending', 'delivery_pending', 'completed']
    with store._connection() as conn:
        assert conn.execute('SELECT COUNT(*) FROM inbox_items').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM deliveries').fetchone()[0] == 1
        delivery = dict(conn.execute('SELECT * FROM deliveries WHERE job_id=?', (job,)).fetchone())
    assert delivery['state'] == 'sent' and delivery['attempts'] == 2
    assert sha(image) == image_hash and sha(helper) == helper_hash
    for rel, expected in source_pins.items():
        assert sha(source_root / rel) == expected['sha256']
        assert (source_root / rel).stat().st_size == expected['size']
    (root / 'owned-storage-recovery-proof.json').write_text(json.dumps({'scope': 'actual_owned_child_store_durability_only',
        'original_exit': outcome, 'before_abrupt_exit': witness, 'terminal': terminal, 'redelivery': transported,
        'source_pins': source_pins, 'helper_sha256': helper_hash, 'image_sha256': image_hash,
        'daemon_executed': False, 'model_inference_executed': False, 'network_receiver_executed': False,
        'physical_power_loss_verified': False, 'hardware_or_installed_release_approved': False,
        'quality_approved': False, 'parent_accepted': False}, indent=2), encoding='utf-8')

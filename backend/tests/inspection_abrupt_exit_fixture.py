"""Owned test utility: commit one controlled storage result, then exit abruptly.
Never starts an HTTP daemon, model, receiver, subprocess, GPU or training job.
The fixture witness is not a real receiver or model-quality receipt.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys


def _identity(info):
    # Reads may update atime; protect content and named inode identity instead.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def regular(path):
    path = Path(path)
    for ancestor in reversed(path.parents):
        assert stat.S_ISDIR(ancestor.lstat().st_mode), 'No aliased test ancestor'
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode) and info.st_nlink == 1
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        assert _identity(opened) == _identity(info)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read()
        after = os.fstat(fd)
        assert _identity(after) == _identity(info)
    finally:
        os.close(fd)
    assert _identity(path.lstat()) == _identity(info)
    return data


def publish(path, value):
    data = (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb', closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    request_path = Path(args.request).absolute()
    request = json.loads(regular(request_path))
    assert request['schema'] == 'modu-vision.owned-inspection-store-interruption/v1'
    root = request_path.parent
    assert str(root) == request['owned_root']
    assert set(request) == {'schema', 'owned_root', 'source_root', 'source_pins', 'image_sha256', 'image_id', 'input_key', 'fixture_epoch', 'binding', 'controlled_result'}
    assert not (root / 'state').exists()
    assert not (root / 'before-abrupt-exit.json').exists()
    source_root = Path(request['source_root'])
    assert source_root.is_absolute() and source_root != root and source_root not in root.parents
    for rel, expected in request['source_pins'].items():
        assert not Path(rel).is_absolute() and '..' not in Path(rel).parts
        data = regular(source_root / rel)
        assert len(data) == expected['size'] and hashlib.sha256(data).hexdigest() == expected['sha256']
    image = root / 'inbox' / 'part.png'
    image_bytes = regular(image)
    assert hashlib.sha256(image_bytes).hexdigest() == request['image_sha256']
    assert request['binding']['device'] == 'cpu'
    assert request['binding']['fixture_input_epoch'] == request['fixture_epoch']
    assert request['controlled_result']['fixture_scope'] == 'controlled_storage_result_no_inference'
    assert request['controlled_result']['fixture_input_epoch'] == request['fixture_epoch']
    sys.path.insert(0, str(source_root))
    import psutil
    from backend.engine.inspection_service import InspectionStore
    me = psutil.Process()
    store = InspectionStore(root / 'state', max_attempts=3, runtime_provider=lambda: request['binding'])
    job = store.enqueue(image, 'inbox', image_id=request['image_id'], idempotency_key=request['input_key'])
    assert store.claim()['job_id'] == job
    store.finish(job, result=request['controlled_result'], require_delivery=True)
    assert store.claim_delivery()['job_id'] == job
    # The production calls above each returned from their SQLite transaction.
    # Do not run finish_delivery or any daemon/HTTP/model here.
    with store._connection() as conn:
        original_raw = dict(conn.execute('SELECT * FROM jobs WHERE job_id=?', (job,)).fetchone())
        delivery_raw = dict(conn.execute('SELECT * FROM deliveries WHERE job_id=?', (job,)).fetchone())
        inbox_raw = dict(conn.execute('SELECT * FROM inbox_items WHERE job_id=?', (job,)).fetchone())
    assert original_raw['state'] == 'delivery_pending' and delivery_raw['state'] == 'sending'
    binding_data = original_raw['runtime_binding_json'].encode()
    assert hashlib.sha256(binding_data).hexdigest() == original_raw['runtime_binding_sha256']
    process = {'pid': me.pid, 'birth': me.create_time(), 'command': me.cmdline(), 'parent_pid': me.ppid()}
    if os.name == 'posix':
        process.update(pgid=os.getpgid(0), sid=os.getsid(0))
    witness = {'schema': 'modu-vision.owned-inspection-store-before-abrupt-exit/v1', 'process': process,
        'job_id': job, 'original_job_raw': original_raw, 'delivery_raw': delivery_raw, 'inbox_raw': inbox_raw,
        'image_sha256': request['image_sha256'], 'fixture_input_epoch': request['fixture_epoch'],
        'original_result_sha256': hashlib.sha256(original_raw['result_json'].encode()).hexdigest(),
        'abrupt_exit_code': 73, 'local_ack_committed': False, 'daemon_started': False,
        'inference_executed': False, 'receiver_executed': False, 'hardware_or_power_loss_verified': False}
    publish(root / 'before-abrupt-exit.json', witness)
    os._exit(73)  # This owned child only; never signal a PID/group or another process.


if __name__ == '__main__':
    main()

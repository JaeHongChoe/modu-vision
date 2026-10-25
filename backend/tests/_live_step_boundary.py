"""Test-only child scheduling observer; copied as temporary sitecustomize.

The real CLI, trainer, optimizer and status callback remain untouched. Resolve
original callback code lazily, after its ordinary production import completes.
"""
import dis
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback


def _publish(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    temporary.replace(path)


def step_boundary(config):
    output = Path(config['output'])
    spec_path = Path(config['spec_path'])
    original_code = None
    normal_returns = set()
    used = False

    def observe(frame, event, arg):
        nonlocal original_code, normal_returns, used
        if used or event != 'return' or threading.current_thread() is not threading.main_thread():
            return
        if original_code is None:
            module = sys.modules.get('backend.remote.worker')
            callback_type = getattr(module, '_TrainingStatusCallback', None)
            if callback_type is None:
                return
            original_code = callback_type.on_step_end.__code__
            normal_returns = {op.offset for op in dis.get_instructions(original_code)
                              if op.opname in {'RETURN_VALUE', 'RETURN_CONST'}}
        # Profile return events also occur during exception unwinding. Only an
        # executed return opcode proves the original callback returned normally.
        if frame.f_code is not original_code or frame.f_lasti not in normal_returns:
            return
        if frame.f_locals.get('step') != 0 or frame.f_locals.get('epoch') != 0:
            return
        callback = frame.f_locals.get('self')
        writer = getattr(callback, 'writer', None)
        if getattr(writer, 'path', None) != output / 'status.json':
            return
        spec = json.loads(spec_path.read_bytes())
        digest = hashlib.sha256(spec_path.read_bytes()).hexdigest()
        status = json.loads(writer.path.read_bytes())
        if (spec.get('job_id') != config['job_id'] or spec.get('output_dir') != str(output)
                or status.get('job_id') != config['job_id'] or status.get('spec_sha256') != digest
                or writer._payload.get('spec_sha256') != digest or status.get('status') != 'running'
                or status.get('current_step') != 1 or status.get('current_epoch') != 1):
            return
        used = True
        if time.monotonic() >= config['deadline']:
            raise TimeoutError('Step fixture exceeded original readiness deadline')
        import psutil
        held = dict(config, pid=os.getpid(), created_at=psutil.Process().create_time(),
                    owner_token=os.environ.get('MODU_VISION_LOCAL_WORKER_TOKEN'),
                    spec_sha256=digest, current_epoch=1, current_step=1,
                    stack=traceback.format_stack(frame)[-12:])
        if time.monotonic() >= config['deadline']:
            raise TimeoutError('Step fixture exceeded original readiness deadline')
        _publish(output / 'fixture-step-held.json', held)
        while time.monotonic() < config['deadline']:
            release = output / 'fixture-step-release.json'
            if (release.is_file() and json.loads(release.read_bytes()).get('token') == config['token']
                    and time.monotonic() < config['deadline']):
                return
            time.sleep(.01)
        raise TimeoutError('Step fixture exceeded original readiness deadline')

    return observe


if os.environ.get('MODU_TEST_STEP_BOUNDARY_CONFIG'):
    sys.setprofile(step_boundary(json.loads(Path(os.environ['MODU_TEST_STEP_BOUNDARY_CONFIG']).read_bytes())))

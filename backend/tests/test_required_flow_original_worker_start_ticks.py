"""Canonical original worker start-tick data; no worker, GPU, network or signals.

The original /proc parser is exercised against a private fake proc tree. Original
capture consumes only private source-control JSON; these are data-contract tests,
not process ownership, installed execution or lease-release acceptance.
"""
from pathlib import Path
import hashlib
import json
import os

import pytest
from backend.engine import required_flow_passive_capture as capture
from backend.remote.process_control import _entry

TICKS = '324294533'


def put(path, value):
    raw = (json.dumps(value, sort_keys=True, separators=(',', ':'))+'\n').encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    return hashlib.sha256(raw).hexdigest()


def private_original_records(tmp_path, *, ticks=TICKS):
    run = tmp_path/'runs'/('op_'+'1'*32)
    run.mkdir(parents=True, mode=0o700)
    spec = {'protocol_version':1, 'operation':'flowchart_run', 'task':'rotation',
            'job_id':'job_flow_original_data_control', 'input_manifest_sha256':'b'*64,
            'required_passive_capture':{'nonce':'a'*32}}
    spec_sha = put(run/'spec.json', spec)
    status = {'protocol_version':1, 'job_id':spec['job_id'], 'operation':'flowchart_run',
              'spec_sha256':spec_sha, 'status':'running'}
    put(run/'status.json', status)
    # This projection is the exact original _StatusWriter producer contract.
    fake_proc = tmp_path/'fake-proc'
    proc = fake_proc/str(os.getpid())
    proc.mkdir(parents=True)
    fields = ['R', '1', str(os.getpgrp()), str(os.getsid(0)), *(['0']*15), TICKS]
    (proc/'stat').write_text(str(os.getpid())+' (original worker with spaces) '+' '.join(fields)+'\n')
    process = _entry(fake_proc, os.getpid(), details=False)
    assert process['start_ticks'] == TICKS and type(process['start_ticks']) is str
    worker = {key:process[key] for key in ('pid','group','session','start_ticks')}
    worker.update(token='original-control-token', job_id=spec['job_id'],run_id=run.name,
                  operation='flowchart_run', protocol_version=1, spec_sha256=spec_sha)
    worker['start_ticks'] = ticks
    put(run/'worker_identity.json', worker)
    return run, spec, status, worker


@pytest.mark.parametrize('ticks', ['1', TICKS, '18446744073709551615'])
def test_original_proc_string_producer_is_preserved_without_identity_rewrite(tmp_path, ticks):
    run, spec, _, _ = private_original_records(tmp_path, ticks=ticks)
    original = (run/'worker_identity.json').read_bytes()
    binding = capture.original_worker_capture_binding(run, spec)
    assert binding['start_ticks'] == ticks and type(binding['start_ticks']) is str
    assert binding['worker_identity_sha256'] == hashlib.sha256(original).hexdigest()
    assert binding['spec_sha256'] == hashlib.sha256((run/'spec.json').read_bytes()).hexdigest()
    assert binding['run_id'] == run.name and binding['job_id'] == spec['job_id']
    assert binding['pid'] == os.getpid() and binding['group'] == os.getpgrp()
    assert binding['session'] == os.getsid(0)
    assert binding['runtime_authority'] is False and binding['parent_credit'] is False
    assert (run/'worker_identity.json').read_bytes() == original


@pytest.mark.parametrize('ticks', [
    None, True, False, 0, 1, 324294533, 1.0, [], {}, '', '0', '00', '01',
    '-1', '+1', ' 1', '1 ', '1\n', '1.0', '1e3', '١٢', '１２', '1\x00',
])
def test_noncanonical_or_nonpositive_start_ticks_are_not_coerced(tmp_path, ticks):
    run, spec, _, _ = private_original_records(tmp_path, ticks=ticks)
    with pytest.raises(ValueError, match='Original task/worker/session records differ'):
        capture.original_worker_capture_binding(run, spec)


def test_missing_start_ticks_still_refuses(tmp_path):
    run, spec, _, worker = private_original_records(tmp_path)
    del worker['start_ticks']
    put(run/'worker_identity.json', worker)
    with pytest.raises(ValueError, match='Original task/worker/session records differ'):
        capture.original_worker_capture_binding(run, spec)


@pytest.mark.parametrize('field,value', [
    ('job_id','different-job'), ('run_id','op_'+'2'*32), ('operation','train'),
    ('spec_sha256','c'*64), ('pid',-1), ('group',-1), ('session',-1), ('token',''),
])
def test_canonical_tick_text_does_not_relax_original_worker_binding_guards(tmp_path, field, value):
    run, spec, _, worker = private_original_records(tmp_path)
    worker[field] = value
    put(run/'worker_identity.json', worker)
    with pytest.raises(ValueError, match='Original task/worker/session records differ'):
        capture.original_worker_capture_binding(run, spec)


@pytest.mark.parametrize('field,value', [
    ('job_id','different-job'), ('operation','train'),
    ('spec_sha256','c'*64), ('status','completed'),
])
def test_original_status_scope_is_still_required(tmp_path, field, value):
    run, spec, status, _ = private_original_records(tmp_path)
    status[field] = value
    put(run/'status.json', status)
    with pytest.raises(ValueError, match='Original task/worker/session records differ'):
        capture.original_worker_capture_binding(run, spec)


def test_private_worker_record_security_is_not_bypassed_by_valid_ticks(tmp_path):
    run, spec, _, _ = private_original_records(tmp_path)
    (run/'worker_identity.json').chmod(0o644)
    with pytest.raises(ValueError, match='Original private operation record identity differs'):
        capture.original_worker_capture_binding(run, spec)

"""Strict, portable inference settings shared by all SDKs."""
from __future__ import annotations
import os
import re
from backend.engine.runtime_deadline import validate_deadline


def runtime_options(raw=None):
    raw={} if raw is None else raw
    if not isinstance(raw,dict) or set(raw)-{'deadline_ms','device','cpu_threads'}:
        raise ValueError('Unknown runtime options; use deadline_ms, device and cpu_threads')
    deadline=validate_deadline(raw.get('deadline_ms'))
    device=raw.get('device','cpu')
    if not isinstance(device,str) or not re.fullmatch(r'cpu|mps|cuda(?::[0-9]+)?|openvino:(?:CPU|GPU(?:\.[0-9]+)?|NPU(?:\.[0-9]+)?)',device):
        raise ValueError('Unsupported explicit runtime device')
    threads=raw.get('cpu_threads',1)
    if type(threads) is not int or not 1<=threads<=min(64,os.cpu_count() or 1):
        raise ValueError('cpu_threads must be a bounded positive integer')
    return {'deadline_ms':deadline,'device':device,'cpu_threads':threads}

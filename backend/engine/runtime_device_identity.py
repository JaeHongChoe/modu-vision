"""Device evidence and allocator limits for owned remote package parity."""
from __future__ import annotations

import os


def runtime_device_identity(device: str) -> dict:
    import torch
    actual = torch.device(device)
    identity = {'device': str(actual), 'process_id': os.getpid(), 'gpu_uuid': None,
                'device_name': 'CPU', 'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES'),
                'nvidia_visible_devices': os.environ.get('NVIDIA_VISIBLE_DEVICES'), 'memory_budget_mb': None}
    if actual.type == 'cuda':
        index = actual.index if actual.index is not None else torch.cuda.current_device()
        properties = torch.cuda.get_device_properties(index)
        identifier = getattr(properties, 'uuid', None)
        if isinstance(identifier, bytes):
            identifier = identifier.decode('ascii')
        if identifier is None or not str(identifier).strip():
            raise ValueError('Selected CUDA runtime cannot provide a GPU UUID; parity identity is unsupported')
        identity.update(device_name=properties.name, gpu_uuid=str(identifier), logical_cuda_index=index)
        budget = os.environ.get('VISION_PACKAGE_PARITY_CUDA_BUDGET_MB')
        if budget is not None:
            if not budget.isdecimal() or int(budget) < 1:
                raise ValueError('Invalid owned package CUDA allocator budget')
            requested = int(budget) * 1024 * 1024
            if requested > properties.total_memory:
                raise ValueError('Owned package CUDA allocator budget exceeds observed capacity')
            torch.cuda.set_per_process_memory_fraction(requested / properties.total_memory, index)
            identity['memory_budget_mb'] = int(budget)
    elif actual.type != 'cpu':
        raise ValueError('Remote package parity supports explicit CPU and CUDA devices only')
    return identity

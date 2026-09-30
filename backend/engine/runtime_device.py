"""Explicit runtime device selection never silently changes execution hardware."""
import re
import torch


def resolve_package_device(device='cpu'):
    if isinstance(device,str) and device.startswith('openvino:'):
        from backend.engine.openvino_runtime import require_openvino_device
        require_openvino_device(device.split(':',1)[1])
        return device
    return resolve_runtime_device(device)


def resolve_runtime_device(device='cpu'):
    if device == 'cpu': return torch.device('cpu')
    if device == 'mps':
        if not hasattr(torch.backends, 'mps') or not torch.backends.mps.is_available():
            raise ValueError('Requested MPS runtime is unavailable')
        return torch.device('mps')
    if isinstance(device, str) and re.fullmatch(r'cuda(?::[0-9]+)?', device):
        index = int(device.split(':')[1]) if ':' in device else 0
        if not torch.cuda.is_available() or index >= torch.cuda.device_count():
            raise ValueError('Requested CUDA runtime device is unavailable')
        return torch.device(device)
    raise ValueError('Runtime device must be cpu, cuda[:index], or mps')

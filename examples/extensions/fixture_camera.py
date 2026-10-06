"""MIT-licensed contributor input example. Finite control, no hardware claim.

Import this module only after source review as part of the application's static
build. A manifest never chooses a Python module, command or executable.
"""
import numpy as np
from backend.engine.camera_adapters import SimulatedCamera


def open_fixture_camera(source):
    if source != 'finite-fixture':
        raise ValueError('This example opens only its finite in-memory fixture')
    frame = np.zeros((32, 48, 3), dtype=np.uint8)
    frame[:, :, 1] = 96
    return SimulatedCamera([frame])

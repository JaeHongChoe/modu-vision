"""Shared fixtures for the backend's production-module tests."""

from pathlib import Path
import shutil
import sys
import tempfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def device_module():
    from backend.engine import device

    return device


@pytest.fixture
def synthetic_module():
    from backend.engine import synthetic_generator

    return synthetic_generator


@pytest.fixture
def loader_module():
    from backend.engine import dataset_loaders

    return dataset_loaders


@pytest.fixture
def temp_dir():
    tmp = tempfile.mkdtemp(prefix="modu_vision_test_")
    try:
        yield tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

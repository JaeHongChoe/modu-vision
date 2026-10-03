"""Test isolation for the application data stores, and shared fixtures for the backend's production-module tests.

pytest loads this file before any backend test module (it is the parent of backend/tests, and tests/e2e/conftest.py
imports it), so module-level stores (the training manager's reservation DB, the job ledger, local and remote job
journals, flow templates, saved splits, the thumbnail cache) resolve inside one temporary folder per test session
instead of the user's application data. HOME is never changed. A shell value of VISION_AI_STUDIO_USER_DATA_DIR is
deliberately replaced, as are the other store variables: they may name the user's real stores. A test that needs its
own folder sets VISION_AI_STUDIO_USER_DATA_DIR with monkeypatch where the store reads it when used (the job ledger);
stores fixed at import (the training manager's reservation DB, the split and thumbnail folders) are monkeypatched on
the module instead (routes_dataset.SPLIT_MANIFEST_DIR or THUMBNAIL_CACHE_DIR, dataset_loaders.SPLIT_MANIFEST_DIR).
The session folder is left in place after the run (temporary folders are never deleted by the tests).
"""

from pathlib import Path
import os
import shutil
import sys
import tempfile

import pytest

# MODU_TEST_DATA_DIR names an explicit folder for this session (a reviewer's or CI run); it is used, never removed.
# Exporting it makes any second run of this block in the same process reuse the folder.
_SESSION_DATA = os.environ.get('MODU_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='modu-vision-tests-')
os.environ['MODU_TEST_DATA_DIR'] = _SESSION_DATA
os.environ['VISION_AI_STUDIO_USER_DATA_DIR'] = os.path.join(_SESSION_DATA, 'user_data')
os.environ['MODU_FLOW_TEMPLATE_DIR'] = os.path.join(_SESSION_DATA, 'flow_templates')
os.environ['MODU_SPLIT_MANIFEST_DIR'] = os.path.join(_SESSION_DATA, 'splits')
os.environ['MODU_THUMBNAIL_CACHE_DIR'] = os.path.join(_SESSION_DATA, 'thumbnails')
os.environ.pop('VISION_RESOURCE_LEASE_DB', None)  # derived from the user-data folder above


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

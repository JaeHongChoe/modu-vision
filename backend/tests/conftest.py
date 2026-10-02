"""Test isolation for the application data stores.

Loaded before any test module imports the backend, so module-level stores (the training manager's reservation DB,
the job ledger, local and remote job journals, flow templates) resolve inside one temporary folder per test session
instead of the user's application data. HOME is never changed. A test that needs its own folder still sets
VISION_AI_STUDIO_USER_DATA_DIR (or a per-store variable) with monkeypatch, which takes precedence. A shell value of
VISION_AI_STUDIO_USER_DATA_DIR is deliberately replaced: it may name the user's real application data. The session
folder is left in place after the run (temporary folders are never deleted by the tests).
"""
import os
import tempfile

# MODU_TEST_DATA_DIR names an explicit folder for this session (a reviewer's or CI run); it is used, never removed.
_SESSION_DATA = os.environ.get('MODU_TEST_DATA_DIR') or tempfile.mkdtemp(prefix='modu-vision-tests-')
os.environ['VISION_AI_STUDIO_USER_DATA_DIR'] = os.path.join(_SESSION_DATA, 'user_data')
os.environ['MODU_FLOW_TEMPLATE_DIR'] = os.path.join(_SESSION_DATA, 'flow_templates')
os.environ.pop('VISION_RESOURCE_LEASE_DB', None)  # derived from the user-data folder above

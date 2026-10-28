"""The application data stores are isolated for every test session in backend/conftest.py, which pytest loads
before this folder's test modules."""

import os
from pathlib import Path
from types import FunctionType

import pytest

from scripts.ci.canary_unknown_observer import NODEID, TEST_NAME, Observer

_CANARY_ORIGINAL_CODE = pytest.StashKey()
_CANARY_CALL_FAILED = pytest.StashKey()


def pytest_collection_modifyitems(items):
    # Capture the collected original after pytest assertion rewriting, before fixtures.
    for item in items:
        if item.nodeid != NODEID:
            continue
        function = item.obj
        canonical = item.module.__dict__.get(TEST_NAME)
        if type(function) is FunctionType and function is canonical:
            item.stash[_CANARY_ORIGINAL_CODE] = function.__code__


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if item.nodeid == NODEID and report.when == 'call':
        # Reporting never changes the original outcome, exception or assertions.
        item.stash[_CANARY_CALL_FAILED] = report.failed is True


@pytest.fixture(autouse=True)
def _capture_original_canary_unknown(request):
    item = request.node
    directory = os.environ.get('MV_CI_RECORD_DIR')
    callspec = getattr(item, 'callspec', None)
    if (item.nodeid != NODEID or type(directory) is not str or not directory
            or callspec is None or callspec.params.get('point') != 'after_canary_result'):
        yield
        return
    anchor = item.stash.get(_CANARY_ORIGINAL_CODE, None)
    canonical = item.module.__dict__.get(TEST_NAME)
    if request.function is not item.obj or request.function is not canonical:
        yield
        return
    try:
        from backend.engine import runtime_deadline as runtime
        root = Path(__file__).resolve().parents[2]
        observer = Observer(module=runtime,
            runtime_source=(root/'backend/engine/runtime_deadline.py').read_bytes(),
            test_source=(root/'backend/tests/test_staged_update_canary.py').read_bytes(),
            selected_callable=request.function, canonical_test_callable=canonical,
            selected_code_anchor=anchor, nodeid=item.nodeid,
            point=callspec.params['point'])
    except Exception:
        # A diagnostic preparation problem cannot replace the original test.
        yield
        return
    try:
        with observer:
            yield
    finally:
        # The call report already exists; wrappers/trace are restored before I/O.
        try:
            observer.emit_failure(directory,
                call_failed=item.stash.get(_CANARY_CALL_FAILED, False))
        except Exception:
            pass

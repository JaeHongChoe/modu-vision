"""Original app-factory composition; no server, model, worker or GPU starts.

Run only under the repository's original isolated backend conftest. The pure
selector suite separately supplies exact retained input/profile data controls.
"""
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from backend import main
from backend.engine.application_launch_handshake import _new_original_work_admission


def test_original_owned_factory_binds_typed_selector_without_new_endpoint(tmp_path, monkeypatch):
    admission = _new_original_work_admission()
    monkeypatch.setattr(main, 'backend_work_admission', lambda: admission)
    app = main.create_app(project_dir=str(tmp_path/'projects'))
    selector = getattr(app.state, 'required_retained_flow_capture', None)
    # This is the causal original-factory assertion, before importing a new API.
    assert selector is not None
    from backend.engine.required_flow_passive_capture import (
        _OriginalRequiredFlowSelector, offer_original_required_flow_capture,
    )
    assert type(selector) is _OriginalRequiredFlowSelector
    assert selector.app is app
    assert selector.admission is admission
    assert selector.token is app.state.api_token
    assert not selector.used
    assert not any('passive' in getattr(route, 'path', '') for route in app.routes)
    original = Request({'type':'http', 'method':'POST', 'path':'/api/flowchart/run',
        'query_string':b'', 'headers':[(b'x-vision-token',b'wrong')], 'app':app})
    with pytest.raises(ValueError, match='process token'):
        offer_original_required_flow_capture(selector, original, SimpleNamespace(id='server42-l40s-2'),
            pipeline={}, image_path=tmp_path/'unused.png', device='cuda', stop_node_id=None)
    assert not selector.used


def test_standalone_and_shared_factories_keep_default_none(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'backend_work_admission', lambda: None)
    standalone = main.create_app(project_dir=str(tmp_path/'standalone'))
    assert getattr(standalone.state, 'required_retained_flow_capture', None) is None
    # Shared defaultNone remains unowned; explicit owned shared is covered separately.
    monkeypatch.setattr(main, 'backend_work_admission', lambda: None)
    shared = main.create_app(project_dir=str(tmp_path/'shared'), shared_auth_dir=str(tmp_path/'accounts'))
    assert shared.state.accounts is not None
    assert getattr(shared.state, 'required_retained_flow_capture', None) is None

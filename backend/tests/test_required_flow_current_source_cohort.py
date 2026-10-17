"""Actual checkout source admission only; no model, worker, network or native run."""
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from backend.engine import application_launch_handshake as handshake
from backend.engine import required_flow_http_capture as http
from backend.engine import required_flow_passive_capture as capture
from backend.engine.original_request_activity import OriginalRequestActivityObserver


ROOT = Path(__file__).resolve().parents[2]
PROFILE = 'server42-l40s-2-native43-20261016-01'
PROFILE_SHA = '6f36f621864d3c6b327295f684118434c6c1d18ddee8b21f30071527b0e7ea49'
CURRENT_SIX = {
    'backend/engine/flow_operators.py': {'size': 10481, 'sha256': '5b0c54aa9edb8e380747fbbd027d7bfa497ec51d02dc3e0a9628e4b87b5f688c'},
    'backend/engine/flow_package_runtime.py': {'size': 36196, 'sha256': 'c118527a396694b3bb2e5cfa237f174e9d6b9011c5146c13b930a8c17eb83c51'},
    'backend/engine/flowchart_engine.py': {'size': 149647, 'sha256': '958fb9f1d1047d351615f435b3ec16cb4dd5a5d21415ce886948b822f9bf7cba'},
    'backend/engine/model_backbones.py': {'size': 19191, 'sha256': '6873fecb60f4969bf4e15fbb057cebe1100b57c34e0685148b1fcd309d2192cf'},
    'backend/engine/rotation.py': {'size': 20800, 'sha256': '6fd1d70540d08263f46d1165dddd3e0ca1071abf2c1723bf2aa7572e9bd82899'},
    'backend/engine/segmentation_evidence.py': {'size': 8992, 'sha256': 'a25ff47aab794f516d8573c66d3a7d624368b1835c1baa23357bacd1efc207d4'},
}


def _startup(source):
    admission = handshake.BackendWorkAdmission(activity_observer=OriginalRequestActivityObserver())
    app = types.SimpleNamespace(state=types.SimpleNamespace(
        owned_work_admission=admission, api_token='a' * 64, accounts=None))

    class Auth:
        async def __call__(self, *args):
            raise AssertionError('Source qualification must not serve a request')

    selector = capture.bind_original_required_flow_selector(app, lambda: admission, Auth, source)
    return app, admission, selector


def test_current_actual_source_binds_only_an_unused_passive_prepared_record(tmp_path, monkeypatch):
    modules_before = set(sys.modules)
    _, admission, selector = _startup(ROOT)
    assert selector is not None, 'Current actual source must bind its separate exact cohort'
    proof = capture._verify_fixed_source_files(ROOT)
    assert {name: {key: row[key] for key in ('size', 'sha256')}
            for name, row in proof['files'].items()} == CURRENT_SIX
    assert proof['actual_installer_runtime_or_required_call_accepted'] is False

    root = tmp_path.resolve() / 'owned'
    root.mkdir(mode=0o700)
    nonce = '1' * 32
    homes = root / '.application-runtime-homes'
    homes.mkdir(mode=0o700)
    home = homes / nonce
    home.mkdir(mode=0o700)
    monkeypatch.setattr(handshake, '_CACHE', {'root': root, 'proof': {'nonce': nonce},
        'admission': admission, 'context': handshake._context(), 'ready': True})
    owner = http.bind_original_flow_http_capture(selector)
    assert owner is not None
    output = home / 'required-flow-http'
    assert sorted(path.name for path in output.iterdir()) == ['prepared.json']
    prepared = json.loads((output / 'prepared.json').read_bytes())
    assert prepared['launch_nonce'] == nonce and prepared['capture_nonce'] == selector.nonce
    assert prepared['profile_id'] == PROFILE
    assert (prepared['maximum_matching_requests'], prepared['request_budget_seconds']) == (1, 60)
    for key in ('permission_authority', 'lease_authority', 'parent_credit'):
        assert prepared[key] is False
    assert not any(prepared.get(key, False) for key in
                   ('model_quality_approved', 'actual_installed_runtime_accepted', 'native_acceptance'))
    assert selector.used is False and selector.issued is None
    assert owner.attempt is None and owner.finished is False and admission._active == 0
    assert not any(name == 'torch' or name.startswith('torch.') or name in
                   {'backend.engine.rotation', 'backend.engine.model_backbones',
                    'backend.engine.flowchart_engine'} for name in set(sys.modules) - modules_before)


@pytest.mark.parametrize('changed', sorted(CURRENT_SIX))
def test_one_byte_drift_in_any_current_producer_refuses_before_prepared(tmp_path, changed):
    source = tmp_path.resolve() / 'source'
    for relative in CURRENT_SIX:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    file = source / changed
    body = file.read_bytes()
    file.write_bytes(bytes([body[0] ^ 1]) + body[1:])
    assert file.stat().st_size == CURRENT_SIX[changed]['size']
    app, _, selector = _startup(source)
    assert selector is None
    assert getattr(app.state, 'required_retained_flow_capture', None) is None
    assert http.bind_original_flow_http_capture(selector) is None


def test_unknown_source_does_not_activate_a_selector_or_prepared_record(tmp_path):
    source = tmp_path.resolve() / 'unknown'
    source.mkdir()
    _, _, selector = _startup(source)
    assert selector is None and http.bind_original_flow_http_capture(selector) is None
    assert list(source.iterdir()) == []


def test_new_exact_cohort_keeps_historical_source_and_profile_bindings():
    cohort = capture._cohort_for_data(PROFILE, PROFILE_SHA, CURRENT_SIX)
    assert dict(cohort['source_pins']) == CURRENT_SIX
    assert cohort['source_pins'] is capture.PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS
    historical = (capture.PRODUCER_SOURCE_PINS, capture.CURRENT_PRODUCER_SOURCE_PINS,
                  capture.COMBINED_PRODUCER_SOURCE_PINS)
    assert tuple(row['source_pins'] for row in capture.RETAINED_CAPTURE_COHORTS[:3]) == historical
    for pins in historical:
        assert pins['backend/engine/rotation.py'] == {
            'size': 20447, 'sha256': 'b02622ed1c444272950b5f1f52523a29df096d71b8f257fc27903e380a492b45'}
    assert capture._cohort_for_data(PROFILE, PROFILE_SHA) is capture.RETAINED_CAPTURE_COHORTS[1]
    for profile, digest in ((capture.RETAINED_PROFILE_ID, PROFILE_SHA), (PROFILE, '0' * 64)):
        with pytest.raises(ValueError, match='coupled retained source/profile'):
            capture._cohort_for_data(profile, digest, CURRENT_SIX)


def test_new_cohort_still_requires_the_exact_registered_engine_observer(monkeypatch):
    # pytest's backend/../ import path is not an eligible immutable code filename.
    # Load this stdlib observer from its actual canonical source, without a model import.
    observer_path = ROOT / 'backend/engine/original_owned_flow_execution_request.py'
    observer = types.ModuleType('backend.engine.original_owned_flow_execution_request')
    observer.__file__ = str(observer_path)
    monkeypatch.setitem(sys.modules, observer.__name__, observer)
    exec(compile(observer_path.read_bytes(), str(observer_path), 'exec', dont_inherit=True), vars(observer))
    path = ROOT / 'backend/engine/flowchart_engine.py'
    compiled = compile(path.read_bytes(), str(path), 'exec', dont_inherit=True, optimize=sys.flags.optimize)

    def code_named(code, name):
        if code.co_qualname == name:
            return code
        for child in code.co_consts:
            if isinstance(child, types.CodeType):
                result = code_named(child, name)
                if result is not None:
                    return result

    # Construct the actual source code object without importing a model or executing it.
    engine = types.ModuleType('backend.engine.flowchart_engine')
    engine.__file__ = str(path)
    original = types.FunctionType(code_named(compiled, 'FlowchartEngine.execute'), vars(engine), 'execute')
    wrapped = observer.observe_original_execution_effect('engine')(original)
    engine.FlowchartEngine = type('FlowchartEngine', (), {'execute': wrapped})
    monkeypatch.setitem(sys.modules, engine.__name__, engine)
    spec = capture.FunctionSpec('original_required_execute', wrapped, ())
    proof = capture.verify_bound_functions((spec,), ROOT, CURRENT_SIX)
    assert set(proof['observer_files']) == {'backend/engine/original_owned_flow_execution_request.py'}
    assert proof['actual_installer_runtime_or_required_call_accepted'] is False
    with pytest.raises(ValueError, match='registered original engine observer'):
        capture.verify_bound_functions((capture.FunctionSpec('original_required_execute', original, ()),),
                                       ROOT, CURRENT_SIX)

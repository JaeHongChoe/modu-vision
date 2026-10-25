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
PARAMETERLESS_SIX = {
    'backend/engine/flow_operators.py': {'size': 10481, 'sha256': '5b0c54aa9edb8e380747fbbd027d7bfa497ec51d02dc3e0a9628e4b87b5f688c'},
    'backend/engine/flow_package_runtime.py': {'size': 36196, 'sha256': 'c118527a396694b3bb2e5cfa237f174e9d6b9011c5146c13b930a8c17eb83c51'},
    'backend/engine/flowchart_engine.py': {'size': 151555, 'sha256': '83bb9b25c7d66f91796cdbe6a83d6c13a3728991d76dd9cf7d3f73a27938f976'},
    'backend/engine/model_backbones.py': {'size': 19191, 'sha256': '6873fecb60f4969bf4e15fbb057cebe1100b57c34e0685148b1fcd309d2192cf'},
    'backend/engine/rotation.py': {'size': 20800, 'sha256': '6fd1d70540d08263f46d1165dddd3e0ca1071abf2c1723bf2aa7572e9bd82899'},
    'backend/engine/segmentation_evidence.py': {'size': 8992, 'sha256': 'a25ff47aab794f516d8573c66d3a7d624368b1835c1baa23357bacd1efc207d4'},
}
CURRENT_FILES = {
    **PARAMETERLESS_SIX,
    'backend/engine/flowchart_engine.py': {'size': 149170, 'sha256': 'aa2b99d4e8fd2d76e6123f98f89e8fc670b8107139f9ad7571187de85e9e833a'},
    'backend/engine/flowchart_schema.py': {'size': 2873, 'sha256': '37f10afcb3637ce1222597a695a074724484ea88571d3885914a0155d31e707c'},
}


def _source_copy(source):
    for relative in CURRENT_FILES:
        target = source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return source


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
            for name, row in proof['files'].items()} == CURRENT_FILES
    assert selector.cohort is capture.COLD_SCHEMA_CAPTURE_COHORT
    assert capture._pins_dictionary(selector.cohort) == CURRENT_FILES
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


@pytest.mark.parametrize('changed', sorted(CURRENT_FILES))
def test_one_byte_drift_in_any_current_producer_refuses_before_prepared(tmp_path, changed):
    source = _source_copy(tmp_path.resolve() / 'source')
    file = source / changed
    body = file.read_bytes()
    file.write_bytes(bytes([body[0] ^ 1]) + body[1:])
    assert file.stat().st_size == CURRENT_FILES[changed]['size']
    app, _, selector = _startup(source)
    assert selector is None
    assert getattr(app.state, 'required_retained_flow_capture', None) is None
    assert http.bind_original_flow_http_capture(selector) is None


@pytest.mark.parametrize('changed', ('missing', 'symlink'))
def test_schema_dependency_cannot_be_omitted_or_aliased_before_prepared(tmp_path, changed):
    source = _source_copy(tmp_path.resolve() / 'source')
    schema = source / 'backend/engine/flowchart_schema.py'
    schema.unlink()
    if changed == 'symlink':
        schema.symlink_to(ROOT / 'backend/engine/flowchart_schema.py')
    app, _, selector = _startup(source)
    assert selector is None
    assert getattr(app.state, 'required_retained_flow_capture', None) is None
    assert http.bind_original_flow_http_capture(selector) is None


def test_exact_current_cohort_rechecks_schema_bytes_after_selector_binding(tmp_path):
    source = _source_copy(tmp_path.resolve() / 'source')
    _, _, selector = _startup(source)
    assert selector is not None
    schema = source / 'backend/engine/flowchart_schema.py'
    body = schema.read_bytes()
    schema.write_bytes(body.replace(b'max_workers: int', b'max_workers: str', 1))
    assert schema.stat().st_size == CURRENT_FILES['backend/engine/flowchart_schema.py']['size']
    with pytest.raises(ValueError, match='source custody'):
        capture._fixed_source_cohort(source, selector.cohort)
    assert selector.used is False and selector.issued is None


def test_unknown_source_does_not_activate_a_selector_or_prepared_record(tmp_path):
    source = tmp_path.resolve() / 'unknown'
    source.mkdir()
    _, _, selector = _startup(source)
    assert selector is None and http.bind_original_flow_http_capture(selector) is None
    assert list(source.iterdir()) == []


def test_new_exact_cohort_keeps_historical_source_and_profile_bindings():
    cohort = capture._cohort_for_data(PROFILE, PROFILE_SHA, CURRENT_FILES)
    assert dict(cohort['source_pins']) == CURRENT_FILES
    assert cohort['source_pins'] is capture.COLD_SCHEMA_PRODUCER_SOURCE_PINS
    historical = (capture.PRODUCER_SOURCE_PINS, capture.CURRENT_PRODUCER_SOURCE_PINS,
                  capture.COMBINED_PRODUCER_SOURCE_PINS,
                  capture.PARAMETERLESS_ROTATION_PRODUCER_SOURCE_PINS)
    assert tuple(row['source_pins'] for row in capture.RETAINED_CAPTURE_COHORTS[:4]) == historical
    legacy_rotation = {
        'size': 20447, 'sha256': 'b02622ed1c444272950b5f1f52523a29df096d71b8f257fc27903e380a492b45'}
    combined = {**PARAMETERLESS_SIX, 'backend/engine/rotation.py': legacy_rotation}
    observer = {**combined, 'backend/engine/flowchart_engine.py': {
        'size': 148410, 'sha256': '00da5942fd90a37ea8cb5a86e89aed9ac3c65026f9e50b9337a42019c5acf001'}}
    legacy = {**observer, 'backend/engine/flowchart_engine.py': {
        'size': 148262, 'sha256': 'e62c8461a0f57d4ce8b4e49f5a0dbf21f50e25515e627f5cff778225cdcf1a70'},
        'backend/engine/flow_package_runtime.py': {
            'size': 35583, 'sha256': 'a55ca0b694d398bebe30af4916ccd43e0ab7ea115a9ae080af3bc313abcfeeec'}}
    for index, pins in enumerate((legacy, observer, combined, PARAMETERLESS_SIX)):
        retained = capture.RETAINED_CAPTURE_COHORTS[index]
        assert dict(historical[index]) == pins
        assert capture._cohort_for_data(retained['profile_id'], retained['execution_profile_sha256'], pins) is retained
    assert capture._cohort_for_data(PROFILE, PROFILE_SHA, PARAMETERLESS_SIX) is capture.PARAMETERLESS_ROTATION_CAPTURE_COHORT
    assert capture._cohort_for_data(PROFILE, PROFILE_SHA) is capture.RETAINED_CAPTURE_COHORTS[1]
    for profile, digest in ((capture.RETAINED_PROFILE_ID, PROFILE_SHA), (PROFILE, '0' * 64)):
        with pytest.raises(ValueError, match='coupled retained source/profile'):
            capture._cohort_for_data(profile, digest, CURRENT_FILES)


@pytest.mark.parametrize('changed', ('missing-schema', 'old-engine-with-schema', 'changed-schema'))
def test_new_cohort_rejects_incomplete_or_mixed_source_declarations(changed):
    pins = {name: dict(row) for name, row in CURRENT_FILES.items()}
    if changed == 'missing-schema':
        del pins['backend/engine/flowchart_schema.py']
    elif changed == 'old-engine-with-schema':
        pins['backend/engine/flowchart_engine.py'] = dict(PARAMETERLESS_SIX['backend/engine/flowchart_engine.py'])
    else:
        pins['backend/engine/flowchart_schema.py']['sha256'] = '0' * 64
    with pytest.raises(ValueError, match='coupled retained source/profile'):
        capture._cohort_for_data(PROFILE, PROFILE_SHA, pins)


@pytest.mark.parametrize('changed', ('unwrapped', 'reconstructed-wrapper', 'unregistered-wrapper'))
def test_new_cohort_still_requires_the_exact_registered_engine_observer(monkeypatch, changed):
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
    proof = capture.verify_bound_functions((spec,), ROOT, CURRENT_FILES)
    assert set(proof['observer_files']) == {'backend/engine/original_owned_flow_execution_request.py'}
    assert proof['actual_installer_runtime_or_required_call_accepted'] is False
    if changed == 'unwrapped':
        replacement = original
    else:
        replacement = types.FunctionType(wrapped.__code__, wrapped.__globals__, wrapped.__name__,
                                         wrapped.__defaults__, wrapped.__closure__)
        replacement.__dict__.update(wrapped.__dict__)
        if changed == 'unregistered-wrapper':
            engine.FlowchartEngine.execute = replacement
    with pytest.raises(ValueError, match='registered original engine observer'):
        capture.verify_bound_functions((capture.FunctionSpec('original_required_execute', replacement, ()),),
                                       ROOT, CURRENT_FILES)

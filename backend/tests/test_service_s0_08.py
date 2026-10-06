"""S0-08: the core paths run for real, not only through fixtures.

An anomaly model trained through the app's own training API on the CPU (PaDiM, no pretrained weights, so nothing is
downloaded) used to fail after its first epoch: the live progress message rounded every recorded metric as a number,
and an anomaly model also records a confusion matrix, its threshold basis and flags. The job now completes and the
flow model catalog offers it with its calibrated distance threshold.

Each reproduced core defect (S0-01 to S0-04) gets a BaselineEvidence record from scripts/service_baseline_evidence.py:
the tested commit, the fixture hash, the production path, the command, each test's outcome and the scope.
"""
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient
import numpy as np
import torch
from PIL import Image, ImageDraw

from backend.main import create_app
from backend.tests.test_service_integrity import PINNED

ROOT = Path(__file__).resolve().parents[2]


def _baseline():
    spec = importlib.util.spec_from_file_location('service_baseline_evidence', ROOT / 'scripts/service_baseline_evidence.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the module's annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _junit(*cases):
    """A JUnit report (bytes) with ``(name, outcome)`` cases; outcome is passed, failed, error or skipped."""
    marks = {'failed': '<failure message="assert 1 == 2 · 확인"/>', 'error': '<error message="fixture setup failed"/>',
             'skipped': '<skipped/>'}
    body = ''.join(f'<testcase name="{name}">{marks.get(outcome, "")}</testcase>' for name, outcome in cases)
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite>{body}</testsuite></testsuites>'.encode('utf-8')


def test_the_live_progress_message_rounds_numbers_and_keeps_other_recorded_metrics():
    from backend.api import websocket_telemetry
    sent = []
    callback = websocket_telemetry.WebSocketTelemetryCallback('job_progress')
    callback._broadcast = lambda event, data: sent.append((event, data))
    callback.on_epoch_end(0, 1, 0.123456789, 0.5, 0.001, {
        'image_auroc': 0.987654321, 'confusion_matrix': [[2, 0], [1, 1]], 'threshold_basis': 'f1_optimal',
        'threshold_search_available': True, 'pixel_auroc': None, 'sample_count': 4,
        'numpy_score': np.float32(0.123456789), 'numpy_flag': np.bool_(True), 'tensor_loss': torch.tensor(2.5),
        'tensor_matrix': torch.tensor([[1, 0], [0, 1]])})
    event, data = sent[-1]
    assert event == 'epoch_progress'
    assert data['metrics'] == {'image_auroc': 0.98765, 'confusion_matrix': [[2, 0], [1, 1]], 'threshold_basis': 'f1_optimal',
                               'threshold_search_available': True, 'pixel_auroc': None, 'sample_count': 4.0,
                               'numpy_score': 0.12346, 'numpy_flag': True, 'tensor_loss': 2.5, 'tensor_matrix': [[1, 0], [0, 1]]}
    assert data['metrics']['threshold_search_available'] is True and data['metrics']['numpy_flag'] is True, 'a flag is not rounded'
    json.dumps(data)  # the message serialises


def _anomaly_dataset(root: Path) -> Path:
    for partition, (folder, count, defect) in enumerate((('train/good', 6, False), ('test/good', 2, False), ('test/defect', 2, True))):
        (root / folder).mkdir(parents=True, exist_ok=True)
        for index in range(count):
            image = Image.new('RGB', (48, 48), (200 - partition * 12, 200, 200))
            pen = ImageDraw.Draw(image)
            pen.rectangle((4 + index, 4, 20 + index, 20), fill=(180, 180, 180))
            if defect:
                pen.ellipse((26, 26, 40, 40), fill=(20, 20, 20))
            image.save(root / folder / f'{"ng" if defect else "ok"}_{index}.png')
    return root


def test_an_anomaly_model_trained_through_the_app_on_the_cpu_completes_with_a_calibrated_threshold(tmp_path):
    app = create_app(project_dir=str(tmp_path / 'workspaces'))
    api = TestClient(app, headers={'X-Vision-Token': app.state.api_token})
    assert api.post('/api/project/create', json={'name': 'S0-08 anomaly', 'task': 'anomaly'}).status_code == 200
    source = _anomaly_dataset(tmp_path / '이상 데이터')
    project = api.put('/api/project/update', json={'source_dataset_dir': str(source)}).json()
    assert api.post('/api/dataset/import', json={'folder_path': str(source), 'task': 'anomaly'}).status_code == 200
    started = api.post('/api/training/start', json={
        'task': 'anomaly', 'preset': 'fast', 'dataset_path': project['source_dataset_dir'], 'device': 'cpu',
        'config_overrides': {'pretrained': False, 'image_size': 32, 'anomaly_method': 'padim', 'num_workers': 0, 'batch_size': 2}})
    assert started.status_code == 200, started.text
    job_id, deadline = started.json()['job_id'], time.monotonic() + 300
    status = {}
    while time.monotonic() < deadline:
        status = api.get('/api/training/status', params={'job_id': job_id}).json()
        if status.get('status') not in ('queued', 'preparing', 'running'):
            break
        time.sleep(0.5)
    assert status.get('status') == 'completed', status.get('error')
    catalog = api.get('/api/flowchart/models/catalog', params={'source_dataset_path': project['source_dataset_dir']}).json()
    model = next(row for row in catalog['models'] if row['job_id'] == job_id)
    assert model['task'] == 'anomaly'
    assert model['score_spec']['domain'] == 'distance' and model['score_spec']['unit'] == 'mahalanobis_distance'
    assert model['score_spec']['threshold'] > 0 and model['threshold_settings']['threshold'] == model['score_spec']['threshold']


def test_every_pinned_defect_has_a_production_path_and_a_scope():
    baseline = _baseline()
    assert set(baseline.PATHS) == set(baseline.SCOPES) == set(PINNED)
    assert all(text.strip() for text in [*baseline.PATHS.values(), *baseline.SCOPES.values()])


def test_a_baseline_record_carries_the_contract_fields_and_each_tests_outcome():
    baseline = _baseline()
    module, tests = PINNED['S0-01']
    commands = []
    cases = [(f'{tests[0]}[coco]', 'passed'), (f'{tests[0]}[yolo]', 'passed'), *((test, 'passed') for test in tests[1:])]
    record = baseline.record('S0-01', runner=lambda command: (commands.append(command), (0, _junit(*cases), ''))[1])
    source = ROOT / (module.replace('.', '/') + '.py')
    assert record.defect == 'S0-01' and record.path == baseline.PATHS['S0-01'] and record.scope == baseline.SCOPES['S0-01']
    assert record.source_sha is None or re.fullmatch(r'[0-9a-f]{40}', record.source_sha)
    assert record.fixture_files[0] == source.relative_to(ROOT).as_posix()
    assert record.fixture_files[-2:] == ['backend/conftest.py', 'backend/tests/conftest.py'], 'the isolation conftest is hashed'
    assert record.fixture_hash == baseline.fixture_hash([ROOT / name for name in record.fixture_files])
    assert record.command[:3] == ['python', '-m', 'pytest'] and commands[0][1:] == record.command[1:-1]
    assert record.command[-len(tests) - 1:] == [*(f'{source.relative_to(ROOT).as_posix()}::{test}' for test in tests), '--junitxml=<report>']
    assert record.result['outcome'] == 'passed' and record.result['failures'] == {}
    assert record.result['tests'] == {tests[0]: 'passed (2 cases)', **{test: 'passed' for test in tests[1:]}}
    assert set(record.result['packages']) >= {'fastapi', 'starlette', 'httpx', 'torch'}


def test_a_failed_errored_skipped_or_missing_test_or_a_failing_exit_fails_the_record_with_its_reason():
    baseline = _baseline()
    tests = PINNED['S0-02'][1]
    others = [(test, 'passed') for test in tests[1:]]
    failed = baseline.record('S0-02', runner=lambda command: (1, _junit((f'{tests[0]}[a]', 'passed'), (f'{tests[0]}[b]', 'failed'), *others), ''))
    assert failed.result['outcome'] == 'failed' and failed.result['tests'][tests[0]] == 'failed'
    assert failed.result['failures'] == {tests[0]: 'assert 1 == 2 · 확인'}, 'the failure keeps its own message'
    errored = baseline.record('S0-02', runner=lambda command: (1, _junit((tests[0], 'error'), *others), ''))
    assert errored.result['tests'][tests[0]] == 'failed' and errored.result['failures'][tests[0]] == 'fixture setup failed'
    missing = baseline.record('S0-02', runner=lambda command: (0, _junit(*others), ''))
    assert missing.result['outcome'] == 'failed' and missing.result['tests'][tests[0]] == 'not run'
    skipped = baseline.record('S0-02', runner=lambda command: (0, _junit((tests[0], 'skipped'), *others), ''))
    assert skipped.result['outcome'] == 'failed' and skipped.result['tests'][tests[0]] == 'skipped'
    crashed = baseline.record('S0-02', runner=lambda command: (4, b'<testsuites/>', 'ERROR: file or directory not found \ufffd'))
    assert crashed.result['outcome'] == 'failed' and crashed.result['output_tail'].startswith('ERROR: file or directory not found')
    broken = baseline.record('S0-02', runner=lambda command: (1, b'<testsuites', 'INTERNALERROR> crashed'))
    assert broken.result['outcome'] == 'failed' and 'unreadable JUnit report' in broken.result['failures']['report']
    assert broken.result['output_tail'] == 'INTERNALERROR> crashed', 'the only diagnostic is kept'


def test_a_dirty_checkout_and_every_fixture_module_are_part_of_the_record(monkeypatch):
    baseline = _baseline()
    answers = {'rev-parse': 'a' * 40 + '\n', 'status': ' M backend/engine/augmentations.py\n?? backend/tests/runtime_release_fixture.py\n'}
    monkeypatch.setattr(baseline, '_git', lambda *args: answers[args[0]])
    assert baseline.source_state() == ('a' * 40, ['backend/engine/augmentations.py', 'backend/tests/runtime_release_fixture.py'])
    monkeypatch.setattr(baseline, '_git', lambda *args: None)
    assert baseline.source_state()[0] is None
    module = PINNED['S0-03'][0]
    source = (ROOT / (module.replace('.', '/') + '.py')).read_text(encoding='utf-8')
    imported = set(re.findall(r'^from (backend\.tests\.\w+) import', source, flags=re.M))
    files = {path.relative_to(ROOT).as_posix() for path in baseline.fixture_files(module)}
    assert {name.replace('.', '/') + '.py' for name in imported} <= files, 'a fixture module the regressions import is hashed'


def test_the_fixture_hash_ignores_line_endings(tmp_path, monkeypatch):
    baseline = _baseline()
    monkeypatch.setattr(baseline, 'ROOT', tmp_path)
    fixture = tmp_path / 'fixture.py'
    fixture.write_bytes(b'a = 1\nb = 2\n')
    lf = baseline.fixture_hash([fixture])
    fixture.write_bytes(b'a = 1\r\nb = 2\r\n')
    assert baseline.fixture_hash([fixture]) == lf
    fixture.write_bytes(b'a = 1\nb = 3\n')
    assert baseline.fixture_hash([fixture]) != lf


def test_the_script_imports_nothing_from_the_app():
    """Reading PINNED never imports the regression modules, so the script itself opens no app store."""
    import subprocess
    probe = ('import importlib.util, sys; spec = importlib.util.spec_from_file_location("b", sys.argv[1]); '
             'module = importlib.util.module_from_spec(spec); sys.modules["b"] = module; spec.loader.exec_module(module); '
             'print(sorted(name for name in sys.modules if name == "backend" or name.startswith("backend.")))')
    loaded = subprocess.run([sys.executable, '-c', probe, str(ROOT / 'scripts/service_baseline_evidence.py')],
                            capture_output=True, text=True, check=True).stdout.strip()
    assert loaded == '[]'


def test_the_command_line_runs_one_defect_for_real_and_writes_its_record(tmp_path):
    output = tmp_path / 'baseline.json'
    assert _baseline().main(['--output', str(output), '--defect', 'S0-01']) == 0
    records = json.loads(output.read_text(encoding='utf-8'))['records']
    assert [row['defect'] for row in records] == ['S0-01']
    assert records[0]['result']['outcome'] == 'passed'
    assert set(records[0]) == {'defect', 'source_sha', 'source_changes', 'fixture_hash', 'fixture_files', 'path', 'command',
                               'result', 'scope'}


def test_the_regressions_run_with_the_platform_default_encoding(monkeypatch):
    """Output is read as UTF-8, but the regressions are not put into UTF-8 mode: that would hide a default-code-page
    open() on Windows, the defect class the encoding tests exist for."""
    baseline = _baseline()
    seen = {}

    class Completed:
        returncode, stdout, stderr = 0, '', ''

    def run(command, **kwargs):
        seen.update(kwargs)
        return Completed()
    monkeypatch.setattr(baseline.subprocess, 'run', run)
    baseline.run_pytest(['python', '-m', 'pytest'])
    assert seen['env']['PYTHONIOENCODING'] == 'utf-8'
    assert seen['env'].get('PYTHONUTF8') == os.environ.get('PYTHONUTF8'), 'the script adds no UTF-8 mode of its own'
    assert seen['encoding'] == 'utf-8' and seen['errors'] == 'replace'

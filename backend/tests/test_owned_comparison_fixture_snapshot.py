"""The untrained CPU comparison fixture must also satisfy STEP4's real holdout contract."""
import hashlib
import json
import os
import runpy
import sys
from pathlib import Path

import pytest
import torch
from fastapi import HTTPException
from PIL import Image

from backend.api import routes_dataset, routes_evaluation
from backend.engine import checkpoint_paths
from backend.engine.dataset_loaders import ClassificationDataset


def _pixels(source, splits):
    for split in splits:
        for name, color in (('OK', 'white'), ('NG', 'red')):
            folder = source / split / name
            folder.mkdir(parents=True)
            Image.new('RGB', (64, 64), color).save(folder / (name + '.png'))


def _hashes(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob('*') if path.is_file()}


def _run_comparison_fixture(source, models, monkeypatch):
    # A generic repository pytest run uses its own checked-in fixture. The
    # private RED/GREEN harness may still explicitly select another original.
    fixture_file = os.environ.get('MODU_COMPARISON_FIXTURE_UNDER_TEST') or str(
        Path(__file__).resolve().parents[2] / 'scripts/e2e/fixtures/comparison_models.py')
    monkeypatch.setattr(sys, 'argv', [fixture_file, str(source), str(models)])
    runpy.run_path(fixture_file, run_name='__main__')


def test_fixture_snapshot_supports_original_latest_recovery_and_exact_heldout_pixels(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'source'
    _pixels(source, ('train', 'val', 'test'))
    original = _hashes(source)
    models = tmp_path / 'project' / 'models'
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(routes_evaluation.training_job_manager, '_jobs', {})
    monkeypatch.setattr(routes_evaluation.training_job_manager, '_active_job_id', None)
    monkeypatch.setattr(checkpoint_paths, '_ACTIVE_PROJECT_MODELS_DIR', None)
    # The real resolver, checkpoint loader, inference, metrics, remapping and
    # persistence remain original. Only this controlled test's device is CPU.
    monkeypatch.setattr(routes_evaluation, 'get_device', lambda: torch.device('cpu'))
    _run_comparison_fixture(source, models, monkeypatch)
    generated = json.loads(capsys.readouterr().out)
    assert generated['checkpoint_kind'] == 'untrained_deterministic'
    checkpoint_paths.set_active_project_models_dir(models)

    # This is the exact latest-recovery call made on STEP4 entry: no job_id,
    # no dataset override, no rewritten source/partition or fake HTTP result.
    latest = routes_evaluation.run_or_load_evaluation(
        source_dataset_path=str(source), source_task='classification')
    assert latest['job_id'] in {'job_fixture_incumbent', 'job_fixture_candidate'}
    assert len(latest['test_predictions']) == 2
    assert latest['metrics']['evaluated_split'] == 'test'
    assert latest['metrics']['selection_overlap'] is False
    assert latest['metrics']['accuracy'] == pytest.approx(0.5)
    expected = {str(source / 'test' / name / (name + '.png')) for name in ('OK', 'NG')}
    assert {row['file_path'] for row in latest['test_predictions']} == expected
    assert all(row['labeling_supported'] is True for row in latest['test_predictions'])
    assert all(Path(row['evaluation_file_path']).is_relative_to(models / latest['job_id'] / 'dataset' / 'test')
               for row in latest['test_predictions'])
    for name in ('job_fixture_incumbent', 'job_fixture_candidate'):
        job = models / name
        receipt = json.loads((job / 'job_receipt.json').read_text())
        assert receipt['test_fixture'] == 'untrained_deterministic'
        assert receipt['dataset_path'] == str(job / 'dataset')
        copied = _hashes(job / 'dataset')
        assert {key: copied[key] for key in original} == original
        assert set(copied) == set(original) | {'source_manifest.json'}
        for split in ('train', 'val', 'test'):
            dataset = ClassificationDataset(job / 'dataset', split=split)
            assert dataset.split_basis == 'folders'
            assert len(dataset.samples) == 2
        saved = json.loads((job / 'dataset' / 'source_manifest.json').read_text())
        assert len(saved) == 6
        assert {row['source_image'] for row in saved} == {str(source / relative) for relative in original}
        assert all(Path(row['image']).relative_to(job / 'dataset') == Path(row['source_image']).relative_to(source)
                   for row in saved)
    assert _hashes(source) == original


@pytest.mark.parametrize('splits', [(), ('train',)])
def test_original_evaluation_still_refuses_empty_or_train_only_without_loading_checkpoint(tmp_path, splits):
    source = tmp_path / 'source'
    source.mkdir()
    _pixels(source, splits)
    with pytest.raises(HTTPException) as caught:
        routes_evaluation._evaluate_classification(tmp_path / 'absent.pt', {}, source, torch.device('cpu'))
    assert caught.value.status_code == 422
    assert caught.value.detail == 'No held-out classification images. Save a nonempty test or validation partition before evaluation.'


def test_explicit_fixture_environment_still_executes_that_exact_supplied_file(tmp_path, monkeypatch):
    supplied = tmp_path / 'explicit-comparison-fixture.py'
    supplied.write_text('raise RuntimeError("exact explicit comparison fixture observed")\n')
    monkeypatch.setenv('MODU_COMPARISON_FIXTURE_UNDER_TEST', str(supplied))
    with pytest.raises(RuntimeError, match='^exact explicit comparison fixture observed$'):
        _run_comparison_fixture(tmp_path / 'source', tmp_path / 'models', monkeypatch)
    assert not (tmp_path / 'models').exists(), 'The checked-in model generator must not run instead'

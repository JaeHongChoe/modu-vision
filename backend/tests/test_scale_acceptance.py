"""Acceptance measurements use isolated fixtures and do not invent target support."""
from pathlib import Path
import pytest
from scripts.acceptance.scale_and_soak import measure_scale, queue_control


def test_search_measurement_covers_every_metadata_row_and_decodes_separate_image(tmp_path):
    result = measure_scale(tmp_path / 'scale', rows=250, width=512, height=384, repeats=3)
    assert result['metadata']['fixture_kind'] == 'metadata_only_not_image_inventory'
    assert result['metadata']['rows'] == result['metadata']['paged_rows'] == 250
    assert result['metadata']['unique_ordered_rows']
    assert result['metadata']['searches']['missing']['returned'] == 0
    assert result['metadata']['searches']['ng']['returned'] > 0
    assert result['large_image']['decoded']
    assert result['large_image']['dimensions'] == [512, 384]
    assert result['large_image']['source_unchanged']
    assert not result['target_support_approved']
    assert not result['soak_72h_completed']


def test_owned_measurement_directory_cannot_replace_existing_evidence(tmp_path):
    root = tmp_path / 'existing'; root.mkdir()
    (root / 'preserved').write_text('receipt', encoding='utf-8')
    with pytest.raises(FileExistsError): measure_scale(root, rows=10, width=16, height=16, repeats=1)
    assert (root / 'preserved').read_text(encoding='utf-8') == 'receipt'


def test_queue_control_exercises_duplicate_backpressure_and_restart_without_model_claim(tmp_path):
    result = queue_control(tmp_path / 'queue', cycles=3, capacity=4)
    assert result['completed'] == 12
    assert result['duplicate_requests'] == 12
    assert result['backpressure_refusals'] == 3
    assert result['reopened_completed'] == 12
    assert result['source_unchanged']
    assert not result['actual_model_inference']
    assert not result['soak_72h_completed']

"""A field export that keeps normal and defect shots in one train folder names them in the file: anomaly001.jpg is a
defect wherever it sits, nomaly2024….jpg is normal, and names that say nothing fall back to the folder rules."""
from pathlib import Path

from backend.engine.grouped_dataset_views import is_anomaly_normal


def test_defect_and_normal_file_names_override_the_train_folder(tmp_path):
    source = tmp_path
    assert is_anomaly_normal(source / 'train' / 'anomaly001.jpg', source) is False
    assert is_anomaly_normal(source / 'train' / 'nomaly20240925_2409256762584_5.jpg', source) is True
    assert is_anomaly_normal(source / 'validation' / 'anomaly007.jpg', source) is False
    assert is_anomaly_normal(source / 'validation' / 'normal_12.jpg', source) is True


def test_names_that_say_nothing_keep_the_folder_rules(tmp_path):
    source = tmp_path
    assert is_anomaly_normal(source / 'train' / 'img_001.jpg', source) is True
    assert is_anomaly_normal(source / 'test' / 'good' / 'img_001.jpg', source) is True
    assert is_anomaly_normal(source / 'test' / 'crack' / 'img_001.jpg', source) is False

from pathlib import Path

import pytest
from fastapi import HTTPException

from backend.api.routes_evaluation import _paired_evaluation_paths


@pytest.mark.parametrize("task,label_path", [
    ("detection", "annotations_test.json"),
    ("segmentation", "masks/test"),
])
def test_independent_test_partition_precedes_validation(tmp_path: Path, task: str, label_path: str):
    for split in ("test", "val"):
        (tmp_path / "images" / split).mkdir(parents=True)
    if task == "detection":
        for split in ("test", "val"):
            (tmp_path / f"annotations_{split}.json").write_text("{}")
    else:
        for split in ("test", "val"):
            (tmp_path / "masks" / split).mkdir(parents=True)

    image_dir, labels, split = _paired_evaluation_paths(tmp_path, task)
    assert split == "test"
    assert image_dir == tmp_path / "images" / "test"
    assert labels == tmp_path / label_path


def test_incomplete_test_partition_fails_instead_of_silently_using_validation(tmp_path: Path):
    (tmp_path / "images" / "test").mkdir(parents=True)
    (tmp_path / "images" / "val").mkdir(parents=True)
    (tmp_path / "annotations_val.json").write_text("{}")

    with pytest.raises(HTTPException, match="test"):
        _paired_evaluation_paths(tmp_path, "detection")

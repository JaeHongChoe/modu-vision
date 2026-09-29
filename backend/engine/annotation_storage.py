"""Dataset-scoped location for Studio annotations, separate from source images."""

from __future__ import annotations

import hashlib
from pathlib import Path


def dataset_annotation_dir(dataset_folder: Path, root: Path = Path("./annotations")) -> Path:
    canonical = str(Path(dataset_folder).resolve())
    dataset_key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return Path(root) / "by_dataset" / dataset_key

"""What changed in a project's data since a model was trained, in words a first-time user can act on (audit F-09/A3).

A model trained from a saved dataset version records that version. When the folder no longer matches it, every later
step refuses the model (correctly), but the refusal alone does not say what changed or how to get back. This module
compares the bound version with the current folder and sorts the differences into added, removed and changed images,
changed label files, a changed split and changed per-image classes. It only reads; restoring stays the existing,
confirmed dataset-version restore, which puts back labels, split and classes but never source images.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Dict, Optional

DATA_CHANGED_PREFIX = "데이터가 바뀌었습니다"
EXAMPLES = 10


def data_changed_message(job_id: str) -> str:
    return (f"{DATA_CHANGED_PREFIX}: 모델 {job_id}을(를) 학습한 뒤 데이터 폴더의 이미지·라벨·분할이 달라졌습니다. "
            "바뀐 내용은 아래 안내줄의 '왜 그런가요?'에서 볼 수 있습니다. 3단계에서 다시 학습하거나, 라벨·분할·클래스만 "
            "바뀌었다면 1단계 '데이터 버전'에서 학습 때 버전으로 되돌리세요.")


def _image(name: str) -> bool:
    from backend.engine.dataset_loaders import SUPPORTED_IMAGE_EXTENSIONS
    return PurePosixPath(name).suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS


def describe_data_changes(project: Dict[str, Any], binding: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Differences between the model's bound dataset version and the current folder.

    ``state`` is ``current`` (no difference found), ``changed`` or ``unverified`` (no usable version to compare with).
    Counts are exact; the example lists hold at most ``EXAMPLES`` relative paths each.
    """
    from backend.api.routes_dataset_versions import _manifest_digest, _verify
    from backend.engine.workflow_impact import _read
    empty = {"added": [], "removed": [], "modified": []}
    result: Dict[str, Any] = {"state": "unverified", "reason": "", "dataset_version_id": None, "dataset_version_name": None,
                              "counts": {"added": 0, "removed": 0, "modified": 0, "label_files": 0},
                              "split_changed": False, "classes_changed": False, "labelset_changed": False,
                              "examples": empty, "restorable": False}
    if not binding or not binding.get("version_dir"):
        result["reason"] = "이 모델은 데이터 버전을 남기지 않아 무엇이 바뀌었는지 비교할 수 없습니다."
        return result
    root = Path(project["project_dir"]).resolve()
    directory = Path(binding["version_dir"])
    if directory.is_symlink() or not directory.resolve().is_relative_to(root / "versions"):
        result["reason"] = "학습 때 데이터 버전이 이 프로젝트 밖에 있습니다."
        return result
    manifest = _read(directory / "manifest.json")
    if not manifest:
        result["reason"] = "학습 때 데이터 버전 기록을 찾을 수 없습니다."
        return result
    result["dataset_version_id"] = manifest.get("id") or binding.get("dataset_version_id")
    result["dataset_version_name"] = manifest.get("name")
    if manifest.get("project_id") != project.get("id") or _manifest_digest(manifest) != binding.get("manifest_sha256"):
        result["reason"] = "학습 때 데이터 버전 기록이 바뀌어 비교할 수 없습니다."
        return result
    checked = _verify(project, directory, manifest)
    saved = {row["relative_path"]: row for row in manifest["files"] if row.get("origin") == "source"}
    source = Path(manifest["source_dataset_dir"])
    added, removed, modified, label_files = [], [], [], 0
    for name in checked["changed_files"]:
        if not name.startswith("source/"):
            continue  # a damaged backup inside the version; reported through restorable below
        relative = name[len("source/"):]
        if not _image(relative):
            label_files += 1
        elif relative not in saved:
            added.append(relative)
        elif not (source / relative).is_file():
            removed.append(relative)
        else:
            modified.append(relative)
    split_changed = classes_changed = False
    for name in checked["editable_changed_files"]:
        parts = PurePosixPath(name).parts
        if name == "split/manifest.json":
            split_changed = True
        elif parts[-2:] == ("classification", "class_overrides.json"):
            classes_changed = True
        elif "metadata" in parts[1:-1]:
            continue  # review and usage audit; compared through the team-data binding, not as a label change
        else:
            label_files += 1
    labelset_changed = manifest.get("labelset_id", "default") != project.get("active_labelset_id", "default")
    backup_damaged = any(name.startswith("backup/") for name in checked["changed_files"])
    changed = bool(added or removed or modified or label_files or split_changed or classes_changed or labelset_changed)
    result.update({
        "state": "changed" if changed else "current",
        "counts": {"added": len(added), "removed": len(removed), "modified": len(modified), "label_files": label_files},
        "split_changed": split_changed, "classes_changed": classes_changed, "labelset_changed": labelset_changed,
        "examples": {"added": added[:EXAMPLES], "removed": removed[:EXAMPLES], "modified": modified[:EXAMPLES]},
        # The version restore puts back labels, split and classes. It refuses while source images differ.
        "restorable": changed and not (added or removed or modified or backup_damaged) and not labelset_changed,
    })
    if not changed:
        result["reason"] = "이미지·라벨·분할은 학습 때와 같습니다."
    elif labelset_changed:
        result["reason"] = "학습 때와 다른 라벨 세트를 쓰고 있습니다."
    elif result["restorable"]:
        result["reason"] = "이미지는 그대로이고 라벨·분할·클래스만 바뀌었습니다. 학습 때 버전으로 되돌릴 수 있습니다."
    else:
        result["reason"] = "이미지가 더해지거나 빠지거나 바뀌었습니다. 버전 복원은 이미지를 되돌리지 않으니 다시 학습하세요."
    return result

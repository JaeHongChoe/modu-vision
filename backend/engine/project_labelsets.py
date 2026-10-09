"""Independent editable Studio annotation overlays within one project."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any


_SET_ID = re.compile(r"ls_[0-9a-f]{12}")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def labelset_root(project_dir: Path, set_id: str) -> Path:
    project_dir = Path(project_dir).resolve()
    if set_id == "default":
        return project_dir / "annotations"
    if not _SET_ID.fullmatch(set_id):
        raise ValueError("Invalid label set ID")
    return project_dir / "labelsets" / set_id / "annotations"


def load_labelsets(project_dir: Path) -> dict[str, Any]:
    project_dir = Path(project_dir).resolve()
    path = project_dir / "labelsets.json"
    if not path.exists():
        registry = {
            "schema_version": 1,
            "active_id": "default",
            "labelsets": [{"id": "default", "name": "기본 라벨", "source_id": None,
                           "created_at": ""}],
        }
        _atomic_json(path, registry)
        return registry
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
        rows = registry["labelsets"]
        ids = [row["id"] for row in rows]
        if (registry["schema_version"] != 1 or not isinstance(rows, list)
                or "default" not in ids or len(ids) != len(set(ids))
                or registry["active_id"] not in ids
                or any(not isinstance(row.get("name"), str)
                       or (row["id"] != "default" and not _SET_ID.fullmatch(row["id"]))
                       for row in rows)):
            raise ValueError("Invalid label set registry")
        return registry
    except (OSError, TypeError, KeyError, ValueError) as exc:
        raise ValueError(f"Invalid label set registry: {exc}") from exc


def create_labelset(project_dir: Path, name: str) -> dict[str, Any]:
    if isinstance(name, str) and not str.strip(name):
        raise ValueError("Label set name must not be blank")
    registry = load_labelsets(project_dir)
    source_id = registry["active_id"]
    source = labelset_root(project_dir, source_id)
    if source.is_symlink() or (source.exists() and any(path.is_symlink() for path in source.rglob("*"))):
        raise ValueError("Label set contains a symbolic link")
    set_id = f"ls_{uuid.uuid4().hex[:12]}"
    target = labelset_root(project_dir, set_id)
    target.parent.mkdir(parents=True, exist_ok=False)
    try:
        if source.exists():
            shutil.copytree(source, target)
        else:
            target.mkdir()
        row = {"id": set_id, "name": name.strip(), "source_id": source_id,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        registry["labelsets"].append(row)
        _atomic_json(Path(project_dir) / "labelsets.json", registry)
        return row
    except BaseException:
        shutil.rmtree(target.parent, ignore_errors=True)
        raise


def activate_labelset(project_dir: Path, set_id: str) -> dict[str, Any]:
    registry = load_labelsets(project_dir)
    if set_id not in {row["id"] for row in registry["labelsets"]}:
        raise KeyError(set_id)
    root = labelset_root(project_dir, set_id)
    if root.is_symlink() or (root.exists() and any(path.is_symlink() for path in root.rglob("*"))):
        raise ValueError("Label set contains a symbolic link")
    root.mkdir(parents=True, exist_ok=True)
    registry["active_id"] = set_id
    _atomic_json(Path(project_dir) / "labelsets.json", registry)
    return registry

"""Validated, credential-free SSH profiles stored in app user data."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator,model_validator


_STORE_LOCK = threading.RLock()
_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_TARGET_PATTERN = re.compile(r"(?:[A-Za-z_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_SAFE_PATH_PART = re.compile(r"[A-Za-z0-9_.-]+\Z")


def _validate_absolute_remote_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.startswith("/") or path == PurePosixPath("/"):
        raise ValueError("remote_root must be an absolute workspace path")
    if any(part in (".", "..") or not _SAFE_PATH_PART.fullmatch(part) for part in value.split("/")[1:] if part):
        raise ValueError("remote_root contains an unsafe path component")
    if "//" in value or value.endswith("/"):
        raise ValueError("remote_root must be normalized")
    return str(path)


class ComputeProfile(BaseModel):
    """An immutable SSH target. The schema deliberately has no secret fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    id: str
    name: str = Field(min_length=1, max_length=100)
    ssh_target: str
    ssh_port: int = Field(strict=True, ge=1, le=65535)
    remote_root: str
    runtime_kind: Literal["python", "docker"]
    runtime_value: str
    gpu_selector: str | None = None
    memory_budget_mb:int|None=Field(None,strict=True,ge=1,le=1048576)
    allow_sharing:bool=False
    distributed_processes:int=Field(1,strict=True,ge=1,le=16)

    @model_validator(mode='after')
    def valid_allocation(self):
        if self.allow_sharing and not self.memory_budget_mb:raise ValueError('GPU sharing requires an explicit memory budget')
        selected=(self.gpu_selector or '').split(',')
        if self.allow_sharing and (not self.gpu_selector or len(selected)!=1 or self.gpu_selector=='all'):
            raise ValueError('GPU sharing requires one explicit device selector')
        if self.distributed_processes>1:
            if self.allow_sharing:raise ValueError('Distributed training and GPU sharing are separate allocation modes')
            if self.gpu_selector!='all' and len(selected)<self.distributed_processes:raise ValueError('Distributed process count exceeds selected GPU count')
        return self

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _ID_PATTERN.fullmatch(value):
            raise ValueError("id must be a simple identifier")
        return value

    @field_validator("ssh_target")
    @classmethod
    def valid_target(cls, value: str) -> str:
        if not _TARGET_PATTERN.fullmatch(value) or ".." in value:
            raise ValueError("ssh_target must be a host or user@host")
        return value

    @field_validator("remote_root")
    @classmethod
    def valid_remote_root(cls, value: str) -> str:
        return _validate_absolute_remote_path(value)

    @field_validator("runtime_value")
    @classmethod
    def valid_runtime(cls, value: str, info) -> str:
        if info.data.get("runtime_kind") == "docker":
            pattern = r"[A-Za-z0-9][A-Za-z0-9._/:@-]*\Z"
        else:
            pattern = r"(?:/[A-Za-z0-9_.-]+)+\Z|[A-Za-z_][A-Za-z0-9_.-]*\Z"
        if not re.fullmatch(pattern, value) or ".." in value:
            raise ValueError("runtime_value must be a single executable or image identifier")
        return value

    @field_validator("gpu_selector")
    @classmethod
    def valid_gpu_selector(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.,:/-]*\Z", value):
            raise ValueError("gpu_selector contains unsafe characters")
        return value


def default_profile_path() -> Path:
    """Resolve a per-user file; Electron supplies its exact userData directory."""
    override = os.environ.get("VISION_AI_STUDIO_USER_DATA_DIR")
    if override:
        return Path(override).expanduser() / "compute_profiles.json"
    return Path.home() / ".modu_vision" / "compute_profiles.json"


class ProfileStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_profile_path()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"profiles": [], "selected": None}
        with self.path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if not isinstance(raw, dict) or not isinstance(raw.get("profiles"), list):
            raise ValueError("compute profile store is invalid")
        profiles = [ComputeProfile.model_validate(item) for item in raw["profiles"]]
        selected = raw.get("selected")
        if selected is not None and selected not in {profile.id for profile in profiles}:
            raise ValueError("selected compute profile is missing")
        return {"profiles": profiles, "selected": selected}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        def serialize(profile):
            row=profile.model_dump()
            for name,default in {'memory_budget_mb':None,'allow_sharing':False,'distributed_processes':1}.items():
                if row[name]==default:row.pop(name)
            return row
        payload = {
            "profiles": [serialize(profile) for profile in data["profiles"]],
            "selected": data["selected"],
        }
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.path.parent, prefix=".compute_profiles.", delete=False) as handle:
                temporary = handle.name
                os.chmod(temporary, 0o600)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def list(self) -> list[ComputeProfile]:
        with _STORE_LOCK:
            return list(self._read()["profiles"])

    def get(self, profile_id: str) -> ComputeProfile | None:
        with _STORE_LOCK:
            return next((profile for profile in self._read()["profiles"] if profile.id == profile_id), None)

    def save(self, profile: ComputeProfile) -> ComputeProfile:
        with _STORE_LOCK:
            data = self._read()
            profiles = [existing for existing in data["profiles"] if existing.id != profile.id]
            profiles.append(profile)
            data["profiles"] = profiles
            self._write(data)
            return profile

    def delete(self, profile_id: str) -> bool:
        with _STORE_LOCK:
            data = self._read()
            profiles = [profile for profile in data["profiles"] if profile.id != profile_id]
            if len(profiles) == len(data["profiles"]):
                return False
            data["profiles"] = profiles
            if data["selected"] == profile_id:
                data["selected"] = None
            self._write(data)
            return True

    def get_selected(self) -> str | None:
        with _STORE_LOCK:
            return self._read()["selected"]

    def set_selected(self, profile_id: str | None) -> str | None:
        with _STORE_LOCK:
            data = self._read()
            if profile_id is not None and profile_id not in {profile.id for profile in data["profiles"]}:
                raise KeyError(profile_id)
            data["selected"] = profile_id
            self._write(data)
            return profile_id


def get_profile_store() -> ProfileStore:
    """Use the current userData override without holding state across tests or app restarts."""
    return ProfileStore()

"""Declarative extension admission. No imports, scripts or installation run here.

Maintainers supply the reviewed source pins, license decisions and capability
set from outside the manifest. Admission permits only an already reviewed,
statically registered adapter; a manifest cannot turn code into trusted inference.
"""
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Permission = Literal['model.infer', 'source.read', 'result.write', 'artifact.store']
_PERMISSIONS = {
    'model': frozenset({'model.infer', 'source.read'}),
    'input': frozenset({'source.read'}),
    'delivery': frozenset({'result.write'}),
    'storage': frozenset({'artifact.store'}),
}


class ExtensionManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str = Field(pattern=r'^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$', max_length=100)
    version: str = Field(pattern=r'^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?$', max_length=100)
    protocol_version: Literal[1]
    kind: Literal['model', 'input', 'delivery', 'storage']
    adapter_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    license: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9.+-]*$', max_length=100)
    permissions: tuple[Permission, ...] = Field(min_length=1, max_length=4)

    @field_validator('permissions')
    @classmethod
    def distinct_permissions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('Duplicate permission')
        return value


@dataclass(frozen=True)
class ExtensionAdmission:
    allowed: bool
    reasons: tuple[str, ...]
    execution: Literal['reviewed_static_adapter_only', 'refused']


def _reviewed_source_sha256(path):
    import hashlib
    import os
    import stat
    from pathlib import Path
    file = Path(path).absolute()
    if any(item.is_symlink() for item in (file, *file.parents)):
        raise ValueError('Reviewed camera source cannot use linked paths')
    fd = os.open(file, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > 2 * 1024 * 1024:
            raise ValueError('Reviewed camera source exceeds its bounded regular-file contract')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(remaining, 65536))
            if not chunk:
                raise ValueError('Reviewed camera source shortened during verification')
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ValueError('Reviewed camera source grew during verification')
        after, current = os.fstat(fd), file.stat(follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current)
                or any(item.is_symlink() for item in (file, *file.parents))):
            raise ValueError('Reviewed camera source changed during verification')
        return digest.hexdigest()
    finally:
        os.close(fd)


def admit_extension(manifest: ExtensionManifest, *, reviewed_sources: dict[str, str],
                    granted_permissions: set[str], approved_licenses: set[str]) -> ExtensionAdmission:
    reasons = []
    reviewed = reviewed_sources.get(manifest.id)
    if reviewed is None:
        reasons.append('source_not_reviewed')
    elif reviewed != manifest.adapter_sha256:
        reasons.append('source_hash_changed')
    for permission in manifest.permissions:
        if permission not in _PERMISSIONS[manifest.kind]:
            reasons.append('permission_incompatible:' + permission)
        if permission not in granted_permissions:
            reasons.append('permission_not_granted:' + permission)
    if manifest.license not in approved_licenses:
        reasons.append('license_not_approved:' + manifest.license)
    return ExtensionAdmission(not reasons, tuple(reasons),
                              'refused' if reasons else 'reviewed_static_adapter_only')


def create_reviewed_camera(manifest: ExtensionManifest, source_file, factory, source, *,
                           reviewed_sources: dict[str, str], granted_permissions: set[str],
                           approved_licenses: set[str]):
    """Gate a statically imported camera factory before it opens its input.

    The maintainer supplies the source pin, grants and factory; untrusted input
    cannot select an import or callable. This does not sandbox reviewed code.
    Compiled SDKs need a reviewed Python wrapper and their own binary inventory.
    """
    import inspect
    from pathlib import Path
    from backend.engine.camera_adapters import CameraAdapterFactory
    admission = admit_extension(manifest, reviewed_sources=reviewed_sources,
        granted_permissions=granted_permissions, approved_licenses=approved_licenses)
    if not admission.allowed:
        raise ValueError('Extension admission refused: ' + ', '.join(admission.reasons))
    if manifest.kind != 'input' or not isinstance(factory, CameraAdapterFactory):
        raise ValueError('A camera registration requires a reviewed input factory')
    file = Path(source_file)
    actual = inspect.getsourcefile(factory.open)
    if (file.is_symlink() or not file.is_file() or file.stat().st_size > 2 * 1024 * 1024
            or not actual or Path(actual).resolve() != file.resolve()):
        raise ValueError('Reviewed camera source does not match the static factory')
    if _reviewed_source_sha256(file) != manifest.adapter_sha256:
        raise ValueError('Reviewed camera source changed before opening')
    return factory.create(source)

"""Reopening a private fleet must retain data and already-safe metadata."""
import hashlib
import os
from pathlib import Path
from backend.engine.fleet import FleetRegistry


def receipt(path):
    s=path.lstat()
    return (s.st_dev,s.st_ino,s.st_mode,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns,hashlib.sha256(path.read_bytes()).hexdigest())


def test_reopening_private_fleet_preserves_database_bytes_and_metadata(tmp_path):
    registry=FleetRegistry(tmp_path)
    before=receipt(registry.path)
    assert registry.path.stat().st_mode & 0o7777 == 0o600
    assert registry.targets()==[]
    reopened=FleetRegistry(tmp_path)
    assert reopened.targets()==[]
    assert receipt(registry.path)==before


def test_reopening_fleet_restricts_existing_excess_permissions(tmp_path):
    registry=FleetRegistry(tmp_path)
    original=hashlib.sha256(registry.path.read_bytes()).hexdigest()
    registry.path.chmod(0o644)
    reopened=FleetRegistry(tmp_path)
    assert reopened.path.stat().st_mode & 0o7777 == 0o600
    assert hashlib.sha256(reopened.path.read_bytes()).hexdigest()==original
    assert reopened.targets()==[]

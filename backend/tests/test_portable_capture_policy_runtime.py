"""Fresh packages must validate capture policy and start without a host backend."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from backend.engine import flow_package
from backend.engine.flowchart_engine import FlowchartPipeline, get_single_detection_flowchart


POLICY = {
    "revision": 3,
    "policy": {
        "required_view_ids": ["back", "front"],
        "timestamp_basis": "trigger_offset",
        "max_skew_ms": 20,
        "deadline_ms": 1000,
        "late_window_ms": 1000,
        "completeness_policy": "all_required",
    },
}

PROBE = r'''
import asyncio, json, sys
from pathlib import Path
sys.dont_write_bytecode = True
package, state, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
sys.path.insert(0, str(package))
import backend
assert Path(backend.__file__).resolve().is_relative_to(package)
from backend.engine.flow_package_runtime import verify_flow_package
from pydantic import ValidationError
if mode == 'bad_helper':
    try:
        verify_flow_package(package)
    except ValueError as error:
        assert 'checksum' in str(error).lower()
    else:
        raise AssertionError('Changed packaged helper was accepted')
    assert not state.exists()
    result = {'changed_helper_refused': True}
elif mode == 'invalid_policy':
    try:
        verify_flow_package(package)
    except ValidationError as error:
        assert any(row['loc'] == ('capture_group_policy',) for row in error.errors())
    else:
        raise AssertionError('Malformed packaged capture policy was accepted')
    from backend.engine.inspection_service import create_service_app
    try:
        create_service_app(package, state, token='owned-fixture-token', auto_worker=False)
    except ValidationError:
        pass
    else:
        raise AssertionError('Service accepted malformed capture policy')
    assert not state.exists(), 'Refused policy must not initialize mutable service state'
    result = {'invalid_policy_refused': True}
else:
    from backend.engine.flowchart_engine import FlowchartPipeline
    data = json.loads((package / 'pipeline.json').read_text(encoding='utf-8'))
    direct = FlowchartPipeline.model_validate(data)
    pipeline, checkpoints = verify_flow_package(package)
    assert direct.capture_group_policy == pipeline.capture_group_policy
    from backend.engine.inspection_service import create_service_app
    import httpx
    async def startup():
        app = create_service_app(package, state, token='owned-fixture-token', auto_worker=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned-fixture') as client:
                assert (await client.get('/v1/capture-groups')).status_code == 401
                response = await client.get('/v1/capture-groups', headers={'X-Vision-Token': 'owned-fixture-token'})
                assert response.status_code == 200
                policy = response.json()['policy']
                ready = await client.get('/v1/readiness', headers={'X-Vision-Token': 'owned-fixture-token'})
                assert ready.json()['worker_started'] is False
        reopened = create_service_app(package, state, token='owned-fixture-token', auto_worker=False)
        async with reopened.router.lifespan_context(reopened):
            assert reopened.state.inspection_store.capture_group_status()['policy'] == policy
        return policy
    persisted = asyncio.run(startup())
    result = {'pipeline_policy': pipeline.capture_group_policy, 'persisted_policy': persisted,
              'checkpoint_jobs': sorted(checkpoints), 'worker_started': False}
for name, module in tuple(sys.modules.items()):
    if name == 'backend' or name.startswith('backend.'):
        origin = getattr(module, '__file__', None)
        if origin:
            assert Path(origin).resolve().is_relative_to(package), (name, origin)
result['loaded_backend_modules'] = sorted(name for name in sys.modules if name.startswith('backend.'))
print(json.dumps(result))
'''


def build_package(tmp_path: Path, *, capture_policy: bool = True) -> Path:
    checkpoint = tmp_path / "job_detector" / "best_model.pt"
    checkpoint.parent.mkdir(exist_ok=True)
    # Startup verifies bytes and policy without loading or executing a model.
    checkpoint.write_bytes(b"owned detector checkpoint fixture")
    data = get_single_detection_flowchart(job_id="job_detector").model_dump()
    if capture_policy:
        data["capture_group_policy"] = POLICY
    result = flow_package.build_flow_package(
        pipeline=FlowchartPipeline.model_validate(data),
        checkpoints={"job_detector": checkpoint},
        output_base_dir=tmp_path / "exports",
        package_name="portable_capture",
    )
    return Path(result["package_path"])


def isolated_probe(package: Path, tmp_path: Path, mode: str = "valid") -> dict:
    cwd = tmp_path / "non_repository_cwd"
    cwd.mkdir(exist_ok=True)
    environment = dict(os.environ, PYTHONPATH="")
    result = subprocess.run(
        [sys.executable, "-I", "-c", PROBE, str(package), str(tmp_path / "owned_state"), mode],
        cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_fresh_package_reloads_policy_and_starts_owned_service_without_host_imports(tmp_path: Path):
    package = build_package(tmp_path)
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    remote_rows = {row["path"]: row for row in manifest["files"] if row["path"].startswith("backend/remote/")}
    assert set(remote_rows) == {"backend/remote/__init__.py", "backend/remote/file_replace.py"}
    source_root = Path(flow_package.__file__).resolve().parents[2]
    for relative, row in remote_rows.items():
        expected = (source_root / relative).read_bytes()
        assert (package / relative).read_bytes() == expected
        assert row["sha256"] == hashlib.sha256(expected).hexdigest()
        assert row["size"] == len(expected)
    result = isolated_probe(package, tmp_path)
    assert result["pipeline_policy"] == POLICY
    assert result["persisted_policy"] == POLICY
    assert result["checkpoint_jobs"] == ["job_detector"]
    assert result["worker_started"] is False
    assert "backend.remote.file_replace" in result["loaded_backend_modules"]


def test_isolated_package_refuses_hash_bound_malformed_policy_before_state_creation(tmp_path: Path):
    package = build_package(tmp_path)
    pipeline_path = package / "pipeline.json"
    data = json.loads(pipeline_path.read_text(encoding="utf-8"))
    data["capture_group_policy"]["revision"] = 0
    pipeline_path.write_text(json.dumps(data), encoding="utf-8")
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = next(row for row in manifest["files"] if row["path"] == "pipeline.json")
    row.update(size=pipeline_path.stat().st_size, sha256=hashlib.sha256(pipeline_path.read_bytes()).hexdigest())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert isolated_probe(package, tmp_path, "invalid_policy")["invalid_policy_refused"]


@pytest.mark.parametrize("name", ["__init__.py", "file_replace.py"])
def test_changed_portable_helper_refuses_package_verification(tmp_path: Path, name: str):
    package = build_package(tmp_path)
    helper = package / "backend" / "remote" / name
    helper.write_bytes(helper.read_bytes() + b"\n# changed after export\n")
    assert isolated_probe(package, tmp_path, "bad_helper")["changed_helper_refused"]


@pytest.mark.parametrize("unsafe", ["missing_file", "file_link", "directory_link", "copy_drift"])
def test_portable_dependency_source_refusal_publishes_no_package(tmp_path: Path, monkeypatch, unsafe: str):
    source_root = Path(flow_package.__file__).resolve().parents[2]
    copied = tmp_path / "owned_source"
    for directory in ("backend/engine", "backend/remote", "native_runtime", "examples"):
        shutil.copytree(source_root / directory, copied / directory,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    remote = copied / "backend" / "remote"
    helper = remote / "file_replace.py"
    if unsafe == "missing_file":
        helper.unlink()
    elif unsafe == "file_link":
        outside = tmp_path / "outside_helper.py"
        helper.replace(outside)
        helper.symlink_to(outside)
    elif unsafe == "directory_link":
        outside = tmp_path / "outside_remote"
        remote.rename(outside)
        remote.symlink_to(outside, target_is_directory=True)
    else:
        copy = flow_package.shutil.copyfile
        def changed_copy(source, destination, **kwargs):
            result = copy(source, destination, **kwargs)
            if Path(source) == helper:
                helper.write_bytes(helper.read_bytes() + b"\n# source drift during copy\n")
            return result
        monkeypatch.setattr(flow_package.shutil, "copyfile", changed_copy)
    monkeypatch.setattr(flow_package, "__file__", str(copied / "backend" / "engine" / "flow_package.py"))
    with pytest.raises(ValueError, match="(?i)portable.*source"):
        build_package(tmp_path, capture_policy=False)
    assert list((tmp_path / "exports").iterdir()) == []

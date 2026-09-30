"""Standard-library bootstrap for a declared CPU Edge flow deployment.

Keep this module free of third-party imports: target and integrity failures must
be readable even on a machine where the inspection dependencies are absent.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import venv
from typing import Any


SUPPORTED_TARGETS = {
    "linux": ("x86_64", "arm64"),
    "windows": ("x86_64",),
    "macos": ("arm64",),
}

_IMPORT_NAMES = {
    "Pillow": "PIL", "opencv-python-headless": "cv2", "scikit-learn": "sklearn",
}


def normalize_target(target_os: str | None, target_arch: str | None) -> dict[str, str]:
    os_name = str(target_os or "").lower()
    arch = str(target_arch or "").lower()
    arch = {"amd64": "x86_64", "aarch64": "arm64"}.get(arch, arch)
    if os_name not in SUPPORTED_TARGETS or arch not in SUPPORTED_TARGETS[os_name]:
        choices = ", ".join(f"{name}/{cpu}" for name, cpus in SUPPORTED_TARGETS.items() for cpu in cpus)
        raise ValueError(f"Unsupported CPU Edge target {os_name or '(missing)'}/{arch or '(missing)'}. Supported declarations: {choices}")
    return {"os": os_name, "architecture": arch}


def create_edge_profile(target_os: str | None, target_arch: str | None, requirements: str) -> dict[str, Any]:
    dependencies = []
    for line in requirements.splitlines():
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)>=([0-9]+(?:\.[0-9]+)*)", line.strip())
        if not match:
            raise ValueError("CPU Edge requirements must declare distribution minimum versions")
        dependencies.append({"distribution": match[1], "minimum": match[2]})
    return {
        "schema_version": 1, "profile": "edge_cpu", "device": "cpu",
        "target": normalize_target(target_os, target_arch),
        "python": {"minimum": "3.10", "maximum_exclusive": "3.14"},
        "dependencies": dependencies,
        "entrypoints": {"install": "edge.py install", "preflight": "edge.py preflight",
                        "run": "edge.py run", "serve": "edge.py serve"},
    }


def _file(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Invalid CPU Edge package file path")
    pieces = PurePosixPath(relative)
    if pieces.is_absolute() or any(piece in ("", ".", "..") for piece in relative.split("/")):
        raise ValueError("Invalid CPU Edge package file path")
    path = root.joinpath(*pieces.parts)
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if root in parent.parents):
        raise ValueError(f"CPU Edge package file is a symbolic link: {relative}")
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"CPU Edge package file is missing: {relative}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_profile(root: Path) -> dict[str, Any]:
    manifest = json.loads(_file(root, "manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("Unsupported CPU Edge package manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Incomplete CPU Edge package manifest")
    seen = set()
    for row in files:
        if not isinstance(row, dict) or not isinstance(row.get("path"), str) or row["path"] in seen:
            raise ValueError("Duplicate or invalid CPU Edge package file entry")
        path = _file(root, row["path"])
        if path.stat().st_size != row.get("size") or _sha256(path) != row.get("sha256"):
            raise ValueError(f"CPU Edge package checksum mismatch: {row['path']}")
        seen.add(row["path"])
    required = {"edge.py", "edge_deployment.json", "requirements.txt", "pipeline.json", "run_flow.py", "serve_flow.py",
                "backend/engine/edge_runtime.py"}
    if not required.issubset(seen):
        raise ValueError("Incomplete CPU Edge package bootstrap")
    # An unlisted Python module could shadow one of the verified bundled imports.
    if any(path.relative_to(root).as_posix() not in seen for path in (root / "backend").rglob("*.py")):
        raise ValueError("CPU Edge package contains unlisted runtime code")
    profile = json.loads((root / "edge_deployment.json").read_text(encoding="utf-8"))
    if not isinstance(profile, dict) or profile != manifest.get("deployment"):
        raise ValueError("CPU Edge deployment profile does not match the package manifest")
    expected = create_edge_profile(profile.get("target", {}).get("os"), profile.get("target", {}).get("architecture"),
                                   (root / "requirements.txt").read_text(encoding="utf-8"))
    if profile != expected:
        raise ValueError("Unsupported CPU Edge deployment profile")
    return profile


def _check_host(profile: dict[str, Any]) -> None:
    os_name = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system(), platform.system().lower())
    host = normalize_target(os_name, platform.machine())
    if host != profile["target"]:
        raise ValueError(f"CPU Edge target {profile['target']['os']}/{profile['target']['architecture']} does not match host {host['os']}/{host['architecture']}. Export for this host; emulation and cross-install are unsupported.")
    if not (3, 10) <= tuple(sys.version_info[:2]) < (3, 14):
        raise ValueError("CPU Edge requires Python >=3.10,<3.14; select a supported interpreter before installation")


def _check_dependencies(profile: dict[str, Any]) -> dict[str, str]:
    versions, errors = {}, []
    for dependency in profile["dependencies"]:
        name, minimum = dependency["distribution"], dependency["minimum"]
        try:
            version = metadata.version(name)
            digits = re.match(r"\d+(?:\.\d+)*", version)
            installed = tuple(int(value) for value in digits[0].split(".")) if digits else ()
            required = tuple(int(value) for value in minimum.split("."))
            # Pad a release such as 6.0 to compare with 6.0.0 correctly.
            width = max(len(installed), len(required))
            if installed + (0,) * (width - len(installed)) < required + (0,) * (width - len(required)):
                errors.append(f"{name}>={minimum} required, found {version}")
            versions[name] = version
        except metadata.PackageNotFoundError:
            errors.append(f"{name}>={minimum} is missing")
    if errors:
        raise ValueError("CPU Edge dependencies are incompatible: " + "; ".join(errors) + ". Run edge.py install and use its environment.")
    for dependency in profile["dependencies"]:
        name = dependency["distribution"]
        try:
            importlib.import_module(_IMPORT_NAMES.get(name, name.replace("-", "_")))
        except Exception as exc:
            raise ValueError(f"CPU Edge dependency {name} cannot load ({exc}); reinstall compatible wheels with edge.py install") from exc
    # A CPU tensor also rejects a broken PyTorch native runtime before model loading.
    torch = importlib.import_module("torch")
    try:
        torch.zeros(1, device="cpu").add_(1)
    except Exception as exc:
        raise ValueError(f"CPU Edge PyTorch CPU runtime is unusable: {exc}") from exc
    return versions


def preflight_edge_package(package_dir: Path, *, check_dependencies: bool = True) -> dict[str, Any]:
    root = Path(package_dir).expanduser().resolve()
    profile = _load_profile(root)
    _check_host(profile)
    versions = _check_dependencies(profile) if check_dependencies else None
    if check_dependencies:
        runtime = importlib.import_module("backend.engine.flow_package_runtime")
        runtime.verify_flow_package(root)
    return {"status": "ready" if check_dependencies else "verified", "profile": "edge_cpu", "device": "cpu",
            "target": profile["target"], "python": platform.python_version(),
            "manifest_sha256": _sha256(root / "manifest.json"),
            "dependencies": versions, "dependencies_checked": check_dependencies}


def enforce_edge_device(package_dir: Path, device: str) -> None:
    """Apply the declaration to direct runner/service calls as well as the CLI."""
    root = Path(package_dir).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if "deployment" not in manifest:
        return
    if device != "cpu":
        raise ValueError("CPU Edge deployment only supports device cpu; GPU fallback and overrides are disabled")
    preflight_edge_package(root, check_dependencies=True)


def install_edge_package(package_dir: Path, environment: Path) -> dict[str, Any]:
    root = Path(package_dir).resolve()
    checked = preflight_edge_package(root, check_dependencies=False)
    target = Path(environment).expanduser()
    if target.is_symlink() or any(parent.is_symlink() for parent in target.parents):
        raise ValueError("CPU Edge environment cannot be a symbolic link")
    if target.exists():
        raise ValueError("CPU Edge environment already exists; select a new --venv directory")
    target = target.resolve()
    venv.EnvBuilder(with_pip=True).create(target)
    python = target / ("Scripts/python.exe" if checked["target"]["os"] == "windows" else "bin/python")
    try:
        # Linux/Windows explicitly install CPU wheels; macOS has no CUDA wheel.
        torch_args = [str(python), "-m", "pip", "install", "torch>=2.4", "torchvision>=0.19"]
        if checked["target"]["os"] in ("linux", "windows"):
            torch_args.extend(("--index-url", "https://download.pytorch.org/whl/cpu"))
        subprocess.run(torch_args, check=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
        subprocess.run([str(python), str(root / "edge.py"), "preflight"], check=True)
    except subprocess.CalledProcessError as exc:
        raise ValueError("CPU Edge installation failed. A compatible Python/PyTorch CPU wheel and dependency wheels are required for the declared target. Inspect pip output, resolve access or wheel support, and retry with a new --venv directory.") from exc
    return {"status": "installed", "environment": str(target), "python": str(python), "device": "cpu",
            "preflight_command": [str(python), str(root / "edge.py"), "preflight"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Install, verify, and run a generic CPU Edge flow package")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install", help="Create a fresh isolated environment and install dependencies")
    install.add_argument("--venv", type=Path)
    check = sub.add_parser("preflight", help="Verify target, Python, integrity, and usable dependencies")
    check.add_argument("--skip-dependencies", action="store_true", help="Check integrity/target only; does not establish runtime readiness")
    for name in ("run", "serve"):
        launch = sub.add_parser(name, help="Run the bundled flow or persistent HTTP service on CPU")
        launch.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "install":
            result = install_edge_package(root, args.venv or root / ".edge_venv")
        elif args.command == "preflight":
            result = preflight_edge_package(root, check_dependencies=not args.skip_dependencies)
        else:
            preflight_edge_package(root)
            arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            # Passing another package would bypass this package's declared target.
            if any(arg in ("--device", "--package") or arg.startswith(("--device=", "--package=")) for arg in arguments):
                raise ValueError("CPU Edge launch fixes package and device cpu; do not pass --device or --package")
            runner = "run_flow.py" if args.command == "run" else "serve_flow.py"
            return subprocess.run([sys.executable, str(root / runner), "--device", "cpu", *arguments],
                                  env={**os.environ, "PYTHONPATH": ""}, check=False).returncode
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, f"CPU Edge error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())

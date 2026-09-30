#!/usr/bin/env python3
"""
scripts/build_backend_binary.py

Compiles the entire Vision AI Studio Python backend into a standalone native executable
(vision_ai_backend.exe on Windows, vision_ai_backend on macOS/Linux).

Benefits:
  - Bundles the configured Python runtime and its detected dependencies.
  - Optional model, hardware and SDK dependencies require separate target validation.
  - Native Integration: Electron supervisor automatically detects and executes this binary.

Usage:
  pip install pyinstaller
  python scripts/build_backend_binary.py
"""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
OUTPUT_DIR = ROOT_DIR / "dist-backend"
ENTRY_POINT = BACKEND_DIR / "main.py"

NATIVE_EXPORT_SOURCES = (
    "build_native.py", "vision_runtime.h", "vision_runtime.hpp", "vision_runtime.cpp",
    "predict.cpp", "execute.cpp", "VisionRuntime.cs", "VisionRuntime.csproj",
    "CMakeLists.txt", "README.md",
)
HTTP_EXPORT_CLIENTS = ("inspection-service-client.mjs", "InspectionServiceClient.cs")


def export_resource_files(root: Path) -> list[tuple[Path, str]]:
    """Preserve the source tree read by flow/GAN export inside the frozen root."""
    root = Path(root)
    resources = [(root / "native_runtime" / name, "native_runtime") for name in NATIVE_EXPORT_SOURCES]
    resources.extend((root / "examples" / name, "examples") for name in HTTP_EXPORT_CLIENTS)
    engine = root / "backend" / "engine"
    sources = sorted(source for source in engine.rglob("*.py")
                     if not {"tests", "__pycache__", ".pytest_cache"}.intersection(source.relative_to(engine).parts))
    if not sources:
        raise ValueError("Flow export Python engine sources are missing")
    resources.extend((source, (Path("backend/engine") / source.relative_to(engine).parent).as_posix()) for source in sources)
    for source, _ in resources:
        if not source.is_file() or source.is_symlink() or any(parent.is_symlink() for parent in source.parents if parent != root and root in parent.parents):
            raise ValueError(f"Missing or unsafe export resource: {source.relative_to(root)}")
    return resources

def check_pyinstaller():
    try:
        import PyInstaller
        return True
    except ImportError:
        return False

def build_binary():
    print("=" * 70)
    print(" Vision AI Studio: Python Backend Native Binary Compiler")
    print("=" * 70)
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Target Entry: {ENTRY_POINT}")
    print(f"Output Directory: {OUTPUT_DIR}")

    if not check_pyinstaller():
        print("\n[ERROR] PyInstaller is not installed in the current Python environment.")
        print("Please install PyInstaller first by running:")
        print("    pip install pyinstaller")
        sys.exit(1)

    resources = export_resource_files(ROOT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    is_win = platform.system() == "Windows"
    exe_name = "vision_ai_backend.exe" if is_win else "vision_ai_backend"

    # PyInstaller Arguments
    cmd = [
        sys.executable,
        "-m", "PyInstaller",
        "--name", "vision_ai_backend",
        "--onedir" if is_win else "--onefile", # onedir is significantly faster to load with PyTorch on Windows
        "--clean",
        "--noconfirm",
        f"--distpath={OUTPUT_DIR}",
        f"--workpath={ROOT_DIR / 'build' / 'pyinstaller_work'}",
        f"--specpath={ROOT_DIR / 'build'}",
        # Hidden imports critical for FastAPI / PyTorch / Uvicorn reflection
        "--hidden-import=uvicorn.logging",
        "--hidden-import=uvicorn.loops",
        "--hidden-import=uvicorn.loops.auto",
        "--hidden-import=uvicorn.protocols",
        "--hidden-import=uvicorn.protocols.http",
        "--hidden-import=uvicorn.protocols.http.auto",
        "--hidden-import=uvicorn.protocols.websockets",
        "--hidden-import=uvicorn.protocols.websockets.auto",
        "--hidden-import=uvicorn.lifespans",
        "--hidden-import=uvicorn.lifespans.on",
        "--hidden-import=uvicorn.lifespans.off",
        "--hidden-import=fastapi",
        "--hidden-import=pydantic",
        "--hidden-import=starlette",
        "--hidden-import=multipart",
        "--hidden-import=python_multipart",
        "--hidden-import=torch",
        "--hidden-import=torchvision",
        "--hidden-import=cv2",
        "--hidden-import=PIL",
        "--hidden-import=sklearn",
        "--hidden-import=engine",
        "--hidden-import=api",
        "--hidden-import=utils",
        f"--paths={BACKEND_DIR}",
    ]
    separator = ";" if is_win else ":"
    for source, destination in resources:
        cmd.extend(["--add-data", f"{source}{separator}{destination}"])
    cmd.append(str(ENTRY_POINT))

    print("\n[INFO] Executing PyInstaller command:")
    print(" ".join(cmd))
    print("\nCompiling... (this may take a few minutes for PyTorch symbols)\n")

    res = subprocess.run(cmd, cwd=str(ROOT_DIR))
    if res.returncode != 0:
        print(f"\n[ERROR] PyInstaller compilation failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    print("\n" + "=" * 70)
    print(" Compilation Successful!")
    print(f" Compiled Binary Output: {OUTPUT_DIR}")
    print("=" * 70)

if __name__ == "__main__":
    build_binary()

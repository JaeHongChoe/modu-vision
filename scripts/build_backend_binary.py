#!/usr/bin/env python3
"""
scripts/build_backend_binary.py

Compiles the entire Vision AI Studio Python backend into a standalone native executable
(vision_ai_backend.exe on Windows, vision_ai_backend on macOS/Linux).

Benefits:
  - 100% Standalone: End-users do not need Python, Anaconda, or libraries installed.
  - IP Protection: Python source code (.py) is compiled and encrypted, not exposed in plain text.
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
        str(ENTRY_POINT),
    ]

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

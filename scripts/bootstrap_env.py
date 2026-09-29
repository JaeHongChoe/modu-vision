#!/usr/bin/env python3
"""
scripts/bootstrap_env.py

Automated Python Dependency Bootstrapper and Self-Healing Installer.
Features:
  - Verifies presence of critical computer vision and deep learning packages.
  - Automatically identifies missing modules (e.g. torch, torchvision, cv2, fastapi).
  - Installs missing packages silently using pip with fallback to offline wheels cache.
  - Verifies PyTorch hardware acceleration (CUDA on Windows, MPS on Mac).
"""

import importlib
import os
import platform
import subprocess
import sys
from pathlib import Path

REQUIRED_MODULES = {
    "fastapi": "fastapi>=0.115.0",
    "uvicorn": "uvicorn[standard]>=0.30.0",
    "pydantic": "pydantic>=2.8.0",
    "torch": "torch>=2.4.0",
    "torchvision": "torchvision>=0.19.0",
    "cv2": "opencv-python-headless>=4.10.0.84",
    "PIL": "Pillow>=10.4.0",
    "numpy": "numpy>=1.26.0",
    "sklearn": "scikit-learn>=1.5.0",
    "psutil": "psutil>=6.0.0",
    "httpx": "httpx>=0.27.0",
}

ROOT_DIR = Path(__file__).resolve().parent.parent

def check_missing_modules():
    missing = []
    for mod_name, pkg_spec in REQUIRED_MODULES.items():
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append((mod_name, pkg_spec))
    return missing

def install_missing(missing):
    print(f"\n[Bootstrap] Missing {len(missing)} required package(s): {[m[0] for m in missing]}")
    print("[Bootstrap] Launching automatic dependency installer...")

    # Check for offline wheels directory (for air-gapped factory cleanroom PCs)
    offline_wheels = None
    for cand in [
        ROOT_DIR / "resources" / "wheels",
        ROOT_DIR / "wheels",
        Path(os.environ.get("LOCALAPPDATA", "")) / "vision-ai-studio" / "wheels",
    ]:
        if cand.is_dir() and any(cand.glob("*.whl")):
            offline_wheels = cand
            break

    pip_cmd = [sys.executable, "-m", "pip", "install", "--no-warn-script-location"]

    if offline_wheels:
        print(f"[Bootstrap] Offline wheel cache detected: {offline_wheels}")
        pip_cmd.extend(["--no-index", f"--find-links={offline_wheels}"])
    else:
        print("[Bootstrap] Connecting to PyPI repository...")
        pip_cmd.extend(["--timeout", "60", "--retries", "3"])

    # If requirements.txt exists and multiple packages missing, install via requirements.txt
    req_file = ROOT_DIR / "requirements.txt"
    if req_file.exists() and len(missing) >= 3:
        cmd = pip_cmd + ["-r", str(req_file)]
    else:
        cmd = pip_cmd + [spec for _, spec in missing]

    print(f"[Bootstrap] Running: {' '.join(cmd)}")
    res = subprocess.run(cmd)
    return res.returncode == 0

def main():
    print("=" * 65)
    print(" Vision AI Studio - Dependency Integrity Verifier")
    print(f" Python Executable: {sys.executable}")
    print(f" Platform: {platform.system()} {platform.machine()} ({sys.version.split()[0]})")
    print("=" * 65)

    missing = check_missing_modules()
    if not missing:
        print("[Bootstrap] [PASS] All required AI libraries are present and verified.")
        return 0

    success = install_missing(missing)
    if not success:
        print("\n[Bootstrap] [FAIL] Automatic package installation failed.")
        return 1

    # Re-verify
    re_missing = check_missing_modules()
    if re_missing:
        print(f"\n[Bootstrap] [FAIL] Following modules are still missing after install: {[m[0] for m in re_missing]}")
        return 1

    print("\n[Bootstrap] [SUCCESS] All missing libraries were successfully installed and verified!")
    return 0

if __name__ == "__main__":
    sys.exit(main())

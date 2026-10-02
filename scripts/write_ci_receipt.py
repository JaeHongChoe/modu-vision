"""Write source/toolchain identity and observed test counts, without release claims."""
import argparse
import hashlib
import json
import os
import platform
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command_version(command):
    try:
        return subprocess.check_output(command, cwd=ROOT, text=True, encoding='utf-8', errors='replace',
                                       stderr=subprocess.DEVNULL, timeout=15).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def test_counts(path):
    if not path.is_file():
        return {"status": "not_recorded"}
    tree = ET.parse(path).getroot()
    suites = [tree] if tree.tag == "testsuite" else list(tree.iter("testsuite"))
    counts = {key: sum(int(suite.get(key, "0")) for suite in suites) for key in ("tests", "failures", "errors", "skipped")}
    counts["status"] = "failed" if counts["failures"] or counts["errors"] else "recorded"
    return counts


def receipt(root, junit):
    root = Path(root)
    lockfiles = ["package-lock.json", "build/ci/requirements-ubuntu-py313-cpu.lock", "build/ci/requirements-windows-py313-cpu.lock"]
    inputs = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in lockfiles if (root / name).is_file()}
    electron = root / "node_modules/electron/package.json"
    return {
        "receipt": "SourceCIManifest/v1",
        "source_sha": command_version(["git", "rev-parse", "HEAD"]),
        "platform": {"system": platform.system(), "release": platform.release(), "machine": platform.machine()},
        "toolchain": {"python": platform.python_version(), "node": command_version(["node", "--version"]),
                      "electron": json.loads(electron.read_text(encoding='utf-8'))["version"] if electron.is_file() else None},
        "input_sha256": inputs,
        "pytest": test_counts(Path(junit)),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "gates_not_covered": ["Windows 11 installer", "signing", "real hardware", "model quality approval", "public binary distribution"],
        "scope": "source CI execution; test records and skip reasons require review; not release acceptance",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--junit", required=True, type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(receipt(ROOT, args.junit), indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

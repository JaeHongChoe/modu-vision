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
import argparse
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = ROOT_DIR / "backend"
OUTPUT_DIR = ROOT_DIR / "dist-backend"
ENTRY_POINT = ROOT_DIR / "scripts" / "frozen_backend_entry.py"

# Names are distribution/module pairs because wheel and import names differ.
DEPENDENCIES = (
    ('fastapi', 'fastapi', True), ('uvicorn', 'uvicorn', True),
    ('pydantic', 'pydantic', True), ('python-multipart', 'python_multipart', True),
    ('httpx', 'httpx', True), ('psutil', 'psutil', True), ('numpy', 'numpy', True),
    ('Pillow', 'PIL', True), ('opencv-python-headless', 'cv2', True),
    ('scikit-learn', 'sklearn', True), ('torch', 'torch', True),
    ('torchvision', 'torchvision', True), ('timm', 'timm', True),
    ('safetensors', 'safetensors', True), ('huggingface-hub', 'huggingface_hub', True),
    ('ultralytics', 'ultralytics', True), ('onnxruntime', 'onnxruntime', True),
    ('PyYAML', 'yaml', True), ('typing_extensions', 'typing_extensions', True),
    ('transformers', 'transformers', False), ('pydicom', 'pydicom', False),
    ('openvino', 'openvino', False), ('nncf', 'nncf', False),
    ('nvidia-ml-py', 'pynvml', False),
)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as reader:
        for chunk in iter(lambda: reader.read(1024 * 1024), b''): digest.update(chunk)
    return digest.hexdigest()


def dependency_inventory(root: Path) -> dict:
    from packaging.requirements import Requirement
    root = Path(root)
    normalize=lambda name:name.lower().replace('_','-')
    requirements={}
    for line in (root/'requirements.txt').read_text(encoding='utf-8').splitlines():
        value=line.strip()
        if value and not value.startswith('#'):
            requirement=Requirement(value);requirements[normalize(requirement.name)]=requirement
    dependencies = []
    for distribution, module, required in DEPENDENCIES:
        available = importlib.util.find_spec(module) is not None
        try: version = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            providers = importlib.metadata.packages_distributions().get(module, [])
            version = importlib.metadata.version(providers[0]) if providers else None
        declared=requirements.get(normalize(distribution))
        dependencies.append({'distribution': distribution, 'module': module,
                             'version': version, 'available': available, 'required': required,
                             'requirement':str(declared) if declared else None,
                             'satisfies_requirement':bool(version and (declared is None or declared.specifier.contains(version)))})
    sources = sorted(path for path in (root / 'backend').rglob('*.py')
                     if not {'tests', '__pycache__', '.pytest_cache'}.intersection(path.parts))
    resources = sorted(set(path for path, _ in export_resource_files(root)) | set(sources)
                       | {root / 'scripts' / 'frozen_backend_entry.py',root/'requirements.txt',
                          root/'scripts/package_license_texts.py',root/'scripts/license_inventory.py'})
    inventory = {'schema_version': 1, 'platform': platform.system(), 'architecture': platform.machine(),
                 'python_version': platform.python_version(), 'dependencies': dependencies,
                 'compiler': {'name': 'PyInstaller', 'version': importlib.metadata.version('pyinstaller') if check_pyinstaller() else None},
                 'resources': [{'path': str(path.relative_to(root)), 'sha256': sha256(path)} for path in resources],
                 'offline': {'pretrained_weights_bundled': False,
                             'network_downloads_required_for_uncached_training': True,
                             'inference_requires_exported_package_weights': True,
                             'hardware_prerequisites': ['CUDA driver for CUDA targets', 'Compatible camera/PLC SDK and device permissions'],
                             'optional_features_unavailable': [row['module'] for row in dependencies if not row['required'] and not row['available']]}}
    inventory['build_identity_sha256'] = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return inventory


def validate_inventory(inventory):
    missing=[row['module'] for row in inventory['dependencies'] if row['required'] and not row['available']]
    outdated=[row['module'] for row in inventory['dependencies'] if row['required'] and row['available'] and not row['satisfies_requirement']]
    if missing:raise RuntimeError('Build environment lacks required dependencies: '+', '.join(missing))
    if outdated:raise RuntimeError('Build environment does not satisfy declared requirements: '+', '.join(outdated))


def snapshot_sources(root: Path, destination: Path, resources: list[dict]) -> Path:
    """Freeze from one checksum-bound source revision while the checkout is editable."""
    root, destination = Path(root).resolve(), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for row in resources:
        source = root / row['path'];target = destination / row['path']
        if not source.resolve().is_relative_to(root) or source.is_symlink() or not target.resolve().is_relative_to(destination.resolve()):
            raise ValueError('Unsafe frozen source path')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source,target,follow_symlinks=False)
        if sha256(target) != row['sha256']: raise ValueError('Source changed during frozen build snapshot: ' + row['path'])
    return destination


def pyinstaller_command(root: Path, output: Path, target: str) -> list[str]:
    root, output = Path(root), Path(output)
    if target not in ('Windows', 'Darwin', 'Linux'): raise ValueError('Unsupported release platform')
    command = [sys.executable, '-m', 'PyInstaller', '--name', 'vision_ai_backend', '--onedir',
               '--clean', '--noconfirm', f'--distpath={output}',
               f'--workpath={output / ".build" / "work"}', f'--specpath={output / ".build"}',
               f'--paths={root}', f'--paths={root / "backend"}',
               '--collect-all=timm', '--collect-all=ultralytics', '--collect-all=onnxruntime',
               '--collect-all=safetensors', '--collect-all=huggingface_hub']
    # Global scientific environments may contain notebook/GUI/browser tooling.
    # They are not backend dependencies and can drag multi-GB SDKs into hooks.
    for module in ('IPython','notebook','jupyterlab','nbconvert','PyQt5','PyQt6','PySide2','PySide6',
                   'playwright','altair','bokeh','streamlit','tensorflow','keras','pytest'):
        command.append('--exclude-module='+module)
    for module in ('uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                   'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'python_multipart',
                   'torch', 'torchvision', 'cv2', 'PIL', 'sklearn', 'psutil', 'yaml'):
        command.append('--hidden-import=' + module)
    # Dynamic API imports and service child entry points must survive freezing.
    for source in sorted((root / 'backend').rglob('*.py')):
        if {'tests', '__pycache__', '.pytest_cache'}.intersection(source.parts): continue
        parts = list(source.relative_to(root).with_suffix('').parts)
        if parts[-1] == '__init__': parts.pop()
        command.append('--hidden-import=' + '.'.join(parts))
    separator = ';' if target == 'Windows' else ':'
    for source, destination in export_resource_files(root):
        command.extend(['--add-data', f'{source}{separator}{destination}'])
    command.extend(['--add-data', f'{output / ".build" / "backend-build-inventory.json"}{separator}.'])
    command.append(str(root / 'scripts' / 'frozen_backend_entry.py'))
    return command


# Public API documented at https://pyinstaller.org/en/stable/usage.html#running-pyinstaller-from-python-code.
# Keep the process command fixed-size as dynamic imports/resources grow.
_PYINSTALLER_RUNNER = (
    "import json, sys\n"
    "from PyInstaller.__main__ import run\n"
    "with open(sys.argv[1], encoding='utf-8') as stream:\n"
    "    arguments = json.load(stream)\n"
    "run(arguments)\n"
)


def pyinstaller_invocation(command: list[str], build_dir: Path) -> list[str]:
    """Preserve every compiler argument without putting it in CreateProcess's argv."""
    import uuid
    if command[1:3] != ['-m', 'PyInstaller']:
        raise ValueError('Expected the PyInstaller module command')
    arguments_path = Path(build_dir) / ('pyinstaller-arguments-' + uuid.uuid4().hex + '.json')
    with arguments_path.open('x', encoding='utf-8') as writer:
        json.dump(command[3:], writer, ensure_ascii=False)
        writer.flush()
        os.fsync(writer.fileno())
    return [command[0], '-c', _PYINSTALLER_RUNNER, str(arguments_path)]

NATIVE_EXPORT_SOURCES = (
    "build_native.py", "vision_runtime.h", "vision_runtime.hpp", "vision_runtime.cpp",
    "predict.cpp", "execute.cpp", "cancel.cpp", "VisionRuntime.cs", "VisionRuntime.csproj",
    "CMakeLists.txt", "README.md",
)
HTTP_EXPORT_CLIENTS = ("inspection-service-client.mjs", "InspectionServiceClient.cs")


def export_resource_files(root: Path) -> list[tuple[Path, str]]:
    """Preserve sources used by flow export and the existing remote bundler."""
    root = Path(root)
    resources = [(root / "native_runtime" / name, "native_runtime") for name in NATIVE_EXPORT_SOURCES]
    resources.extend((root / "examples" / name, "examples") for name in HTTP_EXPORT_CLIENTS)
    backend = root / "backend"
    sources = sorted(source for source in backend.rglob("*.py")
                     if not {"tests", "__pycache__", ".pytest_cache"}.intersection(source.relative_to(backend).parts))
    if not sources:
        raise ValueError("Flow export Python engine sources are missing")
    resources.extend((source, (Path("backend") / source.relative_to(backend).parent).as_posix()) for source in sources)
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

def build_binary(output=OUTPUT_DIR, *, accept=True):
    print("=" * 70)
    print(" Vision AI Studio: Python Backend Native Binary Compiler")
    print("=" * 70)
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Target Entry: {ENTRY_POINT}")
    output = Path(output).resolve()
    print(f"Output Directory: {output}")

    if not check_pyinstaller():
        print("\n[ERROR] PyInstaller is not installed in the current Python environment.")
        print("Please install PyInstaller first by running:")
        print("    pip install pyinstaller")
        sys.exit(1)

    inventory = dependency_inventory(ROOT_DIR)
    validate_inventory(inventory)
    output.mkdir(parents=True, exist_ok=True)
    (output / '.build').mkdir(exist_ok=True)
    inventory_path = output / '.build' / 'backend-build-inventory.json'
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    import uuid
    frozen_source = snapshot_sources(ROOT_DIR, output / '.build' / ('source-' + uuid.uuid4().hex), inventory['resources'])
    cmd = pyinstaller_command(frozen_source, output, platform.system())

    print("\n[INFO] Executing PyInstaller command:")
    print(" ".join(cmd))
    print("\nCompiling... (this may take a few minutes for PyTorch symbols)\n")

    invocation = pyinstaller_invocation(cmd, output / '.build')
    res = subprocess.run(invocation, cwd=str(ROOT_DIR))
    if res.returncode != 0:
        print(f"\n[ERROR] PyInstaller compilation failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    binary_dir = output / 'vision_ai_backend'
    if str(ROOT_DIR) not in sys.path:sys.path.insert(0,str(ROOT_DIR))
    from scripts.package_license_texts import collect_frozen_licenses
    license_texts = collect_frozen_licenses(binary_dir, output/'.build/work/vision_ai_backend/PYZ-00.toc',
                                          binary_dir/'third_party_licenses')
    executable = binary_dir / ('vision_ai_backend.exe' if platform.system() == 'Windows' else 'vision_ai_backend')
    release = {'schema_version': 1, 'executable': executable.name, 'executable_sha256': sha256(executable),
               'inventory': inventory, 'signature_status': 'unverified', 'acceptance': None}
    release['license_texts'] = license_texts
    release['files'] = [{'path': str(path.relative_to(binary_dir)), 'sha256': sha256(path)}
                        for path in sorted(binary_dir.rglob('*')) if path.is_file()]
    receipt_path = binary_dir / 'backend-release.json'
    receipt_path.write_text(json.dumps(release, indent=2), encoding='utf-8')
    if accept:
        acceptance_path = binary_dir / 'backend-acceptance.json'
        subprocess.run([sys.executable, str(ROOT_DIR / 'scripts' / 'release_backend_acceptance.py'),
                        '--backend-dir', str(binary_dir), '--output', str(acceptance_path)], check=True)
        release['acceptance'] = json.loads(acceptance_path.read_text(encoding='utf-8'))
        receipt_path.write_text(json.dumps(release, indent=2), encoding='utf-8')

    print("\n" + "=" * 70)
    print(" Compilation Successful!")
    print(f" Compiled Binary Output: {binary_dir}")
    print("=" * 70)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Build a native backend on its actual target OS')
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    parser.add_argument('--skip-acceptance', action='store_true', help='Build only; release hooks will require separate acceptance')
    args = parser.parse_args()
    build_binary(args.output, accept=not args.skip_acceptance)

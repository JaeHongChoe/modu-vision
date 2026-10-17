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
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import stat
import sysconfig
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0,str(ROOT_DIR))
BACKEND_DIR = ROOT_DIR / "backend"
OUTPUT_DIR = ROOT_DIR / "dist-backend"
ENTRY_POINT = ROOT_DIR / "scripts" / "frozen_backend_entry.py"

OWNED_APPLICATION_LAUNCH_RESOURCES = (
    'scripts/frozen_backend_entry.py',
    'backend/engine/application_launch_controller.py',
    'backend/engine/application_launch_handshake.py',
    'backend/engine/application_launch_lease.py',
)
OWNED_APPLICATION_LAUNCH_PROTOCOL = 'owned_application_launch_controller_protocol'
OWNED_APPLICATION_CPU_PROTOCOL = 'owned_application_cpu_execution_protocol'
OWNED_APPLICATION_CPU_RESOURCES = OWNED_APPLICATION_LAUNCH_RESOURCES + (
    'backend/engine/application_launch_execution.py',
    'backend/engine/flow_package_runtime.py',
    'backend/engine/ocr.py',
    'backend/engine/runtime_deadline.py',
    'backend/engine/process_isolation.py',
)
OWNED_STAGED_CANARY_PROTOCOL = 'owned_staged_canary_cpu_execution_protocol'
OWNED_STAGED_CANARY_RESOURCES = OWNED_APPLICATION_CPU_RESOURCES + (
    'backend/engine/staged_canary_frozen_execution.py',
    'backend/engine/staged_update_canary.py', 'backend/engine/runtime_update.py',
    'backend/engine/global_migration.py', 'backend/engine/migration_guard.py',
)

# Names are distribution/module pairs because wheel and import names differ.
DEPENDENCIES = (
    ('fastapi', 'fastapi', True), ('uvicorn', 'uvicorn', True),
    ('pydantic', 'pydantic', True), ('python-multipart', 'python_multipart', True),
    ('httpx', 'httpx', True), ('psutil', 'psutil', True), ('numpy', 'numpy', True),
    ('cryptography','cryptography',True),
    ('Pillow', 'PIL', True), ('pi-heif', 'pi_heif', True), ('opencv-python-headless', 'cv2', True),
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


def _launch_protocol_resources(resources, names=OWNED_APPLICATION_LAUNCH_RESOURCES):
    """Require one exact checksum row per prerequisite; aliases are not pins."""
    if not isinstance(resources, list):
        raise ValueError('Owned application launch protocol resources are missing')
    pins = {}
    for name in names:
        rows = [row for row in resources if isinstance(row, dict) and row.get('path') == name]
        if len(rows) != 1 or set(rows[0]) != {'path', 'sha256'}:
            raise ValueError('Owned application launch protocol resource differs: '+name)
        digest = rows[0]['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(value not in '0123456789abcdef' for value in digest):
            raise ValueError('Owned application launch protocol checksum differs: '+name)
        pins[name] = digest
    return pins


def _launch_dispatch_present(raw):
    """A flag in comments or a dormant function cannot advertise a dispatcher."""
    try:
        tree = ast.parse(raw)
    except (SyntaxError, UnicodeError, ValueError):
        return False
    shape = lambda node: ast.dump(node, include_attributes=False)
    guard = ast.parse("if __name__ == '__main__': pass").body[0].test
    dispatch = ast.parse("""if len(sys.argv)>1 and sys.argv[1]=='--owned-application-launch-controller':
    from backend.engine.application_launch_controller import main
    raise SystemExit(main(sys.argv[2:]))
""").body[0]
    bootstrap_import = ast.parse('from backend.engine.application_launch_handshake import early_backend_bootstrap').body[0]
    bootstrap_call = ast.parse('early_backend_bootstrap()').body[0]
    legacy_server = ast.parse("runpy.run_module('backend.main', run_name='__main__')").body[0]
    canonical_import = ast.parse('from backend.main import main as backend_main').body[0]
    canonical_server = ast.parse('backend_main()').body[0]
    entries = [node for node in tree.body if isinstance(node, ast.If) and shape(node.test) == shape(guard)]
    if len(entries) != 1:
        return False
    entry = entries[0]
    # Legacy declared inventories remain readable; new builds use the exact
    # canonical callable so ownership classes are not duplicated by runpy.
    legacy = [node for node in entry.body if shape(node) == shape(legacy_server)]
    canonical = [node for node in entry.body if shape(node) == shape(canonical_server)]
    imports = [node for node in entry.body if shape(node) == shape(canonical_import)]
    if len(legacy) == 1 and not canonical and not imports:
        tail = [shape(legacy_server)]
    elif not legacy and len(canonical) == len(imports) == 1:
        tail = [shape(canonical_import), shape(canonical_server)]
    else:
        return False
    wanted = [shape(dispatch), shape(bootstrap_import), shape(bootstrap_call), *tail]
    positions = []
    for expected in wanted:
        matches = [index for index, node in enumerate(entry.body) if shape(node) == expected]
        if len(matches) != 1:
            return False
        positions.append(matches[0])
    if positions != sorted(positions):
        return False
    freeze_support = shape(ast.parse('multiprocessing.freeze_support()').body[0])
    if any(shape(node) != freeze_support for node in entry.body[:positions[0]]):
        return False
    forbidden = {shape(legacy_server), shape(canonical_import), shape(canonical_server)}
    return not any(shape(node) in forbidden for node in tree.body[:tree.body.index(entry)])


def _owned_launch_protocol_available(root, resources):
    """Bind the declaration to bytes included in this exact source inventory."""
    try:
        pins = _launch_protocol_resources(resources)
    except ValueError:
        return False
    root = Path(root)
    for name, digest in pins.items():
        source = root/name
        if (not source.is_file() or source.is_symlink()
                or any(parent.is_symlink() for parent in source.parents if parent != root and root in parent.parents)
                or sha256(source) != digest):
            return False
    raw = (root/OWNED_APPLICATION_LAUNCH_RESOURCES[0]).read_bytes()
    return (hashlib.sha256(raw).hexdigest() == pins[OWNED_APPLICATION_LAUNCH_RESOURCES[0]]
            and _launch_dispatch_present(raw))


def _owned_cpu_protocol_available(root, resources):
    """The separately versioned worker must precede every mutable fallback."""
    if not _owned_launch_protocol_available(root, resources): return False
    try:
        pins = _launch_protocol_resources(resources, OWNED_APPLICATION_CPU_RESOURCES)
        for name, digest in pins.items():
            source = Path(root)/name
            if source.is_symlink() or not source.is_file() or sha256(source) != digest: return False
        tree = ast.parse((Path(root)/OWNED_APPLICATION_LAUNCH_RESOURCES[0]).read_bytes())
        shape = lambda node: ast.dump(node, include_attributes=False)
        guard = ast.parse("if __name__ == '__main__': pass").body[0].test
        worker = ast.parse("""if len(sys.argv)>1 and sys.argv[1]=='--owned-application-cpu-worker':
    from backend.engine.application_launch_execution import frozen_worker_main
    raise SystemExit(frozen_worker_main(sys.argv[2:]))
""").body[0]
        entry = next(node for node in tree.body if isinstance(node, ast.If) and shape(node.test)==shape(guard))
        # The original controller declaration independently validates its exact
        # prefix. The new worker is its immediate successor, before diagnostics,
        # ordinary bootstrap, or any additional command dispatcher.
        positions = [i for i,node in enumerate(entry.body) if shape(node)==shape(worker)]
        return positions == [2] and shape(entry.body[0])==shape(ast.parse('multiprocessing.freeze_support()').body[0])
    except (ValueError, SyntaxError, OSError, StopIteration): return False


def _owned_staged_canary_protocol_available(root, resources):
    """The fixed staged role is separate from ordinary committed execution."""
    if not _owned_cpu_protocol_available(root, resources): return False
    try:
        pins=_launch_protocol_resources(resources,OWNED_STAGED_CANARY_RESOURCES)
        for name,digest in pins.items():
            source=Path(root)/name
            if source.is_symlink() or not source.is_file() or sha256(source)!=digest:return False
        tree=ast.parse((Path(root)/OWNED_APPLICATION_LAUNCH_RESOURCES[0]).read_bytes())
        shape=lambda node:ast.dump(node,include_attributes=False)
        guard=ast.parse("if __name__ == '__main__': pass").body[0].test
        worker=ast.parse("""if len(sys.argv)>1 and sys.argv[1]=='--owned-staged-canary-cpu-worker':
    from backend.engine.staged_canary_frozen_execution import frozen_worker_main
    raise SystemExit(frozen_worker_main(sys.argv[2:]))
""").body[0]
        entry=next(node for node in tree.body if isinstance(node,ast.If) and shape(node.test)==shape(guard))
        return [i for i,node in enumerate(entry.body) if shape(node)==shape(worker)]==[3]
    except (ValueError,SyntaxError,OSError,StopIteration):return False


def dependency_inventory(root: Path, *, supplier_manifest=None) -> dict:
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
                          root/'scripts/package_license_texts.py',root/'scripts/license_inventory.py',root/'scripts/build_backend_binary.py'})
    inventory = {'schema_version': 1, 'platform': platform.system(), 'architecture': platform.machine(),
                 'python_version': platform.python_version(), 'dependencies': dependencies,
                 'compiler': {'name': 'PyInstaller', 'version': importlib.metadata.version('pyinstaller') if check_pyinstaller() else None},
                 'resources': [{'path': str(path.relative_to(root)), 'sha256': sha256(path)} for path in resources],
                 'offline': {'pretrained_weights_bundled': False,
                             'network_downloads_required_for_uncached_training': True,
                             'inference_requires_exported_package_weights': True,
                             'hardware_prerequisites': ['CUDA driver for CUDA targets', 'Compatible camera/PLC SDK and device permissions'],
                             'optional_features_unavailable': [row['module'] for row in dependencies if not row['required'] and not row['available']]}}
    if _owned_launch_protocol_available(root, inventory['resources']):
        # This declares the fixed bootstrap interface, never native startup,
        # process-tree exit, inference, publisher, or release acceptance.
        inventory[OWNED_APPLICATION_LAUNCH_PROTOCOL] = 1
    if _owned_cpu_protocol_available(root, inventory['resources']):
        inventory[OWNED_APPLICATION_CPU_PROTOCOL] = 1
    if _owned_staged_canary_protocol_available(root, inventory['resources']):
        inventory[OWNED_STAGED_CANARY_PROTOCOL] = 1
    if supplier_manifest is not None:
        from scripts.package_license_texts import _supplier_licenses,_read
        source=Path(supplier_manifest).absolute();suppliers=_supplier_licenses(source)
        inventory['license_supplier']={'manifest_sha256':hashlib.sha256(_read(source,source.parent,1024*1024)).hexdigest(),
                                       'archives':[suppliers[key][1] for key in sorted(suppliers)]}
    inventory['build_identity_sha256'] = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return inventory


def validate_inventory(inventory):
    if OWNED_STAGED_CANARY_PROTOCOL in inventory:
        if (type(inventory[OWNED_STAGED_CANARY_PROTOCOL]) is not int or inventory[OWNED_STAGED_CANARY_PROTOCOL]!=1
                or type(inventory.get(OWNED_APPLICATION_CPU_PROTOCOL)) is not int or inventory[OWNED_APPLICATION_CPU_PROTOCOL]!=1):
            raise ValueError('Invalid staged canary worker protocol')
        _launch_protocol_resources(inventory.get('resources'),OWNED_STAGED_CANARY_RESOURCES)
    if OWNED_APPLICATION_CPU_PROTOCOL in inventory:
        if (type(inventory[OWNED_APPLICATION_CPU_PROTOCOL]) is not int
                or inventory[OWNED_APPLICATION_CPU_PROTOCOL] != 1
                or type(inventory.get(OWNED_APPLICATION_LAUNCH_PROTOCOL)) is not int
                or inventory[OWNED_APPLICATION_LAUNCH_PROTOCOL] != 1):
            raise ValueError('Unsupported owned application CPU execution protocol declaration')
        _launch_protocol_resources(inventory.get('resources'), OWNED_APPLICATION_CPU_RESOURCES)
    if OWNED_APPLICATION_LAUNCH_PROTOCOL in inventory:
        protocol = inventory[OWNED_APPLICATION_LAUNCH_PROTOCOL]
        if type(protocol) is not int or protocol != 1:
            raise ValueError('Unsupported owned application launch protocol declaration')
        _launch_protocol_resources(inventory.get('resources'))
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


def pyinstaller_command(root: Path, output: Path, target: str, *, native_path_guard=None) -> list[str]:
    root, output = Path(root), Path(output)
    if target not in ('Windows', 'Darwin', 'Linux'): raise ValueError('Unsupported release platform')
    command = [sys.executable, '-m', 'PyInstaller', '--name', 'vision_ai_backend', '--onedir',
               '--clean', '--noconfirm', f'--distpath={output}',
               f'--workpath={output / ".build" / "work"}', f'--specpath={output / ".build"}',
               f'--paths={root}', f'--paths={root / "backend"}',
               '--collect-all=timm', '--collect-all=ultralytics', '--collect-all=onnxruntime',
               '--collect-all=safetensors', '--collect-all=huggingface_hub', '--collect-all=pi_heif']
    # Global scientific environments may contain notebook/GUI/browser tooling.
    # They are not backend dependencies and can drag multi-GB SDKs into hooks.
    for module in ('IPython','notebook','jupyterlab','nbconvert','PyQt5','PyQt6','PySide2','PySide6',
                   'playwright','altair','bokeh','streamlit','tensorflow','keras','pytest','sitecustomize',
                   'panel','sphinx'):
        command.append('--exclude-module='+module)
    for module in ('uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
                   'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on', 'python_multipart',
                   'torch', 'torchvision', 'cv2', 'PIL', 'sklearn', 'psutil', 'yaml'):
        command.append('--hidden-import=' + module)
    # Dynamic API imports and service child entry points must survive freezing.
    for source in sorted((root / 'backend').rglob('*.py')):
        if {'tests', '__pycache__', '.pytest_cache'}.intersection(source.relative_to(root).parts): continue
        parts = list(source.relative_to(root).with_suffix('').parts)
        if parts[-1] == '__init__': parts.pop()
        command.append('--hidden-import=' + '.'.join(parts))
    separator = ';' if target == 'Windows' else ':'
    for source, destination in export_resource_files(root):
        command.extend(['--add-data', f'{source}{separator}{destination}'])
    command.extend(['--add-data', f'{root / "scripts" / "frozen_backend_entry.py"}{separator}scripts'])
    command.extend(['--add-data', f'{output / ".build" / "backend-build-inventory.json"}{separator}.'])
    if native_path_guard is not None and native_path_guard['compiled']:
        binary = Path(native_path_guard['compiled_binary']['path'])
        current = _runtime_guard_pin(binary)
        if current != native_path_guard['compiled_binary']:
            raise ValueError('Native path guard binary changed before PyInstaller')
        command.append('--hidden-import=backend.engine._runtime_path_guard')
        command.extend(['--add-binary', f'{binary}{separator}backend/engine'])
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
    guard_source = backend / 'engine' / '_runtime_path_guard.c'
    if guard_source.exists() or guard_source.is_symlink():
        resources.append((guard_source, 'backend/engine'))
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


# This optional POSIX extension is built only in the existing owned snapshot.
# Its source and ABI binary are new material; no previous CPU result covers it.
RUNTIME_PATH_GUARD_SOURCE = 'backend/engine/_runtime_path_guard.c'
RUNTIME_PATH_GUARD_MODULE = 'backend.engine._runtime_path_guard'


def _runtime_guard_pin(path):
    """Full stable bytes/identity pin, excluding access time only."""
    path = Path(path).absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError('Native build provenance cannot follow links: ' + str(path))
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    primary = None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Native build provenance must be a regular file')
        identity = lambda s: [s.st_dev, s.st_ino, s.st_mode, s.st_nlink, s.st_size,
                              s.st_mtime_ns, s.st_ctime_ns]
        extra = lambda s: [s.st_uid, s.st_gid, getattr(s, 'st_flags', 0)]
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024):
            digest.update(chunk)
        if (identity(os.fstat(fd)) != identity(before)
                or identity(path.lstat()) != identity(before)
                or extra(os.fstat(fd)) != extra(before)):
            raise ValueError('Native build provenance changed while reading')
        result = {'path': str(path), 'sha256': digest.hexdigest(), 'size': before.st_size,
                  'identity': identity(before), 'uid': before.st_uid,
                  'gid': before.st_gid, 'flags': getattr(before, 'st_flags', 0)}
    except BaseException as error:
        primary = error
    finally:
        try:
            os.close(fd)
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                try: primary.add_note('Secondary native provenance close failure: ' + repr(error))
                except BaseException: pass
    if primary is not None:
        raise primary
    return result


def _runtime_guard_json(path, value):
    """Closed pending writer, exclusive publication, and negative invalidation."""
    import uuid
    path = Path(path)
    pending = path.with_name(path.name + '.partial')
    primary = None
    writer = pending.open('x', encoding='utf-8')
    try:
        json.dump(value, writer, sort_keys=True, indent=2)
        writer.flush()
        os.fsync(writer.fileno())
    except BaseException as error:
        primary = error
    finally:
        try:
            writer.close()
        except BaseException as error:
            if primary is None:
                primary = error
            else:
                try: primary.add_note('Secondary native record close failure: ' + repr(error))
                except BaseException: pass
    if primary is not None:
        raise primary
    partial = _runtime_guard_pin(pending)
    if partial['identity'][3] != 1:
        raise ValueError('Native build pending record has unexpected links')
    parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    parent_identity = None
    published = False
    close_attempted = False
    result = None
    def secondary(error):
        try: primary.add_note('Secondary native record publication failure: ' + repr(error))
        except BaseException: pass
    try:
        parent_identity = os.fstat(parent_fd)
        try:
            os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError('Native build canonical record already exists')
        os.link(pending.name, path.name, src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd, follow_symlinks=False)
        published = True
        linked = _runtime_guard_pin(path)
        if (linked['sha256'] != partial['sha256'] or linked['size'] != partial['size']
                or linked['identity'][:3] != partial['identity'][:3]
                or linked['identity'][3] != 2
                or any(linked[key] != partial[key] for key in ('uid', 'gid', 'flags'))):
            raise ValueError('Native build record publication differs')
        held = os.stat(pending.name, dir_fd=parent_fd, follow_symlinks=False)
        if [held.st_dev, held.st_ino] != partial['identity'][:2]:
            raise ValueError('Native build owned pending record changed')
        os.unlink(pending.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        result = _runtime_guard_pin(path)
        if (result['identity'][3] != 1 or result['sha256'] != partial['sha256']
                or result['size'] != partial['size']
                or result['identity'][:3] != partial['identity'][:3]
                or any(result[key] != partial[key] for key in ('uid', 'gid', 'flags'))):
            raise ValueError('Native build record final readback differs')
    except BaseException as error:
        primary = error
    finally:
        if primary is None:
            close_attempted = True
            try:
                os.close(parent_fd)
            except BaseException as error:
                primary = error
        if primary is not None:
            recovery_fd = parent_fd
            recovery_valid = True
            if close_attempted:
                # A raised close leaves its original descriptor ownership
                # uncertain. Never retry its number: another thread may reuse it.
                try: primary.add_note('Original native receipt parent close raised; closure is unconfirmed')
                except BaseException: pass
                recovery_fd = None
                recovery_valid = False
                try:
                    recovery_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                    recovered = os.fstat(recovery_fd)
                    if (recovered.st_dev, recovered.st_ino, recovered.st_mode,
                            recovered.st_uid, recovered.st_gid, getattr(recovered, 'st_flags', 0)) != (
                            parent_identity.st_dev, parent_identity.st_ino, parent_identity.st_mode,
                            parent_identity.st_uid, parent_identity.st_gid, getattr(parent_identity, 'st_flags', 0)):
                        raise ValueError('Native build receipt parent changed during recovery')
                    recovery_valid = True
                except BaseException as error:
                    secondary(error)
            try:
                if published and recovery_fd is not None and recovery_valid:
                    observed = os.stat(path.name, dir_fd=recovery_fd, follow_symlinks=False)
                    if (observed.st_dev, observed.st_ino) != tuple(partial['identity'][:2]):
                        raise ValueError('Native build canonical record is no longer the owned inode')
                    invalid = path.name + '.invalid-' + uuid.uuid4().hex
                    try:
                        os.stat(invalid, dir_fd=recovery_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise FileExistsError('Native build invalid record already exists')
                    os.rename(path.name, invalid, src_dir_fd=recovery_fd, dst_dir_fd=recovery_fd)
                    os.fsync(recovery_fd)
            except BaseException as error:
                secondary(error)
            finally:
                if recovery_fd is not None:
                    try: os.close(recovery_fd)
                    except BaseException as error: secondary(error)
    if primary is not None:
        raise primary
    return result


def build_runtime_path_guard(root: Path, output: Path, target: str, resources: list[dict]):
    """Compile the pinned snapshot C source using only the current local ABI."""
    root, output = Path(root), Path(output)
    rows = [row for row in resources if row['path'] == RUNTIME_PATH_GUARD_SOURCE]
    if not rows:
        return None
    if len(rows) != 1:
        raise ValueError('Native path guard source inventory is ambiguous')
    source = root / RUNTIME_PATH_GUARD_SOURCE
    source_pin = _runtime_guard_pin(source)
    if source_pin['sha256'] != rows[0]['sha256']:
        raise ValueError('Native path guard snapshot source differs')
    if target == 'Windows':
        return {'schema_version': 1, 'module': RUNTIME_PATH_GUARD_MODULE,
                'compiled': False, 'source': source_pin,
                'reason': 'POSIX-only accelerator; original Python guard fallback',
                'Native_accepted': False}
    if target not in ('Darwin', 'Linux') or os.name != 'posix' or target != platform.system():
        raise ValueError('Native path guard requires an actual POSIX build target')
    import uuid
    native_dir = output / '.build' / ('runtime-path-guard-' + uuid.uuid4().hex)
    native_dir.mkdir(mode=0o700, exist_ok=False)
    suffix = sysconfig.get_config_var('EXT_SUFFIX')
    if not isinstance(suffix, str) or not suffix.endswith('.so') or '/' in suffix:
        raise ValueError('Unsupported current Python extension ABI suffix')
    abi = {name: sysconfig.get_config_var(name) for name in
           ('SOABI', 'EXT_SUFFIX', 'MULTIARCH', 'ABIFLAGS', 'Py_DEBUG',
            'Py_GIL_DISABLED', 'SIZEOF_VOID_P', 'MACOSX_DEPLOYMENT_TARGET')}
    include = Path(sysconfig.get_config_var('INCLUDEPY')).resolve(strict=True)
    headers = [_runtime_guard_pin(include / name) for name in ('Python.h', 'pyconfig.h')]
    interpreter = _runtime_guard_pin(Path(sys.executable).resolve(strict=True))
    before = {row['path']: _runtime_guard_pin(root / row['path']) for row in resources}
    compiler_env = os.environ.copy()
    compiler_env.update({
        'YOLO_AUTOINSTALL': 'false', 'PIP_NO_INDEX': '1',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'TMPDIR': str(native_dir),
    })
    discovery = []
    def query(argv):
        completed = subprocess.run(argv, cwd=str(native_dir), env=compiler_env,
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        discovery.append({'argv': argv, 'returncode': completed.returncode,
                          'stdout_sha256': hashlib.sha256(completed.stdout).hexdigest(),
                          'stderr_sha256': hashlib.sha256(completed.stderr).hexdigest()})
        if completed.returncode:
            raise subprocess.CalledProcessError(completed.returncode, argv,
                                                completed.stdout, completed.stderr)
        return completed.stdout.decode('utf-8').strip()
    sdk = None
    if target == 'Darwin':
        xcrun = shutil.which('xcrun')
        if xcrun is None:
            raise RuntimeError('Local xcrun and macOS SDK are required for the POSIX accelerator')
        xcrun = str(Path(xcrun).resolve(strict=True))
        xcrun_pin = _runtime_guard_pin(xcrun)
        sdk_root = Path(query([xcrun, '--sdk', 'macosx', '--show-sdk-path'])).resolve(strict=True)
        compiler = Path(query([xcrun, '--sdk', 'macosx', '--find', 'clang'])).resolve(strict=True)
        settings = sdk_root / 'SDKSettings.json'
        if not settings.is_file(): settings = sdk_root / 'SDKSettings.plist'
        sdk_stat = sdk_root.stat()
        sdk = {'path': str(sdk_root), 'identity': [sdk_stat.st_dev, sdk_stat.st_ino,
               sdk_stat.st_mode, sdk_stat.st_nlink, sdk_stat.st_size,
               sdk_stat.st_mtime_ns, sdk_stat.st_ctime_ns],
               'settings': _runtime_guard_pin(settings), 'xcrun': xcrun_pin}
        architecture = platform.machine()
        if architecture not in ('arm64', 'x86_64'):
            raise ValueError('Unsupported current macOS Python architecture')
        flags = ['-isysroot', str(sdk_root), '-bundle', '-undefined', 'dynamic_lookup',
                 '-O2', '-Wall', '-Wextra', '-Werror=implicit-function-declaration',
                 '-arch', architecture, '-I' + str(include)]
    else:
        local_cc = shutil.which('cc')
        if local_cc is None:
            raise RuntimeError('A locally installed cc is required for the POSIX accelerator')
        compiler = Path(local_cc).resolve(strict=True)
        flags = ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra',
                 '-Werror=implicit-function-declaration', '-I' + str(include)]
    compiler_pin = _runtime_guard_pin(compiler)
    compiler_version = query([str(compiler), '--version'])
    pending_binary = native_dir / ('_runtime_path_guard' + suffix + '.partial')
    binary = root / 'backend' / 'engine' / ('_runtime_path_guard' + suffix)
    argv = [str(compiler), *flags, str(source), '-o', str(pending_binary)]
    primary = None
    result = {'schema_version': 1, 'module': RUNTIME_PATH_GUARD_MODULE, 'compiled': False,
              'source': source_pin, 'interpreter': interpreter, 'python_version': sys.version,
              'python_executable': sys.executable, 'ABI': abi, 'python_headers': headers,
              'target': target, 'architecture': platform.machine(), 'compiler': compiler_pin,
              'compiler_version': compiler_version, 'SDK': sdk, 'flags': flags, 'argv': argv,
              'cwd': str(native_dir), 'discovery': discovery, 'Native_accepted': False}
    try:
        completed = subprocess.run(argv, cwd=str(native_dir), env=compiler_env,
                                   stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        result['returncode'] = completed.returncode
        compile_error = (subprocess.CalledProcessError(completed.returncode, argv,
                         completed.stdout, completed.stderr) if completed.returncode else None)
        for name, data in (('compile-stdout.txt', completed.stdout),
                           ('compile-stderr.txt', completed.stderr)):
            try:
                (native_dir / name).write_bytes(data)
            except BaseException as error:
                if compile_error is None: compile_error = error
                else:
                    try: compile_error.add_note('Secondary native compiler log failure: ' + repr(error))
                    except BaseException: pass
        if compile_error is not None:
            raise compile_error
        pending_pin = _runtime_guard_pin(pending_binary)
        os.link(pending_binary, binary, follow_symlinks=False)
        if _runtime_guard_pin(binary)['sha256'] != pending_pin['sha256']:
            raise ValueError('Native path guard compiled binary transfer differs')
        os.unlink(pending_binary)
        result['compiled_binary'] = _runtime_guard_pin(binary)
        if result['compiled_binary']['identity'][3] != 1:
            raise ValueError('Native path guard compiled binary has unexpected links')
        result['compiled'] = True
    except BaseException as error:
        primary = error
    finally:
        try:
            after = {row['path']: _runtime_guard_pin(root / row['path']) for row in resources}
            if before != after or compiler_pin != _runtime_guard_pin(compiler):
                raise ValueError('Native path guard source snapshot/compiler changed during build')
            if interpreter != _runtime_guard_pin(Path(sys.executable).resolve(strict=True)):
                raise ValueError('Native path guard interpreter changed during build')
            if headers != [_runtime_guard_pin(include / name) for name in ('Python.h', 'pyconfig.h')]:
                raise ValueError('Native path guard headers changed during build')
            if sdk is not None:
                sdk_after = Path(sdk['path']).stat()
                if (sdk['settings'] != _runtime_guard_pin(Path(sdk['settings']['path']))
                        or sdk['xcrun'] != _runtime_guard_pin(Path(sdk['xcrun']['path']))
                        or sdk['identity'] != [sdk_after.st_dev, sdk_after.st_ino,
                           sdk_after.st_mode, sdk_after.st_nlink, sdk_after.st_size,
                           sdk_after.st_mtime_ns, sdk_after.st_ctime_ns]):
                    raise ValueError('Native path guard SDK/discovery tool changed during build')
            result['source_snapshot_unchanged'] = True
        except BaseException as error:
            result['source_snapshot_unchanged'] = False
            if primary is None: primary = error
            else:
                try: primary.add_note('Secondary native source postcheck failure: ' + repr(error))
                except BaseException: pass
        result['first_exception'] = None if primary is None else repr(primary)
        if primary is not None: result['compiled'] = False
        try:
            result['provenance_receipt'] = _runtime_guard_json(native_dir / 'compile.json', result)
        except BaseException as error:
            if primary is None: primary = error
            else:
                try: primary.add_note('Secondary native provenance receipt failure: ' + repr(error))
                except BaseException: pass
    if primary is not None:
        raise primary
    return result


def packaged_runtime_path_guard(binary_dir: Path, provenance):
    """Record the actual frozen binary; PyInstaller may sign or rewrite Mach-O."""
    if provenance is None or not provenance['compiled']:
        return provenance
    filename = Path(provenance['compiled_binary']['path']).name
    matches = [path for path in Path(binary_dir).rglob(filename)
               if path.parent.parts[-2:] == ('backend', 'engine')]
    if len(matches) != 1:
        raise ValueError('Exactly one frozen backend.engine._runtime_path_guard binary is required')
    packaged = _runtime_guard_pin(matches[0])
    return {**provenance, 'packaged_binary': packaged,
            'packaged_relative_path': str(matches[0].relative_to(binary_dir)),
            'packaged_SHA_matches_compiled': packaged['sha256'] == provenance['compiled_binary']['sha256'],
            'Native_accepted': False}

def build_binary(output=OUTPUT_DIR, *, accept=True, supplier_manifest=None):
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

    inventory = dependency_inventory(ROOT_DIR,supplier_manifest=supplier_manifest)
    validate_inventory(inventory)
    output.mkdir(parents=True, exist_ok=True)
    (output / '.build').mkdir(exist_ok=True)
    inventory_path = output / '.build' / 'backend-build-inventory.json'
    inventory_path.write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    import uuid
    frozen_source = snapshot_sources(ROOT_DIR, output / '.build' / ('source-' + uuid.uuid4().hex), inventory['resources'])
    native_path_guard = build_runtime_path_guard(frozen_source, output, platform.system(), inventory['resources'])
    if native_path_guard is not None:
        inventory['native_runtime_path_guard'] = native_path_guard
        inventory.pop('build_identity_sha256')
        inventory['build_identity_sha256'] = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        inventory_path.write_text(json.dumps(inventory, indent=2), encoding='utf-8')
    if native_path_guard is not None and native_path_guard['compiled']:
        cmd = pyinstaller_command(frozen_source, output, platform.system(), native_path_guard=native_path_guard)
    else:
        cmd = pyinstaller_command(frozen_source, output, platform.system())

    print("\n[INFO] Executing PyInstaller command:")
    print(" ".join(cmd))
    print("\nCompiling... (this may take a few minutes for PyTorch symbols)\n")

    invocation = pyinstaller_invocation(cmd, output / '.build')
    # Compiler hooks may import Ultralytics. Keep its auto-install path and
    # pip index access disabled in the child without changing the caller.
    compiler_env = os.environ.copy()
    compiler_env.update({
        'YOLO_AUTOINSTALL': 'false', 'PIP_NO_INDEX': '1',
        'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONDONTWRITEBYTECODE': '1',
        'HF_HUB_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
    })
    res = subprocess.run(invocation, cwd=str(ROOT_DIR), env=compiler_env)
    if res.returncode != 0:
        print(f"\n[ERROR] PyInstaller compilation failed with exit code {res.returncode}")
        sys.exit(res.returncode)

    binary_dir = output / 'vision_ai_backend'
    if str(ROOT_DIR) not in sys.path:sys.path.insert(0,str(ROOT_DIR))
    from scripts.package_license_texts import collect_frozen_licenses
    if supplier_manifest is not None and sha256(supplier_manifest)!=inventory['license_supplier']['manifest_sha256']:
        raise ValueError('Offline license supplier manifest changed during compilation')
    license_texts = collect_frozen_licenses(binary_dir, output/'.build/work/vision_ai_backend/PYZ-00.toc',
                                          binary_dir/'third_party_licenses',supplier_manifest=supplier_manifest)
    if supplier_manifest is not None and sha256(supplier_manifest)!=inventory['license_supplier']['manifest_sha256']:
        raise ValueError('Offline license supplier manifest changed during collection')
    executable = binary_dir / ('vision_ai_backend.exe' if platform.system() == 'Windows' else 'vision_ai_backend')
    release = {'schema_version': 1, 'executable': executable.name, 'executable_sha256': sha256(executable),
               'inventory': inventory, 'signature_status': 'unverified', 'acceptance': None}
    if native_path_guard is not None:
        release['native_runtime_path_guard'] = packaged_runtime_path_guard(binary_dir, native_path_guard)
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
    parser.add_argument('--license-supplier-manifest',type=Path,help='Explicit offline hash-pinned vendor archives or immutable upstream license inputs; no package is installed')
    args = parser.parse_args()
    build_binary(args.output, accept=not args.skip_acceptance,supplier_manifest=args.license_supplier_manifest)

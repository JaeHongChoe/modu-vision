"""First-run state of this installation (S2-01) and the example dataset's place on disk.

The guide's state is one small JSON record in the user data folder (written atomically): whether it was dismissed.
The example dataset lives in an app-owned folder next to it, drawn from its seed when absent; a folder that no longer
matches the example is left alone and a new one is drawn next to it. The example's progress itself is driven by the app
through the same project, data, training, flow and inspection calls a user makes.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

from backend.engine.demo_dataset import DEMO_ID, DEMO_SIZE, DEMO_VERSION, write_demo_dataset
from backend.engine.runtime_process_control import atomic_private_json

_LOCK = threading.Lock()


def user_data_dir() -> Path:
    return Path(os.environ.get('VISION_AI_STUDIO_USER_DATA_DIR') or Path.home() / '.modu_vision')


def examples_root() -> Path:
    return user_data_dir() / 'examples'


class OnboardingRecordError(Exception):
    """The guide's record cannot be kept where it belongs (something other than a file is there)."""


class OnboardingStore:
    def __init__(self, path: Optional[Path | str] = None):
        self.path = Path(path) if path is not None else user_data_dir() / 'onboarding.json'

    def state(self) -> dict[str, Any]:
        with _LOCK:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                return {'version': 1, 'dismissed': False}
            except (OSError, ValueError):  # an unreadable record shows the guide again; nothing else depends on it
                return {'version': 1, 'dismissed': False, 'record_error': 'unreadable'}
            if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('dismissed'), bool):
                return {'version': 1, 'dismissed': False, 'record_error': 'unreadable'}
            return {'version': 1, 'dismissed': data['dismissed']}

    def dismiss(self, dismissed: bool = True) -> dict[str, Any]:
        with _LOCK:
            if self.path.is_symlink() or (self.path.exists() and not self.path.is_file()):
                raise OnboardingRecordError(f'안내 기록 위치({self.path.name})가 파일이 아니어서 저장하지 않았습니다. 사용자 데이터 폴더를 확인하세요.')
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_private_json(self.path, {'version': 1, 'dismissed': bool(dismissed)})
        return {'version': 1, 'dismissed': bool(dismissed)}


def _pixel_sha256(path: Path) -> Optional[str]:
    from PIL import Image
    try:
        with Image.open(path) as image:
            # The size comes from the header: an image of another size is not the example's and is never decoded (a
            # tiny file can declare a huge image).
            if image.size != (DEMO_SIZE, DEMO_SIZE):
                return None
            return hashlib.sha256(image.convert('L').tobytes()).hexdigest()
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


# Files a file browser leaves behind (macOS Finder, Windows Explorer) that the importer does not read. AppleDouble
# '._' files carry the image's extension, and the importer and the training loader read them, so they are a change.
_SYSTEM_FILES = {'.ds_store', 'thumbs.db', 'desktop.ini'}


def _system_file(path: Path) -> bool:
    return path.name.lower() in _SYSTEM_FILES


def _matches(folder: Path, manifest: Any) -> bool:
    """The folder holds exactly the example's images with the example's pixels, in exactly its folders (no added,
    missing or changed file, no added folder: an empty folder is an empty class to the importer). A manifest of another
    shape matches nothing."""
    try:
        expected = {row['path']: row['pixel_sha256'] for row in manifest['images']}
    except (KeyError, TypeError, AttributeError):
        return False
    entries = list(folder.rglob('*'))
    present = {path.relative_to(folder).as_posix() for path in entries if path.is_file() and not _system_file(path)}
    folders = {path.relative_to(folder).as_posix() for path in entries if path.is_dir()}
    expected_folders = {parent.as_posix() for name in expected for parent in Path(name).parents if parent.as_posix() != '.'}
    return (present == set(expected) and folders == expected_folders
            and all(_pixel_sha256(folder / name) == digest for name, digest in expected.items()))


def _example_folder_names() -> list[str]:
    base = f'{DEMO_ID}-v{DEMO_VERSION}'
    return [base] + [f'{base}-{attempt}' for attempt in range(2, 100)]


def ensure_example_dataset(root: Optional[Path] = None) -> dict[str, Any]:
    """The example dataset folder, drawn from its seed when absent; a folder that no longer matches it (a file added,
    removed or changed) is left as it is and a new folder is used, so nothing a user put there is overwritten."""
    root = Path(root) if root is not None else examples_root()
    root.mkdir(parents=True, exist_ok=True)
    for name in _example_folder_names():
        folder = root / name
        manifest_path = root / f'{folder.name}.manifest.json'
        if folder.is_symlink() or manifest_path.is_symlink():
            continue  # never followed: the example is only ever a real folder the app drew
        if manifest_path.exists() and not manifest_path.is_file():
            continue  # its record cannot be written there; the next name is used
        if folder.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if folder.is_dir() and _matches(folder, manifest):
                return {'folder': str(folder.resolve()), 'manifest': manifest, 'created': False}
            continue
        manifest = write_demo_dataset(folder)
        atomic_private_json(manifest_path, manifest)
        return {'folder': str(folder.resolve()), 'manifest': manifest, 'created': True}
    raise RuntimeError('예제 데이터 폴더를 만들 수 없습니다. 사용자 데이터 폴더의 examples 폴더를 확인하세요.')


def is_example_source(source: Optional[str], root: Optional[Path] = None) -> bool:
    """Whether a project's source folder is one of the app's example dataset folders."""
    if not source:
        return False
    root = (Path(root) if root is not None else examples_root()).resolve()
    folder = Path(source).expanduser().resolve()
    # Windows paths compare without letter case (C:\Users and c:\users are one folder).
    same_parent = os.path.normcase(str(folder.parent)) == os.path.normcase(str(root))
    if same_parent and os.path.normcase(folder.name) in {os.path.normcase(name) for name in _example_folder_names()}:
        return True
    # A file system that ignores letter case (macOS by default) reaches an example folder under another spelling too.
    for name in _example_folder_names():
        candidate = root / name
        try:
            # A link placed under an example name is not an example (the app only draws real folders there).
            if not candidate.is_symlink() and candidate.is_dir() and folder.exists() and os.path.samefile(candidate, folder):
                return True
        except OSError:
            continue
    return False

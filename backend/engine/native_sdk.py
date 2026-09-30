"""Native SDK sources are a required, checksum-bound export artifact."""
from pathlib import Path
import shutil


_REQUIRED = (
    'build_native.py', 'vision_runtime.h', 'vision_runtime.hpp', 'vision_runtime.cpp',
    'predict.cpp', 'execute.cpp', 'VisionRuntime.cs', 'VisionRuntime.csproj',
    'CMakeLists.txt', 'README.md',
)


def copy_native_sdk(source_dir: Path, destination_dir: Path) -> None:
    source_dir = Path(source_dir)
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise ValueError('Native SDK sources are missing from the application resources')
    for name in _REQUIRED:
        source = source_dir / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f'Native SDK source is missing or unsafe: {name}')
    destination_dir.mkdir(parents=True, exist_ok=True)
    for name in _REQUIRED:
        shutil.copyfile(source_dir / name, destination_dir / name, follow_symlinks=False)

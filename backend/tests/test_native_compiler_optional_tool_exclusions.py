"""Keep the frozen backend command intact while dropping optional GUI/docs tools.

These are command-contract controls. They do not compile or qualify a native
binary, runtime imports, service handshake, latency, or memory use.
"""
from pathlib import Path
import sys

import pytest

from scripts import build_backend_binary as build


@pytest.mark.parametrize('platform_name,separator', [
    ('Darwin', ':'), ('Linux', ':'), ('Windows', ';'),
])
def test_optional_tool_exclusions_preserve_the_whole_backend_command(
        tmp_path, monkeypatch, platform_name, separator):
    root = tmp_path/'source'
    output = tmp_path/'compiled'
    for rel in ('backend/__init__.py', 'backend/engine/__init__.py',
                'backend/engine/controller.py', 'backend/service.py',
                'backend/tests/not_a_hidden_import.py',
                'backend/__pycache__/not_a_hidden_import.py'):
        path = root/rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# isolated compiler command fixture\n')
    resources = [(root/'backend/engine/controller.py', 'backend/engine'),
                 (root/'native_runtime/runtime.py', 'native_runtime')]
    monkeypatch.setattr(build, 'export_resource_files', lambda requested: resources)

    command = build.pyinstaller_command(root, output, platform_name)
    # Old compiler fails here before the unchanged full-command comparison.
    assert command.count('--exclude-module=panel') == 1
    assert command.count('--exclude-module=sphinx') == 1
    remaining = [arg for arg in command if arg not in (
        '--exclude-module=panel', '--exclude-module=sphinx')]
    original = [
        sys.executable, '-m', 'PyInstaller', '--name', 'vision_ai_backend',
        '--onedir', '--clean', '--noconfirm', f'--distpath={output}',
        f'--workpath={output/".build"/"work"}', f'--specpath={output/".build"}',
        f'--paths={root}', f'--paths={root/"backend"}',
        '--collect-all=timm', '--collect-all=ultralytics',
        '--collect-all=onnxruntime', '--collect-all=safetensors',
        '--collect-all=huggingface_hub',
    ]
    original += ['--exclude-module='+name for name in (
        'IPython', 'notebook', 'jupyterlab', 'nbconvert', 'PyQt5', 'PyQt6',
        'PySide2', 'PySide6', 'playwright', 'altair', 'bokeh', 'streamlit',
        'tensorflow', 'keras', 'pytest', 'sitecustomize',
    )]
    original += ['--hidden-import='+name for name in (
        'uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets.auto', 'uvicorn.lifespan.on',
        'python_multipart', 'torch', 'torchvision', 'cv2', 'PIL', 'sklearn',
        'psutil', 'yaml', 'backend', 'backend.engine',
        'backend.engine.controller', 'backend.service',
    )]
    for source, destination in resources:
        original += ['--add-data', f'{source}{separator}{destination}']
    original += ['--add-data', f'{root/"scripts"/"frozen_backend_entry.py"}{separator}scripts',
                 '--add-data', f'{output/".build"/"backend-build-inventory.json"}{separator}.',
                 str(root/'scripts'/'frozen_backend_entry.py')]
    assert remaining == original

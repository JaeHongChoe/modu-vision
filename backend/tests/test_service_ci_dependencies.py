"""CPU source gates consume complete hash locks, not the developer environment."""
from pathlib import Path
import re
from urllib.parse import unquote, urlparse

import pytest
from packaging.requirements import Requirement
from packaging.utils import parse_wheel_filename
from packaging.version import Version

ROOT = Path(__file__).resolve().parents[2]


def _entries(path):
    text = path.read_text()
    logical = text.replace('\\\n', ' ')
    entries = {}
    for line in logical.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', '--')):
            continue
        requirement, *hashes = line.split('--hash=')
        parsed = Requirement(requirement.strip())
        assert hashes and all(re.fullmatch(r'sha256:[a-f0-9]{64}', h.strip()) for h in hashes)
        assert parsed.url or (len(parsed.specifier) == 1 and next(iter(parsed.specifier)).operator == '==')
        entries[parsed.name.lower().replace('_', '-')] = parsed
    return text, entries


@pytest.mark.parametrize('platform', ['ubuntu', 'windows'])
def test_cpu_ci_lock_covers_declared_runtime_and_testing_dependencies(platform):
    text, entries = _entries(ROOT / f'build/ci/requirements-{platform}-py313-cpu.lock')
    for line in (ROOT / 'requirements.txt').read_text().splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        declared = Requirement(line)
        selected = entries[declared.name.lower().replace('_', '-')]
        if selected.url:
            _, version, _, _ = parse_wheel_filename(unquote(urlparse(selected.url).path.rsplit('/', 1)[-1]))
        else:
            version = Version(next(iter(selected.specifier)).version)
        assert version in declared.specifier
    assert str(entries['pytest'].specifier) == '==8.3.4'
    for transitive in ['starlette', 'pydantic-core', 'anyio', 'scipy', 'sympy', 'matplotlib', 'requests', 'urllib3', 'packaging', 'pluggy', 'iniconfig', 'pydicom']:
        assert transitive in entries
    assert not any(name.startswith(('nvidia-', 'triton', 'cuda-', 'pytorch-triton')) for name in entries)
    for package in ['torch', 'torchvision']:
        assert entries[package].url and '/whl/cpu/' in entries[package].url
        assert urlparse(entries[package].url).hostname in {'download.pytorch.org', 'download-r2.pytorch.org'}
        filename = unquote(urlparse(entries[package].url).path.rsplit('/', 1)[-1])
        _, version, _, _ = parse_wheel_filename(filename)
        assert version == Version('2.9.1+cpu' if package == 'torch' else '0.24.1+cpu')
        assert 'cp313-cp313-' in entries[package].url
        assert ('win_amd64' if platform == 'windows' else 'manylinux_2_28_x86_64') in entries[package].url
    assert ('colorama' in entries) == (platform == 'windows')
    assert ('uvloop' in entries) == (platform == 'ubuntu')
    assert '--index-url https://pypi.org/simple' in text

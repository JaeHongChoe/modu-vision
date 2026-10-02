"""Component, dependency and model-weight license inventory (S6-01).

The receipt is review input for the public distribution decision (S6-11). It
records what each artifact contains, how it reaches users and which license
conditions are unresolved. It is not a legal compatibility determination and
never marks an unrecognised or unresolved license as cleared.

Usage:
  python scripts/license_inventory.py --output receipt.json
  python scripts/license_inventory.py --frozen-build dist-backend/vision_ai_backend --output receipt.json
  python scripts/license_inventory.py --write-notices THIRD_PARTY_NOTICES.md --write-matrix docs/model-license-matrix.md
  python scripts/license_inventory.py --check-notices THIRD_PARTY_NOTICES.md
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import email
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = 'modu-vision.license-inventory/v2'
PYTHON_RUNTIME = 'requirements.txt'
PYTHON_OPTIONAL = {'dicom': 'backend/requirements-dicom.txt', 'openvino': 'backend/requirements-openvino.txt',
                   'semantic_labeling': 'backend/requirements-semantic-labeling.txt'}
REMOTE_WORKER_LOCK = 'build/remote/requirements-models.lock'
REMOTE_WORKER_DOCKERFILE = 'build/remote/Dockerfile'
CACHED_WEIGHTS = 'build/remote/cache_weights.py'
DISTRIBUTED_THIRD_PARTY = ('npm_runtime', 'npm_bundled_build_output', 'electron_runtime', 'python_runtime')
# npm packages whose own code is emitted into the renderer bundle by the build.
BUNDLED_BUILD_OUTPUT = {'tailwindcss': 'base styles in the built CSS', 'vite': 'module preload helper in the built JS'}

# Licenses cleared only when every term is one of these (SPDX identifiers and
# common classifier spellings). Everything else needs review or a decision.
PERMISSIVE = {
    'MIT', 'MIT-0', 'MIT-CMU', 'X11', 'ISC', 'BSD-2-CLAUSE', 'BSD-3-CLAUSE', 'BSD-3-CLAUSE-CLEAR', 'BSD', '0BSD',
    'APACHE-2.0', 'APACHE', 'PSF-2.0', 'PYTHON-2.0', 'CNRI-PYTHON', 'ZLIB', 'UNLICENSE', 'CC0-1.0', 'BSL-1.0',
    'HPND', 'BLUEOAK-1.0.0', 'CC-BY-4.0',
}
CLASSIFIER_ALIASES = {
    'MIT LICENSE': 'MIT', 'BSD LICENSE': 'BSD', 'APACHE SOFTWARE LICENSE': 'APACHE', 'ISC LICENSE (ISCL)': 'ISC',
    'PYTHON SOFTWARE FOUNDATION LICENSE': 'PSF-2.0', 'THE UNLICENSE (UNLICENSE)': 'UNLICENSE', 'ZLIB/LIBPNG LICENSE': 'ZLIB',
    'HISTORICAL PERMISSION NOTICE AND DISCLAIMER (HPND)': 'HPND', 'BOOST SOFTWARE LICENSE 1.0 (BSL-1.0)': 'BSL-1.0',
    'APACHE LICENSE 2.0': 'APACHE-2.0', 'APACHE 2.0': 'APACHE-2.0', 'APACHE-2': 'APACHE-2.0', 'NEW BSD': 'BSD-3-CLAUSE',
    'NEW BSD LICENSE': 'BSD-3-CLAUSE', 'MODIFIED BSD': 'BSD-3-CLAUSE', 'SIMPLIFIED BSD': 'BSD-2-CLAUSE',
    '3-CLAUSE BSD': 'BSD-3-CLAUSE', 'BSD-3': 'BSD-3-CLAUSE', 'PSF': 'PSF-2.0', 'PSFL': 'PSF-2.0',
    'BSD 3-CLAUSE': 'BSD-3-CLAUSE', 'BSD 3-CLAUSE LICENSE': 'BSD-3-CLAUSE', '3-CLAUSE BSD LICENSE': 'BSD-3-CLAUSE',
    'BSD 2-CLAUSE': 'BSD-2-CLAUSE', 'BSD 2-CLAUSE LICENSE': 'BSD-2-CLAUSE', 'ISC LICENSE': 'ISC', 'APACHE LICENSE': 'APACHE',
}
STRONG_COPYLEFT = re.compile(r'\bA?GPL|AFFERO|\bSSPL|\bEUPL|\bOSL\b|GENERAL PUBLIC LICENSE', re.IGNORECASE)
WEAK_COPYLEFT = re.compile(r'\bLGPL|LESSER GENERAL PUBLIC|LIBRARY GENERAL PUBLIC|\bMPL|MOZILLA|\bEPL|ECLIPSE|\bCDDL', re.IGNORECASE)
RESTRICTED = re.compile(r'PROPRIETARY|COMMERCIAL|NON-?COMMERCIAL|-NC\b|\bNC-|NVIDIA|^UNLICENSED$|SEE LICENSE IN|BUSL|BSL-1\.1', re.IGNORECASE)
# SPDX exceptions that only grant additional permissions (upper case).
GRANTING_EXCEPTIONS = {
    'LLVM-EXCEPTION', 'CLASSPATH-EXCEPTION-2.0', 'GCC-EXCEPTION-2.0', 'GCC-EXCEPTION-3.1', 'AUTOCONF-EXCEPTION-2.0',
    'AUTOCONF-EXCEPTION-3.0', 'BISON-EXCEPTION-2.2', 'LIBTOOL-EXCEPTION', 'FONT-EXCEPTION-2.0', 'SWIFT-EXCEPTION',
}

DINO_LICENSE = 'DINOv3 License (the provider\'s own license; not an OSI-approved license)'
DINO_LICENSE_SOURCE = 'https://github.com/facebookresearch/dinov3/blob/main/LICENSE.md'
YOLO_LICENSE = 'AGPL-3.0 (the vendor also offers an enterprise license)'
YOLO_LICENSE_SOURCE = 'https://www.ultralytics.com/license'
TORCHVISION_LICENSE = 'Not established: torchvision code is BSD-3-Clause; weight and training-data terms are not reviewed'
TORCHVISION_WEIGHTS = {
    # torchvision weights name: where the code uses its DEFAULT weights
    'resnet18': 'classification backbone, anomaly feature extractor',
    'resnet50': 'anomaly feature extractor',
    'convnext_tiny': 'classification backbone',
    'efficientnet_b0': 'classification backbone',
    'fasterrcnn_mobilenet_v3_large_fpn': 'detection alternative',
    'fasterrcnn_resnet50_fpn_v2': 'detection alternative',
    'deeplabv3_resnet50': 'segmentation alternative',
    'deeplabv3_mobilenet_v3_large': 'segmentation alternative',
}
DINO_WEIGHTS = {
    'dinov3_vits16': 'vit_small_patch16_dinov3.lvd1689m',
    'dinov3_vitb16': 'vit_base_patch16_dinov3.lvd1689m',
    'dinov3_vitl16': 'vit_large_patch16_dinov3.lvd1689m',
}
DINO_USES = 'default backbone for classification, segmentation and patch classification; anomaly and labeling features'
YOLO_WEIGHTS = ('yolo26n', 'yolo26s')
USER_CONFIGURED_MODELS = {
    'sam2_user_configured': ('SAM2 model directory configured by the user (semantic labeling pack)', 'backend/engine/foundation_labeling.py'),
    'grounding_dino_user_configured': ('Grounding DINO model directory configured by the user (semantic labeling pack)',
                                       'backend/engine/label_candidate_providers.py'),
}
# Native libraries whose presence in a frozen build needs a license decision.
NATIVE_FAMILIES = [
    (re.compile(r'^(lib)?(x264|x265|readline)\b|^readline\.', re.I), 'needs_decision', 'commonly GPL-licensed native library'),
    (re.compile(r'^lib(avcodec|avformat|avutil|swscale|swresample|avfilter|gmp|mpfr|mpc|mp3lame)\b', re.I), 'needs_review',
     'LGPL or GPL depending on how the library was built'),
    (re.compile(r'^(lib)?python3(\.\d+)?\.(so|dylib|dll)|^libpython3\.\d+', re.I), 'resolved', 'Python interpreter (PSF-2.0)'),
]


def _sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(['git', '--no-optional-locks', *args], cwd=root, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


_RANK = {'resolved': 0, 'needs_review': 1, 'needs_decision': 2}


def _term_class(term: str) -> str:
    parts = re.split(r'\s+WITH(?:\s+|$)', term.strip(), flags=re.IGNORECASE)
    if len(parts) > 1:
        # An exception changes the terms: only one recognised permission-granting
        # exception keeps the base status; anything else is reviewed.
        base = _base_term_class(parts[0].strip())
        exception = parts[1].strip() if len(parts) == 2 else ''
        if exception.upper() in GRANTING_EXCEPTIONS:
            return base
        flagged = 'needs_decision' if STRONG_COPYLEFT.search(exception) or RESTRICTED.search(exception) else 'needs_review'
        return max(base, flagged, key=_RANK.get)
    return _base_term_class(term)


def _base_term_class(term: str) -> str:
    term = term.strip()
    upper = term.upper()
    canonical = CLASSIFIER_ALIASES.get(upper, upper)
    if WEAK_COPYLEFT.search(term):
        return 'needs_review'
    if STRONG_COPYLEFT.search(term) or RESTRICTED.search(term):
        return 'needs_decision'
    if canonical in PERMISSIVE:
        return 'resolved'
    return 'needs_review'


def _tokens(text: str) -> list[str]:
    spaced = re.sub(r'([()])', r' \1 ', text.replace(';', ' AND ').replace(',', ' AND ').replace(' / ', ' OR '))
    tokens, words = [], []
    for word in spaced.split():
        if word in ('(', ')') or word.upper() in ('AND', 'OR'):
            if words:
                tokens.append(' '.join(words))
                words = []
            tokens.append(word.upper() if word.upper() in ('AND', 'OR') else word)
        else:
            words.append(word)
    if words:
        tokens.append(' '.join(words))
    # A parenthesis right after a name without an operator belongs to the name,
    # e.g. "GNU General Public License v3 (GPLv3)".
    merged: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == '(' and merged and merged[-1] not in ('AND', 'OR', '('):
            depth, end = 0, index
            while end < len(tokens):
                depth += tokens[end] == '('
                depth -= tokens[end] == ')'
                if depth == 0:
                    break
                end += 1
            merged[-1] = f"{merged[-1]} ({' '.join(tokens[index + 1:end])})"
            index = end + 1
            continue
        merged.append(token)
        index += 1
    return merged


def _evaluate(tokens: list[str]) -> str:
    """OR binds weaker than AND. A choice is cleared only when every branch is
    permissive; with a permissive branch available the choice needs recording."""
    position = 0

    def expression():
        nonlocal position
        branches = [conjunction()]
        while position < len(tokens) and tokens[position] == 'OR':
            position += 1
            branches.append(conjunction())
        if len(branches) == 1:
            return branches[0]
        if all(branch == 'resolved' for branch in branches):
            return 'resolved'
        if 'resolved' in branches:
            return 'needs_review'
        return min(branches, key=_RANK.get)

    def conjunction():
        nonlocal position
        terms = [primary()]
        while position < len(tokens) and tokens[position] == 'AND':
            position += 1
            terms.append(primary())
        return max(terms, key=_RANK.get)

    def primary():
        nonlocal position
        if position >= len(tokens):
            return 'needs_review'
        token = tokens[position]
        position += 1
        if token == '(':
            value = expression()
            if position < len(tokens) and tokens[position] == ')':
                position += 1
                return value
            return max(value, 'needs_review', key=_RANK.get)
        if token in ('AND', 'OR', ')'):
            return 'needs_review'
        return _term_class(token)

    value = expression()
    return value if position >= len(tokens) else 'needs_review'


def license_status(text: str | None) -> str:
    """Allowlist-based status: resolved only when every required term is permissive."""
    if not text or text.strip().upper() in {'UNKNOWN', 'NONE', ''}:
        return 'needs_review'
    depth = 0
    for char in text:
        depth += {'(': 1, ')': -1}.get(char, 0)
        if depth < 0:
            break
    if depth != 0:
        # Malformed expressions are never cleared; flagged terms still need a decision.
        return 'needs_decision' if STRONG_COPYLEFT.search(text) or RESTRICTED.search(text) else 'needs_review'
    return _evaluate(_tokens(text))


def _npm_components(root: Path) -> list[dict]:
    lock = json.loads((root / 'package-lock.json').read_text(encoding='utf-8'))
    components = []
    for key, entry in sorted(lock.get('packages', {}).items()):
        if not key:
            continue
        name = key.split('node_modules/')[-1]
        license_text = entry.get('license') or 'UNKNOWN'
        dev = bool(entry.get('dev') or entry.get('devOptional'))
        status = None
        if name == 'electron':
            category, distribution, notices = 'electron_runtime', 'installer_runtime', True
            license_text = f'{license_text} (npm package); the Electron binary bundles Chromium, Node.js and FFmpeg under their own licenses'
            status = 'needs_review'
        elif name in BUNDLED_BUILD_OUTPUT:
            category, distribution, notices = 'npm_bundled_build_output', f'bundled_in_renderer ({BUNDLED_BUILD_OUTPUT[name]})', True
        elif dev:
            category, distribution, notices = 'npm_build_tool', 'not_distributed', False
        else:
            category, distribution, notices = 'npm_runtime', 'bundled_in_renderer', True
        components.append({
            'category': category, 'name': name, 'version': entry.get('version'), 'license': license_text,
            'source': entry.get('resolved'), 'distribution': distribution, 'notices_required': notices,
            'bundled': distribution != 'not_distributed',
            'status': status or (license_status(entry.get('license')) if notices else 'not_distributed'),
        })
    unique = {(item['category'], item['name'], item['version']): item for item in components}
    return list(unique.values())


def _requirement_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = []
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.split('#', 1)[0].strip()
        if line and re.match(r'^[A-Za-z0-9]', line):
            lines.append(line)
    return lines


def _canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).lower()


def _parse_requirement(line: str):
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return None
    return Requirement(line)


def _python_license(meta) -> str:
    expression = (meta.get('License-Expression') or '').strip()
    if expression:
        return expression
    declared = (meta.get('License') or '').strip()
    # Some packages place the whole license text in this field; keep a short name.
    declared = declared if declared and '\n' not in declared and len(declared) <= 80 and declared.upper() != 'UNKNOWN' else ''
    classifiers = sorted({value.split('::')[-1].strip() for value in meta.get_all('Classifier') or []
                          if value.startswith('License ::') and value.strip() != 'License :: OSI Approved'})
    # Both sources are kept; the status of the combination is the stricter one.
    parts = ([declared] if declared else []) + [item for item in classifiers if item != declared]
    return ' AND '.join(parts) or 'UNKNOWN'


def _python_source(meta) -> str | None:
    for value in meta.get_all('Project-URL') or []:
        label, _, url = value.partition(',')
        if label.strip().lower() in {'homepage', 'home', 'source', 'repository', 'source code'}:
            return url.strip()
    return (meta.get('Home-page') or '').strip() or f'https://pypi.org/project/{meta.get("Name")}/'


def _python_closure(lines: list[str]) -> tuple[dict[str, metadata.Distribution], list[str], list[str]]:
    """Dependency closure in this interpreter, honouring requested extras."""
    found: dict[str, metadata.Distribution] = {}
    missing: list[str] = []
    notes: list[str] = []
    queue: list[tuple[str, set[str]]] = []
    for line in lines:
        parsed = _parse_requirement(line)
        if parsed is None:
            notes.append('packaging is unavailable; extras and markers were not evaluated')
            queue.append((re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*', line).group(0), set()))
        else:
            queue.append((parsed.name, set(parsed.extras)))
    visited: set[tuple[str, frozenset]] = set()
    while queue:
        name, extras = queue.pop()
        key = (_canonical(name), frozenset(extras))
        if key in visited:
            continue
        visited.add(key)
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            if _canonical(name) not in missing:
                missing.append(_canonical(name))
            continue
        found[_canonical(name)] = dist
        for requirement in dist.requires or []:
            parsed = _parse_requirement(requirement)
            if parsed is None:
                continue
            environments = [{'extra': extra} for extra in extras | {''}]
            if parsed.marker is None or any(parsed.marker.evaluate(env) for env in environments):
                queue.append((parsed.name, set(parsed.extras)))
    return found, sorted(missing), sorted(set(notes))


def _requirements_satisfied(lines: list[str]) -> list[str]:
    problems = []
    for line in lines:
        parsed = _parse_requirement(line)
        if parsed is None:
            continue
        try:
            version = metadata.version(parsed.name)
        except metadata.PackageNotFoundError:
            problems.append(f'{parsed.name} is not installed')
            continue
        if parsed.specifier and not parsed.specifier.contains(version, prereleases=True):
            problems.append(f'{parsed.name} {version} does not satisfy {parsed.specifier}')
    return problems


def _python_components(root: Path) -> tuple[list[dict], list[str], list[str]]:
    components, notes, unsatisfied = [], [], []
    groups = [('python_runtime', 'frozen_backend', PYTHON_RUNTIME, None)]
    groups += [('python_optional', 'optional_pack', relative, group) for group, relative in PYTHON_OPTIONAL.items()]
    seen_runtime: set[str] = set()
    for category, distribution, relative, group in groups:
        lines = _requirement_lines(root / relative)
        if category == 'python_runtime':
            unsatisfied = _requirements_satisfied(lines)
        found, missing, closure_notes = _python_closure(lines)
        notes.extend(closure_notes)
        for name, dist in sorted(found.items()):
            if category != 'python_runtime' and name in seen_runtime:
                continue
            license_text = _python_license(dist.metadata)
            components.append({
                'category': category, 'name': name, 'version': dist.version, 'license': license_text,
                'source': _python_source(dist.metadata), 'distribution': distribution, 'group': group,
                'notices_required': True, 'bundled': category == 'python_runtime', 'status': license_status(license_text),
            })
            if category == 'python_runtime':
                seen_runtime.add(name)
        for name in missing:
            components.append({
                'category': category, 'name': name, 'version': None, 'license': 'UNKNOWN', 'source': None,
                'distribution': distribution, 'group': group, 'notices_required': True,
                'bundled': category == 'python_runtime', 'status': 'not_installed_in_inventory_environment',
            })
    return components, sorted(set(notes)), unsatisfied


def _remote_worker_components(root: Path) -> list[dict]:
    """The worker image is built by the server owner from the shipped Dockerfile."""
    components = []
    for line in _requirement_lines(root / REMOTE_WORKER_LOCK):
        match = re.match(r'^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;]+)', line)
        if match:
            components.append({'category': 'python_remote_worker', 'name': _canonical(match.group(1)), 'version': match.group(2),
                               'license': 'see the package metadata of the pinned version', 'source': REMOTE_WORKER_LOCK,
                               'distribution': 'dockerfile_built_by_server_owner', 'notices_required': True, 'bundled': False,
                               'status': 'needs_review'})
    dockerfile = root / REMOTE_WORKER_DOCKERFILE
    if dockerfile.is_file():
        text = dockerfile.read_text(encoding='utf-8')
        base = re.search(r'ARG BASE_IMAGE=(\S+)', text)
        components.append({'category': 'python_remote_worker', 'name': 'base_image', 'version': base.group(1) if base else None,
                           'license': 'container base image with CUDA libraries under their own terms',
                           'source': REMOTE_WORKER_DOCKERFILE, 'distribution': 'dockerfile_built_by_server_owner',
                           'notices_required': True, 'bundled': False, 'status': 'needs_review'})
        specs = re.findall(r'"([A-Za-z0-9._-]+)([<>=!~][^"]*)"', text)
        # Unquoted pins inside pip install commands, e.g. torchvision==0.21.0+cu126.
        for block in re.findall(r'RUN[^\n]*pip install(?:[^\n]*\\\n)*[^\n]*', text):
            specs += re.findall(r'(?<![\w"\'=-])([A-Za-z][A-Za-z0-9._-]*)(==[^\s"\'\\]+)', block)
        seen_specs = set()
        for name, spec in specs:
            if (_canonical(name), spec) in seen_specs:
                continue
            seen_specs.add((_canonical(name), spec))
            components.append({'category': 'python_remote_worker', 'name': _canonical(name), 'version': spec,
                               'license': 'see the package metadata of the installed version', 'source': REMOTE_WORKER_DOCKERFILE,
                               'distribution': 'dockerfile_built_by_server_owner', 'notices_required': True, 'bundled': False,
                               'status': 'needs_review'})
    return components


def _share_worker_licenses(components: list[dict]) -> None:
    """Worker pins reuse the license and status recorded for the same package."""
    known = {item['name']: item for item in components if item['category'] == 'python_runtime' and item['version']}
    for item in components:
        if item['category'] == 'python_remote_worker' and item['name'] in known:
            item['license'] = known[item['name']]['license'] + ' (as recorded for the inventory interpreter version)'
            item['status'] = known[item['name']]['status']


def _cached_worker_weights(root: Path) -> set[str]:
    path = root / CACHED_WEIGHTS
    if not path.is_file():
        return set()
    return set(re.findall(r'"(\w+)":\s*[\w.]+_Weights\.DEFAULT', path.read_text(encoding='utf-8')))


def _weight_components(root: Path) -> list[dict]:
    components = []
    for name, model_id in DINO_WEIGHTS.items():
        components.append({
            'category': 'model_weights', 'name': name, 'version': model_id, 'license': DINO_LICENSE,
            'license_source': DINO_LICENSE_SOURCE, 'source': f'https://huggingface.co/timm/{model_id}',
            'distribution': 'runtime_download_from_provider + user_import', 'bundled': False, 'notices_required': True,
            'use': DINO_USES, 'status': 'needs_decision',
            'conditions': 'Downloaded at first use from the timm repositories on Hugging Face, which publish the weights '
                          'under the DINOv3 License, or imported by the user. The license\'s '
                          'redistribution conditions and use restrictions must be reviewed before any pack or derived '
                          'model is published.',
        })
    for name in YOLO_WEIGHTS:
        components.append({
            'category': 'model_weights', 'name': name, 'version': f'{name}.pt', 'license': YOLO_LICENSE,
            'license_source': YOLO_LICENSE_SOURCE, 'source': 'https://github.com/ultralytics/assets/releases',
            'distribution': 'runtime_download_from_provider + user_import', 'bundled': False, 'notices_required': True,
            'use': 'default detection backbone', 'status': 'needs_decision',
            'conditions': 'The vendor states that its license terms extend to trained weights and derived models.',
        })
    cached = _cached_worker_weights(root)
    for name, use in TORCHVISION_WEIGHTS.items():
        distribution = 'runtime_download_from_provider' + (' + remote_worker_image_cache' if name in cached else '')
        components.append({
            'category': 'model_weights', 'name': name, 'version': f'torchvision {name} DEFAULT',
            'license': TORCHVISION_LICENSE, 'source': 'https://download.pytorch.org/models/',
            'distribution': distribution, 'bundled': False, 'notices_required': True, 'use': use, 'status': 'needs_review',
            'conditions': ('The worker Dockerfile caches a copy inside the image; publishing or sharing that image would '
                           'redistribute it.') if name in cached else None,
        })
    for name, (description, source) in USER_CONFIGURED_MODELS.items():
        components.append({
            'category': 'model_weights', 'name': name, 'version': None,
            'license': 'Determined by the model the user configures; not established by this inventory',
            'source': source, 'distribution': 'user_configured_local_directory', 'bundled': False,
            'notices_required': True, 'use': description, 'status': 'needs_review',
            'conditions': 'The project does not download or ship these models.',
        })
    return components


TRAINED_MODELS = [
    ('yolo_derived', 'Models fine-tuned from the YOLO weights', 'Inherits the vendor terms stated for YOLO weights', 'needs_decision'),
    ('dinov3_derived', 'Heads and fine-tuned models built on DINOv3 weights', 'Derivative terms of the DINOv3 License apply', 'needs_decision'),
    ('torchvision_derived', 'Models fine-tuned from torchvision weights', 'Weight and training-data terms not established', 'needs_review'),
    ('first_party_scratch', 'OCR, rotation, enhancement and GAN models trained from scratch', 'First-party code; rights in the user training data apply', 'needs_review'),
]


def _first_party_components(root: Path, notes: list[str]) -> list[dict]:
    package = json.loads((root / 'package.json').read_text(encoding='utf-8'))
    components = [{
        'category': 'first_party', 'name': 'modu-vision', 'version': package['version'], 'license': 'MIT', 'source': 'LICENSE',
        'distribution': 'source_and_installer', 'bundled': True, 'notices_required': True, 'status': 'resolved',
    }]
    tracked = _git(root, 'ls-files')
    if tracked is None:
        notes.append('git is unavailable; tracked first-party assets were not listed')
    for asset in sorted(path for path in (tracked or '').splitlines()
                        if re.search(r'\.(png|jpe?g|bmp|tiff?|webp|icns|ico|svg|ttf|otf|woff2?)$', path, re.I)):
        components.append({
            'category': 'first_party_asset', 'name': asset, 'version': None,
            'license': 'Project asset; license or trademark terms not stated', 'source': asset,
            'distribution': 'source_and_installer', 'bundled': True, 'notices_required': False, 'status': 'needs_review',
        })
    components.append({
        'category': 'sample_data', 'name': 'synthetic_generator_output', 'version': None,
        'license': 'Generated locally by first-party code', 'source': 'backend/engine/synthetic_generator.py',
        'distribution': 'generated_locally', 'bundled': False, 'notices_required': False, 'status': 'resolved',
    })
    for name, description, terms, status in TRAINED_MODELS:
        components.append({
            'category': 'trained_model', 'name': name, 'version': None, 'license': terms, 'source': description,
            'distribution': 'user_export_package', 'bundled': False, 'notices_required': True, 'status': status,
        })
    return components


def scan_frozen_build(build: Path, pyz_toc: Path | None = None) -> tuple[list[dict], dict]:
    """Inventory a PyInstaller onedir build: dist-info metadata and native libraries.

    Pure-Python modules live in an archive inside the executable without their
    metadata; they are listed only when the build's PYZ table of contents is
    supplied, and remain an open item otherwise.
    """
    build = Path(build)
    if not build.is_dir():
        raise ValueError(f'Frozen build folder does not exist: {build}')
    internal = build / '_internal'
    if not internal.is_dir() and not any((build / name).is_file() for name in ('vision_ai_backend', 'vision_ai_backend.exe')):
        raise ValueError(f'Not a PyInstaller onedir backend build: {build}')
    internal = internal if internal.is_dir() else build
    components = []
    for info in sorted(internal.rglob('*.dist-info')):
        meta_path = info / 'METADATA'
        if not meta_path.is_file():
            continue
        meta = email.message_from_string(meta_path.read_text(encoding='utf-8', errors='replace'))
        license_text = _python_license(meta)
        components.append({
            'category': 'frozen_backend_package', 'name': _canonical(meta.get('Name') or info.name), 'version': meta.get('Version'),
            'license': license_text, 'source': str(info.relative_to(build)), 'distribution': 'frozen_backend',
            'notices_required': True, 'bundled': True, 'status': license_status(license_text),
        })
    for library in sorted(path for path in internal.rglob('*')
                          if path.is_file() and re.search(r'\.(so(\.\d+)*|dylib|dll|pyd)$', path.name, re.I)):
        status, reason = 'needs_review', 'native library without license metadata'
        for pattern, family_status, family_reason in NATIVE_FAMILIES:
            if pattern.search(library.name):
                status, reason = family_status, family_reason
                break
        components.append({
            'category': 'frozen_backend_native', 'name': library.name, 'version': None, 'license': reason,
            'source': str(library.relative_to(build)), 'distribution': 'frozen_backend', 'notices_required': True,
            'bundled': True, 'status': status,
        })
    coverage = {'scanned': True, 'dist_info': sum(item['category'] == 'frozen_backend_package' for item in components),
                'native_libraries': sum(item['category'] == 'frozen_backend_native' for item in components),
                'pyz_toc': bool(pyz_toc), 'unmapped_modules': None}
    if pyz_toc:
        modules = _pyz_top_level_modules(Path(pyz_toc))
        known = {item['name'] for item in components if item['category'] == 'frozen_backend_package'}
        mapping = metadata.packages_distributions()
        unmapped = []
        for module in sorted(modules):
            distributions = mapping.get(module)
            if not distributions:
                unmapped.append(module)
                continue
            for name in distributions:
                if _canonical(name) in known:
                    continue
                known.add(_canonical(name))
                try:
                    dist = metadata.distribution(name)
                    license_text, version = _python_license(dist.metadata), dist.version
                except metadata.PackageNotFoundError:
                    license_text, version = 'UNKNOWN', None
                components.append({
                    'category': 'frozen_backend_module_package', 'name': _canonical(name), 'version': version,
                    'license': license_text, 'source': f'PYZ module {module} (metadata from the inventory interpreter)',
                    'distribution': 'frozen_backend', 'notices_required': True, 'bundled': True,
                    'status': license_status(license_text),
                })
        for module in unmapped:
            components.append({
                'category': 'frozen_backend_module_unmapped', 'name': module, 'version': None,
                'license': 'no distribution metadata found for this bundled module', 'source': 'PYZ table of contents',
                'distribution': 'frozen_backend', 'notices_required': True, 'bundled': True, 'status': 'needs_review',
            })
        coverage['unmapped_modules'] = len(unmapped)
    return components, coverage


def _pyz_top_level_modules(toc: Path) -> set[str]:
    data = ast.literal_eval(toc.read_text(encoding='utf-8', errors='replace'))
    entries = data[1] if isinstance(data, tuple) and len(data) > 1 and isinstance(data[1], list) else data
    first_party = {'backend', 'scripts', '__main__'}
    names = set()
    for entry in entries:
        name = entry[0] if isinstance(entry, (list, tuple)) else str(entry)
        top = name.split('.', 1)[0]
        if top and top not in first_party and top not in sys.stdlib_module_names and not top.startswith('_'):
            names.add(top)
    return names


CONDITIONS = [
    'Separating code into modules or plugins (module separation) does not by itself remove license obligations; '
    'the S6-11 decision must cover each distributed artifact as a whole.',
    'Packs whose license is unresolved are not distributed. Users import weights they obtained lawfully; the training '
    'workspace import records the file hash, stored path and task/model, but not the origin or license.',
    'Python dependencies are not locked. The Python section reflects the inventory interpreter and platform and must be '
    'regenerated from the locked release build environment, including platform-specific wheels.',
    'The notices and every component\'s license text must ship with the installer; the current packaging does not yet '
    'do so (electron-builder removes Electron\'s LICENSE files on macOS and the notices file is not packaged).',
    'LICENSE is unchanged by this inventory; the public distribution license is decided in S6-11.',
]


def build_inventory(root: Path = ROOT, *, frozen_build: Path | None = None, pyz_toc: Path | None = None) -> dict:
    root = Path(root).resolve()
    notes: list[str] = []
    python_components, python_notes, unsatisfied = _python_components(root)
    notes.extend(python_notes)
    components = (_first_party_components(root, notes) + _npm_components(root) + python_components
                  + _remote_worker_components(root) + _weight_components(root))
    frozen_receipt = {'scanned': False, 'reason': 'pass --frozen-build to inventory a built backend'}
    if frozen_build:
        frozen, frozen_receipt = scan_frozen_build(Path(frozen_build), pyz_toc)
        components += frozen
    _share_worker_licenses(components)
    components.sort(key=lambda item: (item['category'], item['name'], str(item.get('group') or ''), str(item['version'] or '')))
    unresolved = [{'id': f'{item["category"]}:{item["name"]}', 'status': item['status'],
                   'license': item['license'], 'distribution': item['distribution']}
                  for item in components if item['status'] not in ('resolved', 'not_distributed')]
    unresolved += [
        {'id': 'python_environment:unlocked', 'status': 'needs_review', 'license': None, 'distribution': 'frozen_backend'},
        {'id': 'license_texts:packaging', 'status': 'needs_review', 'license': None, 'distribution': 'installer'},
    ]
    if unsatisfied:
        unresolved.append({'id': 'python_environment:requirements_not_satisfied', 'status': 'needs_review',
                           'license': None, 'distribution': 'frozen_backend', 'details': unsatisfied})
    if not frozen_build:
        unresolved.append({'id': 'frozen_backend:inventory_required', 'status': 'needs_review', 'license': None,
                           'distribution': 'frozen_backend'})
    elif not frozen_receipt.get('pyz_toc') or frozen_receipt.get('unmapped_modules'):
        unresolved.append({'id': 'frozen_backend:pure_python_modules_not_mapped', 'status': 'needs_review', 'license': None,
                           'distribution': 'frozen_backend'})
    unresolved.append({'id': 'python_remote_worker:transitive_dependencies_not_listed', 'status': 'needs_review',
                       'license': None, 'distribution': 'dockerfile_built_by_server_owner'})
    inputs = {name: _sha256(root / name) for name in
              ['package-lock.json', PYTHON_RUNTIME, REMOTE_WORKER_LOCK, REMOTE_WORKER_DOCKERFILE, CACHED_WEIGHTS,
               *PYTHON_OPTIONAL.values()]}
    counts: dict[str, int] = {}
    for item in components:
        counts[item['category']] = counts.get(item['category'], 0) + 1
    return {
        'schema': SCHEMA, 'receipt': 'InventoryReceipt', 'contract': 'LicenseInventory', 'decision_task': 'S6-11',
        'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': {'commit': _git(root, 'rev-parse', 'HEAD'), 'dirty': bool(_git(root, 'status', '--porcelain'))},
        'inputs': inputs,
        'environment': {'python': '.'.join(map(str, sys.version_info[:3])), 'platform': sys.platform,
                        'requirements_satisfied': not unsatisfied},
        'frozen_backend': frozen_receipt,
        'components': components, 'conditions': CONDITIONS + notes, 'unresolved_items': unresolved, 'counts': counts,
    }


def check_notices(receipt: dict, text: str, root: Path = ROOT) -> list[str]:
    """Locked npm entries must match exactly; Python entries are checked by declared name."""
    missing = []
    for item in receipt['components']:
        if item['category'] in ('npm_runtime', 'npm_bundled_build_output', 'electron_runtime') and item['notices_required']:
            if f'| {item["name"]} | {item["version"]} |' not in text:
                missing.append(f'{item["category"]}:{item["name"]}')
    for line in _requirement_lines(root / PYTHON_RUNTIME):
        name = _canonical(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]*', line).group(0))
        if f'| {name} |' not in text:
            missing.append(f'python_runtime:{name}')
    return missing


def _table(rows: list[dict]) -> list[str]:
    lines = ['| Component | Version | License | Status | Source |', '| --- | --- | --- | --- | --- |']
    for item in rows:
        license_text = str(item['license']).replace('|', '/')
        lines.append(f'| {item["name"]} | {item["version"]} | {license_text} | {item["status"]} | {item.get("source") or ""} |')
    return lines


def render_notices(receipt: dict) -> str:
    by = lambda category: [item for item in receipt['components'] if item['category'] == category]
    environment = receipt['environment']
    satisfied = 'satisfies' if environment['requirements_satisfied'] else 'does NOT satisfy'
    lines = [
        '# Third-party notices', '',
        'Generated by `scripts/license_inventory.py` from `package-lock.json` and a Python inventory interpreter. '
        'Regenerate after dependency changes instead of editing by hand.', '',
        'This file lists third-party components that the desktop application distributes and their declared licenses. '
        'It is review input, not a legal compatibility determination; the public distribution decision is tracked '
        'separately. Each component\'s full license text must ship with the installer (npm package `LICENSE` files, '
        'Python package license files, Electron `LICENSE` and `LICENSES.chromium.html`); the current packaging does not '
        'yet include them, which is recorded as an open item below.', '',
        '## Project', '', '- modu-vision: MIT (see `LICENSE`)', '',
        '## Electron runtime', '', *_table(by('electron_runtime')), '',
        '## Renderer runtime (npm)', '', *_table(by('npm_runtime')), '',
        '## Build tools whose code is emitted into the renderer bundle', '', *_table(by('npm_bundled_build_output')), '',
        f'## Python backend runtime (inventory interpreter: Python {environment["python"]} on {environment["platform"]})', '',
        f'Python dependencies are not locked. This interpreter {satisfied} `requirements.txt`; the list below is the '
        'declared dependency closure in that interpreter and must be regenerated from the locked release build '
        'environment for each target platform. A built backend can contain more packages and native libraries; '
        '`--frozen-build` lists its package metadata and native libraries, and `--pyz-toc` the pure-Python modules '
        'bundled inside the executable.', '',
        *_table(by('python_runtime')), '',
        '## Not bundled with the installer', '',
        '- Model weights are not bundled. See `docs/model-license-matrix.md` for their sources and terms.',
        '- Optional packs (DICOM, OpenVINO, semantic labeling) and the remote worker image built from '
        '`build/remote/Dockerfile` are listed in the inventory receipt.',
        '', '## Open items before public distribution', '',
        *[f'- `{item["id"]}`: {item["status"]}' for item in receipt['unresolved_items']
          if item['id'].split(':', 1)[0] in DISTRIBUTED_THIRD_PARTY + ('python_environment', 'license_texts', 'frozen_backend')],
        '',
    ]
    return '\n'.join(lines)


def render_matrix(receipt: dict) -> str:
    weights = [item for item in receipt['components'] if item['category'] == 'model_weights']
    trained = [item for item in receipt['components'] if item['category'] == 'trained_model']
    lines = [
        '# Model weight license matrix', '',
        'Generated by `scripts/license_inventory.py`. This is review input for the public distribution decision, not a '
        'legal determination. No weights are bundled with the installer or source.', '',
        '## Pretrained weights', '',
        '| Weights | Identifier | Used for | License | Distribution | Status | Source |',
        '| --- | --- | --- | --- | --- | --- | --- |',
    ]
    for item in weights:
        lines.append(f'| {item["name"]} | {item["version"] or ""} | {item.get("use") or ""} | {item["license"]} | '
                     f'{item["distribution"]} | {item["status"]} | {item["source"]} |')
    lines += ['', '## Conditions recorded for review', '']
    for item in weights:
        if item.get('conditions'):
            lines.append(f'- `{item["name"]}`: {item["conditions"]}'
                         + (f' License text: {item["license_source"]}' if item.get('license_source') else ''))
    lines += ['', '## Trained and exported models', '',
              '| Model kind | Description | Terms recorded | Status |', '| --- | --- | --- | --- |']
    for item in trained:
        lines.append(f'| {item["name"]} | {item["source"]} | {item["license"]} | {item["status"]} |')
    lines += ['', '## Distribution policy inputs', '',
              '- Weights with an unresolved license are not published as packs. Users obtain them from the provider or '
              'import a file they obtained lawfully; the import records the file hash, stored path and task/model.',
              '- `runtime_download_from_provider` means the application downloads the weights on the user\'s machine at '
              'first use from the public repository named in the Source column.',
              '- `remote_worker_image_cache` means the worker Dockerfile caches a copy inside the image that a server owner '
              'builds; publishing or sharing such an image would redistribute the weights.',
              '- `user_configured_local_directory` means the user supplies the model; the project neither downloads nor ships it.',
              '- Module or plugin separation alone is not treated as removing obligations.', '']
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--output', type=Path, help='write the InventoryReceipt JSON here (default: stdout)')
    parser.add_argument('--frozen-build', type=Path, help='a built backend folder to inventory as shipped')
    parser.add_argument('--pyz-toc', type=Path, help="the build's PYZ-00.toc listing modules bundled in the executable")
    parser.add_argument('--write-notices', type=Path)
    parser.add_argument('--write-matrix', type=Path)
    parser.add_argument('--check-notices', type=Path)
    args = parser.parse_args(argv)
    try:
        receipt = build_inventory(ROOT, frozen_build=args.frozen_build, pyz_toc=args.pyz_toc)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.write_notices:
        args.write_notices.write_text(render_notices(receipt), encoding='utf-8')
    if args.write_matrix:
        args.write_matrix.write_text(render_matrix(receipt), encoding='utf-8')
    if args.check_notices:
        missing = check_notices(receipt, args.check_notices.read_text(encoding='utf-8'))
        if missing:
            print('Missing notices: ' + ', '.join(missing), file=sys.stderr)
            return 1
    text = json.dumps(receipt, indent=2, ensure_ascii=False) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    elif not (args.write_notices or args.write_matrix or args.check_notices):
        sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

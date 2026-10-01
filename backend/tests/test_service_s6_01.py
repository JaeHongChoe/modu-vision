"""S6-01: component, dependency and model-weight license inventory.

The inventory is review input for the public distribution decision (S6-11).
It must never present an unrecognised or unresolved condition as cleared.
"""
from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
# S6-01 must not change the project license; S6-11 decides distribution.
LICENSE_SHA256 = '654918d8bec2a70f2023728c32a631f2bb901834354878eea40ee380f3d10864'


def _inventory(**kwargs):
    from scripts.license_inventory import build_inventory
    return build_inventory(ROOT, **kwargs)


def _component(receipt, category, name):
    matches = [item for item in receipt['components'] if item['category'] == category and item['name'] == name]
    assert matches, f'{category}:{name} missing'
    return matches[0]


@pytest.mark.parametrize(('text', 'expected'), [
    ('MIT', 'resolved'), ('BSD-3-Clause', 'resolved'), ('Apache-2.0', 'resolved'), ('MIT License', 'resolved'),
    ('MIT OR Apache-2.0', 'resolved'), ('BSD License AND Apache Software License', 'resolved'),
    ('GPLv3', 'needs_decision'), ('GNU General Public License v3 (GPLv3)', 'needs_decision'),
    ('GNU Affero General Public License v3', 'needs_decision'), ('AGPLv3', 'needs_decision'),
    ('AGPL-3.0', 'needs_decision'), ('GPL-2.0 WITH Classpath-exception-2.0', 'needs_decision'),
    ('MPL-2.0 AND AGPL-3.0', 'needs_decision'),
    ('LGPLv2+', 'needs_review'), ('GNU Lesser General Public License v3 (LGPLv3)', 'needs_review'), ('MPL-2.0', 'needs_review'),
    ('MIT OR GPL-3.0', 'needs_review'),
    ('CC-BY-NC-4.0', 'needs_decision'), ('NVIDIA Proprietary Software', 'needs_decision'),
    ('Other/Proprietary License', 'needs_decision'), ('UNLICENSED', 'needs_decision'), ('SEE LICENSE IN LICENSE', 'needs_decision'),
    ('BSL-1.1', 'needs_decision'),
    ('Dual License', 'needs_review'), ('unknown', 'needs_review'), ('UNKNOWN', 'needs_review'), ('', 'needs_review'),
    ('OSI Approved', 'needs_review'),
    ('MIT AND (GPL-3.0 OR AGPL-3.0)', 'needs_decision'), ('Apache-2.0 AND (GPL-2.0-only OR GPL-3.0-only)', 'needs_decision'),
    ('BSD-3-Clause AND (NVIDIA Proprietary OR Commercial)', 'needs_decision'), ('(MIT OR GPL-3.0) AND BSD-3-Clause', 'needs_review'),
    ('BSD License AND GNU General Public License v3 (GPLv3)', 'needs_decision'), ('ISC License (ISCL)', 'resolved'),
])
def test_license_status_clears_only_allowlisted_terms(text, expected):
    from scripts.license_inventory import license_status
    assert license_status(text) == expected


@pytest.mark.parametrize(('text', 'expected'), [
    # An exception changes the license terms; only recognised permission-granting exceptions keep the base status.
    ('MIT WITH Unknown-exception', 'needs_review'), ('MIT WITH LicenseRef-proprietary-condition', 'needs_decision'),
    ('MIT WITH', 'needs_review'), ('MIT WITH LLVM-exception WITH Classpath-exception-2.0', 'needs_review'),
    ('Apache-2.0 WITH LLVM-exception', 'resolved'), ('MIT OR Apache-2.0 WITH Unknown-exception', 'needs_review'),
    ('GPL-2.0-only WITH Classpath-exception-2.0', 'needs_decision'), ('LGPL-2.1 WITH Unknown-exception', 'needs_review'),
    # Unbalanced expressions are malformed and never cleared; a copyleft or restricted term still needs a decision.
    ('(MIT', 'needs_review'), ('MIT OR (Apache-2.0', 'needs_review'), ('MIT)', 'needs_review'), ('(MIT))', 'needs_review'),
    (')MIT(', 'needs_review'), ('((MIT)', 'needs_review'), ('GNU General Public License v3 (GPLv3', 'needs_decision'),
    ('MIT AND (Commercial', 'needs_decision'),
])
def test_license_exceptions_and_malformed_expressions_fail_closed(text, expected):
    from scripts.license_inventory import license_status
    assert license_status(text) == expected


def test_inventory_separates_first_party_dependencies_weights_trained_models_and_samples():
    receipt = _inventory()
    assert receipt['receipt'] == 'InventoryReceipt' and receipt['decision_task'] == 'S6-11'
    categories = {item['category'] for item in receipt['components']}
    assert {'first_party', 'npm_runtime', 'npm_build_tool', 'npm_bundled_build_output', 'electron_runtime', 'python_runtime',
            'python_remote_worker', 'model_weights', 'trained_model', 'first_party_asset', 'sample_data'} <= categories
    own = _component(receipt, 'first_party', 'modu-vision')
    assert own['license'] == 'MIT' and own['status'] == 'resolved'
    assert _component(receipt, 'npm_runtime', 'react')['distribution'] == 'bundled_in_renderer'
    assert _component(receipt, 'npm_build_tool', 'electron-builder')['distribution'] == 'not_distributed'
    assert _component(receipt, 'npm_bundled_build_output', 'tailwindcss')['notices_required'] is True
    electron = _component(receipt, 'electron_runtime', 'electron')
    assert electron['distribution'] == 'installer_runtime' and electron['status'] == 'needs_review'
    assert 'Chromium' in electron['license']


def test_agpl_dependency_and_vendor_weights_are_unresolved_not_cleared():
    receipt = _inventory()
    ultralytics = _component(receipt, 'python_runtime', 'ultralytics')
    try:
        metadata.version('ultralytics')
    except metadata.PackageNotFoundError:
        assert ultralytics['status'] == 'not_installed_in_inventory_environment'
    else:
        assert 'AGPL' in ultralytics['license'] and ultralytics['status'] == 'needs_decision'
    yolo = _component(receipt, 'model_weights', 'yolo26n')
    assert yolo['bundled'] is False and yolo['status'] == 'needs_decision'
    unresolved = {item['id'] for item in receipt['unresolved_items']}
    assert {'python_runtime:ultralytics', 'model_weights:yolo26n', 'model_weights:dinov3_vits16', 'trained_model:yolo_derived',
            'python_environment:unlocked', 'license_texts:packaging', 'frozen_backend:inventory_required'} <= unresolved
    assert any('module separation' in condition for condition in receipt['conditions'])


def test_dinov3_weights_are_downloaded_from_the_provider_and_never_bundled():
    receipt = _inventory()
    for name in ('dinov3_vits16', 'dinov3_vitb16', 'dinov3_vitl16'):
        weight = _component(receipt, 'model_weights', name)
        assert weight['bundled'] is False
        assert weight['distribution'].startswith('runtime_download_from_provider')
        assert 'DINOv3 License' in weight['license'] and weight['status'] == 'needs_decision'
        assert weight['source'].startswith('https://huggingface.co/timm/')


def test_worker_image_contents_are_inventoried_from_its_own_pins_and_cache_is_conditional():
    receipt = _inventory()
    worker = {item['name']: item for item in receipt['components'] if item['category'] == 'python_remote_worker'}
    assert worker['ultralytics']['version'] == '8.4.41' and worker['base_image']['version'].startswith('pytorch/pytorch@sha256:')
    assert 'onnx' in worker and worker['torchvision']['version'] == '==0.21.0+cu126', 'unquoted pins are captured'
    runtime = _component(receipt, 'python_runtime', 'ultralytics')
    assert worker['ultralytics']['status'] == runtime['status'], 'the same package has the same status in the worker image'
    cached = [item for item in receipt['components'] if item['category'] == 'model_weights'
              and 'remote_worker_image_cache' in item['distribution']]
    assert {item['name'] for item in cached} >= {'resnet18', 'deeplabv3_resnet50'}
    assert all('would redistribute' in item['conditions'] for item in cached)


def test_weight_inventory_stays_in_sync_with_the_code():
    receipt = _inventory()
    names = {item['name'] for item in receipt['components'] if item['category'] == 'model_weights'}
    backbones = (ROOT / 'backend' / 'engine' / 'model_backbones.py').read_text(encoding='utf-8')
    dino = set(re.findall(r"'(dinov3_vit\w+)':\s*'", backbones))
    yolo = set(re.findall(r"'(yolo\d+\w)'", re.search(r'YOLO_MODELS = \(([^)]*)\)', backbones).group(1)))
    torchvision = set()
    for source in (ROOT / 'backend' / 'engine').rglob('*.py'):
        for enum in re.findall(r'\b(\w+)_Weights\.DEFAULT', source.read_text(encoding='utf-8')):
            torchvision.add(enum.lower())
    assert dino and yolo and torchvision
    assert dino | yolo | torchvision <= names, (dino | yolo | torchvision) - names


def test_frozen_build_scan_reports_packages_and_native_libraries(tmp_path):
    from scripts.license_inventory import scan_frozen_build
    internal = tmp_path / 'vision_ai_backend' / '_internal'
    info = internal / 'gplpkg-1.0.dist-info'
    info.mkdir(parents=True)
    (info / 'METADATA').write_text('Metadata-Version: 2.4\nName: gplpkg\nVersion: 1.0\nLicense-Expression: GPL-3.0-only\n')
    for name in ('libx264.164.dylib', 'libavcodec.61.dylib', 'libpython3.13.dylib', 'libsomething.so.1'):
        (internal / name).write_bytes(b'\0')
    (internal / 'libboost_python313.dylib').write_bytes(b'\0')
    components, coverage = scan_frozen_build(tmp_path / 'vision_ai_backend')
    found = {item['name']: item for item in components}
    assert coverage['pyz_toc'] is False and coverage['dist_info'] == 1
    assert found['libboost_python313.dylib']['status'] == 'needs_review', 'name fragments do not imply the interpreter license'
    assert found['gplpkg']['status'] == 'needs_decision' and found['gplpkg']['category'] == 'frozen_backend_package'
    assert found['libx264.164.dylib']['status'] == 'needs_decision'
    assert found['libavcodec.61.dylib']['status'] == 'needs_review'
    assert found['libpython3.13.dylib']['status'] == 'resolved'
    assert found['libsomething.so.1']['status'] == 'needs_review'


def test_frozen_build_scan_refuses_missing_or_foreign_folders_and_keeps_the_open_item(tmp_path):
    from scripts.license_inventory import scan_frozen_build
    with pytest.raises(ValueError, match='does not exist'):
        scan_frozen_build(tmp_path / 'missing')
    (tmp_path / 'plain').mkdir()
    with pytest.raises(ValueError, match='onedir'):
        scan_frozen_build(tmp_path / 'plain')
    result = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'license_inventory.py'), '--frozen-build',
                             str(tmp_path / 'missing'), '--output', str(tmp_path / 'r.json')], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 2 and not (tmp_path / 'r.json').exists()
    build = tmp_path / 'vision_ai_backend'
    (build / '_internal').mkdir(parents=True)
    receipt = _inventory(frozen_build=build)
    assert 'frozen_backend:pure_python_modules_not_mapped' in {item['id'] for item in receipt['unresolved_items']}


def test_pyz_table_of_contents_maps_bundled_modules(tmp_path):
    from scripts.license_inventory import scan_frozen_build
    build = tmp_path / 'vision_ai_backend'
    (build / '_internal').mkdir(parents=True)
    toc = tmp_path / 'PYZ-00.toc'
    toc.write_text(repr(('PYZ-00.pyz', [('backend.main', 'x', 'PYMODULE'), ('json.decoder', 'x', 'PYMODULE'),
                                        ('packaging.version', 'x', 'PYMODULE'), ('zz_unknown_bundle.core', 'x', 'PYMODULE')])))
    components, coverage = scan_frozen_build(build, toc)
    names = {(item['category'], item['name']) for item in components}
    assert ('frozen_backend_module_package', 'packaging') in names
    assert ('frozen_backend_module_unmapped', 'zz_unknown_bundle') in names
    assert not any(item['name'] in ('backend', 'json') for item in components), 'first-party and stdlib modules are excluded'
    assert coverage['unmapped_modules'] == 1


def test_committed_notices_cover_locked_npm_entries_and_declared_python_requirements():
    from scripts.license_inventory import check_notices
    receipt = _inventory()
    text = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text(encoding='utf-8')
    assert check_notices(receipt, text) == []
    assert 'must ship with the installer' in text


def test_committed_matrix_matches_the_inventory():
    from scripts.license_inventory import render_matrix
    assert (ROOT / 'docs' / 'model-license-matrix.md').read_text(encoding='utf-8') == render_matrix(_inventory())


def test_committed_documents_have_no_local_paths_and_license_is_unchanged():
    assert hashlib.sha256((ROOT / 'LICENSE').read_bytes()).hexdigest() == LICENSE_SHA256
    for name in ('THIRD_PARTY_NOTICES.md', 'docs/model-license-matrix.md'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert '/Users/' not in text and str(Path.home()) not in text and 'C:\\Users\\' not in text, name


def test_cli_receipt_is_deterministic_apart_from_its_timestamp(tmp_path):
    outputs = []
    for index in range(2):
        output = tmp_path / f'receipt-{index}.json'
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'license_inventory.py'), '--output', str(output)],
                       cwd=ROOT, check=True, capture_output=True, timeout=300)
        receipt = json.loads(output.read_text(encoding='utf-8'))
        receipt.pop('generated_at')
        receipt.pop('source')
        outputs.append(receipt)
    assert outputs[0] == outputs[1]
    assert '/Users/' not in json.dumps(outputs[0])

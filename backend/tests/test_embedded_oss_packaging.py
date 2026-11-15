"""Core embedded engines must ship as libraries, not require external tools."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_embedded_python_engines_are_pinned_required_frozen_dependencies():
    from scripts.build_backend_binary import DEPENDENCIES
    declared = (ROOT / 'requirements.txt').read_text()
    assert 'optuna==5.0.0' in declared
    assert 'opentelemetry-sdk==1.45.2' in declared
    assert ('optuna', 'optuna', True) in DEPENDENCIES
    assert ('opentelemetry-sdk', 'opentelemetry.sdk.trace', True) in DEPENDENCIES
    assert ('opentelemetry-api', 'opentelemetry.trace', True) in DEPENDENCIES


def test_frozen_command_retains_sampler_sdk_dynamic_modules_and_versions(tmp_path):
    from scripts.build_backend_binary import pyinstaller_command
    args = pyinstaller_command(ROOT, tmp_path, 'Linux')
    for name in ('optuna', 'opentelemetry-sdk', 'opentelemetry-api', 'opentelemetry-semantic-conventions'):
        assert '--copy-metadata=' + name in args
    assert '--collect-submodules=optuna.samplers' in args
    assert '--hidden-import=optuna.storages._in_memory' in args
    assert '--hidden-import=opentelemetry.sdk.trace.export' in args
    assert '--hidden-import=backend.vendor.cleanlab' in args
    assert '--collect-all=optuna' not in args  # optional cloud/dashboard integration is not the embedded sampler


def test_no_collector_otlp_or_external_data_tool_added_to_runtime():
    import json
    package = json.loads((ROOT/'package.json').read_text())
    assert package['dependencies']['@opentelemetry/api'] == '1.9.0'
    assert package['dependencies']['@opentelemetry/sdk-trace'] == '2.12.0'
    requirements = (ROOT/'requirements.txt').read_text().lower()
    assert 'opentelemetry-exporter' not in requirements
    assert 'mlflow' not in requirements and 'fiftyone' not in requirements and 'cleanlab' not in requirements


def test_selected_source_license_bytes_ship_with_web_and_frozen_backend(tmp_path):
    from scripts.build_backend_binary import export_resource_files
    from scripts.license_inventory import _vendored_components, check_notices
    resources = dict(export_resource_files(ROOT))
    for name in ('LICENSE', 'NOTICE', 'provenance.json'):
        original = ROOT/'third_party/cleanlab'/name
        assert resources[original] == 'third_party/cleanlab'
        assert (ROOT/'public/third-party/cleanlab'/name).read_bytes() == original.read_bytes()
    rows = _vendored_components(ROOT)
    assert rows[0]['license'] == 'Apache-2.0' and rows[0]['source_sha256']
    assert rows[0]['name'] == 'cleanlab-normalized-margin'
    assert 'vendored_source:cleanlab-normalized-margin' in check_notices({'components': rows}, '', ROOT)
    assert 'vendored_source:cleanlab-normalized-margin' not in check_notices({'components': rows}, (ROOT/'THIRD_PARTY_NOTICES.md').read_text(), ROOT)

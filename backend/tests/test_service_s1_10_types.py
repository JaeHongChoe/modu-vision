"""S1-10: the renderer's API types are generated from the backend's pydantic models and CI fails when they drift.

scripts/generate_api_types.py writes src/renderer/services/generated/apiTypes.ts (request and response models keyed by
"METHOD /path"); `--check` fails when the committed file differs from a fresh generation.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('generate_api_types', ROOT / 'scripts/generate_api_types.py')
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def _run(*args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts/generate_api_types.py'), *args], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)


def test_json_schema_shapes_become_typescript_types():
    ts = generator.ts_type
    assert ts({'$ref': '#/$defs/Body_upload-file'}) == 'Body_upload_file'
    assert ts({'type': 'string'}) == 'string' and ts({'type': 'integer'}) == 'number' and ts({'type': 'boolean'}) == 'boolean'
    assert ts({'const': 'cpu'}) == '"cpu"' and ts({'enum': ['OK', 'NG', 1]}) == '"OK" | "NG" | 1'
    assert ts({'anyOf': [{'type': 'string'}, {'type': 'null'}]}) == 'string | null'
    assert ts({'anyOf': [{'type': 'string'}, {'type': 'string'}]}) == 'string', 'repeated alternatives are merged'
    assert ts({'allOf': [{'$ref': '#/$defs/A'}, {'$ref': '#/$defs/B'}]}) == 'A & B'
    assert ts({'type': ['string', 'null']}) == 'string | null'
    assert ts({'type': 'array', 'items': {'type': 'number'}}) == 'Array<number>'
    assert ts({'type': 'array', 'prefixItems': [{'type': 'number'}, {'type': 'string'}]}) == '[number, string]'
    assert ts({}) == 'unknown' and ts({'type': 'mystery'}) == 'unknown', 'never any'
    shape = ts({'type': 'object', 'properties': {'job_id': {'type': 'string'}, 'two words': {'type': 'integer'}}, 'required': ['job_id']})
    assert shape == '{\n  job_id: string;\n  "two words"?: number;\n}'
    assert ts({'type': 'object', 'additionalProperties': {'type': 'number'}}) == '{\n  [key: string]: number;\n}'
    assert ts({'type': 'object'}) == '{ [key: string]: unknown }'
    assert ts({'type': 'object', 'additionalProperties': False}) == 'Record<string, never>'


def test_the_committed_types_are_current_and_cover_every_route_with_a_body_model():
    done = _run('--check')
    assert done.returncode == 0, done.stderr
    text = (ROOT / 'src/renderer/services/generated/apiTypes.ts').read_text(encoding='utf-8')
    assert '"POST /api/training/start": TrainingStartRequest;' in text
    assert '"POST /api/workers/local/preflight": PreflightRequest;' in text
    assert 'export type PreflightRequest = {\n  task: string;\n  device?: "cpu" | "cuda" | "mps";' in text
    # String literals (an enum value or a quoted property could be the word any) are not types.
    types_only = __import__('re').sub(r'"(?:[^"\\]|\\.)*"', '""', text)
    assert not __import__('re').search(r'\bany\b', types_only), 'unknown shapes are unknown, never any (also inside Record<...>)'


def test_a_stale_file_fails_the_check_and_names_the_fix(tmp_path):
    stale = tmp_path / 'apiTypes.ts'
    stale.write_text((ROOT / 'src/renderer/services/generated/apiTypes.ts').read_text(encoding='utf-8').replace(
        'export type PreflightRequest = {\n  task: string;', 'export type PreflightRequest = {\n  task: number;'), encoding='utf-8')
    done = _run('--check', '--file', str(stale))
    assert done.returncode == 1 and 'out of date' in done.stderr and 'generate_api_types.py' in done.stderr
    assert 'task: number' in stale.read_text(encoding='utf-8'), 'the check writes nothing'


def test_every_documented_operation_is_seen_by_the_generator(tmp_path, monkeypatch):
    """FastAPI releases differ in how an included router is listed (newer ones keep it as one lazy entry); the
    generator must still see every route the app documents, whichever release runs it."""
    for name in ('VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_FLOW_TEMPLATE_DIR', 'MODU_SPLIT_MANIFEST_DIR', 'MODU_THUMBNAIL_CACHE_DIR'):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    import backend.main as main
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    import re
    # OpenAPI writes a path convertor ({image_path:path}) as the bare parameter.
    seen = {(method, re.sub(r'\{(\w+):\w+\}', r'{\1}', path)) for path, methods, _, _ in generator._api_routes(app) for method in methods}
    documented = {(method.upper(), path) for path, operations in app.openapi()['paths'].items() for method in operations}
    assert len(documented) > 300 and documented <= seen, sorted(documented - seen)[:10]


def test_every_documented_json_request_body_has_its_generated_type(tmp_path, monkeypatch):
    """An optional body (``Model | None``) or any other form the generator might skip would leave a route untyped while
    the drift check stays green; FastAPI's own document says which operations take a JSON body."""
    for name in ('VISION_AI_STUDIO_USER_DATA_DIR', 'MODU_FLOW_TEMPLATE_DIR', 'MODU_SPLIT_MANIFEST_DIR', 'MODU_THUMBNAIL_CACHE_DIR'):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    import re
    import backend.main as main
    app = main.create_app(project_dir=str(tmp_path / 'projects'))
    documented = {f'{method.upper()} {path}' for path, operations in app.openapi()['paths'].items()
                  for method, operation in operations.items()
                  if 'application/json' in (operation.get('requestBody') or {}).get('content', {})}
    text = (ROOT / 'src/renderer/services/generated/apiTypes.ts').read_text(encoding='utf-8')
    block = text[text.index('export interface ApiRequestBody {'):text.index('export interface ApiResponseBody {')]
    generated = {re.sub(r'\{(\w+):\w+\}', r'{\1}', key) for key in re.findall(r'^  "([A-Z]+ [^"]+)":', block, flags=re.M)}
    assert len(documented) > 100 and documented <= generated, sorted(documented - generated)
    assert 'POST /api/product-delivery/diagnostics' in generated, 'an optional body model is typed'


def test_the_generator_leaves_the_working_folder_and_environment_as_they_were(tmp_path):
    before = {path.name for path in tmp_path.iterdir()}
    done = subprocess.run([sys.executable, str(ROOT / 'scripts/generate_api_types.py'), '--check'], cwd=tmp_path,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert {path.name for path in tmp_path.iterdir()} == before, 'importing the backend wrote nothing into the working folder'


def test_the_generator_restores_the_working_folder_and_store_variables_in_process(tmp_path, monkeypatch):
    import os
    # The backend is imported under the session's store folders first: imported inside the generator, its module-level
    # folders would point at the generator's temporary folder after it is removed.
    import backend.main  # noqa: F401
    folder = tmp_path / 'cwd'
    folder.mkdir()
    monkeypatch.chdir(folder)
    monkeypatch.setenv('VISION_AI_STUDIO_USER_DATA_DIR', str(tmp_path / 'mine'))
    monkeypatch.delenv('MODU_FLOW_TEMPLATE_DIR', raising=False)
    before = {name: os.environ.get(name) for name in generator._STORES}
    before_cwd = os.getcwd()
    assert generator.main(['--check']) == 0
    assert os.getcwd() == before_cwd, 'the working folder is the caller\'s again (the temporary one can be removed on Windows)'
    assert {name: os.environ.get(name) for name in generator._STORES} == before, 'set variables keep their value; unset ones stay unset'
    assert list(folder.iterdir()) == []

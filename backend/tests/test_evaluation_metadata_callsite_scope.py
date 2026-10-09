"""Five real metadata call expressions, without evaluation or model execution.

The production call expressions are AST-bound to their enclosing function and
ordinal. The real metadata producer, original persisted revision, UUID/hash and
full project/source/legacy trees are exercised. These controls do not execute a
remote/API/comparison/model entry point or qualify its metrics or target.
"""
import ast
from contextlib import nullcontext
from pathlib import Path

import pytest
from PIL import Image

from backend.api import routes_dataset
from backend.engine.annotation_storage import scoped_annotation_root
from backend.engine.dataset_metadata import metadata_for_path
from backend.tests.test_evaluation_archive_scope import _metadata, _project, _scope, _tree


CALLERS = [
    ('api/routes_evaluation.py', '_run_common_evaluation', 0, 1, 'common'),
    ('api/routes_evaluation.py', 'run_or_load_evaluation', 0, 1, 'legacy'),
    ('api/routes_model_comparisons.py', '_run_comparison', 0, 1, 'comparison'),
    ('engine/evaluation_history.py', 'archive_specialized_evaluation', 0, 2, 'ocr'),
    ('engine/evaluation_history.py', 'archive_specialized_evaluation', 1, 2, 'specialized'),
]


def _call_expression(relative, function_name, ordinal, count):
    path = Path(__file__).resolve().parents[1] / relative
    tree = ast.parse(path.read_text(encoding='utf-8'))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function_name)
    calls = sorted((node for node in ast.walk(function)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == 'metadata_for_path'), key=lambda node: node.lineno)
    assert len(calls) == count
    call = calls[ordinal]
    assert len(call.args) == 4 and not call.keywords
    return compile(ast.fix_missing_locations(ast.Expression(body=call)), str(path), 'eval')


@pytest.mark.parametrize('relative,function_name,ordinal,count,kind', CALLERS)
@pytest.mark.parametrize('request_scope', [True, False], ids=['active-project', 'legacy-fallback'])
def test_evaluation_metadata_call_uses_existing_request_scope_or_legacy_fallback(
        tmp_path, monkeypatch, relative, function_name, ordinal, count, kind, request_scope):
    source = tmp_path / 'source'
    image_path = source / 'test/OK/owned.png'
    image_path.parent.mkdir(parents=True)
    Image.new('RGB', (16, 16), (31, 79, 127)).save(image_path)
    project, legacy = _project(tmp_path, monkeypatch, source)
    # Seed only the intended existing ledger before protected tree snapshots.
    expected_project = project if request_scope else {**project, 'annotations_dir': str(legacy)}
    expected = _metadata(expected_project, source, [image_path])[str(image_path)]
    before = (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))
    environment = {
        'Path': Path, 'project': project, 'project_root': Path(project['project_dir']),
        'source': source, 'bound_source': source, 'cohort': {'source': source},
        'row': {'file_path': str(image_path)}, 'path': str(image_path),
        'image': {'file_path': str(image_path)} if kind == 'comparison' else image_path,
        'original': image_path, 'routes_dataset': routes_dataset,
        'metadata_for_path': metadata_for_path, 'scoped_annotation_root': scoped_annotation_root,
    }
    with _scope(project) if request_scope else nullcontext():
        observed = eval(_call_expression(relative, function_name, ordinal, count), environment)
    assert observed == expected
    assert observed['revision'] == 7 and observed['product'] == 'owned-scoped-history'
    assert before == (_tree(Path(project['project_dir'])), _tree(source), _tree(legacy))

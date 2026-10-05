"""Public source CI must enumerate authentic-weight tests it cannot execute."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def ci_module():
    spec = importlib.util.spec_from_file_location('ci_lane_receipt', ROOT / 'scripts/write_ci_receipt.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(name, tags=(), status=None):
    test = {'projectName': 'browser', 'results': [] if status is None else [{'status': status}]}
    return {'suites': [{'file': 'scripts/e2e/controlled.spec.ts', 'specs': [
        {'id': name, 'title': name, 'tags': list(tags), 'tests': [test]}]}]}


def test_public_collection_excludes_existing_authentic_weight_qualifications():
    public = json.loads(subprocess.check_output(['npx', 'playwright', 'test', '--project=browser',
        '--grep-invert', '@owned-model', '--list', '--reporter=json'], cwd=ROOT, text=True))
    owned = json.loads(subprocess.check_output(['npx', 'playwright', 'test', '--project=browser',
        '--grep', '@owned-model', '--list', '--reporter=json'], cwd=ROOT, text=True))
    def specs(suites):
        for suite in suites:
            yield from suite.get('specs', [])
            yield from specs(suite.get('suites', []))
    public_rows, owned_rows = list(specs(public['suites'])), list(specs(owned['suites']))
    assert len(owned_rows) == 3
    assert all('owned-model' in [tag.lstrip('@') for tag in row['tags']] for row in owned_rows)
    assert not any('owned-model' in [tag.lstrip('@') for tag in row.get('tags', [])] for row in public_rows)
    assert not {row['id'] for row in public_rows} & {row['id'] for row in owned_rows}
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    commands = '\n'.join(step.get('run', '') for step in workflow['jobs']['source']['steps'])
    assert 'test:e2e:browser -- --grep-invert @owned-model' in commands
    assert 'ci-owned-model-selection.json' in commands and '--owned-selection' in commands
    windows = yaml.safe_load((ROOT / '.github/workflows/windows-native.yml').read_text())
    assert any('test:e2e:electron -- --grep-invert @owned-model' in step.get('run', '')
               for job in windows['jobs'].values() for step in job['steps'])


def test_receipt_distinguishes_selected_failed_and_unavailable_owned_models(tmp_path):
    ci = ci_module()
    public, owned = tmp_path / 'public.json', tmp_path / 'owned.json'
    public.write_text(json.dumps(report('controlled public')))
    owned.write_text(json.dumps(report('controlled owned', ['@owned-model'])))
    evidence = tmp_path / 'e2e' / 'one-run'; evidence.mkdir(parents=True)
    (evidence / 'report.json').write_text(json.dumps(report('controlled public', status='failed')))
    observed = ci.receipt(ROOT, tmp_path / 'missing.xml', public_selection=public,
        owned_selection=owned, e2e_root=evidence.parent)
    lane = observed['browser_lane']
    assert lane['status'] == 'failed' and lane['selected_count'] == 1
    assert lane['results'] == {'passed': 0, 'failed': 1, 'skipped': 0, 'not_recorded': 0}
    assert lane['owned_model_tests_not_covered'][0]['title'] == 'controlled owned'
    assert lane['owned_model_reason'] == 'requires explicitly supplied authentic local weights; no public CI credentials or automatic download'
    assert lane['selection_sha256'] and lane['report_sha256']


def test_missing_browser_execution_is_not_a_passing_lane(tmp_path):
    ci = ci_module()
    public, owned = tmp_path / 'public.json', tmp_path / 'owned.json'
    public.write_text(json.dumps(report('controlled public')))
    owned.write_text(json.dumps(report('controlled owned', ['@owned-model'])))
    observed = ci.receipt(ROOT, tmp_path / 'missing.xml', public_selection=public,
        owned_selection=owned, e2e_root=tmp_path / 'absent')
    assert observed['browser_lane']['status'] == 'not_recorded'
    assert observed['browser_lane']['results']['not_recorded'] == 1


def test_receipt_survives_an_earlier_ci_gate_without_browser_selections(tmp_path):
    observed = ci_module().receipt(ROOT, tmp_path / 'missing.xml',
        public_selection=tmp_path / 'missing-public.json', owned_selection=tmp_path / 'missing-owned.json')
    assert observed['browser_lane']['status'] == 'not_recorded'
    assert observed['browser_lane']['selection_status'] == 'not_recorded'


@pytest.mark.parametrize('case', ['overlap', 'unselected_execution', 'ambiguous_execution'])
def test_lane_receipt_refuses_mixed_scope_or_ambiguous_runs(tmp_path, case):
    public, owned = tmp_path / 'public.json', tmp_path / 'owned.json'
    public.write_text(json.dumps(report('public')))
    owned.write_text(json.dumps(report('public' if case == 'overlap' else 'owned', ['@owned-model'])))
    root = tmp_path / 'runs'; first = root / 'one'; first.mkdir(parents=True)
    (first / 'report.json').write_text(json.dumps(report('unselected' if case == 'unselected_execution' else 'public', status='passed')))
    if case == 'ambiguous_execution':
        second = root / 'two'; second.mkdir()
        (second / 'report.json').write_text(json.dumps(report('public', status='passed')))
    with pytest.raises(ValueError):
        ci_module().receipt(ROOT, tmp_path / 'missing.xml', public_selection=public, owned_selection=owned, e2e_root=root)

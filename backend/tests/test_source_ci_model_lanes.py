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
    assert owned_rows, 'authentic-weight qualification lane must be enumerated'
    assert any(row['title'].startswith('all anomaly methods learn from normals') for row in owned_rows)
    assert any(row['title'].startswith('authentic OBB actual empty crowded') for row in owned_rows)
    assert any(row['title'].startswith('actual GAN generation source masks') for row in owned_rows)
    assert all('owned-model' in [tag.lstrip('@') for tag in row['tags']] for row in owned_rows)
    assert not any('owned-model' in [tag.lstrip('@') for tag in row.get('tags', [])] for row in public_rows)
    assert not {row['id'] for row in public_rows} & {row['id'] for row in owned_rows}
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    commands = '\n'.join(step.get('run', '') for job in workflow['jobs'].values() for step in job['steps'])
    assert 'test:e2e:browser -- --grep-invert @owned-model' in commands
    assert 'ci-owned-model-selection.json' in commands and '--owned-selection' in commands
    windows = yaml.safe_load((ROOT / '.github/workflows/windows-native.yml').read_text())
    assert any('test:e2e:electron -- --grep-invert @owned-model' in step.get('run', '')
               for job in windows['jobs'].values() for step in job['steps'])


def test_ci_recording_does_not_dirty_the_checkout_before_actual_gui_source_observation(tmp_path):
    """Execute output placement with controlled producers and the real Git probe."""
    import os,shlex
    checkout=tmp_path/'checkout';checkout.mkdir();(checkout/'source.txt').write_text('Original controlled source')
    for args in [('init','-q'),('add','source.txt'),('-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','controlled source')]:
        subprocess.run(['git',*args],cwd=checkout,check=True,capture_output=True)
    records=tmp_path/'runner-temp/records';records.mkdir(parents=True)
    bin_dir=tmp_path/'controlled-bin';bin_dir.mkdir()
    producers={
        'python': '#!/usr/bin/env python3\nimport pathlib,sys\na=sys.argv[1:]\nif "--output" in a:p=a[a.index("--output")+1]\nelse:p=next(x.split("=",1)[1] for x in a if x.startswith("--junitxml="))\npathlib.Path(p).write_text("Controlled output placement only")\n',
        'npx': '#!/bin/sh\nprintf \'{"suites":[]}\\n\'\n',
        'npm': '#!/bin/sh\nexec node -e '+shlex.quote("const h=require("+json.dumps(str(ROOT/'scripts/e2e/fixtures/harness.cjs'))+");process.stdout.write(JSON.stringify(h.sourceIdentity("+json.dumps(str(checkout))+")));" )+'\n',
    }
    for name,script in producers.items():
        p=bin_dir/name;p.write_text(script);p.chmod(0o700)
    workflow=yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text());steps=[step for job in workflow['jobs'].values() for step in job['steps']]
    environment={**os.environ,'PATH':str(bin_dir)+os.pathsep+os.environ['PATH'],'MV_CI_RECORD_DIR':str(records)}
    observation=None
    for name in ['CPU contract and recovery regressions','Core defect baseline evidence','Browser transport and flow checks']:
        step=next(s for s in steps if s.get('name')==name)
        output=subprocess.check_output(['/bin/sh','-eu','-c',step['run']],cwd=checkout,env=environment,text=True)
        if name.startswith('Browser'):observation=json.loads(output)
    assert observation['dirty'] is False,'CI output files changed the real Git source observation before GUI execution'
    assert all((records/name).is_file() for name in ['ci-pytest.xml','ci-baseline-evidence.json','ci-browser-selection.json','ci-owned-model-selection.json'])
    record_steps=[s for s in steps if s.get('name') in {'CPU contract and recovery regressions','Core defect baseline evidence','Browser transport and flow checks','Record source, toolchain, and license inventory'}]
    assert all(s.get('env',{}).get('MV_CI_RECORD_DIR')=='${{ runner.temp }}/modu-ci-manifests' for s in record_steps)
    preserves=[next(s for s in job['steps'] if s.get('name')=='Preserve evidence')['with']['path'] for job in workflow['jobs'].values()]
    assert all('${{ runner.temp }}/modu-ci-manifests' in preserve for preserve in preserves)


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


def test_actual_reporter_test_root_relative_files_have_exact_source_hashes_and_observed_statuses(tmp_path):
    import hashlib
    ci=ci_module();root=tmp_path/'checkout';test_root=root/'scripts/e2e';test_root.mkdir(parents=True)
    source=test_root/'controlled.spec.ts';source.write_bytes(b'Original selected browser test input')
    public=tmp_path/'public.json';owned=tmp_path/'owned.json';execution=tmp_path/'runs/one';execution.mkdir(parents=True)
    def actual(name,tags=(),status=None):
        value=report(name,tags,status);value['config']={'rootDir':str(test_root)}
        value['suites'][0]['file']='controlled.spec.ts';return value
    public.write_text(json.dumps(actual('public')));owned.write_text(json.dumps(actual('owned',['@owned-model'])))
    (execution/'report.json').write_text(json.dumps(actual('public',status='passed')))
    lane=ci.browser_evidence(root,public,owned,execution.parent)
    assert lane['source_input_sha256']=={'scripts/e2e/controlled.spec.ts':hashlib.sha256(source.read_bytes()).hexdigest()}
    assert lane['selected_tests'][0]['status']=='passed' and lane['selected_tests'][0]['selection_status']=='not_recorded'
    assert lane['source_status']=='recorded' and lane['status']=='passed'


@pytest.mark.parametrize('damage',['missing','linked','foreign_test_root','duplicate_execution','different_source_identity','foreign_execution_root','different_execution_root','parent_in_test_root'])
def test_browser_receipt_does_not_supply_source_identity_from_missing_foreign_or_ambiguous_inputs(tmp_path,damage):
    ci=ci_module();root=tmp_path/'checkout';test_root=root/'scripts/e2e';test_root.mkdir(parents=True)
    source=test_root/'controlled.spec.ts';source.write_bytes(b'Owned original selected source')
    def actual(status=None):
        value=report('public',status=status);value['config']={'rootDir':str(test_root)};value['suites'][0]['file']='controlled.spec.ts';return value
    public=tmp_path/'public.json';owned=tmp_path/'owned.json';public.write_text(json.dumps(actual()));owned.write_text(json.dumps({'config':{'rootDir':str(test_root)},'suites':[]}))
    execution=tmp_path/'runs/one';execution.mkdir(parents=True);value=actual('passed')
    if damage=='missing':source.unlink()
    elif damage=='linked':
        source.unlink();target=tmp_path/'foreign.spec.ts';target.write_bytes(b'Foreign source');source.symlink_to(target)
    elif damage=='foreign_test_root':
        value=actual('passed');p=actual();p['config']['rootDir']=str(tmp_path);public.write_text(json.dumps(p))
    elif damage=='duplicate_execution':value['suites'][0]['specs'].append(dict(value['suites'][0]['specs'][0]))
    elif damage=='foreign_execution_root':value['config']['rootDir']=str(tmp_path)
    elif damage=='different_execution_root':
        other=root/'other';other.mkdir();(other/'controlled.spec.ts').write_bytes(source.read_bytes())
        value['config']['rootDir']=str(other)
    elif damage=='parent_in_test_root':value['config']['rootDir']=str(test_root/'..'/'e2e')
    else:value['suites'][0]['specs'][0]['title']='Different declaration under reused id'
    (execution/'report.json').write_text(json.dumps(value))
    if damage=='missing':
        lane=ci.browser_evidence(root,public,owned,execution.parent)
        assert lane['status']=='incomplete' and lane['source_status']=='missing' and lane['results']['passed']==1
    else:
        with pytest.raises(ValueError):ci.browser_evidence(root,public,owned,execution.parent)


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


def test_all_explicit_ci_pytest_references_exist_and_select_real_functions():
    """A typo must fail before a hosted workflow skips its entire CPU lane."""
    import ast,re
    for workflow in ['ci.yml','windows-native.yml']:
        value=yaml.safe_load((ROOT/'.github/workflows'/workflow).read_text())
        references=set()
        def strings(node):
            if isinstance(node,str):yield node
            elif isinstance(node,dict):
                for item in node.values():yield from strings(item)
            elif isinstance(node,list):
                for item in node:yield from strings(item)
        # Windows stores its selection in matrix.include.tests, then injects it
        # into the runner command. Inspect both literal steps and matrix values.
        for command in strings(value['jobs']):
            references.update(re.findall(r'(?:backend/tests|scripts/tests)/[A-Za-z0-9_./-]+\.py(?:::[A-Za-z0-9_]+)*',command))
        assert references,workflow
        for reference in references:
            file,*selectors=reference.split('::');path=ROOT/file
            assert path.is_file(),f'{workflow}: missing pytest path {reference}'
            nodes=ast.parse(path.read_text(encoding='utf-8')).body
            for index,selector in enumerate(selectors):
                name=selector.split('[',1)[0]
                node=next((n for n in nodes if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)) and n.name==name),None)
                if index==len(selectors)-1:
                    assert isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and name.startswith('test_'),reference
                else:assert isinstance(node,ast.ClassDef) and name.startswith('Test'),reference
                nodes=node.body


def test_cpu_and_browser_ci_have_independent_original_bounds_and_complete_commands():
    """Full CPU selection cannot consume the browser lane's original75min."""
    import hashlib
    workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    jobs = workflow['jobs']
    assert set(jobs) == {'source', 'browser'}, 'CPU and browser require distinct hosted jobs'
    assert workflow['permissions'] == {'contents': 'read'}
    assert workflow['concurrency'] == {'group': 'source-${{ github.workflow }}-${{ github.ref }}',
        'queue': 'max', 'cancel-in-progress': False}
    assert 'secrets.' not in json.dumps(workflow)
    def step(job, name):
        values = [value for value in job['steps'] if value.get('name') == name]
        assert len(values) == 1, name
        return values[0]
    for job in jobs.values():
        assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == 75
        assert not any(key in job for key in ('needs', 'if', 'permissions', 'environment', 'strategy'))
        assert job['env'] == {'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}
        checkout = job['steps'][0]
        assert checkout == {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {'persist-credentials': False}}
        assert job['steps'][1] == {'uses': 'actions/setup-node@820762786026740c76f36085b0efc47a31fe5020',
            'with': {'node-version': '24', 'cache': 'npm'}}
        assert job['steps'][2] == {'uses': 'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
            'with': {'python-version': '3.13'}}
        assert step(job, 'Install locked CPU environment')['run'] == 'python -m pip install --require-hashes --only-binary=:all: -r build/ci/requirements-ubuntu-py313-cpu.lock'
        assert any(value.get('run') == 'npm ci' for value in job['steps'])
        assert step(job, 'Prepare isolated CI records')['run'] == 'mkdir -p "${{ runner.temp }}/modu-ci-manifests"'
        assert step(job, 'Check preserved scope and acceptance states')['run'] == 'python scripts/check_service_plan.py\npython scripts/check_action_evidence.py\n'
        evidence = step(job, 'Preserve evidence')
        assert evidence['if'] == 'always()'
        assert evidence['uses'] == 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'
        assert evidence['with']['retention-days'] == 14 and evidence['with']['if-no-files-found'] == 'error'
        assert evidence['with']['path'] == '${{ runner.temp }}/modu-ci-manifests\n${{ runner.temp }}/modu-e2e\n!${{ runner.temp }}/modu-e2e/**/ws/**\n'
        assert step(job, 'Record source, toolchain, and license inventory')['if'] == 'always()'
        assert step(job, 'Record source, toolchain, and license inventory')['env'] == {'MV_CI_RECORD_DIR': '${{ runner.temp }}/modu-ci-manifests'}
    cpu, browser = jobs['source'], jobs['browser']
    assert step(cpu, 'Type checks, renderer regressions, and build') == step(browser, 'Type checks, renderer regressions, and build')
    assert step(cpu, 'Record source, toolchain, and license inventory') == step(browser, 'Record source, toolchain, and license inventory')
    cpu_step = step(cpu, 'CPU contract and recovery regressions')
    assert hashlib.sha256(cpu_step['run'].encode()).hexdigest() == 'bcc48453cbc55193ffc09ddfab2675bc657897eb509143ebb2d52264b71742fe', 'Full original CPU command/selection must be unchanged'
    assert cpu_step['env'] == {'MV_CI_RECORD_DIR': '${{ runner.temp }}/modu-ci-manifests'}
    assert step(cpu, 'Core defect baseline evidence')['run'] == 'python scripts/service_baseline_evidence.py --output "$MV_CI_RECORD_DIR/ci-baseline-evidence.json"'
    assert not any(value.get('name') in {'Install test browser', 'Browser transport and flow checks'} for value in cpu['steps'])
    assert not any(value.get('name') in {'CPU contract and recovery regressions', 'Core defect baseline evidence'} for value in browser['steps'])
    assert step(browser, 'Install test browser')['run'] == 'npx playwright install --with-deps chromium'
    browser_step = step(browser, 'Browser transport and flow checks')
    assert hashlib.sha256(browser_step['run'].encode()).hexdigest() == 'a155441e6d8ce4c2a379046d015d1abfc75496abddd6652e36e5d59c385d9085', 'Original public/owned lists and public workers2 execution must be unchanged'
    assert browser_step['env'] == {'MV_E2E_ARTIFACT_DIR': '${{ runner.temp }}/modu-e2e', 'MV_CI_RECORD_DIR': '${{ runner.temp }}/modu-ci-manifests'}
    assert step(cpu, 'Preserve evidence')['with']['name'] == 'linux-cpu-${{ github.sha }}'
    assert step(browser, 'Preserve evidence')['with']['name'] == 'linux-browser-${{ github.sha }}'


@pytest.mark.parametrize('job_name', ['source', 'browser'])
def test_ci_artifact_paths_exclude_all_owned_workspaces_and_retain_original_evidence(job_name):
    """Source contract for the pinned uploader's documented negative-path rules.

    This does not execute the uploader or assert that a hosted artifact is
    secret-free. Local fixtures and failed-trace/report production remain intact.
    """
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    steps = [step for step in workflow['jobs'][job_name]['steps']
             if step.get('name') == 'Preserve evidence']
    assert len(steps) == 1
    evidence = steps[0]
    paths = evidence['with']['path'].splitlines()
    exclusion = '!${{ runner.temp }}/modu-e2e/**/ws/**'
    assert exclusion in paths, 'Private fixture keys, data, models and profiles must not be uploaded'
    assert paths == ['${{ runner.temp }}/modu-ci-manifests',
                     '${{ runner.temp }}/modu-e2e', exclusion]
    assert evidence['uses'] == 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'
    assert evidence['with'].get('include-hidden-files', False) is False
    assert evidence['if'] == 'always()'
    assert evidence['with']['retention-days'] == 14
    assert evidence['with']['if-no-files-found'] == 'error'

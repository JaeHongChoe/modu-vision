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
    # Budgets from measured hosted-runner durations (source up to ~77 min, browser up to 69 min) with ~40% headroom.
    for name, job in jobs.items():
        assert job['runs-on'] == 'ubuntu-24.04' and job['timeout-minutes'] == {'source': 110, 'browser': 100}[name]
        assert not any(key in job for key in ('needs', 'if', 'permissions', 'environment', 'strategy'))
        assert job['env'] == {'OMP_NUM_THREADS': '2', 'MKL_NUM_THREADS': '2'}
        checkout = job['steps'][0]
        assert checkout == {'uses': 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
            'with': {'persist-credentials': False, 'fetch-depth': 0}}
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
    additional = (
        'backend/tests/test_inspection_daemon_recovery.py',
        'backend/tests/test_rotation_lifecycle_evidence.py',
        'backend/tests/test_runtime_update_small_file_reads.py',
        'backend/tests/test_runtime_update_large_file_readinto.py',
        'backend/tests/test_runtime_update_safe_path_bound_regex.py',
        'backend/tests/test_selected_core_cohort_task.py',
        'backend/tests/test_version_metadata_registration.py',
        'backend/tests/test_anomaly_owned_evidence_output.py',
        'backend/tests/test_data_workbench_readonly_storage.py',
        'backend/tests/test_flow_result_bundle.py',
        'backend/tests/test_rotated_detection_lifecycle_evidence.py',
        'backend/tests/test_enhancement_gan_lifecycle_evidence.py',
        'backend/tests/test_gan_generation_lifecycle_evidence.py',
        'backend/tests/test_migration_writer_snapshot_order.py',
        'backend/tests/test_native_compiler_optional_tool_exclusions.py',
        'backend/tests/test_yolo_obb_cpu_device_contract.py',
        'backend/tests/test_labelset_blank_name_boundary.py',
        'backend/tests/test_runtime_spawn_diagnostic.py',
    )
    command = cpu_step['run']
    # These four source controls were published after the original lane. Remove
    # only their exact, contiguous insertion before checking the original bytes.
    source_controls = (
        'backend/tests/test_dataset_import_source_authority.py',
        'backend/tests/test_private_qa_fixtures.py',
        'backend/tests/test_test_store_isolation.py',
        'backend/tests/test_required_flow_current_source_cohort.py',
    )
    source_marker = 'backend/tests/test_dataset_import_contract.py '
    source_insertion = ' '.join(source_controls) + ' '
    assert command.count(source_marker + source_insertion) == 1
    assert all(command.split().count(reference) == 1 for reference in source_controls)
    original_command = command.replace(source_marker + source_insertion, source_marker, 1)
    # Later publications: the owned-lifetime diagnostics after the runtime update recovery control, and the
    # beginner-path controls (truth from labels, anomaly name rules, Korean refusals) after the whole-flow review.
    later_controls = (
        ('backend/tests/test_runtime_update_recovery.py ', (
            'backend/tests/test_first_unsupported_origin_diagnostic.py',
            'backend/tests/test_owned_health_diagnostic.py',
            'backend/tests/test_owned_health_failure_diagnostic.py',
            'backend/tests/test_original_request_route_projection.py',
            'backend/tests/test_flow_open_additional_route_labels.py',
            'backend/tests/test_warm_scoped_get_lifetime.py',
            'backend/tests/test_original_resident_execution_resources.py',
            'backend/tests/test_original_owned_preflight_list_lifetime.py',
            'backend/tests/test_original_owned_flow_change_history_lifetime.py',
            'backend/tests/test_original_owned_flow_lifetime.py',
            'backend/tests/test_flowchart_flow_contract.py',
            'backend/tests/test_application_final_drain_diagnostic.py',
            'backend/tests/test_original_unchanged_flow_save_lifetime.py',
            'backend/tests/test_original_owned_library_no_revision.py',
        )),
        ('backend/tests/test_whole_flow_runtime_review.py ', (
            'backend/tests/test_flow_evaluation_folder_split.py',
            'backend/tests/test_flow_truth_from_labels.py',
            'backend/tests/test_anomaly_name_prefixes.py',
            'backend/tests/test_anomaly_flat_name_layout.py',
            'backend/tests/test_field_export_layouts.py',
            'backend/tests/test_project_activation_race.py',
            'backend/tests/test_managed_service_first_read.py',
            'backend/tests/test_beginner_errors_korean.py',
            'backend/tests/test_workflow_impact.py',
            'backend/tests/test_audit_data_input.py',
            'backend/tests/test_audit_class_overrides.py',
            'backend/tests/test_audit_decision_threshold.py',
            'backend/tests/test_audit_data_changes.py',
            'backend/tests/test_audit_training_setup.py',
            'backend/tests/test_audit_inspection_csv.py',
            'backend/tests/test_audit_rotated_reuse.py',
            'backend/tests/test_audit_flow_score_parity.py',
        )),
    )
    for later_marker, controls in later_controls:
        later_insertion = ' '.join(controls) + ' '
        assert original_command.count(later_marker + later_insertion) == 1, later_marker
        assert all(command.split().count(reference) == 1 for reference in controls), later_marker
        original_command = original_command.replace(later_marker + later_insertion, later_marker, 1)
    history_marker = 'backend/tests/test_gui_execution_evidence.py '
    history_control = 'backend/tests/test_gui_execution_historical_source.py '
    assert original_command.count(history_marker + history_control) == 1
    assert command.split().count(history_control.strip()) == 1
    original_command = original_command.replace(history_marker + history_control, history_marker, 1)
    marker = 'backend/tests/test_service_s5_01.py '
    insertion = ' '.join(additional) + ' '
    assert command.count(marker + insertion) == 1, 'Reviewed eighteen-module addition must remain at its original single insertion point'
    assert all(command.split().count(reference) == 1 for reference in additional), 'Every added verified-control module must execute once'
    preserved = original_command.replace(marker + insertion, marker, 1)
    assert hashlib.sha256(preserved.encode()).hexdigest() == 'bcc48453cbc55193ffc09ddfab2675bc657897eb509143ebb2d52264b71742fe', 'Every original CPU selector, ordering and command bound must be preserved'
    assert hashlib.sha256(original_command.encode()).hexdigest() == '402fdf2a6f2a37e30ab2c7e52acf7236d083c819502a172e57e235558df5feee', 'Complete original CPU command plus exactly eighteen published controls must remain unchanged'
    assert hashlib.sha256(command.encode()).hexdigest() == '8b17b9189572d3de2c84ad5c3d6a8c8bc2defc8a042295dbc5a14a9dbf1d8fbb', 'All original selectors, the reviewed source controls and the later publications retain exact ordering and command bytes'
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


def related_module():
    spec = importlib.util.spec_from_file_location('ci_related_checks', ROOT / 'scripts/ci/related_checks.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_full_regression_keeps_its_jobs_and_runs_for_the_merge_head_and_main_only():
    """Every PR update no longer queues an hour-long qualification; the merge head is dispatched instead."""
    workflow = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    assert workflow['name'] == 'Linux CPU source checks'
    assert workflow['on'] == {'push': {'branches': ['main']}, 'workflow_dispatch': None}
    assert set(workflow['jobs']) == {'source', 'browser'}


def test_fast_lane_runs_the_full_lanes_static_commands_in_its_own_queue_and_gates_on_the_exact_head():
    full = yaml.safe_load((ROOT/'.github/workflows/ci.yml').read_text())
    fast = yaml.safe_load((ROOT/'.github/workflows/ci-fast.yml').read_text())
    assert fast['on']['pull_request'] is None and set(fast['on']['workflow_dispatch']['inputs']) == {'pytest', 'specs'}
    assert fast['permissions'] == {'contents': 'read'} and 'secrets.' not in json.dumps(fast)
    assert fast['concurrency']['cancel-in-progress'] is True and fast['concurrency']['group'] != full['concurrency']['group']
    # The merge gate lives in its own workflow, so a fast run's result is only the fast checks' result.
    assert set(fast['jobs']) == {'static', 'pytest', 'browser'}
    def run(job, name):
        values = [step['run'] for step in job['steps'] if step.get('name') == name]
        assert len(values) == 1, name
        return values[0]
    for name in ('Install locked CPU environment', 'Check preserved scope and acceptance states', 'Type checks, renderer regressions, and build'):
        assert run(fast['jobs']['static'], name) == run(full['jobs']['source'], name) == run(full['jobs']['browser'], name)
    assert fast['jobs']['pytest']['strategy']['matrix']['shard'] == [1, 2, 3, 4, 5, 6]
    assert 'related_checks.py select --kind pytest' in run(fast['jobs']['pytest'], 'Select related pytest references')
    assert '--shard "$SHARD"' in run(fast['jobs']['pytest'], 'Select related pytest references')
    assert fast['jobs']['browser']['strategy']['matrix']['shard'] == [1, 2, 3, 4]
    assert '--grep-invert @owned-model' in run(fast['jobs']['browser'], 'Related browser checks')
    gate = yaml.safe_load((ROOT/'.github/workflows/ci-merge-gate.yml').read_text())
    assert gate['on'] == {'pull_request': None, 'workflow_dispatch': None} and gate['permissions'] == {'contents': 'read'}
    assert gate['concurrency']['group'] not in (fast['concurrency']['group'], full['concurrency']['group'])
    assert set(gate['jobs']) == {'full-regression'} and gate['jobs']['full-regression']['name'] == 'full regression on this exact head'
    assert 'related_checks.py full-regression' in run(gate['jobs']['full-regression'], 'Exact-head full regression gate')
    gate_env = next(step for step in gate['jobs']['full-regression']['steps'] if step.get('name') == 'Exact-head full regression gate')['env']
    assert gate_env['SHA'] == '${{ github.event.pull_request.head.sha || github.sha }}'
    assert 'secrets.' not in json.dumps(gate)
    for job in [*fast['jobs'].values(), *gate['jobs'].values()]:
        assert job['steps'][0]['uses'] == full['jobs']['source']['steps'][0]['uses']
        assert job['steps'][0]['with']['persist-credentials'] is False
        # Inputs reach commands only through the environment, never through expression text in a script.
        assert not any('${{ inputs.' in step.get('run', '') for step in job['steps'])


def test_fast_selection_only_picks_full_lane_references_and_specs(tmp_path):
    related = related_module()
    references = related.full_pytest_references()
    assert 'backend/tests/test_source_ci_model_lanes.py' in references
    assert 'backend/tests/test_flow_package_outcome_diagnostic.py' in references
    always = set(related.ALWAYS_PYTEST)
    assert always <= set(references)
    assert set(related.select_pytest([])) == always
    assert 'backend/tests/test_application_node_writer_authority.py' in related.select_pytest(['backend/tests/test_application_node_writer_authority.py'])
    # A changed module selects the full-lane tests that read it, by dotted import or by `from package import module`.
    chosen = related.select_pytest(['backend/engine/local_training_worker.py'])
    assert 'backend/tests/test_service_s1_09.py' in chosen
    assert set(chosen) <= set(references) and len(chosen) < len(references)
    with pytest.raises(SystemExit):
        related.select_pytest([], extra=['backend/tests/not_in_the_full_lane.py'])
    assert related.select_specs(['scripts/e2e/comparison-task-center.spec.ts', 'src/renderer/App.tsx']) == ['scripts/e2e/comparison-task-center.spec.ts']
    with pytest.raises(SystemExit):
        related.select_specs([], extra=['scripts/e2e/missing.spec.ts'])
    # Shards split by file, keep each reference exactly once and keep the full lane's order inside a shard.
    selected = related.select_pytest(['backend/engine/local_training_worker.py', 'backend/api/routes_training.py'])
    shards = [related.shard(selected, index, 6) for index in range(1, 7)]
    assert sorted(value for part in shards for value in part) == sorted(selected)
    assert all(part == [value for value in selected if value in part] for part in shards)
    files = [{value.split('::', 1)[0] for value in part} for part in shards]
    assert all(not (files[a] & files[b]) for a in range(6) for b in range(a + 1, 6))


def test_exact_head_gate_passes_only_for_a_successful_full_regression_on_that_commit():
    related = related_module()
    sha, other = 'a' * 40, 'b' * 40
    ok = {'head_sha': sha, 'status': 'completed', 'conclusion': 'success', 'html_url': 'run'}
    assert related.gate_decision([ok], sha)[0] is True
    assert related.gate_decision([{**ok, 'head_sha': other}], sha) == (False, related.gate_decision([], sha)[1])
    assert 'has not run' in related.gate_decision([], sha)[1]
    assert related.gate_decision([{**ok, 'status': 'in_progress', 'conclusion': None}], sha)[0] is False
    assert related.gate_decision([{**ok, 'conclusion': 'failure'}], sha)[0] is False
    assert related.gate_decision([{**ok, 'conclusion': 'cancelled'}, ok], sha)[0] is True


def _commits(repo, names):
    """A linear history: one commit per name, each adding the file of that name; returns their SHAs."""
    run = lambda *args: subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
    run('init', '-q')
    shas = []
    for name in names:
        (repo / name).write_text(name)
        run('add', name)
        run('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', name)
        shas.append(run('rev-parse', 'HEAD'))
    return shas


def test_fast_range_moves_past_a_passing_fast_run_while_the_merge_gate_is_red(tmp_path):
    """Fast checks passed on B; the full regression never ran there. The next run's range starts at B."""
    related = related_module()
    base, b, c = _commits(tmp_path, ['base.txt', 'b.txt', 'c.txt'])
    # Only the fast workflow's passing runs are read; the separate merge gate's red result cannot hold B back.
    found = related.last_green_fast_head('owner/repo', 'branch', 'HEAD', root=tmp_path, passed=lambda repo, branch: [b])
    assert found == b
    assert related.changed_files(found, 'HEAD', root=tmp_path) == ['c.txt']
    assert related.gate_decision([], c)[0] is False, 'the merge gate stays red until the full regression passes on C'


def test_a_cancelled_fast_run_leaves_its_changes_in_the_next_range(tmp_path):
    """Passed on B, cancelled on C (not listed as passed), now at D: the range covers C and D."""
    related = related_module()
    base, b, c, d = _commits(tmp_path, ['base.txt', 'b.txt', 'c.txt', 'd.txt'])
    found = related.last_green_fast_head('owner/repo', 'branch', 'HEAD', root=tmp_path, passed=lambda repo, branch: [b])
    assert found == b and related.changed_files(found, 'HEAD', root=tmp_path) == ['c.txt', 'd.txt']
    # The head itself, and commits outside this history (a force-pushed-away head), are never the base.
    assert related.last_green_fast_head('o/r', 'b', 'HEAD', root=tmp_path, passed=lambda repo, branch: [d, 'f' * 40, b]) == b
    assert related.last_green_fast_head('o/r', 'b', 'HEAD', root=tmp_path, passed=lambda repo, branch: []) is None
    assert related.last_green_fast_head('o/r', 'b', 'HEAD', root=tmp_path, passed=lambda repo, branch: None) is None


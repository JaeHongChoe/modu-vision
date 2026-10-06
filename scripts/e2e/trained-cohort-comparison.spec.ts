import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test, expect} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
const sha = (file: string) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

test('native genuine learned models compare heldout cohort and five-checkpoint whole flow',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(720_000);
  test.skip(!process.env.MV_E2E_DINO_WEIGHTS, 'Requires explicitly supplied authentic local DINOv3 weights');
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api = (method: string, route: string, body?: any): Promise<any> => window.evaluate(async ({port, method, route, body}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method,
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }, {port: backend.port, method, route, body});
  const fixtureScript = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/dino_classification_data.py');
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [fixtureScript, workspace.root], {encoding: 'utf8'}));
  await api('POST', '/api/project/create', {name: 'Actual learned cohort and five-model chain', task: 'classification'});
  const project = await api('PUT', '/api/project/update', {source_dataset_dir: fixture.source});
  await api('POST', '/api/dataset/import', {folder_path: fixture.source, task: 'classification'});
  const split = JSON.parse(execFileSync(harness.resolvePython(), [fixtureScript, workspace.root, JSON.stringify(project)], {encoding: 'utf8'}));
  const trials: any[] = []; let datasetVersion: string | undefined;
  for (let index = 0; index < 5; index++) {
    const submission = await api('POST', '/api/automated-training/start', {task: 'classification', dataset_path: fixture.source,
      device: 'cpu', mode: 'quick', seed: 20261006 + index, epochs_per_trial: 2,
      ...(datasetVersion ? {dataset_version_id: datasetVersion} : {}),
      budget: {max_trials: 1, max_total_epochs: 2, max_seconds: 180, max_memory_mb: 4096},
      search_space: {architectures: ['dinov3_vits16'], learning_rates: [.001], weight_decays: [.0001], image_sizes: [64], batch_sizes: [2], augmentation_profiles: ['none']},
      base_config: {train_mode: 'head_only', num_workers: 0, pretrained_checkpoint: fixture.weights, pretrained_sha256: fixture.weights_sha256}});
    let measured: any;
    await expect.poll(async () => {measured = await api('GET', '/api/automated-training/jobs/' + submission.search_id);
      if (measured.status === 'failed') throw Error(JSON.stringify(measured)); return measured.status;
    }, {timeout: 185_000}).toBe('completed');
    expect(measured.epochs_consumed).toBe(2);
    datasetVersion ||= measured.training_provenance.dataset_version_id;
    expect(measured.training_provenance.dataset_version_id).toBe(datasetVersion);
    expect(sha(measured.winner.checkpoint_path)).toBe(measured.winner.checkpoint_sha256);
    trials.push(measured);
  }
  const jobs = trials.map(row => row.winner.trial_id);
  expect(new Set(trials.map(row => row.winner.checkpoint_sha256)).size).toBe(5);
  await window.reload();
  await expect(window.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  await window.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(3).click();
  const panel = window.getByRole('region', {name: '현행과 후보 모델 비교'});
  await panel.getByLabel('비교 기준 모델', {exact: true}).selectOption(jobs[0]);
  await panel.getByLabel('후보 모델', {exact: true}).selectOption(jobs[1]);
  const submitted = window.waitForResponse(r => new URL(r.url()).pathname === '/api/evaluation/model-comparisons/jobs' && r.request().method() === 'POST');
  await panel.getByRole('button', {name: '동일 test 이미지로 비교', exact: true}).click();
  const response = await submitted; expect(response.ok(), await response.text()).toBe(true);
  const submittedJob = await response.json(), query = '?' + new URLSearchParams({source_dataset_path: fixture.source, task: 'classification'});
  let compared: any;
  await expect.poll(async () => {compared = await api('GET', '/api/evaluation/model-comparisons/jobs/' + submittedJob.job_id + query);return compared.status;}, {timeout: 90_000}).toBe('completed');
  const report = await api('GET', '/api/evaluation/model-comparisons/' + compared.report_id + query);
  expect(report.images).toHaveLength(3);
  expect(report.images.every((row: any) => fixture.files.some((file: any) => file.split === 'test' && file.sha256 === row.image_sha256))).toBe(true);
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(compared.report_id);
  await evidence.screenshot(window, 'native-genuine-learned-same-heldout-comparison');
  const exported = await api('GET', '/api/evaluation/model-comparisons/' + compared.report_id + '/export' + query);
  const versions = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/trained_flow_comparison.py'), workspace.root, JSON.stringify(project), JSON.stringify(jobs)], {encoding: 'utf8', timeout: 180_000}));
  expect(versions.package_parity).toHaveLength(3);
  expect(versions.package_parity.every((row: any) => row.parity.status === 'passed')).toBe(true);
  // Explicit synthetic control truth exercises coverage and confusion metrics.
  // It is never a human reviewed manufacturing quality or deployment approval.
  const classes = ['OK', 'scratch', 'stain'];
  for (const file of fixture.files.filter((row: any) => row.split === 'test')) {
    const truthQuery = new URLSearchParams({image_path: file.path, task: 'classification'});
    for (const label of classes) truthQuery.append('classes', label);
    const current = await api('GET', '/api/image-truth?' + truthQuery);
    await api('PUT', '/api/image-truth', {image_path: file.path, task: 'classification', classes,
      verdict: file.label === 'OK' ? 'OK' : 'NG', defect_classes: file.label === 'OK' ? [] : [file.label],
      reviewer: 'Synthetic control fixture', expected_revision: current.truth_revision,
      expected_image_revision: current.image_revision,
      note: 'Generated synthetic control only. No human quality review or deployment approval.'});
  }
  const cohort = await api('POST', '/api/flow-evaluations/cohorts', {version_id: versions.version_a, name: 'Untouched three-image synthetic control'});
  expect(cohort.count).toBe(3);
  const a = await api('POST', '/api/flow-evaluations', {version_id: versions.version_a, cohort_id: cohort.cohort_id});
  const b = await api('POST', '/api/flow-evaluations', {version_id: versions.version_b, cohort_id: cohort.cohort_id});
  for (const row of [a,b]) {
    expect(row.status).toBe('completed');expect(row.validity.valid).toBe(true);expect(row.coverage.total).toBe(3);
    expect(row.errors).toEqual([]);expect(row.cohort_sha256).toBe(cohort.record_sha256);
    expect(row.coverage.known).toBe(3);expect(row.coverage.unknown).toBe(0);
  }
  expect(b.models).toHaveLength(5);
  const modelNodes = versions.chain.nodes.filter((node: any) => node.data.node_type === 'inspection').map((node: any) => node.id);
  expect(modelNodes).toHaveLength(5);
  expect(b.records.every((row: any) => modelNodes.every((id: string) =>
    row.node_evidence.filter((step: any) => step.node_id === id && !['error', 'warning', 'skipped'].includes(step.status)).length === 1))).toBe(true);
  const saved = await api('GET', '/api/flow-evaluations/' + b.evaluation_id);
  expect(saved.record_sha256).toBe(b.record_sha256);
  await window.reload();
  await expect(panel.getByLabel('저장된 모델 비교')).toHaveValue(compared.report_id, {timeout: 15_000});
  await evidence.screenshot(window, 'native-genuine-comparison-reopen');
  for (const file of fixture.files) expect(sha(file.path)).toBe(file.sha256);
  for (const [job, checksum] of Object.entries(versions.checkpoint_sha256)) expect(sha(path.join(project.models_dir, job, 'best_model.pt'))).toBe(checksum);
  evidence.note('genuine_learned_cohort', {project, fixture, split, trials, compared, report, exported, versions, cohort, a, b, saved,
    actual_native_comparison: true, actual_five_trained_checkpoints: true, source_preserved: true,
    synthetic_control_only: true, process_quality_approval: false, active_flow_changed: false});
});

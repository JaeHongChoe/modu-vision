import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {test, expect} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');

test('native AutoDL measures reopens reuses exact snapshot and stops its active trial',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(300_000);
  const {window} = electronSession;
  const backend = await electronSession.waitForBackend();
  const api = (route: string, body?: any): Promise<any> => window.evaluate(async ({port, route, body}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {...(body === undefined ? {} : {
      method: route.endsWith('/update') ? 'PUT' : 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`${response.status}: ${await response.text()}`);
    return response.json();
  }, {port: backend.port, route, body});
  const script = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/dino_classification_data.py');
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root], {encoding: 'utf8', timeout: 15_000}));
  await api('/api/project/create', {name: 'AutoDL actual native lifecycle', task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: fixture.source});
  const project = await api('/api/project/current');
  await api('/api/dataset/import', {folder_path: fixture.source, task: 'classification'});
  const split = JSON.parse(execFileSync(harness.resolvePython(), [script, workspace.root, JSON.stringify(project)], {encoding: 'utf8', timeout: 15_000}));
  const open = async () => {
    await window.reload();
    await expect(window.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await window.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(2).click();
    await window.locator('summary').filter({hasText: '빠른 학습 · 자동 탐색 · 설정 재사용'}).click();
  };
  await open();
  const panel = window.locator('details').filter({has: window.locator('summary').filter({hasText: '빠른 학습 · 자동 탐색 · 설정 재사용'})});
  const configure = async (epochs = 1) => {
    for (const [label, value] of Object.entries({'모델 구조 후보 · 쉼표 구분': 'dinov3_vits16',
      '학습률 후보 · 쉼표 구분': '.001', '가중치 감쇠 후보 · 쉼표 구분': '.0001',
      '입력 크기 후보 · 쉼표 구분': '64', '배치 크기 후보 · 쉼표 구분': '2', '증강 방법 후보 · 쉼표 구분': 'none',
      '후보당 Epoch': String(epochs), '최대 후보 수': '1', '전체 Epoch 예산': String(epochs),
      '전체 시간 예산 · 초': '180', '프로세스 메모리 예산 · MB': '4096', '탐색 Seed': '20261006'})) {
      await panel.getByLabel(label, {exact: true}).fill(value);
    }
    await panel.getByRole('combobox', {name: 'DINO 학습 범위', exact: true}).selectOption('head_only');
  };
  const start = async () => {
    const waiting = window.waitForResponse(r => new URL(r.url()).pathname === '/api/automated-training/start' && r.request().method() === 'POST');
    await panel.getByRole('button', {name: '측정 학습 시작', exact: true}).click();
    const response = await waiting;
    expect(response.ok(), await response.text()).toBe(true);
    return {posted: response.request().postDataJSON(), accepted: await response.json()};
  };
  const terminal = async (id: string) => {
    let result: any;
    await expect.poll(async () => {
      result = await api('/api/automated-training/jobs/' + id);
      if (result.status === 'failed') throw Error(JSON.stringify(result));
      return result.status;
    }, {timeout: 185_000}).toBe('completed');
    return result;
  };
  await configure();
  const first = await start();
  const measured = await terminal(first.accepted.search_id);
  expect(measured.epochs_consumed).toBe(1);
  expect(measured.winner.latency_ms).toBeGreaterThan(0);
  expect(Object.values(measured.winner.metrics).every(value => Number.isFinite(value))).toBe(true);
  const checkpoint = measured.winner.checkpoint_path;
  const originalHash = crypto.createHash('sha256').update(fs.readFileSync(checkpoint)).digest('hex');
  expect(originalHash).toBe(measured.winner.checkpoint_sha256);
  await expect(panel).toContainText('측정 완료', {timeout: 15_000});
  await evidence.screenshot(window, 'autodl-actual-measured-native');
  await open(); await configure();
  await panel.getByRole('combobox', {name: '저장된 자동 학습 작업', exact: true}).selectOption(measured.search_id);
  await expect(panel).toContainText('측정 완료');
  await panel.getByRole('combobox', {name: '완료 후보 재사용', exact: true}).selectOption(measured.search_id);
  const second = await start();
  const reused = await terminal(second.accepted.search_id);
  expect(reused.training_provenance).toEqual(measured.training_provenance);
  expect(reused.epochs_consumed).toBe(0);
  expect(reused.trials).toHaveLength(1);
  expect(reused.winner.reused_from_search_id).toBe(measured.search_id);
  expect(reused.winner.checkpoint_sha256).toBe(originalHash);
  await expect(panel).toContainText('측정 완료', {timeout: 15_000});
  await evidence.screenshot(window, 'autodl-native-exact-reuse-after-reopen');
  await panel.getByRole('combobox', {name: '완료 후보 재사용', exact: true}).selectOption('');
  await panel.getByRole('button', {name: '부모 설정으로 재학습', exact: true}).click();
  await panel.getByRole('combobox', {name: '호환 완료 부모 모델', exact: true}).selectOption(measured.winner.trial_id);
  const retrain = await start();
  const retrained = await terminal(retrain.accepted.search_id);
  expect(retrained.configuration_parent.parent_job_id).toBe(measured.winner.trial_id);
  expect(retrained.configuration_parent.parent_checkpoint_sha256).toBe(originalHash);
  expect(retrained.epochs_consumed).toBe(1);
  expect(retrained.winner.checkpoint_path).not.toBe(checkpoint);
  await expect(panel).toContainText('측정 완료', {timeout: 15_000});
  await panel.getByRole('button', {name: '구조·조건 자동 탐색', exact: true}).click();
  await panel.getByRole('combobox', {name: '호환 완료 부모 모델', exact: true}).selectOption('');
  await configure(100);
  const third = await start();
  await expect.poll(async () => {
    const state = await api('/api/automated-training/jobs/' + third.accepted.search_id);
    if (state.status === 'failed') throw Error(JSON.stringify(state));
    return state.trials.some((trial: any) => trial.status === 'running' && (trial.progress?.epoch || 0) >= 1);
  }, {timeout: 75_000}).toBe(true);
  const stopResponse = window.waitForResponse(r => new URL(r.url()).pathname.endsWith('/' + third.accepted.search_id + '/cancel'));
  await panel.getByRole('button', {name: '탐색 중지', exact: true}).click();
  expect((await stopResponse).ok()).toBe(true);
  let stopped: any;
  await expect.poll(async () => {
    stopped = await api('/api/automated-training/jobs/' + third.accepted.search_id);
    return stopped.status;
  }, {timeout: 25_000}).toBe('cancelled');
  await expect(panel).toContainText('중지됨', {timeout: 15_000});
  await evidence.screenshot(window, 'autodl-native-owned-trial-stopped');
  expect(crypto.createHash('sha256').update(fs.readFileSync(checkpoint)).digest('hex')).toBe(originalHash);
  for (const file of fixture.files) {
    expect(crypto.createHash('sha256').update(fs.readFileSync(file.path)).digest('hex')).toBe(file.sha256);
    evidence.addFile(file.path);
  }
  evidence.addFile(split.split_path); evidence.addFile(checkpoint);
  evidence.note('actual_autodl_lifecycle', {project, fixture, split, first, measured, second, reused, retrain, retrained, third, stopped,
    real_native_buttons: true, real_cpu_measured_training: true, immutable_version_reused: true,
    real_cooperative_cancel: true, original_source_unchanged: true, model_quality_approval: false});
});

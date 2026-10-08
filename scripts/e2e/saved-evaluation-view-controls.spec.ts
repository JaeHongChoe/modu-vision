import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Route, Locator} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

const harness = require('./fixtures/harness.cjs');
test.use({actionTimeout: 10_000});
type OwnedApi = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: OwnedApi, native: boolean, url?: string) {
  const source = path.join(workspace.root, 'saved-view-source');
  fs.mkdirSync(source);
  const original = path.join(source, 'part.png');
  fs.writeFileSync(original, png(64, 3, (x, y) => [x, y, 60]));
  const originalHash = sha(fs.readFileSync(original));
  const name = 'Owned saved evaluation view controls';
  const project = await api('/api/project/create', {name, task: 'segmentation'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation'});
  await api('/api/project/labelsets', {name: 'Other saved view labelset'});
  const sets = await api('/api/project/labelsets');
  const secondSet = sets.labelsets.find((entry: any) => entry.id !== 'default').id;
  // Immutable controlled reports, not model inference or human truth.
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [
    path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/evaluation_selection_reports.py'),
    workspace.root, project.project_dir, source, secondSet,
  ], {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const segmentation = fixture.items.find((item: any) => item.variant === 'selection_segmentation');
  const classification = fixture.items.find((item: any) => item.variant === 'selection_classification');
  expect(classification.record.result.test_predictions).toEqual([]);
  expect(classification.record.grouped_errors).toEqual({product: {}, lot: {}, ground_truth: {}});
  const projectBytes = fs.readFileSync(path.join(project.project_dir, 'project.json'));
  const writes: string[] = [];
  const queries: Array<{task: string | null; labelset: string | null; source: string | null}> = [];
  page.on('request', request => {
    const target = new URL(request.url());
    if (request.method() !== 'GET' && /\/(evaluation|train|jobs)(\/|$)/.test(target.pathname))
      writes.push(`${request.method()} ${target.pathname}`);
    if (target.pathname === '/api/evaluation/history')
      queries.push({task: target.searchParams.get('task'), labelset: target.searchParams.get('labelset_id'), source: target.searchParams.get('source_dataset_path')});
  });
  const summary = page.locator('summary').filter({hasText: '평가 이력 · 제품/Lot별 오류'});
  const history = summary.locator('..');
  const family = history.getByLabel('평가 이력 모델 종류', {exact: true});
  const labelset = history.getByLabel('평가 라벨 세트', {exact: true});
  const group = history.getByLabel('평가 오류 집계 기준', {exact: true});
  const selector = history.getByLabel(/^모델별 저장 평가/);
  const identity = history.locator('details').filter({has: page.locator('summary').filter({hasText: '평가 버전·데이터·모델 해시 확인'})}).first();
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button');
  const open = async () => { if (await history.getAttribute('open') === null) await summary.click(); };
  const navigate = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(name);
    await stages.nth(3).click();
    await open();
  };
  const assertRecord = async (item: any) => {
    await expect(selector).toHaveValue(item.record.evaluation_id);
    await expect(identity.locator('pre')).toContainText(item.record.evaluation_id);
    await expect(identity.locator('pre')).toContainText(item.record.evidence_sha256);
  };
  const choose = async (item: any) => {
    await family.selectOption(item.record.result.task);
    await labelset.selectOption('default');
    await expect(selector.locator(`option[value="${item.record.evaluation_id}"]`)).toHaveCount(1);
    await selector.selectOption(item.record.evaluation_id);
    await assertRecord(item);
  };
  const viewPreference = async () => page.evaluate(({projectId, source}) => {
    const matches = Object.keys(localStorage).filter(key => key.startsWith('vision-evaluation-view:') && key.includes(projectId) && key.includes(source));
    if (matches.length !== 1) throw new Error('Expected one exact owned project/source view preference');
    return {key: matches[0], raw: localStorage.getItem(matches[0])!};
  }, {projectId: project.id, source});
  const screenshot = async (label: string, target: Locator = family) => {
    await target.scrollIntoViewIfNeeded();
    await expect(target).toBeInViewport();
    await evidence.screenshot(page, `${native ? 'native' : 'browser'}-${label}`);
  };
  const dimensions: Record<string, unknown> = {};
  await navigate();
  await choose(segmentation);
  await group.selectOption('lot');

  // The existing reader treats the entire malformed optional view as invalid.
  // No unknown task or group becomes request authority after a real reload.
  for (const field of ['task', 'group'] as const) {
    const before = await viewPreference();
    const injected = {...JSON.parse(before.raw), [field]: field === 'task' ? 'controlled-unknown-family' : 'controlled-unknown-group'};
    const queryStart = queries.length;
    await page.evaluate(({key, value}) => localStorage.setItem(key, JSON.stringify(value)), {key: before.key, value: injected});
    await navigate();
    await expect(family).toHaveValue('segmentation');
    await expect(labelset).toHaveValue('');
    await expect(group).toHaveValue('product');
    await assertRecord(segmentation);
    expect(queries.slice(queryStart).length).toBeGreaterThan(0);
    // Initial state hydration may fetch the known default OCR task/source.
    // The injected unknown family never reaches a request, and the final
    // owned-source query agrees with the hydrated segmentation view.
    const requests = queries.slice(queryStart);
    expect(requests.every(query => query.task !== 'controlled-unknown-family')).toBe(true);
    expect(requests.filter(query => query.source === source).at(-1)?.task).toBe('segmentation');
    expect(await family.locator('option').evaluateAll(rows => rows.map(row => (row as HTMLOptionElement).value))).not.toContain('controlled-unknown-family');
    expect(await group.locator('option').evaluateAll(rows => rows.map(row => (row as HTMLOptionElement).value))).not.toContain('controlled-unknown-group');
    dimensions[field === 'task' ? 'family_invalid' : 'group_invalid'] = {
      controlled_optional_preference_fault: {key: before.key, original: JSON.parse(before.raw), injected},
      actual_renderer_reload: true, fallback: {task: 'segmentation', labelset_id: '', group: 'product'},
      requests, selected_evaluation_id: segmentation.record.evaluation_id,
      evidence_sha256: segmentation.record.evidence_sha256,
    };
    await screenshot(`${field}-invalid-preference-fallback`);
    await choose(segmentation);
    await group.selectOption('lot');
  }

  const pattern = /\/api\/evaluation\/history\?/;
  const controlledFailure = 'Controlled selected family history transport failure';
  const refused: Array<{url: string; status: number; forwarded: boolean}> = [];
  const failFamily = async (route: Route) => {
    const target = new URL(route.request().url());
    if (target.searchParams.get('task') !== 'classification') return route.continue();
    expect(target.searchParams.get('source_dataset_path')).toBe(source);
    expect(target.searchParams.get('labelset_id')).toBe('default');
    refused.push({url: target.pathname + target.search, status: 503, forwarded: false});
    await route.fulfill({status: 503, contentType: 'application/json', body: JSON.stringify({detail: controlledFailure})});
  };
  await page.route(pattern, failFamily);
  try {
    const failed = page.waitForResponse(response => new URL(response.url()).pathname === '/api/evaluation/history'
      && new URL(response.url()).searchParams.get('task') === 'classification');
    await family.selectOption('classification');
    expect((await failed).status()).toBe(503);
    await expect(family).toHaveValue('classification');
    await expect(history.getByRole('alert')).toHaveText(controlledFailure);
    await expect(selector).toHaveCount(0);
    await expect(identity).toHaveCount(0);
    await expect(group).toHaveCount(0);
    await expect(history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true})).toBeDisabled();
    await screenshot('family-history-transport-refusal');
  } finally { await page.unroute(pattern, failFamily); }
  expect(refused).toHaveLength(1);
  await choose(segmentation);
  await expect(history.getByRole('alert')).toHaveCount(0);
  await expect(group).toHaveValue('lot');
  dimensions.family_error = {controlled_http_status: 503, requests: refused, stale_record_identity_and_group_absent: true,
    reevaluation_disabled: true, explicit_family_change_recovered: true,
    recovered_evaluation_id: segmentation.record.evaluation_id, evidence_sha256: segmentation.record.evidence_sha256};

  // Escape dismisses the actual select popup without selecting another option.
  // This is view cancellation, not cancellation of an HTTP request or a job.
  for (const [dimension, picker, value] of [['family_cancel', family, 'segmentation'], ['group_cancel', group, 'lot']] as const) {
    const before = await viewPreference(), requestCount = queries.length;
    await picker.click();
    await page.keyboard.press('Escape');
    await expect(picker).toBeFocused();
    await expect(picker).toHaveValue(value);
    await assertRecord(segmentation);
    expect(await viewPreference()).toEqual(before);
    expect(queries.length).toBe(requestCount);
    await screenshot(`${dimension}-popup-dismissed`);
    expect(queries.length).toBe(requestCount);
    dimensions[dimension] = {popup_clicked: true, escape_pressed: true, retained_choice: value,
      view_preference_bytes_unchanged: true, extra_history_requests: 0,
      evaluation_id: segmentation.record.evaluation_id, evidence_sha256: segmentation.record.evidence_sha256,
      request_or_job_cancellation_claimed: false};
  }

  await choose(classification);
  await group.selectOption('ground_truth');
  await expect(group).toHaveValue('ground_truth');
  await expect(history.locator('table tbody tr')).toHaveCount(0);
  await expect(history).toContainText('이진 정상/불량 오류를 임의로 집계하지 않습니다.');
  await assertRecord(classification);
  await screenshot('empty-controlled-group-table', history.getByText('이 모델은 영역·문자·복원 지표로 평가합니다. 이진 정상/불량 오류를 임의로 집계하지 않습니다.', {exact: true}));
  dimensions.group_empty = {evaluation_id: classification.record.evaluation_id, evidence_sha256: classification.record.evidence_sha256,
    test_predictions: [], independently_read_grouped_errors: classification.record.grouped_errors,
    visible_empty_table: true, fabricated_binary_counts: false};

  const handoffPreference = await viewPreference();
  await stages.nth(0).click();
  await expect(summary).toHaveCount(0);
  await stages.nth(3).click();
  await expect(history).toHaveAttribute('open', '');
  await expect(family).toHaveValue('classification');
  await expect(labelset).toHaveValue('default');
  await expect(group).toHaveValue('ground_truth');
  await assertRecord(classification);
  expect(await viewPreference()).toEqual(handoffPreference);
  await screenshot('workflow-handoff-exact-record-and-group');
  await identity.locator('summary').click();
  await expect(identity.locator('pre')).toBeVisible();
  await screenshot('workflow-handoff-retained-record-hash', identity.locator('pre'));
  const handoff = {actual_workflow_stage_leave: 0, actual_workflow_stage_return: 3,
    task: 'classification', labelset_id: 'default', group: 'ground_truth',
    evaluation_id: classification.record.evaluation_id, evidence_sha256: classification.record.evidence_sha256,
    view_preference_bytes_unchanged: true};
  dimensions.family_handoff = handoff;
  dimensions.group_handoff = handoff;

  for (const item of fixture.items) {
    expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);
    evidence.addFile(item.report_path);
  }
  for (const input of fixture.inputs) {
    expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);
    evidence.addFile(input.path);
  }
  expect(sha(fs.readFileSync(original))).toBe(originalHash);
  expect(fs.readFileSync(path.join(project.project_dir, 'project.json')).equals(projectBytes)).toBe(true);
  evidence.addFile(original);
  evidence.addFile(path.join(project.project_dir, 'project.json'));
  expect(writes).toEqual([]);
  expect(Object.keys(dimensions)).toHaveLength(8);
  evidence.note('saved_evaluation_view_controls', {
    feature: 'F059', actions: ['native-saved-evaluation-family', 'native-saved-evaluation-group'],
    dimensions, group_error_pending_no_separate_transport_or_submit: true,
    project_id: project.id, fixture, original_source_sha256: originalHash,
    project_json_sha256: sha(projectBytes), report_and_input_bytes_unchanged: true,
    evaluation_training_job_writes: writes, history_queries: queries,
    controlled_saved_reports: true, model_inference_executed: false, gpu_work_executed: false,
    human_label_approval: false, model_quality_approved: false,
    installed_native_or_windows_acceptance: false, whole_process_tree_verified: false, publisher_approved: false,
  });
}

test('saved evaluation view validates preferences, refuses selected family errors and preserves dismissed and handed off choices', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: OwnedApi = async (route, body, method) => {
    const response = await request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})});
    expect(response.ok(), await response.text()).toBe(true);
    return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});

test('native saved evaluation view validates preferences, refuses selected family errors and preserves dismissed and handed off choices', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: OwnedApi = (route, body, method) => window.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned saved-view API: HTTP ${response.status}`);
    return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(window, workspace, evidence, api, true);
});

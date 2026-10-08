import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Route, Locator} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      expect(entry.isSymbolicLink()).toBe(false); const file = path.join(folder, entry.name);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return result;
}

async function idleDefaultStores(api: Api, source: string, task: string) {
  const params = new URLSearchParams({source_dataset_path: source, task}).toString();
  const records: Record<string, any> = {};
  for (const endpoint of ['/api/model-deployments/active?' + new URLSearchParams({source_dataset_path: source, task: 'ocr'}),
    '/api/model-deployments/active?' + params, '/api/model-deployments/history?' + params,
    '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', '/api/runtime-services/capture-groups', '/api/runtime-services'])
    records[endpoint] = await api(endpoint, undefined, 'GET');
  for (const endpoint of Object.keys(records).filter(value => value.startsWith('/api/model-deployments/active?')))
    expect(records[endpoint]).toEqual({active: null, field_runtime_applied: false});
  expect(records['/api/model-deployments/history?' + params]).toEqual({revisions: []});
  expect(records['/api/fleet/targets']).toEqual({targets: []}); expect(records['/api/fleet/rollouts']).toEqual({rollouts: []});
  expect(records['/api/runtime-services/capture-groups']).toEqual({policy: null, groups: [], total: 0});
  expect(records['/api/runtime-services']).toMatchObject({runtime: {status: 'stopped'}, active: null, history: [],
    adapter_config: {enabled: false, modbus: null, mes: null},
    native_install: {prepared: false, registered: false, enabled: false, running: false, verified: false},
    recovery: {active: null, pending: null, last_operation: null}, runtime_build: null});
  return records;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'saved-selection-originals'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'); fs.writeFileSync(original, png(64, 3, (x, y) => [x, y, 173]));
  const originalHash = fileSha(original);
  const project = await api('/api/project/create', {name: 'Owned saved evaluation selection lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  await api('/api/annotations/save', {image_id: 'part', image_path: original, image_width: 64, image_height: 64,
    actor: 'saved-selection-controlled-fixture', annotations: [{id: 'controlled-original-label', type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]}]});
  await api('/api/project/labelsets', {name: 'Controlled other saved labelset'});
  const secondSet = (await api('/api/project/labelsets')).labelsets.find((row: any) => row.id !== 'default').id;
  await api('/api/project/labelsets', {name: 'Controlled empty saved labelset'});
  const sets = await api('/api/project/labelsets'), emptySet = sets.labelsets.find((row: any) => !['default', secondSet].includes(row.id)).id;
  expect(sets.active_id).toBe('default'); expect(sets.labelsets).toHaveLength(3);
  // Existing fixture data are controlled saved reports, not trained-model output
  // or approval of the synthetic initial label.
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/evaluation_selection_reports.py'), w.root, project.project_dir, source, secondSet],
  {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const selected = fixture.items.find((row: any) => row.variant === 'valid' && row.labelset_id === 'default');
  const alternate = fixture.items.find((row: any) => row.variant === 'selection_segmentation');
  expect(selected.record.binding).toMatchObject({source_dataset_path: source, task: 'segmentation', labelset_id: 'default'});
  expect(alternate.record.evaluation_id).not.toBe(selected.record.evaluation_id);
  expect(fixture.items.some((row: any) => row.labelset_id === emptySet || row.record.binding.task === 'ocr')).toBe(false);
  const historyRoute = (task: string, labelset?: string) => '/api/evaluation/history?' + new URLSearchParams({
    source_dataset_path: source, task, ...(labelset === undefined ? {} : {labelset_id: labelset})});
  const recordRoute = (item: any) => '/api/evaluation/history/' + item.record.evaluation_id + '?' + new URLSearchParams({
    source_dataset_path: source, task: item.record.binding.task});
  const summary = page.locator('summary').filter({hasText: /^평가 이력 · 제품\/Lot별 오류$/}), history = summary.locator('..');
  const family = history.getByLabel('평가 이력 모델 종류', {exact: true}), labelset = history.getByLabel('평가 라벨 세트', {exact: true});
  const selector = history.getByRole('combobox', {name: '모델별 저장 평가', exact: true});
  const group = history.getByLabel('평가 오류 집계 기준', {exact: true});
  const identity = history.locator('summary').filter({hasText: /^평가 버전·데이터·모델 해시 확인$/}).locator('..');
  const result = history.locator('summary').filter({hasText: /^저장된 평가 지표·이미지 결과$/}).locator('..');
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button');
  const pendingStoreReads = new Set<Request>(), unverifiedStoreReads = new Set<Request>(), completedStoreReads: any[] = [], verifiedStoreReads: any[] = [], failedStoreReads: any[] = [];
  const storeReadTimes = new Map<Request, {started: number; deadline: number; finished?: number}>();
  const storeReader = (request: Request) => request.method() === 'GET' &&
    ['/api/model-deployments/active', '/api/model-deployments/history', '/api/provenance/impact'].includes(new URL(request.url()).pathname);
  const evaluationReader = (request: Request) => request.method() === 'GET' &&
    ['/api/model-deployments/active', '/api/model-deployments/history', '/api/provenance/impact',
      '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts',
      '/api/runtime-services/capture-groups', '/api/runtime-services'].includes(new URL(request.url()).pathname);
  const beginStoreRead = (request: Request) => {
    if (evaluationReader(request)) {
      const started = performance.now(); storeReadTimes.set(request, {started, deadline: started + 10_000});
    }
    if (storeReader(request)) {pendingStoreReads.add(request); unverifiedStoreReads.add(request);}
  };
  const finishStoreRead = (request: Request) => {
    if (!evaluationReader(request)) return;
    const address = new URL(request.url()), timing = storeReadTimes.get(request)!;
    timing.finished = performance.now();
    if (!storeReader(request)) return;
    pendingStoreReads.delete(request);
    completedStoreReads.push({method: 'GET', endpoint: address.pathname, source: address.searchParams.get('source_dataset_path'),
      task: address.searchParams.get('task'), request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline});
  };
  const failStoreRead = (request: Request) => {if (storeReader(request)) {pendingStoreReads.delete(request); failedStoreReads.push(new URL(request.url()).pathname);}};
  page.on('request', beginStoreRead); page.on('requestfinished', finishStoreRead); page.on('requestfailed', failStoreRead);
  const withStoreReadDeadline = async <T,>(request: Request, read: () => Promise<T>): Promise<T> => {
    const timing = storeReadTimes.get(request)!;
    const completedInTime = () => timing.finished !== undefined && timing.finished <= timing.deadline;
    // A previously completed original request may be verified later. It does
    // not receive a fresh network deadline when this reader inspects it.
    if (timing.finished !== undefined) {expect(completedInTime()).toBe(true); return read();}
    const remaining = timing.deadline - performance.now();
    if (remaining <= 0) throw Error('Original store GET exceeded its absolute 10s completion deadline');
    let timer: ReturnType<typeof setTimeout> | undefined;
    const operation = read();
    try {
      const result = await Promise.race([operation, new Promise<T>((resolve, reject) => {
        timer = setTimeout(() => completedInTime() ? resolve(operation)
          : reject(Error('Original store GET exceeded its absolute 10s completion deadline')), remaining);
      })]);
      expect(completedInTime()).toBe(true); return result;
    } finally {if (timer !== undefined) clearTimeout(timer);}
  };
  const settleStoreReads = async () => {
    // Await the original, already-started reads. No new read, retry, file
    // exception or stability polling stands in for request completion.
    for (const request of unverifiedStoreReads) {
      const {response, body} = await withStoreReadDeadline(request, async () => {
        const response = await request.response(); expect(response).not.toBeNull();
        expect(response!.status()).toBe(200); expect(await response!.finished()).toBeNull();
        return {response: response!, body: await response!.json()};
      });
      const address = new URL(request.url()), timing = storeReadTimes.get(request)!;
      if (address.pathname === '/api/provenance/impact')
        expect(body).toMatchObject({project_id: project.id, source_dataset_path: source, labelset_id: 'default'});
      else {
        expect(address.searchParams.get('source_dataset_path')).toBe(source);
        expect(['ocr', project.task]).toContain(address.searchParams.get('task'));
        expect(body).toEqual(address.pathname.endsWith('/active') ? {active: null, field_runtime_applied: false} : {revisions: []});
      }
      verifiedStoreReads.push({method: 'GET', endpoint: address.pathname, source: address.searchParams.get('source_dataset_path'),
        task: address.searchParams.get('task'), status: response.status(), response_body_complete: true,
        request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline,
        exact_owned_binding_or_idle_record: true});
      unverifiedStoreReads.delete(request);
    }
    expect(pendingStoreReads.size).toBe(0); expect(unverifiedStoreReads.size).toBe(0); expect(failedStoreReads).toEqual([]);
  };
  const defaultReads = () => ([['/api/model-deployments/active', project.task], ['/api/model-deployments/history', project.task],
    ['/api/model-deployments/active', 'ocr'], ['/api/provenance/impact', null],
    ['/api/fleet/targets', null], ['/api/fleet/capabilities', null], ['/api/fleet/rollouts', null],
    ['/api/runtime-services/capture-groups', null], ['/api/runtime-services', null]] as const).map(([endpoint, modelTask]) =>
    page.waitForResponse(response => {const address = new URL(response.url()); return response.request().method() === 'GET'
      && address.pathname === endpoint && (modelTask === null || (address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === modelTask));}, {timeout: 10_000}));
  const finishDefaults = async (waiting: ReturnType<typeof defaultReads>) => {
    for (const response of await Promise.all(waiting)) {
      const body = await withStoreReadDeadline(response.request(), async () => {
        expect(response.status()).toBe(200); expect(await response.finished()).toBeNull(); return response.json();
      });
      if (new URL(response.url()).pathname === '/api/provenance/impact')
        expect(body).toMatchObject({project_id: project.id, source_dataset_path: source, labelset_id: 'default'});
    }
    await settleStoreReads();
  };
  const open = async () => {if (await history.getAttribute('open') === null) await summary.click();};
  const mountEvaluation = async () => {const waiting = defaultReads(); await stages.nth(3).click(); await finishDefaults(waiting); await open();};
  const navigate = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    // Reload can restore the evaluation stage before response waiters exist.
    // Actually leave it, then observe the fresh mount's declared real reads.
    await stages.nth(1).click(); await expect(summary).toHaveCount(0); await mountEvaluation();
  };
  const assertRecord = async (item: any) => {
    await expect(selector).toHaveValue(item.record.evaluation_id);
    if (await identity.getAttribute('open') === null) await identity.locator('summary').click();
    await expect(identity.locator('pre')).toBeVisible();
    expect(JSON.parse(await identity.locator('pre').innerText())).toEqual({evaluation_id: item.record.evaluation_id,
      evidence_sha256: item.record.evidence_sha256, binding: item.record.binding});
    if (await result.getAttribute('open') === null) await result.locator('summary').click();
    await expect(result.locator('pre')).toBeVisible(); expect(JSON.parse(await result.locator('pre').innerText())).toEqual(item.record.result);
    expect(await api(recordRoute(item))).toEqual(item.record); expect(fileSha(item.report_path)).toBe(item.report_sha256);
  };
  const choose = async (item: any) => {
    if (await family.inputValue() !== item.record.binding.task) await family.selectOption(item.record.binding.task);
    if (await labelset.inputValue() !== item.labelset_id) await labelset.selectOption(item.labelset_id);
    await expect(selector.locator('option[value="' + item.record.evaluation_id + '"]')).toHaveCount(1);
    await selector.selectOption(item.record.evaluation_id); await assertRecord(item);
  };
  const historyResponse = (task: string, labelsetId: string) => page.waitForResponse(response => {const address = new URL(response.url());
    return response.request().method() === 'GET' && address.pathname === '/api/evaluation/history'
      && address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === task
      && address.searchParams.get('labelset_id') === labelsetId;
  });
  await navigate(); await choose(selected); await group.selectOption('lot');
  // Every default-store read and complete metadata/label query precedes the
  // full file baseline. A first visit may materialize empty local schemas.
  const idleEvaluationDefaults = await idleDefaultStores(api, source, project.task);
  const apiBefore: Record<string, any> = {...idleEvaluationDefaults};
  for (const endpoint of ['/api/project/current', '/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100',
    '/api/project/preferences', '/api/dataset/versions', '/api/dataset/metadata/split', '/api/project/labelsets',
    '/api/data-workbench/review-evaluations', '/api/data-workbench/review-queues', annotationRoute,
    historyRoute('segmentation'), historyRoute('segmentation', 'default'), historyRoute('segmentation', secondSet),
    historyRoute('segmentation', emptySet), historyRoute('ocr', 'default')]) apiBefore[endpoint] = await api(endpoint);
  expect(apiBefore['/api/project/labelsets']).toEqual(sets);
  expect(apiBefore[historyRoute('ocr', 'default')]).toEqual({items: [], total: 0});
  expect(apiBefore[historyRoute('segmentation', emptySet)]).toEqual({items: [], total: 0});
  expect(apiBefore['/api/data-workbench/review-queues']).toEqual({queues: []});
  expect(apiBefore['/api/team-data'].settings).toMatchObject({editing_enabled: false, review_enabled: false, revision: 1});
  const metadata = apiBefore['/api/dataset/metadata?limit=100'].items; expect(metadata).toHaveLength(1);
  expect(metadata[0]).toMatchObject({file_path: original, content_hash: originalHash});
  apiBefore['/api/team-data/images/' + metadata[0].image_uuid] = await api('/api/team-data/images/' + metadata[0].image_uuid);
  expect(apiBefore[annotationRoute].annotations).toHaveLength(1); expect(apiBefore[annotationRoute].metadata.workflow_state).not.toBe('approved');
  for (const item of fixture.items) {apiBefore[recordRoute(item)] = await api(recordRoute(item)); expect(apiBefore[recordRoute(item)]).toEqual(item.record);}
  await settleStoreReads(); expect(await idleDefaultStores(api, source, project.task)).toEqual(idleEvaluationDefaults); await settleStoreReads();
  const roots = {source, project: project.project_dir, annotations: active.annotations_dir || project.annotations_dir,
    inputs: path.join(w.root, 'controlled-evaluation-inputs')};
  const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
  const baseline = {roots, before, apiBefore, project, source, fixture, metadata,
    completedStoreReads: [...completedStoreReads], verifiedStoreReads: [...verifiedStoreReads], pendingStoreReads: pendingStoreReads.size};
  const beforeFile = path.join(w.logs, 'saved-selection-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(before[name])) {
    const snapshot = path.join(w.logs, 'saved-selection-protected-before', name, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true});
    fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
  }
  const mutations: any[] = [], queries: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {const address = new URL(request.url()); if (!address.pathname.startsWith('/api/')) return;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations.push({method: request.method(), endpoint: address.pathname,
      body: request.postData()});
    if (request.method() === 'GET' && address.pathname === '/api/evaluation/history') queries.push({source: address.searchParams.get('source_dataset_path'),
      task: address.searchParams.get('task'), labelset_id: address.searchParams.get('labelset_id')});
  };
  page.on('request', observe);
  const settleCustody = async () => {
    await settleStoreReads(); expect(await idleDefaultStores(api, source, project.task)).toEqual(idleEvaluationDefaults); await settleStoreReads();
  };
  const unchanged = async () => {
    await settleCustody();
    for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[name]);
    const apiAfter: Record<string, any> = {};
    for (const [endpoint, saved] of Object.entries(apiBefore)) {
      const actual = await api(endpoint); expect(actual).toEqual(saved); apiAfter[endpoint] = actual;
    }
    await settleStoreReads();
    for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[name]);
    expect(mutations).toEqual([]); expect(fileSha(original)).toBe(originalHash); return apiAfter;
  };
  const capture = async (label: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport(); await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-saved-selection-${label}`);
  };
  const preferences = async () => page.evaluate(({projectId}) => Object.fromEntries(Object.keys(localStorage).filter(key =>
    key.startsWith('vision-evaluation-') && key.includes(projectId)).sort().map(key => [key, localStorage.getItem(key)])),
  {projectId: project.id});
  const recordPreference = async () => page.evaluate(({projectId, source}) => {
    const prefix = 'vision-evaluation-record:', matches = Object.keys(localStorage).filter(key => {if (!key.startsWith(prefix)) return false;
      try {const scope = JSON.parse(key.slice(prefix.length)); return JSON.stringify(scope.slice(0, 4)) === JSON.stringify([projectId, source, 'segmentation', 'default']);} catch {return false;}});
    if (matches.length !== 1) throw Error('Expected exact source/task/default record preference'); return {key: matches[0], raw: localStorage.getItem(matches[0])!};
  }, {projectId: project.id, source});
  const viewPreference = async () => page.evaluate(({projectId, source}) => {
    const prefix = 'vision-evaluation-view:', matches = Object.keys(localStorage).filter(key => key.startsWith(prefix) && key.includes(projectId) && key.includes(source));
    if (matches.length !== 1) throw Error('Expected one exact project/source view preference'); return {key: matches[0], raw: localStorage.getItem(matches[0])!};
  }, {projectId: project.id, source});
  let catalogFailures = 0;
  const failCatalog = async (route: Route) => {expect(route.request().method()).toBe('GET'); expect(new URL(route.request().url()).pathname).toBe('/api/project/labelsets');
    catalogFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled saved labelset catalog GET failure'}});
  };
  try {
    const emptyTask = historyResponse('ocr', 'default'); await family.selectOption('ocr'); const taskRead = await emptyTask;
    expect(taskRead.status()).toBe(200); expect(await taskRead.json()).toEqual(apiBefore[historyRoute('ocr', 'default')]);
    await expect(family).toHaveValue('ocr'); await expect(selector).toHaveCount(0); await expect(identity).toHaveCount(0);
    await expect(result).toHaveCount(0); await expect(history).toContainText('저장된 평가 이력이 없습니다.'); await expect(history.getByRole('alert')).toHaveCount(0);
    await expect(history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true})).toBeDisabled();
    await capture('actual-empty-OCR-scope', family); await unchanged();
    controls.push({action: 'native-saved-evaluation-exact-record', dimension: 'empty', setup: 'Actual OCR family option with no owned saved OCR reports',
      simulated_response_to_create_empty: false, exact_GET_200_empty_scope: true, task: 'ocr', labelset_id: 'default', saved_source_reports_preserved: true});
    await choose(selected);

    const emptyLabelset = historyResponse('segmentation', emptySet); await labelset.selectOption(emptySet); const emptyRead = await emptyLabelset;
    expect(emptyRead.status()).toBe(200); expect(await emptyRead.json()).toEqual(apiBefore[historyRoute('segmentation', emptySet)]);
    await expect(labelset).toHaveValue(emptySet); await expect(selector).toHaveCount(0); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history).toContainText('저장된 평가 이력이 없습니다.'); await expect(history.getByRole('alert')).toHaveCount(0);
    await capture('actual-empty-owned-labelset', labelset); await unchanged();
    controls.push({action: 'native-saved-evaluation-labelset', dimension: 'empty', setup: 'Actual third owned labelset option with no matching saved reports',
      simulated_response_to_create_empty: false, exact_GET_200_empty_scope: true, labelset_id: emptySet, active_project_labelset_unchanged: 'default'});
    await choose(selected);

    // Controlled optional persisted-choice fault, never a fabricated selectable
    // UI option or a claimed invalid evaluation submission.
    const originalChoice = await recordPreference(), missingId = 'evaluation_missing_owned_record';
    await page.evaluate(({key, raw}) => localStorage.setItem(key, raw), {key: originalChoice.key, raw: JSON.stringify({version: 1, evaluation_id: missingId})});
    await navigate(); await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue('default');
    await expect(selector).toHaveValue(''); await expect(selector.locator('option[value="' + selected.record.evaluation_id + '"]')).toHaveCount(1);
    await expect(history.getByRole('alert')).toContainText('선택했던 평가 ' + missingId); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true})).toBeDisabled();
    expect(JSON.parse((await recordPreference()).raw)).toEqual({version: 1, evaluation_id: missingId}); await capture('missing-persisted-record-refusal', selector); await unchanged();
    await choose(selected); await expect(history.getByRole('alert')).toHaveCount(0); await unchanged();
    controls.push({action: 'native-saved-evaluation-exact-record', dimension: 'invalid', controlled_optional_preference_fault: {key: originalChoice.key, evaluation_id: missingId},
      actual_renderer_reload: true, absent_identity_not_silently_replaced: true, known_records_retained: true, explicit_actual_select_recovered: selected.record.evaluation_id});

    const oldView = await viewPreference(), missingSet = 'owned_missing_labelset';
    const injectedView = {...JSON.parse(oldView.raw), labelsetId: missingSet};
    await page.evaluate(({key, raw}) => localStorage.setItem(key, raw), {key: oldView.key, raw: JSON.stringify(injectedView)});
    await navigate(); await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue(missingSet);
    await expect(history.getByRole('alert')).toContainText('선택했던 라벨 세트 ' + missingSet + '를 찾지 못했습니다.');
    await expect(labelset.locator('option[value="' + missingSet + '"]')).toContainText('찾을 수 없음');
    await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true})).toBeDisabled();
    await capture('missing-persisted-labelset-refusal', labelset); await unchanged(); await choose(selected); await unchanged();
    controls.push({action: 'native-saved-evaluation-labelset', dimension: 'invalid', controlled_optional_preference_fault: {key: oldView.key, labelset_id: missingSet},
      actual_renderer_reload: true, missing_set_alert: true, automatic_labelset_activation: false, explicit_actual_select_recovered: 'default'});

    // Dismissing the actual picker preserves view choice. No network request,
    // evaluation job or explicit confirmation dialogue is cancelled here.
    for (const [action, picker, value] of [['native-saved-evaluation-exact-record', selector, selected.record.evaluation_id],
      ['native-saved-evaluation-labelset', labelset, 'default']] as const) {
      const savedPreferences = await preferences(), requestCount = queries.length;
      await picker.click(); await page.keyboard.press('Escape'); await expect(picker).toBeFocused(); await expect(picker).toHaveValue(value);
      await assertRecord(selected); expect(await preferences()).toEqual(savedPreferences); expect(queries.length).toBe(requestCount);
      await capture(action + '-actual-picker-Escape', picker); await unchanged();
      controls.push({action, dimension: 'cancel', actual_picker_clicked: true, actual_Escape_dismissal: true, retained_value: value,
        exact_preferences_unchanged: true, additional_history_requests: 0, request_or_job_cancellation_claimed: false});
    }

    await page.route('**/api/project/labelsets', failCatalog);
    try {
      const failed = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/project/labelsets');
      const preservedHistory = historyResponse('segmentation', 'default');
      await history.getByRole('button', {name: '평가 이력 새로고침', exact: true}).click();
      expect((await failed).status()).toBe(503); const preserved = await preservedHistory; expect(preserved.status()).toBe(200);
      expect(await preserved.json()).toEqual(apiBefore[historyRoute('segmentation', 'default')]);
      await expect(history.getByRole('alert')).toHaveText('Controlled saved labelset catalog GET failure');
      await expect(labelset).toHaveValue('default'); await assertRecord(selected); await capture('catalog-503-real-history-retained', history.getByRole('alert'));
      await settleCustody(); for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[name]); expect(mutations).toEqual([]);
    } finally {await page.unroute('**/api/project/labelsets', failCatalog);}
    const restoredCatalog = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/project/labelsets');
    const restoredHistory = historyResponse('segmentation', 'default'); await history.getByRole('button', {name: '평가 이력 새로고침', exact: true}).click();
    const restored = await restoredCatalog; expect(restored.status()).toBe(200); expect(await restored.json()).toEqual(sets);
    const realHistory = await restoredHistory; expect(realHistory.status()).toBe(200); expect(await realHistory.json()).toEqual(apiBefore[historyRoute('segmentation', 'default')]);
    await expect(history.getByRole('alert')).toHaveCount(0); await assertRecord(selected); await unchanged(); expect(catalogFailures).toBe(1);
    controls.push({action: 'native-saved-evaluation-labelset', dimension: 'error', scoped_actual_catalog_GET_503: 1, selected_labelset_and_real_full_history_retained: true,
      actual_refresh_real_200_recovered_full_catalog_and_record: true, evaluation_id: selected.record.evaluation_id, evidence_sha256: selected.record.evidence_sha256});

    await selector.selectOption(alternate.record.evaluation_id); await assertRecord(alternate); const handoffPreferences = await preferences();
    await stages.nth(0).click(); await expect(summary).toHaveCount(0); await mountEvaluation();
    await expect(history).toHaveAttribute('open', ''); await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue('default');
    await expect(group).toHaveValue('lot'); await assertRecord(alternate); expect(await preferences()).toEqual(handoffPreferences);
    await capture('actual-stage-return-retained-alternate-full-record', identity); await unchanged();
    controls.push({action: 'native-saved-evaluation-exact-record', dimension: 'handoff', actual_select: alternate.record.evaluation_id, stage_leave: 0, stage_return: 3,
      exact_record_identity_and_binding_hash_result_preserved: true, evidence_sha256: alternate.record.evidence_sha256, full_preference_bytes_preserved: true});
    // Existing pending labelset handoff: only the saved evaluation view is
    // retained across actual stage return; the project labelset stays default.
    const otherSelected = fixture.items.find((row: any) => row.variant === 'valid' && row.labelset_id === secondSet);
    expect(otherSelected.record.binding).toMatchObject({source_dataset_path: source, task: 'segmentation', labelset_id: secondSet});
    await choose(otherSelected); await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue(secondSet);
    const otherPreferences = await preferences(); await unchanged();
    await stages.nth(0).click(); await expect(summary).toHaveCount(0); await mountEvaluation();
    await expect(history).toHaveAttribute('open', ''); await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue(secondSet);
    await expect(group).toHaveValue('lot'); await assertRecord(otherSelected); expect(await preferences()).toEqual(otherPreferences);
    expect((await api('/api/project/labelsets')).active_id).toBe('default');
    await capture('actual-other-labelset-stage-return-full-record', identity); await unchanged();
    controls.push({action: 'native-saved-evaluation-labelset', dimension: 'handoff', actual_saved_labelset_select: secondSet,
      actual_saved_record_select: otherSelected.record.evaluation_id, stage_leave: 0, stage_return: 3,
      exact_labelset_record_identity_binding_hash_result_and_preference_bytes_preserved: true,
      evidence_sha256: otherSelected.record.evidence_sha256, active_project_labelset: 'default',
      automatic_labelset_activation_or_training_quality_approval: false, all_API_mutations: 0});
    expect(controls).toHaveLength(9); expect(mutations).toEqual([]);
    await stages.nth(0).click(); await expect(summary).toHaveCount(0); const apiAfter = await unchanged();
    const afterFile = path.join(w.logs, 'saved-selection-protected-after.json'); fs.writeFileSync(afterFile, JSON.stringify({
      roots, after: Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)])), apiAfter, mutations, controls}, null, 2)); e.addFile(afterFile);
    e.note('saved_evaluation_selection_lifecycle', {feature: 'F023', requirements: ['S4-13'], cells: controls, baseline, sourceElectron,
      actual_source_UI: true, full_unfiltered_original_project_annotation_input_trees_preserved: true, all_api_mutations: mutations,
      label_metadata_team_settings_reports_preserved: true, controlled_saved_reports_not_model_inference: true,
      invalid_conditions_are_controlled_optional_preferences: true, cancel_is_picker_dismissal_not_job_or_request: true,
      completed_store_reads: completedStoreReads, verified_store_reads: verifiedStoreReads, apiAfter, failed_store_reads: failedStoreReads,
      final_pending_store_reads: pendingStoreReads.size, final_unverified_store_reads: unverifiedStoreReads.size,
      final_actual_stage: 'Dataset activeStep1', final_evaluation_panel_absent: true,
      human_label_or_model_quality_approval: false, physical_device_frozen_build_Windows_acceptance: false, CPU_GPU_model_started: false});
  } finally {page.off('request', observe); page.off('request', beginStoreRead); page.off('requestfinished', finishStoreRead); page.off('requestfailed', failStoreRead);
    if (!page.isClosed()) await page.unroute('**/api/project/labelsets', failCatalog);}
}

test('saved evaluation choices refuse missing preferences, empty scopes and catalog errors while actual picker dismissal and stage return preserve exact records', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {const response = await page.request.fetch(renderer.origin + route, {
    method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})});
    expect(response.ok(), await response.text()).toBe(true); return response.json();};
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native saved evaluation choices refuse missing preferences, empty scopes and catalog errors while actual picker dismissal and stage return preserve exact records', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => window.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned saved-selection API HTTP ${response.status}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(window, workspace, evidence, api, true);
});

import fs from 'node:fs';
import path from 'node:path';
import {createHash, randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Route, Locator} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness = require('./fixtures/harness.cjs');
type RawReply = {method: string; status: number; body: string; url: string};
type Api = ((route: string, body?: unknown, method?: string) => Promise<any>) & {raw: Record<string, RawReply>; calls: RawReply[]; readbackMarker?: string};
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


export function matchesSavedHistory(method: string, address: string, scope: {origin: string; source: string}) {
  const url = new URL(address), keys = [...url.searchParams.keys()].sort();
  return method === 'GET' && url.origin === scope.origin && url.pathname === '/api/evaluation/history'
    && JSON.stringify(keys) === JSON.stringify(['labelset_id', 'source_dataset_path', 'task'])
    && url.searchParams.get('source_dataset_path') === scope.source && url.searchParams.get('task') === 'segmentation'
    && url.searchParams.get('labelset_id') === 'default';
}
export function assertSavedHistoryReply(actual: RawReply, original: RawReply, scope: {origin: string; source: string}) {
  expect(matchesSavedHistory(actual.method, actual.url, scope)).toBe(true); expect(actual.status).toBe(200); expect(actual).toEqual(original);
}
export function assertSavedHistoryFailure(actual: RawReply, scope: {origin: string; source: string}) {
  expect(matchesSavedHistory(actual.method, actual.url, scope)).toBe(true); expect(actual.status).toBe(503);
  expect(JSON.parse(actual.body)).toEqual({detail: 'Controlled saved evaluation record history GET failure'});
}
export function assertExactSavedRecord(actual: any, original: any, identity: any, result: any, reportSha: string, expectedSha: string) {
  expect(actual).toEqual(original); expect(identity).toEqual({evaluation_id: original.evaluation_id, evidence_sha256: original.evidence_sha256, binding: original.binding});
  expect(result).toEqual(original.result); expect(reportSha).toBe(expectedSha);
}
export function assertExactSavedCustody(actual: any, original: any) {expect(actual).toEqual(original);}
export function assertNoSavedRecordWrites(mutations: unknown[]) {expect(mutations).toEqual([]);}
export function assertSavedPreferences(actual: any, original: any, selectedId: string) {
  expect(actual).toEqual(original); expect(JSON.parse(actual.record.raw)).toEqual({version: 1, evaluation_id: selectedId});
}
async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, originalOrigin: string, url?: string) {
  const source = path.join(w.root, 'saved-record-error-originals'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'); fs.writeFileSync(original, png(64, 3, (x, y) => [x, y, 173]));
  const originalHash = fileSha(original);
  const project = await api('/api/project/create', {name: 'Owned saved evaluation record error', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  await api('/api/annotations/save', {image_id: 'part', image_path: original, image_width: 64, image_height: 64,
    actor: 'saved-selection-controlled-fixture', annotations: [{id: 'controlled-original-label', type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]}]});
  await api('/api/project/labelsets', {name: 'Controlled other saved labelset'});
  const secondSet = (await api('/api/project/labelsets')).labelsets.find((row: any) => row.id !== 'default').id;
  const sets = await api('/api/project/labelsets');
  expect(sets.active_id).toBe('default'); expect(sets.labelsets).toHaveLength(2);
  // Existing fixture data are controlled saved reports, not trained-model output
  // or approval of the synthetic initial label.
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/evaluation_selection_reports.py'), w.root, project.project_dir, source, secondSet],
  {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const selected = fixture.items.find((row: any) => row.variant === 'valid' && row.labelset_id === 'default');
  const alternate = fixture.items.find((row: any) => row.variant === 'selection_segmentation');
  expect(selected.record.binding).toMatchObject({source_dataset_path: source, task: 'segmentation', labelset_id: 'default'});
  expect(alternate.record.evaluation_id).not.toBe(selected.record.evaluation_id);
  expect(fixture.items.some((row: any) => row.record.binding.task === 'ocr')).toBe(false);
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
      '/api/runtime-services/capture-groups', '/api/runtime-services', '/api/evaluation/history', '/api/project/labelsets'].includes(new URL(request.url()).pathname);
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
    const visibleIdentity = JSON.parse(await identity.locator('pre').innerText());
    if (await result.getAttribute('open') === null) await result.locator('summary').click();
    await expect(result.locator('pre')).toBeVisible();
    assertExactSavedRecord(await api(recordRoute(item)), item.record, visibleIdentity,
      JSON.parse(await result.locator('pre').innerText()), fileSha(item.report_path), item.report_sha256);
  };
  const choose = async (item: any) => {
    if (await family.inputValue() !== item.record.binding.task) await family.selectOption(item.record.binding.task);
    if (await labelset.inputValue() !== item.labelset_id) await labelset.selectOption(item.labelset_id);
    await expect(selector.locator('option[value="' + item.record.evaluation_id + '"]')).toHaveCount(1);
    await selector.selectOption(item.record.evaluation_id); await assertRecord(item);
  };
  await navigate(); await choose(selected); await group.selectOption('lot');
  // Every default-store read and complete metadata/label query precedes the
  // full file baseline. A first visit may materialize empty local schemas.
  const idleEvaluationDefaults = await idleDefaultStores(api, source, project.task);
  const apiBefore: Record<string, any> = {...idleEvaluationDefaults};
  for (const endpoint of ['/api/project/current', '/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100',
    '/api/project/preferences', '/api/dataset/versions', '/api/dataset/metadata/split', '/api/project/labelsets',
    '/api/data-workbench/review-evaluations', '/api/data-workbench/review-queues', annotationRoute,
    historyRoute('segmentation'), historyRoute('segmentation', 'default'), historyRoute('segmentation', secondSet),
    historyRoute('ocr', 'default')]) apiBefore[endpoint] = await api(endpoint);
  expect(apiBefore['/api/project/labelsets']).toEqual(sets);
  expect(apiBefore[historyRoute('ocr', 'default')]).toEqual({items: [], total: 0});
  expect(apiBefore['/api/data-workbench/review-queues']).toEqual({queues: []});
  expect(apiBefore['/api/team-data'].settings).toMatchObject({editing_enabled: false, review_enabled: false, revision: 1});
  const metadata = apiBefore['/api/dataset/metadata?limit=100'].items; expect(metadata).toHaveLength(1);
  expect(metadata[0]).toMatchObject({file_path: original, content_hash: originalHash});
  apiBefore['/api/team-data/images/' + metadata[0].image_uuid] = await api('/api/team-data/images/' + metadata[0].image_uuid);
  expect(apiBefore[annotationRoute].annotations).toHaveLength(1); expect(apiBefore[annotationRoute].metadata.workflow_state).not.toBe('approved');
  for (const item of fixture.items) {apiBefore[recordRoute(item)] = await api(recordRoute(item)); expect(apiBefore[recordRoute(item)]).toEqual(item.record);}
  await settleStoreReads(); expect(await idleDefaultStores(api, source, project.task)).toEqual(idleEvaluationDefaults); await settleStoreReads();
  const roots = {source, project: project.project_dir, annotations: active.annotations_dir || project.annotations_dir,
    inputs: path.join(w.root, 'controlled-evaluation-inputs'), originalHarnessDataset: w.dataset};
  const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
  const rawBefore = Object.fromEntries(Object.keys(apiBefore).map(endpoint => [endpoint, {...api.raw[endpoint]}]));
  expect(Object.keys(rawBefore).every(endpoint => rawBefore[endpoint].status === 200)).toBe(true);
  const baseline = {roots, before, apiBefore, rawBefore, project, source, fixture, metadata,
    completedStoreReads: [...completedStoreReads], verifiedStoreReads: [...verifiedStoreReads], pendingStoreReads: pendingStoreReads.size};
  const beforeFile = path.join(w.logs, 'saved-record-error-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(before[name])) {
    const snapshot = path.join(w.logs, 'saved-record-error-protected-before', name, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true});
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
    const state = {trees: Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)])), raw: Object.fromEntries(Object.keys(rawBefore).map(endpoint => [endpoint, {...api.raw[endpoint]}]))};
    assertExactSavedCustody(state, {trees: before, raw: rawBefore});
    assertNoSavedRecordWrites(mutations); expect(fileSha(original)).toBe(originalHash);
    for (const image of w.images) expect(fileSha(image.path)).toBe(image.sha256); return {apiAfter, ...state};
  };
  const capture = async (label: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport(); await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-saved-record-error-${label}`);
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

  const scope = {origin: originalOrigin, source};
  const refresh = history.getByRole('button', {name: '평가 이력 새로고침', exact: true});
  const reevaluate = history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true});
  const selectedId = selected.record.evaluation_id;
  const savedPreferences = {all: await preferences(), record: await recordPreference(), view: await viewPreference()};
  assertSavedPreferences(savedPreferences, savedPreferences, selectedId);
  const originalHistory = rawBefore[historyRoute('segmentation', 'default')], originalCatalog = rawBefore['/api/project/labelsets'];
  expect(JSON.parse(originalHistory.body).items.some((row: any) => row.evaluation_id === selectedId)).toBe(true);
  expect(JSON.parse(originalHistory.body).items[0].evaluation_id).not.toBe(selectedId);
  await expect(reevaluate).toBeEnabled(); await assertRecord(selected); await unchanged();
  const responseSnapshot = async (response: any): Promise<RawReply> => {
    const request = response.request();
    return withStoreReadDeadline(request, async () => {
      expect(await response.finished()).toBeNull();
      return {method: request.method(), status: response.status(), url: response.url(), body: await response.text()};
    });
  };
  const waitHistory = () => page.waitForResponse(response => matchesSavedHistory(response.request().method(), response.url(), scope), {timeout: 10_000});
  const waitCatalog = () => page.waitForResponse(response => response.request().method() === 'GET'
    && new URL(response.url()).origin === originalOrigin && new URL(response.url()).pathname === '/api/project/labelsets', {timeout: 10_000});
  const failureReplies: RawReply[] = [], matchingFaultRequests: any[] = [];
  const failHistory = async (route: Route) => {
    const request = route.request();
    if (!matchesSavedHistory(request.method(), request.url(), scope)) {await route.continue(); return;}
    // This fixture tag is not an authentication credential. Only a GET from
    // the retained original main frame may bypass this test's renderer fault.
    if (sourceElectron && api.readbackMarker && await request.headerValue('accept') === api.readbackMarker) {
      expect(request.frame()).toBe(page.mainFrame()); await route.continue(); return;
    }
    matchingFaultRequests.push({method: request.method(), url: request.url(), body: request.postData()});
    await route.fulfill({status: 503, json: {detail: 'Controlled saved evaluation record history GET failure'}});
  };
  const onlyOriginalHistory = (address: URL) => matchesSavedHistory('GET', address.href, scope);
  const readFaults = async () => {
    // Each original matching request is completed within its original start-based
    // deadline. Panel catalog transitions can legitimately make a second GET;
    // no count assumption or new test-side request retry is used.
    for (const request of storeReadTimes.keys()) {
      if (!matchesSavedHistory(request.method(), request.url(), scope)) continue;
      const response = await request.response(); if (!response || response.status() !== 503) continue;
      if (failureReplies.some(reply => (reply as any).request_index === [...storeReadTimes.keys()].indexOf(request))) continue;
      const reply = await responseSnapshot(response); assertSavedHistoryFailure(reply, scope);
      failureReplies.push({...reply, request_index: [...storeReadTimes.keys()].indexOf(request)} as RawReply);
    }
  };
  let failedState: any, recoveredState: any;
  try {
    await page.route(onlyOriginalHistory, failHistory);
    try {
      const failed = waitHistory(); await refresh.click();
      const reply = await responseSnapshot(await failed); assertSavedHistoryFailure(reply, scope);
      await expect(history.getByRole('alert')).toHaveText('Controlled saved evaluation record history GET failure');
      await expect(selector).toHaveCount(0); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
      await expect(reevaluate).toBeDisabled(); await expect(group).toHaveCount(0);
      assertSavedPreferences({all: await preferences(), record: await recordPreference(), view: await viewPreference()}, savedPreferences, selectedId);
      failedState = await unchanged(); await readFaults(); expect(failureReplies.length).toBeGreaterThan(0);
      expect(matchingFaultRequests.length).toBe(failureReplies.length); assertNoSavedRecordWrites(mutations);
      await capture('history503-no-stale-record-or-reevaluation', history.getByRole('alert'));
    } finally {await page.unroute(onlyOriginalHistory, failHistory);}
    const restoredCatalog = waitCatalog(), restoredHistory = waitHistory(); await refresh.click();
    const realCatalog = await responseSnapshot(await restoredCatalog), realHistory = await responseSnapshot(await restoredHistory);
    expect(realCatalog).toEqual(originalCatalog); assertSavedHistoryReply(realHistory, originalHistory, scope);
    await expect(history.getByRole('alert')).toHaveCount(0); await assertRecord(selected);
    await expect(reevaluate).toBeEnabled(); await expect(group).toHaveValue('lot');
    const recoveredPreferences = {all: await preferences(), record: await recordPreference(), view: await viewPreference()};
    assertSavedPreferences(recoveredPreferences, savedPreferences, selectedId);
    recoveredState = await unchanged(); assertNoSavedRecordWrites(mutations);
    await capture('explicit-refresh200-same-original-record-full-identity-result', identity);
    controls.push({feature: 'F023', requirement: 'S4-13', action: 'native-saved-evaluation-exact-record', dimension: 'error',
      failureReplies, matchingFaultRequests, failedState, realCatalog, realHistory, recoveredState,
      selected_evaluation_id: selectedId, original_record: selected.record, original_report_sha256: selected.report_sha256,
      savedPreferences, recoveredPreferences, renderer_mutations: mutations, actual_explicit_refresh_after_route_removed: true,
      stale_selector_identity_results_absent: true, reevaluate_disabled_during_failure: true,
      controlled_saved_fixture_not_model_inference: true});
    expect(controls).toHaveLength(1); await stages.nth(0).click(); await expect(summary).toHaveCount(0);
    const finalState = await unchanged();
    const output = {schema: 'modu-vision.saved-evaluation-record-error-ui-proof/v1', cells: controls, baseline, finalState,
      sourceElectron, source_origin: originalOrigin, actual_direct_api_calls: api.calls, queries, renderer_mutations: mutations,
      completed_store_reads: completedStoreReads, verified_store_reads: verifiedStoreReads, failed_store_reads: failedStoreReads,
      final_pending_store_reads: pendingStoreReads.size, final_unverified_store_reads: unverifiedStoreReads.size,
      original_source_sha256: originalHash, original_stored_fixture: fixture, final_evaluation_panel_absent: true,
      controlled_renderer_history_transport_fault_not_real_backend_outage: true, CPU_GPU_model_started: false,
      human_label_or_model_quality_approval: false, physical_device_frozen_build_Windows_acceptance: false};
    const outputFile = path.join(w.logs, 'saved-evaluation-record-error-proof.json');fs.writeFileSync(outputFile, JSON.stringify(output, null, 2), {flag: 'wx'});e.addFile(outputFile);
    for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(before[name])) {
      const snapshot = path.join(w.logs, 'saved-record-error-protected-after', name, relative);fs.mkdirSync(path.dirname(snapshot), {recursive: true});
      fs.copyFileSync(path.join(root, relative), snapshot, fs.constants.COPYFILE_EXCL);e.addFile(snapshot);
    }
    e.note('saved_evaluation_record_error', output);
  } finally {
    page.off('request', observe); page.off('request', beginStoreRead);page.off('requestfinished', finishStoreRead);page.off('requestfailed', failStoreRead);
    if (!page.isClosed()) await page.unroute(onlyOriginalHistory, failHistory);
  }
}
function directApi(page: Page, origin: string): Api {
  const raw: Record<string, RawReply> = {}, calls: RawReply[] = [];
  // The actual renderer fault is observed separately. Original read-only backend
  // snapshots use this retained original-origin request context, not page.route.
  const api = (async (route: string, body?: unknown, method?: string) => {
    const verb = method || (body === undefined ? 'GET' : 'POST');
    const response = await page.request.fetch(origin + route, {timeout: 10_000, method: verb, ...(body === undefined ? {} : {data: body})});
    const reply = {method: verb, status: response.status(), body: await response.text(), url: response.url()};raw[route] = reply;calls.push(reply);
    expect(response.ok(), reply.body).toBe(true);return JSON.parse(reply.body);
  }) as Api;
  api.raw = raw;api.calls = calls;return api;
}
export function rendererApi(page: Page, origin: string): Api {
  const address = new URL(origin);expect(address.origin).toBe(origin);expect(address.protocol).toBe('http:');expect(address.hostname).toBe('127.0.0.1');
  const raw: Record<string, RawReply> = {}, calls: RawReply[] = [], readbackMarker = 'application/json; mv-e2e-readback=' + randomUUID();
  const api = (async (route: string, body?: unknown, method?: string) => {
    const verb = method || (body === undefined ? 'GET' : 'POST');
    // Use the existing trusted Electron frame. Its original main-session
    // onBeforeSendHeaders injects the token; the test never reads that token.
    const reply = await page.evaluate(async ({origin, route, body, verb, marker}) => {
      const started = performance.now(), deadline = started + 10_000;let timer: ReturnType<typeof setTimeout> | undefined;
      const operation = (async () => {
        const response = await fetch(origin + route, {method: verb,
          headers: {...(body === undefined ? {} : {'Content-Type': 'application/json'}), ...(verb === 'GET' ? {'accept': marker} : {})},
          ...(body === undefined ? {} : {body: JSON.stringify(body)})});
        const result = {method: verb, status: response.status, body: await response.text(), url: response.url};
        if (performance.now() > deadline) throw Error('Original native API body exceeded its absolute10s');return result;
      })();
      try {return await Promise.race([operation, new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(Error('Original native API body exceeded its absolute10s')), Math.max(0, deadline-performance.now()));
      })]);} finally {if (timer !== undefined) clearTimeout(timer);}
    }, {origin, route, body, verb, marker: readbackMarker});
    raw[route] = reply;calls.push(reply);expect(reply.status).toBeGreaterThanOrEqual(200);expect(reply.status).toBeLessThan(300);return JSON.parse(reply.body);
  }) as Api;
  api.raw = raw;api.calls = calls;api.readbackMarker = readbackMarker;return api;
}
test('saved evaluation history failure clears stale record and explicit refresh recovers exact original identity result and preference', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);await exercise(page, workspace, evidence, directApi(page, renderer.origin), false, renderer.origin, renderer.url);
});
test('native saved evaluation history failure clears stale record and explicit refresh recovers exact original identity result and preference', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const backend = await electronSession.waitForBackend(), origin = 'http://127.0.0.1:' + backend.port;
  await exercise(electronSession.window, workspace, evidence, rendererApi(electronSession.window, origin), true, origin);
});

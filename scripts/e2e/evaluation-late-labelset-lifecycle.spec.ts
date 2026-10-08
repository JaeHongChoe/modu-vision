import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Route, Locator, Response} from '@playwright/test';
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

// Installed only in the isolated test page. The exact original renderer fetch
// runs through Electron main's authenticated network path. Its network200/body
// completes before the test defers delivery of the SAME Response to the UI.
function installOriginalRendererResponseHold({origin, source, secondSet, binding, key}: {
  origin: string; source: string; secondSet: string; binding: string; key: string
}) {
  const target = window as any;
  if (target[key]) throw Error('Original renderer hold already installed');
  const previous = window.fetch;
  let consumed = false;
  const seen = new WeakSet<globalThis.Response>();
  const wrapper: typeof window.fetch = async (input, init) => {
    const address = new URL(input instanceof window.Request ? input.url : String(input), window.location.href);
    const method = String(init?.method || (input instanceof window.Request ? input.method : 'GET')).toUpperCase();
    const exact = address.origin === origin && address.pathname === '/api/evaluation/history'
      && address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === 'segmentation'
      && address.searchParams.get('labelset_id') === secondSet && [...address.searchParams.keys()].length === 3;
    if (!exact || method !== 'GET') return previous.call(window, input, init);
    if (consumed) throw Error('Exact original held renderer GET must occur once');
    consumed = true;
    const response = await previous.call(window, input, init);
    if (!(response instanceof window.Response) || seen.has(response) || response.bodyUsed || response.status !== 200
      || response.redirected || response.url !== address.href) throw Error('Exact original renderer Response refused');
    seen.add(response);
    const bytes = Array.from(new Uint8Array(await response.clone().arrayBuffer()));
    await target[binding]({phase: 'body', url: response.url, status: response.status, bytes,
      original_response_object: true, redirected: response.redirected});
    if (response.bodyUsed || !seen.has(response)) throw Error('Original renderer Response was consumed or replaced');
    await target[binding]({phase: 'delivery', url: response.url, status: response.status,
      same_original_response_object: true, original_body_unconsumed: true});
    return response;
  };
  target[key] = {previous, wrapper}; window.fetch = wrapper;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'late-labelset-originals'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'); fs.writeFileSync(original, png(64, 3, (x, y) => [x, y, 173]));
  const originalHash = fileSha(original);
  const project = await api('/api/project/create', {name: 'Owned late saved labelset lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  await api('/api/annotations/save', {image_id: 'part', image_path: original, image_width: 64, image_height: 64,
    actor: 'late-labelset-controlled-fixture', annotations: [{id: 'controlled-original-label', type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]}]});
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
  expect(fixture.items).toHaveLength(9); expect(new Set(fixture.items.map((row: any) => row.record.evaluation_id)).size).toBe(9);
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
      '/api/runtime-services/capture-groups', '/api/runtime-services', '/api/evaluation/history'].includes(new URL(request.url()).pathname);
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
  const other = fixture.items.find((row: any) => row.variant === 'valid' && row.labelset_id === secondSet);
  await navigate(); await choose(other); await choose(selected); await group.selectOption('lot');
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
  const beforeFile = path.join(w.logs, 'late-labelset-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(before[name])) {
    const snapshot = path.join(w.logs, 'late-labelset-protected-before', name, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true});
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
    for (const query of queries) {
      expect(query.source).toBe(source); expect(['segmentation', 'ocr']).toContain(query.task);
      expect([null, 'default', secondSet, emptySet]).toContain(query.labelset_id);
    }
    expect(mutations).toEqual([]); expect(fileSha(original)).toBe(originalHash); return apiAfter;
  };
  const capture = async (label: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport(); await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-late-labelset-${label}`);
  };
  const preferences = async () => page.evaluate(({projectId}) => Object.fromEntries(Object.keys(localStorage).filter(key =>
    key.startsWith('vision-evaluation-') && key.includes(projectId)).sort().map(key => [key, localStorage.getItem(key)])),
  {projectId: project.id});
  const selectedPreference = async (set: string) => page.evaluate(({projectId, source, set}) => {
    const prefix = 'vision-evaluation-record:', keys = Object.keys(localStorage).filter(key => {
      if (!key.startsWith(prefix)) return false;
      try {return JSON.stringify(JSON.parse(key.slice(prefix.length)).slice(0, 4)) === JSON.stringify([projectId, source, 'segmentation', set]);}
      catch {return false;}
    });
    if (keys.length !== 1) throw Error('Expected one exact owned source/task/labelset record preference');
    return {key: keys[0], raw: localStorage.getItem(keys[0])!};
  }, {projectId: project.id, source, set});
  const historyBodies: any[] = [], lateReads: any[] = [];
  let bodyIndex = 0;
  const readHistory = async (waiting: Promise<Response>, expectedStatus = 200) => {
    const response = await waiting, request = response.request(), address = new URL(response.url());
    expect(request.method()).toBe('GET'); expect(address.pathname).toBe('/api/evaluation/history');
    expect(address.searchParams.get('source_dataset_path')).toBe(source); expect(address.searchParams.get('task')).toBe('segmentation');
    const raw = await withStoreReadDeadline(request, async () => {
      expect(response.status()).toBe(expectedStatus); expect(await response.finished()).toBeNull(); return response.body();
    });
    const body = JSON.parse(raw.toString('utf8')), set = address.searchParams.get('labelset_id'); expect(set).not.toBeNull();
    expect(body).toEqual(expectedStatus === 200 ? apiBefore[historyRoute('segmentation', set!)]
      : {detail: 'Controlled current owned history GET failure while older labelset is held'});
    const file = path.join(w.logs, `late-labelset-history-body-${++bodyIndex}.json`); fs.writeFileSync(file, raw); e.addFile(file);
    const timing = storeReadTimes.get(request)!;
    const proof = {endpoint: address.pathname, method: 'GET', source, task: 'segmentation', labelset_id: set,
      status: response.status(), raw_body_path: file, raw_body_sha256: sha(raw), full_body: body,
      request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline};
    historyBodies.push(proof); return proof;
  };
  const selectSet = async (set: string) => {
    expect(await labelset.inputValue()).not.toBe(set);
    const read = historyResponse('segmentation', set); await labelset.selectOption(set); return readHistory(read);
  };
  const withinOriginalDeadline = async <T,>(request: Request, operation: () => Promise<T>): Promise<T> => {
    const timing = storeReadTimes.get(request); expect(timing).toBeDefined();
    const remaining = timing!.deadline - performance.now();
    if (remaining <= 0) throw Error('Original held history GET exceeded its absolute 10s deadline');
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {return await Promise.race([operation(), new Promise<T>((_resolve, reject) => {
      timer = setTimeout(() => reject(Error('Original held history GET exceeded its absolute 10s deadline')), remaining);
    })]);} finally {if (timer !== undefined) clearTimeout(timer);}
  };
  type HeldRead = {started: Promise<Request>; original: Promise<any>; release: () => void;
    finish: () => Promise<any>; cleanup: () => Promise<void>};
  const openHolds = new Set<HeldRead>();
  const holdOlderSet = async (dimension: string): Promise<HeldRead> => {
    expect(await labelset.inputValue()).toBe('default');
    const known = [...storeReadTimes.keys()].find(request => {const address = new URL(request.url()); return request.method() === 'GET'
      && address.pathname === '/api/evaluation/history' && address.searchParams.get('source_dataset_path') === source
      && address.searchParams.get('task') === 'segmentation';});
    expect(known).toBeDefined(); const origin = new URL(known!.url()).origin;
    const matches = (address: URL) => address.origin === origin && address.pathname === '/api/evaluation/history'
      && address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === 'segmentation'
      && address.searchParams.get('labelset_id') === secondSet && [...address.searchParams.keys()].length === 3;
    let heldRequest: Request | undefined, release!: () => void, complete!: () => void, failed!: (cause: unknown) => void;
    let fetched!: (value: any) => void, rejected!: (cause: unknown) => void, count = 0, deliveredCount = 0, networkBody: any, rendererDelivery: any;
    const gate = new Promise<void>(resolve => release = resolve), done = new Promise<void>((resolve, reject) => {complete = resolve; failed = reject;});
    const original = new Promise<any>((resolve, reject) => {fetched = resolve; rejected = reject;});
    void done.catch(() => undefined); void original.catch(() => undefined);
    const started = page.waitForRequest(request => request.method() === 'GET' && matches(new URL(request.url())), {timeout: 10_000});
    const network = page.waitForResponse(response => response.request().method() === 'GET' && matches(new URL(response.url())), {timeout: 10_000});
    void started.catch(() => undefined); void network.catch(() => undefined);
    const binding = '__moduVisionOriginalLateResponse_' + dimension, key = binding + '_fixture';
    await page.exposeBinding(binding, async ({page: owner}, payload) => {
      try {
        expect(owner).toBe(page);
        if (payload.phase === 'body') {
          heldRequest = await started; expect(heldRequest.method()).toBe('GET'); expect(matches(new URL(heldRequest.url()))).toBe(true);
          expect(++count).toBe(1); expect(payload.url).toBe(heldRequest.url()); expect(payload.status).toBe(200);
          expect(payload.original_response_object).toBe(true); expect(payload.redirected).toBe(false);
          const raw = Buffer.from(payload.bytes); networkBody = await withinOriginalDeadline(heldRequest, () => readHistory(network));
          expect((await network).request()).toBe(heldRequest);
          expect(raw).toEqual(fs.readFileSync(networkBody.raw_body_path)); expect(sha(raw)).toBe(networkBody.raw_body_sha256);
          const body = JSON.parse(raw.toString('utf8')); expect(body).toEqual(apiBefore[historyRoute('segmentation', secondSet)]);
          const file = path.join(w.logs, `late-labelset-${dimension}-actual-held-backend-body.json`); fs.writeFileSync(file, raw); e.addFile(file);
          fetched({source, task: 'segmentation', labelset_id: secondSet, backend_status: 200,
            raw_body_path: file, raw_body_sha256: sha(raw), full_body: body, response_body_substituted: false,
            transport: 'original authenticated renderer fetch; original network response is complete before UI promise release'});
          await withinOriginalDeadline(heldRequest, () => gate);
        } else {
          expect(payload.phase).toBe('delivery'); expect(heldRequest).toBeDefined(); expect(count).toBe(1); expect(++deliveredCount).toBe(1);
          expect(payload.url).toBe(heldRequest!.url()); expect(payload.status).toBe(200);
          expect(payload.same_original_response_object).toBe(true); expect(payload.original_body_unconsumed).toBe(true);
          const timing = storeReadTimes.get(heldRequest!)!, released = performance.now();
          expect(timing.finished).toBeDefined(); expect(timing.finished!).toBeLessThanOrEqual(released); expect(released).toBeLessThanOrEqual(timing.deadline);
          rendererDelivery = {request_started_ms: timing.started, original_network_finished_ms: timing.finished,
            renderer_promise_released_ms: released, absolute_deadline_ms: timing.deadline,
            same_original_response_object: true, original_body_unconsumed: true,
            original_HTTP_response_was_not_delayed_until_after_abandonment: true}; complete();
        }
      } catch (cause) {rejected(cause); failed(cause); throw cause;}
    });
    await page.evaluate(installOriginalRendererResponseHold, {origin, source, secondSet, binding, key});
    const restore = async () => page.evaluate(key => {const target = window as any, state = target[key];
      if (!state || window.fetch !== state.wrapper) throw Error('Original fetch fixture custody differs');
      window.fetch = state.previous; delete target[key];}, key);
    const held: HeldRead = {started, original, release,
      finish: async () => {
        const request = await started, originalBody = await withinOriginalDeadline(request, () => original);
        release(); await withinOriginalDeadline(request, () => done);
        expect(count).toBe(1); expect(deliveredCount).toBe(1);
        expect(networkBody.raw_body_sha256).toBe(originalBody.raw_body_sha256); expect(networkBody.full_body).toEqual(originalBody.full_body);
        const proof = {dimension, original: originalBody, original_network_response: networkBody, renderer_promise_delivery: rendererDelivery,
          actual_backend_and_client_network_200: true, exact_original_response_bytes: true,
          deferred_same_renderer_Response_not_deferred_HTTP_arrival: true}; lateReads.push(proof);
        await restore(); openHolds.delete(held); return proof;
      },
      cleanup: async () => {release(); try {await withinOriginalDeadline(await started, () => done);} finally {await restore(); openHolds.delete(held);}}
    };
    openHolds.add(held); return held;
  };
  const beginOlder = async (dimension: string) => {
    const held = await holdOlderSet(dimension); await labelset.selectOption(secondSet);
    const request = await held.started; expect(new URL(request.url()).searchParams.get('labelset_id')).toBe(secondSet);
    await withinOriginalDeadline(request, () => held.original); await expect(labelset).toHaveValue(secondSet); await expect(selector).toHaveCount(0); await expect(identity).toHaveCount(0);
    return held;
  };
  const noRecord = async () => {await expect(selector).toHaveCount(0); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history.getByRole('button', {name: '선택 모델 재평가 · 새 이력 저장', exact: true})).toBeDisabled();};
  let failures = 0;
  const currentQuery = (address: URL) => address.pathname === '/api/evaluation/history'
    && address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === 'segmentation'
    && address.searchParams.get('labelset_id') === 'default';
  const failCurrent = async (route: Route) => {
    expect(route.request().method()).toBe('GET'); expect(currentQuery(new URL(route.request().url()))).toBe(true); failures++;
    await route.fulfill({status: 503, json: {detail: 'Controlled current owned history GET failure while older labelset is held'}});
  };
  try {
    const emptyHeld = await beginOlder('empty'); await selectSet(emptySet); await expect(labelset).toHaveValue(emptySet); await noRecord();
    await expect(history).toContainText('저장된 평가 이력이 없습니다.'); await expect(history.getByRole('alert')).toHaveCount(0);
    const emptyPreferences = await preferences(); const emptyLate = await emptyHeld.finish(); await expect(labelset).toHaveValue(emptySet); await noRecord();
    await expect(history).toContainText('저장된 평가 이력이 없습니다.'); expect(await preferences()).toEqual(emptyPreferences);
    await capture('late-200-does-not-fill-current-real-empty-set', labelset); await unchanged();
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'empty', current_actual_labelset_id: emptySet,
      current_actual_GET_200_body: apiBefore[historyRoute('segmentation', emptySet)], older: emptyLate,
      simulated_response_to_create_empty: false, exact_preference_bytes_preserved: true});
    await selectSet('default'); await choose(selected);

    const invalidHeld = await beginOlder('invalid'), saved = await selectedPreference('default'), missingId = 'late_missing_owned_default_record';
    const invalidRaw = JSON.stringify({version: 1, evaluation_id: missingId});
    await page.evaluate(({key, raw}) => localStorage.setItem(key, raw), {key: saved.key, raw: invalidRaw});
    await selectSet('default'); await expect(selector).toHaveValue(''); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history.getByRole('alert')).toContainText('선택했던 평가 ' + missingId);
    expect((await selectedPreference('default')).raw).toBe(invalidRaw); const invalidPreferences = await preferences(); const invalidLate = await invalidHeld.finish();
    await expect(labelset).toHaveValue('default'); await expect(selector).toHaveValue(''); await expect(identity).toHaveCount(0); await expect(result).toHaveCount(0);
    await expect(history.getByRole('alert')).toContainText('선택했던 평가 ' + missingId); expect(await preferences()).toEqual(invalidPreferences);
    await capture('late-200-does-not-replace-refused-exact-preference', selector); await unchanged();
    await selector.selectOption(selected.record.evaluation_id); await assertRecord(selected); await expect(history.getByRole('alert')).toHaveCount(0); await unchanged();
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'invalid',
      controlled_optional_preference_fault: {key: saved.key, raw: invalidRaw, evaluation_id: missingId}, current_real_GET_200_scope: 'segmentation/default',
      older: invalidLate, silent_record_replacement: false, explicit_actual_valid_selection: selected.record.evaluation_id});

    const errorHeld = await beginOlder('error'); await page.route(currentQuery, failCurrent);
    const failedRead = historyResponse('segmentation', 'default'); await labelset.selectOption('default'); const currentError = await readHistory(failedRead, 503);
    await expect(history.getByRole('alert')).toHaveText('Controlled current owned history GET failure while older labelset is held'); await noRecord();
    const errorPreferences = await preferences(), errorLate = await errorHeld.finish(); await expect(labelset).toHaveValue('default'); await noRecord();
    await expect(history.getByRole('alert')).toHaveText('Controlled current owned history GET failure while older labelset is held'); expect(await preferences()).toEqual(errorPreferences);
    await capture('late-original-200-does-not-override-current-503', history.getByRole('alert')); await page.unroute(currentQuery, failCurrent); await unchanged();
    const recovery = historyResponse('segmentation', 'default'); await history.getByRole('button', {name: '평가 이력 새로고침', exact: true}).click();
    const currentRecovery = await readHistory(recovery); await assertRecord(selected); await expect(history.getByRole('alert')).toHaveCount(0); expect(failures).toBe(1); await unchanged();
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'error', current: currentError, older: errorLate,
      actual_explicit_refresh_recovery: currentRecovery, exact_recovered_record: selected.record, current_error_not_overridden_by_late_response: true});

    const cancelHeld = await beginOlder('cancel'), cancelPreferences = await preferences(); await stages.nth(0).click(); await expect(summary).toHaveCount(0);
    const cancelledViewLate = await cancelHeld.finish(); await expect(summary).toHaveCount(0); expect(await preferences()).toEqual(cancelPreferences); await unchanged();
    await capture('actual-Dataset-stage-abandonment-after-original-200', page.getByTitle('프로젝트 관리', {exact: true}));
    const returnRead = historyResponse('segmentation', secondSet); await mountEvaluation(); const reopenedSecond = await readHistory(returnRead);
    await expect(labelset).toHaveValue(secondSet); await assertRecord(other); expect(await preferences()).toEqual(cancelPreferences); await unchanged();
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'cancel', actual_stage_leave: 0, actual_stage_return: 3,
      older: cancelledViewLate, fresh_return_current: reopenedSecond, panel_absent_when_original_renderer_promise_released: true,
      exact_preferences_preserved: true, abandonment_only: true, explicit_confirm_or_request_job_cancel_claimed: false});
    await selectSet('default'); await choose(selected);

    const reopenHeld = await beginOlder('reopen'); await selectSet('default'); await selector.selectOption(alternate.record.evaluation_id); await assertRecord(alternate);
    const reopenPreferences = await preferences(), reopenLate = await reopenHeld.finish(); await assertRecord(alternate); expect(await preferences()).toEqual(reopenPreferences);
    const reloadReads = defaultReads(), reloadHistory = historyResponse('segmentation', 'default'); await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await expect(summary).toHaveCount(1);
    await finishDefaults(reloadReads); const actualReload = await readHistory(reloadHistory); await expect(history).toHaveAttribute('open', '');
    await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue('default'); await expect(group).toHaveValue('lot');
    await assertRecord(alternate); expect(await preferences()).toEqual(reopenPreferences); await capture('actual-reload-keeps-newer-exact-record-after-late-200', identity); await unchanged();
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'reopen', older: reopenLate, actual_renderer_reload: true,
      actual_reload_current: actualReload, preserved_record: alternate.record, full_preference_bytes_preserved: true,
      original_renderer_promise_released_before_reload: true, no_reload_aborted_response_claimed_as_200: true});

    const handoffHeld = await beginOlder('handoff'); await selectSet('default'); await selector.selectOption(selected.record.evaluation_id); await assertRecord(selected);
    const handoffPreferences = await preferences(); await stages.nth(0).click(); await expect(summary).toHaveCount(0);
    const remountRead = historyResponse('segmentation', 'default'); await mountEvaluation(); const currentRemount = await readHistory(remountRead);
    await expect(labelset).toHaveValue('default'); await assertRecord(selected); const handoffLate = await handoffHeld.finish();
    await expect(family).toHaveValue('segmentation'); await expect(labelset).toHaveValue('default'); await expect(group).toHaveValue('lot'); await assertRecord(selected);
    expect(await preferences()).toEqual(handoffPreferences); const apiAfterHandoff = await unchanged();
    expect(apiAfterHandoff['/api/project/current']).toEqual(apiBefore['/api/project/current']); expect(apiAfterHandoff['/api/project/labelsets'].active_id).toBe('default');
    expect(apiAfterHandoff['/api/dataset/metadata?limit=100'].items[0]).toMatchObject({file_path: original, content_hash: originalHash,
      image_uuid: metadata[0].image_uuid}); await capture('actual-stage-remount-fences-original-old-labelset-response', identity);
    controls.push({action: 'native-evaluation-late-labelset-selection', dimension: 'handoff', actual_stage_leave: 0, actual_stage_return: 3,
      remounted_current: currentRemount, older: handoffLate, exact_current_record: selected.record,
      project_id: project.id, original_source_path: original, original_sha256: originalHash, original_image_uuid: metadata[0].image_uuid,
      active_project_labelset: 'default', full_preference_bytes_preserved: true, saved_view_context_return_only: true,
      label_approval_training_adoption_or_quality_claimed: false});
    expect(controls.map(control => control.dimension)).toEqual(['empty', 'invalid', 'error', 'cancel', 'reopen', 'handoff']);
    expect(lateReads).toHaveLength(6); expect(openHolds.size).toBe(0); expect(failures).toBe(1); expect(mutations).toEqual([]);
    await stages.nth(0).click(); await expect(summary).toHaveCount(0); const apiAfter = await unchanged();
    const afterFile = path.join(w.logs, 'late-labelset-protected-after.json'); fs.writeFileSync(afterFile, JSON.stringify({roots,
      after: Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)])), apiAfter, mutations, controls, lateReads, historyBodies}, null, 2)); e.addFile(afterFile);
    e.note('late_saved_labelset_lifecycle', {feature: 'F023', requirements: ['S4-13'], action: 'native-evaluation-late-labelset-selection',
      cells: controls, sourceElectron, baseline, apiAfter, actual_source_UI: true, all_api_mutations: mutations,
      original_unchanged_actual_backend_response_holds: lateReads,
      held_transport_is_original_renderer_Response_delivery_not_late_HTTP_arrival: true, complete_scoped_history_reads: historyBodies,
      completed_store_reads: completedStoreReads, verified_store_reads: verifiedStoreReads, failed_store_reads: failedStoreReads,
      final_pending_store_reads: pendingStoreReads.size, final_unverified_store_reads: unverifiedStoreReads.size,
      full_unfiltered_project_original_annotation_settings_input_trees_preserved: true,
      controlled_reports_not_model_inference: true, invalid_is_optional_preference_fault: true, cancel_is_stage_abandonment_only: true,
      final_actual_stage: 'Dataset activeStep1', final_evaluation_panel_absent: true,
      human_label_approval_model_quality_training_adoption_CPU_GPU_physical_frozen_Windows_acceptance: false});
  } finally {
    for (const held of openHolds) held.release();
    try {await Promise.all([...openHolds].map(held => held.cleanup()));}
    finally {await page.unroute(currentQuery, failCurrent); page.off('request', observe); page.off('request', beginStoreRead);
      page.off('requestfinished', finishStoreRead); page.off('requestfailed', failStoreRead);}
  }
}

test('late saved labelset responses preserve current empty, refused and failed scopes through actual stage abandonment, reload and remount', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {const response = await page.request.fetch(renderer.origin + route, {
    method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})});
    expect(response.ok(), await response.text()).toBe(true); return response.json();};
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native late saved labelset responses preserve current empty, refused and failed scopes through actual stage abandonment, reload and remount', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => window.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned late-labelset API HTTP ${response.status}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(window, workspace, evidence, api, true);
});

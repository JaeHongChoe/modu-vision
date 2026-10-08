import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Locator, Page, Request, Route} from '@playwright/test';
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
  if (!fs.existsSync(root)) return result;
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return result;
}
const sorted = (value: any): any => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
const canonical = (value: any) => JSON.stringify(sorted(value));

async function initializeIdleEvaluationStores(api: Api, source: string, task: string) {
  const params = new URLSearchParams({source_dataset_path: source, task}).toString();
  const endpoints = ['/api/model-deployments/active?' + new URLSearchParams({source_dataset_path: source, task: 'ocr'}),
    '/api/model-deployments/active?' + params, '/api/model-deployments/history?' + params,
    '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts',
    '/api/runtime-services/capture-groups', '/api/runtime-services'];
  const records: Record<string, any> = {};
  // Each declared request is a GET. The constructors materialize this owned
  // fixture's empty default stores; no service, deployment or job is started.
  for (const endpoint of endpoints) records[endpoint] = await api(endpoint, undefined, 'GET');
  for (const endpoint of endpoints.filter(value => value.startsWith('/api/model-deployments/active?')))
    expect(records[endpoint]).toEqual({active: null, field_runtime_applied: false});
  expect(records['/api/model-deployments/history?' + params]).toEqual({revisions: []});
  expect(records['/api/fleet/targets']).toEqual({targets: []});
  expect(records['/api/fleet/rollouts']).toEqual({rollouts: []});
  expect(records['/api/fleet/capabilities']).toMatchObject({authentication: 'desktop_process_capability', actor_role: 'local_owner'});
  expect(records['/api/runtime-services/capture-groups']).toEqual({policy: null, groups: [], total: 0});
  expect(records['/api/runtime-services']).toMatchObject({runtime: {status: 'stopped'}, active: null, history: [],
    adapter_config: {enabled: false, modbus: null, mes: null},
    native_install: {prepared: false, registered: false, enabled: false, running: false, verified: false},
    recovery: {active: null, pending: null, last_operation: null}, runtime_build: null});
  return records;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'queue-progress-originals'); fs.mkdirSync(source);
  const originals = ['error', 'disagreement', 'threshold'].map((name, index) => {
    const file = path.join(source, name + '.png'); fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, 130 + index]));
    return {path: file, name, sha256: fileSha(file)};
  });
  const project = await api('/api/project/create', {name: 'Owned queue progress failure controls', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  // Controlled saved labels and reports establish this isolated fixture. No
  // model inference, human label approval, or metadata review is represented.
  for (const original of originals) await api('/api/annotations/save', {image_id: original.name, image_path: original.path,
    image_width: 64, image_height: 64, actor: 'queue-progress-fixture', annotations: [
      {id: 'controlled-original-' + original.name, type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]},
    ]});
  const seed = () => JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/review_queue_reports.py'), w.root, project.project_dir, source],
  {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const origin = seed(), alternate = seed(); expect(origin.record.evaluation_id).not.toBe(alternate.record.evaluation_id);
  expect(origin.record.created_at).toBeLessThan(alternate.record.created_at);
  const initialQueue = await api('/api/data-workbench/review-queues', {evaluation_id: origin.record.evaluation_id, threshold: .5, margin: .05});
  expect(initialQueue.items.map((row: any) => [row.relative_path, row.priority, row.state])).toEqual([
    ['error.png', 400, 'pending'], ['disagreement.png', 200, 'pending'], ['threshold.png', 100, 'pending'],
  ]);
  expect(initialQueue).toMatchObject({cursor: 0, revision: 1, history: [], origin: {
    evaluation_id: origin.record.evaluation_id, evidence_sha256: origin.record.evidence_sha256}});
  const queueEndpoint = '/api/data-workbench/review-queues/' + initialQueue.id, advanceEndpoint = queueEndpoint + '/advance';
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
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
  const panel = page.getByRole('region', {name: '저장된 검토 큐', exact: true});
  const choice = panel.getByLabel('저장 검토 큐 선택', {exact: true});
  const open = panel.getByRole('button', {name: '현재 항목 열기', exact: true});
  const review = panel.getByRole('button', {name: '검토 완료 · 다음', exact: true});
  const skip = panel.getByRole('button', {name: '보류 · 다음', exact: true});
  const returnOrigin = panel.getByRole('button', {name: '원래 평가·비교로 돌아가기', exact: true});
  const mountPanel = async () => {
    await stages.getByRole('button').nth(1).click();
    const focus = page.getByRole('button', {name: '집중 편집', exact: true});
    if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
    const summary = page.locator('summary').filter({hasText: '저장 검토 큐 · 오류·불일치·임계값 우선'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click(); await expect(panel).toBeVisible();
  };
  const reloadPanel = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await mountPanel();
  };
  await reloadPanel(); await expect(choice).toHaveValue(initialQueue.id); await expect(open).toBeEnabled();
  await page.getByRole('button', {name: '이미지 정보·검토', exact: true}).click();
  const actor = 'Owned queue lifecycle operator';
  await page.getByRole('textbox', {name: '작업자·검토자 이름', exact: true}).fill(actor);
  await page.getByRole('button', {name: '이미지 정보·검토', exact: true}).click();
  // A first real evaluation visit reads the model/fleet/runtime default stores.
  // Wait for those exact requests before returning to labeling. In particular,
  // model approval reads can overlap while SQLite WAL/SHM files are transient.
  const evaluationDefaultReads = () => {
    const targets = [
      ['/api/model-deployments/active', project.task], ['/api/model-deployments/history', project.task],
      ['/api/model-deployments/active', 'ocr'], ['/api/provenance/impact', null],
      ['/api/fleet/targets', null], ['/api/fleet/capabilities', null], ['/api/fleet/rollouts', null],
      ['/api/runtime-services/capture-groups', null], ['/api/runtime-services', null],
    ] as const;
    return targets.map(([endpoint, modelTask]) => page.waitForResponse(response => {
      const address = new URL(response.url());
      return response.request().method() === 'GET' && address.pathname === endpoint
        && (modelTask === null || (address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === modelTask));
    }, {timeout: 10_000}));
  };
  const completeEvaluationDefaults = async (waiting: ReturnType<typeof evaluationDefaultReads>) => {
    const responses = await Promise.all(waiting), observed = [];
    for (const response of responses) {
      const body = await withStoreReadDeadline(response.request(), async () => {
        expect(response.status()).toBe(200); expect(await response.finished()).toBeNull(); return response.json();
      });
      const address = new URL(response.url()), timing = storeReadTimes.get(response.request())!;
      if (address.pathname === '/api/provenance/impact')
        expect(body).toMatchObject({project_id: project.id, source_dataset_path: source, labelset_id: 'default'});
      observed.push({endpoint: address.pathname, source: address.searchParams.get('source_dataset_path'), task: address.searchParams.get('task'), body,
        request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline});
    }
    await settleStoreReads();
    return observed;
  };
  const warmEvaluationReads = evaluationDefaultReads();
  await stages.getByRole('button').nth(3).click();
  const initialEvaluationReads = await completeEvaluationDefaults(warmEvaluationReads);
  const warmHistorySummary = page.locator('summary').filter({hasText: /^평가 이력 · 제품\/Lot별 오류$/});
  if (await warmHistorySummary.locator('..').getAttribute('open') === null) await warmHistorySummary.click();
  await expect(page.getByRole('combobox', {name: '모델별 저장 평가', exact: true})).toHaveValue(alternate.record.evaluation_id);
  await mountPanel(); await expect(choice).toHaveValue(initialQueue.id); await expect(open).toBeEnabled();
  expect(await api(queueEndpoint)).toEqual(initialQueue);
  await settleStoreReads();
  const idleEvaluationDefaults = await initializeIdleEvaluationStores(api, source, project.task);
  await settleStoreReads();
  // Real default initialization and all complete API reads precede the baseline.
  const apiBefore: Record<string, any> = {};
  const protectedEndpoints = ['/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100',
    '/api/project/preferences', '/api/dataset/versions', '/api/dataset/metadata/split', '/api/data-workbench/review-evaluations',
    '/api/project/labelsets'];
  Object.assign(apiBefore, idleEvaluationDefaults);
  for (const endpoint of protectedEndpoints) apiBefore[endpoint] = await api(endpoint);
  expect(apiBefore['/api/project/labelsets'].labelsets.map((row: any) => row.id)).toEqual(['default']);
  const metadata = apiBefore['/api/dataset/metadata?limit=100'].items; expect(metadata).toHaveLength(3);
  expect(apiBefore['/api/team-data'].settings).toMatchObject({revision: 1, editing_enabled: false, review_enabled: false});
  expect(apiBefore['/api/data-workbench/review-evaluations'].evaluations.map((row: any) => row.id))
    .toEqual([alternate.record.evaluation_id, origin.record.evaluation_id]);
  for (const row of metadata) apiBefore['/api/team-data/images/' + row.image_uuid] = await api('/api/team-data/images/' + row.image_uuid);
  for (const original of originals) apiBefore[annotationRoute(original.path)] = await api(annotationRoute(original.path));
  expect(originals.every(original => apiBefore[annotationRoute(original.path)].annotations.length === 1
    && apiBefore[annotationRoute(original.path)].metadata.workflow_state !== 'approved')).toBe(true);
  expect(await api(queueEndpoint)).toEqual(initialQueue); expect(await api('/api/data-workbench/review-queues')).toEqual({queues: [initialQueue]});
  await settleStoreReads();
  expect(await initializeIdleEvaluationStores(api, source, project.task)).toEqual(idleEvaluationDefaults);
  await settleStoreReads();
  const roots = {source, project: project.project_dir, annotations: active.annotations_dir || project.annotations_dir};
  const treesBefore = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)]));
  const queueFiles = Object.keys(treesBefore.project).filter(relative => relative.endsWith('/review_queues/' + initialQueue.id + '.json'));
  expect(queueFiles).toHaveLength(1); const queueRelative = queueFiles[0];
  const queueLockRelative = path.posix.join(path.posix.dirname(path.posix.dirname(queueRelative)), 'review_queue.lock');
  expect(treesBefore.project[queueRelative]).toBe(sha(canonical(initialQueue)));
  expect(treesBefore.project[queueLockRelative]).toBeUndefined();
  const baseline = {roots, treesBefore, apiBefore, initialQueue, origin, alternate, metadata, initialEvaluationReads,
    verifiedStoreReads: [...verifiedStoreReads], pendingStoreReads: pendingStoreReads.size};
  const beforeFile = path.join(w.logs, 'queue-progress-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(treesBefore[name])) {
    const snapshot = path.join(w.logs, 'queue-progress-protected-before', name, relative);
    fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
  }
  let expectedQueue = structuredClone(initialQueue);
  const writes: any[] = [], expectedWrites: any[] = [], queueReads: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const address = new URL(request.url()); if (!address.pathname.startsWith('/api/')) return;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
      writes.push({method: request.method(), endpoint: address.pathname, body});
    }
    if (request.method() === 'GET' && address.pathname === queueEndpoint) queueReads.push({method: 'GET', endpoint: address.pathname});
  };
  page.on('request', observe);
  const checkFiles = () => {
    expect(tree(source)).toEqual(treesBefore.source); expect(tree(roots.annotations)).toEqual(treesBefore.annotations);
    const projectExpected = {...treesBefore.project, [queueRelative]: sha(canonical(expectedQueue))};
    if (expectedQueue.cursor) projectExpected[queueLockRelative] = sha(Buffer.alloc(0));
    expect(tree(project.project_dir)).toEqual(projectExpected);
    expect(JSON.parse(fs.readFileSync(path.join(project.project_dir, queueRelative), 'utf8'))).toEqual(expectedQueue);
  };
  const settleCustody = async () => {
    await settleStoreReads();
    expect(await initializeIdleEvaluationStores(api, source, project.task)).toEqual(idleEvaluationDefaults);
    await settleStoreReads();
  };
  const unchanged = async () => {
    await settleCustody();
    checkFiles(); for (const [endpoint, value] of Object.entries(apiBefore)) expect(await api(endpoint)).toEqual(value);
    expect(await api(queueEndpoint)).toEqual(expectedQueue); expect(await api('/api/data-workbench/review-queues')).toEqual({queues: [expectedQueue]});
    await settleStoreReads();
    checkFiles();
    expect(writes).toEqual(expectedWrites);
  };
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-queue-progress-${name}`);
  };
  // Fallback disables cached image responses without providing a mock image.
  const rawFallback = (route: Route) => route.fallback();
  const rawPattern = '**/api/dataset/raw/**'; await page.route(rawPattern, rawFallback);
  const imageResponses = (target: any) => {
    const raw = page.waitForResponse(response => {const address = new URL(response.url()); return response.request().method() === 'GET'
      && address.pathname === '/api/dataset/raw/' + encodeURIComponent(path.basename(target.file_path)) && address.searchParams.get('file_path') === target.file_path;});
    const annotation = page.waitForResponse(response => {const address = new URL(response.url()); return response.request().method() === 'GET'
      && address.pathname === '/api/annotations/' + path.basename(target.file_path, '.png') && address.searchParams.get('file_path') === target.file_path;});
    return {raw, annotation};
  };
  const verifyImage = async (pending: ReturnType<typeof imageResponses>, target: any, suffix: string) => {
    const raw = await pending.raw, annotation = await pending.annotation; expect(raw.status()).toBe(200); expect(annotation.status()).toBe(200);
    const bytes = await raw.body(), labels = await annotation.json();
    expect(bytes).toEqual(fs.readFileSync(target.file_path)); expect(sha(bytes)).toBe(target.source_sha256);
    expect(labels).toEqual(apiBefore[annotationRoute(target.file_path)]);
    const meta = metadata.find((row: any) => row.file_path === target.file_path); expect(meta).toBeDefined();
    expect(labels.metadata).toMatchObject({file_path: target.file_path, content_hash: target.source_sha256,
      image_uuid: meta.image_uuid, revision: meta.revision});
    const selected = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: path.basename(target.file_path), exact: true});
    await expect(selected.locator('..')).toHaveClass(/border-blue-500/);
    expect(new URL((await selected.getAttribute('src'))!, page.url()).searchParams.get('file_path')).toBe(target.file_path);
    await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
    const snapshot = path.join(w.logs, 'queue-progress-' + suffix + '.png'); fs.writeFileSync(snapshot, bytes); e.addFile(snapshot);
    return {path: target.file_path, image_uuid: meta.image_uuid, source_sha256: sha(bytes), revision: meta.revision,
      raw_status: 200, annotations_status: 200, complete_original_and_annotations_equal: true};
  };
  let listFailures = 0, advanceFailures = 0, originFailures = 0;
  const failList = async (route: Route) => {
    expect(route.request().method()).toBe('GET'); expect(new URL(route.request().url()).pathname).toBe('/api/data-workbench/review-queues');
    listFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled exact queue list GET failure'}});
  };
  const failOrigin = async (route: Route) => {
    expect(route.request().method()).toBe('GET'); expect(new URL(route.request().url()).pathname).toBe(queueEndpoint);
    originFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled exact origin queue GET failure'}});
  };
  try {
    await page.route('**/api/data-workbench/review-queues', failList);
    try {
      const waiting = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/data-workbench/review-queues');
      await reloadPanel(); expect((await waiting).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled exact queue list GET failure');
      await expect(choice).toHaveValue(''); await expect(choice.locator('option')).toHaveCount(1);
      await expect(open).toHaveCount(0); await expect(review).toHaveCount(0); await expect(skip).toHaveCount(0);
      await settleCustody(); checkFiles(); expect(writes).toEqual([]); await capture('list-503-no-stale-action', panel.getByRole('alert'));
    } finally {await page.unroute('**/api/data-workbench/review-queues', failList);}
    const recovered = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === '/api/data-workbench/review-queues');
    await reloadPanel(); const realList = await recovered; expect(realList.status()).toBe(200); expect(await realList.json()).toEqual({queues: [initialQueue]});
    await expect(choice).toHaveValue(initialQueue.id); await expect(open).toBeEnabled(); await expect(panel.getByRole('alert')).toHaveCount(0);
    await unchanged(); expect(listFailures).toBe(1);
    controls.push({action: 'U015.saved-queue-selected-reopen', dimension: 'error', actual_reload: true,
      exact_GET_503_count: listFailures, failure_selection_empty_no_queue_actions: true, saved_queue_preserved: true, real_200_recovery: true});

    // The real blank option is an empty selection, not an invented cancellation.
    await choice.selectOption(''); await expect(choice).toHaveValue(''); await expect(open).toHaveCount(0);
    await expect(review).toHaveCount(0); await expect(skip).toHaveCount(0); await expect(returnOrigin).toHaveCount(0);
    await unchanged(); await capture('actual-blank-selection', choice);
    await reloadPanel(); await expect(choice).toHaveValue(initialQueue.id); await expect(open).toBeEnabled(); await unchanged();
    controls.push({action: 'U015.saved-queue-selected-reopen', dimension: 'empty', setup: 'real blank queue option',
      response_simulated_to_create_condition: false, queue_actions_absent: true, no_mutations: true,
      actual_reload_falls_back_to_existing_first_queue: initialQueue.id});

    await page.locator('[data-labeling-filmstrip]').getByRole('img', {name: 'threshold.png', exact: true}).click();
    const firstImages = imageResponses(initialQueue.items[0]); const firstRead = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
    await open.click(); expect((await firstRead).status()).toBe(200); await verifyImage(firstImages, initialQueue.items[0], 'initial-open');
    await expect(review).toBeEnabled(); await expect(skip).toBeEnabled(); await unchanged();
    const applyExpectedAdvance = (actual: any, state: 'reviewed' | 'skipped') => {
      const next = structuredClone(expectedQueue), index = next.cursor, relative = next.items[index].relative_path;
      expect(actual.items[index].reviewed_at).toEqual(expect.any(Number)); expect(Number.isFinite(actual.items[index].reviewed_at)).toBe(true);
      expect(actual.history[index].at).toEqual(expect.any(Number)); expect(Number.isFinite(actual.history[index].at)).toBe(true);
      next.items[index] = {...next.items[index], state, actor, reviewed_at: actual.items[index].reviewed_at};
      next.cursor++; next.revision++; next.history.push({relative_path: relative, state, actor, at: actual.history[index].at});
      expect(actual).toEqual(next); expectedQueue = next;
    };
    const advanceWithRetry = async (button: Locator, state: 'reviewed' | 'skipped', action: string) => {
      const body = {expected_revision: expectedQueue.revision, relative_path: expectedQueue.items[expectedQueue.cursor].relative_path, state, actor};
      const failAdvance = async (route: Route) => {
        expect(route.request().method()).toBe('POST'); expect(new URL(route.request().url()).pathname).toBe(advanceEndpoint);
        expect(route.request().postDataJSON()).toEqual(body); advanceFailures++;
        await route.fulfill({status: 503, json: {detail: 'Controlled exact ' + state + ' advance POST failure'}});
      };
      await page.route('**' + advanceEndpoint, failAdvance);
      try {
        expectedWrites.push({method: 'POST', endpoint: advanceEndpoint, body});
        const failed = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === advanceEndpoint);
        await button.click(); const response = await failed; expect(response.status()).toBe(503); expect(response.request().postDataJSON()).toEqual(body);
        await expect(panel.getByRole('alert')).toContainText('Controlled exact ' + state + ' advance POST failure');
        await expect(panel).toContainText('검토 진행 ' + expectedQueue.cursor + ' / 3'); await expect(button).toBeEnabled();
        await unchanged(); await capture(state + '-503-retained', panel.getByRole('alert'));
      } finally {await page.unroute('**' + advanceEndpoint, failAdvance);}
      const target = expectedQueue.items[expectedQueue.cursor + 1], images = imageResponses(target);
      expectedWrites.push({method: 'POST', endpoint: advanceEndpoint, body});
      const retried = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === advanceEndpoint);
      await button.click(); const response = await retried; expect(response.status()).toBe(200); expect(response.request().postDataJSON()).toEqual(body);
      const saved = await response.json(); applyExpectedAdvance(saved, state);
      const image = await verifyImage(images, target, state + '-real-retry-next-original');
      await expect(panel).toContainText('검토 진행 ' + expectedQueue.cursor + ' / 3'); await expect(panel.getByRole('alert')).toHaveCount(0);
      await unchanged(); await capture(state + '-real-retry-cursor', panel);
      controls.push({action: 'U015.' + action, dimension: 'error', exact_POST_503_count: 1,
        failed_request_preserved_complete_queue: true, real_200_retry: true, retry_body: body, saved, next_original: image,
        only_one_cursor_revision_history_increment: true, label_approval: false});
    };
    await advanceWithRetry(review, 'reviewed', 'saved-queue-review-next');
    await advanceWithRetry(skip, 'skipped', 'saved-queue-skip-next'); expect(advanceFailures).toBe(2);
    // One final real, whitelisted cursor move establishes the exhausted state.
    // It is setup for the new empty-open condition, not another success claim.
    const lastBody = {expected_revision: expectedQueue.revision, relative_path: expectedQueue.items[expectedQueue.cursor].relative_path, state: 'reviewed', actor};
    expectedWrites.push({method: 'POST', endpoint: advanceEndpoint, body: lastBody});
    const last = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === advanceEndpoint);
    await review.click(); const lastResponse = await last; expect(lastResponse.status()).toBe(200); expect(lastResponse.request().postDataJSON()).toEqual(lastBody);
    applyExpectedAdvance(await lastResponse.json(), 'reviewed'); await expect(panel).toContainText('큐의 모든 항목을 검토했습니다.');
    expect(expectedQueue).toMatchObject({cursor: 3, revision: 4}); await expect(open).toBeDisabled(); await unchanged();
    await reloadPanel(); await expect(choice).toHaveValue(initialQueue.id); await expect(panel).toContainText('검토 진행 3 / 3');
    await expect(open).toBeDisabled(); await expect(panel).not.toContainText('다음:'); const noOpenReads = queueReads.length;
    await capture('exhausted-real-queue-open-disabled', open); expect(queueReads).toHaveLength(noOpenReads); await unchanged();
    controls.push({action: 'U015.saved-queue-open', dimension: 'empty', setup: 'three real whitelisted cursor moves after controlled saved labels/reports',
      response_simulated_to_create_condition: false, actual_saved_queue: expectedQueue, actual_reload: true, open_disabled_no_current_item: true,
      disabled_control_not_clicked: true, saved_labels_and_complete_originals_preserved: true});

    await page.route('**' + queueEndpoint, failOrigin);
    try {
      const failed = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
      await returnOrigin.click(); expect((await failed).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled exact origin queue GET failure');
      await expect(panel).toBeVisible(); await expect(page.getByRole('combobox', {name: '모델별 저장 평가', exact: true})).toHaveCount(0);
      await settleCustody(); checkFiles(); expect(writes).toEqual(expectedWrites); await capture('origin-503-stays-labeling', panel.getByRole('alert'));
    } finally {await page.unroute('**' + queueEndpoint, failOrigin);}
    expect(originFailures).toBe(1); await unchanged();
    const realOrigin = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
    const realHistory = page.waitForResponse(response => {
      const address = new URL(response.url());
      return response.request().method() === 'GET' && address.pathname === '/api/evaluation/history'
        && address.searchParams.get('source_dataset_path') === source && address.searchParams.get('task') === project.task
        && (address.searchParams.get('labelset_id') || 'default') === origin.record.binding.labelset_id;
    });
    const finalEvaluationReads = evaluationDefaultReads();
    await returnOrigin.click(); const confirmed = await realOrigin; expect(confirmed.status()).toBe(200); expect(await confirmed.json()).toEqual(expectedQueue);
    const historyResponse = await realHistory; expect(historyResponse.status()).toBe(200);
    const savedHistory = await historyResponse.json(); expect(savedHistory.items.find((row: any) => row.evaluation_id === origin.record.evaluation_id)).toEqual(origin.record);
    await expect(page.getByRole('combobox', {name: '모델별 저장 평가', exact: true})).toHaveValue(origin.record.evaluation_id);
    const originDetails = page.locator('summary').filter({hasText: /^평가 버전·데이터·모델 해시 확인$/}).locator('..');
    if (await originDetails.getAttribute('open') === null) await originDetails.locator('summary').click();
    expect(JSON.parse(await originDetails.locator('pre').innerText())).toEqual({evaluation_id: origin.record.evaluation_id,
      evidence_sha256: origin.record.evidence_sha256, binding: origin.record.binding});
    await completeEvaluationDefaults(finalEvaluationReads);
    await capture('real-200-exact-older-origin-hash', originDetails);
    // Dataset is actualStep 1: the model panels are unmounted and the global
    // readiness hook does not launch new impact readers in this stage.
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0);
    await expect(page.getByRole('combobox', {name: '모델별 저장 평가', exact: true})).toHaveCount(0);
    await unchanged();
    controls.push({action: 'U015.saved-queue-stale-origin', dimension: 'error', exact_GET_503_count: originFailures,
      labeling_stage_preserved_on_failure: true, real_200_retry: true, older_origin_id: origin.record.evaluation_id,
      actual_selected_id: origin.record.evaluation_id, exact_origin_evidence_sha256: origin.record.evidence_sha256,
      complete_saved_origin_equal: true, latest_alternate_not_selected: alternate.record.evaluation_id, queue_and_labels_preserved: true});
    expect(controls).toHaveLength(6); expect(writes).toEqual(expectedWrites); expect(writes).toHaveLength(5); await unchanged();
    const afterFile = path.join(w.logs, 'queue-progress-protected-after.json'); fs.writeFileSync(afterFile,
      JSON.stringify({trees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])),
        apiBefore, complete_queue: expectedQueue, writes, exact_allowed_writes: expectedWrites}, null, 2)); e.addFile(afterFile);
    e.note('saved_queue_progress_lifecycle', {requirements: ['S3-09', 'S4-13'], cells: controls, baseline, sourceElectron,
      actual_source_ui: true, all_protected_files_unfiltered: true, full_annotations_metadata_team_settings_preserved: true,
      all_api_mutations: writes, exact_allowed_writes: expectedWrites, queueReads, queue_file_and_new_empty_lock_only: true,
      store_read_completions: completedStoreReads, verified_store_reads: verifiedStoreReads, failed_store_reads: failedStoreReads,
      final_pending_store_reads: pendingStoreReads.size, final_unverified_store_reads: unverifiedStoreReads.size,
      final_actual_stage: 'Dataset activeStep1', final_model_panels_absent: true, original_stage3_identity_and_screenshot_before_leave: true,
      queue_review_state_not_human_label_approval: true, labels_saved_after_baseline: false, controlled_reports_not_model_inference: true,
      human_annotation_or_quality_approval: false, actual_training_inference_or_gpu: false,
      physical_device_or_frozen_package_or_windows_acceptance: false});
  } finally {
    page.off('request', observe);
    page.off('request', beginStoreRead); page.off('requestfinished', finishStoreRead); page.off('requestfailed', failStoreRead);
    if (!page.isClosed()) {
      await page.unroute(rawPattern, rawFallback); await page.unroute('**/api/data-workbench/review-queues', failList);
      await page.unroute('**' + queueEndpoint, failOrigin);
    }
  }
}

test('saved queue progress retries preserve labels and empty selection and origin failures retain exact saved records', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native saved queue progress retries and real empty states preserve complete originals labels and older origin', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned queue progress API ${response.status}: ${await response.text()}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

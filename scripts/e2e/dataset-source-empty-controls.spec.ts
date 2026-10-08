import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {inflateSync} from 'node:zlib';
import type {Page, Request, Response} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test.use({actionTimeout: 10_000});
type Reply = {method: string; route: string; request: unknown; status: number; raw: string; url: string; raw_sha256?: string; raw_size?: number; started: number; deadline: number; finished: number};
type Api = (route: string, body?: unknown, method?: string) => Promise<Reply>;
type Tree = Record<string, {kind: 'directory'} | {kind: 'file'; size: number; sha256: string}>;
type Write = {method: string; url: string; body: string | null};
type Clock = {request: Request; started: number; deadline: number; row?: any; pending?: Promise<void>; failure?: unknown};
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const EMPTY_DETAIL = 'Import a dataset into this project before creating a version.';
const ROUTES = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/training-workspace/tasks'];
const EMPTY_ROUTES = ['/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics'];
const CELLS = ['U013.readiness-diagnose.empty', 'U013.group-split-preview.empty', 'U013.group-split-apply.empty'];

export function assertSourceNullReply(reply: Reply, project: any) {
  expect(project.source_dataset_dir).toBeNull(); expect(project.task).toBe('classification');
  expect(reply.method).toBe('GET'); expect(reply.request).toBeNull(); expect(ROUTES).toContain(reply.route);
  expect(new URL(reply.url).pathname + new URL(reply.url).search).toBe(reply.route); expect(reply.raw_sha256).toBe(sha(reply.raw)); expect(reply.raw_size).toBe(Buffer.byteLength(reply.raw));
  if (EMPTY_ROUTES.includes(reply.route)) {expect(reply.status).toBe(422); expect(JSON.parse(reply.raw)).toEqual({detail: EMPTY_DETAIL});}
  else expect(reply.status).toBe(200);
  for (const value of [reply.started, reply.deadline, reply.finished]) expect(Number.isFinite(value)).toBe(true);
  expect(reply.deadline).toBe(reply.started + 10_000); expect(reply.finished).toBeGreaterThanOrEqual(reply.started); expect(reply.finished).toBeLessThanOrEqual(reply.deadline);
}
export function assertSetupWrites(native: boolean, origin: string, rows: Write[], create: Reply) {
  expect(create.method).toBe('POST'); expect(create.route).toBe('/api/project/create'); expect(create.status).toBe(200);
  expect(create.request).toEqual({name: 'Owned source-empty dataset controls', task: 'classification'});
  expect(rows).toEqual(native ? [{method: 'POST', url: origin + create.route, body: JSON.stringify(create.request)}] : []);
}
export function assertRawClock(row: any) {
  expect(typeof row.raw).toBe('string'); expect(row.raw_sha256).toBe(sha(row.raw)); expect(row.raw_size).toBe(Buffer.byteLength(row.raw));
  expect(['GET', 'POST', 'PUT', 'PATCH', 'DELETE']).toContain(row.method); expect(row.terminal).toBeNull();
  for (const value of [row.started, row.deadline, row.finished]) expect(Number.isFinite(value)).toBe(true);
  expect(row.deadline).toBe(row.started + 10_000); expect(row.finished).toBeGreaterThanOrEqual(row.started); expect(row.finished).toBeLessThanOrEqual(row.deadline);
  expect(Number.isInteger(row.index)).toBe(true); expect(row.index).toBeGreaterThanOrEqual(0);
}
export function assertHarnessPixels(raw: Buffer, label: string) {
  expect(['ok', 'ng']).toContain(label); expect(raw.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  let cursor = 8, header: Buffer | undefined; const data: Buffer[] = [];
  while (cursor < raw.length) {
    const size = raw.readUInt32BE(cursor), name = raw.toString('ascii', cursor + 4, cursor + 8), payload = raw.subarray(cursor + 8, cursor + 8 + size);
    expect(cursor + size + 12).toBeLessThanOrEqual(raw.length);
    if (name === 'IHDR') {expect(header).toBeUndefined(); header = payload;} if (name === 'IDAT') data.push(payload); cursor += size + 12;
  }
  expect(cursor).toBe(raw.length); expect(header).toBeDefined(); expect(header!.readUInt32BE(0)).toBe(32); expect(header!.readUInt32BE(4)).toBe(32); expect([...header!.subarray(8)]).toEqual([8, 2, 0, 0, 0]);
  const pixels = inflateSync(Buffer.concat(data)), expected = Buffer.alloc(32 * 97);
  for (let y = 0; y < 32; y++) for (let x = 0; x < 32; x++) {const value = label === 'ng' && x >= 8 && x < 16 && y >= 8 && y < 16 ? 30 : 180; expected.fill(value, y * 97 + 1 + x * 3, y * 97 + 4 + x * 3);}
  expect(pixels.equals(expected)).toBe(true); return {width: 32, height: 32, channels: 3, pixels: 1024, exact_all_class_fixture_RGB: true};
}

function protectedTree(root:string):Tree{
 const result:Tree={};const walk=(directory:string)=>{
  const entry=fs.lstatSync(directory);expect(entry.isSymbolicLink()).toBe(false);expect(entry.isDirectory()).toBe(true);
  expect(fs.realpathSync(directory)).toBe(directory);result[path.relative(root,directory).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(directory).sort()){
   const file=path.join(directory,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);
   if(before.isDirectory())walk(file);else{
    expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);
    const descriptor=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let raw:Buffer;
    try{const opened=fs.fstatSync(descriptor);expect([opened.dev,opened.ino,opened.size]).toEqual([before.dev,before.ino,before.size]);raw=fs.readFileSync(descriptor);
     const after=fs.fstatSync(descriptor),named=fs.lstatSync(file);
     expect([after.dev,after.ino,after.size,after.mtimeMs,after.ctimeMs]).toEqual([opened.dev,opened.ino,opened.size,opened.mtimeMs,opened.ctimeMs]);
     expect([named.dev,named.ino,named.size,named.mtimeMs,named.ctimeMs]).toEqual([opened.dev,opened.ino,opened.size,opened.mtimeMs,opened.ctimeMs]);
    }finally{fs.closeSync(descriptor);}
    result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:sha(raw!)};
   }
  }
 };walk(root);return result;
}

async function exercise(page: Page, w: Workspace, e: Evidence, invoke: Api, native: boolean, origin: string, url?: string) {
  const calls: Reply[] = [], writes: Write[] = [], options: Write[] = [], observed: any[] = [], preObserver: any[] = [], snapshots: any[] = [];
  const clocks = new Map<Request, Clock>(), prePending: Promise<void>[] = [], failures: unknown[] = []; const observerWallCutoff = Date.now();
  const listener = (request: Request) => {
    if (!new URL(request.url()).pathname.startsWith('/api/')) return; expect(new URL(request.url()).origin).toBe(origin);
    const row = {method: request.method(), url: request.url(), body: request.postData()};
    if (request.method() === 'OPTIONS') {expect(row.body).toBeNull(); options.push(row); return;}
    if (request.method() === 'HEAD') return;
    const started = performance.now(); clocks.set(request, {request, started, deadline: started + 10_000});
    if (request.method() !== 'GET') writes.push(row);
  };
  const failed = (request: Request) => {const row = clocks.get(request); if (row) row.failure = request.failure()?.errorText || 'Original request failed';};
  const onResponse = (response: Response) => {
    const request = response.request(); if (!new URL(response.url()).pathname.startsWith('/api/') || ['HEAD', 'OPTIONS'].includes(request.method())) return;
    const row = clocks.get(request), eventStarted = performance.now(), started = row?.started ?? eventStarted, deadline = row?.deadline ?? eventStarted + 10_000;
    const pending = (async () => {let timer: NodeJS.Timeout | undefined;
      try {
        if (!row) {expect(request.method()).toBe('GET'); expect(request.postData()).toBeNull(); const actualStart = request.timing().startTime; expect(Number.isFinite(actualStart)).toBe(true); expect(actualStart).toBeGreaterThan(0); expect(actualStart).toBeLessThanOrEqual(observerWallCutoff);}
        const [raw, terminal] = await Promise.race([Promise.all([response.text(), response.finished()]), new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original API response full body completed late')), Math.max(0, deadline - performance.now()));})]);
        const finished = performance.now(); expect(terminal).toBeNull(); expect(finished).toBeLessThanOrEqual(deadline);
        const captured = {method: request.method(), url: request.url(), request_body: request.postData(), status: response.status(), raw, raw_sha256: sha(raw), raw_size: Buffer.byteLength(raw), started, deadline, finished, terminal, index: row ? [...clocks.keys()].indexOf(request) : null};
        if (row) {row.row = captured; assertRawClock(captured); observed.push(captured);} else preObserver.push({...captured, index: null, request_actual_wall_start: request.timing().startTime, observer_wall_cutoff: observerWallCutoff, clock_scope: 'original response-event only; original request start unavailable', full_request_10s_claim: false});
      } catch (cause) {if (row) row.failure = cause; else failures.push(cause);} finally {clearTimeout(timer);}
    })();
    if (row) {expect(row.pending).toBeUndefined(); row.pending = pending;} else prePending.push(pending);
  };
  page.on('request', listener); page.on('requestfailed', failed); page.on('response', onResponse);
  const settle = async () => {
    for (const row of clocks.values()) {
      if (!row.pending && !row.failure) await expect.poll(() => Boolean(row.pending || row.failure), {timeout: Math.max(1, row.deadline - performance.now())}).toBe(true);
      expect(row.failure).toBeUndefined(); await row.pending; expect(row.failure).toBeUndefined(); expect(row.row).toBeDefined(); assertRawClock(row.row);
    }
    await Promise.all(prePending); expect(failures).toEqual([]);
  };
  const api: Api = async (...args) => {const started = performance.now(), deadline = started + 10_000; let timer: NodeJS.Timeout | undefined;
    try {const result = await Promise.race([invoke(...args), new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original fixture API full body completed late')), Math.max(0, deadline - performance.now()));})]);
      expect(performance.now()).toBeLessThanOrEqual(deadline); expect(result.deadline).toBe(result.started + 10_000); expect(result.finished).toBeLessThanOrEqual(result.deadline); expect(result.url).toBe(origin + args[0]); JSON.parse(result.raw); const row = {...result, raw_sha256: sha(result.raw), raw_size: Buffer.byteLength(result.raw)}; calls.push(row); return row;
    } finally {clearTimeout(timer);}
  };
  try {
    await settle();
    const create = await api('/api/project/create', {name: 'Owned source-empty dataset controls', task: 'classification'}, 'POST'), project = JSON.parse(create.raw);
    expect(create.status).toBe(200); expect(project.source_dataset_dir).toBeNull(); expect(project.task).toBe('classification'); expect(project.id).toMatch(/^[0-9a-f]{8}$/); expect(project.active_labelset_id).toBe('default');
    const parent = native ? path.join(w.userData, 'projects') : w.projects;
    expect(path.dirname(project.project_dir)).toBe(parent); expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir); expect(fs.readdirSync(project.dataset_dir)).toEqual([]); await settle();
    if (url) await page.goto(url); else await page.reload();
    const controls = async () => {
      await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
      await expect(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true})).toBeVisible();
      const entry = page.getByRole('button', {name: '이미지 검토·그룹 분할·라벨 교환', exact: true});
      await expect(entry).toBeVisible(); await expect(entry).toBeDisabled(); await expect(entry).toHaveAttribute('aria-expanded', 'false');
      await expect(page.getByRole('region', {name: '데이터 검토와 라벨 교환', exact: true})).toHaveCount(0);
      await expect(page.getByRole('button', {name: '분할 미리보기', exact: true})).toHaveCount(0); await expect(page.getByRole('button', {name: '분할 적용', exact: true})).toHaveCount(0);
      await expect(page.locator('summary').filter({hasText: '데이터 준비 상태 · 품질과 중복 진단'})).toHaveCount(0);
      await expect(page.getByRole('region', {name: '데이터 준비 진단', exact: true})).toHaveCount(0); await expect(page.getByRole('button', {name: '준비 상태 진단', exact: true})).toHaveCount(0);
    };
    await controls(); await settle();
    const roots = {project: project.project_dir, empty_project_dataset: project.dataset_dir, harness_original_dataset: w.dataset};
    const state = async () => {
      await settle(); const beforeReadTrees: Record<string, Tree> = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, protectedTree(root)]));
      const api: Record<string, any> = {}, raw_api: Record<string, any> = {};
      for (const route of ROUTES) {const reply = await apiCall(route); assertSourceNullReply(reply, project); api[route] = JSON.parse(reply.raw); raw_api[route] = {status: reply.status, raw: reply.raw};}
      await settle(); const trees: Record<string, Tree> = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, protectedTree(root)]));
      const storage = await page.evaluate(() => ({local: Object.fromEntries(Object.entries(localStorage).sort(([a], [b]) => a.localeCompare(b))), session: Object.fromEntries(Object.entries(sessionStorage).sort(([a], [b]) => a.localeCompare(b)))}));
      const storage_hashes = Object.fromEntries(Object.entries(storage).map(([kind, entries]) => [kind, Object.fromEntries(Object.entries(entries).map(([key, value]) => [key, {size: Buffer.byteLength(value), sha256: sha(value)}]))]));
      return {api, raw_api, trees, storage_hashes, before_read_trees: beforeReadTrees};
    };
    const apiCall = api;
    // Complete real default GET initialization before the only protected baseline.
    for (const route of ROUTES) assertSourceNullReply(await api(route), project); await settle();
    const setupWrites = [...writes]; assertSetupWrites(native, origin, setupWrites, create); writes.length = 0;
    const before = await state(); expect(before.trees).toEqual(before.before_read_trees); const baselineReadIndex = clocks.size;
    expect(before.api['/api/project/current']).toEqual(project); expect(before.api['/api/training-workspace/tasks'].tasks).toEqual([]);
    expect(before.api['/api/training-workspace/tasks'].source_dataset_path).toBeNull(); expect(before.api['/api/training-workspace/tasks'].labelset_id).toBe('default');
    const originals = w.images.map(image => {const raw = fs.readFileSync(image.path); expect(raw.length).toBe(image.bytes); expect(sha(raw)).toBe(image.sha256); return {...image, pixels: assertHarnessPixels(raw, image.label)};});
    for (const [name, root] of Object.entries(roots)) for (const [relative, record] of Object.entries(before.trees[name])) {
      const copy = path.join(w.logs, 'source-empty-before-copy', name, relative);
      if (record.kind === 'directory') fs.mkdirSync(copy, {recursive: true}); else {fs.mkdirSync(path.dirname(copy), {recursive: true}); fs.copyFileSync(path.join(root, relative), copy); expect(sha(fs.readFileSync(copy))).toBe(record.sha256); e.addFile(copy);}
    }
    const snapshot = (tag: string, value: any) => {const file = path.join(w.logs, 'source-empty-' + tag + '.json'); fs.writeFileSync(file, JSON.stringify({roots, state: value}, null, 2) + '\n', {flag: 'wx'}); e.addFile(file); snapshots.push({tag, file, state: value});};
    snapshot('before', before); await controls(); await e.screenshot(page, `${native ? 'native' : 'browser'}-source-empty-readiness-and-split-unavailable`);
    const after = await state(); expect(after).toEqual(before); expect(writes).toEqual([]); snapshot('after-controls', after);
    await settle(); await page.reload(); await controls(); await settle();
    const reopened = await state(); expect(reopened).toEqual(before); expect(writes).toEqual([]); snapshot('after-reopen', reopened);
    await e.screenshot(page, `${native ? 'native' : 'browser'}-source-empty-controls-remain-unavailable-after-reload`); await settle();
    const final = await state(); expect(final).toEqual(before); expect(writes).toEqual([]); snapshot('final', final);
    expect(calls.filter(row => row.method !== 'GET')).toEqual([create]);
    const postBaselineReads = observed.filter(row => row.index >= baselineReadIndex);
    for (const row of postBaselineReads) {const requestURL = new URL(row.url), route = requestURL.pathname + requestURL.search; if (ROUTES.includes(route)) assertSourceNullReply({...row, route, request: row.request_body}, project);}
    expect(postBaselineReads.filter(row => new URL(row.url).pathname === '/api/data-workbench/diagnostics')).toEqual([]);
    for (const image of originals) {const raw = fs.readFileSync(image.path); expect(sha(raw)).toBe(image.sha256); expect(raw.length).toBe(image.bytes); assertHarnessPixels(raw, image.label);}
    const proof = {cells: CELLS, native, project, roots, create, setup_renderer_writes: setupWrites, post_baseline_business_writes: writes, OPTIONS_transport_requests: options, fixture_calls: calls, observed_full_API_replies: observed, baseline_read_index: baselineReadIndex, post_baseline_full_API_replies: postBaselineReads, pre_observer_GET_replies: preObserver, snapshots, originals,
      scope: {no_selected_project_source: true, source_dataset_dir: null, project_dataset_empty: true, harness_disk_images_exist: true, zero_import_POSTs: true, owning_controls_absent_or_disabled_only: true, workflow_entry_force_click: false,
        zero_image_backend_diagnosis_or_split_refusal: false, cancellation: false, handoff: false, model_or_training_execution: false, human_quality_or_parent_acceptance: false, whole_workspace_or_userData_tree_claim: false,
        complete_project_and_explicit_original_dataset_trees_with_empty_dirs: true, project_dataset_subtree_overlap_declared: true, before_file_copies_retained: true, full_raw_API_status_JSON_retained: true,
        native_error_capture_unavailable: native, clocked_original_request_fullbody_budget_ms: 10000, pre_observer_full_request_clock_claim: false, pre_observer_only_original_response_event_budget_ms: 10000, OPTIONS_response_cleanliness_claim: false, local_and_session_Storage_values_only_hashes: true}};
    const file = path.join(w.logs, 'dataset-source-empty-controls-proof.json'); fs.writeFileSync(file, JSON.stringify(proof, null, 2) + '\n', {flag: 'wx'}); e.addFile(file);
    e.note('dataset_source_empty_controls', {proof_path: file, proof_sha256: sha(fs.readFileSync(file)), proof_size: fs.statSync(file).size});
  } finally {page.off('request', listener); page.off('requestfailed', failed); page.off('response', onResponse);}
}

test('source-less classification project keeps readiness and grouped split controls unavailable after reload', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(120_000); await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method = 'GET') => {const started = Date.now(), deadline = started + 10_000;
    const response = await request.fetch(renderer.origin + route, {method, ...(body === undefined ? {} : {data: body}), timeout: deadline - Date.now()});
    const raw = await response.text(), finished = Date.now(); expect(finished).toBeLessThanOrEqual(deadline); return {method, route, request: body ?? null, status: response.status(), raw, url: response.url(), started, deadline, finished};
  };
  await exercise(page, workspace, evidence, api, false, renderer.origin, renderer.url);
});
test('source Electron no selected original keeps readiness and grouped split controls unavailable after reload', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(120_000); const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method = 'GET') => page.evaluate(async ({port, route, body, method}) => {
    const started = Date.now(), deadline = started + 10_000, controller = new AbortController(), timer = setTimeout(() => controller.abort(), Math.max(0, deadline - Date.now()));
    try {const response = await fetch(`http://127.0.0.1:${port}${route}`, {method, signal: controller.signal, ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
      const raw = await response.text(), finished = Date.now(); if (finished > deadline) throw Error('Owned fixture full body completed late'); return {method, route, request: body ?? null, status: response.status, raw, url: response.url, started, deadline, finished};
    } finally {clearTimeout(timer);}
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true, `http://127.0.0.1:${backend.port}`);
});

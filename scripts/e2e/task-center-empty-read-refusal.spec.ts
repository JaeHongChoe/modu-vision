import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';

test.use({actionTimeout: 10_000});
type Call = {method: string; route: string; request: unknown; status: number; raw: string; started: number; deadline: number; finished: number};
type Api = (route: string, body?: unknown, method?: string) => Promise<Call>;
const sha = (data: Buffer | string) => crypto.createHash('sha256').update(data).digest('hex');
const TASKS = '/api/training-workspace/tasks';
const DETAIL = 'Controlled exact task-center read unavailable';

function tree(root: string): Record<string, unknown> {
  const result: Record<string, unknown> = {'': {kind: 'directory'}};
  const walk = (directory: string) => {
    for (const name of fs.readdirSync(directory).sort()) {
      const file = path.join(directory, name), relative = path.relative(root, file).split(path.sep).join('/');
      const before = fs.lstatSync(file, {bigint: true});
      if (before.isSymbolicLink()) throw Error('Owned evidence tree cannot contain links');
      if (before.isDirectory()) {result[relative] = {kind: 'directory'}; walk(file);}
      else {
        if (!before.isFile() || before.nlink !== 1n) throw Error('Owned evidence tree requires regular single-link files');
        const bytes = fs.readFileSync(file), after = fs.lstatSync(file, {bigint: true});
        for (const key of ['dev', 'ino', 'size', 'mtimeNs', 'ctimeNs'] as const) expect(after[key]).toBe(before[key]);
        result[relative] = {kind: 'file', size: bytes.length, sha256: sha(bytes)};
      }
    }
  };
  walk(root); return result;
}

export function assertTaskRead(row: any, origin: string, expectedStatus: number, expectedBody: unknown) {
  expect(row.method).toBe('GET'); expect(row.url).toBe(origin + TASKS); expect(row.request_body).toBeNull();
  expect(row.status).toBe(expectedStatus); expect(JSON.parse(row.raw)).toEqual(expectedBody);
  expect(row.raw_sha256).toBe(sha(row.raw)); expect(row.raw_size).toBe(Buffer.byteLength(row.raw));
  for (const number of [row.started, row.deadline, row.finished]) expect(Number.isFinite(number)).toBe(true);
  expect(row.deadline).toBe(row.started + 10_000); expect(row.finished).toBeGreaterThanOrEqual(row.started); expect(row.finished).toBeLessThanOrEqual(row.deadline);
  expect(row.terminal).toBeNull(); expect(Number.isInteger(row.read_index)).toBe(true); expect(row.read_index).toBeGreaterThanOrEqual(0);
}
export function assertPhaseReads(phase: string, rows: any[], controls: any[], origin: string, own: any, foreign: string) {
  expect(['initial', 'invalid', 'error', 'invalid-reopen', 'error-reopen']).toContain(phase);
  expect(rows.length).toBeGreaterThanOrEqual(1); const indexes = rows.map(row => row.read_index); expect(new Set(indexes).size).toBe(indexes.length);
  const expected = phase === 'invalid' ? {...own, source_dataset_path: foreign} : phase === 'error' ? {detail: DETAIL} : own;
  const status = phase === 'error' ? 503 : 200;
  for (const row of rows) assertTaskRead(row, origin, status, expected);
  if (phase === 'invalid' || phase === 'error') {
    expect(controls.length).toBe(rows.length); expect(new Set(controls.map(row => row.read_index)).size).toBe(controls.length);
    expect(controls.map(row => row.read_index).sort((a,b) => a-b)).toEqual([...indexes].sort((a,b) => a-b));
    for (const control of controls) {expect(control.phase).toBe(phase); expect(control.method).toBe('GET'); expect(control.url).toBe(origin + TASKS); expect(control.request_body).toBeNull(); expect(control.status).toBe(status); expect(control.raw).toBe(JSON.stringify(expected));}
  } else expect(controls).toEqual([]);
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, native: boolean, origin: string, url?: string) {
  const fixtureCalls: Call[] = [], invoke = api;
  api = async (...args) => {const result = await invoke(...args); fixtureCalls.push(result); return result;};
  const setup: Call[] = [];
  const create = await api('/api/project/create', {name: 'Owned task-center read refusals', task: 'classification'}, 'POST'); setup.push(create);
  setup.push(await api('/api/project/update', {source_dataset_dir: workspace.dataset}, 'PUT'));
  setup.push(await api('/api/dataset/import', {folder_path: workspace.dataset, task: 'classification', validate_images: true}, 'POST'));
  const current = await api('/api/project/current'), project = JSON.parse(current.raw);
  const roots = {project: project.project_dir, source: workspace.dataset};
  const routes = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/versions', '/api/dataset/revisions', TASKS];
  const state = async () => {
    const replies: Record<string, unknown> = {}, rawReplies: Record<string, unknown> = {};
    for (const route of routes) {const reply = await api(route); expect(reply.status).toBe(200); replies[route] = JSON.parse(reply.raw); rawReplies[route] = {status: reply.status, raw: reply.raw};}
    return {api: replies, raw_api: rawReplies, trees: {project: tree(roots.project), source: tree(roots.source)}};
  };
  const original = await api(TASKS), tasks = JSON.parse(original.raw);
  expect(original.status).toBe(200); expect(tasks.tasks).toEqual([]); expect(tasks.source_dataset_path).toBe(workspace.dataset); expect(tasks.labelset_id).toBe('default');
  const writes: Array<{method: string; url: string; body: string | null}> = [], transportOptions: Array<{method: string; url: string; body: string | null}> = [];
  type ReadClock = {started: number; deadline: number; read_index: number; pending?: Promise<void>; row?: any; failure?: unknown};
  const clocks = new Map<any, ReadClock>(), observed: any[] = [], preObserverTaskResponses: any[] = [];
  const listener = (request: any) => {
    const pathname = new URL(request.url()).pathname;
    if (pathname.startsWith('/api/')) {
      const row = {method: request.method(), url: request.url(), body: request.postData()};
      if (request.method() === 'OPTIONS') {expect(row.body).toBeNull(); transportOptions.push(row);}
      else if (!['GET', 'HEAD'].includes(request.method())) writes.push(row);
    }
    if (pathname === TASKS && request.method() === 'GET') {const started = performance.now(); clocks.set(request, {started, deadline: started + 10_000, read_index: clocks.size});}
  };
  const requestFailed = (request: any) => {const clock = clocks.get(request); if (clock) clock.failure = request.failure()?.errorText || 'Original task request failed';};
  const replyListener = (response: any) => {
    const request = response.request(); if (new URL(request.url()).pathname !== TASKS || request.method() !== 'GET') return;
    const clock = clocks.get(request);
    if (!clock) {preObserverTaskResponses.push({method: request.method(), url: request.url(), status: response.status(), request_start_clock_unavailable: true}); return;}
    expect(clock.pending).toBeUndefined();
    clock.pending = (async () => {
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        const [body, terminal] = await Promise.race([Promise.all([response.text(), response.finished()]), new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original task read full response completed late')), Math.max(0, clock.deadline - performance.now()));})]);
        const finished = performance.now(); expect(terminal).toBeNull(); expect(finished).toBeLessThanOrEqual(clock.deadline);
        clock.row = {method: request.method(), url: request.url(), request_body: request.postData(), status: response.status(), raw: body, raw_sha256: sha(body), raw_size: Buffer.byteLength(body), started: clock.started, deadline: clock.deadline, read_index: clock.read_index, finished, terminal};
        observed.push(clock.row);
      } catch (cause) {clock.failure = cause;} finally {clearTimeout(timer);}
    })();
  };
  const settle = async () => {for (const clock of clocks.values()) {
    if (!clock.pending && !clock.failure) await expect.poll(() => Boolean(clock.pending || clock.failure), {timeout: Math.max(1, clock.deadline - performance.now())}).toBe(true);
    expect(clock.failure).toBeUndefined(); await clock.pending; expect(clock.failure).toBeUndefined(); expect(clock.row).toBeDefined();
  }};
  const awaitOriginal = async (response: any) => {const clock = clocks.get(response.request()); expect(clock).toBeDefined(); await clock!.pending; expect(clock!.failure).toBeUndefined(); expect(clock!.row).toBeDefined(); return clock!.row;};
  page.on('request', listener); page.on('requestfailed', requestFailed); page.on('response', replyListener);
  if (url) await page.goto(url); else await page.reload();
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  const details = page.locator('details').filter({has: page.locator('summary').filter({hasText: '작업 센터 · 현재 프로젝트'})}).first();
  const summary = details.locator('summary').first(), select = details.getByLabel('저장 작업 다시 열기', {exact: true});
  const open = async () => {
    if (await details.count() === 0) await page.getByRole('navigation', {name: '프로젝트 작업 공간', exact: true}).getByRole('button', {name: '작업 센터', exact: true}).click();
    else if (await details.getAttribute('open') === null) await summary.click();
  };
  const close = async () => {if (await details.getAttribute('open') !== null) await summary.click();};
  const empty = async () => {await expect(summary).toContainText('현재 프로젝트 0개'); await expect(select.locator('option')).toHaveCount(1); await expect(select).toHaveValue(''); await expect(details.getByRole('button', {name: '비교 작업·결과 열기', exact: true})).toHaveCount(0);};
  const controlled: Array<{phase: string; read_index: number; method: string; url: string; request_body: string | null; status: number; raw: string}> = [];
  const phaseReads: Array<{phase: string; rows: any[]; controls: any[]}> = [], snapshots: Array<{tag: string; file: string; state: any}> = [];
  const saveState = async (tag: string) => {const value = await state(); await settle(); const file = path.join(workspace.logs, 'task-center-' + tag + '-snapshot.json'); fs.writeFileSync(file, JSON.stringify({roots, state: value}, null, 2) + '\n', {flag: 'wx'}); evidence.addFile(file); snapshots.push({tag, file, state: value}); return value;};
  const foreign = workspace.root + '/unselected-foreign-source';
  const openRead = async (phase: string) => {
    const firstIndex = clocks.size;
    const awaited = page.waitForResponse(response => response.request().method() === 'GET' && response.url() === origin + TASKS, {timeout: 10_000});
    await open(); const row = await awaitOriginal(await awaited);
    assertTaskRead(row, origin, phase === 'error' ? 503 : 200, phase === 'invalid' ? {...tasks, source_dataset_path: foreign} : phase === 'error' ? {detail: DETAIL} : tasks);
    return firstIndex;
  };
  const finishPhase = async (phase: string, firstIndex: number) => {
    await close(); await settle(); const rows = observed.filter(row => row.read_index >= firstIndex), controls = controlled.filter(row => row.phase === phase);
    assertPhaseReads(phase, rows, controls, origin, tasks, foreign); phaseReads.push({phase, rows, controls});
  };
  const first = await openRead('initial'); await empty();
  const before = await saveState('before');
  await select.focus(); await select.press('Home'); await select.press('Enter'); await empty();
  await evidence.screenshot(page, `${native ? 'native' : 'browser'}-empty-task-center-refuses-comparison-handoff`);
  await finishPhase('initial', first); expect(await saveState('after-empty')).toEqual(before);
  for (const phase of ['invalid', 'error'] as const) {
    const route = '**/api/training-workspace/tasks';
    const handle = async (intercepted: any) => {
      const request = intercepted.request(); if (request.method() === 'OPTIONS') {await intercepted.continue(); return;} expect(request.method()).toBe('GET'); expect(request.url()).toBe(origin + TASKS); expect(request.postData()).toBeNull();
      const clock = clocks.get(request); expect(clock).toBeDefined();
      const body = phase === 'invalid' ? {...tasks, source_dataset_path: foreign} : {detail: DETAIL};
      const raw = JSON.stringify(body), status = phase === 'invalid' ? 200 : 503;
      controlled.push({phase, read_index: clock!.read_index, method: request.method(), url: request.url(), request_body: request.postData(), status, raw});
      await intercepted.fulfill({status, contentType: 'application/json', body: raw});
    };
    await page.route(route, handle);
    const firstIndex = await openRead(phase);
    await expect(details.getByRole('alert')).toHaveText(phase === 'invalid' ? '현재 프로젝트 작업 응답과 출처가 다릅니다. 다시 확인하세요.' : DETAIL);
    await empty(); await evidence.screenshot(page, `${native ? 'native' : 'browser'}-task-center-${phase}-retains-empty-owning-scope`);
    await finishPhase(phase, firstIndex); await page.unroute(route, handle);
    expect(await saveState('after-' + phase)).toEqual(before);
    const recovery = await openRead(phase + '-reopen'); await empty(); await expect(details.getByRole('alert')).toHaveCount(0);
    await finishPhase(phase + '-reopen', recovery); expect(await saveState('after-' + phase + '-reopen')).toEqual(before);
  }
  expect(writes).toEqual([]); for (const row of setup) expect(row.status).toBe(200);
  const after = await saveState('after'); await settle();
  for (const phase of ['invalid', 'error']) {const matches = controlled.filter(row => row.phase === phase); expect(matches.length).toBeGreaterThanOrEqual(1);}
  expect(observed.filter(row => row.status === 503).length).toBe(controlled.filter(row => row.phase === 'error').length);
  expect(preObserverTaskResponses).toEqual([]); expect(writes).toEqual([]);
  for (const row of observed) {const controlledRow = controlled.find(control => control.read_index === row.read_index); assertTaskRead(row, origin, controlledRow?.status ?? 200, controlledRow ? JSON.parse(controlledRow.raw) : tasks);}
  for (const image of workspace.images) expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
  page.off('request', listener); page.off('requestfailed', requestFailed); page.off('response', replyListener);
  const proof = {cells: ['U003.completed-comparison-task-handoff.empty', 'U003.completed-comparison-task-handoff.invalid', 'U003.completed-comparison-task-handoff.error'], native, project, roots, setup, before, after, controlled, phase_reads: phaseReads, snapshots, fixture_calls: fixtureCalls, original_observed_task_fullbody_clocks: observed, pre_observer_task_responses: preObserverTaskResponses, OPTIONS_transport_requests: transportOptions, original_images: workspace.images, post_observer_business_writes: writes, actual_task_list_empty: tasks,
    scope: {selected_completed_job: false, actual_comparison_or_model_execution: false, cancellation: false, quality_or_parent_acceptance: false, invalid_source_response_controlled: true, error_transport_response_controlled: true, native_capture_unavailable: native,
      lawful_panel_poll_ms: 2500, controlled_response_cardinality: 'every actual matching phase GET is declared with identical exact body; >=1 required; no test retries',
      initial_and_recovery_original_GET200_completed_before_UI_checks: true, OPTIONS_business_write_claim: false, OPTIONS_response_error_cleanliness_claim: false, before_file_byte_copies_retained: false, snapshot_full_unfiltered_hash_and_raw_API_records_retained: true}};
  expect(proof.after).toEqual(before);
  const proofPath = path.join(workspace.root, 'logs/task-center-empty-read-refusal-proof.json');
  fs.writeFileSync(proofPath, JSON.stringify(proof, null, 2) + '\n', {flag: 'wx'}); evidence.addFile(proofPath); evidence.note('task_center_empty_read_refusal', {proof_path: proofPath, proof_sha256: sha(fs.readFileSync(proofPath)), proof_size: fs.statSync(proofPath).size});
}

test('empty task center refuses foreign scope and exact read503 before explicit reopen', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(120_000);
  const api: Api = async (route, body, method = 'GET') => {
    const started = Date.now(), deadline = started + 10_000;
    const response = await request.fetch(renderer.origin + route, {method, ...(body === undefined ? {} : {data: body}), timeout: deadline - Date.now()});
    const raw = await response.text(), finished = Date.now(); expect(finished).toBeLessThanOrEqual(deadline);
    return {method, route, request: body ?? null, status: response.status(), raw, started, deadline, finished};
  };
  await exercise(page, workspace, evidence, api, false, renderer.origin, renderer.url);
});
test('source Electron empty task center keeps project after invalid read and exact503', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  test.setTimeout(120_000); const {window} = electronSession, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method = 'GET') => window.evaluate(async ({port, route, body, method}) => {
    const started = Date.now(), deadline = started + 10_000, controller = new AbortController(), timer = setTimeout(() => controller.abort(), Math.max(0, deadline - Date.now()));
    try {
      const response = await fetch(`http://127.0.0.1:${port}${route}`, {method, signal: controller.signal, ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
      const raw = await response.text(), finished = Date.now(); if (finished > deadline) throw Error('Owned fixture full body completed late');
      return {method, route, request: body ?? null, status: response.status, raw, started, deadline, finished};
    } finally {clearTimeout(timer);}
  }, {port: backend.port, route, body, method});
  await exercise(window, workspace, evidence, api, true, `http://127.0.0.1:${backend.port}`);
});

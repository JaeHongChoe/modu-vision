import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import type {Page, Request} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api = (route: string, body?: unknown, method?: string) => Promise<{status: number; body: any}>;
type Pick = {kind: 'source' | 'inventory'; options: unknown; result: null};
type Producer = {read(): Promise<Pick[]>; restore(): Promise<void>};
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
test.use({actionTimeout: 15_000});

function tree(root: string): Record<string, {bytes: number; sha256: string}> {
  expect(fs.realpathSync(root)).toBe(root);
  const files: Record<string, {bytes: number; sha256: string}> = {};
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); const raw = fs.readFileSync(file); files[path.relative(root, file).split(path.sep).join('/')] = {bytes: raw.length, sha256: sha(raw)};}
    }
  };
  visit(root); return files;
}

function acceptFixtureResponse(route: string, method: string, response: {status: number; body: any}, datasetEmpty: boolean) {
  if (datasetEmpty && method === 'GET' && ['/api/team-data', '/api/team-data/readiness'].includes(route)) {
    expect(response.status).toBe(422); expect(response.body).toEqual({detail: 'Import a dataset into this project before creating a version.'});
  } else expect(response.status).toBe(200);
  return response.body;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, producer: Producer, native: boolean, url?: string) {
  const fixtureCalls: any[] = [], rendererWrites: any[] = []; let datasetEmpty = false;
  const ownedApi = async (route: string, body?: unknown, method?: string) => {
    const verb = method || (body ? 'POST' : 'GET');
    const response = await api(route, body, method), record = acceptFixtureResponse(route, verb, response, datasetEmpty);
    fixtureCalls.push({method: verb, route, ...(body ? {body} : {}), status: response.status, response: record});
    return record;
  };
  const pending = new Map<Request, {started: number; finished?: number; failed?: string}>();
  const observe = (request: Request) => {
    const endpoint = new URL(request.url()).pathname;
    if (!endpoint.startsWith('/api/')) return;
    if (request.method() === 'GET') pending.set(request, {started: performance.now()});
    else if (!['HEAD', 'OPTIONS'].includes(request.method())) {
      let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
      rendererWrites.push({method: request.method(), endpoint, body});
    }
  };
  const complete = (request: Request) => {const row = pending.get(request); if (row) row.finished = performance.now();};
  const failed = (request: Request) => {const row = pending.get(request); if (row) {row.finished = performance.now(); row.failed = request.failure()?.errorText || 'failed';}};
  page.on('request', observe); page.on('requestfinished', complete); page.on('requestfailed', failed);
  const settle = async () => {
    for (const [request, clock] of pending) {
      const deadline = clock.started + 15_000;
      const finish = async () => {const response = await request.response(); expect(response).not.toBeNull(); expect(await response!.finished()).toBeNull();};
      if (clock.finished === undefined) {
        const remaining = deadline - performance.now(); expect(remaining).toBeGreaterThan(0);
        let timer: ReturnType<typeof setTimeout> | undefined;
        try {await Promise.race([finish(), new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original GET did not complete within its original 15s request budget')), remaining);})]);}
        finally {if (timer) clearTimeout(timer);}
      }
      expect(clock.failed).toBeUndefined(); expect(clock.finished).toBeDefined(); expect(clock.finished!).toBeLessThanOrEqual(deadline);
    }
  };
  try {
    const project = await ownedApi('/api/project/create', {name: 'Owned runtime picker cancellation', task: 'classification'});
    expect(project.source_dataset_dir).toBeNull(); expect(fs.readdirSync(project.dataset_dir)).toEqual([]); datasetEmpty = true;
    const expectedRoot = native ? path.join(w.userData, 'projects') : w.projects;
    expect(path.dirname(project.project_dir)).toBe(expectedRoot); expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
    const inputs = path.join(project.project_dir, 'runtime-picker-only-inputs'); fs.mkdirSync(inputs);
    const payload = path.join(inputs, 'payload'); fs.mkdirSync(payload); fs.writeFileSync(path.join(payload, 'inert-input.txt'), 'Picker-only fixture; never installed or activated.\n');
    const inventory = path.join(inputs, 'inventory.json'); fs.writeFileSync(inventory, JSON.stringify({fixture: 'picker-input-preservation-only'}));
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
    await page.getByRole('navigation', {name: '배포 운영 화면'}).getByRole('button', {name: '설치·진단', exact: true}).click();
    const panel = page.getByRole('group', {name: '선택형 런타임 팩', exact: true});
    await expect(panel).toContainText('이 프로젝트에 보관한 런타임 팩이 없습니다.');
    const source = panel.getByLabel('런타임 팩 payload 폴더', {exact: true}), document = panel.getByLabel('런타임 팩 inventory 경로', {exact: true}), pin = panel.getByLabel('런타임 팩 검토 SHA-256', {exact: true});
    await source.fill(payload); await document.fill(inventory); await pin.fill('a'.repeat(64));
    await expect(panel.getByRole('button', {name: '팩 검증·보관', exact: true})).toBeEnabled();
    await settle();
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
      '/api/product-delivery/runtime-packs', '/api/product-delivery/installation', '/api/product-delivery/hardware', '/api/product-delivery/packages', '/api/runtime-services'];
    const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await ownedApi(endpoint);
    expect(apiBefore['/api/project/current'].id).toBe(project.id);
    expect(apiBefore['/api/product-delivery/runtime-packs'].packs).toEqual([]);
    expect(apiBefore['/api/runtime-services'].active).toBeNull(); expect(apiBefore['/api/runtime-services'].runtime.status).toBe('stopped');
    await settle();
    for (const image of w.images) {expect(sha(fs.readFileSync(image.path))).toBe(image.sha256); expect(fs.statSync(image.path).size).toBe(image.bytes);}
    const roots = {project: project.project_dir, harness_original_dataset: w.dataset, input_only_files: inputs};
    const before = Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, tree(root)]));
    const inputBefore = {source: payload, inventory, expected_sha256: 'a'.repeat(64)};
    const rendererSetup = [...rendererWrites]; rendererWrites.length = 0;
    // The source-less project has no import, install, training, inference or runtime activation setup.
    expect(rendererSetup).toEqual(native ? [{method: 'POST', endpoint: '/api/project/create', body: {name: 'Owned runtime picker cancellation', task: 'classification'}}] : []);
    expect(fixtureCalls.filter(row => row.method !== 'GET')).toEqual([{method: 'POST', route: '/api/project/create', body: {name: 'Owned runtime picker cancellation', task: 'classification'}, status: 200, response: project}]);
    const beforeRecord = {project, roots, before, apiBefore, inputBefore, harness_original_images: w.images, fixtureCalls: [...fixtureCalls], rendererSetup};
    const beforeFile = path.join(w.logs, 'runtime-picker-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(beforeRecord, null, 2), {flag: 'wx'}); e.addFile(beforeFile);
    for (const [kind, root] of Object.entries(roots)) for (const relative of Object.keys(before[kind])) {
      const snapshot = path.join(w.logs, 'runtime-picker-before', kind, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
    }
    const unchanged = async () => {
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      const apiAfter: Record<string, any> = {}; for (const [endpoint, value] of Object.entries(apiBefore)) {apiAfter[endpoint] = await ownedApi(endpoint); expect(apiAfter[endpoint]).toEqual(value);}
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      expect(rendererWrites).toEqual([]); await expect(source).toHaveValue(inputBefore.source); await expect(document).toHaveValue(inputBefore.inventory); await expect(pin).toHaveValue(inputBefore.expected_sha256);
      await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel.getByRole('button', {name: '팩 검증·보관', exact: true})).toBeEnabled();
      return apiAfter;
    };
    const controls: any[] = [];
    for (const [kind, name] of [['source', '팩 폴더 선택'], ['inventory', 'Inventory 파일 선택']] as const) {
      const picker = panel.getByRole('button', {name, exact: true}); await expect(picker).toBeEnabled(); await picker.click();
      await expect.poll(async () => (await producer.read()).length).toBe(controls.length + 1); await expect(picker).toBeEnabled();
      const row = (await producer.read())[controls.length];
      expect(row).toEqual({kind, options: kind === 'source' ? {title: '프로젝트 안의 런타임 팩 payload 폴더', defaultPath: project.project_dir} : {title: '런타임 팩 inventory JSON', filters: [{name: 'JSON', extensions: ['json']}]}, result: null});
      const apiAfter = await unchanged(); await panel.scrollIntoViewIfNeeded(); await e.screenshot(page, `runtime-pack-${native ? 'native-ipc' : 'browser-bridge'}-${kind}-null-cancellation`);
      controls.push({kind, producer: row, complete_api_after: apiAfter, no_runtime_pack_POST: true, post_baseline_renderer_writes: [...rendererWrites]});
    }
    const apiAfter = await unchanged(), after = Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, tree(root)]));
    const proof = {action: 'U033.reviewed-runtime-pack-install', dimension: 'cancel', parent: 'S6-03', scope: 'pre-install picker null result only', native_original_preload_IPC: native,
      controlled_dialog_result: native ? {canceled: true, filePaths: []} : null, actual_OS_dialog_user_cancellation_claim: false,
      in_flight_install_cancellation_claim: false, activation: false, signing_acceptance: false, models_or_training: false, beforeRecord, after, apiAfter, controls,
      complete_fixture_calls: fixtureCalls, complete_post_baseline_renderer_writes: rendererWrites};
    const proofFile = path.join(w.logs, 'runtime-picker-cancel-proof.json'); fs.writeFileSync(proofFile, JSON.stringify(proof, null, 2), {flag: 'wx'}); e.addFile(proofFile); e.note('runtime_pack_picker_cancel', proof);
  } finally {
    page.off('request', observe); page.off('requestfinished', complete); page.off('requestfailed', failed); await producer.restore();
  }
}

test('runtime pack source and inventory null choices preserve exact inputs before any POST', async ({page, renderer, workspace, evidence}) => {
  const picks: Pick[] = []; await installDesktopHostShim(page, renderer.port);
  await page.exposeFunction('__ownedRuntimeNullPick', async (kind: Pick['kind'], options: unknown) => {picks.push({kind, options, result: null}); return null;});
  await page.addInitScript(() => {const api = (window as any).api; api.selectFolder = (options: unknown) => (window as any).__ownedRuntimeNullPick('source', options); api.selectFile = (options: unknown) => (window as any).__ownedRuntimeNullPick('inventory', options);});
  const api: Api = async (route, body, method) => {const response = await page.request.fetch(renderer.origin + route, {method: method || (body ? 'POST' : 'GET'), data: body}); return {status: response.status(), body: await response.json()};};
  await exercise(page, workspace, evidence, api, {read: async () => [...picks], restore: async () => {}}, false, renderer.url);
});

test('native runtime pack original preload IPC null choices preserve exact inputs before any POST', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, status = await electronSession.waitForBackend();
  await electronSession.app.evaluate(({dialog}) => {
    const state = globalThis as any; if (state.__ownedRuntimeCancelDialog) throw Error('Owned picker producer already exists');
    const original = dialog.showOpenDialog;
    state.__ownedRuntimeCancelDialog = {original, calls: []};
    dialog.showOpenDialog = (async (...args: any[]) => {
      const options = args.length === 2 ? args[1] : args[0];
      const source = options.title === '프로젝트 안의 런타임 팩 payload 폴더', inventory = options.title === '런타임 팩 inventory JSON';
      if (!source && !inventory) throw Error('Foreign dialog refused by owned picker producer');
      const expected = source ? ['openDirectory', 'createDirectory'] : ['openFile'];
      if (JSON.stringify(options.properties) !== JSON.stringify(expected)) throw Error('Owned picker dialog properties changed');
      const {properties: _properties, ...requestOptions} = options;
      state.__ownedRuntimeCancelDialog.calls.push({kind: source ? 'source' : 'inventory', options: requestOptions, result: null});
      return {canceled: true, filePaths: []};
    }) as typeof dialog.showOpenDialog;
  });
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body ? 'POST' : 'GET'), headers: {'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
    return {status: response.status, body: await response.json()};
  }, {port: status.port, route, body, method});
  await exercise(page, workspace, evidence, api, {
    read: () => electronSession.app.evaluate(() => (globalThis as any).__ownedRuntimeCancelDialog.calls),
    restore: () => electronSession.app.evaluate(({dialog}) => {const state = globalThis as any; dialog.showOpenDialog = state.__ownedRuntimeCancelDialog.original; delete state.__ownedRuntimeCancelDialog;})
  }, true);
});

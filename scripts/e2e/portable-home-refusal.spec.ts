import fs from 'node:fs';
import path from 'node:path';
import Module from 'node:module';
import ts from 'typescript';
import {createHash, generateKeyPairSync} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import type {Page, Request} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness = require('./fixtures/harness.cjs');
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
test.use({actionTimeout: 15_000});

function loadPortableManager(name = 'portableUpdate.ts'): any {
  const file = path.join(harness.REPO_ROOT, 'src/main', name), m = new Module(file, module);
  m.filename = file; m.paths = (Module as any)._nodeModulePaths(path.dirname(file)); const original = m.require.bind(m);
  m.require = (key: string) => ['./releaseTrust', './persistentLaunch'].includes(key) ? loadPortableManager(key.slice(2) + '.ts') : original(key);
  (m as any)._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText, file);
  return m.exports;
}

function portableFixture(folder: string) {
  fs.mkdirSync(folder); const resources = path.join(folder, 'resources'), home = path.join(folder, 'current-user'), app = path.join(folder, 'Current.app'), valid = path.join(folder, 'separate-owned');
  for (const target of [resources, home, app, valid]) fs.mkdirSync(target);
  const child = path.join(home, 'child'); fs.mkdirSync(child); const alias = path.join(folder, 'linked-owned'); fs.symlinkSync(valid, alias, 'dir');
  for (const target of [home, child, app, valid]) fs.writeFileSync(path.join(target, 'retained-original.txt'), `Original owned input at ${path.basename(target)}.\n`);
  const backend = path.join(resources, 'backend_bin'); fs.mkdirSync(backend);
  const binary = Buffer.from('Inert fixture bytes, never executed.\n'); fs.writeFileSync(path.join(backend, 'vision_ai_backend'), binary);
  const platform = process.platform === 'darwin' ? 'Darwin' : 'Linux', architecture = process.arch === 'arm64' ? 'arm64' : 'x86_64';
  fs.writeFileSync(path.join(backend, 'backend-release.json'), JSON.stringify({executable: 'vision_ai_backend', executable_sha256: sha(binary), inventory: {build_identity_sha256: 'a'.repeat(64), platform, architecture}}));
  const publicKey = generateKeyPairSync('ed25519').publicKey.export({type: 'spki', format: 'der'}).toString('base64');
  const publisher = 'Portable boundary source fixture';
  const authority = path.join(resources, 'release-trust.json');
  fs.writeFileSync(authority, JSON.stringify({schema_version: 1, publisher, keys: {fixture: publicKey}, revoked_key_ids: [], allowed_origins: ['https://release.example.test'], compatibility: {api_context: 1, worker: 1, runtime: 1, dataset_index: 1}}));
  const state = {status: 'ready', installation_id: 'b'.repeat(32), version: '0.0.0', update_id: null, database_fence: 0, allowed_recovery: [], application_started: false};
  const commands: Array<{file: string; args: string[]}> = [], signatures: Array<string | null> = [], launches: any[] = [];
  const {PortableUpdateManager} = loadPortableManager();
  const manager = new PortableUpdateManager({packaged: true, platform: process.platform, arch: process.arch, resourcesPath: resources, userDataPath: home, appPath: app,
    // Controlled source-port output; neither real signing nor an installed native target is accepted.
    signature: async (target?: string) => {signatures.push(target || null); return {status: 'verified', publisher};},
    runner: async (file: string, args: string[]) => {
      commands.push({file, args: [...args]});
      if (file !== path.join(backend, 'vision_ai_backend') || JSON.stringify(args) !== JSON.stringify(['--offline-application-update', 'inspect', '--root', valid, '--authority', authority, '--pinned-authority-sha256', sha(fs.readFileSync(authority))])) throw Error('Foreign runner request refused by read-only fixture');
      return {stdout: JSON.stringify(state), stderr: ''};
    },
    launchRunner: async (...args: unknown[]) => {launches.push(args); throw Error('No portable application launch is authorized by this fixture');}});
  return {folder, resources, home, app, child, alias, valid, authority, state, manager, commands, signatures, launches};
}

function tree(root: string): Record<string, unknown> {
  expect(fs.realpathSync(root)).toBe(root); const entries: Record<string, unknown> = {};
  const walk = (directory: string) => {
    for (const name of fs.readdirSync(directory).sort()) {
      const file = path.join(directory, name), stat = fs.lstatSync(file), relative = path.relative(root, file).split(path.sep).join('/');
      // Links are inventoried by their original bytes/target and never followed.
      if (stat.isSymbolicLink()) entries[relative] = {kind: 'symlink', target: fs.readlinkSync(file)};
      else if (stat.isDirectory()) {entries[relative] = {kind: 'directory'}; walk(file);}
      else {expect(stat.isFile()).toBe(true); const bytes = fs.readFileSync(file); entries[relative] = {kind: 'file', bytes: bytes.length, sha256: sha(bytes)};}
    }
  };
  walk(root); return entries;
}

function acceptFixtureResponse(route: string, method: string, response: {status: number; body: any}, datasetEmpty: boolean) {
  if (datasetEmpty && method === 'GET' && ['/api/team-data', '/api/team-data/readiness'].includes(route)) {
    expect(response.status).toBe(422); expect(response.body).toEqual({detail: 'Import a dataset into this project before creating a version.'});
  } else expect(response.status).toBe(200);
  return response.body;
}

test('portable home actual choice refuses current parent child and linked scopes before the read-only runner', async ({page, renderer, workspace, evidence}) => {
  const fixture = portableFixture(path.join(workspace.root, 'portable-home-boundaries'));
  const fixtureCalls: any[] = [], rendererWrites: any[] = [], choices: any[] = [], unexpectedOperations: any[] = []; let datasetEmpty = false;
  const api = async (route: string, body?: unknown) => {
    const method = body ? 'POST' : 'GET', response = await page.request.fetch(renderer.origin + route, {method, data: body});
    const status = response.status(), record = acceptFixtureResponse(route, method, {status, body: await response.json()}, datasetEmpty); fixtureCalls.push({method, route, ...(body ? {body} : {}), status, response: record}); return record;
  };
  const observe = (request: Request) => {const endpoint = new URL(request.url()).pathname; if (endpoint.startsWith('/api/') && request.method() === 'GET') pending.set(request, {started: performance.now()}); if (endpoint.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {let body; try {body = request.postDataJSON();} catch {body = request.postData();} rendererWrites.push({method: request.method(), endpoint, body});}};
  const pending = new Map<Request, {started: number; finished?: number; failed?: string}>();
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

  let next: string | null = null;
  await page.exposeFunction('__ownedPortableBoundaryDispatch', async (operation: string, args: unknown[]) => {
    if (operation !== 'select' || args.length !== 0 || next === null) {unexpectedOperations.push({operation, args}); throw Error('Only an explicit owned directory selection is supported');}
    const chosen = next; next = null;
    try {const response = await fixture.manager.select(chosen); choices.push({chosen, outcome: 'accepted', response}); return response;}
    catch (error) {choices.push({chosen, outcome: 'refused', error: String((error as Error).message || error)}); throw error;}
  });
  await installDesktopHostShim(page, renderer.port);
  await page.addInitScript(() => {
    const invoke = (operation: string, args: unknown[] = []) => (window as any).__ownedPortableBoundaryDispatch(operation, args);
    Object.assign((window as any).api, {
      getDistributionStatus: async () => ({app_version: '0.1.0', platform: 'darwin', architecture: 'arm64', signature: {status: 'development', reason: 'Controlled source fixture, no native publisher acceptance', checked_at: 'controlled'}, update: {configured: false, configuration: null, status: 'not_configured', release: null, automatic_update_available: false}}),
      selectPortableUpdateHome: () => invoke('select'), inspectPortableUpdate: () => invoke('inspect'), previewPortableUpdate: (...args: unknown[]) => invoke('preview', args), applyPortableUpdate: (...args: unknown[]) => invoke('apply', args), recoverPortableUpdate: (...args: unknown[]) => invoke('recover', args), launchPortableUpdate: (...args: unknown[]) => invoke('launch', args), inspectPortableLaunch: (...args: unknown[]) => invoke('inspect-launch', args)
    });
  });
  try {
    const project = await api('/api/project/create', {name: 'Owned portable home boundaries', task: 'classification'});
    expect(project.source_dataset_dir).toBeNull(); expect(fs.readdirSync(project.dataset_dir)).toEqual([]); datasetEmpty = true;
    expect(path.dirname(project.project_dir)).toBe(workspace.projects); expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
    await page.goto(renderer.url); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
    await page.getByRole('navigation', {name: '배포 운영 화면'}).getByRole('button', {name: '설치·진단', exact: true}).click();
    const panel = page.getByRole('region', {name: '별도 portable 앱 업데이트', exact: true}), select = panel.getByRole('button', {name: 'portable 설치 폴더 선택', exact: true});
    await expect(select).toBeEnabled(); await expect(page.getByRole('group', {name: '선택형 런타임 팩', exact: true})).toContainText('이 프로젝트에 보관한 런타임 팩이 없습니다.');
    await settle();
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness', '/api/product-delivery/runtime-packs', '/api/product-delivery/installation', '/api/product-delivery/hardware', '/api/product-delivery/packages', '/api/runtime-services'];
    const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
    expect(apiBefore['/api/project/current'].id).toBe(project.id); expect(apiBefore['/api/runtime-services'].active).toBeNull(); expect(apiBefore['/api/runtime-services'].runtime.status).toBe('stopped');
    await settle(); for (const image of workspace.images) {expect(sha(fs.readFileSync(image.path))).toBe(image.sha256); expect(fs.statSync(image.path).size).toBe(image.bytes);}
    const roots = {project: project.project_dir, harness_original_dataset: workspace.dataset, owned_portable_inputs: fixture.folder};
    const before = Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, tree(root)]));
    expect(rendererWrites).toEqual([]); expect(fixture.commands).toEqual([]); expect(fixture.signatures).toEqual([]); expect(fixture.launches).toEqual([]);
    expect(fixtureCalls.filter(row => row.method !== 'GET')).toEqual([{method: 'POST', route: '/api/project/create', body: {name: 'Owned portable home boundaries', task: 'classification'}, status: 200, response: project}]);
    for (const [kind, entries] of Object.entries(before)) {
      const links = Object.entries(entries).filter(([, row]) => (row as any).kind === 'symlink');
      expect(links).toEqual(kind === 'owned_portable_inputs' ? [['linked-owned', {kind: 'symlink', target: fixture.valid}]] : []);
    }
    const beforeRecord = {project, roots, before, apiBefore, harness_original_images: workspace.images, fixtureCalls: [...fixtureCalls], original_link_inventory: [{path: fixture.alias, target: fixture.valid}]};
    const beforeFile = path.join(workspace.logs, 'portable-home-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(beforeRecord, null, 2), {flag: 'wx'}); evidence.addFile(beforeFile);
    for (const [kind, root] of Object.entries(roots)) for (const [relative, row] of Object.entries(before[kind])) if ((row as any).kind === 'file') {
      const snapshot = path.join(workspace.logs, 'portable-home-before', kind, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); evidence.addFile(snapshot);
    }
    const unchanged = async () => {
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      const apiAfter: Record<string, any> = {}; for (const [endpoint, value] of Object.entries(apiBefore)) {apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(value);}
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      expect(rendererWrites).toEqual([]); expect(unexpectedOperations).toEqual([]); expect(fixture.launches).toEqual([]); return apiAfter;
    };
    const refused: any[] = [];
    for (const [scope, chosen] of [['current', fixture.home], ['parent', fixture.folder], ['child', fixture.child], ['linked', fixture.alias]]) {
      next = chosen; await select.click(); await expect(panel.getByRole('alert')).toContainText(scope === 'linked' ? 'Portable update paths cannot follow links' : 'This selection overlaps the current application or its user home');
      await expect(select).toBeEnabled(); await expect(panel.getByText(/^선택한 설치:/)).toHaveCount(0); await expect(panel.getByRole('button', {name: 'portable 상태 다시 읽기', exact: true})).toBeDisabled();
      await expect(panel.getByRole('button', {name: 'portable 변경 내용 확인', exact: true})).toHaveCount(0); await expect(panel.getByRole('button', {name: '검토한 portable 업데이트 적용', exact: true})).toHaveCount(0);
      expect(fixture.commands).toEqual([]); expect(fixture.signatures).toEqual([]); const apiAfter = await unchanged(); await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, `portable-home-${scope}-refused-before-runner`);
      refused.push({scope, choice: choices[choices.length - 1], runner_calls: [...fixture.commands], signature_calls: [...fixture.signatures], complete_api_after: apiAfter});
    }
    expect(choices).toHaveLength(4); expect(choices.map(row => row.outcome)).toEqual(['refused', 'refused', 'refused', 'refused']);
    next = fixture.valid; await select.click(); await expect(panel.getByRole('alert')).toHaveCount(0); await expect(select).toBeEnabled();
    await expect(panel.getByText('선택한 설치: ' + fixture.valid, {exact: true})).toBeVisible(); await expect(panel).toContainText('업데이트 준비 확인됨 · 버전 0.0.0 · 데이터 세대 0');
    await expect(panel.getByRole('button', {name: 'portable 상태 다시 읽기', exact: true})).toBeEnabled(); await expect(panel.getByRole('button', {name: 'portable 변경 내용 확인', exact: true})).toBeDisabled();
    expect(choices).toHaveLength(5); expect(choices[4]).toEqual({chosen: fixture.valid, outcome: 'accepted', response: {...fixture.state, root: fixture.valid}});
    expect(fixture.commands).toEqual([{file: path.join(fixture.resources, 'backend_bin/vision_ai_backend'), args: ['--offline-application-update', 'inspect', '--root', fixture.valid, '--authority', fixture.authority, '--pinned-authority-sha256', sha(fs.readFileSync(fixture.authority))]}]);
    const apiAfter = await unchanged(), after = Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, tree(root)]));
    await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'portable-home-deliberate-separate-valid-source-readback');
    const proof = {action: 'U025.portable-select-owned-home', dimension: 'invalid', parent: 'S6-04', actual_renderer_select_button: true, real_source_manager: true,
      controlled_directory_choice: true, controlled_signature_and_read_only_runner: true, real_signature_or_native_installation_acceptance: false,
      recovery_after_invalid_is_separate_explicit_selection: true, no_inferred_previous_UI_authority: true, refused, choices, beforeRecord, apiAfter, after,
      complete_runner_commands: fixture.commands, complete_signature_calls: fixture.signatures, complete_launch_calls: fixture.launches, complete_fixture_api_calls: fixtureCalls, post_baseline_renderer_writes: rendererWrites,
      app_started: false, database_updated: false, pack_installed: false, models_or_training: false};
    const proofFile = path.join(workspace.logs, 'portable-home-refusal-proof.json'); fs.writeFileSync(proofFile, JSON.stringify(proof, null, 2), {flag: 'wx'}); evidence.addFile(proofFile); evidence.note('portable_home_refusal', proof);
  } finally {page.off('request', observe); page.off('requestfinished', complete); page.off('requestfailed', failed);}
});

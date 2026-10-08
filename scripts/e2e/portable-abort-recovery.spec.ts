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
  for (const target of [home, app, valid]) fs.writeFileSync(path.join(target, 'retained-original.txt'), `Original owned input at ${path.basename(target)}.\n`);
  const backend = path.join(resources, 'backend_bin'); fs.mkdirSync(backend);
  const binary = Buffer.from('Inert fixture bytes, never executed.\n'); fs.writeFileSync(path.join(backend, 'vision_ai_backend'), binary);
  const platform = process.platform === 'darwin' ? 'Darwin' : 'Linux', architecture = process.arch === 'arm64' ? 'arm64' : 'x86_64';
  fs.writeFileSync(path.join(backend, 'backend-release.json'), JSON.stringify({executable: 'vision_ai_backend', executable_sha256: sha(binary), inventory: {build_identity_sha256: 'a'.repeat(64), platform, architecture}}));
  const publicKey = generateKeyPairSync('ed25519').publicKey.export({type: 'spki', format: 'der'}).toString('base64'), publisher = 'Portable abort source fixture';
  const authority = path.join(resources, 'release-trust.json');
  fs.writeFileSync(authority, JSON.stringify({schema_version: 1, publisher, keys: {fixture: publicKey}, revoked_key_ids: [], allowed_origins: ['https://release.example.test'], compatibility: {api_context: 1, worker: 1, runtime: 1, dataset_index: 1}}));
  const state = {status: 'recovery_required', installation_id: 'c'.repeat(32), version: '1.0.0', update_id: 'd'.repeat(32), database_fence: 2, allowed_recovery: ['abort'], application_started: false, native_signature_acceptance: 'unqualified', model_quality_acceptance: 'required'};
  const stateFile = path.join(valid, 'controlled-pre-database-state.json'); fs.writeFileSync(stateFile, JSON.stringify(state, null, 2));
  const refusal = 'Owned abort fixture refused before any application or data change';
  const commands: Array<{file: string; args: string[]; response?: any; error?: string}> = [], signatures: Array<string | null> = [], launches: any[] = [];
  const {PortableUpdateManager} = loadPortableManager();
  const command = (kind: string, rest: string[] = []) => ['--offline-application-update', kind, '--root', valid, '--authority', authority, '--pinned-authority-sha256', sha(fs.readFileSync(authority)), ...rest];
  const manager = new PortableUpdateManager({packaged: true, platform: process.platform, arch: process.arch, resourcesPath: resources, userDataPath: home, appPath: app,
    // Controlled source-port output, neither real signing nor a portable transaction is accepted.
    signature: async (target?: string) => {signatures.push(target || null); return {status: 'verified', publisher};},
    runner: async (file: string, args: string[]) => {
      const row: (typeof commands)[number] = {file, args: [...args]}; commands.push(row);
      if (file !== path.join(backend, 'vision_ai_backend')) throw Error('Foreign inert runner binary refused');
      if (JSON.stringify(args) === JSON.stringify(command('inspect'))) {
        const response = JSON.parse(fs.readFileSync(stateFile, 'utf8')); expect(response).toEqual(state); row.response = response;
        return {stdout: JSON.stringify(response), stderr: ''};
      }
      if (JSON.stringify(args) === JSON.stringify(command('recover', ['--intent', state.update_id, '--action', 'abort']))) {
        row.error = refusal; const error = Object.assign(Error(refusal), {stdout: JSON.stringify({status: 'refused', error: refusal})}); throw error;
      }
      throw Error('Foreign command or recovery binding refused by inert source fixture');
    },
    launchRunner: async (...args: unknown[]) => {launches.push(args); throw Error('No portable application launch is authorized by this fixture');}});
  return {folder, resources, home, app, valid, authority, stateFile, state, refusal, manager, commands, signatures, launches, command};
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

test('portable abort failure retains the exact pre-database intent and explicit reopen restores its disabled recovery', async ({page, renderer, workspace, evidence}) => {
  const fixture = portableFixture(path.join(workspace.root, 'portable-abort-recovery'));
  const fixtureCalls: any[] = [], rendererWrites: any[] = [], bridgeCalls: any[] = [], unexpectedOperations: any[] = []; let datasetEmpty = false;
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

  await page.exposeFunction('__ownedPortableAbortDispatch', async (operation: string, args: unknown[]) => {
    const started = performance.now(), row: any = {operation, args, started, deadline: started + 15_000}; bridgeCalls.push(row);
    try {
      let response;
      if (operation === 'select' && args.length === 0) response = await fixture.manager.select(fixture.valid);
      else if (operation === 'recover' && args.length === 2 && args[0] === 'abort' && JSON.stringify(args[1]) === JSON.stringify({installation_id: fixture.state.installation_id, update_id: fixture.state.update_id})) response = await fixture.manager.recover(args[0], args[1]);
      else {unexpectedOperations.push({operation, args}); throw Error('Foreign bridge operation or abort identity refused');}
      row.response = response; return response;
    } catch (error) {row.error = String((error as Error).message || error); throw error;}
    finally {row.finished = performance.now(); expect(row.finished).toBeLessThanOrEqual(row.deadline);}
  });
  await installDesktopHostShim(page, renderer.port);
  await page.addInitScript(() => {
    const invoke = (operation: string, args: unknown[] = []) => (window as any).__ownedPortableAbortDispatch(operation, args);
    Object.assign((window as any).api, {
      getDistributionStatus: async () => ({app_version: '0.1.0', platform: 'darwin', architecture: 'arm64', signature: {status: 'development', reason: 'Controlled abort source fixture, no native publisher acceptance', checked_at: 'controlled'}, update: {configured: false, configuration: null, status: 'not_configured', release: null, automatic_update_available: false}}),
      selectPortableUpdateHome: () => invoke('select'), inspectPortableUpdate: () => invoke('inspect'), previewPortableUpdate: (...args: unknown[]) => invoke('preview', args), applyPortableUpdate: (...args: unknown[]) => invoke('apply', args), recoverPortableUpdate: (...args: unknown[]) => invoke('recover', args), launchPortableUpdate: (...args: unknown[]) => invoke('launch', args), inspectPortableLaunch: (...args: unknown[]) => invoke('inspect-launch', args)
    });
  });
  try {
    const project = await api('/api/project/create', {name: 'Owned portable abort recovery', task: 'classification'});
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
    expect(fixtureCalls.filter(row => row.method !== 'GET')).toEqual([{method: 'POST', route: '/api/project/create', body: {name: 'Owned portable abort recovery', task: 'classification'}, status: 200, response: project}]);
    for (const entries of Object.values(before)) expect(Object.values(entries).filter(row => (row as any).kind === 'symlink')).toEqual([]);
    const beforeRecord = {project, roots, before, apiBefore, harness_original_images: workspace.images, fixtureCalls: [...fixtureCalls]};
    const beforeFile = path.join(workspace.logs, 'portable-abort-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(beforeRecord, null, 2), {flag: 'wx'}); evidence.addFile(beforeFile);
    for (const [kind, root] of Object.entries(roots)) for (const [relative, row] of Object.entries(before[kind])) if ((row as any).kind === 'file') {
      const snapshot = path.join(workspace.logs, 'portable-abort-before', kind, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); evidence.addFile(snapshot);
    }
    const unchanged = async () => {
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      const apiAfter: Record<string, any> = {}; for (const [endpoint, value] of Object.entries(apiBefore)) {apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(value);}
      await settle(); for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);
      expect(rendererWrites).toEqual([]); expect(unexpectedOperations).toEqual([]); expect(fixture.launches).toEqual([]); return apiAfter;
    };
    const abort = panel.getByRole('button', {name: '데이터 전환 전 설치 취소', exact: true}), confirm = panel.getByRole('checkbox', {name: '선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다', exact: true});
    const expectedState = {...fixture.state, root: fixture.valid};
    const commandRow = (kind: string, rest: string[] = []) => ({file: path.join(fixture.resources, 'backend_bin/vision_ai_backend'), args: fixture.command(kind, rest)});
    await select.click(); await expect(panel.getByText('선택한 설치: ' + fixture.valid, {exact: true})).toBeVisible();
    await expect(panel).toContainText('중단된 업데이트 · 복구 필요 · 버전 1.0.0 · 데이터 세대 2');
    await expect(abort).toBeDisabled(); await expect(confirm).not.toBeChecked();
    expect(bridgeCalls).toHaveLength(1); expect(bridgeCalls[0].response).toEqual(expectedState);
    expect(fixture.commands).toEqual([{...commandRow('inspect'), response: fixture.state}]);
    await confirm.check(); await expect(abort).toBeEnabled(); await abort.click();
    await expect(panel.getByRole('alert')).toContainText(fixture.refusal + ' 선택한 설치의 상태를 다시 읽은 뒤 진행하세요.');
    await expect(select).toBeEnabled(); await expect(abort).toBeDisabled(); await expect(confirm).not.toBeChecked();
    await expect(panel).toContainText('중단된 업데이트 · 복구 필요 · 버전 1.0.0 · 데이터 세대 2');
    expect(bridgeCalls).toHaveLength(2); expect(bridgeCalls[1].operation).toBe('recover'); expect(bridgeCalls[1].args).toEqual(['abort', {installation_id: fixture.state.installation_id, update_id: fixture.state.update_id}]); expect(bridgeCalls[1].error).toBe(fixture.refusal);
    expect(fixture.commands).toEqual([{...commandRow('inspect'), response: fixture.state}, {...commandRow('inspect'), response: fixture.state}, {...commandRow('recover', ['--intent', fixture.state.update_id, '--action', 'abort']), error: fixture.refusal}]);
    const errorAPI = await unchanged(); await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'portable-abort-error-original-intent-retained');
    const errorRecord = {complete_api_after: errorAPI, bridge_calls: [...bridgeCalls], runner_calls: [...fixture.commands], complete_stored_pre_database_state: JSON.parse(fs.readFileSync(fixture.stateFile, 'utf8'))};
    await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
    await page.getByRole('navigation', {name: '배포 운영 화면'}).getByRole('button', {name: '설치·진단', exact: true}).click();
    await expect(select).toBeEnabled(); await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel.getByText(/^선택한 설치:/)).toHaveCount(0);
    await expect(panel.getByRole('button', {name: 'portable 상태 다시 읽기', exact: true})).toBeDisabled(); await expect(abort).toHaveCount(0); expect(bridgeCalls).toHaveLength(2);
    const beforeReselectAPI = await unchanged();
    await select.click(); await expect(panel.getByText('선택한 설치: ' + fixture.valid, {exact: true})).toBeVisible();
    await expect(panel).toContainText('중단된 업데이트 · 복구 필요 · 버전 1.0.0 · 데이터 세대 2');
    await expect(abort).toBeDisabled(); await expect(confirm).not.toBeChecked(); await expect(panel.getByRole('alert')).toHaveCount(0);
    expect(bridgeCalls).toHaveLength(3); expect(bridgeCalls[2].operation).toBe('select'); expect(bridgeCalls[2].response).toEqual(expectedState);
    expect(fixture.commands).toEqual([{...commandRow('inspect'), response: fixture.state}, {...commandRow('inspect'), response: fixture.state}, {...commandRow('recover', ['--intent', fixture.state.update_id, '--action', 'abort']), error: fixture.refusal}, {...commandRow('inspect'), response: fixture.state}]);
    expect(fixture.signatures).toEqual(Array.from({length: 4}, () => [null, path.join(fixture.resources, 'backend_bin/vision_ai_backend')]).flat());
    for (const row of bridgeCalls) {expect(row.deadline).toBe(row.started + 15_000); expect(row.finished).toBeLessThanOrEqual(row.deadline);}
    const apiAfter = await unchanged(), after = Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, tree(root)]));
    await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'portable-abort-explicit-same-intent-reopen');
    const proof = {action: 'U025.portable-abort-before-db', dimensions: ['error', 'reopen'], parent: 'S6-04', actual_renderer_abort_and_reload: true, real_source_manager: true,
      controlled_directory_choice_signature_and_inert_runner: true, controlled_pre_database_fixture_not_actual_transaction: true,
      errorRecord, beforeReselectAPI, complete_restored_state: bridgeCalls[2].response, beforeRecord, apiAfter, after,
      complete_runner_commands: fixture.commands, complete_signature_calls: fixture.signatures, complete_launch_calls: fixture.launches, complete_bridge_calls: bridgeCalls, complete_fixture_api_calls: fixtureCalls, post_baseline_renderer_writes: rendererWrites,
      confirmed_checkbox_is_control_only_not_human_or_quality_approval: true, app_started: false, database_updated: false, successful_abort_or_canary_repeated: false, models_or_training: false};
    const proofFile = path.join(workspace.logs, 'portable-abort-recovery-proof.json'); fs.writeFileSync(proofFile, JSON.stringify(proof, null, 2), {flag: 'wx'}); evidence.addFile(proofFile); evidence.note('portable_abort_recovery', proof);
  } finally {page.off('request', observe); page.off('requestfinished', complete); page.off('requestfailed', failed);}
});

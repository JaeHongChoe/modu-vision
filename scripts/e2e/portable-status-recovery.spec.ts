import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import Module from 'node:module';
import ts from 'typescript';
import type {Request} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness = require('./fixtures/harness.cjs');
const sha = (raw: Buffer | string) => crypto.createHash('sha256').update(raw).digest('hex');
const failure = 'Controlled original portable inspect response failure';
const confirmation = '선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다';
test.use({actionTimeout: 10_000});

// Exact original main/trust modules; all subprocess entry points are injected
// below. These public trust bytes and signature output are controlled fixtures.
function load(name = 'portableUpdate.ts'): any {
  const file = path.join(harness.REPO_ROOT, 'src/main', name), m = new Module(file, module);
  m.filename = file; m.paths = (Module as any)._nodeModulePaths(path.dirname(file));
  const original = m.require.bind(m);
  m.require = (key: string) => ['./releaseTrust', './persistentLaunch'].includes(key) ? load(key.slice(2) + '.ts') : original(key);
  (m as any)._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true}}).outputText, file);
  return m.exports;
}

function tree(root: string) {
  expect(path.isAbsolute(root)).toBe(true); expect(fs.lstatSync(root).isDirectory()).toBe(true);
  expect(fs.lstatSync(root).isSymbolicLink()).toBe(false);
  const files: Record<string, string> = {}, directories: string[] = [];
  const visit = (folder: string) => {
    for (const item of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, item.name), relative = path.relative(root, file).split(path.sep).join('/');
      expect(item.isSymbolicLink()).toBe(false);
      if (item.isDirectory()) {directories.push(relative); visit(file);}
      else {expect(item.isFile()).toBe(true); files[relative] = sha(fs.readFileSync(file));}
    }
  };
  visit(root); return {files, directories: directories.sort()};
}

function sameState(actual: unknown, original: unknown) {expect(actual).toEqual(original);}

function controlledManager(folder: string, userData: string) {
  const root = path.join(folder, 'selected-home'), resources = path.join(folder, 'resources');
  fs.mkdirSync(path.join(root, 'projects'), {recursive: true}); fs.mkdirSync(path.join(resources, 'backend_bin'), {recursive: true});
  const state = {status: 'committed', version: '1.0.0', installation_id: '1'.repeat(32), update_id: '2'.repeat(32),
    database_fence: 3, allowed_recovery: ['finish', 'forward'], application_started: false};
  const stateFile = path.join(root, 'controlled-inspection-state.json');
  fs.writeFileSync(stateFile, JSON.stringify(state));
  fs.writeFileSync(path.join(root, 'projects/labels.json'), JSON.stringify({original_label: 'preserve this exact controlled write'}));
  fs.writeFileSync(path.join(root, 'database-state.json'), JSON.stringify({fence: 3, original_user: 'controlled-owned-user'}));
  const binary = Buffer.from('controlled manager executable binding; never spawned');
  fs.writeFileSync(path.join(resources, 'backend_bin/vision_ai_backend'), binary);
  const keys = crypto.generateKeyPairSync('ed25519');
  fs.writeFileSync(path.join(resources, 'release-trust.json'), JSON.stringify({schema_version: 1, publisher: 'Controlled UI fixture',
    keys: {fixture: keys.publicKey.export({type: 'spki', format: 'der'}).toString('base64')}, revoked_key_ids: [],
    allowed_origins: ['https://release.example.test'], compatibility: {api_context: 1, worker: 1, runtime: 1, dataset_index: 1}}));
  // No controller protocol means inspect never opens a launch controller.
  fs.writeFileSync(path.join(resources, 'backend_bin/backend-release.json'), JSON.stringify({executable: 'vision_ai_backend',
    executable_sha256: sha(binary), inventory: {build_identity_sha256: 'a'.repeat(64),
      platform: process.platform === 'darwin' ? 'Darwin' : 'Linux', architecture: process.arch === 'arm64' ? 'arm64' : 'x86_64'}}));
  const authority = path.join(resources, 'release-trust.json'), authoritySHA = sha(fs.readFileSync(authority));
  const commands: Array<{command: string; argv: string[]; binary_sha256: string; outcome: string; body: unknown}> = [];
  let failNext = false;
  const manager = new (load().PortableUpdateManager)({packaged: true, platform: process.platform, arch: process.arch,
    resourcesPath: resources, userDataPath: userData, appPath: path.join(folder, 'Current.app'),
    signature: async () => ({status: 'verified', publisher: 'Controlled UI fixture'}),
    runner: async (file: string, argv: string[]) => {
      expect(file).toBe(path.join(resources, 'backend_bin/vision_ai_backend')); expect(sha(fs.readFileSync(file))).toBe(sha(binary));
      expect(argv).toEqual(['--offline-application-update', 'inspect', '--root', root,
        '--authority', authority, '--pinned-authority-sha256', authoritySHA]);
      const body = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      const row = {command: argv[1], argv: [...argv], binary_sha256: sha(binary), outcome: failNext ? 'controlled_error' : 'readback', body};
      commands.push(row);
      if (failNext) {failNext = false; throw Error(failure);}
      return {stdout: JSON.stringify(body), stderr: ''};
    },
    launchRunner: async () => {throw Error('A portable launch is outside the inspect-only fixture');}});
  return {manager, root, resources, stateFile, state: {...state, root}, commands, failOnce: () => {expect(failNext).toBe(false); failNext = true;}};
}

test('portable inspect error requires explicit same-identity retry and utility handoff preserves original state', async ({page, renderer, workspace, evidence}) => {
  const folder = path.join(workspace.root, 'portable-status-controls'); fs.mkdirSync(folder);
  const controlled = controlledManager(folder, workspace.userData);
  const hostCalls: string[] = [], writes: unknown[] = [], readFailures: string[] = [];
  const pending = new Set<Request>();
  let ownedEmptyProject: any = null;
  const requestStart = (request: Request) => {
    const address = new URL(request.url()); if (!address.pathname.startsWith('/api/')) return;
    if (request.method() === 'GET') pending.add(request);
    else if (!['HEAD', 'OPTIONS'].includes(request.method())) writes.push({method: request.method(), path: address.pathname, body: request.postData()});
  };
  const requestFinished = (request: Request) => {pending.delete(request);};
  const requestFailed = (request: Request) => {if (pending.delete(request)) readFailures.push(request.url() + ': ' + request.failure()?.errorText);};
  page.on('request', requestStart); page.on('requestfinished', requestFinished); page.on('requestfailed', requestFailed);
  const settle = async () => {await expect.poll(() => pending.size, {timeout: 10_000}).toBe(0); expect(readFailures).toEqual([]);};
  const api = async (route: string) => {
    const start = Date.now(), response = await page.request.get(renderer.origin + route, {timeout: 10_000});
    const remaining = 10_000 - (Date.now() - start); expect(remaining).toBeGreaterThan(0);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const body = await Promise.race([response.json(), new Promise((_, reject) => {timer = setTimeout(() => reject(Error('Original GET body deadline expired')), remaining);})]);
      const emptySourceRoutes = ['/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics'];
      if (emptySourceRoutes.includes(route)) {
        expect(ownedEmptyProject).not.toBeNull(); expect(ownedEmptyProject.source_dataset_dir).toBeNull();
        expect(response.status()).toBe(422);
        expect(body).toEqual({detail: 'Import a dataset into this project before creating a version.'});
        return {method: 'GET', route, status: response.status(), body};
      }
      expect(response.status()).toBe(200); return body;
    }
    finally {if (timer) clearTimeout(timer); expect(Date.now() - start).toBeLessThan(10_000);}
  };
  await page.exposeFunction('__portableStatusDispatch', async (operation: string) => {
    hostCalls.push(operation);
    if (operation === 'select') return controlled.manager.select(controlled.root);
    if (operation === 'inspect') return controlled.manager.inspect();
    throw Error('Only explicit select/inspect are admitted by this fixture');
  });
  await installDesktopHostShim(page, renderer.port);
  await page.addInitScript(() => {
    const invoke = (operation: string) => (window as any).__portableStatusDispatch(operation);
    const refused = async () => {throw Error('Portable mutation or launch is outside this fixture');};
    Object.assign((window as any).api, {
      getDistributionStatus: async () => ({app_version: '0.1.0', platform: 'darwin', architecture: 'arm64',
        signature: {status: 'development', reason: 'Controlled browser output; no native publisher acceptance', checked_at: 'controlled'},
        update: {configured: false, configuration: null, status: 'not_configured', release: null, automatic_update_available: false}}),
      selectPortableUpdateHome: () => invoke('select'), inspectPortableUpdate: () => invoke('inspect'),
      previewPortableUpdate: refused, applyPortableUpdate: refused, recoverPortableUpdate: refused,
      launchPortableUpdate: refused, inspectPortableLaunch: refused});
  });
  try {
    await page.goto(renderer.url); await settle();
    const created = await page.request.post(renderer.origin + '/api/project/create', {data: {name: 'Owned portable inspect lifecycle', task: 'classification'}, timeout: 10_000});
    expect(created.status()).toBe(200); const createdProject = await created.json(); await settle();
    await page.reload();
    const open = async () => {
      await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
      await page.getByRole('navigation', {name: '배포 운영 화면', exact: true}).getByRole('button', {name: '설치·진단', exact: true}).click();
      await expect(page.getByText('이 프로젝트에 보관한 런타임 팩이 없습니다.', {exact: true})).toBeVisible(); await settle();
    };
    await open();
    const panel = page.getByRole('region', {name: '별도 portable 앱 업데이트', exact: true});
    const select = panel.getByRole('button', {name: 'portable 설치 폴더 선택', exact: true});
    const inspect = panel.getByRole('button', {name: 'portable 상태 다시 읽기', exact: true});
    const confirm = panel.getByLabel(confirmation, {exact: true});
    await select.click(); await expect(inspect).toBeEnabled(); await expect(panel).toContainText('선택한 설치: ' + controlled.root);
    await expect(panel).toContainText('앱·데이터 전환 확인됨 · 버전 1.0.0 · 데이터 세대 3');
    expect(hostCalls).toEqual(['select']); expect(controlled.commands).toHaveLength(1);
    const project = await api('/api/project/current');
    expect(project).toMatchObject({id: createdProject.id, project_dir: createdProject.project_dir, name: createdProject.name, task: 'classification'});
    expect(project.source_dataset_dir).toBeNull(); ownedEmptyProject = project;
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
      '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/versions', '/api/product-delivery/packages',
      '/api/product-delivery/hardware', '/api/product-delivery/installation', '/api/product-delivery/runtime-packs',
      '/api/runtime-services', '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', '/api/flow-evaluations/approvals/active'];
    const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
    expect(apiBefore['/api/dataset/versions']).toEqual({versions: []});
    expect(apiBefore['/api/product-delivery/packages'].packages).toEqual([]); expect(apiBefore['/api/product-delivery/runtime-packs'].packs).toEqual([]);
    expect(apiBefore['/api/fleet/targets'].targets).toEqual([]); expect(apiBefore['/api/fleet/rollouts'].rollouts).toEqual([]);
    expect(apiBefore['/api/runtime-services'].runtime.status).toBe('stopped'); expect(apiBefore['/api/runtime-services'].active).toBeNull();
    expect(apiBefore['/api/runtime-services'].history).toEqual([]); expect(apiBefore['/api/flow-evaluations/approvals/active']).toBeNull();
    await settle();
    const roots = {project: project.project_dir as string, annotations: project.annotations_dir as string,
      originalHarnessDataset: workspace.dataset, selectedPortable: controlled.root, trustedResources: controlled.resources};
    const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
    for (const image of workspace.images) expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
    expect(Object.keys(before.originalHarnessDataset.files).sort()).toEqual(workspace.images.map(image => path.relative(workspace.dataset, image.path).split(path.sep).join('/')).sort());
    const baseline = {project, createdProject, roots, before, apiBefore, portableState: controlled.state, originalHarnessImages: workspace.images};
    const baselineFile = path.join(workspace.logs, 'portable-status-before.json'); fs.writeFileSync(baselineFile, JSON.stringify(baseline, null, 2)); evidence.addFile(baselineFile);
    for (const [kind, root] of Object.entries(roots)) for (const relative of Object.keys(before[kind].files)) {
      const snapshot = path.join(workspace.logs, 'portable-status-before', kind, relative); fs.mkdirSync(path.dirname(snapshot), {recursive: true});
      fs.copyFileSync(path.join(root, relative), snapshot); evidence.addFile(snapshot);
    }
    // Setup project creation is recorded separately; every subsequent API write is forbidden.
    const setupWrites = [{method: 'POST', path: '/api/project/create', body: {name: 'Owned portable inspect lifecycle', task: 'classification'}, status: created.status(), response: createdProject}, ...writes]; writes.length = 0;
    const checkFiles = () => {for (const [key, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[key]);};
    const unchanged = async () => {
      await settle(); checkFiles(); const apiAfter: Record<string, any> = {};
      for (const [endpoint, record] of Object.entries(apiBefore)) {apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(record);}
      await settle(); checkFiles(); expect(writes).toEqual([]); return apiAfter;
    };
    await confirm.check(); await expect(panel.getByRole('button', {name: '같은 업데이트 마무리', exact: true})).toBeEnabled();
    controlled.failOnce(); await inspect.click();
    await expect(panel.getByRole('alert')).toHaveText(failure + ' 선택한 설치의 상태를 다시 읽은 뒤 진행하세요.');
    await expect(inspect).toBeEnabled(); await expect(confirm).toBeChecked();
    await expect(panel).toContainText('선택한 설치: ' + controlled.root);
    await expect(panel).toContainText('앱·데이터 전환 확인됨 · 버전 1.0.0 · 데이터 세대 3');
    expect(hostCalls).toEqual(['select', 'inspect']); expect(controlled.commands).toHaveLength(2);
    expect(controlled.commands[1].outcome).toBe('controlled_error'); await unchanged();
    expect(hostCalls).toEqual(['select', 'inspect']); expect(controlled.commands).toHaveLength(2);
    await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'portable-inspect-error-keeps-original-selection');
    await inspect.click(); await expect(panel.getByRole('alert')).toHaveCount(0); await expect(confirm).not.toBeChecked();
    expect(hostCalls).toEqual(['select', 'inspect', 'inspect']); expect(controlled.commands).toHaveLength(3);
    sameState({...controlled.commands[2].body as object, root: controlled.root}, controlled.state); await unchanged();
    await evidence.screenshot(page, 'portable-explicit-inspect-retry-same-identity');
    const dialog = page.getByRole('dialog', {name: '패키지·장치·설치·진단', exact: true});
    await dialog.getByRole('button', {name: '패키지·장치·설치·진단 닫기', exact: true}).click();
    await expect(panel).toHaveCount(0); await unchanged();
    await open(); await expect(panel.getByText('선택한 설치:', {exact: false})).toHaveCount(0); await expect(inspect).toBeDisabled();
    await select.click(); await expect(inspect).toBeEnabled(); await expect(panel).toContainText('선택한 설치: ' + controlled.root);
    await inspect.click(); await expect(inspect).toBeEnabled(); await expect(panel.getByRole('alert')).toHaveCount(0);
    await expect(panel).toContainText('앱·데이터 전환 확인됨 · 버전 1.0.0 · 데이터 세대 3'); await expect(confirm).not.toBeChecked();
    expect(hostCalls).toEqual(['select', 'inspect', 'inspect', 'select', 'inspect']); expect(controlled.commands).toHaveLength(5);
    expect(controlled.commands.map(row => row.command)).toEqual(Array(5).fill('inspect'));
    expect(controlled.commands.map(row => row.outcome)).toEqual(['readback', 'controlled_error', 'readback', 'readback', 'readback']);
    for (const command of controlled.commands) sameState({...command.body as object, root: controlled.root}, controlled.state);
    const apiAfter = await unchanged(); await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'portable-utility-handoff-explicit-same-home-read');
    const after = {trees: Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)])), apiAfter, writes: [...writes]};
    const afterFile = path.join(workspace.logs, 'portable-status-after.json'); fs.writeFileSync(afterFile, JSON.stringify(after, null, 2)); evidence.addFile(afterFile);
    evidence.note('portable_status_recovery', {requirements: ['S6-04'], cells: [
      {action: 'U025.portable-inspect-state', dimension: 'error', exact_controlled_inspect_errors: 1, automatic_retry: false,
        failed_original_selection_and_confirmation_preserved: true, deliberate_retry_same_complete_state: true},
      {action: 'U025.portable-inspect-state', dimension: 'handoff', actual_utility_close_and_remount: true,
        volatile_selection_not_adopted: true, explicit_same_owned_home_selected: true, same_complete_original_state: true}],
      baseline, after, setupWrites, hostCalls, original_main_manager_commands: controlled.commands,
      all_post_baseline_API_mutations: writes, actual_original_main_manager_and_trust_readers: true,
      runner: 'controlled inspect-only response; never spawns the inert executable', signature: 'controlled output; no native publisher acceptance',
      stale_handler_negative_scope: 'separate source-only original TSX handler control; not an asserted browser interaction',
      native_electron: false, actual_install_recover_or_launch: false, actual_source_CPU_or_model: false,
      Windows_or_frozen_package_or_whole_parent_acceptance: false, release_ready: false});
  } finally {page.off('request', requestStart); page.off('requestfinished', requestFinished); page.off('requestfailed', requestFailed);}
});

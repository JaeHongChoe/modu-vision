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
const confirmation = '선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다';
test.use({actionTimeout: 10_000});

// Same production main/trust module loader as portable-status-recovery.spec.ts.
// Only the CLI response and native-signature outputs below are controlled.
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

function controlledManager(folder: string, userData: string, recovery: 'finish' | 'forward' | null) {
  const root = path.join(folder, 'selected-home'), resources = path.join(folder, 'resources');
  fs.mkdirSync(path.join(root, 'projects'), {recursive: true}); fs.mkdirSync(path.join(resources, 'backend_bin'), {recursive: true});
  const state = {status: 'committed', version: '1.0.0', installation_id: '1'.repeat(32), update_id: '2'.repeat(32),
    database_fence: 3, allowed_recovery: recovery ? [recovery] : ['finish', 'forward'], application_started: false};
  const stateFile = path.join(root, 'controlled-inspection-state.json');
  fs.writeFileSync(stateFile, JSON.stringify(state));
  fs.writeFileSync(path.join(root, 'projects/labels.json'), JSON.stringify({original_label: 'preserve this exact controlled write'}));
  fs.writeFileSync(path.join(root, 'database-state.json'), JSON.stringify({fence: 3, original_user: 'controlled-owned-user'}));
  const binary = Buffer.from('controlled manager executable binding; never spawned');
  const binaryPath = path.join(resources, 'backend_bin/vision_ai_backend'); fs.writeFileSync(binaryPath, binary);
  const keys = crypto.generateKeyPairSync('ed25519');
  fs.writeFileSync(path.join(resources, 'release-trust.json'), JSON.stringify({schema_version: 1, publisher: 'Controlled UI fixture',
    keys: {fixture: keys.publicKey.export({type: 'spki', format: 'der'}).toString('base64')}, revoked_key_ids: [],
    allowed_origins: ['https://release.example.test'], compatibility: {api_context: 1, worker: 1, runtime: 1, dataset_index: 1}}));
  // No controller protocol: the real manager never opens a launch controller.
  fs.writeFileSync(path.join(resources, 'backend_bin/backend-release.json'), JSON.stringify({executable: 'vision_ai_backend',
    executable_sha256: sha(binary), inventory: {build_identity_sha256: 'a'.repeat(64),
      platform: process.platform === 'darwin' ? 'Darwin' : 'Linux', architecture: process.arch === 'arm64' ? 'arm64' : 'x86_64'}}));
  const authority = path.join(resources, 'release-trust.json'), authoritySHA = sha(fs.readFileSync(authority));
  const commands: Array<{command: string; argv: string[]; body: unknown; controlled: true}> = [];
  let gate: {command: 'inspect' | 'recover'; started: boolean; promise: Promise<void>; release: () => void} | null = null;
  const manager = new (load().PortableUpdateManager)({packaged: true, platform: process.platform, arch: process.arch,
    resourcesPath: resources, userDataPath: userData, appPath: path.join(folder, 'Current.app'),
    signature: async () => ({status: 'verified', publisher: 'Controlled UI fixture'}),
    runner: async (file: string, argv: string[]) => {
      expect(file).toBe(binaryPath); expect(sha(fs.readFileSync(file))).toBe(sha(binary));
      const command = argv[1]; expect(['inspect', 'recover']).toContain(command);
      const expected = ['--offline-application-update', command, '--root', root,
        '--authority', authority, '--pinned-authority-sha256', authoritySHA];
      if (command === 'recover') {
        expect(recovery).not.toBeNull(); expected.push('--intent', state.update_id, '--action', recovery!);
      }
      expect(argv).toEqual(expected);
      const body = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      commands.push({command, argv: [...argv], body, controlled: true});
      if (gate && !gate.started && gate.command === command) {gate.started = true; await gate.promise;}
      // Handoff/UI evidence only: no actual recovery transaction or new fence is asserted.
      return {stdout: JSON.stringify(body), stderr: ''};
    }, launchRunner: async () => {throw Error('A portable launch is outside these GUI handoff fixtures');}});
  return {manager, root, resources, state: {...state, root}, commands,
    arm(command: 'inspect' | 'recover') {
      expect(gate).toBeNull(); let release!: () => void;
      const promise = new Promise<void>(resolve => {release = resolve;}); gate = {command, started: false, promise, release};
    }, started: () => Boolean(gate?.started), release: () => {gate?.release();}};
}

const cases = [
  {name: 'portable inspect state requires explicit same-home read after renderer reopen', action: 'portable-inspect-state', dimension: 'reopen', mode: 'reopen', recovery: null},
  {name: 'owned portable home selection handoff never adopts a retired response', action: 'select-owned-home', dimension: 'handoff', mode: 'select', recovery: null},
  {name: 'portable finish handoff preserves one original intent without replay', action: 'recover-finish', dimension: 'handoff', mode: 'recover', recovery: 'finish'},
  {name: 'portable forward handoff preserves one original intent without replay', action: 'recover-forward', dimension: 'handoff', mode: 'recover', recovery: 'forward'},
] as const;

for (const scenario of cases) test(scenario.name, async ({page, renderer, workspace, evidence}) => {
  const folder = path.join(workspace.root, 'portable-pending-ui'); fs.mkdirSync(folder);
  const controlled = controlledManager(folder, workspace.userData, scenario.recovery);
  const hostCalls: Array<{operation: string; args: unknown[]}> = [], writes: unknown[] = [], readFailures: string[] = [];
  const pending = new Set<Request>(), tasks: Promise<unknown>[] = [];
  let completed = 0, primary: unknown = null, ownedEmptyProject: any = null;
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
      if (['/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics'].includes(route)) {
        expect(ownedEmptyProject?.source_dataset_dir).toBeNull(); expect(response.status()).toBe(422);
        expect(body).toEqual({detail: 'Import a dataset into this project before creating a version.'});
        return {method: 'GET', route, status: response.status(), body};
      }
      expect(response.status()).toBe(200); return body;
    } finally {if (timer) clearTimeout(timer); expect(Date.now() - start).toBeLessThan(10_000);}
  };
  await page.exposeFunction('__portablePendingDispatch', async (operation: string, args: any[]) => {
    hostCalls.push({operation, args});
    let task: Promise<unknown>;
    if (operation === 'select') task = controlled.manager.select(controlled.root);
    else if (operation === 'inspect') task = controlled.manager.inspect();
    else if (operation === 'recover') task = controlled.manager.recover(args[0], args[1]);
    else throw Error('Only explicit select/inspect/named controlled recovery is admitted');
    tasks.push(task); try {return await task;} finally {completed++;}
  });
  await installDesktopHostShim(page, renderer.port);
  await page.addInitScript(() => {
    const invoke = (operation: string, args: unknown[] = []) => (window as any).__portablePendingDispatch(operation, args);
    const refused = async () => {throw Error('Install, preview and launch are outside these four GUI cases');};
    Object.assign((window as any).api, {
      getDistributionStatus: async () => ({app_version: '0.1.0', platform: 'darwin', architecture: 'arm64',
        signature: {status: 'development', reason: 'Controlled browser output; no native publisher acceptance', checked_at: 'controlled'},
        update: {configured: false, configuration: null, status: 'not_configured', release: null, automatic_update_available: false}}),
      selectPortableUpdateHome: () => invoke('select'), inspectPortableUpdate: () => invoke('inspect'),
      recoverPortableUpdate: (action: string, expected: unknown) => invoke('recover', [action, expected]),
      previewPortableUpdate: refused, applyPortableUpdate: refused, launchPortableUpdate: refused, inspectPortableLaunch: refused});
  });
  try {
    await page.goto(renderer.url); await settle();
    const created = await page.request.post(renderer.origin + '/api/project/create', {data: {name: 'Owned portable pending UI', task: 'classification'}, timeout: 10_000});
    expect(created.status()).toBe(200); const project = await created.json();
    await page.getByTitle('프로젝트 관리', {exact: true}).click();
    const projects = page.getByRole('dialog', {name: '프로젝트 관리', exact: true});
    await projects.getByRole('button', {name: '폴더에서 열기', exact: true}).click();
    await projects.getByPlaceholder('/path/to/project', {exact: true}).fill(project.project_dir);
    const accepted = page.waitForResponse(response => response.request().method() === 'POST'
      && new URL(response.url()).origin === renderer.origin && new URL(response.url()).pathname === '/api/project/open'
      && new URL(response.url()).search === '' && Object.keys(JSON.parse(response.request().postData() || 'null') || {}).sort().join(',') === 'project_dir'
      && JSON.parse(response.request().postData() || 'null')?.project_dir === project.project_dir, {timeout: 10_000});
    await projects.getByRole('button', {name: '프로젝트 열기', exact: true}).click(); expect((await accepted).status()).toBe(200);
    await expect(projects).toHaveCount(0); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await settle();
    ownedEmptyProject = await api('/api/project/current'); expect(ownedEmptyProject).toMatchObject({id: project.id, project_dir: project.project_dir, source_dataset_dir: null});
    const open = async () => {
      await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click();
      await page.getByRole('navigation', {name: '배포 운영 화면', exact: true}).getByRole('button', {name: '설치·진단', exact: true}).click(); await settle();
    };
    await open();
    const panel = page.getByRole('region', {name: '별도 portable 앱 업데이트', exact: true});
    const select = panel.getByRole('button', {name: 'portable 설치 폴더 선택', exact: true});
    const inspect = panel.getByRole('button', {name: 'portable 상태 다시 읽기', exact: true});
    const confirm = panel.getByLabel(confirmation, {exact: true});
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
      '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/versions', '/api/product-delivery/packages',
      '/api/product-delivery/hardware', '/api/product-delivery/installation', '/api/product-delivery/runtime-packs',
      '/api/runtime-services', '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', '/api/flow-evaluations/approvals/active'];
    const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
    const roots = {project: project.project_dir as string, annotations: ownedEmptyProject.annotations_dir as string,
      dataset: workspace.dataset, selectedPortable: controlled.root, trustedResources: controlled.resources};
    const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
    const setupWrites = [...writes]; writes.length = 0;
    const unchanged = async () => {
      await settle(); for (const [key, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[key]);
      for (const [endpoint, record] of Object.entries(apiBefore)) expect(await api(endpoint)).toEqual(record);
      await settle(); for (const [key, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[key]); expect(writes).toEqual([]);
      for (const image of workspace.images) expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
    };
    const blank = async () => {await expect(panel.getByText('선택한 설치:', {exact: false})).toHaveCount(0); await expect(inspect).toBeDisabled();};
    if (scenario.mode !== 'select') {await select.click(); await expect(inspect).toBeEnabled(); await expect(panel).toContainText('선택한 설치: ' + controlled.root);}
    if (scenario.mode === 'reopen') {
      await inspect.click(); await expect(inspect).toBeEnabled(); await confirm.check(); const callsBefore = hostCalls.length;
      await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await open();
      await blank(); expect(hostCalls).toHaveLength(callsBefore);
    } else {
      controlled.arm(scenario.mode === 'select' ? 'inspect' : 'recover');
      if (scenario.mode === 'select') await select.click();
      else {await confirm.check(); await panel.getByRole('button', {name: scenario.recovery === 'finish' ? '같은 업데이트 마무리' : '새 쓰기 보존하며 복구', exact: true}).click();}
      await expect.poll(controlled.started, {timeout: 10_000}).toBe(true); const callsBefore = hostCalls.length;
      const dialog = page.getByRole('dialog', {name: '패키지·장치·설치·진단', exact: true});
      await dialog.getByRole('button', {name: '패키지·장치·설치·진단 닫기', exact: true}).click(); await expect(panel).toHaveCount(0);
      await open(); await blank(); expect(hostCalls).toHaveLength(callsBefore);
      controlled.release(); await expect.poll(() => completed, {timeout: 10_000}).toBe(tasks.length);
      await blank(); expect(hostCalls).toHaveLength(callsBefore);
    }
    // A new mounted view never adopts the retired response; explicit reselection reads it.
    await select.click(); await expect(inspect).toBeEnabled(); await expect(panel).toContainText('선택한 설치: ' + controlled.root);
    await expect(panel).toContainText('앱·데이터 전환 확인됨 · 버전 1.0.0 · 데이터 세대 3'); await expect(confirm).not.toBeChecked();
    await expect(panel.getByRole('alert')).toHaveCount(0);
    const recoveries = controlled.commands.filter(row => row.command === 'recover');
    if (scenario.recovery) {
      expect(recoveries).toHaveLength(1); expect(hostCalls.filter(row => row.operation === 'recover')).toEqual([{operation: 'recover', args: [scenario.recovery,
        {installation_id: controlled.state.installation_id, update_id: controlled.state.update_id}]}]);
      await expect(panel.getByRole('button', {name: scenario.recovery === 'finish' ? '같은 업데이트 마무리' : '새 쓰기 보존하며 복구', exact: true})).toBeDisabled();
    } else expect(recoveries).toHaveLength(0);
    await unchanged(); await panel.scrollIntoViewIfNeeded(); await evidence.screenshot(page, scenario.action + '-' + scenario.dimension);
    evidence.note('portable_pending_ui', {cell: 'U025.' + scenario.action + '.' + scenario.dimension, setupWrites, hostCalls,
      original_main_manager_commands: controlled.commands, before, roots, all_post_baseline_API_mutations: writes,
      actual_renderer_and_production_component: true, actual_original_main_manager: true,
      explicit_same_owned_home_required: true, retired_response_not_adopted: true, automatic_recovery_replay: false,
      CLI_and_signature: 'controlled responses only; inert executable is never spawned', actual_installer_or_recovery_transaction: false,
      actual_frozen_A45_B45_execution: false, actual_source_CPU_or_model: false, native_electron: false, release_ready: false});
  } catch (error) {primary = error; throw error;}
  finally {
    controlled.release(); const outcomes = await Promise.allSettled(tasks);
    evidence.note('portable_pending_owned_tasks', {started: tasks.length, completed, outcomes: outcomes.map(row => ({status: row.status,
      error: row.status === 'rejected' ? String(row.reason) : null}))});
    page.off('request', requestStart); page.off('requestfinished', requestFinished); page.off('requestfailed', requestFailed);
    if (!primary) expect(outcomes.every(row => row.status === 'fulfilled')).toBe(true);
  }
});

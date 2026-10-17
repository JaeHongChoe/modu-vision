import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page, Request, Response} from '@playwright/test';
import {test, expect, type Evidence} from './fixtures/test';
import {datasetPickerOwnedHost} from './fixtures/dataset-picker-owned-host';
import {handoffProject} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';

test.use({actionTimeout: 10_000});
const sha = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex');
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
type Cohort = {source: string; count: number; originals: Array<{path: string; sha256: string}>};
type Mutation = {url: string; method: string; body: any; project: string | null; context: string | null};

function cohort(root: string, name: string, count: number): Cohort {
  const source = path.join(fs.realpathSync(root), name), originals: Cohort['originals'] = [];
  for (let index = 0; index < count; index++) {
    const file = path.join(source, 'train', index % 2 ? 'NG' : 'OK', `${name}-${index}.png`);
    fs.mkdirSync(path.dirname(file), {recursive: true});
    const bytes = png(24, 3, (x, y) => [(x + index * 19) % 256, (y + name.length * 13) % 256, index * 23]);
    fs.writeFileSync(file, bytes, {flag: 'wx'}); originals.push({path: file, sha256: sha(bytes)});
  }
  return {source, count, originals};
}

async function seed(api: Api, name: string, data: Cohort) {
  await api('/api/project/create', {name, task: 'classification'});
  const project = await api('/api/project/update', {source_dataset_dir: data.source}, 'PUT');
  const summary = await api('/api/dataset/import', {folder_path: data.source, task: 'classification', validate_images: true});
  expect(summary.total_images).toBe(data.count); return project;
}

async function view(page: Page, project: any, data: Cohort) {
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
  await expect(page.locator('span[title]').filter({hasText: data.source})).toHaveAttribute('title', data.source);
  await expect(page.getByText('학습 가능 이미지', {exact: true}).locator('..').getByText(String(data.count), {exact: true})).toBeVisible();
  const images = page.locator('button[aria-label$=" 라벨링에서 열기"] img');
  await expect(images).toHaveCount(data.count);
  await expect.poll(() => images.evaluateAll(nodes => nodes.every(node => node instanceof HTMLImageElement && node.complete && node.naturalWidth > 0))).toBe(true);
  const paths = await images.evaluateAll(nodes => nodes.map(node => new URL((node as HTMLImageElement).src).searchParams.get('file_path')).sort());
  expect(paths).toEqual(data.originals.map(row => row.path).sort());
  return {project_id: project.id, source: data.source, count: data.count, original_paths: paths,
    original_sha256: data.originals, all_gallery_pixels_loaded: true};
}

async function holdPicker(page: Page) {
  await page.evaluate(() => {
    const state = {calls: 0, released: false, resolve: null as null | ((value: string | null) => void)};
    (window as any).__datasetPickerGate = state;
    window.api!.selectFolder = async () => {state.calls++; return new Promise<string | null>(resolve => {state.resolve = resolve;});};
  });
  await page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click();
  await expect.poll(() => page.evaluate(() => (window as any).__datasetPickerGate.calls)).toBe(1);
}

async function releasePicker(page: Page, source: string | null) {
  await page.evaluate(async value => {
    const gate = (window as any).__datasetPickerGate;
    if (!gate.resolve || gate.released) throw Error('Owned picker is not pending');
    gate.released = true; gate.resolve(value);
    // The actual async click handler gets its answer, then the browser completes
    // two rendering turns. Later fresh UI operations provide another barrier.
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  }, source);
}

function responseFor(page: Page, origin: string, route: string, method: string, field: string, value: string) {
  const waiting = page.waitForResponse(response => response.request().frame() === page.mainFrame()
    && new URL(response.url()).origin === origin && new URL(response.url()).pathname === route
    && response.request().method() === method && response.request().postDataJSON()?.[field] === value, {timeout: 10_000});
  void waiting.catch(() => undefined); return waiting;
}

async function receipt(page: Page, response: Response, project: any, evidence: Evidence, label: string) {
  expect(response.request().frame()).toBe(page.mainFrame()); expect(response.status()).toBe(200);
  expect(response.request().headers()['x-vision-project']).toBe(project.id);
  const context = JSON.parse(response.request().headers()['x-vision-context']); expect(context.project_id).toBe(project.id);
  const raw = await response.body(); expect(raw.length).toBeLessThan(1024 * 1024); expect(await response.finished()).toBeNull();
  const body = JSON.parse(raw.toString('utf8'));
  const file = test.info().outputPath(label + '-actual-response.json'); fs.writeFileSync(file, raw, {flag: 'wx'}); evidence.addFile(file);
  evidence.note(label, {url: response.url(), method: response.request().method(), request_body: response.request().postDataJSON(),
    project_id: project.id, context, response_status: response.status(), response_body: body, response_sha256: sha(raw), response_file: file, actual_main_frame: true, finished: true});
  return body;
}

async function freshPick(page: Page, origin: string, prefix: string, project: any, data: Cohort, evidence: Evidence) {
  await page.evaluate(source => {window.api!.selectFolder = async () => source;}, data.source);
  const imported = responseFor(page, origin, prefix + '/api/dataset/import', 'POST', 'folder_path', data.source);
  const saved = responseFor(page, origin, prefix + '/api/project/update', 'PUT', 'source_dataset_dir', data.source);
  await page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click();
  const summary = await receipt(page, await imported, project, evidence, prefix ? 'shared_fresh_import' : 'local_fresh_import');
  expect(summary.total_images).toBe(data.count);
  const updated = await receipt(page, await saved, project, evidence, prefix ? 'shared_fresh_source' : 'local_fresh_source');
  expect(updated.id).toBe(project.id); expect(updated.source_dataset_dir).toBe(data.source);
  evidence.note(prefix ? 'shared_fresh_gallery' : 'local_fresh_gallery', await view(page, updated, data)); return updated;
}

test('delayed dataset picker keeps project and backend ownership and a fresh choice succeeds', async ({page, rendererBuild, workspace, evidence, loopbackGuard}) => {
  test.setTimeout(300_000);
  const host = await datasetPickerOwnedHost(workspace, rendererBuild.outDir, evidence);
  loopbackGuard.allow(host.port);
  const mutations: Mutation[] = [], prohibited: string[] = [], consoleErrors: string[] = [], pageErrors: string[] = [];
  const observe = (request: Request) => {
    const url = new URL(request.url()); if (url.origin !== host.origin) return;
    if (request.method() === 'POST' && /\/(training\/start|train|compute\/jobs|dataset\/split)$/.test(url.pathname)) prohibited.push(request.url());
    if ((request.method() === 'POST' && url.pathname.endsWith('/api/dataset/import'))
      || (request.method() === 'PUT' && url.pathname.endsWith('/api/project/update'))) {
      mutations.push({url: request.url(), method: request.method(), body: request.postDataJSON(),
        project: request.headers()['x-vision-project'] || null, context: request.headers()['x-vision-context'] || null});
    }
  };
  page.on('request', observe); page.on('console', message => {if (message.type() === 'error') consoleErrors.push(message.text());});
  page.on('pageerror', error => pageErrors.push(error.message));
  let primary: unknown;
  try {
    const A = cohort(workspace.root, 'picker-source-a', 2), B = cohort(workspace.root, 'picker-source-b', 4);
    const C = cohort(workspace.root, 'picker-source-c', 3), staleA = cohort(workspace.root, 'picker-stale-a', 5);
    const staleB = cohort(workspace.root, 'picker-stale-b', 7), freshB = cohort(workspace.root, 'picker-fresh-b', 6), freshC = cohort(workspace.root, 'picker-fresh-c', 8);
    const projectA = await seed(host.callLocal, 'Picker owner A', A);
    let projectB = await seed(host.callLocal, 'Picker owner B', B);
    let projectC = await seed(host.callShared, 'Picker shared owner C', C);
    await host.callLocal('/api/project/open', {project_dir: projectA.project_dir});
    await host.install(page); await page.goto(host.url); await host.attachSessionShim(page); await view(page, projectA, A);
    const manifest = (project: any) => fs.readFileSync(path.join(project.project_dir, 'project.json'));
    await holdPicker(page);
    const switched = await handoffProject(page, {project: projectB, source: B.source}, host.origin);
    await view(page, projectB, B);
    const beforeProjectRelease = [manifest(projectA), manifest(projectB), manifest(projectC)], beforeProjectWrites = mutations.slice();
    await releasePicker(page, staleA.source);
    expect(mutations).toEqual(beforeProjectWrites);
    expect([manifest(projectA), manifest(projectB), manifest(projectC)]).toEqual(beforeProjectRelease);
    expect((await host.callLocal('/api/project/current')).source_dataset_dir).toBe(B.source); await view(page, projectB, B);
    projectB = await freshPick(page, host.origin, '', projectB, freshB, evidence);
    await evidence.screenshot(page, 'dataset-picker-project-handoff-fresh-owner-b');

    await holdPicker(page);
    await page.getByRole('button', {name: '서버 관리', exact: true}).click();
    await page.getByText('공동 작업 서버', {exact: true}).click();
    await page.getByLabel('공동 작업 서버 주소', {exact: true}).fill(host.origin + '/picker-shared');
    await page.getByLabel('공유 계정 이름', {exact: true}).fill('pickeradmin');
    await page.getByLabel('공유 계정 비밀번호', {exact: true}).fill(host.password);
    const sharedCurrent = page.waitForResponse(response => new URL(response.url()).pathname === '/picker-shared/api/project/current' && response.request().method() === 'GET');
    void sharedCurrent.catch(() => undefined);
    await page.getByRole('button', {name: '연결·로그인', exact: true}).click();
    expect((await sharedCurrent).status()).toBe(200);
    await expect(page.getByLabel('공동 작업 프로젝트', {exact: true})).toHaveValue(projectC.id);
    await page.getByRole('dialog', {name: '컴퓨팅 서버 관리', exact: true}).getByRole('button', {name: '닫기', exact: true}).click();
    await view(page, projectC, C);
    const beforeBackendRelease = [manifest(projectA), manifest(projectB), manifest(projectC)], beforeBackendWrites = mutations.slice();
    await releasePicker(page, staleB.source);
    expect(mutations).toEqual(beforeBackendWrites);
    expect([manifest(projectA), manifest(projectB), manifest(projectC)]).toEqual(beforeBackendRelease);
    expect((await host.callShared('/api/project/current')).source_dataset_dir).toBe(C.source); await view(page, projectC, C);
    projectC = await freshPick(page, host.origin, '/picker-shared', projectC, freshC, evidence);
    await evidence.screenshot(page, 'dataset-picker-backend-handoff-fresh-shared-owner-c');
    expect(mutations.map(row => row.body.folder_path || row.body.source_dataset_dir)).toEqual([freshB.source, freshB.source, freshC.source, freshC.source]);
    expect(mutations.map(row => row.project)).toEqual([projectB.id, projectB.id, projectC.id, projectC.id]);
    expect(mutations.map(row => row.method)).toEqual(['POST', 'PUT', 'POST', 'PUT']);
    expect((await host.callLocal('/api/project/current')).source_dataset_dir).toBe(freshB.source);
    expect((await host.callShared('/api/project/current')).source_dataset_dir).toBe(freshC.source);
    const originals = [A, B, C, staleA, staleB, freshB, freshC].flatMap(data => data.originals);
    for (const row of originals) {expect(sha(fs.readFileSync(row.path))).toBe(row.sha256); evidence.addFile(row.path);}
    expect(prohibited).toEqual([]); expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(loopbackGuard.blocked()).toEqual([]);
    evidence.note('dataset_picker_ownership', {actual_renderer_and_two_owned_backends: true, actual_UI_project_switch: switched,
      actual_UI_backend_login: true, controlled_desktop_dialog_answer: true, stale_sources: [staleA.source, staleB.source],
      stale_answer_mutations: [], mutations, projects: [projectA, projectB, projectC], originals,
      browser_errors: {page: pageErrors, console: consoleErrors}, prohibited_writes: prohibited,
      native_OS_picker: false, installed_native_app: false, training_or_model_quality_assessed: false});
  } catch (error) {primary = error;}
  finally {
    page.off('request', observe);
    const failures: unknown[] = [];
    try {await page.close();} catch (error) {failures.push(error);}
    try {await host.close();} catch (error) {failures.push(error);}
    evidence.note('dataset_picker_completion', {primary_failure: primary === undefined ? null : String(primary),
      cleanup_failures: failures.map(String), unknown_closure_is_not_a_pass: true});
    if (primary === undefined && failures.length) primary = failures.shift();
    if (primary instanceof Error && failures.length) try {
      Object.defineProperty(primary, 'dataset_picker_cleanup_failures', {value: failures.map(String), configurable: true});
    } catch {/* Keep the original failure; evidence above retains cleanup failures. */}
  }
  if (primary !== undefined) throw primary;
});

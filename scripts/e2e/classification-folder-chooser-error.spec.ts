import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {BigIntStats} from 'node:fs';
import type {APIRequestContext, Page, Request, Response} from '@playwright/test';
import {test, expect, type Workspace, type Evidence, type RendererServer} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffWithin} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';

test.use({actionTimeout: 10_000});
const FILES = ['train/OK/a.png', 'train/OK/b.png', 'train/NG/c.png', 'val/OK/d.png', 'val/NG/e.png', 'test/NG/f.png'];
const DEPENDENCIES = [
  'scripts/e2e/classification-folder-chooser-error.spec.ts', 'scripts/e2e/fixtures/test.ts',
  'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
  'scripts/e2e/fixtures/remaining-project-handoff.ts', 'scripts/e2e/qa/appFlow.ts', 'playwright.config.ts',
  'src/renderer/components/dataset/DatasetStudio.tsx', 'src/renderer/components/dataset/DatasetStatisticsPanel.tsx',
  'src/renderer/stores/useDatasetStore.ts', 'src/renderer/stores/useProjectStore.ts',
  'src/renderer/services/api.ts', 'src/renderer/services/hostAdapter.ts', 'backend/api/routes_dataset.py', 'backend/api/routes_project.py',
  'backend/api/routes_dataset_metadata.py', 'backend/engine/dataset_summary.py',
  'backend/engine/dataset_loaders.py', 'backend/engine/industrial_adapters.py',
];
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));
const raw9 = (s: BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.uid, s.gid, s.size, s.mtimeNs, s.ctimeNs].map(String);
const CHOOSER_ERROR = 'Owned classification folder chooser failed';
type FileSnapshot = {kind: 'file'; raw9: string[]; sha256: string};
type Tree = Record<string, FileSnapshot | {kind: 'directory'; raw9: string[]}>;
type Snapshot = {source: Tree; harness_originals: Tree; project: Tree};
type Fixtures = {page: Page; request: APIRequestContext; renderer: RendererServer; workspace: Workspace; evidence: Evidence};

async function exercise( {page, request, renderer, workspace: w, evidence: e}: Fixtures) {
  const pins = Object.fromEntries(DEPENDENCIES.map(file => [file, sha(fs.readFileSync(file))]));
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(); expect(head).toMatch(/^[a-f0-9]{40}$/);
  const fd = {opened: 0, closed: 0}, proofs: any[] = [], writes: unknown[] = [], callbackFailures: unknown[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], non2xx: unknown[] = [];
  const pageError = (error: Error) => pageErrors.push(error.message);
  const consoleError = (message: {type(): string; text(): string}) => {if (message.type() === 'error') consoleErrors.push(message.text());};
  const failedResponse = (response: Response) => {try {const u = new URL(response.url()); if (u.origin === renderer.origin && u.pathname.startsWith('/api/') && response.status() >= 400) non2xx.push({method: response.request().method(), route: u.pathname, status: response.status()});} catch (error) {callbackFailures.push(error);}};
  page.on('pageerror', pageError); page.on('console', consoleError); page.on('response', failedResponse);
  const save = (label: string, value: unknown) => {const file = path.join(w.logs, label + '.json'); fs.writeFileSync(file, JSON.stringify(value, null, 2), {flag: 'wx'}); e.addFile(file);};
  const file = (name: string): FileSnapshot => {
    const before = fs.lstatSync(name, {bigint: true}); expect(before.isSymbolicLink()).toBe(false); expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
    const descriptor = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); fd.opened++;
    try {expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); const bytes = fs.readFileSync(descriptor); expect(BigInt(bytes.length)).toBe(before.size);
      expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); expect(raw9(fs.lstatSync(name, {bigint: true}))).toEqual(raw9(before)); return {kind: 'file', raw9: raw9(before), sha256: sha(bytes)};
    } finally {fs.closeSync(descriptor); fd.closed++;}
  };
  const tree = (root: string): Tree => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Tree = {};
    const walk = (directory: string) => {const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw9: raw9(before)};
      for (const name of names) {const member = path.join(directory, name), info = fs.lstatSync(member); expect(info.isSymbolicLink()).toBe(false); if (info.isDirectory()) walk(member); else rows[path.relative(root, member)] = file(member);}
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw9(fs.lstatSync(directory, {bigint: true}))).toEqual(raw9(before));
    }; walk(root); return rows;
  };
  const api = async (route: string, body?: unknown) => {const started = performance.now(), deadline = started + 10_000;
    const response = await handoffWithin(request.fetch(renderer.origin + route, {method: body === undefined ? 'GET' : 'POST', ...(body === undefined ? {} : {data: body}), timeout: remaining(deadline)}), deadline, 'fixture API');
    const bytes = await handoffWithin(response.body(), deadline, 'fixture full body'); expect(response.status()).toBe(200); expect(bytes.length).toBeLessThanOrEqual(1024 * 1024); const value = JSON.parse(bytes.toString('utf8'));
    proofs.push({kind: 'fixture API', route, request: body ?? null, status: response.status(), response_sha256: sha(bytes), response: value, started, deadline, completed: performance.now()}); return value;
  };
  const wire = (route: string, method: string, match: (r: Request, u: URL) => boolean) => {
    let original: Request | undefined, accept!: (r: Response) => void, refuse!: (error: unknown) => void;
    const reply = new Promise<Response>((resolve, reject) => {accept = resolve; refuse = reject;}); void reply.catch(() => undefined);
    const poison = (error: unknown) => {callbackFailures.push(error); refuse(error);};
    const timer = setTimeout(() => poison(Error('Original 10s UI response missing: ' + route)), 10_000);
    const listener = (r: Request) => {try {const u = new URL(r.url()); if (u.origin === renderer.origin && u.pathname === route && r.method() === method) {expect(r.frame()).toBe(page.mainFrame()); if (match(r, u)) {expect(original).toBeUndefined(); original = r;}}} catch (error) {poison(error);}};
    const responded = (r: Response) => {try {if (r.request() === original) accept(r);} catch (error) {poison(error);}};
    page.on('request', listener); page.on('response', responded);
    return {reply, close: () => {clearTimeout(timer); page.off('request', listener); page.off('response', responded);}, request: () => original};
  };
  let project: any, context: any;
  const body = async (response: Response, deadline: number, label: string, original: Request | undefined) => {
    const own = response.request(); expect(original).toBeDefined(); expect(own).toBe(original); expect(own.frame()).toBe(page.mainFrame()); expect(new URL(response.url()).origin).toBe(renderer.origin); expect(response.status()).toBe(200);
    expect(await handoffWithin(own.headerValue('x-vision-project'), deadline, 'own project header')).toBe(project.id);
    const rawContext = await handoffWithin(own.headerValue('x-vision-context'), deadline, 'own context header'); expect(rawContext).not.toBeNull(); const actualContext = JSON.parse(rawContext!); expect(actualContext.project_id).toBe(project.id); expect(actualContext.mode).toBe('local');
    if (context) expect(actualContext).toEqual(context); else context = actualContext;
    const raw = await handoffWithin(response.body(), deadline, 'same Request full response'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024); expect(await handoffWithin(response.finished(), deadline, 'same response finished')).toBeNull();
    const actual = JSON.parse(raw.toString('utf8')), rawFile = path.join(w.logs, label + '-raw-response.json'); fs.writeFileSync(rawFile, raw, {flag: 'wx'}); e.addFile(rawFile);
    proofs.push({kind: 'original UI response', label, method: own.method(), url: response.url(), request_body: own.postDataJSON(), project_id: project.id, context: actualContext, status: 200, response: actual, response_sha256: sha(raw), bytes: raw.length, raw_file: rawFile, same_Request: true, main_frame: true, finished: true, deadline, completed: performance.now()}); return actual;
  };
  const summary = (value: any, count: 6) => {expect(value.status).toBe('success'); expect(value.total_images).toBe(count); expect(value.source_images).toBe(count); expect(value.unlabeled_images).toBe(0); expect(value.classes).toEqual({OK: 3, NG: 3}); expect(value.split).toEqual({train: 3, val: 2, test: 1});
    expect(value.corrupted_images).toEqual([]); expect(value.validation).toEqual({requested: true, checked_images: count, complete: true, scope: 'all images: every image file under the folder was decoded'}); expect(value.split_supported).toBe(true); expect(value.split_unavailable_reason).toBeNull();
  };
  const source = path.join(fs.realpathSync(w.root), 'owned-classification-source');
  for (const [index, relative] of FILES.entries()) {const name = path.join(source, relative); fs.mkdirSync(path.dirname(name), {recursive: true}); fs.writeFileSync(name, png(32, 3, (x, y) => [(x + index * 7) % 256, (y + index * 9) % 256, 50 + index]), {flag: 'wx'}); e.addFile(name);}
  const view = async (selected: string, count: 6, deadline: number) => {
    const opts = () => ({timeout: remaining(deadline)}), panel = page.getByRole('region', {name: '활성 라벨 세트 데이터 통계', exact: true});
    await handoffWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name, opts()), deadline, 'own project title');
    await handoffWithin(expect(page.locator('span[title]').filter({hasText: selected})).toHaveAttribute('title', selected, opts()), deadline, 'own saved source');
    await handoffWithin(expect(page.getByText('학습 가능 이미지', {exact: true}).locator('..').getByText(String(count), {exact: true})).toBeVisible(opts()), deadline, 'own eligible count');
    const split = [['Train:', 3], ['Val:', 2], ['Test:', 1]] as const;
    for (const [label, value] of split) await handoffWithin(expect(page.getByText(label, {exact: true}).locator('..').getByText(String(value), {exact: true})).toBeVisible(opts()), deadline, 'own partition count');
    for (const [label, value] of [['전체', count], ['라벨 완료', count], ['라벨 미완료', 0]] as const) await handoffWithin(expect(panel.getByRole('button').filter({has: page.getByText(label, {exact: true})}).locator('strong')).toHaveText(String(value), opts()), deadline, 'own label count');
    const images = page.locator('button[aria-label$=" 라벨링에서 열기"] img'); await handoffWithin(expect(images).toHaveCount(count, opts()), deadline, 'own gallery count');
    expect(await handoffWithin(images.evaluateAll(nodes => nodes.map(node => new URL((node as HTMLImageElement).src).searchParams.get('file_path')).sort()), deadline, 'exact gallery source paths')).toEqual(FILES.map(relative => path.join(source, relative)).sort());
    await handoffWithin(expect.poll(() => images.evaluateAll(nodes => nodes.every(node => node instanceof HTMLImageElement && node.complete && node.naturalWidth === 32 && node.naturalHeight === 32)), opts()).toBe(true), deadline, 'all owning gallery images decoded at source dimensions');
    await handoffWithin(expect(page.getByRole('alert').filter({hasText: '데이터셋을 불러오지 못했습니다'})).toHaveCount(0, opts()), deadline, 'no import error');
  };
  const choose = async (selected: string, count: 6, label: string) => {
    await page.evaluate(value => {window.api!.selectFolder = async () => value;}, selected);
    const imported = wire('/api/dataset/import', 'POST', r => r.postDataJSON()?.folder_path === selected), saved = wire('/api/project/update', 'PUT', r => r.postDataJSON()?.source_dataset_dir === selected);
    try {const deadline = performance.now() + 10_000; await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'ordinary folder import click');
      const importedBody = await body(await handoffWithin(imported.reply, deadline, 'actual import'), deadline, label + '-import', imported.request()); expect(imported.request()!.postDataJSON()).toEqual({folder_path: selected, task: 'classification', validate_images: true}); summary(importedBody, count);
      const updated = await body(await handoffWithin(saved.reply, deadline, 'actual saved source'), deadline, label + '-source', saved.request()); expect(saved.request()!.postDataJSON()).toEqual({source_dataset_dir: selected});
      expect(updated).toEqual({...project, source_dataset_dir: selected, updated_at: updated.updated_at}); expect(updated.updated_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/); project = updated;
      await view(selected, count, deadline);
    } finally {imported.close(); saved.close();}
  };
  const warm = async (selected: string) => {await api('/api/evaluation/history?source_dataset_path=' + encodeURIComponent(selected) + '&task=classification'); await api('/api/team-data'); await api('/api/team-data/readiness'); await api('/api/training-workspace/tasks');};
  const statistics = async (selected: string, count: 6) => {
    const value = await api('/api/dataset/metadata/statistics?folder_path=' + encodeURIComponent(selected)); expect(value.total).toBe(count); expect(value.labelset_id).toBe('default');
    return value;
  };
  const mutations = (r: Request) => {try {if (new URL(r.url()).origin === renderer.origin && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push({method: r.method(), route: new URL(r.url()).pathname, body: r.postDataJSON()});} catch (error) {callbackFailures.push(error);}};
  const snapshot = (): Snapshot => ({source: tree(source), harness_originals: tree(w.dataset), project: tree(project.project_dir)});
  let before: Snapshot | undefined, primary: unknown, primaryFailed = false, baselineProject: any, acknowledgement: unknown;
  const after: Record<string, Snapshot> = {};
  const preserved = (value: Snapshot) => expect(value).toEqual(before);

  try {
    await installDesktopHostShim(page, renderer.port); project = await api('/api/project/create', {name: 'Owned classification chooser error', task: 'classification'}); expect(path.dirname(project.project_dir)).toBe(w.projects); expect(project.task).toBe('classification');
    await page.goto(renderer.url); await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    await choose(source, 6, 'prepare-six'); await warm(source); const originalStatistics = await statistics(source, 6);
    await view(source, 6, performance.now() + 10_000); baselineProject = structuredClone(project); before = snapshot(); save('immutable-before', before); page.on('request', mutations);
    await page.evaluate(message => {
      const gate = {calls: 0, options: null as unknown, rejected: false, reject: null as null | ((error: Error) => void)};
      (window as any).__ownedClassificationChooserError = gate;
      window.api!.selectFolder = async options => {gate.calls++; gate.options = options; return new Promise<string | null>((_resolve, reject) => {gate.reject = reject;});};
      (window as any).__ownedClassificationChooserErrorMessage = message;
    }, CHOOSER_ERROR);
    const deadline = performance.now() + 10_000;
    await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'ordinary owning folder chooser click');
    await handoffWithin(expect.poll(() => page.evaluate(() => (window as any).__ownedClassificationChooserError.calls), {timeout: remaining(deadline)}).toBe(1), deadline, 'one owning chooser pending');
    await handoffWithin(page.evaluate(() => {
      const gate = (window as any).__ownedClassificationChooserError;
      if (gate.calls !== 1 || typeof gate.reject !== 'function' || gate.rejected) throw Error('Owned chooser is not pending');
      gate.rejected = true; gate.reject(Error((window as any).__ownedClassificationChooserErrorMessage)); gate.reject = null;
    }), deadline, 'reject the exact original pending chooser');
    const alert = page.getByRole('alert').filter({hasText: CHOOSER_ERROR});
    await handoffWithin(expect(alert).toHaveText(CHOOSER_ERROR, {timeout: remaining(deadline)}), deadline, 'owning handler caught the chooser failure and rendered its message');
    acknowledgement = await handoffWithin(page.evaluate(() => {const gate = (window as any).__ownedClassificationChooserError; return {calls: gate.calls, options: gate.options, rejected: gate.rejected, reject_cleared: gate.reject === null};}), deadline, 'chooser failure acknowledgement');
    expect(acknowledgement).toEqual({calls: 1, options: {title: 'Select Industrial Dataset'}, rejected: true, reject_cleared: true});
    await handoffWithin(page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))), deadline, 'original error continuation rendered');
    await view(source, 6, deadline); expect(writes).toEqual([]);
    expect(await api('/api/project/current')).toEqual(baselineProject); expect(await statistics(source, 6)).toEqual(originalStatistics);
    after.action = snapshot(); preserved(after.action); save('immutable-after-chooser-error', after.action); await e.screenshot(page, 'owning-chooser-error-keeps-source-gallery-and-counts');
    const restored = wire('/api/dataset/current-summary', 'GET', (_r, u) => u.searchParams.get('folder_path') === source && u.searchParams.get('task') === 'classification');
    try {
      const reloadDeadline = performance.now() + 10_000;
      await handoffWithin(page.reload(), reloadDeadline, 'ordinary owning reload');
      summary(await body(await handoffWithin(restored.reply, reloadDeadline, 'own saved-source summary'), reloadDeadline, 'reload-summary', restored.request()), 6);
      await view(source, 6, reloadDeadline);
      await handoffWithin(expect(page.getByRole('alert').filter({hasText: CHOOSER_ERROR})).toHaveCount(0, {timeout: remaining(reloadDeadline)}), reloadDeadline, 'transient chooser error cleared on ordinary reload');
    } finally {restored.close();}
    expect(await api('/api/project/current')).toEqual(baselineProject); expect(await statistics(source, 6)).toEqual(originalStatistics);
    after.reload = snapshot(); preserved(after.reload); expect(after.reload).toEqual(after.action); expect(writes).toEqual([]); save('immutable-after-reload', after.reload);
    await e.screenshot(page, 'owning-classification-source-restored-after-chooser-error');
  } catch (error) {primaryFailed = true; primary = error; throw error;} finally {
    const failures: unknown[] = []; page.off('request', mutations); page.off('pageerror', pageError); page.off('console', consoleError); page.off('response', failedResponse);
    try {expect(callbackFailures).toEqual([]); expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(non2xx).toEqual([]); expect(fd.closed).toBe(fd.opened); expect(Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]))).toEqual(pins); expect(execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim()).toBe(head);} catch (error) {failures.push(error);}
    e.note('classification_folder_chooser_error', {cells: ['F019.native-classification-import-label-summary.error', 'F022.native-classification-partitions-import-reopen.error'], source: {head, sha256: pins}, actual_renderer_backend: true, controlled_desktop_shim_rejected_folder_answer: true, actual_native_picker: false, installed_native: false, source_electron: false, backend_500_or_import_route_error_claim: false, acknowledgement, project, baselineProject, expected: {original_files: FILES, source, count: 6, classes: {OK: 3, NG: 3}, split: {train: 3, val: 2, test: 1}}, original_10s_action_and_reload_frames: true, no_arbitrary_negative_sleep: true, proofs, before, after, post_baseline_mutations: writes, protected_complete_project_includes_models_and_labelset_registry: true, protected_harness_original_dataset: true, raw9_file_and_directory_custody: true, descriptors: fd, page_errors: pageErrors, console_errors: consoleErrors, non2xx_API_responses: non2xx, callback_errors: callbackFailures.map(String), gallery_claim: 'readiness/dimensions/paths only; no full RGB canvas comparison', training_or_model_execution: false, human_model_quality_acceptance: false, original_parent_acceptance: false, secondary_errors: failures.map(error => String(error))});
    if (primary instanceof Error && failures.length) Object.defineProperty(primary, 'secondary_errors', {value: failures.map(String)}); else if (!primaryFailed && failures.length) throw failures[0];
  }
}

test('classification folder chooser failure preserves owning source counts and bytes through ordinary reload', async ({page, request, renderer, workspace, evidence}) => exercise({page, request, renderer, workspace, evidence}));

import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {BigIntStats} from 'node:fs';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffWithin} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';

const {createScopedGalleryMediaWitness} = require('./fixtures/scoped-gallery-media-witness.cjs');

test.use({actionTimeout: 10_000});
const CELLS = ['F019.native-classification-import-label-summary.invalid', 'F022.native-classification-partitions-import-reopen.invalid'];
const FILES = ['train/OK/a.png', 'train/OK/b.png', 'train/NG/c.png', 'val/OK/d.png', 'val/NG/e.png', 'test/NG/f.png'];
const DEPENDENCIES = [
  'scripts/e2e/classification-import-invalid-controls.spec.ts', 'scripts/e2e/classification-import-invalid-controls-scoped-media.spec.ts', 'scripts/e2e/fixtures/scoped-gallery-media-witness.cjs', 'scripts/e2e/fixtures/test.ts',
  'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
  'scripts/e2e/fixtures/remaining-project-handoff.ts', 'scripts/e2e/qa/appFlow.ts', 'playwright.config.ts',
  'src/renderer/components/dataset/DatasetStudio.tsx', 'src/renderer/components/dataset/DatasetStatisticsPanel.tsx',
  'src/renderer/stores/useDatasetStore.ts', 'src/renderer/stores/useProjectStore.ts',
  'src/renderer/services/api.ts', 'backend/api/routes_dataset.py', 'backend/api/routes_project.py',
];
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));
const raw7 = (s: BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs].map(String);

test('classification folder import rejects a missing source and restores owning label and partition counts on reload', async ({page, request, renderer, workspace, evidence}) => {
  const pins = Object.fromEntries(DEPENDENCIES.map(file => [file, sha(fs.readFileSync(file))]));
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim();
  expect(head).toMatch(/^[a-f0-9]{40}$/);
  const fd = {opened: 0, closed: 0}, proofs: unknown[] = [], writes: unknown[] = [], callbackFailures: unknown[] = [];
  const save = (label: string, value: unknown) => {
    const file = path.join(workspace.logs, label + '.json');
    fs.writeFileSync(file, JSON.stringify(value, null, 2), {flag: 'wx'}); evidence.addFile(file);
    return {path: file, sha256: sha(fs.readFileSync(file))};
  };
  const file = (name: string) => {
    const before = fs.lstatSync(name, {bigint: true}); expect(before.isSymbolicLink()).toBe(false); expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
    const descriptor = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); fd.opened++;
    try {
      expect(raw7(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw7(before));
      const bytes = fs.readFileSync(descriptor); expect(BigInt(bytes.length)).toBe(before.size);
      expect(raw7(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw7(before)); expect(raw7(fs.lstatSync(name, {bigint: true}))).toEqual(raw7(before));
      return {kind: 'file', raw7: raw7(before), sha256: sha(bytes)};
    } finally {fs.closeSync(descriptor); fd.closed++;}
  };
  const tree = (root: string) => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Record<string, unknown> = {};
    const walk = (directory: string) => {
      const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw7: raw7(before)};
      for (const name of names) {const member = path.join(directory, name), info = fs.lstatSync(member); expect(info.isSymbolicLink()).toBe(false); if (info.isDirectory()) walk(member); else rows[path.relative(root, member)] = file(member);}
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw7(fs.lstatSync(directory, {bigint: true}))).toEqual(raw7(before));
    }; walk(root); return rows;
  };
  const api = async (route: string, body?: unknown) => {
    const started = performance.now(), deadline = started + 10_000;
    const response = await handoffWithin(request.fetch(renderer.origin + route, {method: body === undefined ? 'GET' : 'POST', ...(body === undefined ? {} : {data: body}), timeout: remaining(deadline)}), deadline, 'fixture API');
    const bytes = await handoffWithin(response.body(), deadline, 'fixture full body'); expect(response.status()).toBe(200); expect(bytes.length).toBeLessThanOrEqual(1024 * 1024);
    const value = JSON.parse(bytes.toString('utf8')); proofs.push({kind: 'fixture API', route, request: body ?? null, response_sha256: sha(bytes), response: value, started, deadline, finished: performance.now()}); return value;
  };
  const wire = (route: string, method: string, match: (r: Request, u: URL) => boolean) => {
    let original: Request | undefined;
    let accept!: (r: Response) => void, refuse!: (error: unknown) => void;
    const reply = new Promise<Response>((resolve, reject) => {accept = resolve; refuse = reject;}); void reply.catch(() => undefined);
    const poison = (error: unknown) => {callbackFailures.push(error); refuse(error);};
    const timer = setTimeout(() => poison(Error('Original 10s UI response missing: ' + route)), 10_000);
    const listener = (r: Request) => {try {const u = new URL(r.url()); if (u.origin === renderer.origin && u.pathname === route && r.method() === method) {expect(r.frame()).toBe(page.mainFrame()); if (match(r, u)) {expect(original).toBeUndefined(); original = r;}}} catch (error) {poison(error);}};
    const responded = (r: Response) => {try {if (r.request() === original) accept(r);} catch (error) {poison(error);}};
    page.on('request', listener); page.on('response', responded);
    return {reply, close: () => {clearTimeout(timer); page.off('request', listener); page.off('response', responded);}, request: () => original};
  };
  let project: any, context: any;
  const media = createScopedGalleryMediaWitness(page, {origin: renderer.origin, artifactDir: workspace.logs}); let mediaDeadline = 0;
  const recordMedia = async (deadline: number, expectedPaths: string[], label: string, visibleSource: string = project.source_dataset_dir) => {mediaDeadline = deadline; const proof = await media.assert({deadline, expectedPaths, label, project: project.id, projectName: project.name, source: visibleSource, context, naturalWidth: 32, naturalHeight: 32}); proofs.push({kind: 'original scoped media', ...proof}); evidence.addFile(proof.file); for (const name of proof.files) evidence.addFile(name);};
  const body = async (response: Response, expected: number, deadline: number, label: string, original: Request | undefined) => {
    const own = response.request(); expect(original).toBeDefined(); expect(own).toBe(original); expect(own.frame()).toBe(page.mainFrame()); expect(new URL(response.url()).origin).toBe(renderer.origin); expect(response.status()).toBe(expected);
    expect(await handoffWithin(own.headerValue('x-vision-project'), deadline, 'own project header')).toBe(project.id);
    const rawContext = await handoffWithin(own.headerValue('x-vision-context'), deadline, 'own context header'); expect(rawContext).not.toBeNull(); const actualContext = JSON.parse(rawContext!); expect(actualContext.project_id).toBe(project.id); expect(actualContext.mode).toBe('local');
    if (context) expect(actualContext).toEqual(context); else context = actualContext;
    const raw = await handoffWithin(response.body(), deadline, 'same Request full response'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024); expect(await handoffWithin(response.finished(), deadline, 'same response finished')).toBeNull();
    const actual = JSON.parse(raw.toString('utf8')), rawFile = path.join(workspace.logs, label + '-raw-response.json'); fs.writeFileSync(rawFile, raw, {flag: 'wx'}); evidence.addFile(rawFile);
    proofs.push({kind: 'original UI response', label, method: own.method(), url: response.url(), request_body: own.postDataJSON(), project_id: project.id, context: actualContext, status: expected, response: actual, response_sha256: sha(raw), bytes: raw.length, raw_file: rawFile, same_Request: true, main_frame: true, finished: true, deadline, completed: performance.now()});
    return actual;
  };
  const summary = (value: any) => {expect(value.total_images).toBe(6); expect(value.source_images).toBe(6); expect(value.unlabeled_images).toBe(0); expect(value.classes).toEqual({OK: 3, NG: 3}); expect(value.split).toEqual({train: 3, val: 2, test: 1});};
  const source = path.join(fs.realpathSync(workspace.root), 'owned-classification-source');
  for (const [index, relative] of FILES.entries()) {const name = path.join(source, relative); fs.mkdirSync(path.dirname(name), {recursive: true}); fs.writeFileSync(name, png(32, 3, (x, y) => [(x + index * 7) % 256, (y + index * 9) % 256, 50 + index]), {flag: 'wx'}); evidence.addFile(name);}
  const view = async (deadline: number) => {
    const opts = () => ({timeout: remaining(deadline)});
    await handoffWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name, opts()), deadline, 'own project title');
    await handoffWithin(expect(page.locator('span[title]').filter({hasText: source})).toHaveAttribute('title', source, opts()), deadline, 'own saved source');
    await handoffWithin(expect(page.getByText('학습 가능 이미지', {exact: true}).locator('..').getByText('6', {exact: true})).toBeVisible(opts()), deadline, 'own eligible count');
    for (const [label, count] of [['Train:', 3], ['Val:', 2], ['Test:', 1]] as const) await handoffWithin(expect(page.getByText(label, {exact: true}).locator('..').getByText(String(count), {exact: true})).toBeVisible(opts()), deadline, 'own partition count');
    const panel = page.getByRole('region', {name: '활성 라벨 세트 데이터 통계', exact: true});
    for (const [label, count] of [['전체', 6], ['라벨 완료', 6], ['라벨 미완료', 0]] as const) await handoffWithin(expect(panel.getByRole('button').filter({has: page.getByText(label, {exact: true})}).locator('strong')).toHaveText(String(count), opts()), deadline, 'own label count');
    const images = page.locator('button[aria-label$=" 라벨링에서 열기"] img'); await handoffWithin(expect(images).toHaveCount(6, opts()), deadline, 'own gallery count');
    await recordMedia(deadline, FILES.map(relative => path.join(source, relative)), 'owning-gallery');
    await handoffWithin(expect.poll(() => images.evaluateAll(nodes => nodes.every(node => node instanceof HTMLImageElement && node.complete && node.naturalWidth === 32 && node.naturalHeight === 32)), opts()).toBe(true), deadline, 'all owning gallery images decoded at source dimensions');
  };
  const mutations = (r: Request) => {try {if (new URL(r.url()).origin === renderer.origin && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push({method: r.method(), route: new URL(r.url()).pathname, body: r.postDataJSON()});} catch (error) {callbackFailures.push(error);}};
  let before: unknown, primary: unknown, removed: unknown; const after: Record<string, unknown> = {};
  const snapshot = () => ({source: tree(source), project: tree(project.project_dir)});
  try {
    await installDesktopHostShim(page, renderer.port); project = await api('/api/project/create', {name: 'Owned invalid classification import', task: 'classification'});
    expect(path.dirname(project.project_dir)).toBe(workspace.projects); expect(project.task).toBe('classification');
    await page.goto(renderer.url); await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    await page.evaluate(value => {window.api!.selectFolder = async () => value;}, source);
    const imported = wire('/api/dataset/import', 'POST', r => r.postDataJSON()?.folder_path === source), saved = wire('/api/project/update', 'PUT', r => r.postDataJSON()?.source_dataset_dir === source);
    try {const deadline = performance.now() + 10_000; mediaDeadline = deadline; await media.begin(deadline, 'ordinary-import'); await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'ordinary successful import click'); summary(await body(await handoffWithin(imported.reply, deadline, 'successful actual import'), 200, deadline, 'setup-import', imported.request()));
      const updated = await body(await handoffWithin(saved.reply, deadline, 'successful saved source'), 200, deadline, 'setup-source', saved.request()); expect(updated.id).toBe(project.id); expect(updated.project_dir).toBe(project.project_dir); expect(updated.source_dataset_dir).toBe(source); project = updated;
      await view(deadline);
    } finally {imported.close(); saved.close();}
    await api('/api/evaluation/history?source_dataset_path=' + encodeURIComponent(source) + '&task=classification');
    await view(performance.now() + 10_000); await api('/api/team-data'); await api('/api/team-data/readiness'); await api('/api/training-workspace/tasks');
    const statistics = await api('/api/dataset/metadata/statistics?folder_path=' + encodeURIComponent(source)); expect(statistics.total).toBe(6);
    before = snapshot(); save('immutable-before', before);
    const missing = path.join(fs.realpathSync(workspace.root), 'owned-removed-chooser-answer'); expect(fs.existsSync(missing)).toBe(false); fs.mkdirSync(missing);
    const owned = fs.lstatSync(missing, {bigint: true}); expect(owned.isDirectory()).toBe(true); expect(owned.isSymbolicLink()).toBe(false); expect(fs.readdirSync(missing)).toEqual([]);
    await page.evaluate(() => {const gate = {calls: 0, resolve: null as null | ((value: string) => void)}; (window as any).__ownedMissingFolder = gate; window.api!.selectFolder = async () => {gate.calls++; return new Promise<string>(resolve => {gate.resolve = resolve;});};});
    page.on('request', mutations); const invalid = wire('/api/dataset/import', 'POST', r => r.postDataJSON()?.folder_path === missing); const deadline = performance.now() + 10_000; mediaDeadline = deadline; await media.begin(deadline, 'invalid-selection');
    try {
      await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'ordinary rejected import click');
      await handoffWithin(expect.poll(() => page.evaluate(() => (window as any).__ownedMissingFolder.calls), {timeout: remaining(deadline)}).toBe(1), deadline, 'owned chooser pending');
      expect(raw7(fs.lstatSync(missing, {bigint: true}))).toEqual(raw7(owned)); expect(fs.readdirSync(missing)).toEqual([]); fs.rmdirSync(missing); expect(fs.existsSync(missing)).toBe(false); removed = {path: missing, raw7: raw7(owned), created_by_this_case: true, empty_directory_only: true};
      await handoffWithin(page.evaluate(value => {const gate = (window as any).__ownedMissingFolder; if (typeof gate.resolve !== 'function') throw Error('Owned chooser is not pending'); gate.resolve(value); gate.resolve = null;}, missing), deadline, 'release exact removed folder answer');
      const result = await body(await handoffWithin(invalid.reply, deadline, 'genuine invalid import response'), 404, deadline, 'invalid-import', invalid.request());
      expect(invalid.request()!.postDataJSON()).toEqual({folder_path: missing, task: 'classification', validate_images: true}); expect(result.detail.error_code).toBe('ERR_NO_DATA'); expect(result.detail.details).toBe('Folder not found: ' + missing);
      await handoffWithin(expect(page.getByRole('alert').filter({hasText: '데이터셋을 불러오지 못했습니다'})).toContainText('Folder not found: ' + missing, {timeout: remaining(deadline)}), deadline, 'actual rejected import error');
      await handoffWithin(expect(page.locator('button[aria-label$=" 라벨링에서 열기"] img')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'failed selection has no stale gallery');
      await recordMedia(deadline, [], 'invalid-selection-no-stale-media', missing);
      const invalidStatistics = page.getByRole('region', {name: '활성 라벨 세트 데이터 통계', exact: true});
      await handoffWithin(expect(invalidStatistics).toHaveCount(1, {timeout: remaining(deadline)}), deadline, 'own statistics region remains');
      await handoffWithin(expect(invalidStatistics.locator('strong')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'failed selection has no stale label statistics');
      await handoffWithin(expect(invalidStatistics).toContainText('전체 이미지 —개', {timeout: remaining(deadline)}), deadline, 'invalid selection has no accepted statistics');
    } finally {invalid.close();}
    expect(writes).toEqual([{method: 'POST', route: '/api/dataset/import', body: {folder_path: missing, task: 'classification', validate_images: true}}]);
    expect(await api('/api/project/current')).toEqual(project); after.invalid = snapshot(); expect(after.invalid).toEqual(before); save('immutable-after-invalid', after.invalid); await evidence.screenshot(page, 'actual-missing-folder-refused-no-saved-source-change');
    const restored = wire('/api/dataset/current-summary', 'GET', (_r, u) => u.searchParams.get('folder_path') === source && u.searchParams.get('task') === 'classification');
    try {const reloadDeadline = performance.now() + 10_000; mediaDeadline = reloadDeadline; await media.begin(reloadDeadline, 'owning-reload', {navigation:true}); await handoffWithin(page.reload(), reloadDeadline, 'ordinary reload after rejected source'); summary(await body(await handoffWithin(restored.reply, reloadDeadline, 'saved-source summary after reload'), 200, reloadDeadline, 'reload-summary', restored.request())); await view(reloadDeadline); await media.close(reloadDeadline);} finally {restored.close();}
    expect(await api('/api/project/current')).toEqual(project); expect(await api('/api/dataset/metadata/statistics?folder_path=' + encodeURIComponent(source))).toEqual(statistics);
    expect(writes).toEqual([{method: 'POST', route: '/api/dataset/import', body: {folder_path: missing, task: 'classification', validate_images: true}}]);
    after.reload = snapshot(); expect(after.reload).toEqual(before); save('immutable-after-reload', after.reload); await evidence.screenshot(page, 'owning-source-labels-and-partitions-restored-by-reload');
  } catch (error) {primary = error; throw error;} finally {
    const failures: unknown[] = []; try {await media.close(mediaDeadline);} catch (error) {failures.push(error);} page.off('request', mutations);
    try {expect(callbackFailures).toEqual([]); expect(fd.closed).toBe(fd.opened); expect(Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]))).toEqual(pins); expect(execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim()).toBe(head);} catch (error) {failures.push(error);}
    evidence.note('classification_import_invalid', {cells: CELLS, source: {head, sha256: pins}, actual_renderer_backend: true, controlled_desktop_shim_folder_answer: true, actual_native_picker: false, installed_native: false, source_electron: false, removed_owned_empty_directory: removed, project, expected: {files: FILES, classes: {OK: 3, NG: 3}, split: {train: 3, val: 2, test: 1}}, original_10s_action_and_reload_frames: true, proofs, before, after, writes, descriptors: fd, callback_errors: callbackFailures.map(String), training_or_model_execution: false, human_model_quality_acceptance: false, original_parent_acceptance: false, secondary_errors: failures.map(error => String(error))});
    if (primary instanceof Error && failures.length) Object.defineProperty(primary, 'secondary_errors', {value: failures.map(String)});
    else if (primary === undefined && failures.length) throw failures[0];
  }
});

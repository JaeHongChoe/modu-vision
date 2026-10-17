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
  'scripts/e2e/classification-import-empty-cancel-controls.spec.ts', 'scripts/e2e/fixtures/test.ts',
  'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
  'scripts/e2e/fixtures/remaining-project-handoff.ts', 'scripts/e2e/qa/appFlow.ts', 'playwright.config.ts',
  'src/renderer/components/dataset/DatasetStudio.tsx', 'src/renderer/components/dataset/DatasetStatisticsPanel.tsx',
  'src/renderer/stores/useDatasetStore.ts', 'src/renderer/stores/useProjectStore.ts',
  'src/renderer/services/api.ts', 'backend/api/routes_dataset.py', 'backend/api/routes_project.py',
  'backend/api/routes_dataset_metadata.py', 'backend/engine/dataset_summary.py',
  'backend/engine/dataset_loaders.py', 'backend/engine/industrial_adapters.py',
];
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));
const raw7 = (s: BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs].map(String);
type FileSnapshot = {kind: 'file'; raw7: string[]; sha256: string};
type Tree = Record<string, FileSnapshot | {kind: 'directory'; raw7: string[]}>;
type Snapshot = {source: Tree; empty: Tree; project: Tree};
type Fixtures = {page: Page; request: APIRequestContext; renderer: RendererServer; workspace: Workspace; evidence: Evidence};

async function exercise(mode: 'empty' | 'cancel', {page, request, renderer, workspace: w, evidence: e}: Fixtures) {
  const pins = Object.fromEntries(DEPENDENCIES.map(file => [file, sha(fs.readFileSync(file))]));
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(); expect(head).toMatch(/^[a-f0-9]{40}$/);
  const fd = {opened: 0, closed: 0}, proofs: any[] = [], writes: unknown[] = [], callbackFailures: unknown[] = [];
  const save = (label: string, value: unknown) => {const file = path.join(w.logs, label + '.json'); fs.writeFileSync(file, JSON.stringify(value, null, 2), {flag: 'wx'}); e.addFile(file);};
  const file = (name: string): FileSnapshot => {
    const before = fs.lstatSync(name, {bigint: true}); expect(before.isSymbolicLink()).toBe(false); expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
    const descriptor = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); fd.opened++;
    try {expect(raw7(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw7(before)); const bytes = fs.readFileSync(descriptor); expect(BigInt(bytes.length)).toBe(before.size);
      expect(raw7(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw7(before)); expect(raw7(fs.lstatSync(name, {bigint: true}))).toEqual(raw7(before)); return {kind: 'file', raw7: raw7(before), sha256: sha(bytes)};
    } finally {fs.closeSync(descriptor); fd.closed++;}
  };
  const tree = (root: string): Tree => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Tree = {};
    const walk = (directory: string) => {const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw7: raw7(before)};
      for (const name of names) {const member = path.join(directory, name), info = fs.lstatSync(member); expect(info.isSymbolicLink()).toBe(false); if (info.isDirectory()) walk(member); else rows[path.relative(root, member)] = file(member);}
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw7(fs.lstatSync(directory, {bigint: true}))).toEqual(raw7(before));
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
  const summary = (value: any, count: 0 | 6) => {expect(value.status).toBe('success'); expect(value.total_images).toBe(count); expect(value.source_images).toBe(count); expect(value.unlabeled_images).toBe(0); expect(value.classes).toEqual(count === 6 ? {OK: 3, NG: 3} : {}); expect(value.split).toEqual(count === 6 ? {train: 3, val: 2, test: 1} : {train: 0, val: 0, test: 0});
    expect(value.corrupted_images).toEqual([]); expect(value.validation).toEqual({requested: true, checked_images: count, complete: true, scope: 'all images: every image file under the folder was decoded'}); expect(value.split_supported).toBe(true); expect(value.split_unavailable_reason).toBeNull();
  };
  const source = path.join(fs.realpathSync(w.root), 'owned-classification-source'), empty = path.join(fs.realpathSync(w.root), 'owned-empty-classification-source');
  expect(fs.existsSync(empty)).toBe(false); fs.mkdirSync(empty); expect(fs.readdirSync(empty)).toEqual([]);
  for (const [index, relative] of FILES.entries()) {const name = path.join(source, relative); fs.mkdirSync(path.dirname(name), {recursive: true}); fs.writeFileSync(name, png(32, 3, (x, y) => [(x + index * 7) % 256, (y + index * 9) % 256, 50 + index]), {flag: 'wx'}); e.addFile(name);}
  const view = async (selected: string, count: 0 | 6, deadline: number) => {
    const opts = () => ({timeout: remaining(deadline)}), panel = page.getByRole('region', {name: '활성 라벨 세트 데이터 통계', exact: true});
    await handoffWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name, opts()), deadline, 'own project title');
    await handoffWithin(expect(page.locator('span[title]').filter({hasText: selected})).toHaveAttribute('title', selected, opts()), deadline, 'own saved source');
    await handoffWithin(expect(page.getByText('학습 가능 이미지', {exact: true}).locator('..').getByText(String(count), {exact: true})).toBeVisible(opts()), deadline, 'own eligible count');
    const split = count === 6 ? [['Train:', 3], ['Val:', 2], ['Test:', 1]] as const : [['Train:', 0], ['Val:', 0], ['Test:', 0]] as const;
    for (const [label, value] of split) await handoffWithin(expect(page.getByText(label, {exact: true}).locator('..').getByText(String(value), {exact: true})).toBeVisible(opts()), deadline, 'own partition count');
    for (const [label, value] of [['전체', count], ['라벨 완료', count], ['라벨 미완료', 0]] as const) await handoffWithin(expect(panel.getByRole('button').filter({has: page.getByText(label, {exact: true})}).locator('strong')).toHaveText(String(value), opts()), deadline, 'own label count');
    const images = page.locator('button[aria-label$=" 라벨링에서 열기"] img'); await handoffWithin(expect(images).toHaveCount(count, opts()), deadline, 'own gallery count');
    expect(await handoffWithin(images.evaluateAll(nodes => nodes.map(node => new URL((node as HTMLImageElement).src).searchParams.get('file_path')).sort()), deadline, 'exact gallery source paths')).toEqual(count === 6 ? FILES.map(relative => path.join(source, relative)).sort() : []);
    if (count === 6) await handoffWithin(expect.poll(() => images.evaluateAll(nodes => nodes.every(node => node instanceof HTMLImageElement && node.complete && node.naturalWidth === 32 && node.naturalHeight === 32)), opts()).toBe(true), deadline, 'all owning gallery images decoded at source dimensions');
    else {await handoffWithin(expect(page.getByText('가져온 이미지가 없습니다. 상단에서 폴더를 열거나 합성 데이터를 생성하세요.', {exact: true})).toBeVisible(opts()), deadline, 'accepted empty gallery message'); await handoffWithin(expect(page.getByRole('button', {name: '3-Way 분할 적용', exact: true})).toBeDisabled(opts()), deadline, 'zero-image split remains disabled');}
    await handoffWithin(expect(page.getByRole('alert').filter({hasText: '데이터셋을 불러오지 못했습니다'})).toHaveCount(0, opts()), deadline, 'no import error');
  };
  const choose = async (selected: string, count: 0 | 6, label: string) => {
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
  const statistics = async (selected: string, count: 0 | 6) => {
    const value = await api('/api/dataset/metadata/statistics?folder_path=' + encodeURIComponent(selected)); expect(value.total).toBe(count); expect(value.labelset_id).toBe('default');
    if (count === 0) {expect(value.items).toEqual([]); expect(value.classes).toEqual({}); expect(value.labeling).toEqual({labeled: {count: 0, ratio: 0}, unlabeled: {count: 0, ratio: 0}}); expect(value.assignments).toEqual(Object.fromEntries(['train', 'val', 'test', 'not_used', 'not_split'].map(key => [key, {count: 0, ratio: 0}])));}
    return value;
  };
  const mutations = (r: Request) => {try {if (new URL(r.url()).origin === renderer.origin && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push({method: r.method(), route: new URL(r.url()).pathname, body: r.postDataJSON()});} catch (error) {callbackFailures.push(error);}};
  const snapshot = (): Snapshot => ({source: tree(source), empty: tree(empty), project: tree(project.project_dir)});
  let before: Snapshot | undefined, primary: unknown, baselineProject: any, cancelAcknowledgement: unknown, coldBefore: unknown; const after: Record<string, Snapshot> = {}, coldAfter: Record<string, unknown> = {};
  const originalAssets = () => ({source: tree(source), models: tree(project.models_dir), labelset_registry: file(path.join(project.project_dir, 'labelsets.json'))});
  const expectedWrites = mode === 'empty' ? [{method: 'POST', route: '/api/dataset/import', body: {folder_path: empty, task: 'classification', validate_images: true}}, {method: 'PUT', route: '/api/project/update', body: {source_dataset_dir: empty}}] : [];
  const preserved = (current: Snapshot) => {
    expect(current.source).toEqual(before!.source); expect(current.empty).toEqual(before!.empty); expect(Object.keys(current.project)).toEqual(Object.keys(before!.project));
    for (const relative of Object.keys(before!.project)) {
      if (mode === 'empty' && relative === 'project.json') {expect(JSON.parse(fs.readFileSync(path.join(project.project_dir, relative), 'utf8'))).toEqual({...baselineProject, source_dataset_dir: empty, updated_at: project.updated_at}); expect(project).toEqual({...baselineProject, source_dataset_dir: empty, updated_at: project.updated_at}); expect(current.project[relative].kind).toBe('file');}
      else if (mode === 'empty' && relative === '.') {expect(current.project[relative].kind).toBe('directory'); expect(current.project[relative].raw7.slice(0, 5)).toEqual(before!.project[relative].raw7.slice(0, 5));}
      else expect(current.project[relative]).toEqual(before!.project[relative]);
    }
  };
  try {
    await installDesktopHostShim(page, renderer.port); project = await api('/api/project/create', {name: 'Owned classification ' + mode + ' controls', task: 'classification'}); expect(path.dirname(project.project_dir)).toBe(w.projects); expect(project.task).toBe('classification');
    coldBefore = originalAssets(); save('original-source-model-labelset-before-any-import', coldBefore);
    await page.goto(renderer.url); await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    // Complete genuine source-specific metadata initialization before custody.
    // The measured empty transition is warm; no metadata fixture is fabricated.
    if (mode === 'empty') {await choose(empty, 0, 'prepare-empty'); await warm(empty); await statistics(empty, 0); coldAfter.empty_preparation = originalAssets(); expect(coldAfter.empty_preparation).toEqual(coldBefore); await e.screenshot(page, 'cold-empty-source-zero-label-and-partition-counts');}
    await choose(source, 6, 'prepare-six'); await warm(source); const originalStatistics = await statistics(source, 6);
    await view(source, 6, performance.now() + 10_000); baselineProject = structuredClone(project); before = snapshot(); save('immutable-before', before); page.on('request', mutations);
    if (mode === 'empty') {
      await choose(empty, 0, 'actual-empty'); expect(await api('/api/project/current')).toEqual(project); await statistics(empty, 0);
      after.action = snapshot(); preserved(after.action); expect(writes).toEqual(expectedWrites); save('custody-after-empty', after.action); await e.screenshot(page, 'actual-empty-source-zero-label-and-partition-counts');
    } else {
      await page.evaluate(() => {const gate = {calls: 0, returned: false, resolve: null as null | ((value: null) => void)}; (window as any).__ownedCancelFolder = gate; window.api!.selectFolder = async () => {gate.calls++; const answer = await new Promise<null>(resolve => {gate.resolve = resolve;}); gate.returned = true; return answer;};});
      const deadline = performance.now() + 10_000; await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'ordinary folder chooser click');
      await handoffWithin(expect.poll(() => page.evaluate(() => (window as any).__ownedCancelFolder.calls), {timeout: remaining(deadline)}).toBe(1), deadline, 'own chooser pending');
      await handoffWithin(page.evaluate(() => {const gate = (window as any).__ownedCancelFolder; if (typeof gate.resolve !== 'function' || gate.returned) throw Error('Owned chooser is not pending'); gate.resolve(null); gate.resolve = null;}), deadline, 'release exact null chooser answer');
      await handoffWithin(expect.poll(() => page.evaluate(() => (window as any).__ownedCancelFolder.returned), {timeout: remaining(deadline)}).toBe(true), deadline, 'owned chooser returned null');
      await handoffWithin(page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())))), deadline, 'ordinary click continuation settled');
      await view(source, 6, deadline); expect(writes).toEqual([]); cancelAcknowledgement = {calls: 1, answer: null, returned: true, running_job_cancel: false, deadline, completed: performance.now()};
      expect(await api('/api/project/current')).toEqual(baselineProject); expect(await statistics(source, 6)).toEqual(originalStatistics); after.action = snapshot(); preserved(after.action); save('immutable-after-cancel', after.action); await e.screenshot(page, 'null-folder-selection-keeps-owning-gallery-and-counts');
    }
    const selected = mode === 'empty' ? empty : source, count = mode === 'empty' ? 0 : 6;
    const restored = wire('/api/dataset/current-summary', 'GET', (_r, u) => u.searchParams.get('folder_path') === selected && u.searchParams.get('task') === 'classification');
    try {const deadline = performance.now() + 10_000; await handoffWithin(page.reload(), deadline, 'ordinary owning reload'); summary(await body(await handoffWithin(restored.reply, deadline, 'own saved-source summary'), deadline, 'reload-summary', restored.request()), count); await view(selected, count, deadline);} finally {restored.close();}
    expect(await api('/api/project/current')).toEqual(project); await statistics(selected, count); after.reload = snapshot(); preserved(after.reload); expect(after.reload).toEqual(after.action); expect(writes).toEqual(expectedWrites); coldAfter.final = originalAssets(); expect(coldAfter.final).toEqual(coldBefore); save('original-source-model-labelset-after-reload', coldAfter); save('custody-after-reload', after.reload); await e.screenshot(page, mode + '-source-label-and-partition-counts-after-reload');
  } catch (error) {primary = error; throw error;} finally {
    const failures: unknown[] = []; page.off('request', mutations);
    try {expect(callbackFailures).toEqual([]); expect(fd.closed).toBe(fd.opened); expect(Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]))).toEqual(pins); expect(execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim()).toBe(head);} catch (error) {failures.push(error);}
    e.note('classification_import_' + mode, {cells: ['F019.native-classification-import-label-summary.' + mode, 'F022.native-classification-partitions-import-reopen.' + mode], source: {head, sha256: pins}, actual_renderer_backend: true, controlled_desktop_shim_folder_answer: true, actual_native_picker: false, installed_native: false, source_electron: false, empty_scope: mode === 'empty' ? 'Genuine initial empty import and initialization retained; protected6-to-empty transition uses warmed source-specific metadata namespace' : null, cancelAcknowledgement, project, baselineProject, expected: {original_files: FILES, source, empty, chosen_source: mode === 'empty' ? empty : source}, original_10s_action_and_reload_frames: true, proofs, coldBefore, coldAfter, before, after, writes, descriptors: fd, callback_errors: callbackFailures.map(String), gallery_claim: 'readiness/dimensions/paths only; no full RGB canvas comparison', running_job_cancellation: false, training_or_model_execution: false, human_model_quality_acceptance: false, original_parent_acceptance: false, secondary_errors: failures.map(error => String(error))});
    if (primary instanceof Error && failures.length) Object.defineProperty(primary, 'secondary_errors', {value: failures.map(String)}); else if (primary === undefined && failures.length) throw failures[0];
  }
}

test('classification folder import accepts an empty source with zero label and partition counts after reload', async ({page, request, renderer, workspace, evidence}) => exercise('empty', {page, request, renderer, workspace, evidence}));
test('classification null folder selection preserves owning source label and partition counts after reload', async ({page, request, renderer, workspace, evidence}) => exercise('cancel', {page, request, renderer, workspace, evidence}));

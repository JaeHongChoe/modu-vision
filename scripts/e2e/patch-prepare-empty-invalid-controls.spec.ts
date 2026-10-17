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
const SPEC = 'scripts/e2e/patch-prepare-empty-invalid-controls.spec.ts';
const DEPENDENCIES = [SPEC, 'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs',
  'scripts/e2e/fixtures/desktop-host-shim.ts', 'scripts/e2e/fixtures/remaining-project-handoff.ts',
  'scripts/e2e/qa/appFlow.ts', 'playwright.config.ts',
  'src/renderer/components/training/PatchClassificationWorkbench.tsx',
  'src/renderer/components/training/useProgramWorkbench.ts', 'src/renderer/components/training/ProgramWorkbenchControls.tsx',
  'src/renderer/services/modelTrainingProgram.ts', 'src/renderer/services/datasetWorkflow.ts', 'src/renderer/services/api.ts',
  'backend/api/routes_patch_classification.py', 'backend/engine/patch_preparation.py',
  'backend/engine/grouped_dataset_views.py'];
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
const raw7 = (s: BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs].map(String);
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));
type Tree = Record<string, {kind: 'file' | 'directory'; raw7: string[]; sha256?: string}>;
type Fixtures = {page: Page; request: APIRequestContext; renderer: RendererServer; workspace: Workspace; evidence: Evidence};

async function exercise(mode: 'empty' | 'invalid', {page, request, renderer, workspace: w, evidence: e}: Fixtures) {
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim();
  const pins = Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]));
  const descriptors = {opened: 0, closed: 0}, proofs: unknown[] = [], writes: unknown[] = [], callbackErrors: unknown[] = [];
  const save = (name: string, value: unknown) => {const file = path.join(w.logs, name + '.json'); fs.writeFileSync(file, JSON.stringify(value, null, 2), {flag: 'wx'}); e.addFile(file);};
  const tree = (root: string): Tree => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Tree = {};
    const visit = (directory: string) => {
      const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw7: raw7(before)};
      for (const name of names) {
        const file = path.join(directory, name), info = fs.lstatSync(file, {bigint: true}); expect(info.isSymbolicLink()).toBe(false);
        if (info.isDirectory()) {visit(file); continue;}
        expect(info.isFile()).toBe(true); expect(info.nlink).toBe(1n);
        const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); descriptors.opened++;
        try {expect(raw7(fs.fstatSync(fd, {bigint: true}))).toEqual(raw7(info)); const bytes = fs.readFileSync(fd);
          expect(BigInt(bytes.length)).toBe(info.size); expect(raw7(fs.fstatSync(fd, {bigint: true}))).toEqual(raw7(info));
          expect(raw7(fs.lstatSync(file, {bigint: true}))).toEqual(raw7(info)); rows[path.relative(root, file)] = {kind: 'file', raw7: raw7(info), sha256: sha(bytes)};
        } finally {fs.closeSync(fd); descriptors.closed++;}
      }
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw7(fs.lstatSync(directory, {bigint: true}))).toEqual(raw7(before));
    }; visit(root); return rows;
  };
  const fixtureApi = async (route: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') => {
    const deadline = performance.now() + 10_000;
    const response = await handoffWithin(request.fetch(renderer.origin + route, {method, ...(body === undefined ? {} : {data: body}), timeout: remaining(deadline)}), deadline, 'original fixture API');
    const raw = await handoffWithin(response.body(), deadline, 'original fixture full body'); expect(response.status()).toBe(200); expect(raw.length).toBeLessThanOrEqual(1024 * 1024);
    const value = JSON.parse(raw.toString('utf8')); proofs.push({kind: 'fixture API', route, method, body: body ?? null, response: value, sha256: sha(raw), deadline, completed: performance.now()}); return value;
  };
  let project: any, acceptedContext: unknown;
  const wire = (route: string, method: string, match: (r: Request, u: URL) => boolean) => {
    let original: Request | undefined, accept!: (r: Response) => void, refuse!: (e: unknown) => void;
    const reply = new Promise<Response>((resolve, reject) => {accept = resolve; refuse = reject;}); void reply.catch(() => undefined);
    const failed = (error: unknown) => {callbackErrors.push(error); refuse(error);};
    const timer = setTimeout(() => failed(Error('Original10s response missing: ' + route)), 10_000);
    const outgoing = (r: Request) => {try {const u = new URL(r.url()); if (u.origin === renderer.origin && u.pathname === route && r.method() === method && match(r, u)) {expect(original).toBeUndefined(); expect(r.frame()).toBe(page.mainFrame()); original = r;}} catch (error) {failed(error);}};
    const incoming = (r: Response) => {try {if (r.request() === original) accept(r);} catch (error) {failed(error);}};
    page.on('request', outgoing); page.on('response', incoming);
    return {reply, original: () => original, close: () => {clearTimeout(timer); page.off('request', outgoing); page.off('response', incoming);}};
  };
  const actualBody = async (reply: Response, original: Request | undefined, deadline: number, label: string, status: number) => {
    expect(original).toBeDefined(); expect(reply.request()).toBe(original); expect(original!.frame()).toBe(page.mainFrame()); expect(new URL(reply.url()).origin).toBe(renderer.origin); expect(reply.status()).toBe(status);
    expect(await handoffWithin(original!.headerValue('x-vision-project'), deadline, 'owning project header')).toBe(project.id);
    const rawContext = await handoffWithin(original!.headerValue('x-vision-context'), deadline, 'owning full request context'); expect(rawContext).not.toBeNull();
    const context = JSON.parse(rawContext!); expect(context.project_id).toBe(project.id); expect(context.mode).toBe('local');
    if (acceptedContext) expect(context).toEqual(acceptedContext); else {expect(status).toBe(200); acceptedContext = context;}
    const raw = await handoffWithin(reply.body(), deadline, 'same Request full body'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024); expect(await handoffWithin(reply.finished(), deadline, 'same response finished')).toBeNull();
    const file = path.join(w.logs, label + '-raw-response.json'); fs.writeFileSync(file, raw, {flag: 'wx'}); e.addFile(file);
    const value = JSON.parse(raw.toString('utf8')); proofs.push({kind: 'original UI response', label, method: original!.method(), path: new URL(reply.url()).pathname, request_body: original!.postDataJSON(), status, context, response: value, bytes: raw.length, sha256: sha(raw), raw_file: file, same_Request: true, main_frame: true, finished: true, deadline, completed: performance.now()}); return value;
  };
  const source = path.join(fs.realpathSync(w.root), 'patch-original-region-source'), empty = path.join(fs.realpathSync(w.root), 'patch-empty-source');
  fs.mkdirSync(source); fs.mkdirSync(empty); expect(fs.readdirSync(empty)).toEqual([]);
  for (let i = 0; i < 3; i++) {
    const file = path.join(source, `part-${i}.png`), label = file.replace(/\.png$/, '.json');
    fs.writeFileSync(file, png(32, 3, (x, y) => [x + i * 5, y, 120]), {flag: 'wx'});
    fs.writeFileSync(label, JSON.stringify({imagePath: path.basename(file), imageWidth: 32, imageHeight: 32, shapes: [{label: 'chip', shape_type: 'rectangle', points: [[0, 0], [8, 8]]}]}), {flag: 'wx'}); e.addFile(file); e.addFile(label);
  }
  const originalSource = tree(source), selected = mode === 'empty' ? empty : source;
  const selectSource = async () => {
    await page.evaluate(value => {window.api!.selectFolder = async () => value;}, selected);
    const imported = wire('/api/dataset/import', 'POST', r => r.postDataJSON()?.folder_path === selected), updated = wire('/api/project/update', 'PUT', r => r.postDataJSON()?.source_dataset_dir === selected);
    try {
      const deadline = performance.now() + 10_000; await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'original source button');
      const value = await actualBody(await handoffWithin(imported.reply, deadline, 'original import response'), imported.original(), deadline, 'owning-source-import', 200);
      expect(imported.original()!.postDataJSON()).toEqual({folder_path: selected, task: 'segmentation', validate_images: true}); expect(value.status).toBe('success'); expect(value.total_images).toBe(mode === 'empty' ? 0 : 3); expect(value.corrupted_images).toEqual([]);
      const saved = await actualBody(await handoffWithin(updated.reply, deadline, 'original saved source response'), updated.original(), deadline, 'owning-source-persist', 200);
      expect(updated.original()!.postDataJSON()).toEqual({source_dataset_dir: selected}); expect(saved).toEqual({...project, source_dataset_dir: selected, updated_at: saved.updated_at}); project = saved;
      expect(await fixtureApi('/api/project/current')).toEqual(project); expect(JSON.parse(fs.readFileSync(path.join(project.project_dir, 'project.json'), 'utf8'))).toEqual(project);
    } finally {imported.close(); updated.close();}
  };
  const enterPatch = async (deadline: number) => {
    const opts = () => ({timeout: remaining(deadline)});
    await handoffWithin(page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(2).click(), deadline, 'ordinary model stage');
    await handoffWithin(page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /^패치 분류/}).click(), deadline, 'ordinary patch tab');
    await handoffWithin(expect(page.getByRole('heading', {name: '고해상도 패치 분류', exact: true})).toBeVisible(opts()), deadline, 'actual patch workbench');
    const prepared = page.getByRole('combobox', {name: '프로젝트에 저장된 패치 정답', exact: true});
    await handoffWithin(expect(prepared.locator('option')).toHaveCount(1, opts()), deadline, 'no prepared dataset option');
    await handoffWithin(expect(prepared).toHaveValue('', opts()), deadline, 'no prepared dataset selected');
    await handoffWithin(expect(page.getByRole('button', {name: '패치 분류 후보 학습', exact: true})).toBeDisabled(opts()), deadline, 'training unavailable without prepared data');
  };
  const observeWrites = (r: Request) => {try {if (new URL(r.url()).origin === renderer.origin && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push({method: r.method(), path: new URL(r.url()).pathname, body: r.postDataJSON()});} catch (error) {callbackErrors.push(error);}};
  const snapshot = () => ({source: tree(source), empty: tree(empty), project: tree(project.project_dir)});
  let before: ReturnType<typeof snapshot> | undefined, after: ReturnType<typeof snapshot> | undefined, reopened: ReturnType<typeof snapshot> | undefined, primary: unknown;
  try {
    await installDesktopHostShim(page, renderer.port); project = await fixtureApi('/api/project/create', {name: 'Owned patch prepare ' + mode, task: 'segmentation'});
    expect(path.dirname(project.project_dir)).toBe(w.projects); await page.goto(renderer.url); await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click(); await selectSource();
    // Genuine lazy read initialization is completed before measured custody.
    for (const route of ['/api/evaluation/history?source_dataset_path=' + encodeURIComponent(selected) + '&task=segmentation', '/api/team-data', '/api/team-data/readiness', '/api/training-workspace/tasks']) await fixtureApi(route);
    await enterPatch(performance.now() + 10_000); expect(await fixtureApi('/api/patch-classification/datasets')).toEqual({datasets: []});
    expect((await fixtureApi('/api/training/jobs')).jobs).toEqual([]); expect((await fixtureApi('/api/evaluation/model-comparisons/models?source_dataset_path=' + encodeURIComponent(selected) + '&task=patch_classification')).models).toEqual([]);
    const size = page.getByLabel('원본 패치 크기', {exact: true}), stride = page.getByLabel('패치 간격', {exact: true});
    await size.fill(mode === 'invalid' ? '15' : '16'); await stride.fill(mode === 'invalid' ? '1' : '16');
    await expect(size).toHaveValue(mode === 'invalid' ? '15' : '16'); await expect(stride).toHaveValue(mode === 'invalid' ? '1' : '16');
    const originalValidity = await size.evaluate(input => ({value: (input as HTMLInputElement).value, min: (input as HTMLInputElement).min, rangeUnderflow: (input as HTMLInputElement).validity.rangeUnderflow}));
    expect(originalValidity).toEqual({value: mode === 'invalid' ? '15' : '16', min: '16', rangeUnderflow: mode === 'invalid'}); proofs.push({kind: 'read-only original input validity', originalValidity});
    const prepare = page.getByRole('button', {name: '영역 라벨에서 패치 준비', exact: true}); await expect(prepare).toBeEnabled();
    before = snapshot(); expect(before.source).toEqual(originalSource); expect(fs.existsSync(path.join(project.dataset_dir, 'patch'))).toBe(false); save('custody-before-prepare', before); page.on('request', observeWrites);
    const captured = wire('/api/patch-classification/prepare', 'POST', () => true);
    const expectedRequest = {patch_size: mode === 'invalid' ? 15 : 16, stride: mode === 'invalid' ? 1 : 16, normal_class: 'OK', minimum_overlap: .05};
    const expectedResponse = mode === 'empty' ? {detail: 'Patch preparation needs annotated source images; unlabeled images cannot establish normal truth'} : {detail: [{type: 'greater_than_equal', loc: ['body', 'patch_size'], msg: 'Input should be greater than or equal to 16', input: 15, ctx: {ge: 16}}]};
    try {
      const deadline = performance.now() + 10_000; await handoffWithin(prepare.click(), deadline, 'ordinary prepare click');
      const body = await actualBody(await handoffWithin(captured.reply, deadline, 'own rejected prepare response'), captured.original(), deadline, 'actual-' + mode + '-prepare', 422);
      expect(captured.original()!.postDataJSON()).toEqual(expectedRequest); expect(body).toEqual(expectedResponse);
      // Reuse the product workflow formatter's original field-validation message.
      const expectedAlert = mode === 'empty' ? expectedResponse.detail as string : 'patch_size: Input should be greater than or equal to 16';
      await handoffWithin(expect(page.getByRole('alert')).toHaveText(expectedAlert, {timeout: remaining(deadline)}), deadline, 'actual prepare refusal alert');
      await handoffWithin(expect(prepare).toBeEnabled({timeout: remaining(deadline)}), deadline, 'prepare returned from original action');
    } finally {captured.close();}
    expect(writes).toEqual([{method: 'POST', path: '/api/patch-classification/prepare', body: expectedRequest}]);
    expect(await fixtureApi('/api/patch-classification/datasets')).toEqual({datasets: []}); expect(await fixtureApi('/api/project/current')).toEqual(project);
    after = snapshot(); expect(after).toEqual(before); expect(fs.existsSync(path.join(project.dataset_dir, 'patch'))).toBe(false); save('custody-after-prepare', after);
    await e.screenshot(page, 'actual-patch-prepare-' + mode + '-422-no-prepared-data');
    const reloadDeadline = performance.now() + 10_000; await handoffWithin(page.reload(), reloadDeadline, 'ordinary owning reload'); await enterPatch(reloadDeadline);
    await handoffWithin(expect(page.getByRole('alert')).toHaveCount(0, {timeout: remaining(reloadDeadline)}), reloadDeadline, 'ordinary reload clears prior prepare error');
    expect(await fixtureApi('/api/patch-classification/datasets')).toEqual({datasets: []}); expect(await fixtureApi('/api/project/current')).toEqual(project); expect((await fixtureApi('/api/training/jobs')).jobs).toEqual([]);
    reopened = snapshot(); expect(reopened).toEqual(before); expect(writes).toEqual([{method: 'POST', path: '/api/patch-classification/prepare', body: expectedRequest}]); expect(fs.existsSync(path.join(project.dataset_dir, 'patch'))).toBe(false); save('custody-after-reload', reopened);
    await e.screenshot(page, 'ordinary-reload-patch-' + mode + '-still-no-prepared-data');
  } catch (error) {primary = error; throw error;} finally {
    page.off('request', observeWrites); const secondary: unknown[] = [];
    try {expect(callbackErrors).toEqual([]); expect(descriptors.closed).toBe(descriptors.opened); expect(tree(source)).toEqual(originalSource); expect(Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]))).toEqual(pins); expect(execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim()).toBe(head);} catch (error) {secondary.push(error);}
    e.note('patch_prepare_' + mode, {cell: 'F026.dino-patch-native-prepare.' + mode, source: {head, sha256: pins}, actual_renderer_backend: true, original_10s_actions_full_bodies: true, fixture_source_adoption_before_custody: true, controlled_folder_answer_only: true, actual_native_picker: false, project, selected, originalSource, before, after, reopened, proofs, writes, descriptors, callback_errors: callbackErrors.map(String), secondary_errors: secondary.map(String), training: false, model_execution: false, GPU: false, native: false, quality_approval: false, parent_acceptance: false});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary.map(String)}); else if (primary === undefined && secondary.length) throw secondary[0];
  }
}

test('patch preparation rejects a persisted empty source without generating prepared data or training', async ({page, request, renderer, workspace, evidence}) => exercise('empty', {page, request, renderer, workspace, evidence}));
test('patch preparation forwards an invalid original size15 and rejects it without changing source or prepared data', async ({page, request, renderer, workspace, evidence}) => exercise('invalid', {page, request, renderer, workspace, evidence}));

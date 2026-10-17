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
const SPEC = 'scripts/e2e/patch-prepare-reopen-error-controls.spec.ts';
const DEPENDENCIES = [SPEC, 'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs',
  'scripts/e2e/fixtures/desktop-host-shim.ts', 'scripts/e2e/fixtures/remaining-project-handoff.ts',
  'scripts/e2e/qa/appFlow.ts', 'playwright.config.ts',
  'src/renderer/components/training/PatchClassificationWorkbench.tsx',
  'src/renderer/components/training/useProgramWorkbench.ts', 'src/renderer/components/training/ProgramWorkbenchControls.tsx',
  'src/renderer/services/modelTrainingProgram.ts', 'src/renderer/services/datasetWorkflow.ts', 'src/renderer/services/api.ts',
  'backend/api/routes_patch_classification.py', 'backend/engine/patch_preparation.py',
  'backend/engine/grouped_dataset_views.py', 'backend/engine/patch_classification.py',
  'src/renderer/components/training/PatchRecipePreview.tsx'];
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
const raw7 = (s: BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs].map(String);
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));
type Tree = Record<string, {kind: 'file' | 'directory'; raw7: string[]; sha256?: string}>;
type Fixtures = {page: Page; request: APIRequestContext; renderer: RendererServer; workspace: Workspace; evidence: Evidence};

async function exercise(mode: 'reopen' | 'error', {page, request, renderer, workspace: w, evidence: e}: Fixtures) {
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
  const source = path.join(fs.realpathSync(w.root), 'patch-original-region-source');
  fs.mkdirSync(source);
  for (let i = 0; i < 3; i++) {
    const file = path.join(source, `part-${i}.png`), label = file.replace(/\.png$/, '.json');
    fs.writeFileSync(file, png(32, 3, (x, y) => [x + i * 5, y, 120]), {flag: 'wx'});
    fs.writeFileSync(label, JSON.stringify({imagePath: path.basename(file), imageWidth: 32, imageHeight: 32, shapes: [{label: 'chip', shape_type: 'rectangle', points: [[0, 0], [8, 8]]}]}), {flag: 'wx'}); e.addFile(file); e.addFile(label);
  }
  const originalSource = tree(source);
  const selectSource = async () => {
    await page.evaluate(value => {window.api!.selectFolder = async () => value;}, source);
    const imported = wire('/api/dataset/import', 'POST', r => r.postDataJSON()?.folder_path === source), updated = wire('/api/project/update', 'PUT', r => r.postDataJSON()?.source_dataset_dir === source);
    try {
      const deadline = performance.now() + 10_000; await handoffWithin(page.getByRole('button', {name: '데이터셋 폴더 열기', exact: true}).click(), deadline, 'original source button');
      const value = await actualBody(await handoffWithin(imported.reply, deadline, 'original import response'), imported.original(), deadline, 'owning-source-import', 200);
      expect(imported.original()!.postDataJSON()).toEqual({folder_path: source, task: 'segmentation', validate_images: true}); expect(value.status).toBe('success'); expect(value.total_images).toBe(3); expect(value.corrupted_images).toEqual([]);
      const saved = await actualBody(await handoffWithin(updated.reply, deadline, 'original saved source response'), updated.original(), deadline, 'owning-source-persist', 200);
      expect(updated.original()!.postDataJSON()).toEqual({source_dataset_dir: source}); expect(saved).toEqual({...project, source_dataset_dir: source, updated_at: saved.updated_at}); project = saved;
      expect(await fixtureApi('/api/project/current')).toEqual(project); expect(JSON.parse(fs.readFileSync(path.join(project.project_dir, 'project.json'), 'utf8'))).toEqual(project);
    } finally {imported.close(); updated.close();}
  };
  const datasetSelector = () => page.getByRole('combobox', {name: '프로젝트에 저장된 패치 정답', exact: true});
  const enterPatch = async (deadline: number, selectedPath = '') => {
    const opts = () => ({timeout: remaining(deadline)});
    await handoffWithin(page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(2).click(), deadline, 'ordinary model stage');
    await handoffWithin(page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /^패치 분류/}).click(), deadline, 'ordinary patch tab');
    await handoffWithin(expect(page.getByRole('heading', {name: '고해상도 패치 분류', exact: true})).toBeVisible(opts()), deadline, 'actual patch workbench');
    await handoffWithin(expect(datasetSelector().locator('option')).toHaveCount(selectedPath ? 2 : 1, opts()), deadline, 'exact prepared option count');
    await handoffWithin(expect(datasetSelector()).toHaveValue(selectedPath, opts()), deadline, 'exact owning prepared selection');
    const train = expect(page.getByRole('button', {name: '패치 분류 후보 학습', exact: true}));
    if (selectedPath) await handoffWithin(train.toBeEnabled(opts()), deadline, 'prepared data available without starting training');
    else await handoffWithin(train.toBeDisabled(opts()), deadline, 'training unavailable without prepared data');
  };
  const previewReady = async (deadline: number, sample: any) => {
    const panel = page.getByRole('region', {name: '원본 패치 정답·판정 미리보기'}), image = panel.getByAltText('검수 정답 패치', {exact: true});
    await handoffWithin(expect(panel.getByRole('img', {name: '패치 정답의 원본 위치', exact: true})).toHaveAttribute('viewBox', '0 0 32 32', {timeout: remaining(deadline)}), deadline, 'owning source dimensions');
    await handoffWithin(expect(image).toBeVisible({timeout: remaining(deadline)}), deadline, 'owning patch preview');
    await handoffWithin(expect.poll(() => image.evaluate(node => ({complete: (node as HTMLImageElement).complete, width: (node as HTMLImageElement).naturalWidth, height: (node as HTMLImageElement).naturalHeight})), {timeout: remaining(deadline)}).toEqual({complete: true, width: 16, height: 16}), deadline, 'patch preview decoded at source crop dimensions');
    await handoffWithin(expect(panel).toContainText(sample.source_relative_path, {timeout: remaining(deadline)}), deadline, 'same owning source path');
    await handoffWithin(expect(panel).toContainText('원본 SHA-256 ' + sample.source_sha256, {timeout: remaining(deadline)}), deadline, 'same owning source hash');
  };
  const observeWrites = (r: Request) => {try {if (new URL(r.url()).origin === renderer.origin && !['GET', 'HEAD', 'OPTIONS'].includes(r.method())) writes.push({method: r.method(), path: new URL(r.url()).pathname, body: r.postDataJSON()});} catch (error) {callbackErrors.push(error);}};
  const snapshot = () => ({source: tree(source), project: tree(project.project_dir)});
  let before: ReturnType<typeof snapshot> | undefined, after: ReturnType<typeof snapshot> | undefined, reopened: ReturnType<typeof snapshot> | undefined, primary: unknown;
  let prepared: any, baselineManifest: any, baselineDatasets: any, baselineSample: any, errorFixture: unknown;
  try {
    await installDesktopHostShim(page, renderer.port); project = await fixtureApi('/api/project/create', {name: 'Owned patch prepare ' + mode, task: 'segmentation'});
    expect(path.dirname(project.project_dir)).toBe(w.projects); await page.goto(renderer.url); await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click(); await selectSource();
    // Complete genuine lazy read initialization before the measured action.
    for (const route of ['/api/evaluation/history?source_dataset_path=' + encodeURIComponent(source) + '&task=segmentation', '/api/team-data', '/api/team-data/readiness', '/api/training-workspace/tasks']) await fixtureApi(route);
    await enterPatch(performance.now() + 10_000); expect(await fixtureApi('/api/patch-classification/datasets')).toEqual({datasets: []});
    expect((await fixtureApi('/api/training/jobs')).jobs).toEqual([]); expect((await fixtureApi('/api/evaluation/model-comparisons/models?source_dataset_path=' + encodeURIComponent(source) + '&task=patch_classification')).models).toEqual([]);
    const patchRoot = path.join(project.dataset_dir, 'patch'); expect(fs.realpathSync(project.dataset_dir)).toBe(project.dataset_dir); expect(fs.existsSync(patchRoot)).toBe(false);
    if (mode === 'error') {
      // This NEW fixture's ordinary regular file obstructs the real staging
      // directory creation. No product hook, fake response, chmod or deletion.
      const bytes = Buffer.from('owned F026 preparation storage obstruction\n', 'utf8');
      const fd = fs.openSync(patchRoot, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600); descriptors.opened++;
      try {fs.writeFileSync(fd, bytes); expect(fs.fstatSync(fd).isFile()).toBe(true);} finally {fs.closeSync(fd); descriptors.closed++;}
      errorFixture = {path: patchRoot, regular_file: true, sha256: sha(bytes), created_before_custody: true, original_or_shared_file_modified: false};
    }
    await page.getByLabel('원본 패치 크기', {exact: true}).fill('16'); await page.getByLabel('패치 간격', {exact: true}).fill('16');
    await expect(page.getByLabel('원본 패치 크기', {exact: true})).toHaveValue('16'); await expect(page.getByLabel('패치 간격', {exact: true})).toHaveValue('16');
    const prepare = page.getByRole('button', {name: '영역 라벨에서 패치 준비', exact: true}); await expect(prepare).toBeEnabled();
    before = snapshot(); expect(before.source).toEqual(originalSource); save('custody-before-prepare', before); page.on('request', observeWrites);
    const captured = wire('/api/patch-classification/prepare', 'POST', () => true), expectedRequest = {patch_size: 16, stride: 16, normal_class: 'OK', minimum_overlap: .05};
    try {
      const deadline = performance.now() + 10_000; await handoffWithin(prepare.click(), deadline, 'ordinary prepare click');
      const body = await actualBody(await handoffWithin(captured.reply, deadline, 'owning prepare response'), captured.original(), deadline, 'actual-' + mode + '-prepare', mode === 'error' ? 422 : 200);
      expect(captured.original()!.postDataJSON()).toEqual(expectedRequest);
      if (mode === 'error') {
        // Python's original POSIX NotADirectoryError includes a freshly created
        // UUID. Validate the ENTIRE response; only that original UUID varies.
        const prefix = `[Errno 20] Not a directory: '${patchRoot}/.patch-preparation-`;
        expect(typeof body.detail).toBe('string'); expect(body.detail.startsWith(prefix)).toBe(true); expect(body.detail.endsWith("'")).toBe(true);
        const uuid = body.detail.slice(prefix.length, -1); expect(uuid).toMatch(/^[0-9a-f]{32}$/); expect(body).toEqual({detail: prefix + uuid + "'"});
        await handoffWithin(expect(page.getByRole('alert')).toHaveText(body.detail, {timeout: remaining(deadline)}), deadline, 'actual original filesystem error');
        await handoffWithin(page.getByRole('alert').scrollIntoViewIfNeeded(), deadline, 'original filesystem error visible for screenshot');
      } else {
        prepared = body; expect(path.dirname(prepared.dataset_path)).toBe(patchRoot); expect(path.basename(prepared.dataset_path)).toMatch(/^[0-9a-f]{32}$/);
        const manifestRaw = fs.readFileSync(path.join(prepared.dataset_path, 'patches.json'));
        const sourceHashes = Object.fromEntries([0, 1, 2].map(i => [`images/part-${i}.png`, originalSource[`part-${i}.png`].sha256!]));
        const sourceMap = Object.fromEntries(Object.entries(sourceHashes).map(([image, source_sha256]) => [image, {source_relative_path: path.basename(image), source_sha256}]));
        const digest = createHash('sha256').update('patch-classification-dataset-v1\0').update(sha(manifestRaw));
        for (const [image, hash] of Object.entries(sourceHashes).sort(([a], [b]) => a.localeCompare(b))) digest.update('\0').update(image).update('\0').update(hash);
        const provenance = {dataset_sha256: 'sha256:' + digest.digest('hex'), manifest_sha256: sha(manifestRaw), source_sha256: sourceHashes,
          split_counts: {train: 4, val: 4, test: 4}, source_image_count: 3, patch_count: 12, source_dataset_path: source, source_map: sourceMap};
        expect(prepared).toEqual({dataset_path: prepared.dataset_path, classes: ['OK', 'chip'], patch_count: 12,
          normal_class: 'OK', patch_size: 16, stride: 16, provenance, split_counts: {train: 4, val: 4, test: 4}, warnings: [], source_dataset_path: source});
        await handoffWithin(expect(datasetSelector()).toHaveValue(prepared.dataset_path, {timeout: remaining(deadline)}), deadline, 'original success selects prepared dataset');
        await handoffWithin(expect(datasetSelector().locator('option:checked')).toHaveText('준비 1 · 패치 12개 · 16px / 간격 16px', {timeout: remaining(deadline)}), deadline, 'original prepare parameters visible before reload');
        await handoffWithin(expect(page.getByText('클래스 OK · chip · 원본 크기 16px · train/val/test 이미지 단위 분할', {exact: true})).toBeVisible({timeout: remaining(deadline)}), deadline, 'original prepare size caption visible before reload');
        await handoffWithin(expect(page.getByRole('status').filter({hasText: '정답 패치 12개를 프로젝트에 준비했습니다.'})).toBeVisible({timeout: remaining(deadline)}), deadline, 'actual prepare success status');
      }
      await handoffWithin(expect(prepare).toBeEnabled({timeout: remaining(deadline)}), deadline, 'prepare action completed');
    } finally {captured.close();}
    expect(writes).toEqual([{method: 'POST', path: '/api/patch-classification/prepare', body: expectedRequest}]);
    if (mode === 'reopen') {
      const manifestFile = path.join(prepared.dataset_path, 'patches.json'), raw = fs.readFileSync(manifestFile), manifest = JSON.parse(raw.toString('utf8'));
      expect(Object.keys(manifest).sort()).toEqual(['version', 'classes', 'normal_class', 'patch_size', 'stride', 'patches', 'source_dataset_path', 'source_map', 'test_image_verdicts', 'review_warnings', 'label_semantics', 'minimum_overlap'].sort());
      expect(manifest).toMatchObject({version: 1, classes: ['OK', 'chip'], normal_class: 'OK', patch_size: 16, stride: 16, source_dataset_path: source, minimum_overlap: .05, review_warnings: [], label_semantics: 'background outside supplied complete region annotations'});
      const sourceHashes = Object.fromEntries([0, 1, 2].map(i => [`images/part-${i}.png`, originalSource[`part-${i}.png`].sha256!]));
      const orderedHashes = Object.values(sourceHashes).sort(); expect(new Set(orderedHashes).size).toBe(3);
      const splitFor = (hash: string) => ['train', 'val', 'test'][orderedHashes.indexOf(hash)];
      const expectedRows = Object.entries(sourceHashes).flatMap(([image, source_sha256]) => [[0, 0, 16, 16], [16, 0, 32, 16], [0, 16, 16, 32], [16, 16, 32, 32]].map(box => ({image, box, label: box[0] === 0 && box[1] === 0 ? 'chip' : 'OK', split: splitFor(source_sha256), source_sha256})));
      const sortedRows = (rows: any[]) => rows.map(row => JSON.stringify(row)).sort(); expect(sortedRows(manifest.patches)).toEqual(sortedRows(expectedRows));
      const sourceMap = Object.fromEntries(Object.entries(sourceHashes).map(([image, source_sha256]) => [image, {source_relative_path: path.basename(image), source_sha256}]));
      expect(manifest.source_map).toEqual(sourceMap); expect(manifest.test_image_verdicts).toEqual(Object.fromEntries(Object.entries(sourceHashes).filter(([, hash]) => splitFor(hash) === 'test').map(([image]) => [image, 'NG'])));
      for (const [image, hash] of Object.entries(sourceHashes)) expect(sha(fs.readFileSync(path.join(prepared.dataset_path, image)))).toBe(hash);
      const sourceManifest = JSON.parse(fs.readFileSync(path.join(prepared.dataset_path, 'source_manifest.json'), 'utf8'));
      expect(sourceManifest).toEqual(Object.entries(sourceMap).map(([image, row]) => ({image: path.join(prepared.dataset_path, image), source_image: path.join(source, row.source_relative_path), source_sha256: row.source_sha256})));
      const digest = createHash('sha256').update('patch-classification-dataset-v1\0').update(sha(raw));
      for (const [image, hash] of Object.entries(sourceHashes).sort(([a], [b]) => a.localeCompare(b))) digest.update('\0').update(image).update('\0').update(hash);
      baselineManifest = await fixtureApi('/api/patch-classification/manifest?dataset_path=' + encodeURIComponent(prepared.dataset_path));
      expect(baselineManifest).toEqual({dataset_path: prepared.dataset_path, classes: ['OK', 'chip'], normal_class: 'OK', patch_size: 16, stride: 16, patch_count: 12, provenance: {dataset_sha256: 'sha256:' + digest.digest('hex'), manifest_sha256: sha(raw), source_sha256: sourceHashes, split_counts: {train: 4, val: 4, test: 4}, source_image_count: 3, patch_count: 12, source_dataset_path: source, source_map: sourceMap}});
      baselineDatasets = await fixtureApi('/api/patch-classification/datasets'); expect(baselineDatasets).toEqual({datasets: [baselineManifest]});
      baselineSample = await fixtureApi('/api/patch-classification/sample?dataset_path=' + encodeURIComponent(prepared.dataset_path) + '&sample_index=0');
      expect(baselineSample).toEqual({sample_index: 0, sample_count: 12, image: manifest.patches[0].image, source_relative_path: path.basename(manifest.patches[0].image), source_sha256: manifest.patches[0].source_sha256, box: manifest.patches[0].box, label: manifest.patches[0].label, split: manifest.patches[0].split, source_size: [32, 32], patch_size: 16, stride: 16, preview_only: true, original_base64: baselineSample.original_base64, patch_base64: baselineSample.patch_base64});
      expect(Buffer.from(baselineSample.original_base64, 'base64').subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])); expect(Buffer.from(baselineSample.patch_base64, 'base64').subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
      await previewReady(performance.now() + 10_000, baselineSample);
      expect(fs.readdirSync(patchRoot)).toEqual([path.basename(prepared.dataset_path)]);
      e.addFile(manifestFile); e.addFile(path.join(prepared.dataset_path, 'source_manifest.json'));
    } else {
      expect(await fixtureApi('/api/patch-classification/datasets')).toEqual({datasets: []}); expect(fs.lstatSync(patchRoot).isFile()).toBe(true);
    }
    after = snapshot(); expect(after.source).toEqual(before.source);
    if (mode === 'error') expect(after).toEqual(before);
    else {
      const datasetRelative = path.relative(project.project_dir, project.dataset_dir), patchRelative = path.relative(project.project_dir, patchRoot);
      for (const [relative, row] of Object.entries(before.project)) {
        if (relative === datasetRelative) {expect(after.project[relative].kind).toBe('directory'); expect(after.project[relative].raw7.slice(0, 3)).toEqual(row.raw7.slice(0, 3));}
        else expect(after.project[relative]).toEqual(row);
      }
      for (const relative of Object.keys(after.project).filter(key => !(key in before!.project))) expect(relative === patchRelative || relative.startsWith(patchRelative + path.sep)).toBe(true);
    }
    save('custody-after-prepare', after); await e.screenshot(page, 'actual-patch-prepare-' + mode);
    const reloadDatasets = wire('/api/patch-classification/datasets', 'GET', () => true);
    const reloadSample = mode === 'reopen' ? wire('/api/patch-classification/sample', 'GET', (_r, url) => url.searchParams.get('dataset_path') === prepared.dataset_path && url.searchParams.get('sample_index') === '0') : undefined;
    try {
      const deadline = performance.now() + 10_000; await handoffWithin(page.reload(), deadline, 'ordinary owning reload'); await enterPatch(deadline, mode === 'reopen' ? prepared.dataset_path : '');
      expect(await actualBody(await handoffWithin(reloadDatasets.reply, deadline, 'owning reload dataset list'), reloadDatasets.original(), deadline, 'ordinary-reload-datasets', 200)).toEqual(mode === 'reopen' ? baselineDatasets : {datasets: []});
      if (mode === 'reopen') {
        expect(await actualBody(await handoffWithin(reloadSample!.reply, deadline, 'owning reload sample'), reloadSample!.original(), deadline, 'ordinary-reload-sample', 200)).toEqual(baselineSample);
        await previewReady(deadline, baselineSample); await handoffWithin(expect(datasetSelector().locator('option:checked')).toHaveText('준비 1 · 패치 12개 · 16px / 간격 16px', {timeout: remaining(deadline)}), deadline, 'saved original preparation parameters restored');
      }
      await handoffWithin(expect(page.getByRole('alert')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'ordinary reload has no stale prepare error');
    } finally {reloadDatasets.close(); reloadSample?.close();}
    if (mode === 'reopen') expect(await fixtureApi('/api/patch-classification/manifest?dataset_path=' + encodeURIComponent(prepared.dataset_path))).toEqual(baselineManifest);
    expect(await fixtureApi('/api/project/current')).toEqual(project); expect((await fixtureApi('/api/training/jobs')).jobs).toEqual([]);
    reopened = snapshot(); expect(reopened).toEqual(after); expect(writes).toEqual([{method: 'POST', path: '/api/patch-classification/prepare', body: expectedRequest}]); save('custody-after-reload', reopened);
    await e.screenshot(page, 'ordinary-reload-patch-' + mode);
  } catch (error) {primary = error; throw error;} finally {
    page.off('request', observeWrites); const secondary: unknown[] = [];
    try {expect(callbackErrors).toEqual([]); expect(descriptors.closed).toBe(descriptors.opened); expect(tree(source)).toEqual(originalSource); expect(Object.fromEntries(DEPENDENCIES.map(name => [name, sha(fs.readFileSync(name))]))).toEqual(pins); expect(execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim()).toBe(head);} catch (error) {secondary.push(error);}
    e.note('patch_prepare_' + mode, {cell: 'F026.dino-patch-native-prepare.' + mode, source: {head, sha256: pins}, actual_renderer_backend: true, original_10s_actions_full_bodies: true, request_context_header_equality: true, fixture_source_adoption_before_custody: true, controlled_folder_answer_only: true, actual_native_picker: false, project, selected: source, originalSource, before, after, reopened, prepared, baselineManifest, baselineDatasets, baselineSample, errorFixture, proofs, writes, descriptors, callback_errors: callbackErrors.map(String), secondary_errors: secondary.map(String), source_and_prepared_custody: 'raw7 plus SHA; preparation creates only its declared new owned subtree; reload is read-only', preview_claim: 'decoded readiness, dimensions and owning body/path/hash; no full RGB parity claim', training: false, model_execution: false, GPU: false, native: false, quality_approval: false, parent_acceptance: false});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary.map(String)}); else if (primary === undefined && secondary.length) throw secondary[0];
  }
}

test('patch preparation reopens its actual saved dataset manifest and original-coordinate preview without training', async ({page, request, renderer, workspace, evidence}) => exercise('reopen', {page, request, renderer, workspace, evidence}));
test('patch preparation reports a real owned filesystem obstruction and reloads without fabricated prepared data', async ({page, request, renderer, workspace, evidence}) => exercise('error', {page, request, renderer, workspace, evidence}));

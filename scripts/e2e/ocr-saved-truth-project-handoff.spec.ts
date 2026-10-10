import fs from 'node:fs';
import path from 'node:path';
import type {Page, Request} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import {handoffApi, handoffHash, handoffProject, handoffLateRead,
  handoffSave, handoffWithin, type HandoffApi} from './fixtures/remaining-project-handoff';

test.use({actionTimeout: 10_000});
const identity = (s: fs.Stats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeMs, s.ctimeMs];
const relative = ['row-0/part.png', 'row-1/part.png', 'row-2/part.png', 'row-3/part.png'];
const splits = ['train', 'train', 'val', 'test'] as const;
type Scope = {tag: 'A' | 'B'; project: any; source: string; files: string[]; truth: string;
  originals: Array<{path: string; size: number; sha256: string}>; manifest?: any};

// These declared scopes include every prepared manifest/copy, source image,
// original annotation, report, labelset and OCR model member. Unrelated
// project lifecycle SQLite stores are outside this fixture's claim.
function tree(root: string): Record<string, unknown> {
  const result: Record<string, unknown> = {};
  try {fs.lstatSync(root);} catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {'.': {kind: 'absent'}};
    throw error;
  }
  const visit = (file: string) => {
    const named = fs.lstatSync(file), member = path.relative(root, file).split(path.sep).join('/') || '.';
    expect(named.isSymbolicLink()).toBe(false);
    if (named.isDirectory()) {
      result[member] = {kind: 'directory', identity: identity(named)};
      for (const name of fs.readdirSync(file).sort()) visit(path.join(file, name));
      expect(identity(fs.lstatSync(file))).toEqual(identity(named)); return;
    }
    expect(named.isFile()).toBe(true); expect(named.nlink).toBe(1);
    const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
    let primary: unknown;
    try {
      expect(identity(fs.fstatSync(fd))).toEqual(identity(named));
      const raw = fs.readFileSync(fd); expect(raw.length).toBe(named.size);
      expect(identity(fs.fstatSync(fd))).toEqual(identity(named));
      expect(identity(fs.lstatSync(file))).toEqual(identity(named));
      result[member] = {kind: 'file', identity: identity(named), sha256: handoffHash(raw)};
    } catch (error) {primary = error; throw error;}
    finally {try {fs.closeSync(fd);} catch (error) {if (primary === undefined) throw error;}}
  };
  visit(root); return result;
}

async function make(api: HandoffApi, workspace: Workspace, tag: 'A' | 'B'): Promise<Scope> {
  const source = path.join(workspace.root, 'ocr-saved-truth-source-' + tag); fs.mkdirSync(source);
  const files = relative.map((name, index) => {
    const file = path.join(source, name); fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, (tag === 'A' ? 30 : 140) + index * 20])); return file;
  });
  const originals = files.map(file => ({path: file, size: fs.statSync(file).size, sha256: handoffHash(fs.readFileSync(file))}));
  expect(new Set(originals.map(row => row.sha256)).size).toBe(4);
  const made = await api('/api/project/create', {name: 'Owned OCR saved truth handoff ' + tag, task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  const project = await api('/api/project/current'); expect(project.id).toBe(made.id);
  expect(project.source_dataset_dir).toBe(source); expect(project.task).toBe('classification');
  await api('/api/team-data'); await api('/api/team-data/readiness');
  const metadata = (await api('/api/dataset/metadata?limit=100')).items; expect(metadata).toHaveLength(4);
  for (const row of metadata) await api('/api/team-data/images/' + row.image_uuid);
  for (const file of files) await api('/api/annotations/part?file_path=' + encodeURIComponent(file));
  await api('/api/project/labelsets'); await api('/api/ocr/datasets'); await api('/api/ocr/models');
  return {tag, project, source, files, originals, truth: tag === 'A' ? '  검사Ａ12\u00a0  ' : '  확인Ｂ34\u00a0  '};
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, origin: string, url: string) {
  const api = handoffApi(page, origin, false), A = await make(api, workspace, 'A'), B = await make(api, workspace, 'B');
  expect(A.project.id).not.toBe(B.project.id); expect(A.project.project_dir).not.toBe(B.project.project_dir);
  expect(A.source).not.toBe(B.source); expect(A.truth).not.toBe(B.truth);
  expect(new Set([...A.originals, ...B.originals].map(row => row.sha256)).size).toBe(8);
  const ocr = page.locator('details').filter({has: page.locator('summary').filter({hasText: '문자 인식 모델 실험'})}).first();
  const picker = page.getByLabel('문자 원본 이미지 선택', {exact: true});
  const text = page.getByLabel('OCR 실제 정답 문자열', {exact: true});
  const table = ocr.getByRole('textbox', {name: /^정답 표 · 한 줄에/});
  const folder = ocr.getByRole('textbox', {name: '문자 이미지 폴더 경로', exact: true});
  const load = ocr.getByRole('button', {name: '저장된 정답 읽기', exact: true});
  const save = ocr.getByRole('button', {name: '정답과 이미지 해시 저장', exact: true});
  const expectedTable = (scope: Scope) => relative.map((name, i) => `${name}\t${scope.truth}\t${splits[i]}`).join('\n');
  const checkProject = async (scope: Scope) => {
    const current = await api('/api/project/current');
    expect([current.id, current.project_dir, current.source_dataset_dir, current.task, current.active_labelset_id])
      .toEqual([scope.project.id, scope.project.project_dir, scope.source, scope.project.task, scope.project.active_labelset_id]);
  };
  const enter = async (scope: Scope) => {
    await checkProject(scope);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button', {name: /^03.*오토딥러닝/}).click();
    await page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /^문자 인식/}).click();
    await expect(ocr).toBeVisible(); await expect(picker).toBeEnabled();
    await expect(picker.locator('option')).toHaveCount(5);
    expect((await picker.locator('option').evaluateAll(options => options.map(option => (option as HTMLOptionElement).value))).sort())
      .toEqual(['', ...scope.files].sort());
    const details = ocr.locator('details').filter({has: page.locator('summary').filter({hasText: '정답 표·가져오기 상세 설정'})});
    if (await details.getAttribute('open') === null) await details.locator('summary').click();
    await expect(folder).toHaveValue(scope.manifest?.dataset_path ?? scope.source);
    await expect(picker).toHaveValue(''); await expect(text).toHaveValue(''); await expect(table).toHaveValue('');
    await expect(ocr.getByRole('button', {name: '선택 이미지의 문자 정답 추가', exact: true})).toBeDisabled();
    await expect(save).toBeDisabled();
  };
  const requestContext = async (request: Request, scope: Scope, deadline: number) => {
    expect(request.frame()).toBe(page.mainFrame()); expect(request.isNavigationRequest()).toBe(false);
    expect(await handoffWithin(request.headerValue('x-vision-project'), deadline, 'owning project header')).toBe(scope.project.id);
    const raw = await handoffWithin(request.headerValue('x-vision-context'), deadline, 'owning project context');
    expect(raw).not.toBeNull(); const value = JSON.parse(raw!); expect(value.project_id).toBe(scope.project.id);
    expect(value.workspace_id).toEqual(expect.any(String)); expect(value.actor_id).toEqual(expect.any(String)); expect(value.mode).toBe('local'); return value;
  };
  const checkManifest = (scope: Scope, value: any) => {
    expect(value.sample_count).toBe(4); expect(path.dirname(value.dataset_path)).toBe(path.join(scope.project.dataset_dir, 'ocr'));
    expect(value.alphabet).toBe(Array.from(new Set(scope.truth)).sort().join(''));
    expect(value.samples).toEqual(relative.map((image, i) => ({image, text: scope.truth, split: splits[i], source_sha256: scope.originals[i].sha256})));
    expect(value.provenance.source_dataset_path).toBe(scope.source);
    expect(value.provenance.source_map).toEqual(Object.fromEntries(relative.map((image, i) => [image, {source_relative_path: image, source_sha256: scope.originals[i].sha256}])));
    expect(value.provenance.split_counts).toEqual({train: 2, val: 1, test: 1});
  };
  const proof: any[] = [];
  const rawResponse = (label: string, raw: Buffer) => {
    const file = path.join(workspace.logs, label + '-actual-raw-response.json');
    fs.writeFileSync(file, raw, {flag: 'wx'}); evidence.addFile(file);
    return {path: file, size: raw.length, sha256: handoffHash(raw)};
  };
  const saveTruth = async (scope: Scope) => {
    for (let i = 0; i < scope.files.length; i++) {
      await picker.selectOption(scope.files[i]); await text.fill(scope.truth);
      await page.getByLabel(/^독립 이미지 분할/).selectOption(splits[i]);
      await ocr.getByRole('button', {name: '선택 이미지의 문자 정답 추가', exact: true}).click();
    }
    await expect(table).toHaveValue(expectedTable(scope)); await expect(save).toBeEnabled();
    const deadline = performance.now() + 10_000;
    const waiting = page.waitForResponse(response => {
      const address = new URL(response.url()); return address.origin === origin && address.pathname === '/api/ocr/prepare'
        && response.request().method() === 'POST' && response.request().frame() === page.mainFrame();
    }, {timeout: Math.max(1, deadline - performance.now())});
    const [response] = await handoffWithin(Promise.all([waiting, save.click()]), deadline, 'actual Unicode truth save');
    const request = response.request(), context = await requestContext(request, scope, deadline);
    expect(request.postDataJSON()).toEqual({source_dataset_path: scope.source, samples: relative.map((image, i) => ({image, text: scope.truth, split: splits[i]}))});
    expect(response.status()).toBe(200); const raw = await handoffWithin(response.body(), deadline, 'complete saved manifest response');
    expect(raw.length).toBeLessThanOrEqual(1024 * 1024); expect(await handoffWithin(response.finished(), deadline, 'saved response finished')).toBeNull();
    scope.manifest = JSON.parse(raw.toString('utf8')); checkManifest(scope, scope.manifest);
    await expect(folder).toHaveValue(scope.manifest.dataset_path); await expect(ocr.getByText('검증된 정답 4개', {exact: true})).toBeVisible();
    const captured = rawResponse('ocr-' + scope.tag + '-saved', raw);
    proof.push({project_id: scope.project.id, source: scope.source, method: 'POST', path: '/api/ocr/prepare', status: 200,
      context, exact_raw_bytes: raw.length, exact_raw_sha256: handoffHash(raw), raw_response: captured});
    await table.scrollIntoViewIfNeeded(); await expect(table).toBeInViewport(); await evidence.screenshot(page, 'ocr-' + scope.tag + '-saved-distinct-unicode');
  };
  const readSaved = async (scope: Scope) => {
    const deadline = performance.now() + 10_000;
    const waiting = page.waitForResponse(response => {const address = new URL(response.url()); return address.origin === origin
      && address.pathname === '/api/ocr/manifest' && address.searchParams.get('dataset_path') === scope.manifest.dataset_path
      && response.request().method() === 'GET' && response.request().frame() === page.mainFrame();}, {timeout: Math.max(1, deadline - performance.now())});
    const [response] = await handoffWithin(Promise.all([waiting, load.click()]), deadline, 'actual saved truth read');
    const address = new URL(response.url()); expect(Array.from(address.searchParams.keys())).toEqual(['dataset_path']);
    const context = await requestContext(response.request(), scope, deadline); expect(response.status()).toBe(200);
    const raw = await handoffWithin(response.body(), deadline, 'complete saved truth body'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024);
    expect(JSON.parse(raw.toString('utf8'))).toEqual(scope.manifest);
    expect(await handoffWithin(response.finished(), deadline, 'saved truth HTTP finished')).toBeNull();
    await handoffWithin(expect(table).toHaveValue(expectedTable(scope), {timeout: Math.max(1, deadline - performance.now())}), deadline, 'owning visible exact truth');
    await expect(folder).toHaveValue(scope.manifest.dataset_path);
    proof.push({project_id: scope.project.id, source: scope.source, method: 'GET', path: '/api/ocr/manifest', status: 200, context,
      exact_raw_bytes: raw.length, exact_raw_sha256: handoffHash(raw), dataset_path: scope.manifest.dataset_path,
      raw_response: rawResponse('ocr-' + scope.tag + '-read-' + proof.length, raw)});
  };
  const state = async (scope: Scope) => {
    await checkProject(scope);
    const metadata = (await api('/api/dataset/metadata?limit=100')).items; expect(metadata).toHaveLength(4);
    const roots = {source: scope.source, annotations: scope.project.annotations_dir, reports: scope.project.reports_dir,
      labelsets: path.join(scope.project.project_dir, 'labelsets'), preparedOCR: path.join(scope.project.dataset_dir, 'ocr'), modelsOCR: path.join(scope.project.models_dir, 'ocr')};
    return {roots: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])), metadata,
      annotations: await Promise.all(scope.files.map(file => api('/api/annotations/part?file_path=' + encodeURIComponent(file)))),
      team: await api('/api/team-data'), readiness: await api('/api/team-data/readiness'),
      teamImages: await Promise.all(metadata.map((row: any) => api('/api/team-data/images/' + row.image_uuid))),
      labelsets: await api('/api/project/labelsets'), split: await api('/api/dataset/metadata/split'),
      registry_sha256: handoffHash(fs.readFileSync(path.join(scope.project.project_dir, 'labelsets.json'))),
      datasets: await api('/api/ocr/datasets'), models: await api('/api/ocr/models'),
      manifest: await api('/api/ocr/manifest?dataset_path=' + encodeURIComponent(scope.manifest.dataset_path))};
  };
  let late: Awaited<ReturnType<typeof handoffLateRead>> | undefined, primary: unknown;
  const writes: Array<{method: string; path: string; body: unknown}> = [];
  const observe = (request: Request) => {const address = new URL(request.url()); if (address.origin === origin
    && address.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method()))
      writes.push({method: request.method(), path: address.pathname, body: request.postDataJSON()});};
  try {
    await page.goto(url); await handoffProject(page, A, origin); await enter(A); await saveTruth(A); const beforeA = await state(A);
    await handoffProject(page, B, origin); await enter(B); await saveTruth(B); const beforeB = await state(B);
    expect(A.manifest.dataset_path).not.toBe(B.manifest.dataset_path); expect(A.manifest.provenance.dataset_sha256).not.toBe(B.manifest.provenance.dataset_sha256);
    await handoffProject(page, A, origin); await enter(A); await readSaved(A); expect(await state(A)).toEqual(beforeA);
    page.on('request', observe);
    late = await handoffLateRead(page, origin, false, address => address.pathname === '/api/ocr/manifest'
      && address.searchParams.get('dataset_path') === A.manifest.dataset_path);
    const oldDeadline = performance.now() + 10_000;
    const oldRequest = page.waitForRequest(request => {const address = new URL(request.url()); return address.origin === origin
      && address.pathname === '/api/ocr/manifest' && address.searchParams.get('dataset_path') === A.manifest.dataset_path
      && request.method() === 'GET' && request.frame() === page.mainFrame();}, {timeout: Math.max(1, oldDeadline - performance.now())});
    const [originalRequest] = await handoffWithin(Promise.all([oldRequest, load.click()]), oldDeadline, 'owning A UI request and click');
    const oldContext = await requestContext(originalRequest, A, oldDeadline);
    const captured = await handoffWithin(late.ready(), oldDeadline, 'genuine original A response captured'); expect(captured.body).toEqual(A.manifest);
    const oldRaw = rawResponse('ocr-A-held-owning-read', captured.bytes);
    const toB = await handoffProject(page, B, origin); await enter(B); await readSaved(B); const oldA = await late.finish();
    await expect(table).toHaveValue(expectedTable(B)); await expect(folder).toHaveValue(B.manifest.dataset_path);
    expect(await state(B)).toEqual(beforeB); await table.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'ocr-B-owning-truth-after-old-A-read');
    const toA = await handoffProject(page, A, origin); await enter(A); await readSaved(A);
    expect(await state(A)).toEqual(beforeA); await table.scrollIntoViewIfNeeded(); await evidence.screenshot(page, 'ocr-A-original-truth-reopened-after-handoff');
    expect(writes).toEqual([toB, toA].map(row => ({method: 'POST', path: '/api/project/open', body: {project_dir: row.project_dir}})));
    handoffSave(evidence, workspace, 'ocr-saved-truth-handoff-custody', {beforeA, beforeB, afterA: await state(A), original_response_proofs: proof, transitions: [toB, toA], held_original_A_read: {oldA, context: oldContext, raw_response: oldRaw}});
    evidence.note('ocr_saved_truth_project_handoff', {action: 'F030.ocr-native-exact-truth-save-reopen', dimensions: ['handoff'],
      actual_browser_UI: true, two_actual_prepare_POSTs: 2, shared_relative_names_distinct_original_pixels_and_Unicode: true,
      exact_saved_source_hashes_splits_and_visible_truth: true, saved_records_not_transferred_between_project_contexts: true,
      actual_A_B_A_project_open: [toB, toA], late_A_read_transport: oldA,
      six_declared_source_annotation_report_labelset_prepared_model_trees_exact: true,
      OCR_train_evaluate_predict_and_annotation_writes_after_setup: 0, business_mutations_after_baseline: writes,
      original_OCR_case_sources_unchanged: true, source_Electron: false, installed_native: false,
      training: false, model_inference: false, human_truth_review: false, quality_parent_target_acceptance: false});
  } catch (error) {primary = error; throw error;}
  finally {
    let cleanupError: unknown;
    try {page.off('request', observe);} catch (error) {cleanupError = error;}
    if (late) {try {await late.close();} catch (error) {if (cleanupError === undefined) cleanupError = error;}}
    if (primary === undefined && cleanupError !== undefined) throw cleanupError;
  }
}

test('saved OCR Unicode truth and source hashes stay scoped through A B A project handoff and an old owning read',
  async ({page, renderer, workspace, evidence}) => {
    await installDesktopHostShim(page, renderer.port);
    await exercise(page, workspace, evidence, renderer.origin, renderer.url);
  });

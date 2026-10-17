import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout: 10_000});
const sha = (raw: Buffer) => crypto.createHash('sha256').update(raw).digest('hex');
const raw9 = (s: fs.BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.size, s.mtimeNs, s.ctimeNs, s.uid, s.gid].map(String);
function remaining(deadline: number) {const left = deadline - performance.now(); if (left <= 0) throw Error('Original 10s fitting frame expired'); return Math.max(1, Math.floor(left));}
async function within<T>(work: Promise<T>, deadline: number, label: string): Promise<T> {
  let timer: NodeJS.Timeout | undefined;
  try {const value = await Promise.race([work, new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original 10s fitting frame expired: ' + label)), remaining(deadline));})]); expect(performance.now()).toBeLessThanOrEqual(deadline); return value;}
  finally {clearTimeout(timer);}
}
type FilePin = {kind: 'file'; raw9: string[]; sha256: string};
type Tree = Record<string, FilePin | {kind: 'directory'; raw9: string[]}>;

// Actual face duplicate-edge and irregular collinear-source points. The
// original fitter must return its exact geometric422 and owning alert while
// refusing append and preserving the existing saved three-split truth.
const modes = [
  {mode: 'face', button: '한 면 기준', targets: [[64, 64], [64, 64], [128, 96]], detail: 'Fitting edge must have positive length'},
  {mode: 'irregular', button: '불규칙 외곽', targets: [[64, 64], [128, 64], [192, 64]], detail: 'Polygon has no positive area'},
] as const;
for (const variant of modes) {
test(variant.mode + ' fitting refuses actual degenerate points without changing saved truth and reopens exact baseline', async ({page, request, renderer, workspace: w, evidence: e}) => {
  await installDesktopHostShim(page, renderer.port);
  const head = execFileSync('git', ['rev-parse', 'HEAD'], {encoding: 'utf8'}).trim(); expect(head).toMatch(/^[a-f0-9]{40}$/);
  const custody = {opened: 0, closed: 0, uncertain: [] as string[]};
  const proofs: any[] = [], writes: Array<{method: string; route: string; body: unknown}> = [], callbackErrors: string[] = [];
  const pageErrors: string[] = [], consoleErrors: Array<{text: string; location: {url: string; lineNumber: number; columnNumber: number}}> = [], non2xx: unknown[] = [];
  const pageError = (error: Error) => pageErrors.push(error.message);
  const consoleError = (message: {type(): string; text(): string; location(): {url: string; lineNumber: number; columnNumber: number}}) => {if (message.type() === 'error') consoleErrors.push({text: message.text(), location: message.location()});};
  const failedResponse = (response: Response) => {try {const u = new URL(response.url()); if (u.origin === renderer.origin && u.pathname.startsWith('/api/') && response.status() >= 400) non2xx.push({method: response.request().method(), route: u.pathname, status: response.status()});} catch (error) {callbackErrors.push(String(error));}};
  page.on('pageerror', pageError); page.on('console', consoleError); page.on('response', failedResponse);
  const file = (name: string): FilePin => {
    const before = fs.lstatSync(name, {bigint: true}); expect(before.isSymbolicLink()).toBe(false); expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
    const descriptor = fs.openSync(name, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW); custody.opened++;
    let primary: unknown, failed = false, result: FilePin | undefined;
    try {expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); const bytes = fs.readFileSync(descriptor); expect(BigInt(bytes.length)).toBe(before.size);
      expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(before)); expect(raw9(fs.lstatSync(name, {bigint: true}))).toEqual(raw9(before)); result = {kind: 'file', raw9: raw9(before), sha256: sha(bytes)};
    } catch (error) {failed = true; primary = error;}
    finally {try {fs.closeSync(descriptor); custody.closed++;} catch (error) {custody.uncertain.push(String(error)); if (!failed) {failed = true; primary = error;}}}
    if (failed) throw primary; return result!;
  };
  const tree = (root: string): Tree => {
    expect(fs.realpathSync(root)).toBe(root); const rows: Tree = {};
    const walk = (directory: string) => {const before = fs.lstatSync(directory, {bigint: true}); expect(before.isDirectory()).toBe(true); expect(before.isSymbolicLink()).toBe(false);
      const names = fs.readdirSync(directory).sort(); rows[path.relative(root, directory) || '.'] = {kind: 'directory', raw9: raw9(before)};
      for (const name of names) {const member = path.join(directory, name), info = fs.lstatSync(member); expect(info.isSymbolicLink()).toBe(false); if (info.isDirectory()) walk(member); else rows[path.relative(root, member)] = file(member);}
      expect(fs.readdirSync(directory).sort()).toEqual(names); expect(raw9(fs.lstatSync(directory, {bigint: true}))).toEqual(raw9(before));
    }; walk(root); return rows;
  };
  const api = async (route: string, data?: unknown, method = data === undefined ? 'GET' : 'POST') => {
    const deadline = performance.now() + 10_000;
    const response = await within(request.fetch(renderer.origin + route, {method, ...(data === undefined ? {} : {data}), timeout: remaining(deadline)}), deadline, 'fixture API');
    const raw = await within(response.body(), deadline, 'fixture full body'); expect(response.status()).toBe(200); expect(raw.length).toBeLessThanOrEqual(1024 * 1024);
    const body = JSON.parse(raw.toString('utf8')); proofs.push({kind: 'fixture API', method, route, request: data ?? null, status: 200, response_sha256: sha(raw), response: body}); return body;
  };
  const wire = (route: string, method: string, match: (r: Request, u: URL) => boolean) => {
    let original: Request | undefined, accept!: (r: Response) => void, refuse!: (error: unknown) => void;
    const reply = new Promise<Response>((resolve, reject) => {accept = resolve; refuse = reject;}); void reply.catch(() => undefined);
    const poison = (error: unknown) => {callbackErrors.push(String(error)); refuse(error);};
    const timer = setTimeout(() => poison(Error('Original 10s UI response missing: ' + route)), 10_000);
    const started = (r: Request) => {try {const u = new URL(r.url()); if (u.origin === renderer.origin && u.pathname === route && r.method() === method && match(r, u)) {expect(r.frame()).toBe(page.mainFrame()); expect(original).toBeUndefined(); original = r;}} catch (error) {poison(error);}};
    const responded = (r: Response) => {try {if (r.request() === original) accept(r);} catch (error) {poison(error);}};
    page.on('request', started); page.on('response', responded);
    return {reply, original: () => original, close: () => {clearTimeout(timer); page.off('request', started); page.off('response', responded);}};
  };
  let project: any, context: any;
  const body = async (response: Response, deadline: number, label: string, original: Request | undefined, expectedStatus: 200 | 422 = 200) => {
    const own = response.request(); expect(original).toBeDefined(); expect(own).toBe(original); expect(own.frame()).toBe(page.mainFrame()); expect(new URL(response.url()).origin).toBe(renderer.origin); expect(response.status()).toBe(expectedStatus);
    expect(await within(own.headerValue('x-vision-project'), deadline, 'own project header')).toBe(project.id);
    const rawContext = await within(own.headerValue('x-vision-context'), deadline, 'own request context header'); expect(rawContext).not.toBeNull(); const actualContext = JSON.parse(rawContext!); expect(actualContext.project_id).toBe(project.id); expect(actualContext.mode).toBe('local');
    if (context) expect(actualContext).toEqual(context); else context = actualContext;
    const raw = await within(response.body(), deadline, 'same Request full response'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024); expect(await within(response.finished(), deadline, 'same response completion')).toBeNull();
    const actual = JSON.parse(raw.toString('utf8')), rawFile = path.join(w.logs, label + '-raw-response.json'); fs.writeFileSync(rawFile, raw, {flag: 'wx'}); e.addFile(rawFile);
    proofs.push({kind: 'original UI response', label, method: own.method(), url: response.url(), request_body: own.postDataJSON(), project_id: project.id, request_context: actualContext, status: expectedStatus, response: actual, response_sha256: sha(raw), bytes: raw.length, raw_file: rawFile, same_Request: true, main_frame: true, finished: true, deadline, completed: performance.now()}); return actual;
  };
  const source = path.join(fs.realpathSync(w.root), 'owned-invalid-' + variant.mode + '-fitting-source'), relative = ['train.png', 'val.png', 'test.png']; fs.mkdirSync(source);
  for (const [i, name] of relative.entries()) {const target = path.join(source, name); fs.writeFileSync(target, png(256, 3, (x, y) => [x, y, 31 + i * 67]), {flag: 'wx'}); e.addFile(target);}
  const sourceHashes = Object.fromEntries(relative.map(name => [name, file(path.join(source, name)).sha256]));
  project = await api('/api/project/create', {name: 'Owned ' + variant.mode + ' fitting invalid fixture', task: 'classification'});
  project = await api('/api/project/update', {source_dataset_dir: source}, 'PUT'); expect(project.source_dataset_dir).toBe(source); expect(path.dirname(project.project_dir)).toBe(w.projects);
  const imported = await api('/api/dataset/import', {folder_path: source, task: 'classification', validate_images: true}); expect(imported.total_images).toBe(3);
  for (const name of relative) await api('/api/dataset/metadata/image?image_path=' + encodeURIComponent(path.join(source, name)));
  await api('/api/team-data/readiness');
  const samples = relative.map((image, i) => ({image, split: ['train', 'val', 'test'][i], label: 'owned-fixture-box', box: {cx: 80, cy: 60, width: 40, height: 20, angle_deg: 0}, direction_deg: 15 + i}));
  const saved = await api('/api/rotated-detection/manifest', {dataset_path: source, samples}); expect(saved.sample_count).toBe(3); expect(saved.split_counts).toEqual({train: 1, val: 1, test: 1});
  expect(path.dirname(saved.dataset_path)).toBe(path.join(project.dataset_dir, 'rotated_detection')); expect(path.basename(saved.dataset_path)).toMatch(/^[a-f0-9]{32}$/);
  expect(saved.samples).toHaveLength(3);
  for (const sample of saved.samples) {const supplied = samples.find(row => row.image === sample.image)!; expect(supplied).toBeDefined(); expect(sample.source_sha256).toBe(sourceHashes[sample.image]); expect(sample.split).toBe(supplied.split);
    const objects = sample.objects ?? [{label: sample.label, box: sample.box, direction_deg: sample.direction_deg}]; expect(objects).toEqual([{label: supplied.label, box: supplied.box, direction_deg: supplied.direction_deg}]);}
  const manifestRoute = '/api/rotated-detection/manifest', manifestQuery = manifestRoute + '?dataset_path=' + encodeURIComponent(saved.dataset_path);
  expect(await api(manifestQuery)).toEqual(saved);
  const panel = () => page.locator('details').filter({has: page.locator('summary').filter({hasText: '회전 객체 위치 모델'})}).first();
  const fitting = () => panel().locator('section').filter({has: page.getByRole('heading', {name: '원본 이미지에서 회전 박스 그리기', exact: true})});
  const fitButton = () => fitting().getByRole('button', {name: '회전 박스 맞추기', exact: true}), append = () => fitting().getByRole('button', {name: '정답 표에 추가', exact: true});
  const table = () => panel().getByRole('textbox', {name: /^정답 표 · 이미지 상대 경로/});
  const open = async (label: string, reload: boolean) => {
    const capture = wire(manifestRoute, 'GET', (_r, u) => u.searchParams.get('dataset_path') === saved.dataset_path), deadline = performance.now() + 10_000;
    try {
      if (reload) await within(page.reload(), deadline, 'actual browser reload'); else await within(page.goto(renderer.url), deadline, 'original browser entry');
      await within(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name, {timeout: remaining(deadline)}), deadline, 'own current project');
      await within(page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(2).click({timeout: remaining(deadline)}), deadline, 'ordinary training stage');
      await within(page.getByRole('region', {name: '모델 학습 허브'}).getByRole('button', {name: /^회전 객체 검출/}).click({timeout: remaining(deadline)}), deadline, 'ordinary rotated workbench');
      expect(await body(await within(capture.reply, deadline, 'original saved manifest GET'), deadline, label, capture.original())).toEqual(saved);
      await within(expect(panel().getByRole('combobox', {name: '프로젝트에 저장된 회전 박스 정답', exact: true})).toHaveValue(saved.dataset_path, {timeout: remaining(deadline)}), deadline, 'own saved manifest selection');
      await within(expect(fitting().getByRole('combobox', {name: '이미지', exact: true}).locator('option')).toHaveCount(4, {timeout: remaining(deadline)}), deadline, 'three original image options');
      await within(expect(table()).not.toHaveValue('', {timeout: remaining(deadline)}), deadline, 'saved truth table hydrated');
    } finally {capture.close();}
  };
  await open('initial-own-saved-manifest', false);
  const image = path.join(source, 'train.png'), previewCapture = wire('/api/rotated-detection/fit-source', 'GET', (_r, u) => u.searchParams.get('image_path') === image);
  let preview: any;
  try {const deadline = performance.now() + 10_000; await within(fitting().getByRole('combobox', {name: '이미지', exact: true}).selectOption(image, {timeout: remaining(deadline)}), deadline, 'own source image selection');
    preview = await body(await within(previewCapture.reply, deadline, 'original fit-source GET'), deadline, 'own-fitting-source', previewCapture.original()); expect(preview.source_size).toEqual([256, 256]); expect(preview.source_sha256).toBe(sourceHashes['train.png']);
    await within(expect(fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true}).locator('image')).toHaveAttribute('href', preview.preview_data_url, {timeout: remaining(deadline)}), deadline, 'original preview rendered');
  } finally {previewCapture.close();}
  await fitting().getByRole('button', {name: variant.button, exact: true}).click();
  const initialTable = await table().inputValue(); expect(initialTable.split('\n')).toHaveLength(3);
  const protectedTrees = () => ({source: tree(source), project: tree(project.project_dir), prepared: tree(saved.dataset_path)});
  const before = protectedTrees(); expect(before.source).toEqual(tree(source));
  for (const name of relative) expect((before.source[name] as FilePin).sha256).toBe(sourceHashes[name]);
  const observe = (own: Request) => {try {const u = new URL(own.url()); if (u.origin === renderer.origin && u.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(own.method())) writes.push({method: own.method(), route: u.pathname, body: own.postDataJSON()});} catch (error) {callbackErrors.push(String(error));}};
  page.on('request', observe);
  await page.evaluate(() => {const state = {clicks: [] as any[], pointers: [] as any[]}; (window as any).__f029ModeInvalid = state; document.addEventListener('click', event => {const element = event.target instanceof Element ? event.target : null, button = element?.closest('button'); if (button?.textContent?.trim() === '회전 박스 맞추기') state.clicks.push({text: button.textContent.trim(), trusted: event.isTrusted}); if (element?.closest('svg[aria-label="회전 박스 정답 그리기"]')) state.pointers.push({client: [event.clientX, event.clientY], trusted: event.isTrusted});}, true);});
  let failed = false, primary: unknown, proof: any; const secondary: string[] = [];
  try {
    const svg = fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true});
    await expect(svg.locator('circle')).toHaveCount(0); await expect(svg.locator('polygon')).toHaveCount(0); await expect(append()).toBeDisabled();
    const delivered: number[][] = [], clients: number[][] = [], capture = wire('/api/rotated-detection/fit-box', 'POST', r => r.postDataJSON()?.image_path === image && r.postDataJSON()?.mode === variant.mode);
    let actualRequest: any, refusal: any, trusted: any;
    try {const deadline = performance.now() + 10_000; await within(svg.scrollIntoViewIfNeeded({timeout: remaining(deadline)}), deadline, 'original fitting viewport');
      const geometry = await within(svg.boundingBox(), deadline, 'actual source SVG geometry'); expect(geometry).not.toBeNull();
      const scale = Math.min(geometry!.width / 256, geometry!.height / 256), left = geometry!.x + (geometry!.width - 256 * scale) / 2, top = geometry!.y + (geometry!.height - 256 * scale) / 2;
      // Only original pointer events supply the request's points. Equal target
      // coordinates use the same fixed viewport/client pixel; no body injection.
      for (const [index, [x, y]] of variant.targets.entries()) {expect(await within(svg.boundingBox(), deadline, 'unchanged actual source viewport')).toEqual(geometry); const client = [Math.floor(left + x * scale), Math.floor(top + y * scale)]; clients.push(client); delivered.push([(client[0] - left) / scale, (client[1] - top) / scale]);
        await within(page.mouse.click(client[0], client[1]), deadline, 'actual source pointer delivery'); await within(expect(svg.locator('circle')).toHaveCount(index + 1, {timeout: remaining(deadline)}), deadline, 'original point state received click');}
      await within(expect(fitButton()).toBeEnabled({timeout: remaining(deadline)}), deadline, 'three points reach original fitting action');
      const circles = await within(svg.locator('circle').evaluateAll(rows => rows.map(row => [Number(row.getAttribute('cx')), Number(row.getAttribute('cy'))])), deadline, 'original rendered point coordinates');
      let independentDegeneracy: unknown;
      if (variant.mode === 'face') {expect(circles[0]).toEqual(circles[1]); expect(circles[2]).not.toEqual(circles[0]); const edge = Math.hypot(circles[1][0] - circles[0][0], circles[1][1] - circles[0][1]); expect(edge).toBe(0); independentDegeneracy = {zero_edge_length: edge};}
      else {expect(circles[0][1]).toBe(circles[1][1]); expect(circles[1][1]).toBe(circles[2][1]); expect(circles[0][0]).toBeLessThan(circles[1][0]); expect(circles[1][0]).toBeLessThan(circles[2][0]); const [a, b, c] = circles, twiceArea = Math.abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])); expect(twiceArea).toBe(0); independentDegeneracy = {same_horizontal_y: a[1], triangle_cross_product_area: twiceArea / 2};}
      await within(fitButton().click({timeout: remaining(deadline)}), deadline, 'trusted original invalid fit click');
      refusal = await body(await within(capture.reply, deadline, 'original fit-box 422'), deadline, 'actual-' + variant.mode + '-degenerate-refusal', capture.original(), 422);
      expect(refusal).toEqual({detail: variant.detail});
      actualRequest = capture.original()!.postDataJSON(); expect(actualRequest).toEqual({image_path: image, mode: variant.mode, points: actualRequest.points, source_sha256: sourceHashes['train.png']}); expect(actualRequest.points).toHaveLength(3);
      expect(actualRequest.points).toEqual(circles);
      for (let i = 0; i < 3; i++) for (let axis = 0; axis < 2; axis++) expect(actualRequest.points[i][axis]).toBeCloseTo(delivered[i][axis], 8);
      await within(expect(fitting().getByRole('alert')).toHaveText(refusal.detail, {timeout: remaining(deadline)}), deadline, 'owning fitter exact original refusal');
      await within(expect(fitting().getByRole('alert')).toBeVisible({timeout: remaining(deadline)}), deadline, 'owning refusal visible');
      await within(expect(fitButton()).toBeEnabled({timeout: remaining(deadline)}), deadline, 'original action recovered after refusal'); await within(expect(append()).toBeDisabled({timeout: remaining(deadline)}), deadline, 'invalid proposal cannot append');
      await within(expect(svg.locator('polygon')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'no returned geometric proposal');
      trusted = await within(page.evaluate(() => (window as any).__f029ModeInvalid), deadline, 'trusted pointer and fitting delivery');
      expect(trusted.clicks).toEqual([{text: '회전 박스 맞추기', trusted: true}]); expect(trusted.pointers).toEqual(clients.map(client => ({client, trusted: true})));
      proof = {mode: variant.mode, independently_computed_degeneracy: independentDegeneracy, delivered_source_points: delivered, actual_client_points: clients, rendered_circles: circles, actual_fit_request: actualRequest, refusal, trusted};
    } finally {capture.close();}
    expect(await table().inputValue()).toBe(initialTable); expect(protectedTrees()).toEqual(before); expect(writes).toEqual([{method: 'POST', route: '/api/rotated-detection/fit-box', body: actualRequest}]);
    await svg.scrollIntoViewIfNeeded(); await e.screenshot(page, 'f029-' + variant.mode + '-invalid-owning-refusal-no-append-saved-truth-intact');
    await open('real-reload-own-saved-manifest', true); expect(await table().inputValue()).toBe(initialTable); expect(protectedTrees()).toEqual(before);
    await expect(fitting().getByRole('alert')).toHaveCount(0); await expect(fitButton()).toBeDisabled(); await expect(append()).toBeDisabled(); expect(writes).toHaveLength(1);
    await table().scrollIntoViewIfNeeded(); await e.screenshot(page, 'f029-' + variant.mode + '-invalid-real-reload-saved-truth-exact');
    expect(pageErrors).toEqual([]); expect(callbackErrors).toEqual([]); expect(non2xx).toEqual([{method: 'POST', route: '/api/rotated-detection/fit-box', status: 422}]);
    // Chrome's one network diagnostic must be the same original geometric 422.
    // Every unrelated console or HTTP error still fails this case.
    expect(consoleErrors).toHaveLength(1); expect(consoleErrors[0].text).toBe('Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)');
    expect(consoleErrors[0].location.url).toBe(renderer.origin + '/api/rotated-detection/fit-box');
    proof = {...proof, schema: 'modu-vision.F029-mode-fit-invalid/v1', cells: ['F029.obb-native-' + variant.mode + '-fit-save-reopen.invalid'], source_head: head, project, source, source_hashes: sourceHashes, saved_manifest: saved, initial_table: initialTable, preview, actual_degenerate_geometry_exactly_refused: true, same_saved_table_after_refusal_and_real_reload: true, after_baseline_nonGET_requests: writes.slice(), persistent_API_mutations_after_baseline: 0, protected_before: before, protected_after: protectedTrees(), full_request_context_header_equality: true, original_UI_responses: proofs, errors: {page: pageErrors, console: consoleErrors, non2xx, callbacks: callbackErrors}, expected_original_HTTP_refusal: 422, unexpected_errors: 0, training_started: false, actual_model_inference: false, fitting_scope: 'Original PIL preview and geometric invalid-input refusal; no model forward or training', native_or_installed_GUI_claim: false, quality_approved: false, parent_completion_credit: false, registry_credit: false};
  } catch (error) {failed = true; primary = error;}
  finally {
    for (const [label, cleanup] of [['request observer', () => page.off('request', observe)], ['page-error observer', () => page.off('pageerror', pageError)], ['console observer', () => page.off('console', consoleError)], ['response observer', () => page.off('response', failedResponse)], ['protected trees', () => expect(protectedTrees()).toEqual(before)]] as const) {
      try {cleanup();} catch (error) {secondary.push(label + ': ' + String(error)); if (!failed) {failed = true; primary = error;}}
    }
    e.note('f029_mode_fit_invalid_progress', {proof, original_UI_responses: proofs, writes, errors: {page: pageErrors, console: consoleErrors, non2xx, callbacks: callbackErrors}});
    e.note('f029_mode_fit_closure', {FD_custody: custody, source_project_prepared_preserved: !secondary.some(row => row.startsWith('protected trees:')), secondary, first_exception: failed ? String(primary) : null});
  }
  if (failed) throw primary; expect(custody.opened).toBe(custody.closed); expect(custody.uncertain).toEqual([]); e.note('f029_mode_fit_invalid', proof);
});
}

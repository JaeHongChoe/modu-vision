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

// Two independent original-source error cases. Synthetic source mutation stays
// confined to the owned fixture file; original bytes are restored through the
// same held-descriptor protocol, while mtime/ctime changes remain explicit.
const sourceErrorModes = [
  {mode: 'face', button: '한 면 기준', points: [[64, 64], [192, 64], [64, 128]], boxPrecision: 6},
  {mode: 'irregular', button: '불규칙 외곽', points: [[64, 64], [192, 64], [192, 128], [64, 128]], boxPrecision: 4},
] as const;
for (const variant of sourceErrorModes) {
test(variant.mode + ' fitting source-change error retires the unsent proposal and recovers original saved truth', async ({page, request, renderer, workspace: w, evidence: e}) => {
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
  const body = async (response: Response, deadline: number, label: string, original: Request | undefined, status = 200) => {
    const own = response.request(); expect(original).toBeDefined(); expect(own).toBe(original); expect(own.frame()).toBe(page.mainFrame()); expect(new URL(response.url()).origin).toBe(renderer.origin); expect(response.status()).toBe(status);
    expect(await within(own.headerValue('x-vision-project'), deadline, 'own project header')).toBe(project.id);
    const rawContext = await within(own.headerValue('x-vision-context'), deadline, 'own request context header'); expect(rawContext).not.toBeNull(); const actualContext = JSON.parse(rawContext!); expect(actualContext.project_id).toBe(project.id); expect(actualContext.mode).toBe('local');
    if (context) expect(actualContext).toEqual(context); else context = actualContext;
    const raw = await within(response.body(), deadline, 'same Request full response'); expect(raw.length).toBeLessThanOrEqual(1024 * 1024); if (status === 422) expect(raw.length).toBe(49); expect(await within(response.finished(), deadline, 'same response completion')).toBeNull();
    const actual = JSON.parse(raw.toString('utf8')), rawFile = path.join(w.logs, label + '-raw-response.json'); fs.writeFileSync(rawFile, raw, {flag: 'wx'}); e.addFile(rawFile);
    proofs.push({kind: 'original UI response', label, method: own.method(), url: response.url(), request_body: own.postDataJSON(), project_id: project.id, request_context: actualContext, status, response: actual, response_sha256: sha(raw), bytes: raw.length, raw_file: rawFile, same_Request: true, main_frame: true, finished: true, deadline, completed: performance.now()}); return actual;
  };
  const source = path.join(fs.realpathSync(w.root), 'owned-' + variant.mode + '-fitting-source-error'), relative = ['train.png', 'val.png', 'test.png']; fs.mkdirSync(source);
  const originalInputBodies = new Map<string, Buffer>();
  for (const [i, name] of relative.entries()) {const target = path.join(source, name); const input = png(256, 3, (x, y) => [x, y, 31 + i * 67]); originalInputBodies.set(target, Buffer.from(input)); fs.writeFileSync(target, input, {flag: 'wx'}); e.addFile(target);}
  const sourceHashes = Object.fromEntries(relative.map(name => [name, file(path.join(source, name)).sha256]));
  project = await api('/api/project/create', {name: 'Owned ' + variant.mode + ' fitting source-change error fixture', task: 'classification'});
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
  const fitSummary = () => fitting().locator('p').filter({hasText: /^중심 \(/});
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
  await page.evaluate(() => {const state = {clicks: [] as any[]}; (window as any).__f029SourceChange = state; document.addEventListener('click', event => {const target = event.target instanceof Element ? event.target.closest('button') : null; if (target && ['회전 박스 맞추기', '점 다시 그리기'].includes(target.textContent?.trim() || '')) state.clicks.push({text: target.textContent!.trim(), trusted: event.isTrusted});}, true);});
  const originalImageBytes = Buffer.from(originalInputBodies.get(image)!);
  const changedImageBytes = png(256, 3, (x, y) => [255 - x, y, 173]);
  expect(sha(originalImageBytes)).toBe(sourceHashes['train.png']);
  expect(sha(changedImageBytes)).not.toBe(sourceHashes['train.png']);
  const alternate = path.join(w.logs, 'owned-source-change-alternate.png');
  fs.writeFileSync(alternate, changedImageBytes, {flag: 'wx'}); e.addFile(alternate);
  const alternatePin = file(alternate);
  let restoreNeeded = false, imagePin = before.source['train.png'] as FilePin;
  let changedPin: FilePin | undefined, restoredPin: FilePin | undefined, finalImagePin: FilePin | undefined;
  let failed = false, primary: unknown, proof: any; const secondary: string[] = [];
  const failedWriteReadbacks: Array<{expected: FilePin; observed: FilePin; cause: string}> = [];
  const replaceOwnedImage = (expected: FilePin, bytes: Buffer): FilePin => {
    expect(file(image)).toEqual(expected);
    const descriptor = fs.openSync(image, fs.constants.O_RDWR | fs.constants.O_NOFOLLOW); custody.opened++;
    let failed = false, primary: unknown, result: FilePin | undefined, writeEligible = false;
    try {
      expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(expected.raw9);
      writeEligible = true; restoreNeeded = true;
      let offset = 0;
      while (offset < bytes.length) {const written = fs.writeSync(descriptor, bytes, offset, bytes.length - offset, offset); expect(written).toBeGreaterThan(0); offset += written;}
      fs.ftruncateSync(descriptor, bytes.length); fs.fsyncSync(descriptor);
      const after = fs.fstatSync(descriptor, {bigint: true});
      for (const index of [0, 1, 2, 3, 7, 8]) expect(raw9(after)[index]).toBe(expected.raw9[index]);
      const readback = fs.readFileSync(descriptor); expect(readback).toEqual(bytes);
      expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(raw9(after));
      expect(raw9(fs.lstatSync(image, {bigint: true}))).toEqual(raw9(after));
      result = {kind: 'file', raw9: raw9(after), sha256: sha(readback)};
      imagePin = result;
    } catch (error) {
      failed = true; primary = error;
      if (writeEligible) {
        // Preserve a partial own write only under the held original descriptor
        // and unchanged path identity. Unknown/replaced custody is refused.
        try {
          const actual = fs.fstatSync(descriptor, {bigint: true});
          for (const index of [0, 1, 2, 3, 7, 8]) expect(raw9(actual)[index]).toBe(expected.raw9[index]);
          expect(actual.size).toBeLessThanOrEqual(BigInt(Math.max(originalImageBytes.length, changedImageBytes.length)));
          expect(raw9(fs.lstatSync(image, {bigint: true}))).toEqual(raw9(actual));
          const observed = file(image); expect(observed.raw9).toEqual(raw9(actual));
          expect(raw9(fs.fstatSync(descriptor, {bigint: true}))).toEqual(observed.raw9);
          imagePin = observed; failedWriteReadbacks.push({expected, observed, cause: String(error)});
        } catch (custodyError) {secondary.push('owned source failed-write custody: ' + String(custodyError));}
      }
    }
    finally {try {fs.closeSync(descriptor); custody.closed++;} catch (error) {custody.uncertain.push(String(error)); if (!failed) {failed = true; primary = error;}}}
    if (failed) throw primary; return result!;
  };
  const protectedAt = (expectedImage: FilePin) => {
    const now = protectedTrees();
    expect(now.project).toEqual(before.project); expect(now.prepared).toEqual(before.prepared);
    expect({...now.source, 'train.png': before.source['train.png']}).toEqual(before.source);
    expect(now.source['train.png']).toEqual(expectedImage);
    for (const index of [0, 1, 2, 3, 7, 8]) expect(expectedImage.raw9[index]).toBe((before.source['train.png'] as FilePin).raw9[index]);
    return now;
  };
  const screenshotGuards: any[] = [];
  const proposalScreenshot = async (name: string, anchor: 'proposal' | 'error') => {
    // This is a separate visual action frame after the original HTTP body has
    // completed. It never refreshes that request's existing10s body deadline.
    const deadline = performance.now() + 10_000;
    const svg = fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true});
    const target = anchor === 'proposal' ? fitSummary() : fitting().getByRole('alert');
    await within(target.scrollIntoViewIfNeeded({timeout: remaining(deadline)}), deadline, 'screenshot owning anchor viewport');
    await within(svg.scrollIntoViewIfNeeded({timeout: remaining(deadline)}), deadline, 'screenshot original canvas viewport');
    await within(expect(svg).toBeVisible({timeout: remaining(deadline)}), deadline, 'screenshot actual canvas visible');
    await within(expect(svg).toBeInViewport({ratio: 1, timeout: remaining(deadline)}), deadline, 'screenshot whole original canvas in viewport');
    await within(expect(target).toBeVisible({timeout: remaining(deadline)}), deadline, 'screenshot actual owning anchor visible');
    await within(expect(target).toBeInViewport({ratio: 1, timeout: remaining(deadline)}), deadline, 'screenshot owning anchor in viewport');
    const canvas = await within(svg.boundingBox(), deadline, 'screenshot original canvas geometry');
    const owningAnchor = await within(target.boundingBox(), deadline, 'screenshot owning anchor geometry');
    expect(canvas).not.toBeNull(); expect(owningAnchor).not.toBeNull();
    screenshotGuards.push({name, anchor, canvas, owning_anchor: owningAnchor, viewport: page.viewportSize(), whole_canvas_and_anchor_in_viewport: true});
    await e.screenshot(page, name);
  };
  const fitRoute = '/api/rotated-detection/fit-box';
  const fitPositive = async (label: string) => {
    const svg = fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true});
    const delivered: number[][] = [], capture = wire(fitRoute, 'POST', r => r.postDataJSON()?.image_path === image && r.postDataJSON()?.mode === variant.mode);
    try {
      const deadline = performance.now() + 10_000;
      await within(svg.scrollIntoViewIfNeeded({timeout: remaining(deadline)}), deadline, 'original fitting viewport');
      for (const [index, [x, y]] of variant.points.entries()) {
        const b = await within(svg.boundingBox(), deadline, 'actual original SVG geometry'); expect(b).not.toBeNull();
        const scale = Math.min(b!.width / 256, b!.height / 256), left = b!.x + (b!.width - 256 * scale) / 2, top = b!.y + (b!.height - 256 * scale) / 2;
        const clientX = Math.floor(left + x * scale), clientY = Math.floor(top + y * scale);
        delivered.push([(clientX - left) / scale, (clientY - top) / scale]);
        await within(page.mouse.click(clientX, clientY), deadline, 'actual source fitting point');
        await within(expect(svg.locator('circle')).toHaveCount(index + 1, {timeout: remaining(deadline)}), deadline, 'original delivered point acknowledged');
      }
      await within(expect(svg.locator('circle')).toHaveCount(variant.points.length, {timeout: remaining(deadline)}), deadline, 'all selected-mode delivered points');
      await within(expect(fitButton()).toBeEnabled({timeout: remaining(deadline)}), deadline, 'real fitting available');
      await within(fitButton().click({timeout: remaining(deadline)}), deadline, 'ordinary fit click');
      const fitted = await body(await within(capture.reply, deadline, 'original fit-box POST'), deadline, label, capture.original());
      const actualRequest = capture.original()!.postDataJSON();
      expect(actualRequest).toEqual({image_path: image, mode: variant.mode, points: actualRequest.points, source_sha256: sourceHashes['train.png']});
      expect(actualRequest.points).toHaveLength(variant.points.length);
      for (let i = 0; i < variant.points.length; i++) for (let axis = 0; axis < 2; axis++) expect(actualRequest.points[i][axis]).toBeCloseTo(delivered[i][axis], 8);
      const [a, b, c] = delivered; expect(b[0]).toBeGreaterThan(a[0]); expect(b[1]).toBe(a[1]); expect(c[1]).toBeGreaterThan(a[1]);
      let expected: {cx: number; cy: number; width: number; height: number; angle_deg: number};
      if (variant.mode === 'face') {
        expect(c[0]).toBe(a[0]); expected = {cx: (a[0] + b[0]) / 2, cy: (a[1] + c[1]) / 2, width: b[0] - a[0], height: c[1] - a[1], angle_deg: 0};
      } else {
        const d = delivered[3]; expect(c[0]).toBe(b[0]); expect(d).toEqual([a[0], c[1]]);
        const xs = delivered.map(point => point[0]), ys = delivered.map(point => point[1]);
        const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
        expect(maxX - minX).toBeGreaterThan(maxY - minY);
        expected = {cx: (minX + maxX) / 2, cy: (minY + maxY) / 2, width: maxX - minX, height: maxY - minY, angle_deg: 0};
      }
      expect(fitted.mode).toBe(variant.mode); expect(fitted.source_size).toEqual([256, 256]); expect(fitted.source_sha256).toBe(sourceHashes['train.png']);
      // Original irregular fitter uses float32/OpenCV; expected rectangle is independently derived from delivered pointer coordinates.
      for (const [key, value] of Object.entries(expected)) expect(fitted.box[key]).toBeCloseTo(value, variant.boxPrecision);
      const radians = expected.angle_deg * Math.PI / 180, co = Math.cos(radians), si = Math.sin(radians);
      const corners = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(([sx, sy]) => [expected.cx + sx * expected.width / 2 * co - sy * expected.height / 2 * si, expected.cy + sx * expected.width / 2 * si + sy * expected.height / 2 * co]);
      const actual = [...fitted.polygon].sort((p: number[], q: number[]) => p[0] - q[0] || p[1] - q[1]), sorted = corners.sort((p, q) => p[0] - q[0] || p[1] - q[1]); expect(actual).toHaveLength(4);
      for (let i = 0; i < 4; i++) for (let axis = 0; axis < 2; axis++) expect(actual[i][axis]).toBeCloseTo(sorted[i][axis], 3);
      await within(expect(svg.locator('polygon')).toHaveCount(1, {timeout: remaining(deadline)}), deadline, 'returned proposal actually visible');
      await within(expect(append()).toBeEnabled({timeout: remaining(deadline)}), deadline, 'fresh geometric proposal appendable');
      await within(expect(fitSummary()).toHaveText(`중심 (${fitted.box.cx.toFixed(1)}, ${fitted.box.cy.toFixed(1)}) · ${fitted.box.width.toFixed(1)} × ${fitted.box.height.toFixed(1)} px · ${fitted.box.angle_deg.toFixed(1)}°`, {timeout: remaining(deadline)}), deadline, 'original returned box summary');
      await within(fitSummary().scrollIntoViewIfNeeded({timeout: remaining(deadline)}), deadline, 'original proposal summary viewport');
      await within(expect(fitSummary()).toBeVisible({timeout: remaining(deadline)}), deadline, 'original returned summary visible');
      const rendered = await within(svg.locator('circle').evaluateAll(rows => rows.map(row => [Number(row.getAttribute('cx')), Number(row.getAttribute('cy'))])), deadline, 'actual rendered source points');
      expect(rendered).toEqual(actualRequest.points);
      const renderedPolygon = await within(svg.locator('polygon').getAttribute('points', {timeout: remaining(deadline)}), deadline, 'actual rendered returned polygon');
      expect(renderedPolygon).not.toBeNull();
      const renderedCorners = renderedPolygon!.trim().split(/\s+/).map(pair => pair.split(',').map(Number));
      expect(renderedCorners).toEqual(fitted.polygon);
      return {delivered, rendered, rendered_corners: renderedCorners, actual_fit_request: actualRequest, independently_expected_box: expected, fitted};
    } finally {capture.close();}
  };
  try {
    const originalFit = await fitPositive('original-' + variant.mode + '-fit-before-source-change');
    expect(await table().inputValue()).toBe(initialTable); expect(protectedTrees()).toEqual(before);
    await proposalScreenshot('f029-' + variant.mode + '-before-source-change-unsent-proposal', 'proposal');
    const capture = wire(fitRoute, 'POST', r => r.postDataJSON()?.image_path === image && r.postDataJSON()?.mode === variant.mode);
    let refusal: any, failedFitRequest: any;
    try {
      const deadline = performance.now() + 10_000;
      changedPin = replaceOwnedImage(imagePin, changedImageBytes); imagePin = changedPin; restoreNeeded = true;
      expect(changedPin.sha256).toBe(alternatePin.sha256); expect(changedPin.sha256).not.toBe(preview.source_sha256);
      protectedAt(changedPin);
      await within(fitButton().click({timeout: remaining(deadline)}), deadline, 'ordinary stale-preview refit');
      refusal = await body(await within(capture.reply, deadline, 'original changed-source 422'), deadline, 'actual-source-changed-refusal', capture.original(), 422);
      expect(refusal).toEqual({detail: 'Fitting source changed after preview'});
      failedFitRequest = capture.original()!.postDataJSON(); expect(failedFitRequest).toEqual(originalFit.actual_fit_request);
      expect(failedFitRequest.source_sha256).toBe(preview.source_sha256); expect(failedFitRequest.source_sha256).not.toBe(changedPin.sha256);
      await within(expect(fitting().getByRole('alert')).toHaveText(refusal.detail, {timeout: remaining(deadline)}), deadline, 'exact original owning refusal');
      await within(expect(fitting().getByRole('alert')).toBeVisible({timeout: remaining(deadline)}), deadline, 'refusal visible');
      await within(expect(append()).toBeDisabled({timeout: remaining(deadline)}), deadline, 'old successful proposal cannot append after refit refusal');
      await within(expect(fitting().locator('polygon')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'previous proposal retired');
      await within(expect(fitButton()).toBeEnabled({timeout: remaining(deadline)}), deadline, 'original action is no longer busy');
      expect(await table().inputValue()).toBe(initialTable); protectedAt(changedPin);
      // The original response has completed within its unchanged deadline.
      // Retire its listener/timer before the separate visual action frame.
      capture.close();
      await proposalScreenshot('f029-' + variant.mode + '-source-changed-owning-refusal-no-append', 'error');
    } finally {capture.close();}
    restoredPin = replaceOwnedImage(imagePin, originalImageBytes); imagePin = restoredPin; restoreNeeded = false;
    expect(restoredPin.sha256).toBe(sourceHashes['train.png']); expect(restoredPin.raw9[4]).toBe((before.source['train.png'] as FilePin).raw9[4]);
    protectedAt(restoredPin);
    // A normal selection transition obtains a new original preview; no API is
    // called to refresh or bypass the rejected preview digest.
    const recoveryPreviewCapture = wire('/api/rotated-detection/fit-source', 'GET', (_r, u) => u.searchParams.get('image_path') === image);
    let recoveryPreview: any;
    try {
      const deadline = performance.now() + 10_000, selector = fitting().getByRole('combobox', {name: '이미지', exact: true});
      await within(selector.selectOption('', {timeout: remaining(deadline)}), deadline, 'ordinary deselection');
      await within(expect(fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true})).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'old preview removed');
      await within(selector.selectOption(image, {timeout: remaining(deadline)}), deadline, 'ordinary restored original selection');
      recoveryPreview = await body(await within(recoveryPreviewCapture.reply, deadline, 'fresh original preview GET'), deadline, 'restored-source-fresh-preview', recoveryPreviewCapture.original());
      expect(recoveryPreview.source_sha256).toBe(sourceHashes['train.png']); expect(recoveryPreview.source_size).toEqual([256, 256]);
      expect(recoveryPreview.preview_data_url).toBe(preview.preview_data_url);
      await within(expect(fitting().getByRole('img', {name: '회전 박스 정답 그리기', exact: true}).locator('image')).toHaveAttribute('href', recoveryPreview.preview_data_url, {timeout: remaining(deadline)}), deadline, 'fresh original preview rendered');
      await within(expect(fitting().getByRole('alert')).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'normal selection cleared old error');
    } finally {recoveryPreviewCapture.close();}
    const recoveredFit = await fitPositive('restored-source-' + variant.mode + '-fit-success');
    expect(await table().inputValue()).toBe(initialTable); protectedAt(restoredPin);
    await proposalScreenshot('f029-' + variant.mode + '-restored-source-fresh-preview-fit-recovered', 'proposal');
    const trusted = await page.evaluate(() => (window as any).__f029SourceChange.clicks);
    expect(trusted).toEqual(Array.from({length: 3}, () => ({text: '회전 박스 맞추기', trusted: true})));
    await open('real-reload-own-saved-manifest-after-source-recovery', true);
    expect(await table().inputValue()).toBe(initialTable); protectedAt(restoredPin);
    await expect(fitting().getByRole('alert')).toHaveCount(0); await expect(fitButton()).toBeDisabled(); await expect(append()).toBeDisabled();
    expect(writes).toEqual([originalFit.actual_fit_request, failedFitRequest, recoveredFit.actual_fit_request].map(value => ({method: 'POST', route: fitRoute, body: value})));
    expect(pageErrors).toEqual([]); expect(callbackErrors).toEqual([]);
    expect(non2xx).toEqual([{method: 'POST', route: fitRoute, status: 422}]);
    expect(consoleErrors).toHaveLength(1); expect(consoleErrors[0].text).toBe('Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)');
    expect(consoleErrors[0].location.url).toBe(renderer.origin + fitRoute);
    const tableViewportDeadline = performance.now() + 10_000;
    await within(table().scrollIntoViewIfNeeded({timeout: remaining(tableViewportDeadline)}), tableViewportDeadline, 'saved table screenshot viewport');
    await within(expect(table()).toBeVisible({timeout: remaining(tableViewportDeadline)}), tableViewportDeadline, 'saved table actually visible');
    await within(expect(table()).toBeInViewport({ratio: 1, timeout: remaining(tableViewportDeadline)}), tableViewportDeadline, 'saved table in actual screenshot viewport');
    const visibleTableBox = await within(table().boundingBox(), tableViewportDeadline, 'saved table screenshot geometry'); expect(visibleTableBox).not.toBeNull();
    screenshotGuards.push({name: 'f029-' + variant.mode + '-source-error-recovery-real-reload-saved-truth', anchor: 'saved-table', table: visibleTableBox, viewport: page.viewportSize(), saved_table_in_viewport: true});
    await e.screenshot(page, 'f029-' + variant.mode + '-source-error-recovery-real-reload-saved-truth');
    proof = {schema: 'modu-vision.F029-mode-fit-source-change-error/v1', mode: variant.mode, cells: ['F029.obb-native-' + variant.mode + '-fit-save-reopen.error'], source_head: head,
      project, source, source_hashes: sourceHashes, saved_manifest: saved, initial_table: initialTable, preview, original_fit: originalFit, refusal,
      failed_fit_request: failedFitRequest, recovery_preview: recoveryPreview, recovered_fit: recoveredFit, trusted_clicks: trusted, screenshot_viewport_guards: screenshotGuards,
      intentional_source_mutation: {image, original: before.source['train.png'], alternate_fixture: {path: alternate, pin: alternatePin}, changed: changedPin, restored: restoredPin,
        original_bytes_restored: true, inode_mode_uid_gid_nlink_preserved: true, historical_mtime_ctime_restored: false,
        intentional_phase_deltas: ['content bytes and SHA256', 'file size', 'mtime_ns', 'ctime_ns'], final_allowed_deltas: ['mtime_ns', 'ctime_ns']},
      source_other_files_and_directories_raw9_preserved: true, project_prepared_all_raw9_preserved: true,
      protected_before: before, protected_after: protectedAt(restoredPin), same_saved_table_after_refusal_recovery_and_reload: true,
      old_successful_proposal_removed_on_refit_refusal: true, fresh_preview_and_successful_fit_after_exact_byte_restoration: true,
      after_baseline_nonGET_requests: writes.slice(), persistent_API_mutations_after_baseline: 0, full_request_context_header_equality: true,
      original_UI_responses: proofs, errors: {page: pageErrors, console: consoleErrors, non2xx, callbacks: callbackErrors}, expected_original_HTTP_refusal: 422,
      unexpected_errors: 0, training_started: false, actual_model_inference: false, fitting_scope: 'Original PIL preview and geometric fit; one owned synthetic source byte replacement and exact-body restoration',
      native_or_installed_GUI_claim: false, quality_approved: false, parent_completion_credit: false, registry_credit: false};
  } catch (error) {failed = true; primary = error;}
  finally {
    if (restoreNeeded) {
      try {restoredPin = replaceOwnedImage(imagePin, originalImageBytes); imagePin = restoredPin; restoreNeeded = false;}
      catch (error) {secondary.push('owned source restoration: ' + String(error)); if (!failed) {failed = true; primary = error;}}
    }
    for (const [label, cleanup] of [['request observer', () => page.off('request', observe)], ['page-error observer', () => page.off('pageerror', pageError)],
      ['console observer', () => page.off('console', consoleError)], ['response observer', () => page.off('response', failedResponse)],
      ['protected trees', () => {finalImagePin = file(image); expect(finalImagePin.sha256).toBe(sourceHashes['train.png']); expect(finalImagePin).toEqual(imagePin); protectedAt(finalImagePin); expect(file(alternate)).toEqual(alternatePin);}]] as const) {
      try {cleanup();} catch (error) {secondary.push(label + ': ' + String(error)); if (!failed) {failed = true; primary = error;}}
    }
    e.note('f029_mode_fit_source_error_progress', {proof, original_UI_responses: proofs, writes, screenshot_viewport_guards: screenshotGuards,
      source_phase_pins: {original: before.source['train.png'], changed: changedPin, restored: restoredPin, actual_final: finalImagePin}, failed_write_readbacks: failedWriteReadbacks,
      errors: {page: pageErrors, console: consoleErrors, non2xx, callbacks: callbackErrors}});
    e.note('f029_mode_fit_source_error_closure', {FD_custody: custody, owned_source_bytes_restored: finalImagePin?.sha256 === sourceHashes['train.png'], restoration_protocol_completed: !restoreNeeded,
      source_other_files_and_project_prepared_preserved: !secondary.some(row => row.startsWith('protected trees:')), secondary, first_exception: failed ? String(primary) : null});
  }
  if (failed) throw primary; expect(custody.opened).toBe(custody.closed); expect(custody.uncertain).toEqual([]); e.note('f029_mode_fit_source_error', proof);
});
}

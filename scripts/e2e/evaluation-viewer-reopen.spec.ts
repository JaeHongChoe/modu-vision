import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Locator, Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

const harness = require('./fixtures/harness.cjs');
test.use({actionTimeout: 10_000});
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!fs.existsSync(root)) return result;
  const visit = (directory: string) => {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true})) {
      const file = path.join(directory, entry.name);
      if (entry.isSymbolicLink()) throw new Error('Protected annotation fixture contains a link');
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile()) result[path.relative(root, file)] = fileSha(file);
    }
  };
  visit(root);
  return result;
}

async function raster(image: Locator) {
  const decoded = await image.evaluate(async node => {
    const img = node as HTMLImageElement;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const context = canvas.getContext('2d')!;
    context.drawImage(img, 0, 0);
    return {size: [canvas.width, canvas.height], pixels: Array.from(context.getImageData(0, 0, canvas.width, canvas.height).data)};
  });
  expect(decoded.size).toEqual([160, 96]);
  return {size: decoded.size, rgba_sha256: sha(Buffer.from(decoded.pixels))};
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(workspace.root, 'reopen-source'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png');
  execFileSync(harness.resolvePython(), ['-c', "import sys,numpy as np;from PIL import Image;y,x=np.indices((96,160));Image.fromarray(np.dstack([x,y,np.full_like(x,60)]).astype(np.uint8)).save(sys.argv[1])", original], {timeout: 30_000});
  const originalHash = fileSha(original);
  const project = await api('/api/project/create', {name: 'Saved overlay reopen fixture', task: 'segmentation'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation'});
  await api('/api/project/labelsets', {name: 'Reopen second labelset'});
  const sets = await api('/api/project/labelsets');
  const secondSet = sets.labelsets.find((entry: any) => entry.id !== 'default').id;
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/pixel_evaluation_reports.py'), workspace.root, project.project_dir, source, secondSet, 'bound'], {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const item = fixture.items.find((entry: any) => entry.labelset_id === 'default' && entry.variant === 'valid');
  // The existing readiness GET initializes default team settings on first read.
  // Initialize that fixture state before freezing the complete metadata bytes.
  const teamBefore = await api('/api/team-data');
  expect(teamBefore).toMatchObject({book: null, book_history: [], settings: {revision: 1, editing_enabled: false, review_enabled: false}});
  const annotationRoute = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  const annotationBefore = await api(annotationRoute);
  const annotationsBefore = tree(project.annotations_dir);
  const protectedBefore = Object.fromEntries([original, ...fixture.items.map((entry: any) => entry.report_path), ...fixture.inputs.map((entry: any) => entry.path)].map(file => [file, fileSha(file)]));
  const protectedReceipt = path.join(workspace.logs, 'saved-overlay-protected-before.json');
  fs.writeFileSync(protectedReceipt, JSON.stringify({protectedBefore, annotationBefore, annotationsBefore, teamBefore}, null, 2));
  evidence.addFile(protectedReceipt);
  for (const relative of Object.keys(annotationsBefore)) {
    const snapshot = path.join(workspace.logs, 'annotation-before', relative);
    fs.mkdirSync(path.dirname(snapshot), {recursive: true});
    fs.copyFileSync(path.join(project.annotations_dir, relative), snapshot);
    evidence.addFile(snapshot);
  }
  const writes: string[] = [];
  page.on('request', request => {
    const route = new URL(request.url()).pathname;
    if (request.method() !== 'GET' && /\/(evaluation|train|jobs)(\/|$)|\/api\/annotations\/save|\/api\/dataset\/(metadata\/(bulk|split)|formats\/import|masks\/import)$/.test(route)) writes.push(`${request.method()} ${route}`);
  });
  const openHistory = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText('Saved overlay reopen fixture');
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(3).click();
    const summary = page.locator('summary').filter({hasText: '평가 이력 · 제품/Lot별 오류'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    const history = summary.locator('..');
    await history.getByLabel('평가 라벨 세트', {exact: true}).selectOption('default');
    const selector = history.getByLabel(/^모델별 저장 평가/);
    await expect.poll(() => selector.locator('option').evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value).sort())).toEqual([item.record.evaluation_id]);
    await selector.selectOption(item.record.evaluation_id);
    await expect(selector).toHaveValue(item.record.evaluation_id);
    const details = history.locator('details').filter({has: page.locator('summary').filter({hasText: '객체·픽셀·문자 오류와 분포 분석'})}).first();
    if (await details.getAttribute('open') === null) await details.locator('summary').first().click();
    await expect(details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true})).toBeEnabled();
    return details;
  };
  const viewer = page.getByRole('dialog', {name: '이미지 판정 근거 보기', exact: true});
  const openViewer = async (details: Locator) => {
    await details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true}).click();
    await expect(viewer).toBeVisible();
    await expect(viewer).toContainText(item.record.evaluation_id);
    await expect(viewer).toContainText(originalHash);
    await expect(viewer.getByLabel('근거 이미지 종류', {exact: true})).toHaveValue('original');
    await raster(viewer.getByRole('img', {name: '평가 입력 원본', exact: true}));
  };
  const truth = async () => {
    await viewer.getByLabel('근거 겹침 이미지', {exact: true}).selectOption('truth');
    await expect(viewer.getByLabel('근거 겹침 투명도', {exact: true})).toBeEnabled();
    return raster(viewer.locator('img').nth(1));
  };

  let details = await openHistory();
  await expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('all');
  await openViewer(details); const allBefore = await truth();
  await viewer.getByRole('button', {name: '저장 평가로 돌아가기', exact: true}).click();
  await expect(viewer).toHaveCount(0);
  await details.getByLabel('평가 증거 클래스', {exact: true}).selectOption('Scratch');
  await openViewer(details); const scratchBefore = await truth();
  expect(scratchBefore.rgba_sha256).not.toBe(allBefore.rgba_sha256);
  const opacity = viewer.getByLabel('근거 겹침 투명도', {exact: true});
  await opacity.focus(); for (let i = 0; i < 3; i++) await page.keyboard.press('ArrowRight');
  await expect(opacity).toHaveValue('0.65');
  await viewer.getByLabel('근거 이미지 확대', {exact: true}).click();
  await expect(viewer.getByRole('status')).toHaveText('125%');
  const area = viewer.getByLabel('근거 이미지 이동 영역', {exact: true});
  const bounds = (await area.boundingBox())!;
  await page.mouse.move(bounds.x + 80, bounds.y + 80); await page.mouse.down();
  await page.mouse.move(bounds.x + 120, bounds.y + 105); await page.mouse.up();
  await expect(area.locator('div').first()).toHaveAttribute('style', /translate\(40px, 25px\) scale\(1.25\)/);
  await evidence.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-overlay-before-close`);
  await page.keyboard.press('Escape'); await expect(viewer).toHaveCount(0);
  await expect(details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true})).toBeFocused();
  await expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('Scratch');
  await openViewer(details);
  await expect(opacity).toHaveValue('0.5');
  await expect(viewer.getByRole('status')).toHaveText('100%');
  await expect(area.locator('div').first()).toHaveAttribute('style', /translate\(0px, 0px\) scale\(1\)/);
  await expect(viewer.getByLabel('근거 겹침 이미지', {exact: true})).toHaveValue('');
  const scratchAfter = await truth(); expect(scratchAfter).toEqual(scratchBefore);
  await expect(opacity).toHaveValue('0.5');
  await evidence.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-overlay-reopened-reset`);
  await viewer.getByRole('button', {name: '저장 평가로 돌아가기', exact: true}).click();
  await expect(viewer).toHaveCount(0);
  await details.getByLabel('평가 증거 클래스', {exact: true}).selectOption('Crack');
  await expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('Crack');
  details = await openHistory(); // Actual navigation/reload remounts the evaluation panel.
  await expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('all');
  await openViewer(details); const allAfter = await truth(); expect(allAfter).toEqual(allBefore);
  await evidence.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-class-reopened-all`);
  await viewer.getByRole('button', {name: '저장 평가로 돌아가기', exact: true}).click();
  await expect(viewer).toHaveCount(0);
  expect(await api(annotationRoute)).toEqual(annotationBefore);
  expect(await api('/api/team-data')).toEqual(teamBefore);
  expect(tree(project.annotations_dir)).toEqual(annotationsBefore);
  for (const [file, digest] of Object.entries(protectedBefore)) {expect(fileSha(file)).toBe(digest); evidence.addFile(file);}
  expect(writes).toEqual([]);
  evidence.note('saved_overlay_reopen', {evaluation_id: item.record.evaluation_id, report_sha256: item.report_sha256, labelset_id: 'default', original_path: original, original_sha256: originalHash, source_size: [160, 96], allBefore, allAfter, scratchBefore, scratchAfter, protected_before: protectedBefore, annotation_before: annotationBefore, annotation_files_before: annotationsBefore, writes,
    scenario_cells: ['U011.native-saved-overlay-opacity.reopen', 'U011.native-saved-overlay-zoom.reopen', 'U011.native-saved-overlay-mouse-pan.reopen', 'F051.native-saved-overlay-class-selector.reopen'],
    actual_close_remount_reset: true, actual_class_reload_reset: true, source_electron: sourceElectron, controlled_saved_reports_not_model_inference: true, human_labels_applied: false, frozen_native_acceptance: false, installed_target_acceptance: false, device_acceptance: false, quality_acceptance: false});
}

test('saved overlay reopen resets opacity zoom pan and scoped class without modifying evidence', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {const response = await request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();};
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('source Electron saved overlay reopen resets opacity zoom pan and scoped class without modifying evidence', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})}); if (!response.ok) throw Error(`Owned reopen API HTTP ${response.status}`); return response.json();}, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

// Source-only U011 display controls. Root alone binds and executes this case.
import type {Request as ViewerDisplayRequest} from '@playwright/test';
import {handoffApi as viewerDisplayApi, handoffNamespace as viewerDisplayNamespace, handoffWithin as viewerDisplayWithin} from './fixtures/remaining-project-handoff';

const VIEWER_DISPLAY_CELLS = ['U011.native-saved-overlay-opacity.handoff', 'U011.native-saved-overlay-zoom.handoff', 'U011.native-saved-overlay-mouse-pan.handoff'];
type ViewerDisplayScope = {tag: 'A' | 'B'; root: string; source: string; original: string; originalSha: string; blue: number; project: any; fixture: any; item: any};
const viewerDisplayRemaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));

function viewerDisplaySave(evidence: Evidence, workspace: Workspace, label: string, value: unknown) {
  const file = path.join(workspace.logs, label + '.json'), bytes = Buffer.isBuffer(value) ? value : Buffer.from(JSON.stringify(value, null, 2));
  const fd = fs.openSync(file, fs.constants.O_WRONLY | fs.constants.O_CREAT | fs.constants.O_EXCL | fs.constants.O_NOFOLLOW, 0o600);
  let failed = false, primary: unknown;
  try {expect(fs.writeSync(fd, bytes)).toBe(bytes.length); fs.fsyncSync(fd);}
  catch (error) {failed = true; primary = error;}
  finally {try {fs.closeSync(fd);} catch (error) {if (!failed) {failed = true; primary = error;}}}
  if (failed) throw primary;
  evidence.addFile(file); return {path: file, size: bytes.length, sha256: sha(bytes)};
}

async function viewerDisplayMake(workspace: Workspace, api: Api, tag: 'A' | 'B'): Promise<ViewerDisplayScope> {
  const root = path.join(workspace.root, 'viewer-display-' + tag); fs.mkdirSync(root);
  const source = path.join(root, 'source'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'), blue = tag === 'A' ? 60 : 160;
  execFileSync(harness.resolvePython(), ['-c', "import sys,numpy as np;from PIL import Image;y,x=np.indices((96,160));Image.fromarray(np.dstack([x,y,np.full_like(x,int(sys.argv[2]))]).astype(np.uint8)).save(sys.argv[1])", original, String(blue)], {timeout: 30_000});
  const made = await api('/api/project/create', {name: 'Viewer display handoff ' + tag, task: 'segmentation', project_dir: path.join(root, 'project')});
  expect(made.project_dir).toBe(path.join(root, 'project'));
  expect(fs.realpathSync(made.project_dir)).toBe(made.project_dir); expect(fs.lstatSync(made.project_dir).isSymbolicLink()).toBe(false);
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation'});
  await api('/api/project/labelsets', {name: 'Viewer display second labelset ' + tag});
  const sets = await api('/api/project/labelsets'), secondSet = sets.labelsets.find((entry: any) => entry.id !== 'default').id;
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/pixel_evaluation_reports.py'), root, made.project_dir, source, secondSet, 'bound'], {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const item = fixture.items.find((entry: any) => entry.labelset_id === 'default' && entry.variant === 'valid');
  expect(item).toBeTruthy(); expect(item.record.binding).toMatchObject({source_dataset_path: source, task: 'segmentation', labelset_id: 'default'});
  expect(item.record.result.fixture_kind).toBe('controlled_display_only_no_model_inference');
  expect(item.record.result.test_predictions[0]).toMatchObject({file_path: original, image_sha256: fileSha(original), pixel_evidence: {shape: [64, 64], coordinate_space: 'model_input_px', mapping: {kind: 'full_image_resize', source_size: [160, 96]}}});
  await api('/api/team-data'); await api('/api/team-data/readiness'); await api('/api/annotations/part?file_path=' + encodeURIComponent(original));
  const project = await api('/api/project/current'); expect(project.id).toBe(made.id); expect(project.source_dataset_dir).toBe(source);
  return {tag, root, source, original, originalSha: fileSha(original), blue, project, fixture, item};
}

function viewerDisplayCustody(scope: ViewerDisplayScope) {
  const roots = {source: scope.source, annotations: scope.project.annotations_dir, reports: scope.project.reports_dir, models: scope.project.models_dir,
    dataset: scope.project.dataset_dir, labelsets: path.join(scope.project.project_dir, 'labelsets'), controlled_inputs: path.join(scope.root, 'controlled-evaluation-inputs')};
  const files = [path.join(scope.project.project_dir, 'project.json'), path.join(scope.project.project_dir, 'labelsets.json')];
  return {project_id: scope.project.id, evaluation_id: scope.item.record.evaluation_id,
    roots: Object.fromEntries(Object.entries(roots).map(([kind, root]) => [kind, {root, snapshot: viewerDisplayNamespace(root)}])),
    files: Object.fromEntries(files.map(file => [file, {size: fs.statSync(file).size, sha256: fileSha(file)}]))};
}

async function viewerDisplayProject(page: Page, scope: ViewerDisplayScope, origin: string, evidence: Evidence, workspace: Workspace, label: string) {
  const deadline = performance.now() + 10_000;
  const teamClose = page.getByRole('button', {name: '팀 데이터 작업 닫기', exact: true});
  if (await viewerDisplayWithin(teamClose.isVisible(), deadline, 'visible team dialog')) {
    await viewerDisplayWithin(teamClose.click(), deadline, 'ordinary team dialog close');
    await viewerDisplayWithin(expect(teamClose).toHaveCount(0, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'team dialog closed');
  }
  await viewerDisplayWithin(page.getByTitle('프로젝트 관리', {exact: true}).click(), deadline, 'project manager');
  const dialog = page.getByRole('dialog', {name: '프로젝트 관리', exact: true});
  await viewerDisplayWithin(dialog.getByRole('button', {name: '최근 프로젝트', exact: true}).click(), deadline, 'ordinary recent projects');
  const item = dialog.getByRole('button').filter({has: page.locator('span[title]').filter({hasText: scope.project.project_dir})});
  await viewerDisplayWithin(expect(item).toHaveCount(1, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'one owning project');
  await viewerDisplayWithin(expect(item).toBeEnabled({timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning project enabled');
  const wire = page.waitForResponse(response => {const request = response.request(), address = new URL(response.url());
    return request.frame() === page.mainFrame() && request.method() === 'POST' && address.origin === origin && address.pathname === '/api/project/open' && request.postDataJSON()?.project_dir === scope.project.project_dir;
  }, {timeout: viewerDisplayRemaining(deadline)});
  void wire.catch(() => undefined); // Immediate rejection ownership; the same wire is awaited below.
  await viewerDisplayWithin(item.click(), deadline, 'ordinary owning project click');
  const response = await viewerDisplayWithin(wire, deadline, 'owning project response'), bytes = await viewerDisplayWithin(response.body(), deadline, 'complete project response');
  expect(response.status()).toBe(200); expect(bytes.length).toBeLessThanOrEqual(1024 * 1024);
  expect(response.request().postDataJSON()).toEqual({project_dir: scope.project.project_dir}); expect(JSON.parse(bytes.toString('utf8'))).toEqual(scope.project);
  expect(await viewerDisplayWithin(response.finished(), deadline, 'project request completed')).toBeNull();
  await viewerDisplayWithin(expect(dialog).toHaveCount(0, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'project dialog closed');
  await viewerDisplayWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(scope.project.name, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning project header');
  const receipt = viewerDisplaySave(evidence, workspace, label + '-actual-project-open', bytes);
  return {project_id: scope.project.id, project_dir: scope.project.project_dir, method: 'POST', origin, pathname: '/api/project/open', status: 200, actual_main_frame: true, completed: true, raw_response: receipt};
}

async function viewerDisplayHistory(page: Page, scope: ViewerDisplayScope) {
  const deadline = performance.now() + 10_000;
  await viewerDisplayWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(scope.project.name, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning header');
  await viewerDisplayWithin(page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(3).click(), deadline, 'ordinary evaluation stage');
  const summary = page.locator('summary').filter({hasText: '평가 이력 · 제품/Lot별 오류'}), history = summary.locator('..');
  if (await viewerDisplayWithin(history.getAttribute('open'), deadline, 'history disclosure state') === null) await viewerDisplayWithin(summary.click(), deadline, 'history disclosure');
  await viewerDisplayWithin(history.getByLabel('평가 이력 모델 종류', {exact: true}).selectOption('segmentation'), deadline, 'segmentation history');
  await viewerDisplayWithin(history.getByLabel('평가 라벨 세트', {exact: true}).selectOption('default'), deadline, 'default labelset');
  const selector = history.getByLabel(/^모델별 저장 평가/);
  await viewerDisplayWithin(expect.poll(() => selector.locator('option').evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value).filter(Boolean).sort()), {timeout: viewerDisplayRemaining(deadline)}).toEqual([scope.item.record.evaluation_id]), deadline, 'exact owning saved report catalog');
  await viewerDisplayWithin(selector.selectOption(scope.item.record.evaluation_id), deadline, 'owning saved evaluation');
  await viewerDisplayWithin(expect(selector).toHaveValue(scope.item.record.evaluation_id, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning evaluation identity');
  const details = history.locator('details').filter({has: page.locator('summary').filter({hasText: '객체·픽셀·문자 오류와 분포 분석'})}).first();
  if (await viewerDisplayWithin(details.getAttribute('open'), deadline, 'evidence disclosure state') === null) await viewerDisplayWithin(details.locator('summary').first().click(), deadline, 'evidence disclosure');
  await viewerDisplayWithin(expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('all', {timeout: viewerDisplayRemaining(deadline)}), deadline, 'fresh all-class scope');
  return details;
}

function viewerDisplayPixels(blue: number, layer: 'original' | 'truth' | 'prediction' | 'error') {
  const result: number[] = [];
  for (let y = 0; y < 96; y++) for (let x = 0; x < 160; x++) {
    if (layer === 'original') {result.push(x, y, blue, 255); continue;}
    const mx = Math.floor(x * 64 / 160), my = Math.floor(y * 64 / 96);
    const truth = mx >= 8 && mx < 24 && my >= 8 && my < 24 ? 7 : mx >= 32 && mx < 48 && my >= 16 && my < 40 ? 23 : 0;
    const predicted = mx >= 12 && mx < 28 && my >= 8 && my < 24 ? 7 : mx >= 32 && mx < 48 && my >= 20 && my < 44 ? 23 : 0;
    const rgb = layer === 'truth' ? (truth ? [74, 222, 128] : [35, 35, 35]) : layer === 'prediction' ? (predicted ? [34, 211, 238] : [35, 35, 35]) : [truth && truth !== predicted ? 244 : 35, predicted && truth !== predicted ? 180 : 35, predicted && truth !== predicted ? 255 : 35];
    result.push(...(rgb.every(value => value === 35) ? [0, 0, 0, 0] : [...rgb, 255]));
  }
  return result;
}

async function viewerDisplayRaster(image: Locator, expected: number[], deadline: number) {
  const decoded = await viewerDisplayWithin(image.evaluate(async node => {
    const img = node as HTMLImageElement; await img.decode(); const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth; canvas.height = img.naturalHeight; const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0);
    return {size: [canvas.width, canvas.height], pixels: Array.from(ctx.getImageData(0, 0, canvas.width, canvas.height).data)};
  }), deadline, 'complete decoded raster');
  expect(decoded.size).toEqual([160, 96]); expect(decoded.pixels).toEqual(expected);
  return {size: decoded.size, rgba_sha256: sha(Buffer.from(decoded.pixels))};
}

async function viewerDisplayOpen(page: Page, scope: ViewerDisplayScope, details: Locator, origin: string, evidence: Evidence, workspace: Workspace, label: string) {
  const deadline = performance.now() + 10_000, opener = details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true});
  await viewerDisplayWithin(expect(opener).toBeEnabled({timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning raster ready');
  const wire = page.waitForResponse(response => {const request = response.request(), address = new URL(response.url());
    return request.frame() === page.mainFrame() && request.method() === 'GET' && address.origin === origin && address.pathname === '/api/evaluation/history/' + scope.item.record.evaluation_id + '/evidence-image'
      && address.searchParams.get('source_dataset_path') === scope.source && address.searchParams.get('task') === 'segmentation' && address.searchParams.get('image_path') === scope.original;
  }, {timeout: viewerDisplayRemaining(deadline)});
  void wire.catch(() => undefined);
  await viewerDisplayWithin(opener.click(), deadline, 'open owning source Electron viewer');
  const response = await viewerDisplayWithin(wire, deadline, 'actual owning preview'), bytes = await viewerDisplayWithin(response.body(), deadline, 'complete preview bytes');
  expect(response.status()).toBe(200); expect(bytes.length).toBeLessThanOrEqual(1024 * 1024); expect(await viewerDisplayWithin(response.finished(), deadline, 'preview completed')).toBeNull();
  expect(JSON.parse(bytes.toString('utf8'))).toMatchObject({evaluation_id: scope.item.record.evaluation_id, image_path: scope.original, image_sha256: scope.originalSha, original_size: [160, 96], read_only: true});
  const headers = response.request().headers(); expect(headers['x-vision-project']).toBe(scope.project.id); expect(JSON.parse(headers['x-vision-context']).project_id).toBe(scope.project.id);
  const rawResponse = viewerDisplaySave(evidence, workspace, label + '-actual-preview', bytes);
  const viewer = page.getByRole('dialog', {name: '이미지 판정 근거 보기', exact: true});
  await viewerDisplayWithin(expect(viewer).toBeVisible({timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning viewer visible');
  await viewerDisplayWithin(expect(viewer).toContainText(scope.item.record.evaluation_id, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning saved evaluation identity');
  await viewerDisplayWithin(expect(viewer).toContainText(scope.originalSha, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning source SHA');
  await viewerDisplayWithin(expect(viewer.getByLabel('근거 이미지 종류', {exact: true})).toHaveValue('original', {timeout: viewerDisplayRemaining(deadline)}), deadline, 'original selected');
  await viewerDisplayWithin(expect(viewer.getByRole('status')).toHaveText('100%', {timeout: viewerDisplayRemaining(deadline)}), deadline, 'new viewer zoom reset');
  const area = viewer.getByLabel('근거 이미지 이동 영역', {exact: true});
  await viewerDisplayWithin(expect(area.locator('div').first()).toHaveAttribute('style', /translate\(0px, 0px\) scale\(1\)/, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'new viewer pan reset');
  await viewerDisplayWithin(expect(viewer.getByLabel('근거 겹침 이미지', {exact: true})).toHaveValue('', {timeout: viewerDisplayRemaining(deadline)}), deadline, 'new viewer overlay reset');
  const rasters: Record<string, {size: number[]; rgba_sha256: string}> = {};
  rasters.original = await viewerDisplayRaster(viewer.getByRole('img', {name: '평가 입력 원본', exact: true}), viewerDisplayPixels(scope.blue, 'original'), deadline);
  for (const [layer, name] of [['truth', '겹침 정답 마스크'], ['prediction', '겹침 예측 마스크'], ['error', '겹침 미검·과검 마스크']] as const) {
    await viewerDisplayWithin(viewer.getByLabel('근거 겹침 이미지', {exact: true}).selectOption(layer), deadline, 'ordinary ' + layer + ' overlay');
    await viewerDisplayWithin(expect(viewer.getByLabel('근거 겹침 투명도', {exact: true})).toBeEnabled({timeout: viewerDisplayRemaining(deadline)}), deadline, 'decoded owning overlay');
    rasters[layer] = await viewerDisplayRaster(viewer.getByRole('img', {name, exact: true}), viewerDisplayPixels(scope.blue, layer), deadline);
  }
  await viewerDisplayWithin(viewer.getByLabel('근거 겹침 이미지', {exact: true}).selectOption('truth'), deadline, 'return to ordinary truth overlay');
  await viewerDisplayWithin(expect(viewer.getByLabel('근거 겹침 투명도', {exact: true})).toBeEnabled({timeout: viewerDisplayRemaining(deadline)}), deadline, 'truth overlay decoded');
  await viewerDisplayWithin(expect(viewer.getByLabel('근거 겹침 투명도', {exact: true})).toHaveValue('0.5', {timeout: viewerDisplayRemaining(deadline)}), deadline, 'new viewer opacity reset');
  return {viewer, opener, rasters, proof: {project_id: scope.project.id, evaluation_id: scope.item.record.evaluation_id, source_path: scope.original, source_sha256: scope.originalSha, actual_main_frame: true, method: 'GET', origin, pathname: new URL(response.url()).pathname, status: 200, completed: true, project_header: headers['x-vision-project'], context: JSON.parse(headers['x-vision-context']), raw_response: rawResponse}};
}

async function viewerDisplayChange(page: Page, viewer: Locator, owning: 'A' | 'B') {
  const deadline = performance.now() + 10_000, opacity = viewer.getByLabel('근거 겹침 투명도', {exact: true});
  await viewerDisplayWithin(opacity.focus(), deadline, 'ordinary opacity input focus');
  for (let i = 0; i < 3; i++) await viewerDisplayWithin(page.keyboard.press(owning === 'A' ? 'ArrowRight' : 'ArrowLeft'), deadline, 'ordinary opacity step');
  const opacityValue = owning === 'A' ? '0.65' : '0.35', zoomValue = owning === 'A' ? '125%' : '80%';
  await viewerDisplayWithin(expect(opacity).toHaveValue(opacityValue, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'actual nondefault opacity');
  await viewerDisplayWithin(viewer.getByLabel(owning === 'A' ? '근거 이미지 확대' : '근거 이미지 축소', {exact: true}).click(), deadline, 'ordinary owning zoom');
  await viewerDisplayWithin(expect(viewer.getByRole('status')).toHaveText(zoomValue, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'actual nondefault zoom');
  const area = viewer.getByLabel('근거 이미지 이동 영역', {exact: true}), bounds = await viewerDisplayWithin(area.boundingBox(), deadline, 'real pan viewport bounds'); expect(bounds).not.toBeNull();
  const delta = owning === 'A' ? [40, 25] : [-30, -15];
  let down = false, failed = false, primary: unknown;
  try {
    await viewerDisplayWithin(page.mouse.move(bounds!.x + 80, bounds!.y + 80), deadline, 'actual pointer start');
    down = true; await viewerDisplayWithin(page.mouse.down(), deadline, 'actual pointer down');
    await viewerDisplayWithin(page.mouse.move(bounds!.x + 80 + delta[0], bounds!.y + 80 + delta[1]), deadline, 'actual pointer move');
  } catch (error) {failed = true; primary = error;}
  finally {if (down) {try {await viewerDisplayWithin(page.mouse.up(), deadline, 'same pointer up');} catch (error) {if (!failed) {failed = true; primary = error;}}}}
  if (failed) throw primary;
  const transform = owning === 'A' ? /translate\(40px, 25px\) scale\(1.25\)/ : /translate\(-30px, -15px\) scale\(0.8\)/;
  await viewerDisplayWithin(expect(area.locator('div').first()).toHaveAttribute('style', transform, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'actual owning pan and scale');
  return {opacity: Number(opacityValue), zoom_percent: owning === 'A' ? 125 : 80, pan: delta};
}

async function viewerDisplayClose(page: Page, viewer: Locator, opener: Locator) {
  const deadline = performance.now() + 10_000;
  await viewerDisplayWithin(viewer.getByRole('button', {name: '저장 평가로 돌아가기', exact: true}).click(), deadline, 'ordinary viewer close before project handoff');
  await viewerDisplayWithin(expect(viewer).toHaveCount(0, {timeout: viewerDisplayRemaining(deadline)}), deadline, 'viewer closed');
  await viewerDisplayWithin(expect(opener).toBeFocused({timeout: viewerDisplayRemaining(deadline)}), deadline, 'owning opener focus returned');
}

test('source Electron saved overlay opacity zoom and mouse pan reset to the owning evidence through A B A project handoff', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend(), origin = `http://127.0.0.1:${backend.port}`, api = viewerDisplayApi(page, origin, true);
  const a = await viewerDisplayMake(workspace, api, 'A'), b = await viewerDisplayMake(workspace, api, 'B');
  expect(a.project.id).not.toBe(b.project.id); expect(a.originalSha).not.toBe(b.originalSha); expect(a.item.record.evaluation_id).not.toBe(b.item.record.evaluation_id);
  await page.reload(); await viewerDisplayHistory(page, b); await viewerDisplayProject(page, a, origin, evidence, workspace, 'setup-A');
  let details = await viewerDisplayHistory(page, a);
  const before = {A: viewerDisplayCustody(a), B: viewerDisplayCustody(b)};
  viewerDisplaySave(evidence, workspace, 'viewer-display-before', before);
  const writes: {method: string; path: string; body: unknown; main_frame: boolean}[] = [], transitions: unknown[] = [], views: any[] = [], cleanupFailures: {role: string; error: string}[] = [];
  const observe = (request: ViewerDisplayRequest) => {const address = new URL(request.url()); if (address.origin === origin && address.pathname.startsWith('/api/') && request.method() !== 'GET') writes.push({method: request.method(), path: address.pathname, body: request.postDataJSON(), main_frame: request.frame() === page.mainFrame()});};
  let failed = false, primary: unknown, after: any;
  const remember = (role: string, error: unknown) => {cleanupFailures.push({role, error: error instanceof Error ? error.message : String(error)}); if (!failed) {failed = true; primary = error;}};
  try {
    page.on('request', observe);
    const first = await viewerDisplayOpen(page, a, details, origin, evidence, workspace, 'owning-A-before');
    views.push({visit: 'A-before', rasters: first.rasters, proof: first.proof, controls: await viewerDisplayChange(page, first.viewer, 'A')});
    await evidence.screenshot(page, 'source-electron-A-opacity065-zoom125-pan40-25'); await viewerDisplayClose(page, first.viewer, first.opener);
    transitions.push(await viewerDisplayProject(page, b, origin, evidence, workspace, 'handoff-B')); details = await viewerDisplayHistory(page, b);
    const middle = await viewerDisplayOpen(page, b, details, origin, evidence, workspace, 'owning-B');
    expect(middle.rasters.original.rgba_sha256).not.toBe(first.rasters.original.rgba_sha256);
    expect(middle.rasters.truth).toEqual(first.rasters.truth); expect(middle.rasters.prediction).toEqual(first.rasters.prediction); expect(middle.rasters.error).toEqual(first.rasters.error);
    views.push({visit: 'B', rasters: middle.rasters, proof: middle.proof, fresh_controls: {opacity: 0.5, zoom_percent: 100, pan: [0, 0]}, controls: await viewerDisplayChange(page, middle.viewer, 'B')});
    await evidence.screenshot(page, 'source-electron-B-owning-opacity035-zoom80-pan-minus30-minus15'); await viewerDisplayClose(page, middle.viewer, middle.opener);
    transitions.push(await viewerDisplayProject(page, a, origin, evidence, workspace, 'return-A')); details = await viewerDisplayHistory(page, a);
    const returned = await viewerDisplayOpen(page, a, details, origin, evidence, workspace, 'returned-A'); expect(returned.rasters).toEqual(first.rasters);
    views.push({visit: 'A-return', rasters: returned.rasters, proof: returned.proof, fresh_controls: {opacity: 0.5, zoom_percent: 100, pan: [0, 0]}});
    await evidence.screenshot(page, 'source-electron-returned-A-opacity050-zoom100-pan0-0'); await viewerDisplayClose(page, returned.viewer, returned.opener);
    expect(writes).toEqual([{method: 'POST', path: '/api/project/open', body: {project_dir: b.project.project_dir}, main_frame: true}, {method: 'POST', path: '/api/project/open', body: {project_dir: a.project.project_dir}, main_frame: true}]);
    expect(await api('/api/project/current')).toEqual(a.project);
  } catch (error) {failed = true; primary = error;}
  finally {
    try {page.off('request', observe);} catch (error) {remember('remove-own-request-observer', error);}
    after = {};
    for (const scope of [a, b]) {
      try {after[scope.tag] = viewerDisplayCustody(scope); expect(after[scope.tag]).toEqual(before[scope.tag]);} catch (error) {remember('full-owning-custody-' + scope.tag, error);}
      const frozen = before[scope.tag];
      for (const root of Object.values(frozen.roots)) for (const member of Object.keys(root.snapshot.files)) {
        try {evidence.addFile(path.join(root.root, member));} catch (error) {remember('retain-owning-file-' + scope.tag + '-' + member, error);}
      }
      for (const file of Object.keys(frozen.files)) {try {evidence.addFile(file);} catch (error) {remember('retain-owning-top-file-' + scope.tag, error);}}
    }
    try {viewerDisplaySave(evidence, workspace, 'viewer-display-after-and-readback', {before, after, writes, transitions, views, cleanupFailures});} catch (error) {remember('durable-readback', error);}
    try {evidence.note('saved_viewer_display_project_handoff', {scenario_cells: VIEWER_DISPLAY_CELLS, before, after, writes, transitions, views, cleanup_failures: cleanupFailures,
      actual_A_B_A_project_navigation: transitions.length === 2, transient_display_reset: true, display_values_not_persisted: true,
      source_electron: true, controlled_saved_reports_not_model_inference: true, all_source_annotation_report_hashes_unchanged: !failed,
      installed_native_acceptance: false, frozen_native_acceptance: false, device_acceptance: false, human_labels_applied: false, model_quality_acceptance: false, parent_acceptance: false});} catch (error) {remember('harness-note', error);}
  }
  if (failed) throw primary;
});

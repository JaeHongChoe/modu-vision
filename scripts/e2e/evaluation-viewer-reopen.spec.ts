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

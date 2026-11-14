import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {APIRequestContext, Locator, Page} from '@playwright/test';
import {test, expect, type Evidence, type RendererServer, type Workspace} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import {openWorkspaceSettings} from './fixtures/workspaceSettings';

// New synthetic browser evidence only. Existing source-bound acceptance records
// and installed applications are not inputs to this fixture.
test.use({actionTimeout: 10_000});
const harness = require('./fixtures/harness.cjs');
const sha = (raw: Buffer | string) => crypto.createHash('sha256').update(raw).digest('hex');
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
// Compare an existing file's canonical identity, not a platform-specific alias spelling.
const fileIdentity = (value: unknown): string | null => {
  if (typeof value !== 'string' || !value) return null;
  try { return fs.realpathSync(value); } catch { return null; }
};
type Point = [number, number];
type Context = {page: Page; request: APIRequestContext; renderer: RendererServer; workspace: Workspace; evidence: Evidence};
const square: Point[] = [[40, 40], [180, 40], [180, 180], [40, 180]];

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  const visit = (folder: string) => {
    for (const name of fs.readdirSync(folder).sort()) {
      const file = path.join(folder, name), stat = fs.lstatSync(file);
      expect(stat.isSymbolicLink()).toBe(false);
      if (stat.isDirectory()) visit(file);
      else { expect(stat.isFile()).toBe(true); result[path.relative(root, file)] = sha(fs.readFileSync(file)); }
    }
  };
  visit(root); return result;
}

async function editor(c: Context, names = ['part']) {
  const {page, request, renderer, workspace: w, evidence: e} = c;
  const source = path.join(w.root, 'polygon-precision-source'); fs.mkdirSync(source);
  const images = names.map((name, index) => {
    const file = path.join(source, name + '.png');
    fs.writeFileSync(file, png(256, 3, (x, y) => [x, y, 70 + index * 70]), {flag: 'wx'});
    return {name, file, sha256: sha(fs.readFileSync(file))};
  });
  const api = async (route: string, body?: unknown, method?: string) => {
    const response = await request.fetch(renderer.origin + route, {
      method: method || (body === undefined ? 'GET' : 'POST'), timeout: 10_000,
      ...(body === undefined ? {} : {data: body}),
    });
    const bytes = await response.body();
    expect(response.status(), bytes.toString('utf8')).toBe(200);
    expect(bytes.length).toBeLessThanOrEqual(2 * 1024 * 1024);
    return JSON.parse(bytes.toString('utf8'));
  };
  const project = await api('/api/project/create', {name: 'Owned polygon precision ' + names.join('-'), task: 'segmentation'});
  // macOS /var is a symlink to /private/var; compare the actual owned directory identity.
  expect(fs.realpathSync(path.dirname(project.project_dir))).toBe(fs.realpathSync(w.projects));
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: true});
  const protectedBefore = {source: tree(source), models: tree(project.models_dir), reports: tree(project.reports_dir)};
  const pageErrors: string[] = [], consoleErrors: string[] = [], foreignRequests: string[] = [], forbiddenRequests: unknown[] = [];
  const writes: Array<{route: string; method: string; body: unknown}> = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  page.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  page.on('request', r => {
    const address = new URL(r.url());
    if (/^https?:$/.test(address.protocol) && address.origin !== renderer.origin) foreignRequests.push(r.url());
    if (address.origin !== renderer.origin || !address.pathname.startsWith('/api/') || ['GET', 'HEAD', 'OPTIONS'].includes(r.method())) return;
    const row = {route: address.pathname, method: r.method(), body: r.postDataJSON()}; writes.push(row);
    if (/^\/api\/(?:training|automated-training|inference|foundation|assistant|chat|model-execution)(?:\/|-)/.test(address.pathname)) forbiddenRequests.push(row);
  });
  await installDesktopHostShim(page, renderer.port); await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  const controls = page.getByRole('group', {name: '다각형 점 편집', exact: true});
  const saveButton = page.getByTestId('annotation-save-button');
  const query = (index = 0) => '/api/annotations/' + images[index].name + '?file_path=' + encodeURIComponent(images[index].file);
  const read = (index = 0) => api(query(index));
  const oneToOne = async () => {
    await page.getByTitle('실제 크기 (1:1)', {exact: true}).click();
    await expect(page.getByTestId('canvas-hud')).toContainText('100%');
  };
  const openLabeling = async () => {
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
    const focus = page.getByRole('button', {name: '집중 편집', exact: true});
    if (await focus.getAttribute('aria-pressed') !== 'true') await focus.click();
    await expect.poll(async () => Number((await page.getByTestId('canvas-hud').innerText()).match(/배율\s*([\d.]+)\s*%/)?.[1] || 0)).toBeGreaterThan(100);
    await oneToOne();
  };
  const point = async (x: number, y: number) => {
    const b = await page.locator('[data-canvas-container]').boundingBox(); expect(b).not.toBeNull();
    return {x: b!.x + (b!.width - 256) / 2 + x, y: b!.y + (b!.height - 256) / 2 + y};
  };
  const clickPoint = async (x: number, y: number, shift = false) => {
    // Controls/save status can resize the toolbar. Explicitly recenter the real
    // image rather than deriving coordinates from a stale bounding rectangle.
    await oneToOne(); const p = await point(x, y);
    if (shift) await page.keyboard.down('Shift');
    try { await page.mouse.click(p.x, p.y); }
    finally { if (shift) await page.keyboard.up('Shift'); }
  };
  const draw = async (points: Point[]) => {
    await page.getByRole('button', {name: /^Scratch(?: \d+)?$/}).click();
    await page.getByTitle('다각형 폴리곤 (Polygon - 4)', {exact: true}).click(); await oneToOne();
    for (const [x, y] of points) { const p = await point(x, y); await page.mouse.click(p.x, p.y); }
    await page.keyboard.press('Enter'); await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1);
  };
  const select = async () => {
    await page.getByTitle('선택 및 이동 (Select / Move - 1)', {exact: true}).click();
    await page.getByTitle('라벨 삭제', {exact: true}).locator('..').locator('..').click();
    await expect(controls).toBeVisible(); await expect(page.getByTestId('polygon-point-controls')).toBeVisible();
    await oneToOne();
  };
  const save = async (expected: Point[], index = 0) => {
    const before = await read(index);
    const response = page.waitForResponse(r => new URL(r.url()).origin === renderer.origin
      && new URL(r.url()).pathname === '/api/annotations/save' && r.request().method() === 'POST'
      && fileIdentity(r.request().postDataJSON()?.image_path) === fs.realpathSync(images[index].file), {timeout: 10_000});
    await expect(saveButton).toBeEnabled(); await saveButton.click();
    const reply = await response; expect(reply.status(), await reply.text()).toBe(200); expect(await reply.finished()).toBeNull();
    const posted = reply.request().postDataJSON(), value = await reply.json();
    if (posted.lease_token) e.redact(posted.lease_token);
    expect(posted.expected_revision).toBe(before.metadata.revision);
    expect(fileIdentity(posted.image_path)).toBe(fs.realpathSync(images[index].file)); expect(value.status).toBe('saved'); expect(value.mask_generated).toBe(true);
    await expect(saveButton).toContainText('저장됨');
    const stored = await read(index); geometry(stored, expected);
    expect(stored.metadata.revision).toBe(value.metadata.revision);
    expect(stored.metadata.revision).toBeGreaterThan(before.metadata.revision);
    expect(stored.annotations[0].id).toBe(posted.annotations[0].id);
    return {stored, posted, response: value};
  };
  const switchImage = async (direction: 'Next' | 'Previous', index: number) => {
    const loaded = page.waitForResponse(r => new URL(r.url()).origin === renderer.origin
      && new URL(r.url()).pathname === '/api/annotations/' + images[index].name
      && fileIdentity(new URL(r.url()).searchParams.get('file_path')) === fs.realpathSync(images[index].file)
      && r.request().method() === 'GET', {timeout: 10_000});
    await page.getByRole('button', {name: direction + ' image or page', exact: true}).click();
    const response = await loaded; expect(response.status()).toBe(200); expect(await response.finished()).toBeNull();
    expect(fileIdentity((await response.json()).metadata.file_path)).toBe(fs.realpathSync(images[index].file));
    await expect(page.getByText('기존 라벨을 불러오는 중입니다.', {exact: true})).toHaveCount(0);
    await expect(page.getByText(/기존 라벨 조회 실패:/)).toHaveCount(0);
  };
  const mask = async (stored: any, inside: Point = [100, 100]) => {
    expect(typeof stored.mask_file).toBe('string');
    expect(path.relative(project.annotations_dir, stored.mask_file)).not.toMatch(/^\.\./);
    const raw = fs.readFileSync(stored.mask_file); e.addFile(stored.mask_file);
    const pixels = await page.evaluate(async ({url, inside}) => {
      const image = new Image();
      await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(Error('Owned polygon mask decode failed')); image.src = url; });
      const canvas = document.createElement('canvas'); canvas.width = image.width; canvas.height = image.height;
      const ctx = canvas.getContext('2d')!; ctx.drawImage(image, 0, 0);
      return {width: image.width, height: image.height, values: [inside, [10, 10], [220, 220]].map(([x, y]) => ctx.getImageData(x, y, 1, 1).data[0])};
    }, {url: 'data:image/png;base64,' + raw.toString('base64'), inside});
    expect(pixels).toEqual({width: 256, height: 256, values: [2, 0, 0]});
    return {sha256: sha(raw), pixels};
  };
  const finish = (proof: unknown) => {
    expect({source: tree(source), models: tree(project.models_dir), reports: tree(project.reports_dir)}).toEqual(protectedBefore);
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(foreignRequests).toEqual([]); expect(forbiddenRequests).toEqual([]);
    const sourceFiles = ['src/renderer/components/labeling/LabelingCanvas.tsx',
      'src/renderer/components/labeling/LabelingStudio.tsx', 'src/renderer/components/labeling/PolygonVertexControls.tsx',
      'src/renderer/components/labeling/MaskLayerControls.tsx', 'src/renderer/components/labeling/polygonEditing.ts',
      'src/renderer/stores/useAnnotationStore.ts',
      'scripts/e2e/labeling-polygon-precision.spec.ts'];
    e.note('polygon_precision', {source: harness.sourceIdentity(),
      source_files_sha256: Object.fromEntries(sourceFiles.map(file => [file, sha(fs.readFileSync(path.join(harness.REPO_ROOT, file)))])),
      project_id: project.id, images,
      writes, page_errors: pageErrors, console_errors: consoleErrors, foreign_requests: foreignRequests,
      forbidden_execution_requests: forbiddenRequests, proof, actual_browser: true, installed_native: false,
      synthetic_fixture: true, model_training_or_inference: false, provider_calls: false,
      human_annotation_quality_acceptance: false, historical_acceptance_requalified: false});
  };
  await openLabeling();
  return {api, project, images, source, controls, saveButton, read, draw, select, save, switchImage, mask, finish, clickPoint, oneToOne, openLabeling, writes};
}

function geometry(snapshot: any, expected: Point[]) {
  expect(snapshot.annotations).toHaveLength(1); const annotation = snapshot.annotations[0];
  expect(annotation.type).toBe('polygon'); expect(annotation.label).toBe('Scratch'); expect(annotation.category_id).toBe(2);
  expect(annotation.polygon).toEqual(expected); expect(annotation.points).toEqual(expected);
}

function unchangedShapeMetadata(actual: any, original: any) {
  const metadata = (a: any) => Object.fromEntries(Object.entries(a).filter(([key]) => !['bbox', 'polygon', 'points'].includes(key)));
  expect(metadata(actual)).toEqual(metadata(original));
}

async function keyboardActivate(page: Page, target: Locator) {
  await expect(target).toBeEnabled();
  // Real Tab traversal and Enter: no synthetic click, DOM focus, or store writes.
  for (let index = 0; index < 80; index++) {
    if (await target.evaluate(element => element === document.activeElement)) { await page.keyboard.press('Enter'); return; }
    await page.keyboard.press('Tab');
  }
  throw Error('The polygon control was not reachable through keyboard Tab traversal');
}

test('polygon edge projection and selected-point keys retain exact geometry through undo redo save and reopen', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(180_000); const x = await editor({page, request, renderer, workspace, evidence});
  await x.draw(square); const drawn = await x.save(square);
  // Real owned API seed gives a previously saved polygon non-empty metadata.
  // This is synthetic provenance data; no selector/model execution is implied.
  const seededRequest = clone(drawn.posted); seededRequest.expected_revision = drawn.stored.metadata.revision;
  Object.assign(seededRequest.annotations[0], {text: '합성 점 편집 보존 확인', direction_deg: 315,
    origin: {kind: 'auto_selector', backend: 'synthetic_fixture', seed_point: [100, 100], tolerance: 20,
      fallback: false, created_at: '2026-11-14T00:00:00Z'}});
  await x.api('/api/annotations/save', seededRequest); const seeded = await x.read(); geometry(seeded, square);
  await page.reload(); await x.openLabeling(); await x.select();
  await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  // Off-edge CSS input must project onto y=40 rather than inserting y=43.
  await x.clickPoint(108, 43, true);
  await page.keyboard.press('ArrowDown'); await page.keyboard.press('Shift+ArrowRight');
  const moved: Point[] = [[40, 40], [118, 41], [180, 40], [180, 180], [40, 180]];
  const inserted = await x.save(moved); unchangedShapeMetadata(inserted.stored.annotations[0], seeded.annotations[0]);
  expect(inserted.stored.annotations[0].bbox).toEqual([40, 40, 180, 180]);
  await x.clickPoint(118, 41); await page.keyboard.press('Delete'); const deleted = await x.save(square);
  unchangedShapeMetadata(deleted.stored.annotations[0], seeded.annotations[0]);
  await page.getByTitle('실행 취소 (Ctrl+Z)', {exact: true}).click();
  await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  const undone = await x.save(moved); unchangedShapeMetadata(undone.stored.annotations[0], seeded.annotations[0]);
  await page.getByTitle('다시 실행 (Ctrl+Y)', {exact: true}).click();
  await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  const redone = await x.save(square); unchangedShapeMetadata(redone.stored.annotations[0], seeded.annotations[0]);
  const savedMask = await x.mask(redone.stored);
  await page.reload(); await x.openLabeling(); await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1);
  const reopened = await x.read(); expect(reopened).toEqual(redone.stored); expect(await x.mask(reopened)).toEqual(savedMask);
  await x.select(); await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  await evidence.screenshot(page, 'polygon-pointer-keys-exact-saved-and-reopened');
  x.finish({drawn: drawn.stored, seeded, inserted: inserted.stored, deleted: deleted.stored,
    undone: undone.stored, redone: redone.stored, reopened, saved_mask: savedMask,
    synthetic_metadata_preserved: true, projected_pointer: [108, 40], selected_point_after_nudges: [118, 41]});
});

test('keyboard polygon controls wrap insert and respect the three-point minimum without deleting a focused shape', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(180_000); const x = await editor({page, request, renderer, workspace, evidence});
  await x.draw(square); const initial = await x.save(square); await x.select();
  const control = (name: string) => x.controls.getByRole('button', {name, exact: true});
  await expect(control('점 삭제')).toBeDisabled(); await expect(control('변에 점 추가')).toBeDisabled();
  // Next from whole-shape selection chooses the first point; previous/next wrap.
  await keyboardActivate(page, control('다음 점')); await keyboardActivate(page, control('이전 점'));
  await keyboardActivate(page, control('다음 점')); await keyboardActivate(page, control('변에 점 추가'));
  const midpoint: Point[] = [[40, 40], [110, 40], [180, 40], [180, 180], [40, 180]];
  const inserted = await x.save(midpoint); unchangedShapeMetadata(inserted.stored.annotations[0], initial.stored.annotations[0]);
  await keyboardActivate(page, control('점 삭제')); await keyboardActivate(page, control('도형 선택'));
  await keyboardActivate(page, control('다음 점')); await keyboardActivate(page, control('점 삭제'));
  const triangle: Point[] = [[180, 40], [180, 180], [40, 180]];
  await expect(control('점 삭제')).toBeDisabled(); const minimum = await x.save(triangle);
  unchangedShapeMetadata(minimum.stored.annotations[0], initial.stored.annotations[0]);
  await keyboardActivate(page, control('도형 선택'));
  const mutations = x.writes.length;
  await page.keyboard.press('Delete'); await page.keyboard.press('Backspace');
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1); await expect(x.saveButton).toContainText('저장됨');
  await expect(control('점 삭제')).toBeDisabled(); expect(x.writes).toHaveLength(mutations);
  expect(await x.read()).toEqual(minimum.stored); const savedMask = await x.mask(minimum.stored, [150, 150]);
  await page.setViewportSize({width: 760, height: 900}); await expect(x.controls).toBeVisible();
  const bounds = await x.controls.boundingBox(); expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0); expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(760);
  const themes: string[] = [];
  for (const desired of ['dark', 'light']) {
    if (await page.locator('html').getAttribute('data-theme') !== desired) {
      await openWorkspaceSettings(page);
      await page.getByRole('button', {name: desired === 'light' ? '밝은 화면' : '어두운 화면', exact: true}).click();
    }
    await expect(page.locator('html')).toHaveAttribute('data-theme', desired); themes.push(desired);
    await expect(x.controls).toBeVisible(); await expect(control('점 삭제')).toBeDisabled();
    await evidence.screenshot(page, 'polygon-keyboard-minimum-narrow-' + desired);
    for (const name of ['이전 점', '다음 점']) {
      // Visibility and an in-viewport panel rectangle do not establish that a
      // neighboring floating canvas toolbar leaves an actual button exposed.
      const exposed = await control(name).evaluate(button => {
        const r = button.getBoundingClientRect();
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return !!hit && button.contains(hit);
      });
      expect(exposed, desired + ' narrow ' + name + ' must not be covered by another canvas toolbar').toBe(true);
    }
  }
  expect(await x.read()).toEqual(minimum.stored);
  x.finish({initial: initial.stored, inserted: inserted.stored, minimum: minimum.stored, saved_mask: savedMask,
    real_tab_enter_controls: true, focused_delete_and_backspace_no_mutation: true, viewport: {width: 760, height: 900}, themes});
});

test('changing images clears a selected polygon point and leaves the other saved image untouched', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(180_000); const x = await editor({page, request, renderer, workspace, evidence}, ['part-a', 'part-b']);
  await x.draw(square); const first = await x.save(square, 0);
  await x.switchImage('Next', 1);
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(0);
  const triangle: Point[] = [[60, 50], [190, 50], [120, 190]];
  await x.draw(triangle); const second = await x.save(triangle, 1);
  await x.switchImage('Previous', 0);
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1); await x.select(); await x.clickPoint(40, 40);
  await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeEnabled();
  await x.switchImage('Next', 1);
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1); await expect(x.controls).toHaveCount(0);
  // The image transition supplies no shape selection. Delivered editing keys
  // must not reuse the previous image's point or select/delete this shape.
  await x.oneToOne(); const blank = await page.locator('[data-canvas-container]').boundingBox(); expect(blank).not.toBeNull();
  await page.mouse.click(blank!.x + 8, blank!.y + 8);
  const mutationCount = x.writes.length;
  await page.keyboard.press('ArrowRight'); await page.keyboard.press('Delete'); await page.keyboard.press('Backspace');
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1); await expect(x.saveButton).toContainText('저장됨');
  await x.select(); await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  expect(await x.read(1)).toEqual(second.stored); expect(await x.read(0)).toEqual(first.stored); expect(x.writes).toHaveLength(mutationCount);
  await x.switchImage('Previous', 0);
  await expect(page.getByTitle('라벨 삭제', {exact: true})).toHaveCount(1); await x.select();
  await expect(x.controls.getByRole('button', {name: '점 삭제', exact: true})).toBeDisabled();
  expect(await x.read(0)).toEqual(first.stored); expect(await x.read(1)).toEqual(second.stored);
  await evidence.screenshot(page, 'polygon-image-A-B-A-clears-selected-point');
  x.finish({first: first.stored, second: second.stored, image_handoff: 'A-B-A', delivered_empty_selection_keys_no_mutation: true});
});

test('first selection vertex drag retains the canvas viewport and moves only that point by the real pointer delta', async ({page, request, renderer, workspace, evidence}) => {
  test.setTimeout(180_000); const x = await editor({page, request, renderer, workspace, evidence});
  await x.draw(square); const initial = await x.save(square);
  await page.reload(); await x.openLabeling();
  // Do not select the annotation through its list: this gesture must perform
  // the first shape selection as well as the point drag in the real component.
  await page.getByTitle('선택 및 이동 (Select / Move - 1)', {exact: true}).click();
  await x.oneToOne(); await expect(page.getByTestId('canvas-hud')).toContainText('100%');
  await expect(x.controls).toHaveCount(0);
  const viewport = page.locator('[data-canvas-container]');
  const before = await viewport.boundingBox(); expect(before).not.toBeNull();
  const start = {x: before!.x + (before!.width - 256) / 2 + 40,
    y: before!.y + (before!.height - 256) / 2 + 40};
  const frames = () => page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await page.mouse.move(start.x, start.y); await page.mouse.down(); await frames();
  const afterDown = await viewport.boundingBox(); expect(afterDown).toEqual(before);
  // Point controls are deferred until this original first-selection gesture
  // finishes, so they cannot alter the viewport halfway through the drag.
  await expect(x.controls).toHaveCount(0);
  await page.mouse.move(start.x + 10, start.y, {steps: 5}); await frames();
  // The existing local-draft recovery strip may reflow this viewport during
  // the drag. Preserve that evidence; pointer geometry must still use the
  // original gesture's coordinates and scale rather than the reflowed rect.
  const afterMove = await viewport.boundingBox(); expect(afterMove).not.toBeNull();
  const draftRecoveryDuringMove = await page.getByRole('region', {name: '라벨 연결 복구', exact: true}).evaluateAll(regions =>
    regions.map(region => ({text: region.textContent, rect: region.getBoundingClientRect().toJSON()})));
  await expect(x.controls).toHaveCount(0);
  await page.mouse.up(); await expect(x.controls).toBeVisible(); await frames();
  // Finishing the gesture may reveal a control row and resize the viewport;
  // no subsequent pointer coordinate is interpreted under the old rectangle.
  const afterUp = await viewport.boundingBox(); expect(afterUp).not.toBeNull();
  const expected: Point[] = [[50, 40], [180, 40], [180, 180], [40, 180]];
  const moved = await x.save(expected); unchangedShapeMetadata(moved.stored.annotations[0], initial.stored.annotations[0]);
  expect(moved.stored.annotations[0].bbox).toEqual([40, 40, 180, 180]);
  expect(moved.stored.annotations[0].polygon.slice(1)).toEqual(initial.stored.annotations[0].polygon.slice(1));
  const savedMask = await x.mask(moved.stored);
  await page.reload(); await x.openLabeling();
  const reopened = await x.read(); expect(reopened).toEqual(moved.stored);
  await evidence.screenshot(page, 'polygon-first-selection-drag-exact-delta-saved-and-reopened');
  x.finish({initial: initial.stored, moved: moved.stored, reopened, saved_mask: savedMask,
    pointer_start_css: start, pointer_end_css: {x: start.x + 10, y: start.y}, image_scale_percent: 100,
    canvas_rects: {before, after_down: afterDown, after_move: afterMove, after_up: afterUp},
    draft_recovery_during_move: draftRecoveryDuringMove,
    first_selection_without_list: true, controls_deferred_until_pointer_up: true,
    browser_counterexample_red_observed: false});
});

import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Locator, Page, Request} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi, handoffHash, handoffLateRead, handoffNamespace, handoffProject, handoffSave, handoffWithin, type HandoffApi} from './fixtures/remaining-project-handoff';

const harness = require('./fixtures/harness.cjs');
test.use({actionTimeout: 10_000});
const CELLS = ['U029.native-saved-overlay-close-return-focus.reopen', 'U029.native-saved-overlay-close-return-focus.handoff', 'F051.native-saved-overlay-class-selector.handoff'];
type ClassName = 'all' | 'Scratch' | 'Crack';
type Scope = {tag: 'A' | 'B'; root: string; source: string; original: string; originalSha: string; project: any; fixture: any; item: any; blue: number};
const fileSha = (file: string) => handoffHash(fs.readFileSync(file));
const remaining = (deadline: number) => Math.max(1, Math.floor(deadline - performance.now()));

async function makeScope(workspace: Workspace, api: HandoffApi, tag: 'A' | 'B'): Promise<Scope> {
  const root = path.join(workspace.root, 'saved-viewer-' + tag);
  fs.mkdirSync(root);
  const source = path.join(root, 'source'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'), blue = tag === 'A' ? 60 : 160;
  // The original saved-overlay source generator, with a distinct owning blue channel.
  execFileSync(harness.resolvePython(), ['-c', "import sys,numpy as np;from PIL import Image;y,x=np.indices((96,160));Image.fromarray(np.dstack([x,y,np.full_like(x,int(sys.argv[2]))]).astype(np.uint8)).save(sys.argv[1])", original, String(blue)], {timeout: 30_000});
  const created = await api('/api/project/create', {name: 'Saved viewer handoff ' + tag, task: 'segmentation', project_dir: path.join(root, 'project')});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation'});
  await api('/api/project/labelsets', {name: 'Saved viewer second labelset ' + tag});
  const sets = await api('/api/project/labelsets');
  const secondSet = sets.labelsets.find((entry: any) => entry.id !== 'default').id;
  // Each producer receives its own isolated root; its original mkdir contract is retained.
  const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/pixel_evaluation_reports.py'), root, created.project_dir, source, secondSet, 'bound'], {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const item = fixture.items.find((entry: any) => entry.labelset_id === 'default' && entry.variant === 'valid');
  expect(item).toBeTruthy(); expect(item.record.binding.source_dataset_path).toBe(source);
  expect(item.record.result.fixture_kind).toBe('controlled_display_only_no_model_inference');
  const sample = item.record.result.test_predictions[0];
  expect(sample.file_path).toBe(original); expect(sample.image_sha256).toBe(fileSha(original));
  expect(sample.pixel_evidence.mapping).toEqual({kind: 'full_image_resize', source_size: [160, 96]});
  expect(sample.pixel_evidence.per_class.Scratch.class_id).toBe(7); expect(sample.pixel_evidence.per_class.Crack.class_id).toBe(23);
  await api('/api/team-data'); await api('/api/team-data/readiness');
  await api('/api/annotations/part?file_path=' + encodeURIComponent(original));
  const project = await api('/api/project/current');
  expect(project.id).toBe(created.id); expect(project.source_dataset_dir).toBe(source);
  return {tag, root, source, original, originalSha: fileSha(original), project, fixture, item, blue};
}

function custody(scope: Scope) {
  const roots = {source: scope.source, annotations: scope.project.annotations_dir, reports: scope.project.reports_dir,
    models: scope.project.models_dir, dataset: scope.project.dataset_dir, labelsets: path.join(scope.project.project_dir, 'labelsets'),
    controlled_inputs: path.join(scope.root, 'controlled-evaluation-inputs')};
  const files = [path.join(scope.project.project_dir, 'project.json'), path.join(scope.project.project_dir, 'labelsets.json')];
  return {project_id: scope.project.id, evaluation_id: scope.item.record.evaluation_id,
    roots: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, {root, snapshot: handoffNamespace(root)}])),
    files: Object.fromEntries(files.map(file => [file, {size: fs.statSync(file).size, sha256: fileSha(file)}]))};
}

async function openHistory(page: Page, scope: Scope, deadline = performance.now() + 10_000) {
  await handoffWithin(expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(scope.project.name, {timeout: remaining(deadline)}), deadline, 'owning project header');
  await handoffWithin(page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(3).click(), deadline, 'ordinary evaluation stage');
  const summary = page.locator('summary').filter({hasText: '평가 이력 · 제품/Lot별 오류'});
  if (await handoffWithin(summary.locator('..').getAttribute('open'), deadline, 'history disclosure state') === null) await handoffWithin(summary.click(), deadline, 'history disclosure');
  const history = summary.locator('..');
  await handoffWithin(history.getByLabel('평가 이력 모델 종류', {exact: true}).selectOption('segmentation'), deadline, 'owning saved model kind');
  await handoffWithin(history.getByLabel('평가 라벨 세트', {exact: true}).selectOption('default'), deadline, 'owning default labelset');
  const selector = history.getByLabel(/^모델별 저장 평가/);
  await handoffWithin(expect.poll(() => selector.locator('option').evaluateAll(nodes => nodes.map(node => (node as HTMLOptionElement).value).filter(Boolean).sort()), {timeout: remaining(deadline)}).toEqual([scope.item.record.evaluation_id]), deadline, 'exact owning report catalog');
  await handoffWithin(selector.selectOption(scope.item.record.evaluation_id), deadline, 'saved owning report select');
  await handoffWithin(expect(selector).toHaveValue(scope.item.record.evaluation_id, {timeout: remaining(deadline)}), deadline, 'saved owning report identity');
  const details = history.locator('details').filter({has: page.locator('summary').filter({hasText: '객체·픽셀·문자 오류와 분포 분석'})}).first();
  if (await handoffWithin(details.getAttribute('open'), deadline, 'evidence disclosure state') === null) await handoffWithin(details.locator('summary').first().click(), deadline, 'evidence disclosure');
  await handoffWithin(expect(details.getByLabel('평가 증거 클래스', {exact: true})).toHaveValue('all', {timeout: remaining(deadline)}), deadline, 'transient class resets in new owning scope');
  await handoffWithin(expect(details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true})).toBeEnabled({timeout: remaining(deadline)}), deadline, 'owning pixel evidence ready');
  return details;
}

async function raster(image: Locator, deadline: number) {
  const actual = await handoffWithin(image.evaluate(async node => {
    const img = node as HTMLImageElement; await img.decode();
    const canvas = document.createElement('canvas'); canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d')!; ctx.drawImage(img, 0, 0);
    return {size: [canvas.width, canvas.height], pixels: Array.from(ctx.getImageData(0, 0, canvas.width, canvas.height).data)};
  }), deadline, 'read actual decoded raster');
  expect(actual.size).toEqual([160, 96]);
  return actual;
}

function expectedTruth(className: ClassName) {
  const expected: number[] = [], wanted = className === 'all' ? null : className === 'Scratch' ? 7 : 23;
  for (let y = 0; y < 96; y++) for (let x = 0; x < 160; x++) {
    const mx = Math.floor(x * 64 / 160), my = Math.floor(y * 64 / 96);
    const value = mx >= 8 && mx < 24 && my >= 8 && my < 24 ? 7 : mx >= 32 && mx < 48 && my >= 16 && my < 40 ? 23 : 0;
    expected.push(...((wanted === null ? value > 0 : value === wanted) ? [74, 222, 128, 255] : [0, 0, 0, 0]));
  }
  return expected;
}

async function openViewer(page: Page, scope: Scope, details: Locator, className: ClassName, evidence: Evidence, workspace: Workspace, label: string) {
  const deadline = performance.now() + 10_000;
  await handoffWithin(details.getByLabel('평가 증거 클래스', {exact: true}).selectOption(className), deadline, 'actual owning class selection');
  const opener = details.getByRole('button', {name: '원본 위에 평가 마스크 보기', exact: true});
  await handoffWithin(expect(opener).toBeEnabled({timeout: remaining(deadline)}), deadline, 'class raster ready');
  const wire = page.waitForResponse(response => {const request = response.request(), url = new URL(response.url());
    return request.method() === 'GET' && request.frame() === page.mainFrame() && url.origin === new URL(page.url()).origin && url.pathname === '/api/evaluation/history/' + scope.item.record.evaluation_id + '/evidence-image'
      && url.searchParams.get('source_dataset_path') === scope.source && url.searchParams.get('image_path') === scope.original && url.searchParams.get('task') === 'segmentation';}, {timeout: remaining(deadline)});
  await handoffWithin(opener.click(), deadline, 'open actual read-only viewer');
  const response = await handoffWithin(wire, deadline, 'actual owning source preview');
  expect(response.status()).toBe(200); const bytes = await handoffWithin(response.body(), deadline, 'complete owning source preview body');
  expect(bytes.length).toBeLessThanOrEqual(1024 * 1024); expect(await handoffWithin(response.finished(), deadline, 'source preview closed')).toBeNull();
  const body = JSON.parse(bytes.toString('utf8'));
  expect(body).toMatchObject({evaluation_id: scope.item.record.evaluation_id, image_path: scope.original, image_sha256: scope.originalSha, original_size: [160, 96]});
  const rawFile = path.join(workspace.logs, label + '-actual-preview.json'); fs.writeFileSync(rawFile, bytes, {flag: 'wx'}); evidence.addFile(rawFile);
  const viewer = page.getByRole('dialog', {name: '이미지 판정 근거 보기', exact: true});
  await handoffWithin(expect(viewer).toBeVisible({timeout: remaining(deadline)}), deadline, 'owning dialog visible');
  await handoffWithin(expect(viewer).toContainText(scope.item.record.evaluation_id, {timeout: remaining(deadline)}), deadline, 'owning saved evaluation identity');
  await handoffWithin(expect(viewer).toContainText(scope.originalSha, {timeout: remaining(deadline)}), deadline, 'owning original byte hash');
  await handoffWithin(expect(viewer.getByLabel('근거 이미지 종류', {exact: true})).toHaveValue('original', {timeout: remaining(deadline)}), deadline, 'owning original layer');
  const original = await raster(viewer.getByRole('img', {name: '평가 입력 원본', exact: true}), deadline), originalExpected: number[] = [];
  for (let y = 0; y < 96; y++) for (let x = 0; x < 160; x++) originalExpected.push(x, y, scope.blue, 255);
  expect(original.pixels).toEqual(originalExpected);
  await handoffWithin(viewer.getByLabel('근거 겹침 이미지', {exact: true}).selectOption('truth'), deadline, 'owning saved truth overlay');
  const truth = await raster(viewer.locator('img').nth(1), deadline); expect(truth.pixels).toEqual(expectedTruth(className));
  await handoffWithin(expect(viewer.getByLabel('근거 겹침 투명도', {exact: true})).toHaveValue('0.5', {timeout: remaining(deadline)}), deadline, 'fresh viewer opacity');
  await handoffWithin(expect(viewer.getByRole('status')).toHaveText('100%', {timeout: remaining(deadline)}), deadline, 'fresh viewer zoom');
  const proof = {project_id: scope.project.id, evaluation_id: scope.item.record.evaluation_id, original_sha256: scope.originalSha,
    className, source_size: original.size, original_rgba_sha256: handoffHash(Buffer.from(original.pixels)), truth_rgba_sha256: handoffHash(Buffer.from(truth.pixels)),
    request: {method: 'GET', actual_main_frame: true, origin: new URL(response.url()).origin, pathname: new URL(response.url()).pathname, source: scope.source, image_path: scope.original, status: response.status(), response_sha256: handoffHash(bytes), response_bytes: bytes.length, finished: true}};
  await evidence.screenshot(page, label);
  return {viewer, opener, proof};
}

async function closeOwnViewer(page: Page, viewer: Locator, opener: Locator, via: 'Escape' | 'button') {
  const deadline = performance.now() + 10_000;
  if (via === 'Escape') await handoffWithin(page.keyboard.press('Escape'), deadline, 'ordinary Escape return');
  else await handoffWithin(viewer.getByRole('button', {name: '저장 평가로 돌아가기', exact: true}).click(), deadline, 'ordinary return button');
  await handoffWithin(expect(viewer).toHaveCount(0, {timeout: remaining(deadline)}), deadline, 'dialog closed');
  await handoffWithin(expect(opener).toBeFocused({timeout: remaining(deadline)}), deadline, 'actual connected owning opener focus');
  expect(await opener.evaluate(node => node.isConnected)).toBe(true);
}

test('saved evaluation viewer closes and reacquires owning focus and class through A B A project handoff', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api = handoffApi(page, renderer.origin, false);
  const a = await makeScope(workspace, api, 'A'), b = await makeScope(workspace, api, 'B');
  expect(a.project.id).not.toBe(b.project.id); expect(a.originalSha).not.toBe(b.originalSha);
  expect(a.item.record.evaluation_id).not.toBe(b.item.record.evaluation_id);
  await page.goto(renderer.url); await openHistory(page, b); const bAnnotation = await api('/api/annotations/part?file_path=' + encodeURIComponent(b.original)), bTeam = await api('/api/team-data');
  await handoffProject(page, a, renderer.origin); let aDetails = await openHistory(page, a);
  const aAnnotation = await api('/api/annotations/part?file_path=' + encodeURIComponent(a.original)), aTeam = await api('/api/team-data');
  const before = {A: custody(a), B: custody(b)};
  handoffSave(evidence, workspace, 'viewer-handoff-before', {before, aAnnotation, bAnnotation, aTeam, bTeam});
  const writes: {method: string; path: string; body: unknown; main_frame: boolean}[] = [];
  const observe = (request: Request) => {const url = new URL(request.url()); if (url.origin !== renderer.origin || request.method() === 'GET') return;
    let body: unknown; try {body = request.postDataJSON();} catch {body = {invalid_json: true};}
    writes.push({method: request.method(), path: url.pathname, body, main_frame: request.frame() === page.mainFrame()});};
  page.on('request', observe);
  let primary: unknown, failed = false, late: Awaited<ReturnType<typeof handoffLateRead>> | undefined;
  const cleanupFailures: string[] = []; let observerRemoved = false;
  try {
    const first = await openViewer(page, a, aDetails, 'Scratch', evidence, workspace, 'owning-A-before-close');
    await closeOwnViewer(page, first.viewer, first.opener, 'Escape');
    const reopened = await openViewer(page, a, aDetails, 'Scratch', evidence, workspace, 'owning-A-reopened');
    expect(reopened.proof.truth_rgba_sha256).toBe(first.proof.truth_rgba_sha256);
    expect(reopened.proof.original_rgba_sha256).toBe(first.proof.original_rgba_sha256);
    await closeOwnViewer(page, reopened.viewer, reopened.opener, 'button');
    const oldOpener = await reopened.opener.elementHandle(); expect(oldOpener).not.toBeNull();
    late = await handoffLateRead(page, renderer.origin, false, url => url.pathname === '/api/evaluation/history/' + a.item.record.evaluation_id + '/evidence-image' && url.searchParams.get('source_dataset_path') === a.source && url.searchParams.get('image_path') === a.original);
    const handoffDeadline = performance.now() + 10_000;
    await handoffWithin(reopened.opener.click(), handoffDeadline, 'actual pending owning A preview');
    const captured = await handoffWithin(late.ready(), handoffDeadline, 'captured actual old A preview');
    expect(captured.body).toMatchObject({evaluation_id: a.item.record.evaluation_id, image_path: a.original, image_sha256: a.originalSha});
    const oldRaw = path.join(workspace.logs, 'old-A-held-preview.json'); fs.writeFileSync(oldRaw, captured.bytes, {flag: 'wx'}); evidence.addFile(oldRaw);
    const toB = await handoffWithin(handoffProject(page, b, renderer.origin), handoffDeadline, 'real A to B project transition');
    const bDetails = await handoffWithin(openHistory(page, b, handoffDeadline), handoffDeadline, 'new B saved report ready');
    const disposition = await handoffWithin(late.finish(), handoffDeadline, 'old A actual request disposition');
    expect(await oldOpener!.evaluate(node => node.isConnected)).toBe(false);
    await expect(page.getByRole('dialog', {name: '이미지 판정 근거 보기', exact: true})).toHaveCount(0);
    const bView = await openViewer(page, b, bDetails, 'Crack', evidence, workspace, 'owning-B-class-Crack');
    expect(bView.proof.truth_rgba_sha256).not.toBe(first.proof.truth_rgba_sha256);
    await expect(bView.viewer).not.toContainText(a.item.record.evaluation_id); await expect(bView.viewer).not.toContainText(a.originalSha);
    await closeOwnViewer(page, bView.viewer, bView.opener, 'Escape');
    expect(await api('/api/annotations/part?file_path=' + encodeURIComponent(b.original))).toEqual(bAnnotation); expect(await api('/api/team-data')).toEqual(bTeam);
    const toA = await handoffProject(page, a, renderer.origin); aDetails = await openHistory(page, a);
    const returned = await openViewer(page, a, aDetails, 'Scratch', evidence, workspace, 'returned-A-owning-class-and-source');
    expect(returned.proof.truth_rgba_sha256).toBe(first.proof.truth_rgba_sha256); expect(returned.proof.original_rgba_sha256).toBe(first.proof.original_rgba_sha256);
    await expect(returned.viewer).not.toContainText(b.item.record.evaluation_id); await expect(returned.viewer).not.toContainText(b.originalSha);
    await closeOwnViewer(page, returned.viewer, returned.opener, 'button');
    expect(await api('/api/annotations/part?file_path=' + encodeURIComponent(a.original))).toEqual(aAnnotation); expect(await api('/api/team-data')).toEqual(aTeam);
    const after = {A: custody(a), B: custody(b)}; expect(after).toEqual(before);
    expect(writes).toEqual([{method: 'POST', path: '/api/project/open', body: {project_dir: b.project.project_dir}, main_frame: true}, {method: 'POST', path: '/api/project/open', body: {project_dir: a.project.project_dir}, main_frame: true}]);
    for (const scope of [a, b]) {evidence.addFile(scope.original); for (const item of scope.fixture.items) evidence.addFile(item.report_path); for (const input of scope.fixture.inputs) evidence.addFile(input.path);}
    handoffSave(evidence, workspace, 'saved-viewer-handoff-custody', {before, after, first: first.proof, reopened: reopened.proof, B: bView.proof, returned: returned.proof, toB, toA, writes, old_A: {sha256: captured.sha256, bytes: captured.bytes.length, disposition}});
    evidence.note('saved_viewer_owning_handoff', {scenario_cells: CELLS, browser_only: true, owning_saved_report_and_original_hash: true,
      close_paths: ['Escape', 'button'], actual_owning_focus_return: true, old_opener_disconnected_on_project_handoff: true,
      owning_class_transition: ['A:Scratch', 'B:all->Crack', 'A:all->Scratch'], class_is_transient_reset_not_persisted: true,
      complete_declared_trees_unchanged: true, saved_report_fixture_is_synthetic: true, controlled_saved_masks_not_model_inference: true,
      native_execution: false, installed_target_acceptance: false, quality_approved: false, human_labels_applied: false, parent_acceptance: false});
  } catch (error) {failed = true; primary = error;} finally {
    try {if (late) await late.close();} catch (error) {cleanupFailures.push(error instanceof Error ? error.name : typeof error); if (!failed) {failed = true; primary = error;}}
    try {page.off('request', observe); observerRemoved = true;} catch (error) {cleanupFailures.push(error instanceof Error ? error.name : typeof error); if (!failed) {failed = true; primary = error;}}
    const retainedAfter: Record<string, unknown> = {};
    for (const scope of [a, b]) try {const after = custody(scope); retainedAfter[scope.tag] = after; expect(after).toEqual(before[scope.tag]);}
    catch (error) {cleanupFailures.push(error instanceof Error ? error.name : typeof error); if (!failed) {failed = true; primary = error;}}
    try {handoffSave(evidence, workspace, 'viewer-handoff-cleanup', {first_failure_retained: failed, cleanup_failures: cleanupFailures, renderer_observer_removed: observerRemoved, before, retained_after: retainedAfter, native_model_or_quality_approval: false});} catch (error) {if (!failed) {failed = true; primary = error;}}
  }
  if (failed) throw primary;
});

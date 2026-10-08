import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Locator, Page, Request, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const files: Record<string, string> = {};
  if (!fs.existsSync(root)) return files;
  const visit = (dir: string) => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(dir, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); files[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return files;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'queue-lifecycle-originals'); fs.mkdirSync(source);
  const originals = ['error', 'disagreement', 'threshold'].map((name, index) => {
    const file = path.join(source, name + '.png'); fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, 100 + index]));
    return {path: file, sha256: fileSha(file), name};
  });
  const project = await api('/api/project/create', {name: 'Owned saved queue lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  // These saved labels and reports are controlled setup records. They do not
  // represent inference, a person's reviewed truth, or annotation approval.
  for (const original of originals) {
    await api('/api/annotations/save', {image_id: original.name, image_path: original.path,
      image_width: 64, image_height: 64, actor: 'queue-lifecycle-fixture', annotations: [
        {id: 'controlled-original-' + original.name, type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]},
      ]});
  }
  const seed = () => JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/review_queue_reports.py'), w.root, project.project_dir, source],
  {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const origin = seed(), alternate = seed(); expect(origin.record.evaluation_id).not.toBe(alternate.record.evaluation_id);
  const queue = await api('/api/data-workbench/review-queues', {evaluation_id: origin.record.evaluation_id, threshold: .5, margin: .05});
  expect(queue.items.map((row: any) => [row.relative_path, row.priority, row.state])).toEqual([
    ['error.png', 400, 'pending'], ['disagreement.png', 200, 'pending'], ['threshold.png', 100, 'pending'],
  ]);
  expect(queue).toMatchObject({cursor: 0, revision: 1, history: [], origin: {evaluation_id: origin.record.evaluation_id}});
  const queueEndpoint = '/api/data-workbench/review-queues/' + queue.id;
  // Real default settings, image metadata and complete annotation reads happen
  // before the baseline; no lock, revision, metadata key or file is filtered.
  const teamBefore = await api('/api/team-data');
  expect(teamBefore).toMatchObject({book: null, book_history: [], settings: {revision: 1, editing_enabled: false, review_enabled: false}});
  const readinessBefore = await api('/api/team-data/readiness');
  const metadataBefore = (await api('/api/dataset/metadata?limit=100')).items; expect(metadataBefore).toHaveLength(3);
  const teamImagesBefore = await Promise.all(metadataBefore.map((row: any) => api('/api/team-data/images/' + row.image_uuid)));
  const annotationsBefore = await Promise.all(originals.map(original => api(annotationRoute(original.path))));
  expect(annotationsBefore.every(row => row.annotations.length === 1 && row.metadata.workflow_state !== 'approved')).toBe(true);
  const preferencesBefore = await api('/api/project/preferences'), versionsBefore = await api('/api/dataset/versions');
  const splitBefore = await api('/api/dataset/metadata/split');
  const evaluationsBefore = await api('/api/data-workbench/review-evaluations');
  expect(evaluationsBefore.evaluations).toHaveLength(2);
  expect(new Set(evaluationsBefore.evaluations.map((row: any) => row.id))).toEqual(new Set([origin.record.evaluation_id, alternate.record.evaluation_id]));
  const defaultEvaluation = evaluationsBefore.evaluations[0].id;
  const unsentEvaluation = evaluationsBefore.evaluations[1].id; expect(unsentEvaluation).not.toBe(defaultEvaluation);
  const queuesBefore = await api('/api/data-workbench/review-queues'), queueBefore = await api(queueEndpoint);
  expect(queuesBefore.queues).toEqual([queue]); expect(queueBefore).toEqual(queue);
  const target = metadataBefore.find((row: any) => row.file_path === queue.items[0].file_path);
  expect(target).toBeDefined(); expect(target.content_hash).toBe(queue.items[0].source_sha256); expect(target.image_uuid).toBeTruthy();
  const roots = {source, annotations: active.annotations_dir || project.annotations_dir,
    reports: path.join(project.project_dir, 'reports'), workbench: path.join(project.dataset_dir, 'data_workbench'),
    splits: path.join(project.dataset_dir, 'splits')};
  const protectedTrees = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)]));
  const baseline = {protectedTrees, teamBefore, readinessBefore, metadataBefore, teamImagesBefore,
    annotationsBefore, preferencesBefore, versionsBefore, splitBefore, evaluationsBefore, queuesBefore, queueBefore, target};
  const baselineFile = path.join(w.logs, 'queue-lifecycle-protected-before.json'); fs.writeFileSync(baselineFile, JSON.stringify(baseline, null, 2)); e.addFile(baselineFile);
  for (const [name, root] of Object.entries(roots)) {
    for (const relative of Object.keys(tree(root))) {
      const snapshot = path.join(w.logs, 'queue-lifecycle-protected-before', name, relative);
      fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
    }
  }
  const writes: any[] = [], queueReads: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const address = new URL(request.url());
    if (!address.pathname.startsWith('/api/')) return;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
      writes.push({method: request.method(), endpoint: address.pathname, body});
    }
    if (request.method() === 'GET' && address.pathname === queueEndpoint) queueReads.push({method: 'GET', endpoint: address.pathname});
  };
  page.on('request', observe);
  const panel = page.getByRole('region', {name: '저장된 검토 큐', exact: true});
  const originChoice = panel.getByLabel('검토 큐 원본 평가', {exact: true});
  const threshold = panel.getByLabel('검토 큐 임계값', {exact: true});
  const margin = panel.getByLabel('검토 큐 임계 주변 범위', {exact: true});
  const queueChoice = panel.getByLabel('저장 검토 큐 선택', {exact: true});
  const create = panel.getByRole('button', {name: '우선순위 큐 저장', exact: true});
  const open = panel.getByRole('button', {name: '현재 항목 열기', exact: true});
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const mountQueue = async () => {
    await stages.getByRole('button').nth(1).click();
    const focus = page.getByRole('button', {name: '집중 편집', exact: true});
    if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
    const summary = page.locator('summary').filter({hasText: '저장 검토 큐 · 오류·불일치·임계값 우선'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    await expect(panel).toContainText('검토 진행 0 / 3'); await expect(open).toBeEnabled();
    await expect(queueChoice).toHaveValue(queue.id);
  };
  const reloadQueue = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await mountQueue();
  };
  const unchanged = async () => {
    for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(protectedTrees[name]);
    expect((await api('/api/dataset/metadata?limit=100')).items).toEqual(metadataBefore);
    expect(await Promise.all(originals.map(original => api(annotationRoute(original.path))))).toEqual(annotationsBefore);
    expect(await api('/api/team-data')).toEqual(teamBefore); expect(await api('/api/team-data/readiness')).toEqual(readinessBefore);
    expect(await Promise.all(metadataBefore.map((row: any) => api('/api/team-data/images/' + row.image_uuid)))).toEqual(teamImagesBefore);
    expect(await api('/api/project/preferences')).toEqual(preferencesBefore); expect(await api('/api/dataset/versions')).toEqual(versionsBefore);
    expect(await api('/api/dataset/metadata/split')).toEqual(splitBefore); expect(await api('/api/data-workbench/review-evaluations')).toEqual(evaluationsBefore);
    expect(await api('/api/data-workbench/review-queues')).toEqual(queuesBefore); expect(await api(queueEndpoint)).toEqual(queueBefore);
    expect(writes).toEqual([]);
  };
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-queue-lifecycle-${name}`);
  };
  const setUnsent = async () => {
    await originChoice.selectOption(unsentEvaluation); await threshold.fill('0.7'); await margin.fill('0.11');
    await expect(originChoice).toHaveValue(unsentEvaluation); await expect(threshold).toHaveValue('0.7');
    await expect(margin).toHaveValue('0.11'); await expect(create).toBeEnabled();
  };
  const defaults = async () => {
    await expect(originChoice).toHaveValue(defaultEvaluation); await expect(threshold).toHaveValue('0.5');
    await expect(margin).toHaveValue('0.05'); await expect(queueChoice).toHaveValue(queue.id);
  };
  let controlledQueueGETs = 0;
  const failQueueRead = async (route: Route) => {
    const request = route.request(); expect(request.method()).toBe('GET');
    expect(new URL(request.url()).pathname).toBe(queueEndpoint); controlledQueueGETs++;
    await route.fulfill({status: 503, json: {detail: 'Controlled exact saved queue GET failure'}});
  };
  try {
    await reloadQueue(); await defaults(); await setUnsent(); await unchanged();
    await capture('valid-unsent-origin-threshold-margin', create);
    // Abandonment by real stage exit is the cancellation exercised here. The
    // product has no explicit confirm/cancel dialog for these queue inputs.
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0); await unchanged();
    await mountQueue(); await defaults(); await unchanged(); await capture('stage-abandonment-reset', create);
    for (const action of ['saved-queue-create', 'saved-queue-threshold', 'saved-queue-margin', 'saved-queue-origin-choice']) {
      controls.push({action: 'U015.' + action, dimension: 'cancel', actual_stage_exit: true,
        cancellation_kind: 'abandoned valid unsent inputs; no explicit cancellation dialog', queue_POSTs: 0});
    }
    await setUnsent(); await capture('valid-unsent-before-actual-reload', create);
    await reloadQueue(); await defaults(); await unchanged(); await capture('actual-reload-reset-defaults', create);
    for (const action of ['saved-queue-threshold', 'saved-queue-margin', 'saved-queue-origin-choice']) {
      controls.push({action: 'U015.' + action, dimension: 'reopen', actual_reload: true,
        abandoned_inputs: {evaluation: unsentEvaluation, threshold: .7, margin: .11},
        reset_inputs: {evaluation: defaultEvaluation, threshold: .5, margin: .05}, saved_queue_unchanged: true, queue_POSTs: 0});
    }
    await page.route('**' + queueEndpoint, failQueueRead);
    try {
      const waiting = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
      await open.click(); expect((await waiting).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled exact saved queue GET failure');
      await expect(panel).toContainText('다음: error.png'); await expect(panel).toContainText('검토 진행 0 / 3');
      await expect(open).toBeEnabled(); await expect(queueChoice).toHaveValue(queue.id);
      for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(protectedTrees[name]);
      expect(writes).toEqual([]); await capture('original-queue-retained-on-exact-get-503', panel.getByRole('alert'));
    } finally {await page.unroute('**' + queueEndpoint, failQueueRead);}
    expect(controlledQueueGETs).toBe(1); await unchanged();
    const openExactOriginal = async (suffix: string) => {
      // Choosing a different real original first guarantees the next open must
      // read and decode the queue's exact file instead of reusing current pixels.
      const other = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: 'disagreement.png', exact: true});
      await other.click(); await expect(other.locator('..')).toHaveClass(/border-blue-500/);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
      const rawEndpoint = '/api/dataset/raw/' + encodeURIComponent(path.basename(target.file_path));
      const rawWaiting = page.waitForResponse(response => {const address = new URL(response.url());
        return response.request().method() === 'GET' && address.pathname === rawEndpoint && address.searchParams.get('file_path') === target.file_path;});
      const annotationWaiting = page.waitForResponse(response => {const address = new URL(response.url());
        return response.request().method() === 'GET' && address.pathname === '/api/annotations/error' && address.searchParams.get('file_path') === target.file_path;});
      const queueWaiting = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
      await open.click(); const realQueue = await queueWaiting; expect(realQueue.status()).toBe(200); expect(await realQueue.json()).toEqual(queueBefore);
      const raw = await rawWaiting, annotation = await annotationWaiting; expect(raw.status()).toBe(200); expect(annotation.status()).toBe(200);
      const rawBytes = await raw.body(), openedAnnotation = await annotation.json();
      expect(rawBytes).toEqual(fs.readFileSync(target.file_path)); expect(sha(rawBytes)).toBe(target.content_hash);
      expect(openedAnnotation).toEqual(annotationsBefore[originals.findIndex(original => original.path === target.file_path)]);
      expect(openedAnnotation.metadata).toMatchObject({image_uuid: target.image_uuid, file_path: target.file_path,
        content_hash: target.content_hash, revision: target.revision});
      const selected = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: 'error.png', exact: true});
      await expect(selected.locator('..')).toHaveClass(/border-blue-500/);
      expect(new URL((await selected.getAttribute('src'))!, page.url()).searchParams.get('file_path')).toBe(target.file_path);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved'); await expect(panel.getByRole('alert')).toHaveCount(0);
      await unchanged(); await capture(suffix, page.locator('[data-labeling-work-area]'));
      const retainedRaw = path.join(w.logs, 'queue-open-' + suffix + '.png'); fs.writeFileSync(retainedRaw, rawBytes); e.addFile(retainedRaw);
      return {real_queue_status: 200, raw_status: 200, annotation_status: 200, image_uuid: target.image_uuid,
        path: target.file_path, sha256: sha(rawBytes), complete_original_byte_equality: true,
        revision: target.revision, annotation_url: annotation.url(), raw_url: raw.url()};
    };
    const retry = await openExactOriginal('real-200-retry-exact-original');
    controls.push({action: 'U015.saved-queue-open', dimension: 'error', exact_scoped_GET_503_count: controlledQueueGETs,
      saved_queue_cursor_history_and_files_unchanged: true, retry});
    await reloadQueue(); await defaults(); await unchanged();
    const reopened = await openExactOriginal('reload-reselected-queue-exact-original');
    controls.push({action: 'U015.saved-queue-open', dimension: 'reopen', actual_reload: true,
      actual_saved_queue_id: queue.id, reopened, cursor: 0, revision: 1, history: [], queue_advanced: false});
    expect(controls).toHaveLength(9); expect(writes).toEqual([]); await unchanged();
    const afterFile = path.join(w.logs, 'queue-lifecycle-protected-after.json'); fs.writeFileSync(afterFile,
      JSON.stringify({trees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])),
        complete_queue: await api(queueEndpoint), metadata: (await api('/api/dataset/metadata?limit=100')).items,
        annotations: await Promise.all(originals.map(original => api(annotationRoute(original.path)))), writes}, null, 2)); e.addFile(afterFile);
    e.note('saved_queue_lifecycle', {cells: controls, baseline, origin, alternate, sourceElectron,
      actual_source_ui: true, protected_files_unfiltered: true, all_api_mutations: writes, queueReads,
      explicit_cancel_dialog_exists: false, cancellation_is_real_stage_abandonment: true,
      labels_saved_after_baseline: false, queue_created_or_advanced_after_baseline: false,
      controlled_reports_not_model_inference: true, human_annotation_or_quality_approval: false,
      actual_training_inference_or_gpu: false, physical_device_or_frozen_package_or_windows_acceptance: false});
  } finally {page.off('request', observe); if (!page.isClosed()) await page.unroute('**' + queueEndpoint, failQueueRead);}
}

test('unsent saved queue choices reset after abandonment and reload while failed open retains original queue', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native unsent queue choices and saved queue exact original open preserve complete source labels and history', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned queue lifecycle API ${response.status}: ${await response.text()}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

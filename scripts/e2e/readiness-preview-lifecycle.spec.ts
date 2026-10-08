import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Locator, Page, Request, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const diagnosticEndpoint = '/api/data-workbench/diagnostics';
const splitEndpoint = '/api/dataset/metadata/split';
const thresholds = {blur_threshold: 1_000_000_000, exposure_fraction: .9, near_distance: 6};
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
const isPost = (request: Request, endpoint: string) => request.method() === 'POST' && new URL(request.url()).pathname === endpoint;
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!fs.existsSync(root)) return result;
  const visit = (directory: string) => {
    for (const entry of fs.readdirSync(directory, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(directory, entry.name);
      expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return result;
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const project = await api('/api/project/create', {name: 'Owned readiness preview lifecycle', task: 'classification'});
  const active = await api('/api/project/update', {source_dataset_dir: workspace.dataset}, 'PUT');
  await api('/api/dataset/import', {folder_path: workspace.dataset, task: 'classification', validate_images: false});
  const imported = (await api('/api/dataset/metadata?limit=10')).items;
  expect(imported).toHaveLength(workspace.images.length);
  for (let index = 0; index < imported.length; index++) {
    const row = imported[index];
    await api('/api/dataset/metadata/' + row.image_uuid, {expected_revision: row.revision, actor: 'readiness-fixture',
      changes: {product: 'saved-product-family', lot: 'preview-lot-' + index, group: 'declared-original-' + index}}, 'PATCH');
  }
  // First-read materialization belongs to setup. No metadata key, annotation
  // file, lock file, split receipt or version is excluded from the baseline.
  const teamBefore = await api('/api/team-data');
  expect(teamBefore).toMatchObject({book: null, book_history: [], settings: {revision: 1, editing_enabled: false, review_enabled: false}});
  await api('/api/team-data/readiness');
  for (const row of imported) await api('/api/team-data/images/' + row.image_uuid);
  const preferencesBefore = await api('/api/project/preferences');
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  const annotationsBefore = await Promise.all(workspace.images.map(image => api(annotationRoute(image.path))));
  const setupSplit = await api(splitEndpoint, {group_by: ['product'], train_ratio: 1, val_ratio: 0, test_ratio: 0, apply: true, actor: 'readiness-fixture'});
  expect(setupSplit).toMatchObject({applied: true, group_count: 1, split: {train: 2, val: 0, test: 0}});
  const savedBefore = await api(splitEndpoint); expect(savedBefore.stale).toBe(false);
  expect(savedBefore.qualification.group_by).toEqual(['product']);
  const diagnosticSeed = await api(diagnosticEndpoint, thresholds);
  expect(diagnosticSeed.items).toHaveLength(2);
  expect(diagnosticSeed.items.every((row: any) => row.issues.includes('blur'))).toBe(true);
  const diagnosticBefore = await api(diagnosticEndpoint);
  const metadataBefore = (await api('/api/dataset/metadata?limit=10')).items;
  const versionsBefore = await api('/api/dataset/versions');
  const annotationRoot = active.annotations_dir || project.annotations_dir;
  const splitRoot = path.join(project.dataset_dir, 'splits');
  const diagnosticFile = path.join(project.project_dir, 'dataset', 'data_workbench', sha(fs.realpathSync(workspace.dataset)).slice(0, 24), 'diagnostics_default.json');
  expect(fs.existsSync(diagnosticFile)).toBe(true);
  const diagnosticBeforeSha = fileSha(diagnosticFile);
  const before = {annotations: tree(annotationRoot), source: tree(workspace.dataset), splits: tree(splitRoot)};
  const target = metadataBefore.find((row: any) => row.file_path === workspace.images.find(image => image.label === 'ng')!.path);
  expect(target).toBeDefined(); expect(target.image_uuid).toBeTruthy(); expect(target.content_hash).toBe(fileSha(target.file_path));
  expect(diagnosticBefore.items.find((row: any) => row.file_path === target.file_path)).toMatchObject({image_uuid: target.image_uuid, source_sha256: target.content_hash, revision: target.revision});
  const setupReceipt = path.join(workspace.logs, 'readiness-preview-protected-before.json');
  fs.writeFileSync(setupReceipt, JSON.stringify({project, before, teamBefore, preferencesBefore, metadataBefore, annotationsBefore, versionsBefore, savedBefore, diagnosticBefore, diagnosticBeforeSha, target}, null, 2));
  evidence.addFile(setupReceipt);
  for (const [kind, root] of [['annotations', annotationRoot], ['splits', splitRoot], ['source', workspace.dataset]]) {
    for (const relative of Object.keys(tree(root))) {
      const snapshot = path.join(workspace.logs, 'protected-before', kind, relative);
      fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); evidence.addFile(snapshot);
    }
  }
  const diagnosticCopy = path.join(workspace.logs, 'diagnostics-before.json'); fs.copyFileSync(diagnosticFile, diagnosticCopy); evidence.addFile(diagnosticCopy);
  const requests: any[] = [], forbiddenWrites: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const endpoint = new URL(request.url()).pathname;
    if (!endpoint.startsWith('/api/') || ['GET', 'HEAD', 'OPTIONS'].includes(request.method())) return;
    let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
    const row = {method: request.method(), endpoint, body}; requests.push(row);
    if (!(isPost(request, diagnosticEndpoint) || isPost(request, splitEndpoint) && (row.body as {apply?: unknown})?.apply === false)) forbiddenWrites.push(row);
  };
  page.on('request', observe);
  const readiness = page.getByRole('region', {name: '데이터 준비 진단', exact: true});
  const panel = page.getByRole('region', {name: '데이터 검토와 라벨 교환', exact: true});
  const navigate = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
  };
  const openReadiness = async () => {
    const summary = page.locator('summary').filter({hasText: '데이터 준비 상태 · 품질과 중복 진단'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    await expect(readiness.getByText('전체 2장', {exact: true})).toBeVisible();
  };
  const openWorkflow = async () => {
    const toggle = page.getByRole('button', {name: '이미지 검토·그룹 분할·라벨 교환', exact: true});
    if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click();
    await expect(panel).toContainText('검색 결과 2개'); await expect(panel.getByRole('status').filter({hasText: '처리 중'})).toHaveCount(0);
  };
  const requestFromButton = async (endpoint: string, button: Locator) => {
    const waiting = page.waitForResponse(response => isPost(response.request(), endpoint)); await button.click(); return waiting;
  };
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await evidence.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-readiness-${name}`);
  };
  const protectedState = async () => {
    expect(tree(annotationRoot)).toEqual(before.annotations); expect(tree(workspace.dataset)).toEqual(before.source); expect(tree(splitRoot)).toEqual(before.splits);
    expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(metadataBefore);
    expect(await Promise.all(workspace.images.map(image => api(annotationRoute(image.path))))).toEqual(annotationsBefore);
    expect(await api('/api/team-data')).toEqual(teamBefore); expect(await api('/api/project/preferences')).toEqual(preferencesBefore);
    expect(await api('/api/dataset/versions')).toEqual(versionsBefore); expect(await api(splitEndpoint)).toEqual(savedBefore);
    expect(forbiddenWrites).toEqual([]);
  };
  try {
    await navigate(); await openReadiness(); await readiness.getByLabel('흐림 진단 기준', {exact: true}).fill(String(thresholds.blur_threshold));
    const diagnose = readiness.getByRole('button', {name: '준비 상태 진단', exact: true}); await expect(diagnose).toBeEnabled();
    const reportRowsBefore = await readiness.locator('tbody').innerText(); let diagnosticFailures = 0;
    const diagnosticFailure = async (route: Route) => {
      if (route.request().method() !== 'POST') {await route.fallback(); return;}
      expect(isPost(route.request(), diagnosticEndpoint)).toBe(true); expect(route.request().postDataJSON()).toEqual(thresholds);
      diagnosticFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled readiness diagnosis transport failure'}});
    };
    await page.route('**' + diagnosticEndpoint, diagnosticFailure);
    try {
      expect((await requestFromButton(diagnosticEndpoint, diagnose)).status()).toBe(503);
      await expect(readiness.getByRole('alert')).toContainText('Controlled readiness diagnosis transport failure'); await expect(diagnose).toBeEnabled();
      expect(await readiness.locator('tbody').innerText()).toBe(reportRowsBefore); expect(fileSha(diagnosticFile)).toBe(diagnosticBeforeSha);
      expect(await api(diagnosticEndpoint)).toEqual(diagnosticBefore); await protectedState(); await capture('diagnosis-503-saved-report-preserved', readiness.getByRole('alert'));
    } finally {await page.unroute('**' + diagnosticEndpoint, diagnosticFailure);}
    expect(diagnosticFailures).toBe(1);
    const realDiagnosis = await requestFromButton(diagnosticEndpoint, diagnose); expect(realDiagnosis.status()).toBe(200);
    const retriedDiagnosis = await realDiagnosis.json(); expect(retriedDiagnosis).toEqual(diagnosticSeed); await expect(readiness.getByRole('alert')).toHaveCount(0);
    await expect(diagnose).toBeEnabled(); await protectedState(); await capture('actual-diagnosis-retry', readiness.getByRole('row').filter({hasText: target.relative_path}));
    controls.push({action: 'U013.readiness-diagnose', dimension: 'error', controlled_status: 503, saved_report_sha256_preserved: diagnosticBeforeSha, retry_actual_backend_status: 200});
    const rawPath = '/api/dataset/raw/' + encodeURIComponent(path.basename(target.file_path));
    const rawWaiting = page.waitForResponse(response => {const address = new URL(response.url()); return response.request().method() === 'GET' && address.pathname === rawPath && address.searchParams.get('file_path') === target.file_path;});
    const annotationWaiting = page.waitForResponse(response => {const address = new URL(response.url()); return response.request().method() === 'GET' && address.pathname === '/api/annotations/' + path.basename(target.file_path, '.png') && address.searchParams.get('file_path') === target.file_path;});
    await readiness.getByRole('row').filter({hasText: target.relative_path}).getByRole('button', {name: '이미지 검토', exact: true}).click();
    const raw = await rawWaiting, annotation = await annotationWaiting; expect(raw.status()).toBe(200); expect(annotation.status()).toBe(200);
    const rawSha = sha(await raw.body()), handoff = await annotation.json(); expect(rawSha).toBe(target.content_hash);
    expect(handoff.metadata).toMatchObject({image_uuid: target.image_uuid, file_path: target.file_path, content_hash: target.content_hash, revision: target.revision});
    expect(handoff).toEqual(annotationsBefore[workspace.images.findIndex(image => image.path === target.file_path)]);
    await expect(page.locator('[data-labeling-work-area]')).toBeVisible();
    const selectedImage = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: path.basename(target.file_path), exact: true});
    await expect(selectedImage.locator('..')).toHaveClass(/border-blue-500/);
    expect(new URL((await selectedImage.getAttribute('src'))!, page.url()).searchParams.get('file_path')).toBe(target.file_path);
    await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved'); await protectedState();
    await capture('actual-exact-original-review-handoff', page.locator('[data-labeling-work-area]'));
    controls.push({action: 'U013.readiness-diagnose', dimension: 'handoff', stage: 2, image_uuid: target.image_uuid, revision: target.revision, file_path: target.file_path, source_sha256: target.content_hash, actual_raw_response_sha256: rawSha, annotation_url: annotation.url(), raw_url: raw.url(), labels_saved: false});
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click(); await openWorkflow();
    await expect(panel).toContainText('독립 그룹 1개'); await expect(panel.getByLabel('저장된 분할 근거', {exact: true})).toContainText('저장 기준 product');
    await expect(panel.getByRole('button', {name: '분할 적용', exact: true})).toBeDisabled();
    await panel.getByLabel('분할 그룹 기준', {exact: true}).selectOption('lot');
    const setRatios = async (values: number[]) => {for (const [index, name] of ['학습', '검증', '시험'].entries()) await panel.getByLabel(name + ' 그룹 분할 비율', {exact: true}).fill(String(values[index]));};
    const preview = panel.getByRole('button', {name: '분할 미리보기', exact: true});
    await setRatios([60, 30, 0]); const invalid = await requestFromButton(splitEndpoint, preview); expect(invalid.status()).toBe(422);
    expect(await invalid.json()).toMatchObject({detail: {message: 'Split ratios must sum to one', availability: 'unavailable'}});
    await expect(panel.getByRole('alert')).toContainText('Split ratios must sum to one'); await expect(preview).toBeEnabled();
    await expect(panel.getByRole('button', {name: '분할 적용', exact: true})).toBeDisabled(); await protectedState(); await capture('actual-ratio-sum-refusal-422', panel.getByRole('alert'));
    controls.push({action: 'U013.group-split-preview', dimension: 'invalid', ratios: [.6, .3, 0], actual_backend_status: 422, saved_split_unchanged: true, split_applied: false});
    await setRatios([50, 50, 0]); let splitFailures = 0;
    const splitFailure = async (route: Route) => {
      if (route.request().method() !== 'POST') {await route.fallback(); return;}
      expect(isPost(route.request(), splitEndpoint)).toBe(true);
      expect(route.request().postDataJSON()).toEqual({group_by: ['lot'], train_ratio: .5, val_ratio: .5, test_ratio: 0, apply: false, actor: 'operator'});
      splitFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled grouped split preview transport failure'}});
    };
    await page.route('**' + splitEndpoint, splitFailure);
    try {
      expect((await requestFromButton(splitEndpoint, preview)).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled grouped split preview transport failure'); await expect(preview).toBeEnabled();
      await expect(panel.getByRole('button', {name: '분할 적용', exact: true})).toBeDisabled(); await protectedState(); await capture('split-preview-503-no-apply', panel.getByRole('alert'));
    } finally {await page.unroute('**' + splitEndpoint, splitFailure);}
    expect(splitFailures).toBe(1); const actualPreview = await requestFromButton(splitEndpoint, preview); expect(actualPreview.status()).toBe(200);
    const previewBody = await actualPreview.json(); expect(previewBody).toMatchObject({applied: false, apply_supported: true, group_count: 2, split: {train: 1, val: 1, test: 0}});
    expect(previewBody.qualification.group_by).toEqual(['lot']); expect(previewBody.qualification.sha256).not.toBe(savedBefore.qualification.sha256);
    await expect(panel).toContainText('독립 그룹 2개'); await expect(panel.getByLabel('저장된 분할 근거', {exact: true})).toContainText('저장 기준 lot');
    await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel.getByRole('button', {name: '분할 적용', exact: true})).toBeEnabled();
    await protectedState(); await capture('actual-split-preview-retry-unsaved', panel.getByLabel('저장된 분할 근거', {exact: true}));
    controls.push({action: 'U013.group-split-preview', dimension: 'error', controlled_status: 503, retry_actual_backend_status: 200, preview_qualification_sha256: previewBody.qualification.sha256, split_applied: false});
    const beforeReloadPosts = requests.length; await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await openWorkflow();
    await expect(panel.getByLabel('분할 그룹 기준', {exact: true})).toHaveValue('lot');
    for (const [index, name] of ['학습', '검증', '시험'].entries()) await expect(panel.getByLabel(name + ' 그룹 분할 비율', {exact: true})).toHaveValue(String([70, 20, 10][index]));
    await expect(panel).toContainText('독립 그룹 1개'); await expect(panel).not.toContainText('독립 그룹 2개');
    await expect(panel.getByLabel('저장된 분할 근거', {exact: true})).toContainText('저장 기준 product');
    await expect(panel.getByRole('button', {name: '분할 적용', exact: true})).toBeDisabled(); await expect(panel.getByRole('alert')).toHaveCount(0);
    expect(requests.length).toBe(beforeReloadPosts); await protectedState(); await capture('actual-reload-discards-unsent-preview', panel.getByLabel('저장된 분할 근거', {exact: true}));
    controls.push({action: 'U013.group-split-preview', dimension: 'reopen', actual_full_reload: true, unsent_preview_discarded: true, saved_qualification_sha256: savedBefore.qualification.sha256, split_applied: false, extra_post: 0});
    expect(requests.filter(row => row.endpoint === diagnosticEndpoint)).toHaveLength(2);
    expect(requests.filter(row => row.endpoint === splitEndpoint)).toHaveLength(3);
    expect(requests).toHaveLength(5); expect(forbiddenWrites).toEqual([]);
    const after = {annotations: tree(annotationRoot), source: tree(workspace.dataset), splits: tree(splitRoot)}; expect(after).toEqual(before);
    const diagnosticAfter = await api(diagnosticEndpoint); expect(diagnosticAfter).toEqual(diagnosticBefore);
    expect(fileSha(diagnosticFile)).toBe(diagnosticBeforeSha);
    evidence.addFile(diagnosticFile); for (const image of workspace.images) {expect(fileSha(image.path)).toBe(image.sha256); evidence.addFile(image.path);}
    evidence.note('readiness_preview_lifecycle', {project, controls, requests, forbiddenWrites, before, after, metadata_before: metadataBefore, metadata_after: (await api('/api/dataset/metadata?limit=10')).items,
      annotations_before: annotationsBefore, annotations_after: await Promise.all(workspace.images.map(image => api(annotationRoute(image.path)))), diagnostic_before: diagnosticBefore, diagnostic_after: diagnosticAfter,
      saved_split_before: savedBefore, saved_split_after: await api(splitEndpoint), actual_source_ui: true, source_electron: sourceElectron, diagnostic_transport_controlled: true, preview_transport_controlled: true,
      protected_baseline_after_real_default_setup: true, annotation_or_review_or_split_apply_posts: 0, labels_saved: false, actual_model_inference: false, training_or_job_dispatch: false, gpu_used: false,
      physical_device_acceptance: false, frozen_native_acceptance: false, installed_target_acceptance: false, human_quality_accepted: false, windows_excluded: true});
  } finally {
    page.off('request', observe);
    evidence.note('readiness_preview_observed_requests', {requests, forbiddenWrites, completed_controls: controls});
  }
}

test('readiness diagnosis and unsent grouped split lifecycle preserve exact originals and saved split', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {const response = await request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();};
  await exercise(page, workspace, evidence, api, false, renderer.url);
});

test('source Electron readiness diagnosis and unsent grouped split lifecycle preserve exact originals and saved split', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})}); if (!response.ok) throw Error(`Owned readiness fixture HTTP ${response.status}: ${await response.text()}`); return response.json();}, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {APIRequestContext, Page, Route} from '@playwright/test';
import {expect, test, type Evidence, type Workspace} from './fixtures/test';
import {openWorkspaceSettings} from './fixtures/workspaceSettings';
import {png} from './qa/appFlow';

// Real isolated renderer/backend and imported synthetic PNGs. Model, evaluation
// and inspection identities below are explicitly controlled GET display records;
// this suite never qualifies a model, an executed inspection, or field quality.
test.use({viewport: {width: 1440, height: 1000}, actionTimeout: 10_000});

const digest = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');
type Fixture = Awaited<ReturnType<typeof fixture>>;
async function fixture(request: APIRequestContext, origin: string, workspace: Workspace, name: string) {
  const source = path.join(workspace.root, 'workflow-photos');
  const files: Array<{path: string; sha256: string}> = [];
  for (const split of ['train', 'val', 'test']) for (const label of ['OK', 'NG']) {
    for (let i = 0; i < (split === 'test' ? 2 : 1); i++) {
      const file = path.join(source, split, label, `${split}-${label}-${i}.png`);
      const bytes = png(64, 3, (x, y) => [x + 64, y + 64, 20 + files.length * 25]);
      fs.mkdirSync(path.dirname(file), {recursive: true}); fs.writeFileSync(file, bytes);
      files.push({path: file, sha256: digest(bytes)});
    }
  }
  const api = async (route: string, body?: unknown, method?: string) => {
    const response = await request.fetch(origin + route, {
      method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body}),
    });
    expect(response.ok(), `${route}: ${await response.text()}`).toBe(true);
    return response.json();
  };
  const project = await api('/api/project/create', {name, task: 'classification'});
  await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification'});
  const images = (await api('/api/dataset/images?' + new URLSearchParams({folder_path: source, task: 'classification', limit: '100'}))).items;
  expect(images).toHaveLength(8);
  return {source, files, project, images, api};
}

async function selectStep(page: Page, step: number) {
  const goal = ['', 'photos', 'teach', 'teach', 'check', 'use', 'use'][step];
  await page.locator(`[data-beginner-goal="${goal}"]`).click();
  await page.locator(`[data-beginner-step="${step}"]`).click();
  await expect(page.locator(`[data-beginner-step="${step}"]`)).toHaveAttribute('aria-current', 'step');
}
async function simple(page: Page, url: string, projectName: string) {
  await page.goto(url); await expect(page.locator('#workflow-project')).toContainText(projectName);
  await page.getByRole('button', {name: '간단히 보기', exact: true}).click();
}
async function theme(page: Page, value: 'light' | 'dark') {
  await openWorkspaceSettings(page);
  const button = page.getByRole('button', {name: value === 'light' ? '밝은 화면' : '어두운 화면', exact: true});
  if (await button.isVisible()) await button.click(); else await page.keyboard.press('Escape');
  await expect(page.locator('html')).toHaveAttribute('data-theme', value);
}
async function decodedPhoto(page: Page) {
  const image = page.locator('img:visible').first(); await expect(image).toBeVisible();
  await expect.poll(() => image.evaluate(node => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0)).toBe(true);
}
async function settledLabelingPixels(page: Page, evidence: Evidence) {
  await expect(page.getByText('기존 라벨을 불러오는 중입니다.', {exact: true})).toHaveCount(0);
  await expect(page.getByText('기존 라벨을 불러오지 못해 편집을 중지했습니다. 위의 다시 시도를 누르세요.', {exact: true})).toHaveCount(0);
  const base = page.locator('[data-canvas-container] > canvas').first();
  await expect(base).toBeVisible();
  const centerPixel = () => base.evaluate(node => {
    const canvas = node as HTMLCanvasElement;
    if (!canvas.width || !canvas.height) return [];
    return Array.from(canvas.getContext('2d')!.getImageData(Math.floor(canvas.width / 2), Math.floor(canvas.height / 2), 1, 1).data);
  });
  // Every owned fixture has an opaque colored source center. The base raster
  // stays transparent until the selected image is actually decoded and painted.
  await expect.poll(async () => {
    const [r, g, b, a] = await centerPixel();
    return r >= 64 && r <= 127 && g >= 64 && g <= 127 && b >= 20 && b <= 195 && a === 255;
  }).toBe(true);
  evidence.note('settled_labeling_base_source_pixels', {rgba: await centerPixel(), canvas_bounds: await base.boundingBox(), owned_synthetic_source: true});
}
async function noPageOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}
function preserve(f: Fixture, evidence: Evidence, extra: Record<string, unknown> = {}) {
  for (const image of f.files) expect(digest(fs.readFileSync(image.path))).toBe(image.sha256);
  evidence.note('product_workflow_scope', {synthetic_pngs: f.files, gallery_image_element_decode: true,
    controlled_get_records_not_model_execution: true, actual_training_or_inspection_or_GPT: false,
    field_quality_or_installed_runtime_accepted: false, ...extra});
}
const prohibited = (method: string, pathname: string) => method !== 'GET'
  && /^\/api\/(?:training\/(?:start|stop)|model-execution(?:\/|$)|flowchart\/run|inspections\/runs(?:\/|$)|assistant(?:\/|$)|chatgpt(?:\/|$))/.test(pathname);
async function guard(page: Page, controlled: (route: Route, url: URL) => Promise<boolean>, writes: string[]) {
  const httpErrors: Array<{method: string; path: string; status: number}> = [];
  page.on('response', response => {
    if (response.status() >= 400) httpErrors.push({method: response.request().method(), path: new URL(response.url()).pathname, status: response.status()});
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url());
    if (prohibited(request.method(), url.pathname)) {
      writes.push(`${request.method()} ${url.pathname}`);
      await route.fulfill({status: 409, contentType: 'application/json', body: JSON.stringify({detail: 'Display-only QA never executes model or inspection work'})}); return;
    }
    if (request.method() === 'GET' && await controlled(route, url)) return;
    await route.continue();
  });
  return httpErrors;
}
const fulfill = (route: Route, body: unknown, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});

test('classification tools keep optional regions separate and keyboard-selected advanced tools discoverable', async ({page, request, renderer, workspace, evidence}) => {
  const f = await fixture(request, renderer.origin, workspace, '작업 도구 표시 검증');
  const forbidden: string[] = []; const httpErrors = await guard(page, async () => false, forbidden);
  await simple(page, renderer.url, '작업 도구 표시 검증'); await theme(page, 'light');
  await decodedPhoto(page); await selectStep(page, 2); await settledLabelingPixels(page, evidence);
  const toolbar = page.locator('[data-labeling-toolbar]'), other = toolbar.locator('details[data-labeling-other-tools]');
  await expect(toolbar).toContainText('사진 분류 · 영역은 선택 사항');
  await expect(page.getByText('선택적으로 그린 영역이 없습니다', {exact: true})).toBeVisible();
  await toolbar.getByRole('button', {name: '선택', exact: true}).click();
  await expect(other).not.toHaveAttribute('open', '');
  await expect(toolbar.getByRole('button', {name: '선택', exact: true})).toHaveAttribute('aria-pressed', 'true');
  await expect(toolbar.getByRole('button', {name: '다각형', exact: true})).not.toBeVisible();
  await page.keyboard.press('4');
  const polygon = toolbar.getByRole('button', {name: '다각형', exact: true});
  await expect(polygon).toBeVisible(); await expect(polygon).toHaveAttribute('aria-pressed', 'true');
  expect(await polygon.evaluate(node => node.closest('details') === null)).toBe(true);
  await expect(other).not.toHaveAttribute('open', '');
  const summary = other.locator(':scope > summary'); await summary.focus(); await page.keyboard.press('Enter');
  await expect(other).toHaveAttribute('open', '');
  await other.getByRole('button', {name: '회전 박스', exact: true}).click();
  await expect(other).not.toHaveAttribute('open', ''); await expect(summary).toBeFocused();
  const rotated = toolbar.getByRole('button', {name: '회전 박스', exact: true});
  await expect(rotated).toBeVisible(); await expect(rotated).toHaveAttribute('aria-pressed', 'true');
  expect(await rotated.evaluate(node => node.closest('details') === null)).toBe(true);
  await expect(page.getByText('선택적으로 그린 영역이 없습니다', {exact: true})).toBeVisible();
  await evidence.screenshot(page, 'classification-light-active-optional-tool');
  await page.setViewportSize({width: 720, height: 1000}); await theme(page, 'dark');
  await page.getByRole('complementary', {name: '다음 할 일'}).getByRole('button', {name: '도우미 열기', exact: true}).click();
  await expect(page.getByRole('heading', {name: 'ChatGPT 도우미', exact: true})).toBeVisible();
  await expect(rotated).toHaveAttribute('aria-pressed', 'true'); await noPageOverflow(page);
  await expect(toolbar.getByTestId('annotation-save-button')).toBeVisible();
  await settledLabelingPixels(page, evidence);
  await evidence.screenshot(page, 'classification-dark-720-assistant-open');
  expect(forbidden).toEqual([]); preserve(f, evidence, {keyboard_active_tool_preserved: true, optional_regions_not_class_truth: true, settled_labeling_base_raster_painted: true, http_errors: httpErrors});
});

test('evaluation summary opens preview errors without changing saved threshold or executing evaluation', async ({page, request, renderer, workspace, evidence}) => {
  const f = await fixture(request, renderer.origin, workspace, '오류 사진 탐색 검증');
  const job = 'job_display_evaluation_20261116', roles = {version: 1, roles: {OK: 'normal', NG: 'defect'}};
  const image = (name: string) => f.images.find((row: any) => row.file_name === name);
  const cases = [
    {...image('test-NG-0.png'), ground_truth: 'NG', predicted_class: 'OK', confidence: .7, defect_score: .3, is_correct: false},
    {...image('test-NG-1.png'), ground_truth: 'NG', predicted_class: 'NG', confidence: .65, defect_score: .65, is_correct: true},
    {...image('test-OK-0.png'), ground_truth: 'OK', predicted_class: 'NG', confidence: .8, defect_score: .8, is_correct: false},
    {...image('test-OK-1.png'), ground_truth: 'OK', predicted_class: 'OK', confidence: .9, defect_score: .1, is_correct: true},
  ];
  expect(cases.every(row => row.file_path)).toBe(true);
  const catalog = {models: [{job_id: job, task: 'classification', label: '표시 전용 모델', preset: 'fast', created_at: '2026-11-16T10:30:00Z',
    source_dataset_path: f.source, training_labelset_id: 'default', class_names: ['OK', 'NG'], class_semantics: roles}], total: 1};
  const result = {job_id: job, task: 'classification', zero_underkill_calibrated: false, class_semantics: roles,
    metrics: {active_threshold: .5, accuracy: .5}, confusion_matrix: {classes: ['OK', 'NG'], matrix: [[1, 1], [1, 1]], normalized_matrix: [[.5, .5], [.5, .5]], cell_samples: {}}, test_predictions: cases};
  const forbidden: string[] = [], controlled: string[] = []; let failRead = false;
  const httpErrors = await guard(page, async (route, url) => {
    const p = url.pathname;
    if (['/api/flowchart/models/catalog', '/api/evaluation/model-comparisons/models'].includes(p)) {controlled.push(p); await fulfill(route, catalog); return true;}
    if (p === '/api/evaluation/results') {
      expect(url.searchParams.get('source_dataset_path')).toBe(f.source); expect(url.searchParams.get('source_task')).toBe('classification');
      controlled.push(p); await fulfill(route, failRead ? {detail: '표시 전용 평가 읽기 오류'} : result, failRead ? 503 : 200); return true;
    }
    if (p === '/api/evaluation/overkill-underkill') {await fulfill(route, null); return true;}
    if (p.startsWith('/api/evaluation/heatmap/')) {await fulfill(route, {overlay_base64: null}); return true;}
    return false;
  }, forbidden);
  await simple(page, renderer.url, '오류 사진 탐색 검증'); await theme(page, 'light'); await decodedPhoto(page); await selectStep(page, 4);
  const misses = page.getByRole('button', {name: '미리보기 미검 이미지 보기', exact: true});
  await expect(misses).toContainText('1장');
  const model = page.getByLabel('평가 모델', {exact: true});
  const option = model.locator(`option[value="${job}"]`); await expect(option).toContainText('사진 분류');
  await expect(option).not.toContainText(job); await model.selectOption(job); await expect(model).toHaveValue(job);
  // Model selection clears the old displayed result; the explicit read remains a GET.
  await page.locator('#evaluation-refresh').click(); await expect(misses).toContainText('1장');
  const stats = page.locator('details[aria-label="평가 고급 통계와 도구"]'), technical = page.locator('details[aria-label="평가 기술 근거"]');
  await expect(stats).not.toHaveAttribute('open', ''); await expect(technical).not.toHaveAttribute('open', '');
  await misses.click(); await expect(misses).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('tab', {name: '미검 (1)', exact: true})).toHaveAttribute('aria-selected', 'true');
  await stats.locator(':scope > summary').click(); await page.getByLabel('평가 판정 임계값', {exact: true}).fill('0.75');
  await expect(misses).toContainText('2장');
  const saved = page.locator('#workflow-missed-defects'); await saved.click();
  await expect(saved).toHaveAttribute('aria-expanded', 'true'); await expect(page.getByLabel('평가 결과 기준', {exact: true})).toContainText('0.75 · 저장된 평가 기준 0.50');
  await expect(page.getByLabel('평가 결과 기준', {exact: true})).toContainText('저장된 미검 목록');
  await misses.click(); await expect(saved).toHaveAttribute('aria-expanded', 'false'); await expect(misses).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('tab', {name: '미검 (2)', exact: true})).toHaveAttribute('aria-selected', 'true');
  await stats.locator(':scope > summary').click(); await page.setViewportSize({width: 720, height: 900}); await noPageOverflow(page);
  await misses.click();
  // The original tabpanel intentionally uses display: contents, so assert on
  // the real focused heading and decoded photo boxes rather than its zero-size box.
  const photoReview = page.locator('#evaluation-photo-review');
  const firstErrorPhoto = page.locator('#evaluation-image-list img').first();
  await expect(photoReview).toBeFocused();
  await expect(photoReview).toBeInViewport({ratio: .8});
  const filterReadability = await page.getByRole('tablist', {name: '평가 이미지 필터', exact: true}).getByRole('tab').evaluateAll(tabs => tabs.map(node => ({
    text: node.textContent, client_width: node.clientWidth, scroll_width: node.scrollWidth,
    font_size: getComputedStyle(node).fontSize, white_space: getComputedStyle(node).whiteSpace, overflow: getComputedStyle(node).overflow,
  })));
  evidence.note('filter_readability', filterReadability);
  expect(filterReadability.filter(tab => tab.scroll_width > tab.client_width), 'image-filter names and counts must remain readable at 720px').toEqual([]);
  await expect(firstErrorPhoto).toBeVisible();
  await expect.poll(() => firstErrorPhoto.evaluate(node => (node as HTMLImageElement).naturalWidth > 0)).toBe(true);
  evidence.note('summary_navigation_actual_boxes', {viewport: page.viewportSize(), heading_bounds: await photoReview.boundingBox(), image_bounds: await firstErrorPhoto.boundingBox()});
  await evidence.screenshot(page, 'evaluation-summary-target-before-viewport-assert');
  await expect(firstErrorPhoto, 'summary action must reveal its related error photograph at a narrow viewport').toBeInViewport({ratio: .2});
  await evidence.screenshot(page, 'evaluation-preview-vs-saved-720');
  failRead = true; await page.locator('#evaluation-refresh').click();
  const alert = page.getByRole('alert').filter({hasText: '표시 전용 평가 읽기 오류'}).first(); await expect(alert).toBeVisible();
  expect(await alert.evaluate(node => node.closest('details') === null)).toBe(true);
  await expect(stats).not.toHaveAttribute('open', ''); await evidence.screenshot(page, 'evaluation-read-error-outside-disclosure');
  expect(forbidden).toEqual([]); preserve(f, evidence, {controlled_get_paths: controlled, preview_threshold: .75, saved_threshold: .5, executed_evaluations: 0, http_errors: httpErrors});
});

test('inspection current saved choice stays distinct from historical execution and legacy evidence', async ({page, request, renderer, workspace, evidence}) => {
  const f = await fixture(request, renderer.origin, workspace, '검사 실행 근거 표시 검증');
  const current = {id: 'flow-current-display', name: '현재 표면 검사', nodes: [{id: 'model-current', position: {x: 0, y: 0},
    data: {label: '현재 모델', node_type: 'inspection', task: 'classification', model_job_id: 'job-current-display'}}], edges: []};
  const currentVersion = {version_id: 'version-current-display', pipeline_id: current.id, pipeline_hash: 'a'.repeat(64),
    is_active: true, recipe_task: 'classification', name: current.name, saved_at: '2026-11-16T10:30:00Z'};
  const history = {run_id: 'run-history-display', source_folder: f.source, canonical_source_folder: f.source, task: 'classification', scope: 'test',
    pipeline_id: 'flow-history-display', pipeline_name: '어제 검사 흐름', saved_version_id: 'version-history-display', pipeline_hash: 'b'.repeat(64),
    model_sha256: {'job-history-display': 'c'.repeat(64)}, execution_config: {execution_target: 'local', device: 'cpu'},
    status: 'completed', rows: [], created_at: '2026-11-15T10:30:00Z', updated_at: '2026-11-15T10:30:01Z', total: 0, counts: {}};
  const legacy = {...history, run_id: 'run-legacy-display', pipeline_name: '옛 검사 기록', saved_version_id: null, pipeline_hash: '', model_sha256: {}};
  const forbidden: string[] = [];
  const httpErrors = await guard(page, async (route, url) => {
    const p = url.pathname;
    if (p === '/api/flowchart/pipelines') {await fulfill(route, {pipelines: [currentVersion], total: 1}); return true;}
    if (p === `/api/flowchart/pipelines/${currentVersion.version_id}`) {await fulfill(route, current); return true;}
    if (p === '/api/inspections/runs') {await fulfill(route, {runs: [history, legacy]}); return true;}
    if (p === `/api/inspections/runs/${history.run_id}`) {await fulfill(route, history); return true;}
    if (p === `/api/inspections/runs/${legacy.run_id}`) {await fulfill(route, legacy); return true;}
    if (p === '/api/inspections/review-queue') {await fulfill(route, {items: [], total: 0, review_required: 0, diagnostic_errors: 0}); return true;}
    return false;
  }, forbidden);
  await simple(page, renderer.url, '검사 실행 근거 표시 검증'); await theme(page, 'light'); await decodedPhoto(page); await selectStep(page, 6);
  // The export panel also reuses this shared card; target the next-run owner.
  const choice = page.getByText('다음 검사에 사용할 활성 플로우', {exact: true}).locator('..')
    .getByLabel('검사 플로우 저장 버전 정보', {exact: true});
  await expect(choice).toContainText(current.name); await expect(choice).toContainText('모델 1개 연결');
  const recorded = page.getByRole('region', {name: '선택한 검사 실행 식별자', exact: true});
  await expect(recorded).toContainText(history.pipeline_name); await expect(recorded).toContainText('현재 선택과 별도로');
  await expect(choice.locator('details')).not.toHaveAttribute('open', ''); await expect(recorded.locator('details')).not.toHaveAttribute('open', '');
  await choice.locator('summary').click();
  for (const value of [currentVersion.version_id, current.id, currentVersion.pipeline_hash, 'job-current-display']) await expect(choice).toContainText(value);
  await recorded.locator('summary').click();
  for (const value of [history.run_id, history.pipeline_id, history.saved_version_id, history.pipeline_hash, 'job-history-display', 'c'.repeat(64)]) await expect(recorded).toContainText(value);
  await expect(recorded).not.toContainText(current.name); await expect(recorded).not.toContainText('job-current-display');
  await evidence.screenshot(page, 'inspection-current-choice-and-historical-snapshot');
  await page.locator('#workflow-inspection-history').getByRole('button').filter({hasText: legacy.pipeline_name}).click();
  await expect(recorded).toContainText(legacy.pipeline_name); const missing = recorded.getByRole('status');
  await expect(missing).toBeVisible(); await expect(missing).toContainText('미기록');
  expect(await missing.evaluate(node => node.closest('details') === null)).toBe(true);
  await expect(choice).toContainText(current.name); await expect(recorded).not.toContainText('job-current-display');
  await page.setViewportSize({width: 720, height: 1000}); await theme(page, 'dark'); await noPageOverflow(page);
  await expect(page.getByRole('button', {name: '검사 시작', exact: true})).toBeVisible();
  await evidence.screenshot(page, 'inspection-legacy-evidence-dark-720');
  expect(forbidden).toEqual([]); preserve(f, evidence, {http_errors: httpErrors, controlled_current_flow: currentVersion, controlled_historical_run: history.run_id,
    controlled_legacy_run: legacy.run_id, selected_execution_never_borrowed_current_identity: true});
});

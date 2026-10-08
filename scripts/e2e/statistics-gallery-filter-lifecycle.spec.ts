import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Locator, Page, Request, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
const statisticsEndpoint = '/api/dataset/metadata/statistics';
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const files: Record<string, string> = {};
  if (!fs.existsSync(root)) return files;
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); files[path.relative(root, file).split(path.sep).join('/')] = sha(fs.readFileSync(file));}
    }
  };
  visit(root); return files;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'statistics-gallery-originals'); fs.mkdirSync(source);
  const originals = [['train', 'TrainOnly', 'train-only.png'], ['test', 'TestOnly', 'test-only.png']].map(([split, label, name], index) => {
    const file = path.join(source, split, label, name); fs.mkdirSync(path.dirname(file), {recursive: true});
    fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, 80 + index]));
    return {split, label, name, path: file, sha256: sha(fs.readFileSync(file))};
  });
  const project = await api('/api/project/create', {name: 'Owned statistics gallery lifecycle', task: 'classification'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'classification', validate_images: false});
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const panel = page.getByRole('region', {name: '활성 라벨 세트 데이터 통계', exact: true});
  const refresh = panel.getByRole('button', {name: '데이터 통계 새로고침', exact: true});
  const navigate = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await stages.getByRole('button').nth(0).click();
    await expect(panel).toContainText('전체 이미지 2개'); await expect(refresh).toBeEnabled();
    await expect(page.getByRole('button', {name: 'train-only.png 라벨링에서 열기', exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: 'test-only.png 라벨링에서 열기', exact: true})).toBeVisible();
  };
  const expandClasses = async () => {
    const summary = panel.locator('summary').filter({hasText: '클래스별 이미지'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    await expect(panel.getByRole('button', {name: /^TrainOnly/})).toBeVisible();
  };
  await navigate(); await expandClasses();
  const galleryRoute = (className?: string, split?: string) => '/api/dataset/images?' + new URLSearchParams({folder_path: source,
    task: 'classification', limit: '120', offset: '0', ...(className ? {class_name: className} : {}), ...(split ? {split} : {})});
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data',
    '/api/team-data/readiness', '/api/dataset/metadata?limit=100', statisticsEndpoint, '/api/dataset/versions',
    galleryRoute(), galleryRoute('TrainOnly'), galleryRoute('TrainOnly', 'test'), ...originals.map(row => annotationRoute(row.path))];
  const apiBefore: Record<string, any> = {};
  for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
  const statistics = apiBefore[statisticsEndpoint];
  expect(statistics).toMatchObject({total: 2, labelset_id: 'default',
    classes: {TrainOnly: {count: 1, ratio: .5}, TestOnly: {count: 1, ratio: .5}},
    assignments: {train: {count: 1, ratio: .5}, test: {count: 1, ratio: .5}}});
  expect(Object.keys(statistics.classes).sort()).toEqual(['TestOnly', 'TrainOnly']);
  expect(apiBefore[galleryRoute('TrainOnly', 'test')]).toMatchObject({total: 0, items: [], class_split_counts: null});
  const metadata = apiBefore['/api/dataset/metadata?limit=100'].items;
  expect(metadata).toHaveLength(2);
  for (const original of originals) {
    const row = metadata.find((value: any) => value.file_path === original.path);
    expect(row).toBeDefined(); expect(row.content_hash).toBe(original.sha256); expect(row.image_uuid).toBeTruthy();
    expect(row.workflow_state).not.toBe('approved');
  }
  const roots = {source, project: project.project_dir, annotations: active.annotations_dir || project.annotations_dir};
  const before = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)]));
  const baseline = {project, roots, before, apiBefore, originals, metadata};
  const beforeFile = path.join(w.logs, 'statistics-gallery-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [kind, root] of Object.entries(roots)) for (const relative of Object.keys(before[kind])) {
    const snapshot = path.join(w.logs, 'statistics-gallery-before', kind, relative);
    fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
  }
  const writes: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const endpoint = new URL(request.url()).pathname;
    if (endpoint.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
      writes.push({method: request.method(), endpoint, body});
    }
  };
  page.on('request', observe);
  const checkFiles = () => {for (const [kind, root] of Object.entries(roots)) expect(tree(root)).toEqual(before[kind]);};
  const unchanged = async () => {
    const apiAfter: Record<string, any> = {};
    checkFiles(); for (const [endpoint, record] of Object.entries(apiBefore)) {apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(record);}
    checkFiles(); expect(writes).toEqual([]);
    return apiAfter;
  };
  const capture = async (name: string, target: Locator) => {
    await target.scrollIntoViewIfNeeded(); await expect(target).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-statistics-gallery-${name}`);
  };
  const galleryWaiting = (className: string | null, split: string | null) => page.waitForResponse(response => {
    const address = new URL(response.url());
    return response.request().method() === 'GET' && address.pathname === '/api/dataset/images'
      && address.searchParams.get('folder_path') === source && address.searchParams.get('task') === 'classification'
      && address.searchParams.get('class_name') === className && address.searchParams.get('split') === split;
  }, {timeout: 10_000});
  try {
    let failures = 0;
    const fail = async (route: Route) => {
      if (route.request().method() !== 'GET') {await route.fallback(); return;}
      expect(new URL(route.request().url()).pathname).toBe(statisticsEndpoint); failures++;
      await route.fulfill({status: 503, json: {detail: 'Controlled exact statistic GET failure'}});
    };
    await page.route('**' + statisticsEndpoint, fail);
    try {
      const failed = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === statisticsEndpoint);
      await refresh.click(); expect((await failed).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled exact statistic GET failure');
      await expect(panel).toContainText('전체 이미지 —개'); await expect(panel.getByRole('button', {name: /^TrainOnly/})).toHaveCount(0);
      await expect(refresh).toBeEnabled(); checkFiles(); expect(writes).toEqual([]); await capture('503-no-stale-statistic-chip', panel.getByRole('alert'));
    } finally {await page.unroute('**' + statisticsEndpoint, fail);}
    expect(failures).toBe(1); await unchanged();
    const recovered = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === statisticsEndpoint);
    await refresh.click(); const response = await recovered; expect(response.status()).toBe(200); expect(await response.json()).toEqual(statistics);
    await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel).toContainText('전체 이미지 2개'); await expandClasses();
    await unchanged(); await capture('real-200-complete-statistics-recovery', panel);
    controls.push({action: 'F021.class-statistic-gallery-filter', dimension: 'error', exact_GET_503_count: failures,
      stale_statistic_chip_absent: true, real_refresh_200: true, complete_actual_statistic_record_equal: true});
    const classRead = galleryWaiting('TrainOnly', null); await panel.getByRole('button', {name: /^TrainOnly/}).click();
    const classResponse = await classRead, classBody = await classResponse.json(); expect(classResponse.status()).toBe(200);
    expect(classBody.items).toEqual(apiBefore[galleryRoute('TrainOnly')].items); expect(classBody.total).toBe(1);
    await expect(page.getByText('Class: TrainOnly', {exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: 'train-only.png 라벨링에서 열기', exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: 'test-only.png 라벨링에서 열기', exact: true})).toHaveCount(0);
    const emptyRead = galleryWaiting('TrainOnly', 'test'); await page.getByRole('button', {name: '테스트용 (1)', exact: true}).click();
    const emptyResponse = await emptyRead, emptyBody = await emptyResponse.json(); expect(emptyResponse.status()).toBe(200);
    expect(emptyBody).toMatchObject({total: 0, items: [], class_split_counts: null});
    await expect(page.getByText('현재 필터에 맞는 이미지가 없습니다. 라벨 상태나 분할 조건을 바꿔보세요.', {exact: true})).toBeVisible();
    await expect(page.getByText('Class: TrainOnly', {exact: true})).toBeVisible();
    for (const original of originals) await expect(page.getByRole('button', {name: original.name + ' 라벨링에서 열기', exact: true})).toHaveCount(0);
    await expect(panel).toContainText('전체 이미지 2개'); await unchanged();
    await capture('real-empty-class-and-test-intersection', page.getByText('현재 필터에 맞는 이미지가 없습니다. 라벨 상태나 분할 조건을 바꿔보세요.', {exact: true}));
    controls.push({action: 'F021.class-statistic-gallery-filter', dimension: 'empty', actual_class_query: classResponse.url(),
      actual_empty_query: emptyResponse.url(), real_backend_status: 200, real_total: 0, real_items: [],
      observed_class_count: 1, class_filter_retained: true, response_simulated_to_create_empty: false});
    const reopened = galleryWaiting(null, null); await navigate(); const reopenedResponse = await reopened;
    expect(reopenedResponse.status()).toBe(200); expect((await reopenedResponse.json()).items).toEqual(apiBefore[galleryRoute()].items);
    await expect(page.getByText('Class: TrainOnly', {exact: true})).toHaveCount(0); await expandClasses();
    const selectedAgain = galleryWaiting('TrainOnly', null); await panel.getByRole('button', {name: /^TrainOnly/}).click();
    const again = await selectedAgain; expect(again.status()).toBe(200); expect((await again.json()).items).toEqual(classBody.items);
    await expect(page.getByRole('button', {name: 'train-only.png 라벨링에서 열기', exact: true})).toBeVisible();
    await expect(page.getByRole('button', {name: 'test-only.png 라벨링에서 열기', exact: true})).toHaveCount(0);
    const apiAfter = await unchanged(); await capture('actual-reload-reset-and-class-filter-reopen', panel);
    controls.push({action: 'F021.class-statistic-gallery-filter', dimension: 'reopen', actual_reload: true,
      volatile_filter_reset_to_all: true, unfiltered_complete_original_items_equal: true, reopened_class_items_equal: true,
      selected_original: metadata.find((row: any) => row.file_path === originals[0].path), class_query: again.url()});
    // Existing × abandons the active class filter; this is not a new cancel dialog.
    const classBadge = page.getByText('Class: TrainOnly', {exact: true}).locator('..');
    const clearClass = classBadge.getByRole('button', {name: '×', exact: true});
    await expect(clearClass).toHaveCount(1); await expect(clearClass).toBeEnabled();
    const cancelledRead = galleryWaiting(null, null); await clearClass.click();
    const cancelledResponse = await cancelledRead, cancelledBody = await cancelledResponse.json();
    expect(cancelledResponse.status()).toBe(200); expect(cancelledBody.items).toEqual(apiBefore[galleryRoute()].items);
    expect(cancelledBody.total).toBe(2); expect(new URL(cancelledResponse.url()).searchParams.has('class_name')).toBe(false);
    await expect(page.getByText('Class: TrainOnly', {exact: true})).toHaveCount(0);
    for (const original of originals) await expect(page.getByRole('button', {name: original.name + ' 라벨링에서 열기', exact: true})).toBeVisible();
    const cancelledApiAfter = await unchanged(); expect(cancelledApiAfter).toEqual(apiAfter);
    await capture('actual-class-badge-clear-restores-both-originals', panel);
    controls.push({action: 'F021.class-statistic-gallery-filter', dimension: 'cancel', actual_existing_class_badge_clear: true,
      invented_confirm_or_cancel_dialog: false, cancelled_class: 'TrainOnly', actual_unfiltered_query: cancelledResponse.url(),
      real_backend_status: 200, complete_original_items: cancelledBody.items, real_total: 2, post_baseline_mutations: [...writes]});
    const after = {trees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])), apiAfter: cancelledApiAfter, writes};
    const afterFile = path.join(w.logs, 'statistics-gallery-after.json'); fs.writeFileSync(afterFile, JSON.stringify(after, null, 2)); e.addFile(afterFile);
    e.note('statistics_gallery_lifecycle', {requirements: ['S3-07'], cells: controls, baseline, after, all_api_mutations: writes,
      full_source_project_annotations_unfiltered: true, complete_API_records_preserved: true, original_image_hashes_and_UUIDs_preserved: true,
      actual_source_ui: true, sourceElectron, labels_saved_after_baseline: false, human_label_quality_approval: false,
      actual_training_inference_or_GPU: false, physical_device_Windows_frozen_package_or_parent_acceptance: false});
  } finally {page.off('request', observe);}
}

test('class statistic gallery empty recovery and reopen preserve complete owned evidence', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {const response = await request.fetch(renderer.origin + route,
    {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();};
  await exercise(page, workspace, evidence, api, false, renderer.url);
});

test('source Electron class statistic gallery empty recovery and reopen preserve complete owned evidence', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {const response = await fetch(`http://127.0.0.1:${port}${route}`,
    {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned statistics gallery HTTP ${response.status}: ${await response.text()}`); return response.json();}, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

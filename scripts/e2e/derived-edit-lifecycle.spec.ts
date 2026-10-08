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
const endpoint = '/api/data-workbench/derived';
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const files: Record<string, string> = {}; if (!fs.existsSync(root)) return files;
  const visit = (dir: string) => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(dir, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); files[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return files;
}

// Independent expected pixels use only the explicit original and PIL's image
// operators. This helper imports no application edit/derive implementation.
const pixelControl = String.raw`
from pathlib import Path
from PIL import Image,ImageEnhance
import hashlib,json,sys
original,out=map(Path,sys.argv[1:3]);out.mkdir()
source_hash=hashlib.sha256(original.read_bytes()).hexdigest()
result={}
with Image.open(original) as opened:
 image=opened.convert('RGB')
 for name,edited in [('align',image.rotate(-30,resample=Image.Resampling.BICUBIC,expand=True)),('brightness',ImageEnhance.Brightness(image).enhance(.7))]:
  target=out/(name+'.png');edited.save(target,format='PNG')
  result[name]={'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'size':list(edited.size),'decoded_rgb_sha256':hashlib.sha256(edited.tobytes()).hexdigest()}
assert hashlib.sha256(original.read_bytes()).hexdigest()==source_hash
print(json.dumps({'original_sha256':source_hash,'expected':result,'application_transform_imported':False,'model_cpu_gpu_execution':False}))
`;

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'derived-lifecycle-originals'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'); fs.writeFileSync(original, png(64, 3, (x, y) => [x * 3, y * 3, (x + y) % 200]));
  const sourceSha = fileSha(original);
  const pixels = JSON.parse(execFileSync(harness.resolvePython(), ['-c', pixelControl, original, path.join(w.logs, 'independent-derived-pixels')],
    {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 15_000}));
  expect(pixels.original_sha256).toBe(sourceSha); for (const row of Object.values(pixels.expected) as any[]) e.addFile(row.path);
  const project = await api('/api/project/create', {name: 'Owned derived edit lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const saved = await api('/api/annotations/save', {image_id: 'part', image_path: original, image_width: 64, image_height: 64,
    actor: 'derived-lifecycle-fixture', annotations: [{id: 'original-box', type: 'bbox', label: 'Defect', category_id: 1,
      bbox: [8, 10, 40, 44], direction_deg: 20, text: 'A01'}]});
  const basePayload = {image_path: original, expected_revision: saved.metadata.revision,
    expected_sha256: sourceSha, actor: 'derived-lifecycle-fixture'};
  const seed = await api(endpoint, {...basePayload, operation: {kind: 'brightness', factor: 1.1}});
  const historyEndpoint = endpoint + '?image_path=' + encodeURIComponent(original);
  // Real materialization and seed output reads complete before baseline. The
  // full original labels/settings/metadata are protected, not selected fields.
  const teamBefore = await api('/api/team-data');
  expect(teamBefore).toMatchObject({book: null, book_history: [], settings: {revision: 1, editing_enabled: false, review_enabled: false}});
  const readinessBefore = await api('/api/team-data/readiness'), preferencesBefore = await api('/api/project/preferences');
  const metadataBefore = (await api('/api/dataset/metadata?limit=100')).items; expect(metadataBefore).toHaveLength(1);
  const metadata = metadataBefore[0]; expect(metadata).toMatchObject({image_uuid: saved.metadata.image_uuid, file_path: original, content_hash: sourceSha, revision: saved.metadata.revision});
  const teamImageBefore = await api('/api/team-data/images/' + metadata.image_uuid);
  const annotationEndpoint = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  const annotationBefore = await api(annotationEndpoint); expect(annotationBefore.annotations).toHaveLength(1);
  expect(annotationBefore.metadata.workflow_state).not.toBe('approved');
  const versionsBefore = await api('/api/dataset/versions'), splitBefore = await api('/api/dataset/metadata/split');
  const adoptionsBefore = await api('/api/data-workbench/derived-adoptions'); expect(adoptionsBefore.versions).toEqual([]);
  const historyBefore = await api(historyEndpoint); expect(historyBefore.versions).toHaveLength(1); expect(historyBefore.versions[0].id).toBe(seed.id);
  let expectedHistory = historyBefore.versions;
  const roots = {source, annotations: active.annotations_dir || project.annotations_dir,
    reports: path.join(project.project_dir, 'reports'), splits: path.join(project.dataset_dir, 'splits')};
  const workbench = path.join(project.dataset_dir, 'data_workbench');
  const protectedTrees = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)]));
  let expectedWorkbench = tree(workbench);
  const baseline = {protectedTrees, workbench: expectedWorkbench, teamBefore, readinessBefore, preferencesBefore,
    metadataBefore, teamImageBefore, annotationBefore, versionsBefore, splitBefore, adoptionsBefore, historyBefore, metadata};
  const beforeFile = path.join(w.logs, 'derived-lifecycle-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(baseline, null, 2)); e.addFile(beforeFile);
  for (const [name, root] of Object.entries({...roots, workbench})) {
    for (const relative of Object.keys(tree(root))) {
      const copy = path.join(w.logs, 'derived-lifecycle-protected-before', name, relative);
      fs.mkdirSync(path.dirname(copy), {recursive: true}); fs.copyFileSync(path.join(root, relative), copy); e.addFile(copy);
    }
  }
  const writes: any[] = [], forbidden: any[] = [], controls: any[] = [], outputs: any[] = [];
  const knownOperations = [{kind: 'align', degrees: 30}, {kind: 'brightness', factor: .7}];
  const observe = (request: Request) => {
    const address = new URL(request.url()); if (!address.pathname.startsWith('/api/') || ['GET', 'HEAD', 'OPTIONS'].includes(request.method())) return;
    let body: any; try {body = request.postDataJSON();} catch {body = request.postData();}
    const row = {method: request.method(), endpoint: address.pathname, body}; writes.push(row);
    if (request.method() !== 'POST' || address.pathname !== endpoint || body?.image_path !== original ||
        body?.expected_sha256 !== sourceSha || body?.expected_revision !== metadata.revision ||
        body?.actor !== basePayload.actor || body?.parent_id !== undefined ||
        !knownOperations.some(operation => JSON.stringify(operation) === JSON.stringify(body?.operation))) forbidden.push(row);
  };
  page.on('request', observe);
  const panel = page.getByRole('region', {name: '파생 이미지 편집', exact: true});
  const choice = panel.getByLabel('파생 편집 기준 버전', {exact: true});
  const angle = panel.getByLabel('파생 이미지 정렬 각도', {exact: true}), brightness = panel.getByLabel('파생 이미지 밝기 배율', {exact: true});
  const alignSave = panel.getByRole('button', {name: '정렬 새 버전 저장', exact: true});
  const brightSave = panel.getByRole('button', {name: '밝기 새 버전 저장', exact: true});
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const mount = async () => {
    await stages.getByRole('button').nth(1).click(); await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
    const focus = page.getByRole('button', {name: '집중 편집', exact: true}); if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
    const summary = page.locator('summary').filter({hasText: '원본 보존 이미지 편집 · 파생 버전'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    await expect(choice.locator('option')).toHaveCount(expectedHistory.length + 1);
  };
  const reload = async () => {
    if (url) await page.goto(url); else await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await mount();
  };
  const enterActor = async () => {
    await page.getByRole('button', {name: '이미지 정보·검토', exact: true}).click();
    await page.getByRole('region', {name: '이미지 정보와 검토 기록'}).getByLabel('작업자·검토자 이름', {exact: true}).fill(basePayload.actor);
    await page.getByRole('button', {name: '이미지 정보·검토', exact: true}).click();
  };
  const unchanged = async () => {
    for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(protectedTrees[name]);
    expect(tree(workbench)).toEqual(expectedWorkbench); expect((await api('/api/dataset/metadata?limit=100')).items).toEqual(metadataBefore);
    expect(await api(annotationEndpoint)).toEqual(annotationBefore); expect(await api('/api/team-data')).toEqual(teamBefore);
    expect(await api('/api/team-data/readiness')).toEqual(readinessBefore); expect(await api('/api/team-data/images/' + metadata.image_uuid)).toEqual(teamImageBefore);
    expect(await api('/api/project/preferences')).toEqual(preferencesBefore); expect(await api('/api/dataset/versions')).toEqual(versionsBefore);
    expect(await api('/api/dataset/metadata/split')).toEqual(splitBefore); expect(await api('/api/data-workbench/derived-adoptions')).toEqual(adoptionsBefore);
    expect((await api(historyEndpoint)).versions).toEqual(expectedHistory); expect((await api('/api/project/current')).source_dataset_dir).toBe(source);
    expect(forbidden).toEqual([]);
  };
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-derived-lifecycle-${name}`);
  };
  try {
    await reload(); await enterActor(); await choice.selectOption('');
    await angle.fill('181'); await expect(alignSave).toBeDisabled(); await brightness.fill('4.1'); await expect(brightSave).toBeDisabled();
    expect(writes).toEqual([]); await unchanged(); await capture('actual-out-of-range-inputs-no-post', alignSave);
    for (const [action, field, value] of [['align-version-save', 'clockwise_degrees', 181], ['brightness-version-save', 'factor', 4.1]]) {
      controls.push({action: 'U014.' + action, dimension: 'invalid', actual_disabled_save_control: true, invalid_field: field, invalid_value: value, derived_POSTs: 0});
    }
    await angle.fill('30'); await brightness.fill('0.7'); await expect(alignSave).toBeEnabled(); await expect(brightSave).toBeEnabled();
    await capture('valid-unsent-controls-before-stage-abandonment', alignSave);
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0); await unchanged(); expect(writes).toEqual([]);
    await mount(); await expect(angle).toHaveValue('0'); await expect(brightness).toHaveValue('1'); await expect(choice).toHaveValue('');
    await unchanged(); await capture('stage-abandonment-discards-valid-unsent-controls', alignSave);
    for (const action of ['align-version-save', 'brightness-version-save']) {
      controls.push({action: 'U014.' + action, dimension: 'cancel', actual_stage_exit: true,
        cancellation_kind: 'abandon valid unsent parameters; no explicit cancel dialog', derived_POSTs: 0});
    }
    await enterActor();
    for (const [kind, input, button, value] of [['align', angle, alignSave, '30'], ['brightness', brightness, brightSave, '0.7']] as const) {
      await choice.selectOption(''); await input.fill(value); await expect(button).toBeEnabled();
      const operation = knownOperations.find(row => row.kind === kind)!; let controlled = 0;
      const fail = async (route: Route) => {
        if (route.request().method() !== 'POST') {await route.fallback(); return;}
        expect(new URL(route.request().url()).pathname).toBe(endpoint);
        expect(route.request().postDataJSON()).toEqual({...basePayload, operation}); controlled++;
        await route.fulfill({status: 503, json: {detail: 'Controlled ' + kind + ' derived POST failure'}});
      };
      await page.route('**' + endpoint, fail);
      try {
        const wait = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === endpoint);
        await button.click(); expect((await wait).status()).toBe(503); await expect(panel.getByRole('alert')).toContainText('Controlled ' + kind + ' derived POST failure');
        await expect(button).toBeEnabled(); await expect(choice).toHaveValue(''); await unchanged();
        await capture(kind + '-503-no-new-version', panel.getByRole('alert'));
      } finally {await page.unroute('**' + endpoint, fail);}
      expect(controlled).toBe(1);
      const wait = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === endpoint);
      await button.click(); const response = await wait; expect(response.status()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({...basePayload, operation}); const created = await response.json();
      expect(created).toMatchObject({parent_id: null, source_path: original, source_sha256: sourceSha,
        source_relative_path: 'part.png', actor: basePayload.actor, operation, size: pixels.expected[kind].size,
        derived_sha256: pixels.expected[kind].sha256, omitted_annotation_ids: []});
      expect(fs.readFileSync(created.file_path)).toEqual(fs.readFileSync(pixels.expected[kind].path));
      const originalBox = annotationBefore.annotations[0]; expect(created.annotations).toHaveLength(1);
      if (kind === 'brightness') expect(created.annotations).toEqual(annotationBefore.annotations);
      else {
        const [width, height] = created.size, radians = Math.PI / 6, co = Math.cos(radians), si = Math.sin(radians);
        const points = [[8, 10], [40, 10], [40, 44], [8, 44]].map(([x, y]) => [co * (x - 32) - si * (y - 32) + width / 2, si * (x - 32) + co * (y - 32) + height / 2]);
        expect(created.annotations[0]).toMatchObject({id: originalBox.id, type: 'polygon', label: originalBox.label,
          category_id: originalBox.category_id, direction_deg: 50, text: 'A01'});
        for (const [index, point] of points.entries()) for (const [axis, value] of point.entries()) expect(created.annotations[0].polygon[index][axis]).toBeCloseTo(value, 12);
        expect(created.annotations[0].points).toEqual(created.annotations[0].polygon);
        const bounds = [Math.min(...points.map(p => p[0])), Math.min(...points.map(p => p[1])), Math.max(...points.map(p => p[0])), Math.max(...points.map(p => p[1]))];
        for (const [index, bound] of bounds.entries()) expect(created.annotations[0].bbox[index]).toBeCloseTo(bound, 12);
      }
      const outputTree = tree(created.dataset_path); expect(Object.keys(outputTree).sort()).toEqual(['annotations.json', 'images/derived.json', 'images/derived.png', 'masks/derived.png', 'version.json']);
      for (const [name, digest] of Object.entries(created.annotation_files_sha256)) expect(outputTree[name]).toBe(digest);
      const relativeRoot = path.relative(workbench, created.dataset_path).split(path.sep).join('/');
      const extension = Object.fromEntries(Object.entries(outputTree).map(([name, digest]) => [relativeRoot + '/' + name, digest]));
      expectedWorkbench = {...expectedWorkbench, ...extension}; expect(tree(workbench)).toEqual(expectedWorkbench);
      const reopened = await api(endpoint + '/' + created.id); expect(reopened).toEqual({...created, review_revision: 0, review: null});
      expectedHistory = [...expectedHistory, reopened]; await expect(choice).toHaveValue(created.id);
      await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel.getByRole('status')).toContainText('새 버전으로 저장했습니다'); await unchanged();
      await capture(kind + '-actual-200-exact-output', panel.getByRole('img', {name: '편집 원본과 변환 라벨 미리보기', exact: true}));
      controls.push({action: 'U014.' + (kind === 'align' ? 'align-version-save' : 'brightness-version-save'), dimension: 'error',
        exact_POST_503_count: controlled, no_output_on_failure: true, actual_backend_retry: 200, original_labels_and_settings_unchanged: true, created_version_id: created.id});
      outputs.push({kind, row: reopened, full_output_tree: outputTree, complete_expected_png_bytes_equal: true, expected_pixels: pixels.expected[kind]});
      for (const name of Object.keys(outputTree)) e.addFile(path.join(created.dataset_path, name));
    }
    await reload(); await expect(choice).toHaveValue(''); await expect(angle).toHaveValue('0'); await expect(brightness).toHaveValue('1');
    await unchanged();
    for (const output of outputs) {
      const imageEndpoint = endpoint + '/' + output.row.id + '/image';
      // Routing disables the browser image cache. The scoped route falls
      // through to the real backend; it neither supplies pixels nor fulfills.
      const actualImage = async (route: Route) => {
        expect(route.request().method()).toBe('GET'); expect(new URL(route.request().url()).pathname).toBe(imageEndpoint);
        await route.fallback();
      };
      await page.route('**' + imageEndpoint, actualImage);
      try {
        const wait = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === imageEndpoint);
        await choice.selectOption(output.row.id); const response = await wait; expect(response.status()).toBe(200);
        expect(await response.body()).toEqual(fs.readFileSync(output.row.file_path)); expect(sha(await response.body())).toBe(output.row.derived_sha256);
      } finally {await page.unroute('**' + imageEndpoint, actualImage);}
      const preview = panel.getByRole('img', {name: '편집 원본과 변환 라벨 미리보기', exact: true});
      await expect(preview).toHaveAttribute('viewBox', '0 0 ' + output.row.size.join(' '));
      expect(new URL((await preview.locator('image').first().getAttribute('href'))!, page.url()).pathname).toBe(imageEndpoint);
      const labelPoints = await preview.locator('polygon').first().getAttribute('points');
      const expectedPoints = output.kind === 'align' ? output.row.annotations[0].polygon : [[8, 10], [40, 10], [40, 44], [8, 44]];
      expect(labelPoints).toBe(expectedPoints.map((point: number[]) => point.join(',')).join(' '));
      const detail = panel.locator('details').filter({hasText: '버전 출처와 보존 해시'});
      expect(JSON.parse(await detail.locator('pre').innerText())).toMatchObject({version: output.row.id, parent: null,
        actor: basePayload.actor, operation: output.row.operation, original_sha256: sourceSha, derived_sha256: output.row.derived_sha256, dataset_path: output.row.dataset_path});
      await expect(panel).toContainText('검수 대기'); await unchanged(); await capture(output.kind + '-actual-reload-exact-version-reopened', preview);
      controls.push({action: 'U014.' + (output.kind === 'align' ? 'align-version-save' : 'brightness-version-save'), dimension: 'reopen',
        actual_reload_and_exact_history_selection: true, exact_version_id: output.row.id, raw_GET_status: 200,
        complete_png_bytes_equal: true, original_sha256: sourceSha, derived_sha256: output.row.derived_sha256,
        original_image_uuid: metadata.image_uuid, original_revision: metadata.revision, reviewed_or_adopted: false});
    }
    expect(controls).toHaveLength(8); expect(writes).toHaveLength(4); expect(forbidden).toEqual([]);
    for (const operation of knownOperations) expect(writes.filter(row => JSON.stringify(row.body.operation) === JSON.stringify(operation))).toHaveLength(2);
    await unchanged(); const afterFile = path.join(w.logs, 'derived-lifecycle-protected-after.json'); fs.writeFileSync(afterFile,
      JSON.stringify({protectedTrees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])),
        complete_workbench_tree: tree(workbench), expectedHistory, writes, outputs}, null, 2)); e.addFile(afterFile);
    e.note('derived_edit_lifecycle', {cells: controls, baseline, intended_new_outputs: outputs, all_api_mutations: writes,
      full_unfiltered_protected_originals: true, new_workbench_files_exactly_intended_outputs: true,
      sourceElectron, actual_source_ui: true, invalid_is_actual_UI_save_disabled_no_backend_dispatch: true,
      explicit_cancel_dialog_exists: false, cancellation_is_actual_stage_abandonment: true,
      label_save_or_review_or_adoption_after_baseline: false, original_source_switched: false,
      controlled_original_annotation_fixture_not_human_truth: true, model_training_inference_or_gpu: false,
      physical_device_frozen_package_windows_human_quality_acceptance: false});
  } finally {page.off('request', observe);}
}

test('align and brightness derived versions preserve originals through invalid abandon retry and exact reopen', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port); const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();
  }; await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native derived align and brightness lifecycle binds exact saved pixels labels and original custody', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned derived lifecycle API ${response.status}: ${await response.text()}`); return response.json();
  }, {port: backend.port, route, body, method}); await exercise(page, workspace, evidence, api, true);
});

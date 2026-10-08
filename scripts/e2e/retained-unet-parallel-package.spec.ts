import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Response, APIRequestContext} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {confirmFlowSave} from './fixtures/flowChange';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
type Tree = {directories: string[]; files: Record<string, {bytes: number; sha256: string}>};
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
test.use({actionTimeout: 10_000});

function tree(root: string): Tree {
  expect(fs.lstatSync(root).isSymbolicLink()).toBe(false);
  const directories: string[] = [], files: Tree['files'] = {};
  const visit = (folder: string) => {
    for (const item of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, item.name), relative = path.relative(root, file).split(path.sep).join('/');
      expect(item.isSymbolicLink()).toBe(false);
      if (item.isDirectory()) {directories.push(relative); visit(file);}
      else {expect(item.isFile()).toBe(true); const raw = fs.readFileSync(file); files[relative] = {bytes: raw.length, sha256: sha(raw)};}
    }
  };
  visit(root); return {directories: directories.sort(), files};
}

async function within<T>(promise: Promise<T>, deadline: number): Promise<T> {
  const remaining = deadline - Date.now(); expect(remaining).toBeGreaterThan(0);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {return await Promise.race([promise, new Promise<T>((_, reject) => {timer = setTimeout(() => reject(Error('Original request deadline expired')), remaining);})]);}
  finally {if (timer) clearTimeout(timer);}
}

async function prepareValidatedRetainedRevision(api: Api, setup: Api, original: any, project: any) {
  // The legacy summary import does not create an active validated index.
  // Use the original durable job and explicit acceptance before the baseline;
  // its manifest digest is distinct from the model's legacy v1 fingerprint.
  expect(original.task).toBe('segmentation');
  expect(project.source_dataset_dir).toBe(original.source_dataset_path);
  const started = Date.now(), deadline = started + 10_000;
  const bounded = async <T>(operation: () => Promise<T>): Promise<T> => {
    expect(Date.now()).toBeLessThanOrEqual(deadline);
    const value = await within(operation(), deadline);
    expect(Date.now()).toBeLessThanOrEqual(deadline); return value;
  };
  const before = await bounded(() => api('/api/dataset/revisions'));
  expect(before).toEqual({active_revision: null, revisions: []});
  const submitted = await bounded(() => setup('/api/dataset/imports',
    {task: 'segmentation', verify: true, invalid_policy: 'reject', follow_links: false}));
  expect(typeof submitted.job_id).toBe('string'); expect(submitted.job_id.length).toBeGreaterThan(0);
  expect(submitted.source.root).toBe(original.source_dataset_path);
  const observed: any[] = []; let completed: any;
  while (Date.now() < deadline) {
    completed = await bounded(() => api('/api/dataset/imports/' + submitted.job_id));
    expect(completed.job_id).toBe(submitted.job_id); expect(completed.source.root).toBe(original.source_dataset_path);
    observed.push(completed);
    if (['completed', 'failed', 'aborted', 'interrupted', 'cancelled'].includes(completed.state)) break;
    await bounded(() => new Promise<void>(resolve => setTimeout(resolve, 50)));
  }
  expect(completed.state).toBe('completed');
  const expectedImages = Object.entries(original.source_tree.files)
    .filter(([relative]) => relative.startsWith('images/') && relative.endsWith('.png'))
    .map(([relative_path, row]: [string, any]) => ({relative_path, sha256: row.sha256}))
    .sort((a, b) => a.relative_path.localeCompare(b.relative_path));
  expect(expectedImages.length).toBeGreaterThan(0);
  const revision = completed.result.revision;
  expect(revision.revision_id).toMatch(/^[a-f0-9]{32}$/); expect(revision.manifest_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(revision).toMatchObject({state: 'prepared', image_count: expectedImages.length, valid_count: expectedImages.length,
    error_count: 0, invalid_policy: 'reject', skipped_links: 0, unreadable_folders: 0, reused_entries: 0, verified_all: true});
  const accepted = await bounded(() => setup('/api/dataset/imports/' + submitted.job_id + '/accept',
    {revision_id: revision.revision_id, expected_active: null}));
  expect(accepted).toEqual({active_revision: revision.revision_id, job_id: submitted.job_id});
  const active = await bounded(() => api('/api/dataset/revisions'));
  expect(active.active_revision).toBe(revision.revision_id); expect(active.revisions).toHaveLength(1);
  expect(active.revisions[0]).toMatchObject({revision_id: revision.revision_id, source_root: original.source_dataset_path,
    project_root: project.project_dir, task: 'segmentation', publication_key: submitted.job_id, active: true});
  const library = await bounded(() => api('/api/dataset/library/images?state=valid&limit=120'));
  expect(library.revision_id).toBe(revision.revision_id); expect(library.source_root).toBe(original.source_dataset_path);
  expect(library.active).toBe(true); expect(library.next_cursor).toBe(null);
  expect(library.items.map((row: any) => ({relative_path: row.relative_path, sha256: row.sha256}))
    .sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path))).toEqual(expectedImages);
  for (const row of library.items) expect(row.image_uuid).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/);
  return {started, deadline, finished: Date.now(), before, submitted, observed, completed, revision, accepted, active, library};
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  test.setTimeout(420_000);
  const bindingFile = process.env.MV_E2E_RETAINED_UNET_BINDING;
  const bindingHash = process.env.MV_E2E_RETAINED_UNET_BINDING_SHA256;
  expect(bindingFile, 'Root must provide an explicit frozen retained-model binding').toBeTruthy();
  expect(bindingHash).toMatch(/^[a-f0-9]{64}$/);
  const bindingRaw = fs.readFileSync(bindingFile!); expect(sha(bindingRaw)).toBe(bindingHash);
  const original = JSON.parse(bindingRaw.toString());
  const helper = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/retained_unet_flow.py');
  const invoke = (action: string, args: string[] = [], timeout = 20_000) => JSON.parse(execFileSync(harness.resolvePython(),
    ['-B', helper, action, '--binding', bindingFile!, '--binding-sha256', bindingHash!, ...args],
    {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout, maxBuffer: 64 * 1024 * 1024,
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1'}}));
  const originalCheck = invoke('check');
  const source = original.source_dataset_path;
  const setupWrites: any[] = [];
  const setup = async (route: string, body: unknown, method = 'POST') => {
    const started = Date.now(), response = await api(route, body, method);
    setupWrites.push({route, method, body, status: 200, response, started, finished: Date.now()});
    return response;
  };
  const created = await setup('/api/project/create', {name: 'Retained original UNet two-ROI functional flow', task: 'segmentation'});
  const project = await setup('/api/project/update', {source_dataset_dir: source}, 'PUT');
  expect(project).toMatchObject({id: created.id, task: 'segmentation', source_dataset_dir: source});
  await setup('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const validatedRevision = await prepareValidatedRetainedRevision(api, setup, original, project);
  expect(invoke('check')).toEqual(originalCheck);
  const prepared = invoke('prepare', ['--workspace', workspace.root, '--project', JSON.stringify(project)]);
  const query = new URLSearchParams({source_dataset_path: source});
  const catalogRoute = '/api/flowchart/models/catalog?' + query;
  const catalog = await api(catalogRoute);
  expect(catalog.models).toHaveLength(1);
  expect(catalog.models[0]).toMatchObject({job_id: original.job_id, task: 'segmentation', source_dataset_path: source});
  const verified = await setup('/api/flowchart/models/verify', {source_dataset_path: source,
    models: [{job_id: original.job_id, task: 'segmentation'}]});
  const pipelineRoute = '/api/flowchart/pipeline?' + new URLSearchParams({recipe_task: 'segmentation', source_dataset_path: source,
    change_reason: 'Controlled initial graph; original completed model/provenance, no new training'});
  const initial = await setup(pipelineRoute, prepared.pipeline);
  expect(initial.status).toBe('saved');
  const initialGraphRecord = await api('/api/flowchart/pipelines/' + initial.version_id);
  const draftGraph = JSON.parse(JSON.stringify(initialGraphRecord.pipeline || initialGraphRecord));
  const draftContext = {project_id: project.id, source_dataset_path: source, labelset_id: 'default'};
  const drafts: Array<{started: number; finished: number; request: any; response: any}> = [];
  let finalVersion: string | null = null, finalGraphSha: string | null = null;
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const openFlow = async () => {
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await stages.getByRole('button').nth(4).click();
    await expect(page.getByRole('heading', {name: '검사 플로우 편집기', exact: true})).toBeVisible();
    await expect(page.locator('[data-flow-node-id="inspect_left"]')).toBeVisible();
    await expect(page.locator('[data-flow-node-id="inspect_right"]')).toBeVisible();
    await expect(page.getByRole('button', {name: '플로우 저장', exact: true})).toBeEnabled();
  };
  // Complete all original renderer GETs before and after every protected API
  // reread. A failed/unknown read is retained, never filtered from custody.
  const pending = new Set<Request>(), readTimes = new Map<Request, number>(), finishedReads: any[] = [], readFailures: string[] = [];
  const startRead = (request: Request) => {if (request.method() === 'GET' && new URL(request.url()).pathname.startsWith('/api/')) {
    pending.add(request); readTimes.set(request, Date.now());
  }};
  const finishRead = async (request: Request) => {
    if (!pending.has(request)) return;
    try {const response = await within(request.response(), readTimes.get(request)! + 10_000); expect(response).not.toBeNull();
      await within(response!.finished(), readTimes.get(request)! + 10_000);
      const raw = await within(response!.body(), readTimes.get(request)! + 10_000);
      const contentType = response!.headers()['content-type'] || '';
      const endpoint = new URL(request.url()).pathname;
      let body: any;
      if (contentType.startsWith('application/json')) body = JSON.parse(raw.toString('utf8'));
      else {
        expect(endpoint.startsWith('/api/dataset/thumbnail/') || endpoint.startsWith('/api/dataset/raw/')).toBe(true);
        expect(['image/jpeg', 'image/png']).toContain(contentType.split(';')[0]);
        expect(raw.length).toBeGreaterThan(0); body = {bytes: raw.length, sha256: sha(raw)};
      }
      if (endpoint === '/api/flowchart/draft' && response!.status() === 404) {
        if (body.detail === 'No draft for this project, source and labelset') expect(drafts).toHaveLength(0);
        else {
          expect(body).toEqual({detail: 'A newer saved flow supersedes this draft'});
          expect(finalVersion).not.toBeNull(); expect(finalGraphSha).not.toBeNull();
          expect(drafts.at(-1)?.response.draft_sha256).not.toBe(finalGraphSha);
        }
      } else if (endpoint === '/api/flowchart/draft') {
        expect(response!.status()).toBe(200); expect(body.context).toEqual(draftContext);
        expect(body.version).toBe(1); expect(body.base_version_id).toBe(initial.version_id);
        expect(body.draft_sha256).toMatch(/^[a-f0-9]{64}$/);
      } else expect(response!.status()).toBe(200);
      finishedReads.push({url: request.url(), started: readTimes.get(request), finished: Date.now(), status: response!.status(), content_type: contentType,
        raw_bytes: raw.length, raw_sha256: sha(raw), body});
    } catch (error) {readFailures.push(String(error));} finally {pending.delete(request);}
  };
  page.on('request', startRead); page.on('requestfinished', request => {void finishRead(request);});
  page.on('requestfailed', request => {if (pending.delete(request)) readFailures.push('Failed original read ' + request.url());});
  const settle = async () => {await expect.poll(() => pending.size, {timeout: 10_000}).toBe(0); expect(readFailures).toEqual([]);};
  if (url) await page.goto(url); else await page.reload();
  await openFlow(); await settle();
  const scoped = new URLSearchParams({source_dataset_path: source, task: 'segmentation'});
  const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
    '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/metadata/split', '/api/dataset/versions', '/api/dataset/revisions',
    '/api/data-workbench/review-evaluations', '/api/data-workbench/review-queues', '/api/training/jobs', catalogRoute,
    '/api/evaluation/history?' + scoped, '/api/model-deployments/active?' + scoped, '/api/model-deployments/history?' + scoped,
    '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', '/api/runtime-services/capture-groups', '/api/runtime-services',
    '/api/product-delivery/packages', '/api/product-delivery/runtime-packs', '/api/flow-evaluations/approvals/active'];
  const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
  expect(apiBefore['/api/training/jobs'].jobs || apiBefore['/api/training/jobs']).toEqual([]);
  expect(apiBefore['/api/evaluation/history?' + scoped]).toEqual({items: [], total: 0});
  expect(apiBefore['/api/runtime-services']).toMatchObject({runtime: {status: 'stopped'}, active: null, history: [],
    native_install: {prepared: false, registered: false, enabled: false, running: false, verified: false}});
  expect(apiBefore['/api/fleet/targets']).toEqual({targets: []}); expect(apiBefore['/api/fleet/rollouts']).toEqual({rollouts: []});
  const changesBefore = await api('/api/flowchart/changes'); expect(changesBefore.integrity).toEqual({intact: true, rows: 1});
  const resourcesBefore = await api('/api/flowchart/execution-resources?device=cpu');
  expect(resourcesBefore.engine_device_capacity).toBe(1); expect(resourcesBefore.active_executions).toBe(0);
  const roots = {project: project.project_dir, annotations: project.annotations_dir, copiedModel: prepared.model_dir,
    originalSource: source, originalModel: original.original_model_dir, harnessDataset: workspace.dataset};
  await settle();
  const before: Record<string, Tree> = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(String(root))]));
  const baselineFile = path.join(workspace.logs, 'retained-unet-before.json');
  fs.writeFileSync(baselineFile, JSON.stringify({originalCheck, original, project, prepared, catalog, verified,
    initial, setupWrites, roots, before, apiBefore, changesBefore, resourcesBefore}, null, 2)); evidence.addFile(baselineFile);
  // Preserve full owned before bytes; large retained originals remain their
  // exact external hashed inventory, independently rechecked by check().
  for (const key of ['project', 'harnessDataset']) for (const relative of Object.keys(before[key].files)) {
    const copy = path.join(workspace.logs, 'retained-unet-before', key, relative); fs.mkdirSync(path.dirname(copy), {recursive: true});
    fs.copyFileSync(path.join(String(roots[key as keyof typeof roots]), relative), copy); evidence.addFile(copy);
  }
  const writes: Array<{method: string; url: string; body: any; request: Request}> = [];
  const observeWrite = (request: Request) => {if (new URL(request.url()).pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
    writes.push({method: request.method(), url: request.url(), body: request.postDataJSON(), request});
  }};
  page.on('request', observeWrite);
  // Every genuine semantic update is independently persisted by the existing
  // 650ms autosave, then read back, before the next edit. No timer is disabled.
  const settleDraft = async (action: () => Promise<unknown>, edit: () => void) => {
    edit(); const started = Date.now();
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/draft'
      && response.request().method() === 'PUT', {timeout: 10_000});
    await action(); const response = await within(responsePromise, started + 10_000);
    expect(response.status()).toBe(200); await within(response.finished(), started + 10_000);
    const record = await within(response.json(), started + 10_000), request = response.request().postDataJSON();
    expect(request).toEqual({pipeline: draftGraph, context: draftContext, base_version_id: initial.version_id});
    expect(record).toMatchObject({version: 1, context: draftContext, base_version_id: initial.version_id, active_version_id: null});
    expect(record.pipeline).toEqual(draftGraph); expect(record.draft_sha256).toMatch(/^[a-f0-9]{64}$/);
    drafts.push({started, finished: Date.now(), request, response: record});
    await expect(page.getByRole('status').filter({hasText: /^초안 저장됨 · 실행본 활성화 전$/})).toBeVisible();
    await settle();
  };
  const roiDraft = (identity: string, box: number[]) => {draftGraph.nodes.find((row: any) => row.id === identity).data.params.roi_bbox = box;};
  const selectNode = async (identity: string) => {await page.getByRole('tab', {name: '편집', exact: true}).click();
    await page.locator('[data-flow-node-id="' + identity + '"]').getByRole('group').click();};
  await selectNode('roi_left'); await settleDraft(async () => {
    await page.getByLabel('너비 (px)', {exact: true}).fill('32'); await page.getByLabel('너비 (px)', {exact: true}).press('Enter');
  }, () => roiDraft('roi_left', [0, 0, 32, 64]));
  await selectNode('roi_right'); await settleDraft(async () => {
    await page.getByLabel('X 시작 (px)', {exact: true}).fill('32'); await page.getByLabel('X 시작 (px)', {exact: true}).press('Enter');
  }, () => roiDraft('roi_right', [32, 0, 96, 64]));
  await settleDraft(async () => {
    await page.getByLabel('너비 (px)', {exact: true}).fill('32'); await page.getByLabel('너비 (px)', {exact: true}).press('Enter');
  }, () => roiDraft('roi_right', [32, 0, 64, 64]));
  await page.locator('summary').filter({hasText: '빠른 노드 추가·연결·실행 자원'}).click();
  await page.locator('summary').filter({hasText: '병렬 실행 · 작업'}).click();
  await settleDraft(() => page.getByLabel('독립 노드 작업 수', {exact: true}).fill('2'), () => {draftGraph.execution_config.max_workers = 2;});
  await settleDraft(() => page.getByLabel('요청 장치 슬롯', {exact: true}).fill('2'), () => {draftGraph.execution_config.device_slots = 2;});
  expect(drafts).toHaveLength(5);
  const resourceResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/execution-resources' && response.request().method() === 'PUT');
  await page.getByRole('button', {name: 'CPU 엔진에 슬롯 적용', exact: true}).click();
  const configured = await resourceResponse; expect(configured.status()).toBe(200);
  const resourcesParallel = await configured.json(); expect(resourcesParallel).toMatchObject({engine_device_capacity: 2, active_executions: 0});
  const saveResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST');
  await page.getByRole('button', {name: '플로우 저장', exact: true}).click();
  await confirmFlowSave(page, 'Original retained UNet two source-coordinate ROI branches; functional parallel/aggregate control only');
  const savedResponse = await saveResponse; expect(savedResponse.status()).toBe(200); const saved = await savedResponse.json();
  expect(saved.status).toBe('saved'); expect(saved.version_id).not.toBe(initial.version_id);
  const pipeline = await api('/api/flowchart/pipelines/' + saved.version_id);
  const exactPipeline = pipeline.pipeline || pipeline;
  const savedList = await api('/api/flowchart/pipelines?' + query);
  expect(savedList.total).toBe(2); expect(savedList.pipelines.find((row: any) => row.is_active).version_id).toBe(saved.version_id);
  const graphSha = savedList.pipelines.find((row: any) => row.version_id === saved.version_id).pipeline_hash;
  expect(exactPipeline).toEqual(draftGraph); expect(drafts.at(-1)!.response.draft_sha256).toBe(graphSha);
  finalVersion = saved.version_id; finalGraphSha = graphSha;
  const savedGraphFile = path.join(workspace.logs, 'retained-unet-saved-pipeline.json'); fs.writeFileSync(savedGraphFile, JSON.stringify(exactPipeline)); evidence.addFile(savedGraphFile);

  const results: any[] = [], runs: any[] = [];
  const run = async (item: any, capacity: number) => {
    await page.getByRole('button', {name: /이미지 변경/}).click();
    const picker = page.getByRole('dialog', {name: '검사 대상 이미지 선택', exact: true});
    await picker.getByLabel('이미지 검색', {exact: true}).fill(path.basename(item.relative_path));
    const originalImage = picker.getByRole('listitem', {name: item.relative_path, exact: true});
    await expect(originalImage).toHaveCount(1); await originalImage.click();
    await expect(originalImage).toHaveAttribute('aria-pressed', 'true');
    await picker.getByRole('button', {name: '선택 확정', exact: true}).click();
    await page.getByRole('tab', {name: '테스트', exact: true}).click();
    await page.getByLabel('플로우 실행 위치', {exact: true}).selectOption('local_cpu');
    const started = Date.now(), responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run'
      && response.request().method() === 'POST', {timeout: 60_000});
    await page.getByRole('button', {name: '선택 이미지 검사', exact: true}).click();
    const response: Response = await within(responsePromise, started + 60_000);
    expect(response.status()).toBe(200); await within(response.finished(), started + 60_000);
    const result = await within(response.json(), started + 60_000), requestBody = response.request().postDataJSON();
    expect(requestBody).toMatchObject({project_id: project.id, device: 'cpu', execution_target: 'local',
      image_path: path.join(source, item.relative_path), pipeline: exactPipeline});
    expect(requestBody.stop_node_id).toBeUndefined();
    expect(result).toMatchObject({status: 'success', image_path: requestBody.image_path, image_id: requestBody.image_id,
      graph_sha256: graphSha, inspected_image_size: [64, 64], routed_output_node_id: 'node_output',
      execution_resources: {requested_workers: 2, requested_device_slots: 2, effective_device_slots: capacity,
        device: 'cpu', engine_device_capacity: capacity}});
    expect(result.crops.map((row: any) => [row.source_node_id, row.bbox])).toEqual([
      ['inspect_left', [0, 0, 32, 64]], ['inspect_right', [32, 0, 64, 64]]]);
    expect(result.execution_steps.filter((row: any) => ['inspect_left', 'inspect_right'].includes(row.node_id))
      .map((row: any) => [row.input_count, row.output_count])).toEqual([[1, 1], [1, 1]]);
    expect(result.execution_steps.find((row: any) => row.node_id === 'aggregate')).toMatchObject({input_count: 2, output_count: 2, branch_verdict: result.final_verdict});
    expect(result.final_verdict).toBe(result.crops.some((row: any) => row.verdict === 'NG') ? 'NG' : 'OK');
    for (const crop of result.crops) expect(crop.segmentation_classes.map((row: any) => [row.class_id, row.class_name, row.mask.shape, row.probability.shape]))
      .toEqual([[0, 'background', [64, 32], [64, 32]], [1, 'defect', [64, 32], [64, 32]]]);
    const output = path.join(workspace.logs, 'retained-unet-app-' + capacity + '-' + runs.length + '.json');
    fs.writeFileSync(output, JSON.stringify(result)); evidence.addFile(output);
    runs.push({started, finished: Date.now(), request: requestBody, response: result, output, capacity, original: item});
    await expect(page.getByAltText('실제 검사 결과', {exact: true})).toHaveAttribute('src', result.annotated_image);
    await evidence.screenshot(page, `${sourceElectron ? 'native' : 'browser'}-retained-unet-${capacity}-${path.basename(item.relative_path)}`);
    return result;
  };
  for (const item of original.cohort) results.push(await run(item, 2));
  await page.reload(); await openFlow();
  expect(await api('/api/flowchart/pipelines?' + query)).toEqual(savedList);
  const restoredResources = await api('/api/flowchart/execution-resources', {device: 'cpu', device_slots: 1}, 'PUT');
  expect(restoredResources).toEqual(resourcesBefore);
  const serialReferences = []; for (const item of original.cohort) serialReferences.push(await run(item, 1));
  const parallelFile = path.join(workspace.logs, 'retained-unet-app-parallel-references.json');
  fs.writeFileSync(parallelFile, JSON.stringify(results)); evidence.addFile(parallelFile);
  const referenceFile = path.join(workspace.logs, 'retained-unet-app-serial-references.json');
  fs.writeFileSync(referenceFile, JSON.stringify(serialReferences)); evidence.addFile(referenceFile);
  const packageProof = invoke('package', ['--workspace', workspace.root, '--project', JSON.stringify(project),
    '--pipeline', savedGraphFile, '--references', referenceFile, '--parallel-references', parallelFile], 115_000);
  expect(packageProof.parity).toHaveLength(2); for (const row of packageProof.parity) expect(row.comparison).toMatchObject({status: 'passed', mismatched_fields: []});
  const packageFile = path.join(workspace.logs, 'retained-unet-package-proof.json'); fs.writeFileSync(packageFile, JSON.stringify(packageProof)); evidence.addFile(packageFile);
  for (const row of packageProof.parity) evidence.addFile(row.output);
  await settle();
  const apiAfter: Record<string, any> = {}; for (const [endpoint, record] of Object.entries(apiBefore)) {
    apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(record);
  }
  await settle();
  const after: Record<string, Tree> = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(String(root))]));
  for (const key of ['annotations', 'copiedModel', 'originalSource', 'originalModel', 'harnessDataset']) expect(after[key]).toEqual(before[key]);
  const sourceKey = sha(source).slice(0, 16), draftRelative = 'flowcharts/drafts/default/' + sourceKey + '/draft.json';
  const draftFile = path.join(project.project_dir, draftRelative), draftRaw = fs.readFileSync(draftFile);
  const {active_version_id: _active, ...persistedDraft} = drafts.at(-1)!.response;
  expect(JSON.parse(draftRaw.toString())).toEqual(persistedDraft);
  evidence.addFile(draftFile);
  const allowed = ['flowcharts/active.json', 'flowcharts/pipeline_segmentation_' + sourceKey + '.json',
    'configuration_changes.sqlite3', 'flowcharts/versions/' + saved.version_id + '.json', draftRelative];
  const changed = [...new Set([...Object.keys(before.project.files), ...Object.keys(after.project.files)])].filter(relative =>
    JSON.stringify(before.project.files[relative]) !== JSON.stringify(after.project.files[relative])).sort();
  expect(changed).toEqual(allowed.sort()); expect(after.project.directories).toEqual([...before.project.directories,
    'flowcharts/drafts', 'flowcharts/drafts/default', 'flowcharts/drafts/default/' + sourceKey].sort());
  const changesAfter = await api('/api/flowchart/changes'); expect(changesAfter.integrity).toEqual({intact: true, rows: 2});
  expect(changesAfter.changes.slice(1)).toEqual(changesBefore.changes);
  expect(changesAfter.changes[0]).toMatchObject({action: 'save', parent_revision: initial.version_id, next_revision: saved.version_id, layout_only: false});
  expect(changesAfter.runtime).toEqual(changesBefore.runtime);
  expect(await api('/api/flowchart/pipelines?' + query)).toEqual(savedList);
  expect(await api('/api/flowchart/execution-resources?device=cpu')).toEqual(resourcesBefore);
  await settle(); for (const [key, root] of Object.entries(roots)) expect(tree(String(root))).toEqual(after[key]);
  invoke('check');
  for (const write of writes) {
    const endpoint = new URL(write.url).pathname;
    expect(['/api/flowchart/models/verify', '/api/flowchart/execution-resources', '/api/flowchart/pipeline', '/api/flowchart/run', '/api/flowchart/draft']).toContain(endpoint);
    if (endpoint === '/api/flowchart/models/verify') {expect(write.method).toBe('POST'); expect(write.body.source_dataset_path).toBe(source);
      expect(write.body.models).toEqual([{job_id: original.job_id, task: 'segmentation'}, {job_id: original.job_id, task: 'segmentation'}]);}
    const response = await write.request.response(); expect(response).not.toBeNull(); expect(response!.status()).toBe(200);
  }
  expect(writes.filter(row => new URL(row.url).pathname === '/api/flowchart/run')).toHaveLength(4);
  expect(writes.filter(row => new URL(row.url).pathname === '/api/flowchart/pipeline')).toHaveLength(1);
  expect(writes.filter(row => new URL(row.url).pathname === '/api/flowchart/draft').map(row => ({method: row.method, body: row.body})))
    .toEqual(drafts.map(row => ({method: 'PUT', body: row.request})));
  for (const read of finishedReads.filter(row => new URL(row.url).pathname === '/api/flowchart/draft' && row.status === 200)) {
    const accepted = drafts.find(row => row.response.draft_sha256 === read.body.draft_sha256); expect(accepted).toBeDefined();
    expect(read.body.pipeline).toEqual(accepted!.response.pipeline);
    expect({...read.body, active_version_id: null}).toEqual(accepted!.response);
    expect(read.body.active_version_id).toBe(read.body.draft_sha256 === graphSha && read.finished >= drafts.at(-1)!.finished && read.body.active_version_id !== null
      ? saved.version_id : null);
  }
  // Browser direct request API restore is recorded here separately; Electron
  // uses original renderer fetch and therefore appears in writes as well.
  expect(writes.filter(row => new URL(row.url).pathname === '/api/flowchart/execution-resources')).toHaveLength(sourceElectron ? 2 : 1);
  evidence.note('retained_unet_parallel_package', {binding_sha256: bindingHash, project, original, originalCheck, prepared, catalog, verified,
    initial, setupWrites, validatedRevision, saved, savedList, exactPipeline, graphSha, roots, before, after, apiBefore, apiAfter, changed, changesBefore, changesAfter,
    resourcesBefore, resourcesParallel, restoredResources, drafts, persistedDraft, draft_raw: {relative_path: draftRelative, bytes: draftRaw.length, sha256: sha(draftRaw)}, runs, packageProof,
    writes: writes.map(({request: _request, ...record}) => record), explicit_restore_write: {method: 'PUT', endpoint: '/api/flowchart/execution-resources',
      body: {device: 'cpu', device_slots: 1}, response: restoredResources}, finishedReads,
    actual_source_electron: sourceElectron, compiled_backend_covered: false, actual_os_dialog: false,
    original_training_reused: true, new_training: false, original_validation_overlap: true,
    model_quality_approved: false, whole_flow_approved: false, release_approved: false, target_device_accepted: false});
  const finalFile = path.join(workspace.logs, 'retained-unet-final.json'); fs.writeFileSync(finalFile, JSON.stringify({before, after, apiBefore, apiAfter,
    validatedRevision, drafts, persistedDraft, draft_raw: {relative_path: draftRelative, bytes: draftRaw.length, sha256: sha(draftRaw)},
    runs, packageProof, changesBefore, changesAfter, changed}, null, 2)); evidence.addFile(finalFile);
}

const browserApi = (request: APIRequestContext, origin: string): Api => async (route, body, method = body === undefined ? 'GET' : 'POST') => {
  const started = Date.now(), response = await within(request.fetch(origin + route, {method, data: body, timeout: 10_000}), started + 10_000);
  expect(response.status()).toBe(200); return within(response.json(), started + 10_000);
};
test('retained original UNet source two ROI parallel aggregate and standalone package', {tag: '@owned-model'}, async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port); await exercise(page, workspace, evidence, browserApi(request, renderer.origin), false, renderer.url);
});
test('native retained original UNet source two ROI parallel aggregate and standalone package', {tag: ['@electron', '@owned-model']},
  async ({electronSession, workspace, evidence}) => {
    const page = electronSession.window, backend = await electronSession.waitForBackend();
    const api: Api = (route, body, method = body === undefined ? 'GET' : 'POST') => page.evaluate(async ({port, route, body, method}) => {
      const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10_000);
      try {const response = await fetch(`http://127.0.0.1:${port}${route}`, {method, signal: controller.signal,
        headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
        const value = await response.json(); if (response.status !== 200) throw Error(route + ' ' + response.status + ' ' + JSON.stringify(value)); return value;
      } finally {clearTimeout(timer);}
    }, {port: backend.port, route, body, method});
    await exercise(page, workspace, evidence, api, true);
  });

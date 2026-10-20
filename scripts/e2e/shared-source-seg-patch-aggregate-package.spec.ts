import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {confirmFlowSave} from './fixtures/flowChange';
import {openTaskCenter} from './qa/appFlow';
const harness = require('./fixtures/harness.cjs');
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
type Tree = {directories: string[]; files: Record<string, {bytes: number; sha256: string}>};
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
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
  try {const value = await Promise.race([promise, new Promise<T>((_, reject) => {
    timer = setTimeout(() => reject(Error('Original absolute operation deadline expired')), remaining);
  })]); expect(Date.now()).toBeLessThanOrEqual(deadline); return value;}
  finally {if (timer) clearTimeout(timer);}
}

function patchCorrespondence(raw: any, result: any, checkpointSha: string, image: any) {
  expect(raw).toMatchObject({task: 'patch_classification', patch_count: 4, patch_size: 32, stride: 32,
    source_size: [64, 64], model_sha256: checkpointSha, source_sha256: image.sha256});
  expect(raw.patches.map((row: any) => row.box)).toEqual([[0, 0, 32, 32], [32, 0, 64, 32], [0, 32, 32, 64], [32, 32, 64, 64]]);
  for (const row of raw.patches) {
    expect(Object.keys(row.class_scores)).toEqual(['OK', 'chip']);
    for (const score of Object.values(row.class_scores) as number[]) expect(Number.isFinite(score) && score >= 0 && score <= 1).toBe(true);
    expect(Math.abs(row.class_scores.OK + row.class_scores.chip - 1)).toBeLessThan(1e-6);
    expect(Math.abs(row.defect_score - (1 - row.class_scores.OK))).toBeLessThan(1e-6);
    expect(row.decision).toBe(row.defect_score >= 0.5 ? 'FAIL' : 'PASS');
  }
  const originalRight = raw.patches.filter((row: any) => row.box[0] === 32);
  const actualRight = result.crops.filter((row: any) => row.source_node_id === 'inspect_patch');
  expect(actualRight).toHaveLength(2);
  for (let index = 0; index < 2; index++) {
    expect(actualRight[index].bbox).toEqual(originalRight[index].box);
    expect(actualRight[index].label).toBe(originalRight[index].predicted_class);
    expect(actualRight[index].verdict).toBe(originalRight[index].decision === 'FAIL' ? 'NG' : 'OK');
    expect(Math.abs(actualRight[index].defect_score - originalRight[index].defect_score)).toBeLessThanOrEqual(1e-6);
    expect(Math.abs(actualRight[index].confidence - originalRight[index].confidence)).toBeLessThanOrEqual(1e-6);
  }
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, origin: string) {
  // Root's single native gate executes two small CPU fits. Ordinary CI excludes
  // @owned-model; source preparation does not invoke this body or any helper CLI.
  test.setTimeout(660_000);
  const bindingFile = process.env.MV_E2E_SHARED_SEG_PATCH_BINDING;
  const bindingHash = process.env.MV_E2E_SHARED_SEG_PATCH_BINDING_SHA256;
  expect(bindingFile).toBeTruthy(); expect(bindingHash).toMatch(/^[a-f0-9]{64}$/);
  expect(sha(fs.readFileSync(bindingFile!))).toBe(bindingHash);
  const binding = JSON.parse(fs.readFileSync(bindingFile!, 'utf8'));
  const helper = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/shared_source_seg_patch.py');
  let helperOrdinal = 0;
  const invoke = (action: string, value: any, timeout = 20_000) => {
    const request = path.join(workspace.logs, `shared-mixed-helper-${helperOrdinal++}-${action}.json`);
    fs.writeFileSync(request, JSON.stringify(value), {flag: 'wx'}); evidence.addFile(request);
    const raw = execFileSync(harness.resolvePython(), ['-B', helper, action, '--request', request], {
      cwd: harness.REPO_ROOT, encoding: 'utf8', timeout, maxBuffer: 64 * 1024 * 1024,
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1'}});
    const output = request + '.result.json'; fs.writeFileSync(output, raw, {flag: 'wx'}); evidence.addFile(output);
    return JSON.parse(raw);
  };
  const fixture = invoke('seed', {workspace: workspace.root, binding: bindingFile, binding_sha256: bindingHash});
  const setup: any[] = [];
  const put = async (route: string, body: any, method = 'POST') => {
    const started = Date.now(), response = await api(route, body, method);
    setup.push({route, method, body, response, started, finished: Date.now()}); return response;
  };
  await put('/api/project/create', {name: 'Same original source SEG and Patch CPU control', task: 'segmentation'});
  const project = await put('/api/project/update', {source_dataset_dir: fixture.source}, 'PUT');
  expect(project).toMatchObject({task: 'segmentation', source_dataset_dir: fixture.source, active_labelset_id: 'default'});
  await put('/api/dataset/import', {folder_path: fixture.source, task: 'segmentation', validate_images: false});
  const split = invoke('split', {workspace: workspace.root, project, fixture});
  // The genuine validated import and explicit acceptance precede both fits and
  // the final UI baseline. No current-library409 is filtered or relabelled.
  const revisionStart = Date.now(), revisionDeadline = revisionStart + 10_000;
  expect(await api('/api/dataset/revisions')).toEqual({active_revision: null, revisions: []});
  const imported = await within(put('/api/dataset/imports', {task: 'segmentation', verify: true,
    invalid_policy: 'reject', follow_links: false}), revisionDeadline);
  expect(imported.source.root).toBe(fixture.source);
  const importReads: any[] = []; let completedImport: any;
  while (Date.now() < revisionDeadline) {
    completedImport = await within(api('/api/dataset/imports/' + imported.job_id), revisionDeadline);
    expect(completedImport.job_id).toBe(imported.job_id); expect(completedImport.source.root).toBe(fixture.source);
    importReads.push(completedImport);
    if (['completed', 'failed', 'aborted', 'interrupted', 'cancelled'].includes(completedImport.state)) break;
    await within(new Promise<void>(resolve => setTimeout(resolve, 50)), revisionDeadline);
  }
  expect(completedImport.state).toBe('completed'); const revision = completedImport.result.revision;
  expect(revision).toMatchObject({state: 'prepared', image_count: 6, valid_count: 6, error_count: 0,
    invalid_policy: 'reject', skipped_links: 0, unreadable_folders: 0, reused_entries: 0, verified_all: true});
  const acceptedRevision = await within(put('/api/dataset/imports/' + imported.job_id + '/accept',
    {revision_id: revision.revision_id, expected_active: null}), revisionDeadline);
  expect(acceptedRevision).toEqual({active_revision: revision.revision_id, job_id: imported.job_id});
  const library = await api('/api/dataset/library/images?state=valid&limit=120');
  expect(library).toMatchObject({revision_id: revision.revision_id, source_root: fixture.source, active: true, next_cursor: null});
  expect(library.items.map((row: any) => ({relative_path: row.relative_path, sha256: row.sha256})).sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path)))
    .toEqual(fixture.images.map((row: any) => ({relative_path: row.relative_path, sha256: row.sha256})).sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path)));
  const preparedPatch = await put('/api/patch-classification/prepare', {patch_size: 32, stride: 32, normal_class: 'OK', minimum_overlap: 0.05});
  expect(preparedPatch).toMatchObject({source_dataset_path: fixture.source, classes: ['OK', 'chip'], patch_count: 24,
    split_counts: {train: 8, val: 8, test: 8}});
  // Initialize the official source review inventory before the ONE immutable
  // version. A first training bind must not add unsnapshotted workflow metadata.
  const readinessStarted = Date.now(), readinessDeadline = readinessStarted + 10_000;
  const readiness = await within(put('/api/team-data/readiness', undefined, 'GET'), readinessDeadline);
  expect(Object.keys(readiness).sort()).toEqual(['ready', 'counts', 'blockers', 'book_version', 'book_sha256',
    'policy_sha256', 'eligibility_sha256', 'eligible_image_uuids', 'gold'].sort());
  expect(readiness.ready).toBe(true);
  expect(readiness.counts).toEqual({approved: 0, pending: 6, rejected: 0, disputed: 0, unused: 0, gold: 0, total: 6, eligible: 6});
  expect(readiness.blockers).toEqual([]); expect(readiness.book_version).toBe(0); expect(readiness.book_sha256).toBeNull();
  expect(readiness.policy_sha256).toBe('0557ed6da6964bdbcb7f5c7a7fcc40fc456f07a97f3df5c7c1f9cc2b1392c50e');
  expect(readiness.eligibility_sha256).toMatch(/^[a-f0-9]{64}$/);
  expect(readiness.eligible_image_uuids).toHaveLength(6); expect(new Set(readiness.eligible_image_uuids).size).toBe(6);
  expect(readiness.gold).toEqual({include_gold_in_training: false, gold_images: 0,
    gold_set_sha256: '4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945'});
  const version = await put('/api/dataset/versions', {name: 'One immutable original source for two typed purposes', dataset_path: fixture.source});
  expect(version.dataset_fingerprint).toMatch(/^v1:[a-f0-9]{64}$/); expect(version.image_count).toBe(6);
  const jobs: Record<string, string> = {}, training: any[] = [];
  const fit = async (task: string, route: string, body: any) => {
    const submittedAt = Date.now(), response = await put(route, body), deadline = submittedAt + 125_000;
    expect(response.job_id).toMatch(/^job_[0-9]+_[a-z0-9]+$/);
    expect(response.training_provenance).toMatchObject({dataset_version_id: version.id, dataset_fingerprint: version.dataset_fingerprint});
    const team = response.training_provenance.team_data;
    expect(team).toMatchObject({book_version: readiness.book_version, book_sha256: readiness.book_sha256,
      policy_sha256: readiness.policy_sha256, eligibility_sha256: readiness.eligibility_sha256, gold: readiness.gold,
      scope: {project_id: project.id, source: fixture.source, labelset_id: 'default'}});
    expect(team.eligibility.map((row: any) => row.image_uuid).sort()).toEqual([...readiness.eligible_image_uuids].sort());
    expect(team.eligibility.map((row: any) => ({relative_path: row.relative_path, content_hash: row.content_hash,
      annotation_hash: row.annotation_hash, mask_hash: row.mask_hash})).sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path)))
      .toEqual(fixture.images.map((row: any) => ({relative_path: row.relative_path, content_hash: row.sha256,
        annotation_hash: row.annotation_sha256, mask_hash: null})).sort((a: any, b: any) => a.relative_path.localeCompare(b.relative_path)));
    if (task === 'patch_classification') {
      expect(Object.keys(response).sort()).toEqual(['job_id', 'status', 'dataset_path', 'source_dataset_path', 'training_provenance'].sort());
      expect(['running', 'queued', 'completed']).toContain(response.status);
      expect(response.dataset_path).toBe(preparedPatch.dataset_path); expect(response.source_dataset_path).toBe(fixture.source);
      expect(response.training_provenance).toMatchObject({family_task: task, family_provenance: {source_dataset_path: fixture.source}});
    } else {expect(response.task).toBe(task); expect(['started', 'queued']).toContain(response.status);}
    jobs[task] = response.job_id; const reads: any[] = []; let terminal: any;
    while (Date.now() < deadline) {
      terminal = await within(api('/api/training/status?job_id=' + response.job_id), deadline); reads.push(terminal);
      if (['completed', 'failed', 'cancelled', 'aborted', 'stopped', 'interrupted'].includes(terminal.status)) break;
      await within(new Promise<void>(resolve => setTimeout(resolve, 200)), deadline);
    }
    expect(terminal.status, JSON.stringify(terminal)).toBe('completed');
    expect(terminal.job_id).toBe(response.job_id); expect(terminal.task).toBe(task);
    expect(terminal.total_epochs).toBe(2); expect(terminal.current_epoch).toBeGreaterThanOrEqual(1);
    const localPath = path.join(project.models_dir, response.job_id, 'local_job.json');
    let local: any;
    while (Date.now() < deadline) {
      local = JSON.parse(fs.readFileSync(localPath, 'utf8'));
      expect(local.job_id).toBe(response.job_id);
      if (local.worker_exit_confirmed === true) break;
      await within(new Promise<void>(resolve => setTimeout(resolve, 50)), deadline);
    }
    expect(local.worker_exit_confirmed).toBe(true); expect(local.worker_exit_code).toBe(0);
    training.push({task, submittedAt, deadline, finished: Date.now(), route, posted: body, accepted: response, reads, terminal, local});
  };
  await fit('segmentation', '/api/training/start', {task: 'segmentation', preset: 'fast', dataset_path: fixture.source,
    device: 'cpu', dataset_version_id: version.id, queue: true, priority: 0, max_runtime_s: 120,
    config_overrides: {model_name: 'dinov3_vits16', train_mode: 'head_only', epochs: 2, image_size: 64, batch_size: 2,
      pretrained_checkpoint: fixture.weights, pretrained_sha256: binding.base_sha256}});
  await fit('patch_classification', '/api/patch-classification/train', {dataset_path: preparedPatch.dataset_path,
    backbone: 'dinov3_vits16', epochs: 2, batch_size: 4, image_size: 32, device: 'cpu', max_runtime_s: 120,
    dataset_version_id: version.id, queue: true, priority: 0, pretrained_checkpoint: fixture.weights, pretrained_sha256: binding.base_sha256});
  // This handoff uses only the genuine completed SEG row and current source.
  await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
  const taskCenter = await openTaskCenter(page);
  await taskCenter.getByLabel('작업 모델 종류', {exact: true}).selectOption('segmentation');
  await taskCenter.getByLabel('저장 작업 다시 열기', {exact: true}).selectOption('training:local:' + jobs.segmentation);
  await expect(taskCenter.getByRole('status').first()).toContainText('완료');
  const evaluationPromise = page.waitForResponse(response => {
    const url = new URL(response.url()); return url.pathname === '/api/evaluation/results'
      && url.searchParams.get('job_id') === jobs.segmentation && response.request().method() === 'GET';
  }, {timeout: 10_000});
  await taskCenter.getByRole('button', {name: '완료 후보 평가로 이동', exact: true}).click();
  const evaluationResponse = await evaluationPromise; expect(evaluationResponse.status()).toBe(200);
  const evaluated = await evaluationResponse.json(); expect(evaluated.job_id).toBe(jobs.segmentation);
  await expect(page.getByLabel('평가 모델', {exact: true})).toHaveValue(jobs.segmentation);
  const handoffs = await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('vision-task-handoff:'))
    .map(([key, value]) => ({key, value: JSON.parse(value)})));
  const handoff = handoffs.find(row => row.value.jobId === jobs.segmentation);
  expect(handoff).toBeTruthy(); expect(handoff!.value).toMatchObject({jobId: jobs.segmentation, family: 'segmentation',
    kind: 'training', status: 'completed', step: 4, taskKey: 'training:local:' + jobs.segmentation});
  expect(JSON.parse(handoff!.value.scope).slice(0, 4)).toEqual([project.project_dir, project.id, fixture.source, 'default']);
  expect(await api('/api/project/current')).toEqual(project);
  await evidence.screenshot(page, 'shared-source-genuine-completed-seg-task-center-evaluation');
  const models = invoke('models', {project, fixture, version, jobs, training});
  for (const fit of training) {expect(models[fit.task].receipt.task).toBe(fit.task);
    expect(models[fit.task].receipt.training_provenance).toEqual(fit.accepted.training_provenance);}
  const catalogRoute = '/api/flowchart/models/catalog?' + new URLSearchParams({source_dataset_path: fixture.source});
  const catalog = await api(catalogRoute);
  expect(catalog.models.map((row: any) => [row.job_id, row.task]).sort()).toEqual(Object.entries(jobs).map(([task, job]) => [job, task]).sort());
  const verified = await put('/api/flowchart/models/verify', {source_dataset_path: fixture.source,
    models: [{job_id: jobs.segmentation, task: 'segmentation'}, {job_id: jobs.patch_classification, task: 'patch_classification'}]});
  const initialRaw = invoke('graph', {jobs});
  const initial = await put('/api/flowchart/pipeline?' + new URLSearchParams({recipe_task: 'mixed', source_dataset_path: fixture.source,
    change_reason: 'Tiny genuine same-source SEG + Patch CPU functional control'}), initialRaw);
  const initialRecord = await api('/api/flowchart/pipelines/' + initial.version_id);
  const draftGraph = JSON.parse(JSON.stringify(initialRecord.pipeline || initialRecord));
  const draftContext = {project_id: project.id, source_dataset_path: fixture.source, labelset_id: 'default'};
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const openFlow = async () => {await stages.getByRole('button').nth(4).click();
    await expect(page.getByRole('heading', {name: '검사 플로우 편집기', exact: true})).toBeVisible();
    await expect(page.locator('[data-flow-node-id="inspect_seg"]')).toBeVisible();
    await expect(page.locator('[data-flow-node-id="inspect_patch"]')).toBeVisible();};
  await openFlow(); await expect(page.getByRole('button', {name: '플로우 저장', exact: true})).toBeEnabled();
  const scoped = new URLSearchParams({source_dataset_path: fixture.source, task: 'segmentation'});
  const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
    '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/metadata/split', '/api/dataset/versions', '/api/dataset/revisions',
    '/api/data-workbench/review-evaluations', '/api/data-workbench/review-queues', '/api/training/jobs', catalogRoute,
    '/api/evaluation/history?' + scoped, '/api/model-deployments/active?' + scoped, '/api/model-deployments/history?' + scoped,
    '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', '/api/runtime-services/capture-groups', '/api/runtime-services',
    '/api/product-delivery/packages', '/api/product-delivery/runtime-packs', '/api/flow-evaluations/approvals/active'];
  const apiBefore: Record<string, any> = {}; for (const endpoint of endpoints) apiBefore[endpoint] = await api(endpoint);
  const resourcesBefore = await api('/api/flowchart/execution-resources?device=cpu');
  expect(resourcesBefore).toMatchObject({engine_device_capacity: 1, active_executions: 0});
  const roots = {project: project.project_dir, annotations: project.annotations_dir, source: fixture.source,
    segModel: path.dirname(models.segmentation.checkpoint), patchModel: path.dirname(models.patch_classification.checkpoint), harnessDataset: workspace.dataset};
  const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(String(root))]));
  for (const key of ['project', 'source', 'harnessDataset']) for (const relative of Object.keys(before[key].files)) {
    const copy = path.join(workspace.logs, 'shared-mixed-before', key, relative); fs.mkdirSync(path.dirname(copy), {recursive: true});
    fs.copyFileSync(path.join(roots[key as keyof typeof roots], relative), copy); evidence.addFile(copy);
  }
  const writes: any[] = [], reads: any[] = [], failures: string[] = [], pending = new Set<Promise<void>>();
  const startedRequests = new Map<Request, number>();
  const observe = (request: Request) => {
    const url = new URL(request.url()); if (!url.pathname.startsWith('/api/')) return;
    const started = Date.now(); startedRequests.set(request, started);
    let work: Promise<void>; work = (async () => {
      const deadline = started + (['/api/flowchart/run', '/api/patch-classification/predict'].includes(url.pathname) ? 60_000 : 10_000);
      const response = await within(request.response(), deadline); expect(response).not.toBeNull();
      await within(response!.finished(), deadline); const raw = await within(response!.body(), deadline);
      const contentType = response!.headers()['content-type'] || '', body = contentType.startsWith('application/json') ? JSON.parse(raw.toString()) : null;
      const row = {method: request.method(), url: request.url(), body: request.method() === 'GET' ? null : request.postDataJSON(),
        status: response!.status(), response: body, raw_base64: raw.toString('base64'), bytes: raw.length, sha256: sha(raw), started, deadline, finished: Date.now()};
      (row.method === 'GET' ? reads : writes).push(row);
      evidence.note('shared_source_seg_patch_original_page_requests', [...reads, ...writes].sort((a, b) => a.started - b.started));
      if (row.method === 'GET' && url.pathname === '/api/flowchart/draft' && row.status === 404)
        expect(['No draft for this project, source and labelset', 'A newer saved flow supersedes this draft']).toContain(body.detail);
      else expect(row.status).toBe(200);
      if (body === null) {
        if (contentType.startsWith('application/json')) {
          expect(row.method).toBe('GET'); expect(url.pathname).toBe('/api/flow-evaluations/approvals/active');
          expect(raw.toString()).toBe('null');
        } else {
          expect(['/api/dataset/thumbnail/', '/api/dataset/raw/'].some(prefix => url.pathname.startsWith(prefix))).toBe(true);
          expect(raw.length).toBeGreaterThan(0);
        }
      }
    })().catch(error => {failures.push(String(error));}).finally(() => {pending.delete(work); startedRequests.delete(request);}); pending.add(work);
  };
  page.on('request', observe);
  const settle = async () => {while (pending.size) await Promise.all([...pending]); expect(failures).toEqual([]);};
  const drafts: any[] = [];
  const settleDraft = async (action: () => Promise<unknown>, edit: () => void) => {
    edit(); const started = Date.now(), deadline = started + 10_000;
    const waiting = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/draft' && response.request().method() === 'PUT');
    await action(); const response = await within(waiting, deadline); expect(response.status()).toBe(200);
    await within(response.finished(), deadline); const record = await within(response.json(), deadline), posted = response.request().postDataJSON();
    expect(posted).toEqual({pipeline: draftGraph, context: draftContext, base_version_id: initial.version_id});
    expect(record.pipeline).toEqual(draftGraph); expect(record.context).toEqual(draftContext); expect(record.base_version_id).toBe(initial.version_id);
    drafts.push({started, deadline, finished: Date.now(), posted, record}); await settle();
  };
  const select = async (identity: string) => {await page.getByRole('tab', {name: '편집', exact: true}).click();
    await page.locator('[data-flow-node-id="' + identity + '"]').getByRole('group').click();};
  await select('roi_seg'); await settleDraft(async () => {await page.getByLabel('너비 (px)', {exact: true}).fill('32');
    await page.getByLabel('너비 (px)', {exact: true}).press('Enter');}, () => {draftGraph.nodes.find((row: any) => row.id === 'roi_seg').data.params.roi_bbox = [0, 0, 32, 64];});
  await select('roi_patch'); await settleDraft(async () => {await page.getByLabel('X 시작 (px)', {exact: true}).fill('32');
    await page.getByLabel('X 시작 (px)', {exact: true}).press('Enter');}, () => {draftGraph.nodes.find((row: any) => row.id === 'roi_patch').data.params.roi_bbox = [32, 0, 96, 64];});
  await settleDraft(async () => {await page.getByLabel('너비 (px)', {exact: true}).fill('32'); await page.getByLabel('너비 (px)', {exact: true}).press('Enter');},
    () => {draftGraph.nodes.find((row: any) => row.id === 'roi_patch').data.params.roi_bbox = [32, 0, 64, 64];});
  await page.locator('summary').filter({hasText: '빠른 노드 추가·연결·실행 자원'}).click();
  await page.locator('summary').filter({hasText: '병렬 실행 · 작업'}).click();
  await settleDraft(() => page.getByLabel('독립 노드 작업 수', {exact: true}).fill('2'), () => {draftGraph.execution_config.max_workers = 2;});
  await settleDraft(() => page.getByLabel('요청 장치 슬롯', {exact: true}).fill('2'), () => {draftGraph.execution_config.device_slots = 2;});
  expect(drafts).toHaveLength(5);
  const resourceResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/execution-resources' && response.request().method() === 'PUT');
  await page.getByRole('button', {name: 'CPU 엔진에 슬롯 적용', exact: true}).click(); expect((await resourceResponse).status()).toBe(200);
  const saveResponse = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline' && response.request().method() === 'POST');
  await page.getByRole('button', {name: '플로우 저장', exact: true}).click();
  const saveReason = 'Same original SEG and Patch jobs, exact source-coordinate ROIs and any_ng CPU aggregation';
  await confirmFlowSave(page, saveReason);
  const savedResponse = await saveResponse; expect(savedResponse.status()).toBe(200); const saved = await savedResponse.json();
  await settle();
  const savedWire = writes.find(row => new URL(row.url).pathname === '/api/flowchart/pipeline' && row.response?.version_id === saved.version_id);
  expect(savedWire).toBeDefined();
  const pipelineRecord = await api('/api/flowchart/pipelines/' + saved.version_id), pipeline = pipelineRecord.pipeline || pipelineRecord;
  expect(pipeline).toEqual(draftGraph); expect(saved.version_id).not.toBe(initial.version_id);
  const savedList = await api('/api/flowchart/pipelines?' + new URLSearchParams({source_dataset_path: fixture.source}));
  const graphSha = savedList.pipelines.find((row: any) => row.version_id === saved.version_id).pipeline_hash;
  expect(drafts.at(-1).record.draft_sha256).toBe(graphSha); expect(savedList.total).toBe(2);
  const parallel: any[] = [], serial: any[] = [], predictions: any[] = [], runRecords: any[] = [], pickerCustody: any[] = [];
  let previousPick: any = null;
  const rememberedPick = () => page.evaluate((projectId) => Object.entries(localStorage)
    .filter(([key]) => key.startsWith('modu.inspectionImage.v2:') && JSON.parse(key.slice('modu.inspectionImage.v2:'.length))[3] === projectId)
    .map(([key, raw]) => ({key, value: JSON.parse(raw)})), project.id);
  const run = async (item: any, capacity: number) => {
    const remembered = await rememberedPick();
    expect(remembered.map(row => row.value)).toEqual(previousPick === null ? [] : [previousPick]);
    const originalItem = library.items.find((row: any) => row.relative_path === item.relative_path);
    expect(originalItem.file_path).toBe(item.path); expect(originalItem.sha256).toBe(item.sha256);
    await page.getByRole('button', {name: /이미지 변경/}).click(); const picker = page.getByRole('dialog', {name: '검사 대상 이미지 선택', exact: true});
    await picker.getByLabel('이미지 검색', {exact: true}).fill(path.basename(item.relative_path));
    const row = picker.getByRole('listitem', {name: item.relative_path, exact: true}); await expect(row).toHaveCount(1); await row.click();
    await picker.getByRole('button', {name: '선택 확정', exact: true}).click(); await settle();
    previousPick = {source: 'dataset', imagePath: item.path, imageId: originalItem.image_uuid, imageUuid: originalItem.image_uuid,
      sha256: item.sha256, relativePath: item.relative_path, fileName: originalItem.file_name,
      thumbnailUrl: `/api/dataset/thumbnail/${encodeURIComponent(originalItem.file_name)}?file_path=${encodeURIComponent(item.path)}`};
    const confirmed = await rememberedPick(); expect(confirmed.map(row => row.value)).toEqual([previousPick]);
    pickerCustody.push({capacity, input: item, before: remembered, after: confirmed});
    await page.getByRole('tab', {name: '테스트', exact: true}).click(); await page.getByLabel('플로우 실행 위치', {exact: true}).selectOption('local_cpu');
    const started = Date.now(), deadline = started + 60_000;
    const waiting = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run' && response.request().method() === 'POST', {timeout: 60_000});
    await page.getByRole('button', {name: '선택 이미지 검사', exact: true}).click(); const response = await within(waiting, deadline);
    expect(response.status()).toBe(200); await within(response.finished(), deadline); const result = await within(response.json(), deadline);
    const posted = response.request().postDataJSON(); expect(posted).toMatchObject({project_id: project.id, device: 'cpu', execution_target: 'local',
      image_path: item.path, pipeline}); expect(posted.stop_node_id).toBeUndefined();
    expect(result).toMatchObject({status: 'success', image_path: item.path, image_id: posted.image_id, graph_sha256: graphSha,
      execution_resources: {requested_workers: 2, requested_device_slots: 2, effective_device_slots: capacity, engine_device_capacity: capacity, device: 'cpu'}});
    expect(result.execution_steps.filter((step: any) => step.node_id.startsWith('inspect_')).map((step: any) => step.status))
      .toEqual(expect.arrayContaining([expect.stringMatching(/^(passed|flagged_ng)$/), expect.stringMatching(/^(passed|flagged_ng)$/)]));
    const prediction = await api('/api/patch-classification/predict', {job_id: jobs.patch_classification, image_path: item.path, device: 'cpu',
      recipe: {version: 1, mode: 'max', threshold: 0.5, vote_fraction: 0.5, minimum_ng_count: 1, threshold_comparison: 'greater_than_or_equal'}});
    patchCorrespondence(prediction, result, models.patch_classification.tree.files['best_model.pt'].sha256, item);
    predictions.push({capacity, input: item, response: prediction}); runRecords.push({started, deadline, finished: Date.now(), capacity, request: posted, response: result});
    await expect(page.getByAltText('실제 검사 결과', {exact: true})).toHaveAttribute('src', result.annotated_image);
    await evidence.screenshot(page, `same-source-seg-patch-${capacity}-${path.basename(item.path)}`); await settle(); return result;
  };
  for (const item of fixture.cohort) parallel.push(await run(item, 2));
  await page.reload(); await openFlow(); await settle(); expect(await api('/api/flowchart/pipelines?' + new URLSearchParams({source_dataset_path: fixture.source}))).toEqual(savedList);
  const restored = await api('/api/flowchart/execution-resources', {device: 'cpu', device_slots: 1}, 'PUT'); expect(restored).toEqual(resourcesBefore);
  for (const item of fixture.cohort) serial.push(await run(item, 1));
  const packageProof = invoke('package', {workspace: workspace.root, project, fixture, version, jobs, models, pipeline, parallel, serial}, 115_000);
  expect(packageProof.parity).toHaveLength(2); for (const row of packageProof.parity) {expect(row.comparison).toEqual(expect.objectContaining({status: 'passed', mismatched_fields: []})); evidence.addFile(row.output);}
  await settle(); const apiAfter: Record<string, any> = {};
  for (const endpoint of endpoints) {apiAfter[endpoint] = await api(endpoint); expect(apiAfter[endpoint]).toEqual(apiBefore[endpoint]);}
  await settle();
  const after = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(String(root))]));
  for (const key of ['annotations', 'source', 'segModel', 'patchModel', 'harnessDataset']) expect(after[key]).toEqual(before[key]);
  const draftRelative = 'flowcharts/drafts/default/' + sha(fixture.source).slice(0, 16) + '/draft.json';
  const allowed = ['flowcharts/active.json', 'flowcharts/pipeline_mixed_' + sha(fixture.source).slice(0, 16) + '.json',
    'configuration_changes.sqlite3', 'flowcharts/versions/' + saved.version_id + '.json', draftRelative];
  const changed = [...new Set([...Object.keys(before.project.files), ...Object.keys(after.project.files)])].filter(relative =>
    JSON.stringify(before.project.files[relative]) !== JSON.stringify(after.project.files[relative])).sort();
  expect(changed).toEqual(allowed.sort());
  expect(after.project.directories).toEqual([...before.project.directories, 'flowcharts/drafts', 'flowcharts/drafts/default',
    'flowcharts/drafts/default/' + sha(fixture.source).slice(0, 16)].sort());
  const draftFile = path.join(project.project_dir, draftRelative); evidence.addFile(draftFile);
  const {active_version_id: _active, ...persistedDraft} = drafts.at(-1).record; expect(JSON.parse(fs.readFileSync(draftFile, 'utf8'))).toEqual(persistedDraft);
  const expectedWrites: any[] = [];
  const expected = (endpoint: string, method: string, query: any, body: any, response: any) =>
    expectedWrites.push({path: endpoint, method, query, body, response});
  const pair = [{job_id: jobs.segmentation, task: 'segmentation'}, {job_id: jobs.patch_classification, task: 'patch_classification'}];
  for (let ordinal = 0; ordinal < 6; ordinal++) expected('/api/flowchart/models/verify', 'POST', {},
    {source_dataset_path: fixture.source, models: pair}, {verified_job_ids: pair.map(row => row.job_id)});
  expected('/api/flowchart/pipeline/diff', 'POST', {expected_version_id: initial.version_id}, pipeline, null);
  expected('/api/flowchart/pipeline/diff', 'POST', {expected_version_id: saved.version_id}, pipeline, null);
  expected('/api/flowchart/execution-resources', 'PUT', {}, {device: 'cpu', device_slots: 2},
    {...resourcesBefore, engine_device_capacity: 2});
  expected('/api/flowchart/execution-resources', 'PUT', {}, {device: 'cpu', device_slots: 1}, resourcesBefore);
  for (const item of [fixture.cohort[0], fixture.cohort[1], fixture.cohort[0]]) {
    const original = library.items.find((row: any) => row.relative_path === item.relative_path);
    const identity = {image_uuid: original.image_uuid, sha256: item.sha256};
    expected('/api/dataset/library/resolve', 'POST', {}, {selections: [identity]},
      {revision_id: revision.revision_id, active: true, results: [{...identity, status: 'found', candidates: [],
        current: {relative_path: item.relative_path, ...identity, valid: 1, file_path: item.path}}]});
  }
  for (const draft of drafts) expected('/api/flowchart/draft', 'PUT', {}, draft.posted, draft.record);
  expected('/api/flowchart/pipeline', 'POST', {recipe_task: 'mixed', source_dataset_path: fixture.source,
    change_reason: saveReason, expected_version_id: initial.version_id}, pipeline, saved);
  for (let ordinal = 0; ordinal < 4; ordinal++) {
    const item = fixture.cohort[ordinal % 2], original = library.items.find((row: any) => row.relative_path === item.relative_path);
    expected('/api/flowchart/run', 'POST', {}, {project_id: project.id, device: 'cpu', execution_target: 'local',
      image_path: item.path, image_id: original.image_uuid, pipeline}, runRecords[ordinal].response);
    expected('/api/patch-classification/predict', 'POST', {}, {job_id: jobs.patch_classification,
      image_path: item.path, device: 'cpu', recipe: {version: 1, mode: 'max', threshold: 0.5, vote_fraction: 0.5,
        minimum_ng_count: 1, threshold_comparison: 'greater_than_or_equal'}}, predictions[ordinal].response);
  }
  const transportProof = invoke('transport', {origin, writes, expected: expectedWrites, initial_version_id: initial.version_id,
    initial_pipeline: initialRecord.pipeline || initialRecord, saved_version_id: saved.version_id, save_started: savedWire.started, pipeline});
  expect(transportProof).toMatchObject({write_count: 27, full_tuple_and_reply: true, incidental_get_inventory_enumerated: false});
  expect(await api('/api/flowchart/execution-resources?device=cpu')).toEqual(resourcesBefore);
  expect(sha(fs.readFileSync(binding.base_weights))).toBe(binding.base_sha256); expect(sha(fs.readFileSync(fixture.weights))).toBe(binding.base_sha256);
  expect(invoke('models', {project, fixture, version, jobs, training})).toEqual(models);
  for (const [key, root] of Object.entries(roots)) expect(tree(String(root))).toEqual(after[key]);
  await settle(); page.off('request', observe);
  const proof = {schema: 'modu-vision.shared-source-seg-patch-actual/v1', bindingHash, fixture, project, split, setup,
    validatedRevision: {imported, importReads, completedImport, acceptedRevision, library}, trainingReadiness: readiness, version, jobs, training, handoff, evaluated, models,
    catalog, verified, initial, drafts, saved, pipeline, graphSha, resourcesBefore, runRecords, predictions, parallel, serial, packageProof,
    roots, before, after, apiBefore, apiAfter, writes, reads, failures, changed, transportProof, expectedWrites, pickerCustody,
    scope: {actual_native_gui: true, actual_local_cpu_fits: 2, epochs_each: 2, max_runtime_s_each: 120,
      actual_gui_training_buttons: false, actual_seg_patch_aggregate: true, actual_standalone_package: true, incidental_renderer_get_inventory_enumerated: false,
      synthetic_only: true, human_truth: false, gpu: false, remote_training: false, quality_approval: false, deployment_approval: false}};
  const proofFile = path.join(workspace.logs, 'shared-source-seg-patch-proof.json'); fs.writeFileSync(proofFile, JSON.stringify(proof, null, 2), {flag: 'wx'});
  evidence.addFile(proofFile); evidence.addFile(bindingFile!); evidence.note('shared_source_seg_patch_qualification', proof);
}

test('native same-source SEG and Patch CPU candidates run two ROI branches aggregate and original standalone package',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, workspace, evidence}) => {
    const {window} = electronSession, backend = await electronSession.waitForBackend();
    const requests: any[] = [];
    const api: Api = async (route, body, method = body === undefined ? 'GET' : 'POST') => {
      const started = Date.now(), deadline = started + (route.startsWith('/api/patch-classification/predict') ? 60_000 : 10_000);
      const reply = await within(window.evaluate(async ({port, route, body, method}) => {
        const response = await fetch(`http://127.0.0.1:${port}${route}`, {method,
          ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
        return {status: response.status, raw: await response.text()};
      }, {port: backend.port, route, body, method}), deadline);
      const parsed = JSON.parse(reply.raw); requests.push({route, method, body: body ?? null, started, deadline,
        finished: Date.now(), status: reply.status, raw: reply.raw, bytes: Buffer.byteLength(reply.raw), sha256: sha(reply.raw), response: parsed});
      expect(reply.status, reply.raw).toBe(200); return parsed;
    };
    try {await exercise(window, workspace, evidence, api, `http://127.0.0.1:${backend.port}`);}
    finally {evidence.note('shared_source_seg_patch_original_api_requests', requests);}
  });

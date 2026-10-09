import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Response, APIRequestContext} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {confirmFlowSave} from './fixtures/flowChange';
const harness = require('./fixtures/harness.cjs');
type Tree = {directories: string[]; files: Record<string, {bytes: number; sha256: string}>};
type Wire = {route: string; method: string; body?: any; status: number; raw: string; response: any; started: number; deadline: number; finished: number};
type Api = (route: string, body?: any, method?: string, budget?: number) => Promise<Wire>;
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
test.use({actionTimeout: 10_000});

function tree(root: string): Tree {
  expect(path.isAbsolute(root)).toBe(true); expect(fs.realpathSync(root)).toBe(root);
  const directories: string[] = [], files: Tree['files'] = {};
  const visit = (folder: string) => {
    for (const item of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const full = path.join(folder, item.name), relative = path.relative(root, full).split(path.sep).join('/');
      expect(item.isSymbolicLink()).toBe(false);
      if (item.isDirectory()) {directories.push(relative); visit(full);}
      else {expect(item.isFile()).toBe(true); const raw = fs.readFileSync(full); files[relative] = {bytes: raw.length, sha256: sha(raw)};}
    }
  };
  visit(root); return {directories: directories.sort(), files};
}

async function within<T>(operation: Promise<T>, deadline: number): Promise<T> {
  const remaining = deadline - Date.now(); expect(remaining).toBeGreaterThan(0);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {return await Promise.race([operation, new Promise<T>((_, reject) => {timer = setTimeout(() => reject(Error('Original request deadline expired')), remaining);})]);}
  finally {if (timer) clearTimeout(timer);}
}

function requestBudget(route: string): number {
  if (route === '/api/flowchart/run') return 60_000;
  if (route === '/api/ocr/evaluate') return 60_000;
  if (route === '/api/export/flow') return 115_000;
  return 10_000;
}

function assertChain(pipeline: any, rotationJob: string, ocrJob: string) {
  expect(pipeline.nodes.map((row: any) => [row.id, row.data.node_type])).toEqual([
    ['node_input', 'input'], ['learned_direction', 'preprocess'], ['node_inspect', 'inspection'],
    ['node_decision', 'decision'], ['node_output', 'output']]);
  expect(pipeline.edges.map((row: any) => [row.source, row.target, row.payload_type])).toEqual([
    ['node_input', 'learned_direction', 'image'], ['learned_direction', 'node_inspect', 'roi'],
    ['node_inspect', 'node_decision', 'result'], ['node_decision', 'node_output', 'result']]);
  expect(pipeline.nodes[1].data).toMatchObject({model_job_id: rotationJob, params: {operation: 'learned_rotation'}});
  expect(pipeline.nodes[2].data).toMatchObject({task: 'ocr', model_job_id: ocrJob, params: {expected_text: '↑'}});
  expect(pipeline.nodes[1].data.params).toEqual({operation: 'learned_rotation'});
  expect(pipeline.nodes[2].data.params).toEqual({expected_text: '↑'});
  expect(pipeline.nodes[3].data.rule).toBe('any_defect_is_ng');
  expect(pipeline.execution_config).toEqual({max_workers: 1, device_slots: 1});
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, rawApi: Api, native: boolean, url?: string) {
  test.setTimeout(420_000);
  const bindingFile = process.env.MV_E2E_RETAINED_ROTATION_BINDING, bindingHash = process.env.MV_E2E_RETAINED_ROTATION_BINDING_SHA256;
  expect(bindingFile).toBeTruthy(); expect(bindingHash).toMatch(/^[a-f0-9]{64}$/);
  const bindingRaw = fs.readFileSync(bindingFile!); expect(sha(bindingRaw)).toBe(bindingHash);
  const original = JSON.parse(bindingRaw.toString('utf8'));
  const helper = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/retained_rotation_inspection.py');
  const invoke = (action: string, args: string[] = []) => JSON.parse(execFileSync(harness.resolvePython(),
    ['-B', helper, action, '--binding', bindingFile!, '--binding-sha256', bindingHash!, ...args],
    {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 15_000, maxBuffer: 8 * 1024 * 1024,
      env: {...process.env, PYTHONDONTWRITEBYTECODE: '1', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1'}}));
  const inputCheck = invoke('check'); expect(inputCheck.glyph_rows).toHaveLength(16);
  expect([...new Set(inputCheck.glyph_rows.map((row: any) => row.text))].sort()).toEqual(['↑', '→', '↓', '←'].sort());
  const archive = invoke('archive', ['--workspace', workspace.root, '--output', path.join(workspace.logs, 'retained-rotation-input.zip')]).archive;
  evidence.addFile(archive.path); expect(archive.synthetic_restore_input).toBe(true); expect(archive.product_backup_creation).toBe(false);

  // Every request begun after this observer gets its original start clock. Body
  // capture begins from the same Request immediately, before later navigation.
  // Native requests already in flight before this point are not claimed captured.
  const traffic: any[] = [], captureFailures: string[] = [], pending = new Set<Promise<void>>();
  const rowByRequest = new Map<Request, any>(), completionByRequest = new Map<Request, Promise<void>>();
  const observe = (request: Request) => {
    const parsed = new URL(request.url()); if (!parsed.pathname.startsWith('/api/')) return;
    const started = Date.now(), deadline = started + requestBudget(parsed.pathname), method = request.method();
    const rawBody = request.postData(), row: any = {method, url: request.url(), started, deadline, request_raw: rawBody,
      request_sha256: rawBody === null ? null : sha(rawBody), request_bytes: rawBody === null ? 0 : Buffer.byteLength(rawBody)};
    traffic.push(row); rowByRequest.set(request, row);
    let operation: Promise<void>;
    operation = (async () => {
      const response = await within(request.response(), deadline); expect(response).not.toBeNull(); expect(response!.request()).toBe(request);
      const raw = await within(response!.body(), deadline); expect(await within(response!.finished(), deadline)).toBeNull();
      row.finished = Date.now(); expect(row.finished).toBeLessThanOrEqual(deadline);
      Object.assign(row, {status: response!.status(), content_type: response!.headers()['content-type'] || '',
        response_bytes: raw.length, response_sha256: sha(raw), response_base64: raw.toString('base64')});
      if (row.content_type.startsWith('application/json')) row.response = JSON.parse(raw.toString('utf8'));
    })().catch(error => {row.capture_error = String(error); captureFailures.push(row.url + ': ' + String(error));})
      .finally(() => pending.delete(operation)); pending.add(operation); completionByRequest.set(request, operation);
  };
  page.on('request', observe);
  const settle = async () => {while (pending.size) await Promise.all([...pending]); expect(captureFailures).toEqual([]);};
  const capturedJson = async (response: Response) => {
    const request = response.request(), row = rowByRequest.get(request), completion = completionByRequest.get(request);
    expect(row).toBeDefined(); expect(completion).toBeDefined();
    if (row.finished === undefined) await within(completion!, row.deadline);
    expect(row.capture_error).toBeUndefined(); expect(row.finished).toBeLessThanOrEqual(row.deadline);
    expect(row.status).toBe(response.status()); expect(row.content_type).toContain('application/json');
    return row.response;
  };
  const fixtureCalls: Wire[] = [];
  const api: Api = async (route, body, method = body === undefined ? 'GET' : 'POST', budget = 10_000) => {
    const row = await rawApi(route, body, method, budget); fixtureCalls.push(row);
    expect(row.status, row.raw).toBe(route === '/api/ocr/train' && method === 'POST' ? 202 : 200);
    expect(row.deadline).toBe(row.started + budget); expect(row.finished).toBeLessThanOrEqual(row.deadline);
    return row;
  };
  const get = async (route: string) => (await api(route)).response;
  const projectRoot = native ? path.join(workspace.userData, 'projects') : workspace.projects;
  fs.mkdirSync(projectRoot, {recursive: true}); expect(fs.realpathSync(projectRoot)).toBe(projectRoot);
  const target = path.join(projectRoot, 'retained_rotation_ocr_flow'); expect(fs.existsSync(target)).toBe(false);
  const restored = (await api('/api/project/restore', {archive_path: archive.path, target_dir: target})).response;
  const project = await get('/api/project/current');
  expect(project).toMatchObject({id: original.project_id, project_dir: target,
    source_dataset_dir: path.join(target, 'dataset/restored_source')});
  expect(path.dirname(project.project_dir)).toBe(projectRoot); expect(fs.realpathSync(target)).toBe(target);
  const source = project.source_dataset_dir, rotationModel = path.join(target, 'models/rotation', original.job_id);
  expect(tree(source)).toEqual(original.input.source);
  expect(sha(fs.readFileSync(path.join(rotationModel, 'best_model.pt')))).toBe(original.checkpoint_sha256);
  const restoredRotation = tree(rotationModel), restoredVersion = tree(path.join(target, 'versions', original.version_id));
  const restoredMeta = JSON.parse(fs.readFileSync(path.join(rotationModel, 'model_meta.json'), 'utf8'));
  const restoredReceipt = JSON.parse(fs.readFileSync(path.join(rotationModel, 'job_receipt.json'), 'utf8'));
  expect(restoredReceipt.status).toBe('completed'); expect(restoredReceipt.task).toBe('rotation');
  expect(restoredReceipt.training_provenance).toEqual(restoredMeta.training_provenance);
  const beforeTraining = {project: tree(target), source: tree(source), original: invoke('check'), harnessDataset: tree(workspace.dataset)};
  // Original image-picker identity requires the real durable validated index;
  // a legacy summary import alone does not accept a revision. This is declared
  // setup under the retained UNet fixture's original single 10s deadline.
  const revisionStarted = Date.now(), revisionDeadline = revisionStarted + 10_000;
  const revisionBounded = async <T>(operation: () => Promise<T>) => {
    const value = await within(operation(), revisionDeadline); expect(Date.now()).toBeLessThanOrEqual(revisionDeadline); return value;
  };
  const revisionsBefore = await revisionBounded(() => get('/api/dataset/revisions'));
  expect(revisionsBefore).toEqual({active_revision: null, revisions: []});
  const importBody = {task: 'classification', verify: true, invalid_policy: 'reject', follow_links: false};
  const indexAdmitted = (await revisionBounded(() => api('/api/dataset/imports', importBody))).response;
  expect(indexAdmitted.source.root).toBe(source); const indexObserved: any[] = []; let indexCompleted: any;
  while (Date.now() < revisionDeadline) {
    indexCompleted = await revisionBounded(() => get('/api/dataset/imports/' + indexAdmitted.job_id));
    expect(indexCompleted.job_id).toBe(indexAdmitted.job_id); expect(indexCompleted.source.root).toBe(source); indexObserved.push(indexCompleted);
    if (['completed', 'failed', 'aborted', 'interrupted', 'cancelled'].includes(indexCompleted.state)) break;
    await revisionBounded(() => new Promise<void>(resolve => setTimeout(resolve, 50)));
  }
  expect(indexCompleted.state).toBe('completed'); const indexedRevision = indexCompleted.result.revision;
  expect(indexedRevision.revision_id).toMatch(/^[a-f0-9]{32}$/);
  expect(indexedRevision).toMatchObject({state: 'prepared', image_count: 16, valid_count: 16, error_count: 0,
    invalid_policy: 'reject', skipped_links: 0, unreadable_folders: 0, reused_entries: 0, verified_all: true});
  const indexAccepted = (await revisionBounded(() => api('/api/dataset/imports/' + indexAdmitted.job_id + '/accept',
    {revision_id: indexedRevision.revision_id, expected_active: null}))).response;
  expect(indexAccepted).toEqual({active_revision: indexedRevision.revision_id, job_id: indexAdmitted.job_id});
  const library = await revisionBounded(() => get('/api/dataset/library/images?state=valid&limit=120'));
  expect(library).toMatchObject({revision_id: indexedRevision.revision_id, source_root: source, active: true, next_cursor: null});
  expect(library.items.map((row: any) => ({name: row.relative_path, sha256: row.sha256})).sort((a: any, b: any) => a.name.localeCompare(b.name)))
    .toEqual(Object.entries(original.input.source.files).map(([name, row]: [string, any]) => ({name, sha256: row.sha256})).sort((a, b) => a.name.localeCompare(b.name)));
  for (const row of library.items) expect(row.image_uuid).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/);
  const validatedIndex = {started: revisionStarted, deadline: revisionDeadline, finished: Date.now(), revisionsBefore,
    importBody, indexAdmitted, indexObserved, indexCompleted, indexedRevision, indexAccepted, library};
  const prepared = (await api('/api/ocr/prepare', {source_dataset_path: source, samples: inputCheck.glyph_rows})).response;
  expect(prepared.provenance.split_counts).toEqual({train: 8, val: 4, test: 4});
  expect(prepared.samples.map(({source_sha256: _sha, ...row}: any) => row)).toEqual(inputCheck.glyph_rows);
  for (const row of prepared.samples) expect(row.source_sha256).toBe(original.input.source.files[row.image].sha256);
  const trainBody = {dataset_path: prepared.dataset_path, epochs: 2, batch_size: 2, image_height: 32, image_width: 32,
    learning_rate: 0.001, device: 'cpu', background: true, seed: 0, queue: false, priority: 0, max_runtime_s: 120,
    recipe: {mode: 'crop', charset: '↑→↓←', normalizer: 'none', text_rules: {}, orientation: 'horizontal'}};
  const admitted = (await api('/api/ocr/train', trainBody)).response;
  expect(admitted).toMatchObject({task: 'ocr', epochs: 2, device: 'cpu', source_dataset_path: source, dataset_path: prepared.dataset_path,
    budget: {max_runtime_s: 120}}); expect(admitted.job_id).toMatch(/^[a-f0-9]{32}$/);
  const ocrJob = admitted.job_id;
  await expect.poll(async () => (await get('/api/ocr/jobs/' + ocrJob)).status, {timeout: 120_000}).toBe('completed');
  const terminal = await get('/api/ocr/jobs/' + ocrJob);
  expect(terminal).toMatchObject({status: 'completed', task: 'ocr', epochs: 2, epoch: 2, device: 'cpu', source_dataset_path: source,
    dataset_path: prepared.dataset_path});
  const ocrModel = path.join(target, 'models/ocr', ocrJob), ocrBeforeFlow = tree(ocrModel);
  const ocrMetadata = JSON.parse(fs.readFileSync(path.join(ocrModel, 'model_meta.json'), 'utf8'));
  const ocrReceipt = JSON.parse(fs.readFileSync(path.join(ocrModel, 'job_receipt.json'), 'utf8'));
  expect(ocrMetadata).toMatchObject({task: 'ocr', epochs_completed: 2, training_samples: 8, validation_samples: 4, recipe: trainBody.recipe});
  expect(ocrReceipt).toMatchObject({job_id: ocrJob, task: 'ocr', status: 'completed', source_dataset_path: source,
    dataset_path: prepared.dataset_path, checkpoint_sha256: sha(fs.readFileSync(path.join(ocrModel, 'best_model.pt')))});
  expect(ocrReceipt.training_provenance).toEqual(ocrMetadata.training_provenance);
  const evaluation = (await api('/api/ocr/evaluate', {job_id: ocrJob, dataset_path: prepared.dataset_path, split: 'test', device: 'cpu'}, 'POST', 60_000)).response;
  expect(evaluation.sample_count).toBe(4); expect(Number.isFinite(evaluation.character_error_rate)).toBe(true);
  // The two-epoch candidate is deliberately not required to recognize the
  // original or corrected arrows accurately. This is execution/provenance QA.
  expect(tree(source)).toEqual(original.input.source); expect(tree(rotationModel)).toEqual(restoredRotation);
  expect(tree(path.join(target, 'versions', original.version_id))).toEqual(restoredVersion);
  const catalogRoute = '/api/flowchart/models/catalog?' + new URLSearchParams({source_dataset_path: source});
  const catalog = await get(catalogRoute);
  for (const [job, task] of [[original.job_id, 'rotation'], [ocrJob, 'ocr']]) {
    const matches = catalog.models.filter((row: any) => row.job_id === job && row.task === task);
    expect(matches).toHaveLength(1); expect(matches[0].source_dataset_path).toBe(source);
  }
  const verified = (await api('/api/flowchart/models/verify', {source_dataset_path: source,
    models: [{job_id: original.job_id, task: 'rotation'}, {job_id: ocrJob, task: 'ocr'}]})).response;
  const graph = await get('/api/flowchart/templates/single-segmentation?' + new URLSearchParams({inspection_task: 'ocr', job_id: ocrJob}));
  const inspect = graph.nodes.find((node: any) => node.id === 'node_inspect'); inspect.data.params = {expected_text: '↑'};
  graph.nodes.splice(1, 0, {id: 'learned_direction', type: 'custom', position: {x: 220, y: 160},
    data: {...graph.nodes[0].data, label: '보관 정방향 후보 · 같은 원본', node_type: 'preprocess', model_job_id: original.job_id,
      params: {operation: 'learned_rotation'}}});
  graph.edges = [{id: 'input-rotation', source: 'node_input', target: 'learned_direction', payload_type: 'image'},
    {id: 'rotation-ocr', source: 'learned_direction', target: 'node_inspect', payload_type: 'roi'},
    {id: 'ocr-decision', source: 'node_inspect', target: 'node_decision', payload_type: 'result'},
    {id: 'decision-output', source: 'node_decision', target: 'node_output', payload_type: 'result'}];
  graph.id = 'retained_rotation_synthetic_glyph_inspection'; graph.name = '보관 학습 회전 → 합성 방향 문자 검사';
  graph.description = 'Same original pixels; two CPU epochs; no representative truth or quality approval';
  assertChain(graph, original.job_id, ocrJob);
  const initial = (await api('/api/flowchart/pipeline?' + new URLSearchParams({recipe_task: 'ocr', source_dataset_path: source,
    change_reason: 'Controlled existing rotation→OCR inspection recipe; retain original checkpoint'}), graph)).response;
  expect(initial.status).toBe('saved');
  const initialGraph = await get('/api/flowchart/pipelines/' + initial.version_id); assertChain(initialGraph, original.job_id, ocrJob);
  if (url) await page.goto(url); else await page.reload();
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const openFlow = async () => {await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await stages.getByRole('button').nth(4).click(); await expect(page.getByRole('heading', {name: '검사 플로우 편집기', exact: true})).toBeVisible();
    await expect(page.locator('[data-flow-node-id="learned_direction"]')).toBeVisible();
    await expect(page.locator('[data-flow-node-id="node_inspect"]')).toBeVisible();};
  await openFlow(); await settle();
  const savePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/pipeline'
    && response.request().method() === 'POST', {timeout: 10_000});
  await page.getByRole('button', {name: '플로우 저장', exact: true}).click();
  await confirmFlowSave(page, 'Explicit same-source learned rotation and synthetic glyph candidate functional inspection');
  const saveResponse = await savePromise; expect(saveResponse.status()).toBe(200);
  const saved = await capturedJson(saveResponse); const exactGraph = await get('/api/flowchart/pipelines/' + saved.version_id);
  const guiSaveWire = rowByRequest.get(saveResponse.request()); expect(guiSaveWire).toBeTruthy();
  assertChain(exactGraph, original.job_id, ocrJob); expect(exactGraph).toEqual(initialGraph);
  await expect(page.getByLabel('저장 버전', {exact: true})).toHaveValue(saved.version_id); await settle();
  // Materialize the genuine package panel's read-only default stores before
  // the protected file baseline. No export, approval or service is performed.
  await stages.getByRole('button').nth(5).click();
  await expect(page.getByRole('region', {name: '전체 검사 플로우 패키지'})).toBeVisible();
  await expect(page.getByRole('region', {name: '전체 검사 플로우 패키지'}).getByRole('combobox', {name: '저장된 플로우 버전', exact: true})).toHaveValue(saved.version_id);
  await settle(); await openFlow(); await settle();
  const scoped = new URLSearchParams({source_dataset_path: source, task: 'classification'});
  const protectedEndpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
    '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/metadata/split', '/api/dataset/versions', '/api/dataset/revisions',
    '/api/model-deployments/active?' + scoped, '/api/model-deployments/history?' + scoped,
    '/api/fleet/targets', '/api/fleet/rollouts', '/api/runtime-services', '/api/runtime-services/capture-groups',
    '/api/training/jobs', '/api/ocr/jobs', '/api/rotation/jobs', catalogRoute];
  const apiBefore: Record<string, any> = {}; for (const route of protectedEndpoints) apiBefore[route] = await get(route);
  expect(apiBefore['/api/fleet/targets']).toEqual({targets: []}); expect(apiBefore['/api/fleet/rollouts']).toEqual({rollouts: []});
  expect(apiBefore['/api/runtime-services']).toMatchObject({runtime: {status: 'stopped'}, active: null,
    native_install: {prepared: false, registered: false, enabled: false, running: false, verified: false}});
  await settle();
  const roots = {project: target, originalProject: original.original_project, originalSource: original.original_source,
    source, rotationModel, originalRotationVersion: path.join(target, 'versions', original.version_id),
    ocrModel, ocrDataset: prepared.dataset_path, harnessDataset: workspace.dataset};
  const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
  const retain = (name: string, value: any) => {const file = path.join(workspace.logs, name); fs.writeFileSync(file, JSON.stringify(value)); evidence.addFile(file); return file;};
  const baseline = retain('retained-rotation-inspection-before.json', {roots, before, beforeTraining, restored, prepared, admitted, terminal,
    ocrMetadata, ocrReceipt, evaluation, catalog, verified, initial, initialGraph, saved, exactGraph, validatedIndex, apiBefore});
  for (const [key, snapshot] of Object.entries(before) as Array<[string, Tree]>) for (const relative of Object.keys(snapshot.files)) {
    const copy = path.join(workspace.logs, 'retained-rotation-before', key, relative); fs.mkdirSync(path.dirname(copy), {recursive: true});
    fs.copyFileSync(path.join(roots[key as keyof typeof roots], relative), copy); evidence.addFile(copy);
  }
  const runs: any[] = [];
  for (const name of ['test_90_0.png', 'test_-90_0.png']) {
    await page.getByRole('button', {name: /이미지 변경/}).click(); const picker = page.getByRole('dialog', {name: '검사 대상 이미지 선택'});
    await picker.getByLabel('이미지 검색').fill(name); const item = picker.getByRole('listitem', {name, exact: true});
    await item.click(); await picker.getByRole('button', {name: '선택 확정', exact: true}).click();
    await page.getByRole('tab', {name: '테스트', exact: true}).click();
    await page.getByLabel('플로우 실행 위치', {exact: true}).selectOption('local_cpu');
    const responsePromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/run'
      && response.request().method() === 'POST', {timeout: 60_000});
    await page.getByRole('button', {name: '선택 이미지 검사', exact: true}).click(); const response = await responsePromise;
    expect(response.status()).toBe(200); const result = await capturedJson(response), posted = response.request().postDataJSON();
    expect(posted).toMatchObject({project_id: project.id, image_path: path.join(source, name), device: 'cpu', execution_target: 'local', pipeline: exactGraph});
    expect(result).toMatchObject({status: 'success', image_path: posted.image_path, inspected_image_size: [32, 32], routed_output_node_id: 'node_output'});
    const rotationStep = result.execution_steps.find((step: any) => step.node_id === 'learned_direction');
    expect(rotationStep).toMatchObject({status: 'passed', input_count: 1, output_count: 1});
    expect(rotationStep.artifacts).toHaveLength(1);
    const rotated = rotationStep.artifacts[0];
    expect(rotated.rotation).toMatchObject({checkpoint_sha256: original.checkpoint_sha256, source_size: [32, 32],
      angle_semantics: 'counterclockwise_upright_correction_degrees_360'});
    expect(Number.isFinite(rotated.rotation.correction_deg)).toBe(true);
    expect(rotated.image).toMatch(/^data:image\/png;base64,/); expect(rotated.image_size).toEqual(rotated.rotation.output_size);
    expect(rotated.source_transform).toHaveLength(3);
    for (const row of rotated.source_transform) {expect(row).toHaveLength(3); expect(row.every(Number.isFinite)).toBe(true);}
    const matrix = rotated.source_transform;
    const determinant = matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
      - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
      + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0]);
    expect(Math.abs(determinant)).toBeGreaterThan(1e-8);
    expect(result.execution_steps.find((step: any) => step.node_id === 'node_inspect')).toMatchObject({status: 'passed', input_count: 1, output_count: 1});
    expect(result.crops).toHaveLength(1); expect(result.crops[0].source_node_id).toBe('node_inspect');
    expect(typeof result.crops[0].recognized_text).toBe('string');
    // The expected upright glyph is a concrete original pixel direction, not
    // an accuracy promise: retain the model's actual mismatch as genuine NG.
    const crop = result.crops[0], matched = crop.recognized_text === '↑';
    expect(crop).toMatchObject({original_text: crop.recognized_text, corrected_text: crop.recognized_text,
      correction_applied: false, rule_violations: matched ? [] : [{rule: 'expected_text'}],
      defect_score: matched ? 0 : 1, verdict: matched ? 'OK' : 'NG'});
    expect(result.final_verdict).toBe(matched ? 'OK' : 'NG'); expect(result.is_ok).toBe(matched);
    expect(result.execution_steps.find((step: any) => step.node_id === 'node_decision').status).toBe(matched ? 'passed' : 'flagged_ng');
    runs.push({name, posted, result}); retain('retained-rotation-run-' + runs.length + '.json', runs.at(-1));
    await expect(page.getByAltText('실제 검사 결과', {exact: true})).toHaveAttribute('src', result.annotated_image);
    await evidence.screenshot(page, 'retained-rotation-ocr-flow-' + runs.length); await settle();
  }
  await stages.getByRole('button').nth(5).click(); const pack = page.getByRole('region', {name: '전체 검사 플로우 패키지'});
  await pack.getByRole('combobox', {name: '저장된 플로우 버전', exact: true}).selectOption(saved.version_id);
  await pack.getByRole('checkbox', {name: '현장 서비스에 적용할 승인 포함 패키지로 만들기'}).uncheck();
  await pack.getByRole('radio', {name: '한 장 (제한된 확인)', exact: true}).check();
  await pack.getByRole('combobox', {name: '한 장 확인 이미지', exact: true}).selectOption(path.join(source, 'test_90_0.png'));
  await expect(pack.getByRole('combobox', {name: '배포 프로필', exact: true})).toHaveValue('standard');
  const exportPromise = page.waitForResponse(response => new URL(response.url()).pathname === '/api/export/flow'
    && response.request().method() === 'POST', {timeout: 115_000});
  await pack.getByRole('button', {name: '전체 플로우 내보내기', exact: true}).click(); const exportedResponse = await exportPromise;
  expect(exportedResponse.status()).toBe(200); const exported = await capturedJson(exportedResponse), exportBody = exportedResponse.request().postDataJSON();
  expect(exportBody).toMatchObject({source_dataset_path: source, recipe_task: 'ocr', version_id: saved.version_id,
    verification_image_path: path.join(source, 'test_90_0.png'), runtime_config: {device: 'cpu', cpu_threads: 1, deadline_ms: 30000}});
  // The production UI omits standard/target fields; the route's explicit
  // standard default is retained in the actual manifest below.
  expect(exportBody.deployment_profile).toBeUndefined(); expect(exportBody.target_os).toBeUndefined(); expect(exportBody.target_arch).toBeUndefined();
  expect(exportBody.approval_revision_ids).toBeUndefined(); expect(exportBody.compute_profile_id).toBeUndefined();
  expect(exported.model_job_ids).toEqual([original.job_id, ocrJob].sort());
  expect(exported.parity).toMatchObject({contract: 'flow_parity_v1', status: 'passed', scope: 'single_image',
    device: 'cpu', image_count: 1, completed_count: 1, mismatched_fields: [], execution_target: 'local'});
  expect(exported.parity.images[0].image_sha256).toBe(original.input.source.files['test_90_0.png'].sha256);
  const packageRoot = exported.package_path; expect(path.isAbsolute(packageRoot)).toBe(true);
  expect(path.relative(target, packageRoot).startsWith('exports/')).toBe(true); expect(fs.realpathSync(packageRoot)).toBe(packageRoot);
  const packageTree = tree(packageRoot), manifest = JSON.parse(fs.readFileSync(path.join(packageRoot, 'manifest.json'), 'utf8'));
  expect(manifest.deployment).toBeUndefined(); expect(manifest.runtime).toEqual({device: 'cpu', cpu_threads: 1, deadline_ms: 30000});
  expect(manifest.models.map((row: any) => [row.job_id, row.task]).sort()).toEqual([[original.job_id, 'rotation'], [ocrJob, 'ocr']].sort());
  for (const row of manifest.files) expect(packageTree.files[row.path]).toEqual({bytes: row.size, sha256: row.sha256});
  expect(JSON.parse(fs.readFileSync(path.join(packageRoot, 'parity_receipt.json'), 'utf8'))).toEqual({schema_version: 1, ...exported.parity});
  for (const [job, expected] of [[original.job_id, original.checkpoint_sha256], [ocrJob, ocrReceipt.checkpoint_sha256]]) {
    const checkpointMatches = Object.entries(packageTree.files).filter(([relative, record]) => relative.includes(job) && record.sha256 === expected);
    expect(checkpointMatches).toHaveLength(1);
  }
  expect(manifest.release).toBeUndefined(); retain('retained-rotation-export.json', {exportBody, exported, packageTree, manifest});
  await evidence.screenshot(page, 'retained-rotation-ocr-single-image-parity'); await settle();
  await page.reload(); await openFlow(); await page.getByLabel('저장 버전', {exact: true}).selectOption(saved.version_id);
  await expect(page.locator('[data-flow-node-id="learned_direction"]')).toBeVisible(); await settle();
  expect(await get('/api/flowchart/pipelines/' + saved.version_id)).toEqual(exactGraph);
  const apiAfter: Record<string, any> = {}; for (const route of protectedEndpoints) {apiAfter[route] = await get(route); expect(apiAfter[route]).toEqual(apiBefore[route]);}
  await settle(); const after = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
  for (const key of Object.keys(roots).filter(key => key !== 'project')) expect(after[key]).toEqual(before[key]);
  expect(tree(packageRoot)).toEqual(packageTree); expect(invoke('check')).toEqual(inputCheck);
  expect(tree(rotationModel)).toEqual(restoredRotation); expect(tree(ocrModel)).toEqual(ocrBeforeFlow);
  const projectChanges = [...new Set([...Object.keys(before.project.files), ...Object.keys(after.project.files)])].filter(relative =>
    JSON.stringify(before.project.files[relative]) !== JSON.stringify(after.project.files[relative])).sort();
  // Only the declared export subtree, its complete library registration and
  // the original delivery coordination file may change after the baseline.
  const exportRelative = path.relative(target, packageRoot).split(path.sep).join('/');
  for (const relative of projectChanges) expect(relative.startsWith(exportRelative + '/') || ['delivery/library.json', 'delivery/runtime_lifecycle.lock'].includes(relative)).toBe(true);
  expect(after.project.files['delivery/runtime_lifecycle.lock']).toEqual({bytes: 0, sha256: sha(Buffer.alloc(0))});
  const newDirectories = [exportRelative, ...packageTree.directories.map(relative => exportRelative + '/' + relative)];
  let parent = path.posix.dirname(exportRelative);
  while (parent !== '.') {newDirectories.push(parent); parent = path.posix.dirname(parent);}
  expect(after.project.directories).toEqual([...new Set([...before.project.directories, ...newDirectories])].sort());
  const libraryJournal = JSON.parse(fs.readFileSync(path.join(target, 'delivery/library.json'), 'utf8'));
  const packageId = sha(exportRelative).slice(0, 32);
  expect(Object.keys(libraryJournal.packages)).toEqual([packageId]); expect(libraryJournal.selection).toBeNull();
  expect(libraryJournal.packages[packageId]).toMatchObject({project_id: project.id, source_dataset_path: source, task: project.task,
    package_id: packageId, relative_path: exportRelative, manifest_sha256: sha(fs.readFileSync(path.join(packageRoot, 'manifest.json'))),
    version_id: saved.version_id, recipe_task: 'ocr', parity: exported.parity});
  retain('retained-rotation-original-requests.json', traffic);
  const mutations = traffic.filter(row => !['GET', 'HEAD', 'OPTIONS'].includes(row.method));
  for (const row of traffic.filter(row => row.method === 'GET')) {
    const parsed = new URL(row.url);
    if (parsed.pathname === '/api/flowchart/draft' && row.status === 404)
      expect(row.response).toEqual({detail: 'No draft for this project, source and labelset'});
    else if (parsed.pathname === '/api/evaluation/results' && row.status === 404) {
      expect(Object.fromEntries(parsed.searchParams)).toEqual({source_dataset_path: source, source_task: project.task});
      expect(project.task).toBe('classification');
      expect(row.response).toEqual({detail: 'No completed training job has been selected'});
    }
    else expect(row.status, row.url).toBe(200);
  }
  const allowed = ['/api/project/restore', '/api/ocr/prepare', '/api/ocr/train', '/api/ocr/evaluate', '/api/dataset/import',
    '/api/dataset/imports', '/api/dataset/imports/' + indexAdmitted.job_id + '/accept',
    '/api/flowchart/models/verify', '/api/flowchart/pipeline', '/api/flowchart/run', '/api/export/flow',
    '/api/dataset/library/resolve', '/api/flowchart/pipeline/diff'];
  for (const row of mutations) {const endpoint = new URL(row.url).pathname; expect(allowed).toContain(endpoint);
    expect(row.method).toBe('POST'); expect(row.status).toBe(endpoint === '/api/ocr/train' ? 202 : 200);
    const body = JSON.parse(row.request_raw);
    if (endpoint === '/api/dataset/import') expect(body).toEqual({folder_path: source, task: project.task, validate_images: false});
    if (endpoint === '/api/flowchart/models/verify') expect(body).toEqual({source_dataset_path: source,
      models: [{job_id: original.job_id, task: 'rotation'}, {job_id: ocrJob, task: 'ocr'}]});
    if (endpoint === '/api/project/restore') expect(body).toEqual({archive_path: archive.path, target_dir: target});
    if (endpoint === '/api/ocr/prepare') expect(body).toEqual({source_dataset_path: source, samples: inputCheck.glyph_rows});
    if (endpoint === '/api/ocr/train') expect(body).toEqual(trainBody);
    if (endpoint === '/api/ocr/evaluate') expect(body).toEqual({job_id: ocrJob, dataset_path: prepared.dataset_path, split: 'test', device: 'cpu'});
    if (endpoint === '/api/dataset/imports') expect(body).toEqual(importBody);
    if (endpoint === '/api/dataset/imports/' + indexAdmitted.job_id + '/accept')
      expect(body).toEqual({revision_id: indexedRevision.revision_id, expected_active: null});
    if (endpoint === '/api/flowchart/pipeline') {
      // The initial raw submission precedes the producer's explicit null
      // defaults. GUI resave submits the exact serialized saved graph.
      if (row.response.version_id === initial.version_id) {expect(row.response).toEqual(initial); expect(body).toEqual(graph);}
      else {expect(row.response).toEqual(saved); expect(body).toEqual(exactGraph);}
      expect(new URL(row.url).searchParams.get('source_dataset_path')).toBe(source);
      expect(new URL(row.url).searchParams.get('recipe_task')).toBe('ocr');}
    if (endpoint === '/api/flowchart/pipeline/diff') {expect(body).toEqual(exactGraph);
      // A pre-save confirmation compares the initial version; the later
      // reopen confirmation compares the actually saved version.
      expect(Object.fromEntries(new URL(row.url).searchParams)).toEqual({expected_version_id:
        row.started < guiSaveWire.started ? initial.version_id : saved.version_id});}
    if (endpoint === '/api/dataset/library/resolve') {
      expect(Object.keys(body).sort()).toEqual(body.revision_id === undefined ? ['selections'] : ['revision_id', 'selections']);
      if (body.revision_id !== undefined) expect(body.revision_id).toBe(indexedRevision.revision_id);
      for (const selected of body.selections) {expect(Object.keys(selected).sort()).toEqual(['image_uuid', 'sha256']);
        const matches = library.items.filter((item: any) => item.image_uuid === selected.image_uuid && item.sha256 === selected.sha256);
        expect(matches).toHaveLength(1);}
      expect(row.response).toMatchObject({revision_id: indexedRevision.revision_id, active: true});
      expect(row.response.results).toHaveLength(body.selections.length);
      for (const resolved of row.response.results) expect(resolved).toMatchObject({status: 'found', candidates: []});
    }
  }
  expect(mutations.filter(row => new URL(row.url).pathname === '/api/flowchart/pipeline')).toHaveLength(native ? 2 : 1);
  expect(mutations.filter(row => new URL(row.url).pathname === '/api/flowchart/run')).toHaveLength(2);
  expect(mutations.filter(row => new URL(row.url).pathname === '/api/export/flow')).toHaveLength(1);
  for (const endpoint of ['/api/project/restore', '/api/ocr/prepare', '/api/ocr/train', '/api/ocr/evaluate', '/api/dataset/imports',
    '/api/dataset/imports/' + indexAdmitted.job_id + '/accept'])
    expect(mutations.filter(row => new URL(row.url).pathname === endpoint)).toHaveLength(native ? 1 : 0);
  const proof = retain('retained-rotation-inspection-proof.json', {binding_sha256: bindingHash, inputCheck, archive, restored, project, roots, beforeTraining,
    prepared, trainBody, admitted, terminal, ocrMetadata, ocrReceipt, evaluation, catalog, verified, initial, saved, exactGraph, validatedIndex,
    apiBefore, apiAfter, before, after, projectChanges, runs, exported, exportBody, packageTree, manifest, libraryJournal, fixtureCalls, traffic,
    options: traffic.filter(row => row.method === 'OPTIONS'), captured_from_request_event: true, baseline,
    retained_training_reused: true, new_training: {family: 'ocr', epochs: 2, device: 'cpu', max_runtime_s: 120},
    actual_synthetic_glyph_recognition_only: true, recognition_accuracy_asserted: false, representative_truth_accepted: false,
    model_quality_approved: false, release_approved: false, target_device_accepted: false, compiled_backend_covered: false,
    native_console_capture: native ? 'unavailable' : 'harness-browser-only', product_backup_creation: false});
  evidence.note('retained_rotation_inspection_package', {proof_path: proof, proof_sha256: sha(fs.readFileSync(proof)), proof_size: fs.statSync(proof).size});
}

function browserApi(request: APIRequestContext, origin: string): Api {
  return async (route, body, method = body === undefined ? 'GET' : 'POST', budget = 10_000) => {
    const started = Date.now(), deadline = started + budget;
    const response = await within(request.fetch(origin + route, {method, data: body, timeout: budget}), deadline);
    const raw = await within(response.text(), deadline);
    return {route, method, body, status: response.status(), raw, response: JSON.parse(raw), started, deadline, finished: Date.now()};
  };
}
test('retained learned rotation restores exact checkpoint and runs saves reopens a real synthetic OCR inspection package',
  {tag: '@owned-model'}, async ({page, request, renderer, workspace, evidence}) => {
    await installDesktopHostShim(page, renderer.port); await exercise(page, workspace, evidence, browserApi(request, renderer.origin), false, renderer.url);
  });
test('native retained learned rotation executes the same-source synthetic OCR flow and limited CPU package parity',
  {tag: ['@electron', '@owned-model']}, async ({electronSession, workspace, evidence}) => {
    const page = electronSession.window, backend = await electronSession.waitForBackend();
    const api: Api = (route, body, method = body === undefined ? 'GET' : 'POST', budget = 10_000) => page.evaluate(async ({port, route, body, method, budget}) => {
      const started = Date.now(), deadline = started + budget, controller = new AbortController(), timer = setTimeout(() => controller.abort(), budget);
      try {const response = await fetch(`http://127.0.0.1:${port}${route}`, {method, signal: controller.signal,
        headers: {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body)});
        const raw = await response.text(); if (Date.now() > deadline) throw Error('Original API body completed late');
        return {route, method, body, status: response.status, raw, response: JSON.parse(raw), started, deadline, finished: Date.now()};
      } finally {clearTimeout(timer);}
    }, {port: backend.port, route, body, method, budget});
    await exercise(page, workspace, evidence, api, true);
  });

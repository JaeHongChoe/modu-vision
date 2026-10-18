import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffProject} from './fixtures/remaining-project-handoff';

const harness = require('./fixtures/harness.cjs');
const {joinOriginalResponse} = require('./fixtures/u007-response-join.cjs');
test.use({actionTimeout: 15_000});
const sha = (raw: Buffer | string) => crypto.createHash('sha256').update(raw).digest('hex');
const raw9 = (s: fs.BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.uid, s.gid, s.size, s.mtimeNs, s.ctimeNs].map(String);
function filePin(file: string) {
  const named = fs.lstatSync(file, {bigint: true});
  expect(named.isFile()).toBe(true); expect(named.isSymbolicLink()).toBe(false); expect(named.nlink).toBe(1n);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    expect(raw9(fs.fstatSync(fd, {bigint: true}))).toEqual(raw9(named));
    const body = fs.readFileSync(fd); expect(BigInt(body.length)).toBe(named.size);
    expect(raw9(fs.fstatSync(fd, {bigint: true}))).toEqual(raw9(named));
    expect(raw9(fs.lstatSync(file, {bigint: true}))).toEqual(raw9(named));
    return {raw9: raw9(named), size: body.length, sha256: sha(body)};
  } finally { fs.closeSync(fd); }
}
function treePin(root: string): Record<string, unknown> {
  if (!fs.existsSync(root)) return {'.': {kind: 'absent'}};
  const result: Record<string, unknown> = {};
  const visit = (file: string) => {
    const named = fs.lstatSync(file, {bigint: true}), relative = path.relative(root, file) || '.';
    expect(named.isSymbolicLink()).toBe(false); expect(fs.realpathSync(file)).toBe(file);
    if (!named.isDirectory()) { result[relative] = {kind: 'file', ...filePin(file)}; return; }
    const names = fs.readdirSync(file).sort(); result[relative] = {kind: 'directory', raw9: raw9(named), names};
    for (const name of names) visit(path.join(file, name));
    expect(fs.readdirSync(file).sort()).toEqual(names); expect(raw9(fs.lstatSync(file, {bigint: true}))).toEqual(raw9(named));
  };
  visit(root); return result;
}
async function within<T>(pending: Promise<T>, deadline: number, label: string): Promise<T> {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw Error('Original 10s action/body budget expired: ' + label);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try { return await Promise.race([pending, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(Error('Original 10s action/body budget expired: ' + label)), remaining);
  })]); } finally { if (timer) clearTimeout(timer); }
}
type Wire = {request: Request; phase: string; started: number; deadline: number; status?: number;
  body?: string; size?: number; sha256?: string; finished?: number; failure?: string;
  done: Promise<void>; resolve(): void};


type Scope = {tag: 'A' | 'B'; project: any; source: string; fixture: any; seeded: any;
  context: any; seed: any; saved: any; nodeId: string; marker: string; draftFile: string;
  originalFiles: unknown; ownerContext?: any; readback?: any};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
export function canonical(value:any):any{return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;}
export function assertDraftRecord(record:any,pipeline:any,context:any){
 expect(Object.keys(record).sort()).toEqual(['active_version_id','base_version_id','context','draft_sha256','pipeline','saved_at_ns','version']);
 expect(record.version).toBe(1);expect(record.context).toEqual(context);expect(record.pipeline).toEqual(pipeline);
 expect(record.draft_sha256).toBe(sha(JSON.stringify(canonical(pipeline))));expect(record.base_version_id).toBe('none');expect(record.active_version_id).toBeNull();
 expect(Number.isFinite(record.saved_at_ns)).toBe(true);expect(record.saved_at_ns).toBeGreaterThan(0);
}

test('F064 global inspector resize and collapse preserve owning large DAG drafts through A B A project handoff', async ({page, renderer, workspace: w, evidence: e}) => {
  test.setTimeout(180_000);
  const identity = harness.sourceIdentity(); expect(identity.dirty, 'Root must checkpoint the exact reviewed spec').toBe(false);
  const dependencies = ['src/renderer/components/flowchart/FlowInspectorPanel.tsx',
    'src/renderer/components/flowchart/FlowchartStudio.tsx', 'src/renderer/components/flowchart/flowchartStartup.ts', 'src/renderer/stores/useFlowchartStore.ts',
    'src/renderer/stores/useProjectStore.ts', 'src/renderer/services/api.ts', 'src/renderer/services/flowDraft.ts',
    'scripts/e2e/fixtures/flow_large_dag_control.py', 'scripts/e2e/flow-inspector-large-dag.spec.ts',
    'scripts/e2e/fixtures/remaining-project-handoff.ts', 'scripts/e2e/fixtures/u007-response-join.cjs',
    'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs', 'playwright.config.ts',
    'scripts/e2e/flow-inspector-project-handoff.spec.ts'];
  const sourcePins = () => Object.fromEntries(dependencies.map(file => [file, filePin(path.join(harness.REPO_ROOT, file))]));
  const pinsBefore = sourcePins(), wires: Wire[] = [], byRequest = new Map<Request, Wire>(), apiCalls: unknown[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], failedRequests: unknown[] = [], foreignRequests: string[] = [];
  const writes: Array<{phase: string; method: string; route: string; body: unknown}> = [];
  const responseObjects = new Map<Request, Response>();
  const ownerResponse = (response: Response) => {responseObjects.set(response.request(), response);};
  let phase = 'setup', sequence = 0, primary: unknown, baseline: any, A: Scope | undefined, B: Scope | undefined;
  const transitions: unknown[] = [], saves: unknown[] = [], layouts: unknown[] = [], snapshots: unknown[] = [];
  const proof: Record<string, unknown> = {cell: 'F064.large-dag-inspector-layout.handoff', source: identity,
    source_pins_before: pinsBefore, actual_browser: true, installed_native: false, training_or_inference: false,
    cache_branch_coverage: false, cancel_credit: false, empty_credit: false, reopen_credit: false, parent_acceptance: false};
  const retain = (label: string, body: Buffer) => {
    const file = path.join(w.logs, `f064-${++sequence}-${label}.body`);
    fs.writeFileSync(file, body, {flag: 'wx', mode: 0o600}); e.addFile(file);
    return {body: file, size: body.length, sha256: sha(body)};
  };
  const onRequest = (request: Request) => {
    const url = new URL(request.url());
    if (/^https?:$/.test(url.protocol) && url.origin !== renderer.origin) foreignRequests.push(request.url());
    if (url.origin !== renderer.origin || !url.pathname.startsWith('/api/') || request.method() === 'OPTIONS') return;
    let resolve!: () => void;
    const row: Wire = {request, phase, started: performance.now(), deadline: 0,
      done: new Promise<void>(done => {resolve = done;}), resolve: () => resolve()};
    row.deadline = row.started + 10_000; wires.push(row); byRequest.set(request, row);
    if (!['GET', 'HEAD'].includes(request.method())) writes.push({phase, method: request.method(), route: url.pathname, body: request.postDataJSON()});
  };
  const onResponse = (response: Response) => {
    const row = byRequest.get(response.request()); if (!row) return;
    row.status = response.status();
    void (async () => {
      try {
        const [body, terminal] = await within(Promise.all([response.body(), response.finished()]), row.deadline, 'original browser response');
        expect(terminal).toBeNull(); expect(body.length).toBeLessThanOrEqual(2 * 1024 * 1024);
        Object.assign(row, retain('browser-response', body), {finished: performance.now()}); expect(row.finished!).toBeLessThanOrEqual(row.deadline);
        expect(row.status).toBeGreaterThanOrEqual(200); expect(row.status).toBeLessThan(300);
      } catch (error) {row.failure = String(error);} finally {row.resolve();}
    })();
  };
  const onFailed = (request: Request) => {
    failedRequests.push({url: request.url(), error: request.failure()});
    const row = byRequest.get(request); if (row) {row.failure = request.failure()?.errorText || 'Original request failed'; row.resolve();}
  };
  const onPageError = (error: Error) => pageErrors.push(error.message);
  const onConsole = (message: {type(): string; text(): string}) => {if (message.type() === 'error') consoleErrors.push(message.text());};
  page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onFailed);
  page.on('pageerror', onPageError); page.on('console', onConsole);
  const settle = async () => {
    for (let index = 0; index < wires.length; index++) {
      // An already joined receipt retains its original finish/deadline. A
      // later inspection must not re-wait it against an elapsed wall clock.
      await joinOriginalResponse(wires[index], within);
    }
  };
  const api = async (route: string, body?: unknown, method?: string) => {
    const started = performance.now(), deadline = started + 10_000, actualMethod = method || (body === undefined ? 'GET' : 'POST');
    const response = await within(page.request.fetch(renderer.origin + route, {method: actualMethod, timeout: 10_000,
      ...(body === undefined ? {} : {data: body})}), deadline, 'actual fixture request');
    const raw = await within(response.body(), deadline, 'actual fixture full body'); expect(response.status(), raw.toString('utf8')).toBe(200);
    expect(raw.length).toBeLessThanOrEqual(2 * 1024 * 1024);
    apiCalls.push({phase, route, method: actualMethod, request: body ?? null, status: response.status(),
      started, finished: performance.now(), deadline, ...retain('fixture-response', raw)});
    return JSON.parse(raw.toString('utf8'));
  };
  const ownWire = (row: Wire, scope: Scope, method: string, pathname: string) => {
    expect(row.request.frame()).toBe(page.mainFrame()); expect(row.request.method()).toBe(method);
    expect(new URL(row.request.url()).origin).toBe(renderer.origin); expect(new URL(row.request.url()).pathname).toBe(pathname);
    const headers = row.request.headers(), response = responseObjects.get(row.request); expect(response).toBeDefined();
    const encoded = headers['x-vision-context']; expect(encoded).toBeDefined();
    const context = JSON.parse(encoded); expect(headers['x-vision-project']).toBe(scope.project.id);
    expect(context.project_id).toBe(scope.project.id); expect(context.mode).toBe('local');
    expect(typeof context.workspace_id).toBe('string'); expect(typeof context.actor_id).toBe('string');
    expect(context.workspace_id.length).toBeGreaterThan(0); expect(context.actor_id.length).toBeGreaterThan(0);
    expect(JSON.parse(response!.headers()['x-vision-context'])).toEqual(context);
    if (scope.ownerContext) expect(context).toEqual(scope.ownerContext); else scope.ownerContext = context;
    return {method, pathname, project_id: scope.project.id, owner_context: context,
      raw_response: {path: row.body, size: row.size, sha256: row.sha256}, status: row.status,
      started: row.started, deadline: row.deadline, finished: row.finished, actual_same_Request: true};
  };
  const scopedTree = (root: string) => {
    try {expect(fs.lstatSync(root).isSymbolicLink()).toBe(false);} catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    return treePin(root);
  };

  page.on('response', ownerResponse);
  const fixtureScript = path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/flow_large_dag_control.py');
  const makeScope = async (tag: 'A' | 'B'): Promise<Scope> => {
    // The original fixture hardcodes its source name beneath this root. Separate
    // roots preserve both original six-image inputs without any fixture change.
    const root = path.join(w.root, 'owned-large-DAG-' + tag); fs.mkdirSync(root);
    const fixture = JSON.parse(execFileSync(harness.resolvePython(), [fixtureScript, root], {encoding: 'utf8', timeout: 15_000}));
    await api('/api/project/create', {name: 'Owned F064 large DAG handoff ' + tag, task: 'segmentation'});
    await api('/api/project/update', {source_dataset_dir: fixture.source}, 'PUT');
    const project = await api('/api/project/current'); expect(path.dirname(project.project_dir)).toBe(w.projects);
    await api('/api/dataset/import', {folder_path: fixture.source, task: 'segmentation'});
    // Legacy import does not establish the immutable accepted revision used
    // by the renderer library. Complete the original real validation workflow
    // for both six-image sources before opening either owning renderer view.
    const validationDeadline = performance.now() + 10_000;
    const initialRevisions = await within(api('/api/dataset/revisions'), validationDeadline, 'fresh revision state');
    expect(initialRevisions.active_revision).toBeNull(); expect(initialRevisions.revisions).toEqual([]);
    const validation = await within(api('/api/dataset/imports', {task: 'segmentation', verify: true, invalid_policy: 'reject'}), validationDeadline, 'submit actual source validation');
    let validated = validation;
    while (!['completed', 'failed', 'aborted', 'interrupted'].includes(validated.state)) {
      await within(new Promise<void>(resolve => setTimeout(resolve, 20)), validationDeadline, 'original validation progress cadence');
      validated = await within(api('/api/dataset/imports/' + validation.job_id), validationDeadline, 'same original validation job');
    }
    expect(validated.state).toBe('completed');
    const revision = validated.result.revision.revision_id; expect(revision).toMatch(/^[0-9a-f]{32}$/);
    const accepted = await within(api('/api/dataset/imports/' + validation.job_id + '/accept', {revision_id: revision, expected_active: null}), validationDeadline, 'explicit CAS revision acceptance');
    expect(accepted).toEqual({active_revision: revision, job_id: validation.job_id});
    const activeRevisions = await within(api('/api/dataset/revisions'), validationDeadline, 'accepted revision readback');
    expect(activeRevisions.active_revision).toBe(revision);
    const library = await within(api('/api/dataset/library/images?state=valid&limit=120'), validationDeadline, 'real library prerequisite readback');
    expect(library.active).toBe(true); expect(library.source_root).toBe(fixture.source);
    expect(fixture.files).toHaveLength(6); expect(library.items).toHaveLength(6);
    expect(library.items.map((row: any) => row.file_path).sort()).toEqual(fixture.files.map((row: any) => row.path).sort());
    for (const file of fixture.files) expect(library.items.find((row: any) => row.file_path === file.path)).toMatchObject({
      file_path: file.path, relative_path: path.relative(fixture.source, file.path), valid: true, sha256: filePin(file.path).sha256});
    proof['actual_library_prerequisite_' + tag] = {initial: initialRevisions, submitted: validation, terminal: validated, accepted,
      readback: activeRevisions, original_library: library, deadline: validationDeadline, finished: performance.now(),
      no_route_fake_or_409_exception: true, no_additional_legacy_import: true};
    const seeded = JSON.parse(execFileSync(harness.resolvePython(), [fixtureScript, root, JSON.stringify(project)], {encoding: 'utf8', timeout: 15_000}));
    expect(seeded.graph.nodes).toHaveLength(46); expect(seeded.graph.edges).toHaveLength(52);
    expect(seeded.actual_model_execution).toBe(false); expect(filePin(seeded.graph_file).sha256).toBe(seeded.graph_sha256);
    const context = {project_id: project.id, source_dataset_path: fixture.source, labelset_id: project.active_labelset_id || 'default'};
    // F064_INSPECTOR_DRAFT_UNBOUND_MODELS_V1: preserve the original fixture's
    // recipe/version, but edit an explicitly unbound draft. No model is claimed
    // complete, bound, verified or executable by this inspector handoff case.
    const editable = clone(seeded.graph), modelIds = Array.from({length: 8}, (_, index) => 'model-' + String(index).padStart(3, '0'));
    const bound = editable.nodes.filter((node: any) => node.data.model_job_id);
    expect(bound.map((node: any) => node.id)).toEqual(modelIds);
    for (const node of bound) {
      expect(node.data).toMatchObject({node_type: 'inspection', task: 'segmentation', model_job_id: 'job_large_dag_unavailable'});
      node.data.model_job_id = null;
    }
    expect(editable.nodes.some((node: any) => node.data.model_job_id)).toBe(false);
    const restored = clone(editable);
    for (const id of modelIds) restored.nodes.find((node: any) => node.id === id).data.model_job_id = 'job_large_dag_unavailable';
    expect(restored).toEqual(seeded.graph);
    const seed = await api('/api/flowchart/draft', {pipeline: editable, context, base_version_id: 'none'}, 'PUT');
    expect(seed.pipeline).toEqual(editable);
    assertDraftRecord(seed, seed.pipeline, context);
    proof['editable_draft_model_fixture_' + tag] = {policy: 'F064_INSPECTOR_DRAFT_UNBOUND_MODELS_V1',
      unbound_node_ids: modelIds, only_changed_field: 'model_job_id', original_recipe_version_graph_unchanged: true,
      model_binding_or_inference_acceptance: false, draft_sha256: seed.draft_sha256};
    const draftFile = path.join(project.project_dir, 'flowcharts', 'drafts', context.labelset_id, sha(fixture.source).slice(0,16), 'draft.json');
    await api('/api/project/labelsets'); await api('/api/team-data'); await api('/api/team-data/readiness');
    await api('/api/dataset/metadata?limit=100');
    for (const file of fixture.files) await api('/api/annotations/part?file_path=' + encodeURIComponent(file.path));
    const versionFile = path.join(project.project_dir, 'flowcharts', 'versions', seeded.version_id + '.json');
    const originalFiles = {recipe: filePin(seeded.graph_file), version: filePin(versionFile),
      images: fixture.files.map((row: any) => ({path: row.path, pin: filePin(row.path)}))};
    for (const row of fixture.files) expect(filePin(row.path).sha256).toBe(row.sha256);
    return {tag, project, source: fixture.source, fixture, seeded, context, seed, saved: seed, draftFile, originalFiles,
      nodeId: tag === 'A' ? 'model-007' : 'model-000', marker: 'Owned F064 persisted inspector ' + tag};
  };
  const originalFiles = (scope: Scope) => ({recipe: filePin(scope.seeded.graph_file),
    version: filePin(path.join(scope.project.project_dir, 'flowcharts', 'versions', scope.seeded.version_id + '.json')),
    images: scope.fixture.files.map((row: any) => ({path: row.path, pin: filePin(row.path)}))});
  const custody = (scope: Scope) => ({source: scopedTree(scope.source), dataset: scopedTree(scope.project.dataset_dir),
    annotations: scopedTree(scope.project.annotations_dir), models: scopedTree(scope.project.models_dir), reports: scopedTree(scope.project.reports_dir),
    labelsets: scopedTree(path.join(scope.project.project_dir, 'labelsets')), flowcharts: scopedTree(path.join(scope.project.project_dir, 'flowcharts')),
    project: filePin(path.join(scope.project.project_dir, 'project.json')), labelset_registry: filePin(path.join(scope.project.project_dir, 'labelsets.json')),
    originals: originalFiles(scope), draft: filePin(scope.draftFile)});
  const both = () => ({A: custody(A!), B: custody(B!), harness_dataset: scopedTree(w.dataset)});
  const rendered = () => page.evaluate(() => ({
    nodes: [...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row => ({id: row.dataset.flowNodeId, label: row.querySelector('h4')?.textContent})),
    edges: [...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row => ({id: row.dataset.flowEdge,
      source: row.dataset.flowFrom?.split(':')[0], target: row.dataset.flowTo?.split(':')[0]}))}));
  const view = (pipeline: any) => ({nodes: pipeline.nodes.map((row: any) => ({id: row.id, label: row.data.label})),
    edges: pipeline.edges.map((row: any) => ({id: row.id, source: row.source, target: row.target}))});
  const panel = page.getByRole('complementary', {name: '플로우 속성 패널', exact: true});
  const handle = page.getByRole('separator', {name: '속성 패널 크기 조절', exact: true});
  const search = page.getByLabel('플로우 노드 검색', {exact: true});
  const label = page.getByRole('textbox', {name: '노드 명칭', exact: true});
  const savedStatus = page.getByRole('status').filter({hasText: /^초안 저장됨 · 실행본 활성화 전$/});
  const chooseNode = async (scope: Scope) => {
    await search.fill(scope.nodeId); const result = page.getByRole('list', {name: '노드 검색 결과'}).getByRole('button');
    await expect(result).toHaveCount(1); await result.click();
    const node = scope.saved.pipeline.nodes.find((row: any) => row.id === scope.nodeId);
    await expect(label).toHaveValue(node.data.label);
    await expect(page.getByLabel('결함 판정 임계치', {exact: true})).toHaveValue(String(node.data.threshold));
  };
  const enter = async (scope: Scope, first: number, reset: boolean) => {
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(scope.project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click();
    await expect(page.locator('[data-flow-node-id]')).toHaveCount(46);
    await expect(page.locator('path[data-flow-edge]')).toHaveCount(52);
    await expect.poll(rendered).toEqual(view(scope.saved.pipeline)); await expect(savedStatus).toHaveCount(1); await settle();
    const drafts = wires.slice(first).filter(row => row.request.method() === 'GET' && new URL(row.request.url()).pathname === '/api/flowchart/draft');
    expect(drafts.length).toBeGreaterThan(0);
    for (const row of drafts) {ownWire(row, scope, 'GET', '/api/flowchart/draft'); expect(JSON.parse(fs.readFileSync(row.body!, 'utf8'))).toEqual(scope.saved);}
    if (reset) await expect(label).toHaveCount(0);
  };
  const readback = async (scope: Scope) => {
    const value = {project: await api('/api/project/current'), draft: await api('/api/flowchart/draft'),
      recipe: await api('/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path=' + encodeURIComponent(scope.source)),
      version: await api('/api/flowchart/pipelines/' + scope.seeded.version_id),
      versions: await api('/api/flowchart/pipelines?source_dataset_path=' + encodeURIComponent(scope.source)),
      active: await api('/api/flowchart/pipeline/active-version'), labelsets: await api('/api/project/labelsets'),
      metadata: await api('/api/dataset/metadata?limit=100')};
    expect(value.project).toEqual(scope.project); expect(value.draft).toEqual(scope.saved);
    assertDraftRecord(value.draft, scope.saved.pipeline, scope.context);
    expect(value.recipe).toEqual(scope.seeded.graph); expect(value.version).toEqual(scope.seeded.graph);
    expect(value.active).toEqual({version_id: null}); expect(value.versions.total).toBe(1);
    expect(value.versions.pipelines.map((row: any) => row.version_id)).toEqual([scope.seeded.version_id]);
    expect(JSON.parse(fs.readFileSync(scope.draftFile, 'utf8'))).toEqual(Object.fromEntries(Object.entries(scope.saved).filter(([key]) => key !== 'active_version_id')));
    expect(originalFiles(scope)).toEqual(scope.originalFiles);
    expect(fs.existsSync(path.join(scope.project.project_dir, 'flowcharts', 'active.json'))).toBe(false);
    return value;
  };
  const saveMarker = async (scope: Scope) => {
    await chooseNode(scope); const wanted = clone(scope.seed.pipeline);
    wanted.nodes.find((row: any) => row.id === scope.nodeId).data.label = scope.marker;
    const first = wires.length, automaticDeadline = performance.now() + 10_000; await label.fill(scope.marker);
    await within(expect.poll(() => wires.slice(first).some(row => row.request.method() === 'PUT'
      && new URL(row.request.url()).pathname === '/api/flowchart/draft' && row.finished !== undefined
      && JSON.stringify(canonical(row.request.postDataJSON()?.pipeline)) === JSON.stringify(canonical(wanted))),
      {timeout: 10_000}).toBe(true), automaticDeadline, 'actual wanted automatic draft response');
    await expect(savedStatus).toHaveCount(1); await expect.poll(rendered).toEqual(view(wanted)); await settle();
    const automatic = wires.slice(first).filter(row => row.request.method() === 'PUT' && new URL(row.request.url()).pathname === '/api/flowchart/draft'
      && JSON.stringify(canonical(row.request.postDataJSON()?.pipeline)) === JSON.stringify(canonical(wanted)));
    expect(automatic.length).toBeGreaterThan(0);
    for (const row of automatic) {ownWire(row, scope, 'PUT', '/api/flowchart/draft'); assertDraftRecord(JSON.parse(fs.readFileSync(row.body!, 'utf8')), wanted, scope.context);}
    const explicitFirst = wires.length, deadline = performance.now() + 10_000;
    const waiting = page.waitForResponse(response => response.request().method() === 'PUT' && response.request().frame() === page.mainFrame()
      && new URL(response.url()).origin === renderer.origin && new URL(response.url()).pathname === '/api/flowchart/draft', {timeout: 10_000});
    await within(page.getByRole('button', {name: '초안 저장', exact: true}).click(), deadline, 'ordinary explicit owning draft save');
    const response = await within(waiting, deadline, 'same explicit draft response'); await settle();
    const row = byRequest.get(response.request()); expect(row).toBeDefined(); expect(wires.indexOf(row!)).toBeGreaterThanOrEqual(explicitFirst);
    const owning = ownWire(row!, scope, 'PUT', '/api/flowchart/draft');
    expect(row!.request.postDataJSON()).toEqual({pipeline: wanted, context: scope.context, base_version_id: 'none'});
    const record = JSON.parse(fs.readFileSync(row!.body!, 'utf8')); assertDraftRecord(record, wanted, scope.context); scope.saved = record;
    await expect(savedStatus).toHaveCount(1); await expect.poll(rendered).toEqual(view(wanted));
    const disk = filePin(scope.draftFile); expect(JSON.parse(fs.readFileSync(scope.draftFile, 'utf8'))).toEqual(Object.fromEntries(Object.entries(record).filter(([key]) => key !== 'active_version_id')));
    expect(originalFiles(scope)).toEqual(scope.originalFiles);
    saves.push({project_id: scope.project.id, actual_original_auto_puts: automatic.length, owning_explicit: owning,
      request: row!.request.postDataJSON(), saved_record: record, disk});
  };
  const preference = () => page.evaluate(() => JSON.parse(localStorage.getItem('modu-vision:flow-inspector-layout:v1') || 'null'));
  const layout = async (scope: Scope, collapsed: boolean) => {
    await expect(panel).toHaveCSS('width', collapsed ? '44px' : '360px');
    await expect.poll(preference).toEqual({version: 1, width: 360, collapsed});
    if (!collapsed) await expect(handle).toHaveAttribute('aria-valuenow', '360');
    layouts.push({project_id: scope.project.id, app_local_global_preference: await preference(), panel_bbox: await panel.boundingBox()});
  };
  const screenshot = async (name: string, scope: Scope, collapsed: boolean) => {
    await settle(); const deadline = performance.now() + 10_000;
    await within(panel.scrollIntoViewIfNeeded(), deadline, 'owning inspector ordinary scroll');
    await within(expect(panel).toBeVisible(), deadline, 'owning inspector visible');
    if (!collapsed) {
      const threshold = page.getByLabel('결함 판정 임계치', {exact: true});
      await within(threshold.scrollIntoViewIfNeeded(), deadline, 'owning threshold ordinary scroll');
      await within(expect(threshold).toBeVisible(), deadline, 'own threshold visible');
      await within(expect(threshold).toBeInViewport({ratio: 1}), deadline, 'own threshold in viewport');
    }
    await within(e.screenshot(page, name), deadline, 'actual owning layout screenshot');
    proof[name] = {project_id: scope.project.id, collapsed,
      global_preference: await within(preference(), deadline, 'same visual frame preference read'),
      panel_bbox: await within(panel.boundingBox(), deadline, 'same visual frame inspector bounds'),
      separate_scroll_witness: true, whole_graph_and_inspector_simultaneous_visibility_claimed: false};
  };
  const projectSwitch = async (scope: Scope) => {
    const first = wires.length, reply = await handoffProject(page, scope, renderer.origin); await settle();
    const opens = wires.slice(first).filter(row => row.request.method() === 'POST' && new URL(row.request.url()).pathname === '/api/project/open');
    expect(opens).toHaveLength(1); const row = opens[0], headers = row.request.headers();
    expect(headers['x-vision-project']).toBeUndefined(); expect(headers['x-vision-context']).toBeUndefined();
    expect(row.request.postDataJSON()).toEqual({project_dir: scope.project.project_dir});
    const response = responseObjects.get(row.request); expect(response).toBeDefined();
    const target = JSON.parse(response!.headers()['x-vision-context']); expect(target.project_id).toBe(scope.project.id); expect(target.mode).toBe('local');
    if (scope.ownerContext) expect(target).toEqual(scope.ownerContext); else scope.ownerContext = target;
    expect(JSON.parse(fs.readFileSync(row.body!, 'utf8'))).toEqual(scope.project);
    transitions.push({...reply, original_response_body: row.body, original_response_sha256: row.sha256,
      request_context_absent: true, response_owner_context: target});
    await enter(scope, first, true);
  };
  try {
    await installDesktopHostShim(page, renderer.port); A = await makeScope('A'); B = await makeScope('B');
    expect(A.project.id).not.toBe(B.project.id); expect(A.source).not.toBe(B.source);
    await page.goto(renderer.url); await enter(B, 0, false); await saveMarker(B); B.readback = await readback(B);
    phase = 'setup_B_to_A'; await projectSwitch(A); await saveMarker(A); A.readback = await readback(A);
    expect(A.saved.draft_sha256).not.toBe(B.saved.draft_sha256); await settle(); baseline = both(); const writeBoundary = writes.length;
    snapshots.push({phase: 'A-before', custody: baseline, readback: A.readback});
    // Preserve the original real keyboard/pointer resize, without a layout or
    // storage setter. The resulting preference belongs to the whole app.
    await handle.press('Home'); await expect(handle).toHaveAttribute('aria-valuenow', '260');
    await handle.press('ArrowLeft'); await expect(handle).toHaveAttribute('aria-valuenow', '280');
    const box = await handle.boundingBox(); expect(box).not.toBeNull();
    await page.mouse.move(box!.x + 4, box!.y + 40); await page.mouse.down();
    await page.mouse.move(box!.x - 76, box!.y + 40, {steps: 8}); await page.mouse.up();
    await layout(A, false); await screenshot('f064-A-resized-global360-own-draft', A, false);
    await page.getByRole('button', {name: '속성 패널 접기', exact: true}).click(); await layout(A, true);
    await expect(label).toBeHidden(); await screenshot('f064-A-global-collapsed44-before-handoff', A, true);
    phase = 'handoff_A_to_B'; await projectSwitch(B); await layout(B, true);
    await page.getByRole('button', {name: '속성 패널 펼치기', exact: true}).click(); await layout(B, false); await chooseNode(B);
    expect(await readback(B)).toEqual(B.readback); await settle(); expect(both()).toEqual(baseline);
    await screenshot('f064-B-inherits-global360-own-draft', B, false); snapshots.push({phase, custody: both()});
    await page.getByRole('button', {name: '속성 패널 접기', exact: true}).click(); await layout(B, true);
    phase = 'handoff_B_to_A'; await projectSwitch(A); await layout(A, true);
    await page.getByRole('button', {name: '속성 패널 펼치기', exact: true}).click(); await layout(A, false); await chooseNode(A);
    expect(await readback(A)).toEqual(A.readback); await settle(); expect(both()).toEqual(baseline);
    await screenshot('f064-A-return-global360-own-draft', A, false); snapshots.push({phase, custody: both()});
    expect(writes.slice(writeBoundary)).toEqual([
      {phase: 'handoff_A_to_B', method: 'POST', route: '/api/project/open', body: {project_dir: B.project.project_dir}},
      {phase: 'handoff_B_to_A', method: 'POST', route: '/api/project/open', body: {project_dir: A.project.project_dir}},
    ]);
    expect(wires.filter(row => new URL(row.request.url()).pathname === '/api/flowchart/models/verify')).toEqual([]);
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(failedRequests).toEqual([]); expect(foreignRequests).toEqual([]);
    proof.post_baseline_writes = writes.slice(writeBoundary); proof.owning_handoff_and_protected_truth_passed = true;
  } catch (error) {primary = error; throw error;} finally {
    const secondary: string[] = [];
    try {await settle();} catch (error) {secondary.push(String(error));}
    try {expect(sourcePins()).toEqual(pinsBefore); expect(harness.sourceIdentity()).toEqual(identity);} catch (error) {secondary.push(String(error));}
    if (baseline && A && B) {try {expect(both()).toEqual(baseline);} catch (error) {secondary.push(String(error));}}
    for (const scope of [A, B]) if (scope) {
      try {expect(originalFiles(scope)).toEqual(scope.originalFiles);
        expect(fs.existsSync(path.join(scope.project.project_dir, 'flowcharts', 'active.json'))).toBe(false);} catch (error) {secondary.push(String(error));}
    }
    page.off('request', onRequest); page.off('response', onResponse); page.off('response', ownerResponse); page.off('requestfailed', onFailed);
    page.off('pageerror', onPageError); page.off('console', onConsole);
    e.note('F064_large_DAG_inspector_project_handoff', {...proof, projects: [A?.project, B?.project], saves, layouts, transitions, snapshots,
      policy: {global_app_local_preference: true, per_project_preference_claimed: false, graph_label_markers_saved_before_baseline: true,
        no_model_execution: true, post_baseline_draft_writes: 0, unavailable_model_reference_is_fixture_data: true},
      protected_roots_scope: 'Both seven project trees, project/labelset registries, original recipe/version/six source PNGs, owning drafts and harness dataset; unrelated lifecycle SQLite/global active-project pointer is not claimed immutable.',
      rendered_graph_scope: 'All46 node IDs/labels and52 edge IDs/endpoints; complete original positions/topology are protected through original API/disk, without mirroring renderer normalization of negative coordinates.',
      writes, api_calls: apiCalls, original_browser_wires: wires.map(({request, done, resolve, ...row}) => ({...row,
        method: request.method(), url: request.url(), request_body: request.postDataJSON()})),
      page_errors: pageErrors, console_errors: consoleErrors, failed_requests: failedRequests, foreign_requests: foreignRequests,
      primary: primary === undefined ? null : String(primary), secondary_errors: secondary});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary});
    else if (primary === undefined && secondary.length) throw Error(secondary.join('; '));
  }
});

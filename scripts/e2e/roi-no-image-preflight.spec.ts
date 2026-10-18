import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

const harness = require('./fixtures/harness.cjs');
const {joinOriginalResponse} = require('./fixtures/u007-response-join.cjs');
test.use({actionTimeout: 10_000});
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

test('empty ROI image selection shows the owning placeholder and preserves saved original coordinates through node selection and reload', async ({page, renderer, workspace: w, evidence: e}) => {
  const identity = harness.sourceIdentity(); expect(identity.dirty, 'Root must checkpoint the reviewed spec before execution').toBe(false);
  const dependencies = ['src/renderer/components/flowchart/ImageRoiEditor.tsx',
    'src/renderer/components/flowchart/FlowchartStudio.tsx', 'src/renderer/components/flowchart/FlowDraftControls.tsx',
    'src/renderer/components/common/ImagePickerModal.tsx', 'src/renderer/stores/useFlowchartStore.ts',
    'src/renderer/stores/useDatasetStore.ts', 'src/renderer/services/api.ts', 'src/renderer/services/flowDraft.ts',
    'backend/api/routes_flow_workspace.py', 'backend/api/routes_flowchart.py', 'backend/api/routes_dataset.py',
    'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
    'scripts/e2e/fixtures/u007-response-join.cjs', 'scripts/tests/u007-response-join.test.cjs',
    'scripts/e2e/roi-too-small-image.spec.ts', 'scripts/e2e/roi-no-image-preflight.spec.ts', 'playwright.config.ts'];
  const sourcePins = () => Object.fromEntries(dependencies.map(file => [file, filePin(path.join(harness.REPO_ROOT, file))]));
  const pinsBefore = sourcePins(), wires: Wire[] = [], byRequest = new Map<Request, Wire>(), apiCalls: unknown[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], failedRequests: unknown[] = [], foreignRequests: string[] = [];
  const writes: Array<{phase: string; method: string; route: string; body: unknown}> = [];
  let phase = 'setup', sequence = 0, project: any, primary: unknown, baseline: any, afterInvalid: any, afterReopen: any;
  let source = '', normal = '', tiny = '', draftFile = '', originalInputs: unknown;
  const roiId = 'owned-u007-fixed-roi', wanted = [32, 16, 128, 96];
  const proof: Record<string, unknown> = {cell: 'U007.native-original-roi-scale-drag.empty',
    source: identity, source_pins_before: pinsBefore, actual_browser: true, installed_native: false,
    training_or_inference: false, cache_branch_coverage: false, keyboard_invalid_credit: false, parent_acceptance: false};
  const retain = (label: string, body: Buffer) => {
    const file = path.join(w.logs, `u007-${++sequence}-${label}.body`);
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
  const roi = () => page.getByRole('application', {name: '원본 이미지 ROI 편집', exact: true});
  const showNode = async () => {
    const node = page.locator(`[data-flow-node-id="${roiId}"]`); await expect(node).toHaveCount(1);
    await node.locator('[data-flow-node-focus]').click();
  };
  const choose = async (file: string, dimensions: [number, number]) => {
    const first = wires.length, deadline = performance.now() + 10_000;
    await within(page.getByRole('button', {name: '이미지 변경...', exact: true}).click(), deadline, 'ordinary image modal open');
    const dialog = page.getByRole('dialog', {name: '검사 대상 이미지 선택', exact: true});
    await within(dialog.getByRole('button', {name: '로컬 파일', exact: true}).click(), deadline, 'ordinary local-file tab');
    await within(dialog.getByPlaceholder('예: /path/to/inspection-image.png').fill(file), deadline, 'owned literal file path');
    await within(dialog.getByRole('button', {name: '선택 확정', exact: true}).click(), deadline, 'ordinary file confirmation');
    await within(expect(dialog).toHaveCount(0, {timeout: 10_000}), deadline, 'dialog closure');
    await within(expect.poll(() => wires.slice(first).filter(row => new URL(row.request.url()).pathname === '/api/flow-workspace/image-info'
      && new URL(row.request.url()).searchParams.get('image_path') === file).length, {timeout: 10_000}).toBeGreaterThan(0), deadline, 'actual original image-info request');
    await settle();
    const information = wires.slice(first).filter(row => new URL(row.request.url()).pathname === '/api/flow-workspace/image-info'
      && new URL(row.request.url()).searchParams.get('image_path') === file);
    for (const row of information) {
      expect(row.status).toBe(200); expect(JSON.parse(fs.readFileSync(row.body!, 'utf8'))).toEqual({width: dimensions[0], height: dimensions[1], sha256: filePin(file).sha256});
      expect(row.request.frame()).toBe(page.mainFrame());
    }
    const preview = page.locator(`img[src*="/api/dataset/thumbnail/"]`).filter({visible: true});
    await within(expect.poll(() => preview.evaluateAll((nodes, expectedFile) => nodes.some(node => node instanceof HTMLImageElement
      && new URL(node.src).searchParams.get('file_path') === expectedFile && node.complete && node.naturalWidth > 0), file), {timeout: 10_000}).toBe(true), deadline, 'actual selected preview decoded');
    proof[phase + '_selection'] = {file, dimensions, info_original_request_count: information.length,
      original_info_bodies: information.map(row => ({body: row.body, sha256: row.sha256, status: row.status})), local_path_mode: true};
  };
  const protectedState = () => ({source: treePin(source), tiny: filePin(tiny),
    draft: filePin(draftFile), annotations: treePin(project.annotations_dir), models: treePin(project.models_dir),
    reports: treePin(project.reports_dir), flowcharts: treePin(path.join(project.project_dir, 'flowcharts')),
    project: filePin(path.join(project.project_dir, 'project.json')), labelsets: filePin(path.join(project.project_dir, 'labelsets.json'))});
  try {
    source = path.join(w.root, 'owned-u007-normal-source'); fs.mkdirSync(path.join(source, 'train', 'good'), {recursive: true});
    normal = path.join(source, 'train', 'good', 'coordinate-512x256.png');
    const make = (file: string, width: number, height: number) => execFileSync(harness.resolvePython(), ['-B', '-c',
      'from PIL import Image; from pathlib import Path; import sys; p=Path(sys.argv[1]); assert not p.exists(); im=Image.new("RGB",(int(sys.argv[2]),int(sys.argv[3]))); im.putdata([((x*3)%256,(y*5)%256,113) for y in range(im.height) for x in range(im.width)]); im.save(p)',
      file, String(width), String(height)], {timeout: 30_000, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}});
    make(normal, 512, 256);
    project = await api('/api/project/create', {name: 'Owned U007 too-small ROI input', task: 'anomaly'});
    expect(path.dirname(project.project_dir)).toBe(w.projects);
    await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
    const imported = await api('/api/dataset/import', {folder_path: source, task: 'anomaly', validate_images: true});
    expect(imported.validation).toMatchObject({checked_images: 1, complete: true}); expect(imported.corrupted_images).toEqual([]);
    project = await api('/api/project/current'); expect(project.source_dataset_dir).toBe(source);
    // The legacy import above decodes the renderer source; it does not create
    // the immutable accepted revision required by the actual image library.
    // Use the same real verify -> terminal -> CAS accept workflow as the
    // original image-library E2E fixture, before opening the renderer.
    const validationDeadline = performance.now() + 10_000;
    const initialRevisions = await within(api('/api/dataset/revisions'), validationDeadline, 'fresh revision state');
    expect(initialRevisions.active_revision).toBeNull(); expect(initialRevisions.revisions).toEqual([]);
    const validation = await within(api('/api/dataset/imports', {task: 'anomaly', verify: true, invalid_policy: 'reject'}), validationDeadline, 'submit actual source validation');
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
    expect(library.active).toBe(true); expect(library.source_root).toBe(source); expect(library.items).toHaveLength(1);
    expect(library.items[0]).toMatchObject({file_path: normal, relative_path: 'train/good/coordinate-512x256.png', valid: true, sha256: filePin(normal).sha256});
    proof.actual_library_prerequisite = {initial: initialRevisions, submitted: validation, terminal: validated, accepted,
      readback: activeRevisions, original_library: library, deadline: validationDeadline, finished: performance.now(),
      no_route_fake_or_409_exception: true, no_additional_legacy_import: true};
    const tinyRoot = path.join(project.dataset_dir, 'u007-owned-boundary-input'); fs.mkdirSync(tinyRoot);
    tiny = path.join(tinyRoot, 'too-small-8x8.png'); make(tiny, 8, 8);
    originalInputs = {normal: filePin(normal), tiny: filePin(tiny)};
    const context = {project_id: project.id, source_dataset_path: source, labelset_id: project.active_labelset_id};
    const pipeline = {id: 'owned-u007-no-model-flow', name: 'Owned U007 original-coordinate boundary', nodes: [
      {id: 'owned-u007-input', position: {x: 20, y: 160}, data: {label: 'Owned input', node_type: 'input'}},
      {id: roiId, position: {x: 340, y: 160}, data: {label: 'Owned fixed ROI', node_type: 'fixed_roi', params: {roi_bbox: [32, 16, 128, 96]}}},
      {id: 'owned-u007-unbound-inspection', position: {x: 660, y: 160}, data: {label: 'Unbound inspection (never executed)', node_type: 'inspection', task: 'anomaly', model_job_id: null}},
      {id: 'owned-u007-decision', position: {x: 980, y: 160}, data: {label: 'Owned decision', node_type: 'decision'}},
      {id: 'owned-u007-output', position: {x: 1300, y: 160}, data: {label: 'Owned output', node_type: 'output'}},
    ], edges: [
      {id: 'owned-u007-edge-1', source: 'owned-u007-input', target: roiId, payload_type: 'image'},
      {id: 'owned-u007-edge-2', source: roiId, target: 'owned-u007-unbound-inspection', payload_type: 'roi'},
      {id: 'owned-u007-edge-3', source: 'owned-u007-unbound-inspection', target: 'owned-u007-decision', payload_type: 'result'},
      {id: 'owned-u007-edge-4', source: 'owned-u007-decision', target: 'owned-u007-output', payload_type: 'result'},
    ]};
    await api('/api/flowchart/draft', {pipeline, context, base_version_id: 'none'}, 'PUT');
    draftFile = path.join(project.project_dir, 'flowcharts', 'drafts', project.active_labelset_id,
      sha(source).slice(0, 16), 'draft.json');
    await installDesktopHostShim(page, renderer.port); await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click(); await showNode();
    // Fresh renderer state has no selected inspection image. Choosing the
    // real fixed-ROI node must render its own empty branch, not an API fixture.
    phase = 'empty_no_selected_image';await settle();
    const noImage=page.getByText('검사 이미지가 선택되지 않았습니다.',{exact:true});
    const placeholder=page.getByText('상단에서 검사 이미지를 선택하면 원본 위에 ROI를 그릴 수 있습니다.',{exact:true});
    const coordinates=page.getByText('원본 기준 [32, 16, 128, 96]',{exact:false});
    const empty=async()=>{await expect(noImage).toBeVisible();await expect(placeholder).toHaveCount(1);await expect(placeholder).toBeVisible();await expect(roi()).toHaveCount(0);await expect(page.getByLabel('ROI 잘림 미리보기',{exact:true})).toHaveCount(0);await expect(coordinates).toBeVisible();};
    await empty();const saved=await api('/api/flowchart/draft');expect(saved.pipeline.nodes.find((node:any)=>node.id===roiId)?.data.params.roi_bbox).toEqual(wanted);
    e.addFile(normal);e.addFile(tiny);e.addFile(draftFile);
    await settle();baseline=protectedState();proof.saved_draft=saved;const writeBoundary=writes.length,apiBoundary=apiCalls.length,wireBoundary=wires.length;const states:any[]=[];
    const unchanged=async()=>{await settle();expect(writes.slice(writeBoundary)).toEqual([]);expect(await api('/api/flowchart/draft')).toEqual(saved);expect(protectedState()).toEqual(baseline);expect(apiCalls.slice(apiBoundary).every((row:any)=>row.method==='GET')).toBe(true);expect(wires.slice(wireBoundary).filter(row=>new URL(row.request.url()).pathname==='/api/flow-workspace/image-info')).toHaveLength(0);};
    // Causal UI witness: selecting another actual node removes this branch;
    // selecting the fixed ROI again brings it back with unchanged coordinates.
    const input=page.locator('[data-flow-node-id="owned-u007-input"]');await expect(input).toHaveCount(1);await input.locator('[data-flow-node-focus]').click();await expect(placeholder).toHaveCount(0);await expect(roi()).toHaveCount(0);await unchanged();states.push({boundary:'actual-input-node-selected',owning_placeholder_count:await placeholder.count(),ROI_editor_count:await roi().count()});
    await showNode();await empty();await unchanged();states.push({boundary:'actual-fixed-ROI-node-selected-without-image',owning_placeholder_count:await placeholder.count(),ROI_editor_count:await roi().count()});
    await placeholder.scrollIntoViewIfNeeded();await expect(placeholder).toBeInViewport({ratio:1});await e.screenshot(page,'u007-empty-owning-ROI-placeholder-after-real-node-selection');afterInvalid=protectedState();
    phase='empty_ordinary_reload';await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();await showNode();await empty();await unchanged();afterReopen=protectedState();
    expect({normal:filePin(normal),tiny:filePin(tiny)}).toEqual(originalInputs);states.push({boundary:'ordinary-reload-same-saved-ROI-empty-owner',owning_placeholder_count:await placeholder.count(),ROI_editor_count:await roi().count()});expect(states.map(row=>row.owning_placeholder_count)).toEqual([0,1,1]);expect(states.every(row=>row.ROI_editor_count===0)).toBe(true);
    proof.empty_UI_states=states;proof.selected_pending_pointer='/records/129/actions/0/scenarios/empty';proof.no_selected_image=true;proof.drag_or_keyboard_dispatched=false;proof.post_baseline_mutations=writes.slice(writeBoundary);proof.post_baseline_image_info_requests=0;proof.no_cancel_or_reopen_cell_credit=true;
    await placeholder.scrollIntoViewIfNeeded();await expect(placeholder).toBeInViewport({ratio:1});await e.screenshot(page,'u007-empty-saved-ROI-placeholder-after-ordinary-reload');
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(failedRequests).toEqual([]); expect(foreignRequests).toEqual([]);
    expect(fs.existsSync(path.join(project.project_dir, 'flowcharts', 'active.json'))).toBe(false);
  } catch (error) {primary = error; throw error;} finally {
    const secondary: string[] = [];
    try {await settle();} catch (error) {secondary.push(String(error));}
    try {expect(sourcePins()).toEqual(pinsBefore); expect(harness.sourceIdentity()).toEqual(identity);} catch (error) {secondary.push(String(error));}
    page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed);
    page.off('pageerror', onPageError); page.off('console', onConsole);
    e.note('U007_empty_no_selected_image', {...proof, project, normal, tiny, original_inputs: originalInputs,
      boundary_scope: 'Only no-selected-image ROI empty preflight: real input-to-ROI node selection renders the owning placeholder without SVG. No drag, key gesture, image error, cancel, model, whole-flow or native acceptance is claimed',
      protected_roots_scope: 'source, tiny input, draft, flowcharts, annotations, models, reports, project.json, labelsets.json; runtime SQLite/home outside these roots are not claimed immutable',
      before: baseline, after_empty: afterInvalid, after_same_empty_reload: afterReopen, writes, api_calls: apiCalls,
      original_browser_wires: wires.map(({request, done, resolve, ...row}) => ({...row, method: request.method(), url: request.url(), request_body: request.postDataJSON()})),
      page_errors: pageErrors, console_errors: consoleErrors, failed_requests: failedRequests, foreign_requests: foreignRequests,
      primary: primary === undefined ? null : String(primary), secondary_errors: secondary});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary});
    else if (primary === undefined && secondary.length) throw Error(secondary.join('; '));
  }
});

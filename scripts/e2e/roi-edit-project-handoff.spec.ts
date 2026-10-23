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
  done: Promise<void>; resolve(): void;
  // Gallery thumbnail reads only: the browser's own end event, and the retirement that event proved.
  ended?: Promise<'failed' | 'finished'>; end?(kind: 'failed' | 'finished'): void;
  retired?: {at: number; failure: string; project: string | null; file_path: string | null; headers_status: number | null}};


type Scope = {tag: 'A' | 'B'; project: any; source: string; normal: string; tiny: string;
  dimensions: [number, number]; context: any; seed: any; saved: any; roiId: string;
  draftFile: string; originalInputs: unknown; readback?: any; ownerContext?: any};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
export function canonical(value:any):any{return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;}
export function assertDraftRecord(record:any,pipeline:any,context:any){
 expect(Object.keys(record).sort()).toEqual(['active_version_id','base_version_id','context','draft_sha256','pipeline','saved_at_ns','version']);
 expect(record.version).toBe(1);expect(record.context).toEqual(context);expect(record.pipeline).toEqual(pipeline);
 expect(record.draft_sha256).toBe(sha(JSON.stringify(canonical(pipeline))));expect(record.base_version_id).toBe('none');expect(record.active_version_id).toBeNull();
 expect(Number.isFinite(record.saved_at_ns)).toBe(true);expect(record.saved_at_ns).toBeGreaterThan(0);
}

test('U007 actual ROI drag and trusted resize keys survive A B A project handoff with exact saved original coordinates', async ({page, renderer, workspace: w, evidence: e}) => {
  test.setTimeout(240_000);
  const identity = harness.sourceIdentity(); expect(identity.dirty, 'Root must checkpoint the reviewed spec before execution').toBe(false);
  const dependencies = ['src/renderer/components/flowchart/ImageRoiEditor.tsx',
    'src/renderer/components/flowchart/FlowchartStudio.tsx', 'src/renderer/components/flowchart/FlowDraftControls.tsx',
    'src/renderer/components/common/ImagePickerModal.tsx', 'src/renderer/stores/useFlowchartStore.ts',
    'src/renderer/stores/useProjectStore.ts', 'src/renderer/stores/useDatasetStore.ts', 'src/renderer/services/api.ts', 'src/renderer/services/flowDraft.ts',
    'backend/api/routes_flow_workspace.py', 'backend/api/routes_flowchart.py', 'backend/api/routes_dataset.py',
    'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
    'scripts/e2e/fixtures/u007-response-join.cjs', 'scripts/tests/u007-response-join.test.cjs',
    'scripts/e2e/fixtures/remaining-project-handoff.ts', 'scripts/e2e/roi-keyboard-small-image.spec.ts',
    'scripts/e2e/flow-draft-save-error.spec.ts', 'scripts/e2e/roi-edit-project-handoff.spec.ts', 'playwright.config.ts'];
  const sourcePins = () => Object.fromEntries(dependencies.map(file => [file, filePin(path.join(harness.REPO_ROOT, file))]));
  const pinsBefore = sourcePins(), wires: Wire[] = [], byRequest = new Map<Request, Wire>(), apiCalls: unknown[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], failedRequests: unknown[] = [], foreignRequests: string[] = [];
  const retiredThumbnails: Wire[] = [];
  const galleryThumbnail = (request: Request) => request.method() === 'GET' && request.frame() === page.mainFrame() && request.resourceType() === 'fetch'
    && new URL(request.url()).origin === renderer.origin && new URL(request.url()).pathname.startsWith('/api/dataset/thumbnail/');
  const writes: Array<{phase: string; method: string; route: string; body: unknown}> = [];
  const responseObjects = new Map<Request, Response>();
  const ownerResponse = (response: Response) => {responseObjects.set(response.request(), response);};
  let phase = 'setup', sequence = 0, primary: unknown, baseline: any, A: Scope | undefined, B: Scope | undefined;
  let roiId = 'owned-u007-fixed-roi';
  const wanted = [65, 32, 258, 162];
  const proof: Record<string, unknown> = {cells: [
    'U007.native-original-roi-scale-drag.handoff', 'U007.native-original-roi-keyboard-resize.handoff'],
    source: identity, source_pins_before: pinsBefore, actual_browser: true, installed_native: false,
    training_or_inference: false, cache_branch_coverage: false, cancel_credit: false, empty_credit: false, parent_acceptance: false};
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
    if (galleryThumbnail(request)) {let end!: (kind: 'failed' | 'finished') => void;
      row.ended = new Promise(settled => {end = settled;}); row.end = kind => end(kind);}
    if (!['GET', 'HEAD'].includes(request.method())) writes.push({phase, method: request.method(), route: url.pathname, body: request.postDataJSON()});
  };
  const onResponse = (response: Response) => {
    const row = byRequest.get(response.request()); if (!row) return;
    row.status = response.status();
    void (async () => {
      try {
        // A gallery thumbnail read can be retired after its headers arrived, and then the browser keeps no body.
        // Its own end event decides first; onFailed below judges a failed one. A finished one is checked in full.
        if (row.ended && await within(row.ended, row.deadline, 'gallery thumbnail end event') === 'failed') return;
        const [body, terminal] = await within(Promise.all([response.body(), response.finished()]), row.deadline, 'original browser response');
        expect(terminal).toBeNull(); expect(body.length).toBeLessThanOrEqual(2 * 1024 * 1024);
        Object.assign(row, retain('browser-response', body), {finished: performance.now()}); expect(row.finished!).toBeLessThanOrEqual(row.deadline);
        expect(row.status).toBeGreaterThanOrEqual(200); expect(row.status).toBeLessThan(300);
      } catch (error) {row.failure = String(error);} finally {row.resolve();}
    })();
  };
  const onFinished = (request: Request) => {byRequest.get(request)?.end?.('finished');};
  const onFailed = (request: Request) => {
    const row = byRequest.get(request), failure = request.failure()?.errorText || 'Original request failed'; row?.end?.('failed');
    // Leaving step 1 retires the gallery's in-flight thumbnail reads (ScopedDatasetThumbnail aborts them), before or
    // after their headers. Only an aborted main-frame fetch GET /api/dataset/thumbnail/* is set aside and recorded;
    // settle checks it is this spec's own image and project. Every other failure stays a failed request.
    if (row?.ended && /ERR_ABORTED|aborted|cancel/i.test(failure)) {
      row.retired = {at: performance.now(), failure, project: request.headers()['x-vision-project'] ?? null,
        file_path: new URL(request.url()).searchParams.get('file_path'), headers_status: row.status ?? null};
      retiredThumbnails.push(row); row.resolve(); return;
    }
    failedRequests.push({url: request.url(), error: request.failure()});
    if (row) {row.failure = failure; row.resolve();}
  };
  const onPageError = (error: Error) => pageErrors.push(error.message);
  const onConsole = (message: {type(): string; text(): string}) => {if (message.type() === 'error') consoleErrors.push(message.text());};
  // Both end listeners are attached before the request observer, so no thumbnail end event can be missed.
  page.on('requestfailed', onFailed); page.on('requestfinished', onFinished); page.on('request', onRequest); page.on('response', onResponse);
  page.on('pageerror', onPageError); page.on('console', onConsole);
  const retiredRead = (row: Wire) => {
    const url = new URL(row.request.url()), retired = row.retired!;
    expect(row.request.method()).toBe('GET'); expect(row.request.frame()).toBe(page.mainFrame()); expect(row.request.resourceType()).toBe('fetch');
    expect(url.origin).toBe(renderer.origin); expect(url.pathname.startsWith('/api/dataset/thumbnail/')).toBe(true);
    expect(retired.failure).toMatch(/ERR_ABORTED|aborted|cancel/i);
    expect(A && B).toBeTruthy(); const owner = [A!, B!].find(scope => scope.project.id === retired.project);
    expect(owner).toBeDefined(); expect(retired.file_path).toBe(owner!.normal);
    expect(url.pathname).toBe('/api/dataset/thumbnail/' + path.basename(retired.file_path!));
    if (retired.headers_status !== null) expect([200, 304]).toContain(retired.headers_status);
    expect(row.failure).toBeUndefined(); expect(row.body).toBeUndefined();
    expect(retired.at).toBeGreaterThanOrEqual(row.started); expect(retired.at).toBeLessThanOrEqual(row.deadline);
  };
  const settle = async () => {
    for (let index = 0; index < wires.length; index++) {
      const row = wires[index];
      // A gallery thumbnail read first waits for its own end event under its original deadline. Only a read the
      // gallery retired is set aside; a finished or otherwise failed one is joined in full like every other read.
      if (row.ended && !row.retired && row.finished === undefined) await within(row.done, row.deadline, 'gallery thumbnail end event');
      if (row.retired) {retiredRead(row); continue;}
      // An already joined receipt retains its original finish/deadline. A
      // later inspection must not re-wait it against an elapsed wall clock.
      await joinOriginalResponse(row, within);
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
  const keyProofs: unknown[] = [];
  const observeOriginalKeys = async () => page.evaluate(() => {
    const editor = document.querySelector('[role="application"][aria-label="원본 이미지 ROI 편집"]');
    if (!(editor instanceof SVGSVGElement)) throw Error('Original connected ROI SVG is absent');
    const state: any = {phase: '', keys: [], originalEditor: editor};
    (window as any).__u007InvalidROIKeys = state;
    // Observe original browser events only. This listener never prevents or
    // dispatches a key, calls a product callback, or changes product state.
    window.addEventListener('keydown', (event: KeyboardEvent) => {
      if (!['ArrowRight', 'ArrowDown'].includes(event.key)) return;
      const target = event.target instanceof Element ? event.target : null;
      const row: any = {phase: state.phase, at: performance.now(), trusted: event.isTrusted,
        key: event.key, code: event.code, shift: event.shiftKey, ctrl: event.ctrlKey,
        alt: event.altKey, meta: event.metaKey, repeat: event.repeat,
        target_tag: target?.tagName.toUpperCase() ?? null,
        target_role: target?.getAttribute('role') ?? null,
        target_aria_label: target?.getAttribute('aria-label') ?? null,
        target_text: target?.textContent?.trim() ?? null,
        target_connected: target?.isConnected ?? false,
        target_is_original_editor: target === state.originalEditor,
        original_editor_connected: state.originalEditor.isConnected,
        active_element_matches_target: document.activeElement === target,
        editable_or_dialog: Boolean(target && ((target instanceof HTMLElement && target.isContentEditable)
          || target.closest('input, textarea, select, [role="textbox"], [role="dialog"]'))),
        modal: Boolean(document.querySelector('[role="dialog"][aria-modal="true"]')),
        focused_document: document.hasFocus()};
      state.keys.push(row);
      // This matches the existing U020 trusted-key observer. Inspect after
      // native dispatch, including later listeners, and retain two frames.
      requestAnimationFrame(() => {
        row.default_prevented_after_dispatch = event.defaultPrevented;
        row.dispatch_completed = true;
        requestAnimationFrame(() => {row.two_animation_frames = true;});
      });
    }, {capture: true});
  });
  const deliveredKey = async (label: string, key: 'ArrowRight' | 'Shift+ArrowDown', validEditor: boolean) => {
    const deadline = performance.now() + 10_000;
    await within(page.evaluate(label => {
      const state = (window as any).__u007InvalidROIKeys;
      if (!state || state.keys.some((row: any) => row.phase === label)) throw Error('Original key observation scope differs');
      state.phase = label;
    }, label), deadline, 'label original key observation only');
    await within(page.keyboard.press(key), deadline, label + ' trusted ordinary key');
    await within(page.waitForFunction(label => {
      const rows = (window as any).__u007InvalidROIKeys.keys.filter((row: any) => row.phase === label);
      return rows.length === 1 && rows[0].dispatch_completed && rows[0].two_animation_frames;
    }, label, {timeout: 10_000}), deadline, label + ' original dispatch completion');
    const row = await within(page.evaluate(label => (window as any).__u007InvalidROIKeys.keys.find((row: any) => row.phase === label), label), deadline, 'original key event readback');
    expect(row).toMatchObject({phase: label, trusted: true, key: key === 'ArrowRight' ? 'ArrowRight' : 'ArrowDown',
      code: key === 'ArrowRight' ? 'ArrowRight' : 'ArrowDown', shift: key !== 'ArrowRight', ctrl: false, alt: false,
      meta: false, repeat: false, target_connected: true, active_element_matches_target: true,
      target_is_original_editor: validEditor, original_editor_connected: validEditor,
      editable_or_dialog: false, modal: false, focused_document: true,
      dispatch_completed: true, two_animation_frames: true, default_prevented_after_dispatch: validEditor});
    if (validEditor) expect(row).toMatchObject({target_tag: 'SVG', target_role: 'application', target_aria_label: '원본 이미지 ROI 편집'});
    else expect(row).toMatchObject({target_tag: 'BUTTON', target_text: '이미지 변경...'});
    keyProofs.push(row); return row;
  };
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
    return deadline;
  };

  page.on('response', ownerResponse);
  const makeScope = async (tag: 'A' | 'B'): Promise<Scope> => {
    let project: any, source = '', normal = '', tiny = '', draftFile = '', originalInputs: unknown;
    const dimensions: [number, number] = tag === 'A' ? [512, 256] : [384, 192];
    source = path.join(w.root, 'owned-u007-handoff-source-' + tag); fs.mkdirSync(path.join(source, 'train', 'good'), {recursive: true});
    normal = path.join(source, 'train', 'good', tag === 'A' ? 'coordinate-512x256.png' : 'coordinate-384x192.png');
    const make = (file: string, width: number, height: number) => execFileSync(harness.resolvePython(), ['-B', '-c',
      'from PIL import Image; from pathlib import Path; import sys; p=Path(sys.argv[1]); assert not p.exists(); im=Image.new("RGB",(int(sys.argv[2]),int(sys.argv[3]))); im.putdata([((x*3)%256,(y*5)%256,113) for y in range(im.height) for x in range(im.width)]); im.save(p)',
      file, String(width), String(height)], {timeout: 30_000, env: {...process.env, PYTHONDONTWRITEBYTECODE: '1'}});
    make(normal, dimensions[0], dimensions[1]);
    project = await api('/api/project/create', {name: 'Owned U007 ROI edit handoff ' + tag, task: 'anomaly'});
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
    expect(library.items[0]).toMatchObject({file_path: normal, relative_path: 'train/good/' + path.basename(normal), valid: true, sha256: filePin(normal).sha256});
    proof['actual_library_prerequisite_' + tag] = {initial: initialRevisions, submitted: validation, terminal: validated, accepted,
      readback: activeRevisions, original_library: library, deadline: validationDeadline, finished: performance.now(),
      no_route_fake_or_409_exception: true, no_additional_legacy_import: true};
    const tinyRoot = path.join(project.dataset_dir, 'u007-owned-boundary-input'); fs.mkdirSync(tinyRoot);
    tiny = path.join(tinyRoot, 'too-small-8x8.png'); make(tiny, 8, 8);
    originalInputs = {normal: filePin(normal), tiny: filePin(tiny)};
    const context = {project_id: project.id, source_dataset_path: source, labelset_id: project.active_labelset_id};
    const pipeline = {id: 'owned-u007-no-model-flow-' + tag, name: 'Owned U007 original-coordinate handoff ' + tag, nodes: [
      {id: 'owned-u007-input', position: {x: 20, y: 160}, data: {label: 'Owned input', node_type: 'input'}},
      {id: roiId, position: {x: 340, y: 160}, data: {label: 'Owned fixed ROI', node_type: 'fixed_roi', params: {roi_bbox: tag === 'A' ? [32, 16, 128, 96] : [24, 12, 96, 72]}}},
      {id: 'owned-u007-unbound-inspection', position: {x: 660, y: 160}, data: {label: 'Unbound inspection (never executed)', node_type: 'inspection', task: 'anomaly', model_job_id: null}},
      {id: 'owned-u007-decision', position: {x: 980, y: 160}, data: {label: 'Owned decision', node_type: 'decision'}},
      {id: 'owned-u007-output', position: {x: 1300, y: 160}, data: {label: 'Owned output', node_type: 'output'}},
    ], edges: [
      {id: 'owned-u007-edge-1', source: 'owned-u007-input', target: roiId, payload_type: 'image'},
      {id: 'owned-u007-edge-2', source: roiId, target: 'owned-u007-unbound-inspection', payload_type: 'roi'},
      {id: 'owned-u007-edge-3', source: 'owned-u007-unbound-inspection', target: 'owned-u007-decision', payload_type: 'result'},
      {id: 'owned-u007-edge-4', source: 'owned-u007-decision', target: 'owned-u007-output', payload_type: 'result'},
    ]};
    const seed = await api('/api/flowchart/draft', {pipeline, context, base_version_id: 'none'}, 'PUT');
    draftFile = path.join(project.project_dir, 'flowcharts', 'drafts', project.active_labelset_id,
      sha(source).slice(0, 16), 'draft.json');

    assertDraftRecord(seed, seed.pipeline, context);
    for (const node of seed.pipeline.nodes) expect(node.data.model_job_id).toBeNull();
    await api('/api/project/labelsets'); await api('/api/team-data'); await api('/api/team-data/readiness');
    await api('/api/dataset/metadata?limit=100'); await api('/api/annotations/part?file_path=' + encodeURIComponent(normal));
    return {tag, project, source, normal, tiny, dimensions, context, seed, saved: seed, roiId, draftFile, originalInputs};
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
  const custody = (scope: Scope) => ({source: scopedTree(scope.source), dataset: scopedTree(scope.project.dataset_dir),
    annotations: scopedTree(scope.project.annotations_dir), models: scopedTree(scope.project.models_dir), reports: scopedTree(scope.project.reports_dir),
    labelsets: scopedTree(path.join(scope.project.project_dir, 'labelsets')), flowcharts: scopedTree(path.join(scope.project.project_dir, 'flowcharts')),
    project: filePin(path.join(scope.project.project_dir, 'project.json')), labelset_registry: filePin(path.join(scope.project.project_dir, 'labelsets.json')),
    normal: filePin(scope.normal), tiny: filePin(scope.tiny), draft: filePin(scope.draftFile)});
  const both = () => ({A: custody(A!), B: custody(B!), harness_dataset: scopedTree(w.dataset)});
  const readback = async (scope: Scope) => {
    const value = {project: await api('/api/project/current'), draft: await api('/api/flowchart/draft'),
      revisions: await api('/api/dataset/revisions'), labelsets: await api('/api/project/labelsets'),
      metadata: await api('/api/dataset/metadata?limit=100'), active: await api('/api/flowchart/pipeline/active-version'),
      versions: await api('/api/flowchart/pipelines?source_dataset_path=' + encodeURIComponent(scope.source)),
      annotation: await api('/api/annotations/part?file_path=' + encodeURIComponent(scope.normal))};
    expect(value.project).toEqual(scope.project); expect(value.draft).toEqual(scope.saved);
    assertDraftRecord(value.draft, scope.saved.pipeline, scope.context);
    expect(value.active).toEqual({version_id: null}); expect(value.versions).toEqual({pipelines: [], total: 0});
    expect(value.revisions.active_revision).toMatch(/^[a-f0-9]{32}$/);
    expect(JSON.parse(fs.readFileSync(scope.draftFile, 'utf8'))).toEqual(Object.fromEntries(Object.entries(scope.saved).filter(([key]) => key !== 'active_version_id')));
    return value;
  };
  const rectIs = async (box: number[], dimensions: [number, number]) => {
    await expect(roi()).toHaveAttribute('viewBox', `0 0 ${dimensions[0]} ${dimensions[1]}`);
    for (const [key, value] of Object.entries({x: String(box[0]), y: String(box[1]), width: String(box[2] - box[0]), height: String(box[3] - box[1])}))
      await expect(roi().locator('rect')).toHaveAttribute(key, value);
    await expect(page.getByText(`원본 기준 [${box.join(', ')}]`, {exact: false})).toBeVisible();
  };
  const enter = async (scope: Scope, first: number, requireImageReset: boolean) => {
    roiId = scope.roiId;
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(scope.project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click();
    await expect(page.locator('[data-flow-node-id]')).toHaveCount(5); await showNode(); await settle();
    await expect(page.getByRole('textbox', {name: '노드 명칭', exact: true})).toHaveValue('Owned fixed ROI');
    const drafts = wires.slice(first).filter(row => row.request.method() === 'GET' && new URL(row.request.url()).pathname === '/api/flowchart/draft');
    expect(drafts.length).toBeGreaterThan(0);
    for (const row of drafts) {ownWire(row, scope, 'GET', '/api/flowchart/draft'); expect(JSON.parse(fs.readFileSync(row.body!, 'utf8'))).toEqual(scope.saved);}
    if (requireImageReset) {
      await expect(roi()).toHaveCount(0);
      await expect(page.getByText('검사 이미지가 선택되지 않았습니다.', {exact: true})).toBeVisible();
      await expect(page.getByText('상단에서 검사 이미지를 선택하면 원본 위에 ROI를 그릴 수 있습니다.', {exact: true})).toBeVisible();
      await expect(page.getByRole('img', {name: 'Thumbnail', exact: true})).toHaveCount(0);
    }
  };
  type Pixels = {width: number; height: number; rgba_sha256: string};
  const originalPreviews = new Map<string, {href: string; input: ReturnType<typeof filePin>; context: any;
    wire: Wire; body: ReturnType<typeof filePin>; pixels: Pixels}>();
  const selection = async (scope: Scope) => {
    const first = wires.length;
    const deadline = await choose(scope.normal, scope.dimensions);
    const information = wires.slice(first).filter(row => new URL(row.request.url()).pathname === '/api/flow-workspace/image-info');
    expect(information.length).toBeGreaterThan(0);
    for (const row of information) {
      expect(new URL(row.request.url()).searchParams.get('image_path')).toBe(scope.normal);
      ownWire(row, scope, 'GET', '/api/flow-workspace/image-info');
    }
    const href = await within(roi().locator('image').getAttribute('href'), deadline, 'same selected ROI image URI'); expect(href).not.toBeNull();
    expect(new URL(href!, renderer.origin).searchParams.get('file_path')).toBe(scope.normal);
    const thumbnails = wires.slice(first).filter(row => new URL(row.request.url()).pathname.startsWith('/api/dataset/thumbnail/')
      && new URL(row.request.url()).searchParams.get('file_path') === scope.normal);
    for (const row of thumbnails) {
      expect(row.request.method()).toBe('GET'); expect(row.status).toBe(200); expect(row.finished).toBeLessThanOrEqual(row.deadline);
      expect(row.body).toBeDefined(); expect(row.sha256).toMatch(/^[a-f0-9]{64}$/); expect(row.request.frame()).toBe(page.mainFrame());
    }
    const freshPreview = thumbnails.find(row => row.request.url() === href), previous = originalPreviews.get(scope.normal);
    expect(freshPreview || previous, 'The exact URI needs a genuine original response, never an invented fresh request').toBeDefined();
    const original = freshPreview || previous!.wire, response = responseObjects.get(original.request); expect(response).toBeDefined();
    const context = JSON.parse(response!.headers()['x-vision-context']); expect(context).toEqual(scope.ownerContext);
    expect(original.request.url()).toBe(href); expect(original.status).toBe(200); expect(original.failure).toBeUndefined();
    const input = filePin(scope.normal), body = filePin(original.body!);
    expect(body.size).toBe(original.size); expect(body.sha256).toBe(original.sha256); expect(body.size).toBeLessThanOrEqual(2 * 1024 * 1024);
    const mime = response!.headers()['content-type']; expect(mime).toBe('image/jpeg');
    const decoded = await within(page.evaluate(async ({href, bytes, mime, dimensions}) => {
      const images = [...document.querySelectorAll('img')].filter(node => node.src === href
        && node.getClientRects().length > 0 && node.complete && node.naturalWidth > 0);
      if (images.length !== 1) throw Error('Expected one visible decoded original preview with the exact URI');
      const image = images[0];
      const pixels = async (source: CanvasImageSource, width: number, height: number) => {
        if (width < 1 || height < 1 || width > dimensions[0] || height > dimensions[1]) throw Error('Preview dimensions exceed the original input');
        // Detached canvases/Blob decoding only: no application DOM mutation,
        // network fetch, cache setting or synthetic application callback.
        const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d'); if (!ctx) throw Error('Original preview pixel readback unavailable');
        ctx.drawImage(source, 0, 0); const rgba = ctx.getImageData(0, 0, width, height).data;
        const digest = await globalThis.crypto.subtle.digest('SHA-256', new Uint8Array(rgba));
        return {width, height, rgba_sha256: [...new Uint8Array(digest)].map(value => value.toString(16).padStart(2, '0')).join('')};
      };
      const visible = await pixels(image, image.naturalWidth, image.naturalHeight);
      if (!image.isConnected || !image.complete || image.src !== href) throw Error('Visible preview changed during readback');
      const originalImage = new Image(), originalURL = URL.createObjectURL(new Blob([new Uint8Array(bytes)], {type: mime}));
      try {
        originalImage.src = originalURL; await originalImage.decode();
        if (!originalImage.complete || originalImage.isConnected) throw Error('Expected a detached decoded original JPEG');
        return {visible, retained_body: await pixels(originalImage, originalImage.naturalWidth, originalImage.naturalHeight)};
      } finally {originalImage.removeAttribute('src'); URL.revokeObjectURL(originalURL);}
    }, {href: href!, bytes: [...fs.readFileSync(original.body!)], mime, dimensions: scope.dimensions}), deadline, 'original decoded preview and retained JPEG pixels');
    expect(decoded.visible).toEqual(decoded.retained_body); expect(filePin(original.body!)).toEqual(body);
    if (previous) {
      expect(href).toBe(previous.href); expect(input).toEqual(previous.input); expect(context).toEqual(previous.context);
      expect(filePin(previous.wire.body!)).toEqual(previous.body); expect(decoded.visible).toEqual(previous.pixels);
    } else {
      expect(freshPreview, 'The first owning selection requires an actual original preview response').toBeDefined();
      originalPreviews.set(scope.normal, {href: href!, input, context, wire: original, body, pixels: decoded.visible});
    }
    expect(performance.now()).toBeLessThanOrEqual(deadline);
    return {project_id: scope.project.id, file: scope.normal, input,
      image_info: information.map(row => ownWire(row, scope, 'GET', '/api/flow-workspace/image-info')),
      thumbnails: thumbnails.map(({request, done, resolve, ...row}) => ({...row, url: request.url(), method: request.method()})), href,
      thumbnail_witness: {branch: freshPreview ? 'actual_original_HTTP_response' : 'retained_decoded_resource',
        fresh_preview_HTTP: Boolean(freshPreview), original_URL: original.request.url(), original_body: body,
        original_response_context: context, decoded_visible: decoded.visible, decoded_original_body: decoded.retained_body,
        first_owning_pixels: originalPreviews.get(scope.normal)!.pixels, cache_implementation_claimed: false}};
  };
  const screenshot = async (name: string, scope: Scope, box: number[]) => {
    // A fresh visual frame begins only after original HTTP bodies have joined.
    await settle(); const deadline = performance.now() + 10_000;
    await within(roi().scrollIntoViewIfNeeded(), deadline, 'whole owning ROI screenshot');
    await within(expect(roi()).toBeVisible(), deadline, 'visible original ROI');
    await within(expect(roi()).toBeInViewport({ratio: 1}), deadline, 'whole original ROI in viewport');
    const SVG_bbox = await within(roi().boundingBox(), deadline, 'original SVG bounds');
    await within(e.screenshot(page, name + '-ROI'), deadline, 'own ROI screenshot');
    const summary = page.getByText(`원본 기준 [${box.join(', ')}]`, {exact: false});
    // The original inspector scrolls internally: retain distinct visible frames
    // within this same deadline, without claiming simultaneous co-visibility.
    await within(summary.scrollIntoViewIfNeeded(), deadline, 'own coordinate summary scroll');
    await within(expect(summary).toBeVisible(), deadline, 'own coordinate summary');
    await within(expect(summary).toBeInViewport({ratio: 1}), deadline, 'own summary in viewport');
    proof[name + '_visual'] = {project_id: scope.project.id, original_rect: box,
      distinct_scroll_frames: true, simultaneous_visibility_claimed: false,
      ROI_screenshot: name + '-ROI', summary_screenshot: name + '-coordinate-summary', SVG_bbox,
      summary_bbox: await within(summary.boundingBox(), deadline, 'coordinate summary bounds')};
    await within(e.screenshot(page, name + '-coordinate-summary'), deadline, 'own coordinate summary screenshot');
  };
  const transitions: unknown[] = [], selections: unknown[] = [], snapshots: unknown[] = [];
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
    return first;
  };
  try {
    A = await makeScope('A'); B = await makeScope('B');
    expect(A.project.id).not.toBe(B.project.id); expect(A.source).not.toBe(B.source);
    expect(A.seed.draft_sha256).not.toBe(B.seed.draft_sha256); expect(filePin(A.normal).sha256).not.toBe(filePin(B.normal).sha256);
    // As in the original two-project donor, visit both real UI scopes before
    // custody starts so first-mount reads are part of declared setup.
    await installDesktopHostShim(page, renderer.port); const initial = wires.length; await page.goto(renderer.url); await enter(B, initial, true);
    phase = 'setup_B_own_image'; const firstBSelection = await selection(B); selections.push(firstBSelection); await rectIs([24, 12, 96, 72], B.dimensions);
    B.readback = await readback(B); await settle();
    phase = 'setup_B_to_A'; await projectSwitch(A);
    phase = 'A_actual_original_drag_and_keys'; const firstASelection = await selection(A); selections.push(firstASelection);
    expect(firstASelection.thumbnail_witness.decoded_visible.rgba_sha256).not.toBe(firstBSelection.thumbnail_witness.decoded_visible.rgba_sha256);
    await expect(roi()).toHaveAttribute('viewBox', '0 0 512 256');
    await roi().scrollIntoViewIfNeeded();
    const points = await roi().evaluate(element => {
      const matrix = (element as SVGSVGElement).getScreenCTM(); if (!matrix) throw Error('Original ROI has no screen transform');
      return [[64.25, 32.25], [256.25, 160.25]].map(([x, y]) => {const point = new DOMPoint(x, y).matrixTransform(matrix); return {x: point.x, y: point.y};});
    });
    await page.mouse.move(points[0].x, points[0].y); await page.mouse.down();
    await page.mouse.move(points[1].x, points[1].y, {steps: 4}); await page.mouse.up();
    const rect = roi().locator('rect');
    for (const [key, value] of Object.entries({x: '64', y: '32', width: '193', height: '129'})) await expect(rect).toHaveAttribute(key, value);
    await observeOriginalKeys(); await roi().focus(); await expect(roi()).toBeFocused();
    await deliveredKey('positive_original_ArrowRight', 'ArrowRight', true);
    await expect(rect).toHaveAttribute('x', '65'); await expect(rect).toHaveAttribute('width', '193');
    await deliveredKey('positive_original_ShiftArrowDown', 'Shift+ArrowDown', true);
    for (const [key, value] of Object.entries({x: '65', y: '32', width: '193', height: '130'})) await expect(rect).toHaveAttribute(key, value);

    const expectedPipeline = clone(A.seed.pipeline);
    expectedPipeline.nodes.find((node: any) => node.id === roiId).data.params.roi_bbox = wanted;
    const save = page.getByRole('button', {name: '초안 저장', exact: true});
    const savedStatus = page.getByRole('status').filter({hasText: /^초안 저장됨 · 실행본 활성화 전$/});
    // Do not assume a fixed count for genuine650ms autosaves between drag/keys.
    // Join the settled wanted graph before starting a distinct explicit save.
    await expect.poll(async () => (await api('/api/flowchart/draft')).pipeline, {timeout: 10_000}).toEqual(expectedPipeline);
    await expect(save).toBeEnabled(); await expect(savedStatus).toHaveCount(1); await settle();
    const automatic = wires.filter(row => row.request.method() === 'PUT' && new URL(row.request.url()).pathname === '/api/flowchart/draft');
    expect(automatic.some(row => JSON.stringify(canonical(row.request.postDataJSON().pipeline)) === JSON.stringify(canonical(expectedPipeline)))).toBe(true);
    const deadline = performance.now() + 10_000, firstExplicit = wires.length;
    const waiting = page.waitForResponse(response => response.request().method() === 'PUT' && response.url() === renderer.origin + '/api/flowchart/draft'
      && response.request().frame() === page.mainFrame(), {timeout: 10_000});
    await within(save.click(), deadline, 'own explicit draft button');
    const explicit = await within(waiting, deadline, 'own explicit draft response'); await settle();
    const joined = byRequest.get(explicit.request()); expect(joined).toBeDefined(); expect(wires.indexOf(joined!)).toBeGreaterThanOrEqual(firstExplicit);
    expect(explicit.request().postDataJSON()).toEqual({pipeline: expectedPipeline, context: A.context, base_version_id: 'none'});
    ownWire(joined!, A, 'PUT', '/api/flowchart/draft');
    A.saved = JSON.parse(fs.readFileSync(joined!.body!, 'utf8')); assertDraftRecord(A.saved, expectedPipeline, A.context);
    expect(JSON.parse(fs.readFileSync(A.draftFile, 'utf8'))).toEqual(Object.fromEntries(Object.entries(A.saved).filter(([key]) => key !== 'active_version_id')));
    for (const row of automatic) {ownWire(row, A, 'PUT', '/api/flowchart/draft'); expect(row.request.postDataJSON().context).toEqual(A.context);}
    await expect(save).toBeEnabled(); await expect(savedStatus).toHaveCount(1); await rectIs(wanted, A.dimensions);
    A.readback = await readback(A); await settle();
    e.addFile(A.normal); e.addFile(A.tiny); e.addFile(A.draftFile); e.addFile(B.normal); e.addFile(B.tiny); e.addFile(B.draftFile);
    await screenshot('u007-A-real-drag-keys-and-explicit-save', A, wanted);
    baseline = both(); const writeBoundary = writes.length;
    proof.actual_drag = {DOM_CTM_points: points, rect_before_keys: [64, 32, 257, 161], actual_mouse: true};
    proof.actual_keys = keyProofs; proof.explicit_saved_draft = {record: A.saved, wire: ownWire(joined!, A, 'PUT', '/api/flowchart/draft'), disk: filePin(A.draftFile)};
    snapshots.push({phase: 'A-before', custody: baseline, readback: A.readback});
    phase = 'handoff_A_to_B'; await projectSwitch(B);
    expect(await page.evaluate(() => (window as any).__u007InvalidROIKeys.originalEditor.isConnected)).toBe(false);
    selections.push(await selection(B)); const bBox = [24, 12, 96, 72]; await rectIs(bBox, B.dimensions);
    expect(await readback(B)).toEqual(B.readback); await settle(); expect(both()).toEqual(baseline);
    await screenshot('u007-B-own-image-and-original-ROI-after-A', B, bBox); snapshots.push({phase, custody: both()});
    phase = 'handoff_B_to_A'; await projectSwitch(A);
    const returnedASelection = await selection(A); selections.push(returnedASelection); await rectIs(wanted, A.dimensions);
    expect(returnedASelection.input).toEqual(firstASelection.input);
    expect(returnedASelection.thumbnail_witness.original_body.sha256).toBe(firstASelection.thumbnail_witness.original_body.sha256);
    expect(returnedASelection.thumbnail_witness.decoded_visible).toEqual(firstASelection.thumbnail_witness.decoded_visible);
    expect(await readback(A)).toEqual(A.readback); await settle(); expect(both()).toEqual(baseline);
    await screenshot('u007-A-return-own-image-exact-65-32-258-162', A, wanted); snapshots.push({phase, custody: both()});
    expect(writes.slice(writeBoundary)).toEqual([
      {phase: 'handoff_A_to_B', method: 'POST', route: '/api/project/open', body: {project_dir: B.project.project_dir}},
      {phase: 'handoff_B_to_A', method: 'POST', route: '/api/project/open', body: {project_dir: A.project.project_dir}},
    ]);
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(failedRequests).toEqual([]); expect(foreignRequests).toEqual([]);
    proof.post_baseline_writes = writes.slice(writeBoundary);
    proof.both_causal_phases_and_persisted_handoff_truth_passed = true;
  } catch (error) {primary = error; throw error;} finally {
    const secondary: string[] = [];
    try {await settle();} catch (error) {secondary.push(String(error));}
    try {expect(sourcePins()).toEqual(pinsBefore); expect(harness.sourceIdentity()).toEqual(identity);} catch (error) {secondary.push(String(error));}
    if (baseline && A && B) {
      try {expect(both()).toEqual(baseline);} catch (error) {secondary.push(String(error));}
    }
    for (const scope of [A, B]) if (scope) {
      try {expect({normal: filePin(scope.normal), tiny: filePin(scope.tiny)}).toEqual(scope.originalInputs);
        expect(fs.existsSync(path.join(scope.project.project_dir, 'flowcharts', 'active.json'))).toBe(false);} catch (error) {secondary.push(String(error));}
    }
    page.off('request', onRequest); page.off('response', onResponse); page.off('response', ownerResponse); page.off('requestfailed', onFailed);
    page.off('requestfinished', onFinished); page.off('pageerror', onPageError); page.off('console', onConsole);
    e.note('U007_roi_edit_project_handoff', {...proof, projects: [A?.project, B?.project], selections, transitions, snapshots,
      protected_roots_scope: 'Both original source/dataset/annotations/models/reports/labelsets/flowcharts trees plus project and labelset registries, normal/tiny inputs, drafts and harness dataset. Unrelated lifecycle SQLite/global project pointer namespace is not claimed immutable.',
      actual_image_selection_is_transient: true, no_per_project_layout_or_model_claim: true, no_API_route_fake: true,
      writes, api_calls: apiCalls, original_browser_wires: wires.map(({request, done, resolve, ended, end, ...row}) => ({...row,
        method: request.method(), url: request.url(), request_body: request.postDataJSON()})),
      retired_gallery_thumbnail_reads: retiredThumbnails.map(row => ({phase: row.phase, method: row.request.method(), resource_type: row.request.resourceType(),
        path: new URL(row.request.url()).pathname, file_path: row.retired!.file_path, project: row.retired!.project, failure: row.retired!.failure,
        headers_status: row.retired!.headers_status, started: row.started, retired_at: row.retired!.at, deadline: row.deadline})),
      set_aside_only_aborted_main_frame_gallery_thumbnail_fetches_of_A_or_B: true,
      page_errors: pageErrors, console_errors: consoleErrors, failed_requests: failedRequests, foreign_requests: foreignRequests,
      primary: primary === undefined ? null : String(primary), secondary_errors: secondary});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary});
    else if (primary === undefined && secondary.length) throw Error(secondary.join('; '));
  }
});

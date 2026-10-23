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
  done: Promise<void>; resolve(): void;
  // Gallery thumbnail reads only: the browser's own end event, and the retirement that event proved.
  ended?: Promise<'failed' | 'finished'>; end?(kind: 'failed' | 'finished'): void;
  retired?: {at: number; failure: string; project: string | null; file_path: string | null; headers_status: number | null}};

test('U007 trusted ROI keys after a real 8x8 input unmount preserve the saved original-coordinate draft', async ({page, renderer, workspace: w, evidence: e}) => {
  const identity = harness.sourceIdentity(); expect(identity.dirty, 'Root must checkpoint the reviewed spec before execution').toBe(false);
  const dependencies = ['src/renderer/components/flowchart/ImageRoiEditor.tsx',
    'src/renderer/components/flowchart/FlowchartStudio.tsx', 'src/renderer/components/flowchart/FlowDraftControls.tsx',
    'src/renderer/components/common/ImagePickerModal.tsx', 'src/renderer/stores/useFlowchartStore.ts',
    'src/renderer/stores/useDatasetStore.ts', 'src/renderer/services/api.ts', 'src/renderer/services/flowDraft.ts',
    'backend/api/routes_flow_workspace.py', 'backend/api/routes_flowchart.py', 'backend/api/routes_dataset.py',
    'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs', 'scripts/e2e/fixtures/desktop-host-shim.ts',
    'scripts/e2e/fixtures/u007-response-join.cjs', 'scripts/tests/u007-response-join.test.cjs',
    'scripts/e2e/roi-keyboard-small-image.spec.ts', 'scripts/e2e/roi-too-small-image.spec.ts', 'playwright.config.ts'];
  const sourcePins = () => Object.fromEntries(dependencies.map(file => [file, filePin(path.join(harness.REPO_ROOT, file))]));
  const pinsBefore = sourcePins(), wires: Wire[] = [], byRequest = new Map<Request, Wire>(), apiCalls: unknown[] = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], failedRequests: unknown[] = [], foreignRequests: string[] = [];
  const retiredThumbnails: Wire[] = [];
  const galleryThumbnail = (request: Request) => request.method() === 'GET' && request.frame() === page.mainFrame() && request.resourceType() === 'fetch'
    && new URL(request.url()).origin === renderer.origin && new URL(request.url()).pathname.startsWith('/api/dataset/thumbnail/');
  const writes: Array<{phase: string; method: string; route: string; body: unknown}> = [];
  let phase = 'setup', sequence = 0, project: any, primary: unknown, baseline: any, afterInvalid: any, afterReopen: any;
  let source = '', normal = '', tiny = '', draftFile = '', originalInputs: unknown;
  const roiId = 'owned-u007-fixed-roi', wanted = [65, 32, 258, 162];
  const proof: Record<string, unknown> = {cell: 'U007.native-original-roi-keyboard-resize.invalid',
    source: identity, source_pins_before: pinsBefore, actual_browser: true, installed_native: false,
    training_or_inference: false, cache_branch_coverage: false, drag_invalid_credit: false, parent_acceptance: false};
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
    expect(retired.project).toBe(project.id); expect(retired.file_path).toBe(normal);
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
    project = await api('/api/project/create', {name: 'Owned U007 invalid ROI keyboard context', task: 'anomaly'});
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
    phase = 'positive_original_drag'; await choose(normal, [512, 256]);
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
    await page.getByRole('button', {name: '초안 저장', exact: true}).click();
    await expect.poll(async () => (await api('/api/flowchart/draft')).pipeline.nodes.find((node: any) => node.id === roiId)?.data.params.roi_bbox,
      {timeout: 10_000}).toEqual(wanted);
    await expect(page.getByRole('button', {name: '초안 저장', exact: true})).toBeEnabled();
    await expect(page.getByRole('status').filter({hasText: /^초안 저장됨 · 실행본 활성화 전$/})).toHaveCount(1);
    await settle();
    const saved = await api('/api/flowchart/draft'); expect(saved.pipeline.nodes.find((node: any) => node.id === roiId).data.params.roi_bbox).toEqual(wanted);
    e.addFile(normal); e.addFile(tiny); e.addFile(draftFile);
    await e.screenshot(page, 'u007-keyboard-positive-original-keys-saved');
    baseline = protectedState(); proof.saved_draft = saved;
    phase = 'invalid_too_small'; const writeBoundary = writes.length;
    await choose(tiny, [8, 8]);
    await expect(page.getByRole('alert').filter({hasText: '원본 8×8: ROI 편집에는 최소 16×16 픽셀 이미지가 필요합니다.'})).toHaveCount(1);
    await expect(roi()).toHaveCount(0); await expect(page.getByText('원본 기준 [65, 32, 258, 162]', {exact: false})).toBeVisible();
    // Original modal cleanup restores the live opener. Do not focus a
    // detached node or synthesize a key against an absent editor.
    await expect(page.getByRole('button', {name: '이미지 변경...', exact: true})).toBeFocused();
    expect(await page.evaluate(() => (window as any).__u007InvalidROIKeys.originalEditor.isConnected)).toBe(false);
    for (const [label, key] of [['invalid_unmounted_ArrowRight', 'ArrowRight'], ['invalid_unmounted_ShiftArrowDown', 'Shift+ArrowDown']] as const) {
      await deliveredKey(label, key, false); await settle();
      expect(writes.slice(writeBoundary)).toEqual([]); expect(await api('/api/flowchart/draft')).toEqual(saved);
      expect(protectedState()).toEqual(baseline); await expect(roi()).toHaveCount(0);
      await expect(page.getByText('원본 기준 [65, 32, 258, 162]', {exact: false})).toBeVisible();
    }
    await settle(); expect(writes.slice(writeBoundary)).toEqual([]);
    expect(await api('/api/flowchart/draft')).toEqual(saved); afterInvalid = protectedState(); expect(afterInvalid).toEqual(baseline);
    await e.screenshot(page, 'u007-keyboard-unmounted-editor-trusted-keys-no-write');
    phase = 'normal_reopen';
    await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(4).click();
    await page.getByRole('tab', {name: '편집', exact: true}).click(); await showNode(); await choose(normal, [512, 256]);
    await expect(roi()).toHaveAttribute('viewBox', '0 0 512 256');
    for (const [key, value] of Object.entries({x: '65', y: '32', width: '193', height: '130'})) await expect(roi().locator('rect')).toHaveAttribute(key, value);
    await settle(); expect(writes.slice(writeBoundary)).toEqual([]); expect(await api('/api/flowchart/draft')).toEqual(saved);
    afterReopen = protectedState(); expect(afterReopen).toEqual(baseline);
    expect({normal: filePin(normal), tiny: filePin(tiny)}).toEqual(originalInputs);
    await e.screenshot(page, 'u007-keyboard-normal-source-reopened-with-exact-saved-roi');
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(failedRequests).toEqual([]); expect(foreignRequests).toEqual([]);
    expect(fs.existsSync(path.join(project.project_dir, 'flowcharts', 'active.json'))).toBe(false);
  } catch (error) {primary = error; throw error;} finally {
    const secondary: string[] = [];
    try {await settle();} catch (error) {secondary.push(String(error));}
    try {expect(sourcePins()).toEqual(pinsBefore); expect(harness.sourceIdentity()).toEqual(identity);} catch (error) {secondary.push(String(error));}
    page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed);
    page.off('requestfinished', onFinished); page.off('pageerror', onPageError); page.off('console', onConsole);
    e.note('U007_keyboard_invalid_small_image', {...proof, project, normal, tiny, original_inputs: originalInputs, original_key_events: keyProofs,
      boundary_scope: 'Trusted keys delivered after a real invalid-size source unmount must not mutate the prior ROI; no key is synthetically dispatched to the detached SVG, and no other scenario or whole-flow acceptance is claimed',
      protected_roots_scope: 'source, tiny input, draft, flowcharts, annotations, models, reports, project.json, labelsets.json; runtime SQLite/home outside these roots are not claimed immutable',
      before: baseline, after_invalid: afterInvalid, after_normal_reopen: afterReopen, writes, api_calls: apiCalls,
      original_browser_wires: wires.map(({request, done, resolve, ended, end, ...row}) => ({...row, method: request.method(), url: request.url(), request_body: request.postDataJSON()})),
      retired_gallery_thumbnail_reads: retiredThumbnails.map(row => ({phase: row.phase, method: row.request.method(), resource_type: row.request.resourceType(),
        path: new URL(row.request.url()).pathname, file_path: row.retired!.file_path, project: row.retired!.project, failure: row.retired!.failure,
        headers_status: row.retired!.headers_status, started: row.started, retired_at: row.retired!.at, deadline: row.deadline})),
      set_aside_only_aborted_main_frame_gallery_thumbnail_fetches_of_own_image: true,
      page_errors: pageErrors, console_errors: consoleErrors, failed_requests: failedRequests, foreign_requests: foreignRequests,
      primary: primary === undefined ? null : String(primary), secondary_errors: secondary});
    if (primary instanceof Error && secondary.length) Object.defineProperty(primary, 'secondary_errors', {value: secondary});
    else if (primary === undefined && secondary.length) throw Error(secondary.join('; '));
  }
});

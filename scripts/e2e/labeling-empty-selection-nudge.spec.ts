import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

const harness = require('./fixtures/harness.cjs');
test.use({actionTimeout: 10_000});
const sha = (raw: Buffer | string) => crypto.createHash('sha256').update(raw).digest('hex');
const raw9 = (s: fs.BigIntStats) => [s.dev, s.ino, s.mode, s.nlink, s.uid, s.gid, s.size, s.mtimeNs, s.ctimeNs].map(String);
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

async function within<T>(pending: Promise<T>, deadline: number, label: string): Promise<T> {
  const remaining = deadline - performance.now();
  if (remaining <= 0) throw Error('Original 10s action/body budget expired: ' + label);
  let timer: ReturnType<typeof setTimeout> | undefined;
  return Promise.race([pending, new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(Error('Original 10s action/body budget expired: ' + label)), remaining);
  })]).finally(() => { if (timer) clearTimeout(timer); });
}

function filePin(file: string) {
  const named = fs.lstatSync(file, {bigint: true});
  expect(named.isFile()).toBe(true); expect(named.isSymbolicLink()).toBe(false); expect(named.nlink).toBe(1n);
  const fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  try {
    const opened = fs.fstatSync(fd, {bigint: true}); expect(raw9(opened)).toEqual(raw9(named));
    const bytes = fs.readFileSync(fd); expect(BigInt(bytes.length)).toBe(opened.size);
    expect(raw9(fs.fstatSync(fd, {bigint: true}))).toEqual(raw9(opened));
    expect(raw9(fs.lstatSync(file, {bigint: true}))).toEqual(raw9(opened));
    return {raw9: raw9(opened), size: bytes.length, sha256: sha(bytes)};
  } finally { fs.closeSync(fd); }
}

function treePin(root: string) {
  expect(fs.realpathSync(root)).toBe(root);
  const rows: Record<string, unknown> = {};
  const visit = (at: string) => {
    const named = fs.lstatSync(at, {bigint: true}), relative = path.relative(root, at) || '.';
    expect(named.isSymbolicLink()).toBe(false);
    if (!named.isDirectory()) { rows[relative] = {kind: 'file', ...filePin(at)}; return; }
    const names = fs.readdirSync(at).sort();
    rows[relative] = {kind: 'directory', raw9: raw9(named), names};
    for (const name of names) visit(path.join(at, name));
    expect(fs.readdirSync(at).sort()).toEqual(names);
    expect(raw9(fs.lstatSync(at, {bigint: true}))).toEqual(raw9(named));
  };
  visit(root); return rows;
}

type Wire = {
  request: Request; started: number; deadline: number; finished?: number;
  status?: number; file?: string; sha256?: string; size?: number; failure?: string;
  done: Promise<void>; resolve(): void;
};

test('U020 empty selection ignores a delivered coarse nudge after an owning OBB control', async ({page, renderer, workspace: w, evidence: e}) => {
  test.setTimeout(180_000);
  const sourceIdentity = harness.sourceIdentity();
  expect(sourceIdentity.dirty, 'Execute only after Root checkpoints this new case').toBe(false);
  const sourceFiles = [
    'src/renderer/components/labeling/LabelingCanvas.tsx',
    'src/renderer/components/labeling/AnnotationList.tsx',
    'src/renderer/components/labeling/LabelingToolbar.tsx',
    'src/renderer/stores/useAnnotationStore.ts', 'src/renderer/services/api.ts',
    'scripts/e2e/fixtures/test.ts', 'scripts/e2e/fixtures/harness.cjs',
    'scripts/e2e/fixtures/desktop-host-shim.ts', 'scripts/e2e/qa/appFlow.ts',
    'scripts/e2e/labeling-empty-selection-nudge.spec.ts', 'playwright.config.ts',
  ];
  const sourcePins = () => Object.fromEntries(sourceFiles.map(file => [file, filePin(path.join(harness.REPO_ROOT, file))]));
  const originalSource = sourcePins(), wires: Wire[] = [], byRequest = new Map<Request, Wire>();
  const writes: Array<{method: string; route: string; body: unknown}> = [];
  const pageErrors: string[] = [], consoleErrors: string[] = [], requestFailures: unknown[] = [], foreignRequests: string[] = [], apiCalls: unknown[] = [];
  const proof: Record<string, unknown> = {cell: 'U020.independent-direction-nudge.empty', source: sourceIdentity,
    source_pins_before: originalSource, actual_browser: true, installed_native: false,
    model_training_or_inference: false, annotation_quality_approval: false, parent_acceptance: false};
  let sequence = 0, writeBoundary: number | undefined;
  const retain = (label: string, bytes: Buffer) => {
    const file = path.join(w.logs, `empty-nudge-${++sequence}-${label}.body`);
    fs.writeFileSync(file, bytes, {flag: 'wx', mode: 0o600}); e.addFile(file);
    return {file, size: bytes.length, sha256: sha(bytes)};
  };
  const onRequest = (request: Request) => {
    const address = new URL(request.url());
    if (/^https?:$/.test(address.protocol) && address.origin !== renderer.origin) foreignRequests.push(request.url());
    if (address.origin !== renderer.origin || !address.pathname.startsWith('/api/') || request.method() === 'OPTIONS') return;
    let resolve!: () => void;
    const row: Wire = {request, started: performance.now(), deadline: 0, done: new Promise<void>(done => { resolve = done; }), resolve: () => resolve()};
    row.deadline = row.started + 10_000; wires.push(row); byRequest.set(request, row);
    if (!['GET', 'HEAD'].includes(request.method())) writes.push({method: request.method(), route: address.pathname, body: request.postDataJSON()});
  };
  const onResponse = (response: Response) => {
    const row = byRequest.get(response.request()); if (!row) return;
    row.status = response.status();
    void (async () => {
      try {
        const [bytes, terminal] = await within(Promise.all([response.body(), response.finished()]), row.deadline, 'original browser response');
        expect(terminal).toBeNull(); expect(bytes.length).toBeLessThanOrEqual(2 * 1024 * 1024);
        Object.assign(row, retain('browser-response', bytes), {finished: performance.now()});
        expect(row.finished!).toBeLessThanOrEqual(row.deadline); expect(row.status).toBeGreaterThanOrEqual(200); expect(row.status).toBeLessThan(300);
      } catch (error) { row.failure = String(error); } finally { row.resolve(); }
    })();
  };
  const onFailed = (request: Request) => {
    requestFailures.push({url: request.url(), method: request.method(), failure: request.failure()});
    const row = byRequest.get(request); if (row) { row.failure = request.failure()?.errorText || 'Original request failed'; row.resolve(); }
  };
  const onPageError = (error: Error) => pageErrors.push(error.message);
  const onConsole = (message: {type(): string; text(): string}) => { if (message.type() === 'error') consoleErrors.push(message.text()); };
  page.on('request', onRequest); page.on('response', onResponse); page.on('requestfailed', onFailed);
  page.on('pageerror', onPageError); page.on('console', onConsole);
  const settle = async () => {
    for (let index = 0; index < wires.length; index++) {
      const row = wires[index]; await within(row.done, row.deadline, 'original browser request completion');
      expect(row.failure).toBeUndefined(); expect(row.finished).toBeDefined();
    }
  };
  const read = async (route: string, body?: unknown, method?: string, headers?: Record<string, string>) => {
    const started = performance.now(), deadline = started + 10_000, actualMethod = method || (body === undefined ? 'GET' : 'POST');
    const response = await within(page.request.fetch(renderer.origin + route, {method: actualMethod, headers,
      ...(body === undefined ? {} : {data: body}), timeout: 10_000}), deadline, 'actual fixture request');
    const bytes = await within(response.body(), deadline, 'actual fixture complete body');
    const row = {route, method: actualMethod, request: body ?? null, headers: headers ?? null, status: response.status(),
      started, finished: performance.now(), deadline, ...retain('fixture-response', bytes)};
    apiCalls.push(row); expect(bytes.length).toBeLessThanOrEqual(2 * 1024 * 1024);
    expect(row.finished).toBeLessThanOrEqual(deadline); expect(response.status(), bytes.toString('utf8')).toBe(200);
    expect(response.url()).toBe(renderer.origin + route);
    return {bytes, body: JSON.parse(bytes.toString('utf8')), proof: row};
  };
  const delivered = async (phase: string, expectedPrevented: boolean) => {
    const deadline = performance.now() + 10_000;
    await within(page.keyboard.press('Shift+ArrowRight'), deadline, phase + ' real keyboard action');
    await within(page.waitForFunction(phase => {
      const rows = (window as any).__u020EmptyNudge.keys.filter((row: any) => row.phase === phase);
      return rows.length === 1 && rows[0].dispatch_completed && rows[0].two_animation_frames;
    }, phase, {timeout: 10_000}), deadline, phase + ' key delivery and completed dispatch');
    const row = await within(page.evaluate(phase => (window as any).__u020EmptyNudge.keys.find((row: any) => row.phase === phase), phase), deadline, phase + ' original key readback');
    expect(row).toMatchObject({phase, trusted: true, key: 'ArrowRight', code: 'ArrowRight', shift: true,
      editable_or_dialog: false, modal: false, focused_document: true, dispatch_completed: true,
      two_animation_frames: true, default_prevented_after_dispatch: expectedPrevented});
    // The product canvas has no tabIndex. A real canvas click yields BODY or
    // CANVAS as the keyboard target; no test focus/DOM/store override is used.
    expect(['BODY', 'CANVAS']).toContain(row.target_tag);
    expect(row.pointer).toMatchObject({phase, trusted: true, on_canvas: true, button: 0});
    expect(row.pointer.at).toBeLessThanOrEqual(row.at); return row;
  };
  try {
    const source = path.join(w.root, 'empty-nudge-original-source'); fs.mkdirSync(source);
    const image = path.join(source, 'part.png'), originalImage = png(256, 3, (x, y) => [x, y, 100]);
    fs.writeFileSync(image, originalImage, {flag: 'wx'});
    const project = (await read('/api/project/create', {name: 'Owned empty selection nudge', task: 'segmentation'})).body;
    expect(path.dirname(project.project_dir)).toBe(w.projects);
    await read('/api/project/update', {source_dataset_dir: source}, 'PUT');
    await read('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: true});
    await installDesktopHostShim(page, renderer.port); await page.goto(renderer.url);
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name, {timeout: 10_000});
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(1).click();
    await page.getByRole('button', {name: '집중 편집', exact: true}).click();
    await expect.poll(async () => Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1] || 0), {timeout: 10_000}).toBeGreaterThan(100);
    await page.getByTitle('100% Zoom (1:1)', {exact: true}).click();
    await expect(page.getByTestId('canvas-hud')).toContainText('100%', {timeout: 10_000});
    const canvas = page.locator('[data-canvas-container]');
    const point = async (x: number, y: number) => {
      const bounds = await canvas.boundingBox(); expect(bounds).not.toBeNull();
      return {x: bounds!.x + (bounds!.width - 256) / 2 + x, y: bounds!.y + (bounds!.height - 256) / 2 + y};
    };
    await page.getByRole('button', {name: /^Crack(?: \d+)?$/}).click();
    await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)', {exact: true}).click();
    const start = await point(70, 150), end = await point(150, 190);
    await page.mouse.move(start.x, start.y); await page.mouse.down(); await page.mouse.move(end.x, end.y, {steps: 8}); await page.mouse.up();
    const direction = page.getByLabel('객체 독립 방향 라벨', {exact: true});
    const angle = page.getByText('Angle (θ)', {exact: true}).locator('..').locator('..').getByRole('spinbutton');
    await direction.fill('315'); await angle.fill('30');
    const saveButton = page.getByTestId('annotation-save-button'), annotationRoute = '/api/annotations/part?file_path=' + encodeURIComponent(image);
    let headers: Record<string, string> = {};
    const save = async () => {
      const deadline = performance.now() + 10_000;
      const own = (response: Response, route: string) => response.request().frame() === page.mainFrame() &&
        new URL(response.url()).origin === renderer.origin && new URL(response.url()).pathname === route && response.request().method() === 'POST';
      const saved = page.waitForResponse(response => own(response, '/api/annotations/save'), {timeout: 10_000});
      const imported = page.waitForResponse(response => own(response, '/api/dataset/import'), {timeout: 10_000});
      const [reply, refresh] = await within(Promise.all([saved, imported, saveButton.click()]), deadline, 'real save and original manifest refresh');
      for (const response of [reply, refresh]) {
        const row = byRequest.get(response.request()); expect(row).toBeDefined();
        await within(row!.done, Math.min(deadline, row!.deadline), 'save and refresh original bodies'); expect(row!.failure).toBeUndefined();
      }
      expect(reply.request().postDataJSON()).toMatchObject({image_id: 'part', image_path: image});
      expect(refresh.request().postDataJSON()).toEqual({folder_path: source, task: 'segmentation', validate_images: false});
      for (const name of ['x-vision-project', 'x-vision-context']) {
        const value = await within(reply.request().headerValue(name), deadline, 'owning save context header');
        expect(value).not.toBeNull(); headers[name] = value!;
      }
      expect(headers['x-vision-project']).toBe(project.id);
      expect(JSON.parse(headers['x-vision-context']).project_id).toBe(project.id);
      await expect(saveButton).toHaveText('Saved', {timeout: Math.max(1, Math.floor(deadline - performance.now()))});
      return {request: reply.request().postDataJSON(), original: byRequest.get(reply.request())!, annotation: await read(annotationRoute, undefined, undefined, headers)};
    };
    const initial = await save(); expect(initial.annotation.body.annotations).toHaveLength(1);
    expect(initial.annotation.body.annotations[0]).toMatchObject({type: 'rotated_bbox', label: 'Crack', rotated_bbox: [110, 170, 80, 40, 30], direction_deg: 315});
    await page.evaluate(() => {
      const state: any = {phase: 'positive', pointers: [], keys: []}; (window as any).__u020EmptyNudge = state;
      const pointer = (event: PointerEvent) => {
        const target = event.target instanceof Element ? event.target : null;
        if (!target?.closest('[data-canvas-container]')) return;
        state.pointers.push({phase: state.phase, at: performance.now(), trusted: event.isTrusted,
          on_canvas: target.tagName === 'CANVAS', button: event.button, x: event.clientX, y: event.clientY});
      };
      const key = (event: KeyboardEvent) => {
        if (event.key !== 'ArrowRight' || !event.shiftKey) return;
        const target = event.target instanceof HTMLElement ? event.target : null;
        const row: any = {phase: state.phase, at: performance.now(), trusted: event.isTrusted, key: event.key,
          code: event.code, shift: event.shiftKey, target_tag: target?.tagName ?? null,
          editable_or_dialog: Boolean(target && (target.isContentEditable || target.closest('input, textarea, select, [role="textbox"], [role="dialog"]'))),
          modal: Boolean(document.querySelector('[role="dialog"][aria-modal="true"]')), focused_document: document.hasFocus(), pointer: state.pointers.at(-1)};
        state.keys.push(row);
        // A trusted-event microtask can precede a later window listener.
        // Read preventDefault only on the next frame, after native dispatch.
        requestAnimationFrame(() => {
          row.default_prevented_after_dispatch = event.defaultPrevented; row.dispatch_completed = true;
          requestAnimationFrame(() => { row.two_animation_frames = true; });
        });
      };
      window.addEventListener('pointerdown', pointer, {capture: true, passive: true});
      window.addEventListener('keydown', key, {capture: true, passive: true});
      state.remove = () => { window.removeEventListener('pointerdown', pointer, true); window.removeEventListener('keydown', key, true); };
    });
    await page.getByTitle('선택 및 이동 (Select / Move - 1)', {exact: true}).click();
    const center = await point(110, 170); await page.mouse.click(center.x, center.y);
    await expect(direction).toHaveValue('315', {timeout: 10_000}); await expect(angle).toHaveValue('30', {timeout: 10_000});
    const positiveKey = await delivered('positive', true);
    await expect(saveButton).toHaveText('Save Changes', {timeout: 10_000});
    const moved = await save(), movedOBB = moved.annotation.body.annotations[0];
    expect(moved.annotation.body.annotations).toHaveLength(1);
    expect(movedOBB).toMatchObject({id: initial.annotation.body.annotations[0].id, type: 'rotated_bbox', label: 'Crack',
      rotated_bbox: [120, 170, 80, 40, 30], direction_deg: 315});
    const sameFields = clone(initial.annotation.body.annotations[0]); sameFields.rotated_bbox = movedOBB.rotated_bbox; sameFields.bbox = movedOBB.bbox;
    expect(movedOBB).toEqual(sameFields);
    expect(moved.annotation.body.metadata.revision).toBe(initial.annotation.body.metadata.revision + 1);
    Object.assign(proof, {project, source, image, original_image_sha256: sha(originalImage), initial: initial.annotation,
      positive_control: {key: positiveKey, actual_save_request: moved.request, annotation: moved.annotation}});
    await e.screenshot(page, 'u020-owning-selected-obb-coarse-nudge-control');
    await settle();
    const expectedWrites = [initial, moved].flatMap(row => [
      {method: 'POST', route: '/api/annotations/save', body: row.request},
      {method: 'POST', route: '/api/dataset/import', body: {folder_path: source, task: 'segmentation', validate_images: false}},
    ]);
    expect(writes).toEqual(expectedWrites);
    // These are actual ordinary startup reads. Their first lazy metadata/team
    // initialization belongs to setup; all later complete bodies and files
    // must already be stable before the empty-selection action starts.
    const routes = ['/api/team-data/readiness', '/api/dataset/metadata?limit=100', '/api/project/current', '/api/project/labelsets', annotationRoute];
    for (const route of routes) await read(route, undefined, undefined, headers);
    const snapshot = async () => {
      const replies: Record<string, unknown> = {};
      for (const route of routes) {
        const result = await read(route, undefined, undefined, headers);
        replies[route] = {body: result.body, raw_base64: result.bytes.toString('base64'), sha256: sha(result.bytes)};
      }
      await settle();
      return {replies, roots: {project: treePin(project.project_dir), source: treePin(source), harness_dataset: treePin(w.dataset)}};
    };
    const before = await snapshot(); proof.protected_before = before;
    const repeated = await snapshot(); proof.protected_repeated_before = repeated; expect(repeated).toEqual(before);
    writeBoundary = writes.length; const requestBoundary = wires.length;
    await page.evaluate(() => { (window as any).__u020EmptyNudge.phase = 'empty'; });
    const blank = await point(15, 15); await page.mouse.click(blank.x, blank.y);
    await expect(direction).toHaveCount(0, {timeout: 10_000}); await expect(angle).toHaveCount(0, {timeout: 10_000});
    await expect(page.getByTitle('Delete annotation', {exact: true})).toHaveCount(1, {timeout: 10_000});
    await expect(page.getByTestId('canvas-hud')).toContainText(/W:\s*--/, {timeout: 10_000});
    await expect(saveButton).toHaveText('Saved', {timeout: 10_000});
    const emptyKey = await delivered('empty', false); proof.empty_key = emptyKey;
    await expect(direction).toHaveCount(0, {timeout: 10_000}); await expect(angle).toHaveCount(0, {timeout: 10_000});
    await expect(saveButton).toHaveText('Saved', {timeout: 10_000});
    const after = await snapshot(); proof.protected_after = after; expect(after).toEqual(before);
    expect(writes.slice(writeBoundary)).toEqual([]); expect(writes).toEqual(expectedWrites);
    expect(fs.readFileSync(image)).toEqual(originalImage);
    await e.screenshot(page, 'u020-delivered-empty-selection-nudge-preserves-saved-obb');
    await settle(); expect(writes.slice(writeBoundary)).toEqual([]);
    expect({project: treePin(project.project_dir), source: treePin(source), harness_dataset: treePin(w.dataset)}).toEqual(before.roots);
    expect(pageErrors).toEqual([]); expect(consoleErrors).toEqual([]); expect(requestFailures).toEqual([]); expect(foreignRequests).toEqual([]);
    const afterSource = sourcePins(); proof.source_pins_after = afterSource;
    expect(afterSource).toEqual(originalSource); expect(harness.sourceIdentity()).toEqual(sourceIdentity);
    Object.assign(proof, {write_boundary: writeBoundary,
      browser_request_boundary: requestBoundary, observation_closure: 'trusted real key dispatch + two animation frames + complete original read bodies + exact files',
      empty_action_business_mutations: [], original_action_ms: 10_000, actual_models_or_jobs_dispatched: false});
  } finally {
    if (!page.isClosed()) {
      try { proof.delivered_events = await page.evaluate(() => {
        const state = (window as any).__u020EmptyNudge; state?.remove();
        return state ? {pointers: state.pointers, keys: state.keys} : null;
      }); } catch (error) { proof.observer_cleanup_error = String(error); }
    }
    page.off('request', onRequest); page.off('response', onResponse); page.off('requestfailed', onFailed);
    page.off('pageerror', onPageError); page.off('console', onConsole);
    const transport = wires.map(row => ({method: row.request.method(), url: row.request.url(), request_body: row.request.postData(),
      started: row.started, deadline: row.deadline, finished: row.finished, status: row.status, file: row.file, sha256: row.sha256, size: row.size, failure: row.failure}));
    const file = path.join(w.logs, 'u020-empty-selection-nudge-readback.json');
    fs.writeFileSync(file, JSON.stringify({...proof, api_calls: apiCalls, original_browser_transport: transport, writes,
      observed_postbaseline_mutations: writeBoundary === undefined ? null : writes.slice(writeBoundary),
      page_errors: pageErrors, console_errors: consoleErrors, request_failures: requestFailures, foreign_requests: foreignRequests}, null, 2), {flag: 'wx', mode: 0o600});
    e.addFile(file); e.note('u020_empty_selection_nudge', {...proof, readback: {file, ...filePin(file)},
      page_errors: pageErrors, console_errors: consoleErrors, request_failures: requestFailures, foreign_requests: foreignRequests,
      worker_process_exit_claim: false, backend_natural_shutdown: 'original owned harness fixture verdict; no custom stop or signal'});
  }
});

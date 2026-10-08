import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {performance} from 'node:perf_hooks';
import {inflateSync} from 'node:zlib';
import type {Page, Request, Response, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

const endpoint = '/api/dataset/metadata/split', READ_MS = 10_000;
const actor = 'split-fixture-reviewer';
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
type ApiResult = {status: number; raw: string; response: any};
type Api = (route: string, body?: unknown, method?: string) => Promise<ApiResult>;
type Snapshot = {files: Record<string, string>; directories: string[]};
test.use({actionTimeout: 10_000});

function snapshot(root: string): Snapshot {
  expect(fs.realpathSync(root)).toBe(root);
  const files: Record<string, string> = {}, directories: string[] = [];
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : 0)) {
      const file = path.join(folder, entry.name), relative = path.relative(root, file).split(path.sep).join('/');
      expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) {directories.push(relative); visit(file);}
      else {expect(entry.isFile()).toBe(true); files[relative] = fileSha(file);}
    }
  };
  visit(root); return {files, directories: directories.sort()};
}

function canonical(value: any): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  return '{' + Object.keys(value).sort().map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
}

function qualification(rows: any[], project: any, groupBy: string[], ratios: number[]) {
  const fields = ['relative_path', 'content_hash', 'annotation_hash', 'mask_hash', 'product', 'lot', 'group', 'revision', 'usage_state', 'train_eligible'];
  const members = [...rows].sort((a, b) => a.relative_path < b.relative_path ? -1 : 1).map(row => Object.fromEntries(fields.map(key => [key, row[key] ?? null])));
  const value = {schema_version: 1, task: project.task, labelset_id: project.active_labelset_id || 'default', group_by: groupBy, seed: 42,
    ratios, members, mandatory_unions: ['content_hash', 'nonempty_common_original_group'],
    unknown_lineage: 'Blank group does not prove an independent original; declare the same group for external crops/derived/synthetic variants.'};
  // These declared ratios are Pydantic floats. Only this numeric field needs
  // Python's .0 representation; member revisions and seed remain integers.
  const raw = canonical(value).replace('"ratios":' + canonical(ratios), '"ratios":[' + ratios.map(n => Number.isInteger(n) ? n + '.0' : String(n)).join(',') + ']');
  return {...value, sha256: sha(raw)};
}

function snapshotRows(source: string, annotationRoot: string, splitFile: string) {
  const studio = path.join(annotationRoot, 'by_dataset', sha(source).slice(0, 16));
  const parents = [...new Set(Object.keys(snapshot(source).files).map(name => path.dirname(path.join(source, name))))];
  const scopes = [...new Set([studio, ...parents.map(folder => path.join(annotationRoot, 'by_dataset', sha(folder).slice(0, 16)))])].sort();
  const rows: any[] = [];
  const record = (origin: string, file: string, relative: string, kind: string) => rows.push({origin, kind, relative_path: relative,
    source_path: file, sha256: fileSha(file), size_bytes: fs.statSync(file).size, snapshot_path: kind === 'label' ? `labels/${origin}/${relative}` : null});
  // The declared source has only the two original PNGs. Snapshot label scopes
  // use the producer's hidden-file rules; full custody snapshots exclude none.
  for (const relative of Object.keys(snapshot(source).files).sort()) record('source', path.join(source, relative), relative, 'image');
  for (const scope of scopes) if (fs.existsSync(scope)) for (const relative of Object.keys(snapshot(scope).files).sort()) {
    const parts = relative.split('/');
    if (parts.some((part, i) => part.startsWith('.') || i < parts.length - 1 && part.startsWith('__'))) continue;
    record(scope === studio ? 'studio' : 'studio_scoped', path.join(scope, relative), scope === studio ? relative : path.basename(scope) + '/' + relative, 'label');
  }
  record('split', splitFile, 'manifest.json', 'label');
  return rows.sort((a, b) => (a.origin + '\0' + a.relative_path) < (b.origin + '\0' + b.relative_path) ? -1 : 1);
}

function fingerprint(source: string, annotationRoot: string, splitFile: string) {
  const hash = createHash('sha256').update('modu-dataset-fingerprint-v1\0').update(source).update('\0');
  const rows = snapshotRows(source, annotationRoot, splitFile);
  const stats: any[] = [];
  for (const row of rows.filter(row => row.origin !== 'split')) {
    if (row.origin !== 'source' && row.relative_path.split('/').includes('metadata')) continue;
    if (!/\.(png|json|txt|xml|csv|yaml|yml)$/i.test(row.source_path)) continue;
    const stat = fs.statSync(row.source_path, {bigint: true}), identity = `${stat.size}:${stat.mtimeNs}:${stat.ctimeNs}`;
    const relative = row.origin === 'source' ? 'source/' + row.relative_path : row.origin === 'studio' ? 'studio/' + row.relative_path : 'studio_scoped/' + row.relative_path;
    hash.update(relative).update('\0').update(identity).update('\0');
    if (/\.(json|txt|xml|csv|yaml|yml)$/i.test(row.source_path)) hash.update(fs.readFileSync(row.source_path)).update('\0');
    stats.push({relative_path: relative, source_path: row.source_path, stat_identity: identity, sha256: row.sha256});
  }
  hash.update('split-manifest\0').update(fs.readFileSync(splitFile));
  return {fingerprint: 'v1:' + hash.digest('hex'), stats};
}

function intendedProject(before: Snapshot, splitRelative: string, splitSHA: string, backupRelative: string, backupFiles: Record<string, string>): Snapshot {
  expect(before.files[splitRelative]).toBeDefined();
  const files = {...before.files, [splitRelative]: splitSHA}, directories = new Set(before.directories);
  for (const [relative, digest] of Object.entries(backupFiles)) {
    const name = backupRelative + '/' + relative; expect(files[name]).toBeUndefined(); files[name] = digest;
    for (let folder = path.posix.dirname(name); folder !== '.'; folder = path.posix.dirname(folder)) directories.add(folder);
  }
  return {files, directories: [...directories].sort()};
}

function originalPixels(file: string, defect: boolean) {
  const bytes = fs.readFileSync(file);
  expect(bytes.subarray(0, 8)).toEqual(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  let position = 8; const idat: Buffer[] = []; let dimensions: Buffer | undefined;
  const crc32 = (data: Buffer) => {
    let crc = 0xffffffff;
    for (const byte of data) {crc ^= byte; for (let bit = 0; bit < 8; bit++) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;}
    return (crc ^ 0xffffffff) >>> 0;
  };
  while (position < bytes.length) {
    const size = bytes.readUInt32BE(position), kind = bytes.toString('ascii', position + 4, position + 8);
    const content = bytes.subarray(position + 8, position + 8 + size);
    expect(bytes.readUInt32BE(position + 8 + size)).toBe(crc32(bytes.subarray(position + 4, position + 8 + size)));
    if (kind === 'IHDR') dimensions = content;
    else if (kind === 'IDAT') idat.push(content);
    else expect(kind).toBe('IEND');
    position += size + 12;
  }
  expect(position).toBe(bytes.length); expect(dimensions).toBeDefined();
  expect(dimensions!).toEqual(Buffer.from([0, 0, 0, 32, 0, 0, 0, 32, 8, 2, 0, 0, 0]));
  const pixels = inflateSync(Buffer.concat(idat)); expect(pixels).toHaveLength(32 * 97);
  for (let y = 0; y < 32; y++) {
    expect(pixels[y * 97]).toBe(0);
    for (let x = 0; x < 32; x++) for (let c = 0; c < 3; c++)
      expect(pixels[y * 97 + 1 + x * 3 + c]).toBe(defect && x >= 8 && x < 16 && y >= 8 && y < 16 ? 30 : 180);
  }
  return {width: 32, height: 32, codec: 'RGB8/filter0/CRC32', all_pixels: 1024, sha256: sha(bytes)};
}


type ReadClock = {started: number; deadline: number; budget: number};
function readClock(): ReadClock {const started = performance.now(); return {started, deadline: started + READ_MS, budget: READ_MS};}
async function originalRead<T>(clock: ReadClock, operation: () => Promise<T>) {
  expect(clock.budget).toBe(READ_MS); expect(clock.deadline).toBe(clock.started + READ_MS);
  const remaining = clock.deadline - performance.now(); expect(remaining).toBeGreaterThan(0);
  let timer: ReturnType<typeof setTimeout> | undefined;
  try {
    const result = await Promise.race([operation(), new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original split read exceeded its absolute 10s deadline')), remaining);})]);
    const captured = performance.now(); expect(captured).toBeLessThanOrEqual(clock.deadline); return {result, ...clock, captured};
  } finally {clearTimeout(timer);}
}

function statistics(images: Workspace['images'], source: string, assignments: Record<string, string>) {
  const paths = images.map(image => path.relative(source, image.path).split(path.sep).join('/')).sort();
  expect(paths).toEqual(['ng/sample-ng.png', 'ok/sample-ok.png']); expect(Object.keys(assignments).sort()).toEqual(paths);
  const items = paths.map(relative => {
    const file = path.join(source, relative), label = relative.split('/')[0], split = assignments[relative];
    expect(['train', 'val', 'test']).toContain(split);
    return {image_id: path.basename(file, '.png'), file_name: path.basename(file), file_path: file, relative_path: relative,
      labels: [label], label, label_status: 'labeled', split, thumbnail_url: `/api/dataset/thumbnail/${path.basename(file)}?file_path=${file}`};
  });
  const counts = Object.fromEntries(['train', 'val', 'test', 'not_used', 'not_split'].map(part => [part, {count: items.filter(row => row.split === part).length, ratio: items.filter(row => row.split === part).length / 2}]));
  return {total: 2, labeling: {labeled: {count: 2, ratio: 1}, unlabeled: {count: 0, ratio: 0}}, assignments: counts,
    classes: {ng: {count: 1, ratio: .5}, ok: {count: 1, ratio: .5}}, items,
    class_count_grain: 'distinct source images per class; multi-class counts may exceed total', labelset_id: 'default'};
}

async function exercise(page: Page, w: Workspace, e: Evidence, rawApi: Api, native: boolean, origin: string, url?: string) {
  const calls: any[] = [], writes: any[] = [], controls: any[] = [], splitClocks = new Map<Request, ReadClock>();
  const observer = (request: Request) => {
    const address = new URL(request.url()); if (address.origin !== origin || !address.pathname.startsWith('/api/')) return;
    if (request.method() === 'POST' && [endpoint, '/api/dataset/import'].includes(address.pathname) || request.method() === 'GET' && address.pathname === '/api/dataset/images') {
      expect(splitClocks.has(request)).toBe(false); splitClocks.set(request, readClock());
    }
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method())) return;
    writes.push({method: request.method(), path: address.pathname, query: address.search, body: request.postData() ? request.postDataJSON() : null});
  };
  page.on('request', observer);
  const api = async (route: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') => {
    const read = await originalRead(readClock(), () => rawApi(route, body, method)), r = read.result; expect(r.status, route + ': ' + r.raw).toBe(200);
    expect(JSON.parse(r.raw)).toEqual(r.response); calls.push({route, method, body: body ?? null, ...r, clock: {started: read.started, deadline: read.deadline, budget: read.budget, captured: read.captured}}); return r.response;
  };
  const originals = w.images.map(image => ({...image, decoded: originalPixels(image.path, image.label === 'ng')}));
  const currentStorage = () => page.evaluate(() => Object.fromEntries(Object.keys(localStorage).sort().map(key => [key, localStorage.getItem(key)])));
  try {
    const project = await api('/api/project/create', {name: 'Owned split apply refusal', task: 'classification'});
    const ownedRoot = native ? path.join(w.userData, 'projects') : w.projects;
    expect(path.dirname(project.project_dir)).toBe(ownedRoot); expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
    const active = await api('/api/project/update', {source_dataset_dir: w.dataset}, 'PUT');
    const imported = await api('/api/dataset/import', {folder_path: w.dataset, task: 'classification', validate_images: false});
    expect(imported.total_images).toBe(2);
    const initial = (await api('/api/dataset/metadata?limit=10')).items;
    expect(initial).toHaveLength(2); expect(new Set(initial.map((r: any) => r.content_hash)).size).toBe(2);
    for (const [index, row] of initial.entries()) await api('/api/dataset/metadata/' + row.image_uuid, {expected_revision: row.revision, actor,
      changes: {product: 'saved-product-family', lot: 'controlled-lot-' + index, group: 'declared-original-' + index}}, 'PATCH');
    const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
      '/api/dataset/metadata?limit=10', '/api/dataset/metadata/statistics', '/api/dataset/versions', '/api/dataset/revisions', endpoint,
      ...initial.map((row: any) => '/api/team-data/images/' + row.image_uuid), ...w.images.map(image => annotationRoute(image.path))];
    for (const route of endpoints) await api(route);
    const setup = await api(endpoint, {group_by: ['product'], train_ratio: 1, val_ratio: 0, test_ratio: 0, apply: true, actor});
    expect(setup).toMatchObject({applied: true, group_count: 1, split: {train: 2, val: 0, test: 0}});
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click();
    const toggle = page.getByRole('button', {name: '이미지 검토·그룹 분할·라벨 교환', exact: true});
    if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click();
    const panel = page.getByRole('region', {name: '데이터 검토와 라벨 교환', exact: true});
    await expect(panel).toContainText('검색 결과 2개'); await expect(panel.getByRole('status').filter({hasText: '처리 중'})).toHaveCount(0);
    await panel.getByLabel('데이터 작업자 이름', {exact: true}).fill(actor);
    await panel.getByLabel('분할 그룹 기준', {exact: true}).selectOption('lot');
    for (const [index, name] of ['학습', '검증', '시험'].entries()) await panel.getByLabel(name + ' 그룹 분할 비율', {exact: true}).fill(String([50, 50, 0][index]));
    const apiBefore: Record<string, any> = {}; for (const route of endpoints) apiBefore[route] = await api(route);
    const metadata = apiBefore['/api/dataset/metadata?limit=10'].items, annotationRoot = active.annotations_dir;
    expect(metadata.map((row: any) => row.relative_path)).toEqual(['ng/sample-ng.png', 'ok/sample-ok.png']);
    expect(metadata.every((row: any) => row.usage_state === 'active' && row.annotation_hash === null && row.mask_hash === null)).toBe(true);
    expect(apiBefore['/api/dataset/metadata/statistics']).toEqual(statistics(w.images, w.dataset, {'ng/sample-ng.png': 'train', 'ok/sample-ok.png': 'train'}));
    expect(metadata).toHaveLength(2); expect(apiBefore[endpoint].qualification).toEqual(qualification(metadata, active, ['product'], [1, 0, 0]));
    expect(apiBefore['/api/dataset/versions'].versions).toHaveLength(1);
    const roots = {source: w.dataset, project: project.project_dir, annotations: annotationRoot};
    const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, snapshot(root)]));
    const splitFile = path.join(active.dataset_dir, 'splits', sha(w.dataset) + '.json'), splitRelative = path.relative(project.project_dir, splitFile).split(path.sep).join('/');
    const splitRaw = fs.readFileSync(splitFile), backupRows = snapshotRows(w.dataset, annotationRoot, splitFile), fingerprintBefore = fingerprint(w.dataset, annotationRoot, splitFile);
    expect(backupRows.filter(row => row.origin.startsWith('studio')).every(row => row.relative_path.split('/').includes('metadata'))).toBe(true);
    const setupWrites = writes.map(row => ({...row})), setupCalls = calls.map(row => ({...row}));
    const expectedSetup = setupCalls.filter(row => row.method !== 'GET').map(row => ({method: row.method, path: row.route, query: '', body: row.body}));
    expect(expectedSetup).toHaveLength(6); expect(setupWrites).toEqual(native ? expectedSetup : []);
    const storageBefore = await currentStorage();
    const beforeRecord = {project: active, roots, before, apiBefore, metadata, split_path: splitFile, split_raw: splitRaw.toString(), split_sha256: sha(splitRaw),
      backup_rows: backupRows, fingerprint_before: fingerprintBefore, setupWrites, setupCalls, storageBefore, originals};
    const beforeFile = path.join(w.logs, 'split-apply-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(beforeRecord, null, 2)); e.addFile(beforeFile);
    for (const [kind, root] of Object.entries(roots)) for (const relative of Object.keys(before[kind].files)) {
      const copy = path.join(w.logs, 'split-apply-before', kind, relative); fs.mkdirSync(path.dirname(copy), {recursive: true}); fs.copyFileSync(path.join(root, relative), copy); e.addFile(copy);
    }
    const protectedState = async (expectedProject = before.project, overrides: Record<string, any> = {}, expectedReviewer = actor) => {
      expect(snapshot(project.project_dir)).toEqual(expectedProject); expect(snapshot(w.dataset)).toEqual(before.source); expect(snapshot(annotationRoot)).toEqual(before.annotations);
      const after: Record<string, any> = {};
      for (const route of endpoints) {after[route] = await api(route); expect(after[route], route).toEqual(Object.hasOwn(overrides, route) ? overrides[route] : apiBefore[route]);}
      expect(snapshot(project.project_dir)).toEqual(expectedProject); expect(snapshot(w.dataset)).toEqual(before.source); expect(snapshot(annotationRoot)).toEqual(before.annotations);
      expect(await currentStorage()).toEqual({...storageBefore, 'modu-reviewer-name': expectedReviewer}); return after;
    };
    const fromButton = async (button: ReturnType<Page['getByRole']>) => {
      const reply = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).origin === origin && new URL(r.url()).pathname === endpoint, {timeout: READ_MS});
      await button.click(); const result = await reply, request = result.request(), clock = splitClocks.get(request);
      expect(clock).toBeDefined();
      const read = await originalRead(clock!, async () => {const raw = await result.text(); const finished = await result.finished(); expect(finished).toBeNull(); return {raw, finished};});
      const {raw, finished} = read.result;
      return {status: result.status(), raw, response: JSON.parse(raw), request: request.postDataJSON(), finished,
        clock: {started: read.started, deadline: read.deadline, budget: read.budget, captured: read.captured, provenance: 'same observed original POST request'}};
    };
    const previewButton = panel.getByRole('button', {name: '분할 미리보기', exact: true}), apply = panel.getByRole('button', {name: '분할 적용', exact: true});
    const preview = await fromButton(previewButton), expectedQualification = qualification(metadata, active, ['lot'], [.5, .5, 0]);
    expect(preview.status).toBe(200); expect(preview.request).toEqual({group_by: ['lot'], train_ratio: .5, val_ratio: .5, test_ratio: 0, apply: false, actor});
    // Python seed42 swaps the two ordered independent groups; the first emitted
    // group becomes train and the remaining one val under equal targets.
    const members = [...metadata].sort((a, b) => a.relative_path < b.relative_path ? -1 : 1);
    const assignments = {[members[1].relative_path]: 'train', [members[0].relative_path]: 'val'};
    expect(preview.response).toEqual({assignments, split: {train: 1, val: 1, test: 0}, group_count: 2, group_by: ['lot'], seed: 42,
      duplicates: [], applied: false, apply_supported: true, apply_unavailable_reason: null, qualification: expectedQualification, availability: 'available'});
    await expect(apply).toBeEnabled(); await protectedState();
    const postBaseline = () => writes.slice(setupWrites.length);
    const afterPreview = postBaseline().map(row => ({...row})); expect(afterPreview).toEqual([{method: 'POST', path: endpoint, query: '', body: preview.request}]);
    await panel.getByLabel('데이터 작업자 이름', {exact: true}).fill('   ');
    await apply.click(); await expect(panel.getByRole('alert')).toHaveText('작업자 이름을 입력하세요.'); await expect(apply).toBeEnabled();
    expect(postBaseline()).toEqual(afterPreview); await protectedState(before.project, {}, '   ');
    await panel.getByRole('alert').scrollIntoViewIfNeeded(); await e.screenshot(page, `${native ? 'native' : 'browser'}-split-blank-actor-refusal`);
    controls.push({cell: 'U013.group-split-apply.invalid', actual_enabled_button: true, whitespace_actor: true, exact_alert: '작업자 이름을 입력하세요.', new_POSTs: 0});
    await panel.getByLabel('데이터 작업자 이름', {exact: true}).fill(actor);
    const body = {...preview.request, apply: true, expected_qualification_sha256: expectedQualification.sha256};
    const faultBody = {detail: 'Controlled grouped split apply transport failure'}; let faults = 0;
    const fault = async (route: Route) => {
      if (route.request().method() !== 'POST') {await route.fallback(); return;}
      expect(new URL(route.request().url()).origin).toBe(origin); expect(route.request().postDataJSON()).toEqual(body); faults++;
      await route.fulfill({status: 503, json: faultBody});
    };
    await page.route('**' + endpoint, fault);
    let failed: any;
    try {
      failed = await fromButton(apply); expect(failed.status).toBe(503); expect(failed.request).toEqual(body); expect(failed.response).toEqual(faultBody);
      await expect(panel.getByRole('alert')).toContainText(faultBody.detail); await expect(apply).toBeEnabled(); await protectedState();
      await panel.getByRole('alert').scrollIntoViewIfNeeded(); await e.screenshot(page, `${native ? 'native' : 'browser'}-split-503-preserves-original-split`);
    } finally {await page.unroute('**' + endpoint, fault);}
    expect(faults).toBe(1);
    // Actual annotationsChanged() re-imports this exact saved source once and
    // loads its first gallery page; splitData() then loads that page once more.
    // Capture the original responses eagerly while the source UI awaits them.
    const refreshReads: Promise<any>[] = [];
    const refreshObserver = (reply: Response) => {
      const request = reply.request(), address = new URL(reply.url());
      if (address.origin !== origin || !['/api/dataset/import', '/api/dataset/images'].includes(address.pathname)) return;
      const clock = splitClocks.get(request); expect(clock).toBeDefined();
      refreshReads.push(originalRead(clock!, async () => {const raw = await reply.text(), finished = await reply.finished(); expect(finished).toBeNull();
        return {path: address.pathname, query: address.search, method: request.method(), request_body: request.postData() ? request.postDataJSON() : null,
          status: reply.status(), raw, raw_sha256: sha(raw), raw_size: Buffer.byteLength(raw), response: JSON.parse(raw), finished};})
        .then(read => ({...read.result, clock: {started: read.started, deadline: read.deadline, budget: read.budget, captured: read.captured,
          provenance: 'same original automatic import or gallery request'}})).catch(error => ({capture_error: String(error)})));
    };
    page.on('response', refreshObserver);
    let retried: any, automaticRefresh: any[] = []; const retryStarted = Date.now();
    try {
      retried = await fromButton(apply);
      await expect(panel.getByRole('status').filter({hasText: '처리 중'})).toHaveCount(0);
      automaticRefresh = await Promise.all(refreshReads); expect(automaticRefresh).toHaveLength(3);
      expect(automaticRefresh.every(row => !Object.hasOwn(row, 'capture_error'))).toBe(true);
    } finally {page.off('response', refreshObserver);}
    const automaticImport = automaticRefresh.filter(row => row.path === '/api/dataset/import'), automaticImages = automaticRefresh.filter(row => row.path === '/api/dataset/images');
    expect(automaticImport).toHaveLength(1); expect(automaticImages).toHaveLength(2);
    expect(automaticImport[0]).toMatchObject({method: 'POST', query: '', request_body: {folder_path: w.dataset, task: 'classification', validate_images: false}, status: 200, finished: null});
    expect(automaticImport[0].response).toEqual({status: 'success', total_images: 2, source_images: 2, unlabeled_images: 0, classes: {ng: 1, ok: 1},
      split: {train: 1, val: 1, test: 0}, corrupted_images: [], validation: {requested: false, checked_images: 0, complete: false, scope: 'not requested'},
      split_supported: true, split_unavailable_reason: null});
    for (const row of automaticImages) {
      expect(row).toMatchObject({method: 'GET', request_body: null, status: 200, finished: null});
      expect(Object.fromEntries(new URLSearchParams(row.query))).toEqual({folder_path: w.dataset, task: 'classification', limit: '48', offset: '0'});
      const expectedItems = statistics(w.images, w.dataset, assignments).items.map(item => ({image_id: item.image_id, file_name: item.file_name,
        file_path: item.file_path, width: 32, height: 32, split: item.split, label: item.label, labels: item.labels, thumbnail_url: item.thumbnail_url}));
      expect(row.response).toEqual({total: 2, limit: 48, offset: 0, items: expectedItems,
        class_split_counts: {ng: {train: 0, val: 1, test: 0}, ok: {train: 1, val: 0, test: 0}}});
    }
    expect(automaticImages[0].response).toEqual(automaticImages[1].response);
    expect(retried.status).toBe(200); expect(retried.request).toEqual(body); expect(retried.request).toEqual(failed.request);
    const saved = retried.response, backupID = saved.backup_version_id;
    expect(backupID).toMatch(/^v_\d{8}_\d{6}_[0-9a-f]{8}$/);
    expect(saved).toEqual({...preview.response, applied: true, backup_version_id: backupID});
    await expect(panel.getByRole('alert')).toHaveCount(0); await expect(panel.getByRole('status').filter({hasText: '처리 중'})).toHaveCount(0); await expect(panel.getByRole('status')).toContainText('그룹 분할 적용 · 이전 버전 ' + backupID);
    const expectedSplit = {folder_path: w.dataset, seed: 42, assignments, qualification: expectedQualification, group_count: 2};
    expect(JSON.parse(fs.readFileSync(splitFile, 'utf8'))).toEqual(expectedSplit);
    const backup = path.join(project.project_dir, 'versions', backupID), backupRelative = 'versions/' + backupID;
    const manifest = await api('/api/dataset/versions/' + backupID), unsigned = {...manifest}; delete unsigned.content_digest;
    expect(manifest.content_digest).toBe(sha(canonical(unsigned))); expect(manifest.files).toEqual(backupRows);
    expect(manifest).toEqual({schema_version: 1, id: backupID, project_id: project.id, labelset_id: 'default', name: '그룹 분할 전 자동 백업',
      note: actor + ": ['lot']", kind: 'auto_backup', created_at: manifest.created_at, source_dataset_dir: w.dataset, task: 'classification',
      image_count: 2, label_file_count: backupRows.filter(row => row.kind === 'label').length,
      total_image_bytes: w.images.reduce((n, image) => n + fs.statSync(image.path).size, 0), copied_label_bytes: backupRows.filter(row => row.kind === 'label').reduce((n, row) => n + row.size_bytes, 0),
      dataset_fingerprint: fingerprintBefore.fingerprint, files: backupRows, content_digest: sha(canonical(unsigned))});
    const createdMS = Date.parse(manifest.created_at); expect(Number.isFinite(createdMS)).toBe(true); expect(createdMS).toBeGreaterThanOrEqual(Math.floor(retryStarted / 1000) * 1000); expect(createdMS).toBeLessThanOrEqual(Date.now());
    const backupFiles: Record<string, string> = {'manifest.json': fileSha(path.join(backup, 'manifest.json'))};
    expect(JSON.parse(fs.readFileSync(path.join(backup, 'manifest.json'), 'utf8'))).toEqual(manifest);
    for (const row of backupRows) if (row.snapshot_path) {
      const copy = path.join(backup, row.snapshot_path); expect(fs.readFileSync(copy)).toEqual(fs.readFileSync(path.join(w.logs, 'split-apply-before', row.origin === 'split' || row.origin.startsWith('studio') ? 'project' : 'source', row.origin === 'source' ? row.relative_path : path.relative(project.project_dir, row.source_path))));
      expect(fileSha(copy)).toBe(row.sha256); backupFiles[row.snapshot_path] = row.sha256; e.addFile(copy);
    }
    expect(snapshot(backup).files).toEqual(backupFiles);
    const expectedTree = intendedProject(before.project, splitRelative, fileSha(splitFile), backupRelative, backupFiles);
    const summary = {id: backupID, name: manifest.name, note: manifest.note, labelset_id: 'default', kind: 'auto_backup', task: 'classification', created_at: manifest.created_at,
      source_dataset_dir: w.dataset, image_count: manifest.image_count, label_file_count: manifest.label_file_count, total_image_bytes: manifest.total_image_bytes,
      copied_label_bytes: manifest.copied_label_bytes, dataset_fingerprint: manifest.dataset_fingerprint, status: 'not_checked'};
    const expectedVersions = {versions: [summary, ...apiBefore['/api/dataset/versions'].versions].sort((a, b) => a.id < b.id ? 1 : -1)};
    const expectedSaved = {availability: 'available', assignments, split: {train: 1, val: 1, test: 0}, qualification: expectedQualification, stale: false, applied: true, apply_supported: false, duplicates: [], group_count: 2};
    const apiAfter = await protectedState(expectedTree, {[endpoint]: expectedSaved, '/api/dataset/versions': expectedVersions, '/api/dataset/metadata/statistics': statistics(w.images, w.dataset, assignments)});
    expect(postBaseline()).toEqual([{method: 'POST', path: endpoint, query: '', body: preview.request}, {method: 'POST', path: endpoint, query: '', body},
      {method: 'POST', path: endpoint, query: '', body}, {method: 'POST', path: '/api/dataset/import', query: '', body: {folder_path: w.dataset, task: 'classification', validate_images: false}}]);
    controls.push({cell: 'U013.group-split-apply.error', controlled503: failed, explicit_actual200: retried, exact_same_request_body: true, backup_version_id: backupID});
    await panel.getByRole('status').scrollIntoViewIfNeeded(); await e.screenshot(page, `${native ? 'native' : 'browser'}-split-explicit-real-retry-exact-backup`);
    const after = {project: snapshot(project.project_dir), source: snapshot(w.dataset), annotations: snapshot(annotationRoot)};
    const proof = path.join(w.logs, 'group-split-apply-refusal-proof.json'); fs.writeFileSync(proof, JSON.stringify({beforeRecord, controls, preview,
      full_fixture_calls: calls, all_observed_mutations: writes, post_baseline_mutations: postBaseline(), failed, retried, expectedTree, after, apiAfter,
      expectedQualification, manifest, backupFiles, expectedSaved, expectedVersions, automaticRefresh, whole_SOURCE_API_labels_and_settings_preserved: true,
      saved_split_overwrite1_and_auto_backup1_only: true, actual_model_training_inference_GPU_or_human_quality: false, native_runtime_error_cleanliness_accepted: false,
      native_page_console_blocked_loopback_capture: native ? 'unavailable' : 'browser-harness-only', original_fixture_deadlines_scope: 'fixture raw API full body completion bound to wrapper start plus 10s; each split POST response/body/finished bound to same observed original request start plus 10s', native}, null, 2), {flag: 'wx'});
    e.addFile(proof); e.addFile(splitFile); e.addFile(path.join(backup, 'manifest.json'));
    for (const image of originals) {expect(fileSha(image.path)).toBe(image.sha256); expect(originalPixels(image.path, image.label === 'ng')).toEqual(image.decoded); e.addFile(image.path);}
    e.note('group_split_apply_refusal', {proof_path: proof, proof_sha256: fileSha(proof), proof_size: fs.statSync(proof).size});
  } finally {page.off('request', observer);}
}

test('grouped split blank actor and exact failed apply preserve originals before explicit retry', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {
    const response = await request.fetch(renderer.origin + route, {timeout: READ_MS, method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})});
    const raw = await response.text(); return {status: response.status(), raw, response: JSON.parse(raw)};
  };
  await exercise(page, workspace, evidence, api, false, renderer.origin, renderer.url);
});
test('source Electron grouped split blank actor and exact failed apply preserve originals before explicit retry', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    const raw = await response.text(); return {status: response.status, raw, response: JSON.parse(raw)};
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true, `http://127.0.0.1:${backend.port}`);
});

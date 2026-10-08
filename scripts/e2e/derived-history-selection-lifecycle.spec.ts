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
const sha = (raw: Buffer | string) => createHash('sha256').update(raw).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  if (!fs.existsSync(root)) return result;
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return result;
}
const sorted = (value: any): any => Array.isArray(value) ? value.map(sorted) : value && typeof value === 'object'
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
const canonical = (value: any) => JSON.stringify(sorted(value));

async function initializeIdleEvaluationStores(api: Api, source: string, task: string) {
  const params = new URLSearchParams({source_dataset_path: source, task}).toString();
  const endpoints = ['/api/model-deployments/active?' + new URLSearchParams({source_dataset_path: source, task: 'ocr'}),
    '/api/model-deployments/active?' + params, '/api/model-deployments/history?' + params,
    '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts',
    '/api/runtime-services/capture-groups', '/api/runtime-services'];
  const records: Record<string, any> = {};
  // Each declared request is a GET. The constructors materialize this owned
  // fixture's empty default stores; no service, deployment or job is started.
  for (const endpoint of endpoints) records[endpoint] = await api(endpoint, undefined, 'GET');
  for (const endpoint of endpoints.filter(value => value.startsWith('/api/model-deployments/active?')))
    expect(records[endpoint]).toEqual({active: null, field_runtime_applied: false});
  expect(records['/api/model-deployments/history?' + params]).toEqual({revisions: []});
  expect(records['/api/fleet/targets']).toEqual({targets: []});
  expect(records['/api/fleet/rollouts']).toEqual({rollouts: []});
  expect(records['/api/fleet/capabilities']).toMatchObject({authentication: 'desktop_process_capability', actor_role: 'local_owner'});
  expect(records['/api/runtime-services/capture-groups']).toEqual({policy: null, groups: [], total: 0});
  expect(records['/api/runtime-services']).toMatchObject({runtime: {status: 'stopped'}, active: null, history: [],
    adapter_config: {enabled: false, modbus: null, mes: null},
    native_install: {prepared: false, registered: false, enabled: false, running: false, verified: false},
    recovery: {active: null, pending: null, last_operation: null}, runtime_build: null});
  return records;
}

const pixelControl = String.raw`
from pathlib import Path
from PIL import Image,ImageEnhance
import hashlib,json,sys
original,out=map(Path,sys.argv[1:3]);out.mkdir()
source_hash=hashlib.sha256(original.read_bytes()).hexdigest()
result={}
with Image.open(original) as opened:
 image=opened.convert('RGB')
 for name,edited in [('flip',image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)),('brightness',ImageEnhance.Brightness(image).enhance(.5))]:
  target=out/(name+'.png');edited.save(target,format='PNG')
  result[name]={'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'size':list(edited.size),'decoded_rgb_sha256':hashlib.sha256(edited.tobytes()).hexdigest()}
assert hashlib.sha256(original.read_bytes()).hexdigest()==source_hash
print(json.dumps({'original_sha256':source_hash,'expected':result,'application_transform_imported':False,'model_cpu_gpu_execution':False}))
`;

// Python retains JSON integer/float spelling used by the original canonical
// record. The independent reader imports no application derive/read helpers.
const recordControl = String.raw`
from pathlib import Path
from PIL import Image,ImageDraw
import hashlib,json,sys
path=Path(sys.argv[1]);record=json.loads(path.read_bytes())
digest=record['evidence_sha256'];payload={k:v for k,v in record.items() if k!='evidence_sha256'}
assert hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()==digest
root=path.parent;image=root/'images'/'derived.png'
assert image.resolve()==Path(record['file_path']).resolve() and root.resolve()==Path(record['dataset_path']).resolve()
assert hashlib.sha256(image.read_bytes()).hexdigest()==record['derived_sha256']
assert hashlib.sha256(Path(record['source_path']).read_bytes()).hexdigest()==record['source_sha256']
assert set(record['annotation_files_sha256'])=={'annotations.json','images/derived.json','masks/derived.png'}
for relative,digest in record['annotation_files_sha256'].items():assert hashlib.sha256((root/relative).read_bytes()).hexdigest()==digest
width,height=record['size'];labels=record['annotations'];assert len(labels)==1 and labels[0]['type']=='bbox'
assert json.loads((root/'annotations.json').read_bytes())=={'image_id':'derived','annotations':labels,'image_width':width,'image_height':height}
label=labels[0];x1,y1,x2,y2=label['bbox'];flags={}
if label.get('direction_deg') is not None:flags['studio_direction_deg']=label['direction_deg']
assert json.loads((root/'images'/'derived.json').read_bytes())=={'version':'5.0','imagePath':'derived.png','imageWidth':width,'imageHeight':height,
 'shapes':[{'label':label['label'],'points':[[x1,y1],[x2,y2]],'shape_type':'rectangle','flags':flags}],'flags':{},'derived_version_id':record['id']}
expected=Image.new('L',(width,height),0);ImageDraw.Draw(expected).rectangle(tuple(int(v) for v in label['bbox']),fill=label['category_id'])
with Image.open(root/'masks'/'derived.png') as mask:assert mask.mode=='L' and mask.size==(width,height) and mask.tobytes()==expected.tobytes()
print(json.dumps({'raw_record':record,'raw_record_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'canonical_evidence_sha256':record['evidence_sha256'],
 'all_output_and_complete_annotation_files_sha_verified':True,'independent_bbox_mask_pixels_and_LabelMe_geometry_verified':True,'application_deriver_or_reader_imported':False}))
`;

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'derived-history-originals'); fs.mkdirSync(source);
  const original = path.join(source, 'part.png'); fs.writeFileSync(original, png(64, 3, (x, y) => [x * 3, y * 3, (x + y) % 200]));
  const originalHash = fileSha(original);
  const oracle = JSON.parse(execFileSync(harness.resolvePython(), ['-c', pixelControl, original, path.join(w.logs, 'derived-history-pixel-oracle')],
    {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 15_000}));
  expect(oracle.original_sha256).toBe(originalHash); for (const row of Object.values(oracle.expected) as any[]) e.addFile(row.path);
  const project = await api('/api/project/create', {name: 'Owned derived history selection', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const saved = await api('/api/annotations/save', {image_id: 'part', image_path: original, image_width: 64, image_height: 64,
    actor: 'derived-history-fixture', annotations: [{id: 'controlled-original-box', type: 'bbox', label: 'Defect', category_id: 1,
      bbox: [8, 10, 40, 44], direction_deg: 20, text: 'A01'}]});
  const endpoint = '/api/data-workbench/derived', historyEndpoint = endpoint + '?image_path=' + encodeURIComponent(original);
  const annotationEndpoint = '/api/annotations/part?file_path=' + encodeURIComponent(original);
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const pendingStoreReads = new Set<Request>(), unverifiedStoreReads = new Set<Request>(), completedStoreReads: any[] = [], verifiedStoreReads: any[] = [], failedStoreReads: any[] = [];
  const storeReadTimes = new Map<Request, {started: number; deadline: number; finished?: number}>();
  const storeReader = (request: Request) => request.method() === 'GET' &&
    ['/api/model-deployments/active', '/api/model-deployments/history', '/api/provenance/impact'].includes(new URL(request.url()).pathname);
  const evaluationReader = (request: Request) => request.method() === 'GET' &&
    ['/api/model-deployments/active', '/api/model-deployments/history', '/api/provenance/impact',
      '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts',
      '/api/runtime-services/capture-groups', '/api/runtime-services', '/api/data-workbench/derived'].includes(new URL(request.url()).pathname);
  const beginStoreRead = (request: Request) => {
    if (evaluationReader(request)) {
      const started = performance.now(); storeReadTimes.set(request, {started, deadline: started + 10_000});
    }
    if (storeReader(request)) {pendingStoreReads.add(request); unverifiedStoreReads.add(request);}
  };
  const finishStoreRead = (request: Request) => {
    if (!evaluationReader(request)) return;
    const address = new URL(request.url()), timing = storeReadTimes.get(request)!;
    timing.finished = performance.now();
    if (!storeReader(request)) return;
    pendingStoreReads.delete(request);
    completedStoreReads.push({method: 'GET', endpoint: address.pathname, source: address.searchParams.get('source_dataset_path'),
      task: address.searchParams.get('task'), request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline});
  };
  const failStoreRead = (request: Request) => {if (storeReader(request)) {pendingStoreReads.delete(request); failedStoreReads.push(new URL(request.url()).pathname);}};
  page.on('request', beginStoreRead); page.on('requestfinished', finishStoreRead); page.on('requestfailed', failStoreRead);
  const withStoreReadDeadline = async <T,>(request: Request, read: () => Promise<T>): Promise<T> => {
    const timing = storeReadTimes.get(request)!;
    const completedInTime = () => timing.finished !== undefined && timing.finished <= timing.deadline;
    // A previously completed original request may be verified later. It does
    // not receive a fresh network deadline when this reader inspects it.
    if (timing.finished !== undefined) {expect(completedInTime()).toBe(true); return read();}
    const remaining = timing.deadline - performance.now();
    if (remaining <= 0) throw Error('Original store GET exceeded its absolute 10s completion deadline');
    let timer: ReturnType<typeof setTimeout> | undefined;
    const operation = read();
    try {
      const result = await Promise.race([operation, new Promise<T>((resolve, reject) => {
        timer = setTimeout(() => completedInTime() ? resolve(operation)
          : reject(Error('Original store GET exceeded its absolute 10s completion deadline')), remaining);
      })]);
      expect(completedInTime()).toBe(true); return result;
    } finally {if (timer !== undefined) clearTimeout(timer);}
  };
  const settleStoreReads = async () => {
    // Await the original, already-started reads. No new read, retry, file
    // exception or stability polling stands in for request completion.
    for (const request of unverifiedStoreReads) {
      const {response, body} = await withStoreReadDeadline(request, async () => {
        const response = await request.response(); expect(response).not.toBeNull();
        expect(response!.status()).toBe(200); expect(await response!.finished()).toBeNull();
        return {response: response!, body: await response!.json()};
      });
      const address = new URL(request.url()), timing = storeReadTimes.get(request)!;
      if (address.pathname === '/api/provenance/impact')
        expect(body).toMatchObject({project_id: project.id, source_dataset_path: source, labelset_id: 'default'});
      else {
        expect(address.searchParams.get('source_dataset_path')).toBe(source);
        expect(['ocr', project.task]).toContain(address.searchParams.get('task'));
        expect(body).toEqual(address.pathname.endsWith('/active') ? {active: null, field_runtime_applied: false} : {revisions: []});
      }
      verifiedStoreReads.push({method: 'GET', endpoint: address.pathname, source: address.searchParams.get('source_dataset_path'),
        task: address.searchParams.get('task'), status: response.status(), response_body_complete: true,
        request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline,
        exact_owned_binding_or_idle_record: true});
      unverifiedStoreReads.delete(request);
    }
    expect(pendingStoreReads.size).toBe(0); expect(unverifiedStoreReads.size).toBe(0); expect(failedStoreReads).toEqual([]);
  };
  const panel = page.getByRole('region', {name: '파생 이미지 편집', exact: true});
  const choice = panel.getByLabel('파생 편집 기준 버전', {exact: true});
  const historySummary = page.locator('summary').filter({hasText: /^원본 보존 이미지 편집 · 파생 버전/});
  const identity = panel.locator('summary').filter({hasText: /^버전 출처와 보존 해시$/}).locator('..');
  const historyReads: any[] = [];
  const historyRead = () => page.waitForResponse(response => {const address = new URL(response.url());
    return response.request().method() === 'GET' && address.pathname === endpoint && address.searchParams.get('image_path') === original;
  }, {timeout: 10_000});
  const completeHistory = async (waiting: ReturnType<typeof historyRead>, status = 200) => {
    const response = await waiting;
    const body = await withStoreReadDeadline(response.request(), async () => {
      expect(response.status()).toBe(status); expect(await response.finished()).toBeNull(); return response.json();
    });
    const timing = storeReadTimes.get(response.request())!;
    historyReads.push({method: 'GET', endpoint, image_path: original, status, complete_body: body,
      request_started_ms: timing.started, request_finished_ms: timing.finished, absolute_deadline_ms: timing.deadline});
    return body;
  };
  const mount = async () => {
    await stages.getByRole('button').nth(1).click(); await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
    const focus = page.getByRole('button', {name: '집중 편집', exact: true}); if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
    if (await historySummary.locator('..').getAttribute('open') === null) await historySummary.click(); await expect(panel).toBeVisible();
  };
  const reload = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await mount();
  };
  await reload(); expect(await api(historyEndpoint)).toEqual({versions: []});
  await settleStoreReads(); const idleDefaults = await initializeIdleEvaluationStores(api, source, project.task); await settleStoreReads();
  const settleCustody = async () => {
    await settleStoreReads(); expect(await initializeIdleEvaluationStores(api, source, project.task)).toEqual(idleDefaults); await settleStoreReads();
  };
  const roots = {source, project: project.project_dir, annotations: active.annotations_dir || project.annotations_dir};
  const endpoints = ['/api/project/current', '/api/team-data', '/api/team-data/readiness', '/api/dataset/metadata?limit=100',
    '/api/project/preferences', '/api/dataset/versions', '/api/dataset/metadata/split', '/api/project/labelsets',
    '/api/data-workbench/review-evaluations', '/api/data-workbench/review-queues', '/api/data-workbench/derived-adoptions',
    annotationEndpoint, historyEndpoint];
  const writes: any[] = [], controls: any[] = [], setupPosts: any[] = [], phaseReadbacks: any[] = [];
  const observe = (request: Request) => {const address = new URL(request.url());
    if (address.pathname.startsWith('/api/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      let body: any; try {body = request.postDataJSON();} catch {body = request.postData();}
      writes.push({method: request.method(), endpoint: address.pathname, body});
    }
  };
  const freezePhase = async (name: string, versions: any[]) => {
    await settleCustody();
    const records: Record<string, any> = {...idleDefaults}; for (const endpoint of endpoints) records[endpoint] = await api(endpoint);
    expect(records[historyEndpoint]).toEqual({versions}); expect(records['/api/project/current']).toMatchObject({id: project.id, source_dataset_dir: source, task: project.task});
    expect(records['/api/data-workbench/derived-adoptions']).toEqual({versions: []});
    expect(records['/api/team-data'].settings).toMatchObject({revision: 1, editing_enabled: false, review_enabled: false});
    const metadata = records['/api/dataset/metadata?limit=100'].items; expect(metadata).toHaveLength(1);
    expect(metadata[0]).toMatchObject({file_path: original, content_hash: originalHash, image_uuid: saved.metadata.image_uuid, revision: saved.metadata.revision});
    const teamRoute = '/api/team-data/images/' + metadata[0].image_uuid; records[teamRoute] = await api(teamRoute);
    expect(records[annotationEndpoint].annotations).toHaveLength(1); expect(records[annotationEndpoint].metadata.workflow_state).not.toBe('approved');
    for (const version of versions) records[endpoint + '/' + version.id] = await api(endpoint + '/' + version.id);
    await settleCustody();
    const trees = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root)]));
    const phase = {name, roots, trees, records, metadata, controlled_versions: versions, verifiedStoreReads: [...verifiedStoreReads]};
    const file = path.join(w.logs, 'derived-history-' + name + '-protected-before.json'); fs.writeFileSync(file, JSON.stringify(phase, null, 2)); e.addFile(file);
    for (const [name, root] of Object.entries(roots)) for (const relative of Object.keys(trees[name])) {
      const copy = path.join(w.logs, 'derived-history-' + phase.name + '-protected-before', name, relative);
      fs.mkdirSync(path.dirname(copy), {recursive: true}); fs.copyFileSync(path.join(root, relative), copy); e.addFile(copy);
    }
    return phase;
  };
  const unchanged = async (phase: Awaited<ReturnType<typeof freezePhase>>) => {
    await settleCustody(); for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(phase.trees[name]);
    const apiAfter: Record<string, any> = {};
    for (const [endpoint, record] of Object.entries(phase.records)) {const actual = await api(endpoint); expect(actual).toEqual(record); apiAfter[endpoint] = actual;}
    await settleStoreReads(); for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(phase.trees[name]);
    expect(writes).toEqual([]); expect(fileSha(original)).toBe(originalHash); return apiAfter;
  };
  const snapshotAfter = async (phase: Awaited<ReturnType<typeof freezePhase>>) => {
    const apiAfter = await unchanged(phase); const file = path.join(w.logs, 'derived-history-' + phase.name + '-protected-after.json');
    const after = {phase: phase.name, roots, trees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])),
      records: apiAfter, writes: [...writes]};
    fs.writeFileSync(file, JSON.stringify(after, null, 2)); e.addFile(file); phaseReadbacks.push(after);
  };
  const capture = async (name: string, locator: Locator) => {await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-derived-history-${name}`);};
  const emptyPhase = await freezePhase('empty', []); page.on('request', observe);
  try {
    const empty = historyRead(); await reload(); expect(await completeHistory(empty)).toEqual({versions: []});
    await expect(choice).toHaveValue(''); await expect(choice.locator('option')).toHaveCount(1);
    await expect(choice.locator('option')).toHaveText('원본과 현재 저장 라벨'); await expect(identity).toHaveCount(0);
    await expect(panel.locator('[aria-label="파생본 검수와 학습 연결"]')).toHaveCount(0);
    await unchanged(emptyPhase); await capture('real-empty-history-original-only', choice); await snapshotAfter(emptyPhase);
    controls.push({action: 'U014.derived-history-select', dimension: 'empty', real_GET_200_versions_empty: true,
      actual_original_option_only: true, full_empty_phase_source_annotations_settings_API_preserved: true, response_simulated_to_create_condition: false});
  } finally {page.off('request', observe);}
  // The empty proof is sealed before two declared setup writes. Intended
  // derived fixture outputs are protected by a separate final baseline.
  const base = {image_path: original, expected_revision: saved.metadata.revision, expected_sha256: originalHash, actor: 'derived-history-fixture'};
  const created: any[] = [], versionProofs: any[] = [];
  for (const [kind, operation] of [['brightness', {kind: 'brightness', factor: .5}], ['flip', {kind: 'flip', axis: 'horizontal'}]] as const) {
    const body = {...base, operation}; setupPosts.push({method: 'POST', endpoint, body}); const row = await api(endpoint, body);
    expect(row).toMatchObject({source_path: original, source_relative_path: 'part.png', source_sha256: originalHash, parent_id: null,
      actor: base.actor, operation, size: [64, 64], omitted_annotation_ids: [], derived_sha256: oracle.expected[kind].sha256});
    expect(fs.readFileSync(row.file_path)).toEqual(fs.readFileSync(oracle.expected[kind].path));
    const labels = emptyPhase.records[annotationEndpoint].annotations;
    expect(row.annotations).toEqual(kind === 'brightness' ? labels : [{...labels[0], bbox: [24, 10, 56, 44], direction_deg: 160}]);
    const rawFile = path.join(row.dataset_path, 'version.json'), raw = JSON.parse(fs.readFileSync(rawFile, 'utf8'));
    const proof = JSON.parse(execFileSync(harness.resolvePython(), ['-c', recordControl, rawFile],
      {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 15_000}));
    expect(proof.raw_record).toEqual(raw); expect(proof.raw_record_sha256).toBe(fileSha(rawFile));
    expect(proof.canonical_evidence_sha256).toBe(raw.evidence_sha256); versionProofs.push(proof);
    const {image_url, ...storedCreated} = row; expect(raw).toEqual(storedCreated); expect(image_url).toBe(endpoint + '/' + row.id + '/image');
    expect(Object.keys(raw.annotation_files_sha256).sort()).toEqual(['annotations.json', 'images/derived.json', 'masks/derived.png']);
    for (const [relative, hash] of Object.entries(raw.annotation_files_sha256)) expect(fileSha(path.join(row.dataset_path, relative))).toBe(hash);
    expect(JSON.parse(fs.readFileSync(path.join(row.dataset_path, 'annotations.json'), 'utf8'))).toEqual({image_id: 'derived', annotations: row.annotations, image_width: 64, image_height: 64});
    expect(fs.realpathSync(row.file_path)).toBe(path.join(fs.realpathSync(row.dataset_path), 'images', 'derived.png'));
    created.push(await api(endpoint + '/' + row.id)); e.addFile(rawFile); e.addFile(row.file_path);
  }
  expect(setupPosts).toHaveLength(2); expect(new Set(created.map(row => row.id)).size).toBe(2);
  for (const row of created) expect(row).toMatchObject({review_revision: 0, review: null, image_url: endpoint + '/' + row.id + '/image'});
  const savedHistory = await api(historyEndpoint); expect(savedHistory).toEqual({versions: created});
  const loaded = historyRead(); await reload(); expect(await completeHistory(loaded)).toEqual(savedHistory);
  await expect(choice.locator('option')).toHaveCount(3); await expect(choice).toHaveValue('');
  const storedPhase = await freezePhase('stored', created); page.on('request', observe);
  expect(storedPhase.trees.source).toEqual(emptyPhase.trees.source);
  expect(storedPhase.trees.annotations).toEqual(emptyPhase.trees.annotations);
  for (const [route, record] of Object.entries(emptyPhase.records))
    if (route !== historyEndpoint) expect(storedPhase.records[route]).toEqual(record);
  const intendedProjectTree = {...emptyPhase.trees.project};
  for (const row of created) for (const name of ['version.json', 'images/derived.png', 'annotations.json', 'images/derived.json', 'masks/derived.png']) {
    const relative = path.relative(project.project_dir, path.join(row.dataset_path, name)).split(path.sep).join('/');
    expect(relative.startsWith('../') || path.isAbsolute(relative)).toBe(false);
    expect(Object.hasOwn(intendedProjectTree, relative)).toBe(false);
    intendedProjectTree[relative] = fileSha(path.join(row.dataset_path, name));
  }
  expect(storedPhase.trees.project).toEqual(intendedProjectTree);
  const assertVersion = async (row: any) => {
    await expect(choice).toHaveValue(row.id); if (await identity.getAttribute('open') === null) await identity.locator('summary').click();
    expect(JSON.parse(await identity.locator('pre').innerText())).toEqual({version: row.id, parent: row.parent_id, actor: row.actor,
      operation: row.operation, original_sha256: row.source_sha256, derived_sha256: row.derived_sha256, dataset_path: row.dataset_path});
    const picture = panel.getByRole('img', {name: '편집 원본과 변환 라벨 미리보기', exact: true}).locator('image').first();
    expect(new URL((await picture.getAttribute('href'))!, page.url()).pathname).toBe(row.image_url);
    expect(await api(endpoint + '/' + row.id)).toEqual(row); expect(fileSha(row.file_path)).toBe(row.derived_sha256);
  };
  let failures = 0;
  const failHistory = async (route: Route) => {const request = route.request(), address = new URL(request.url());
    if (request.method() !== 'GET' || address.pathname !== endpoint || address.searchParams.get('image_path') !== original) {await route.fallback(); return;}
    failures++; await route.fulfill({status: 503, json: {detail: 'Controlled exact owned derived history GET failure'}});
  };
  try {
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0); await unchanged(storedPhase);
    await page.route('**/api/data-workbench/derived?*', failHistory);
    try {
      const failed = historyRead(); await mount(); expect(await completeHistory(failed, 503)).toEqual({detail: 'Controlled exact owned derived history GET failure'});
      await expect(panel.getByRole('alert')).toContainText('Controlled exact owned derived history GET failure');
      await expect(choice).toHaveValue(''); await expect(choice.locator('option')).toHaveCount(1); await expect(identity).toHaveCount(0);
      await capture('exact-history-503-no-stale-selection', panel.getByRole('alert'));
    } finally {await page.unroute('**/api/data-workbench/derived?*', failHistory);}
    await unchanged(storedPhase); expect(failures).toBe(1); const recovered = historyRead(); await reload(); expect(await completeHistory(recovered)).toEqual(savedHistory);
    await expect(choice.locator('option')).toHaveCount(3); await expect(panel.getByRole('alert')).toHaveCount(0);
    await choice.selectOption(created[0].id); await assertVersion(created[0]); await unchanged(storedPhase); await capture('real-200-exact-saved-version', identity);
    controls.push({action: 'U014.derived-history-select', dimension: 'error', exact_GET_503_count: failures,
      actual_failed_stage_entry_original_only_no_stale_version: true, actual_reload_real_200_full_stored_history: true,
      explicit_saved_selection: created[0].id, full_stored_versions_outputs_source_annotations_settings_API_preserved: true});

    const beforeChoice = await choice.inputValue(); await choice.click(); await page.keyboard.press('Escape'); await expect(choice).toBeFocused();
    await expect(choice).toHaveValue(beforeChoice); await assertVersion(created[0]); await unchanged(storedPhase); await capture('actual-picker-Escape-preserves-selection', choice);
    controls.push({action: 'U014.derived-history-select', dimension: 'cancel', actual_picker_Escape: true,
      cancellation_kind: 'dismiss actual choice popup without changing selected saved version; no explicit cancel dialog or job cancellation',
      exact_selected_version: beforeChoice, mutating_API_requests: 0, full_saved_versions_and_original_preserved: true});

    await choice.selectOption(created[1].id); await assertVersion(created[1]); await unchanged(storedPhase);
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0);
    await expect(page.getByTitle(source, {exact: true})).toBeVisible();
    await page.getByRole('button', {name: '파생 편집 · 검수한 학습 데이터 버전', exact: true}).click();
    const adoptions = page.getByRole('region', {name: '파생 편집 데이터 버전', exact: true});
    await expect(adoptions).toContainText('검수한 파생 학습 버전이 없습니다.');
    await expect(adoptions.getByRole('button', {name: '검수한 파생 버전을 데이터 원본으로 선택', exact: true})).toHaveCount(0);
    expect(await api('/api/project/current')).toEqual(storedPhase.records['/api/project/current']);
    expect(await api('/api/data-workbench/derived-adoptions')).toEqual({versions: []}); await unchanged(storedPhase);
    await capture('Dataset-handoff-keeps-original-source-no-adoption', adoptions); await adoptions.getByRole('button', {name: '닫기', exact: true}).click();
    const returned = historyRead(); await mount(); expect(await completeHistory(returned)).toEqual(savedHistory);
    await expect(choice).toHaveValue(''); await expect(choice.locator('option')).toHaveCount(3); await expect(identity).toHaveCount(0);
    await choice.selectOption(created[1].id); await assertVersion(created[1]); await unchanged(storedPhase); await capture('handoff-explicit-exact-saved-version', identity);
    controls.push({action: 'U014.derived-history-select', dimension: 'handoff', selected_saved_version_before_leave: created[1].id,
      actual_Dataset_stage_project_id_and_registered_original_source_display_preserved: true, original_default_source_not_silently_adopted: source,
      all_full_metadata_labels_settings_and_API_preserved: true, derived_training_adoptions_empty: true,
      actual_labeling_remount_selection_resets_to_original: true, actual_explicit_saved_selection_after_return: created[1].id,
      exact_full_saved_history_and_version_hash_preserved: true, derived_train_handoff_or_training_executed: false});
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0); await snapshotAfter(storedPhase);
    expect(controls).toHaveLength(4); expect(writes).toEqual([]);
    e.note('derived_history_selection_lifecycle', {requirements: ['S3-08'], cells: controls, emptyPhase, storedPhase, setupPosts,
      sourceElectron, actual_source_UI: true, actual_phase_after_records: phaseReadbacks, independent_saved_version_proofs: versionProofs,
      controlled_output_pixel_oracle: oracle, declared_setup_expected_project_tree: intendedProjectTree,
      all_complete_original_API_source_and_annotation_bytes_preserved_through_setup: true, all_protected_files_unfiltered: true,
      all_post_baseline_mutating_API_calls: writes, exact_allowed_post_baseline_writes: [], historyReads,
      store_read_completions: completedStoreReads, verified_store_reads: verifiedStoreReads, failed_store_reads: failedStoreReads,
      final_pending_store_reads: pendingStoreReads.size, final_unverified_store_reads: unverifiedStoreReads.size,
      original_annotations_and_saved_versions_all_preserved: true, selected_derived_not_training_adoption_or_default_input_switch: true,
      invalid_cell_or_already_verified_reopen_not_promoted: true, human_annotation_or_model_quality_approval: false,
      model_CPU_GPU_training_inference_or_deployment: false, physical_device_frozen_package_or_Windows_acceptance: false});
  } finally {page.off('request', observe); page.off('request', beginStoreRead); page.off('requestfinished', finishStoreRead); page.off('requestfailed', failStoreRead);}
}

test('derived history empty error Escape and Dataset handoff preserve exact versions and originals', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native derived history empty error Escape and Dataset handoff preserve complete versions and originals', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned derived history API ${response.status}: ${await response.text()}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});

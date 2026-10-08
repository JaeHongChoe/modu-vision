import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {inflateSync} from 'node:zlib';
import {performance} from 'node:perf_hooks';
import type {Request, Response} from '@playwright/test';
import {test, expect} from './fixtures/test';
const harness = require('./fixtures/harness.cjs');
const OV_PROVIDER = '/Volumes/ModuVisionScratch-20261007-77052e76/all-parent-closure/remaining-app-20261007/openvino-qualification-venv/bin/python';
const READ_MS = 15_000;
const sha = (value: Buffer | string) => createHash('sha256').update(value).digest('hex');
const fileSha = (file: string) => sha(fs.readFileSync(file));
test.use({actionTimeout: 10_000});

function requestBudget(method: string, pathname: string) {
  // This existing endpoint executes one real package with its original 30s
  // worker deadline. All other observed requests retain the original 15s bound.
  return method === 'POST' && /^\/api\/product-delivery\/packages\/[0-9a-f]{32}\/verify$/.test(pathname) ? 30_000 : READ_MS;
}

function tree(root: string): Record<string, string> {
  const result: Record<string, string> = {};
  const visit = (folder: string) => {
    for (const entry of fs.readdirSync(folder, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(folder, entry.name);
      expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); result[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return result;
}

function ownedDirectChild(root: string, file: string) {
  expect(path.dirname(file)).toBe(root);
  expect(fs.realpathSync(file)).toBe(file);
  for (let current = file; current !== path.dirname(current); current = path.dirname(current))
    expect(fs.lstatSync(current).isSymbolicLink()).toBe(false);
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

function verifyManifest(root: string, expectedSHA: string) {
  expect(fileSha(path.join(root, 'manifest.json'))).toBe(expectedSHA);
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'manifest.json'), 'utf8'));
  expect(manifest.schema_version).toBe(1); expect(Array.isArray(manifest.files)).toBe(true);
  const expected: Record<string, string> = {'manifest.json': expectedSHA};
  for (const row of manifest.files) {
    expect(typeof row.path).toBe('string'); expect(path.isAbsolute(row.path)).toBe(false);
    expect(row.path.split('/')).not.toContain('..'); expect(expected[row.path]).toBeUndefined();
    expect(Number.isSafeInteger(row.size) && row.size >= 0).toBe(true);
    expect(row.sha256).toMatch(/^[0-9a-f]{64}$/);
    const file = path.join(root, row.path);
    expect(fs.statSync(file).size).toBe(row.size); expect(fileSha(file)).toBe(row.sha256); expected[row.path] = row.sha256;
  }
  expect(tree(root)).toEqual(expected); return manifest;
}

function inputIdentity(project: any, source: string, heldout: string[]) {
  expect(fs.realpathSync(source)).toBe(source);
  expect(Object.keys(tree(source)).sort()).toEqual(heldout.map(file => path.relative(source, file).split(path.sep).join('/')).sort());
  const digest = createHash('sha256').update('modu-dataset-fingerprint-v1\0').update(source).update('\0');
  const sourceStats = [...heldout].sort((a, b) => path.relative(source, a).localeCompare(path.relative(source, b))).map(file => {
    const stat = fs.statSync(file, {bigint: true}), relative = path.relative(source, file).split(path.sep).join('/');
    const identity = `${stat.size}:${stat.mtimeNs}:${stat.ctimeNs}`;
    digest.update(`source/${relative}`).update('\0').update(identity).update('\0');
    return {relative_path: relative, sha256: fileSha(file), stat_identity: identity};
  });
  const overlayScopes = [...new Set([source, ...heldout.map(file => path.dirname(file))])].map(folder => path.join(project.annotations_dir, 'by_dataset', sha(folder).slice(0, 16)));
  for (const scope of overlayScopes) if (fs.existsSync(scope)) for (const relative of Object.keys(tree(scope))) expect(relative.split('/')[0]).toBe('metadata');
  const split = path.join(project.dataset_dir, 'splits', sha(source) + '.json'); expect(fs.existsSync(split)).toBe(false);
  digest.update('no-split-manifest\0');
  return {receipt: {source_dataset_path: source, source_fingerprint: 'v1:' + digest.digest('hex'), split_manifest_path: null, split_manifest_sha256: null,
    calibration_images: [], validation_images: heldout.map(file => ({relative_path: path.relative(source, file).split(path.sep).join('/'), sha256: fileSha(file), split: 'test'}))}, sourceStats, overlayScopes, absentSplit: split};
}

function exactJob(job: any, id: string, originalPackage: string, inputReceipt: any, heldout: string[], backendPID: number) {
  expect(id).toMatch(/^[0-9a-f]{32}$/); expect(job.job_id).toBe(id);
  expect(job.options).toEqual({package_dir: originalPackage, precision: 'fp32', device: 'CPU', cpu_threads: 1,
    calibration_images: [], validation_images: heldout, input_receipt: inputReceipt});
  expect(Number.isSafeInteger(backendPID) && backendPID > 0).toBe(true); expect(job.owner_pid).toBe(backendPID);
}

// Only the actual test body runs this existing supported model factory. SOURCE
// preparation may parse/transpile this string, but never calls it or imports torch.
const FIXTURE = String.raw`
import hashlib,json,shutil,sys
from pathlib import Path
import torch
from backend.engine.classification.model import create_classification_model
from backend.engine.flowchart_engine import get_single_segmentation_flowchart
from backend.engine.flow_package import build_flow_package
from backend.engine.product_delivery import record_package
p=json.loads(sys.argv[1]);project=p['project'];root=Path(project['project_dir'])
assert root.is_dir() and not root.is_symlink() and root.parent==Path(p['owned_project_root'])
source=root/'original-source';source.mkdir();heldout=[]
for row in p['images']:
    original=Path(row['path']);assert original.is_file() and not original.is_symlink()
    assert hashlib.sha256(original.read_bytes()).hexdigest()==row['sha256']
    label='OK' if row['label']=='ok' else 'NG';target=source/'test'/label/original.name
    target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(original,target,follow_symlinks=False);heldout.append(str(target))
model=create_classification_model(backbone='efficientnet_b0',num_classes=2,pretrained=False)
torch.set_num_threads(1)
with torch.no_grad():
    for parameter in model.parameters():parameter.zero_()
    model.classifier[-1].bias.copy_(torch.tensor([3.,0.]))
job='job_e2e_ov';checkpoint=Path(project['models_dir'])/job/'best_model.pt';checkpoint.parent.mkdir()
torch.save({'task':'classification','backbone':'efficientnet_b0','classes':['OK','NG'],'image_size':[32,32],'model_state_dict':model.state_dict()},checkpoint)
checkpoint.with_name('model_meta.json').write_text(json.dumps({'task':'classification','backbone':'efficientnet_b0','classes':['OK','NG'],'image_size':[32,32]}))
assert not any((checkpoint.parent/name).exists() for name in ('job.json','job_state.json','job_receipt.json'))
graph=get_single_segmentation_flowchart(job)
graph.nodes[1].data.task='classification';graph.nodes[1].data.params={};graph.nodes[1].data.crop_padding=0
package=build_flow_package(pipeline=graph,checkpoints={job:checkpoint},output_base_dir=root/'exports/flows',package_name='original_cpu_fixture')
bound={**project,'source_dataset_dir':str(source)}
identifier=record_package(bound,package['package_path'],recipe_task='classification')
print(json.dumps({'source':str(source),'heldout':heldout,'checkpoint':str(checkpoint),'job_id':job,'package':package,'package_id':identifier,'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'training_completed':False,'quality_approved':False}))
`;

test('native actual OpenVINO job preserves full original package and reopens exact IR evidence', {tag: '@electron'}, async ({electronSession, workspace: w, evidence: e}) => {
  test.setTimeout(480_000);
  expect(process.env.MV_E2E_OPENVINO_PYTHON).toBe(OV_PROVIDER);
  expect(fs.statSync(OV_PROVIDER).isFile()).toBe(true);
  const providerBefore = {selected: OV_PROVIDER, realpath: fs.realpathSync(OV_PROVIDER), file_sha256: fileSha(fs.realpathSync(OV_PROVIDER)),
    config_sha256: fileSha(path.join(path.dirname(path.dirname(OV_PROVIDER)), 'pyvenv.cfg'))};
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const apiCalls: any[] = [], writes: any[] = [], clocks = new Map<Request, {started: number; budget: number; deadline: number; finished?: number; failure?: string}>();
  const reads: any[] = [], replies: any[] = [], writeRequests: Request[] = [];
  const observed = (request: Request) => {
    const url = new URL(request.url()); if (!url.pathname.startsWith('/api/')) return;
    if (request.method() === 'OPTIONS') return;
    const started = performance.now(), budget = requestBudget(request.method(), url.pathname); clocks.set(request, {started, budget, deadline: started + budget});
    if (request.method() !== 'GET') {writes.push({method: request.method(), path: url.pathname, body: request.postData() ? request.postDataJSON() : null}); writeRequests.push(request);}
  };
  const finished = (request: Request) => {const row = clocks.get(request); if (row) row.finished = performance.now();};
  const failed = (request: Request) => {const row = clocks.get(request); if (row) {row.finished = performance.now(); row.failure = request.failure()?.errorText || 'unknown';}};
  const response = (reply: Response) => {if (new URL(reply.url()).pathname.startsWith('/api/')) replies.push({request: reply.request(), reply, status: reply.status()});};
  page.on('request', observed); page.on('requestfinished', finished); page.on('requestfailed', failed); page.on('response', response);
  const api = async (route: string, body?: unknown, method = body === undefined ? 'GET' : 'POST') => {
    const value = await page.evaluate(async ({port, route, method, body, budget}) => {
      const started = globalThis.performance.now(), deadline = started + budget;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        return await Promise.race([
          (async () => {const r = await fetch(`http://127.0.0.1:${port}${route}`, {method,
            ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
            const raw = await r.text(); if (globalThis.performance.now() > deadline) throw Error('Original fixture body exceeded its started read budget');
            return {status: r.status, raw, response: JSON.parse(raw)};})(),
          new Promise<never>((_, reject) => {timer = setTimeout(() => reject(Error('Original fixture read deadline expired')), Math.max(0, deadline - globalThis.performance.now()));}),
        ]);
      } finally {clearTimeout(timer);}
    }, {port: backend.port, route, method, body, budget: READ_MS});
    apiCalls.push({route, method, body: body ?? null, ...value}); return value;
  };
  const read = async (route: string) => {const value = await api(route); expect(value.status, route + ': ' + value.raw).toBe(200); return value.response;};
  const settle = async () => {
    // Original request start, not each check, creates the absolute deadline.
    for (const [request, clock] of clocks) {
      if (reads.some(row => row.request === request)) continue;
      if (clock.finished === undefined) await expect.poll(() => clock.finished, {timeout: Math.max(1, clock.deadline - performance.now())}).toBeDefined();
      expect(clock.failure).toBeUndefined(); expect(clock.budget).toBe(requestBudget(request.method(), new URL(request.url()).pathname)); expect(clock.deadline).toBe(clock.started + clock.budget);
      expect(clock.finished!).toBeLessThanOrEqual(clock.deadline);
      reads.push({request, method: request.method(), route: new URL(request.url()).pathname, query: new URL(request.url()).search, ...clock});
    }
  };
  const library = () => page.getByRole('region', {name: '저장된 검사 패키지 보관함', exact: true});
  const panel = () => page.getByRole('region', {name: 'Runtime 최적화와 양자화', exact: true});
  const open = async () => {await page.getByRole('button', {name: '패키지·장치·진단', exact: true}).click(); await expect(library()).toBeVisible();};
  const close = async () => {await page.getByRole('button', {name: '패키지·장치·설치·진단 닫기', exact: true}).click(); await expect(library()).toHaveCount(0); await expect(panel()).toHaveCount(0); await settle();};
  let originalProject: any;
  try {
    const capabilities = await read('/api/export/runtime-capabilities');
    expect(capabilities.openvino.available).toBe(true); expect(capabilities.openvino.devices).toContain('CPU');
    const created = await api('/api/project/create', {name: 'Owned real OpenVINO package boundary', task: 'classification'});
    expect(created.status).toBe(200); originalProject = created.response;
    const ownedRoot = path.join(w.userData, 'projects'); ownedDirectChild(ownedRoot, originalProject.project_dir);
    const fixture = JSON.parse(execFileSync(harness.resolvePython(), ['-c', FIXTURE, JSON.stringify({project: originalProject, images: w.images, owned_project_root: ownedRoot})], {
      cwd: harness.REPO_ROOT, env: {...process.env, HOME: w.home, USERPROFILE: w.home, VISION_AI_STUDIO_USER_DATA_DIR: w.userData,
        XDG_CACHE_HOME: path.join(w.home, '.cache'), TORCH_HOME: path.join(w.home, '.cache/torch'), HF_HOME: path.join(w.home, '.cache/huggingface'),
        OMP_NUM_THREADS: '1', MKL_NUM_THREADS: '1', PYTHONDONTWRITEBYTECODE: '1', HF_HUB_OFFLINE: '1', TRANSFORMERS_OFFLINE: '1', CUDA_VISIBLE_DEVICES: ''}, encoding: 'utf8', timeout: 120_000,
    }).trim());
    const updated = await api('/api/project/update', {source_dataset_dir: fixture.source}, 'PUT'); expect(updated.status).toBe(200);
    const imported = await api('/api/dataset/import', {folder_path: fixture.source, task: 'classification', validate_images: false}); expect(imported.status).toBe(200);
    await settle(); await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(originalProject.name); await open(); await settle();
    const current = await read('/api/project/current'); expect(current.id).toBe(originalProject.id); expect(current.source_dataset_dir).toBe(fixture.source);
    const folder = encodeURIComponent(fixture.source);
    const endpoints = ['/api/project/current', '/api/project/labelsets', '/api/project/preferences', '/api/team-data', '/api/team-data/readiness',
      '/api/dataset/metadata?limit=100', '/api/dataset/metadata/statistics', '/api/dataset/versions', '/api/dataset/revisions',
      '/api/product-delivery/runtime-packs', '/api/product-delivery/installation', '/api/product-delivery/hardware', '/api/runtime-services', '/api/runtime-services/capture-groups',
      '/api/fleet/targets', '/api/fleet/capabilities', '/api/fleet/rollouts', `/api/model-deployments/active?folder_path=${folder}&task=classification`, `/api/model-deployments/history?folder_path=${folder}&task=classification`,
      ...fixture.heldout.map((file: string) => '/api/annotations/' + encodeURIComponent(path.basename(file, '.png')) + '?file_path=' + encodeURIComponent(file))];
    const apiBefore: Record<string, any> = {}; for (const route of endpoints) apiBefore[route] = await read(route);
    expect(apiBefore['/api/runtime-services'].active).toBeNull(); expect(apiBefore['/api/runtime-services'].runtime.status).toBe('stopped');
    expect(apiBefore['/api/product-delivery/runtime-packs'].activation_supported).toBe(false); expect(apiBefore['/api/product-delivery/runtime-packs'].packs).toEqual([]);
    const inventoryBefore = await read('/api/product-delivery/packages'); expect(inventoryBefore.packages).toHaveLength(1);
    const original = inventoryBefore.packages[0]; expect(original.package_id).toBe(fixture.package_id); expect(original.package_path).toBe(fixture.package.package_path);
    expect(original.integrity).toBe('verified'); expect(original.scope_matches).toBe(true); expect(original.approval_present).toBe(false);
    const originalManifest = verifyManifest(original.package_path, original.manifest_sha256); expect(originalManifest.release).toBeUndefined();
    for (const image of w.images) {expect(fileSha(image.path)).toBe(image.sha256); expect(fs.statSync(image.path).size).toBe(image.bytes); originalPixels(image.path, image.label === 'ng');}
    const pixelProof = fixture.heldout.map((file: string, index: number) => {expect(fs.readFileSync(file)).toEqual(fs.readFileSync(w.images[index].path)); return originalPixels(file, w.images[index].label === 'ng');});
    const roots = {project: originalProject.project_dir, original_harness_dataset: w.dataset, source: fixture.source, models: originalProject.models_dir, original_package: original.package_path};
    await settle(); const before = Object.fromEntries(Object.entries(roots).map(([key, root]) => [key, tree(root as string)]));
    const journalPath = path.join(originalProject.project_dir, 'delivery/library.json'), journalBefore = JSON.parse(fs.readFileSync(journalPath, 'utf8'));
    const hardwarePath = path.join(originalProject.project_dir, 'delivery/hardware.json'); expect(fs.existsSync(hardwarePath)).toBe(false);
    expect(apiBefore['/api/product-delivery/hardware'].devices.every((row: any) => row.evidence.length === 0 && row.live_verified === false && row.approved === false)).toBe(true);
    expect(apiBefore['/api/product-delivery/hardware'].devices.filter((row: any) => row.device === 'openvino:CPU')).toHaveLength(1);
    const storageBefore = await page.evaluate(() => Object.fromEntries(Object.keys(localStorage).sort().map(key => [key, localStorage.getItem(key)])));
    expect(backend.pid).not.toBeNull(); const backendPID = backend.pid!;
    const identity = inputIdentity(current, fixture.source, fixture.heldout);
    const setupWrites = [...writes], setupCalls = [...apiCalls], setupWriteRequests = writeRequests.length; writes.length = 0;
    const beforeRecord = {project: current, fixture, roots, before, apiBefore, inventoryBefore, journalBefore, storageBefore, pixelProof, setupWrites, setupCalls, providerBefore, capabilities, identity, backendPID};
    const beforeFile = path.join(w.logs, 'real-openvino-protected-before.json'); fs.writeFileSync(beforeFile, JSON.stringify(beforeRecord, null, 2), {flag: 'wx'}); e.addFile(beforeFile);
    for (const [key, root] of Object.entries(roots)) for (const relative of Object.keys(before[key])) {
      const copy = path.join(w.logs, 'real-openvino-before', key, relative); fs.mkdirSync(path.dirname(copy), {recursive: true}); fs.copyFileSync(path.join(root as string, relative), copy); e.addFile(copy);
    }
    const originalRow = () => library().getByRole('article').filter({has: page.getByText(original.name, {exact: true})});
    await expect(originalRow()).toHaveCount(1);
    const selectOriginalReply = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/product-delivery/packages/${original.package_id}/select`);
    await originalRow().getByRole('button', {name: '최적화로 이동', exact: true}).click();
    const originalReply = await selectOriginalReply; expect(originalReply.status()).toBe(200); expect(await originalReply.json()).toEqual(original);
    await expect(panel()).toBeVisible(); const cohort = panel().getByRole('group', {name: '변환 오차 검증 · test / val', exact: true});
    await expect(cohort.getByRole('checkbox')).toHaveCount(2); for (const file of fixture.heldout) {const checkbox = cohort.getByRole('checkbox', {name: path.basename(file), exact: true}); await expect(checkbox).toBeChecked(); await checkbox.uncheck();}
    for (const file of fixture.heldout) {const checkbox = cohort.getByRole('checkbox', {name: path.basename(file), exact: true}); await checkbox.check(); await expect(checkbox).toBeChecked();}
    await panel().getByLabel(/^검증 장치/).selectOption('CPU'); await panel().getByLabel(/^정밀도/).selectOption('fp32');
    const optimizeReply = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/export/flow/optimize');
    await panel().getByRole('button', {name: '독립 후보 패키지 생성', exact: true}).click();
    const submitted = await optimizeReply; expect(submitted.status()).toBe(200); const start = await submitted.json();
    exactJob(start, start.job_id, original.package_path, identity.receipt, fixture.heldout, backendPID);
    const expectedBody = {package_dir: original.package_path, source_dataset_path: fixture.source, precision: 'fp32', device: 'CPU', cpu_threads: 1, calibration_images: [], validation_images: fixture.heldout};
    expect(submitted.request().postDataJSON()).toEqual(expectedBody);
    let completed: any;
    await expect.poll(async () => {completed = await read(`/api/export/flow/optimization-jobs/${start.job_id}`); exactJob(completed, start.job_id, original.package_path, identity.receipt, fixture.heldout, backendPID); if (['failed', 'cancelled', 'interrupted'].includes(completed.status)) throw Error(JSON.stringify(completed)); return completed.status;}, {timeout: 160_000}).toBe('completed');
    expect(completed.error).toBeNull(); expect(completed.result.quality_approved).toBe(false);
    expect(completed.result.source_manifest_sha256).toBe(original.manifest_sha256); expect(completed.result.heldout_flow_count).toBe(2); expect(completed.result.heldout_flow_passed_count).toBe(2);
    const candidate = path.join(originalProject.project_dir, 'exports/flows/openvino_' + start.job_id); expect(completed.result.package_path).toBe(candidate); expect(completed.package_path).toBe(candidate);
    const jobFile = path.join(originalProject.project_dir, 'exports/optimization_jobs', start.job_id + '.json'); expect(JSON.parse(fs.readFileSync(jobFile, 'utf8'))).toEqual(completed);
    const candidateManifest = verifyManifest(candidate, completed.result.candidate_manifest_sha256);
    expect(candidateManifest.release).toBeUndefined(); expect(candidateManifest.runtime_acceptance_sha256).toBeUndefined(); expect(candidateManifest.models).toEqual(originalManifest.models);
    expect(candidateManifest.runtime).toEqual({...originalManifest.runtime, device: 'openvino:CPU', cpu_threads: 1});
    const info = JSON.parse(fs.readFileSync(path.join(candidate, 'openvino_models.json'), 'utf8'));
    expect(info.quality_approved).toBe(false); expect(info.input_receipt).toEqual(completed.options.input_receipt); expect(info.source_manifest_sha256).toBe(original.manifest_sha256);
    expect(info.models).toEqual(completed.result.models); expect(info.models).toHaveLength(1);
    const model = info.models[0]; expect(model.job_id).toBe(fixture.job_id); expect(model.task).toBe('classification'); expect(model.precision).toBe('fp32');
    expect(model.openvino_version).toBe(capabilities.openvino.version); expect(model.device).toBe('CPU'); expect(model.role).toBe('model'); expect(model.quality_approved).toBe(false);
    expect(model.input_shape).toEqual([1, 3, 32, 32]); expect(model.dynamic_spatial).toBe(false); expect(model.metrics.calibration_count).toBe(0); expect(model.metrics.validation_count).toBe(2);
    expect(model.checkpoint_sha256).toBe(fixture.checkpoint_sha256); expect(model.metrics.validation_image_count).toBe(2);
    for (const number of Object.values(model.metrics)) if (typeof number === 'number') expect(Number.isFinite(number)).toBe(true);
    expect(model.metrics.max_absolute_error).toBeLessThan(0.0001);
    for (const name of ['model.xml', 'model.bin', 'conversion.json']) expect(fs.statSync(path.join(candidate, model.directory, name)).size).toBeGreaterThan(0);
    expect(JSON.parse(fs.readFileSync(path.join(candidate, model.directory, 'conversion.json'), 'utf8'))).toEqual(Object.fromEntries(Object.entries(model).filter(([key]) => !['job_id', 'task', 'checkpoint', 'checkpoint_sha256', 'directory', 'role'].includes(key))));
    const heldoutFile = path.join(candidate, 'heldout_flow_results.json'); expect(fileSha(heldoutFile)).toBe(info.heldout_flow_results_sha256);
    const heldout = JSON.parse(fs.readFileSync(heldoutFile, 'utf8')); expect(heldout).toHaveLength(2); const rawResults: any[] = [];
    for (let index = 0; index < heldout.length; index++) {
      const row = heldout[index], relative = `heldout/heldout_${String(index).padStart(4, '0')}.json`;
      expect(row.result_path).toBe(relative); expect(row.result_sha256).toBe(fileSha(path.join(candidate, relative))); expect(row.image_sha256).toBe(fileSha(fixture.heldout[index]));
      const raw = JSON.parse(fs.readFileSync(path.join(candidate, relative), 'utf8'));
      expect(raw.image_sha256).toBe(row.image_sha256); expect(raw.comparison).toEqual(row.comparison); expect(raw.comparison.status).toBe('passed'); expect(raw.comparison.mismatched_fields).toEqual([]);
      expect(raw.reference.final_verdict).toBe('OK'); expect(raw.candidate.final_verdict).toBe('OK'); expect(raw.candidate.model_runtime).toEqual({backend: 'openvino', device: 'CPU', compiled_models: 1, statistics_backend: 'torch_cpu', quality_approved: false});
      expect(await read(`/api/export/flow/optimization-jobs/${start.job_id}/heldout-results/${index}`)).toEqual({index, total: 2, ...raw}); rawResults.push(raw);
    }
    const prerequisitePath = `/api/export/flow/optimization-jobs/${start.job_id}/approval-prerequisites`;
    const prerequisite = await api(prerequisitePath); expect(prerequisite.status).toBe(409); expect(prerequisite.response).toEqual({detail: 'Model classification needs its current active checkpoint approval before precision acceptance'});
    await expect(panel().getByRole('status').first()).toContainText('completed');
    const approval = panel().getByRole('button', {name: '검토 후 새 승인 패키지 생성', exact: true}); await expect(approval).toBeDisabled();
    await panel().scrollIntoViewIfNeeded(); await e.screenshot(page, 'real-openvino-job-completed-unapproved-two-heldouts');
    await library().getByRole('button', {name: '새로고침', exact: true}).click(); const inventoryAfter = await read('/api/product-delivery/packages'); expect(inventoryAfter.packages).toHaveLength(2);
    const saved = inventoryAfter.packages.find((row: any) => row.package_path === candidate); expect(saved).toBeTruthy(); expect(saved.integrity).toBe('verified'); expect(saved.scope_matches).toBe(true);
    expect(saved.manifest_sha256).toBe(completed.result.candidate_manifest_sha256); expect(saved.approval_present).toBe(false); expect(saved.runtime.device).toBe('openvino:CPU');
    const candidateRow = library().getByRole('article').filter({hasText: saved.name}); const selectCandidate = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/product-delivery/packages/${saved.package_id}/select`);
    await candidateRow.getByRole('button', {name: '다시 열기', exact: true}).click(); const candidateSelected = await selectCandidate; expect(candidateSelected.status()).toBe(200); expect(await candidateSelected.json()).toEqual(saved);
    const selectedInventory = await read('/api/product-delivery/packages'); expect(selectedInventory.selected_package_id).toBe(saved.package_id);
    const verificationImage = fixture.heldout[0];
    await library().getByLabel('패키지 확인 이미지', {exact: true}).selectOption(verificationImage);
    await library().getByLabel('패키지 검증 실행 장치', {exact: true}).fill('openvino:CPU');
    const verifyPath = `/api/product-delivery/packages/${saved.package_id}/verify`, verificationStarted = Date.now() / 1000;
    const verifyReply = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === verifyPath, {timeout: 30_000});
    await library().getByRole('button', {name: '이미지 1장 검사', exact: true}).click(); const verifiedReply = await verifyReply;
    expect(verifiedReply.status()).toBe(200); expect(verifiedReply.request().postDataJSON()).toEqual({image_path: verificationImage, device: 'openvino:CPU'});
    const verification = await verifiedReply.json(); expect(Object.keys(verification).sort()).toEqual(['evidence', 'result']);
    expect(verification.result.status).toBe('success'); expect(verification.result.final_verdict).toBe('OK');
    expect(verification.result.model_runtime).toEqual({backend: 'openvino', device: 'CPU', compiled_models: 1, statistics_backend: 'torch_cpu', quality_approved: false});
    expect(verification.result.runtime_execution).toEqual({device: 'openvino:CPU', deadline_ms: 30_000, cpu_threads: 1,
      isolated_process: true, pid: verification.result.runtime_execution.pid, elapsed_ms: verification.result.runtime_execution.elapsed_ms});
    expect(Number.isSafeInteger(verification.result.runtime_execution.pid) && verification.result.runtime_execution.pid > 0).toBe(true);
    expect(verification.result.runtime_execution.pid).not.toBe(backendPID); expect(verification.result.runtime_execution.elapsed_ms).toBeGreaterThan(0); expect(verification.result.runtime_execution.elapsed_ms).toBeLessThanOrEqual(30_000);
    const hardwareReceipt = verification.evidence;
    expect(hardwareReceipt).toEqual({scope: inventoryBefore.scope, device: 'openvino:CPU', manifest_sha256: saved.manifest_sha256,
      image_sha256: fileSha(verificationImage), verdict: verification.result.final_verdict, observed_at: hardwareReceipt.observed_at});
    expect(Number.isFinite(hardwareReceipt.observed_at)).toBe(true); expect(hardwareReceipt.observed_at).toBeGreaterThanOrEqual(verificationStarted); expect(hardwareReceipt.observed_at).toBeLessThanOrEqual(Date.now() / 1000);
    const hardwareAfter = await read('/api/product-delivery/hardware');
    const expectedHardware = {...apiBefore['/api/product-delivery/hardware'], devices: apiBefore['/api/product-delivery/hardware'].devices.map((row: any) => row.device === 'openvino:CPU' ? {...row, live_verified: true, approved: false, evidence: [hardwareReceipt]} : row)};
    expect(hardwareAfter).toEqual(expectedHardware); expect(JSON.parse(fs.readFileSync(hardwarePath, 'utf8'))).toEqual({executions: [hardwareReceipt]});
    await expect(library().getByRole('status')).toContainText('실제 입력 실행 완료 · OK · 이 장치 실행 기록 저장');
    await close(); await page.reload(); await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(originalProject.name); await open(); await settle();
    expect(await read('/api/product-delivery/packages')).toEqual(selectedInventory); await expect(library().getByRole('article').filter({hasText: saved.name}).getByRole('button', {name: '선택됨', exact: true})).toBeVisible();
    const delivery = page.getByRole('region', {name: '제품 배포와 운영 작업 공간', exact: true});
    await delivery.getByRole('button', {name: '장치 검증', exact: true}).click(); await delivery.getByRole('button', {name: '실행 기록 새로고침', exact: true}).click();
    const hardwareRow = delivery.getByRole('row').filter({has: page.getByRole('cell', {name: 'openvino:CPU', exact: true})});
    await expect(hardwareRow).toHaveCount(1); await expect(hardwareRow).toContainText('실제 실행 확인 · 운영 승인 전'); await expect(hardwareRow).toContainText('1회');
    await hardwareRow.locator('summary').click(); await expect(hardwareRow).toContainText(saved.manifest_sha256); await expect(hardwareRow).toContainText(fileSha(verificationImage));
    await e.screenshot(page, 'real-openvino-device-execution-reopened-not-operational-approval');
    expect(await read('/api/product-delivery/hardware')).toEqual(expectedHardware);
    await delivery.getByRole('button', {name: '패키지·배포', exact: true}).click(); await expect(library()).toBeVisible(); await settle();
    const finalSelect = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === `/api/product-delivery/packages/${original.package_id}/select`);
    const reopenJob = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname === `/api/export/flow/optimization-jobs/${start.job_id}`);
    await originalRow().getByRole('button', {name: '최적화로 이동', exact: true}).click(); expect((await finalSelect).status()).toBe(200); const reopenedJob = await reopenJob; expect(reopenedJob.status()).toBe(200); expect(await reopenedJob.json()).toEqual(completed);
    await expect(panel().getByLabel('선택한 최적화 작업', {exact: true})).toContainText(start.job_id); await expect(panel().getByRole('status').first()).toContainText('completed');
    await expect(panel().getByText('2 / 2 동일 판정·공간 결과', {exact: false})).toBeVisible(); await expect(approval).toBeDisabled();
    await panel().getByRole('button', {name: '다음', exact: true}).click(); await expect(panel().getByText('2 / 2 · passed', {exact: false})).toBeVisible();
    await panel().scrollIntoViewIfNeeded(); await e.screenshot(page, 'real-openvino-original-completed-job-reopened'); await close();
    const protectedApiAfter: Record<string, any> = {}; for (const route of endpoints) {protectedApiAfter[route] = await read(route); expect(protectedApiAfter[route], route).toEqual(route === '/api/product-delivery/hardware' ? expectedHardware : apiBefore[route]);}
    await settle();
    for (const key of ['original_harness_dataset', 'source', 'models', 'original_package']) expect(tree(roots[key as keyof typeof roots])).toEqual(before[key]);
    expect(JSON.parse(fs.readFileSync(jobFile, 'utf8'))).toEqual(completed); const candidateTree = tree(candidate); verifyManifest(candidate, completed.result.candidate_manifest_sha256);
    const finalJournal = JSON.parse(fs.readFileSync(journalPath, 'utf8'));
    expect(finalJournal).toEqual({...journalBefore, selections: {[sha('local-desktop')]: {package_id: original.package_id, scope: inventoryBefore.scope, manifest_sha256: original.manifest_sha256}}});
    const intendedProject = {...before.project, 'delivery/library.json': fileSha(journalPath), 'delivery/hardware.json': fileSha(hardwarePath), [`exports/optimization_jobs/${start.job_id}.json`]: fileSha(jobFile),
      ...Object.fromEntries(Object.entries(candidateTree).map(([relative, digest]) => [`exports/flows/openvino_${start.job_id}/${relative}`, digest]))};
    expect(tree(originalProject.project_dir)).toEqual(intendedProject);
    const key = `runtime-optimization:${JSON.stringify([originalProject.project_dir, original.package_path, fixture.source, 'classification'])}`;
    const storageAfter = await page.evaluate(() => Object.fromEntries(Object.keys(localStorage).sort().map(key => [key, localStorage.getItem(key)])));
    expect(storageAfter).toEqual({...storageBefore, [key]: start.job_id});
    const imports = writes.filter(row => row.path === '/api/dataset/import');
    for (const row of imports) expect(row).toEqual({method: 'POST', path: '/api/dataset/import', body: {folder_path: fixture.source, task: 'classification', validate_images: false}});
    expect(writes.filter(row => row.path !== '/api/dataset/import')).toEqual([
      {method: 'POST', path: `/api/product-delivery/packages/${original.package_id}/select`, body: null},
      {method: 'POST', path: '/api/export/flow/optimize', body: expectedBody},
      {method: 'POST', path: `/api/product-delivery/packages/${saved.package_id}/select`, body: null},
      {method: 'POST', path: verifyPath, body: {image_path: verificationImage, device: 'openvino:CPU'}},
      {method: 'POST', path: `/api/product-delivery/packages/${original.package_id}/select`, body: null},
    ]);
    expect(fileSha(fs.realpathSync(OV_PROVIDER))).toBe(providerBefore.file_sha256); expect(fs.realpathSync(OV_PROVIDER)).toBe(providerBefore.realpath);
    expect(fileSha(path.join(path.dirname(path.dirname(OV_PROVIDER)), 'pyvenv.cfg'))).toBe(providerBefore.config_sha256);
    const non200: any[] = [];
    for (const row of replies.filter(row => row.status !== 200)) {expect(row.status).toBe(409); expect(row.request.method()).toBe('GET'); expect(new URL(row.request.url()).pathname).toBe(prerequisitePath);
      const raw = await row.reply.text(); expect(JSON.parse(raw)).toEqual(prerequisite.response); non200.push({path: prerequisitePath, method: 'GET', status: 409, raw, response: JSON.parse(raw)});}
    const mutationReplies: any[] = [];
    for (const request of writeRequests.slice(setupWriteRequests)) {const reply = await request.response(); expect(reply).not.toBeNull(); expect(reply!.status()).toBe(200); expect(await reply!.finished()).toBeNull();
      const raw = await reply!.text(), body = JSON.parse(raw); mutationReplies.push({method: request.method(), path: new URL(request.url()).pathname, request: request.postData() ? request.postDataJSON() : null, status: 200, raw, response: body});
      if (new URL(request.url()).pathname === '/api/dataset/import') expect(body).toEqual(imported.response);}
    expect(inputIdentity(current, fixture.source, fixture.heldout)).toEqual(identity);
    const notes = {scope: 'actual original native conversion job and library reopen; additional boundary proof, zero new registry cells', beforeRecord,
      actual_native: true, actual_job_UUID32: start.job_id, actual_start: start, actual_terminal: completed, original_package: original, candidate_saved: saved,
      candidate_manifest: candidateManifest, candidate_tree: candidateTree, openvino_models: info, heldout_rows: heldout, heldout_raw: rawResults,
      actual_selected_inventory: selectedInventory, final_delivery_journal: finalJournal, protected_api_after: protectedApiAfter, final_project_tree: intendedProject,
      original_UI_package_verify: {path: verifyPath, image: verificationImage, image_sha256: fileSha(verificationImage), actual: verification, hardware_after: hardwareAfter, expected_hardware: expectedHardware},
      complete_fixture_calls: apiCalls, complete_post_baseline_renderer_writes: writes, complete_post_baseline_mutation_responses: mutationReplies, exact_hydration_imports: imports, original_non200_responses: non200,
      observed_renderer_requests: reads.map(({request: _request, ...clock}) => clock),
      source_checkpoint_image_and_full_package_unchanged: true, selected_provider_readonly: providerBefore, original_prerequisite_409: prerequisite,
      models_initialized_not_trained: true, synthetic_NG_is_missed_by_all_OK_weights: true, model_quality_human_signing_deploy_GPU_Windows_accepted: false,
      runtime_pack_install_activation_signature_license_accepted: false, original_worker_Popen_exit_receipt_exported: false,
      native_page_console_blocked_loopback_capture: 'unavailable', native_runtime_error_cleanliness_accepted: false,
      ordinary_renderer_requests_and_fixture_reads_original_started_15s: true, single_real_package_verify_original_started_30s_boundary: true, canonical_manifest_MAX8MiB_unchanged: true, new_cells_promoted: 0};
    const proof = path.join(w.logs, 'package-library-real-openvino-proof.json'); fs.writeFileSync(proof, JSON.stringify(notes, null, 2), {flag: 'wx'}); e.addFile(proof);
    e.note('package_library_real_openvino', {proof_path: proof, proof_sha256: fileSha(proof), proof_size: fs.statSync(proof).size});
  } finally {page.off('request', observed); page.off('requestfinished', finished); page.off('requestfailed', failed); page.off('response', response);}
});

import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Locator, Page, Request, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
type Folder = (result: string | null, error?: string) => Promise<void>;
type Downloads = {run: (action: () => Promise<void>) => Promise<string>; count: () => Promise<number>};
const importEndpoint = '/api/dataset/masks/import', exportEndpoint = '/api/dataset/masks/export';
const sha = (file: string) => createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const python = (code: string, args: string[]) => execFileSync(process.env.MV_E2E_PYTHON || 'python3', ['-c', code, ...args], {encoding: 'utf8', timeout: 10_000});
const isPost = (request: Request, endpoint: string) => request.method() === 'POST' && new URL(request.url()).pathname === endpoint;
test.use({actionTimeout: 10_000});

function tree(root: string): Record<string, string> {
  const hashes: Record<string, string> = {};
  if (!fs.existsSync(root)) return hashes;
  for (const entry of fs.readdirSync(root, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
    const file = path.join(root, entry.name); expect(entry.isSymbolicLink()).toBe(false);
    if (entry.isDirectory()) for (const [name, value] of Object.entries(tree(file))) hashes[entry.name + '/' + name] = value;
    else {expect(entry.isFile()).toBe(true); hashes[entry.name] = sha(file);}
  }
  return hashes;
}

async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, folder: Folder, downloads: Downloads, native: boolean, url?: string) {
  const valid = path.join(workspace.root, 'owned-mask-preview'), invalid = path.join(workspace.root, 'owned-invalid-mask');
  fs.mkdirSync(valid); fs.mkdirSync(invalid);
  python(`import hashlib,json,pathlib,sys,numpy as np
from PIL import Image
root=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); rows=[]
for image in sorted(source.rglob('*.png')):
 with Image.open(image) as im: w,h=im.size
 pixels=np.zeros((h,w),np.uint8); pixels[4:12,3:15]=7
 relative=image.relative_to(source).as_posix(); name='masks/'+relative+'.mask.png'; (root/name).parent.mkdir(parents=True,exist_ok=True); Image.fromarray(pixels).save(root/name)
 rows.append({'file_name':relative,'mask_file':name,'width':w,'height':h,'source_sha256':hashlib.sha256(image.read_bytes()).hexdigest()})
(root/'mask_manifest.json').write_text(json.dumps({'schema_version':1,'classes':[{'id':0,'name':'background','color':'#000000'},{'id':7,'name':'ControlledScratch','color':'#f59e0b'}],'images':rows}))`, [valid, workspace.dataset]);
  const fixtureManifest = JSON.parse(fs.readFileSync(path.join(valid, 'mask_manifest.json'), 'utf8'));
  expect(fixtureManifest.images.map((row: any) => row.file_name).sort()).toEqual(workspace.images.map(image => path.relative(workspace.dataset, image.path).split(path.sep).join('/')).sort());
  fs.writeFileSync(path.join(invalid, 'mask_manifest.json'), JSON.stringify({schema_version: 99, classes: [], images: []}));
  const project = await api('/api/project/create', {name: 'Owned pixel mask lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: workspace.dataset}, 'PUT');
  await api('/api/dataset/import', {folder_path: workspace.dataset, task: 'segmentation'});
  // First-read default settings and image metadata belong to fixture setup,
  // before the complete, unfiltered annotation tree is protected.
  const teamBefore = await api('/api/team-data'); await api('/api/team-data/readiness');
  const metadataBefore = (await api('/api/dataset/metadata?limit=10')).items;
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  const labelsBefore = await Promise.all(workspace.images.map(image => api(annotationRoute(image.path))));
  expect(labelsBefore.every(row => row.annotations.length === 0)).toBe(true);
  const versionsBefore = await api('/api/dataset/versions');
  const annotations = active.annotations_dir || project.annotations_dir;
  const before = {annotations: tree(annotations), source: tree(workspace.dataset), valid: tree(valid), invalid: tree(invalid)};
  const setup = path.join(workspace.logs, 'pixel-mask-protected-before.json');
  fs.writeFileSync(setup, JSON.stringify({project, before, teamBefore, metadataBefore, labelsBefore, versionsBefore}, null, 2)); evidence.addFile(setup);
  for (const name of Object.keys(before.annotations)) {
    const saved = path.join(workspace.logs, 'annotation-before', name); fs.mkdirSync(path.dirname(saved), {recursive: true});
    fs.copyFileSync(path.join(annotations, name), saved); evidence.addFile(saved);
  }
  const requests: any[] = [], writes: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const endpoint = new URL(request.url()).pathname;
    if (isPost(request, importEndpoint) || isPost(request, exportEndpoint)) requests.push({endpoint, body: request.postDataJSON()});
    else if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && endpoint.startsWith('/api/')) writes.push({method: request.method(), endpoint, body: request.postData()});
  };
  page.on('request', observe);
  const panel = page.getByRole('region', {name: '데이터 검토와 라벨 교환', exact: true});
  const toggle = page.getByRole('button', {name: '이미지 검토·그룹 분할·라벨 교환', exact: true});
  const section = panel.getByLabel('외부 multiclass mask 교환', {exact: true});
  const open = async () => {
    if (await toggle.getAttribute('aria-expanded') === 'false') await toggle.click();
    await expect(panel).toContainText('검색 결과 2개');
    if (await section.getAttribute('open') === null) await section.locator('summary').click();
  };
  const select = async (value: string) => {await folder(value); await section.getByRole('button', {name: 'mask 폴더 선택', exact: true}).click(); await expect(section.getByText(value, {exact: true})).toBeVisible();};
  const preview = async () => {
    const waiting = page.waitForResponse(response => isPost(response.request(), importEndpoint));
    await section.getByRole('button', {name: 'mask 미리보기', exact: true}).click(); return waiting;
  };
  const capture = async (name: string, visible: Locator = section) => {await visible.scrollIntoViewIfNeeded(); await expect(visible).toBeInViewport(); await evidence.screenshot(page, `${native ? 'source-electron' : 'browser'}-mask-${name}`);};
  try {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name);
    await page.getByRole('navigation', {name: 'Workflow Stages'}).getByRole('button').nth(0).click(); await open();
    await select(valid); const first = await preview(); expect(first.status()).toBe(200);
    const firstBody = await first.json(); expect(firstBody.applied).toBe(false); expect(firstBody.preview).toHaveLength(2);
    expect(firstBody.preview.every((row: any) => row.existing_count === 0 && row.incoming_count === 1)).toBe(true);
    await expect(section.locator('article')).toHaveCount(2); await capture('real-preview', section.locator('article').first());
    const previewsBefore = requests.length;
    await folder(null); await section.getByRole('button', {name: 'mask 폴더 선택', exact: true}).click();
    await expect(section.getByText(valid, {exact: true})).toBeVisible(); await expect(section.locator('article')).toHaveCount(2);
    await expect(section.getByRole('status').filter({hasText: '처리 중'})).toHaveCount(0); expect(requests.length).toBe(previewsBefore); await capture('chooser-cancel-preserves-preview');
    controls.push({action: 'F118.pixel-mask-folder', dimension: 'cancel', chooser: 'controlled bridge null', extra_post: 0, preview_preserved: true});
    await folder(null, 'Controlled mask folder chooser failure'); await section.getByRole('button', {name: 'mask 폴더 선택', exact: true}).click();
    await expect(section.getByRole('alert')).toContainText('Controlled mask folder chooser failure');
    await expect(section.getByText(valid, {exact: true})).toBeVisible(); await expect(section.locator('article')).toHaveCount(2);
    expect(requests.length).toBe(previewsBefore); await capture('chooser-error-preserves-preview', section.getByRole('alert'));
    controls.push({action: 'F118.pixel-mask-folder', dimension: 'error', chooser: 'controlled bridge throw', extra_post: 0, preview_preserved: true});
    await select(invalid); const refused = await preview(); expect(refused.status()).toBe(422);
    await expect(section.getByRole('alert')).toContainText('Mask manifest schema_version must be 1'); await expect(section.locator('article')).toHaveCount(0);
    await expect(section.getByRole('button', {name: '검토한 mask 라벨 적용', exact: true})).toBeDisabled(); await capture('actual-invalid-manifest-422', section.getByRole('alert'));
    controls.push({action: 'F118.pixel-mask-preview', dimension: 'invalid', status: 422, actual_backend: true, applied: false});
    await select(valid); let failed = 0;
    const failure = async (route: Route) => {expect(isPost(route.request(), importEndpoint)).toBe(true); expect(route.request().postDataJSON()).toMatchObject({import_dir: valid, mode: 'preview'}); failed++; await route.fulfill({status: 503, json: {detail: 'Controlled mask preview transport failure'}});};
    await page.route('**' + importEndpoint, failure);
    try {expect((await preview()).status()).toBe(503); await expect(section.getByRole('alert')).toContainText('Controlled mask preview transport failure'); await expect(section.getByRole('button', {name: 'mask 미리보기', exact: true})).toBeEnabled(); await capture('preview-503-no-apply', section.getByRole('alert'));}
    finally {await page.unroute('**' + importEndpoint, failure);}
    expect(failed).toBe(1); const retried = await preview(); expect(retried.status()).toBe(200); expect(await retried.json()).toEqual(firstBody);
    await expect(section.locator('article')).toHaveCount(2); await expect(section.getByRole('alert')).toHaveCount(0); await capture('real-preview-retry', section.locator('article').first());
    controls.push({action: 'F118.pixel-mask-preview', dimension: 'error', controlled_status: 503, retry_actual_backend_status: 200, applied: false});
    const beforeReopen = requests.length; await toggle.click(); await expect(panel).toHaveCount(0); await open();
    await expect(section.getByText('mask_manifest.json 필요', {exact: true})).toBeVisible(); await expect(section.locator('article')).toHaveCount(0);
    await expect(section.getByRole('button', {name: 'mask 미리보기', exact: true})).toBeDisabled(); await expect(section.getByRole('button', {name: '검토한 mask 라벨 적용', exact: true})).toBeDisabled();
    expect(requests.length).toBe(beforeReopen); await capture('actual-remount-unsent-state-reset');
    controls.push({action: 'F118.pixel-mask-folder', dimension: 'reopen', actual_remount: true, transient_folder_cleared: true, extra_post: 0}, {action: 'F118.pixel-mask-preview', dimension: 'reopen', actual_remount: true, transient_preview_cleared: true, extra_post: 0});
    let exportFailures = 0; const exportsRoot = path.join(project.project_dir, 'exports'); expect(fs.existsSync(exportsRoot)).toBe(false);
    const beforeDownloads = await downloads.count();
    const exportFailure = async (route: Route) => {expect(isPost(route.request(), exportEndpoint)).toBe(true); expect(route.request().postDataJSON()).toEqual({include_originals: true}); exportFailures++; await route.fulfill({status: 503, json: {detail: 'Controlled mask export transport failure'}});};
    await page.route('**' + exportEndpoint, exportFailure);
    try {const waiting = page.waitForResponse(response => isPost(response.request(), exportEndpoint)); await section.getByRole('button', {name: 'pixel mask 내보내기', exact: true}).click(); expect((await waiting).status()).toBe(503); await expect(section.getByRole('alert')).toContainText('Controlled mask export transport failure'); await expect(section.getByRole('button', {name: 'pixel mask 내보내기', exact: true})).toBeEnabled(); await capture('export-503-no-download', section.getByRole('alert'));}
    finally {await page.unroute('**' + exportEndpoint, exportFailure);}
    expect(exportFailures).toBe(1); expect(await downloads.count()).toBe(beforeDownloads); expect(fs.existsSync(exportsRoot)).toBe(false);
    const waiting = page.waitForResponse(response => isPost(response.request(), exportEndpoint));
    const downloaded = await downloads.run(() => section.getByRole('button', {name: 'pixel mask 내보내기', exact: true}).click());
    const exported = await waiting; expect(exported.status()).toBe(200); const body = await exported.json();
    expect(body).toMatchObject({image_count: 2, annotation_count: 0, include_originals: true});
    const originalZip = path.join(exportsRoot, path.basename(body.download_url) + '.zip'); expect(sha(downloaded)).toBe(sha(originalZip));
    const archive = JSON.parse(python(`import hashlib,io,json,pathlib,stat,sys,zipfile,numpy as np
from PIL import Image
archive=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); assert archive.stat().st_size<=1048576
with zipfile.ZipFile(archive) as z:
 items=z.infolist(); assert len(items)==5 and sum(i.file_size for i in items)<=1048576
 assert len({i.filename for i in items})==len(items)
 for i in items:
  p=pathlib.PurePosixPath(i.filename); assert not p.is_absolute() and '..' not in p.parts and not stat.S_ISLNK(i.external_attr>>16)
 manifest=json.loads(z.read('mask_manifest.json')); assert manifest['classes']==[{'id':0,'name':'background','color':'#000000'}]
 rows=[]
 for row in manifest['images']:
  with Image.open(io.BytesIO(z.read(row['mask_file']))) as im: pixels=np.asarray(im); assert im.mode=='P' and pixels.shape==(row['height'],row['width']) and not np.any(pixels)
  original=z.read(row['original_file']); assert original==(source/row['file_name']).read_bytes()
  assert hashlib.sha256(original).hexdigest()==row['source_sha256']
  assert hashlib.sha256(z.read(row['mask_file'])).hexdigest()==row['mask_sha256']
  rows.append({'file_name':row['file_name'],'size':[row['width'],row['height']],'source_sha256':row['source_sha256'],'mask_sha256':row['mask_sha256'],'all_pixels_background':True})
 assert len(rows)==2
 print(json.dumps({'manifest':manifest,'rows':rows,'members':sorted(z.namelist())}))`, [downloaded, workspace.dataset]));
    await expect(section.getByRole('status').filter({hasText: '2개 이미지 · 0개 라벨'})).toBeVisible(); await expect(section.getByRole('alert')).toHaveCount(0); await capture('real-empty-label-zero-pixel-export', section.getByRole('status').filter({hasText: '2개 이미지 · 0개 라벨'}));
    controls.push({action: 'F118.pixel-mask-export', dimension: 'error', controlled_status: 503, retry_actual_backend_status: 200, extra_failed_download: 0}, {action: 'F118.pixel-mask-export', dimension: 'empty', images: 2, annotations: 0, actual_archive_all_background_masks: true, zero_image_dataset: false});
    expect(await downloads.count()).toBe(beforeDownloads + 1);
    expect(await Promise.all(workspace.images.map(image => api(annotationRoute(image.path))))).toEqual(labelsBefore);
    expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(metadataBefore); expect(await api('/api/team-data')).toEqual(teamBefore); expect(await api('/api/dataset/versions')).toEqual(versionsBefore);
    const after = {annotations: tree(annotations), source: tree(workspace.dataset), valid: tree(valid), invalid: tree(invalid)}; expect(after).toEqual(before);
    expect(writes).toEqual([]); expect(requests.filter(row => row.endpoint === importEndpoint).every(row => row.body.mode === 'preview')).toBe(true);
    for (const image of workspace.images) {expect(sha(image.path)).toBe(image.sha256); evidence.addFile(image.path);}
    evidence.addFile(downloaded); evidence.addFile(originalZip);
    evidence.note('pixel_mask_lifecycle', {project, controls, requests, writes, before, after, annotations_before: labelsBefore, annotations_after: labelsBefore, archive, downloaded_sha256: sha(downloaded), export_response: body,
      actual_source_ui: true, source_electron: native, folder_chooser_controlled: true, physical_os_chooser_acceptance: false, mask_apply_requests: 0, human_labels_applied: false, model_inference: false, gpu_used: false, quality_accepted: false, frozen_native_acceptance: false, installed_target_acceptance: false});
  } finally {page.off('request', observe);}
}

test('pixel mask lifecycle preserves labels across chooser refusal preview errors remount and empty export', async ({page, request, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port); let count = 0; page.on('download', () => count++);
  const api: Api = async (route, body, method) => {const r = await request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {data: body})}); expect(r.ok(), await r.text()).toBe(true); return r.json();};
  const folder: Folder = (value, error) => page.evaluate(({value, error}) => {(window as any).api.selectFolder = async () => {if (error) throw Error(error); return value;};}, {value, error});
  const downloads: Downloads = {count: async () => count, run: async action => {const waiting = page.waitForEvent('download'); await action(); const item = await waiting, target = path.join(workspace.logs, 'empty-pixel-mask.zip'); await item.saveAs(target); expect(await item.failure()).toBeNull(); return target;}};
  await exercise(page, workspace, evidence, api, folder, downloads, false, renderer.url);
});
test('source Electron pixel mask lifecycle preserves labels across chooser refusal preview errors remount and empty export', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, app = electronSession.app, backend = await electronSession.waitForBackend();
  await app.evaluate(({session}) => {(globalThis as any).__maskDownloads = 0; session.defaultSession.on('will-download', () => (globalThis as any).__maskDownloads++);});
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {const r = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'), ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})}); if (!r.ok) throw Error(`Owned mask fixture HTTP ${r.status}: ${await r.text()}`); return r.json();}, {port: backend.port, route, body, method});
  const folder: Folder = (value, error) => app.evaluate(({dialog}, {value, error}) => {dialog.showOpenDialog = async () => {if (error) throw Error(error); return {canceled: value === null, filePaths: value === null ? [] : [value]};};}, {value, error});
  const downloads: Downloads = {count: () => app.evaluate(() => (globalThis as any).__maskDownloads), run: async action => {const target = path.join(workspace.logs, 'empty-pixel-mask.zip'); await app.evaluate(({session}, file) => {(globalThis as any).__maskDownloadDone = null; session.defaultSession.once('will-download', (_event, item) => {item.setSavePath(file); item.once('done', (_event, state) => (globalThis as any).__maskDownloadDone = state);});}, target); await action(); await expect.poll(() => app.evaluate(() => (globalThis as any).__maskDownloadDone)).toBe('completed'); return target;}};
  await exercise(page, workspace, evidence, api, folder, downloads, true);
});

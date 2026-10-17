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

// F118: a controlled dialog answers only a directory choice; all project selection
// and mask preview responses below come from the real source Electron backend.
import type {Response as MaskHandoffResponse} from '@playwright/test';
import {png as maskHandoffPng} from './qa/appFlow';
type MaskHandoffContext = {workspace_id:string;project_id:string;actor_id:string;mode:'local'|'team'};
type MaskHandoffPreview = {file_name:string;image_uuid:string;revision:number;existing_count:number;incoming_count:number;conflict:boolean;classes:Array<{id:number;name:string;color:string}>;preview_masks:Array<{label:string;color:string;mask_rle:string}>};
type MaskHandoffScope = {tag:'A'|'B';source:string;bundle:string;project:any;manifest:any;metadata:any[];labels:any[];context:MaskHandoffContext;setup:{team:any;summary:any;readiness:any};roots:Record<string,string>};
type MaskHandoffFile = {kind:'file'|'directory';raw7:string[];uid:string;gid:string;sha256?:string};
function maskHandoffStat(s:fs.BigIntStats):string[]{return [s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);}
function maskHandoffFile(file:string):MaskHandoffFile {
  const before=fs.lstatSync(file,{bigint:true});expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1n);
  expect(fs.realpathSync(file)).toBe(path.resolve(file));let fd:number|undefined,primary:unknown,bytes:Buffer|undefined;
  try {fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);expect(maskHandoffStat(fs.fstatSync(fd,{bigint:true}))).toEqual(maskHandoffStat(before));bytes=fs.readFileSync(fd);expect(maskHandoffStat(fs.fstatSync(fd,{bigint:true}))).toEqual(maskHandoffStat(before));}
  catch(error){primary=error;}finally{if(fd!==undefined)try{fs.closeSync(fd);}catch(error){if(primary===undefined)primary=error;}}
  if(primary!==undefined)throw primary;expect(bytes).toBeDefined();expect(maskHandoffStat(fs.lstatSync(file,{bigint:true}))).toEqual(maskHandoffStat(before));
  return {kind:'file',raw7:maskHandoffStat(before),uid:String(before.uid),gid:String(before.gid),sha256:createHash('sha256').update(bytes!).digest('hex')};
}
function maskHandoffTree(root:string):Record<string,MaskHandoffFile>|null {
  if(!fs.existsSync(root))return null;const result:Record<string,MaskHandoffFile>={};
  const walk=(file:string,relative:string)=>{const s=fs.lstatSync(file,{bigint:true});expect(s.isSymbolicLink()).toBe(false);expect(fs.realpathSync(file)).toBe(path.resolve(file));
    if(s.isDirectory()){result[relative]={kind:'directory',raw7:maskHandoffStat(s),uid:String(s.uid),gid:String(s.gid)};const names=fs.readdirSync(file).sort();for(const name of names)walk(path.join(file,name),relative==='.'?name:relative+'/'+name);expect(fs.readdirSync(file).sort()).toEqual(names);expect(maskHandoffStat(fs.lstatSync(file,{bigint:true}))).toEqual(maskHandoffStat(s));}
    else {expect(s.isFile()).toBe(true);result[relative]=maskHandoffFile(file);}
  };walk(root,'.');return result;
}
function maskHandoffWithin<T>(value:Promise<T>,deadline:number,label:string):Promise<T>{
  void value.catch(()=>{});let timer:ReturnType<typeof setTimeout>|undefined;
  return Promise.race([value,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(Error(`${label} exceeded original 10-second request deadline`)),Math.max(0,deadline-Date.now()));})]).finally(()=>{if(timer!==undefined)clearTimeout(timer);});
}
function maskHandoffContext(encoded:string|undefined|null,project:any):MaskHandoffContext {
  expect(encoded).toBeTruthy();const context=JSON.parse(encoded!) as MaskHandoffContext;
  expect(Object.keys(context).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(context.project_id).toBe(project.id);expect(context.mode).toBe('local');
  for(const id of [context.actor_id,context.project_id,context.workspace_id])expect(id).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);return context;
}
function maskHandoffFixture(workspace:Workspace,tag:'A'|'B'):{source:string;bundle:string;manifest:any}{
  const source=path.join(workspace.root,`F118-${tag}-source`),bundle=path.join(workspace.root,`F118-${tag}-mask-bundle`);fs.mkdirSync(bundle);
  for(const [i,name] of ['batch-a/part.png','batch-b/part.png'].entries()){const file=path.join(source,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,maskHandoffPng(256,3,(x,y)=>[x,y,(tag==='A'?60:110)+i*60]));
    fs.writeFileSync(file.replace(/\.png$/,'.json'),JSON.stringify({version:'5.0.1',imagePath:'part.png',imageWidth:256,imageHeight:256,shapes:[{label:`Owned-${tag}-${i}`,shape_type:'rectangle',points:[[8+i,9+i],[25+i,31+i]],flags:{}}]}));}
  // Original nested roundtrip palette and hole formula; B uses a distinct shift
  // so an old preview cannot pass merely because both scopes have the same names.
  python(`import sys,pathlib,json,hashlib,numpy as np
from PIL import Image
r=pathlib.Path(sys.argv[1]);s=pathlib.Path(sys.argv[2]);tag=sys.argv[3];images=[]
for i,name in enumerate(['batch-a/part.png','batch-b/part.png']):
 p=np.zeros((256,256),np.uint8);dy=i*10+(5 if tag=='B' else 0);p[20+dy:100+dy,20:110]=7;p[40+dy:70+dy,45:80]=0;p[130+dy:200+dy,130:210]=23;p[155+dy:175+dy,155:185]=0
 mask='masks/'+name+'.png';f=r/mask;f.parent.mkdir(parents=True,exist_ok=True);Image.fromarray(p).save(f)
 images.append({'file_name':name,'mask_file':mask,'width':256,'height':256,'source_sha256':hashlib.sha256((s/name).read_bytes()).hexdigest()})
classes=[{'id':0,'name':'background','color':'#000000'},{'id':7,'name':'Scratch','color':'#f59e0b'},{'id':23,'name':'Crack','color':'#ef4444'},{'id':31,'name':'Void','color':'#a855f7'}]
(r/'mask_manifest.json').write_text(json.dumps({'schema_version':1,'classes':classes,'images':images}),encoding='utf-8')`,[bundle,source,tag]);
  return {source,bundle,manifest:JSON.parse(fs.readFileSync(path.join(bundle,'mask_manifest.json'),'utf8'))};
}
test('source Electron pixel mask folder and preview stay owned through A B A project handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const {window:page,app}=electronSession,backend=await electronSession.waitForBackend();let primary:unknown;const cleanupErrors:string[]=[];
  const scopedApi=async(route:string,body?:unknown,method?:string,context?:MaskHandoffContext):Promise<{data:any;context:string|null;raw_sha256:string}>=>page.evaluate(async({port,route,body,method,context})=>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10_000);
    try {const headers:Record<string,string>={'Content-Type':'application/json'};if(context){headers['X-Vision-Project']=context.project_id;headers['X-Vision-Context']=JSON.stringify(context);}
      const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{body:JSON.stringify(body)}),signal:controller.signal});const raw=await response.text();if(!response.ok)throw Error(`Owned F118 fixture ${route}: ${response.status} ${raw}`);
      const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));return {data:JSON.parse(raw),context:response.headers.get('X-Vision-Context'),raw_sha256:Array.from(new Uint8Array(digest)).map(x=>x.toString(16).padStart(2,'0')).join('')};
    } finally {clearTimeout(timer);}
  },{port:backend.port,route,body,method,context});
  const scopes:MaskHandoffScope[]=[];const requests:any[]=[],replies:any[]=[],pending:Promise<unknown>[]=[],observerErrors:string[]=[];
  const observe=(request:Request)=>{try{const endpoint=new URL(request.url()).pathname;if(endpoint.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))requests.push({endpoint,method:request.method(),body:request.postDataJSON(),headers:request.headers()});}catch(error){observerErrors.push(String(error));}};
  const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true}),toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});
  const section=panel.getByLabel('외부 multiclass mask 교환',{exact:true});
  const openPanel=async()=>{await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();await expect(panel).toContainText('검색 결과 2개');if(await section.getAttribute('open')===null)await section.locator(':scope > summary').click();};
  const responseFor=async(endpoint:string,action:()=>Promise<unknown>):Promise<{response:MaskHandoffResponse;data:any;headers:Record<string,string>;raw_sha256:string;request:any}>=>{
    const deadline=Date.now()+10_000;const wire=page.waitForResponse(r=>isPost(r.request(),endpoint),{timeout:10_000});void wire.catch(()=>{});
    const complete=(async()=>{const response=await wire;const [raw,finished,headers]=await maskHandoffWithin(Promise.all([response.body(),response.finished(),response.allHeaders()]),deadline,endpoint+' body/finished');expect(finished).toBeNull();expect(response.status()).toBe(200);return {response,data:JSON.parse(raw.toString('utf8')),headers,raw_sha256:createHash('sha256').update(raw).digest('hex'),request:{endpoint,body:response.request().postDataJSON(),headers:response.request().headers()}};})();void complete.catch(()=>{});pending.push(complete);
    await action();return complete;
  };
  const openProject=async(scope:MaskHandoffScope)=>{await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(scope.project.project_dir);
    const reply=await responseFor('/api/project/open',()=>dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click());expect(reply.request.body).toEqual({project_dir:scope.project.project_dir});expect(reply.data).toMatchObject({id:scope.project.id,task:'segmentation',project_dir:scope.project.project_dir,source_dataset_dir:scope.source});scope.context=maskHandoffContext(reply.headers['x-vision-context'],scope.project);await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);await openPanel();return {...reply,response:undefined};};
  const snapshot=()=>{const owners:Record<string,unknown>={},capture_errors:string[]=[];
    for(const scope of scopes){const roots:Record<string,unknown>={};for(const [role,root] of Object.entries(scope.roots))try{roots[role]=maskHandoffTree(root);}catch(error){roots[role]=null;capture_errors.push(`${scope.tag}.${role}: ${String(error)}`);}
      let project_manifest:MaskHandoffFile|null=null;try{project_manifest=maskHandoffFile(path.join(scope.project.project_dir,'project.json'));}catch(error){capture_errors.push(`${scope.tag}.project_manifest: ${String(error)}`);}owners[scope.tag]={roots,project_manifest};
    }return {owners,capture_errors};};
  let before:ReturnType<typeof snapshot>|undefined;const visits:any[]=[];
  try {
    for(const tag of ['A','B'] as const){const fixture=maskHandoffFixture(workspace,tag);const project=(await scopedApi('/api/project/create',{name:`F118 mask owner ${tag}`,task:'segmentation'})).data;expect(path.dirname(project.project_dir)).toBe(path.join(workspace.userData,'projects'));expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
      const updated=(await scopedApi('/api/project/update',{source_dataset_dir:fixture.source},'PUT')).data;expect(updated).toMatchObject({id:project.id,source_dataset_dir:fixture.source});
      const current=await scopedApi('/api/project/current');const context=maskHandoffContext(current.context,project);const team=await scopedApi('/api/team-data',undefined,undefined,context),summary=await scopedApi('/api/dataset/metadata/statistics',undefined,undefined,context),readiness=await scopedApi('/api/team-data/readiness',undefined,undefined,context);
      for(const reply of [team,summary,readiness])expect(maskHandoffContext(reply.context,project)).toEqual(context);expect(team.data.scope).toEqual({project_id:project.id,source:fixture.source,labelset_id:updated.active_labelset_id||'default'});expect(summary.data.labelset_id).toBe(team.data.scope.labelset_id);expect(summary.data.total).toBe(2);expect(summary.data.items.map((row:any)=>row.relative_path)).toEqual(['batch-a/part.png','batch-b/part.png']);expect(readiness.data.counts.total).toBe(2);expect(typeof readiness.data.ready).toBe('boolean');expect(Array.isArray(readiness.data.blockers)).toBe(true);
      const metadata=(await scopedApi('/api/dataset/metadata?limit=10',undefined,undefined,context)).data.items;
      const labels=await Promise.all(fixture.manifest.images.map((row:any)=>scopedApi('/api/annotations/part?file_path='+encodeURIComponent(path.join(fixture.source,row.file_name)),undefined,undefined,context).then(reply=>reply.data)));
      expect(labels.map((row:any)=>row.annotations.map((item:any)=>item.label))).toEqual([[`Owned-${tag}-0`],[`Owned-${tag}-1`]]);expect(metadata).toHaveLength(2);for(const [i,row] of labels.entries()){expect(row.metadata).toMatchObject({file_path:path.join(fixture.source,fixture.manifest.images[i].file_name),relative_path:fixture.manifest.images[i].file_name,content_hash:fixture.manifest.images[i].source_sha256,width:256,height:256,revision:1});expect(row.metadata.image_uuid).toMatch(/^[0-9a-f-]{36}$/);expect(metadata.find((item:any)=>item.file_path===row.metadata.file_path)).toEqual(row.metadata);}await scopedApi('/api/dataset/versions',undefined,undefined,context);
      scopes.push({tag,...fixture,project:updated,metadata,labels,context,setup:{team,summary,readiness},roots:{source:fixture.source,mask_bundle:fixture.bundle,annotations:updated.annotations_dir,dataset:updated.dataset_dir,models:updated.models_dir,reports:updated.reports_dir,versions:path.join(updated.project_dir,'versions')}});
    }
    const [A,B]=scopes;expect(A.project.id).not.toBe(B.project.id);for(let i=0;i<2;i++){expect(A.manifest.images[i].source_sha256).not.toBe(B.manifest.images[i].source_sha256);expect(A.labels[i].metadata.image_uuid).not.toBe(B.labels[i].metadata.image_uuid);}
    await page.reload();await openProject(A);await openProject(B);before=snapshot();expect(before.capture_errors).toEqual([]);page.on('request',observe);
    for(const [visit,scope] of [A,B,A].entries()){
      const opened=await openProject(scope);await expect(section.getByText('mask_manifest.json 필요',{exact:true})).toBeVisible();await expect(section.locator('article')).toHaveCount(0);await expect(section.getByRole('button',{name:'mask 미리보기',exact:true})).toBeDisabled();await expect(section.getByRole('button',{name:'검토한 mask 라벨 적용',exact:true})).toBeDisabled();await expect(section.getByRole('alert')).toHaveCount(0);
      await app.evaluate(({dialog},directory)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[directory]});},scope.bundle);const countBefore=requests.filter(row=>row.endpoint===importEndpoint).length;await section.getByRole('button',{name:'mask 폴더 선택',exact:true}).click();await expect(section.getByText(scope.bundle,{exact:true})).toBeVisible();await expect(section.getByText(scopes.find(other=>other.tag!==scope.tag)!.bundle,{exact:true})).toHaveCount(0);await expect(section.locator('article')).toHaveCount(0);expect(requests.filter(row=>row.endpoint===importEndpoint)).toHaveLength(countBefore);
      await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill(`F118-owner-${scope.tag}`);const reply=await responseFor(importEndpoint,()=>section.getByRole('button',{name:'mask 미리보기',exact:true}).click());expect(reply.request.body).toEqual({import_dir:scope.bundle,mode:'preview',conflict_policy:'reject',actor:`F118-owner-${scope.tag}`,expected_revisions:{}});expect(reply.request.headers['x-vision-project']).toBe(scope.project.id);expect(maskHandoffContext(reply.request.headers['x-vision-context'],scope.project)).toEqual(scope.context);expect(maskHandoffContext(reply.headers['x-vision-context'],scope.project)).toEqual(scope.context);
      const preview=reply.data.preview as MaskHandoffPreview[];expect(preview.map(row=>row.file_name)).toEqual(['batch-a/part.png','batch-b/part.png']);const manifestSHA=sha(path.join(scope.bundle,'mask_manifest.json'));const signature=createHash('sha256').update(scope.manifest.images.map((row:any)=>manifestSHA+sha(path.join(scope.bundle,row.mask_file))).join('')).digest('hex');expect(reply.data.manifest_sha256).toBe(signature);expect(reply.data.applied).toBe(false);
      const rasters:any[]=[];for(const [i,row] of preview.entries()){
        expect(row).toMatchObject({image_uuid:scope.labels[i].metadata.image_uuid,revision:scope.labels[i].metadata.revision,existing_count:1,incoming_count:2,conflict:true});expect(row.classes).toEqual(scope.manifest.classes);expect(row.preview_masks.map(mask=>[mask.label,mask.color])).toEqual([['Scratch','#f59e0b'],['Crack','#ef4444']]);
        const article=section.locator('article').filter({hasText:row.file_name});await expect(article).toHaveCount(1);await expect(article).toContainText(`${row.file_name} · 수정 ${row.revision} · 기존 1 / mask 클래스 2 · 충돌 있음`);await expect(article).toContainText('31=Void');
        for(const [maskIndex,mask] of row.preview_masks.entries()){expect(mask.mask_rle).toMatch(/^data:image\/png;base64,/);const file=path.join(workspace.logs,`F118-visit-${visit}-${i}-mask-${maskIndex}.png`);fs.writeFileSync(file,Buffer.from(mask.mask_rle.split(',')[1],'base64'));evidence.addFile(file);
          const proof=JSON.parse(python(`import sys,hashlib,json,numpy as np
from PIL import Image
with Image.open(sys.argv[1]) as im: actual=np.asarray(im.convert('RGBA'))
with Image.open(sys.argv[2]) as im: ids=np.asarray(im)
class_id=int(sys.argv[3]);color=sys.argv[4];rgb=tuple(bytes.fromhex(color[1:]));expected=np.zeros((256,256,4),np.uint8);expected[:,:,:3]=rgb;expected[:,:,3]=(ids==class_id).astype(np.uint8)*255
assert actual.shape==(256,256,4) and np.array_equal(actual,expected)
print(json.dumps({'width':256,'height':256,'whole_rgba_sha256':hashlib.sha256(actual.tobytes()).hexdigest(),'expected_rgba_sha256':hashlib.sha256(expected.tobytes()).hexdigest(),'foreground_pixels':int(np.count_nonzero(ids==class_id))}))`,[file,path.join(scope.bundle,scope.manifest.images[i].mask_file),String(maskIndex===0?7:23),mask.color]));
          const image=article.getByRole('img',{name:`${row.file_name} ${mask.label} pixel mask`,exact:true});await expect(image).toHaveAttribute('src',mask.mask_rle);await expect.poll(()=>image.evaluate((node:HTMLImageElement)=>({complete:node.complete,width:node.naturalWidth,height:node.naturalHeight}))).toEqual({complete:true,width:256,height:256});rasters.push({file,sha256:sha(file),image_uuid:row.image_uuid,class_id:maskIndex===0?7:23,...proof});
        }
      }
      await expect(section.getByRole('button',{name:'mask 폴더 선택',exact:true})).toBeEnabled();await expect(section.getByRole('alert')).toHaveCount(0);await expect(section.getByRole('status')).toHaveCount(0);
      const labelsAfter=await Promise.all(scope.manifest.images.map((row:any)=>scopedApi('/api/annotations/part?file_path='+encodeURIComponent(path.join(scope.source,row.file_name)),undefined,undefined,scope.context).then(item=>item.data)));expect(labelsAfter).toEqual(scope.labels);expect(snapshot()).toEqual(before);
      visits.push({visit,owner:scope.tag,project:scope.project,context:scope.context,project_open:opened,preview:{request:reply.request,response_headers:reply.headers,response:reply.data,full_response_sha256:reply.raw_sha256,finished:true},rasters,labels_unchanged:true,complete_roots_unchanged:true});await evidence.screenshot(page,`source-electron-F118-${scope.tag}-visit-${visit}`);replies.push(reply.data);
    }
    expect(observerErrors).toEqual([]);expect(requests.map(row=>[row.endpoint,row.method])).toEqual([['/api/project/open','POST'],[importEndpoint,'POST'],['/api/project/open','POST'],[importEndpoint,'POST'],['/api/project/open','POST'],[importEndpoint,'POST']]);expect(replies[2]).toEqual(replies[0]);expect(replies[1].preview.map((row:MaskHandoffPreview)=>row.image_uuid)).not.toEqual(replies[0].preview.map((row:MaskHandoffPreview)=>row.image_uuid));
  } catch(error){primary=error;}finally{
    if(observerErrors.length&&primary===undefined)primary=Error(observerErrors[0]);try{page.off('request',observe);}catch(error){cleanupErrors.push(String(error));if(primary===undefined)primary=error;}
    try{const settled=await Promise.allSettled(pending);for(const result of settled)if(result.status==='rejected'){cleanupErrors.push(String(result.reason));if(primary===undefined)primary=result.reason;}}catch(error){cleanupErrors.push(String(error));if(primary===undefined)primary=error;}
    if(before!==undefined)try{expect(snapshot()).toEqual(before);}catch(error){cleanupErrors.push(String(error));if(primary===undefined)primary=error;}
    try{const proof=path.join(workspace.logs,'F118-mask-folder-preview-handoff.json');fs.writeFileSync(proof,JSON.stringify({schema:'modu-vision.f118-mask-folder-preview-handoff/v1',cells:['F118.pixel-mask-folder.handoff','F118.pixel-mask-preview.handoff'],mode:'source-electron',owners:scopes,visits,requests,before,after:before===undefined?null:snapshot(),cleanup_errors:cleanupErrors,observer_errors:observerErrors,primary:primary===undefined?null:String(primary),folder_dialog_controlled:true,api_response_mocked:false,mask_apply_or_export:false,installed_native_acceptance:false,annotation_quality_acceptance:false,human_picker_acceptance:false,new_training:false},null,2));evidence.addFile(proof);}catch(error){cleanupErrors.push(String(error));if(primary===undefined)primary=error;}
  }
  if(primary!==undefined)throw primary;
});

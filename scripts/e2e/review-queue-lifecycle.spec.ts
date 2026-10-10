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
  const files: Record<string, string> = {};
  if (!fs.existsSync(root)) return files;
  const visit = (dir: string) => {
    for (const entry of fs.readdirSync(dir, {withFileTypes: true}).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = path.join(dir, entry.name); expect(entry.isSymbolicLink()).toBe(false);
      if (entry.isDirectory()) visit(file);
      else {expect(entry.isFile()).toBe(true); files[path.relative(root, file).split(path.sep).join('/')] = fileSha(file);}
    }
  };
  visit(root); return files;
}

async function exercise(page: Page, w: Workspace, e: Evidence, api: Api, sourceElectron: boolean, url?: string) {
  const source = path.join(w.root, 'queue-lifecycle-originals'); fs.mkdirSync(source);
  const originals = ['error', 'disagreement', 'threshold'].map((name, index) => {
    const file = path.join(source, name + '.png'); fs.writeFileSync(file, png(64, 3, (x, y) => [x, y, 100 + index]));
    return {path: file, sha256: fileSha(file), name};
  });
  const project = await api('/api/project/create', {name: 'Owned saved queue lifecycle', task: 'segmentation'});
  const active = await api('/api/project/update', {source_dataset_dir: source}, 'PUT');
  await api('/api/dataset/import', {folder_path: source, task: 'segmentation', validate_images: false});
  const annotationRoute = (file: string) => '/api/annotations/' + path.basename(file, '.png') + '?file_path=' + encodeURIComponent(file);
  // These saved labels and reports are controlled setup records. They do not
  // represent inference, a person's reviewed truth, or annotation approval.
  for (const original of originals) {
    await api('/api/annotations/save', {image_id: original.name, image_path: original.path,
      image_width: 64, image_height: 64, actor: 'queue-lifecycle-fixture', annotations: [
        {id: 'controlled-original-' + original.name, type: 'bbox', label: 'Defect', category_id: 1, bbox: [2, 3, 12, 13]},
      ]});
  }
  const seed = () => JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT,
    'scripts/e2e/fixtures/review_queue_reports.py'), w.root, project.project_dir, source],
  {cwd: harness.REPO_ROOT, encoding: 'utf8', timeout: 30_000}));
  const origin = seed(), alternate = seed(); expect(origin.record.evaluation_id).not.toBe(alternate.record.evaluation_id);
  const queue = await api('/api/data-workbench/review-queues', {evaluation_id: origin.record.evaluation_id, threshold: .5, margin: .05});
  expect(queue.items.map((row: any) => [row.relative_path, row.priority, row.state])).toEqual([
    ['error.png', 400, 'pending'], ['disagreement.png', 200, 'pending'], ['threshold.png', 100, 'pending'],
  ]);
  expect(queue).toMatchObject({cursor: 0, revision: 1, history: [], origin: {evaluation_id: origin.record.evaluation_id}});
  const queueEndpoint = '/api/data-workbench/review-queues/' + queue.id;
  // Real default settings, image metadata and complete annotation reads happen
  // before the baseline; no lock, revision, metadata key or file is filtered.
  const teamBefore = await api('/api/team-data');
  expect(teamBefore).toMatchObject({book: null, book_history: [], settings: {revision: 1, editing_enabled: false, review_enabled: false}});
  const readinessBefore = await api('/api/team-data/readiness');
  const metadataBefore = (await api('/api/dataset/metadata?limit=100')).items; expect(metadataBefore).toHaveLength(3);
  const teamImagesBefore = await Promise.all(metadataBefore.map((row: any) => api('/api/team-data/images/' + row.image_uuid)));
  const annotationsBefore = await Promise.all(originals.map(original => api(annotationRoute(original.path))));
  expect(annotationsBefore.every(row => row.annotations.length === 1 && row.metadata.workflow_state !== 'approved')).toBe(true);
  const preferencesBefore = await api('/api/project/preferences'), versionsBefore = await api('/api/dataset/versions');
  const splitBefore = await api('/api/dataset/metadata/split');
  const evaluationsBefore = await api('/api/data-workbench/review-evaluations');
  expect(evaluationsBefore.evaluations).toHaveLength(2);
  expect(new Set(evaluationsBefore.evaluations.map((row: any) => row.id))).toEqual(new Set([origin.record.evaluation_id, alternate.record.evaluation_id]));
  const defaultEvaluation = evaluationsBefore.evaluations[0].id;
  const unsentEvaluation = evaluationsBefore.evaluations[1].id; expect(unsentEvaluation).not.toBe(defaultEvaluation);
  const queuesBefore = await api('/api/data-workbench/review-queues'), queueBefore = await api(queueEndpoint);
  expect(queuesBefore.queues).toEqual([queue]); expect(queueBefore).toEqual(queue);
  const target = metadataBefore.find((row: any) => row.file_path === queue.items[0].file_path);
  expect(target).toBeDefined(); expect(target.content_hash).toBe(queue.items[0].source_sha256); expect(target.image_uuid).toBeTruthy();
  const roots = {source, annotations: active.annotations_dir || project.annotations_dir,
    reports: path.join(project.project_dir, 'reports'), workbench: path.join(project.dataset_dir, 'data_workbench'),
    splits: path.join(project.dataset_dir, 'splits')};
  const protectedTrees = Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)]));
  const baseline = {protectedTrees, teamBefore, readinessBefore, metadataBefore, teamImagesBefore,
    annotationsBefore, preferencesBefore, versionsBefore, splitBefore, evaluationsBefore, queuesBefore, queueBefore, target};
  const baselineFile = path.join(w.logs, 'queue-lifecycle-protected-before.json'); fs.writeFileSync(baselineFile, JSON.stringify(baseline, null, 2)); e.addFile(baselineFile);
  for (const [name, root] of Object.entries(roots)) {
    for (const relative of Object.keys(tree(root))) {
      const snapshot = path.join(w.logs, 'queue-lifecycle-protected-before', name, relative);
      fs.mkdirSync(path.dirname(snapshot), {recursive: true}); fs.copyFileSync(path.join(root, relative), snapshot); e.addFile(snapshot);
    }
  }
  const writes: any[] = [], queueReads: any[] = [], controls: any[] = [];
  const observe = (request: Request) => {
    const address = new URL(request.url());
    if (!address.pathname.startsWith('/api/')) return;
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method())) {
      let body: unknown; try {body = request.postDataJSON();} catch {body = request.postData();}
      writes.push({method: request.method(), endpoint: address.pathname, body});
    }
    if (request.method() === 'GET' && address.pathname === queueEndpoint) queueReads.push({method: 'GET', endpoint: address.pathname});
  };
  page.on('request', observe);
  const panel = page.getByRole('region', {name: '저장된 검토 큐', exact: true});
  const originChoice = panel.getByLabel('검토 큐 원본 평가', {exact: true});
  const threshold = panel.getByLabel('검토 큐 임계값', {exact: true});
  const margin = panel.getByLabel('검토 큐 임계 주변 범위', {exact: true});
  const queueChoice = panel.getByLabel('저장 검토 큐 선택', {exact: true});
  const create = panel.getByRole('button', {name: '우선순위 큐 저장', exact: true});
  const open = panel.getByRole('button', {name: '현재 항목 열기', exact: true});
  const stages = page.getByRole('navigation', {name: 'Workflow Stages'});
  const mountQueue = async () => {
    await stages.getByRole('button').nth(1).click();
    const focus = page.getByRole('button', {name: '집중 편집', exact: true});
    if (await focus.getAttribute('aria-pressed') === 'true') await focus.click();
    const summary = page.locator('summary').filter({hasText: '저장 검토 큐 · 오류·불일치·임계값 우선'});
    if (await summary.locator('..').getAttribute('open') === null) await summary.click();
    await expect(panel).toContainText('검토 진행 0 / 3'); await expect(open).toBeEnabled();
    await expect(queueChoice).toHaveValue(queue.id);
  };
  const reloadQueue = async () => {
    if (url) await page.goto(url); else await page.reload();
    await expect(page.getByTitle('프로젝트 관리', {exact: true})).toContainText(project.name); await mountQueue();
  };
  const unchanged = async () => {
    for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(protectedTrees[name]);
    expect((await api('/api/dataset/metadata?limit=100')).items).toEqual(metadataBefore);
    expect(await Promise.all(originals.map(original => api(annotationRoute(original.path))))).toEqual(annotationsBefore);
    expect(await api('/api/team-data')).toEqual(teamBefore); expect(await api('/api/team-data/readiness')).toEqual(readinessBefore);
    expect(await Promise.all(metadataBefore.map((row: any) => api('/api/team-data/images/' + row.image_uuid)))).toEqual(teamImagesBefore);
    expect(await api('/api/project/preferences')).toEqual(preferencesBefore); expect(await api('/api/dataset/versions')).toEqual(versionsBefore);
    expect(await api('/api/dataset/metadata/split')).toEqual(splitBefore); expect(await api('/api/data-workbench/review-evaluations')).toEqual(evaluationsBefore);
    expect(await api('/api/data-workbench/review-queues')).toEqual(queuesBefore); expect(await api(queueEndpoint)).toEqual(queueBefore);
    expect(writes).toEqual([]);
  };
  const capture = async (name: string, locator: Locator) => {
    await locator.scrollIntoViewIfNeeded(); await expect(locator).toBeInViewport();
    await e.screenshot(page, `${sourceElectron ? 'source-electron' : 'browser'}-queue-lifecycle-${name}`);
  };
  const setUnsent = async () => {
    await originChoice.selectOption(unsentEvaluation); await threshold.fill('0.7'); await margin.fill('0.11');
    await expect(originChoice).toHaveValue(unsentEvaluation); await expect(threshold).toHaveValue('0.7');
    await expect(margin).toHaveValue('0.11'); await expect(create).toBeEnabled();
  };
  const defaults = async () => {
    await expect(originChoice).toHaveValue(defaultEvaluation); await expect(threshold).toHaveValue('0.5');
    await expect(margin).toHaveValue('0.05'); await expect(queueChoice).toHaveValue(queue.id);
  };
  let controlledQueueGETs = 0;
  const failQueueRead = async (route: Route) => {
    const request = route.request(); expect(request.method()).toBe('GET');
    expect(new URL(request.url()).pathname).toBe(queueEndpoint); controlledQueueGETs++;
    await route.fulfill({status: 503, json: {detail: 'Controlled exact saved queue GET failure'}});
  };
  try {
    await reloadQueue(); await defaults(); await setUnsent(); await unchanged();
    await capture('valid-unsent-origin-threshold-margin', create);
    // Abandonment by real stage exit is the cancellation exercised here. The
    // product has no explicit confirm/cancel dialog for these queue inputs.
    await stages.getByRole('button').nth(0).click(); await expect(panel).toHaveCount(0); await unchanged();
    await mountQueue(); await defaults(); await unchanged(); await capture('stage-abandonment-reset', create);
    for (const action of ['saved-queue-create', 'saved-queue-threshold', 'saved-queue-margin', 'saved-queue-origin-choice']) {
      controls.push({action: 'U015.' + action, dimension: 'cancel', actual_stage_exit: true,
        cancellation_kind: 'abandoned valid unsent inputs; no explicit cancellation dialog', queue_POSTs: 0});
    }
    await setUnsent(); await capture('valid-unsent-before-actual-reload', create);
    await reloadQueue(); await defaults(); await unchanged(); await capture('actual-reload-reset-defaults', create);
    for (const action of ['saved-queue-threshold', 'saved-queue-margin', 'saved-queue-origin-choice']) {
      controls.push({action: 'U015.' + action, dimension: 'reopen', actual_reload: true,
        abandoned_inputs: {evaluation: unsentEvaluation, threshold: .7, margin: .11},
        reset_inputs: {evaluation: defaultEvaluation, threshold: .5, margin: .05}, saved_queue_unchanged: true, queue_POSTs: 0});
    }
    await page.route('**' + queueEndpoint, failQueueRead);
    try {
      const waiting = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
      await open.click(); expect((await waiting).status()).toBe(503);
      await expect(panel.getByRole('alert')).toContainText('Controlled exact saved queue GET failure');
      await expect(panel).toContainText('다음: error.png'); await expect(panel).toContainText('검토 진행 0 / 3');
      await expect(open).toBeEnabled(); await expect(queueChoice).toHaveValue(queue.id);
      for (const [name, root] of Object.entries(roots)) expect(tree(root)).toEqual(protectedTrees[name]);
      expect(writes).toEqual([]); await capture('original-queue-retained-on-exact-get-503', panel.getByRole('alert'));
    } finally {await page.unroute('**' + queueEndpoint, failQueueRead);}
    expect(controlledQueueGETs).toBe(1); await unchanged();
    const openExactOriginal = async (suffix: string) => {
      // Choosing a different real original first guarantees the next open must
      // read and decode the queue's exact file instead of reusing current pixels.
      const other = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: 'disagreement.png', exact: true});
      await other.click(); await expect(other.locator('..')).toHaveClass(/border-blue-500/);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
      const rawEndpoint = '/api/dataset/raw/' + encodeURIComponent(path.basename(target.file_path));
      const rawWaiting = page.waitForResponse(response => {const address = new URL(response.url());
        return response.request().method() === 'GET' && address.pathname === rawEndpoint && address.searchParams.get('file_path') === target.file_path;});
      const annotationWaiting = page.waitForResponse(response => {const address = new URL(response.url());
        return response.request().method() === 'GET' && address.pathname === '/api/annotations/error' && address.searchParams.get('file_path') === target.file_path;});
      const queueWaiting = page.waitForResponse(response => response.request().method() === 'GET' && new URL(response.url()).pathname === queueEndpoint);
      await open.click(); const realQueue = await queueWaiting; expect(realQueue.status()).toBe(200); expect(await realQueue.json()).toEqual(queueBefore);
      const raw = await rawWaiting, annotation = await annotationWaiting; expect(raw.status()).toBe(200); expect(annotation.status()).toBe(200);
      const rawBytes = await raw.body(), openedAnnotation = await annotation.json();
      expect(rawBytes).toEqual(fs.readFileSync(target.file_path)); expect(sha(rawBytes)).toBe(target.content_hash);
      expect(openedAnnotation).toEqual(annotationsBefore[originals.findIndex(original => original.path === target.file_path)]);
      expect(openedAnnotation.metadata).toMatchObject({image_uuid: target.image_uuid, file_path: target.file_path,
        content_hash: target.content_hash, revision: target.revision});
      const selected = page.locator('[data-labeling-filmstrip]').getByRole('img', {name: 'error.png', exact: true});
      await expect(selected.locator('..')).toHaveClass(/border-blue-500/);
      expect(new URL((await selected.getAttribute('src'))!, page.url()).searchParams.get('file_path')).toBe(target.file_path);
      await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved'); await expect(panel.getByRole('alert')).toHaveCount(0);
      await unchanged(); await capture(suffix, page.locator('[data-labeling-work-area]'));
      const retainedRaw = path.join(w.logs, 'queue-open-' + suffix + '.png'); fs.writeFileSync(retainedRaw, rawBytes); e.addFile(retainedRaw);
      return {real_queue_status: 200, raw_status: 200, annotation_status: 200, image_uuid: target.image_uuid,
        path: target.file_path, sha256: sha(rawBytes), complete_original_byte_equality: true,
        revision: target.revision, annotation_url: annotation.url(), raw_url: raw.url()};
    };
    const retry = await openExactOriginal('real-200-retry-exact-original');
    controls.push({action: 'U015.saved-queue-open', dimension: 'error', exact_scoped_GET_503_count: controlledQueueGETs,
      saved_queue_cursor_history_and_files_unchanged: true, retry});
    await reloadQueue(); await defaults(); await unchanged();
    const reopened = await openExactOriginal('reload-reselected-queue-exact-original');
    controls.push({action: 'U015.saved-queue-open', dimension: 'reopen', actual_reload: true,
      actual_saved_queue_id: queue.id, reopened, cursor: 0, revision: 1, history: [], queue_advanced: false});
    expect(controls).toHaveLength(9); expect(writes).toEqual([]); await unchanged();
    const afterFile = path.join(w.logs, 'queue-lifecycle-protected-after.json'); fs.writeFileSync(afterFile,
      JSON.stringify({trees: Object.fromEntries(Object.entries(roots).map(([name, root]) => [name, tree(root)])),
        complete_queue: await api(queueEndpoint), metadata: (await api('/api/dataset/metadata?limit=100')).items,
        annotations: await Promise.all(originals.map(original => api(annotationRoute(original.path)))), writes}, null, 2)); e.addFile(afterFile);
    e.note('saved_queue_lifecycle', {cells: controls, baseline, origin, alternate, sourceElectron,
      actual_source_ui: true, protected_files_unfiltered: true, all_api_mutations: writes, queueReads,
      explicit_cancel_dialog_exists: false, cancellation_is_real_stage_abandonment: true,
      labels_saved_after_baseline: false, queue_created_or_advanced_after_baseline: false,
      controlled_reports_not_model_inference: true, human_annotation_or_quality_approval: false,
      actual_training_inference_or_gpu: false, physical_device_or_frozen_package_or_windows_acceptance: false});
  } finally {page.off('request', observe); if (!page.isClosed()) await page.unroute('**' + queueEndpoint, failQueueRead);}
}

test('unsent saved queue choices reset after abandonment and reload while failed open retains original queue', async ({page, renderer, workspace, evidence}) => {
  await installDesktopHostShim(page, renderer.port);
  const api: Api = async (route, body, method) => {
    const response = await page.request.fetch(renderer.origin + route, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {data: body})}); expect(response.ok(), await response.text()).toBe(true); return response.json();
  };
  await exercise(page, workspace, evidence, api, false, renderer.url);
});
test('native unsent queue choices and saved queue exact original open preserve complete source labels and history', {tag: '@electron'}, async ({electronSession, workspace, evidence}) => {
  const page = electronSession.window, backend = await electronSession.waitForBackend();
  const api: Api = (route, body, method) => page.evaluate(async ({port, route, body, method}) => {
    const response = await fetch(`http://127.0.0.1:${port}${route}`, {method: method || (body === undefined ? 'GET' : 'POST'),
      ...(body === undefined ? {} : {headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)})});
    if (!response.ok) throw Error(`Owned queue lifecycle API ${response.status}: ${await response.text()}`); return response.json();
  }, {port: backend.port, route, body, method});
  await exercise(page, workspace, evidence, api, true);
});



// Additional SOURCE-only three saved U015 input handoffs. Root alone executes.
import type {Response as QueueInputResponse} from '@playwright/test';
import * as queueInputHandoff from './fixtures/remaining-project-handoff';
type QueueInputScope={tag:'A'|'B';source:string;project:any;inputs:any[];metadata:any[];annotations:Record<string,any>;selectedOrigin:any;defaultOrigin:any;setupQueue:any;threshold:number;margin:number;queue?:any};
const queueInputAnnotationRoute=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
const queueInputHash=(bytes:Buffer|string)=>createHash('sha256').update(bytes).digest('hex');
const queueInputStat=(s:fs.Stats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeMs,s.ctimeMs];
function queueInputFile(file:string){
 const before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown,failed=false;
 try{const opened=fs.fstatSync(fd);expect(queueInputStat(opened)).toEqual(queueInputStat(before));const bytes=fs.readFileSync(fd);expect(bytes.length).toBe(opened.size);
  expect(queueInputStat(fs.fstatSync(fd))).toEqual(queueInputStat(opened));expect(queueInputStat(fs.lstatSync(file))).toEqual(queueInputStat(opened));
  return {bytes,pin:{path:file,sha256:queueInputHash(bytes),size:bytes.length,raw7:queueInputStat(opened)}};
 }catch(error){failed=true;primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!failed)throw error;void primary;}}
}
function queueInputSave(w:Workspace,e:Evidence,label:string,bytes:Buffer){
 const file=path.join(w.logs,label),fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);let failed=false;
 try{expect(fs.writeSync(fd,bytes,0,bytes.length)).toBe(bytes.length);fs.fsyncSync(fd);}catch(error){failed=true;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!failed)throw error;}}
 const result=queueInputFile(file);expect(result.bytes).toEqual(bytes);e.addFile(file);return result.pin;
}
function queueInputTree(root:string){
 const rows:Record<string,any>={};
 const visit=(file:string,relative:string)=>{const info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
  if(info.isDirectory()){rows[relative]={kind:'directory',raw7:queueInputStat(info)};const names=fs.readdirSync(file).sort();for(const name of names)visit(path.join(file,name),relative?relative+'/'+name:name);}
  else{expect(info.isFile()).toBe(true);const original=queueInputFile(file);rows[relative]={kind:'file',...original.pin,path:relative};}};
 visit(root,'');return {root,rows};
}
function queueInputRoots(scope:QueueInputScope){
 const result:Record<string,any>={},roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir};let first:unknown,hasFailure=false;
 for(const [kind,root] of Object.entries(roots)){try{result[kind]=queueInputTree(root as string);}catch(error){if(!hasFailure){hasFailure=true;first=error;}}}
 if(hasFailure)throw first;return result;
}
function queueInputOnlyOwnCreation(scope:QueueInputScope,before:any,after:any,queue:any){
 expect(Object.keys(after)).toEqual(Object.keys(before));const storage='data_workbench/'+queueInputHash(fs.realpathSync(scope.source)).slice(0,24),parent=storage+'/review_queues',added=parent+'/'+queue.id+'.json';
 for(const kind of Object.keys(before)){expect(after[kind].root).toBe(before[kind].root);const old=before[kind].rows,next=after[kind].rows;
  expect(Object.keys(next).filter(name=>!(name in old))).toEqual(kind==='dataset'?[added]:[]);expect(Object.keys(old).filter(name=>!(name in next))).toEqual([]);
  for(const name of Object.keys(old)){if(kind==='dataset'&&name===parent){expect(next[name].kind).toBe('directory');expect(next[name].raw7.slice(0,3)).toEqual(old[name].raw7.slice(0,3));}
   else expect(next[name]).toEqual(old[name]);}}
 const saved=queueInputFile(path.join(scope.project.dataset_dir,added));expect(JSON.parse(saved.bytes.toString('utf8'))).toEqual(queue);return {relative_path:added,pin:saved.pin,only_allowed_parent_metadata:parent};
}
async function queueSavedInputsProjectHandoff(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
 const api=queueInputHandoff.handoffApi(page,origin,false),scopes:QueueInputScope[]=[],before:Record<string,any>={},saved:Record<string,any>={},after:Record<string,any>={},creations:Record<string,any>={},reads:any[]=[],transitions:any[]=[],wireProofs:any[]=[],writes:any[]=[],background422:any[]=[],diagnosticErrors:any[]=[];
 const requestTimes=new Map<Request,{started:number;deadline:number}>(),pending=new Set<Promise<{ok:true;value:any}|{ok:false;error:unknown}>>(),diagnosticPending=new Set<Promise<void>>();
 let primary:unknown,failed=false,completed=false,late:Awaited<ReturnType<typeof queueInputHandoff.handoffLateRead>>|undefined,lateRequest:Request|undefined,lateArmed=false;
 const recordRequest=(request:Request)=>{const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.frame()===page.mainFrame()){const now=performance.now();requestTimes.set(request,{started:now,deadline:now+10_000});if(lateArmed&&request.method()==='GET'&&u.pathname==='/api/data-workbench/review-queues')lateRequest=request;}
  if(!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});};
 const background=(response:QueueInputResponse)=>{if(response.status()!==422||new URL(response.url()).origin!==origin)return;let work:Promise<void>;
  work=(async()=>{const request=response.request(),timing=requestTimes.get(request),deadline=timing?.deadline??performance.now()+10_000,u=new URL(response.url());
   const bytes=await queueInputHandoff.handoffWithin(response.body(),deadline,'bounded background422 complete body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);
   const finished=await queueInputHandoff.handoffWithin(response.finished(),deadline,'background422 finished');expect(finished).toBeNull();
   const project=await queueInputHandoff.handoffWithin(request.headerValue('x-vision-project'),deadline,'background422 project header');
   const context=await queueInputHandoff.handoffWithin(request.headerValue('x-vision-context'),deadline,'background422 context header');
   const raw=queueInputSave(w,e,'queue-input-background422-'+background422.length+'.json',bytes);
   background422.push({method:request.method(),path:u.pathname,query:u.search,status:422,main_frame:request.frame()===page.mainFrame(),project_header:project,context:context===null?null:JSON.parse(context),response:raw,cause_not_inferred:true});
  })().catch(error=>{diagnosticErrors.push({type:error instanceof Error?error.name:typeof error,failed_complete_422_observation:true});}).finally(()=>{diagnosticPending.delete(work);});diagnosticPending.add(work);};
 const wire=(scope:QueueInputScope,method:string,endpoint:string,expected:unknown,label:string,requireProject=true)=>{
  const deadline=performance.now()+10_000,waiting=page.waitForRequest(request=>{const u=new URL(request.url());return request.frame()===page.mainFrame()&&u.origin===origin&&request.method()===method&&u.pathname===endpoint&&u.search===''&&(method==='GET'||JSON.stringify(request.postDataJSON())===JSON.stringify(expected));},{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
  const outcome=(async()=>{const request=await queueInputHandoff.handoffWithin(waiting,deadline,'original queue input request'),timing=requestTimes.get(request);expect(timing).toBeTruthy();const bound=Math.min(deadline,timing!.deadline);
   const projectHeader=await queueInputHandoff.handoffWithin(request.headerValue('x-vision-project'),bound,'original request project header'),contextHeader=await queueInputHandoff.handoffWithin(request.headerValue('x-vision-context'),bound,'original request context header'),context=contextHeader===null?null:JSON.parse(contextHeader);
   if(requireProject){expect(projectHeader).toBe(scope.project.id);expect(context).toMatchObject({project_id:scope.project.id});}else{expect(projectHeader).toBeNull();expect(context).toBeNull();}
   if(method!=='GET')expect(request.postDataJSON()).toEqual(expected);
   const response=await queueInputHandoff.handoffWithin(request.response(),bound,'original queue input response');expect(response).not.toBeNull();expect(response!.request()).toBe(request);expect(response!.status()).toBe(200);
   const bytes=await queueInputHandoff.handoffWithin(response!.body(),bound,'original queue complete body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);expect(await queueInputHandoff.handoffWithin(response!.finished(),bound,'original queue finished')).toBeNull();
   const raw=queueInputSave(w,e,'queue-input-'+label+'-original-response.json',bytes),proof={method,path:endpoint,query:'',origin,owning_project_id:scope.project.id,project_header:projectHeader,context,main_frame:true,status:200,request_body:method==='GET'?null:expected,request_started_ms:timing!.started,absolute_deadline_ms:bound,finished_ms:performance.now(),raw};wireProofs.push(proof);return {body:JSON.parse(bytes.toString('utf8')),proof};
  })().then(value=>({ok:true as const,value}),error=>({ok:false as const,error}));pending.add(outcome);
  return async()=>{const result=await outcome;pending.delete(outcome);if(!result.ok)throw result.error;return result.value;};
 };
 const panel=()=>page.getByRole('region',{name:'저장된 검토 큐',exact:true}),choice=()=>panel().getByLabel('저장 검토 큐 선택',{exact:true});
 const enter=async(scope:QueueInputScope,expectedId=scope.queue?.id??scope.setupQueue.id)=>{
  const deadline=performance.now()+10_000,remaining=()=>Math.max(1,Math.floor(deadline-performance.now()));
  await queueInputHandoff.handoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click(),deadline,'ordinary labeling entry');
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await queueInputHandoff.handoffWithin(focus.click(),deadline,'ordinary focus exit');
  const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await queueInputHandoff.handoffWithin(summary.click(),deadline,'ordinary queue details');
  await queueInputHandoff.handoffWithin(expect(panel()).toBeVisible({timeout:remaining()}),deadline,'queue panel visible');
  await queueInputHandoff.handoffWithin(expect(choice()).toHaveValue(expectedId,{timeout:remaining()}),deadline,'own remembered queue');
  for(const originRow of [scope.selectedOrigin,scope.defaultOrigin])await queueInputHandoff.handoffWithin(expect(panel().getByLabel('검토 큐 원본 평가',{exact:true}).locator('option[value="'+originRow.record.evaluation_id+'"]')).toHaveCount(1,{timeout:remaining()}),deadline,'own evaluation option');
  const other=scopes.find(row=>row.tag!==scope.tag);if(other)for(const originRow of [other.selectedOrigin,other.defaultOrigin])await expect(panel().getByLabel('검토 큐 원본 평가',{exact:true}).locator('option[value="'+originRow.record.evaluation_id+'"]')).toHaveCount(0,{timeout:remaining()});
 };
 const state=async(scope:QueueInputScope)=>{
  const body:Record<string,any>={};for(const endpoint of ['/api/project/current','/api/project/labelsets','/api/project/preferences','/api/team-data','/api/team-data/readiness','/api/dataset/versions','/api/dataset/metadata?limit=100','/api/dataset/metadata/split','/api/data-workbench/review-evaluations'])body[endpoint]=await api(endpoint);
  for(const input of scope.inputs)body[queueInputAnnotationRoute(input.path)]=await api(queueInputAnnotationRoute(input.path));
  body.catalog=await api('/api/data-workbench/review-queues');for(const q of body.catalog.queues)body[q.id]=await api('/api/data-workbench/review-queues/'+q.id);
  return {roots:queueInputRoots(scope),API:body,project_json:queueInputFile(path.join(scope.project.project_dir,'project.json')).pin,labelsets_json:queueInputFile(path.join(scope.project.project_dir,'labelsets.json')).pin};
 };
 const owningChoice=async(scope:QueueInputScope)=>{
  expect(scope.queue).toBeTruthy();await expect(choice()).toHaveValue(scope.queue.id,{timeout:10_000});await expect(panel()).toContainText('검토 진행 0 / '+scope.queue.items.length,{timeout:10_000});
  const other=scopes.find(row=>row.tag!==scope.tag)!;for(const q of [other.setupQueue,other.queue].filter(Boolean))await expect(choice().locator('option[value="'+q.id+'"]')).toHaveCount(0);
  await expect(panel().getByRole('button',{name:'현재 항목 열기',exact:true})).toBeEnabled();await expect(panel().getByRole('alert')).toHaveCount(0);
  const rows=panel().locator('ol').locator('li');await expect(rows).toHaveCount(scope.queue.items.length);
  for(let i=0;i<scope.queue.items.length;i++){await expect(rows.nth(i)).toContainText((i+1)+'. '+scope.queue.items[i].relative_path);await expect(rows.nth(i)).toContainText('대기');}
  if(scope.tag==='A')await expect(rows.nth(0)).toContainText('오류 · 임계값 근처');else await expect(rows.nth(0)).toContainText('모델 불일치 · 임계값 근처');
  const remembered=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu-review-queue:')));
  const own=remembered.filter(([key])=>{const parts=JSON.parse(key.slice('modu-review-queue:'.length));return parts[0]===scope.project.project_dir&&parts[1]===scope.project.id&&parts[2]===scope.project.task&&parts[3]===scope.source&&parts[4]==='default';});
  expect(own).toHaveLength(1);expect(own[0][1]).toBe(scope.queue.id);
  return {remembered:own[0],visible_items:await rows.allTextContents(),unsent_controls:{evaluation:await panel().getByLabel('검토 큐 원본 평가',{exact:true}).inputValue(),threshold:await panel().getByLabel('검토 큐 임계값',{exact:true}).inputValue(),margin:await panel().getByLabel('검토 큐 임계 주변 범위',{exact:true}).inputValue()},unsent_control_values_not_claimed_persisted:true};
 };
 const openProject=async(scope:QueueInputScope,label:string)=>{
  const response=wire(scope,'POST','/api/project/open',{project_dir:scope.project.project_dir},label,false),actual=await queueInputHandoff.handoffProject(page,scope,origin),full=await response();expect(full.body).toEqual(scope.project);expect(full.proof.raw.sha256).toBe(actual.response_sha256);transitions.push({...actual,original_response:full.proof});return actual;
 };
 const readQueue=async(scope:QueueInputScope,label:string)=>{
  const response=wire(scope,'GET','/api/data-workbench/review-queues/'+scope.queue.id,null,label),deadline=performance.now()+10_000;
  await queueInputHandoff.handoffWithin(panel().getByRole('button',{name:'현재 항목 열기',exact:true}).click(),deadline,'own saved queue read');
  const actual=await response();expect(actual.body).toEqual(scope.queue);const selected=await owningChoice(scope);reads.push({tag:scope.tag,queue:actual.proof,selected});return actual;
 };
 const createOwn=async(scope:QueueInputScope,label:string)=>{
  const evaluation=panel().getByLabel('검토 큐 원본 평가',{exact:true});await expect(evaluation).toHaveValue(scope.defaultOrigin.record.evaluation_id);
  await evaluation.selectOption(scope.selectedOrigin.record.evaluation_id);await panel().getByLabel('검토 큐 임계값',{exact:true}).fill(String(scope.threshold));await panel().getByLabel('검토 큐 임계 주변 범위',{exact:true}).fill(String(scope.margin));
  await expect(evaluation).toHaveValue(scope.selectedOrigin.record.evaluation_id);await expect(panel().getByLabel('검토 큐 임계값',{exact:true})).toHaveValue(String(scope.threshold));await expect(panel().getByLabel('검토 큐 임계 주변 범위',{exact:true})).toHaveValue(String(scope.margin));
  const requestBody={evaluation_id:scope.selectedOrigin.record.evaluation_id,threshold:scope.threshold,margin:scope.margin},response=wire(scope,'POST','/api/data-workbench/review-queues',requestBody,label),deadline=performance.now()+10_000;
  await queueInputHandoff.handoffWithin(panel().getByRole('button',{name:'우선순위 큐 저장',exact:true}).click(),deadline,'actual owning queue input save');const actual=await response(),queue=actual.body;scope.queue=queue;
  expect(queue.id).toMatch(/^review_[0-9a-f]{32}$/);expect(queue.id).not.toBe(scope.setupQueue.id);expect(queue).toMatchObject({schema_version:1,scope:{source:scope.source,task:'segmentation',labelset_id:'default'},threshold:scope.threshold,margin:scope.margin,cursor:0,revision:1,history:[],origin:{evaluation_id:scope.selectedOrigin.record.evaluation_id,evidence_sha256:scope.selectedOrigin.record.evidence_sha256,job_id:'controlled-review-ui',step:4}});
  expect(queue.origin.evaluation_id).not.toBe(scope.defaultOrigin.record.evaluation_id);
  const expected=scope.tag==='A'?[['error.png',['error','threshold'],400],['disagreement.png',['disagreement'],200],['threshold.png',['threshold'],100]]:[['disagreement.png',['disagreement','threshold'],300],['error.png',['error'],300]];
  expect(queue.items.map((row:any)=>[row.relative_path,row.reasons,row.priority])).toEqual(expected);
  for(const row of queue.items){expect(row.state).toBe('pending');const input=scope.inputs.find(item=>item.path===row.file_path),metadata=scope.metadata.find(item=>item.file_path===row.file_path);expect(input).toBeTruthy();expect(metadata).toBeTruthy();expect(row.source_sha256).toBe(input.sha256);expect(metadata.content_hash).toBe(row.source_sha256);expect(metadata.image_uuid).toBeTruthy();}
  await owningChoice(scope);const current=await state(scope),ownNewFile=queueInputOnlyOwnCreation(scope,before[scope.tag].roots,current.roots,queue);
  for(const key of Object.keys(before[scope.tag].API))if(key!=='catalog'&&key!==scope.setupQueue.id)expect(current.API[key]).toEqual(before[scope.tag].API[key]);
  expect(current.API[scope.setupQueue.id]).toEqual(before[scope.tag].API[scope.setupQueue.id]);expect(current.project_json).toEqual(before[scope.tag].project_json);expect(current.labelsets_json).toEqual(before[scope.tag].labelsets_json);
  expect(current.API.catalog).toEqual({queues:[queue,scope.setupQueue]});expect(current.API[queue.id]).toEqual(queue);creations[scope.tag]={request:requestBody,response:actual.proof,ownNewFile,saved_queue:queue};saved[scope.tag]=current;e.addFile(ownNewFile.pin.path);return queue;
 };
 try{
  for(const tag of ['A','B'] as const){
   const source=path.join(w.root,'queue-saved-inputs-'+tag);fs.mkdirSync(source);const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,(tag==='A'?50:150)+index]),{flag:'wx'});return {name,path:file,sha256:queueInputFile(file).pin.sha256};});
   const made=await api('/api/project/create',{name:'Owned queue saved inputs '+tag,task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation',validate_images:false});
   for(const row of inputs)await api('/api/annotations/save',{image_id:row.name,image_path:row.path,image_width:64,image_height:64,actor:'queue-saved-inputs-controlled-'+tag,annotations:[{id:'queue-saved-inputs-'+tag+'-'+row.name,type:'bbox',label:'Defect',category_id:1,bbox:[2,3,12,13]}]});
   const seed=()=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,made.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
   const selectedOrigin=seed(),defaultOrigin=seed();expect(selectedOrigin.controlled_reports_not_model_inference).toBe(true);expect(defaultOrigin.controlled_reports_not_model_inference).toBe(true);expect(selectedOrigin.record.evaluation_id).not.toBe(defaultOrigin.record.evaluation_id);expect(selectedOrigin.record.created_at).toBeLessThan(defaultOrigin.record.created_at);
   const setupQueue=await api('/api/data-workbench/review-queues',{evaluation_id:defaultOrigin.record.evaluation_id,threshold:.1,margin:.01}),metadata:any[]=[],annotations:Record<string,any>={};
   for(const endpoint of ['/api/team-data','/api/team-data/readiness','/api/project/preferences','/api/project/labelsets','/api/dataset/versions','/api/dataset/metadata/split'])await api(endpoint);
   for(const input of inputs){const row=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(input.path));expect(row.content_hash).toBe(input.sha256);expect(row.image_uuid).toBeTruthy();expect(row.workflow_state).not.toBe('approved');metadata.push(row);annotations[input.path]=await api(queueInputAnnotationRoute(input.path));}
   expect((await api('/api/dataset/metadata?limit=100')).items).toHaveLength(3);
   scopes.push({tag,source,project:await api('/api/project/current'),inputs,metadata,annotations,selectedOrigin,defaultOrigin,setupQueue,threshold:tag==='A'?.5:.95,margin:tag==='A'?.05:.01});
  }
  const A=scopes[0],B=scopes[1];expect(A.project.id).not.toBe(B.project.id);expect(A.metadata.some(a=>B.metadata.some(b=>a.image_uuid===b.image_uuid))).toBe(false);
  for(const scope of scopes)for(const row of [scope.selectedOrigin,scope.defaultOrigin])expect(queueInputFile(row.path).pin.sha256).toBe(row.sha256);
  for(const scope of scopes){for(const input of scope.inputs)e.addFile(input.path);for(const report of [scope.selectedOrigin,scope.defaultOrigin])e.addFile(report.path);}
  await page.goto(url);await enter(B);before.B=await state(B);await queueInputHandoff.handoffProject(page,A,origin);await enter(A);before.A=await state(A);
  page.on('request',recordRequest);page.on('response',background);
  await createOwn(A,'A-create');await e.screenshot(page,'queue-saved-inputs-A-own-origin-threshold-margin');
  await openProject(B,'to-B-create');await enter(B);expect(await state(B)).toEqual(before.B);await createOwn(B,'B-create');await e.screenshot(page,'queue-saved-inputs-B-different-origin-threshold-margin');
  expect(A.queue.items).toHaveLength(3);expect(B.queue.items).toHaveLength(2);expect(A.queue.origin.evaluation_id).not.toBe(B.queue.origin.evaluation_id);expect(A.queue.origin.evidence_sha256).not.toBe(B.queue.origin.evidence_sha256);expect(A.queue.threshold).not.toBe(B.queue.threshold);expect(A.queue.margin).not.toBe(B.queue.margin);
  await openProject(A,'to-A-before-late');await enter(A);await readQueue(A,'A-before-late');
  late=await queueInputHandoff.handoffLateRead(page,origin,false,u=>u.pathname==='/api/data-workbench/review-queues');lateArmed=true;
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const captured=await late.ready();lateArmed=false;expect(captured.body).toEqual({queues:[A.queue,A.setupQueue]});expect(lateRequest).toBeTruthy();expect(await lateRequest!.headerValue('x-vision-project')).toBe(A.project.id);expect(JSON.parse((await lateRequest!.headerValue('x-vision-context'))!)).toMatchObject({project_id:A.project.id});
  const lateRaw=queueInputSave(w,e,'queue-input-late-A-original-catalog.json',captured.bytes);
  await openProject(B,'to-B-late');await enter(B);await readQueue(B,'B-before-old-A');const B_beforeOld=await owningChoice(B),lateOutcome=await late.finish();expect(await owningChoice(B)).toEqual(B_beforeOld);expect(await state(B)).toEqual(saved.B);
  await late.close();late=undefined;await e.screenshot(page,'queue-saved-inputs-B-after-old-A-catalog');
  await openProject(A,'to-A-return');await enter(A);const catalog=wire(A,'GET','/api/data-workbench/review-queues',null,'A-document-reopen');await page.reload();await enter(A);expect((await catalog()).body).toEqual({queues:[A.queue,A.setupQueue]});await readQueue(A,'A-own-return');expect(await state(A)).toEqual(saved.A);await e.screenshot(page,'queue-saved-inputs-A-saved-origin-threshold-margin-return');
  await openProject(B,'to-B-final');await enter(B);after.B=await state(B);expect(after.B).toEqual(saved.B);
  await openProject(A,'to-A-final');await enter(A);after.A=await state(A);expect(after.A).toEqual(saved.A);
  expect(transitions).toHaveLength(6);expect(writes).toEqual([{method:'POST',path:'/api/data-workbench/review-queues',body:creations.A.request},{method:'POST',path:'/api/project/open',body:{project_dir:B.project.project_dir}},{method:'POST',path:'/api/data-workbench/review-queues',body:creations.B.request},...[A,B,A,B,A].map(s=>({method:'POST',path:'/api/project/open',body:{project_dir:s.project.project_dir}}))]);
  for(const scope of scopes){for(const row of scope.inputs)expect(queueInputFile(row.path).pin.sha256).toBe(row.sha256);for(const row of [scope.selectedOrigin,scope.defaultOrigin])expect(queueInputFile(row.path).pin.sha256).toBe(row.sha256);expect(scope.queue).toMatchObject({cursor:0,revision:1,history:[]});}
  wireProofs.push({late_A:captured.request_identity,captured_original_response:lateRaw,actual_disposition:lateOutcome,old_A_did_not_replace_B:true});completed=true;
 }catch(error){failed=true;primary=error;}
 finally{
  for(const close of [async()=>{page.off('request',recordRequest);},async()=>{page.off('response',background);},async()=>{if(late)await late.close();},async()=>{for(const item of pending){const result=await item;pending.delete(item);if(!result.ok&&primary===undefined)primary=result.error;}},async()=>{for(const item of [...diagnosticPending])await item;}]){try{await close();}catch(error){if(primary===undefined)primary=error;}}
  for(const scope of scopes){try{after[scope.tag]=queueInputRoots(scope);if(saved[scope.tag])expect(after[scope.tag]).toEqual(saved[scope.tag].roots);else if(before[scope.tag])expect(after[scope.tag]).toEqual(before[scope.tag].roots);}catch(error){if(primary===undefined)primary=error;}}
  const proof={cells:['U015.saved-queue-threshold.handoff','U015.saved-queue-margin.handoff','U015.saved-queue-origin-choice.handoff'],scopes,creations,reads,transitions,wireProofs,writes,before,saved,after,background422,diagnosticErrors,all_postroles_attempted:true,full_declared_root_names_bytes_raw_stat:true,all_success_assertions_completed:completed,only_two_own_new_queue_files_before_read_baseline:completed,saved_cursor_revision_history_unchanged:completed,unsent_controls_not_claimed_persisted:true,old_queue_open_selected_reopen_review_skip_create_cells_not_new_credit:true,controlled_reports_not_inference_or_human_truth:true,no_training_model_activation_quality_human_installed_parent_acceptance:true,primary:primary===undefined?null:String(primary),original_test_error_preserved:failed};
  try{queueInputSave(w,e,'queue-saved-inputs-handoff-proof.json',Buffer.from(JSON.stringify(proof,null,2)));}catch(error){if(primary===undefined)primary=error;}
  try{e.note('saved_queue_input_project_handoff',proof);}catch(error){if(primary===undefined)primary=error;}
 }
 if(primary!==undefined)throw primary;
}
test('saved queue threshold margin and selected origin stay bound through A B A project handoff',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);await installDesktopHostShim(page,renderer.port);await queueSavedInputsProjectHandoff(page,workspace,evidence,renderer.origin,renderer.url);
});

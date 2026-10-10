import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFileSync } from 'node:child_process';
import type { Page } from '@playwright/test';
import { test, expect, type Workspace, type Evidence } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { keyboardAction as key } from './fixtures/keyboard-action';
const harness = require('./fixtures/harness.cjs');
type Api = (route: string, body?: any) => Promise<any>;
const ids = (page: Page) => page.locator('[data-flow-node-id]').evaluateAll(nodes => nodes.map(n => n.getAttribute('data-flow-node-id')!));
async function exercise(page: Page, workspace: Workspace, evidence: Evidence, api: Api, resize: (width: number, height: number) => Promise<void>, url?: string) {
    const fixture = JSON.parse(execFileSync(harness.resolvePython(), [path.join(harness.REPO_ROOT, 'scripts/e2e/fixtures/canvas_source.py'), workspace.root], { encoding: 'utf8' }));
    await api('/api/project/create', { name: 'Keyboard canvas projection', task: 'anomaly' });
    await api('/api/project/update', { source_dataset_dir: fixture.source });
    const project = await api('/api/project/current');
    await api('/api/dataset/import', { folder_path: fixture.source, task: 'anomaly' });
    if (url)
        await page.goto(url);
    else
        await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText(project.name);
    await page.getByRole('navigation', { name: 'Workflow Stages' }).getByRole('button').nth(4).click();
    await expect(page.getByRole('heading', { name: '검사 플로우 편집기', exact: true })).toBeVisible();
    await expect(page.locator('[data-flow-node-id]')).not.toHaveCount(0);
    const enterQuick = async () => { const summary = page.locator('summary').filter({ hasText: '빠른 노드 추가·연결·실행 자원' }); if (!await summary.evaluate(e => (e.parentElement as HTMLDetailsElement).open))
        await key(page, summary); };
    await enterQuick();
    const before = await ids(page), edges = await page.locator('[data-flow-edge]').count();
    await key(page, page.locator('details').filter({ has: page.locator('summary').filter({ hasText: '빠른 노드 추가·연결·실행 자원' }) }).getByRole('button', { name: '검사 모델', exact: true }));
    await expect.poll(async () => (await ids(page)).length).toBe(before.length + 1);
    const added = (await ids(page)).find(id => !before.includes(id))!;
    const node = page.locator(`[data-flow-node-id="${added}"]`), focus = node.locator('[data-flow-node-focus]');
    await expect(focus).toBeFocused();
    await page.keyboard.press('Space');
    await expect(focus).toHaveAttribute('aria-label', /선택됨/);
    await page.keyboard.press('Tab');
    await expect(node.getByRole('button', { name: /IMAGE\/ROI IN$/ })).toBeFocused();
    const existing = page.locator('[data-flow-node-id]').filter({ has: page.getByRole('button', { name: /RESULT OUT$/ }) }).filter({ hasNot: focus }).first();
    await key(page, existing.getByRole('button', { name: /RESULT OUT$/ }));
    await key(page, node.getByRole('button', { name: /IMAGE\/ROI IN$/ }));
    await expect(page.getByText(/이 입력은 이미지·ROI만 받습니다/)).toBeVisible();
    await key(page, existing.getByRole('button', { name: /IMAGE\/ROI OUT$/ }));
    await key(page, node.getByRole('button', { name: /IMAGE\/ROI IN$/ }));
    await expect.poll(() => page.locator('[data-flow-edge]').count()).toBe(edges + 1);
    await expect(node.getByRole('button').first()).toBeEnabled();
    await key(page, focus, 'Delete');
    await expect(node).toHaveCount(0);
    await expect(page.locator('[data-flow-node-focus]:focus')).toHaveCount(1);
    await expect(page.getByRole('button', { name: '플로우 실행 취소', exact: true })).toBeEnabled();
    await page.keyboard.press('Control+z');
    await expect(node).toHaveCount(1);
    await expect.poll(() => page.locator('[data-flow-edge]').count()).toBe(edges + 1);
    await expect(page.getByRole('button', { name: '플로우 다시 실행', exact: true })).toBeEnabled();
    await page.keyboard.press('Control+Shift+z');
    await expect(node).toHaveCount(0);
    await expect(page.getByRole('button', { name: '플로우 실행 취소', exact: true })).toBeEnabled();
    await page.keyboard.press('Control+z');
    await expect(node).toHaveCount(1);
    const opener = page.getByRole('button', { name: '이미지 변경...', exact: true });
    await key(page, opener);
    const dialog = page.getByRole('dialog', { name: '검사 대상 이미지 선택', exact: true });
    await expect(dialog).toBeVisible();
    const close = dialog.getByRole('button', { name: '닫기 (Esc)', exact: true });
    await expect(close).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(dialog.getByRole('button', { name: '취소', exact: true })).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(close).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();
    await expect(opener).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(dialog).toBeVisible();
    await key(page, dialog.getByRole('button', { name: '로컬 파일', exact: true }));
    await dialog.getByPlaceholder('예: /path/to/inspection-image.png').fill(fixture.path);
    await key(page, dialog.getByRole('button', { name: '선택 확정', exact: true }));
    await expect(dialog).not.toBeVisible();
    await expect(opener).toBeFocused();
    await enterQuick();
    const prior = await ids(page);
    await key(page, page.locator('details').filter({ has: page.locator('summary').filter({ hasText: '빠른 노드 추가·연결·실행 자원' }) }).getByRole('button', { name: '고정 ROI', exact: true }));
    await expect.poll(async () => (await ids(page)).length).toBe(prior.length + 1);
    const roiId = (await ids(page)).find(id => !prior.includes(id))!, roiNode = page.locator(`[data-flow-node-id="${roiId}"]`);
    await expect(roiNode.locator('[data-flow-node-focus]')).toBeFocused();
    const roi = page.getByRole('application', { name: '원본 이미지 ROI 편집', exact: true });
    await expect(roi).toHaveAttribute('viewBox', '0 0 512 256');
    const scales: any[] = [];
    for (const size of [[1366, 768], [1920, 1080]]) {
        await resize(size[0], size[1]);
        for (const scale of [1, 1.25, 1.5, 2]) {
            await page.evaluate(s => document.documentElement.style.zoom = String(s), scale);
            await roi.scrollIntoViewIfNeeded();
            await roi.focus();
            expect(await roi.evaluate(e => getComputedStyle(e).outlineStyle)).not.toBe('none');
            const points = await roi.evaluate(e => { const matrix = (e as SVGSVGElement).getScreenCTM()!; return [[64.25, 32.25], [256.25, 160.25]].map(([x, y]) => { const p = new DOMPoint(x, y).matrixTransform(matrix); return { x: p.x, y: p.y }; }); });
            await page.mouse.move(points[0].x, points[0].y);
            await page.mouse.down();
            await page.mouse.move(points[1].x, points[1].y, { steps: 4 });
            await page.mouse.up();
            const rect = roi.locator('rect');
            await expect(rect).toHaveAttribute('x', '64');
            await expect(rect).toHaveAttribute('y', '32');
            await expect(rect).toHaveAttribute('width', '193');
            await expect(rect).toHaveAttribute('height', '129');
            await roi.focus();
            await page.keyboard.press('ArrowRight');
            await expect(rect).toHaveAttribute('x', '65');
            await page.keyboard.press('Shift+ArrowDown');
            await expect(rect).toHaveAttribute('height', '130');
            await roiNode.scrollIntoViewIfNeeded();
            const start = await roiNode.evaluate(e => ({ x: parseFloat((e as HTMLElement).style.left), y: parseFloat((e as HTMLElement).style.top) }));
            const box = (await roiNode.boundingBox())!, factor = box.width / 272;
            const a = { x: box.x + box.width * .5, y: box.y + 12 * factor };
            await page.mouse.move(a.x, a.y);
            await page.mouse.down();
            await page.mouse.move(a.x + 40 * factor, a.y + 30 * factor, { steps: 4 });
            await page.mouse.up();
            await expect.poll(() => roiNode.evaluate(e => ({ x: parseFloat((e as HTMLElement).style.left), y: parseFloat((e as HTMLElement).style.top) }))).toEqual({ x: start.x + 40, y: start.y + 30 });
            scales.push({ size, scale, roi: [65, 32, 258, 162], node_delta: [40, 30] });
        }
    }
    await page.evaluate(() => document.documentElement.style.zoom = '1');
    const savedDraft = page.waitForResponse(response => new URL(response.url()).pathname === '/api/flowchart/draft' && response.request().method() === 'PUT');
    await key(page, page.getByRole('button', { name: '초안 저장', exact: true }));
    const savedDraftResponse = await savedDraft;
    expect(savedDraftResponse.ok(), await savedDraftResponse.text()).toBe(true);
    await expect.poll(async () => (await api('/api/flowchart/draft')).pipeline.nodes.find((n: any) => n.id === roiId)?.data.params.roi_bbox).toEqual([65, 32, 258, 162]);
    const draft = await api('/api/flowchart/draft');
    await evidence.screenshot(page, 'keyboard-nodes-original-roi-scale');
    await page.reload();
    await expect(page.getByTitle('프로젝트 관리', { exact: true })).toContainText(project.name);
    await page.getByRole('navigation', { name: 'Workflow Stages' }).getByRole('button').nth(4).click();
    await expect(roiNode).toHaveCount(1);
    await key(page, roiNode.locator('[data-flow-node-focus]'));
    await expect(page.getByText('원본 기준 [65, 32, 258, 162]', { exact: false })).toBeVisible();
    const reopened = await api('/api/flowchart/draft');
    expect(reopened.draft_sha256).toBe(draft.draft_sha256);
    expect(crypto.createHash('sha256').update(fs.readFileSync(fixture.path)).digest('hex')).toBe(fixture.sha256);
    evidence.addFile(fixture.path);
    evidence.note('keyboard_canvas_qualification', { fixture, project, draft, reopened, roi_id: roiId, scales, actual_keyboard_edits: true, actual_dialog_focus_restore: true, actual_original_coordinate_drag: true, actual_draft_reopen: true, windows_native_dpi: false, training_or_quality_approval: false });
}
test('keyboard graph and original ROI survive window and display scale changes', async ({ page, request, renderer, workspace, evidence }) => { test.setTimeout(240000); await installDesktopHostShim(page, renderer.port); const api: Api = async (route, body) => { const r = body === undefined ? await request.get(renderer.origin + route) : route.endsWith('/update') ? await request.put(renderer.origin + route, { data: body }) : await request.post(renderer.origin + route, { data: body }); expect(r.ok(), await r.text()).toBe(true); return r.json(); }; await exercise(page, workspace, evidence, api, async (w, h) => page.setViewportSize({ width: w, height: h }), renderer.url); });
test('native keyboard graph and original ROI preserve draft and focus', { tag: '@electron' }, async ({ electronSession, workspace, evidence }) => { test.setTimeout(240000); const { window, app } = electronSession; const backend = await electronSession.waitForBackend(); const api: Api = (route, body) => window.evaluate(async ({ port, route, body }) => { const r = await fetch(`http://127.0.0.1:${port}${route}`, body === undefined ? {} : { method: route.endsWith('/update') ? 'PUT' : 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); if (!r.ok)
    throw Error(`Owned API ${r.status}: ${await r.text()}`); return r.json(); }, { port: backend.port, route, body }); await exercise(window, workspace, evidence, api, async (w, h) => { await app.evaluate(({ BrowserWindow }, { w, h }) => BrowserWindow.getAllWindows()[0].setContentSize(w, h), { w, h }); }); });

import {inflateSync} from 'node:zlib';
import type {Request,Response} from '@playwright/test';
import {png} from './qa/appFlow';
const sha=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
function canonical(value:any):any{return Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;}
function assertDraftRecord(record:any,pipeline:any,context:any){
 expect(Object.keys(record).sort()).toEqual(['active_version_id','base_version_id','context','draft_sha256','pipeline','saved_at_ns','version']);
 expect(record.version).toBe(1);expect(record.context).toEqual(context);expect(record.pipeline).toEqual(pipeline);
 expect(record.draft_sha256).toBe(sha(JSON.stringify(canonical(pipeline))));expect(record.base_version_id).toBe('none');expect(record.active_version_id).toBeNull();
 expect(Number.isFinite(record.saved_at_ns)).toBe(true);expect(record.saved_at_ns).toBeGreaterThan(0);
}

// Exact declared FlowNodeData/FlowNode/FlowEdge defaults in frozen
// backend/engine/flowchart_engine.py. Keep submitted bytes separately; the
// genuine draft response includes these defaults for newly added UI nodes.
function flowFiveNormalized(pipeline:any){
 const nodeKeys=['id','type','position','data'],dataKeys=['label','node_type','task','model_job_id','threshold','score_spec','crop_padding','rule','params'];
 const edgeKeys=['id','source','target','label','isBranch','payload_type','predicate'];
 return {...pipeline,nodes:pipeline.nodes.map((node:any)=>{
  expect(Object.keys(node).every(k=>nodeKeys.includes(k))).toBe(true);expect(Object.keys(node.data).every(k=>dataKeys.includes(k))).toBe(true);
  return {type:'custom',...node,data:{task:'anomaly',model_job_id:null,threshold:.5,score_spec:null,crop_padding:10,rule:'any_defect_is_ng',params:{},...node.data}};
 }),edges:pipeline.edges.map((edge:any)=>{expect(Object.keys(edge).every(k=>edgeKeys.includes(k))).toBe(true);return {label:null,isBranch:null,payload_type:null,predicate:null,...edge};})};
}
function assertAnomalyImport(value:any){
 expect(value).toEqual({status:'success',total_images:2,source_images:2,unlabeled_images:0,classes:{good:2},split:{train:1,val:1,test:0},corrupted_images:[],
  validation:{requested:true,checked_images:2,complete:true,scope:'all images: every image file under the folder was decoded'},split_supported:false,
  split_unavailable_reason:'anomaly 분할은 현재 학습 데이터에 적용되지 않습니다. 원본 데이터의 train/val/test 구성을 사용하세요. / anomaly split is not applied by the training loader; use source train/val/test folders.'});
}

// SOURCE-only append. Root alone executes and qualifies the owned fixture.
import {handoffApi as flowFiveApi, handoffProject as flowFiveProject,
  handoffWithin as flowFiveWithin, handoffSave as flowFiveSave,
  type HandoffApi as FlowFiveApi} from './fixtures/remaining-project-handoff';

type FlowFiveScope={tag:'A'|'B';project:any;source:string;context:any;seed:any;saved:any;
  roiId:string;label:string;draftFile:string;inspectionId?:string;addedRoiId?:string;addedEdgeId?:string;finalRoi?:number[];finalPolicy?:'review'|'ng';inspectionTemplate?:any;ROI_template?:any;edgeTemplate?:any;images:Array<{path:string;size:number;sha256:string;blue:number}>;readback?:any};
type FlowFiveClock={request:Request;started:number;deadline:number;pending?:Promise<void>;failure?:unknown;
  finished?:number;status?:number;raw?:Buffer;context?:any;project?:string};
const flowFiveRaw7=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);

// Complete declared trees, including their directories and absent roots. No
// lifecycle SQLite stores outside these explicit roots are claimed here.
function flowFiveTree(root:string):Record<string,unknown>{
 const result:Record<string,unknown>={};
 try{fs.lstatSync(root,{bigint:true});}catch(error){if((error as NodeJS.ErrnoException).code==='ENOENT')return{'.':{kind:'absent'}};throw error;}
 const visit=(file:string)=>{
  const named=fs.lstatSync(file,{bigint:true}),identity=flowFiveRaw7(named),member=path.relative(root,file).split(path.sep).join('/')||'.';
  expect(named.isSymbolicLink()).toBe(false);expect(fs.realpathSync(file)).toBe(file);
  if(named.isDirectory()){
   const names=fs.readdirSync(file).sort();result[member]={kind:'directory',identity};
   for(const name of names)visit(path.join(file,name));
   expect(fs.readdirSync(file).sort()).toEqual(names);expect(flowFiveRaw7(fs.lstatSync(file,{bigint:true}))).toEqual(identity);return;
  }
  expect(named.isFile()).toBe(true);expect(named.nlink).toBe(1n);
  const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown;
  try{
   expect(flowFiveRaw7(fs.fstatSync(fd,{bigint:true}))).toEqual(identity);const raw=fs.readFileSync(fd);
   expect(BigInt(raw.length)).toBe(named.size);expect(flowFiveRaw7(fs.fstatSync(fd,{bigint:true}))).toEqual(identity);
   expect(flowFiveRaw7(fs.lstatSync(file,{bigint:true}))).toEqual(identity);result[member]={kind:'file',identity,size:raw.length,sha256:sha(raw)};
  }catch(error){primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)throw error;}}
 };visit(root);return result;
}

function flowFivePixels(raw:Buffer,blue:number){
 let cursor=8,header:Buffer|undefined;const chunks:Buffer[]=[];expect(raw.subarray(0,8)).toEqual(Buffer.from([137,80,78,71,13,10,26,10]));
 while(cursor<raw.length){const count=raw.readUInt32BE(cursor),kind=raw.toString('ascii',cursor+4,cursor+8);expect(cursor+count+12).toBeLessThanOrEqual(raw.length);
  if(kind==='IHDR'){expect(header).toBeUndefined();header=raw.subarray(cursor+8,cursor+8+count);}if(kind==='IDAT')chunks.push(raw.subarray(cursor+8,cursor+8+count));cursor+=count+12;}
 expect(cursor).toBe(raw.length);expect(header).toBeDefined();expect([...header!]).toEqual([...Buffer.from([0,0,1,0,0,0,1,0,8,2,0,0,0])]);
 const actual=inflateSync(Buffer.concat(chunks)),expected=Buffer.alloc(256*769);
 for(let y=0;y<256;y++)for(let x=0;x<256;x++){const offset=y*769+1+x*3;expected[offset]=x;expected[offset+1]=y;expected[offset+2]=blue;}
 expect(actual.equals(expected)).toBe(true);return{width:256,height:256,pixels:65536,channels:3,blue};
}

async function flowFiveMake(api:FlowFiveApi,w:Workspace,tag:'A'|'B',projectRoot:string):Promise<FlowFiveScope>{
 const source=path.join(w.root,'flow5-keyboard-policy-source-'+tag),normal=path.join(source,'train','good');fs.mkdirSync(normal,{recursive:true});
 const images=[0,1].map(index=>{
  const blue=(tag==='A'?100:120)+index,file=path.join(normal,index?'part-1.png':'part.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,blue]),{flag:'wx'});
  const raw=fs.readFileSync(file);flowFivePixels(raw,blue);return{path:file,size:raw.length,sha256:sha(raw),blue};
 });
 const made=await api('/api/project/create',{name:'Owned keyboard and policy handoff '+tag,task:'anomaly'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');assertAnomalyImport(await api('/api/dataset/import',{folder_path:source,task:'anomaly',validate_images:true}));
 const project=await api('/api/project/current');expect(project.id).toBe(made.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(projectRoot);
 const context={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default'},suffix=tag.toLowerCase(),roiId='roi-'+suffix;
 const pipeline={id:'owned-draft-handoff-'+suffix,name:'Owned editable no-model flow '+tag,nodes:[
  {id:'input-'+suffix,position:{x:32,y:170},data:{label:'Input '+tag,node_type:'input'}},
  {id:roiId,position:{x:332,y:170},data:{label:'ROI initial '+tag,node_type:'fixed_roi',params:{roi_bbox:tag==='A'?[2,3,20,21]:[12,13,40,41]}}},
  {id:'decision-'+suffix,position:{x:632,y:170},data:{label:'Decision '+tag,node_type:'decision',rule:'any_defect_is_ng'}},
  {id:'output-'+suffix,position:{x:932,y:170},data:{label:'Output '+tag,node_type:'output'}}],edges:[
  {id:'input-roi-'+suffix,source:'input-'+suffix,target:roiId,payload_type:'image'},
  {id:'roi-decision-'+suffix,source:roiId,target:'decision-'+suffix,payload_type:'roi'},
  {id:'decision-output-'+suffix,source:'decision-'+suffix,target:'output-'+suffix,payload_type:'result'}]};
 const seed=await api('/api/flowchart/draft',{pipeline,context,base_version_id:'none'},'PUT');assertDraftRecord(seed,seed.pipeline,context);
 for(const node of seed.pipeline.nodes)expect(node.data.model_job_id).toBeNull();
 await api('/api/project/labelsets');await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/dataset/metadata?limit=100');
 for(const image of images)await api('/api/annotations/part?file_path='+encodeURIComponent(image.path));
 const draftFile=path.join(project.project_dir,'flowcharts','drafts',context.labelset_id,sha(source).slice(0,16),'draft.json');
 expect(JSON.parse(fs.readFileSync(draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(seed).filter(([key])=>key!=='active_version_id')));
 return{tag,project,source,context,seed,saved:seed,roiId,label:'ROI persisted '+tag,draftFile,images};
}

async function flowFiveExercise(page:Page,w:Workspace,e:Evidence,origin:string,native:boolean,url?:string){
 const projectRoot=native?path.join(w.userData,'projects'):w.projects,api=flowFiveApi(page,origin,native),A=await flowFiveMake(api,w,'A',projectRoot),B=await flowFiveMake(api,w,'B',projectRoot);
 expect(A.project.id).not.toBe(B.project.id);expect(A.project.project_dir).not.toBe(B.project.project_dir);expect(A.source).not.toBe(B.source);
 expect(A.seed.draft_sha256).not.toBe(B.seed.draft_sha256);expect(new Set([...A.images,...B.images].map(row=>row.sha256)).size).toBe(4);
 const controlledFixtureReads:any[]=[],writes:Array<{request:Request;method:string;path:string;body:any}>=[],clocks=new Map<Request,FlowFiveClock>();let primary:unknown,baseline=false;
 const observed=(request:Request)=>{
  const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/')||request.method()==='OPTIONS')return;
  if(!['GET','HEAD'].includes(request.method()))writes.push({request,method:request.method(),path:u.pathname,body:request.postDataJSON()});
  if(u.pathname==='/api/flowchart/draft'){
   // Source Electron fixture readbacks use a separate untagged renderer GET.
   // Only the actual context-bearing product GETs qualify original UI200.
   const headers=request.headers();
   if(request.method()==='GET'&&(!headers['x-vision-project']||!headers['x-vision-context'])){
    expect(native).toBe(true);controlledFixtureReads.push({method:'GET',path:u.pathname,original_UI_HTTP200_provenance:false});return;
   }
   const started=performance.now();clocks.set(request,{request,started,deadline:started+10_000});
  }
 };
 const failed=(request:Request)=>{const row=clocks.get(request);if(row)row.failure=request.failure()?.errorText||'Original draft request failed';};
 const replied=(response:Response)=>{
  const row=clocks.get(response.request());if(!row)return;
  row.pending=(async()=>{try{
   const deadline=row.deadline;row.status=response.status();expect(row.status).toBe(200);expect(row.request.frame()).toBe(page.mainFrame());
   row.project=(await flowFiveWithin(row.request.headerValue('x-vision-project'),deadline,'original draft owner header'))!;
   const context=await flowFiveWithin(row.request.headerValue('x-vision-context'),deadline,'original draft context header');expect(context).not.toBeNull();row.context=JSON.parse(context!);
   row.raw=await flowFiveWithin(response.body(),deadline,'original complete draft response');expect(row.raw.length).toBeLessThanOrEqual(1024*1024);
   const owning=[A,B].find(scope=>scope.project.id===row.project);expect(owning).toBeDefined();expect(row.context.project_id).toBe(row.project);
   const envelope=JSON.parse(row.raw.toString('utf8'));assertDraftRecord(envelope,envelope.pipeline,owning!.context);
   expect(await flowFiveWithin(response.finished(),deadline,'original draft response finished')).toBeNull();row.finished=performance.now();expect(row.finished).toBeLessThanOrEqual(deadline);
  }catch(error){row.failure=error;}})();
 };
 const drain=async()=>{for(const row of clocks.values()){
  if(!row.pending)await flowFiveWithin(expect.poll(()=>Boolean(row.pending||row.failure),{timeout:Math.max(1,row.deadline-performance.now())}).toBe(true),row.deadline,'original draft response arrival');
  expect(row.failure).toBeUndefined();await row.pending;expect(row.failure).toBeUndefined();expect(row.deadline).toBe(row.started+10_000);
  expect(row.raw).toBeDefined();expect(row.finished!).toBeLessThanOrEqual(row.deadline);
 }};
 const rendered=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')].map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
 const view=(pipeline:any)=>({nodes:pipeline.nodes.map((row:any)=>({id:row.id,label:row.data.label,x:row.position.x,y:row.position.y})),edges:pipeline.edges.map((row:any)=>({id:row.id,source:row.source,target:row.target}))});
 const savedStatus=page.getByRole('status').filter({hasText:/^초안 저장됨 · 실행본 활성화 전$/}),save=page.getByRole('button',{name:'초안 저장',exact:true});
 const enter=async(scope:FlowFiveScope,reopened:boolean)=>{
  expect(await api('/api/project/current')).toEqual(scope.project);
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();await expect(page.locator('[data-flow-node-id]')).toHaveCount(scope.saved.pipeline.nodes.length);
  await expect.poll(rendered).toEqual(view(scope.saved.pipeline));await expect(savedStatus).toHaveCount(1);await expect(save).toBeEnabled();
  if(reopened){await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeDisabled();await expect(page.getByRole('button',{name:'플로우 다시 실행',exact:true})).toBeDisabled();}
  await page.getByLabel('플로우 노드 검색',{exact:true}).fill(scope.roiId);await page.getByRole('list',{name:'노드 검색 결과'}).getByRole('button').click();
  await expect(page.getByRole('textbox',{name:'노드 명칭',exact:true})).toHaveValue(scope.saved.pipeline.nodes.find((row:any)=>row.id===scope.roiId).data.label);
  if(scope.inspectionId){
   const other=scope===A?B:A;for(const id of [other.inspectionId,other.addedRoiId])if(id)await expect(page.locator(`[data-flow-node-id="${id}"]`)).toHaveCount(0);
   await key(page,page.locator(`[data-flow-node-id="${scope.inspectionId}"]`).locator('[data-flow-node-focus]'),'Space');
   await expect(page.locator(`[data-flow-node-id="${scope.inspectionId}"] [data-flow-node-focus]`)).toHaveAttribute('aria-label',/선택됨/);
   await expect(page.locator(`[data-flow-edge="${scope.addedEdgeId}"]`)).toHaveCount(1);
   await key(page,page.locator(`[data-flow-node-id="${scope.addedRoiId}"]`).locator('[data-flow-node-focus]'));
   await expect(page.getByText('원본 기준 ['+scope.finalRoi!.join(', ')+']',{exact:false})).toBeVisible();
   await key(page,page.locator(`[data-flow-node-id="decision-${scope.tag.toLowerCase()}"]`).locator('[data-flow-node-focus]'));
   await expect(page.getByLabel('UNKNOWN·미실행·오류 처리',{exact:true})).toHaveValue(scope.finalPolicy!);
  }await drain();
 };
 const responses:any[]=[],snapshots:any[]=[],transitions:any[]=[];
 const rawResponse=(label:string,raw:Buffer)=>{const file=path.join(w.logs,label+'.json');fs.writeFileSync(file,raw,{flag:'wx'});e.addFile(file);return{path:file,size:raw.length,sha256:sha(raw)};};
 const actionProofs:any[]=[];
 const savePrepared=async(scope:FlowFiveScope)=>{
  const own=(id:string)=>page.locator(`[data-flow-node-id="${id}"]`);
  const inputId='input-'+scope.tag.toLowerCase(),decisionId='decision-'+scope.tag.toLowerCase();
  const inspect=(pipeline:any)=>pipeline.nodes.find((n:any)=>n.id===scope.inspectionId);
  const fixed=(pipeline:any)=>pipeline.nodes.find((n:any)=>n.id===scope.addedRoiId);
  const decision=(pipeline:any)=>pipeline.nodes.find((n:any)=>n.id===decisionId);
  const capture=async(response:Response,label:string,check:(pipeline:any)=>void)=>{
   const row=clocks.get(response.request());expect(row).toBeDefined();await drain();
   expect(row!.project).toBe(scope.project.id);expect(row!.context.project_id).toBe(scope.project.id);expect(row!.context.mode).toBe('local');
   expect(typeof row!.context.workspace_id).toBe('string');expect(typeof row!.context.actor_id).toBe('string');
   const body=response.request().postDataJSON();expect(Object.keys(body).sort()).toEqual(['base_version_id','context','pipeline']);
   expect(body.context).toEqual(scope.context);expect(body.base_version_id).toBe('none');
   expect(body.pipeline.id).toBe(scope.seed.pipeline.id);expect(body.pipeline.name).toBe(scope.seed.pipeline.name);
   for(const node of scope.seed.pipeline.nodes){const actual=body.pipeline.nodes.find((n:any)=>n.id===node.id);expect(actual).toBeDefined();
    if(node.id===decisionId){expect({...actual.data,params:node.data.params}).toEqual(node.data);expect(actual.position).toEqual(node.position);}
    else expect(actual).toEqual(node);
   }
   for(const edge of scope.seed.pipeline.edges)expect(body.pipeline.edges.find((r:any)=>r.id===edge.id)).toEqual(edge);
   for(const node of body.pipeline.nodes)expect(node.data.model_job_id??null).toBeNull();
   const normalized=flowFiveNormalized(body.pipeline);check(normalized);const record=JSON.parse(row!.raw!.toString('utf8'));assertDraftRecord(record,normalized,scope.context);scope.saved=record;
   await expect(savedStatus).toHaveCount(1);await expect(save).toBeEnabled();await expect.poll(rendered).toEqual(view(record.pipeline));
   expect(await api('/api/flowchart/draft')).toEqual(record);await drain();
   const disk=fs.readFileSync(scope.draftFile);expect(JSON.parse(disk.toString('utf8'))).toEqual(Object.fromEntries(Object.entries(record).filter(([k])=>k!=='active_version_id')));
   const index=responses.length;responses.push({kind:label,project_id:scope.project.id,context:scope.context,actual_owner_context:row!.context,
    started:row!.started,deadline:row!.deadline,finished:row!.finished,method:'PUT',path:'/api/flowchart/draft',status:200,
    request:body,response:record,raw_response:rawResponse('flow5-'+scope.tag+'-put-'+index,row!.raw!),disk_sha256:sha(disk)});
  };
  const edit=async(label:string,work:()=>Promise<void>,check:(pipeline:any)=>void)=>{
   const deadline=performance.now()+10_000;
   const waiting=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/draft'&&r.request().method()==='PUT'&&r.request().frame()===page.mainFrame(),{timeout:Math.max(1,deadline-performance.now())});
   void waiting.catch(()=>{}); // Own early rejection; the same wire remains awaited below.
   await flowFiveWithin(work(),deadline,label+' actual input');
   await capture(await flowFiveWithin(waiting,deadline,label+' original650ms autosave'),label,check);
  };
  const quick=page.locator('details').filter({has:page.locator('summary').filter({hasText:'빠른 노드 추가·연결·실행 자원'})});
  if(!await quick.evaluate(el=>(el as HTMLDetailsElement).open))await key(page,quick.locator(':scope > summary'));
  await edit('650ms-inspection-keyboard-add-select',async()=>{
   const before=await ids(page);expect(before).toHaveLength(4);
   await key(page,quick.getByRole('button',{name:'검사 모델',exact:true}));await expect.poll(async()=>(await ids(page)).length).toBe(5);
   const added=(await ids(page)).filter(id=>!before.includes(id));expect(added).toHaveLength(1);scope.inspectionId=added[0];
   const focus=own(scope.inspectionId).locator('[data-flow-node-focus]');await expect(focus).toBeFocused();await page.keyboard.press('Space');
   await expect(focus).toHaveAttribute('aria-label',/선택됨/);
  },pipeline=>{expect(pipeline.nodes).toHaveLength(5);expect(inspect(pipeline).data).toMatchObject({node_type:'inspection',task:'anomaly',label:'검사 모델 1',threshold:.5,params:{min_defect_area_px:8}});expect(inspect(pipeline).position).toEqual({x:670,y:80});expect(pipeline.edges).toEqual(scope.seed.pipeline.edges);scope.inspectionTemplate=clone(inspect(pipeline));});
  await edit('650ms-typed-image-port-keyboard-connect',async()=>{
   await key(page,own(inputId).getByRole('button',{name:/IMAGE OUT$/}));
   await key(page,own(scope.inspectionId!).getByRole('button',{name:/IMAGE\/ROI IN$/}));await expect(page.locator('[data-flow-edge]')).toHaveCount(4);
  },pipeline=>{expect(pipeline.nodes).toEqual(scope.saved.pipeline.nodes);expect(pipeline.edges).toHaveLength(4);
   const added=pipeline.edges.filter((r:any)=>!scope.seed.pipeline.edges.some((o:any)=>o.id===r.id));expect(added).toHaveLength(1);expect(added[0]).toMatchObject({source:inputId,target:scope.inspectionId,payload_type:'image'});scope.addedEdgeId=added[0].id;scope.edgeTemplate=clone(added[0]);});
  const opener=page.getByRole('button',{name:'이미지 변경...',exact:true});await key(page,opener);
  const dialog=page.getByRole('dialog',{name:'검사 대상 이미지 선택',exact:true});await expect(dialog).toBeVisible();
  await key(page,dialog.getByRole('button',{name:'로컬 파일',exact:true}));await dialog.getByPlaceholder('예: /path/to/inspection-image.png').fill(scope.images[0].path);
  await key(page,dialog.getByRole('button',{name:'선택 확정',exact:true}));await expect(dialog).toHaveCount(0);await expect(opener).toBeFocused();
  await edit('650ms-fixed-ROI-keyboard-add',async()=>{
   const before=await ids(page);await key(page,quick.getByRole('button',{name:'고정 ROI',exact:true}));await expect.poll(async()=>(await ids(page)).length).toBe(6);
   const added=(await ids(page)).filter(id=>!before.includes(id));expect(added).toHaveLength(1);scope.addedRoiId=added[0];await expect(own(scope.addedRoiId).locator('[data-flow-node-focus]')).toBeFocused();
  },pipeline=>{expect(pipeline.nodes).toHaveLength(6);expect(fixed(pipeline).data).toMatchObject({node_type:'fixed_roi',label:'고정 ROI 2',params:{roi_bbox:[0,0,512,512]}});expect(fixed(pipeline).position).toEqual({x:325,y:320});expect(pipeline.edges).toEqual(scope.saved.pipeline.edges);scope.ROI_template=clone(fixed(pipeline));});
  const roi=page.getByRole('application',{name:'원본 이미지 ROI 편집',exact:true});await expect(roi).toHaveAttribute('viewBox','0 0 256 256');
  const initial=scope.tag==='A'?[16,24,80,88]:[32,40,112,120];
  await edit('650ms-original-pixel-ROI-drag',async()=>{
   await roi.scrollIntoViewIfNeeded();const points=await roi.evaluate((el,box)=>{const matrix=(el as SVGSVGElement).getScreenCTM()!;return [[box[0]+.25,box[1]+.25],[box[2]-.25,box[3]-.25]].map(([x,y])=>{const p=new DOMPoint(x,y).matrixTransform(matrix);return{x:p.x,y:p.y};});},initial);
   await page.mouse.move(points[0].x,points[0].y);await page.mouse.down();await page.mouse.move(points[1].x,points[1].y,{steps:4});await page.mouse.up();
  },pipeline=>expect(fixed(pipeline).data.params.roi_bbox).toEqual(initial));
  const moved=[initial[0]+1,initial[1],initial[2]+1,initial[3]];
  await edit('650ms-original-pixel-ROI-keyboard-move',async()=>{await roi.focus();await page.keyboard.press('ArrowRight');},pipeline=>expect(fixed(pipeline).data.params.roi_bbox).toEqual(moved));
  scope.finalRoi=[moved[0],moved[1],moved[2],moved[3]+1];
  await edit('650ms-original-pixel-ROI-keyboard-resize',async()=>{await roi.focus();await page.keyboard.press('Shift+ArrowDown');},pipeline=>expect(fixed(pipeline).data.params.roi_bbox).toEqual(scope.finalRoi));
  await key(page,own(decisionId).locator('[data-flow-node-focus]'));const policy=page.getByLabel('UNKNOWN·미실행·오류 처리',{exact:true});await expect(policy).toHaveValue('review');
  await edit('650ms-incomplete-policy-ng',async()=>{await policy.selectOption('ng');},pipeline=>expect(decision(pipeline).data.params.incomplete_policy).toBe('ng'));
  scope.finalPolicy=scope.tag==='A'?'review':'ng';
  if(scope.finalPolicy==='review')await edit('650ms-incomplete-policy-review-restored',async()=>{await policy.selectOption('review');},pipeline=>expect(decision(pipeline).data.params.incomplete_policy).toBe('review'));
  const expected=clone(scope.seed.pipeline);expected.nodes.push(scope.inspectionTemplate,clone(scope.ROI_template));expected.edges.push(scope.edgeTemplate);
  expected.nodes.find((n:any)=>n.id===scope.addedRoiId).data.params.roi_bbox=scope.finalRoi;
  expected.nodes.find((n:any)=>n.id===decisionId).data.params.incomplete_policy=scope.finalPolicy;
  expect(scope.saved.pipeline).toEqual(expected);
  const final=clone(expected),deadline=performance.now()+10_000;
  const explicit=page.waitForResponse(r=>r.url()===origin+'/api/flowchart/draft'&&r.request().method()==='PUT'&&r.request().frame()===page.mainFrame(),{timeout:Math.max(1,deadline-performance.now())});
  void explicit.catch(()=>{}); // Own early rejection; the same wire remains awaited below.
  await flowFiveWithin(key(page,save),deadline,'actual explicit keyboard draft save');await capture(await flowFiveWithin(explicit,deadline,'explicit full draft response'),'explicit-keyboard-button',pipeline=>expect(pipeline).toEqual(final));
  actionProofs.push({project_id:scope.project.id,inspection_id:scope.inspectionId,ROI_id:scope.addedRoiId,typed_edge_id:scope.addedEdgeId,
   typed_payload:'image',ROI:scope.finalRoi,incomplete_policy:scope.finalPolicy,actual_keyboard_add_select_connect_fixed_ROI_and_save:true,
   actual_650ms_autosave:true,original_pixels:{width:256,height:256,image:scope.images[0]},saved_hash:scope.saved.draft_sha256});
 };
 const readback=async(scope:FlowFiveScope)=>{
  const value={project:await api('/api/project/current'),draft:await api('/api/flowchart/draft'),labelsets:await api('/api/project/labelsets'),
   metadata:await api('/api/dataset/metadata?limit=100'),active:await api('/api/flowchart/pipeline/active-version'),pipelines:await api('/api/flowchart/pipelines?source_dataset_path='+encodeURIComponent(scope.source)),
   annotations:await Promise.all(scope.images.map(image=>api('/api/annotations/part?file_path='+encodeURIComponent(image.path)))),revisions:await api('/api/dataset/revisions')};
  expect(value.project).toEqual(scope.project);expect(value.draft).toEqual(scope.saved);assertDraftRecord(value.draft,scope.saved.pipeline,scope.context);
  expect(value.metadata.items).toHaveLength(2);expect(new Set(value.metadata.items.map((row:any)=>row.image_uuid)).size).toBe(2);
  expect(value.active).toEqual({version_id:null});expect(value.pipelines).toEqual({pipelines:[],total:0});
  expect(JSON.parse(fs.readFileSync(scope.draftFile,'utf8'))).toEqual(Object.fromEntries(Object.entries(scope.saved).filter(([key])=>key!=='active_version_id')));
  return value;
 };
 const custody=(scope:FlowFiveScope)=>{
  const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir,
   labelsets:path.join(scope.project.project_dir,'labelsets'),flowcharts:path.join(scope.project.project_dir,'flowcharts')};
  const value={roots,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,flowFiveTree(root)])),
   project_config:flowFiveTree(path.join(scope.project.project_dir,'project.json')),labelset_registry:flowFiveTree(path.join(scope.project.project_dir,'labelsets.json'))};
  expect(fs.existsSync(path.join(scope.project.project_dir,'flowcharts','active.json'))).toBe(false);
  for(const image of scope.images){const raw=fs.readFileSync(image.path);expect(raw.length).toBe(image.size);expect(sha(raw)).toBe(image.sha256);flowFivePixels(raw,image.blue);}return value;
 };
 const both=()=>({A:custody(A),B:custody(B),harness_dataset:flowFiveTree(w.dataset)});
 let baselineTrees:ReturnType<typeof both>|undefined;
 page.on('request',observed);page.on('requestfailed',failed);page.on('response',replied);
 try{
  if(url)await page.goto(url);else await page.reload();await enter(B,true);await savePrepared(B);B.readback=await readback(B);await e.screenshot(page,'saved-flow-B-actual-put200-before-handoff');
  transitions.push(await flowFiveProject(page,A,origin));await enter(A,true);await savePrepared(A);A.readback=await readback(A);await e.screenshot(page,'saved-flow-A-actual-put200-before-handoff');
  await drain();baselineTrees=both();expect(writes.filter(row=>row.path==='/api/flowchart/draft')).toHaveLength(17);
  for(const scope of[A,B])expect(writes.filter(row=>row.path==='/api/flowchart/draft'&&row.body.context.project_id===scope.project.id)).toHaveLength(scope.tag==='A'?9:8);
  const setupWrites=writes.map(({request:_request,...row})=>row);writes.length=0;const clockStart=clocks.size;baseline=true;
  snapshots.push({tag:'A-before',state:baselineTrees,readback:A.readback});
  transitions.push(await flowFiveProject(page,B,origin));await enter(B,true);expect(await readback(B)).toEqual(B.readback);await drain();expect(both()).toEqual(baselineTrees);
  snapshots.push({tag:'B-after',state:both(),readback:B.readback});await e.screenshot(page,'saved-flow-B-own-graph-and-hash-after-A-to-B');
  transitions.push(await flowFiveProject(page,A,origin));await enter(A,true);expect(await readback(A)).toEqual(A.readback);await drain();expect(both()).toEqual(baselineTrees);
  snapshots.push({tag:'A-return',state:both(),readback:A.readback});await e.screenshot(page,'saved-flow-A-return-own-graph-and-hash');
  expect(writes.map(({request:_request,...row})=>row)).toEqual([{method:'POST',path:'/api/project/open',body:{project_dir:B.project.project_dir}},{method:'POST',path:'/api/project/open',body:{project_dir:A.project.project_dir}}]);
  const owningReads=[...clocks.values()].slice(clockStart);expect(owningReads.length).toBeGreaterThanOrEqual(2);
  for(const scope of[B,A])expect(owningReads.some(row=>row.request.method()==='GET'&&row.project===scope.project.id&&JSON.parse(row.raw!.toString('utf8')).draft_sha256===scope.saved.draft_sha256)).toBe(true);
  const actualDraftReplies=[...clocks.values()].map((row,index)=>({method:row.request.method(),url:row.request.url(),request:row.request.postData(),project_id:row.project,owner_context:row.context,
   started:row.started,deadline:row.deadline,finished:row.finished,status:row.status,raw_response:rawResponse('saved-flow-observed-reply-'+index,row.raw!)}));
  flowFiveSave(e,w,'flow-five-keyboard-policy-project-handoff-proof',{schema:'modu-vision.flow-five-keyboard-policy-project-handoff-proof/v1',cells:['U012.incomplete-policy.handoff','F064.native-keyboard-inspection-add-select.handoff','F064.native-keyboard-typed-port-connection.handoff','F064.native-keyboard-fixed-roi-node-add.handoff','F064.native-keyboard-draft-save-reopen.handoff'],action_proofs:actionProofs,projects:[A.project,B.project],contexts:[A.context,B.context],
   originals:[A.images,B.images],seeds:[A.seed,B.seed],saved_graphs:[A.saved,B.saved],actual_UI_puts:responses,actual_project_transitions:transitions,
   setup_renderer_writes:setupWrites,post_baseline_renderer_writes:writes.map(({request:_request,...row})=>row),actual_original_draft_replies:actualDraftReplies,controlled_fixture_renderer_GETs:controlledFixtureReads,snapshots,
   exact_topology_positions_ROI_labels_contexts_and_hashes:true,original_request_UUID_context_and_response_context_match:true,all_declared_seven_trees_per_project_plus_registries_and_harness_unchanged:true,
   policy:{original_debounce_ms:650,baseline_after_all_15_auto_and_2_explicit_full_responses_and_clean_status:true,dirty_unmount_persistence_not_disabled:true,no_dirty_project_switch:true,post_baseline_draft_writes:0},
   scope:{browser:!native,source_Electron:native,installed_native:false,training:false,model_or_GPU_execution:false,active_flow:false,quality_or_human_or_parent_target_acceptance:false,unrelated_project_lifecycle_SQLite_namespace:false}});
 }catch(error){primary=error;throw error;}finally{
  let cleanupError:unknown;const cleanupRows:Array<{role:string;unchanged:boolean;error_type?:string}>=[];
  const attempt=async(role:string,work:()=>unknown|Promise<unknown>)=>{try{await work();cleanupRows.push({role,unchanged:true});}catch(error){
   if(cleanupError===undefined)cleanupError=error;cleanupRows.push({role,unchanged:false,error_type:error instanceof Error?error.name:typeof error});}};
  await attempt('original-draft-responses',drain);await attempt('request-observer-remove',()=>page.off('request',observed));
  await attempt('failed-observer-remove',()=>page.off('requestfailed',failed));await attempt('response-observer-remove',()=>page.off('response',replied));
  if(baseline){await attempt('A-full-declared-custody',()=>expect(custody(A)).toEqual(baselineTrees!.A));
   await attempt('B-full-declared-custody',()=>expect(custody(B)).toEqual(baselineTrees!.B));
   await attempt('original-harness-dataset',()=>expect(flowFiveTree(w.dataset)).toEqual(baselineTrees!.harness_dataset));}
  await attempt('durable-final-custody',()=>flowFiveSave(e,w,'flow-five-keyboard-policy-project-handoff-final-custody',{
   baseline_reached:baseline,primary_present:primary!==undefined,roles:[...cleanupRows],original_error_preserved:true}));
  if(primary===undefined&&cleanupError!==undefined)throw cleanupError;
 }
}
test('keyboard edited graphs ROI and incomplete policies stay owned across A B A project handoff',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);await installDesktopHostShim(page,renderer.port);await flowFiveExercise(page,workspace,evidence,renderer.origin,false,renderer.url);
});
test('native keyboard edited graphs ROI and incomplete policies stay owned across A B A project handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(240_000);const {window}=electronSession,backend=await electronSession.waitForBackend();await flowFiveExercise(window,workspace,evidence,'http://127.0.0.1:'+backend.port,true);
});

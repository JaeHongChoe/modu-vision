import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
test.use({actionTimeout:15_000});
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string,mode='single'){
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/dicom_control.py'),path.join(workspace.root,'dicom-source'),mode],{encoding:'utf8',timeout:30_000}));
 await api('/api/project/create',{name:'DICOM pack controlled CPU',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:fixture.source},'PUT');
 await api('/api/dataset/import',{folder_path:fixture.source,task:'segmentation'});
 const open=async()=>{if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('DICOM pack controlled CPU');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const panel=page.getByLabel('DICOM 입력 표시',{exact:true});await expect(panel).toBeVisible();
  await panel.locator('summary').click();return panel;};
 let panel=await open();
 const apply=async()=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/dicom/view'&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'Window 적용·정보 조회',exact:true}).click();return response;};
 await panel.getByLabel('DICOM window center',{exact:true}).fill('200');
 await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 let response=await apply();expect(response.status(),await response.text()).toBe(200);const first=await response.json();
 expect(first).toMatchObject({width:32,height:16,source_sha256:fixture.sha256,frame_index:0,window_center:200,window_width:400});
 if(mode==='rle-multi'){
  expect(first.frames).toBe(2);expect(first.transfer_syntax_uid).toBe('1.2.840.10008.1.2.5');
  const saves:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/annotations/save')saves.push(r.url());});
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('1');response=await apply();expect(response.status(),await response.text()).toBe(200);const second=await response.json();
  expect(second.frame_index).toBe(1);expect(second.source_sha256).toBe(first.source_sha256);expect(second.view_sha256).not.toBe(first.view_sha256);
  await expect(panel).toContainText(second.view_sha256);
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('2');response=await apply();expect(response.status()).toBe(422);
  await expect(panel.getByRole('alert')).toContainText('outside available frames');await expect(panel).toContainText(second.view_sha256);
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('0');response=await apply();expect(response.status()).toBe(200);expect((await response.json()).view_sha256).toBe(first.view_sha256);
  expect(saves).toEqual([]);await evidence.screenshot(page,'actual-rle-distinct-frame-out-of-range-refusal');
 }
 await expect(panel).toContainText(first.source_sha256);await expect(panel).toContainText(first.view_sha256);
 await evidence.screenshot(page,'actual-dicom-optional-pack-window-and-source-hashes');
 await panel.getByLabel('DICOM window width',{exact:true}).fill('0');response=await apply();expect(response.status()).toBe(422);
 await expect(panel.getByRole('alert')).toContainText('window_width: Input should be greater than 0');await expect(panel).toContainText(first.view_sha256);
 await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 const blocked='**/api/dataset/dicom/view';await page.route(blocked,r=>r.abort('failed'));
 await panel.getByRole('button',{name:'Window 적용·정보 조회',exact:true}).click();await expect(panel.getByRole('alert')).toBeVisible();
 await page.unroute(blocked);await expect(panel).toContainText(first.view_sha256);
 await evidence.screenshot(page,'actual-dicom-invalid-network-refusal-preserves-display');
 panel=await open();await expect(panel.getByLabel('DICOM window width',{exact:true})).toHaveValue('');
 await panel.getByLabel('DICOM window center',{exact:true}).fill('200');await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 response=await apply();expect(response.status()).toBe(200);const reopened=await response.json();
 expect(reopened.view_sha256).toBe(first.view_sha256);expect(reopened.view_path).toBe(first.view_path);
 expect(crypto.createHash('sha256').update(fs.readFileSync(fixture.file)).digest('hex')).toBe(fixture.sha256);
 await evidence.screenshot(page,'actual-dicom-reopened-owned-view-source-unchanged');
 evidence.note('dicom_runtime',{actual_ui_and_backend:true,synthetic_non_patient_input:true,source_sha256:fixture.sha256,
  view_sha256:first.view_sha256,source_unchanged:true,provider:'pydicom',mode,frames:fixture.frames,
  transfer_syntax_uid:fixture.transfer_syntax_uid,quality_approved:false,
  builtin_rle_view_qualified:mode==='rle-multi',other_compressed_decoders_qualified:false,frame_labeling_qualified:false});
}
test('DICOM optional runtime preserves source through window errors and reopen',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,renderer.url);
});
test('native DICOM optional runtime preserves source through window errors and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned DICOM HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api);
});
test('DICOM built-in RLE view selects distinct frames and refuses an unavailable frame',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,renderer.url,'rle-multi');
});
test('native DICOM built-in RLE view selects distinct frames and refuses an unavailable frame',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned DICOM HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,undefined,'rle-multi');
});

// One Source Electron handoff case; the four original cases above remain byte-exact.
import type {Request as DicomHandoffRequest, Response as DicomHandoffResponse, Locator as DicomHandoffLocator} from '@playwright/test';
const dicomHandoffSHA=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const dicomHandoffIdentity=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
function dicomHandoffRead(file:string){
 const named=fs.lstatSync(file,{bigint:true});expect(named.isFile()).toBe(true);expect(named.isSymbolicLink()).toBe(false);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let failed=false,primary:unknown;
 try{const before=fs.fstatSync(fd,{bigint:true});expect(dicomHandoffIdentity(before)).toEqual(dicomHandoffIdentity(named));
  const raw=fs.readFileSync(fd);expect(BigInt(raw.length)).toBe(before.size);
  expect(dicomHandoffIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(dicomHandoffIdentity(before));
  expect(dicomHandoffIdentity(fs.lstatSync(file,{bigint:true}))).toEqual(dicomHandoffIdentity(before));
  return {sha256:dicomHandoffSHA(raw),size:raw.length,identity:dicomHandoffIdentity(before)};
 }catch(error){failed=true;primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!failed)throw error;
  try{if(primary&&typeof primary==='object')Object.defineProperty(primary,'dicom_read_close_error',{value:String(error),configurable:true});}catch{/* retain original read/assertion failure */}}}
}
function dicomHandoffTree(root:string){
 expect(path.isAbsolute(root)).toBe(true);
 if(!fs.existsSync(root)){expect(fs.lstatSync(path.dirname(root)).isDirectory()).toBe(true);return {exists:false};}
 expect(fs.realpathSync(root)).toBe(root);
 const rows:Record<string,unknown>={};
 const visit=(folder:string)=>{const before=fs.lstatSync(folder,{bigint:true});expect(before.isDirectory()).toBe(true);expect(before.isSymbolicLink()).toBe(false);
  const names=fs.readdirSync(folder).sort();rows[path.relative(root,folder)||'.']={kind:'directory',identity:dicomHandoffIdentity(before),names};
  for(const name of names){const file=path.join(folder,name),s=fs.lstatSync(file);expect(s.isSymbolicLink()).toBe(false);
   if(s.isDirectory())visit(file);else{expect(s.isFile()).toBe(true);rows[path.relative(root,file)]={kind:'file',...dicomHandoffRead(file)};}}
  expect(fs.readdirSync(folder).sort()).toEqual(names);expect(dicomHandoffIdentity(fs.lstatSync(folder,{bigint:true}))).toEqual(dicomHandoffIdentity(before));};
 visit(root);return {exists:true,rows};
}
async function dicomHandoffBounded<T>(pending:Promise<T>,label:string):Promise<T>{
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([pending,new Promise<T>((_,reject)=>{timer=setTimeout(()=>reject(Error(`DICOM handoff ${label} exceeded original 15000ms action bound`)),15_000);})]);}
 finally{if(timer)clearTimeout(timer);}
}
type DicomHandoffOwner={name:string;id:string;projectDir:string;fixture:{source:string;file:string;sha256:string};sourceBefore:ReturnType<typeof dicomHandoffTree>;imagePath?:string;views:any[];guard?:unknown};
test('native built-in RLE frames and canvas prompts stay owned through A B A project handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${status.port}`;
 const pending=new Set<DicomHandoffRequest>(),writes:unknown[]=[],transportFailures:unknown[]=[],visits:unknown[]=[],viewProofs:unknown[]=[];
 const start=(r:DicomHandoffRequest)=>{if(new URL(r.url()).origin!==origin||!new URL(r.url()).pathname.startsWith('/api/'))return;
  pending.add(r);if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:new URL(r.url()).pathname,headers:r.headers(),body:r.postData()});};
 const finished=(r:DicomHandoffRequest)=>{pending.delete(r);};
 const requestFailed=(r:DicomHandoffRequest)=>{if(pending.delete(r))transportFailures.push({url:r.url(),failure:r.failure()});};
 page.on('request',start);page.on('requestfinished',finished);page.on('requestfailed',requestFailed);
 const settle=async()=>{await expect.poll(()=>pending.size,{timeout:15_000}).toBe(0);};
 const api:Api=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{
  const r=await fetch(origin+route,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(15_000)});
  if(!r.ok)throw Error(`Owned DICOM handoff fixture HTTP ${r.status}: ${await r.text()}`);return r.json();
 },{origin,route,body,method});
 let primary:unknown,failed=false;
 try{
  const make=async(letter:string):Promise<DicomHandoffOwner>=>{
   const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/dicom_control.py'),path.join(workspace.root,`dicom-handoff-${letter}`),'rle-multi'],{encoding:'utf8',timeout:30_000}));
   expect(fixture).toMatchObject({mode:'rle-multi',frames:2,transfer_syntax_uid:'1.2.840.10008.1.2.5'});
   expect(dicomHandoffRead(fixture.file).sha256).toBe(fixture.sha256);const sourceBefore=dicomHandoffTree(fixture.source);
   const projectDir=path.join(workspace.userData,'projects',`dicom-rle-owning-${letter}`);expect(fs.existsSync(projectDir)).toBe(false);
   const name=`Owned RLE handoff ${letter}`,made=await api('/api/project/create',{name,task:'segmentation',project_dir:projectDir});
   expect(made).toMatchObject({name,task:'segmentation',project_dir:projectDir});expect(typeof made.id).toBe('string');expect(made.id.length).toBeGreaterThan(0);
   expect(fs.realpathSync(projectDir)).toBe(projectDir);
   await api('/api/project/update',{source_dataset_dir:fixture.source},'PUT');await api('/api/dataset/import',{folder_path:fixture.source,task:'segmentation'});
   return {name,id:made.id,projectDir,fixture,sourceBefore,views:[]};
  };
  const a=await make('A'),b=await make('B');expect(a.id).not.toBe(b.id);expect(a.fixture.sha256).not.toBe(b.fixture.sha256);
  const assistButton=page.getByRole('button',{name:'모델 보조 라벨링 패널',exact:true});
  const controls=page.getByLabel('SAM2 기반 라벨링',{exact:true});
  const showPrompts=async()=>{const showHelpers=page.getByTitle('보조 패널을 다시 보입니다',{exact:true});if(await showHelpers.count())await showHelpers.click();
   if(await assistButton.getAttribute('aria-expanded')!=='true')await assistButton.click();await expect(controls).toBeVisible();return controls;};
  const open=async(owner:DicomHandoffOwner)=>{
   await settle();await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
   await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(owner.projectDir);
   const wire=page.waitForResponse(r=>new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/project/open'&&r.request().method()==='POST'&&r.request().postDataJSON()?.project_dir===owner.projectDir,{timeout:15_000});
   void wire.catch(()=>undefined);await dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click();const response=await wire;
   expect(response.status()).toBe(200);expect(await dicomHandoffBounded(response.finished(),'open response completion')).toBeNull();const project=await dicomHandoffBounded(response.json(),'open body');
   expect(project).toMatchObject({id:owner.id,project_dir:owner.projectDir,name:owner.name,task:'segmentation',source_dataset_dir:owner.fixture.source});
   const context=JSON.parse(response.headers()['x-vision-context']||'null');expect(context).toMatchObject({project_id:owner.id,mode:'local'});
   await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(owner.name);
   await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const panel=page.getByLabel('DICOM 입력 표시',{exact:true});await expect(panel).toBeVisible();
   if(await panel.getAttribute('open')===null)await panel.locator(':scope > summary').click();await settle();
   await expect(panel.getByLabel('DICOM window center',{exact:true})).toHaveValue('');await expect(panel.getByLabel('DICOM window width',{exact:true})).toHaveValue('');await expect(panel.getByLabel('DICOM frame index',{exact:true})).toHaveValue('0');
   await expect(panel).not.toContainText('원본 SHA256');await expect(await showPrompts()).toContainText('현재 원본 좌표 · 점 0 / 박스 0');
   const current=await api('/api/project/current');expect(current).toMatchObject({id:owner.id,project_dir:owner.projectDir,source_dataset_dir:owner.fixture.source});
   visits.push({project,context,current,controls_reset:true,prompts_reset:true});return panel;
  };
  const apply=async(owner:DicomHandoffOwner,panel:DicomHandoffLocator,frame:number)=>{
   await panel.getByLabel('DICOM window center',{exact:true}).fill('100');await panel.getByLabel('DICOM window width',{exact:true}).fill('100');await panel.getByLabel('DICOM frame index',{exact:true}).fill(String(frame));
   const wire=page.waitForResponse(r=>new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/dataset/dicom/view'&&r.request().method()==='POST'&&r.request().postDataJSON()?.frame_index===frame,{timeout:15_000});
   const pngWire=page.waitForResponse(r=>new URL(r.url()).origin===origin&&/^\/api\/dataset\/dicom\/views\/[0-9a-f]{64}$/.test(new URL(r.url()).pathname)&&r.request().method()==='GET',{timeout:15_000});
   void wire.catch(()=>undefined);void pngWire.catch(()=>undefined);await panel.getByRole('button',{name:'Window 적용·정보 조회',exact:true}).click();
   const response:DicomHandoffResponse=await wire;expect(response.status()).toBe(200);expect(await dicomHandoffBounded(response.finished(),'view completion')).toBeNull();
   const request=response.request(),requestBody=request.postDataJSON();expect(Object.keys(requestBody).sort()).toEqual(['frame_index','image_path','window_center','window_width']);
   expect(requestBody).toMatchObject({window_center:100,window_width:100,frame_index:frame});expect(fs.realpathSync(requestBody.image_path)).toBe(owner.fixture.file);
   if(owner.imagePath)expect(requestBody.image_path).toBe(owner.imagePath);else owner.imagePath=requestBody.image_path;
   expect(request.headers()['x-vision-project']).toBe(owner.id);expect(JSON.parse(request.headers()['x-vision-context']||'null')).toMatchObject({project_id:owner.id,mode:'local'});
   expect(JSON.parse(response.headers()['x-vision-context']||'null')).toMatchObject({project_id:owner.id,mode:'local'});
   const view=await dicomHandoffBounded(response.json(),'view body');expect(view).toMatchObject({source_path:requestBody.image_path,source_sha256:owner.fixture.sha256,width:32,height:16,frames:2,frame_index:frame,window_center:100,window_width:100,transfer_syntax_uid:'1.2.840.10008.1.2.5'});
   expect(view.view_id).toMatch(/^[0-9a-f]{64}$/);expect(view.view_path).toBe(path.join(owner.projectDir,'dicom_views',`${view.view_id}.png`));expect(view.display_url).toBe(`/api/dataset/dicom/views/${view.view_id}`);
   expect(dicomHandoffRead(view.view_path).sha256).toBe(view.view_sha256);const receiptFile=path.join(owner.projectDir,'dicom_views',`${view.view_id}.json`);const receipt=JSON.parse(fs.readFileSync(receiptFile,'utf8'));const persisted={...view};delete persisted.display_url;expect(receipt).toEqual(persisted);
   const png=await pngWire;expect(new URL(png.url()).pathname).toBe(view.display_url);expect(png.status()).toBe(200);expect(await dicomHandoffBounded(png.finished(),'canvas PNG completion')).toBeNull();
   const pngBytes=await dicomHandoffBounded(png.body(),'canvas PNG bytes');expect(dicomHandoffSHA(pngBytes)).toBe(view.view_sha256);expect(png.headers()['cache-control']).toBe('no-store');
   await expect(panel).toContainText(owner.fixture.sha256);await expect(panel).toContainText(view.view_sha256);await settle();
   const raster=page.locator('[data-canvas-container="true"] > canvas').nth(0);
   const centerPixel=()=>raster.evaluate(element=>{const canvas=element as HTMLCanvasElement;const ctx=canvas.getContext('2d');if(!ctx)throw Error('Original canvas raster context absent');return [...ctx.getImageData(Math.floor(canvas.width/2),Math.floor(canvas.height/2),1,1).data];});
   // The100/100 window gives frame0 a black plateau covering central neighboring source pixels.
   // The unchanged signed16-bit ramp/rescale and MONOCHROME1 inversion keep frame1's zeros white.
   await expect.poll(centerPixel,{timeout:15_000}).toEqual(frame===0?[0,0,0,255]:[255,255,255,255]);
   viewProofs.push({owner:owner.id,request:{url:request.url(),headers:request.headers(),body:requestBody},response:{status:response.status(),headers:response.headers(),body:view},canvas_get:{url:png.url(),status:png.status(),headers:png.headers(),sha256:dicomHandoffSHA(pngBytes),size:pngBytes.length},canvas_center_pixel:await centerPixel()});
   evidence.addFile(view.view_path);evidence.addFile(receiptFile);return view;
  };
  const point=async(count:number,label:'0'|'1')=>{
   await showPrompts();await controls.getByLabel('SAM2 점 positive negative',{exact:true}).selectOption(label);await controls.getByRole('button',{name:'캔버스 점 prompt',exact:true}).click();
   await page.getByLabel('모델 보조 라벨링 검토',{exact:true}).getByRole('button',{name:'닫기',exact:true}).click();
   const canvas=page.locator('[data-canvas-container="true"] > canvas').nth(2);await expect(canvas).toBeVisible();const box=await canvas.boundingBox();expect(box).not.toBeNull();expect(box!.width).toBeGreaterThan(0);expect(box!.height).toBeGreaterThan(0);
   await canvas.click({position:{x:box!.width/2,y:box!.height/2}});await expect(await showPrompts()).toContainText(`현재 원본 좌표 · 점 ${count} / 박스 0`);
  };
  const guard=(owner:DicomHandoffOwner)=>({source:dicomHandoffTree(owner.fixture.source),views:dicomHandoffTree(path.join(owner.projectDir,'dicom_views')),annotations:dicomHandoffTree(path.join(owner.projectDir,'annotations')),manifest:dicomHandoffRead(path.join(owner.projectDir,'project.json'))});
  let panel=await open(a);a.views=[await apply(a,panel,0),await apply(a,panel,1)];expect(a.views[0].view_sha256).not.toBe(a.views[1].view_sha256);
  await point(1,'1');await evidence.screenshot(page,'owned-rle-A-frame1-and-one-canvas-prompt');a.guard=guard(a);
  panel=await open(b);b.views=[await apply(b,panel,0),await apply(b,panel,1)];expect(b.views[0].view_sha256).not.toBe(b.views[1].view_sha256);
  expect(a.views.map(v=>v.view_id).filter(id=>b.views.some(v=>v.view_id===id))).toEqual([]);
  await point(1,'0');await point(2,'1');await evidence.screenshot(page,'owned-rle-B-distinct-source-and-two-canvas-prompts');b.guard=guard(b);expect(guard(a)).toEqual(a.guard);
  panel=await open(a);const returned0=await apply(a,panel,0),returned1=await apply(a,panel,1);expect(returned0).toEqual(a.views[0]);expect(returned1).toEqual(a.views[1]);
  await expect(await showPrompts()).toContainText('현재 원본 좌표 · 점 0 / 박스 0');await evidence.screenshot(page,'owned-rle-return-A-exact-cached-frames-prompts-reset');await settle();
  expect(guard(a)).toEqual(a.guard);expect(guard(b)).toEqual(b.guard);expect(dicomHandoffTree(a.fixture.source)).toEqual(a.sourceBefore);expect(dicomHandoffTree(b.fixture.source)).toEqual(b.sourceBefore);
  expect(transportFailures).toEqual([]);const uiWrites=writes as Array<{path:string}>;
  expect(uiWrites.filter(r=>r.path==='/api/annotations/save'||r.path.includes('/train')||r.path==='/api/label-candidates/generate'||r.path==='/api/model-deployments/activate')).toEqual([]);
  const proof={schema:'modu-vision.dicom-rle-owning-project-handoff/v1',cell:'F116.rle-frame-window.handoff',source_Electron:true,visits,viewProofs,writes,transportFailures,pending_requests:pending.size,
   owners:[a,b],source_unchanged:true,annotation_and_cache_custody:true,prompts_ephemeral_reset:true,builtin_rle_only:true,synthetic_non_patient_input:true,installed_native_acceptance:false,model_training:false,quality_approved:false,human_accepted:false};
  const proofFile=path.join(workspace.logs,'dicom-rle-owning-project-handoff-proof.json'),raw=Buffer.from(JSON.stringify(proof,null,2)+'\n');const fd=fs.openSync(proofFile,'wx',0o600);let writeFailed=false,writePrimary:unknown;
  try{expect(fs.writeSync(fd,raw)).toBe(raw.length);fs.fsyncSync(fd);}catch(error){writeFailed=true;writePrimary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!writeFailed)throw error;try{if(writePrimary&&typeof writePrimary==='object')Object.defineProperty(writePrimary,'dicom_proof_close_error',{value:String(error),configurable:true});}catch{/* original write failure retained */}}}
  evidence.addFile(proofFile);evidence.addFile(a.fixture.file);evidence.addFile(b.fixture.file);evidence.note('dicom_rle_handoff',proof);
 }catch(error){failed=true;primary=error;throw error;}finally{
  const cleanup:unknown[]=[];
  try{page.off('request',start);}catch(error){cleanup.push(error);}
  try{page.off('requestfinished',finished);}catch(error){cleanup.push(error);}
  try{page.off('requestfailed',requestFailed);}catch(error){cleanup.push(error);}
  if(cleanup.length){if(!failed)throw cleanup[0];try{if(primary&&typeof primary==='object')Object.defineProperty(primary,'dicom_handoff_cleanup_errors',{value:cleanup.map(String),configurable:true});}catch{/* original test failure retained */}}
 }
});

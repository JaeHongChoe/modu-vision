import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect, type Workspace, type Evidence } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { png } from './qa/appFlow';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(workspace.root,'modal-source');fs.mkdirSync(source,{recursive:true});
 const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,70]));
 const sha=crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex');
 const project=await api('/api/project/create',{name:'Keyboard modal isolation',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();
 const bounds=(await page.locator('[data-canvas-container]').boundingBox())!;
 const origin={x:bounds.x+(bounds.width-256)/2,y:bounds.y+(bounds.height-256)/2};
 await page.mouse.move(origin.x+20,origin.y+20);await page.mouse.down();await page.mouse.move(origin.x+70,origin.y+60,{steps:8});await page.mouse.up();
 const query=`/api/annotations/part?file_path=${encodeURIComponent(image)}`;
 const save=async()=>{const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await pending).status()).toBe(200);return api(query);};
 const initial=await save();expect(initial.annotations).toHaveLength(1);expect(initial.annotations[0].bbox).toEqual([20,20,70,60]);
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();
 await page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click();
 await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 const opener=page.getByRole('button',{name:/팀 작업 · 라벨 기준·검수/});await opener.click();
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});await expect(dialog).toBeVisible();
 const close=dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});await close.focus();await expect(close).toBeFocused();
 for(const key of ['ArrowRight','Delete','Control+z','Control+y']){
  await page.keyboard.press(key);
  await expect(dialog).toBeVisible();await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 }
 await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();await expect(opener).toBeFocused();
 // The same shortcuts must still work after dismissing the modal.
 await page.keyboard.press('ArrowRight');const nudged=await save();expect(nudged.annotations[0].bbox).toEqual([21,20,71,60]);
 await page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click();
 await page.keyboard.press('Delete');await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(0);
 await page.keyboard.press('Control+z');await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
 const restored=await save();expect(restored.annotations).toEqual(nudged.annotations);
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 1$/})).toBeVisible();
 expect((await api(query)).annotations).toEqual(restored.annotations);
 expect(crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex')).toBe(sha);
 await evidence.screenshot(page,native?'native-modal-keyboard-content-reopened':'browser-modal-keyboard-content-reopened');
 evidence.note('modal_keyboard_isolation',{project_id:project.id,image_sha256:sha,initial:initial.annotations,nudged:nudged.annotations,
  restored:restored.annotations,blocked_modal_keys:['ArrowRight','Delete','Control+z','Control+y'],
  close_focus_restored:true,post_close_shortcuts_work:true,actual_draw_save_reopen:true,original_image_unchanged:true,
  native,physical_keyboard_or_human_annotation_quality:false});
}
test('modal keyboard cannot edit the selected background annotation',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native modal keyboard cannot edit the selected background annotation',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned modal API ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});
 await exercise(window,workspace,evidence,api,true);
});

// U020 handoff: real browser projects, canvas saves, and modal-owned keys.
import type { APIRequestContext, Request as ModalHandoffRequest, Response as ModalHandoffResponse } from '@playwright/test';
type ModalHandoffProject={id:string;name:string;task:string;project_dir:string;source_dataset_dir:string;annotations_dir:string;models_dir:string;reports_dir:string};
type ModalHandoffOwner={tag:'A'|'B';project:ModalHandoffProject;source:string;image:string;imageId:string;context:Record<string,unknown>;bbox:number[]};
const modalHandoffHash=(raw:Buffer|string)=>crypto.createHash('sha256').update(raw).digest('hex');
async function modalHandoffWithin<T>(work:Promise<T>,deadline:number,label:string):Promise<T>{
 void work.catch(()=>undefined);const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s modal handoff frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s modal handoff frame expired: '+label)),remaining);})]);}
 finally{if(timer)clearTimeout(timer);}
}
function modalHandoffRaw(evidence:Evidence,workspace:Workspace,label:string,raw:Buffer){
 const file=path.join(workspace.logs,label+'.json');const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL,0o600);let primary:unknown;
 try{let done=0;while(done<raw.length){const count=fs.writeSync(fd,raw,done,raw.length-done);if(count<=0)throw Error('Short owned modal proof write');done+=count;}fs.fsyncSync(fd);}
 catch(error){primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)throw error;evidence.note('modal_proof_close_secondary',String(error));}}
 evidence.addFile(file);return {path:file,sha256:modalHandoffHash(raw),size:raw.length};
}
function modalHandoffTree(root:string){
 const files:Record<string,{sha256:string;size:number;identity:string[]}>= {},directories:string[]=[];
 const same=(a:fs.BigIntStats,b:fs.BigIntStats)=>[a.dev,a.ino,a.mode,a.nlink,a.size,a.mtimeNs,a.ctimeNs].map(String).join(':')===[b.dev,b.ino,b.mode,b.nlink,b.size,b.mtimeNs,b.ctimeNs].map(String).join(':');
 const walk=(dir:string)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
  const member=path.join(dir,entry.name),relative=path.relative(root,member).split(path.sep).join('/'),before=fs.lstatSync(member,{bigint:true});expect(before.isSymbolicLink()).toBe(false);
  if(before.isDirectory()){directories.push(relative);walk(member);continue;}
  expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1n);const fd=fs.openSync(member,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown;
  try{const opened=fs.fstatSync(fd,{bigint:true});expect(same(before,opened)).toBe(true);const raw=fs.readFileSync(fd);const after=fs.fstatSync(fd,{bigint:true}),named=fs.lstatSync(member,{bigint:true});expect(same(opened,after)&&same(after,named)).toBe(true);expect(BigInt(raw.length)).toBe(after.size);
   files[relative]={sha256:modalHandoffHash(raw),size:raw.length,identity:[after.dev,after.ino,after.mode,after.nlink,after.size,after.mtimeNs,after.ctimeNs].map(String)};
  }catch(error){primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)throw error;if(primary instanceof Error){try{Object.defineProperty(primary,'modal_custody_close_secondary',{value:String(error),enumerable:true});}catch{/* Attachment is best effort; preserve the original failure. */}}}}
 }};
 const stat=fs.lstatSync(root);expect(stat.isDirectory()).toBe(true);expect(stat.isSymbolicLink()).toBe(false);expect(fs.realpathSync(root)).toBe(root);walk(root);return {root,files,directories};
}
async function modalHandoffApi(request:APIRequestContext,origin:string,route:string,body?:unknown,method?:string,owner?:ModalHandoffOwner){
 const deadline=performance.now()+10_000,headers:Record<string,string>={};if(owner){headers['X-Vision-Project']=owner.project.id;headers['X-Vision-Context']=JSON.stringify(owner.context);}
 const response=await modalHandoffWithin(request.fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{data:body}),timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owned fixture API');
 expect(response.status()).toBe(200);const raw=await modalHandoffWithin(response.body(),deadline,'owned fixture API body');expect(raw.length).toBeLessThanOrEqual(1024*1024);const context=JSON.parse(response.headers()['x-vision-context']);
 if(owner)expect(context).toEqual(owner.context);return {body:JSON.parse(raw.toString('utf8')),raw,context};
}
async function modalHandoffMake(request:APIRequestContext,origin:string,workspace:Workspace,tag:'A'|'B'):Promise<ModalHandoffOwner>{
 const source=path.join(workspace.root,'modal-handoff-source-'+tag);fs.mkdirSync(source,{mode:0o700});const imageId='part',image=path.join(source,imageId+'.png');
 const raw=png(256,3,(x,y)=>tag==='A'?[x,y,70]:[y,90,x]);fs.writeFileSync(image,raw,{flag:'wx',mode:0o600});
 const made=await modalHandoffApi(request,origin,'/api/project/create',{name:'Modal handoff '+tag,task:'segmentation'});
 expect(path.dirname(made.body.project_dir)).toBe(workspace.projects);expect(fs.realpathSync(made.body.project_dir)).toBe(made.body.project_dir);expect(fs.lstatSync(made.body.project_dir).isSymbolicLink()).toBe(false);
 await modalHandoffApi(request,origin,'/api/project/update',{source_dataset_dir:source},'PUT');await modalHandoffApi(request,origin,'/api/dataset/import',{folder_path:source,task:'segmentation'});
 const selected=await modalHandoffApi(request,origin,'/api/project/current');expect(selected.body.id).toBe(made.body.id);expect(selected.body.source_dataset_dir).toBe(source);expect(selected.context.project_id).toBe(made.body.id);
 const owner:ModalHandoffOwner={tag,project:selected.body,source,image,imageId,context:selected.context,bbox:tag==='A'?[20,20,70,60]:[90,90,140,130]};
 const history=await modalHandoffApi(request,origin,'/api/evaluation/history?source_dataset_path='+encodeURIComponent(source)+'&task=segmentation',undefined,'GET',owner);expect(history.body).toEqual({items:[],total:0});
 expect(modalHandoffTree(owner.project.reports_dir).directories).toEqual(['evaluations']);return owner;
}
async function modalHandoffResponse(page:Page,response:ModalHandoffResponse,owner:ModalHandoffOwner,origin:string,method:string,route:string,deadline:number,evidence:Evidence,workspace:Workspace,label:string){
 const wire=response.request(),address=new URL(response.url());expect(wire.frame()).toBe(page.mainFrame());expect(address.origin).toBe(origin);expect(address.pathname).toBe(route);expect(wire.method()).toBe(method);expect(response.status()).toBe(200);
 const raw=await modalHandoffWithin(response.body(),deadline,label+' raw body');expect(raw.length).toBeLessThanOrEqual(1024*1024);expect(await modalHandoffWithin(response.finished(),deadline,label+' completion')).toBeNull();
 const context=JSON.parse((await modalHandoffWithin(response.allHeaders(),deadline,label+' response headers'))['x-vision-context']);expect(context).toEqual(owner.context);
 const proof=modalHandoffRaw(evidence,workspace,label,raw);return {body:JSON.parse(raw.toString('utf8')),proof:{...proof,project_id:owner.project.id,method,origin,path:route,status:200,finished:true,request_headers:wire.headers(),request_body:wire.postData(),response_context:context}};
}
async function modalHandoffOpen(page:Page,owner:ModalHandoffOwner,origin:string,evidence:Evidence,workspace:Workspace,label:string){
 const deadline=performance.now()+10_000;await modalHandoffWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'ordinary manager open');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await modalHandoffWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'ordinary recent tab');const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:owner.project.project_dir})});
 await expect(item).toHaveCount(1,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(item).toBeEnabled({timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 const opened=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/project/open'&&r.request().method()==='POST'&&r.request().postDataJSON()?.project_dir===owner.project.project_dir,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});void opened.catch(()=>undefined);
 const loaded=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/annotations/'+owner.imageId&&new URL(r.url()).searchParams.get('file_path')===owner.image&&r.request().method()==='GET',{timeout:10_000});void loaded.catch(()=>undefined);
 await modalHandoffWithin(item.click(),deadline,'ordinary exact recent project');const response=await modalHandoffWithin(opened,deadline,'owning project response');expect(response.request().postDataJSON()).toEqual({project_dir:owner.project.project_dir});
 const result=await modalHandoffResponse(page,response,owner,origin,'POST','/api/project/open',deadline,evidence,workspace,label+'-open');expect(result.body).toEqual(owner.project);
 await expect(dialog).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(owner.project.name,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 return {opened:result.proof,loaded};
}
async function modalHandoffEnter(page:Page,owner:ModalHandoffOwner,origin:string,loaded:Promise<ModalHandoffResponse>,evidence:Evidence,workspace:Workspace,label:string){
 const deadline=performance.now()+10_000;await modalHandoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click(),deadline,'ordinary labeling stage');
 const focus=page.getByRole('button',{name:'집중 편집',exact:true});await expect(focus).toBeVisible({timeout:Math.max(1,Math.floor(deadline-performance.now()))});if(await modalHandoffWithin(focus.getAttribute('aria-pressed'),deadline,'current focus mode')==='false')await modalHandoffWithin(focus.click(),deadline,'ordinary focused edit');await expect(focus).toHaveAttribute('aria-pressed','true',{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 const response=await modalHandoffWithin(loaded,deadline,'owning current-image read');expect(response.request().headers()['x-vision-project']).toBe(owner.project.id);expect(JSON.parse(response.request().headers()['x-vision-context'])).toEqual(owner.context);
 const read=await modalHandoffResponse(page,response,owner,origin,'GET','/api/annotations/'+owner.imageId,deadline,evidence,workspace,label+'-annotations');expect(new URL(response.url()).searchParams.get('file_path')).toBe(owner.image);expect(read.body.image_id).toBe(owner.imageId);expect(read.body.metadata.file_path).toBe(owner.image);expect(read.body.metadata.content_hash).toBe(modalHandoffHash(fs.readFileSync(owner.image)));
 await expect.poll(()=>page.locator('[data-canvas-container] canvas').first().evaluate(canvas=>{const c=canvas as HTMLCanvasElement;return c.getContext('2d')?.getImageData(Math.floor(c.width/2),Math.floor(c.height/2),1,1).data[3]??0;}),{timeout:Math.max(1,Math.floor(deadline-performance.now()))}).toBeGreaterThan(0);
 await modalHandoffWithin(page.getByTitle('100% Zoom (1:1)',{exact:true}).click(),deadline,'ordinary one-to-one zoom');await expect(page.getByTestId('canvas-hud')).toContainText('100%',{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 await modalHandoffWithin(page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve())))),deadline,'paint completion');await expect(page.getByTestId('canvas-hud')).toContainText('100%',{timeout:Math.max(1,Math.floor(deadline-performance.now()))});return read;
}
async function modalHandoffSave(page:Page,request:APIRequestContext,owner:ModalHandoffOwner,origin:string,bbox:number[],evidence:Evidence,workspace:Workspace,label:string){
 const deadline=performance.now()+10_000;const waiting=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST'&&r.request().headers()['x-vision-project']===owner.project.id,{timeout:10_000});void waiting.catch(()=>undefined);
 await modalHandoffWithin(page.getByRole('button',{name:'Save Changes',exact:true}).click(),deadline,'ordinary canvas save');const response=await modalHandoffWithin(waiting,deadline,'owning saved-label response'),body=response.request().postDataJSON();
 expect(JSON.parse(response.request().headers()['x-vision-context'])).toEqual(owner.context);expect(body.image_id).toBe(owner.imageId);expect(body.image_path).toBe(owner.image);expect([body.image_width,body.image_height]).toEqual([256,256]);expect(body.annotations).toHaveLength(1);expect(body.annotations[0].bbox).toEqual(bbox);expect(body.annotations[0].label).toBe('Solder Bridge');expect(body.annotations[0].category_id).toBe(1);expect(Number.isSafeInteger(body.expected_revision)).toBe(true);
 const saved=await modalHandoffResponse(page,response,owner,origin,'POST','/api/annotations/save',deadline,evidence,workspace,label+'-save');expect(saved.body.status).toBe('saved');expect(saved.body.count).toBe(1);expect(saved.body.image_id).toBe(owner.imageId);
 const read=await modalHandoffWithin(modalHandoffApi(request,origin,'/api/annotations/'+owner.imageId+'?file_path='+encodeURIComponent(owner.image),undefined,'GET',owner),deadline,'owning saved-label read');expect(read.body.annotations).toHaveLength(1);expect(read.body.annotations[0].bbox).toEqual(bbox);expect(read.body.metadata.file_path).toBe(owner.image);
 const readProof=modalHandoffRaw(evidence,workspace,label+'-saved-read',read.raw);await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});return {annotations:read.body.annotations,save:saved.proof,read:readProof};
}
async function modalHandoffDraw(page:Page,owner:ModalHandoffOwner){
 const deadline=performance.now()+10_000;await modalHandoffWithin(page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click(),deadline,'ordinary bbox tool');await expect(page.getByTestId('canvas-hud')).toContainText('100%',{timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 const bounds=await modalHandoffWithin(page.locator('[data-canvas-container]').boundingBox(),deadline,'current canvas bounds');expect(bounds).not.toBeNull();const origin={x:bounds!.x+(bounds!.width-256)/2,y:bounds!.y+(bounds!.height-256)/2},[x1,y1,x2,y2]=owner.bbox;
 await modalHandoffWithin(page.mouse.move(origin.x+x1,origin.y+y1),deadline,'bbox start');await modalHandoffWithin(page.mouse.down(),deadline,'bbox down');await modalHandoffWithin(page.mouse.move(origin.x+x2,origin.y+y2,{steps:8}),deadline,'actual bbox drag');await modalHandoffWithin(page.mouse.up(),deadline,'bbox up');
}
async function modalHandoffBlock(page:Page,request:APIRequestContext,owner:ModalHandoffOwner,origin:string,saved:any,posts:unknown[],evidence:Evidence,workspace:Workspace,label:string){
 const deadline=performance.now()+10_000;await modalHandoffWithin(page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click(),deadline,'ordinary select tool');await modalHandoffWithin(page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click(),deadline,'ordinary saved annotation selection');
 const before=modalHandoffTree(owner.project.annotations_dir),postCount=posts.length,opener=page.getByRole('button',{name:/팀 작업 · 라벨 기준·검수/});await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await modalHandoffWithin(opener.click(),deadline,'ordinary team dialog');
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true}),close=dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});await expect(dialog).toBeVisible({timeout:Math.max(1,Math.floor(deadline-performance.now()))});await modalHandoffWithin(close.focus(),deadline,'dialog-owned focus');await expect(close).toBeFocused({timeout:Math.max(1,Math.floor(deadline-performance.now()))});
 for(const key of ['ArrowRight','Delete','Control+z','Control+y']){await modalHandoffWithin(page.keyboard.press(key),deadline,'modal-owned '+key);await expect(dialog).toBeVisible({timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});expect(posts.length).toBe(postCount);}
 await modalHandoffWithin(evidence.screenshot(page,label+'-modal-owned-keys'),deadline,'actual modal screenshot');const read=await modalHandoffWithin(modalHandoffApi(request,origin,'/api/annotations/'+owner.imageId+'?file_path='+encodeURIComponent(owner.image),undefined,'GET',owner),deadline,'owning saved-label read');expect(read.body.annotations).toEqual(saved);expect(modalHandoffTree(owner.project.annotations_dir)).toEqual(before);const raw=modalHandoffRaw(evidence,workspace,label+'-modal-read',read.raw);
 await modalHandoffWithin(page.keyboard.press('Escape'),deadline,'ordinary modal Escape');await expect(dialog).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(opener).toBeFocused({timeout:Math.max(1,Math.floor(deadline-performance.now()))});expect(posts.length).toBe(postCount);return {tag:owner.tag,project_id:owner.project.id,blocked_keys:['ArrowRight','Delete','Control+z','Control+y'],no_annotation_POST:true,annotation_namespace:before,read:raw,escape_focus_restored:true};
}
test('modal keyboard isolation and saved labels remain owning through A B A project handoff',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const A=await modalHandoffMake(request,renderer.origin,workspace,'A'),B=await modalHandoffMake(request,renderer.origin,workspace,'B');
 const immutable=()=>[A,B].map(owner=>({tag:owner.tag,source:modalHandoffTree(owner.source),models:modalHandoffTree(owner.project.models_dir),reports:modalHandoffTree(owner.project.reports_dir)}));const before=immutable();
 const posts:unknown[]=[],observe=(r:ModalHandoffRequest)=>{const u=new URL(r.url());if(u.origin===renderer.origin&&u.pathname==='/api/annotations/save'&&r.method()==='POST')posts.push({origin:u.origin,path:u.pathname,headers:r.headers(),body:r.postData()});};let primary:unknown;
 page.on('request',observe);
 try{
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(B.project.name);
  const first=await modalHandoffOpen(page,A,renderer.origin,evidence,workspace,'modal-A-initial');const firstRead=await modalHandoffEnter(page,A,renderer.origin,first.loaded,evidence,workspace,'modal-A-initial');expect(firstRead.body.annotations).toEqual([]);await modalHandoffDraw(page,A);const savedA=await modalHandoffSave(page,request,A,renderer.origin,A.bbox,evidence,workspace,'modal-A-initial');const blockedA=await modalHandoffBlock(page,request,A,renderer.origin,savedA.annotations,posts,evidence,workspace,'modal-A-initial');
  const second=await modalHandoffOpen(page,B,renderer.origin,evidence,workspace,'modal-B-owning');const secondRead=await modalHandoffEnter(page,B,renderer.origin,second.loaded,evidence,workspace,'modal-B-owning');expect(secondRead.body.annotations).toEqual([]);await modalHandoffDraw(page,B);const savedB=await modalHandoffSave(page,request,B,renderer.origin,B.bbox,evidence,workspace,'modal-B-owning');const blockedB=await modalHandoffBlock(page,request,B,renderer.origin,savedB.annotations,posts,evidence,workspace,'modal-B-owning');const bLabels=modalHandoffTree(B.project.annotations_dir);
  const returned=await modalHandoffOpen(page,A,renderer.origin,evidence,workspace,'modal-A-return');const returnedRead=await modalHandoffEnter(page,A,renderer.origin,returned.loaded,evidence,workspace,'modal-A-return');expect(returnedRead.body.annotations).toEqual(savedA.annotations);const blockedReturn=await modalHandoffBlock(page,request,A,renderer.origin,savedA.annotations,posts,evidence,workspace,'modal-A-return');
  await page.keyboard.press('ArrowRight');const nudged=await modalHandoffSave(page,request,A,renderer.origin,[21,20,71,60],evidence,workspace,'modal-A-post-close');expect(modalHandoffTree(B.project.annotations_dir)).toEqual(bLabels);
  const bRead=await modalHandoffApi(request,renderer.origin,'/api/annotations/'+B.imageId+'?file_path='+encodeURIComponent(B.image),undefined,'GET',B);expect(bRead.body.annotations).toEqual(savedB.annotations);const bProof=modalHandoffRaw(evidence,workspace,'modal-B-final-owning-read',bRead.raw);
  const reopenDeadline=performance.now()+10_000,loaded=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&new URL(r.url()).origin===renderer.origin&&new URL(r.url()).pathname==='/api/annotations/'+A.imageId&&new URL(r.url()).searchParams.get('file_path')===A.image&&r.request().method()==='GET',{timeout:10_000});void loaded.catch(()=>undefined);
  await modalHandoffWithin(page.reload(),reopenDeadline,'ordinary owning reload');const reopened=await modalHandoffEnter(page,A,renderer.origin,loaded,evidence,workspace,'modal-A-reopened');expect(reopened.body.annotations).toEqual(nudged.annotations);await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);expect(immutable()).toEqual(before);expect(modalHandoffTree(B.project.annotations_dir)).toEqual(bLabels);expect(posts).toHaveLength(3);
  await evidence.screenshot(page,'modal-A-owning-label-reopened');evidence.note('modal_keyboard_project_handoff',{actual_browser:true,project_open_order:['A','B','A'],opens:[first.opened,second.opened,returned.opened],blocked:[blockedA,blockedB,blockedReturn],saved_A:savedA,saved_B:savedB,post_close_A:nudged,B_final_read:bProof,reopened_A:reopened.proof,annotation_POSTs:posts,immutable_roots:before,B_labels_unchanged:bLabels,action_cell:'U020.modal-keyboard-edit-isolation.handoff',physical_keyboard_or_human_annotation_quality:false,parent_acceptance:false,native_acceptance:false});
 }catch(error){primary=error;throw error;}
 finally{try{page.off('request',observe);}catch(error){if(primary===undefined)throw error;evidence.note('modal_request_observer_cleanup_secondary',String(error));}}
});

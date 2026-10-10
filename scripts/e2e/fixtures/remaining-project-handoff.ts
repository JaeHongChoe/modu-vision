// SOURCE-only appended GUI controls; Root alone executes and qualifies.
import fs from 'node:fs';
import handoffPath from 'node:path';
import {createHash as handoffCreateHash} from 'node:crypto';
import type {Page, Request as HandoffRequest, Route as HandoffRoute, Locator} from '@playwright/test';
import {expect, type Evidence, type Workspace} from './test';
export type HandoffApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
export const handoffHash=(bytes:Buffer|string)=>handoffCreateHash('sha256').update(bytes).digest('hex');
export type HandoffScope={tag:'A'|'B';project:any;source:string};
export function handoffApi(page:Page,origin:string,native:boolean):HandoffApi {
 return async(route,body,method)=>{
  const deadline=performance.now()+10_000;
  if(native)return handoffWithin(page.evaluate(async({origin,route,body,method,remaining})=>{
   const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);
   try{const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:abort.signal});
    const raw=await response.text();if(!response.ok)throw Error('Owned handoff API status '+response.status);return JSON.parse(raw);
   }finally{clearTimeout(timer);}
  },{origin,route,body,method,remaining:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owned renderer API');
  const response=await handoffWithin(page.request.fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body}),timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'owned browser API');
  expect(response.ok()).toBe(true);return handoffWithin(response.json(),deadline,'owned API body');
 };
}
export function handoffNamespace(root:string){
 const files:Record<string,{sha256:string;size:number}>={},directories:string[]=[];
 const walk=(dir:string)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
  const file=handoffPath.join(dir,entry.name),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
  const member=handoffPath.relative(root,file).split(handoffPath.sep).join('/');
  if(info.isDirectory()){directories.push(member);walk(file);}else{expect(info.isFile()).toBe(true);const bytes=fs.readFileSync(file);files[member]={size:bytes.length,sha256:handoffHash(bytes)};}
 }};
 const info=fs.lstatSync(root);expect(info.isDirectory()).toBe(true);expect(info.isSymbolicLink()).toBe(false);walk(root);return {files,directories};
}
export function handoffRoots(scope:HandoffScope){
 const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir};
 return Object.fromEntries(Object.entries(roots).map(([kind,root])=>[kind,{root,snapshot:handoffNamespace(root)}]));
}
export function handoffAssertRoots(before:any,after:any,allowed:Record<string,string[]>={}){
 expect(Object.keys(after)).toEqual(Object.keys(before));
 for(const kind of Object.keys(before)){
  expect(after[kind].root).toBe(before[kind].root);expect(after[kind].snapshot.directories).toEqual(before[kind].snapshot.directories);
  expect(Object.keys(after[kind].snapshot.files)).toEqual(Object.keys(before[kind].snapshot.files));
  for(const member of Object.keys(before[kind].snapshot.files))if(!(allowed[kind]||[]).includes(member))expect(after[kind].snapshot.files[member]).toEqual(before[kind].snapshot.files[member]);
 }
}
export function handoffSave(e:Evidence,w:Workspace,label:string,value:unknown){
 const file=handoffPath.join(w.logs,label+'.json');fs.writeFileSync(file,JSON.stringify(value,null,2),{flag:'wx'});e.addFile(file);return {path:file,sha256:handoffHash(fs.readFileSync(file))};
}
export async function handoffPost(page:Page,origin:string,button:Locator,path:string,expected:unknown,e:Evidence,w:Workspace,label:string){
 const deadline=performance.now()+10_000;
 const waiting=page.waitForResponse(response=>{const request=response.request(),u=new URL(response.url());return request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===path&&request.method()==='POST';},{timeout:10_000});
 await handoffWithin(button.click(),deadline,'actual button');const response=await handoffWithin(waiting,deadline,'actual UI response');
 expect(response.request().postDataJSON()).toEqual(expected);expect(response.status()).toBe(200);const bytes=await handoffWithin(response.body(),deadline,'actual raw POST body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);
 expect(await handoffWithin(response.finished(),deadline,'actual POST finished')).toBeNull();
 const file=handoffPath.join(w.logs,label+'-actual-response.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);
 return {body:JSON.parse(bytes.toString('utf8')),proof:{method:'POST',path,status:200,body:expected,response_sha256:handoffHash(bytes),bytes:bytes.length,actual_main_frame:true,finished:true,elapsed_ms:10_000-(deadline-performance.now())}};
}
export async function handoffWithin<T>(promise:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s handoff frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;
 return Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s handoff frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
export async function handoffProject(page:Page,scope:{project:any;source:string},origin:string){
 const deadline=performance.now()+10_000;
 const teamClose=page.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});
 if(await handoffWithin(teamClose.isVisible(),deadline,'existing team dialog visible')){
  await handoffWithin(teamClose.click(),deadline,'ordinary team dialog close');
  await handoffWithin(expect(teamClose).toHaveCount(0,{timeout:Math.max(1,Math.floor(deadline-performance.now()))}),deadline,'team dialog closed');
 }
 await handoffWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'project manager open');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await handoffWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'recent project list');
 const wire=page.waitForResponse(r=>r.request().frame()===page.mainFrame()&&r.request().method()==='POST'&&new URL(r.url()).origin===origin&&new URL(r.url()).pathname==='/api/project/open'&&r.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});
 const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
 await expect(item).toHaveCount(1,{timeout:Math.max(1,Math.floor(deadline-performance.now()))});await expect(item).toBeEnabled({timeout:Math.max(1,Math.floor(deadline-performance.now()))});await handoffWithin(item.click(),deadline,'exact recent project click');
 const response=await handoffWithin(wire,deadline,'project open');expect(response.status()).toBe(200);const bytes=await handoffWithin(response.body(),deadline,'project open raw body');expect(bytes.length).toBeLessThanOrEqual(1024*1024);
 expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});expect(JSON.parse(bytes.toString('utf8'))).toEqual(scope.project);expect(await handoffWithin(response.finished(),deadline,'project open finished')).toBeNull();
 await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
 return {project_id:scope.project.id,project_dir:scope.project.project_dir,method:'POST',origin,path:'/api/project/open',actual_main_frame:true,status:200,response_sha256:handoffHash(bytes),finished:true};
}
export async function handoffLateRead(page:Page,origin:string,native:boolean,match:(u:URL)=>boolean){
 const pattern=origin+'/api/**';let release!:()=>void,reached!:()=>void,finished!:()=>void;
 const gate=new Promise<void>(r=>release=r),ready=new Promise<void>(r=>reached=r),done=new Promise<void>(r=>finished=r);
 let captured:{status:number;bytes:Buffer;sha256:string;body:any;request_identity:{method:string;origin:string;pathname:string;query_sha256:string;main_frame:boolean}}|undefined,primary:unknown,routeError:unknown,request:HandoffRequest|undefined,deadline=0,armed=true;
 let resolveDisposition!:(row:{kind:'finished'|'failed';failure:string|null})=>void;
 const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>resolveDisposition=r);
 const ended=(r:HandoffRequest)=>{if(r===request)resolveDisposition({kind:'finished',failure:null});};
 const failed=(r:HandoffRequest)=>{if(r===request)resolveDisposition({kind:'failed',failure:r.failure()?.errorText??null});};
 page.on('requestfinished',ended);page.on('requestfailed',failed);
 const handler=async(route:HandoffRoute)=>{
  const own=route.request(),address=new URL(own.url());
  if(!armed||own.method()!=='GET'||address.origin!==origin||address.searchParams.has('_modu_handoff_delegate')||!match(address)){await route.continue();return;}
  armed=false;request=own;deadline=performance.now()+10_000;
  try{
   let bytes:Buffer,status:number,contentType:string|null;
   if(native){
    // This is a separate authenticated renderer GET, not original UI200 provenance.
    const headers:Record<string,string>={};for(const name of ['content-type','x-vision-project','x-vision-context']){const value=await own.headerValue(name);if(value!==null)headers[name]=value;}
    const delegate=new URL(own.url());delegate.searchParams.set('_modu_handoff_delegate',handoffCreateHash('sha256').update(own.url()+String(Date.now())).digest('hex'));
    const snapshot=await page.evaluate(async({url,headers,remaining})=>{
     const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);
     try{const response=await fetch(url,{method:'GET',headers,signal:abort.signal});const raw=new Uint8Array(await response.arrayBuffer());
      if(raw.length>1024*1024)throw Error('Controlled handoff snapshot exceeds 1MiB');let binary='';for(const byte of raw)binary+=String.fromCharCode(byte);
      return {status:response.status,contentType:response.headers.get('content-type'),base64:btoa(binary)};
     }finally{clearTimeout(timer);}
    },{url:delegate.toString(),headers,remaining:Math.max(1,Math.floor(deadline-performance.now()))});
    status=snapshot.status;contentType=snapshot.contentType;bytes=Buffer.from(snapshot.base64,'base64');
   }else{const snapshot=await route.fetch({timeout:Math.max(1,Math.floor(deadline-performance.now()))});status=snapshot.status();contentType=snapshot.headers()['content-type']??null;bytes=await snapshot.body();}
   expect(status).toBe(200);expect(bytes.length).toBeLessThanOrEqual(1024*1024);
   expect(own.frame()).toBe(page.mainFrame());captured={status,bytes,sha256:handoffHash(bytes),body:JSON.parse(bytes.toString('utf8')),request_identity:{method:own.method(),origin:address.origin,pathname:address.pathname,query_sha256:handoffHash(address.search),main_frame:true}};reached();
   await handoffWithin(gate,deadline,'held read release');
   try{await route.fulfill({status,body:bytes,headers:contentType?{'content-type':contentType}:{}});}catch(error){routeError=error;}
  }catch(error){primary=error;reached();}finally{finished();}
 };
 await page.route(pattern,handler);
 return {
  ready:async()=>{await handoffWithin(ready,performance.now()+10_000,'captured original read');if(primary)throw primary;expect(captured).toBeTruthy();return captured!;},
  finish:async()=>{release();await handoffWithin(done,deadline,'route completion');if(primary)throw primary;const row=await handoffWithin(disposition,deadline,'original Request disposition');
   if(row.kind==='failed')expect(row.failure).toContain('ERR_ABORTED');else{if(routeError)throw routeError;const response=await request!.response();expect(response).not.toBeNull();expect(response!.status()).toBe(200);expect(handoffHash(await response!.body())).toBe(captured!.sha256);expect(await response!.finished()).toBeNull();}
   await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));
   return {request_disposition:row,request_identity:captured!.request_identity,captured_sha256:captured!.sha256,captured_bytes:captured!.bytes.length,snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',original_UI_response_delivered:row.kind==='finished',native_original_HTTP200_provenance:false};},
  close:async()=>{release();if(!page.isClosed())await page.unroute(pattern,handler);page.off('requestfinished',ended);page.off('requestfailed',failed);},
 };
}

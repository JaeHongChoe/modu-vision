import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route,Locator} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
const endpoint='/api/dataset/masks/import';
const cell='F118.pixel-mask-reviewed-apply.error';
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Custody={opened:number;closed:number;uncertain:Array<{file:string;fd:number;error:string}>};
const raw=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs,s.uid,s.gid].map(String);
function filePin(file:string,custody:Custody){
 const first=fs.lstatSync(file,{bigint:true});expect(first.isFile()).toBe(true);expect(first.isSymbolicLink()).toBe(false);expect(first.nlink).toBe(1n);
 let fd:number|undefined,primary:unknown,result:{raw:string[];sha256:string}|undefined;
 try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);custody.opened++;expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(first));const bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(first.size);expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(first));expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(first));result={raw:raw(first),sha256:hash(bytes)};}
 catch(cause){primary=cause;}
 finally{if(fd!==undefined){const owned=fd;fd=undefined;try{fs.closeSync(owned);custody.closed++;}catch(cause){custody.uncertain.push({file,fd:owned,error:String(cause)});if(primary===undefined)primary=cause;}}}
 if(primary!==undefined)throw primary;return result!;
}
function treePin(root:string,custody:Custody):Record<string,unknown>{
 if(!fs.existsSync(root))return {'':{exists:false}};
 const rows:Record<string,unknown>={};const walk=(file:string,relative:string)=>{const first=fs.lstatSync(file,{bigint:true});expect(first.isSymbolicLink()).toBe(false);if(first.isDirectory()){const names=fs.readdirSync(file).sort();rows[relative]={kind:'directory',raw:raw(first),names};for(const name of names)walk(path.join(file,name),relative?relative+'/'+name:name);expect(fs.readdirSync(file).sort()).toEqual(names);expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(first));}else{expect(first.isFile()).toBe(true);rows[relative]={kind:'file',...filePin(file,custody)};}};
 walk(root,'');return rows;
}
async function within<T>(operation:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original10s mask error frame expired: '+label);let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original10s mask error frame expired: '+label)),remaining);})]);}finally{if(timer!==undefined)clearTimeout(timer);}
}

async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,folder:(directory:string)=>Promise<void>,native:boolean,origin:string,url?:string){
 const custody:Custody={opened:0,closed:0,uncertain:[]},bundle=path.join(w.root,'owned-mask-reviewed-apply-error');fs.mkdirSync(bundle);
 execFileSync(harness.resolvePython(),['-c',`import hashlib,json,pathlib,sys,numpy as np
from PIL import Image
root=pathlib.Path(sys.argv[1]); source=pathlib.Path(sys.argv[2]); rows=[]
for image in sorted(source.rglob('*.png')):
 with Image.open(image) as im: w,h=im.size
 pixels=np.zeros((h,w),np.uint8); pixels[4:12,3:15]=7
 relative=image.relative_to(source).as_posix(); name='masks/'+relative+'.mask.png'; (root/name).parent.mkdir(parents=True,exist_ok=True); Image.fromarray(pixels).save(root/name)
 rows.append({'file_name':relative,'mask_file':name,'width':w,'height':h,'source_sha256':hashlib.sha256(image.read_bytes()).hexdigest()})
(root/'mask_manifest.json').write_text(json.dumps({'schema_version':1,'classes':[{'id':0,'name':'background','color':'#000000'},{'id':7,'name':'ControlledScratch','color':'#f59e0b'}],'images':rows}))`,bundle,w.dataset],{encoding:'utf8',timeout:10_000});
 const manifest=JSON.parse(fs.readFileSync(path.join(bundle,'mask_manifest.json'),'utf8'));
 expect(manifest.images.map((row:any)=>row.file_name).sort()).toEqual(w.images.map(image=>path.relative(w.dataset,image.path).split(path.sep).join('/')).sort());expect(manifest.images).toHaveLength(2);
 const project=await api('/api/project/create',{name:'Owned reviewed mask apply error',task:'segmentation'}),active=await api('/api/project/update',{source_dataset_dir:w.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:w.dataset,task:'segmentation'});await api('/api/team-data');await api('/api/team-data/readiness');
 const annotation=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
 for(const image of w.images)expect((await api(annotation(image.path))).annotations).toEqual([]);
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
 const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
 const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});await expect(panel).toContainText('검색 결과 2개');await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('controlled-mask-apply-error');
 const section=panel.getByLabel('외부 multiclass mask 교환',{exact:true});if(await section.getAttribute('open')===null)await section.locator('summary').click();await folder(bundle);await section.getByRole('button',{name:'mask 폴더 선택',exact:true}).click();await expect(section.getByText(bundle,{exact:true})).toBeVisible();
 const preview=section.getByRole('button',{name:'mask 미리보기',exact:true}),apply=section.getByRole('button',{name:'검토한 mask 라벨 적용',exact:true});
 const captures:any[]=[],requests:any[]=[],writes:any[]=[],pageErrors:string[]=[],controls:Array<{close:()=>Promise<void>}>=[];
 const matches=(request:Request)=>{const u=new URL(request.url());return request.method()==='POST'&&request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===endpoint&&u.search==='';};
 const observer=(request:Request)=>{const u=new URL(request.url());if(u.origin!==origin)return;if(matches(request))requests.push(request.postDataJSON());else if(u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postData()});};
 const errorObserver=(error:Error)=>pageErrors.push(String(error));
 const capture=async(button:Locator,label:string,status:number,expectedBody:any)=>{const deadline=performance.now()+10_000,waiting=page.waitForResponse(response=>matches(response.request()),{timeout:10_000});await within(button.click(),deadline,label+' click');const response=await within(waiting,deadline,label+' owning response');expect(response.status()).toBe(status);expect(response.request().postDataJSON()).toEqual(expectedBody);const bytes=await within(response.body(),deadline,label+' original fullbody');expect(await within(response.finished(),deadline,label+' original finished')).toBeNull();const file=path.join(w.logs,'mask-apply-'+captures.length+'-'+label+'-actual-body.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);const row={label,request:response.request(),request_body:expectedBody,status,body:JSON.parse(bytes.toString('utf8')),sha256:hash(bytes),size:bytes.length,file,main_frame:true,origin,path:endpoint,finished:true};captures.push(row);return row;};
 const protectedState=()=>({source:treePin(w.dataset,custody),bundle:treePin(bundle,custody),annotations:treePin(active.annotations_dir||project.annotations_dir,custody),versions:treePin(path.join(project.project_dir,'versions'),custody)});
 const durable=async()=>({labels:await Promise.all(w.images.map(image=>api(annotation(image.path)))),metadata:(await api('/api/dataset/metadata?limit=10')).items,versions:await api('/api/dataset/versions'),team:await api('/api/team-data'),jobs:await api('/api/training/jobs')});
 const expected=(mode:'preview'|'apply',revisions:Record<string,number>,signature?:string)=>({import_dir:bundle,mode,conflict_policy:'reject',actor:'controlled-mask-apply-error',expected_revisions:revisions,...(signature===undefined?{}:{expected_manifest_sha256:signature})});
 let primary:unknown;
 page.on('request',observer);page.on('pageerror',errorObserver);
 try{
  const first=await capture(preview,'real-preview',200,expected('preview',{}));expect(first.body.applied).toBe(false);expect(first.body.preview).toHaveLength(2);expect(first.body.preview.every((row:any)=>row.existing_count===0&&row.incoming_count===1&&!row.conflict)).toBe(true);await expect(section.locator('article')).toHaveCount(2);await expect(apply).toBeEnabled();
  const revisions=Object.fromEntries(first.body.preview.map((row:any)=>[row.image_uuid,row.revision])),applyBody=expected('apply',revisions,first.body.manifest_sha256),baselineDurable=await durable(),baseline=protectedState();expect(baselineDurable.labels.every(row=>row.annotations.length===0)).toBe(true);expect(baselineDurable.jobs.jobs).toEqual([]);
  await section.scrollIntoViewIfNeeded();await expect(section).toBeInViewport();await e.screenshot(page,'mask-reviewed-ready-before-apply-error');
  const pattern=origin+endpoint,detail='Controlled owning reviewed mask apply POST unavailable',body=JSON.stringify({detail});
  let owned:Request|undefined,ownedRoute:Route|undefined,count=0,handlerError:unknown,done=false,terminal=false;
  let resolveDone!:()=>void,resolveTerminal!:()=>void;const completed=new Promise<void>(resolve=>resolveDone=resolve),disposition=new Promise<void>(resolve=>resolveTerminal=resolve);
  const ended=(request:Request)=>{if(request===owned){terminal=true;resolveTerminal();}};page.on('requestfinished',ended);page.on('requestfailed',ended);
  const handler=async(route:Route)=>{const request=route.request();if(count!==0||!matches(request)||request.postDataJSON().mode!=='apply'){await route.continue();return;}count++;owned=request;ownedRoute=route;const deadline=performance.now()+10_000;try{expect(request.postDataJSON()).toEqual(applyBody);await within(route.fulfill({status:503,contentType:'application/json',body}),deadline,'owned disclosed503');}catch(cause){handlerError=cause;try{await within(route.abort('aborted'),deadline,'owning handler failure abort');}catch{/* First error retained. */}}finally{done=true;resolveDone();}};
  await page.route(pattern,handler);
  const control={close:async()=>{const deadline=performance.now()+10_000;let failure=handlerError;try{if(owned){await within(completed,deadline,'owning handler join');if(!terminal&&ownedRoute)await within(ownedRoute.abort('aborted'),deadline,'owning unresolved request abort');await within(disposition,deadline,'original request disposition');expect(done&&terminal).toBe(true);}await within(page.unroute(pattern,handler),deadline,'exact owning handler removal');}catch(cause){if(failure===undefined)failure=cause;}finally{page.off('requestfinished',ended);page.off('requestfailed',ended);}if(failure!==undefined)throw failure;expect(count).toBe(1);}};controls.push(control);
  let failed:any,actionError:unknown;
  try{failed=await capture(apply,'controlled-owning-apply503',503,applyBody);expect(failed.request).toBe(owned);expect(JSON.stringify(failed.body)).toBe(body);await expect(section.getByRole('alert')).toContainText(detail);await expect(section.getByRole('status')).toHaveCount(0);await expect(section.locator('article')).toHaveCount(2);await expect(section.getByText(bundle,{exact:true})).toBeVisible();await expect(apply).toBeEnabled();await expect(preview).toBeEnabled();expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(writes).toEqual([]);await section.getByRole('alert').scrollIntoViewIfNeeded();await expect(section.getByRole('alert')).toBeInViewport();await e.screenshot(page,'mask-reviewed-original-apply503-keeps-preview-and-records');}catch(cause){actionError=cause;}finally{try{await control.close();controls.splice(controls.indexOf(control),1);}catch(cause){if(actionError===undefined)actionError=cause;}}if(actionError!==undefined)throw actionError;
  // Deliberate original preview200 proves route removal and error recovery.
  // No reviewed apply200 or human label acceptance is claimed or attempted.
  const recovered=await capture(preview,'real-explicit-preview-recovery',200,expected('preview',revisions));expect(recovered.request).not.toBe(failed.request);expect(recovered.body).toEqual(first.body);await expect(section.getByRole('alert')).toHaveCount(0);await expect(section.locator('article')).toHaveCount(2);await expect(apply).toBeEnabled();expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(requests).toEqual(captures.map(row=>row.request_body));expect(requests).toHaveLength(3);expect(writes).toEqual([]);expect(pageErrors).toEqual([]);await section.scrollIntoViewIfNeeded();await expect(section).toBeInViewport();await e.screenshot(page,'mask-reviewed-explicit-original-preview200-recovery');
  e.note('mask_reviewed_apply_error',{schema:'modu-vision.f118-reviewed-mask-apply-error/v1',selected_pending_pointers:[cell],mode:native?'source-electron':'browser',project,manifest,baseline_durable:baselineDurable,final_durable:await durable(),protected_before:baseline,protected_after:protectedState(),captures:captures.map(({request,...row})=>row),requests,writes,pageErrors,owning_apply_controlled503:true,controlled_transport_not_backend503:true,original_apply_not_dispatched_to_server:true,exact_revision_map_and_manifest_signature:true,real_preview200_before_and_after:true,preview_retained_and_apply_reenabled:true,apply200_not_attempted:true,new_labels_or_version_created:false,folder_dialog_controlled:true,OS_folder_picker_not_accepted:true,actual_model_inference:false,GPU_used:false,Native_accepted:false,parent_completion_credit:false,quality_human_installed_target_approval:false});
 }catch(cause){primary=cause;}
 finally{for(const control of controls){try{await control.close();}catch(cause){if(primary===undefined)primary=cause;}}page.off('request',observer);page.off('pageerror',errorObserver);try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);e.note('mask_reviewed_apply_error_closure',{owned_FDs_opened:custody.opened,owned_FDs_closed:custody.closed,uncertain:custody.uncertain,remaining_owned_handlers:controls.length,first_exception:primary===undefined?null:String(primary)});}catch(cause){if(primary===undefined)primary=cause;}}
 if(primary!==undefined)throw primary;
}
test('reviewed mask apply exact POST error preserves owned preview and annotations before original preview recovery',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};const folder=(directory:string)=>page.evaluate(value=>{(window as any).api.selectFolder=async()=>value;},directory);await exercise(page,workspace,evidence,api,folder,false,renderer.origin,renderer.url);});
test('native reviewed mask apply exact POST error preserves owned originals before original preview recovery',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;const api:Api=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned mask error fixture HTTP ${response.status}: ${await response.text()}`);return response.json();},{origin,route,body,method});const folder=(directory:string)=>electronSession.app.evaluate(({dialog},value)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[value]});},directory);await exercise(page,workspace,evidence,api,folder,true,origin);});

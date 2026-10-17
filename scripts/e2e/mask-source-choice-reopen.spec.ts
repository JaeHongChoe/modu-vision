import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
const endpoint='/api/dataset/masks/import';
const cell='F118.pixel-mask-source-choice.reopen';
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
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original10s mask reopen frame expired: '+label);let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original10s mask reopen frame expired: '+label)),remaining);})]);}finally{if(timer!==undefined)clearTimeout(timer);}
}

async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,folder:(directory:string)=>Promise<void>,native:boolean,origin:string,url?:string){
 const custody:Custody={opened:0,closed:0,uncertain:[]},bundle=path.join(w.root,'owned-mask-source-choice-reopen');fs.mkdirSync(bundle);
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
 const project=await api('/api/project/create',{name:'Owned mask source choice reopen',task:'segmentation'}),active=await api('/api/project/update',{source_dataset_dir:w.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:w.dataset,task:'segmentation'});await api('/api/team-data');await api('/api/team-data/readiness');
 const annotation=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
 for(const image of w.images)expect((await api(annotation(image.path))).annotations).toEqual([]);
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
 const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
 const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});await expect(panel).toContainText('검색 결과 2개');await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('controlled-mask-source-choice-reopen');
 const section=panel.getByLabel('외부 multiclass mask 교환',{exact:true});if(await section.getAttribute('open')===null)await section.locator('summary').click();await folder(bundle);await section.getByRole('button',{name:'mask 폴더 선택',exact:true}).click();await expect(section.getByText(bundle,{exact:true})).toBeVisible();
 const preview=section.getByRole('button',{name:'mask 미리보기',exact:true}),apply=section.getByRole('button',{name:'검토한 mask 라벨 적용',exact:true}),choice=section.getByRole('checkbox',{name:'원본 이미지 함께 내보내기',exact:true});
 const expectedPreview={import_dir:bundle,mode:'preview',conflict_policy:'reject',actor:'controlled-mask-source-choice-reopen',expected_revisions:{}};
 const matches=(request:Request)=>{const u=new URL(request.url());return request.method()==='POST'&&request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===endpoint&&u.search==='';};
 const deadline=performance.now()+10_000,waiting=page.waitForResponse(response=>matches(response.request()),{timeout:10_000});
 await within(preview.click(),deadline,'initial real preview click');const response=await within(waiting,deadline,'initial real preview response');expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual(expectedPreview);
 const bytes=await within(response.body(),deadline,'initial original fullbody');expect(await within(response.finished(),deadline,'initial original finished')).toBeNull();const result=JSON.parse(bytes.toString('utf8'));expect(result.applied).toBe(false);expect(result.preview).toHaveLength(2);expect(result.preview.every((row:any)=>row.existing_count===0&&row.incoming_count===1&&!row.conflict)).toBe(true);
 const file=path.join(w.logs,'mask-source-choice-initial-real-preview-body.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);
 const initialCapture={file,sha256:hash(bytes),size:bytes.length,status:200,body:result,request_body:expectedPreview,main_frame:true,finished:true,original_HTTP:true,controlled_response:false,origin,path:endpoint};
 await expect(section.locator('article')).toHaveCount(2);await expect(apply).toBeEnabled();await expect(choice).toBeChecked();
 const protectedState=()=>({source:treePin(w.dataset,custody),bundle:treePin(bundle,custody),annotations:treePin(active.annotations_dir||project.annotations_dir,custody),versions:treePin(path.join(project.project_dir,'versions'),custody),exports:treePin(path.join(project.project_dir,'exports'),custody)});
 const durable=async()=>({labels:await Promise.all(w.images.map(image=>api(annotation(image.path)))),metadata:(await api('/api/dataset/metadata?limit=10')).items,versions:await api('/api/dataset/versions'),team:await api('/api/team-data'),jobs:await api('/api/training/jobs')});
 const baselineDurable=await durable(),baseline=protectedState();expect(baselineDurable.labels.every(row=>row.annotations.length===0)).toBe(true);expect(baselineDurable.jobs.jobs).toEqual([]);expect((baseline.exports as any)['']).toEqual({exists:false});
 const mutations:any[]=[],pageErrors:string[]=[],states:any[]=[];
 const observer=(request:Request)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))mutations.push({method:request.method(),path:u.pathname,body:request.postData()});};
 const errorObserver=(error:Error)=>pageErrors.push(String(error));let primary:unknown;
 page.on('request',observer);page.on('pageerror',errorObserver);
 try{
  states.push({boundary:'initial-original-instance',include_originals:await choice.isChecked(),preview_rows:await section.locator('article').count()});
  await choice.uncheck();await expect(choice).not.toBeChecked();await expect(section.locator('article')).toHaveCount(2);expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(mutations).toEqual([]);states.push({boundary:'unsent-false-in-original-instance',include_originals:await choice.isChecked(),preview_rows:await section.locator('article').count()});await section.scrollIntoViewIfNeeded();await expect(section).toBeInViewport();await e.screenshot(page,'mask-source-choice-unsent-false-before-collapse');
  // Closing a details element hides, but does not unmount, this component.
  await section.locator(':scope > summary').click();await expect(choice).toBeHidden();await expect(panel).toBeVisible();await section.locator(':scope > summary').click();await expect(choice).not.toBeChecked();await expect(section.getByText(bundle,{exact:true})).toBeVisible();await expect(section.locator('article')).toHaveCount(2);await expect(apply).toBeEnabled();expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(mutations).toEqual([]);states.push({boundary:'same-instance-details-reopened',include_originals:await choice.isChecked(),preview_rows:await section.locator('article').count()});await section.scrollIntoViewIfNeeded();await expect(section).toBeInViewport();await e.screenshot(page,'mask-source-choice-same-instance-retains-unsent-false');
  // The real outer workflow toggle removes the owning section and component.
  // Its new instance uses the existing useState(true) default, not saved false.
  await toggle.click();await expect(panel).toHaveCount(0);await expect(toggle).toHaveAttribute('aria-expanded','false');await toggle.click();await expect(panel).toContainText('검색 결과 2개');await expect(toggle).toHaveAttribute('aria-expanded','true');await expect(section).toHaveCount(1);if(await section.getAttribute('open')===null)await section.locator(':scope > summary').click();await expect(choice).toBeChecked();await expect(section.getByText('mask_manifest.json 필요',{exact:true})).toBeVisible();await expect(section.locator('article')).toHaveCount(0);await expect(preview).toBeDisabled();await expect(apply).toBeDisabled();await expect(section.getByRole('alert')).toHaveCount(0);await expect(section.getByRole('status')).toHaveCount(0);expect(protectedState()).toEqual(baseline);const finalDurable=await durable();expect(finalDurable).toEqual(baselineDurable);expect(mutations).toEqual([]);expect(pageErrors).toEqual([]);states.push({boundary:'new-instance-after-real-outer-remount',include_originals:await choice.isChecked(),preview_rows:await section.locator('article').count()});await section.scrollIntoViewIfNeeded();await expect(section).toBeInViewport();await e.screenshot(page,'mask-source-choice-original-remount-default-true');
  expect(states.map(row=>row.include_originals)).toEqual([true,false,false,true]);expect(states.map(row=>row.preview_rows)).toEqual([2,2,2,0]);
  e.note('mask_source_choice_reopen',{schema:'modu-vision.f118-mask-source-choice-reopen/v1',selected_pending_pointers:[cell],mode:native?'source-electron':'browser',project,manifest,initial_capture:initialCapture,baseline_durable:baselineDurable,final_durable:finalDurable,protected_before:baseline,protected_after:protectedState(),states,mutations,pageErrors,original_details_same_instance:true,details_collapse_unmounted_component:false,same_instance_kept_unsent_false:true,outer_panel_absence_observed:true,new_instance_reset_existing_default_true:true,unsent_false_saved_or_restored:false,zero_POST_after_local_choice:true,mask_apply_or_export_attempted:false,controlled_API_response:false,folder_dialog_controlled:true,OS_folder_picker_not_accepted:true,actual_model_inference:false,GPU_used:false,Native_accepted:false,parent_completion_credit:false,quality_human_installed_target_approval:false});
 }catch(cause){primary=cause;}
 finally{page.off('request',observer);page.off('pageerror',errorObserver);try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);e.note('mask_source_choice_reopen_closure',{owned_FDs_opened:custody.opened,owned_FDs_closed:custody.closed,uncertain:custody.uncertain,remaining_owned_handlers:0,first_exception:primary===undefined?null:String(primary)});}catch(cause){if(primary===undefined)primary=cause;}}
 if(primary!==undefined)throw primary;
}
test('mask source choice stays local through details collapse and resets default on original panel remount',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};const folder=(directory:string)=>page.evaluate(value=>{(window as any).api.selectFolder=async()=>value;},directory);await exercise(page,workspace,evidence,api,folder,false,renderer.origin,renderer.url);});
test('native mask source choice stays local through details collapse and resets default on original panel remount',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;const api:Api=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned mask reopen fixture HTTP ${response.status}: ${await response.text()}`);return response.json();},{origin,route,body,method});const folder=(directory:string)=>electronSession.app.evaluate(({dialog},value)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[value]});},directory);await exercise(page,workspace,evidence,api,folder,true,origin);});

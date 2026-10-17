import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
test.use({actionTimeout:10_000});
const cell='U015.saved-queue-selected-reopen.invalid',endpoint='/api/data-workbench/review-queues';
type SplitControlCustody={opened:number;closed:number;uncertain:Array<{file:string;fd:number;error:string}>};
const splitControlRaw=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs,s.uid,s.gid].map(String);
function splitControlFile(file:string,custody:SplitControlCustody):{bytes:Buffer;raw:string[];sha256:string}{
 const first=fs.lstatSync(file,{bigint:true});expect(first.isFile()).toBe(true);expect(first.isSymbolicLink()).toBe(false);expect(first.nlink).toBe(1n);
 let fd:number|undefined,primary:unknown,failed=false,result:{bytes:Buffer;raw:string[];sha256:string}|undefined;
 try{fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);custody.opened++;expect(splitControlRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(splitControlRaw(first));
  const bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(first.size);expect(splitControlRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(splitControlRaw(first));expect(splitControlRaw(fs.lstatSync(file,{bigint:true}))).toEqual(splitControlRaw(first));result={bytes,raw:splitControlRaw(first),sha256:hash(bytes)};
 }catch(cause){failed=true;primary=cause;}
 finally{if(fd!==undefined){const owned=fd;fd=undefined;try{fs.closeSync(owned);custody.closed++;}catch(cause){custody.uncertain.push({file,fd:owned,error:String(cause)});if(!failed){failed=true;primary=cause;}else if(primary instanceof Error)primary.message+='; owned file close failed: '+String(cause);}}}
 if(failed)throw primary;return result!;
}
function splitControlTree(root:string,custody:SplitControlCustody):Record<string,unknown>{
 const rows:Record<string,unknown>={};const walk=(file:string,relative:string)=>{
  const first=fs.lstatSync(file,{bigint:true});expect(first.isSymbolicLink()).toBe(false);
  if(first.isDirectory()){const names=fs.readdirSync(file).sort();rows[relative]={kind:'directory',raw:splitControlRaw(first),names};for(const name of names)walk(path.join(file,name),relative?relative+'/'+name:name);expect(fs.readdirSync(file).sort()).toEqual(names);expect(splitControlRaw(fs.lstatSync(file,{bigint:true}))).toEqual(splitControlRaw(first));}
  else{expect(first.isFile()).toBe(true);const pin=splitControlFile(file,custody);rows[relative]={kind:'file',raw:pin.raw,sha256:pin.sha256};}
 };walk(root,'');return rows;
}
async function splitControlWithin<T>(operation:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s split-control frame expired: '+label);let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s split-control frame expired: '+label)),remaining);})]);}finally{if(timer!==undefined)clearTimeout(timer);}
}

async function exercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=handoffApi(page,origin,native),custody:SplitControlCustody={opened:0,closed:0,uncertain:[]},source=path.join(w.root,'owned-saved-queue-invalid-pref-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,70+index]),{flag:'wx'});return {name,file,sha256:hash(fs.readFileSync(file))};});
 const created=await api('/api/project/create',{name:'Owned saved queue unknown preference',task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);
 const seed=()=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const fixtures=[seed(),seed()];expect(fixtures.every(f=>f.controlled_reports_not_model_inference===true)).toBe(true);expect(fixtures[0].record.evaluation_id).not.toBe(fixtures[1].record.evaluation_id);
 const queues:any[]=[];for(const fixture of fixtures){const queue=await api(endpoint,{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});expect(queue).toMatchObject({cursor:0,revision:1,history:[]});expect(queue.items.map((row:any)=>row.relative_path)).toEqual(['error.png','disagreement.png','threshold.png']);queues.push(queue);}
 expect(queues[0].id).not.toBe(queues[1].id);
 for(const route of ['/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/training/jobs','/api/dataset/versions'])await api(route);
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(3);for(const input of inputs){expect(metadata.find((row:any)=>row.file_path===input.file).content_hash).toBe(input.sha256);expect((await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file))).annotations).toEqual([]);}
 const queueList=await api(endpoint);expect(queueList.queues).toHaveLength(2);expect(new Set(queueList.queues.map((row:any)=>row.id))).toEqual(new Set(queues.map(row=>row.id)));const first=queueList.queues[0],selected=queueList.queues[1],missing='missing-owned-saved-review-queue';expect(queueList.queues.some((row:any)=>row.id===missing)).toBe(false);
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true}),current=panel.getByRole('button',{name:'현재 항목 열기',exact:true});
 const enter=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await expect(panel).toBeVisible();};
 const captures:any[]=[],mutations:any[]=[],invalidRequests:any[]=[],pageErrors:string[]=[],allRequests:any[]=[],pending=new Set<Promise<{ok:true;row:any}|{ok:false;error:unknown}>>();
 const observer=(request:Request)=>{const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;const row={method:request.method(),path:u.pathname,search:u.search,body:request.postData(),main_frame:request.frame()===page.mainFrame()};allRequests.push(row);if(!['GET','HEAD','OPTIONS'].includes(row.method))mutations.push(row);if(u.pathname===endpoint+'/'+missing)invalidRequests.push(row);};const errorObserver=(error:Error)=>pageErrors.push(String(error));
 const mount=async(label:string,expected:string,firstNavigation=false)=>{const deadline=performance.now()+10_000,wire=page.waitForResponse(response=>{const request=response.request(),u=new URL(response.url());return request.method()==='GET'&&request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===endpoint&&u.search==='';},{timeout:10_000});
  const collecting=(async()=>{const response=await splitControlWithin(wire,deadline,label+' original queue list');expect(response.status()).toBe(200);const bytes=await splitControlWithin(response.body(),deadline,label+' original fullbody');expect(await splitControlWithin(response.finished(),deadline,label+' original finished')).toBeNull();const body=JSON.parse(bytes.toString('utf8'));expect(body).toEqual(queueList);const file=path.join(w.logs,'saved-queue-pref-'+captures.length+'-'+label+'-original-body.json');fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);return {label,method:'GET',path:endpoint,origin,status:200,main_frame:true,finished:true,original_HTTP:true,controlled_response:false,file,sha256:hash(bytes),size:bytes.length,body};})();
  const outcome=collecting.then(row=>({ok:true as const,row}),error=>({ok:false as const,error}));pending.add(outcome);await splitControlWithin(firstNavigation&&url?page.goto(url):page.reload(),deadline,label+' document load');await splitControlWithin(enter(),deadline,label+' original panel');const result=await splitControlWithin(outcome,deadline,label+' list readback');pending.delete(outcome);if(!result.ok)throw result.error;captures.push(result.row);await expect(choice).toHaveValue(expected);await expect(current).toBeEnabled();await expect(panel).toContainText('검토 진행 0 / 3');};
 const protectedState=()=>({source:splitControlTree(source,custody),annotations:splitControlTree(project.annotations_dir,custody),models:splitControlTree(project.models_dir,custody),reports:splitControlTree(project.reports_dir,custody),dataset:splitControlTree(project.dataset_dir,custody)});
 const durable=async()=>({queues:await api(endpoint),records:await Promise.all(queues.map(q=>api(endpoint+'/'+q.id))),jobs:await api('/api/training/jobs'),labels:await Promise.all(inputs.map(input=>api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file))))});
 let primary:unknown,preference:{key:string;value:string}|undefined,changed=false,baseline:any,baselineDurable:any;
 try{
  await mount('initial-real-list',first.id,true);await choice.selectOption(selected.id);await expect(choice).toHaveValue(selected.id);await expect(panel.getByRole('alert')).toHaveCount(0);
  const entries=await page.evaluate(value=>Object.entries(localStorage).filter(([key,item])=>key.startsWith('modu-review-queue:')&&item===value),selected.id);expect(entries).toHaveLength(1);preference={key:entries[0][0],value:entries[0][1]};expect(preference.value).toBe(selected.id);
  baselineDurable=await durable();expect(baselineDurable.jobs.jobs).toEqual([]);expect(baselineDurable.labels.every((row:any)=>row.annotations.length===0)).toBe(true);baseline=protectedState();await panel.scrollIntoViewIfNeeded();await expect(panel).toBeInViewport();await e.screenshot(page,'saved-queue-valid-nondefault-before-optional-pref-fault');
  page.on('request',observer);page.on('pageerror',errorObserver);
  // Deliberate corruption of one optional local display preference only.
  // No DOM option is invented and no queue/origin/source record is modified.
  changed=true;await page.evaluate(({key,missing})=>localStorage.setItem(key,missing),{key:preference.key,missing});expect(await page.evaluate(key=>localStorage.getItem(key),preference.key)).toBe(missing);
  await mount('unknown-pref-real-reload-list',first.id);await expect(choice.locator('option[value="'+missing+'"]')).toHaveCount(0);expect(await page.evaluate(key=>localStorage.getItem(key),preference.key)).toBe(missing);await expect(panel.getByRole('alert')).toHaveCount(0);expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(mutations).toEqual([]);expect(invalidRequests).toEqual([]);await panel.scrollIntoViewIfNeeded();await expect(panel).toBeInViewport();await e.screenshot(page,'saved-queue-missing-remembered-id-falls-back-to-own-first-valid-queue');
  // A deliberate existing valid option writes the original remembered id;
  // genuine reload then restores it, without any queue create/open POST.
  await choice.selectOption(selected.id);await expect(choice).toHaveValue(selected.id);expect(await page.evaluate(key=>localStorage.getItem(key),preference.key)).toBe(preference.value);changed=false;await mount('explicit-valid-selection-real-reload-list',selected.id);await expect(choice.locator('option[value="'+missing+'"]')).toHaveCount(0);expect(await page.evaluate(key=>localStorage.getItem(key),preference.key)).toBe(preference.value);await expect(panel.getByRole('alert')).toHaveCount(0);expect(protectedState()).toEqual(baseline);expect(await durable()).toEqual(baselineDurable);expect(mutations).toEqual([]);expect(invalidRequests).toEqual([]);expect(pageErrors).toEqual([]);expect(captures).toHaveLength(3);await panel.scrollIntoViewIfNeeded();await expect(panel).toBeInViewport();await e.screenshot(page,'saved-queue-deliberate-valid-nondefault-restored-after-real-reload');
  e.note('saved_queue_selection_invalid_pref',{schema:'modu-vision.u015-saved-queue-selection-invalid-pref/v1',selected_pending_pointers:[cell],mode:native?'source-electron':'browser',project,source,inputs,fixtures,queue_ids:queues.map(q=>q.id),original_list:queueList,preference_before:preference,controlled_preference:{key:preference.key,invalid_value:missing},fallback_queue_id:first.id,deliberate_recovery_queue_id:selected.id,preference_final:{key:preference.key,value:await page.evaluate(key=>localStorage.getItem(key),preference.key)},captures,protected_before:baseline,protected_after:protectedState(),baseline_durable:baselineDurable,final_durable:await durable(),mutations,invalidRequests,allRequests,pageErrors,one_optional_preference_fault:true,unknown_id_not_option_or_GET_authority:true,actual_document_reload:true,existing_own_first_queue_fallback:true,explicit_valid_selection_recovery:true,no_data_or_queue_mutation:true,controlled_API_response:false,actual_model_inference:false,GPU_used:false,Native_accepted:false,parent_completion_credit:false,quality_human_installed_target_approval:false});
 }catch(cause){primary=cause;}
 finally{
  page.off('request',observer);page.off('pageerror',errorObserver);
  for(const waiting of pending){try{const result=await waiting;if(!result.ok&&primary===undefined)primary=result.error;}catch(cause){if(primary===undefined)primary=cause;}}
  if(changed&&preference&&!page.isClosed()){try{await page.evaluate(({key,value})=>localStorage.setItem(key,value),preference);expect(await page.evaluate(key=>localStorage.getItem(key),preference.key)).toBe(preference.value);changed=false;}catch(cause){if(primary===undefined)primary=cause;}}
  try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);expect(changed).toBe(false);e.note('saved_queue_selection_invalid_pref_closure',{owned_FDs_opened:custody.opened,owned_FDs_closed:custody.closed,uncertain:custody.uncertain,remaining_owned_handlers:0,all_original_read_waits_joined:true,controlled_optional_preference_restored:!changed,first_exception:primary===undefined?null:String(primary)});}catch(cause){if(primary===undefined)primary=cause;}
 }
 if(primary!==undefined)throw primary;
}
test('saved queue selection rejects a missing remembered id and restores deliberate owning choice after real reload',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native saved queue selection rejects a missing remembered id and restores deliberate owning choice after real reload',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const backend=await electronSession.waitForBackend();await exercise(electronSession.window,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Request} from '@playwright/test';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi,handoffWithin} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
const raw=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs,s.uid,s.gid].map(String);
type FilePin={bytes:Buffer;sha256:string;raw9:string[]};
type Custody={opened:number;closed:number;uncertain:Array<{path:string;error:string}>};
function readFile(file:string,custody:Custody):FilePin{
 const before=fs.lstatSync(file,{bigint:true});expect(before.isFile()).toBe(true);expect(before.isSymbolicLink()).toBe(false);expect(before.nlink).toBe(1n);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);custody.opened++;let primary:unknown,failed=false,value:FilePin|undefined;
 try{expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(before));const bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(before.size);expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(raw(before));expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(before));value={bytes,sha256:hash(bytes),raw9:raw(before)};}
 catch(error){failed=true;primary=error;}finally{try{fs.closeSync(fd);custody.closed++;}catch(error){custody.uncertain.push({path:file,error:String(error)});if(!failed){failed=true;primary=error;}}}
 if(failed)throw primary;return value!;
}
function tree(root:string,custody:Custody):Record<string,unknown>{
 const out:Record<string,unknown>={};const walk=(file:string,relative:string)=>{const s=fs.lstatSync(file,{bigint:true});expect(s.isSymbolicLink()).toBe(false);
  if(s.isDirectory()){const names=fs.readdirSync(file).sort();out[relative]={kind:'directory',raw9:raw(s),names};for(const name of names)walk(path.join(file,name),relative?relative+'/'+name:name);expect(fs.readdirSync(file).sort()).toEqual(names);expect(raw(fs.lstatSync(file,{bigint:true}))).toEqual(raw(s));}
  else{const row=readFile(file,custody);out[relative]={kind:'regular',raw9:row.raw9,sha256:row.sha256};}
 };walk(root,'');return out;
}
function replaceOneOwnedByte(file:string,expected:FilePin,offset:number,next:number,custody:Custody){
 const current=readFile(file,custody);expect(current).toEqual(expected);expect(offset).toBeGreaterThanOrEqual(0);expect(offset).toBeLessThan(current.bytes.length);expect(current.bytes[offset]).not.toBe(next);
 const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_NOFOLLOW);custody.opened++;let primary:unknown,failed=false;
 try{expect(raw(fs.fstatSync(fd,{bigint:true}))).toEqual(expected.raw9);expect(fs.writeSync(fd,Buffer.from([next]),0,1,offset)).toBe(1);fs.fsyncSync(fd);}
 catch(error){failed=true;primary=error;}finally{try{fs.closeSync(fd);custody.closed++;}catch(error){custody.uncertain.push({path:file,error:String(error)});if(!failed){failed=true;primary=error;}}}
 if(failed)throw primary;
 const changed=readFile(file,custody),wanted=Buffer.from(expected.bytes);wanted[offset]=next;expect(changed.bytes).toEqual(wanted);
 for(const i of[0,1,2,3,4,7,8])expect(changed.raw9[i]).toBe(expected.raw9[i]);return changed;
}
type Read={request:Request;started:number;deadline:number;owned:Promise<void>;status?:number;sha256?:string;bytes?:Buffer;finished?:number;error?:unknown;failed:boolean};

test('saved queue open refuses an actual invalid owned evaluation record without changing the selected queue or originals',async({page,renderer,workspace,evidence})=>{
 // One browser-only U015.saved-queue-open.invalid cell. No cancel, success,
 // error, reopen, handoff, training, native or model-quality credit is added.
 await installDesktopHostShim(page,renderer.port);
 const api=handoffApi(page,renderer.origin,false),custody:Custody={opened:0,closed:0,uncertain:[]};
 const source=path.join(workspace.root,'queue-open-invalid-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,70+index]),{flag:'wx'});return {name,file,sha256:hash(fs.readFileSync(file))};});
 const created=await api('/api/project/create',{name:'Owned queue invalid origin',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation',validate_images:false});
 const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(workspace.projects);
 expect(fs.realpathSync(source)).toBe(source);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),workspace.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(fixture.path).toBe(path.join(project.project_dir,'reports','evaluations',fixture.record.evaluation_id+'.json'));expect(path.resolve(fixture.path)).toBe(fixture.path);expect(fs.realpathSync(fixture.path)).toBe(fixture.path);
 const ownedRelative=path.relative(workspace.root,fixture.path);expect(path.isAbsolute(ownedRelative)).toBe(false);expect(ownedRelative==='..'||ownedRelative.startsWith('..'+path.sep)).toBe(false);
 for(let parent=path.dirname(fixture.path);;parent=path.dirname(parent)){const info=fs.lstatSync(parent);expect(info.isDirectory()).toBe(true);expect(info.isSymbolicLink()).toBe(false);if(parent===workspace.root)break;expect(parent).not.toBe(path.dirname(parent));}
 const originOriginal=readFile(fixture.path,custody);expect(originOriginal.sha256).toBe(fixture.sha256);expect(JSON.parse(originOriginal.bytes.toString('utf8'))).toEqual(fixture.record);
 const originalEvidence=path.join(workspace.logs,'queue-invalid-owned-report-original.json');fs.writeFileSync(originalEvidence,originOriginal.bytes,{flag:'wx'});evidence.addFile(originalEvidence);
 const queue=await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});
 expect(queue).toMatchObject({cursor:0,revision:1,history:[],origin:{evaluation_id:fixture.record.evaluation_id,evidence_sha256:fixture.record.evidence_sha256}});
 expect(queue.items.map((r:any)=>[r.relative_path,r.priority,r.state])).toEqual([['error.png',400,'pending'],['disagreement.png',200,'pending'],['threshold.png',100,'pending']]);
 const queuePath='/api/data-workbench/review-queues/'+queue.id,queueURL=renderer.origin+queuePath;
 const annotation=(input:typeof inputs[number])=>'/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file);
 // Existing default constructors are initialized before the immutable baseline.
 const params=new URLSearchParams({source_dataset_path:source,task:'segmentation'}).toString();
 for(const endpoint of ['/api/model-deployments/active?'+new URLSearchParams({source_dataset_path:source,task:'ocr'}),'/api/model-deployments/active?'+params,'/api/model-deployments/history?'+params,'/api/fleet/targets','/api/fleet/capabilities','/api/fleet/rollouts','/api/runtime-services/capture-groups','/api/runtime-services'])await api(endpoint);
 const endpoints=['/api/project/current','/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/dataset/versions','/api/dataset/metadata?limit=100','/api/dataset/metadata/split','/api/data-workbench/review-evaluations','/api/data-workbench/review-queues',queuePath,'/api/training/jobs'];
 const state=async()=>{const out:Record<string,any>={};for(const endpoint of endpoints)out[endpoint]=await api(endpoint);for(const input of inputs)out[annotation(input)]=await api(annotation(input));return out;};await state();
 const reads:Read[]=[],writes:Array<{method:string;path:string;body:string|null}>=[],pageErrors:string[]=[],consoleErrors:Array<{text:string;url:string}>=[],non2xx:Array<{request:Request;status:number;url:string}>=[];
 let invalidArmed=false,invalidRequest:Request|undefined;
 const observe=(request:Request)=>{const u=new URL(request.url());if(u.origin!==renderer.origin||!u.pathname.startsWith('/api/'))return;
  if(!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postData()});
  if(request.method()!=='GET')return;const started=performance.now();
  if(invalidArmed&&request.url()===queueURL){invalidRequest=request;invalidArmed=false;}
  const row:Read={request,started,deadline:started+10_000,owned:Promise.resolve(),failed:false};reads.push(row);
  row.owned=(async()=>{const response=await handoffWithin(request.response(),row.deadline,'original UI GET response');expect(response).not.toBeNull();const invalid=request===invalidRequest;
   expect(response!.status()).toBe(invalid?422:200);const bytes=await handoffWithin(response!.body(),row.deadline,'complete original UI GET body');expect(await handoffWithin(response!.finished(),row.deadline,'original UI GET finish')).toBeNull();
   if(invalid){expect(request.frame()).toBe(page.mainFrame());expect(await handoffWithin(request.headerValue('x-vision-project'),row.deadline,'own project header')).toBe(project.id);const context=await handoffWithin(request.headerValue('x-vision-context'),row.deadline,'own context header');expect(context).not.toBeNull();expect(JSON.parse(context!).project_id).toBe(project.id);expect(JSON.parse(bytes.toString('utf8'))).toEqual({detail:'Evaluation integrity failure'});expect(request.postData()).toBeNull();}
   row.status=response!.status();row.bytes=bytes;row.sha256=hash(bytes);row.finished=performance.now();expect(row.finished).toBeLessThan(row.deadline);
  })().catch(error=>{row.failed=true;row.error=error;});
 };
 const settle=async()=>{for(const r of reads){await r.owned;if(r.failed)throw r.error;expect(r.finished).toBeDefined();}};
 const pageError=(error:Error)=>pageErrors.push(error.message),consoleError=(message:{type():string;text():string;location():{url:string}})=>{if(message.type()==='error')consoleErrors.push({text:message.text(),url:message.location().url});};
 const responseError=(response:import('@playwright/test').Response)=>{if(response.status()>=400)non2xx.push({request:response.request(),status:response.status(),url:response.url()});};
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true}),open=panel.getByRole('button',{name:'현재 항목 열기',exact:true});
 const snapshot=()=>({source:tree(source,custody),project:tree(project.project_dir,custody)});
 const remembered=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu-review-queue:')).sort(([a],[b])=>a.localeCompare(b)));
 const reportRelative=path.relative(project.project_dir,fixture.path).split(path.sep).join('/');expect(reportRelative.startsWith('reports/evaluations/evaluation_')).toBe(true);
 let before:ReturnType<typeof snapshot>|undefined,invalidBaseline:ReturnType<typeof snapshot>|undefined,baselineState:Record<string,any>|undefined,rememberedBefore:Awaited<ReturnType<typeof remembered>>|undefined,originInvalid:FilePin|undefined,offset=-1,originalByte=-1;
 let plannedInvalidBytes:Buffer|undefined;let mutationAttempted=false,restored=false,completed=false,failed=false,primary:unknown,proof:unknown;const secondary:Array<{stage:string;error:string}>=[];
 const retain=(stage:string,error:unknown)=>{if(!failed){failed=true;primary=error;}else secondary.push({stage,error:String(error)});};
 const compareExceptOwnedReport=(original:ReturnType<typeof snapshot>,current:ReturnType<typeof snapshot>)=>{expect(current.source).toEqual(original.source);expect(Object.keys(current.project)).toEqual(Object.keys(original.project));for(const key of Object.keys(original.project))if(key!==reportRelative)expect(current.project[key]).toEqual(original.project[key]);};
 page.on('request',observe);page.on('pageerror',pageError);page.on('console',consoleError);page.on('response',responseError);
 try{
  const startupDeadline=performance.now()+10_000;await handoffWithin(page.goto(renderer.url),startupDeadline,'original document mount');await handoffWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name),startupDeadline,'own project title');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();
  const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();
  await expect(panel).toBeVisible();await expect(choice).toHaveValue(queue.id);await expect(open).toBeEnabled();await expect(panel).toContainText('검토 진행 0 / 3');
  // Real selection before custody creates the owning remembered key; this is setup only.
  await choice.selectOption('');await choice.selectOption(queue.id);await expect(open).toBeEnabled();await settle();
  baselineState=await state();expect(baselineState[queuePath]).toEqual(queue);expect(baselineState['/api/data-workbench/review-queues']).toEqual({queues:[queue]});expect(baselineState['/api/training/jobs'].jobs).toEqual([]);
  await settle();before=snapshot();rememberedBefore=await remembered();expect(rememberedBefore.some(([,value])=>value===queue.id)).toBe(true);expect(writes).toEqual([]);expect(pageErrors).toEqual([]);expect(consoleErrors).toEqual([]);expect(non2xx).toEqual([]);
  // Cause one actual invalid backend state in our own controlled report, without
  // touching the saved queue, dataset, labels, project config or source images.
  const needle=Buffer.from('"evidence_sha256":"'+fixture.record.evidence_sha256+'"');const start=originOriginal.bytes.indexOf(needle);expect(start).toBeGreaterThanOrEqual(0);expect(originOriginal.bytes.indexOf(needle,start+1)).toBe(-1);
  offset=start+Buffer.byteLength('"evidence_sha256":"');originalByte=originOriginal.bytes[offset];plannedInvalidBytes=Buffer.from(originOriginal.bytes);plannedInvalidBytes[offset]=originalByte===48?49:48;
  const invalidEvidence=path.join(workspace.logs,'queue-invalid-owned-report-corrupted.json');fs.writeFileSync(invalidEvidence,plannedInvalidBytes,{flag:'wx'});evidence.addFile(invalidEvidence);mutationAttempted=true;
  originInvalid=replaceOneOwnedByte(fixture.path,originOriginal,offset,plannedInvalidBytes[offset],custody);
  const invalidRecord=JSON.parse(originInvalid.bytes.toString('utf8'));expect({...invalidRecord,evidence_sha256:fixture.record.evidence_sha256}).toEqual(fixture.record);expect(invalidRecord.evidence_sha256).not.toBe(fixture.record.evidence_sha256);
  invalidBaseline=snapshot();compareExceptOwnedReport(before,invalidBaseline);expect(readFile(fixture.path,custody)).toEqual(originInvalid);
  await expect(choice).toHaveValue(queue.id);await expect(open).toBeEnabled();const requestCount=reads.length,deadline=performance.now()+10_000;invalidArmed=true;
  await handoffWithin(open.click(),deadline,'original current-item button');await handoffWithin(expect(panel.getByRole('alert')).toHaveText('Evaluation integrity failure'),deadline,'original backend invalid alert');await settle();
  expect(invalidRequest).toBeDefined();expect(invalidArmed).toBe(false);const actual=reads.filter(r=>r.request===invalidRequest);expect(actual).toHaveLength(1);expect(actual[0].status).toBe(422);expect(actual[0].request.url()).toBe(queueURL);expect(reads.slice(requestCount).filter(r=>new URL(r.request.url()).pathname===queuePath)).toHaveLength(1);
  await expect(choice).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');await expect(open).toBeEnabled();expect(await remembered()).toEqual(rememberedBefore);expect(snapshot()).toEqual(invalidBaseline);expect(writes).toEqual([]);expect(pageErrors).toEqual([]);
  expect(non2xx).toHaveLength(1);expect(non2xx[0]).toEqual({request:invalidRequest,status:422,url:queueURL});expect(consoleErrors).toEqual([{text:'Failed to load resource: the server responded with a status of 422 (Unprocessable Entity)',url:queueURL}]);
  await evidence.screenshot(page,'browser-queue-real-invalid-origin422-selected-queue-unchanged');
  proof={schema:'modu-vision.U015-saved-queue-open-invalid/v1',cells:['U015.saved-queue-open.invalid'],own_project:project,source,controlled_fixture:fixture,selected_queue:queue,remembered_before:rememberedBefore,remembered_after:await remembered(),actual_request:{method:invalidRequest!.method(),url:invalidRequest!.url(),body:invalidRequest!.postData(),main_frame:invalidRequest!.frame()===page.mainFrame()},actual_response:{status:actual[0].status,bytes:actual[0].bytes!.length,sha256:actual[0].sha256,body:JSON.parse(actual[0].bytes!.toString('utf8')),started:actual[0].started,deadline:actual[0].deadline,finished:actual[0].finished},one_owned_report_mutation:{path:fixture.path,original_evidence_copy:originalEvidence,offset,original_sha256:originOriginal.sha256,invalid_sha256:originInvalid.sha256,original_raw9:originOriginal.raw9,invalid_raw9:originInvalid.raw9,only_field:'evidence_sha256',same_length_same_inode_mode_owner_nlink:true},original_project_and_source:before,invalid_baseline:invalidBaseline,after_click:snapshot(),after_baseline_nonGET_requests:writes,expected_real_backend_HTTP422:true,controlled_HTTP_response_or_abort:false,no_page_route_or_fulfill:true,backend_rejects_actual_fixture_file_integrity:true,no_cancel_or_other_passed_cell_credit:true};completed=true;
 }catch(error){retain('scenario',error);}finally{
  try{await settle();}catch(error){retain('owned original GET closure',error);}
  // Restore only the one owned byte even on an assertion failure. Metadata
  // clocks are intentionally not restored or reported as raw9-identical.
  if(mutationAttempted){try{const current=readFile(fixture.path,custody);expect(current.bytes.length).toBe(originOriginal.bytes.length);const desired=Buffer.from(originOriginal.bytes);expect(current.bytes.equals(desired)||current.bytes.equals(plannedInvalidBytes??Buffer.alloc(0))).toBe(true);
    if(!current.bytes.equals(desired))replaceOneOwnedByte(fixture.path,current,offset,originalByte,custody);
    const final=readFile(fixture.path,custody);expect(final.bytes).toEqual(originOriginal.bytes);expect(final.sha256).toBe(originOriginal.sha256);for(const i of[0,1,2,3,4,7,8])expect(final.raw9[i]).toBe(originOriginal.raw9[i]);restored=true;
    if(before)compareExceptOwnedReport(before,snapshot());evidence.note('queue_invalid_owned_report_restoration',{path:fixture.path,original_sha256:originOriginal.sha256,restored_sha256:final.sha256,original_raw9:originOriginal.raw9,restored_raw9:final.raw9,body_exact:true,mtime_ctime_not_reset_or_claimed_unchanged:true});
   }catch(error){retain('exact owned report body restoration',error);}}
  if(restored&&baselineState){try{expect(await state()).toEqual(baselineState);expect(await remembered()).toEqual(rememberedBefore);expect(writes).toEqual([]);}catch(error){retain('restored original API bodies and selection',error);}}
  try{page.off('request',observe);page.off('pageerror',pageError);page.off('console',consoleError);page.off('response',responseError);expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);}catch(error){retain('owned observer and FD closure',error);}
  evidence.note('queue_open_invalid_progress',{proof,completed,restored,FDs:custody,read_ledger:reads.map(r=>({url:r.request.url(),method:r.request.method(),started:r.started,deadline:r.deadline,status:r.status,sha256:r.sha256,finished:r.finished,failure:r.failed?String(r.error):null})),writes,page_errors:pageErrors,console_errors:consoleErrors,non2xx:non2xx.map(r=>({url:r.url,status:r.status,owning_invalid:r.request===invalidRequest})),first_exception:failed?String(primary):null,secondary,controlled_saved_reports_not_inference:true,product_source_or_existing_tests_changed:false,cancel_or_native_or_installed_or_parent_acceptance:false});
 }
 if(failed)throw primary;expect(completed).toBe(true);expect(restored).toBe(true);
});

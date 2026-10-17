import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');


// U015 four exact empty-state controls. No model, queue creation or training is credited.
import type {Response as QueueEmptyResponse} from '@playwright/test';
import {handoffApi} from './fixtures/remaining-project-handoff';
test.use({actionTimeout:10_000});
const hash=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
type SplitControlRequest=import('@playwright/test').Request;
type SplitControlRow={image:string;text:string;split:'train'|'val'|'test'};
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
async function splitControlBody(response:import('@playwright/test').Response,deadline:number){
 expect(response.status()).toBe(200);const bytes=await splitControlWithin(response.body(),deadline,'full response body');expect(await splitControlWithin(response.finished(),deadline,'HTTP completion')).toBeNull();return {body:JSON.parse(bytes.toString('utf8')),sha256:hash(bytes),status:200};
}

const QUEUE_EMPTY_POINTERS=[
 '/records/137/actions/1/scenarios/empty',
 '/records/137/actions/9/scenarios/empty',
 '/records/137/actions/6/scenarios/empty',
 '/records/137/actions/7/scenarios/empty',
] as const;
type QueueEmptyWire={request:SplitControlRequest;started:number;deadline:number;complete:boolean;status?:number;sha256?:string;failure?:string};
async function queueEmptyExercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api:Api=handoffApi(page,origin,native),custody:SplitControlCustody={opened:0,closed:0,uncertain:[]};
 const source=path.join(w.root,'queue-empty-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,index)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,50+index]),{flag:'wx'});return {name,file,sha256:hash(fs.readFileSync(file))};});
 const created=await api('/api/project/create',{name:'Owned empty queue controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const project=await api('/api/project/current'),owner=native?path.join(w.userData,'projects'):w.projects;
 expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(owner);expect(fs.realpathSync(source)).toBe(source);expect(fs.realpathSync(project.project_dir)).toBe(project.project_dir);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(splitControlFile(fixture.path,custody).sha256).toBe(fixture.sha256);
 const queue=await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});
 expect(queue.items.map((row:any)=>[row.relative_path,row.priority,row.state])).toEqual([['error.png',400,'pending'],['disagreement.png',200,'pending'],['threshold.png',100,'pending']]);expect(queue).toMatchObject({cursor:0,revision:1,history:[],origin:{evaluation_id:fixture.record.evaluation_id}});
 const team=await api('/api/team-data');expect(team.settings.editing_enabled).toBe(false);
 await api('/api/team-data/settings',{expected_revision:team.settings.revision,actor:'owned-U015-empty-fixture',changes:{review_enabled:true,approved_only_training:true}},'PUT');
 // Original GET constructors materialize only owned default files before custody starts.
 const params=new URLSearchParams({source_dataset_path:source,task:'segmentation'}).toString();
 for(const endpoint of ['/api/model-deployments/active?'+new URLSearchParams({source_dataset_path:source,task:'ocr'}),'/api/model-deployments/active?'+params,'/api/model-deployments/history?'+params,'/api/fleet/targets','/api/fleet/capabilities','/api/fleet/rollouts','/api/runtime-services/capture-groups','/api/runtime-services'])await api(endpoint,undefined,'GET');
 const queuePath='/api/data-workbench/review-queues/'+queue.id;
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(3);
 for(const input of inputs){const image=metadata.find((row:any)=>row.file_path===input.file);expect(image).toBeTruthy();expect(image.content_hash).toBe(input.sha256);expect(image.workflow_state).not.toBe('approved');await api('/api/team-data/images/'+image.image_uuid);const annotation=await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file));expect(annotation.annotations).toEqual([]);}
 const endpoints=['/api/project/current','/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/dataset/versions','/api/dataset/metadata?limit=100','/api/dataset/metadata/split','/api/data-workbench/review-evaluations','/api/data-workbench/review-queues',queuePath,'/api/training/jobs'];
 const readState=async()=>{const result:Record<string,any>={};for(const endpoint of endpoints)result[endpoint]=await api(endpoint,undefined,'GET');for(const input of inputs)result['annotation:'+input.file]=await api('/api/annotations/'+input.name+'?file_path='+encodeURIComponent(input.file));return result;};
 await readState();
 const reads:QueueEmptyWire[]=[],writes:Array<{method:string;path:string;body:string|null}>=[],trainingRequests:string[]=[];
 const observe=(request:SplitControlRequest)=>{const u=new URL(request.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
  if(request.method()==='GET')reads.push({request,started:performance.now(),deadline:performance.now()+10_000,complete:false});
  else if(!['HEAD','OPTIONS'].includes(request.method())){writes.push({method:request.method(),path:u.pathname,body:request.postData()});if(/\/(train|training|jobs|evaluate|predict)(\/|$)/.test(u.pathname))trainingRequests.push(u.pathname);}
 };
 const settleOriginalReads=async()=>{for(const row of reads){if(row.complete)continue;try{const response=await splitControlWithin(row.request.response(),row.deadline,'original owned API GET');expect(response).not.toBeNull();expect(response!.status()).toBe(200);const bytes=await splitControlWithin(response!.body(),row.deadline,'original GET whole bytes');expect(await splitControlWithin(response!.finished(),row.deadline,'original GET completion')).toBeNull();row.complete=true;row.status=response!.status();row.sha256=hash(bytes);}catch(cause){row.failure=String(cause);throw cause;}}};
 const treeState=()=>({source:splitControlTree(source,custody),project:splitControlTree(project.project_dir,custody)});
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),stages=page.getByRole('navigation',{name:'Workflow Stages'});
 const choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true}),threshold=panel.getByRole('spinbutton',{name:'검토 큐 임계값',exact:true}),margin=panel.getByRole('spinbutton',{name:'검토 큐 임계 주변 범위',exact:true});
 const returnOrigin=panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true}),prepare=panel.getByRole('button',{name:'수정·검수 데이터로 학습 준비',exact:true});
 const mount=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await stages.getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await expect(panel).toBeVisible();await expect(choice).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');};
 let baseline:ReturnType<typeof treeState>|undefined,baselineState:Record<string,any>|undefined,proof:Record<string,unknown>|undefined,primary:unknown,failed=false,removed=false,completed=false;const secondary:string[]=[];
 page.on('request',observe);
 try{
  if(url)await page.goto(url);else await page.reload();await mount();await settleOriginalReads();
  baselineState=await readState();const readiness=baselineState['/api/team-data/readiness'];expect(readiness.ready).toBe(false);expect(readiness.counts.total).toBe(3);expect(readiness.counts.approved).toBe(0);expect(readiness.counts.eligible).toBe(0);expect(readiness.eligible_image_uuids).toEqual([]);expect(readiness.blockers).toContain('학습에 사용할 이미지가 없습니다. 라벨 승인·미사용 상태를 확인하세요.');expect(baselineState['/api/team-data'].settings.approved_only_training).toBe(true);expect(baselineState['/api/training/jobs'].jobs).toEqual([]);expect(baselineState[queuePath]).toEqual(queue);expect(baselineState['/api/data-workbench/review-queues'].queues).toEqual([queue]);
  await settleOriginalReads();baseline=treeState();const writeStart=writes.length;expect(writes).toEqual([]);
  const same=async()=>{await settleOriginalReads();expect(treeState()).toEqual(baseline);expect(await readState()).toEqual(baselineState);expect(writes.slice(writeStart)).toEqual([]);expect(trainingRequests).toEqual([]);};
  // Real clearing enters the existing onChange(Number('')) branch: blank normalizes to0.
  await expect(threshold).toHaveValue('0.5');await threshold.fill('');await expect(threshold).toHaveValue('0');await expect(panel.getByRole('button',{name:'우선순위 큐 저장',exact:true})).toBeEnabled();await same();
  await e.screenshot(page,`${native?'native':'browser'}-queue-empty-threshold-normalizes-zero`);await threshold.fill('0.5');await expect(threshold).toHaveValue('0.5');
  await expect(margin).toHaveValue('0.05');await margin.fill('');await expect(margin).toHaveValue('0');await expect(panel.getByRole('button',{name:'우선순위 큐 저장',exact:true})).toBeEnabled();await same();
  await e.screenshot(page,`${native?'native':'browser'}-queue-empty-margin-normalizes-zero`);await margin.fill('0.05');await expect(margin).toHaveValue('0.05');
  // The actual blank saved-queue option has no originating queue action. No invented ID.
  const beforeEmptyReads=reads.filter(row=>new URL(row.request.url()).pathname===queuePath).length;
  await choice.selectOption('');await expect(choice).toHaveValue('');await expect(returnOrigin).toHaveCount(0);await expect(prepare).toHaveCount(0);await expect(panel.getByRole('button',{name:'현재 항목 열기',exact:true})).toHaveCount(0);await settleOriginalReads();
  expect(reads.filter(row=>new URL(row.request.url()).pathname===queuePath)).toHaveLength(beforeEmptyReads);await same();await e.screenshot(page,`${native?'native':'browser'}-queue-empty-origin-action-absent`);
  await choice.selectOption(queue.id);await expect(choice).toHaveValue(queue.id);await expect(prepare).toBeEnabled();await expect(returnOrigin).toBeEnabled();
  const deadline=performance.now()+10_000;
  const readyWire=page.waitForResponse(response=>response.request().method()==='GET'&&new URL(response.url()).origin===origin&&new URL(response.url()).pathname==='/api/team-data/readiness',{timeout:10_000});await splitControlWithin(prepare.click(),deadline,'own training preparation button');const readyResponse:QueueEmptyResponse=await splitControlWithin(readyWire,deadline,'own readiness response');const ready=await splitControlBody(readyResponse,deadline);expect(ready.body).toEqual(readiness);await expect(panel.getByRole('alert')).toHaveText(readiness.blockers.join(' · '));await expect(panel).toBeVisible();await expect(page.getByRole('region',{name:'모델 학습 허브',exact:true})).toHaveCount(0);await expect(choice).toHaveValue(queue.id);expect(reads.filter(row=>row.request===readyResponse.request())).toHaveLength(1);await same();
  await e.screenshot(page,`${native?'native':'browser'}-queue-empty-eligible-training-refused`);
  proof={selected_pending_pointers:QUEUE_EMPTY_POINTERS,cells:[{action:'U015.saved-queue-threshold',dimension:'empty',real_clear_normalized_to:0,queue_POSTs:0},{action:'U015.saved-queue-margin',dimension:'empty',real_clear_normalized_to:0,queue_POSTs:0},{action:'U015.saved-queue-stale-origin',dimension:'empty',real_blank_saved_queue:true,origin_button_absent:true,origin_action_GETs:0},{action:'U015.saved-queue-prepare-training',dimension:'empty',real_button_clicked:true,readiness:ready,eligible:0,stage3_not_entered:true,training_POSTs:0}],project,inputs,fixture,original_saved_queue:queue,baseline_state:baselineState,after_state:await readState(),protected_before:baseline,protected_after:treeState(),writes,training_requests:trainingRequests,original_read_count:reads.length,own_readiness_original_HTTP200:true,own_ready_fullbody_equals_saved_baseline:true,queue_cursor_revision_history_unchanged:true,source_images_and_unapproved_empty_annotations_unchanged:true,numeric_empty_is_normalization_not_validator_rejection:true,only_four_empty_dimensions_no_reopen_or_handoff_credit:true,controlled_saved_report_not_model_inference:true,installed_Native_model_quality_human_parent_acceptance:false,native};completed=true;
 }catch(cause){failed=true;primary=cause;}
 finally{
  try{await settleOriginalReads();}catch(cause){secondary.push('original GET closure: '+String(cause));if(!failed){failed=true;primary=cause;}}
  try{page.off('request',observe);removed=true;}catch(cause){secondary.push('observer removal: '+String(cause));if(!failed){failed=true;primary=cause;}}
  if(baseline!==undefined){try{expect(treeState()).toEqual(baseline);}catch(cause){secondary.push('final full tree custody: '+String(cause));if(!failed){failed=true;primary=cause;}}}
  if(baselineState!==undefined){try{expect(await readState()).toEqual(baselineState);}catch(cause){secondary.push('final original body custody: '+String(cause));if(!failed){failed=true;primary=cause;}}}
  try{expect(custody.opened).toBe(custody.closed);expect(custody.uncertain).toEqual([]);}catch(cause){secondary.push('owned FD closure: '+String(cause));if(!failed){failed=true;primary=cause;}}
  e.note('queue_empty_control_closure',{all_owned_FDs_closed:custody.opened===custody.closed&&custody.uncertain.length===0,FD_ledger:custody,request_observer_removed:removed,all_original_GETs_complete:reads.every(row=>row.complete&&!row.failure),secondary,first_exception:failed?String(primary):null,actual_success_assertions_completed:completed});
 }
 if(failed)throw primary;expect(removed).toBe(true);expect(completed).toBe(true);expect(proof).toBeTruthy();e.note('saved_queue_empty_controls',proof);
}
test('saved queue empty threshold margin origin and eligible training preserve exact saved controls',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await queueEmptyExercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native saved queue empty threshold margin origin and eligible training preserve exact originals',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const page=electronSession.window,backend=await electronSession.waitForBackend();await queueEmptyExercise(page,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});

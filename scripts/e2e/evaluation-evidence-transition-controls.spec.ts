import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
test.use({actionTimeout:10_000});

import handoffPath from 'node:path';
import {createHash as handoffCreateHash} from 'node:crypto';
import type {Request as HandoffRequest, Route as HandoffRoute} from '@playwright/test';
import {png as handoffPng} from './qa/appFlow';
type HandoffApi = (route:string,body?:unknown,method?:string)=>Promise<any>;
const handoffHash=(bytes:Buffer)=>handoffCreateHash('sha256').update(bytes).digest('hex');

async function handoffWithin<T>(promise:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s handoff frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;
 return Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s handoff frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
async function handoffProject(page:Page,scope:{project:any;source:string}){
 const deadline=performance.now()+10_000;
 await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click();
 const wire=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/project/open'&&r.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});
 const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
 await expect(item).toHaveCount(1);await expect(item).toBeEnabled();await item.click();
 const response=await handoffWithin(wire,deadline,'project open');expect(response.status()).toBe(200);const bytes=await response.body();
 expect(JSON.parse(bytes.toString('utf8'))).toEqual(scope.project);expect(await response.finished()).toBeNull();
 await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
 return {project_id:scope.project.id,project_dir:scope.project.project_dir,status:200,response_sha256:handoffHash(bytes)};
}

function rocHandoffRemaining(deadline:number){const left=deadline-performance.now();if(left<=0)throw Error('Original 10s ROC handoff frame expired');return Math.max(1,Math.floor(left));}
async function rocHandoffWithin<T>(work:Promise<T>,deadline:number,label:string):Promise<T>{let timer:NodeJS.Timeout|undefined;try{const value=await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s ROC handoff frame expired: '+label)),rocHandoffRemaining(deadline));})]);expect(performance.now()).toBeLessThanOrEqual(deadline);return value;}finally{clearTimeout(timer);}}
function rocHandoffTree(root:string,retainedFDcustody?:number[]){
 const result:Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>={};const walk=(dir:string)=>{const s=fs.lstatSync(dir);expect(s.isSymbolicLink()).toBe(false);expect(s.isDirectory()).toBe(true);expect(fs.realpathSync(dir)).toBe(dir);result[path.relative(root,dir).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(dir).sort()){const file=path.join(dir,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);if(before.isDirectory())walk(file);else{expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW),retained=retainedFDcustody!==undefined&&['model_deployments.sqlite3','model_deployments.sqlite3-wal','model_deployments.sqlite3-shm'].includes(path.relative(root,file));if(retained)retainedFDcustody!.push(fd);let raw:Buffer;try{const opened=fs.fstatSync(fd);expect([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]).toEqual([before.dev,before.ino,before.mode,before.nlink,before.size,before.mtimeMs,before.ctimeMs]);raw=fs.readFileSync(fd);for(const after of[fs.fstatSync(fd),fs.lstatSync(file)])expect([after.dev,after.ino,after.mode,after.nlink,after.size,after.mtimeMs,after.ctimeMs]).toEqual([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]);}finally{if(!retained)fs.closeSync(fd);}result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:sha(raw!)};}}
 };walk(root);return result;
}

type F056Scope={tag:'A'|'B';source:string;project:any;fixture:any;full:any};
async function f056SavedROCPending(page:Page,w:Workspace,e:Evidence,api:OwnedApi,native:boolean,origin:string,url?:string,threeActions=false){
 const frame=async<T>(label:string,work:(deadline:number)=>Promise<T>):Promise<T>=>{const deadline=performance.now()+10_000;return rocHandoffWithin(work(deadline),deadline,'F056 '+label);};
 const read=(route:string,body?:unknown,method?:string)=>frame('fixture API full body',()=>api(route,body,method));
 const {DatabaseSync}=await import('node:sqlite');
 const databases:Array<import('node:sqlite').DatabaseSync>=[],retainedFDs:number[]=[],controls:Array<{close:()=>Promise<void>}>=[];
 const writes:Array<{method:string;path:string;body:string|null}>=[];
 const observe=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:u.pathname,body:r.postData()});};
 let observing=false,failed=false,proof:any;
 const threeSnapshots:Record<string,unknown>={};
 try{
  const make=async(tag:'A'|'B'):Promise<F056Scope>=>{
   const source=path.join(w.root,'f056-pending-'+tag);fs.mkdirSync(source);
   for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),png(16,3,(x,y)=>[x,y,(tag==='A'?30:130)+i]),{flag:'wx'});
   const created=await read('/api/project/create',{name:'F056 pending controls '+tag,task:'segmentation'});
   await read('/api/project/update',{source_dataset_dir:source},'PUT');await read('/api/dataset/import',{folder_path:source,task:'segmentation'});
   const project=await read('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);
   const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
   expect(fixture.kind).toBe('controlled_saved_filter_reports_not_model_inference');expect(fixture.items).toHaveLength(3);
   const full=fixture.items.find((item:any)=>item.variant==='full');expect(full).toBeTruthy();expect(full.record.result.test_predictions).toHaveLength(6);
   expect(full.record.binding.threshold_settings).toEqual({probability_threshold:.5});expect(full.record.binding.source_dataset_path).toBe(source);
   for(const item of fixture.items)expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);
   for(const input of fixture.inputs)expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);
   for(const route of['/api/team-data','/api/team-data/readiness','/api/team-data/queue?offset=0&limit=30','/api/product-delivery/packages','/api/fleet/targets','/api/runtime-services','/api/runtime-services/capture-groups','/api/flow-evaluations/approvals/active','/api/model-deployments/active?source_dataset_path='+encodeURIComponent(source)+'&task=segmentation','/api/dataset/versions','/api/provenance/impact'])await read(route);
   const file=path.join(project.project_dir,'model_deployments.sqlite3'),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);expect(info.isFile()).toBe(true);expect(info.nlink).toBe(1);expect(fs.realpathSync(file)).toBe(file);
   const database=new DatabaseSync(file,{readOnly:true});databases.push(database);database.prepare('SELECT revisions.* FROM active_revisions JOIN revisions ON revisions.revision_id=active_revisions.revision_id').all();
   return{tag,source,project,fixture,full};
  };
  const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.full.record.evaluation_id).not.toBe(B.full.record.evaluation_id);
  await read('/api/project/open',{project_dir:A.project.project_dir});await frame('initial renderer',deadline=>url?page.goto(url,{timeout:rocHandoffRemaining(deadline)}):page.reload({timeout:rocHandoffRemaining(deadline)}));
  const trees=(scope:F056Scope)=>({source:rocHandoffTree(scope.source),project:rocHandoffTree(scope.project.project_dir,retainedFDs)}),before={A:trees(A),B:trees(B)};
  const summary=page.locator('summary').filter({hasText:/^평가 이력 · 제품\/Lot별 오류$/}),history=summary.locator('..'),family=history.getByLabel('평가 이력 모델 종류',{exact:true}),selector=history.getByLabel(/^모델별 저장 평가/);
  const bodyExact=(body:any,scope:F056Scope)=>{expect(body.items).toHaveLength(3);expect(body.total).toBe(3);expect(body.items.map((item:any)=>item.evaluation_id).sort()).toEqual(scope.fixture.items.map((item:any)=>item.record.evaluation_id).sort());for(const item of scope.fixture.items)expect(body.items.find((row:any)=>row.evaluation_id===item.record.evaluation_id)).toEqual(item.record);};
  const saved=(scope:F056Scope)=>frame('saved result and binding',async deadline=>{
   const result=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();if(await result.getAttribute('open')===null)await result.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   expect(JSON.parse(await result.locator('pre').innerText({timeout:rocHandoffRemaining(deadline)}))).toEqual(scope.full.record.result);
   const binding=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();if(await binding.getAttribute('open')===null)await binding.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   const value=JSON.parse(await binding.locator('pre').innerText({timeout:rocHandoffRemaining(deadline)}));expect(value).toEqual({evaluation_id:scope.full.record.evaluation_id,evidence_sha256:scope.full.record.evidence_sha256,binding:scope.full.record.binding});await expect(history.getByText('평가 임계값:',{exact:false})).toContainText('"probability_threshold":0.5',{timeout:rocHandoffRemaining(deadline)});return value;
  });
  const refresh=(scope:F056Scope)=>frame('original owning history refresh',async deadline=>{
   const generation=new Set<import('@playwright/test').Request>(),started=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(r.frame()===page.mainFrame()&&r.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='detection'&&!u.searchParams.has('labelset_id'))generation.add(r);};page.on('request',started);
   try{const wire=page.waitForResponse(r=>generation.has(r.request()),{timeout:rocHandoffRemaining(deadline)});await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)});const response=await rocHandoffWithin(wire,deadline,'F056 original history response');expect(response.status()).toBe(200);const raw=await rocHandoffWithin(response.body(),deadline,'F056 original history full body');expect(await rocHandoffWithin(response.finished(),deadline,'F056 original history completion')).toBeNull();const body=JSON.parse(raw.toString('utf8'));bodyExact(body,scope);await expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});return{status:200,body,sha256:sha(raw),main_frame:true,source:scope.source};}finally{page.off('request',started);}
  });
  const enter=(scope:F056Scope)=>frame('enter current saved ROC',async deadline=>{
   await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:rocHandoffRemaining(deadline)});await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click({timeout:rocHandoffRemaining(deadline)});if(await history.getAttribute('open')===null)await summary.click({timeout:rocHandoffRemaining(deadline)});await family.selectOption('detection',{timeout:rocHandoffRemaining(deadline)});await expect(selector.locator('option[value="'+scope.full.record.evaluation_id+'"]')).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});await selector.selectOption(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
   const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await detail.getAttribute('open')===null)await detail.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5',{timeout:rocHandoffRemaining(deadline)});return detail;
  });
  const review=(detail:ReturnType<Page['locator']>,value:string,counts:string)=>frame('transient review change',async deadline=>{const slider=detail.getByLabel('ROC 검토 임계값',{exact:true});await slider.fill(value,{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveValue(value,{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText('AUC 0.8333',{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText(counts,{timeout:rocHandoffRemaining(deadline)});return{value,counts,model_or_flow_threshold_saved:false};});
  // Three dependent saved-display families share the owning history admission.
  // These assertions use only rendered options, count buttons and score bins.
  const threeNames=(d:ReturnType<Page['locator']>)=>d.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents();
  const allThreeNames=['case-0.png · FN 0 / FP 0','case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1','case-3.png · FN 0 / FP 0','case-4.png · FN 0 / FP 0','case-5.png · FN 0 / FP 1'];
  const threeStats=(scope:F056Scope,d:ReturnType<Page['locator']>,index:number,allClassBins:boolean)=>frame('three families exact current report',async deadline=>{
   await expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
   const scratch=d.getByRole('row').filter({has:page.getByRole('cell',{name:'Scratch',exact:true})}),crack=d.getByRole('row').filter({has:page.getByRole('cell',{name:'Crack',exact:true})});
   await expect(scratch).toHaveText('Scratch112',{timeout:rocHandoffRemaining(deadline)});await expect(crack).toHaveText('Crack100',{timeout:rocHandoffRemaining(deadline)});
   const classes=d.getByLabel('평가 증거 클래스',{exact:true}),errors=d.getByLabel('평가 증거 오류',{exact:true}),bins=d.getByRole('button',{name:/^점수 /});
   expect(await classes.locator('option').evaluateAll(options=>options.map(option=>(option as HTMLOptionElement).value).sort())).toEqual(['Crack','Scratch','all']);
   expect(await errors.locator('option').evaluateAll(options=>options.map(option=>(option as HTMLOptionElement).value))).toEqual(['all','fn','fp','incorrect']);
   await expect(bins).toHaveCount(10,{timeout:rocHandoffRemaining(deadline)});
   if(allClassBins){await expect(classes).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});const expected=[0,1,0,1,0,0,1,1,1,1];for(let i=0;i<10;i++)await expect(bins.nth(i)).toHaveAttribute('aria-label','점수 '+(i/10).toFixed(1)+'에서 '+((i+1)/10).toFixed(1)+' '+expected[i]+'개',{timeout:rocHandoffRemaining(deadline)});}
   const image=d.getByLabel('정답과 예측 객체 위치',{exact:true}).locator('image'),expectedPath=path.join(scope.source,'case-'+index+'.png');
   await expect(image).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});const href=await image.getAttribute('href');expect(href).not.toBeNull();const uri=new URL(href!,origin);expect(uri.origin).toBe(origin);expect(uri.pathname).toBe('/api/dataset/raw/case-'+index+'.png');expect(uri.searchParams.get('file_path')).toBe(expectedPath);
   return{project_id:scope.project.id,evaluation_id:scope.full.record.evaluation_id,source:scope.source,scratch:{tp:1,fn:1,fp:2},crack:{tp:1,fn:0,fp:0},bins:await bins.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label'))),selected_source_path:expectedPath};
  });
  const threeReset=(d:ReturnType<Page['locator']>)=>frame('three families reset after current read',async deadline=>{
   await expect(d.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByText(/^선택 점수 /)).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(allThreeNames);return{class_value:'all',error_value:'all',score_range:null,filenames:allThreeNames};
  });
  const threeActive=(scope:F056Scope,d:ReturnType<Page['locator']>)=>frame('three real class and score controls',async deadline=>{
   const scratch=d.getByRole('row').filter({has:page.getByRole('cell',{name:'Scratch',exact:true})}),classes=d.getByLabel('평가 증거 클래스',{exact:true}),errors=d.getByLabel('평가 증거 오류',{exact:true});
   await scratch.getByRole('button',{name:'1',exact:true}).click({timeout:rocHandoffRemaining(deadline)});await expect(classes).toHaveValue('Scratch',{timeout:rocHandoffRemaining(deadline)});await expect(errors).toHaveValue('fn',{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(['case-1.png · FN 1 / FP 0']);
   await scratch.getByRole('button',{name:'2',exact:true}).click({timeout:rocHandoffRemaining(deadline)});await expect(errors).toHaveValue('fp',{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(['case-2.png · FN 0 / FP 1','case-5.png · FN 0 / FP 1']);
   if(scope.tag==='A'){await scratch.getByRole('button',{name:'1',exact:true}).click({timeout:rocHandoffRemaining(deadline)});await d.getByRole('button',{name:'점수 0.3에서 0.4 1개',exact:true}).click({timeout:rocHandoffRemaining(deadline)});await expect(errors).toHaveValue('fn',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByText('선택 점수 0.3–0.4',{exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(['case-1.png · FN 1 / FP 0']);}
   else{await d.getByRole('button',{name:'점수 0.7에서 0.8 1개',exact:true}).click({timeout:rocHandoffRemaining(deadline)});await expect(d.getByText('선택 점수 0.7–0.8',{exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(['case-2.png · FN 0 / FP 1']);}
   return{project_id:scope.project.id,evaluation_id:scope.full.record.evaluation_id,class_value:'Scratch',error_value:scope.tag==='A'?'fn':'fp',score_range:scope.tag==='A'?[.3,.4]:[.7,.8],filenames:await threeNames(d)};
  });
  const threeUnavailable=()=>frame('current read removes all three stale families',async deadline=>{
   await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByRole('columnheader',{name:'클래스/문자',exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByLabel('평가 증거 클래스',{exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByLabel('평가 증거 오류',{exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByRole('button',{name:/^점수 /})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});return{saved_class_table_count:0,class_control_count:0,error_control_count:0,score_bin_count:0};
  });
  const threeRetained=(d:ReturnType<Page['locator']>)=>frame('late A cannot change B class and score selection',async deadline=>{
   await expect(d.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('Scratch',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('fp',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByText('선택 점수 0.7–0.8',{exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});expect(await threeNames(d)).toEqual(['case-2.png · FN 0 / FP 1']);return{class_value:'Scratch',error_value:'fp',score_range:[.7,.8],filenames:['case-2.png · FN 0 / FP 1']};
  });
  // The control is a local native range. The invalid attempt is a real keyboard
  // step beyond its boundary, not Playwright fill rejection or injected state.
  let detail=await enter(A);const initialSaved=await saved(A);const originalRead=await refresh(A);detail=await enter(A);
  if(threeActions){threeSnapshots.initial_A=await threeStats(A,detail,0,true);threeSnapshots.active_A=await threeActive(A,detail);}
  page.on('request',observe);observing=true;
  const invalid=threeActions?null:await frame('range boundary keyboard attempts',async deadline=>{
   const slider=detail.getByLabel('ROC 검토 임계값',{exact:true});await expect(slider).toHaveAttribute('type','range',{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveAttribute('min','0',{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveAttribute('max','1',{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveAttribute('step','.01',{timeout:rocHandoffRemaining(deadline)});
   const lower={counts:'TP 3 · 미검 FN 0 · 과검 FP 2 · TN 0.'};await slider.fill('0',{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText(lower.counts,{timeout:rocHandoffRemaining(deadline)});await slider.press('ArrowLeft',{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveValue('0',{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText(lower.counts,{timeout:rocHandoffRemaining(deadline)});
   const upper={counts:'TP 0 · 미검 FN 3 · 과검 FP 0 · TN 2.'};await slider.fill('1',{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText(upper.counts,{timeout:rocHandoffRemaining(deadline)});await slider.press('ArrowRight',{timeout:rocHandoffRemaining(deadline)});await expect(slider).toHaveValue('1',{timeout:rocHandoffRemaining(deadline)});await expect(detail).toContainText(upper.counts,{timeout:rocHandoffRemaining(deadline)});
   return{attempts:[{at:0,key:'ArrowLeft',after:0,counts:lower.counts},{at:1,key:'ArrowRight',after:1,counts:upper.counts}],browser_range_prevents_out_of_bounds:true,no_fabricated_value_or_product_validation_error:true};
  });
  await review(detail,'0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.');expect(await saved(A)).toEqual(initialSaved);await e.screenshot(page,`${native?'source-electron':'browser'}-f056-invalid-range-boundaries-saved-05`);
  const controlledMessage='F056 controlled saved-history outage';
  const installControl=async(scope:F056Scope,kind:'error'|'late')=>{
   const pattern=origin+'/api/evaluation/history**';let armed=true,own:import('@playwright/test').Request|undefined,captured:any,primary:unknown,primarySet=false,fulfillError:unknown,fulfillFailed=false,installed=false,closeAttempted=false,deadline=0,release!:()=>void,ready!:()=>void,done!:()=>void;
   const gate=new Promise<void>(r=>release=r),readyPromise=new Promise<void>(r=>ready=r),donePromise=new Promise<void>(r=>done=r);let dispose!:(row:{kind:'finished'|'failed';failure:string|null})=>void;const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>dispose=r);
   const ended=(r:import('@playwright/test').Request)=>{if(r===own)dispose({kind:'finished',failure:null});},aborted=(r:import('@playwright/test').Request)=>{if(r===own)dispose({kind:'failed',failure:r.failure()?.errorText??null});};page.on('requestfinished',ended);page.on('requestfailed',aborted);
   const handler=async(route:import('@playwright/test').Route)=>{
    const request=route.request(),u=new URL(request.url());if(!armed||request.frame()!==page.mainFrame()||request.method()!=='GET'||u.origin!==origin||u.pathname!=='/api/evaluation/history'||u.searchParams.get('source_dataset_path')!==scope.source||u.searchParams.get('task')!=='detection'||u.searchParams.has('labelset_id')||u.searchParams.has('_f056_control_readback')){await route.continue();return;}
    armed=false;own=request;deadline=performance.now()+10_000;
    try{
     let status:number,raw:Buffer,contentType:string|null;
     if(native){
      // Electron uses a separately authenticated renderer GET. It is explicitly
      // a controlled reply source, never original native UI HTTP200 provenance.
      const headers:Record<string,string>={};for(const name of['content-type','x-vision-project','x-vision-context']){const value=await request.headerValue(name);if(value!==null)headers[name]=value;}
      const delegate=new URL(request.url());delegate.searchParams.set('_f056_control_readback',crypto.randomUUID());
      const snapshot=await page.evaluate(async({url,headers,remaining})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);try{const response=await fetch(url,{method:'GET',headers,signal:abort.signal});const bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>1024*1024)throw Error('F056 controlled body exceeds1MiB');let value='';for(const byte of bytes)value+=String.fromCharCode(byte);return{status:response.status,contentType:response.headers.get('content-type'),base64:btoa(value)};}finally{clearTimeout(timer);}},{url:delegate.toString(),headers,remaining:rocHandoffRemaining(deadline)});
      status=snapshot.status;raw=Buffer.from(snapshot.base64,'base64');contentType=snapshot.contentType;
     }else{const snapshot=await route.fetch({timeout:rocHandoffRemaining(deadline)});status=snapshot.status();raw=await rocHandoffWithin(snapshot.body(),deadline,'F056 captured body');contentType=snapshot.headers()['content-type']??null;}
     expect(status).toBe(200);expect(raw.length).toBeLessThanOrEqual(1024*1024);const body=JSON.parse(raw.toString('utf8'));bodyExact(body,scope);captured={body,sha256:sha(raw),size:raw.length,status,source:scope.source,main_frame:true,transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response'};ready();
     if(kind==='late')await rocHandoffWithin(gate,deadline,'F056 release canceled-scope read');
     try{await route.fulfill(kind==='error'?{status:503,contentType:'application/json',body:JSON.stringify({detail:controlledMessage})}:{status:200,body:raw,headers:contentType?{'content-type':contentType}:{}});}catch(error){fulfillError=error;fulfillFailed=true;}
    }catch(error){primary=error;primarySet=true;ready();}finally{done();}
   };
   const readyRead=async()=>{await rocHandoffWithin(readyPromise,performance.now()+10_000,'F056 captured owning request');if(primarySet)throw primary;expect(captured).toBeTruthy();return captured;};
   const finish=async()=>{release();await rocHandoffWithin(donePromise,deadline,'F056 controlled handler completion');if(primarySet)throw primary;const result=await rocHandoffWithin(disposition,deadline,'F056 original request disposition');if(result.kind==='failed'){expect(kind).toBe('late');expect(result.failure).toContain('ERR_ABORTED');}else{if(fulfillFailed)throw fulfillError;const response=await rocHandoffWithin(own!.response(),deadline,'F056 controlled original response');expect(response).not.toBeNull();expect(response!.status()).toBe(kind==='error'?503:200);const raw=await rocHandoffWithin(response!.body(),deadline,'F056 controlled delivered body');expect(await rocHandoffWithin(response!.finished(),deadline,'F056 controlled HTTP completion')).toBeNull();if(kind==='error')expect(JSON.parse(raw.toString('utf8'))).toEqual({detail:controlledMessage});else expect(sha(raw)).toBe(captured.sha256);}
    await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));return{kind,disposition:result,captured,controlled_503:kind==='error',backend_503_claim:false,original_native_HTTP200_provenance:false,server_cancel_request_sent:false};};
   const close=async()=>{if(closeAttempted)return;closeAttempted=true;release();let first:unknown,set=false;try{if(own)await rocHandoffWithin(donePromise,performance.now()+10_000,'F056 owned route final join');}catch(error){first=error;set=true;}try{if(installed&&!page.isClosed())await page.unroute(pattern,handler);}catch(error){if(!set){first=error;set=true;}}finally{page.off('requestfinished',ended);page.off('requestfailed',aborted);}if(set)throw first;};
   const result={ready:readyRead,finish,close};controls.push(result);await page.route(pattern,handler);installed=true;return result;
  };
  const errorControl=await installControl(A,'error');await frame('actual refresh under controlled outage',deadline=>history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)}));await errorControl.ready();const error=await errorControl.finish();
  await frame('current failed history removes stale ROC',async deadline=>{await expect(history.getByRole('alert')).toHaveText(controlledMessage,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByLabel('ROC 검토 임계값',{exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(selector).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});});if(threeActions)threeSnapshots.error_unavailable=await threeUnavailable();await e.screenshot(page,`${native?'source-electron':'browser'}-f056-controlled-history-error-no-stale-ROC`);await errorControl.close();
  const recovered=await refresh(A);detail=await enter(A);expect(await saved(A)).toEqual(initialSaved);await review(detail,'0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.');
  if(threeActions){threeSnapshots.recovery_reset=await threeReset(detail);threeSnapshots.recovered_A=await threeStats(A,detail,0,true);threeSnapshots.recovery_active_A=await threeActive(A,detail);}
  const late=await installControl(A,'late');await frame('start original read before abandoning A',deadline=>history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)}));const canceledRead=await late.ready();
  await frame('pending read has no editable stale ROC',async deadline=>{await expect(history.getByRole('status')).toContainText('평가 기록 확인 중',{timeout:rocHandoffRemaining(deadline)});await expect(history.getByLabel('ROC 검토 임계값',{exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});});
  if(threeActions)threeSnapshots.pending_unavailable=await threeUnavailable();
  const toB=await handoffProject(page,B);detail=await enter(B);const B_saved=await saved(B),B_review=await review(detail,'0.25','TP 3 · 미검 FN 0 · 과검 FP 1 · TN 1.');if(threeActions){threeSnapshots.B_reset=await threeReset(detail);threeSnapshots.B_report=await threeStats(B,detail,0,true);threeSnapshots.B_active=await threeActive(B,detail);threeSnapshots.B_selected_before=await threeStats(B,detail,2,false);}const cancel=await late.finish();await late.close();
  await frame('old A response cannot repopulate B ROC',async deadline=>{await expect(selector).toHaveValue(B.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});await expect(selector.locator('option[value="'+A.full.record.evaluation_id+'"]')).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByRole('alert')).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.25',{timeout:rocHandoffRemaining(deadline)});});expect(await saved(B)).toEqual(B_saved);if(threeActions){threeSnapshots.B_retained=await threeRetained(detail);threeSnapshots.B_selected_after=await threeStats(B,detail,2,false);expect(threeSnapshots.B_selected_after).toEqual(threeSnapshots.B_selected_before);}await e.screenshot(page,`${native?'source-electron':'browser'}-f056-canceled-A-read-does-not-overwrite-B`);
  const toA=await handoffProject(page,A);detail=await enter(A);expect(await saved(A)).toEqual(initialSaved);const returnRead=await refresh(A);detail=await enter(A);await expect(detail).toContainText('TP 2 · 미검 FN 1 · 과검 FP 1 · TN 1.');await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(selector.locator('option[value="'+B.full.record.evaluation_id+'"]')).toHaveCount(0);const returnReview=await review(detail,'0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.');expect(await saved(A)).toEqual(initialSaved);
  if(threeActions){threeSnapshots.return_A_reset=await threeReset(detail);threeSnapshots.return_A=await threeStats(A,detail,0,true);expect(threeSnapshots.return_A).toEqual(threeSnapshots.initial_A);}
  expect(writes.map(r=>r.method+' '+r.path)).toEqual(['POST /api/project/open','POST /api/project/open']);expect(writes.map(r=>JSON.parse(r.body!))).toEqual([{project_dir:B.project.project_dir},{project_dir:A.project.project_dir}]);
  const after={A:trees(A),B:trees(B)};expect(after).toEqual(before);for(const scope of[A,B]){for(const item of scope.fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);e.addFile(item.report_path);}for(const input of scope.fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);e.addFile(input.path);}}
  await e.screenshot(page,`${native?'source-electron':'browser'}-f056-A-return-transient-only`);
  proof={schema:'modu-vision.f056-saved-ROC-pending-controls/v1',action:'F056.saved-roc-threshold',dimensions:['invalid','error','cancel'],mode:native?'source-electron':'browser',fixtures:{A,B},invalid,error,cancel:{...cancel,original_captured_A_read:canceledRead,meaning:'navigate to B during pending local saved-ROC read; revoke A display admission, not cancel a server job'},toB,toA,B_review,returnReview,return_A_review_reset:.5,saved_A:initialSaved,saved_B:B_saved,saved_threshold_settings:{A:initialSaved.binding.threshold_settings,B:B_saved.binding.threshold_settings},original_owning_history_reads:[originalRead,recovered,returnRead],original_raw_trees:before,final_raw_trees:after,business_writes:writes,model_or_flow_threshold_saved:false,controlled_saved_reports_not_model_inference:true,actual_model_inference:false,Native_accepted:false,parent_completion_credit:false,quality_human_installed_target_approval:false};
  if(threeActions){
   delete proof.invalid;proof.schema='modu-vision.f049-f052-f054-history-error-cancel/v1';proof.action='three_saved_evaluation_display_families';proof.actions=['F049.saved-class-metrics','F052.class-fn-fp-filter','F054.score-bin-filter'];proof.dimensions=['error','cancel'];proof.family_observations=threeSnapshots;
   proof.invalid_cells_claimed=false;proof.error_scope='current dependent owning-history controlled503 removes all three saved display families and permits exact recovery';proof.cancel_scope='abandon A display admission during pending owning-history read; B exact table filters bins and source URI survive A disposition; no server-job cancel';
  }
 }catch(error){failed=true;throw error;}finally{
  if(observing)page.off('request',observe);let first:unknown,closeFailed=false,count=0;
  const note=(error:unknown)=>{count++;if(!closeFailed){first=error;closeFailed=true;}};
  for(const control of controls.reverse()){try{await control.close();}catch(error){note(error);}}
  for(const database of databases.reverse()){try{database.close();}catch(error){note(error);}}
  for(const fd of retainedFDs.reverse()){try{fs.closeSync(fd);}catch(error){note(error);}}
  if(closeFailed){try{e.note('f056_pending_secondary_cleanup',{failures:count,type:typeof first});}catch{}if(!failed)throw first;}
 }
 proof.resource_closure={owned_route_handlers_joined:true,request_observers_removed:true,readonly_sqlite_observers_closed:true,retained_raw_FDs_closed:true};const output=path.join(w.logs,threeActions?'evaluation-three-actions-error-cancel-proof.json':'f056-saved-roc-pending-proof.json');fs.writeFileSync(output,JSON.stringify(proof),{flag:'wx'});e.addFile(output);e.note(threeActions?'evaluation_three_actions_error_cancel':'f056_saved_roc_pending',proof);
}

// Source-only six-cell extension; Root authenticates and runs these exact selectors.
test('three saved evaluation families remove stale controls on failed history and fence canceled A read',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),timeout:10_000,...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await f056SavedROCPending(page,workspace,evidence,api,false,renderer.origin,renderer.url,true);
});
test('native three saved evaluation families retain B class filters and score bins after canceled A history',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;const api:OwnedApi=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!response.ok)throw Error('Owned F056 fixture HTTP '+response.status);return response.json();},{origin,route,body,method});await f056SavedROCPending(page,workspace,evidence,api,true,origin,undefined,true);
});

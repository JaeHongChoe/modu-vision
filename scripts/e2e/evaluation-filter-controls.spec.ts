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

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'filter-source');fs.mkdirSync(source);
 for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),png(16,3,(x,y)=>[x,y,30+i]));
 const project=await api('/api/project/create',{name:'Saved evaluation filter controls',task:'segmentation'});
 // Import image sources only; controlled detection reports do not claim a
 // trainable detection dataset or invoke a detector/annotation importer.
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),workspace.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const writes:string[]=[];page.on('request',request=>{if(request.method()!=='GET'&&/\/(evaluation|train|jobs)(\/|$)/.test(new URL(request.url()).pathname))writes.push(`${request.method()} ${new URL(request.url()).pathname}`);});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await page.getByLabel('평가 이력 모델 종류',{exact:true}).selectOption('detection');};
 await navigate();const history=page.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'})}).first(),selector=history.getByLabel(/^모델별 저장 평가/);
 const full=fixture.items.find((r:any)=>r.variant==='full'),unknown=fixture.items.find((r:any)=>r.variant==='no-binary-truth'),empty=fixture.items.find((r:any)=>r.variant==='empty');
 const select=async(item:any)=>{await selector.selectOption(item.record.evaluation_id);const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(item!==empty&&await detail.getAttribute('open')===null)await detail.locator('summary').first().click();return detail;};
 let detail=await select(full);const classControl=detail.getByLabel('평가 증거 클래스',{exact:true}),errorControl=detail.getByLabel('평가 증거 오류',{exact:true});
 const results=()=>detail.getByRole('button',{name:/^case-\d\.png · FN/});
 const names=async()=>results().allTextContents();
 const classRow=(name:string)=>detail.getByRole('row').filter({has:page.getByRole('cell',{name,exact:true})});
 const observations:any[]=[];
 expect(await names()).toEqual(['case-0.png · FN 0 / FP 0','case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1','case-3.png · FN 0 / FP 0','case-4.png · FN 0 / FP 0','case-5.png · FN 0 / FP 1']);
 await expect(classRow('Scratch')).toHaveText('Scratch112');await expect(classRow('Crack')).toHaveText('Crack100');
 await classRow('Scratch').getByRole('button',{name:'1',exact:true}).click();await expect(classControl).toHaveValue('Scratch');await expect(errorControl).toHaveValue('fn');expect(await names()).toEqual(['case-1.png · FN 1 / FP 0']);
 await classRow('Scratch').getByRole('button',{name:'2',exact:true}).click();await expect(errorControl).toHaveValue('fp');expect(await names()).toEqual(['case-2.png · FN 0 / FP 1','case-5.png · FN 0 / FP 1']);observations.push({control:'class-error-cells',fn:['case-1.png'],fp:['case-2.png','case-5.png']});
 await classRow('Crack').getByRole('button',{name:'0',exact:true}).first().click();await expect(results()).toHaveCount(0);await expect(detail).toContainText('선택 조건에 해당하는 결과가 없습니다.');await expect(detail.getByLabel('정답과 예측 객체 위치',{exact:true})).toHaveCount(0);
 await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();await classControl.selectOption('all');await errorControl.selectOption('incorrect');expect(await names()).toEqual(['case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1']);await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();
 const histogram=detail.getByRole('button',{name:/^점수 /});expect(await histogram.count()).toBe(10);expect(await histogram.first().getAttribute('aria-label')).toBe('점수 0.0에서 0.1 0개');
 const expectedBins=[0,1,0,1,0,0,1,1,1,1];for(let i=0;i<10;i++)await expect(histogram.nth(i)).toHaveAttribute('aria-label',`점수 ${(i/10).toFixed(1)}에서 ${((i+1)/10).toFixed(1)} ${expectedBins[i]}개`);
 await detail.getByRole('button',{name:'점수 0.7에서 0.8 1개',exact:true}).click();expect(await names()).toEqual(['case-2.png · FN 0 / FP 1']);
 await detail.getByRole('button',{name:'점수 0.2에서 0.3 0개',exact:true}).click();await expect(results()).toHaveCount(0);await expect(detail).toContainText('선택 조건에 해당하는 결과가 없습니다.');await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();await expect(results()).toHaveCount(6);observations.push({control:'score-bins',counts:expectedBins,empty_range:[.2,.3],half_open_upper_boundary:true});
 const roc=detail.getByLabel('ROC 곡선',{exact:true}),slider=detail.getByLabel('ROC 검토 임계값',{exact:true});await expect(roc).toHaveCount(1);await expect(detail).toContainText('AUC 0.8333 · TP 2 · 미검 FN 1 · 과검 FP 1 · TN 1.');
 for(const [value,text]of[['0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.'],['0','TP 3 · 미검 FN 0 · 과검 FP 2 · TN 0.'],['1','TP 0 · 미검 FN 3 · 과검 FP 0 · TN 2.']]){await slider.fill(value);await expect(detail).toContainText(text);observations.push({control:'roc-threshold',value,counts:text});}
 await slider.fill('0.5');const thresholds=history.getByText('평가 임계값:',{exact:false});await expect(thresholds).toContainText('"probability_threshold":0.5');
 const sizeSummary=detail.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'});await sizeSummary.click();const areas=detail.getByRole('button',{name:/^결함 면적 /});await expect(areas).toHaveCount(10);
 // Independent areas: (2+i)^2 for predicted indices0,2,4,5 ->4,16,36,49.
 const expectedAreas=[1,0,0,1,0,0,0,1,0,1];for(let i=0;i<10;i++)await expect(areas.nth(i)).toHaveAttribute('aria-label',`결함 면적 ${(i*4.9).toFixed(1)}에서 ${((i+1)*4.9).toFixed(1)} ${expectedAreas[i]}개`);
 await areas.nth(3).click();expect(await names()).toEqual(['case-2.png · FN 0 / FP 1']);await areas.nth(1).click();await expect(results()).toHaveCount(0);await detail.getByRole('button',{name:'면적 필터 해제',exact:true}).click();await expect(results()).toHaveCount(6);observations.push({control:'area-bins',predicted_areas:[4,16,36,49],counts:expectedAreas});
 await detail.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-saved-filters-and-roc`);
 detail=await select(unknown);await expect(detail.getByLabel('ROC 곡선',{exact:true})).toHaveCount(0);await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveCount(0);await expect(detail).toContainText('실제 정상·불량 정답과 결함 점수가 함께 있어야 ROC를 계산합니다.');
 await select(empty);await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0);
 await select(full);await history.getByLabel('평가 오류 집계 기준',{exact:true}).selectOption('lot');await navigate();await expect(selector).toHaveValue(full.record.evaluation_id);await expect(history.getByLabel('평가 오류 집계 기준',{exact:true})).toHaveValue('lot');detail=await select(full);await expect(detail.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(results()).toHaveCount(6);
 const saved=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();await saved.locator('summary').click();expect(JSON.parse(await saved.locator('pre').innerText())).toEqual(full.record.result);
 for(const item of fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);const readback=await api(`/api/evaluation/history/${item.record.evaluation_id}?source_dataset_path=${encodeURIComponent(source)}&task=detection`);expect(readback).toEqual(item.record);evidence.addFile(item.report_path);}
 for(const input of fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);evidence.addFile(input.path);}expect(writes).toEqual([]);
 await detail.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-same-evaluation-reopened`);
 evidence.note('saved_filter_controls',{fixture,project_id:project.id,observations,unknown_truth_not_binary:true,empty_saved_report:true,same_record_and_group_reopened:true,ephemeral_filters_reset:true,saved_result_json_exact:true,source_reports_and_images_unchanged:true,evaluation_training_writes:writes,controlled_reports_not_model_inference:true});
}

test('saved evaluation filters preserve exact class errors, score and area bins and ROC arithmetic',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved evaluation filters preserve exact class errors, score and area bins and ROC arithmetic',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned filter fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

// SOURCE-only extension: actual execution and registry credit remain Root-owned.
import handoffPath from 'node:path';
import {createHash as handoffCreateHash} from 'node:crypto';
import type {Request as HandoffRequest, Route as HandoffRoute} from '@playwright/test';
import {png as handoffPng} from './qa/appFlow';
type HandoffApi = (route:string,body?:unknown,method?:string)=>Promise<any>;
const handoffHash=(bytes:Buffer)=>handoffCreateHash('sha256').update(bytes).digest('hex');
const handoffTree=(root:string):Record<string,{sha256:string;size:number}>=>{
 const rows:Record<string,{sha256:string;size:number}>={};
 const walk=(dir:string)=>{for(const entry of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){
  const file=handoffPath.join(dir,entry.name),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
  if(info.isDirectory())walk(file);else{expect(info.isFile()).toBe(true);const bytes=fs.readFileSync(file);rows[handoffPath.relative(root,file).split(handoffPath.sep).join('/') ]={sha256:handoffHash(bytes),size:bytes.length};}
 }};walk(root);return rows;
};
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
async function handoffLabels(w:Workspace,api:HandoffApi,family:string,tag:'A'|'B'){
 const source=handoffPath.join(w.root,family+'-handoff-'+tag);fs.mkdirSync(source);const image=handoffPath.join(source,'part.png');
 fs.writeFileSync(image,handoffPng(256,3,(x,y)=>[x,y,tag==='A'?103:201]));
 const project=await api('/api/project/create',{name:family+' handoff '+tag,task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Handoff '+tag,categories:[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]});
 const saved=await api('/api/annotations/save',{image_id:'part',image_path:image,image_width:256,image_height:256,actor:'handoff-labeler-'+tag,
  annotations:[{id:'handoff-original-'+tag,type:'bbox',label:'Scratch',category_id:2,bbox:[2,3,20,21]}]});
 await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/team-data/queue?offset=0&limit=30');
 const activeProject=await api('/api/project/current');expect(activeProject.id).toBe(project.id);
 return {tag,project:activeProject,source,image,uuid:saved.metadata.image_uuid,query:'/api/annotations/part?file_path='+encodeURIComponent(image),imageRoute:'/api/team-data/images/'+saved.metadata.image_uuid};
}
async function handoffEnterLabels(page:Page,scope:{image:string;tag:string},team:boolean){
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 if(team){await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
  await dialog.getByRole('region',{name:'팀 작업 목록',exact:true}).getByRole('button',{name:'part.png',exact:true}).click();
  await expect(dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true})).toContainText('handoff-labeler-'+scope.tag);return dialog;
 }
 await expect(page.getByRole('region',{name:'라벨 연결 복구',exact:true})).toBeVisible();return null;
}
async function handoffLateRead(page:Page,origin:string,native:boolean,match:(u:URL)=>boolean){
 const pattern=origin+'/api/**';let release!:()=>void,reached!:()=>void,finished!:()=>void;
 const gate=new Promise<void>(r=>release=r),ready=new Promise<void>(r=>reached=r),done=new Promise<void>(r=>finished=r);
 let captured:{status:number;bytes:Buffer;sha256:string;body:any}|undefined,primary:unknown,routeError:unknown,request:HandoffRequest|undefined,deadline=0,armed=true;
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
   captured={status,bytes,sha256:handoffHash(bytes),body:JSON.parse(bytes.toString('utf8'))};reached();
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
   return {request_disposition:row,captured_sha256:captured!.sha256,captured_bytes:captured!.bytes.length,snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',original_UI_response_delivered:row.kind==='finished',native_original_HTTP200_provenance:false};},
  close:async()=>{release();if(!page.isClosed())await page.unroute(pattern,handler);page.off('requestfinished',ended);page.off('requestfailed',failed);},
 };
}
async function savedFilterProjectHandoff(page:Page,w:Workspace,e:Evidence,api:HandoffApi,native:boolean,origin:string,url?:string){
 const make=async(tag:'A'|'B')=>{
  const source=path.join(w.root,'saved-filter-handoff-'+tag);fs.mkdirSync(source);for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),handoffPng(16,3,(x,y)=>[x,y,(tag==='A'?30:130)+i]));
  await api('/api/project/create',{name:'Saved filter handoff '+tag,task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
  const project=await api('/api/project/current');
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
  await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/team-data/queue?offset=0&limit=30');
  const selected=fixture.items.find((r:any)=>r.variant==='full');expect(selected).toBeTruthy();expect(selected.record.binding.source_dataset_path).toBe(source);
  return {tag,source,project,fixture,selected};
 };
 const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.selected.record.evaluation_id).not.toBe(B.selected.record.evaluation_id);
 const trees={A:{source:handoffTree(A.source),reports:handoffTree(path.join(A.project.project_dir,'reports','evaluations')),annotations:handoffTree(A.project.annotations_dir)},B:{source:handoffTree(B.source),reports:handoffTree(path.join(B.project.project_dir,'reports','evaluations')),annotations:handoffTree(B.project.annotations_dir)}};
 await api('/api/project/open',{project_dir:A.project.project_dir});if(url)await page.goto(url);else await page.reload();
 const summary=page.locator('summary').filter({hasText:/^평가 이력 · 제품\/Lot별 오류$/}),history=summary.locator('..'),family=history.getByLabel('평가 이력 모델 종류',{exact:true}),selector=history.getByLabel(/^모델별 저장 평가/);
 const group=history.getByLabel('평가 오류 집계 기준',{exact:true});
 const enter=async(scope:typeof A)=>{await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();if(await history.getAttribute('open')===null)await summary.click();await family.selectOption('detection');await expect(selector.locator('option[value="'+scope.selected.record.evaluation_id+'"]')).toHaveCount(1);await selector.selectOption(scope.selected.record.evaluation_id);
  const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await detail.getAttribute('open')===null)await detail.locator('summary').first().click();return detail;};
 const resultJSON=async()=>{const saved=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();if(await saved.getAttribute('open')===null)await saved.locator('summary').first().click();return JSON.parse(await saved.locator('pre').innerText());};
 let detail=await enter(A);await group.selectOption('lot');await detail.getByLabel('평가 증거 클래스',{exact:true}).selectOption('Scratch');await detail.getByLabel('평가 증거 오류',{exact:true}).selectOption('fp');
 expect(await detail.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual(['case-2.png · FN 0 / FP 1','case-5.png · FN 0 / FP 1']);
 await detail.getByRole('button',{name:'점수 0.7에서 0.8 1개',exact:true}).click();expect(await detail.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual(['case-2.png · FN 0 / FP 1']);
 const areaSummary=detail.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'});if(await areaSummary.locator('..').getAttribute('open')===null)await areaSummary.click();await detail.getByRole('button',{name:/^결함 면적 /}).nth(3).click();
 expect(await resultJSON()).toEqual(A.selected.record.result);
 const writes:Array<{method:string;path:string}>=[];const observe=(r:HandoffRequest)=>{const p=new URL(r.url()).pathname;if(r.method()!=='GET'&&/^\/api\/(evaluation|annotations|team-data|training|train|jobs|dataset\/metadata)(\/|$)/.test(p))writes.push({method:r.method(),path:p});};page.on('request',observe);
 await family.selectOption('segmentation');const late=await handoffLateRead(page,origin,native,u=>u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===A.source&&u.searchParams.get('task')==='detection');let primary:unknown;
 try{
  await family.selectOption('detection');const captured=await late.ready();expect(captured.body.items).toHaveLength(3);expect(captured.body.items.map((r:any)=>r.evaluation_id).sort()).toEqual(A.fixture.items.map((r:any)=>r.record.evaluation_id).sort());for(const row of captured.body.items)expect(row.binding.source_dataset_path).toBe(A.source);
  const toB=await handoffProject(page,B);detail=await enter(B);await group.selectOption('product');
  await expect(detail.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(detail.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(6);
  expect(await resultJSON()).toEqual(B.selected.record.result);const outcome=await late.finish();await expect(selector).toHaveValue(B.selected.record.evaluation_id);expect(await resultJSON()).toEqual(B.selected.record.result);await expect(group).toHaveValue('product');
  await expect(selector.locator('option[value="'+A.selected.record.evaluation_id+'"]')).toHaveCount(0);await e.screenshot(page,`${native?'native':'browser'}-saved-filters-B-after-old-A-catalog`);
  const toA=await handoffProject(page,A);detail=await enter(A);await expect(group).toHaveValue('lot');await expect(selector).toHaveValue(A.selected.record.evaluation_id);
  await expect(detail.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(detail.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(6);expect(await resultJSON()).toEqual(A.selected.record.result);
  expect(writes).toEqual([]);for(const scope of [A,B]){expect(handoffTree(scope.source)).toEqual(trees[scope.tag].source);expect(handoffTree(path.join(scope.project.project_dir,'reports','evaluations'))).toEqual(trees[scope.tag].reports);expect(handoffTree(scope.project.annotations_dir)).toEqual(trees[scope.tag].annotations);
   for(const item of scope.fixture.items){expect(handoffHash(fs.readFileSync(item.report_path))).toBe(item.report_sha256);e.addFile(item.report_path);}for(const input of scope.fixture.inputs)expect(handoffHash(fs.readFileSync(input.path))).toBe(input.sha256);
  }
  await e.screenshot(page,`${native?'native':'browser'}-saved-filters-A-return-exact-record`);
  e.note('saved_filter_project_handoff',{record_ids:['F049','F052','F054'],dimension:'handoff',projects:[A.project.id,B.project.id,A.project.id],evaluation_ids:[A.selected.record.evaluation_id,B.selected.record.evaluation_id,A.selected.record.evaluation_id],source_paths:[A.source,B.source,A.source],toB,toA,read_only_late_A:outcome,raw_A_catalog_sha256:captured.sha256,
   A_class_error_score_area_filter_exercised:true,B_and_return_A_ephemeral_filters_reset:true,group_preferences_project_scoped:{A:'lot',B:'product'},saved_result_json_exact:true,business_mutations:writes,original_reports_images_annotations_namespace_hashes:trees,
   controlled_reports_not_model_inference:true,actual_model_inference:false,quality_human_installed_target_parent_approval:false,source_electron:native});
 }catch(error){primary=error;throw error;}finally{page.off('request',observe);try{await late.close();}catch(error){if(!primary)throw error;e.note('saved_filter_handoff_secondary_cleanup',{type:error instanceof Error?error.name:'unknown'});}}
}
test('saved filter project handoff A B A fences an old catalog and preserves exact report UUIDs',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:HandoffApi=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await savedFilterProjectHandoff(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native saved filter project handoff retains original sources filters and reports',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;
 const api:HandoffApi=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const r=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error(`Owned handoff fixture HTTP ${r.status}`);return r.json();},{origin,route,body,method});
 await savedFilterProjectHandoff(page,workspace,evidence,api,true,origin);
});

// The review slider is transient. Its .75/.25 values never become a saved model
// threshold; project remount starts review at .5 and retained reports stay exact.
function rocHandoffRemaining(deadline:number){const left=deadline-performance.now();if(left<=0)throw Error('Original 10s ROC handoff frame expired');return Math.max(1,Math.floor(left));}
async function rocHandoffWithin<T>(work:Promise<T>,deadline:number,label:string):Promise<T>{let timer:NodeJS.Timeout|undefined;try{const value=await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s ROC handoff frame expired: '+label)),rocHandoffRemaining(deadline));})]);expect(performance.now()).toBeLessThanOrEqual(deadline);return value;}finally{clearTimeout(timer);}}
function rocHandoffTree(root:string,retainedFDcustody?:number[]){
 const result:Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>={};const walk=(dir:string)=>{const s=fs.lstatSync(dir);expect(s.isSymbolicLink()).toBe(false);expect(s.isDirectory()).toBe(true);expect(fs.realpathSync(dir)).toBe(dir);result[path.relative(root,dir).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(dir).sort()){const file=path.join(dir,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);if(before.isDirectory())walk(file);else{expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW),retained=retainedFDcustody!==undefined&&['model_deployments.sqlite3','model_deployments.sqlite3-wal','model_deployments.sqlite3-shm'].includes(path.relative(root,file));if(retained)retainedFDcustody!.push(fd);let raw:Buffer;try{const opened=fs.fstatSync(fd);expect([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]).toEqual([before.dev,before.ino,before.mode,before.nlink,before.size,before.mtimeMs,before.ctimeMs]);raw=fs.readFileSync(fd);for(const after of[fs.fstatSync(fd),fs.lstatSync(file)])expect([after.dev,after.ino,after.mode,after.nlink,after.size,after.mtimeMs,after.ctimeMs]).toEqual([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]);}finally{if(!retained)fs.closeSync(fd);}result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:sha(raw!)};}}
 };walk(root);return result;
}
async function rocThresholdProjectHandoff(page:Page,w:Workspace,e:Evidence,api:OwnedApi,native:boolean,origin:string,url?:string){
 // Own two readonly SQLite observers plus same-process raw FD custody only
 // for the three exact deployment DB files opened by the complete tree reads.
 // Fresh opens, full bytes/stat/hash and every DB/WAL/SHM remain in each guard.
 // No transaction, PRAGMA, immutable/nolock, writer, new process or GC is used.
 const {DatabaseSync}=await import('node:sqlite');
 const sqliteObservers:Array<import('node:sqlite').DatabaseSync>=[],retainedFDcustody:number[]=[];let fixtureFailed=false;
 try{
 const read=async(route:string,body?:unknown,method?:string)=>{const deadline=performance.now()+10_000;return rocHandoffWithin(api(route,body,method),deadline,'fixture API full body');};
 const make=async(tag:'A'|'B')=>{const source=path.join(w.root,'roc-threshold-'+tag);fs.mkdirSync(source);for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),png(16,3,(x,y)=>[x,y,(tag==='A'?30:130)+i]),{flag:'wx'});
  const created=await read('/api/project/create',{name:'ROC review handoff '+tag,task:'segmentation'});await read('/api/project/update',{source_dataset_dir:source},'PUT');await read('/api/dataset/import',{folder_path:source,task:'segmentation'});const project=await read('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));expect(fixture.kind).toBe('controlled_saved_filter_reports_not_model_inference');
  const full=fixture.items.find((r:any)=>r.variant==='full');expect(full).toBeTruthy();expect(full.record.binding.threshold_settings).toEqual({probability_threshold:.5});expect(full.record.binding.source_dataset_path).toBe(source);expect(full.record.result.test_predictions).toHaveLength(6);for(const input of fixture.inputs)expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);
  await read('/api/team-data');await read('/api/team-data/readiness');await read('/api/team-data/queue?offset=0&limit=30');
  // Complete the existing project-store initializers before the full raw-tree
  // baseline. These are genuine GETs already made by the initial app panels.
  for(const route of['/api/product-delivery/packages','/api/fleet/targets','/api/runtime-services','/api/runtime-services/capture-groups','/api/flow-evaluations/approvals/active','/api/model-deployments/active?source_dataset_path='+encodeURIComponent(source)+'&task=segmentation','/api/dataset/versions'])await read(route);
  await read('/api/provenance/impact');
  const database=path.join(project.project_dir,'model_deployments.sqlite3'),databaseInfo=fs.lstatSync(database);expect(databaseInfo.isSymbolicLink()).toBe(false);expect(databaseInfo.isFile()).toBe(true);expect(databaseInfo.nlink).toBe(1);expect(fs.realpathSync(database)).toBe(database);
  const observer=new DatabaseSync(database,{readOnly:true});sqliteObservers.push(observer);
  observer.prepare('SELECT revisions.* FROM active_revisions JOIN revisions ON revisions.revision_id=active_revisions.revision_id').all();
  return{tag,source,project,fixture,full};
 };
 const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.full.record.evaluation_id).not.toBe(B.full.record.evaluation_id);await read('/api/project/open',{project_dir:A.project.project_dir});if(url)await page.goto(url);else await page.reload();
 const trees=(scope:typeof A)=>({source:rocHandoffTree(scope.source),project:rocHandoffTree(scope.project.project_dir,retainedFDcustody)}),before={A:trees(A),B:trees(B)};
 const summary=page.locator('summary').filter({hasText:/^평가 이력 · 제품\/Lot별 오류$/}),history=summary.locator('..'),family=history.getByLabel('평가 이력 모델 종류',{exact:true}),selector=history.getByLabel(/^모델별 저장 평가/);
 const writes:Array<{method:string;path:string;body:string|null}>=[];const observe=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:u.pathname,body:r.postData()});};page.on('request',observe);
 const enter=async(scope:typeof A)=>{
  const deadline=performance.now()+10_000;await rocHandoffWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:rocHandoffRemaining(deadline)}),deadline,'own project header');await rocHandoffWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click({timeout:rocHandoffRemaining(deadline)}),deadline,'evaluation stage');if(await rocHandoffWithin(history.getAttribute('open'),deadline,'history disclosure')===null)await rocHandoffWithin(summary.click({timeout:rocHandoffRemaining(deadline)}),deadline,'open history');await rocHandoffWithin(family.selectOption('detection',{timeout:rocHandoffRemaining(deadline)}),deadline,'saved report task');await rocHandoffWithin(expect(selector.locator('option[value="'+scope.full.record.evaluation_id+'"]')).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)}),deadline,'own saved record available');await rocHandoffWithin(selector.selectOption(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)}),deadline,'explicit saved record');
  const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await rocHandoffWithin(detail.getAttribute('open'),deadline,'current evidence disclosure')===null)await rocHandoffWithin(detail.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)}),deadline,'open current evidence');await rocHandoffWithin(expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5',{timeout:rocHandoffRemaining(deadline)}),deadline,'project-entry reset before refresh');await rocHandoffWithin(saved(scope),deadline,'own full record before refresh');
  // A real refresh yields a fresh owning Request and full unchanged saved body.
  // No intercepted, delegated, synthesized, or replayed network reply is used.
  const refreshDeadline=performance.now()+10_000,generation=new Set<import('@playwright/test').Request>();const started=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(r.frame()===page.mainFrame()&&r.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='detection'&&!u.searchParams.has('labelset_id'))generation.add(r);};page.on('request',started);
  let readback:any;try{const wire=page.waitForResponse(r=>generation.has(r.request()),{timeout:rocHandoffRemaining(refreshDeadline)});await rocHandoffWithin(history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(refreshDeadline)}),refreshDeadline,'actual readonly refresh');const response=await rocHandoffWithin(wire,refreshDeadline,'fresh actual history response');expect(response.status()).toBe(200);const raw=await rocHandoffWithin(response.body(),refreshDeadline,'complete saved history JSON');expect(await rocHandoffWithin(response.finished(),refreshDeadline,'saved history HTTP completion')).toBeNull();const body=JSON.parse(raw.toString('utf8'));expect(body.items).toHaveLength(3);expect(body.items.map((r:any)=>r.evaluation_id).sort()).toEqual(scope.fixture.items.map((r:any)=>r.record.evaluation_id).sort());for(const item of scope.fixture.items)expect(body.items.find((r:any)=>r.evaluation_id===item.record.evaluation_id)).toEqual(item.record);readback={body,body_sha256:sha(raw),status:200,method:'GET',source:scope.source,origin,main_frame:true};await rocHandoffWithin(expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(refreshDeadline)}),refreshDeadline,'owning selected record after refresh');}finally{page.off('request',started);}
  if(await detail.getAttribute('open')===null)await detail.locator('summary').first().click();return{detail,readback,project_entry_reset_before_refresh:true};
 };
 const saved=async(scope:typeof A)=>{const result=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();if(await result.getAttribute('open')===null)await result.locator('summary').first().click();expect(JSON.parse(await result.locator('pre').innerText())).toEqual(scope.full.record.result);const binding=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();if(await binding.getAttribute('open')===null)await binding.locator('summary').first().click();const raw=JSON.parse(await binding.locator('pre').innerText());expect(raw).toEqual({evaluation_id:scope.full.record.evaluation_id,evidence_sha256:scope.full.record.evidence_sha256,binding:scope.full.record.binding});await expect(history.getByText('평가 임계값:',{exact:false})).toContainText('"probability_threshold":0.5');return raw;};
 const review=async(detail:ReturnType<Page['locator']>,value:string,arithmetic:string)=>{const deadline=performance.now()+10_000,slider=detail.getByLabel('ROC 검토 임계값',{exact:true});await rocHandoffWithin(slider.fill(value,{timeout:rocHandoffRemaining(deadline)}),deadline,'actual review slider change');await rocHandoffWithin(expect(slider).toHaveValue(value,{timeout:rocHandoffRemaining(deadline)}),deadline,'review slider value');await rocHandoffWithin(expect(detail).toContainText(arithmetic,{timeout:rocHandoffRemaining(deadline)}),deadline,'exact independent ROC arithmetic');await rocHandoffWithin(expect(detail).toContainText('AUC 0.8333',{timeout:rocHandoffRemaining(deadline)}),deadline,'same original ROC');return{value,arithmetic,model_or_flow_threshold_saved:false};};
 const switchProject=async(scope:typeof A)=>{const deadline=performance.now()+10_000;await rocHandoffWithin(page.getByTitle('프로젝트 관리',{exact:true}).click({timeout:rocHandoffRemaining(deadline)}),deadline,'project manager');const manager=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await rocHandoffWithin(manager.getByRole('button',{name:'최근 프로젝트',exact:true}).click({timeout:rocHandoffRemaining(deadline)}),deadline,'recent projects');const generation=new Set<import('@playwright/test').Request>(),started=(r:import('@playwright/test').Request)=>{if(r.frame()===page.mainFrame()&&r.method()==='POST'&&r.url()===origin+'/api/project/open')generation.add(r);};page.on('request',started);
  try{const wire=page.waitForResponse(r=>generation.has(r.request())&&r.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:rocHandoffRemaining(deadline)});const item=manager.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await rocHandoffWithin(expect(item).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)}),deadline,'exact recent row');await rocHandoffWithin(item.click({timeout:rocHandoffRemaining(deadline)}),deadline,'normal project selection');const response=await rocHandoffWithin(wire,deadline,'own project open');expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});const raw=await rocHandoffWithin(response.body(),deadline,'full project response');expect(await rocHandoffWithin(response.finished(),deadline,'project HTTP completion')).toBeNull();const body=JSON.parse(raw.toString('utf8'));expect([body.id,body.project_dir,body.source_dataset_dir]).toEqual([scope.project.id,scope.project.project_dir,scope.source]);await rocHandoffWithin(expect(manager).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)}),deadline,'normal manager closes');return{project_id:body.id,response_sha256:sha(raw),source:body.source_dataset_dir};}finally{page.off('request',started);}
 };
 try{
  const first=await enter(A);await expect(first.detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');const A_review=await review(first.detail,'0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.'),A_saved=await saved(A);await e.screenshot(page,`${native?'source-electron':'browser'}-roc-A-review-075-saved-05`);
  const toB=await switchProject(B),second=await enter(B);await expect(second.detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(selector.locator('option[value="'+A.full.record.evaluation_id+'"]')).toHaveCount(0);const B_review=await review(second.detail,'0.25','TP 3 · 미검 FN 0 · 과검 FP 1 · TN 1.'),B_saved=await saved(B);await e.screenshot(page,`${native?'source-electron':'browser'}-roc-B-review-025-no-A-record`);
  const toA=await switchProject(A),third=await enter(A);await expect(third.detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(third.detail).toContainText('TP 2 · 미검 FN 1 · 과검 FP 1 · TN 1.');await expect(selector.locator('option[value="'+B.full.record.evaluation_id+'"]')).toHaveCount(0);expect(await saved(A)).toEqual(A_saved);const A_review_again=await review(third.detail,'0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.');expect(await saved(A)).toEqual(A_saved);await e.screenshot(page,`${native?'source-electron':'browser'}-roc-return-A-reset-05-explicit-review-075`);
  expect(writes).toEqual([{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:B.project.project_dir})},{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:A.project.project_dir})}]);for(const scope of[A,B]){expect(trees(scope)).toEqual(before[scope.tag]);for(const item of scope.fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);e.addFile(item.report_path);}for(const input of scope.fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);e.addFile(input.path);}}
  const proof={key:'F056.saved-roc-threshold.handoff',projects:[A.project.id,B.project.id,A.project.id],evaluation_ids:[A.full.record.evaluation_id,B.full.record.evaluation_id,A.full.record.evaluation_id],toB,toA,A_review,B_review,A_review_again,return_A_review_reset:.5,project_entry_resets_observed_before_refresh:[first.project_entry_reset_before_refresh,second.project_entry_reset_before_refresh,third.project_entry_reset_before_refresh],saved_threshold_settings:{A:A_saved.binding.threshold_settings,B:B_saved.binding.threshold_settings},saved_result_and_full_binding_exact:true,owning_history_reads:[first.readback,second.readback,third.readback],original_raw_trees:before,business_writes:writes,fixture_readonly_sqlite_observers:{count:sqliteObservers.length,read_only:true,completed_select:true,held_through_full_tree_assertion:true,DB_WAL_SHM_all_in_unchanged_guard:true},fixture_same_process_retained_FD_custody:{count:retainedFDcustody.length,exact_root_filenames:['model_deployments.sqlite3','model_deployments.sqlite3-wal','model_deployments.sqlite3-shm'],fresh_readonly_nofollow_open_each_guard:true,held_through_full_tree_assertion:true,close_order:'all SQLite observers then all retained raw FDs'},controlled_saved_reports_not_model_inference:true,actual_model_inference:false,source_electron:native,quality_human_installed_target_parent_approval:false};const output=path.join(w.logs,'roc-threshold-handoff-proof.json');fs.writeFileSync(output,JSON.stringify(proof),{flag:'wx'});e.addFile(output);e.note('roc_review_threshold_project_handoff',proof);
 }finally{page.off('request',observe);}
 }catch(error){fixtureFailed=true;throw error;}finally{
  let closeFailed=false,firstClose:unknown,closeFailureCount=0,sqliteCloseFailures=0,retainedFDCloseFailures=0;
  for(const observer of sqliteObservers.reverse()){try{observer.close();}catch(error){sqliteCloseFailures++;closeFailureCount++;if(!closeFailed){closeFailed=true;firstClose=error;}}}
  for(const fd of retainedFDcustody.reverse()){try{fs.closeSync(fd);}catch(error){retainedFDCloseFailures++;closeFailureCount++;if(!closeFailed){closeFailed=true;firstClose=error;}}}
  if(closeFailed){try{e.note('roc_readonly_observer_secondary_cleanup',{failures:closeFailureCount,sqlite_failures:sqliteCloseFailures,retained_FD_failures:retainedFDCloseFailures,type:typeof firstClose});}catch{}if(!fixtureFailed)throw firstClose;}
 }
}
test('ROC review thresholds change explicitly and reset across A B A while saved reports stay exact',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await rocThresholdProjectHandoff(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native ROC review handoff resets transient thresholds and preserves exact saved record bindings',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;const api:OwnedApi=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const r=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error('Owned ROC fixture HTTP '+r.status);return r.json();},{origin,route,body,method});await rocThresholdProjectHandoff(page,workspace,evidence,api,true,origin);
});

// F055-only browser extension. Generated saved reports exercise display/state,
// not detector inference, representative truth or manufacturing approval.
async function savedAreaBinProjectHandoff(page:Page,w:Workspace,e:Evidence,origin:string,url:string){
 const frame=async<T>(label:string,work:(deadline:number)=>Promise<T>):Promise<T>=>{const deadline=performance.now()+10_000;return rocHandoffWithin(work(deadline),deadline,label);};
 const api:OwnedApi=(route,body,method)=>frame('owned area fixture API full body',async deadline=>{
  const response=await page.request.fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),timeout:rocHandoffRemaining(deadline),...(body===undefined?{}:{data:body})});
  const raw=await response.body();expect(response.ok(),'Owned area fixture API HTTP '+response.status()).toBe(true);return JSON.parse(raw.toString('utf8'));
 });
 const make=async(tag:'A'|'B')=>{
  const source=path.join(w.root,'area-bin-handoff-'+tag);fs.mkdirSync(source);
  for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),handoffPng(16,3,(x,y)=>[x,y,(tag==='A'?30:130)+i]),{flag:'wx'});
  const created=await api('/api/project/create',{name:'Area bin handoff '+tag,task:'segmentation'});
  await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
  const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
  expect(fixture.kind).toBe('controlled_saved_filter_reports_not_model_inference');expect(fixture.inputs).toHaveLength(6);expect(fixture.items).toHaveLength(3);
  const full=fixture.items.find((item:any)=>item.variant==='full');expect(full).toBeTruthy();expect(full.record.binding.source_dataset_path).toBe(source);expect(full.record.binding.task).toBe('detection');expect(full.record.result.test_predictions).toHaveLength(6);
  for(const row of full.record.result.test_predictions){expect(row.file_path).toBe(path.join(source,row.file_name));expect(row.object_evidence.coordinate_space).toBe('original_image_px');expect(row.object_evidence.source_size).toEqual([16,16]);}
  await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/team-data/queue?offset=0&limit=30');
  return {tag,source,project,fixture,full};
 };
 const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.full.record.evaluation_id).not.toBe(B.full.record.evaluation_id);
 const rawTrees=(scope:typeof A)=>({source:rocHandoffTree(scope.source),reports:rocHandoffTree(path.join(scope.project.project_dir,'reports','evaluations')),annotations:rocHandoffTree(scope.project.annotations_dir)});
 const before={A:rawTrees(A),B:rawTrees(B)};
 await api('/api/project/open',{project_dir:A.project.project_dir});await frame('initial own renderer',deadline=>page.goto(url,{timeout:rocHandoffRemaining(deadline)}));
 const summary=page.locator('summary').filter({hasText:/^평가 이력 · 제품\/Lot별 오류$/}),history=summary.locator('..');
 const family=history.getByLabel('평가 이력 모델 종류',{exact:true}),selector=history.getByLabel(/^모델별 저장 평가/),group=history.getByLabel('평가 오류 집계 기준',{exact:true});
 const fullNames=['case-0.png · FN 0 / FP 0','case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1','case-3.png · FN 0 / FP 0','case-4.png · FN 0 / FP 0','case-5.png · FN 0 / FP 1'];
 const saved=async(scope:typeof A)=>frame('exact own saved result and binding',async deadline=>{
  const result=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();
  if(await result.getAttribute('open')===null)await result.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
  expect(JSON.parse(await result.locator('pre').innerText())).toEqual(scope.full.record.result);
  const binding=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();
  if(await binding.getAttribute('open')===null)await binding.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
  const value=JSON.parse(await binding.locator('pre').innerText());expect(value).toEqual({evaluation_id:scope.full.record.evaluation_id,evidence_sha256:scope.full.record.evidence_sha256,binding:scope.full.record.binding});return value;
 });
 const enter=async(scope:typeof A)=>{
  const detail=await frame('own report and area reset before refresh',async deadline=>{
   await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:rocHandoffRemaining(deadline)});
   await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click({timeout:rocHandoffRemaining(deadline)});
   if(await history.getAttribute('open')===null)await summary.click({timeout:rocHandoffRemaining(deadline)});
   await family.selectOption('detection',{timeout:rocHandoffRemaining(deadline)});
   await expect(selector.locator('option[value="'+scope.full.record.evaluation_id+'"]')).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
   await selector.selectOption(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
   const current=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();
   if(await current.getAttribute('open')===null)await current.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   await expect(current.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
   await expect(current.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
   await expect(current.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   await expect(current.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(6,{timeout:rocHandoffRemaining(deadline)});
   expect(await current.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual(fullNames);return current;
  });
  const binding=await saved(scope);
  // Refresh only after observing entry reset, and before selecting the area.
  // No refresh, family toggle or clear action occurs while the area is active.
  const readback=await frame('fresh owning history response before area selection',async deadline=>{
   const generation=new Set<HandoffRequest>();const started=(request:HandoffRequest)=>{const u=new URL(request.url());if(request.frame()===page.mainFrame()&&request.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='detection'&&!u.searchParams.has('labelset_id'))generation.add(request);};page.on('request',started);
   try{
    const wire=page.waitForResponse(response=>generation.has(response.request()),{timeout:rocHandoffRemaining(deadline)});
    await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)});
    const response=await wire;expect(response.status()).toBe(200);const raw=await response.body();expect(await response.finished()).toBeNull();const body=JSON.parse(raw.toString('utf8'));
    expect(body.items).toHaveLength(3);expect(body.items.map((item:any)=>item.evaluation_id).sort()).toEqual(scope.fixture.items.map((item:any)=>item.record.evaluation_id).sort());
    for(const item of scope.fixture.items)expect(body.items.find((row:any)=>row.evaluation_id===item.record.evaluation_id)).toEqual(item.record);
    await expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
    return {method:'GET',status:200,main_frame:true,source:scope.source,body,sha256:handoffHash(raw),full_HTTP_completion:true,no_interception_or_delegation:true};
   }finally{page.off('request',started);}
  });
  await frame('unchanged unfiltered result after refresh',async deadline=>{
   if(await detail.getAttribute('open')===null)await detail.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   await expect(detail.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   await expect(detail.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(6,{timeout:rocHandoffRemaining(deadline)});
   expect(await detail.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual(fullNames);
  });
  return {detail,binding,readback,area_reset_before_refresh:true,unfiltered_names:fullNames};
 };
 const selectArea=async(detail:ReturnType<Page['locator']>,index:3|7,expected:string)=>frame('actual independent area bin selection',async deadline=>{
  const areaSummary=detail.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'});
  if(await areaSummary.locator('..').getAttribute('open')===null)await areaSummary.click({timeout:rocHandoffRemaining(deadline)});
  const bins=detail.getByRole('button',{name:/^결함 면적 /}),counts=[1,0,0,1,0,0,0,1,0,1];await expect(bins).toHaveCount(10,{timeout:rocHandoffRemaining(deadline)});
  for(let i=0;i<10;i++)await expect(bins.nth(i)).toHaveAttribute('aria-label',`결함 면적 ${(i*4.9).toFixed(1)}에서 ${((i+1)*4.9).toFixed(1)} ${counts[i]}개`,{timeout:rocHandoffRemaining(deadline)});
  await bins.nth(index).click({timeout:rocHandoffRemaining(deadline)});
  await expect(detail.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
  await expect(detail.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
  await expect(detail.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
  await expect(detail.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
  expect(await detail.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual([expected]);
  return {bin_index:index,bin_counts:counts,selected_names:[expected],class_filter:'all',error_filter:'all',area_filter_active:true,clear_button_present:true};
 });
 const switchProject=async(scope:typeof A,detail:ReturnType<Page['locator']>,activeName:string)=>frame('actual recent project handoff with active area',async deadline=>{
  await expect(detail.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
  expect(await detail.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents()).toEqual([activeName]);
  await page.getByTitle('프로젝트 관리',{exact:true}).click({timeout:rocHandoffRemaining(deadline)});const manager=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
  await manager.getByRole('button',{name:'최근 프로젝트',exact:true}).click({timeout:rocHandoffRemaining(deadline)});
  const generation=new Set<HandoffRequest>(),started=(request:HandoffRequest)=>{if(request.frame()===page.mainFrame()&&request.method()==='POST'&&request.url()===origin+'/api/project/open')generation.add(request);};page.on('request',started);
  try{
   const wire=page.waitForResponse(response=>generation.has(response.request())&&response.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:rocHandoffRemaining(deadline)});
   const item=manager.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await expect(item).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});await item.click({timeout:rocHandoffRemaining(deadline)});
   const response=await wire;expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});const raw=await response.body();expect(await response.finished()).toBeNull();const body=JSON.parse(raw.toString('utf8'));
   expect([body.id,body.project_dir,body.source_dataset_dir]).toEqual([scope.project.id,scope.project.project_dir,scope.source]);await expect(manager).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   return {project_id:body.id,project_dir:body.project_dir,source:body.source_dataset_dir,status:200,request_body:response.request().postDataJSON(),response_sha256:handoffHash(raw),full_HTTP_completion:true,area_active_before_normal_project_open:true};
  }finally{page.off('request',started);}
 });
 const writes:Array<{method:string;path:string;body:string|null}>=[];const observe=(request:HandoffRequest)=>{const u=new URL(request.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),path:u.pathname,body:request.postData()});};page.on('request',observe);
 try{
  const first=await enter(A);await frame('A group preference',deadline=>group.selectOption('lot',{timeout:rocHandoffRemaining(deadline)}));const A_area=await selectArea(first.detail,3,'case-2.png · FN 0 / FP 1');await e.screenshot(page,'browser-area-A-bin3-active');
  const toB=await switchProject(B,first.detail,'case-2.png · FN 0 / FP 1'),second=await enter(B);await expect(selector.locator('option[value="'+A.full.record.evaluation_id+'"]')).toHaveCount(0,{timeout:10_000});
  await frame('B group preference',deadline=>group.selectOption('product',{timeout:rocHandoffRemaining(deadline)}));await e.screenshot(page,'browser-area-B-reset-own-record');
  const B_area=await selectArea(second.detail,7,'case-4.png · FN 0 / FP 0');await e.screenshot(page,'browser-area-B-bin7-active');
  const toA=await switchProject(A,second.detail,'case-4.png · FN 0 / FP 0'),third=await enter(A);await expect(group).toHaveValue('lot',{timeout:10_000});await expect(selector.locator('option[value="'+B.full.record.evaluation_id+'"]')).toHaveCount(0,{timeout:10_000});expect(third.binding).toEqual(first.binding);await e.screenshot(page,'browser-area-return-A-reset-own-record');
  expect(writes).toEqual([{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:B.project.project_dir})},{method:'POST',path:'/api/project/open',body:JSON.stringify({project_dir:A.project.project_dir})}]);
  const after={A:rawTrees(A),B:rawTrees(B)};expect(after).toEqual(before);
  for(const scope of[A,B]){for(const item of scope.fixture.items){expect(handoffHash(fs.readFileSync(item.report_path))).toBe(item.report_sha256);e.addFile(item.report_path);}for(const input of scope.fixture.inputs){expect(handoffHash(fs.readFileSync(input.path))).toBe(input.sha256);e.addFile(input.path);}}
  const proof={schema:'modu-vision.f055-area-project-handoff-proof/v1',key:'F055.area-bin-filter.handoff',mode:'browser',projects:[A.project.id,B.project.id,A.project.id],evaluation_ids:[A.full.record.evaluation_id,B.full.record.evaluation_id,A.full.record.evaluation_id],source_paths:[A.source,B.source,A.source],fixtures:{A,B},A_area,B_area,toB,toA,entry_area_reset_before_refresh:[first.area_reset_before_refresh,second.area_reset_before_refresh,third.area_reset_before_refresh],entry_unfiltered_names:[first.unfiltered_names,second.unfiltered_names,third.unfiltered_names],owning_history_reads:[first.readback,second.readback,third.readback],saved_bindings:[first.binding,second.binding,third.binding],raw_trees_before:before,raw_trees_after:after,business_writes:writes,guard_scope:'two source, two evaluation-report and two annotation trees; not entire project runtime stores',no_refresh_or_clear_while_area_active:true,no_request_interception_or_delegation:true,controlled_saved_reports_not_model_inference:true,actual_model_inference:false,source_electron:false,quality_human_installed_target_parent_approval:false};
  const output=path.join(w.logs,'f055-area-handoff-proof.json');fs.writeFileSync(output,JSON.stringify(proof),{flag:'wx'});e.addFile(output);e.note('area_bin_project_handoff',proof);
 }finally{page.off('request',observe);}
}
test('saved area bins remain active until real A B A project handoff and reset to exact owning reports',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);await savedAreaBinProjectHandoff(page,workspace,evidence,renderer.origin,renderer.url);
});

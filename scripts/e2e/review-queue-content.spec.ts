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
const sha=(p:string)=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(workspace.root,'queue-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,i)=>{const p=path.join(source,name+'.png');fs.writeFileSync(p,png(64,3,(x,y)=>[x,y,50+i]));return {path:p,sha256:sha(p)};});
 const project=await api('/api/project/create',{name:'Saved review queue controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();
  await page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true}).click();};
 await navigate();const panel=page.getByRole('region',{name:'저장된 검토 큐'}),create=panel.getByRole('button',{name:'우선순위 큐 저장',exact:true});
 await expect(create).toBeDisabled();await expect(panel).toContainText('4단계에서 현재 데이터의 평가를 저장하면 검토 큐를 만들 수 있습니다.');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),workspace.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 await navigate();await expect(create).toBeEnabled();const threshold=panel.getByRole('spinbutton',{name:'검토 큐 임계값',exact:true});
 await threshold.fill('2');const invalid=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();expect((await invalid).status()).toBe(422);
 await expect(panel.getByRole('alert')).toBeVisible();expect((await api('/api/data-workbench/review-queues')).queues).toEqual([]);
 await threshold.fill('0.5');const created=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();const response=await created;expect(response.status()).toBe(200);const queue=await response.json();
 expect(queue.items.map((r:any)=>[r.relative_path,r.reasons,r.priority])).toEqual([['error.png',['error','threshold'],400],['disagreement.png',['disagreement'],200],['threshold.png',['threshold'],100]]);
 await expect(panel.getByRole('alert')).toHaveCount(0);await panel.getByRole('button',{name:'현재 항목 열기',exact:true}).click();
 const advance=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true}),skip=panel.getByRole('button',{name:'보류 · 다음',exact:true});await expect(advance).toBeEnabled();
 await advance.click();await expect(panel.getByRole('alert')).toContainText('검토자 이름');expect((await api('/api/data-workbench/review-queues/'+queue.id)).cursor).toBe(0);
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await page.getByRole('textbox',{name:'작업자·검토자 이름',exact:true}).fill('Owned queue test operator');await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
 await skip.click();await expect(panel).toContainText('검토 진행 1 / 3');await expect(advance).toBeEnabled();await advance.click();await expect(panel).toContainText('검토 진행 2 / 3');await skip.click();await expect(panel).toContainText('큐의 모든 항목을 검토했습니다.');
 const finished=await api('/api/data-workbench/review-queues/'+queue.id);expect(finished.cursor).toBe(3);expect(finished.revision).toBe(4);
 expect(finished.history.map((r:any)=>[r.relative_path,r.state,r.actor])).toEqual([['error.png','skipped','Owned queue test operator'],['disagreement.png','reviewed','Owned queue test operator'],['threshold.png','skipped','Owned queue test operator']]);
 await navigate();await expect(panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 3 / 3');await expect(advance).toBeDisabled();await expect(skip).toBeDisabled();
 const reopened=await api('/api/data-workbench/review-queues/'+queue.id);expect(reopened).toEqual(finished);
 for(const input of inputs){expect(sha(input.path)).toBe(input.sha256);const metadata=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(input.path));expect(metadata.workflow_state).not.toBe('approved');}
 expect(sha(fixture.path)).toBe(fixture.sha256);
 // Temporarily change one owned synthetic image: the same saved queue must refuse resumption.
 const original=fs.readFileSync(inputs[0].path);try{fs.writeFileSync(inputs[0].path,png(64,3,()=>[1,2,3]));await navigate();await expect(panel).toContainText('원본 변경으로 재생성 필요');await expect(panel.getByRole('alert')).toContainText('source image changed');await expect(panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true})).toBeDisabled();}finally{fs.writeFileSync(inputs[0].path,original);}
 await navigate();await expect(panel).not.toContainText('원본 변경으로 재생성 필요');expect(await api('/api/data-workbench/review-queues/'+queue.id)).toEqual(finished);
 await evidence.screenshot(page,native?'native-review-queue-reopened':'browser-review-queue-reopened');
 evidence.note('saved_review_queue_content',{project_id:project.id,inputs,fixture,finished,reopened,empty_origin_disabled:true,invalid_threshold_422:true,missing_reviewer_refused:true,priority_order_verified:true,skip_and_review_history_verified:true,stale_source_refused:true,reload_exact_cursor:true,original_bytes_preserved:true,native,controlled_reports_not_model_inference:true,human_quality_or_annotation_approval:false});
}
test('saved review queue preserves priority cursor and reviewer without approving labels',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved review queue preserves priority cursor and reviewer without approving labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned queue API ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});


// Additional U015.saved-queue-create.handoff; reports remain controlled, not inference.
import type {Request as QueueHandoffRequest} from '@playwright/test';
import {handoffApi as queueHandoffApi,handoffProject as queueHandoffProject,handoffLateRead as queueLateRead,handoffRoots as queueRoots,handoffAssertRoots as queueAssertRoots,handoffPost as queuePost,handoffSave as queueSave} from './fixtures/remaining-project-handoff';
async function queueCreateProjectHandoff(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=queueHandoffApi(page,origin,native);
 const prepare=async(tag:'A'|'B')=>{
  const source=path.join(w.root,'queue-create-handoff-'+tag);fs.mkdirSync(source);
  const inputs=['error','disagreement','threshold'].map((name,i)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,(tag==='A'?50:150)+i]));return {path:file,sha256:sha(file)};});
  const project=await api('/api/project/create',{name:'Queue create handoff '+tag,task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(sha(fixture.path)).toBe(fixture.sha256);
  await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/team-data/queue?offset=0&limit=30');await api('/api/project/preferences');
  const metadata=[];for(const row of inputs){const image=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(row.path));expect(image.content_hash).toBe(row.sha256);expect(image.image_uuid).toBeTruthy();metadata.push(image);await api('/api/annotations/'+path.basename(row.path,'.png')+'?file_path='+encodeURIComponent(row.path));}
  const queue=tag==='B'?await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05}):null;
  return {tag,source,project:await api('/api/project/current'),inputs,metadata,fixture,queue};
 };
 const A=await prepare('A'),B=await prepare('B');expect(A.project.id).not.toBe(B.project.id);expect(A.fixture.record.evaluation_id).not.toBe(B.fixture.record.evaluation_id);expect(A.metadata.some(row=>B.metadata.some(other=>row.image_uuid===other.image_uuid))).toBe(false);
 expect(A.fixture.record.result.test_predictions.map((r:any)=>r.image_sha256)).toEqual(A.inputs.map(row=>row.sha256));
 const enter=async(scope:typeof A)=>{
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();
  const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();const panel=page.getByRole('region',{name:'저장된 검토 큐'});await expect(panel.getByRole('combobox',{name:'검토 큐 원본 평가',exact:true})).toHaveValue(scope.fixture.record.evaluation_id);return panel;
 };
 if(url)await page.goto(url);else await page.reload();await enter(B);await queueHandoffProject(page,A,origin);let panel=await enter(A);expect((await api('/api/data-workbench/review-queues')).queues).toEqual([]);
 const rootsA=queueRoots(A);await queueHandoffProject(page,B,origin);await enter(B);const rootsB=queueRoots(B),savedB=await api('/api/data-workbench/review-queues/'+B.queue.id);await queueHandoffProject(page,A,origin);panel=await enter(A);
 const writes:any[]=[],observe=(request:QueueHandoffRequest)=>{const u=new URL(request.url());if(u.origin===origin&&!['GET','HEAD','OPTIONS'].includes(request.method())&&u.pathname.startsWith('/api/')&&u.pathname!=='/api/project/open')writes.push({method:request.method(),path:u.pathname,body:request.postDataJSON()});};page.on('request',observe);
 const expected={evaluation_id:A.fixture.record.evaluation_id,threshold:.5,margin:.05};let primary:unknown;
 try{
  const created=await queuePost(page,origin,panel.getByRole('button',{name:'우선순위 큐 저장',exact:true}),'/api/data-workbench/review-queues',expected,e,w,'queue-handoff');const queue=created.body;
  await expect(panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');await expect(panel.getByRole('button',{name:'우선순위 큐 저장',exact:true})).toBeEnabled();
  expect(queue.scope.source).toBe(A.source);expect(queue.origin.evaluation_id).toBe(A.fixture.record.evaluation_id);expect(queue.cursor).toBe(0);expect(queue.history).toEqual([]);
  expect(queue.items.map((r:any)=>[r.relative_path,r.reasons,r.priority])).toEqual([['error.png',['error','threshold'],400],['disagreement.png',['disagreement'],200],['threshold.png',['threshold'],100]]);
  const catalogA=await api('/api/data-workbench/review-queues');expect(catalogA.queues).toHaveLength(1);expect(catalogA.queues[0].id).toBe(queue.id);const savedA=await api('/api/data-workbench/review-queues/'+queue.id),expectedRoots=queueRoots(A);
  // Genuine queue creation is the only new A persisted file; its raw path/hash is pinned.
  for(const kind of ['source','annotations','models','reports'])expect(expectedRoots[kind]).toEqual(rootsA[kind]);
  const storage='data_workbench/'+crypto.createHash('sha256').update(fs.realpathSync(A.source)).digest('hex').slice(0,24),beforeFiles=rootsA.dataset.snapshot.files,afterFiles=expectedRoots.dataset.snapshot.files,newMembers=Object.keys(afterFiles).filter(x=>!(x in beforeFiles));expect(newMembers).toEqual([storage+'/review_queues/'+queue.id+'.json']);for(const member of Object.keys(beforeFiles))expect(afterFiles[member]).toEqual(beforeFiles[member]);
  const addedDirectories=expectedRoots.dataset.snapshot.directories.filter(x=>!rootsA.dataset.snapshot.directories.includes(x));expect(addedDirectories).toEqual(['data_workbench',storage,storage+'/review_queues'].filter(x=>!rootsA.dataset.snapshot.directories.includes(x)));
  expect(expectedRoots.dataset.snapshot.directories).toEqual([...rootsA.dataset.snapshot.directories,...addedDirectories].sort());
  for(const row of queue.items){const image=A.metadata.find(item=>item.file_path===row.file_path)!;expect(image).toBeTruthy();expect(image.content_hash).toBe(row.source_sha256);}
  const rawQueue=JSON.parse(fs.readFileSync(path.join(A.project.dataset_dir,newMembers[0]),'utf8'));expect(rawQueue).toEqual(savedA);
  const late=await queueLateRead(page,origin,native,u=>u.pathname==='/api/data-workbench/review-queues');let latePrimary:unknown;
  try{
   await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const captured=await late.ready();expect(captured.body).toEqual(catalogA);
   const toB=await queueHandoffProject(page,B,origin);const panelB=await enter(B);await expect(panelB.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(B.queue.id);expect(await api('/api/data-workbench/review-queues/'+B.queue.id)).toEqual(savedB);queueAssertRoots(rootsB,queueRoots(B));
   const outcome=await late.finish();await expect(panelB.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(B.queue.id);expect((await api('/api/data-workbench/review-queues')).queues.map((r:any)=>r.id)).toEqual([B.queue.id]);queueAssertRoots(rootsB,queueRoots(B));
   const toA=await queueHandoffProject(page,A,origin);panel=await enter(A);await expect(panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');expect(await api('/api/data-workbench/review-queues/'+queue.id)).toEqual(savedA);queueAssertRoots(expectedRoots,queueRoots(A));
   expect(writes).toEqual([{method:'POST',path:'/api/data-workbench/review-queues',body:expected}]);for(const scope of [A,B]){for(const input of scope.inputs)expect(sha(input.path)).toBe(input.sha256);expect(sha(scope.fixture.path)).toBe(scope.fixture.sha256);}
   await e.screenshot(page,`${native?'native':'browser'}-created-queue-exact-A-return-after-old-A-catalog`);queueSave(e,w,'queue-create-handoff-proof',{A,B,rootsA,rootsB,expectedRoots,created:created.proof,savedA,savedB,newMembers,toB,toA,captured_sha256:captured.sha256,outcome,writes});
   e.note('saved_queue_create_project_handoff',{record_id:'U015',action:'saved-queue-create',dimension:'handoff',actual_UI_create:created.proof,projects:[A.project.id,B.project.id,A.project.id],evaluation_ids:[A.fixture.record.evaluation_id,B.fixture.record.evaluation_id],queue_ids:[queue.id,B.queue.id,queue.id],toB,toA,late_read:outcome,original_inputs_reports_labels_exact:true,queue_cursor_and_history_exact:true,controlled_reports_not_model_inference:true,human_quality_installed_target_parent_approval:false,source_electron:native});
  }catch(error){latePrimary=error;throw error;}finally{try{await late.close();}catch(error){if(!latePrimary)throw error;e.note('queue_handoff_secondary_route_cleanup',{type:error instanceof Error?error.name:'unknown'});}}
 }catch(error){primary=error;throw error;}finally{page.off('request',observe);void primary;}
}
test('created saved queue hands off A B A with exact controlled report cursor and original inputs',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);await queueCreateProjectHandoff(page,workspace,evidence,false,renderer.origin,renderer.url);
});
test('native created saved queue fences old A catalog and preserves exact reopened queue',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();await queueCreateProjectHandoff(page,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);
});

import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const queuePath='/api/team-data/queue';
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const filterOf=(request:Request)=>new URL(request.url()).searchParams.get('assignee');
const isQueue=(request:Request)=>request.method()==='GET'&&new URL(request.url()).pathname===queuePath;
test.use({actionTimeout:10_000});

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const known='controlled-queue-assignee',missing='controlled-no-assigned-images';
 const project=await api('/api/project/create',{name:'Owned team queue controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 const imported=await api('/api/dataset/metadata?limit=10');expect(imported.items).toHaveLength(2);
 await api('/api/team-data');
 const initial=await api(queuePath+'?offset=0&limit=30');
 const image=initial.items.find((row:any)=>row.file_path===workspace.images[0].path);expect(image).toBeTruthy();
 await api('/api/team-data/images/'+image.image_uuid+'/assign',{expected_revision:image.revision,actor:'controlled-fixture-owner',assignee:known,priority:70});
 // These are controlled task assignments, not annotations or human review truth.
 // Stabilize lazy metadata registration before comparing subsequent queue reads.
 await api('/api/team-data/readiness');
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 const open=async()=>{
  await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
  await expect(dialog.getByLabel('작업 목록 담당 필터',{exact:true})).toBeVisible();return dialog;
 };
 let dialog=await open();
 const list=()=>dialog.getByRole('region',{name:'팀 작업 목록',exact:true});
 const field=()=>dialog.getByLabel('작업 목록 담당 필터',{exact:true});
 await expect(list()).toContainText('총 2장');
 const baseline=await api(queuePath+'?offset=0&limit=30');expect(baseline.total).toBe(2);
 const assigned=await api(queuePath+'?assignee='+known+'&offset=0&limit=30');
 expect(assigned.total).toBe(1);expect(assigned.items[0].image_uuid).toBe(image.image_uuid);
 for(const row of baseline.items){expect(row.annotation_hash).toBeNull();expect(row.mask_hash).toBeNull();}
 const mutations:Array<{method:string;pathname:string}>=[];
 const observe=(request:Request)=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&(pathname.startsWith('/api/team-data')||pathname.startsWith('/api/annotations')||pathname.startsWith('/api/training')))mutations.push({method:request.method(),pathname});};
 page.on('request',observe);
 let release:()=>void=()=>{},holdHandler:((route:Route)=>Promise<void>)|undefined;
 const errorHandler=async(route:Route)=>{
  if(!isQueue(route.request())||filterOf(route.request())!==known)return route.continue();
  await route.fulfill({status:503,json:{detail:'Controlled team queue transport failure'}});
 };
 const empty=async()=>{
  await expect(field()).toHaveValue(missing);await expect(list()).toContainText('총 0장');
  await expect(list().locator('ol > li')).toHaveCount(0);await expect(list().getByText('0건',{exact:true})).toBeVisible();
  await expect(list().getByRole('button',{name:'다음',exact:true})).toBeDisabled();
 };
 const realReply=(assignee:string)=>page.waitForResponse(r=>isQueue(r.request())&&filterOf(r.request())===assignee);
 let removeDispositionObservers:()=>void=()=>{};
 try{
  const emptyReply=realReply(missing);await field().fill(missing);
  const response=await emptyReply;expect(response.status()).toBe(200);const emptyQueue=await response.json();
  expect(emptyQueue).toEqual({items:[],total:0,offset:0,limit:30});await response.finished();await empty();
  await list().scrollIntoViewIfNeeded();await expect(list()).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-actual-empty-assignee-queue`);

  // This is a controlled HTTP transport refusal, not a fabricated server state.
  await page.route('**/api/team-data/queue?*',errorHandler);
  const refusedReply=realReply(known);await field().fill(known);const refused=await refusedReply;
  expect(refused.status()).toBe(503);expect(await refused.json()).toEqual({detail:'Controlled team queue transport failure'});
  const alert=dialog.getByRole('alert').filter({hasText:'Controlled team queue transport failure'});
  await expect(alert).toBeVisible();await alert.scrollIntoViewIfNeeded();await expect(alert).toBeInViewport();
  expect(await api(queuePath+'?offset=0&limit=30')).toEqual(baseline);
  await evidence.screenshot(page,`${native?'native':'browser'}-controlled-queue-503-original-records-preserved`);
  await page.unroute('**/api/team-data/queue?*',errorHandler);
  const retryReply=realReply(known);await dialog.getByRole('button',{name:'새로고침',exact:true}).click();
  const retry=await retryReply;expect(retry.status()).toBe(200);expect(await retry.json()).toEqual(assigned);await retry.finished();
  await expect(dialog.getByRole('alert')).toHaveCount(0);await expect(list()).toContainText('총 1장');
  await expect(list().getByRole('button',{name:assigned.items[0].relative_path,exact:true})).toBeVisible();
  await list().scrollIntoViewIfNeeded();await expect(list()).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-explicit-queue-refresh-actual-pinned-row`);

  // First restore a real empty queue, then hold the old known-assignee read.
  const resetReply=realReply(missing);await field().fill(missing);expect((await resetReply).status()).toBe(200);await empty();
  let reached!:()=>void,finished!:()=>void;
  const held=new Promise<void>(r=>reached=r),handled=new Promise<void>(r=>finished=r),released=new Promise<void>(r=>release=r);
  let heldRequest:Request|undefined,captured:any,holdError:unknown,armed=true;
  holdHandler=async route=>{
   if(!armed||!isQueue(route.request())||filterOf(route.request())!==known){await route.continue();return;}
   armed=false;heldRequest=route.request();
   try{
    if(native){
     // Playwright route.fetch is outside Electron's frame token injection.
     // A separate authenticated renderer GET supplies a pinned state snapshot;
     // the original UI200 is explicitly controlled and has no HTTP200 provenance.
     captured=await api(queuePath+'?assignee='+known+'&offset=0&limit=30');reached();await released;
     await route.fulfill({status:200,json:captured});
    }else{
     const fetched=await route.fetch();expect(fetched.status()).toBe(200);captured=await fetched.json();reached();await released;
     await route.fulfill({response:fetched});
    }
   }catch(cause){holdError=cause;reached();}finally{finished();}
  };
  await page.route('**/api/team-data/queue?*',holdHandler);
  await field().fill(known);await held;if(holdError)throw holdError;
  expect(captured).toEqual(assigned);expect(heldRequest).toBeTruthy();
  let dispositionResolve!:(value:{kind:'finished'|'failed';failure:string|null})=>void;
  const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>dispositionResolve=r);
  const requestFinished=(request:Request)=>{if(request===heldRequest)dispositionResolve({kind:'finished',failure:null});};
  const requestFailed=(request:Request)=>{if(request===heldRequest)dispositionResolve({kind:'failed',failure:request.failure()?.errorText??null});};
  page.on('requestfinished',requestFinished);page.on('requestfailed',requestFailed);
  removeDispositionObservers=()=>{page.off('requestfinished',requestFinished);page.off('requestfailed',requestFailed);};
  // Closing is a user view action. Changing the reopened filter supersedes the
  // old read; HTTP cancellation is measured below rather than assumed.
  await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
  await expect(dialog).toHaveCount(0);dialog=await open();await expect(field()).toHaveValue(known);
  const currentReply=realReply(missing);await field().fill(missing);
  const current=await currentReply;expect(current.status()).toBe(200);expect(await current.json()).toEqual(emptyQueue);await current.finished();await empty();
  release();await handled;
  let timer:ReturnType<typeof setTimeout>|undefined;
  const outcome=await Promise.race([disposition,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Held exact queue Request did not finish or fail')),10_000);})]).finally(()=>{if(timer)clearTimeout(timer);});
  removeDispositionObservers();
  if(outcome.kind==='finished'){
   if(holdError)throw holdError;const delivered=await heldRequest!.response();expect(delivered).not.toBeNull();
   expect(delivered!.status()).toBe(200);expect(await delivered!.json()).toEqual(captured);expect(await delivered!.finished()).toBeNull();
  }else{
   // A failed request is cancellation evidence only with Chromium's explicit
   // aborted disposition. Other transport failures fail this case.
   expect(outcome.failure).toContain('ERR_ABORTED');
  }
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await page.unroute('**/api/team-data/queue?*',holdHandler);holdHandler=undefined;
  await empty();await expect(list()).not.toContainText(assigned.items[0].relative_path);
  await list().scrollIntoViewIfNeeded();await expect(list()).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-closed-reopened-current-empty-queue-after-old-read`);
  expect(await api(queuePath+'?offset=0&limit=30')).toEqual(baseline);expect(mutations).toEqual([]);
  for(const original of workspace.images){expect(sha(original.path)).toBe(original.sha256);evidence.addFile(original.path);}
  evidence.note('team_queue_controls',{action_id:'F024',action:'queue-filter',dimensions:['empty','error','cancel'],project_id:project.id,baseline,assigned,actual_empty_queue:emptyQueue,error:{controlled_http_status:503,actual_server_error:false,stored_records_preserved:true},retry:{explicit_refresh_click:true,actual_http_status:200,exact_queue_row:true},cancel:{explicit_dialog_close:true,actual_reopen:true,superseded_assignee:known,current_assignee:missing,exact_request_disposition:outcome,readonly_http_request_aborted:outcome.kind==='failed',original_reply_delivered:outcome.kind==='finished',browser_original_delayed_http200_provenance:!native&&outcome.kind==='finished',native_original_delayed_http200_provenance:false,controlled_original_ui_reply:native,snapshot_transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response',captured_queue:captured,late_old_read_did_not_repaint:true},ui_mutations:mutations,source_images:workspace.images,controlled_assignments_not_human_truth:true,source_ui:true,source_electron:native,actual_model_inference:false,model_quality_accepted:false,independent_feature_acceptance:false,gpu_used:false,windows_excluded:true});
 }finally{
  release();removeDispositionObservers();page.off('request',observe);
  if(!page.isClosed()){await page.unroute('**/api/team-data/queue?*',errorHandler);if(holdHandler)await page.unroute('**/api/team-data/queue?*',holdHandler);}
 }
}

test('team queue filter shows actual empty and preserves current read across refusal cancel and reopen',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native team queue filter keeps current empty state after refusal cancelled old read and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(`Owned queue fixture HTTP ${response.status}`);return response.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

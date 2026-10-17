import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Locator} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffApi} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';
import {hash,ownedFile,ownedTree,within,matches,originalBody,holdOwningAbort,type Custody} from './fixtures/owningCancelControl';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
async function exercise(page:Page,w:Workspace,e:Evidence,native:boolean,origin:string,url?:string){
 const api=handoffApi(page,origin,native),c:Custody={opened:0,closed:0,uncertain:[]};
 const source=path.join(w.root,'u016-owning-request-cancel-source');fs.mkdirSync(source);
 for(const [i,n]of ['error','disagreement','threshold'].entries())fs.writeFileSync(path.join(source,n+'.png'),png(64,3,(x,y)=>[x,y,50+i]),{flag:'wx'});
 const created=await api('/api/project/create',{name:'U016 original current and origin GET cancellation',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const project=await api('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);expect(path.dirname(project.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));expect(fixture.controlled_reports_not_model_inference).toBe(true);expect(ownedFile(fixture.path,c).sha256).toBe(fixture.sha256);
 const queue=await api('/api/data-workbench/review-queues',{evaluation_id:fixture.record.evaluation_id,threshold:.5,margin:.05});expect(queue).toMatchObject({cursor:0,revision:1,history:[],origin:{evaluation_id:fixture.record.evaluation_id}});
 const queuePath='/api/data-workbench/review-queues/'+queue.id,q='?'+new URLSearchParams({source_dataset_path:source,task:'segmentation'});
 for(const r of ['/api/project/preferences','/api/project/labelsets','/api/team-data','/api/team-data/readiness','/api/model-deployments/active'+q,'/api/model-deployments/history'+q,'/api/training/jobs'])await api(r);
 const metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(3);
 for(const r of metadata)await api('/api/annotations/'+path.basename(r.file_path,path.extname(r.file_path))+'?file_path='+encodeURIComponent(r.file_path));
 const panel=page.getByRole('region',{name:'저장된 검토 큐',exact:true}),choice=panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true});
 const current=panel.getByRole('button',{name:'현재 항목 열기',exact:true}),returnOrigin=panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true}),next=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true});
 const enter=async()=>{await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();const summary=page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await expect(panel).toBeVisible();await expect(choice).toHaveValue(queue.id);};
 const retained=()=>({source:ownedTree(source,c),dataset:ownedTree(project.dataset_dir,c),annotations:ownedTree(project.annotations_dir,c),models:ownedTree(project.models_dir,c),reports:ownedTree(project.reports_dir,c)});
 const durable=async()=>({queue:await api(queuePath),list:await api('/api/data-workbench/review-queues'),jobs:await api('/api/training/jobs'),deployment:await api('/api/model-deployments/active'+q),deployment_history:await api('/api/model-deployments/history'+q)});
 const selection=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).filter(([k])=>k.startsWith('modu-review-queue:')||k.startsWith('modu-evaluation-return:')||k.startsWith('modu-comparison-return:'))));
 const bodies:any[]=[],proofs:Record<string,any>={},writes:any[]=[],pageErrors:string[]=[],controllers:Array<{close:()=>Promise<void>}>=[];
 const observe=(r:Request)=>{const u=new URL(r.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:u.pathname,body:r.postData()});};const onError=(err:Error)=>pageErrors.push(String(err));
 const originalGet=async(button:Locator,label:string)=>{const deadline=performance.now()+10_000;const wait=page.waitForResponse(r=>matches(r.request(),page,origin,queuePath,''),{timeout:10_000});void wait.catch(()=>{});await within(button.click(),deadline,label+' UI click');const body=await originalBody(await within(wait,deadline,label+' original GET'),page,w,e,origin,project.id,label,deadline);bodies.push(body.proof);return body;};
 let primary:unknown,before:any,beforeDurable:any,beforeSelection:any;
 try{
  if(url)await page.goto(url);else await page.reload();await enter();await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();
  const initialized=await originalGet(current,'u016-cancel-initial-original-queue');expect(initialized.body).toEqual(queue);await expect(next).toBeEnabled();
  before=retained();beforeDurable=await durable();beforeSelection=await selection();expect(beforeDurable.jobs.jobs).toEqual([]);expect(beforeDurable.queue).toEqual(queue);await e.screenshot(page,'u016-cancel-original-ready');
  page.on('request',observe);page.on('pageerror',onError);
  for(const [button,label,cell]of [[current,'current-open','U016.saved-review-current-open.cancel'],[returnOrigin,'origin-return','U016.saved-review-origin-return.cancel']]as const){
   const control=await holdOwningAbort(page,origin,queuePath,'',project.id);controllers.push(control);const deadline=performance.now()+10_000;let failure:any,first:unknown;
   try{
    await within(button.click(),deadline,label+' genuine UI dispatch');await within(control.ready(),deadline,label+' original request held');await expect(panel.getByRole('status')).toHaveText('검토 큐 처리 중…');await expect(current).toBeDisabled();await expect(returnOrigin).toBeDisabled();
    failure=await within(control.finish(),deadline,label+' genuine owning abort');await expect(panel.getByRole('status')).toHaveCount(0);await expect(panel.getByRole('alert')).toContainText(/abort|failed to fetch|network|fetch failed/i);await expect(current).toBeEnabled();await expect(returnOrigin).toBeEnabled();await expect(panel).toBeVisible();await expect(choice).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 0 / 3');expect(await selection()).toEqual(beforeSelection);expect(retained()).toEqual(before);expect(writes).toEqual([]);await e.screenshot(page,'u016-'+label+'-actual-owning-abort-preserved');
   }catch(error){first=error;}finally{try{await control.close();controllers.splice(controllers.indexOf(control),1);}catch(error){first??=error;}}
   if(first!==undefined)throw first;expect(await durable()).toEqual(beforeDurable);
   const recovered=await originalGet(current,'u016-'+label+'-fresh-original-recovery');expect(recovered.bytes).toEqual(initialized.bytes);expect(recovered.body).toEqual(queue);await expect(panel.getByRole('alert')).toHaveCount(0);await expect(next).toBeEnabled();expect(await durable()).toEqual(beforeDurable);expect(await selection()).toEqual(beforeSelection);expect(retained()).toEqual(before);expect(writes).toEqual([]);await e.screenshot(page,'u016-'+label+'-fresh-original-200-recovery');
   proofs[cell]={owning_UI_click:true,owning_original_GET_dispatched:true,abort:failure,queue_cursor_revision_history_unchanged:true,source_annotation_model_report_full_raw_unchanged:true,same_project_queue_source_scope:true,origin_navigation_not_committed:true,origin_localStorage_unchanged:true,original_recovery_full_bytes_equal:true,recovery:recovered.proof,normal_current_open_recovery_not_origin_return_success_credit:true};
  }
  expect(pageErrors).toEqual([]);expect(retained()).toEqual(before);expect(await durable()).toEqual(beforeDurable);expect(await selection()).toEqual(beforeSelection);
  const proof={schema:'modu-vision.u016-owning-GET-abort-cancel-proof/v1',native,project,source,fixture,queue,cells:proofs,original_bodies:bodies,retained_before:before,retained_after:retained(),durable_before:beforeDurable,durable_after:await durable(),selection_before:beforeSelection,selection_after:await selection(),writes,page_errors:pageErrors,transport_control_disclosed:true,user_cancel_button_claim:false,response_fabricated:false,timeout_as_pass:false,source_navigation_abandonment:false,model_training:false,model_deployment:false,installed_native_accepted:false,quality_approved:false};const file=path.join(w.logs,'u016-owning-GET-abort-cancel-proof.json');fs.writeFileSync(file,JSON.stringify(proof,null,2)+'\n',{flag:'wx'});e.addFile(file);e.note('u016_owning_GET_abort_cancel',proof);
 }catch(error){primary=error;}finally{
  for(const control of controllers)try{await control.close();}catch(error){primary??=error;}
  page.off('request',observe);page.off('pageerror',onError);
  if(before)try{expect(retained()).toEqual(before);expect(await durable()).toEqual(beforeDurable);expect(await selection()).toEqual(beforeSelection);}catch(error){primary??=error;}
  try{expect(c.opened).toBe(c.closed);expect(c.uncertain).toEqual([]);}catch(error){primary??=error;}
  e.note('u016_owning_GET_abort_cancel_closure',{opened:c.opened,closed:c.closed,uncertain:c.uncertain,owning_routes_joined_and_removed:controllers.length===0,first_exception:primary===undefined?null:String(primary),source_electron_fixture_teardown_required:true});
 }
 if(primary!==undefined)throw primary;
}
test('saved-review current and origin UI requests genuinely abort and preserve owning saved records',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,false,renderer.origin,renderer.url);});
test('native saved-review current and origin UI requests genuinely abort and preserve owning saved records',{tag:'@electron'},async({electronSession,workspace,evidence})=>{expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS).toBe('1');const backend=await electronSession.waitForBackend();await exercise(electronSession.window,workspace,evidence,true,`http://127.0.0.1:${backend.port}`);});

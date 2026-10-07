import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// Assigning disposable synthetic work is a metadata action. No annotation,
// model truth, review vote, training or production account is changed here.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Owned assignment controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 await api('/api/team-data');await api('/api/team-data/readiness');
 const rows=await api('/api/team-data/queue?offset=0&limit=30');expect(rows.items).toHaveLength(2);
 const image=rows.items.find((row:any)=>row.file_path===workspace.images[0].path);expect(image).toBeTruthy();
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
 const current=dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true});
 const queue=dialog.getByRole('region',{name:'팀 작업 목록',exact:true});
 const endpoint='/api/team-data/images/'+image.image_uuid+'/assign';
 const mutations:Array<{method:string;pathname:string}>=[];
 const observe=(request:any)=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&(pathname.startsWith('/api/team-data')||pathname.startsWith('/api/annotations')||pathname.startsWith('/api/training')))mutations.push({method:request.method(),pathname});};
 page.on('request',observe);
 const transport=async(route:Route)=>{
  if(route.request().method()==='POST'&&new URL(route.request().url()).pathname===endpoint)await route.fulfill({status:503,json:{detail:'Controlled assignment POST transport failure'}});
  else await route.continue();
 };
 try{
  await queue.getByRole('button',{name:image.relative_path,exact:true}).click();
  await expect(current).toContainText(image.relative_path);
  const actor=dialog.getByLabel('팀 작업자 이름',{exact:true}),apply=current.getByRole('button',{name:'작업 배정',exact:true});
  await actor.fill('');await expect(apply).toBeDisabled();expect(mutations).toEqual([]);
  const emptyBaseline=(await api('/api/team-data/images/'+image.image_uuid)).image;
  await current.scrollIntoViewIfNeeded();await expect(apply).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-empty-actor-disabled-no-command`);
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(emptyBaseline);
  await actor.fill('controlled-assignment-manager');await current.getByLabel('이미지 담당자',{exact:true}).fill('controlled-assignment-target');
  await current.getByLabel('작업 우선순위',{exact:true}).fill('151');
  const baseline=(await api('/api/team-data/images/'+image.image_uuid)).image;
  expect(baseline.annotation_hash).toBeNull();expect(baseline.mask_hash).toBeNull();
  const invalidReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const invalid=await invalidReply;expect(invalid.status()).toBe(422);const invalidBody=await invalid.json();
  expect(invalidBody.detail).toEqual(expect.arrayContaining([expect.objectContaining({loc:['body','priority'],input:151,ctx:{le:100}})]));
  const invalidAlert=dialog.getByRole('alert');await expect(invalidAlert).toBeVisible();await expect(invalidAlert).toContainText('priority');await expect(invalidAlert).toContainText('100');
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(baseline);
  await invalidAlert.scrollIntoViewIfNeeded();await expect(invalidAlert).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-priority-actual-422-no-record-write`);
  await current.getByLabel('작업 우선순위',{exact:true}).fill('60');
  await page.route('**/api/team-data/images/*/assign',transport);
  const refusedReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const refused=await refusedReply;expect(refused.status()).toBe(503);
  const refusedBody=await refused.json();expect(refusedBody).toEqual({detail:'Controlled assignment POST transport failure'});
  const error=dialog.getByRole('alert').filter({hasText:'Controlled assignment POST transport failure'});await expect(error).toBeVisible();
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(baseline);
  await error.scrollIntoViewIfNeeded();await expect(error).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-valid-post-controlled-503-record-preserved`);
  await page.unroute('**/api/team-data/images/*/assign',transport);
  const retryReply=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===endpoint);
  await apply.click();const retry=await retryReply;expect(retry.status()).toBe(200);const assigned=(await retry.json()).image;
  await expect(dialog.getByRole('alert')).toHaveCount(0);
  expect(assigned.image_uuid).toBe(image.image_uuid);expect(assigned.revision).toBe(baseline.revision+1);
  expect(assigned.team.assignment.assignee).toBe('controlled-assignment-target');expect(assigned.team.assignment.priority).toBe(60);
  expect(assigned.annotation_hash).toBe(baseline.annotation_hash);expect(assigned.mask_hash).toBe(baseline.mask_hash);
  expect((await api('/api/team-data/images/'+image.image_uuid)).image).toEqual(assigned);
  await expect(queue).toContainText('담당 controlled-assignment-target');await expect(queue).toContainText('우선 60');
  expect(mutations).toEqual([{method:'POST',pathname:endpoint},{method:'POST',pathname:endpoint},{method:'POST',pathname:endpoint}]);
  await current.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-assignment-explicit-real-retry-one-revision`);
  for(const file of workspace.images)expect(sha(file.path)).toBe(file.sha256);
  evidence.note('assignment_controls',{record_id:'F024',action:'assign',dimensions:['empty','invalid','error'],project_id:project.id,image_uuid:image.image_uuid,baseline,assigned,empty:{blank_actor_disabled:true,no_assign_command_dispatched:true,selected_image_record_preserved:true},invalid:{priority:151,status:422,response:invalidBody,record_unchanged:true},error:{valid_priority:60,controlled_exact_POST_status:503,original_POST_not_dispatched:true,record_unchanged:true,explicit_real_retry_status:200,one_revision_increment:true},ui_mutations:mutations,source_images:workspace.images,controlled_assignments_not_human_truth:true,source_ui:true,source_electron:native,annotation_or_review_write:false,actual_model_inference:false,quality_accepted:false,installed_target_verified:false,gpu_used:false,windows_excluded:true});
 }finally{page.off('request',observe);if(!page.isClosed())await page.unroute('**/api/team-data/images/*/assign',transport);}
}
test('assignment empty invalid priority and exact POST failure preserve records before real retry',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native assignment empty invalid and exact POST failure preserve annotations before real retry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(`Owned assignment HTTP ${response.status}`);return response.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

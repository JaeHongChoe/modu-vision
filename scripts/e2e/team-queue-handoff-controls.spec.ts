import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const queuePath='/api/team-data/queue';
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const pathname=(request:Request)=>new URL(request.url()).pathname;
test.use({actionTimeout:10_000});

// Only same-context dialog state is retained. The product has no invalid
// assignee input or durable queue-filter persistence contract.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const assignee='controlled-queue-handoff-owner',state='pending';
 const project=await api('/api/project/create',{name:'Owned queue reopen and exact handoff',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 await api('/api/team-data');await api('/api/team-data/readiness');
 const initial=await api(queuePath+'?offset=0&limit=30');expect(initial.items).toHaveLength(2);
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 const focus=page.getByRole('button',{name:'집중 편집',exact:true});
 await expect(focus).toHaveAttribute('aria-pressed','false');await focus.click();
 await expect(focus).toHaveAttribute('aria-pressed','true');
 const open=async()=>{
  await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();
  const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
  await expect(dialog.getByRole('region',{name:'팀 작업 목록',exact:true})).toBeVisible();return dialog;
 };
 let dialog=await open();
 const current=()=>dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true});
 const list=()=>dialog.getByRole('region',{name:'팀 작업 목록',exact:true});
 const actor=()=>dialog.getByLabel('작업 목록 담당 필터',{exact:true});
 const status=()=>dialog.getByLabel('작업 목록 상태',{exact:true});
 await expect(current().locator('p').first()).toHaveText(/^(ng|ok)\/sample-(ng|ok)\.png$/);
 const previousPath=await current().locator('p').first().innerText();
 const target=initial.items.find((row:any)=>row.relative_path!==previousPath);expect(target).toBeTruthy();
 // Controlled fixture assignment precedes the observed UI contract. No label,
 // review vote or human truth is supplied by this setup.
 await api('/api/team-data/images/'+target.image_uuid+'/assign',{
  expected_revision:target.revision,actor:'controlled-fixture-owner',assignee,priority:75,
 });
 await api('/api/team-data/readiness');
 const baseline=await api(queuePath+'?offset=0&limit=30');
 const filtered=await api(queuePath+'?assignee='+assignee+'&state='+state+'&offset=0&limit=30');
 expect(filtered.total).toBe(1);expect(filtered.items).toHaveLength(1);
 const selected=filtered.items[0];expect(selected.image_uuid).toBe(target.image_uuid);
 expect(selected.file_path).toBe(target.file_path);expect(selected.relative_path).not.toBe(previousPath);
 const source=await api('/api/project/current');expect(source.id).toBe(project.id);
 expect(source.source_dataset_dir).toBe(workspace.dataset);
 for(const row of baseline.items){expect(row.annotation_hash).toBeNull();expect(row.mask_hash).toBeNull();}
 const mutations:Array<{method:string;pathname:string}>=[];
 const observe=(request:Request)=>{
  const path=pathname(request);
  if(request.method()!=='GET'&&(path.startsWith('/api/team-data')||path.startsWith('/api/annotations')||path.startsWith('/api/training')))
   mutations.push({method:request.method(),pathname:path});
 };
 page.on('request',observe);
 const exactQueue=(request:Request)=>{
  const query=new URL(request.url()).searchParams;
  return request.method()==='GET'&&pathname(request)===queuePath&&query.get('assignee')===assignee
   &&query.get('state')===state&&query.get('offset')==='0'&&query.get('limit')==='30';
 };
 const assertFiltered=async()=>{
  await expect(actor()).toHaveValue(assignee);await expect(status()).toHaveValue(state);
  await expect(list()).toContainText('총 1장');await expect(list().locator('ol > li')).toHaveCount(1);
  await expect(list().getByRole('button',{name:selected.relative_path,exact:true})).toBeVisible();
  await expect(list()).toContainText('담당 '+assignee);await expect(list()).toContainText('우선 75');
  await expect(list().getByRole('button',{name:'이전',exact:true})).toBeDisabled();
  await expect(list().getByRole('button',{name:'다음',exact:true})).toBeDisabled();
  await expect(list().getByText('1–1',{exact:true})).toBeVisible();
 };
 try{
  await actor().fill(assignee);
  const selectedReply=page.waitForResponse(response=>exactQueue(response.request()));
  await status().selectOption(state);const applied=await selectedReply;
  expect(applied.status()).toBe(200);expect(await applied.json()).toEqual(filtered);expect(await applied.finished()).toBeNull();
  await assertFiltered();await expect(current()).toContainText(previousPath);
  await list().scrollIntoViewIfNeeded();await expect(list()).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-actual-filtered-queue-before-dialog-close`);
  await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
  await expect(dialog).toHaveCount(0);dialog=await open();
  await expect(dialog.getByRole('tab',{name:'작업·검수',exact:true})).toHaveAttribute('aria-selected','true');
  await assertFiltered();await expect(current()).toContainText(previousPath);
  // Refresh after reopening proves the retained controls address the same real
  // backend rows. This is not persisted filter state after an application reload.
  const refreshReply=page.waitForResponse(response=>exactQueue(response.request()));
  await dialog.getByRole('button',{name:'새로고침',exact:true}).click();const refreshed=await refreshReply;
  expect(refreshed.status()).toBe(200);expect(await refreshed.json()).toEqual(filtered);expect(await refreshed.finished()).toBeNull();
  await assertFiltered();await list().scrollIntoViewIfNeeded();await expect(list()).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-reopened-retained-filters-real-readback`);

  const stem=selected.relative_path.split(/[\\/]/).pop()!.replace(/\.[^.]+$/,'');
  const annotationReply=page.waitForResponse(response=>{
   const request=response.request(),address=new URL(request.url());
   return request.method()==='GET'&&address.pathname==='/api/annotations/'+encodeURIComponent(stem)
    &&address.searchParams.get('file_path')===selected.file_path;
  });
  await list().getByRole('button',{name:selected.relative_path,exact:true}).click();
  const handoff=await annotationReply;expect(handoff.status()).toBe(200);const annotation=await handoff.json();
  expect(await handoff.finished()).toBeNull();expect(annotation.annotations).toEqual([]);
  expect(annotation.metadata).toEqual(selected);expect(annotation.image_id).toBe(stem);
  await expect(current().locator('p').first()).toHaveText(selected.relative_path);
  await expect(current()).not.toContainText(previousPath);
  // The real queue does not close itself after selection. Close explicitly to
  // inspect the labeling pane rather than treating the click as proof of load.
  await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.locator('[data-labeling-filmstrip]')).toContainText(selected.relative_path.split('/').pop()!);
  await expect(page.getByRole('alert').filter({hasText:'기존 라벨 조회 실패'})).toHaveCount(0);
  await expect(page.getByText('기존 라벨을 불러오는 중입니다.',{exact:true})).toHaveCount(0);
  await focus.click();await expect(focus).toHaveAttribute('aria-pressed','false');
  await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
  const info=page.getByRole('region',{name:'이미지 정보와 검토 기록',exact:true});
  await info.locator('summary').filter({hasText:'출처·변경 기록'}).click();
  const provenance=info.locator('dl');
  await expect(provenance.locator('dd').nth(0)).toHaveText(selected.image_uuid);
  await expect(provenance.locator('dd').nth(1)).toHaveText(selected.file_path);
  await expect(provenance.locator('dd').nth(2)).toHaveText(selected.content_hash);
  await expect(provenance).toContainText('수정 '+selected.revision);
  await provenance.scrollIntoViewIfNeeded();await expect(provenance).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-queue-handoff-exact-uuid-source-hash-labeling-pane`);
  expect(await api(queuePath+'?offset=0&limit=30')).toEqual(baseline);
  expect((await api('/api/project/current')).source_dataset_dir).toBe(source.source_dataset_dir);
  expect((await api('/api/team-data/images/'+selected.image_uuid)).image).toEqual(selected);
  expect(mutations).toEqual([]);
  for(const image of workspace.images){expect(sha(image.path)).toBe(image.sha256);evidence.addFile(image.path);}
  evidence.note('team_queue_handoff_controls',{
   action_id:'F024',action:'queue-filter',dimensions:['reopen','handoff'],project_id:project.id,
   reopen:{same_mounted_project_context:true,explicit_dialog_close:true,retained_assignee:assignee,
    retained_state:state,retained_offset:0,retained_tab:'work',explicit_refresh:true,actual_status:200,
    exact_filtered_queue:filtered,durable_filter_persistence_claimed:false},
   handoff:{previous_relative_path:previousPath,selected_queue_row:selected,actual_annotations_status:200,
    annotation_response:annotation,visible_image_uuid:selected.image_uuid,visible_source_path:selected.file_path,
    visible_content_hash:selected.content_hash,explicit_dialog_close:true,actual_labeling_metadata_verified:true},
   invalid:{qualified:false,reason:'Free-text assignee accepts arbitrary strings; state select and paging expose only valid requests. No invalid filter mapping fabricated.'},
   baseline,ui_mutations:mutations,source_images:workspace.images,source_directory_preserved:true,
   controlled_assignment_not_human_truth:true,source_ui:true,source_electron:native,annotation_or_review_write:false,
   actual_model_inference:false,model_quality_accepted:false,installed_target_verified:false,gpu_used:false,windows_excluded:true,
  });
 }finally{page.off('request',observe);}
}

test('team queue filter retains same-context reopened controls and hands the exact image to labeling',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native team queue filter reopens retained controls and hands exact UUID source and hash to labeling',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!response.ok)throw Error(`Owned queue handoff fixture HTTP ${response.status}`);return response.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

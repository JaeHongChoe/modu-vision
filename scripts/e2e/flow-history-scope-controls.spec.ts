import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(value:Buffer|string)=>crypto.createHash('sha256').update(value).digest('hex');
const canonical=(value:any):any=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'
 ?Object.fromEntries(Object.keys(value).sort().map(key=>[key,canonical(value[key])])):value;
const graphSha=(graph:any)=>sha(JSON.stringify(canonical(graph)));

function draftFile(projectDir:string,root:string):string {
 expect(path.relative(root,projectDir).startsWith('..')).toBe(false);
 const files:string[]=[];
 const walk=(directory:string)=>{for(const row of fs.readdirSync(directory,{withFileTypes:true})){
  const child=path.join(directory,row.name);expect(row.isSymbolicLink()).toBe(false);
  if(row.isDirectory())walk(child);else if(row.name==='draft.json')files.push(child);
 }};
 walk(path.join(projectDir,'flowcharts','drafts'));expect(files).toHaveLength(1);return files[0];
}

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string) {
 const prepare=async(tag:'A'|'B')=>{
  await api('/api/project/create',{name:`History scope ${tag}`,task:'anomaly'});
  await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  const project=await api('/api/project/current');
  const pipeline={id:`history-${tag}`,name:`Scoped graph ${tag}`,nodes:[
   {id:`input-${tag}`,position:{x:32,y:170},data:{label:`Input ${tag}`,node_type:'input'}},
   {id:`roi-${tag}`,position:{x:332,y:170},data:{label:`ROI ${tag}`,node_type:'fixed_roi',params:{roi_bbox:[0,0,tag==='A'?16:32,16]}}},
   {id:`decision-${tag}`,position:{x:632,y:170},data:{label:`Decision ${tag}`,node_type:'decision',rule:'any_defect_is_ng'}},
   {id:`output-${tag}`,position:{x:932,y:170},data:{label:`Output ${tag}`,node_type:'output'}}],edges:[
   {id:`input-roi-${tag}`,source:`input-${tag}`,target:`roi-${tag}`,payload_type:'image'},
   {id:`roi-decision-${tag}`,source:`roi-${tag}`,target:`decision-${tag}`,payload_type:'roi'},
   {id:`decision-output-${tag}`,source:`decision-${tag}`,target:`output-${tag}`,payload_type:'result'}]};
  const saved=await api('/api/flowchart/draft',{pipeline,context:{project_id:project.id,
   source_dataset_path:workspace.dataset,labelset_id:project.active_labelset_id||'default'},base_version_id:'none'},'PUT');
  expect(saved.draft_sha256).toBe(graphSha(saved.pipeline));
  const file=draftFile(project.project_dir,workspace.root);
  return {tag,project,record:saved,file,raw_sha256:sha(fs.readFileSync(file)),
   project_file:path.join(project.project_dir,'project.json'),project_sha256:sha(fs.readFileSync(path.join(project.project_dir,'project.json')))};
 };
 const A=await prepare('A'),B=await prepare('B');
 expect(A.record.draft_sha256).not.toBe(B.record.draft_sha256);
 await api('/api/project/open',{project_dir:A.project.project_dir});
 const images=workspace.images.map(row=>({...row,observed_sha256:sha(fs.readFileSync(row.path))}));
 if(url)await page.goto(url);else await page.reload();
 const stages=page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button');
 const openFlow=async()=>{await stages.nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();};
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(A.project.name);
 await openFlow();
 const undo=()=>page.getByRole('button',{name:'플로우 실행 취소',exact:true});
 const redo=()=>page.getByRole('button',{name:'플로우 다시 실행',exact:true});
 const view=()=>page.evaluate(()=>({nodes:[...document.querySelectorAll<HTMLElement>('[data-flow-node-id]')]
  .map(row=>({id:row.dataset.flowNodeId,label:row.querySelector('h4')?.textContent,x:parseFloat(row.style.left),y:parseFloat(row.style.top)})),
  edges:[...document.querySelectorAll<SVGElement>('path[data-flow-edge]')].map(row=>({id:row.dataset.flowEdge,
   source:row.dataset.flowFrom?.split(':')[0],target:row.dataset.flowTo?.split(':')[0]}))}));
 const expectedView=(record:any)=>({nodes:record.pipeline.nodes.map((row:any)=>({id:row.id,label:row.data.label,x:row.position.x,y:row.position.y})),
  edges:record.pipeline.edges.map((row:any)=>({id:row.id,source:row.source,target:row.target}))});
 const matches=async(record:any)=>expect.poll(view).toEqual(expectedView(record));
 await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);await matches(A.record);
 let phase='empty';const writes:Array<{phase:string;method:string;path:string;body:string|null}>=[];
 const observe=(request:any)=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/')&&!['GET','HEAD'].includes(request.method()))
  writes.push({phase,method:request.method(),path:url.pathname,body:request.postData()});};
 page.on('request',observe);
 const phases:any[]=[],handoffSummaries:any[]=[];
 const readback=async(label:string,current= A)=>{
  const saved=await api('/api/flowchart/draft');expect(saved.draft_sha256).toBe(current.record.draft_sha256);expect(saved.pipeline).toEqual(current.record.pipeline);
  for(const scope of [A,B]){expect(sha(fs.readFileSync(scope.file))).toBe(scope.raw_sha256);
   expect(sha(fs.readFileSync(scope.project_file))).toBe(scope.project_sha256);
   expect(fs.existsSync(path.join(scope.project.project_dir,'flowcharts','active.json'))).toBe(false);}
  for(const row of images)expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);
  phases.push({label,project_id:current.project.id,draft_sha256:saved.draft_sha256,rendered_graph:await view(),writes:[...writes]});
 };
 const empty=async()=>{await expect(undo()).toBeDisabled();await expect(redo()).toBeDisabled();
  await stages.nth(4).focus();await page.keyboard.press('Control+z');await page.keyboard.press('Control+Shift+z');
  await expect(undo()).toBeDisabled();await expect(redo()).toBeDisabled();};
 await empty();await matches(A.record);await readback('fresh_history_empty');expect(writes).toEqual([]);

 const deleteUndo=async()=>{
  const row=page.locator('[data-flow-node-id="roi-A"] [data-flow-node-focus]');await row.focus();await row.press('Enter');await row.press('Delete');
  await expect(page.locator('[data-flow-node-id="roi-A"]')).toHaveCount(0);await expect(page.locator('path[data-flow-edge]')).toHaveCount(1);
  await page.keyboard.press('Control+z');await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);await matches(A.record);
  await expect(undo()).toBeDisabled();await expect(redo()).toBeEnabled();
 };
 phase='same_context_history';await deleteUndo();
 const taskButton=page.getByRole('button',{name:'작업 센터',exact:true});
 const taskResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/training-workspace/tasks'&&response.request().method()==='GET');
 await taskButton.click();const taskWire=await taskResponse;expect(taskWire.ok()).toBe(true);const taskSnapshot=await taskWire.json();
 expect(taskSnapshot).toMatchObject({source_dataset_path:workspace.dataset,labelset_id:A.project.active_labelset_id||'default',tasks:[]});
 const taskDialog=page.getByRole('dialog',{name:'작업 센터',exact:true});
 await expect(taskDialog.getByLabel('저장 작업 다시 열기',{exact:true}).locator('option')).toHaveCount(1);
 await expect(taskDialog.getByText('작업 센터 · 현재 프로젝트 0개',{exact:true})).toBeVisible();
 await taskDialog.getByRole('button',{name:'작업 센터 닫기',exact:true}).click();await expect(taskButton).toBeFocused();
 await expect(redo()).toBeEnabled();await page.keyboard.press('Control+Shift+z');
 await expect(page.locator('[data-flow-node-id="roi-A"]')).toHaveCount(0);await expect(page.locator('path[data-flow-edge]')).toHaveCount(1);
 await page.keyboard.press('Control+z');await matches(A.record);await readback('empty_task_center_preserves_original_redo');expect(writes).toEqual([]);
 await evidence.screenshot(page,'task-center-close-preserves-clean-graph-and-redo');

 phase='workflow_reopen';await stages.nth(3).click();await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toHaveCount(0);
 await openFlow();await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);await empty();await matches(A.record);
 await readback('workflow_return_reopens_persisted_graph_resets_history');expect(writes).toEqual([]);
 await page.reload();await openFlow();await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);await empty();await matches(A.record);
 await readback('actual_reload_restores_exact_draft_without_undo_redo');expect(writes).toEqual([]);
 await evidence.screenshot(page,'persisted-graph-reopened-with-empty-history');

 phase='project_handoff';await deleteUndo();
 const selectProject=async(scope:typeof A)=>{
  await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
  await dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click();
  // A never-opened project first enters Dataset Studio. Observe its actual
  // read-only source restoration before leaving that mounted prerequisite.
  const restored=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/current-summary'&&response.request().method()==='GET');
  const selected=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/project/open'&&response.request().method()==='POST');
  await dialog.getByRole('button').filter({hasText:scope.project.name}).click();const wire=await selected;
  expect(wire.ok(),await wire.text()).toBe(true);expect(wire.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});
  await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
  const restoredWire=await restored;expect(restoredWire.ok(),await restoredWire.text()).toBe(true);
  const restoredUrl=new URL(restoredWire.url());expect(restoredUrl.searchParams.get('folder_path')).toBe(workspace.dataset);
  expect(restoredUrl.searchParams.get('task')).toBe('anomaly');const summary=await restoredWire.json();expect(summary.total_images).toBe(2);
  handoffSummaries.push({project_id:scope.project.id,method:restoredWire.request().method(),path:restoredUrl.pathname,summary});
  await openFlow();await expect(page.locator('[data-flow-node-id]')).toHaveCount(4);await empty();await matches(scope.record);
 };
 await selectProject(B);
 const search=page.getByLabel('플로우 노드 검색',{exact:true});await search.fill('ROI A');
 await expect(page.getByRole('status').filter({hasText:'일치하는 노드가 없습니다.'})).toBeVisible();
 await search.fill('ROI B');const searchResult=page.getByRole('list',{name:'노드 검색 결과'}).getByRole('button');
 await expect(searchResult).toHaveCount(1);await searchResult.press('Enter');await expect(page.getByRole('textbox',{name:'노드 명칭',exact:true})).toHaveValue('ROI B');
 await matches(B.record);await readback('project_B_search_and_empty_history_use_B_only',B);
 await evidence.screenshot(page,'project-B-search-cannot-select-project-A-history');
 await selectProject(A);await readback('project_A_return_cannot_replay_project_B_or_old_history');
 expect(writes).toHaveLength(2);expect(writes.every(row=>row.phase==='project_handoff'&&row.method==='POST'&&row.path==='/api/project/open')).toBe(true);
 page.off('request',observe);
 const queueAfter=await api('/api/training-workspace/tasks');expect(queueAfter.tasks).toEqual([]);expect(queueAfter.source_dataset_path).toBe(workspace.dataset);
 await evidence.screenshot(page,'project-A-return-keeps-pinned-graph-empty-history');
 evidence.note('flow_history_scope',{A,B,images,phases,writes,taskSnapshot,queueAfter,handoffSummaries,
  dimensions:{'U012/undo':['empty','reopen','handoff'],'U012/redo':['empty','reopen','handoff'],
   'F064/native-keyboard-delete-undo-redo':['empty','handoff'],'F064/graph-search-minimap-navigation':['handoff']},
  actual_rendered_node_edge_snapshots:true,raw_draft_project_and_image_hashes_unchanged:true,empty_tasks_actual_get:true,
  same_context_overlay_preserves_redo:true,workflow_reload_and_project_change_reset_history:true,
  accidental_content_writes:0,intentional_project_open_posts:2,automatic_save_activation_inference_posts:0,
  source_browser_or_electron_only:true,training:false,inference:false,quality_approved:false});
 for(const scope of [A,B]){evidence.addFile(scope.file);evidence.addFile(scope.project_file);}
}

test('empty scoped graph history reopens and hands off without accidental graph writes',async({page,request,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),
  ...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await exercise(page,workspace,evidence,api,renderer.url);
});
test('native graph history stays scoped across empty queue reopen and project handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession;const backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),
   ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  if(!response.ok)throw Error(`Owned API ${response.status}: ${await response.text()}`);return response.json();
 },{port:backend.port,route,body,method});
 await exercise(window,workspace,evidence,api);
});

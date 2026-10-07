import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
function tree(root:string):Record<string,string>{
 const rows:Record<string,string>={};
 const walk=(dir:string)=>{for(const row of fs.readdirSync(dir,{withFileTypes:true})){
  const file=path.join(dir,row.name);expect(row.isSymbolicLink()).toBe(false);
  if(row.isDirectory())walk(file);else if(row.isFile()&&!row.name.endsWith('.lock'))rows[path.relative(root,file)]=sha(fs.readFileSync(file));
 }};if(fs.existsSync(root))walk(root);return rows;
}

// Catches pagination skipping/duplicating rows, a search failing to reset its
// last page, leaking inactive/skipped ROI paths, and viewer return losing focus.
// Only the execution response is controlled: graph/project/source and controls
// remain actual. These artifacts are display fixtures, not model inference.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Debugger artifact controls';
 const project=await api('/api/project/create',{name,task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 const graph=await api('/api/flowchart/templates/fixed-roi?inspection_task=segmentation');
 graph.name='Controlled debugger evidence';graph.nodes.find((n:any)=>n.id==='node_fixed_roi').data.params.roi_bbox=[0,0,32,32];
 const source=workspace.images[0];
 const saved=await api('/api/flowchart/pipeline?recipe_task=segmentation&source_dataset_path='+encodeURIComponent(workspace.dataset),graph);
 expect(saved.version_id).toBeTruthy();const graphBefore=tree(path.join(project.project_dir,'flowcharts'));
 const raster='data:image/png;base64,'+png(16,3,(x,y)=>[x*11,y*13,71]).toString('base64');
 const inputs=Array.from({length:30},(_,i)=>({roi_id:'region:'+String(i).padStart(2,'0'),bbox:[0,0,16,16],image:raster,
  image_size:[16,16],source_transform:[[1,0,0],[0,1,0],[0,0,1]],evidence:{fixture_kind:'controlled_input',ordinal:i}}));
 const outputs=inputs.map((r,i)=>({...r,roi_id:r.roi_id+':crop',evidence:{fixture_kind:'controlled_output',ordinal:i,
  predicted_class:i===29?'scratch':'clean',verdict:i===29?'NG':'OK',confidence:i===29?.91:.88}}));
 const steps=[
  {node_id:'node_input',name:'검사 이미지',status:'passed',latency_ms:0,input_count:0,output_count:30,selected_edge_ids:['input-fixed'],artifacts:inputs},
  {node_id:'node_fixed_roi',name:'고정 ROI',status:'passed',latency_ms:0,input_count:30,output_count:30,selected_edge_ids:[],artifacts:outputs},
  // A matching ID in a skipped node must not be reported as an observed path.
  {node_id:'node_inspect',name:'ROI 결함 검사',status:'skipped',skip_reason:'outside_debug_scope',latency_ms:0,input_count:0,output_count:0,selected_edge_ids:[],artifacts:[outputs[29]]},
  {node_id:'node_decision',name:'최종 판정',status:'skipped',skip_reason:'outside_debug_scope',latency_ms:0,input_count:0,output_count:0,selected_edge_ids:[],artifacts:[]},
  {node_id:'node_output',name:'검사 결과',status:'skipped',skip_reason:'outside_debug_scope',latency_ms:0,input_count:0,output_count:0,selected_edge_ids:[],artifacts:[]},
 ];
 const result={status:'partial',stop_node_id:'node_fixed_roi',final_verdict:'REVIEW',is_ok:false,
  rejection_reason:'Controlled display fixture; no model execution',roi_count:30,defective_roi_count:0,crops:[],execution_steps:steps,total_latency_ms:0,
  inspected_image_size:[32,32],image_path:source.path,image_id:path.basename(source.path,'.png'),graph_sha256:'c'.repeat(64),execution_target:'local',execution_device:'cpu'};
 let fixtureRequests=0;const writes:string[]=[];
 page.on('request',r=>{if(['POST','PUT','PATCH','DELETE'].includes(r.method()))writes.push(r.method()+' '+new URL(r.url()).pathname);});
 const controlled=async(route:any)=>{expect(route.request().method()).toBe('POST');const body=route.request().postDataJSON();
  expect(body).toMatchObject({project_id:project.id,image_path:source.path,stop_node_id:'node_fixed_roi',execution_target:'local',device:'cpu'});
  expect(body.pipeline.nodes.find((n:any)=>n.id==='node_fixed_roi').data.params.roi_bbox).toEqual([0,0,32,32]);
  fixtureRequests++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(result)});
 };
 await page.route('**/api/flowchart/run',controlled);
 try{
  if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
  await page.getByRole('button',{name:'이미지 변경...',exact:true}).click();
  await page.getByRole('button',{name:path.basename(source.path)+' 검사 이미지 선택',exact:true}).click();
  await page.getByRole('button',{name:'선택 확정',exact:true}).click();
  await page.getByRole('tab',{name:'편집',exact:true}).click();await page.locator('[data-flow-node-id="node_fixed_roi"]').click();
  await page.getByRole('tab',{name:'테스트',exact:true}).click();await page.getByLabel('플로우 실행 위치',{exact:true}).selectOption('local_cpu');
  await page.getByRole('button',{name:'선택 노드까지 실행',exact:true}).click();
  const panel=page.getByRole('region',{name:'선택 노드 실행 근거',exact:true});await expect(panel).toBeVisible();
  const input=panel.getByRole('heading',{name:'입력 · 30개',exact:true}).locator('..').locator('..');
  const output=panel.getByRole('heading',{name:'출력 · 30개',exact:true}).locator('..').locator('..');
  const ids=(section:any)=>section.locator('figure figcaption').evaluateAll((nodes:Element[])=>nodes.map(n=>n.firstChild?.textContent));
  await expect(output.locator('figure')).toHaveCount(12);expect(await ids(output)).toEqual(Array.from({length:12},(_,i)=>'region:'+String(i).padStart(2,'0')+':crop'));
  await expect(output.getByRole('button',{name:'이전',exact:true})).toBeDisabled();
  await output.getByRole('button',{name:'다음',exact:true}).press('Enter');await expect(output).toContainText('2 / 3');
  expect(await ids(output)).toEqual(Array.from({length:12},(_,i)=>'region:'+String(i+12).padStart(2,'0')+':crop'));
  await output.getByRole('button',{name:'다음',exact:true}).press('Space');await expect(output).toContainText('3 / 3');
  expect(await ids(output)).toEqual(['region:24:crop','region:25:crop','region:26:crop','region:27:crop','region:28:crop','region:29:crop']);
  await expect(output.getByRole('button',{name:'다음',exact:true})).toBeDisabled();
  await expect(input).toContainText('1 / 3');await expect(input.locator('figure')).toHaveCount(12);
  await evidence.screenshot(page,prefix+'-independent-input-output-pages');
  await output.getByLabel('출력 ROI 검색',{exact:true}).fill('SCRATCH');await expect(output.locator('figure')).toHaveCount(1);
  expect(await ids(output)).toEqual(['region:29:crop']);await expect(output.getByRole('button',{name:'다음',exact:true})).toHaveCount(0);
  const details=output.locator('summary');await details.press('Enter');await expect(output.locator('pre')).toBeVisible();
  const measurements=JSON.parse(await output.locator('pre').innerText());expect(measurements).toEqual({fixture_kind:'controlled_output',ordinal:29,predicted_class:'scratch',verdict:'NG',confidence:.91});
  await output.getByTitle('이 ROI의 실행 경로 보기',{exact:true}).press('Enter');
  const trace=panel.locator('section').filter({has:page.getByRole('heading',{name:'ROI 경로 · region:29:crop',exact:true})}).last();
  await expect(trace).toContainText('검사 이미지 · passed · 0 → 30');await expect(trace).toContainText('고정 ROI · passed · 30 → 30');
  await expect(trace).toContainText('연결: node_fixed_roi');await expect(trace).not.toContainText('ROI 결함 검사');await expect(trace).not.toContainText('최종 판정');
  const opener=output.getByRole('button',{name:'확대하여 근거 보기',exact:true});await opener.press('Space');
  const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});await expect(viewer).toBeVisible();
  await expect(viewer).toContainText('region:29:crop');await expect(viewer).toContainText('node_fixed_roi');await expect(viewer).toContainText('c'.repeat(64));
  expect(await viewer.locator('img').first().getAttribute('src')).toBe(raster);
  await evidence.screenshot(page,prefix+'-selected-exact-controlled-roi');
  await page.keyboard.press('Escape');await expect(viewer).toBeHidden();await expect(opener).toBeFocused();
  await expect(output.getByLabel('출력 ROI 검색',{exact:true})).toHaveValue('SCRATCH');await expect(output.locator('pre')).toBeVisible();
  await opener.press('Enter');await expect(viewer).toBeVisible();await viewer.getByRole('button',{name:'선택 노드로 돌아가기',exact:true}).click();
  await expect(viewer).toBeHidden();await expect(opener).toBeFocused();
  await output.getByLabel('출력 ROI 검색',{exact:true}).fill('not-present-ordinal');await expect(output.locator('figure')).toHaveCount(0);await expect(output).toContainText('표시할 영역이 없습니다.');
  await output.getByLabel('출력 ROI 검색',{exact:true}).fill('');await expect(output.locator('figure')).toHaveCount(12);await expect(output).toContainText('1 / 3');
  await input.getByLabel('입력 ROI 검색',{exact:true}).fill('region:29');expect(await ids(input)).toEqual(['region:29']);await expect(output.locator('figure')).toHaveCount(12);
  await evidence.screenshot(page,prefix+'-search-empty-reset-and-independent-input');
  const afterControls=writes.slice();expect(fixtureRequests).toBe(1);expect(afterControls.filter(x=>x!=='POST /api/flowchart/run')).toEqual([]);
  expect(tree(path.join(project.project_dir,'flowcharts'))).toEqual(graphBefore);
  for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
  await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
  await page.getByRole('tab',{name:'편집',exact:true}).click();await page.locator('[data-flow-node-id="node_fixed_roi"]').click();
  await expect(page.getByRole('region',{name:'선택 노드 실행 근거',exact:true})).toHaveCount(0);
  await expect(page.getByText('실행 후 이 노드의 입력·출력과 판정 근거가 표시됩니다.',{exact:false})).toBeVisible();
  expect(fixtureRequests).toBe(1);expect(writes).toEqual(afterControls);expect(tree(path.join(project.project_dir,'flowcharts'))).toEqual(graphBefore);
  const reread=await api('/api/flowchart/pipeline?inspection_task=segmentation&source_dataset_path='+encodeURIComponent(workspace.dataset));expect(reread.nodes).toEqual(graph.nodes);expect(reread.edges).toEqual(graph.edges);
  evidence.note('debugger_artifact_controls',{record_id:'U006',supplemental_action_ids:['debugger-artifact-search','debugger-artifact-page','debugger-artifact-details','debugger-roi-path','debugger-viewer-return'],
   actual_ui:true,source_electron:native,controlled_execution_response:true,actual_backend_execution:false,fixture_result_sha256:sha(Buffer.from(JSON.stringify(result))),fixture_requests:fixtureRequests,
   graph_before:graphBefore,graph_after:tree(path.join(project.project_dir,'flowcharts')),project_id:project.id,source_images:workspace.images,writes,
   pages:[12,12,6],independent_input_output_paging:true,uppercase_search_resets_last_page:true,empty_search_message:true,reset_first_page:true,
   exact_measurements:measurements,skipped_node_excluded_from_path:true,selected_edge_path_only:true,enter_space_controls:true,escape_and_explicit_return_focus:true,
   reload_does_not_restore_stale_execution:true,graph_source_unchanged:true,model_inference_executed:false,model_quality_accepted:false,installed_native_accepted:false,process_tree_accepted:false,windows_excluded:true});
 }finally{await page.unroute('**/api/flowchart/run',controlled);}
}

test('flow debugger pages searches and returns exact controlled artifacts without writes',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(reply.ok(),await reply.text()).toBe(true);return reply.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native flow debugger keyboard controls keep exact controlled artifact and return focus',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!reply.ok)throw Error(`Owned debugger HTTP ${reply.status}: ${await reply.text()}`);return reply.json();},{port:backend.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

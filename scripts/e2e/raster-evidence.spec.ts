import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown)=>Promise<any>;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string) {
 const source=path.join(workspace.root,'raster-source'),originals:Record<string,string>={};
 const images=[...Array.from({length:3},(_,i)=>({split:'train',label:'good',name:`train-good-${i}.png`,shift:i*10})),
  {split:'val',label:'good',name:'val-good.png',shift:5},{split:'test',label:'good',name:'test-good.png',shift:0},
  {split:'test',label:'anomaly',name:'test-anomaly.png',shift:150}];
 for(const image of images) {const dir=path.join(source,image.split,image.label);fs.mkdirSync(dir,{recursive:true});const file=path.join(dir,image.name);
  fs.writeFileSync(file,png(64,3,(x,y)=>[Math.min(255,30+image.shift+x),30+y,50]));originals[file]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');}
 const project=await api('/api/project/create',{name:'Stored raster evidence fixture',task:'anomaly'});
 await api('/api/project/update',{source_dataset_dir:source});await api('/api/dataset/import',{folder_path:source,task:'anomaly'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/raster_models.py'),source,project.models_dir],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:60_000}).trim());
 await api('/api/flowchart/models/verify',{source_dataset_path:source,models:[{job_id:'job_fixture_raster',task:'anomaly'}]});
 const pipeline=await api('/api/flowchart/templates/single-segmentation?inspection_task=anomaly&job_id=job_fixture_raster');
 const saved=await api(`/api/flowchart/pipeline?source_dataset_path=${encodeURIComponent(source)}`,pipeline),frozen=await api(`/api/flowchart/pipelines/${saved.version_id}`);
 const testImages=(await api(`/api/dataset/images?folder_path=${encodeURIComponent(source)}&task=anomaly&split=test`)).items;
 const run=await api('/api/inspections/runs',{source_folder:source,task:'anomaly',scope:'test',pipeline:frozen,images:testImages,execution_target:'local',device:'cpu',project_id:project.id});
 const results=[];for(const image of testImages){const result=await api(`/api/inspections/runs/${run.run_id}/execute`,{image_path:image.file_path});expect(result.execution_device).toBe('cpu');expect(result.crops).toHaveLength(1);expect(result.crops[0].mask).toMatch(/^data:image\/png;base64,/);expect(result.crops[0].anomaly_map).toMatch(/^data:image\/png;base64,/);results.push(result);}
 await api(`/api/inspections/runs/${run.run_id}/finish`,{status:'completed'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Stored raster evidence fixture');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(5).click();
 await page.locator('[aria-label="검사 이력"]').getByRole('button').filter({hasText:frozen.name}).click();
 await page.getByRole('button',{name:'원본·판정 근거 보기',exact:true}).click();
 const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
 await expect(viewer.getByLabel('근거 이미지 종류')).toHaveValue('original');
 await expect(viewer.getByLabel('근거 ROI 표시')).toBeVisible();await expect(viewer.getByLabel('근거 ROI 표시').locator('text')).toHaveCount(1);
 await viewer.getByLabel('근거 ROI 라벨').uncheck();await expect(viewer.getByLabel('근거 ROI 표시').locator('text')).toHaveCount(0);
 await expect(viewer.getByLabel('근거 ROI 표시').locator('rect')).toHaveCount(1);
 const displayedPath=(await viewer.locator('p').first().innerText()).split(' · 실행 ')[0];
 const result=results.find(row=>row.image_path===displayedPath);expect(result).toBeTruthy();
 for(const [layer,field] of [['roi:0:image','crop_thumbnail'],['roi:0:mask','mask'],['roi:0:map','anomaly_map']] as const) {
  await viewer.getByLabel('근거 이미지 종류').selectOption(layer);
  expect(await viewer.locator('img').first().getAttribute('src')).toBe(result.crops[0][field]);
  await expect(viewer.getByLabel('근거 겹침 이미지').locator('option')).toHaveCount(1);
  await expect(viewer.getByLabel('근거 ROI 표시')).toHaveCount(0);
 }
 await viewer.locator('summary').click();await expect(viewer.locator('pre')).toContainText('pixel_score');
 expect(await viewer.locator('pre').innerText()).not.toContain(result.crops[0].anomaly_values.data);
 await evidence.screenshot(page,native?'native-stored-heatmap':'browser-stored-heatmap');
 await viewer.getByRole('button',{name:'검사 결과로 돌아가기',exact:true}).click();
 await expect(page.locator('[aria-label="선택한 검사 실행 식별자"]')).toContainText(saved.version_id.slice(0,8));
 // Execute through the actual flow test controls, then reopen the selected node's output.
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
 await page.getByRole('button',{name:/이미지 변경/}).click();
 await page.getByRole('button',{name:'test-anomaly.png 검사 이미지 선택',exact:true}).click();await page.getByRole('button',{name:'선택 확정',exact:true}).click();
 await page.getByRole('tab',{name:'테스트',exact:true}).click();await page.getByLabel('플로우 실행 위치').selectOption('local_cpu');
 const ran=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/flowchart/run'&&response.request().method()==='POST');
 await page.getByRole('button',{name:/선택 이미지 검사/}).click();const response=await ran;expect(response.status()).toBe(200);const flow=await response.json();expect(flow.execution_device).toBe('cpu');
 await page.getByRole('tab',{name:'편집',exact:true}).click();await page.locator('[data-flow-node-id="node_inspect"]').click();
 const debuggerPanel=page.getByRole('region',{name:'선택 노드 실행 근거'});await debuggerPanel.getByLabel('출력 ROI 검색').fill('full_image');await debuggerPanel.getByRole('button',{name:'확대하여 근거 보기',exact:true}).last().click();
 await expect(viewer).toContainText('node_inspect');await expect(viewer).toContainText(saved.version_id);expect(flow.graph_sha256).toMatch(/^[a-f0-9]{64}$/);await expect(viewer).toContainText(flow.graph_sha256);const mapOption=viewer.getByLabel('근거 이미지 종류').locator('option').filter({hasText:'열지도'});expect(await mapOption.count()).toBe(1);
 await viewer.getByLabel('근거 이미지 종류').selectOption((await mapOption.getAttribute('value'))!);expect(await viewer.locator('img').getAttribute('src')).toBe(flow.crops[0].anomaly_map);
 await evidence.screenshot(page,native?'native-node-heatmap':'browser-node-heatmap');
 await viewer.getByRole('button',{name:'선택 노드로 돌아가기',exact:true}).click();await expect(debuggerPanel.getByRole('heading',{name:'실행 근거 · 전체 이미지 이상 탐지',exact:true})).toBeVisible();await expect(debuggerPanel.getByLabel('출력 ROI 검색')).toHaveValue('full_image');
 for(const[file,sha]of Object.entries(originals))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
 evidence.note('rasters',{actual_cpu_inspection:true,actual_flow_control:true,app_training:false,model_quality_approval:false,fixture,run_id:run.run_id,saved_version_id:saved.version_id,
  results,flow,source_unchanged:true,source_roi_box:true,roi_labels:true,exact_return:true,mask_and_map_match_saved_bytes:true,separate_coordinate_spaces:true});
}

test('stored ROI mask and heatmap match CPU inspection and exact node output',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body)=>{const response=body===undefined?await request.get(renderer.origin+route):route.endsWith('/update')||route.endsWith('/finish')?await request.put(renderer.origin+route,{data:body}):await request.post(renderer.origin+route,{data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native stored ROI mask and heatmap match CPU inspection and exact node output', {tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession;const backend=await electronSession.waitForBackend();
 const api:OwnedApi=(route,body)=>window.evaluate(async({port,route,body})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,body===undefined?{}:{method:route.endsWith('/update')||route.endsWith('/finish')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(!response.ok)throw Error(`Owned fixture API: HTTP ${response.status}`);return response.json();
 },{port:backend.port,route,body});
 await exercise(window,workspace,evidence,api,true);
});

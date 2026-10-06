import {copyFileSync,mkdirSync} from 'node:fs';
import {join} from 'node:path';
import type {Page,Route} from '@playwright/test';
import {expect,test,type RendererServer,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import { confirmFlowSave } from './fixtures/flowChange';

// Actual renderer on the owned synthetic harness. Only completed-model catalog,
// reference verification and approval readback are transport fixtures: no trained
// weights, real inference, approval creation or target deployment is claimed.
const model='s204-distance-model';
const score={domain:'distance',unit:'mahalanobis_distance',direction:'higher_is_defect',calibration_id:'s204-model-calibration',threshold:8};
const json=(route:Route,body:unknown,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});
// Anomaly import needs normal-only train data and explicit evaluation folders.
// The generic ok/ng tree only works via the OK fallback on case-insensitive filesystems.
function anomalyDataset(workspace:Workspace){
 const root=join(workspace.root,'anomaly-dataset'),ok=workspace.images.find(image=>image.label==='ok')!.path,ng=workspace.images.find(image=>image.label==='ng')!.path;
 const layout:Array<[string,Array<[string,string]>]>=[
  ['train/good',[['sample-ok-1.png',ok],['sample-ok-2.png',ok],['sample-ok-3.png',ok]]],
  ['test/good',[['sample-ok.png',ok]]],['test/defect',[['sample-ng.png',ng]]],
 ];
 for(const [folder,files] of layout){mkdirSync(join(root,folder),{recursive:true});for(const [name,from] of files)copyFileSync(from,join(root,folder,name));}
 return root;
}
async function setup(page:Page,renderer:RendererServer,workspace:Workspace,evidence:Evidence){
 const source=anomalyDataset(workspace);
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S204 workspace',task:'anomaly'}});expect(created.ok()).toBe(true);
 const updated=await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:source}});expect(updated.ok()).toBe(true);
 const dataset=(await updated.json()).source_dataset_dir;expect(typeof dataset).toBe('string');evidence.note('source_dataset',{picked:source,saved:dataset});
 let refuse=false,hold=false,release=()=>{};const requests:Array<{path:string;body:unknown}>=[];
 const record=(route:Route)=>{requests.push({path:new URL(route.request().url()).pathname,body:route.request().postData()});evidence.note('transport_fixtures',requests);};
 await page.route('**/api/flowchart/models/catalog?*',route=>{record(route);return json(route,{models:[{job_id:model,task:'anomaly',label:'Recorded distance model',preset:null,created_at:null,best_metric:null,source_dataset_path:dataset,class_names:['ng','ok'],class_ids:[1,2],score_spec:score}],total:1});});
 await page.route('**/api/flowchart/models/verify',async route=>{record(route);if(hold)await new Promise<void>(resolve=>{release=resolve;});return json(route,refuse?{detail:'fixture: model reference revoked'}:{verified_job_ids:[model]},refuse?409:200);});
 await page.route('**/api/export/flow/approval-prerequisites?*',route=>{record(route);return json(route,{status:'ready',approval_created:false,approval_revision_ids:{[model]:'revision-fixture'},models:[{job_id:model,task:'anomaly',node_ids:['node_inspect'],checkpoint_sha256:'a'.repeat(64),candidates:[],selected_revision_id:'revision-fixture',reason:null}]});});
 await installDesktopHostShim(page,renderer.port);
 const imported=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/current-summary'&&response.request().method()==='GET');
 await page.goto(renderer.url);const importResponse=await imported;expect(importResponse.status()).toBe(200);
 const importReadback=await importResponse.json();expect(importReadback.total_images).toBe(5);evidence.note('dataset_import',importReadback);
 await expect(page.getByRole('button',{name:/\.png 라벨링에서 열기$/})).toHaveCount(5);
 await page.getByRole('button',{name:/05.*플로우차트/}).click();
 await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();
 await expect(page.locator('[data-flow-node-id]')).not.toHaveCount(0);
 return {requests,refuse:()=>{refuse=true;},hold:()=>{hold=true;},release:()=>{hold=false;release();}};
}
const area=(page:Page,name:string)=>page.getByRole('tab',{name,exact:true});
const ids=(page:Page)=>page.locator('[data-flow-node-id]').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-flow-node-id')));
async function preview(page:Page){await page.getByRole('list',{name:'목적 레시피'}).getByRole('button',{name:/^고정 ROI 검사/}).click();const dialog=page.getByRole('dialog',{name:'레시피 미리보기·모델 매핑'});await expect(dialog).toBeVisible();return dialog;}
async function map(page:Page){const dialog=await preview(page);await dialog.getByLabel('검사 모델 레시피 모델',{exact:true}).selectOption(model);await dialog.getByLabel('검사 모델 클래스 적용 범위').selectOption('all');return dialog;}

for(const viewport of [{width:1366,height:768},{width:1920,height:1080}])test(`four work areas preserve their identities at ${viewport.width}x${viewport.height}`,async({page,renderer,workspace,evidence})=>{
 await page.setViewportSize(viewport);const fixture=await setup(page,renderer,workspace,evidence);
 const tabs=page.getByRole('tablist',{name:'플로우 작업 영역'});
 await expect(tabs.getByRole('tab')).toHaveText(['편집','테스트','일괄 평가','배포']);
 const identity=page.getByRole('region',{name:'플로우 식별 정보'});
 await expect(identity).toBeVisible();await expect(identity).toContainText('승인 미조회');
 const canvas=page.locator('[aria-label="검사 노드 팔레트"]'),box=(await canvas.boundingBox())!;
 expect(box.height).toBeGreaterThanOrEqual(240);
 const layout=await page.locator('#flow-panel-edit').evaluate(panel=>{
  let next=panel.nextElementSibling as HTMLElement|null;while(next&&(next.hidden||next.getBoundingClientRect().height===0))next=next.nextElementSibling as HTMLElement|null;
  const box=panel.getBoundingClientRect(),row=panel.firstElementChild!.getBoundingClientRect();
  return {overlap:next?Math.max(0,box.bottom-next.getBoundingClientRect().top):0,rowInside:row.bottom<=box.bottom+1,height:box.height};});
 expect(layout.overlap,'nothing below is drawn over the edit area').toBe(0);expect(layout.rowInside,'the canvas row stays inside the edit area').toBe(true);
 expect(layout.height,'the edit area keeps at least 240 px').toBeGreaterThanOrEqual(240);
 // The rotated ROI starter is part of the current six-recipe catalog.
 await expect(page.getByRole('list',{name:'목적 레시피'}).getByRole('button')).toHaveCount(6);
 await expect(page.getByRole('list',{name:'목적 레시피'}).getByRole('button',{name:/^회전 ROI 정렬 검사/})).toBeVisible();
 await expect(page.locator('[data-primary-action]:visible')).toHaveText(['플로우 저장']);
 await evidence.screenshot(page,`s204-edit-${viewport.width}`);
 await area(page,'편집').focus();await page.keyboard.press('ArrowRight');await expect(area(page,'테스트')).toBeFocused();await expect(area(page,'테스트')).toHaveAttribute('aria-selected','true');
 await expect(page.locator('[data-primary-action]:visible')).toHaveText(['선택 이미지 검사']);await expect(page.getByText('먼저 검사할 이미지를 선택하세요.')).toBeVisible();
 await area(page,'일괄 평가').click();await expect(page.locator('[data-primary-action]:visible')).toHaveText(['선택 코호트 평가']);
 await page.getByLabel('시험 코호트 이름').fill('Retained draft cohort');await area(page,'편집').click();await area(page,'일괄 평가').click();await expect(page.getByLabel('시험 코호트 이름')).toHaveValue('Retained draft cohort');
 await area(page,'배포').click();await expect(page.locator('[data-primary-action]:visible')).toHaveText(['패키지·배포로 이동']);
 await expect(identity).toContainText('대상 적용 응답 확인 필요');expect(fixture.requests.filter(row=>row.path.includes('approval-prerequisites'))).toHaveLength(0);
 await evidence.screenshot(page,`s204-release-${viewport.width}`);
 await expect(page.getByRole('button',{name:'패키지·배포로 이동',exact:true})).toBeDisabled();await expect(page.getByText('저장된 버전을 연 상태에서만 패키지·배포로 이동합니다.')).toBeVisible();
});

test('recipe cancel preserves current DAG; explicit mapping adopts raw-distance model as unsaved draft',async({page,renderer,workspace,evidence})=>{
 await setup(page,renderer,workspace,evidence);const original=await ids(page);let dialog=await preview(page);
 await expect(dialog.getByRole('button',{name:'매핑 확인·새 초안으로 채택'})).toBeDisabled();await expect.poll(()=>ids(page)).toEqual(original);
 await dialog.getByRole('button',{name:'취소 · 현재 그래프 유지'}).click();await expect.poll(()=>ids(page)).toEqual(original);
 const writes:string[]=[];page.on('request',request=>{const path=new URL(request.url()).pathname;if(request.method()!=='GET'&&path.startsWith('/api/flowchart/')&&!path.endsWith('/models/verify'))writes.push(`${request.method()} ${path}`);});
 dialog=await map(page);await expect(dialog).toContainText('임계값 8');await evidence.screenshot(page,'s204-recipe-mapped');
 await dialog.getByRole('button',{name:'매핑 확인·새 초안으로 채택'}).click();await expect(dialog).toHaveCount(0);await expect(page.locator('[data-flow-node-id="node_fixed_roi"]')).toBeVisible();
 await page.locator('[data-flow-node-id="node_inspect"]').click();await expect(page.getByLabel('결함 판정 임계치')).toHaveValue('8');await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(model);
 await evidence.screenshot(page,'s204-adopted-draft');expect(writes,'adoption saves and activates nothing').toEqual([]);
 // Explicit adoption captures a new active-version basis. Reusing old undo
 // would resurrect a stale graph against that basis; ordinary later edits
 // still undo within the adopted recipe.
 const undo=page.getByRole('button',{name:'플로우 실행 취소',exact:true});
 await expect(undo).toBeDisabled();
 const adopted=await ids(page);expect(adopted).not.toEqual(original);
 await page.getByLabel('결함 판정 임계치').fill('9');await expect(undo).toBeEnabled();
 await undo.click();await expect(page.getByLabel('결함 판정 임계치')).toHaveValue('8');
 await expect.poll(()=>ids(page)).toEqual(adopted);await expect(undo).toBeDisabled();
});

test('model verification refusal and cancellation during held verification preserve graph',async({page,renderer,workspace,evidence})=>{
 const fixture=await setup(page,renderer,workspace,evidence),original=await ids(page);fixture.refuse();let dialog=await map(page);
 await dialog.getByRole('button',{name:'매핑 확인·새 초안으로 채택'}).click();await expect(dialog.getByRole('alert')).toContainText('model reference revoked');await expect.poll(()=>ids(page)).toEqual(original);
 fixture.hold();await dialog.getByRole('button',{name:'매핑 확인·새 초안으로 채택'}).click();await expect(dialog.getByRole('button',{name:'처리 중…'})).toBeVisible();await dialog.getByRole('button',{name:'취소 · 현재 그래프 유지'}).click();fixture.release();await expect.poll(()=>ids(page)).toEqual(original);await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('approval readback is explicit and invalidates after semantic editing without claiming target deployment',async({page,renderer,workspace,evidence})=>{
 const fixture=await setup(page,renderer,workspace,evidence),dialog=await map(page);await dialog.getByRole('button',{name:'매핑 확인·새 초안으로 채택'}).click();await expect(dialog).toHaveCount(0);
 await page.getByRole('button',{name:'플로우 저장',exact:true}).click();await confirmFlowSave(page,'first saved flow');await expect(page.getByRole('region',{name:'플로우 식별 정보'})).not.toContainText('저장 미선택');
 await area(page,'배포').click();await page.getByRole('button',{name:'이 저장 버전의 승인 근거 확인'}).click();await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText('조회 시점의 revision 검증됨');
 expect(fixture.requests.filter(row=>row.path.includes('approval-prerequisites'))).toHaveLength(1);await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText('대상 적용 응답 확인 필요');
 await expect(page.getByRole('button',{name:'패키지·배포로 이동',exact:true})).toBeEnabled();
 await area(page,'편집').click();await page.locator('[data-flow-node-id="node_inspect"]').click();await page.getByLabel('결함 판정 임계치').fill('9');await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText('승인 미조회');
 await area(page,'배포').click();await expect(page.getByRole('button',{name:'패키지·배포로 이동',exact:true}),'an unsaved edit cannot be packaged').toBeDisabled();
});

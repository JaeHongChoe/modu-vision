import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const hash=(value:Buffer)=>crypto.createHash('sha256').update(value).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(workspace.root,'draft-boundary-source');fs.mkdirSync(source);const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,100]));const originalHash=hash(fs.readFileSync(image));
 const project=await api('/api/project/create',{name:'Annotation draft boundary fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 const query=`/api/annotations/part?file_path=${encodeURIComponent(image)}`,empty=await api(query);expect(empty.annotations).toEqual([]);
 const point=async(x:number,y:number)=>{const b=(await page.locator('[data-canvas-container]').boundingBox())!;return{x:b.x+(b.width-256)/2+x,y:b.y+(b.height-256)/2+y};};
 const drag=async(x1:number,y1:number,x2:number,y2:number,cancel=false)=>{const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});if(cancel)await page.keyboard.press('Escape');await page.mouse.up();};
 const drawPoints=async(points:number[][])=>{
  // Saving can change toolbar height. A 1:1 view intentionally preserves its
  // existing pan across resize; explicitly recenter before source-pixel input.
  await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();
  for(const[x,y]of points){const p=await point(x,y);await page.mouse.move(p.x,p.y);await expect(page.getByTestId('canvas-hud')).toContainText(new RegExp(`X:\\s*${x}\\s*px\\s*Y:\\s*${y}\\s*px`));await page.mouse.click(p.x,p.y);}
 };
 const saveButton=page.getByTestId('annotation-save-button'),pending=()=>page.getByRole('button',{name:'Save Changes',exact:true}),count=()=>page.getByTitle('Delete annotation',{exact:true});
 const writes:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/annotations/save')writes.push(r.url());});
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();await page.keyboard.press('Delete');await page.keyboard.press('Control+z');await page.keyboard.press('Control+y');await expect(count()).toHaveCount(0);await expect(pending()).toHaveCount(0);
 const cancellations:any[]=[];
 for(const tool of ['바운딩 박스 (BBox - 2)','회전 바운딩 박스 (Rotated BBox OBB - 3)']){
  await page.getByTitle(tool,{exact:true}).click();await drag(35,35,35,35);await expect(count()).toHaveCount(0);await expect(pending()).toHaveCount(0);await drag(20,20,70,60,true);await expect(count()).toHaveCount(0);await expect(pending()).toHaveCount(0);expect((await api(query)).annotations).toEqual([]);cancellations.push({tool,zero_area_rejected:true,escape_during_drag_no_annotation:true});
 }
 await page.getByTitle('다각형 폴리곤 (Polygon - 4)',{exact:true}).click();await drawPoints([[120,30],[180,30]]);await page.keyboard.press('Enter');await expect(count()).toHaveCount(0);await expect(pending()).toHaveCount(0);await page.keyboard.press('Escape');await drawPoints([[120,30],[180,30],[150,80]]);await page.keyboard.press('Escape');await page.keyboard.press('Enter');await expect(count()).toHaveCount(0);await expect(pending()).toHaveCount(0);expect((await api(query)).annotations).toEqual([]);expect(writes).toEqual([]);await evidence.screenshot(page,`${native?'native':'browser'}-incomplete-and-cancelled-geometry-no-write`);
 const save=async(status=200)=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await saveButton.click();expect((await response).status()).toBe(status);return api(query);};
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();await drag(20,20,70,60);const baseline=await save();expect(baseline.annotations).toHaveLength(1);expect(baseline.annotations[0].bbox).toEqual([20,20,70,60]);const baselineHash=hash(Buffer.from(JSON.stringify(baseline)));
 await page.getByTitle('다각형 폴리곤 (Polygon - 4)',{exact:true}).click();await drawPoints([[120,30],[180,30],[150,80]]);await page.keyboard.press('Enter');await expect(count()).toHaveCount(2);
 // An explicit transport failure must preserve the original saved revision and
 // the unsaved draft. Only the user's deliberate retry can write it.
 const blocked='**/api/annotations/save';await page.route(blocked,r=>r.fulfill({status:503,json:{detail:'Controlled annotation save unavailable'}}));const failed=await save(503);expect(failed).toEqual(baseline);expect(hash(Buffer.from(JSON.stringify(failed)))).toBe(baselineHash);await expect(page.getByText(/Failed:.*Controlled annotation save unavailable/)).toBeVisible();await expect(saveButton).toBeEnabled();await expect(count()).toHaveCount(2);await evidence.screenshot(page,`${native?'native':'browser'}-failed-save-retains-baseline-and-draft`);
 await page.unroute(blocked);const retried=await save();expect(retried.annotations).toHaveLength(2);expect(retried.annotations.find((a:any)=>a.type==='bbox')).toEqual(baseline.annotations[0]);expect(retried.annotations.find((a:any)=>a.type==='polygon').polygon).toEqual([[120,30],[180,30],[150,80]]);
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 2$/})).toBeVisible();const reopened=await api(query);expect(reopened.annotations).toEqual(retried.annotations);await expect(count()).toHaveCount(2);expect(hash(fs.readFileSync(image))).toBe(originalHash);expect(writes).toHaveLength(3);await evidence.screenshot(page,`${native?'native':'browser'}-deliberate-retry-exact-labels-reopened`);
 evidence.note('annotation_draft_boundaries',{project_id:project.id,image,image_sha256:originalHash,empty_annotations:empty.annotations,cancellations,polygon_incomplete_enter_no_write:true,polygon_escape_no_write:true,baseline,failed_save_equal_baseline:true,baseline_sha256:baselineHash,retried,reopened,actual_save_request_count:writes.length,explicit_transport_failure_503:true,original_unchanged:true,actual_ui_draw_cancel_save_reopen:true,representative_human_annotation_quality:false,training_or_model_execution:false});
}
test('incomplete and cancelled annotation drafts cannot replace saved labels, failed save retries explicitly',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native incomplete and cancelled annotation drafts preserve saved labels through explicit retry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned annotation API ${r.status}`);return r.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

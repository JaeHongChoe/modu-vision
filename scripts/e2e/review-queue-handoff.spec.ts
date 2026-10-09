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
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// Read the actual base canvas at source-pixel centres. This is a decoded
// synthetic image/coordinate readiness witness, not model or display-quality approval.
async function canvasSourceCentres(page:Page,rasterPan={x:0,y:0}){
 return page.locator('[data-canvas-container]').evaluate((container,rasterPan)=>{
  const canvas=container.querySelector('canvas'),hud=document.querySelector('[data-testid="canvas-hud"]') as HTMLElement|null;
  const rect=container.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  const scale=Number(hud?.innerText.match(/scale\s*([\d.]+)\s*%/)?.[1]||0)/100;
  if(!canvas||!scale)return null;
  const ctx=canvas.getContext('2d');if(!ctx)return null;
  const x0=(rect.width-64*scale)/2+rasterPan.x,y0=(rect.height-64*scale)/2+rasterPan.y;
  if(x0<0||y0<0)return null;
  const pixels:number[]=[];
  for(let y=0;y<64;y++)for(let x=0;x<64;x++){
   const px=Math.floor((x0+(x+.5)*scale)*dpr),py=Math.floor((y0+(y+.5)*scale)*dpr);
   if(px<0||py<0||px>=canvas.width||py>=canvas.height)return null;
   pixels.push(...ctx.getImageData(px,py,1,1).data);
  }
  const transform=ctx.getTransform();
  return {scale,dpr,css_width:rect.width,css_height:rect.height,backing_width:canvas.width,backing_height:canvas.height,transform:{a:transform.a,b:transform.b,c:transform.c,d:transform.d,e:transform.e,f:transform.f},offset_x:x0,offset_y:y0,pixels};
 },rasterPan);
}

// The real 1:1 toolbar centers against fractional CSS extents. Align that
// unchanged view with the backing-pixel grid using the existing middle-button pan.
function canvasRasterAlignment(mapping:{scale:number;dpr:number;css_width:number;css_height:number;backing_width:number;backing_height:number;transform:{a:number;b:number;c:number;d:number;e:number;f:number}}){
 const {scale,dpr,css_width,css_height,backing_width,backing_height,transform}=mapping;
 if(scale!==1||![dpr,css_width,css_height].every(Number.isFinite)||dpr<=0||css_width<64||css_height<64||backing_width!==Math.floor(css_width*dpr)||backing_height!==Math.floor(css_height*dpr)||transform.a!==dpr||transform.d!==dpr||transform.b!==0||transform.c!==0||transform.e!==0||transform.f!==0)throw Error('Exact original 1:1 canvas backing transform required');
 const x0=(css_width-64)/2,y0=(css_height-64)/2;
 return {x:Math.round(x0*dpr)/dpr-x0,y:Math.round(y0*dpr)/dpr-y0};
}

async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(w.root,'queue-handoff-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,i)=>{const file=path.join(source,name+'.png');fs.writeFileSync(file,png(64,3,(x,y)=>[x,y,100+i]));return {file,sha256:sha(file)};});
 const project=await api('/api/project/create',{name:'Queue handoff controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const seed=()=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),w.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const original=seed(),alternate=seed();expect(original.record.evaluation_id).not.toBe(alternate.record.evaluation_id);
 const stages=page.getByRole('navigation',{name:'Workflow Stages'});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await stages.getByRole('button').nth(1).click();const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();await page.locator('summary').filter({hasText:'저장 검토 큐 · 오류·불일치·임계값 우선'}).click();};
 await navigate();const panel=page.getByRole('region',{name:'저장된 검토 큐'});
 const origin=panel.getByLabel('검토 큐 원본 평가',{exact:true}),margin=panel.getByLabel('검토 큐 임계 주변 범위',{exact:true}),choice=panel.getByLabel('저장 검토 큐 선택',{exact:true}),create=panel.getByRole('button',{name:'우선순위 큐 저장',exact:true});
 await origin.selectOption('');await expect(create).toBeDisabled();await origin.selectOption(original.record.evaluation_id);await margin.fill('2');
 let response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();expect((await response).status()).toBe(422);expect((await api('/api/data-workbench/review-queues')).queues).toEqual([]);
 const createQueue=async(id:string,range:string)=>{await origin.selectOption(id);await margin.fill(range);const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();const saved=await reply;expect(saved.ok(),await saved.text()).toBe(true);return saved.json();};
 const first=await createQueue(original.record.evaluation_id,'.05'),second=await createQueue(alternate.record.evaluation_id,'.01');
 expect(first.origin.evaluation_id).toBe(original.record.evaluation_id);expect(second.origin.evaluation_id).toBe(alternate.record.evaluation_id);
 expect(first.items.map((r:any)=>r.relative_path)).toEqual(['error.png','disagreement.png','threshold.png']);expect(second.items.map((r:any)=>r.relative_path)).toEqual(['error.png','disagreement.png']);
 await choice.selectOption(first.id);await expect(panel).toContainText('검토 진행 0 / 3');await navigate();await expect(choice).toHaveValue(first.id);
 await choice.selectOption(second.id);await expect(panel).toContainText('검토 진행 0 / 2');await navigate();await expect(choice).toHaveValue(second.id);
 await choice.selectOption(first.id);await panel.getByRole('button',{name:'현재 항목 열기',exact:true}).click();await expect(panel.getByRole('button',{name:'검토 완료 · 다음',exact:true})).toBeEnabled();
 // Return to the older explicitly selected evaluation, not the latest report.
 await panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true}).click();await expect(page.getByRole('combobox',{name:'모델별 저장 평가',exact:true})).toHaveValue(original.record.evaluation_id);
 await page.reload();await expect(page.getByRole('combobox',{name:'모델별 저장 평가',exact:true})).toHaveValue(original.record.evaluation_id);await e.screenshot(page,`${native?'native':'browser'}-queue-exact-evaluation-return`);
 await navigate();await expect(choice).toHaveValue(first.id);await panel.getByRole('button',{name:'현재 항목 열기',exact:true}).click();
 // Real unsaved canvas content must block advancing or leaving for training.
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 // A stale Fit percentage is not evidence that this image decoded. Keep the
 // original configured twenty-second absolute scope for readiness and 1:1 mapping.
 const canvasBegan=Date.now(),canvasDeadline=canvasBegan+20_000;
 const remainingCanvas=()=>{const remaining=canvasDeadline-Date.now();expect(remaining).toBeGreaterThan(0);return remaining;};
 await expect.poll(async()=>{const probe=await canvasSourceCentres(page);return probe?.pixels.every((value,index)=>index%4===2?value===100:index%4===3?value===255:true)||false;},{timeout:remainingCanvas()}).toBe(true);
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 const beforeAlignment=await canvasSourceCentres(page);expect(beforeAlignment).not.toBeNull();
 const rasterPan=canvasRasterAlignment(beforeAlignment!),panBounds=(await page.locator('[data-canvas-container]').boundingBox())!;
 expect(panBounds.width).toBe(beforeAlignment!.css_width);expect(panBounds.height).toBe(beforeAlignment!.css_height);
 const panStart={x:panBounds.x+panBounds.width/2,y:panBounds.y+panBounds.height/2},panEnd={x:panStart.x+rasterPan.x,y:panStart.y+rasterPan.y};
 if(rasterPan.x!==0||rasterPan.y!==0){await page.mouse.move(panStart.x,panStart.y);await page.mouse.down({button:'middle'});await page.mouse.move(panEnd.x,panEnd.y);await page.mouse.up({button:'middle'});}
 const expectedPixels=Array.from({length:64*64},(_,index)=>[index%64,Math.floor(index/64),100,255]).flat();
 await expect.poll(async()=>{const probe=await canvasSourceCentres(page,rasterPan);return probe&&{scale:probe.scale,pixels:probe.pixels};},{timeout:remainingCanvas()}).toEqual({scale:1,pixels:expectedPixels});
 const canvasMapping=await canvasSourceCentres(page,rasterPan),canvasFinished=Date.now();expect(canvasMapping).not.toBeNull();
 expect(canvasMapping!.scale).toBe(1);expect(canvasMapping!.pixels).toEqual(expectedPixels);expect(Date.now()).toBeLessThanOrEqual(canvasDeadline);
 const bounds=(await page.locator('[data-canvas-container]').boundingBox())!;
 expect(bounds.width).toBe(canvasMapping!.css_width);expect(bounds.height).toBe(canvasMapping!.css_height);
 const pt=(v:number)=>({x:bounds.x+canvasMapping!.offset_x+v,y:bounds.y+canvasMapping!.offset_y+v});const a=pt(10),b=pt(30);
 await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:6});await page.mouse.up();await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toBeVisible();await page.getByRole('button',{name:'집중 편집',exact:true}).click();await page.locator('summary').filter({hasText:'저장 검토 큐 · 오류·불일치·임계값 우선'}).click();await expect(choice).toHaveValue(first.id);
 const review=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true}),skip=panel.getByRole('button',{name:'보류 · 다음',exact:true}),prepare=panel.getByRole('button',{name:'수정·검수 데이터로 학습 준비',exact:true});
 await expect(review).toBeDisabled();await expect(skip).toBeDisabled();await expect(prepare).toBeDisabled();expect(await api('/api/data-workbench/review-queues/'+first.id)).toEqual(first);await e.screenshot(page,`${native?'native':'browser'}-queue-unsaved-guards`);
 response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await response).status()).toBe(200);await expect(prepare).toBeEnabled();
 const saved=await api('/api/annotations/error?file_path='+encodeURIComponent(inputs[0].file));expect(saved.annotations).toHaveLength(1);expect(saved.annotations[0].bbox).toEqual([10,10,30,30]);expect(saved.metadata.workflow_state).not.toBe('approved');
 await api('/api/team-data/settings',{expected_revision:1,actor:'owned-functional-fixture',changes:{review_enabled:true,approved_only_training:true}},'PUT');
 const blocked=await api('/api/team-data/readiness');expect(blocked.ready).toBe(false);expect(blocked.counts.eligible).toBe(0);await prepare.click();await expect(panel.getByRole('alert')).toContainText('학습에 사용할 이미지가 없습니다.');await expect(panel).toBeVisible();
 // A separate fixture policy permits unreviewed inputs; navigation is not a
 // training submission, annotation approval, human review or quality claim.
 const settings=(await api('/api/team-data')).settings;await api('/api/team-data/settings',{expected_revision:settings.revision,actor:'owned-functional-fixture',changes:{approved_only_training:false}},'PUT');
 const permitted=await api('/api/team-data/readiness');expect(permitted.ready).toBe(true);expect(permitted.counts.approved).toBe(0);await prepare.click();await expect(page.getByRole('region',{name:'저장된 검토 큐'})).toHaveCount(0);
 expect((await api('/api/training/jobs')).jobs).toEqual([]);await stages.getByRole('button').nth(1).click();await page.locator('summary').filter({hasText:'저장 검토 큐 · 오류·불일치·임계값 우선'}).click();await expect(choice).toHaveValue(first.id);expect(await api('/api/data-workbench/review-queues/'+first.id)).toEqual(first);expect(await api('/api/data-workbench/review-queues/'+second.id)).toEqual(second);
 for(const input of inputs)expect(sha(input.file)).toBe(input.sha256);expect(sha(original.path)).toBe(original.sha256);expect(sha(alternate.path)).toBe(alternate.sha256);
 await e.screenshot(page,`${native?'native':'browser'}-queue-training-return`);e.note('saved_queue_handoff',{project_id:project.id,original,alternate,first,second,inputs,saved,blocked,permitted,canvas_mapping:{...canvasMapping,alignment:{before:beforeAlignment,raster_pan:rasterPan,pointer_start:panStart,pointer_end:panEnd,performed:rasterPan.x!==0||rasterPan.y!==0,existing_middle_button_pan:true},started:canvasBegan,deadline:canvasDeadline,finished:canvasFinished,source_centres_only:true},invalid_margin_422:true,alternate_queue_and_reload:true,exact_original_evaluation_return:true,actual_unsaved_annotation_blocks_actions:true,actual_readiness_gate:true,no_training_submitted:true,controlled_reports_not_model_inference:true,human_annotation_or_quality_approval:false,native});
}
test('saved queue returns to exact evaluation and gates training on saved data and policy',async({page,request,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native saved queue returns to exact evaluation and gates training on saved data and policy',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned queue handoff API ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);});

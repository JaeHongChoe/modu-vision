import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
type OwnedApi=(route:string,body?:unknown)=>Promise<any>;
type Mode='valid'|'bad-base'|'bad-overlay'|'empty'|'external';
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
test.use({actionTimeout:10_000});

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'viewer-source'),originals:Record<string,string>={};
 const images=[...Array.from({length:3},(_,i)=>({split:'train',label:'good',name:`train-good-${i}.png`,shift:i*10})),
  {split:'val',label:'good',name:'val-good.png',shift:5},{split:'test',label:'anomaly',name:'owned-part.png',shift:150}];
 for(const image of images){const dir=path.join(source,image.split,image.label);fs.mkdirSync(dir,{recursive:true});const file=path.join(dir,image.name);fs.writeFileSync(file,png(64,3,(x,y)=>[Math.min(255,30+image.shift+x),30+y,50]));originals[file]=sha(fs.readFileSync(file));}
 const project=await api('/api/project/create',{name:'Evidence viewer recovery fixture',task:'anomaly'});
 await api('/api/project/update',{source_dataset_dir:source});await api('/api/dataset/import',{folder_path:source,task:'anomaly'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/raster_models.py'),source,project.models_dir],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:60_000}).trim());
 await api('/api/flowchart/models/verify',{source_dataset_path:source,models:[{job_id:'job_fixture_raster',task:'anomaly'}]});
 const pipeline=await api('/api/flowchart/templates/single-segmentation?inspection_task=anomaly&job_id=job_fixture_raster');
 const saved=await api(`/api/flowchart/pipeline?source_dataset_path=${encodeURIComponent(source)}`,pipeline),frozen=await api(`/api/flowchart/pipelines/${saved.version_id}`);
 const testImages=(await api(`/api/dataset/images?folder_path=${encodeURIComponent(source)}&task=anomaly&split=test`)).items;expect(testImages).toHaveLength(1);
 const run=await api('/api/inspections/runs',{source_folder:source,task:'anomaly',scope:'test',pipeline:frozen,images:testImages,execution_target:'local',device:'cpu',project_id:project.id});
 const result=await api(`/api/inspections/runs/${run.run_id}/execute`,{image_path:testImages[0].file_path});expect(result.execution_device).toBe('cpu');expect(result.crops).toHaveLength(1);
 await api(`/api/inspections/runs/${run.run_id}/finish`,{status:'completed'});
 const runPath=`/api/inspections/runs/${run.run_id}`,originalPath=`${runPath}/evidence-image`,originalQuery=`${originalPath}?image_path=${encodeURIComponent(testImages[0].file_path)}`;
 const protectedRun=await api(runPath),protectedOriginal=await api(originalQuery);expect(protectedOriginal.image_sha256).toBe(originals[testImages[0].file_path]);
 const protectedHashes={run:sha(Buffer.from(JSON.stringify(protectedRun))),original:sha(Buffer.from(JSON.stringify(protectedOriginal)))};
 let mode:Mode='valid';const hits:Record<string,number>={},foreignRequests:string[]=[],writes:string[]=[];
 // Controlled response corruption exercises the real image decoder. The
 // backend's saved report, checkpoint and original file remain untouched.
 const bad='data:image/png;base64,AA==',external='https://viewer-external.invalid/owned-image.png';
 await page.route('**/api/inspections/runs/**',async route=>{
  const request=route.request(),pathname=new URL(request.url()).pathname;
  if(request.method()!=='GET'||![runPath,originalPath].includes(pathname)){await route.fallback();return;}
  // Replay the exact authenticated read captured above. In native Electron,
  // route.fetch does not pass through main's process-token injection; do not
  // expose that token to a test or disable the production authentication.
  const body=structuredClone(pathname===runPath?protectedRun:protectedOriginal);hits[`${mode}:${pathname===runPath?'report':'original'}`]=(hits[`${mode}:${pathname===runPath?'report':'original'}`]||0)+1;
  if(pathname===originalPath){if(mode==='bad-base')body.image=bad;else if(mode==='empty')body.image='';else if(mode==='external')body.image=external;}
  else if(mode==='bad-overlay'||mode==='empty'||mode==='external')for(const row of body.rows)if(row.result){
   if(mode==='external')row.image.thumbnail_url=external;
   if(mode==='bad-overlay')row.result.annotated_image=bad;
   else{row.result.annotated_image=mode==='external'?external:'';row.result.crops=mode==='empty'?[]:row.result.crops.map((crop:any)=>({...crop,crop_thumbnail:external,mask:external,anomaly_map:external}));}
  }
  await route.fulfill({status:200,json:body});
 });
 page.on('request',request=>{const pathname=new URL(request.url()).pathname;if(request.url().startsWith('https://viewer-external.invalid/'))foreignRequests.push(request.url());if(request.method()!=='GET'&&/\/api\/(train|jobs|evaluation|inspections|flowchart\/pipeline)(\/|$)/.test(pathname))writes.push(`${request.method()} ${pathname}`);});
 const open=async(next:Mode)=>{mode=next;if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Evidence viewer recovery fixture');await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(5).click();await page.locator('[aria-label="검사 이력"]').getByRole('button').filter({hasText:frozen.name}).click();await page.getByRole('button',{name:'원본·판정 근거 보기',exact:true}).click();await expect.poll(()=>hits[`${next}:original`]||0).toBeGreaterThan(0);};
 const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true}),zoom=viewer.getByLabel('근거 이미지 확대'),fit=viewer.getByRole('button',{name:'전체 맞춤',exact:true}),surface=viewer.getByLabel('근거 이미지 이동 영역'),overlay=viewer.getByLabel('근거 겹침 이미지'),opacity=viewer.getByLabel('근거 겹침 투명도');
 const transform=()=>surface.locator(':scope > div').getAttribute('style');
 const assertIdentity=async()=>{await expect(viewer).toContainText(run.run_id);await expect(viewer).toContainText(saved.version_id);await expect(viewer).toContainText(protectedOriginal.image_sha256);};
 await open('bad-base');await assertIdentity();await expect(viewer.getByRole('alert')).toContainText('선택한 근거 이미지를 읽을 수 없습니다');await expect(zoom).toBeDisabled();await expect(fit).toBeDisabled();await expect(overlay).toBeDisabled();await expect(opacity).toBeDisabled();await surface.focus();await page.keyboard.press('+');await page.keyboard.press('ArrowRight');await expect(viewer.getByRole('status')).toHaveText('100%');expect(await transform()).toContain('translate(0px, 0px) scale(1)');
 await evidence.screenshot(page,`${native?'native':'browser'}-bad-base-controls-blocked`);
 await viewer.getByLabel('근거 이미지 종류').selectOption('roi:0:map');await expect(zoom).toBeEnabled();await expect(viewer.getByRole('alert')).toHaveCount(0);expect(await viewer.locator('img').getAttribute('src')).toBe(result.crops[0].anomaly_map);
 await zoom.click();await expect(viewer.getByRole('status')).toHaveText('125%');await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);await expect(page.locator('[aria-label="선택한 검사 실행 식별자"]')).toContainText(saved.version_id.slice(0,8));
 await open('valid');await assertIdentity();await expect(zoom).toBeEnabled();await expect(viewer.getByRole('alert')).toHaveCount(0);await expect(viewer.getByRole('status')).toHaveText('100%');expect(await viewer.locator('img').first().getAttribute('src')).toBe(protectedOriginal.image);
 await overlay.selectOption('overlay');await expect(opacity).toBeEnabled();await opacity.fill('0.25');await expect(viewer.getByAltText('겹침 저장된 판정 overlay')).toHaveCSS('opacity','0.25');await viewer.getByLabel('근거 ROI 라벨').uncheck();await expect(viewer.getByLabel('근거 ROI 표시').locator('text')).toHaveCount(0);await expect(viewer.getByLabel('근거 ROI 표시').locator('rect')).toHaveCount(1);
 await surface.focus();for(let i=0;i<20;i++)await page.keyboard.press('+');await expect(viewer.getByRole('status')).toHaveText('800%');for(let i=0;i<30;i++)await page.keyboard.press('-');await expect(viewer.getByRole('status')).toHaveText('25%');await page.keyboard.press('ArrowRight');await page.keyboard.press('ArrowDown');expect(await transform()).toContain('translate(30px, 30px) scale(0.25)');await page.keyboard.press('0');expect(await transform()).toContain('translate(0px, 0px) scale(1)');
 const rect=await surface.boundingBox();expect(rect).not.toBeNull();await page.mouse.move(rect!.x+80,rect!.y+80);await page.mouse.down();await page.mouse.move(rect!.x+125,rect!.y+105);await page.mouse.up();expect(await transform()).toContain('translate(45px, 25px) scale(1)');await fit.click();expect(await transform()).toContain('translate(0px, 0px) scale(1)');
 await evidence.screenshot(page,`${native?'native':'browser'}-valid-overlay-zoom-fit`);
 await viewer.getByRole('button',{name:'검사 결과로 돌아가기',exact:true}).click();await expect(viewer).toHaveCount(0);await page.getByRole('button',{name:'원본·판정 근거 보기',exact:true}).click();await expect(zoom).toBeEnabled();await expect(overlay).toHaveValue('');await expect(opacity).toHaveValue('0.5');await expect(viewer.getByLabel('근거 ROI 라벨')).toBeChecked();await expect(viewer.getByRole('status')).toHaveText('100%');
 await open('bad-overlay');await expect(zoom).toBeEnabled();await overlay.selectOption('overlay');await expect(viewer.getByRole('alert')).toContainText('겹침 이미지를 읽을 수 없습니다');await expect(opacity).toBeDisabled();await expect(zoom).toBeEnabled();expect(await viewer.locator('img').getAttribute('src')).toBe(protectedOriginal.image);await evidence.screenshot(page,`${native?'native':'browser'}-bad-overlay-base-preserved`);await overlay.selectOption('');await expect(viewer.getByRole('alert')).toHaveCount(0);await expect(zoom).toBeEnabled();
 for(const next of ['empty','external'] as const){await open(next);await assertIdentity();await expect(viewer).toContainText('검증된 저장 이미지가 없습니다');await expect(viewer.locator('img')).toHaveCount(0);await expect(viewer.getByLabel('근거 이미지 종류')).toBeDisabled();await expect(zoom).toBeDisabled();await expect(fit).toBeDisabled();await expect(overlay).toBeDisabled();await expect(opacity).toBeDisabled();await surface.focus();await page.keyboard.press('+');await expect(viewer.getByRole('status')).toHaveText('100%');await evidence.screenshot(page,`${native?'native':'browser'}-${next}-non-executable`);await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);}
 await open('valid');await assertIdentity();await expect(zoom).toBeEnabled();await expect(viewer.getByRole('alert')).toHaveCount(0);expect(await viewer.locator('img').getAttribute('src')).toBe(protectedOriginal.image);await evidence.screenshot(page,`${native?'native':'browser'}-exact-reopened-original`);
 await page.unroute('**/api/inspections/runs/**');const reread=await api(runPath),rereadOriginal=await api(originalQuery);expect(reread).toEqual(protectedRun);expect(rereadOriginal).toEqual(protectedOriginal);expect(sha(Buffer.from(JSON.stringify(reread)))).toBe(protectedHashes.run);expect(sha(Buffer.from(JSON.stringify(rereadOriginal)))).toBe(protectedHashes.original);for(const[file,hash]of Object.entries(originals))expect(sha(fs.readFileSync(file))).toBe(hash);expect(foreignRequests).toEqual([]);expect(writes).toEqual([]);
 evidence.note('viewer_safety',{actual_cpu_inspection:true,actual_app_controls:true,app_training:false,model_quality_approval:false,fixture,run_id:run.run_id,saved_version_id:saved.version_id,original_sha256:protectedOriginal.image_sha256,protectedHashes,rereadHashes:{run:sha(Buffer.from(JSON.stringify(reread))),original:sha(Buffer.from(JSON.stringify(rereadOriginal)))},originals,source_unchanged:true,saved_run_unchanged:true,transport_corruption_only:true,modes:hits,foreignRequests,writes,base_decode_error:true,overlay_decode_error:true,empty_and_external_refused:true,zoom_bounds:[25,800],keyboard_pan:[30,30],pointer_pan:[45,25],fit_reset:true,cancel_and_reopen_identity:true,overlay_and_roi_reset:true});
}
test('saved evidence decoder errors and empty snapshots recover without altering exact CPU report',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body)=>{const response=body===undefined?await request.get(renderer.origin+route):route.endsWith('/update')||route.endsWith('/finish')?await request.put(renderer.origin+route,{data:body}):await request.post(renderer.origin+route,{data:body});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved evidence decoder errors and empty snapshots recover without altering exact CPU report',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body)=>window.evaluate(async({port,route,body})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,body===undefined?{}:{method:route.endsWith('/update')||route.endsWith('/finish')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!response.ok)throw Error(`Owned viewer fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body});await exercise(window,workspace,evidence,api,true);
});

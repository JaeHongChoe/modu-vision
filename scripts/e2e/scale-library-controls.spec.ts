import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Probe=(route:string)=>Promise<{status:number;body:string}>;
const digest=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const relative=(index:number)=>`metadata/entry-${String(index).padStart(6,'0')}.png`;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,probe:Probe,native:boolean,url?:string){
 const name='100k metadata-only library qualification';
 await api('/api/project/create',{name,task:'classification'});
 const current=await api('/api/project/current'),context=await api('/api/context');
 const helper=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/scale_library_metadata.py');
 expect(fs.existsSync(helper),'Owned scale metadata fixture helper is required').toBe(true);
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[helper,workspace.root,JSON.stringify({...current,project_context:context.project_context})],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData},encoding:'utf8',timeout:30_000}).trim());
 expect(fixture.metadata_rows).toBe(100_000);expect(fixture.actual_images).toBe(3);expect(fixture.verified_all).toBe(false);
 await api('/api/project/update',{source_dataset_dir:fixture.source_root},'PUT');
 const original=[...workspace.images.map(row=>({path:row.path,sha256:row.sha256})),...fixture.source_hashes];
 const pages:any[]=[],failures:any[]=[],requests:any[]=[],pending:Promise<void>[]=[],prohibited:string[]=[],pageErrors:string[]=[];
 page.on('pageerror',error=>pageErrors.push(error.message));
 page.on('request',request=>{const u=new URL(request.url());if(u.pathname==='/api/dataset/library/images')requests.push({q:u.searchParams.get('q'),cursor:u.searchParams.get('cursor'),limit:Number(u.searchParams.get('limit'))});if(request.method()==='POST'&&/\/(training\/start|compute\/jobs|train)$/.test(u.pathname))prohibited.push(u.pathname);});
 page.on('response',response=>{const u=new URL(response.url());if(u.pathname!=='/api/dataset/library/images')return;pending.push((async()=>{const body=await response.json();const row={q:u.searchParams.get('q'),state:u.searchParams.get('state'),cursor:u.searchParams.get('cursor'),limit:Number(u.searchParams.get('limit')),status:response.status(),...body};(response.ok()?pages:failures).push(row);})());});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('button',{name:/05.*플로우차트/}).click();await page.getByRole('button',{name:'이미지 변경...'}).click();};
 await navigate();const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),grid=picker.getByRole('list',{name:'데이터 버전 이미지'}),search=picker.getByLabel('이미지 검색',{exact:true});
 await expect(grid.getByRole('listitem').first()).toBeVisible();
 let maximumDom=0;
 const observeDom=()=>grid.evaluate(element=>{const observed={maximum:0};(element as any).__scaleRows=observed;const update=()=>{observed.maximum=Math.max(observed.maximum,element.querySelectorAll('[role="listitem"]').length);};new MutationObserver(update).observe(element,{childList:true,subtree:true});update();});
 await observeDom();
 const unfiltered=()=>pages.filter(row=>row.q===null&&row.state===null);
 await expect.poll(()=>unfiltered().length).toBeGreaterThanOrEqual(1);
 for(let count=2;count<=3;count++){
  await grid.evaluate(element=>{element.scrollTop=element.scrollHeight;element.dispatchEvent(new Event('scroll',{bubbles:true}));});
  await expect.poll(()=>unfiltered().length,{timeout:15_000}).toBeGreaterThanOrEqual(count);
  expect(await grid.getByRole('listitem').count()).toBeLessThanOrEqual(120);
 }
 const first=unfiltered().slice(0,3);expect(first).toHaveLength(3);
 for(let index=0;index<3;index++){
  expect(first[index].status).toBe(200);expect(first[index].items.map((row:any)=>row.relative_path)).toEqual(Array.from({length:120},(_,offset)=>relative(index*120+offset)));
  expect(first[index].items).toHaveLength(120);expect(first[index].next_cursor).toBeTruthy();
  if(index){expect(first[index].cursor).toBe(first[index-1].next_cursor);expect(first[index].items[0].relative_path>first[index-1].items.at(-1).relative_path).toBe(true);}
 }
 expect(new Set(first.flatMap(row=>row.items.map((item:any)=>item.image_uuid))).size).toBe(360);
 await search.fill('entry-099999.png');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText('entry-099999.png');
 const tail=await api('/api/dataset/library/images?q=entry-099999.png&limit=120');expect(tail.items).toHaveLength(1);expect(tail.items[0].image_uuid).toBe(fixture.tail.image_uuid);expect(tail.items[0].sha256).toBe(fixture.tail.sha256);expect(tail.items[0].valid).toBe(true);
 await search.fill('no-match-scale-qualification');await expect(grid.getByRole('listitem')).toHaveCount(0);await expect(picker).toContainText('조건에 맞는 이미지가 없습니다.');
 await search.fill('entry-099999.png');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid.getByRole('listitem')).toBeEnabled();await grid.getByRole('listitem').click();maximumDom=await grid.evaluate(element=>(element as any).__scaleRows.maximum);await picker.getByRole('button',{name:'선택 확정'}).click();await expect(picker).toBeHidden();
 await navigate();await observeDom();await expect(picker).toContainText('선택: metadata/entry-099999.png');await expect(picker.getByRole('button',{name:'선택 확정'})).toBeEnabled();
 await search.fill('entry-099999.png');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid.getByRole('listitem')).toHaveAttribute('aria-pressed','true');
 const saved=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,value])=>({key,value:JSON.parse(value)})));expect(saved).toHaveLength(1);expect(saved[0].value.imageUuid).toBe(fixture.tail.image_uuid);expect(saved[0].value.sha256).toBe(fixture.tail.sha256);
 await evidence.screenshot(page,`${native?'native':'browser'}-100k-tail-reopened`);
 // No image is implied for metadata-only rows. Check actual filesystem/API failure independently.
 await search.fill('entry-000001.png');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText('READ_ERROR');
 const missing=await api('/api/dataset/library/images?q=entry-000001.png&limit=120');expect(missing.items).toHaveLength(1);expect(missing.items[0].valid).toBe(false);expect(missing.items[0].sha256).toBeNull();expect(fs.existsSync(missing.items[0].file_path)).toBe(false);
 const missingThumbnail=await probe(`/api/dataset/thumbnail/entry-000001.png?file_path=${encodeURIComponent(missing.items[0].file_path)}`);expect(missingThumbnail.status).toBe(404);expect(missingThumbnail.body).toContain('Image file not found');
 await grid.getByRole('listitem').click();
 evidence.note('missing_source_click_observation',{row:missing.items[0],actual_thumbnail:missingThumbnail,picker_text:await picker.textContent(),saved_tail:saved[0]});
 await expect(picker).toContainText('선택: metadata/entry-099999.png');await expect(picker.getByRole('alert')).toContainText('검사 대상으로 선택할 수 없습니다.');
 await evidence.screenshot(page,`${native?'native':'browser'}-100k-missing-source-refused`);
 maximumDom=Math.max(maximumDom,await grid.evaluate(element=>(element as any).__scaleRows.maximum));
 // Controlled transport responses exercise the actual fetch/error and close guards.
 // They are not an outage of the owned backend, which stays healthy for teardown.
 const remembered=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,value])=>({key,value:JSON.parse(value)})));
 const failingQuery='entry-000000.png',lateQuery='entry-000359.png';
 const transportAlert=picker.getByRole('alert').filter({hasText:'Controlled metadata picker transport failure'});
 const matchQuery=(q:string)=>(u:URL)=>u.pathname==='/api/dataset/library/images'&&u.searchParams.get('q')===q;
 const failedMatch=matchQuery(failingQuery),failedHandler=async(route:Route)=>route.fulfill({status:503,json:{detail:'Controlled metadata picker transport failure'}});
 await page.route(failedMatch,failedHandler);
 try {
  await search.fill(failingQuery);await expect(transportAlert).toBeVisible();
  await expect(grid.getByRole('listitem')).toHaveCount(0);await expect(picker).toContainText('선택: metadata/entry-099999.png');
  expect(await remembered()).toEqual(saved);
  await evidence.screenshot(page,`${native?'native':'browser'}-100k-transport-failure`);
 } finally {await page.unroute(failedMatch,failedHandler);}
 await picker.getByRole('button',{name:'다시 불러오기',exact:true}).click();
 await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText(failingQuery);await expect(transportAlert).toBeHidden();
 await grid.getByRole('listitem').click();await expect(picker).toContainText('선택: metadata/entry-000000.png');
 expect(await remembered()).toEqual(saved); // A pending choice is not a confirmed handoff.
 let releaseLate!:()=>void;
 const gate=new Promise<void>(resolve=>{releaseLate=resolve;}),lateMatch=matchQuery(lateQuery);
 let heldRequest:string|null=null,lateFulfilled=false;
 const lateHandler=async(route:Route)=>{heldRequest=route.request().url();await gate;await route.fulfill({status:409,json:{detail:'Controlled late response from closed metadata picker'}});lateFulfilled=true;};
 await page.route(lateMatch,lateHandler);
 try {
  await search.fill(lateQuery);await expect.poll(()=>heldRequest).not.toBeNull();
  await expect(picker).toContainText('불러오는 중');await picker.getByTitle('닫기 (Esc)',{exact:true}).click();await expect(picker).toBeHidden();
  expect(await remembered()).toEqual(saved);
  // Reopen before releasing the old reply: a stale callback must not replace
  // this new library with the legacy unavailable-revision path.
  await page.getByRole('button',{name:'이미지 변경...'}).click();await expect(picker).toBeVisible();
  await expect(grid.getByRole('listitem').first()).toBeVisible();await observeDom();await expect(picker).toContainText('선택: metadata/entry-099999.png');
  const lateResponse=page.waitForResponse(response=>response.url()===heldRequest&&response.status()===409);
  releaseLate();await lateResponse;await expect.poll(()=>lateFulfilled).toBe(true);
  await page.evaluate(()=>new Promise<void>(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve()))));
  await expect(picker.getByRole('button',{name:'검증된 데이터 버전',exact:true})).toBeVisible();
  await expect(picker.getByRole('alert')).toBeHidden();await expect(grid.getByRole('listitem').first()).toBeVisible();
  await expect(picker).toContainText('선택: metadata/entry-099999.png');expect(await remembered()).toEqual(saved);
  await evidence.screenshot(page,`${native?'native':'browser'}-100k-cancel-late-response-guarded`);
 } finally {releaseLate();await page.unroute(lateMatch,lateHandler);}
 maximumDom=Math.max(maximumDom,await grid.evaluate(element=>(element as any).__scaleRows.maximum));
 await picker.getByRole('button',{name:'선택 확정'}).click();await expect(picker).toBeHidden();
 const identity=page.getByRole('region',{name:'플로우 식별 정보'});
 await expect(identity).toContainText(fixture.tail.path);
 await page.getByRole('tab',{name:'테스트',exact:true}).click();
 const flowTest=page.getByRole('tabpanel',{name:'테스트',exact:true});await expect(flowTest).toBeVisible();
 await flowTest.getByText('다음 검사 이미지 미리보기',{exact:true}).click();
 const preview=flowTest.getByAltText('다음 검사 이미지 원본 보기',{exact:true});await expect(preview).toBeVisible();
 await expect.poll(()=>preview.evaluate(image=>(image as HTMLImageElement).naturalWidth)).toBe(32);
 const previewUrl=await preview.getAttribute('src');expect(previewUrl).toBeTruthy();
 expect(new URL(previewUrl!,page.url()).searchParams.get('file_path')).toBe(fixture.tail.path);
 // Read through the renderer's real network session. Native Electron attaches
 // the backend token there; Playwright's separate request client does not.
 const previewResponse=await page.evaluate(async url=>{const response=await fetch(url);return {status:response.status,bytes:Array.from(new Uint8Array(await response.arrayBuffer()))};},new URL(previewUrl!,page.url()).href);
 expect(previewResponse.status).toBe(200);
 const previewBytes=Buffer.from(previewResponse.bytes),previewSha=crypto.createHash('sha256').update(previewBytes).digest('hex');
 // The thumbnail is JPEG, so its bytes differ from the original PNG. Decode
 // both independently to verify the actual downstream pixels and source hash.
 const previewProof=JSON.parse(execFileSync(harness.resolvePython(),['-c','import hashlib,io,json,sys;from pathlib import Path;from PIL import Image;p=Path(sys.argv[1]);s=Image.open(p).convert("RGB");v=Image.open(io.BytesIO(sys.stdin.buffer.read())).convert("RGB");print(json.dumps({"source_sha256":hashlib.sha256(p.read_bytes()).hexdigest(),"source_size":s.size,"preview_size":v.size,"source_rgb":s.getpixel((16,16)),"preview_rgb":v.getpixel((16,16))}))',fixture.tail.path],{input:previewBytes,encoding:'utf8',timeout:10_000}).trim());
 expect(previewProof.source_sha256).toBe(fixture.tail.sha256);expect(previewProof.preview_size).toEqual([32,32]);expect(previewProof.source_size).toEqual([32,32]);
 for(let channel=0;channel<3;channel++)expect(Math.abs(previewProof.preview_rgb[channel]-previewProof.source_rgb[channel])).toBeLessThanOrEqual(3);
 expect(await remembered()).toEqual(saved);await expect(flowTest.getByRole('button',{name:'선택 이미지 검사',exact:true})).toBeDisabled();
 await evidence.screenshot(page,`${native?'native':'browser'}-100k-exact-identity-handoff`);
 evidence.note('metadata_scale_picker_pending_dimensions',{action:'U030.metadata-scale-picker',error:{controlled_http_status:503,query:failingQuery,visible_error:'Controlled metadata picker transport failure',explicit_retry_succeeded:true,confirmed_selection_unchanged:true},cancel:{query:lateQuery,held_request:heldRequest,closed_before_late_response:true,controlled_late_status:409,reopened_library_remained_available:true,unconfirmed_selection_discarded:true,confirmed_selection_unchanged:true},handoff:{image_uuid:saved[0].value.imageUuid,relative_path:saved[0].value.relativePath,sha256:saved[0].value.sha256,file_path:fixture.tail.path,preview_url:previewUrl,actual_preview_sha256:previewSha,previewProof,actual_preview_width:32,downstream_panel:'FlowchartStudio test image preview',inspection_executed:false},actual_browser_source_ui:true,controlled_transport_fixture:true,metadata_only:true,metadata_rows:100_000,actual_images:3,model_quality_approved:false,target_execution_verified:false});
 await Promise.all(pending);expect(pages.every(row=>row.items.length<=120&&row.limit<=120&&row.status===200)).toBe(true);expect(requests.every(row=>row.limit<=120)).toBe(true);
 expect(failures.map(row=>({q:row.q,status:row.status}))).toEqual([{q:failingQuery,status:503},{q:lateQuery,status:409}]);
 expect(maximumDom).toBeLessThanOrEqual(120);
 for(const row of original)expect(digest(row.path)).toBe(row.sha256);
 expect(pageErrors).toEqual([]);expect(prohibited).toEqual([]);
 evidence.note('scale_library_qualification',{metadata_only:true,metadata_rows:fixture.metadata_rows,actual_images:fixture.actual_images,fixture,three_keyset_pages:first.map(row=>({cursor:row.cursor,next_cursor:row.next_cursor,first:row.items[0].relative_path,last:row.items.at(-1).relative_path,rows:row.items.length})),maximum_dom_rows:maximumDom,maximum_response_rows:Math.max(...pages.map(row=>row.items.length)),requests,controlled_failures:failures,saved,missing_thumbnail:missingThumbnail,source_hashes:original,source_unchanged:true,page_errors:pageErrors,prohibited,actual_ui_and_backend:true,actual_model_inference:false,model_quality_approved:false,target_tact_qualified:false,soak_72h_completed:false});
}

test('100k metadata pages through the actual library picker and restores the exact tail image',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),`Owned API ${route}: HTTP ${response.status()}`).toBe(true);return response.json();};
 const probe:Probe=async(route)=>{const response=await request.get(renderer.origin+route);return {status:response.status(),body:await response.text()};};
 await exercise(page,workspace,evidence,api,probe,false,renderer.url);
});
test('native 100k metadata picker preserves keyset progress and exact reopened identity',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();
 const raw=(route:string,body?:unknown,method?:string)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return {status:response.status,body:await response.text()};},{port:backend.port,route,body,method});
 const api:Api=async(route,body,method)=>{const response=await raw(route,body,method);expect(response.status,`Owned API ${route}`).toBeLessThan(400);return JSON.parse(response.body);};
 await exercise(window,workspace,evidence,api,(route)=>raw(route),true);
});

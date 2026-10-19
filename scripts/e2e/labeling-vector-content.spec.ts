import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const hash=(value:Buffer)=>crypto.createHash('sha256').update(value).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'vector-source');fs.mkdirSync(source,{recursive:true});const imagePath=path.join(source,'part.png');
 fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,100]));const originalSha=hash(fs.readFileSync(imagePath));
 const project=await api('/api/project/create',{name:'Vector content fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Vector content fixture');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 // Wait for the actual source decode and focus-layout Fit before requesting 1:1.
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 await page.getByTitle('실제 크기 (1:1)',{exact:true}).click();
 await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 const point=async(x:number,y:number)=>{const b=(await page.locator('[data-canvas-container]').boundingBox())!;return{x:b.x+(b.width-256)/2+x,y:b.y+(b.height-256)/2+y};};
 const drag=async(x1:number,y1:number,x2:number,y2:number)=>{const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});await page.mouse.up();};
 const query=`/api/annotations/part?file_path=${encodeURIComponent(imagePath)}`;
 const save=async()=>{const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'변경 저장',exact:true}).click();expect((await reply).status()).toBe(200);return api(query);};
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();await drag(20,20,70,60);const boxSaved=await save();
 const box=boxSaved.annotations.find((a:any)=>a.type==='bbox');expect(box.label).toBe('Solder Bridge');expect(box.category_id).toBe(1);expect(box.bbox).toEqual([20,20,70,60]);
 await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await page.getByTitle('다각형 폴리곤 (Polygon - 4)',{exact:true}).click();
 await page.getByTitle('실제 크기 (1:1)',{exact:true}).click();
 const polygon=[[120,30],[180,30],[150,80]];for(const [x,y] of polygon){const p=await point(x,y);await page.mouse.click(p.x,p.y);}await page.keyboard.press('Enter');const polygonSaved=await save();
 const poly=polygonSaved.annotations.find((a:any)=>a.type==='polygon');expect(poly.label).toBe('Scratch');expect(poly.category_id).toBe(2);expect(poly.polygon).toEqual(polygon);expect(poly.points).toEqual(polygon);
 await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();await page.getByTitle('실제 크기 (1:1)',{exact:true}).click();await drag(70,150,150,190);
 await page.getByLabel('객체 독립 방향 라벨',{exact:true}).fill('315');
 const angle=page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton');await angle.fill('30');
 const rotatedSaved=await save();const rotated=rotatedSaved.annotations.find((a:any)=>a.type==='rotated_bbox');expect(rotated.label).toBe('Crack');expect(rotated.category_id).toBe(3);expect(rotated.rotated_bbox).toEqual([110,170,80,40,30]);expect(rotated.direction_deg).toBe(315);
 // A real keyboard nudge preserves the independent direction and angle.
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();await page.keyboard.press('Shift+ArrowRight');const shiftedSaved=await save();const shifted=shiftedSaved.annotations.find((a:any)=>a.type==='rotated_bbox');
 expect(shifted.rotated_bbox).toEqual([120,170,80,40,30]);expect(shifted.direction_deg).toBe(315);
 expect(shiftedSaved.annotations.find((a:any)=>a.type==='bbox').bbox).toEqual(box.bbox);expect(shiftedSaved.annotations.find((a:any)=>a.type==='polygon').polygon).toEqual(polygon);
 await page.getByTitle('라벨 삭제',{exact:true}).nth(0).click();const deleted=await save();expect(deleted.annotations).toHaveLength(2);expect(deleted.annotations.some((a:any)=>a.type==='bbox')).toBe(false);
 await page.getByTitle('실행 취소 (Ctrl+Z)',{exact:true}).click();const restored=await save();expect(restored.annotations).toEqual(shiftedSaved.annotations);
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Scratch 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Crack 1$/})).toBeVisible();
 const reopened=await api(query);expect(reopened.annotations).toEqual(restored.annotations);expect(hash(fs.readFileSync(imagePath))).toBe(originalSha);
 const raster=await page.evaluate(async data=>{const img=new Image();await new Promise<void>((resolve,reject)=>{img.onload=()=>resolve();img.onerror=()=>reject(Error('Saved vector raster decode failed'));img.src=data;});const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;const ctx=canvas.getContext('2d')!;ctx.drawImage(img,0,0);return{width:img.width,height:img.height,classes:[[45,40],[150,45],[120,170]].map(([x,y])=>ctx.getImageData(x,y,1,1).data[0])};},'data:image/png;base64,'+fs.readFileSync(reopened.mask_file).toString('base64'));
 expect(raster).toEqual({width:256,height:256,classes:[1,2,3]});
 // Select the reopened OBB through its actual inspector row, then check the two independent values.
 await page.getByTitle('라벨 삭제',{exact:true}).nth(2).locator('..').locator('..').click();
 await expect(page.getByLabel('객체 독립 방향 라벨',{exact:true})).toHaveValue('315');await expect(angle).toHaveValue('30');
 await evidence.screenshot(page,native?'native-vector-labels-reopened':'browser-vector-labels-reopened');
 evidence.note('vector_content',{project_id:project.id,image_path:imagePath,image_sha256:originalSha,annotation_snapshot:reopened,
  expected_bbox:[20,20,70,60],expected_polygon:polygon,expected_rotated_bbox:[120,170,80,40,30],expected_direction_deg:315,
  actual_ui_draw:true,actual_keyboard_nudge:true,delete_undo_exact:true,reopened_exact:true,original_unchanged:true,
  saved_raster_centers:raster,representative_annotation_quality:false,gpu_or_model_training:false});
}
test('bbox polygon OBB and independent direction retain exact saved geometry',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native bbox polygon OBB and independent direction retain exact saved geometry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned vector fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

// Source Electron only: ordinary saved-project navigation, not installed-native acceptance.
type VectorHandoffContext={workspace_id:string;project_id:string;actor_id:string;mode:'local'|'team'};
type VectorHandoffProject={id:string;name:string;project_dir:string;source_dataset_dir:string;annotations_dir:string;dataset_dir:string;models_dir:string;reports_dir:string};
type VectorHandoffOwner={name:string;source:string;image:string;sha256:string;color:number[];project:VectorHandoffProject;context:VectorHandoffContext;angle:number;direction:number;box:number[];saved?:any};
const vectorHandoffRaw7=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
function vectorHandoffSecondary(primary:unknown,secondary:unknown){
 if(primary&&typeof primary==='object')try{Object.defineProperty(primary,'vector_handoff_secondary',{value:secondary,configurable:true});}catch{/* Keep the original thrown object even when it is frozen. */}
}
function vectorHandoffFile(file:string){
 const named=fs.lstatSync(file,{bigint:true});expect(named.isFile()).toBe(true);expect(named.isSymbolicLink()).toBe(false);expect(named.nlink).toBe(1n);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let failed=false,primary:unknown;
 try{const pre=fs.fstatSync(fd,{bigint:true});expect(vectorHandoffRaw7(pre)).toEqual(vectorHandoffRaw7(named));const raw=fs.readFileSync(fd);
  expect(vectorHandoffRaw7(fs.fstatSync(fd,{bigint:true}))).toEqual(vectorHandoffRaw7(pre));expect(vectorHandoffRaw7(fs.lstatSync(file,{bigint:true}))).toEqual(vectorHandoffRaw7(pre));
  expect(BigInt(raw.length)).toBe(pre.size);return{raw7:vectorHandoffRaw7(pre),sha256:hash(raw),size:raw.length};
 }catch(error){failed=true;primary=error;throw error;}finally{try{fs.closeSync(fd);}catch(error){if(!failed)throw error;vectorHandoffSecondary(primary,error);}}
}
function vectorHandoffTree(root:string){
 expect(fs.realpathSync(root)).toBe(path.resolve(root));const rows:Record<string,unknown>={};
 const visit=(at:string)=>{const before=fs.lstatSync(at,{bigint:true}),rel=path.relative(root,at)||'.';expect(before.isSymbolicLink()).toBe(false);
  if(before.isDirectory()){const names=fs.readdirSync(at).sort();rows[rel]={kind:'directory',raw7:vectorHandoffRaw7(before),names};for(const name of names)visit(path.join(at,name));
   expect(fs.readdirSync(at).sort()).toEqual(names);expect(vectorHandoffRaw7(fs.lstatSync(at,{bigint:true}))).toEqual(vectorHandoffRaw7(before));
  }else{expect(before.isFile()).toBe(true);rows[rel]={kind:'file',...vectorHandoffFile(at)};}};
 visit(root);return rows;
}
function vectorHandoffContext(encoded:string|null|undefined,ownerId:string){
 expect(encoded).toBeTruthy();const c=JSON.parse(encoded!);expect(Object.keys(c).sort()).toEqual(['actor_id','mode','project_id','workspace_id']);expect(c.project_id).toBe(ownerId);
 expect(c.mode).toBe('local');for(const key of ['workspace_id','project_id','actor_id'])expect(c[key]).toMatch(/^[A-Za-z0-9_.:-]{1,128}$/);return c as VectorHandoffContext;
}
function vectorHandoffGeometry(snapshot:any,owner:VectorHandoffOwner,nudged:boolean){
 expect(snapshot.image_id).toBe('part');expect(snapshot.image_width).toBe(256);expect(snapshot.image_height).toBe(256);expect(snapshot.annotations).toHaveLength(1);
 const item=snapshot.annotations[0];expect(item.type).toBe('rotated_bbox');expect(item.label).toBe('Crack');expect(item.category_id).toBe(3);
 expect(item.rotated_bbox).toEqual([owner.box[0]+(nudged?10:0),owner.box[1],owner.box[2],owner.box[3],owner.angle]);expect(item.direction_deg).toBe(owner.direction);
 expect(snapshot.metadata.image_uuid).toBeTruthy();expect(snapshot.metadata.file_path).toBe(owner.image);expect(snapshot.metadata.content_hash).toBe(owner.sha256);
 expect(snapshot.metadata.width).toBe(256);expect(snapshot.metadata.height).toBe(256);expect(Number.isSafeInteger(snapshot.metadata.revision)).toBe(true);
}
test('native OBB angle and independent direction nudge stay owned through A B A project handoff',{tag:'@electron'},async({electronSession,workspace,evidence},testInfo)=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();const proof:any={scope:'source_electron_saved_project_handoff',cells:['U020.independent-box-angle.handoff','U020.independent-direction-nudge.handoff'],setup_requests:[],visits:[],saves:[],page_errors:[],console_errors:[],request_failures:[],acceptance:false,installed_native:false,model_training:false,representative_annotation_quality:false};
 const onError=(error:Error)=>proof.page_errors.push(error.message),onConsole=(message:import('@playwright/test').ConsoleMessage)=>{if(message.type()==='error')proof.console_errors.push(message.text());},onFailed=(request:import('@playwright/test').Request)=>proof.request_failures.push({url:request.url(),method:request.method(),failure:request.failure()});
 page.on('pageerror',onError);page.on('console',onConsole);page.on('requestfailed',onFailed);let primary:unknown,failed=false;
 const api=async(route:string,body?:unknown,method?:string,context?:VectorHandoffContext)=>{
  const result=await page.evaluate(async({port,route,body,method,context})=>{const headers:Record<string,string>={'Content-Type':'application/json'};if(context){headers['X-Vision-Project']=context.project_id;headers['X-Vision-Context']=JSON.stringify(context);}
   const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{body:JSON.stringify(body)})});const raw=await response.text();return{status:response.status,context:response.headers.get('X-Vision-Context'),raw};
  },{port:backend.port,route,body,method,context});expect(result.status,result.raw).toBe(200);const value=JSON.parse(result.raw);
  if(context)expect(vectorHandoffContext(result.context,context.project_id)).toEqual(context);proof.setup_requests.push({route,method:method||(body===undefined?'GET':'POST'),body:body??null,request_context:context??null,response:result});return{value,context:result.context};
 };
 const inspectResponse=async(response:import('@playwright/test').Response,owner:VectorHandoffOwner,context:VectorHandoffContext)=>{
  expect(response.status()).toBe(200);expect(await response.finished()).toBeNull();const request=response.request(),headers=await request.allHeaders();expect(headers['x-vision-project']).toBe(owner.project.id);
  expect(vectorHandoffContext(headers['x-vision-context'],owner.project.id)).toEqual(context);expect(vectorHandoffContext(await response.headerValue('x-vision-context'),owner.project.id)).toEqual(context);
  return{url:response.url(),method:request.method(),headers,body:request.postData(),response:await response.json(),status:response.status()};
 };
 const query=(owner:VectorHandoffOwner)=>`/api/annotations/part?file_path=${encodeURIComponent(owner.image)}`;
 const read=async(owner:VectorHandoffOwner)=>{const answer=await api(query(owner),undefined,'GET',owner.context);return answer.value;};
 const open=async(owner:VectorHandoffOwner)=>{
  // Own both waiters immediately, including failures before the actual click.
  const labels=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/part'&&new URL(r.url()).searchParams.get('file_path')===owner.image&&r.request().method()==='GET',{timeout:10_000});void labels.catch(()=>{});
  const selected=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/open'&&r.request().method()==='POST'&&r.request().postDataJSON()?.project_dir===owner.project.project_dir,{timeout:10_000});void selected.catch(()=>{});
  await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await dialog.getByRole('button',{name:'폴더에서 열기',exact:true}).click();await dialog.getByPlaceholder('/path/to/project',{exact:true}).fill(owner.project.project_dir);await dialog.getByRole('button',{name:'프로젝트 열기',exact:true}).click();
  const reply=await selected;expect(reply.status()).toBe(200);expect(await reply.finished()).toBeNull();expect(reply.request().postDataJSON()).toEqual({project_dir:owner.project.project_dir});const opened=await reply.json();expect(opened.id).toBe(owner.project.id);expect(opened.source_dataset_dir).toBe(owner.source);expect(opened.project_dir).toBe(owner.project.project_dir);owner.project=opened;owner.context=vectorHandoffContext(await reply.headerValue('x-vision-context'),owner.project.id);
  await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(owner.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
  const response=await inspectResponse(await labels,owner,owner.context);expect(response.response.metadata.file_path).toBe(owner.image);expect(response.response.metadata.content_hash).toBe(owner.sha256);
  await expect(page.getByText('기존 라벨을 불러오는 중입니다.',{exact:true})).toHaveCount(0);await expect(page.getByText(/기존 라벨 조회 실패:/)).toHaveCount(0);
  // Keep the original real decode/layout gate before requesting 1:1.
  await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0),{timeout:10_000}).toBeGreaterThan(100);
  await page.getByTitle('실제 크기 (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');proof.visits.push({project:opened,context:owner.context,annotation_response:response});return response.response;
 };
 const select=async()=>{await expect(page.getByTitle('라벨 삭제',{exact:true})).toHaveCount(1);await page.getByTitle('라벨 삭제',{exact:true}).locator('..').locator('..').click();};
 const angle=()=>page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton');
 const save=async(owner:VectorHandoffOwner,before:any,nudged:boolean)=>{
  const wire=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST'&&r.request().postDataJSON()?.image_path===owner.image,{timeout:10_000});void wire.catch(()=>{});
  await page.getByRole('button',{name:'변경 저장',exact:true}).click();const recorded=await inspectResponse(await wire,owner,owner.context),sent=JSON.parse(recorded.body!);expect(sent.image_id).toBe('part');expect(sent.image_path).toBe(owner.image);expect(sent.expected_revision).toBe(before.metadata.revision);expect(sent.image_width).toBe(256);expect(sent.image_height).toBe(256);expect(sent.annotations).toHaveLength(1);
  expect(sent.annotations[0].rotated_bbox).toEqual([owner.box[0]+(nudged?10:0),owner.box[1],owner.box[2],owner.box[3],owner.angle]);expect(sent.annotations[0].direction_deg).toBe(owner.direction);expect(recorded.response.status).toBe('saved');expect(recorded.response.metadata.image_uuid).toBe(before.metadata.image_uuid);expect(recorded.response.metadata.revision).toBeGreaterThan(before.metadata.revision);
  await expect.poll(async()=>{const saved=page.getByRole('button',{name:'저장됨',exact:true});return await saved.count()===1&&await saved.isVisible()&&await saved.isEnabled();},{timeout:20_000}).toBe(true);const snapshot=await read(owner);vectorHandoffGeometry(snapshot,owner,nudged);expect(snapshot.metadata.image_uuid).toBe(before.metadata.image_uuid);expect(snapshot.metadata.revision).toBe(recorded.response.metadata.revision);expect(snapshot.annotations).toEqual(sent.annotations.map((a:any)=>({...a,polygon:a.polygon??null,points:a.points??null,mask_rle:a.mask_rle??null,is_normal:a.is_normal??null,text:null})));
  proof.saves.push({project_id:owner.project.id,context:owner.context,nudged,transport:recorded,snapshot});return snapshot;
 };
 const custody=(owner:VectorHandoffOwner)=>({source:vectorHandoffTree(owner.source),annotations:vectorHandoffTree(owner.project.annotations_dir),dataset:vectorHandoffTree(owner.project.dataset_dir),models:vectorHandoffTree(owner.project.models_dir),reports:vectorHandoffTree(owner.project.reports_dir),project_manifest:vectorHandoffFile(path.join(owner.project.project_dir,'project.json'))});
 try{
  const owners:VectorHandoffOwner[]=[];
  for(const settings of [{key:'A',angle:30,direction:315,box:[110,170,80,40],color:[30,80,100]},{key:'B',angle:-15,direction:45,box:[90,100,60,30],color:[180,40,120]}]){
   const source=path.join(workspace.root,`vector-handoff-source-${settings.key}`);fs.mkdirSync(source,{recursive:false});expect(fs.realpathSync(source)).toBe(path.resolve(source));const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,()=>settings.color),{flag:'wx'});const sha256=vectorHandoffFile(image).sha256;
   const projectDir=path.join(workspace.userData,'projects',`vector-handoff-${settings.key}`);expect(fs.existsSync(projectDir)).toBe(false);const made=await api('/api/project/create',{name:`Vector handoff ${settings.key}`,task:'segmentation',project_dir:projectDir});expect(made.value.project_dir).toBe(projectDir);expect(fs.realpathSync(projectDir)).toBe(path.resolve(projectDir));const context=vectorHandoffContext(made.context,made.value.id);
   const updated=await api('/api/project/update',{source_dataset_dir:source},'PUT',context);expect(updated.value.source_dataset_dir).toBe(source);await api('/api/dataset/import',{folder_path:source,task:'segmentation'},'POST',context);
   owners.push({name:`Vector handoff ${settings.key}`,source,image,sha256,color:settings.color,project:updated.value,context,angle:settings.angle,direction:settings.direction,box:settings.box});
  }
  const [A,B]=owners;expect(A.project.id).not.toBe(B.project.id);expect(A.sha256).not.toBe(B.sha256);const sourceBefore=owners.map(o=>vectorHandoffTree(o.source));
  for(const owner of owners){
   const before=await open(owner);expect(before.annotations).toEqual([]);await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();
   const rect=(await page.locator('[data-canvas-container]').boundingBox())!;const [cx,cy,w,h]=owner.box;const point=(x:number,y:number)=>({x:rect.x+(rect.width-256)/2+x,y:rect.y+(rect.height-256)/2+y});const p1=point(cx-w/2,cy-h/2),p2=point(cx+w/2,cy+h/2);
   await page.mouse.move(p1.x,p1.y);await page.mouse.down();await page.mouse.move(p2.x,p2.y,{steps:8});await page.mouse.up();await select();await angle().fill(String(owner.angle));await page.getByLabel('객체 독립 방향 라벨',{exact:true}).fill(String(owner.direction));await expect(angle()).toHaveValue(String(owner.angle));await expect(page.getByLabel('객체 독립 방향 라벨',{exact:true})).toHaveValue(String(owner.direction));
   const angled=await save(owner,before,false);await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();await page.keyboard.press('Shift+ArrowRight');owner.saved=await save(owner,angled,true);await expect(angle()).toHaveValue(String(owner.angle));await expect(page.getByLabel('객체 독립 방향 라벨',{exact:true})).toHaveValue(String(owner.direction));await evidence.screenshot(page,`native-vector-handoff-${owner.name.endsWith('A')?'A':'B'}-saved`);
   const datasetKey=crypto.createHash('sha256').update(fs.realpathSync(owner.source),'utf8').digest('hex').slice(0,16);const dir=path.join(owner.project.annotations_dir,'by_dataset',datasetKey);expect(owner.saved.mask_file).toBe(path.join(dir,'masks','part.png'));const stored=JSON.parse(fs.readFileSync(path.join(dir,'part.json'),'utf8'));expect(stored.annotations).toEqual(owner.saved.annotations);expect(stored.image_width).toBe(256);expect(stored.image_height).toBe(256);
  }
  expect(A.saved.metadata.image_uuid).not.toBe(B.saved.metadata.image_uuid);expect(owners.map(o=>vectorHandoffTree(o.source))).toEqual(sourceBefore);const beforeCustody=owners.map(custody);const finalReads:any[]=[];
  for(const [visit,owner] of [A,B,A].entries()){
   const snapshot=await open(owner);vectorHandoffGeometry(snapshot,owner,true);expect(snapshot.annotations).toEqual(owner.saved.annotations);expect(snapshot.metadata.image_uuid).toBe(owner.saved.metadata.image_uuid);expect(snapshot.metadata.revision).toBe(owner.saved.metadata.revision);expect(snapshot.mask_file).toBe(owner.saved.mask_file);await select();await expect(angle()).toHaveValue(String(owner.angle));await expect(page.getByLabel('객체 독립 방향 라벨',{exact:true})).toHaveValue(String(owner.direction));await evidence.screenshot(page,`native-vector-handoff-visit-${visit+1}`);finalReads.push({visit:visit+1,project_id:owner.project.id,context:owner.context,snapshot});
  }
  const afterCustody=owners.map(custody);expect(afterCustody).toEqual(beforeCustody);expect(owners.map(o=>vectorHandoffTree(o.source))).toEqual(sourceBefore);expect(proof.page_errors).toEqual([]);
  proof.owners=owners;proof.final_reads=finalReads;proof.source_before=sourceBefore;proof.before_custody=beforeCustody;proof.after_custody=afterCustody;proof.custody_unchanged=true;proof.actual_keyboard_nudges=2;proof.actual_angle_edits=2;proof.actual_save_posts=proof.saves.length;expect(proof.saves).toHaveLength(4);proof.actual_owning_ABA_readbacks=3;
 }catch(error){failed=true;primary=error;throw error;}finally{
  const cleanup:unknown[]=[];for(const off of [()=>page.off('pageerror',onError),()=>page.off('console',onConsole),()=>page.off('requestfailed',onFailed)])try{off();}catch(error){cleanup.push(error);if(!failed){failed=true;primary=error;}else vectorHandoffSecondary(primary,error);}
  proof.cleanup_errors=cleanup.map(String);proof.test_body_failed=failed;evidence.note('vector_project_handoff',proof);
  const proofFile=testInfo.outputPath('vector-project-handoff-proof.json');let fd:number|undefined,writeFailed=false,writePrimary:unknown;
  try{fd=fs.openSync(proofFile,fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_WRONLY,0o600);const raw=Buffer.from(JSON.stringify(proof,null,2)+'\n');let at=0;while(at<raw.length){const n=fs.writeSync(fd,raw,at,raw.length-at);if(n<=0)throw Error('Short vector proof write');at+=n;}fs.fsyncSync(fd);
  }catch(error){writeFailed=true;writePrimary=error;if(!failed){failed=true;primary=error;}else vectorHandoffSecondary(primary,error);
  }finally{if(fd!==undefined)try{fs.closeSync(fd);}catch(error){if(writeFailed)vectorHandoffSecondary(writePrimary,error);if(!failed){failed=true;primary=error;}else vectorHandoffSecondary(primary,error);}}
  if(!writeFailed)evidence.addFile(proofFile);if(failed)throw primary;
 }
});

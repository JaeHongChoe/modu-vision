import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url?:string};
type Api=(route:string,body?:unknown,method?:string)=>Promise<Reply>;
type Write={index:number;method:string;endpoint:string;body:string|null};
type SaveReply=Reply&{request:string};
const sha=(raw:Buffer)=>crypto.createHash('sha256').update(raw).digest('hex');

export function assertOwnedProjectDirectory(projectDir:string,ownedRoot:string){
 expect(path.isAbsolute(ownedRoot)).toBe(true);expect(path.normalize(ownedRoot)).toBe(ownedRoot);
 expect(path.isAbsolute(projectDir)).toBe(true);expect(path.normalize(projectDir)).toBe(projectDir);
 expect(path.dirname(projectDir)).toBe(ownedRoot);
 for(const directory of [ownedRoot,projectDir]){
  const entry=fs.lstatSync(directory);expect(entry.isSymbolicLink()).toBe(false);expect(entry.isDirectory()).toBe(true);
  expect(fs.realpathSync(directory)).toBe(directory);
 }
}

export function protectedTree(root:string):Record<string,string>{
 const tree:Record<string,string>={};
 const walk=(folder:string)=>{
  const stat=fs.lstatSync(folder);expect(stat.isSymbolicLink()).toBe(false);expect(stat.isDirectory()).toBe(true);
  for(const name of fs.readdirSync(folder).sort()){
   const file=path.join(folder,name),entry=fs.lstatSync(file);expect(entry.isSymbolicLink()).toBe(false);
   if(entry.isDirectory())walk(file);
   else{expect(entry.isFile()).toBe(true);tree[path.relative(root,file).split(path.sep).join('/')]=sha(fs.readFileSync(file));}
  }
 };
 walk(root);return tree;
}
export function assertExactAnnotations(actual:unknown,original:unknown){expect(actual).toEqual(original);}
export function assertExactSnapshot(actual:unknown,original:unknown){expect(actual).toEqual(original);}
export function assertSourceIdentity(actual:any,original:any){
 for(const key of ['image_uuid','file_path','content_hash','content_version'])expect(actual[key]).toEqual(original[key]);
}
export function assertInitialAnnotations(annotations:any[]){
 expect(annotations).toHaveLength(3);expect(new Set(annotations.map(item=>item.id)).size).toBe(3);
 for(const item of annotations){expect(typeof item.id).toBe('string');expect(item.id.length).toBeGreaterThan(0);}
 const [box,polygon,obb]=annotations;
 expect([box.type,box.label,box.category_id,box.bbox]).toEqual(['bbox','Solder Bridge',1,[20,20,70,60]]);
 expect([polygon.type,polygon.label,polygon.category_id,polygon.polygon,polygon.points]).toEqual([
  'polygon','Scratch',2,[[120,30],[180,30],[150,80]],[[120,30],[180,30],[150,80]]]);
 expect([obb.type,obb.label,obb.category_id,obb.rotated_bbox,obb.direction_deg]).toEqual([
  'rotated_bbox','Crack',3,[110,170,80,40,30],315]);
}
export function assertDeletedSubmission(annotations:any[],original:any[]){
 expect(annotations).toHaveLength(2);expect(original).toHaveLength(3);
 expect(annotations.map(item=>item.id)).toEqual(original.slice(1).map(item=>item.id));
 const [polygon,obb]=annotations;
 expect([polygon.type,polygon.label,polygon.category_id,polygon.polygon,polygon.points]).toEqual([
  'polygon','Scratch',2,[[120,30],[180,30],[150,80]],[[120,30],[180,30],[150,80]]]);
 expect([obb.type,obb.label,obb.category_id,obb.rotated_bbox,obb.direction_deg]).toEqual([
  'rotated_bbox','Crack',3,[110,170,80,40,30],315]);
}
export function assertSaveCycle(writes:Write[],status:number,source:string){
 expect([200,503]).toContain(status);expect(writes).toHaveLength(status===200?2:1);
 expect(writes[0].method).toBe('POST');expect(writes[0].endpoint).toBe('/api/annotations/save');
 if(status===200){
  expect(writes[1].method).toBe('POST');expect(writes[1].endpoint).toBe('/api/dataset/import');
  expect(writes[1].index).toBeGreaterThan(writes[0].index);
  expect(JSON.parse(writes[1].body!)).toEqual({folder_path:source,task:'segmentation',validate_images:false});
 }
}
export function assertRetry(failed:SaveReply,retry:SaveReply,detail:string){
 expect(failed.status).toBe(503);expect(JSON.parse(failed.body)).toEqual({detail});
 expect(retry.status).toBe(200);expect(JSON.parse(retry.body).status).toBe('saved');
 expect(retry.request).toBe(failed.request);
}

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'source-electron':'browser',writes:Write[]=[],calls:any[]=[],cells:any[]=[],saveCycles:any[]=[];
 const order=new WeakMap<Request,number>();let sequence=0;
 page.on('request',request=>{
  order.set(request,++sequence);const endpoint=new URL(request.url()).pathname;
  if(endpoint.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))
   writes.push({index:sequence,method:request.method(),endpoint,body:request.postData()});
 });
 const value=async(route:string,body?:unknown,method?:string)=>{
  const reply=await api(route,body,method);expect(reply.status,route).toBe(200);
  calls.push({route,method:method||(body===undefined?'GET':'POST'),request:body,...reply});return JSON.parse(reply.body);
 };
 const raw=async(route:string)=>{const reply=await api(route);expect(reply.status,route).toBe(200);return reply;};
 const screenshot=(name:string)=>evidence.screenshot(page,`${prefix}-vector-delete-undo-${name}`);
 const source=path.join(workspace.root,'delete-undo-source');fs.mkdirSync(source,{recursive:false});
 const imagePath=path.join(source,'part.png');fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,100]),{flag:'wx'});
 const imageSha=sha(fs.readFileSync(imagePath)),name='Owned vector deletion undo lifecycle';
 const project=await value('/api/project/create',{name,task:'segmentation'});
 await value('/api/project/update',{source_dataset_dir:source},'PUT');
 await value('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
 const enter=async()=>{
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});
  if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
 };
 await enter();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();
 const preludeWrites=writes.slice();
 // Native fixture setup uses the existing renderer fetch transport; the browser
 // uses its fixture request context. Neither may hide another mutation endpoint.
 expect(preludeWrites.map(row=>[row.method,row.endpoint])).toEqual(native?[
  ['POST','/api/project/create'],['PUT','/api/project/update'],['POST','/api/dataset/import']]:[]);
 if(native){
  expect(JSON.parse(preludeWrites[0].body!)).toEqual({name,task:'segmentation'});
  expect(JSON.parse(preludeWrites[1].body!)).toEqual({source_dataset_dir:source});
  expect(JSON.parse(preludeWrites[2].body!)).toEqual({folder_path:source,task:'segmentation'});
 }
 const bodyWriteStart=writes.length,current=await value('/api/project/current');
 expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
 assertOwnedProjectDirectory(current.project_dir,native?path.join(workspace.userData,'projects'):workspace.projects);
 const query=`/api/annotations/part?file_path=${encodeURIComponent(imagePath)}`;
 const metadataQuery='/api/dataset/metadata/image?image_path='+encodeURIComponent(imagePath);
 const roots={project:current.project_dir as string,source};
 const snapshot=async()=>({current:await raw('/api/project/current'),labelsets:await raw('/api/project/labelsets'),
  annotations:await raw(query),metadata:await raw(metadataQuery),
  trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)]))});
 const protect=async(label:string)=>{
  const state=await snapshot(),folder=path.join(workspace.logs,'delete-undo-protected-'+label);
  expect(fs.existsSync(folder)).toBe(false);fs.mkdirSync(folder,{recursive:true});
  for(const[key,root]of Object.entries(roots)){
   fs.cpSync(root,path.join(folder,key),{recursive:true,errorOnExist:true});
   expect(protectedTree(path.join(folder,key))).toEqual(state.trees[key]);
   for(const relative of Object.keys(state.trees[key]))evidence.addFile(path.join(folder,key,relative));
  }
  const file=path.join(folder,'snapshot.json');fs.writeFileSync(file,JSON.stringify({roots,state}),{flag:'wx'});evidence.addFile(file);
  return{state,folder};
 };
 const unchanged=async(original:Awaited<ReturnType<typeof protect>>)=>{
  const after=await snapshot();assertExactSnapshot(after,original.state);
  for(const[key,root]of Object.entries(roots))for(const relative of Object.keys(original.state.trees[key]))
   expect(fs.readFileSync(path.join(root,relative))).toEqual(fs.readFileSync(path.join(original.folder,key,relative)));
  return after;
 };
 const rows=()=>page.getByTitle('Delete annotation',{exact:true}),undo=()=>page.getByTitle('Undo (Ctrl+Z)',{exact:true}),redo=()=>page.getByTitle('Redo (Ctrl+Y)',{exact:true});
 const saveButton=()=>page.getByTestId('annotation-save-button');
 const point=async(x:number,y:number)=>{const box=await page.locator('[data-canvas-container]').boundingBox();expect(box).not.toBeNull();return{x:box!.x+(box!.width-256)/2+x,y:box!.y+(box!.height-256)/2+y};};
 const recenter=async()=>{await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');};
 const drag=async(x1:number,y1:number,x2:number,y2:number)=>{
  const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});await page.mouse.up();
 };
 const save=async(status:200|503,label:string,expectedRows:number)=>{
  const start=writes.length;
  const responseWait=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/annotations/save'&&response.request().method()==='POST');
  let importOrder:number|null=null;
  const importWait=status===200?page.waitForResponse(response=>{
   const request=response.request();if(new URL(response.url()).pathname!=='/api/dataset/import'||request.method()!=='POST')return false;
   expect(request.postDataJSON()).toEqual({folder_path:source,task:'segmentation',validate_images:false});
   importOrder=order.get(request)!;return true;
  }):null;
  const imagesWait=status===200?page.waitForResponse(response=>{
   const address=new URL(response.url());return address.pathname==='/api/dataset/images'
    &&address.searchParams.get('folder_path')===source&&address.searchParams.get('task')==='segmentation'
    &&address.searchParams.get('offset')==='0'&&importOrder!==null&&order.get(response.request())!>importOrder;
  }):null;
  await saveButton().click();const response=await responseWait;
  const reply:SaveReply={status:response.status(),body:await response.text(),url:response.url(),request:response.request().postData()!};
  expect(reply.status).toBe(status);let refresh:any=null,images:any=null;
  if(status===200){
   const refreshed=await importWait!;expect(refreshed.status()).toBe(200);
   refresh={status:refreshed.status(),body:await refreshed.text(),request:refreshed.request().postData(),order:importOrder};
   const loaded=await imagesWait!;expect(loaded.status()).toBe(200);
   images={status:loaded.status(),body:await loaded.text(),url:loaded.url(),order:order.get(loaded.request())};
   const body=JSON.parse(images.body);expect(body.total).toBe(1);expect(body.items).toHaveLength(1);expect(body.items[0].file_path).toBe(imagePath);
   await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();
   await expect(rows()).toHaveCount(expectedRows);await expect(saveButton()).toContainText('Saved');
  }
  const mutations=writes.slice(start);assertSaveCycle(mutations,status,source);
  saveCycles.push({label,reply,refresh,images,mutations});return reply;
 };
 const mask=async(label:string,saved:any,expectedClasses:number[])=>{
  expect(typeof saved.mask_file).toBe('string');const bytes=fs.readFileSync(saved.mask_file);
  const file=path.join(workspace.logs,'delete-undo-'+label+'-mask.png');fs.writeFileSync(file,bytes,{flag:'wx'});evidence.addFile(file);
  const centers=await page.evaluate(async(data)=>{
   const image=new Image();await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('Saved deletion mask decode failed'));image.src=data;});
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const context=canvas.getContext('2d')!;context.drawImage(image,0,0);
   return{width:image.width,height:image.height,classes:[[45,40],[150,45],[110,170]].map(([x,y])=>context.getImageData(x,y,1,1).data[0])};
  },'data:image/png;base64,'+bytes.toString('base64'));
  expect(centers).toEqual({width:256,height:256,classes:expectedClasses});return{file,sha256:sha(bytes),centers};
 };

 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();await recenter();await drag(20,20,70,60);
 await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await page.getByTitle('다각형 폴리곤 (Polygon - 4)',{exact:true}).click();await recenter();
 for(const[x,y]of [[120,30],[180,30],[150,80]]){const p=await point(x,y);await page.mouse.move(p.x,p.y);await expect(page.getByTestId('canvas-hud')).toContainText(new RegExp(`X:\\s*${x}\\s*px\\s*Y:\\s*${y}\\s*px`));await page.mouse.click(p.x,p.y);}
 await page.keyboard.press('Enter');
 await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();await recenter();await drag(70,150,150,190);
 await page.getByLabel('객체 독립 방향 라벨',{exact:true}).fill('315');
 await page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton').fill('30');
 await save(200,'initial-three-labels',3);const initial=await value(query);assertInitialAnnotations(initial.annotations);
 const identity=await value(metadataQuery);expect(identity.image_uuid).toBeTruthy();expect(identity.content_hash).toBe(imageSha);
 const initialMask=await mask('initial',initial,[1,2,3]);
 // Actual reload establishes no selection/history while retaining nonempty labels.
 await page.reload();await enter();await expect(rows()).toHaveCount(3);await expect(saveButton()).toContainText('Saved');
 await expect(undo()).toBeDisabled();await expect(redo()).toBeDisabled();
 const invalid=await protect('invalid'),invalidWriteStart=writes.length;
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();await page.keyboard.press('Escape');
 for(const key of ['Delete','Backspace','Control+z','Control+y'])await page.keyboard.press(key);
 await expect(rows()).toHaveCount(3);await expect(undo()).toBeDisabled();await expect(redo()).toBeDisabled();
 await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 expect(writes.slice(invalidWriteStart)).toEqual([]);const invalidAfter=await unchanged(invalid);await screenshot('invalid-no-selection-or-history');
 cells.push({action:'U030.vector-delete-undo',dimension:'invalid',before:invalid,after:invalidAfter,mutations:writes.slice(invalidWriteStart),actual_nonempty_invalid_keys:true});

 const errorBefore=await protect('before-error');await rows().nth(0).click();await expect(rows()).toHaveCount(2);
 const route='**/api/annotations/save',detail='Controlled deletion save unavailable';
 await page.route(route,request=>request.fulfill({status:503,json:{detail}}));let failed:SaveReply;
 try{
  failed=await save(503,'controlled-deletion-failure',2);expect(JSON.parse(failed.body)).toEqual({detail});
  const body=JSON.parse(failed.request);expect(body.image_id).toBe('part');expect(body.image_path).toBe(imagePath);
  expect(body.expected_revision).toBe(JSON.parse(errorBefore.state.metadata.body).revision);
  expect(body.image_width).toBe(256);expect(body.image_height).toBe(256);
  assertDeletedSubmission(body.annotations,initial.annotations);
  await expect(page.getByText('Failed: '+detail,{exact:true})).toBeVisible();await expect(saveButton()).toBeEnabled();
  const failedAfter=await unchanged(errorBefore),historyStart=writes.length;
  await undo().click();await expect(rows()).toHaveCount(3);await redo().click();await expect(rows()).toHaveCount(2);
  expect(writes.slice(historyStart)).toEqual([]);const historyAfter=await unchanged(errorBefore);
  await screenshot('error-original-baseline-and-undo-redo-retained');
  cells.push({action:'U030.vector-delete-undo',dimension:'error',before:errorBefore,failed,failedAfter,historyAfter,undo_rows:3,redo_rows:2,history_mutations:writes.slice(historyStart)});
 }finally{await page.unroute(route);}
 const retried=await save(200,'deliberate-original-deletion-retry',2);assertRetry(failed!,retried,detail);
 const deleted=await value(query);assertExactAnnotations(deleted.annotations,initial.annotations.slice(1));
 assertSourceIdentity(await value(metadataQuery),identity);const deletedMask=await mask('deleted',deleted,[0,2,3]);
 await undo().click();await expect(rows()).toHaveCount(3);await save(200,'explicit-undo-restoration',3);
 const restored=await value(query);assertExactAnnotations(restored.annotations,initial.annotations);assertSourceIdentity(await value(metadataQuery),identity);
 const restoredMask=await mask('restored',restored,[1,2,3]);

 const cancel=await protect('before-cancel'),cancelWriteStart=writes.length;await rows().nth(0).click();await expect(rows()).toHaveCount(2);
 const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
 const read=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/annotations/part'&&response.request().method()==='GET'
  &&new URL(response.url()).searchParams.get('file_path')===imagePath);
 await page.getByRole('button',{name:'현재 편집을 버리고 최신 라벨 불러오기',exact:true}).click();expect((await read).status()).toBe(200);
 await expect(rows()).toHaveCount(3);await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 expect(writes.slice(cancelWriteStart)).toEqual([]);const cancelAfter=await unchanged(cancel);await screenshot('cancel-explicit-discard-restores-server-labels');
 cells.push({action:'U030.vector-delete-undo',dimension:'cancel',before:cancel,after:cancelAfter,mutations:writes.slice(cancelWriteStart),actual_discard_latest_label_read:true,history_reset_claim:false});
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();

 const handoff=await protect('before-handoff'),handoffWriteStart=writes.length;
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
 const preparation=page.getByRole('region',{name:'공통 모델 준비'});await expect(preparation).toBeVisible();
 await expect(preparation).toContainText(name);await expect(preparation).toContainText('원본 1장');
 const atTraining=await unchanged(handoff);await preparation.getByRole('button',{name:'정답 검토',exact:true}).click();
 await expect(rows()).toHaveCount(3);const returned=await unchanged(handoff);
 expect(writes.slice(handoffWriteStart)).toEqual([]);await screenshot('handoff-real-truth-review-return');
 await page.reload();await expect(rows()).toHaveCount(3);const reopened=await unchanged(handoff);
 expect(writes.slice(handoffWriteStart)).toEqual([]);
 cells.push({action:'U030.vector-delete-undo',dimension:'handoff',before:handoff,atTraining,returned,reopened,mutations:writes.slice(handoffWriteStart),actual_stage3_and_truth_review_stage2:true});
 expect(writes.slice(bodyWriteStart).map(row=>[row.method,row.endpoint])).toEqual([
  ['POST','/api/annotations/save'],['POST','/api/dataset/import'],['POST','/api/annotations/save'],
  ['POST','/api/annotations/save'],['POST','/api/dataset/import'],['POST','/api/annotations/save'],['POST','/api/dataset/import']]);
 expect(sha(fs.readFileSync(imagePath))).toBe(imageSha);for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
 const notes={sourceElectron:native,project,current,identity,imagePath,image_sha256:imageSha,initial,deleted,restored,
  initialMask,deletedMask,restoredMask,preludeWrites,calls,writes,saveCycles,cells,
  actual_pointer_delete_undo_redo_discard_and_clean_handoff:true,controlled503_then_deliberate_original200:true,
  declared_post_save_refreshes:3,complete_mutation_inventory:true,full_unfiltered_original_api_and_byte_trees:true,
  raster_centers_only_not_full_independent_oracle:true,training_inference_download_or_job_execution:false,
  representative_human_annotation_quality:false,whole_feature_installed_native_or_release_acceptance:false};
 const proof=path.join(workspace.logs,'vector-delete-undo-lifecycle-proof.json');fs.writeFileSync(proof,JSON.stringify(notes),{flag:'wx'});evidence.addFile(proof);
 evidence.note('vector_delete_undo_lifecycle',notes);
}
test('vector delete undo invalid input failure cancellation and handoff preserve original saved labels',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});return{status:response.status(),body:await response.text(),url:response.url()};};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native vector delete undo invalid input failure cancellation and handoff preserve original saved labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const backend=await electronSession.waitForBackend(),window=electronSession.window;
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return{status:response.status,body:await response.text(),url:response.url};
 },{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

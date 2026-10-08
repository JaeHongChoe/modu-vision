import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url:string};
type Api=(route:string,body?:unknown,method?:string)=>Promise<Reply>;
const sha=(data:Buffer)=>crypto.createHash('sha256').update(data).digest('hex');
const tools=[
 {action:'vector-bbox-draw',title:'바운딩 박스 (BBox - 2)',type:'bbox',label:'Solder Bridge',category:1},
 {action:'vector-polygon-draw',title:'다각형 폴리곤 (Polygon - 4)',type:'polygon',label:'Scratch',category:2},
 {action:'vector-obb-draw',title:'회전 바운딩 박스 (Rotated BBox OBB - 3)',type:'rotated_bbox',label:'Crack',category:3},
] as const;
type Tool=typeof tools[number];

// Whole trees include every regular file; links and unsupported entries refuse.
export function protectedTree(root:string):Record<string,string>{
 const files:Record<string,string>={};
 const walk=(folder:string)=>{
  const st=fs.lstatSync(folder);expect(st.isSymbolicLink()).toBe(false);expect(st.isDirectory()).toBe(true);
  for(const name of fs.readdirSync(folder).sort()){
   const file=path.join(folder,name),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
   if(info.isDirectory())walk(file);
   else{expect(info.isFile()).toBe(true);files[path.relative(root,file).split(path.sep).join('/')]=sha(fs.readFileSync(file));}
  }
 };
 walk(root);return files;
}

export function assertShape(annotation:any,tool:Tool){
 expect(annotation.type).toBe(tool.type);expect(annotation.label).toBe(tool.label);expect(annotation.category_id).toBe(tool.category);
 expect(typeof annotation.id).toBe('string');expect(annotation.id.length).toBeGreaterThan(0);
 if(tool.type==='bbox')expect(annotation.bbox).toEqual([20,20,70,60]);
 else if(tool.type==='polygon'){
  expect(annotation.polygon).toEqual([[120,30],[180,30],[150,80]]);expect(annotation.points).toEqual(annotation.polygon);
 }else{
  expect(annotation.rotated_bbox).toEqual([110,170,80,40,30]);expect(annotation.direction_deg).toBe(315);
 }
}

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'source-electron':'browser',calls:any[]=[],writes:any[]=[],cells:any[]=[];
 const value=async(route:string,body?:unknown,method?:string)=>{
  const reply=await api(route,body,method);expect(reply.status,`Owned vector API ${route}`).toBe(200);
  const parsed=JSON.parse(reply.body);calls.push({route,method:method||(body===undefined?'GET':'POST'),request:body,...reply});return parsed;
 };
 const raw=async(route:string)=>{const reply=await api(route);expect(reply.status).toBe(200);return reply;};
 const screenshot=(name:string)=>evidence.screenshot(page,`${prefix}-vector-${name}`);
 const enter=async()=>{
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});
  if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
 };
 page.on('request',request=>{
  const u=new URL(request.url());if(u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))
   writes.push({method:request.method(),endpoint:u.pathname,body:request.postData()});
 });
 const emptyProject=await value('/api/project/create',{name:'Owned vector empty source fixture',task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(emptyProject.name);await enter();
 const emptyCurrent=await value('/api/project/current'),emptyRoots={project:emptyCurrent.project_dir,source:workspace.dataset};
 const emptySnapshot=async()=>({current:await raw('/api/project/current'),labelsets:await raw('/api/project/labelsets'),trees:Object.fromEntries(Object.entries(emptyRoots).map(([key,root])=>[key,protectedTree(root)]))});
 const emptyBefore=await emptySnapshot(),emptyWrites=writes.length;
 for(const tool of tools){
  await page.getByTitle(tool.title,{exact:true}).click();
  const canvas=page.locator('[data-canvas-container]'),box=await canvas.boundingBox();expect(box).not.toBeNull();
  if(tool.type==='polygon'){
   for(const[x,y]of [[35,35],[90,35],[60,80]])await page.mouse.click(box!.x+x,box!.y+y);
   await page.keyboard.press('Enter');
  }else{
   await page.mouse.move(box!.x+35,box!.y+35);await page.mouse.down();await page.mouse.move(box!.x+90,box!.y+80,{steps:8});await page.mouse.up();
  }
  await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
  await expect(page.getByText('No annotations on this image',{exact:true})).toBeVisible();
  await page.keyboard.press('Escape');expect(writes.slice(emptyWrites)).toEqual([]);
  const after=await emptySnapshot();expect(after).toEqual(emptyBefore);
  await screenshot(tool.action+'-empty-no-current-image');
  cells.push({action:'U030.'+tool.action,dimension:'empty',current_image_absent:true,before:emptyBefore,after,mutations:writes.slice(emptyWrites)});
 }

 const source=path.join(workspace.root,'vector-lifecycle-source');fs.mkdirSync(source,{recursive:false});
 const imagePath=path.join(source,'part.png');fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,100]),{flag:'wx'});const sourceSha=sha(fs.readFileSync(imagePath));
 const project=await value('/api/project/create',{name:'Owned vector service failure and stage handoff',task:'segmentation'});
 await value('/api/project/update',{source_dataset_dir:source},'PUT');await value('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await enter();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 const current=await value('/api/project/current'),query=`/api/annotations/part?file_path=${encodeURIComponent(imagePath)}`;
 const metadataQuery='/api/dataset/metadata/image?image_path='+encodeURIComponent(imagePath);
 const metadata=await value(metadataQuery);expect(metadata.image_uuid).toBeTruthy();
 const roots={project:current.project_dir,source};
 const snapshot=async()=>({current:await raw('/api/project/current'),labelsets:await raw('/api/project/labelsets'),annotation:await raw(query),metadata:await raw(metadataQuery),trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)]))});
 const protect=async(name:string)=>{
  const state=await snapshot(),baseline=path.join(workspace.root,'logs/vector-lifecycle-protected-'+name);
  expect(fs.existsSync(baseline)).toBe(false);fs.mkdirSync(baseline,{recursive:true});
  for(const[key,root]of Object.entries(roots)){
   fs.cpSync(root,path.join(baseline,key),{recursive:true,errorOnExist:true});expect(protectedTree(path.join(baseline,key))).toEqual(state.trees[key]);
   for(const relative of Object.keys(state.trees[key]))evidence.addFile(path.join(baseline,key,relative));
  }
  const file=path.join(workspace.root,'logs/vector-lifecycle-'+name+'.json');fs.writeFileSync(file,JSON.stringify({roots,baseline,state}),{flag:'wx'});evidence.addFile(file);
  return {state,baseline};
 };
 const exactProtected=async(saved:{state:Awaited<ReturnType<typeof snapshot>>;baseline:string})=>{
  const after=await snapshot();expect(after).toEqual(saved.state);
  for(const[key,root]of Object.entries(roots))for(const relative of Object.keys(saved.state.trees[key]))
   expect(fs.readFileSync(path.join(root,relative))).toEqual(fs.readFileSync(path.join(saved.baseline,key,relative)));
  return after;
 };
 const point=async(x:number,y:number)=>{const b=(await page.locator('[data-canvas-container]').boundingBox())!;return{x:b.x+(b.width-256)/2+x,y:b.y+(b.height-256)/2+y};};
 const drag=async(x1:number,y1:number,x2:number,y2:number)=>{
  const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});await page.mouse.up();
 };
 const draw=async(tool:Tool)=>{
  await page.getByRole('button',{name:new RegExp('^'+tool.label+'(?: \\d+)?$')}).click();await page.getByTitle(tool.title,{exact:true}).click();
  await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
  if(tool.type==='bbox')await drag(20,20,70,60);
  else if(tool.type==='polygon'){
   for(const[x,y]of [[120,30],[180,30],[150,80]]){const p=await point(x,y);await page.mouse.move(p.x,p.y);await expect(page.getByTestId('canvas-hud')).toContainText(new RegExp(`X:\\s*${x}\\s*px\\s*Y:\\s*${y}\\s*px`));await page.mouse.click(p.x,p.y);}
   await page.keyboard.press('Enter');
  }else{
   await drag(70,150,150,190);await page.getByLabel('객체 독립 방향 라벨',{exact:true}).fill('315');
   await page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton').fill('30');
  }
 };
 const save=async(expectedStatus:number)=>{
  const waiting=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/annotations/save'&&response.request().method()==='POST');
  await page.getByTestId('annotation-save-button').click();const response=await waiting;
  const reply={status:response.status(),body:await response.text(),url:response.url(),request:response.request().postData()};expect(reply.status).toBe(expectedStatus);return reply;
 };
 for(const[i,tool]of tools.entries()){
  const baseline=await protect(tool.action+'-before-error'),prior=JSON.parse(baseline.state.annotation.body);
  expect(prior.annotations).toHaveLength(i);await draw(tool);await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(i+1);
  const route='**/api/annotations/save',detail='Controlled '+tool.action+' annotation save unavailable';
  await page.route(route,request=>request.fulfill({status:503,json:{detail}}));
  let failed:Awaited<ReturnType<typeof save>>;
  try{
   failed=await save(503);expect(JSON.parse(failed.body)).toEqual({detail});
   const submitted=JSON.parse(failed.request!);expect(submitted.annotations).toHaveLength(i+1);
   const draft=submitted.annotations.find((a:any)=>a.type===tool.type);assertShape(draft,tool);
   for(const previous of prior.annotations){const known=tools.find(row=>row.type===previous.type)!;assertShape(submitted.annotations.find((a:any)=>a.id===previous.id),known);}
   await expect(page.getByText('Failed: '+detail,{exact:true})).toBeVisible();await expect(page.getByTestId('annotation-save-button')).toBeEnabled();
   await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(i+1);const after=await exactProtected(baseline);
   await screenshot(tool.action+'-error-exact503-baseline-retained');
   cells.push({action:'U030.'+tool.action,dimension:'error',failed,baseline,after,submitted});
  }finally{await page.unroute(route);}
  const retried=await save(200),saved=await value(query);expect(saved.annotations).toHaveLength(i+1);
  assertShape(saved.annotations.find((a:any)=>a.type===tool.type),tool);
  for(const previous of prior.annotations)expect(saved.annotations.find((a:any)=>a.id===previous.id)).toEqual(previous);
  const maskBytes=fs.readFileSync(saved.mask_file);evidence.addFile(saved.mask_file);
  const raster=await page.evaluate(async(data)=>{
   const image=new Image();await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('Original class raster decode failed'));image.src=data;});
   const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const context=canvas.getContext('2d')!;context.drawImage(image,0,0);
   return {width:image.width,height:image.height,classes:[[45,40],[150,45],[110,170]].map(([x,y])=>context.getImageData(x,y,1,1).data[0])};
  },'data:image/png;base64,'+maskBytes.toString('base64'));
  expect(raster).toEqual({width:256,height:256,classes:[1,i>=1?2:0,i>=2?3:0]});
  const failedBody=JSON.parse(failed!.request!),retryBody=JSON.parse(retried.request!);expect(retryBody).toEqual(failedBody);
  const handoff=await protect(tool.action+'-before-handoff'),writeStart=writes.length;
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
  const preparation=page.getByRole('region',{name:'공통 모델 준비'});await expect(preparation).toBeVisible();
  await expect(preparation).toContainText(project.name);await expect(preparation).toContainText('원본 1장');
  const atTraining=await exactProtected(handoff);await preparation.getByRole('button',{name:'정답 검토',exact:true}).click();
  await expect(page.getByTitle(tool.title,{exact:true})).toBeVisible();await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(i+1);
  const returned=await exactProtected(handoff);expect(writes.slice(writeStart)).toEqual([]);
  expect(sha(fs.readFileSync(imagePath))).toBe(sourceSha);await screenshot(tool.action+'-handoff-stage3-truth-review-return');
  cells.push({action:'U030.'+tool.action,dimension:'handoff',retry:retried,saved,raster,mask_sha256:sha(maskBytes),metadata,source_sha256:sourceSha,handoff,atTraining,returned,mutations:writes.slice(writeStart)});
 }
 await page.reload();await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(3);
 const reopened=await value(query);for(const tool of tools)assertShape(reopened.annotations.find((a:any)=>a.type===tool.type),tool);
 expect(sha(fs.readFileSync(imagePath))).toBe(sourceSha);
 for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
 const notes={cells,sourceElectron:native,project,current,metadata,imagePath,source_sha256:sourceSha,reopened,calls,writes,
  actual_empty_source_pointer_inputs:true,controlled_503_and_explicit_original_200_separate:true,full_unfiltered_protected_trees:true,
  actual_stage3_and_truth_review_stage2:true,model_training_inference_or_download:false,whole_feature_or_native_installed_release_acceptance:false};
 const proof=path.join(workspace.root,'logs/vector-label-lifecycle-proof.json');fs.writeFileSync(proof,JSON.stringify(notes),{flag:'wx'});evidence.addFile(proof);
 evidence.note('vector_label_lifecycle',notes);
}

test('vector empty input service failures and explicit stage handoff preserve exact original labels',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});return{status:reply.status(),body:await reply.text(),url:reply.url()};};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native vector empty input service failures and explicit stage handoff preserve exact original labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const backend=await electronSession.waitForBackend(),window=electronSession.window;
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{
  const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return{status:reply.status,body:await reply.text(),url:reply.url};
 },{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

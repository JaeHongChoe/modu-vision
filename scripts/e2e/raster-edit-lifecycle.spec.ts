import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Reply={status:number;body:string;url?:string};
type Api=(route:string,body?:unknown,method?:string)=>Promise<Reply>;
type Write={index:number;method:string;endpoint:string;body:string|null};
type SavedReply=Reply&{request:string};
type ReadTiming={started:number;deadline:number;finished?:number};
const sha=(value:Buffer|string)=>crypto.createHash('sha256').update(value).digest('hex');
const actions=['brush-class-mask','erase-selected-mask','mask-undo-redo'] as const;

export function assertOwnedProjectDirectory(directory:string,root:string){
 for(const p of [root,directory]){
  expect(path.isAbsolute(p)).toBe(true);expect(path.normalize(p)).toBe(p);
  const stat=fs.lstatSync(p);expect(stat.isSymbolicLink()).toBe(false);expect(stat.isDirectory()).toBe(true);
  expect(fs.realpathSync(p)).toBe(p);
 }
 expect(path.dirname(directory)).toBe(root);
}
export function protectedTree(root:string):Record<string,string>{
 const result:Record<string,string>={};
 const walk=(directory:string)=>{
  const stat=fs.lstatSync(directory);expect(stat.isSymbolicLink()).toBe(false);expect(stat.isDirectory()).toBe(true);
  for(const name of fs.readdirSync(directory).sort()){
   const p=path.join(directory,name),s=fs.lstatSync(p);expect(s.isSymbolicLink()).toBe(false);
   if(s.isDirectory())walk(p);else{expect(s.isFile()).toBe(true);result[path.relative(root,p).split(path.sep).join('/')]=sha(fs.readFileSync(p));}
  }
 };
 walk(root);return result;
}
export function assertExactSnapshot(actual:unknown,original:unknown){expect(actual).toEqual(original);}
export function assertSourceIdentity(actual:any,original:any){
 for(const key of ['image_uuid','file_path','content_hash','content_version'])expect(actual[key]).toEqual(original[key]);
}
export function assertTwoClasses(annotations:any[]){
 expect(annotations).toHaveLength(2);expect(new Set(annotations.map(a=>a.id)).size).toBe(2);
 for(const a of annotations){expect(typeof a.id).toBe('string');expect(a.id.length).toBeGreaterThan(0);}
 expect(annotations.map(a=>[a.type,a.label,a.category_id])).toEqual([['brush_mask','Solder Bridge',1],['brush_mask','Scratch',2]]);
}
export function assertOnlySelectedRasterChanged(after:any[],before:any[]){
 assertTwoClasses(after);assertTwoClasses(before);expect(after[0].mask_rle).not.toBe(before[0].mask_rle);
 expect(after[0]).toEqual({...before[0],mask_rle:after[0].mask_rle});expect(after[1]).toEqual(before[1]);
}
// Wire-only schema normal form: AnnotationItem.model_dump() materializes
// declared optional nulls that JSON requests may omit. Stored records and
// complete raw API/file snapshots retain their original exact comparisons.
export function assertWireRasterRecordsEqual(wire:any[],stored:any[]){
 const declared=['id','type','label','category_id','bbox','rotated_bbox','polygon','points','mask_rle','is_normal','color','text','direction_deg'];
 const nullable=['id','bbox','rotated_bbox','polygon','points','mask_rle','is_normal','color','text','direction_deg'];
 expect(wire).toHaveLength(stored.length);
 for(let i=0;i<wire.length;i++){
  for(const key of Object.keys(wire[i]))expect(declared.includes(key)).toBe(true);
  const normalized={...wire[i]};
  for(const key of nullable)if(!Object.prototype.hasOwnProperty.call(wire[i],key)&&stored[i][key]===null)normalized[key]=null;
  expect(Object.keys(normalized).sort()).toEqual(Object.keys(stored[i]).sort());
  expect(normalized).toEqual(stored[i]);
 }
}
export function assertOnlySelectedWireRasterChanged(wire:any[],before:any[]){
 assertTwoClasses(wire);assertTwoClasses(before);expect(typeof wire[0].mask_rle).toBe('string');expect(wire[0].mask_rle.length).toBeGreaterThan(0);expect(wire[0].mask_rle).not.toBe(before[0].mask_rle);
 assertWireRasterRecordsEqual(wire,[{...before[0],mask_rle:wire[0].mask_rle},before[1]]);
}
export function assertPaintCountChange(after:number,before:number,operation:'brush'|'eraser'){
 expect(after).toBeGreaterThan(0);if(operation==='brush')expect(after).toBeGreaterThan(before);else expect(after).toBeLessThan(before);
}
export function assertRetry(failed:SavedReply,retry:SavedReply,detail:string){
 expect(failed.status).toBe(503);expect(JSON.parse(failed.body)).toEqual({detail});
 expect(retry.status).toBe(200);expect(JSON.parse(retry.body).status).toBe('saved');expect(retry.request).toBe(failed.request);
}
export function assertSaveCycle(writes:Write[],status:200|503,source:string){
 expect(writes).toHaveLength(status===200?2:1);
 expect([writes[0].method,writes[0].endpoint]).toEqual(['POST','/api/annotations/save']);
 if(status===200){
  expect([writes[1].method,writes[1].endpoint]).toEqual(['POST','/api/dataset/import']);
  expect(writes[1].index).toBeGreaterThan(writes[0].index);
  expect(JSON.parse(writes[1].body!)).toEqual({folder_path:source,task:'segmentation',validate_images:false});
 }
}
export function assertReadDeadline(timing:ReadTiming){
 expect(timing.deadline).toBe(timing.started+10_000);
 expect(timing.finished).toBeDefined();expect(timing.finished!).toBeGreaterThanOrEqual(timing.started);expect(timing.finished!).toBeLessThanOrEqual(timing.deadline);
}
export async function withinOriginalReadDeadline<T>(timing:ReadTiming,read:()=>Promise<T>,now=()=>performance.now()):Promise<T>{
 if(timing.finished!==undefined){assertReadDeadline(timing);return read();}
 const remaining=timing.deadline-now();if(remaining<=0)throw Error('Original API GET exceeded absolute 10s completion deadline');
 let timer:ReturnType<typeof setTimeout>|undefined;const operation=read();
 try{
  const result=await Promise.race([operation,new Promise<T>((resolve,reject)=>{
   timer=setTimeout(()=>timing.finished!==undefined&&timing.finished<=timing.deadline?resolve(operation):reject(Error('Original API GET exceeded absolute 10s completion deadline')),remaining);
  })]);assertReadDeadline(timing);return result;
 }finally{if(timer!==undefined)clearTimeout(timer);}
}
export function assertReadinessPreserved(after:Reply,before:Reply,expectedEligibilitySha:string){
 const original=JSON.parse(before.body);expect(JSON.parse(after.body)).toEqual({...original,eligibility_sha256:expectedEligibilitySha});
 expect(after.status).toBe(before.status);expect(after.url).toBe(before.url);
}
export function assertMetadataTransition(after:any,before:any){
 const changing=new Set(['revision','workflow_state','audit','annotation_hash','mask_hash','team']);
 expect(Object.keys(after).sort()).toEqual(Object.keys(before).sort());
 expect(Object.fromEntries(Object.entries(after).filter(([k])=>!changing.has(k)))).toEqual(Object.fromEntries(Object.entries(before).filter(([k])=>!changing.has(k))));
 expect(after.revision).toBe(before.revision+1);expect(after.workflow_state).toBe('needs_review');
 expect(after.audit.slice(0,-1)).toEqual(before.audit);expect(after.audit).toHaveLength(before.audit.length+1);
 const event=after.audit.at(-1);expect(Object.keys(event).sort()).toEqual(['id','at','actor','action','revision','changes'].sort());
 expect(event.id).toBeTruthy();expect(event.at).toBeTruthy();expect(event.actor).toBe('operator');expect(event.action).toBe('annotation_changed');expect(event.revision).toBe(after.revision);expect(event.changes).toEqual({workflow_state:'needs_review'});
 expect(after.team).toEqual({...before.team,annotation_actor:'operator'});
}

// Independent Pillow decoding/composition; the original renderer canvas and
// backend OpenCV stroke/raster helpers are never used by this oracle.
export const PYTHON_RASTER_ORACLE=String.raw`
import base64,hashlib,io,json,pathlib,sys,uuid
from PIL import Image
root=pathlib.Path(sys.argv[1]);source=pathlib.Path(sys.argv[2]);payload=json.loads(pathlib.Path(sys.argv[3]).read_text())
record=payload['record'];identity=payload['identity'];expected=payload['expected_identity']
assert source.is_absolute() and source.resolve()==source and source.name=='part.png'
assert identity['file_path']==str(source)==expected['file_path']
for key in ['image_uuid','content_hash','content_version']:assert identity[key]==expected[key]
assert identity['relative_path']=='part.png'
assert str(uuid.UUID(identity['image_uuid']))==identity['image_uuid']
assert type(identity['content_version']) is int and identity['content_version']>=1
assert identity['width']==identity['height']==256
assert identity['content_hash']==hashlib.sha256(source.read_bytes()).hexdigest()
assert record['image_id']=='part' and record['image_width']==record['image_height']==256
with Image.open(source) as im:
 assert im.format=='PNG' and im.mode=='RGB' and im.size==(256,256)
 assert list(im.getdata())==[(x,y,80) for y in range(256) for x in range(256)]
rows=record['annotations'];assert len(rows)==2 and len({a['id'] for a in rows})==2
assert [(a['type'],a['label'],a['category_id']) for a in rows]==[('brush_mask','Solder Bridge',1),('brush_mask','Scratch',2)]
composite=bytearray(256*256);layers=[]
for row in rows:
 assert isinstance(row['id'],str) and row['id']
 prefix,encoded=row['mask_rle'].split(',',1);assert prefix=='data:image/png;base64'
 raw=base64.b64decode(encoded,validate=True)
 with Image.open(io.BytesIO(raw)) as layer:
  assert layer.format=='PNG' and layer.mode=='RGBA' and layer.size==(256,256)
  rgba=layer.tobytes();alpha=layer.getchannel('A').tobytes()
 assert any(alpha)
 for i,value in enumerate(alpha):
  if value>0:composite[i]=row['category_id']
 layers.append({'id':row['id'],'label':row['label'],'category_id':row['category_id'],'rle_sha256':hashlib.sha256(row['mask_rle'].encode()).hexdigest(),'png_sha256':hashlib.sha256(raw).hexdigest(),'rgba_sha256':hashlib.sha256(rgba).hexdigest(),'alpha_sha256':hashlib.sha256(alpha).hexdigest(),'painted':sum(v>0 for v in alpha),'alpha_base64':base64.b64encode(alpha).decode()})
mask=record.get('mask_file');actual_mask=None
if payload['require_saved_composite']:
 assert isinstance(mask,str) and mask
 p=pathlib.Path(mask);assert p.is_absolute() and p.resolve()==p and p.is_relative_to(root)
 assert p.name=='part.png' and p.parent.name=='masks'
 assert record['metadata']==identity
 annotation=p.parent.parent/'part.json'
 assert annotation.resolve()==annotation and annotation.is_relative_to(root)
 original_annotation=annotation.read_bytes()
 assert json.loads(original_annotation)=={k:v for k,v in record.items() if k!='metadata'}
 assert hashlib.sha256(original_annotation).hexdigest()==identity['annotation_hash']
 raw=p.read_bytes()
 assert hashlib.sha256(raw).hexdigest()==identity['mask_hash']
 with Image.open(p) as im:
  assert im.format=='PNG' and im.mode=='L' and im.size==(256,256)
  pixels=im.tobytes()
 assert pixels==bytes(composite)
 actual_mask={'file':str(p),'sha256':hashlib.sha256(raw).hexdigest(),'class_pixels_sha256':hashlib.sha256(pixels).hexdigest(),'class_pixels_base64':base64.b64encode(pixels).decode()}
eligibility=[{key:identity[key] for key in ['image_uuid','relative_path','content_hash','annotation_hash','mask_hash']}]
eligibility_sha=hashlib.sha256(json.dumps(eligibility,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
result={'image_id':'part','file_path':str(source),'source_sha256':identity['content_hash'],'image_uuid':identity['image_uuid'],'content_version':identity['content_version'],'dimensions':[256,256],'layers':layers,'expected_composite_sha256':hashlib.sha256(composite).hexdigest(),'actual_composite':actual_mask,'readiness_eligibility_sha256':eligibility_sha,'all_65536_class_pixels_equal':payload['require_saved_composite'],'ideal_stroke_or_human_quality_claim':False}
print(json.dumps(result,separators=(',',':')))
`;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'source-electron':'browser',calls:any[]=[],writes:Write[]=[],cells:any[]=[],saveCycles:any[]=[],oracles:any[]=[];
 const order=new WeakMap<Request,number>();let sequence=0,trackReads=false;
 const readTimes=new Map<Request,ReadTiming>(),unverifiedReads=new Set<Request>(),verifiedReads:any[]=[],failedReads:any[]=[];
 page.on('request',r=>{
  order.set(r,++sequence);const endpoint=new URL(r.url()).pathname;
  if(endpoint.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))
   writes.push({index:sequence,method:r.method(),endpoint,body:r.postData()});
  if(trackReads&&endpoint.startsWith('/api/')){
   const started=performance.now();readTimes.set(r,{started,deadline:started+10_000});if(r.method()==='GET')unverifiedReads.add(r);
  }
 });
 page.on('requestfinished',r=>{const timing=readTimes.get(r);if(timing)timing.finished=performance.now();});
 page.on('requestfailed',r=>{if(unverifiedReads.has(r))failedReads.push({method:r.method(),url:r.url(),failure:r.failure()});});
 const settleReads=async()=>{
  for(const r of unverifiedReads){
   const timing=readTimes.get(r)!;
   const completed=await withinOriginalReadDeadline(timing,async()=>{
    const response=await r.response();expect(response).not.toBeNull();expect(response!.status(),r.url()).toBe(200);expect(await response!.finished()).toBeNull();
    return{response:response!,bytes:await response!.body()};
   });
   verifiedReads.push({method:'GET',url:r.url(),status:completed.response.status(),body_sha256:sha(completed.bytes),body_base64:completed.bytes.toString('base64'),...timing});unverifiedReads.delete(r);
  }
  expect(failedReads).toEqual([]);expect(unverifiedReads.size).toBe(0);
 };
 const raw=async(route:string)=>{const reply=await api(route);expect(reply.status,route).toBe(200);return reply;};
 const value=async(route:string,body?:unknown,method?:string)=>{
  const reply=await api(route,body,method);expect(reply.status,route).toBe(200);
  calls.push({route,method:method||(body===undefined?'GET':'POST'),request:body,...reply});return JSON.parse(reply.body);
 };
 const screenshot=(label:string)=>evidence.screenshot(page,prefix+'-raster-'+label);
 const ownedRoot=native?path.join(workspace.userData,'projects'):workspace.projects;
 const enter=async()=>{
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
 };
 const focus=async(enabled:boolean)=>{
  const button=page.getByRole('button',{name:'집중 편집',exact:true});
  if((await button.getAttribute('aria-pressed')==='true')!==enabled)await button.click();
 };
 const treeSnapshot=async(roots:Record<string,string>,routes:Record<string,string>)=>{
  await settleReads();
  const records:Record<string,Reply>={};for(const[key,route]of Object.entries(routes))records[key]=await raw(route);
  await settleReads();
  return{api:records,trees:Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,protectedTree(root)]))};
 };
 const protect=async(label:string,roots:Record<string,string>,routes:Record<string,string>)=>{
  const state=await treeSnapshot(roots,routes),directory=path.join(workspace.logs,'raster-protected-'+label);
  fs.mkdirSync(directory,{recursive:false});
  for(const[key,root]of Object.entries(roots)){
   const dest=path.join(directory,key);fs.cpSync(root,dest,{recursive:true,errorOnExist:true});
   expect(protectedTree(dest)).toEqual(state.trees[key]);
   for(const relative of Object.keys(state.trees[key]))evidence.addFile(path.join(dest,relative));
  }
  const file=path.join(directory,'snapshot.json');fs.writeFileSync(file,JSON.stringify({roots,routes,state}),{flag:'wx'});evidence.addFile(file);
  return{directory,roots,routes,state};
 };
 type Protected=Awaited<ReturnType<typeof protect>>;
 const unchanged=async(before:Protected)=>{
  const after=await treeSnapshot(before.roots,before.routes);assertExactSnapshot(after,before.state);
  for(const[key,root]of Object.entries(before.roots))for(const relative of Object.keys(before.state.trees[key]))
   expect(fs.readFileSync(path.join(root,relative))).toEqual(fs.readFileSync(path.join(before.directory,key,relative)));
  return after;
 };
 const emptyProject=await value('/api/project/create',{name:'Owned raster empty current image',task:'segmentation'});
 trackReads=true;
 if(url)await page.goto(url);else await page.reload();await enter();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(emptyProject.name);
 await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(0);
 const emptyCurrent=await value('/api/project/current');expect(emptyCurrent.source_dataset_dir).toBeNull();
 assertOwnedProjectDirectory(emptyCurrent.project_dir,ownedRoot);
 const emptyRoots={project:emptyCurrent.project_dir,originalHarnessDataset:workspace.dataset};
 const emptyRoutes={current:'/api/project/current',labelsets:'/api/project/labelsets'};
 const emptyBefore=await protect('empty',emptyRoots,emptyRoutes);
 for(const action of actions){
  const start=writes.length;
  if(action==='mask-undo-redo'){
   await expect(page.getByTitle('Undo (Ctrl+Z)',{exact:true})).toBeDisabled();await expect(page.getByTitle('Redo (Ctrl+Y)',{exact:true})).toBeDisabled();
   await page.keyboard.press('Control+z');await page.keyboard.press('Control+y');
  }else{
   await page.getByTitle(action==='brush-class-mask'?'브러시 마스크 (Brush - 5)':'마스크 지우개 (Eraser - 6)',{exact:true}).click();
   const b=(await page.locator('[data-canvas-container]').boundingBox())!;expect(b).not.toBeNull();
   await page.mouse.move(b.x+b.width/2-15,b.y+b.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2+15,b.y+b.height/2,{steps:5});await page.mouse.up();
  }
  await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(0);
  await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
  expect(writes.slice(start)).toEqual([]);const after=await unchanged(emptyBefore);await screenshot(action+'-empty');
  cells.push({action:'U030.'+action,dimension:'empty',before:emptyBefore,after,mutations:writes.slice(start),actual_current_image_absent:true,harness_disk_dataset_empty_claim:false});
 }
 const source=path.join(workspace.root,'raster-lifecycle-source');fs.mkdirSync(source,{recursive:false});
 const imagePath=path.join(source,'part.png');fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,80]),{flag:'wx'});const imageSha=sha(fs.readFileSync(imagePath));
 const project=await value('/api/project/create',{name:'Owned raster brush erase undo lifecycle',task:'segmentation'});
 await value('/api/project/update',{source_dataset_dir:source},'PUT');await value('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();await enter();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 const current=await value('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
 assertOwnedProjectDirectory(current.project_dir,ownedRoot);
 const query='/api/annotations/part?file_path='+encodeURIComponent(imagePath),metadataQuery='/api/dataset/metadata/image?image_path='+encodeURIComponent(imagePath);
 const roots={project:current.project_dir as string,source,originalHarnessDataset:workspace.dataset};
 const routes={current:'/api/project/current',labelsets:'/api/project/labelsets',team:'/api/team-data',readiness:'/api/team-data/readiness',versions:'/api/dataset/versions',annotations:query,metadata:metadataQuery};
 // Real first-read default initialization belongs to controlled setup.
 await focus(false);await treeSnapshot(roots,routes);await focus(true);
 const rows=()=>page.getByTitle('Delete annotation',{exact:true}),saveButton=()=>page.getByTestId('annotation-save-button');
 const preludeWrites=writes.slice();expect(preludeWrites.map(w=>[w.method,w.endpoint])).toEqual(native?[
  ['POST','/api/project/create'],['POST','/api/project/create'],['PUT','/api/project/update'],['POST','/api/dataset/import']]:[]);
 if(native)expect(preludeWrites.map(w=>JSON.parse(w.body!))).toEqual([
  {name:emptyProject.name,task:'segmentation'},{name:project.name,task:'segmentation'},{source_dataset_dir:source},{folder_path:source,task:'segmentation'}]);
 const bodyWriteStart=writes.length;
 const selectClass=async()=>{await page.getByRole('button',{name:/^Solder Bridge(?: \d+)?$/}).click();};
 const recenter=async()=>{await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');};
 const point=async(x:number,y:number)=>{const b=await page.locator('[data-canvas-container]').boundingBox();expect(b).not.toBeNull();return{x:b!.x+(b!.width-256)/2+x,y:b!.y+(b!.height-256)/2+y};};
 const stroke=async(y:number,x1:number,x2:number,button:'left'|'right'='left')=>{
  const a=await point(x1,y),b=await point(x2,y);await page.mouse.move(a.x,a.y);await page.mouse.down({button});await page.mouse.move(b.x,b.y,{steps:10});await page.mouse.up({button});
 };
 const view=async()=>{const payload=await page.locator('[data-canvas-container] canvas').nth(1).evaluate((c:HTMLCanvasElement)=>({width:c.width,height:c.height,png:c.toDataURL('image/png')}));return{width:payload.width,height:payload.height,sha256:sha(payload.png)};};
 const save=async(status:200|503,label:string)=>{
  const start=writes.length;let importOrder:number|null=null;
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST',{timeout:10_000});
  const refresh=status===200?page.waitForResponse(r=>{
   if(new URL(r.url()).pathname!=='/api/dataset/import'||r.request().method()!=='POST')return false;
   expect(r.request().postDataJSON()).toEqual({folder_path:source,task:'segmentation',validate_images:false});importOrder=order.get(r.request())!;return true;
  },{timeout:10_000}):null;
  const images=status===200?page.waitForResponse(r=>{
   const u=new URL(r.url());return u.pathname==='/api/dataset/images'&&r.request().method()==='GET'&&u.searchParams.get('folder_path')===source&&u.searchParams.get('task')==='segmentation'&&u.searchParams.get('offset')==='0'&&importOrder!==null&&order.get(r.request())!>importOrder;
  },{timeout:10_000}):null;
  await saveButton().click();const r=await response;const result:SavedReply={status:r.status(),body:await withinOriginalReadDeadline(readTimes.get(r.request())!,()=>r.text()),request:r.request().postData()!,url:r.url()};
  expect(result.status).toBe(status);let importReply:any=null,imageReply:any=null;
  if(status===200){
   const refreshed=await refresh!;expect(refreshed.status()).toBe(200);importReply={status:200,body:await withinOriginalReadDeadline(readTimes.get(refreshed.request())!,()=>refreshed.text()),request:refreshed.request().postData(),order:importOrder};
   const loaded=await images!;expect(loaded.status()).toBe(200);imageReply={status:200,body:await withinOriginalReadDeadline(readTimes.get(loaded.request())!,()=>loaded.text()),url:loaded.url(),order:order.get(loaded.request())};
   const body=JSON.parse(imageReply.body);expect(body.total).toBe(1);expect(body.items).toHaveLength(1);expect(body.items[0].file_path).toBe(imagePath);
   await expect(rows()).toHaveCount(2);await expect(saveButton()).toContainText('Saved');await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();
  }
  const mutations=writes.slice(start);assertSaveCycle(mutations,status,source);saveCycles.push({label,result,importReply,imageReply,mutations});return result;
 };
 await page.getByRole('button',{name:/^Solder Bridge(?: \d+)?$/}).click();await page.getByTitle('브러시 마스크 (Brush - 5)',{exact:true}).click();await recenter();await stroke(80,50,170);
 await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await stroke(170,50,170);
 await save(200,'initial-two-class-controlled-setup');
 const identity=await value(metadataQuery);expect(identity.file_path).toBe(imagePath);expect(identity.content_hash).toBe(imageSha);expect(identity.image_uuid).toBeTruthy();
 const oracle=async(label:string,record:any,requireSaved=true)=>{
  assertTwoClasses(record.annotations);const metadata=await value(metadataQuery);assertSourceIdentity(metadata,identity);
  const input=path.join(workspace.logs,'raster-oracle-'+label+'-input.json');fs.writeFileSync(input,JSON.stringify({record,identity:metadata,expected_identity:identity,require_saved_composite:requireSaved}),{flag:'wx'});evidence.addFile(input);
  const result=JSON.parse(execFileSync(process.env.MV_E2E_PYTHON||'python3',['-c',PYTHON_RASTER_ORACLE,current.project_dir,imagePath,input],{encoding:'utf8',timeout:10_000}));
  const file=path.join(workspace.logs,'raster-oracle-'+label+'.json');fs.writeFileSync(file,JSON.stringify(result),{flag:'wx'});evidence.addFile(file);
  if(requireSaved){const mask=path.join(workspace.logs,'raster-'+label+'-composite.png');fs.copyFileSync(record.mask_file,mask,fs.constants.COPYFILE_EXCL);evidence.addFile(mask);expect(sha(fs.readFileSync(mask))).toBe(result.actual_composite.sha256);}
  oracles.push({label,input,file,result});return result;
 };
 const initial=await value(query),initialOracle=await oracle('initial',initial);
 await settleReads();
 await page.reload();await enter();await expect(rows()).toHaveCount(2);await selectClass();await recenter();
 const initialProtected=await protect('invalid',roots,routes);
 expect(JSON.parse(initialProtected.state.api.readiness.body).eligibility_sha256).toBe(initialOracle.readiness_eligibility_sha256);
 for(const action of actions){
  const start=writes.length;
  if(action==='mask-undo-redo'){
   await expect(page.getByTitle('Undo (Ctrl+Z)',{exact:true})).toBeDisabled();await expect(page.getByTitle('Redo (Ctrl+Y)',{exact:true})).toBeDisabled();
   await page.keyboard.press('Control+z');await page.keyboard.press('Control+y');
  }else{
   await page.getByTitle(action==='brush-class-mask'?'브러시 마스크 (Brush - 5)':'마스크 지우개 (Eraser - 6)',{exact:true}).click();
   await stroke(80,90,105,'right');await page.keyboard.press('Escape');
  }
  await expect(rows()).toHaveCount(2);await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
  await expect(page.getByTitle('Undo (Ctrl+Z)',{exact:true})).toBeDisabled();await expect(page.getByTitle('Redo (Ctrl+Y)',{exact:true})).toBeDisabled();
  expect(writes.slice(start)).toEqual([]);const after=await unchanged(initialProtected);await screenshot(action+'-invalid');
  cells.push({action:'U030.'+action,dimension:'invalid',before:initialProtected,after,mutations:writes.slice(start),unsupported_right_pointer_button:action!=='mask-undo-redo',nonempty_no_history:action==='mask-undo-redo',escape_brush_cancel_claim:false});
 }
 const intendedTrees=(before:Protected,after:Awaited<ReturnType<typeof treeSnapshot>>,maskFile:string,expectedEligibilitySha:string)=>{
  expect(after.trees.source).toEqual(before.state.trees.source);expect(after.trees.originalHarnessDataset).toEqual(before.state.trees.originalHarnessDataset);
  expect(Object.keys(after.trees.project).sort()).toEqual(Object.keys(before.state.trees.project).sort());
  const dir=path.dirname(path.dirname(maskFile));
  const allowed=[maskFile,path.join(dir,'part.json'),path.join(dir,'metadata','workflow.json')].map(p=>path.relative(current.project_dir,p).split(path.sep).join('/')).sort();
  const changed=Object.keys(after.trees.project).filter(key=>after.trees.project[key]!==before.state.trees.project[key]).sort();expect(changed).toEqual(allowed);
  for(const key of ['current','labelsets','team','versions'])expect(after.api[key]).toEqual(before.state.api[key]);
  assertReadinessPreserved(after.api.readiness,before.state.api.readiness,expectedEligibilitySha);
  return{changed,allowed};
 };
 const cancel=async(action:string,makeDraft:()=>Promise<void>)=>{
  await focus(true);await selectClass();await recenter();const before=await protect(action+'-cancel',roots,routes),viewBefore=await view(),start=writes.length;
  await makeDraft();await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toBeVisible();
  await expect.poll(async()=> (await view()).sha256).not.toBe(viewBefore.sha256);const draftView=await view();
  await focus(false);await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
  const read=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/part'&&r.request().method()==='GET'&&new URL(r.url()).searchParams.get('file_path')===imagePath,{timeout:10_000});
  await page.getByRole('button',{name:'현재 편집을 버리고 최신 라벨 불러오기',exact:true}).click();const reply=await read;expect(reply.status()).toBe(200);const originalReply={status:200,body:await withinOriginalReadDeadline(readTimes.get(reply.request())!,()=>reply.text()),url:reply.url()};
  await expect(rows()).toHaveCount(2);await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await focus(true);await recenter();
  await expect.poll(async()=>await view()).toEqual(viewBefore);
  const after=await unchanged(before);expect(writes.slice(start)).toEqual([]);await screenshot(action+'-cancel-explicit-server-read');
  cells.push({action:'U030.'+action,dimension:'cancel',before,after,viewBefore,draftView,viewAfter:await view(),originalReply,mutations:writes.slice(start),history_reset_claim:false,escape_cancel_claim:false});
 };
 const handoff=async(action:string)=>{
  const before=await protect(action+'-handoff',roots,routes),start=writes.length;
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();
  const preparation=page.getByRole('region',{name:'공통 모델 준비'});await expect(preparation).toBeVisible();await expect(preparation).toContainText(project.name);await expect(preparation).toContainText('원본 1장');
  const atPreparation=await unchanged(before);await preparation.getByRole('button',{name:'정답 검토',exact:true}).click();await expect(rows()).toHaveCount(2);
  const returned=await unchanged(before);expect(writes.slice(start)).toEqual([]);await screenshot(action+'-handoff-truth-review-return');
  cells.push({action:'U030.'+action,dimension:'handoff',before,atPreparation,returned,mutations:writes.slice(start),training_or_dataset_adoption_claim:false});
 };
 const error=async(action:string,makeDraft:()=>Promise<void>,prior:any,priorOracle:any,operation:'brush'|'eraser'|'undo',restoreRecord?:any)=>{
  const before=await protect(action+'-error',roots,routes);await makeDraft();await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toBeVisible();
  const route='**/api/annotations/save',detail='Controlled '+action+' raster save unavailable';let failed:SavedReply;
  await page.route(route,r=>r.fulfill({status:503,json:{detail}}));
  try{
   failed=await save(503,action+'-controlled503');const body=JSON.parse(failed.request);expect(JSON.parse(failed.body)).toEqual({detail});
   expect(Object.keys(body).sort()).toEqual(['expected_revision','actor','image_id','image_path','annotations','image_width','image_height'].sort());expect(body.actor).toBe('operator');
   expect(body.image_id).toBe('part');expect(body.image_path).toBe(imagePath);expect(body.image_width).toBe(256);expect(body.image_height).toBe(256);
   expect(body.expected_revision).toBe(JSON.parse(before.state.api.metadata.body).revision);
   if(operation==='undo')assertWireRasterRecordsEqual(body.annotations,restoreRecord.annotations);else assertOnlySelectedWireRasterChanged(body.annotations,prior.annotations);
   const failedOracle=await oracle(action+'-failed-draft',{...body,mask_file:null},false);
   if(operation!=='undo')assertPaintCountChange(failedOracle.layers[0].painted,priorOracle.layers[0].painted,operation);
   else expect(failedOracle.layers.map((x:any)=>[x.rle_sha256,x.alpha_sha256])).toEqual(restoreRecord.oracle.layers.map((x:any)=>[x.rle_sha256,x.alpha_sha256]));
   await expect(page.getByText('Failed: '+detail,{exact:true})).toBeVisible();await expect(saveButton()).toBeEnabled();
   const failedAfter=await unchanged(before);await screenshot(action+'-error-retains-original-server-state');
   cells.push({action:'U030.'+action,dimension:'error',before,failed,failedAfter,failedOracle,controlled503:true});
  }finally{await page.unroute(route);}
  const retried=await save(200,action+'-deliberate200');assertRetry(failed!,retried,detail);
  const saved=await value(query),savedOracle=await oracle(action+'-accepted',saved);
  assertMetadataTransition(saved.metadata,prior.metadata);
  if(operation==='undo')expect(saved.annotations).toEqual(restoreRecord.annotations);else{
   assertOnlySelectedRasterChanged(saved.annotations,prior.annotations);assertPaintCountChange(savedOracle.layers[0].painted,priorOracle.layers[0].painted,operation);
  }
  expect(savedOracle.layers[1]).toEqual(priorOracle.layers[1]);assertSourceIdentity(await value(metadataQuery),identity);
  const after=await treeSnapshot(roots,routes);const delta=intendedTrees(before,after,saved.mask_file,savedOracle.readiness_eligibility_sha256);
  Object.assign(cells.at(-1),{retried,saved,savedOracle,acceptedAfter:after,intendedDelta:delta});return{record:saved,oracle:savedOracle};
 };
 await selectClass();await page.getByTitle('브러시 마스크 (Brush - 5)',{exact:true}).click();await recenter();
 const painted=await error('brush-class-mask',()=>stroke(115,70,150),initial,initialOracle,'brush');
 await cancel('brush-class-mask',async()=>{await page.getByTitle('브러시 마스크 (Brush - 5)',{exact:true}).click();await stroke(35,65,145);});await handoff('brush-class-mask');
 // Discard preserves history. Eraser cancellation must happen BEFORE the
 // accepted erasure whose immediate history is the subsequent exact Undo.
 await cancel('erase-selected-mask',async()=>{await page.getByTitle('마스크 지우개 (Eraser - 6)',{exact:true}).click();await stroke(80,100,115);});
 await focus(true);await selectClass();await page.getByTitle('마스크 지우개 (Eraser - 6)',{exact:true}).click();await recenter();
 const erased=await error('erase-selected-mask',()=>stroke(80,100,115),painted.record,painted.oracle,'eraser');await handoff('erase-selected-mask');
 const undone=await error('mask-undo-redo',async()=>{await page.getByTitle('Undo (Ctrl+Z)',{exact:true}).click();},erased.record,erased.oracle,'undo',{...painted.record,oracle:painted.oracle});
 expect(undone.oracle.expected_composite_sha256).toBe(painted.oracle.expected_composite_sha256);
 await cancel('mask-undo-redo',async()=>{await page.getByTitle('Redo (Ctrl+Y)',{exact:true}).click();});await handoff('mask-undo-redo');
 const finalBefore=await protect('final-reload',roots,routes),finalWriteStart=writes.length;
 await page.reload();await enter();await expect(rows()).toHaveCount(2);const finalAfter=await unchanged(finalBefore);expect(writes.slice(finalWriteStart)).toEqual([]);
 const reopened=await value(query);expect(reopened.annotations).toEqual(undone.record.annotations);const reopenedOracle=await oracle('reopened',reopened);expect(reopenedOracle).toEqual(undone.oracle);
 const finalReadback=await unchanged(finalBefore);
 const actualBodyWrites=writes.slice(bodyWriteStart);expect(actualBodyWrites).toHaveLength(11);
 expect(actualBodyWrites.filter(w=>w.endpoint==='/api/annotations/save')).toHaveLength(7);expect(actualBodyWrites.filter(w=>w.endpoint==='/api/dataset/import')).toHaveLength(4);
 expect(saveCycles.map(x=>x.result.status)).toEqual([200,503,200,503,200,503,200]);
 expect(new Set(cells.map(x=>x.action+'.'+x.dimension))).toEqual(new Set(actions.flatMap(a=>['empty','invalid','error','cancel','handoff'].map(d=>'U030.'+a+'.'+d))));expect(cells).toHaveLength(15);
 expect(sha(fs.readFileSync(imagePath))).toBe(imageSha);for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
 const notes={sourceElectron:native,project,current,emptyProject,emptyCurrent,ownedRoot,imagePath,image_sha256:imageSha,identity,initial,painted,erased,undone,reopened,reopenedOracle,
  preludeWrites,calls,writes,saveCycles,oracles,cells,finalBefore,finalAfter,finalReadback,actual_body_mutating_calls:actualBodyWrites,verifiedReads,failedReads,unverified_reads:unverifiedReads.size,
  independent_PIL_entire_class_pixels_from_original_RGBA_alpha_and_order:true,ideal_brush_geometry_or_human_quality_claim:false,
  declared_successful_dirty_save_imports:4,controlled503_then_deliberate_same_raw200:true,full_unfiltered_project_original_source_and_harness_dataset_custody:true,
  whole_workspace_or_userData_store_preservation_claim:false,training_inference_Gpu_download_job_execution:false,installed_windows_physical_or_release_acceptance:false};
 const proof=path.join(workspace.logs,'raster-edit-lifecycle-proof.json');fs.writeFileSync(proof,JSON.stringify(notes),{flag:'wx'});evidence.addFile(proof);evidence.note('raster_edit_lifecycle',notes);
}
test('raster brush erase undo empty invalid failures discard and stage handoff preserve full original class pixels',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{timeout:10_000,method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});return{status:r.status(),body:await r.text(),url:r.url()};};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native raster brush erase undo empty invalid failures discard and stage handoff preserve full original class pixels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const backend=await electronSession.waitForBackend(),window=electronSession.window;
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{
  const started=performance.now();let timer:ReturnType<typeof setTimeout>|undefined;
  const operation=(async()=>{const r=await fetch('http://127.0.0.1:'+port+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});return{status:r.status,body:await r.text(),url:r.url};})();
  try{return await Promise.race([operation,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original fixture API exceeded absolute10s')),Math.max(0,10_000-(performance.now()-started)));})]);}
  finally{if(timer!==undefined)clearTimeout(timer);}
 },{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

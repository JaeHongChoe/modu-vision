import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Download=(action:()=>Promise<void>,format:string)=>Promise<string>;
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const canonical=(items:any[])=>items.map(a=>({type:a.type,label:a.label,category_id:a.category_id,color:a.color,
 ...(a.type==='bbox'?{bbox:a.bbox}:a.type==='polygon'?{polygon:a.polygon||a.points}:{rotated_bbox:a.rotated_bbox,direction_deg:a.direction_deg})}));
function checkGeometry(actual:any[],expected:any[]){
 expect(actual).toHaveLength(expected.length);
 for(let i=0;i<actual.length;i++){
  const a=actual[i],e=expected[i];expect({type:a.type,label:a.label,category_id:a.category_id,color:a.color}).toEqual({type:e.type,label:e.label,category_id:e.category_id,color:e.color});
  if(e.type==='bbox')e.bbox.forEach((v:number,j:number)=>expect(a.bbox[j]).toBeCloseTo(v,10));
  if(e.type==='polygon')e.polygon.forEach((p:number[],j:number)=>p.forEach((v:number,k:number)=>expect((a.polygon||a.points)[j][k]).toBeCloseTo(v,10)));
  if(e.type==='rotated_bbox'){expect(a.rotated_bbox).toEqual(e.rotated_bbox);expect(a.direction_deg).toBe(e.direction_deg);}
 }
}
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,chooseFolder:(dir:string)=>Promise<void>,download:Download,native:boolean,url?:string){
 const source=path.join(workspace.root,'format-source');const paths=['batch-a/part.png','batch-b/part.png'].map((name,i)=>{const file=path.join(source,name);fs.mkdirSync(path.dirname(file),{recursive:true});fs.writeFileSync(file,png(256,3,(x,y)=>[x,y,80+i*50]));return file;});
 const original=paths.map(file=>({path:file,sha256:sha(file)}));
 const project=await api('/api/project/create',{name:'Format roundtrip fixture',task:'segmentation'});await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const query=(file:string)=>`/api/annotations/part?file_path=${encodeURIComponent(file)}`;
 const geometry=(i:number,obb:boolean)=>[
  {id:`box-${i}`,type:'bbox',label:'Scratch',category_id:7,color:'#f59e0b',bbox:[20.25+i,20.5,70.75+i,60.5]},
  {id:`poly-${i}`,type:'polygon',label:'Crack',category_id:23,color:'#ef4444',polygon:[[120.5,30.25+i],[180.75,30.25+i],[150.5,80.75+i]]},
  ...(obb?[{id:`obb-${i}`,type:'rotated_bbox',label:'Void',category_id:31,color:'#a855f7',rotated_bbox:[120,170+i,80,40,30],direction_deg:315}]:[])];
 const seed=async(obb:boolean)=>{for(let i=0;i<paths.length;i++){const current=await api(query(paths[i]));await api('/api/annotations/save',{image_id:'part',image_path:paths[i],image_width:256,image_height:256,annotations:geometry(i,obb),actor:'format-owner',expected_revision:current.metadata.revision});}};
 await seed(true);if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Format roundtrip fixture');
 const stages=page.getByRole('navigation',{name:'Workflow Stages'});
 const openPanel=async()=>{await stages.getByRole('button').nth(0).click();const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});await expect(panel).toContainText('검색 결과 2개');await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('format-owner');const summary=panel.getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();return panel;};
 const receipts:any[]=[];
 let panel=await openPanel();
 // Lossy formats must stop before a bundle or a source edit is written.
 for(const format of ['coco','yolo']){
  await panel.getByLabel('라벨 교환 형식',{exact:true}).selectOption(format);const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/formats/export'&&r.request().method()==='POST');await panel.getByRole('button',{name:'라벨 내보내기',exact:true}).click();expect((await response).status()).toBe(422);await expect(panel.getByRole('alert')).toContainText('Direction targets require LabelMe');
 }
 expect(fs.existsSync(path.join(project.project_dir,'exports'))).toBe(false);
 for(const format of ['labelme','coco','yolo']){
  if(format!=='labelme')await seed(false);
  const beforeMasks=await Promise.all(paths.map(async file=>{const saved=await api(query(file));return sha(saved.mask_file);}));
  panel=await openPanel();await panel.getByLabel('라벨 교환 형식',{exact:true}).selectOption(format);
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/formats/export'&&r.request().method()==='POST');
  const zip=await download(()=>panel.getByRole('button',{name:'라벨 내보내기',exact:true}).click(),format);const exported=await (await response).json();
  expect(exported.image_count).toBe(2);expect(exported.annotation_count).toBe(format==='labelme'?6:4);await expect(panel.getByRole('status').filter({hasText:'내보내기 완료'})).toBeVisible();
  const dir=path.join(workspace.root,`exchange-${format}`);fs.mkdirSync(dir);
  execFileSync(process.env.MV_E2E_PYTHON||'python3',['-c',"import sys,zipfile,pathlib\nr=pathlib.Path(sys.argv[2]).resolve()\nwith zipfile.ZipFile(sys.argv[1]) as z:\n for name in z.namelist():\n  p=(r/name).resolve();assert p.is_relative_to(r);assert not name.startswith('/')\n z.extractall(r)",zip,dir]);
  await chooseFolder(dir);await panel.getByRole('button',{name:'라벨 폴더 선택',exact:true}).click();await expect(panel.getByText(dir,{exact:true})).toBeVisible();
  await panel.getByLabel('기존 라벨 충돌 처리',{exact:true}).selectOption('replace');
  const previewResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/formats/import'&&r.request().method()==='POST');await panel.getByRole('button',{name:'가져오기 미리보기',exact:true}).click();const previewReply=await previewResponse;expect(previewReply.status(),await previewReply.text()).toBe(200);const preview=await previewReply.json();
  expect(preview.preview.map((r:any)=>r.file_name).sort()).toEqual(['batch-a/part.png','batch-b/part.png']);expect(preview.preview.every((r:any)=>r.conflict)).toBe(true);
  const appliedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/formats/import'&&r.request().method()==='POST');await panel.getByRole('button',{name:'검토한 라벨 적용',exact:true}).click();const appliedReply=await appliedResponse;expect(appliedReply.status(),await appliedReply.text()).toBe(200);const applied=await appliedReply.json();expect(applied.backup_version_id).toBeTruthy();await expect(panel.getByRole('status').filter({hasText:'이전 버전'})).toBeVisible();
  const reopened:any[]=[];
  for(let i=0;i<paths.length;i++){
   const saved=await api(query(paths[i]));checkGeometry(saved.annotations,geometry(i,format==='labelme'));expect(sha(paths[i])).toBe(original[i].sha256);
   expect(sha(saved.mask_file)).toBe(beforeMasks[i]);
   const diskJson=path.join(path.dirname(path.dirname(saved.mask_file)),'part.json');expect(JSON.parse(fs.readFileSync(diskJson,'utf8')).annotations).toEqual(saved.annotations);
   const snapshotJson=path.join(workspace.logs,`${format}-${i}-annotation.json`),snapshotMask=path.join(workspace.logs,`${format}-${i}-mask.png`);
   fs.copyFileSync(diskJson,snapshotJson);fs.copyFileSync(saved.mask_file,snapshotMask);evidence.addFile(snapshotJson);evidence.addFile(snapshotMask);
   panel=await openPanel();const row=panel.getByRole('row').filter({hasText:`batch-${i===0?'a':'b'}/part.png`});await row.getByRole('button',{name:'2단계 검토',exact:true}).click();
   await expect(page.getByRole('button',{name:/^Scratch 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Crack 1$/})).toBeVisible();
   if(format==='labelme')await expect(page.getByRole('button',{name:/^Void 1$/})).toBeVisible();
   await evidence.screenshot(page,`${native?'native':'browser'}-${format}-batch-${i===0?'a':'b'}-reopened`);
   reopened.push({file_path:paths[i],annotation_snapshot:saved,canonical_annotations:canonical(saved.annotations),snapshot_json:snapshotJson,snapshot_mask:snapshotMask,
    preexport_mask_sha256:beforeMasks[i],exact_mask_bytes_after_roundtrip:true});
  }
  const sourceZip=path.join(project.project_dir,'exports',path.basename(exported.download_url)+'.zip');expect(sha(zip)).toBe(sha(sourceZip));
  evidence.addFile(zip);receipts.push({format,downloaded_zip:zip,downloaded_sha256:sha(zip),source_zip:sourceZip,import_dir:dir,preview,applied,reopened});
 }
 evidence.note('format_roundtrip',{project,source,original,receipts,actual_export_preview_replace_reopen:true,native_folder_dialog_stubbed:native,browser_folder_bridge_stubbed:!native,
  independent_obb_direction_preserved:true,lossy_obb_direction_export_rejected:true,representative_label_quality:false,new_gpu_training:false});
}
test('app format bundles preserve two nested images and native class meaning',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 const folder=(dir:string)=>page.evaluate(value=>{(window as any).api.selectFolder=async()=>value;},dir);
 const download:Download=async(action,format)=>{const event=page.waitForEvent('download');await action();const item=await event;const target=path.join(workspace.logs,`${format}-download.zip`);await item.saveAs(target);expect(await item.failure()).toBeNull();return target;};
 await exercise(page,workspace,evidence,api,folder,download,false,renderer.url);
});
test('native app format bundles preserve two nested images and native class meaning',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window,app}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned format fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});
 const folder=(dir:string)=>app.evaluate(({dialog},value)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[value]});},dir);
 const download:Download=async(action,format)=>{const target=path.join(workspace.logs,`${format}-download.zip`);await app.evaluate(({session},file)=>{(globalThis as any).__formatDownload=null;session.defaultSession.once('will-download',(_event,item)=>{item.setSavePath(file);item.once('done',(_ev,state)=>{(globalThis as any).__formatDownload=state;});});},target);await action();await expect.poll(()=>app.evaluate(()=> (globalThis as any).__formatDownload)).toBe('completed');return target;};
 await exercise(window,workspace,evidence,api,folder,download,true);
});

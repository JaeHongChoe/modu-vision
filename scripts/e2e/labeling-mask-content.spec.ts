import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const hash=(value:Buffer|string)=>crypto.createHash('sha256').update(value).digest('hex');
async function pixels(page:Page,data:string){return page.evaluate(async data=>{
 const image=new Image();await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(Error('Fixture mask decode failed'));image.src=data;});
 const canvas=document.createElement('canvas');canvas.width=image.width;canvas.height=image.height;const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);
 const values=ctx.getImageData(0,0,image.width,image.height).data;let count=0;for(let i=3;i<values.length;i+=4)if(values[i]>0)count++;
 return {width:image.width,height:image.height,painted:count};
},data);}
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'mask-source');fs.mkdirSync(source,{recursive:true});const imagePath=path.join(source,'part.png');
 fs.writeFileSync(imagePath,png(256,3,(x,y)=>[x,y,80]));const originalSha=hash(fs.readFileSync(imagePath));
 const project=await api('/api/project/create',{name:'Mask content fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const team=await api('/api/team-data');await api('/api/team-data/settings',{expected_revision:team.settings.revision,actor:'mask-owner',changes:{editing_enabled:true}},'PUT');
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Mask content fixture');
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 const focus=page.getByRole('button',{name:'집중 편집',exact:true});await focus.click();
 await page.getByRole('button',{name:/팀 작업 · 라벨 기준·검수/}).click();const dialog=page.getByRole('dialog',{name:'팀 데이터 작업'});
 await dialog.getByLabel('팀 작업자 이름').fill('mask-owner');await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
 await page.getByRole('button',{name:'편집 시작',exact:true}).click();await expect(page.getByText('현재 이미지 편집 중',{exact:true})).toBeVisible();
 const query=`/api/annotations/part?file_path=${encodeURIComponent(imagePath)}`;
 const stroke=async(offset:number,from:number,to:number)=>{
  const bounds=(await page.locator('[data-canvas-container]').boundingBox())!;const x=bounds.x+bounds.width/2,y=bounds.y+bounds.height/2+offset;
  await page.mouse.move(x+from,y);await page.mouse.down();await page.mouse.move(x+to,y,{steps:12});await page.mouse.up();
  await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toBeVisible();
 };
 const save=async()=>{
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await response).status()).toBe(200);return api(query);
 };
 await page.getByRole('button',{name:/^Solder Bridge(?: \d+)?$/}).click();await page.getByTitle('브러시 마스크 (Brush - 5)',{exact:true}).click();
 await stroke(0,-50,50);const first=await save();const firstMask=first.annotations.find((a:any)=>a.label==='Solder Bridge');expect(firstMask.type).toBe('brush_mask');
 const firstPixels=await pixels(page,firstMask.mask_rle);expect(firstPixels.painted).toBeGreaterThan(100);
 await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await stroke(60,-50,50);const both=await save();
 const otherMask=both.annotations.find((a:any)=>a.label==='Scratch');expect(otherMask.type).toBe('brush_mask');const otherSha=hash(otherMask.mask_rle);
 expect(both.annotations.find((a:any)=>a.label==='Solder Bridge').mask_rle).toBe(firstMask.mask_rle);
 const release=async()=>{const reply=page.waitForResponse(r=>new URL(r.url()).pathname.endsWith('/lease/release')&&r.request().method()==='POST');await page.getByRole('button',{name:'편집 종료',exact:true}).click();const response=await reply;expect(response.status()).toBe(200);expect((await response.json()).image.team.edit_lease).toBeNull();};
 await release();
 await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Mask content fixture');
 await expect(page.getByRole('button',{name:'집중 편집',exact:true})).toHaveAttribute('aria-pressed','true');
 await page.getByRole('button',{name:'편집 시작',exact:true}).click();await expect(page.getByText('현재 이미지 편집 중',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:/^Solder Bridge(?: \d+)?$/}).click();
 // Wait for the selected class raster to be decoded after the real reload.
 await expect(page.getByTitle('마스크 지우개 (Eraser - 6)',{exact:true})).toBeVisible();await page.getByTitle('마스크 지우개 (Eraser - 6)',{exact:true}).click();
 await stroke(0,-8,8);const erased=await save();const erasedMask=erased.annotations.find((a:any)=>a.label==='Solder Bridge');const erasedPixels=await pixels(page,erasedMask.mask_rle);
 expect(erasedPixels.painted).toBeGreaterThan(0);expect(erasedPixels.painted).toBeLessThan(firstPixels.painted);
 expect(hash(erased.annotations.find((a:any)=>a.label==='Scratch').mask_rle)).toBe(otherSha);
 await page.getByTitle('Undo (Ctrl+Z)',{exact:true}).click();const undone=await save();expect(undone.annotations.find((a:any)=>a.label==='Solder Bridge').mask_rle).toBe(firstMask.mask_rle);
 await page.getByTitle('Redo (Ctrl+Y)',{exact:true}).click();const redone=await save();expect(redone.annotations.find((a:any)=>a.label==='Solder Bridge').mask_rle).toBe(erasedMask.mask_rle);
 await release();
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Scratch 1$/})).toBeVisible();
 const reopened=await api(query);expect(reopened.annotations.find((a:any)=>a.label==='Solder Bridge').mask_rle).toBe(erasedMask.mask_rle);
 expect(hash(reopened.annotations.find((a:any)=>a.label==='Scratch').mask_rle)).toBe(otherSha);expect(hash(fs.readFileSync(imagePath))).toBe(originalSha);
 await evidence.screenshot(page,native?'native-two-class-mask-reopened':'browser-two-class-mask-reopened');
 evidence.note('mask_content',{project_id:project.id,image_path:imagePath,image_sha256:originalSha,first_pixels:firstPixels,erased_pixels:erasedPixels,
  first_mask_sha256:hash(firstMask.mask_rle),erased_mask_sha256:hash(erasedMask.mask_rle),other_mask_sha256:otherSha,
  annotation_snapshot:reopened,original_unchanged:true,actual_ui_brush:true,actual_ui_eraser:true,undo_redo_exact:true,reloaded_two_classes:true,
  explicit_owned_fixture_lease:true,released_before_reload:true,representative_annotation_quality:false,gpu_or_model_training:false});
}
test('two-class brush erase undo redo and reload preserve exact mask content',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native two-class brush erase undo redo and reload preserve exact mask content',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned mask fixture API: HTTP ${response.status}`);return response.json();
 },{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

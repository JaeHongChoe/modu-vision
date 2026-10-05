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
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();
 await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 const point=async(x:number,y:number)=>{const b=(await page.locator('[data-canvas-container]').boundingBox())!;return{x:b.x+(b.width-256)/2+x,y:b.y+(b.height-256)/2+y};};
 const drag=async(x1:number,y1:number,x2:number,y2:number)=>{const a=await point(x1,y1),b=await point(x2,y2);await page.mouse.move(a.x,a.y);await page.mouse.down();await page.mouse.move(b.x,b.y,{steps:8});await page.mouse.up();};
 const query=`/api/annotations/part?file_path=${encodeURIComponent(imagePath)}`;
 const save=async()=>{const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await reply).status()).toBe(200);return api(query);};
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();await drag(20,20,70,60);const boxSaved=await save();
 const box=boxSaved.annotations.find((a:any)=>a.type==='bbox');expect(box.label).toBe('Solder Bridge');expect(box.category_id).toBe(1);expect(box.bbox).toEqual([20,20,70,60]);
 await page.getByRole('button',{name:/^Scratch(?: \d+)?$/}).click();await page.getByTitle('다각형 폴리곤 (Polygon - 4)',{exact:true}).click();
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();
 const polygon=[[120,30],[180,30],[150,80]];for(const [x,y] of polygon){const p=await point(x,y);await page.mouse.click(p.x,p.y);}await page.keyboard.press('Enter');const polygonSaved=await save();
 const poly=polygonSaved.annotations.find((a:any)=>a.type==='polygon');expect(poly.label).toBe('Scratch');expect(poly.category_id).toBe(2);expect(poly.polygon).toEqual(polygon);expect(poly.points).toEqual(polygon);
 await page.getByRole('button',{name:/^Crack(?: \d+)?$/}).click();await page.getByTitle('회전 바운딩 박스 (Rotated BBox OBB - 3)',{exact:true}).click();await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await drag(70,150,150,190);
 await page.getByLabel('객체 독립 방향 라벨',{exact:true}).fill('315');
 const angle=page.getByText('Angle (θ)',{exact:true}).locator('..').locator('..').getByRole('spinbutton');await angle.fill('30');
 const rotatedSaved=await save();const rotated=rotatedSaved.annotations.find((a:any)=>a.type==='rotated_bbox');expect(rotated.label).toBe('Crack');expect(rotated.category_id).toBe(3);expect(rotated.rotated_bbox).toEqual([110,170,80,40,30]);expect(rotated.direction_deg).toBe(315);
 // A real keyboard nudge preserves the independent direction and angle.
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();await page.keyboard.press('Shift+ArrowRight');const shiftedSaved=await save();const shifted=shiftedSaved.annotations.find((a:any)=>a.type==='rotated_bbox');
 expect(shifted.rotated_bbox).toEqual([120,170,80,40,30]);expect(shifted.direction_deg).toBe(315);
 expect(shiftedSaved.annotations.find((a:any)=>a.type==='bbox').bbox).toEqual(box.bbox);expect(shiftedSaved.annotations.find((a:any)=>a.type==='polygon').polygon).toEqual(polygon);
 await page.getByTitle('Delete annotation',{exact:true}).nth(0).click();const deleted=await save();expect(deleted.annotations).toHaveLength(2);expect(deleted.annotations.some((a:any)=>a.type==='bbox')).toBe(false);
 await page.getByTitle('Undo (Ctrl+Z)',{exact:true}).click();const restored=await save();expect(restored.annotations).toEqual(shiftedSaved.annotations);
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Scratch 1$/})).toBeVisible();await expect(page.getByRole('button',{name:/^Crack 1$/})).toBeVisible();
 const reopened=await api(query);expect(reopened.annotations).toEqual(restored.annotations);expect(hash(fs.readFileSync(imagePath))).toBe(originalSha);
 const raster=await page.evaluate(async data=>{const img=new Image();await new Promise<void>((resolve,reject)=>{img.onload=()=>resolve();img.onerror=()=>reject(Error('Saved vector raster decode failed'));img.src=data;});const canvas=document.createElement('canvas');canvas.width=img.width;canvas.height=img.height;const ctx=canvas.getContext('2d')!;ctx.drawImage(img,0,0);return{width:img.width,height:img.height,classes:[[45,40],[150,45],[120,170]].map(([x,y])=>ctx.getImageData(x,y,1,1).data[0])};},'data:image/png;base64,'+fs.readFileSync(reopened.mask_file).toString('base64'));
 expect(raster).toEqual({width:256,height:256,classes:[1,2,3]});
 // Select the reopened OBB through its actual inspector row, then check the two independent values.
 await page.getByTitle('Delete annotation',{exact:true}).nth(2).locator('..').locator('..').click();
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

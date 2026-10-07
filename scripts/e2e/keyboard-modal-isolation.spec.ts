import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type { Page } from '@playwright/test';
import { test, expect, type Workspace, type Evidence } from './fixtures/test';
import { installDesktopHostShim } from './fixtures/desktop-host-shim';
import { png } from './qa/appFlow';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(workspace.root,'modal-source');fs.mkdirSync(source,{recursive:true});
 const image=path.join(source,'part.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,70]));
 const sha=crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex');
 const project=await api('/api/project/create',{name:'Keyboard modal isolation',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();
 const bounds=(await page.locator('[data-canvas-container]').boundingBox())!;
 const origin={x:bounds.x+(bounds.width-256)/2,y:bounds.y+(bounds.height-256)/2};
 await page.mouse.move(origin.x+20,origin.y+20);await page.mouse.down();await page.mouse.move(origin.x+70,origin.y+60,{steps:8});await page.mouse.up();
 const query=`/api/annotations/part?file_path=${encodeURIComponent(image)}`;
 const save=async()=>{const pending=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await pending).status()).toBe(200);return api(query);};
 const initial=await save();expect(initial.annotations).toHaveLength(1);expect(initial.annotations[0].bbox).toEqual([20,20,70,60]);
 await page.getByTitle('선택 및 이동 (Select / Move - 1)',{exact:true}).click();
 await page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click();
 await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 const opener=page.getByRole('button',{name:/팀 작업 · 라벨 기준·검수/});await opener.click();
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});await expect(dialog).toBeVisible();
 const close=dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});await close.focus();await expect(close).toBeFocused();
 for(const key of ['ArrowRight','Delete','Control+z','Control+y']){
  await page.keyboard.press(key);
  await expect(dialog).toBeVisible();await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
  await expect(page.getByRole('button',{name:'Save Changes',exact:true})).toHaveCount(0);
 }
 await page.keyboard.press('Escape');await expect(dialog).not.toBeVisible();await expect(opener).toBeFocused();
 // The same shortcuts must still work after dismissing the modal.
 await page.keyboard.press('ArrowRight');const nudged=await save();expect(nudged.annotations[0].bbox).toEqual([21,20,71,60]);
 await page.getByTitle('Delete annotation',{exact:true}).locator('..').locator('..').click();
 await page.keyboard.press('Delete');await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(0);
 await page.keyboard.press('Control+z');await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
 const restored=await save();expect(restored.annotations).toEqual(nudged.annotations);
 await page.reload();await expect(page.getByRole('button',{name:/^Solder Bridge 1$/})).toBeVisible();
 expect((await api(query)).annotations).toEqual(restored.annotations);
 expect(crypto.createHash('sha256').update(fs.readFileSync(image)).digest('hex')).toBe(sha);
 await evidence.screenshot(page,native?'native-modal-keyboard-content-reopened':'browser-modal-keyboard-content-reopened');
 evidence.note('modal_keyboard_isolation',{project_id:project.id,image_sha256:sha,initial:initial.annotations,nudged:nudged.annotations,
  restored:restored.annotations,blocked_modal_keys:['ArrowRight','Delete','Control+z','Control+y'],
  close_focus_restored:true,post_close_shortcuts_work:true,actual_draw_save_reopen:true,original_image_unchanged:true,
  native,physical_keyboard_or_human_annotation_quality:false});
}
test('modal keyboard cannot edit the selected background annotation',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native modal keyboard cannot edit the selected background annotation',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned modal API ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});
 await exercise(window,workspace,evidence,api,true);
});

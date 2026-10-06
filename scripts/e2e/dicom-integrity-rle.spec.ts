import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
test.use({actionTimeout:15_000});
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string,mode='single'){
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/dicom_control.py'),path.join(workspace.root,'dicom-source'),mode],{encoding:'utf8',timeout:30_000}));
 await api('/api/project/create',{name:'DICOM pack controlled CPU',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:fixture.source},'PUT');
 await api('/api/dataset/import',{folder_path:fixture.source,task:'segmentation'});
 const open=async()=>{if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('DICOM pack controlled CPU');
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const panel=page.getByLabel('DICOM 입력 표시',{exact:true});await expect(panel).toBeVisible();
  await panel.locator('summary').click();return panel;};
 let panel=await open();
 const apply=async()=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/dicom/view'&&r.request().method()==='POST');
  await panel.getByRole('button',{name:'Window 적용·정보 조회',exact:true}).click();return response;};
 await panel.getByLabel('DICOM window center',{exact:true}).fill('200');
 await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 let response=await apply();expect(response.status(),await response.text()).toBe(200);const first=await response.json();
 expect(first).toMatchObject({width:32,height:16,source_sha256:fixture.sha256,frame_index:0,window_center:200,window_width:400});
 if(mode==='rle-multi'){
  expect(first.frames).toBe(2);expect(first.transfer_syntax_uid).toBe('1.2.840.10008.1.2.5');
  const saves:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&new URL(r.url()).pathname==='/api/annotations/save')saves.push(r.url());});
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('1');response=await apply();expect(response.status(),await response.text()).toBe(200);const second=await response.json();
  expect(second.frame_index).toBe(1);expect(second.source_sha256).toBe(first.source_sha256);expect(second.view_sha256).not.toBe(first.view_sha256);
  await expect(panel).toContainText(second.view_sha256);
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('2');response=await apply();expect(response.status()).toBe(422);
  await expect(panel.getByRole('alert')).toContainText('outside available frames');await expect(panel).toContainText(second.view_sha256);
  await panel.getByLabel('DICOM frame index',{exact:true}).fill('0');response=await apply();expect(response.status()).toBe(200);expect((await response.json()).view_sha256).toBe(first.view_sha256);
  expect(saves).toEqual([]);await evidence.screenshot(page,'actual-rle-distinct-frame-out-of-range-refusal');
 }
 await expect(panel).toContainText(first.source_sha256);await expect(panel).toContainText(first.view_sha256);
 await evidence.screenshot(page,'actual-dicom-optional-pack-window-and-source-hashes');
 await panel.getByLabel('DICOM window width',{exact:true}).fill('0');response=await apply();expect(response.status()).toBe(422);
 await expect(panel.getByRole('alert')).toContainText('window_width: Input should be greater than 0');await expect(panel).toContainText(first.view_sha256);
 await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 const blocked='**/api/dataset/dicom/view';await page.route(blocked,r=>r.abort('failed'));
 await panel.getByRole('button',{name:'Window 적용·정보 조회',exact:true}).click();await expect(panel.getByRole('alert')).toBeVisible();
 await page.unroute(blocked);await expect(panel).toContainText(first.view_sha256);
 await evidence.screenshot(page,'actual-dicom-invalid-network-refusal-preserves-display');
 panel=await open();await expect(panel.getByLabel('DICOM window width',{exact:true})).toHaveValue('');
 await panel.getByLabel('DICOM window center',{exact:true}).fill('200');await panel.getByLabel('DICOM window width',{exact:true}).fill('400');
 response=await apply();expect(response.status()).toBe(200);const reopened=await response.json();
 expect(reopened.view_sha256).toBe(first.view_sha256);expect(reopened.view_path).toBe(first.view_path);
 expect(crypto.createHash('sha256').update(fs.readFileSync(fixture.file)).digest('hex')).toBe(fixture.sha256);
 await evidence.screenshot(page,'actual-dicom-reopened-owned-view-source-unchanged');
 evidence.note('dicom_runtime',{actual_ui_and_backend:true,synthetic_non_patient_input:true,source_sha256:fixture.sha256,
  view_sha256:first.view_sha256,source_unchanged:true,provider:'pydicom',mode,frames:fixture.frames,
  transfer_syntax_uid:fixture.transfer_syntax_uid,quality_approved:false,
  builtin_rle_view_qualified:mode==='rle-multi',other_compressed_decoders_qualified:false,frame_labeling_qualified:false});
}
test('DICOM optional runtime preserves source through window errors and reopen',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,renderer.url);
});
test('native DICOM optional runtime preserves source through window errors and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned DICOM HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api);
});
test('DICOM built-in RLE view selects distinct frames and refuses an unavailable frame',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};
 await exercise(page,workspace,evidence,api,renderer.url,'rle-multi');
});
test('native DICOM built-in RLE view selects distinct frames and refuses an unavailable frame',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned DICOM HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,undefined,'rle-multi');
});

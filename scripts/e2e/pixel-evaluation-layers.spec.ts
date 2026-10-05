import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'pixel-source');fs.mkdirSync(source);const original=path.join(source,'part.png');fs.writeFileSync(original,png(64,3,(x,y)=>[x,y,60]));const originalHash=sha(fs.readFileSync(original));
 const project=await api('/api/project/create',{name:'Pixel evaluation display fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/project/labelsets',{name:'Second display labelset'});const sets=await api('/api/project/labelsets');const secondSet=sets.labelsets.find((entry:any)=>entry.id!=='default').id;
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/pixel_evaluation_reports.py'),workspace.root,project.project_dir,source,secondSet],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const writes:string[]=[];page.on('request',request=>{if(request.method()!=='GET'&&/\/(evaluation|train|jobs)(\/|$)/.test(new URL(request.url()).pathname))writes.push(`${request.method()} ${new URL(request.url()).pathname}`);});
 const open=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Pixel evaluation display fixture');await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();await page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'}).click();await page.getByLabel('평가 이력 모델 종류',{exact:true}).selectOption('segmentation');};
 await open();
 const history=page.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'})}).first();
 const selector=history.getByLabel(/^모델별 저장 평가/);const captures:any[]=[];const refusals:any[]=[];
 const selectSet=async(id:string)=>{await history.getByLabel('평가 라벨 세트',{exact:true}).selectOption(id);const ids=fixture.items.filter((item:any)=>item.labelset_id===id).map((item:any)=>item.record.evaluation_id).sort();await expect.poll(async()=>selector.locator('option').evaluateAll(nodes=>nodes.map(node=>(node as HTMLOptionElement).value).sort())).toEqual(ids);};
 const openRecord=async(item:any)=>{await selector.selectOption(item.record.evaluation_id);const details=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await details.getAttribute('open')===null)await details.locator('summary').first().click();return details;};
 for(const labelsetId of ['default',secondSet]){
  await selectSet(labelsetId);const item=fixture.items.find((entry:any)=>entry.labelset_id===labelsetId&&entry.variant==='valid');const details=await openRecord(item);
  const version=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();await version.locator('summary').click();await expect(version.locator('pre')).toContainText(item.record.evaluation_id);await expect(version.locator('pre')).toContainText(item.record.evidence_sha256);
  for(const className of ['all','Scratch','Crack'])for(const view of ['truth','prediction','error']){
   await details.getByLabel('평가 증거 클래스',{exact:true}).selectOption(className);await details.getByLabel('평가 마스크 보기',{exact:true}).selectOption(view);
   const canvas=details.locator('canvas');await expect.poll(()=>canvas.evaluate(node=>({width:(node as HTMLCanvasElement).width,height:(node as HTMLCanvasElement).height}))).toEqual({width:64,height:64});await expect(details.getByRole('status')).toHaveCount(0);await expect(details.getByRole('alert')).toHaveCount(0);
   const output=await canvas.evaluate(node=>{const canvas=node as HTMLCanvasElement;return {pixels:Array.from(canvas.getContext('2d')!.getImageData(0,0,canvas.width,canvas.height).data),png:canvas.toDataURL('image/png')};});
   const expected:number[]=[];const classId=className==='all'?null:className==='Scratch'?7:23;
   const inside=(x:number,y:number,l:number,t:number,w:number,h:number)=>x>=l&&x<l+w&&y>=t&&y<t+h;
   for(let y=0;y<64;y++)for(let x=0;x<64;x++){
    const ty=labelsetId==='default'?y:63-y,px=labelsetId==='default'?x:63-x;
    const truth=inside(x,ty,8,8,16,16)?7:inside(x,ty,32,16,16,24)?23:0,pred=inside(px,y,12,8,16,16)?7:inside(px,y,32,20,16,24)?23:0;
    const actual=classId===null?truth>0:truth===classId,guess=classId===null?pred>0:pred===classId;
    expected.push(...(view==='truth'?(actual?[74,222,128,255]:[35,35,35,255]):view==='prediction'?(guess?[34,211,238,255]:[35,35,35,255]):[actual&&truth!==pred?244:35,guess&&truth!==pred?180:35,guess&&truth!==pred?255:35,255]));
   }
   expect(output.pixels).toEqual(expected);const file=path.join(workspace.logs,`${labelsetId}-${className}-${view}.png`);fs.writeFileSync(file,Buffer.from(output.png.split(',')[1],'base64'));evidence.addFile(file);captures.push({evaluation_id:item.record.evaluation_id,labelset_id:labelsetId,className,view,path:file,sha256:sha(fs.readFileSync(file)),rgba_sha256:sha(Buffer.from(output.pixels))});
   if(className==='all'){await canvas.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-${labelsetId}-${view}`);}
  }
 }
 for(const variant of ['missing_truth','unequal_size','wrong_shape']){
  const valid=fixture.items.find((item:any)=>item.labelset_id===secondSet&&item.variant==='valid');await openRecord(valid);await expect.poll(()=>history.locator('canvas').evaluate(node=>(node as HTMLCanvasElement).width)).toBe(64);
  const item=fixture.items.find((entry:any)=>entry.variant===variant),details=await openRecord(item);await expect(details.getByRole('alert')).toContainText(variant==='missing_truth'?'정답':'크기');await expect.poll(()=>details.locator('canvas').evaluate(node=>({width:(node as HTMLCanvasElement).width,height:(node as HTMLCanvasElement).height}))).toEqual({width:0,height:0});await details.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-${variant}-refused`);refusals.push({variant,evaluation_id:item.record.evaluation_id,error:await details.getByRole('alert').innerText(),canvas_cleared:true});
 }
 await open();await selectSet('default');await openRecord(fixture.items[0]);await expect.poll(()=>history.locator('canvas').evaluate(node=>(node as HTMLCanvasElement).width)).toBe(64);
 for(const item of fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);const stored=await api(`/api/evaluation/history/${item.record.evaluation_id}?source_dataset_path=${encodeURIComponent(source)}&task=segmentation`);expect(stored).toEqual(item.record);evidence.addFile(item.report_path);}
 for(const input of fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);evidence.addFile(input.path);}
 expect(sha(fs.readFileSync(original))).toBe(originalHash);expect(writes).toEqual([]);
 evidence.note('pixel_layers',{fixture,project_id:project.id,source_path:original,source_sha256:originalHash,captures,refusals,reopened_default:true,evaluation_training_writes:writes,controlled_reports_not_model_inference:true});
}
test('saved labelset evaluations expose exact truth, prediction and error pixels and refuse stale masks',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved labelset evaluations expose exact truth, prediction and error pixels and refuse stale masks',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned evaluation fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

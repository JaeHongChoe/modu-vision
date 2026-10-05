import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'overlay-source');fs.mkdirSync(source);const original=path.join(source,'part.png');
 execFileSync(harness.resolvePython(),['-c',"import sys,numpy as np;from PIL import Image;y,x=np.indices((96,160));Image.fromarray(np.dstack([x,y,np.full_like(x,60)]).astype(np.uint8)).save(sys.argv[1])",original],{timeout:30_000});
 const originalBytes=fs.readFileSync(original),originalHash=sha(originalBytes);
 const project=await api('/api/project/create',{name:'Saved evaluation source overlay fixture',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/project/labelsets',{name:'Second source overlay labelset'});const sets=await api('/api/project/labelsets'),secondSet=sets.labelsets.find((entry:any)=>entry.id!=='default').id;
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/pixel_evaluation_reports.py'),workspace.root,project.project_dir,source,secondSet,'bound'],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const writes:string[]=[];page.on('request',request=>{if(request.method()!=='GET'&&/\/(evaluation|train|jobs)(\/|$)/.test(new URL(request.url()).pathname))writes.push(`${request.method()} ${new URL(request.url()).pathname}`);});
 const open=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Saved evaluation source overlay fixture');await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();await page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'}).click();};await open();
 const history=page.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'})}).first(),selector=history.getByLabel(/^모델별 저장 평가/);
 const choose=async(item:any)=>{await history.getByLabel('평가 라벨 세트',{exact:true}).selectOption(item.labelset_id);await expect.poll(()=>selector.locator('option').evaluateAll(nodes=>nodes.map(node=>(node as HTMLOptionElement).value).sort())).toEqual(fixture.items.filter((r:any)=>r.labelset_id===item.labelset_id).map((r:any)=>r.record.evaluation_id).sort());await selector.selectOption(item.record.evaluation_id);const details=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await details.getAttribute('open')===null)await details.locator('summary').first().click();await expect(details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeEnabled();await expect(details.getByRole('img',{name:'평가 원본',exact:true})).toHaveCount(0);return details;};
 const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true}),captures:any[]=[],refusals:any[]=[];
 const inside=(x:number,y:number,l:number,t:number,w:number,h:number)=>x>=l&&x<l+w&&y>=t&&y<t+h;
 for(const labelsetId of ['default',secondSet]){
  const item=fixture.items.find((r:any)=>r.labelset_id===labelsetId&&r.variant==='valid'),details=await choose(item);
  for(const className of ['all','Scratch','Crack']){
   await details.getByLabel('평가 증거 클래스',{exact:true}).selectOption(className);await details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await expect(viewer).toBeVisible();await expect(viewer).toContainText(item.record.evaluation_id);await expect(viewer).toContainText(originalHash);await expect(viewer.getByLabel('근거 이미지 종류')).toHaveValue('original');await expect(viewer.getByLabel('근거 겹침 이미지').locator('option')).toHaveCount(4);
   for(const view of ['truth','prediction','error']){
    await viewer.getByLabel('근거 겹침 이미지').selectOption(view);const src=(await viewer.locator('img').nth(1).getAttribute('src'))!;
    const actual=await viewer.locator('img').nth(1).evaluate(async node=>{const img=node as HTMLImageElement;await img.decode();const canvas=document.createElement('canvas');canvas.width=img.naturalWidth;canvas.height=img.naturalHeight;const ctx=canvas.getContext('2d')!;ctx.drawImage(img,0,0);return {width:canvas.width,height:canvas.height,pixels:Array.from(ctx.getImageData(0,0,canvas.width,canvas.height).data)};});expect([actual.width,actual.height]).toEqual([160,96]);
    const expected:number[]=[];const classId=className==='all'?null:className==='Scratch'?7:23;
    for(let y=0;y<96;y++)for(let x=0;x<160;x++){
     const mx=Math.floor(x*64/160),my=Math.floor(y*64/96),ty=labelsetId==='default'?my:63-my,px=labelsetId==='default'?mx:63-mx;
     const truth=inside(mx,ty,8,8,16,16)?7:inside(mx,ty,32,16,16,24)?23:0,pred=inside(px,my,12,8,16,16)?7:inside(px,my,32,20,16,24)?23:0,a=classId===null?truth>0:truth===classId,b=classId===null?pred>0:pred===classId;
     const color=view==='truth'?(a?[74,222,128,255]:[0,0,0,0]):view==='prediction'?(b?[34,211,238,255]:[0,0,0,0]):[a&&truth!==pred?244:35,b&&truth!==pred?180:35,b&&truth!==pred?255:35,255];if(view==='error'&&color.slice(0,3).every(v=>v===35))color.splice(0,4,0,0,0,0);expected.push(...color);
    }
    expect(actual.pixels).toEqual(expected);const file=path.join(workspace.logs,`${labelsetId}-${className}-${view}-source-overlay.png`);fs.writeFileSync(file,Buffer.from(src.split(',')[1],'base64'));evidence.addFile(file);captures.push({evaluation_id:item.record.evaluation_id,labelset_id:labelsetId,className,view,path:file,sha256:sha(fs.readFileSync(file)),rgba_sha256:sha(Buffer.from(actual.pixels))});
    if(className==='all')await evidence.screenshot(page,`${native?'native':'browser'}-${labelsetId}-${view}-over-original`);
   }
   await viewer.getByLabel('근거 겹침 투명도').focus();for(let i=0;i<3;i++)await page.keyboard.press('ArrowRight');await expect(viewer.getByLabel('근거 겹침 투명도')).toHaveValue('0.65');await viewer.getByLabel('근거 이미지 확대').click();await expect(viewer.getByRole('status')).toHaveText('125%');const area=viewer.getByLabel('근거 이미지 이동 영역'),bounds=(await area.boundingBox())!;await page.mouse.move(bounds.x+80,bounds.y+80);await page.mouse.down();await page.mouse.move(bounds.x+120,bounds.y+105);await page.mouse.up();await expect(area.locator('div').first()).toHaveAttribute('style',/translate\(40px, 25px\) scale\(1.25\)/);await page.keyboard.press('Escape');await expect(viewer).toHaveCount(0);await expect(details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeFocused();
  }
 }
 for(const variant of ['missing_source_hash','unsupported_mapping']){const item=fixture.items.find((r:any)=>r.variant===variant),details=await choose(item);await details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await expect(details.getByRole('alert')).toContainText(variant==='missing_source_hash'?'source image hash':'좌표');await expect(viewer).toHaveCount(0);await expect.poll(()=>details.locator('canvas').evaluate(n=>(n as HTMLCanvasElement).width)).toBe(64);await details.getByRole('alert').scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-${variant}-overlay-refused`);refusals.push({variant,message:await details.getByRole('alert').innerText(),standalone_mask_retained:true});}
 const valid=fixture.items.find((r:any)=>r.labelset_id==='default'&&r.variant==='valid');let details=await choose(valid);
 try{execFileSync(harness.resolvePython(),['-c',"import sys;from PIL import Image;Image.new('RGB',(160,96),'black').save(sys.argv[1])",original]);await details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await expect(details.getByRole('alert')).toContainText('changed');await expect(viewer).toHaveCount(0);refusals.push({variant:'changed_source',message:await details.getByRole('alert').innerText(),standalone_mask_retained:true});}finally{fs.writeFileSync(original,originalBytes);}
 // Delay the real source request, change the selected labelset, then let the actual response finish.
 let release!:()=>void,entered!:()=>void;const gate=new Promise<void>(resolve=>release=resolve),started=new Promise<void>(resolve=>entered=resolve);const pattern=/\/api\/evaluation\/history\/[^/]+\/evidence-image\?/;
 await page.route(pattern,async route=>{entered();await gate;await route.continue();});const oldResponse=page.waitForResponse(response=>new URL(response.url()).pathname===`/api/evaluation/history/${valid.record.evaluation_id}/evidence-image`);await details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await started;
 const next=fixture.items.find((r:any)=>r.labelset_id===secondSet&&r.variant==='valid');details=await choose(next);release();expect((await oldResponse).status()).toBe(200);await page.unroute(pattern);await details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await expect(viewer).toContainText(next.record.evaluation_id);await expect(viewer).not.toContainText(valid.record.evaluation_id);await viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click();
 await open();await choose(valid);await history.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click();await expect(viewer).toContainText(valid.record.evaluation_id);await viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click();
 for(const item of fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);evidence.addFile(item.report_path);}for(const input of fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);evidence.addFile(input.path);}expect(sha(fs.readFileSync(original))).toBe(originalHash);expect(writes).toEqual([]);
 evidence.note('source_overlays',{fixture,project_id:project.id,source_path:original,source_sha256:originalHash,source_size:[160,96],captures,refusals,selection_during_pending_response_fenced:true,reopened_same_record:true,readonly_writes:writes,controlled_reports_not_model_inference:true});
}
test('saved segmentation overlays bind non-square source bytes and refuse missing or changed provenance',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved segmentation overlays bind non-square source bytes and refuse missing or changed provenance',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned source overlay API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

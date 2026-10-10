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
 const open=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Saved evaluation source overlay fixture');await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'});if(await summary.locator('..').getAttribute('open')===null)await summary.click();};await open();
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

// Source-only F047/F051 saved-overlay handoff and genuine refusal reopen controls.
import type {Locator as OverlaySixLocator, Request as OverlaySixRequest, Response as OverlaySixResponse} from '@playwright/test';
import {handoffApi as overlaySixApi, handoffNamespace as overlaySixNamespace, handoffWithin as overlaySixWithin} from './fixtures/remaining-project-handoff';
const OVERLAY_SIX_CELLS = [
 'F047.native-saved-overlay-truth.handoff', 'F047.native-saved-overlay-prediction.handoff', 'F051.native-saved-overlay-error.handoff',
 'F047.native-saved-overlay-refuse-missing-source-hash.reopen', 'F047.native-saved-overlay-refuse-unsupported-mapping.reopen', 'F047.native-saved-overlay-refuse-changed-source.reopen',
];
type OverlaySixScope = {tag:'A'|'B';root:string;source:string;original:string;originalSha:string;blue:number;project:any;fixture:any;valid:any;secondSet:string;changed:any;changedSource:string;changedBeforeSha:string;changedAfterSha:string};
const overlaySixRemaining=(deadline:number)=>Math.max(1,Math.floor(deadline-performance.now()));
const overlaySixFileHash=(file:string)=>sha(fs.readFileSync(file));
function overlaySixSave(e:Evidence,w:Workspace,label:string,value:unknown,extension='.json'){
 const file=path.join(w.logs,label+extension),bytes=Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value,null,2));
 const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);
 let failed=false,primary:unknown;try{expect(fs.writeSync(fd,bytes)).toBe(bytes.length);fs.fsyncSync(fd);}catch(error){failed=true;primary=error;}
 finally{try{fs.closeSync(fd);}catch(error){if(!failed){failed=true;primary=error;}}}if(failed)throw primary;
 e.addFile(file);return{path:file,size:bytes.length,sha256:sha(bytes)};
}
// The original fixture writes genuine EvaluationHistory records. This additional
// record captures bytes FIRST, then only its own source is changed during setup.
const OVERLAY_SIX_CHANGED_SETUP=String.raw`import hashlib,json,sys
from pathlib import Path
from PIL import Image
from backend.engine.evaluation_history import EvaluationHistory
root,project,source,original,report=(Path(v).resolve() for v in sys.argv[1:6])
for p in (project,source,original,report):
 if not p.is_relative_to(root) or p.is_symlink():raise ValueError('Owned overlay setup boundary')
if original.parent!=source or report.parent!=project/'reports'/'evaluations':raise ValueError('Owned report/source boundary')
changed=source/'changed.png'
with changed.open('xb') as writer:writer.write(original.read_bytes())
before=hashlib.sha256(changed.read_bytes()).hexdigest()
old=json.loads(report.read_text(encoding='utf-8'))
result=json.loads(json.dumps(old['result']));result['job_id']='controlled-display-changed-source'
row=result['test_predictions'][0];row.update(file_path=str(changed),file_name=changed.name,image_sha256=before)
record=EvaluationHistory(project/'reports'/'evaluations').append(result,json.loads(json.dumps(old['binding'])))
Image.new('RGB',(160,96),(1,2,int(sys.argv[6]))).save(changed)
after=hashlib.sha256(changed.read_bytes()).hexdigest()
if before==after:raise ValueError('Changed fixture must change actual source bytes')
report=project/'reports'/'evaluations'/(record['evaluation_id']+'.json')
print(json.dumps({'variant':'changed_source','labelset_id':record['binding']['labelset_id'],'record':record,'report_path':str(report),'report_sha256':hashlib.sha256(report.read_bytes()).hexdigest(),'source_path':str(changed),'saved_source_sha256':before,'actual_source_sha256':after}))`;
async function overlaySixMake(w:Workspace,api:OwnedApi,tag:'A'|'B'):Promise<OverlaySixScope>{
 const root=path.join(w.root,'saved-overlay-six-'+tag);fs.mkdirSync(root);const source=path.join(root,'source');fs.mkdirSync(source);
 const original=path.join(source,'part.png'),blue=tag==='A'?60:160;
 execFileSync(harness.resolvePython(),['-c',"import sys,numpy as np;from PIL import Image;y,x=np.indices((96,160));Image.fromarray(np.dstack([x,y,np.full_like(x,int(sys.argv[2]))]).astype(np.uint8)).save(sys.argv[1])",original,String(blue)],{timeout:30_000});
 const made=await api('/api/project/create',{name:'Saved overlay six handoff '+tag,task:'segmentation',project_dir:path.join(root,'project')});
 expect(made.project_dir).toBe(path.join(root,'project'));expect(fs.realpathSync(made.project_dir)).toBe(made.project_dir);expect(fs.lstatSync(made.project_dir).isSymbolicLink()).toBe(false);
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/project/labelsets',{name:'Saved overlay six second set '+tag});const sets=await api('/api/project/labelsets'),secondSet=sets.labelsets.find((v:any)=>v.id!=='default').id;
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/pixel_evaluation_reports.py'),root,made.project_dir,source,secondSet,'bound'],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const originalValid=fixture.items.find((v:any)=>v.labelset_id==='default'&&v.variant==='valid');expect(originalValid).toBeTruthy();
 const changed=JSON.parse(execFileSync(harness.resolvePython(),['-c',OVERLAY_SIX_CHANGED_SETUP,root,made.project_dir,source,original,originalValid.report_path,String(blue+1)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 fixture.items.push(changed);const valid=fixture.items.find((v:any)=>v.labelset_id===(tag==='A'?'default':secondSet)&&v.variant==='valid');
 expect(changed.record.result.test_predictions[0]).toMatchObject({file_path:changed.source_path,image_sha256:changed.saved_source_sha256});
 expect(changed.saved_source_sha256).toBe(overlaySixFileHash(original));expect(changed.actual_source_sha256).toBe(overlaySixFileHash(changed.source_path));expect(changed.saved_source_sha256).not.toBe(changed.actual_source_sha256);
 for(const entry of fixture.items){expect(entry.record.binding).toMatchObject({source_dataset_path:source,task:'segmentation',labelset_id:entry.labelset_id});expect(entry.record.result.fixture_kind).toBe('controlled_display_only_no_model_inference');expect(overlaySixFileHash(entry.report_path)).toBe(entry.report_sha256);}
 await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 await api('/api/team-data');await api('/api/team-data/readiness');await api('/api/annotations/part?file_path='+encodeURIComponent(original));await api('/api/annotations/changed?file_path='+encodeURIComponent(changed.source_path));
 await api('/api/evaluation/history?'+new URLSearchParams({source_dataset_path:source,task:'segmentation'}));
 const project=await api('/api/project/current');expect(project.id).toBe(made.id);expect(project.source_dataset_dir).toBe(source);
 return{tag,root,source,original,originalSha:overlaySixFileHash(original),blue,project,fixture,valid,secondSet,changed,changedSource:changed.source_path,changedBeforeSha:changed.saved_source_sha256,changedAfterSha:changed.actual_source_sha256};
}
function overlaySixCustody(scope:OverlaySixScope){
 const roots={source:scope.source,annotations:scope.project.annotations_dir,reports:scope.project.reports_dir,models:scope.project.models_dir,dataset:scope.project.dataset_dir,labelsets:path.join(scope.project.project_dir,'labelsets'),controlled_inputs:path.join(scope.root,'controlled-evaluation-inputs')};
 const files=[path.join(scope.project.project_dir,'project.json'),path.join(scope.project.project_dir,'labelsets.json')];
 return{project_id:scope.project.id,source:scope.source,source_sha256:scope.originalSha,changed_source_sha256:scope.changedAfterSha,
  roots:Object.fromEntries(Object.entries(roots).map(([kind,root])=>[kind,{root,snapshot:overlaySixNamespace(root)}])),
  files:Object.fromEntries(files.map(file=>[file,{size:fs.statSync(file).size,sha256:overlaySixFileHash(file)}]))};
}
async function overlaySixBody(response:OverlaySixResponse,deadline:number,e:Evidence,w:Workspace,label:string){
 const bytes=await overlaySixWithin(response.body(),deadline,'complete owning response');expect(bytes.length).toBeLessThanOrEqual(1024*1024);
 expect(await overlaySixWithin(response.finished(),deadline,'same response finished')).toBeNull();
 const request=response.request(),headers=request.headers(),context=JSON.parse(headers['x-vision-context']||'null');
 return{bytes,body:JSON.parse(bytes.toString('utf8')),proof:{method:request.method(),pathname:new URL(response.url()).pathname,status:response.status(),completed:true,actual_main_frame:true,project_header:headers['x-vision-project'],context,raw_response:overlaySixSave(e,w,label,bytes)}};
}
async function overlaySixProject(page:Page,scope:OverlaySixScope,origin:string,e:Evidence,w:Workspace,label:string){
 const deadline=performance.now()+10_000,remaining=()=>overlaySixRemaining(deadline),team=page.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});
 if(await overlaySixWithin(team.isVisible(),deadline,'team dialog state')){await overlaySixWithin(team.click(),deadline,'ordinary team close');await overlaySixWithin(expect(team).toHaveCount(0,{timeout:remaining()}),deadline,'team closed');}
 await overlaySixWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'project manager');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await overlaySixWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'ordinary recent projects');
 const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await overlaySixWithin(expect(item).toHaveCount(1,{timeout:remaining()}),deadline,'one owning project');await overlaySixWithin(expect(item).toBeEnabled({timeout:remaining()}),deadline,'owning project enabled');
 const wire=page.waitForResponse(r=>{const q=r.request(),u=new URL(r.url());return q.frame()===page.mainFrame()&&q.method()==='POST'&&u.origin===origin&&u.pathname==='/api/project/open'&&q.postDataJSON()?.project_dir===scope.project.project_dir;},{timeout:remaining()});void wire.catch(()=>undefined);
 await overlaySixWithin(item.click(),deadline,'ordinary owning project open');const response=await overlaySixWithin(wire,deadline,'owning project wire'),raw=await overlaySixBody(response,deadline,e,w,label+'-project-open');
 expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});expect(raw.body).toEqual(scope.project);
 await overlaySixWithin(expect(dialog).toHaveCount(0,{timeout:remaining()}),deadline,'project dialog closed');await overlaySixWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:remaining()}),deadline,'owning header');
 return{project_id:scope.project.id,project_dir:scope.project.project_dir,request_body:response.request().postDataJSON(),...raw.proof};
}
async function overlaySixChoose(page:Page,scope:OverlaySixScope,item:any,origin:string,e:Evidence,w:Workspace,label:string){
 const deadline=performance.now()+10_000,remaining=()=>overlaySixRemaining(deadline);
 await overlaySixWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:remaining()}),deadline,'owning project header');
 await overlaySixWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click(),deadline,'ordinary evaluation stage');
 const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'}),history=summary.locator('..');await overlaySixWithin(expect(summary).toHaveCount(1,{timeout:remaining()}),deadline,'one history scope');
 if(await overlaySixWithin(history.getAttribute('open'),deadline,'history state')===null)await overlaySixWithin(summary.click(),deadline,'history disclosure');
 await overlaySixWithin(history.getByLabel('평가 이력 모델 종류',{exact:true}).selectOption('segmentation'),deadline,'owning task');await overlaySixWithin(history.getByLabel('평가 라벨 세트',{exact:true}).selectOption(item.labelset_id),deadline,'owning labelset');
 const refresh=history.getByRole('button',{name:'평가 이력 새로고침',exact:true});await overlaySixWithin(expect(refresh).toBeEnabled({timeout:remaining()}),deadline,'owning history ready');
 const wire=page.waitForResponse(r=>{const q=r.request(),u=new URL(r.url());return q.frame()===page.mainFrame()&&q.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='segmentation'&&u.searchParams.get('labelset_id')===item.labelset_id;},{timeout:remaining()});void wire.catch(()=>undefined);
 await overlaySixWithin(refresh.click(),deadline,'ordinary owning history refresh');const response=await overlaySixWithin(wire,deadline,'actual history response'),raw=await overlaySixBody(response,deadline,e,w,label+'-history');expect(response.status()).toBe(200);
 expect(raw.proof.project_header).toBe(scope.project.id);expect(raw.proof.context.project_id).toBe(scope.project.id);const expected=scope.fixture.items.filter((v:any)=>v.labelset_id===item.labelset_id).map((v:any)=>v.record.evaluation_id).sort();
 expect(raw.body.total).toBe(expected.length);expect(raw.body.items.map((v:any)=>v.evaluation_id).sort()).toEqual(expected);expect(raw.body.items.find((v:any)=>v.evaluation_id===item.record.evaluation_id)).toEqual(item.record);
 const selector=history.getByLabel(/^모델별 저장 평가/);await overlaySixWithin(expect.poll(()=>selector.locator('option').evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value).filter(Boolean).sort()),{timeout:remaining()}).toEqual(expected),deadline,'exact owning catalog');
 await overlaySixWithin(selector.selectOption(item.record.evaluation_id),deadline,'saved report selection');await overlaySixWithin(expect(selector).toHaveValue(item.record.evaluation_id,{timeout:remaining()}),deadline,'saved report identity');
 const details=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await overlaySixWithin(details.getAttribute('open'),deadline,'evidence state')===null)await overlaySixWithin(details.locator('summary').first().click(),deadline,'ordinary evidence disclosure');
 await overlaySixWithin(expect(details.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:remaining()}),deadline,'fresh report class scope');
 await overlaySixWithin(expect(details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeEnabled({timeout:remaining()}),deadline,'saved masks ready');
 return{details,history_proof:raw.proof,evaluation_id:item.record.evaluation_id,labelset_id:item.labelset_id};
}
function overlaySixPixels(blue:number,layer:'original'|'truth'|'prediction'|'error',flip:boolean,className:'all'|'Scratch'|'Crack',projected=true){
 const result:number[]=[],width=projected?160:64,height=projected?96:64,classId=className==='all'?null:className==='Scratch'?7:23;
 for(let y=0;y<height;y++)for(let x=0;x<width;x++){
  if(layer==='original'){result.push(x,y,blue,255);continue;}
  const mx=projected?Math.floor(x*64/160):x,my=projected?Math.floor(y*64/96):y,ty=flip?63-my:my,px=flip?63-mx:mx;
  const truth=mx>=8&&mx<24&&ty>=8&&ty<24?7:mx>=32&&mx<48&&ty>=16&&ty<40?23:0,pred=px>=12&&px<28&&my>=8&&my<24?7:px>=32&&px<48&&my>=20&&my<44?23:0;
  const a=classId===null?truth>0:truth===classId,b=classId===null?pred>0:pred===classId;
  const rgb=layer==='truth'?(a?[74,222,128]:[35,35,35]):layer==='prediction'?(b?[34,211,238]:[35,35,35]):[a&&truth!==pred?244:35,b&&truth!==pred?180:35,b&&truth!==pred?255:35];
  result.push(...(projected&&rgb.every(v=>v===35)?[0,0,0,0]:[...rgb,255]));
 }return result;
}
async function overlaySixRaster(node:OverlaySixLocator,expected:number[],size:number[],deadline:number,e:Evidence,w:Workspace,label:string){
 const raw=await overlaySixWithin(node.evaluate(async element=>{const image=element as HTMLImageElement;await image.decode();const canvas=document.createElement('canvas');canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const ctx=canvas.getContext('2d')!;ctx.drawImage(image,0,0);return{size:[canvas.width,canvas.height],pixels:Array.from(ctx.getImageData(0,0,canvas.width,canvas.height).data),src:image.src};}),deadline,'full decoded owning raster');
 expect(raw.size).toEqual(size);expect(raw.pixels).toEqual(expected);expect(raw.src).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/);
 const rgba=overlaySixSave(e,w,label+'-actual-RGBA',{size:raw.size,pixels:raw.pixels}),png=overlaySixSave(e,w,label+'-actual-raster',Buffer.from(raw.src.split(',')[1],'base64'),'.png');return{size:raw.size,rgba_sha256:sha(Buffer.from(raw.pixels)),rgba,png};
}
async function overlaySixPreview(page:Page,scope:OverlaySixScope,item:any,details:OverlaySixLocator,origin:string,e:Evidence,w:Workspace,label:string){
 const deadline=performance.now()+10_000,remaining=()=>overlaySixRemaining(deadline),sample=item.record.result.test_predictions[0],opener=details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true});
 await overlaySixWithin(expect(opener).toBeEnabled({timeout:remaining()}),deadline,'owning preview ready');
 const wire=page.waitForResponse(r=>{const q=r.request(),u=new URL(r.url());return q.frame()===page.mainFrame()&&q.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history/'+item.record.evaluation_id+'/evidence-image'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='segmentation'&&u.searchParams.get('image_path')===sample.file_path;},{timeout:remaining()});void wire.catch(()=>undefined);
 await overlaySixWithin(opener.click(),deadline,'ordinary saved preview click');const response=await overlaySixWithin(wire,deadline,'actual saved preview'),raw=await overlaySixBody(response,deadline,e,w,label+'-preview');
 expect(raw.proof.project_header).toBe(scope.project.id);expect(raw.proof.context.project_id).toBe(scope.project.id);
 return{response,raw,deadline,opener,sample,proof:{project_id:scope.project.id,evaluation_id:item.record.evaluation_id,labelset_id:item.labelset_id,source_path:sample.file_path,saved_source_sha256:sample.image_sha256??null,actual_source_sha256:overlaySixFileHash(sample.file_path),...raw.proof}};
}
async function overlaySixValid(page:Page,scope:OverlaySixScope,origin:string,e:Evidence,w:Workspace,label:string){
 const chosen=await overlaySixChoose(page,scope,scope.valid,origin,e,w,label),className=scope.tag==='A'?'Scratch':'Crack';
 const classDeadline=performance.now()+10_000;await overlaySixWithin(chosen.details.getByLabel('평가 증거 클래스',{exact:true}).selectOption(className),classDeadline,'ordinary owning class choice');
 const opened=await overlaySixPreview(page,scope,scope.valid,chosen.details,origin,e,w,label),{deadline}=opened,remaining=()=>overlaySixRemaining(deadline),viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
 expect(opened.response.status()).toBe(200);expect(opened.raw.body).toMatchObject({evaluation_id:scope.valid.record.evaluation_id,image_path:scope.original,image_sha256:scope.originalSha,original_size:[160,96],read_only:true});
 await overlaySixWithin(expect(viewer).toBeVisible({timeout:remaining()}),deadline,'owning viewer visible');await overlaySixWithin(expect(viewer).toContainText(scope.valid.record.evaluation_id,{timeout:remaining()}),deadline,'owning report identity');await overlaySixWithin(expect(viewer).toContainText(scope.originalSha,{timeout:remaining()}),deadline,'owning image hash');
 await overlaySixWithin(expect(viewer.getByLabel('근거 이미지 종류',{exact:true})).toHaveValue('original',{timeout:remaining()}),deadline,'owning original');
 const rasters:Record<string,{size:number[];rgba_sha256:string;rgba:{path:string;size:number;sha256:string};png:{path:string;size:number;sha256:string}}>={};rasters.original=await overlaySixRaster(viewer.getByRole('img',{name:'평가 입력 원본',exact:true}),overlaySixPixels(scope.blue,'original',false,className),[160,96],deadline,e,w,label+'-original');
 for(const [layer,name] of [['truth','겹침 정답 마스크'],['prediction','겹침 예측 마스크'],['error','겹침 미검·과검 마스크']] as const){
  await overlaySixWithin(viewer.getByLabel('근거 겹침 이미지',{exact:true}).selectOption(layer),deadline,'ordinary '+layer+' choice');await overlaySixWithin(expect(viewer.getByLabel('근거 겹침 투명도',{exact:true})).toBeEnabled({timeout:remaining()}),deadline,'owning '+layer+' decoded');
  rasters[layer]=await overlaySixRaster(viewer.getByRole('img',{name,exact:true}),overlaySixPixels(scope.blue,layer,scope.valid.labelset_id!== 'default',className),[160,96],deadline,e,w,label+'-'+layer);
 }
 await evidenceScreenshot(e,page,label+'-owning-three-masks');await overlaySixWithin(viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click(),deadline,'ordinary viewer close');await overlaySixWithin(expect(viewer).toHaveCount(0,{timeout:remaining()}),deadline,'viewer closed');await overlaySixWithin(expect(opened.opener).toBeFocused({timeout:remaining()}),deadline,'owning opener focus');
 return{visit:label,className,history:chosen.history_proof,preview:opened.proof,rasters};
}
async function evidenceScreenshot(e:Evidence,page:Page,label:string){await e.screenshot(page,label);}
async function overlaySixRefuse(page:Page,scope:OverlaySixScope,variant:'missing_source_hash'|'unsupported_mapping'|'changed_source',origin:string,e:Evidence,w:Workspace,label:string){
 const item=variant==='changed_source'?scope.changed:scope.fixture.items.find((v:any)=>v.variant===variant),chosen=await overlaySixChoose(page,scope,item,origin,e,w,label);
 const opened=await overlaySixPreview(page,scope,item,chosen.details,origin,e,w,label),{deadline}=opened,remaining=()=>overlaySixRemaining(deadline),viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
 const message=variant==='missing_source_hash'?'This saved evaluation has no captured source image hash':variant==='changed_source'?'Source image changed since this execution; saved prediction remains read-only.':'저장된 원본 좌표 변환이 없어 마스크를 겹칠 수 없습니다.';
 if(variant==='unsupported_mapping'){expect(opened.response.status()).toBe(200);expect(opened.raw.body).toMatchObject({evaluation_id:item.record.evaluation_id,image_path:scope.original,image_sha256:scope.originalSha,read_only:true});expect(item.record.result.test_predictions[0].pixel_evidence.mapping.kind).toBe('unspecified_crop');}
 else{expect(opened.response.status()).toBe(409);expect(opened.raw.body).toEqual({detail:message});}
 const alert=chosen.details.getByRole('alert');await overlaySixWithin(expect(alert).toHaveCount(1,{timeout:remaining()}),deadline,'one genuine refusal alert');await overlaySixWithin(expect(alert).toHaveText(message,{timeout:remaining()}),deadline,'exact genuine producer refusal');await overlaySixWithin(expect(viewer).toHaveCount(0,{timeout:remaining()}),deadline,'no unverified source viewer');
 await overlaySixWithin(expect(chosen.details.getByLabel('평가 마스크 보기',{exact:true})).toHaveValue('error',{timeout:remaining()}),deadline,'standalone saved error mask retained');
 const raw=await overlaySixWithin(chosen.details.locator('canvas').evaluate(element=>{const canvas=element as HTMLCanvasElement;const ctx=canvas.getContext('2d')!;return{size:[canvas.width,canvas.height],pixels:Array.from(ctx.getImageData(0,0,canvas.width,canvas.height).data)};}),deadline,'full standalone saved raster');
 expect(raw.size).toEqual([64,64]);expect(raw.pixels).toEqual(overlaySixPixels(scope.blue,'error',item.labelset_id!=='default','all',false));overlaySixSave(e,w,label+'-standalone-mask-RGBA',raw);
 await evidenceScreenshot(e,page,label+'-'+variant+'-genuine-refusal');
 return{visit:label,variant,project_id:scope.project.id,evaluation_id:item.record.evaluation_id,history:chosen.history_proof,preview:opened.proof,message,source_viewer_absent:true,standalone_mask:{size:raw.size,rgba_sha256:sha(Buffer.from(raw.pixels))},report_sha256:overlaySixFileHash(item.report_path)};
}
async function overlaySixExercise(page:Page,w:Workspace,e:Evidence,api:OwnedApi,origin:string,native:boolean,url?:string){
 const a=await overlaySixMake(w,api,'A'),b=await overlaySixMake(w,api,'B');expect(a.project.id).not.toBe(b.project.id);expect(a.originalSha).not.toBe(b.originalSha);expect(a.valid.record.evaluation_id).not.toBe(b.valid.record.evaluation_id);expect(path.basename(a.original)).toBe(path.basename(b.original));
 const setupDeadline=performance.now()+10_000;if(url)await overlaySixWithin(page.goto(url),setupDeadline,'owning browser renderer');else await overlaySixWithin(page.reload(),setupDeadline,'owning source Electron renderer');
 await overlaySixChoose(page,b,b.valid,origin,e,w,'setup-B');await overlaySixProject(page,a,origin,e,w,'setup-A');await overlaySixChoose(page,a,a.valid,origin,e,w,'setup-A');
 const before={A:overlaySixCustody(a),B:overlaySixCustody(b)};overlaySixSave(e,w,'overlay-six-before',before);
 const writes:{method:string;path:string;body:unknown;main_frame:boolean}[]=[],transitions:unknown[]=[],visits:any[]=[],refusals:any[]=[],cleanupFailures:{role:string;error:string}[]=[];
 const observe=(q:OverlaySixRequest)=>{const u=new URL(q.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&q.method()!=='GET')writes.push({method:q.method(),path:u.pathname,body:q.postDataJSON(),main_frame:q.frame()===page.mainFrame()});};
 let failed=false,primary:unknown,after:any;const remember=(role:string,error:unknown)=>{cleanupFailures.push({role,error:error instanceof Error?error.message:String(error)});if(!failed){failed=true;primary=error;}};
 try{
  page.on('request',observe);
  for(const [scope,label] of [[a,'A-before'],[b,'B'],[a,'A-return']] as const){
   if(label!=='A-before')transitions.push(await overlaySixProject(page,scope,origin,e,w,'overlay-six-'+label));
   const view=await overlaySixValid(page,scope,origin,e,w,'overlay-six-'+label);visits.push(view);
   for(const variant of ['missing_source_hash','unsupported_mapping','changed_source'] as const)refusals.push(await overlaySixRefuse(page,scope,variant,origin,e,w,'overlay-six-'+label+'-'+variant));
  }
  expect(visits).toHaveLength(3);const identities=(rasters:any)=>Object.fromEntries(Object.entries(rasters).map(([layer,row])=>{const r=row as {size:number[];rgba_sha256:string};return[layer,{size:r.size,rgba_sha256:r.rgba_sha256}];}));expect(identities(visits[2].rasters)).toEqual(identities(visits[0].rasters));expect(visits[2].preview.evaluation_id).toBe(visits[0].preview.evaluation_id);
  for(const layer of ['original','truth','prediction','error'])expect(visits[1].rasters[layer].rgba_sha256).not.toBe(visits[0].rasters[layer].rgba_sha256);
  for(const variant of ['missing_source_hash','unsupported_mapping','changed_source']){
   const first=refusals.find(v=>v.visit==='overlay-six-A-before-'+variant),returned=refusals.find(v=>v.visit==='overlay-six-A-return-'+variant),middle=refusals.find(v=>v.visit==='overlay-six-B-'+variant);
   expect(returned.evaluation_id).toBe(first.evaluation_id);expect(returned.project_id).toBe(first.project_id);expect(returned.message).toBe(first.message);expect(returned.report_sha256).toBe(first.report_sha256);expect(returned.standalone_mask).toEqual(first.standalone_mask);
   expect(middle.project_id).not.toBe(first.project_id);expect(middle.evaluation_id).not.toBe(first.evaluation_id);expect(returned.preview.actual_source_sha256).toBe(first.preview.actual_source_sha256);
  }
  expect(writes).toEqual([{method:'POST',path:'/api/project/open',body:{project_dir:b.project.project_dir},main_frame:true},{method:'POST',path:'/api/project/open',body:{project_dir:a.project.project_dir},main_frame:true}]);expect(await api('/api/project/current')).toEqual(a.project);
 }catch(error){failed=true;primary=error;}
 finally{
  try{page.off('request',observe);}catch(error){remember('remove-own-request-observer',error);}
  try{if(!page.isClosed()){const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});if(await viewer.count()){const deadline=performance.now()+10_000;await overlaySixWithin(viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click(),deadline,'close owned viewer in teardown');}}}catch(error){remember('ordinary-owned-viewer-close',error);}
  after={};for(const scope of [a,b]){
   try{after[scope.tag]=overlaySixCustody(scope);expect(after[scope.tag]).toEqual(before[scope.tag]);}catch(error){remember('full-owning-custody-'+scope.tag,error);}
   for(const root of Object.values(before[scope.tag].roots))for(const member of Object.keys(root.snapshot.files)){try{e.addFile(path.join(root.root,member));}catch(error){remember('retain-owning-file-'+scope.tag+'-'+member,error);}}
   for(const file of Object.keys(before[scope.tag].files)){try{e.addFile(file);}catch(error){remember('retain-owning-top-file-'+scope.tag,error);}}
  }
  const readback={scenario_cells:OVERLAY_SIX_CELLS,before,after,writes,transitions,visits,refusals,cleanup_failures:cleanupFailures,source_electron:native,
   actual_A_B_A_project_navigation:transitions.length===2,genuine_backend_409_missing_hash_and_changed_source:refusals.filter(v=>v.variant!=='unsupported_mapping'&&v.preview.status===409).length===6,genuine_UI_mapping_refusal_after_backend200:refusals.filter(v=>v.variant==='unsupported_mapping'&&v.preview.status===200).length===3,
   controlled_saved_reports_not_model_inference:true,changed_source_fixture_prepared_before_baseline:true,full_raster_formula_compared:visits.length===3,
   owning_source_annotation_reports_models_dataset_labelsets_inputs_unchanged:!failed,U011_display_state_cells_claimed:false,
   installed_native_acceptance:false,frozen_native_acceptance:false,device_acceptance:false,human_labels_applied:false,model_quality_acceptance:false,parent_acceptance:false};
  try{overlaySixSave(e,w,'overlay-six-after-and-readback',readback);}catch(error){remember('durable-readback',error);}
  try{e.note('saved_overlay_six_project_handoff',readback);}catch(error){remember('harness-note',error);}
 }
 if(failed)throw primary;
}
test('saved truth prediction and error overlays hand off owning reports and reopen genuine provenance refusals through A B A',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api=overlaySixApi(page,renderer.origin,false);await overlaySixExercise(page,workspace,evidence,api,renderer.origin,false,renderer.url);
});
test('source Electron saved truth prediction and error overlays hand off owning reports and reopen genuine provenance refusals through A B A',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`,api=overlaySixApi(page,origin,true);await overlaySixExercise(page,workspace,evidence,api,origin,true);
});

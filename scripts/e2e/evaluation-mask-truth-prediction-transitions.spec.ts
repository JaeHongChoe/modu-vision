// SOURCE ONLY: Root alone imports, integrates, executes and qualifies this spec.
// Four pending F047 truth/prediction error/cancel cells; Root binds actual evidence.
// Controlled saved reports are display fixtures, never actual model inference.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Locator, Request, Response, Route} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness = require('./fixtures/harness.cjs');
type OwnedApi = (route:string, body?:unknown, method?:string)=>Promise<any>;
type Variant = 'missing_source_hash'|'unsupported_mapping'|'changed_source';
type Mask = 'truth'|'prediction'|'error';
type Overlay = 'truth'|'prediction';
type Scope = {tag:'A'|'B';root:string;source:string;original:string;blue:number;project:any;fixture:any;valid:any};
const VARIANTS:Variant[] = ['missing_source_hash','unsupported_mapping','changed_source'];
const LAYERS:Overlay[] = ['truth','prediction'];
const ACTIONS:Record<Overlay,string> = {
  truth:'F047.native-saved-overlay-truth',
  prediction:'F047.native-saved-overlay-prediction',
};
const CELLS = LAYERS.flatMap(layer=>['error','cancel'].map(dimension=>ACTIONS[layer]+'.'+dimension));
const sha = (value:Buffer|string)=>crypto.createHash('sha256').update(value).digest('hex');
const remaining = (deadline:number)=>{
  const value=deadline-performance.now();
  if(value<=0)throw Error('Original 10s source-refusal frame expired');
  return Math.max(1,Math.floor(value));
};
async function within<T>(work:Promise<T>,deadline:number,label:string):Promise<T>{
  let timer:ReturnType<typeof setTimeout>|undefined;
  try{return await Promise.race([work,new Promise<never>((_,reject)=>{
    timer=setTimeout(()=>reject(Error('Original 10s source-refusal frame expired: '+label)),remaining(deadline));
  })]);}finally{if(timer)clearTimeout(timer);}
}
const frame = <T>(label:string,work:(deadline:number)=>Promise<T>)=>{
  const deadline=performance.now()+10_000;return within(work(deadline),deadline,label);
};
function save(e:Evidence,w:Workspace,label:string,value:unknown){
  const bytes=Buffer.isBuffer(value)?value:Buffer.from(JSON.stringify(value,null,2));
  const file=path.join(w.logs,label+'.json');
  const fd=fs.openSync(file,fs.constants.O_WRONLY|fs.constants.O_CREAT|fs.constants.O_EXCL|fs.constants.O_NOFOLLOW,0o600);
  try{expect(fs.writeSync(fd,bytes)).toBe(bytes.length);fs.fsyncSync(fd);}finally{fs.closeSync(fd);}
  e.addFile(file);return{path:file,size:bytes.length,sha256:sha(bytes)};
}
function ownedApi(page:Page,origin:string,native:boolean):OwnedApi{
  return (route,body,method)=>frame('normal authenticated fixture API',async deadline=>{
    if(native)return within(page.evaluate(async({origin,route,body,method,timeout})=>{
      const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),timeout);
      try{
        const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),
          headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body),signal:abort.signal});
        const raw=await response.text();if(!response.ok)throw Error('Owned source fixture API status '+response.status);
        return JSON.parse(raw);
      }finally{clearTimeout(timer);}
    },{origin,route,body,method,timeout:remaining(deadline)}),deadline,'authenticated native fixture API');
    const response=await within(page.request.fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),
      ...(body===undefined?{}:{data:body}),timeout:remaining(deadline)}),deadline,'authenticated browser fixture API');
    expect(response.ok()).toBe(true);return within(response.json(),deadline,'fixture API full body');
  });
}
// Copied and narrowed from the frozen pixel report producer: genuine append,
// explicit display inputs only. Source changes happen before the read-only baseline.
const SEED = String.raw`import base64,hashlib,io,json,sys
from pathlib import Path
import numpy as np
from PIL import Image
from backend.engine.evaluation_history import EvaluationHistory
root,project,source=(Path(value).resolve() for value in sys.argv[1:4])
tag=sys.argv[4]
if tag not in ('A','B') or not project.is_relative_to(root) or not source.is_relative_to(root):raise ValueError('Owned report boundary')
for directory in (root,project,source):
 if directory.is_symlink():raise ValueError('No linked fixture paths')
blue=60 if tag=='A' else 160
original=source/'part.png'
y,x=np.indices((96,160))
with original.open('xb') as writer:Image.fromarray(np.dstack([x,y,np.full_like(x,blue)]).astype(np.uint8)).save(writer,format='PNG')
inputs=root/'controlled-evaluation-inputs';inputs.mkdir()
def digest(file):return hashlib.sha256(file.read_bytes()).hexdigest()
def raster(mask,name):
 file=inputs/name;output=io.BytesIO();Image.fromarray(mask).save(output,format='PNG')
 with file.open('xb') as writer:writer.write(output.getvalue())
 return 'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode()
truth=np.zeros((64,64),dtype=np.uint8);predicted=truth.copy()
truth[8:24,8:24]=7;truth[16:40,32:48]=23
predicted[8:24,12:28]=7;predicted[20:44,32:48]=23
if tag=='B':truth=np.flipud(truth).copy();predicted=np.fliplr(predicted).copy()
maps={'truth_mask':raster(truth,'truth.png'),'prediction_mask':raster(predicted,'prediction.png')}
counts={}
for name,class_id in (('Scratch',7),('Crack',23)):
 actual,guess=truth==class_id,predicted==class_id
 counts[name]={'class_id':class_id,'tp':int((actual&guess).sum()),'fp':int((~actual&guess).sum()),'fn':int((actual&~guess).sum()),'truth_area_px':int(actual.sum()),'predicted_area_px':int(guess.sum())}
sample={'file_path':str(original),'file_name':original.name,'image_sha256':digest(original),'pixel_evidence':{**maps,'per_class':counts,'shape':[64,64],'coordinate_space':'model_input_px','mapping':{'kind':'full_image_resize','source_size':[160,96]}}}
history=EvaluationHistory(project/'reports'/'evaluations');items=[]
for variant in ('valid','missing_source_hash','unsupported_mapping','changed_source'):
 row=json.loads(json.dumps(sample))
 if variant=='missing_source_hash':row.pop('image_sha256')
 elif variant=='unsupported_mapping':row['pixel_evidence']['mapping']['kind']='unspecified_crop'
 elif variant=='changed_source':
  changed=source/'changed.png'
  with changed.open('xb') as writer:writer.write(original.read_bytes())
  row.update(file_path=str(changed),file_name=changed.name,image_sha256=digest(changed))
 record=history.append({'job_id':'controlled-display-'+tag+'-'+variant,'task':'segmentation','fixture_kind':'controlled_display_only_no_model_inference','test_predictions':[row]},
  {'source_dataset_path':str(source),'task':'segmentation','labelset_id':'default','fixture_kind':'controlled_display_only_no_model_inference'})
 report=history.directory/(record['evaluation_id']+'.json')
 if variant=='changed_source':Image.new('RGB',(160,96),(1,2,blue+1)).save(changed)
 actual=digest(Path(row['file_path']))
 if variant=='changed_source' and actual==row['image_sha256']:raise ValueError('Source fixture did not change')
 items.append({'variant':variant,'labelset_id':'default','record':record,'report_path':str(report),'report_sha256':digest(report),'source_path':row['file_path'],'saved_source_sha256':row.get('image_sha256'),'actual_source_sha256':actual})
print(json.dumps({'kind':'controlled_saved_report_display_fixture','items':items,'inputs':[{'path':str(file),'sha256':digest(file)} for file in sorted(inputs.iterdir())]}))`;
async function make(w:Workspace,api:OwnedApi,tag:'A'|'B'):Promise<Scope>{
  const root=path.join(w.root,'f047-source-transitions-'+tag),source=path.join(root,'source');
  fs.mkdirSync(root);fs.mkdirSync(source);
  const made=await api('/api/project/create',{name:'Saved source refusal transitions '+tag,task:'segmentation',project_dir:path.join(root,'project')});
  expect(made.project_dir).toBe(path.join(root,'project'));
  await api('/api/project/update',{source_dataset_dir:source},'PUT');
  const fixture=JSON.parse(execFileSync(harness.resolvePython(),['-c',SEED,root,made.project_dir,source,tag],
    {cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
  expect(fixture.kind).toBe('controlled_saved_report_display_fixture');expect(fixture.items).toHaveLength(4);
  await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
  for(const item of fixture.items){
    expect(item.record.binding).toMatchObject({source_dataset_path:source,task:'segmentation',labelset_id:'default'});
    expect(item.record.result.fixture_kind).toBe('controlled_display_only_no_model_inference');
    expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);
    expect(sha(fs.readFileSync(item.source_path))).toBe(item.actual_source_sha256);
  }
  for(const input of fixture.inputs)expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);
  const changed=fixture.items.find((item:any)=>item.variant==='changed_source');
  expect(changed.saved_source_sha256).not.toBe(changed.actual_source_sha256);
  for(const route of ['/api/team-data','/api/team-data/readiness','/api/team-data/queue?offset=0&limit=30',
    '/api/product-delivery/packages','/api/fleet/targets','/api/runtime-services','/api/runtime-services/capture-groups',
    '/api/flow-evaluations/approvals/active','/api/model-deployments/active?source_dataset_path='+encodeURIComponent(source)+'&task=segmentation',
    '/api/dataset/versions','/api/provenance/impact',
    '/api/annotations/part?file_path='+encodeURIComponent(path.join(source,'part.png')),
    '/api/annotations/changed?file_path='+encodeURIComponent(path.join(source,'changed.png'))])await api(route);
  const project=await api('/api/project/current');expect(project.id).toBe(made.id);expect(project.source_dataset_dir).toBe(source);
  return{tag,root,source,original:path.join(source,'part.png'),blue:tag==='A'?60:160,project,fixture,
    valid:fixture.items.find((item:any)=>item.variant==='valid')};
}
// Full raw project/source/input trees, no symlinks and no tolerated writes.
function tree(root:string,retainedFDs?:number[]){
  const result:Record<string,unknown>={};
  const walk=(dir:string)=>{
    const info=fs.lstatSync(dir,{bigint:true});expect(info.isDirectory()).toBe(true);expect(info.isSymbolicLink()).toBe(false);
    expect(fs.realpathSync(dir)).toBe(dir);result[path.relative(root,dir).split(path.sep).join('/')||'.']={kind:'directory'};
    for(const name of fs.readdirSync(dir).sort()){
      const file=path.join(dir,name),before=fs.lstatSync(file,{bigint:true});expect(before.isSymbolicLink()).toBe(false);
      if(before.isDirectory()){walk(file);continue;}
      expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1n);
      const raw7=(s:fs.BigIntStats)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
      const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW),held=!!retainedFDs&&/\.sqlite3?(?:-(?:wal|shm))?$/.test(file);
      if(held)retainedFDs!.push(fd);
      let bytes:Buffer;
      try{const opened=fs.fstatSync(fd,{bigint:true});expect(raw7(opened)).toEqual(raw7(before));bytes=fs.readFileSync(fd);
        expect(raw7(fs.fstatSync(fd,{bigint:true}))).toEqual(raw7(opened));expect(raw7(fs.lstatSync(file,{bigint:true}))).toEqual(raw7(opened));
      }finally{if(!held)fs.closeSync(fd);}
      result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:bytes!.length,sha256:sha(bytes!),raw7:raw7(before)};
    }
  };walk(root);return result;
}
function custody(scope:Scope,retainedFDs?:number[]){
  return{project_id:scope.project.id,source:tree(scope.source),project:tree(scope.project.project_dir,retainedFDs),
    controlled_inputs:tree(path.join(scope.root,'controlled-evaluation-inputs'))};
}
// Only the two known owned project SHM files are coordination observers.
// Full raw7 stability during each tree() file read remains mandatory above.
// DB/WAL bytes and all seven identity fields remain strict before/after.
function compareOwnedCustody(scope:Scope,before:any,after:any){
  expect(['A','B']).toContain(scope.tag);
  expect(path.basename(scope.root)).toBe('f047-source-transitions-'+scope.tag);
  expect(scope.project.project_dir).toBe(path.join(scope.root,'project'));
  expect(before.project_id).toBe(scope.project.id);expect(after.project_id).toBe(scope.project.id);
  const regular=(row:any)=>{
    expect(row).toBeTruthy();expect(row.kind).toBe('file');expect(row.raw7).toHaveLength(7);
    expect(row.raw7.every((value:unknown)=>typeof value==='string'&&/^\d+$/.test(value as string))).toBe(true);
    expect(BigInt(row.raw7[2])&0o170000n).toBe(0o100000n);expect(row.raw7[3]).toBe('1');
    expect(Number.isSafeInteger(row.size)&&row.size>=0).toBe(true);expect(row.raw7[4]).toBe(String(row.size));
    expect(row.sha256).toMatch(/^[a-f0-9]{64}$/);
  };
  const database='model_deployments.sqlite3',wal='model_deployments.sqlite3-wal',shm='model_deployments.sqlite3-shm';
  // Presence and regular-file custody are required for DB and WAL too; no
  // absent file or other SQLite coordination file receives an allowance.
  for(const member of [database,wal,shm]){regular(before.project[member]);regular(after.project[member]);}
  for(const member of [database,wal])expect(after.project[member]).toEqual(before.project[member]);
  const first=before.project[shm],last=after.project[shm];
  expect(last.size).toBe(first.size);expect(last.sha256).toBe(first.sha256);
  expect(last.raw7.slice(0,5)).toEqual(first.raw7.slice(0,5));
  // Clone only for comparison. Preserve unmodified full before/after snapshots
  // and allow exactly mtime_ns/ctime_ns of this known owned SHM member.
  const comparedBefore=JSON.parse(JSON.stringify(before)),comparedAfter=JSON.parse(JSON.stringify(after));
  comparedBefore.project[shm].raw7=first.raw7.slice(0,5);comparedAfter.project[shm].raw7=last.raw7.slice(0,5);
  expect(comparedAfter).toEqual(comparedBefore);
  return{kind:'allowed_timestamp_only_sqlite_shm_coordination',project_tag:scope.tag,project_id:scope.project.id,
    path:path.join(scope.project.project_dir,shm),relative_member:shm,
    allowed_fields:['raw7[5].mtime_ns','raw7[6].ctime_ns'],before_raw7:[...first.raw7],after_raw7:[...last.raw7],
    same_sha256:true,before_sha256:first.sha256,after_sha256:last.sha256,
    same_size:true,before_size:first.size,after_size:last.size,same_dev_inode_mode_nlink_size:true,
    timestamp_values_changed:first.raw7[5]!==last.raw7[5]||first.raw7[6]!==last.raw7[6],
    database_and_wal_full_raw7_and_bytes_equal:true,all_other_whole_custody_fields_equal:true,
    full_raw7_stability_during_each_file_read_required:true,classification:'owned fixture observer coordination; no database content change allowance'};
}
async function body(response:Response,deadline:number,e:Evidence,w:Workspace,label:string){
  const raw=await within(response.body(),deadline,'complete original UI body');expect(raw.length).toBeLessThanOrEqual(1024*1024);
  expect(await within(response.finished(),deadline,'original UI response completion')).toBeNull();
  const request=response.request(),headers=request.headers();
  return{value:JSON.parse(raw.toString('utf8')),proof:{status:response.status(),method:request.method(),uri:request.url(),
    main_frame:true,project_header:headers['x-vision-project'],context:JSON.parse(headers['x-vision-context']||'null'),
    completed:true,raw:save(e,w,label,raw)}};
}
async function project(page:Page,scope:Scope,origin:string,e:Evidence,w:Workspace,label:string,deadline=performance.now()+10_000){
  const team=page.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true});
  if(await within(team.isVisible(),deadline,'team state'))await within(team.click(),deadline,'ordinary team close');
  await within(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'ordinary project manager');
  const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
  await within(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'ordinary recent projects');
  const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
  await expect(item).toHaveCount(1,{timeout:remaining(deadline)});await expect(item).toBeEnabled({timeout:remaining(deadline)});
  const wire=page.waitForResponse(r=>{const q=r.request(),u=new URL(r.url());return q.frame()===page.mainFrame()&&q.method()==='POST'&&u.origin===origin&&u.pathname==='/api/project/open'&&q.postDataJSON()?.project_dir===scope.project.project_dir;},{timeout:remaining(deadline)});
  void wire.catch(()=>undefined);await within(item.click(),deadline,'ordinary project open');
  const response=await within(wire,deadline,'owning project response'),raw=await body(response,deadline,e,w,label+'-project-open');
  expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});expect(raw.value).toEqual(scope.project);
  await expect(dialog).toHaveCount(0,{timeout:remaining(deadline)});await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:remaining(deadline)});
  return raw.proof;
}
function historyLocators(page:Page){
  const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'}),history=summary.locator('..');
  return{summary,history,selector:history.getByLabel(/^모델별 저장 평가/),
    details:history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first()};
}
async function saved(page:Page,item:any,deadline:number){
  const {history,selector}=historyLocators(page);await expect(selector).toHaveValue(item.record.evaluation_id,{timeout:remaining(deadline)});
  const result=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();
  if(await result.getAttribute('open')===null)await within(result.locator('summary').click(),deadline,'saved result disclosure');
  expect(JSON.parse(await within(result.locator('pre').innerText(),deadline,'saved result JSON'))).toEqual(item.record.result);
  const binding=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();
  if(await binding.getAttribute('open')===null)await within(binding.locator('summary').click(),deadline,'saved binding disclosure');
  const exact={evaluation_id:item.record.evaluation_id,evidence_sha256:item.record.evidence_sha256,binding:item.record.binding};
  expect(JSON.parse(await within(binding.locator('pre').innerText(),deadline,'saved binding JSON'))).toEqual(exact);return exact;
}
async function choose(page:Page,scope:Scope,item:any,origin:string,e:Evidence,w:Workspace,label:string,deadline=performance.now()+10_000){
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:remaining(deadline)});
  await within(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click(),deadline,'ordinary evaluation stage');
  const {summary,history,selector,details}=historyLocators(page);await expect(summary).toHaveCount(1,{timeout:remaining(deadline)});
  if(await history.getAttribute('open')===null)await within(summary.click(),deadline,'ordinary history disclosure');
  await within(history.getByLabel('평가 이력 모델 종류',{exact:true}).selectOption('segmentation'),deadline,'owning task');
  await within(history.getByLabel('평가 라벨 세트',{exact:true}).selectOption('default'),deadline,'owning labelset');
  const refresh=history.getByRole('button',{name:'평가 이력 새로고침',exact:true});await expect(refresh).toBeEnabled({timeout:remaining(deadline)});
  const generation=new Set<Request>();
  const started=(q:Request)=>{const u=new URL(q.url());if(q.frame()===page.mainFrame()&&q.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='segmentation'&&u.searchParams.get('labelset_id')==='default')generation.add(q);};
  page.on('request',started);
  try{
    const wire=page.waitForResponse(r=>generation.has(r.request()),{timeout:remaining(deadline)});void wire.catch(()=>undefined);
    await within(refresh.click(),deadline,'explicit owning history refresh');
    const response=await within(wire,deadline,'original history response'),raw=await body(response,deadline,e,w,label+'-history');
    expect(response.status()).toBe(200);expect(raw.proof.project_header).toBe(scope.project.id);expect(raw.proof.context.project_id).toBe(scope.project.id);
    const expected=scope.fixture.items.map((entry:any)=>entry.record.evaluation_id).sort();
    expect(raw.value.total).toBe(4);expect(raw.value.items.map((entry:any)=>entry.evaluation_id).sort()).toEqual(expected);
    for(const entry of scope.fixture.items)expect(raw.value.items.find((row:any)=>row.evaluation_id===entry.record.evaluation_id)).toEqual(entry.record);
    await expect.poll(()=>selector.locator('option').evaluateAll(nodes=>nodes.map(n=>(n as HTMLOptionElement).value).filter(Boolean).sort()),{timeout:remaining(deadline)}).toEqual(expected);
    await within(selector.selectOption(item.record.evaluation_id),deadline,'explicit saved report choice');
    if(await details.getAttribute('open')===null)await within(details.locator('summary').first().click(),deadline,'ordinary evidence disclosure');
    await expect(details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeEnabled({timeout:remaining(deadline)});
    return{details,history:raw.proof,binding:await saved(page,item,deadline)};
  }finally{page.off('request',started);}
}
function pixels(scope:Scope,view:Mask,className:'all'|'Scratch'|'Crack'){
  const output:number[]=[],classId=className==='all'?null:className==='Scratch'?7:23;
  for(let y=0;y<64;y++)for(let x=0;x<64;x++){
    const ty=scope.tag==='B'?63-y:y,px=scope.tag==='B'?63-x:x;
    const truth=x>=8&&x<24&&ty>=8&&ty<24?7:x>=32&&x<48&&ty>=16&&ty<40?23:0;
    const prediction=px>=12&&px<28&&y>=8&&y<24?7:px>=32&&px<48&&y>=20&&y<44?23:0;
    const a=classId===null?truth>0:truth===classId,b=classId===null?prediction>0:prediction===classId;
    const rgb=view==='truth'?(a?[74,222,128]:[35,35,35]):view==='prediction'?(b?[34,211,238]:[35,35,35]):[a&&truth!==prediction?244:35,b&&truth!==prediction?180:35,b&&truth!==prediction?255:35];
    output.push(...rgb,255);
  }return output;
}
async function mask(details:Locator,scope:Scope,view:Mask,className:'all'|'Scratch'|'Crack',deadline:number){
  await expect(details.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue(className,{timeout:remaining(deadline)});
  await expect(details.getByLabel('평가 마스크 보기',{exact:true})).toHaveValue(view,{timeout:remaining(deadline)});
  const canvas=details.locator('canvas'),expected=pixels(scope,view,className);
  // Match all RGBA bytes; a previous 64x64 frame is insufficient.
  await expect.poll(()=>canvas.evaluate(element=>{const c=element as HTMLCanvasElement;if(!c.width||!c.height)return null;
    return Array.from(c.getContext('2d')!.getImageData(0,0,c.width,c.height).data);}),{timeout:remaining(deadline)}).toEqual(expected);
  const actual=await within(canvas.evaluate(element=>{const c=element as HTMLCanvasElement;return{size:[c.width,c.height],pixels:Array.from(c.getContext('2d')!.getImageData(0,0,c.width,c.height).data)};}),deadline,'independent saved mask bytes');
  expect(actual.size).toEqual([64,64]);expect(actual.pixels).toEqual(expected);
  return{view,className,size:actual.size,rgba_sha256:sha(Buffer.from(actual.pixels))};
}
async function selectMask(details:Locator,scope:Scope,view:Mask,className:'all'|'Scratch'|'Crack',deadline:number){
  await within(details.getByLabel('평가 증거 클래스',{exact:true}).selectOption(className),deadline,'ordinary mask class');
  await within(details.getByLabel('평가 마스크 보기',{exact:true}).selectOption(view),deadline,'ordinary independent mask');
  return mask(details,scope,view,className,deadline);
}
const message=(variant:Variant)=>variant==='missing_source_hash'?'This saved evaluation has no captured source image hash':variant==='changed_source'?'Source image changed since this execution; saved prediction remains read-only.':'저장된 원본 좌표 변환이 없어 마스크를 겹칠 수 없습니다.';
const sourceURI=(scope:Scope,item:any,origin:string)=>origin+'/api/evaluation/history/'+item.record.evaluation_id+'/evidence-image?'+
  new URLSearchParams({source_dataset_path:scope.source,task:'segmentation',image_path:item.record.result.test_predictions[0].file_path});
function sourceMatch(q:Request,page:Page,scope:Scope,item:any,origin:string){
  const u=new URL(q.url()),sample=item.record.result.test_predictions[0];
  return q.frame()===page.mainFrame()&&q.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history/'+item.record.evaluation_id+'/evidence-image'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='segmentation'&&u.searchParams.get('image_path')===sample.file_path&&q.url()===sourceURI(scope,item,origin);
}
function validateSource(status:number,value:any,scope:Scope,item:any){
  const sample=item.record.result.test_predictions[0];
  if(item.variant==='missing_source_hash'||item.variant==='changed_source'){
    expect(status).toBe(409);expect(value).toEqual({detail:message(item.variant)});
  }else{
    expect(status).toBe(200);expect(value).toMatchObject({evaluation_id:item.record.evaluation_id,image_path:sample.file_path,
      image_sha256:sample.image_sha256,original_size:[160,96],read_only:true});
    expect(value.image).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/);
    expect(sha(fs.readFileSync(sample.file_path))).toBe(sample.image_sha256);
    if(item.variant==='unsupported_mapping')expect(sample.pixel_evidence.mapping.kind).toBe('unspecified_crop');
  }
  expect(item.record.binding.source_dataset_path).toBe(scope.source);
}
async function originalSource(page:Page,scope:Scope,item:any,details:Locator,origin:string,e:Evidence,w:Workspace,label:string,deadline=performance.now()+10_000){
  const opener=details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true});await expect(opener).toBeEnabled({timeout:remaining(deadline)});
  const generation=new Set<Request>(),started=(q:Request)=>{if(sourceMatch(q,page,scope,item,origin)&&!new URL(q.url()).searchParams.has('_f047_source_control_readback'))generation.add(q);};
  page.on('request',started);
  try{
    const wire=page.waitForResponse(r=>generation.has(r.request()),{timeout:remaining(deadline)});void wire.catch(()=>undefined);
    await within(opener.click(),deadline,'explicit original source retry');const response=await within(wire,deadline,'original owning source response');
    const raw=await body(response,deadline,e,w,label+'-original-source');validateSource(response.status(),raw.value,scope,item);
    expect(raw.proof.uri).toBe(sourceURI(scope,item,origin));
    expect(raw.proof.project_header).toBe(scope.project.id);expect(raw.proof.context.project_id).toBe(scope.project.id);
    return{...raw,opener,deadline,report_uuid:item.record.evaluation_id,source_path:item.source_path,
      saved_source_sha256:item.saved_source_sha256??null,actual_source_sha256:item.actual_source_sha256};
  }finally{page.off('request',started);}
}
async function refusal(page:Page,details:Locator,scope:Scope,item:any,retained:any,deadline:number){
  await expect(details.getByRole('alert')).toHaveCount(1,{timeout:remaining(deadline)});
  await expect(details.getByRole('alert')).toHaveText(message(item.variant),{timeout:remaining(deadline)});
  await expect(page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true})).toHaveCount(0,{timeout:remaining(deadline)});
  await expect(details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeEnabled({timeout:remaining(deadline)});
  expect(await mask(details,scope,retained.view,retained.className,deadline)).toEqual(retained);
  await saved(page,item,deadline);
}
type Control={ready:()=>Promise<any>;finish:()=>Promise<any>;close:()=>Promise<void>;deadline:()=>number};
async function control(page:Page,scope:Scope,item:any,origin:string,native:boolean,kind:'error'|'late',controlledMessage:string,e:Evidence,w:Workspace,label:string):Promise<Control>{
  const pattern=origin+'/api/evaluation/history/'+item.record.evaluation_id+'/evidence-image**';
  let armed=true,own:Request|undefined,captured:any,deadline=0,primary:unknown,hasPrimary=false,
    fulfillError:unknown,fulfillFailed=false,settlementError:unknown,settlementFailed=false,installed=false,closed=false;
  let release!:()=>void,ready!:()=>void,done!:()=>void,dispose!:(row:{kind:'finished'|'failed';failure:string|null})=>void;
  const gate=new Promise<void>(r=>release=r),readyPromise=new Promise<void>(r=>ready=r),donePromise=new Promise<void>(r=>done=r);
  const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>dispose=r);
  const ended=(q:Request)=>{if(q===own)dispose({kind:'finished',failure:null});},failed=(q:Request)=>{if(q===own)dispose({kind:'failed',failure:q.failure()?.errorText??null});};
  page.on('requestfinished',ended);page.on('requestfailed',failed);
  const handler=async(route:Route)=>{
    const q=route.request(),u=new URL(q.url());
    if(!armed||!sourceMatch(q,page,scope,item,origin)||u.searchParams.has('_f047_source_control_readback')){await route.continue();return;}
    armed=false;own=q;deadline=performance.now()+10_000;
    try{
      const headers:Record<string,string>={};for(const name of ['content-type','x-vision-project','x-vision-context']){const value=await q.headerValue(name);if(value!==null)headers[name]=value;}
      expect(headers['x-vision-project']).toBe(scope.project.id);expect(JSON.parse(headers['x-vision-context']).project_id).toBe(scope.project.id);
      let status:number,raw:Buffer,contentType:string|null,serverReadURI=q.url();
      if(native){
        // Electron reply source is a separate authenticated renderer GET.
        // It proves the server refusal source, never original native UI wire provenance.
        const delegate=new URL(q.url());delegate.searchParams.set('_f047_source_control_readback',crypto.randomUUID());
        serverReadURI=delegate.toString();
        const snapshot=await within(page.evaluate(async({url,headers,timeout})=>{
          const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),timeout);
          try{const response=await fetch(url,{method:'GET',headers,signal:abort.signal});const bytes=new Uint8Array(await response.arrayBuffer());
            if(bytes.length>1024*1024)throw Error('Controlled source response exceeds 1MiB');let binary='';for(const byte of bytes)binary+=String.fromCharCode(byte);
            return{status:response.status,contentType:response.headers.get('content-type'),base64:btoa(binary)};
          }finally{clearTimeout(timer);}
        },{url:delegate.toString(),headers,timeout:remaining(deadline)}),deadline,'controlled authenticated native server read');
        status=snapshot.status;raw=Buffer.from(snapshot.base64,'base64');contentType=snapshot.contentType;
      }else{
        const snapshot=await within(route.fetch({timeout:remaining(deadline)}),deadline,'original browser server response');
        status=snapshot.status();raw=await within(snapshot.body(),deadline,'complete browser source response');contentType=snapshot.headers()['content-type']??null;
      }
      expect(raw.length).toBeLessThanOrEqual(1024*1024);const value=JSON.parse(raw.toString('utf8'));validateSource(status,value,scope,item);
      expect(q.url()).toBe(sourceURI(scope,item,origin));
      captured={status,value,sha256:sha(raw),size:raw.length,request_uri:q.url(),controlled_server_read_uri:serverReadURI,request_headers:headers,
        report_uuid:item.record.evaluation_id,source_path:item.source_path,actual_main_frame:true,
        raw:save(e,w,label+'-captured-server-response',raw),
        transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_server_response'};
      ready();if(kind==='late')await within(gate,deadline,'release original A response');
      try{await within(route.fulfill(kind==='error'?{status:503,contentType:'application/json',body:JSON.stringify({detail:controlledMessage})}:
        {status,body:raw,headers:contentType?{'content-type':contentType}:{}}),deadline,'fulfill owning UI request');}catch(error){fulfillError=error;fulfillFailed=true;}
    }catch(error){
      primary=error;hasPrimary=true;ready();
      // This handler has admitted exactly own=q above. Settle only that request;
      // a failed server read must not leave its original UI route paused.
      try{await within(route.abort('aborted'),performance.now()+10_000,'abort only owned failed source request');}
      catch(error){settlementError=error;settlementFailed=true;}
    }finally{done();}
  };
  const close=async()=>{
    if(closed)return;closed=true;release();
    const deadline=performance.now()+10_000,failures:Array<{role:string;error:unknown}>=[];
    if(own){
      // Keep both disposition observers until the handler AND its original
      // request have joined, under one fresh bounded cleanup frame.
      try{await within(donePromise,deadline,'owned source handler final join');}catch(error){failures.push({role:'handler join',error});}
      try{await within(disposition,deadline,'owned source request final disposition');}catch(error){failures.push({role:'request disposition join',error});}
      if(hasPrimary)failures.push({role:'original source control error',error:primary});
      if(settlementFailed)failures.push({role:'owned route abort failure',error:settlementError});
    }
    try{if(installed&&!page.isClosed())await within(page.unroute(pattern,handler),deadline,'remove owned source route');}
    catch(error){failures.push({role:'owned route removal',error});}
    finally{page.off('requestfinished',ended);page.off('requestfailed',failed);}
    if(failures.length)throw new AggregateError(failures.map(row=>row.error),
      'Owned source cleanup failed: '+failures.map(row=>row.role+': '+String(row.error)).join('; '));
  };
  try{await page.route(pattern,handler);installed=true;}catch(error){await close();throw error;}
  return{deadline:()=>deadline,
    ready:async()=>{await within(readyPromise,performance.now()+10_000,'captured owning source GET');if(hasPrimary)throw primary;expect(captured).toBeTruthy();return captured;},
    finish:async()=>{
      release();await within(donePromise,deadline,'original owning handler completion');if(hasPrimary)throw primary;
      const result=await within(disposition,deadline,'original UI request disposition');
      if(result.kind==='failed'){expect(kind).toBe('late');expect(result.failure).toContain('ERR_ABORTED');}
      else{if(fulfillFailed)throw fulfillError;const response=await within(own!.response(),deadline,'owning controlled response');expect(response).not.toBeNull();
        expect(response!.status()).toBe(kind==='error'?503:captured.status);
        const raw=await within(response!.body(),deadline,'controlled delivered full body');expect(await within(response!.finished(),deadline,'controlled UI completion')).toBeNull();
        if(kind==='error')expect(JSON.parse(raw.toString('utf8'))).toEqual({detail:controlledMessage});else expect(sha(raw)).toBe(captured.sha256);}
      await within(page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r())))),deadline,'settle current UI after old response');
      return{kind,captured,disposition:result,controlled_503:kind==='error',backend_503_claim:false,
        original_native_HTTP_response_provenance:false,network_abort_guaranteed:false,server_job_cancel_claim:false};
    },close};
}
async function viewerOriginal(page:Page,scope:Scope,item:any,deadline:number){
  const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});await expect(viewer).toBeVisible({timeout:remaining(deadline)});
  await expect(viewer).toContainText(item.record.evaluation_id,{timeout:remaining(deadline)});
  await expect(viewer).toContainText(item.saved_source_sha256,{timeout:remaining(deadline)});
  await expect(viewer).toContainText(item.source_path,{timeout:remaining(deadline)});
  await expect(viewer.getByLabel('근거 이미지 종류',{exact:true})).toHaveValue('original',{timeout:remaining(deadline)});
  await expect(viewer.getByRole('alert')).toHaveCount(1,{timeout:remaining(deadline)});
  await expect(viewer.getByRole('alert')).toHaveText('전체 이미지 크기 변환으로 표시한 마스크입니다. 원본 해상도의 정답이나 측정값을 새로 산출하지 않습니다.',{timeout:remaining(deadline)});
  const actual=await within(viewer.getByRole('img',{name:'평가 입력 원본',exact:true}).evaluate(async element=>{
    const image=element as HTMLImageElement;await image.decode();const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;
    const ctx=c.getContext('2d')!;ctx.drawImage(image,0,0);return{size:[c.width,c.height],src:image.src,pixels:Array.from(ctx.getImageData(0,0,c.width,c.height).data)};
  }),deadline,'exact B original raster');
  const expected:number[]=[];for(let y=0;y<96;y++)for(let x=0;x<160;x++)expected.push(x,y,scope.blue,255);
  expect(actual.size).toEqual([160,96]);expect(actual.pixels).toEqual(expected);expect(actual.src).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/);
  return{report_uuid:item.record.evaluation_id,source_path:item.source_path,source_sha256:item.saved_source_sha256,
    size:actual.size,data_uri_sha256:sha(actual.src),rgba_sha256:sha(Buffer.from(actual.pixels))};
}
// Independent fixture oracle: nearest-floor projection from the saved 64x64
// class mask. Canvas decode canonically zeros RGB of fully transparent pixels.
function overlayPixels(scope:Scope,layer:Overlay,className:'all'|'Scratch'|'Crack'){
  const savedMask=pixels(scope,layer,className),output:number[]=[];
  for(let y=0;y<96;y++)for(let x=0;x<160;x++){
    const source=(Math.floor(y*64/96)*64+Math.floor(x*64/160))*4;
    if(savedMask[source]===35&&savedMask[source+1]===35&&savedMask[source+2]===35)output.push(0,0,0,0);
    else output.push(savedMask[source],savedMask[source+1],savedMask[source+2],255);
  }return output;
}
async function viewerOverlay(page:Page,scope:Scope,item:any,layer:Overlay,className:'all'|'Scratch'|'Crack',deadline:number,select=true){
  const original=await viewerOriginal(page,scope,item,deadline);
  const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
  const choice=viewer.getByLabel('근거 겹침 이미지',{exact:true}),label=layer==='truth'?'정답 마스크':'예측 마스크';
  // These are genuine available source-coordinate overlay controls. The API
  // carries no layer parameter; primary image remains the retrieved original.
  await expect(choice).toBeEnabled({timeout:remaining(deadline)});
  for(const [value,text]of [['truth','정답 마스크'],['prediction','예측 마스크']]){
    await expect(choice.locator('option[value="'+value+'"]')).toHaveCount(1,{timeout:remaining(deadline)});
    await expect(choice.locator('option[value="'+value+'"]')).toHaveText(text,{timeout:remaining(deadline)});
  }
  if(select)await within(choice.selectOption(layer),deadline,'ordinary saved '+layer+' overlay choice');
  // Readback after old A settles must not repair B by selecting the mode again.
  await expect(choice).toHaveValue(layer,{timeout:remaining(deadline)});
  await expect(viewer.getByLabel('근거 이미지 종류',{exact:true})).toHaveValue('original',{timeout:remaining(deadline)});
  const opacity=viewer.getByLabel('근거 겹침 투명도',{exact:true});
  await expect(opacity).toBeEnabled({timeout:remaining(deadline)});await expect(opacity).toHaveValue('0.5',{timeout:remaining(deadline)});
  const actual=await within(viewer.getByRole('img',{name:'겹침 '+label,exact:true}).evaluate(async element=>{
    const image=element as HTMLImageElement;await image.decode();const c=document.createElement('canvas');c.width=image.naturalWidth;c.height=image.naturalHeight;
    const ctx=c.getContext('2d')!;ctx.drawImage(image,0,0);const base=image.parentElement!.querySelector('img[alt="평가 입력 원본"]') as HTMLImageElement|null;
    if(!base)throw Error('Original source raster missing beside overlay');
    const rectangle=(node:Element)=>{const r=node.getBoundingClientRect();return[r.x,r.y,r.width,r.height];};
    return{size:[c.width,c.height],src:image.src,pixels:Array.from(ctx.getImageData(0,0,c.width,c.height).data),
      css:{opacity:getComputedStyle(image).opacity,overlay_object_fit:getComputedStyle(image).objectFit,
        original_object_fit:getComputedStyle(base).objectFit,overlay_rectangle:rectangle(image),original_rectangle:rectangle(base),
        same_parent:image.parentElement===base.parentElement,original_src:base.src}};
  }),deadline,'exact saved '+layer+' source overlay raster');
  const expected=overlayPixels(scope,layer,className);
  expect(actual.size).toEqual([160,96]);expect(actual.pixels).toEqual(expected);
  expect(actual.src).toMatch(/^data:image\/png;base64,[A-Za-z0-9+/=]+$/);
  expect(actual.css.opacity).toBe('0.5');expect(actual.css.same_parent).toBe(true);
  expect(actual.css.overlay_object_fit).toBe('contain');expect(actual.css.original_object_fit).toBe('contain');
  expect(actual.css.overlay_rectangle).toEqual(actual.css.original_rectangle);expect(actual.css.overlay_rectangle[2]).toBeGreaterThan(0);expect(actual.css.overlay_rectangle[3]).toBeGreaterThan(0);
  const facts=JSON.parse(await within(viewer.locator('details pre').textContent(),deadline,'source projection facts')||'null');
  expect(facts).toMatchObject({className,coordinate_mapping:{kind:'full_image_resize',source_size:[160,96]},
    model_input_shape:[64,64],projection:'nearest floor sampling for display only',saved_metrics_unchanged:true});
  const {original_src,...css}=actual.css;expect(sha(original_src)).toBe(original.data_uri_sha256);
  return{layer,conceptual_mode:layer==='truth'?'truth_overlay':'prediction_overlay',overlay_label:label,className,
    primary_mode:'original',original,size:actual.size,data_uri_sha256:sha(actual.src),rgba_sha256:sha(Buffer.from(actual.pixels)),
    css,facts,projection_is_saved_fixture_display_only:true};
}
async function exercise(page:Page,w:Workspace,e:Evidence,api:OwnedApi,origin:string,native:boolean,url?:string){
  const controls:Control[]=[],retainedFDs:number[]=[],databases:Array<{path:string;db:any}>=[],scopes:Scope[]=[],
    observations:any[]=[],transitions:any[]=[],sqliteSyncObservations:any[]=[],writes:Array<{method:string;path:string;body:unknown;main_frame:boolean}>=[],cleanupFailures:Array<{role:string;error:string}>=[];
  let before:any,after:any,sqliteBefore:any,sqliteAfter:any,failed=false,primary:unknown,observing=false;
  const observe=(q:Request)=>{const u=new URL(q.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(q.method()))writes.push({method:q.method(),path:u.pathname,body:q.postDataJSON(),main_frame:q.frame()===page.mainFrame()});};
  const remember=(role:string,error:unknown)=>{cleanupFailures.push({role,error:String(error)});if(!failed){failed=true;primary=error;}};
  const sqlite=()=>Object.fromEntries(databases.map(({path:file,db})=>{
    const tables=db.prepare("SELECT name,sql FROM sqlite_schema WHERE type='table' ORDER BY name").all();
    return[file,tables.map((table:any)=>({name:table.name,sql:table.sql,rows:db.prepare('SELECT * FROM "'+String(table.name).replace(/"/g,'""')+'"').all()
      .map((row:any)=>JSON.stringify(row,(_,value)=>typeof value==='bigint'?String(value):value)).sort()}))];
  }));
  try{
    const A=await make(w,api,'A'),B=await make(w,api,'B');scopes.push(A,B);
    expect(A.project.id).not.toBe(B.project.id);expect(A.valid.record.evaluation_id).not.toBe(B.valid.record.evaluation_id);
    const {DatabaseSync}=await import('node:sqlite');
    for(const scope of scopes){
      const file=path.join(scope.project.project_dir,'model_deployments.sqlite3'),info=fs.lstatSync(file);
      expect(info.isFile()).toBe(true);expect(info.isSymbolicLink()).toBe(false);expect(info.nlink).toBe(1);expect(fs.realpathSync(file)).toBe(file);
      const db=new DatabaseSync(file,{readOnly:true});databases.push({path:file,db});
      db.prepare('SELECT revisions.* FROM active_revisions JOIN revisions ON revisions.revision_id=active_revisions.revision_id').all();
    }
    await api('/api/project/open',{project_dir:A.project.project_dir});
    await frame('initial renderer navigation',deadline=>url?page.goto(url,{timeout:remaining(deadline)}):page.reload({timeout:remaining(deadline)}));
    for(const scope of scopes)expect(scope.fixture.items.map((item:any)=>item.variant)).toEqual(['valid',...VARIANTS]);
    await choose(page,A,A.valid,origin,e,w,'initial-A');
    sqliteBefore=sqlite();before=Object.fromEntries(scopes.map(scope=>[scope.tag,custody(scope,retainedFDs)]));
    page.on('request',observe);observing=true;
    for(const layer of LAYERS){
      const item=A.valid,label=(native?'source-electron':'browser')+'-'+layer+'-overlay';
      expect(item.variant).toBe('valid');expect(B.valid.variant).toBe('valid');
      const chosen=await choose(page,A,item,origin,e,w,label+'-A-before');
      const retained=await frame('independent saved '+layer+' mask before source outage',deadline=>selectMask(chosen.details,A,layer,'all',deadline));
      const controlledMessage='F047 controlled source-image outage '+layer+' overlay';
      const outage=await control(page,A,item,origin,native,'error',controlledMessage,e,w,label+'-error');controls.push(outage);
      await frame('owning source error click',deadline=>chosen.details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click({timeout:remaining(deadline)}));
      await outage.ready();const error=await outage.finish();await outage.close();
      await frame('controlled source error leaves saved '+layer+' mask',async deadline=>{
        await expect(chosen.details.getByRole('alert')).toHaveCount(1,{timeout:remaining(deadline)});
        await expect(chosen.details.getByRole('alert')).toHaveText(controlledMessage,{timeout:remaining(deadline)});
        await expect(historyLocators(page).history.getByRole('alert')).toHaveCount(1,{timeout:remaining(deadline)});
        await expect(page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true})).toHaveCount(0,{timeout:remaining(deadline)});
        expect(await mask(chosen.details,A,layer,'all',deadline)).toEqual(retained);
        expect(await saved(page,item,deadline)).toEqual(chosen.binding);
        await expect(chosen.details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true})).toBeEnabled({timeout:remaining(deadline)});
      });
      await e.screenshot(page,label+'-controlled503-independent-mask-retained');
      // Route removed: explicit retry is a genuine original authenticated GET.
      const retry=await originalSource(page,A,item,chosen.details,origin,e,w,label+'-retry');
      const recovered=await viewerOverlay(page,A,item,layer,'all',retry.deadline);
      expect(retry.proof.status).toBe(200);expect(recovered.original.data_uri_sha256).toBe(sha(retry.value.image));
      expect(await saved(page,item,retry.deadline)).toEqual(chosen.binding);
      expect(await mask(chosen.details,A,layer,'all',retry.deadline)).toEqual(retained);
      await expect(chosen.details.locator('xpath=.//p[@role="alert" and not(ancestor::*[@role="dialog"])]')).toHaveCount(0,{timeout:remaining(retry.deadline)});
      await e.screenshot(page,label+'-genuine-source-retry-overlay');
      const recoveredViewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
      await within(recoveredViewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click(),retry.deadline,'ordinary recovered A viewer close');
      await expect(recoveredViewer).toHaveCount(0,{timeout:remaining(retry.deadline)});await expect(retry.opener).toBeFocused({timeout:remaining(retry.deadline)});
      const fresh=await choose(page,A,item,origin,e,w,label+'-A-before-cancel');
      const A_mask=await frame('fresh A saved '+layer+' mask',deadline=>selectMask(fresh.details,A,layer,'all',deadline));expect(A_mask).toEqual(retained);
      const late=await control(page,A,item,origin,native,'late',controlledMessage,e,w,label+'-cancel');controls.push(late);
      await frame('start real A source read',deadline=>fresh.details.getByRole('button',{name:'원본 위에 평가 마스크 보기',exact:true}).click({timeout:remaining(deadline)}));
      const captured=await late.ready(),deadline=late.deadline();
      expect(captured.status).toBe(200);expect(captured.request_uri).toBe(sourceURI(A,item,origin));
      await expect(fresh.details.getByRole('button',{name:'평가 원본 확인 중…',exact:true})).toBeDisabled({timeout:remaining(deadline)});
      await expect(fresh.details.getByRole('alert')).toHaveCount(0,{timeout:remaining(deadline)});
      await expect(page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true})).toHaveCount(0,{timeout:remaining(deadline)});
      expect(await mask(fresh.details,A,layer,'all',deadline)).toEqual(retained);
      expect(await saved(page,item,deadline)).toEqual(fresh.binding);
      transitions.push(await project(page,B,origin,e,w,label+'-to-B',deadline));
      const Bchosen=await choose(page,B,B.valid,origin,e,w,label+'-B-current',deadline);
      const Bmask=await selectMask(Bchosen.details,B,layer,'Crack',deadline);expect(Bmask.rgba_sha256).not.toBe(A_mask.rgba_sha256);
      const Bsource=await originalSource(page,B,B.valid,Bchosen.details,origin,e,w,label+'-B-source',deadline);
      const Bbefore=await viewerOverlay(page,B,B.valid,layer,'Crack',deadline),Bbinding=await saved(page,B.valid,deadline);
      expect(Bsource.proof.status).toBe(200);expect(Bsource.proof.uri).toBe(sourceURI(B,B.valid,origin));
      expect(Bbefore.original.data_uri_sha256).toBe(sha(Bsource.value.image));
      expect(Bbefore.original.report_uuid).not.toBe(item.record.evaluation_id);expect(Bbefore.original.source_path).not.toBe(item.source_path);
      expect(Bsource.proof.uri).not.toBe(captured.request_uri);expect(Bbefore.rgba_sha256).not.toBe(recovered.rgba_sha256);
      const cancel=await late.finish();await late.close();
      expect(cancel.captured.sha256).toBe(captured.sha256);
      await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(B.project.name,{timeout:remaining(deadline)});
      await expect(historyLocators(page).selector).toHaveValue(B.valid.record.evaluation_id,{timeout:remaining(deadline)});
      // The legitimate projection warning belongs to B's viewer; source/history
      // errors outside the dialog remain absent after A's original disposition.
      await expect(Bchosen.details.locator('xpath=.//p[@role="alert" and not(ancestor::*[@role="dialog"])]')).toHaveCount(0,{timeout:remaining(deadline)});
      await expect(historyLocators(page).history.locator('xpath=.//p[@role="alert" and not(ancestor::*[@role="dialog"])]')).toHaveCount(0,{timeout:remaining(deadline)});
      const Bafter=await viewerOverlay(page,B,B.valid,layer,'Crack',deadline,false);expect(Bafter).toEqual(Bbefore);
      expect(await saved(page,B.valid,deadline)).toEqual(Bbinding);expect(await mask(Bchosen.details,B,layer,'Crack',deadline)).toEqual(Bmask);
      const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});
      await expect(viewer).not.toContainText(item.record.evaluation_id,{timeout:remaining(deadline)});
      await expect(viewer).not.toContainText(item.source_path,{timeout:remaining(deadline)});
      await expect(viewer).not.toContainText(controlledMessage,{timeout:remaining(deadline)});
      await e.screenshot(page,label+'-old-A-reply-cannot-replace-B-overlay');
      await within(viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click(),deadline,'ordinary B viewer close');
      await expect(viewer).toHaveCount(0,{timeout:remaining(deadline)});await expect(Bsource.opener).toBeFocused({timeout:remaining(deadline)});
      expect(await mask(Bchosen.details,B,layer,'Crack',deadline)).toEqual(Bmask);
      transitions.push(await project(page,A,origin,e,w,label+'-return-A'));
      const returned=await choose(page,A,item,origin,e,w,label+'-A-return');
      const returnedMask=await frame('returned A genuine report retains owned '+layer+' mask',deadline=>selectMask(returned.details,A,layer,'all',deadline));expect(returnedMask).toEqual(retained);
      expect(returned.binding).toEqual(chosen.binding);
      observations.push({action:ACTIONS[layer],layer,conceptual_mode:layer==='truth'?'truth_overlay':'prediction_overlay',
        error_cell:ACTIONS[layer]+'.error',cancel_cell:ACTIONS[layer]+'.cancel',
        A_report_uuid:item.record.evaluation_id,A_source_uri:captured.request_uri,A_source_path:item.source_path,
        A_report_sha256:item.report_sha256,independent_mask:retained,error,retry:{...retry,opener:undefined},recovered_overlay:recovered,
        cancel:{...cancel,meaning:'revoke A display admission by ordinary project/report B navigation; release captured A server reply'},
        B_report_uuid:B.valid.record.evaluation_id,B_source_uri:Bsource.proof.uri,B_source_readback:{...Bsource,opener:undefined},
        B_overlay_before:Bbefore,B_overlay_after:Bafter,B_mask:Bmask,B_binding:Bbinding,A_return_binding:returned.binding});
    }
    expect(observations).toHaveLength(2);expect(transitions).toHaveLength(4);
    expect(writes).toEqual(LAYERS.flatMap(()=>[B,A].map(scope=>({method:'POST',path:'/api/project/open',body:{project_dir:scope.project.project_dir},main_frame:true}))));
    expect(await api('/api/project/current')).toEqual(A.project);
  }catch(error){failed=true;primary=error;}
  finally{
    for(const own of controls)try{await own.close();}catch(error){remember('join-and-remove-owned-source-route',error);}
    if(observing)try{page.off('request',observe);}catch(error){remember('remove-own-business-write-observer',error);}
    try{if(!page.isClosed()){
      const viewer=page.getByRole('dialog',{name:'이미지 판정 근거 보기',exact:true});if(await viewer.count())await frame('ordinary owned viewer cleanup',deadline=>viewer.getByRole('button',{name:'저장 평가로 돌아가기',exact:true}).click({timeout:remaining(deadline)}));
    }}catch(error){remember('ordinary-viewer-cleanup',error);}
    if(before){
      try{sqliteAfter=sqlite();expect(sqliteAfter).toEqual(sqliteBefore);}catch(error){remember('readonly-SQLite-row-snapshot',error);}
      after={};for(const scope of scopes)try{after[scope.tag]=custody(scope);sqliteSyncObservations.push(compareOwnedCustody(scope,before[scope.tag],after[scope.tag]));}catch(error){remember('whole-own-project-source-input-tree-'+scope.tag,error);}
      for(const scope of scopes)for(const [kind,root]of [['source',scope.source],['project',scope.project.project_dir],['controlled_inputs',path.join(scope.root,'controlled-evaluation-inputs')]] as const){
        for(const [member,row]of Object.entries(before[scope.tag][kind] as Record<string,any>))if(row.kind==='file')try{e.addFile(path.join(root,member));}catch(error){remember('retain-own-file-'+scope.tag+'-'+member,error);}
      }
    }
    for(const {db}of databases)try{db.close();}catch(error){remember('close-readonly-SQLite-observer',error);}
    for(const fd of retainedFDs)try{fs.closeSync(fd);}catch(error){remember('close-retained-raw-SQLite-FD',error);}
    const proof={schema:'modu-vision.F047-truth-prediction-overlay-error-cancel/v1',mode:native?'source-electron':'browser',scenario_cells:CELLS,
      fixture_records:scopes.map(scope=>({tag:scope.tag,project:scope.project,fixture:scope.fixture})),before,after,sqlite_before:sqliteBefore,sqlite_after:sqliteAfter,
      observations,transitions,sqlite_sync_observations:sqliteSyncObservations,business_writes:writes,cleanup_failures:cleanupFailures,
      resource_closure:{owned_routes_joined_and_removed:cleanupFailures.every(row=>row.role!=='join-and-remove-owned-source-route'),
        request_observer_removed:!cleanupFailures.some(row=>row.role==='remove-own-business-write-observer'),
        readonly_sqlite_closed:!cleanupFailures.some(row=>row.role==='close-readonly-SQLite-observer'),
        retained_raw_FDs_closed:!cleanupFailures.some(row=>row.role==='close-retained-raw-SQLite-FD')},
      controlled_saved_reports_not_model_inference:true,actual_model_inference:false,controlled_503_not_backend_outage_claim:true,
      cancellation_is_stale_UI_response_fencing:true,network_abort_guaranteed:false,server_job_cancel_claim:false,
      F047_truth_prediction_error_cancel_assertions_complete:!failed,
      F047_refusal_cells_recredited:false,F047_truth_or_prediction_layer_credit:false,F051_error_layer_credit:false,
      registry_mutation:false,parent_acceptance:false,installed_native_acceptance:false,frozen_native_acceptance:false,
      device_acceptance:false,human_label_acceptance:false,model_quality_acceptance:false,
      completed_assertions_without_authority_credit:!failed};
    try{save(e,w,'evaluation-mask-truth-prediction-transitions-proof',proof);e.note('evaluation_mask_truth_prediction_transitions',proof);}catch(error){remember('durable-own-readback',error);}
  }
  if(failed)throw primary;
}
test.use({actionTimeout:10_000});
test('saved truth and prediction overlays recover after source outage and fence old A replies from B',async({page,renderer,workspace,evidence})=>{
  await installDesktopHostShim(page,renderer.port);
  await exercise(page,workspace,evidence,ownedApi(page,renderer.origin,false),renderer.origin,false,renderer.url);
});
test('source Electron saved truth and prediction overlays recover after outage and preserve B after canceled A read',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;
  await exercise(page,workspace,evidence,ownedApi(page,origin,true),origin,true);
});

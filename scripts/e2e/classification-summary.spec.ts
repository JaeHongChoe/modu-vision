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
type Folder=(folder:string)=>Promise<void>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const cases=[
 {name:'Folder cohort',files:['train/OK/a.png','train/OK/b.png','train/NG/c.png','val/OK/d.png','val/NG/e.png','test/NG/f.png'],
  ignored:['train/OK/.hidden.png','train/@eaDir/thumb.png','val/OK/.hidden.png','val/@eaDir/thumb.png','test/OK/.hidden.png','test/@eaDir/thumb.png'],classes:{OK:3,NG:3},split:{train:3,val:2,test:1}},
 {name:'Small automatic cohort',files:['OK/a.png','OK/b.png','NG/c.png','NG/d.png'],ignored:['OK/.hidden.png','@eaDir/thumb.png'],classes:{OK:2,NG:2},split:{train:2,val:2,test:0}},
 {name:'Singleton automatic cohort',files:['OK/a.png','NG/b.png'],ignored:['OK/.hidden.png','@eaDir/thumb.png'],classes:{OK:1,NG:1},split:{train:2,val:0,test:0}},
 {name:'Validation only cohort',files:['val/OK/a.png','test/NG/b.png'],ignored:['val/OK/.hidden.png','test/@eaDir/thumb.png'],classes:{OK:1,NG:1},split:{train:0,val:1,test:1}},
];

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,folder:Folder,native:boolean,url?:string){
 const writes:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/\/(training\/start|train|compute\/jobs|dataset\/split)$/.test(new URL(r.url()).pathname))writes.push(r.url());});
 const receipts:any[]=[];
 const navigate=async(name:string)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();};
 const assertCards=async(entry:typeof cases[number])=>{
  await expect(page.getByText('학습 가능 이미지',{exact:true}).locator('..').getByText(String(entry.files.length),{exact:true})).toBeVisible();
  await expect(page.getByText('적용된 학습 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.train),{exact:true})).toBeVisible();
  await expect(page.getByText('적용된 검증 / 테스트 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.val+entry.split.test),{exact:true})).toBeVisible();
  for(const [key,value]of Object.entries(entry.split))await expect(page.getByText(key==='train'?'Train:':key==='val'?'Val:':'Test:',{exact:true}).locator('..').getByText(String(value),{exact:true})).toBeVisible();
 };
 for(const [index,entry]of cases.entries()){
  const source=path.join(workspace.root,`classification-${index}`),originals:Record<string,string>={};
  for(const [i,relative]of [...entry.files,...entry.ignored].entries()){const file=path.join(source,relative);fs.mkdirSync(path.dirname(file),{recursive:true});const bytes=png(32,3,(x,y)=>[(x+i*7)%256,(y+i*9)%256,50+i]);fs.writeFileSync(file,bytes);originals[relative]=sha(bytes);}
  const project=await api('/api/project/create',{name:entry.name,task:'classification'});await navigate(entry.name);await folder(source);
  const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/import'&&r.request().method()==='POST'&&r.request().postDataJSON().folder_path===source);
  await page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true}).click();const response=await reply;expect(response.status()).toBe(200);const imported=await response.json();
  expect(imported.total_images).toBe(entry.files.length);expect(imported.source_images).toBe(entry.files.length);expect(imported.classes).toEqual(entry.classes);expect(imported.split).toEqual(entry.split);await assertCards(entry);
  const current=await api('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
  const cohort=JSON.parse(execFileSync(process.env.MV_E2E_PYTHON||'python3',['scripts/e2e/fixtures/classification_cohort.py',source],{encoding:'utf8'}));
  expect(cohort.files).toEqual(originals);const actual=Object.values(cohort.partitions).flat() as Array<{path:string;label:string}>;
  expect(actual.map(r=>r.path).sort()).toEqual([...entry.files].sort());for(const [partition,count]of Object.entries(entry.split))expect(cohort.partitions[partition]).toHaveLength(count);
  expect(new Set(actual.map(r=>r.path)).size).toBe(entry.files.length);
  await navigate(entry.name);await assertCards(entry);await evidence.screenshot(page,`${native?'native':'browser'}-classification-${index}-reopened`);
  for(const [relative,want]of Object.entries(originals)){const file=path.join(source,relative);expect(sha(fs.readFileSync(file))).toBe(want);evidence.addFile(file);}
  receipts.push({name:entry.name,project_id:project.id,project_dir:project.project_dir,source,expected:{total:entry.files.length,classes:entry.classes,split:entry.split,eligible:entry.files,ignored:entry.ignored},imported,cohort,originals,reopened:true});
 }
 await api('/api/project/open',{project_dir:receipts[0].project_dir});await navigate(cases[0].name);await assertCards(cases[0]);expect((await api('/api/project/current')).source_dataset_dir).toBe(receipts[0].source);
 expect(writes).toEqual([]);evidence.note('classification_summary',{receipts,original_project_reopened:true,prohibited_writes:writes,folder_dialog_answer_controlled:true,actual_renderer_and_backend:true,default_loader_cohort_only:true,training_and_model_quality_not_assessed:true});
}

test('classification import summary matches eligible default loader partitions and reopens',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const folder:Folder=(value)=>page.evaluate(source=>{(window as any).api.selectFolder=async()=>source;},value);await exercise(page,workspace,evidence,api,folder,false,renderer.url);
});
test('native classification import summary matches eligible default loader partitions and reopens',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window,app}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned cohort API HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});
 const folder:Folder=value=>app.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},value);await exercise(window,workspace,evidence,api,folder,true);
});


// F019 handoff: controlled folder answers, genuine renderer/backend requests.
// Source Electron is distinct from an installed application or an OS picker.
import type {Response as ClassificationResponse, Request as ClassificationRequest} from '@playwright/test';
import type {BigIntStats as ClassificationStat} from 'node:fs';
type ClassificationProject={id:string;name:string;task:string;project_dir:string;dataset_dir:string;models_dir:string;reports_dir:string;annotations_dir:string;active_labelset_id?:string;source_dataset_dir:string|null};
type ClassificationScope={index:number;tag:string;source:string;project:ClassificationProject;entry:typeof cases[number];originals:Record<string,string>;context?:Record<string,unknown>;statistics?:ClassificationStatistics;metadata?:ClassificationMetadata[]};
type ClassificationItem={image_id:string;file_name:string;file_path:string;relative_path:string;labels:string[];label:string;label_status:string;split:string;thumbnail_url:string};
type ClassificationStatistics={total:number;labelset_id:string;labeling:Record<string,{count:number;ratio:number}>;assignments:Record<string,{count:number;ratio:number}>;classes:Record<string,{count:number;ratio:number}>;items:ClassificationItem[];class_count_grain:string};
type ClassificationMetadata={image_uuid:string;file_path:string;relative_path:string;content_hash:string;content_version:number;revision:number;workflow_state:string;usage_state:string;[key:string]:unknown};
type ClassificationSnapshot=Record<string,{kind:'file'|'directory';raw7:string[];sha256?:string}>;
const classificationRaw=(s:ClassificationStat)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
function classificationSecondary(primary:unknown,failures:unknown[]){
 if(primary instanceof Error&&failures.length)try{Object.defineProperty(primary,'classification_secondary',{value:failures.map(e=>e instanceof Error?e.name:typeof e),configurable:true});}catch{/* Keep the original failure. */}
}
function classificationFile(file:string){
 const before=fs.lstatSync(file,{bigint:true});expect(before.isFile()).toBe(true);expect(before.isSymbolicLink()).toBe(false);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown,bytes:Buffer|undefined;const failures:unknown[]=[];
 try{expect(classificationRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(classificationRaw(before));bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(before.size);
  expect(classificationRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(classificationRaw(before));expect(classificationRaw(fs.lstatSync(file,{bigint:true}))).toEqual(classificationRaw(before));
 }catch(error){primary=error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)primary=error;else failures.push(error);}}
 if(primary!==undefined){classificationSecondary(primary,failures);throw primary;}return {kind:'file' as const,raw7:classificationRaw(before),sha256:sha(bytes!)};
}
function classificationTree(root:string):ClassificationSnapshot{
 expect(path.isAbsolute(root)).toBe(true);expect(fs.realpathSync(root)).toBe(root);const rows:ClassificationSnapshot={};
 const walk=(directory:string)=>{const before=fs.lstatSync(directory,{bigint:true});expect(before.isDirectory()).toBe(true);expect(before.isSymbolicLink()).toBe(false);
  const names=fs.readdirSync(directory).sort();rows[path.relative(root,directory)||'.']={kind:'directory',raw7:classificationRaw(before)};
  for(const name of names){const file=path.join(directory,name),info=fs.lstatSync(file,{bigint:true});expect(info.isSymbolicLink()).toBe(false);
   if(info.isDirectory())walk(file);else rows[path.relative(root,file)]=classificationFile(file);}
  expect(fs.readdirSync(directory).sort()).toEqual(names);expect(classificationRaw(fs.lstatSync(directory,{bigint:true}))).toEqual(classificationRaw(before));};walk(root);return rows;
}
function classificationRoots(scope:ClassificationScope){
 const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir};
 for(const [kind,root]of Object.entries(roots))if(kind!=='source'){expect(path.dirname(root)).toBe(scope.project.project_dir);expect(fs.realpathSync(root)).toBe(root);}
 return {trees:Object.fromEntries(Object.entries(roots).map(([kind,root])=>[kind,{root,rows:classificationTree(root)}])),manifest:classificationFile(path.join(scope.project.project_dir,'project.json'))};
}
async function classificationWithin<T>(wire:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original classification 10s frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;return Promise.race([wire,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original classification 10s frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
const classificationRemaining=(deadline:number)=>Math.max(1,Math.floor(deadline-performance.now()));
function classificationSave(w:Workspace,e:Evidence,label:string,value:unknown){const file=path.join(w.logs,label+'.json');fs.writeFileSync(file,JSON.stringify(value,null,2),{flag:'wx'});e.addFile(file);return {path:file,sha256:sha(fs.readFileSync(file))};}
function classificationWire(page:Page,origin:string,route:string,method:string,match?:(r:ClassificationRequest,u:URL)=>boolean){
 const wire=page.waitForResponse(response=>{const request=response.request(),u=new URL(response.url());return request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===route&&request.method()===method&&(!match||match(request,u));},{timeout:10_000});
 void wire.catch(()=>{});return wire;
}
async function classificationBody<T>(page:Page,origin:string,response:ClassificationResponse,scope:ClassificationScope|null,deadline:number,w:Workspace,e:Evidence,label:string){
 const request=response.request(),u=new URL(response.url());expect(request.frame()).toBe(page.mainFrame());expect(u.origin).toBe(origin);expect(response.status()).toBe(200);
 const projectHeader=await classificationWithin(request.headerValue('x-vision-project'),deadline,'request project header'),contextHeader=await classificationWithin(request.headerValue('x-vision-context'),deadline,'request context header');
 let context:Record<string,unknown>|null=null;if(scope){expect(projectHeader).toBe(scope.project.id);expect(contextHeader).not.toBeNull();context=JSON.parse(contextHeader!);expect(context!.project_id).toBe(scope.project.id);expect(context!.mode).toBe('local');}
 const raw=await classificationWithin(response.body(),deadline,'full renderer response body');expect(raw.length).toBeLessThanOrEqual(1024*1024);expect(await classificationWithin(response.finished(),deadline,'same response finished')).toBeNull();
 const file=path.join(w.logs,label+'-response.json');fs.writeFileSync(file,raw,{flag:'wx'});e.addFile(file);
 return {body:JSON.parse(raw.toString('utf8')) as T,proof:{method:request.method(),origin,route:u.pathname,query:u.search,request_body:request.postDataJSON(),project_header:projectHeader,context,status:200,actual_main_frame:true,finished:true,response_sha256:sha(raw),response_bytes:raw.length,response_file:file}};
}
function classificationSummary(value:{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown},scope:ClassificationScope){
 expect(value.total_images).toBe(scope.entry.files.length);expect(value.source_images).toBe(scope.entry.files.length);expect(value.unlabeled_images).toBe(0);expect(value.classes).toEqual(scope.entry.classes);expect(value.split).toEqual(scope.entry.split);
}
function classificationStatistics(value:ClassificationStatistics,scope:ClassificationScope){
 const total=scope.entry.files.length;expect(value.total).toBe(total);expect(value.labelset_id).toBe(scope.project.active_labelset_id||'default');
 expect(value.labeling).toEqual({labeled:{count:total,ratio:1},unlabeled:{count:0,ratio:0}});
 const counts={train:0,val:0,test:0,not_used:0,not_split:0};
 for(const rel of scope.entry.files){const part=rel.split('/')[0];counts[part==='train'||part==='val'||part==='test'?part:'not_split']++;}
 expect(value.assignments).toEqual(Object.fromEntries(Object.entries(counts).map(([key,count])=>[key,{count,ratio:count/total}])));
 expect(value.classes).toEqual(Object.fromEntries(Object.entries(scope.entry.classes).map(([key,count])=>[key,{count,ratio:count/total}])));
 expect(value.items.map(item=>item.file_path).sort()).toEqual(scope.entry.files.map(rel=>path.join(scope.source,rel)).sort());
 for(const item of value.items){const rel=path.relative(scope.source,item.file_path);expect(item.relative_path).toBe(rel);expect(item.file_name).toBe(path.basename(rel));expect(item.image_id).toBe(path.basename(rel,'.png'));
  expect(item.labels).toEqual([path.basename(path.dirname(rel))]);expect(item.label).toBe(path.basename(path.dirname(rel)));expect(item.label_status).toBe('labeled');
  const part=rel.split('/')[0];expect(item.split).toBe(part==='train'||part==='val'||part==='test'?part:'not_split');expect(new URL(item.thumbnail_url,'http://owned.invalid').searchParams.get('file_path')).toBe(item.file_path);}
}
async function classificationCards(page:Page,scope:ClassificationScope,deadline:number){
 const timeout=()=>({timeout:classificationRemaining(deadline)});const entry=scope.entry;
 await classificationWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,timeout()),deadline,'own project header');
 await classificationWithin(expect(page.getByText('학습 가능 이미지',{exact:true}).locator('..').getByText(String(entry.files.length),{exact:true})).toBeVisible(timeout()),deadline,'eligible images card');
 for(const [key,count]of Object.entries(entry.split))await classificationWithin(expect(page.getByText(key==='train'?'Train:':key==='val'?'Val:':'Test:',{exact:true}).locator('..').getByText(String(count),{exact:true})).toBeVisible(timeout()),deadline,'original default-loader split card');
 const panel=page.getByRole('region',{name:'활성 라벨 세트 데이터 통계',exact:true});await classificationWithin(expect(panel).toHaveCount(1,timeout()),deadline,'own statistics region');
 for(const [name,count]of [['전체',entry.files.length],['라벨 완료',entry.files.length],['라벨 미완료',0]] as const){const button=panel.getByRole('button').filter({has:page.getByText(name,{exact:true})});await classificationWithin(expect(button).toHaveCount(1,timeout()),deadline,'exact count chip');await classificationWithin(expect(button.locator('strong')).toHaveText(String(count),timeout()),deadline,'exact label count');}
 const details=panel.locator('details');await classificationWithin(expect(details).toHaveCount(1,timeout()),deadline,'class counts details');if(!(await classificationWithin(details.evaluate(node=>node.hasAttribute('open')),deadline,'class details state')))await classificationWithin(details.locator(':scope > summary').click(),deadline,'ordinary class counts disclosure');
 for(const [label,count]of Object.entries(entry.classes)){const button=details.getByRole('button').filter({has:page.getByText(label,{exact:true})});await classificationWithin(expect(button).toHaveCount(1,timeout()),deadline,'own class chip');await classificationWithin(expect(button.locator('strong')).toHaveText(String(count),timeout()),deadline,'own class count');}
 const thumbs=page.locator('button[aria-label$=" 라벨링에서 열기"] img');await classificationWithin(expect(thumbs).toHaveCount(entry.files.length,timeout()),deadline,'eligible gallery');
 await classificationWithin(expect.poll(()=>thumbs.evaluateAll(nodes=>nodes.every(node=>node instanceof HTMLImageElement&&node.complete&&node.naturalWidth>0)),timeout()).toBe(true),deadline,'actual gallery pixels');
 const paths=await classificationWithin(thumbs.evaluateAll(nodes=>nodes.map(node=>new URL((node as HTMLImageElement).src).searchParams.get('file_path')).sort()),deadline,'gallery original source paths');expect(paths).toEqual(entry.files.map(rel=>path.join(scope.source,rel)).sort());
}
async function classificationHandoff(page:Page,w:Workspace,e:Evidence,folder:Folder,native:boolean,origin:string,url?:string){
 const scopes:ClassificationScope[]=[],proofs:unknown[]=[],before=new Map<number,ReturnType<typeof classificationRoots>>(),after:Record<string,unknown>={},prohibited:unknown[]=[];let baseline=false,sequence=0,primary:unknown;
 const observe=(r:ClassificationRequest)=>{const u=new URL(r.url());if(baseline&&u.origin===origin&&r.method()!=='GET'&&r.method()!=='HEAD'&&!(r.method()==='POST'&&u.pathname==='/api/project/open'))prohibited.push({method:r.method(),route:u.pathname,body:r.postData()});};page.on('request',observe);
 const api=async<T>(route:string,scope:ClassificationScope|null=null,body?:unknown,method?:string):Promise<T>=>{const deadline=performance.now()+10_000;
  const result=await classificationWithin(page.evaluate(async({origin,route,body,method,project,context,remaining})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);try{const headers:Record<string,string>={'Content-Type':'application/json'};if(project){headers['X-Vision-Project']=project.id;if(context)headers['X-Vision-Context']=JSON.stringify(context);}
   const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort.signal});const raw=await response.text();if(raw.length>1024*1024)throw Error('Owned classification API response exceeds 1MiB');return {status:response.status,raw};}finally{clearTimeout(timer);}},
   {origin,route,body,method,project:scope?.project||null,context:scope?.context||null,remaining:classificationRemaining(deadline)}),deadline,'owned complete API read');expect(result.status).toBe(200);proofs.push({kind:'controlled owned API',route,method:method||(body===undefined?'GET':'POST'),project_id:scope?.project.id||null,response_sha256:sha(Buffer.from(result.raw)),finished_body:true});return JSON.parse(result.raw) as T;};
 const view=async(scope:ClassificationScope,label:string)=>{const deadline=performance.now()+10_000;
  await classificationWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click(),deadline,'ordinary data stage');
  const panel=page.getByRole('region',{name:'활성 라벨 세트 데이터 통계',exact:true}),button=panel.getByRole('button',{name:'데이터 통계 새로고침',exact:true});
  await classificationWithin(expect(button).toBeEnabled({timeout:classificationRemaining(deadline)}),deadline,'statistics ready');const wire=classificationWire(page,origin,'/api/dataset/metadata/statistics','GET');await classificationWithin(button.click(),deadline,'ordinary statistics refresh');
  const row=await classificationBody<ClassificationStatistics>(page,origin,await classificationWithin(wire,deadline,'own statistics response'),scope,deadline,w,e,`${label}-${++sequence}`);classificationStatistics(row.body,scope);scope.context=row.proof.context!;await classificationCards(page,scope,deadline);
  const current=await api<ClassificationProject>('/api/project/current',scope);expect(current).toEqual(scope.project);
  const metadata=await api<{items:ClassificationMetadata[];total:number}>('/api/dataset/metadata?folder_path='+encodeURIComponent(scope.source)+'&limit=5000',scope);expect(metadata.total).toBe(scope.entry.files.length);
  const ordered=metadata.items.sort((a,b)=>a.file_path.localeCompare(b.file_path));expect(ordered.map(item=>item.file_path).sort()).toEqual(scope.entry.files.map(rel=>path.join(scope.source,rel)).sort());
  for(const item of ordered){const rel=path.relative(scope.source,item.file_path);expect(item.relative_path).toBe(rel);expect(item.content_hash).toBe(scope.originals[rel]);expect(item.content_version).toBe(1);expect(item.revision).toBe(1);expect(item.workflow_state).toBe('unworked');expect(item.usage_state).toBe('active');expect(item.image_uuid).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);}
  if(scope.statistics){expect(row.body).toEqual(scope.statistics);expect(ordered).toEqual(scope.metadata);}else{scope.statistics=row.body;scope.metadata=ordered;}
  proofs.push({scope:scope.tag,statistics:row.proof,image_metadata:ordered});await e.screenshot(page,`${native?'native':'browser'}-classification-handoff-${label}-${scope.tag}`);
 };
 const open=async(scope:ClassificationScope,label:string)=>{const deadline=performance.now()+10_000;
  await classificationWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'project manager');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await classificationWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'recent projects');
  const wire=classificationWire(page,origin,'/api/project/open','POST',r=>r.postDataJSON()?.project_dir===scope.project.project_dir),summary=classificationWire(page,origin,'/api/dataset/current-summary','GET',(_r,u)=>u.searchParams.get('folder_path')===scope.source&&u.searchParams.get('task')==='classification');
  const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await classificationWithin(expect(item).toHaveCount(1,{timeout:classificationRemaining(deadline)}),deadline,'exact owning recent project');await classificationWithin(expect(item).toBeEnabled({timeout:classificationRemaining(deadline)}),deadline,'own recent project enabled');await classificationWithin(item.click(),deadline,'normal owning project open');
  const opened=await classificationBody<ClassificationProject>(page,origin,await classificationWithin(wire,deadline,'own POST open'),null,deadline,w,e,`${label}-open-${++sequence}`);expect(opened.proof.request_body).toEqual({project_dir:scope.project.project_dir});expect(opened.body).toEqual(scope.project);
  const restored=await classificationBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationWithin(summary,deadline,'own saved summary'),scope,deadline,w,e,`${label}-summary-${++sequence}`);classificationSummary(restored.body,scope);
  await classificationWithin(expect(dialog).toHaveCount(0,{timeout:classificationRemaining(deadline)}),deadline,'project manager closed');proofs.push({scope:scope.tag,open:opened.proof,current_summary:restored.proof});await view(scope,label);
 };
 try{
  if(url)await page.goto(url);else await page.reload();
  for(const [index,entry]of cases.entries()){
   const source=path.join(w.root,'classification-handoff-'+index);expect(fs.existsSync(source)).toBe(false);const originals:Record<string,string>={};
   for(const [i,rel]of [...entry.files,...entry.ignored].entries()){const file=path.join(source,rel);fs.mkdirSync(path.dirname(file),{recursive:true});const bytes=png(32,3,(x,y)=>[(x+index*47+i*7)%256,(y+index*31+i*9)%256,50+index*30+i]);fs.writeFileSync(file,bytes,{flag:'wx'});originals[rel]=sha(bytes);}
   const created=await api<ClassificationProject>('/api/project/create',null,{name:'Classification handoff '+index,task:'classification'});expect(created.id).toMatch(/^[0-9a-f]{8}$/);expect(created.task).toBe('classification');expect(path.dirname(created.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);expect(fs.realpathSync(created.project_dir)).toBe(created.project_dir);
   if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(created.name,{timeout:10_000});await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await folder(source);
   const scope:ClassificationScope={index,tag:'P'+index,source,project:created,entry,originals};const deadline=performance.now()+10_000;
   const saved=classificationWire(page,origin,'/api/project/update','PUT',r=>r.postDataJSON()?.source_dataset_dir===source),imported=classificationWire(page,origin,'/api/dataset/import','POST',r=>r.postDataJSON()?.folder_path===source&&r.postDataJSON()?.task==='classification');
   await classificationWithin(page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true}).click(),deadline,'normal controlled folder import');
   const save=await classificationBody<ClassificationProject>(page,origin,await classificationWithin(saved,deadline,'own source PUT'),scope,deadline,w,e,`setup-${index}-source`);expect(save.proof.request_body).toEqual({source_dataset_dir:source});expect(save.body.id).toBe(created.id);expect(save.body.project_dir).toBe(created.project_dir);expect(save.body.source_dataset_dir).toBe(source);scope.project=save.body;scope.context=save.proof.context!;
   const result=await classificationBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationWithin(imported,deadline,'actual import POST'),scope,deadline,w,e,`setup-${index}-import`);expect(result.proof.request_body).toEqual({folder_path:source,task:'classification',validate_images:true});classificationSummary(result.body,scope);proofs.push({scope:scope.tag,source_put:save.proof,import:result.proof});
   // Genuine lazy report/history initialization is completed before immutable maps.
   expect(await api('/api/evaluation/history?source_dataset_path='+encodeURIComponent(source)+'&task=classification',scope)).toEqual({items:[],total:0});
   await view(scope,'prepared');scopes.push(scope);
  }
  expect(new Set(scopes.map(scope=>scope.project.id)).size).toBe(4);expect(new Set(scopes.map(scope=>scope.source)).size).toBe(4);expect(new Set(scopes.flatMap(scope=>scope.metadata!.map(item=>item.image_uuid))).size).toBe(14);
  // Open every saved source once so documented activation migrations finish before the immutable baseline.
  for(const scope of scopes)await open(scope,'prebaseline-open-'+scope.index);
  for(const scope of scopes){before.set(scope.index,classificationRoots(scope));for(const rel of [...scope.entry.files,...scope.entry.ignored])expect(classificationFile(path.join(scope.source,rel)).sha256).toBe(scope.originals[rel]);}
  baseline=true;
  for(const [position,index]of [0,1,0,2,3,2].entries()){await open(scopes[index],'switch-'+position);for(const other of scopes)if(other.index!==index)expect(scopes[index].metadata!.every(item=>!other.metadata!.some(foreign=>foreign.image_uuid===item.image_uuid))).toBe(true);}
  const returning=scopes[2],deadline=performance.now()+10_000,restored=classificationWire(page,origin,'/api/dataset/current-summary','GET',(_r,u)=>u.searchParams.get('folder_path')===returning.source&&u.searchParams.get('task')==='classification');
  await classificationWithin(page.reload(),deadline,'ordinary owning reload');const reply=await classificationBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationWithin(restored,deadline,'reloaded own saved summary'),returning,deadline,w,e,'final-reload-summary');classificationSummary(reply.body,returning);proofs.push(reply.proof);await view(returning,'final-reload');
 }catch(error){primary=error;throw error;}finally{
  const failures:unknown[]=[];
  for(const scope of scopes)if(before.has(scope.index))try{after[scope.tag]=classificationRoots(scope);expect(after[scope.tag]).toEqual(before.get(scope.index));}catch(error){failures.push(error);}
  try{expect(prohibited).toEqual([]);}catch(error){failures.push(error);}try{page.off('request',observe);}catch(error){failures.push(error);}
  try{e.note('classification_import_summary_handoff',{cell:'F019.native-classification-import-label-summary.handoff',source_electron:native,installed_native:false,real_OS_picker:false,controlled_folder_answer:true,default_loader_partitions_preserved:true,statistics_automatic_cohorts_not_split:true,pairs:[[0,1,0],[2,3,2]],proofs,scopes:scopes.map(scope=>({tag:scope.tag,project:scope.project,source:scope.source,expected:scope.entry,originals:scope.originals})),before:Object.fromEntries(before),after,prohibited,all_immutable_scopes_attempted:before.size,secondary_types:failures.map(error=>error instanceof Error?error.name:typeof error),training_and_quality_not_assessed:true});}catch(error){failures.push(error);}
  if(primary!==undefined)classificationSecondary(primary,failures);else if(failures.length){classificationSecondary(failures[0],failures.slice(1));throw failures[0];}
 }
}
test('classification import-label summary follows own saved projects A B A',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const folder:Folder=value=>page.evaluate(source=>{(window as any).api.selectFolder=async()=>source;},value);
 await classificationHandoff(page,workspace,evidence,folder,false,renderer.origin,renderer.url);
});
test('native classification import-label summary follows own saved projects A B A',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window,app}=electronSession,backend=await electronSession.waitForBackend();const folder:Folder=value=>app.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},value);
 await classificationHandoff(window,workspace,evidence,folder,true,`http://127.0.0.1:${backend.port}`);
});


// F022 partition handoff: controlled C/D cohorts, genuine renderer/backend requests.
// The whole reviewed F019 spec above is preserved; this case declares only F022.
// Source Electron is distinct from an installed application or an OS picker.
import type {Response as ClassificationPartitionsResponse, Request as ClassificationPartitionsRequest} from '@playwright/test';
import type {BigIntStats as ClassificationPartitionsStat} from 'node:fs';
type ClassificationPartitionsProject={id:string;name:string;task:string;project_dir:string;dataset_dir:string;models_dir:string;reports_dir:string;annotations_dir:string;active_labelset_id?:string;source_dataset_dir:string|null};
type ClassificationPartitionsScope={index:number;cohort_index:number;tag:string;source:string;project:ClassificationPartitionsProject;entry:typeof cases[number];originals:Record<string,string>;context?:Record<string,unknown>;statistics?:ClassificationPartitionsStatistics;metadata?:ClassificationPartitionsMetadata[]};
type ClassificationPartitionsItem={image_id:string;file_name:string;file_path:string;relative_path:string;labels:string[];label:string;label_status:string;split:string;thumbnail_url:string};
type ClassificationPartitionsStatistics={total:number;labelset_id:string;labeling:Record<string,{count:number;ratio:number}>;assignments:Record<string,{count:number;ratio:number}>;classes:Record<string,{count:number;ratio:number}>;items:ClassificationPartitionsItem[];class_count_grain:string};
type ClassificationPartitionsMetadata={image_uuid:string;file_path:string;relative_path:string;content_hash:string;content_version:number;revision:number;workflow_state:string;usage_state:string;[key:string]:unknown};
type ClassificationPartitionsSnapshot=Record<string,{kind:'file'|'directory';raw7:string[];sha256?:string}>;
const classificationPartitionsRaw=(s:ClassificationPartitionsStat)=>[s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
function classificationPartitionsSecondary(primary:unknown,failures:unknown[]){
 if(primary instanceof Error&&failures.length)try{Object.defineProperty(primary,'classification_partitions_secondary',{value:failures.map(e=>e instanceof Error?e.name:typeof e),configurable:true});}catch{/* Keep the original failure. */}
}
function classificationPartitionsFile(file:string){
 const before=fs.lstatSync(file,{bigint:true});expect(before.isFile()).toBe(true);expect(before.isSymbolicLink()).toBe(false);
 const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let primary:unknown,bytes:Buffer|undefined;const failures:unknown[]=[];
 try{expect(classificationPartitionsRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(classificationPartitionsRaw(before));bytes=fs.readFileSync(fd);expect(BigInt(bytes.length)).toBe(before.size);
  expect(classificationPartitionsRaw(fs.fstatSync(fd,{bigint:true}))).toEqual(classificationPartitionsRaw(before));expect(classificationPartitionsRaw(fs.lstatSync(file,{bigint:true}))).toEqual(classificationPartitionsRaw(before));
 }catch(error){primary=error;}finally{try{fs.closeSync(fd);}catch(error){if(primary===undefined)primary=error;else failures.push(error);}}
 if(primary!==undefined){classificationPartitionsSecondary(primary,failures);throw primary;}return {kind:'file' as const,raw7:classificationPartitionsRaw(before),sha256:sha(bytes!)};
}
function classificationPartitionsTree(root:string):ClassificationPartitionsSnapshot{
 expect(path.isAbsolute(root)).toBe(true);expect(fs.realpathSync(root)).toBe(root);const rows:ClassificationPartitionsSnapshot={};
 const walk=(directory:string)=>{const before=fs.lstatSync(directory,{bigint:true});expect(before.isDirectory()).toBe(true);expect(before.isSymbolicLink()).toBe(false);
  const names=fs.readdirSync(directory).sort();rows[path.relative(root,directory)||'.']={kind:'directory',raw7:classificationPartitionsRaw(before)};
  for(const name of names){const file=path.join(directory,name),info=fs.lstatSync(file,{bigint:true});expect(info.isSymbolicLink()).toBe(false);
   if(info.isDirectory())walk(file);else rows[path.relative(root,file)]=classificationPartitionsFile(file);}
  expect(fs.readdirSync(directory).sort()).toEqual(names);expect(classificationPartitionsRaw(fs.lstatSync(directory,{bigint:true}))).toEqual(classificationPartitionsRaw(before));};walk(root);return rows;
}
function classificationPartitionsRoots(scope:ClassificationPartitionsScope){
 const roots={source:scope.source,annotations:scope.project.annotations_dir,models:scope.project.models_dir,reports:scope.project.reports_dir,dataset:scope.project.dataset_dir};
 for(const [kind,root]of Object.entries(roots))if(kind!=='source'){expect(path.dirname(root)).toBe(scope.project.project_dir);expect(fs.realpathSync(root)).toBe(root);}
 return {trees:Object.fromEntries(Object.entries(roots).map(([kind,root])=>[kind,{root,rows:classificationPartitionsTree(root)}])),manifest:classificationPartitionsFile(path.join(scope.project.project_dir,'project.json'))};
}
async function classificationPartitionsWithin<T>(wire:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original classification 10s frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;return Promise.race([wire,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original classification 10s frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
const classificationPartitionsRemaining=(deadline:number)=>Math.max(1,Math.floor(deadline-performance.now()));
function classificationPartitionsSave(w:Workspace,e:Evidence,label:string,value:unknown){const file=path.join(w.logs,label+'.json');fs.writeFileSync(file,JSON.stringify(value,null,2),{flag:'wx'});e.addFile(file);return {path:file,sha256:sha(fs.readFileSync(file))};}
function classificationPartitionsWire(page:Page,origin:string,route:string,method:string,match?:(r:ClassificationPartitionsRequest,u:URL)=>boolean){
 const wire=page.waitForResponse(response=>{const request=response.request(),u=new URL(response.url());return request.frame()===page.mainFrame()&&u.origin===origin&&u.pathname===route&&request.method()===method&&(!match||match(request,u));},{timeout:10_000});
 void wire.catch(()=>{});return wire;
}
async function classificationPartitionsBody<T>(page:Page,origin:string,response:ClassificationPartitionsResponse,scope:ClassificationPartitionsScope|null,deadline:number,w:Workspace,e:Evidence,label:string){
 const request=response.request(),u=new URL(response.url());expect(request.frame()).toBe(page.mainFrame());expect(u.origin).toBe(origin);expect(response.status()).toBe(200);
 const projectHeader=await classificationPartitionsWithin(request.headerValue('x-vision-project'),deadline,'request project header'),contextHeader=await classificationPartitionsWithin(request.headerValue('x-vision-context'),deadline,'request context header');
 let context:Record<string,unknown>|null=null;if(scope){expect(projectHeader).toBe(scope.project.id);expect(contextHeader).not.toBeNull();context=JSON.parse(contextHeader!);expect(context!.project_id).toBe(scope.project.id);expect(context!.mode).toBe('local');}
 const raw=await classificationPartitionsWithin(response.body(),deadline,'full renderer response body');expect(raw.length).toBeLessThanOrEqual(1024*1024);expect(await classificationPartitionsWithin(response.finished(),deadline,'same response finished')).toBeNull();
 const file=path.join(w.logs,label+'-response.json');fs.writeFileSync(file,raw,{flag:'wx'});e.addFile(file);
 return {body:JSON.parse(raw.toString('utf8')) as T,proof:{method:request.method(),origin,route:u.pathname,query:u.search,request_body:request.postDataJSON(),project_header:projectHeader,context,status:200,actual_main_frame:true,finished:true,response_sha256:sha(raw),response_bytes:raw.length,response_file:file}};
}
function classificationPartitionsSummary(value:{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown},scope:ClassificationPartitionsScope){
 expect(value.total_images).toBe(scope.entry.files.length);expect(value.source_images).toBe(scope.entry.files.length);expect(value.unlabeled_images).toBe(0);expect(value.classes).toEqual(scope.entry.classes);expect(value.split).toEqual(scope.entry.split);
}
function classificationPartitionsStatistics(value:ClassificationPartitionsStatistics,scope:ClassificationPartitionsScope){
 const total=scope.entry.files.length;expect(value.total).toBe(total);expect(value.labelset_id).toBe(scope.project.active_labelset_id||'default');
 expect(value.labeling).toEqual({labeled:{count:total,ratio:1},unlabeled:{count:0,ratio:0}});
 const counts={train:0,val:0,test:0,not_used:0,not_split:0};
 for(const rel of scope.entry.files){const part=rel.split('/')[0];counts[part==='train'||part==='val'||part==='test'?part:'not_split']++;}
 expect(value.assignments).toEqual(Object.fromEntries(Object.entries(counts).map(([key,count])=>[key,{count,ratio:count/total}])));
 expect(value.classes).toEqual(Object.fromEntries(Object.entries(scope.entry.classes).map(([key,count])=>[key,{count,ratio:count/total}])));
 expect(value.items.map(item=>item.file_path).sort()).toEqual(scope.entry.files.map(rel=>path.join(scope.source,rel)).sort());
 for(const item of value.items){const rel=path.relative(scope.source,item.file_path);expect(item.relative_path).toBe(rel);expect(item.file_name).toBe(path.basename(rel));expect(item.image_id).toBe(path.basename(rel,'.png'));
  expect(item.labels).toEqual([path.basename(path.dirname(rel))]);expect(item.label).toBe(path.basename(path.dirname(rel)));expect(item.label_status).toBe('labeled');
  const part=rel.split('/')[0];expect(item.split).toBe(part==='train'||part==='val'||part==='test'?part:'not_split');expect(new URL(item.thumbnail_url,'http://owned.invalid').searchParams.get('file_path')).toBe(item.file_path);}
}
async function classificationPartitionsCards(page:Page,scope:ClassificationPartitionsScope,deadline:number){
 const timeout=()=>({timeout:classificationPartitionsRemaining(deadline)});const entry=scope.entry;
 await classificationPartitionsWithin(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,timeout()),deadline,'own project header');
 await classificationPartitionsWithin(expect(page.getByText('학습 가능 이미지',{exact:true}).locator('..').getByText(String(entry.files.length),{exact:true})).toBeVisible(timeout()),deadline,'eligible images card');
 await classificationPartitionsWithin(expect(page.getByText('적용된 학습 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.train),{exact:true})).toBeVisible(timeout()),deadline,'original applied train card');
 await classificationPartitionsWithin(expect(page.getByText('적용된 검증 / 테스트 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.val+entry.split.test),{exact:true})).toBeVisible(timeout()),deadline,'original applied val/test card');
 for(const [key,count]of Object.entries(entry.split))await classificationPartitionsWithin(expect(page.getByText(key==='train'?'Train:':key==='val'?'Val:':'Test:',{exact:true}).locator('..').getByText(String(count),{exact:true})).toBeVisible(timeout()),deadline,'original default-loader split card');
 const panel=page.getByRole('region',{name:'활성 라벨 세트 데이터 통계',exact:true});await classificationPartitionsWithin(expect(panel).toHaveCount(1,timeout()),deadline,'own statistics region');
 for(const [name,count]of [['전체',entry.files.length],['라벨 완료',entry.files.length],['라벨 미완료',0]] as const){const button=panel.getByRole('button').filter({has:page.getByText(name,{exact:true})});await classificationPartitionsWithin(expect(button).toHaveCount(1,timeout()),deadline,'exact count chip');await classificationPartitionsWithin(expect(button.locator('strong')).toHaveText(String(count),timeout()),deadline,'exact label count');}
 const assignmentCounts={train:0,val:0,test:0,not_used:0,not_split:0};for(const rel of entry.files){const part=rel.split('/')[0];assignmentCounts[part==='train'||part==='val'||part==='test'?part:'not_split']++;}
 for(const [key,label]of [['train','학습'],['val','검증'],['test','시험'],['not_used','미사용'],['not_split','미분할']] as const){const button=panel.getByRole('button').filter({has:page.getByText(label,{exact:true})});await classificationPartitionsWithin(expect(button).toHaveCount(1,timeout()),deadline,'own partition chip');await classificationPartitionsWithin(expect(button.locator('strong')).toHaveText(String(assignmentCounts[key]),timeout()),deadline,'own actual image-grain partition count');}
 const details=panel.locator('details');await classificationPartitionsWithin(expect(details).toHaveCount(1,timeout()),deadline,'class counts details');if(!(await classificationPartitionsWithin(details.evaluate(node=>node.hasAttribute('open')),deadline,'class details state')))await classificationPartitionsWithin(details.locator(':scope > summary').click(),deadline,'ordinary class counts disclosure');
 for(const [label,count]of Object.entries(entry.classes)){const button=details.getByRole('button').filter({has:page.getByText(label,{exact:true})});await classificationPartitionsWithin(expect(button).toHaveCount(1,timeout()),deadline,'own class chip');await classificationPartitionsWithin(expect(button.locator('strong')).toHaveText(String(count),timeout()),deadline,'own class count');}
 const thumbs=page.locator('button[aria-label$=" 라벨링에서 열기"] img');await classificationPartitionsWithin(expect(thumbs).toHaveCount(entry.files.length,timeout()),deadline,'eligible gallery');
 await classificationPartitionsWithin(expect.poll(()=>thumbs.evaluateAll(nodes=>nodes.every(node=>node instanceof HTMLImageElement&&node.complete&&node.naturalWidth>0)),timeout()).toBe(true),deadline,'actual gallery pixels');
 const paths=await classificationPartitionsWithin(thumbs.evaluateAll(nodes=>nodes.map(node=>new URL((node as HTMLImageElement).src).searchParams.get('file_path')).sort()),deadline,'gallery original source paths');expect(paths).toEqual(entry.files.map(rel=>path.join(scope.source,rel)).sort());
}
async function classificationPartitionsHandoff(page:Page,w:Workspace,e:Evidence,folder:Folder,native:boolean,origin:string,url?:string){
 const scopes:ClassificationPartitionsScope[]=[],proofs:unknown[]=[],before=new Map<number,ReturnType<typeof classificationPartitionsRoots>>(),after:Record<string,unknown>={},prohibited:unknown[]=[];let baseline=false,sequence=0,primary:unknown;
 const observe=(r:ClassificationPartitionsRequest)=>{const u=new URL(r.url());if(baseline&&u.origin===origin&&r.method()!=='GET'&&r.method()!=='HEAD'&&!(r.method()==='POST'&&u.pathname==='/api/project/open'))prohibited.push({method:r.method(),route:u.pathname,body:r.postData()});};page.on('request',observe);
 const api=async<T>(route:string,scope:ClassificationPartitionsScope|null=null,body?:unknown,method?:string):Promise<T>=>{const deadline=performance.now()+10_000;
  const result=await classificationPartitionsWithin(page.evaluate(async({origin,route,body,method,project,context,remaining})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);try{const headers:Record<string,string>={'Content-Type':'application/json'};if(project){headers['X-Vision-Project']=project.id;if(context)headers['X-Vision-Context']=JSON.stringify(context);}
   const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers,...(body===undefined?{}:{body:JSON.stringify(body)}),signal:abort.signal});const raw=await response.text();if(raw.length>1024*1024)throw Error('Owned classification API response exceeds 1MiB');return {status:response.status,raw};}finally{clearTimeout(timer);}},
   {origin,route,body,method,project:scope?.project||null,context:scope?.context||null,remaining:classificationPartitionsRemaining(deadline)}),deadline,'owned complete API read');expect(result.status).toBe(200);proofs.push({kind:'controlled owned API',route,method:method||(body===undefined?'GET':'POST'),project_id:scope?.project.id||null,response_sha256:sha(Buffer.from(result.raw)),finished_body:true});return JSON.parse(result.raw) as T;};
 const view=async(scope:ClassificationPartitionsScope,label:string)=>{const deadline=performance.now()+10_000;
  await classificationPartitionsWithin(page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click(),deadline,'ordinary data stage');
  const panel=page.getByRole('region',{name:'활성 라벨 세트 데이터 통계',exact:true}),button=panel.getByRole('button',{name:'데이터 통계 새로고침',exact:true});
  await classificationPartitionsWithin(expect(button).toBeEnabled({timeout:classificationPartitionsRemaining(deadline)}),deadline,'statistics ready');const wire=classificationPartitionsWire(page,origin,'/api/dataset/metadata/statistics','GET');await classificationPartitionsWithin(button.click(),deadline,'ordinary statistics refresh');
  const row=await classificationPartitionsBody<ClassificationPartitionsStatistics>(page,origin,await classificationPartitionsWithin(wire,deadline,'own statistics response'),scope,deadline,w,e,`${label}-${++sequence}`);classificationPartitionsStatistics(row.body,scope);scope.context=row.proof.context!;await classificationPartitionsCards(page,scope,deadline);
  const current=await api<ClassificationPartitionsProject>('/api/project/current',scope);expect(current).toEqual(scope.project);
  const metadata=await api<{items:ClassificationPartitionsMetadata[];total:number}>('/api/dataset/metadata?folder_path='+encodeURIComponent(scope.source)+'&limit=5000',scope);expect(metadata.total).toBe(scope.entry.files.length);
  const ordered=metadata.items.sort((a,b)=>a.file_path.localeCompare(b.file_path));expect(ordered.map(item=>item.file_path).sort()).toEqual(scope.entry.files.map(rel=>path.join(scope.source,rel)).sort());
  for(const item of ordered){const rel=path.relative(scope.source,item.file_path);expect(item.relative_path).toBe(rel);expect(item.content_hash).toBe(scope.originals[rel]);expect(item.content_version).toBe(1);expect(item.revision).toBe(1);expect(item.workflow_state).toBe('unworked');expect(item.usage_state).toBe('active');expect(item.image_uuid).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);}
  if(scope.statistics){expect(row.body).toEqual(scope.statistics);expect(ordered).toEqual(scope.metadata);}else{scope.statistics=row.body;scope.metadata=ordered;}
  proofs.push({scope:scope.tag,statistics:row.proof,image_metadata:ordered});await e.screenshot(page,`${native?'native':'browser'}-classification-partitions-handoff-${label}-${scope.tag}`);
 };
 const open=async(scope:ClassificationPartitionsScope,label:string)=>{const deadline=performance.now()+10_000;
  await classificationPartitionsWithin(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'project manager');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await classificationPartitionsWithin(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'recent projects');
  const wire=classificationPartitionsWire(page,origin,'/api/project/open','POST',r=>r.postDataJSON()?.project_dir===scope.project.project_dir),summary=classificationPartitionsWire(page,origin,'/api/dataset/current-summary','GET',(_r,u)=>u.searchParams.get('folder_path')===scope.source&&u.searchParams.get('task')==='classification');
  const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await classificationPartitionsWithin(expect(item).toHaveCount(1,{timeout:classificationPartitionsRemaining(deadline)}),deadline,'exact owning recent project');await classificationPartitionsWithin(expect(item).toBeEnabled({timeout:classificationPartitionsRemaining(deadline)}),deadline,'own recent project enabled');await classificationPartitionsWithin(item.click(),deadline,'normal owning project open');
  const opened=await classificationPartitionsBody<ClassificationPartitionsProject>(page,origin,await classificationPartitionsWithin(wire,deadline,'own POST open'),null,deadline,w,e,`${label}-open-${++sequence}`);expect(opened.proof.request_body).toEqual({project_dir:scope.project.project_dir});expect(opened.body).toEqual(scope.project);
  const restored=await classificationPartitionsBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationPartitionsWithin(summary,deadline,'own saved summary'),scope,deadline,w,e,`${label}-summary-${++sequence}`);classificationPartitionsSummary(restored.body,scope);
  await classificationPartitionsWithin(expect(dialog).toHaveCount(0,{timeout:classificationPartitionsRemaining(deadline)}),deadline,'project manager closed');proofs.push({scope:scope.tag,open:opened.proof,current_summary:restored.proof});await view(scope,label);
 };
 try{
  if(url)await page.goto(url);else await page.reload();
  for(const [index,cohortIndex]of [2,3].entries()){
   const entry=cases[cohortIndex],tag=index===0?'C':'D';
   const source=path.join(w.root,'classification-partitions-handoff-'+tag);expect(fs.existsSync(source)).toBe(false);const originals:Record<string,string>={};
   for(const [i,rel]of [...entry.files,...entry.ignored].entries()){const file=path.join(source,rel);fs.mkdirSync(path.dirname(file),{recursive:true});const bytes=png(32,3,(x,y)=>[(x+cohortIndex*47+i*7)%256,(y+cohortIndex*31+i*9)%256,50+cohortIndex*30+i]);fs.writeFileSync(file,bytes,{flag:'wx'});originals[rel]=sha(bytes);}
   const created=await api<ClassificationPartitionsProject>('/api/project/create',null,{name:'Classification partition handoff '+tag,task:'classification'});expect(created.id).toMatch(/^[0-9a-f]{8}$/);expect(created.task).toBe('classification');expect(path.dirname(created.project_dir)).toBe(native?path.join(w.userData,'projects'):w.projects);expect(fs.realpathSync(created.project_dir)).toBe(created.project_dir);
   if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(created.name,{timeout:10_000});await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await folder(source);
   const scope:ClassificationPartitionsScope={index,cohort_index:cohortIndex,tag,source,project:created,entry,originals};const deadline=performance.now()+10_000;
   const saved=classificationPartitionsWire(page,origin,'/api/project/update','PUT',r=>r.postDataJSON()?.source_dataset_dir===source),imported=classificationPartitionsWire(page,origin,'/api/dataset/import','POST',r=>r.postDataJSON()?.folder_path===source&&r.postDataJSON()?.task==='classification');
   await classificationPartitionsWithin(page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true}).click(),deadline,'normal controlled folder import');
   const save=await classificationPartitionsBody<ClassificationPartitionsProject>(page,origin,await classificationPartitionsWithin(saved,deadline,'own source PUT'),scope,deadline,w,e,`setup-${index}-source`);expect(save.proof.request_body).toEqual({source_dataset_dir:source});expect(save.body.id).toBe(created.id);expect(save.body.project_dir).toBe(created.project_dir);expect(save.body.source_dataset_dir).toBe(source);scope.project=save.body;scope.context=save.proof.context!;
   const result=await classificationPartitionsBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationPartitionsWithin(imported,deadline,'actual import POST'),scope,deadline,w,e,`setup-${index}-import`);expect(result.proof.request_body).toEqual({folder_path:source,task:'classification',validate_images:true});classificationPartitionsSummary(result.body,scope);proofs.push({scope:scope.tag,source_put:save.proof,import:result.proof});
   // Genuine lazy report/history initialization is completed before immutable maps.
   expect(await api('/api/evaluation/history?source_dataset_path='+encodeURIComponent(source)+'&task=classification',scope)).toEqual({items:[],total:0});
   await view(scope,'prepared');scopes.push(scope);
  }
  expect(scopes.map(scope=>scope.tag)).toEqual(['C','D']);expect(scopes.map(scope=>scope.cohort_index)).toEqual([2,3]);expect(new Set(scopes.map(scope=>scope.project.id)).size).toBe(2);expect(new Set(scopes.map(scope=>scope.source)).size).toBe(2);expect(new Set(scopes.flatMap(scope=>scope.metadata!.map(item=>item.image_uuid))).size).toBe(4);
  // Open every saved source once so documented activation migrations finish before the immutable baseline.
  for(const scope of scopes)await open(scope,'prebaseline-open-'+scope.index);
  for(const scope of scopes){before.set(scope.index,classificationPartitionsRoots(scope));for(const rel of [...scope.entry.files,...scope.entry.ignored])expect(classificationPartitionsFile(path.join(scope.source,rel)).sha256).toBe(scope.originals[rel]);}
  baseline=true;
  for(const [position,index]of [0,1,0].entries()){await open(scopes[index],'switch-'+position);for(const other of scopes)if(other.index!==index)expect(scopes[index].metadata!.every(item=>!other.metadata!.some(foreign=>foreign.image_uuid===item.image_uuid))).toBe(true);}
  const returning=scopes[0],deadline=performance.now()+10_000,restored=classificationPartitionsWire(page,origin,'/api/dataset/current-summary','GET',(_r,u)=>u.searchParams.get('folder_path')===returning.source&&u.searchParams.get('task')==='classification');
  await classificationPartitionsWithin(page.reload(),deadline,'ordinary owning reload');const reply=await classificationPartitionsBody<{total_images:number;source_images:number;unlabeled_images:number;classes:unknown;split:unknown}>(page,origin,await classificationPartitionsWithin(restored,deadline,'reloaded own saved summary'),returning,deadline,w,e,'final-reload-summary');classificationPartitionsSummary(reply.body,returning);proofs.push(reply.proof);await view(returning,'final-reload');
 }catch(error){primary=error;throw error;}finally{
  const failures:unknown[]=[];
  for(const scope of scopes)if(before.has(scope.index))try{after[scope.tag]=classificationPartitionsRoots(scope);expect(after[scope.tag]).toEqual(before.get(scope.index));}catch(error){failures.push(error);}
  try{expect(prohibited).toEqual([]);}catch(error){failures.push(error);}try{page.off('request',observe);}catch(error){failures.push(error);}
  try{e.note('classification_partitions_handoff',{cell:'F022.native-classification-partitions-import-reopen.handoff',F019_credit_changed:false,source_electron:native,installed_native:false,real_OS_picker:false,controlled_folder_answer:true,default_loader_partitions_preserved:true,statistics_automatic_cohorts_not_split:true,pairs:[[0,1,0]],cohort_order:['C','D','C'],proofs,scopes:scopes.map(scope=>({tag:scope.tag,cohort_index:scope.cohort_index,project:scope.project,source:scope.source,expected:scope.entry,originals:scope.originals})),before:Object.fromEntries(before),after,prohibited,all_immutable_scopes_attempted:before.size,secondary_types:failures.map(error=>error instanceof Error?error.name:typeof error),training_and_quality_not_assessed:true});}catch(error){failures.push(error);}
  if(primary!==undefined)classificationPartitionsSecondary(primary,failures);else if(failures.length){classificationPartitionsSecondary(failures[0],failures.slice(1));throw failures[0];}
 }
}
test('native classification Train Val Test cards and imported gallery stay owning through C D C project handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window,app}=electronSession,backend=await electronSession.waitForBackend();const folder:Folder=value=>app.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},value);
 await classificationPartitionsHandoff(window,workspace,evidence,folder,true,`http://127.0.0.1:${backend.port}`);
});

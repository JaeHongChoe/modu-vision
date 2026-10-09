import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page,Request,Response} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<{status:number;body:string;url:string}>;
type Tree=Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>;
const hash=(raw:Buffer)=>crypto.createHash('sha256').update(raw).digest('hex');
const clone=<T,>(value:T):T=>JSON.parse(JSON.stringify(value));
const conflict='다른 작업자가 수정했습니다. 최신 내용을 불러와 다시 검토하세요.';

function tree(root:string):Tree {
 const result:Tree={};const walk=(directory:string)=>{
  const stat=fs.lstatSync(directory);expect(stat.isSymbolicLink()).toBe(false);expect(stat.isDirectory()).toBe(true);
  expect(fs.realpathSync(directory)).toBe(directory);result[path.relative(root,directory).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(directory).sort()){
   const file=path.join(directory,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);
   if(before.isDirectory())walk(file);else{
    expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);
    const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);let raw:Buffer;
    try{const opened=fs.fstatSync(fd);expect([opened.dev,opened.ino,opened.size]).toEqual([before.dev,before.ino,before.size]);raw=fs.readFileSync(fd);
     for(const after of [fs.fstatSync(fd),fs.lstatSync(file)])expect([after.dev,after.ino,after.mode,after.nlink,after.size,after.mtimeMs,after.ctimeMs])
      .toEqual([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]);
    }finally{fs.closeSync(fd);}
    result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:hash(raw!)};
   }
  }
 };walk(root);return result;
}

function leaseChange(previous:any,next:any,actor:string,kind:'acquired'|'released'){
 expect(next.revision).toBe(previous.revision+1);expect(next.audit).toHaveLength(previous.audit.length+1);
 const event=next.audit.at(-1);expect(Object.keys(event).sort()).toEqual(['action','actor','at','changes','id','revision']);
 expect(event.id).toMatch(/^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
 expect(Number.isNaN(Date.parse(event.at))).toBe(false);expect(event.actor).toBe(actor);
 expect(event.action).toBe('edit_lease_'+kind);expect(event.revision).toBe(next.revision);
 expect(event.changes).toEqual(kind==='acquired'?{owner:actor}:{});
 if(kind==='acquired'){
  expect(Object.keys(next.team.edit_lease).sort()).toEqual(['expires_at','owner']);
  expect(next.team.edit_lease.owner).toBe(actor);expect(Number.isFinite(next.team.edit_lease.expires_at)).toBe(true);
 }else expect(next.team.edit_lease).toBeNull();
 const expected=clone(previous);expected.revision=next.revision;expected.audit.push(event);expected.team.edit_lease=next.team.edit_lease;
 expect(next).toEqual(expected);
}

async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const actor='owned-lease-lifecycle-operator',prefix=native?'source-electron':'browser';
 const timings:any[]=[],fixtureCalls:any[]=[],traffic:any[]=[],preobserver:any[]=[],transport:any[]=[],snapshots:any[]=[],cells:any[]=[];
 const requestRows=new Map<Request,any>(),responseRows=new Map<Response,Promise<any>>();const pending=new Set<Promise<any>>();const errors:string[]=[];
 const capturedTokens=new Set<string>();let phase='fixture-setup';
 const redact=(raw:Buffer)=>{
  let value:any;try{value=JSON.parse(raw.toString('utf8'));}catch{return {body_base64:raw.toString('base64'),token_redacted:false};}
  if(value&&typeof value==='object'&&Object.hasOwn(value,'lease_token')){
   expect(typeof value.lease_token).toBe('string');expect(value.lease_token).toMatch(/^[A-Za-z0-9_-]{43}$/);
   const token=value.lease_token;capturedTokens.add(token);e.redact(token);
   return{body_base64:Buffer.from(JSON.stringify({...value,lease_token:'[REDACTED-OWNED-LEASE]'})).toString('base64'),token_redacted:true,lease_token_sha256:hash(Buffer.from(token))};
  }
  return{body_base64:raw.toString('base64'),token_redacted:false};
 };
 const observeRequest=(request:Request)=>{
  if(!new URL(request.url()).pathname.startsWith('/api/'))return;
  const started=performance.now(),raw=request.postDataBuffer();
  const row={index:traffic.length,phase,method:request.method(),url:request.url(),started,deadline:started+10_000,
   request_body_sha256:raw===null?null:hash(raw),request_body_bytes:raw?.length??0,...(raw===null?{request_body_base64:null}:{request_body_base64:redact(raw).body_base64})};
  requestRows.set(request,row);traffic.push(row);
  if(request.method()==='OPTIONS')transport.push(row);
 };
 const observeResponse=(response:Response)=>{
  if(!new URL(response.url()).pathname.startsWith('/api/'))return;
  const request=response.request(),row=requestRows.get(request);
  const owned:any=row||{phase:'preobserver-response',method:request.method(),url:response.url(),status:response.status(),
   response_observed:performance.now(),request_start_not_captured:true,full_request_budget_claimed:false};
  if(!row){expect(request.method()).toBe('GET');owned.deadline=owned.response_observed+10_000;preobserver.push(owned);}
  const task=(async()=>{
   // Eager custody uses the original request deadline, or a separately disclosed
   // response-event budget for genuinely pre-observer GETs. No start is invented.
   let timer:NodeJS.Timeout|undefined;try{
    const [raw,finished]=await Promise.race([Promise.all([response.body(),response.finished()]),new Promise<never>((_,reject)=>{
     timer=setTimeout(()=>reject(Error('Owned API body/finished absolute deadline expired')),Math.max(0,owned.deadline-performance.now()));})]);
    const ended=performance.now();expect(ended).toBeLessThanOrEqual(owned.deadline);expect(finished).toBeNull();
    Object.assign(owned,{status:response.status(),response_url:response.url(),body_finished:ended,response_finished:finished,
     original_body_sha256:hash(raw),original_body_bytes:raw.length,...redact(raw)});return{response,row:owned,raw};
   }finally{clearTimeout(timer);}
  })();responseRows.set(response,task);pending.add(task);void task.catch(cause=>errors.push(String(cause))).finally(()=>pending.delete(task));
 };
 page.on('request',observeRequest);page.on('response',observeResponse);
 const frame=async<T,>(label:string,run:(start:number)=>Promise<T>)=>{
  const started=performance.now(),deadline=started+10_000;let timer:NodeJS.Timeout|undefined;
  try{const value=await Promise.race([run(started),new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Owned '+label+' absolute action deadline expired')),Math.max(0,deadline-performance.now()));})]);
   const finished=performance.now();expect(finished).toBeLessThanOrEqual(deadline);timings.push({label,started,deadline,finished});return value;
  }finally{clearTimeout(timer);}
 };
 const call=async(route:string,body?:unknown,method?:string)=>frame('fixture '+route,async started=>{
  const reply=await api(route,body,method);expect(reply.status,route).toBe(200);expect(new URL(reply.url).pathname+new URL(reply.url).search).toBe(route);
  const requestRaw=body===undefined?null:Buffer.from(JSON.stringify(body)),responseRaw=Buffer.from(reply.body);
  fixtureCalls.push({phase,route,started,deadline:started+10_000,finished:performance.now(),method:method||(body===undefined?'GET':'POST'),status:reply.status,url:reply.url,
   request_body_sha256:requestRaw===null?null:hash(requestRaw),request_body_base64:requestRaw===null?null:redact(requestRaw).body_base64,
   original_body_sha256:hash(responseRaw),original_body_bytes:responseRaw.length,...redact(responseRaw)});
  return JSON.parse(reply.body);
 });
 const settled=async()=>{
  while(pending.size)await Promise.all([...pending]);expect(errors).toEqual([]);
 };
 const setup=async(tag:'A'|'B')=>{
  const source=path.join(w.root,'lease-lifecycle-source-'+tag);fs.mkdirSync(source);
  const image=path.join(source,'part-'+tag+'.png');fs.writeFileSync(image,png(256,3,(x,y)=>[x,y,tag==='A'?100:101]),{flag:'wx'});
  const project=await call('/api/project/create',{name:'Owned lease lifecycle '+tag,task:'segmentation'});
  await call('/api/project/update',{source_dataset_dir:source},'PUT');await call('/api/dataset/import',{folder_path:source,task:'segmentation'});
  await call('/api/team-data');await call('/api/team-data/readiness');
  await call('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Original controlled book '+tag,categories:[
   {id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b'}]});
  const saved=await call('/api/annotations/save',{image_id:'part-'+tag,image_path:image,image_width:256,image_height:256,actor,
   annotations:[{id:'original-'+tag,type:'bbox',label:'Scratch',category_id:2,color:'#f59e0b',bbox:[2,3,20,21]}]});
  const team=await call('/api/team-data');await call('/api/team-data/settings',{expected_revision:team.settings.revision,actor:'fixture-owner',changes:{editing_enabled:true}},'PUT');
  const current=await call('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
  const original=await call('/api/annotations/part-'+tag+'?file_path='+encodeURIComponent(image));
  await call('/api/project/labelsets');await call('/api/dataset/metadata?limit=100');await call('/api/team-data/queue?offset=0&limit=30');
  await call('/api/dataset/versions');await call('/api/training-workspace/tasks');
  await call('/api/dataset/images?folder_path='+encodeURIComponent(source)+'&task=segmentation&offset=0&limit=120');
  return{tag,project:current,source,image,image_sha256:hash(fs.readFileSync(image)),uuid:saved.metadata.image_uuid,
   query:'/api/annotations/part-'+tag+'?file_path='+encodeURIComponent(image),route:'/api/team-data/images/'+saved.metadata.image_uuid,
   original_annotations:original.annotations,row:(await call('/api/dataset/metadata/image?image_path='+encodeURIComponent(image)))};
 };
 let A:Awaited<ReturnType<typeof setup>>,B:Awaited<ReturnType<typeof setup>>;
 const leases=new Map<string,{scope:Awaited<ReturnType<typeof setup>>;token:string}>();
 try{
  A=await setup('A');B=await setup('B');expect(A.uuid).not.toBe(B.uuid);expect(A.project.id).not.toBe(B.project.id);
  const ownedRoot=native?path.join(w.userData,'projects'):w.projects;
  for(const scope of [A,B])expect(path.dirname(scope.project.project_dir)).toBe(ownedRoot);
  await call('/api/project/open',{project_dir:A.project.project_dir});
  const setupCalls=fixtureCalls.slice(),setupWriteCalls=setupCalls.filter(row=>!['GET','HEAD','OPTIONS'].includes(row.method));
  expect(setupWriteCalls).toHaveLength(13);await settled();
  await call('/api/project/current');const origin=new URL(fixtureCalls.at(-1).url).origin;
  const stages=page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button');
  const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
  const enter=async(scope:typeof A)=>{
   await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);await stages.nth(1).click();
   const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')!=='true')await focus.click();
   await expect(page.getByText('1 / 1',{exact:true})).toBeVisible();await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
   await expect(page.getByTestId('annotation-save-button')).not.toContainText('Save Changes');
   await expect(page.getByText('기존 라벨을 불러오는 중입니다.',{exact:true})).toHaveCount(0);
   await expect(page.getByRole('alert').filter({hasText:'기존 라벨 조회 실패'})).toHaveCount(0);
   await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(dialog).toBeVisible();
   await expect(dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true}).getByText(path.basename(scope.image),{exact:true})).toBeVisible();
   await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(dialog).toHaveCount(0);
  };
  const actorInput=async()=>{
   await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(dialog).toBeVisible();
   await dialog.getByLabel('팀 작업자 이름',{exact:true}).fill(actor);await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
   await expect(dialog).toHaveCount(0);
  };
  phase='initial-readonly-view';if(url)await page.goto(url);else await page.reload();await enter(A);await actorInput();await settled();
  const uiStart=traffic.length,callStart=fixtureCalls.length;
  const observedSetup=traffic.slice(0,uiStart).filter(row=>!['GET','HEAD','OPTIONS'].includes(row.method));
  expect(observedSetup.map(row=>[row.method,new URL(row.url).pathname,row.request_body_base64])).toEqual(native
   ?setupWriteCalls.map(row=>[row.method,row.route,row.request_body_base64]):[]);
  const storage=()=>page.evaluate(()=>Object.fromEntries(Object.entries(localStorage).sort(([a],[b])=>a.localeCompare(b))));
  const roots={project_A:A.project.project_dir,project_B:B.project.project_dir,source_A:A.source,source_B:B.source,harness_dataset:w.dataset};
  const routes=(scope:typeof A)=>({project:'/api/project/current',labelsets:'/api/project/labelsets',annotations:scope.query,
   metadata:'/api/dataset/metadata/image?image_path='+encodeURIComponent(scope.image),metadata_list:'/api/dataset/metadata?limit=100',team_image:scope.route,
   team:'/api/team-data',queue:'/api/team-data/queue?offset=0&limit=30',readiness:'/api/team-data/readiness',versions:'/api/dataset/versions',
   images:'/api/dataset/images?folder_path='+encodeURIComponent(scope.source)+'&task=segmentation&offset=0&limit=120',tasks:'/api/training-workspace/tasks'});
  const expectedRows={A:clone(A.row),B:clone(B.row)};
  const baselineTrees=Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,tree(root)]));
  const originalLedgers:Record<string,any>={},workflows:Record<string,string>={},rowKeys:Record<string,string>={};
  for(const scope of [A,B]){
   const files=Object.keys(baselineTrees['project_'+scope.tag]).filter(name=>name.endsWith('/metadata/workflow.json'));expect(files).toHaveLength(1);
   workflows[scope.tag]=files[0];const ledger=JSON.parse(fs.readFileSync(path.join(scope.project.project_dir,files[0]),'utf8'));
   originalLedgers[scope.tag]=clone(ledger);const keys=Object.keys(ledger.images).filter(key=>ledger.images[key].image_uuid===scope.uuid);expect(keys).toHaveLength(1);rowKeys[scope.tag]=keys[0];
   expect(ledger.images[keys[0]].team.edit_lease).toBeNull();
  }
  const expectedLedgers=clone(originalLedgers);
  const snapshot=async(label:string,scope:typeof A)=>{
   const values:Record<string,unknown>={},start=fixtureCalls.length;
   for(const[key,route]of Object.entries(routes(scope)))values[key]=await call(route);
   expect(values.project).toEqual(scope.project);expect((values.annotations as any).annotations).toEqual(scope.original_annotations);
   expect((values.annotations as any).metadata).toEqual(expectedRows[scope.tag]);expect(values.metadata).toEqual(expectedRows[scope.tag]);
   expect(values.team_image).toEqual({image:expectedRows[scope.tag]});expect((values.metadata_list as any).items).toEqual([expectedRows[scope.tag]]);
   expect((values.queue as any).items).toEqual([expectedRows[scope.tag]]);expect((values.tasks as any).tasks).toEqual([]);expect((values.versions as any).versions).toEqual([]);
   const trees=Object.fromEntries(Object.entries(roots).map(([key,root])=>[key,tree(root)]));const expected=clone(baselineTrees);
   for(const selected of [A,B]){
    const relative=workflows[selected.tag],file=path.join(selected.project.project_dir,relative);
    expect(JSON.parse(fs.readFileSync(file,'utf8'))).toEqual(expectedLedgers[selected.tag]);expected['project_'+selected.tag][relative]=trees['project_'+selected.tag][relative];
    expect(hash(fs.readFileSync(selected.image))).toBe(selected.image_sha256);
   }
   expect(trees).toEqual(expected);const stored=await frame('readonly local storage '+label,async()=>storage());
   expect(Object.keys(stored).filter(key=>key.startsWith('modu-annotation-draft:v1:'))).toEqual([]);
   for(const value of Object.values(stored))for(const token of capturedTokens)expect(value.includes(token)).toBe(false);
   const folder=path.join(w.logs,'lease-lifecycle-'+label);fs.mkdirSync(folder);
   for(const[key,root]of Object.entries(roots)){fs.cpSync(root,path.join(folder,key),{recursive:true,errorOnExist:true});expect(tree(path.join(folder,key))).toEqual(trees[key]);
    for(const[relative,item]of Object.entries(trees[key]))if(item.kind==='file')e.addFile(path.join(folder,key,relative));}
   const value={label,scope:scope.tag,roots,values,api_raw:fixtureCalls.slice(start),trees,storage:stored};
   const file=path.join(folder,'snapshot.json');fs.writeFileSync(file,JSON.stringify(value),{flag:'wx'});e.addFile(file);snapshots.push(value);return value;
  };
  const originalViews:Record<string,any>={};originalViews.A=await snapshot('before',A);
  const sameReadValues=(actual:any,baseline:any)=>{
   for(const key of ['project','labelsets','team','readiness','versions','images','tasks'])expect(actual.values[key]).toEqual(baseline.values[key]);
   const before=baseline.values.annotations,next=actual.values.annotations;expect({...next,metadata:before.metadata}).toEqual(before);
   };
  const waitOwn=(started:number,method:string,route:string,body:unknown)=>page.waitForResponse(response=>{
   const row=requestRows.get(response.request());return row&&row.started>=started&&response.request().frame()===page.mainFrame()
    &&response.request().method()===method&&response.url()===origin+route&&JSON.stringify(response.request().postDataJSON())===JSON.stringify(body);
  },{timeout:10_000});
  const captured=async(response:Response)=>{const task=responseRows.get(response);expect(task).toBeDefined();return task!;};
  const applyLease=(scope:typeof A,next:any,kind:'acquired'|'released',token?:string)=>{
   leaseChange(expectedRows[scope.tag],next,actor,kind);expectedRows[scope.tag]=clone(next);
   const raw=clone(expectedLedgers[scope.tag].images[rowKeys[scope.tag]]);raw.revision=next.revision;raw.audit=clone(next.audit);
   raw.team.edit_lease=kind==='acquired'?{...next.team.edit_lease,token_hash:hash(Buffer.from(token!))}:null;
   expectedLedgers[scope.tag].images[rowKeys[scope.tag]]=raw;
  };
  const acquire=async(scope:typeof A)=>frame('own '+scope.tag+' acquire',async started=>{
   const body={expected_revision:expectedRows[scope.tag].revision,actor,ttl_seconds:120},waiting=waitOwn(started,'POST',scope.route+'/lease/acquire',body),clock=Date.now()/1000;
   await page.getByRole('button',{name:'편집 시작',exact:true}).click();const wire=await captured(await waiting);expect(wire.row.status).toBe(200);
   const value=JSON.parse(wire.raw.toString('utf8'));expect(Object.keys(value).sort()).toEqual(['image','lease_token']);
   expect(typeof value.lease_token).toBe('string');expect(value.lease_token).toMatch(/^[A-Za-z0-9_-]{43}$/);e.redact(value.lease_token);
   expect(value.image.team.edit_lease.expires_at).toBeGreaterThanOrEqual(clock+120);expect(value.image.team.edit_lease.expires_at).toBeLessThanOrEqual(Date.now()/1000+120);
   applyLease(scope,value.image,'acquired',value.lease_token);leases.set(scope.tag,{scope,token:value.lease_token});
   await expect(page.getByText('현재 이미지 편집 중',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'편집 종료',exact:true})).toBeEnabled();return value;
  });
  const release=async(scope:typeof A)=>frame('own '+scope.tag+' release',async started=>{
   const token=leases.get(scope.tag)!.token,body={expected_revision:expectedRows[scope.tag].revision,actor,lease_token:token};
   const waiting=waitOwn(started,'POST',scope.route+'/lease/release',body);await page.getByRole('button',{name:'편집 종료',exact:true}).click();
   const wire=await captured(await waiting);expect(wire.row.status).toBe(200);const value=JSON.parse(wire.raw.toString('utf8'));expect(Object.keys(value)).toEqual(['image']);
   applyLease(scope,value.image,'released');leases.delete(scope.tag);await expect(page.getByText('편집 시작 필요',{exact:true})).toBeVisible();
   await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();return wire.row;
  });
  const cleanupRelease=async(scope:typeof A)=>{
   phase='declared-owned-fixture-release-'+scope.tag;const lease=leases.get(scope.tag);expect(lease).toBeDefined();
   const body={expected_revision:expectedRows[scope.tag].revision,actor,lease_token:lease!.token};const value=await call(scope.route+'/lease/release',body);
   expect(Object.keys(value)).toEqual(['image']);applyLease(scope,value.image,'released');leases.delete(scope.tag);
  };
  const refresh=async()=>frame('same image refresh',async started=>{
   await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(dialog).toBeVisible();
   const waiting=page.waitForResponse(response=>{const row=requestRows.get(response.request());return row&&row.started>=started&&response.request().method()==='GET'&&response.url()===origin+A.route;},{timeout:10_000});
   await dialog.getByRole('button',{name:'새로고침',exact:true}).click();const wire=await captured(await waiting);expect(wire.row.status).toBe(200);expect(JSON.parse(wire.raw.toString('utf8'))).toEqual({image:expectedRows.A});
   await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();
  });

  phase='cancel';await frame('cancel clean acquired edit',async()=>{
   await acquire(A);await release(A);await expect(page.getByTitle('Delete annotation',{exact:true})).toHaveCount(1);
   await expect(page.getByTestId('annotation-save-button')).not.toContainText('Save Changes');
  });
  const cancelled=await snapshot('cancel-clean-release',A);sameReadValues(cancelled,originalViews.A);expect(cancelled.storage).toEqual(originalViews.A.storage);
  await e.screenshot(page,prefix+'-clean-edit-explicitly-ended-without-save');
  cells.push({action:'edit-start',dimension:'cancel',cancel_kind:'explicit clean edit lease release before any annotation modification/save',request_cancelled:false,
   acquire_200:true,owned_release_200:true,labels_and_policy_unchanged:true,snapshot:cancelled.label});

  phase='reopen';await acquire(A);const liveBefore=await snapshot('reopen-live-before',A);
  await frame('reload same image refuses lease takeover',async started=>{
   await settled();await page.reload();await enter(A);await expect(page.getByText('편집 시작 필요',{exact:true})).toBeVisible();
   await expect(page.getByRole('button',{name:'편집 종료',exact:true})).toHaveCount(0);
   const body={expected_revision:expectedRows.A.revision,actor,ttl_seconds:120},waiting=waitOwn(started,'POST',A.route+'/lease/acquire',body);
   await page.getByRole('button',{name:'편집 시작',exact:true}).click();const wire=await captured(await waiting);expect(wire.row.status).toBe(409);
   expect(JSON.parse(wire.raw.toString('utf8'))).toEqual({detail:{message:conflict,current:expectedRows.A}});
   await expect(page.getByRole('alert').filter({hasText:conflict})).toContainText(conflict);
   await expect(page.getByRole('button',{name:'편집 시작',exact:true})).toBeEnabled();
  });
  const reopened=await snapshot('reopen-live-no-takeover',A);expect(reopened.values).toEqual(liveBefore.values);expect(reopened.trees).toEqual(liveBefore.trees);
  expect(reopened.storage).toEqual(liveBefore.storage);await e.screenshot(page,prefix+'-reload-preserves-server-lease-without-client-authority');
  cells.push({action:'edit-start',dimension:'reopen',actual_reload:true,same_project_image_uuid:true,client_release_absent:true,live_server_lease_unchanged:true,
   same_actor_acquire_409:true,automatic_takeover:false,snapshot:reopened.label});
  await cleanupRelease(A);await refresh();const afterReopenCleanup=await snapshot('reopen-owned-fixture-release',A);
  sameReadValues(afterReopenCleanup,originalViews.A);expect(afterReopenCleanup.storage).toEqual(originalViews.A.storage);

  const selectProject=async(scope:typeof A)=>frame('real project switch '+scope.tag,async started=>{
   await page.getByTitle('프로젝트 관리',{exact:true}).click();const projects=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
   await projects.getByRole('button',{name:'최근 프로젝트',exact:true}).click();
   const body={project_dir:scope.project.project_dir},selected=waitOwn(started,'POST','/api/project/open',body);
   const summary=page.waitForResponse(response=>{const row=requestRows.get(response.request()),address=new URL(response.url());return row&&row.started>=started
    &&response.request().method()==='GET'&&address.origin===origin&&address.pathname==='/api/dataset/current-summary'
    &&address.searchParams.get('folder_path')===scope.source&&address.searchParams.get('task')==='segmentation';},{timeout:10_000});
   await projects.getByRole('button').filter({hasText:scope.project.name}).click();const selectedWire=await captured(await selected);expect(selectedWire.row.status).toBe(200);
   expect(JSON.parse(selectedWire.raw.toString('utf8'))).toEqual(scope.project);const restored=await captured(await summary);expect(restored.row.status).toBe(200);
   expect(JSON.parse(restored.raw.toString('utf8')).total_images).toBe(1);await expect(projects).toHaveCount(0);await enter(scope);
   await expect(page.getByText('편집 시작 필요',{exact:true})).toBeVisible();await expect(page.getByRole('button',{name:'편집 종료',exact:true})).toHaveCount(0);
  });
  phase='handoff';await acquire(A);const handoffA=await snapshot('handoff-A-live-before',A);
  await selectProject(B);originalViews.B=await snapshot('handoff-B-before-acquire',B);
  expect(originalViews.B.values.metadata).toEqual(B.row);await acquire(B);await release(B);
  const handoffB=await snapshot('handoff-B-clean-release',B);sameReadValues(handoffB,originalViews.B);expect(handoffB.storage).toEqual(originalViews.B.storage);
  await e.screenshot(page,prefix+'-project-B-exact-lease-isolated-from-project-A');
  await selectProject(A);const handoffReturn=await snapshot('handoff-A-return-live-no-client-token',A);
  expect(handoffReturn.values).toEqual(handoffA.values);
  await e.screenshot(page,prefix+'-project-A-return-preserves-own-live-server-lease');
  cells.push({action:'edit-start',dimension:'handoff',real_recent_project_GUI_switch:true,projects:[A.project.id,B.project.id,A.project.id],
   exact_sources:[A.source,B.source,A.source],exact_image_uuids:[A.uuid,B.uuid,A.uuid],A_live_server_lease_retained:true,B_owned_acquire_release_200:true,
   client_release_absent_on_return:true,automatic_cross_project_lease_mutation:false,snapshots:[handoffA.label,handoffB.label,handoffReturn.label]});
  await cleanupRelease(A);await refresh();const final=await snapshot('final-all-owned-leases-released',A);sameReadValues(final,originalViews.A);
  expect(leases.size).toBe(0);expect(expectedRows.A.team.edit_lease).toBeNull();expect(expectedRows.B.team.edit_lease).toBeNull();
  const beforeStorage=originalViews.A.storage as Record<string,string>,afterStorage=final.storage as Record<string,string>;
  const newKeys=Object.keys(afterStorage).filter(key=>!Object.hasOwn(beforeStorage,key));expect(newKeys).toHaveLength(1);
  expect(newKeys[0].startsWith('vision-project-view:')).toBe(true);const view=JSON.parse(newKeys[0].slice('vision-project-view:'.length));
  expect(view.slice(1)).toEqual([B.project.id,B.project.project_dir,B.source,B.project.active_labelset_id||'default','segmentation']);
  const originalAKey=Object.keys(beforeStorage).find(key=>{if(!key.startsWith('vision-project-view:'))return false;const tuple=JSON.parse(key.slice('vision-project-view:'.length));return tuple[1]===A.project.id;});
  expect(originalAKey).toBeDefined();expect(view[0]).toBe(JSON.parse(originalAKey!.slice('vision-project-view:'.length))[0]);
  expect(afterStorage[newKeys[0]]).toBe('2');const expectedStorage={...beforeStorage,[newKeys[0]]:'2'};expect(afterStorage).toEqual(expectedStorage);
  await settled();const allWrites=traffic.slice(uiStart).filter(row=>!['GET','HEAD','OPTIONS'].includes(row.method));
  const expectedUI=[['POST',A.route+'/lease/acquire'],['POST',A.route+'/lease/release'],['POST',A.route+'/lease/acquire'],['POST',A.route+'/lease/acquire'],
   ...(native?[['POST',A.route+'/lease/release']]:[]),['POST',A.route+'/lease/acquire'],['POST','/api/project/open'],
   ['POST',B.route+'/lease/acquire'],['POST',B.route+'/lease/release'],['POST','/api/project/open'],...(native?[['POST',A.route+'/lease/release']]:[])];
  expect(allWrites.map(row=>[row.method,new URL(row.url).pathname])).toEqual(expectedUI);
  const cleanups=fixtureCalls.slice(callStart).filter(row=>row.method!=='GET');expect(cleanups).toHaveLength(2);
  expect(cleanups.map(row=>[row.method,row.route])).toEqual([['POST',A.route+'/lease/release'],['POST',A.route+'/lease/release']]);
  expect(allWrites.map(row=>row.status)).toEqual(native?[200,200,200,409,200,200,200,200,200,200,200]:[200,200,200,409,200,200,200,200,200]);
  for(const row of traffic.filter(row=>row.method!=='OPTIONS'))if(row.phase!=='fixture-setup')expect(row.status).toBeDefined();
  for(const row of traffic)expect(row.response_finished).toBeNull();
  for(const row of preobserver)expect(row.method).toBe('GET');
  const proof={schema_version:1,record_id:'F024',actions:{'edit-start':['reopen','cancel','handoff']},scope:{source_ui:true,source_electron:native,
   installed_target:false,actual_model_inference:false,training:false,gpu:false,human_review_or_quality_accepted:false,parent_acceptance:false,
   cancel_is_clean_owned_release_not_inflight_request_cancel:true,reopen_preserves_live_lock_not_automatic_authority:true,
   preobserver_GET_response_event_budget_only:true,full_request_budget_not_claimed_for_preobserver:true,lease_tokens_redacted:true},projects:[A.project,B.project],images:[A,B].map(scope=>({tag:scope.tag,path:scope.image,sha256:scope.image_sha256,image_uuid:scope.uuid})),
   cells,roots,snapshots,expected_rows:expectedRows,setup_calls:setupCalls,setup_write_count:13,renderer_setup_writes:traffic.slice(0,uiStart).filter(row=>!['GET','HEAD','OPTIONS'].includes(row.method)),
   all_renderer_traffic:traffic,OPTIONS_transport:transport,preobserver_responses:preobserver,ui_writes:allWrites,fixture_calls:fixtureCalls,
   owned_fixture_releases:cleanups,timings,original_API_budget_ms:10_000,case_budget_ms:240_000,remaining_owned_leases:0};
  const file=path.join(w.logs,'team-edit-lease-lifecycle-proof.json');fs.writeFileSync(file,JSON.stringify(proof),{flag:'wx'});e.addFile(file);
  for(const scope of [A,B])e.addFile(scope.image);e.note('team_edit_lease_lifecycle',{proof_path:file,proof_sha256:hash(fs.readFileSync(file)),proof_size:fs.statSync(file).size,
   actions:proof.actions,source_application_ui:true,quality_accepted:false});
 }finally{
  // Never recover a failure by releasing a guessed lease, resetting state, or
  // swallowing an assertion. The owned fixture teardown retains failed runs.
  page.off('request',observeRequest);page.off('response',observeResponse);
 }
}

test('edit lease clean cancellation reload refusal and real project handoff preserve exact originals',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(240_000);await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),data:body});
  return{status:response.status(),body:await response.text(),url:response.url()};};
 await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native edit lease cancellation reload refusal and project handoff retain original authority',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 test.setTimeout(240_000);const page=electronSession.window,status=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,
  {method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});
  return{status:response.status,body:await response.text(),url:response.url};},{port:status.port,route,body,method});
 await exercise(page,workspace,evidence,api,true);
});

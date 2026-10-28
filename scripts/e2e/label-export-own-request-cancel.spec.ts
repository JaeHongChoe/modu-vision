import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Response,Download} from '@playwright/test';
import {test as original,expect,type Workspace,type Evidence,type RendererServer} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const proxy=require('./fixtures/owning-export-proxy.cjs');
// Load only the frozen, previously reviewed passive original-event observer.
const ownedFailedHTTPHelperPath=path.join(__dirname,'fixtures','owned-failed-http-observer.cjs');
if(createHash('sha256').update(fs.readFileSync(ownedFailedHTTPHelperPath)).digest('hex')!=='08f1555a2e6a1d70784f77025969281082c4b7d4586e8083c53e061bde6ff4e0')throw new Error('Original passive failed-HTTP helper custody changed');
const {observeOwnedFailedHTTP}=require(ownedFailedHTTPHelperPath);
const endpoint='/api/dataset/formats/export';
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
function tree(root:string):Record<string,string>{
 const result:Record<string,string>={};
 const walk=(dir:string)=>{for(const name of fs.readdirSync(dir).sort()){
  const file=path.join(dir,name),stat=fs.lstatSync(file);expect(stat.isSymbolicLink()).toBe(false);
  if(stat.isDirectory())walk(file);else{expect(stat.isFile()).toBe(true);result[path.relative(root,file)]=sha(file);}
 }};
 if(fs.existsSync(root))walk(root);return result;
}

function readBundle(file:string):Record<string,{sha256:string;bytes:number;text:string}>{
 // Read only this generated small archive, never extract it or follow members.
 const program=`import hashlib,json,pathlib,stat,sys,zipfile
p=pathlib.Path(sys.argv[1]);assert p.stat().st_size<=1048576
with zipfile.ZipFile(p) as z:
 rows=z.infolist();assert len(rows)<=20;assert sum(r.file_size for r in rows)<=4194304
 assert len({r.filename for r in rows})==len(rows)
 result={}
 for r in rows:
  name=pathlib.PurePosixPath(r.filename)
  assert not name.is_absolute() and all(v not in ('','.', '..') for v in name.parts) and '\\\\' not in r.filename
  assert not stat.S_ISLNK(r.external_attr>>16) and not r.is_dir() and r.file_size<=1048576
  raw=z.read(r);assert len(raw)==r.file_size
  result[r.filename]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'text':raw.decode('utf-8')}
 print(json.dumps(result,sort_keys=True))`;
 return JSON.parse(execFileSync(process.env.MV_E2E_PYTHON||'python3',['-c',program,file],{encoding:'utf8',timeout:10_000}));
}

type Gate={captured:Promise<any>;snapshot:()=>any;release:()=>any};
type ControlledRenderer=RendererServer&{control:{arm:(owner:{key:string;project:string;context:string;format:string})=>Gate;snapshots:()=>any[];assertSettled:()=>void};close:()=>Promise<void>};
type Scope={project:any;active:any;source:string;images:Workspace['images'];baseline:any};
const test=original.extend<{renderer:ControlledRenderer}>({renderer:async({rendererBuild,backend,evidence,loopbackGuard,page},use)=>{
 const server:ControlledRenderer=await proxy.startRendererServer({staticDir:rendererBuild.outDir,backend});loopbackGuard.allow(server.port);
 evidence.note('owning_export_proxy',{origin:server.origin,static_source:rendererBuild,original_guards_retained:true,real_HTTP_forwarding:true});
 // Attach before use(server), hence before the test's first page.goto.
 // page.request setup traffic is not relabeled as an original main-frame request.
 const failedHTTP=observeOwnedFailedHTTP(page,{isAllowedPort:(port:number)=>loopbackGuard.isAllowed(port)});
 let primary:unknown;
 try{await use(server);server.control.assertSettled();}catch(error){primary=error;throw error;}finally{
  let diagnosticFailure:unknown;
  try{
   // Existing fixture teardown has not closed the original browser context.
   // This snapshot never awaits pending bodies or introduces a new close/deadline.
   const snapshot=await failedHTTP.finishAfterOriginalContextClose(false);
   evidence.note('owned_failed_http_observation',snapshot);
   evidence.note('owning_export_failed_http_diagnostic_scope',{historical_Root02_failed:true,historical_Root02_URL_reconstructed:false,original_context_close_not_observed_here:true,original_harness_closure_independently_required:true,console_zero_guard_unchanged:true});
   if(snapshot.pending_count!==0||snapshot.observer_errors.length!==0||snapshot.failed_responses.some((row:any)=>row.full_original_body!==true||row.finished!==true))diagnosticFailure=new Error('Original failed-HTTP capture remains incomplete; no diagnostic acceptance');
  }catch(error){diagnosticFailure=error;}
  if(diagnosticFailure!==undefined)evidence.note('owning_export_failed_http_diagnostic_failure',String(diagnosticFailure));
  evidence.note('owning_export_proxy_final',{gates:server.control.snapshots(),requests:server.stats()});
  try{await server.close();}catch(error){if(primary===undefined)throw error;evidence.note('owning_export_proxy_secondary_close',String(error));}
  if(diagnosticFailure!==undefined){
   if(primary===undefined)throw diagnosticFailure;
   evidence.note('owning_export_failed_http_diagnostic_secondary_preserved',String(diagnosticFailure));
  }
 }
}});
test.use({actionTimeout:10_000});
async function within<T>(promise:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw new Error(label+' original ten-second deadline expired');
 let timer:ReturnType<typeof setTimeout>|undefined;
 try{return await Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(new Error(label+' original ten-second deadline expired')),remaining);})]);}finally{if(timer!==undefined)clearTimeout(timer);}
}
const isExport=(request:Request,origin:string)=>request.method()==='POST'&&new URL(request.url()).origin===origin&&new URL(request.url()).pathname===endpoint;

test('real owning export Abort cancels download adoption then recovers without deleting completed server bundles',async({page,renderer,workspace,evidence})=>{
 const exports:Request[]=[],failed:Request[]=[],retiredThumbnails:Request[]=[],responses:Response[]=[],downloads:Download[]=[],writes:any[]=[],errors:string[]=[],contexts=new Map<string,string>();let recording=false;
 const onRequest=(request:Request)=>{
  if(request.frame()!==page.mainFrame()||new URL(request.url()).origin!==renderer.origin)return;
  const h=request.headers();if(h['x-vision-project']&&h['x-vision-context'])contexts.set(h['x-vision-project'],h['x-vision-context']);
  if(!recording)return;if(isExport(request,renderer.origin))exports.push(request);
  else if(!['GET','HEAD'].includes(request.method()))writes.push({path:new URL(request.url()).pathname,method:request.method(),body:request.postData()});
 };
 // Leaving a project retires its gallery's in-flight thumbnail reads (ScopedDatasetThumbnail aborts them; right after
 // the first load that is the previous current project's gallery). Only exactly those are set aside: every other
 // failed request still lands in failed, which must hold the two aborted exports and nothing else.
 const retiredThumbnail=(request:Request)=>request.method()==='GET'&&request.frame()===page.mainFrame()&&new URL(request.url()).origin===renderer.origin&&new URL(request.url()).pathname.startsWith('/api/dataset/thumbnail/')&&request.failure()?.errorText==='net::ERR_ABORTED';
 const onFailed=(request:Request)=>{(retiredThumbnail(request)?retiredThumbnails:failed).push(request);},onResponse=(response:Response)=>{if(isExport(response.request(),renderer.origin))responses.push(response);},onDownload=(download:Download)=>downloads.push(download),onError=(error:Error)=>errors.push(error.message);
 page.on('request',onRequest);page.on('requestfailed',onFailed);page.on('response',onResponse);page.on('download',onDownload);page.on('pageerror',onError);
 const api=async(route:string,body?:unknown,method?:string)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const labelQuery=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
 const baseline=async(scope:Scope)=>({source_tree:tree(scope.source),annotation_tree:tree(scope.active.annotations_dir||scope.project.annotations_dir),project_sha256:sha(path.join(scope.project.project_dir,'project.json')),labels:await Promise.all(scope.images.map(image=>api(labelQuery(image.path)))),metadata:(await api('/api/dataset/metadata?limit=10')).items,versions:await api('/api/dataset/versions')});
 const panel=()=>page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});
 const shot=async(name:string,kind:'cancel'|'success'|'idle')=>{const target=kind==='idle'?panel().getByRole('button',{name:'내보내기 요청 취소',exact:true}):panel().getByRole('status').filter({hasText:kind==='cancel'?'다운로드를 시작하지 않았습니다.':'2개 이미지 · 2개 라벨 ZIP 생성 완료 · 다운로드 요청'});await target.scrollIntoViewIfNeeded();await expect(target).toBeInViewport();await evidence.screenshot(page,name);};
 const open=async(scope:Scope)=>{
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
  const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
  await expect(panel()).toContainText('검색 결과 2개');const summary=panel().getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true});if(await summary.locator('..').getAttribute('open')===null)await summary.click();
  await expect(panel().getByRole('button',{name:'라벨 내보내기',exact:true})).toBeEnabled();await expect.poll(()=>contexts.get(scope.project.id)).toBeTruthy();
 };
 const switchProject=async(scope:Scope,deadline=performance.now()+10_000)=>{
  await within(page.getByTitle('프로젝트 관리',{exact:true}).click(),deadline,'own project dialog');const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
  await within(dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click(),deadline,'original recent projects');
  const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});await within(expect(item).toHaveCount(1,{timeout:Math.max(1,deadline-performance.now())}),deadline,'exact owned project option');
  const wire=page.waitForResponse(response=>response.request().frame()===page.mainFrame()&&response.request().method()==='POST'&&new URL(response.url()).origin===renderer.origin&&new URL(response.url()).pathname==='/api/project/open'&&response.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});
  await within(item.click(),deadline,'real project handoff');const response=await within(wire,deadline,'original project-open response');expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({project_dir:scope.project.project_dir});const body=await within(response.body(),deadline,'project-open original full body');expect(JSON.parse(body.toString('utf8')).id).toBe(scope.project.id);expect(await within(response.finished(),deadline,'project-open response finish')).toBeNull();evidence.note('owning_export_project_open',{project_id:scope.project.id,request_body:response.request().postData(),response_body_base64:body.toString('base64'),response_sha256:createHash('sha256').update(body).digest('hex')});await within(expect(dialog).toHaveCount(0,{timeout:Math.max(1,deadline-performance.now())}),deadline,'real project dialog close');
  await within(expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:Math.max(1,deadline-performance.now())}),deadline,'real selected project');
 };
 const scopes:Scope[]=[],gates:Gate[]=[],zipReceipts:any[]=[],proofs:any[]=[];
 const generatedZip=(scope:Scope,body:any)=>path.join(scope.project.project_dir,'exports',path.basename(body.download_url)+'.zip');
 const verifyHeld=async(scope:Scope,gate:Gate,deadline:number)=>{
  const proof=await within(gate.captured,deadline,'genuine original upstream200 body');expect(proof.upstream.status).toBe(200);expect(proof.upstream.complete).toBe(true);expect(proof.response_headers_written).toBe(false);
  const raw=Buffer.from(proof.upstream.body_base64,'base64');expect(createHash('sha256').update(raw).digest('hex')).toBe(proof.upstream.body_sha256);const body=JSON.parse(raw.toString('utf8'));expect(body).toMatchObject({format:'yolo',image_count:2,annotation_count:2});
  const zip=generatedZip(scope,body);expect(fs.existsSync(zip)).toBe(true);const members=readBundle(zip);expect(members['classes.txt'].text).toBe('ControlledBox');evidence.addFile(zip);return{proof,body,zip,zip_sha256:sha(zip),members};
 };
 const assertAborted=async(gate:Gate,request:Request,deadline:number)=>{
  await within(expect.poll(()=>failed.includes(request),{timeout:Math.max(1,deadline-performance.now())}).toBe(true),deadline,'original browser requestfailed');expect(request.failure()?.errorText).toContain('ERR_ABORTED');expect(await within(request.response(),deadline,'original browser noResponse')).toBeNull();
  await within(expect.poll(()=>Boolean(gate.snapshot().client_close),{timeout:Math.max(1,deadline-performance.now())}).toBe(true),deadline,'genuine proxy client close');const snapshot=gate.snapshot();expect(snapshot.client_close).toMatchObject({headers_sent:false,writable_finished:false,closed_before_release:true});expect(snapshot.client_close.bytes_at_close).toBe(snapshot.client_close.bytes_before);return snapshot;
 };
 const freshDownload=async(scope:Scope,name:string)=>{
  await panel().getByLabel('라벨 교환 형식',{exact:true}).selectOption('yolo');const wire=page.waitForResponse(response=>isExport(response.request(),renderer.origin)),download=page.waitForEvent('download');
  await panel().getByRole('button',{name:'라벨 내보내기',exact:true}).click();const response=await wire;expect(response.status()).toBe(200);expect(response.request().postDataJSON()).toEqual({format:'yolo'});const raw=await response.body();expect(await response.finished()).toBeNull();const body=JSON.parse(raw.toString('utf8'));expect(body).toMatchObject({format:'yolo',image_count:2,annotation_count:2});
  const item=await download,file=path.join(workspace.logs,name+'.zip');await item.saveAs(file);expect(await item.failure()).toBeNull();const original=generatedZip(scope,body);expect(sha(file)).toBe(sha(original));const members=readBundle(file),names=scope.images.map(image=>path.relative(scope.source,image.path).replace(/\\/g,'/'));
  expect(Object.keys(members).sort()).toEqual([...names.map(n=>n.replace(/\.png$/,'.txt')),'classes.txt','image_manifest.json','studio_classes.json'].sort());expect(members['classes.txt'].text).toBe('ControlledBox');for(const n of names.map(n=>n.replace(/\.png$/,'.txt')))expect(members[n].text.trim().split(/\s+/).map(Number)).toEqual([0,0.328125,0.375,0.46875,0.5]);
  const receipt={request_body:response.request().postData(),status:200,response_body_base64:raw.toString('base64'),response_sha256:createHash('sha256').update(raw).digest('hex'),downloaded:file,original_zip:original,zip_sha256:sha(file),members};zipReceipts.push(receipt);evidence.addFile(file);evidence.addFile(original);await expect(panel().getByRole('status').filter({hasText:'2개 이미지 · 2개 라벨 ZIP 생성 완료 · 다운로드 요청'})).toBeVisible();return receipt;
 };
 try{
  await installDesktopHostShim(page,renderer.port);
  for(const tag of ['A','B']){
   const source=tag==='A'?workspace.dataset:path.join(workspace.root,'export-B-source');const images=tag==='A'?workspace.images:workspace.images.map(image=>{fs.mkdirSync(source,{recursive:true});const target=path.join(source,path.basename(image.path));fs.copyFileSync(image.path,target,fs.constants.COPYFILE_EXCL);return{...image,path:target};});
   const project=await api('/api/project/create',{name:'Owned real export '+tag,task:'segmentation'});const active=await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});await api('/api/team-data');await api('/api/team-data/readiness');
   for(let i=0;i<images.length;i++){const image=images[i],current=await api(labelQuery(image.path));await api('/api/annotations/save',{image_id:path.basename(image.path,'.png'),image_path:image.path,image_width:32,image_height:32,annotations:[{id:'controlled-export-'+tag+'-'+i,type:'bbox',label:'ControlledBox',category_id:7,color:'#f59e0b',bbox:[3,4,18,20]}],actor:'controlled-export-fixture',expected_revision:current.metadata.revision});}
   const scope:Scope={project,active,source,images,baseline:null};scope.baseline=await baseline(scope);scopes.push(scope);
  }
  const [A,B]=scopes;await page.goto(renderer.url);await switchProject(A);await open(A);A.baseline=await baseline(A);recording=true;
  await panel().getByLabel('라벨 교환 형식',{exact:true}).selectOption('yolo');const first=renderer.control.arm({key:'explicit-cancel-a',project:A.project.id,context:contexts.get(A.project.id)!,format:'yolo'});gates.push(first);
  const deadline=performance.now()+10_000,firstRequest=page.waitForRequest(request=>isExport(request,renderer.origin)&&request.frame()===page.mainFrame());await within(panel().getByRole('button',{name:'라벨 내보내기',exact:true}).click(),deadline,'own export click');const request=await within(firstRequest,deadline,'actual original export Request');const held=await verifyHeld(A,first,deadline);expect(request.headers()['x-vision-project']).toBe(A.project.id);expect(request.headers()['x-vision-context']).toBe(contexts.get(A.project.id));expect(request.postData()).toBe(JSON.stringify({format:'yolo'}));
  await within(panel().getByRole('button',{name:'내보내기 요청 취소',exact:true}).click(),deadline,'explicit owning cancellation');const aborted=await assertAborted(first,request,deadline);await within(expect(panel().getByRole('status').filter({hasText:'다운로드를 시작하지 않았습니다.'})).toBeVisible({timeout:Math.max(1,deadline-performance.now())}),deadline,'honest own cancellation status');expect(downloads).toHaveLength(0);expect(await baseline(A)).toEqual(A.baseline);proofs.push({kind:'explicit-cancel',original:held,aborted,body_dispatched_to_real_backend:true,server_bundle_retained:true,no_backend_worker_stop_claim:true});await shot('browser-own-export-explicit-cancel-no-download','cancel');
  await freshDownload(A,'fresh-after-own-cancel');expect(downloads).toHaveLength(1);first.release();expect(first.snapshot()).toMatchObject({discarded_after_client_close:true,response_headers_written:false});expect(downloads).toHaveLength(1);await expect(panel().getByRole('status').filter({hasText:'2개 이미지 · 2개 라벨 ZIP 생성 완료 · 다운로드 요청'})).toBeVisible();expect(sha(held.zip)).toBe(held.zip_sha256);await shot('browser-own-export-new-success-old-response-discarded','success');
  const second=renderer.control.arm({key:'scope-fence-a',project:A.project.id,context:contexts.get(A.project.id)!,format:'yolo'});gates.push(second);const switchedDeadline=performance.now()+10_000,nextWire=page.waitForRequest(q=>isExport(q,renderer.origin)&&q.frame()===page.mainFrame());await within(panel().getByRole('button',{name:'라벨 내보내기',exact:true}).click(),switchedDeadline,'second own export');const nextRequest=await within(nextWire,switchedDeadline,'second original export Request');const nextHeld=await verifyHeld(A,second,switchedDeadline);await switchProject(B,switchedDeadline);const fenced=await assertAborted(second,nextRequest,switchedDeadline);second.release();expect(second.snapshot().discarded_after_client_close).toBe(true);expect(downloads).toHaveLength(1);proofs.push({kind:'actual-project-context-switch-only',original:nextHeld,aborted:fenced,explicit_cancel_credit:false});await open(B);expect(await baseline(B)).toEqual(B.baseline);await expect(panel().getByRole('status').filter({hasText:/ZIP 생성 완료 · 다운로드 요청|다운로드를 시작하지 않았습니다/})).toHaveCount(0);await shot('browser-own-export-B-no-A-response-adoption','idle');
  await switchProject(A);await open(A);expect(await baseline(A)).toEqual(A.baseline);await freshDownload(A,'fresh-after-project-return');expect(downloads).toHaveLength(2);expect(await baseline(A)).toEqual(A.baseline);expect(exports).toHaveLength(4);expect(failed).toEqual([request,nextRequest]);for(const read of retiredThumbnails)expect([A.project.id,B.project.id]).toContain(read.headers()['x-vision-project']);expect(responses).toHaveLength(2);expect(errors).toEqual([]);expect(writes.map(w=>w.path)).toEqual(['/api/project/open','/api/project/open']);renderer.control.assertSettled();await shot('browser-own-export-A-return-real-recovery','success');
  const record={cell:'F117.format-export-bundle.cancel',mode:'browser',explicit_actual_cancel:true,genuine_browser_abort:true,real_original_upstream_bodies:proofs,original_ZIP_readback:zipReceipts,protected_before:scopes.map(scope=>({project:scope.project,source:scope.source,baseline:scope.baseline})),source_hashes:scopes.map(scope=>scope.images.map(image=>({path:image.path,sha256:sha(image.path),original_sha256:image.sha256}))),proxy:renderer.control.snapshots(),download_count:downloads.length,retired_gallery_thumbnail_reads:retiredThumbnails.map(read=>({path:new URL(read.url()).pathname,project:read.headers()['x-vision-project'],failure:read.failure()?.errorText})),actual_project_switch:true,no_panel_dismissal_credit:true,no_response_fabrication_or_route_abort:true,no_server_rollback_or_worker_stop:true,model_training:false,model_GPU_compute:false,browser_graphics_GPU_state_not_qualified:true,installed_native:false,whole_parent:false};
  const proof=path.join(workspace.logs,'owning-export-real-abort-proof.json');fs.writeFileSync(proof,JSON.stringify(record,null,2));evidence.addFile(proof);evidence.note('owning_export_real_abort',record);
 }finally{
  for(const gate of gates){if(gate.snapshot().upstream&&!gate.snapshot().released)gate.release();}
  page.off('request',onRequest);page.off('requestfailed',onFailed);page.off('response',onResponse);page.off('download',onDownload);page.off('pageerror',onError);
 }
});

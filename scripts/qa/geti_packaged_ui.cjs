'use strict';
/** Actual packaged Electron + frozen parent + packaged Geti acceptance.
 * Fresh synthetic state only. No training, provider, installed-user-app or 8443 access.
 */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const net=require('node:net');
const {spawn,spawnSync}=require('node:child_process');
const {_electron:electron,expect}=require('@playwright/test');
const {launchEnv,redact,verifyIdentity}=require('../verify_windows_packaged.cjs');
const digest=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const observerCode=String.raw`
import argparse,hashlib,json,os,threading,time
from pathlib import Path
import psutil
p=argparse.ArgumentParser();p.add_argument('--pid',type=int,required=True);p.add_argument('--output',required=True);p.add_argument('--resources',required=True);p.add_argument('--user-data',required=True);a=p.parse_args()
stop=threading.Event();threading.Thread(target=lambda:(os.read(0,1),stop.set()),daemon=True).start()
root=psutil.Process(a.pid);resources=Path(a.resources).resolve();userdata=Path(a.user_data).resolve()
processes={};hashes={};ports=set();errors=set()
def sha(file):
 h=hashlib.sha256()
 with open(file,'rb') as f:
  for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
 return h.hexdigest()
def record(proc):
 try:
  exe=proc.exe();argv=proc.cmdline();created=proc.create_time();kind=None
  if proc.pid==a.pid:kind='app'
  elif Path(exe).name in {'vision_ai_backend','vision_ai_backend.exe'}:kind='backend'
  elif Path(exe).resolve().is_relative_to(resources/'geti-platform'):kind='geti' if any('overlay/server.py' in s.replace('\\','/') for s in argv) else 'geti-worker'
  else:return
  key=(proc.pid,created)
  if exe not in hashes:hashes[exe]=sha(exe)
  row=processes.setdefault(key,{'pid':proc.pid,'create_time':created,'kind':kind,'executable':exe,'sha256':hashes[exe],'first_seen':time.time()})
  row['last_seen']=time.time()
  if kind=='backend':
   env=proc.environ();row['descriptor_from_packaged_resources']=env.get('MODU_GETI_RUNTIME_DESCRIPTOR')==str(resources/'geti-platform/runtime.json')
  if kind=='geti':
   env=proc.environ();source=env.get('MODU_GETI_SOURCE_ROOT','');data=env.get('DATA_DIR','');row['uses_packaged_source']=bool(source and Path(source).resolve()==resources/'geti-platform/source');row['isolated_data_root']=bool(data and Path(data).resolve().is_relative_to(userdata));row['entrypoint_from_packaged_resources']=any(str(resources/'geti-platform/overlay/server.py')==s for s in argv)
   port=env.get('PORT','');
   if port.isdigit():ports.add(int(port))
  try:
   for connection in proc.net_connections(kind='inet'):
    if connection.status==psutil.CONN_LISTEN and connection.laddr and connection.laddr.ip in {'127.0.0.1','::1'}:ports.add(connection.laddr.port)
  except psutil.AccessDenied:errors.add('per-process-port-enumeration-denied')
 except (psutil.Error,OSError,ValueError):pass
def write(final=False):
 result={'processes':list(processes.values()),'ports':sorted(ports),'observation_errors':sorted(errors)}
 if final:
  remaining=[]
  for (pid,created),row in processes.items():
   try:
    q=psutil.Process(pid)
    if q.create_time()==created and q.is_running() and q.status()!=psutil.STATUS_ZOMBIE:remaining.append({'pid':pid,'create_time':created,'kind':row['kind']})
   except psutil.Error:pass
  result['remaining_processes']=remaining
 Path(a.output).write_text(json.dumps(result,indent=2))
while not stop.is_set():
 try:children=[root,*root.children(recursive=True)]
 except psutil.Error:children=[]
 for child in children:record(child)
 write();stop.wait(.2)
write(True)
`;
async function waitFor(fn,timeout,label){const until=Date.now()+timeout;let last;while(Date.now()<until){try{const result=await fn();if(result)return result;}catch(error){last=error;}await delay(250);}throw Error(label+' timed out'+(last?': '+redact(last.message):''));}
async function api(page,route,body,method,timeout=30000){return page.evaluate(async ({route,body,method,timeout})=>{const port=await window.api.getBackendPort();if(!port)throw Error('backend not ready');const response=await fetch(`http://127.0.0.1:${port}${route}`,{signal:AbortSignal.timeout(timeout),method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`${route}: ${response.status}: ${(await response.text()).slice(0,2000)}`);return response.json();},{route,body,method,timeout});}
function sourceHashes(root){const result={};const visit=dir=>{for(const row of fs.readdirSync(dir,{withFileTypes:true})){const file=path.join(dir,row.name);if(row.isDirectory())visit(file);else if(row.isFile()&&/\.(png|jpe?g)$/i.test(row.name))result[path.relative(root,file)]=digest(file);}};visit(root);return result;}
async function verifyAdmittedImages(page,receipt,root,hashes){
 const items=[];let total;
 do{const reply=await api(page,`/api/platform/geti/images?offset=${items.length}&limit=13`);assert.equal(reply.pagination.offset,items.length);assert.equal(reply.pagination.limit,13);total??=reply.pagination.total;assert.equal(reply.pagination.total,total);assert(reply.items.length>0||items.length===total);items.push(...reply.items);}while(items.length<total);
 assert.equal(total,64);assert.equal(new Set(items.map(row=>row.relative_path)).size,64);
 const frozen=new Map(receipt.source_mapping.map(row=>[row.relative_path,row]));
 for(const row of items){const bound=frozen.get(row.relative_path);assert(bound);assert.equal(row.transferred,true);assert.equal(row.image_uuid,bound.media_id);assert.equal(row.sha256,bound.sha256);assert.equal(row.sha256,hashes[row.relative_path]);assert.equal(path.resolve(row.source_path),path.resolve(root,row.relative_path));}
 return {count:items.length,pagination:true,frozen_media_ids:true,original_hashes:true,library_activation:false};
}
function resourceTree(root){const result={};const visit=dir=>{for(const row of fs.readdirSync(dir,{withFileTypes:true}).sort((a,b)=>a.name.localeCompare(b.name))){const file=path.join(dir,row.name),relative=path.relative(root,file).replaceAll('\\','/'),stat=fs.lstatSync(file);if(stat.isSymbolicLink())result[relative]={kind:'link',target:fs.readlinkSync(file)};else if(stat.isDirectory()){result[relative]={kind:'directory',mode:stat.mode&0o777};visit(file);}else if(stat.isFile())result[relative]={kind:'file',mode:stat.mode&0o777,size:stat.size,sha256:digest(file)};else throw Error('Unexpected runtime resource kind: '+relative);}};visit(root);return result;}
function unchangedTree(before,after){const changed=[];for(const name of new Set([...Object.keys(before),...Object.keys(after)]))if(JSON.stringify(before[name])!==JSON.stringify(after[name]))changed.push(name);return changed;}
function verifySignature(bundle){const result=spawnSync('codesign',['--verify','--deep','--strict',bundle],{encoding:'utf8',timeout:120000});assert.equal(result.status,0,'packaged resource signature verification failed: '+redact(result.stderr||result.error?.message||''));return {status:'passed',deep:true,strict:true,trust:'ad-hoc; notarization unverified'};}
async function closedPort(port,host='127.0.0.1'){return new Promise(resolve=>{const socket=net.createConnection({host,port});let done=false;const finish=value=>{if(done)return;done=true;socket.destroy();resolve(value);};socket.setTimeout(500,()=>finish(false));socket.once('error',()=>finish(true));socket.once('connect',()=>finish(false));});}
async function closeOwned(app){let timer;try{await Promise.race([app.close(),new Promise((_,reject)=>{timer=setTimeout(()=>{app.process().kill();reject(Error('Owned packaged application close exceeded 60 seconds'));},60000);})]);}finally{clearTimeout(timer);}}
async function finishObserver(observer,output){observer.stdin.end('x');const exit=observer.exitCode!==null?observer.exitCode:await Promise.race([new Promise(resolve=>observer.once('exit',resolve)),delay(10000).then(()=>{throw Error('Owned observer did not exit');})]);assert.equal(exit,0,'process observer failed');const value=JSON.parse(fs.readFileSync(output));assert.deepEqual(value.remaining_processes,[],'owned process still alive after app close');const portResults=[];for(const port of value.ports){const ipv4=await closedPort(port),ipv6=await closedPort(port,'::1');portResults.push({port,closed_ipv4:ipv4,closed_ipv6:ipv6,closed:ipv4&&ipv6});}assert(portResults.every(row=>row.closed),'owned listener remains reachable after close');value.port_closure=portResults;fs.writeFileSync(output,JSON.stringify(value,null,2));return value;}
async function openPlatform(page){const simple=page.getByRole('button',{name:'간단히 보기',exact:true});if(await simple.isVisible())await simple.click();await page.locator('[data-beginner-goal="teach"]').click();await page.locator('[data-beginner-step="3"]').click();await page.getByRole('button',{name:'학습 작업대 열기 · 데이터부터 모델까지',exact:true}).click();const panel=page.getByTestId('geti-platform-workspace');await expect(panel).toBeVisible();await expect(panel).toHaveAttribute('aria-busy','false',{timeout:60000});return panel;}
async function saveInputRule(page){const simple=page.getByRole('button',{name:'간단히 보기',exact:true});if(await simple.isVisible())await simple.click();await page.locator('[data-beginner-goal="teach"]').click();await page.locator('[data-beginner-step="3"]').click();const rail=page.getByRole('tablist',{name:'학습 데이터 준비 단계'});await rail.getByRole('tab',{name:/사진 전처리/}).click();const preparation=page.getByRole('tabpanel').filter({has:page.getByRole('heading',{name:'사진 전처리',exact:true})});await preparation.getByRole('button',{name:'+ 밝기',exact:true}).click();await preparation.getByLabel('1번 밝기 배율',{exact:true}).fill('1.1');const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/training/input-preparation/preview'&&r.request().method()==='POST',{timeout:60000});await preparation.getByRole('button',{name:'실제 입력 미리보기',exact:true}).click();const reply=await response;assert(reply.ok(),await reply.text());const preview=await reply.json();await preparation.getByRole('button',{name:'규칙 저장',exact:true}).click();await expect(preparation).toContainText('다음 학습에 사용할 입력 규칙을 저장했습니다.');return preview.input_recipe_sha256;}
async function run(options){
 assert.equal(process.platform,'darwin','This artifact acceptance is macOS-specific');
 const bundle=path.resolve(options.app),output=path.resolve(options.output),python=path.resolve(options.python);assert(bundle.endsWith('.app'));
 assert(!fs.existsSync(path.join(output,'packaged-receipt.json')),'Use a fresh owned output directory');fs.mkdirSync(output,{recursive:true});
 const userData=path.join(output,'user-data'),exe=path.join(bundle,'Contents/MacOS/Vision AI Studio'),resources=path.join(bundle,'Contents/Resources');
 const expectedBackend=options['backend-sha'];assert.equal(typeof expectedBackend,'string','Pass the independently verified accepted --backend-sha');assert.match(expectedBackend,/^[a-f0-9]{64}$/);
 const release=JSON.parse(fs.readFileSync(path.join(resources,'backend_bin/backend-release.json'),'utf8'));
 const backend=path.join(resources,'backend_bin',release.executable),descriptor=path.join(resources,'geti-platform/runtime.json');
 assert.equal(digest(backend),expectedBackend);assert.equal(release.executable_sha256,expectedBackend);assert.equal(release.acceptance?.status,'passed');
 const runtimeDescriptor=JSON.parse(fs.readFileSync(descriptor,'utf8')),runtimePython=path.join(path.dirname(descriptor),runtimeDescriptor.python);
 const receipt={schema_version:1,status:'failed',started_at:new Date().toISOString(),scope:'owned synthetic actual packaged app; no installed user app or live web access',app_executable:exe,app_sha256:digest(exe),asar_sha256:digest(path.join(resources,'app.asar')),backend_sha256:digest(backend),backend_build_identity_sha256:release.inventory.build_identity_sha256,geti_descriptor_sha256:digest(descriptor),geti_python_sha256:digest(runtimePython),user_data:userData,launches:[],mutations:[],training:false,weights_download:false,gpt:false,qualification:{signing:'unverified',notarization:'unverified',managed_automatic_update:'unqualified',model_quality:'unverified'}};
 const beforeTree=resourceTree(path.dirname(descriptor));fs.writeFileSync(path.join(output,'geti-resource-tree-before.json'),JSON.stringify(beforeTree));receipt.resource_tree_sha256=crypto.createHash('sha256').update(JSON.stringify(beforeTree)).digest('hex');receipt.signature_before=verifySignature(bundle);
 const diagnostics=[];let app,observer,observerOutput;let first;
 const persist=()=>fs.writeFileSync(path.join(output,'packaged-receipt.json'),JSON.stringify(receipt,null,2));
 try{
  for(let iteration=0;iteration<2;iteration++){
   app=await electron.launch({executablePath:exe,args:[],cwd:output,env:launchEnv(process.env,userData),timeout:180000});
   const identity=await app.evaluate(({app})=>({packaged:app.isPackaged,execPath:process.execPath,resourcesPath:process.resourcesPath,pid:process.pid,userData:app.getPath('userData')}));verifyIdentity(identity,exe);assert.equal(path.resolve(identity.userData),userData);assert.equal(path.resolve(identity.resourcesPath),resources);
   observerOutput=path.join(output,`processes-${iteration}.json`);
   observer=spawn(python,['-u','-c',observerCode,'--pid',String(identity.pid),'--output',observerOutput,'--resources',resources,'--user-data',userData],{stdio:['pipe','pipe','pipe'],env:launchEnv(process.env,userData)});
   observer.stderr.on('data',chunk=>diagnostics.push(redact(chunk.toString())));observer.on('error',error=>diagnostics.push(redact(error.message)));observer.stdin.on('error',error=>diagnostics.push(error.code||'observer pipe error'));
   const page=await app.firstWindow();page.setDefaultTimeout(60000);const pageErrors=[];page.on('pageerror',error=>pageErrors.push(redact(error.message)));page.on('request',request=>{const url=new URL(request.url());if(url.pathname.startsWith('/api/platform/geti/')&&request.method()!=='GET')receipt.mutations.push({iteration,path:url.pathname,body:request.postDataJSON()});});
   await waitFor(async()=>page.url().startsWith('file:')&&await page.evaluate(()=>Boolean(window.api)),120000,'packaged renderer');
   const health=await waitFor(()=>api(page,'/health'),180000,'actual frozen parent health');assert.equal(health.status,'ok');
   await app.evaluate(({BrowserWindow})=>BrowserWindow.getAllWindows()[0].setSize(1440,1040));
   let context,project,dataReceipt,originals,recipe,imageListing;
   if(iteration===0){
    const accountMode=await api(page,'/api/accounts/config');receipt.accounts_mode=accountMode.enabled?'shared':'native-local-capability';
    project=await api(page,'/api/project/create',{name:'패키지 검증 · 합성 64장과 학습용 복사본',task:'classification'});
    context=(await api(page,'/api/context')).project_context;assert.equal(context.project_id,project.id);
    const {folder}=await api(page,'/api/onboarding/example-dataset',{});assert(!path.relative(userData,folder).startsWith('..'),'sample escaped owned user data');
    await api(page,'/api/project/update',{source_dataset_dir:folder},'PUT');await api(page,'/api/dataset/import',{folder_path:folder,task:'classification',validate_images:true});
    const split=(await api(page,'/api/dataset/split',{folder_path:folder,task:'classification',train_ratio:.6,val_ratio:.2,test_ratio:.2,seed:42})).split;
    const version=await api(page,'/api/dataset/versions',{name:'패키지 합성 원본 고정',dataset_path:folder});originals=sourceHashes(folder);assert.equal(Object.keys(originals).length,64,'owned example must contain 64 real images');
    await page.reload();await expect(page.locator('#workflow-project')).toContainText(project.name);recipe=await saveInputRule(page);
    const panel=await openPlatform(page);const before=await api(page,'/api/platform/geti/status');assert.equal(before.available,true,JSON.stringify(before.runtime));assert.equal(before.runtime.state,'stopped');assert.equal(before.dataset,null);assert.deepEqual(before.jobs,[]);
    const startedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/platform/geti/start'&&r.request().method()==='POST',{timeout:240000});await panel.getByRole('button',{name:'학습 환경 시작',exact:true}).click();const started=await startedResponse;assert(started.ok(),await started.text());assert.equal((await started.json()).runtime.state,'running');
    const preparedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/platform/geti/prepare'&&r.request().method()==='POST',{timeout:180000});await expect(panel.getByRole('button',{name:'학습용 데이터 준비',exact:true})).toBeEnabled();await panel.getByRole('button',{name:'학습용 데이터 준비',exact:true}).click();const prepared=await preparedResponse;assert(prepared.ok(),await prepared.text());dataReceipt=await prepared.json();
    assert.equal(dataReceipt.recipe_sha256,recipe);assert.deepEqual(dataReceipt.counts,{training:split.train,validation:split.val,testing:split.test,total:64});assert.equal(dataReceipt.source_mapping.length,64);assert(dataReceipt.source_mapping.every(row=>!Object.hasOwn(row,'copied_path')));
    imageListing=await verifyAdmittedImages(page,dataReceipt,folder,originals);
    assert.equal((await api(page,`/api/dataset/versions/${encodeURIComponent(version.id)}/verify`)).status,'verified');assert.equal((await api(page,`/api/dataset/versions/${encodeURIComponent(dataReceipt.dataset_version_id)}/verify`)).status,'verified');
    await expect(panel).toContainText('학습 기준 고정됨');await expect(panel.locator('.platform-counts')).toContainText('장');await page.screenshot({path:path.join(output,'packaged-real-prepared.png'),fullPage:true});
    first={project,context,folder,originals,recipe,receipt_id:dataReceipt.id,geti_project_id:dataReceipt.geti_project_id,counts:dataReceipt.counts,dataset_version_id:dataReceipt.dataset_version_id};receipt.first=first;
   }else{
    project=await api(page,'/api/project/current');context=(await api(page,'/api/context')).project_context;assert.equal(project.id,first.project.id);assert.equal(project.name,first.project.name);assert.deepEqual(context,first.context);assert.equal(project.source_dataset_dir,first.folder);assert.deepEqual(sourceHashes(first.folder),first.originals);
    await page.reload();await expect(page.locator('#workflow-project')).toContainText(project.name);const panel=await openPlatform(page);let status=await api(page,'/api/platform/geti/status');assert.equal(status.runtime.state,'stopped');assert.equal(status.dataset.id,first.receipt_id);assert.deepEqual(status.jobs,[]);assert.equal(status.dataset.recipe_sha256,first.recipe);
    imageListing=await verifyAdmittedImages(page,status.dataset,first.folder,first.originals);assert.equal((await api(page,'/api/platform/geti/status')).runtime.state,'stopped');imageListing.did_not_start_child=true;
    const startedResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/platform/geti/start'&&r.request().method()==='POST',{timeout:240000});await panel.getByRole('button',{name:'학습 환경 시작',exact:true}).click();const started=await startedResponse;assert(started.ok(),await started.text());status=await started.json();assert.equal(status.runtime.state,'running');assert.notEqual(status.runtime.degraded,true);assert.equal(status.dataset.id,first.receipt_id);assert.equal(status.dataset.geti_project_id,first.geti_project_id);assert.deepEqual(status.dataset.counts,first.counts);assert.deepEqual(status.jobs,[]);assert.deepEqual(sourceHashes(first.folder),first.originals);
    dataReceipt=status.dataset;await expect(panel).toContainText('학습 기준 고정됨');await page.screenshot({path:path.join(output,'packaged-restart-same-receipt.png'),fullPage:true});receipt.restart_persistence={project:true,context:true,receipt:true,originals_unchanged:true,new_training_jobs:0};
   }
   assert.deepEqual(pageErrors,[],'renderer error');assert.deepEqual(sourceHashes(first.folder),first.originals);
   receipt.launches.push({identity,health,context,renderer_url:page.url(),receipt_id:dataReceipt.id,image_listing:imageListing});persist();
   await closeOwned(app);app=null;await delay(1500);const observed=await finishObserver(observer,observerOutput);observer=null;
   assert(observed.processes.some(row=>row.kind==='backend'&&path.resolve(row.executable)===path.resolve(backend)&&row.sha256===expectedBackend&&row.descriptor_from_packaged_resources),'actual frozen parent identity/packaged descriptor not observed');
   assert(observed.processes.some(row=>row.kind==='geti'&&row.sha256===receipt.geti_python_sha256&&row.uses_packaged_source&&row.isolated_data_root&&row.entrypoint_from_packaged_resources),'actual packaged Python3.14 source/entrypoint/owned child not observed');
   const afterTree=resourceTree(path.dirname(descriptor)),changed=unchangedTree(beforeTree,afterTree);fs.writeFileSync(path.join(output,`geti-resource-tree-after-${iteration}.json`),JSON.stringify(afterTree));receipt.launches[iteration].resource_integrity={changed_paths:changed,unchanged:changed.length===0};persist();assert.deepEqual(changed,[],'Geti resource bytes/file tree changed during actual runtime use');receipt.launches[iteration].signature_after=verifySignature(bundle);
   receipt.launches[iteration].observed={backend:true,geti:true,remaining_processes:observed.remaining_processes,ports_closed:observed.port_closure.every(row=>row.closed)};persist();
  }
  assert.deepEqual(receipt.mutations.map(row=>[row.iteration,row.path]),[[0,'/api/platform/geti/start'],[0,'/api/platform/geti/prepare'],[1,'/api/platform/geti/start']]);
  receipt.status='passed';receipt.finished_at=new Date().toISOString();persist();process.stdout.write(JSON.stringify({status:receipt.status,output,backend_sha256:receipt.backend_sha256,receipt_id:receipt.first.receipt_id,counts:receipt.first.counts,training:false})+'\n');
 }catch(error){receipt.error=redact(error.stack||error.message);persist();throw error;}
 finally{if(app)try{await closeOwned(app);}catch(error){diagnostics.push(redact(error.message));}if(observer){try{await finishObserver(observer,observerOutput);}catch(error){diagnostics.push(redact(error.message));if(observer.exitCode===null)observer.kill();}}fs.writeFileSync(path.join(output,'harness-diagnostics.log'),diagnostics.join('\n'));}
}
module.exports={run,sourceHashes,api,observerCode};
if(require.main===module){const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];run(options).catch(error=>{process.stderr.write(redact(error.stack||error.message)+'\n');process.exitCode=1;});}

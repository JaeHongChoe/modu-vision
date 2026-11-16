#!/usr/bin/env node
'use strict';
/** Actual cookie-authenticated HTTPS model-results acceptance using an existing synthetic trained model.
 * Owns its TLS origin/renderer/parent process. Never trains, prepares data, downloads weights or creates an account.
 */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const http=require('node:http'),https=require('node:https'),net=require('node:net');
const {execFileSync}=require('node:child_process');
const {chromium,expect}=require('@playwright/test');
const harness=require('../e2e/fixtures/harness.cjs');
const repo=path.resolve(__dirname,'../..');
const sha=bytes=>crypto.createHash('sha256').update(bytes).digest('hex');
const fileSha=file=>sha(fs.readFileSync(file));
const readJson=file=>JSON.parse(fs.readFileSync(file,'utf8'));
function imageHashes(root){const result={};for(const dir of fs.readdirSync(root,{withFileTypes:true})){const file=path.join(root,dir.name);if(dir.isDirectory()){for(const [name,digest]of Object.entries(imageHashes(file)))result[path.join(dir.name,name)]=digest;}else if(dir.isFile()&&/\.(png|jpe?g)$/i.test(dir.name))result[dir.name]=fileSha(file);}return result;}
function safeError(error){return String(error.stack||error.message||error).replace(/(?:vision_session|csrf_token|X-Vision-Token)[=: ]+[^\s,;]+/gi,'[redacted credential]');}
function zipMembers(python,file){return JSON.parse(execFileSync(python,['-c','import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); print(json.dumps([{ "name":x.filename,"size":x.file_size} for x in z.infolist()]))',file],{encoding:'utf8'}));}
async function anonymousStatus(url,certificate){return new Promise((resolve,reject)=>{const request=https.get(url,{ca:certificate,headers:{Origin:new URL(url).origin}},response=>{response.resume();response.on('end',()=>resolve(response.statusCode));});request.on('error',reject);});}

async function tlsOrigin(output,staticDir){
 const tlsDir=path.join(output,'tls');fs.mkdirSync(tlsDir,{recursive:true,mode:0o700});
 const key=path.join(tlsDir,'owned-key.pem'),cert=path.join(tlsDir,'owned-cert.pem');
 execFileSync('openssl',['req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost,IP:127.0.0.1'],{stdio:'ignore'});fs.chmodSync(key,0o600);
 const certificate=fs.readFileSync(cert),publicKey=new crypto.X509Certificate(certificate).publicKey.export({type:'spki',format:'der'});
 const sockets=new Set(),stats={api:0,static:0,foreign:0,token_injection:false};let backendPort=null,origin='';
 const admitted=headers=>(!headers.origin||headers.origin===origin)&&(!headers['sec-fetch-site']||['same-origin','none'].includes(headers['sec-fetch-site']));
 const server=https.createServer({key:fs.readFileSync(key),cert:certificate},(request,response)=>{
  const route=request.url||'/';if(!admitted(request.headers)){stats.foreign++;response.writeHead(403).end();return;}
  if(route.startsWith('/api/')||route==='/health'){
   if(!backendPort){response.writeHead(503).end();return;}stats.api++;
   // Preserve the actual cookie, CSRF, Origin and selected-context headers. No process token is added.
   const headers={...request.headers,connection:'close','x-forwarded-proto':'https'};delete headers['x-vision-token'];delete headers.authorization;
   const upstream=http.request({host:'127.0.0.1',port:backendPort,path:route,method:request.method,headers,agent:false},result=>{response.writeHead(result.statusCode||502,{...result.headers,connection:'close'});result.pipe(response);result.on('error',()=>response.destroy());});
   upstream.on('error',()=>{if(response.headersSent)response.destroy();else response.writeHead(502,{'content-type':'application/json'}).end(JSON.stringify({detail:'Owned parent unavailable'}));});
   response.on('close',()=>{if(!response.writableFinished)upstream.destroy();});request.pipe(upstream);return;
  }
  if(request.method!=='GET'&&request.method!=='HEAD'){response.writeHead(405).end();return;}
  const pathname=new URL(route,origin).pathname;let selected;
  try{selected=fs.realpathSync(path.resolve(staticDir,'.'+decodeURIComponent(pathname==='/'?'/index.html':pathname)));}catch{response.writeHead(404).end();return;}
  if(!selected.startsWith(fs.realpathSync(staticDir)+path.sep)||!fs.statSync(selected).isFile()){response.writeHead(403).end();return;}
  stats.static++;const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'};
  response.writeHead(200,{'content-type':types[path.extname(selected)]||'application/octet-stream','cache-control':'no-store'});if(request.method==='HEAD')response.end();else fs.createReadStream(selected).pipe(response);
 });
 server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
 server.on('upgrade',(request,socket,head)=>{
  if(!backendPort||!request.url?.startsWith('/ws/')||!admitted(request.headers)){socket.end('HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n');return;}
  const upstream=net.connect(backendPort,'127.0.0.1',()=>{const headers={...request.headers,'x-forwarded-proto':'wss'};delete headers['x-vision-token'];delete headers.authorization;let first=`${request.method} ${request.url} HTTP/1.1\r\n`;for(const [key,value]of Object.entries(headers))for(const item of Array.isArray(value)?value:[value])first+=`${key}: ${item}\r\n`;upstream.write(first+'\r\n');if(head.length)upstream.write(head);upstream.pipe(socket);socket.pipe(upstream);});
  sockets.add(upstream);upstream.on('close',()=>sockets.delete(upstream));upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());socket.on('close',()=>upstream.destroy());
 });
 await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(0,'127.0.0.1',resolve);});origin=`https://localhost:${server.address().port}`;
 return{origin,certificate,spki:crypto.createHash('sha256').update(publicKey).digest('base64'),setBackend:port=>{backendPort=port;},stats,close:()=>new Promise(resolve=>{for(const socket of sockets)socket.destroy();server.close(resolve);})};
}

async function run(options){
 const owned=path.resolve(options.root||''),descriptor=path.resolve(options.descriptor||path.join(repo,'dist-geti/runtime.json'));
 assert(options.root&&fs.existsSync(path.join(owned,'receipt.json')),'Select the completed synthetic integrated QA root');
 const originalReceipt=readJson(path.join(owned,'receipt.json'));assert.equal(originalReceipt.status,'PASS');assert.equal(originalReceipt.job.status,'done');assert.equal(originalReceipt.job.worker_exited,true);assert(originalReceipt.models.length);
 const python=options.python||process.env.MV_E2E_PYTHON||path.join(repo,'.superpowers/sdd/2026-11-14-novice-first/qa-venv/bin/python');
 const output=path.resolve(options.output||path.join(owned,'browser-results-'+new Date().toISOString().replace(/[:.]/g,'-')));fs.mkdirSync(output,{recursive:false,mode:0o700});
 const source=path.join(owned,'source'),originals=imageHashes(source),sourceHead=execFileSync('git',['rev-parse','HEAD'],{cwd:repo,encoding:'utf8'}).trim();
 const sourceFiles=['src/renderer/services/getiPlatform.ts','src/renderer/components/platform/PlatformWorkspaceView.tsx','src/renderer/services/assistantCommandCatalog.ts','backend/api/routes_platform_geti.py'];
 const report={schema:1,status:'running',kind:'owned HTTPS browser trained-model acceptance',source_head:sourceHead,source_inputs_sha256:Object.fromEntries(sourceFiles.map(file=>[file,fileSha(path.join(repo,file))])),started_at:new Date().toISOString(),reused_job_id:originalReceipt.job.id,expected_model_id:originalReceipt.job.model_id,descriptor_sha256:fileSha(descriptor),original_image_count:Object.keys(originals).length,mutations:[],responses:[],predictions:[],exports:[],screenshots:[],page_errors:[],external_requests:[],limits:{new_training:false,new_accounts:false,private_images:false,gpt:false,user_8443:false,installed_app:false}};
 const persist=()=>fs.writeFileSync(path.join(output,'receipt.json'),JSON.stringify(report,null,2));
 const event=(name,value={})=>{process.stdout.write(JSON.stringify({event:name,...value})+'\n');};
 let tls,backend,browser,page,bodyError;
 try{
  const build=await harness.buildRenderer({repoRoot:repo,outDir:path.join(output,'renderer'),logFile:path.join(output,'renderer-build.log')});report.renderer_index_sha256=build.index.sha256;
  tls=await tlsOrigin(output,build.outDir);report.origin=tls.origin;report.tls={spki_pinned:true,certificate_sha256:sha(tls.certificate)};
  const baseEnv=harness.sanitizeEnv(process.env);for(const key of ['MODU_GETI_PYTHON','MODU_GETI_SOURCE_ROOT','MODU_GETI_ENTRYPOINT'])delete baseEnv[key];
  backend=await harness.startOwnedBackend({command:python,args:[path.join(repo,'backend/main.py'),'--host','127.0.0.1','--port','0','--project-dir',path.join(owned,'projects'),'--shared-auth-dir',path.join(owned,'auth'),'--log-level','warning'],cwd:repo,baseEnv,env:{PYTHONPATH:repo,PYTHONDONTWRITEBYTECODE:'1',VISION_AI_STUDIO_USER_DATA_DIR:path.join(owned,'userdata'),MODU_GETI_RUNTIME_DESCRIPTOR:descriptor,MODU_BROWSER_ORIGINS:JSON.stringify([tls.origin])},logDir:path.join(output,'parent-logs'),ownershipMarker:owned,timeoutMs:180000});
  tls.setBackend(backend.port);report.parent={pid:backend.pid,port:backend.port};
  browser=await chromium.launch({headless:true,args:[`--ignore-certificate-errors-spki-list=${tls.spki}`]});
  const context=await browser.newContext({viewport:{width:1440,height:1400},acceptDownloads:true});
  await context.route('**/*',route=>{const request=route.request(),url=new URL(request.url());if(url.origin===tls.origin&&request.method()!=='GET'&&/^\/api\/platform\/geti\/(train|weights|prepare|adopt-labels|recover)$/.test(url.pathname)){report.blocked_mutation=url.pathname;return route.abort('blockedbyclient');}if(['data:','blob:'].includes(url.protocol)||url.origin===tls.origin)return route.continue();report.external_requests.push(url.origin+url.pathname);return route.abort('blockedbyclient');});
  page=await context.newPage();page.setDefaultTimeout(25000);page.on('pageerror',error=>report.page_errors.push(safeError(error)));
  page.on('request',request=>{const route=new URL(request.url()).pathname;if(route.startsWith('/api/platform/geti/')&&request.method()!=='GET')report.mutations.push({path:route,body:request.postDataJSON()});});
  const requestStarts=new WeakMap();page.on('request',request=>requestStarts.set(request,Date.now()));
  page.on('response',response=>{const route=new URL(response.url()).pathname;if(route.startsWith('/api/platform/geti/')||route==='/api/dataset/library/images'){const record={path:route,status:response.status(),headers_ms:Date.now()-requestStarts.get(response.request())};report.responses.push(record);if(route.endsWith('/status')||route.endsWith('/start'))event('platform_read',record);}});
  const screenshot=async name=>{const file=path.join(output,name+'.png');await page.screenshot({path:file,fullPage:true});report.screenshots.push(file);persist();};
  const read=route=>page.evaluate(async route=>{const response=await fetch(route,{credentials:'include'});return{status:response.status,body:await response.json()};},route);
  await page.goto(tls.origin);await expect(page.getByRole('heading',{name:'브라우저 계정 로그인',exact:true})).toBeVisible();
  await page.getByLabel('공유 계정 이름',{exact:true}).fill('owned-platform-admin');await page.getByLabel('공유 계정 비밀번호',{exact:true}).fill('owned synthetic password 123');
  const loginResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/accounts/login'&&response.request().method()==='POST');await page.getByRole('button',{name:'연결·로그인',exact:true}).click();assert.equal((await loginResponse).status(),200);
  await page.getByRole('combobox',{name:'작업할 프로젝트',exact:true}).selectOption(originalReceipt.project_id);await expect(page.locator('#workflow-project')).toContainText('Owned integrated classification');
  const cookie=(await context.cookies()).find(cookie=>cookie.name==='vision_session');assert(cookie?.secure&&cookie?.httpOnly);report.authentication={actual_ui_login:true,actual_project_selection:true,secure_httponly_cookie:true,process_token_in_renderer:false};event('authenticated',{project_id:originalReceipt.project_id});
  const intro=page.getByRole('dialog',{name:'처음 시작하기',exact:true});if(await intro.isVisible())await intro.getByRole('button',{name:'닫기',exact:true}).click();
  await page.getByText('설정·도구',{exact:true}).click();await page.getByRole('button',{name:'학습 작업대 · 데이터부터 모델까지',exact:true}).click();
  const panel=page.getByTestId('geti-platform-workspace');await expect(panel).toHaveAttribute('aria-busy','false',{timeout:30000});
  const initial=await read('/api/platform/geti/status');assert.equal(initial.status,200);assert.equal(initial.body.jobs.length,1);assert.equal(initial.body.jobs[0].id,originalReceipt.job.id);assert.equal(initial.body.dataset.id,originalReceipt.dataset.id);
  if(initial.body.runtime.state!=='running'){
   const started=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/platform/geti/start'&&response.request().method()==='POST',{timeout:180000});await panel.getByRole('button',{name:'학습 환경 시작',exact:true}).click();const response=await started;assert(response.ok(),await response.text());
  }
  const status=(await read('/api/platform/geti/status')).body;assert.notEqual(status.runtime.degraded,true);const model=status.models.find(model=>model.id===originalReceipt.job.model_id);assert(model);assert.equal(status.jobs.length,1);
  const catalog=await read('/api/automation/catalog?operation='+encodeURIComponent('GET /api/platform/geti/status')+'&schemas=false');assert.equal(catalog.status,200);const operation=catalog.body.operations.find(item=>item.id==='GET /api/platform/geti/status');assert.equal(operation?.effect,'read');assert.equal(operation?.assistant?.mode,'read');report.assistant_catalog={operation:operation.id,effect:operation.effect,mode:operation.assistant.mode,gpt_calls:0};
  report.models={id:model.id,name:model.name,variants:model.variants.map(variant=>({id:variant.id,format:variant.format,evaluations:variant.evaluations,prediction_supported:variant.prediction_supported}))};
  const frozenPreparation=panel.getByRole('heading',{name:'고정된 학습·예측 규칙',exact:true}).locator('..');await expect(frozenPreparation).toContainText('크기 맞추기 80 × 64');await expect(panel).toContainText('현재 브라우저에 저장한 편집 규칙이 없습니다.');
  report.preparation={receipt_recipe_sha256:status.dataset.recipe_sha256,receipt_input_recipe:status.dataset.input_recipe,visible_copy:await panel.locator('section[aria-labelledby="platform-prepare-title"]').innerText(),reconnected_frozen_and_draft_distinguished:true};
  const results=panel.getByTestId('geti-platform-results');await expect(results.getByRole('combobox',{name:'확인할 모델',exact:true})).toHaveValue(model.id);await expect(results.locator('table')).toContainText('Accuracy');await expect(results.locator('table')).toContainText('0.5');await frozenPreparation.scrollIntoViewIfNeeded();await screenshot('trained-model-metrics');event('model_results',{model_id:model.id,formats:model.variants.map(variant=>variant.format)});
  const imagesResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/library/images'||new URL(response.url()).pathname==='/api/platform/geti/images');
  await results.getByRole('button',{name:'확인할 사진 불러오기',exact:true}).click();const imagesReply=await imagesResponse;const imagesBody=await imagesReply.json();
  report.image_picker={path:new URL(imagesReply.url()).pathname,status:imagesReply.status(),detail:typeof imagesBody.detail==='string'?imagesBody.detail:null,returned_images:imagesBody.items?.length??null};persist();
  if(!imagesReply.ok()){await screenshot('image-picker-error');throw Error(`Actual model image picker failed: ${imagesReply.status()} ${report.image_picker.detail||''}`);}
  const picker=results.getByRole('combobox',{name:'확인할 사진',exact:true});await expect(picker).toBeEnabled();await expect(picker.locator('option')).not.toHaveCount(0);
  report.image_picker.options=await picker.locator('option').count();report.image_picker.selected=await picker.inputValue();assert(report.image_picker.selected);assert.equal(report.image_picker.options,25);assert.equal(imagesBody.items.filter(item=>item.transferred).length,24);const newPhoto=imagesBody.items.find(item=>!item.transferred);assert(newPhoto);await expect(picker).toContainText('새 검사 사진');
  for(const format of ['openvino','onnx']){
   const variant=model.variants.find(variant=>variant.format===format);assert(variant?.prediction_supported);await results.getByRole('combobox',{name:'사용할 파일 형식',exact:true}).selectOption(variant.id);if(format==='onnx')await picker.selectOption(newPhoto.image_uuid);
   const predictionResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/platform/geti/predict'&&response.request().method()==='POST',{timeout:180000});await results.getByRole('button',{name:'선택 사진 예측',exact:true}).click();const response=await predictionResponse;assert(response.ok(),await response.text());const prediction=await response.json();
   assert.equal(prediction.model_id,model.id);assert.equal(prediction.variant_id,variant.id);assert.equal(prediction.recipe_sha256,originalReceipt.dataset.recipe_sha256);assert(prediction.predictions.length);
   await expect(results.locator('.platform-prediction')).toContainText(prediction.predictions[0].label);const selectedId=await picker.inputValue(),selectedImage=imagesBody.items.find(item=>item.image_uuid===selectedId);assert(selectedImage);assert.equal(prediction.image_sha256,selectedImage.sha256);report.predictions.push({format,model_id:prediction.model_id,variant_id:prediction.variant_id,recipe_sha256:prediction.recipe_sha256,image_sha256:prediction.image_sha256,source_relative_path:selectedImage.relative_path,transferred:selectedImage.transferred,predictions:prediction.predictions});await screenshot('prediction-'+format);
   const exportRoute=`/api/platform/geti/models/${model.id}/export/${variant.id}`;assert.equal(await anonymousStatus(tls.origin+exportRoute,tls.certificate),401);
   const exportResponse=page.waitForResponse(response=>new URL(response.url()).pathname===exportRoute,{timeout:180000}),downloadEvent=page.waitForEvent('download',{timeout:180000});await results.getByRole('button',{name:'선택 모델 내보내기',exact:true}).click();const exported=await exportResponse;assert(exported.ok(),await exported.text());const download=await downloadEvent;const file=path.join(output,'download-'+format+'.zip');await download.saveAs(file);assert.equal(await download.failure(),null);const headers=await exported.allHeaders();assert.equal(fileSha(file),headers['x-artifact-sha256']);
   const members=zipMembers(python,file);assert(members.some(member=>format==='onnx'?member.name.endsWith('.onnx'):member.name.endsWith('.xml')));if(format==='openvino')assert(members.some(member=>member.name.endsWith('.bin')));
   report.exports.push({format,file,bytes:fs.statSync(file).size,sha256:fileSha(file),members,unauthenticated_status:401,authenticated_status:exported.status()});persist();event('used_model',{format,predictions:prediction.predictions.length,export_sha256:fileSha(file)});
  }
  await panel.getByRole('button',{name:'상태 새로고침',exact:true}).click();await expect(panel).toHaveAttribute('aria-busy','false');await expect(results.getByRole('combobox',{name:'확인할 모델',exact:true})).toHaveValue(model.id);
  const final=(await read('/api/platform/geti/status')).body;assert.equal(final.jobs.length,1);assert.equal(final.jobs[0].id,originalReceipt.job.id);assert.equal(final.dataset.id,originalReceipt.dataset.id);assert.deepEqual(imageHashes(source),originals);assert.deepEqual(report.page_errors,[]);assert.deepEqual(report.external_requests,[]);assert.equal(report.blocked_mutation,undefined);
  report.source_originals_unchanged=true;report.same_training_job=true;report.status='passed';report.finished_at=new Date().toISOString();persist();event('PASS',{output,formats:report.predictions.map(item=>item.format),new_training:false});
 }catch(error){bodyError=error;report.status='failed';report.error=safeError(error);if(page)try{const file=path.join(output,'failure.png');await page.screenshot({path:file,fullPage:true});report.screenshots.push(file);}catch{}persist();event('FAIL',{output,error:report.error});}
 finally{
  if(browser)await browser.close();if(tls){await tls.close();report.https=tls.stats;}
  if(backend){report.parent_stop=await backend.stop();report.parent_port_closed=await harness.waitForPortClosed(backend.port,15000);assert.equal(report.parent_port_closed,true);assert.equal(report.parent_stop.groupAlive,false);assert.deepEqual(report.parent_stop.escaped,[]);}
  assert.deepEqual(imageHashes(source),originals);report.originals_preserved_on_exit=true;persist();
 }
 if(bodyError)throw bodyError;return report;
}
module.exports={run,tlsOrigin};
if(require.main===module){const options={};for(let i=2;i<process.argv.length;i+=2)options[process.argv[i].replace(/^--/,'')]=process.argv[i+1];run(options).catch(error=>{process.stderr.write(safeError(error)+'\n');process.exitCode=1;});}

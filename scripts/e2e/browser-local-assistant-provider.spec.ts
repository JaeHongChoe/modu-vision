import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect} from './fixtures/test';
import {localAssistantBrowser} from './fixtures/localAssistantBrowser';
import {expectBrowserLoginGate,chooseBrowserProject,browserLoginWithin} from './fixtures/browserGateLogin';
import {png} from './qa/appFlow';

const {assertPrivateEvidencePolicy}=require('./fixtures/localAssistantHelper.cjs');
// Raw Playwright traces can include HttpOnly cookies and CSRF responses. Never record them here.
test.use({ignoreHTTPSErrors:true,actionTimeout:15000,trace:'off'});
const sha=(data:Buffer)=>crypto.createHash('sha256').update(data).digest('hex');
// All provider identities/terms/plan usage here are controlled local simulations.
// No preload shim, principal/grant/store injection or actual external OAuth/provider exists.
test('real browser local helper controls provider setup read preview decline confirm cancel and actor-scoped saved log',async({page,rendererBuild,workspace,evidence,loopbackGuard,browser,trace})=>{
 assertPrivateEvidencePolicy(trace);
 if(process.env.MV_E2E_ASSISTANT_TRACE_POLICY_FAILURE==='1'){await page.goto('data:text/html,Controlled%20private%20evidence%20policy');throw Error('Controlled trace-policy failure before any account/server/provider setup');}
 test.setTimeout(240000);
 const server=await localAssistantBrowser(workspace,rendererBuild.outDir);loopbackGuard.allow(server.port);evidence.redact(server.backend.token);
 const username='assistantowner',password='owned assistant browser fixture 123';evidence.redact(password);
 const source=path.join(workspace.root,'assistant-originals'),originals:Array<{path:string;sha256:string}>=[];
 for(const [i,label] of ['OK','NG'].entries()){fs.mkdirSync(path.join(source,label),{recursive:true});for(let n=0;n<10;n++){const file=path.join(source,label,`${n}.png`);fs.writeFileSync(file,png(64,3,(x,y)=>[100+i*50+n,x%256,y%256]));originals.push({path:file,sha256:sha(fs.readFileSync(file))});}}
 const foreign:string[]=[],pageErrors:string[]=[],writes:Array<{body:any;project:string|null;context:any;run:string|null}>=[],secrets:string[]=[];
 const responses:Promise<void>[]=[],publicBodies:string[]=[];
 const ledger=()=>fs.existsSync(server.ledger)?fs.readFileSync(server.ledger,'utf8').trim().split('\n').filter(Boolean).map(line=>JSON.parse(line)):[];
 const guard=async(target:Page)=>{target.on('pageerror',error=>pageErrors.push(error.message));await target.context().route(/^https?:\/\//,route=>{const url=new URL(route.request().url());if(url.origin===server.origin)return route.fallback();foreign.push(url.origin);return route.abort('blockedbyclient');});await target.context().routeWebSocket(/^wss?:\/\//,socket=>{if(new URL(socket.url()).host===new URL(server.origin).host)socket.connectToServer();else{foreign.push(new URL(socket.url()).origin);socket.close();}});};
 const login=async(target:Page,projectId:string,user=username)=>{await target.goto(server.origin);await expectBrowserLoginGate(target,server.origin);await target.getByLabel('공유 계정 이름',{exact:true}).fill(user);await target.getByLabel('공유 계정 비밀번호',{exact:true}).fill(password);const browserLoginDeadline=performance.now()+20_000;await target.getByRole('button',{name:'연결·로그인',exact:true}).click();await chooseBrowserProject(target,projectId,'local_host',browserLoginDeadline);await browserLoginWithin(()=>expect(target.getByRole('main',{name:'브라우저 계정 로그인',exact:true})).toHaveCount(0),browserLoginDeadline);};
 const open=async(target:Page)=>{await target.getByRole('button',{name:'왜 그런가요?',exact:true}).click();await target.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();return target.getByRole('complementary',{name:'도우미',exact:true});};
 let second:Awaited<ReturnType<typeof browser.newContext>>|undefined,other:Awaited<ReturnType<typeof browser.newContext>>|undefined;
 try{
  await server.call('/api/accounts/bootstrap',{username,password});const owner=await server.call('/api/accounts/login',{username,password});evidence.redact(owner.token);
  const api=(route:string,body?:unknown,method?:string)=>server.call(route,body,owner.token,method);
  const project=await api('/api/project/create',{name:'Controlled browser assistant',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
  await api('/api/dataset/split',{folder_path:source,task:'classification',train_ratio:.8,val_ratio:.1,test_ratio:.1});
  const splitFile=path.join(project.dataset_dir,'splits',crypto.createHash('sha256').update(source).digest('hex')+'.json'),initial=sha(fs.readFileSync(splitFile));
  await guard(page);
  page.on('request',request=>{const url=new URL(request.url());if(url.origin!==server.origin)return;const headers=request.headers();if(url.pathname==='/api/dataset/split'&&request.method()==='POST')writes.push({body:request.postDataJSON(),project:headers['x-vision-project']??null,context:headers['x-vision-context']?JSON.parse(headers['x-vision-context']):null,run:headers['x-modu-assistant-run']??null});if(/\/api\/(training\/start|label-suggestions\/batches|labeling-jobs)/.test(url.pathname)&&request.method()!=='GET')pageErrors.push('Unexpected model operation');});
  page.on('response',response=>{const route=new URL(response.url()).pathname;if(route==='/api/accounts/login'){responses.push(response.json().then(value=>{secrets.push(value.csrf_token);evidence.redact(value.csrf_token);expect(value.token).toBeUndefined();}));}else if(route.startsWith('/api/local-host/assistant/'))responses.push(response.text().then(text=>{publicBodies.push(text);}));});
  await login(page,project.id);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await expect(page.getByRole('button',{name:'전체 (20)',exact:true})).toBeVisible();
  const assistant=await open(page);await expect(assistant.getByText('데스크톱 앱에서 여세요.',{exact:false})).toHaveCount(0);
  const terms=assistant.getByRole('checkbox',{name:'약관과 전송·보관 안내를 읽고 동의합니다.',exact:true});await expect(terms).not.toBeChecked();await expect(assistant.getByRole('button',{name:'Continue with ChatGPT',exact:true})).toBeDisabled();
  expect(ledger().filter(row=>row.kind==='provider')).toEqual([]);
  // Synthetic legal/provider interaction ONLY: helper has no real fetch fallback.
  await terms.check();await assistant.getByRole('button',{name:'Continue with ChatGPT',exact:true}).click();
  await page.getByRole('dialog',{name:'ChatGPT 플랜 사용 안내',exact:true}).getByRole('button',{name:'안내 확인',exact:true}).click();
  await assistant.getByRole('button',{name:'모델 목록 불러오기',exact:true}).click();await assistant.getByLabel('ChatGPT 모델',{exact:true}).selectOption('fixture-model');
  const question=assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true});await expect(question).toBeEnabled();
  const ask=async(text:string)=>{await question.fill(text);await assistant.getByRole('button',{name:'보내기',exact:true}).click();};
  await ask('READ_FIXTURE 데이터 수를 읽어 주세요.');await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('데이터 수를 읽었습니다.');await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('제어된 도우미 완료');expect(writes).toEqual([]);
  await ask('WRITE_FIXTURE 분할을 저장할 미리보기를 보여 주세요.');const preview=assistant.getByRole('group',{name:'쓰기 실행 확인',exact:true});await expect(preview).toContainText('학습 70% · 검증 20% · 시험 10%');expect(writes).toEqual([]);expect(sha(fs.readFileSync(splitFile))).toBe(initial);
  await evidence.screenshot(page,'real-browser-helper-write-preview-no-mutation');await assistant.getByRole('button',{name:'실행하지 않기',exact:true}).click();await expect(preview).toHaveCount(0);await expect(question).toBeEnabled();expect(writes).toEqual([]);expect(sha(fs.readFileSync(splitFile))).toBe(initial);
  await ask('WRITE_FIXTURE 같은 분할을 확인 후 저장하겠습니다.');await expect(preview).toBeVisible();expect(writes).toEqual([]);
  await assistant.getByRole('button',{name:'확인하고 실행',exact:true}).click();await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('분할을 저장했습니다(학습 14 · 검증 4 · 시험 2장).');await expect(question).toBeEnabled();
  expect(writes).toHaveLength(1);expect(writes[0]).toMatchObject({project:project.id,context:{project_id:project.id,actor_id:owner.user.id,mode:'team'},body:{folder_path:source,task:'classification',train_ratio:.7,val_ratio:.2,test_ratio:.1}});expect(writes[0].run).toMatch(/^run_[a-f0-9]{24}$/);expect(sha(fs.readFileSync(splitFile))).not.toBe(initial);
  await ask('CANCEL_FIXTURE 멈추기 경계를 확인합니다.');await expect(assistant.getByRole('button',{name:'멈추기',exact:true})).toBeVisible();await expect.poll(()=>ledger().filter(row=>row.route==='/v1/responses').length).toBe(7);await assistant.getByRole('button',{name:'멈추기',exact:true}).click();await expect(question).toBeEnabled();await expect.poll(()=>ledger().some(row=>row.kind==='provider_cancelled')).toBe(true);expect(writes).toHaveLength(1);
  let runs=(await api('/api/assistant/runs')).runs;expect(runs).toEqual(expect.arrayContaining([expect.objectContaining({tool:'read_dataset_summary',tier:'read',actor:username,result:'ok'}),expect.objectContaining({tool:'save_split',confirmed:false,result:'refused',error_code:'declined_by_person',actor:username}),expect.objectContaining({run_id:writes[0].run,tool:'save_split',confirmed:true,result:'ok',actor:username})]));
  await assistant.getByText('실행 기록(이 프로젝트)',{exact:true}).click();await expect(assistant.getByRole('list',{name:'도우미 실행 기록',exact:true})).toContainText('save_split');
  // A separate cookie session for the same real account starts with no selected provider recipient.
  second=await browser.newContext({ignoreHTTPSErrors:true});const tab=await second.newPage();await guard(tab);await login(tab,project.id);const sibling=await open(tab);await expect(sibling.getByLabel('ChatGPT 계정',{exact:true})).toHaveValue('');await expect(sibling.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeDisabled();await expect(question).toBeEnabled();await ask('READ_FIXTURE 다른 탭 연결 뒤에도 원래 탭의 읽기만 확인합니다.');await expect(question).toBeEnabled();await expect.poll(()=>ledger().filter(row=>row.route==='/v1/responses').length).toBe(9);expect(writes).toHaveLength(1);
  // Different real account cannot read the owner's generated provider registrations.
  const foreignUser=await api('/api/accounts/users',{username:'assistantother',password,administrator:false});
  // This account previously had no project. Explicit choice now needs its own inert read-only destination.
  const foreignProject=await api('/api/project/create',{name:'Isolated viewer assistant fixture',task:'classification'});
  await api(`/api/accounts/projects/${foreignProject.id}/members`,{user_id:foreignUser.id,role:'viewer'},'PUT');
  await api('/api/accounts/select-project',{project_id:project.id});
  const foreignSession=await server.call('/api/accounts/login',{username:'assistantother',password});evidence.redact(foreignSession.token);
  expect(foreignSession.user.administrator).toBe(0);
  const foreignMe=await server.call('/api/accounts/me',undefined,foreignSession.token);
  expect(foreignMe.projects.map((row:{id:string;role:string})=>({id:row.id,role:row.role}))).toEqual([{id:foreignProject.id,role:'viewer'}]);
  await expect(server.call('/api/accounts/select-project',{project_id:project.id},foreignSession.token)).rejects.toThrow(/403/);
  await server.call('/api/accounts/select-project',{project_id:foreignProject.id},foreignSession.token);
  await expect(server.call('/api/project/update',{name:'Refused viewer mutation'},foreignSession.token,'PUT')).rejects.toThrow(/403/);
  evidence.note('isolated_nonadmin_project_choice',{project_id:foreignProject.id,role:'viewer',owner_project_access_refused:true,viewer_mutation_refused:true,administrator:false});
  other=await browser.newContext({ignoreHTTPSErrors:true});const foreignActor=await other.newPage();await guard(foreignActor);await login(foreignActor,foreignProject.id,'assistantother');const isolated=await open(foreignActor);await expect(isolated.getByLabel('ChatGPT 계정',{exact:true})).toHaveCount(0);await expect(isolated.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeDisabled();
  runs=(await api('/api/assistant/runs')).runs;expect(runs).toHaveLength(12);expect(runs.every((run:{actor:string})=>run.actor===username)).toBe(true);
  // This check observes public renderer state; it does not inject a bridge, capability or credential.
  await Promise.all(responses);const visible=await page.evaluate(()=>({preload:typeof window.api,html:document.documentElement.outerHTML,storage:JSON.stringify({local:{...localStorage},session:{...sessionStorage}})}));expect(visible.preload).toBe('undefined');expect(publicBodies.join('\n')).not.toMatch(/eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/);for(const secret of ['fixture-browser-access-secret','fixture-browser-refresh-secret',...secrets]){expect(visible.html).not.toContain(secret);expect(visible.storage).not.toContain(secret);expect(publicBodies.join('\n')).not.toContain(secret);expect(JSON.stringify(runs)).not.toContain(secret);expect(fs.readFileSync(server.ledger,'utf8')).not.toContain(secret);}
  for(const file of originals)expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);expect(foreign).toEqual([]);expect(pageErrors).toEqual([]);const rows=ledger();expect(rows.some(row=>row.pkce_verified)).toBe(true);expect(rows.filter(row=>row.method==='assistant.beginRun')).toHaveLength(1);expect(rows.find(row=>row.method==='assistant.beginRun')).toMatchObject({actor_id:owner.user.id,run_id:writes[0].run,tool:'save_split'});
  await evidence.screenshot(page,'real-browser-helper-confirmed-split-saved-run-log');evidence.note('local_browser_assistant_provider',{actual_mounted_browser:true,real_cookie_account:true,real_private_protocol_service:true,provider_network:'controlled_fetch_no_fallback',native_io_and_encryptor:'controlled_test_boundary_not_OS_acceptance',synthetic_terms_only:true,actual_provider_terms_or_account_acceptance:false,remote_browser_positive_flow:false,remote_isolation_evidence:'existing real-controller remote assistant and renderer remote-envelope suites',project_id:project.id,actor_id:owner.user.id,write_count:writes.length,write:writes[0],saved_runs:runs,host_provider_trace:rows,originals,external_network_requests:foreign,window_api:'absent',provider_secrets_exposed:false,models_or_training_executed:false});
 }finally{await second?.close();await other?.close();evidence.note('local_assistant_provider_close',await server.close());}
});

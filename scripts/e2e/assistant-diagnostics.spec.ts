import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test,expect} from './fixtures/test';
import {localAssistantBrowser} from './fixtures/localAssistantBrowser';
import {expectBrowserLoginGate} from './fixtures/browserGateLogin';
import {png} from './qa/appFlow';
import type {AssistantTask} from '../../src/renderer/services/assistantTasks/types';

const {assertPrivateEvidencePolicy,ACCESS,REFRESH}=require('./fixtures/localAssistantHelper.cjs');
const sha=(value:Buffer)=>crypto.createHash('sha256').update(value).digest('hex');
test.use({ignoreHTTPSErrors:true,actionTimeout:15_000,trace:'off'});

// Real browser login/cookie/CSRF, helper protocol, app stores and task ledger. Only the helper provider and two
// explicitly owned split-error responses are controlled. This fixture never injects stores/preload or runs models.
test('explicit diagnostics question receives scoped errors and GET-only task feedback before an unapproved follow-up',async({page,rendererBuild,workspace,evidence,loopbackGuard,trace})=>{
  assertPrivateEvidencePolicy(trace);test.setTimeout(240_000);
  const repo=path.resolve(__dirname,'../..'),receiptFiles=[
    'scripts/e2e/assistant-diagnostics.spec.ts','scripts/e2e/fixtures/localAssistantHelper.cjs',
    'src/renderer/services/api.ts','src/renderer/services/apiDiagnostics.ts','src/renderer/services/assistantDiagnostics.ts',
    'src/renderer/services/assistantDiagnosticsRuntime.ts','src/renderer/services/assistantContext.ts','src/renderer/services/assistantSession.ts',
    'src/renderer/services/assistantTools.ts','src/renderer/services/assistantTasks/feedback.ts','src/renderer/services/assistantTasks/controller.ts',
    'src/main/assistant/service.ts','src/main/assistant/redact.ts',
  ].map(file=>path.join(repo,file));
  receiptFiles.push(path.join(rendererBuild.outDir,'index.html'),...fs.readdirSync(path.join(rendererBuild.outDir,'assets')).filter(file=>/^main-.*\.(js|css)$/.test(file)).map(file=>path.join(rendererBuild.outDir,'assets',file)));
  evidence.note('diagnostics_source_bytes',receiptFiles.map(file=>({path:file,sha256:sha(fs.readFileSync(file))})));
  const server=await localAssistantBrowser(workspace,rendererBuild.outDir);loopbackGuard.allow(server.port);evidence.redact(server.backend.token);
  const username='diagnosticsfixture',password='owned diagnostics fixture password 123';evidence.redact(password);
  const pageErrors:string[]=[],foreign:string[]=[],prohibited:string[]=[],taskEvents:any[]=[],taskReads:string[]=[],splitErrors:any[]=[],jobsReads:string[]=[];
  const responseChecks:Promise<void>[]=[];
  const canaries=['fixture-diagnostics-secret','diagnostics-private.invalid','diagnostics-private@example.invalid','10.77.88.99','/Users/diagnostics-private'];
  for(const value of [...canaries,ACCESS,REFRESH])evidence.redact(value);
  const source=path.join(workspace.root,'diagnostics-originals'),originals:Array<{path:string;sha256:string}>=[];
  for(const [index,label]of ['OK','NG'].entries()){
    fs.mkdirSync(path.join(source,label),{recursive:true});
    for(let i=0;i<10;i++){const file=path.join(source,label,`${label}-${i}.png`);fs.writeFileSync(file,png(64,3,(x,y)=>[index*80+i,x%256,y%256]));originals.push({path:file,sha256:sha(fs.readFileSync(file))});}
  }
  const ledger=()=>fs.existsSync(server.ledger)?fs.readFileSync(server.ledger,'utf8').trim().split('\n').filter(Boolean).map(row=>JSON.parse(row)):[];
  let sourceSplitPath='',initialSplitHash='';
  try{
    await server.call('/api/accounts/bootstrap',{username,password});
    const owner=await server.call('/api/accounts/login',{username,password});evidence.redact(owner.token);
    const api=(route:string,body?:unknown,method?:string)=>server.call(route,body,owner.token,method);
    const project=await api('/api/project/create',{name:'Diagnostics controlled project',task:'classification'});
    await api('/api/project/update',{source_dataset_dir:source},'PUT');
    await api('/api/dataset/import',{folder_path:source,task:'classification'});
    await api('/api/dataset/split',{folder_path:source,task:'classification',train_ratio:.8,val_ratio:.1,test_ratio:.1});
    sourceSplitPath=path.join(project.dataset_dir,'splits',crypto.createHash('sha256').update(source).digest('hex')+'.json');
    initialSplitHash=sha(fs.readFileSync(sourceSplitPath));
    const readTask=async(id:string):Promise<AssistantTask>=>(await api('/api/assistant-tasks/'+id)).task;
    const taskByGoal=async(goal:string):Promise<AssistantTask|undefined>=>(await api('/api/assistant-tasks')).tasks.find((row:AssistantTask)=>row.goal===goal);
    page.on('pageerror',error=>pageErrors.push(error.message));
    page.on('request',request=>{
      const url=new URL(request.url());if(!/^https?:$/.test(url.protocol)||url.origin!==server.origin)return;
      const p=url.pathname,method=request.method();
      if(/^\/api\/assistant-tasks\/task_[a-f0-9]{32}$/.test(p)&&method==='GET')taskReads.push(p);
      if(/^\/api\/assistant-tasks\/task_[a-f0-9]{32}\/events$/.test(p)&&method==='POST')taskEvents.push(request.postDataJSON());
      if(p==='/api/training-workspace/tasks'&&method==='GET')jobsReads.push(p);
      if(method!=='GET'&&/^\/api\/(training\/start|label-suggestions\/batches|labeling-jobs|inspections?\/runs|flow-evaluations|evaluation\/|flowchart\/(run|partial-run))/.test(p))prohibited.push(p);
    });
    page.on('response',response=>{if(new URL(response.url()).pathname==='/api/accounts/login')responseChecks.push(response.json().then(value=>{evidence.redact(value.csrf_token);expect(value.token).toBeUndefined();}));});
    await page.context().route(/^https?:\/\//,route=>{const url=new URL(route.request().url());if(url.origin===server.origin)return route.fallback();foreign.push(url.origin);return route.abort('blockedbyclient');});
    await page.context().routeWebSocket(/^wss?:\/\//,socket=>{if(new URL(socket.url()).host===new URL(server.origin).host)socket.connectToServer();else{foreign.push(new URL(socket.url()).origin);socket.close();}});
    await page.goto(server.origin);await expectBrowserLoginGate(page,server.origin);
    await page.getByLabel('공유 계정 이름',{exact:true}).fill(username);await page.getByLabel('공유 계정 비밀번호',{exact:true}).fill(password);
    await page.getByRole('button',{name:'연결·로그인',exact:true}).click();
    await expect(page.getByRole('main',{name:'브라우저 계정 로그인',exact:true})).toHaveCount(0);
    await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
    await expect(page.getByRole('button',{name:'전체 (20)',exact:true})).toBeVisible();
    const openAssistant=async()=>{await page.getByRole('button',{name:'왜 그런가요?',exact:true}).click();await page.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();return page.getByRole('complementary',{name:'도우미',exact:true});};
    let assistant=await openAssistant();
    await assistant.getByRole('checkbox',{name:'약관과 전송·보관 안내를 읽고 동의합니다.',exact:true}).check();
    await assistant.getByRole('button',{name:'Continue with ChatGPT',exact:true}).click();
    await page.getByRole('dialog',{name:'ChatGPT 플랜 사용 안내',exact:true}).getByRole('button',{name:'안내 확인',exact:true}).click();
    await assistant.getByRole('button',{name:'모델 목록 불러오기',exact:true}).click();
    const restore=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/assistant-tasks'&&response.request().method()==='GET');
    await assistant.getByLabel('ChatGPT 모델',{exact:true}).selectOption('fixture-model');await restore;
    await expect(assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();
    expect(ledger().filter(row=>row.route==='/v1/responses')).toHaveLength(0);
    const ask=async(text:string)=>{await assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true}).fill(text);await assistant.getByRole('button',{name:'보내기',exact:true}).click();await expect(assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();};
    await ask('DIAGNOSTICS_FIXTURE_SEED 실행하지 말고 진단용 계획만 준비해 주세요.');
    // Wait for the real UI/ledger mutation, as the textbox can still be enabled at the start of the async send.
    await expect(assistant.getByRole('region',{name:'도우미 작업',exact:true}).getByRole('button',{name:'계획 확인하고 실행',exact:true})).toBeEnabled();
    const seed=await taskByGoal('진단용 미승인 작업');expect(seed).toBeTruthy();expect(seed!.status).toBe('draft');expect(seed!.approval).toBeNull();
    expect(seed!.steps.every(step=>step.status==='pending')).toBe(true);expect(ledger().filter(row=>row.route==='/v1/responses')).toHaveLength(2);
    await assistant.getByRole('button',{name:'닫기',exact:true}).click();
    let fault=0;
    await page.route('**/api/dataset/split',async route=>{
      if(route.request().method()!=='POST')return route.fallback();
      fault++;splitErrors.push({attempt:fault,method:'POST',status:fault===1?409:200,kind:fault===1?'structured_http':'empty_json'});
      if(fault===1)return route.fulfill({status:409,contentType:'application/json',body:JSON.stringify({detail:{code:'dataset.SPLIT_CONFLICT',message:'진단용 분할 충돌 /Users/diagnostics-private/source.png https://diagnostics-private.invalid/task diagnostics-private@example.invalid 10.77.88.99 token=fixture-diagnostics-secret'}})});
      if(fault===2)return route.fulfill({status:200,contentType:'application/json',body:''});
      throw Error('Unexpected diagnostic fixture split retry');
    });
    const split=page.getByRole('button',{name:'이 비율로 나누기',exact:true});await expect(split).toBeEnabled();
    const conflict=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/split'&&response.status()===409);
    await split.click();await conflict;await expect(page.getByText(/진단용 분할 충돌/)).toBeVisible();
    await expect(split).toBeEnabled();const empty=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/split'&&response.status()===200);
    await split.click();await empty;await expect(page.getByText(/Unexpected end of JSON input/)).toBeVisible();
    expect(fault).toBe(2);expect(sha(fs.readFileSync(sourceSplitPath))).toBe(initialSplitHash);
    expect(ledger().filter(row=>row.route==='/v1/responses')).toHaveLength(2);
    assistant=await openAssistant();const beforeReadCount=taskReads.length,beforeJobs=jobsReads.length;
    await evidence.screenshot(page,'01-explicit-question-before-read-tools');
    await ask('DIAGNOSTICS_FIXTURE_READ 지금 오류와 저장된 작업 상태를 읽고 원인을 설명해 주세요. 자동으로 재시도하지 말고 확인 계획만 주세요.');
    await expect(assistant).toContainText('409 충돌과 빈 JSON 응답을 확인했고');
    const followup=await taskByGoal('자료 오류 확인 계획');expect(followup).toBeTruthy();expect(followup!.status).toBe('draft');expect(followup!.approval).toBeNull();
    expect(followup!.steps.map(step=>step.status)).toEqual(['pending']);
    const seedAfter=await readTask(seed!.id);expect(seedAfter.revision).toBe(seed!.revision);expect(seedAfter.approval).toBeNull();expect(seedAfter.steps.every(step=>step.status==='pending')).toBe(true);
    expect(taskReads.slice(beforeReadCount)).toContain('/api/assistant-tasks/'+seed!.id);expect(jobsReads.length).toBeGreaterThan(beforeJobs);
    const rows=ledger(),feedback=rows.find(row=>row.kind==='diagnostics_feedback');expect(feedback).toBeTruthy();
    expect(feedback).toMatchObject({saw_http_409:true,saw_json_failure:true,job_snapshot_state:'observed',privacy_clean:true,
      task:{task_id:seed!.id,revision:seed!.revision,status:'draft',fresh:true,source:'ledger',current_context_matches:true}});
    expect(feedback.tool_names).toEqual(['read_app_diagnostics:requests','read_app_diagnostics:dataset','read_app_diagnostics:jobs','read_task_status']);
    expect(feedback.output_lengths.every((length:number)=>length<4000)).toBe(true);
    expect(rows.filter(row=>row.route==='/v1/responses')).toHaveLength(5);expect(rows.some(row=>row.kind==='blocked_provider_url')).toBe(false);
    for(const value of [...canaries,ACCESS,REFRESH])expect(JSON.stringify(rows)).not.toContain(value);
    expect(taskEvents).toEqual([]);expect(prohibited).toEqual([]);expect(foreign).toEqual([]);expect(pageErrors).toEqual([]);
    expect(sha(fs.readFileSync(sourceSplitPath))).toBe(initialSplitHash);for(const row of originals)expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);
    expect(await page.evaluate(()=>typeof window.api)).toBe('undefined');
    await expect(assistant.getByRole('region',{name:'도우미 작업',exact:true}).getByRole('button',{name:'계획 확인하고 실행',exact:true})).toBeEnabled();
    await evidence.screenshot(page,'02-scoped-diagnostic-answer-and-unapproved-followup');
    await Promise.all(responseChecks);
    evidence.note('assistant_diagnostics',{actual_browser_cookie_csrf_task_ledger:true,controlled_provider_no_external_fallback:true,
      controlled_structured_409_and_empty_json:true,split_errors:splitErrors,task_reads_after_question:taskReads.slice(beforeReadCount),jobs_reads_after_question:jobsReads.length-beforeJobs,
      seed_task:{id:seed!.id,revision:seed!.revision,status:seed!.status,approval:seed!.approval},followup_task:{id:followup!.id,revision:followup!.revision,status:followup!.status,approval:followup!.approval},
      feedback,controlled_provider_response_calls:5,explicit_user_questions:2,automatic_provider_calls_before_questions:0,
      task_events:taskEvents,source_split_unchanged:true,originals,page_errors:pageErrors,forbidden_model_operations:prohibited,external_network_requests:foreign,
      actual_provider_or_SIWC_login:false,model_or_training_or_evaluation_execution:false,human_approval_or_truth:false,installed_app_updated:false});
  }finally{
    await Promise.all(responseChecks);
    evidence.note('assistant_diagnostics_observed_requests',{task_events:taskEvents,task_reads:taskReads,jobs_reads:jobsReads,split_errors:splitErrors,page_errors:pageErrors,external_network_requests:foreign,forbidden_model_operations:prohibited});
    evidence.note('assistant_diagnostics_close',await server.close());
  }
});

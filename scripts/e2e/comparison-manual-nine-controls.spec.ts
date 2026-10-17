import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page, Request, Route, Response} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {handoffProject, handoffWithin} from './fixtures/remaining-project-handoff';
import {png} from './qa/appFlow';

const harness = require('./fixtures/harness.cjs');
const JOBS = '/api/evaluation/model-comparisons/jobs';
const MODELS = '/api/evaluation/model-comparisons/models';
const HISTORY = '/api/evaluation/model-comparisons';
const CATALOG_ERROR = 'F057 controlled model catalog unavailable';
const SUBMIT_ERROR = 'F057 controlled comparison submit unavailable';
type Api = (route: string, body?: unknown, method?: string) => Promise<any>;
type Scope = {tag: 'A'|'B'; project: any; source: string; baseline: string; candidate: string;
  fixture: any; job: any; report: any; history: any; jobs: any; deployment: any};
const hash = (bytes: Buffer|string) => crypto.createHash('sha256').update(bytes).digest('hex');
const rawIdentity = (s: fs.BigIntStats) => [s.dev,s.ino,s.mode,s.nlink,s.size,s.mtimeNs,s.ctimeNs].map(String);
const query = (scope: Scope) => '?' + new URLSearchParams({source_dataset_path: scope.source,task:'classification'});
test.use({actionTimeout: 10_000});

// Controlled fixtures alone never establish training, representative quality,
// installed native acceptance or model deployment.
function retainedTree(root: string): Record<string,unknown> {
  const rows: Record<string,unknown> = {};
  const visit = (p: string) => {
    const before = fs.lstatSync(p,{bigint:true});
    expect(before.isSymbolicLink()).toBe(false);
    const name = path.relative(root,p).split(path.sep).join('/') || '.';
    if(before.isDirectory()) {
      const children = fs.readdirSync(p).sort();
      rows[name] = {kind:'directory',identity:rawIdentity(before),children};
      for(const child of children) visit(path.join(p,child));
      expect(fs.readdirSync(p).sort()).toEqual(children);
    } else {
      expect(before.isFile()).toBe(true); expect(before.nlink).toBe(1n);
      const fd = fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW|fs.constants.O_NONBLOCK);
      let body: Buffer;
      try {
        expect(rawIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(rawIdentity(before));
        body = fs.readFileSync(fd);
        expect(rawIdentity(fs.fstatSync(fd,{bigint:true}))).toEqual(rawIdentity(before));
      } finally { fs.closeSync(fd); }
      rows[name] = {kind:'file',identity:rawIdentity(before),sha256:hash(body!),size:body!.length};
    }
    expect(rawIdentity(fs.lstatSync(p,{bigint:true}))).toEqual(rawIdentity(before));
  };
  visit(root);return rows;
}

async function exercise(page: Page,w: Workspace,e: Evidence,api: Api,native: boolean,origin: string,url?: string) {
  const proofs: Record<string,any> = {},controls: Array<{close:()=>Promise<void>}> = [];
  const posts: Array<any> = [],writes: Array<any> = [],pageErrors: string[] = [];
  const captures: Array<any> = [];
  let serial = 0,observing = false,primary: unknown;
  const frame = <T>(label:string,fn:(deadline:number)=>Promise<T>) => {
    const deadline=performance.now()+10_000;return handoffWithin(fn(deadline),deadline,'F057 '+label);
  };
  const persist = (name:string,bytes:Buffer) => {
    const file=path.join(w.logs,`f057-${serial++}-${name}.json`);
    fs.writeFileSync(file,bytes,{flag:'wx'});e.addFile(file);
    return {file,sha256:hash(bytes),size:bytes.length};
  };
  const apiRead = (route:string,body?:unknown,method?:string) => frame('fixture/readback full body',()=>api(route,body,method));
  const make = async(tag:'A'|'B'):Promise<Scope> => {
    const source=path.join(w.root,'f057-nine-controls-'+tag);
    for(const split of ['train','val','test'])for(const label of ['OK','NG']) {
      const dir=path.join(source,split,label);fs.mkdirSync(dir,{recursive:true});
      fs.writeFileSync(path.join(dir,label+'.png'),png(64,3,(x,y)=>[label==='OK'?(tag==='A'?210:180):(tag==='A'?30:70),x,y]),{flag:'wx'});
    }
    await apiRead('/api/project/create',{name:'F057 controlled comparison '+tag,task:'classification'});
    await apiRead('/api/project/update',{source_dataset_dir:source},'PUT');
    await apiRead('/api/dataset/import',{folder_path:source,task:'classification'});
    const project=await apiRead('/api/project/current');expect(project.source_dataset_dir).toBe(source);
    const fixture=JSON.parse(execFileSync(harness.resolvePython(),[
      path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/comparison_models.py'),source,project.models_dir
    ],{cwd:harness.REPO_ROOT,env:{...process.env,VISION_AI_STUDIO_USER_DATA_DIR:w.userData},encoding:'utf8',timeout:60_000}));
    expect(fixture.checkpoint_kind).toBe('untrained_deterministic');
    const baseline=tag==='A'?'job_fixture_candidate':'job_fixture_incumbent';
    const candidate=tag==='A'?'job_fixture_incumbent':'job_fixture_candidate';
    const scope={tag,project,source,baseline,candidate,fixture} as Scope;
    // Genuine CPU comparison creates the retained history before the measured
    // UI failures. This API fixture POST is not counted as a successful UI action.
    const created=await apiRead(JOBS,{source_dataset_path:source,task:'classification',
      incumbent_job_id:baseline,candidate_job_id:candidate,max_images:4,full_test:true,
      execution_target:'local_cpu',compute_profile_id:null,device:'cpu',
      incumbent_task:'classification',candidate_task:'classification',
      incumbent_params:{threshold:.5},candidate_params:{threshold:.5}});
    await expect.poll(async()=>{
      scope.job=await apiRead(JOBS+'/'+created.job_id+query(scope));
      if(scope.job.status==='failed')throw Error(JSON.stringify(scope.job));
      return scope.job.status;
    },{timeout:60_000}).toBe('completed');
    scope.report=await apiRead(HISTORY+'/'+scope.job.report_id+query(scope));
    expect(scope.report.incumbent_job_id).toBe(baseline);expect(scope.report.candidate_job_id).toBe(candidate);
    expect(scope.report.images).toHaveLength(2);expect(scope.report.summary.error_images).toBe(0);
    scope.history=await apiRead(HISTORY+query(scope));scope.jobs=await apiRead(JOBS+query(scope));
    scope.deployment=await apiRead('/api/model-deployments/active'+query(scope));
    expect(scope.deployment.active).toBeNull();
    await apiRead('/api/model-deployments/history'+query(scope));
    return scope;
  };

  const A=await make('A'),B=await make('B');
  const custody=(s:Scope)=>({source:retainedTree(s.source),models:retainedTree(s.project.models_dir),
    annotations:retainedTree(s.project.annotations_dir),dataset:retainedTree(s.project.dataset_dir),
    comparison_reports:retainedTree(path.join(s.project.reports_dir,'model_comparisons'))});
  let before: {A:ReturnType<typeof custody>;B:ReturnType<typeof custody>}|undefined;
  const panel=()=>page.getByRole('region',{name:'현행과 후보 모델 비교',exact:true});
  const baseline=()=>panel().getByLabel('비교 기준 모델',{exact:true});
  const candidate=()=>panel().getByLabel('후보 모델',{exact:true});
  const submit=()=>panel().getByRole('button',{name:'동일 test 이미지로 비교',exact:true});
  const settle=()=>frame('two renderer effect frames',()=>page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r())))));
  const choosePair=async(s:Scope)=>{
    await expect(baseline()).toBeEnabled();await baseline().selectOption(s.baseline);
    await candidate().selectOption(s.candidate);await expect(baseline()).toHaveValue(s.baseline);
    await expect(candidate()).toHaveValue(s.candidate);await expect(submit()).toBeEnabled();
  };
  const enter=async(s:Scope)=>{
    await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(s.project.name);
    await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();
    await expect(baseline()).toBeEnabled();await choosePair(s);
  };
  const owner=(r:Request,s:Scope)=>{
    expect(r.frame()).toBe(page.mainFrame());expect(r.headers()['x-vision-project']).toBe(s.project.id);
    expect(JSON.parse(r.headers()['x-vision-context']).project_id).toBe(s.project.id);
  };
  const captureOriginal=async(response:Response,s:Scope,label:string)=>frame('original '+label,async deadline=>{
    owner(response.request(),s);expect(response.status()).toBe(200);
    const raw=await handoffWithin(response.body(),deadline,'F057 original raw body');
    expect(raw.length).toBeLessThanOrEqual(1024*1024);
    expect(await handoffWithin(response.finished(),deadline,'F057 original HTTP finish')).toBeNull();
    const body=JSON.parse(raw.toString('utf8'));
    captures.push({label,source:s.source,project:s.project.id,path:new URL(response.url()).pathname,
      original_owning_main_frame:true,status:200,finished:true,...persist(label,raw)});
    return body;
  });
  const refreshComparisonCatalog=async()=>{
    const toolbar=page.getByRole('heading',{name:'검증 및 품질 분석 스튜디오',exact:true}).locator('..').locator('..');
    const control=toolbar.getByRole('button',{name:'새로고침',exact:true});
    await expect(control).toHaveCount(1);await expect(control).toBeEnabled();
    await control.click({timeout:10_000});
  };
  const catalogMatches=(r:Request,s:Scope)=>{
    const u=new URL(r.url());return r.method()==='GET'&&r.frame()===page.mainFrame()&&u.origin===origin
      &&u.pathname===MODELS&&u.searchParams.get('source_dataset_path')===s.source
      &&u.searchParams.get('task')==='classification';
  };
  const genuineCatalog=async(s:Scope,label:string)=>frame('ordinary model choice catalog read',async deadline=>{
    const wire=page.waitForResponse(r=>catalogMatches(r.request(),s),{timeout:10_000});void wire.catch(()=>{});
    await refreshComparisonCatalog();const response=await handoffWithin(wire,deadline,'F057 genuine catalog');
    const body=await captureOriginal(response,s,label);
    expect(body.models.map((m:any)=>m.job_id).sort()).toEqual(['job_fixture_candidate','job_fixture_incumbent']);
    await expect(baseline()).toBeEnabled();await expect(candidate()).toBeEnabled();return body;
  });
  const savedReport=async(s:Scope,label:string)=>frame('ordinary saved comparison select',async deadline=>{
    const selector=panel().getByLabel('저장된 모델 비교',{exact:true});
    await expect(selector.locator('option[value="'+s.report.comparison_id+'"]')).toHaveCount(1);
    const wire=page.waitForResponse(r=>{
      const own=r.request(),u=new URL(r.url());return own.method()==='GET'&&own.frame()===page.mainFrame()
        &&u.origin===origin&&u.pathname===HISTORY+'/'+s.report.comparison_id
        &&u.searchParams.get('source_dataset_path')===s.source&&u.searchParams.get('task')==='classification';
    },{timeout:10_000});void wire.catch(()=>{});
    await selector.selectOption(s.report.comparison_id);
    const body=await captureOriginal(await handoffWithin(wire,deadline,'F057 original saved report'),s,label);
    expect(body).toEqual(s.report);await expect(selector).toHaveValue(s.report.comparison_id);
    await expect(panel()).toContainText(s.baseline+' → '+s.candidate);
    return body;
  });
  const readUnchanged=async(s:Scope)=>{
    expect(await apiRead(HISTORY+query(s))).toEqual(s.history);
    expect(await apiRead(JOBS+query(s))).toEqual(s.jobs);
    expect(await apiRead('/api/model-deployments/active'+query(s))).toEqual(s.deployment);
  };
  const observe=(r:Request)=>{
    const u=new URL(r.url());if(u.origin!==origin||!u.pathname.startsWith('/api/'))return;
    if(!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:u.pathname,body:r.postData()});
    if(r.method()==='POST'&&u.pathname===JOBS)posts.push({body:r.postDataJSON(),main_frame:r.frame()===page.mainFrame(),
      project:r.headers()['x-vision-project'],context:r.headers()['x-vision-context']});
  };
  const onPageError=(error:Error)=>pageErrors.push(error.message);

  // The error controls supply a disclosed 503 without forwarding the request.
  // The cancellation control holds and aborts the original POST before the
  // backend accepts a job. No synthetic success body or delegated POST exists.
  const control=async(s:Scope,kind:'catalog-503'|'submit-503'|'submit-abort')=>{
    let own:Request|undefined,armed=true,release!:()=>void,ready!:()=>void,done!:()=>void;
    let fail:unknown,routeDispositionError:unknown,deadline=0,closed=false;
    let capturedRoute:Route|undefined,routeSettled=false;
    const routeCloseErrors:string[]=[];
    const gate=new Promise<void>(r=>release=r),reached=new Promise<void>(r=>ready=r),completed=new Promise<void>(r=>done=r);
    let dispose!:(v:{kind:'finished'|'failed';failure:string|null})=>void;
    const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>dispose=r);
    const ended=(r:Request)=>{if(r===own)dispose({kind:'finished',failure:null});};
    const failed=(r:Request)=>{if(r===own)dispose({kind:'failed',failure:r.failure()?.errorText||null});};
    const pattern=origin+'/api/**';
    const handler=async(route:Route)=>{
      const r=route.request(),u=new URL(r.url());
      const match=kind==='catalog-503'?catalogMatches(r,s):r.method()==='POST'&&r.frame()===page.mainFrame()
        &&u.origin===origin&&u.pathname===JOBS&&r.postDataJSON()?.source_dataset_path===s.source;
      if(!armed||!match){await route.continue();return;}
      armed=false;own=r;capturedRoute=route;deadline=performance.now()+10_000;
      try {
        owner(r,s);
        if(kind!=='catalog-503') {
          const body=r.postDataJSON();expect(body.incumbent_job_id).toBe(s.baseline);
          expect(body.candidate_job_id).toBe(s.candidate);expect(body.execution_target).toBe('local_cpu');
          expect(body.compute_profile_id).toBeNull();expect(body.device).toBe('cpu');
        }
        ready();
        if(kind==='submit-abort') {
          await handoffWithin(gate,deadline,'F057 abandoned A submit release');
          try {await handoffWithin(route.abort('aborted'),deadline,'F057 owned POST abort');routeSettled=true;}
          catch(error) {routeDispositionError=error;}
        } else {
          const detail=kind==='catalog-503'?CATALOG_ERROR:SUBMIT_ERROR;
          await handoffWithin(route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail})}),
            deadline,'F057 owned failure response');routeSettled=true;
        }
      } catch(error) {
        fail=error;ready();
        // An assertion or failed fulfillment must not leave an intercepted
        // POST pending or allow it to reach the backend during cleanup.
        if(!routeSettled)try {
          await handoffWithin(route.abort('aborted'),performance.now()+10_000,'F057 failed owned route abort');
          routeSettled=true;
        }catch(closeError){routeCloseErrors.push(String(closeError));}
      } finally {done();}
    };
    page.on('requestfinished',ended);page.on('requestfailed',failed);await page.route(pattern,handler);
    const result={
      ready:async()=>{await handoffWithin(reached,performance.now()+10_000,'F057 control captured');if(fail)throw fail;expect(own).toBeTruthy();},
      finish:async()=>{
        release();await handoffWithin(completed,deadline,'F057 route joined');if(fail)throw fail;
        const result=await handoffWithin(disposition,deadline,'F057 original request disposition');
        if(kind==='submit-abort') {
          expect(result.kind).toBe('failed');expect(result.failure).toContain('ERR_ABORTED');routeSettled=true;
        }
        else {
          expect(result.kind).toBe('finished');const response=await handoffWithin(own!.response(),deadline,'F057 original controlled response');
          expect(response).not.toBeNull();expect(response!.status()).toBe(503);
          const raw=await handoffWithin(response!.body(),deadline,'F057 controlled original body');
          expect(JSON.parse(raw.toString('utf8'))).toEqual({detail:kind==='catalog-503'?CATALOG_ERROR:SUBMIT_ERROR});
          expect(await handoffWithin(response!.finished(),deadline,'F057 controlled original finish')).toBeNull();
          captures.push({label:kind,controlled_503:true,backend_503_claim:false,...persist(kind,raw)});
        }
        await settle();return {kind,request_disposition:result,controlled_transport:true,
          request_forwarded:false,server_job_created:false,server_job_cancel_claim:false,
          stale_route_disposition_error:routeDispositionError?String(routeDispositionError):null,route_close_errors:routeCloseErrors};
      },
      close:async()=>{
        if(closed)return;closed=true;release();let first:unknown;const closeDeadline=performance.now()+10_000;
        try {if(own)await handoffWithin(completed,closeDeadline,'F057 final route join');}catch(error){first=error;}
        if(capturedRoute&&!routeSettled)try {
          await handoffWithin(capturedRoute.abort('aborted'),closeDeadline,'F057 final captured route abort');
          routeSettled=true;
        }catch(error){routeCloseErrors.push(String(error));first??=error;}
        try {if(own)await handoffWithin(disposition,closeDeadline,'F057 final original Request terminal event');}
        catch(error){first??=error;}
        try {if(!page.isClosed())await handoffWithin(page.unroute(pattern,handler),closeDeadline,'F057 final unroute');}
        catch(error){first??=error;}
        finally {page.off('requestfinished',ended);page.off('requestfailed',failed);}
        if(first)throw first;
      }
    };
    controls.push(result);return result;
  };
  try {
    if(url)await page.goto(url);else await page.reload();
    // Finish each scope's ordinary lazy reads before freezing retained roots.
    // These warm-up reads are retained as original response evidence, and are
    // not silently excluded from filesystem preservation during measurement.
    await enter(B);await genuineCatalog(B,'B-initial-catalog');await choosePair(B);
    await savedReport(B,'B-initial-stored-report');await settle();
    await handoffProject(page,A,origin);await enter(A);
    await genuineCatalog(A,'A-initial-catalog');await choosePair(A);await savedReport(A,'A-initial-stored-report');
    await settle();before={A:custody(A),B:custody(B)};
    page.on('request',observe);page.on('pageerror',onPageError);observing=true;

    await baseline().selectOption(A.candidate);await expect(baseline()).toHaveValue(A.candidate);
    await expect(candidate()).toHaveValue(A.candidate);await expect(submit()).toBeDisabled();await settle();
    expect(posts).toHaveLength(0);proofs.invalid_baseline={pair:[A.candidate,A.candidate],submit_disabled:true,comparison_POSTs:0};
    await e.screenshot(page,'f057-invalid-baseline-same-job');await choosePair(A);
    await candidate().selectOption(A.baseline);await expect(baseline()).toHaveValue(A.baseline);
    await expect(candidate()).toHaveValue(A.baseline);await expect(submit()).toBeDisabled();await settle();
    expect(posts).toHaveLength(0);proofs.invalid_candidate={pair:[A.baseline,A.baseline],submit_disabled:true,comparison_POSTs:0};
    await e.screenshot(page,'f057-invalid-candidate-same-job');await choosePair(A);

    const catalogError=await control(A,'catalog-503');await refreshComparisonCatalog();await catalogError.ready();
    proofs.catalog_error=await catalogError.finish();await expect(panel().getByRole('alert')).toHaveText(CATALOG_ERROR);
    await expect(baseline()).toHaveValue(A.baseline);await expect(candidate()).toHaveValue(A.candidate);
    await expect(baseline()).toBeEnabled();await expect(candidate()).toBeEnabled();await expect(submit()).toBeEnabled();
    expect(posts).toHaveLength(0);await e.screenshot(page,'f057-catalog-error-exact-manual-pair');await catalogError.close();
    await genuineCatalog(A,'A-recovered-catalog');await expect(baseline()).toHaveValue(A.baseline);
    await expect(candidate()).toHaveValue(A.candidate);expect(A.report.summary.new_missed_ng).toBe(1);
    const qualityWarning=`NG 정답 중 기준은 NG, 후보는 OK로 판정한 이미지 ${A.report.summary.new_missed_ng}장 — 개별 사례 확인이 필요합니다.`;
    await expect(panel().getByRole('alert')).toHaveCount(1);await expect(panel().getByRole('alert')).toHaveText(qualityWarning);
    await savedReport(A,'A-recovered-stored-report');await expect(panel().getByRole('alert')).toHaveCount(1);
    await expect(panel().getByRole('alert')).toHaveText(qualityWarning);await e.screenshot(page,'f057-recovered-original-pair-and-report');

    const submitError=await control(A,'submit-503');await submit().click();await submitError.ready();
    proofs.submit_error=await submitError.finish();await expect(panel().getByRole('alert')).toHaveText(SUBMIT_ERROR);
    await expect(baseline()).toHaveValue(A.baseline);await expect(candidate()).toHaveValue(A.candidate);
    await expect(submit()).toBeEnabled();expect(posts).toHaveLength(1);await readUnchanged(A);
    await e.screenshot(page,'f057-submit-error-no-new-backend-job');await submitError.close();

    const abandoned=await control(A,'submit-abort');await submit().click();await abandoned.ready();
    await expect(panel()).toHaveAttribute('aria-busy','true');await expect(baseline()).toBeDisabled();
    await expect(candidate()).toBeDisabled();await expect(panel().getByRole('button',{name:'비교 실행 중…',exact:true})).toBeDisabled();
    expect(posts).toHaveLength(2);await e.screenshot(page,'f057-A-submit-held-before-server-acceptance');
    proofs.handoff_to_B=await handoffProject(page,B,origin);await enter(B);
    await genuineCatalog(B,'B-owning-catalog');await choosePair(B);
    const Bpair=[await baseline().inputValue(),await candidate().inputValue()];expect(Bpair).toEqual([B.baseline,B.candidate]);
    proofs.cancel=await abandoned.finish();await abandoned.close();
    await expect(baseline()).toHaveValue(B.baseline);await expect(candidate()).toHaveValue(B.candidate);
    await expect(submit()).toBeEnabled();await expect(panel()).toHaveAttribute('aria-busy','false');
    expect(B.report.summary.new_missed_ng).toBe(0);await expect(panel().getByRole('alert')).toHaveCount(0);expect(posts).toHaveLength(2);
    await savedReport(B,'B-retained-stored-report');await readUnchanged(B);
    await e.screenshot(page,'f057-B-excludes-aborted-A-state');
    proofs.handoff_to_A=await handoffProject(page,A,origin);await enter(A);
    await genuineCatalog(A,'A-return-catalog');await choosePair(A);
    await savedReport(A,'A-return-stored-report');await readUnchanged(A);
    await e.screenshot(page,'f057-A-stored-pair-history-return-unchanged');
    expect(posts).toHaveLength(2);
    for(const post of posts) {
      expect(post.main_frame).toBe(true);expect(post.project).toBe(A.project.id);
      expect(JSON.parse(post.context).project_id).toBe(A.project.id);
      expect(post.body.incumbent_job_id).toBe(A.baseline);expect(post.body.candidate_job_id).toBe(A.candidate);
      expect(post.body.source_dataset_path).toBe(A.source);
    }
    expect(writes.filter(r=>r.path!==JOBS&&r.path!=='/api/project/open')).toEqual([]);
    const after={A:custody(A),B:custody(B)};expect(after).toEqual(before);expect(pageErrors).toEqual([]);
    proofs.custody={before,after,all_complete_roots_unchanged:true,SQLite_raw_identity_claim:false};
    proofs.fixture={A,B,actual_CPU_fixture_inference:true,actual_training:false,untrained_deterministic:true};
    proofs.posts=posts;proofs.writes=writes;proofs.original_body_captures=captures;
  } catch(error) {primary=error;} finally {
    for(const item of controls.reverse())try {await item.close();}catch(error){primary??=error;}
    // Preservation checks are attempted even after the first scenario failure.
    if(before&&!proofs.custody) {
      const after:Record<string,unknown>={},checks:Record<string,boolean>={};
      for(const scope of [A,B])try {
        after[scope.tag]=custody(scope);expect(after[scope.tag]).toEqual(before[scope.tag]);checks[scope.tag]=true;
      }catch(error){checks[scope.tag]=false;primary??=error;}
      proofs.custody={before,after,checks,all_complete_roots_unchanged:checks.A===true&&checks.B===true,
        SQLite_raw_identity_claim:false,all_retained_roles_attempted:true};
    }
    if(observing){page.off('request',observe);page.off('pageerror',onPageError);}
    proofs.posts=posts;proofs.writes=writes;proofs.original_body_captures=captures;proofs.page_errors=pageErrors;
    proofs.resource_closure={routes_joined_and_removed:primary===undefined,request_observers_removed:true};
    proofs.proposed_cells=['comparison-manual-baseline','comparison-manual-candidate','comparison-manual-submit']
      .flatMap(action=>['invalid','error','cancel'].map(dimension=>'F057.'+action+'.'+dimension));
    proofs.cancel_meaning='Owning A-to-B abandonment of an intercepted pre-acceptance submit; no server-job cancel, no per-selector cancel button.';
    proofs.cancel_cells_require_independent_semantic_acceptance=true;
    proofs.unsaved_pair_reopen_persistence_claim=false;proofs.mode=native?'source-electron':'browser';
    proofs.production_changes=false;proofs.model_deployment=false;proofs.quality_approved=false;
    proofs.installed_native_accepted=false;proofs.parent_completion_credit=false;
    const file=path.join(w.logs,'f057-comparison-nine-controls-proof.json');
    fs.writeFileSync(file,JSON.stringify(proofs),{flag:'wx'});e.addFile(file);e.note('f057_comparison_nine_controls',proofs);
  }
  if(primary!==undefined)throw primary;
}

test('manual comparison same-job guards and controlled errors preserve pair before abandoned A submit in B',
  async({page,renderer,workspace,evidence})=>{
    page.setDefaultTimeout(10_000);await installDesktopHostShim(page,renderer.port);
    const api:Api=async(route,body,method)=>{
      const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),
        ...(body===undefined?{}:{data:body}),timeout:10_000});
      expect(response.ok(),await response.text()).toBe(true);return response.json();
    };
    await exercise(page,workspace,evidence,api,false,renderer.origin,renderer.url);
  });

test('native manual comparison invalid pair errors and canceled A submit preserve distinct B and stored history',
  {tag:'@electron'},async({electronSession,workspace,evidence})=>{
    expect(process.env.MV_E2E_NATIVE_FULL_BODY_READS,'F057 needs original native HTTP bodies').toBe('1');
    const page=electronSession.window,status=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${status.port}`;
    page.setDefaultTimeout(10_000);
    const api:Api=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{
      const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),10_000);
      try {
        const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),signal:abort.signal,
          ...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
        if(!response.ok)throw Error(`Owned F057 API ${response.status}: ${await response.text()}`);
        return await response.json();
      } finally {clearTimeout(timer);}
    },{origin,route,body,method});
    await exercise(page,workspace,evidence,api,true,origin);
  });

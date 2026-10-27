import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// Controlled presentation regression. The renderer and isolated project/import are real; the saved flow,
// source-qualified model metadata and empty evaluation history below are explicit synthetic responses.
// This test does not train, infer, declare truth, approve quality, deploy, or connect a provider.
test.use({actionTimeout:12_000,trace:'off'});

test('guided Step 5 preserves advanced edits and makes evaluation prerequisites readable',async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(160_000);
  await installDesktopHostShim(page,renderer.port);
  const api=async(route:string,body?:unknown,method?:string)=>{
    const reply=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});
    expect(reply.ok(),route+': '+await reply.text()).toBe(true);return reply.json();
  };
  const source=fs.realpathSync(workspace.dataset);
  const project=await api('/api/project/create',{name:'Synthetic Step 5 preparation',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification',validate_images:true});
  const sourceHashes=workspace.images.map(row=>({path:row.path,sha256:crypto.createHash('sha256').update(fs.readFileSync(row.path)).digest('hex')}));
  const version='5'.repeat(32),graphHash='a'.repeat(64),job='job_step5_controlled_metadata';
  const graph={id:'step5-controlled',name:'기본 검사 순서',description:'합성 메타데이터 · 모델 실행 없음',nodes:[
    {id:'input',position:{x:40,y:160},data:{label:'검사 이미지',node_type:'input'}},
    {id:'model',position:{x:340,y:160},data:{label:'표면 분류 모델',node_type:'inspection',task:'classification',model_job_id:job,threshold:.5}},
    {id:'decision',position:{x:640,y:160},data:{label:'최종 판정',node_type:'decision',rule:'any_defect_is_ng'}},
    {id:'output',position:{x:940,y:160},data:{label:'검사 결과',node_type:'output'}}],edges:[
    {id:'input-model',source:'input',target:'model',payload_type:'image'},
    {id:'model-decision',source:'model',target:'decision',payload_type:'result'},
    {id:'decision-output',source:'decision',target:'output',payload_type:'result'}]};
  const model={job_id:job,task:'classification',label:'합성 완료 모델',preset:null,created_at:'2026-10-27T00:00:00Z',best_metric:null,
    source_dataset_path:source,class_names:['ok','ng'],class_ids:[0,1],training_labelset_id:project.active_labelset_id||'default'};
  const saved={version_id:version,name:graph.name,saved_at:'2026-10-27T00:00:00Z',is_active:true,pipeline_hash:graphHash,recipe_task:'classification'};
  const scope={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default',task:'classification',classes:['ok','ng'],
    class_semantics:{version:1,roles:{ok:'normal',ng:'defect'},basis:{ok:'explicit',ng:'explicit'}}};
  const controlled:any[]=[],writes:any[]=[],forbidden:any[]=[],external:string[]=[];
  let draftFailures=0;
  page.on('request',r=>{const url=new URL(r.url());if(!['http:','https:'].includes(url.protocol))return;
    if(url.origin!==renderer.origin)external.push(r.url());
    if(url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({path:url.pathname,method:r.method(),body:r.postData()});
  });
  await page.route(renderer.origin+'/api/**',async route=>{
    const r=route.request(),url=new URL(r.url()),p=url.pathname;
    const reply=async(body:unknown,status=200)=>{controlled.push({path:p,method:r.method(),status});await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});};
    if(p==='/api/flowchart/pipeline/active/record')return reply({version_id:version,pipeline:graph});
    if(p==='/api/flowchart/pipeline/active-version')return reply({version_id:version});
    if(p==='/api/flowchart/pipelines')return reply({pipelines:[saved],total:1});
    if(p==='/api/flowchart/pipelines/'+version)return reply(graph);
    if(p==='/api/flowchart/models/catalog')return reply({models:[model],total:1});
    if(p==='/api/flowchart/models/verify'){
      expect(r.method()).toBe('POST');expect(r.postDataJSON()).toEqual({source_dataset_path:source,models:[{job_id:job,task:'classification'}]});
      return reply({verified_job_ids:[job]});
    }
    if(p==='/api/flow-evaluations/cohorts'&&r.method()==='GET')return reply({cohorts:[],total:0});
    if(p==='/api/flow-evaluations'&&r.method()==='GET')return reply({evaluations:[],total:0});
    if(p==='/api/flow-evaluations/scope/'+version)return reply(scope);
    if(p==='/api/flowchart/draft'&&r.method()==='PUT'){
      draftFailures++;return reply({detail:'Controlled autosave refusal keeps this UI edit unsaved'},503);
    }
    if(p.startsWith('/api/training/')||p.startsWith('/api/inference/')||p.startsWith('/api/evaluation/')||p==='/api/flowchart/run'
      ||p.startsWith('/api/flowchart/partial-run')||p.startsWith('/api/assistant/')||p.startsWith('/api/flow-evaluations')&&r.method()!=='GET'
      ||p==='/api/image-truth'&&r.method()!=='GET'){
      forbidden.push({path:p,method:r.method()});return reply({detail:'This controlled UI test never executes models, truth writes, or providers'},503);
    }
    return route.continue();
  });
  await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  // The shared fixture starts full view. Use the real product toggle to select the beginner surface.
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
  const prepare=page.getByRole('region',{name:'검사 준비',exact:true});
  await expect(prepare).toBeVisible();await expect(prepare).toHaveAttribute('aria-busy','false');
  await expect(prepare.getByRole('heading',{name:'검사 준비',exact:true})).toBeVisible();
  await expect(page.locator('[aria-label="플로우 그래프 캔버스"]')).toBeHidden();
  const next=prepare.getByRole('button',{name:'시험 이미지로 검사 확인하기',exact:true});
  await expect(next).toBeEnabled();
  await expect(prepare.locator('[data-primary-action="true"]:visible')).toHaveCount(1);
  await expect(prepare.getByText('결함 하나라도 NG',{exact:false})).toBeVisible();
  await evidence.screenshot(page,'01-guided-preparation');

  await next.focus();await page.keyboard.press('Enter');
  const evaluation=page.getByRole('region',{name:'전체 흐름 평가',exact:true});
  await expect(evaluation).toBeVisible();
  await expect(evaluation.getByText(/4단계에서는 모델 자체의 성능을 확인/)).toBeVisible();
  const freeze=evaluation.getByRole('button',{name:'시험 이미지 묶음 고정',exact:true});
  await expect(freeze).toBeEnabled();
  await expect(evaluation.getByRole('button',{name:'선택 코호트 평가',exact:true})).toHaveCount(0);
  await expect(prepare.locator('[data-primary-action="true"]:visible')).toHaveCount(0);
  await expect(evaluation.locator('[data-primary-action="true"]:visible')).toHaveCount(1);
  await evidence.screenshot(page,'02-evaluation-prerequisite-no-run');
  await prepare.getByRole('button',{name:'검사 준비로 돌아가기',exact:true}).click();
  await prepare.getByRole('button',{name:'고급 편집',exact:true}).click();
  const canvas=page.locator('[aria-label="플로우 그래프 캔버스"]');
  await expect(canvas).toBeVisible();await expect(canvas.locator('[data-flow-node-id]')).toHaveCount(4);
  const originalEdges=await canvas.locator('[data-flow-edge]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-flow-edge')));
  const modelNode=canvas.locator('[data-flow-node-id="model"]');
  await modelNode.getByRole('heading',{name:'표면 분류 모델',exact:true}).click();
  const label=page.getByRole('textbox',{name:'노드 명칭',exact:true});
  await expect(label).toHaveValue('표면 분류 모델');
  await label.fill('검토 중인 모델 이름');
  await expect(page.getByText('Controlled autosave refusal keeps this UI edit unsaved',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'검사 준비로 돌아가기',exact:true}).click();
  await expect(prepare).toBeVisible();await expect(canvas).toBeHidden();
  await expect(prepare.getByText('검토 중인 모델 이름',{exact:true})).toBeVisible();
  await expect(prepare.getByRole('button',{name:'이 검사 흐름 저장하기',exact:true})).toBeEnabled();
  await expect(prepare.getByText('저장 필요',{exact:true})).toBeVisible();
  await expect(prepare.getByText('평가 근거 확인됨',{exact:true})).toHaveCount(0);
  await prepare.getByRole('button',{name:'고급 편집',exact:true}).click();
  await expect(label).toHaveValue('검토 중인 모델 이름');
  expect(await canvas.locator('[data-flow-edge]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-flow-edge')))).toEqual(originalEdges);
  await expect(page.getByRole('button',{name:'플로우 실행 취소',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'검사 준비로 돌아가기',exact:true}).click();
  await evidence.screenshot(page,'03-guided-return-preserves-unsaved-custom-graph');

  const narrow:any[]=[];
  await page.setViewportSize({width:390,height:844});
  for(const theme of ['dark','light']){
    // The theme attribute is a presentation-only test control, never a workflow/store mutation.
    await page.evaluate(value=>document.documentElement.setAttribute('data-theme',value),theme);
    await expect(prepare).toBeVisible();
    const bounds=await prepare.evaluate(el=>({client:el.clientWidth,scroll:el.scrollWidth,viewport:innerWidth}));
    expect(bounds.scroll).toBeLessThanOrEqual(bounds.client);narrow.push({theme,...bounds});
    await prepare.getByRole('button',{name:'고급 편집',exact:true}).focus();
    await expect(prepare.getByRole('button',{name:'고급 편집',exact:true})).toBeFocused();
    await page.keyboard.press('Enter');await expect(canvas).toBeVisible();
    await page.getByRole('button',{name:'검사 준비로 돌아가기',exact:true}).focus();await page.keyboard.press('Enter');
    await expect(prepare).toBeVisible();await expect(prepare.getByText('검토 중인 모델 이름',{exact:true})).toBeVisible();
    await evidence.screenshot(page,'04-guided-390-'+theme);
  }
  expect(forbidden).toEqual([]);expect(external).toEqual([]);expect(draftFailures).toBe(1);
  expect(writes.filter(row=>row.path!=='/api/flowchart/models/verify'&&row.path!=='/api/flowchart/draft')).toEqual([]);
  for(const row of sourceHashes)expect(crypto.createHash('sha256').update(fs.readFileSync(row.path)).digest('hex')).toBe(row.sha256);
  const summary={scope:'controlled_metadata_UI_regression',actual_renderer:true,actual_isolated_project_and_import:true,
    controlled_saved_flow_and_model_verification:true,controlled_autosave_503_preserves_unsaved_state:true,
    graph_roundtrip_retained:true,nodes:graph.nodes.map(row=>row.id),edges:originalEdges,keyboard:true,narrow,
    controlled,writes,forbidden,external,sourceHashes,model_execution:false,training:false,provider_calls:false,
    real_provider_or_model_quality_verified:false,installed_app_updated:false};
  const record=path.join(workspace.logs,'step5-guided-summary.json');fs.writeFileSync(record,JSON.stringify(summary,null,2));
  evidence.addFile(record);evidence.note('step5_guided_preparation',summary);
});

test('all-unknown saved evaluation keeps truth review required and enhancement model visible',async({page,request,renderer,workspace,evidence})=>{
  test.setTimeout(120_000);
  await installDesktopHostShim(page,renderer.port);
  const api=async(route:string,body?:unknown,method?:string)=>{
    const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});
    expect(r.ok(),route+': '+await r.text()).toBe(true);return r.json();
  };
  const source=fs.realpathSync(workspace.dataset);
  const project=await api('/api/project/create',{name:'Synthetic unknown-truth preparation',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:source},'PUT');
  await api('/api/dataset/import',{folder_path:source,task:'classification',validate_images:true});
  const sourceHashes=workspace.images.map(row=>({path:row.path,sha256:crypto.createHash('sha256').update(fs.readFileSync(row.path)).digest('hex')}));
  const version='6'.repeat(32),graphHash='b'.repeat(64),modelJob='job_step5_unknown_classifier',enhanceJob='job_step5_unknown_enhancement';
  const graph={id:'step5-controlled-enhancement',name:'영상 개선과 분류 검사',nodes:[
    {id:'input',position:{x:40,y:160},data:{label:'검사 이미지',node_type:'input'}},
    {id:'enhance',position:{x:340,y:160},data:{label:'영상 개선 보조 모델',node_type:'preprocess',task:'enhancement',model_job_id:enhanceJob,params:{operation:'enhancement'}}},
    {id:'model',position:{x:640,y:160},data:{label:'불량 분류 모델',node_type:'inspection',task:'classification',model_job_id:modelJob,threshold:.5}},
    {id:'decision',position:{x:940,y:160},data:{label:'최종 판정',node_type:'decision',rule:'any_defect_is_ng'}},
    {id:'output',position:{x:1240,y:160},data:{label:'검사 결과',node_type:'output'}}],edges:[
    {id:'input-enhance',source:'input',target:'enhance',payload_type:'image'},
    {id:'enhance-model',source:'enhance',target:'model',payload_type:'roi'},
    {id:'model-decision',source:'model',target:'decision',payload_type:'result'},
    {id:'decision-output',source:'decision',target:'output',payload_type:'result'}]};
  const modelBase={preset:null,created_at:'2026-10-27T00:00:00Z',best_metric:null,source_dataset_path:source,training_labelset_id:project.active_labelset_id||'default'};
  const models=[{...modelBase,job_id:enhanceJob,task:'enhancement',label:'영상 개선 보조 모델',class_names:[],class_ids:[],
    capabilities:{role:'preprocess',operation:'enhancement',native_source_coordinates:true}},
    {...modelBase,job_id:modelJob,task:'classification',label:'불량 분류 모델',class_names:['ok','ng'],class_ids:[0,1]}];
  const saved={version_id:version,name:graph.name,saved_at:'2026-10-27T00:00:00Z',is_active:true,pipeline_hash:graphHash,recipe_task:'classification'};
  const scope={project_id:project.id,source_dataset_path:source,labelset_id:project.active_labelset_id||'default',task:'classification',classes:['ok','ng'],
    class_semantics:{version:1,roles:{ok:'normal',ng:'defect'},basis:{ok:'explicit',ng:'explicit'}}};
  const cohort={cohort_id:'controlled-unknown-cohort',name:'정답 미확인 시험 묶음',created_at:'2026-10-27T00:00:00Z',scope,count:2,split:'test',
    record_sha256:'c'.repeat(64),input_sha256:'d'.repeat(64),truth_sha256:'e'.repeat(64),split_sha256:'f'.repeat(64)};
  const records=workspace.images.map((row,index)=>({relative_path:path.relative(workspace.dataset,row.path),image_path:fs.realpathSync(row.path),input_sha256:sourceHashes[index].sha256,
    truth_sha256:'e'.repeat(64),truth:'UNKNOWN',truth_reason:'not_reviewed',decision:'NG',execution_status:'completed',rejection_reason:'',node_evidence:[],roi_evidence:[],error:null}));
  const result={evaluation_id:'controlled_all_unknown',created_at:'2026-10-27T00:00:00Z',status:'completed',version_id:version,cohort_id:cohort.cohort_id,scope,
    graph_sha256:graphHash,cohort_sha256:cohort.record_sha256,truth_sha256:cohort.truth_sha256,input_sha256:cohort.input_sha256,
    model_sha256:'7'.repeat(64),record_sha256:'8'.repeat(64),device:'cpu',coverage:{total:2,known:0,unknown:2,invalidated:0,known_fraction:0},
    confusion:{OK:{OK:0,NG:0,REVIEW:0},NG:{OK:0,NG:0,REVIEW:0}},
    metrics:{escape_rate:null,overkill_rate:null,review_rate:0,escape_unavailable_reason:'no_defect_truth',overkill_unavailable_reason:'no_normal_truth',normal_count:0,defect_count:0},
    validity:{valid:true,reasons:[]},records,escapes:[],overkills:[],unknown_truth:records,errors:[]};
  const {records:_records,escapes:_escapes,overkills:_overkills,unknown_truth:_unknown,errors:_errors,...history}=result;
  const controlled:any[]=[],writes:any[]=[],forbidden:any[]=[],external:string[]=[];
  let resultReads=0;
  page.on('request',r=>{const u=new URL(r.url());if(!['http:','https:'].includes(u.protocol))return;
    if(u.origin!==renderer.origin)external.push(r.url());
    if(u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({path:u.pathname,method:r.method(),body:r.postData()});
  });
  await page.route(renderer.origin+'/api/**',async route=>{
    const r=route.request(),p=new URL(r.url()).pathname;
    const reply=async(body:unknown,status=200)=>{controlled.push({path:p,method:r.method(),status});await route.fulfill({status,contentType:'application/json',body:JSON.stringify(body)});};
    if(p==='/api/flowchart/pipeline/active/record')return reply({version_id:version,pipeline:graph});
    if(p==='/api/flowchart/pipeline/active-version')return reply({version_id:version});
    if(p==='/api/flowchart/pipelines')return reply({pipelines:[saved],total:1});
    if(p==='/api/flowchart/pipelines/'+version)return reply(graph);
    if(p==='/api/flowchart/models/catalog')return reply({models,total:2});
    if(p==='/api/flowchart/models/verify'){
      expect(r.method()).toBe('POST');expect(r.postDataJSON()).toEqual({source_dataset_path:source,models:[{job_id:enhanceJob,task:'enhancement'},{job_id:modelJob,task:'classification'}]});
      return reply({verified_job_ids:[enhanceJob,modelJob]});
    }
    if(p==='/api/flow-evaluations/cohorts'&&r.method()==='GET')return reply({cohorts:[cohort],total:1});
    if(p==='/api/flow-evaluations'&&r.method()==='GET')return reply({evaluations:[history],total:1});
    if(p==='/api/flow-evaluations/scope/'+version)return reply(scope);
    if(p==='/api/flow-evaluations/approvals/active'&&r.method()==='GET')return reply(null);
    if(p==='/api/flow-evaluations/'+result.evaluation_id&&r.method()==='GET'){resultReads++;return reply(result);}
    if(p.startsWith('/api/training/')||p.startsWith('/api/inference/')||p.startsWith('/api/evaluation/')||p==='/api/flowchart/run'
      ||p.startsWith('/api/flowchart/partial-run')||p.startsWith('/api/assistant/')||p.startsWith('/api/flow-evaluations')&&r.method()!=='GET'
      ||p==='/api/image-truth'&&r.method()!=='GET'||p==='/api/flowchart/draft'&&r.method()!=='GET'){
      forbidden.push({path:p,method:r.method()});return reply({detail:'Controlled read-only unknown-truth regression refuses mutation or model execution'},503);
    }
    return route.continue();
  });
  await page.goto(renderer.url);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  await page.getByRole('button',{name:'간단히 보기',exact:true}).click();
  await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
  const prepare=page.getByRole('region',{name:'검사 준비',exact:true});
  await expect(prepare).toHaveAttribute('aria-busy','false');
  await expect(prepare.getByText('영상 개선 보조 모델',{exact:true})).toBeVisible();
  await expect(prepare.getByText('학습형 영상 개선',{exact:true})).toBeVisible();
  await expect(prepare.getByText('불량 분류 모델',{exact:true})).toBeVisible();
  await expect(prepare.getByRole('button',{name:'시험 이미지로 검사 확인하기',exact:true})).toBeEnabled();
  await evidence.screenshot(page,'05-enhancement-and-classification-models-listed');
  await prepare.getByRole('button',{name:'시험 이미지로 검사 확인하기',exact:true}).click();
  const evaluation=page.getByRole('region',{name:'전체 흐름 평가',exact:true});
  await expect(evaluation.getByRole('textbox',{name:'정답 일괄 확정 검토자',exact:true})).toBeVisible();
  await expect(evaluation.getByRole('button',{name:'라벨을 정답으로 확정',exact:true}).first()).toBeDisabled();
  await evaluation.locator('summary').filter({hasText:'자세히 · 흐름 버전·시험 묶음·정답·평가 이력'}).click();
  await evaluation.getByRole('combobox',{name:'전체 흐름 평가 이력',exact:true}).selectOption(result.evaluation_id);
  await expect(evaluation.getByText(/저장된 평가 근거 유효.*정답 확인 0\/2/)).toBeVisible();
  await expect(evaluation.getByRole('textbox',{name:'정답 일괄 확정 검토자',exact:true})).toBeVisible();
  await expect(evaluation.getByRole('button',{name:'라벨을 정답으로 확정',exact:true}).first()).toBeDisabled();
  expect(resultReads).toBe(1);
  await evidence.screenshot(page,'06-valid-receipt-with-zero-reviewed-truth-remains-human-gated');
  await prepare.getByRole('button',{name:'검사 준비로 돌아가기',exact:true}).click();
  await expect(prepare.getByText('저장됨',{exact:true})).toBeVisible();
  await expect(prepare.getByText('평가 근거 확인 필요',{exact:true})).toBeVisible();
  await expect(prepare.getByText('평가 근거 확인됨',{exact:true})).toHaveCount(0);
  await expect(prepare.getByRole('button',{name:'6단계 검사 실행으로 이동',exact:true})).toHaveCount(0);
  await expect(prepare.getByRole('button',{name:'시험 이미지로 검사 확인하기',exact:true})).toBeEnabled();
  await expect(prepare.getByText('영상 개선 보조 모델',{exact:true})).toBeVisible();
  expect(writes.filter(row=>row.path!=='/api/flowchart/models/verify')).toEqual([]);
  expect(forbidden).toEqual([]);expect(external).toEqual([]);
  for(const row of sourceHashes)expect(crypto.createHash('sha256').update(fs.readFileSync(row.path)).digest('hex')).toBe(row.sha256);
  await evidence.screenshot(page,'07-unknown-truth-guide-stays-at-evaluation');
  const summary={scope:'controlled_metadata_unknown_truth_and_enhancement_UI_regression',actual_renderer:true,actual_isolated_project_and_import:true,
    controlled_saved_flow_and_model_verification:true,controlled_validity:true,version_and_graph_matched:true,known_truth:0,unknown_truth:2,
    history_record_explicitly_selected:true,resultReads,enhancement_model_displayed:true,guide_evaluation_not_ready:true,
    truth_review_remains_human:true,mutation_requests:writes,forbidden,external,controlled,sourceHashes,
    model_execution:false,training:false,provider_calls:false,truth_declaration:false,quality_approved:false,installed_app_updated:false};
  const record=path.join(workspace.logs,'step5-guided-unknown-truth-summary.json');fs.writeFileSync(record,JSON.stringify(summary,null,2));
  evidence.addFile(record);evidence.note('step5_guided_unknown_truth',summary);
});

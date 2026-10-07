import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
// eslint-disable-next-line @typescript-eslint/no-require-imports
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown)=>Promise<any>;
test.use({actionTimeout:15_000});
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const library='/api/flow-workspace/templates';

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
  const prefix=native?'native':'browser';
  const python=(file:string,args:string[])=>JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures',file),...args],{encoding:'utf8',timeout:30_000}));
  const fixture=python('flow_comparison_control.py',[workspace.root]);
  await api('/api/project/create',{name:'Template safety fixture',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:fixture.source});await api('/api/dataset/import',{folder_path:fixture.source,task:'classification'});
  const project=await api('/api/project/current');python('comparison_models.py',[fixture.source,project.models_dir]);
  const graph=python('flow_template_control.py',[JSON.stringify(project)]);
  const checkpoint=path.join(project.models_dir,graph.job_id,'best_model.pt'),checkpointSha=sha(fs.readFileSync(checkpoint));
  const versionFile=path.join(project.project_dir,'flowcharts/versions',graph.version+'.json'),versionSha=sha(fs.readFileSync(versionFile));
  const open=async()=>{
    if(url)await page.goto(url);else await page.reload();
    await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
    await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
    await page.getByLabel('저장 버전',{exact:true}).selectOption(graph.version);
    const details=page.locator('details').filter({has:page.getByText('내 템플릿 · 모델·클래스 매핑',{exact:true})}).first();
    if(await details.getAttribute('open')===null)await details.locator('summary').first().click();
  };
  await open();expect((await api(library)).templates).toHaveLength(0);
  const name=page.getByLabel('템플릿 이름',{exact:true}),whole=page.getByRole('button',{name:'전체 흐름 저장',exact:true});
  const saveResponse=()=>page.waitForResponse(r=>new URL(r.url()).pathname===library&&r.request().method()==='POST');
  for(const input of ['', 'X'.repeat(121)]){
    await name.fill(input);const waiting=saveResponse();await whole.click();expect((await waiting).status()).toBe(422);
    await expect(page.getByRole('alert')).toBeVisible();expect((await api(library)).templates).toHaveLength(0);expect(sha(fs.readFileSync(versionFile))).toBe(versionSha);
  }
  await name.fill('Abandoned unsubmitted whole');await open();expect((await api(library)).templates).toHaveLength(0);await expect(name).not.toHaveValue('Abandoned unsubmitted whole');
  await name.fill('Owned reviewed whole');
  await page.route('**/api/flow-workspace/templates',r=>r.request().method()==='POST'?r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned template transport unavailable'})}):r.continue());
  await whole.click();await expect(page.getByRole('alert')).toContainText('Owned template transport unavailable');expect((await api(library)).templates).toHaveLength(0);
  await page.unroute('**/api/flow-workspace/templates');let waiting=saveResponse();await whole.click();const saved=await waiting;expect(saved.status()).toBe(200);const template=await saved.json();expect(template.kind).toBe('flow');
  const subset=page.getByText('부분 흐름으로 저장할 노드 선택',{exact:true}).locator('..');await subset.locator('summary').click();
  const nodes=graph.pipeline.nodes.filter((n:any)=>['fixed_roi','inspection'].includes(n.data.node_type));
  for(const node of nodes)await subset.getByRole('checkbox',{name:node.data.label,exact:true}).check();
  const partial=page.getByRole('button',{name:'선택 노드 저장',exact:true});
  for(const input of ['', 'X'.repeat(121)]){await name.fill(input);waiting=saveResponse();await partial.click();expect((await waiting).status()).toBe(422);expect((await api(library)).templates).toHaveLength(1);}
  await name.fill('Owned reviewed subset');
  await page.route('**/api/flow-workspace/templates',r=>r.request().method()==='POST'?r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned subset transport unavailable'})}):r.continue());
  await partial.click();await expect(page.getByRole('alert')).toContainText('Owned subset transport unavailable');expect((await api(library)).templates).toHaveLength(1);
  await page.unroute('**/api/flow-workspace/templates');waiting=saveResponse();await partial.click();const partialReply=await waiting;expect(partialReply.status()).toBe(200);const module=await partialReply.json();
  expect(module.kind).toBe('subgraph');expect(module.pipeline.nodes.map((n:any)=>n.id).sort()).toEqual(nodes.map((n:any)=>n.id).sort());expect(module.ports.inputs).toHaveLength(1);expect(module.ports.outputs).toHaveLength(1);
  const savedLibrary=(await api(library)).templates;expect(savedLibrary).toHaveLength(2);
  const ownedLibrary=path.join(workspace.home,'.modu_vision','flow_templates');
  const templateFiles=fs.readdirSync(ownedLibrary,{recursive:true}).map(String).filter(file=>file.endsWith('.json')).map(file=>path.join(ownedLibrary,file));
  expect(templateFiles).toHaveLength(2);
  const templateHashes=templateFiles.map(file=>({path:file,sha256:sha(fs.readFileSync(file))}));
  expect(templateFiles.map(file=>JSON.parse(fs.readFileSync(file,'utf8')).template_id).sort()).toEqual([template.template_id,module.template_id].sort());
  await name.fill('Abandoned unsubmitted subset');await open();expect((await api(library)).templates).toEqual(savedLibrary);await expect(name).not.toHaveValue('Abandoned unsubmitted subset');
  await page.getByLabel('내 템플릿 선택',{exact:true}).selectOption(module.template_id);expect((await api(library)).templates.find((r:any)=>r.template_id===module.template_id)).toEqual(module);
  await page.getByLabel('내 템플릿 선택',{exact:true}).selectOption(template.template_id);
  const model=page.getByLabel('ROI 결함 검사 템플릿 모델 매핑',{exact:true}),className=page.getByLabel('ROI 결함 검사 · 이름 NG 매핑',{exact:true});
  const apply=page.getByRole('button',{name:'모델·클래스 매핑 후 초안으로 열기',exact:true});
  const draftSaved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/flowchart/draft'&&r.request().method()==='PUT');await page.getByRole('button',{name:'초안 저장',exact:true}).click();expect((await draftSaved).status()).toBe(200);
  await expect(page.getByRole('status').filter({hasText:'✓ 편집 초안이 저장되었습니다.'})).toBeVisible();const beforeDraft=await api('/api/flowchart/draft');
  let mapRequests=0;const listener=(r:any)=>{if(new URL(r.url()).pathname===library+'/'+template.template_id+'/map'&&r.method()==='POST')mapRequests++;};page.on('request',listener);
  page.once('dialog',dialog=>dialog.dismiss());await apply.click();await expect(apply).toBeEnabled();expect(mapRequests).toBe(0);expect((await api('/api/flowchart/draft')).draft_sha256).toBe(beforeDraft.draft_sha256);
  const mapResponse=()=>page.waitForResponse(r=>new URL(r.url()).pathname===library+'/'+template.template_id+'/map'&&r.request().method()==='POST');
  const mappingAttempt=async()=>{page.once('dialog',dialog=>dialog.accept());const reply=mapResponse();await apply.click();return reply;};
  let mapped=await mappingAttempt();expect(mapped.status()).toBe(422);await expect(page.getByRole('alert')).toContainText('mapping');
  await model.selectOption(graph.job_id);mapped=await mappingAttempt();expect(mapped.status()).toBe(422);await expect(page.getByRole('alert')).toContainText('mapping');
  await className.fill('class-not-in-target');mapped=await mappingAttempt();expect(mapped.status()).toBe(422);expect((await api('/api/flowchart/draft')).draft_sha256).toBe(beforeDraft.draft_sha256);
  await className.fill('NG');
  const requestsBeforeClassCancel=mapRequests;page.once('dialog',dialog=>dialog.dismiss());await apply.click();await expect(apply).toBeEnabled();expect(mapRequests).toBe(requestsBeforeClassCancel);await expect(className).toHaveValue('NG');expect((await api('/api/flowchart/draft')).draft_sha256).toBe(beforeDraft.draft_sha256);
  await page.route('**/api/flow-workspace/templates/*/map',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned mapping transport unavailable'})}));
  mapped=await mappingAttempt();expect(mapped.status()).toBe(503);await expect(page.getByRole('alert')).toContainText('Owned mapping transport unavailable');expect((await api('/api/flowchart/draft')).draft_sha256).toBe(beforeDraft.draft_sha256);
  await page.unroute('**/api/flow-workspace/templates/*/map');
  await evidence.screenshot(page,`${prefix}-failed-and-cancelled-template-preserves-original-draft`);
  mapped=await mappingAttempt();expect(mapped.status()).toBe(200);const mappedBody=await mapped.json(),mappedRequest=mapped.request().postDataJSON();
  expect(mappedRequest).toMatchObject({project_id:project.id,models:{[graph.model_id]:graph.job_id},classes:{[graph.model_id+':name:NG']:'NG'}});
  expect(mappedBody.pipeline.nodes.find((n:any)=>n.id===graph.model_id).data.model_job_id).toBe(graph.job_id);
  await expect(page.getByText('모델·클래스를 매핑한 초안으로 열었습니다. 연결을 확인하고 저장하세요.',{exact:true})).toBeVisible();
  const saveDraft=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/flowchart/draft'&&r.request().method()==='PUT');await page.getByRole('button',{name:'초안 저장',exact:true}).click();expect((await saveDraft).status()).toBe(200);
  await expect(page.getByRole('status').filter({hasText:'✓ 편집 초안이 저장되었습니다.'})).toBeVisible();const finalDraft=await api('/api/flowchart/draft');
  await page.reload();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
  expect((await api('/api/flowchart/draft')).draft_sha256).toBe(finalDraft.draft_sha256);expect((await api(library)).templates).toEqual(savedLibrary);
  expect(sha(fs.readFileSync(versionFile))).toBe(versionSha);expect(sha(fs.readFileSync(checkpoint))).toBe(checkpointSha);expect(fs.existsSync(path.join(project.project_dir,'flowcharts/active.json'))).toBe(false);
  for(const record of templateHashes){expect(sha(fs.readFileSync(record.path))).toBe(record.sha256);evidence.addFile(record.path);}
  for(const row of fixture.files){expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);evidence.addFile(row.path);}evidence.addFile(checkpoint);evidence.addFile(versionFile);
  await evidence.screenshot(page,`${prefix}-mapped-draft-reopened-and-saved-source-version-preserved`);page.off('request',listener);
  evidence.note('template_safety',{project,graph,template,module,savedLibrary,beforeDraft,finalDraft,mappedRequest,mappedBody,owned_template_root:ownedLibrary,owned_template_files:templateHashes,owned_template_store_verified:true,checkpoint_sha256:checkpointSha,source_version_sha256:versionSha,
    actual_ui_and_backend:true,empty_and_oversize_name_422:true,subset_empty_name_422:true,subset_oversize_name_422:true,controlled_save_subset_map_503:true,corrected_whole_and_subset_saved:true,abandoned_unsubmitted_save_preserved:true,abandoned_whole_save_preserved:true,populated_class_mapping_cancelled:true,
    template_library_reopened_exact:true,replacement_cancelled_without_map_request:true,empty_model_and_class_422:true,unknown_class_422:true,failed_mapping_preserved_draft:true,explicit_mapping_round_trip:true,
    mapped_draft_reopened_exact:true,source_images_and_checkpoint_preserved:true,saved_source_version_preserved:true,no_active_version:true,no_training:true,quality_approved:false,independent_acceptance:false});
}
test('template save and explicit mapping preserve originals across errors cancellation and reopen',async({page,request,renderer,workspace,evidence})=>{
  await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body)=>{const r=body===undefined?await request.get(renderer.origin+route):route.endsWith('/update')?await request.put(renderer.origin+route,{data:body}):await request.post(renderer.origin+route,{data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native template safety preserves saved versions and reopens explicitly mapped draft',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body)=>window.evaluate(async({port,route,body})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{...(body===undefined?{}:{method:route.endsWith('/update')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned template API ${r.status}`);return r.json();},{port:status.port,route,body});await exercise(window,workspace,evidence,api,true);
});

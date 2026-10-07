import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(p:string)=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const source=path.join(workspace.root,'queue-source');fs.mkdirSync(source);
 const inputs=['error','disagreement','threshold'].map((name,i)=>{const p=path.join(source,name+'.png');fs.writeFileSync(p,png(64,3,(x,y)=>[x,y,50+i]));return {path:p,sha256:sha(p)};});
 const project=await api('/api/project/create',{name:'Saved review queue controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();
  const focus=page.getByRole('button',{name:'집중 편집',exact:true});if(await focus.getAttribute('aria-pressed')==='true')await focus.click();
  await page.getByText('저장 검토 큐 · 오류·불일치·임계값 우선',{exact:true}).click();};
 await navigate();const panel=page.getByRole('region',{name:'저장된 검토 큐'}),create=panel.getByRole('button',{name:'우선순위 큐 저장',exact:true});
 await expect(create).toBeDisabled();await expect(panel).toContainText('4단계에서 현재 데이터의 평가를 저장하면 검토 큐를 만들 수 있습니다.');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/review_queue_reports.py'),workspace.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 await navigate();await expect(create).toBeEnabled();const threshold=panel.getByRole('spinbutton',{name:'검토 큐 임계값',exact:true});
 await threshold.fill('2');const invalid=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();expect((await invalid).status()).toBe(422);
 await expect(panel.getByRole('alert')).toBeVisible();expect((await api('/api/data-workbench/review-queues')).queues).toEqual([]);
 await threshold.fill('0.5');const created=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/data-workbench/review-queues'&&r.request().method()==='POST');await create.click();const response=await created;expect(response.status()).toBe(200);const queue=await response.json();
 expect(queue.items.map((r:any)=>[r.relative_path,r.reasons,r.priority])).toEqual([['error.png',['error','threshold'],400],['disagreement.png',['disagreement'],200],['threshold.png',['threshold'],100]]);
 await expect(panel.getByRole('alert')).toHaveCount(0);await panel.getByRole('button',{name:'현재 항목 열기',exact:true}).click();
 const advance=panel.getByRole('button',{name:'검토 완료 · 다음',exact:true}),skip=panel.getByRole('button',{name:'보류 · 다음',exact:true});await expect(advance).toBeEnabled();
 await advance.click();await expect(panel.getByRole('alert')).toContainText('검토자 이름');expect((await api('/api/data-workbench/review-queues/'+queue.id)).cursor).toBe(0);
 await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();await page.getByRole('textbox',{name:'작업자·검토자 이름',exact:true}).fill('Owned queue test operator');await page.getByRole('button',{name:'이미지 정보·검토',exact:true}).click();
 await skip.click();await expect(panel).toContainText('검토 진행 1 / 3');await expect(advance).toBeEnabled();await advance.click();await expect(panel).toContainText('검토 진행 2 / 3');await skip.click();await expect(panel).toContainText('큐의 모든 항목을 검토했습니다.');
 const finished=await api('/api/data-workbench/review-queues/'+queue.id);expect(finished.cursor).toBe(3);expect(finished.revision).toBe(4);
 expect(finished.history.map((r:any)=>[r.relative_path,r.state,r.actor])).toEqual([['error.png','skipped','Owned queue test operator'],['disagreement.png','reviewed','Owned queue test operator'],['threshold.png','skipped','Owned queue test operator']]);
 await navigate();await expect(panel.getByRole('combobox',{name:'저장 검토 큐 선택',exact:true})).toHaveValue(queue.id);await expect(panel).toContainText('검토 진행 3 / 3');await expect(advance).toBeDisabled();await expect(skip).toBeDisabled();
 const reopened=await api('/api/data-workbench/review-queues/'+queue.id);expect(reopened).toEqual(finished);
 for(const input of inputs){expect(sha(input.path)).toBe(input.sha256);const metadata=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(input.path));expect(metadata.workflow_state).not.toBe('approved');}
 expect(sha(fixture.path)).toBe(fixture.sha256);
 // Temporarily change one owned synthetic image: the same saved queue must refuse resumption.
 const original=fs.readFileSync(inputs[0].path);try{fs.writeFileSync(inputs[0].path,png(64,3,()=>[1,2,3]));await navigate();await expect(panel).toContainText('원본 변경으로 재생성 필요');await expect(panel.getByRole('alert')).toContainText('source image changed');await expect(panel.getByRole('button',{name:'원래 평가·비교로 돌아가기',exact:true})).toBeDisabled();}finally{fs.writeFileSync(inputs[0].path,original);}
 await navigate();await expect(panel).not.toContainText('원본 변경으로 재생성 필요');expect(await api('/api/data-workbench/review-queues/'+queue.id)).toEqual(finished);
 await evidence.screenshot(page,native?'native-review-queue-reopened':'browser-review-queue-reopened');
 evidence.note('saved_review_queue_content',{project_id:project.id,inputs,fixture,finished,reopened,empty_origin_disabled:true,invalid_threshold_422:true,missing_reviewer_refused:true,priority_order_verified:true,skip_and_review_history_verified:true,stale_source_refused:true,reload_exact_cursor:true,original_bytes_preserved:true,native,controlled_reports_not_model_inference:true,human_quality_or_annotation_approval:false});
}
test('saved review queue preserves priority cursor and reviewer without approving labels',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved review queue preserves priority cursor and reviewer without approving labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned queue API ${r.status}: ${await r.text()}`);return r.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

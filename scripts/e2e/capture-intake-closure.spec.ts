import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
// eslint-disable-next-line @typescript-eslint/no-require-imports
const harness=require('./fixtures/harness.cjs');
test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Capture intake fixture';
 await api('/api/project/create',{name,task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/capture_intake_records.py'),workspace.root,JSON.stringify(project)],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();};
 await navigate();await page.getByRole('button',{name:'서비스 캡처 · 데이터 개선 후보',exact:true}).click();
 const panel=page.getByRole('region',{name:'서비스 캡처 데이터 개선'});
 await panel.getByLabel('캡처 서비스 작업 ID').fill(Object.values(fixture.jobs).join(','));await panel.getByRole('button',{name:'실제 서비스 캡처 등록',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('6개 작업');
 let queue=await api('/api/capture-intake/review-queue');expect(queue.total).toBe(6);expect(queue.pending).toBe(6);
 const byReason=(reason:string)=>queue.candidates.find((r:any)=>r.origin.job_id===fixture.jobs[reason]);
 for(const reason of ['disagreement','review','threshold'])expect(byReason(reason).review_reasons).toContain(reason);
 expect(byReason('damaged').routing).toBe('failed');expect(byReason('duplicate').routing).toBe('duplicate');
 expect(queue.candidates.map((r:any)=>r.review_priority)).toEqual([410,310,210,110,10,10]);
 expect(queue.candidates.every((r:any)=>r.truth_verdict==='UNKNOWN')).toBe(true);
 const candidate=byReason('review'),failed=byReason('damaged'),duplicate=byReason('duplicate');
 const row=(id:string)=>panel.getByRole('row').filter({has:page.getByLabel(`${id} 채택 선택`,{exact:true})});
 await expect(panel.getByLabel(`${failed.candidate_id} 채택 선택`,{exact:true})).toBeDisabled();
 await expect(panel.getByLabel(`${duplicate.candidate_id} 채택 선택`,{exact:true})).toBeDisabled();
 await row(failed.candidate_id).getByRole('button',{name:'캡처 검토',exact:true}).click();
 let detail=panel.locator('[aria-label="캡처 후보 검토"]');await detail.getByLabel('캡처 검토자',{exact:true}).fill('fixture-reviewer');await expect(detail.getByRole('button',{name:'새 데이터에 채택할 후보로 검토',exact:true})).toBeDisabled();
 await row(candidate.candidate_id).getByRole('button',{name:'캡처 검토',exact:true}).click();
 detail=panel.locator('[aria-label="캡처 후보 검토"]');await expect(detail.getByRole('img',{name:'해시로 고정한 실제 서비스 캡처'})).toBeVisible();await detail.locator('summary',{hasText:'검사 작업·모델·노드·ROI 근거'}).click();await expect(detail.locator('pre')).toContainText(fixture.jobs.review);await expect(detail.locator('pre')).toContainText('decision');
 await detail.getByLabel('캡처 검토 메모').fill('Controlled intake review');await detail.getByRole('button',{name:'새 데이터에 채택할 후보로 검토',exact:true}).click();await expect(panel.getByRole('status')).toContainText('채택 후보 검토를 저장');
 await evidence.screenshot(page,`${prefix}-capture-reasons-source-node-review`);
 await navigate();await page.getByRole('button',{name:'서비스 캡처 · 데이터 개선 후보',exact:true}).click();
 await expect(row(candidate.candidate_id)).toContainText('채택 후보 · fixture-reviewer');queue=await api('/api/capture-intake/review-queue');expect(queue.pending).toBe(5);expect(queue.candidates.at(-1).candidate_id).toBe(candidate.candidate_id);
 await row(candidate.candidate_id).getByRole('button',{name:'캡처 검토',exact:true}).click();detail=panel.locator('[aria-label="캡처 후보 검토"]');await detail.getByLabel('캡처 검토자',{exact:true}).fill('fixture-reviewer');await panel.getByLabel(`${candidate.candidate_id} 채택 선택`,{exact:true}).check();await panel.getByLabel('캡처 채택 버전 이름').fill('Reviewed intake branch');await panel.getByRole('button',{name:'검토한 1개로 새 소유 데이터 버전 생성',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('1개 캡처를 새 소유 데이터 버전에 복사');
 const version=(await api('/api/capture-intake/versions')).versions[0];expect(version.activated).toBe(false);expect((await api('/api/project/current')).source_dataset_dir).toBe(workspace.dataset);expect(version.adopted).toHaveLength(1);expect(version.fixed_test_records).toHaveLength(1);
 await panel.getByRole('button',{name:'이 버전을 데이터 원본으로 명시적 선택',exact:true}).click();
 await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(version.source_dataset_path);
 await expect(panel.getByRole('button',{name:'검증한 채택 전 원본으로 돌아가기',exact:true})).toBeEnabled();
 const adopted=version.adopted[0],image=path.join(version.source_dataset_path,adopted.relative_path);
 expect(sha(fs.readFileSync(image))).toBe(adopted.source_sha256);const metadata=await api('/api/dataset/metadata?limit=10'),captureRow=metadata.items.find((r:any)=>r.file_path===image);expect(captureRow.workflow_state).toBe('needs_review');expect(captureRow.usage_state).toBe('not_used');
 const truth=await api('/api/image-truth?'+new URLSearchParams({image_path:image,task:'classification'})+'&classes=ok&classes=ng');expect(truth.verdict).toBe('UNKNOWN');
 const splitFile=path.join(project.dataset_dir,'splits',sha(Buffer.from(version.source_dataset_path))+'.json'),savedSplit=JSON.parse(fs.readFileSync(splitFile,'utf8'));expect(savedSplit.assignments[adopted.relative_path]).toBe('train');expect(savedSplit.assignments[version.fixed_test_records[0].relative_path]).toBe('test');
 await evidence.screenshot(page,`${prefix}-capture-owned-version-explicit-source`);
 // The adopted image remains editable through the ordinary labeling workflow.
 // Drawing a label must not declare a normal/defect truth or automatically opt it into training.
 await panel.getByRole('button',{name:'닫기',exact:true}).click();
 await page.getByRole('button',{name:`${path.basename(image)} 라벨링에서 열기`,exact:true}).click();
 await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();
 const team=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});await team.getByLabel('팀 작업자 이름').fill('capture-labeler');await team.getByRole('button',{name:'편집 시작',exact:true}).click();await expect(team.getByRole('button',{name:'편집 종료',exact:true})).toBeEnabled();await team.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();
 await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 await expect.poll(async()=>Number((await page.getByTestId('canvas-hud').innerText()).match(/scale\s*([\d.]+)\s*%/)?.[1]||0)).toBeGreaterThan(100);
 await page.getByTitle('100% Zoom (1:1)',{exact:true}).click();await expect(page.getByTestId('canvas-hud')).toContainText('100%');
 await page.getByTitle('바운딩 박스 (BBox - 2)',{exact:true}).click();
 const canvas=(await page.locator('[data-canvas-container]').boundingBox())!,x=canvas.x+(canvas.width-32)/2,y=canvas.y+(canvas.height-32)/2;
 await page.mouse.move(x+4,y+4);await page.mouse.down();await page.mouse.move(x+20,y+20,{steps:5});await page.mouse.up();
 const saved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/annotations/save'&&r.request().method()==='POST');await page.getByRole('button',{name:'Save Changes',exact:true}).click();expect((await saved).status()).toBe(200);
 const annotation=await api('/api/annotations/'+path.basename(image,'.png')+'?file_path='+encodeURIComponent(image));expect(annotation.annotations[0].bbox).toEqual([4,4,20,20]);
 const afterLabelTruth=await api('/api/image-truth?'+new URLSearchParams({image_path:image,task:'classification'})+'&classes=ok&classes=ng');expect(afterLabelTruth.verdict).toBe('UNKNOWN');
 const afterLabelMetadata=(await api('/api/dataset/metadata?limit=10')).items.find((r:any)=>r.file_path===image);expect(afterLabelMetadata.usage_state).toBe('not_used');
 await evidence.screenshot(page,`${prefix}-adopted-capture-label-saved-not-truth`);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('button',{name:'서비스 캡처 · 데이터 개선 후보',exact:true}).click();
 await panel.getByRole('button',{name:'검증한 채택 전 원본으로 돌아가기',exact:true}).click();await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(workspace.dataset);
 await navigate();await page.getByRole('button',{name:'서비스 캡처 · 데이터 개선 후보',exact:true}).click();await expect(row(candidate.candidate_id)).toContainText('채택 후보 · fixture-reviewer');await expect(panel.getByText('Reviewed intake branch',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'이 버전을 데이터 원본으로 명시적 선택',exact:true}).click();await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(version.source_dataset_path);await expect(panel.getByRole('button',{name:'검증한 채택 전 원본으로 돌아가기',exact:true})).toBeEnabled();
 const original=workspace.images[0],originalBytes=fs.readFileSync(original.path);let refusal='';
 try{fs.writeFileSync(original.path,Buffer.from('owned changed original fixture'));await panel.getByRole('button',{name:'검증한 채택 전 원본으로 돌아가기',exact:true}).click();await expect(panel.getByRole('alert')).toContainText('changed');refusal=await panel.getByRole('alert').innerText();expect((await api('/api/project/current')).source_dataset_dir).toBe(version.source_dataset_path);await evidence.screenshot(page,`${prefix}-changed-original-return-refused`);}finally{fs.writeFileSync(original.path,originalBytes);}
 await panel.getByRole('button',{name:'검증한 채택 전 원본으로 돌아가기',exact:true}).click();await expect.poll(async()=>(await api('/api/project/current')).source_dataset_dir).toBe(workspace.dataset);
 for(const original of workspace.images){expect(sha(fs.readFileSync(original.path))).toBe(original.sha256);evidence.addFile(original.path);}
 for(const file of [fixture.split_path,splitFile,path.join(path.dirname(version.source_dataset_path),'record.json'),image,path.join(project.dataset_dir,'capture_intake','index.json')])evidence.addFile(file);
 evidence.addFile(annotation.mask_file);
 evidence.note('capture_intake_closure',{project,fixture,queue,version,metadata:captureRow,truth,savedSplit,refusal,annotation,afterLabelTruth,afterLabelMetadata,original:workspace.images,actual_ui_and_backend:true,controlled_service_results_not_model_execution:true,review_reopened:true,explicit_source_and_verified_parent_return:true,fixed_test_preserved:true,human_ui_label_not_truth_or_training_inclusion:true});
}
test('captured evidence routes through human review owned adoption and verified original return',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned capture fixture HTTP ${r.status()}`).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native captured evidence review adoption and verified original return',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned capture fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

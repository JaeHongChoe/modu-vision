import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
test.use({actionTimeout:10_000});

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:OwnedApi,native:boolean,url?:string){
 const source=path.join(workspace.root,'filter-source');fs.mkdirSync(source);
 for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),png(16,3,(x,y)=>[x,y,30+i]));
 const project=await api('/api/project/create',{name:'Saved evaluation filter controls',task:'segmentation'});
 // Import image sources only; controlled detection reports do not claim a
 // trainable detection dataset or invoke a detector/annotation importer.
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'segmentation'});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/evaluation_filter_reports.py'),workspace.root,project.project_dir,source],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const writes:string[]=[];page.on('request',request=>{if(request.method()!=='GET'&&/\/(evaluation|train|jobs)(\/|$)/.test(new URL(request.url()).pathname))writes.push(`${request.method()} ${new URL(request.url()).pathname}`);});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click();const summary=page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'});if(await summary.locator('..').getAttribute('open')===null)await summary.click();await page.getByLabel('평가 이력 모델 종류',{exact:true}).selectOption('detection');};
 await navigate();const history=page.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 이력 · 제품/Lot별 오류'})}).first(),selector=history.getByLabel(/^모델별 저장 평가/);
 const full=fixture.items.find((r:any)=>r.variant==='full'),unknown=fixture.items.find((r:any)=>r.variant==='no-binary-truth'),empty=fixture.items.find((r:any)=>r.variant==='empty');
 const select=async(item:any)=>{await selector.selectOption(item.record.evaluation_id);const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(item!==empty&&await detail.getAttribute('open')===null)await detail.locator('summary').first().click();return detail;};
 let detail=await select(full);const classControl=detail.getByLabel('평가 증거 클래스',{exact:true}),errorControl=detail.getByLabel('평가 증거 오류',{exact:true});
 const results=()=>detail.getByRole('button',{name:/^case-\d\.png · FN/});
 const names=async()=>results().allTextContents();
 const classRow=(name:string)=>detail.getByRole('row').filter({has:page.getByRole('cell',{name,exact:true})});
 const observations:any[]=[];
 expect(await names()).toEqual(['case-0.png · FN 0 / FP 0','case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1','case-3.png · FN 0 / FP 0','case-4.png · FN 0 / FP 0','case-5.png · FN 0 / FP 1']);
 await expect(classRow('Scratch')).toHaveText('Scratch112');await expect(classRow('Crack')).toHaveText('Crack100');
 await classRow('Scratch').getByRole('button',{name:'1',exact:true}).click();await expect(classControl).toHaveValue('Scratch');await expect(errorControl).toHaveValue('fn');expect(await names()).toEqual(['case-1.png · FN 1 / FP 0']);
 await classRow('Scratch').getByRole('button',{name:'2',exact:true}).click();await expect(errorControl).toHaveValue('fp');expect(await names()).toEqual(['case-2.png · FN 0 / FP 1','case-5.png · FN 0 / FP 1']);observations.push({control:'class-error-cells',fn:['case-1.png'],fp:['case-2.png','case-5.png']});
 await classRow('Crack').getByRole('button',{name:'0',exact:true}).first().click();await expect(results()).toHaveCount(0);await expect(detail).toContainText('선택 조건에 해당하는 결과가 없습니다.');await expect(detail.getByLabel('정답과 예측 객체 위치',{exact:true})).toHaveCount(0);
 await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();await classControl.selectOption('all');await errorControl.selectOption('incorrect');expect(await names()).toEqual(['case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1']);await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();
 const histogram=detail.getByRole('button',{name:/^점수 /});expect(await histogram.count()).toBe(10);expect(await histogram.first().getAttribute('aria-label')).toBe('점수 0.0에서 0.1 0개');
 const expectedBins=[0,1,0,1,0,0,1,1,1,1];for(let i=0;i<10;i++)await expect(histogram.nth(i)).toHaveAttribute('aria-label',`점수 ${(i/10).toFixed(1)}에서 ${((i+1)/10).toFixed(1)} ${expectedBins[i]}개`);
 await detail.getByRole('button',{name:'점수 0.7에서 0.8 1개',exact:true}).click();expect(await names()).toEqual(['case-2.png · FN 0 / FP 1']);
 await detail.getByRole('button',{name:'점수 0.2에서 0.3 0개',exact:true}).click();await expect(results()).toHaveCount(0);await expect(detail).toContainText('선택 조건에 해당하는 결과가 없습니다.');await detail.getByRole('button',{name:'필터 초기화',exact:true}).click();await expect(results()).toHaveCount(6);observations.push({control:'score-bins',counts:expectedBins,empty_range:[.2,.3],half_open_upper_boundary:true});
 const roc=detail.getByLabel('ROC 곡선',{exact:true}),slider=detail.getByLabel('ROC 검토 임계값',{exact:true});await expect(roc).toHaveCount(1);await expect(detail).toContainText('AUC 0.8333 · TP 2 · 미검 FN 1 · 과검 FP 1 · TN 1.');
 for(const [value,text]of[['0.75','TP 2 · 미검 FN 1 · 과검 FP 0 · TN 2.'],['0','TP 3 · 미검 FN 0 · 과검 FP 2 · TN 0.'],['1','TP 0 · 미검 FN 3 · 과검 FP 0 · TN 2.']]){await slider.fill(value);await expect(detail).toContainText(text);observations.push({control:'roc-threshold',value,counts:text});}
 await slider.fill('0.5');const thresholds=history.getByText('평가 임계값:',{exact:false});await expect(thresholds).toContainText('"probability_threshold":0.5');
 const sizeSummary=detail.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'});await sizeSummary.click();const areas=detail.getByRole('button',{name:/^결함 면적 /});await expect(areas).toHaveCount(10);
 // Independent areas: (2+i)^2 for predicted indices0,2,4,5 ->4,16,36,49.
 const expectedAreas=[1,0,0,1,0,0,0,1,0,1];for(let i=0;i<10;i++)await expect(areas.nth(i)).toHaveAttribute('aria-label',`결함 면적 ${(i*4.9).toFixed(1)}에서 ${((i+1)*4.9).toFixed(1)} ${expectedAreas[i]}개`);
 await areas.nth(3).click();expect(await names()).toEqual(['case-2.png · FN 0 / FP 1']);await areas.nth(1).click();await expect(results()).toHaveCount(0);await detail.getByRole('button',{name:'면적 필터 해제',exact:true}).click();await expect(results()).toHaveCount(6);observations.push({control:'area-bins',predicted_areas:[4,16,36,49],counts:expectedAreas});
 await detail.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-saved-filters-and-roc`);
 detail=await select(unknown);await expect(detail.getByLabel('ROC 곡선',{exact:true})).toHaveCount(0);await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveCount(0);await expect(detail).toContainText('실제 정상·불량 정답과 결함 점수가 함께 있어야 ROC를 계산합니다.');
 await select(empty);await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0);
 await select(full);await history.getByLabel('평가 오류 집계 기준',{exact:true}).selectOption('lot');await navigate();await expect(selector).toHaveValue(full.record.evaluation_id);await expect(history.getByLabel('평가 오류 집계 기준',{exact:true})).toHaveValue('lot');detail=await select(full);await expect(detail.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all');await expect(detail.getByLabel('ROC 검토 임계값',{exact:true})).toHaveValue('0.5');await expect(results()).toHaveCount(6);
 const saved=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();await saved.locator('summary').click();expect(JSON.parse(await saved.locator('pre').innerText())).toEqual(full.record.result);
 for(const item of fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);const readback=await api(`/api/evaluation/history/${item.record.evaluation_id}?source_dataset_path=${encodeURIComponent(source)}&task=detection`);expect(readback).toEqual(item.record);evidence.addFile(item.report_path);}
 for(const input of fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);evidence.addFile(input.path);}expect(writes).toEqual([]);
 await detail.scrollIntoViewIfNeeded();await evidence.screenshot(page,`${native?'native':'browser'}-same-evaluation-reopened`);
 evidence.note('saved_filter_controls',{fixture,project_id:project.id,observations,unknown_truth_not_binary:true,empty_saved_report:true,same_record_and_group_reopened:true,ephemeral_filters_reset:true,saved_result_json_exact:true,source_reports_and_images_unchanged:true,evaluation_training_writes:writes,controlled_reports_not_model_inference:true});
}

test('saved evaluation filters preserve exact class errors, score and area bins and ROC arithmetic',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native saved evaluation filters preserve exact class errors, score and area bins and ROC arithmetic',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:OwnedApi=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned filter fixture API: HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(p:string)=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 execFileSync(harness.resolvePython(),['-c',`from pathlib import Path
from PIL import Image
import sys
root=Path(sys.argv[1])
for i in range(6):
 image=Image.new('RGB',(32,32),(70+i*20,)*3);position=0 if i<2 else i;image.putpixel((position,position),(220,)*3);image.save(root/'ok'/f'owned-{i}.png')`,workspace.dataset],{cwd:harness.REPO_ROOT,timeout:20_000});
 await api('/api/project/create',{name:'Readiness grouping',task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification',validate_images:false});
 let metadata=(await api('/api/dataset/metadata?limit=100')).items;expect(metadata).toHaveLength(8);
 const original=Object.fromEntries(metadata.map((row:any)=>[row.file_path,sha(row.file_path)]));
 for(let i=0;i<metadata.length;i++)await api('/api/dataset/metadata/'+metadata[i].image_uuid,{expected_revision:metadata[i].revision,actor:'fixture-owner',changes:{product:'product-'+i,group:i<3?'same-original':'original-'+i}},'PATCH');
 const open=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Readiness grouping');await page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true}).click();};
 await open();let panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환'});
 await panel.getByLabel('데이터 작업자 이름').fill('fixture-owner');await panel.getByLabel('분할 그룹 기준').selectOption('product');
 for(const [label,value] of [['학습','50'],['검증','25'],['시험','25']])await panel.getByLabel(label+' 그룹 분할 비율').fill(value);
 await panel.getByRole('button',{name:'분할 미리보기',exact:true}).click();await expect(panel).toContainText('독립 그룹 6개');await panel.getByRole('button',{name:'분할 적용',exact:true}).click();
 await expect(panel).toContainText('그룹 분할 적용');const saved=await api('/api/dataset/metadata/split');expect(saved.stale).toBe(false);expect(saved.group_count).toBe(6);
 expect(new Set(metadata.slice(0,3).map((row:any)=>saved.assignments[row.relative_path])).size).toBe(1);
 expect(saved.qualification.group_by).toEqual(['product']);expect(saved.qualification.ratios).toEqual([.5,.25,.25]);
 await open();panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환'});await expect(panel.getByLabel('저장된 분할 근거')).toContainText('저장 기준 product');await expect(panel).toContainText('독립 그룹 6개');
 await evidence.screenshot(page,(native?'native':'browser')+'-grouped-split-reopened');
 metadata=(await api('/api/dataset/metadata?limit=100')).items;await api('/api/dataset/metadata/'+metadata[0].image_uuid,{expected_revision:metadata[0].revision,actor:'fixture-owner',changes:{lot:'changed-after-split'}},'PATCH');
 const corrupt=path.join(workspace.dataset,'ok','owned-corrupt.png');fs.writeFileSync(corrupt,'owned unreadable image fixture');
 const stale=await api('/api/dataset/metadata/split');expect(stale.stale).toBe(true);expect(stale.assignments).toEqual(saved.assignments);
 await open();panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환'});await expect(panel.getByLabel('저장된 분할 근거')).toContainText('다시 미리보기 필요');await page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true}).click();
 await page.locator('summary',{hasText:'데이터 준비 상태 · 품질과 중복 진단'}).click();const readiness=page.getByRole('region',{name:'데이터 준비 진단'});await readiness.getByRole('button',{name:'준비 상태 진단',exact:true}).click();
 await expect(readiness.getByLabel('작업 유형 준비도')).toContainText('정상 8 / 오류 1');await expect(readiness.getByLabel('클래스별 이미지 비율')).toContainText('ng 1장');
 const diagnosis=await api('/api/data-workbench/diagnostics');expect(diagnosis.task_schema.invalid_count).toBe(1);expect(diagnosis.issue_counts.unreadable).toBe(1);expect(diagnosis.issue_counts.label_schema).toBe(1);expect(diagnosis.near_duplicates.length).toBeGreaterThan(0);
 await readiness.scrollIntoViewIfNeeded();await evidence.screenshot(page,(native?'native':'browser')+'-task-schema-diagnostics');
 await page.reload();await page.locator('summary',{hasText:'데이터 준비 상태 · 품질과 중복 진단'}).click();await expect(page.getByRole('region',{name:'데이터 준비 진단'}).getByLabel('작업 유형 준비도')).toContainText('정상 8 / 오류 1');
 for(const [file,digest] of Object.entries(original)){expect(sha(file)).toBe(digest);evidence.addFile(file);}
 evidence.note('readiness_closure',{project,saved,stale,diagnosis,original,corrupt:{path:corrupt,sha256:sha(corrupt)},actual_ui_save_reopen:true,unknown_external_lineage_not_inferred:true,native,windows_excluded:true});
}
test('declared original families grouped split receipt and task diagnostics reopen',async({page,renderer,workspace,evidence})=>{await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native declared original families grouped split receipt and task diagnostics reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const page=electronSession.window,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned readiness fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);});

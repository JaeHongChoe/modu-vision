import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const name='Label prompt exclusions';await api('/api/project/create',{name,task:'segmentation'});
 const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('button',{name:path.basename(workspace.images[0].path)+' 라벨링에서 열기',exact:true}).click();};
 await navigate();const open=page.getByRole('button',{name:'모델 보조 라벨링 패널',exact:true});await open.click();const panel=page.getByRole('region',{name:'SAM2 기반 라벨링'});await expect(panel).toBeVisible();await expect(page.getByText('현재 이미지 · '+path.basename(workspace.images[0].path),{exact:true})).toBeVisible();const setup=await api('/api/label-candidates/setup');
 // Actual missing provider gate. No route or provider replacement and no model
 // download: UI input and unavailable state do not certify learned inference.
 if(!setup.providers.foundation.ready)await expect(panel.getByRole('button',{name:'현재 이미지 SAM2 후보 생성',exact:true})).toBeDisabled();
 await panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'}).click();await expect(panel.getByRole('button',{name:'제외 영역 추가',exact:true})).toBeEnabled();await panel.getByLabel('제외할 원본 영역').fill('2, 3, 10, 12');await panel.getByRole('button',{name:'제외 영역 추가',exact:true}).click();await expect(panel.getByText('제외 1: [2, 3, 10, 12]',{exact:true})).toBeVisible();
 await panel.getByLabel('제외할 원본 영역').fill('10, 3, 2, 12');await panel.getByRole('button',{name:'제외 영역 추가',exact:true}).click();await expect(panel.getByRole('alert')).toContainText('원본 좌표');await expect(panel.getByText('제외 1: [2, 3, 10, 12]',{exact:true})).toBeVisible();
 await panel.getByLabel('제외 영역 1 제거').click();await expect(panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'})).toHaveText('현재 이미지 제외 영역 · 0개');
 await panel.getByLabel('제외할 원본 영역').fill('2, 3, 10, 12');await panel.getByRole('button',{name:'제외 영역 추가',exact:true}).click();await panel.getByLabel('텍스트 검출 프롬프트').fill('scratch. '.repeat(140));await expect(panel.getByLabel('텍스트 검출 프롬프트')).toHaveValue('scratch. '.repeat(140));
 await evidence.screenshot(page,(native?'native':'browser')+'-explicit-exclusions-provider-prerequisite');
 await open.click();const next=page.getByLabel('Next image or page');await (await next.isEnabled()?next:page.getByLabel('Previous image or page')).click();await open.click();await expect(panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'})).toHaveText('현재 이미지 제외 영역 · 0개');
 await navigate();await open.click();await expect(panel.locator('summary').filter({hasText:'현재 이미지 제외 영역'})).toHaveText('현재 이미지 제외 영역 · 0개');
 const proposals=await api('/api/label-suggestions');expect(proposals.suggestions).toHaveLength(0);
 for(const row of workspace.images){expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);evidence.addFile(row.path);}
 evidence.addFile(path.join(project.project_dir,'project.json'));evidence.note('label_prompt_exclusions',{project,setup,originals:workspace.images,actual_ui_add_remove_invalid_image_switch_reload:true,no_original_label_mutation:true,no_provider_substitution_or_learned_execution:true,parent_not_promoted:true});
}
test('exclusion controls stay image-specific and missing learned provider remains blocked',async({page,request,renderer,workspace,evidence})=>{test.setTimeout(120_000);await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native exclusion controls and honest unavailable-provider gate',{tag:'@electron'},async({electronSession,workspace,evidence})=>{test.setTimeout(120_000);const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned labeling API ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);});

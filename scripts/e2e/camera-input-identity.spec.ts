import path from 'node:path';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py');
 const env={...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:90_000}).trim());evidence.note('fixture',fixture);
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'운영자 검사',exact:true}).click();let operator=page.getByRole('region',{name:'운영자 검사 작업 공간'});
  await operator.getByText('입력 연결 설정',{exact:true}).click();await operator.getByLabel('운영자 입력 방식',{exact:true}).selectOption('camera');
  await operator.getByLabel('운영자 카메라 주소',{exact:true}).fill('rtsp://127.0.0.1:1/owned-unopened');await operator.getByLabel('운영자 카메라 ID',{exact:true}).fill('invalid spaces');
  await operator.getByRole('button',{name:'입력 설정 검증·저장',exact:true}).click();await expect(operator.getByRole('alert')).toContainText('opaque');
  await operator.getByLabel('운영자 카메라 ID',{exact:true}).fill('line-owned');await operator.getByRole('button',{name:'입력 설정 검증·저장',exact:true}).click();await expect(operator).toContainText('입력 설정 저장됨');
  const saved=await api('/api/product-delivery/operator');expect(saved.input_health.configuration.camera_id).toBe('line-owned');expect(saved.service.runtime.status).toBe('stopped');
  await page.reload();await page.getByRole('button',{name:'운영자 검사',exact:true}).click();operator=page.getByRole('region',{name:'운영자 검사 작업 공간'});await operator.getByText('입력 연결 설정',{exact:true}).click();
  await expect(operator.getByLabel('운영자 카메라 ID',{exact:true})).toHaveValue('line-owned');await expect(operator.getByLabel('운영자 카메라 주소',{exact:true})).toHaveValue('rtsp://127.0.0.1:1/owned-unopened');
  await evidence.screenshot(page,`${native?'native':'browser'}-reopened-camera-identity`);
  await operator.getByLabel('운영자 카메라 주소',{exact:true}).fill('2');await operator.getByLabel('운영자 카메라 ID',{exact:true}).fill('');await operator.getByRole('button',{name:'입력 설정 검증·저장',exact:true}).click();await expect(operator).toContainText('입력 설정 저장됨');
  const usb=await api('/api/product-delivery/operator');expect(usb.input_health.configuration.camera_id).toBe('usb:2');
  await operator.getByLabel('운영자 입력 방식',{exact:true}).selectOption('manual');await operator.getByRole('button',{name:'입력 설정 검증·저장',exact:true}).click();await expect(operator).toContainText('입력 설정 저장됨');
  const cleared=await api('/api/product-delivery/operator');expect(cleared.input_health.configuration.camera_id).toBeNull();expect(cleared.input_health.configuration.camera).toBeNull();expect(cleared.service.runtime.status).toBe('stopped');
  evidence.note('camera_config_closure',{project:fixture.project,saved,usb,cleared,native,actual_ui_reopen:true,physical_camera_contacted:false,windows_excluded:true});
 }finally{const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');}
}
test('camera input identity form, invalid refusal and saved reopen',async({page,renderer,workspace,evidence})=>{const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);});
test('native camera input identity persistence and explicit manual clear',{tag:'@electron'},async({electronSession,workspace,evidence})=>{const status=await electronSession.waitForBackend(),page=electronSession.window;const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned camera fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);});

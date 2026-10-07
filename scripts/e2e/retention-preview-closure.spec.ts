import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const route='/api/project/retention/trash';

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
  const prefix=native?'native':'browser',name='Reviewed retention fixture';
  await api('/api/project/create',{name,task:'classification'});
  const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  expect(path.resolve(project.project_dir).startsWith(path.resolve(workspace.root)+path.sep)).toBe(true);
  const report=path.join(project.project_dir,'reports/reviewed.json');
  fs.mkdirSync(path.dirname(report),{recursive:true});fs.writeFileSync(report,'owned version one');
  const beforeSha=sha(fs.readFileSync(report));
  if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  const open=async()=>{
    await page.getByTitle('프로젝트 관리',{exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
    await dialog.getByRole('button',{name:'보존기한·복구 보관함',exact:true}).click();
    const panel=dialog.getByRole('region',{name:'프로젝트 보존기한과 복구 보관함'});
    await expect(panel.getByLabel('파일 보존일',{exact:true})).toBeVisible();
    return {dialog,panel};
  };
  let {dialog,panel}=await open();
  const save=()=>panel.getByRole('button',{name:'보존 정책 저장',exact:true});
  const previewButton=()=>panel.getByRole('button',{name:'이동 가능 여부 미리 확인',exact:true});
  const move=()=>panel.getByRole('button',{name:'확인한 경로를 복구 보관함으로 이동',exact:true});
  const paths=()=>panel.getByLabel('복구 보관함 이동 경로');
  const preview=async()=>{
    const response=page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.request().postDataJSON()?.dry_run===true);
    await previewButton().click();const r=await response;expect(r.status()).toBe(200);
    const body=await r.json();expect(body.preview_sha256).toMatch(/^[0-9a-f]{64}$/);await expect(move()).toBeEnabled();return body;
  };
  await expect(previewButton()).toBeDisabled();await expect(move()).toHaveCount(0);
  await expect(panel.getByRole('button',{name:'원래 경로로 복원',exact:true})).toHaveCount(0);
  const initial=await api('/api/project/retention');expect(initial.trash).toHaveLength(0);
  await panel.getByLabel('파일 보존일',{exact:true}).fill('-1');await expect(save()).toBeDisabled();
  await panel.getByLabel('파일 보존일',{exact:true}).fill('0');
  await panel.getByLabel('복구 보관함 기준일',{exact:true}).fill('3651');await expect(save()).toBeDisabled();
  await panel.getByLabel('복구 보관함 기준일',{exact:true}).fill('7');
  await panel.getByLabel('프로젝트 용량 기준 MB',{exact:true}).fill('0');await expect(save()).toBeDisabled();
  await panel.getByLabel('프로젝트 용량 기준 MB',{exact:true}).fill('');await expect(save()).toBeEnabled();
  await page.route('**/api/project/retention/policy',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned policy transport unavailable'})}));
  await save().click();await expect(panel.getByRole('alert')).toContainText('Owned policy transport unavailable');
  expect((await api('/api/project/retention')).policy).toEqual(initial.policy);
  await page.unroute('**/api/project/retention/policy');await save().click();await expect(panel.getByRole('status')).toContainText('보존 정책을 저장');
  const saved=await api('/api/project/retention');expect(saved.policy).toEqual({retention_days:0,trash_days:7,quota_bytes:null});
  expect(sha(fs.readFileSync(report))).toBe(beforeSha);
  await panel.getByLabel('복구 보관함 기준일',{exact:true}).fill('17');
  await dialog.getByRole('button',{name:'프로젝트 관리 닫기',exact:true}).click();
  expect((await api('/api/project/retention')).policy).toEqual(saved.policy);
  ({dialog,panel}=await open());await expect(panel.getByLabel('복구 보관함 기준일',{exact:true})).toHaveValue('7');
  await paths().fill('project.json');await previewButton().click();await expect(panel.getByRole('alert')).toContainText('protected');
  expect((await api('/api/project/retention')).trash).toHaveLength(0);
  await paths().fill('reports/reviewed.json');
  await page.route('**/api/project/retention/trash',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned preview transport unavailable'})}));
  await previewButton().click();await expect(panel.getByRole('alert')).toContainText('Owned preview transport unavailable');await expect(move()).toHaveCount(0);
  await page.unroute('**/api/project/retention/trash');const cancelled=await preview();
  await dialog.getByRole('button',{name:'프로젝트 관리 닫기',exact:true}).click();expect(sha(fs.readFileSync(report))).toBe(beforeSha);expect((await api('/api/project/retention')).trash).toHaveLength(0);
  ({dialog,panel}=await open());await expect(paths()).toHaveValue('');await expect(move()).toHaveCount(0);
  await expect(panel.getByLabel('복구 보관함 기준일',{exact:true})).toHaveValue('7');
  await paths().fill('reports/reviewed.json');const stalePreview=await preview();expect(stalePreview.preview_sha256).toBe(cancelled.preview_sha256);
  fs.writeFileSync(report,'owned version two');const afterSha=sha(fs.readFileSync(report));expect(afterSha).not.toBe(beforeSha);
  const staleResponse=page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.request().postDataJSON()?.dry_run===false);
  await move().click();const stale=await staleResponse,staleRequest=stale.request().postDataJSON();expect(stale.status()).toBe(409);
  expect(staleRequest.expected_preview_sha256).toBe(stalePreview.preview_sha256);
  await expect(panel.getByRole('alert')).toContainText('preview changed');expect(sha(fs.readFileSync(report))).toBe(afterSha);expect((await api('/api/project/retention')).trash).toHaveLength(0);
  await evidence.screenshot(page,`${prefix}-changed-preview-preserves-original`);
  const fresh=await preview();expect(fresh.preview_sha256).not.toBe(stalePreview.preview_sha256);
  await page.route('**/api/project/retention/trash',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned move transport unavailable'})}));
  await move().click();await expect(panel.getByRole('alert')).toContainText('Owned move transport unavailable');
  expect(sha(fs.readFileSync(report))).toBe(afterSha);expect((await api('/api/project/retention')).trash).toHaveLength(0);
  await page.unroute('**/api/project/retention/trash');
  const movedResponse=page.waitForResponse(r=>new URL(r.url()).pathname===route&&r.request().postDataJSON()?.dry_run===false);
  await move().click();const movedReply=await movedResponse,moved=await movedReply.json();expect(movedReply.status()).toBe(200);expect(movedReply.request().postDataJSON().expected_preview_sha256).toBe(fresh.preview_sha256);
  await expect(panel.getByRole('status')).toContainText('1개 경로를 복구 보관함으로 이동');expect(fs.existsSync(report)).toBe(false);expect(moved.trashed).toHaveLength(1);
  const stored=await api('/api/project/retention');expect(stored.trash).toHaveLength(1);expect(stored.trash[0].state).toBe('trashed');
  const payload=path.join(project.project_dir,'.retention/trash',moved.trashed[0].trash_id,'payload');expect(sha(fs.readFileSync(payload))).toBe(afterSha);
  await dialog.getByRole('button',{name:'프로젝트 관리 닫기',exact:true}).click();
  expect((await api('/api/project/retention')).trash[0].state).toBe('trashed');expect(sha(fs.readFileSync(payload))).toBe(afterSha);
  await page.reload();({dialog,panel}=await open());await expect(panel.getByLabel('복구 보관함 기준일',{exact:true})).toHaveValue('7');
  await expect(paths()).toHaveValue('');await expect(move()).toHaveCount(0);
  // A separately written owned target must never be overwritten by restore.
  fs.writeFileSync(report,'owned occupied target');const occupiedSha=sha(fs.readFileSync(report));
  await panel.getByRole('button',{name:'원래 경로로 복원',exact:true}).click();await expect(panel.getByRole('alert')).toContainText('already exists');
  expect(sha(fs.readFileSync(report))).toBe(occupiedSha);expect(sha(fs.readFileSync(payload))).toBe(afterSha);
  const occupied=path.join(project.project_dir,'reports/occupied-preserved.json');fs.renameSync(report,occupied);
  await page.route('**/api/project/retention/restore-trash',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned restore transport unavailable'})}));
  await panel.getByRole('button',{name:'원래 경로로 복원',exact:true}).click();await expect(panel.getByRole('alert')).toContainText('Owned restore transport unavailable');
  expect(fs.existsSync(report)).toBe(false);expect(sha(fs.readFileSync(payload))).toBe(afterSha);
  await page.unroute('**/api/project/retention/restore-trash');
  await panel.getByRole('button',{name:'원래 경로로 복원',exact:true}).click();await expect(panel.getByRole('status')).toContainText('원래 경로로 복원');
  expect(sha(fs.readFileSync(report))).toBe(afterSha);expect(sha(fs.readFileSync(occupied))).toBe(occupiedSha);expect(fs.existsSync(payload)).toBe(false);
  const restored=await api('/api/project/retention');expect(restored.trash[0].state).toBe('restored');expect(restored.permanent_deletion_supported).toBe(false);
  await page.reload();({dialog,panel}=await open());await expect(panel.getByRole('button',{name:'원래 경로로 복원',exact:true})).toHaveCount(0);await expect(previewButton()).toBeDisabled();
  await evidence.screenshot(page,`${prefix}-restored-exact-reviewed-bytes-and-reopened-policy`);
  for(const image of workspace.images){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);evidence.addFile(image.path);}
  evidence.addFile(report);evidence.addFile(occupied);
  evidence.note('retention_preview_closure',{project,initial,saved,stalePreview,staleRequest,stale_http_status:409,fresh,moved,stored,restored,before_sha256:beforeSha,reviewed_sha256:afterSha,occupied_sha256:occupiedSha,
    actual_ui_and_backend:true,empty_paths_disabled:true,invalid_policy_disabled:true,optional_empty_quota_saved:true,controlled_policy_preview_move_restore_503:true,cancelled_policy_no_save:true,cancelled_restore_no_change:true,
    cancelled_preview_no_movement:true,stale_bytes_preserved:true,exact_digest_round_trip:true,reopened_trash_and_policy:true,occupied_restore_preserved:true,exact_restored_bytes:true,
    source_images_preserved:true,no_permanent_deletion:true,no_training_submitted:true,human_quality_approval:false,independent_acceptance:false});
}

test('reviewed retention refuses stale bytes and preserves cancelled failed and restored artifacts',async({page,request,renderer,workspace,evidence})=>{
  await installDesktopHostShim(page,renderer.port);
  const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned retention fixture HTTP ${r.status()}`).toBe(true);return r.json();};
  await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native reviewed retention requires fresh preview and restores exact bytes after reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const {window}=electronSession,status=await electronSession.waitForBackend();
  const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned retention fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
  await exercise(window,workspace,evidence,api,true);
});

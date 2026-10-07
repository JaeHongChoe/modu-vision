import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
  const prefix=native?'native':'browser',name='Cancelled and retried archive fixture';
  await api('/api/project/create',{name,task:'classification'});
  const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification'});
  expect(path.resolve(project.project_dir).startsWith(path.resolve(workspace.root)+path.sep)).toBe(true);
  const marker=path.join(project.project_dir,'reports/archive-marker.json');
  fs.mkdirSync(path.dirname(marker),{recursive:true});
  fs.writeFileSync(marker,JSON.stringify({scope:'owned persisted archive control',original:true}));
  const markerSha=sha(fs.readFileSync(marker)),backups=path.join(workspace.root,'archive-controls-backups');
  const target=path.join(workspace.projects,'archive-controls-restored');
  const archiveFiles=()=>fs.existsSync(backups)?fs.readdirSync(backups):[];
  const calls:{route:string,body:any}[]=[];
  page.on('request',r=>{const route=new URL(r.url()).pathname;if(r.method()==='POST'&&['/api/project/backup','/api/project/restore'].includes(route))calls.push({route,body:r.postDataJSON()});});
  const originals=()=>{
    expect(sha(fs.readFileSync(marker))).toBe(markerSha);
    for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
  };
  if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  const open=async(tab:'프로젝트 백업'|'백업에서 복원'|'보존기한·복구 보관함')=>{
    await page.getByTitle('프로젝트 관리',{exact:true}).click();
    const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
    await dialog.getByRole('button',{name:tab,exact:true}).click();return dialog;
  };
  const close=async()=>{await page.getByRole('dialog',{name:'프로젝트 관리',exact:true}).getByRole('button',{name:'프로젝트 관리 닫기',exact:true}).click();};
  let dialog=await open('프로젝트 백업');
  const backupInput=()=>dialog.getByPlaceholder('백업 ZIP을 저장할 폴더');
  const backupButton=()=>dialog.getByRole('button',{name:'프로젝트 백업 만들기',exact:true});
  await expect(backupInput()).toHaveValue('');await expect(backupButton()).toBeDisabled();
  await backupInput().fill('   ');await expect(backupButton()).toBeDisabled();
  expect(calls).toHaveLength(0);expect(archiveFiles()).toEqual([]);originals();
  await backupInput().fill(project.project_dir);
  const invalidResponse=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/backup'&&r.request().method()==='POST');
  await backupButton().click();const invalid=await invalidResponse;expect(invalid.status()).toBe(422);
  await expect(dialog.getByRole('alert')).toContainText('outside the project and source dataset');
  expect(archiveFiles()).toEqual([]);originals();
  await backupInput().fill(backups);
  await page.route('**/api/project/backup',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned archive transport unavailable'})}));
  await backupButton().click();await expect(dialog.getByRole('alert')).toContainText('Owned archive transport unavailable');
  expect(archiveFiles()).toEqual([]);originals();
  await page.unroute('**/api/project/backup');
  await close();expect(archiveFiles()).toEqual([]);expect((await api('/api/project/current')).project_dir).toBe(project.project_dir);
  dialog=await open('프로젝트 백업');await expect(backupInput()).toHaveValue('');await expect(backupButton()).toBeDisabled();
  await backupInput().fill(backups);const beforeCancel=calls.length;
  await close();expect(calls).toHaveLength(beforeCancel);expect(archiveFiles()).toEqual([]);originals();
  dialog=await open('프로젝트 백업');await expect(backupInput()).toHaveValue('');
  await backupInput().fill(backups);
  const backed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/backup'&&r.request().method()==='POST');
  await backupButton().click();const backupReply=await backed;expect(backupReply.status()).toBe(200);const backup=await backupReply.json();
  await expect(dialog.getByRole('status')).toContainText('백업 완료');
  expect(backup.backup_status).toBe('archive_verified');expect(backup.restore_status).toBe('unverified');
  expect(archiveFiles()).toEqual([path.basename(backup.archive_path)]);
  const archiveSha=sha(fs.readFileSync(backup.archive_path));expect(archiveSha).toBe(backup.archive_sha256);
  const sourceStatus=await api('/api/project/retention');expect(sourceStatus.backups.find((r:any)=>r.backup_id===backup.backup_id).restore_verified).toBe(false);
  await close();await page.reload();dialog=await open('프로젝트 백업');
  await expect(backupInput()).toHaveValue('');await expect(backupButton()).toBeDisabled();
  expect(archiveFiles()).toEqual([path.basename(backup.archive_path)]);expect(sha(fs.readFileSync(backup.archive_path))).toBe(archiveSha);originals();
  await evidence.screenshot(page,`${prefix}-archive-reopened-with-originals-preserved`);
  await dialog.getByRole('button',{name:'백업에서 복원',exact:true}).click();
  const archiveInput=()=>dialog.getByPlaceholder('.mvision.zip 파일 경로');
  const targetInput=()=>dialog.getByPlaceholder('아직 존재하지 않는 새 폴더 경로');
  const restoreButton=()=>dialog.getByRole('button',{name:'새 프로젝트로 복원',exact:true});
  await expect(restoreButton()).toBeDisabled();await archiveInput().fill(backup.archive_path);await expect(restoreButton()).toBeDisabled();
  await archiveInput().fill('');await targetInput().fill(target);await expect(restoreButton()).toBeDisabled();
  await archiveInput().fill(backup.archive_path);const beforeRestoreCancel=calls.length;
  await close();expect(calls).toHaveLength(beforeRestoreCancel);expect(fs.existsSync(target)).toBe(false);expect(sha(fs.readFileSync(backup.archive_path))).toBe(archiveSha);originals();
  dialog=await open('백업에서 복원');await expect(archiveInput()).toHaveValue('');await expect(targetInput()).toHaveValue('');await expect(restoreButton()).toBeDisabled();
  await archiveInput().fill(backup.archive_path);await targetInput().fill(target);
  await page.route('**/api/project/restore',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Owned fresh restore transport unavailable'})}));
  await restoreButton().click();await expect(dialog.getByRole('alert')).toContainText('Owned fresh restore transport unavailable');
  expect(fs.existsSync(target)).toBe(false);expect((await api('/api/project/current')).project_dir).toBe(project.project_dir);
  expect(sha(fs.readFileSync(backup.archive_path))).toBe(archiveSha);originals();
  await evidence.screenshot(page,`${prefix}-failed-restore-preserves-selected-project-and-archive`);
  await page.unroute('**/api/project/restore');
  const restored=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/restore'&&r.request().method()==='POST');
  await restoreButton().click();const restoreReply=await restored;expect(restoreReply.status()).toBe(200);
  const reply=await restoreReply.json();expect(reply.project_dir).toBe(target);await expect(dialog).toHaveCount(0);
  const active=await api('/api/project/current');expect(active.project_dir).toBe(target);
  expect(active.source_dataset_dir).toBe(path.join(target,'dataset/restored_source'));
  const restoredMarker=path.join(target,'reports/archive-marker.json');expect(sha(fs.readFileSync(restoredMarker))).toBe(markerSha);
  expect(sha(fs.readFileSync(backup.archive_path))).toBe(archiveSha);originals();
  for(const image of workspace.images){const relocated=path.join(active.source_dataset_dir,path.relative(workspace.dataset,image.path));expect(sha(fs.readFileSync(relocated))).toBe(image.sha256);evidence.addFile(relocated);evidence.addFile(image.path);}
  const restoredStatus=await api('/api/project/retention');expect(restoredStatus.restores.some((r:any)=>r.status==='restore_verified'&&r.archive_sha256===archiveSha)).toBe(true);
  await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  expect((await api('/api/project/current')).project_dir).toBe(target);
  dialog=await open('보존기한·복구 보관함');
  await expect(dialog.getByText(/새 폴더 복원 검증 1건/)).toBeVisible();
  await evidence.screenshot(page,`${prefix}-fresh-project-handoff-reopened`);
  evidence.addFile(backup.archive_path);evidence.addFile(marker);evidence.addFile(restoredMarker);
  evidence.note('project_archive_controls',{project,backup,active,sourceStatus,restoredStatus,calls,archive_sha256:archiveSha,marker_sha256:markerSha,source_images:workspace.images,
    actual_ui_and_backend:true,backup_empty_disabled:true,backup_invalid_target_status:422,controlled_backup_503:true,backup_cancel_no_request:true,backup_reopened:true,archive_used_for_fresh_restore:true,
    restore_empty_disabled:true,restore_cancel_no_request:true,controlled_restore_503:true,failed_restore_no_target:true,selected_project_preserved_on_failure:true,explicit_successful_retries:true,
    exact_archive_and_source_hashes:true,fresh_restore_reopened:true,no_training_submitted:true,human_quality_approval:false,independent_acceptance:false});
}

test('archive controls preserve originals across invalid paths cancellation transport errors and fresh restore',async({page,request,renderer,workspace,evidence})=>{
  await installDesktopHostShim(page,renderer.port);
  const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned archive fixture HTTP ${r.status()}`).toBe(true);return r.json();};
  await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native archive controls preserve original bytes and reopen the explicitly restored fresh project',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const {window}=electronSession,status=await electronSession.waitForBackend();
  const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned archive fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});
  await exercise(window,workspace,evidence,api,true);
});

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
const seedCode=`import json,sqlite3,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve();project=Path(sys.argv[2]).resolve()
assert project.is_relative_to(root)
project.joinpath('reports').mkdir(exist_ok=True)
with sqlite3.connect(project/'reports'/'fixture.sqlite3') as db:
 db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE evidence(id TEXT,value TEXT)')
 db.execute("INSERT INTO evidence VALUES('review','controlled persisted record')");db.commit()
project.joinpath('reports/old-report.json').write_text('{"scope":"owned expired fixture"}')
project.joinpath('models/retained.bin').write_bytes(b'owned inert artifact; not a usable model')
project.joinpath('runtime_service').mkdir(exist_ok=True)
project.joinpath('runtime_service/config.json').write_text('{"enabled":false,"scope":"owned fixture"}')
print(json.dumps({'sqlite_rows':[['review','controlled persisted record']]}))`;

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Project recovery fixture';
 await api('/api/project/create',{name,task:'classification'});
 const project=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');await api('/api/dataset/import',{folder_path:workspace.dataset,task:'classification'});
 const image=workspace.images.find(row=>row.label==='ng')!,beforeAnnotations=[{id:'controlled-box',type:'bbox',label:'reviewed-fixture',category_id:1,bbox:[4,4,20,20]}];
 await api('/api/annotations/save',{image_id:path.basename(image.path,'.png'),image_path:image.path,image_width:32,image_height:32,annotations:beforeAnnotations,actor:'fixture-labeler'});
 const beforeSavedAnnotations=await api('/api/annotations/'+path.basename(image.path,'.png')+'?file_path='+encodeURIComponent(image.path));expect(beforeSavedAnnotations.annotations).toMatchObject(beforeAnnotations);
 let row=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image.path));row=await api('/api/dataset/metadata/'+row.image_uuid,{expected_revision:row.revision,actor:'fixture-reviewer',changes:{workflow_state:'approved'}},'PATCH');expect(row.workflow_state).toBe('approved');
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),['-c',seedCode,workspace.root,project.project_dir],{encoding:'utf8',timeout:30_000}));
 const oldReport=path.join(project.project_dir,'reports/old-report.json'),held=path.join(project.project_dir,'models/retained.bin'),runtimeConfig=path.join(project.project_dir,'runtime_service/config.json');
 const oldSha=sha(fs.readFileSync(oldReport)),heldSha=sha(fs.readFileSync(held)),runtimeSha=sha(fs.readFileSync(runtimeConfig));
 await api('/api/project/retention/pins',{owner:'fixture-review-hold',paths:['models/retained.bin'],reason:'fixture_hold'});
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
 await page.getByTitle('프로젝트 관리',{exact:true}).click();let dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await dialog.getByRole('button',{name:'보존기한·복구 보관함',exact:true}).click();
 let retention=dialog.getByRole('region',{name:'프로젝트 보존기한과 복구 보관함'});await retention.getByLabel('파일 보존일',{exact:true}).fill('0');await retention.getByLabel('복구 보관함 기준일',{exact:true}).fill('7');await retention.getByLabel('프로젝트 용량 기준 MB',{exact:true}).fill('1');await retention.getByRole('button',{name:'보존 정책 저장',exact:true}).click();await expect(retention.getByRole('status')).toContainText('보존 정책을 저장');
 await retention.getByLabel('복구 보관함 이동 경로').fill('models/retained.bin');await retention.getByRole('button',{name:'이동 가능 여부 미리 확인',exact:true}).click();await expect(retention.getByRole('alert')).toContainText('pinned');expect(sha(fs.readFileSync(held))).toBe(heldSha);
 await retention.getByLabel('복구 보관함 이동 경로').fill('reports/old-report.json');await retention.getByRole('button',{name:'이동 가능 여부 미리 확인',exact:true}).click();await retention.getByRole('button',{name:'확인한 경로를 복구 보관함으로 이동',exact:true}).click();await expect(retention.getByRole('status')).toContainText('1개 경로를 복구 보관함으로 이동');expect(fs.existsSync(oldReport)).toBe(false);
 await retention.getByRole('button',{name:'원래 경로로 복원',exact:true}).click();await expect(retention.getByRole('status')).toContainText('원래 경로로 복원');expect(sha(fs.readFileSync(oldReport))).toBe(oldSha);await evidence.screenshot(page,`${prefix}-recoverable-trash-and-protected-hold`);
 await dialog.getByRole('button',{name:'프로젝트 백업',exact:true}).click();await dialog.getByPlaceholder('백업 ZIP을 저장할 폴더').fill(path.join(workspace.root,'backups'));
 const backed=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/backup'&&r.request().method()==='POST');await dialog.getByRole('button',{name:'프로젝트 백업 만들기',exact:true}).click();const backupReply=await backed;expect(backupReply.status()).toBe(200);const backup=await backupReply.json();
 await expect(dialog.getByRole('status')).toContainText('백업 완료');expect(backup.backup_status).toBe('archive_verified');expect(backup.restore_status).toBe('unverified');const beforeRestore=await api('/api/project/retention');expect(beforeRestore.backups.find((b:any)=>b.backup_id===backup.backup_id).restore_verified).toBe(false);await evidence.screenshot(page,`${prefix}-archive-verified-restore-not-yet-run`);
 const target=path.join(workspace.projects,'fresh-restored');expect(fs.existsSync(target)).toBe(false);await dialog.getByRole('button',{name:'백업에서 복원',exact:true}).click();await dialog.getByPlaceholder('.mvision.zip 파일 경로').fill(backup.archive_path);await dialog.getByPlaceholder('아직 존재하지 않는 새 폴더 경로').fill(target);
 const restored=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/project/restore'&&r.request().method()==='POST');await dialog.getByRole('button',{name:'새 프로젝트로 복원',exact:true}).click();expect((await restored).status()).toBe(200);await expect(dialog).toHaveCount(0);
 const active=await api('/api/project/current');expect(active.project_dir).toBe(target);expect(active.source_dataset_dir).toBe(path.join(target,'dataset/restored_source'));
 for(const original of workspace.images){expect(sha(fs.readFileSync(original.path))).toBe(original.sha256);const relocated=path.join(active.source_dataset_dir,path.relative(workspace.dataset,original.path));expect(sha(fs.readFileSync(relocated))).toBe(original.sha256);evidence.addFile(relocated);evidence.addFile(original.path);}
 const relocatedImage=path.join(active.source_dataset_dir,path.relative(workspace.dataset,image.path));const annotations=await api('/api/annotations/'+path.basename(image.path,'.png')+'?file_path='+encodeURIComponent(relocatedImage));expect(annotations.annotations).toEqual(beforeSavedAnnotations.annotations);const metadata=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(relocatedImage));expect(metadata.workflow_state).toBe('approved');expect(metadata.audit.some((e:any)=>e.actor==='fixture-reviewer')).toBe(true);
 expect(sha(fs.readFileSync(path.join(target,'models/retained.bin')))).toBe(heldSha);expect(sha(fs.readFileSync(path.join(target,'runtime_service/config.json')))).toBe(runtimeSha);expect(sha(fs.readFileSync(path.join(target,'reports/old-report.json')))).toBe(oldSha);
 const sqliteRows=JSON.parse(execFileSync(harness.resolvePython(),['-c',"import json,sqlite3,sys\nwith sqlite3.connect(sys.argv[1]) as db: print(json.dumps(db.execute('SELECT id,value FROM evidence').fetchall()))",path.join(target,'reports/fixture.sqlite3')],{encoding:'utf8'}));expect(sqliteRows).toEqual(fixture.sqlite_rows);
 const afterRestore=await api('/api/project/retention');expect(afterRestore.restores.some((r:any)=>r.status==='restore_verified'&&r.archive_sha256===backup.archive_sha256)).toBe(true);expect(afterRestore.policy).toEqual({retention_days:0,trash_days:7,quota_bytes:1048576});expect(afterRestore.pins.some((p:any)=>p.relative_path==='models/retained.bin')).toBe(true);
 await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByTitle('프로젝트 관리',{exact:true}).click();dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});await dialog.getByRole('button',{name:'보존기한·복구 보관함',exact:true}).click();retention=dialog.getByRole('region',{name:'프로젝트 보존기한과 복구 보관함'});await expect(retention.getByLabel('복구 보관함 기준일',{exact:true})).toHaveValue('7');await expect(retention.getByText(/새 폴더 복원 검증 1건/)).toBeVisible();await evidence.screenshot(page,`${prefix}-fresh-restore-policy-reopened`);
 await dialog.getByRole('button',{name:'백업에서 복원',exact:true}).click();await dialog.getByPlaceholder('.mvision.zip 파일 경로').fill(backup.archive_path);await dialog.getByPlaceholder('아직 존재하지 않는 새 폴더 경로').fill(target);await dialog.getByRole('button',{name:'새 프로젝트로 복원',exact:true}).click();await expect(dialog.getByRole('alert')).toContainText('exist');expect((await api('/api/project/current')).project_dir).toBe(target);expect(sha(fs.readFileSync(path.join(target,'models/retained.bin')))).toBe(heldSha);
 for(const file of [backup.archive_path,path.join(target,'reports/fixture.sqlite3'),path.join(target,'models/retained.bin'),path.join(target,'runtime_service/config.json'),annotations.mask_file])evidence.addFile(file);
 evidence.note('project_recovery_closure',{project,fixture,backup,beforeRestore,active,beforeSavedAnnotations,annotations,metadata,afterRestore,sqliteRows,original:workspace.images,protected_artifact_sha256:heldSha,runtime_config_sha256:runtimeSha,old_report_sha256:oldSha,actual_ui_and_backend:true,fresh_empty_restore:true,archive_and_restore_separate:true,models_not_executed:true,fixture_review_not_operational_approval:true});
}
test('project archive restores labels review records SQLite and recoverable retention into a fresh workspace',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned recovery fixture HTTP ${r.status()}`).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native fresh project recovery preserves reviewed labels DB runtime settings and holds',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned recovery fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

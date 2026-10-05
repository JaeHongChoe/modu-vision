import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {storedZip} from './fixtures/storedZip';
import {png} from './qa/appFlow';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Durable ZIP fixture';
 const project=await api('/api/project/create',{name,task:'classification'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 const writes:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/\/(training\/start|train|compute\/jobs)$/.test(new URL(r.url()).pathname))writes.push(r.url());});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();await page.getByRole('button',{name:'검증된 데이터 버전',exact:true}).click();};
 const dialog=page.getByRole('dialog',{name:'검증된 데이터 버전'}),current=dialog.getByRole('region',{name:'현재 가져오기'}),zip=dialog.getByRole('region',{name:'ZIP으로 가져오기'});
 const files:[string,Buffer][]=[['OK/검사 1.png',png(32,3,(x,y)=>[x,y,75])],['NG/검사 2.png',png(32,3,(x,y)=>[y,x,175])]];
 const archive=storedZip(files),archivePath=path.join(workspace.root,'worker-valid.zip'),digest=sha(archive);fs.writeFileSync(archivePath,archive);
 await navigate();await zip.getByLabel('가져올 ZIP 파일').setInputFiles(archivePath);
 const started=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/imports/archive'&&r.request().method()==='POST');await zip.getByRole('button',{name:'ZIP 올리고 검증'}).click();const response=await started;expect(response.status()).toBe(200);const accepted=await response.json();
 await expect(current).toContainText('검증 완료 · 채택 전',{timeout:30_000});await expect(current).toContainText(accepted.job_id);await expect(current).toContainText(digest.slice(0,12));
 const view=await api(`/api/dataset/imports/${accepted.job_id}`);expect(view.state).toBe('completed');expect(view.result.revision.image_count).toBe(2);expect(view.result.revision.valid_count).toBe(2);expect(view.operation.archive_source_snapshot).toMatch(/^[a-f0-9]{64}$/);expect(view.operation.progress_unit).toBe('image');
 const source=path.join(project.project_dir,'dataset_imports',digest);expect(view.source.root).toBe(source);
 for(const [relative,bytes]of files){const file=path.join(source,relative);expect(sha(fs.readFileSync(file))).toBe(sha(bytes));evidence.addFile(file);}
 expect((await api('/api/dataset/revisions')).active_revision).toBeNull();
 await navigate();await expect(current).toContainText(accepted.job_id);await expect(current).toContainText('검증 완료 · 채택 전');await evidence.screenshot(page,`${prefix}-zip-result-reopened`);
 await current.getByRole('button',{name:'이 버전 채택',exact:true}).click();expect((await api('/api/dataset/revisions')).active_revision).toBeNull();await current.getByRole('button',{name:'채택 확인',exact:true}).click();await expect(current).toContainText('검증 완료 · 활성 버전');
 const active=view.result.revision.revision_id;expect((await api('/api/dataset/revisions')).active_revision).toBe(active);await navigate();await expect(current).toContainText('검증 완료 · 활성 버전');await evidence.screenshot(page,`${prefix}-zip-explicit-adoption-reopened`);
 const invalid=storedZip([['../outside.png',files[0][1]]]),invalidPath=path.join(workspace.root,'worker-unsafe.zip');fs.writeFileSync(invalidPath,invalid);
 await zip.getByLabel('가져올 ZIP 파일').setInputFiles(invalidPath);const rejected=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/imports/archive'&&r.request().method()==='POST');await zip.getByRole('button',{name:'ZIP 올리고 검증'}).click();const failureResponse=await rejected;expect(failureResponse.status()).toBe(200);const failureAccepted=await failureResponse.json();await expect(current).toContainText('실패:',{timeout:30_000});await expect(current).toContainText(failureAccepted.job_id);await expect(current.getByRole('button',{name:'이 버전 채택',exact:true})).toHaveCount(0);
 const failure=await api(`/api/dataset/imports/${failureAccepted.job_id}`);expect(failure.state).toBe('failed');expect(failure.result.revision).toBeUndefined();expect(failure.result.error.message).toContain('ArchiveRefused');expect((await api('/api/dataset/revisions')).active_revision).toBe(active);expect(fs.existsSync(path.join(project.project_dir,'dataset_imports',sha(invalid)))).toBe(false);expect(fs.existsSync(path.join(project.project_dir,'outside.png'))).toBe(false);
 await navigate();await expect(current).toContainText(failureAccepted.job_id);await expect(current).toContainText('실패:');await evidence.screenshot(page,`${prefix}-unsafe-zip-durable-failure-reopened`);
 expect((await api('/api/project/current')).source_dataset_dir).toBe(workspace.dataset);for(const image of workspace.images){expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);evidence.addFile(image.path);}expect(sha(fs.readFileSync(archivePath))).toBe(digest);expect(sha(fs.readFileSync(invalidPath))).toBe(sha(invalid));evidence.addFile(archivePath);evidence.addFile(invalidPath);expect(writes).toEqual([]);
 evidence.note('archive_worker',{project_id:project.id,accepted,completed:view,failure_accepted:failureAccepted,failed:failure,active_revision:active,archive_sha256:digest,source,registered_source_unchanged:true,exact_job_reopened:true,explicit_two_click_adoption:true,failed_job_reopened:true,failed_import_preserved_active:true,prohibited_writes:writes,actual_ui_backend:true,intermediate_progress_verified_by_backend_and_formatter_tests_not_tiny_ui_fixture:true,training_and_model_quality_not_assessed:true});
}
test('ZIP worker result and failure reopen and adoption requires explicit confirmation',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native ZIP worker result and failure reopen and adoption requires explicit confirmation',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned ZIP API HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

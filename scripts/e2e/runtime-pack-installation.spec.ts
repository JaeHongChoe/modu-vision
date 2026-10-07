import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
test.use({actionTimeout:15_000});
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string){
 const projectDir=path.join(workspace.projects,'owned-runtime-pack-a');
 await api('/api/project/create',{name:'Owned runtime pack A',task:'classification',project_dir:projectDir});
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/runtime_pack_control.py'),projectDir],{encoding:'utf8',timeout:30_000}));
 const originalHashes=Object.fromEntries(fixture.files.map((r:any)=>[path.join(fixture.source_dir,r.path),sha(path.join(fixture.source_dir,r.path))]));
 const before=await api('/api/product-delivery/installation');
 const open=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText('Owned runtime pack');await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();await page.getByRole('navigation',{name:'배포 운영 화면'}).getByRole('button',{name:'설치·진단',exact:true}).click();const panel=page.getByRole('group',{name:'선택형 런타임 팩',exact:true});await expect(panel).toContainText('실행 백엔드');await panel.scrollIntoViewIfNeeded();return panel;};
 let panel=await open();await expect(panel).toContainText('이 프로젝트에 보관한 런타임 팩이 없습니다.');
 const button=()=>panel.getByRole('button',{name:'팩 검증·보관',exact:true});await expect(button()).toBeDisabled();
 await panel.getByLabel('런타임 팩 payload 폴더',{exact:true}).fill(fixture.source_dir);await panel.getByLabel('런타임 팩 inventory 경로',{exact:true}).fill(fixture.inventory_path);await panel.getByLabel('런타임 팩 검토 SHA-256',{exact:true}).fill('0'.repeat(64));
 const install=async()=>{const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/product-delivery/runtime-packs/install'&&r.request().method()==='POST');await button().click();return response;};
 let response=await install();expect(response.status()).toBe(409);await expect(panel.getByRole('alert')).toContainText('independently reviewed SHA-256');
 expect((await api('/api/product-delivery/runtime-packs')).packs).toEqual([]);
 await evidence.screenshot(page,'runtime-pack-wrong-independent-pin-refused');
 await panel.getByLabel('런타임 팩 검토 SHA-256',{exact:true}).fill(fixture.expected_sha256);response=await install();expect(response.status(),await response.text()).toBe(200);const first=await response.json();
 expect(first).toMatchObject({inventory_sha256:fixture.expected_sha256,integrity:'verified',target_compatible:true,installed:true,activated:false,signature_verified:false,execution_verified:false,total_bytes:fixture.total_bytes});
 const card=()=>panel.getByRole('listitem',{name:`런타임 팩 ${fixture.id}`,exact:true});await expect(card()).toContainText('파일 무결성 확인됨');await expect(card()).toContainText(fixture.expected_sha256);await card().scrollIntoViewIfNeeded();await evidence.screenshot(page,'runtime-pack-actual-payload-installed-inactive');
 const target=path.join(projectDir,first.installation_relative_path),receipt=path.join(target,'receipt.json'),originalReceipt=sha(receipt);
 expect(sha(path.join(target,'inventory.json'))).toBe(fixture.expected_sha256);
 for(const row of fixture.files)expect(sha(path.join(target,'payload',row.path))).toBe(row.sha256);
 const blocked='**/api/product-delivery/runtime-packs/install';await page.route(blocked,r=>r.abort('failed'));await button().click();await expect(panel.getByRole('alert')).toBeVisible();await page.unroute(blocked);response=await install();expect(response.status()).toBe(200);await expect(panel.getByRole('alert')).toHaveCount(0);expect(sha(receipt)).toBe(originalReceipt);
 const changed=path.join(target,'payload',fixture.files[0].path),preserved=fs.readFileSync(changed);fs.writeFileSync(changed,Buffer.from('owned intentional tamper control'));
 await panel.getByRole('button',{name:'팩 무결성 다시 확인',exact:true}).click();await expect(card()).toContainText('파일 무결성 확인 실패');await card().scrollIntoViewIfNeeded();await evidence.screenshot(page,'runtime-pack-reread-detects-owned-tampering');
 response=await install();expect(response.status()).toBe(409);expect(fs.readFileSync(changed).toString()).toBe('owned intentional tamper control');expect(sha(receipt)).toBe(originalReceipt);
 fs.writeFileSync(changed,preserved);await panel.getByRole('button',{name:'팩 무결성 다시 확인',exact:true}).click();await expect(card()).toContainText('파일 무결성 확인됨');
 await api('/api/project/create',{name:'Owned runtime pack B',task:'classification',project_dir:path.join(workspace.projects,'owned-runtime-pack-b')});panel=await open();await expect(panel).toContainText('이 프로젝트에 보관한 런타임 팩이 없습니다.');await expect(panel.getByLabel('런타임 팩 payload 폴더',{exact:true})).toHaveValue('');
 await api('/api/project/open',{project_dir:projectDir});panel=await open();await expect(card()).toContainText('파일 무결성 확인됨');await expect(card()).toContainText(fixture.expected_sha256);await expect(panel.getByLabel('런타임 팩 검토 SHA-256',{exact:true})).toHaveValue('');await card().scrollIntoViewIfNeeded();await evidence.screenshot(page,'runtime-pack-exact-admitted-inventory-reopened');
 const after=await api('/api/product-delivery/installation');expect(after.runtime_dependencies).toEqual(before.runtime_dependencies);expect(after.host).toEqual(before.host);expect(after.runtime_packs.packs[0].activated).toBe(false);
 const rereadHashes=Object.fromEntries(Object.keys(originalHashes).map(file=>[file,sha(file)]));expect(rereadHashes).toEqual(originalHashes);expect(sha(receipt)).toBe(originalReceipt);
 evidence.note('runtime_pack_studio',{actual_ui_and_backend:true,actual_file_copy:true,fixture_scope:fixture.fixture_scope,inventory_sha256:fixture.expected_sha256,payload_files:fixture.files,total_bytes:fixture.total_bytes,wrong_pin_refused:true,network_failure_explicit_retry:true,idempotent_receipt_preserved:true,actual_owned_tamper_detected:true,no_automatic_repair:true,cross_project_inventory_isolated:true,exact_inventory_reopened:true,originalHashes,rereadHashes,active_dependencies_unchanged:true,activation:false,publisher_signature_verified:false,target_execution_qualified:false});
}
test('runtime packs install inertly and preserve reviewed pin through refusal tamper and reopen',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,renderer.url);
});
test('native runtime packs install inertly and preserve reviewed pin through refusal tamper and reopen',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned runtime pack HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api);
});

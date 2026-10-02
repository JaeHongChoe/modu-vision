import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {APIRequestContext} from '@playwright/test';
import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

async function validated(request:APIRequestContext,origin:string){
 const started=await (await request.post(`${origin}/api/dataset/imports`,{data:{task:'classification',verify:true}})).json();
 let view=started;for(let i=0;i<200&&!['completed','failed','aborted','interrupted'].includes(view.state);i++){await new Promise(r=>setTimeout(r,100));view=await (await request.get(`${origin}/api/dataset/imports/${started.job_id}`)).json();}
 expect(view.state).toBe('completed');
 const listed=await (await request.get(`${origin}/api/dataset/revisions`)).json();
 const accepted=await request.post(`${origin}/api/dataset/imports/${view.job_id}/accept`,{data:{revision_id:view.result.revision.revision_id,expected_active:listed.active_revision}});
 expect(accepted.ok()).toBe(true);}

// Actual renderer and backend on the owned synthetic harness: the inspection image is chosen from the validated revision
// by search, kept by identity, and after the file moves the picker reports it instead of re-selecting by path.
test('the inspection image is chosen by identity and a moved image is reported, not re-selected by path',async({page,renderer,workspace,evidence})=>{
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S207 library',task:'classification'}});expect(created.ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 await validated(page.request,renderer.origin);
 const serverErrors:string[]=[];
 page.on('response',response=>{const pathname=new URL(response.url()).pathname;if(pathname.startsWith('/api/')&&response.status()>=500)serverErrors.push(`${response.status()} ${pathname}`);});
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:/05.*플로우차트/}).click();
 await page.getByRole('button',{name:'이미지 변경...'}).click();
 const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'});await expect(picker).toBeVisible();
 await picker.getByLabel('이미지 검색').fill('sample-ng');
 const grid=picker.getByRole('list',{name:'데이터 버전 이미지'});
 await expect(grid.getByRole('listitem')).toHaveCount(1);
 await grid.getByRole('listitem').first().click();
 await expect(picker).toContainText('선택: ng/sample-ng.png');
 await evidence.screenshot(page,'s207-picked');
 await picker.getByRole('button',{name:'선택 확정'}).click();
 await expect(picker).toBeHidden();
 const images=(workspace as unknown as {images:Array<{label:string;path:string;sha256:string}>}).images;
 const ng=images.find(image=>image.label==='ng')!;
 fs.renameSync(ng.path,path.join(path.dirname(ng.path),'renamed-ng.png'));
 await validated(page.request,renderer.origin);
 await page.getByRole('button',{name:'이미지 변경...'}).click();
 await expect(picker).toBeVisible();
 await expect(picker.getByRole('status')).toContainText('ng/sample-ng.png이(가) ng/renamed-ng.png(으)로 옮겨졌습니다');
 await expect(picker.getByRole('button',{name:'선택 확정'})).toBeDisabled();
 await picker.getByRole('button',{name:'ng/renamed-ng.png 선택'}).click();
 await expect(picker).toContainText('선택: ng/renamed-ng.png');
 await evidence.screenshot(page,'s207-moved');
 expect(serverErrors,'no server error while choosing images').toEqual([]);
 expect(crypto.createHash('sha256').update(fs.readFileSync(path.join(path.dirname(ng.path),'renamed-ng.png'))).digest('hex'),'the source bytes are only read').toBe(ng.sha256);
});

test('fixed test images are kept by identity across a reload and a removed image is reported',async({page,renderer,workspace,evidence})=>{
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S207 test set',task:'classification'}});expect(created.ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 await validated(page.request,renderer.origin);
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 const open=async()=>{
  await page.getByRole('button',{name:/05.*플로우차트/}).click();
  await page.getByRole('tab',{name:'일괄 평가',exact:true}).click();
  const outer=page.locator('summary',{hasText:'고정 테스트 세트 · 플로우 버전 A/B 비교'});await outer.click();
  const inner=page.locator('summary',{hasText:/고정 테스트 이미지 \d+\/20/});await inner.click();
  return inner;};
 let summary=await open();
 const grid=page.getByRole('list',{name:'데이터 버전 이미지'});
 await expect(grid.getByRole('listitem')).toHaveCount(2);
 await grid.getByRole('listitem').nth(0).click();await grid.getByRole('listitem').nth(1).click();
 await expect(summary).toContainText('고정 테스트 이미지 2/20');
 await page.reload();summary=await open();
 await expect(summary).toContainText('고정 테스트 이미지 2/20');
 await expect(page.getByRole('list',{name:'선택한 고정 테스트 이미지'}).getByRole('listitem')).toHaveCount(2);
 const images=(workspace as unknown as {images:Array<{label:string;path:string}>}).images;
 fs.unlinkSync(images.find(image=>image.label==='ok')!.path);
 await validated(page.request,renderer.origin);
 await page.reload();summary=await open();
 await expect(summary).toContainText('고정 테스트 이미지 1/20 · 확인 필요 1');
 await expect(page.getByText(/찾을 수 없음 1장/)).toBeVisible();
 // the removed image is left out of comparisons but stays listed, so nothing disappears silently
 await expect(page.getByRole('list',{name:'확인이 필요한 고정 테스트 이미지'})).toContainText('찾을 수 없음');
 await evidence.screenshot(page,'s207-test-set');
});

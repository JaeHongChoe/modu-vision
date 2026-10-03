import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

test('a damaged image can be inspected but cannot enter the fixed comparison test set',async({page,renderer,workspace,evidence})=>{
 const damaged=path.join(workspace.dataset,'ng','broken.png');
 fs.writeFileSync(damaged,'This is a damaged PNG fixture.');
 const before=crypto.createHash('sha256').update(fs.readFileSync(damaged)).digest('hex');
 expect((await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S207 damaged image',task:'classification'}})).ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const started=await (await page.request.post(`${renderer.origin}/api/dataset/imports`,{data:{task:'classification',verify:true}})).json();
 let job=started;
 for(let i=0;i<200&&!['completed','failed','aborted','interrupted'].includes(job.state);i++){
  await new Promise(resolve=>setTimeout(resolve,100));
  job=await (await page.request.get(`${renderer.origin}/api/dataset/imports/${started.job_id}`)).json();
 }
 expect(job.state).toBe('completed');
 const listed=await (await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
 expect((await page.request.post(`${renderer.origin}/api/dataset/imports/${job.job_id}/accept`,{data:{revision_id:job.result.revision.revision_id,expected_active:listed.active_revision}})).ok()).toBe(true);
 const serverErrors:string[]=[];
 page.on('response',response=>{if(new URL(response.url()).pathname.startsWith('/api/')&&response.status()>=500)serverErrors.push(`${response.status()} ${response.url()}`);});
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 const open=async()=>{
  await page.getByRole('button',{name:/05.*플로우차트/}).click();
  await page.getByRole('tab',{name:'일괄 평가',exact:true}).click();
  await page.locator('summary',{hasText:'고정 테스트 세트 · 플로우 버전 A/B 비교'}).click();
  const summary=page.locator('summary',{hasText:/고정 테스트 이미지 \d+\/20/});await summary.click();return summary;
 };
 let summary=await open();
 const state=page.getByLabel('이미지 상태',{exact:true});
 const grid=page.getByRole('list',{name:'데이터 버전 이미지'});
 await expect(state).toHaveValue('valid');
 await expect(grid.getByRole('listitem')).toHaveCount(2);
 await state.selectOption('invalid');
 const invalid=grid.getByRole('listitem');await expect(invalid).toHaveCount(1);
 await invalid.click();
 await expect(page.getByRole('status').filter({hasText:'잘못된 이미지는 고정 테스트 비교에 쓸 수 없습니다:'})).toHaveText('잘못된 이미지는 고정 테스트 비교에 쓸 수 없습니다: ng/broken.png');
 await expect(summary).toContainText('고정 테스트 이미지 0/20');
 await expect(invalid).toHaveAttribute('aria-pressed','false');
 await evidence.screenshot(page,'s207-damaged-image-refused');
 await state.selectOption('valid');await expect(grid.getByRole('listitem')).toHaveCount(2);
 await grid.getByRole('listitem').first().click();
 await expect(summary).toContainText('고정 테스트 이미지 1/20');
 await expect(page.getByText('잘못된 이미지는 고정 테스트 비교에 쓸 수 없습니다:',{exact:false})).toHaveCount(0);
 await page.reload();summary=await open();
 await expect(summary).toContainText('고정 테스트 이미지 1/20');
 await expect(page.getByRole('list',{name:'선택한 고정 테스트 이미지'}).getByRole('listitem')).toHaveCount(1);
 expect(serverErrors).toEqual([]);
 expect(crypto.createHash('sha256').update(fs.readFileSync(damaged)).digest('hex'),'damaged source bytes remain unchanged').toBe(before);
 await evidence.screenshot(page,'s207-valid-selection-restored');
});

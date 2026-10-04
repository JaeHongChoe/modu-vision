import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {expect,test,type RendererServer,type Workspace} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

async function seed(page:Page,renderer:RendererServer,workspace:Workspace,validated=false){
 expect((await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S207 list recovery',task:'classification'}})).ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 if(!validated)expect((await page.request.post(`${renderer.origin}/api/dataset/import`,{data:{folder_path:workspace.dataset,task:'classification',validate_images:false}})).ok()).toBe(true);
 if(validated){
  const started=await(await page.request.post(`${renderer.origin}/api/dataset/imports`,{data:{task:'classification',verify:true}})).json();let job=started;
  for(let i=0;i<200&&!['completed','failed','aborted','interrupted'].includes(job.state);i++){
   await new Promise(resolve=>setTimeout(resolve,100));job=await(await page.request.get(`${renderer.origin}/api/dataset/imports/${started.job_id}`)).json();
  }
  expect(job.state).toBe('completed');const listed=await(await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
  expect((await page.request.post(`${renderer.origin}/api/dataset/imports/${job.job_id}/accept`,{data:{revision_id:job.result.revision.revision_id,expected_active:listed.active_revision}})).ok()).toBe(true);
 }
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toHaveText('S207 list recovery');
 await expect(page.getByLabel('검사 작업 종류',{exact:true})).toHaveValue('classification');
 await page.getByRole('button',{name:/05.*플로우차트/}).click();
}
async function testSet(page:Page){
 await page.getByRole('tab',{name:'일괄 평가',exact:true}).click();
 await page.locator('summary',{hasText:'고정 테스트 세트 · 플로우 버전 A/B 비교'}).click();
 const summary=page.locator('summary',{hasText:/고정 테스트 이미지 (저장 )?\d+\/20/});await summary.click();return summary;
}
const hashes=(workspace:Workspace)=>workspace.images.map(image=>crypto.createHash('sha256').update(fs.readFileSync(image.path)).digest('hex'));

test('a failed validated next page recovers through its exact cursor and preserves the selected UUID',async({page,renderer,workspace,evidence})=>{
 for(let i=0;i<130;i++)fs.copyFileSync(workspace.images[0].path,path.join(workspace.dataset,'ok',`extra-${String(i).padStart(3,'0')}.png`));
 const before=hashes(workspace);await seed(page,renderer,workspace,true);const summary=await testSet(page);
 const grid=page.getByRole('list',{name:'데이터 버전 이미지'});await expect(grid.getByRole('listitem').first()).toBeVisible();
 await grid.getByRole('listitem').first().click();await expect(summary).toContainText('고정 테스트 이미지 1/20');
 let failed=false;const queries:string[]=[];
 await page.route('**/api/dataset/library/images?**',async route=>{
  const url=new URL(route.request().url());if(!url.searchParams.get('cursor'))return route.continue();queries.push(url.search);
  if(!failed){failed=true;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled next-page outage'})});}
  return route.continue();
 });
 await grid.evaluate(element=>{element.scrollTop=element.scrollHeight;element.dispatchEvent(new Event('scroll',{bubbles:true}));});
 await expect(page.getByText('Controlled next-page outage',{exact:true})).toBeVisible();
 await expect(summary).toContainText('고정 테스트 이미지 1/20');await expect(page.getByText('120장 표시 · 끝',{exact:true})).toBeVisible();
 const retry=page.getByRole('button',{name:'다시 불러오기',exact:true});await expect(retry).toBeVisible({timeout:2000});await retry.click();
 await expect(page.getByText('132장 표시 · 끝',{exact:true})).toBeVisible();expect(queries).toHaveLength(2);expect(queries[1]).toBe(queries[0]);
 await grid.evaluate(element=>{element.scrollTop=0;element.dispatchEvent(new Event('scroll',{bubbles:true}));});
 await expect(grid.getByRole('listitem').first()).toHaveAttribute('aria-pressed','true');expect(hashes(workspace)).toEqual(before);
 evidence.note('list_retry',{scope:'validated cursor',requests:queries,loaded:132,selected:1,real_backend_after_retry:true});await evidence.screenshot(page,'s207-cursor-recovered');
});

test('legacy fixed test paths survive listing failure and manual recovery in the same panel',async({page,renderer,workspace,evidence})=>{
 const before=hashes(workspace);await seed(page,renderer,workspace);let summary=await testSet(page);
 await expect(summary).toContainText('처음 200장');const parent=summary.locator('..');await expect(parent.getByRole('checkbox')).toHaveCount(2);
 await parent.getByRole('checkbox').first().check();await expect(summary).toContainText('저장 1/20');
 let fail=true;const queries:string[]=[];await page.route('**/api/dataset/images?**',async route=>{
  const url=new URL(route.request().url());if(url.searchParams.get('limit')!=='200')return route.continue();queries.push(url.search);
  if(fail){fail=false;return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled legacy-list outage'})});}return route.continue();
 });
 await page.reload();await page.getByRole('button',{name:/05.*플로우차트/}).click();summary=await testSet(page);
 await expect(summary).toContainText('불러오지 못해 저장 경로를 확인할 수 없음');await expect(summary).toContainText('저장 1/20');
 await expect(page.getByRole('list',{name:'목록에 보이지 않는 저장 경로'})).toHaveCount(0);
 const retry=page.getByRole('button',{name:'테스트 이미지 목록 다시 불러오기',exact:true});await expect(retry).toBeVisible({timeout:2000});await retry.click();
 await expect(summary.locator('..').getByRole('checkbox')).toHaveCount(2);await expect(summary.locator('..').getByRole('checkbox').first()).toBeChecked();
 await expect(page.getByText('Controlled legacy-list outage',{exact:true})).toHaveCount(0);expect(queries).toHaveLength(2);expect(queries[1]).toBe(queries[0]);expect(hashes(workspace)).toEqual(before);
 evidence.note('list_retry',{scope:'legacy test panel',requests:queries,selected:1,real_backend_after_retry:true});await evidence.screenshot(page,'s207-legacy-test-recovered');
});

test('legacy image picker retries the same page without replacing its confirmed selection',async({page,renderer,workspace,evidence})=>{
 const before=hashes(workspace);await seed(page,renderer,workspace);
 await page.getByRole('button',{name:/이미지 변경/}).click();let dialog=page.getByRole('dialog',{name:'검사 대상 이미지 선택',exact:true});
 const first=dialog.getByRole('button',{name:/검사 이미지 선택$/}).first();await expect(first).toBeVisible();const selectedName=await first.getAttribute('aria-label');await first.click();
 await dialog.getByRole('button',{name:'선택 확정',exact:true}).click();await expect(dialog).toHaveCount(0);
 let recovering=false;const queries:string[]=[];await page.route('**/api/dataset/images?**',async route=>{
  const url=new URL(route.request().url());if(url.searchParams.get('limit')!=='48')return route.continue();queries.push(url.search);
  if(!recovering)return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Controlled picker outage'})});return route.continue();
 });
 await page.getByRole('button',{name:/이미지 변경/}).click();dialog=page.getByRole('dialog',{name:'검사 대상 이미지 선택',exact:true});await expect(dialog.getByText('Controlled picker outage',{exact:true})).toBeVisible();
 const failedRequests=queries.length;expect(failedRequests).toBeGreaterThan(0);recovering=true;
 const retry=dialog.getByRole('button',{name:'이미지 목록 다시 불러오기',exact:true});await expect(retry).toBeVisible({timeout:2000});await retry.click();
 await expect(dialog.getByRole('button',{name:selectedName!,exact:true})).toHaveAttribute('aria-pressed','true');await expect(dialog.getByText('Controlled picker outage',{exact:true})).toHaveCount(0);
 expect(queries).toHaveLength(failedRequests+1);expect(queries.every(query=>query===queries[0])).toBe(true);expect(hashes(workspace)).toEqual(before);
 evidence.note('list_retry',{scope:'legacy picker',requests:queries,selected_name:selectedName,real_backend_after_retry:true});await evidence.screenshot(page,'s207-picker-recovered');
});

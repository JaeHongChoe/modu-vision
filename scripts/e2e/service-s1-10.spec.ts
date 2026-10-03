import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// S1-10 on the actual renderer and harness backend. In a plain browser (no preload bridge) host features that need the
// desktop say so instead of doing nothing; with the desktop bridge they are offered. The job event cursor answers through
// the same token-holding server the renderer uses.
async function project(request:import('@playwright/test').APIRequestContext,origin:string){
 const created=await request.post(`${origin}/api/project/create`,{data:{name:'S110 workspace',task:'classification'}});expect(created.ok()).toBe(true);
}

test('a plain browser explains that it cannot open a folder dialog, and paths are typed in',async({page,request,renderer,evidence})=>{
 await project(request,renderer.origin);
 await page.goto(renderer.url);
 await expect(page.getByRole('navigation',{name:'프로젝트 작업 공간'})).toBeVisible();
 expect(await page.evaluate(()=>'api' in window),'no preload bridge in a plain browser').toBe(false);
 // the dataset step's folder button answers instead of doing nothing
 await page.getByRole('button',{name:'데이터셋 폴더 열기'}).click();
 await expect(page.getByRole('alert').filter({hasText:'브라우저에서는 폴더·파일 선택 창을 열 수 없습니다'})).toBeVisible();
 await page.getByRole('button',{name:'S110 workspace'}).first().click();
 const dialog=page.getByRole('dialog',{name:'프로젝트 관리'});await expect(dialog).toBeVisible();
 await dialog.getByRole('button',{name:'새 프로젝트'}).click();
 const browse=dialog.getByRole('button',{name:/^찾기$/}).first();
 await expect(browse).toBeDisabled();
 await expect(browse).toHaveAttribute('title',/브라우저에서는 폴더·파일 선택 창을 열 수 없습니다/);
 await evidence.screenshot(page,'s110-browser-host');
});

test('the desktop bridge offers the folder dialog',async({page,request,renderer,evidence})=>{
 await project(request,renderer.origin);
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:'S110 workspace'}).first().click();
 const dialog=page.getByRole('dialog',{name:'프로젝트 관리'});await expect(dialog).toBeVisible();
 await dialog.getByRole('button',{name:'새 프로젝트'}).click();
 await expect(dialog.getByRole('button',{name:/^찾기$/}).first()).toBeEnabled();
 await evidence.screenshot(page,'s110-desktop-host');
});

test('the job event cursor starts with a reset and continues with what the ledger records',async({request,renderer})=>{
 await project(request,renderer.origin);
 const first=await request.get(`${renderer.origin}/api/job-events`);expect(first.status()).toBe(200);
 const page1=await first.json();expect(page1.reset).toBe(true);expect(page1.reason).toBe('initial');expect(page1.events).toEqual([]);
 const next=await request.get(`${renderer.origin}/api/job-events`,{params:{after:page1.cursor}});expect(next.status()).toBe(200);
 const page2=await next.json();expect(page2.reset).toBe(false);expect(page2.events).toEqual([]);expect(page2.cursor).toBe(page1.cursor);
 const foreign=await request.get(`${renderer.origin}/api/job-events`,{params:{after:'v1.not-this-stream.1.0'}});
 expect((await foreign.json()).reason).toBe('cursor_expired');
});

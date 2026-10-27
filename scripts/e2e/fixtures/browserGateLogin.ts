import type {Page} from '@playwright/test';
import {expect} from './test';

/** Ordinary HTTPS startup/reload login; never restores a proof or preload from a fixture. */
export async function expectBrowserLoginGate(page:Page,origin:string){
  await expect(page.getByRole('main',{name:'브라우저 계정 로그인',exact:true})).toBeVisible();
  await expect(page.getByRole('navigation',{name:'Workflow Stages'})).toHaveCount(0);
  const address=page.getByLabel('공동 작업 서버 주소',{exact:true});
  await expect(address).toHaveValue(origin);
  await expect(address).toBeDisabled();
  await expect(page.getByLabel('공유 계정 이름',{exact:true})).toBeVisible();
}

/** After the gate accepts the account flow, inspect the existing management UI normally. */
export async function openBrowserServerManagement(page:Page){
  await expect(page.getByRole('main',{name:'브라우저 계정 로그인',exact:true})).toHaveCount(0);
  await page.getByRole('button',{name:'서버 관리',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'컴퓨팅 서버 관리',exact:true})).toBeVisible();
}

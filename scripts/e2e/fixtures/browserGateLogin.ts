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

/** Account login and project acceptance share the caller's original readiness frame. */
export async function chooseBrowserProject(page:Page,projectId:string,mode:'shared'|'local_host',deadline:number):Promise<void>{
  if(typeof projectId!=='string'||!/^[-A-Za-z0-9_]{1,128}$/.test(projectId)||(mode!=='shared'&&mode!=='local_host'))throw Error('Invalid explicit browser project choice');
  const remaining=()=>browserLoginRemaining(deadline);
  remaining();
  const gate=page.getByRole('main',{name:'브라우저 계정 로그인',exact:true});
  await expect(gate.getByRole('region',{name:'로그인한 계정의 프로젝트 선택',exact:true})).toBeVisible({timeout:remaining()});
  const choice=gate.getByLabel(mode==='local_host'?'작업할 로컬 프로젝트':'작업할 프로젝트',{exact:true});
  await expect(choice.locator(`option[value="${projectId}"]`)).toHaveCount(1,{timeout:remaining()});
  await expect(choice).toBeEnabled({timeout:remaining()});
  await choice.selectOption(projectId,{timeout:Math.min(10_000,remaining())});
  await expect(gate).toHaveCount(0,{timeout:remaining()});
  remaining();
}

function browserLoginRemaining(deadline:number):number{
  const timeout=Math.floor(deadline-performance.now());
  if(!Number.isFinite(deadline)||!Number.isFinite(timeout)||timeout<=0)throw Error('Original browser login preparation deadline expired');
  return timeout;
}

/** Added preparation cannot grant the original workspace assertion a new frame. */
export async function browserLoginWithin<T>(action:()=>Promise<T>,deadline:number):Promise<T>{
  const timeout=browserLoginRemaining(deadline);
  let timer:ReturnType<typeof setTimeout>|undefined;
  const expired=new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original browser login preparation deadline expired')),timeout);});
  try{
    const result=await Promise.race([action(),expired]);
    browserLoginRemaining(deadline);
    return result;
  }finally{if(timer!==undefined)clearTimeout(timer);}
}

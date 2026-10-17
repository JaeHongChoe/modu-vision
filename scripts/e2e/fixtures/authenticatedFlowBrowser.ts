import type {Page} from '@playwright/test';
import path from 'node:path';
import {expect,type Workspace,type Evidence} from './test';
import {browserTeamServer} from './browser_team_server';
import {handoffWithin,type HandoffApi} from './remaining-project-handoff';

export type FlowNavigation=(project:{id:string;name:string})=>Promise<void>;
export async function chooseFlowImage(page:Page,source:string,file:string){
  await page.getByRole('button',{name:/이미지 변경/}).click();
  const relative=path.relative(source,file).split(path.sep).join('/');
  expect(relative.startsWith('../')||path.isAbsolute(relative)).toBe(false);
  await page.getByRole('list',{name:'데이터 버전 이미지',exact:true}).getByRole('listitem',{name:relative,exact:true}).click();
  await page.getByRole('button',{name:'선택 확정',exact:true}).click();
  await expect(page.getByTitle(path.basename(file),{exact:true})).toBeVisible();
}
export async function selectFlowVersion(page:Page,version:string,pipeline:any){
  const deadline=performance.now()+10_000;
  const within=<T>(promise:Promise<T>,label:string)=>handoffWithin(promise,deadline,label);
  const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/flowchart/pipelines/'+version&&r.request().method()==='GET',{timeout:10_000});
  void response.catch(()=>{});
  let count=0,failure:unknown;const tasks:Promise<void>[]=[];
  const confirm=(dialog:import('@playwright/test').Dialog)=>{
    const task=(async()=>{try{
      expect(dialog.page()).toBe(page);expect(dialog.type()).toBe('confirm');
      expect(dialog.message()).toBe('저장하지 않은 플로우 변경 사항을 버리고 선택한 버전을 열까요?');expect(++count).toBe(1);
      await within(dialog.accept(),'exact original saved-version confirmation');
    }catch(error){failure=error;await dialog.dismiss();}})();tasks.push(task);
  };
  page.on('dialog',confirm);let wire;
  try{
    await within(page.getByLabel('저장 버전',{exact:true}).selectOption(version),'original saved-version selector');
    wire=await within(response,'original saved-version response');await within(Promise.all(tasks),'original confirmation completed');if(failure)throw failure;
  }finally{page.off('dialog',confirm);}
  expect(wire.status()).toBe(200);const raw=await within(wire.body(),'full original saved graph');expect(raw.length).toBeLessThanOrEqual(1024*1024);
  expect(JSON.parse(raw.toString('utf8'))).toEqual(pipeline);expect(await within(wire.finished(),'saved graph normally finished')).toBeNull();
  await within(expect(page.locator('[data-flow-node-id]')).toHaveCount(pipeline.nodes.length,{timeout:10_000}),'rendered saved node count');
  for(const node of pipeline.nodes)await within(expect(page.locator('[data-flow-node-id="'+node.id+'"]').getByRole('heading')).toHaveText(node.data.label,{timeout:10_000}),'rendered exact saved node');
}
/** The real picker reads only an explicitly accepted, validated revision. */
export async function activateFlowDataset(api:HandoffApi,task:string,evidence:Evidence){
  const deadline=performance.now()+10_000;
  const call=(route:string,body?:unknown)=>handoffWithin(api(route,body),deadline,'actual fixture dataset validation and adoption');
  const started=await call('/api/dataset/imports',{task,invalid_policy:'reject',verify:true,follow_links:false});
  let completed:any;
  await handoffWithin(expect.poll(async()=>{
    completed=await call('/api/dataset/imports/'+started.job_id);
    expect(['accepted','running','completed']).toContain(completed.state);
    return completed.state;
  },{timeout:10_000}).toBe('completed'),deadline,'original dataset validation completion');
  expect(completed.job_id).toBe(started.job_id);
  const revision=completed.result.revision.revision_id;
  const accepted=await call('/api/dataset/imports/'+started.job_id+'/accept',{revision_id:revision,expected_active:null});
  expect(accepted).toEqual({active_revision:revision,job_id:started.job_id});
  const library=await call('/api/dataset/library/images?limit=120');
  expect(library).toMatchObject({revision_id:revision,active:true,next_cursor:null});
  expect(library.items.length).toBeGreaterThan(0);
  evidence.note('authenticated_partial_dataset_adoption',{started,completed,accepted,library});
  return library;
}
type Inputs={page:Page;rendererBuild:{outDir:string};workspace:Workspace;evidence:Evidence;
  loopbackGuard:{allow:(port:number)=>void}};

/** A real account-owned HTTPS provider. Every renderer reload logs in through
 * the ordinary controls again: BrowserSession deliberately keeps CSRF in memory.
 * No account/process capability is injected into renderer requests or storage.
 */
export async function authenticatedFlowBrowser(inputs:Inputs,
  use:(value:{api:HandoffApi;origin:string;navigate:FlowNavigation})=>Promise<void>){
  const {page,workspace,evidence,loopbackGuard}=inputs;
  const server=await browserTeamServer(workspace,inputs.rendererBuild.outDir);
  loopbackGuard.allow(server.port);evidence.redact(server.backend.token);
  const username='partialowner',password='owned partial CPU fixture password 123';
  evidence.redact(password);let primary:unknown;
  try{
    await server.call('/api/accounts/bootstrap',{username,password});
    const owner=await server.call('/api/accounts/login',{username,password});evidence.redact(owner.token);
    const api:HandoffApi=(route,body,method)=>{
      const deadline=performance.now()+10_000;
      return handoffWithin(server.call(route,body,owner.token,method||(body===undefined?'GET':route.endsWith('/update')?'PUT':'POST')),
        deadline,'original authenticated fixture API');
    };
    const logins:Array<{actor_id:string;project_id:string;cookie_session:true;actual_UI_login:true}>=[];
    const navigate:FlowNavigation=async project=>{
      const restoringCookie=(await page.context().cookies(server.origin)).some(cookie=>cookie.name==='vision_session');
      await page.goto(server.origin);
      // An existing cookie starts an ordinary saved-source restore on reload.
      // Wait for its real gallery before login/project switching: saveOpenEdits
      // correctly refuses switching while that dataset operation is active.
      await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
      const datasetReady=async()=>{
        await expect(page.getByRole('button',{name:/ 라벨링에서 열기$/}).first()).toBeVisible({timeout:10_000});
        await expect(page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true})).toBeEnabled({timeout:10_000});
      };
      if(restoringCookie)await datasetReady();
      await page.getByRole('button',{name:'서버 관리',exact:true}).click();
      await page.getByText('공동 작업 서버',{exact:true}).click();
      await page.getByLabel('공동 작업 서버 주소').fill(server.origin);
      await page.getByLabel('공유 계정 이름',{exact:true}).fill(username);
      await page.getByLabel('공유 계정 비밀번호',{exact:true}).fill(password);
      const waiting=page.waitForResponse(response=>new URL(response.url()).origin===server.origin
        &&new URL(response.url()).pathname==='/api/accounts/login'&&response.request().method()==='POST',{timeout:10_000});
      await page.getByRole('button',{name:'연결·로그인',exact:true}).click();
      const response=await waiting;expect(response.status()).toBe(200);const session=await response.json();
      expect(session.user.id).toBe(owner.user.id);expect(session.token).toBeUndefined();
      evidence.redact(session.csrf_token);
      const select=page.getByLabel('공동 작업 프로젝트',{exact:true});
      await expect(select.locator('option[value="'+project.id+'"]')).toHaveCount(1);
      await expect(select).toBeEnabled();
      await expect(page.getByLabel('서버 데이터 원본 경로',{exact:true})).toBeEnabled();
      await datasetReady();
      if(await select.inputValue()!==project.id)await select.selectOption(project.id);
      await expect(select).toHaveValue(project.id);
      await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
      // Selection text is published before syncCurrentProject finishes. Wait
      // for the ordinary controls to confirm the real context adoption.
      await expect(select).toBeEnabled();
      await expect(page.getByLabel('서버 데이터 원본 경로',{exact:true})).toBeEnabled();
      await page.getByRole('dialog',{name:'컴퓨팅 서버 관리',exact:true}).getByRole('button',{name:'닫기',exact:true}).click();
      // A different project may restore its own remembered later stage.
      await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
      await datasetReady();
      const cookies=await page.context().cookies(server.origin);
      expect(cookies.find(cookie=>cookie.name==='vision_session')).toMatchObject({httpOnly:true,secure:true,sameSite:'Strict'});
      logins.push({actor_id:owner.user.id,project_id:project.id,cookie_session:true,actual_UI_login:true});
      evidence.note('authenticated_partial_browser_logins',logins.slice());
    };
    await use({api,origin:server.origin,navigate});
  }catch(error){primary=error;}
  finally{
    try{evidence.note('authenticated_partial_browser_provider_close',await server.close());}
    catch(error){primary??=error;}
  }
  if(primary!==undefined)throw primary;
}

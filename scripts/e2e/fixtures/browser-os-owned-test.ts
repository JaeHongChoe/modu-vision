import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {chromium,type Page,type BrowserContext} from '@playwright/test';
import {test as base,expect} from './test';
import {browserGrant,validateBrowserOwnerFacts,type BrowserOwner} from './browserOSPopupBridge';
export {expect};
export type {Workspace,Evidence} from './test';
type Session={context:BrowserContext;page:Page;owner:BrowserOwner;origin:string};
const LOOPBACK=/^(https?|wss?):\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?\//i;
export const test=base.extend<{browserOSSession:Session}>({
 browserOSSession:async({renderer,evidence,loopbackGuard},use)=>{
  const grant=browserGrant();
  evidence.note('fresh_owned_browser_OS_app_grant',grant);evidence.addFile(process.env.MV_E2E_F057_BROWSER_GRANT!);
  // A new empty, retained profile and original BrowserContext. No user browser
  // attachment, copied preferences, remote port discovery or PID-based stop.
  expect(fs.readdirSync(grant.profile)).toEqual([]);
  const context=await chromium.launchPersistentContext(grant.profile,{executablePath:grant.exe,headless:false,viewport:{width:1440,height:900},timeout:60_000});
  const pageErrors:string[]=[],consoleErrors:string[]=[];let first:unknown,sessionClosed=false,cdpClosed=false,owner:BrowserOwner|undefined;
  const portOf=(url:string)=>Number(new URL(url).port||(/^(https|wss):/i.test(url)?443:80));
  try{
   const pages=context.pages();expect(pages).toHaveLength(1);const page=pages[0];
   await context.route(LOOPBACK,route=>{const url=route.request().url();if(loopbackGuard.isAllowed(portOf(url)))return route.continue();loopbackGuard.recordBlocked(url);return route.abort('blockedbyclient');});
   await context.routeWebSocket(LOOPBACK,socket=>{if(loopbackGuard.isAllowed(portOf(socket.url())))socket.connectToServer();else{loopbackGuard.recordBlocked(socket.url());socket.close();}});
   page.on('pageerror',error=>pageErrors.push(error.message));page.on('console',message=>{if(message.type()==='error')consoleErrors.push(message.text());});
   // Own only display metadata, as Source02 owns its native window title.
   // No input/change/request behavior or application source is modified.
   await page.addInitScript(title=>{window.addEventListener('DOMContentLoaded',()=>{document.title=title;const el=document.querySelector('title');if(el)new MutationObserver(()=>{if(document.title!==title)document.title=title;}).observe(el,{childList:true,subtree:true,characterData:true});},{once:true});},grant.title);
   await page.goto(renderer.url);await page.waitForLoadState('domcontentloaded');expect(await page.title()).toBe(grant.title);
   const browser=context.browser();expect(browser).not.toBeNull();const cdp=await browser!.newBrowserCDPSession();
   try{
    const processes=await cdp.send('SystemInfo.getProcessInfo');const own=processes.processInfo.filter((p:any)=>p.type==='browser');expect(own).toHaveLength(1);
    const pid=own[0].id;const target=await cdp.send('Target.getTargets');const actual=target.targetInfos.filter((t:any)=>t.type==='page'&&t.url===page.url());expect(actual).toHaveLength(1);
    const window=await cdp.send('Browser.getWindowForTarget',{targetId:actual[0].targetId});
    const command=execFileSync('/bin/ps',['-p',String(pid),'-o','command='],{encoding:'utf8'}).trim();
    owner={pid,exe:grant.exe,appPath:grant.appPath,bundleID:grant.bundleID,title:grant.title,windowID:window.windowId,windowID_kind:'original_CDP_Browser_getWindowForTarget',targetID:actual[0].targetId,url:page.url(),userData:grant.profile,original_context_retained:true,official_bundle_metadata_unchanged:true};
    validateBrowserOwnerFacts(owner,grant,{page_count:context.pages().length,browser_process_count:own.length,command});
   }finally{await cdp.detach();cdpClosed=true;}
   evidence.note('fresh_owned_browser_OS_app_window',owner);
   await use({context,page,owner:owner!,origin:renderer.origin});
  }catch(error){first=error;}finally{
   // Close the exact original context, never a discovered PID or foreign app.
   try{await context.close();sessionClosed=true;}catch(error){first??=error;}
   evidence.note('browser_OS_fixture_closure',{original_context_closed:sessionClosed,original_browser_CDP_session_closed:cdpClosed,original_context_handle_retained:true,PID_stop_or_adoption:false,user_browser_settings_or_security_changed:false,first_exception:first===undefined?null:String(first)});
   evidence.note('page_errors',pageErrors);evidence.note('console_errors',consoleErrors);evidence.note('blocked_loopback_requests',loopbackGuard.blocked());
  }
  if(first!==undefined)throw first;
 }
});

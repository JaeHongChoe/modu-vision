import type {Page} from '@playwright/test';
import {test,expect,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
type Api=(route:string,body?:unknown)=>Promise<any>;
async function exercise(page:Page,evidence:Evidence,api:Api,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Language help fixture',task:'classification'});
 if(url)await page.goto(url);else await page.reload();
 await page.getByRole('button',{name:'가이드 펼치기',exact:true}).click();
 await page.getByTitle('Toggle Language (KR / EN)').click();
 await expect(page.getByText('Before continuing',{exact:true})).toBeVisible();
 await expect(page.getByText('Open an image folder to label it in step 2.',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Glossary',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Manufacturing vision AI glossary',exact:true});
 await expect(dialog).toBeVisible();await dialog.getByRole('textbox',{name:'Search glossary'}).fill('escape');
 await expect(dialog).toContainText('An actual NG item predicted as OK');
 await expect(dialog).not.toContainText('The production cycle time per item.');
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
 await page.reload();await expect(page.getByTitle('Toggle Language (KR / EN)')).toContainText('EN');
 await expect(page.getByText('Before continuing',{exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Collapse guidance',exact:true})).toHaveAttribute('aria-expanded','true');
 await page.getByRole('button',{name:'Operator inspection',exact:true}).click();
 await expect(page.getByRole('contentinfo')).toHaveCount(0);
 await expect(page.getByRole('button',{name:'Task center',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Operator inspection workspace',exact:true}).getByRole('searchbox')).toHaveAttribute('placeholder','Search by file name or folder');
 await page.keyboard.press('F1');let help=page.getByRole('complementary',{name:'Operator help',exact:true});await expect(help).toBeVisible();
 await page.getByTitle('Toggle Language (KR / EN)').click();
 help=page.getByRole('complementary',{name:'작업자 도움말',exact:true});await expect(help).toBeVisible();await expect(help).toContainText('Tab/Shift+Tab');
 await page.keyboard.press('Escape');await expect(help).toHaveCount(0);
 await evidence.screenshot(page,`${native?'native':'browser'}-language-settings-help`);
 evidence.note('language_help',{project_id:project.id,english_glossary_search:true,live_setting_without_context_change:true,reload_language:'en',reload_guidance:'expanded',f1_toggle:true,escape_close:true,no_service_started:true});
}
test('language settings and guidance persist and glossary help follows the chosen language',async({page,renderer,evidence})=>{
 const api:Api=async(route,body)=>{const r=await page.request.fetch(renderer.origin+route,{method:body?'POST':'GET',data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,evidence,api,false,renderer.url);
});
test('native language guidance glossary and operator keyboard help survive reload',{tag:'@electron'},async({electronSession,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body)=>page.evaluate(async({port,route,body})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned help fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body});await exercise(page,evidence,api,true);
});

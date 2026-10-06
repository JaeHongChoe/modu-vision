import type {Page} from '@playwright/test';
import {test,expect} from './fixtures/test';
async function installation(page:Page){
 await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
 await page.getByRole('navigation',{name:'배포 운영 화면',exact:true}).getByRole('button',{name:'설치·진단',exact:true}).click();
}
test('browser shows desktop-only offline verification without requesting a file',async({page,renderer,request,evidence})=>{
 const response=await request.post(renderer.origin+'/api/project/create',{data:{name:'Browser offline boundary',task:'classification'}});expect(response.ok(),await response.text()).toBe(true);
 await page.goto(renderer.url);await installation(page);
 await expect(page.getByRole('button',{name:'오프라인 패키지 검증',exact:true})).toBeDisabled();
 await expect(page.getByRole('alert').filter({hasText:'배포 상태 확인은 데스크톱 앱'})).toBeVisible();
 await evidence.screenshot(page,'browser-offline-release-desktop-only');evidence.note('offline_boundary',{browser:true,native_picker_requested:false,installation_occurred:false});
});
test('development Electron refuses offline handoff before a native picker or installation',{tag:'@electron'},async({electronSession,evidence})=>{
 const {window}=electronSession;const backend=await electronSession.waitForBackend();
 await window.evaluate(async port=>{const r=await fetch(`http://127.0.0.1:${port}/api/project/create`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:'Native offline boundary',task:'classification'})});if(!r.ok)throw Error(await r.text());},backend.port);
 await window.reload();await installation(window);
 await window.getByRole('button',{name:'오프라인 패키지 검증',exact:true}).click();
 await expect(window.getByRole('alert').filter({hasText:'Offline release handoff requires'})).toBeVisible();
 await evidence.screenshot(window,'native-development-offline-handoff-refused');evidence.note('offline_boundary',{development_native:true,pinned_publisher_required:true,installation_occurred:false});
});

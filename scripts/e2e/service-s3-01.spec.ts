import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// Actual renderer and backend on the owned synthetic harness: the durable import job reads the real dataset folder
// (two synthetic images plus one corrupt file); nothing is faked over the transport.
test('a validated revision reads every image, a reject policy holds it, and only an explicit accept activates one',async({page,renderer,workspace,evidence})=>{
 fs.writeFileSync(path.join(workspace.dataset,'ng','broken.png'),'not an image');
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S301 import',task:'classification'}});expect(created.ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const serverErrors:string[]=[];
 page.on('response',response=>{const path=new URL(response.url()).pathname;if(path.startsWith('/api/')&&response.status()>=500)serverErrors.push(`${response.status()} ${path}`);});
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:/01.*데이터 관리/}).click();
 const open=page.getByRole('button',{name:'검증된 데이터 버전',exact:true});
 await expect(open).toBeEnabled({timeout:30000});await open.click();
 const dialog=page.getByRole('dialog',{name:'검증된 데이터 버전'});await expect(dialog).toBeVisible();
 await expect(dialog.getByText('REGISTERED SOURCE')).toBeVisible();

 await dialog.getByLabel(/하나라도 있으면 거부/).check();
 await dialog.getByRole('button',{name:'전체 검증 실행'}).click();
 const current=dialog.getByRole('region',{name:'현재 가져오기'});
 await expect(current).toContainText('검증 완료 · 채택 전',{timeout:30000});
 await expect(current.getByRole('definition').nth(0)).toHaveText('3');
 await expect(current.getByRole('definition').nth(1)).toHaveText('2');
 await expect(current.getByRole('definition').nth(2)).toHaveText('1');
 const excluded=dialog.getByRole('region',{name:'손상·제외 이미지'});
 await expect(excluded).toContainText('ng/broken.png');await expect(excluded).toContainText('UNIDENTIFIED_IMAGE');
 await expect(dialog.getByRole('button',{name:'이 버전 채택'})).toBeDisabled();
 await expect(current).toContainText('거부 정책');
 await evidence.screenshot(page,'s301-reject-held');

 await dialog.getByLabel(/제외하고 기록/).check();
 await dialog.getByRole('button',{name:'전체 검증 실행'}).click();
 await expect(current).toContainText('검증 완료 · 채택 전',{timeout:30000});
 const accept=dialog.getByRole('button',{name:'이 버전 채택'});await expect(accept).toBeEnabled();
 await accept.click();await expect(dialog.getByRole('button',{name:'채택 확인'})).toBeVisible();
 await expect(current).toContainText('현재 활성 버전 없음');
 const before=await (await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
 expect(before.active_revision,'a finished scan activates nothing').toBeNull();
 await dialog.getByRole('button',{name:'채택 확인'}).click();
 await expect(dialog.getByRole('region',{name:'버전 목록'})).toContainText('활성');
 await expect(current).toContainText('검증 완료 · 활성 버전');
 const after=await (await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
 expect(after.revisions).toHaveLength(2);
 const active=after.revisions.find((row:{active:boolean})=>row.active);
 expect(active.invalid_policy).toBe('exclude');expect(active.error_count).toBe(1);expect(after.active_revision).toBe(active.revision_id);
 await evidence.screenshot(page,'s301-accepted');
 expect(serverErrors,'no server error while the dataset screen lists a corrupt image').toEqual([]);
 const statistics=await page.request.get(`${renderer.origin}/api/dataset/metadata/statistics?folder_path=${encodeURIComponent(workspace.dataset)}`);
 expect(statistics.status(),'statistics still answer with a corrupt image in the source').toBe(200);
 for(const image of (workspace as unknown as {images:Array<{path:string;sha256:string}>}).images)
  expect(crypto.createHash('sha256').update(fs.readFileSync(image.path)).digest('hex'),'the source is only read').toBe(image.sha256);
});

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

// Actual renderer and backend on the owned synthetic harness: the same bytes filed under two labels are reported as a
// conflicting duplicate group, listed in the panel, and left in place.
test('a validated revision reports byte-identical images under different labels and removes nothing',async({page,renderer,workspace,evidence})=>{
 const images=(workspace as unknown as {images:Array<{label:string;path:string;sha256:string}>}).images;
 const ok=images.find(image=>image.label==='ok')!;
 const copy=path.join(workspace.dataset,'ng','copy-of-ok.png');fs.copyFileSync(ok.path,copy);
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S301 duplicates',task:'classification'}});expect(created.ok()).toBe(true);
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const serverErrors:string[]=[];
 page.on('response',response=>{const pathname=new URL(response.url()).pathname;if(pathname.startsWith('/api/')&&response.status()>=500)serverErrors.push(`${response.status()} ${pathname}`);});
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:/01.*데이터 관리/}).click();
 const open=page.getByRole('button',{name:'검증된 데이터 버전',exact:true});
 await expect(open).toBeEnabled({timeout:30000});await open.click();
 const dialog=page.getByRole('dialog',{name:'검증된 데이터 버전'});await expect(dialog).toBeVisible();
 await dialog.getByRole('button',{name:'전체 검증 실행'}).click();
 const current=dialog.getByRole('region',{name:'현재 가져오기'});
 await expect(current).toContainText('검증 완료 · 채택 전',{timeout:30000});
 const notes=current.getByRole('list',{name:'주석과 중복'});
 await expect(notes).toContainText('같은 내용 그룹 1개(이미지 2장)');
 await expect(notes).toContainText('라벨이 다른 그룹 1개');
 await expect(notes).toContainText('주석 파일(LabelMe·COCO·YOLO)이 연결된 이미지 0장');
 const duplicates=dialog.getByRole('region',{name:'같은 내용의 이미지'});
 await expect(duplicates).toContainText('ng/copy-of-ok.png');await expect(duplicates).toContainText('ok/sample-ok.png');
 await expect(duplicates).toContainText('라벨 다름');
 await duplicates.getByRole('radio',{name:'분할 섞임'}).click();
 await expect(duplicates).toContainText('이 조건의 그룹이 없습니다.');
 await duplicates.getByRole('radio',{name:'라벨 다름'}).click();
 await expect(duplicates).toContainText('ng/copy-of-ok.png');
 await evidence.screenshot(page,'s301-duplicates');
 const listed=await (await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
 const revision=listed.revisions[0];
 expect([revision.annotations_scanned,revision.duplicate_groups,revision.conflicting_duplicates,revision.cross_split_duplicates]).toEqual([true,1,1,0]);
 const served=await (await page.request.get(`${renderer.origin}/api/dataset/revisions/${revision.revision_id}/duplicates?kind=conflicting`)).json();
 expect(served.groups.map((group:{members:number})=>group.members)).toEqual([2]);
 expect(serverErrors,'no server error while duplicates are listed').toEqual([]);
 expect(fs.existsSync(copy),'a duplicate is reported, never removed').toBe(true);
 for(const image of images)
  expect(crypto.createHash('sha256').update(fs.readFileSync(image.path)).digest('hex'),'the source is only read').toBe(image.sha256);
});

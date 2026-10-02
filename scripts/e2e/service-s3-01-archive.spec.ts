import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import {expect,test} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

/** A stored (uncompressed) ZIP with UTF-8 names, enough for a dataset archive fixture. */
function storedZip(entries:Array<[string,Buffer]>):Buffer{
 const locals:Buffer[]=[],centrals:Buffer[]=[];let offset=0;
 for(const [name,data] of entries){
  const encoded=Buffer.from(name,'utf8'),crc=zlib.crc32(data)>>>0;
  const local=Buffer.alloc(30);local.writeUInt32LE(0x04034b50,0);local.writeUInt16LE(20,4);local.writeUInt16LE(0x0800,6);
  local.writeUInt32LE(crc,14);local.writeUInt32LE(data.length,18);local.writeUInt32LE(data.length,22);local.writeUInt16LE(encoded.length,26);
  const central=Buffer.alloc(46);central.writeUInt32LE(0x02014b50,0);central.writeUInt16LE(20,4);central.writeUInt16LE(20,6);
  central.writeUInt16LE(0x0800,8);central.writeUInt32LE(crc,16);central.writeUInt32LE(data.length,20);central.writeUInt32LE(data.length,24);
  central.writeUInt16LE(encoded.length,28);central.writeUInt32LE(offset,42);
  locals.push(local,encoded,data);centrals.push(central,encoded);offset+=30+encoded.length+data.length;}
 const directory=Buffer.concat(centrals),end=Buffer.alloc(22);end.writeUInt32LE(0x06054b50,0);
 end.writeUInt16LE(entries.length,8);end.writeUInt16LE(entries.length,10);end.writeUInt32LE(directory.length,12);end.writeUInt32LE(offset,16);
 return Buffer.concat([...locals,directory,end]);}

// Actual renderer and backend on the owned synthetic harness: a ZIP picked in the panel is hashed and uploaded in
// chunks, extracted into the project folder, validated as a revision, and the registered source is left untouched.
test('a dataset ZIP picked in the panel is uploaded, extracted into the project and validated',async({page,renderer,workspace,evidence})=>{
 const images=(workspace as unknown as {images:Array<{label:string;path:string;sha256:string}>}).images;
 const archive=storedZip([['양품/검사 01.png',fs.readFileSync(images.find(image=>image.label==='ok')!.path)],
  ['불량/검사 02.png',fs.readFileSync(images.find(image=>image.label==='ng')!.path)],['불량/깨진 파일.png',Buffer.from('not an image')]]);
 const zipPath=path.join(workspace.root,'검사 데이터셋.zip');fs.writeFileSync(zipPath,archive);
 const created=await page.request.post(`${renderer.origin}/api/project/create`,{data:{name:'S301 archive',task:'classification'}});expect(created.ok()).toBe(true);
 const project=await created.json();
 expect((await page.request.put(`${renderer.origin}/api/project/update`,{data:{source_dataset_dir:workspace.dataset}})).ok()).toBe(true);
 const serverErrors:string[]=[];
 page.on('response',response=>{const pathname=new URL(response.url()).pathname;if(pathname.startsWith('/api/')&&response.status()>=500)serverErrors.push(`${response.status()} ${pathname}`);});
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.getByRole('button',{name:/01.*데이터 관리/}).click();
 const open=page.getByRole('button',{name:'검증된 데이터 버전',exact:true});
 await expect(open).toBeEnabled({timeout:30000});await open.click();
 const dialog=page.getByRole('dialog',{name:'검증된 데이터 버전'});await expect(dialog).toBeVisible();
 const zipSection=dialog.getByRole('region',{name:'ZIP으로 가져오기'});
 await zipSection.getByLabel('가져올 ZIP 파일').setInputFiles(zipPath);
 await expect(zipSection).toContainText('검사 데이터셋.zip');
 await zipSection.getByRole('button',{name:'ZIP 올리고 검증'}).click();
 const current=dialog.getByRole('region',{name:'현재 가져오기'});
 await expect(current).toContainText('검증 완료 · 채택 전',{timeout:30000});
 const digest=crypto.createHash('sha256').update(archive).digest('hex');
 await expect(current).toContainText(`읽은 원본: 업로드한 ZIP · ${digest.slice(0,12)}`);
 await expect(current.getByRole('definition').nth(0)).toHaveText('3');
 await expect(current.getByRole('definition').nth(1)).toHaveText('2');
 await expect(dialog.getByRole('region',{name:'손상·제외 이미지'})).toContainText('불량/깨진 파일.png');
 await evidence.screenshot(page,'s301-archive-imported');
 const extracted=path.join(project.project_dir,'dataset_imports',digest);
 expect(fs.readFileSync(path.join(extracted,'양품','검사 01.png')).equals(fs.readFileSync(images.find(image=>image.label==='ok')!.path))).toBe(true);
 const listed=await (await page.request.get(`${renderer.origin}/api/dataset/revisions`)).json();
 expect(listed.revisions[0].source_root).toBe(extracted);
 expect(serverErrors,'no server error during upload and import').toEqual([]);
 for(const image of images)
  expect(crypto.createHash('sha256').update(fs.readFileSync(image.path)).digest('hex'),'the registered source is only read').toBe(image.sha256);
});

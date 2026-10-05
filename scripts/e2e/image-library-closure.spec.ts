import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const prefix=native?'native':'browser',name='Image library complete fixture';
 const project=await api('/api/project/create',{name,task:'classification'});
 const original:Array<{path:string;sha256:string}>=workspace.images.map(i=>({path:i.path,sha256:i.sha256}));
 for(let i=0;i<245;i++){
  const file=path.join(workspace.dataset,'ng',`part-${String(i).padStart(3,'0')}.png`),bytes=png(8,3,(x,y)=>[i,x*10,y*10]);
  fs.writeFileSync(file,bytes);original.push({path:file,sha256:sha(bytes)});
 }
 const damaged=path.join(workspace.dataset,'ng','damaged.png'),annotation=path.join(workspace.dataset,'ng','part-244.json');
 fs.writeFileSync(damaged,'owned damaged image fixture');fs.writeFileSync(annotation,'{broken annotation fixture');
 original.push({path:damaged,sha256:sha(fs.readFileSync(damaged))},{path:annotation,sha256:sha(fs.readFileSync(annotation))});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 const started=await api('/api/dataset/imports',{task:'classification',verify:true});let view:any;
 for(let i=0;i<200;i++){view=await api(`/api/dataset/imports/${started.job_id}`);if(['completed','failed','aborted','interrupted'].includes(view.state))break;await new Promise(r=>setTimeout(r,50));}
 expect(view.state).toBe('completed');await api(`/api/dataset/imports/${started.job_id}/accept`,{revision_id:view.result.revision.revision_id,expected_active:null});
 const rows=await api('/api/dataset/metadata?limit=500');expect(rows.total).toBe(248);
 const target=rows.items.find((r:any)=>r.relative_path==='ng/part-244.png');expect(target).toBeTruthy();
 const edit=await api(`/api/dataset/metadata/${target.image_uuid}`,{expected_revision:target.revision,actor:'fixture',changes:{tags:['inspection-ready'],product:'Fixture board',lot:'Lot-7'}},'PATCH');
 expect(edit.tags).toEqual(['inspection-ready']);
 const requests:Array<{q:string|null;tag:string|null;product:string|null;lot:string|null;error:string|null;limit:string|null;cursor:boolean}>=[];
 const prohibited:string[]=[];
 page.on('request',r=>{const u=new URL(r.url());if(u.pathname==='/api/dataset/library/images')requests.push({q:u.searchParams.get('q'),tag:u.searchParams.get('tag'),product:u.searchParams.get('product'),lot:u.searchParams.get('lot'),error:u.searchParams.get('error'),limit:u.searchParams.get('limit'),cursor:u.searchParams.has('cursor')});if(r.method()==='POST'&&/\/(training\/start|compute\/jobs|train)$/.test(u.pathname))prohibited.push(u.pathname);});
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('button',{name:/05.*플로우차트/}).click();await page.getByRole('button',{name:'이미지 변경...'}).click();};
 await navigate();const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),grid=picker.getByRole('list',{name:'데이터 버전 이미지'});
 await expect(grid.getByRole('listitem').first()).toBeVisible();expect(await grid.getByRole('listitem').count()).toBeLessThan(120);
 await picker.getByLabel('태그',{exact:true}).fill('inspection-ready');await picker.getByLabel('제품',{exact:true}).fill('Fixture board');await picker.getByLabel('Lot',{exact:true}).fill('Lot-7');
 await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText('part-244.png');
 await picker.getByLabel('Lot',{exact:true}).fill('missing');await expect(grid.getByRole('listitem')).toHaveCount(0);await expect(picker).toContainText('조건에 맞는 이미지가 없습니다.');await picker.getByLabel('Lot',{exact:true}).fill('Lot-7');await expect(grid.getByRole('listitem')).toHaveCount(1);
 await picker.getByLabel('오류 종류',{exact:true}).selectOption('annotation');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText('라벨 오류');
 const exact=await api('/api/dataset/library/images?tag=inspection-ready&product=Fixture%20board&lot=Lot-7&error=annotation');expect(exact.items).toHaveLength(1);expect(exact.items[0].relative_path).toBe('ng/part-244.png');expect(exact.items[0].valid).toBe(true);expect(exact.items[0].annotation_error).toBeTruthy();
 await grid.getByRole('listitem').click();await expect(picker).toContainText('선택: ng/part-244.png');await evidence.screenshot(page,`${prefix}-combined-metadata-label-error`);await picker.getByRole('button',{name:'선택 확정'}).click();await expect(picker).toBeHidden();
 await navigate();await expect(picker).toContainText('선택: ng/part-244.png');await expect(picker.getByRole('button',{name:'선택 확정'})).toBeEnabled();
 await picker.getByLabel('오류 종류',{exact:true}).selectOption('image');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid).toContainText('damaged.png');await expect(grid).not.toContainText('part-244.png');await evidence.screenshot(page,`${prefix}-source-error-selected-identity-retained`);
 await picker.getByLabel('오류 종류',{exact:true}).selectOption('');await picker.getByLabel('이미지 검색',{exact:true}).fill('part-244');await expect(grid.getByRole('listitem')).toHaveCount(1);await expect(grid.getByRole('listitem')).toHaveAttribute('aria-pressed','true');
 const selected=await page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,value])=>({key,value:JSON.parse(value)})));expect(selected).toHaveLength(1);expect(selected[0].value.imageUuid).toBe(exact.items[0].image_uuid);expect(selected[0].value.sha256).toBe(exact.items[0].sha256);
 for(const file of original)expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
 expect(requests.some(q=>q.tag==='inspection-ready'&&q.product==='Fixture board'&&q.lot==='Lot-7'&&q.error==='annotation')).toBe(true);expect(requests.every(q=>Number(q.limit)<=120)).toBe(true);expect(prohibited).toEqual([]);
 evidence.addFile(annotation);evidence.addFile(damaged);evidence.addFile(path.join(workspace.dataset,'ng','part-244.png'));
 evidence.note('library_closure',{project_id:project.id,revision:view.result.revision.revision_id,total:248,original,exact:exact.items[0],metadata:edit,selection:selected[0],requests,prohibited,actual_ui_and_backend:true,virtualized:true,reopened_identity:true,source_unchanged:true,fixture_model_quality_not_assessed:true});
}
test('metadata and error filters find an image outside the first 200 and preserve its reopened identity',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const r=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(r.ok(),`Owned fixture ${route}: HTTP ${r.status()}`).toBe(true);return r.json();};await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native metadata and error filters find an image outside the first 200 and preserve its reopened identity',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!r.ok)throw Error(`Owned fixture HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(window,workspace,evidence,api,true);
});

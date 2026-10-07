import fs from 'node:fs';
import {createHash} from 'node:crypto';
import type {Page,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');

// Disposable synthetic book/assignment metadata does not approve model truth.
async function exercise(page:Page,w:Workspace,e:Evidence,api:Api,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Owned book draft controls',task:'segmentation'});
 await api('/api/project/update',{source_dataset_dir:w.dataset},'PUT');await api('/api/dataset/import',{folder_path:w.dataset,task:'segmentation'});
 await api('/api/team-data/books',{expected_version:0,actor:'fixture-owner',title:'Controlled original book',categories:[{id:0,name:'OK',color:'#10b981'},{id:2,name:'Scratch',color:'#f59e0b',definition:'Controlled scratch'}]});
 const original=await api('/api/team-data'),rows=await api('/api/team-data/queue?offset=0&limit=30');expect(rows.items).toHaveLength(2);
 const image=rows.items.find((row:any)=>row.file_path===w.images[0].path);expect(image).toBeTruthy();
 if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(1).click();await page.getByRole('button',{name:'집중 편집',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'팀 데이터 작업',exact:true});
 const open=async()=>{await page.getByRole('button',{name:'팀 작업 · 라벨 기준·검수',exact:true}).click();await expect(dialog).toBeVisible();};
 const close=async()=>{await dialog.getByRole('button',{name:'팀 데이터 작업 닫기',exact:true}).click();await expect(dialog).toHaveCount(0);};
 const preserved=async()=>{const state=await api('/api/team-data');expect(state.book).toEqual(original.book);expect(state.book_history).toEqual(original.book_history);expect(state.settings).toEqual(original.settings);expect((await api('/api/team-data/queue?offset=0&limit=30')).items).toEqual(rows.items);};
 const mutations:Array<{method:string;path:string}>=[],observe=(request:any)=>{const pathname=new URL(request.url()).pathname;if(request.method()!=='GET'&&(pathname.startsWith('/api/team-data')||pathname.startsWith('/api/annotations')||pathname.startsWith('/api/training')))mutations.push({method:request.method(),path:pathname});};
 page.on('request',observe);
 const transport=async(route:Route)=>{if(route.request().method()==='POST')await route.fulfill({status:503,json:{detail:'Controlled label book POST transport failure'}});else await route.continue();};
 try{
  await open();await dialog.getByRole('region',{name:'팀 작업 목록',exact:true}).getByRole('button',{name:image.relative_path,exact:true}).click();
  const current=dialog.getByRole('region',{name:'현재 이미지 팀 작업',exact:true});await expect(current).toContainText(image.relative_path);
  await dialog.getByLabel('팀 작업자 이름',{exact:true}).fill('fixture-manager');await current.getByLabel('이미지 담당자',{exact:true}).fill('unsent-assignee');await current.getByLabel('작업 우선순위',{exact:true}).fill('91');
  await close();await preserved();expect(mutations).toEqual([]);await open();await expect(current.getByLabel('이미지 담당자',{exact:true})).toHaveValue('unsent-assignee');
  await current.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-assignment-closed-draft-not-submitted`);
  // The parent retains the unsent input; close is not a cancellation of an
  // already dispatched mutation and does not claim an assignment rollback.
  await dialog.getByRole('tab',{name:'라벨 기준서',exact:true}).click();const book=dialog.getByRole('region',{name:'공유 라벨 기준서',exact:true}),publish=book.getByRole('button',{name:'새 기준 버전 발행',exact:true});
  await expect(book.getByLabel('라벨 기준서 제목',{exact:true})).toHaveValue(original.book.title);
  await dialog.getByLabel('팀 작업자 이름',{exact:true}).fill('');await publish.click();await expect(dialog.getByRole('alert')).toContainText('작업자 이름을 입력하세요.');await preserved();expect(mutations).toEqual([]);
  await publish.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-book-empty-actor-local-refusal-no-post`);
  await dialog.getByLabel('팀 작업자 이름',{exact:true}).fill('fixture-manager');await book.getByLabel('기준서 새 클래스',{exact:true}).fill('OK');await book.getByRole('button',{name:'클래스 추가',exact:true}).click();await publish.click();
  await expect(book.getByRole('alert')).toContainText('클래스 이름이 중복됩니다.');await preserved();expect(mutations).toEqual([]);await publish.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-book-duplicate-name-local-refusal-no-post`);
  await close();await open();await dialog.getByRole('tab',{name:'라벨 기준서',exact:true}).click();await expect(book.getByLabel('라벨 기준서 제목',{exact:true})).toHaveValue(original.book.title);
  await book.getByLabel('라벨 기준서 제목',{exact:true}).fill('Unpublished controlled draft');await book.getByRole('button',{name:'Scratch',exact:true}).click();await book.getByLabel('클래스 정의',{exact:true}).fill('Unpublished controlled definition');
  await close();await preserved();expect(mutations).toEqual([]);await open();await dialog.getByRole('tab',{name:'라벨 기준서',exact:true}).click();await expect(book.getByLabel('라벨 기준서 제목',{exact:true})).toHaveValue(original.book.title);await book.getByRole('button',{name:'Scratch',exact:true}).click();await expect(book.getByLabel('클래스 정의',{exact:true})).toHaveValue('Controlled scratch');
  await book.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-book-explicit-close-discards-unsent-draft`);
  await book.getByLabel('라벨 기준서 제목',{exact:true}).fill('Controlled new book');await book.getByLabel('기준서 새 클래스',{exact:true}).fill('OwnedEdge');await book.getByRole('button',{name:'클래스 추가',exact:true}).click();await book.getByLabel('클래스 정의',{exact:true}).fill('Synthetic owned edge guideline');
  await page.route('**/api/team-data/books',transport);const refusal=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/team-data/books');await publish.click();expect((await refusal).status()).toBe(503);
  await expect(dialog.getByRole('alert')).toContainText('Controlled label book POST transport failure');await preserved();expect(mutations).toEqual([{method:'POST',path:'/api/team-data/books'}]);await publish.scrollIntoViewIfNeeded();await e.screenshot(page,`${native?'native':'browser'}-book-controlled-post-503-preserves-v1`);
  await page.unroute('**/api/team-data/books',transport);const accepted=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/team-data/books');await publish.click();const response=await accepted;expect(response.status()).toBe(200);const published=await response.json();expect(published.version).toBe(2);expect(published.title).toBe('Controlled new book');expect(published.categories.find((row:any)=>row.name==='OwnedEdge').definition).toBe('Synthetic owned edge guideline');
  await expect(dialog.getByRole('status')).toContainText('새 라벨 기준 버전을 발행했습니다.');const after=await api('/api/team-data');expect(after.book).toEqual(published);expect(after.book_history).toHaveLength(original.book_history.length+1);expect(after.book_history[0]).toEqual(original.book);expect(after.settings).toEqual(original.settings);
  await close();const palette=page.getByRole('button',{name:/^OwnedEdge(?: \d+)?$/});await expect(palette).toHaveCount(1);await palette.click();await expect(palette).toHaveAttribute('aria-pressed','true');await expect(page.getByRole('button',{name:/^Scratch(?: \d+)?$/})).toHaveAttribute('aria-pressed','false');await expect(page.getByText('공유 기준 v2 · 클래스 추가는 팀 기준서에서',{exact:true})).toBeVisible();
  await e.screenshot(page,`${native?'native':'browser'}-book-published-version-handoff-to-exact-label-palette`);
  const later=(await api('/api/team-data/images/'+image.image_uuid)).image;expect(later.content_hash).toBe(image.content_hash);expect(later.annotation_hash).toBe(image.annotation_hash);expect(later.mask_hash).toBe(image.mask_hash);expect(later.team.reviews).toEqual(image.team.reviews);
  expect(mutations).toEqual([{method:'POST',path:'/api/team-data/books'},{method:'POST',path:'/api/team-data/books'}]);for(const file of w.images)expect(sha(file.path)).toBe(file.sha256);
  e.note('team_book_draft_controls',{record_id:'F024',book_action:'publish-book',book_dimensions:['empty','invalid','error','cancel','handoff'],assignment_action:'assign',assignment_dimensions:['cancel'],project_id:project.id,original,published,after,selected_image_uuid:image.image_uuid,original_images:rows.items,selected_image_after:later,empty:{blank_actor_local_refusal:true,no_POST:true},invalid:{duplicate_name:'OK',local_validation_refusal:true,no_POST:true},error:{exact_POST_books_controlled_503:true,original_POST_not_dispatched:true,original_book_history_settings_images_preserved:true,explicit_retry_status:200},book_cancel:{explicit_close:true,unsent_title_definition_discarded:true,no_POST:true},assignment_cancel:{explicit_close:true,no_POST:true,record_unchanged:true,unsent_draft_retained:true,dispatched_mutation_not_cancelled:true},handoff:{book_version:2,book_sha256:published.sha256,category_name:'OwnedEdge',category_id:published.categories.find((row:any)=>row.name==='OwnedEdge').id,exact_palette_selected:true,no_annotation_write:true},ui_mutations:mutations,source_ui:true,source_electron:native,synthetic_control:true,human_review_or_model_truth:false,model_quality_accepted:false,actual_model_inference:false,installed_target_verified:false,gpu_used:false,windows_excluded:true});
 }finally{page.off('request',observe);if(!page.isClosed())await page.unroute('**/api/team-data/books',transport);}
}
test('book draft refusals and close preserve records then explicit publication hands off exact palette',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native book draft refusals close and exact palette handoff preserve labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,status=await electronSession.waitForBackend();const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned book HTTP ${r.status}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);
});

import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page,Request} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type FolderResult={kind:'select';directory:string}|{kind:'cancel'}|{kind:'error'};
type FolderControl={set:(result:FolderResult)=>Promise<void>;calls:()=>Promise<any[]>};
const endpoint='/api/dataset/formats/import';
const errorText='Controlled label folder dialog failure';
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const isImport=(request:Request)=>request.method()==='POST'&&new URL(request.url()).pathname===endpoint;
test.use({actionTimeout:10_000});
function tree(root:string):Record<string,string>{
 const result:Record<string,string>={};
 const walk=(dir:string)=>{for(const name of fs.readdirSync(dir).sort()){
  const file=path.join(dir,name),stat=fs.lstatSync(file);expect(stat.isSymbolicLink()).toBe(false);
  if(stat.isDirectory())walk(file);else{expect(stat.isFile()).toBe(true);result[path.relative(root,file)]=sha(file);}
 }};
 if(fs.existsSync(root))walk(root);return result;
}

// These generated LabelMe proposals remain unsent for application. Dialog
// responses are controlled; preview and exact original-image handoff use the
// actual source renderer/backend. Closing a completed preview abandons UI scope,
// not an in-flight request, an annotation change, or a human review decision.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,folder:FolderControl,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Owned label exchange lifecycle',task:'segmentation'});
 const active=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 await api('/api/team-data');await api('/api/team-data/readiness');
 const metadata=await api('/api/dataset/metadata?limit=10');expect(metadata.items).toHaveLength(2);
 const images=workspace.images.map(file=>metadata.items.find((row:any)=>row.file_path===file.path));
 expect(images.every(Boolean)).toBe(true);
 const annotation=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
 const beforeLabels=await Promise.all(workspace.images.map(file=>api(annotation(file.path))));
 expect(beforeLabels.every(row=>row.annotations.length===0)).toBe(true);
 const annotationRoot=active.annotations_dir||project.annotations_dir;expect(typeof annotationRoot).toBe('string');
 const beforeTree=tree(annotationRoot),beforeVersions=await api('/api/dataset/versions');
 const inputs=images.map((image:any,index:number)=>{
  const directory=path.join(workspace.root,`unsent-labelme-${index}`),file=path.join(directory,'proposal.json');fs.mkdirSync(directory);
  fs.writeFileSync(file,JSON.stringify({version:'5.0.0',flags:{},imagePath:image.relative_path,imageData:null,imageWidth:32,imageHeight:32,
   shapes:[{label:'ControlledProposal',points:[[3,4],[18,20]],shape_type:'rectangle',flags:{},group_id:null}]}));
  evidence.addFile(file);return{directory,file,sha256:sha(file),image};
 });
 const navigate=async()=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();};
 await navigate();
 const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});
 const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});
 const open=async()=>{
  if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
  await expect(panel).toContainText('검색 결과 2개');
  const summary=panel.getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true});
  if(await summary.locator('..').getAttribute('open')===null)await summary.click();
 };
 await open();await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('controlled-label-lifecycle');
 const previewButton=panel.getByRole('button',{name:'가져오기 미리보기',exact:true}),apply=panel.getByRole('button',{name:'검토한 라벨 적용',exact:true});
 const observed:any[]=[],writes:any[]=[],receipts:any[]=[],states:any[]=[],folderCalls:any[]=[];
 const observe=(request:Request)=>{
  if(isImport(request))observed.push(request.postDataJSON());
  const route=new URL(request.url()).pathname;
  if(request.method()!=='GET'&&(/\/api\/annotations\//.test(route)||/\/api\/dataset\/metadata\//.test(route)))writes.push({method:request.method(),route,body:request.postData()});
 };
 page.on('request',observe);
 const count=async()=>({imports:observed.length,writes:writes.length,dialogs:(await folder.calls()).length});
 const setFolder=async(result:FolderResult)=>{
  const before=await count();await folder.set(result);await panel.getByRole('button',{name:'라벨 폴더 선택',exact:true}).click();
  await expect.poll(async()=>(await folder.calls()).length).toBe(before.dialogs+1);
  await expect(panel.getByRole('button',{name:'라벨 폴더 선택',exact:true})).toBeEnabled();
  folderCalls.push((await folder.calls()).at(-1));
  expect(observed.length).toBe(before.imports);expect(writes.length).toBe(before.writes);
 };
 const selected=(directory:string)=>panel.locator('span').filter({hasText:directory});
 const previewLine=(image:any)=>panel.getByText(`${image.relative_path} · 기존 0 / 가져올 1`,{exact:true});
 const capture=async(input:typeof inputs[number])=>{
  const waiting=page.waitForResponse(response=>isImport(response.request())&&response.request().postDataJSON().import_dir===input.directory);
  await previewButton.click();const response=await waiting;expect(response.status(),await response.text()).toBe(200);
  const request=response.request().postDataJSON();expect(request).toEqual({format:'labelme',import_dir:input.directory,mode:'preview',conflict_policy:'reject',actor:'controlled-label-lifecycle',expected_revisions:{}});
  const body=await response.json();await response.finished();expect(body.applied).toBe(false);expect(body.format).toBe('labelme');expect(body.preview).toHaveLength(1);
  expect(body.preview[0]).toEqual(expect.objectContaining({file_name:input.image.relative_path,image_uuid:input.image.image_uuid,revision:input.image.revision,existing_count:0,incoming_count:1,conflict:false}));
  expect(body.preview[0].annotations).toEqual([{type:'bbox',label:'ControlledProposal',category_id:1,bbox:[3,4,18,20]}]);
  receipts.push({request,status:response.status(),response:body});await expect(previewLine(input.image)).toBeVisible();await expect(apply).toBeEnabled();return body;
 };
 const remember=async(name:string)=>states.push({name,selected_folder:await panel.locator('span[title]').evaluateAll(elements=>elements.map(element=>({title:element.getAttribute('title'),text:element.textContent}))),preview:await panel.locator('ul').allTextContents(),imports:observed.length,writes:writes.length});
 try{
  await expect(previewButton).toBeDisabled();await expect(apply).toBeDisabled();
  // No folder selected: the actual UI invokes the controlled cancel response.
  const initialCalls=(await folder.calls()).length;await folder.set({kind:'cancel'});await panel.getByRole('button',{name:'라벨 폴더 선택',exact:true}).click();
  await expect(panel.getByText('폴더를 선택하세요',{exact:true})).toBeVisible();await expect(previewButton).toBeDisabled();await expect(apply).toBeDisabled();
  await expect.poll(async()=>(await folder.calls()).length).toBe(initialCalls+1);folderCalls.push((await folder.calls()).at(-1));expect(observed).toEqual([]);await remember('initial-folder-cancel');
  await setFolder({kind:'select',directory:inputs[0].directory});await expect(selected(inputs[0].directory)).toHaveAttribute('title',inputs[0].directory);await capture(inputs[0]);
  await setFolder({kind:'cancel'});await expect(selected(inputs[0].directory)).toHaveAttribute('title',inputs[0].directory);await expect(previewLine(images[0])).toBeVisible();await expect(apply).toBeEnabled();await remember('folder-cancel-preserves-unsent-preview');
  await setFolder({kind:'error'});const alert=panel.getByRole('alert');await expect(alert).toContainText(errorText);await alert.scrollIntoViewIfNeeded();await expect(alert).toBeInViewport();
  await expect(selected(inputs[0].directory)).toHaveAttribute('title',inputs[0].directory);await expect(previewLine(images[0])).toBeVisible();await expect(apply).toBeEnabled();await evidence.screenshot(page,`${native?'native':'browser'}-label-folder-error-unsent-preview`);await remember('folder-error-preserves-unsent-preview');
  await setFolder({kind:'select',directory:inputs[1].directory});await expect(selected(inputs[1].directory)).toHaveAttribute('title',inputs[1].directory);
  await expect(previewLine(images[0])).toHaveCount(0);await expect(apply).toBeDisabled();await expect(panel.getByRole('alert')).toHaveCount(0);await remember('replacement-folder-clears-old-preview');await capture(inputs[1]);
  const closeCount=observed.length;await toggle.click();await expect(panel).toHaveCount(0);expect(observed.length).toBe(closeCount);expect(writes).toEqual([]);
  await open();await expect(selected(inputs[1].directory)).toHaveAttribute('title',inputs[1].directory);await expect(previewLine(images[1])).toBeVisible();await expect(apply).toBeEnabled();expect(observed.length).toBe(closeCount);await remember('closed-completed-unsent-preview-reopened');
  await apply.scrollIntoViewIfNeeded();await expect(apply).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-label-unsent-preview-reopened-no-apply`);
  await navigate();await open();await expect(panel.getByText('폴더를 선택하세요',{exact:true})).toBeVisible();await expect(previewButton).toBeDisabled();await expect(apply).toBeDisabled();await expect(previewLine(images[1])).toHaveCount(0);expect(observed.length).toBe(closeCount);await remember('actual-reload-resets-folder-preview');
  await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('controlled-label-lifecycle');
  await setFolder({kind:'select',directory:inputs[1].directory});await capture(inputs[1]);
  const image=images[1],rawPath='/api/dataset/raw/'+encodeURIComponent(path.basename(image.file_path));
  const rawWaiting=page.waitForResponse(response=>{const target=new URL(response.url());return target.pathname===rawPath&&target.searchParams.get('file_path')===image.file_path;});
  const annotationWaiting=page.waitForResponse(response=>{const target=new URL(response.url());return target.pathname==='/api/annotations/'+path.basename(image.file_path,'.png')&&target.searchParams.get('file_path')===image.file_path&&response.request().method()==='GET';});
  const row=panel.getByRole('row').filter({hasText:image.relative_path});await expect(row.locator('td[title]')).toHaveAttribute('title',image.file_path);
  await row.getByRole('button',{name:'2단계 검토',exact:true}).click();
  const raw=await rawWaiting,labels=await annotationWaiting;expect(raw.status()).toBe(200);expect(labels.status()).toBe(200);
  const rawBytes=await raw.body(),rawSha=createHash('sha256').update(rawBytes).digest('hex');expect(rawSha).toBe(image.content_hash);expect(rawSha).toBe(workspace.images[1].sha256);
  const handoffLabels=await labels.json();await raw.finished();await labels.finished();expect(handoffLabels.annotations).toEqual(beforeLabels[1].annotations);expect(handoffLabels.mask_file).toBe(beforeLabels[1].mask_file);
  await expect(page.locator('[data-labeling-work-area]')).toBeVisible();
  const selectedImage=page.locator('[data-labeling-filmstrip]').getByRole('img',{name:path.basename(image.file_path),exact:true});
  await expect(selectedImage.locator('..')).toHaveClass(/border-blue-500/);
  expect(new URL((await selectedImage.getAttribute('src'))!,page.url()).searchParams.get('file_path')).toBe(image.file_path);
  await expect(page.getByTestId('annotation-save-button')).toHaveText('Saved');
  await evidence.screenshot(page,`${native?'native':'browser'}-label-exact-original-review-handoff-no-apply`);
  expect(observed).toEqual(receipts.map(receipt=>receipt.request));expect(observed).toHaveLength(3);expect(observed.every(request=>request.mode==='preview')).toBe(true);expect(writes).toEqual([]);
  const afterLabels=await Promise.all(workspace.images.map(file=>api(annotation(file.path))));for(let index=0;index<afterLabels.length;index++){expect(afterLabels[index].annotations).toEqual(beforeLabels[index].annotations);expect(afterLabels[index].mask_file).toBe(beforeLabels[index].mask_file);}
  const afterMetadata=(await api('/api/dataset/metadata?limit=10')).items;expect(afterMetadata).toEqual(metadata.items);expect(tree(annotationRoot)).toEqual(beforeTree);expect(await api('/api/dataset/versions')).toEqual(beforeVersions);
  for(const file of workspace.images)expect(sha(file.path)).toBe(file.sha256);for(const input of inputs)expect(sha(input.file)).toBe(input.sha256);
  evidence.note('label_exchange_lifecycle',{record_id:'F117',actions:{'format-import-folder':['cancel','error','reopen'],'format-import-preview':['cancel','reopen','handoff'],'format-import-reviewed-apply':['cancel']},project,source_images:workspace.images,inputs,receipts,states,folder_dialog_calls:folderCalls,metadata_before:metadata.items,metadata_after:afterMetadata,annotations_before:beforeLabels,annotations_after:afterLabels,annotation_tree_before:beforeTree,annotation_tree_after:tree(annotationRoot),versions_before:beforeVersions,versions_after:await api('/api/dataset/versions'),annotation_or_metadata_writes:writes,
   handoff:{file_path:image.file_path,image_uuid:image.image_uuid,revision:image.revision,source_sha256:image.content_hash,actual_raw_response_sha256:rawSha,raw_url:raw.url(),annotation_url:labels.url(),annotations:handoffLabels.annotations,stage:2},
   scope_cancel:{completed_unsent_preview_closed:true,inflight_transport_cancel_verified:false,zero_apply_POST:true},actual_reload_resets_selected_folder_and_preview:true,replacement_folder_clears_previous_preview:true,source_ui:true,source_electron:native,native_folder_dialog_response_controlled:native,browser_folder_bridge_controlled:!native,human_OS_picker_verified:false,generated_proposals_are_human_truth:false,label_or_review_write:false,actual_model_inference:false,quality_accepted:false,installed_target_verified:false,gpu_used:false,windows_excluded:true});
 }finally{page.off('request',observe);}
}

test('label exchange folder and unsent preview lifecycle preserve exact original handoff',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);
 const folder:FolderControl={set:result=>page.evaluate(({result,errorText})=>{const target=window as any;target.__labelFolderCalls||=[];target.api.selectFolder=async(options:any)=>{target.__labelFolderCalls.push({result,options});if(result.kind==='error')throw Error(errorText);return result.kind==='select'?result.directory:null;};},{result,errorText}),calls:()=>page.evaluate(()=>(window as any).__labelFolderCalls||[])};
 await exercise(page,workspace,evidence,api,folder,false,renderer.url);
});
test('native label exchange folder and unsent preview lifecycle preserve original handoff',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned label lifecycle HTTP ${response.status}: ${await response.text()}`);return response.json();},{port:backend.port,route,body,method});
 const folder:FolderControl={set:result=>electronSession.app.evaluate(({dialog},{result,errorText})=>{const target=globalThis as any;target.__labelFolderCalls||=[];dialog.showOpenDialog=async(...args:any[])=>{target.__labelFolderCalls.push({result,options:args[args.length-1]});if(result.kind==='error')throw Error(errorText);return{canceled:result.kind==='cancel',filePaths:result.kind==='select'?[result.directory]:[]};};},{result,errorText}),calls:()=>electronSession.app.evaluate(()=>(globalThis as any).__labelFolderCalls||[])};
 await exercise(page,workspace,evidence,api,folder,true);
});

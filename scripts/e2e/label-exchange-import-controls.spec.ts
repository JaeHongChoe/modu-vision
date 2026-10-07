import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const endpoint='/api/dataset/formats/import';
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

// Generated LabelMe proposals are never applied. The sole metadata edit below
// models a concurrent operator; it changes one lot/revision, not labels or truth.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,chooseFolder:(dir:string)=>Promise<void>,native:boolean,url?:string){
 const project=await api('/api/project/create',{name:'Owned label exchange controls',task:'segmentation'});
 const active=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
 // Opening the app also initializes team defaults in the shared metadata ledger.
 // Complete that existing lazy registration before taking the byte baseline.
 await api('/api/team-data');await api('/api/team-data/readiness');
 const metadata=await api('/api/dataset/metadata?limit=10');expect(metadata.items).toHaveLength(2);
 const image=metadata.items.find((row:any)=>row.file_path===workspace.images[0].path);expect(image).toBeTruthy();
 const annotation=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
 const beforeLabels=await Promise.all(workspace.images.map(file=>api(annotation(file.path))));
 expect(beforeLabels.every(row=>row.annotations.length===0)).toBe(true);
 const annotationRoot=active.annotations_dir||project.annotations_dir;
 expect(typeof annotationRoot).toBe('string');const beforeTree=tree(annotationRoot),beforeVersions=await api('/api/dataset/versions');
 const invalidDir=path.join(workspace.root,'invalid-labelme'),validDir=path.join(workspace.root,'valid-labelme');
 const document=(imagePath:string)=>({version:'5.0.0',flags:{},imagePath,imageData:null,imageWidth:32,imageHeight:32,
  shapes:[{label:'ControlledProposal',points:[[3,4],[18,20]],shape_type:'rectangle',flags:{},group_id:null}]});
 for(const [dir,name] of [[invalidDir,'../outside.png'],[validDir,image.relative_path]]){
  fs.mkdirSync(dir);const file=path.join(dir,'proposal.json');fs.writeFileSync(file,JSON.stringify(document(name)));evidence.addFile(file);
 }
 const inputs=[invalidDir,validDir].map(dir=>({directory:dir,file:path.join(dir,'proposal.json'),sha256:sha(path.join(dir,'proposal.json'))}));
 if(url)await page.goto(url);else await page.reload();
 await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();
 const toggle=page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});
 if(await toggle.getAttribute('aria-expanded')==='false')await toggle.click();
 const panel=page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});await expect(panel).toContainText('검색 결과 2개');
 await panel.getByLabel('데이터 작업자 이름',{exact:true}).fill('controlled-label-exchange');
 const summary=panel.getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true});
 if(await summary.locator('..').getAttribute('open')===null)await summary.click();
 const previewButton=panel.getByRole('button',{name:'가져오기 미리보기',exact:true}),apply=panel.getByRole('button',{name:'검토한 라벨 적용',exact:true});
 const observed:any[]=[],receipts:any[]=[];
 const observe=(request:Request)=>{if(isImport(request))observed.push(request.postDataJSON());};page.on('request',observe);
 let controlledMode:'preview'|'apply'='preview';
 const transport=async(route:Route)=>{
  const request=route.request();
  if(isImport(request)&&request.postDataJSON().mode===controlledMode&&request.postDataJSON().import_dir===validDir){
   await route.fulfill({status:503,json:{detail:`Controlled label ${controlledMode} POST transport failure`}});
  }else await route.continue();
 };
 const reply=(mode:'preview'|'apply',dir:string)=>page.waitForResponse(r=>isImport(r.request())&&r.request().postDataJSON().mode===mode&&r.request().postDataJSON().import_dir===dir);
 const choose=async(dir:string)=>{await chooseFolder(dir);await panel.getByRole('button',{name:'라벨 폴더 선택',exact:true}).click();await expect(panel.locator('span').filter({hasText:dir})).toHaveAttribute('title',dir);};
 const pinnedRequest=(mode:string,dir:string,revisions:Record<string,number>)=>({format:'labelme',import_dir:dir,mode,conflict_policy:'reject',actor:'controlled-label-exchange',expected_revisions:revisions});
 const capture=async(mode:'preview'|'apply',dir:string,button:typeof apply,status:number,revisions:Record<string,number>)=>{
  const waiting=reply(mode,dir);await button.click();const response=await waiting;
  expect(response.status(),await response.text()).toBe(status);expect(response.request().postDataJSON()).toEqual(pinnedRequest(mode,dir,revisions));
  const body=await response.json();await response.finished();receipts.push({request:response.request().postDataJSON(),status,response:body});return body;
 };
 const screenshotAlert=async(name:string,text:string)=>{const alert=panel.getByRole('alert');await expect(alert).toContainText(text);await alert.scrollIntoViewIfNeeded();await expect(alert).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-${name}`);};
 try{
  await expect(previewButton).toBeDisabled();await expect(apply).toBeDisabled();expect(observed).toEqual([]);
  await previewButton.scrollIntoViewIfNeeded();await expect(previewButton).toBeInViewport();await expect(apply).toBeInViewport();
  await evidence.screenshot(page,`${native?'native':'browser'}-label-preview-empty-folder-apply-empty-preview`);
  expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(metadata.items);
  await choose(invalidDir);await expect(apply).toBeDisabled();
  const invalid=await capture('preview',invalidDir,previewButton,422,{});expect(invalid).toEqual({detail:'Unsafe image path'});
  await screenshotAlert('label-preview-real-traversal-422','Unsafe image path');expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(metadata.items);
  await choose(validDir);controlledMode='preview';await page.route('**/api/dataset/formats/import',transport);
  const previewError=await capture('preview',validDir,previewButton,503,{});expect(previewError).toEqual({detail:'Controlled label preview POST transport failure'});
  await screenshotAlert('label-preview-valid-post-controlled-503','Controlled label preview POST transport failure');
  await expect(apply).toBeDisabled();expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(metadata.items);
  await page.unroute('**/api/dataset/formats/import',transport);
  const preview=await capture('preview',validDir,previewButton,200,{});expect(preview.applied).toBe(false);expect(preview.format).toBe('labelme');expect(preview.preview).toHaveLength(1);
  expect(preview.preview[0]).toEqual(expect.objectContaining({file_name:image.relative_path,image_uuid:image.image_uuid,revision:image.revision,existing_count:0,incoming_count:1,conflict:false}));
  expect(preview.preview[0].annotations).toEqual([{type:'bbox',label:'ControlledProposal',category_id:1,bbox:[3,4,18,20]}]);
  await expect(panel.getByRole('alert')).toHaveCount(0);await expect(apply).toBeEnabled();
  expect(tree(annotationRoot)).toEqual(beforeTree);
  const concurrent=await api('/api/dataset/metadata/'+image.image_uuid,{expected_revision:image.revision,actor:'controlled-concurrent-metadata-writer',changes:{lot:'controlled-stale-preview-lot'}},'PATCH');
  expect(concurrent.revision).toBe(image.revision+1);expect(concurrent.lot).toBe('controlled-stale-preview-lot');expect(concurrent.annotation_hash).toBe(image.annotation_hash);expect(concurrent.mask_hash).toBe(image.mask_hash);
  const afterConcurrentTree=tree(annotationRoot);expect(Object.keys(afterConcurrentTree)).toEqual(Object.keys(beforeTree));
  const controlledLedgerChanges=Object.keys(beforeTree).filter(name=>beforeTree[name]!==afterConcurrentTree[name]);
  expect(controlledLedgerChanges).toHaveLength(1);expect(controlledLedgerChanges[0]).toMatch(/^by_dataset\/[0-9a-f]{16}\/metadata\/workflow\.json$/);
  const stalePins={[image.image_uuid]:image.revision};const stale=await capture('apply',validDir,apply,409,stalePins);
  expect(stale).toEqual({detail:'Preview is outdated or missing: '+image.relative_path});
  await screenshotAlert('label-apply-real-stale-preview-409','Preview is outdated or missing');
  expect(await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(image.file_path))).toEqual(concurrent);
  const refreshed=await capture('preview',validDir,previewButton,200,stalePins);expect(refreshed.preview[0].revision).toBe(concurrent.revision);expect(refreshed.applied).toBe(false);
  await expect(panel.getByRole('alert')).toHaveCount(0);controlledMode='apply';await page.route('**/api/dataset/formats/import',transport);
  const applyError=await capture('apply',validDir,apply,503,{[image.image_uuid]:concurrent.revision});expect(applyError).toEqual({detail:'Controlled label apply POST transport failure'});
  await screenshotAlert('label-apply-valid-pins-controlled-503','Controlled label apply POST transport failure');
  expect(observed).toEqual(receipts.map(row=>row.request));expect(observed).toHaveLength(6);
  const afterLabels=await Promise.all(workspace.images.map(file=>api(annotation(file.path))));
  for(let i=0;i<afterLabels.length;i++){expect(afterLabels[i].annotations).toEqual(beforeLabels[i].annotations);expect(afterLabels[i].mask_file).toBe(beforeLabels[i].mask_file);}
  const afterMetadata=(await api('/api/dataset/metadata?limit=10')).items;
  expect(afterMetadata).toEqual(metadata.items.map((row:any)=>row.image_uuid===image.image_uuid?concurrent:row));
  expect(tree(annotationRoot)).toEqual(afterConcurrentTree);expect(await api('/api/dataset/versions')).toEqual(beforeVersions);
  for(const file of workspace.images)expect(sha(file.path)).toBe(file.sha256);for(const input of inputs)expect(sha(input.file)).toBe(input.sha256);
  evidence.note('label_exchange_import_controls',{record_id:'F117',actions:{'format-import-preview':['empty','invalid','error'],'format-import-reviewed-apply':['empty','invalid','error']},project,source_images:workspace.images,inputs,metadata_before:metadata.items,controlled_concurrent_metadata_edit:concurrent,metadata_after:afterMetadata,receipts,annotation_tree_before:beforeTree,annotation_tree_after_controlled_metadata_edit:afterConcurrentTree,controlled_ledger_changes:controlledLedgerChanges,annotation_tree_after:tree(annotationRoot),versions_before:beforeVersions,versions_after:await api('/api/dataset/versions'),annotations_before:beforeLabels,annotations_after:afterLabels,
   empty:{no_selected_folder_preview_disabled:true,no_preview_apply_disabled:true,no_import_POST:true},preview_invalid:{actual_422_unsafe_path:true},preview_error:{exact_valid_POST_controlled_503:true,original_POST_not_dispatched:true,explicit_real_200_retry:true},apply_invalid:{actual_409_stale_expected_revision:true,one_fixture_metadata_revision_separate_from_labels:true},apply_error:{exact_valid_POST_controlled_503:true,original_POST_not_dispatched:true},
   source_ui:true,source_electron:native,native_folder_dialog_response_controlled:native,browser_folder_bridge_controlled:!native,human_OS_picker_verified:false,generated_proposals_are_human_truth:false,label_or_review_write:false,actual_model_inference:false,quality_accepted:false,installed_target_verified:false,gpu_used:false,windows_excluded:true});
 }finally{page.off('request',observe);if(!page.isClosed())await page.unroute('**/api/dataset/formats/import',transport);}
}

test('label exchange preview and apply empty invalid exact POST errors preserve annotations',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await installDesktopHostShim(page,renderer.port);const folder=(dir:string)=>page.evaluate(value=>{(window as any).api.selectFolder=async()=>value;},dir);
 await exercise(page,workspace,evidence,api,folder,false,renderer.url);
});
test('native label exchange preview and apply empty invalid POST errors preserve annotations',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned label exchange HTTP ${response.status}: ${await response.text()}`);return response.json();},{port:backend.port,route,body,method});
 const folder=(dir:string)=>electronSession.app.evaluate(({dialog},value)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[value]});},dir);
 await exercise(page,workspace,evidence,api,folder,true);
});

import fs from 'node:fs';
import path from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page,Request,Route} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';

type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Downloads={run:(action:()=>Promise<void>,name:string)=>Promise<string>;count:()=>Promise<number>};
const endpoint='/api/dataset/formats/export';
const sha=(file:string)=>createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const isExport=(request:Request)=>request.method()==='POST'&&new URL(request.url()).pathname===endpoint;
test.use({actionTimeout:10_000});
function tree(root:string):Record<string,string>{
 const result:Record<string,string>={};
 const walk=(dir:string)=>{for(const name of fs.readdirSync(dir).sort()){
  const file=path.join(dir,name),stat=fs.lstatSync(file);expect(stat.isSymbolicLink()).toBe(false);
  if(stat.isDirectory())walk(file);else{expect(stat.isFile()).toBe(true);result[path.relative(root,file)]=sha(file);}
 }};
 if(fs.existsSync(root))walk(root);return result;
}
function readBundle(file:string):Record<string,{sha256:string;bytes:number;text:string}>{
 // Read only this generated small archive, never extract it or follow members.
 const program=`import hashlib,json,pathlib,stat,sys,zipfile
p=pathlib.Path(sys.argv[1]);assert p.stat().st_size<=1048576
with zipfile.ZipFile(p) as z:
 rows=z.infolist();assert len(rows)<=20;assert sum(r.file_size for r in rows)<=4194304
 assert len({r.filename for r in rows})==len(rows)
 result={}
 for r in rows:
  name=pathlib.PurePosixPath(r.filename)
  assert not name.is_absolute() and all(v not in ('','.', '..') for v in name.parts) and '\\\\' not in r.filename
  assert not stat.S_ISLNK(r.external_attr>>16) and not r.is_dir() and r.file_size<=1048576
  raw=z.read(r);assert len(raw)==r.file_size
  result[r.filename]={'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'text':raw.decode('utf-8')}
 print(json.dumps(result,sort_keys=True))`;
 return JSON.parse(execFileSync(process.env.MV_E2E_PYTHON||'python3',['-c',program,file],{encoding:'utf8',timeout:10_000}));
}

// Export uses generated labels and original fixture PNGs. Empty LabelMe is an
// actual zero-member ZIP, not proof of a useful dataset. Panel dismissal is
// completed-view reopen only: the current UI has no export Cancel control.
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,downloads:Downloads,native:boolean,url?:string){
 const observed:any[]=[],writes:any[]=[],receipts:any[]=[],states:any[]=[];
 let recording=false;
 const observe=(request:Request)=>{
  if(!recording)return;
  if(isExport(request))observed.push({method:request.method(),path:endpoint,body:request.postDataJSON()});
  const pathname=new URL(request.url()).pathname;
  if(request.method()!=='GET'&&!isExport(request)&&/^\/api\/(annotations|dataset|team-data|training|export|flow-evaluations|runtime-services|product-delivery)(\/|$)/.test(pathname))
   writes.push({method:request.method(),path:pathname,body:request.postData()});
 };
 page.on('request',observe);
 const panel=()=>page.getByRole('region',{name:'데이터 검토와 라벨 교환',exact:true});
 const toggle=()=>page.getByRole('button',{name:'이미지 검토·그룹 분할·라벨 교환',exact:true});
 const navigate=async(name:string)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();};
 const open=async(total:number)=>{
  if(await toggle().getAttribute('aria-expanded')==='false')await toggle().click();
  await expect(panel()).toContainText(`검색 결과 ${total}개`);
  const summary=panel().getByText('LabelMe · COCO · YOLO 라벨 가져오기/내보내기',{exact:true});
  if(await summary.locator('..').getAttribute('open')===null)await summary.click();
  await expect(panel().getByRole('button',{name:'라벨 내보내기',exact:true})).toBeEnabled();
 };
 const capture=async(name:string)=>{
  const status=panel().getByRole('status').filter({hasText:/내보내기 완료/});
  const target=await status.count()?status:panel().getByRole('alert');
  await target.scrollIntoViewIfNeeded();await expect(target).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-label-export-${name}`);
 };
 const exportBundle=async(format:string,name:string)=>{
  await panel().getByLabel('라벨 교환 형식',{exact:true}).selectOption(format);
  const waiting=page.waitForResponse(response=>isExport(response.request()));
  const downloaded=await downloads.run(()=>panel().getByRole('button',{name:'라벨 내보내기',exact:true}).click(),name);
  const response=await waiting;expect(response.status(),await response.text()).toBe(200);expect(response.request().postDataJSON()).toEqual({format});
  const body=await response.json();await response.finished();expect(body.format).toBe(format);
  const active=await api('/api/project/current');
  const sourceZip=path.join(active.project_dir,'exports',path.basename(body.download_url)+'.zip');
  expect(sha(downloaded)).toBe(sha(sourceZip));const members=readBundle(downloaded);
  const receipt={format,request:{format},status:response.status(),response:body,downloaded_zip:downloaded,downloaded_sha256:sha(downloaded),source_zip:sourceZip,source_sha256:sha(sourceZip),members};
  evidence.addFile(downloaded);evidence.addFile(sourceZip);receipts.push(receipt);return receipt;
 };
 try{
  const emptySource=path.join(workspace.root,'empty-export-source');fs.mkdirSync(emptySource);
  const emptyProject=await api('/api/project/create',{name:'Owned zero-image export',task:'segmentation'});
  const emptyActive=await api('/api/project/update',{source_dataset_dir:emptySource},'PUT');
  await api('/api/dataset/import',{folder_path:emptySource,task:'segmentation'});
  // These GETs initialize the default team workspace before byte baselines,
  // matching the panel's ordinary startup reads even when there are no images.
  await api('/api/team-data');await api('/api/team-data/readiness');
  const emptyMetadata=await api('/api/dataset/metadata?limit=10'),emptyVersions=await api('/api/dataset/versions');expect(emptyMetadata.items).toEqual([]);
  const emptyAnnotationRoot=emptyActive.annotations_dir||emptyProject.annotations_dir,emptyAnnotationTree=tree(emptyAnnotationRoot),emptyProjectHash=sha(path.join(emptyProject.project_dir,'project.json'));
  recording=true;await navigate(emptyProject.name);await open(0);
  const empty=await exportBundle('labelme','empty-labelme');
  expect(empty.response.image_count).toBe(0);expect(empty.response.annotation_count).toBe(0);
  expect(empty.response.payload).toEqual({format:'labelme',documents:[]});expect(empty.members).toEqual({});
  await expect(panel().getByRole('status').filter({hasText:'0개 이미지 · 0개 라벨 내보내기 완료'})).toBeVisible();await capture('zero-image-zero-member-zip');
  expect(tree(emptySource)).toEqual({});expect(tree(emptyAnnotationRoot)).toEqual(emptyAnnotationTree);
  expect(await api('/api/dataset/versions')).toEqual(emptyVersions);expect((await api('/api/dataset/metadata?limit=10')).items).toEqual(emptyMetadata.items);
  expect(sha(path.join(emptyProject.project_dir,'project.json'))).toBe(emptyProjectHash);
  const emptyProof={project:emptyProject,source:emptySource,metadata:emptyMetadata.items,versions:emptyVersions,annotation_tree:emptyAnnotationTree,
   project_sha256:emptyProjectHash,zero_image_export_allowed:true,archive_member_count:0,manifest_document_count:0,source_unchanged:true,useful_dataset_or_quality_accepted:false};

  // Deliberate fixture setup writes are outside the UI-control observation.
  recording=false;
  const project=await api('/api/project/create',{name:'Owned export lifecycle',task:'segmentation'});
  const active=await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  await api('/api/dataset/import',{folder_path:workspace.dataset,task:'segmentation'});
  await api('/api/team-data');await api('/api/team-data/readiness');
  const query=(file:string)=>'/api/annotations/'+path.basename(file,'.png')+'?file_path='+encodeURIComponent(file);
  const seeded:any[]=[];
  for(let index=0;index<workspace.images.length;index++){
   const file=workspace.images[index],current=await api(query(file.path));
   const annotations=[{id:`controlled-export-box-${index}`,type:'bbox',label:'ControlledBox',category_id:7,color:'#f59e0b',bbox:[3,4,18,20]}];
   const saved=await api('/api/annotations/save',{image_id:path.basename(file.path,'.png'),image_path:file.path,image_width:32,image_height:32,
    annotations,actor:'controlled-export-fixture',expected_revision:current.metadata.revision});seeded.push(saved);
  }
  const metadata=(await api('/api/dataset/metadata?limit=10')).items;expect(metadata).toHaveLength(2);
  const annotationRoot=active.annotations_dir||project.annotations_dir,baselineTree=tree(annotationRoot),baselineVersions=await api('/api/dataset/versions');
  const baselineLabels=await Promise.all(workspace.images.map(file=>api(query(file.path))));
  const projectHash=sha(path.join(project.project_dir,'project.json')),sourceTree=tree(workspace.dataset),exportsRoot=path.join(project.project_dir,'exports');
  expect(fs.existsSync(exportsRoot)).toBe(false);
  recording=true;await navigate(project.name);await open(2);
  await panel().getByLabel('라벨 교환 형식',{exact:true}).selectOption('yolo');
  const beforeErrorDownloads=await downloads.count();let intercepted:any,errors=0;
  const failure=async(route:Route)=>{
   expect(isExport(route.request())).toBe(true);intercepted=route.request().postDataJSON();expect(intercepted).toEqual({format:'yolo'});errors++;
   await route.fulfill({status:503,json:{detail:'Controlled label export transport failure'}});
  };
  await page.route('**'+endpoint,failure);
  try{
   const waiting=page.waitForResponse(response=>isExport(response.request()));
   await panel().getByRole('button',{name:'라벨 내보내기',exact:true}).click();expect((await waiting).status()).toBe(503);
   await expect(panel().getByRole('alert')).toContainText('Controlled label export transport failure');
   await expect(panel().getByRole('button',{name:'라벨 내보내기',exact:true})).toBeEnabled();
   await expect(panel().getByLabel('라벨 교환 형식',{exact:true})).toHaveValue('yolo');await capture('controlled-post-503-no-download');
  }finally{await page.unroute('**'+endpoint,failure);}
  expect(errors).toBe(1);expect(await downloads.count()).toBe(beforeErrorDownloads);expect(fs.existsSync(exportsRoot)).toBe(false);
  expect(tree(annotationRoot)).toEqual(baselineTree);expect(tree(workspace.dataset)).toEqual(sourceTree);
  const errorProof={path:endpoint,body:intercepted,status:503,backend_dispatch:false,export_artifact_created:false,extra_download:0,selected_format:'yolo',button_reenabled:true,
   download_transport_failure_verified:false};

  const exported=await exportBundle('yolo','completed-yolo');
  expect(exported.response.image_count).toBe(2);expect(exported.response.annotation_count).toBe(2);
  const names=workspace.images.map(file=>path.relative(workspace.dataset,file.path).split(path.sep).join('/'));
  const labelNames=names.map(name=>name.replace(/\.png$/,'.txt'));
  expect(Object.keys(exported.members).sort()).toEqual([...labelNames,'classes.txt','image_manifest.json','studio_classes.json'].sort());
  expect(exported.members['classes.txt'].text).toBe('ControlledBox');
  expect(JSON.parse(exported.members['image_manifest.json'].text).sort((a:any,b:any)=>a.file_name.localeCompare(b.file_name))).toEqual([...names].sort().map(name=>({file_name:name,width:32,height:32})));
  expect(JSON.parse(exported.members['studio_classes.json'].text)).toEqual([{label:'ControlledBox',category_id:7,color:'#f59e0b'}]);
  for(const name of labelNames){const line=exported.members[name].text.trim().split(/\s+/).map(Number);expect(line).toEqual([0,0.328125,0.375,0.46875,0.5]);}
  await expect(panel().getByRole('status').filter({hasText:'2개 이미지 · 2개 라벨 내보내기 완료'})).toBeVisible();await capture('completed-real-yolo-bundle');
  const beforeReopen={POSTs:observed.length,downloads:await downloads.count(),zip_sha256:exported.downloaded_sha256};
  await toggle().click();await expect(panel()).toHaveCount(0);await open(2);
  await expect(panel().getByLabel('라벨 교환 형식',{exact:true})).toHaveValue('yolo');
  await expect(panel().getByRole('status').filter({hasText:'2개 이미지 · 2개 라벨 내보내기 완료'})).toBeVisible();
  expect(observed.length).toBe(beforeReopen.POSTs);expect(await downloads.count()).toBe(beforeReopen.downloads);await capture('completed-panel-reopen-no-repeat-export');
  states.push({name:'completed-view-close-open',format:'yolo',notice_preserved:true,extra_export_POST:0,extra_download:0,zip_sha256:sha(exported.downloaded_zip)});
  await navigate(project.name);await open(2);await expect(panel().getByLabel('라벨 교환 형식',{exact:true})).toHaveValue('labelme');
  await expect(panel().getByRole('status').filter({hasText:/내보내기 완료/})).toHaveCount(0);
  expect(observed.length).toBe(beforeReopen.POSTs);expect(await downloads.count()).toBe(beforeReopen.downloads);
  const exportButton=panel().getByRole('button',{name:'라벨 내보내기',exact:true});await exportButton.scrollIntoViewIfNeeded();await expect(exportButton).toBeInViewport();await evidence.screenshot(page,`${native?'native':'browser'}-label-export-reload-default-no-automatic-export`);
  states.push({name:'actual-renderer-reload',format:'labelme',transient_notice_cleared:true,persisted_zip_sha256:sha(exported.source_zip),extra_export_POST:0,extra_download:0});
  expect(sha(exported.downloaded_zip)).toBe(beforeReopen.zip_sha256);expect(sha(exported.source_zip)).toBe(beforeReopen.zip_sha256);
  const afterLabels=await Promise.all(workspace.images.map(file=>api(query(file.path))));expect(afterLabels).toEqual(baselineLabels);
  const afterMetadata=(await api('/api/dataset/metadata?limit=10')).items;expect(afterMetadata).toEqual(metadata);
  expect(tree(annotationRoot)).toEqual(baselineTree);expect(await api('/api/dataset/versions')).toEqual(baselineVersions);
  expect(tree(workspace.dataset)).toEqual(sourceTree);expect(sha(path.join(project.project_dir,'project.json'))).toBe(projectHash);
  expect(observed.map(row=>row.body)).toEqual([{format:'labelme'},{format:'yolo'},{format:'yolo'}]);expect(writes).toEqual([]);expect(await downloads.count()).toBe(2);
  for(const file of workspace.images){expect(sha(file.path)).toBe(file.sha256);evidence.addFile(file.path);}
  for(const name of Object.keys(baselineTree))evidence.addFile(path.join(annotationRoot,name));evidence.addFile(path.join(project.project_dir,'project.json'));evidence.addFile(path.join(emptyProject.project_dir,'project.json'));
  evidence.note('label_export_lifecycle',{record_id:'F117',action:'format-export-bundle',verified_dimensions:['empty','error','reopen'],pending_dimensions:{cancel:'No export-specific Cancel control or cancellable download acknowledgement exists in DatasetWorkflowPanel; common panel dismissal is completed-view reopen only.'},
   empty:emptyProof,error:errorProof,reopen:states,project,source_images:workspace.images,source_tree_before:sourceTree,source_tree_after:tree(workspace.dataset),project_sha256:projectHash,
   fixture_setup:{explicit_synthetic_annotation_save_count:seeded.length,actor:'controlled-export-fixture',responses:seeded,setup_writes_excluded_from_control_counts:true},
   annotations_before:baselineLabels,annotations_after:afterLabels,annotation_tree_before:baselineTree,annotation_tree_after:tree(annotationRoot),metadata_before:metadata,metadata_after:afterMetadata,
   versions_before:baselineVersions,versions_after:await api('/api/dataset/versions'),export_receipts:receipts,observed_export_requests:observed,annotation_metadata_or_model_control_writes:writes,completed_downloads:await downloads.count(),
   actual_generated_zip_members_verified:true,original_images_hash_bound_separately:true,source_images_are_in_export_zip:false,actual_source_ui:true,source_electron:native,
   completed_export_view_dismissal_is_request_cancel:false,inflight_export_or_download_cancel_verified:false,human_label_truth:false,model_inference:false,gpu_used:false,
   quality_accepted:false,frozen_packaged_native_acceptance:false,windows_qualified:false,whole_process_tree_verified:false});
 }finally{page.off('request',observe);}
}

test('label export zero-image bundle, transport error and completed view reopen preserve generated source',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);
 const api:Api=async(route,body,method)=>{const reply=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(reply.ok(),await reply.text()).toBe(true);return reply.json();};
 let count=0;const observe=()=>{count++;};page.on('download',observe);
 const downloads:Downloads={count:async()=>count,run:async(action,name)=>{const waiting=page.waitForEvent('download');await action();const item=await waiting,file=path.join(workspace.logs,name+'.zip');await item.saveAs(file);expect(await item.failure()).toBeNull();return file;}};
 try{await exercise(page,workspace,evidence,api,downloads,false,renderer.url);}finally{page.off('download',observe);}
});
test('native label export empty bundle, error and completed view reopen preserve generated labels',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const reply=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!reply.ok)throw Error(`Owned export fixture HTTP ${reply.status}: ${await reply.text()}`);return reply.json();},{port:backend.port,route,body,method});
 await electronSession.app.evaluate(({session})=>{(globalThis as any).__exportDownloads=0;session.defaultSession.on('will-download',()=>{(globalThis as any).__exportDownloads++;});});
 const downloads:Downloads={count:()=>electronSession.app.evaluate(()=>(globalThis as any).__exportDownloads),run:async(action,name)=>{
  const file=path.join(workspace.logs,name+'.zip');await electronSession.app.evaluate(({session},target)=>{(globalThis as any).__exportDownloadState=null;session.defaultSession.once('will-download',(_event,item)=>{item.setSavePath(target);item.once('done',(_done,state)=>{(globalThis as any).__exportDownloadState=state;});});},file);
  await action();await expect.poll(()=>electronSession.app.evaluate(()=>(globalThis as any).__exportDownloadState)).toBe('completed');return file;
 }};
 await exercise(page,workspace,evidence,api,downloads,true);
});

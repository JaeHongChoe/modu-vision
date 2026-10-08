import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import type {Page} from '@playwright/test';
import {test, expect, type Workspace, type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
test.use({actionTimeout:10_000});
type Api = (route:string, body?:unknown, method?:string) => Promise<any>;
const sha = (raw:Buffer) => crypto.createHash('sha256').update(raw).digest('hex');

// Include every regular file. Linked/unsupported entries are errors, never a
// reason to exclude a database journal, annotation or newly written artifact.
function tree(root:string):Record<string,string> {
  const result:Record<string,string> = {};
  const walk = (folder:string) => {
    const st=fs.lstatSync(folder);expect(st.isDirectory()).toBe(true);expect(st.isSymbolicLink()).toBe(false);
    for(const name of fs.readdirSync(folder).sort()){
      const file=path.join(folder,name),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);
      if(info.isDirectory())walk(file);
      else {expect(info.isFile()).toBe(true);result[path.relative(root,file).split(path.sep).join('/')]=sha(fs.readFileSync(file));}
    }
  };
  walk(root);return result;
}

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
  const name='Owned invalid metadata filter lifecycle',prefix=native?'source-electron':'browser';
  const project=await api('/api/project/create',{name,task:'classification'});
  await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
  const started=await api('/api/dataset/imports',{task:'classification',verify:true});let imported:any;
  const until=Date.now()+10_000;
  while(Date.now()<until){
    imported=await api(`/api/dataset/imports/${started.job_id}`);
    if(['completed','failed','aborted','interrupted'].includes(imported.state))break;
    await new Promise(resolve=>setTimeout(resolve,50));
  }
  expect(imported.state).toBe('completed');
  await api(`/api/dataset/imports/${started.job_id}/accept`,{revision_id:imported.result.revision.revision_id,expected_active:null});
  const listing=await api('/api/dataset/library/images?limit=120');expect(listing.items.length).toBeGreaterThan(1);
  const first=listing.items[0],metadata=await api('/api/dataset/metadata/image?image_path='+encodeURIComponent(first.file_path));
  await api('/api/dataset/metadata/'+metadata.image_uuid,{expected_revision:metadata.revision,actor:'invalid-filter-fixture',changes:{tags:['owned-valid-filter'],product:'owned-product',lot:'owned-lot'}},'PATCH');
  const preference=()=>page.evaluate(()=>Object.entries(localStorage).filter(([key])=>key.startsWith('modu.inspectionImage.v2:')).map(([key,raw])=>({key,raw})));
  if(url)await page.goto(url);else await page.reload();
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);
  await page.getByRole('button',{name:/05.*플로우차트/}).click();
  const open=()=>page.getByRole('button',{name:'이미지 변경...',exact:true}).click();
  const picker=page.getByRole('dialog',{name:'검사 대상 이미지 선택'}),grid=picker.getByRole('list',{name:'데이터 버전 이미지'});
  await open();await expect(grid.getByRole('listitem').first()).toBeVisible();
  await grid.getByRole('listitem').filter({hasText:first.file_name}).first().click();
  await picker.getByRole('button',{name:'선택 확정',exact:true}).click();await expect(picker).toBeHidden();
  const saved=await preference();expect(saved).toHaveLength(1);
  const chosen=JSON.parse(saved[0].raw);expect(chosen.imageUuid).toBe(first.image_uuid);expect(chosen.sha256).toBe(first.sha256);
  await open();await expect(picker).toContainText('선택: '+first.relative_path);await expect(grid.getByRole('listitem').first()).toBeVisible();
  await expect(picker.getByRole('button',{name:'선택 확정',exact:true})).toBeEnabled();

  const current=await api('/api/project/current'),roots={source:workspace.dataset,project:current.project_dir};
  const snapshot=async()=>({records:{current:await api('/api/project/current'),metadata:await api('/api/dataset/metadata?limit=100'),library:await api('/api/dataset/library/images?limit=120'),labelsets:await api('/api/project/labelsets'),annotation:await api('/api/annotations/'+encodeURIComponent(path.parse(first.file_name).name)+'?file_path='+encodeURIComponent(first.file_path))},trees:Object.fromEntries(Object.entries(roots).map(([key,folder])=>[key,tree(folder)]))});
  const before=await snapshot(),baseline=path.join(workspace.root,'logs/library-invalid-filter-protected-before');
  expect(fs.existsSync(baseline)).toBe(false);fs.mkdirSync(baseline,{recursive:true});
  for(const [key,folder] of Object.entries(roots)){fs.cpSync(folder,path.join(baseline,key),{recursive:true,errorOnExist:true});expect(tree(path.join(baseline,key))).toEqual(before.trees[key]);for(const relative of Object.keys(before.trees[key]))evidence.addFile(path.join(baseline,key,relative));}
  const beforeFile=path.join(workspace.root,'logs/library-invalid-filter-protected-before.json');fs.writeFileSync(beforeFile,JSON.stringify({roots,...before}),{flag:'wx'});evidence.addFile(beforeFile);
  const writes:{method:string;endpoint:string}[]=[];page.on('request',request=>{const u=new URL(request.url());if(u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method()))writes.push({method:request.method(),endpoint:u.pathname});});
  const errors:any[]=[],overlong='x'.repeat(257);
  for(const [key,label] of [['q','이미지 검색'],['tag','태그'],['product','제품'],['lot','Lot']] as const){
    const field=picker.getByLabel(label,{exact:true});
    const response=page.waitForResponse(response=>{const u=new URL(response.url());return u.pathname==='/api/dataset/library/images'&&u.searchParams.get(key)===overlong;});
    await field.fill(overlong);await expect(field).toHaveValue(overlong);
    const reply=await response,body=await reply.json();expect(reply.status()).toBe(422);
    expect(body.detail.some((row:any)=>JSON.stringify(row.loc)===JSON.stringify(['query',key])&&row.type==='string_too_long'&&row.ctx.max_length===256)).toBe(true);
    await expect(grid.getByRole('listitem')).toHaveCount(0);await expect(picker.getByRole('alert')).toBeVisible();
    await expect(picker).toContainText('선택: '+first.relative_path);expect(await preference()).toEqual(saved);
    errors.push({key,label,input:overlong,status:reply.status(),body,url:reply.url(),visible_alert:await picker.getByRole('alert').innerText()});
    await evidence.screenshot(page,`${prefix}-library-invalid-${key}-actual422`);
    await field.fill('');await expect(grid.getByRole('listitem').first()).toBeVisible();await expect(picker.getByRole('alert')).toBeHidden();
  }
  // An explicit valid filter recovers the original identity, without selecting
  // an invalid response or starting a training/inspection operation.
  await picker.getByLabel('태그',{exact:true}).fill('owned-valid-filter');await expect(grid.getByRole('listitem')).toHaveCount(1);
  await expect(grid).toContainText(first.file_name);await expect(grid.getByRole('listitem')).toHaveAttribute('aria-pressed','true');
  await picker.getByRole('button',{name:'선택 확정',exact:true}).click();await expect(picker).toBeHidden();
  await expect(page.getByRole('region',{name:'플로우 식별 정보'})).toContainText(first.file_path);
  expect(await preference()).toEqual(saved);expect(writes).toEqual([]);
  const after=await snapshot();expect(after).toEqual(before);
  for(const [key,folder] of Object.entries(roots))for(const relative of Object.keys(before.trees[key]))expect(fs.readFileSync(path.join(folder,relative))).toEqual(fs.readFileSync(path.join(baseline,key,relative)));
  for(const image of workspace.images)expect(sha(fs.readFileSync(image.path))).toBe(image.sha256);
  const afterFile=path.join(workspace.root,'logs/library-invalid-filter-protected-after.json');fs.writeFileSync(afterFile,JSON.stringify({roots,...after}),{flag:'wx'});evidence.addFile(afterFile);
  await evidence.screenshot(page,`${prefix}-library-invalid-filter-explicit-recovery`);
  evidence.note('library_invalid_filter_lifecycle',{requirements:['S7-01'],cells:[{action:'U030.library-metadata-filter',dimension:'invalid'}],sourceElectron:native,project,current,first,chosen,saved,roots,baseline,before,after,errors,writes,
    actual_original_backend_422:true,all_complete_protected_trees_unfiltered:true,confirmed_identity_and_preference_bytes_preserved:true,explicit_valid_filter_recovery:true,
    model_training_inference_or_deployment:false,native_installed_release_acceptance:false,human_annotation_or_model_quality_approval:false});
}

test('metadata filters reject genuine overlong queries and preserve the exact confirmed original',async({page,request,renderer,workspace,evidence})=>{
  await installDesktopHostShim(page,renderer.port);
  const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),`Owned API ${route} HTTP${response.status()}`).toBe(true);return response.json();};
  await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native metadata filters reject genuine overlong queries and preserve the exact confirmed original',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
  const backend=await electronSession.waitForBackend(),window=electronSession.window;
  const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned API ${route} HTTP${response.status}`);return response.json();},{port:backend.port,route,body,method});
  await exercise(window,workspace,evidence,api,true);
});

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
type Folder=(folder:string)=>Promise<void>;
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
const cases=[
 {name:'Folder cohort',files:['train/OK/a.png','train/OK/b.png','train/NG/c.png','val/OK/d.png','val/NG/e.png','test/NG/f.png'],
  ignored:['train/OK/.hidden.png','train/@eaDir/thumb.png','val/OK/.hidden.png','val/@eaDir/thumb.png','test/OK/.hidden.png','test/@eaDir/thumb.png'],classes:{OK:3,NG:3},split:{train:3,val:2,test:1}},
 {name:'Small automatic cohort',files:['OK/a.png','OK/b.png','NG/c.png','NG/d.png'],ignored:['OK/.hidden.png','@eaDir/thumb.png'],classes:{OK:2,NG:2},split:{train:2,val:2,test:0}},
 {name:'Singleton automatic cohort',files:['OK/a.png','NG/b.png'],ignored:['OK/.hidden.png','@eaDir/thumb.png'],classes:{OK:1,NG:1},split:{train:2,val:0,test:0}},
 {name:'Validation only cohort',files:['val/OK/a.png','test/NG/b.png'],ignored:['val/OK/.hidden.png','test/@eaDir/thumb.png'],classes:{OK:1,NG:1},split:{train:0,val:1,test:1}},
];

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,folder:Folder,native:boolean,url?:string){
 const writes:string[]=[];page.on('request',r=>{if(r.method()==='POST'&&/\/(training\/start|train|compute\/jobs|dataset\/split)$/.test(new URL(r.url()).pathname))writes.push(r.url());});
 const receipts:any[]=[];
 const navigate=async(name:string)=>{if(url)await page.goto(url);else await page.reload();await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(name);await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(0).click();};
 const assertCards=async(entry:typeof cases[number])=>{
  await expect(page.getByText('학습 가능 이미지',{exact:true}).locator('..').getByText(String(entry.files.length),{exact:true})).toBeVisible();
  await expect(page.getByText('적용된 학습 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.train),{exact:true})).toBeVisible();
  await expect(page.getByText('적용된 검증 / 테스트 분할',{exact:true}).locator('..').locator('..').getByText(String(entry.split.val+entry.split.test),{exact:true})).toBeVisible();
  for(const [key,value]of Object.entries(entry.split))await expect(page.getByText(key==='train'?'Train:':key==='val'?'Val:':'Test:',{exact:true}).locator('..').getByText(String(value),{exact:true})).toBeVisible();
 };
 for(const [index,entry]of cases.entries()){
  const source=path.join(workspace.root,`classification-${index}`),originals:Record<string,string>={};
  for(const [i,relative]of [...entry.files,...entry.ignored].entries()){const file=path.join(source,relative);fs.mkdirSync(path.dirname(file),{recursive:true});const bytes=png(32,3,(x,y)=>[(x+i*7)%256,(y+i*9)%256,50+i]);fs.writeFileSync(file,bytes);originals[relative]=sha(bytes);}
  const project=await api('/api/project/create',{name:entry.name,task:'classification'});await navigate(entry.name);await folder(source);
  const reply=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/dataset/import'&&r.request().method()==='POST'&&r.request().postDataJSON().folder_path===source);
  await page.getByRole('button',{name:'데이터셋 폴더 열기',exact:true}).click();const response=await reply;expect(response.status()).toBe(200);const imported=await response.json();
  expect(imported.total_images).toBe(entry.files.length);expect(imported.source_images).toBe(entry.files.length);expect(imported.classes).toEqual(entry.classes);expect(imported.split).toEqual(entry.split);await assertCards(entry);
  const current=await api('/api/project/current');expect(current.id).toBe(project.id);expect(current.source_dataset_dir).toBe(source);
  const cohort=JSON.parse(execFileSync(process.env.MV_E2E_PYTHON||'python3',['scripts/e2e/fixtures/classification_cohort.py',source],{encoding:'utf8'}));
  expect(cohort.files).toEqual(originals);const actual=Object.values(cohort.partitions).flat() as Array<{path:string;label:string}>;
  expect(actual.map(r=>r.path).sort()).toEqual([...entry.files].sort());for(const [partition,count]of Object.entries(entry.split))expect(cohort.partitions[partition]).toHaveLength(count);
  expect(new Set(actual.map(r=>r.path)).size).toBe(entry.files.length);
  await navigate(entry.name);await assertCards(entry);await evidence.screenshot(page,`${native?'native':'browser'}-classification-${index}-reopened`);
  for(const [relative,want]of Object.entries(originals)){const file=path.join(source,relative);expect(sha(fs.readFileSync(file))).toBe(want);evidence.addFile(file);}
  receipts.push({name:entry.name,project_id:project.id,project_dir:project.project_dir,source,expected:{total:entry.files.length,classes:entry.classes,split:entry.split,eligible:entry.files,ignored:entry.ignored},imported,cohort,originals,reopened:true});
 }
 await api('/api/project/open',{project_dir:receipts[0].project_dir});await navigate(cases[0].name);await assertCards(cases[0]);expect((await api('/api/project/current')).source_dataset_dir).toBe(receipts[0].source);
 expect(writes).toEqual([]);evidence.note('classification_summary',{receipts,original_project_reopened:true,prohibited_writes:writes,folder_dialog_answer_controlled:true,actual_renderer_and_backend:true,default_loader_cohort_only:true,training_and_model_quality_not_assessed:true});
}

test('classification import summary matches eligible default loader partitions and reopens',async({page,request,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 const folder:Folder=(value)=>page.evaluate(source=>{(window as any).api.selectFolder=async()=>source;},value);await exercise(page,workspace,evidence,api,folder,false,renderer.url);
});
test('native classification import summary matches eligible default loader partitions and reopens',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window,app}=electronSession,backend=await electronSession.waitForBackend();const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned cohort API HTTP ${response.status}`);return response.json();},{port:backend.port,route,body,method});
 const folder:Folder=value=>app.evaluate(({dialog},source)=>{dialog.showOpenDialog=async()=>({canceled:false,filePaths:[source]});},value);await exercise(window,workspace,evidence,api,folder,true);
});

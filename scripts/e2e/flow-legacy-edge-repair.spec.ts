import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';

const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
const sha=(bytes:Buffer|string)=>crypto.createHash('sha256').update(bytes).digest('hex');
const stable=(v:any):any=>Array.isArray(v)?v.map(stable):v&&typeof v==='object'?Object.fromEntries(Object.keys(v).sort().map(k=>[k,stable(v[k])])):v;
const graphSha=(graph:any)=>sha(JSON.stringify(stable(graph)));

async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,url?:string){
 await api('/api/project/create',{name:'Owned legacy edge repair',task:'anomaly'});
 await api('/api/project/update',{source_dataset_dir:workspace.dataset},'PUT');
 const project=await api('/api/project/current');
 const graph={id:'legacy-edge-fixture',name:'Legacy wire repair',nodes:[
  {id:'input',position:{x:32,y:220},data:{label:'Input',node_type:'input'}},
  {id:'model_a',position:{x:392,y:32},data:{label:'Model A',node_type:'inspection',task:'anomaly',threshold:.5}},
  {id:'model_b',position:{x:392,y:430},data:{label:'Model B',node_type:'inspection',task:'anomaly',threshold:.5}},
  {id:'decision',position:{x:752,y:220},data:{label:'Decision',node_type:'decision',rule:'any_defect_is_ng'}},
  {id:'output',position:{x:1112,y:220},data:{label:'Output',node_type:'output'}}],edges:[
  {id:'input-a',source:'input',target:'model_a',payload_type:'image',label:'Missing original'},
  {id:'input-b',source:'input',target:'model_b',payload_type:'image'},
  {id:'first',source:'model_a',target:'decision',payload_type:'result',label:'First preserved'},
  {id:'second',source:'model_b',target:'decision',payload_type:'result',label:'Second selected'},
  {id:'output-edge',source:'decision',target:'output',payload_type:'result'}]};
 const context={project_id:project.id,source_dataset_path:workspace.dataset,labelset_id:project.active_labelset_id||'default'};
 await api('/api/flowchart/draft',{pipeline:graph,context,base_version_id:'none'},'PUT');
 // Reconstruct an older, hash-consistent private draft. Production save still
 // rejects duplicate IDs; this fixture does not weaken that admission policy.
 const prepared=JSON.parse(execFileSync(harness.resolvePython(),['-c',[
  'import json,sys,hashlib',
  'from pathlib import Path',
  'from backend.engine.flow_provenance import pipeline_sha256',
  'root=Path(sys.argv[1]).resolve(); project=Path(sys.argv[2]).resolve()',
  'assert project.is_relative_to(root)',
  'files=list((project/"flowcharts"/"drafts").rglob("draft.json")); assert len(files)==1',
  'file=files[0]; record=json.loads(file.read_bytes()); graph=record["pipeline"]',
  'graph["edges"][0]["id"]=""; graph["edges"][2]["id"]="duplicate"; graph["edges"][3]["id"]="duplicate"',
  'record["draft_sha256"]=pipeline_sha256(graph); file.write_text(json.dumps(record,ensure_ascii=False,indent=2)+"\\n")',
  'print(json.dumps({"path":str(file),"raw_sha256":hashlib.sha256(file.read_bytes()).hexdigest(),"record":record}))',
 ].join('\n'),workspace.root,project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
 const initial=await api('/api/flowchart/draft');expect(initial.draft_sha256).toBe(graphSha(initial.pipeline));
 expect(initial.pipeline.edges.map((e:any)=>e.id)).toEqual(['','input-b','duplicate','duplicate','output-edge']);
 const images=workspace.images.map(row=>({...row,current_sha256:sha(fs.readFileSync(row.path))}));
 if(url)await page.goto(url);else await page.reload();
 await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();
 await page.getByRole('tab',{name:'편집',exact:true}).click();
 await expect(page.getByRole('heading',{name:'검사 플로우 편집기',exact:true})).toBeVisible();
 await expect(page.locator('path[data-flow-edge]')).toHaveCount(5);
 const wire=(source:string,target:string)=>page.getByRole('button',{name:new RegExp(`^Select connection ${source} to ${target}(?: when |:|$)`)});
 const select=async(source:string,target:string)=>{await wire(source,target).focus();await wire(source,target).press('Enter');};
 const name=()=>page.getByText('연결선 이름',{exact:true}).locator('..').getByRole('textbox');
 await select('model_b','decision');
 await expect(name()).toHaveValue('Second selected');
 expect(sha(fs.readFileSync(prepared.path)),'opening/selecting does not normalize or save legacy IDs').toBe(prepared.raw_sha256);
 expect((await api('/api/flowchart/draft')).draft_sha256).toBe(initial.draft_sha256);
 await name().fill('Second edited only');
 await select('model_a','decision');await expect(name()).toHaveValue('First preserved');
 await select('model_b','decision');await expect(name()).toHaveValue('Second edited only');
 await page.getByRole('combobox',{name:/^클래스 조건/}).selectOption('present');
 await page.getByRole('combobox',{name:'분기 클래스 이름',exact:true}).fill('controlled-class');
 await select('model_a','decision');await expect(page.getByRole('combobox',{name:/^클래스 조건/})).toHaveValue('');
 await select('model_b','decision');await expect(page.getByRole('combobox',{name:'분기 클래스 이름',exact:true})).toHaveValue('controlled-class');
 await page.getByText('다음 노드 실행 조건',{exact:true}).locator('..').getByRole('combobox').selectOption('fail');
 await expect(page.getByRole('combobox',{name:/^클래스 조건/})).toHaveValue('');
 await select('model_a','decision');await expect(page.getByText('다음 노드 실행 조건',{exact:true}).locator('..').getByRole('combobox')).toHaveValue('default');
 await page.getByRole('button',{name:'연결선 삭제',exact:true}).click();
 await expect(page.locator('path[data-flow-edge]')).toHaveCount(4);
 await expect(wire('model_b','decision')).toBeVisible();await expect(wire('model_a','decision')).toHaveCount(0);
 await page.getByRole('button',{name:'플로우 실행 취소',exact:true}).click();
 await expect(page.locator('path[data-flow-edge]')).toHaveCount(5);
 await select('model_a','decision');await expect(name()).toHaveValue('First preserved');
 await page.getByRole('button',{name:'플로우 다시 실행',exact:true}).click();await expect(page.locator('path[data-flow-edge]')).toHaveCount(4);
 await select('input','model_a');await expect(name()).toHaveValue('Missing original');
 await name().fill('Missing edited explicitly');
 await select('model_b','decision');await expect(name()).toHaveValue('Second edited only');
 const saved=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/flowchart/draft'&&r.request().method()==='PUT');
 await page.getByRole('button',{name:'초안 저장',exact:true}).click();expect((await saved).status()).toBe(200);
 await expect(page.getByRole('status').filter({hasText:'✓ 편집 초안이 저장되었습니다.'})).toBeVisible();
 const final=await api('/api/flowchart/draft');expect(final.draft_sha256).toBe(graphSha(final.pipeline));
 expect(final.pipeline.edges.map((e:any)=>e.id)).toEqual(['','input-b','duplicate','output-edge']);
 expect(final.pipeline.edges.find((e:any)=>e.source==='model_b'&&e.target==='decision')).toMatchObject({label:'Second edited only',isBranch:'fail',predicate:null,id:'duplicate'});
 expect(final.pipeline.edges[0]).toMatchObject({id:'',label:'Missing edited explicitly'});
 expect(final.pipeline.nodes).toEqual(initial.pipeline.nodes);
 await evidence.screenshot(page,'one-legacy-row-edited-and-deleted-without-normalization');
 await page.reload();await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(4).click();await page.getByRole('tab',{name:'편집',exact:true}).click();
 await expect(page.locator('path[data-flow-edge]')).toHaveCount(4);await select('input','model_a');await expect(name()).toHaveValue('Missing edited explicitly');
 expect((await api('/api/flowchart/draft')).draft_sha256).toBe(final.draft_sha256);
 await expect(page.getByRole('button',{name:'플로우 저장',exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'연결선 삭제',exact:true}).click();await expect(wire('input','model_a')).toHaveCount(0);
 await page.getByRole('button',{name:'플로우 실행 취소',exact:true}).click();await select('input','model_a');await expect(name()).toHaveValue('Missing edited explicitly');
 for(const row of images)expect(sha(fs.readFileSync(row.path))).toBe(row.sha256);
 expect(fs.existsSync(path.join(project.project_dir,'flowcharts','active.json'))).toBe(false);
 await evidence.screenshot(page,'explicit-draft-reopened-missing-id-remains-runtime-invalid');
 evidence.note('legacy_edge_repair',{prepared,initial,final,images,exact_row_label_predicate_branch_delete:true,missing_id_explicit_edit_delete:true,undo_redo:true,saved_graph_sha256:final.draft_sha256,reopened_exact:true,auto_normalized_ids:false,runtime_invalid_missing_id_retained:true,no_active_version:true,training:false,inference:false,quality_approved:false});
 evidence.addFile(prepared.path);
}

test('legacy missing and duplicate wire IDs allow exact row repair without rewriting saved identity',async({page,request,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const response=await request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};
 await exercise(page,workspace,evidence,api,renderer.url);
});
test('native legacy wire repair preserves exact saved draft and runtime identity refusal',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession;const backend=await electronSession.waitForBackend();
 const api:Api=(route,body,method)=>window.evaluate(async({port,route,body,method})=>{const response=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body===undefined?'GET':'POST'),...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});if(!response.ok)throw Error(`Owned API ${response.status}: ${await response.text()}`);return response.json();},{port:backend.port,route,body,method});
 await exercise(window,workspace,evidence,api);
});

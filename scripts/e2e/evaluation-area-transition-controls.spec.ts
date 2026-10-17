import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {execFileSync} from 'node:child_process';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
const harness=require('./fixtures/harness.cjs');
const AREA_FIXTURE_SCRIPT="import hashlib,json,sys\nfrom pathlib import Path\nfrom backend.engine.evaluation_history import EvaluationHistory\ndef seed(root, project, source, tag):\n    root, project, source = (Path(value).resolve() for value in (root, project, source))\n    if not project.is_relative_to(root) or not source.is_relative_to(root):\n        raise ValueError('Saved report fixture requires an isolated workspace')\n    if tag not in ('A', 'B'):\n        raise ValueError('Owned area fixture requires A or B')\n    rows = []\n    # Explicit known inputs: [truth, prediction, score, object truth, object prediction].\n    cases = [('NG', 'NG', .9, 'Scratch', 'Scratch'), ('NG', 'OK', .3, 'Scratch', None),\n             ('OK', 'NG', .7, None, 'Scratch'), ('OK', 'OK', .1, None, None),\n             ('NG', 'NG', .8, 'Crack', 'Crack'), ('review', 'NG', .6, None, 'Scratch')]\n    for index, (truth, prediction, score, actual, guess) in enumerate(cases):\n        file = source / f'case-{index}.png'\n        if not file.is_file() or file.is_symlink():\n            raise ValueError('Fixture image is missing or linked')\n        matched = actual is not None and actual == guess\n        box = [1, 1, 3 + index, 3 + index] if tag == 'A' else [[1, 1, 3, 4], [1, 1, 4, 4], [1, 1, 4, 5], [1, 1, 5, 5], [1, 1, 5, 7], [1, 1, 6, 7]][index]\n        classes = {label: {'tp': int(matched and actual == label),\n                           'fn': int(actual == label and not matched),\n                           'fp': int(guess == label and not matched)} for label in ('Scratch', 'Crack')}\n        rows.append({'file_path': str(file), 'file_name': file.name,\n                     'image_sha256': hashlib.sha256(file.read_bytes()).hexdigest(),\n                     'ground_truth': truth, 'predicted_class': prediction, 'defect_score': score,\n                     'is_correct': None if truth == 'review' else truth == prediction,\n                     'product': 'part-a' if index < 3 else 'part-b', 'lot': 'lot-' + str(index % 2),\n                     'object_evidence': {'truth': [{'label': actual, 'box': box}] if actual else [],\n                                         'predicted': [{'label': guess, 'box': box, 'confidence': score}] if guess else [],\n                                         'per_class': classes,\n                                         'matches': [{'prediction_index': 0, 'truth_index': 0, 'label': actual, 'iou': 1.0}] if matched else [],\n                                         'missing_truth_indices': [0] if actual and not matched else [],\n                                         'extra_prediction_indices': [0] if guess and not matched else [],\n                                         'source_size': [16, 16], 'coordinate_space': 'original_image_px'}})\n    history = EvaluationHistory(project / 'reports' / 'evaluations')\n    result = {'job_id': 'controlled-filter-display', 'task': 'detection',\n              'fixture_kind': 'controlled_display_only_no_model_inference',\n              'test_predictions': rows, 'metrics': {'fixture_only': True}}\n    binding = {'source_dataset_path': str(source), 'task': 'detection', 'labelset_id': 'default',\n               'threshold_settings': {'probability_threshold': .5}, 'fixture_kind': result['fixture_kind']}\n    items = []\n    for variant in ('full', 'no-binary-truth', 'empty'):\n        value = json.loads(json.dumps(result))\n        value['job_id'] += '-' + variant\n        if variant == 'no-binary-truth':\n            for row in value['test_predictions']:\n                row['ground_truth'] = 'review'\n        elif variant == 'empty':\n            value['test_predictions'] = []\n        record = history.append(value, binding)\n        file = history.directory / (record['evaluation_id'] + '.json')\n        items.append({'variant': variant, 'record': record, 'report_path': str(file),\n                      'report_sha256': hashlib.sha256(file.read_bytes()).hexdigest()})\n    return {'kind': 'controlled_saved_filter_reports_not_model_inference', 'items': items,\n            'inputs': [{'path': row['file_path'], 'sha256': row['image_sha256']} for row in rows]}\nprint(json.dumps(seed(*sys.argv[1:]),ensure_ascii=False))\n";
const sha=(bytes:Buffer)=>crypto.createHash('sha256').update(bytes).digest('hex');
type OwnedApi=(route:string,body?:unknown,method?:string)=>Promise<any>;
test.use({actionTimeout:10_000});

import handoffPath from 'node:path';
import {createHash as handoffCreateHash} from 'node:crypto';
import type {Request as HandoffRequest, Route as HandoffRoute} from '@playwright/test';
import {png as handoffPng} from './qa/appFlow';
type HandoffApi = (route:string,body?:unknown,method?:string)=>Promise<any>;
const handoffHash=(bytes:Buffer)=>handoffCreateHash('sha256').update(bytes).digest('hex');

async function handoffWithin<T>(promise:Promise<T>,deadline:number,label:string):Promise<T>{
 const remaining=deadline-performance.now();if(remaining<=0)throw Error('Original 10s handoff frame expired: '+label);
 let timer:ReturnType<typeof setTimeout>|undefined;
 return Promise.race([promise,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s handoff frame expired: '+label)),remaining);})]).finally(()=>{if(timer)clearTimeout(timer);});
}
async function handoffProject(page:Page,scope:{project:any;source:string}){
 const deadline=performance.now()+10_000;
 await page.getByTitle('프로젝트 관리',{exact:true}).click();const dialog=page.getByRole('dialog',{name:'프로젝트 관리',exact:true});
 await dialog.getByRole('button',{name:'최근 프로젝트',exact:true}).click();
 const wire=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname==='/api/project/open'&&r.request().postDataJSON()?.project_dir===scope.project.project_dir,{timeout:10_000});
 const item=dialog.getByRole('button').filter({has:page.locator('span[title]').filter({hasText:scope.project.project_dir})});
 await expect(item).toHaveCount(1);await expect(item).toBeEnabled();await item.click();
 const response=await handoffWithin(wire,deadline,'project open');expect(response.status()).toBe(200);const bytes=await response.body();
 expect(JSON.parse(bytes.toString('utf8'))).toEqual(scope.project);expect(await response.finished()).toBeNull();
 await expect(dialog).toHaveCount(0);await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name);
 return {project_id:scope.project.id,project_dir:scope.project.project_dir,status:200,response_sha256:handoffHash(bytes)};
}

function rocHandoffRemaining(deadline:number){const left=deadline-performance.now();if(left<=0)throw Error('Original 10s ROC handoff frame expired');return Math.max(1,Math.floor(left));}
async function rocHandoffWithin<T>(work:Promise<T>,deadline:number,label:string):Promise<T>{let timer:NodeJS.Timeout|undefined;try{const value=await Promise.race([work,new Promise<never>((_,reject)=>{timer=setTimeout(()=>reject(Error('Original 10s ROC handoff frame expired: '+label)),rocHandoffRemaining(deadline));})]);expect(performance.now()).toBeLessThanOrEqual(deadline);return value;}finally{clearTimeout(timer);}}
function rocHandoffTree(root:string,retainedFDcustody?:number[]){
 const result:Record<string,{kind:'directory'}|{kind:'file';size:number;sha256:string}>={};const walk=(dir:string)=>{const s=fs.lstatSync(dir);expect(s.isSymbolicLink()).toBe(false);expect(s.isDirectory()).toBe(true);expect(fs.realpathSync(dir)).toBe(dir);result[path.relative(root,dir).split(path.sep).join('/')]={kind:'directory'};
  for(const name of fs.readdirSync(dir).sort()){const file=path.join(dir,name),before=fs.lstatSync(file);expect(before.isSymbolicLink()).toBe(false);if(before.isDirectory())walk(file);else{expect(before.isFile()).toBe(true);expect(before.nlink).toBe(1);const fd=fs.openSync(file,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW),retained=retainedFDcustody!==undefined&&['model_deployments.sqlite3','model_deployments.sqlite3-wal','model_deployments.sqlite3-shm'].includes(path.relative(root,file));if(retained)retainedFDcustody!.push(fd);let raw:Buffer;try{const opened=fs.fstatSync(fd);expect([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]).toEqual([before.dev,before.ino,before.mode,before.nlink,before.size,before.mtimeMs,before.ctimeMs]);raw=fs.readFileSync(fd);for(const after of[fs.fstatSync(fd),fs.lstatSync(file)])expect([after.dev,after.ino,after.mode,after.nlink,after.size,after.mtimeMs,after.ctimeMs]).toEqual([opened.dev,opened.ino,opened.mode,opened.nlink,opened.size,opened.mtimeMs,opened.ctimeMs]);}finally{if(!retained)fs.closeSync(fd);}result[path.relative(root,file).split(path.sep).join('/')]={kind:'file',size:raw!.length,sha256:sha(raw!)};}}
 };walk(root);return result;
}

type F055Scope={tag:'A'|'B';source:string;project:any;fixture:any;full:any};
async function f055SavedAreaPending(page:Page,w:Workspace,e:Evidence,api:OwnedApi,native:boolean,origin:string,url?:string){
 const frame=async<T>(label:string,work:(deadline:number)=>Promise<T>):Promise<T>=>{const deadline=performance.now()+10_000;return rocHandoffWithin(work(deadline),deadline,'F055 '+label);};
 const read=(route:string,body?:unknown,method?:string)=>frame('fixture API full body',()=>api(route,body,method));
 const {DatabaseSync}=await import('node:sqlite');
 const databases:Array<import('node:sqlite').DatabaseSync>=[],retainedFDs:number[]=[],controls:Array<{close:()=>Promise<void>}>=[];
 const writes:Array<{method:string;path:string;body:string|null}>=[];
 const observe=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(u.origin===origin&&u.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(r.method()))writes.push({method:r.method(),path:u.pathname,body:r.postData()});};
 let observing=false,failed=false,proof:any;
 const areaSnapshots:Record<string,unknown>={};
 try{
  const make=async(tag:'A'|'B'):Promise<F055Scope>=>{
   const source=path.join(w.root,'f055-area-transition-'+tag);fs.mkdirSync(source);
   for(let i=0;i<6;i++)fs.writeFileSync(path.join(source,`case-${i}.png`),png(16,3,(x,y)=>[x,y,(tag==='A'?30:130)+i]),{flag:'wx'});
   const created=await read('/api/project/create',{name:'F055 pending controls '+tag,task:'segmentation'});
   await read('/api/project/update',{source_dataset_dir:source},'PUT');await read('/api/dataset/import',{folder_path:source,task:'segmentation'});
   const project=await read('/api/project/current');expect(project.id).toBe(created.id);expect(project.source_dataset_dir).toBe(source);
   const fixture=JSON.parse(execFileSync(harness.resolvePython(),['-c',AREA_FIXTURE_SCRIPT,w.root,project.project_dir,source,tag],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}));
   expect(fixture.kind).toBe('controlled_saved_filter_reports_not_model_inference');expect(fixture.items).toHaveLength(3);
   const full=fixture.items.find((item:any)=>item.variant==='full');expect(full).toBeTruthy();expect(full.record.result.test_predictions).toHaveLength(6);
   expect(full.record.binding.threshold_settings).toEqual({probability_threshold:.5});expect(full.record.binding.source_dataset_path).toBe(source);
   for(const item of fixture.items)expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);
   for(const input of fixture.inputs)expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);
   for(const route of['/api/team-data','/api/team-data/readiness','/api/team-data/queue?offset=0&limit=30','/api/product-delivery/packages','/api/fleet/targets','/api/runtime-services','/api/runtime-services/capture-groups','/api/flow-evaluations/approvals/active','/api/model-deployments/active?source_dataset_path='+encodeURIComponent(source)+'&task=segmentation','/api/dataset/versions','/api/provenance/impact'])await read(route);
   const file=path.join(project.project_dir,'model_deployments.sqlite3'),info=fs.lstatSync(file);expect(info.isSymbolicLink()).toBe(false);expect(info.isFile()).toBe(true);expect(info.nlink).toBe(1);expect(fs.realpathSync(file)).toBe(file);
   const database=new DatabaseSync(file,{readOnly:true});databases.push(database);database.prepare('SELECT revisions.* FROM active_revisions JOIN revisions ON revisions.revision_id=active_revisions.revision_id').all();
   return{tag,source,project,fixture,full};
  };
  const A=await make('A'),B=await make('B');expect(A.project.id).not.toBe(B.project.id);expect(A.full.record.evaluation_id).not.toBe(B.full.record.evaluation_id);
  await read('/api/project/open',{project_dir:A.project.project_dir});await frame('initial renderer',deadline=>url?page.goto(url,{timeout:rocHandoffRemaining(deadline)}):page.reload({timeout:rocHandoffRemaining(deadline)}));
  const trees=(scope:F055Scope)=>({source:rocHandoffTree(scope.source),project:rocHandoffTree(scope.project.project_dir,retainedFDs)}),before={A:trees(A),B:trees(B)};
  const summary=page.locator('summary').filter({hasText:/^평가 이력 · 제품\/Lot별 오류$/}),history=summary.locator('..'),family=history.getByLabel('평가 이력 모델 종류',{exact:true}),selector=history.getByLabel(/^모델별 저장 평가/);
  const bodyExact=(body:any,scope:F055Scope)=>{expect(body.items).toHaveLength(3);expect(body.total).toBe(3);expect(body.items.map((item:any)=>item.evaluation_id).sort()).toEqual(scope.fixture.items.map((item:any)=>item.record.evaluation_id).sort());for(const item of scope.fixture.items)expect(body.items.find((row:any)=>row.evaluation_id===item.record.evaluation_id)).toEqual(item.record);};
  const saved=(scope:F055Scope)=>frame('saved result and binding',async deadline=>{
   const result=history.locator('details').filter({has:page.locator('summary').filter({hasText:'저장된 평가 지표·이미지 결과'})}).first();if(await result.getAttribute('open')===null)await result.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   expect(JSON.parse(await result.locator('pre').innerText({timeout:rocHandoffRemaining(deadline)}))).toEqual(scope.full.record.result);
   const binding=history.locator('details').filter({has:page.locator('summary').filter({hasText:'평가 버전·데이터·모델 해시 확인'})}).first();if(await binding.getAttribute('open')===null)await binding.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});
   const value=JSON.parse(await binding.locator('pre').innerText({timeout:rocHandoffRemaining(deadline)}));expect(value).toEqual({evaluation_id:scope.full.record.evaluation_id,evidence_sha256:scope.full.record.evidence_sha256,binding:scope.full.record.binding});await expect(history.getByText('평가 임계값:',{exact:false})).toContainText('"probability_threshold":0.5',{timeout:rocHandoffRemaining(deadline)});return value;
  });
  const refresh=(scope:F055Scope)=>frame('original owning history refresh',async deadline=>{
   const generation=new Set<import('@playwright/test').Request>(),started=(r:import('@playwright/test').Request)=>{const u=new URL(r.url());if(r.frame()===page.mainFrame()&&r.method()==='GET'&&u.origin===origin&&u.pathname==='/api/evaluation/history'&&u.searchParams.get('source_dataset_path')===scope.source&&u.searchParams.get('task')==='detection'&&!u.searchParams.has('labelset_id'))generation.add(r);};page.on('request',started);
   try{const wire=page.waitForResponse(r=>generation.has(r.request()),{timeout:rocHandoffRemaining(deadline)});await history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)});const response=await rocHandoffWithin(wire,deadline,'F055 original history response');expect(response.status()).toBe(200);const raw=await rocHandoffWithin(response.body(),deadline,'F055 original history full body');expect(await rocHandoffWithin(response.finished(),deadline,'F055 original history completion')).toBeNull();const body=JSON.parse(raw.toString('utf8'));bodyExact(body,scope);await expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});return{status:200,body,sha256:sha(raw),main_frame:true,source:scope.source};}finally{page.off('request',started);}
  });
  const enter=(scope:F055Scope)=>frame('enter current saved ROC',async deadline=>{
   await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(scope.project.name,{timeout:rocHandoffRemaining(deadline)});await page.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(3).click({timeout:rocHandoffRemaining(deadline)});if(await history.getAttribute('open')===null)await summary.click({timeout:rocHandoffRemaining(deadline)});await family.selectOption('detection',{timeout:rocHandoffRemaining(deadline)});await expect(selector.locator('option[value="'+scope.full.record.evaluation_id+'"]')).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});await selector.selectOption(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
   const detail=history.locator('details').filter({has:page.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})}).first();if(await detail.getAttribute('open')===null)await detail.locator('summary').first().click({timeout:rocHandoffRemaining(deadline)});const areaSummary=detail.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'});if(await areaSummary.locator('..').getAttribute('open')===null)await areaSummary.click({timeout:rocHandoffRemaining(deadline)});await expect(detail.getByRole('button',{name:/^결함 면적 /})).toHaveCount(10,{timeout:rocHandoffRemaining(deadline)});return detail;
  });
  const allAreaNames=['case-0.png · FN 0 / FP 0','case-1.png · FN 1 / FP 0','case-2.png · FN 0 / FP 1','case-3.png · FN 0 / FP 0','case-4.png · FN 0 / FP 0','case-5.png · FN 0 / FP 1'];
  const areaNames=(d:ReturnType<Page['locator']>)=>d.getByRole('button',{name:/^case-\d\.png · FN/}).allTextContents();
  const areaStats=(scope:F055Scope,d:ReturnType<Page['locator']>,index:number)=>frame('exact owning area histogram and source URI',async deadline=>{
   await expect(selector).toHaveValue(scope.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByText('객체 면적 · 원본 px²',{exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
   const bins=d.getByRole('button',{name:/^결함 면적 /}),counts=scope.tag==='A'?[1,0,0,1,0,0,0,1,0,1]:[0,0,1,0,1,0,0,0,1,1],step=scope.tag==='A'?4.9:3;
   await expect(bins).toHaveCount(10,{timeout:rocHandoffRemaining(deadline)});
   for(let i=0;i<10;i++)await expect(bins.nth(i)).toHaveAttribute('aria-label',`결함 면적 ${(i*step).toFixed(1)}에서 ${((i+1)*step).toFixed(1)} ${counts[i]}개`,{timeout:rocHandoffRemaining(deadline)});
   const image=d.getByLabel('정답과 예측 객체 위치',{exact:true}).locator('image'),expectedPath=path.join(scope.source,`case-${index}.png`);
   await expect(image).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});const href=await image.getAttribute('href');expect(href).not.toBeNull();const uri=new URL(href!,origin);
   expect(uri.origin).toBe(origin);expect(uri.pathname).toBe(`/api/dataset/raw/case-${index}.png`);expect(uri.searchParams.get('file_path')).toBe(expectedPath);
   return{project_id:scope.project.id,evaluation_id:scope.full.record.evaluation_id,source:scope.source,unit:'original_image_px2',bin_counts:counts,step,bin_labels:await bins.evaluateAll(nodes=>nodes.map(node=>node.getAttribute('aria-label'))),source_filename:`case-${index}.png`,selected_source_path:expectedPath,selected_source_uri:uri.toString()};
  });
  const areaReset=(d:ReturnType<Page['locator']>)=>frame('owning area controls reset after fresh saved read',async deadline=>{
   await expect(d.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByLabel('평가 증거 클래스',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});await expect(d.getByLabel('평가 증거 오류',{exact:true})).toHaveValue('all',{timeout:rocHandoffRemaining(deadline)});
   expect(await areaNames(d)).toEqual(allAreaNames);return{area_filter_active:false,clear_button_count:0,filenames:allAreaNames,class_filter:'all',error_filter:'all'};
  });
  const areaActive=(scope:F055Scope,d:ReturnType<Page['locator']>)=>frame('real independent area-bin click',async deadline=>{
   const index=scope.tag==='A'?3:8,step=scope.tag==='A'?4.9:3,filename=scope.tag==='A'?'case-2.png · FN 0 / FP 1':'case-4.png · FN 0 / FP 0';
   const label=`결함 면적 ${(index*step).toFixed(1)}에서 ${((index+1)*step).toFixed(1)} 1개`;
   await d.getByRole('button',{name:label,exact:true}).click({timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});
   await expect(d.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});expect(await areaNames(d)).toEqual([filename]);
   return{project_id:scope.project.id,evaluation_id:scope.full.record.evaluation_id,bin_index:index,clicked_bin_label:label,bin_range:[index*step,(index+1)*step],area_filter_active:true,clear_button_count:1,filenames:[filename]};
  });
  const areaUnavailable=()=>frame('failed or pending owning read removes real area controls',async deadline=>{
   await expect(history.locator('summary').filter({hasText:'객체·픽셀·문자 오류와 분포 분석'})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   await expect(history.locator('summary').filter({hasText:'결함 크기 분포 · 구간을 눌러 이미지 확인'})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   await expect(history.getByRole('button',{name:/^결함 면적 /})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});
   return{analysis_summary_count:0,area_summary_count:0,area_bin_count:0,clear_button_count:0};
  });
  const areaRetained=(d:ReturnType<Page['locator']>)=>frame('late A cannot replace distinctive B area selection',async deadline=>{
   await expect(d.getByRole('button',{name:'면적 필터 해제',exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});await expect(d.getByRole('button',{name:/^case-\d\.png · FN/})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});expect(await areaNames(d)).toEqual(['case-4.png · FN 0 / FP 0']);
   await expect(d.getByRole('button',{name:'결함 면적 24.0에서 27.0 1개',exact:true})).toHaveCount(1,{timeout:rocHandoffRemaining(deadline)});return{bin_index:8,bin_range:[24,27],area_filter_active:true,clear_button_count:1,filenames:['case-4.png · FN 0 / FP 0']};
  });
  let detail=await enter(A);const initialSaved=await saved(A);const originalRead=await refresh(A);detail=await enter(A);
  areaSnapshots.initial_A_reset=await areaReset(detail);areaSnapshots.initial_A=await areaStats(A,detail,0);areaSnapshots.initial_A_selected=await areaActive(A,detail);areaSnapshots.initial_A_selected_source=await areaStats(A,detail,2);
  page.on('request',observe);observing=true;await e.screenshot(page,`${native?'source-electron':'browser'}-f055-A-area-bin3-before-history-error`);
  const controlledMessage='F055 controlled saved-history outage';
  const installControl=async(scope:F055Scope,kind:'error'|'late')=>{
   const pattern=origin+'/api/evaluation/history**';let armed=true,own:import('@playwright/test').Request|undefined,captured:any,primary:unknown,primarySet=false,fulfillError:unknown,fulfillFailed=false,installed=false,closeAttempted=false,deadline=0,release!:()=>void,ready!:()=>void,done!:()=>void;
   const gate=new Promise<void>(r=>release=r),readyPromise=new Promise<void>(r=>ready=r),donePromise=new Promise<void>(r=>done=r);let dispose!:(row:{kind:'finished'|'failed';failure:string|null})=>void;const disposition=new Promise<{kind:'finished'|'failed';failure:string|null}>(r=>dispose=r);
   const ended=(r:import('@playwright/test').Request)=>{if(r===own)dispose({kind:'finished',failure:null});},aborted=(r:import('@playwright/test').Request)=>{if(r===own)dispose({kind:'failed',failure:r.failure()?.errorText??null});};page.on('requestfinished',ended);page.on('requestfailed',aborted);
   const handler=async(route:import('@playwright/test').Route)=>{
    const request=route.request(),u=new URL(request.url());if(!armed||request.frame()!==page.mainFrame()||request.method()!=='GET'||u.origin!==origin||u.pathname!=='/api/evaluation/history'||u.searchParams.get('source_dataset_path')!==scope.source||u.searchParams.get('task')!=='detection'||u.searchParams.has('labelset_id')||u.searchParams.has('_f055_control_readback')){await route.continue();return;}
    armed=false;own=request;deadline=performance.now()+10_000;
    try{
     let status:number,raw:Buffer,contentType:string|null;
     if(native){
      // Electron uses a separately authenticated renderer GET. It is explicitly
      // a controlled reply source, never original native UI HTTP200 provenance.
      const headers:Record<string,string>={};for(const name of['content-type','x-vision-project','x-vision-context']){const value=await request.headerValue(name);if(value!==null)headers[name]=value;}
      const delegate=new URL(request.url());delegate.searchParams.set('_f055_control_readback',crypto.randomUUID());
      const snapshot=await page.evaluate(async({url,headers,remaining})=>{const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),remaining);try{const response=await fetch(url,{method:'GET',headers,signal:abort.signal});const bytes=new Uint8Array(await response.arrayBuffer());if(bytes.length>1024*1024)throw Error('F055 controlled body exceeds1MiB');let value='';for(const byte of bytes)value+=String.fromCharCode(byte);return{status:response.status,contentType:response.headers.get('content-type'),base64:btoa(value)};}finally{clearTimeout(timer);}},{url:delegate.toString(),headers,remaining:rocHandoffRemaining(deadline)});
      status=snapshot.status;raw=Buffer.from(snapshot.base64,'base64');contentType=snapshot.contentType;
     }else{const snapshot=await route.fetch({timeout:rocHandoffRemaining(deadline)});status=snapshot.status();raw=await rocHandoffWithin(snapshot.body(),deadline,'F055 captured body');contentType=snapshot.headers()['content-type']??null;}
     expect(status).toBe(200);expect(raw.length).toBeLessThanOrEqual(1024*1024);const body=JSON.parse(raw.toString('utf8'));bodyExact(body,scope);captured={body,sha256:sha(raw),size:raw.length,status,source:scope.source,main_frame:true,transport:native?'separate_authenticated_renderer_GET_controlled_original_UI_reply':'original_browser_request_actual_response'};ready();
     if(kind==='late')await rocHandoffWithin(gate,deadline,'F055 release canceled-scope read');
     try{await route.fulfill(kind==='error'?{status:503,contentType:'application/json',body:JSON.stringify({detail:controlledMessage})}:{status:200,body:raw,headers:contentType?{'content-type':contentType}:{}});}catch(error){fulfillError=error;fulfillFailed=true;}
    }catch(error){primary=error;primarySet=true;ready();}finally{done();}
   };
   const readyRead=async()=>{await rocHandoffWithin(readyPromise,performance.now()+10_000,'F055 captured owning request');if(primarySet)throw primary;expect(captured).toBeTruthy();return captured;};
   const finish=async()=>{release();await rocHandoffWithin(donePromise,deadline,'F055 controlled handler completion');if(primarySet)throw primary;const result=await rocHandoffWithin(disposition,deadline,'F055 original request disposition');if(result.kind==='failed'){expect(kind).toBe('late');expect(result.failure).toContain('ERR_ABORTED');}else{if(fulfillFailed)throw fulfillError;const response=await rocHandoffWithin(own!.response(),deadline,'F055 controlled original response');expect(response).not.toBeNull();expect(response!.status()).toBe(kind==='error'?503:200);const raw=await rocHandoffWithin(response!.body(),deadline,'F055 controlled delivered body');expect(await rocHandoffWithin(response!.finished(),deadline,'F055 controlled HTTP completion')).toBeNull();if(kind==='error')expect(JSON.parse(raw.toString('utf8'))).toEqual({detail:controlledMessage});else expect(sha(raw)).toBe(captured.sha256);}
    await page.evaluate(()=>new Promise<void>(r=>requestAnimationFrame(()=>requestAnimationFrame(()=>r()))));return{kind,disposition:result,captured,controlled_503:kind==='error',backend_503_claim:false,original_native_HTTP200_provenance:false,server_cancel_request_sent:false};};
   const close=async()=>{if(closeAttempted)return;closeAttempted=true;release();let first:unknown,set=false;try{if(own)await rocHandoffWithin(donePromise,performance.now()+10_000,'F055 owned route final join');}catch(error){first=error;set=true;}try{if(installed&&!page.isClosed())await page.unroute(pattern,handler);}catch(error){if(!set){first=error;set=true;}}finally{page.off('requestfinished',ended);page.off('requestfailed',aborted);}if(set)throw first;};
   const result={ready:readyRead,finish,close};controls.push(result);await page.route(pattern,handler);installed=true;return result;
  };
  const errorControl=await installControl(A,'error');await frame('actual refresh under controlled outage',deadline=>history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)}));await errorControl.ready();const error=await errorControl.finish();
  await frame('current failed history shows controlled error',async deadline=>{await expect(history.getByRole('alert')).toHaveText(controlledMessage,{timeout:rocHandoffRemaining(deadline)});await expect(selector).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});});areaSnapshots.error_unavailable=await areaUnavailable();await e.screenshot(page,`${native?'source-electron':'browser'}-f055-controlled-history-error-no-stale-area`);await errorControl.close();
  const recovered=await refresh(A);detail=await enter(A);expect(await saved(A)).toEqual(initialSaved);
  areaSnapshots.recovery_reset=await areaReset(detail);areaSnapshots.recovered_A=await areaStats(A,detail,0);expect(areaSnapshots.recovered_A).toEqual(areaSnapshots.initial_A);areaSnapshots.recovery_active_A=await areaActive(A,detail);
  await e.screenshot(page,`${native?'source-electron':'browser'}-f055-A-exact-area-recovery`);
  const late=await installControl(A,'late');await frame('start original read before abandoning A',deadline=>history.getByRole('button',{name:'평가 이력 새로고침',exact:true}).click({timeout:rocHandoffRemaining(deadline)}));const canceledRead=await late.ready();
  await frame('pending current read reports loading',async deadline=>{await expect(history.getByRole('status')).toContainText('평가 기록 확인 중',{timeout:rocHandoffRemaining(deadline)});});areaSnapshots.pending_unavailable=await areaUnavailable();
  const toB=await handoffProject(page,B);detail=await enter(B);const B_saved=await saved(B);
  areaSnapshots.B_reset=await areaReset(detail);areaSnapshots.B_report=await areaStats(B,detail,0);areaSnapshots.B_selected=await areaActive(B,detail);areaSnapshots.B_selected_before=await areaStats(B,detail,4);
  const cancel=await late.finish();await late.close();
  await frame('old A cannot repopulate B saved-area record',async deadline=>{await expect(selector).toHaveValue(B.full.record.evaluation_id,{timeout:rocHandoffRemaining(deadline)});await expect(selector.locator('option[value="'+A.full.record.evaluation_id+'"]')).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});await expect(history.getByRole('alert')).toHaveCount(0,{timeout:rocHandoffRemaining(deadline)});});expect(await saved(B)).toEqual(B_saved);
  areaSnapshots.B_retained=await areaRetained(detail);areaSnapshots.B_selected_after=await areaStats(B,detail,4);expect(areaSnapshots.B_selected_after).toEqual(areaSnapshots.B_selected_before);await e.screenshot(page,`${native?'source-electron':'browser'}-f055-canceled-A-preserves-distinctive-B-area-bin8`);
  const toA=await handoffProject(page,A);detail=await enter(A);expect(await saved(A)).toEqual(initialSaved);const returnRead=await refresh(A);detail=await enter(A);await expect(selector.locator('option[value="'+B.full.record.evaluation_id+'"]')).toHaveCount(0);
  areaSnapshots.return_A_reset=await areaReset(detail);areaSnapshots.return_A=await areaStats(A,detail,0);expect(areaSnapshots.return_A).toEqual(areaSnapshots.initial_A);
  expect(writes.map(r=>r.method+' '+r.path)).toEqual(['POST /api/project/open','POST /api/project/open']);expect(writes.map(r=>JSON.parse(r.body!))).toEqual([{project_dir:B.project.project_dir},{project_dir:A.project.project_dir}]);
  const after={A:trees(A),B:trees(B)};expect(after).toEqual(before);for(const scope of[A,B]){for(const item of scope.fixture.items){expect(sha(fs.readFileSync(item.report_path))).toBe(item.report_sha256);e.addFile(item.report_path);}for(const input of scope.fixture.inputs){expect(sha(fs.readFileSync(input.path))).toBe(input.sha256);e.addFile(input.path);}}
  await e.screenshot(page,`${native?'source-electron':'browser'}-f055-A-return-transient-only`);
  proof={schema:'modu-vision.f055-area-history-error-cancel/v1',action:'F055.area-bin-filter',dimensions:['error','cancel'],mode:native?'source-electron':'browser',fixtures:{A,B},fixture_arithmetic:{A:{predicted_areas:[4,16,36,49],maximum:49,step:4.9,counts:[1,0,0,1,0,0,0,1,0,1],selected_bin:3,selected_file:'case-2.png'},B:{predicted_areas:[6,12,24,30],maximum:30,step:3,counts:[0,0,1,0,1,0,0,0,1,1],selected_bin:8,selected_file:'case-4.png'},unit:'original_image_px2',source_images:'16x16 valid own fixture images'},area_observations:areaSnapshots,error,cancel:{...cancel,original_captured_A_read:canceledRead,meaning:'navigate to B during pending local saved-area read; revoke A display admission, not cancel a server job'},toB,toA,saved_A:initialSaved,saved_B:B_saved,original_owning_history_reads:[originalRead,recovered,returnRead],original_raw_trees:before,final_raw_trees:after,business_writes:writes,model_or_flow_threshold_saved:false,controlled_saved_reports_not_model_inference:true,actual_model_inference:false,invalid_cells_claimed:false,Native_accepted:false,parent_completion_credit:false,quality_human_installed_target_approval:false};
 }catch(error){failed=true;throw error;}finally{
  if(observing)page.off('request',observe);let first:unknown,closeFailed=false,count=0;
  const note=(error:unknown)=>{count++;if(!closeFailed){first=error;closeFailed=true;}};
  for(const control of controls.reverse()){try{await control.close();}catch(error){note(error);}}
  for(const database of databases.reverse()){try{database.close();}catch(error){note(error);}}
  for(const fd of retainedFDs.reverse()){try{fs.closeSync(fd);}catch(error){note(error);}}
  if(closeFailed){try{e.note('f055_pending_secondary_cleanup',{failures:count,type:typeof first});}catch{}if(!failed)throw first;}
 }
 proof.resource_closure={owned_route_handlers_joined:true,request_observers_removed:true,readonly_sqlite_observers_closed:true,retained_raw_FDs_closed:true};const output=path.join(w.logs,'evaluation-area-history-error-cancel-proof.json');fs.writeFileSync(output,JSON.stringify(proof),{flag:'wx'});e.addFile(output);e.note('evaluation_area_history_error_cancel',proof);
}

// Source-only F055 error/cancel extension; Root runs the exact browser and source-Electron cases.
test('saved area bins disappear on failed history and survive canceled A read in B',async({page,renderer,workspace,evidence})=>{
 await installDesktopHostShim(page,renderer.port);const api:OwnedApi=async(route,body,method)=>{const response=await page.request.fetch(renderer.origin+route,{method:method||(body===undefined?'GET':'POST'),timeout:10_000,...(body===undefined?{}:{data:body})});expect(response.ok(),await response.text()).toBe(true);return response.json();};await f055SavedAreaPending(page,workspace,evidence,api,false,renderer.origin,renderer.url);
});
test('native saved area bins recover exact A and retain distinctive B after canceled history',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const page=electronSession.window,backend=await electronSession.waitForBackend(),origin=`http://127.0.0.1:${backend.port}`;const api:OwnedApi=(route,body,method)=>page.evaluate(async({origin,route,body,method})=>{const response=await fetch(origin+route,{method:method||(body===undefined?'GET':'POST'),headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!response.ok)throw Error('Owned F055 fixture HTTP '+response.status);return response.json();},{origin,route,body,method});await f055SavedAreaPending(page,workspace,evidence,api,true,origin,undefined);
});

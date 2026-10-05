import fs from 'node:fs';import path from 'node:path';import crypto from 'node:crypto';
import {test,expect} from './fixtures/test';import {png} from './qa/appFlow';

test.use({actionTimeout:10_000});
test('native rotation runs CPU optimization then acknowledges its runtime budget without a manual stop',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const {window}=electronSession;const backend=await electronSession.waitForBackend();expect(new URL(window.url()).protocol).toBe('file:');
 const api=async(route:string,body?:any)=>window.evaluate(async({port,route,body})=>{
  const response=await fetch(`http://127.0.0.1:${port}${route}`,{...(body===undefined?{}:{method:route.endsWith('/update')?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
  if(!response.ok)throw new Error(`Owned fixture API: HTTP ${response.status}`);return response.json();
 },{port:backend.port,route,body});
 const source=path.join(workspace.root,'native-rotation-source');fs.mkdirSync(source);const rows:any[]=[];const originals:Record<string,string>={};
 for(let i=0;i<4;i++){const image=`part-${i}.png`,file=path.join(source,image);fs.writeFileSync(file,png(64,3,(x,y)=>[x+i*5,y,120]));originals[file]=crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');rows.push({image,split:['train','train','val','test'][i],correction_deg:i*30});}
 const project=await api('/api/project/create',{name:'Native specialist runtime',task:'classification'});await api('/api/project/update',{source_dataset_dir:source});const prepared=await api('/api/rotation/prepare',{source_dataset_path:source,samples:rows});
 let jobId='',posted:any;window.on('response',async response=>{if(new URL(response.url()).pathname==='/api/rotation/train'&&response.request().method()==='POST'){posted=response.request().postDataJSON();jobId=(await response.json()).job_id;}});
 await window.reload();await expect(window.getByTitle('프로젝트 관리',{exact:true})).toContainText('Native specialist runtime');
 await window.getByRole('navigation',{name:'Workflow Stages'}).getByRole('button').nth(2).click();await window.getByRole('region',{name:'모델 학습 허브'}).getByRole('button',{name:/^정방향 보정/}).click();
 await window.getByLabel('Epoch',{exact:true}).fill('500');await window.getByLabel('배치 크기',{exact:true}).fill('1');await window.getByLabel('모델 입력 크기',{exact:true}).fill('128');await window.getByLabel(/CNN 채널 폭/).selectOption('32');
 await window.locator('summary',{hasText:'학습 시간 제한'}).click();await window.getByLabel('학습 시간 제한 (분)',{exact:true}).fill('0.02');
 await window.getByLabel('학습 대기열 우선순위',{exact:true}).fill('8');
 await window.getByRole('button',{name:'정방향 모델 후보 학습',exact:true}).click();await expect.poll(()=>jobId).not.toBe('');
 await expect(window.getByRole('status',{name:'학습 작업 상태'})).toContainText('Training runtime limit exceeded',{timeout:15000});await expect(window.getByRole('button',{name:'정방향 모델 후보 학습',exact:true})).toBeEnabled();
 const chain=window.getByRole('list',{name:'취소 확인 단계'});
 for(const label of ['요청 저장','작업자 확인','종료 확인','예약 반환'])await expect(chain.getByRole('listitem').filter({hasText:label})).toContainText('✓');
 const terminal=await api(`/api/rotation/jobs/${jobId}`);expect(terminal.status).toBe('stopped');expect(terminal.stop_reason).toBe('time_limit');expect(terminal.batch).toBeGreaterThan(0);expect(terminal.ledger_state).toBe('aborted');expect(terminal.priority).toBe(8);
 expect(terminal.observation.cancel.complete).toBe(true);expect(terminal.observation.pending_finalization).toBe(false);
 expect(posted).toMatchObject({dataset_path:prepared.dataset_path,max_runtime_s:1.2,device:'cpu',queue:true,priority:8});
 for(const name of ['best_model.pt','job_receipt.json'])expect(fs.existsSync(path.join(project.models_dir,'rotation',jobId,name))).toBe(false);expect((await api('/api/rotation/models')).models).toEqual([]);
 for(const [file,sha]of Object.entries(originals))expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(sha);
 await window.getByRole('status',{name:'학습 작업 상태'}).scrollIntoViewIfNeeded();await expect(chain).toBeVisible();
 await evidence.screenshot(window,'native-specialist-runtime-stopped');evidence.note('scope',{actual_electron_main_preload:true,actual_owned_backend:true,actual_cpu_training:true,server:false,manual_cancellation:false,posted,terminal,source_unchanged:true,source_images:Object.entries(originals).map(([path,sha256])=>({path,sha256}))});
});

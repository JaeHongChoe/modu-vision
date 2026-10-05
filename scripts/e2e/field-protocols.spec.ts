import path from 'node:path';
import {createServer} from 'node:http';
import {execFileSync} from 'node:child_process';
import type {AddressInfo} from 'node:net';
import type {Page} from '@playwright/test';
import {test,expect,type Workspace,type Evidence} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
const harness=require('./fixtures/harness.cjs');
type Api=(route:string,body?:unknown,method?:string)=>Promise<any>;
async function exercise(page:Page,workspace:Workspace,evidence:Evidence,api:Api,native:boolean,url?:string){
 const tool=path.join(harness.REPO_ROOT,'scripts/e2e/fixtures/operator_runtime.py'),env={...process.env,VISION_AI_STUDIO_USER_DATA_DIR:workspace.userData};
 const fixture=JSON.parse(execFileSync(harness.resolvePython(),[tool,workspace.root],{cwd:harness.REPO_ROOT,env,encoding:'utf8',timeout:90_000}).trim());
 evidence.note('fixture',fixture);
 let accepted=false;const received:Array<{payload:any;key:string|undefined}>=[];
 const receiver=createServer(async(req,res)=>{const chunks:Buffer[]=[];for await(const chunk of req)chunks.push(Buffer.from(chunk));const payload=JSON.parse(Buffer.concat(chunks).toString());received.push({payload,key:req.headers['idempotency-key'] as string|undefined});res.setHeader('Content-Type','application/json');res.end(JSON.stringify({receipt:{accepted,job:payload.receipt_id}}));});
 await new Promise<void>(done=>receiver.listen(0,'127.0.0.1',done));const port=(receiver.address() as AddressInfo).port;
 try{
  await api('/api/project/open',{project_dir:fixture.project.project_dir});if(url)await page.goto(url);else await page.reload();
  await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();
  let panel=page.getByRole('region',{name:'검사 서비스 배포'});
  await panel.getByText('PLC·MES 연결 설정과 로컬 시험',{exact:true}).click();
  await panel.getByText('고급 JSON · 필드/판정/ACK 매핑',{exact:true}).click();
  const mapping={enabled:false,modbus:{host:'127.0.0.1',port:1,result_register:10,ack_register:11,sequence_register:12,trigger_register:13,trigger_image_path:Object.keys(fixture.images)[0],verdict_values:{OK:4660,NG:2,REVIEW:3}},mes:null};
  await panel.getByLabel('PLC MES 고급 설정 JSON',{exact:true}).fill(JSON.stringify(mapping));
  await panel.getByLabel('PLC 16-bit payload byte order',{exact:true}).selectOption('little');
  await panel.getByRole('button',{name:'설정 검증·저장',exact:true}).click();await expect(panel).toContainText('설정 저장됨');
  const saved=await api('/api/runtime-services');expect(saved.adapter_config.modbus.byte_order).toBe('little');expect(saved.adapter_config.modbus.trigger_image_path).toBe(mapping.modbus.trigger_image_path);
  await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();panel=page.getByRole('region',{name:'검사 서비스 배포'});
  await panel.getByText('PLC·MES 연결 설정과 로컬 시험',{exact:true}).click();
  await expect(panel.getByLabel('PLC 16-bit payload byte order',{exact:true})).toHaveValue('little');
  await expect(panel.getByLabel('트리거 이미지 (프로젝트 원본)',{exact:true})).toHaveValue(mapping.modbus.trigger_image_path);
  const contracts=[];
  for(const protocol of ['modbus','http'] as const)for(const mode of ['success','reject','timeout'] as const){
   await panel.getByLabel('프로토콜 로컬 시험 종류',{exact:true}).selectOption(mode);
   const reply=page.waitForResponse(r=>r.url().endsWith('/protocol-test')&&r.request().method()==='POST');
   await panel.getByRole('button',{name:protocol==='http'?'HTTP 로컬 시험':'Modbus 로컬 시험',exact:true}).click();const response=await reply;
   expect(response.status(),await response.text()).toBe(200);const result=await response.json();expect(result.acknowledged).toBe(mode==='success');expect(result.physical_equipment_verified).toBe(false);contracts.push(result);
   await expect(panel).toContainText(`${protocol==='http'?'HTTP':'Modbus'} 로컬 ${mode} 시험`);
  }
  await panel.getByText('고급 JSON · 필드/판정/ACK 매핑',{exact:true}).click();
  const mes={enabled:true,modbus:null,mes:{url:`http://127.0.0.1:${port}/owned-receiver`,timeout:1,field_mapping:{receipt_id:'job_id',decision:'model_verdict',input_hash:'image_sha256'},ack_field:'receipt.accepted',ack_value:true,ack_job_field:'receipt.job'}};
  await panel.getByLabel('PLC MES 고급 설정 JSON',{exact:true}).fill(JSON.stringify(mes));await panel.getByRole('button',{name:'설정 검증·저장',exact:true}).click();await expect(panel).toContainText('설정 저장됨');
  await page.getByRole('navigation',{name:'배포 운영 화면',exact:true}).getByRole('button',{name:'운영자 검사',exact:true}).click();const operator=page.getByRole('region',{name:'운영자 검사 작업 공간'});
  await operator.getByRole('button',{name:'검사 시작',exact:true}).click();await expect(operator).toContainText('승인 적용 기록과 실행 버전 일치',{timeout:60_000});
  await operator.getByLabel('운영자 검사 이미지',{exact:true}).selectOption(Object.keys(fixture.images)[0]);
  const pending=page.waitForResponse(r=>r.url().endsWith('/operator/inspect')&&r.request().method()==='POST');await operator.getByRole('button',{name:'검사 입력',exact:true}).click();const submitted=await pending;expect(submitted.status()).toBe(202);const id=(await submitted.json()).job_id;
  const job=async()=>{const q=await api('/api/product-delivery/operator/queue');return q.jobs.find((j:any)=>j.job_id===id);};
  await expect.poll(async()=>(await job())?.state,{timeout:30_000}).toBe('delivery_error');const rejected=await job();
  expect(rejected.model_verdict).toBe('OK');expect(rejected.verdict).toBe('REVIEW');expect(received).toHaveLength(1);expect(received[0].key).toBe(id);
  const card=operator.locator('article').filter({has:page.getByRole('button',{name:'ACK 전송 재시도',exact:true})});
  await expect(card).toContainText('delivery_error',{timeout:20_000});await expect(card).toContainText('모델 OK · 운영 REVIEW');
  await evidence.screenshot(page,`${native?'native':'browser'}-rejected-mes-delivery`);
  accepted=true;await card.getByRole('button',{name:'ACK 전송 재시도',exact:true}).click();await expect.poll(async()=>(await job())?.state,{timeout:30_000}).toBe('completed');const completed=await job();
  expect(completed.model_verdict).toBe('OK');expect(completed.verdict).toBe('OK');expect(received).toHaveLength(2);expect(received[1]).toEqual(received[0]);
  await expect(operator).toContainText('completed',{timeout:20_000});await evidence.screenshot(page,`${native?'native':'browser'}-acknowledged-mes-retry`);
  await page.reload();await page.getByRole('button',{name:'패키지·장치·진단',exact:true}).click();panel=page.getByRole('region',{name:'검사 서비스 배포'});await panel.getByText('PLC·MES 연결 설정과 로컬 시험',{exact:true}).click();
  await expect(panel.getByLabel('MES HTTP 주소',{exact:true})).toHaveValue(mes.mes.url);
  const reopened=await api('/api/runtime-services');expect(reopened.adapter_config.mes.field_mapping).toEqual(mes.mes.field_mapping);expect(reopened.adapter_config.mes.ack_job_field).toBe('receipt.job');
  await panel.getByRole('button',{name:'서비스 중지',exact:true}).click();await expect(panel).toContainText('현재 응답: stopped');
  evidence.note('protocol_closure',{project:fixture.project,saved_mapping:saved.adapter_config,reopened_config:reopened.adapter_config,contracts,inspection:id,rejected,completed,received,receiver_port:port,
   actual_cpu_inspection:true,actual_socket_receivers:true,actual_ui_save_reload_retry:true,physical_equipment_verified:false,native,windows_excluded:true});
 }finally{
  await new Promise<void>((done,reject)=>receiver.close(error=>error?reject(error):done()));
  const stopped=JSON.parse(execFileSync(harness.resolvePython(),[tool,'stop',fixture.project.project_dir],{cwd:harness.REPO_ROOT,encoding:'utf8',timeout:30_000}).trim());evidence.note('owned_service_cleanup',stopped);expect(stopped.status).toBe('stopped');
 }
}
test('field mappings and explicit rejected-to-ACK retry on actual sockets',async({page,renderer,workspace,evidence})=>{
 const api:Api=async(route,body,method)=>{const r=await page.request.fetch(renderer.origin+route,{method:method||(body?'POST':'GET'),data:body});expect(r.ok(),await r.text()).toBe(true);return r.json();};await installDesktopHostShim(page,renderer.port);await exercise(page,workspace,evidence,api,false,renderer.url);
});
test('native field mapping persistence and explicit same-job ACK retry',{tag:'@electron'},async({electronSession,workspace,evidence})=>{
 const status=await electronSession.waitForBackend(),page=electronSession.window;
 const api:Api=(route,body,method)=>page.evaluate(async({port,route,body,method})=>{const r=await fetch(`http://127.0.0.1:${port}${route}`,{method:method||(body?'POST':'GET'),headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});if(!r.ok)throw Error(`Owned protocol fixture HTTP ${r.status}: ${await r.text()}`);return r.json();},{port:status.port,route,body,method});await exercise(page,workspace,evidence,api,true);
});

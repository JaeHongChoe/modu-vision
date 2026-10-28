import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test,expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import type {AssistantBridge,AssistantConfigState} from '../../src/types/electron';
test.use({actionTimeout:15_000});

// Isolated app-owned browser fixture. Provider replies are controlled; real project/task/preview/split APIs execute.
// It does not use any user browser profile, login, model, external service or training request.
test('a direct work request saves previewed preparation and executes one durable plan without a review checkpoint',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(180_000);
 const source=path.join(workspace.root,'continuous-preparation-originals'),originals:Array<{file:string;hash:string}>=[];
 for(const [index,label]of ['OK','NG'].entries()){
  const folder=path.join(source,label);fs.mkdirSync(folder,{recursive:true});
  for(let n=0;n<10;n++){const file=path.join(folder,`${label}-${n}.png`);fs.writeFileSync(file,png(64,3,(x,y)=>[50+index*70+n,x%256,y%256]));originals.push({file,hash:crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')});}
 }
 const api=async(route:string,body?:unknown,method=body===undefined?'GET':'POST')=>{
  const response=await page.request.fetch(renderer.origin+route,{method,...(body===undefined?{}:{data:body})});expect(response.ok(),`${method} ${route}: ${await response.text()}`).toBe(true);return response.json();
 };
 const project=await api('/api/project/create',{name:'Continuous preparation fixture',task:'classification'});
 await api('/api/project/update',{source_dataset_dir:source},'PUT');await api('/api/dataset/import',{folder_path:source,task:'classification'});
 await api('/api/dataset/split',{folder_path:source,task:'classification',train_ratio:.8,val_ratio:.1,test_ratio:.1});
 const errors:string[]=[],foreign:string[]=[],forbidden:string[]=[],splitWrites:unknown[]=[];
 page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{const url=new URL(request.url());if(/^https?:$/.test(url.protocol)&&url.origin!==renderer.origin)foreign.push(url.origin);if(url.pathname==='/api/dataset/split'&&request.method()==='POST')splitWrites.push(request.postDataJSON());if(request.method()!=='GET'&&/\/api\/(training\/start|labeling-jobs|inspections\/runs)/.test(url.pathname))forbidden.push(url.pathname);});
 await page.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===renderer.origin?route.fallback():route.abort('blockedbyclient'));
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await expect(page.getByRole('button',{name:'전체 (20)',exact:true})).toBeVisible();
 await page.evaluate(()=>{
  const config:AssistantConfigState={active:'chatgpt',encryptionAvailable:true,providers:[{provider:'chatgpt',label:'Controlled no-network provider',baseUrl:'https://api.openai.com/v1',model:'continuous-fixture-no-network',format:'responses',hasKey:false,supportsImages:false,ready:true}],chatgpt:{active:'fixture',accounts:[{recordId:'fixture',label:'Controlled fixture',model:'continuous-fixture-no-network',signedIn:true,planUsageEnabled:true,planUsageAcknowledged:true}],status:'connected',encryptionAvailable:true,authority:'controlled'}};
  let calls=0;const bridge:AssistantBridge={getState:async()=>config,saveProvider:async()=>config,removeKey:async()=>config,removeProvider:async()=>config,setActive:async()=>config,listModels:async()=>({ok:true,models:[]}),cancel:async()=>true,beginRun:async()=>true,endRun:async()=>{},imageIntent:async()=>({ok:false,error:{code:'image_refused',reason:'No external image in this fixture',action:'Use preparation preview only'}}),send:async()=>{
   calls++;if(calls===1)return{ok:true,text:'',stop:'tool',toolCalls:[{id:'continuous-plan',name:'propose_task',arguments:{goal:'합성 자료의 고정 입력과 학습 설정 준비',steps:[
    {action:'configure_input_recipe',args:{input_recipe:{version:1,operations:[{op:'brightness',factor:1.2}]}},label:'선택 원본으로 입력 규칙 검증·저장'},
    {action:'save_split',args:{train_percent:70,val_percent:20,test_percent:10},label:'분할 저장'},
    {action:'configure_training',args:{preset:'fast',device:'cpu',batch_size:8},label:'CPU 설정 준비'},
    {action:'open_step',args:{step:3},label:'준비한 학습 화면 열기'}]}}]};
   return{ok:true,text:'저장한 준비 결과를 확인했습니다.',stop:'end',toolCalls:[]};
  }};
  if(!window.api)throw Error('Controlled desktop shim unavailable');Object.assign(window.api,{assistant:bridge});
 });
 await page.getByRole('button',{name:'왜 그런가요?',exact:true}).click();await page.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();
 const assistant=page.getByRole('complementary',{name:'도우미',exact:true}),card=assistant.getByRole('region',{name:'도우미 작업',exact:true});
 await expect(card).toHaveAttribute('aria-busy','false');await assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true}).fill('이 자료 전처리를 저장하고 학습 준비해줘');await assistant.getByRole('button',{name:'보내기',exact:true}).click();
 await expect.poll(async()=>{const tasks=(await api('/api/assistant-tasks')).tasks;return tasks.find((task:{goal:string})=>task.goal==='합성 자료의 고정 입력과 학습 설정 준비')?.status;},{timeout:30_000}).toBe('completed');
 const task=(await api('/api/assistant-tasks')).tasks.find((item:{goal:string})=>item.goal==='합성 자료의 고정 입력과 학습 설정 준비');
 expect(task.authorization.source).toBe('user_request');expect(task.approval).toBeNull();expect(task.steps.map((step:{status:string})=>step.status)).toEqual(['completed','completed','completed','completed']);
 expect(task.steps[0].receipt.input_recipe_sha256).toMatch(/^[a-f0-9]{64}$/);expect(task.steps[0].receipt.preview_source_sha256).toMatch(/^[a-f0-9]{64}$/);expect(task.steps[0].receipt.previewed).toBe(true);
 await expect(card.getByRole('button',{name:'계획 확인하고 실행',exact:true})).toHaveCount(0);expect(splitWrites).toHaveLength(1);
 await assistant.getByRole('button',{name:'닫기',exact:true}).click();await expect(page.getByRole('heading',{name:'학습 흐름 준비',exact:true})).toBeVisible();
 await expect(page.getByLabel('1번 밝기 배율',{exact:true})).toHaveValue('1.2');
 await evidence.screenshot(page,'continuous-preparation-saved-without-review');
 for(const {file,hash}of originals)expect(crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')).toBe(hash);
 expect(errors).toEqual([]);expect(foreign).toEqual([]);expect(forbidden).toEqual([]);evidence.note('continuous_preparation',{project_id:project.id,task_id:task.id,status:task.status,authorization_source:task.authorization.source,input_recipe_sha256:task.steps[0].receipt.input_recipe_sha256,originals_preserved:originals.length,controlled_provider:true,actual_training:false});
});

test('selected image is normalized locally and keeps its reasoning choice through a controlled tool followup',async({page,renderer,workspace,evidence})=>{
 test.setTimeout(120_000);
 const project=await page.request.post(renderer.origin+'/api/project/create',{data:{name:'Image effort controlled fixture',task:'classification'}});expect(project.ok()).toBe(true);
 const errors:string[]=[],foreign:string[]=[];page.on('pageerror',error=>errors.push(error.message));page.on('request',request=>{const url=new URL(request.url());if(/^https?:$/.test(url.protocol)&&url.origin!==renderer.origin)foreign.push(url.origin);});
 await page.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===renderer.origin?route.fallback():route.abort('blockedbyclient'));
 await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
 await page.evaluate(()=>{
  const capabilities={imageInput:true,reasoningEfforts:['low','high'] as const,source:'catalog' as const};
  const config:AssistantConfigState={active:'chatgpt',encryptionAvailable:true,providers:[{provider:'chatgpt',label:'Controlled image model',baseUrl:'https://api.openai.com/v1',model:'image-fixture-no-network',format:'responses',hasKey:false,supportsImages:true,ready:true,capabilities:{...capabilities,reasoningEfforts:[...capabilities.reasoningEfforts]}}],chatgpt:{active:'fixture',accounts:[{recordId:'fixture',label:'Controlled fixture',model:'image-fixture-no-network',signedIn:true,planUsageEnabled:true,planUsageAcknowledged:true,capabilities:{...capabilities,reasoningEfforts:[...capabilities.reasoningEfforts]}}],status:'connected',encryptionAvailable:true,authority:'image-controlled'}};
  const unavailable=async():Promise<AssistantConfigState>=>{throw Error('This fixture does not perform account operations');};
  const intents:Array<{hash:string;context:unknown}>=[],calls:Array<{grant:string;hash:string;effort?:string;bytes:number}>=[];
  Object.assign(window,{__imageEffortFixture:{intents,calls}});
  const bridge:AssistantBridge={getState:async()=>config,saveProvider:async()=>config,removeKey:async()=>config,removeProvider:async()=>config,setActive:async()=>config,listModels:async()=>({ok:true,models:[]}),cancel:async()=>true,beginRun:async()=>true,endRun:async()=>{},chatgpt:{begin:unavailable,acknowledgePlanUsage:unavailable,resumeUsage:unavailable,recover:unavailable,cancel:unavailable,selectAccount:unavailable,selectModel:unavailable,signOut:unavailable},imageIntent:async input=>{intents.push({hash:input.imageIdentity,context:input.context});return{ok:true,grantId:'controlled-'+intents.length,message:'Selected fixture pixels only',providerLabel:'Controlled fixture',expiresAt:Date.now()+30_000};},send:async input=>{
   if(!input.image)throw Error('Selected pixels were omitted from the stateless request');const bytes=Uint8Array.from(atob(input.image.base64),value=>value.charCodeAt(0)),hash=Array.from(new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256',bytes)),value=>value.toString(16).padStart(2,'0')).join('');if(hash!==input.image.imageIdentity)throw Error('Selected bytes changed');
   calls.push({grant:input.image.grantId,hash,effort:input.reasoningEffort,bytes:bytes.length});
   return calls.length===1?{ok:true,text:'',stop:'tool',toolCalls:[{id:'image-read-status',name:'read_status',arguments:{}}]}:{ok:true,text:'선택 이미지와 현재 상태를 확인했습니다.',stop:'end',toolCalls:[]};
  }};
  if(!window.api)throw Error('Controlled desktop shim unavailable');Object.assign(window.api,{assistant:bridge});
 });
 await page.getByRole('button',{name:'왜 그런가요?',exact:true}).click();await page.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();const assistant=page.getByRole('complementary',{name:'도우미',exact:true});
 await assistant.locator('summary').filter({hasText:/^ChatGPT 연결$/}).click();
 await expect(assistant.getByLabel('추론 강도',{exact:true})).toBeEnabled();await assistant.getByLabel('추론 강도',{exact:true}).selectOption('high');
 const selected=path.join(workspace.root,'selected-image.png');fs.writeFileSync(selected,png(64,3,(x,y)=>[x,y,120]));
 await assistant.getByLabel('도우미 이미지 파일 선택',{exact:true}).setInputFiles(selected);
 await expect(assistant.getByRole('img',{name:/첨부/})).toBeVisible();await expect(assistant).toContainText('ChatGPT');
 await evidence.screenshot(page,'selected-image-before-explicit-send');
 await assistant.getByRole('checkbox',{name:'설명만 받기 · 작업은 실행하지 않음',exact:true}).check();
 await assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true}).fill('이 이미지와 현재 상태를 설명해줘');await assistant.getByRole('button',{name:'보내기',exact:true}).click();
 await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('선택 이미지와 현재 상태를 확인했습니다.');
 const recorded=await page.evaluate(()=>{const state=(window as unknown as {__imageEffortFixture:{intents:Array<{hash:string;context:unknown}>;calls:Array<{grant:string;hash:string;effort?:string;bytes:number}>}}).__imageEffortFixture;return{intents:state.intents,calls:state.calls};});
 expect(recorded.calls).toHaveLength(2);expect(recorded.intents).toHaveLength(2);expect(recorded.calls.map(call=>call.grant)).toEqual(['controlled-1','controlled-2']);expect(recorded.calls.map(call=>call.effort)).toEqual(['high','high']);expect(recorded.calls[0].hash).toBe(recorded.calls[1].hash);expect(recorded.calls[0].bytes).toBeGreaterThan(0);expect(recorded.intents[0].context).toEqual(recorded.intents[1].context);
 expect((await page.request.get(renderer.origin+'/api/assistant-tasks')).ok()).toBe(true);expect(errors).toEqual([]);expect(foreign).toEqual([]);evidence.note('controlled_image_effort',{...recorded,actual_provider:false,explicit_image:true});
});

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import {test, expect} from './fixtures/test';
import {installDesktopHostShim} from './fixtures/desktop-host-shim';
import {png} from './qa/appFlow';
import type {AssistantBridge, AssistantConfigState, AssistantSendInput} from '../../src/types/electron';

const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');

// The renderer, backend, stores and run log are real. The provider reply and begin/end IPC are controlled;
// this tests the in-panel approval flow, not Electron main-process run-header attribution.
test('a declined assistant split never writes; a confirmed split saves and logs its own store mutation successfully', async ({page, renderer, workspace, evidence}) => {
  test.setTimeout(180_000);
  const source=path.join(workspace.root,'confirmed-split-source');
  const originals: Array<{path: string; sha256: string}>=[];
  for (const [index,label] of ['OK','NG'].entries()) {
    const folder=path.join(source,label);fs.mkdirSync(folder,{recursive:true});
    for(let number=0;number<10;number++) {
      const file=path.join(folder,`${label}-${String(number).padStart(2,'0')}.png`);
      fs.writeFileSync(file,png(128,3,(x,y)=>[100+index*50+number,(x+number)%256,(y+index)%256]));
      originals.push({path:file,sha256:sha(fs.readFileSync(file))});
    }
  }
  const api=async(route:string,body?:unknown,method=body===undefined?'GET':'POST')=>{
    const response=await page.request.fetch(renderer.origin+route,{method,...(body===undefined?{}:{data:body})});
    expect(response.ok(),`${method} ${route}: ${await response.text()}`).toBe(true);return response.json();
  };
  const project=await api('/api/project/create',{name:'Assistant confirmed split',task:'classification'});
  await api('/api/project/update',{source_dataset_dir:source},'PUT');
  await api('/api/dataset/import',{folder_path:source,task:'classification'});
  const initial=await api('/api/dataset/split',{folder_path:source,task:'classification',train_ratio:.8,val_ratio:.1,test_ratio:.1});
  expect(initial.split).toEqual({train:16,val:2,test:2});
  const splitFile=path.join(project.dataset_dir,'splits',crypto.createHash('sha256').update(source).digest('hex')+'.json');
  const initialSha=sha(fs.readFileSync(splitFile));
  const pageErrors:string[]=[],externalRequests:string[]=[],splitWrites:unknown[]=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('request',request=>{
    const url=new URL(request.url());
    if(/^https?:$/.test(url.protocol)&&url.origin!==renderer.origin)externalRequests.push(request.url());
    if(url.origin===renderer.origin&&url.pathname==='/api/dataset/split'&&request.method()==='POST')splitWrites.push(request.postDataJSON());
    if(url.origin===renderer.origin&&/\/api\/(training\/start|label-suggestions\/batches|labeling-jobs)/.test(url.pathname)&&request.method()!=='GET')
      pageErrors.push(`Unexpected model execution request: ${url.pathname}`);
  });
  await page.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===renderer.origin?route.fallback():route.abort('blockedbyclient'));
  await installDesktopHostShim(page,renderer.port);await page.goto(renderer.url);
  await expect(page.getByTitle('프로젝트 관리',{exact:true})).toContainText(project.name);
  // Project identity arrives before dataset hydration on a slow host. The real save entry guards current source and count.
  await expect(page.getByRole('button',{name:'이 비율로 나누기',exact:true})).toBeEnabled();
  await expect(page.getByRole('button',{name:'전체 (20)',exact:true})).toBeVisible();
  await page.evaluate(()=>{
    const config:AssistantConfigState={active:'chatgpt',encryptionAvailable:true,providers:[{
      provider:'chatgpt',label:'ChatGPT',baseUrl:'https://api.openai.com/v1',model:'scripted-no-network',format:'responses',hasKey:false,supportsImages:false,ready:true,
    }],chatgpt:{accounts:[{recordId:'qa-chatgpt',label:'QA 계정',model:'scripted-no-network',signedIn:true,planUsageEnabled:true,planUsageAcknowledged:true}],active:'qa-chatgpt',encryptionAvailable:true,status:'connected',authority:'qa-authority'}};
    const sent:AssistantSendInput[]=[],armed:Array<{runId:string;tool:string}>=[],ended:string[]=[];
    const bridge:AssistantBridge={getState:async()=>config,saveProvider:async()=>config,removeKey:async()=>config,
      removeProvider:async()=>config,setActive:async()=>config,listModels:async()=>({ok:true,models:[]}),cancel:async()=>true,
      imageIntent:async()=>({ok:false,error:{code:'image_refused',reason:'이 검사는 이미지를 보내지 않습니다.',action:'분할만 확인하세요.'}}),
      beginRun:async(runId,tool)=>{armed.push({runId,tool});return true;},endRun:async runId=>{ended.push(runId);},
      send:async input=>{
        sent.push(input);const index=sent.length;
        if(index===1||index===3)return {ok:true,text:'분할 저장 미리보기를 확인하세요.',stop:'tool',toolCalls:[{
          id:`save-split-${index}`,name:'save_split',arguments:{train_percent:70,val_percent:20,test_percent:10},
        }]};
        return {ok:true,text:index===2?'거절을 확인했습니다. 저장하지 않았습니다.':'새 분할 저장을 마쳤습니다. 대화를 완료합니다.',stop:'end',toolCalls:[]};
      },
    };
    if(!window.api)throw new Error('Desktop host shim did not initialize');
    Object.assign(window.api,{assistant:bridge});Object.assign(window,{__confirmedSplit:{sent,armed,ended}});
  });
  await page.getByRole('button',{name:'왜 그런가요?',exact:true}).click();
  await page.getByRole('button',{name:'도우미에게 묻기',exact:true}).click();
  const assistant=page.getByRole('complementary',{name:'도우미',exact:true});
  const ask=async(text:string)=>{
    await assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true}).fill(text);
    await assistant.getByRole('button',{name:'보내기',exact:true}).click();
    await expect(assistant.getByRole('group',{name:'쓰기 실행 확인',exact:true})).toContainText('학습 70% · 검증 20% · 시험 10%');
  };
  await ask('분할을 70/20/10으로 저장하려고 합니다. 미리보기를 보여 주세요.');
  expect(splitWrites).toHaveLength(0);expect(sha(fs.readFileSync(splitFile))).toBe(initialSha);
  await evidence.screenshot(page,'real-assistant-split-preview-before-any-write');
  await assistant.getByRole('button',{name:'실행하지 않기',exact:true}).click();
  await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('거절을 확인했습니다. 저장하지 않았습니다.');
  await expect(assistant.getByRole('group',{name:'쓰기 실행 확인',exact:true})).toHaveCount(0);
  expect(splitWrites).toHaveLength(0);expect(sha(fs.readFileSync(splitFile))).toBe(initialSha);
  const declined=(await api('/api/assistant/runs')).runs.filter((run:{tool:string})=>run.tool==='save_split');
  expect(declined).toHaveLength(1);expect(declined[0]).toMatchObject({result:'refused',confirmed:false,error_code:'declined_by_person'});

  await ask('이번에는 같은 70/20/10 분할을 저장하겠습니다. 미리보기를 보여 주세요.');
  expect(splitWrites).toHaveLength(0);
  const savedResponse=page.waitForResponse(response=>new URL(response.url()).pathname==='/api/dataset/split'&&response.request().method()==='POST');
  await assistant.getByRole('button',{name:'확인하고 실행',exact:true}).click();
  const response=await savedResponse;expect(response.status()).toBe(200);
  expect((await response.json()).split).toEqual({train:14,val:4,test:2});
  await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('분할을 저장했습니다(학습 14 · 검증 4 · 시험 2장).');
  await expect(assistant.getByRole('log',{name:'도우미 대화'})).toContainText('새 분할 저장을 마쳤습니다. 대화를 완료합니다.');
  await expect(assistant.getByRole('group',{name:'쓰기 실행 확인',exact:true})).toHaveCount(0);
  await expect(assistant.getByRole('textbox',{name:'도우미에게 묻기',exact:true})).toBeEnabled();
  await expect(assistant.getByRole('button',{name:'멈추기',exact:true})).toHaveCount(0);
  expect(splitWrites).toHaveLength(1);expect(splitWrites[0]).toMatchObject({folder_path:source,task:'classification',train_ratio:.7,val_ratio:.2,test_ratio:.1});
  const finalSha=sha(fs.readFileSync(splitFile));expect(finalSha).not.toBe(initialSha);
  const stored=JSON.parse(fs.readFileSync(splitFile,'utf8'));
  const counts=Object.values(stored.assignments).reduce<Record<string,number>>((all,name)=>{const key=String(name);all[key]=(all[key]||0)+1;return all;},{});
  expect(counts).toEqual({train:14,val:4,test:2});
  const logged=(await api('/api/assistant/runs')).runs.filter((run:{tool:string})=>run.tool==='save_split');
  expect(logged).toHaveLength(2);expect(logged[1]).toMatchObject({result:'ok',confirmed:true,tier:'write'});
  const ipc=await page.evaluate(()=>{
    const state=(window as unknown as {__confirmedSplit:{sent:AssistantSendInput[];armed:Array<{runId:string;tool:string}>;ended:string[]}}).__confirmedSplit;
    return {providerCalls:state.sent.length,imageSends:state.sent.filter(call=>call.image).length,armed:state.armed,ended:state.ended};
  });
  expect(ipc.providerCalls).toBe(4);expect(ipc.imageSends).toBe(0);expect(ipc.armed).toEqual([{runId:logged[1].run_id,tool:'save_split'}]);expect(ipc.ended).toEqual([logged[1].run_id]);
  for(const file of originals)expect(sha(fs.readFileSync(file.path))).toBe(file.sha256);
  expect(externalRequests).toEqual([]);expect(pageErrors).toEqual([]);
  await evidence.screenshot(page,'confirmed-real-split-success-log-and-completed-conversation');
  evidence.note('assistant_confirmed_split',{
    actual_browser_renderer_backend_stores:true,fake_provider_bridge:true,fake_beginRun_true_endRun_noop:true,main_run_header_attribution_tested:false,
    project_id:project.id,source_images:originals,initial_split:{train:16,val:2,test:2},final_split:counts,
    initial_split_sha256:initialSha,final_split_sha256:finalSha,declined_split_write_count:0,confirmed_split_write_count:splitWrites.length,
    persisted_session_write_log:logged,conversation_completed:true,ipc,provider_network_requests:externalRequests.length,page_errors:pageErrors,
    training_model_or_image_send:false,native_electron_qa:false,
  });
});

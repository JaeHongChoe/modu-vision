// Assistant provider clients against a local mock server on 127.0.0.1: each format's request shape (tools, tool results,
// images), the parsed tool calls, the model list, and every error mapped to a Korean reason and next action. Only a
// placeholder key is used and nothing leaves this computer.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),http=require('node:http'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,cache=new Map()){if(cache.has(file))return cache.get(file).exports;const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));cache.set(file,m);const original=m.require.bind(m);
 m.require=name=>{if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name)+'.ts';if(fs.existsSync(target))return load(target,cache);}return original(name);};
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);return m.exports;}
const providers=load(path.join(__dirname,'providers.ts'));
const KEY='test-placeholder-key-0002';

/** A scripted provider: `reply(request)` returns {status, body, headers, delay}; every request is recorded. */
async function mockServer(t,reply){
 const requests=[];
 const server=http.createServer((req,res)=>{let body='';req.on('data',chunk=>body+=chunk);req.on('end',()=>{
  const request={method:req.method,url:req.url,headers:req.headers,body:body?JSON.parse(body):null};requests.push(request);
  const answer=reply(request)||{status:200,body:{}};
  setTimeout(()=>{if(res.destroyed)return;res.writeHead(answer.status||200,{'content-type':'application/json',...(answer.headers||{})});res.end(typeof answer.body==='string'?answer.body:JSON.stringify(answer.body));},answer.delay||0);
 });});
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 t.after(()=>new Promise(resolve=>{server.closeAllConnections?.();server.close(resolve);}));
 return {base:`http://127.0.0.1:${server.address().port}`,requests};
}
const tools=[{name:'read_status',description:'상태 읽기',parameters:{type:'object',properties:{},additionalProperties:false}}];
const conversation=[{role:'user',text:'왜 막혔나요?'},{role:'assistant',text:'',toolCalls:[{id:'call_1',name:'read_status',arguments:{}}]},{role:'tool',toolCallId:'call_1',name:'read_status',content:'{"step":3}'},{role:'user',text:'다음은?'}];

test('Chat Completions: system first, function tools, tool results by tool_call_id, no max_tokens; tool calls parsed',async t=>{
 const server=await mockServer(t,()=>({body:{choices:[{finish_reason:'tool_calls',message:{role:'assistant',content:null,tool_calls:[{id:'call_2',type:'function',function:{name:'open_step',arguments:'{"step":3}'}}]}}]},headers:{'x-request-id':'req-chat-1'}}));
 const config={provider:'openai-compatible',baseUrl:server.base+'/v1',model:'mock-model',format:'chat',supportsImages:true};
 const result=await providers.complete({config,key:KEY,system:'SYSTEM',messages:conversation,tools,image:{mediaType:'image/png',base64:'iVBORw0KGgo='}});
 assert.deepEqual(result,{text:'',toolCalls:[{id:'call_2',name:'open_step',arguments:{step:3}}],stop:'tool',requestId:'req-chat-1'});
 const [request]=server.requests;
 assert.equal(request.url,'/v1/chat/completions');assert.equal(request.headers.authorization,`Bearer ${KEY}`);
 assert.equal(request.body.model,'mock-model');assert.ok(!('max_tokens' in request.body)&&!('max_completion_tokens' in request.body));
 assert.deepEqual(request.body.messages[0],{role:'system',content:'SYSTEM'});
 assert.deepEqual(request.body.messages[2],{role:'assistant',content:null,tool_calls:[{id:'call_1',type:'function',function:{name:'read_status',arguments:'{}'}}]});
 assert.deepEqual(request.body.messages[3],{role:'tool',tool_call_id:'call_1',content:'{"step":3}'});
 assert.deepEqual(request.body.messages[4].content[1],{type:'image_url',image_url:{url:'data:image/png;base64,iVBORw0KGgo='}},'the image rides on the last question only');
 assert.equal(typeof request.body.messages[1].content,'string');
 assert.deepEqual(request.body.tools,[{type:'function',function:tools[0]}]);
});

test('Responses: instructions, store:false, function_call items without ids, function_call_output by call_id',async t=>{
 const server=await mockServer(t,()=>({body:{status:'completed',output:[{type:'reasoning',id:'rs_1',summary:[]},{type:'message',content:[{type:'output_text',text:'3단계를 여세요.'}]},{type:'function_call',id:'fc_9',call_id:'call_9',name:'open_step',arguments:'{"step":3}'}]}}));
 const config={provider:'openai-compatible',baseUrl:server.base+'/v1',model:'mock-model',format:'responses',supportsImages:true};
 const result=await providers.complete({config,key:KEY,system:'SYSTEM',messages:conversation,tools,image:{mediaType:'image/jpeg',base64:'/9j/'}});
 assert.deepEqual(result.toolCalls,[{id:'call_9',name:'open_step',arguments:{step:3}}]);assert.equal(result.text,'3단계를 여세요.');assert.equal(result.stop,'tool');
 const {url,body}=server.requests[0];
 assert.equal(url,'/v1/responses');assert.equal(body.instructions,'SYSTEM');assert.equal(body.store,false);
 assert.deepEqual(body.input[1],{type:'function_call',call_id:'call_1',name:'read_status',arguments:'{}'});
 assert.deepEqual(body.input[2],{type:'function_call_output',call_id:'call_1',output:'{"step":3}'});
 assert.deepEqual(body.input[3].content[1],{type:'input_image',image_url:'data:image/jpeg;base64,/9j/'});
 assert.deepEqual(body.tools,[{type:'function',...tools[0]}]);
 assert.deepEqual(body.include,['reasoning.encrypted_content'],'reasoning comes back encrypted because the server keeps nothing');
 assert.deepEqual(result.replay.map(item=>item.type),['reasoning','message','function_call']);
 // The next call of the same question sends those output items back unchanged, reasoning next to its function call.
 const next=[...conversation.slice(0,3),{role:'assistant',text:result.text,toolCalls:result.toolCalls,replay:result.replay},{role:'tool',toolCallId:'call_9',name:'open_step',content:'열었습니다'}];
 await providers.complete({config,key:KEY,system:'SYSTEM',messages:next,tools});
 const second=server.requests[1].body.input;
 assert.deepEqual(second.slice(3,6),result.replay);assert.deepEqual(second[6],{type:'function_call_output',call_id:'call_9',output:'열었습니다'});
});

test('Anthropic Messages: key and version headers, input_schema tools, tool results first in one user turn, thinking blocks replayed unchanged',async t=>{
 const thinking={type:'thinking',thinking:'',signature:'sig-for-test'};
 const server=await mockServer(t,()=>({body:{content:[thinking,{type:'text',text:'확인하겠습니다.'},{type:'tool_use',id:'toolu_2',name:'read_status',input:{}}],stop_reason:'tool_use'},headers:{'request-id':'req_anthropic_1'}}));
 const config={provider:'anthropic',baseUrl:server.base,model:'mock-model',format:null,supportsImages:true};
 const replayed=[thinking,{type:'tool_use',id:'call_1',name:'read_status',input:{}}];
 const messages=conversation.map(message=>message.role==='assistant'?{...message,replay:replayed}:message);
 const result=await providers.complete({config,key:KEY,system:'SYSTEM',messages,tools,image:{mediaType:'image/webp',base64:'UklGRg=='}});
 assert.deepEqual(result.toolCalls,[{id:'toolu_2',name:'read_status',arguments:{}}]);assert.equal(result.stop,'tool');assert.equal(result.requestId,'req_anthropic_1');
 assert.deepEqual(result.replay,[thinking,{type:'text',text:'확인하겠습니다.'},{type:'tool_use',id:'toolu_2',name:'read_status',input:{}}]);
 const {url,headers,body}=server.requests[0];
 assert.equal(url,'/v1/messages');assert.equal(headers['x-api-key'],KEY);assert.equal(headers['anthropic-version'],'2023-06-01');assert.ok(!headers.authorization);
 assert.equal(body.system,'SYSTEM');assert.equal(body.max_tokens,2048);assert.deepEqual(body.tools,[{name:'read_status',description:'상태 읽기',input_schema:tools[0].parameters}]);
 assert.deepEqual(body.messages.map(turn=>turn.role),['user','assistant','user']);
 assert.deepEqual(body.messages[1].content,replayed,'the stored blocks are sent back as they came');
 assert.deepEqual(body.messages[2].content.slice(0,2),[{type:'tool_result',tool_use_id:'call_1',content:'{"step":3}'},{type:'text',text:'다음은?'}]);
 assert.deepEqual(body.messages[2].content[2],{type:'image',source:{type:'base64',media_type:'image/webp',data:'UklGRg=='}});
});

test('model lists: OpenAI-compatible /models and Anthropic /v1/models',async t=>{
 const server=await mockServer(t,request=>request.url.startsWith('/v1/models')?{body:{data:[{id:'claude-test',display_name:'Claude Test',type:'model'}],has_more:false}}:{body:{data:[{id:'gpt-test',object:'model'},{id:'local-test'}]}});
 assert.deepEqual(await providers.listModels({provider:'openai-compatible',baseUrl:server.base+'/openai',model:'',format:'chat'},KEY),[{id:'gpt-test',label:'gpt-test'},{id:'local-test',label:'local-test'}]);
 assert.deepEqual(await providers.listModels({provider:'anthropic',baseUrl:server.base+'/v1',model:'',format:null},KEY),[{id:'claude-test',label:'Claude Test'}]);
 assert.deepEqual(server.requests.map(request=>request.url),['/openai/models','/v1/models?limit=100']);
});

test('every HTTP failure maps to a Korean reason and next action without the key',async t=>{
 const cases=[[401,'bad_key',{type:'error',error:{type:'authentication_error',message:'invalid x-api-key'}}],[402,'billing',{}],[403,'forbidden',{error:{type:'permission_error'}}],
  [404,'not_found',{}],[400,'bad_request',{error:{code:'invalid_request_error'}}],[429,'rate_limited',{error:{type:'rate_limit_error'}}],
  [500,'server',{}],[503,'server','<html>unavailable</html>'],[529,'overloaded',{error:{type:'overloaded_error'}}]];
 let index=0;const server=await mockServer(t,()=>({status:cases[index][0],body:cases[index][2],headers:{'x-request-id':`req-${cases[index][0]}`}}));
 for(;index<cases.length;index++){
  const [status,code]=cases[index];
  await assert.rejects(providers.complete({config:{provider:'openai-compatible',baseUrl:server.base+'/v1',model:'m',format:'chat'},key:KEY,system:'s',messages:[{role:'user',text:'q'}],tools:[]}),error=>{
   assert.equal(error.detail.code,code,`HTTP ${status}`);assert.equal(error.detail.status,status);assert.equal(error.detail.requestId,`req-${status}`);
   assert.match(error.detail.reason,/[가-힣]/);assert.match(error.detail.action,/[가-힣]/);assert.ok(!JSON.stringify(error.detail).includes(KEY));
   assert.ok(!JSON.stringify(error.detail).includes('invalid x-api-key'),'the provider\'s own text is not passed on');return true;});
 }
});

test('network, timeout, cancellation, redirects and malformed bodies',async t=>{
 const closed=http.createServer();await new Promise(resolve=>closed.listen(0,'127.0.0.1',resolve));const port=closed.address().port;await new Promise(resolve=>closed.close(resolve));
 const request=(baseUrl,extra={})=>providers.complete({config:{provider:'openai-compatible',baseUrl,model:'m',format:'chat'},key:KEY,system:'s',messages:[{role:'user',text:'q'}],tools:[],...extra});
 await assert.rejects(request(`http://127.0.0.1:${port}/v1`),error=>error.detail.code==='network'&&/인터넷/.test(error.detail.action));
 const target=await mockServer(t,()=>({body:{choices:[{message:{content:'leaked'}}]}}));
 const server=await mockServer(t,req=>req.url.includes('slow')?{delay:400,body:{}}:req.url.includes('redirect')?{status:302,headers:{location:target.base+'/v1/chat/completions'},body:{}}:req.url.includes('broken')?{body:'not json'}:{body:{choices:[]}});
 await assert.rejects(request(server.base+'/slow',{timeoutMs:100}),error=>error.detail.code==='timeout');
 const controller=new AbortController();setTimeout(()=>controller.abort(),50);
 await assert.rejects(request(server.base+'/slow',{signal:controller.signal}),error=>error.detail.code==='cancelled');
 await assert.rejects(request(server.base+'/redirect'),error=>error.detail.code==='network');
 assert.equal(target.requests.length,0,'a redirect never carries the key to another address');
 await assert.rejects(request(server.base+'/broken'),error=>error.detail.code==='bad_response');
 await assert.rejects(request(server.base+'/empty'),error=>error.detail.code==='bad_response');
});

test('tool arguments that are not a JSON object are flagged, not guessed',()=>{
 const parsed=providers.parseChat({choices:[{message:{tool_calls:[{id:'a',type:'function',function:{name:'open_step',arguments:'{step:'}}]}}]});
 assert.deepEqual(parsed.toolCalls,[{id:'a',name:'open_step',arguments:{},argumentsError:true}]);
});

test('base URLs: HTTPS, or HTTP on this computer only',()=>{
 assert.equal(providers.validateBaseUrl('https://api.openai.com/v1/'),'https://api.openai.com/v1');
 assert.equal(providers.validateBaseUrl('http://localhost:11434/v1'),'http://localhost:11434/v1');
 for(const value of ['http://10.0.0.5/v1','http://api.openai.com/v1','https://a:b@x.example/v1','https://x.example/v1#k',''])assert.throws(()=>providers.validateBaseUrl(value));
 assert.equal(providers.providerLabel({provider:'openai-compatible',baseUrl:'https://api.openai.com/v1'}),'OpenAI');
 assert.equal(providers.providerLabel({provider:'openai-compatible',baseUrl:'http://127.0.0.1:11434/v1'}),'OpenAI 호환 서버(127.0.0.1:11434)');
});

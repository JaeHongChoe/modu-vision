'use strict';
const assert=require('node:assert/strict'),test=require('node:test'),fs=require('node:fs'),os=require('node:os'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto'),ts=require('typescript');
const {expect}=require('@playwright/test');
// Exercise the actual spec's observer with synthetic Request/Response events.
// No backend, DICOM decoder, browser, model or network is executed by this test.
const spec=path.resolve(__dirname,'../e2e/dicom-first-frame-thumbnail-owned-abort.spec.ts');
const source=ts.createSourceFile(spec,fs.readFileSync(spec,'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TS);
let arrow;const visit=node=>{if(ts.isVariableDeclaration(node)&&node.name.getText(source)==='capture')arrow=node.initializer.getText(source);ts.forEachChild(node,visit);};visit(source);assert.ok(arrow);
const compiled=ts.transpileModule('const capture='+arrow+';capture;',{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.CommonJS}}).outputText;
const origin='http://127.0.0.1:40001',route='/api/project/open',context={workspace_id:'workspace',project_id:'single',actor_id:'owner',mode:'local'};
const target={id:'single',name:'Single',project_dir:'/owned/single'},expected={project_dir:target.project_dir};
function fixture(t,{method='POST',requestRoute=route,png=false}={}){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'dicom-capture-'));t.after(()=>fs.rmSync(root,{recursive:true,force:true}));
 const frame={},handlers=new Map(),waiters=[];
 const page={mainFrame:()=>frame,on:(name,fn)=>{if(!handlers.has(name))handlers.set(name,new Set());handlers.get(name).add(fn);},off:(name,fn)=>handlers.get(name)?.delete(fn),waitForResponse:predicate=>new Promise(resolve=>waiters.push({predicate,resolve}))};
 const emit=(name,value)=>{for(const fn of handlers.get(name)||[])fn(value);if(name==='response')for(const waiter of waiters.splice(0))if(waiter.predicate(value))waiter.resolve(value);else waiters.push(waiter);};
 const requestContext=method==='GET'?context:{...context,project_id:'default'};
 const request=(body=expected)=>({frame:()=>frame,method:()=>method,url:()=>origin+requestRoute,postDataJSON:()=>body,headers:()=>({'x-vision-project':requestContext.project_id,'x-vision-context':JSON.stringify(requestContext)})});
 const pngBytes=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j3XcAAAAASUVORK5CYII=','base64');
 const bodyReads=[];
 const response=(q,status=200,body=target)=>({request:()=>q,status:()=>status,body:async()=>{bodyReads.push(q);return png?pngBytes:Buffer.from(JSON.stringify(body));},finished:async()=>null,allHeaders:async()=>({'x-vision-context':JSON.stringify(context)})});
 const captures=[],pending=new Set();const capture=vm.runInNewContext(compiled,{page,expect,within:async operation=>operation,performance,origin,w:{logs:root},e:{addFile(){},note(){}},captures,pending,fs,path,Buffer,hash:bytes=>crypto.createHash('sha256').update(bytes).digest('hex'),matches:(q,method,path)=>q.frame()===frame&&q.method()===method&&q.url()===origin+path});
 return {capture,emit,request,response,captures,handlers,pngBytes,bodyReads,scope:{project:target,context}};
}
test('exact action Request and full original body are joined and observers are removed',async t=>{const f=fixture(t),q=f.request();const result=await f.capture('single','POST',route,expected,f.scope,async()=>{f.emit('request',q);f.emit('response',f.response(q));});assert.equal(result.body.id,'single');assert.equal(result.same_original_Request_response_identity,true);assert.equal(result.own_request_observed_after_action_started,true);assert.ok([...f.handlers.values()].every(set=>set.size===0));});
test('a same-path unobserved success cannot stand in for the action Request',async t=>{const f=fixture(t),foreign=f.request(),own=f.request();await assert.rejects(f.capture('single','POST',route,expected,f.scope,async()=>{f.emit('response',f.response(foreign));f.emit('request',own);f.emit('response',f.response(own));}),/unobserved|original Request|identity/i);assert.equal(f.captures.length,0);});
test('a wrong project open during the action fails and is never ignored for a later success',async t=>{const f=fixture(t),foreign=f.request({project_dir:'/owned/default'}),own=f.request();await assert.rejects(f.capture('single','POST',route,expected,f.scope,async()=>{f.emit('request',foreign);f.emit('response',f.response(foreign));f.emit('request',own);f.emit('response',f.response(own));}));assert.equal(f.captures.length,0);});
test('the actual owning non-200 response remains a failure',async t=>{const f=fixture(t),q=f.request();await assert.rejects(f.capture('single','POST',route,expected,f.scope,async()=>{f.emit('request',q);f.emit('response',f.response(q,409,{detail:'conflict'}));}));assert.equal(f.captures.length,0);});
test('malformed JSON is refused by the tracked observer without throwing out of its callback',async t=>{
 const f=fixture(t),malformed={...f.request(),postDataJSON(){throw Error('malformed JSON request');}},own=f.request();let callbackError;
 await assert.rejects(f.capture('single','POST',route,expected,f.scope,async()=>{
  try{f.emit('request',malformed);}catch(error){callbackError=error;}
  f.emit('request',own);f.emit('response',f.response(own));
 }),/malformed JSON request/);
 assert.equal(callbackError,undefined);assert.equal(f.captures.length,0);assert.ok([...f.handlers.values()].every(set=>set.size===0));
});

const pngRoute='/api/dataset/dicom/views/original-frame1';
test('a PNG response from an earlier UI image cannot claim the explicit fetch before its own Request',async t=>{
 const f=fixture(t,{method:'GET',requestRoute:pngRoute,png:true}),prior=f.request(),own=f.request();
 f.emit('request',prior); // LabelingCanvas started this image before the new capture attached.
 const result=await f.capture('frame1','GET',pngRoute,null,f.scope,async()=>{
  f.emit('response',f.response(prior));f.emit('request',own);f.emit('response',f.response(own));
 },true);
 assert.equal(result.same_original_Request_response_identity,true);assert.equal(result.own_request_observed_after_action_started,true);
 assert.equal(result.original_request_observations.length,1);assert.equal(result.PNG,true);
 assert.equal(result.ignored_pre_action_PNG_responses.length,1);assert.equal(result.ignored_pre_action_PNG_responses[0].credited,false);
 assert.deepEqual(f.bodyReads,[own]);
 assert.deepEqual(fs.readFileSync(result.file),f.pngBytes);assert.ok([...f.handlers.values()].every(set=>set.size===0));
});
test('a late prior UI PNG response cannot replace the explicit fetch after its own Request was observed',async t=>{
 const f=fixture(t,{method:'GET',requestRoute:pngRoute,png:true}),prior=f.request(),own=f.request();
 f.emit('request',prior);
 const result=await f.capture('frame1','GET',pngRoute,null,f.scope,async()=>{
  f.emit('request',own);f.emit('response',f.response(prior));f.emit('response',f.response(own));
 },true);
 assert.equal(result.same_original_Request_response_identity,true);assert.equal(result.original_request_observations.length,1);
 assert.equal(result.ignored_pre_action_PNG_responses.length,1);assert.equal(result.ignored_pre_action_PNG_responses[0].credited,false);
 assert.deepEqual(f.bodyReads,[own]);
 assert.deepEqual(fs.readFileSync(result.file),f.pngBytes);
});
test('a second same-URL PNG Request observed during the action still refuses the capture',async t=>{
 const f=fixture(t,{method:'GET',requestRoute:pngRoute,png:true}),first=f.request(),duplicate=f.request();
 await assert.rejects(f.capture('frame1','GET',pngRoute,null,f.scope,async()=>{
  f.emit('request',first);f.emit('request',duplicate);f.emit('response',f.response(first));f.emit('response',f.response(duplicate));
 },true));
 assert.equal(f.captures.length,0);assert.ok([...f.handlers.values()].every(set=>set.size===0));
});
test('an earlier successful PNG cannot mask the owning fetch non-200 response',async t=>{
 const f=fixture(t,{method:'GET',requestRoute:pngRoute,png:true}),prior=f.request(),own=f.request();
 f.emit('request',prior);
 await assert.rejects(f.capture('frame1','GET',pngRoute,null,f.scope,async()=>{
  f.emit('response',f.response(prior));f.emit('request',own);f.emit('response',f.response(own,409));
 },true));
 assert.deepEqual(f.bodyReads,[]);assert.equal(f.captures.length,0);
});

const assert=require('node:assert/strict');
const fs=require('node:fs');const path=require('node:path');const Module=require('node:module');
const test=require('node:test');const ts=require('typescript');
function service(request){
  const file=path.resolve(__dirname,'captureIntake.ts');assert.ok(fs.existsSync(file),'Capture intake service missing');
  const loaded=new Module(file,module);loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);
  loaded.require=name=>{if(name==='./api')return {request};throw new Error(name);};
  loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
  return loaded.exports;
}
test('service OK remains prediction and unknown stays the review routing label',()=>{
  const {captureRoutingLabel}=service(()=>{});
  assert.equal(captureRoutingLabel('unknown'),'정답 미확인');
  assert.equal(captureRoutingLabel('duplicate'),'중복');assert.equal(captureRoutingLabel('failed'),'실패');
});
test('adoption names only selected reviewed candidates and does not switch source',async()=>{
  const calls=[];const {captureIntake}=service(async (path,options)=>{calls.push({path,body:JSON.parse(options.body)});return {activated:false};});
  const result=await captureIntake.adopt(['capture_one'],'Reviewer','Owned version');
  assert.equal(result.activated,false);assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/capture-intake/adopt');
  assert.deepEqual(calls[0].body,{candidate_ids:['capture_one'],actor:'Reviewer',name:'Owned version'});
});

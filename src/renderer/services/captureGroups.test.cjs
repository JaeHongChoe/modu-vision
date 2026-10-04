const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
test('capture policy save carries expected revision and reopens project-scoped groups',async()=>{
  const file=path.join(__dirname,'captureGroups.ts');assert.ok(fs.existsSync(file),'Production capture group API client missing');
  const loaded=new Module(file,module);loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);const calls=[];
  loaded.require=ref=>ref==='./api'?{request:async(url,options)=>{calls.push({url,body:options?JSON.parse(options.body):null});return {policy:null,groups:[]};}}:require(ref);
  loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);
  const record={revision:2,policy:{required_view_ids:['front','back'],timestamp_basis:'trigger_offset',max_skew_ms:20,deadline_ms:1000}};
  await loaded.exports.captureGroups.save(record,1);await loaded.exports.captureGroups.status();
  assert.deepEqual(calls,[{url:'/api/runtime-services/capture-groups/policy',body:{policy:record,expected_revision:1}},{url:'/api/runtime-services/capture-groups',body:null}]);
});

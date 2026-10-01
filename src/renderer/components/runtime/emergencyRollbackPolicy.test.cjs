const test=require('node:test');const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');const ts=require('typescript');const Module=require('node:module');
const file=path.join(__dirname,'emergencyRollbackPolicy.ts');const m=new Module(file,module);m.filename=file;m.paths=module.paths;m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,file);
const {emergencyAcknowledged}=m.exports;
const result={deployment_id:'new',emergency:{request_id:'request',event_id:'commit'}};
const state={target:{target_id:'cell'},active:{deployment_id:'new',release:{manifest_sha256:'a'.repeat(64)}},runtime:{status:'ready',manifest_sha256:'a'.repeat(64)},matches_active:true,emergency_rollback_events:[{target_id:'cell',request_id:'request',event_id:'commit',event:'committed',result_deployment_id:'new'}]};
test('acknowledgement requires target runtime and committed audit for this request',()=>{assert.equal(emergencyAcknowledged('cell',result,state),true);assert.equal(emergencyAcknowledged('another-cell',result,state),false);assert.equal(emergencyAcknowledged('cell',result,{...state,runtime:{status:'stopped'}}),false);assert.equal(emergencyAcknowledged('cell',result,{...state,emergency_rollback_events:[]}),false);assert.equal(emergencyAcknowledged('cell',{...result,emergency:{...result.emergency,request_id:'old-request'}},state),false);});

for(const hash of [undefined, '', 'not-a-hash', 'a'.repeat(63), 'g'.repeat(64)]) {
 test(`matching invalid manifest hashes never acknowledge: ${String(hash)}`,()=>{
  const invalid={...state,runtime:{status:'ready',manifest_sha256:hash},active:{deployment_id:'new',release:{manifest_sha256:hash}}};
  assert.equal(emergencyAcknowledged('cell',result,invalid),false);
 });
}
test('malformed receipt and readback shapes fail closed without throwing',()=>{
 for(const invalid of [null,undefined,{},[],{...state,target:null},{...state,runtime:null},{...state,active:{deployment_id:'new'}},{...state,emergency_rollback_events:{}},{...state,emergency_rollback_events:[null]}])assert.equal(emergencyAcknowledged('cell',result,invalid),false);
 for(const invalid of [null,undefined,{},[],{deployment_id:'new',emergency:null}])assert.equal(emergencyAcknowledged('cell',invalid,state),false);
 assert.equal(emergencyAcknowledged('cell',result,{...state,matches_active:'false'}),false);
});
test('different valid runtime and active manifest hashes never acknowledge',()=>{
 assert.equal(emergencyAcknowledged('cell',result,{...state,runtime:{status:'ready',manifest_sha256:'b'.repeat(64)}}),false);
});

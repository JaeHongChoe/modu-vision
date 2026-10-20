// Audit F-08 / A4: a model node says which threshold the model uses outside step 4 and offers it explicitly.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mocks){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));
 mod.require=ref=>ref in mocks?mocks[ref]:require(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return mod.exports;}
const {thresholdHintText}=load('ModelThresholdHint.tsx',{'../../services/api':{request:async()=>null}});
const calibrated={job_id:'j',threshold:0.91,source:'evaluation_calibration',calibrated:true,not_transferable_reason:null,model_threshold:0.5};
test('a step 4 calibration is named and offered when the node differs',()=>{
 const hint=thresholdHintText(calibrated,0.5);assert.equal(hint.offer,true);assert.match(hint.text,/평가\(4단계\)에서 정한 기준 점수 0\.91/);
 assert.equal(thresholdHintText(calibrated,0.91).offer,false,'nothing to offer when the node already uses it');
});
test('without a usable calibration the saved threshold is named with the reason',()=>{
 const hint=thresholdHintText({...calibrated,threshold:0.5,calibrated:false,source:'saved_model_threshold',not_transferable_reason:'checkpoint_binding_mismatch'},0.7);
 assert.match(hint.text,/모델 파일이 평가 때와 달라 모델 기본 기준 점수 0\.50/);assert.equal(hint.offer,true);
});
test('the flow node shows the hint and the change is an ordinary node edit',()=>{
 const studio=fs.readFileSync(path.join(__dirname,'FlowchartStudio.tsx'),'utf8');
 assert.match(studio,/<ModelThresholdHint jobId=\{selectedNode\.data\.model_job_id\}/);
 assert.match(studio,/onUse=\{value => updateNodeData\(selectedNode\.id, thresholdUpdate\(selectedNode\.data, value\)\)\}/);
});

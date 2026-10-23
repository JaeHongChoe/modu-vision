const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {completedSteps,stepsFromEvidence}=load('stepProgress.ts');
const base={hasData:false,images:0,unlabeled:0,trainingDone:false,evaluated:false,flowSaved:false};
test('nothing is done in an empty project',()=>{assert.deepEqual(completedSteps(base),[]);});
test('data, labels, training, evaluation and a saved flow each mark their own step',()=>{
 assert.deepEqual(completedSteps({...base,hasData:true,images:64,unlabeled:3}),[1],'images in, some unlabeled');
 assert.deepEqual(completedSteps({...base,hasData:true,images:64,unlabeled:0}),[1,2]);
 assert.deepEqual(completedSteps({...base,hasData:true,images:64,unlabeled:0,trainingDone:true,evaluated:true,flowSaved:true}),[1,2,3,4,5]);
});
test('labels are not done without images, and a folder without images is not data',()=>{
 assert.deepEqual(completedSteps({...base,hasData:true,images:0,unlabeled:0}),[]);
 assert.deepEqual(completedSteps({...base,hasData:false,images:10,unlabeled:0}),[]);
});
test('a loaded evaluation also marks training, since only a trained model can be evaluated',()=>{
 assert.deepEqual(completedSteps({...base,hasData:true,images:64,unlabeled:0,evaluated:true}),[1,2,3,4]);
 assert.deepEqual(completedSteps({...base,flowSaved:true}),[5],'a saved flow alone says nothing about training');
});
test('the saved evidence proves training, evaluation and an evaluated flow only for current rows of this task',()=>{
 const model={job_id:'m',task:'classification',scope_matches:true,checkpoint_state:'current',data_state:'current'};
 assert.deepEqual(stepsFromEvidence(null,'classification'),[]);
 assert.deepEqual(stepsFromEvidence({models:[model],model_evaluations:[{job_id:'m',state:'current'}],flows:[{state:'current',scope_matches:true}]},'classification'),[3,4,5]);
 assert.deepEqual(stepsFromEvidence({models:[{...model,data_state:'changed'}],model_evaluations:[{job_id:'m',state:'current'}],flows:[]},'classification'),[],'a model trained on older data proves nothing');
 assert.deepEqual(stepsFromEvidence({models:[model],model_evaluations:[{job_id:'other',state:'current'}],flows:[{state:'changed',scope_matches:true}]},'classification'),[3]);
 assert.deepEqual(stepsFromEvidence({models:[model],model_evaluations:[],flows:[]},'detection'),[],'another task\'s model does not count');
});
test('once an impact report exists, step 5 is the report\'s: a saved flow whose evaluation is stale loses its check (F-14)',()=>{
 const {mergeStepProgress}=load('stepProgress.ts');
 assert.deepEqual(mergeStepProgress([1,2,3,4,5],null),[1,2,3,4,5],'without a report the saved flow stands');
 assert.deepEqual(mergeStepProgress([1,2,3,4,5],[3,4]),[1,2,3,4],'the report says the flow needs a new evaluation');
 assert.deepEqual(mergeStepProgress([1,2],[3,4,5]),[1,2,3,4,5]);
});

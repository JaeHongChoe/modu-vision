const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const {completedSteps}=load('stepProgress.ts');
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

const assert=require('node:assert/strict');
const fs=require('node:fs');const Module=require('node:module');const path=require('node:path');const test=require('node:test');const ts=require('typescript');
const source=path.resolve(__dirname,'../src/renderer/components/training/rotatedFittingCoordinates.ts');
const mod=new Module(source,module);mod.filename=source;mod.paths=Module._nodeModulePaths(path.dirname(source));
mod._compile(ts.transpileModule(fs.readFileSync(source,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,source);
const {fittingPoint}=mod.exports;
test('native OBB click coordinates preserve centered letterbox and pillarbox geometry',()=>{
  assert.deepEqual(fittingPoint({left:100,top:50,width:800,height:400},[2000,1000],[500,250]),[1000,500]);
  assert.deepEqual(fittingPoint({left:0,top:0,width:800,height:400},[1000,1000],[400,200]),[500,500]);
  assert.equal(fittingPoint({left:0,top:0,width:800,height:400},[1000,1000],[100,200]),null);
  assert.deepEqual(fittingPoint({left:0,top:0,width:400,height:800},[1000,1000],[200,400]),[500,500]);
  assert.equal(fittingPoint({left:0,top:0,width:400,height:800},[1000,1000],[200,100]),null);
  assert.equal(fittingPoint({left:0,top:0,width:0,height:0},[1000,1000],[0,0]),null);
});
const scopeFile=path.resolve(__dirname,'../src/renderer/components/training/scopedTrainingJob.ts');
const scopeModule=new Module(scopeFile,module);scopeModule.filename=scopeFile;scopeModule.paths=Module._nodeModulePaths(path.dirname(scopeFile));
scopeModule._compile(ts.transpileModule(fs.readFileSync(scopeFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,scopeFile);
const {scopedTrainingJob}=scopeModule.exports;
test('completed jobs cannot fire a new project or labelset callback before reset effects',()=>{
  const job={status:'completed',task:'ocr',source_dataset_path:'/same/source',training_provenance:{labelset_id:'labels-v1'}};
  const snapshot={scope:'projectA',job};
  assert.equal(scopedTrainingJob(snapshot,'projectA','/same/source','labels-v1','ocr'),job);
  assert.equal(scopedTrainingJob(snapshot,'projectB','/same/source','labels-v1','ocr'),null);
  assert.equal(scopedTrainingJob(snapshot,'projectA','/new/source','labels-v1','ocr'),null);
  assert.equal(scopedTrainingJob(snapshot,'projectA','/same/source','labels-v2','ocr'),null);
  assert.equal(scopedTrainingJob(snapshot,'projectA','/same/source','labels-v1','defect_gan'),null);
});

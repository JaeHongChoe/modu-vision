const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mock){const name=path.resolve(__dirname,file),mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));const original=mod.require.bind(mod);mod.require=ref=>mock?.(ref)??original(ref);mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return mod.exports;}
test('optimization and explicit approval send independent calibration, holdout and revision contracts',async()=>{
  const calls=[];
  const {runtimeDeploymentApi}=load('../../services/runtimeDeploymentApi.ts',ref=>ref==='./api'?{api:{},request:async(url,options)=>{calls.push({url,body:JSON.parse(options.body)});return {};}}:undefined);
  const options={package_dir:'/project/exports/flow',source_dataset_path:'/original',precision:'int8',device:'CPU',cpu_threads:1,calibration_images:['/original/train.png'],validation_images:['/original/test.png']};
  await runtimeDeploymentApi.optimize(options);assert.deepEqual(calls[0].body,options);
  const approval={reviewer:'engineer',reason:'Actual holdout outputs reviewed',holdout_reviewed:true,maximum_absolute_drift:.02,approval_revision_ids:{job:'revision'}};
  await runtimeDeploymentApi.approve('a'.repeat(32),approval);assert.deepEqual(calls[1].body,approval);
  assert.match(calls[1].url,/optimization-jobs\/[a-f0-9]+\/approve$/);
});
function render(reviewed){
  const react=require('react');let index=0;
  const job={job_id:'a'.repeat(32),status:'completed',error:null,result:{package_path:'/project/exports/candidate',models:[{job_id:'model',task:'segmentation',precision:'int8',metrics:{max_absolute_error:.01,validation_image_count:2,reference_latency_mean_ms:8,latency_mean_ms:4}}]}};
  const injected={0:['CPU'],7:job,10:'engineer',11:'Actual holdout outputs reviewed',12:reviewed,14:{model:'revision'},16:{index:0,total:1,image_sha256:'a'.repeat(64),reference:{final_verdict:'NG',roi_count:0,crops:[]},candidate:{final_verdict:'NG',roi_count:0,crops:[]},comparison:{status:'passed',mismatched_fields:[]}}};
  const mockReact={...react,useState:initial=>react.useState(Object.hasOwn(injected,index)?injected[index++]:(index++,initial))};
  const {RuntimeOptimizationPanel}=load('RuntimeOptimizationPanel.tsx',ref=>ref==='react'?mockReact:ref==='../../services/api'?{api:{}}:ref==='../../services/runtimeDeploymentApi'?{runtimeDeploymentApi:{}}:ref==='../../stores/useProjectStore'?{useProjectStore:selector=>selector({projectDir:'/project'})}:undefined);
  return require('react-dom/server').renderToStaticMarkup(react.createElement(RuntimeOptimizationPanel,{packagePath:'/project/exports/source',sourceFolder:'/original',task:'segmentation'}));
}
test('runtime panel presents measured evidence and requires explicit holdout review',()=>{
  const markup=render(false);assert.match(markup,/0.0100/);assert.match(markup,/8.00 \/ 4.00/);assert.match(markup,/정밀도·Runtime 별도 승인/);
  const button=markup.match(/<button[^>]*>검토 후 새 승인 패키지 생성<\/button>/)[0];assert.match(button,/disabled/);
  assert.doesNotMatch(render(true).match(/<button[^>]*>검토 후 새 승인 패키지 생성<\/button>/)[0],/disabled=""/);
});

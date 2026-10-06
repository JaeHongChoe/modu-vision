const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const ts = require('typescript');
function load(name) {
  const filename=path.join(__dirname,name);
  const loaded=new Module(filename,module);loaded.filename=filename;loaded.paths=Module._nodeModulePaths(__dirname);
  const original=loaded.require.bind(loaded);
  loaded.require=(ref)=>ref.startsWith('./')?load(ref.slice(2)+'.ts'):original(ref);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
  return loaded.exports;
}
const graph=load('flowchartGraph.ts'), startup=load('flowchartStartup.ts');
const node=(id,node_type,extra={})=>({id,position:{x:0,y:0},data:{label:id,node_type,...extra}});
const pipeline=()=>({id:'geometry',name:'Geometry',execution_config:{max_workers:2,device_slots:1},nodes:[
  node('in','input'),node('rotate','preprocess',{params:{operation:'learned_rotation'},model_job_id:'rotation-job'}),
  node('fit','preprocess',{params:{operation:'fitted_roi'}}),node('seg','inspection',{task:'segmentation'}),
  node('measure','measurement',{params:{paths:[{id:'length',interpolation:'polyline',points:[[0,0],[3,4]]}],calibration:{unit:'mm',mm_per_pixel_x:0.1,mm_per_pixel_y:0.2,source_size:[32,32]}}}),
  node('decision','decision',{rule:'any_defect_is_ng'}),node('out','output')],
  edges:[['in','rotate'],['rotate','fit'],['fit','seg'],['seg','measure'],['measure','decision'],['decision','out']].map(([source,target],i)=>({id:'e'+i,source,target}))});
test('learned rotation and calibrated geometry are executable editor nodes',()=>{
  assert.equal(graph.validateFlowchartGraph(pipeline()),null);
  assert.deepEqual(startup.getFlowchartModelReferences(pipeline()),[{job_id:'rotation-job',task:'rotation'}]);
});
test('oriented detector references retain their family and refuse an axis detector automatic binding',()=>{
  const p={nodes:[node('crop','detection_crop',{task:'rotated_detection',model_job_id:'a'.repeat(32)})],edges:[]};
  assert.deepEqual(startup.getFlowchartModelReferences(p),[{job_id:'a'.repeat(32),task:'rotated_detection'}]);
  delete p.nodes[0].data.model_job_id;
  assert.equal(startup.singleModelAutoBinding(p,'detection','axis-detector'),null);
  delete p.nodes[0].data.task;
  assert.equal(startup.getFlowchartModelTask(p.nodes[0]),'detection');
});
test('measurement validation rejects malformed points, missing curve controls and nonfinite calibration',()=>{
  for(const patch of [{paths:[{id:'a',points:[[0,0],[NaN,1]]}]},{paths:[{id:'a',interpolation:'bezier',points:[[0,0],[1,1]]}]},{calibration:{unit:'mm',mm_per_pixel_x:Infinity,mm_per_pixel_y:1,source_size:[32,32]}}]){
    const p=pipeline();Object.assign(p.nodes[4].data.params,patch);
    assert.match(graph.validateFlowchartGraph(p),/measure/);
  }
});
test('bounded executor and Blob class bounds are validated before save',()=>{
  const p=pipeline();p.execution_config.max_workers=9;assert.match(graph.validateFlowchartGraph(p),/병렬/);
  const b=pipeline();b.nodes[4]=node('measure','blob_measure',{params:{rule_mode:'required_structure',class_rules:[{class_id:1,min_count:4,max_count:2}]}});
  assert.match(graph.validateFlowchartGraph(b),/measure/);
});

function renderCrop(crop) {
  const filename=path.join(__dirname,'CropDetailModal.tsx');const loaded=new Module(filename,module);
  loaded.filename=filename;loaded.paths=Module._nodeModulePaths(__dirname);const original=loaded.require.bind(loaded);
  loaded.require=ref=>ref==='../../stores/useFlowchartStore'?{useFlowchartStore:()=>({pipeline:null,executionResult:{crops:[crop]},setInspectedCrop:()=>{}}),isExecutionResultCurrent:()=>false}:original(ref);
  loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
  return require('react-dom/server').renderToStaticMarkup(require('react').createElement(loaded.exports.CropDetailModal,{crop,onClose:()=>{}}));
}
test('saved result presents original and corrected OCR plus source measurement units',()=>{
  const markup=renderCrop({roi_id:'r',label:'text',bbox:[0,0,32,32],defect_score:0,verdict:'OK',crop_thumbnail:'data:image/png;base64,',flaw_type:'OCR',original_text:'A0',corrected_text:'AO',correction_applied:true,measurements:[{id:'curve',length:12.25,length_px:122.5,unit:'mm',coordinate_space:'original_image',source_size:[32,32],measurement_source:'source_path',verdict:'OK'}],blob_measurements:[{class_id:2,class_name:'structure',count:3,area_px:72,mean_grayscale:110,verdict:'OK',violations:[]}]});
  assert.match(markup,/원본 인식/);assert.match(markup,/A0/);assert.match(markup,/AO/);assert.match(markup,/12.25/);assert.match(markup,/mm/);assert.match(markup,/structure/);assert.match(markup,/110/);
});

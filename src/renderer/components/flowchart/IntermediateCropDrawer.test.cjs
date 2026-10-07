const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
function render(master,crop){
 const state={pipeline:{nodes:[]},executionResult:{annotated_image:master,final_verdict:'REVIEW',is_ok:false,roi_count:1,crops:[{roi_id:'owned',label:'Owned ROI',flaw_type:'unknown',defect_score:.1,bbox:[0,0,8,8],verdict:'REVIEW',crop_thumbnail:crop}],execution_steps:[]},setInspectedCrop(){}};
 const react={useState:v=>[v,()=>{}],useMemo:f=>f()};
 const jsx=(type,props)=>({type,props});
 function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);m.require=name=>name==='react'?{__esModule:true,default:react,...react}:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='lucide-react'?{}:name==='../../stores/useFlowchartStore'?{useFlowchartStore:()=>state}:name==='../inference/inspectionImage'?load(path.resolve(__dirname,'../inference/inspectionImage.ts')):name==='../common/evidenceViewer'?load(path.resolve(__dirname,'../common/evidenceViewer.ts')):req(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);return m.exports;}
 return nodes(load(path.resolve(__dirname,'IntermediateCropDrawer.tsx')).IntermediateCropDrawer({}));
}
const inline='data:image/png;base64,AA==';
test('actual flow result keeps exact inline master and ROI snapshots',()=>{assert.deepEqual(render(inline,inline).filter(n=>n.type==='img').map(n=>n.props.src),[inline,inline]);});
for(const value of ['https://foreign.invalid/image.png','//foreign.invalid/image.png','file:///private/image.png','data:image/svg+xml,<svg/>'])test('actual flow result rejects stored raster address '+value,()=>{
 const tree=render(value,value);assert.equal(tree.filter(n=>n.type==='img').length,0);assert.match(JSON.stringify(tree),/결과 이미지를 읽을 수 없/);assert.match(JSON.stringify(tree),/ROI 이미지를 읽을 수 없/);
});

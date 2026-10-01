const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function render(current, scoreSpec){
 const crop={roi_id:'seg:1',source_node_id:'seg',label:'NG',bbox:[0,0,10,10],defect_score:.4,
   verdict:'NG',crop_thumbnail:'fixture.png',flaw_type:'scratch',defect_area_px:12,score_spec:scoreSpec};
 const pipeline={nodes:[{id:'seg',data:{node_type:'inspection',task:'segmentation',threshold:.8,params:{min_defect_area_px:999}}}],edges:[]};
 const state={pipeline,executionResult:{crops:[crop]},executionIdentity:{semantic_key:'saved'},setInspectedCrop(){}};
 const jsx=(type,props)=>({type,props:props||{}}),empty=()=>null;
 const mocks={react:{useMemo:fn=>fn(),useEffect(){}},'react/jsx-runtime':{jsx,jsxs:jsx},
   'lucide-react':new Proxy({},{get:()=>empty}),
   '../../stores/useFlowchartStore':{useFlowchartStore:()=>state,isExecutionResultCurrent:()=>current}};
 const filename=path.join(__dirname,'CropDetailModal.tsx'),loaded=new Module(filename,module);
 loaded.filename=filename;loaded.paths=Module._nodeModulePaths(__dirname);loaded.require=name=>mocks[name];
 loaded._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);
 return loaded.exports.CropDetailModal({crop,onClose(){}});
}
function text(node){if(node===null||node===undefined||typeof node==='boolean')return '';if(typeof node!=='object')return String(node);
 return [node.props.children].flat(Infinity).map(text).join('');}
test('stale crop modal never compares old measurements with current node rules',()=>{
 const rendered=text(render(false));
 assert.match(rendered,/이전 규칙의 실행 결과/);assert.match(rendered,/임계 기준: 확인 불가/);
 assert.ok(!rendered.includes('80.0%'),'current probability threshold must not reinterpret historical scores');
 assert.ok(!rendered.includes('999'),'current minimum area must not reinterpret historical measurements');
});
test('stale crop retains its explicitly recorded score threshold',()=>{
 const rendered=text(render(false,{domain:'probability',unit:'probability',threshold:.2}));
 assert.match(rendered,/이전 규칙의 실행 결과/);assert.match(rendered,/임계 기준: 20.0%/);
 assert.ok(!rendered.includes('999'));
});
test('current crop can explain its current node threshold and minimum defect area',()=>{
 const rendered=text(render(true));assert.match(rendered,/임계 기준: 80.0%/);assert.match(rendered,/12 \/ 999 px/);
 assert.ok(!rendered.includes('이전 규칙의 실행 결과'));
});

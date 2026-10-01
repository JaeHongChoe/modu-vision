const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');

// Render the real editor structure without executing its network effects. The
// regression is its flex/overflow contract, not graph or training behavior.
function editor(){
 const pipeline={id:'layout',name:'Layout',nodes:[],edges:[]};
 const store=value=>{const fn=selector=>selector?selector(value):value;fn.getState=()=>value;return fn;};
 const flow=store({pipeline,pipelineDirty:false,pipelineIsDraft:false,executionChoiceOverride:'local_cpu',errorMessage:'Visible error',canUndo:false,canRedo:false});
 const jsx=(type,props)=>({type,props:props||{}}),component=()=>null;
 const mocks={'react':{useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:initial=>({current:initial}),useEffect(){},useLayoutEffect(){}},'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({}, {get:()=>component}),
  '../../stores/useFlowchartStore':{useFlowchartStore:flow},'../../stores/useDatasetStore':{useDatasetStore:store({folderPath:'/source',datasetKey:'/source\0segmentation',hasSelectedFolder:true})},'../../stores/useProjectStore':{useProjectStore:store({language:'ko',task:'segmentation'})},'../../stores/useEvaluationStore':{useEvaluationStore:store({})},'../../stores/useTrainingStore':{useTrainingStore:store({})},'../../stores/useComputeStore':{useComputeStore:store({profiles:[],selectedProfileId:null,isLoaded:true})},'../../services/api':{},
  './flowchartViewport':{computeFlowchartViewport:()=>({scale:1,contentWidth:800,contentHeight:240,offsetX:0,offsetY:0,layerWidth:800,layerHeight:240}),readableFlowScale:value=>value},'./flowchartStartup':{getFlowchartModelTask:()=>null},'./flowchartGraph':{validateFlowchartGraph:()=>null,locateFlowIssue:()=>null},'./flowHandoff':{flowRecipeLabel:()=> '검사'},'./flowExecution':{}};
 const name=path.join(__dirname,'FlowchartStudio.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>mocks[ref]??new Proxy({}, {get:()=>component});m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports.FlowchartStudio();
}
const children=node=>[node.props.children].flat(Infinity).filter(value=>value&&typeof value==='object');
const has=(node,token)=>(node.props.className||'').split(/\s+/).includes(token);
test('expanded dock scrolls below a nonzero canvas instead of shrinking the graph away',()=>{
 const tree=editor();assert.ok(has(tree,'overflow-y-auto'),'editor allows vertical scrolling when the dock consumes available space');
 const row=children(tree).find(node=>children(node).some(child=>child.props['aria-label']==='검사 노드 팔레트'));assert.ok(row,'graph row is present');assert.ok(has(row,'min-h-[240px]'),'graph retains at least 240 pixels');assert.ok(has(row,'shrink-0'),'dock cannot compress graph below its minimum');
});
test('toolbar, resource, state, input and error rows retain natural height without overlapping',()=>{
 const rows=children(editor()).filter(node=>typeof node.type==='string'&&node.type==='div'&&has(node,'border-b'));
 assert.ok(rows.length>=5,'toolbar/resources/version/input/error regions are rendered');for(const row of rows)assert.ok(has(row,'shrink-0'),`fixed region can shrink: ${row.props.className}`);
});

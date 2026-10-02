const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');

// Render the real editor structure without executing its network effects. The
// regression is its flex/overflow contract, not graph or training behavior.
function editor(){
 const previousStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 const values=new Map();
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{
  getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key),
 }});
 try{
 const pipeline={id:'layout',name:'Layout',nodes:[],edges:[]};
 const store=value=>{const fn=selector=>selector?selector(value):value;fn.getState=()=>value;return fn;};
 const flow=store({pipeline,pipelineDirty:false,pipelineIsDraft:false,executionChoiceOverride:'local_cpu',errorMessage:'Visible error',canUndo:false,canRedo:false});
 const jsx=(type,props)=>({type,props:props||{}}),component=()=>null;
 const mocks={'react':{useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:initial=>({current:initial}),useEffect(){},useLayoutEffect(){}},'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({}, {get:()=>component}),
  '../../stores/useFlowchartStore':{useFlowchartStore:flow,flowSemanticKey:()=>'',isExecutionResultCurrent:()=>true},'../../stores/useDatasetStore':{useDatasetStore:store({folderPath:'/source',datasetKey:'/source\0segmentation',hasSelectedFolder:true})},'../../stores/useProjectStore':{useProjectStore:store({language:'ko',task:'segmentation'})},'../../stores/useEvaluationStore':{useEvaluationStore:store({})},'../../stores/useTrainingStore':{useTrainingStore:store({})},'../../stores/useComputeStore':{useComputeStore:store({profiles:[],selectedProfileId:null,isLoaded:true})},'../../services/api':{getProjectContext:()=>null,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=> 'local'},
  './FlowEditorWorkspace':{FlowEditorWorkspace:component,flowWorkspaceIdentity:()=>({exactSaved:false,evaluationCurrent:false,approval:'미조회',models:[],revision:0,draft:''})},'./flowRecipes':{FLOW_RECIPES:[{id:'single',title:'단일 모델 검사',description:'원본 → 모델'}],createFlowRecipe:()=>null},
  './flowchartViewport':{computeFlowchartViewport:()=>({scale:1,contentWidth:800,contentHeight:240,offsetX:0,offsetY:0,layerWidth:800,layerHeight:240}),readableFlowScale:value=>value},'./flowchartStartup':{getFlowchartModelTask:()=>null},'./flowchartGraph':{validateFlowchartGraph:()=>null,locateFlowIssue:()=>null},'./flowHandoff':{flowRecipeLabel:()=> '검사'},'./flowExecution':{}};
 const name=path.join(__dirname,'FlowchartStudio.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>mocks[ref]??new Proxy({}, {get:()=>component});m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports.FlowchartStudio();
 }finally{
  if(previousStorage)Object.defineProperty(globalThis,'localStorage',previousStorage);
  else delete globalThis.localStorage;
 }
}
const children=node=>[node.props.children].flat(Infinity).filter(value=>value&&typeof value==='object');
const has=(node,token)=>(node.props.className||'').split(/\s+/).includes(token);
test('expanded dock scrolls below a nonzero canvas instead of shrinking the graph away',()=>{
 const tree=editor();assert.ok(has(tree,'overflow-y-auto'),'editor allows vertical scrolling when the dock consumes available space');
 const panel=children(tree).find(node=>node.props.id==='flow-panel-edit');assert.ok(panel,'the edit area is a direct child of the scroll column');
 for(const token of ['flex','flex-col','flex-1','shrink-0','min-h-[240px]','overflow-hidden'])assert.ok(has(panel,token),`the shown edit area takes the free height, never less than its canvas, and its palette scrolls inside (${token})`);
 assert.ok(!has(panel,'min-h-0'),'min-h-0 would let the canvas row spill under the panels below');
 const row=children(panel).find(node=>children(node).some(child=>child.props['aria-label']==='검사 노드 팔레트'));assert.ok(row,'graph row is a direct child of the edit area');
 assert.ok(has(row,'flex-1'),'graph row grows into the available height');assert.ok(has(row,'min-h-[240px]'),'graph retains at least 240 pixels');assert.ok(has(row,'shrink-0'),'dock cannot compress graph below its minimum');
});
test('toolbar, resource, state, input and error rows retain natural height without overlapping',()=>{
 const tree=editor();const rows=[...children(tree),...children(tree).filter(node=>node.type==='div'&&node.props.hidden!==undefined).flatMap(children)].filter(node=>typeof node.type==='string'&&node.type==='div'&&has(node,'border-b'));
 assert.ok(rows.length>=5,'toolbar/resources/version/input/error regions are rendered');for(const row of rows)assert.ok(has(row,'shrink-0'),`fixed region can shrink: ${row.props.className}`);
});

const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function preparationPolicy(){const file=path.join(__dirname,'flowPreparation.ts'),loaded=new Module(file,module);loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return loaded.exports;}

// Render the real editor structure without executing its network effects. The
// regression is its flex/overflow contract, not graph or training behavior.
function editor(project={},flowState={}){
 const previousStorage=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 const values=new Map();
 Object.defineProperty(globalThis,'localStorage',{configurable:true,value:{
  getItem:key=>values.get(key)??null,setItem:(key,value)=>values.set(key,String(value)),removeItem:key=>values.delete(key),
 }});
 try{
 const pipeline={id:'layout',name:'Layout',nodes:[],edges:[]};
 const store=value=>{const fn=selector=>selector?selector(value):value;fn.getState=()=>value;return fn;};
 const flow=store({pipeline,pipelineDirty:false,pipelineIsDraft:false,executionChoiceOverride:'local_cpu',errorMessage:'Visible error',canUndo:false,canRedo:false,...flowState});
 const jsx=(type,props)=>({type,props:props||{}}),component=()=>null;
 const mocks={'react':{useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:initial=>({current:initial}),useEffect(){},useLayoutEffect(){}},'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({}, {get:()=>component}),
  '../../stores/useFlowchartStore':{useFlowchartStore:flow,flowSemanticKey:()=>'',isExecutionResultCurrent:()=>true},'../../stores/useDatasetStore':{useDatasetStore:store({folderPath:'/source',datasetKey:'/source\0segmentation',hasSelectedFolder:true})},'../../stores/useProjectStore':{useProjectStore:store({language:'ko',task:'segmentation',...project})},'../../stores/useEvaluationStore':{useEvaluationStore:store({})},'../../stores/useTrainingStore':{useTrainingStore:store({})},'../../stores/useComputeStore':{useComputeStore:store({profiles:[],selectedProfileId:null,isLoaded:true})},'../../services/api':{getProjectContext:()=>null,getProjectContextGeneration:()=>0,getApiPersistenceIdentity:()=> 'local'},
  './FlowEditorWorkspace':{FlowEditorWorkspace:component,flowWorkspaceIdentity:()=>({exactSaved:false,evaluationCurrent:false,approval:'미조회',models:[],revision:0,draft:''})},'./flowRecipes':{FLOW_RECIPES:[{id:'single',title:'단일 모델 검사',description:'원본 → 모델'}],createFlowRecipe:()=>null},
  './flowchartViewport':{computeFlowchartViewport:()=>({scale:1,contentWidth:800,contentHeight:240,offsetX:0,offsetY:0,layerWidth:800,layerHeight:240}),readableFlowScale:value=>value},'./flowchartStartup':{getFlowchartModelTask:()=>null},'./flowchartGraph':{nodeClassChoices:()=>[],validateFlowchartGraph:()=>null,locateFlowIssue:()=>null,flowIssuesByTarget:()=>({nodes:new Map(),edges:new Map()})},'./flowHandoff':{flowRecipeLabel:()=> '검사'},'./flowExecution':{}};
 const name=path.join(__dirname,'FlowchartStudio.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref==='./flowPreparation'?preparationPolicy():mocks[ref]??new Proxy({}, {get:()=>component});m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports.FlowchartStudio();
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
test('recipes and example templates stay closed while the flow is still opening (its model check and binding)',()=>{
 const tree=editor();
 const all=node=>[node,...children(node).flatMap(all)];
 const recipes=all(tree).find(node=>node.props['aria-label']==='목적 레시피');assert.ok(recipes,'the recipe list is rendered');
 const buttons=all(recipes).filter(node=>node.type==='button');assert.ok(buttons.length>0);
 // The first render is before the opening check finished: a recipe adopted now would be refused when the check binds.
 for(const button of buttons)assert.equal(button.props.disabled,true,'a recipe opened during the check is refused at adoption');
 const templates=all(tree).filter(node=>node.type==='button'&&String(node.props.onClick).includes('handleExampleTemplate'));
 assert.ok(templates.length===2&&templates.every(button=>button.props.disabled===true),'example templates too');
 const menu=all(tree).find(node=>node.type==='details'&&all(node).some(child=>child.type==='summary'&&String(child.props.children).includes('새 플로우')));
 assert.ok(menu,'the new-flow menu is rendered');const items=all(menu).filter(node=>node.type==='button');
 assert.ok(items.length>=7&&items.every(button=>button.props.disabled===true),`every new-flow item is held (${items.length})`);
});

test('the simple view folds the flow information, the image bar, node search and the node palette; other views keep them open',()=>{
 const all=node=>[node,...children(node).flatMap(all)];
 const text=node=>[node.props?.children].flat(Infinity).map(value=>typeof value==='string'?value:value&&typeof value==='object'?text(value):'').join('');
 const simple=all(editor({viewMode:'simple'}));
 const workspace=simple.find(node=>node.props&&'identity' in node.props&&'compact' in node.props);
 assert.equal(workspace?.props.compact,true,'the tab header keeps its identity lines behind one line');
 const summaries=simple.filter(node=>node.type==='summary').map(text);
 assert.ok(summaries.some(value=>value.includes('저장 버전·변경 기록·새 플로우·검사 이미지·확대')),'flow information and the image bar share one line');
 assert.ok(summaries.some(value=>value.includes('노드 찾기·전체 보기')),'node search and the minimap wait behind one line');
 const fold=simple.find(node=>node.type==='details'&&text(node).includes('저장본 불러오기'));
 assert.ok(fold&&text(fold).includes('검사 대상'),'the image bar moves into the flow information fold on the edit tab');
 const palette=simple.find(node=>node.props?.['aria-label']==='검사 노드 팔레트');
 assert.equal(palette.props.hidden,true,'the palette starts closed');
 const toggle=simple.find(node=>node.props?.['aria-controls']==='flow-node-palette');
 assert.equal(toggle.props['aria-expanded'],false);assert.match(text(toggle),/노드 추가 · 레시피/);
 const full=all(editor({viewMode:'full'}));
 assert.ok(!full.some(node=>node.props?.['aria-controls']==='flow-node-palette'),'모두 보기 has no palette toggle');
 assert.equal(full.find(node=>node.props?.['aria-label']==='검사 노드 팔레트').props.hidden,false);
 assert.ok(!full.some(node=>node.type==='summary'&&text(node).includes('노드 찾기·전체 보기')));
});

test('node add buttons are disabled while the flow is still opening, so a click is never silently ignored',()=>{
 const all=node=>[node,...children(node).flatMap(all)];
 const text=node=>[node.props?.children].flat(Infinity).map(value=>typeof value==='string'?value:value&&typeof value==='object'?text(value):'').join('');
 const tree=all(editor());
 const palette=tree.find(node=>node.props?.['aria-label']==='검사 노드 팔레트');
 const paletteButtons=all(palette).filter(node=>node.type==='button'&&!node.props['aria-describedby']);
 assert.ok(paletteButtons.length>=9,'every node type in the palette');
 assert.ok(paletteButtons.every(button=>button.props.disabled===true),'palette node buttons wait for the flow to open');
 const quick=tree.filter(node=>node.type==='button'&&/패치 분할 추가|길이·면적 측정|Blob 측정|결과 집계|판정 출력/.test(text(node)));
 assert.ok(quick.length>=4,'quick-add row buttons are rendered');
 assert.ok(quick.every(button=>button.props.disabled===true),'quick-add buttons wait for the flow to open');
});

test('a draft save keeps undo, redo and flow layout usable, so a click during the autosave is never silently dropped',()=>{
 const all=node=>[node,...children(node).flatMap(all)];
 const text=node=>[node.props?.children].flat(Infinity).map(value=>typeof value==='string'?value:value&&typeof value==='object'?text(value):'').join('');
 const controls=tree=>{const nodes=all(tree);return {
  undo:nodes.find(node=>node.type==='button'&&node.props['aria-label']==='플로우 실행 취소'),
  redo:nodes.find(node=>node.type==='button'&&node.props['aria-label']==='플로우 다시 실행'),
  layout:nodes.find(node=>node.type==='button'&&text(node).includes('흐름 정렬')),
 };};
 const draft=controls(editor({},{canUndo:true,canRedo:true,isSaving:true,isDraftSaving:true}));
 for(const [name,button] of Object.entries(draft)){assert.ok(button,`${name} is rendered`);assert.equal(button.props.disabled,false,`${name} stays usable while a draft save runs`);}
 const version=controls(editor({},{canUndo:true,canRedo:true,isSaving:true,isDraftSaving:false}));
 for(const [name,button] of Object.entries(version))assert.equal(button.props.disabled,true,`${name} waits for a version save`);
});

test('simple Step 5 starts with preparation instead of graph editing; full view retains its editor',()=>{
 const all=node=>[node,...children(node).flatMap(all)];
 const simple=editor({viewMode:'simple'}),nodes=all(simple);
 const preparation=nodes.find(node=>node.props?.next&&node.props?.onPrepare&&node.props?.onAdvanced);
 assert.ok(preparation,'simple view offers a guarded preparation surface');
 assert.equal(preparation.props.area,'edit');assert.equal(preparation.props.loading,true);
 assert.equal(nodes.find(node=>node.props.id==='flow-panel-edit').props.hidden,true,'ports and canvas are not the default');
 const full=all(editor({viewMode:'full'}));
 assert.ok(!full.some(node=>node.props?.next&&node.props?.onPrepare));
 assert.equal(full.find(node=>node.props.id==='flow-panel-edit').props.hidden,false);
});

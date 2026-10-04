const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
test('saved join policy reopens and attaches exact policy to current editable flow',async()=>{
  const file=path.join(__dirname,'CaptureGroupsPanel.tsx');assert.ok(fs.existsSync(file),'Capture group control panel missing');
  let cursor=0;const slots=[],effects=[];const saved={revision:4,policy:{required_view_ids:['back','front'],timestamp_basis:'trigger_offset',max_skew_ms:20,deadline_ms:1000,late_window_ms:1000,completeness_policy:'all_required'}};
  const flow={pipeline:{id:'flow',nodes:[],edges:[]},replacePipeline:next=>flow.pipeline=next};
  const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>slots[i]=typeof value==='function'?value(slots[i]):value];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((value,j)=>value!==slots[i][j])){slots[i]=deps;effects.push(fn);}}};
  const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat()});
  const loaded=new Module(file,module);loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);
  loaded.require=ref=>({react,'react/jsx-runtime':{jsx,jsxs:jsx},'../../services/captureGroups':{captureGroups:{status:async()=>({policy:saved,groups:[{part_id:'A',trigger_id:'trigger',state:'EXPIRED',verdict:'REVIEW',missing_view_ids:['back'],recipe_sha256:'a'.repeat(64)}]})}},'../../stores/useFlowchartStore':{useFlowchartStore:Object.assign(selector=>selector(flow),{getState:()=>flow})}}[ref]||require(ref));
  loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
  const render=()=>{cursor=0;const tree=loaded.exports.CaptureGroupsPanel({scopeKey:'project-a'});effects.splice(0).forEach(fn=>fn());return tree;};
  const nodes=tree=>Array.isArray(tree)?tree.flatMap(nodes):tree&&typeof tree==='object'?[tree,...(tree.children||[]).flatMap(nodes)]:[];
  render();await new Promise(setImmediate);const tree=render();
  assert.equal(nodes(tree).find(node=>node.props['aria-label']==='필수 view ID').props.value,'back, front');
  assert.ok(nodes(tree).some(node=>node.children.includes('A / trigger · EXPIRED · REVIEW')));
  nodes(tree).find(node=>node.props['aria-label']==='편집 플로우에 수집 정책 연결').props.onClick();
  assert.deepEqual(flow.pipeline.capture_group_policy,saved);
});

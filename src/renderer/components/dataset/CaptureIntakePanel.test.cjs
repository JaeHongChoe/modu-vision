const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
test('legacy captures and drift reopen when optional authenticated sampling is unavailable',async()=>{
  let cursor=0;const slots=[],effects=[];
  const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>slots[i]=typeof value==='function'?value(slots[i]):value];},
    useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
    useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((value,j)=>value!==slots[i][j])){slots[i]=deps;effects.push(fn);}}};
  const jsx=(type,props)=>({type,props:props||{},children:[props?.children].flat().filter(value=>value!==null&&value!==undefined&&value!==false)});
  const file=path.join(__dirname,'CaptureIntakePanel.tsx'),loaded=new Module(file,module);
  loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);
  loaded.require=ref=>({react,'react/jsx-runtime':{jsx,jsxs:jsx},'../../services/datasetWorkflow':{workflowError:error=>error.message},
    '../../services/captureIntake':{captureRoutingLabel:()=>'',captureIntake:{list:async()=>({candidates:[]}),versions:async()=>({versions:[]}),
      driftReferences:async()=>({references:[{reference_id:'ref',name:'Legacy drift reference'}]}),samplingStatus:async()=>{throw new Error('Authentication required for sampling policy');}}}}[ref]||require(ref));
  loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
  const render=()=>{cursor=0;const tree=loaded.exports.CaptureIntakePanel({sourceDatasetPath:'/legacy'});effects.splice(0).forEach(effect=>effect());return tree;};
  const text=tree=>Array.isArray(tree)?tree.map(text).join(''):typeof tree==='string'||typeof tree==='number'?String(tree):tree?.children?.map(text).join('')||'';
  const tree=render();tree.children[0].props.onClick();render();await new Promise(setImmediate);
  assert.match(text(render()),/Legacy drift reference/);
  assert.match(text(render()),/Authentication required for sampling policy/,'optional policy error stays visible');
});

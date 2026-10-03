const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mocks={}){const name=path.resolve(__dirname,file);if(!fs.existsSync(name))return {};const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=ref=>mocks[ref]??req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
function harness(){let cursor=0;const slots=[],effects=[];const react={useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});}}};const jsx=(type,props)=>({type,props:props||{}});return{react,mocks:{react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':{}},render:fn=>{cursor=0;const tree=fn();effects.splice(0).forEach(fn=>fn());return tree;}};}
function nodes(tree){const found=[];function visit(v){if(Array.isArray(v))v.forEach(visit);else if(v&&typeof v==='object'){found.push(v);visit(v.props?.children);}}visit(tree);return found;}
const store=state=>{const fn=selector=>selector?selector(state):state;fn.getState=()=>state;return fn;};
function fixture(){
 const h=harness(),Browser=()=>null;
 const m=load('FlowWorkspacePanel.tsx',{...h.mocks,
  '../../services/api':{getApiPersistenceIdentity:()=> 'local',getProjectContextGeneration:()=>0},
  '../../stores/useProjectStore':{useProjectStore:store({project:null,task:'segmentation'})},
  '../../stores/useDatasetStore':{useDatasetStore:store({folderPath:'/source'})},
  '../../stores/useComputeStore':{useComputeStore:store({transportRevision:0,selectedProfileId:null})},
  '../../stores/useFlowchartStore':{useFlowchartStore:store({pipeline:null,isRunning:false})},
  './flowWorkspace':{},'./modelFlowHandoff':{},'./flowTestSet':load('flowTestSet.ts'),
  '../common/ImageLibraryBrowser':{ImageLibraryBrowser:Browser}});
 const render=()=>h.render(()=>m.FlowWorkspacePanel({versions:[],models:[],onOpenImage(){},area:'evaluate'}));
 render();return {render,browser:()=>nodes(render()).find(row=>row.type===Browser)};
}
const image=(id,valid)=>({image_uuid:id,sha256:'a'.repeat(64),relative_path:`ok/${id}.png`,file_name:`${id}.png`,file_path:`/source/ok/${id}.png`,valid});
test('fixed test-set browser starts with valid images',()=>{
 const f=fixture();assert.deepEqual(f.browser().props.initialFilters,{state:'valid'});
});
test('fixed test-set rejects an invalid pick even when browsing invalid images',()=>{
 const f=fixture();f.browser().props.onPick(image('good',true));
 f.browser().props.onPick(image('broken',false));
 assert.deepEqual([...f.browser().props.selectedIds],['good'],'invalid image must not enter comparison selection');
 assert.ok(nodes(f.render()).some(row=>row.props.role==='status'&&JSON.stringify(row.props.children).includes('broken.png')),'refusal identifies the rejected image');
 f.browser().props.onPick(image('good',true));assert.equal(f.browser().props.selectedIds.size,0,'valid selection still toggles off');
 assert.ok(!nodes(f.render()).some(row=>row.props.role==='status'),'valid pick clears the refusal');
});

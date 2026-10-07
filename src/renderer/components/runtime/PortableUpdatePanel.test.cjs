const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function nodes(node){if(!node)return[];if(Array.isArray(node))return node.flatMap(nodes);if(typeof node!=='object')return[];return[node,...nodes(node.props?.children)];}
function fixture(){
 let cursor=0,tree,dirty=true;const slots=[],effects=[],launches=[],reads=[],inspections=[];
 const state={status:'committed',root:'/owned-external',installation_id:'1'.repeat(32),update_id:'2'.repeat(32),database_fence:3,version:'1.0.0',allowed_recovery:['finish','forward']};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!slots[i]){slots[i]={deps};effects.push(()=>slots[i].cleanup=fn());}}};
 const jsx=(type,props)=>({type,props}),file=path.join(__dirname,'PortableUpdatePanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const host={can:()=>true,updates:{selectPortableHome:async()=>state,inspectPortable:async()=>{inspections.push(true);return state;},launchPortable(expected){const d=deferred();launches.push({...d,expected});return d.promise;},inspectPortableLaunch(expected){const d=deferred();reads.push({...d,expected});return d.promise;}}};
 m.require=name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='../../services/hostAdapter'?{host}:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){dirty=true;for(let i=0;i<10&&dirty;i++){dirty=false;cursor=0;tree=m.exports.PortableUpdatePanel();effects.splice(0).forEach(f=>f());}assert(!dirty);return tree;}
 render();return{state,launches,reads,inspections,render,button:name=>nodes(tree).find(n=>n.type==='button'&&n.props.children===name),text:()=>JSON.stringify(tree),unmount:()=>slots.forEach(s=>s?.cleanup?.())};
}
async function select(f){f.button('portable 설치 폴더 선택').props.onClick();await tick();f.render();}
test('duplicate clicks before React rerenders cannot submit two launch requests or hide the first outcome',async()=>{
 const f=fixture();await select(f);const button=f.button('전환한 portable 앱 시작');button.props.onClick();button.props.onClick();
 assert.equal(f.launches.length,1);assert.deepEqual(f.launches[0].expected,{installation_id:f.state.installation_id,update_id:f.state.update_id,database_fence:3});
 f.launches[0].resolve({status:'starting',bootstrap_binding_verified:false});await tick();f.render();assert(f.text().includes('앱 시작 중'));assert(f.button('전환한 portable 앱 시작').props.disabled);assert(f.button('portable 변경 내용 확인').props.disabled);f.unmount();
});
test('lost response keeps launch disabled and state refresh uses launch inspection instead of installation mutation',async()=>{
 const f=fixture();await select(f);f.button('전환한 portable 앱 시작').props.onClick();f.launches[0].reject(Error('controlled response loss'));await tick();f.render();
 assert(f.button('전환한 portable 앱 시작').props.disabled);assert(f.text().includes('controlled response loss'));
 f.button('portable 상태 다시 읽기').props.onClick();assert.equal(f.reads.length,1);assert.equal(f.inspections.length,0);
 f.reads[0].resolve({status:'recovery_required'});await tick();f.render();assert(f.text().includes('앱 실행 확인·복구 필요'));assert(!f.text().includes('실행 완료'));f.unmount();
});
test('late launch success after unmount cannot replace the pending screen state',async()=>{
 const f=fixture();await select(f);f.button('전환한 portable 앱 시작').props.onClick();f.render();const before=f.text();f.unmount();
 f.launches[0].resolve({status:'ready',bootstrap_binding_verified:true});await tick();f.render();assert.equal(f.text(),before);
});
test('reopened selection restores durable launch state and blocks duplicate launch or update',async()=>{
 const f=fixture();f.state.launch_state={status:'ready',bootstrap_binding_verified:true};await select(f);
 assert(f.text().includes('앱과 백엔드의 설치 연결 확인됨'));assert(f.button('전환한 portable 앱 시작').props.disabled);assert(f.button('portable 변경 내용 확인').props.disabled);
 f.button('portable 상태 다시 읽기').props.onClick();assert.equal(f.reads.length,1);assert.equal(f.inspections.length,0);f.unmount();
});

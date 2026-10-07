const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function nodes(node){if(!node)return[];if(Array.isArray(node))return node.flatMap(nodes);if(typeof node!=='object')return[];return[node,...nodes(node.props?.children)];}
function fixture(){
 let cursor=0,tree,dirty=true;const slots=[],effects=[],launches=[],reads=[],inspections=[],previews=[],applications=[];
 const state={status:'committed',root:'/owned-external',installation_id:'1'.repeat(32),update_id:'2'.repeat(32),database_fence:3,version:'1.0.0',allowed_recovery:['finish','forward']};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!slots[i]){slots[i]={deps};effects.push(()=>slots[i].cleanup=fn());}}};
 const jsx=(type,props)=>({type,props}),file=path.join(__dirname,'PortableUpdatePanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const host={can:()=>true,updates:{selectPortableHome:async()=>state,inspectPortable:async()=>{inspections.push(true);return state;},previewPortable(channel,pins){const d=deferred();previews.push({...d,channel,pins});return d.promise;},applyPortable:async id=>{applications.push(id);return state;},launchPortable(expected){const d=deferred();launches.push({...d,expected});return d.promise;},inspectPortableLaunch(expected){const d=deferred();reads.push({...d,expected});return d.promise;}}};
 m.require=name=>name==='react'?react:name==='react/jsx-runtime'?{jsx,jsxs:jsx}:name==='../../services/hostAdapter'?{host}:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){dirty=true;for(let i=0;i<10&&dirty;i++){dirty=false;cursor=0;tree=m.exports.PortableUpdatePanel();effects.splice(0).forEach(f=>f());}assert(!dirty);return tree;}
 render();return{state,launches,reads,inspections,previews,applications,render,input:name=>nodes(tree).find(n=>n.type==='input'&&n.props['aria-label']===name),button:name=>nodes(tree).find(n=>n.type==='button'&&n.props.children===name),text:()=>JSON.stringify(tree),unmount:()=>slots.forEach(s=>s?.cleanup?.())};
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

const pins={workspace_id:'1'.repeat(32),project_id:'2'.repeat(32),plan_sha256:'3'.repeat(64)};
function fill(f){for(const[key,label]of[['workspace_id','검증 워크스페이스 ID'],['project_id','검증 프로젝트 ID'],['plan_sha256','검증 계획 해시']]){const input=f.input(label);assert(input,'missing independent canary input '+label);input.props.onChange({target:{value:pins[key]}});f.render();}}
function previewResult(installable=true){return {review_id:'controlled-review',current_version:'1',version:'2',publisher:'controlled',application_layout:'portable/v1',application_file_count:1,application_link_count:0,pack_count:0,artifact_bytes:10,source_sha256:'4'.repeat(64),plan_sha256:'5'.repeat(64),installable,preactivation_canary:{reason:installable?null:'The fixed candidate worker is unavailable.',pins:{...pins},supported:installable}};}
test('canary preview requires explicit complete bindings and one captured request',async()=>{
 const f=fixture();await select(f);assert(f.button('portable 변경 내용 확인').props.disabled);fill(f);assert(!f.button('portable 변경 내용 확인').props.disabled);
 const button=f.button('portable 변경 내용 확인');button.props.onClick();button.props.onClick();assert.equal(f.previews.length,1);assert.deepEqual(f.previews[0].pins,pins);f.render();assert(f.input('검증 계획 해시').props.disabled);
 f.input('검증 계획 해시').props.onChange({target:{value:'6'.repeat(64)}});f.render();assert.equal(f.input('검증 계획 해시').props.value,pins.plan_sha256);
 f.previews[0].resolve(previewResult());await tick();f.render();assert(f.text().includes('업데이트 전에 기준 이미지 검증을 실행할 준비'));f.unmount();
});
test('canary changed selection revokes visible review and a retained old apply handler',async()=>{
 const f=fixture();await select(f);fill(f);f.button('portable 변경 내용 확인').props.onClick();f.previews[0].resolve(previewResult());await tick();f.render();
 f.input('선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다').props.onChange({target:{checked:true}});f.render();const old=f.button('검토한 portable 업데이트 적용');assert(!old.props.disabled);
 f.input('검증 계획 해시').props.onChange({target:{value:'6'.repeat(64)}});f.render();assert.equal(f.button('검토한 portable 업데이트 적용'),undefined);
 old.props.onClick();await tick();f.render();assert.deepEqual(f.applications,[]);f.unmount();
});
test('canary unavailable target stays visibly refused even if an old handler is invoked',async()=>{
 const f=fixture();await select(f);fill(f);f.button('portable 변경 내용 확인').props.onClick();f.previews[0].resolve(previewResult(false));await tick();f.render();
 assert(f.text().includes('업데이트 적용이 차단'));assert(f.text().includes('fixed candidate worker is unavailable'));
 f.input('선택한 portable 설치의 학습·검사가 종료되고 백업을 확인했습니다').props.onChange({target:{checked:true}});f.render();const apply=f.button('검토한 portable 업데이트 적용');assert(apply.props.disabled);apply.props.onClick();await tick();f.render();assert.deepEqual(f.applications,[]);f.unmount();
});

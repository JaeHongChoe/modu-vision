const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b;});return{promise,resolve,reject};};
function nodes(node){if(!node)return[];if(Array.isArray(node))return node.flatMap(nodes);if(typeof node!=='object')return[];return[node,...nodes(node.props?.children)];}
function fixture(){
 const context={key:'project-a',scope:{current:{}},projectDir:'/project-a',generation:1,identity:'local'},reads=[],installs=[];
 let cursor=0,dirty=true,tree;const slots=[],effects=[];
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{const next=typeof value==='function'?value(slots[i]):value;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}];},useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect(fn,deps){const i=cursor++;if(!slots[i]||!deps.every((d,j)=>Object.is(d,slots[i].deps[j]))){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}}};
 const jsx=(type,props)=>({type,props});
 const file=path.join(__dirname,'RuntimePackPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
 const mocks={react,'react/jsx-runtime':{jsx,jsxs:jsx},'../../services/api':{getApiPersistenceIdentity:()=>context.identity,getProjectContextGeneration:()=>context.generation},'../../services/hostAdapter':{host:{can:()=>false}},'./useDeliveryScope':{useDeliveryScope:()=>context},'../../services/productDeliveryApi':{productDeliveryApi:{runtimePacks(){const d=deferred();reads.push(d);return d.promise;},installRuntimePack(body){const d=deferred();installs.push({body,...d});return d.promise;}}}};
 m.require=name=>mocks[name]??original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 function render(){dirty=true;for(let i=0;i<10&&dirty;i++){dirty=false;cursor=0;tree=m.exports.RuntimePackPanel();effects.splice(0).forEach(f=>f());}assert(!dirty);return tree;}
 const button=name=>nodes(tree).find(n=>n.type==='button'&&n.props.children===name),input=name=>nodes(tree).find(n=>n.type==='input'&&n.props['aria-label']===name);
 render();return{context,reads,installs,render,button,input,nodes:()=>nodes(tree),fill(){for(const[name,value]of[['런타임 팩 payload 폴더','/project-a/payload'],['런타임 팩 inventory 경로','/project-a/inventory.json'],['런타임 팩 검토 SHA-256','a'.repeat(64)]]){input(name).props.onChange({target:{value}});render();}},switch(){context.key='project-b';context.scope.current={};context.projectDir='/project-b';context.generation++;render();},unmount(){slots.forEach(s=>s?.cleanup?.());},text:()=>JSON.stringify(tree)};
}
const report=packs=>({project_id:'project-a',target:{platform:'darwin',arch:'arm64',python:'3.12'},packs,activation_supported:false});
const row={id:'pydicom-cpu',version:'3.0.2',kind:'dicom',inventory_sha256:'a'.repeat(64),integrity:'verified',target_compatible:true,python_abi:{compatible:true,state:'wheel_tags_match'},total_bytes:2,estimated_staging_bytes:6,activated:false,signature_verified:false,execution_verified:false};
test('empty or invalid independent pin cannot install and a current failure allows deliberate retry',async()=>{
 const f=fixture();f.reads[0].resolve(report([]));await tick();f.render();assert(f.button('팩 검증·보관').props.disabled);f.fill();assert(!f.button('팩 검증·보관').props.disabled);
 f.button('팩 검증·보관').props.onClick();f.render();assert(f.button('팩 검증·보관').props.disabled);f.installs[0].reject(Error('Payload hash mismatch'));await tick();f.render();assert(f.text().includes('Payload hash mismatch'));assert(!f.button('팩 검증·보관').props.disabled);
 f.button('팩 검증·보관').props.onClick();f.installs[1].resolve(row);await tick();f.reads[1].resolve(report([row]));await tick();f.render();assert(!f.text().includes('Payload hash mismatch'));assert(f.text().includes('검증한 파일 보관 완료'));assert(f.text().includes('배포자 서명·대상 실행 검증 필요'));assert.equal(f.installs.length,2);f.unmount();
});
test('older initial inventory cannot overwrite newer install inventory',async()=>{
 const f=fixture();f.fill();f.button('팩 검증·보관').props.onClick();f.installs[0].resolve(row);await tick();f.reads[1].resolve(report([row]));await tick();f.render();assert(f.text().includes('pydicom-cpu'));f.reads[0].resolve(report([]));await tick();f.render();assert(f.text().includes('pydicom-cpu'));f.unmount();
});
test('a superseded initial error cannot replace a fresh tamper result',async()=>{
 const f=fixture();f.button('팩 무결성 다시 확인').props.onClick();f.reads[1].resolve(report([{...row,integrity:'failed',target_compatible:false,error:'Payload tampered'}]));await tick();f.render();f.reads[0].reject(Error('Old inventory unavailable'));await tick();f.render();assert(f.text().includes('Payload tampered'));assert(!f.text().includes('Old inventory unavailable'));f.unmount();
});
test('scope switch clears sensitive input and discards late install and old errors',async()=>{
 const f=fixture();f.fill();f.button('팩 검증·보관').props.onClick();f.switch();assert.equal(f.input('런타임 팩 payload 폴더').props.value,'');assert.equal(f.input('런타임 팩 검토 SHA-256').props.value,'');f.installs[0].resolve(row);f.reads[0].reject(Error('Old project failure'));await tick();f.render();assert.equal(f.reads.length,2);assert(!f.text().includes('Old project failure'));assert(!f.text().includes('보관 완료'));f.reads[1].resolve(report([]));await tick();f.render();f.unmount();
});

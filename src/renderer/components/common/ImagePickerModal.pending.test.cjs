const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=tree=>Array.isArray(tree)?tree.flatMap(nodes):tree&&typeof tree==='object'?[tree,...nodes(tree.props?.children)]:[];
const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
function fixture(){
 const directory=process.env.MV_MODAL_TEST_DIRECTORY||__dirname;
 let cursor=0,dirty=false,tree,effects=[],isOpen=true,epoch=0;const slots=[],requests=[],confirmed=[],remembered=[];
 const saved={source:'dataset',imagePath:'/owned/original.png',imageUuid:'saved',sha256:'a'.repeat(64),relativePath:'ng/original.png',fileName:'original.png'};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{const next=typeof value==='function'?value(slots[i]):value;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const previous=slots[i];slots[i]={deps,cleanup:previous?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}},useLayoutEffect(){cursor++;},useSyncExternalStore(_subscribe,getSnapshot){cursor++;return getSnapshot();}};
 const jsx=(type,props)=>({type,props});const Browser=()=>null;
 const mocks={'react':react,'react/jsx-runtime':{jsx,jsxs:jsx},'lucide-react':new Proxy({},{get:()=>()=>null}),
  '../../stores/useDatasetStore':{useDatasetStore:s=>s({folderPath:'/owned',images:[]})},
  '../../stores/useFlowchartStore':{useFlowchartStore:()=>({selectedImage:saved,setSelectedImage:value=>confirmed.push(value)})},
  '../../stores/useProjectStore':{useProjectStore:s=>s({task:'classification',project:{id:'owned-project',active_labelset_id:'default'}})},
  '../../services/api':{getProjectContextGeneration:()=>epoch,subscribeProjectContext:()=>()=>{},resolveApiUrl:x=>x,api:{library:{resolve:()=>new Promise((resolve,reject)=>requests.push({resolve,reject}))}}},
  '../../services/hostAdapter':{host:{can:()=>false}},'./ImageLibraryBrowser':{ImageLibraryBrowser:Browser},
  './librarySelection':{identityOf:value=>value?.imageUuid?{image_uuid:value.imageUuid,relative_path:value.relativePath,sha256:value.sha256}:null,recallSelection:()=>saved,rememberSelection:(_project,value)=>remembered.push(value),resolutionNotice:value=>value&&value.status!=='found'?'Saved original changed':null,selectionFromImage:value=>({source:'dataset',imagePath:value.file_path,imageUuid:value.image_uuid,relativePath:value.relative_path,sha256:value.sha256,fileName:value.file_name})}};
 const file=path.join(directory,'ImagePickerModal.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(directory);const original=m.require.bind(m);m.require=name=>mocks[name]??original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 react.useLayoutEffect=react.useEffect;
 const previousWindow=global.window,previousDocument=global.document,previousElement=global.HTMLElement;global.document={activeElement:null};global.HTMLElement=class {};global.window={addEventListener(){},removeEventListener(){}};
 function render(){cursor=0;dirty=false;tree=m.exports.ImagePickerModal({isOpen,onClose:()=>{isOpen=false;dirty=true;}});effects.splice(0).forEach(fn=>fn());return tree;}
 async function settle(){for(let i=0;i<12;i++){if(dirty||!tree)render();await new Promise(setImmediate);if(!dirty)return tree;}throw Error('modal did not settle');}
 const confirm=()=>{const button=nodes(tree).find(n=>n.type==='button'&&n.props?.children==='선택 확정');assert(button);return button;};
 return{saved,requests,confirmed,remembered,settle,confirm,controls:()=>nodes(tree),current:(changes={})=>({image_uuid:saved.imageUuid,relative_path:saved.relativePath,sha256:saved.sha256,valid:1,file_path:'/owned/current.png',...changes}),pick:value=>{const browser=nodes(tree).find(n=>n.type===Browser);assert(browser);browser.props.onPick(value);},close(){for(const slot of slots)slot?.cleanup?.();global.window=previousWindow;global.document=previousDocument;global.HTMLElement=previousElement;}};
}
test('saved dataset identity cannot be confirmed until its actual resolution returns found',async()=>{
 const f=fixture();try{await f.settle();assert.equal(f.requests.length,1);assert.equal(f.confirm().props.disabled,true,'unresolved saved bytes must not be offered for confirmation');assert.deepEqual(f.confirmed,[]);
 f.requests[0].resolve({results:[{status:'found',current:f.current()}]});await f.settle();assert.equal(f.confirm().props.disabled,false);f.confirm().props.onClick();assert.equal(f.confirmed[0].imageUuid,'saved');assert.equal(f.confirmed[0].imagePath,'/owned/current.png');assert.equal(f.remembered[0].sha256,f.saved.sha256);
 }finally{f.close();}
});
test('saved resolution transport failure cannot offer the unchecked path or rewrite remembered bytes',async()=>{
 const f=fixture();try{await f.settle();f.requests[0].reject(Error('owned resolution unavailable'));await f.settle();assert.equal(f.confirm().props.disabled,true);assert.deepEqual(f.confirmed,[]);assert.deepEqual(f.remembered,[]);}finally{f.close();}
});
test('changed saved identity leaves confirmation disabled and preserves remembered original',async()=>{
 const f=fixture();try{await f.settle();f.requests[0].resolve({results:[{status:'changed',relative_path:f.saved.relativePath,candidates:[]}]});await f.settle();assert.equal(f.confirm().props.disabled,true);assert.deepEqual(f.confirmed,[]);assert.deepEqual(f.remembered,[]);}finally{f.close();}
});
test('explicit newly picked valid row survives a late saved-identity failure',async()=>{
 const f=fixture();try{await f.settle();f.pick({image_uuid:'new',file_path:'/owned/new.png',relative_path:'ok/new.png',sha256:'b'.repeat(64),file_name:'new.png',valid:true});await f.settle();assert.equal(f.confirm().props.disabled,false);
 f.requests[0].reject(Error('late saved resolution failure'));await f.settle();assert.equal(f.confirm().props.disabled,false);f.confirm().props.onClick();assert.equal(f.confirmed[0].imageUuid,'new');assert.equal(f.remembered[0].sha256,'b'.repeat(64));
 }finally{f.close();}
});
for(const [label,changes]of [['excluded',{valid:false}],['unhashed',{sha256:null}],['malformed digest',{sha256:'not-a-digest'}],['unchecked',{valid:undefined}]]){
 test(`an ${label} revision row cannot replace the confirmed valid selection`,async()=>{
  const f=fixture();try{await f.settle();f.requests[0].resolve({results:[{status:'found',current:f.current()}]});await f.settle();
   f.pick({image_uuid:'missing',file_path:'/owned/missing.png',relative_path:'ng/missing.png',sha256:'b'.repeat(64),file_name:'missing.png',valid:true,...changes});await f.settle();
   const alert=f.controls().find(node=>node.props?.role==='alert');assert(alert,'rejected row requires a readable reason');assert.match(JSON.stringify(alert.props.children),/검사 대상으로 선택할 수 없습니다/);
   assert.equal(f.confirm().props.disabled,false,'the previous verified choice remains usable');f.confirm().props.onClick();assert.equal(f.confirmed[0].imageUuid,'saved');assert.equal(f.confirmed[0].sha256,f.saved.sha256);assert.equal(f.remembered[0].imageUuid,'saved');
  }finally{f.close();}
 });
 test(`an ${label} first revision row leaves confirmation disabled`,async()=>{
  const f=fixture();try{await f.settle();f.pick({image_uuid:'missing',file_path:'/owned/missing.png',relative_path:'ng/missing.png',sha256:'b'.repeat(64),file_name:'missing.png',valid:true,...changes});await f.settle();assert.equal(f.confirm().props.disabled,true);assert.deepEqual(f.confirmed,[]);assert.deepEqual(f.remembered,[]);
  }finally{f.close();}
 });
}
for(const [label,changes]of [['invalid',{valid:0}],['unhashed',{sha256:null}],['different content',{sha256:'b'.repeat(64)}]]){
 test(`a found saved resolution with ${label} current bytes cannot re-enable confirmation`,async()=>{
  const f=fixture();try{await f.settle();f.requests[0].resolve({results:[{status:'found',current:f.current(changes)}]});await f.settle();assert.equal(f.confirm().props.disabled,true);assert.deepEqual(f.confirmed,[]);assert.deepEqual(f.remembered,[]);}finally{f.close();}
 });
}
for(const [label,changes]of [['invalid',{valid:0}],['unhashed',{sha256:null}]]){
 test(`a moved saved candidate with ${label} bytes cannot bypass revision selection checks`,async()=>{
  const f=fixture();try{await f.settle();const candidate=f.current({image_uuid:'moved',relative_path:'ng/moved.png',file_path:'/owned/moved.png',...changes});f.requests[0].resolve({results:[{status:'moved',relative_path:f.saved.relativePath,candidates:[candidate]}]});await f.settle();
   const button=f.controls().find(node=>node.type==='button'&&Array.isArray(node.props?.children)&&node.props.children[0]==='ng/moved.png');assert(button);assert.equal(button.props.disabled,true);assert.match(button.props.title,/검사 대상으로 선택할 수 없습니다/);
   button.props.onClick();await f.settle();assert.equal(f.confirm().props.disabled,true);assert.deepEqual(f.remembered,[]);
  }finally{f.close();}
 });
}
test('a verified moved candidate is still an explicit usable choice',async()=>{
 const f=fixture();try{await f.settle();const candidate=f.current({image_uuid:'moved',relative_path:'ng/moved.png',file_path:'/owned/moved.png'});f.requests[0].resolve({results:[{status:'moved',relative_path:f.saved.relativePath,candidates:[candidate]}]});await f.settle();
  const button=f.controls().find(node=>node.type==='button'&&Array.isArray(node.props?.children)&&node.props.children[0]==='ng/moved.png');assert(button);assert.equal(button.props.disabled,false);button.props.onClick();await f.settle();f.confirm().props.onClick();assert.equal(f.confirmed[0].imageUuid,'moved');assert.equal(f.remembered[0].sha256,f.saved.sha256);
 }finally{f.close();}
});
test('choosing a verified row after refusal clears the reason and records only verified bytes',async()=>{
 const f=fixture();try{await f.settle();f.pick({image_uuid:'missing',file_path:'/owned/missing.png',relative_path:'ng/missing.png',sha256:null,file_name:'missing.png',valid:false});await f.settle();assert(f.controls().some(node=>node.props?.role==='alert'));
  f.pick({image_uuid:'new',file_path:'/owned/new.png',relative_path:'ok/new.png',sha256:'b'.repeat(64),file_name:'new.png',valid:true});await f.settle();assert(!f.controls().some(node=>node.props?.role==='alert'));f.confirm().props.onClick();assert.equal(f.confirmed[0].imageUuid,'new');assert.equal(f.remembered[0].sha256,'b'.repeat(64));
 }finally{f.close();}
});

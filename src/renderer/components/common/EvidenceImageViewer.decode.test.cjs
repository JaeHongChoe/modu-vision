const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
function fixture(layers=[{id:'original',label:'Original',image:'data:image/png;base64,AA==',space:'source',size:[8,8]}]){
 let cursor=0,tree,dirty=true;const slots=[],effects=[];const props={evidence:{key:'one',title:'Owned snapshot',layers},onClose(){}};
 const same=(a,b)=>a?.length===b?.length&&a.every((v,i)=>Object.is(v,b[i]));
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>{const next=typeof v==='function'?v(slots[i]):v;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}];},useRef(v){const i=cursor++;return slots[i]??(slots[i]={current:v});},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const prior=slots[i];slots[i]={deps,fn,cleanup:prior?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}}};
 const jsx=(type,props)=>({type,props});const mocks={'react':react,'react/jsx-runtime':{jsx,jsxs:jsx}};
 function load(file){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);m.require=n=>mocks[n]??(n==='./evidenceViewer'?load(path.join(__dirname,'evidenceViewer.ts')):req(n));m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;}
 const component=load(path.join(__dirname,'EvidenceImageViewer.tsx')).EvidenceImageViewer,previous=global.document;global.document={activeElement:null};
 function settle(){dirty=true;for(let i=0;i<10&&(dirty||!tree);i++){cursor=0;dirty=false;tree=component(props);effects.splice(0).forEach(f=>f());}assert(!dirty);return tree;}
 const all=()=>nodes(tree),byLabel=label=>all().find(n=>n.props?.['aria-label']===label),images=()=>all().filter(n=>n.type==='img'),alerts=()=>all().filter(n=>n.props?.role==='alert');
 return{props,settle,byLabel,images,alerts,replayMountEffects(){for(const s of slots)if(s?.fn){s.cleanup?.();s.cleanup=s.fn();}},load(img=images()[0]){assert.equal(typeof img.props.onLoad,'function');img.props.onLoad({currentTarget:{naturalWidth:8,naturalHeight:8}});settle();},fail(img=images()[0]){assert.equal(typeof img.props.onError,'function','actual image decoder errors must be handled');img.props.onError({currentTarget:{naturalWidth:0,naturalHeight:0}});settle();},close(){for(const s of slots)s?.cleanup?.();global.document=previous;}};
}
test('StrictMode effect replay does not strand a decoded image in its loading state',()=>{
 const f=fixture();try{f.settle();const img=f.images()[0];f.replayMountEffects();f.load(img);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);}finally{f.close();}
});
test('a captured drag cannot carry displacement into a newly selected raster',()=>{
 const base={id:'original',label:'Original',image:'data:image/png;base64,AA==',space:'source',size:[8,8]},f=fixture([base,{...base,id:'next',label:'Next',image:'data:image/png;base64,AQ=='}]);try{f.settle();f.load();f.byLabel('근거 이미지 이동 영역').props.onPointerDown({button:0,currentTarget:{setPointerCapture(){}},pointerId:1,clientX:10,clientY:10});f.byLabel('근거 이미지 종류').props.onChange({target:{value:'next'}});f.settle();f.load();f.byLabel('근거 이미지 이동 영역').props.onPointerMove({clientX:80,clientY:80});f.settle();assert.equal(f.byLabel('근거 이미지 이동 영역').props.children.props.style.transform,'translate(0px, 0px) scale(1)');}finally{f.close();}
});
test('undecoded and failed snapshots cannot offer image manipulation and decoder failure is visible',()=>{
 const f=fixture();try{f.settle();assert.equal(f.byLabel('근거 이미지 확대').props.disabled,true,'undecoded bytes must not offer image manipulation');f.fail();assert.equal(f.alerts().length,1);assert.match(JSON.stringify(f.alerts()[0].props.children),/읽을 수 없/);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,true);}finally{f.close();}
});
test('a same-layer replacement does not inherit failed decode or accept retained old callbacks',()=>{
 const f=fixture();try{f.settle();const old=f.images()[0];f.fail(old);f.props.evidence={...f.props.evidence,layers:[{...f.props.evidence.layers[0],image:'data:image/png;base64,AQ=='}]};f.settle();assert.equal(f.alerts().length,0);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,true);f.load();assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);f.fail(old);assert.equal(f.alerts().length,0);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);}finally{f.close();}
});
test('late decoder callbacks from another evidence identity cannot invalidate the current readable snapshot',()=>{
 const f=fixture();try{f.settle();const old=f.images()[0];f.props.evidence={...f.props.evidence,key:'another'};f.settle();f.load();f.fail(old);assert.equal(f.alerts().length,0);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);}finally{f.close();}
});
test('an unreadable overlay preserves readable base and can be cleared without carrying its error',()=>{
 const base={id:'original',label:'Original',image:'data:image/png;base64,AA==',space:'source',size:[8,8]},f=fixture([base,{...base,id:'overlay',label:'Overlay',image:'data:image/png;base64,AQ=='}]);try{f.settle();f.load();f.byLabel('근거 겹침 이미지').props.onChange({target:{value:'overlay'}});f.settle();const overlay=f.images().find(n=>n.props.alt==='겹침 Overlay');assert(overlay);f.fail(overlay);assert.equal(f.alerts().length,1);assert.equal(f.byLabel('근거 겹침 투명도').props.disabled,true);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);f.byLabel('근거 겹침 이미지').props.onChange({target:{value:''}});f.settle();assert.equal(f.alerts().length,0);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,false);}finally{f.close();}
});
test('external or absent snapshots keep the empty viewer non-executable',()=>{
 const f=fixture([{id:'external',label:'External',image:'https://foreign.example/image.png',space:'source',size:[8,8]}]);try{f.settle();assert.equal(f.images().length,0);assert.equal(f.byLabel('근거 이미지 확대').props.disabled,true);assert.equal(f.byLabel('근거 겹침 이미지').props.disabled,true);assert.equal(f.byLabel('근거 ROI 라벨').props.disabled,true);}finally{f.close();}
});

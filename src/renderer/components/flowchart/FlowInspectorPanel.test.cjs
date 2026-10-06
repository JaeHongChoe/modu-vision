const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function harness(storage={getItem:()=>null,setItem(){}},rowWidth=1200){
 const values=[],refs=[],effects=[];let cursor=0,refCursor=0;
 const jsx=(type,props)=>({type,props:props||{}});
 const react={useState:initial=>{const i=cursor++;if(!(i in values))values[i]=typeof initial==='function'?initial():initial;return[values[i],next=>values[i]=typeof next==='function'?next(values[i]):next];},useRef:initial=>{const i=refCursor++;return refs[i]||(refs[i]={current:initial});},useEffect:effect=>effects.push(effect),useLayoutEffect:effect=>effects.push(effect)};
 const name=path.join(__dirname,'FlowInspectorPanel.tsx'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m.require=ref=>ref==='react'?react:ref==='react/jsx-runtime'?{jsx,jsxs:jsx}:ref==='lucide-react'?new Proxy({},{get:()=>()=>null}):require(ref);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);
 const child={type:'input',props:{value:'uncommitted ROI draft'}};
 const render=()=>{cursor=0;refCursor=0;effects.length=0;const tree=m.exports.FlowInspectorPanel({language:'ko',children:child,storage});tree.props.ref.current={parentElement:{clientWidth:rowWidth}};return tree;};
 const flush=()=>{for(const effect of effects)effect();};
 return {module:m.exports,render,flush,child};
}
const walk=n=>!n||typeof n!=='object'?[]:[n,...[n.props?.children].flat(Infinity).flatMap(walk)];
const by=(tree,key,value)=>walk(tree).find(n=>n.props[key]===value);
test('saved preferences validate version, finite width and boolean collapse; denied storage stays usable',()=>{
 const {readInspectorPreference,writeInspectorPreference,INSPECTOR_PREFERENCE_KEY}=harness().module;
 const fallback=readInspectorPreference({getItem:()=>null});assert.deepEqual(fallback,{width:340,collapsed:false});
 for(const raw of ['{',JSON.stringify({version:2,width:420,collapsed:true}),JSON.stringify({version:1,width:'420',collapsed:true}),JSON.stringify({version:1,width:420,collapsed:'yes'})])assert.deepEqual(readInspectorPreference({getItem:()=>raw}),fallback);
 assert.deepEqual(readInspectorPreference({getItem:key=>{assert.equal(key,INSPECTOR_PREFERENCE_KEY);return JSON.stringify({version:1,width:99999,collapsed:true});}}),{width:560,collapsed:true});
 assert.deepEqual(readInspectorPreference({getItem(){throw Error('denied');}}),fallback);
 assert.doesNotThrow(()=>writeInspectorPreference({setItem(){throw Error('quota');}},fallback));
});
test('width bounds leave room for palette and graph, and retain a larger stored preference after a narrow viewport',()=>{
 const h=harness(),{inspectorWidthBounds,boundedInspectorWidth}=h.module;
 assert.deepEqual(inspectorWidthBounds(1200),{min:260,max:560});
 assert.deepEqual(inspectorWidthBounds(820),{min:260,max:360});
 assert.equal(boundedInspectorWidth(500,820),360);assert.equal(boundedInspectorWidth(500,1200),500);
 assert.equal(boundedInspectorWidth(NaN,1200),340);
});
test('keyboard and pointer resizing persist only layout while collapse keeps the same mounted draft child',()=>{
 const writes=[],h=harness({getItem:()=>null,setItem:(key,value)=>writes.push([key,JSON.parse(value)])});let tree=h.render();h.flush();tree=h.render();
 let handle=by(tree,'role','separator');assert.equal(handle.props['aria-valuenow'],340);
 const press=key=>{handle.props.onKeyDown({key,preventDefault(){}});tree=h.render();h.flush();handle=by(tree,'role','separator');};
 press('ArrowLeft');assert.equal(handle.props['aria-valuenow'],360);press('End');assert.equal(handle.props['aria-valuenow'],560);press('Home');assert.equal(handle.props['aria-valuenow'],260);
 const captured=[],target={setPointerCapture:id=>captured.push(id),releasePointerCapture(){},hasPointerCapture:()=>true};
 handle.props.onPointerDown({button:0,pointerId:7,clientX:900,currentTarget:target,preventDefault(){}});
 handle.props.onPointerMove({pointerId:8,clientX:100,currentTarget:target});tree=h.render();assert.equal(by(tree,'role','separator').props['aria-valuenow'],260,'foreign pointer cannot resize');
 handle.props.onPointerMove({pointerId:7,clientX:800,currentTarget:target});tree=h.render();h.flush();assert.equal(by(tree,'role','separator').props['aria-valuenow'],360);assert.deepEqual(captured,[7]);
 by(tree,'role','separator').props.onPointerUp({pointerId:7,currentTarget:target});
 by(tree,'aria-label','속성 패널 접기').props.onClick();tree=h.render();h.flush();
 assert.equal(tree.props.style.width,44);assert.equal(by(tree,'role','separator'),undefined);
 let content=by(tree,'data-flow-inspector-content',true);assert.equal(content.props.style.display,'none');assert.equal(content.props.children,h.child);
 by(tree,'aria-label','속성 패널 펼치기').props.onClick();tree=h.render();h.flush();content=by(tree,'data-flow-inspector-content',true);
 assert.equal(content.props.style.display,undefined);assert.equal(content.props.children,h.child);assert.equal(by(tree,'role','separator').props['aria-valuenow'],360);
 assert.deepEqual(Object.keys(writes.at(-1)[1]).sort(),['collapsed','version','width']);
 const reopened=harness({getItem:()=>JSON.stringify({version:1,width:360,collapsed:true}),setItem(){}}).render();assert.equal(reopened.props.style.width,44);
});
test('a canceled drag ignores subsequent pointer movement and non-primary clicks',()=>{
 const h=harness();let tree=h.render();h.flush();tree=h.render();const handle=by(tree,'role','separator'),target={setPointerCapture(){},releasePointerCapture(){},hasPointerCapture:()=>true};
 handle.props.onPointerDown({button:2,pointerId:1,clientX:0,currentTarget:target,preventDefault(){}});handle.props.onPointerMove({pointerId:1,clientX:-200});assert.equal(by(h.render(),'role','separator').props['aria-valuenow'],340);
 handle.props.onPointerDown({button:0,pointerId:1,clientX:0,currentTarget:target,preventDefault(){}});handle.props.onPointerCancel({pointerId:1,currentTarget:target});handle.props.onPointerMove({pointerId:1,clientX:-200});assert.equal(by(h.render(),'role','separator').props['aria-valuenow'],340);
});
test('another pointer cannot replace an owned drag or end it with foreign capture loss',()=>{
 const h=harness();let tree=h.render();h.flush();tree=h.render();const handle=by(tree,'role','separator'),captured=[],target={setPointerCapture:id=>captured.push(id)};
 const down=(id,x)=>handle.props.onPointerDown({button:0,pointerId:id,clientX:x,currentTarget:target,preventDefault(){}});
 down(1,900);down(2,500);handle.props.onLostPointerCapture({pointerId:2});handle.props.onPointerMove({pointerId:1,clientX:860});
 assert.deepEqual(captured,[1]);assert.equal(by(h.render(),'role','separator').props['aria-valuenow'],380);
 handle.props.onLostPointerCapture({pointerId:1});handle.props.onPointerMove({pointerId:1,clientX:700});assert.equal(by(h.render(),'role','separator').props['aria-valuenow'],380);
});

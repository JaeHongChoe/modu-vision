const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// UI finish (native app QA): on a 1440x900 screen the stacked helper panels left the labeling canvas a few percent of a
// large image. Focus editing folds them; the choice is remembered, and blocked storage never breaks the screen.
function load(){const name=path.join(__dirname,'labelingLayout.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
test('focus editing is remembered and blocked storage falls back to the full layout',()=>{const layout=load();
 const values=new Map();const storage={getItem:key=>values.has(key)?values.get(key):null,setItem:(key,value)=>values.set(key,value)};
 assert.equal(layout.readLabelingFocus(storage),false);
 layout.writeLabelingFocus(true,storage);assert.equal(layout.readLabelingFocus(storage),true);
 layout.writeLabelingFocus(false,storage);assert.equal(layout.readLabelingFocus(storage),false);
 const blocked={getItem(){throw new Error('denied');},setItem(){throw new Error('denied');}};
 assert.equal(layout.readLabelingFocus(blocked),false);assert.doesNotThrow(()=>layout.writeLabelingFocus(true,blocked));
 assert.equal(layout.readLabelingFocus(null),false);});
test('the switch keeps one name; its pressed state says whether focus is on (nqa1 review P3-1)',()=>{const layout=load();
 assert.equal(layout.FOCUS_SWITCH_NAME,'집중 편집');
 assert.equal(layout.focusStatus(false),'');
 assert.equal(layout.focusStatus(true),'접은 패널: 이미지 검토 · 파생 이미지 · 저장된 검토 대기열 · 워크플로 영향 · 모델 보조(SAM2)');
 for(const kept of ['라벨 세트','클래스','팀 데이터','DICOM'])assert.ok(!layout.LABELING_HELPER_PANELS.includes(kept),`${kept} stays visible`);});
test('the choice is kept under its key, and a blocked storage getter falls back (nqa1 review P3-2)',()=>{const layout=load();
 const values=new Map();const storage={getItem:key=>values.has(key)?values.get(key):null,setItem:(key,value)=>values.set(key,value)};
 layout.writeLabelingFocus(true,storage);assert.deepEqual([...values.entries()],[['mv.labeling.focus','1']]);
 const descriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new Error('SecurityError');}});
 try{assert.equal(layout.readLabelingFocus(),false);assert.doesNotThrow(()=>layout.writeLabelingFocus(true));}
 finally{if(descriptor)Object.defineProperty(globalThis,'localStorage',descriptor);else delete globalThis.localStorage;}});
test('a canvas shown at Fit is fitted again when its area changes size; a zoomed or panned view stays (nqa1 review P2-1)',()=>{const layout=load();
 const fit=(w,h,iw,ih)=>{const scale=Math.min((w-64)/iw,(h-64)/ih);return{scale,offsetX:(w-iw*scale)/2,offsetY:(h-ih*scale)/2};};
 const image={width:8192,height:5464};const small={width:1200,height:160},tall={width:1200,height:493};
 const atFit=fit(small.width,small.height,image.width,image.height);
 assert.deepEqual(layout.refitAfterResize(atFit,small,tall,image,fit),fit(tall.width,tall.height,image.width,image.height));
 assert.equal(layout.refitAfterResize({...atFit,scale:atFit.scale*2},small,tall,image,fit),null,'zoomed');
 assert.equal(layout.refitAfterResize({...atFit,offsetX:atFit.offsetX+40},small,tall,image,fit),null,'panned');
 assert.equal(layout.refitAfterResize(atFit,null,tall,image,fit),null,'first measure');
 assert.equal(layout.refitAfterResize(atFit,small,small,image,fit),null,'same size');
 assert.deepEqual(layout.refitAfterResize({scale:1,offsetX:0,offsetY:0},{width:0,height:0},tall,image,(w,h,iw,ih)=>w<=0||h<=0?{scale:1,offsetX:0,offsetY:0}:fit(w,h,iw,ih)),fit(tall.width,tall.height,image.width,image.height),'an image loaded into a zero-height area is fitted once it has room');});

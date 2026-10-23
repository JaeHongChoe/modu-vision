const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// UI finish (1024x768 report): the stacked panels above the step-1 toolbar left the body row 0 px tall, so the sidebar
// showed its padding and the gallery nothing. The focus view folds them; the choice is remembered, blocked storage
// never breaks the screen, and a body that is gone (not merely short) folds once by itself for a first-time viewer.
function load(){const name=path.join(__dirname,'datasetLayout.ts'),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
 m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
test('the focus view is remembered and blocked storage falls back to the full layout',()=>{const layout=load();
 const values=new Map();const storage={getItem:key=>values.has(key)?values.get(key):null,setItem:(key,value)=>values.set(key,value)};
 assert.equal(layout.readDatasetFocus(storage),false);
 layout.writeDatasetFocus(true,storage);assert.equal(layout.readDatasetFocus(storage),true);
 layout.writeDatasetFocus(false,storage);assert.equal(layout.readDatasetFocus(storage),false);
 const blocked={getItem(){throw new Error('denied');},setItem(){throw new Error('denied');}};
 assert.equal(layout.readDatasetFocus(blocked),false);assert.doesNotThrow(()=>layout.writeDatasetFocus(true,blocked));
 assert.equal(layout.readDatasetFocus(null),false);});
test('the switch keeps one name, distinct from step 2; its pressed state says whether the focus view is on',()=>{const layout=load();
 assert.equal(layout.FOCUS_SWITCH_NAME,'집중 보기');
 assert.notEqual(layout.FOCUS_SWITCH_NAME,'집중 편집','the browser specs find step 2\'s switch by this exact name');
 assert.equal(layout.focusStatus(false),'');
 assert.equal(layout.focusStatus(true),'접은 패널: 데이터 관리 도구 · 데이터 현황 · 데이터 준비 상태');
 assert.deepEqual([...layout.DATASET_HELPER_PANELS],['데이터 관리 도구','데이터 현황','데이터 준비 상태']);
 for(const kept of ['1단계','데이터 준비','학습 전 점검','데이터셋 폴더 열기','더 보기','검증된 데이터 버전'])assert.ok(!layout.DATASET_HELPER_PANELS.includes(kept),`${kept} stays visible`);});
test('the choice is kept under its own key, and a blocked storage getter falls back',()=>{const layout=load();
 const values=new Map();const storage={getItem:key=>values.has(key)?values.get(key):null,setItem:(key,value)=>values.set(key,value)};
 layout.writeDatasetFocus(true,storage);assert.deepEqual([...values.entries()],[['mv.dataset.focus','1']]);
 const descriptor=Object.getOwnPropertyDescriptor(globalThis,'localStorage');
 Object.defineProperty(globalThis,'localStorage',{configurable:true,get(){throw new Error('SecurityError');}});
 try{assert.equal(layout.readDatasetFocus(),false);assert.equal(layout.storedDatasetFocus(),null);assert.doesNotThrow(()=>layout.writeDatasetFocus(true));}
 finally{if(descriptor)Object.defineProperty(globalThis,'localStorage',descriptor);else delete globalThis.localStorage;}});

test('an unpressed 집중 보기 reads as no choice, so the screen may fold once by itself',()=>{
 const layout=load();const values={};const storage={getItem:key=>values[key]??null,setItem:(key,value)=>{values[key]=value;}};
 assert.equal(layout.storedDatasetFocus(storage),null);
 layout.writeDatasetFocus(false,storage);assert.equal(layout.storedDatasetFocus(storage),false);
 layout.writeDatasetFocus(true,storage);assert.equal(layout.storedDatasetFocus(storage),true);
 assert.equal(layout.storedDatasetFocus({getItem:()=>{throw new Error('blocked');}}),null);
});

test('a short body with the panels open points to 집중 보기; focus on, room or no measure says nothing',()=>{
 const layout=load();const notice=layout.NARROW_BODY_NOTICE;
 assert.equal(notice,'작업 영역이 좁습니다 · 집중 보기로 위쪽 패널을 접을 수 있습니다');assert.ok(notice.includes(layout.FOCUS_SWITCH_NAME));
 assert.equal(layout.NARROW_BODY_HEIGHT,240);
 for(const height of [0,4,239])assert.equal(layout.narrowBodyNotice(height,false),notice,`${height}px`);
 for(const height of [240,493])assert.equal(layout.narrowBodyNotice(height,false),'',`${height}px has room`);
 assert.equal(layout.narrowBodyNotice(4,true),'','the focus view already folded the panels');
 assert.equal(layout.narrowBodyNotice(null,false),'','never measured (no ResizeObserver)');
});

test('a body that is gone folds once by itself only for a viewer who never chose; a stored choice always wins',()=>{
 const layout=load();
 assert.equal(layout.UNUSABLE_BODY_HEIGHT,120);assert.ok(layout.UNUSABLE_BODY_HEIGHT<layout.NARROW_BODY_HEIGHT,'gone is stricter than short');
 assert.equal(layout.AUTO_FOLD_STATUS,'위쪽 패널을 접었습니다 · 집중 보기를 눌러 다시 보일 수 있습니다');assert.ok(layout.AUTO_FOLD_STATUS.includes(layout.FOCUS_SWITCH_NAME));
 for(const [height,choice,expected] of [[0,null,true],[119,null,true],[120,null,false],[239,null,false],[0,false,false],[0,true,false],[null,null,false]])
  assert.equal(layout.autoFoldDecision(height,choice),expected,`${height}px with choice ${choice}`);
});

test('the short status and notice forms fit the toolbar below 1280px and name the same things', () => {
  const {FOLD_STATUS_SHORT, NARROW_BODY_NOTICE_SHORT, AUTO_FOLD_STATUS, NARROW_BODY_NOTICE} = load();
  assert.ok(FOLD_STATUS_SHORT.length < 12 && NARROW_BODY_NOTICE_SHORT.length < 12);
  assert.ok(AUTO_FOLD_STATUS.includes('위쪽 패널') && FOLD_STATUS_SHORT.includes('위쪽 패널'));
  assert.ok(NARROW_BODY_NOTICE.includes('작업 영역') && NARROW_BODY_NOTICE_SHORT.includes('작업 영역'));
});

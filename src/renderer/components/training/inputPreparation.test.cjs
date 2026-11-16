const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const m=new Module('inputPreparation.ts',module);
m._compile(ts.transpileModule(fs.readFileSync(path.join(__dirname,'inputPreparation.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,'inputPreparation.ts');
const {inputPreparationScope,validateInputRecipe,moveInputOperation}=m.exports;
test('scoped recipe keys change with project, labelset, source and task',()=>{
 const source={apiIdentity:'/api',projectId:'p',labelsetId:'a',source:'/images',task:'classification'};
 for(const key of Object.keys(source))assert.notEqual(inputPreparationScope(source),inputPreparationScope({...source,[key]:'other'}));
});
test('chosen operation order is preserved and edits move only the chosen operation',()=>{
 const recipe={version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1},{op:'brightness',factor:1.5}]};
 assert.deepEqual(validateInputRecipe(recipe,'classification'),recipe);
 assert.deepEqual(moveInputOperation(recipe.operations,1,-1),[recipe.operations[1],recipe.operations[0],recipe.operations[2]]);
 assert.deepEqual(recipe.operations[0],{op:'crop',roi:[0,0,.5,1]});
 assert.throws(()=>validateInputRecipe(recipe,'detection'),/분류.*분할/);
 assert.throws(()=>validateInputRecipe({version:1,operations:[{op:'brightness',factor:NaN}]},'classification'));
 assert.throws(()=>validateInputRecipe({version:1,operations:[{op:'normalize'}]},'classification'));
});

function loadComponent(mocks){
 const filename=path.join(__dirname,'TrainingInputPreparation.tsx'),component=new Module(filename,module);component.filename=filename;component.paths=Module._nodeModulePaths(__dirname);const base=component.require.bind(component);
 component.require=key=>key in mocks?mocks[key]:key==='./inputPreparation'?m.exports:base(key);
 component._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,filename);return component.exports;
}
function nodes(tree){if(Array.isArray(tree))return tree.flatMap(nodes);if(!tree||typeof tree!=='object')return[];return[tree,...Object.values(tree.props||{}).flatMap(value=>Array.isArray(value)?value.flatMap(nodes):nodes(value))];}
function words(value){return Array.isArray(value)?value.map(words).join(''):value&&typeof value==='object'?words(value.props?.children):String(value??'');}
function fixture(initial={}){
 let cursor=0,identity=initial.identity||'/api',generation=1,dirty=false;const effects=[],slots=[],requests=[],saved=[],writes=[];
 const props={task:'classification',imageSize:32,disabled:false};
 const project={id:'p',active_labelset_id:'a',source_dataset_dir:'/source'};
 const store={inputRecipe:initial.recipe||null,inputRecipeScope:initial.scope||null,inputRecipeSha256:initial.hash||null,setInputRecipe:(scope,recipe,hash)=>{saved.push([scope,recipe,hash]);Object.assign(store,{inputRecipeScope:scope,inputRecipe:recipe,inputRecipeSha256:hash});dirty=true;}};
 const useTrainingStore=()=>store;useTrainingStore.getState=()=>store;
 const dataset={images:initial.images||[{file_path:'/source/a.png',file_name:'a.png'}],sourceAuthority:'authority-a',datasetKey:'/source\0classification'};
 const api={getApiPersistenceIdentity:()=>identity,getProjectContextGeneration:()=>generation,request:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{input_recipe_sha256:'abc123',source_size:[64,32],prepared_size:[32,32],original_image:'data:before',prepared_image:'data:after',original_target:null,prepared_target:null};}};
 const react={useEffect:(fn,deps)=>{const i=cursor++,old=slots[i];if(!old||deps.some((value,j)=>!Object.is(value,old.deps[j]))){const slot={deps,cleanup:old?.cleanup};slots[i]=slot;effects.push(()=>{slot.cleanup?.();slot.cleanup=fn();});}},useRef:initial=>{const i=cursor++;return slots[i]??(slots[i]={current:initial});},useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],next=>{const value=typeof next==='function'?next(slots[i]):next;if(!Object.is(value,slots[i]))dirty=true;slots[i]=value;}];}};
 const view=loadComponent({'react':react,'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'lucide-react':{},'../../services/api':api,'../../stores/useProjectStore':{useProjectStore:fn=>fn({project})},'../../stores/useDatasetStore':{useDatasetStore:()=>dataset},'../../stores/useTrainingStore':{useTrainingStore},'./ProgramWorkbenchControls':{programButton:'button'}});
 const storage={getItem:key=>key.startsWith(`modu.input-preparation.v1:${initial.identity||'/api'}:`)&&initial.history?JSON.stringify(initial.history):null,setItem:(...args)=>writes.push(args)};
 return{render(task=props.task){props.task=task;const previous=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:storage,configurable:true,writable:true});try{let tree;for(let i=0;i<12;i++){dirty=false;cursor=0;tree=view.TrainingInputPreparation(props);effects.splice(0).forEach(fn=>fn());if(!dirty)return tree;}throw Error('component effects did not settle');}finally{if(previous)Object.defineProperty(global,'localStorage',previous);else delete global.localStorage;}},api,requests,saved,writes,project,store,dataset,props,setIdentity:value=>{identity=value;},setGeneration:value=>{generation=value;},unmount:()=>{for(const slot of slots)slot?.cleanup?.();},runEffects:()=>effects.splice(0).forEach(fn=>fn()),storage};
}

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const button=(tree,label)=>{const found=nodes(tree).find(node=>node.type==='button'&&words(node.props.children)===label);assert.ok(found,`button ${label}`);return found;};
async function withStorage(f,work){const previous=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:f.storage,configurable:true,writable:true});try{return await work();}finally{if(previous)Object.defineProperty(global,'localStorage',previous);else delete global.localStorage;}}
function holdPreviews(f){const held=[];f.api.request=(url,options)=>{f.requests.push({url,body:JSON.parse(options.body)});return new Promise((resolve,reject)=>held.push({resolve,reject}));};return held;}
const previewResult=(tag,extra={})=>({input_recipe_sha256:`hash-${tag}`,source_sha256:'source-original-hash',source_size:[64,32],prepared_size:[32,32],original_image:`data:${tag}-original`,prepared_image:`data:${tag}-prepared`,original_target:null,prepared_target:null,target_status:'classification_label_unchanged',...extra});

test('the panel executes preview before saving, uses final model size, and invalidates preview after edits',async()=>{
 const f=fixture();const previous=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:f.storage,configurable:true,writable:true});
 try{
  let tree=f.render();let all=nodes(tree);
  all.find(n=>n.type==='select').props.onChange({target:{value:'/source/a.png'}});
  all.find(n=>n.type==='button'&&words(n.props.children)==='+ 고정 회전').props.onClick();
  tree=f.render();all=nodes(tree);assert.equal(all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.disabled,true);
  all.find(n=>n.type==='button'&&words(n.props.children)==='실제 입력 미리보기').props.onClick();
  await new Promise(resolve=>setImmediate(resolve));
  tree=f.render();all=nodes(tree);
  assert.equal(f.requests[0].url,'/api/training/input-preparation/preview');
  assert.deepEqual(f.requests[0].body,{task:'classification',image_path:'/source/a.png',input_recipe:{version:1,operations:[{op:'rotate90',turns:1}]},image_size:[32,32]});
  assert.equal(all.filter(n=>n.type==='img').length,2);
  const save=all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장');assert.equal(save.props.disabled,false);save.props.onClick();
  assert.equal(f.saved.length,1);assert.equal(f.writes.length,1);assert.equal(f.saved[0][2],'abc123');
  all.find(n=>n.type==='button'&&words(n.props.children)==='+ 밝기').props.onClick();
  all=nodes(f.render());assert.equal(all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.disabled,true);
 }finally{if(previous)Object.defineProperty(global,'localStorage',previous);else delete global.localStorage;}
});
test('unsupported families show an explicit notice and expose no recipe editor',()=>{
 const f=fixture(),all=nodes(f.render('detection'));
 assert.ok(all.some(n=>n.props?.role==='note'&&words(n).includes('분류·영역 분할')));
 assert.equal(all.filter(n=>n.type==='fieldset').length,0);
});

test('a current scoped recipe configured by an action survives older local history on panel mount',()=>{
 const current={version:1,operations:[{op:'brightness',factor:1.5}]},stale={version:1,operations:[{op:'rotate90',turns:1}]};
 const f=fixture({recipe:current,scope:inputPreparationScope({apiIdentity:'/api',projectId:'p',labelsetId:'a',source:'/source',task:'classification'}),history:[{recipe:stale,sha256:'old'}]});
 const previous=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:f.storage,configurable:true,writable:true});
 try{f.render();f.runEffects();const all=nodes(f.render());assert.ok(all.find(n=>n.type==='input'&&n.props['aria-label']==='1번 밝기 배율'));}
 finally{if(previous)Object.defineProperty(global,'localStorage',previous);else delete global.localStorage;}
});

function scopedFixture(recipe){return fixture({recipe,hash:'initial',scope:inputPreparationScope({apiIdentity:'/api',projectId:'p',labelsetId:'a',source:'/source',task:'classification'})});}
test('an external same-scope recipe update refreshes a pristine editor instead of leaving stale save contents',()=>{
 const before={version:1,operations:[{op:'rotate90',turns:1}]},after={version:1,operations:[{op:'brightness',factor:1.5}]};
 const f=scopedFixture(before);f.render();f.store.setInputRecipe(f.store.inputRecipeScope,after,'new-hash');const all=nodes(f.render());
 assert.ok(all.find(n=>n.type==='input'&&n.props['aria-label']==='1번 밝기 배율'));
 assert.ok(!all.find(n=>n.type==='select'&&n.props['aria-label']==='1번 회전 각도'));
});
test('an external update preserves a dirty draft and blocks overwriting it until explicit reload',()=>{
 const before={version:1,operations:[{op:'rotate90',turns:1}]},after={version:1,operations:[{op:'brightness',factor:1.5}]};
 const f=scopedFixture(before);let all=nodes(f.render());all.find(n=>n.type==='button'&&words(n.props.children)==='+ 대비').props.onClick();f.render();
 f.store.setInputRecipe(f.store.inputRecipeScope,after,'new-hash');all=nodes(f.render());
 assert.ok(all.some(n=>n.props?.role==='alert'&&words(n).includes('저장한 규칙이 바뀌었습니다')));
 assert.ok(all.find(n=>n.type==='input'&&n.props['aria-label']==='2번 대비 배율'),'intentional draft survives');
 assert.equal(all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.disabled,true);
 all.find(n=>n.type==='button'&&words(n.props.children)==='저장한 규칙 불러오기').props.onClick();all=nodes(f.render());
 assert.ok(all.find(n=>n.type==='input'&&n.props['aria-label']==='1번 밝기 배율'));
 assert.ok(!all.find(n=>n.type==='input'&&n.props['aria-label']==='2번 대비 배율'));
});
test('switching API identity with identical project and source never restores the previous transport recipe',()=>{
 const before={version:1,operations:[{op:'rotate90',turns:1}]};const f=scopedFixture(before);
 let all=nodes(f.render());assert.ok(all.find(n=>n.type==='select'&&n.props['aria-label']==='1번 회전 각도'));
 f.setIdentity('/other-api');all=nodes(f.render());
 assert.ok(!all.find(n=>n.type==='select'&&n.props['aria-label']==='1번 회전 각도'));
 assert.ok(!f.saved.some(row=>row[0].includes('/other-api')&&row[1]?.operations.length));
});

test('a save closure from before an external update cannot erase the current next-training rule',()=>{
 const f=scopedFixture(null),all=nodes(f.render()),staleSave=all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.onClick;
 const current={version:1,operations:[{op:'rotate90',turns:1}]};f.store.setInputRecipe(f.store.inputRecipeScope,current,'new-hash');
 staleSave();assert.deepEqual(f.store.inputRecipe,current);assert.equal(f.saved.length,1);assert.equal(f.writes.length,0);
});

test('an external rule update retires an in-flight preview without leaving the editor disabled',()=>{
 const before={version:1,operations:[{op:'rotate90',turns:1}]},after={version:1,operations:[{op:'brightness',factor:1.5}]};
 const f=scopedFixture(before);f.api.request=()=>new Promise(()=>{});let all=nodes(f.render());
 all.find(n=>n.type==='button'&&words(n.props.children)==='실제 입력 미리보기').props.onClick();all=nodes(f.render());assert.equal(all.find(n=>n.type==='fieldset').props.disabled,true);
 f.store.setInputRecipe(f.store.inputRecipeScope,after,'new-hash');all=nodes(f.render());assert.equal(all.find(n=>n.type==='fieldset').props.disabled,false);
});

test('preprocessing is a connected block editor with one settings group at a time and preserves drafts on navigation',()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1},{op:'brightness',factor:1.3}]});
 let all=nodes(f.render());
 const blocks=all.filter(n=>n.type==='button'&&n.props['aria-pressed']!==undefined&&n.props['data-input-operation']!==undefined);
 assert.equal(blocks.length,2,'each operation is a selectable block');
 assert.equal(blocks[0].props['aria-pressed'],true);assert.equal(blocks[1].props['aria-pressed'],false);
 blocks[1].props.onClick();all=nodes(f.render());
 assert.equal(all.find(n=>n.props?.['data-operation-settings']===0).props.hidden,true);
 assert.equal(all.find(n=>n.props?.['data-operation-settings']===1).props.hidden,false);
 all.find(n=>n.type==='input'&&n.props['aria-label']==='2번 밝기 배율').props.onChange({target:{value:'1.7'}});
 all=nodes(f.render());all.find(n=>n.type==='button'&&n.props['data-input-operation']===0).props.onClick();
 all=nodes(f.render());all.find(n=>n.type==='button'&&n.props['data-input-operation']===1).props.onClick();
 all=nodes(f.render());assert.equal(all.find(n=>n.type==='input'&&n.props['aria-label']==='2번 밝기 배율').props.value,1.7);
 assert.equal(f.requests.length,0,'selecting blocks does not execute a preview or start a job');
});

test('selected-block preview executes only the prefix and cannot qualify the full training recipe',async()=>{
 const f=scopedFixture({version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1}]});
 let all=nodes(f.render());
 const mode=all.find(n=>n.type==='select'&&n.props['aria-label']==='미리보기 범위');assert.ok(mode,'preview scope is explicit');
 mode.props.onChange({target:{value:'block'}});all=nodes(f.render());
 await all.find(n=>n.type==='button'&&words(n.props.children)==='선택 블록 미리보기').props.onClick();
 all=nodes(f.render());assert.deepEqual(f.requests[0].body.input_recipe.operations,[{op:'crop',roi:[0,0,.5,1]}]);
 assert.equal(all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.disabled,true,'a prefix response is not evidence for the full recipe');
 assert.equal(all.filter(n=>n.type==='img').length,2);
 all.find(n=>n.type==='select'&&n.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'all'}});all=nodes(f.render());
 await all.find(n=>n.type==='button'&&words(n.props.children)==='실제 입력 미리보기').props.onClick();
 all=nodes(f.render());assert.equal(f.requests[1].body.input_recipe.operations.length,2);
 assert.equal(all.find(n=>n.type==='button'&&words(n.props.children)==='규칙 저장').props.disabled,false);
});

// Removing the active-key save fence lets an earlier valid preview commit an
// older recipe over a newer draft, even though the current Save is disabled.
test('a retained save from a qualified full preview cannot overwrite a later operation edit',async()=>{
 const f=scopedFixture(null);
 await withStorage(f,async()=>{
  let tree=f.render();button(tree,'+ 고정 회전').props.onClick();tree=f.render();
  button(tree,'실제 입력 미리보기').props.onClick();await tick();tree=f.render();
  const staleSave=button(tree,'규칙 저장');assert.equal(staleSave.props.disabled,false);
  nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='1번 회전 각도').props.onChange({target:{value:'2'}});
  tree=f.render();assert.equal(button(tree,'규칙 저장').props.disabled,true,'the new recipe requires a fresh preview');
  staleSave.props.onClick();
  assert.equal(f.writes.length,0,'a retired save callback cannot persist the old previewed recipe');
  assert.equal(f.saved.length,0,'a retired callback cannot replace next-training state');
  button(tree,'실제 입력 미리보기').props.onClick();await tick();tree=f.render();
  assert.equal(button(tree,'규칙 저장').props.disabled,false);button(tree,'규칙 저장').props.onClick();
  assert.equal(f.writes.length,1);assert.deepEqual(f.saved[0][1],{version:1,operations:[{op:'rotate90',turns:2}]});
 });
});

test('rapid duplicate preview clicks send one request and preserve the pending fieldset until completion',async()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1}]}),held=holdPreviews(f);
 let tree=f.render();const inspect=button(tree,'실제 입력 미리보기').props.onClick;
 inspect();inspect();tree=f.render();assert.equal(f.requests.length,1);assert.equal(nodes(tree).find(node=>node.type==='fieldset').props.disabled,true);
 held[0].resolve(previewResult('one'));await tick();tree=f.render();
 assert.equal(nodes(tree).find(node=>node.type==='fieldset').props.disabled,false);
 assert.deepEqual(nodes(tree).filter(node=>node.type==='img').map(node=>node.props.src),['data:one-original','data:one-prepared']);
 assert.equal(button(tree,'규칙 저장').props.disabled,false);
});

for(const outcome of ['success','error'])test(`retired ${outcome} reply cannot overwrite a new preview or release its pending fieldset after project ABA`,async()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1}]}),held=holdPreviews(f);
 let tree=f.render();button(tree,'실제 입력 미리보기').props.onClick();assert.equal(held.length,1);
 f.project.id='other';f.render();f.project.id='p';tree=f.render();
 button(tree,'실제 입력 미리보기').props.onClick();tree=f.render();assert.equal(held.length,2);
 if(outcome==='success')held[0].resolve(previewResult('retired'));else held[0].reject(Error('retired project refusal'));
 await tick();tree=f.render();
 assert.equal(nodes(tree).find(node=>node.type==='fieldset').props.disabled,true,'old finally cannot release the current request');
 assert.equal(nodes(tree).filter(node=>node.type==='img').length,0,'the returned old scope has not earned a fresh preview');
 assert.ok(!nodes(tree).some(node=>node.props?.role==='alert'&&words(node).includes('retired')));
 held[1].resolve(previewResult('current'));await tick();tree=f.render();
 assert.equal(nodes(tree).find(node=>node.type==='fieldset').props.disabled,false);
 assert.deepEqual(nodes(tree).filter(node=>node.type==='img').map(node=>node.props.src),['data:current-original','data:current-prepared']);
});

const scopeTransitions=[
 ['API identity',f=>f.setIdentity('/other-api')],
 ['labelset',f=>{f.project.active_labelset_id='b';}],
 ['project source',f=>{f.project.source_dataset_dir='/other-source';f.dataset.images=[{file_path:'/other-source/a.png',file_name:'a.png'}];f.dataset.datasetKey='/other-source\0classification';}],
 ['context generation',f=>f.setGeneration(2)],
 ['accepted dataset authority',f=>{f.dataset.sourceAuthority='authority-b';}],
];
for(const [name,change] of scopeTransitions)test(`an in-flight prefix cannot qualify or render under a new ${name}`,async()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1},{op:'contrast',factor:1.5}]}),held=holdPreviews(f);
 let tree=f.render();nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'block'}});
 tree=f.render();button(tree,'선택 블록 미리보기').props.onClick();assert.equal(held.length,1);
 assert.deepEqual(f.requests[0].body.input_recipe.operations,[{op:'rotate90',turns:1}]);
 change(f);tree=f.render();const savedBefore=f.saved.length,writesBefore=f.writes.length;
 held[0].resolve(previewResult('old-prefix'));await tick();tree=f.render();
 assert.equal(nodes(tree).filter(node=>node.type==='img').length,0,'retired image/mask output is not current');
 assert.equal(f.saved.length,savedBefore);assert.equal(f.writes.length,writesBefore);
 if(name==='context generation'||name==='accepted dataset authority')assert.equal(button(tree,'규칙 저장').props.disabled,true,'an unpreviewed full recipe remains unqualified');
});

test('a partial block preview never replaces the hash of an already qualified full recipe',async()=>{
 const recipe={version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1}]};
 const f=scopedFixture(recipe),held=holdPreviews(f);
 await withStorage(f,async()=>{
  let tree=f.render();button(tree,'실제 입력 미리보기').props.onClick();held[0].resolve(previewResult('full'));await tick();tree=f.render();
  nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'block'}});
  tree=f.render();button(tree,'선택 블록 미리보기').props.onClick();held[1].resolve(previewResult('prefix'));await tick();tree=f.render();
  assert.deepEqual(f.requests.map(row=>row.body.input_recipe.operations),[recipe.operations,[{op:'crop',roi:[0,0,.5,1]}]]);
  assert.deepEqual(nodes(tree).filter(node=>node.type==='img').map(node=>node.props.src),['data:prefix-original','data:prefix-prepared']);
  assert.equal(button(tree,'규칙 저장').props.disabled,false);button(tree,'규칙 저장').props.onClick();
  assert.deepEqual(f.saved[0][1],recipe);assert.equal(f.saved[0][2],'hash-full','saving binds the verified whole-recipe hash, not the visible prefix hash');
  assert.equal(JSON.parse(f.writes[0][1]).at(-1).sha256,'hash-full');
 });
});

test('a current prefix failure stays visible while an earlier qualified full preview remains intact',async()=>{
 const f=scopedFixture({version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1}]}),held=holdPreviews(f);
 let tree=f.render();button(tree,'실제 입력 미리보기').props.onClick();held[0].resolve(previewResult('full-valid'));await tick();tree=f.render();
 nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'block'}});tree=f.render();
 button(tree,'선택 블록 미리보기').props.onClick();held[1].reject(Error('현재 블록의 미리보기 실패'));await tick();tree=f.render();
 const alerts=nodes(tree).filter(node=>node.props?.role==='alert');assert.equal(alerts.length,1);assert.match(words(alerts[0]),/현재 블록의 미리보기 실패/);
 assert.equal(button(tree,'규칙 저장').props.disabled,false,'the same whole recipe still has its successful validation');
 nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'all'}});tree=f.render();
 assert.deepEqual(nodes(tree).filter(node=>node.type==='img').map(node=>node.props.src),['data:full-valid-original','data:full-valid-prepared']);
 assert.equal(f.saved.length,0);assert.equal(f.writes.length,0);
});

test('the last selected block qualifies saving only because its request contains the complete recipe',async()=>{
 const recipe={version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1}]},f=scopedFixture(recipe),held=holdPreviews(f);
 await withStorage(f,async()=>{
  let tree=f.render();nodes(tree).find(node=>node.props?.['data-input-operation']===1).props.onClick();tree=f.render();
  nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'block'}});tree=f.render();
  button(tree,'선택 블록 미리보기').props.onClick();assert.deepEqual(f.requests[0].body.input_recipe,recipe);
  held[0].resolve(previewResult('whole-prefix'));await tick();tree=f.render();
  assert.equal(button(tree,'규칙 저장').props.disabled,false);button(tree,'규칙 저장').props.onClick();
  assert.deepEqual(f.saved[0][1],recipe);assert.equal(f.saved[0][2],'hash-whole-prefix');
 });
});

for(const state of ['disabled','isTraining','isRecoveringTraining','contextGeneration'])test(`retained preview and save callbacks refuse a newly active ${state} guard`,async()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1}]});
 await withStorage(f,async()=>{
  let tree=f.render();button(tree,'실제 입력 미리보기').props.onClick();await tick();tree=f.render();
  const inspect=button(tree,'실제 입력 미리보기').props.onClick,save=button(tree,'규칙 저장').props.onClick;
  if(state==='disabled'){f.props.disabled=true;f.render();}
  else if(state==='contextGeneration')f.setGeneration(2);
  else f.store[state]=true;
  inspect();save();await tick();
  assert.equal(f.requests.length,1,'the old callback cannot send another preview');assert.equal(f.writes.length,0);assert.equal(f.saved.length,0);
 });
});

test('a retained qualified save cannot write after its editor unmounts',async()=>{
 const f=scopedFixture({version:1,operations:[{op:'rotate90',turns:1}]});
 await withStorage(f,async()=>{
  let tree=f.render();button(tree,'실제 입력 미리보기').props.onClick();await tick();tree=f.render();
  const save=button(tree,'규칙 저장').props.onClick;f.unmount();save();assert.equal(f.writes.length,0);assert.equal(f.saved.length,0);
 });
});

test('segmentation prefix previews show original and transformed mask overlays without qualifying the full recipe',async()=>{
 const f=fixture({recipe:{version:1,operations:[{op:'crop',roi:[0,0,.5,1]},{op:'rotate90',turns:1}]},hash:'saved-mask',scope:inputPreparationScope({apiIdentity:'/api',projectId:'p',labelsetId:'a',source:'/source',task:'segmentation'})}),held=holdPreviews(f);
 let tree=f.render('segmentation');nodes(tree).find(node=>node.type==='select'&&node.props['aria-label']==='미리보기 범위').props.onChange({target:{value:'block'}});tree=f.render();
 button(tree,'선택 블록 미리보기').props.onClick();assert.equal(f.requests[0].body.task,'segmentation');assert.deepEqual(f.requests[0].body.input_recipe.operations,[{op:'crop',roi:[0,0,.5,1]}]);
 held[0].resolve(previewResult('mask-prefix',{original_target:'data:original-mask-overlay',prepared_target:'data:cropped-mask-overlay',target_status:'synchronized_mask'}));await tick();tree=f.render();
 assert.deepEqual(nodes(tree).filter(node=>node.type==='img').map(node=>({src:node.props.src,alt:node.props.alt})),[{src:'data:original-mask-overlay',alt:'원본 이미지와 정답 영역'},{src:'data:cropped-mask-overlay',alt:'준비한 입력 이미지와 정답 영역'}]);
 assert.ok(nodes(tree).filter(node=>node.type==='figcaption').every(node=>words(node).includes('청록: 정답 영역')));
 assert.equal(button(tree,'규칙 저장').props.disabled,true);assert.equal(f.saved.length,0);assert.equal(f.writes.length,0);
});

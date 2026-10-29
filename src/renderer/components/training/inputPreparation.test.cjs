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
function nodes(tree){if(!tree||typeof tree!=='object')return[];return[tree,...Object.values(tree.props||{}).flatMap(value=>Array.isArray(value)?value.flatMap(nodes):nodes(value))];}
function words(value){return Array.isArray(value)?value.map(words).join(''):value&&typeof value==='object'?words(value.props?.children):String(value??'');}
function fixture(initial={}){
 let cursor=0,identity=initial.identity||'/api',dirty=false;const effects=[],slots=[],requests=[],saved=[],writes=[];
 const project={id:'p',active_labelset_id:'a',source_dataset_dir:'/source'};
 const store={inputRecipe:initial.recipe||null,inputRecipeScope:initial.scope||null,inputRecipeSha256:initial.hash||null,setInputRecipe:(scope,recipe,hash)=>{saved.push([scope,recipe,hash]);Object.assign(store,{inputRecipeScope:scope,inputRecipe:recipe,inputRecipeSha256:hash});dirty=true;}};
 const useTrainingStore=()=>store;useTrainingStore.getState=()=>store;
 const api={getApiPersistenceIdentity:()=>identity,getProjectContextGeneration:()=>1,request:async(url,options)=>{requests.push({url,body:JSON.parse(options.body)});return{input_recipe_sha256:'abc123',source_size:[64,32],prepared_size:[32,32],original_image:'data:before',prepared_image:'data:after',original_target:null,prepared_target:null};}};
 const react={useEffect:(fn,deps)=>{const i=cursor++,old=slots[i];if(!old||deps.some((value,j)=>!Object.is(value,old.deps[j]))){slots[i]={deps};effects.push(fn);}},useRef:initial=>{const i=cursor++;return slots[i]??(slots[i]={current:initial});},useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],next=>{const value=typeof next==='function'?next(slots[i]):next;if(!Object.is(value,slots[i]))dirty=true;slots[i]=value;}];}};
 const view=loadComponent({'react':react,'react/jsx-runtime':{jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})},'lucide-react':{},'../../services/api':api,'../../stores/useProjectStore':{useProjectStore:fn=>fn({project})},'../../stores/useDatasetStore':{useDatasetStore:()=>({images:initial.images||(initial.images=[{file_path:'/source/a.png',file_name:'a.png'}])})},'../../stores/useTrainingStore':{useTrainingStore},'./ProgramWorkbenchControls':{programButton:'button'}});
 const storage={getItem:key=>key.startsWith(`modu.input-preparation.v1:${initial.identity||'/api'}:`)&&initial.history?JSON.stringify(initial.history):null,setItem:(...args)=>writes.push(args)};
 return{render(task='classification'){const previous=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{value:storage,configurable:true,writable:true});try{let tree;for(let i=0;i<12;i++){dirty=false;cursor=0;tree=view.TrainingInputPreparation({task,imageSize:32});effects.splice(0).forEach(fn=>fn());if(!dirty)return tree;}throw Error('component effects did not settle');}finally{if(previous)Object.defineProperty(global,'localStorage',previous);else delete global.localStorage;}},api,requests,saved,writes,project,store,setIdentity:value=>{identity=value;},runEffects:()=>effects.splice(0).forEach(fn=>fn()),storage};
}

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

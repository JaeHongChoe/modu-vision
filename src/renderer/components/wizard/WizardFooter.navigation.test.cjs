const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=value=>Array.isArray(value)?value.flatMap(nodes):value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];
const text=value=>Array.isArray(value)?value.map(text).join(''):value&&typeof value==='object'?text(value.props?.children):typeof value==='string'||typeof value==='number'?String(value):'';
function mount(nextAction,options={}){
 const calls=[],states=[],effects=[],listeners=new Map();let cursor=0,effectCursor=0,pending=[],modal=false;
 const state={activeStep:options.activeStep||2,language:options.language||'ko',viewMode:options.viewMode||'simple',setStep:async step=>{calls.push(['step',step]);await options.save?.();}};
 const readiness={stage:state.activeStep,status:'ready',missingInputs:[],completedInputs:['사진 가져오기'],nextAction,...options.readiness};
 const react={useState(initial){const i=cursor++;if(!(i in states))states[i]=typeof initial==='function'?initial():initial;return[states[i],value=>{states[i]=typeof value==='function'?value(states[i]):value;}];},useEffect(fn,deps){const i=effectCursor++,old=effects[i];if(!old||deps.some((value,j)=>!Object.is(value,old.deps[j])))pending.push(()=>{old?.cleanup?.();effects[i]={deps,cleanup:fn()};});}};
 const jsx=(type,props)=>({type,props});
 const mocks={react,'react/jsx-runtime':{jsx,jsxs:jsx,Fragment:'fragment'},'lucide-react':{ArrowLeft:'icon',ArrowRight:'icon'},
  '../../stores/useProjectStore':{useProjectStore:()=>state},'../common/AsyncAction':{AsyncAction:'AsyncAction'},
  './useWorkflowReadiness':{useWorkflowReadiness:()=>({readiness,refresh:()=>calls.push(['refresh'])})},'./DataChangeNotice':{DataChangeNotice:'DataChangeNotice'},
  './workflowReadiness':{focusWorkflowTarget:target=>calls.push(['focus',target]),progressLine:()=>options.progress||''},
  '../labeling/unlabeledImage':{openUnlabeledImage:async()=>calls.push(['unlabeled'])},
  '../../stores/assistContext':{assistPanelErrors:()=>[],subscribeAssistContext:()=>()=>{}},
  './AutoProgressDialog':{AUTO_TITLE:'원클릭 자동 진행',AutoProgressDialog:'AutoProgressDialog',useAutoProgressStore:selector=>selector({run:options.run})},
  '../../stores/useAssistantStore':{useAssistantStore:{getState:()=>({open:()=>calls.push(['assistant'])})}},
 };
 const file=path.join(__dirname,'WizardFooter.tsx'),mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(__dirname);const requireOriginal=mod.require.bind(mod);mod.require=name=>name in mocks?mocks[name]:requireOriginal(name);
 mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 const oldWindow=global.window,oldDocument=global.document;
 global.window={addEventListener:(name,handler)=>listeners.set(name,handler),removeEventListener:name=>listeners.delete(name)};
 global.document={querySelector:()=>modal?{}:null};
 const render=()=>{cursor=0;effectCursor=0;pending=[];const tree=mod.exports.WizardFooter();for(const effect of pending)effect();return tree;};
 return{calls,state,render,primary:tree=>nodes(tree).find(node=>node.type==='AsyncAction'),keydown:event=>listeners.get('keydown')(event),modal:value=>{modal=value;},close(){for(const effect of effects)effect?.cleanup?.();global.window=oldWindow;global.document=oldDocument;}};
}
test('the footer says it opens training and waits for the existing navigation save before focusing',async()=>{
 let release;const save=new Promise(resolve=>{release=resolve;});
 const h=mount({label:'모델 학습 시작 (3단계)',step:3,target:'workflow-training'},{save:()=>save,progress:' · 확정 12장'});
 try{
  const tree=h.render(),primary=h.primary(tree);
  assert.equal(text(primary),'학습 화면 열기');assert.equal(primary.props['aria-label'],'권장 행동: 학습 화면 열기');
  primary.props.onClick();assert.deepEqual(h.calls,[['step',3]]);
  release();await new Promise(setImmediate);assert.deepEqual(h.calls,[['step',3],['focus','workflow-training']]);
  const status=nodes(tree).find(node=>node.props?.role==='status');assert.match(status.props.title,/학습 화면 열기.*확정 12장/);
 }finally{release();h.close();}
});
test('refresh, details and unlabeled-image navigation retain their distinct behavior',async()=>{
 for(const kind of ['refresh','details']){
  const h=mount({label:'학습 오류 확인',step:3,kind});try{h.primary(h.render()).props.onClick();await new Promise(setImmediate);assert.deepEqual(h.calls,kind==='refresh'?[['refresh']]:[]);if(kind==='details')assert.ok(nodes(h.render()).some(node=>node.type==='section'&&node.props['aria-label']==='단계 준비도 상세'));}finally{h.close();}
 }
 const h=mount({label:'직접 라벨 달기',step:2,target:'labeling-canvas'});try{h.primary(h.render()).props.onClick();await new Promise(setImmediate);assert.deepEqual(h.calls,[['step',2],['unlabeled'],['focus','labeling-canvas']]);}finally{h.close();}
});
test('keyboard navigation still skips editable fields and modal dialogs, and keeps internal step bounds',()=>{
 const h=mount({label:'확인',step:2},{activeStep:1});try{
  h.render();const event=extra=>({key:']',target:{closest:()=>null},preventDefault:()=>{},...extra});
  h.keydown(event({target:{closest:()=>({})}}));h.modal(true);h.keydown(event());assert.deepEqual(h.calls,[]);
  h.modal(false);h.keydown(event({key:'['}));assert.deepEqual(h.calls,[],'cannot step below one');
  h.keydown(event());assert.deepEqual(h.calls,[['step',2]]);
  h.state.activeStep=6;h.render();h.keydown(event());assert.equal(h.calls.length,1,'cannot step beyond six');
 }finally{h.close();}
});
test('the automatic-run entry says it opens setup or progress and only opens the existing dialog',()=>{
 for(const run of [null,{state:'running'}]){
  const h=mount({label:'확인',step:2},{run});try{const button=nodes(h.render()).find(node=>node.type==='button'&&node.props['aria-haspopup']==='dialog');assert.equal(button.props.children,run?'원클릭 자동 진행 상태 보기':'원클릭 자동 진행 설정 열기');button.props.onClick();assert.ok(nodes(h.render()).some(node=>node.type==='AutoProgressDialog'));assert.deepEqual(h.calls,[]);}finally{h.close();}
 }
});
test('simple footer names its internal task and gives only the recommended action visual priority',()=>{
 const h=mount({label:'데이터 가져오기',step:1,target:'workflow-data'},{activeStep:3,readiness:{status:'blocked',missingInputs:['현재 원본 이미지 가져오기 필요']}});
 try{
  const tree=h.render();assert.match(text(tree),/현재 작업.*모델 학습/);assert.doesNotMatch(text(tree),/3\/6/);
  const status=nodes(tree).find(node=>node.props?.role==='status');assert.doesNotMatch(status.props.className,/amber|bg-/,'status is not a full warning-colored bar');
  assert.match(text(status),/현재 원본 이미지 가져오기 필요/);
  assert.equal(nodes(tree).filter(node=>node.props?.className?.includes('workspace-button--primary')).length,1);
  const previous=nodes(tree).find(node=>node.props?.['aria-label']==='이전 단계 보기');
  const next=nodes(tree).find(node=>node.props?.['aria-label']==='다음 단계 보기');
  for(const button of [previous,next,nodes(tree).find(node=>node.props?.['aria-haspopup']==='dialog'),nodes(tree).find(node=>node.props?.['aria-expanded']===false)])assert.match(button.props.className,/workspace-button--quiet/);
  previous.props.onClick();next.props.onClick();assert.deepEqual(h.calls,[['step',2],['step',4]],'quiet arrows still navigate internal steps');
 }finally{h.close();}
 const full=mount({label:'확인',step:3},{activeStep:3,viewMode:'full'});try{const tree=full.render();assert.match(text(tree),/현재 작업.*모델 학습/);assert.equal(tree.type,'aside');assert.doesNotMatch(nodes(tree).find(node=>node.props?.['aria-label']==='이전 단계 보기').props.className,/workspace-button--quiet/);}finally{full.close();}
});

test('both view modes preserve an accessible next-action landmark and optional assistant entry',()=>{for(const viewMode of ['simple','full']){const h=mount({label:'확인',step:3},{viewMode});try{const tree=h.render();assert.equal(tree.type,'aside');assert.equal(tree.props['aria-label'],'다음 할 일');const assistant=nodes(tree).find(n=>n.type==='button'&&text(n)==='도우미 열기');assistant.props.onClick();assert.deepEqual(h.calls,[['assistant']]);assert.equal(nodes(tree).filter(n=>n.type==='AsyncAction').length,1);}finally{h.close();}}});

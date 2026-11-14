const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=value=>Array.isArray(value)?value.flatMap(nodes):value&&typeof value==='object'?[value,...nodes(value.props?.children)]:[];
function mount(nextAction,options={}){
 const calls=[],states=[],effects=[],listeners=new Map();let cursor=0,effectCursor=0,pending=[],modal=false;
 const state={activeStep:options.activeStep||2,language:options.language||'ko',setStep:async step=>{calls.push(['step',step]);await options.save?.();}};
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
  assert.equal(primary.props.children,'학습 화면 열기');assert.equal(primary.props['aria-label'],'권장 행동: 학습 화면 열기');
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

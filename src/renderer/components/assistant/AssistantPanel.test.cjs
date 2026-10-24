// The assistant panel follows the simple view rules (one primary button whose label follows the state, every other
// control a secondary workspace-button, nothing below 13px) and says plainly that every step works without it. The key
// field hands its value to the main process and clears itself; no key lives in the store.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const read=name=>fs.readFileSync(path.join(__dirname,name),'utf8');
const sources=fs.readdirSync(__dirname).filter(name=>name.endsWith('.tsx')).map(name=>[name,read(name)]);
function load(file,mocks){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name in mocks?mocks[name]:original(name);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);return m.exports;}
const nodes=tree=>!tree||typeof tree!=='object'?[]:[tree,...[].concat(tree.props?.children||[]).flat(Infinity).flatMap(nodes)];
const text=tree=>!tree?'':typeof tree==='string'||typeof tree==='number'?String(tree):[].concat(tree.props?.children||[]).flat(Infinity).map(text).join('');

function render(state,ui={}){
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState:initial=>[ui.draft!==undefined&&initial===''?ui.draft:typeof initial==='function'?initial():initial,value=>ui.sets?.push(value)],useRef:()=>({current:null}),useEffect:()=>{}};
 const store=Object.assign(selector=>selector(state),{getState:()=>state});
 const {AssistantPanel}=load(path.join(__dirname,'AssistantPanel.tsx'),{react:{__esModule:true,default:react,...react},
  '../../stores/useAssistantStore':{useAssistantStore:store,assistantBridge:()=>state.bridge},
  '../../stores/useAnnotationStore':{useAnnotationStore:selector=>selector({currentImage:{file_path:'/data/a.png'}})},
  './ChatgptConnectionSetup':{ChatgptConnectionSetup:()=>null},'./AssistantProviderSetup':{AssistantProviderSetup:()=>null},'./AssistantRunLog':{AssistantRunLog:()=>null}});
 return AssistantPanel({});
}
const base={isOpen:true,busy:false,pending:null,imageOffer:null,items:[],bridge:{},config:{active:'anthropic',encryptionAvailable:true,providers:[{provider:'anthropic',baseUrl:'https://api.anthropic.com',model:'m',format:null,hasKey:true,supportsImages:true,label:'Anthropic'}]},
 close(){},ask(){},stop(){},decide(){},offerImage(){},sendImage(){},dropImage(){},newConversation(){}};

test('exactly one primary button in every state, labelled by what it does',()=>{
 const states=[[base,'보내기'],[{...base,pending:{call:{id:'c',name:'save_split',arguments:{}},runId:'run_x',preview:{title:'분할 저장',lines:['학습 70%'],undo:'다시 나누기'}}},'확인하고 실행'],
  [{...base,imageOffer:{grantId:'g',message:'이 이미지 1장을 Anthropic로 보냅니다.',providerLabel:'Anthropic',question:'불량인가요?'}},'이미지 1장 보내기']];
 for(const [state,label] of states){
  const buttons=nodes(render(state)).filter(node=>node.type==='button');
  const primary=buttons.filter(button=>/workspace-button--primary/.test(button.props.className));
  assert.equal(primary.length,1);assert.equal(text(primary[0]),label);
  assert.ok(buttons.every(button=>/(^|\s)workspace-button(\s|$)/.test(button.props.className)),'every button is a workspace-button');
 }
 const confirm=nodes(render(states[1][0])).find(node=>node.props?.['aria-label']==='쓰기 실행 확인');
 assert.match(text(confirm),/분할 저장 · 확인이 필요합니다/);assert.match(text(confirm),/되돌리는 방법: 다시 나누기/);
 assert.match(text(nodes(render(states[2][0])).find(node=>node.props?.['aria-label']==='이미지 보내기 확인')),/이 이미지 1장을 Anthropic로 보냅니다/);
});
test('a paused ChatGPT account disables the composer even if older public readiness is still true',()=>{
 const state={...base,config:{active:'chatgpt',providers:[{provider:'chatgpt',baseUrl:'https://api.openai.com/v1',model:'chosen',hasKey:false,ready:true,label:'ChatGPT',supportsImages:false}],chatgpt:{accounts:[{recordId:'a',model:'chosen',signedIn:true,planUsageEnabled:true,usagePaused:true}],active:'a'}}};const tree=render(state);
 assert.equal(nodes(tree).find(n=>n.type==='textarea').props.disabled,true);assert.equal(nodes(tree).filter(n=>n.type==='button'&&/workspace-button--primary/.test(n.props.className)).length,0,'usage management owns the primary action while sending is paused');
});
test('paused ChatGPT keeps API settings collapsed and directs the composer to usage confirmation',()=>{
 const state={...base,config:{active:'chatgpt',providers:[{provider:'chatgpt',baseUrl:'https://api.openai.com/v1',model:'chosen',hasKey:false,ready:false,label:'ChatGPT',supportsImages:false}],chatgpt:{accounts:[{recordId:'a',model:'chosen',signedIn:true,planUsageEnabled:true,usagePaused:true}],active:'a'}}};const tree=render(state);
 assert.equal(nodes(tree).find(n=>n.type==='details').props.open,false);const composer=nodes(tree).find(n=>n.type==='textarea');assert.match(composer.props.placeholder,/사용량.*요청 재개/);assert.ok(!composer.props.placeholder.includes('공급자'));
 const missingApi=render({...base,config:{...base.config,providers:[{...base.config.providers[0],model:''}]}});assert.equal(nodes(missingApi).find(n=>n.type==='details').props.open,true,'missing API configuration still opens its own settings');
});

test('the panel says every step works without it, renders nothing when closed and tells a browser it is desktop-only',()=>{
 assert.equal(render({...base,isOpen:false}),null);
 assert.match(text(render(base)),/도우미 없이도 모든 단계를 쓸 수 있습니다/);
 assert.match(text(render({...base,bridge:null})),/데스크톱 앱에서 여세요/);
 const idle=nodes(render({...base,config:{active:null,providers:[],encryptionAvailable:true}}));
 assert.equal(idle.find(node=>node.type==='button'&&/primary/.test(node.props.className)).props.disabled,true,'nothing can be sent before a provider is set up');
});

test('simple-view type floor and Korean copy',()=>{
 for(const [name,source] of sources){
  assert.ok(!/text-\[(7|8|9|10|11|12)px\]/.test(source),`${name} sets text below 13px`);
  assert.ok(!/text-\[0\.[0-7]/.test(source),`${name} sets a fractional size below 13px`);
 }
});

test('the key field sends straight to the main process and clears itself; no key in the store or browser storage',()=>{
 const setup=read('AssistantProviderSetup.tsx');
 assert.match(setup,/aria-label="공급자 키" type="password"/);assert.match(setup,/autoComplete="off"/);
 assert.match(setup,/const key = keyDraft;\s*setKeyDraft\(''\);/,'the field is emptied before the save even starts');
 const store=fs.readFileSync(path.join(__dirname,'..','..','stores','useAssistantStore.ts'),'utf8');
 assert.ok(!/keyDraft|apiKey|\bkey:\s*string/.test(store),'the store holds no key');
 for(const [name,source] of [...sources,['useAssistantStore.ts',store]])assert.ok(!/localStorage|sessionStorage/.test(source),name);
});

// Design 5.4-6 (f): on step 2 the draft queue bar (z-[68]) drew over the open drawer (z-40). While open the drawer now
// has its own column: the app root reserves exactly the drawer's width, so the page narrows instead of sliding under it.
test('the open drawer takes its own column instead of lying under the step bars',()=>{
 const panel=read('AssistantPanel.tsx');
 const width=/<aside aria-label="도우미" className="fixed right-0 top-0 z-40 flex h-full (w-\[[0-9.]+rem\]) max-w-full/.exec(panel);
 assert.ok(width,'the drawer is fixed to the right edge with one width');
 const app=fs.readFileSync(path.join(__dirname,'..','..','App.tsx'),'utf8');
 assert.match(app,/const assistantOpen = useAssistantStore\(s => s\.isOpen\);/);
 assert.ok(app.includes("font-sans${assistantOpen ? ' pr-["+width[1].slice(3,-1)+"]' : ''}"),'the root reserves the drawer width while it is open');
});

test('ChatGPT readiness comes from the selected account and plan, never a key flag',()=>{
 const provider={provider:'chatgpt',baseUrl:'https://chatgpt.com',model:'chosen',format:null,hasKey:true,supportsImages:false,label:'ChatGPT',ready:true};
 for(const account of [{recordId:'a',model:'chosen',signedIn:false,planUsageEnabled:true},{recordId:'a',model:'chosen',signedIn:true,planUsageEnabled:false},{recordId:'a',model:'',signedIn:true,planUsageEnabled:true}]){
  const state={...base,config:{active:'chatgpt',providers:[provider],encryptionAvailable:true,chatgpt:{accounts:[account],active:'a',status:'connected'}}};
  assert.equal(nodes(render(state)).find(n=>n.type==='textarea').props.disabled,true);
 }
 const ready={...base,config:{active:'chatgpt',providers:[{...provider,hasKey:false}],encryptionAvailable:true,chatgpt:{accounts:[{recordId:'a',model:'chosen',signedIn:true,planUsageEnabled:true}],active:'a',status:'connected'}}};
 assert.equal(nodes(render(ready)).find(n=>n.type==='textarea').props.disabled,false);
 assert.match(text(render(ready)),/ChatGPT.*텍스트/);
 assert.ok(nodes(render(ready)).some(n=>n.type==='button'&&text(n)==='이미지와 함께 묻기'&&n.props.disabled));
});

test('a ChatGPT send rate limit exposes usage management directly beside the conversation controls',()=>{
 const state={...base,items:[{id:7,kind:'error',code:'rate_limited',text:'한도에 도달했습니다.',detail:'사용량 확인'}],config:{active:'chatgpt',providers:[{provider:'chatgpt',baseUrl:'https://chatgpt.com',model:'chosen',hasKey:false,ready:true,label:'ChatGPT',supportsImages:false}],chatgpt:{accounts:[{recordId:'a',model:'chosen',signedIn:true,planUsageEnabled:true}],active:'a'}}};
 assert.ok(nodes(render(state)).some(n=>n.type==='a'&&n.props.href==='https://chatgpt.com/settings/usage'&&/workspace-button/.test(n.props.className||'')));
});

// Defect: '멈추기' was offered while a confirmed write executed, and the settings controls stayed live, so either could end the
// turn after the backend write had already happened.
test('an executing confirmed write offers no stop and locks the settings until its outcome is recorded',()=>{
 const tree=render({...base,busy:true,writing:true}),buttons=nodes(tree).filter(n=>n.type==='button');
 assert.ok(!buttons.some(b=>text(b)==='멈추기'),'no stop while the write runs');
 assert.equal(buttons.filter(b=>text(b)==='실행 중…'&&b.props.disabled).length,1);
 assert.equal(nodes(tree).find(n=>n.type==='fieldset'&&n.props['aria-label']==='도우미 설정')?.props.disabled,true,'provider and ChatGPT controls are disabled');
 assert.match(text(tree),/실행 중.*결과/);
 const asking=render({...base,busy:true});assert.ok(nodes(asking).some(b=>b.type==='button'&&text(b)==='멈추기'),'a provider call can still be stopped');
 assert.equal(nodes(asking).find(n=>n.type==='fieldset').props.disabled,false);
});
// Defect: while a settings call ran, send cleared the typed question and the store silently dropped it.
test('a question typed while settings load is kept: send and image wait, and nothing is cleared without sending',()=>{
 const asked=[],offered=[],sets=[],question='왜 학습을 시작할 수 없나요?';
 const state={...base,configuring:true,ask:value=>asked.push(value),offerImage:value=>offered.push(value)};
 const tree=render(state,{draft:question,sets}),buttons=nodes(tree).filter(n=>n.type==='button');
 const primary=buttons.find(b=>/workspace-button--primary/.test(b.props.className)),image=buttons.find(b=>text(b)==='이미지와 함께 묻기');
 assert.equal(primary.props.disabled,true);assert.equal(image.props.disabled,true);
 primary.props.onClick();image.props.onClick();
 nodes(tree).find(n=>n.type==='textarea').props.onKeyDown({key:'Enter',shiftKey:false,nativeEvent:{isComposing:false},preventDefault(){}});
 assert.deepEqual([asked,offered],[[],[]]);assert.ok(!sets.includes(''),'the typed question stays in the composer');
 const after=[],ready=render({...state,configuring:false},{draft:question,sets:after});
 nodes(ready).find(b=>b.type==='button'&&/workspace-button--primary/.test(b.props.className)).props.onClick();
 assert.deepEqual(asked,[question]);assert.deepEqual(after,['']);
});

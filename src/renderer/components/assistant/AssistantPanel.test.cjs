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

function render(state){
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState:initial=>[typeof initial==='function'?initial():initial,()=>{}],useRef:()=>({current:null}),useEffect:()=>{}};
 const store=Object.assign(selector=>selector(state),{getState:()=>state});
 const {AssistantPanel}=load(path.join(__dirname,'AssistantPanel.tsx'),{react:{__esModule:true,default:react,...react},
  '../../stores/useAssistantStore':{useAssistantStore:store,assistantBridge:()=>state.bridge},
  '../../stores/useAnnotationStore':{useAnnotationStore:selector=>selector({currentImage:{file_path:'/data/a.png'}})},
  './AssistantProviderSetup':{AssistantProviderSetup:()=>null},'./AssistantRunLog':{AssistantRunLog:()=>null}});
 return AssistantPanel({});
}
const base={isOpen:true,busy:false,pending:null,imageOffer:null,items:[],bridge:{},config:{active:'anthropic',encryptionAvailable:true,providers:[{provider:'anthropic',baseUrl:'https://api.anthropic.com',model:'m',format:null,hasKey:true,supportsImages:true,label:'Anthropic'}]},
 close(){},ask(){},stop(){},decide(){},offerImage(){},sendImage(){},dropImage(){},newConversation(){}};

test('exactly one primary button in every state, labelled by what it does',()=>{
 assert.equal(sources.reduce((count,[,source])=>count+(source.match(/workspace-button--primary/g)||[]).length,0),1,'one primary across the assistant components');
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

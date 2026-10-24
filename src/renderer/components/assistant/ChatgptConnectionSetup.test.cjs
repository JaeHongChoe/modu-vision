const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>!t||typeof t!=='object'?[]:[t,...[].concat(t.props?.children||[]).flat(Infinity).flatMap(nodes)];
const text=t=>!t?'':typeof t==='string'||typeof t==='number'?String(t):[].concat(t.props?.children||[]).flat(Infinity).map(text).join('');
const deferred=()=>{let resolve;return {promise:new Promise(r=>resolve=r),resolve};};
function harness(chatgpt={accounts:[],active:null,encryptionAvailable:true,status:'idle',authority:'a0'}){
 let state={isOpen:true,config:{active:chatgpt.active?'chatgpt':null,providers:[],encryptionAvailable:true,chatgpt},configError:null},slots=[],cursor=0,effects=[],timers=new Map(),nextTimer=0,mounted=true;const calls=[];
 const bridge={chatgpt:{}};
 const methods={beginChatgpt:async input=>{calls.push(['begin',input]);return true;},acknowledgeChatgptPlanUsage:async id=>{state.config.chatgpt.accounts.find(a=>a.recordId===id).planUsageAcknowledged=true;return true;},resumeChatgptUsage:async id=>{state.config.chatgpt.accounts.find(a=>a.recordId===id).usagePaused=false;return true;},cancelChatgpt:async()=>{calls.push(['cancel']);return true;},selectChatgptAccount:async id=>{calls.push(['account',id]);return true;},selectChatgptModel:async(id,model)=>{calls.push(['model',id,model]);return true;},signOutChatgpt:async id=>{calls.push(['signOut',id]);return true;},listModels:async id=>{calls.push(['list',id]);return [{id:'visible-model',label:'Visible model'}];},loadConfig:async()=>{calls.push(['poll']);}};
 Object.assign(state,methods);const store=Object.assign(selector=>selector(state),{getState:()=>state});
 const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],value=>{if(mounted)slots[i]=typeof value==='function'?value(slots[i]):value;}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){const old=slots[i];slots[i]={deps,cleanup:old?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}}};
 const file=path.join(__dirname,'ChatgptConnectionSetup.tsx');let Component=()=>null;
 if(fs.existsSync(file)){const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const orig=m.require.bind(m);m.require=n=>n==='react'?{__esModule:true,default:react,...react}:n==='../../stores/useAssistantStore'?{useAssistantStore:store,assistantBridge:()=>bridge}:n==='../common/WorkspaceDialog'?{WorkspaceDialog:'dialog-shell'}:orig(n);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);Component=m.exports.ChatgptConnectionSetup;}
 // Only the component's local polling timer is replaced; every event handler is production code.
 const originalSet=global.setInterval,originalClear=global.clearInterval;
 function timersFor(fn){global.setInterval=(callback)=>{timers.set(++nextTimer,callback);return nextTimer;};global.clearInterval=id=>timers.delete(id);try{return fn();}finally{global.setInterval=originalSet;global.clearInterval=originalClear;}}
 function render(){cursor=0;const tree=Component({});timersFor(()=>{for(const fn of effects.splice(0))fn();});return tree;}
 return {render,calls,state,methods,timers,setChat(next){state.config={...state.config,chatgpt:next};},close(){state.isOpen=false;render();},unmount(){mounted=false;timersFor(()=>slots.forEach(slot=>slot?.cleanup?.()));},tick:async()=>{for(const fn of [...timers.values()])await fn();},button:(tree,label)=>nodes(tree).find(n=>n.type==='button'&&text(n)===label),nodes,text};
}
const account=(extra={})=>({recordId:'opaque-a',label:'계정 1',model:'',signedIn:true,planUsageEnabled:true,...extra});

test('connection requires an explicit terms acknowledgement and the Continue click; initial render never begins login',async()=>{
 const h=harness();let tree=h.render();const button=h.button(tree,'Continue with ChatGPT');assert.ok(button,'ChatGPT connection entrance exists');assert.equal(button.props.disabled,true);assert.equal(h.calls.length,0);
 const links=nodes(tree).filter(n=>n.type==='a');assert.ok(links.some(n=>n.props.href==='https://openai.com/policies/sign-in-with-chatgpt-terms/'));
 assert.match(text(tree),/보호 저장소/);assert.match(text(tree),/이미지/);assert.match(text(tree),/프로젝트/);
 nodes(tree).find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});tree=h.render();assert.equal(h.button(tree,'Continue with ChatGPT').props.disabled,false);
 await h.button(tree,'Continue with ChatGPT').props.onClick();assert.deepEqual(h.calls,[['begin',{termsAccepted:true}]]);
});
test('unavailable protected storage prevents login while API-key setup remains a separate option',()=>{
 const h=harness({accounts:[],active:null,encryptionAvailable:false,status:'idle'});let tree=h.render();nodes(tree).find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});tree=h.render();
 assert.equal(h.button(tree,'Continue with ChatGPT').props.disabled,true);assert.match(text(tree),/보호 저장소/);assert.equal(h.calls.length,0);
});
test('only waiting/exchanging polls and both closing and unmounting stop the local poll',async()=>{
 const h=harness();h.render();assert.equal(h.timers.size,0);
 h.setChat({...h.state.config.chatgpt,status:'waiting'});h.render();assert.equal(h.timers.size,1);await h.tick();assert.deepEqual(h.calls,[['poll']]);
 h.close();assert.equal(h.timers.size,0);await h.tick();assert.equal(h.calls.length,1);
 const x=harness({accounts:[],active:null,encryptionAvailable:true,status:'exchanging'});x.render();assert.equal(x.timers.size,1);x.unmount();assert.equal(x.timers.size,0);
});
test('a pending browser login has an explicit cancel action',async()=>{
 const h=harness({accounts:[],active:null,encryptionAvailable:true,status:'waiting'}),tree=h.render();await h.button(tree,'연결 취소').props.onClick();assert.deepEqual(h.calls,[['cancel']]);
});
test('identity-only login stays disabled for plan usage and never lists or chooses a model automatically',()=>{
 const h=harness({accounts:[account({planUsageEnabled:false})],active:'opaque-a',encryptionAvailable:true,status:'connected'}),tree=h.render();
 assert.match(text(tree),/플랜 사용.*활성화되지 않았/);assert.equal(h.calls.length,0);assert.equal(h.button(tree,'모델 목록 불러오기').props.disabled,true);
});
test('account labels stay opaque and account selection is explicit',async()=>{
 const h=harness({accounts:[account(),account({recordId:'opaque-b',label:'someone@example.invalid'})],active:'opaque-a',encryptionAvailable:true,status:'connected'}),tree=h.render();
 assert.ok(!text(tree).includes('@'));const picker=nodes(tree).find(n=>n.type==='select'&&n.props['aria-label']==='ChatGPT 계정');await picker.props.onChange({target:{value:'opaque-b'}});assert.deepEqual(h.calls,[['account','opaque-b']]);
});
test('model discovery and selecting a visible model require separate user actions',async()=>{
 const h=harness({accounts:[account()],active:'opaque-a',encryptionAvailable:true,status:'connected',authority:'a1'});let tree=h.render();assert.equal(h.calls.length,0);
 await h.button(tree,'모델 목록 불러오기').props.onClick();tree=h.render();assert.deepEqual(h.calls,[['list','chatgpt']]);
 const picker=nodes(tree).find(n=>n.type==='select'&&n.props['aria-label']==='ChatGPT 모델');assert.equal(picker.props.value,'');assert.match(text(tree),/Visible model/);
 await picker.props.onChange({target:{value:'visible-model'}});assert.deepEqual(h.calls.at(-1),['model','opaque-a','visible-model']);
 await picker.props.onChange({target:{value:'not-visible'}});assert.equal(h.calls.length,2,'unlisted models cannot be injected into a selection');
});
test('a model list from the former account cannot select a model for the newly active account',async()=>{
 const h=harness({accounts:[account(),account({recordId:'opaque-b'})],active:'opaque-a',encryptionAvailable:true,status:'connected',authority:'a1'}),result=deferred();h.state.listModels=()=>result.promise;
 let tree=h.render();const loading=h.button(tree,'모델 목록 불러오기').props.onClick();h.setChat({...h.state.config.chatgpt,active:'opaque-b',authority:'a2'});h.render();result.resolve([{id:'old',label:'Old account model'}]);await loading;tree=h.render();assert.ok(!text(tree).includes('Old account model'));
});
test('plan-ready account shows usage management and persists welcome dismissal across a component remount',async()=>{
 const h=harness({accounts:[account({model:'chosen'})],active:'opaque-a',encryptionAvailable:true,status:'connected'});let tree=h.render();assert.match(text(tree),/ChatGPT 플랜 사용 중/);
 assert.ok(nodes(tree).some(n=>n.type==='a'&&n.props.href==='https://chatgpt.com/settings/usage'));assert.match(text(tree),/플랜 사용량/);await h.button(tree,'안내 확인').props.onClick();tree=h.render();assert.equal(h.button(tree,'안내 확인'),undefined);
 assert.equal(h.state.config.chatgpt.accounts[0].planUsageAcknowledged,true);h.unmount();const restarted=harness(structuredClone(h.state.config.chatgpt));assert.equal(restarted.button(restarted.render(),'안내 확인'),undefined);
});
test('identity-only settings offer explicit plan enablement without changing ordinary reconnect',async()=>{
 const h=harness({accounts:[account({planUsageEnabled:false})],active:'opaque-a',encryptionAvailable:true,status:'connected'});let tree=h.render();const enable=h.button(tree,'ChatGPT 플랜 사용 켜기');assert.ok(enable);assert.equal(enable.props.disabled,true);
 nodes(tree).find(n=>n.type==='input'&&n.props.type==='checkbox').props.onChange({target:{checked:true}});tree=h.render();await h.button(tree,'ChatGPT 플랜 사용 켜기').props.onClick();assert.deepEqual(h.calls.at(-1),['begin',{termsAccepted:true,recordId:'opaque-a',enablePlanUsage:true}]);
});
test('paused plan requires visiting usage then explicit resume and never sends a question automatically',async()=>{
 const h=harness({accounts:[account({model:'chosen',usagePaused:true,planUsageAcknowledged:true})],active:'opaque-a',encryptionAvailable:true,status:'connected'});let tree=h.render();let resume=h.button(tree,'사용량 확인 후 요청 재개 허용');assert.ok(resume);assert.equal(resume.props.disabled,true);
 const usage=nodes(tree).find(n=>n.type==='a'&&n.props.href==='https://chatgpt.com/settings/usage'&&typeof n.props.onClick==='function');assert.ok(usage);usage.props.onClick();tree=h.render();resume=h.button(tree,'사용량 확인 후 요청 재개 허용');assert.equal(resume.props.disabled,false);await resume.props.onClick();tree=h.render();assert.equal(h.state.config.chatgpt.accounts[0].usagePaused,false);assert.equal(h.button(tree,'사용량 확인 후 요청 재개 허용'),undefined);assert.equal(h.calls.length,0);
});
test('stale recovery previews local disconnection and runs only a current explicit confirmation',async()=>{
 const ticket='a'.repeat(64),h=harness({accounts:[],active:null,encryptionAvailable:false,status:'error',recovery:{available:true,reason:'stale_owner',ticket}});let recovered=0;h.state.recoverChatgpt=async input=>{assert.deepEqual(input,{ticket,confirmed:true});recovered++;h.setChat({accounts:[],active:null,encryptionAvailable:true,status:'idle'});return true;};
 let tree=h.render();assert.equal(recovered,0);const prepare=h.button(tree,'종료된 연결 복구 준비');assert.ok(prepare);prepare.props.onClick();tree=h.render();assert.match(text(tree),/암호화.*백업/);assert.match(text(tree),/모든.*ChatGPT.*다시 로그인/);assert.match(text(tree),/등록.*모델/);assert.equal(recovered,0);await h.button(tree,'확인하고 로컬 연결 복구').props.onClick();assert.equal(recovered,1);
});
test('active or unverifiable lease never offers recovery and a replaced ticket invalidates an old confirmation',async()=>{
 for(const reason of ['active_owner','unverifiable_owner','unsafe_store','unavailable_authority']){const h=harness({accounts:[],active:null,encryptionAvailable:false,status:'error',recovery:{available:false,reason}}),tree=h.render();assert.equal(h.button(tree,'종료된 연결 복구 준비'),undefined);assert.match(text(tree),/복구/);}
 const h=harness({accounts:[],active:null,encryptionAvailable:false,status:'error',recovery:{available:true,reason:'stale_owner',ticket:'a'.repeat(64)}});let attempts=0;h.state.recoverChatgpt=async()=>{attempts++;return true;};let tree=h.render();h.button(tree,'종료된 연결 복구 준비').props.onClick();tree=h.render();const confirm=h.button(tree,'확인하고 로컬 연결 복구');h.setChat({...h.state.config.chatgpt,recovery:{available:true,reason:'stale_owner',ticket:'b'.repeat(64)}});await confirm.props.onClick();assert.equal(attempts,0);
});
test('rate limits show a prominent usage link and local signout does not claim remote revocation',async()=>{
 const h=harness({accounts:[account({model:'chosen'})],active:'opaque-a',encryptionAvailable:true,status:'error',error:{code:'rate_limited',reason:'한도 도달',action:'사용량 확인'},remoteRevocationConfirmed:false});let tree=h.render();assert.match(text(tree),/한도 도달/);
 assert.ok(nodes(tree).some(n=>n.type==='a'&&n.props.href==='https://chatgpt.com/settings/usage'&&/workspace-button/.test(n.props.className||'')));
 await h.button(tree,'이 컴퓨터에서 연결 지우기').props.onClick();tree=h.render();assert.match(text(tree),/서비스.*해제.*확인되지 않았/);assert.ok(!text(tree).includes('원격 로그아웃 완료'));
});

test('a prepared ChatGPT account is not advertised as in-use while another provider is active',()=>{
 const h=harness({accounts:[account({model:'chosen'})],active:'opaque-a',encryptionAvailable:true,status:'connected'});h.state.config.active='anthropic';assert.ok(!text(h.render()).includes('ChatGPT 플랜 사용 중'));
});
test('a retained model-picker handler cannot update the former account after selection changes',async()=>{
 const h=harness({accounts:[account(),account({recordId:'opaque-b'})],active:'opaque-a',encryptionAvailable:true,status:'connected',authority:'a1'});let tree=h.render();await h.button(tree,'모델 목록 불러오기').props.onClick();tree=h.render();
 const picker=nodes(tree).find(n=>n.type==='select'&&n.props['aria-label']==='ChatGPT 모델');h.setChat({...h.state.config.chatgpt,active:'opaque-b',authority:'a2'});
 await picker.props.onChange({target:{value:'visible-model'}});assert.deepEqual(h.calls,[['list','chatgpt']]);
});

test('local signout notice survives the authority and account removal returned by IPC',async()=>{
 const h=harness({accounts:[account({model:'chosen'})],active:'opaque-a',encryptionAvailable:true,status:'connected',authority:'a1'});h.state.signOutChatgpt=async()=>{h.setChat({accounts:[],active:null,encryptionAvailable:true,status:'idle',authority:'a2',remoteRevocationConfirmed:false});return true;};
 let tree=h.render();await h.button(tree,'이 컴퓨터에서 연결 지우기').props.onClick();h.render();tree=h.render();assert.match(text(tree),/이 컴퓨터의 연결을 지웠습니다/);assert.match(text(tree),/서비스.*해제.*확인되지 않았/);
});
test('polling stops after the pending connection becomes connected',()=>{
 const h=harness({accounts:[],active:null,encryptionAvailable:true,status:'waiting'});h.render();assert.equal(h.timers.size,1);h.setChat({accounts:[account()],active:'opaque-a',encryptionAvailable:true,status:'connected'});h.render();assert.equal(h.timers.size,0);
});

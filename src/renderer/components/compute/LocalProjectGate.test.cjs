const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function fixture(){const file=path.join(__dirname,'SharedProjectPanel.tsx');assert(fs.existsSync(file),'authenticated local project gate is missing');const values=[];let index=0;const react={createElement:(type,props,...children)=>({type,props:{...props,children}}),useState:initial=>{const i=index++;if(values[i]===undefined)values[i]=initial;return[values[i],value=>values[i]=value];}};const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m.require=name=>name==='react'?{__esModule:true,default:react,...react}:{};m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.React,esModuleInterop:true}}).outputText,file);const render=props=>{index=0;return m.exports.LocalProjectGate({mode:'local_host',...props});};return{render};}
function nodes(t){return t&&typeof t==='object'?[t,...[].concat(t.props?.children||[]).flatMap(nodes)]:[];}
for(const [status,code] of [[409,'local_project_location_recovery_required'],[409,'other_conflict'],[401,'local_project_location_recovery_required'],[403,'local_project_location_recovery_required']])test(`authenticated project gate ${status} offers recovery only for fixed location code ${code}`,async()=>{const f=fixture();let selected=0,recovered=0;const props={projects:[{id:'p',path:'/saved/project',role:'owner'}],busy:false,onSelect:async()=>{selected++;throw {status,code,message:'폴더 확인 필요'};},onReconnect:async()=>{recovered++;},onLogout:async()=>{}};let list=nodes(f.render(props));assert.equal(selected,0,'rendering never auto-selects saved projects');const select=list.find(n=>n.type==='select');await select.props.onChange({target:{value:'p'}});list=nodes(f.render(props));const recover=list.find(n=>n.type==='button'&&n.props.children.includes('프로젝트 폴더 다시 확인'));assert.equal(Boolean(recover),status===409&&code==='local_project_location_recovery_required');assert.equal(list.some(n=>n.type==='input'&&n.props.type==='password'),false);if(recover){await recover.props.onClick();assert.equal(recovered,1);}});
test('ordinary selection is explicit and busy actions refuse dispatch',async()=>{const f=fixture();let calls=0;const props={projects:[{id:'p',path:'/saved/project',role:'owner'}],busy:true,onSelect:async()=>{calls++;},onReconnect:async()=>{},onLogout:async()=>{}};const select=nodes(f.render(props)).find(n=>n.type==='select');assert.equal(select.props.disabled,true);await select.props.onChange({target:{value:'p'}});assert.equal(calls,0);});
test('cancelled native reconnection retains the authenticated project gate for another explicit attempt',async()=>{const f=fixture();let attempts=0,logout=0;const props={projects:[{id:'p',path:'/saved/project',role:'owner'}],busy:false,onSelect:async()=>{throw{status:409,code:'local_project_location_recovery_required',message:'중단된 재연결을 다시 확인하세요.'};},onReconnect:async()=>{attempts++;},onLogout:async()=>{logout++;}};await nodes(f.render(props)).find(n=>n.type==='select').props.onChange({target:{value:'p'}});await nodes(f.render(props)).find(n=>n.type==='button'&&n.props.children.includes('프로젝트 폴더 다시 확인')).props.onClick();const after=nodes(f.render(props));assert.equal(attempts,1);assert.equal(logout,0);assert.equal(after.find(n=>n.type==='select').props.value,'p');assert(after.find(n=>n.type==='button'&&n.props.children.includes('프로젝트 폴더 다시 확인')));});

// A rejected placeholder transition used to leave the select on p, preventing a native same-option retry.
test('a 503 project selection can reset to the placeholder without dispatch and explicitly retry the same project',async()=>{
 const f=fixture(),calls=[];
 const props={projects:[{id:'p',path:'/saved/project',role:'owner'}],busy:false,onSelect:async id=>{calls.push(id);if(calls.length===1)throw{status:503,message:'일시적으로 프로젝트를 열지 못했습니다.'};},onReconnect:async()=>{},onLogout:async()=>{}};
 const select=()=>nodes(f.render(props)).find(n=>n.type==='select');
 await select().props.onChange({target:{value:'p'}});
 assert.equal(select().props.value,'p');assert(nodes(f.render(props)).some(n=>n.props?.role==='alert'));
 await select().props.onChange({target:{value:''}});
 assert.equal(select().props.value,'','placeholder reset is accepted after a failed selection');
 assert.deepEqual(calls,['p'],'empty selection performs no project request');
 assert.equal(nodes(f.render(props)).some(n=>n.props?.role==='alert'),false,'reset retires the transient error');
 await select().props.onChange({target:{value:'p'}});
 assert.deepEqual(calls,['p','p']);assert.equal(select().props.value,'p');
 assert.equal(nodes(f.render(props)).some(n=>n.props?.role==='alert'),false);
});

test('fixed 409 recovery keeps selection until an explicit idle reset clears recovery without dispatch',async()=>{
 const f=fixture(),calls=[];
 const props={projects:[{id:'p',path:'/saved/project',role:'owner'}],busy:false,onSelect:async id=>{calls.push(id);throw{status:409,code:'local_project_location_recovery_required',message:'폴더 확인 필요'};},onReconnect:async()=>{throw Error('재연결 확인 실패');},onLogout:async()=>{}};
 const select=()=>nodes(f.render(props)).find(n=>n.type==='select');
 await select().props.onChange({target:{value:'p'}});
 await nodes(f.render(props)).find(n=>n.type==='button'&&n.props.children.includes('프로젝트 폴더 다시 확인')).props.onClick();
 assert.equal(select().props.value,'p');
 props.busy=true;await select().props.onChange({target:{value:''}});
 assert.equal(select().props.value,'p','busy reset cannot retire recovery ownership');
 props.busy=false;await select().props.onChange({target:{value:''}});
 assert.equal(select().props.value,'');assert.deepEqual(calls,['p']);
 assert.equal(nodes(f.render(props)).some(n=>n.props?.role==='alert'),false);
 assert.equal(nodes(f.render(props)).some(n=>n.type==='button'&&n.props.children.includes('프로젝트 폴더 다시 확인')),false);
});

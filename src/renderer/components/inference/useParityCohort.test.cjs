const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file,mocks={}){const name=path.resolve(__dirname,file);if(!fs.existsSync(name))return {};const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=ref=>mocks[ref]??req(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
function harness(){let cursor=0;const slots=[],effects=[];return {react:{useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return [slots[i],v=>slots[i]=typeof v==='function'?v(slots[i]):v];},useRef:initial=>{const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((v,j)=>v!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>slots[i].cleanup=fn());}}},render:fn=>{cursor=0;const r=fn();effects.splice(0).forEach(fn=>fn());return r;}};}
const settle=()=>new Promise(setImmediate);
const helpers=load('parityCohort.ts',{'./flowPackageRelease':load('flowPackageRelease.ts')});
const image=(id,sha='a'.repeat(64))=>({image_uuid:id,sha256:sha,relative_path:`ok/${id}.png`,file_name:`${id}.png`,file_path:`/s/ok/${id}.png`,valid:1});
function fixture(resolve){
 const h=harness(),data=new Map(),writes=[];let context={workspace_id:'w',project_id:'p',actor_id:'a',mode:'local'},generation=0,subscriber;
 const args={projectId:'p',projectDir:'/project',source:'/s',task:'segmentation',labelset:'default',deliveryKey:'d',legacyImages:[]};
 const m=load('useParityCohort.ts',{react:h.react,'./parityCohort':helpers,'./flowPackageRelease':load('flowPackageRelease.ts'),'../../services/api':{api:{library:{resolve}},getApiPersistenceIdentity:()=> 'local',getProjectContext:()=>context,getProjectContextGeneration:()=>generation,subscribeProjectContext:fn=>{subscriber=fn;return()=>{};}}});
 assert.equal(typeof m.useParityCohort,'function');
 const old=Object.getOwnPropertyDescriptor(global,'localStorage');Object.defineProperty(global,'localStorage',{configurable:true,value:{getItem:key=>data.get(key)??null,setItem:(key,value)=>{data.set(key,value);writes.push([key,value]);}}});
 const key=helpers.parityCohortStorageKey({backend:'local',workspace:'w',actor:'a',project:'p',projectDir:'/project',source:'/s',task:'segmentation',labelset:'default'});
 return {render:()=>h.render(()=>m.useParityCohort(args)),args,data,writes,key,authority:actor=>{context={...context,actor_id:actor};generation++;subscriber?.(context);},close:()=>{if(old)Object.defineProperty(global,'localStorage',old);else delete global.localStorage;}};
}
test('reload resolves saved bytes, retains changed entries and saves neither an empty loading state nor failed resolution',async()=>{
 let fail=false;const f=fixture(async saved=>{if(fail)throw new Error('offline');return {results:saved.map(row=>({...row,status:row.image_uuid==='b'?'changed':'found',current:{...row,valid:1,file_path:'/current/a.png'},candidates:[]}))};});
 try{f.data.set(f.key,JSON.stringify([image('a'),image('b')]));assert.equal(f.render().ready,false);assert.equal(f.writes.length,0);await settle();const state=f.render();assert.equal(state.picks[0].file_path,'/current/a.png');assert.equal(state.unresolved[0].image_uuid,'b');assert.equal(state.ready,true);
 const before=f.data.get(f.key);fail=true;state.retry();f.render();await settle();assert.equal(f.render().ready,false);assert.match(f.render().error,/offline/);assert.equal(f.data.get(f.key),before);
 }finally{f.close();}
});
test('late responses and actions from the previous authority cannot enter the next actor namespace',async()=>{
 const pending=[];const f=fixture(saved=>new Promise(resolve=>pending.push({saved,resolve})));
 try{f.data.set(f.key,JSON.stringify([image('a')]));const old=f.render();f.authority('b');f.render();pending[0].resolve({results:[{...image('a'),status:'found',current:image('a'),candidates:[]}]});await settle();assert.equal(f.render().picks.length,0);old.pick(image('old'));assert.equal(f.render().picks.length,0);pending[1].resolve({results:[]});await settle();assert.equal(f.render().ready,true);assert.equal(JSON.parse(f.data.get(f.key)).length,1);}finally{f.close();}
});
test('legacy paths are persisted independently and an explicit empty selection survives reload',async()=>{
 const f=fixture(async()=>{throw Object.assign(new Error('no revision'),{status:409});});
 try{f.args.legacyImages=[{file_path:'/s/a.png'},{file_path:'/s/b.png'}];f.render();await settle();let state=f.render();assert.equal(state.mode,'unavailable');assert.equal(state.paths.length,2);state.clear();f.render();assert.equal(f.data.get(f.key+':paths'),'[]');state.retry();f.render();await settle();assert.equal(f.render().paths.length,0);}finally{f.close();}
});
test('damaged saved selections remain recoverable and cannot be silently overwritten',async()=>{
 for(const raw of ['', '{broken',JSON.stringify([image('a'),{image_uuid:'missing-fields'}])]){
  const f=fixture(async saved=>({results:saved.map(row=>({...row,status:'found',current:{...row,valid:1},candidates:[]}))}));
  try{f.data.set(f.key,raw);f.render();await settle();const state=f.render();assert.equal(state.ready,false);assert.match(state.error,/저장/);assert.equal(f.data.get(f.key),raw);assert.equal(f.writes.length,0);state.clear();f.render();await settle();assert.equal(f.render().ready,true);assert.equal(f.data.get(f.key),'[]');assert.equal(f.data.get(f.key+':paths'),'[]');}finally{f.close();}
 }
 const f=fixture(async()=>{throw Object.assign(new Error('no revision'),{status:409});});
 try{const raw=JSON.stringify(['/s/a.png',3]);f.data.set(f.key+':paths',raw);f.render();await settle();assert.equal(f.render().ready,false);assert.equal(f.data.get(f.key+':paths'),raw);assert.equal(f.writes.length,0);}finally{f.close();}
});
test('a late legacy listing cannot restart an available identity restore or discard a new choice',async()=>{
 let calls=0;const f=fixture(async saved=>{calls++;return {results:saved.map(row=>({...row,status:'found',current:{...row,valid:1},candidates:[]}))};});
 try{f.args.legacyReady=false;f.render();await settle();let state=f.render();assert.equal(state.ready,true);state.pick(image('a'));f.render();f.args.legacyReady=true;f.args.legacyImages=[{file_path:'/s/a.png'}];f.render();await settle();state=f.render();assert.equal(calls,1);assert.equal(state.picks.length,1);}finally{f.close();}
});
test('an unavailable revision waits for legacy images without resetting the identity restore',async()=>{
 let calls=0;const f=fixture(async()=>{calls++;throw Object.assign(new Error('no revision'),{status:409});});
 try{f.args.legacyReady=false;f.render();await settle();assert.equal(f.render().ready,false);f.args.legacyReady=true;f.args.legacyImages=[{file_path:'/s/a.png'},{file_path:'/s/b.png'}];f.render();await settle();const state=f.render();assert.equal(state.ready,true);assert.equal(state.paths.length,2);assert.equal(calls,1);}finally{f.close();}
});
test('an explicit refresh rechecks saved identities and refuses callbacks from the earlier request',async()=>{
 const pending=[];const f=fixture(saved=>new Promise(resolve=>pending.push({saved,resolve})));
 try{f.data.set(f.key,JSON.stringify([image('a')]));f.render();pending[0].resolve({results:[{...image('a'),status:'found',current:image('a'),candidates:[]}]});await settle();const old=f.render();assert.equal(old.picks.length,1);f.args.refreshKey=1;assert.equal(f.render().ready,false);assert.equal(pending.length,2);old.pick(image('old'));assert.equal(f.render().picks.length,0);assert.equal(JSON.parse(f.data.get(f.key)).length,1);pending[1].resolve({results:[{...image('a'),status:'changed',current:image('a','b'.repeat(64)),candidates:[]}]});await settle();const next=f.render();assert.equal(next.picks.length,0);assert.equal(next.unresolved.length,1);}finally{f.close();}
});

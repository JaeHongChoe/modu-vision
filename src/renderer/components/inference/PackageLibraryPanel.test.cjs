const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=t=>Array.isArray(t)?t.flatMap(nodes):t&&typeof t==='object'?[t,...nodes(t.props?.children)]:[];
async function reopened(changes={}){
 let cursor=0,dirty=true,tree;const slots=[],effects=[];
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},useEffect(fn,deps){const i=cursor++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){slots[i]={deps};effects.push(fn);}}};
 const scope={current:{key:'owned-project'}},Review=()=>null;
 const row={package_id:'converted',name:'Accepted CPU precision control',package_path:'/project/exports/flows/converted',integrity:'verified',scope_matches:true,approval_present:true,runtime:{device:'openvino:CPU'},parity:{status:'not_run'},optimization_jobs:[],...changes};
 const mocks={'react':react,'../../services/api':{api:{dataset:{getImages:async()=>({items:[]})}}},'../../services/productDeliveryApi':{productDeliveryApi:{packages:async()=>({packages:[row],selected_package_id:'converted'})}},'../runtime/useDeliveryScope':{useDeliveryScope:()=>({scope,key:'owned-project'})},'../runtime/deliveryContracts':{},'../training/useTaskHandoff':{useTaskHandoff:()=>null},'./RuntimeFlowReviewPanel':{RuntimeFlowReviewPanel:Review}};
 const file=path.join(__dirname,'PackageLibraryPanel.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);m.require=n=>mocks[n]??req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 for(let i=0;i<5;i++){if(dirty){cursor=0;dirty=false;tree=m.exports.PackageLibraryPanel({sourceFolder:'/source',task:'classification'});effects.splice(0).forEach(fn=>fn());}await new Promise(setImmediate);}
 return {review:nodes(tree).find(n=>n.type===Review),row};
}
test('a saved selected precision package reopens its separate full-flow review',async()=>{
 const {review,row}=await reopened();assert.ok(review,'the persisted package needs a review path after app reload');assert.equal(review.props.packagePath,row.package_path);
});
test('failed, foreign, unapproved and original CPU packages cannot open converted review',async()=>{
 for(const change of [{integrity:'failed'},{scope_matches:false},{approval_present:false},{runtime:{device:'cpu'}}])assert.equal((await reopened(change)).review,undefined);
});

// Execute the real panel effect and its real packageHandoff validation. React
// scheduling and the API boundary are inert; no DOM, HTTP, worker or model runs.
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function selectionFixture(options={}){
 let cursor=0,dirty=true,tree,mounted=true,props={sourceFolder:'/owned-source',task:'classification',refreshKey:0};
 const slots=[],effects=[],selected=[],selectCalls=[],catalogCalls=[];
 const scope={current:{key:'owned-project'}};
 let handoff={kind:'export',jobId:'converted',taskKey:'owned-task',selectionId:'selection-1'};
 const target={package_id:'converted',name:'Original converted package',package_path:'/owned-project/exports/flows/converted',
  manifest_sha256:'a'.repeat(64),integrity:'verified',scope_matches:true,approval_present:false,
  runtime:{device:'openvino:CPU'},parity:{status:'not_run'},optimization_jobs:[],...options.target};
 const catalog={packages:[target],selected_package_id:'converted',scope:{project_id:'owned-project',source_dataset_path:'/owned-source',task:'classification'},...options.catalog};
 const react={
  useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return[slots[i],value=>{slots[i]=typeof value==='function'?value(slots[i]):value;dirty=true;}];},
  useEffect(fn,deps){const i=cursor++;if(JSON.stringify(slots[i]?.deps)!==JSON.stringify(deps)){const previous=slots[i];slots[i]={deps};effects.push(()=>{previous?.cleanup?.();slots[i].cleanup=fn();});}},
 };
 const compiled=(file,mocks)=>{const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const req=m.require.bind(m);m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:req(n);
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);return m.exports;};
 const contracts=compiled(path.join(__dirname,'../runtime/deliveryContracts.ts'),{});
 const api={packages:async()=>{catalogCalls.push(true);return options.packages?options.packages():catalog;},
  select:async id=>{selectCalls.push(id);return options.select?options.select(id):target;}};
 const component=compiled(path.join(__dirname,'PackageLibraryPanel.tsx'),{'react':react,
  '../../services/api':{api:{dataset:{getImages:async()=>options.images?options.images():({items:[]})}}},
  '../../services/productDeliveryApi':{productDeliveryApi:api},'../runtime/useDeliveryScope':{useDeliveryScope:()=>({scope,key:scope.current.key})},
  '../runtime/deliveryContracts':contracts,'../training/useTaskHandoff':{useTaskHandoff:()=>handoff},'./RuntimeFlowReviewPanel':{RuntimeFlowReviewPanel:()=>null}}).PackageLibraryPanel;
 const render=()=>{if(!mounted)return;cursor=0;dirty=false;tree=component({...props,onSelected:row=>selected.push(row)});effects.splice(0).forEach(fn=>fn());};
 const flush=async()=>{for(let i=0;i<6;i++){if(dirty)render();await new Promise(setImmediate);}return tree;};
 const unmount=()=>{mounted=false;for(const slot of slots)slot?.cleanup?.();};
 return {target,catalog,selected,selectCalls,catalogCalls,scope,flush,unmount,
  change(next){props={...props,...next};dirty=true;},newHandoff(next){handoff={...handoff,...next};dirty=true;},
  newScope(key){scope.current={key};dirty=true;},all(){return nodes(tree);},
  alerts(){return nodes(tree).filter(n=>n.props?.role==='alert').map(n=>n.props.children);},
  statuses(){return nodes(tree).filter(n=>n.props?.role==='status').map(n=>n.props.children);},
  button(name){return nodes(tree).find(n=>n.type==='button'&&n.props.children===name);},
 };
}
test('same fresh selected handoff reopens the exact verified catalog row without a select POST',async()=>{
 const f=selectionFixture();await f.flush();assert.deepEqual(f.selectCalls,[]);assert.equal(f.selected.length,1);assert.equal(f.selected[0],f.target);
 assert.ok(f.button('선택됨'));assert.deepEqual(f.alerts(),[]);assert.deepEqual(f.statuses(),['작업 센터에서 선택한 패키지 Original converted package 다시 열기 완료']);
});
test('same selected handoff refresh and remount remain read-only',async()=>{
 const f=selectionFixture();await f.flush();f.change({refreshKey:1});await f.flush();f.newHandoff({selectionId:'selection-2'});await f.flush();
 assert.equal(f.catalogCalls.length,3);assert.deepEqual(f.selectCalls,[]);assert.deepEqual(f.selected,[f.target,f.target,f.target]);
 f.unmount();const remount=selectionFixture();await remount.flush();assert.deepEqual(remount.selectCalls,[]);assert.equal(remount.selected[0],remount.target);
});
test('a mismatching or absent selected ID still performs one genuine selection',async()=>{
 for(const selected_package_id of ['other',null,'']){const f=selectionFixture({catalog:{selected_package_id}});await f.flush();assert.deepEqual(f.selectCalls,['converted']);assert.equal(f.selected[0],f.target);assert.deepEqual(f.alerts(),[]);}
});
test('same selected handoff never bypasses fresh integrity or source validation',async()=>{
 for(const target of [{integrity:'failed'},{scope_matches:false}]){const f=selectionFixture({target});await f.flush();assert.deepEqual(f.selectCalls,[]);assert.deepEqual(f.selected,[]);assert.equal(f.alerts().length,1);assert.equal(f.statuses().length,0);}
});
test('missing requested package is refused without selection even if its ID is saved',async()=>{
 const f=selectionFixture({catalog:{packages:[]}});await f.flush();assert.deepEqual(f.selectCalls,[]);assert.deepEqual(f.selected,[]);assert.match(f.alerts()[0],/보관함에서 찾지 못했습니다/);
});
test('a genuine mismatching selection response still needs integrity and source validation',async()=>{
 for(const changed of [{integrity:'failed'},{scope_matches:false}]){const f=selectionFixture({catalog:{selected_package_id:null},select:async()=>({...f.target,...changed})});await f.flush();assert.deepEqual(f.selectCalls,['converted']);assert.deepEqual(f.selected,[]);assert.equal(f.alerts().length,1);}
});
test('catalog and actual selection errors remain visible instead of using a stale row',async()=>{
 for(const options of [{packages:async()=>{throw new Error('Original catalog unavailable');}},{catalog:{selected_package_id:null},select:async()=>{throw new Error('Original select refused');}}]){
  const f=selectionFixture(options);await f.flush();assert.deepEqual(f.selected,[]);assert.equal(f.alerts().length,1);assert.match(f.alerts()[0],/Original/);
 }
});
test('a catalog completed after unmount or scope change cannot select or publish',async()=>{
 for(const mutation of ['unmount','scope']){const waiting=deferred(),f=selectionFixture({packages:()=>waiting.promise,catalog:{selected_package_id:null}});await f.flush();
  if(mutation==='unmount')f.unmount();else f.scope.current={key:'foreign-project'};
  waiting.resolve(f.catalog);await f.flush();assert.deepEqual(f.selectCalls,[]);assert.deepEqual(f.selected,[]);
 }
});
test('late real selection response after unmount or scope change cannot publish',async()=>{
 for(const mutation of ['unmount','scope']){const waiting=deferred(),f=selectionFixture({select:()=>waiting.promise,catalog:{selected_package_id:null}});await f.flush();assert.deepEqual(f.selectCalls,['converted']);
  if(mutation==='unmount')f.unmount();else f.scope.current={key:'foreign-project'};
  waiting.resolve(f.target);await f.flush();assert.deepEqual(f.selected,[]);assert.equal(f.statuses().length,0);
 }
});
test('explicit already-selected reopen button remains an intentional select action',async()=>{
 const f=selectionFixture();await f.flush();assert.deepEqual(f.selectCalls,[]);await f.button('선택됨').props.onClick();await f.flush();assert.deepEqual(f.selectCalls,['converted']);assert.equal(f.selected.at(-1),f.target);
});

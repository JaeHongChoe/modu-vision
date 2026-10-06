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

const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),test=require('node:test'),ts=require('typescript');
function harness(){
 const file=path.join(__dirname,'DatasetWorkflowPanel.tsx'),source=process.env.MV_READINESS_BASE?require('node:child_process').execFileSync('git',['show','HEAD:src/renderer/components/dataset/DatasetWorkflowPanel.tsx'],{cwd:path.resolve(__dirname,'../../../..'),encoding:'utf8'}):fs.readFileSync(file,'utf8');
 let cursor=0,generation=0,calls=0,resolve;const slots=[],effects=[],listeners=[],splitRequests=[];
 const project={project:{id:'p',project_dir:'p',task:'classification',source_dataset_dir:'s'},projectDir:'p',task:'classification',isProjectBusy:false,openImageForLabeling:async()=>true},dataset={folderPath:'s',hasSelectedFolder:true,annotationsChanged:async()=>{},loadImages:async()=>{}},annotation={reviewerName:'fixture',setReviewerName:()=>{}};
 const react={createElement:(type,props,...children)=>({type,props:props||{},children:children.flat()}),useState:initial=>{const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],v=>{slots[i]=typeof v==='function'?v(slots[i]):v;}];},useRef:initial=>{const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},useEffect:(fn,deps)=>{const i=cursor++;if(!slots[i]||deps.some((d,j)=>d!==slots[i].deps[j])){slots[i]?.cleanup?.();slots[i]={deps};effects.push(()=>{slots[i].cleanup=fn();});}}};
 const store=state=>{const fn=()=>state;fn.getState=()=>state;return fn;};
 const api={list:async()=>({items:[],total:0}),duplicates:async()=>({duplicates:[]}),savedSplit:async()=>({availability:'unavailable'}),split:(...args)=>{calls++;splitRequests.push(args);return new Promise(r=>{resolve=r;});}};
 const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);m.require=name=>name==='react'?react:name.endsWith('/stores/useStepSheetStore')?{useStepSheetStore:Object.assign(select=>select({open:null}),{getState:()=>({open:null,show(){}})})}:name.includes('services/api')?{resolveApiUrl:p=>p,getProjectContextGeneration:()=>generation,getApiPersistenceIdentity:()=> 'local',subscribeProjectContext:fn=>{listeners.push(fn);return()=>{};}}:name.includes('datasetWorkflow')?{datasetWorkflow:api,workflowError:e=>e.message}:name.includes('useDatasetStore')?{useDatasetStore:store(dataset)}:name.includes('useProjectStore')?{useProjectStore:store(project)}:name.includes('useAnnotationStore')?{useAnnotationStore:store(annotation)}:name.includes('projectPreferences')?{projectPreferences:{read:async()=>({tag_colors:{}})}}:name.includes('hostAdapter')?{host:{}}:name.includes('ProjectPreferencePanel')?{ProjectPreferencePanel:()=>null}:name.includes('ExternalMaskExchangePanel')?{ExternalMaskExchangePanel:()=>null}:Module.prototype.require.call(m,name);
 m._compile(ts.transpileModule(source,{compilerOptions:{jsx:ts.JsxEmit.React,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,esModuleInterop:true}}).outputText,file);
 const render=()=>{cursor=0;const tree=m.exports.DatasetWorkflowPanel();effects.splice(0).forEach(fn=>fn());return tree;};const nodes=tree=>{const found=[];const walk=n=>{if(n&&typeof n==='object'){found.push(n);n.children?.forEach(walk);}};walk(tree);return found;};
 return{render,nodes,dataset,splitRequests,calls:()=>calls,advance:()=>{generation++;listeners.forEach(fn=>fn());},resolve:v=>resolve(v)};
}
const tick=()=>new Promise(setImmediate);
async function setup(){global.window={addEventListener(){},removeEventListener(){}};const h=harness();let tree=h.render();h.nodes(tree).find(n=>n.type==='button'&&n.children.includes('이미지 검토·그룹 분할·라벨 교환')).props.onClick();h.render();await tick();return h;}
test('retained split callback refuses changed project generation before submission',async()=>{const h=await setup();const action=h.nodes(h.render()).find(n=>n.type==='button'&&n.children.includes('분할 미리보기')).props.onClick;h.advance();await action();assert.equal(h.calls(),0);});
test('late split preview from an earlier generation cannot repopulate current panel',async()=>{const h=await setup();const pending=h.nodes(h.render()).find(n=>n.type==='button'&&n.children.includes('분할 미리보기')).props.onClick();h.advance();h.render();h.resolve({assignments:{},split:{train:1,val:1,test:1},group_count:999,duplicates:[],applied:false,apply_supported:true});await pending;assert.ok(!h.nodes(h.render()).some(n=>n.children.some(c=>typeof c==='string'&&c.includes('독립 그룹 999'))));});
test('split preview waits while a reload still restores the saved source, so the restored scope cannot wipe its result',async()=>{const h=await setup();const preview=()=>h.nodes(h.render()).find(n=>n.type==='button'&&n.children.includes('분할 미리보기'));h.dataset.isLoading=true;assert.equal(preview().props.disabled,true);await tick();assert.equal(preview().props.disabled,true);h.dataset.isLoading=false;h.render();await tick();assert.equal(preview().props.disabled,false);});
test('workflow focus can open the existing label exchange without applying any labels',async()=>{
 const h=await setup();const tree=h.render(),nodes=h.nodes(tree);
 assert.ok(nodes.some(n=>n.type==='button'&&n.props['data-workflow-opens']==='workflow-label-exchange'));
 const exchange=nodes.find(n=>n.props.id==='workflow-label-exchange');assert.ok(exchange);assert.equal(exchange.type,'summary');
 assert.equal(exchange.props.tabIndex,0,'the disclosure stays in keyboard tab order');assert.ok(nodes.some(n=>n.children.includes('가져오기 미리보기')));
 assert.equal(h.calls(),0,'opening the exchange does not save or import');
});

function setRatios(h,values){
 for(const [index,label] of ['학습','검증','시험'].entries()){
  const input=h.nodes(h.render()).find(n=>n.type==='input'&&n.props['aria-label']===label+' 그룹 분할 비율');
  input.props.onChange({target:{value:String(values[index])}});
 }
}
const button=(h,name)=>h.nodes(h.render()).find(n=>n.type==='button'&&n.children.includes(name));
// Regression: an admitted apply owns its completion through a same-source
// annotation summary refresh. Loading is an entry fence, not a new source.
async function appliedSplitWithHeldSummary(){
 const h=await setup();let finishSummary,started=false;
 h.dataset.annotationsChanged=async()=>{started=true;h.dataset.isLoading=true;h.render();await new Promise(resolve=>{finishSummary=resolve;});h.dataset.isLoading=false;h.render();};
 button(h,'분할 미리보기').props.onClick();
 h.resolve({assignments:{},split:{train:1,val:1,test:0},group_count:2,duplicates:[],applied:false,apply_supported:true,qualification:{sha256:'a'.repeat(64),group_by:['lot'],seed:42}});await tick();
 button(h,'분할 적용').props.onClick();
 h.resolve({assignments:{},split:{train:1,val:1,test:0},group_count:2,duplicates:[],applied:true,apply_supported:true,backup_version_id:'backup-owned-apply'});await tick();
 assert.equal(started,true,'the actual component entered its post-apply annotation refresh');
 return {h,finish:async()=>{finishSummary();await tick();await tick();}};
}
test('an admitted grouped apply stays busy while its same-source summary refresh is held',async()=>{
 const {h,finish}=await appliedSplitWithHeldSummary();
 try{assert.ok(h.nodes(h.render()).some(node=>node.props.role==='status'&&node.children.includes('처리 중…')),'a loading fence cannot mark the admitted apply complete');}
 finally{await finish();}
});
test('a completed grouped apply retains its backup outcome after same-source summary refresh',async()=>{
 const {h,finish}=await appliedSplitWithHeldSummary();await finish();
 assert.ok(h.nodes(h.render()).some(node=>node.props.role==='status'&&node.children.join('').includes('backup-owned-apply')),'the current successful apply must retain its concrete backup receipt');
});
for(const [name,values,reason] of [
 ['sum below 100',[60,20,10],/합계가 100%가 아닙니다/],
 ['all zero',[0,0,0],/합계가 100%가 아닙니다/],
 ['sum outside engine tolerance',[70.0002,20,10],/합계가 100%가 아닙니다/],
 ['negative and over 100 despite a total of 100',[-1,101,0],/0~100% 범위/],
 ['NaN',[NaN,20,10],/0~100% 범위/],
 ['infinity',[Infinity,0,0],/0~100% 범위/],
 ['negative infinity',[-Infinity,0,0],/0~100% 범위/],
])test('invalid split percentages show Korean recovery and submit no preview or apply: '+name,async()=>{
 const h=await setup();setRatios(h,values);
 const alerts=h.nodes(h.render()).filter(n=>n.props.role==='alert');assert.equal(alerts.length,1);
 const text=alerts[0].children.join('');assert.match(text,reason);assert.match(text,/확인하세요|맞추세요/);
 for(const name of ['분할 미리보기','분할 적용']){
  const action=button(h,name);assert.equal(action.props.disabled,true);
  action.props.onClick();await tick();
 }
 assert.equal(h.calls(),0,'invalid ratios cannot reach either server action even through its handler');
});

for(const values of [[70,20,10],[50.5,49.5,0],[100,0,0],[0,100,0],[0,0,100],[70.00005,20,10]])
test('valid decimal/zero split percentages retain original request semantics: '+values.join('/'),async()=>{
 const h=await setup();setRatios(h,[60,20,10]);setRatios(h,values);
 assert.equal(h.nodes(h.render()).filter(n=>n.props.role==='alert').length,0,'correcting ratios clears the derived refusal');
 const preview=button(h,'분할 미리보기');assert.equal(preview.props.disabled,false);
 const inputs=h.nodes(h.render()).filter(n=>n.type==='input'&&String(n.props['aria-label']).endsWith(' 그룹 분할 비율'));
 assert.ok(inputs.every(input=>input.props.step==='any'),'fractional percentages remain supported');
 preview.props.onClick();assert.equal(h.calls(),1);
 assert.deepEqual(h.splitRequests[0],[['lot'],values.map(n=>n/100),false,'fixture',undefined]);
 h.resolve({assignments:{},split:{train:1,val:1,test:0},group_count:2,duplicates:[],applied:false,apply_supported:true,qualification:{sha256:'a'.repeat(64),group_by:['lot'],seed:42}});await tick();
 const apply=button(h,'분할 적용');assert.equal(apply.props.disabled,false);apply.props.onClick();
 assert.deepEqual(h.splitRequests[1],[['lot'],values.map(n=>n/100),true,'fixture','a'.repeat(64)]);
 h.resolve({assignments:{},split:{train:1,val:1,test:0},group_count:2,duplicates:[],applied:true,apply_supported:true});await tick();
});

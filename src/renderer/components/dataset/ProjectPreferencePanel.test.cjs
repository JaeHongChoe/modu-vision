const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=tree=>Array.isArray(tree)?tree.flatMap(nodes):tree&&typeof tree==='object'?[tree,...nodes(tree.props?.children)]:[];
const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
function panel(){
 let cursor=0,dirty=false,tree,effects=[];const slots=[],writes=[];let resolve;const pending=new Promise(yes=>{resolve=yes;});
 const project={id:'owned-project',active_labelset_id:'second',source_dataset_dir:'/owned/images'};
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{const next=typeof value==='function'?value(slots[i]):value;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const previous=slots[i];slots[i]={deps,cleanup:previous?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}}};
 const file=path.join(__dirname,'ProjectPreferencePanel.tsx'),loaded=new Module(file,module);loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);const original=loaded.require.bind(loaded);
 const prefs={schema_version:1,revision:1,tag_colors:{ready:'#24a7cb'},model_flags:{},labelset_flags:{default:['baseline'],second:[]}};
 loaded.require=name=>name==='react'?react:name==='../../stores/useProjectStore'?{useProjectStore:select=>select({project})}:name==='../../stores/useAnnotationStore'?{useAnnotationStore:select=>select({reviewerName:'fixture-reviewer'})}:name==='../../services/api'?{api:{flowchart:{modelCatalog:async()=>({models:[],total:0})}}}:name==='../../services/projectPreferences'?{projectPreferences:{read:()=>pending,update:async(revision,actor,changes)=>{writes.push({revision,actor,changes});return {...prefs,revision:2,labelset_flags:{...prefs.labelset_flags,...changes.labelset_flags}};}}}:original(name);
 loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 const prior=global.window;global.window={dispatchEvent(){}};
 function render(){cursor=0;dirty=false;tree=loaded.exports.ProjectPreferencePanel();effects.splice(0).forEach(fn=>fn());return tree;}
 async function settle(){for(let i=0;i<12;i++){if(dirty||!tree)render();await new Promise(setImmediate);if(!dirty)return tree;}throw Error('panel did not settle');}
 return {resolve:()=>resolve(prefs),writes,settle,controls:()=>nodes(tree),open:async()=>{nodes(tree).find(n=>n.type==='button'&&n.props['aria-expanded']!==undefined).props.onClick();await settle();},close:()=>{for(const slot of slots)slot?.cleanup?.();global.window=prior;}};
}
test('preference inputs wait for initial data so a late read cannot erase a just-entered labelset flag',async()=>{
 const p=panel();try{await p.settle();await p.open();for(const name of ['색상을 지정할 태그','태그 색상','라벨 세트 플래그','플래그를 지정할 모델','모델 사용자 플래그']){const c=p.controls().find(n=>n.props?.['aria-label']===name);assert(c);assert.equal(c.props.disabled,true,`${name}: input must await preferences, not accept text which a late read replaces`);}
 p.resolve();await p.settle();const input=p.controls().find(n=>n.props?.['aria-label']==='라벨 세트 플래그');assert.equal(input.props.disabled,false);input.props.onChange({target:{value:'second-pass'}});await p.settle();p.controls().filter(n=>n.type==='button'&&n.props.children==='저장')[1].props.onClick();await p.settle();assert.deepEqual(p.writes,[{revision:1,actor:'fixture-reviewer',changes:{labelset_flags:{second:['second-pass']}}}]);assert.equal(p.controls().find(n=>n.props?.['aria-label']==='라벨 세트 플래그').props.value,'second-pass');}finally{p.close();}
});

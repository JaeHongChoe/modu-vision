const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const nodes=tree=>Array.isArray(tree)?tree.flatMap(nodes):tree&&typeof tree==='object'?[tree,...nodes(tree.props?.children)]:[];
const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
function browser(options={}){
 let cursor=0,dirty=false,tree,effects=[],epoch=0;let mounted=true;const lateUpdates=[],unavailable=[];const slots=[],calls=[],picked=[],selected=new Set(['image-0']);
 const image=i=>({image_uuid:`image-${i}`,file_name:'part.png',file_path:`/images/${i}/part.png`,relative_path:`${i}/part.png`,valid:1,tags:['tag-A'],label:'OK',split:'test'});
 const react={useState(initial){const i=cursor++;if(!(i in slots))slots[i]=typeof initial==='function'?initial():initial;return[slots[i],value=>{if(!mounted)lateUpdates.push(i);const next=typeof value==='function'?value(slots[i]):value;if(!Object.is(next,slots[i])){slots[i]=next;dirty=true;}}];},useRef(initial){const i=cursor++;return slots[i]??(slots[i]={current:initial});},useCallback(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps))slots[i]={deps,fn};return slots[i].fn;},useEffect(fn,deps){const i=cursor++;if(!same(slots[i]?.deps,deps)){const previous=slots[i];slots[i]={deps,cleanup:previous?.cleanup};effects.push(()=>{slots[i].cleanup?.();slots[i].cleanup=fn();});}},useLayoutEffect(){cursor++;}};
 const file=path.join(__dirname,'ImageLibraryBrowser.tsx'),loaded=new Module(file,module);loaded.filename=file;loaded.paths=Module._nodeModulePaths(__dirname);const original=loaded.require.bind(loaded);const utilityFile=path.join(__dirname,'../../utils/virtualWindow.ts'),utility=new Module(utilityFile,module);utility.filename=utilityFile;utility._compile(ts.transpileModule(fs.readFileSync(utilityFile,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,utilityFile);
 react.useSyncExternalStore=(_subscribe,getSnapshot)=>{cursor++;return getSnapshot();};
 loaded.require=name=>name==='../../utils/virtualWindow'?utility.exports:name==='react'?react:name==='../../services/api'?{getProjectContextGeneration:()=>epoch,subscribeProjectContext:()=>()=>{},resolveApiUrl:x=>x,api:{library:{images:async query=>{calls.push({...query});if(options.respond)return options.respond(query,calls.length);const start=query.cursor?120:0;return{items:Array.from({length:120},(_,i)=>image(i+start)),next_cursor:query.cursor?'page-3':'page-2',complete_page:true,scanned_to:null};}}}}:original(name);
 loaded._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
 const previousWindow=global.window;global.window={setTimeout:()=>1,clearTimeout(){}};
 function render(){cursor=0;dirty=false;tree=loaded.exports.ImageLibraryBrowser({selectedIds:selected,onPick:item=>picked.push(item),onUnavailable:reason=>unavailable.push(reason)});effects.splice(0).forEach(fn=>fn());return tree;}
 async function settle(){for(let i=0;i<12;i++){if(dirty||!tree)render();await new Promise(setImmediate);if(!dirty)return tree;}throw Error('component did not settle');}
 return{calls,picked,selected,lateUpdates,unavailable,settle,render,contextChange:()=>{epoch++;dirty=true;},controls:()=>nodes(tree),change:async(label,value)=>{const input=nodes(tree).find(n=>n.props?.['aria-label']===label);assert(input,`production filter ${label} missing`);input.props.onChange({target:{value}});return settle();},scrollWithoutSettling:value=>nodes(tree).find(n=>n.props?.role==='list').props.onScroll({currentTarget:{scrollTop:value}}),scroll:async value=>{nodes(tree).find(n=>n.props?.role==='list').props.onScroll({currentTarget:{scrollTop:value}});return settle();},close(){if(!mounted)return;mounted=false;for(const slot of slots)slot?.cleanup?.();global.window=previousWindow;}};
}
test('production metadata controls send tag/product/Lot together and clear empty filters',async()=>{
 const b=browser();try{await b.settle();await b.change('태그','검사완료');await b.change('제품','PCB-A');await b.change('Lot','LOT-42');const query=b.calls.at(-1);assert.equal(query.tag,'검사완료');assert.equal(query.product,'PCB-A');assert.equal(query.lot,'LOT-42');assert.equal(query.cursor,null);assert.equal(query.limit,120);await b.change('Lot','');assert.equal(b.calls.at(-1).lot,undefined);assert.equal(b.calls.at(-1).tag,'검사완료');}finally{b.close();}
});
test('metadata change starts from first page after pagination and preserves UUID selection',async()=>{
 const b=browser();try{await b.settle();await b.scroll(176*120);assert(b.calls.some(q=>q.cursor==='page-2'),'fixture must exercise actual pagination');for(const [label,value]of [['태그','tag-B'],['제품','PCB-B'],['Lot','LOT-B']]){const before=b.calls.length;await b.change(label,value);assert.equal(b.calls[before].cursor,null,'changed cohort must discard old cursor');}
 const chosen=b.controls().find(n=>n.props?.role==='listitem'&&n.props['aria-pressed']);assert(chosen,'existing selected UUID remains selected after filter change');chosen.props.onClick();assert.equal(b.picked[0].image_uuid,'image-0');assert.equal(b.picked[0].file_path,'/images/0/part.png');assert.deepEqual([...b.selected],['image-0']);}finally{b.close();}
});

test('a cohort change at pagination boundary never sends the prior cursor with new filters',async()=>{
 const b=browser();try{await b.settle();const before=b.calls.length;b.scrollWithoutSettling(176*120);await b.change('Lot','new-cohort');const changed=b.calls.slice(before).filter(query=>query.lot==='new-cohort');assert(changed.length);assert(changed.every(query=>query.cursor===null),'old cursor must not race first-page reset');}finally{b.close();}
});

const pageAnswer=(start,next=null)=>({items:Array.from({length:120},(_,i)=>({image_uuid:`image-${start+i}`,file_name:`part-${start+i}.png`,file_path:`/images/${start+i}/part.png`,relative_path:`${start+i}/part.png`,valid:1,tags:[],label:'OK',split:'test'})),next_cursor:next,complete_page:true,scanned_to:null});
const retryButton=b=>{const button=b.controls().find(n=>n.type==='button'&&n.props?.children==='다시 불러오기');assert(button,'failed request needs an explicit retry button');return button;};
test('a failed first page retries its exact current query without losing selected UUIDs',async()=>{
 const b=browser({respond:async(query,count)=>{if(count===1)throw Error('temporary first-page failure');return pageAnswer(0);}});
 try{await b.settle();retryButton(b).props.onClick();await b.settle();assert.equal(b.calls.length,2);assert.deepEqual(b.calls[1],b.calls[0]);assert.deepEqual([...b.selected],['image-0']);assert(b.controls().some(n=>n.props?.role==='listitem'&&n.props['aria-pressed']));assert(!b.controls().some(n=>n.props?.children==='temporary first-page failure'));}finally{b.close();}
});
test('a failed next page retries that cursor and appends once while preserving the first page',async()=>{
 const b=browser({respond:async(query,count)=>{if(count===1)return pageAnswer(0,'page-2');if(count===2)throw Error('temporary page-2 failure');return pageAnswer(120);}});
 try{await b.settle();await b.scroll(176*120);assert.equal(b.calls[1].cursor,'page-2');retryButton(b).props.onClick();await b.settle();assert.equal(b.calls.length,3);assert.deepEqual(b.calls[2],b.calls[1]);assert(b.controls().some(n=>n.type==='span'&&Array.isArray(n.props.children)&&n.props.children[0]==='240'&&n.props.children[1]==='장 표시'),'both pages remain counted exactly once');assert.deepEqual([...b.selected],['image-0']);}finally{b.close();}
});
test('changing filters after a failed cursor retries only the new first-page cohort',async()=>{
 const b=browser({respond:async(query,count)=>{if(count===1)return pageAnswer(0,'page-2');if(count===2||count===3)throw Error('temporary query failure');return pageAnswer(0);}});
 try{await b.settle();await b.scroll(176*120);await b.change('Lot','new-lot');retryButton(b).props.onClick();await b.settle();assert.equal(b.calls.length,4);assert.deepEqual(b.calls[3],b.calls[2]);assert.equal(b.calls[3].lot,'new-lot');assert.equal(b.calls[3].cursor,null);}finally{b.close();}
});
test('an unmounted validated browser never delivers a late 409 callback or state update',async()=>{
 let reject;const pending=new Promise((resolve,no)=>{reject=no;});const b=browser({respond:()=>pending});
 await b.settle();b.close();reject(Object.assign(Error('old project revision unavailable'),{status:409}));await new Promise(setImmediate);await new Promise(setImmediate);
 assert.deepEqual(b.unavailable,[],'old browser must not switch its former parent into path-only mode');assert.deepEqual(b.lateUpdates,[]);
});

test('error type is a separate filter and changing it discards the previous cursor',async()=>{
 const b=browser();try{await b.settle();await b.scroll(176*120);await b.change('오류 종류','annotation');assert.equal(b.calls.at(-1).error,'annotation');assert.equal(b.calls.at(-1).cursor,null);await b.change('이미지 상태','valid');assert.equal(b.calls.at(-1).state,'valid');assert.equal(b.calls.at(-1).error,'annotation');await b.change('오류 종류','');assert.equal(b.calls.at(-1).error,undefined);}finally{b.close();}
});
test('an accepted context change refreshes the list and discards the former namespace response',async()=>{
 let reject;const first=new Promise((_yes,no)=>{reject=no;});const b=browser({respond:(_query,count)=>count===1?first:pageAnswer(120)});
 try{await b.settle();b.contextChange();await b.settle();assert.equal(b.calls.length,2,'new namespace must request its own list');reject(Object.assign(Error('former namespace'),{status:409}));await b.settle();assert.deepEqual(b.unavailable,[]);assert(b.controls().some(n=>n.props?.role==='listitem'&&n.props.title==='120/part.png'));}finally{b.close();}
});

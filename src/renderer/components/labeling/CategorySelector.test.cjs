// The class bar renders the project's classes as chips with the drawing class pressed; example names are dashed
// buttons (folded behind '예시 이름' in the simple view, inline in the full view, none under a label book); a project
// with no class yet says which name its first labels get.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const EXAMPLES=[{id:0,name:'OK',color:'#10b981'},{id:1,name:'Solder Bridge',color:'#3b82f6'},{id:2,name:'Scratch',color:'#f59e0b'},{id:3,name:'Crack',color:'#ef4444'},{id:4,name:'Void',color:'#a855f7'},{id:5,name:'Contamination',color:'#06b6d4'}];
const store=state=>Object.assign(select=>select?select(state):state,{getState:()=>state});
function render({categories,active,classes={},viewMode='simple',task='detection',labelbookVersion=null,annotations=[]}){
 const file=path.join(__dirname,'CategorySelector.tsx'),m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(__dirname);const req=m.require.bind(m);
 const annotation={categories,activeCategory:categories.find(c=>c.name===active),setActiveCategory(){},addCategory(){},annotations,labelbookVersion,advanceOnBox:false,setAdvanceOnBox(){}};
 const mocks={'../../stores/useAnnotationStore':{useAnnotationStore:store(annotation),DEFAULT_CATEGORIES:EXAMPLES,isStandInCategory:c=>c.standIn===true},
  '../../stores/useDatasetStore':{useDatasetStore:store({classes})},'../../stores/useProjectStore':{useProjectStore:store({viewMode,task})}};
 m.require=n=>Object.hasOwn(mocks,n)?mocks[n]:req(n);
 m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText,file);
 return renderToStaticMarkup(React.createElement(m.exports.CategorySelector));
}
const chips=html=>[...html.matchAll(/<button aria-pressed="(true|false)"[^>]*>.*?<span>([^<]+)<\/span>/g)].map(([,pressed,name])=>`${name}${pressed==='true'?'*':''}`);
const dashed=html=>[...html.matchAll(/title="예시 이름입니다[^"]*"[^>]*>.*?<span>([^<]+)<\/span>/g)].map(([,name])=>name);
const QA={categories:[EXAMPLES[0],{id:1,name:'defect',color:'#3b82f6'}],active:'defect',classes:{defect:24}};
const NOTE=/불량 클래스가 아직 없어 그리는 라벨은 &#x27;defect&#x27; 클래스로 저장됩니다/;

test('the QA project: OK and defect are the chips, defect is pressed, example names fold in the simple view',()=>{
 const html=render(QA);
 assert.deepEqual(chips(html),['OK','defect*']);assert.deepEqual(dashed(html),[]);
 assert.match(html,/예시 이름 5개/);assert.doesNotMatch(html,NOTE);assert.doesNotMatch(html,/Solder Bridge/);
});

test('the full view offers the five example names inline as dashed additions, never as chips',()=>{
 const html=render({...QA,viewMode:'full'});
 assert.deepEqual(chips(html),['OK','defect*']);assert.deepEqual(dashed(html),['Solder Bridge','Scratch','Crack','Void','Contamination']);
 assert.doesNotMatch(html,/예시 이름 5개/);
});

test('the note shows exactly while the drawing class is the store\'s stand-in, whatever other chips there are',()=>{
 const stand={categories:[EXAMPLES[0],{id:1,name:'defect',color:'#3b82f6',standIn:true}],active:'defect'};
 assert.match(render({...stand,classes:{}}),NOTE);
 const segmentation=render({...stand,task:'segmentation',classes:{defect_mask:1}});
 assert.match(segmentation,NOTE);assert.doesNotMatch(segmentation,/defect_mask/,'the summary key is not offered as a class');
 assert.match(render({categories:[EXAMPLES[0],{id:1,name:'good',color:'#3b82f6'},{id:2,name:'defect',color:'#f59e0b',standIn:true}],active:'defect',task:'classification',classes:{good:300}}),NOTE,'next to a normal-named class');
 assert.doesNotMatch(render({categories:[EXAMPLES[0],{id:1,name:'scratch',color:'#3b82f6'},{id:2,name:'dent',color:'#f59e0b'}],active:'scratch',classes:{}}),/클래스가 아직 없어/);
 assert.doesNotMatch(render({categories:[EXAMPLES[0],{id:1,name:'defect',color:'#3b82f6'}],active:'defect',classes:{}}),/클래스가 아직 없어/,'a class the person made or drew with');
});

test('under a label book the bar offers no example names and no stand-in note',()=>{
 const html=render({categories:[EXAMPLES[0],{id:2,name:'Scratch',color:'#f59e0b'}],active:'Scratch',labelbookVersion:2,viewMode:'full'});
 assert.deepEqual(chips(html),['OK','Scratch*']);assert.deepEqual(dashed(html),[]);
 assert.match(html,/공유 기준 v2/);assert.doesNotMatch(html,/아직 클래스가 없어|예시 이름/);
});

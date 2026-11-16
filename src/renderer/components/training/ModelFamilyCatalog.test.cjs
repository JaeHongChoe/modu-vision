const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function render({families=[],error='',selectedFamily='ocr',disabled=false,loading=false,viewMode='simple',query=''}={}){
 const filename=path.join(__dirname,'ModelFamilyCatalog.tsx'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);
 let i=0;const states=[families,error,loading,query];m.require=id=>id==='react'?{...React,useState:()=>[states[i++],()=>{}],useEffect:()=>{}}:id==='../../services/api'?{request:()=>{throw Error('no requests during render')}}:id==='../../stores/useProjectStore'?{useProjectStore:select=>select({viewMode})}:id==='./WorkerSupportPanel'?{WorkerSupportPanel:()=>null}:require(id);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 return renderToStaticMarkup(React.createElement(m.exports.ModelFamilyCatalog,{selectedFamily,onSelect:()=>{throw Error('selection must be explicit')},disabled}));
}
const family=task=>({task,label:task+' 모델',model:'실제 모델',architectures:['native'],devices:['cpu'],default_architecture:'native',prerequisite:task+' 정답 자료',remote_training:false,continuation:'weight_initialization',stages:['train','evaluate'],missing_dependencies:[],execution:{native_recipe_stages:['evaluate','predict'],generation_only:false}});
const visible=html=>html.split('<details')[0];
test('all ten selected families expose actual prerequisite and device summary before disclosure',()=>{
 const tasks=['classification','segmentation','detection','anomaly','patch_classification','ocr','rotated_detection','defect_gan','enhancement','rotation'];
 for(const task of tasks){const html=visible(render({families:tasks.map(family),selectedFamily:task}));assert.match(html,new RegExp(task+' 정답 자료'));assert.match(html,/CPU/);assert.doesNotMatch(html,/개별 실행:|Compute|모델 forward/);assert.match(html,/로컬 학습/);assert.match(html,/결과:/);assert.doesNotMatch(html,/학습 준비 완료/);}
});
test('missing family and method dependencies and generation-only restriction are visible',()=>{
 const gan={...family('defect_gan'),missing_dependencies:['gan-runtime'],execution:{native_recipe_stages:['generate'],generation_only:true}};
 const html=visible(render({families:[gan],selectedFamily:'defect_gan'}));assert.match(html,/gan-runtime/);assert.match(html,/검사 플로우 모델로 사용할 수 없습니다/);
 const anomaly={...family('anomaly'),methods:[{method:'dino_synthetic',missing_dependencies:['timm'],prerequisite:'정상 원본',architectures:[],continuation:'weight_initialization'}]};assert.match(visible(render({families:[anomaly],selectedFamily:'anomaly'})),/dino_synthetic.*timm/);
});
test('loading, failed capability load and absent selected family never imply readiness',()=>{
 assert.match(visible(render({loading:true})),/모델 종류를 확인 중/);
 assert.match(visible(render({error:'offline'})),/모델 종류 확인 실패: offline/);
 assert.match(visible(render({families:[family('ocr')],selectedFamily:'rotation'})),/선택 모델의 지원 정보를 확인할 수 없습니다/);
});

test('all catalog choices remain selectable and busy selection stays disabled',()=>{
 const families=['classification','segmentation','detection','anomaly','patch_classification','ocr','rotated_detection','defect_gan','enhancement','rotation'].map(family);
 const html=render({families});assert.equal((html.match(/type="radio"/g)||[]).length,10);
 assert.equal((render({families,disabled:true}).match(/<input [^>]*disabled/g)||[]).length,10);
 const unknown=visible(render({families:[{...family('ocr'),devices:[],execution:undefined}],viewMode:'full'}));assert.match(unknown,/사용 가능한 장치 없음/);assert.match(unknown,/지원 정보 없음/);
});

test('a selected simple model keeps its support summary visible and offers all ten large choices in an explicit change disclosure',()=>{
 const tasks=['classification','segmentation','detection','anomaly','patch_classification','ocr','rotated_detection','defect_gan','enhancement','rotation'];
 const html=render({families:tasks.map(family),selectedFamily:'detection'});
 assert.match(html,/<details[^>]*><summary>모델 종류 바꾸기 · 10종<\/summary>/);
 assert.doesNotMatch(visible(html),/type="radio"/);
 assert.match(visible(html),/객체 박스와 클래스/);assert.match(visible(html),/detection 정답 자료/);
 assert.equal((html.match(/type="radio"/g)||[]).length,10);
 assert.equal((html.match(/checked=""/g)||[]).length,1);
 for(const task of tasks) assert.match(html,new RegExp(task+' 정답 자료'));
 assert.match(html,/객체 박스와 클래스/);assert.match(html,/선택됨/);
 assert.match(render({families:tasks.map(family),viewMode:'full'}),/<details[^>]*open=""[^>]*><summary>모델 종류 바꾸기/);
 assert.match(render({families:tasks.map(family),selectedFamily:null}),/<details[^>]*open=""[^>]*><summary>모델 종류 선택/);
});

test('local catalog search filters by purpose and data, exposes no-results and can be cleared',()=>{
 const families=['classification','detection','ocr'].map(family);
 const filtered=render({families,query:'문자열'});
 assert.equal((filtered.match(/type="radio"/g)||[]).length,1);assert.match(filtered,/ocr 모델/);
 assert.match(filtered,/aria-label="모델 검색 지우기"/);
 const noMatch=render({families,query:'없는 모델'});
 assert.equal((noMatch.match(/type="radio"/g)||[]).length,0);assert.match(noMatch,/검색.*모델.*없/);
 assert.equal((render({families,query:''}).match(/type="radio"/g)||[]).length,3);
});

test('search clearing restores choices and a disabled radio callback cannot dispatch selection',()=>{
 const filename=path.join(__dirname,'ModelFamilyCatalog.tsx'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);
 const states=[['classification','detection','ocr'].map(family),'',false,''];let cursor=0,controls=[],selections=[],focused=0,disabled=false;
 const runtime=require('react/jsx-runtime');
 m.require=id=>id==='react'?{...React,useState:initial=>{const index=cursor++;if(!(index in states))states[index]=initial;return[states[index],value=>{states[index]=value;}];},useEffect:()=>{},useRef:()=>({current:{focus:()=>focused++}})}
  :id==='react/jsx-runtime'?Object.fromEntries(Object.entries(runtime).map(([key,value])=>[key,typeof value==='function'?(type,props,...args)=>{if(['input','button'].includes(type))controls.push({type,props});return value(type,props,...args);}:value]))
  :id==='../../services/api'?{request:()=>{throw Error('no render request')}}:id==='../../stores/useProjectStore'?{useProjectStore:select=>select({viewMode:'simple'})}:id==='./WorkerSupportPanel'?{WorkerSupportPanel:()=>null}:require(id);
 m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
 const run=()=>{cursor=0;controls=[];return renderToStaticMarkup(React.createElement(m.exports.ModelFamilyCatalog,{selectedFamily:'classification',onSelect:task=>selections.push(task),disabled}));};
 run();const search=controls.find(control=>control.props.type==='search');assert.ok(search,'catalog has an owned search field');
 search.props.onChange({currentTarget:{value:'문자열'}});run();assert.equal(controls.filter(control=>control.props.type==='radio').length,1);
 controls.find(control=>control.props['aria-label']==='모델 검색 지우기').props.onClick();run();
 assert.equal(controls.filter(control=>control.props.type==='radio').length,3);assert.equal(focused,1);
 controls.find(control=>control.props.type==='radio'&&control.props.value==='detection').props.onChange({currentTarget:{checked:true}});
 assert.deepEqual(selections,['detection']);
 disabled=true;run();const blocked=controls.find(control=>control.props.type==='radio'&&control.props.value==='ocr');
 assert.equal(blocked.props.disabled,true);blocked.props.onChange({currentTarget:{checked:true}});assert.deepEqual(selections,['detection']);
});

test('simple guidance translates split terms while technical stages and raw prerequisites stay in details',()=>{
 const data={...family('classification'),remote_training:true,prerequisite:'클래스별 이미지와 서로 분리된 train/val/test',execution:{native_recipe_stages:['evaluate','predict','benchmark'],generation_only:false}};
 const html=render({families:[data],selectedFamily:'classification'}),summary=visible(html);
 assert.match(summary,/서로 분리된 학습·검증·시험 사진/);assert.match(summary,/로컬·서버 학습/);
 assert.doesNotMatch(summary,/train\/val\/test|개별 실행:|Compute|forward|지원 정보이며/);
 assert.match(html,/train\/val\/test/);assert.match(html,/모델 forward 계측/);
 const full=visible(render({families:[data],selectedFamily:'classification',viewMode:'full'}));assert.match(full,/평가.*단일 예측.*모델 forward 계측/);assert.doesNotMatch(full,/Compute/);
});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
function render({families=[],error='',selectedFamily='ocr',disabled=false,loading=false,viewMode='simple'}={}){
 const filename=path.join(__dirname,'ModelFamilyCatalog.tsx'),m=new Module(filename,module);m.filename=filename;m.paths=Module._nodeModulePaths(__dirname);
 let i=0;const states=[families,error,loading];m.require=id=>id==='react'?{...React,useState:()=>[states[i++],()=>{}],useEffect:()=>{}}:id==='../../services/api'?{request:()=>{throw Error('no requests during render')}}:id==='../../stores/useProjectStore'?{useProjectStore:select=>select({viewMode})}:id==='./WorkerSupportPanel'?{WorkerSupportPanel:()=>null}:require(id);
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
 const html=render({families});assert.equal((html.match(/<button /g)||[]).length,10);
 assert.equal((render({families,disabled:true}).match(/<button [^>]*disabled/g)||[]).length,10);
 const unknown=visible(render({families:[{...family('ocr'),devices:[],execution:undefined}],viewMode:'full'}));assert.match(unknown,/사용 가능한 장치 없음/);assert.match(unknown,/지원 정보 없음/);
});

test('simple guidance translates split terms while technical stages and raw prerequisites stay in details',()=>{
 const data={...family('classification'),remote_training:true,prerequisite:'클래스별 이미지와 서로 분리된 train/val/test',execution:{native_recipe_stages:['evaluate','predict','benchmark'],generation_only:false}};
 const html=render({families:[data],selectedFamily:'classification'}),summary=visible(html);
 assert.match(summary,/서로 분리된 학습·검증·시험 사진/);assert.match(summary,/로컬·서버 학습/);
 assert.doesNotMatch(summary,/train\/val\/test|개별 실행:|Compute|forward|지원 정보이며/);
 assert.match(html,/train\/val\/test/);assert.match(html,/모델 forward 계측/);
 const full=visible(render({families:[data],selectedFamily:'classification',viewMode:'full'}));assert.match(full,/평가.*단일 예측.*모델 forward 계측/);assert.doesNotMatch(full,/Compute/);
});

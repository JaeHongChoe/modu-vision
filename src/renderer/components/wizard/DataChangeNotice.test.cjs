// Audit F-09 / A3: the data-change notice names only what changed and offers a version restore only when it can work.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(){const name=path.join(__dirname,'DataChangeNotice.tsx');const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(__dirname);
 const mocks={'../../services/api':{request:async()=>null},'../../stores/useProjectStore':{useProjectStore:{getState:()=>({setStep:async()=>{}})}},'../../stores/dataVersionRequest':{requestDataVersionPanel:()=>{}}};
 mod.require=ref=>ref in mocks?mocks[ref]:require(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return mod.exports;}
const base={job_id:'j',state:'changed',reason:'',dataset_version_id:'v',dataset_version_name:'Training input',counts:{added:0,removed:0,modified:0,label_files:0},
 split_changed:false,classes_changed:false,labelset_changed:false,examples:{added:[],removed:[],modified:[]},restorable:false};
test('the summary lists only the parts that changed',()=>{
 const {changeSummary}=load();
 assert.equal(changeSummary({...base,counts:{added:1,removed:2,modified:0,label_files:3}}),'이미지 추가 1장 · 이미지 삭제 2장 · 라벨 파일 3개 바뀜');
 assert.equal(changeSummary({...base,split_changed:true,classes_changed:true}),'이미지 클래스 지정 바뀜 · 분할 바뀜');
 assert.equal(changeSummary(base),'');
});
test('the restore button exists only for a restorable change and goes through step 1\'s confirmed restore',()=>{
 const source=fs.readFileSync(path.join(__dirname,'DataChangeNotice.tsx'),'utf8');
 assert.match(source,/\{changes\.restorable && <button/);
 assert.match(source,/requestDataVersionPanel\(changes\.dataset_version_id\); go\(1\);/);
 assert.match(source,/지금 데이터로 다시 학습 \(3단계\)/);
});

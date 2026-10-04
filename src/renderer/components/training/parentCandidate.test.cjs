const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file,mocks={}){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref in mocks?mocks[ref]:ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{fileName:name,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
test('parent label shows saved history and dates with the same actual identity',()=>{
 const {parentCandidateLabel}=load('parentCandidate.ts');const row={job_id:'job-parent-1',checkpoint_sha256:'a'.repeat(64),summary:{model_recorded_at:'2026-09-30T00:00:00Z',completed_at:'2026-09-30T01:00:00Z',training_metrics:{best_metric:.91,val_loss:.25,val_accuracy:.9}}};
 const label=parentCandidateLabel(row);assert.match(label,/job-parent-1/);assert.match(label,/저장 모델 지표=0.9100/);assert.match(label,/검증 손실=0.2500/);assert.match(label,/모델 기록 2026-09-30 00:00 UTC/);assert.match(label,/완료 기록 2026-09-30 01:00 UTC/);assert.match(label,/SHA aaaaaaaaaaaa/);
});
test('legacy, malformed dates and nonfinite metrics stay unconfirmed without leaking arbitrary metadata',()=>{
 const {parentCandidateLabel}=load('parentCandidate.ts');const label=parentCandidateLabel({job_id:'x',checkpoint_sha256:'b'.repeat(64),summary:{model_recorded_at:'2026-09-30',completed_at:null,training_metrics:{val_loss:NaN,val_accuracy:Infinity,accuracy:0,private_training_note:1}}});
 assert.match(label,/accuracy=0.000/);assert.match(label,/완료일 미확인/);assert.match(label,/모델 기록일 미확인/);assert.doesNotMatch(label,/NaN|Infinity|private_training_note/);
 const old=parentCandidateLabel({job_id:'x',checkpoint_sha256:'b'.repeat(64)});assert.match(old,/학습 지표 미확인/);
});
test('shared specialist parent selector displays the same bounded context and leaves the option value unchanged',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');let i=0;
 const parent={job_id:'0123456789abcdef0123456789abcdef',checkpoint_sha256:'c'.repeat(64),summary:{model_recorded_at:'2026-09-30T00:00:00Z',completed_at:null,training_metrics:{val_loss:.2}}};
 const useState=initial=>[i++===0?[parent]:initial,()=>{}];
 const {WarmStartSelector}=load('WarmStartSelector.tsx',{react:{...React,useState,useEffect(){}},'../../services/api':{request:()=>Promise.reject(Error('render only'))},'../../stores/useProjectStore':{useProjectStore:sel=>sel({projectDir:'/controlled',project:{source_dataset_dir:'/controlled'}})}});
 const html=renderToStaticMarkup(React.createElement(WarmStartSelector,{family:'ocr',datasetPath:'/controlled',value:parent.job_id,onChange(){},disabled:false}));
 assert.match(html,/검증 손실=0.2000/);assert.match(html,/완료일 미확인/);assert.match(html,/지표는 저장된 학습 기록/);assert.match(html,/value="0123456789abcdef0123456789abcdef"/);
});
test('saved checkpoint and latest epoch metrics are labelled as different records',()=>{
 const {parentCandidateLabel}=load('parentCandidate.ts');const label=parentCandidateLabel({job_id:'ocr-parent',checkpoint_sha256:'d'.repeat(64),summary:{model_recorded_at:null,completed_at:null,training_metrics:{saved_val_loss:.3,saved_angular_mae_deg:12,val_loss:.9}}});
 assert.match(label,/저장 모델 검증 손실=0.3000/);assert.match(label,/저장 모델 각도 MAE=12.00/);assert.match(label,/검증 손실=0.9000/);
});

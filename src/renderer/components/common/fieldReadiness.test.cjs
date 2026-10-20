// Audit F-10/F-11/A6/B1: the field conditions all at once, the single-model export note, and small-test honesty.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript'),test=require('node:test');
function load(file){const name=path.resolve(__dirname,file);const mod=new Module(name,module);mod.filename=name;mod.paths=Module._nodeModulePaths(path.dirname(name));
 mod.require=ref=>ref.startsWith('.')?load(path.join(path.dirname(file),ref+'.ts')):require(ref);
 mod._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return mod.exports;}
const {fieldConditions,singleModelExportNote}=load('fieldReadiness.ts');
const {wilsonInterval,accuracyNote,smallTestWarning}=load('../evaluation/sampleConfidence.ts');
const empty={models:[],model_evaluations:[],flows:[],flow_evaluations:[],approvals:[],packages:[]};
const model=id=>({job_id:id,task:'classification',scope_matches:true,checkpoint_state:'current',data_state:'current'});
test('classification counts the approval test images and every later condition from the report',()=>{
 const rows=fieldConditions({task:'classification',classSplitCounts:{OK:{test:6},NG:{test:6}},impact:empty});
 assert.deepEqual(rows.map(row=>[row.id,row.state]),[['test_images','todo'],['two_models','todo'],['approved_model','todo'],['flow_evaluated','todo'],['package','todo']]);
 assert.match(rows[0].text,/각 8장 이상 \(지금 정상 6 · 불량 6\)/);
 const ready=fieldConditions({task:'classification',classSplitCounts:{OK:{test:8},NG:{test:9}},impact:{...empty,models:[model('a'),model('b')],
  approvals:[{job_id:'a',scope_matches:true,state:'unverified'}],flows:[{scope_matches:true,state:'current'}],packages:[{scope_matches:true,state:'unverified'}]}});
 assert.ok(ready.every(row=>row.state==='done'));
});
test('without a report the conditions say "확인 필요", and stale approvals or data never count',()=>{
 assert.ok(fieldConditions({task:'detection',classSplitCounts:null,impact:null}).every(row=>row.state==='unknown'));
 const stale=fieldConditions({task:'classification',classSplitCounts:{OK:{test:8},NG:{test:8}},impact:{...empty,models:[model('a'),{...model('b'),data_state:'changed'}],
  approvals:[{job_id:'a',scope_matches:true,state:'revalidation_required'}]}});
 assert.equal(stale.find(row=>row.id==='two_models').state,'todo');assert.equal(stale.find(row=>row.id==='approved_model').state,'todo');
});
test('the single-model export says what it does not prove',()=>{
 assert.match(singleModelExportNote('a',{...empty,models:[{...model('a'),data_state:'changed'}]}),/학습 뒤 데이터가 바뀌었습니다 · 아직 승인하지 않은 모델입니다/);
 assert.equal(singleModelExportNote('a',{...empty,models:[model('a')],approvals:[{job_id:'a',scope_matches:true,state:'unverified'}]}),null);
 assert.equal(singleModelExportNote(null,empty),null);
});
test('a small test set reads as a range, not a guarantee (Wilson 95%)',()=>{
 const four=wilsonInterval(4,4);assert.ok(four.low>.5&&four.low<.52&&four.high===1);
 assert.equal(accuracyNote(4,4),'정확도 100% · 실제는 51~100% 사이일 수 있음 (시험 4장)');
 assert.match(accuracyNote(95,100),/^정확도 95% \(95% 신뢰 범위 89~98%\)$/);
 assert.equal(wilsonInterval(0,0),null);assert.equal(smallTestWarning(40),null);assert.match(smallTestWarning(4),/4장뿐이라 한 장마다 정확도가 25%p씩/);
});
test('steps 1, 4 and 6 show the conditions and the export note',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..',file),'utf8');
 assert.match(read('dataset/DatasetStudio.tsx'),/\{hasSelectedFolder && <FieldReadinessPanel \/>\}/);
 assert.match(read('evaluation/EvaluationStudio.tsx'),/<FieldReadinessPanel \/>/);
 assert.match(read('evaluation/EvaluationStudio.tsx'),/accuracyNote\(right, sampleCounts\.all\)/);
 assert.match(read('inference/InferenceCenterStudio.tsx'),/<SingleModelExportNotice jobId=\{jobId \|\| null\} \/>/);
 assert.match(fs.readFileSync(path.join(__dirname,'FieldReadinessPanel.tsx'),'utf8'),/예제\(합성 데이터\)라 승인·현장 적용 대상이 아닙니다/);
});

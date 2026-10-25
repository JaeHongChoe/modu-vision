// Step 3's item 1 gives one training count: the split step 1 keeps (train · val · test), with the unlabeled images named
// as left out until they are labeled and the split is re-applied; with no split it names the step-1 action. The team
// review's own count is never shown as images to train, and once the screen holds a finished run the 'not yet run' note
// says what the check covers instead.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const source=fs.readFileSync(path.join(__dirname,'TrainingPreparationPanel.tsx'),'utf8');
const start=source.indexOf('export function projectImagesLine');const body=source.slice(start,source.indexOf('\n}',start)+2);
const m=new Module('projectImagesLine.ts',module);
m._compile(ts.transpileModule(body.replace('export function','function')+'\nmodule.exports={projectImagesLine};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,'projectImagesLine.ts');
const {projectImagesLine}=m.exports;
const QA={name:'QA 새 검출',sourceImages:54,totalImages:24,unlabeledImages:30,split:{train:19,val:2,test:3},splitState:'saved',core:true};
const NOTE='라벨 없는 30장은 학습에서 빠집니다. 라벨을 달거나 초안을 채택한 뒤 1단계에서 분할을 다시 적용해야 학습·검증·시험으로 나뉘어 들어갑니다 (다시 적용하면 기존 시험 이미지도 다시 나뉩니다).';

test('the QA project: one count, the saved split, and the 30 unlabeled images named as left out',()=>{
 const {line,note}=projectImagesLine(QA);
 assert.equal(line,'QA 새 검출 · 원본 54장 · 학습 19 · 검증 2 · 시험 3장');assert.equal(note,NOTE);
 assert.doesNotMatch(line,/학습 대상|24/,'never the labeled total, which also counts the test images');
});

test('an automatic split says it is not saved; with no split to train on the line names the step-1 action, never "trainable"',()=>{
 assert.equal(projectImagesLine({...QA,splitState:'automatic'}).line,'QA 새 검출 · 원본 54장 · 학습 19 · 검증 2 · 시험 3장 (자동 · 저장 안 됨)');
 const empty={...QA,split:{train:0,val:0,test:0}};
 assert.equal(projectImagesLine({...empty,splitState:'saved'}).line,'QA 새 검출 · 원본 54장 · 라벨 24장 · 저장된 분할에 없는 라벨 이미지가 있어 1단계에서 분할을 다시 적용해야 합니다');
 assert.equal(projectImagesLine({...empty,splitState:'stale'}).line,'QA 새 검출 · 원본 54장 · 라벨 24장 · 저장한 분할이 폴더와 달라 1단계에서 분할을 다시 저장해야 합니다');
 for(const splitState of [null,'source_folders','automatic'])assert.equal(projectImagesLine({...empty,splitState}).line,'QA 새 검출 · 원본 54장 · 라벨 24장 · 분할 전 (1단계에서 분할을 적용하세요)');
 for(const splitState of ['saved','stale',null])assert.doesNotMatch(projectImagesLine({...empty,splitState}).line,/학습 가능/,'training cannot start without a split');
 assert.deepEqual(projectImagesLine({...QA,loading:true}),{line:'QA 새 검출 · 이미지 수를 읽는 중…',note:null});
 assert.deepEqual(projectImagesLine({...QA,failed:true}),{line:'QA 새 검출 · 이미지를 읽지 못했습니다 · 1단계에서 데이터를 다시 가져오세요',note:null},'a failed import is not a split problem');
});

test('with no label yet the line asks for labels first, and before any split the note never speaks of re-applying',()=>{
 const fresh={...QA,totalImages:0,unlabeledImages:54,split:{train:0,val:0,test:0},splitState:'source_folders'};
 assert.deepEqual(projectImagesLine(fresh),{line:'QA 새 검출 · 원본 54장 · 라벨 0장 · 2단계에서 라벨을 단 뒤 1단계에서 분할을 적용하세요',note:null});
 const presplit=projectImagesLine({...QA,split:{train:0,val:0,test:0},splitState:null});
 assert.equal(presplit.note,'라벨 없는 30장은 학습에서 빠집니다. 라벨을 달거나 초안을 채택한 뒤 1단계에서 분할을 적용해야 학습·검증·시험으로 나뉘어 들어갑니다.');
 assert.match(projectImagesLine({...QA,split:{train:0,val:0,test:0},splitState:'saved'}).note,/다시 적용해야.*기존 시험 이미지도 다시 나뉩니다/,'a split was saved before');
});

test('another family stops at the originals; no unlabeled images means no note; a missing name never prints undefined',()=>{
 assert.deepEqual(projectImagesLine({...QA,core:false}),{line:'QA 새 검출 · 원본 54장',note:null});
 assert.equal(projectImagesLine({...QA,unlabeledImages:0}).note,null);
 assert.doesNotMatch(projectImagesLine({...QA,name:undefined}).line,/undefined/);
});

test('the panel uses the helper for the project task only, and once trained says what the check covers instead of "not yet run"',()=>{
 assert.match(source,/core:family===task&&!datasetPath,loading:dataset\.isLoading,failed:Boolean\(dataset\.importError\)\}\);/);
 assert.match(source,/\{source\?images\.line:'프로젝트에 원본 이미지를 연결하세요\.'\}/);
 assert.match(source,/<p className="mt-1 text-slate-400">\{trained\?'이 검사는 다음 학습의 준비 상태만 확인합니다\. 모델 품질은 4단계 평가에서 확인합니다\.':'실제 학습·추론 실행과 모델 품질 승인은 아직 확인하지 않았습니다\.'\}<\/p>/,'true in every state, for any model checked');
 const controller=fs.readFileSync(path.join(__dirname,'TrainingController.tsx'),'utf8');
 assert.match(controller,/<TrainingPreparationPanel [^\n]*trained=\{Boolean\(completedJobId\)\} \/>/,'the training screen passes its finished run');
 assert.match(controller,/const completedJobId = status === 'completed' \? jobId : null;/);
});

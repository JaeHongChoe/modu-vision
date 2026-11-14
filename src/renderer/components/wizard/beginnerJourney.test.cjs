const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function journey(){
 const file=path.join(__dirname,'beginnerJourney.ts');assert.ok(fs.existsSync(file),'beginner journey presentation helper exists');
 const mod=new Module(file,module);mod.filename=file;mod.paths=Module._nodeModulePaths(__dirname);
 mod._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return mod.exports;
}
test('all six steps belong to exactly one of four ordered beginner goals',()=>{
 const {BEGINNER_GOALS,beginnerGoalForStep}=journey();
 assert.deepEqual(BEGINNER_GOALS.map(goal=>goal.steps),[[1],[2,3],[4],[5,6]]);
 assert.deepEqual([1,2,3,4,5,6].map(step=>beginnerGoalForStep(step).id),['photos','teach','teach','check','use','use']);
});
test('goal entry preserves the current substep and otherwise selects the first unfinished step',()=>{
 const {BEGINNER_GOALS,beginnerGoalEntry,beginnerGoalComplete}=journey(),teach=BEGINNER_GOALS[1];
 assert.equal(beginnerGoalEntry(teach,3,[]),3);
 assert.equal(beginnerGoalEntry(teach,1,[]),2);
 assert.equal(beginnerGoalEntry(teach,4,[2]),3);
 assert.equal(beginnerGoalEntry(teach,4,[2,3]),2,'a complete goal remains revisitable');
 assert.equal(beginnerGoalComplete(teach,[1,2,4,5,6]),false);
 assert.equal(beginnerGoalComplete(teach,[2,3]),true);
});
test('navigation labels describe opening the control, including steps previously worded as execution',()=>{
 const {navigationActionLabel}=journey();
 for(const [action,ko,en] of [
  [{label:'모델 학습 시작 (3단계)',step:3,target:'workflow-training'},'학습 화면 열기','Open training'],
  [{label:'초안 만들기',step:2,target:'draft-create'},'초안 만들기 설정 열기','Open draft settings'],
  [{label:'시험 이미지로 평가하기 (4단계)',step:4},'평가 화면 열기','Open evaluation'],
  [{label:'분할 저장하기 (1단계)',step:1},'사진 나누기 설정 열기','Open image split settings'],
  [{label:'검사 시작 버튼으로 이동',step:6,target:'workflow-package'},'검사 결과·사용 준비 열기','Open inspection and use setup'],
  [{label:'준비도 다시 확인',step:3,kind:'refresh'},'준비도 다시 확인','Check readiness again'],
  [{label:'학습 오류 확인',step:3,kind:'details'},'학습 오류 확인','Check training error'],
  [{label:'바뀐 내용과 되돌리기',step:4,kind:'details'},'바뀐 내용과 복구 방법 확인','Check changes and recovery options'],
 ]){assert.equal(navigationActionLabel(action,true),ko);assert.equal(navigationActionLabel(action,false),en);}
});

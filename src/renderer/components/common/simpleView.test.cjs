// The simple view: a first run shows the few controls each step needs, every control stays reachable (더 보기 or
// 모두 보기), the step names read as work, the footer has one primary action, and the test launches declare the full
// view so the existing suites keep every control on screen.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test'),ts=require('typescript');
const read=file=>fs.readFileSync(path.resolve(__dirname,'..','..','..','..',file),'utf8');

function readViewMode(){
 const source=read('src/renderer/stores/useProjectStore.ts');
 const start=source.indexOf('export function readViewMode'),end=source.indexOf('\n}\n',start)+3;
 const js=ts.transpileModule("const VIEW_MODE_KEY='modu:view-mode:v1';\n"+source.slice(start,end),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};new Function('exports',js)(exports);return exports.readViewMode;
}

test('the stored choice wins, then the page URL, else the simple view',()=>{
 const fn=readViewMode();const storage=value=>({getItem:()=>value});
 assert.equal(fn(storage(null)),'simple');
 assert.equal(fn(storage(null),'?port=1&view=full'),'full');
 assert.equal(fn(storage('simple'),'?view=full'),'simple');
 assert.equal(fn(storage('garbage'),'?view=huge'),'simple');
 assert.equal(fn({getItem(){throw new Error('blocked');}},'?view=full'),'full');
});

test('the e2e harness and the Electron test launch declare the full view',()=>{
 const harness=read('scripts/e2e/fixtures/harness.cjs');
 assert.match(harness,/index\.html\?port=\$\{port\}&view=full/);assert.match(harness,/MV_VIEW_MODE: 'full'/);
 const main=read('src/main/index.ts');assert.match(main,/process\.env\.MV_VIEW_MODE === 'full'/);assert.match(main,/query: \{ view: viewMode \}/);
 assert.ok(!read('src/preload/index.ts').includes('viewModeDefault'),'the renderer reads the view mode from the page URL, not the bridge');
});

test('the header names the steps as work, keeps the older names for assistive tech and groups the rest under 더 보기',()=>{
 const header=read('src/renderer/components/wizard/WizardHeader.tsx');
 for(const name of ["nameKo: '데이터 준비'","nameKo: '라벨 달기'","nameKo: '모델 학습'","nameKo: '결과 평가'","nameKo: '검사 흐름 만들기'","nameKo: '검사 실행'"])assert.ok(header.includes(name),`missing ${name}`);
 assert.match(header,/legacyKo: '플로우차트'/);assert.match(header,/sr-only">\(\{s\.legacyKo\}\)/);
 assert.match(header,/full \? \(ko \? '간단히 보기'/);assert.match(header,/<summary[^>]*>\{ko \? '더 보기'/);
 assert.ok(header.indexOf("'서버 관리'")>0&&header.indexOf("'데이터·판정 이력'")>0,'the grouped controls stay reachable');
});

test('the footer has one primary action, one status block and quiet step arrows',()=>{
 const footer=read('src/renderer/components/wizard/WizardFooter.tsx');
 assert.equal((footer.match(/workspace-button--primary/g)||[]).length,1);
 assert.match(footer,/role="status"/);assert.match(footer,/왜 그런가요\?/);assert.match(footer,/aria-label="다음 단계 보기"/);
 assert.ok(!footer.includes('>다음 단계 보기<'),'a second text button competes with the primary action');
 assert.match(footer,/\$\{blocker\} → \$\{readiness\.nextAction\.label\}/);
});

test('the labeling screen starts folded in the simple view and the label set is one line until opened',()=>{
 const studio=read('src/renderer/components/labeling/LabelingStudio.tsx');
 assert.match(studio,/const focus = focusChoice \?\? viewMode === 'simple';/);assert.match(studio,/collapsed=\{viewMode === 'simple'\}/);
 const bar=read('src/renderer/components/labeling/LabelSetBar.tsx');assert.match(bar,/if \(collapsed\) return <details/);
 assert.match(read('src/renderer/App.tsx'),/viewMode==='full'\?<>/);
 assert.match(read('src/renderer/index.css'),/html\[lang="ko"\] body \{ word-break: keep-all;/);
});

test('the simple view shows three data counts, two tiles and one folder button row; the rest waits behind one line',()=>{
 const studio=read('src/renderer/components/dataset/DatasetStudio.tsx');
 assert.match(studio,/\{!full && <DataReadinessPanel \/>\}/);assert.match(studio,/\{full && <DataReadinessPanel \/>\}/);
 assert.match(studio,/\{full \? <>\s*<button\s*onClick=\{\(\) => setShowGeneratorModal\(true\)\}/,'the generator and version dialogs wait under 더 보기');
 assert.match(studio,/\{full && <>\s*<div className="p-2\.5 bg-\[#1A212E\][^>]*>\s*<div className="flex items-center justify-between">\s*<span[^>]*>\s*\{language === 'ko' \? '적용된 학습 분할'/);
 assert.match(studio,/SPLIT_BADGE_KO\[img\.split\]/,'thumbnail badges read 학습/검증/시험/미분할 in Korean');
 const stats=read('src/renderer/components/dataset/DatasetStatisticsPanel.tsx');
 assert.match(stats,/\{full&&Object\.entries\(data\.assignments\)/);assert.match(stats,/분할별 이미지 보기/);
 const header=read('src/renderer/components/wizard/WizardHeader.tsx');
 assert.match(header,/\{full && <span role="status"/,'the engine badge is a 더 보기 line in the simple view');
 const app=read('src/renderer/App.tsx');
 assert.match(app,/\(viewMode==='full'\|\|workspace==='operator'\)&&<>/);assert.match(app,/운영자 검사 화면/);
 const footer=read('src/renderer/components/wizard/WizardFooter.tsx');
 assert.match(footer,/다음: \$\{readiness\.nextAction\.label\}/);assert.match(footer,/sr-only">\{ko\?` · 완료 입력/);
 assert.ok(!footer.includes('`완료 입력 ${readiness.completedInputs.length} · `'),'the visible status no longer starts with a count');
});

test('the simple view folds the step 3 training location, the step 4 comparison and the second header row; wide monitors get larger type',()=>{
 const training=read('src/renderer/components/training/TrainingController.tsx');
 assert.match(training,/\{full \|\| \(jobId && status !== 'completed'\) \? block : <details/);assert.match(training,/<summary[^>]*><span[^>]*>학습 위치<\/span>/);
 const evaluation=read('src/renderer/components/evaluation/EvaluationStudio.tsx');
 assert.match(evaluation,/<summary[^>]*>현행 모델 · 후보 모델 비교<\/summary>/);assert.match(evaluation,/\{full \? <>\s*<div className="rounded border border-\[#3B5269\]/);
 const header=read('src/renderer/components/wizard/WizardHeader.tsx');
 assert.match(header,/simpleControls\?: React\.ReactNode; simpleMenu\?: React\.ReactNode/);assert.match(header,/\{!full && simpleControls\}/);assert.match(header,/\{simpleMenu\}\s*<\/div>\s*<\/details>\}/);
 const app=read('src/renderer/App.tsx');
 assert.match(app,/<WizardHeader completedSteps=\{doneSteps\} simpleControls=\{workspace==='operator'\?null:<button/);assert.match(app,/\{\(viewMode==='full'\|\|workspace==='operator'\)&&<nav/);
 const css=read('src/renderer/index.css');
 assert.match(css,/@media \(min-width: 1920px\) \{ html \{ font-size: 18px; \} \}/);assert.match(css,/@media \(min-width: 2400px\) \{ html \{ font-size: 20px; \} \}/);
});

test('a wrapped labeling toolbar keeps its height instead of hiding its second line under the next row',()=>{
 const toolbar=read('src/renderer/components/labeling/LabelingToolbar.tsx');
 assert.match(toolbar,/className="min-h-12 shrink-0 [^"]*flex-wrap/);
});

test('the step bar keeps step numbers and marks finished steps with a check',()=>{
 const header=read('src/renderer/components/wizard/WizardHeader.tsx');
 assert.match(header,/completedSteps\?: readonly number\[\]/);assert.match(header,/const isDone = completedSteps\.includes\(s\.num\) && !isActive;/);
 assert.match(header,/String\(s\.num\)\.padStart\(2, '0'\)/,'the number stays in the button name');
 const app=read('src/renderer/App.tsx');assert.match(app,/completedSteps\(\{ hasData, images, unlabeled, trainingDone, evaluated, flowSaved \}\), \.\.\.workflowProgressFor\(progressScope\)/,'session state plus what the saved evidence proves');
});

test('the simple view gives every step a plain page header and moves each step\'s main action up',()=>{
 const banner=read('src/renderer/components/common/OperatorGuidanceBanner.tsx');
 for(const title of ["title: '데이터 준비'","title: '라벨 달기'","title: '모델 학습'","title: '결과 평가'","title: '검사 흐름 만들기'","title: '검사 실행'"])assert.ok(banner.includes(title),title);
 assert.match(banner,/if \(simpleView && isKo && simple\)/);
 const training=read('src/renderer/components/training/TrainingController.tsx');assert.match(training,/고급 학습 설정/);
 const evaluation=read('src/renderer/components/evaluation/EvaluationStudio.tsx');assert.match(evaluation,/aria-label="평가 결과 한눈에 보기"/);assert.match(evaluation,/다른 모델이나 시험 이미지 묶음으로 다시 평가/);
 const inference=read('src/renderer/components/inference/InferenceCenterStudio.tsx');
 assert.ok(inference.indexOf('<BatchInspectionPanel />')<inference.indexOf('{simpleView ? <details'),'exports follow the batch inspection');
 assert.match(inference,/현장 적용·내보내기 도구/);assert.match(inference,/\{exportActions\}\{packageNote\}\{advancedTools\}<\/div>\s*<\/details> : advancedTools\}/);
 const studio=read('src/renderer/components/dataset/DatasetStudio.tsx');assert.match(studio,/`폴더: \$\{folderPath/);
});

test('the simple view states the training target in one sentence and folds the review counts and team actions',()=>{
 const readiness=read('src/renderer/components/training/TeamTrainingReadiness.tsx');
 const simple=readiness.slice(readiness.indexOf("if(project.viewMode==='simple')"),readiness.indexOf(' return <section aria-label="팀 검수 학습 준비"',readiness.indexOf("if(project.viewMode==='simple')")+1));
 assert.match(simple,/학습에 쓸 이미지/);assert.match(simple,/학습 대상 이미지 \$\{value\.counts\.eligible/);assert.match(simple,/:reviewTrainingMessage\(value\)/,'blockers still read as before');
 const folded=simple.slice(simple.indexOf('<summary'));
 for(const detail of ['reviewTrainingMessage(value)','라벨 기준','대상 다시 확인','팀 검수 열기'])assert.ok(folded.includes(detail),`${detail} sits behind 검수 세부`);
 assert.ok(!simple.includes('sha256'),'hashes stay in the full view');
});

test('step 3 in the simple view: one model heading, plain preset words, a finished run points to step 4',()=>{
 const catalog=read('src/renderer/components/training/ModelFamilyCatalog.tsx');
 assert.match(catalog,/\{simpleView \? '모델 종류' : '모델 학습 허브'\}/);assert.match(catalog,/aria-label="모델 학습 허브"/,'the region keeps its name');
 const prep=read('src/renderer/components/training/TrainingPreparationPanel.tsx');
 assert.match(prep,/<summary className="cursor-pointer">학습 순서·사전학습 파일<\/summary>/);assert.match(prep,/\{!simpleView&&<><p className="mt-3 text-slate-300">다음 행동/);
 const presets=read('src/renderer/components/training/RecipePresetSelector.tsx');
 assert.match(presets,/처음 학습할 때 고르세요/);assert.match(presets,/\{showMatrix && !simpleView && \(/);
 const training=read('src/renderer/components/training/TrainingController.tsx');
 const banner=training.indexOf("{simpleView && status === 'completed' && jobId && (warmStartParentJobId");
 assert.ok(banner>0&&banner<training.indexOf('{stopError && ('),'the finished-run banner sits under the status, outside 고급 학습 설정');
 assert.match(training,/plain=\{simpleView\}/);assert.match(training,/\{!simpleView && <OperationsObservabilityPanel/);
 assert.match(training,/'This computer' \? '이 컴퓨터'/);
 const progress=read('src/renderer/components/training/JobProgressView.tsx');
 assert.match(progress,/plain \? `학습 \$\{view\.epoch\}/);assert.match(progress,/!\(plain && view\.tone === 'completed'\)/);
 const chart=read('src/renderer/components/training/OscilloscopeLossCurve.tsx');
 assert.ok(!chart.includes('`EP ${epoch}`')&&!chart.includes('10Hz'),'chart labels are plain');
});

test('step 4 in the simple view: results first, reviewer tools and raw metrics one click away, Korean matrix labels',()=>{
 const evaluation=read('src/renderer/components/evaluation/EvaluationStudio.tsx');
 assert.match(evaluation,/<summary[^>]*>모델 승인 · 검사 서비스 · 평가 이력<\/summary>/);
 assert.match(evaluation,/\{!simpleView && <>\s*<ModelDeploymentPanel/,'the full view keeps the panels where they were');
 assert.ok(evaluation.indexOf('모델 승인 · 검사 서비스 · 평가 이력')>evaluation.indexOf('{metricGrid}'),'the folded tools come after the results');
 assert.match(evaluation,/자세한 품질 지표/);assert.match(evaluation,/!\(simpleView && testPredictions\.length > 0\)/);
 assert.match(evaluation,/simpleView \? '추천 점수 적용' : '평가 임계값 적용'/);
 for(const ko of ["'판정 합계'","`합계 ${totalSamples}`","'정밀도'","`정확도 ${overallAccuracy.toFixed(1)}%`"])assert.ok(evaluation.includes(ko),ko);
});

test('the step 4 viewer reads plainly: Korean verdicts, no pixel scale boxes over the images in the simple view',()=>{
 const viewer=read('src/renderer/components/evaluation/SynchronizedDualViewport.tsx');
 assert.ok(!viewer.includes("'REJECT (NG)'")&&!viewer.includes("'PASS (OK)'"),'verdicts are Korean');
 assert.match(viewer,/\{!plain && <PhysicalScaleOverlay scale=\{rawTransform\.scale\} \/>\}/);assert.match(viewer,/\{!plain && <PhysicalScaleOverlay scale=\{overlayTransform\.scale\} \/>\}/);
 assert.match(read('src/renderer/components/evaluation/EvaluationStudio.tsx'),/plain=\{simpleView\}/);
 const readiness=read('src/renderer/components/wizard/workflowReadiness.ts');
 assert.match(readiness,/승인은 필요하지 않습니다/);assert.match(readiness,/openFoldsAround\(element\)/);
});

test('step 1 in the simple view: a saved split is one line, the verified version sits in 더 보기, no pixel scale',()=>{
 const studio=read('src/renderer/components/dataset/DatasetStudio.tsx');
 assert.match(studio,/const splitFolded = simpleView && showAppliedSplit && !splitOpen;/);
 assert.match(studio,/<div className=\{splitFolded \? 'hidden' : 'flex flex-col space-y-2\.5'\}>/);
 assert.match(studio,/학습 \{calibrator\.counts\.train\} · 검증 \{calibrator\.counts\.val\} · 시험 \{calibrator\.counts\.test\}/);
 assert.match(studio,/simpleView \? '이 비율로 나누기' : '3-Way 분할 적용'/);
 const more=studio.indexOf("'더 보기' : 'More'}</summary>");
 assert.ok(studio.indexOf('{simpleView && (',more)>more&&studio.indexOf('검증된 데이터 버전',more)<studio.indexOf('</details>}',more),'the verified version is a 더 보기 item in the simple view');
 assert.match(studio,/\{!simpleView && \(\s*<button\s*type="button"\s*onClick=\{\(\) => setShowImports\(true\)\}/,'the full view keeps it in the toolbar');
 assert.match(studio,/\$\{simpleView \? 'hidden' : 'hidden sm:flex'\} items-center/);
});

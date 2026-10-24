// Icon-only controls carry Korean names, the error dialog is a dialog, and hashes in the results stay behind
// "기술 상세". These read the component sources: the behaviour they guard is the accessible name itself.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test');
const read=file=>fs.readFileSync(path.resolve(__dirname,'..',file),'utf8');

test('labeling toolbar tooltips are Korean, not "Zoom In" / "Undo (Ctrl+Z)"',()=>{
 const toolbar=read('labeling/LabelingToolbar.tsx');
 for(const english of ['title="Undo','title="Redo','title="Zoom Out"','title="Zoom In"','title="Fit to Screen','title="100% Zoom'])assert.ok(!toolbar.includes(english),`still ${english}`);
 assert.match(toolbar,/title="확대"/);assert.match(toolbar,/title="축소"/);assert.match(toolbar,/실행 취소 \(Ctrl\+Z\)/);
});

test('icon-only buttons in the gallery pager, class editor, mask controls and annotation list are named',()=>{
 assert.match(read('dataset/DatasetStudio.tsx'),/aria-label="이전 페이지"/);
 assert.match(read('dataset/DatasetStudio.tsx'),/aria-label="다음 페이지"/);
 const categories=read('labeling/CategorySelector.tsx');
 assert.ok(!categories.includes('placeholder="New class..."')&&!categories.includes('"Add new category"'),'class editor still English');
 assert.match(categories,/aria-label="새 클래스 이름"/);assert.match(categories,/aria-label="추가 취소"/);
 assert.ok(!read('labeling/MaskLayerControls.tsx').includes('Hide Mask Layer'));
 const list=read('labeling/AnnotationList.tsx');
 assert.ok(!list.includes('title="Delete annotation"')&&!list.includes('placeholder="Enter measured pitch"'));
 assert.match(list,/aria-label="픽셀당 길이 \(μm\)"/);
 assert.match(read('flowchart/IntermediateCropDrawer.tsx'),/aria-label="부품명·결함 분류 검색"/);
 assert.match(read('dataset/ProceduralGeneratorModal.tsx'),/aria-label="닫기"/);
});

test('the error dialog is announced as a modal dialog with plain Korean section names',()=>{
 const modal=read('common/ErrorModal.tsx');
 assert.match(modal,/role="dialog"/);assert.match(modal,/aria-modal="true"/);assert.match(modal,/aria-labelledby=/);
 assert.match(modal,/aria-label="닫기"/);
 assert.ok(!modal.includes('[진단 원인 설명]')&&!modal.includes('[권장 엔지니어링 조치 방안]'),'console-style headings remain');
 assert.match(modal,/key === 'Escape'/);
});

test('the batch inspection results show hashes only with 기술 상세, and the readiness footer says "다음 할 일"',()=>{
 const batch=read('inference/BatchInspectionPanel.tsx');
 assert.match(batch,/technicalDetails/);
 const card=read('flowchart/SavedFlowIdentityCard.tsx');
 assert.match(card,/technicalDetails/);assert.match(card,/technicalDetails && <>/);
 assert.match(read('flowchart/FlowEditorWorkspace.tsx'),/selectedVersion\.slice\(0,8\)/);
 const footer=read('wizard/WizardFooter.tsx');
 assert.match(footer,/다음 할 일/);assert.ok(!footer.includes("blocked:'부족한 입력'"));
});

test('the operator workspace has one primary action and explains what approval means',()=>{
 const operator=read('runtime/OperatorWorkspace.tsx');
 assert.match(operator,/workspace-button--primary/);
 assert.match(operator,/승인하면 검사를 시작할 수 있습니다/);
});

test('the training and evaluation screens speak plain Korean (no console codes)',()=>{
 const training=read('training/TrainingController.tsx')+read('training/RecipePresetSelector.tsx')+read('training/OscilloscopeLossCurve.tsx')+read('training/HardwareTelemetryPanel.tsx');
 for(const jargon of ['BEST VAL LOSS','(Recipe B)','(RECIPE A)','Light Aug','Feasibility Check','TIMEBASE','HARDWARE TELEMETRY','label="STATUS"','EPOCH {','AUTOML'])assert.ok(!training.includes(jargon),`training screen still says ${jargon}`);
 assert.match(training,/결과 평가하기 \(4단계\)/);
 const evaluation=read('evaluation/EvaluationStudio.tsx')+read('evaluation/SynchronizedDualViewport.tsx');
 for(const jargon of ['RAW INSPECTION IMAGE','DEFECT HEATMAP OVERLAY','FN 미검 (Escape)','TN 정상합격',"'VERDICT'","'PRED'"])assert.ok(!evaluation.includes(jargon),`evaluation screen still says ${jargon}`);
 assert.match(evaluation,/장 맞음 · 미검/);
 assert.match(read('wizard/workflowReadiness.ts'),/학습 결과 평가 \(4단계\)/);
 const inference=read('inference/InferenceCenterStudio.tsx');
 const tools=inference.indexOf('const advancedTools');assert.ok(tools>0&&tools<inference.indexOf('<CoreModelTrialPanel'),'the single-model trial is one of the field tools');
 assert.ok(inference.indexOf('<BatchInspectionPanel />')<inference.lastIndexOf('advancedTools}'),'the field tools render after the batch inspection');
});

test('step banners name the steps in plain Korean and keep the English code behind 기술 상세',()=>{
 const titles=read('../data/jargonDictionary.ts');
 for(const jargon of ["'오토딥러닝 학습'","'인퍼런스 센터","'플로우차트 다중 모델 스튜디오'","오토라벨러'",'"completed" 상태'])assert.ok(!titles.includes(jargon),`step title still says ${jargon}`);
 assert.match(titles,/titleKo: '모델 학습'/);
 const banner=read('common/OperatorGuidanceBanner.tsx');
 assert.match(banner,/technicalDetails && <span/);
});

test('the class bar shows the project classes as chips and offers example names only as dashed additions',()=>{
 const categories=read('labeling/CategorySelector.tsx');
 assert.match(categories,/const simpleView = useProjectStore\(\(state\) => state\.viewMode\) === 'simple';/);
 assert.match(categories,/예시 이름 \$\{samples\.length\}개/);assert.match(categories,/onClick=\{\(\) => addCategory\(d\.name, d\.color, d\.id\)\}/);
 assert.match(categories,/\(!simpleView \|\| showSamples\) && samples\.map/,'folded in the simple view, inline in the full view');
 assert.match(categories,/cat\.id === activeCategory\.id/,'the drawing class stays visible');
 assert.doesNotMatch(categories,/isUnusedSample|const missing/,'an example name is never a chip by itself and a project class is never only an add button');
});

test('a classification project says where each image\'s answer comes from instead of asking for drawn regions',()=>{
 const picker=read('labeling/ClassificationClassPicker.tsx');
 assert.match(picker,/이 이미지의 클래스/);assert.match(picker,/폴더 클래스로 되돌리기/);assert.match(picker,/원본 파일은 옮기지 않습니다/);
 assert.match(read('labeling/LabelingStudio.tsx'),/<ClassificationClassPicker \/>/);
 assert.match(read('labeling/LabelingToolbar.tsx'),/classification \? 'hidden' : 'flex items-center space-x-2'/,'the OK tag toggle is not offered for classification');
 const list=read('labeling/AnnotationList.tsx');
 assert.match(list,/이 이미지에는 라벨이 없습니다/,'the empty-state title stays');
 assert.match(list,/이미지 분류의 정답은 위 '이 이미지의 클래스'에서 정합니다/);
});

// Audit F-15: result codes read as Korean words on screen; the leftover English and debug labels are gone.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test');
const labels=require('./plainLabels.fixture.cjs');
test('codes become words and unknown codes stay visible as they are',()=>{
 assert.equal(labels.stepStatusLabel('passed'),'통과');assert.equal(labels.stepStatusLabel('flagged_ng'),'NG 표시');assert.equal(labels.stepStatusLabel('custom'),'custom');
 assert.equal(labels.splitLabel('val'),'검증');assert.equal(labels.splitLabel(null),'미분할');assert.equal(labels.cropLabel('Full image'),'전체 이미지');
 assert.equal(labels.scoreUnitLabel(undefined),'확률(0~1)');assert.equal(labels.scoreUnitLabel('mahalanobis_distance'),'마할라노비스 거리');
});
test('the screens no longer show the English or debug labels the audit found',()=>{
 const read=file=>fs.readFileSync(path.join(__dirname,'..','components',file),'utf8');
 const gone={'training/TrainingController.tsx':['학습 중단 (Abort)'],'common/ImagePickerModal.tsx':['(Select Inspection Image)','(Absolute File Path)'],
  'flowchart/IntermediateCropDrawer.tsx':['(Master Annotated View)','단계 (Stage)','(Module)','상태 (Status)','(Takt %)'],
  'flowchart/FlowchartStudio.tsx':['NODE ID:','>TYPE:','COORD:','(RULE POLICY)','명칭 (LABEL)',"unit || 'probability'"],
  'evaluation/EvaluationStudio.tsx':["unit || 'probability'",'시험 분할 (Test)','검증 분할 (Validation)']};
 for(const [file,texts] of Object.entries(gone))for(const text of texts)assert.ok(!read(file).includes(text),`${file}: ${text}`);
 assert.match(read('flowchart/IntermediateCropDrawer.tsx'),/\{stepStatusLabel\(step\.status\)\}/);
 assert.match(read('common/ImagePickerModal.tsx'),/\{splitLabel\(split\)\}/);
});

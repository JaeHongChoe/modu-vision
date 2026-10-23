// The first-run guide reads in the order a beginner acts, and its result button lands on the inspection results.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),test=require('node:test');
const guide=fs.readFileSync(path.resolve(__dirname,'FirstRunStudio.tsx'),'utf8');
const batch=fs.readFileSync(path.resolve(__dirname,'../inference/BatchInspectionPanel.tsx'),'utf8');

test('the guide lists the first-project path in order and does not send a first-timer to step 3 before step 1',()=>{
 const order=['프로젝트 만들기','이미지 폴더 열기','라벨 달기·초안 검토','학습 시작','평가','검사 흐름 저장','검사 실행'].map(label=>guide.indexOf(`>${label}</strong>`));
 assert.ok(order.every(i=>i>=0),`missing labels: ${order}`);
 assert.deepEqual([...order].sort((a,b)=>a-b),order,'guide items are out of order');
 assert.ok(guide.indexOf('1단계로 이동')<guide.indexOf('3단계로 이동'),'the step-3 jump comes before the step-1 jump');
});

test('the guide names the control in the words the header uses',()=>{
 assert.ok(!guide.includes('This computer')&&!guide.includes('상단 Compute'),'guide still uses the English control names');
 assert.match(guide,/실행 위치/);
});

test('"검사 결과 보기" focuses the inspection history, which has a stable anchor',()=>{
 assert.match(guide,/focusWorkflowTarget\('workflow-inspection-history'\)/);
 assert.match(batch,/id="workflow-inspection-history"[^>]*tabIndex=\{-1\}|tabIndex=\{-1\}[^>]*id="workflow-inspection-history"/);
});

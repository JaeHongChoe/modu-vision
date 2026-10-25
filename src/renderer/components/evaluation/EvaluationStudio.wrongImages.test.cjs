// Ordinary preview filter accessibility and presentation guards. The footer's saved-review destination
// is exercised with the real Studio/store click path in evaluationClassSemantics.test.cjs.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const studio=fs.readFileSync(path.join(__dirname,'EvaluationStudio.tsx'),'utf8');

test('the first card\'s colour follows the result: missed defects rose, false alarms amber, all right green',()=>{
 assert.match(studio,/card\('맞힌 이미지', `\$\{right\} \/ \$\{sampleCounts\.all\}장`, accuracyNote\(right, sampleCounts\.all\), sampleCounts\.escape \? 'border-rose-700\/70 bg-rose-950\/30' : sampleCounts\.overkill \? 'border-amber-700\/70 bg-amber-950\/30' : right \? 'border-emerald-700\/60 bg-emerald-950\/20' : 'border-\[#2B3547\] bg-\[#131822\]'\)/);
});

test('the five preview image filters are tabs with their selection stated',()=>{
 assert.match(studio,/<div role="tablist" aria-label="평가 이미지 필터"/);
 const filters=[...studio.matchAll(/role="tab"\n\s*aria-controls="evaluation-image-list"\n\s*aria-selected=\{([^}]+)\}[\s\S]*?onClick=\{\(\) => setSampleFilter\('(\w+)'\)\}/g)].map(([,selected,filter])=>[filter,selected]);
 assert.deepEqual(filters.map(([filter])=>filter),['all','escape','overkill','defect','normal']);
 assert.equal(filters[1][1],"sampleFilter === 'escape' || sampleFilter === 'fn_escape'",'the same condition as its highlight');
 // The footer now has its own saved-authority review action; ordinary preview tabs retain their handlers.
 assert.equal(studio.match(/id="workflow-missed-defects"/g).length,1);
 const readiness=fs.readFileSync(path.join(__dirname,'..','wizard','workflowReadiness.ts'),'utf8');
 assert.match(readiness,/'틀린 이미지 보기',4,'workflow-missed-defects'\)/);
});

test('the filter tabs keep one Tab stop and move with the arrow keys, Home and End',()=>{
 const tabIndexes=[...studio.matchAll(/aria-selected=\{([^}]+)\}\n\s*tabIndex=\{([^}]+) \? 0 : -1\}/g)];
 assert.equal(tabIndexes.length,5);for(const [,selected,stop] of tabIndexes)assert.equal(stop,selected,'the selected tab is the Tab stop');
 const list=studio.slice(studio.indexOf('<div role="tablist" aria-label="평가 이미지 필터"'),studio.indexOf('className=',studio.indexOf('<div role="tablist" aria-label="평가 이미지 필터"')));
 assert.match(list,/event\.key === 'ArrowRight' \? \(index \+ 1\) % tabs\.length : event\.key === 'ArrowLeft' \? \(index \+ tabs\.length - 1\) % tabs\.length/);
 assert.match(list,/event\.key === 'Home' \? 0 : event\.key === 'End' \? tabs\.length - 1 : null/);
 assert.match(list,/event\.preventDefault\(\); tabs\[next\]\.click\(\); tabs\[next\]\.focus\(\);/);
});

test('results missing from the screen: the footer reaches the refresh button; a picked matrix cell never reads as zero misses',()=>{
 assert.match(studio,/id="evaluation-refresh"\n\s*onClick=\{refreshEvaluation\}/);
 assert.match(studio,/selectedCell \? '선택한 혼동 행렬 칸에는 미검 이미지가 없습니다 · 위의 필터 초기화를 누르면 모든 미검 이미지가 보입니다' : hasDefectSamples \? '현재 검증 표본에서 미검 0건' : 'NG 검증 표본 없음'/);
 assert.match(studio,/\{selectedCell \? '선택한 혼동 행렬 칸에는 과검 이미지가 없습니다 · 위의 필터 초기화를 누르면 모든 과검 이미지가 보입니다' : hasNormalSamples/);
 assert.match(studio,/<div id="evaluation-image-list" role="tabpanel" aria-label="평가 이미지 목록" className="contents \[&>\*\]:mt-2">/,'the panel the five tabs control, keeping the cards\' gap');
 assert.equal(studio.match(/aria-controls="evaluation-image-list"/g).length,5);
});

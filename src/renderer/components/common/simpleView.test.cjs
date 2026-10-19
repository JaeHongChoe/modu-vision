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
 assert.match(studio,/readLabelingFocus\(undefined, viewMode === 'simple'\)/);assert.match(studio,/collapsed=\{viewMode === 'simple'\}/);
 const bar=read('src/renderer/components/labeling/LabelSetBar.tsx');assert.match(bar,/if \(collapsed\) return <details/);
 assert.match(read('src/renderer/App.tsx'),/viewMode==='full'\?<>/);
 assert.match(read('src/renderer/index.css'),/html\[lang="ko"\] body \{ word-break: keep-all;/);
});

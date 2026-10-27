const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const {renderToStaticMarkup} = require('react-dom/server');
const file = path.join(__dirname, 'FlowPreparationPanel.tsx');
function load() {
  if (!fs.existsSync(file)) return () => null;
  const m = new Module(file, module); m.filename = file; m.paths = Module._nodeModulePaths(__dirname);
  m._compile(ts.transpileModule(fs.readFileSync(file, 'utf8'), {compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  }}).outputText, file);
  return m.exports.FlowPreparationPanel;
}
const Panel = load();
const nodes = tree => !tree || typeof tree !== 'object' ? [] : [tree, ...[].concat(tree.props?.children || []).flat(Infinity).flatMap(nodes)];
const text = tree => tree == null || typeof tree === 'boolean' ? '' : typeof tree !== 'object' ? String(tree) : [].concat(tree.props?.children || []).flat(Infinity).map(text).join('');
const buttons = tree => nodes(tree).filter(node => node.type === 'button');
const primary = tree => buttons(tree).filter(node => /\bworkspace-button--primary\b/.test(node.props.className || ''));
const stages = tree => nodes(tree).filter(node => node.type === 'li' && node.props['data-preparation-stage']);
const render = (extra = {}) => Panel({area:'edit', models:[{label:'표면 검사',task:'영역 분할',jobId:'job_1'}], rules:['결함 하나라도 NG'],
  exactSaved:false, evaluationCurrent:false, busy:false, loading:false, next:{label:'검사 흐름 저장',disabled:false,onClick(){}},
  onAdvanced(){}, onPrepare(){}, onEvaluate(){}, onReviewModel(){}, ...extra});

test('preparation shows five ordered stages, bound model and judgment without graph terminology', () => {
  const tree=render();
  assert.ok(nodes(tree).some(n=>n.type==='ol'&&n.props['aria-label']==='검사 준비 순서'));
  assert.deepEqual(stages(tree).map(n=>n.props['data-preparation-stage']),['model','rule','save','evaluate','inspect']);
  assert.match(text(tree), /표면 검사.*영역 분할/);
  assert.match(text(tree), /결함 하나라도 NG/);
  assert.equal(primary(tree).length,1);
  assert.equal(text(primary(tree)[0]),'검사 흐름 저장');
  assert.ok(!/노드 ID|포트|코호트/.test(text(tree)));
});

test('pending model is the current stage even if only some model references are bound', () => {
  const tree=render({models:[{label:'하나',task:'분류',jobId:'job_1'},{label:'둘',task:'분류'}]});
  assert.equal(stages(tree).find(n=>n.props['aria-current']==='step').props['data-preparation-stage'],'model');
  assert.match(text(stages(tree)[0]),/모델 연결 필요/);
  assert.equal(buttons(tree).find(n=>text(n)==='평가 화면 열기'),undefined);
});

test('saved evidence never marks evaluation current when the edited graph is unsaved', () => {
  const tree=render({exactSaved:false,evaluationCurrent:true});
  assert.equal(stages(tree).find(n=>n.props['aria-current']==='step').props['data-preparation-stage'],'save');
  assert.match(text(stages(tree)[3]),/평가 근거 확인 필요/);
  assert.ok(!/평가 근거 확인됨/.test(text(tree)));
});

test('a saved graph with stale evaluation advances to evaluation, never inspection or approval', () => {
  const tree=render({exactSaved:true,evaluationCurrent:false});
  assert.equal(stages(tree).find(n=>n.props['aria-current']==='step').props['data-preparation-stage'],'evaluate');
  assert.match(text(stages(tree)[3]),/평가 근거 확인 필요/);
  assert.ok(!/승인 완료|배포 완료/.test(text(tree)));
  assert.equal(buttons(tree).find(n=>text(n)==='평가 화면 열기').props.disabled,false);
});

test('current saved-flow evaluation reaches inspection preparation without claiming quality approval', () => {
  const tree=render({exactSaved:true,evaluationCurrent:true});
  assert.equal(stages(tree).find(n=>n.props['aria-current']==='step').props['data-preparation-stage'],'inspect');
  assert.match(text(tree),/평가 근거 확인됨/);
  assert.match(text(tree),/정답 확정.*최종 승인.*사람/);
  assert.ok(!/검사 품질 보장|승인 완료|배포 완료/.test(text(tree)));
});

test('busy and loading guard every retained callback including the primary', () => {
  for(const lock of [{busy:true},{loading:true}]) for(const area of ['edit','evaluate']) {
    let count=0; const call=()=>count++;
    const tree=render({...lock,area,exactSaved:true,onAdvanced:call,onPrepare:call,onEvaluate:call,onReviewModel:call,
      next:{label:'다음',disabled:false,onClick:call}});
    for(const control of buttons(tree)) {assert.equal(control.props.disabled,true); control.props.onClick();}
    assert.equal(count,0);
    assert.ok(nodes(tree).some(n=>n.props?.role==='status'));
  }
  let count=0; const tree=render({next:{label:'준비 필요',disabled:true,onClick:()=>count++}});
  primary(tree)[0].props.onClick(); assert.equal(count,0);
});

test('secondary navigation calls the supplied owner without starting another action', () => {
  const calls=[]; const tree=render({exactSaved:true,onAdvanced:()=>calls.push('advanced'),onEvaluate:()=>calls.push('evaluate'),onReviewModel:()=>calls.push('model'),next:{label:'저장',disabled:false,onClick:()=>calls.push('save')}});
  buttons(tree).find(n=>text(n)==='판정 기준 수정').props.onClick(); assert.deepEqual(calls,['advanced']);
  buttons(tree).find(n=>text(n)==='모델 다시 선택').props.onClick();
  buttons(tree).find(n=>text(n)==='평가 화면 열기').props.onClick();
  primary(tree)[0].props.onClick(); assert.deepEqual(calls,['advanced','model','evaluate','save']);
});

test('non-edit areas keep orientation and back navigation without a competing primary', () => {
  for(const area of ['test','evaluate','release']) {
    let count=0; const tree=render({area,onPrepare:()=>count++});
    assert.equal(primary(tree).length,0,area);
    assert.equal(stages(tree).length,5,area);
    buttons(tree).find(n=>text(n)==='검사 준비로 돌아가기').props.onClick(); assert.equal(count,1);
  }
});

test('labels and blocked reasons are text, every action uses the shared button contract', () => {
  const tree=render({models:[{label:'<script>attack</script>',task:'<img src=x>',jobId:'<b>job</b>'}],problem:'<svg onload=x>확인 필요'});
  const html=renderToStaticMarkup(tree);
  assert.ok(html.includes('&lt;script&gt;attack&lt;/script&gt;'));
  assert.ok(html.includes('&lt;b&gt;job&lt;/b&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(buttons(tree).every(n=>/(^|\s)workspace-button(\s|$)/.test(n.props.className||'')));
  assert.match(text(tree),/확인 필요/);
});

test('technical model identifiers stay in a closed disclosure and preparation accepts focus restoration', () => {
  const jobId='job_'+('a'.repeat(240));
  const tree=render({models:[{label:'표면 검사',task:'영역 분할',jobId}]});
  const disclosure=nodes(tree).find(n=>n.type==='details'&&text(n).includes('모델 작업 기록'));
  assert.ok(disclosure);
  assert.ok(!disclosure.props.open,'technical records start collapsed');
  assert.match(text(disclosure),new RegExp(jobId));
  const modelStage=stages(tree)[0];
  const visibleText=node=>node?.type==='details'?'':node==null||typeof node==='boolean'?'':typeof node!=='object'?String(node):[].concat(node.props?.children||[]).flat(Infinity).map(visibleText).join('');
  assert.match(visibleText(modelStage),/표면 검사.*영역 분할/);
  assert.ok(!visibleText(modelStage).includes(jobId));
  assert.equal(nodes(tree).find(n=>n.type==='section'&&n.props['aria-label']==='검사 준비').props.tabIndex,-1);
});

test('unbound preparation explicitly opens a basic-flow preview without calling save or advanced editing', () => {
  const calls=[]; const tree=render({models:[],onBasicRecipe:()=>calls.push('preview'),onAdvanced:()=>calls.push('advanced'),next:{label:'모델 연결',disabled:false,onClick:()=>calls.push('next')}});
  const basic=buttons(tree).find(n=>text(n)==='기본 검사 흐름 구성');
  assert.ok(basic);
  assert.ok(!/workspace-button--primary/.test(basic.props.className));
  assert.match(text(tree),/미리보기.*기존 흐름.*확인.*뒤/);
  basic.props.onClick(); assert.deepEqual(calls,['preview']);
  for(const extra of [{models:[{label:'모델',task:'분류',jobId:'job_ready'}]},{area:'evaluate'},{onBasicRecipe:undefined}]) {
    assert.ok(!buttons(render({models:[],onBasicRecipe(){},...extra})).some(n=>text(n)==='기본 검사 흐름 구성'));
  }
});

test('basic-flow preview callback is inert while loading or busy', () => {
  for(const lock of [{busy:true},{loading:true}]) {
    let count=0; const tree=render({...lock,models:[],onBasicRecipe:()=>count++});
    const basic=buttons(tree).find(n=>text(n)==='기본 검사 흐름 구성');
    assert.ok(basic); assert.equal(basic.props.disabled,true);
    basic.props.onClick(); assert.equal(count,0);
  }
});

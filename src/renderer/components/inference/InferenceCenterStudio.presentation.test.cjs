const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module'), ts = require('typescript');
function render(viewMode, running = false) {
  const component = name => Object.assign(() => null, {displayName:name});
  const store = value => Object.assign(select => select ? select(value) : value, {getState:()=>value});
  const evaluation = store({isBenchmarking:running, loadEvaluation(){}});
  const mocks = {
    react:{useEffect(){},useRef:current=>({current}),useState:value=>[value,()=>{}]},
    '../../stores/useProjectStore':{useProjectStore:store({language:'ko',task:'classification',backendStatus:{},viewMode})},
    '../../stores/useEvaluationStore':{useEvaluationStore:evaluation},
    '../../stores/useTrainingStore':{useTrainingStore:store({})},
    '../../stores/useDatasetStore':{useDatasetStore:store({})},
    '../../stores/useComputeStore':{useComputeStore:store({})},
    '../../services/modelExecution':{getExecutionContextIdentity:()=>''},
    './selectInferenceJob':{selectInferenceJobId:()=>null},
    './exportGuidance':{exportPackageGuidance:()=>''},
  };
  const file=path.join(__dirname,'InferenceCenterStudio.tsx'), m=new Module(file,module);
  m.filename=file;m.paths=Module._nodeModulePaths(__dirname);
  m.require=name=>mocks[name] || (name==='react/jsx-runtime'?require(name):new Proxy({}, {get:(_,key)=>component(key)}));
  m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,file);
  return m.exports.InferenceCenterStudio();
}
const walk=(node,parents=[])=>Array.isArray(node)?node.flatMap(n=>walk(n,parents)):node&&typeof node==='object'?[{node,parents},...walk(node.props.children,[...parents,node])]:[];
const text=node=>node==null||typeof node==='boolean'?'':typeof node==='object'?[].concat(node.props.children||[]).map(text).join(''):String(node);
test('saved-flow inspection stays primary in both views; named optional tools remain mounted in closed disclosures',()=>{
  for (const mode of ['simple','full']) {
    const rows=walk(render(mode));
    const batch=rows.find(r=>r.node.type.displayName==='BatchInspectionPanel');
    assert.ok(batch);assert.ok(!batch.parents.some(n=>n.type==='details'));
    for(const name of ['FlowPackagePanel','ModelOperationsPanel','CoreModelTrialPanel']) {
      const row=rows.find(r=>r.node.type.displayName===name);assert.ok(row,name);
      const disclosure=row.parents.find(n=>n.type==='details');assert.ok(disclosure,`${name} has a named disclosure`);
      assert.ok(!disclosure.props.open);
    }
    assert.ok(rows.some(r=>r.node.type==='summary'&&text(r.node).includes('단일 모델 시험·벤치마크')));
    assert.ok(!rows.some(r=>r.node.type.displayName==='OperatorGuidanceBanner'));
  }
});
test('a benchmark in progress remains visible outside optional disclosures',()=>{
  const status=walk(render('simple',true)).find(r=>r.node.props.role==='status'&&text(r.node).includes('벤치마크'));
  assert.ok(status);assert.ok(!status.parents.some(n=>n.type==='details'));
});

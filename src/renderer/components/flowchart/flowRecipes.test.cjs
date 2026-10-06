const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.join(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);m.require=ref=>ref.startsWith('.')?load(ref+'.ts'):require(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const recipes=()=>load('flowRecipes.ts');
test('oriented ROI recipe maps explicit OBB geometry into fitted alignment and the selected inspector',()=>{
 const r=recipes();assert.ok(r.FLOW_RECIPES.some(row=>row.id==='obb'));
 const preview=r.createFlowRecipe('obb','rotated_detection'),obb=model('owned-obb','rotated_detection');
 const draft=r.mapFlowRecipe(preview,[obb],{node_crop:'owned-obb',node_inspect:'owned-obb'},{node_crop:'all',node_inspect:'all'});
 assert.equal(draft.nodes.find(n=>n.id==='node_crop').data.task,'rotated_detection');
 assert.equal(draft.nodes.find(n=>n.id==='node_align').data.params.operation,'fitted_roi');
 assert.deepEqual(draft.edges.filter(e=>e.target==='node_align'||e.source==='node_align').map(e=>e.payload_type),['roi','roi']);
 assert.throws(()=>r.mapFlowRecipe(preview,[model('axis','detection')],{node_crop:'axis',node_inspect:'axis'},{node_crop:'all',node_inspect:'all'}),/실행할 수 있는 작업/);
});
const model=(job_id,task,extra={})=>({job_id,task,label:job_id,source_dataset_path:'/source',...extra});
test('every purpose requires explicit compatible models and class policy before adoption',()=>{const r=recipes();for(const kind of ['single','detector','fixed','rotation','multi']){const graph=r.createFlowRecipe(kind,'anomaly');assert.ok(graph.nodes.length>=4);assert.throws(()=>r.mapFlowRecipe(graph,[],{},{}),/모델/);}});
test('raw distance calibration and threshold survive fixed ROI recipe mapping without mutating preview',()=>{const r=recipes(),preview=r.createFlowRecipe('fixed','anomaly');const spec={domain:'distance',unit:'mahalanobis_distance',direction:'higher_is_defect',calibration_id:'saved-calibration',threshold:8};const selected=model('raw','anomaly',{score_spec:spec});const mapped=r.mapFlowRecipe(preview,[selected],{node_inspect:'raw'},{node_inspect:'all'});assert.equal(preview.nodes.find(n=>n.id==='node_inspect').data.model_job_id,undefined);assert.deepEqual(mapped.nodes.find(n=>n.id==='node_inspect').data.score_spec,spec);assert.equal(mapped.nodes.find(n=>n.id==='node_inspect').data.threshold,8);});
test('family mismatch, unknown model, and implicit class policy are refused',()=>{const r=recipes(),p=r.createFlowRecipe('single','anomaly');assert.throws(()=>r.mapFlowRecipe(p,[model('wrong','classification')],{node_inspect:'wrong'},{node_inspect:'all'}),/실행할 수 있는 작업/);assert.throws(()=>r.mapFlowRecipe(p,[model('right','anomaly')],{node_inspect:'right'},{}),/클래스/);});
test('rotation and aggregation use supported operators and retain per-model decisions',()=>{const r=recipes();assert.equal(r.createFlowRecipe('rotation','ocr').nodes.find(n=>n.id==='node_rotate').data.params.operation,'learned_rotation');const p=r.createFlowRecipe('multi','anomaly');assert.equal(p.nodes.find(n=>n.data.node_type==='aggregate').data.rule,'any_ng');assert.equal(p.nodes.find(n=>n.data.node_type==='decision').data.rule,'aggregate_verdict');});
const library=[model('cls','classification',{class_names:['ok','ng']}),model('seg','segmentation'),model('ano','anomaly'),model('det','detection'),model('rot','rotation'),model('ocr','ocr')];
const fullMapping=(r,preview)=>{const mapping={},policies={};for(const node of r.recipeModelNodes(preview)){const choice=r.selectableModels(node,library)[0];assert.ok(choice,`${preview.id}/${node.id} (${node.data.task}) has a selectable model`);mapping[node.id]=choice.job_id;policies[node.id]='all';}return {mapping,policies};};
test('every recipe maps to a draft the graph validator accepts, for every project task',()=>{const r=recipes();
 for(const task of ['classification','segmentation','anomaly','detection'])for(const kind of ['single','detector','fixed','rotation','multi']){
  const preview=r.createFlowRecipe(kind,task),{mapping,policies}=fullMapping(r,preview);
  assert.doesNotThrow(()=>r.mapFlowRecipe(preview,library,mapping,policies),`${task}/${kind}`);}});
test('a detection project single recipe is the detector itself; inspection nodes never take a detection model',()=>{const r=recipes();
 assert.deepEqual(r.createFlowRecipe('single','detection').nodes.map(n=>n.data.node_type),['input','detection_crop','decision','output']);
 for(const kind of ['detector','fixed','rotation','multi'])for(const node of r.createFlowRecipe(kind,'detection').nodes.filter(n=>n.data.node_type==='inspection')){
  assert.equal(node.data.task,'segmentation',`${kind} inspects ROIs with segmentation`);assert.ok(!r.recipeNodeTasks(node).includes('detection'));
  assert.deepEqual(r.selectableModels({...node,data:{...node.data,task:'detection'}},library),[],'a detection model is never offered for an inspection node');}});
test('an OCR inspection needs an explicit expectation, which the adopted draft carries',()=>{const r=recipes(),preview=r.createFlowRecipe('rotation','ocr');
 const mapping={node_rotate:'rot',node_inspect:'ocr'},policies={node_rotate:'all',node_inspect:'all'};
 assert.throws(()=>r.mapFlowRecipe(preview,library,mapping,policies),/OCR 기대 문자열/);
 const draft=r.mapFlowRecipe(preview,library,mapping,policies,{node_inspect:{kind:'regex',value:'^LOT-[0-9]{4}$'}});
 assert.equal(draft.nodes.find(n=>n.id==='node_inspect').data.params.regex,'^LOT-[0-9]{4}$');
 assert.throws(()=>r.mapFlowRecipe(preview,library,mapping,policies,{node_inspect:{kind:'regex',value:'('}}),/채택할 수 없는 초안/);
 for(const pattern of ['^(?<lot>[0-9]{4})$','^(?P<lot>[0-9]{4})$','^(?P<a>x)(?P=a)$'])
  assert.throws(()=>r.mapFlowRecipe(preview,library,mapping,policies,{node_inspect:{kind:'regex',value:pattern}}),/이름 있는 그룹/,`${pattern}: editor and server disagree on named groups`);
 for(const pattern of ['\\cA','[^]','\\p{L}','(?<=LOT-+)X'])
  assert.throws(()=>r.mapFlowRecipe(preview,library,mapping,policies,{node_inspect:{kind:'regex',value:pattern}}),/지원하지 않는 정규식/,`${pattern}: Python cannot compile it`);
 assert.doesNotThrow(()=>r.mapFlowRecipe(preview,library,mapping,policies,{node_inspect:{kind:'regex',value:'^LOT-[0-9]{4}(?<=[0-9])$'}}),'ordinary patterns and lookbehind remain allowed');});
test('the dialog reads ports as source → target with the payload',()=>{const r=recipes();
 assert.ok(r.recipePorts(r.createFlowRecipe('fixed','anomaly')).includes('검사 이미지 → 고정 ROI (image)'));});

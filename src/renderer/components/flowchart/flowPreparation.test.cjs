const test=require('node:test'), assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
const filename=path.join(__dirname,'flowPreparation.ts');
const m=new Module(filename,module);m.paths=Module._nodeModulePaths(__dirname);
if(fs.existsSync(filename))m._compile(ts.transpileModule(fs.readFileSync(filename,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,filename);
const base={loading:false,busy:false,hasPipeline:true,modelBlocked:false,needsModel:false,graphError:null,canSave:true,exactSaved:false,evaluationCurrent:false};
test('opening and active writes hold the next action rather than offering replacement or save',()=>{
 for(const state of [{loading:true},{busy:true}])assert.deepEqual(m.exports.flowPreparationNext({...base,...state}),{action:'wait',label:'현재 작업 확인 중…',disabled:true});
});
test('missing model and invalid custom graph route to editing instead of resetting graph',()=>{
 assert.equal(m.exports.flowPreparationNext({...base,needsModel:true}).action,'edit');
 assert.equal(m.exports.flowPreparationNext({...base,graphError:'분기 조건 미설정'}).action,'edit');
 assert.equal(m.exports.flowPreparationNext({...base,modelBlocked:true}).action,'review-model');
});
test('save and current saved evaluation remain separate prerequisites, including a dirty evaluated graph',()=>{
 assert.equal(m.exports.flowPreparationNext(base).action,'save');
 assert.equal(m.exports.flowPreparationNext({...base,canSave:false}).disabled,true);
 assert.equal(m.exports.flowPreparationNext({...base,exactSaved:true}).action,'evaluate');
 assert.equal(m.exports.flowPreparationNext({...base,exactSaved:true,evaluationCurrent:true}).action,'inspect');
 assert.equal(m.exports.flowPreparationNext({...base,evaluationCurrent:true}).action,'save');
});
test('summaries preserve multi-model rules and identify unknown rules without inventing criteria',()=>{
 const graph={nodes:[{id:'a',data:{node_type:'inspection',label:'스크래치',task:'segmentation',model_job_id:'job_a'}},{id:'b',data:{node_type:'detection_crop',label:'나사',task:'detection',model_job_id:'job_b'}},{id:'c',data:{node_type:'decision',label:'최종 판정',rule:'any_defect_is_ng'}},{id:'d',data:{node_type:'decision',label:'특수 판정',rule:'future-rule'}}]};
 const result=m.exports.flowPreparationSummary(graph);
 assert.equal(result.models.length,2);assert.equal(result.models[1].jobId,'job_b');
 assert.deepEqual(result.rules,['최종 판정 · 결함 하나라도 NG','특수 판정 · 고급 편집에서 세부 규칙 확인']);
 assert.equal(graph.nodes.length,4);assert.equal(graph.nodes[3].data.rule,'future-rule');
});

test('the criterion summary distinguishes actual numeric score and defect count settings',()=>{
 const graph={nodes:[{data:{node_type:'decision',label:'점수 판정',rule:'score_gt_threshold',threshold:0.7,score_spec:{unit:'probability'}}},
  {data:{node_type:'decision',label:'개수 판정',rule:'max_flaws_allowed',params:{max_flaws_allowed:3}}}]};
 const rows=m.exports.flowPreparationSummary(graph).rules;
 assert.match(rows[0],/0.7.*probability/);assert.match(rows[1],/3개/);
 graph.nodes[0].data.threshold=0.9;graph.nodes[1].data.params.max_flaws_allowed=0;
 const changed=m.exports.flowPreparationSummary(graph).rules;
 assert.match(changed[0],/0.9/);assert.match(changed[1],/0개/);assert.notDeepEqual(changed,rows);
});

test('a valid saved version with no confirmed truth is not ready to leave preparation',()=>{
 assert.equal(m.exports.hasReviewedFlowEvaluation(true,0),false);
 assert.equal(m.exports.hasReviewedFlowEvaluation(false,19),false);
 assert.equal(m.exports.hasReviewedFlowEvaluation(true,1),true);
 assert.equal(m.exports.hasReviewedFlowEvaluation(true,NaN),false);
 const next=m.exports.flowPreparationNext({...base,exactSaved:true,evaluationCurrent:m.exports.hasReviewedFlowEvaluation(true,0)});
 assert.equal(next.action,'evaluate');
});

test('learned enhancement and rotation models remain visible alongside inspection models',()=>{
 const graph={nodes:[{data:{node_type:'preprocess',label:'영상 개선',params:{operation:'enhancement'}}},
  {data:{node_type:'preprocess',label:'회전 보정',params:{operation:'learned_rotation'},model_job_id:'rotation_job'}},
  {data:{node_type:'inspection',label:'분류',task:'classification',model_job_id:'classifier_job'}},
  {data:{node_type:'preprocess',label:'밝기',params:{operation:'brightness'}}}]};
 const result=m.exports.flowPreparationSummary(graph);
 assert.equal(result.models.length,3);
 assert.deepEqual(result.models[0],{label:'영상 개선',task:'학습형 영상 개선',jobId:undefined});
 assert.equal(result.models[1].task,'회전 보정');
});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file){const name=path.resolve(__dirname,file),m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(path.dirname(name));const req=m.require.bind(m);m.require=key=>key.startsWith('.')?load(path.resolve(path.dirname(name),key)+'.ts'):req(key);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const handoff=load('./modelFlowHandoff.ts');
const score={domain:'distance',unit:'mahalanobis_distance',direction:'higher_is_defect',calibration_id:'model-a',threshold:8};
const model={job_id:'job_a',task:'anomaly',score_spec:score,threshold_settings:{threshold:8}};
const pipeline={nodes:[{id:'i',position:{x:0,y:0},data:{label:'inspect',node_type:'inspection',task:'anomaly',threshold:.5,model_job_id:'old'}}],edges:[]};
test('selecting another model binds its saved threshold and calibration',()=>{const result=handoff.bindModelToFlow(pipeline,model,'i').pipeline.nodes[0].data;assert.equal(result.threshold,8);assert.deepEqual(result.score_spec,score);});
test('adding a distance node and editing threshold preserves consistent units',()=>{const result=handoff.bindModelToFlow({nodes:[],edges:[]},model).pipeline.nodes[0].data;assert.equal(result.threshold,8);const edited=handoff.thresholdUpdate(result,12);assert.equal(edited.threshold,12);assert.deepEqual(edited.score_spec,{...score,threshold:12});});
test('probability input and nonfinite distance input fail validation',()=>{assert.throws(()=>handoff.thresholdUpdate({threshold:.5},1.5),/threshold|임계/);assert.throws(()=>handoff.thresholdUpdate({score_spec:score},Infinity),/threshold|임계/);});

test('renderer graph accepts distance threshold8 and rejects mixed global score units',()=>{
 const {validateFlowchartGraph}=load('./flowchartGraph.ts');
 const node=(id,node_type,extra={})=>({id,position:{x:0,y:0},data:{label:id,node_type,...extra}});
 const graph={nodes:[node('input','input'),node('i','inspection',{task:'anomaly',model_job_id:'job_a',threshold:8,score_spec:score}),node('d','decision',{rule:'any_defect_is_ng'}),node('out','output')],edges:[{id:'a',source:'input',target:'i'},{id:'b',source:'i',target:'d'},{id:'c',source:'d',target:'out'}]};
 assert.equal(validateFlowchartGraph(graph),null);
 graph.nodes[2].data={...graph.nodes[2].data,rule:'score_gt_threshold',threshold:.5};
 assert.match(validateFlowchartGraph(graph),/점수|보정|calibration/);
});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
function load(file=path.join(__dirname,'assistantGuidance.ts')){assert.ok(fs.existsSync(file),'the production guidance builder exists');const m=new Module(file,module);m.filename=file;m.paths=Module._nodeModulePaths(path.dirname(file));const original=m.require.bind(m);m.require=name=>name.startsWith('.')?load(path.resolve(path.dirname(file),name)+'.ts'):original(name);m._compile(ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,file);return m.exports;}
const fixture=()=>({task:'classification',jobId:'job_1',threshold:.5,samples:10,knownNormals:6,knownDefects:4,unresolved:0,summary:{samples:10,escapes:2,overkills:1,accuracy:70,threshold:.5},metrics:{evaluated_split:'val',selection_overlap:true},sourceVerified:true,
 analysis:{sample_count:10,total_normals:6,total_defects:4,target_max_underkill:0.1,optimal_threshold:.35,optimal_stats:{threshold:.35,underkill_count:1,overkill_count:2,total_cost:12}}});
test('training evidence names measured input/device/weight receipts without claiming execution or leaking paths',()=>{
 const {preparationEvidence}=load();const actual=preparationEvidence({ready:true,model:'dinov3_vits16',input:{split_counts:{train:14,val:4,test:2}},runtime:{available:true,device:'cpu',reason:''},dependencies:{missing:[]},weights:{state:'file_available',filename:'/private/weights/model.safetensors',sha256:'ab'.repeat(32),content_verified:false},next_actions:[],selected_checkpoint_path:'/private/weights/model.safetensors',execution_verified:false,quality_approved:true});
 assert.equal(actual.ready,true);assert.equal(actual.execution_verified,false);assert.equal(actual.quality_approved,false);assert.equal(actual.weights.filename,'model.safetensors');assert.deepEqual(actual.input.split_counts,{train:14,val:4,test:2});assert.ok(!JSON.stringify(actual).includes('/private/'));
 const unknown=preparationEvidence({ready:true,runtime:{available:true,device:'cpu'},weights:{state:'missing'},dependencies:{missing:[]},input:{split_counts:{}}});assert.equal(unknown.ready,false,'a boolean without usable weights and measured split is not preparation evidence');
});
test('backend train/val readiness does not invent a test count or require a test split for training',()=>{
 const actual=load().preparationEvidence({ready:true,input:{split_counts:{train:14,val:4}},runtime:{available:true,device:'cpu'},dependencies:{missing:[]},weights:{state:'not_required'}});
 assert.equal(actual.ready,true);assert.deepEqual(actual.input.split_counts,{train:14,val:4,test:null});assert.equal(actual.input.measured,true);
});
test('threshold guidance reports both error trades on the loaded cohort and never changes its input',()=>{
 const {evaluationGuidance}=load(),input=fixture(),before=structuredClone(input),actual=evaluationGuidance(input);
 assert.deepEqual(input,before);assert.equal(actual.score.domain,'probability');assert.equal(actual.recommendation.threshold,.35);assert.equal(actual.recommendation.escapes,1);assert.equal(actual.recommendation.overkills,2);assert.equal(actual.current.escapes,2);assert.equal(actual.conditions.selection_overlap,true);assert.equal(actual.quality_approved,false);assert.match(actual.limits,/자동.*적용|직접/);
});
test('distance scores keep their units and cannot become a probability threshold recommendation',()=>{
 const {evaluationGuidance}=load(),input={...fixture(),task:'anomaly',threshold:12,metrics:{score_spec:{domain:'distance',unit:'mahalanobis_distance',direction:'higher_is_defect',calibration_id:'sha256:abc',threshold:12},evaluated_split:'val'}};
 const actual=evaluationGuidance(input);assert.equal(actual.score.domain,'distance');assert.equal(actual.score.unit,'mahalanobis_distance');assert.equal(actual.recommendation,null);assert.match(actual.recommendation_reason,/거리/);
});
test('stale, single-class, unknown-truth, mismatched-analysis and fixed-cohort evidence never gets a threshold recommendation',()=>{
 const {evaluationGuidance}=load();for(const change of [{sourceVerified:false},{knownDefects:0,knownNormals:10},{unresolved:1},{analysis:{...fixture().analysis,sample_count:11}},{analysis:{...fixture().analysis,optimal_threshold:null}},{cohort:{dataset_version_id:'v1',cohort_sha256:'ab'.repeat(32),image_count:10,split:'test'}},{task:'anomaly',metrics:{evaluated_split:'test'}}]){
  const actual=evaluationGuidance({...fixture(),...change});assert.equal(actual.recommendation,null,JSON.stringify(change));assert.ok(actual.recommendation_reason);
 }
});

const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-05: UNKNOWN is an explicit rule. The decision says how an image on which no condition was met is judged; the editor
// knows when that can happen (every route to the decision has a condition) and refuses a rule the backend refuses.
function compile(file){const name=path.join(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);
  m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText,name);return m.exports;}
const graph=compile('flowchartGraph.ts');
const node=(id,node_type,extra={})=>({id,position:{x:0,y:0},data:{label:id,node_type,...extra}});
const PRESENT={kind:'class',operator:'present',class_name:'scratch',min_confidence:0.5};
function flow(toDecision={}){return {id:'p',name:'p',nodes:[node('in','input'),node('cls','inspection',{task:'classification',threshold:0.5,model_job_id:'job'}),node('judge','decision',{rule:'any_defect_is_ng',params:{}}),node('out','output')],
  edges:[{id:'e1',source:'in',target:'cls',payload_type:'image'},{id:'e2',source:'cls',target:'judge',payload_type:'result',...toDecision},{id:'e3',source:'judge',target:'out',payload_type:'result'}]};}
test('the no-branch rule applies only when every route to the decision has a condition',()=>{
 assert.equal(graph.decisionReachedOnlyByConditions(flow()),false,'an unconditional route always reaches the decision');
 assert.equal(graph.decisionReachedOnlyByConditions(flow({predicate:PRESENT})),true,'a class-present-only branch can leave an image unrouted');
 assert.equal(graph.decisionReachedOnlyByConditions(flow({isBranch:'fail'})),true,'a verdict branch too');
 assert.equal(graph.decisionReachedOnlyByConditions(flow({isBranch:'default'})),false);
 const both=flow({predicate:PRESENT});both.nodes.push(node('cls2','inspection',{task:'classification',threshold:0.5,model_job_id:'job2'}));
 both.edges.push({id:'e4',source:'in',target:'cls2',payload_type:'image'},{id:'e5',source:'cls2',target:'judge',payload_type:'result'});
 assert.equal(graph.decisionReachedOnlyByConditions(both),false,'one unconditional route is enough');
 const chained=flow();chained.edges[0]={...chained.edges[0],predicate:undefined};chained.nodes.splice(1,0,node('gate','inspection',{task:'classification',threshold:0.5,model_job_id:'gate'}));
 chained.edges=[{id:'g1',source:'in',target:'gate',payload_type:'image'},{id:'g2',source:'gate',target:'cls',payload_type:'image',predicate:PRESENT},...chained.edges.slice(1)];
 assert.equal(graph.decisionReachedOnlyByConditions(chained),true,'a condition earlier on the only route counts');
 const forked=flow({isBranch:'pass'});forked.edges.push({id:'e5',source:'cls',target:'judge',payload_type:'result',isBranch:'fail'});
 assert.equal(graph.decisionReachedOnlyByConditions(forked),false,'pass and fail together send every image on');
 const classes=flow({predicate:PRESENT});classes.nodes.push(node('detail','inspection',{task:'classification',threshold:0.5,model_job_id:'d'}));
 classes.edges.push({id:'e6',source:'cls',target:'detail',payload_type:'image',predicate:{...PRESENT,operator:'absent'}},{id:'e7',source:'detail',target:'judge',payload_type:'result'});
 assert.equal(graph.decisionReachedOnlyByConditions(classes),false,'present and absent of the same class cover every image');
 classes.edges[classes.edges.length-2].predicate={...PRESENT,operator:'absent',class_name:'dent'};
 assert.equal(graph.decisionReachedOnlyByConditions(classes),true,'another class does not');});
test('the rule is one of review, ok and ng, as the backend accepts',()=>{
 assert.deepEqual([...graph.NO_BRANCH_POLICIES],['review','ok','ng']);
 for(const value of ['review','ok','ng',undefined,null]){const pipeline=flow({predicate:PRESENT});if(value!==undefined)pipeline.nodes[2].data.params.no_branch_policy=value;
  assert.equal(graph.validateFlowchartGraph(pipeline),null,String(value));}
 for(const value of ['OK','pass','',1]){const pipeline=flow({predicate:PRESENT});pipeline.nodes[2].data.params.no_branch_policy=value;
  assert.match(graph.validateFlowchartGraph(pipeline)||'',/조건이 하나도 맞지 않은 이미지의 판정/,String(value));}});
test('a covering split sends every image to the decision only when every branch does',()=>{
 // Review of freeze 2 (P3-1): the absent branch goes on to a node that splits again by pass only.
 const absentOn=flow({predicate:PRESENT});absentOn.nodes.push(node('c','inspection',{task:'classification',threshold:0.5,model_job_id:'c'}));
 absentOn.edges.push({id:'a1',source:'cls',target:'c',payload_type:'image',predicate:{...PRESENT,operator:'absent'}},{id:'a2',source:'c',target:'judge',payload_type:'result',isBranch:'fail'});
 assert.equal(graph.decisionReachedOnlyByConditions(absentOn),true,'an image whose c answers OK reaches no branch');
 absentOn.edges.push({id:'a3',source:'c',target:'judge',payload_type:'result',isBranch:'pass'});
 assert.equal(graph.decisionReachedOnlyByConditions(absentOn),false,'with both of c\'s branches every image arrives');
 const gate=flow({isBranch:'pass'});gate.nodes.push(node('follow','inspection',{task:'classification',threshold:0.5,model_job_id:'f'}));
 gate.edges.push({id:'f1',source:'cls',target:'follow',payload_type:'image',isBranch:'fail'},{id:'f2',source:'follow',target:'judge',payload_type:'result',predicate:PRESENT});
 assert.equal(graph.decisionReachedOnlyByConditions(gate),true,'the fail branch goes on to a class-present-only branch');
 // present at 0.5 and absent at 0.8 still cover every image (no detection at 0.5 means none at 0.8 either)
 const uneven=flow({predicate:PRESENT});uneven.edges.push({id:'u1',source:'cls',target:'judge',payload_type:'result',predicate:{...PRESENT,operator:'absent',min_confidence:0.8}});
 assert.equal(graph.decisionReachedOnlyByConditions(uneven),false);
 uneven.edges[uneven.edges.length-1].predicate={...PRESENT,operator:'absent',min_confidence:0.3};
 assert.equal(graph.decisionReachedOnlyByConditions(uneven),true,'absent at 0.3 leaves images with a detection between 0.3 and 0.5');});

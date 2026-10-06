const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-05: every problem is shown on the node or connection it belongs to, ports name what a connection may carry, and a
// connection started from the wrong port is refused at once with the accepted payloads.
function compile(file,mocks={}){const name=path.join(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
  m.require=ref=>ref in mocks?mocks[ref]:original(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const graph=compile('flowchartGraph.ts');
test('original-image bounds mark a fixed ROI before execution without using model resize dimensions',()=>{
 const p=valid();p.nodes.push(node('roi','fixed_roi',{params:{roi_bbox:[4,4,64,40]}}));p.edges[0]={...p.edges[0],target:'roi'};p.edges.push({id:'crop',source:'roi',target:'seg',payload_type:'roi'});
 const marked=graph.flowIssuesByTarget(p,undefined,[48,48]);assert.match(marked.nodes.get('roi')?.[0]||'',/원본 48×48.*경계/);
 assert.match(graph.validateFlowchartGraph(p,undefined,[48,48]),/경계/);
 p.nodes.at(-1).data.params.roi_bbox=[4,4,48,48];assert.equal(graph.validateFlowchartGraph(p,undefined,[48,48]),null);
 assert.equal(graph.validateFlowchartGraph(p,undefined,[128,128]),null,'a model input size is not original-image bounds');
});
test('reference fixture coordinates require runtime pose validation rather than raw original bounds',()=>{
 const p=valid();p.nodes.push(node('roi','fixed_roi',{params:{roi_bbox:[4,4,64,40],fixture:{reference:'owned-reference'}}}));p.edges[0]={...p.edges[0],target:'roi'};p.edges.push({id:'crop',source:'roi',target:'seg',payload_type:'roi'});
 assert.equal(graph.validateFlowchartGraph(p,undefined,[48,48]),null);
});
const node=(id,node_type,extra={})=>({id,position:{x:0,y:0},data:{label:id,node_type,...extra}});
function valid(){return {id:'p',name:'p',nodes:[node('in','input'),node('seg','inspection',{task:'segmentation',threshold:0.5}),node('cls','inspection',{task:'classification',threshold:0.5}),node('judge','decision',{rule:'any_defect_is_ng'}),node('out','output')],
  edges:[{id:'e1',source:'in',target:'seg',payload_type:'image'},{id:'e2',source:'in',target:'cls',payload_type:'image'},{id:'e3',source:'seg',target:'judge',payload_type:'result'},{id:'e4',source:'cls',target:'judge',payload_type:'result'},{id:'e5',source:'judge',target:'out',payload_type:'result'}]};}
test('S2-05: a valid graph has no issues, and the first collected issue is always the validator answer',()=>{
 assert.deepEqual(graph.flowGraphIssues(valid()),[]);assert.equal(graph.validateFlowchartGraph(valid()),null);
 const broken=valid();broken.nodes[1].data.threshold=4;broken.nodes[2].data.threshold=-1;broken.edges[2].payload_type='image';
 const first=graph.flowGraphIssues(broken,{first:true});assert.equal(first.length,1);assert.equal(graph.validateFlowchartGraph(broken),first[0].message);
 assert.deepEqual(graph.flowGraphIssues(broken)[0],first[0],'the full list starts with the same issue');});
test('S2-05: every broken node and connection reports its own problem at once, each attached to it',()=>{
 const broken=valid();broken.nodes[1].data.threshold=4;broken.nodes[2].data.threshold=-1;broken.edges[2].payload_type='image';
 const {nodes,edges}=graph.flowIssuesByTarget(broken);
 assert.deepEqual([...edges.keys()],['e3']);assert.match(edges.get('e3')[0],/데이터 형식/);
 assert.deepEqual([...nodes.keys()].sort(),['cls','seg']);assert.match(nodes.get('seg')[0],/^seg: 점수 임계치는 0~1/);assert.match(nodes.get('cls')[0],/^cls: /);
 const unconnected=valid();unconnected.nodes.push(node('lonely','inspection',{task:'classification',threshold:0.5}));
 const lonely=graph.flowIssuesByTarget(unconnected).nodes.get('lonely');
 assert.equal(lonely.length,1,'its own problem only, not also the general unreached-node message');assert.match(lonely[0],/모델 입력 연결선이 정확히 하나/,'a node added but not connected is marked');
 const cycle=valid();cycle.nodes.push(node('a','inspection',{task:'classification',threshold:0.5}),node('b','inspection',{task:'classification',threshold:0.5}));
 cycle.edges.push({id:'ab',source:'a',target:'b',payload_type:'roi'},{id:'ba',source:'b',target:'a',payload_type:'roi'},{id:'bj',source:'b',target:'judge',payload_type:'result'},{id:'aj',source:'a',target:'judge',payload_type:'result'});
 const cyclic=graph.flowIssuesByTarget(cycle).nodes;assert.ok([...cyclic.values()].flat().some(message=>/순환 없이/.test(message)),'a cycle with no other problem is still named');});
test('S2-05: graph-wide problems stay in the banner, and a count problem stops before node checks that need it',()=>{
 const two=valid();two.nodes.push(node('judge2','decision',{rule:'any_defect_is_ng'}));
 const issues=graph.flowGraphIssues(two);assert.equal(issues[0].kind,'graph');assert.match(issues[0].message,/판정 노드는 하나/);
 assert.equal(issues.length,1,'node checks that need the one decision are not run');
 const located=valid();located.nodes.push(node('out2','output'));located.edges.push({id:'e6',source:'judge',target:'out2',payload_type:'result'});
 assert.deepEqual(graph.locateFlowIssue(located,graph.validateFlowchartGraph(located)),{kind:'node',id:'judge'},'branch labels point at the decision');});
test('S2-05: ports name the payloads the connection rules allow, and an edge leaves from its payload port',()=>{
 const labels=(n)=>{const ports=graph.flowNodePorts(n);return [ports.inputs.map(p=>p.label),ports.outputs.map(p=>p.label)];};
 assert.deepEqual(labels(node('a','input')),[[],['IMAGE OUT']]);
 assert.deepEqual(labels(node('a','fixed_roi')),[['IMAGE IN'],['ROI OUT']]);
 assert.deepEqual(labels(node('a','inspection')),[['IMAGE/ROI IN'],['IMAGE/ROI OUT','RESULT OUT']]);
 assert.deepEqual(labels(node('a','blob_measure')),[['RESULT IN'],['RESULT OUT']]);
 assert.deepEqual(labels(node('a','decision')),[['RESULT IN'],['VERDICT OUT']]);
 assert.deepEqual(labels(node('a','output')),[['VERDICT IN'],[]]);
 const seg=node('seg','inspection',{task:'segmentation'});
 assert.equal(graph.flowEdgeSourcePort(seg,{payload_type:'result'}),1);assert.equal(graph.flowEdgeSourcePort(seg,{payload_type:'roi'}),0);
 assert.equal(graph.flowEdgeSourcePort(node('d','decision'),{payload_type:'result'}),0,'the decision routes its result through its verdict port');});
test('S2-05: a connection started from the wrong port is refused with what the target accepts',()=>{
 const draft={id:'p',name:'p',nodes:[node('in','input'),node('det','detection_crop',{threshold:0.5}),node('cls','inspection',{task:'classification',threshold:0.5}),node('judge','decision')],edges:[{id:'e1',source:'in',target:'det',payload_type:'image'}]};
 assert.throws(()=>graph.connectFlowNodes(draft,'det','cls',['result']),/det의 결과 출력은 cls에 연결할 수 없습니다\. 이 입력은 이미지·ROI만 받습니다\./);
 assert.throws(()=>graph.connectFlowNodes(draft,'det','judge',['image','roi']),/이 입력은 결과만 받습니다/);
 const regions=graph.connectFlowNodes(draft,'det','cls',['image','roi']);assert.equal(regions.edges.at(-1).payload_type,'roi');
 const result=graph.connectFlowNodes(draft,'det','judge',['result']);assert.equal(result.edges.at(-1).payload_type,'result');
 assert.equal(graph.connectFlowNodes(draft,'det','judge').edges.at(-1).payload_type,'result','without a port the payload follows the node types');});
test('S2-05: a node shows its own problems over its frame and names each port for assistive technology',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const {CustomNode}=compile('CustomNode.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const html=(props)=>renderToStaticMarkup(React.createElement(CustomNode,{node:node('seg','inspection',{task:'segmentation',threshold:0.5}),isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){},...props}));
 const marked=html({issues:['seg: 점수 임계치는 0~1이어야 합니다.','seg: 다음 모델, Blob, 집계 또는 판정 노드로 연결하세요.']});
 assert.match(marked,/role="note"/);assert.match(marked,/⚠ 2/);assert.match(marked,/seg 문제 2건: 점수 임계치는 0~1이어야 합니다\. \/ 다음 모델/);assert.match(marked,/border-amber-500/);
 assert.doesNotMatch(html({}),/role="note"/,'no badge without problems');
 assert.match(marked,/Start connection from seg IMAGE\/ROI OUT/);assert.match(marked,/Start connection from seg RESULT OUT/);assert.match(marked,/Connect to seg IMAGE\/ROI IN/);
 const detector=renderToStaticMarkup(React.createElement(CustomNode,{node:node('det','detection_crop'),isDetectorOnly:true,isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){}}));
 assert.match(detector,/DEFECT BOXES/,'a detector feeding the decision names its result output as defect boxes');
 const inspection=renderToStaticMarkup(React.createElement(CustomNode,{node:node('seg','inspection',{task:'segmentation'}),isDetectorOnly:true,isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){}}));
 assert.doesNotMatch(inspection,/DEFECT BOXES/,'an inspection model feeding the decision keeps RESULT OUT');assert.match(inspection,/RESULT OUT/);});
test('S2-05: the output port a connection starts from is passed on with its payloads',()=>{
 const React=require('react');const started=[];const {CustomNode}=compile('CustomNode.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const tree=CustomNode({node:node('seg','inspection',{task:'segmentation'}),isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){},onConnectStart:payloads=>started.push(payloads)});
 const buttons=[];const visit=v=>{if(Array.isArray(v))v.forEach(visit);else if(v&&typeof v==='object'){if(v.type==='button'&&String(v.props?.['aria-label']).startsWith('Start connection'))buttons.push(v);visit(v.props?.children);}};visit(tree);
 for(const button of buttons)button.props.onClick({stopPropagation(){}});assert.deepEqual(started,[['image','roi'],['result']]);assert.ok(React);});
test('S2-05 review: an older edge without a payload is drawn from the port the backend resolves for it',()=>{
 const seg=node('seg','inspection',{task:'segmentation'});
 assert.equal(graph.flowEdgeSourcePort(seg,{id:'e'},node('judge','decision')),1,'a result to the decision leaves RESULT OUT');
 assert.equal(graph.flowEdgeSourcePort(seg,{id:'e'},node('next','inspection')),0,'regions to the next model leave IMAGE/ROI OUT');
 assert.equal(graph.flowEdgePayload({id:'e'},seg,node('in','input')),'image');assert.equal(graph.flowEdgePayload({id:'e',payload_type:'roi'},node('judge','decision'),seg),'roi','a stored payload is kept');});
test('S2-05 review: a cycle is named even when a node in it has its own problem, and malformed saved data never throws',()=>{
 const cycle=valid();cycle.nodes.push(node('a','inspection',{task:'classification',threshold:7}),node('b','inspection',{task:'classification',threshold:0.5}));
 cycle.edges.push({id:'ab',source:'a',target:'b',payload_type:'roi'},{id:'ba',source:'b',target:'a',payload_type:'roi'},{id:'bj',source:'b',target:'judge',payload_type:'result'},{id:'aj',source:'a',target:'judge',payload_type:'result'});
 assert.ok(graph.flowGraphIssues(cycle).some(issue=>/순환 없이/.test(issue.message)),'the cycle is named');
 const malformed=valid();malformed.edges[2].predicate={kind:'class',operator:'present',class_name:42};malformed.nodes[2].data.score_spec={domain:'probability',unit:'probability',direction:'higher_is_defect',calibration_id:7,threshold:0.5};
 const issues=graph.flowIssuesByTarget(malformed);assert.match(issues.edges.get('e3')[0],/저장된 조건·데이터 형식/);assert.match(issues.nodes.get('cls')[0],/cls: 저장된 설정 형식/);
 assert.doesNotThrow(()=>graph.validateFlowchartGraph(malformed));});
test('S2-05 review: an unsupported node pair names what the target accepts, and issues point at the node to fix',()=>{
 const draft={id:'p',name:'p',nodes:[node('blob','blob_measure'),node('cls','inspection',{task:'classification'})],edges:[]};
 assert.throws(()=>graph.connectFlowNodes(draft,'blob','cls',['result']),/이 노드 사이의 연결은 지원하지 않습니다\. cls 입력은 이미지·ROI를 받으며, blob에서 바로 연결할 수 없습니다\./);
 const outputs=valid();outputs.nodes.push(node('out2','output'));outputs.edges.push({id:'e6',source:'judge',target:'out2',payload_type:'result',isBranch:'fail'});outputs.edges[4].isBranch='pass';
 outputs.edges.push({id:'e7',source:'cls',target:'out2',payload_type:'result'});
 const message=graph.validateFlowchartGraph(outputs);assert.ok(message);const located=graph.locateFlowIssue(outputs,message);
 assert.deepEqual(located,graph.flowGraphIssues(outputs,{first:true}).map(issue=>({kind:issue.kind,id:issue.id}))[0],'the banner jumps to the collected target');});
test('S2-05 review P3: the refusal takes the object particle of what the target accepts',()=>{
 const draft={id:'p',name:'p',nodes:[node('in','input'),node('out','output'),node('blob','blob_measure')],edges:[]};
 assert.throws(()=>graph.connectFlowNodes(draft,'in','out'),/out 입력은 판정을 받으며/);
 assert.throws(()=>graph.connectFlowNodes(draft,'in','blob'),/blob 입력은 결과를 받으며/);});
test('S2-05 review P3: a connection saved without an id shows its problem on its own wire, with nothing to jump to',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 const saved=valid();saved.edges[2]={...saved.edges[2],id:''};
 const {edges}=graph.flowIssuesByTarget(saved);const key=graph.flowEdgeKey(saved.edges[2],2);
 assert.deepEqual([...edges.keys()],[key]);assert.match(edges.get(key)[0],/연결선 ID가 중복되었거나 비었습니다/);
 assert.equal(graph.locateFlowIssue(saved,graph.validateFlowchartGraph(saved)),null,'no connection to select');
 assert.equal(graph.flowEdgeKey(saved.edges[0],0),'e1','a saved id is the key');
 const {DAGCircuitOverlay}=compile('DAGCircuitOverlay.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const html=renderToStaticMarkup(React.createElement(DAGCircuitOverlay,{nodes:saved.nodes,edges:saved.edges,activeRunningNodeId:null,edgeIssues:edges}));
 assert.match(html,/aria-label="Select connection seg to judge: 연결선 ID가 중복되었거나 비었습니다\."/);
 assert.equal((html.match(/stroke-dasharray="2 3"/g)||[]).length,1,'only that wire is marked');
 const marked=valid();marked.edges[2].payload_type='image';
 const markedHtml=renderToStaticMarkup(React.createElement(DAGCircuitOverlay,{nodes:marked.nodes,edges:marked.edges,activeRunningNodeId:null,edgeIssues:graph.flowIssuesByTarget(marked).edges}));
 assert.match(markedHtml,/data-flow-edge="e3"[^>]*stroke-dasharray="2 3"/,'a connection with an id is marked on its wire');
 assert.deepEqual(graph.locateFlowIssue(marked,graph.validateFlowchartGraph(marked)),{kind:'edge',id:'e3'});});
test('S2-05 review P3: a node saved without a label still renders its problems',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');const {CustomNode}=compile('CustomNode.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const pipeline=valid();const bare=node('bare','inspection',{task:'classification',threshold:0.5});delete bare.data.label;pipeline.nodes.push(bare);
 const issues=graph.flowIssuesByTarget(pipeline).nodes.get('bare');assert.ok(issues?.length);
 const html=renderToStaticMarkup(React.createElement(CustomNode,{node:bare,issues,isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){}}));
 assert.match(html,/모델 입력 연결선이 정확히 하나/);assert.doesNotMatch(html,/title="undefined: /,'the validator prefix is stripped');
 const emptied=node('emptied','inspection',{task:'classification',threshold:0.5,label:''});const withEmpty=valid();withEmpty.nodes.push(emptied);
 const emptiedIssues=graph.flowIssuesByTarget(withEmpty).nodes.get('emptied');
 const emptiedHtml=renderToStaticMarkup(React.createElement(CustomNode,{node:emptied,issues:emptiedIssues,isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){}}));
 assert.match(emptiedHtml,/title="모델 입력 연결선이 정확히 하나/,'a name cleared in the inspector leaves no ": " in front');});
test('S2-05 review P3: connection keys, jump targets and wire marks hold with dangling and repeated connections',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
 assert.equal(graph.flowEdgeKey({id:'',source:'a',target:'b'},3),'\u00003','a positional key cannot be typed as an id');
 const repeated=valid();repeated.edges.push({id:'dup',source:'in',target:'seg',payload_type:'image'});
 assert.match(graph.validateFlowchartGraph(repeated),/같은 노드 사이의 연결선이 중복/);
 assert.deepEqual(graph.locateFlowIssue(repeated,graph.validateFlowchartGraph(repeated)),{kind:'edge',id:'dup'},'a connection problem no message rule finds still jumps to its connection');
 const dangling=valid();dangling.edges.unshift({id:'ghost',source:'nowhere',target:'judge',payload_type:'result'});dangling.edges[3]={...dangling.edges[3],id:''};
 const {edges}=graph.flowIssuesByTarget(dangling);assert.ok(edges.get('ghost')?.length);assert.ok(edges.get(graph.flowEdgeKey(dangling.edges[3],3))?.length);
 const {DAGCircuitOverlay}=compile('DAGCircuitOverlay.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const html=renderToStaticMarkup(React.createElement(DAGCircuitOverlay,{nodes:dangling.nodes,edges:dangling.edges,activeRunningNodeId:null,edgeIssues:edges}));
 assert.match(html,/aria-label="Select connection seg to judge: 연결선 ID가 중복되었거나 비었습니다\."/,'the id-less wire keeps its own position behind a wire that cannot be drawn');});
test('S2-05 review: DEFECT BOXES only for a detection model feeding the decision, and the node frame (not the badge) turns amber',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');const {CustomNode}=compile('CustomNode.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const render=(props)=>renderToStaticMarkup(React.createElement(CustomNode,{isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){},...props}));
 assert.doesNotMatch(render({node:node('det','detection_crop'),isDetectorOnly:false}),/DEFECT BOXES/,'a detection model feeding another model keeps RESULT OUT');
 assert.match(render({node:node('seg','inspection',{task:'segmentation'}),issues:['seg: x']}),/border-amber-500 hover:border-amber-400/,'the frame itself is amber');
 assert.doesNotMatch(render({node:node('seg','inspection',{task:'segmentation'})}),/hover:border-amber-400/);
 assert.match(render({node:node('seg','inspection',{task:'segmentation'})}),/data-flow-port="seg:out:1"/,'each port carries its key for the wire layer');});
test('class rule choices use model IDs and refuse conflicting upstream vocabularies',()=>{
  const pipeline={nodes:[{id:'seg',data:{model_job_id:'model'}},{id:'blob',data:{node_type:'blob_measure'}}],edges:[{source:'seg',target:'blob'}]};
  const models=[{job_id:'model',class_names:['background','Bow'],class_ids:[0,1]}];
  assert.deepEqual(graph.nodeClassChoices(pipeline,'blob',models),[{id:1,name:'Bow'}]);
  pipeline.nodes.push({id:'other',data:{model_job_id:'second'}});pipeline.edges.push({source:'other',target:'blob'});
  models.push({job_id:'second',class_names:['background','Crack'],class_ids:[0,1]});
  assert.deepEqual(graph.nodeClassChoices(pipeline,'blob',models),[]);
});
test('S2-05: a class a rule names must exist in the connected model, and an unknown vocabulary is never judged',()=>{
 const catalog=[{job_id:'job_seg',class_names:['background','scratch'],class_ids:[0,1]},{job_id:'job_cls',class_names:['OK','NG'],class_ids:[0,1]}];
 const flow=()=>{const p=valid();p.nodes[1].data.model_job_id='job_seg';p.nodes[2].data.model_job_id='job_cls';return p;};
 // A branch condition on a class the classifier never predicts.
 const predicate=flow();predicate.edges[3]={...predicate.edges[3],predicate:{kind:'class',operator:'present',class_name:'Crack'}};
 assert.deepEqual(graph.flowIssuesByTarget(predicate).edges.get('e4')||[],[],'without the catalog nothing is judged');
 const named=graph.flowIssuesByTarget(predicate,catalog).edges.get('e4')||[];
 assert.ok(named.some(message=>/'Crack': cls 모델에 없는 이름입니다\. 모델 클래스: OK, NG/.test(message)),named);
 predicate.edges[3].predicate.class_name='NG';assert.deepEqual(graph.flowIssuesByTarget(predicate,catalog).edges.get('e4')||[],[]);
 // A Blob rule and a segmentation rule on class IDs the segmentation model does not record.
 const blob=flow();blob.nodes.push(node('blob','blob_measure',{params:{class_ids:[2],class_rules:[{class_id:3}]}}));
 blob.edges[2]={id:'e3',source:'seg',target:'blob',payload_type:'result'};blob.edges.push({id:'e6',source:'blob',target:'judge',payload_type:'result'});
 const blobIssues=graph.flowIssuesByTarget(blob,catalog).nodes.get('blob')||[];
 assert.ok(blobIssues.some(message=>/연결된 모델에 없는 클래스 ID: 2, 3\. 선택 가능한 클래스: 1 scratch/.test(message)),blobIssues);
 assert.deepEqual(graph.flowIssuesByTarget(blob).nodes.get('blob')||[],[],'without the catalog the Blob is not judged');
 const segRules=flow();segRules.nodes[1].data.params={class_ids:[4]};
 assert.ok((graph.flowIssuesByTarget(segRules,catalog).nodes.get('seg')||[]).some(message=>/없는 클래스 ID: 4\./.test(message)));
 // The segmentation channel order must equal the model's, as the engine refuses a different one.
 const order=flow();order.nodes[1].data.params={class_names:['background','dent']};
 assert.ok((graph.flowIssuesByTarget(order,catalog).nodes.get('seg')||[]).some(message=>/분할 클래스 순서\(background, dent\)가 모델\(background, scratch\)과 다릅니다/.test(message)));
 order.nodes[1].data.params={class_names:['background','scratch']};assert.deepEqual(graph.flowIssuesByTarget(order,catalog).nodes.get('seg')||[],[]);
 // A model recorded without class IDs has an unknown vocabulary: nothing is judged (conflicts: the test above).
 assert.deepEqual(graph.flowIssuesByTarget(blob,[{job_id:'job_seg',class_names:['background','scratch']}]).nodes.get('blob')||[],[]);
 assert.equal(graph.validateFlowchartGraph(predicate,catalog),null,'a known class passes the validator');
 predicate.edges[3].predicate.class_name='Crack';assert.match(graph.validateFlowchartGraph(predicate,catalog),/Crack/);
 assert.equal(graph.locateFlowIssue(predicate,graph.validateFlowchartGraph(predicate,catalog),catalog)?.id,'e4');
});
test('S2-05 review: class names are compared exactly as the engine does, and a model node is judged by its own model only',()=>{
 const catalog=[{job_id:'job_seg',class_names:['background','scratch'],class_ids:[0,1]},{job_id:'job_cls',class_names:['OK','NG'],class_ids:[0,1]}];
 const flow=()=>{const p=valid();p.nodes[1].data.model_job_id='job_seg';p.nodes[2].data.model_job_id='job_cls';return p;};
 const edgeIssues=(pipeline,models=catalog)=>graph.flowIssuesByTarget(pipeline,models).edges.get('e4')||[];
 const predicate=flow();predicate.edges[3]={...predicate.edges[3],predicate:{kind:'class',operator:'present',class_name:'NG '}};
 // The engine never matches 'NG ' to NG, so the condition would silently never hold: say to remove the spaces.
 assert.ok(edgeIssues(predicate).some(message=>/'NG '의 앞뒤 공백을 지우세요/.test(message)),edgeIssues(predicate));
 predicate.edges[3].predicate.class_name=' NG';assert.ok(edgeIssues(predicate).some(message=>/공백을 지우세요/.test(message)),'a non-breaking space too');
 predicate.edges[3].predicate.class_name='ng';assert.ok(edgeIssues(predicate).some(message=>/'ng': cls 모델에 없는 이름입니다/.test(message)),'case is kept, as at run time');
 predicate.edges[3].predicate.class_name='NG';assert.deepEqual(edgeIssues(predicate),[]);
 // A source node without a label is named by its id.
 const unlabeled=flow();unlabeled.nodes[2].data.label='';unlabeled.edges[3]={...unlabeled.edges[3],predicate:{kind:'class',operator:'absent',class_name:'Crack'}};
 assert.ok(edgeIssues(unlabeled).some(message=>/'Crack': cls 모델에 없는 이름입니다/.test(message)),edgeIssues(unlabeled));
 // A model recorded with an empty class list has an unknown vocabulary: nothing is judged.
 assert.deepEqual(edgeIssues(predicate,[{job_id:'job_cls',class_names:[],class_ids:[]}]),[]);
 predicate.edges[3].predicate.class_name='Crack';assert.deepEqual(edgeIssues(predicate,[{job_id:'job_cls',class_names:[],class_ids:[]}]),[]);
 // The segmentation channel order is compared in order, not as a set, and with its case.
 const order=flow();const seg=()=>graph.flowIssuesByTarget(order,catalog).nodes.get('seg')||[];
 order.nodes[1].data.params={class_names:['scratch','background']};assert.ok(seg().some(message=>/분할 클래스 순서\(scratch, background\)/.test(message)),'a reordering is refused');
 order.nodes[1].data.params={class_names:['background','Scratch']};assert.ok(seg().some(message=>/분할 클래스 순서/.test(message)),'case is kept');
 // An unbound segmentation node is not judged against an upstream detector's classes (its missing model is the problem).
 const unbound={id:'p',name:'p',nodes:[node('in','input'),node('det','detection_crop',{model_job_id:'job_det',threshold:0.5}),node('seg','inspection',{task:'segmentation',threshold:0.5,params:{class_ids:[3]}}),node('judge','decision',{rule:'any_defect_is_ng'}),node('out','output')],
  edges:[{id:'e1',source:'in',target:'det',payload_type:'image'},{id:'e2',source:'det',target:'seg',payload_type:'roi'},{id:'e3',source:'seg',target:'judge',payload_type:'result'},{id:'e4',source:'judge',target:'out',payload_type:'result'}]};
 const detector=[{job_id:'job_det',class_names:['chip','pad'],class_ids:[1,2]}];
 assert.deepEqual((graph.flowIssuesByTarget(unbound,detector).nodes.get('seg')||[]).filter(message=>/클래스 ID/.test(message)),[]);
 unbound.nodes[2].data.model_job_id='job_seg';
 assert.ok((graph.flowIssuesByTarget(unbound,[...detector,...catalog]).nodes.get('seg')||[]).some(message=>/없는 클래스 ID: 3\. 선택 가능한 클래스: 1 scratch$/.test(message)),'a bound node is judged by its own model');
});
test('S2-05 review 2: the nearest model node decides the classes, and a blank label is named by the id',()=>{
 const pipeline={nodes:[{id:'det',data:{node_type:'detection_crop',model_job_id:'job_det'}},{id:'seg',data:{node_type:'inspection',task:'segmentation'}},{id:'blob',data:{node_type:'blob_measure'}}],
  edges:[{source:'det',target:'seg'},{source:'seg',target:'blob'}]};
 const models=[{job_id:'job_det',class_names:['chip','pad'],class_ids:[1,2]},{job_id:'job_seg',class_names:['background','scratch'],class_ids:[0,1]}];
 assert.deepEqual(graph.nodeClassChoices(pipeline,'blob',models),[],'a Blob behind an unbound segmentation node gets no detector classes');
 assert.deepEqual(graph.nodeClassChoices(pipeline,'seg',models),[],'nor does the unbound node itself');
 pipeline.nodes[1].data.model_job_id='job_seg';
 assert.deepEqual(graph.nodeClassChoices(pipeline,'blob',models),[{id:1,name:'scratch'}],'bound: the segmentation model decides');
 const behindDetector={nodes:[{id:'seg2',data:{node_type:'inspection',task:'segmentation',model_job_id:'job_seg'}},{id:'det2',data:{node_type:'detection_crop'}},{id:'blob2',data:{node_type:'blob_measure'}}],edges:[{source:'seg2',target:'det2'},{source:'det2',target:'blob2'}]};
 assert.deepEqual(graph.nodeClassChoices(behindDetector,'blob2',models),[],'an unbound detector stops the walk too');
 const catalog=[{job_id:'job_cls',class_names:['OK','NG'],class_ids:[0,1]}];
 const flow=valid();flow.nodes[2].data.model_job_id='job_cls';flow.nodes[2].data.label='   ';flow.edges[3]={...flow.edges[3],predicate:{kind:'class',operator:'present',class_name:'Crack'}};
 assert.ok((graph.flowIssuesByTarget(flow,catalog).edges.get('e4')||[]).some(message=>/'Crack': cls 모델에 없는 이름입니다/.test(message)));
});

test('incomplete results cannot use an OK policy and point at their decision node',()=>{
 const p=valid();p.nodes.find(n=>n.id==='judge').data.params={incomplete_policy:'ok'};
 assert.ok(graph.flowGraphIssues(p).some(i=>i.kind==='node'&&i.id==='judge'&&i.message.includes('미실행')));
 p.nodes.find(n=>n.id==='judge').data.params={incomplete_policy:'ng'};
 assert.equal(graph.validateFlowchartGraph(p),null);
});

test('object count rules refuse unknown classes, booleans, duplicates and conditional decisions',()=>{
 const p={id:'count',name:'count',nodes:[node('in','input'),node('det','detection_crop',{task:'detection',model_job_id:'job',threshold:.5,params:{object_requirements:[{class_name:'bolt',min_count:1,max_count:2}]}}),node('judge','decision',{rule:'any_defect_is_ng'}),node('out','output')],edges:[{id:'a',source:'in',target:'det',payload_type:'image'},{id:'b',source:'det',target:'judge',payload_type:'result'},{id:'c',source:'judge',target:'out',payload_type:'result'}]};
 const models=[{job_id:'job',class_names:['bolt','washer'],class_ids:[1,2]}];
 assert.equal(graph.validateFlowchartGraph(p,models),null);
 const bad=structuredClone(p);bad.nodes[1].data.params.object_requirements[0].min_count=true;assert.match(graph.validateFlowchartGraph(bad,models),/최소·최대/);
 bad.nodes[1].data.params.object_requirements[0].min_count=1;bad.nodes[1].data.params.object_requirements[0].class_name='missing';assert.match(graph.validateFlowchartGraph(bad,models),/기록된 객체/);
 bad.nodes[1].data.params.object_requirements=[...p.nodes[1].data.params.object_requirements,...p.nodes[1].data.params.object_requirements];assert.match(graph.validateFlowchartGraph(bad,models),/클래스 이름/);
 bad.nodes[1].data.params.object_requirements=p.nodes[1].data.params.object_requirements;bad.edges[1].isBranch='pass';assert.match(graph.validateFlowchartGraph(bad,models),/조건 없이/);
});

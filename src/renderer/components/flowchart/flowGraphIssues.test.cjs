const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),Module=require('node:module'),ts=require('typescript');
// S2-05: every problem is shown on the node or connection it belongs to, ports name what a connection may carry, and a
// connection started from the wrong port is refused at once with the accepted payloads.
function compile(file,mocks={}){const name=path.join(__dirname,file);const m=new Module(name,module);m.filename=name;m.paths=Module._nodeModulePaths(__dirname);const original=m.require.bind(m);
  m.require=ref=>ref in mocks?mocks[ref]:original(ref);m._compile(ts.transpileModule(fs.readFileSync(name,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX}}).outputText,name);return m.exports;}
const graph=compile('flowchartGraph.ts');
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
 assert.throws(()=>graph.connectFlowNodes(draft,'blob','cls',['result']),/이 노드 사이의 연결은 지원하지 않습니다\. cls 입력은 이미지·ROI을\(를\) 받으며, blob에서 바로 연결할 수 없습니다\./);
 const outputs=valid();outputs.nodes.push(node('out2','output'));outputs.edges.push({id:'e6',source:'judge',target:'out2',payload_type:'result',isBranch:'fail'});outputs.edges[4].isBranch='pass';
 outputs.edges.push({id:'e7',source:'cls',target:'out2',payload_type:'result'});
 const message=graph.validateFlowchartGraph(outputs);assert.ok(message);const located=graph.locateFlowIssue(outputs,message);
 assert.deepEqual(located,graph.flowGraphIssues(outputs,{first:true}).map(issue=>({kind:issue.kind,id:issue.id}))[0],'the banner jumps to the collected target');});
test('S2-05 review: DEFECT BOXES only for a detection model feeding the decision, and the node frame (not the badge) turns amber',()=>{
 const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');const {CustomNode}=compile('CustomNode.tsx',{'./flowchartViewport':{FLOW_NODE_WIDTH:272},'./flowchartGraph':graph});
 const render=(props)=>renderToStaticMarkup(React.createElement(CustomNode,{isSelected:false,isActive:false,isPassed:false,isFlaggedNg:false,onSelect(){},...props}));
 assert.doesNotMatch(render({node:node('det','detection_crop'),isDetectorOnly:false}),/DEFECT BOXES/,'a detection model feeding another model keeps RESULT OUT');
 assert.match(render({node:node('seg','inspection',{task:'segmentation'}),issues:['seg: x']}),/border-amber-500 hover:border-amber-400/,'the frame itself is amber');
 assert.doesNotMatch(render({node:node('seg','inspection',{task:'segmentation'})}),/hover:border-amber-400/);
 assert.match(render({node:node('seg','inspection',{task:'segmentation'})}),/data-flow-port="seg:out:1"/,'each port carries its key for the wire layer');});

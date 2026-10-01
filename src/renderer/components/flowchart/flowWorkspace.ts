import type { FlowchartPipeline, FlowchartExecutionStep, FlowchartExecutionResult, FlowNode } from '../../types';

export function flowTestSetStorageKey(scope:{projectId:string;projectDir:string;source:string;task:string;labelset?:string;computeProfileId:string|null;apiIdentity:string}):string {
  return `flow-test-set:v2:${JSON.stringify([scope.apiIdentity,scope.computeProfileId,scope.projectId,scope.projectDir,scope.source,scope.task,scope.labelset||'default'])}`;
}

export function roiFromDrag(start:number[],end:number[],size:number[]):number[] {
  const [w,h]=size;
  if (![...start,...end,w,h].every(Number.isFinite)||w<16||h<16) throw new Error('원본 이미지 크기를 확인하세요. ROI는 최소 16×16 픽셀입니다.');
  const x1=Math.max(0,Math.min(w-16,Math.floor(Math.min(start[0],end[0]))));
  const y1=Math.max(0,Math.min(h-16,Math.floor(Math.min(start[1],end[1]))));
  return [x1,y1,Math.max(x1+16,Math.min(w,Math.ceil(Math.max(start[0],end[0])))),Math.max(y1+16,Math.min(h,Math.ceil(Math.max(start[1],end[1]))))];
}

export function moveRoi(roi:number[],dx:number,dy:number,size:number[]):number[] {
  const [x1,y1,x2,y2]=roi;const [w,h]=size;
  const width=Math.min(w,x2-x1),height=Math.min(h,y2-y1);
  const x=Math.max(0,Math.min(w-width,x1+dx)),y=Math.max(0,Math.min(h-height,y1+dy));
  return [x,y,x+width,y+height];
}

export function activeInputArtifacts(nodeId:string,pipeline:FlowchartPipeline,result:FlowchartExecutionResult) {
  const steps=result.execution_steps;
  if(steps.find(s=>s.node_id===nodeId)?.status==='skipped')return [];
  return pipeline.edges.filter(e=>e.target===nodeId).flatMap(e=>{
    const parent=steps.find(s=>s.node_id===e.source);
    return parent?.selected_edge_ids?.includes(e.id)?parent.artifacts||[]:[];
  });
}

export function nodeEvidenceText(node:FlowNode,step:FlowchartExecutionStep):string[] {
  const lines=[`입력 ${step.input_count??0}개 → 출력 ${step.output_count??0}개 · ${step.branch_verdict||step.status} · ${step.latency_ms.toFixed(1)} ms`];
  if(step.skip_reason) lines.push(({condition_not_met:'설정한 분기 조건이 맞지 않아 실행하지 않았습니다.',empty_roi:'상류 모델이 검사할 영역을 전달하지 않았습니다.',outside_debug_scope:'선택 노드 뒤쪽 또는 별도 경로이므로 실행하지 않았습니다.',branch_not_selected:'최종 판정과 다른 출력 경로입니다.'} as Record<string,string>)[step.skip_reason]||step.skip_reason);
  const rules=(node.data.params?.class_rules||[]) as Array<Record<string,number>>;
  for(const artifact of step.artifacts||[]) {
    const measured=(artifact.evidence?.blob_measurements||[]) as Array<Record<string,unknown>>;
    for(const row of measured) {
      const rule=rules.find(r=>r.class_id===row.class_id);
      const name=String(row.class_name||row.class_id);
      if(rule?.min_count!=null) lines.push(`${name}: 필요 최소 ${rule.min_count}개, 측정 ${row.count}개`);
      if(rule?.max_count!=null) lines.push(`${name}: 허용 최대 ${rule.max_count}개, 측정 ${row.count}개`);
      if(rule?.min_area_px!=null) lines.push(`${name}: 필요 면적 ${rule.min_area_px} px 이상, 측정 ${row.area_px} px`);
      if(rule?.max_area_px!=null) lines.push(`${name}: 허용 면적 ${rule.max_area_px} px 이하, 측정 ${row.area_px} px`);
      if(!rule) lines.push(`${name}: ${row.count}개, 면적 ${row.area_px} px, ${row.verdict}`);
    }
    if(artifact.evidence?.recognized_text!=null) lines.push(`인식한 문자: ${artifact.evidence.recognized_text}`);
  }
  return [...new Set(lines)];
}

export function insertSubgraph(base:FlowchartPipeline,module:FlowchartPipeline,prefix:string):FlowchartPipeline {
  if(module.nodes.some(n=>['input','decision','output'].includes(n.data.node_type))) throw new Error('입력·판정·출력 노드가 있는 템플릿은 전체 플로우로 여세요.');
  const mapping=new Map(module.nodes.map(n=>[n.id,`${prefix}:${n.id}`]));
  if(base.nodes.some(n=>[...mapping.values()].includes(n.id))) throw new Error('템플릿 노드 ID가 중복됩니다.');
  const offset=Math.max(0,...base.nodes.map(n=>n.position.y))+180;
  return {...base,nodes:[...base.nodes,...module.nodes.map(n=>({...n,id:mapping.get(n.id)!,position:{x:n.position.x,y:n.position.y+offset}}))],edges:[...base.edges,...module.edges.map(e=>({...e,id:`${prefix}:${e.id}`,source:mapping.get(e.source)!,target:mapping.get(e.target)!}))]};
}

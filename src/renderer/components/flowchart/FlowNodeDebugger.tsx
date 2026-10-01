import type {FlowNode,FlowchartPipeline,FlowchartExecutionResult} from '../../types';
import {nodeEvidenceText,activeInputArtifacts} from './flowWorkspace';
export function FlowNodeDebugger({node,pipeline,result}:{node:FlowNode;pipeline:FlowchartPipeline;result:FlowchartExecutionResult|null}) {
  const step=result?.execution_steps.find(s=>s.node_id===node.id);
  if(!step)return <section className="rounded border border-slate-700 p-3 text-xs text-slate-400">실행 후 이 노드의 입력·출력과 판정 근거가 표시됩니다. 그래프나 이미지를 수정하면 이전 근거는 지워집니다.</section>;
  const inputs=result?activeInputArtifacts(node.id,pipeline,result):[];
  const artifacts=(title:string,rows:typeof inputs)=><div><h4 className="mb-2 font-semibold text-slate-200">{title} · {rows.length}개</h4><div className="grid grid-cols-2 gap-2">{rows.slice(0,12).map((r,i)=><figure key={`${r.roi_id}:${i}`} className="rounded border border-slate-700 p-1"><img src={r.image} alt={`${title} ${i+1}`} className="max-h-36 w-full object-contain"/>{r.mask&&<img src={r.mask} alt="검사 영역 마스크" className="max-h-36 w-full object-contain"/>}<figcaption className="mt-1 text-xs text-slate-400">영역 [{r.bbox.join(', ')}]</figcaption></figure>)}</div>{rows.length>12&&<p className="text-slate-400">앞 12개 표시 · 전체 결과는 검사 결과 화면에서 확인하세요.</p>}</div>;
  return <section aria-label="선택 노드 실행 근거" className="rounded border border-slate-700 bg-slate-950/30 p-3 space-y-3 text-xs">
    <h4 className="font-bold text-sky-200">실행 근거 · {node.data.label}</h4>
    {nodeEvidenceText(node,step).map((line,i)=><p key={i}>{line}</p>)}
    {node.data.node_type==='decision'&&<p className="text-amber-200">{result?.rejection_reason}</p>}
    {artifacts('입력',inputs)}{artifacts('출력',step.artifacts||[])}
  </section>;
}
